/* Own descriptor calls only. Include the entire actually patched title TU and
 * link real whole_policy.c. No Guest, register sample or OS signal executes. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "whole_policy.h"
#include "title_botw.c"

/* Unrelated descriptor hooks are strict tripwires, never successful substitutes.
 * The tests exercise only svc_host_call and inspect the other hook identities. */
static _Noreturn void unrelated_hook_called(void) { _Exit(94); }
void botw_base_v0_heap_enable(void) { unrelated_hook_called(); }
size_t dm_guest_heap_current(void) { unrelated_hook_called(); }
int botw_base_v0_heap_verify(size_t accepted, char *error, size_t capacity) {
    (void)accepted; (void)error; (void)capacity;
    unrelated_hook_called();
}

static uint64_t dispatch(int number, uint64_t *owned_argument) {
    return botw_title.svc_host_call ? botw_title.svc_host_call(number, owned_argument) : 0;
}
static void no_side_effect(int number, uint64_t *owned_argument) {
    errno = EDOM;
    assert(dispatch(number, owned_argument) == 0);
    assert(errno == EDOM);
}
static void descriptor_contract(void) {
    assert(dm_title_reg_botw_title == &botw_title);
    assert(botw_title.id == DM_TITLE_BOTW);
    assert(!strcmp(botw_title.name, "botw") && !strcmp(botw_title.argv0, "BotW"));
    assert(botw_title.program_id == UINT64_C(0x01007EF00011E000));
    assert(botw_title.on_select == botw_base_v0_heap_enable);
    assert(botw_title.before_nnmain == base_v0_before_nnmain);
    assert(!botw_title.app_bootstrap && !botw_title.app_boot && !botw_title.host_early);
    assert(!botw_title.post_load && !botw_title.prefetch && !botw_title.guest_early);
    static const char *const expected_modules[] = {"main", "subsdk0", "sdk"};
    for (unsigned i = 0; i < 3; ++i) assert(!strcmp(botw_title.modules[i], expected_modules[i]));
    assert(!botw_title.modules[3]);
    static const char *const expected_also[] = {
        "nndiagStartup", "_ZN2nn2oe10InitializeEv", "_ZN2nn3aoc10InitializeEv",
        "_ZN2nn3err26InitializeContextWriterApiEv", "_ZN2nn2fs24InitializeForApplicationEv",
        "_ZN2nn4pctl10InitializeEv", "_ZN2nn4diag34InitializeApplicationAbortObserverEv"
    };
    for (unsigned i = 0; i < 7; ++i) assert(!strcmp(botw_title.force_also[i], expected_also[i]));
    assert(!botw_title.force_also[7]);
    assert(!strcmp(hle_force_botw_time_startup.name, "nntimeStartup"));
    assert(hle_force_botw_time_startup.titles == DM_TM_BOTW);
    assert(hle_force_botw_time_startup.fn == (void *)botw_time_startup);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const char *mode = argv[1];
    if (!strcmp(mode, "descriptor")) { descriptor_contract(); return 0; }
    const size_t page = (size_t)sysconf(_SC_PAGESIZE);
    void *guard = mmap(NULL, page, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(guard != MAP_FAILED);
    uint64_t *owned = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(owned != MAP_FAILED);
    memset(owned, 0x6d, page);
    unsigned char *before = malloc(page);
    assert(before);
    memcpy(before, owned, page);
    assert(mprotect(owned, page, PROT_READ) == 0);
    if (!strcmp(mode, "failure-null")) return (int)dispatch(0x80, NULL);
    if (!strcmp(mode, "failure-guard")) return (int)dispatch(0x80, guard);
    if (!strcmp(mode, "failure-readonly")) return (int)dispatch(0x80, owned);
    if (!strcmp(mode, "horizon")) {
        for (int number = 0; number <= 0x7f; ++number) no_side_effect(number, owned);
    } else if (!strcmp(mode, "other-host-calls")) {
        for (int number = 0x81; number <= 0x8e; ++number) {
            no_side_effect(number, NULL);
            no_side_effect(number, guard);
            no_side_effect(number, owned);
        }
        static const int outside[] = {INT_MIN, -4096, -1, 0x8f, 0xffff, INT_MAX};
        for (unsigned i = 0; i < sizeof outside / sizeof *outside; ++i) {
            no_side_effect(outside[i], NULL);
            no_side_effect(outside[i], guard);
            no_side_effect(outside[i], owned);
        }
    } else return 5;
    assert(memcmp(owned, before, page) == 0);
    assert(munmap(guard, page) == 0 && munmap(owned, page) == 0);
    free(before);
    return 0;
}
