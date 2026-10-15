#define _GNU_SOURCE 1
#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "local_user.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/random.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif

#define PROFILE_DIRECTORY "botw-profile"
#define UID_FILE "uid.bin"
#define LOCK_FILE ".lock"
#define FILE_BYTES 32
static const uint8_t magic[8] = {'B','O','T','W','U','I','D','1'};
static int failure(botw_local_user_error *error, int code, const char *phase) {
    if (!code) code = EIO;
    if (error) {
        error->code = code;
        snprintf(error->message, sizeof error->message, "%s: %s", phase, strerror(code));
    }
    return code;
}
static int valid_path(const char *path) {
    if (!path || path[0] != '/') return EINVAL;
    size_t length = strnlen(path, PATH_MAX + 1);
    if (length > PATH_MAX) return ENAMETOOLONG;
    if (length < 2 || path[length - 1] == '/') return EINVAL;
    const char *component = path + 1;
    for (const char *p = component;; p++) if (*p == '/' || !*p) {
        size_t size = (size_t)(p - component);
        if (!size || (size == 1 && component[0] == '.') ||
            (size == 2 && component[0] == '.' && component[1] == '.')) return EINVAL;
        if (size > NAME_MAX) return ENAMETOOLONG;
        if (!*p) return 0;
        component = p + 1;
    }
}
static int private_directory(int fd) {
    struct stat info;
    if (fstat(fd, &info)) return errno;
    if (!S_ISDIR(info.st_mode)) return ENOTDIR;
    return info.st_uid == geteuid() && (info.st_mode & 07777) == 0700 ? 0 : EPERM;
}
static int private_file(int fd, off_t size) {
    struct stat info;
    if (fstat(fd, &info)) return errno;
    if (!S_ISREG(info.st_mode)) return EINVAL;
    if (info.st_nlink != 1) return EMLINK;
    if (info.st_uid != geteuid() || (info.st_mode & 07777) != 0600) return EPERM;
    return info.st_size == size ? 0 : EBADMSG;
}
static int same_named_inode(int parent, const char *name, int fd) {
    struct stat opened, named;
    if (fstat(fd, &opened) || fstatat(parent, name, &named, AT_SYMLINK_NOFOLLOW)) return errno;
    return opened.st_dev == named.st_dev && opened.st_ino == named.st_ino ? 0 : ESTALE;
}
static uint32_t checksum(const uint8_t *bytes, size_t length) {
    uint32_t value = UINT32_MAX;
    for (size_t i = 0; i < length; i++) {
        value ^= bytes[i];
        for (unsigned bit = 0; bit < 8; bit++)
            value = (value >> 1) ^ ((value & 1) ? UINT32_C(0xedb88320) : 0);
    }
    return ~value;
}
static int nonzero_uid(const uint8_t *uid) {
    unsigned value = 0;
    for (size_t i = 0; i < 16; i++) value |= uid[i];
    return value != 0;
}
static void format_hex(const uint8_t *bytes, char *hex) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < 16; i++) { hex[i * 2] = digits[bytes[i] >> 4]; hex[i * 2 + 1] = digits[bytes[i] & 15]; }
    hex[32] = 0;
}
static int read_identity(int fd, botw_local_user *user) {
    int code = private_file(fd, FILE_BYTES);
    if (code) return code;
    uint8_t bytes[FILE_BYTES]; size_t done = 0;
    while (done < sizeof bytes) {
        ssize_t count = pread(fd, bytes + done, sizeof bytes - done, (off_t)done);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return errno;
        if (!count) return EBADMSG;
        done += (size_t)count;
    }
    uint32_t stored = 0;
    for (unsigned i = 0; i < 4; i++) stored |= (uint32_t)bytes[28 + i] << (i * 8);
    if (memcmp(bytes, magic, sizeof magic) || bytes[24] || bytes[25] || bytes[26] || bytes[27] ||
        stored != checksum(bytes, 28) || !nonzero_uid(bytes + 8)) return EBADMSG;
    code = private_file(fd, FILE_BYTES);
    if (code) return code;
    memcpy(user->uid, bytes + 8, 16); format_hex(user->uid, user->uid_hex); return 0;
}
static int entropy(uint8_t *bytes) {
    int result;
    do { result = getentropy(bytes, 16); } while (result && errno == EINTR);
    return result ? (errno ? errno : EIO) : 0;
}
static int write_all(int fd, const uint8_t *bytes, size_t length) {
    size_t done = 0;
    while (done < length) {
        ssize_t count = write(fd, bytes + done, length - done);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return errno;
        if (!count) return EIO;
        done += (size_t)count;
    }
    return 0;
}
static int publish_exclusive(int directory, const char *temporary) {
#ifdef __APPLE__
    return renameatx_np(directory, temporary, directory, UID_FILE, RENAME_EXCL) ? errno : 0;
#elif defined(__linux__) && defined(SYS_renameat2)
    return syscall(SYS_renameat2, directory, temporary, directory, UID_FILE, 1u) ? errno : 0;
#else
    (void)directory; (void)temporary; return ENOTSUP;
#endif
}
static int create_identity(int directory, botw_local_user *user) {
    int code = EIO;
    for (unsigned attempt = 0; attempt < 16; attempt++) {
        code = entropy(user->uid);
        if (code) return code;
        if (nonzero_uid(user->uid)) break;
        if (attempt == 15) return EIO;
    }
    format_hex(user->uid, user->uid_hex);
    int fd = -1; char temporary[64] = {0};
    for (unsigned attempt = 0; attempt < 16; attempt++) {
        uint8_t random[16]; char suffix[33]; code = entropy(random);
        if (code) return code;
        format_hex(random, suffix); snprintf(temporary, sizeof temporary, ".uid-%s.tmp", suffix);
        fd = openat(directory, temporary, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd >= 0) break;
        if (errno != EEXIST) return errno;
    }
    if (fd < 0) return EEXIST;
    uint8_t bytes[FILE_BYTES] = {0}; memcpy(bytes, magic, sizeof magic); memcpy(bytes + 8, user->uid, 16);
    uint32_t crc = checksum(bytes, 28);
    for (unsigned i = 0; i < 4; i++) bytes[28 + i] = (uint8_t)(crc >> (i * 8));
    if (fchmod(fd, 0600)) code = errno;
    else code = write_all(fd, bytes, sizeof bytes);
    if (!code && fsync(fd)) code = errno;
    if (!code) code = private_file(fd, FILE_BYTES);
    if (!code) code = same_named_inode(directory, temporary, fd);
    if (!code) code = publish_exclusive(directory, temporary);
    if (!code) {
        temporary[0] = 0;
        code = private_file(fd, FILE_BYTES);
        if (!code) code = same_named_inode(directory, UID_FILE, fd);
        if (!code && fsync(directory)) code = errno;
    }
    if (temporary[0] && !same_named_inode(directory, temporary, fd)) (void)unlinkat(directory, temporary, 0);
    close(fd); return code;
}
int botw_local_user_open(const char *documents, botw_local_user *output, botw_local_user_error *error) {
    const char *phase = "validate trusted Documents path";
    int code = output ? valid_path(documents) : EINVAL;
    if (code) return failure(error, code, phase);
    int parent = -1, directory = -1, lock = -1, identity = -1;
    botw_local_user user;
    phase = "open App Documents directory";
    parent = open(documents, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (parent < 0) { code = errno; goto done; }
    struct stat info;
    if (fstat(parent, &info)) { code = errno; goto done; }
    if (!S_ISDIR(info.st_mode) || info.st_uid != geteuid()) { code = EPERM; goto done; }
    phase = "create private offline profile directory";
    int created = !mkdirat(parent, PROFILE_DIRECTORY, 0700);
    if (!created && errno != EEXIST) { code = errno; goto done; }
    directory = openat(parent, PROFILE_DIRECTORY, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) { code = errno; goto done; }
    code = private_directory(directory);
    if (code) goto done;
    /* A concurrent opener can see the new directory before its creator has
     * synced Documents. Every successful opener makes that entry durable. */
    if (fsync(parent)) { code = errno; goto done; }
    phase = "open private offline profile lock";
    lock = openat(directory, LOCK_FILE, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
    created = lock >= 0;
    if (lock < 0 && errno == EEXIST) lock = openat(directory, LOCK_FILE, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (lock < 0) { code = errno; goto done; }
    if (created && fchmod(lock, 0600)) { code = errno; goto done; }
    code = private_file(lock, 0);
    if (code) goto done;
    phase = "lock offline profile";
    while (flock(lock, LOCK_EX)) { if (errno != EINTR) { code = errno; goto done; } }
    code = private_file(lock, 0);
    if (!code) code = same_named_inode(directory, LOCK_FILE, lock);
    if (code) goto done;
    if (created && (fsync(lock) || fsync(directory))) { code = errno; goto done; }
    phase = "validate persistent offline UID";
    identity = openat(directory, UID_FILE, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (identity >= 0) {
        code = read_identity(identity, &user);
        if (!code) code = same_named_inode(directory, UID_FILE, identity);
    } else if (errno == ENOENT) {
        phase = "atomically publish private offline UID";
        code = create_identity(directory, &user);
    } else code = errno;
    if (!code) code = private_directory(directory);
    if (!code) code = same_named_inode(parent, PROFILE_DIRECTORY, directory);
done:
    if (identity >= 0) close(identity);
    if (lock >= 0) close(lock);
    if (directory >= 0) close(directory);
    if (parent >= 0) close(parent);
    if (code) return failure(error, code, phase);
    *output = user;
    if (error) { error->code = 0; error->message[0] = 0; }
    return 0;
}
