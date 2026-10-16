// Physical-memory dirty tracking. Every host page covered by a live registration has
// exactly one canonical state/generation record shared by all of its owners.
//
// Invariants:
// 1. Canonical pages: while any published or retiring range covers a host page,
//    every such range's immutable page[] slot points to the same page_t. Ranges are
//    created and retired only under g_slot_mu, so a new range adopts the record of
//    any live overlapping peer and allocates records only for uncovered pages.
// 2. Ownership: page_t.refs counts the ranges referencing it and is touched only
//    under g_slot_mu. Signal readers never read refs, chunks or the slot mutex.
// 3. Protection: PG_PROT means the host page was made read-only, PG_NONE means it
//    is writable, and PG_BUSY means one thread owns a transition. Because there is
//    one state per physical page, no owner can see PG_PROT after another owner made
//    the page writable; every unprotection publishes a new canonical generation.
// 4. Retirement: protect/check readers are excluded and drained first. Only pages
//    whose sole owner is the retiring range are restored writable; shared pages
//    keep their state for the surviving owners. The range is then unpublished and
//    readers are drained again before page records, chunks or arrays are freed.
// Signal readers pin immutable registration metadata through the epoch banks.
#include "nvn_dirty.h"
#include "../../host/dm.h"
#include <stdatomic.h>
#include <pthread.h>
#include <sys/mman.h>
#include <unistd.h>
#include <mach/mach_time.h>
#include <sched.h>

enum { PG_NONE = 0, PG_PROT = 1, PG_BUSY = 2, BUSY_WAIT_MS = 2000 };
#define MAX_RANGE 4096
typedef struct page_chunk page_chunk;
typedef struct {
    _Atomic uint64_t gen;
    _Atomic uint8_t state;
    uint32_t refs;          // Owning ranges; g_slot_mu only.
    page_chunk *chunk;      // Allocation holding this record; g_slot_mu only.
} page_t;
// One allocation per registration for the records it created. A chunk is freed
// once none of its records has an owner, so a shared record outlives its creator.
struct page_chunk { size_t live; page_t page[]; };
typedef struct {
    uintptr_t base, end;
    const void *mem;
    uint64_t registered;
    page_t **page;          // Immutable after publication; canonical records.
    _Atomic int retiring;
} range_t;
static _Atomic(range_t *) g_r[MAX_RANGE];
static _Atomic int g_nr;
static _Atomic unsigned g_readers[2];
static _Atomic uint64_t g_epoch;
static uintptr_t g_pg, g_shift;
static mach_timebase_info_data_t g_tb;
static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t g_slot_mu = PTHREAD_MUTEX_INITIALIZER;
static _Atomic uint64_t g_now = 1, g_faults, g_preps, g_adds_total;
static _Atomic uint64_t g_mp_calls, g_mp_pages, g_mp_ticks, g_busy_timeouts;
static _Atomic int g_live, g_live_peak, g_overflow_logged;
static size_t g_pages_live, g_pages_shared;   // Canonical records and extra owner references; g_slot_mu only.
_Static_assert(ATOMIC_CHAR_LOCK_FREE == 2 && ATOMIC_POINTER_LOCK_FREE == 2 && ATOMIC_INT_LOCK_FREE == 2 &&
               ATOMIC_LONG_LOCK_FREE == 2 && ATOMIC_LLONG_LOCK_FREE == 2,
               "dirty signal readers require lock-free atomics");

// Readers validate their epoch before loading any slot. A grace period flips the
// epoch and drains only its old bank, so new unrelated readers cannot starve it.
// Removers serialize through g_slot_mu; no signal waits for a writer or a gate.
// Sequential consistency closes the load-before-pin race, including a reader
// paused between loading an epoch and incrementing that epoch's counter.
static unsigned read_enter(void) {
    for (;;) {
        uint64_t e = atomic_load(&g_epoch);
        unsigned bank = (unsigned)(e & 1);
        atomic_fetch_add(&g_readers[bank], 1);
        if (atomic_load(&g_epoch) == e) return bank;
        atomic_fetch_sub(&g_readers[bank], 1);
    }
}
static void read_leave(unsigned bank) { atomic_fetch_sub(&g_readers[bank], 1); }
static void drain_readers(void) {
    unsigned bank = (unsigned)(atomic_fetch_add(&g_epoch, 1) & 1);
    while (atomic_load(&g_readers[bank])) sched_yield();
}
static int covers(const range_t *r, uintptr_t a, size_t n) {
    return r && n && a >= r->base && a < r->end && n <= r->end - a;
}
// Any owner of a host page resolves to its canonical record, so the fault and
// pre-write paths may use the first range containing the address.
static range_t *find_range(uintptr_t a, int allow_retiring) {
    int n = atomic_load(&g_nr);
    for (int i = 0; i < n; i++) {
        range_t *r = atomic_load(&g_r[i]);
        if (r && a >= r->base && a < r->end &&
            (allow_retiring || !atomic_load(&r->retiring))) return r;
    }
    return NULL;
}
// Full-span queries need a range covering the whole span; a shorter overlapping
// range in an earlier slot must not hide a covering one. Only ranges registered
// no later than max_registered qualify.
static range_t *find_cover(uintptr_t a, size_t len, uint64_t max_registered) {
    int n = atomic_load(&g_nr);
    for (int i = 0; i < n; i++) {
        range_t *r = atomic_load(&g_r[i]);
        if (covers(r, a, len) && !atomic_load(&r->retiring) && r->registered <= max_registered) return r;
    }
    return NULL;
}
static uintptr_t page_index(const range_t *r, uintptr_t a) { return (a - r->base) >> g_shift; }
static int mp(uintptr_t a, size_t len, int prot) {
    uint64_t t = mach_absolute_time();
    int ok = mprotect((void *)a, len, prot) == 0;
    atomic_fetch_add(&g_mp_ticks, mach_absolute_time() - t);
    atomic_fetch_add(&g_mp_calls, 1);
    atomic_fetch_add(&g_mp_pages, len >> g_shift);
    return ok;
}
static int busy_wait(page_t *pg) {
    uint64_t t0 = mach_absolute_time();
    for (unsigned n = 1; atomic_load(&pg->state) == PG_BUSY; n++) {
        if (n & 0x3ff) continue;
        uint64_t dt = mach_absolute_time() - t0;
        uint64_t ns = dt / g_tb.denom * g_tb.numer + dt % g_tb.denom * g_tb.numer / g_tb.denom;
        if (ns > (uint64_t)BUSY_WAIT_MS * 1000000ull) {
            // Formatting and stdio are forbidden in the signal path.
            atomic_fetch_add(&g_busy_timeouts, 1);
            return 0;
        }
        sched_yield();
    }
    return 1;
}
// Caller owns PG_BUSY. Never publish writable state if mprotect failed.
static int unprotect_page(page_t *pg, uintptr_t addr) {
    if (!mp(addr, g_pg, PROT_READ | PROT_WRITE)) {
        atomic_store(&pg->state, PG_PROT);
        return 0;
    }
    atomic_store(&pg->gen, atomic_fetch_add(&g_now, 1) + 1);
    atomic_store(&pg->state, PG_NONE);
    return 1;
}
static int unprotect_one(range_t *r, uintptr_t p) {
    page_t *pg = r->page[p];
    for (;;) {
        uint8_t st = PG_PROT;
        if (atomic_compare_exchange_strong(&pg->state, &st, PG_BUSY)) {
            if (!unprotect_page(pg, r->base + (p << g_shift))) return 0;
            atomic_fetch_add(&g_preps, 1);
            return 1;
        }
        if (st != PG_BUSY) return 1;
        if (!busy_wait(pg)) return 0;
    }
}
static int unprotect_range(range_t *r, uintptr_t a, uintptr_t stop) {
    int ok = 1;
    for (uintptr_t p = page_index(r, a), q = page_index(r, stop - 1); p <= q; p++)
        if (!unprotect_one(r, p)) ok = 0;
    return ok;
}
static int on_fault(void *addr, int is_write, void *uc) {
    (void)uc;
    if (!is_write) return 0;
    unsigned bank = read_enter();
    range_t *r = find_range((uintptr_t)addr, 1);
    int handled = 0;
    if (r) {
        uintptr_t p = page_index(r, (uintptr_t)addr);
        page_t *pg = r->page[p];
        uint8_t st = PG_PROT;
        if (atomic_compare_exchange_strong(&pg->state, &st, PG_BUSY)) {
            handled = unprotect_page(pg, r->base + (p << g_shift));
            if (handled) atomic_fetch_add(&g_faults, 1);
        } else if (st == PG_BUSY) handled = busy_wait(pg);
        else handled = atomic_load(&pg->gen) != 0;
    }
    read_leave(bank);
    return handled;
}
static void prep_write(void *addr, size_t len) {
    uintptr_t a = (uintptr_t)addr;
    if (!len || len > UINTPTR_MAX - a) return;
    uintptr_t e = a + len;
    unsigned bank = read_enter();
    while (a < e) {
        range_t *r = find_range(a, 1);
        if (!r) {
            uintptr_t step = g_pg - (a & (g_pg - 1));
            if (step >= e - a) break;
            a += step;
            continue;
        }
        uintptr_t stop = e < r->end ? e : r->end;
        unprotect_range(r, a, stop);
        a = stop;
    }
    read_leave(bank);
}
static void dirty_init(void) {
    g_pg = (uintptr_t)getpagesize();
    g_shift = __builtin_ctzl(g_pg);
    mach_timebase_info(&g_tb);
    dm_fault_hook_register(on_fault);
    dm_guest_write_prep_register(prep_write);
}

// g_slot_mu held. Adopt canonical records from live overlapping peers, then
// allocate fresh writable records for pages no live range covers.
static void attach_pages(range_t *r, int n) {
    size_t np = (r->end - r->base) >> g_shift, fresh = 0;
    for (int i = 0; i < n; i++) {
        range_t *peer = atomic_load(&g_r[i]);
        if (!peer || r->base >= peer->end || peer->base >= r->end) continue;
        uintptr_t lo = r->base > peer->base ? r->base : peer->base;
        uintptr_t hi = r->end < peer->end ? r->end : peer->end;
        for (uintptr_t a = lo; a < hi; a += g_pg) {
            page_t *pg = peer->page[page_index(peer, a)], **slot = &r->page[page_index(r, a)];
            if (*slot && *slot != pg) die("dirty-page tracking found two records for host page %#lx", (unsigned long)a);
            *slot = pg;
        }
    }
    for (size_t i = 0; i < np; i++) if (!r->page[i]) fresh++;
    page_chunk *c = NULL;
    if (fresh) {
        c = calloc(1, sizeof *c + fresh * sizeof c->page[0]);
        if (!c) die("dirty-page record allocation failed (%zu pages)", fresh);
        c->live = fresh;
    }
    for (size_t i = 0, k = 0; i < np; i++) {
        if (r->page[i]) { r->page[i]->refs++; g_pages_shared++; continue; }
        page_t *pg = &c->page[k++];
        pg->chunk = c; pg->refs = 1;
        r->page[i] = pg;
    }
    g_pages_live += fresh;
}
// g_slot_mu held and r unpublished with readers drained.
static void detach_pages(range_t *r) {
    size_t np = (r->end - r->base) >> g_shift;
    for (size_t i = 0; i < np; i++) {
        page_t *pg = r->page[i];
        if (--pg->refs) { g_pages_shared--; continue; }
        g_pages_live--;
        page_chunk *c = pg->chunk;
        if (!--c->live) free(c);
    }
    free(r->page);
}

void nvn_dirty_add_range(const void *mem, size_t size) {
    pthread_once(&g_once, dirty_init);
    uintptr_t a = (uintptr_t)mem;
    if (!mem || !size || size > UINTPTR_MAX - a || a + size > UINTPTR_MAX - (g_pg - 1)) return;
    atomic_fetch_add(&g_adds_total, 1);
    pthread_mutex_lock(&g_slot_mu);
    int n = atomic_load(&g_nr), slot = -1;
    for (int i = 0; i < n; i++) if (!atomic_load(&g_r[i])) { slot = i; break; }
    if (slot < 0) {
        if (n >= MAX_RANGE) {
            if (!atomic_exchange(&g_overflow_logged, 1)) LOG("[ERROR] dirty-page tracking ranges full (%d), pool %p not tracked", MAX_RANGE, mem);
            pthread_mutex_unlock(&g_slot_mu);
            return;
        }
        slot = n;
    }
    range_t *r = calloc(1, sizeof *r);
    if (!r) die("dirty-page range allocation failed");
    r->base = a & ~(g_pg - 1);
    r->end = (a + size + g_pg - 1) & ~(g_pg - 1);
    r->mem = mem;
    size_t np = (r->end - r->base) >> g_shift;
    r->page = calloc(np, sizeof *r->page);
    if (!r->page) die("dirty-page table allocation failed (%zu pages)", np);
    attach_pages(r, n);
    // Re-registration must invalidate stamps belonging to the previous storage.
    r->registered = atomic_fetch_add(&g_now, 1) + 1;
    atomic_store(&g_r[slot], r);
    if (slot == n) atomic_store(&g_nr, n + 1);
    int lv = atomic_fetch_add(&g_live, 1) + 1, pk = atomic_load(&g_live_peak);
    while (lv > pk && !atomic_compare_exchange_weak(&g_live_peak, &pk, lv)) {}
    pthread_mutex_unlock(&g_slot_mu);
}

void nvn_dirty_remove_range(const void *mem) {
    pthread_mutex_lock(&g_slot_mu);
    int n = atomic_load(&g_nr);
    for (int i = 0; i < n; i++) {
        range_t *r = atomic_load(&g_r[i]);
        if (!r || mem != r->mem) continue;
        // Stop new protect/check readers first. Faults and pre-write preparation
        // remain admitted until every solely owned page has become writable.
        atomic_store(&r->retiring, 1);
        drain_readers();
        // Ownership cannot change while g_slot_mu is held, and no other range can
        // reach a solely owned record, so nothing can re-protect it from here on.
        size_t np = (r->end - r->base) >> g_shift;
        for (uintptr_t p = 0; p < np; p++)
            if (r->page[p]->refs == 1 && !unprotect_one(r, p)) die("cannot restore retiring dirty range %p to writable", mem);
        atomic_store(&g_r[i], NULL);
        drain_readers();
        detach_pages(r);
        atomic_fetch_add(&g_now, 1);
        atomic_fetch_sub(&g_live, 1);
        free(r);
        break;
    }
    pthread_mutex_unlock(&g_slot_mu);
}
int nvn_dirty_tracked(const void *addr, size_t len) {
    unsigned bank = read_enter();
    int ok = find_cover((uintptr_t)addr, len, UINT64_MAX) != NULL;
    read_leave(bank);
    return ok;
}

int nvn_dirty_unambiguous(const void *addr, size_t len) {
    // Overlapping owners share canonical page records, so a covering live range
    // is the complete eligibility condition. Kept separate for certificate callers.
    return nvn_dirty_tracked(addr, len);
}

int nvn_dirty_protect_checked(const void *addr, size_t len) {
    unsigned bank = read_enter();
    range_t *r = find_cover((uintptr_t)addr, len, UINT64_MAX);
    int ok = r != NULL;
    if (ok) {
        uintptr_t p = page_index(r, (uintptr_t)addr);
        uintptr_t q = page_index(r, (uintptr_t)addr + len - 1);
        uintptr_t run = 0;
        int open = 0;
        for (uintptr_t i = p; i <= q + 1; i++) {
            uint8_t st = PG_NONE;
            int took = i <= q && atomic_compare_exchange_strong(&r->page[i]->state, &st, PG_BUSY);
            if (took && !open) { run = i; open = 1; }
            if (!took && open) {
                int protected = mp(r->base + (run << g_shift), (i - run) << g_shift, PROT_READ);
                for (uintptr_t k = run; k < i; k++) atomic_store(&r->page[k]->state, protected ? PG_PROT : PG_NONE);
                if (!protected) ok = 0;
                open = 0;
            }
            // A BUSY page may be becoming writable. Skipping it cannot prove
            // this upload cacheable, even when its write generation is old.
            if (i <= q && !took && st == PG_BUSY) ok = 0;
        }
    }
    read_leave(bank);
    // Renderer view caches take their epoch before calling this helper. Force
    // those caches to retry if this upload could not establish protection.
    if (!ok) atomic_fetch_add(&g_now, 1);
    return ok;
}
void nvn_dirty_protect(const void *addr, size_t len) { (void)nvn_dirty_protect_checked(addr, len); }
uint64_t nvn_dirty_now(void) { return atomic_load(&g_now); }
int nvn_dirty_since(const void *addr, size_t len, uint64_t stamp) {
    if (!len) return 0;
    if (!stamp) return 1;
    unsigned bank = read_enter();
    // Any covering owner registered by the stamp has held these canonical records
    // continuously since then; a later owner alone cannot vouch for older storage.
    range_t *r = find_cover((uintptr_t)addr, len, stamp);
    int dirty = !r;
    if (!dirty) {
        uintptr_t p = page_index(r, (uintptr_t)addr);
        uintptr_t q = page_index(r, (uintptr_t)addr + len - 1);
        for (uintptr_t i = p; i <= q; i++) {
            page_t *pg = r->page[i];
            if (atomic_load(&pg->state) == PG_BUSY || atomic_load(&pg->gen) > stamp) { dirty = 1; break; }
        }
    }
    read_leave(bank);
    return dirty;
}
void nvn_dirty_stat(int *live, int *peak, int *cap) {
    *live = atomic_load(&g_live); *peak = atomic_load(&g_live_peak); *cap = MAX_RANGE;
}
void nvn_dirty_slot_stats(int *live, int *used_slots, uint64_t *adds_total) {
    *live = atomic_load(&g_live); *used_slots = atomic_load(&g_nr); *adds_total = atomic_load(&g_adds_total);
}
void nvn_dirty_stats(uint64_t *faults, uint64_t *preps) { *faults = atomic_load(&g_faults); *preps = atomic_load(&g_preps); }
void nvn_dirty_mp_stats(uint64_t *calls, uint64_t *pages, uint64_t *ns) {
    pthread_once(&g_once, dirty_init);
    uint64_t dt = atomic_load(&g_mp_ticks);
    *calls = atomic_load(&g_mp_calls); *pages = atomic_load(&g_mp_pages);
    *ns = g_tb.denom ? dt / g_tb.denom * g_tb.numer + dt % g_tb.denom * g_tb.numer / g_tb.denom : 0;
}
