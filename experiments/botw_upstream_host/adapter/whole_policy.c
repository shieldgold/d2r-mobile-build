/* SPDX-License-Identifier: MIT */
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include <string.h>
/* Also support a build-wide -include applied before this source is parsed. */
#undef getenv
#undef backtrace
#undef backtrace_symbols_fd
#undef abort
#undef exit
#undef _exit

char *botw_whole_host_getenv(const char *name) {
    if (!name) return NULL;
    if (!strcmp(name, "DM_STOP_ON_UNIMPL")) return "1";
    if (!strcmp(name, "DM_TITLE")) return "botw";
    /* NULL enables the upstream optimization/default at these consumers.
     * Explicit zero/off values preserve the conservative baseline. */
    static const char *const zero[] = {
        "DM_NVN_ZSKIP", "DM_NVN_UNWRITTEN_DC", "DM_NVN_TBDR", "DM_NVN_XV",
        "DM_NVN_DEFER", "DM_NVN_BLOOM_Q", "DM_RT_AB", "DM_LONGFRAME",
        "DM_NVN_FRAMES", "DM_MEM_CAP_GB", "DM_PRESENT_LINK",
        "DM_APP_POOL_ADD_MB", "DM_PERF_FILE"
    };
    for (size_t i = 0; i < sizeof zero / sizeof *zero; i++)
        if (!strcmp(name, zero[i])) return "0";
    if (!strcmp(name, "DM_PSO_MANIFEST")) return "off";
    /* Only product paths, resource/version selection and input/audio settings
     * can reach libc. Unknown future switches are disabled until reviewed.
     * HID's ordinary virtual controller remains available to mobile input. */
    static const char *const product[] = {
        "HOME", "TMPDIR", "TZ", "DM_ROOT", "DM_ROMFS", "DM_SAVE", "DM_TEMP",
        "DM_GAME_VERSION", "DM_BOTW_SHADER_MANIFEST", "DM_ARGV0", "DM_MODULES",
        "DM_AUDIO", "DM_BOTW_AUDIO_CPU", "DM_BOTW_POOL_POLICY", "DM_LANG", "DM_OPMODE",
        "DM_WINDOW", "DM_PRESENT_HZ", "DM_PRESENT_INTERVAL", "DM_PRESENT_LATENCY",
        "DM_HID_NOPAD", "DM_HID_MOUSE_GAIN", "DM_HID_PAD_LAYOUT"
    };
    for (size_t i = 0; i < sizeof product / sizeof *product; i++)
        if (!strcmp(name, product[i])) return getenv(name);
    if (!strncmp(name, "DM_DLC_", 7) && name[7] >= '1' && name[7] <= '9' &&
        (!name[8] || (name[8] >= '0' && name[8] <= '9' && !name[9])))
        return getenv(name);
    return NULL;
}
int botw_backend_compat_diagnostic_enabled(void) { return 0; }
int botw_whole_host_backtrace(void **buffer, int size) {
    (void)buffer; (void)size; return 0;
}
void botw_whole_host_backtrace_symbols_fd(void *const *buffer, int size, int fd) {
    (void)buffer; (void)size; (void)fd;
}
_Noreturn void botw_backend_host_diagnostic_denied(const char *operation) {
    /* Do not inspect even the operation argument: callers may be faulty.
     * write/_exit are async-signal-safe, unlike formatting or callbacks. */
    (void)operation;
    static const char message[] = "[botw whole-host] diagnostic entry disabled\n";
    (void)write(STDERR_FILENO, message, sizeof message - 1);
    _exit(77);
}
_Noreturn void botw_whole_host_failure(void) {
    static const char message[] = "[botw whole-host] upstream failure\n";
    (void)write(STDERR_FILENO, message, sizeof message - 1);
    _exit(70);
}
_Noreturn void botw_whole_host_signal_failure(int signal_number) {
    static const char message[] = "[botw whole-host] unhandled host signal\n";
    (void)write(STDERR_FILENO, message, sizeof message - 1);
    _exit(signal_number > 0 && signal_number < 128 ? 128 + signal_number : 1);
}
_Noreturn void botw_whole_host_exit(int status) {
    if (status) {
        static const char message[] = "[botw whole-host] upstream stopped\n";
        (void)write(STDERR_FILENO, message, sizeof message - 1);
    }
    _exit(status >= 0 && status <= 255 ? status : 1);
}
