// CPU texel flushes (nvnTextureFlushTexels).
//
// Native range semantics for BotW 1.9.0 are limited to whole layer allocations. The host keeps
// separate CPU pool storage and Metal textures, so a verified no-op additionally needs proof that
// no CPU store occurred after a host-synchronized write. Dirty generations provide that proof;
// hashes are only an additional corruption check and never waive a dirty or ambiguous range.
#include "nvn_texflush.h"
#include "nvn_dirty.h"
#include "nvn_hmap.h"
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

static int fail(const char **why, const char *msg) { if (why) *why = msg; return NVN_TEXFLUSH_INVALID; }

int nvn_texflush_layers(const nvn_texdesc *d, int has_layers, int min_layer, int nlayers,
                        uint32_t *first, uint32_t *count, const char **why) {
    if (why) *why = NULL;
    if (!first || !count) return fail(why, "missing layer-range output");
    *first = 0; *count = 0;
    if (!d) return fail(why, "missing texture");
    if (d->target == 2) { *count = 1; return NVN_TEXFLUSH_OK; }   // 3D ignores the view.

    uint32_t layers;
    switch (d->target) {
        case 0: case 1: case 5: case 7: case 10: layers = 1; break;
        case 3: layers = d->height; break;
        case 4: case 6: layers = d->depth; break;
        case 8: layers = 6; break;
        case 9:
            if (d->depth > UINT32_MAX / 6) return fail(why, "cube-array layer count overflows");
            layers = d->depth * 6;
            break;
        default: return fail(why, "unknown texture target");
    }
    if (!layers) return fail(why, "texture has no layers");
    if (!has_layers) { *count = layers; return NVN_TEXFLUSH_OK; }
    if (min_layer < 0 || nlayers < 0) return fail(why, "negative view layer selection");
    if ((uint32_t)min_layer > layers) return fail(why, "view base layer outside the texture");
    uint32_t n = nlayers ? (uint32_t)nlayers : layers - (uint32_t)min_layer;
    if (n > layers - (uint32_t)min_layer) return fail(why, "view layers exceed the texture");
    *first = (uint32_t)min_layer; *count = n;
    return n ? NVN_TEXFLUSH_OK : NVN_TEXFLUSH_EMPTY;
}

static inline uint64_t rotl(uint64_t x, int r) { return x << r | x >> (64 - r); }
static inline uint64_t lane(uint64_t a, uint64_t w) { return rotl(a + w * 0xc2b2ae3d27d4eb4full, 31) * 0x9e3779b97f4a7c15ull; }
uint64_t nvn_texflush_hash(const uint8_t *p, uint64_t n) {
    uint64_t a = 0x60ea27eeadc0b5d6ull ^ n, b = 0xc2b2ae3d27d4eb4full, c = 0x165667b19e3779f9ull, e = 0x27d4eb2f165667c5ull;
    for (; n >= 32; p += 32, n -= 32) {
        uint64_t w[4]; memcpy(w, p, 32);
        a = lane(a, w[0]); b = lane(b, w[1]); c = lane(c, w[2]); e = lane(e, w[3]);
    }
    uint64_t h = rotl(a, 1) + rotl(b, 7) + rotl(c, 12) + rotl(e, 18);
    for (; n >= 8; p += 8, n -= 8) { uint64_t w; memcpy(&w, p, 8); h = lane(h, w); }
    if (n) { uint64_t w = 0; memcpy(&w, p, (size_t)n); h = lane(h ^ n, w); }
    h ^= h >> 33; h *= 0xff51afd7ed558ccdull; h ^= h >> 33; h *= 0xc4ceb9fe1a85ec53ull; h ^= h >> 33;
    return h;
}

typedef struct { uint64_t stamp, hash; uint8_t valid; } layer_cert;
typedef struct {
    const uint8_t *storage;
    uint64_t layer_size;
    uint32_t layers;
    layer_cert cert[];
} rec_t;
static nvn_hmap g_recs = {.klen = 8};
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

static int span_ok(const uint8_t *storage, uint64_t layer_size, uint32_t layers,
                   uint32_t first, uint32_t count) {
    if (!storage || !layer_size || !layers || first > layers || count > layers - first) return 0;
    if (layer_size > SIZE_MAX || layer_size > UINT64_MAX / layers) return 0;
    uint64_t bytes = layer_size * layers;
    return bytes <= UINTPTR_MAX - (uintptr_t)storage;
}
static int same_record(const rec_t *r, const uint8_t *storage, uint64_t layer_size, uint32_t layers) {
    return r && r->storage == storage && r->layer_size == layer_size && r->layers == layers;
}
static rec_t *new_record(const uint8_t *storage, uint64_t layer_size, uint32_t layers) {
    size_t cert_bytes, bytes;
    if (__builtin_mul_overflow((size_t)layers, sizeof(layer_cert), &cert_bytes) ||
        __builtin_add_overflow(sizeof(rec_t), cert_bytes, &bytes)) return NULL;
    rec_t *r = calloc(1, bytes);
    if (!r) return NULL;
    r->storage = storage; r->layer_size = layer_size; r->layers = layers;
    return r;
}
static uint8_t *layer_ptr(const uint8_t *storage, uint64_t layer_size, uint32_t layer) {
    return (uint8_t *)storage + (size_t)((uint64_t)layer * layer_size);
}

int nvn_texflush_note_checked(uint64_t serial, const uint8_t *storage, uint64_t layer_size,
                       uint32_t layers, uint32_t first, uint32_t count, int establish) {
    if (!serial || !span_ok(storage, layer_size, layers, first, count)) return -1;
    layer_cert *next = calloc(count ? count : 1, sizeof *next);
    if (!next) { nvn_texflush_forget(serial); return -1; }

    if (establish) for (uint32_t i = 0; i < count; i++) {
        uint8_t *p = layer_ptr(storage, layer_size, first + i);
        uint64_t stamp = nvn_dirty_now();
        int ok = nvn_dirty_unambiguous(p, (size_t)layer_size) &&
                 nvn_dirty_protect_checked(p, (size_t)layer_size) &&
                 nvn_dirty_unambiguous(p, (size_t)layer_size) &&
                 !nvn_dirty_since(p, (size_t)layer_size, stamp);
        uint64_t hash = ok ? nvn_texflush_hash(p, layer_size) : 0;
        if (ok && (nvn_dirty_since(p, (size_t)layer_size, stamp) ||
                   !nvn_dirty_unambiguous(p, (size_t)layer_size))) ok = 0;
        next[i] = (layer_cert){ok ? stamp : 0, ok ? hash : 0, (uint8_t)ok};
    }

    pthread_mutex_lock(&g_mu);
    rec_t *r = nvn_hm_get(&g_recs, &serial);
    if (!same_record(r, storage, layer_size, layers)) {
        rec_t *nr = new_record(storage, layer_size, layers);
        void *old = NULL;
        if (!nr || nvn_hm_put_checked(&g_recs, &serial, nr, &old)) {
            free(nr); free(next); pthread_mutex_unlock(&g_mu);
            nvn_texflush_forget(serial); return -1;
        }
        free(old);
        r = nr;
    }
    for (uint32_t i = 0; i < count; i++) r->cert[first + i] = next[i];
    pthread_mutex_unlock(&g_mu);
    free(next);
    return 0;
}
void nvn_texflush_note(uint64_t serial, const uint8_t *storage, uint64_t layer_size,
                       uint32_t layers, uint32_t first, uint32_t count, int establish) {
    (void)nvn_texflush_note_checked(serial, storage, layer_size, layers, first, count, establish);
}

int nvn_texflush_verify(uint64_t serial, const uint8_t *storage, uint64_t layer_size,
                        uint32_t layers, uint32_t first, uint32_t count) {
    if (!serial || !span_ok(storage, layer_size, layers, first, count)) return NVN_TEXFLUSH_UNTRACKED;
    layer_cert *saved = malloc((count ? count : 1) * sizeof *saved);
    if (!saved) return NVN_TEXFLUSH_UNTRACKED;
    pthread_mutex_lock(&g_mu);
    rec_t *r = nvn_hm_get(&g_recs, &serial);
    int known = same_record(r, storage, layer_size, layers);
    if (known) for (uint32_t i = 0; i < count; i++) {
        saved[i] = r->cert[first + i];
        if (!saved[i].valid || !saved[i].stamp) known = 0;
    }
    pthread_mutex_unlock(&g_mu);
    if (!known) { free(saved); return NVN_TEXFLUSH_UNTRACKED; }

    int st = NVN_TEXFLUSH_CLEAN;
    for (uint32_t i = 0; i < count; i++) {
        uint8_t *p = layer_ptr(storage, layer_size, first + i);
        if (!nvn_dirty_unambiguous(p, (size_t)layer_size) ||
            nvn_dirty_since(p, (size_t)layer_size, saved[i].stamp) ||
            nvn_texflush_hash(p, layer_size) != saved[i].hash ||
            nvn_dirty_since(p, (size_t)layer_size, saved[i].stamp) ||
            !nvn_dirty_unambiguous(p, (size_t)layer_size)) {
            st = NVN_TEXFLUSH_CHANGED;
            break;
        }
    }
    free(saved);
    return st;
}

void nvn_texflush_forget(uint64_t serial) {
    if (!serial) return;
    pthread_mutex_lock(&g_mu);
    free(nvn_hm_del(&g_recs, &serial));
    pthread_mutex_unlock(&g_mu);
}

size_t nvn_texflush_records(void) {
    pthread_mutex_lock(&g_mu);
    size_t n = g_recs.n;
    pthread_mutex_unlock(&g_mu);
    return n;
}

void nvn_texflush_reset_quiescent(void) {
    pthread_mutex_lock(&g_mu);
    nvn_hm_clear(&g_recs, free);
    free(g_recs.keys); free(g_recs.vals);
    g_recs = (nvn_hmap){.klen = 8};
    pthread_mutex_unlock(&g_mu);
}
