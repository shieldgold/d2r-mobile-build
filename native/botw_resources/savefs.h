#ifndef BOTW_RESOURCES_SAVEFS_H
#define BOTW_RESOURCES_SAVEFS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct botw_savefs botw_savefs;
typedef struct { int code; char message[192]; } botw_savefs_error;
typedef struct { uint64_t size; int is_directory; } botw_savefs_entry;
typedef int (*botw_savefs_directory_callback)(void *user, const char *name,
                                             const botw_savefs_entry *entry);

/* base_directory is a trusted App-container path, e.g. Documents/botw-saves.
 * Creates only its final component and <16 lowercase-hex title>/<32 lowercase-hex user>.
 * Existing symlink roots/children and nonregular files are rejected. One mount
 * holds an exclusive advisory lock; share this instance between FSP sessions.
 * quota_bytes caps the sum of actual regular-file logical sizes (from fstat),
 * not filesystem capacity. Zero means no configured quota. OS disk errors are
 * still returned. All errors return a POSIX errno; error may be NULL.
 */
botw_savefs *botw_savefs_open(const char *base_directory, const char *title_id,
                              const char *user_id, uint64_t quota_bytes,
                              botw_savefs_error *error);
/* close does not commit; check commit before releasing a modified store. */
void botw_savefs_close(botw_savefs *store);
/* Borrowed immutable IDs validated at open; NULL store returns NULL. */
const char *botw_savefs_title_id(const botw_savefs *store);
const char *botw_savefs_user_id(const botw_savefs *store);
int botw_savefs_usage(botw_savefs *store, uint64_t *used_bytes,
                      uint64_t *quota_bytes, botw_savefs_error *error);
int botw_savefs_stat(botw_savefs *store, const char *path,
                     botw_savefs_entry *entry, botw_savefs_error *error);
int botw_savefs_create(botw_savefs *store, const char *path, uint64_t initial_size,
                       botw_savefs_error *error);
int botw_savefs_mkdir(botw_savefs *store, const char *path,
                      botw_savefs_error *error);
int botw_savefs_truncate(botw_savefs *store, const char *path, uint64_t size,
                         botw_savefs_error *error);
/* Absolute UTF-8 guest paths only; components are at most 255 bytes, total at
 * most 768 bytes. No traversal, symlinks, hardlinked files or special files.
 * Requests cap at 16 MiB. Reads clamp at EOF, including UINT64_MAX offsets.
 * actual is required and zeroed initially. Writes report actual bytes changed
 * even if a subsequent host write fails; callers must honor a nonzero result.
 */
int botw_savefs_pread(botw_savefs *store, const char *path, uint64_t offset,
                      void *buffer, size_t length, size_t *actual,
                      botw_savefs_error *error);
int botw_savefs_pwrite(botw_savefs *store, const char *path, uint64_t offset,
                       const void *buffer, size_t length, size_t *actual,
                       botw_savefs_error *error);
/* Rename never replaces an existing destination. remove deletes regular files
 * or empty directories; it does not recursively erase directories. */
int botw_savefs_rename(botw_savefs *store, const char *source,
                       const char *destination, botw_savefs_error *error);
int botw_savefs_remove(botw_savefs *store, const char *path,
                       botw_savefs_error *error);
/* Stable UTF-8 byte-order snapshot of immediate children. Callback nonzero
 * stops successfully; callbacks run without the mutation lock and may reenter.
 */
int botw_savefs_iterate(botw_savefs *store, const char *path,
                        botw_savefs_directory_callback callback, void *user,
                        botw_savefs_error *error);
/* Recursively fsync regular files and directories, then the store root.
 * Mutations and quota checks are serialized; synchronize close with callers. */
int botw_savefs_commit(botw_savefs *store, botw_savefs_error *error);
#ifdef __cplusplus
}
#endif
#endif
