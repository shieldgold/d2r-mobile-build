#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#ifndef _DARWIN_C_SOURCE
#define _DARWIN_C_SOURCE
#endif
#include "savefs.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#if defined(__linux__)
#include <sys/syscall.h>
#endif

#define MAX_GUEST_PATH 768u
#define MAX_IO (16u * 1024u * 1024u)
#define MAX_ENTRIES 1000000u
#define MAX_DEPTH 384u
#define MAX_DIRECTORY_BYTES (64u * 1024u * 1024u)
#define FILE_LIMIT ((uint64_t)INT64_MAX)
struct botw_savefs { int fd; uint64_t quota; pthread_mutex_t mutex; char title[17], user[33]; };
struct directory_item { char name[256]; botw_savefs_entry entry; };

static int fail(botw_savefs_error *error, int code, const char *operation) {
    if (error) {
        error->code = code;
        if (code) (void)snprintf(error->message, sizeof(error->message), "%s: %s", operation, strerror(code));
        else error->message[0] = '\0';
    }
    return code;
}
static int close_checked(int fd, int code) {
    if (fd >= 0 && close(fd) && !code) return errno;
    return code;
}
static int lock_store(botw_savefs *store, botw_savefs_error *error) {
    int code;
    (void)fail(error, 0, "");
    if (!store) return fail(error, EINVAL, "Missing save store");
    code = pthread_mutex_lock(&store->mutex);
    return code ? fail(error, code, "Lock save store") : 0;
}
static int finish(botw_savefs *store, int code, botw_savefs_error *error, const char *operation) {
    int unlocked = pthread_mutex_unlock(&store->mutex);
    return fail(error, code ? code : unlocked, operation);
}
static int valid_name(const unsigned char *name, size_t length) {
    size_t i = 0;
    if (!length || length > 255 || (length == 1 && name[0] == '.') ||
        (length == 2 && name[0] == '.' && name[1] == '.')) return 0;
    while (i < length) {
        unsigned char c = name[i++]; size_t continuation;
        if (c < 0x80) {
            if (c < 32 || c == 127 || c == '/' || c == '\\' || c == ':') return 0;
            continue;
        }
        if (c >= 0xc2 && c <= 0xdf) continuation = 1;
        else if (c >= 0xe0 && c <= 0xef) continuation = 2;
        else if (c >= 0xf0 && c <= 0xf4) continuation = 3;
        else return 0;
        if (continuation > length - i) return 0;
        if ((c == 0xe0 && name[i] < 0xa0) || (c == 0xed && name[i] >= 0xa0) ||
            (c == 0xf0 && name[i] < 0x90) || (c == 0xf4 && name[i] >= 0x90)) return 0;
        while (continuation--) if ((name[i++] & 0xc0) != 0x80) return 0;
    }
    return 1;
}
static int valid_path(const char *path) {
    size_t length, start = 1;
    if (!path || path[0] != '/') return 0;
    length = strnlen(path, MAX_GUEST_PATH + 1u);
    if (!length || length > MAX_GUEST_PATH) return 0;
    if (length == 1) return 1;
    for (size_t i = 1; i <= length; ++i) if (i == length || path[i] == '/') {
        if (!valid_name((const unsigned char *)path + start, i - start)) return 0;
        start = i + 1;
    }
    return 1;
}
static int valid_id(const char *id, size_t length) {
    if (!id || strnlen(id, length + 1) != length) return 0;
    for (size_t i = 0; i < length; ++i)
        if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) return 0;
    return 1;
}
static int directory_copy(int fd) { return openat(fd, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC); }
static int object_kind(const struct stat *info) {
    if (S_ISLNK(info->st_mode)) return ELOOP;
    if (S_ISREG(info->st_mode)) return info->st_nlink == 1 && info->st_size >= 0 ? 0 : EMLINK;
    return S_ISDIR(info->st_mode) ? 0 : EINVAL;
}
/* Every guest-controlled component is resolved relative to an anchored fd. */
static int parent_fd(const botw_savefs *store, const char *path, int *parent, char name[256]) {
    char copy[MAX_GUEST_PATH + 1u], *cursor, *slash;
    int current;
    if (!valid_path(path)) return EINVAL;
    if (!strcmp(path, "/")) return EBUSY;
    memcpy(copy, path + 1, strlen(path)); cursor = copy;
    current = directory_copy(store->fd);
    if (current < 0) return errno;
    while ((slash = strchr(cursor, '/')) != NULL) {
        int next, code;
        *slash = '\0';
        next = openat(current, cursor, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        code = next < 0 ? errno : 0;
        code = close_checked(current, code);
        if (code) { if (next >= 0) (void)close(next); return code; }
        current = next; cursor = slash + 1;
    }
    memcpy(name, cursor, strlen(cursor) + 1); *parent = current;
    return 0;
}
static int open_directory(const botw_savefs *store, const char *path, int *result) {
    int parent, fd, code; char name[256];
    if (!valid_path(path)) return EINVAL;
    if (!strcmp(path, "/")) { fd = directory_copy(store->fd); if (fd < 0) return errno; *result = fd; return 0; }
    code = parent_fd(store, path, &parent, name); if (code) return code;
    fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    code = fd < 0 ? errno : 0;
    code = close_checked(parent, code);
    if (code) { if (fd >= 0) (void)close(fd); return code; }
    *result = fd; return 0;
}
static int open_file(const botw_savefs *store, const char *path, int flags, int *result, struct stat *info) {
    int parent, fd, code; char name[256];
    if (valid_path(path) && !strcmp(path, "/")) return EISDIR;
    code = parent_fd(store, path, &parent, name); if (code) return code;
    fd = openat(parent, name, flags | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    code = fd < 0 ? errno : 0;
    code = close_checked(parent, code);
    if (code) { if (fd >= 0) (void)close(fd); return code; }
    if (fstat(fd, info)) code = errno;
    else if ((code = object_kind(info)) == 0 && !S_ISREG(info->st_mode)) code = EISDIR;
    if (code) { (void)close(fd); return code; }
    *result = fd; return 0;
}
static int scan_directory(int fd, unsigned int depth, int sync, uint64_t *used, size_t *count) {
    int copy, code = 0; DIR *directory; struct dirent *item;
    if (depth > MAX_DEPTH) return ENAMETOOLONG;
    copy = directory_copy(fd); if (copy < 0) return errno;
    directory = fdopendir(copy); if (!directory) { code = errno; (void)close(copy); return code; }
    errno = 0;
    while ((item = readdir(directory)) != NULL) {
        struct stat info; int child;
        if (!strcmp(item->d_name, ".") || !strcmp(item->d_name, "..")) continue;
        if (++*count > MAX_ENTRIES) { code = EFBIG; break; }
        if (!valid_name((const unsigned char *)item->d_name, strlen(item->d_name))) { code = EINVAL; break; }
        if (fstatat(fd, item->d_name, &info, AT_SYMLINK_NOFOLLOW)) { code = errno; break; }
        code = object_kind(&info); if (code) break;
        child = openat(fd, item->d_name, O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC |
                       (S_ISDIR(info.st_mode) ? O_DIRECTORY : 0));
        if (child < 0) { code = errno; break; }
        if (fstat(child, &info)) code = errno;
        else code = object_kind(&info);
        if (!code && S_ISDIR(info.st_mode)) code = scan_directory(child, depth + 1, sync, used, count);
        else if (!code) {
            uint64_t size = (uint64_t)info.st_size;
            if (size > UINT64_MAX - *used) code = EOVERFLOW;
            else *used += size;
            if (!code && sync && fsync(child)) code = errno;
        }
        code = close_checked(child, code); if (code) break;
        errno = 0;
    }
    if (!code && errno) code = errno;
    if (!code && sync && fsync(fd)) code = errno;
    if (closedir(directory) && !code) code = errno;
    return code;
}
static int usage_locked(const botw_savefs *store, uint64_t *used) {
    size_t count = 0; *used = 0;
    return scan_directory(store->fd, 0, 0, used, &count);
}
static int check_growth(const botw_savefs *store, uint64_t growth) {
    uint64_t used; int code;
    if (!growth || !store->quota) return 0;
    code = usage_locked(store, &used); if (code) return code;
    return used > store->quota || growth > store->quota - used ? EDQUOT : 0;
}
static int open_created_directory(int parent, const char *name, int *result) {
    int created = 0, fd;
    if (!mkdirat(parent, name, 0700)) created = 1;
    else if (errno != EEXIST) return errno;
    if (created && fsync(parent)) return errno;
    fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return errno;
    *result = fd; return 0;
}
botw_savefs *botw_savefs_open(const char *base, const char *title, const char *user,
                              uint64_t quota, botw_savefs_error *error) {
    botw_savefs *store = NULL; char host[PATH_MAX], leaf[256], *slash;
    int parent = -1, base_fd = -1, title_fd = -1, root = -1, code = 0;
    uint64_t used = 0; size_t count = 0, length;
    (void)fail(error, 0, "");
    if (!base || base[0] != '/' || !valid_id(title, 16) || !valid_id(user, 32) ||
        !(length = strnlen(base, sizeof(host))) || length >= sizeof(host)) {
        (void)fail(error, EINVAL, "Invalid App save root or title/user ID"); return NULL;
    }
    memcpy(host, base, length + 1); slash = strrchr(host, '/');
    if (!slash || !valid_name((const unsigned char *)slash + 1, strlen(slash + 1))) {
        (void)fail(error, EINVAL, "Invalid App save root component"); return NULL;
    }
    memcpy(leaf, slash + 1, strlen(slash + 1) + 1);
    if (slash == host) host[1] = '\0'; else *slash = '\0';
    /* Ancestors belong to the trusted App-container path supplied by caller. */
    parent = open(host, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (parent < 0) { code = errno; goto failed; }
    if ((code = open_created_directory(parent, leaf, &base_fd)) ||
        (code = open_created_directory(base_fd, title, &title_fd)) ||
        (code = open_created_directory(title_fd, user, &root))) goto failed;
    if (flock(root, LOCK_EX | LOCK_NB)) { code = errno; goto failed; }
    if ((code = scan_directory(root, 0, 0, &used, &count))) goto failed;
    store = calloc(1, sizeof(*store)); if (!store) { code = ENOMEM; goto failed; }
    code = pthread_mutex_init(&store->mutex, NULL);
    if (code) { free(store); store = NULL; goto failed; }
    store->fd = root; store->quota = quota; root = -1;
    memcpy(store->title, title, sizeof(store->title));
    memcpy(store->user, user, sizeof(store->user));
failed:
    code = close_checked(root, code);
    code = close_checked(title_fd, code);
    code = close_checked(base_fd, code);
    code = close_checked(parent, code);
    if (code && store) { botw_savefs_close(store); store = NULL; }
    if (code) (void)fail(error, code, "Open App save store");
    return store;
}
void botw_savefs_close(botw_savefs *store) {
    if (!store) return;
    (void)close(store->fd); (void)pthread_mutex_destroy(&store->mutex); free(store);
}
const char *botw_savefs_title_id(const botw_savefs *store) { return store ? store->title : NULL; }
const char *botw_savefs_user_id(const botw_savefs *store) { return store ? store->user : NULL; }
int botw_savefs_usage(botw_savefs *store, uint64_t *used, uint64_t *quota, botw_savefs_error *error) {
    int code;
    if (used) *used = 0; if (quota) *quota = 0;
    if (!used || !quota) return fail(error, EINVAL, "Missing save usage output");
    if ((code = lock_store(store, error))) return code;
    code = usage_locked(store, used);
    if (code) *used = 0; else *quota = store->quota;
    return finish(store, code, error, "Measure save store");
}
int botw_savefs_stat(botw_savefs *store, const char *path, botw_savefs_entry *entry, botw_savefs_error *error) {
    int code, parent = -1; char name[256]; struct stat info;
    if (!entry) return fail(error, EINVAL, "Missing save stat output");
    memset(entry, 0, sizeof(*entry));
    if ((code = lock_store(store, error))) return code;
    if (!valid_path(path)) code = EINVAL;
    else if (!strcmp(path, "/")) code = fstat(store->fd, &info) ? errno : 0;
    else {
        code = parent_fd(store, path, &parent, name);
        if (!code && fstatat(parent, name, &info, AT_SYMLINK_NOFOLLOW)) code = errno;
    }
    if (!code) code = object_kind(&info);
    if (!code) { entry->is_directory = S_ISDIR(info.st_mode); entry->size = entry->is_directory ? 0 : (uint64_t)info.st_size; }
    code = close_checked(parent, code);
    return finish(store, code, error, "Stat save entry");
}
int botw_savefs_create(botw_savefs *store, const char *path, uint64_t size, botw_savefs_error *error) {
    int code, parent = -1, fd = -1; char name[256]; struct stat existing;
    if (size > FILE_LIMIT) return fail(error, EFBIG, "Save initial size exceeds host offset range");
    if ((code = lock_store(store, error))) return code;
    code = parent_fd(store, path, &parent, name);
    if (!code) {
        if (!fstatat(parent, name, &existing, AT_SYMLINK_NOFOLLOW)) code = EEXIST;
        else if (errno != ENOENT) code = errno;
    }
    if (!code) code = check_growth(store, size);
    if (!code) {
        fd = openat(parent, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd < 0) code = errno;
        else if (ftruncate(fd, (off_t)size)) { code = errno; (void)unlinkat(parent, name, 0); }
    }
    code = close_checked(fd, code); code = close_checked(parent, code);
    return finish(store, code, error, "Create save file");
}
int botw_savefs_mkdir(botw_savefs *store, const char *path, botw_savefs_error *error) {
    int code, parent = -1; char name[256];
    if ((code = lock_store(store, error))) return code;
    code = parent_fd(store, path, &parent, name);
    if (!code && mkdirat(parent, name, 0700)) code = errno;
    code = close_checked(parent, code);
    return finish(store, code, error, "Create save directory");
}
int botw_savefs_truncate(botw_savefs *store, const char *path, uint64_t size, botw_savefs_error *error) {
    int code, fd = -1; struct stat info;
    if (size > FILE_LIMIT) return fail(error, EFBIG, "Save size exceeds host offset range");
    if ((code = lock_store(store, error))) return code;
    code = open_file(store, path, O_RDWR, &fd, &info);
    if (!code) code = check_growth(store, size > (uint64_t)info.st_size ? size - (uint64_t)info.st_size : 0);
    if (!code && ftruncate(fd, (off_t)size)) code = errno;
    code = close_checked(fd, code);
    return finish(store, code, error, "Resize save file");
}
int botw_savefs_pread(botw_savefs *store, const char *path, uint64_t offset,
                      void *buffer, size_t length, size_t *actual, botw_savefs_error *error) {
    int code, fd = -1; struct stat info; size_t done = 0;
    if (actual) *actual = 0;
    if (!actual || (!buffer && length) || length > MAX_IO) return fail(error, EINVAL, "Invalid bounded save read");
    if ((code = lock_store(store, error))) return code;
    code = open_file(store, path, O_RDONLY, &fd, &info);
    if (!code) {
        if (offset >= (uint64_t)info.st_size) length = 0;
        else if ((uint64_t)length > (uint64_t)info.st_size - offset) length = (size_t)((uint64_t)info.st_size - offset);
        while (done < length) {
            ssize_t read_count = pread(fd, (unsigned char *)buffer + done, length - done, (off_t)(offset + done));
            if (read_count < 0 && errno == EINTR) continue;
            if (read_count <= 0) { code = read_count < 0 ? errno : EIO; break; }
            done += (size_t)read_count;
        }
        if (!code) *actual = done;
    }
    code = close_checked(fd, code);
    return finish(store, code, error, "Read save file");
}
int botw_savefs_pwrite(botw_savefs *store, const char *path, uint64_t offset,
                       const void *buffer, size_t length, size_t *actual, botw_savefs_error *error) {
    int code, fd = -1; struct stat info; uint64_t end;
    if (actual) *actual = 0;
    if (!actual || (!buffer && length) || length > MAX_IO) return fail(error, EINVAL, "Invalid bounded save write");
    if (offset > FILE_LIMIT || (uint64_t)length > FILE_LIMIT - offset) return fail(error, EFBIG, "Save write offset overflows host range");
    if ((code = lock_store(store, error))) return code;
    code = open_file(store, path, O_RDWR, &fd, &info);
    end = offset + length;
    if (!code && length) code = check_growth(store, end > (uint64_t)info.st_size ? end - (uint64_t)info.st_size : 0);
    if (!code) while (*actual < length) {
        ssize_t written = pwrite(fd, (const unsigned char *)buffer + *actual, length - *actual, (off_t)(offset + *actual));
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) { code = written < 0 ? errno : EIO; break; }
        *actual += (size_t)written;
    }
    code = close_checked(fd, code);
    return finish(store, code, error, "Write save file");
}
static int rename_exclusive(int source_parent, const char *source, int destination_parent, const char *destination) {
#if defined(__APPLE__)
    return renameatx_np(source_parent, source, destination_parent, destination, RENAME_EXCL) ? errno : 0;
#elif defined(__linux__) && defined(SYS_renameat2)
    return syscall(SYS_renameat2, source_parent, source, destination_parent, destination, 1u) ? errno : 0;
#else
    (void)source_parent; (void)source; (void)destination_parent; (void)destination;
    return ENOTSUP;
#endif
}
int botw_savefs_rename(botw_savefs *store, const char *source, const char *destination, botw_savefs_error *error) {
    int code, source_parent = -1, destination_parent = -1;
    char source_name[256], destination_name[256]; struct stat info;
    if ((code = lock_store(store, error))) return code;
    code = parent_fd(store, source, &source_parent, source_name);
    if (!code) code = parent_fd(store, destination, &destination_parent, destination_name);
    if (!code && fstatat(source_parent, source_name, &info, AT_SYMLINK_NOFOLLOW)) code = errno;
    if (!code) code = object_kind(&info);
    if (!code) code = rename_exclusive(source_parent, source_name, destination_parent, destination_name);
    code = close_checked(source_parent, code);
    code = close_checked(destination_parent, code);
    return finish(store, code, error, "Rename save entry without replacement");
}
int botw_savefs_remove(botw_savefs *store, const char *path, botw_savefs_error *error) {
    int code, parent = -1; char name[256]; struct stat info;
    if ((code = lock_store(store, error))) return code;
    code = parent_fd(store, path, &parent, name);
    if (!code && fstatat(parent, name, &info, AT_SYMLINK_NOFOLLOW)) code = errno;
    if (!code) code = object_kind(&info);
    if (!code && unlinkat(parent, name, S_ISDIR(info.st_mode) ? AT_REMOVEDIR : 0)) code = errno;
    code = close_checked(parent, code);
    return finish(store, code, error, "Remove save entry");
}
static int compare_item(const void *a, const void *b) {
    return strcmp(((const struct directory_item *)a)->name, ((const struct directory_item *)b)->name);
}
int botw_savefs_iterate(botw_savefs *store, const char *path,
                        botw_savefs_directory_callback callback, void *user, botw_savefs_error *error) {
    int code, fd = -1; DIR *directory = NULL; struct dirent *item;
    struct directory_item *items = NULL; size_t count = 0, capacity = 0;
    if (!callback) return fail(error, EINVAL, "Missing save directory callback");
    if ((code = lock_store(store, error))) return code;
    code = open_directory(store, path, &fd);
    if (!code) { directory = fdopendir(fd); if (!directory) code = errno; else fd = -1; }
    if (!code) {
        errno = 0;
        while ((item = readdir(directory)) != NULL) {
            struct stat info;
            if (!strcmp(item->d_name, ".") || !strcmp(item->d_name, "..")) continue;
            if (!valid_name((const unsigned char *)item->d_name, strlen(item->d_name))) { code = EINVAL; break; }
            if (fstatat(dirfd(directory), item->d_name, &info, AT_SYMLINK_NOFOLLOW)) { code = errno; break; }
            code = object_kind(&info); if (code) break;
            if (count == capacity) {
                struct directory_item *grown;
                size_t next = capacity ? capacity * 2 : 16;
                if (next > MAX_ENTRIES || next > MAX_DIRECTORY_BYTES / sizeof(*items)) { code = EFBIG; break; }
                grown = realloc(items, next * sizeof(*items));
                if (!grown) { code = ENOMEM; break; }
                items = grown; capacity = next;
            }
            memcpy(items[count].name, item->d_name, strlen(item->d_name) + 1);
            items[count].entry = (botw_savefs_entry){S_ISDIR(info.st_mode) ? 0 : (uint64_t)info.st_size, S_ISDIR(info.st_mode)};
            ++count; errno = 0;
        }
        if (!code && errno) code = errno;
    }
    if (directory && closedir(directory) && !code) code = errno;
    code = close_checked(fd, code);
    code = finish(store, code, error, "Enumerate save directory");
    if (!code) {
        if (count > 1) qsort(items, count, sizeof(*items), compare_item);
        for (size_t i = 0; i < count; ++i) if (callback(user, items[i].name, &items[i].entry)) break;
    }
    free(items); return code;
}
int botw_savefs_commit(botw_savefs *store, botw_savefs_error *error) {
    int code; uint64_t used = 0; size_t count = 0;
    if ((code = lock_store(store, error))) return code;
    code = scan_directory(store->fd, 0, 1, &used, &count);
    return finish(store, code, error, "Fsync save files and directories");
}
