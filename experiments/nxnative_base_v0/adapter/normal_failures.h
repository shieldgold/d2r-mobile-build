#pragma once

/* Fixed normal-runtime failures. No format, address or Guest frame input. */
typedef enum {
    NXNATIVE_NORMAL_UNIMPLEMENTED_IMPORT,
    NXNATIVE_NORMAL_UNIMPLEMENTED_SVC,
    NXNATIVE_NORMAL_DIAGNOSTIC_DISABLED,
    NXNATIVE_NORMAL_FATAL_BREAK,
    NXNATIVE_NORMAL_SDK_ABORT
} nxnative_normal_failure_kind;

_Noreturn void nxnative_normal_fail(nxnative_normal_failure_kind kind);
/* Only the OS signal number/code; never a fault address or saved context. */
_Noreturn void nxnative_normal_signal_failure(int signal_number, int signal_code);
