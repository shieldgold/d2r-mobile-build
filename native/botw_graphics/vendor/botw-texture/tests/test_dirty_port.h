/* Synthetic test bridge; production installs no signal handlers here. */
#pragma once
#include "../nvn_dirty.h"
#include <assert.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/ucontext.h>
#include <unistd.h>
static void test_dirty_signal(int number, siginfo_t *info, void *context) {
    (void)number;
#if defined(__aarch64__)
    ucontext_t *u = context;
    unsigned esr = u->uc_mcontext->__es.__esr;
    unsigned ec = esr >> 26;
    int write = (ec == 0x24 || ec == 0x25) && (esr & 0x40);
#elif defined(__x86_64__)
    ucontext_t *u = context;
    int write = !!(u->uc_mcontext->__es.__err & 2);
#else
#error Unsupported synthetic signal fixture architecture
#endif
    if (!nvn_dirty_handle_fault(info->si_addr, write, context)) _exit(88);
}
static int test_dirty_registration(int (*fault)(void *, int, void *),
                                   int (*prepare)(void *, size_t, char *, size_t)) {
    assert(fault == nvn_dirty_handle_fault && prepare == nvn_dirty_prepare);
    struct sigaction a = {0}; a.sa_sigaction = test_dirty_signal;
    a.sa_flags = SA_SIGINFO;
    assert(!sigaction(SIGBUS, &a, NULL) && !sigaction(SIGSEGV, &a, NULL));
    return 0;
}
static void test_dirty_install(void) { assert(!nvn_dirty_configure_registration(test_dirty_registration)); }
static void dm_guest_write_prepare(void *p, size_t n) { assert(!nvn_dirty_prepare(p, n, NULL, 0)); }
