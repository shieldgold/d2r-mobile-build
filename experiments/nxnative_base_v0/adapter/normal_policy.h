/* First normal-entry slice only; crash/HLE/GPU diagnostics remain pending. */
#pragma once
#include <stddef.h>
#include <stdlib.h>

typedef enum {
    NXNATIVE_NORMAL_ROMFS,
    NXNATIVE_NORMAL_SAVE,
    NXNATIVE_NORMAL_TEMP,
    NXNATIVE_NORMAL_SHMAP,
    NXNATIVE_NORMAL_MSL_DIR,
    NXNATIVE_NORMAL_FONT_DIR,
    NXNATIVE_NORMAL_AOC_DIR,
    NXNATIVE_NORMAL_ENV_COUNT
} nxnative_normal_environment_key;
typedef struct {
    nxnative_normal_environment_key key;
    const char *value;
} nxnative_normal_environment_entry;
/* Six mandatory paths; optional AOC may be absent/NULL. Publish once, only
 * after every setter succeeds. Failed libc changes are not rolled back. */
int nxnative_normal_environment_apply(const nxnative_normal_environment_entry *entries, size_t count);
char *nxnative_normal_getenv(const char *name);
void nxnative_normal_entry_failed(void);
_Noreturn void nxnative_normal_failure(void);

#if defined(NXNATIVE_NORMAL_POLICY) && NXNATIVE_NORMAL_POLICY && !defined(NXNATIVE_NORMAL_POLICY_IMPLEMENTATION)
#define getenv(name) nxnative_normal_getenv(name)
#endif
