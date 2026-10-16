// CPU storage ownership for separately allocated Metal textures, see nvn_texown.h.
// Records are an unsorted array: claims are made when Metal storage is created and checked only when a fresh
// texture imports its pool bytes, both rare compared with replay work.
#include "nvn_texown.h"
#include <pthread.h>
#include <stdlib.h>

typedef struct { uintptr_t lo, hi; uint64_t serial; int retired; } own_t;
static own_t *g_own;
static size_t g_n, g_cap;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
// Sharing serial -> owner serial, open addressing with linear probing (key 0 = empty, tombstones = UINT64_MAX).
typedef struct { uint64_t serial, owner; } share_t;
static share_t *g_share;
static size_t g_share_cap, g_share_n;
static size_t share_slot(uint64_t serial) { return (size_t)((serial * 0x9e3779b97f4a7c15ull) >> 20) & (g_share_cap - 1); }
static share_t *share_find(uint64_t serial) {   // Holds g_mu
    if (!g_share_cap) return NULL;
    for (size_t i = share_slot(serial), k = 0; k < g_share_cap; i = (i + 1) & (g_share_cap - 1), k++) {
        if (g_share[i].serial == serial) return &g_share[i];
        if (!g_share[i].serial) return NULL;
    }
    return NULL;
}
static void share_put(uint64_t serial, uint64_t owner) {   // Holds g_mu
    share_t *e = share_find(serial);
    if (e) { e->owner = owner; return; }
    if ((g_share_n + 1) * 2 > g_share_cap) {
        size_t old = g_share_cap; share_t *prev = g_share;
        g_share_cap = old ? old * 2 : 1024; g_share = calloc(g_share_cap, sizeof *g_share); if (!g_share) abort();
        g_share_n = 0;
        for (size_t i = 0; i < old; i++) if (prev[i].serial && prev[i].serial != UINT64_MAX) share_put(prev[i].serial, prev[i].owner);
        free(prev);
    }
    for (size_t i = share_slot(serial);; i = (i + 1) & (g_share_cap - 1))
        if (!g_share[i].serial || g_share[i].serial == UINT64_MAX) { g_share[i] = (share_t){serial, owner}; g_share_n++; return; }
}
static uint64_t owner_of(uint64_t serial) { share_t *e = share_find(serial); return e ? e->owner : serial; }   // Holds g_mu

static int range(const void *addr, uint64_t size, uintptr_t *lo, uintptr_t *hi) {
    uintptr_t a = (uintptr_t)addr;
    if (!size || size > UINTPTR_MAX - a) return 0;
    *lo = a; *hi = a + (uintptr_t)size;
    return 1;
}
static void push(own_t o) {   // Holds g_mu
    if (g_n == g_cap) {
        size_t cap = g_cap ? g_cap * 2 : 64;
        own_t *n = realloc(g_own, cap * sizeof *n);
        if (!n) abort();
        g_own = n; g_cap = cap;
    }
    g_own[g_n++] = o;
}
static const own_t *overlap(uintptr_t lo, uintptr_t hi) {   // Holds g_mu
    for (size_t i = 0; i < g_n; i++) if (g_own[i].lo < hi && lo < g_own[i].hi) return &g_own[i];
    return NULL;
}
static void fill_hit(nvn_texown_hit *hit, const own_t *o) {
    if (hit) *hit = (nvn_texown_hit){o->serial, o->lo, o->hi, o->retired};
}

void nvn_texown_claim(uint64_t serial, const void *addr, uint64_t size) {
    uintptr_t lo, hi;
    if (!serial || !range(addr, size, &lo, &hi)) return;
    pthread_mutex_lock(&g_mu);
    int dup = 0;
    for (size_t i = 0; i < g_n && !dup; i++)
        dup = !g_own[i].retired && g_own[i].serial == serial && g_own[i].lo == lo && g_own[i].hi == hi;
    if (!dup) push((own_t){lo, hi, serial, 0});
    pthread_mutex_unlock(&g_mu);
}

int nvn_texown_claim_fresh(uint64_t serial, const void *addr, uint64_t size, nvn_texown_hit *hit) {
    uintptr_t lo, hi;
    if (!serial || !range(addr, size, &lo, &hi)) return -1;
    pthread_mutex_lock(&g_mu);
    const own_t *o = overlap(lo, hi);
    if (o) fill_hit(hit, o);
    else push((own_t){lo, hi, serial, 0});
    pthread_mutex_unlock(&g_mu);
    return o ? 0 : 1;
}

int nvn_texown_find(const void *addr, uint64_t size, nvn_texown_hit *hit) {
    uintptr_t lo, hi;
    if (!range(addr, size, &lo, &hi)) return 0;
    pthread_mutex_lock(&g_mu);
    const own_t *o = overlap(lo, hi);
    if (o) fill_hit(hit, o);
    pthread_mutex_unlock(&g_mu);
    return o != NULL;
}

int nvn_texown_find_other(uint64_t serial, const void *addr, uint64_t size, nvn_texown_hit *hit) {
    uintptr_t lo, hi;
    if (!range(addr, size, &lo, &hi)) return 0;
    pthread_mutex_lock(&g_mu);
    const own_t *o = NULL;
    uint64_t self = owner_of(serial);
    for (size_t i = 0; i < g_n && !o; i++)
        if (g_own[i].lo < hi && lo < g_own[i].hi && (g_own[i].retired || g_own[i].serial != self)) o = &g_own[i];
    if (o) fill_hit(hit, o);
    pthread_mutex_unlock(&g_mu);
    return o != NULL;
}

void nvn_texown_retire(uint64_t serial) {
    if (!serial) return;
    pthread_mutex_lock(&g_mu);
    for (size_t i = 0; i < g_n; i++) if (!g_own[i].retired && g_own[i].serial == serial) g_own[i].retired = 1;
    // Merge touching or overlapping retired records until stable; a merged record keeps the earlier record's serial.
    for (int merged = 1; merged;) {
        merged = 0;
        for (size_t i = 0; i < g_n && !merged; i++) {
            if (!g_own[i].retired) continue;
            for (size_t j = i + 1; j < g_n; j++) {
                if (!g_own[j].retired || g_own[j].lo > g_own[i].hi || g_own[i].lo > g_own[j].hi) continue;
                if (g_own[j].lo < g_own[i].lo) g_own[i].lo = g_own[j].lo;
                if (g_own[j].hi > g_own[i].hi) g_own[i].hi = g_own[j].hi;
                g_own[j] = g_own[--g_n];
                merged = 1;
                break;
            }
        }
    }
    pthread_mutex_unlock(&g_mu);
}

void nvn_texown_share(uint64_t serial, uint64_t owner) {
    if (!serial || !owner || serial == owner) return;
    pthread_mutex_lock(&g_mu); share_put(serial, owner); pthread_mutex_unlock(&g_mu);
}
void nvn_texown_unshare(uint64_t serial) {
    pthread_mutex_lock(&g_mu);
    share_t *e = share_find(serial);
    if (e) { e->serial = UINT64_MAX; e->owner = 0; g_share_n--; }
    pthread_mutex_unlock(&g_mu);
}
uint64_t nvn_texown_owner(uint64_t serial) { pthread_mutex_lock(&g_mu); uint64_t o = owner_of(serial); pthread_mutex_unlock(&g_mu); return o; }

size_t nvn_texown_count(int retired) {
    pthread_mutex_lock(&g_mu);
    size_t n = 0;
    for (size_t i = 0; i < g_n; i++) n += !g_own[i].retired == !retired;
    pthread_mutex_unlock(&g_mu);
    return n;
}
