// nn::fs service layer for Breath of the Wild on macOS.
// On the console these calls go through the fsp-srv service; here each mount name maps to a host
// directory and every operation is confined to that directory:
//   RomFS           -> $DM_ROMFS (default $DM_ROOT/romfs, $DM_ROOT default ~/botw-work), read-only
//   add-on content  -> $DM_DLC_<index> (DM_DLC_1, DM_DLC_2), read-only, must exist to mount
//   save data       -> $DM_SAVE (must be set explicitly), writable, transactional (see "Save data")
// Paths look like "content:/System/Version.txt". The path walker resolves one component at a time
// with openat(O_NOFOLLOW), so ".." cannot climb above the mount root and symlinks are never followed;
// a symlink inside a mount behaves like a missing entry.
// File / directory handles are opaque 64-bit values: {generation, type tag, slot index + 1}. Handle 0,
// closed handles, handles of the wrong type and handles from a previous generation of a slot are all
// rejected instead of being dereferenced.
// Only the symbols that BotW 1.9.0 imports are registered (23 FS entries plus AOC listing; see
// botw-fs-imports.txt in the private workspace). The binary ABI was verified against the executable:
// Result is a 32-bit word, FileHandle / DirectoryHandle are one 64-bit word each, DirectoryEntry is
// 0x310 bytes with the type byte at 0x304 (0 = directory, 1 = file) and the size at 0x308.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include "botw_account.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <sys/clonefile.h>
#include <sys/stat.h>
#include <sys/stdio.h>
#include <sys/xattr.h>
#include <unistd.h>

// ---------- Results ----------
// nn::Result for the fs module: module 2 in the low 9 bits, description above. The description numbers
// follow the public libnx / Atmosphere tables; the game only distinguishes a few of them (PathNotFound,
// PathAlreadyExists), everything else is "some failure".
#define FS_RESULT(desc) ((Result)(((uint32_t)(desc) << 9) | 2u))
enum {
    RES_FS_PATH_NOT_FOUND            = FS_RESULT(1),
    RES_FS_PATH_ALREADY_EXISTS       = FS_RESULT(2),
    RES_FS_TARGET_LOCKED             = FS_RESULT(7),
    RES_FS_DIRECTORY_NOT_EMPTY       = FS_RESULT(8),
    RES_FS_USABLE_SPACE_NOT_ENOUGH   = FS_RESULT(30),
    RES_FS_TARGET_NOT_FOUND          = FS_RESULT(1002),   // add-on content index not present
    RES_FS_NOT_IMPLEMENTED           = FS_RESULT(3001),
    RES_FS_OUT_OF_RANGE              = FS_RESULT(3005),
    RES_FS_UNEXPECTED                = FS_RESULT(5000),   // host I/O failure with no closer description
    RES_FS_INVALID_ARGUMENT          = FS_RESULT(6001),
    RES_FS_TOO_LONG_PATH             = FS_RESULT(6003),
    RES_FS_INVALID_PATH_FORMAT       = FS_RESULT(6005),
    RES_FS_INVALID_OFFSET            = FS_RESULT(6061),
    RES_FS_INVALID_SIZE              = FS_RESULT(6062),
    RES_FS_NULLPTR_ARGUMENT          = FS_RESULT(6063),
    RES_FS_INVALID_MOUNT_NAME        = FS_RESULT(6065),
    RES_FS_INVALID_OPEN_MODE         = FS_RESULT(6300),
    RES_FS_EXTEND_WITHOUT_APPEND     = FS_RESULT(6301),
    RES_FS_READ_NOT_PERMITTED        = FS_RESULT(6302),
    RES_FS_WRITE_NOT_PERMITTED       = FS_RESULT(6303),
    RES_FS_PERMISSION_DENIED         = FS_RESULT(6400),
    RES_FS_UNSUPPORTED_OPERATION     = FS_RESULT(6450),   // write on a read-only mount
    RES_FS_INVALID_HANDLE            = FS_RESULT(6001),   // zero / closed / stale / wrong-type handle
    RES_FS_OPEN_COUNT_LIMIT          = FS_RESULT(6709),
};

// ---------- Public ABI ----------
enum { OPEN_READ = 1, OPEN_WRITE = 2, OPEN_ALLOW_APPEND = 4, OPEN_MODE_ALL = 7 };
enum { ENTRY_DIRECTORY = 0, ENTRY_FILE = 1 };
enum { OPEN_DIR_DIRECTORY = 1, OPEN_DIR_FILE = 2 };
#define OPEN_DIR_NO_FILE_SIZE 0x80000000u   // above INT_MAX, so a macro rather than an enumerator
#define OPEN_DIR_ALL          0x80000003u
enum { WRITE_OPTION_FLUSH = 1 };
typedef struct { char name[0x301]; uint8_t pad0[3]; int8_t type; uint8_t pad1[3]; int64_t size; } DirectoryEntry;
_Static_assert(sizeof(DirectoryEntry) == 0x310, "DirectoryEntry must be 0x310 bytes");
_Static_assert(offsetof(DirectoryEntry, type) == 0x304, "DirectoryEntry.type must sit at 0x304");
_Static_assert(offsetof(DirectoryEntry, size) == 0x308, "DirectoryEntry.size must sit at 0x308");
_Static_assert(sizeof(Result) == 4, "Result must be a 32-bit word");

enum { PATH_MAX_LEN = 0x300, MOUNT_NAME_MAX = 15, IO_CHUNK = 1 << 30, ROM_CACHE_SIZE = 0x1000 };

// ---------- Mount table ----------
// kind: ROM / AOC are read-only; SAVE is writable and transactional unless the transaction setup failed,
// in which case it is mounted read-only (writes fail honestly, commits refuse).
enum { MOUNT_ROM = 1, MOUNT_AOC = 2, MOUNT_SAVE = 3 };
enum { MAX_MOUNT = 8 };
typedef struct {
    char name[MOUNT_NAME_MAX + 1];
    char dir[PATH_MAX];      // committed directory (save) or content root (rom / aoc)
    char work[PATH_MAX];     // save only: working directory the guest reads and writes; "" when not transactional
    int rootfd;              // O_DIRECTORY fd of the directory the guest sees
    int kind, ro, used;
    _Atomic int poison;      // save only: errno of the first failed write in this transaction (0 = clean)
    _Atomic unsigned commits;
} mount_t;
static mount_t g_mnt[MAX_MOUNT];
static pthread_rwlock_t g_mnt_lock = PTHREAD_RWLOCK_INITIALIZER;   // read: path resolution; write: mount / unmount / commit

// ---------- Handle tables ----------
enum { FILE_SLOTS = 2048, DIR_SLOTS = 256, TAG_FILE = 0x46, TAG_DIR = 0x44 };
typedef struct {
    pthread_rwlock_t lock;   // shared for positional reads, exclusive for directory iteration and close
    uint32_t gen;            // bumped on every close, never 0; a handle carries the generation it was issued with
    _Atomic int used;        // read lock-free by the allocator scan, written under the slot lock
    int fd, mode, mnt;
    DIR *dir;
} slot_t;
static slot_t g_files[FILE_SLOTS], g_dirs[DIR_SLOTS];
static pthread_mutex_t g_alloc_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t g_slots_once = PTHREAD_ONCE_INIT;
static void slots_init(void) {
    for (int i = 0; i < FILE_SLOTS; i++) { pthread_rwlock_init(&g_files[i].lock, NULL); g_files[i].gen = 1; g_files[i].fd = -1; }
    for (int i = 0; i < DIR_SLOTS; i++) { pthread_rwlock_init(&g_dirs[i].lock, NULL); g_dirs[i].gen = 1; g_dirs[i].fd = -1; }
}
static uint64_t handle_make(int tag, int idx, uint32_t gen) { return ((uint64_t)gen << 32) | ((uint64_t)tag << 24) | (uint64_t)(idx + 1); }
// Decodes a handle and returns its slot with the lock held (shared or exclusive); NULL if the handle is
// zero, mistyped, out of range, closed, or from an older generation of the slot.
static slot_t *handle_lock(uint64_t h, int tag, int exclusive) {
    pthread_once(&g_slots_once, slots_init);
    slot_t *tab = tag == TAG_FILE ? g_files : g_dirs; int n = tag == TAG_FILE ? FILE_SLOTS : DIR_SLOTS;
    if (!h || ((h >> 24) & 0xffu) != (uint64_t)tag) return NULL;
    uint64_t low = h & 0xffffffu; if (!low || low > (uint64_t)n) return NULL;
    slot_t *s = &tab[low - 1];
    if (exclusive) pthread_rwlock_wrlock(&s->lock); else pthread_rwlock_rdlock(&s->lock);
    if (!atomic_load(&s->used) || s->gen != (uint32_t)(h >> 32)) { pthread_rwlock_unlock(&s->lock); return NULL; }
    return s;
}
// Reserves a free slot and returns it exclusively locked with used = 1; the caller fills it and unlocks.
static int slot_alloc(int tag, slot_t **out) {
    pthread_once(&g_slots_once, slots_init);
    slot_t *tab = tag == TAG_FILE ? g_files : g_dirs; int n = tag == TAG_FILE ? FILE_SLOTS : DIR_SLOTS;
    pthread_mutex_lock(&g_alloc_lock);
    for (int i = 0; i < n; i++) {
        if (atomic_load(&tab[i].used)) continue;
        pthread_rwlock_wrlock(&tab[i].lock); atomic_store(&tab[i].used, 1); pthread_mutex_unlock(&g_alloc_lock);
        *out = &tab[i]; return i;
    }
    pthread_mutex_unlock(&g_alloc_lock);
    return -1;
}
// Releases a slot: closes the host object, bumps the generation so outstanding copies of the handle go stale.
static void slot_release(slot_t *s) {
    if (s->dir) closedir(s->dir); else if (s->fd >= 0) close(s->fd);
    s->dir = NULL; s->fd = -1; s->mode = 0; s->mnt = -1; atomic_store(&s->used, 0);
    if (!++s->gen) s->gen = 1;
}
// Invalidates every handle that belongs to a mount (unmount, or a discarded save transaction).
static int slots_drop_mount(int mi) {
    pthread_once(&g_slots_once, slots_init);
    int n = 0;
    for (int i = 0; i < FILE_SLOTS; i++) { pthread_rwlock_wrlock(&g_files[i].lock); if (g_files[i].used && g_files[i].mnt == mi) { slot_release(&g_files[i]); n++; } pthread_rwlock_unlock(&g_files[i].lock); }
    for (int i = 0; i < DIR_SLOTS; i++) { pthread_rwlock_wrlock(&g_dirs[i].lock); if (g_dirs[i].used && g_dirs[i].mnt == mi) { slot_release(&g_dirs[i]); n++; } pthread_rwlock_unlock(&g_dirs[i].lock); }
    return n;
}

// ---------- errno mapping ----------
static Result res_errno(int e) {
    switch (e) {
    case ENOENT: case ENOTDIR: case ELOOP: case EISDIR: return RES_FS_PATH_NOT_FOUND;   // ELOOP: a symlink under O_NOFOLLOW is treated as absent
    case EEXIST: return RES_FS_PATH_ALREADY_EXISTS;
    case ENOTEMPTY: return RES_FS_DIRECTORY_NOT_EMPTY;
    case ENOSPC: case EDQUOT: case EFBIG: return RES_FS_USABLE_SPACE_NOT_ENOUGH;
    case ENAMETOOLONG: return RES_FS_TOO_LONG_PATH;
    case EACCES: case EPERM: case EROFS: return RES_FS_PERMISSION_DENIED;
    case EMFILE: case ENFILE: return RES_FS_OPEN_COUNT_LIMIT;
    case EBUSY: case ETXTBSY: return RES_FS_TARGET_LOCKED;
    default: return RES_FS_UNEXPECTED;
    }
}
static Result fail_errno(const char *op, const char *what) {
    int e = errno; Result r = res_errno(e);
    if (r == RES_FS_PATH_NOT_FOUND || r == RES_FS_PATH_ALREADY_EXISTS) { if (g_flag.fs) LOG("fs %s %s: %s", op, what, strerror(e)); }
    else LOG("fs %s %s failed: %s (errno %d)", op, what, strerror(e), e);
    errno = e; return r;
}

// ---------- Path resolution ----------
// A resolved path: the mount, an owned fd of the parent directory of the final component, and the final
// component itself. For the mount root the parent fd is the root itself and leaf is "" (is_root = 1).
typedef struct { mount_t *m; int mi, parent, is_root; char leaf[PATH_MAX_LEN + 1]; } res_t;
static void res_close(res_t *r) { if (r->parent >= 0) close(r->parent); r->parent = -1; }
static int mount_find(const char *name, size_t n) {
    for (int i = 0; i < MAX_MOUNT; i++) if (g_mnt[i].used && strlen(g_mnt[i].name) == n && !memcmp(g_mnt[i].name, name, n)) return i;
    return -1;
}
// Normalizes the part after "name:" into a stack of components: "." is dropped, ".." pops, empty
// components are skipped. Returns the number of components, -1 for a malformed path (".." above root).
static int split_components(const char *rest, char *buf, size_t bufn, char **comp, int maxcomp) {
    size_t L = strlen(rest); if (L >= bufn) { errno = ENAMETOOLONG; return -1; }
    memcpy(buf, rest, L + 1);
    int n = 0;
    for (char *p = buf; *p;) {
        while (*p == '/') p++;
        if (!*p) break;
        char *q = p; while (*q && *q != '/') q++;
        if (*q) *q++ = 0;
        if (!strcmp(p, ".")) { p = q; continue; }
        if (!strcmp(p, "..")) { if (!n) { errno = EINVAL; return -1; } n--; p = q; continue; }
        if (n >= maxcomp) { errno = ENAMETOOLONG; return -1; }
        comp[n++] = p; p = q;
    }
    return n;
}
// Must be called with g_mnt_lock held (shared or exclusive). Walks every intermediate component through
// openat(O_DIRECTORY | O_NOFOLLOW) from the mount root; nothing outside the root can be reached.
static Result path_resolve(const char *path, res_t *r) {
    r->parent = -1; r->is_root = 0; r->leaf[0] = 0; r->m = NULL; r->mi = -1;
    if (!path) return RES_FS_NULLPTR_ARGUMENT;
    size_t L = strnlen(path, PATH_MAX_LEN + 1); if (L > PATH_MAX_LEN) return RES_FS_TOO_LONG_PATH;
    for (size_t i = 0; i < L; i++) if ((unsigned char)path[i] < 0x20) return RES_FS_INVALID_PATH_FORMAT;
    const char *colon = strchr(path, ':');
    if (!colon || colon == path || (size_t)(colon - path) > MOUNT_NAME_MAX) return RES_FS_INVALID_MOUNT_NAME;
    int mi = mount_find(path, (size_t)(colon - path));
    if (mi < 0) { if (g_flag.fs) LOG("fs path %s: mount not found", path); return RES_FS_INVALID_MOUNT_NAME; }
    char buf[PATH_MAX_LEN + 1]; char *comp[PATH_MAX_LEN / 2 + 1];
    int n = split_components(colon + 1, buf, sizeof buf, comp, (int)(sizeof comp / sizeof *comp));
    if (n < 0) return errno == ENAMETOOLONG ? RES_FS_TOO_LONG_PATH : RES_FS_INVALID_PATH_FORMAT;
    for (int i = 0; i < n; i++) if (strlen(comp[i]) > NAME_MAX) return RES_FS_TOO_LONG_PATH;
    r->m = &g_mnt[mi]; r->mi = mi;
    int cur = dup(r->m->rootfd); if (cur < 0) return fail_errno("dup root", path);
    for (int i = 0; i + 1 < n; i++) {
        int next = openat(cur, comp[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int e = errno; close(cur);
        if (next < 0) { errno = e; return fail_errno("walk", path); }
        cur = next;
    }
    r->parent = cur;
    if (n) strlcpy(r->leaf, comp[n - 1], sizeof r->leaf); else r->is_root = 1;
    return RES_OK;
}
// lstat-style status of the final component relative to its parent; symlinks report as absent.
static int res_stat(const res_t *r, struct stat *st) {
    if (r->is_root) return fstat(r->parent, st);
    if (fstatat(r->parent, r->leaf, st, AT_SYMLINK_NOFOLLOW)) return -1;
    if (S_ISLNK(st->st_mode)) { errno = ENOENT; return -1; }
    return 0;
}
#define MNT_RDLOCK() pthread_rwlock_t *mnt_guard __attribute__((cleanup(mnt_unlock))) = &g_mnt_lock; pthread_rwlock_rdlock(mnt_guard)
#define MNT_WRLOCK() pthread_rwlock_t *mnt_guard __attribute__((cleanup(mnt_unlock))) = &g_mnt_lock; pthread_rwlock_wrlock(mnt_guard)
static void mnt_unlock(pthread_rwlock_t **m) { pthread_rwlock_unlock(*m); }
#define RESOLVE(path) res_t rs __attribute__((cleanup(res_close))); { Result rr_ = path_resolve((path), &rs); if (rr_ != RES_OK) return rr_; }
#define WRITABLE(path) if (rs.m->ro) { LOG("fs write to read-only mount refused: %s", (path)); return RES_FS_UNSUPPORTED_OPERATION; }

// ---------- Confined tree operations ----------
// Deletes the directory `name` under `parent`, never following symlinks: a symlink inside the tree is
// unlinked as an entry, its target is untouched.
static int rm_tree_at(int parent, const char *name) {
    int fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC); if (fd < 0) return -1;
    DIR *d = fdopendir(fd); if (!d) { int e = errno; close(fd); errno = e; return -1; }
    int rc = 0, e = 0; struct dirent *ent;
    for (;;) {
        errno = 0; ent = readdir(d);
        if (!ent) { if (errno) { rc = -1; e = errno; } break; }
        if (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, "..")) continue;
        struct stat st;
        if (fstatat(fd, ent->d_name, &st, AT_SYMLINK_NOFOLLOW)) { rc = -1; e = errno; continue; }
        if (S_ISDIR(st.st_mode) ? rm_tree_at(fd, ent->d_name) : unlinkat(fd, ent->d_name, 0)) { rc = -1; e = errno; }
    }
    closedir(d);
    if (unlinkat(parent, name, AT_REMOVEDIR)) { if (!rc) e = errno; rc = -1; }
    if (rc) errno = e;
    return rc;
}
// Splits an absolute or relative host path into an opened parent directory fd and the leaf name.
static int open_parent(const char *path, char *leaf, size_t leafn) {
    char tmp[PATH_MAX]; if (strlcpy(tmp, path, sizeof tmp) >= sizeof tmp) { errno = ENAMETOOLONG; return -1; }
    size_t L = strlen(tmp); while (L > 1 && tmp[L - 1] == '/') tmp[--L] = 0;
    char *slash = strrchr(tmp, '/');
    const char *parent = ".";
    if (slash) { if (strlcpy(leaf, slash + 1, leafn) >= leafn) { errno = ENAMETOOLONG; return -1; } if (slash == tmp) parent = "/"; else { *slash = 0; parent = tmp; } }
    else if (strlcpy(leaf, tmp, leafn) >= leafn) { errno = ENAMETOOLONG; return -1; }
    return open(parent, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
}
// Removes a host-side directory that this layer owns (the save working / staging directories). Refuses
// anything that is not a real directory, so a file or symlink at that name is never destroyed.
// Ownership is host metadata, never a guest-visible file. Clones inherit this marker, including the
// old committed tree moved to .new by RENAME_SWAP. An unmarked sibling is never ours to remove.
#define SAVE_OWNER_ATTR "com.botw-mobile.save-owner"
static int mark_owned_dir(const char *path, const char *owner) {
    int fd = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    int rc = fsetxattr(fd, SAVE_OWNER_ATTR, owner, strlen(owner), 0, 0);
    int e = errno; close(fd); errno = e; return rc;
}
static int rm_owned_dir(const char *path, const char *owner) {
    char leaf[NAME_MAX + 1]; int parent = open_parent(path, leaf, sizeof leaf); if (parent < 0) return -1;
    struct stat st; int rc;
    if (fstatat(parent, leaf, &st, AT_SYMLINK_NOFOLLOW)) rc = errno == ENOENT ? 0 : -1;
    else if (!S_ISDIR(st.st_mode)) { errno = ENOTDIR; rc = -1; }
    else {
        int fd = openat(parent, leaf, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        char marker[PATH_MAX];
        ssize_t n = fd < 0 ? -1 : fgetxattr(fd, SAVE_OWNER_ATTR, marker, sizeof marker, 0, 0);
        if (fd >= 0) close(fd);
        if (n != (ssize_t)strlen(owner) || memcmp(marker, owner, (size_t)(n < 0 ? 0 : n))) { errno = EPERM; rc = -1; }
        else rc = rm_tree_at(parent, leaf);
    }
    int e = errno; close(parent); errno = e; return rc;
}
// Pushes a file or directory to stable storage: F_FULLFSYNC where the volume supports it, fsync otherwise.
static int full_sync(int fd) {
    if (!fcntl(fd, F_FULLFSYNC)) return 0;
    if (errno != ENOTSUP && errno != EINVAL && errno != ENOTTY) return -1;
    static _Atomic int warned; if (!atomic_exchange(&warned, 1)) LOG("fs durability: F_FULLFSYNC unsupported on this volume, falling back to fsync");
    return fsync(fd);
}
// Flushes every regular file under `dfd` (owned, consumed) and the directories themselves.
static int sync_tree(int dfd) {
    DIR *d = fdopendir(dfd); if (!d) { int e = errno; close(dfd); errno = e; return -1; }
    int rc = 0, e = 0; struct dirent *ent;
    for (;;) {
        errno = 0; ent = readdir(d);
        if (!ent) { if (errno) { rc = -1; e = errno; } break; }
        if (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, "..")) continue;
        struct stat st;
        if (fstatat(dfd, ent->d_name, &st, AT_SYMLINK_NOFOLLOW)) { rc = -1; e = errno; continue; }
        if (S_ISDIR(st.st_mode)) {
            int sub = openat(dfd, ent->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (sub < 0 || sync_tree(sub)) { rc = -1; e = errno; }
        } else if (S_ISREG(st.st_mode)) {
            int f = openat(dfd, ent->d_name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
            if (f < 0 || full_sync(f)) { rc = -1; e = errno; }
            if (f >= 0) close(f);
        }
    }
    if (full_sync(dfd)) { rc = -1; e = errno; }
    closedir(d);
    if (rc) errno = e;
    return rc;
}
static int sync_path(const char *path) { int fd = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC); return fd < 0 ? -1 : sync_tree(fd); }

// ---------- Mounting ----------
static int mount_name_ok(const char *name) {
    if (!name) return 0;
    size_t n = strnlen(name, MOUNT_NAME_MAX + 1); if (!n || n > MOUNT_NAME_MAX) return 0;
    for (size_t i = 0; i < n; i++) { unsigned char c = (unsigned char)name[i]; if (c <= 0x20 || c == ':' || c == '/' || c == '\\' || c == 0x7f) return 0; }
    return 1;
}
static const char *env_dir(const char *var, const char *rel, char *buf, size_t n) {
    const char *v = getenv(var);
    if (v && *v) return v;
    const char *root = getenv("DM_ROOT");
    if (root && *root) snprintf(buf, n, "%s/%s", root, rel);
    else snprintf(buf, n, "%s/botw-work/%s", getenv("HOME") ? getenv("HOME") : "", rel);
    return buf;
}
// Adds a mount. `dir` is the host directory the guest sees (for a save: the working directory); `commit`
// is the committed save directory or NULL. Requires g_mnt_lock exclusively.
static Result mount_add(const char *name, int kind, const char *dir, const char *commit, int ro) {
    if (!mount_name_ok(name)) { LOG("fs mount: invalid mount name"); return RES_FS_INVALID_MOUNT_NAME; }
    if (mount_find(name, strlen(name)) >= 0) { LOG("fs mount %s: name already mounted", name); return RES_FS_INVALID_MOUNT_NAME; }
    int i = 0; while (i < MAX_MOUNT && g_mnt[i].used) i++;
    if (i == MAX_MOUNT) { LOG("fs mount %s: mount table full", name); return RES_FS_OPEN_COUNT_LIMIT; }
    int fd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) { LOG("fs mount %s: cannot open directory %s: %s", name, dir, strerror(errno)); return res_errno(errno); }
    mount_t *m = &g_mnt[i];
    memset(m->name, 0, sizeof m->name); strlcpy(m->name, name, sizeof m->name);
    strlcpy(m->dir, commit ? commit : dir, sizeof m->dir);
    strlcpy(m->work, commit ? dir : "", sizeof m->work);
    m->rootfd = fd; m->kind = kind; m->ro = ro; atomic_store(&m->poison, 0); atomic_store(&m->commits, 0); m->used = 1;
    LOG("fs mount %s: -> %s%s%s", name, dir, ro ? " (read-only)" : "", commit ? " (transactional save working copy)" : "");
    return RES_OK;
}
static void mount_drop(int mi) {
    mount_t *m = &g_mnt[mi];
    if (m->rootfd >= 0) close(m->rootfd);
    m->rootfd = -1; m->used = 0; m->name[0] = 0;
}

HLE_FORCE("_ZN2nn2fs22QueryMountRomCacheSizeEPm", "BotW: RomFS is a host directory, only a token cache is requested", Result, botw_fs_QueryMountRomCacheSize, size_t *out) {
    if (!out) return RES_FS_NULLPTR_ARGUMENT;
    *out = ROM_CACHE_SIZE; return RES_OK;
}
HLE_FORCE("_ZN2nn2fs8MountRomEPKcPvm", "BotW: RomFS -> $DM_ROMFS host directory, read-only", Result, botw_fs_MountRom, const char *name, void *cache, size_t cache_size) {
    if (cache_size && !cache) return RES_FS_NULLPTR_ARGUMENT;
    if (cache_size < ROM_CACHE_SIZE) { LOG("fs MountRom: cache of %zu bytes is below the %#x the query asked for", cache_size, ROM_CACHE_SIZE); return RES_FS_INVALID_SIZE; }
    char buf[PATH_MAX]; const char *dir = env_dir("DM_ROMFS", "romfs", buf, sizeof buf);
    MNT_WRLOCK();
    return mount_add(name, MOUNT_ROM, dir, NULL, 1);
}
// Enumeration and mounting share the same configured-directory availability rule.
static const char *dlc_directory(int index) {
    if (index < 1 || index > 99) return NULL;
    char var[16]; snprintf(var, sizeof var, "DM_DLC_%d", index);
    const char *dir = getenv(var);
    struct stat st;
    return dir && *dir && !stat(dir, &st) && S_ISDIR(st.st_mode) ? dir : NULL;
}
// Reuse the inherited AOC pagination contract, with actual BotW content directories
// instead of the upstream title's fixed entitlement. Offset is a list position.
HLE_FORCE("_ZN2nn3aoc16ListAddOnContentEPiii", "BotW: enumerate configured add-on content", int, botw_aoc_list, int *out, int offset, int capacity) {
    if (offset < 0 || capacity < 0 || (capacity > 0 && !out))
        die("BotW add-on content: invalid enumeration arguments");
    if (!capacity) return 0;
    int indices[99], total = 0;
    for (int index = 1; index <= 99; ++index)
        if (dlc_directory(index)) indices[total++] = index;
    if (offset >= total) return 0;
    int count = total - offset;
    if (count > capacity) count = capacity;
    dm_guest_write_prepare(out, (size_t)count * sizeof *out);
    memcpy(out, indices + offset, (size_t)count * sizeof *out);
    if (g_flag.fs) LOG("BotW add-on content: available=%d offset=%d returned=%d", total, offset, count);
    return count;
}
HLE_FORCE("_ZN2nn2fs31QueryMountAddOnContentCacheSizeEPmi", "BotW: add-on content is a host directory, only a token cache is requested", Result, botw_fs_QueryMountAddOnContentCacheSize, size_t *out, int index) {
    if (!out) return RES_FS_NULLPTR_ARGUMENT;
    if (index <= 0) return RES_FS_INVALID_ARGUMENT;
    *out = ROM_CACHE_SIZE; return RES_OK;
}
HLE_FORCE("_ZN2nn2fs17MountAddOnContentEPKciPvm", "BotW: add-on content index N -> $DM_DLC_N host directory, read-only", Result, botw_fs_MountAddOnContent, const char *name, int index, void *cache, size_t cache_size) {
    if (cache_size && !cache) return RES_FS_NULLPTR_ARGUMENT;
    if (index <= 0 || index > 99) return RES_FS_INVALID_ARGUMENT;
    if (cache_size < ROM_CACHE_SIZE) { LOG("fs MountAddOnContent: cache of %zu bytes is below the %#x the query asked for", cache_size, ROM_CACHE_SIZE); return RES_FS_INVALID_SIZE; }
    const char *dir = dlc_directory(index);
    if (!dir) { LOG("fs MountAddOnContent index %d: no configured content directory", index); return RES_FS_TARGET_NOT_FOUND; }
    MNT_WRLOCK();
    return mount_add(name, MOUNT_AOC, dir, NULL, 1);
}

// ---------- Save data ----------
// The committed save lives in $DM_SAVE. At mount time it is cloned (APFS copy-on-write) to the sibling
// "$DM_SAVE.work" and the guest reads and writes only the working copy. CommitSaveData flushes every file
// in the working copy to stable storage, clones it to "$DM_SAVE.new", swaps that atomically with $DM_SAVE
// (renamex_np RENAME_SWAP) and flushes the parent directory, then deletes the swapped-out old version.
// Killed at any moment, $DM_SAVE is either the previous or the new committed version. Uncommitted writes
// are discarded on unmount and on the next mount (only an ownership-marked .work is removed).
// A failed write or truncate poisons the transaction: the next commit is refused, the working copy is
// re-cloned from the committed version and every open handle on the mount is invalidated, so the game
// can never commit a half-written save without noticing.
// Unmarked sibling paths are left untouched. A leftover .new may contain either an unfinished snapshot
// or the previous committed save after a swap: preserve it and mount read-only for manual recovery.
HLE_FORCE("_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE", "BotW: save data -> $DM_SAVE host directory, transactional", Result, botw_fs_MountSaveData, const char *name, const void *uid) {
    if (!uid) return RES_FS_NULLPTR_ARGUMENT;
    if (!botw_uid_is_local(uid)) return RES_FS_INVALID_ARGUMENT;
    const char *env = getenv("DM_SAVE");
    if (!env || !*env) { LOG("fs MountSaveData: DM_SAVE is not set; point it at a writable save directory (created if missing)"); return RES_FS_TARGET_NOT_FOUND; }
    char dir[PATH_MAX]; if (strlcpy(dir, env, sizeof dir) >= sizeof dir) return RES_FS_TOO_LONG_PATH;
    size_t L = strlen(dir); while (L > 1 && dir[L - 1] == '/') dir[--L] = 0;
    if (mkdir(dir, 0755) && errno != EEXIST) { LOG("fs MountSaveData: cannot create %s: %s", dir, strerror(errno)); return res_errno(errno); }
    struct stat st;
    if (stat(dir, &st) || !S_ISDIR(st.st_mode)) { LOG("fs MountSaveData: %s is not a directory", dir); return RES_FS_PATH_NOT_FOUND; }
    char canonical[PATH_MAX];
    if (!realpath(dir, canonical)) return fail_errno("canonical save path", dir);
    strlcpy(dir, canonical, sizeof dir);
    char work[PATH_MAX], neu[PATH_MAX], re[PATH_MAX];
    if (snprintf(work, sizeof work, "%s.work", dir) >= (int)sizeof work || snprintf(neu, sizeof neu, "%s.new", dir) >= (int)sizeof neu || snprintf(re, sizeof re, "%s.re", dir) >= (int)sizeof re) return RES_FS_TOO_LONG_PATH;
    MNT_WRLOCK();
    if (!mount_name_ok(name) || mount_find(name, strlen(name)) >= 0) return mount_add(name, MOUNT_SAVE, dir, NULL, 1);   // reports the name problem itself
    for (int i = 0; i < MAX_MOUNT; i++) if (g_mnt[i].used && g_mnt[i].kind == MOUNT_SAVE) {
        struct stat active;
        if (!strcmp(g_mnt[i].dir, dir) || (!stat(g_mnt[i].dir, &active) && active.st_dev == st.st_dev && active.st_ino == st.st_ino)) {
            LOG("fs MountSaveData %s: backing save is already mounted as %s", name, g_mnt[i].name);
            return RES_FS_TARGET_LOCKED;
        }
    }
    const char *why = NULL;
    struct stat pending;
    if (!lstat(neu, &pending)) { errno = EBUSY; why = "staging path exists; preserve it for manual recovery"; }
    else if (errno != ENOENT) why = "cannot inspect the staging path";
    else if (rm_owned_dir(work, dir)) why = "working path is unowned or cannot be cleared";
    else if (rm_owned_dir(re, dir)) why = "rollback path is unowned or cannot be cleared";
    else if (mark_owned_dir(dir, dir)) why = "cannot record save transaction ownership";
    else if (clonefile(dir, work, 0)) why = "cannot clone the committed save into the working copy";
    if (why) {
        int e = errno;
        LOG("fs MountSaveData: %s (%s: %s); mounting %s read-only, writes will fail and commits will refuse", why, work, strerror(e), dir);
        return mount_add(name, MOUNT_SAVE, dir, NULL, 1);
    }
    return mount_add(name, MOUNT_SAVE, work, dir, 0);
}
// Discards the working copy of a poisoned transaction and re-clones it from the committed version.
// Requires g_mnt_lock exclusively. Returns 0 on success; on failure the mount stays poisoned.
static int save_reclone(int mi) {
    mount_t *m = &g_mnt[mi];
    char re[PATH_MAX]; if (snprintf(re, sizeof re, "%s.re", m->dir) >= (int)sizeof re) { errno = ENAMETOOLONG; return -1; }
    if (rm_owned_dir(re, m->dir)) return -1;
    if (clonefile(m->dir, re, 0)) { int e = errno; rm_owned_dir(re, m->dir); errno = e; return -1; }
    if (renamex_np(re, m->work, RENAME_SWAP)) { int e = errno; rm_owned_dir(re, m->dir); errno = e; return -1; }
    if (rm_owned_dir(re, m->dir)) LOG("fs save: could not remove the discarded working copy %s: %s", re, strerror(errno));
    int fd = open(m->work, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return -1;
    close(m->rootfd); m->rootfd = fd;
    int dropped = slots_drop_mount(mi);
    if (dropped) LOG("fs save: %d open handle(s) on %s invalidated by the transaction rollback", dropped, m->name);
    return 0;
}
HLE_FORCE("_ZN2nn2fs14CommitSaveDataEPKc", "BotW: flush the working copy to disk and swap it in atomically", Result, botw_fs_CommitSaveData, const char *name) {
    if (!name) return RES_FS_NULLPTR_ARGUMENT;
    MNT_WRLOCK();
    int mi = mount_find(name, strnlen(name, MOUNT_NAME_MAX + 1));
    if (mi < 0) { LOG("fs CommitSaveData %s: not mounted", name); return RES_FS_INVALID_MOUNT_NAME; }
    mount_t *m = &g_mnt[mi];
    if (m->kind != MOUNT_SAVE) { LOG("fs CommitSaveData %s: not a save mount", name); return RES_FS_UNSUPPORTED_OPERATION; }
    if (m->ro || !m->work[0]) { LOG("fs CommitSaveData %s: mounted read-only, nothing can be committed", name); return RES_FS_UNSUPPORTED_OPERATION; }
    int perr = atomic_exchange(&m->poison, 0);
    if (perr) {
        if (save_reclone(mi)) { atomic_store(&m->poison, perr); LOG("fs CommitSaveData %s refused: a write failed in this transaction (%s); the working copy could not be rolled back (%s) and stays poisoned; the committed save is unchanged", name, strerror(perr), strerror(errno)); }
        else LOG("fs CommitSaveData %s refused: a write failed in this transaction (%s); the working copy was rolled back to the committed save, which is unchanged", name, strerror(perr));
        return res_errno(perr);
    }
    char neu[PATH_MAX]; if (snprintf(neu, sizeof neu, "%s.new", m->dir) >= (int)sizeof neu) return RES_FS_TOO_LONG_PATH;
    if (sync_path(m->work)) { LOG("fs CommitSaveData %s: flushing the working copy failed: %s; the committed save is unchanged", name, strerror(errno)); return res_errno(errno); }
    if (rm_owned_dir(neu, m->dir)) { LOG("fs CommitSaveData %s: staging path is unowned or cannot be cleared: %s", name, strerror(errno)); return res_errno(errno); }
    if (clonefile(m->work, neu, 0)) { LOG("fs CommitSaveData %s: cloning the working copy failed: %s; the committed save is unchanged", name, strerror(errno)); return res_errno(errno); }
    if (sync_path(neu)) { int e = errno; rm_owned_dir(neu, m->dir); LOG("fs CommitSaveData %s: staging flush failed: %s; the committed save is unchanged", name, strerror(e)); return res_errno(e); }
    if (renamex_np(neu, m->dir, RENAME_SWAP)) { int e = errno; rm_owned_dir(neu, m->dir); LOG("fs CommitSaveData %s: atomic swap failed: %s; the committed save is unchanged", name, strerror(e)); return res_errno(e); }
    char leaf[NAME_MAX + 1]; int parent = open_parent(m->dir, leaf, sizeof leaf);
    if (parent < 0 || full_sync(parent)) {
        int e = errno;
        if (parent >= 0) close(parent);
        m->ro = 1;
        LOG("fs CommitSaveData %s failed: new version is in place but directory durability is unknown (%s); prior version retained at %s, mount frozen read-only for manual recovery", name, strerror(e), neu);
        return res_errno(e);
    }
    if (parent >= 0) close(parent);
    if (rm_owned_dir(neu, m->dir)) LOG("fs CommitSaveData %s: could not remove the superseded version %s: %s", name, neu, strerror(errno));
    unsigned n = atomic_fetch_add(&m->commits, 1) + 1;
    LOG("fs CommitSaveData %s: commit #%u durable in %s", name, n, m->dir);
    return RES_OK;
}
HLE_FORCE("_ZN2nn2fs7UnmountEPKc", "BotW: drop the mount; an uncommitted save transaction is discarded", void, botw_fs_Unmount, const char *name) {
    if (!name) return;
    MNT_WRLOCK();
    int mi = mount_find(name, strnlen(name, MOUNT_NAME_MAX + 1));
    if (mi < 0) { LOG("fs Unmount %s: not mounted", name); return; }
    mount_t *m = &g_mnt[mi];
    int dropped = slots_drop_mount(mi);
    if (dropped) LOG("fs Unmount %s: %d handle(s) were still open and have been invalidated", name, dropped);
    if (m->kind == MOUNT_SAVE && m->work[0]) {
        if (rm_owned_dir(m->work, m->dir)) LOG("fs Unmount %s: could not remove the working copy %s: %s", name, m->work, strerror(errno));
        else if (atomic_load(&m->poison) || g_flag.fs) LOG("fs Unmount %s: uncommitted changes discarded", name);
    }
    mount_drop(mi);
    LOG("fs Unmount %s", name);
}

// ---------- Files ----------
static void save_poison(mount_t *m, int e) { if (m && m->kind == MOUNT_SAVE) { int z = 0; atomic_compare_exchange_strong(&m->poison, &z, e ? e : EIO); } }
// Missing/existing paths are normal probes. Genuine host failures invalidate the transaction; a
// partially applied operation always poisons it, whatever errno its last failing step reported.
static Result mutation_failure(mount_t *m, const char *op, const char *path, int partial) {
    int e = errno;
    if (partial || (e != ENOENT && e != ENOTDIR && e != ELOOP && e != EISDIR && e != EEXIST && e != ENOTEMPTY && e != ENAMETOOLONG && e != EINVAL)) save_poison(m, e);
    errno = e; return fail_errno(op, path);
}
static mount_t *slot_mount(const slot_t *s) { return s->mnt >= 0 && s->mnt < MAX_MOUNT && g_mnt[s->mnt].used ? &g_mnt[s->mnt] : NULL; }

HLE_FORCE("_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci", "BotW: open a file inside a mount, confined walk", Result, botw_fs_OpenFile, uint64_t *out, const char *path, int mode) {
    if (!out || !path) return RES_FS_NULLPTR_ARGUMENT;
    *out = 0;
    if (!(mode & (OPEN_READ | OPEN_WRITE)) || (mode & ~OPEN_MODE_ALL)) return RES_FS_INVALID_OPEN_MODE;
    MNT_RDLOCK(); RESOLVE(path);
    if (rs.is_root) return RES_FS_PATH_NOT_FOUND;
    if (mode & (OPEN_WRITE | OPEN_ALLOW_APPEND)) WRITABLE(path);
    int flags = (mode & OPEN_WRITE) ? ((mode & OPEN_READ) ? O_RDWR : O_WRONLY) : O_RDONLY;
    int fd = openat(rs.parent, rs.leaf, flags | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return fail_errno("open", path);
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { close(fd); return RES_FS_PATH_NOT_FOUND; }
    slot_t *s; int idx = slot_alloc(TAG_FILE, &s);
    if (idx < 0) { close(fd); LOG("fs open %s: no free file handle (%d in use)", path, FILE_SLOTS); return RES_FS_OPEN_COUNT_LIMIT; }
    s->fd = fd; s->dir = NULL; s->mode = mode; s->mnt = rs.mi;
    uint64_t h = handle_make(TAG_FILE, idx, s->gen);
    pthread_rwlock_unlock(&s->lock);
    if (g_flag.fs) LOG("fs open %s mode=%d -> handle %#llx", path, mode, (unsigned long long)h);
    *out = h; return RES_OK;
}
static Result read_file(size_t *out, uint64_t h, int64_t off, void *buf, size_t sz) {
    if (!out) return RES_FS_NULLPTR_ARGUMENT;
    *out = 0;
    if (!buf && sz) return RES_FS_NULLPTR_ARGUMENT;
    if (off < 0) return RES_FS_INVALID_OFFSET;
    if (sz > (uint64_t)(INT64_MAX - off)) return RES_FS_INVALID_SIZE;
    slot_t *s = handle_lock(h, TAG_FILE, 0);
    if (!s) return RES_FS_INVALID_HANDLE;
    if (!(s->mode & OPEN_READ)) { pthread_rwlock_unlock(&s->lock); return RES_FS_READ_NOT_PERMITTED; }
    Result r = RES_OK; size_t got = 0;
    if (sz) dm_guest_write_prepare(buf, sz);   // the destination may be a write-protected guest page
    while (got < sz) {
        size_t want = sz - got; if (want > IO_CHUNK) want = IO_CHUNK;
        ssize_t k = pread(s->fd, (uint8_t *)buf + got, want, (off_t)(off + (int64_t)got));
        if (k < 0) { if (errno == EINTR) continue; r = fail_errno("read", "file"); break; }
        if (k == 0) break;   // end of file: a short read is reported through *out, not as an error
        got += (size_t)k;
    }
    pthread_rwlock_unlock(&s->lock);
    *out = got; return r;
}
HLE_FORCE("_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm", "BotW: positional read, short at end of file", Result, botw_fs_ReadFile, size_t *out, uint64_t h, int64_t off, void *buf, size_t sz) {
    return read_file(out, h, off, buf, sz);
}
HLE_FORCE("_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvmRKNS0_10ReadOptionE", "BotW: positional read with ReadOption (no flags are defined)", Result, botw_fs_ReadFileOpt, size_t *out, uint64_t h, int64_t off, void *buf, size_t sz, const uint32_t *opt) {
    if (opt && *opt) { static _Atomic int warned; if (!atomic_exchange(&warned, 1)) LOG("fs ReadFile: unknown ReadOption flags %#x ignored", *opt); }
    return read_file(out, h, off, buf, sz);
}
HLE_FORCE("_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE", "BotW: positional write inside the save working copy", Result, botw_fs_WriteFile, uint64_t h, int64_t off, const void *buf, size_t sz, const uint32_t *opt) {
    if (!buf && sz) return RES_FS_NULLPTR_ARGUMENT;
    if (off < 0) return RES_FS_INVALID_OFFSET;
    if (sz > (uint64_t)(INT64_MAX - off)) return RES_FS_INVALID_SIZE;
    unsigned flags = opt ? *opt : 0;
    MNT_RDLOCK();   // Mount before slot: commits and rollbacks must wait for every in-flight mutation.
    slot_t *s = handle_lock(h, TAG_FILE, 1);
    if (!s) return RES_FS_INVALID_HANDLE;
    mount_t *m = slot_mount(s);
    Result r = RES_OK;
    if (!(s->mode & OPEN_WRITE)) r = RES_FS_WRITE_NOT_PERMITTED;
    else if (!m || m->ro) r = RES_FS_UNSUPPORTED_OPERATION;
    else if (!(s->mode & OPEN_ALLOW_APPEND) && sz) {
        struct stat st;
        if (fstat(s->fd, &st)) { r = fail_errno("stat", "file"); save_poison(m, errno); }
        else if (off + (int64_t)sz > st.st_size) r = RES_FS_EXTEND_WITHOUT_APPEND;   // growing a file needs OpenMode_AllowAppend
    }
    size_t put = 0;
    while (r == RES_OK && put < sz) {
        size_t want = sz - put; if (want > IO_CHUNK) want = IO_CHUNK;
        ssize_t k = pwrite(s->fd, (const uint8_t *)buf + put, want, (off_t)(off + (int64_t)put));
        if (k < 0) { if (errno == EINTR) continue; r = fail_errno("write", "file"); save_poison(m, errno); break; }
        if (k == 0) { errno = ENOSPC; r = fail_errno("write (0 bytes accepted)", "file"); save_poison(m, ENOSPC); break; }
        put += (size_t)k;
    }
    if (r == RES_OK && (flags & WRITE_OPTION_FLUSH) && fsync(s->fd)) { r = fail_errno("flush", "file"); save_poison(m, errno); }
    pthread_rwlock_unlock(&s->lock);
    return r;
}
HLE_FORCE("_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE", "BotW: fstat on the host fd", Result, botw_fs_GetFileSize, int64_t *out, uint64_t h) {
    if (!out) return RES_FS_NULLPTR_ARGUMENT;
    slot_t *s = handle_lock(h, TAG_FILE, 0);
    if (!s) return RES_FS_INVALID_HANDLE;
    struct stat st; Result r = RES_OK;
    if (fstat(s->fd, &st)) r = fail_errno("stat", "file"); else *out = st.st_size;
    pthread_rwlock_unlock(&s->lock);
    return r;
}
HLE_FORCE("_ZN2nn2fs11SetFileSizeENS0_10FileHandleEl", "BotW: ftruncate inside the save working copy", Result, botw_fs_SetFileSize, uint64_t h, int64_t size) {
    if (size < 0) return RES_FS_INVALID_SIZE;
    MNT_RDLOCK();
    slot_t *s = handle_lock(h, TAG_FILE, 1);
    if (!s) return RES_FS_INVALID_HANDLE;
    Result r = RES_OK;
    mount_t *m = slot_mount(s);
    if (!(s->mode & OPEN_WRITE)) r = RES_FS_WRITE_NOT_PERMITTED;
    else if (!m || m->ro) r = RES_FS_UNSUPPORTED_OPERATION;
    else if (ftruncate(s->fd, (off_t)size)) { r = fail_errno("truncate", "file"); save_poison(slot_mount(s), errno); }
    pthread_rwlock_unlock(&s->lock);
    return r;
}
HLE_FORCE("_ZN2nn2fs9FlushFileENS0_10FileHandleE", "BotW: fsync the host fd", Result, botw_fs_FlushFile, uint64_t h) {
    MNT_RDLOCK();
    slot_t *s = handle_lock(h, TAG_FILE, 0);
    if (!s) return RES_FS_INVALID_HANDLE;
    Result r = RES_OK;
    mount_t *m = slot_mount(s);
    if ((s->mode & OPEN_WRITE) && (!m || m->ro)) r = RES_FS_UNSUPPORTED_OPERATION;
    else if ((s->mode & OPEN_WRITE) && fsync(s->fd)) { r = fail_errno("flush", "file"); save_poison(m, errno); }
    pthread_rwlock_unlock(&s->lock);
    return r;
}
HLE_FORCE("_ZN2nn2fs9CloseFileENS0_10FileHandleE", "BotW: close the host fd and retire the handle", void, botw_fs_CloseFile, uint64_t h) {
    slot_t *s = handle_lock(h, TAG_FILE, 1);
    if (!s) { if (h) LOG("fs CloseFile: invalid or already closed handle %#llx", (unsigned long long)h); return; }   // the game closes handle 0 after a failed open; that is a no-op
    slot_release(s);
    pthread_rwlock_unlock(&s->lock);
}

// ---------- Path operations ----------
HLE_FORCE("_ZN2nn2fs10CreateFileEPKcl", "BotW: create a file of the given size inside the save working copy", Result, botw_fs_CreateFile, const char *path, int64_t size) {
    if (!path) return RES_FS_NULLPTR_ARGUMENT;
    if (size < 0) return RES_FS_INVALID_SIZE;
    MNT_RDLOCK(); RESOLVE(path);
    if (rs.is_root) return RES_FS_PATH_ALREADY_EXISTS;
    WRITABLE(path);
    int fd = openat(rs.parent, rs.leaf, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (fd < 0) return mutation_failure(rs.m, "create", path, 0);
    Result r = RES_OK;
    if (ftruncate(fd, (off_t)size)) { r = fail_errno("create size", path); int e = errno; unlinkat(rs.parent, rs.leaf, 0); save_poison(rs.m, e); }
    close(fd);
    return r;
}
HLE_FORCE("_ZN2nn2fs10DeleteFileEPKc", "BotW: unlink inside the save working copy", Result, botw_fs_DeleteFile, const char *path) {
    if (!path) return RES_FS_NULLPTR_ARGUMENT;
    MNT_RDLOCK(); RESOLVE(path);
    if (rs.is_root) return RES_FS_PATH_NOT_FOUND;
    WRITABLE(path);
    struct stat st;
    if (res_stat(&rs, &st)) return mutation_failure(rs.m, "delete", path, 0);
    if (S_ISDIR(st.st_mode)) return RES_FS_PATH_NOT_FOUND;   // directories are not files
    return unlinkat(rs.parent, rs.leaf, 0) ? mutation_failure(rs.m, "delete", path, 0) : RES_OK;
}
HLE_FORCE("_ZN2nn2fs15CreateDirectoryEPKc", "BotW: mkdir inside the save working copy", Result, botw_fs_CreateDirectory, const char *path) {
    if (!path) return RES_FS_NULLPTR_ARGUMENT;
    MNT_RDLOCK(); RESOLVE(path);
    if (rs.is_root) return RES_FS_PATH_ALREADY_EXISTS;
    WRITABLE(path);
    return mkdirat(rs.parent, rs.leaf, 0755) ? mutation_failure(rs.m, "mkdir", path, 0) : RES_OK;
}
HLE_FORCE("_ZN2nn2fs26DeleteDirectoryRecursivelyEPKc", "BotW: confined recursive delete inside the save working copy", Result, botw_fs_DeleteDirectoryRecursively, const char *path) {
    if (!path) return RES_FS_NULLPTR_ARGUMENT;
    MNT_RDLOCK(); RESOLVE(path);
    if (rs.is_root) { LOG("fs DeleteDirectoryRecursively %s: deleting the mount root is not supported", path); return RES_FS_INVALID_PATH_FORMAT; }
    WRITABLE(path);
    struct stat st;
    if (res_stat(&rs, &st)) return mutation_failure(rs.m, "rmtree", path, 0);
    if (!S_ISDIR(st.st_mode)) return RES_FS_PATH_NOT_FOUND;
    return rm_tree_at(rs.parent, rs.leaf) ? mutation_failure(rs.m, "rmtree", path, 1) : RES_OK;
}
HLE_FORCE("_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc", "BotW: lstat inside a mount", Result, botw_fs_GetEntryType, int32_t *out, const char *path) {
    if (!out || !path) return RES_FS_NULLPTR_ARGUMENT;
    MNT_RDLOCK(); RESOLVE(path);
    struct stat st;
    if (res_stat(&rs, &st)) return fail_errno("entry type", path);
    if (S_ISDIR(st.st_mode)) *out = ENTRY_DIRECTORY;
    else if (S_ISREG(st.st_mode)) *out = ENTRY_FILE;
    else return RES_FS_PATH_NOT_FOUND;   // sockets, devices and the like do not exist for the guest
    return RES_OK;
}

// ---------- Directories ----------
HLE_FORCE("_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci", "BotW: opendir inside a mount, confined walk", Result, botw_fs_OpenDirectory, uint64_t *out, const char *path, int mode) {
    if (!out || !path) return RES_FS_NULLPTR_ARGUMENT;
    *out = 0;
    if (!((unsigned)mode & (OPEN_DIR_DIRECTORY | OPEN_DIR_FILE)) || ((unsigned)mode & ~OPEN_DIR_ALL)) return RES_FS_INVALID_OPEN_MODE;
    MNT_RDLOCK(); RESOLVE(path);
    // A fresh open file description every time: a dup() would share the directory offset with the mount root fd.
    int fd = openat(rs.parent, rs.is_root ? "." : rs.leaf, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return fail_errno("opendir", path);
    DIR *d = fdopendir(fd);
    if (!d) { int e = errno; close(fd); errno = e; return fail_errno("opendir", path); }
    slot_t *s; int idx = slot_alloc(TAG_DIR, &s);
    if (idx < 0) { closedir(d); LOG("fs opendir %s: no free directory handle (%d in use)", path, DIR_SLOTS); return RES_FS_OPEN_COUNT_LIMIT; }
    s->fd = fd; s->dir = d; s->mode = mode; s->mnt = rs.mi;
    uint64_t h = handle_make(TAG_DIR, idx, s->gen);
    pthread_rwlock_unlock(&s->lock);
    if (g_flag.fs) LOG("fs opendir %s mode=%#x -> handle %#llx", path, (unsigned)mode, (unsigned long long)h);
    *out = h; return RES_OK;
}
HLE_FORCE("_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl", "BotW: readdir into DirectoryEntry records", Result, botw_fs_ReadDirectory, int64_t *count, DirectoryEntry *ents, uint64_t h, int64_t max) {
    if (!count) return RES_FS_NULLPTR_ARGUMENT;
    *count = 0;
    if (max < 0) return RES_FS_INVALID_SIZE;
    if (!ents && max) return RES_FS_NULLPTR_ARGUMENT;
    slot_t *s = handle_lock(h, TAG_DIR, 1);   // readdir mutates the DIR stream: exclusive
    if (!s) return RES_FS_INVALID_HANDLE;
    int64_t n = 0; struct dirent *e;
    while (n < max && (e = readdir(s->dir))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        struct stat st;
        if (fstatat(s->fd, e->d_name, &st, AT_SYMLINK_NOFOLLOW)) continue;   // vanished between readdir and stat
        int isdir = S_ISDIR(st.st_mode);
        if (!isdir && !S_ISREG(st.st_mode)) continue;   // symlinks and special files are invisible to the guest
        if (!((unsigned)s->mode & (isdir ? OPEN_DIR_DIRECTORY : OPEN_DIR_FILE))) continue;
        if (strlen(e->d_name) > PATH_MAX_LEN) { LOG("fs readdir: entry name longer than %d bytes skipped", PATH_MAX_LEN); continue; }
        DirectoryEntry *o = &ents[n++];
        dm_guest_write_prepare(o, sizeof *o);
        memset(o, 0, sizeof *o);
        strlcpy(o->name, e->d_name, sizeof o->name);
        o->type = isdir ? ENTRY_DIRECTORY : ENTRY_FILE;
        o->size = isdir || ((unsigned)s->mode & OPEN_DIR_NO_FILE_SIZE) ? 0 : (int64_t)st.st_size;
    }
    pthread_rwlock_unlock(&s->lock);
    *count = n; return RES_OK;
}
HLE_FORCE("_ZN2nn2fs14CloseDirectoryENS0_15DirectoryHandleE", "BotW: closedir and retire the handle", void, botw_fs_CloseDirectory, uint64_t h) {
    slot_t *s = handle_lock(h, TAG_DIR, 1);
    if (!s) { if (h) LOG("fs CloseDirectory: invalid or already closed handle %#llx", (unsigned long long)h); return; }   // the game closes handle 0 after a failed open; that is a no-op
    slot_release(s);
    pthread_rwlock_unlock(&s->lock);
}
