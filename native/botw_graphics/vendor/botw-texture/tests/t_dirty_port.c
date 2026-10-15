/* Port-specific real VM faults, checked failures and deterministic retirement.
 * Synthetic mappings only. This is a standalone process, not a production hook. */
#include <stdlib.h>
#include <stdio.h>
#include <assert.h>
#include <stdatomic.h>
#include <pthread.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/vm_map.h>
#include <dlfcn.h>
#include <signal.h>
#include <sys/ucontext.h>
#include <errno.h>
#include <string.h>
#include <unistd.h>
static _Atomic int fail_rw, fail_ro, fail_alloc, fail_query;
static _Atomic int block_rw, entered_rw, release_rw, signal_calls, registrations;
static _Thread_local int in_signal;
static _Atomic int query_race, query_phase;
static int port_mprotect(void *p, size_t n, int prot) {
    if (prot == (PROT_READ | PROT_WRITE) && atomic_exchange(&block_rw, 0)) {
        atomic_store(&entered_rw, 1);
        while (!atomic_load(&release_rw)) {}
    }
    if ((prot == (PROT_READ | PROT_WRITE) && atomic_load(&fail_rw)) ||
            (prot == PROT_READ && atomic_load(&fail_ro))) { errno = EACCES; return -1; }
    return mprotect(p, n, prot);
}
static void *port_calloc(size_t count, size_t size) {
    assert(!in_signal);
    int countdown = atomic_load(&fail_alloc);
    if (countdown > 0 && atomic_fetch_sub(&fail_alloc, 1) == 1) return NULL;
    return calloc(count, size);
}
static kern_return_t port_region(vm_map_read_t task, vm_address_t *address,
        vm_size_t *size, natural_t *depth, vm_region_recurse_info_t info,
        mach_msg_type_number_t *count) {
    if (atomic_load(&fail_query)) return KERN_FAILURE;
    int intercept = atomic_exchange(&query_race, 0);
    if (intercept) { atomic_store(&query_phase, 1); while (atomic_load(&query_phase) < 2) {} }
    kern_return_t rc = vm_region_recurse_64(task, address, size, depth, info, count);
    if (intercept) { atomic_store(&query_phase, 3); while (atomic_load(&query_phase) < 4) {} }
    return rc;
}
#define mprotect port_mprotect
#define calloc port_calloc
#define vm_region_recurse_64 port_region
#include "../nvn_dirty.c"
#undef mprotect
#undef calloc
#undef vm_region_recurse_64

static void port_signal(int number, siginfo_t *info, void *context) {
    (void)number;
    int calls = atomic_fetch_add(&signal_calls, 1) + 1;
    if (calls > 16) _exit(77); /* Bounded proof of a swallowed-fault retry loop. */
#if defined(__aarch64__)
    ucontext_t *u = context;
    unsigned esr = u->uc_mcontext->__es.__esr, ec = esr >> 26;
    int write = (ec == 0x24 || ec == 0x25) && (esr & 0x40);
#else
    ucontext_t *u = context;
    int write = !!(u->uc_mcontext->__es.__err & 2);
#endif
    in_signal = 1;
    int handled = nvn_dirty_handle_fault(info->si_addr, write, context);
    in_signal = 0;
    if (!handled) _exit(88); /* Existing/previous signal disposition sentinel. */
}
static int bridge(int (*fault)(void *, int, void *), int (*prepare)(void *, size_t, char *, size_t)) {
    assert(fault == nvn_dirty_handle_fault && prepare == nvn_dirty_prepare);
    atomic_fetch_add(&registrations, 1);
    struct sigaction a = {0}; a.sa_sigaction = port_signal; a.sa_flags = SA_SIGINFO;
    assert(!sigaction(SIGBUS, &a, NULL) && !sigaction(SIGSEGV, &a, NULL));
    return 0;
}
static int reject_bridge(int (*f)(void *, int, void *), int (*p)(void *, size_t, char *, size_t)) {
    (void)f; (void)p; return -1;
}
static uint8_t *alloc_pages(size_t n) {
    uint8_t *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(p != MAP_FAILED); return p;
}
static void wait_for(_Atomic int *flag, int n) {
    for (unsigned i = 0; i < 10000000; i++) { if (atomic_load(flag) >= n) return; if (!(i % 256)) sched_yield(); }
    assert(!"deterministic worker did not reach expected state");
}
static void *store_worker(void *p) { *(volatile uint8_t *)p = 0x5a; return NULL; }
static int prepare_result;
static void *prepare_worker(void *p) { prepare_result = nvn_dirty_prepare(p, 1, NULL, 0); return NULL; }
static _Atomic int removed;
static void *remove_worker(void *p) {
    assert(!nvn_dirty_remove_range_checked(p, NULL, 0)); atomic_store(&removed, 1); return NULL;
}
int main(int argc, char **argv) {
    assert(argc == 2); alarm(15); size_t pg = (size_t)getpagesize();
    if(!strcmp(argv[1],"submap-read")){void *shared=dlsym(RTLD_NEXT,"getpid");assert(shared);vm_prot_t protection=0;assert(actual_protection((uintptr_t)shared,4,&protection)&&(protection&VM_PROT_READ));puts("SHARED_SUBMAP_LEAF_PERMISSION_OK");return 0;}
    uint8_t *p = alloc_pages(pg * 3); char error[160] = {0};
    if (!strcmp(argv[1], "unconfigured")) {
        assert(nvn_dirty_add_range_checked(p, pg, error, sizeof error) == -1 && strstr(error, "not configured"));
        assert(!nvn_dirty_tracked(p, pg)); assert(!nvn_dirty_protect_checked(p, pg));
        uint64_t c, pages, ns; nvn_dirty_mp_stats(&c, &pages, &ns); assert(!c && !pages);
        assert(nvn_dirty_prepare(p, 1, error, sizeof error) == -1);
        assert(!nvn_dirty_prepare(NULL, 0, NULL, 0));
        assert(nvn_dirty_configure_registration(reject_bridge) == -1);
        assert(!nvn_dirty_configure_registration(bridge));
        assert(!nvn_dirty_configure_registration(bridge) && atomic_load(&registrations) == 1);
        assert(nvn_dirty_configure_registration(reject_bridge) == -1);
        assert(!nvn_dirty_add_range_checked(p, pg, error, sizeof error));
        assert(!nvn_dirty_remove_range_checked(p, error, sizeof error));
    } else {
        assert(!nvn_dirty_configure_registration(bridge));
        assert(!nvn_dirty_add_range_checked(p, pg * 2, error, sizeof error));
        if (!strcmp(argv[1], "fault")) {
            uint64_t stamp = nvn_dirty_now(); assert(nvn_dirty_protect_checked(p, pg));
            *(volatile uint8_t *)p = 33; assert(p[0] == 33 && atomic_load(&signal_calls) == 1);
            assert(nvn_dirty_since(p, pg, stamp));
            assert(!nvn_dirty_handle_fault(p, 0, NULL));
            assert(!nvn_dirty_handle_fault(p + pg * 2, 1, NULL));
        } else if (!strcmp(argv[1], "untracked-write")) {
            assert(!mprotect(p + pg * 2, pg, PROT_READ));
            *(volatile uint8_t *)(p + pg * 2) = 1; assert(!"untracked fault was swallowed");
        } else if (!strcmp(argv[1], "stale-two-writers")) {
            uint64_t stamp = nvn_dirty_now(); assert(nvn_dirty_protect_checked(p, pg));
            atomic_store(&block_rw, 1); pthread_t a, b;
            assert(!pthread_create(&a, NULL, store_worker, p)); wait_for(&entered_rw, 1);
            assert(!pthread_create(&b, NULL, store_worker, p + 1)); wait_for(&signal_calls, 2);
            atomic_store(&release_rw, 1); assert(!pthread_join(a, NULL) && !pthread_join(b, NULL));
            assert(p[0] == 0x5a && p[1] == 0x5a && atomic_load(&signal_calls) == 2);
            assert(nvn_dirty_since(p, pg, stamp));
        } else if (!strcmp(argv[1], "prepare-protect-aba")) {
            assert(nvn_dirty_protect_checked(p, pg)); *(volatile uint8_t *)p = 1;
            atomic_store(&query_race, 1); pthread_t writer;
            assert(!pthread_create(&writer, NULL, prepare_worker, p)); wait_for(&query_phase, 1);
            int protected = nvn_dirty_protect_checked(p, pg);
            atomic_store(&query_phase, 2); wait_for(&query_phase, 3);
            if (protected) assert(!nvn_dirty_prepare(p, 1, error, sizeof error));
            atomic_store(&query_phase, 4); assert(!pthread_join(writer, NULL));
            assert(!prepare_result); p[0] = 2;
        } else if (!strcmp(argv[1], "prepare-failure")) {
            uint64_t stamp = nvn_dirty_now(); assert(nvn_dirty_protect_checked(p, pg));
            atomic_store(&fail_rw, 1); assert(nvn_dirty_prepare(p, 1, error, sizeof error) == -1 && strstr(error, "cannot prepare"));
            assert(!nvn_dirty_since(p, pg, stamp));
            atomic_store(&fail_rw, 0); assert(!nvn_dirty_prepare(p, pg + 1, error, sizeof error));
            assert(nvn_dirty_since(p, pg, stamp)); p[0] = 11;
            atomic_store(&fail_ro, 1); stamp = nvn_dirty_now();
            assert(!nvn_dirty_protect_checked(p, pg) && nvn_dirty_now() > stamp);
            atomic_store(&fail_ro, 0); assert(nvn_dirty_protect_checked(p, pg));
        } else if (!strcmp(argv[1], "retirement-failure")) {
            assert(nvn_dirty_protect_checked(p, pg)); atomic_store(&fail_rw, 1);
            assert(nvn_dirty_remove_range_checked(p, error, sizeof error) == -1 && strstr(error, "still pinned"));
            assert(!nvn_dirty_tracked(p, pg)); int live, peak, cap; nvn_dirty_stat(&live, &peak, &cap); assert(live == 1);
            atomic_store(&fail_rw, 0); assert(!nvn_dirty_remove_range_checked(p, error, sizeof error));
            nvn_dirty_stat(&live, &peak, &cap); assert(!live); p[0] = 12;
        } else if (!strcmp(argv[1], "retirement-drains")) {
            assert(nvn_dirty_protect_checked(p, pg)); atomic_store(&block_rw, 1);
            pthread_t a, b; assert(!pthread_create(&a, NULL, store_worker, p)); wait_for(&entered_rw, 1);
            assert(!pthread_create(&b, NULL, remove_worker, p));
            usleep(20000); assert(!atomic_load(&removed)); atomic_store(&release_rw, 1);
            assert(!pthread_join(a, NULL) && !pthread_join(b, NULL)); assert(atomic_load(&removed));
            assert(!nvn_dirty_tracked(p, pg));
        } else if (!strncmp(argv[1], "allocation-failure", 18)) {
            atomic_store(&fail_alloc, argv[1][18] ? atoi(argv[1] + 18) : 1);
            assert(nvn_dirty_add_range_checked(p + pg * 2, pg, error, sizeof error) == -1 && strstr(error, "allocation failed"));
            assert(!nvn_dirty_tracked(p + pg * 2, pg));
        } else if (!strcmp(argv[1], "shared-reregister")) {
            assert(!nvn_dirty_add_range_checked(p + pg, pg, error, sizeof error));
            uint64_t stamp = nvn_dirty_now(); assert(nvn_dirty_protect_checked(p, pg * 2));
            assert(!nvn_dirty_remove_range_checked(p, error, sizeof error));
            assert(!nvn_dirty_since(p + pg, pg, stamp));
            *(volatile uint8_t *)(p + pg) = 0x71; assert(nvn_dirty_since(p + pg, pg, stamp));
            assert(!nvn_dirty_remove_range_checked(p + pg, error, sizeof error));
            assert(!nvn_dirty_add_range_checked(p, pg, error, sizeof error));
            assert(nvn_dirty_since(p, pg, stamp));
        } else if (!strcmp(argv[1], "invalid")) {
            assert(nvn_dirty_prepare(NULL, 1, error, sizeof error) == -1);
            assert(nvn_dirty_prepare((void *)(UINTPTR_MAX - 1), 4, error, sizeof error) == -1);
            assert(nvn_dirty_add_range_checked(p, 0, error, sizeof error) == -1);
            assert(nvn_dirty_add_range_checked((void *)(UINTPTR_MAX - 1), 4, error, sizeof error) == -1);
            assert(!mprotect(p + pg * 2, pg, PROT_NONE));
            assert(nvn_dirty_add_range_checked(p + pg * 2, pg, error, sizeof error) == -1 && strstr(error, "not readable"));
            assert(!mprotect(p + pg * 2, pg, PROT_READ | PROT_WRITE));
        } else {
            assert(nvn_dirty_protect_checked(p, pg));
            if (strncmp(argv[1], "owned-", 6) && strcmp(argv[1], "read-fault")) {
                *(volatile uint8_t *)p = 1; atomic_store(&signal_calls, 0); /* nonzero generation, PG_NONE */
            }
            if (!strcmp(argv[1], "query-failure")) atomic_store(&fail_query, 1);
            else assert(!mprotect(p, pg, !strcmp(argv[1], "stale-read") ? PROT_READ : PROT_NONE));
            if (!strcmp(argv[1], "query-failure")) { /* Real stale signal is not required for a failing query. */
                assert(!nvn_dirty_handle_fault(p, 1, NULL)); atomic_store(&fail_query, 0);
            } else if (!strcmp(argv[1], "read-fault")) { volatile uint8_t v = *(volatile uint8_t *)p; (void)v; assert(0); }
            else { *(volatile uint8_t *)p = 2; assert(!"real fault was swallowed"); }
        }
        assert(!nvn_dirty_remove_range_checked(p, error, sizeof error));
    }
    assert(!munmap(p, pg * 3)); printf("dirty port %s: PASS\n", argv[1]); return 0;
}
