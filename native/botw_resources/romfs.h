#ifndef BOTW_RESOURCES_ROMFS_H
#define BOTW_RESOURCES_ROMFS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct botw_romfs botw_romfs;

enum {
    BOTW_ROMFS_OK = 0,
    BOTW_ROMFS_EINVAL = 1,
    BOTW_ROMFS_EIO = 2,
    BOTW_ROMFS_ENOMEM = 3,
    BOTW_ROMFS_ENOENT = 4,
    BOTW_ROMFS_EISDIR = 5,
    BOTW_ROMFS_ENOTDIR = 6,
    BOTW_ROMFS_ERANGE = 7
};

typedef struct {
    int code;
    char message[192];
} botw_romfs_error;

typedef struct {
    uint64_t offset; /* Absolute byte offset in romfs.bin; directories use zero. */
    uint64_t size;
    int is_directory;
} botw_romfs_entry;

typedef int (*botw_romfs_directory_callback)(void *user, const char *name,
                                            const botw_romfs_entry *entry);

/* Reads at most 64 MiB metadata and builds an index capped at 64 MiB.
 * The image is never mapped or copied. No key material is needed or retained.
 * The caller must keep close() synchronized with all readers. Otherwise stat,
 * pread and iterate are safe to call concurrently on an immutable image.
 * Open verifies structure and bounds, not a Nintendo signature or content hash.
 */
botw_romfs *botw_romfs_open(const char *image_path, botw_romfs_error *error);
void botw_romfs_close(botw_romfs *romfs);

/* Paths are absolute UTF-8 POSIX paths. Traversal, empty components, control
 * characters, backslashes and colons are rejected. Success returns zero.
 * error is optional and is cleared on success.
 */
int botw_romfs_stat(const botw_romfs *romfs, const char *path,
                    botw_romfs_entry *entry, botw_romfs_error *error);

/* Each request is limited to 16 MiB. Reads crossing EOF are shortened and
 * offsets at or beyond EOF succeed with zero bytes. actual is required and is
 * zeroed on errors. A short host read fails; the caller must ignore the buffer.
 */
int botw_romfs_pread(const botw_romfs *romfs, const char *path, uint64_t offset,
                     void *buffer, size_t length, size_t *actual,
                     botw_romfs_error *error);

/* Visits immediate children in UTF-8 byte order. name is a borrowed component,
 * valid only during the callback. A nonzero callback return stops successfully.
 */
int botw_romfs_iterate(const botw_romfs *romfs, const char *path,
                       botw_romfs_directory_callback callback, void *user,
                       botw_romfs_error *error);

#ifdef __cplusplus
}
#endif
#endif
