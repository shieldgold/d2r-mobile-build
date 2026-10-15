// nvnTextureFlushTexels helper rules: checked layer selection and dirty-backed coherence
// certificates. All mappings and bytes are synthetic; no game data is used.
#include "../nvn_texflush.h"
#include "../nvn_dirty.h"
#include "test_dirty_port.h"
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>


static int checks, failures;
#define CHECK(x) do { checks++; if (!(x)) { failures++; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)

static nvn_texdesc desc(int target, uint32_t w, uint32_t h, uint32_t depth) {
    nvn_texdesc d = {0};
    d.target = target; d.format = 0x25; d.width = w; d.height = h; d.depth = depth;
    d.levels = 1; d.samples = 1;
    return d;
}
static int sel(const nvn_texdesc *d, int has, int min, int n, uint32_t *first, uint32_t *count) {
    const char *why = (const char *)1;
    int st = nvn_texflush_layers(d, has, min, n, first, count, &why);
    CHECK(st == NVN_TEXFLUSH_INVALID ? why != NULL : why == NULL);
    return st;
}

static void layer_rules(void) {
    uint32_t f, c;
    nvn_texdesc d = desc(1, 4, 4, 1);   // 2D: one layer.
    CHECK(sel(&d, 0, 9, 9, &f, &c) == NVN_TEXFLUSH_OK && f == 0 && c == 1);
    CHECK(sel(&d, 1, 0, 0, &f, &c) == NVN_TEXFLUSH_OK && f == 0 && c == 1);
    CHECK(sel(&d, 1, 1, 0, &f, &c) == NVN_TEXFLUSH_EMPTY && c == 0);
    CHECK(sel(&d, 1, 1, 1, &f, &c) == NVN_TEXFLUSH_INVALID);
    CHECK(sel(&d, 1, -1, 1, &f, &c) == NVN_TEXFLUSH_INVALID);
    CHECK(sel(&d, 1, 0, -1, &f, &c) == NVN_TEXFLUSH_INVALID);

    d = desc(3, 8, 3, 1);   // 1D array takes layers from height.
    d.levels = 33; d.pkg_block_h = 33;
    CHECK(sel(&d, 0, 0, 0, &f, &c) == NVN_TEXFLUSH_OK && c == 3);
    d = desc(4, 4, 4, 4);   // 2D array and multisample array take layers from depth.
    CHECK(sel(&d, 1, 1, 0, &f, &c) == NVN_TEXFLUSH_OK && f == 1 && c == 3);
    CHECK(sel(&d, 1, 1, 2, &f, &c) == NVN_TEXFLUSH_OK && f == 1 && c == 2);
    CHECK(sel(&d, 1, 4, 0, &f, &c) == NVN_TEXFLUSH_EMPTY);
    CHECK(sel(&d, 1, 5, 0, &f, &c) == NVN_TEXFLUSH_INVALID);
    d = desc(6, 4, 4, 7);
    CHECK(sel(&d, 0, 0, 0, &f, &c) == NVN_TEXFLUSH_OK && c == 7);

    d = desc(2, 8, 8, 8);   // 3D always selects one allocation and ignores the view.
    CHECK(sel(&d, 1, -5, -1, &f, &c) == NVN_TEXFLUSH_OK && f == 0 && c == 1);
    d = desc(8, 4, 4, 99);
    CHECK(sel(&d, 0, 0, 0, &f, &c) == NVN_TEXFLUSH_OK && c == 6);
    d = desc(9, 4, 4, 2);
    CHECK(sel(&d, 1, 6, 0, &f, &c) == NVN_TEXFLUSH_OK && f == 6 && c == 6);
    d.depth = UINT32_MAX;
    CHECK(sel(&d, 0, 0, 0, &f, &c) == NVN_TEXFLUSH_INVALID);
    d = desc(4, 4, 4, 0);
    CHECK(sel(&d, 0, 0, 0, &f, &c) == NVN_TEXFLUSH_INVALID);
    d = desc(11, 4, 4, 1);
    CHECK(sel(&d, 0, 0, 0, &f, &c) == NVN_TEXFLUSH_INVALID);
    CHECK(nvn_texflush_layers(NULL, 0, 0, 0, &f, &c, NULL) == NVN_TEXFLUSH_INVALID);
    CHECK(nvn_texflush_layers(&d, 0, 0, 0, NULL, &c, NULL) == NVN_TEXFLUSH_INVALID);
}

static void hash_rules(void) {
    uint8_t b[97];
    for (int i = 0; i < (int)sizeof b; i++) b[i] = (uint8_t)(i * 13 + 1);
    for (unsigned n = 0; n <= sizeof b; n++) {
        uint64_t h = nvn_texflush_hash(b, n);
        CHECK(h == nvn_texflush_hash(b, n));
        if (n < sizeof b) CHECK(h != nvn_texflush_hash(b, n + 1));
        for (unsigned i = 0; i < n; i++) {
            b[i] ^= 0x40; CHECK(h != nvn_texflush_hash(b, n)); b[i] ^= 0x40;
        }
    }
}

static size_t round_pages(size_t n) {
    size_t pg = (size_t)getpagesize();
    return (n + pg - 1) & ~(pg - 1);
}
static uint8_t *map_bytes(size_t n) {
    uint8_t *p = mmap(NULL, round_pages(n), PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (p == MAP_FAILED) { perror("mmap"); exit(2); }
    return p;
}
static void prep_store(uint8_t *p, uint8_t value) {
    dm_guest_write_prepare(p, 1);
    *(volatile uint8_t *)p = value;
}

static void certificate_rules(void) {
    size_t pg = (size_t)getpagesize();
    enum { NL = 3 };
    uint8_t *s = map_bytes(pg * NL);
    for (size_t i = 0; i < pg * NL; i++) s[i] = (uint8_t)i;
    nvn_dirty_add_range(s, pg * NL);
    size_t n0 = nvn_texflush_records();
    uint64_t ser = 0x1234;

    // A fresh record certifies only selected layers. The untouched layer remains unknown.
    s[pg + 17] ^= 1;
    nvn_texflush_note(ser, s, pg, NL, 0, 1, 1);
    CHECK(nvn_texflush_records() == n0 + 1);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 0, 1) == NVN_TEXFLUSH_CLEAN);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 1, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 0, NL) == NVN_TEXFLUSH_UNTRACKED);

    // Hash equality cannot hide a same-value store: the dirty generation is authoritative.
    uint8_t same = s[0];
    prep_store(s, same);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 0, 1) == NVN_TEXFLUSH_CHANGED);
    nvn_texflush_note(ser, s, pg, NL, 0, 1, 1);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 0, 1) == NVN_TEXFLUSH_CLEAN);
    // Hash remains an additional check if write tracking itself is bypassed.
    CHECK(mprotect(s, pg, PROT_READ | PROT_WRITE) == 0);
    s[1] ^= 1;
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 0, 1) == NVN_TEXFLUSH_CHANGED);
    dm_guest_write_prepare(s, 1);
    nvn_texflush_note(ser, s, pg, NL, 0, 1, 1);

    // Caller-declared non-establishing writes invalidate only their selected certificates.
    nvn_texflush_note(ser, s, pg, NL, 1, 1, 1);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 1, 1) == NVN_TEXFLUSH_CLEAN);
    nvn_texflush_note(ser, s, pg, NL, 0, 1, 0);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 1, 1) == NVN_TEXFLUSH_CLEAN);

    // Re-registration invalidates an older valid stamp even when bytes and address are unchanged.
    nvn_dirty_remove_range(s);
    nvn_dirty_add_range(s, pg * NL);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 1, 1) == NVN_TEXFLUSH_CHANGED);
    nvn_texflush_note(ser, s, pg, NL, 1, 1, 1);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 1, 1) == NVN_TEXFLUSH_CLEAN);

    // Geometry and storage pointer are part of record identity.
    uint8_t *other = map_bytes(pg * NL); memset(other, 0x5a, pg * NL);
    nvn_dirty_add_range(other, pg * NL);
    CHECK(nvn_texflush_verify(ser, other, pg, NL, 1, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(ser, s, pg / 2, NL, 1, 1) == NVN_TEXFLUSH_UNTRACKED);
    nvn_texflush_note(ser, other, pg, NL, 2, 1, 1);
    CHECK(nvn_texflush_verify(ser, s, pg, NL, 1, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(ser, other, pg, NL, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(ser, other, pg, NL, 2, 1) == NVN_TEXFLUSH_CLEAN);

    nvn_texflush_forget(ser);
    CHECK(nvn_texflush_records() == n0);
    nvn_dirty_remove_range(other); munmap(other, round_pages(pg * NL));
    nvn_dirty_remove_range(s); munmap(s, round_pages(pg * NL));
}

static void untracked_and_shared(void) {
    size_t pg = (size_t)getpagesize();
    uint8_t *plain = map_bytes(pg); memset(plain, 1, pg);
    nvn_texflush_note(21, plain, pg, 1, 0, 1, 1);
    CHECK(nvn_texflush_verify(21, plain, pg, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
    nvn_texflush_forget(21); munmap(plain, pg);

    // Two registrations sharing one rounded host page share its canonical dirty
    // state, so both remain eligible and observe each other's writes.
    uint8_t *p = map_bytes(pg), *peer = pg >= 8192 ? p + 4096 : p;
    size_t piece = pg >= 8192 ? 4096 : pg;
    uint64_t faults, preps0, preps1;
    nvn_dirty_add_range(p, piece);
    nvn_dirty_add_range(peer, piece);
    CHECK(nvn_dirty_unambiguous(p, piece) && nvn_dirty_unambiguous(peer, piece));
    nvn_texflush_note(22, p, piece, 1, 0, 1, 1);
    CHECK(nvn_texflush_verify(22, p, piece, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
    prep_store(peer + piece - 1, peer[piece - 1]);
    CHECK(nvn_texflush_verify(22, p, piece, 1, 0, 1) == NVN_TEXFLUSH_CHANGED);
    nvn_texflush_note(22, p, piece, 1, 0, 1, 1);
    CHECK(nvn_texflush_verify(22, p, piece, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
    // Retiring the peer must neither invalidate nor unprotect the survivor's page.
    nvn_dirty_remove_range(peer);
    CHECK(nvn_dirty_unambiguous(p, piece));
    CHECK(nvn_texflush_verify(22, p, piece, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
    nvn_dirty_stats(&faults, &preps0);
    prep_store(p, p[0]);
    nvn_dirty_stats(&faults, &preps1);
    CHECK(preps1 == preps0 + 1);
    CHECK(nvn_texflush_verify(22, p, piece, 1, 0, 1) == NVN_TEXFLUSH_CHANGED);
    nvn_texflush_note(22, p, piece, 1, 0, 1, 1);
    nvn_dirty_remove_range(p);
    CHECK(!nvn_dirty_unambiguous(p, piece));
    // An existing certificate cannot survive loss of its last tracking owner.
    CHECK(nvn_texflush_verify(22, p, piece, 1, 0, 1) == NVN_TEXFLUSH_CHANGED);
    nvn_texflush_forget(22); munmap(p, pg);
}

static void same_page_false_positive(void) {
    size_t pg = (size_t)getpagesize();
    if (pg < 8192) return;
    uint8_t *p = map_bytes(pg); memset(p, 3, pg);
    nvn_dirty_add_range(p, pg);
    nvn_texflush_note(31, p, 4096, 1, 0, 1, 1);
    nvn_texflush_note(32, p + 4096, 4096, 1, 0, 1, 1);
    CHECK(nvn_texflush_verify(31, p, 4096, 1, 0, 1) == NVN_TEXFLUSH_CLEAN);
    prep_store(p + 4096, p[4096]);
    CHECK(nvn_texflush_verify(31, p, 4096, 1, 0, 1) == NVN_TEXFLUSH_CHANGED);
    nvn_texflush_forget(31); nvn_texflush_forget(32);
    nvn_dirty_remove_range(p); munmap(p, pg);
}

static void bounds_rules(void) {
    size_t n = nvn_texflush_records();
    uint8_t byte = 0;
    CHECK(nvn_texflush_verify(0, &byte, 1, 1, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(41, &byte, UINT64_MAX, 2, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
    CHECK(nvn_texflush_verify(41, (const uint8_t *)(UINTPTR_MAX - 7), 8, 2, 0, 1) == NVN_TEXFLUSH_UNTRACKED);
    nvn_texflush_note(41, &byte, 1, 1, 1, 1, 1);
    CHECK(nvn_texflush_records() == n);
}

int main(void) {
    test_dirty_install();
    layer_rules();
    hash_rules();
    certificate_rules();
    untracked_and_shared();
    same_page_false_positive();
    bounds_rules();
    printf("TEXFLUSH RULES: %d checks, %d failed\n", checks, failures);
    return failures != 0;
}
