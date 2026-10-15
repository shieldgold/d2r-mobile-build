// NVN virtual memory pools as logical GPU address intervals. See nvn_vpool.h for the model,
// the lease contract and the lock order.
//
// Invariants (all guarded by the registry lock):
//   - Each virtual pool keeps its spans sorted by voff, non-overlapping, each inside the pool.
//   - Every span references a live physical pool; physical finalize removes the spans first.
//   - Virtual pools are kept sorted by base. Finalized pools stay in the table as tombstones
//     that still own their PROT_NONE reservation, so no later pool can be given those addresses.
//   - A span's gen changes whenever its interval -> physical binding changes. Splitting keeps
//     the gen; adjacent spans are merged only when their gens are equal. A cached stamp
//     therefore stays valid exactly while every covering span's gen is <= the stamp.
#include "nvn_vpool.h"
#include <pthread.h>
#include <limits.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

// Fault controls and accounting are compiled out of production. Actual allocator/VM
// calls remain in use, including the high-address reservation test (no fake addresses).
#ifdef NVN_VPOOL_TESTING
static _Atomic long vp_alloc_after = -1, vp_reserve_after = -1;
static _Atomic uint64_t vp_alloc_calls, vp_alloc_live, vp_reserve_calls, vp_reserve_live, vp_reserved, vp_reserve_floor;
static int vp_test_fail(_Atomic long *count) {
    long n = atomic_load(count);
    while (n > 0) if (atomic_compare_exchange_weak(count, &n, n - 1)) return 0;
    return n == 0;
}
void nvn_vpool_test_fail_allocation_after(long n) { atomic_store(&vp_alloc_after, n); }
void nvn_vpool_test_fail_reservation_after(long n) { atomic_store(&vp_reserve_after, n); }
void nvn_vpool_test_reservation_floor(uint64_t n) { atomic_store(&vp_reserve_floor, n); }
static void *vp_malloc(size_t n) {
    atomic_fetch_add(&vp_alloc_calls, 1); if (vp_test_fail(&vp_alloc_after)) return NULL;
    void *p = malloc(n); if (p) atomic_fetch_add(&vp_alloc_live, 1); return p;
}
static void *vp_calloc(size_t n, size_t bytes) {
    atomic_fetch_add(&vp_alloc_calls, 1); if (vp_test_fail(&vp_alloc_after)) return NULL;
    void *p = calloc(n, bytes); if (p) atomic_fetch_add(&vp_alloc_live, 1); return p;
}
static void *vp_realloc(void *old, size_t n) {
    atomic_fetch_add(&vp_alloc_calls, 1); if (vp_test_fail(&vp_alloc_after)) return NULL;
    int fresh = old == NULL; void *p = realloc(old, n);
    if (p && fresh) atomic_fetch_add(&vp_alloc_live, 1); return p;
}
static void vp_free(void *p) { if (p) atomic_fetch_sub(&vp_alloc_live, 1); free(p); }
#else
#define vp_malloc malloc
#define vp_calloc calloc
#define vp_realloc realloc
#define vp_free free
#endif

_Static_assert(sizeof(NVNmappingRequest) == 40, "NVNmappingRequest is 40 bytes");
_Static_assert(offsetof(NVNmappingRequest, physicalPool) == 0, "physicalPool at +0");
_Static_assert(offsetof(NVNmappingRequest, physicalOffset) == 8, "physicalOffset at +8");
_Static_assert(offsetof(NVNmappingRequest, virtualOffset) == 16, "virtualOffset at +16");
_Static_assert(offsetof(NVNmappingRequest, size) == 24, "size at +24");
_Static_assert(offsetof(NVNmappingRequest, storageClass) == 32, "storageClass at +32");

typedef struct {
    const void *owner;
    uint8_t *mem;
    uint64_t size, id, nspans;
} phys_t;

typedef struct {
    uint64_t voff, len, poff, gen;
    phys_t *p;
    uint32_t raw_kind;
} span_t;

typedef struct {
    const void *owner;
    uint64_t base, size, gen;
    span_t *s;
    unsigned n;
    int live;
} virt_t;

static phys_t **g_phys;
static unsigned g_nphys, g_cphys;
static virt_t **g_virt;            // Sorted by base; includes tombstones
static unsigned g_nvirt, g_cvirt;
static uint64_t g_phys_ids, g_above_limit;
static _Atomic uint64_t g_gen;

// ---------- Registry lock ----------
// Writer-preferring, with a per-thread read depth so a thread that already holds a lease can
// take another one even while a writer waits (pthread_rwlock would deadlock there).
static pthread_mutex_t g_lk = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_cv = PTHREAD_COND_INITIALIZER;
static int g_readers, g_writer, g_wwait, g_rwait;
static _Thread_local int t_depth;

static void rd_lock(void) {
    if (t_depth++ > 0) return;
    pthread_mutex_lock(&g_lk);
    g_rwait++;
    while (g_writer || g_wwait) pthread_cond_wait(&g_cv, &g_lk);
    g_rwait--;
    g_readers++;
    pthread_mutex_unlock(&g_lk);
}
static void rd_unlock(void) {
    if (--t_depth > 0) return;
    pthread_mutex_lock(&g_lk);
    if (--g_readers == 0) pthread_cond_broadcast(&g_cv);
    pthread_mutex_unlock(&g_lk);
}
static int wr_lock(void) {
    if (t_depth > 0) return NVN_VPOOL_E_LOCK_ORDER;
    pthread_mutex_lock(&g_lk);
    g_wwait++;
    while (g_writer || g_readers) pthread_cond_wait(&g_cv, &g_lk);
    g_wwait--;
    g_writer = 1;
    pthread_mutex_unlock(&g_lk);
    return NVN_VPOOL_OK;
}
static void wr_unlock(void) {
    pthread_mutex_lock(&g_lk);
    g_writer = 0;
    pthread_cond_broadcast(&g_cv);
    pthread_mutex_unlock(&g_lk);
}

#ifdef NVN_VPOOL_TESTING
void nvn_vpool_test_get_stats(nvn_vpool_test_stats *out) {
    if (!out) return;
    rd_lock();
    *out = (nvn_vpool_test_stats){atomic_load(&vp_alloc_calls), atomic_load(&vp_alloc_live),
        atomic_load(&vp_reserve_calls), atomic_load(&vp_reserve_live), atomic_load(&vp_reserved),
        g_cphys, g_cvirt};
    rd_unlock();
}
#endif

// ---------- Lookups (caller holds the lock) ----------
static phys_t *phys_find(const void *owner) {
    for (unsigned i = 0; i < g_nphys; i++) if (g_phys[i]->owner == owner) return g_phys[i];
    return NULL;
}
static virt_t *virt_find(const void *owner) {
    for (unsigned i = 0; i < g_nvirt; i++) if (g_virt[i]->live && g_virt[i]->owner == owner) return g_virt[i];
    return NULL;
}
// Last pool whose base <= a, or -1.
static int virt_floor(uint64_t a) {
    int lo = 0, hi = (int)g_nvirt - 1, r = -1;
    while (lo <= hi) {
        int m = lo + (hi - lo) / 2;
        if (g_virt[m]->base <= a) { r = m; lo = m + 1; } else hi = m - 1;
    }
    return r;
}
// Any pool (live or tombstone) intersecting [a, a + n); n > 0 and a + n does not overflow.
static virt_t *virt_overlap(uint64_t a, uint64_t n) {
    int i = virt_floor(a + n - 1);
    if (i < 0) return NULL;
    virt_t *v = g_virt[i];
    return a < v->base + v->size ? v : NULL;
}
// First span of v whose end is above off.
static unsigned span_lower(const virt_t *v, uint64_t off) {
    unsigned lo = 0, hi = v->n;
    while (lo < hi) {
        unsigned m = lo + (hi - lo) / 2;
        if (v->s[m].voff + v->s[m].len <= off) lo = m + 1; else hi = m;
    }
    return lo;
}
static int range_ok(uint64_t off, uint64_t n, uint64_t size) { return n <= size && off <= size - n; }

static int grow(void **arr, unsigned *cap, unsigned need, size_t elem) {
    if (need <= *cap) return 1;
    if (!elem || (size_t)need > SIZE_MAX / elem) return 0;
    unsigned c = *cap ? (*cap > UINT_MAX / 2 ? UINT_MAX : *cap * 2) : 16;
    if (c < need || (size_t)c > SIZE_MAX / elem) c = need;
    void *p = vp_realloc(*arr, (size_t)c * elem);
    if (!p) return 0;
    *arr = p; *cap = c;
    return 1;
}

static void errf(char *err, size_t errlen, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void errf(char *err, size_t errlen, const char *fmt, ...) {
    if (!err || !errlen) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

const char *nvn_vpool_status_name(int st) {
    static const char *const names[] = {
        "ok", "not virtual", "unmapped", "stale", "invalid", "unknown pool",
        "duplicate", "unsupported storage class", "overlaps virtual reservation",
        "out of memory", "lock order violation",
    };
    return st >= 0 && st < (int)(sizeof names / sizeof *names) ? names[st] : "unknown status";
}

// ---------- Physical pools ----------
int nvn_vpool_phys_register(const void *owner, void *mem, uint64_t size) {
    uintptr_t m = (uintptr_t)mem;
    if (!owner || !mem || !size || m % NVN_VPOOL_PHYS_ALIGN || size > UINT64_MAX - m) return NVN_VPOOL_INVALID;
    int st = wr_lock();
    if (st) return st;
    phys_t *p = NULL;
    if (phys_find(owner) || virt_find(owner)) st = NVN_VPOOL_E_DUPLICATE;
    else if (virt_overlap(m, size)) st = NVN_VPOOL_E_OVERLAP;
    else if (g_nphys == UINT_MAX || !(p = vp_calloc(1, sizeof *p)) ||
             !grow((void **)&g_phys, &g_cphys, g_nphys + 1, sizeof *g_phys)) {
        vp_free(p); st = NVN_VPOOL_E_NOMEM;
    }
    else {
        *p = (phys_t){owner, (uint8_t *)mem, size, ++g_phys_ids, 0};
        g_phys[g_nphys++] = p;
    }
    wr_unlock();
    return st;
}

int nvn_vpool_phys_finalize(const void *owner, uint64_t *invalidated) {
    if (invalidated) *invalidated = 0;
    int st = wr_lock();
    if (st) return st;
    unsigned idx = 0;
    while (idx < g_nphys && g_phys[idx]->owner != owner) idx++;
    if (idx == g_nphys) { wr_unlock(); return NVN_VPOOL_E_UNKNOWN_POOL; }
    phys_t *p = g_phys[idx];
    uint64_t removed = 0;
    if (p->nspans) {
        uint64_t gen = atomic_fetch_add(&g_gen, 1) + 1;
        for (unsigned i = 0; i < g_nvirt; i++) {
            virt_t *v = g_virt[i];
            unsigned w = 0;
            for (unsigned r = 0; r < v->n; r++) if (v->s[r].p != p) v->s[w++] = v->s[r];
            if (w != v->n) { removed += v->n - w; v->n = w; v->gen = gen; }
        }
    }
    g_phys[idx] = g_phys[--g_nphys];
    vp_free(p);
    wr_unlock();
    if (invalidated) *invalidated = removed;
    return NVN_VPOOL_OK;
}

// ---------- Virtual pools ----------
// Reserves [base, base + size) as PROT_NONE with base aligned to NVN_VPOOL_PAGE. Never uses
// MAP_FIXED, so existing mappings cannot be overwritten; a hint is only a preference.
static uint64_t reserve_at(uint64_t hint, uint64_t size) {
#ifdef NVN_VPOOL_TESTING
    atomic_fetch_add(&vp_reserve_calls, 1);
    if (vp_test_fail(&vp_reserve_after)) return 0;
    uint64_t floor = atomic_load(&vp_reserve_floor);
    if (floor) hint = floor;
#endif
    uint64_t total = size + NVN_VPOOL_PAGE;
    void *p = mmap((void *)(uintptr_t)hint, total, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (p == MAP_FAILED) return 0;
#ifdef NVN_VPOOL_TESTING
    if ((uint64_t)(uintptr_t)p < floor) { munmap(p, total); return 0; }
#endif
    uint64_t a = (uint64_t)(uintptr_t)p, b = (a + NVN_VPOOL_PAGE - 1) & ~(uint64_t)(NVN_VPOOL_PAGE - 1);
    if (b > a) munmap(p, b - a);
    if (a + total > b + size) munmap((void *)(uintptr_t)(b + size), a + total - (b + size));
#ifdef NVN_VPOOL_TESTING
    atomic_fetch_add(&vp_reserve_live, 1); atomic_fetch_add(&vp_reserved, size);
#endif
    return b;
}
static void release_reservation(uint64_t base, uint64_t size) {
    int result = munmap((void *)(uintptr_t)base, size);
#ifdef NVN_VPOOL_TESTING
    if (!result) { atomic_fetch_sub(&vp_reserve_live, 1); atomic_fetch_sub(&vp_reserved, size); }
#else
    (void)result;
#endif
}
static uint64_t reserve(uint64_t size, int *above) {
    *above = 0;
    uint64_t b = reserve_at(0, size);
    if (b && b + size <= NVN_VPOOL_VA_LIMIT) return b;
    if (b) release_reservation(b, size);
    // The kernel's default placement was too high: probe a few hints below the limit.
    for (int k = 36; k <= 39; k++) {
        uint64_t hint = 1ull << k;
        if (size > NVN_VPOOL_VA_LIMIT - hint) break;
        b = reserve_at(hint, size);
        if (b && b + size <= NVN_VPOOL_VA_LIMIT) return b;
        if (b) release_reservation(b, size);
    }
    b = reserve_at(0, size);
    if (b) *above = 1;
    return b;
}

static int register_virtual(const void *owner, uint64_t size, uint64_t *base, int *above_va_limit, int strict) {
    if (base) *base = 0;
    if (above_va_limit) *above_va_limit = 0;
    if (!owner || !base || !size || size % NVN_VPOOL_PAGE || size > UINT64_MAX / 2) return NVN_VPOOL_INVALID;
    if (t_depth > 0) return NVN_VPOOL_E_LOCK_ORDER;
    int above;
    uint64_t b = reserve(size, &above);
    if (!b) return NVN_VPOOL_E_NOMEM;
    if (strict && (b >= NVN_VPOOL_VA_LIMIT || size > NVN_VPOOL_VA_LIMIT - b)) {
        release_reservation(b, size); return NVN_VPOOL_E_NOMEM;
    }
    int st = wr_lock();
    if (st) { release_reservation(b, size); return st; }
    virt_t *v = NULL;
    if (phys_find(owner) || virt_find(owner)) st = NVN_VPOOL_E_DUPLICATE;
    else if (g_nvirt == UINT_MAX || !(v = vp_calloc(1, sizeof *v)) ||
             !grow((void **)&g_virt, &g_cvirt, g_nvirt + 1, sizeof *g_virt)) {
        vp_free(v); st = NVN_VPOOL_E_NOMEM;
    }
    else {
        *v = (virt_t){.owner = owner, .base = b, .size = size, .gen = atomic_fetch_add(&g_gen, 1) + 1, .live = 1};
        unsigned at = (unsigned)(virt_floor(b) + 1);
        memmove(&g_virt[at + 1], &g_virt[at], (g_nvirt - at) * sizeof *g_virt);
        g_virt[at] = v;
        g_nvirt++;
        if (above) g_above_limit++;
    }
    wr_unlock();
    if (st) { release_reservation(b, size); return st; }
    *base = b;
    if (above_va_limit) *above_va_limit = above;
    return NVN_VPOOL_OK;
}

int nvn_vpool_virt_register(const void *owner, uint64_t size, uint64_t *base, int *above_va_limit) {
    return register_virtual(owner, size, base, above_va_limit, 0);
}

int nvn_vpool_virt_finalize(const void *owner) {
    int st = wr_lock();
    if (st) return st;
    virt_t *v = virt_find(owner);
    if (!v) { wr_unlock(); return NVN_VPOOL_E_UNKNOWN_POOL; }
    for (unsigned i = 0; i < v->n; i++) v->s[i].p->nspans--;
    vp_free(v->s);
    v->s = NULL; v->n = 0; v->live = 0;
    v->gen = atomic_fetch_add(&g_gen, 1) + 1;
    wr_unlock();
    return NVN_VPOOL_OK;
}

// ---------- MapVirtual ----------
// Replaces [a, a + n) of src with ns (or a hole when ns is NULL) into dst. dst has room for
// srcn + 2 spans; returns the new count.
static unsigned punch(const span_t *src, unsigned srcn, span_t *dst, uint64_t a, uint64_t n, const span_t *ns) {
    uint64_t e = a + n;
    unsigned w = 0, i = 0;
    for (; i < srcn && src[i].voff + src[i].len <= a; i++) dst[w++] = src[i];
    for (; i < srcn && src[i].voff < e; i++) {
        span_t s = src[i];
        if (s.voff < a) { span_t l = s; l.len = a - s.voff; dst[w++] = l; }
        if (s.voff + s.len > e) {
            if (ns) { dst[w++] = *ns; ns = NULL; }
            span_t r = s; uint64_t cut = e - s.voff;
            r.voff = e; r.poff = s.poff + cut; r.len = s.len - cut;
            dst[w++] = r;
        }
    }
    if (ns) dst[w++] = *ns;
    for (; i < srcn; i++) dst[w++] = src[i];
    return w;
}

static unsigned coalesce(span_t *s, unsigned n) {
    if (!n) return 0;
    unsigned w = 0;
    for (unsigned i = 1; i < n; i++) {
        span_t *t = &s[w];
        if (s[i].p == t->p && s[i].gen == t->gen && s[i].raw_kind == t->raw_kind && t->voff + t->len == s[i].voff && t->poff + t->len == s[i].poff) t->len += s[i].len;
        else s[++w] = s[i];
    }
    return w + 1;
}

static int check_req(const virt_t *v, const NVNmappingRequest *r, int i, char *err, size_t errlen) {
    const uint64_t pg = NVN_VPOOL_PAGE;
    if (!r->size || r->size % pg || r->virtualOffset % pg || !range_ok(r->virtualOffset, r->size, v->size)) {
        errf(err, errlen, "request %d: virtual range %#llx+%#llx is empty, not %#llx-aligned or outside the %#llx-byte pool",
             i, (unsigned long long)r->virtualOffset, (unsigned long long)r->size, (unsigned long long)pg, (unsigned long long)v->size);
        return NVN_VPOOL_INVALID;
    }
    if (!r->physicalPool) return NVN_VPOOL_OK;
    if (r->storageClass != 0 && r->storageClass != 0x100 && r->storageClass != 0x1fe) {
        errf(err, errlen, "request %d: unsupported storage class %#x", i, (unsigned)r->storageClass);
        return NVN_VPOOL_E_CLASS;
    }
    const phys_t *p = phys_find(r->physicalPool);
    if (!p) {
        errf(err, errlen, "request %d: %p is not a live physical pool", i, (const void *)r->physicalPool);
        return NVN_VPOOL_E_UNKNOWN_POOL;
    }
    if (r->physicalOffset % pg || !range_ok(r->physicalOffset, r->size, p->size)) {
        errf(err, errlen, "request %d: physical range %#llx+%#llx is not %#llx-aligned or outside the %#llx-byte pool",
             i, (unsigned long long)r->physicalOffset, (unsigned long long)r->size, (unsigned long long)pg, (unsigned long long)p->size);
        return NVN_VPOOL_INVALID;
    }
    return NVN_VPOOL_OK;
}

int nvn_vpool_map(const void *virt_owner, const NVNmappingRequest *reqs, int count, char *err, size_t errlen) {
    errf(err, errlen, "%s", "");
    if (count < 0 || (count > 0 && !reqs)) { errf(err, errlen, "invalid request count %d", count); return NVN_VPOOL_INVALID; }
    if (count == 0) {
        rd_lock();
        int live = virt_find(virt_owner) != NULL;
        rd_unlock();
        if (!live) errf(err, errlen, "%p is not a live virtual pool", virt_owner);
        return live ? NVN_VPOOL_OK : NVN_VPOOL_E_UNKNOWN_POOL;
    }
    int st = wr_lock();
    if (st) return st;
    virt_t *v = virt_find(virt_owner);
    if (!v) { errf(err, errlen, "%p is not a live virtual pool", virt_owner); wr_unlock(); return NVN_VPOOL_E_UNKNOWN_POOL; }
    for (int i = 0; i < count; i++) if ((st = check_req(v, &reqs[i], i, err, errlen))) { wr_unlock(); return st; }

    // Each request adds at most two spans (the new one plus a split remainder).
    uint64_t cap = (uint64_t)v->n + 2 * (uint64_t)count + 1;
    span_t *a = cap <= UINT_MAX && cap <= SIZE_MAX / sizeof *a ? vp_malloc(cap * sizeof *a) : NULL;
    span_t *b = a ? vp_malloc(cap * sizeof *b) : NULL;
    if (!b) { vp_free(a); errf(err, errlen, "out of memory for %llu spans", (unsigned long long)cap); wr_unlock(); return NVN_VPOOL_E_NOMEM; }

    uint64_t gen = atomic_fetch_add(&g_gen, 1) + 1;
    unsigned n = v->n;
    if (n) memcpy(a, v->s, n * sizeof *a);
    for (int i = 0; i < count; i++) {
        const NVNmappingRequest *r = &reqs[i];
        span_t ns, *nsp = NULL;
        if (r->physicalPool) {
            ns = (span_t){r->virtualOffset, r->size, r->physicalOffset, gen, phys_find(r->physicalPool), (uint32_t)r->storageClass & 0xffu};
            nsp = &ns;
        }
        n = punch(a, n, b, r->virtualOffset, r->size, nsp);
        span_t *t = a; a = b; b = t;
    }
    n = coalesce(a, n);
    for (unsigned i = 0; i < v->n; i++) v->s[i].p->nspans--;
    for (unsigned i = 0; i < n; i++) a[i].p->nspans++;
    vp_free(v->s);
    vp_free(b);
    v->s = a; v->n = n; v->gen = gen;
    wr_unlock();
    return NVN_VPOOL_OK;
}

// ---------- Leases ----------
int nvn_vpool_lease_acquire(nvn_vpool_lease *l, uint64_t addr, uint64_t len) {
    if (!l) return NVN_VPOOL_INVALID;
    memset(l, 0, sizeof *l);
    if (!len || len > UINT64_MAX - addr) return NVN_VPOOL_INVALID;
    rd_lock();
    virt_t *v = virt_overlap(addr, len);
    int st = NVN_VPOOL_OK;
    if (!v) st = NVN_VPOOL_NOT_VIRTUAL;
    else if (addr < v->base || !range_ok(addr - v->base, len, v->size)) st = NVN_VPOOL_INVALID;   // Straddles a pool boundary
    else if (!v->live) st = NVN_VPOOL_STALE;
    if (st) { rd_unlock(); return st; }

    uint64_t off = addr - v->base, end = off + len, at = off, gen = 0;
    unsigned first = span_lower(v, off), i = first;
    int contig = 1;
    for (; i < v->n && at < end; i++) {
        const span_t *s = &v->s[i];
        if (s->voff > at) break;   // Hole
        if (i > first && (s->p != v->s[i - 1].p || s->raw_kind != v->s[i - 1].raw_kind || v->s[i - 1].poff + v->s[i - 1].len != s->poff)) contig = 0;
        if (s->gen > gen) gen = s->gen;
        at = s->voff + s->len;
    }
    if (at < end) { rd_unlock(); return NVN_VPOOL_UNMAPPED; }

    const span_t *s0 = &v->s[first];
    *l = (nvn_vpool_lease){.addr = addr, .len = len, .gen = gen, .nspans = i - first, .contiguous = contig,
                           .v_ = v, .first_ = first, .held_ = 1};
    if (contig) {
        l->phys_owner = s0->p->owner;
        l->phys_id = s0->p->id;
        l->raw_kind = s0->raw_kind;
        l->cpu = s0->p->mem + s0->poff + (off - s0->voff);
    }
    return NVN_VPOOL_OK;
}

int nvn_vpool_lease_span(const nvn_vpool_lease *l, unsigned i, nvn_vpool_span *out) {
    if (!l || !out || !l->held_ || i >= l->nspans) return NVN_VPOOL_INVALID;
    const virt_t *v = l->v_;
    const span_t *s = &v->s[l->first_ + i];
    uint64_t a = l->addr - v->base, e = a + l->len;
    uint64_t lo = s->voff > a ? s->voff : a, hi = s->voff + s->len < e ? s->voff + s->len : e;
    *out = (nvn_vpool_span){.off = lo - a, .len = hi - lo, .phys_owner = s->p->owner, .phys_id = s->p->id,
                            .phys_off = s->poff + (lo - s->voff), .gen = s->gen, .raw_kind = s->raw_kind};
    out->cpu = s->p->mem + out->phys_off;
    return NVN_VPOOL_OK;
}

int nvn_vpool_lease_read(const nvn_vpool_lease *l, uint64_t off, void *dst, uint64_t n) {
    if (!l || !l->held_ || !range_ok(off, n, l->len) || (n && !dst)) return NVN_VPOOL_INVALID;
    if (!n) return NVN_VPOOL_OK;
    if (l->contiguous) { memcpy(dst, l->cpu + off, n); return NVN_VPOOL_OK; }
    uint8_t *d = dst;
    for (unsigned i = 0; i < l->nspans && n; i++) {
        nvn_vpool_span s;
        nvn_vpool_lease_span(l, i, &s);
        if (off >= s.off + s.len) continue;
        uint64_t skip = off - s.off, k = s.len - skip < n ? s.len - skip : n;
        memcpy(d, s.cpu + skip, k);
        d += k; off += k; n -= k;
    }
    return NVN_VPOOL_OK;
}

void nvn_vpool_lease_release(nvn_vpool_lease *l) {
    if (!l || !l->held_) return;
    l->held_ = 0;
    rd_unlock();
}

// ---------- Introspection ----------
uint64_t nvn_vpool_generation(void) { return atomic_load(&g_gen); }

uint64_t nvn_vpool_virt_gen(const void *virt_owner) {
    rd_lock();
    const virt_t *v = virt_find(virt_owner);
    uint64_t g = v ? v->gen : 0;
    rd_unlock();
    return g;
}

void nvn_vpool_stats(nvn_vpool_stats_t *o) {
    if (!o) return;
    memset(o, 0, sizeof *o);
    rd_lock();
    pthread_mutex_lock(&g_lk);
    o->active_readers = (unsigned)g_readers;
    o->waiting_readers = (unsigned)g_rwait;
    o->waiting_writers = (unsigned)g_wwait;
    pthread_mutex_unlock(&g_lk);
    o->phys_live = (int)g_nphys;
    for (unsigned i = 0; i < g_nvirt; i++) {
        const virt_t *v = g_virt[i];
        o->reserved_bytes += v->size;
        if (v->live) { o->virt_live++; o->spans += v->n; }
        else { o->tombstones++; o->tombstone_bytes += v->size; }
    }
    o->above_va_limit = g_above_limit;
    rd_unlock();
}

int nvn_vpool_virt_register_strict(const void *owner, uint64_t size, uint64_t *base) {
    return register_virtual(owner, size, base, NULL, 1);
}
