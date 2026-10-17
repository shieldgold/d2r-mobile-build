#include "normal_failures.h"
#include <errno.h>
#include <signal.h>
#include <stddef.h>
#include <unistd.h>

static void fixed_write(const char *literal, size_t size) {
    int saved = errno;
    ssize_t result;
    do { result = write(STDERR_FILENO, literal, size); } while (result < 0 && errno == EINTR);
    errno = saved;
}
#define FAIL(literal, status) do { \
    static const char message[] = "[nxnative normal] " literal "\n"; \
    fixed_write(message, sizeof message - 1); \
    _exit(status); \
} while (0)

_Noreturn void nxnative_normal_fail(nxnative_normal_failure_kind kind) {
    switch (kind) {
    case NXNATIVE_NORMAL_UNIMPLEMENTED_IMPORT: FAIL("unimplemented import", 3);
    case NXNATIVE_NORMAL_UNIMPLEMENTED_SVC: FAIL("unimplemented svc", 3);
    case NXNATIVE_NORMAL_DIAGNOSTIC_DISABLED: FAIL("diagnostic entry disabled", 77);
    case NXNATIVE_NORMAL_FATAL_BREAK: FAIL("guest requested fatal break", 42);
    case NXNATIVE_NORMAL_SDK_ABORT: FAIL("sdk abort", 70);
    }
    FAIL("host failure", 70);
}

_Noreturn void nxnative_normal_signal_failure(int signal_number, int signal_code) {
    int status = signal_number > 0 && signal_number < NSIG ? 128 + signal_number : 70;
    if (signal_number == SIGSEGV && signal_code == SEGV_MAPERR)
        FAIL("unhandled host mapping fault", status);
    if (signal_number == SIGSEGV && signal_code == SEGV_ACCERR)
        FAIL("unhandled host protection fault", status);
    FAIL("unhandled host signal", status);
}
