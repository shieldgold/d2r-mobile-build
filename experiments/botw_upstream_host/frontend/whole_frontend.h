#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { WHOLE_FRONTEND_RUNTIME_ABI = 1 };
/* All callbacks return 0 on success and a nonzero error on failure. On failure,
 * write a NUL-terminated diagnostic when error_capacity is nonzero. The runtime
 * object is copied; context and callback code must remain valid for the process.
 * No callback may execute guest instructions or unload live host resources. */
typedef struct {
    uint32_t abi;
    void *context;
    int host_argc;
    const char *const *host_argv;
    /* Runs once on an owned 8 MiB host thread, before host_main or preload.
     * Must verify the precise pinned host/guest rewrite ABI and game version,
     * all module/profile identities, the actual RomFS representation and mount
     * support, and the original account UID/save compatibility. Set every host
     * environment/path explicitly; never rename an old profile to 1.9.0, treat
     * RomFS.bin as a directory, select a fallback UID, or create a new save root.
     * A matching runtime must already be packaged; no defaults imply readiness. */
    int (*prepare)(void *context, char *error, size_t error_capacity);
    /* Runs on UIKit main after prepare succeeds, and on each lifecycle change.
     * Must publish foreground/focus through the real BotW applet producer and
     * suspend/resume the real audio backend according to audio_available.
     * The inactive call must release host activity without executing guest code.
     * HID release is separately owned by this frontend and cannot replace this
     * callback. An absent producer must report failure, never pretend success. */
    int (*lifecycle)(void *context, int foreground, int focused, int audio_available,
                     char *error, size_t error_capacity);
    /* The complete upstream main.c, renamed at compilation. It preserves guest
     * TLS/SDK/module initialization and later calls this frontend's front_run. */
    int (*host_main)(int argc, char **argv);
} whole_frontend_runtime;

/* Checks only callback/argument shape. It does not verify game resources; that
 * remains prepare's mandatory responsibility before runtime_ready is set. */
int whole_frontend_runtime_contract_valid(const whole_frontend_runtime *runtime);

/* UIKit entry, called once on process main. host_argv is deep-copied before UI
 * startup. A missing/invalid runtime displays a blocked state and never calls
 * prepare, host_main, preload, or guest code. This is not gameplay acceptance. */
int whole_frontend_main(int argc, char **argv, const whole_frontend_runtime *runtime);

/* Optional provider: only referenced by whole_main.m when the build explicitly
 * defines BOTW_WHOLE_RUNTIME_CONFIGURED. There is no fallback implementation. */
const whole_frontend_runtime *whole_runtime_configuration(void);

double front_display_refresh(char *name, size_t capacity);
#ifdef __cplusplus
}
#endif
