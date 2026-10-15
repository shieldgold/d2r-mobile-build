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

static int range(const void *addr, uint64_t size, uintptr_t *lo, uintptr_t *hi) {
    uintptr_t a = (uintptr_t)addr;
    if (!size || size > UINTPTR_MAX - a) return 0;
    *lo = a; *hi = a + (uintptr_t)size;
    return 1;
}
static int push(own_t o) {   // Holds g_mu
    if (g_n == g_cap) {
        if (g_cap > SIZE_MAX / 2) return -1;
        size_t cap = g_cap ? g_cap * 2 : 64;
        if (cap > SIZE_MAX / sizeof *g_own) return -1;
        own_t *n = realloc(g_own, cap * sizeof *n);
        if (!n) return -1;
        g_own = n; g_cap = cap;
    }
    g_own[g_n++] = o;
    return 0;
}
static const own_t *overlap(uintptr_t lo, uintptr_t hi) {   // Holds g_mu
    for (size_t i = 0; i < g_n; i++) if (g_own[i].lo < hi && lo < g_own[i].hi) return &g_own[i];
    return NULL;
}
static void fill_hit(nvn_texown_hit *hit, const own_t *o) {
    if (hit) *hit = (nvn_texown_hit){o->serial, o->lo, o->hi, o->retired};
}

int nvn_texown_claim_checked(uint64_t serial, const void *addr, uint64_t size) {
    uintptr_t lo, hi;
    if (!serial || !range(addr, size, &lo, &hi)) return -1;
    pthread_mutex_lock(&g_mu);
    int dup = 0;
    for (size_t i = 0; i < g_n && !dup; i++)
        dup = !g_own[i].retired && g_own[i].serial == serial && g_own[i].lo == lo && g_own[i].hi == hi;
    int rc = dup ? 0 : push((own_t){lo, hi, serial, 0});
    pthread_mutex_unlock(&g_mu);
    return rc;
}
void nvn_texown_claim(uint64_t serial, const void *addr, uint64_t size) {
    (void)nvn_texown_claim_checked(serial, addr, size);
}

int nvn_texown_claim_fresh(uint64_t serial, const void *addr, uint64_t size, nvn_texown_hit *hit) {
    uintptr_t lo, hi;
    if (!serial || !range(addr, size, &lo, &hi)) return -1;
    pthread_mutex_lock(&g_mu);
    const own_t *o = overlap(lo, hi);
    if (o) fill_hit(hit, o);
    int rc = o ? 0 : push((own_t){lo, hi, serial, 0}) ? -1 : 1;
    pthread_mutex_unlock(&g_mu);
    return rc;
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

size_t nvn_texown_count(int retired) {
    pthread_mutex_lock(&g_mu);
    size_t n = 0;
    for (size_t i = 0; i < g_n; i++) n += !g_own[i].retired == !retired;
    pthread_mutex_unlock(&g_mu);
    return n;
}

void nvn_texown_reset_quiescent(void) {
    pthread_mutex_lock(&g_mu);
    free(g_own); g_own = NULL; g_n = g_cap = 0;
    pthread_mutex_unlock(&g_mu);
}
