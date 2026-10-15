// Coherent dirty tracking for synthetic registrations sharing host pages. Direct
// stores fault through the production crash.c hook; all storage is owned here.
#include "../nvn_dirty.c"
#include "test_dirty_port.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>



static _Atomic int checks, failures;
#define CHECK(c) do { atomic_fetch_add(&checks, 1); if (!(c)) { atomic_fetch_add(&failures, 1); fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); } } while (0)

static size_t pg;
static uint8_t *P[5];
static uint64_t faults(void) { return atomic_load(&g_faults); }
static uint64_t preps(void) { return atomic_load(&g_preps); }
// Same-value store: only write tracking, not content, can reveal it. Relaxed
// atomics keep the racing synthetic guest stores well defined for TSan while still
// compiling to an ordinary faulting byte store.
static void store(uint8_t *a) { __atomic_store_n(a, __atomic_load_n(a, __ATOMIC_RELAXED), __ATOMIC_RELAXED); }
// A store must not fault; it proves the host page is writable.
static int writable(uint8_t *a) { uint64_t f = faults(); store(a); return faults() == f; }
// A store must fault exactly once; it proves the host page was protected.
static int protected_store(uint8_t *a) { uint64_t f = faults(); store(a); return faults() == f + 1; }
static page_t *record(const void *mem, uintptr_t a) {
    unsigned bank = read_enter();
    page_t *pg_ = NULL;
    int n = atomic_load(&g_nr);
    for (int i = 0; i < n; i++) {
        range_t *r = atomic_load(&g_r[i]);
        if (r && r->mem == mem && a >= r->base && a < r->end) { pg_ = r->page[page_index(r, a)]; break; }
    }
    read_leave(bank);
    return pg_;
}
static void expect_idle(void) {
    int live, used; uint64_t adds;
    nvn_dirty_slot_stats(&live, &used, &adds);
    CHECK(live == 0);
    pthread_mutex_lock(&g_slot_mu);
    CHECK(g_pages_live == 0 && g_pages_shared == 0);
    pthread_mutex_unlock(&g_slot_mu);
    for (int i = 0; i < 4; i++) CHECK(writable(P[i]));
}

static void test_unique(void) {
    uint8_t *p = P[0];
    CHECK(!nvn_dirty_unambiguous(p, pg));
    nvn_dirty_add_range(p, pg);
    CHECK(nvn_dirty_unambiguous(p, pg));
    CHECK(nvn_dirty_unambiguous(p + 1, pg - 1));
    CHECK(!nvn_dirty_unambiguous(p, 0));
    CHECK(!nvn_dirty_unambiguous(NULL, pg));
    CHECK(!nvn_dirty_unambiguous(p + pg - 1, 2));
    CHECK(!nvn_dirty_unambiguous(p + 1, SIZE_MAX));
    // Touching host-page boundaries do not share records.
    nvn_dirty_add_range(p + pg, pg);
    CHECK(nvn_dirty_unambiguous(p, pg) && nvn_dirty_unambiguous(p + pg, pg));
    CHECK(record(p, (uintptr_t)p) != record(p + pg, (uintptr_t)p + pg));
    // A span straddling two registrations has no single covering owner.
    CHECK(!nvn_dirty_unambiguous(p + pg - 1, 2) && !nvn_dirty_tracked(p + pg - 1, 2));
    // Retirement must disqualify a record before it is unpublished.
    unsigned bank = read_enter();
    range_t *r = find_range((uintptr_t)p, 1);
    CHECK(r != NULL);
    atomic_store(&r->retiring, 1);
    CHECK(!nvn_dirty_unambiguous(p, pg));
    read_leave(bank);
    nvn_dirty_remove_range(p);
    CHECK(!nvn_dirty_unambiguous(p, pg));
    CHECK(nvn_dirty_unambiguous(p + pg, pg));
    nvn_dirty_remove_range(p + pg);
    expect_idle();
}

// A owns pages 0-1, B owns pages 1-2; page 1 is shared.
static void test_sequential(int remove_first) {
    uint8_t *a = P[0], *b = P[1];
    nvn_dirty_add_range(a, 2 * pg);
    uint64_t sa = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(a, 2 * pg));
    nvn_dirty_add_range(b, 2 * pg);
    CHECK(record(a, (uintptr_t)P[1]) == record(b, (uintptr_t)P[1]));
    CHECK(record(a, (uintptr_t)P[0]) != record(b, (uintptr_t)P[2]));
    CHECK(nvn_dirty_unambiguous(a, 2 * pg) && nvn_dirty_unambiguous(b, 2 * pg));
    uint64_t sb = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(b, 2 * pg));
    CHECK(!nvn_dirty_since(a, 2 * pg, sa) && !nvn_dirty_since(b, 2 * pg, sb));
    uint8_t *gone = remove_first ? a : b, *kept = remove_first ? b : a;
    uint8_t *gone_only = remove_first ? P[0] : P[2], *kept_only = remove_first ? P[2] : P[0];
    uint64_t sk = remove_first ? sb : sa;
    nvn_dirty_remove_range(gone);
    // The survivor still owns the shared page: it remains protected and clean.
    CHECK(nvn_dirty_unambiguous(kept, 2 * pg));
    CHECK(!nvn_dirty_since(kept, 2 * pg, sk));
    // The retired owner's solely owned page is writable and untracked.
    CHECK(writable(gone_only));
    CHECK(!nvn_dirty_tracked(gone_only, pg));
    CHECK(!nvn_dirty_since(kept_only, pg, sk));
    // One same-value store on the shared page faults and dirties the survivor.
    CHECK(protected_store(P[1]));
    CHECK(nvn_dirty_since(kept, 2 * pg, sk) && nvn_dirty_since(P[1], pg, sk));
    CHECK(!nvn_dirty_since(kept_only, pg, sk));
    // Surviving-owner re-protection establishes a fresh clean stamp.
    uint64_t again = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(kept, 2 * pg));
    CHECK(!nvn_dirty_since(kept, 2 * pg, again));
    CHECK(protected_store(P[1]));
    CHECK(nvn_dirty_since(kept, 2 * pg, again));
    CHECK(protected_store(kept_only));
    nvn_dirty_remove_range(kept);
    expect_idle();
}

static void test_cross_observation(void) {
    uint8_t *a = P[0], *b = P[1];
    nvn_dirty_add_range(a, 2 * pg);
    nvn_dirty_add_range(b, 2 * pg);
    uint64_t s = nvn_dirty_now();
    // Protect only through A. B's full span is covered only by B.
    CHECK(nvn_dirty_protect_checked(a, 2 * pg));
    CHECK(!nvn_dirty_since(a, 2 * pg, s) && !nvn_dirty_since(b, 2 * pg, s));
    CHECK(protected_store(P[1]));
    CHECK(nvn_dirty_since(a, 2 * pg, s) && nvn_dirty_since(b, 2 * pg, s));
    CHECK(!nvn_dirty_since(P[0], pg, s) && !nvn_dirty_since(P[2], pg, s));
    // Protection through B also protects A's view of the shared page.
    s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(b, 2 * pg));
    CHECK(!nvn_dirty_since(a + pg, pg, s));
    // A host pre-write unprotects the shared page once for both owners.
    uint64_t pr = preps();
    dm_guest_write_prepare(P[1] + 7, 1);
    CHECK(preps() == pr + 1);
    CHECK(writable(P[1]));
    CHECK(nvn_dirty_since(a, 2 * pg, s) && nvn_dirty_since(b, 2 * pg, s));
    CHECK(!nvn_dirty_since(P[2], pg, s));
    nvn_dirty_remove_range(a);
    nvn_dirty_remove_range(b);
    expect_idle();
}

static void test_covering_query(void) {
    // Short A occupies the earlier slot; B covers spans A does not.
    uint8_t *a = P[1], *b = P[0];
    nvn_dirty_add_range(a, pg);
    uint64_t before_b = nvn_dirty_now();
    nvn_dirty_add_range(b, 3 * pg);
    CHECK(find_range((uintptr_t)P[1], 0)->mem == a);
    CHECK(nvn_dirty_tracked(P[1], 2 * pg) && nvn_dirty_unambiguous(P[1], 2 * pg));
    uint64_t s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(P[1], 2 * pg));
    CHECK(!nvn_dirty_since(P[1], 2 * pg, s));
    CHECK(protected_store(P[2]));
    CHECK(nvn_dirty_since(P[1], 2 * pg, s));
    // A stamp older than B is still vouched for by A on A's span, but not on
    // spans only the newer registration covers.
    CHECK(nvn_dirty_protect_checked(P[1], pg));
    CHECK(!nvn_dirty_since(P[1], pg, before_b));
    CHECK(nvn_dirty_since(P[1], 2 * pg, before_b));
    CHECK(protected_store(P[1]));
    CHECK(nvn_dirty_since(P[1], pg, before_b));
    nvn_dirty_remove_range(b);
    CHECK(!nvn_dirty_tracked(P[1], 2 * pg) && nvn_dirty_tracked(P[1], pg));
    CHECK(writable(P[0]) && writable(P[2]));
    nvn_dirty_remove_range(a);
    expect_idle();
}

static void test_partial_shared(void) {
    // Disjoint byte ranges share one rounded host page.
    uint8_t *a = P[0] + 16, *b = P[0] + pg / 2;
    nvn_dirty_add_range(a, 16);
    nvn_dirty_add_range(b, 16);
    CHECK(record(a, (uintptr_t)P[0]) == record(b, (uintptr_t)P[0]));
    CHECK(nvn_dirty_unambiguous(a, 16) && nvn_dirty_unambiguous(b, 16));
    uint64_t s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(a, 16));
    CHECK(!nvn_dirty_since(b, 16, s));
    // Page granularity: a store to B's bytes is a coherent false positive for A.
    CHECK(protected_store(b));
    CHECK(nvn_dirty_since(a, 16, s) && nvn_dirty_since(b, 16, s));
    s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(b, 16));
    nvn_dirty_remove_range(a);
    CHECK(!nvn_dirty_since(b, 16, s));
    CHECK(protected_store(a));
    CHECK(nvn_dirty_since(b, 16, s));
    nvn_dirty_remove_range(b);
    expect_idle();

    // Exact duplicates share every record; removing one keeps protection.
    nvn_dirty_add_range(P[0], pg);
    nvn_dirty_add_range(P[0], pg);
    s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(P[0], pg));
    nvn_dirty_remove_range(P[0]);
    CHECK(nvn_dirty_unambiguous(P[0], pg) && !nvn_dirty_since(P[0], pg, s));
    CHECK(protected_store(P[0]));
    CHECK(nvn_dirty_since(P[0], pg, s));
    nvn_dirty_remove_range(P[0]);
    expect_idle();
}

static void test_isolation(void) {
    uint8_t *a = P[0], *b = P[1], *c = P[3];
    nvn_dirty_add_range(a, 2 * pg);
    nvn_dirty_add_range(b, 2 * pg);
    nvn_dirty_add_range(c, pg);
    uint64_t s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(a, 2 * pg) && nvn_dirty_protect_checked(b, 2 * pg) &&
          nvn_dirty_protect_checked(c, pg));
    CHECK(protected_store(P[0]));
    CHECK(nvn_dirty_since(a, 2 * pg, s));
    CHECK(!nvn_dirty_since(b, 2 * pg, s) && !nvn_dirty_since(c, pg, s));
    CHECK(protected_store(P[2]));
    CHECK(nvn_dirty_since(b, 2 * pg, s) && !nvn_dirty_since(P[1], pg, s) && !nvn_dirty_since(c, pg, s));
    CHECK(protected_store(P[1]));
    CHECK(nvn_dirty_since(P[1], pg, s) && !nvn_dirty_since(c, pg, s));
    // Retiring the unrelated range leaves the shared records untouched.
    s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(a, 2 * pg));
    nvn_dirty_remove_range(c);
    CHECK(writable(P[3]));
    CHECK(!nvn_dirty_since(a, 2 * pg, s));
    CHECK(protected_store(P[1]));
    nvn_dirty_remove_range(b);
    nvn_dirty_remove_range(a);
    expect_idle();
}

static void test_record_lifetime(void) {
    // A shared record outlives the registration whose chunk created it.
    nvn_dirty_add_range(P[0], 2 * pg);
    nvn_dirty_add_range(P[1], 2 * pg);
    page_t *shared = record(P[0], (uintptr_t)P[1]);
    pthread_mutex_lock(&g_slot_mu);
    CHECK(g_pages_live == 3 && g_pages_shared == 1 && shared->refs == 2);
    pthread_mutex_unlock(&g_slot_mu);
    nvn_dirty_remove_range(P[0]);
    pthread_mutex_lock(&g_slot_mu);
    CHECK(g_pages_live == 2 && g_pages_shared == 0 && shared->refs == 1);
    pthread_mutex_unlock(&g_slot_mu);
    CHECK(record(P[1], (uintptr_t)P[1]) == shared);
    // A newcomer adopts the surviving record rather than creating a second one.
    nvn_dirty_add_range(P[0], 2 * pg);
    CHECK(record(P[0], (uintptr_t)P[1]) == shared && record(P[0], (uintptr_t)P[0]) != shared);
    nvn_dirty_remove_range(P[1]);
    nvn_dirty_remove_range(P[0]);
    expect_idle();
}

// Concurrency: A (pages 0-1) is permanent; B (pages 1-2) is repeatedly registered,
// protected and retired while other threads protect A and write the shared page.
enum { STRESS_ROUNDS = 3000, STRESS_WRITES = 20000 };
static _Atomic int stress_stop;
static _Atomic uint64_t stress_writes, stress_certified;
static void *stress_retirer(void *arg) {
    (void)arg;
    for (int i = 0; i < STRESS_ROUNDS || atomic_load(&stress_writes) < STRESS_WRITES; i++) {
        nvn_dirty_add_range(P[1], 2 * pg);
        (void)nvn_dirty_protect_checked(P[1], 2 * pg);
        if (i & 1) sched_yield();
        nvn_dirty_remove_range(P[1]);
    }
    atomic_store(&stress_stop, 1);
    return NULL;
}
static void *stress_protector(void *arg) {
    (void)arg;
    while (!atomic_load(&stress_stop)) {
        (void)nvn_dirty_protect_checked(P[0], 2 * pg);
        (void)nvn_dirty_since(P[1], 2 * pg, nvn_dirty_now());
    }
    return NULL;
}
static void *stress_writer(void *arg) {
    (void)arg;
    while (!atomic_load(&stress_stop)) {
        uint64_t s = nvn_dirty_now();
        int ok = nvn_dirty_protect_checked(P[1], pg);
        // A covers page 1 throughout, so direct stores always fault into a live owner.
        store(P[1]);
        atomic_fetch_add(&stress_writes, 1);
        if (ok) { atomic_fetch_add(&stress_certified, 1); CHECK(nvn_dirty_since(P[1], pg, s)); }
        s = nvn_dirty_now();
        ok = nvn_dirty_protect_checked(P[0], 2 * pg);
        dm_guest_write_prepare(P[1] + 3, 1);
        store(P[1] + 3);
        if (ok) CHECK(nvn_dirty_since(P[0], 2 * pg, s));
        // Page 2 may belong to B or to nobody; host pre-writes are safe either way.
        dm_guest_write_prepare(P[2], pg);
    }
    return NULL;
}
static void test_concurrency(void) {
    nvn_dirty_add_range(P[0], 2 * pg);
    pthread_t t[4];
    pthread_create(&t[0], NULL, stress_retirer, NULL);
    pthread_create(&t[1], NULL, stress_protector, NULL);
    pthread_create(&t[2], NULL, stress_writer, NULL);
    pthread_create(&t[3], NULL, stress_writer, NULL);
    for (int i = 0; i < 4; i++) pthread_join(t[i], NULL);
    CHECK(atomic_load(&stress_certified) > 0);
    // A survived every B retirement: its shared page must still be tracked.
    uint64_t s = nvn_dirty_now();
    CHECK(nvn_dirty_protect_checked(P[0], 2 * pg));
    CHECK(!nvn_dirty_since(P[0], 2 * pg, s));
    CHECK(protected_store(P[1]) && protected_store(P[0]));
    CHECK(writable(P[2]));
    nvn_dirty_remove_range(P[0]);
    expect_idle();
}

int main(void) {
    alarm(45);
    test_dirty_install();
    pg = (size_t)getpagesize();
    uint8_t *m = mmap(NULL, pg * 5, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(m != MAP_FAILED);
    for (int i = 0; i < 5; i++) P[i] = m + i * pg;
    for (size_t i = 0; i < pg * 5; i++) m[i] = (uint8_t)(i * 7);
    test_unique();
    test_sequential(0);
    test_sequential(1);
    test_cross_observation();
    test_covering_query();
    test_partial_shared();
    test_isolation();
    test_record_lifetime();
    test_concurrency();
    for (size_t i = 0; i < pg * 5; i++) CHECK(m[i] == (uint8_t)(i * 7));
    CHECK(atomic_load(&g_busy_timeouts) == 0);
    CHECK(munmap(m, pg * 5) == 0);
    printf("t_dirty_unambiguous: %d checks, %d failed (host page %zu bytes, %llu stress writes, %llu certified)\n",
           atomic_load(&checks), atomic_load(&failures), pg,
           (unsigned long long)atomic_load(&stress_writes), (unsigned long long)atomic_load(&stress_certified));
    return atomic_load(&failures) != 0;
}
