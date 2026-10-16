#ifndef BOTW_RESOURCES_LOCAL_USER_H
#define BOTW_RESOURCES_LOCAL_USER_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint8_t uid[16]; char uid_hex[33]; } botw_local_user;
typedef struct { int code; char message[192]; } botw_local_user_error;
/* Open/create only trusted_absolute_documents/botw-profile/uid.bin.
 * The caller supplies its own App-container Documents path; ancestor paths
 * are trusted (including system /var aliases), the final directory must not
 * be a symlink. Absolute paths with dot/traversal/empty components or trailing
 * slash are rejected. Documents must already exist and belong to this user.
 *
 * This is a random local offline profile, not a Nintendo/Apple account. UID
 * bytes are opaque and uid_hex is their ordered 32-character lowercase hex.
 * Reopening returns exactly the same ID. Profile/files are private 0700/0600;
 * symlinks, hardlinked/special files, foreign owners and damaged UID data are
 * rejected without repair. Creation is flock-serialized and atomically
 * published without replacing existing files, with file/directory fsync.
 *
 * Returns 0 or a POSIX errno (also recorded in optional error). Output is
 * unchanged on failure. An fsync error can occur after a complete file was
 * published; retry reads that identity rather than replacing it. */
int botw_local_user_open(const char *trusted_absolute_documents,
    botw_local_user *output, botw_local_user_error *error);
/* Load only an existing profile, lock and UID. Apply the same format, inode,
 * owner and permission checks as open(), without creating or repairing any
 * path. This preserves the account when adopting an existing App container.
 * Missing paths are errors; output is unchanged on every failure. */
int botw_local_user_load_existing(const char *trusted_absolute_documents,
    botw_local_user *output, botw_local_user_error *error);
#ifdef __cplusplus
}
#endif
#endif
