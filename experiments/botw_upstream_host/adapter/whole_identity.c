/* SPDX-License-Identifier: MIT */
#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "whole_identity.h"
#include "whole_policy.h"
#include "botw_account.h"
#include "local_user.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static pthread_mutex_t identity_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic int identity_ready;
static botw_uid identity_uid;
static char identity_save[4096];

static int failed(char *error, size_t capacity, int code, const char *phase) {
    if (error && capacity) snprintf(error, capacity, "%s: %s", phase, strerror(code));
    return code;
}
static int existing_private_directory(int parent, const char *name) {
    int fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st)) { int code = errno; close(fd); errno = code; return -1; }
    if (!S_ISDIR(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 07777) != 0700) {
        close(fd); errno = EPERM; return -1;
    }
    return fd;
}

int whole_identity_bind_documents(const char *documents, char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    botw_local_user user; botw_local_user_error user_error;
    int code = botw_local_user_load_existing(documents, &user, &user_error);
    if (code) return failed(error, capacity, code, "load existing offline profile");
    char save[sizeof identity_save];
    int length = snprintf(save, sizeof save, "%s/botw-saves/01007ef00011e000/%s", documents, user.uid_hex);
    if (length < 0 || (size_t)length >= sizeof save)
        return failed(error, capacity, ENAMETOOLONG, "existing save directory");
    int documents_fd = open(documents, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (documents_fd < 0) return failed(error, capacity, errno, "open existing Documents");
    struct stat st;
    if (fstat(documents_fd, &st)) code = errno;
    else if (!S_ISDIR(st.st_mode) || st.st_uid != geteuid()) code = EPERM;
    int base = -1, title = -1, account = -1;
    if (!code && (base = existing_private_directory(documents_fd, "botw-saves")) < 0) code = errno;
    if (!code && (title = existing_private_directory(base, "01007ef00011e000")) < 0) code = errno;
    if (!code && (account = existing_private_directory(title, user.uid_hex)) < 0) code = errno;
    if (account >= 0) close(account);
    if (title >= 0) close(title);
    if (base >= 0) close(base);
    close(documents_fd);
    if (code) return failed(error, capacity, code, "validate existing account save directory");
    pthread_mutex_lock(&identity_lock);
    if (atomic_load_explicit(&identity_ready, memory_order_acquire)) {
        if (memcmp(&identity_uid, user.uid, 16) || strcmp(identity_save, save)) code = EBUSY;
    } else {
        memcpy(&identity_uid, user.uid, 16);
        memcpy(identity_save, save, (size_t)length + 1);
        atomic_store_explicit(&identity_ready, 1, memory_order_release);
    }
    pthread_mutex_unlock(&identity_lock);
    return code ? failed(error, capacity, code, "offline account cannot change during a run") : 0;
}

const botw_uid *whole_identity_uid(void) {
    if (!atomic_load_explicit(&identity_ready, memory_order_acquire)) botw_whole_host_failure();
    return &identity_uid;
}
const char *whole_identity_save_directory(void) {
    return atomic_load_explicit(&identity_ready, memory_order_acquire) ? identity_save : NULL;
}
