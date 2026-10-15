


// Open-addressing hash table from fixed-length byte keys -> pointers (linear probing + backward shift on deletion, no tombstones).
// The replay hot path uses it instead of NSDictionary: keys are not boxed, no allocation, a lookup does only one hash + memcmp.
// No locking: callers protect it with their own locks. Ownership of values belongs to the caller (put / del return the replaced / deleted old value). Unit test tests/t_hmap.c.
#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint8_t *keys; void **vals; uint32_t klen, cap, n; } nvn_hmap;   //vals[i] == NULL means an empty slot

static inline uint64_t nvn_hm_hash(const void *k, uint32_t len) {
    const uint8_t *p = k; uint64_t h = 0x9e3779b97f4a7c15ull ^ len;
    for (; len >= 8; p += 8, len -= 8) { uint64_t w; memcpy(&w, p, 8); h = (h ^ w) * 0xff51afd7ed558ccdull; h ^= h >> 32; }
    if (len) { uint64_t w = 0; memcpy(&w, p, len); h = (h ^ w) * 0xff51afd7ed558ccdull; }
    h ^= h >> 33; h *= 0xc4ceb9fe1a85ec53ull; h ^= h >> 33;
    return h;
}
static inline void nvn_hm_init(nvn_hmap *m, uint32_t klen) { memset(m, 0, sizeof *m); m->klen = klen; }
static inline void *nvn_hm_get(const nvn_hmap *m, const void *k) {
    if (!m->n) return NULL;
    for (uint32_t i = (uint32_t)nvn_hm_hash(k, m->klen) & (m->cap - 1);; i = (i + 1) & (m->cap - 1)) {
        if (!m->vals[i]) return NULL;
        if (!memcmp(m->keys + (size_t)i * m->klen, k, m->klen)) return m->vals[i];
    }
}
static inline int nvn_hm_put_checked(nvn_hmap *m, const void *k, void *v, void **old);
static inline int nvn_hm_grow_checked(nvn_hmap *m) {
    nvn_hmap o = *m;
    if (o.cap > UINT32_MAX / 2 || !o.klen) return -1;
    nvn_hmap next = {.cap = o.cap ? o.cap * 2 : 64, .klen = o.klen};
    if ((size_t)next.cap > SIZE_MAX / next.klen || (size_t)next.cap > SIZE_MAX / sizeof *next.vals) return -1;
    next.keys = malloc((size_t)next.cap * next.klen); next.vals = calloc(next.cap, sizeof *next.vals);
    if (!next.keys || !next.vals) { free(next.keys); free(next.vals); return -1; }
    for (uint32_t i = 0; i < o.cap; i++) if (o.vals[i]) {
        /* Doubling guarantees sufficient capacity for every existing entry. */
        (void)nvn_hm_put_checked(&next, o.keys + (size_t)i * o.klen, o.vals[i], NULL);
    }
    *m = next;
    free(o.keys); free(o.vals);
    return 0;
}

/* 0 succeeds, -1 leaves the table and value ownership unchanged. */
static inline int nvn_hm_put_checked(nvn_hmap *m, const void *k, void *v, void **old) {
    if (old) *old = NULL;
    if (!m || !k || !v || !m->klen) return -1;
    if (((uint64_t)m->n + 1) * 4 > (uint64_t)m->cap * 3 && nvn_hm_grow_checked(m)) return -1;
    for (uint32_t i = (uint32_t)nvn_hm_hash(k, m->klen) & (m->cap - 1);; i = (i + 1) & (m->cap - 1)) {
        uint8_t *ki = m->keys + (size_t)i * m->klen;
        if (!m->vals[i]) { memcpy(ki, k, m->klen); m->vals[i] = v; m->n++; return 0; }
        if (!memcmp(ki, k, m->klen)) { if (old) *old = m->vals[i]; m->vals[i] = v; return 0; }
    }
}
/* Compatibility helpers cannot report allocation failure; new port consumers
 * use the checked forms. Failure leaves the table unchanged. */
static inline void nvn_hm_grow(nvn_hmap *m) { (void)nvn_hm_grow_checked(m); }
static inline void *nvn_hm_put(nvn_hmap *m, const void *k, void *v) {
    void *old = NULL; (void)nvn_hm_put_checked(m, k, v, &old); return old;
}

// After deletion, shift back later entries on the same probe chain to fill the hole, so that a lookup can stop at the first empty slot
static inline void *nvn_hm_del(nvn_hmap *m, const void *k) {
    if (!m->n) return NULL;
    uint32_t mask = m->cap - 1, i = (uint32_t)nvn_hm_hash(k, m->klen) & mask;
    for (;; i = (i + 1) & mask) {
        if (!m->vals[i]) return NULL;
        if (!memcmp(m->keys + (size_t)i * m->klen, k, m->klen)) break;
    }
    void *old = m->vals[i];
    for (uint32_t j = i;;) {
        m->vals[i] = NULL;
        for (;;) {
            j = (j + 1) & mask;
            if (!m->vals[j]) { m->n--; return old; }
            uint32_t h = (uint32_t)nvn_hm_hash(m->keys + (size_t)j * m->klen, m->klen) & mask;
            if (i <= j ? (i < h && h <= j) : (i < h || h <= j)) continue;   //Ideal position is within (i, j]: cannot move forward
            break;
        }
        memcpy(m->keys + (size_t)i * m->klen, m->keys + (size_t)j * m->klen, m->klen); m->vals[i] = m->vals[j];
        i = j;
    }
}

// Clear; when rel is non-null, call it once for each value (releasing ownership)
static inline void nvn_hm_clear(nvn_hmap *m, void (*rel)(void *)) {
    for (uint32_t i = 0; i < m->cap; i++) if (m->vals[i]) { if (rel) rel(m->vals[i]); m->vals[i] = NULL; }
    m->n = 0;
}
