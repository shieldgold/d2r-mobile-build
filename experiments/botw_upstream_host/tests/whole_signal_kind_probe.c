/* SPDX-License-Identifier: MIT
 * Own CPU objects only. Include the actual patched signal dispatcher and link
 * the real policy producer; no OS signal is sent and no guest is loaded. */
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/ucontext.h>
#include <unistd.h>
#include "whole_policy.h"
#include "crash.c"

struct owned_context {
    unsigned char before[32];
    siginfo_t signal_info;
    ucontext_t context;
    struct __darwin_mcontext64 machine;
    unsigned char after[32];
};
static unsigned char owned_payload[64];
static void *expected_context;
static int calls;

static int owned_dirty(void *address, int write, void *context) {
    assert(address == owned_payload);
    assert(write == 1);
    assert(context == expected_context);
    ++calls;
    return 1;
}
static int forbidden_second_dirty(void *address, int write, void *context) {
    (void)address; (void)write; (void)context;
    assert(!"handled dirty fault reached a later callback");
    return 0;
}
static int forbidden_trap(ucontext_t *context) {
    (void)context;
    assert(!"normal failure reached trap diagnostics");
    return 0;
}
static void forbidden_diagnostic(void) {
    assert(!"normal failure reached diagnostic callbacks");
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const char *mode = argv[1];
    if (!strcmp(mode, "legacy")) botw_whole_host_signal_failure(SIGSEGV);
    const size_t page = (size_t)sysconf(_SC_PAGESIZE);
    const size_t bytes = (sizeof(struct owned_context) + page - 1) / page * page;
    struct owned_context *owned = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
                                      MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(owned != MAP_FAILED);
    memset(owned, 0xa7, bytes);
    owned->context.uc_mcontext = &owned->machine;
    owned->machine.__es.__esr = 1u << 6;
    owned->signal_info.si_addr = owned_payload;
    int sig = SIGSEGV, handled = 0, inaccessible_context = 0, null_info = 0;
    if (!strcmp(mode, "mapping")) owned->signal_info.si_code = SEGV_MAPERR;
    else if (!strcmp(mode, "protection")) owned->signal_info.si_code = SEGV_ACCERR;
    else if (!strcmp(mode, "unknown-zero")) owned->signal_info.si_code = 0;
    else if (!strcmp(mode, "unknown-positive")) owned->signal_info.si_code = 9876;
    else if (!strcmp(mode, "unknown-negative")) owned->signal_info.si_code = -9876;
    else if (!strcmp(mode, "bus-mapping")) { sig = SIGBUS; owned->signal_info.si_code = SEGV_MAPERR; }
    else if (!strcmp(mode, "ill-protection")) {
        sig = SIGILL; owned->signal_info.si_code = SEGV_ACCERR; inaccessible_context = 1;
    } else if (!strcmp(mode, "trap-mapping")) {
        sig = SIGTRAP; owned->signal_info.si_code = SEGV_MAPERR; inaccessible_context = 1;
    } else if (!strcmp(mode, "null-info")) null_info = 1;
    else if (!strcmp(mode, "dirty-mapping")) { handled = 1; owned->signal_info.si_code = SEGV_MAPERR; }
    else if (!strcmp(mode, "dirty-protection")) { handled = 1; owned->signal_info.si_code = SEGV_ACCERR; }
    else if (!strcmp(mode, "dirty-bus")) { handled = 1; sig = SIGBUS; owned->signal_info.si_code = SEGV_MAPERR; }
    else return 5;

    g_dump_hook = forbidden_diagnostic;
    g_wait_dump = forbidden_diagnostic;
    g_trap_hook = forbidden_trap;
    unsigned char *snapshot = malloc(bytes);
    assert(snapshot);
    memcpy(snapshot, owned, bytes);
    expected_context = &owned->context;
    if (handled) {
        dm_fault_hook_register(owned_dirty);
        dm_fault_hook_register(forbidden_second_dirty);
    }
    /* Both classification inputs and all context/canary bytes are read-only.
     * The unchanged dirty path may read ESR and pass only these owned objects. */
    assert(mprotect(owned, bytes, PROT_READ) == 0);
    void *context = &owned->context;
    void *guard = NULL;
    if (inaccessible_context) {
        guard = mmap(NULL, page, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
        assert(guard != MAP_FAILED);
        context = guard;
    }
    errno = EDOM;
    on_crash(sig, null_info ? NULL : &owned->signal_info, context);
    assert(handled && calls == 1);
    assert(errno == EDOM);
    assert(memcmp(owned, snapshot, bytes) == 0);
    assert(munmap(owned, bytes) == 0);
    if (guard) assert(munmap(guard, page) == 0);
    free(snapshot);
    return 0;
}
