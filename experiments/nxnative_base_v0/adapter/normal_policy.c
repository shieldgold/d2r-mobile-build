#define NXNATIVE_NORMAL_POLICY_IMPLEMENTATION
#include "normal_policy.h"
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>
#undef getenv

static const char *const names[NXNATIVE_NORMAL_ENV_COUNT] = {
    "DM_ROMFS", "DM_SAVE", "DM_TEMP", "DM_NVN_SHMAP", "DM_MSL_DIR", "DM_FONT_DIR", "DM_AOC_DIR"
};
static char *paths[NXNATIVE_NORMAL_ENV_COUNT];
static _Atomic int published;
static pthread_mutex_t setup = PTHREAD_MUTEX_INITIALIZER;

static void fixed_write(const char *message, size_t size) {
    int saved = errno;
    ssize_t result;
    do { result = write(STDERR_FILENO, message, size); } while (result < 0 && errno == EINTR);
    errno = saved;
}
void nxnative_normal_entry_failed(void) {
    static const char message[] = "[nxnative normal] entry preparation failed\n";
    fixed_write(message, sizeof message - 1);
}
_Noreturn void nxnative_normal_failure(void) {
    static const char message[] = "[nxnative normal] host failure\n";
    fixed_write(message, sizeof message - 1);
    _exit(70);
}

int nxnative_normal_environment_apply(const nxnative_normal_environment_entry *entries, size_t count) {
    int incoming = errno, failure = EINVAL;
    char *copies[NXNATIVE_NORMAL_ENV_COUNT] = {0};
    unsigned char seen[NXNATIVE_NORMAL_ENV_COUNT] = {0};
    pthread_mutex_lock(&setup);
    if (atomic_load_explicit(&published, memory_order_relaxed)) { failure = EALREADY; goto reject; }
    if (!entries || count < NXNATIVE_NORMAL_AOC_DIR || count > NXNATIVE_NORMAL_ENV_COUNT) goto reject;
    for (size_t i = 0; i < count; i++) {
        unsigned key = (unsigned)entries[i].key;
        const char *value = entries[i].value;
        if (key >= NXNATIVE_NORMAL_ENV_COUNT || seen[key]++) goto reject;
        if (!value && key == NXNATIVE_NORMAL_AOC_DIR) continue;
        if (!value || value[0] != '/' || strlen(value) > 4096 || strchr(value, '\n') || strchr(value, '\r')) goto reject;
        copies[key] = strdup(value);
        if (!copies[key]) { failure = ENOMEM; goto reject; }
    }
    for (unsigned key = 0; key < NXNATIVE_NORMAL_AOC_DIR; key++) if (!seen[key]) goto reject;
    for (unsigned key = 0; key < NXNATIVE_NORMAL_ENV_COUNT; key++) {
        int result = copies[key] ? setenv(names[key], copies[key], 1) : unsetenv(names[key]);
        if (result) { failure = errno; goto reject; }
    }
    for (unsigned key = 0; key < NXNATIVE_NORMAL_ENV_COUNT; key++) paths[key] = copies[key];
    atomic_store_explicit(&published, 1, memory_order_release);
    pthread_mutex_unlock(&setup);
    errno = incoming;
    return 1;
reject:
    for (unsigned key = 0; key < NXNATIVE_NORMAL_ENV_COUNT; key++) free(copies[key]);
    pthread_mutex_unlock(&setup);
    errno = failure;
    nxnative_normal_entry_failed();
    return 0;
}

char *nxnative_normal_getenv(const char *name) {
    if (!name) return NULL;
    if (!strcmp(name, "HOME") || !strcmp(name, "TMPDIR")) return getenv(name);
    if (!atomic_load_explicit(&published, memory_order_acquire)) return NULL;
    for (unsigned key = 0; key < NXNATIVE_NORMAL_ENV_COUNT; key++)
        if (!strcmp(name, names[key])) return paths[key];
    return NULL;
}
