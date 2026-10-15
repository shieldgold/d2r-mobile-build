// Standalone arithmetic regressions for CPU texture writes. Synthetic descriptors
// exercise rejected layouts without reserving their claimed storage sizes.
#include "../nvn_texwrite.h"
#include "../../tex/nvn_deswizzle.h"
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks, failures;
#define CHECK(c) do { checks++; if (!(c)) { failures++; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); } } while (0)
static nvn_texdesc ordinary(void) {
    return (nvn_texdesc){.target = NVN_TEXTURE_TARGET_2D, .format = NVN_FORMAT_RGBA8,
                         .width = 4, .height = 1, .depth = 1, .levels = 1, .samples = 1};
}
static void reject(nvn_texdesc d, unsigned level, NVNcopyRegion region) {
    nvn_texwrite_plan p; const char *why = NULL;
    int st = nvn_texwrite_plan_make(&d, level, 0, -1, 0, &region, 0, 0, &p, &why);
    CHECK(st == NVN_TEXWRITE_INVALID || st == NVN_TEXWRITE_UNSUPPORTED);
    CHECK(why && *why);
}
static int accept(nvn_texdesc d, unsigned level, int layer, NVNcopyRegion region, nvn_texwrite_plan *p) {
    const char *why = NULL;
    int st = nvn_texwrite_plan_make(&d, level, layer, -1, 0, &region, 0, 0, p, &why);
    CHECK(st == NVN_TEXWRITE_OK && why == NULL);
    return st == NVN_TEXWRITE_OK;
}
// Whole-level plans and extraction (fresh-texture import): every plan stays inside exactly-sized heap storage,
// extraction matches the deswizzler layer by layer and is the inverse of apply.
static void level_round_trip(nvn_texdesc d, uint64_t want_size) {
    nvn_texlayout L; nvn_tex_layout(&d, &L);
    if (want_size) CHECK(L.size == want_size);
    uint8_t *storage = malloc((size_t)L.size), *back = malloc((size_t)L.size);
    for (uint64_t i = 0; i < L.size; i++) storage[i] = (uint8_t)(i * 131 + (i >> 9) * 7 + 3);
    memset(back, 0x5C, (size_t)L.size);
    unsigned levels = d.levels ? d.levels : 1;
    for (unsigned l = 0; l < levels; l++) {
        nvn_texwrite_plan p; const char *why = NULL;
        int st = nvn_texwrite_plan_level(&d, l, &p, &why);
        CHECK(st == NVN_TEXWRITE_OK && !why);
        if (st) continue;
        CHECK(p.level == l && p.layer == 0 && !p.x0 && !p.y0 && !p.z0 && p.span_hi <= L.size);
        CHECK(p.src_row == p.wb && p.src_img == (size_t)p.wb * p.hb && p.src_bytes == p.src_img * p.depth);
        uint8_t *lin = malloc(p.src_bytes), *ref = malloc(p.src_img);
        nvn_texwrite_extract(&d, &p, storage, lin);
        unsigned images = p.is3d ? 1 : p.depth;
        for (unsigned y = 0; y < images; y++) {
            uint8_t *one = p.is3d ? malloc(p.src_bytes) : ref;
            CHECK(!nvn_deswizzle(&d, l, y, storage, one, p.wb));
            CHECK(!memcmp(one, lin + (size_t)y * p.src_img, p.is3d ? p.src_bytes : p.src_img));
            if (p.is3d) free(one);
        }
        nvn_texwrite_apply(&d, &p, lin, back);
        uint8_t *again = malloc(p.src_bytes);
        nvn_texwrite_extract(&d, &p, back, again);
        CHECK(!memcmp(again, lin, p.src_bytes));
        free(again); free(ref); free(lin);
    }
    nvn_texwrite_plan p; const char *why = NULL;
    CHECK(nvn_texwrite_plan_level(&d, levels, &p, &why) == NVN_TEXWRITE_INVALID && why);
    free(back); free(storage);
}
static void level_plans(void) {
    // The BotW 1.9.0 runtime shape: R8 3D 32x32x32 with 6 levels, storage 0x15e00
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_3D, .format = NVN_FORMAT_R8, .width = 32, .height = 32, .depth = 32, .levels = 6, .samples = 1}, 0x15e00);
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_2D_ARRAY, .format = NVN_FORMAT_RGBA8, .width = 24, .height = 10, .depth = 3, .levels = 4, .samples = 1}, 0);
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_1D_ARRAY, .format = NVN_FORMAT_RGBA8, .width = 40, .height = 3, .depth = 1, .levels = 3, .samples = 1}, 0);
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_1D, .format = NVN_FORMAT_R8, .width = 100, .height = 1, .depth = 1, .levels = 7, .samples = 1}, 0);
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_CUBEMAP, .format = NVN_FORMAT_RGBA8, .width = 16, .height = 16, .depth = 1, .levels = 5, .samples = 1}, 0);
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_2D, .format = NVN_FORMAT_RGBA_DXT1, .width = 20, .height = 12, .depth = 1, .levels = 5, .samples = 1}, 0);
    level_round_trip((nvn_texdesc){.target = NVN_TEXTURE_TARGET_3D, .format = NVN_FORMAT_RGBA8, .width = 9, .height = 5, .depth = 7, .levels = 4, .samples = 1}, 0);
    nvn_texwrite_plan p; const char *why = NULL;
    nvn_texdesc ms = {.target = NVN_TEXTURE_TARGET_2D_MULTISAMPLE, .format = NVN_FORMAT_RGBA8, .width = 4, .height = 4, .depth = 1, .levels = 1, .samples = 4};
    CHECK(nvn_texwrite_plan_level(&ms, 0, &p, &why) == NVN_TEXWRITE_UNSUPPORTED && why);
    CHECK(nvn_texwrite_plan_level(NULL, 0, &p, &why) == NVN_TEXWRITE_INVALID && why);
}
int main(void) {
    level_plans();
    NVNcopyRegion one = {0, 0, 0, 1, 1, 1};
    nvn_texdesc d = ordinary();
    // Previously: width*4 wrapped to 4, so a 512-byte pool passed the span
    // check while xByte=0x80000000 scattered 16 GiB beyond that allocation.
    d.width = 0x40000001u;
    reject(d, 0, (NVNcopyRegion){0x20000000, 0, 0, 1, 1, 1});
    d = ordinary(); d.levels = 33;
    reject(d, 32, one);                    // Direct uint32 shift by 32
    reject(d, 0, one);                     // Shared helper iterated through mip 32
    d.levels = UINT32_MAX; reject(d, 0, one);
    d = ordinary(); d.width = UINT32_MAX; reject(d, 0, one);
    d = ordinary(); d.height = UINT32_MAX; reject(d, 0, one);
    d = ordinary(); d.width = 16385; reject(d, 0, one);
    d = ordinary(); d.height = 16385; reject(d, 0, one);
    d = ordinary(); d.width = 0; reject(d, 0, one);
    d = ordinary(); d.height = 0; reject(d, 0, one);
    d = ordinary(); d.samples = -1; reject(d, 0, one);
    d = ordinary(); d.levels = 4; reject(d, 0, one); // 4 -> 2 -> 1 is only three levels
    d = ordinary(); reject(d, UINT_MAX, one);

    const int bad_heights[] = {-1, INT_MIN, 7, 29, 30, 32, 33, 256, INT_MAX};
    for (unsigned i = 0; i < sizeof bad_heights / sizeof *bad_heights; i++) {
        d = ordinary(); d.pkg_block_h = bad_heights[i]; reject(d, 0, one);
    }
    d = ordinary(); d.target = NVN_TEXTURE_TARGET_3D; d.depth = UINT32_MAX; reject(d, 0, one);
    d.depth = 2049; reject(d, 0, one);
    d.depth = 1; d.width = 2049; reject(d, 0, one);
    d = ordinary(); d.target = NVN_TEXTURE_TARGET_2D_ARRAY; d.depth = 2049; reject(d, 0, one);
    d.depth = 0; reject(d, 0, one);
    d = ordinary(); d.target = NVN_TEXTURE_TARGET_CUBEMAP_ARRAY; d.depth = UINT32_MAX / 6u + 1; reject(d, 0, one);
    d = ordinary(); d.target = NVN_TEXTURE_TARGET_1D_ARRAY; d.height = UINT32_MAX; reject(d, 0, one);
    d.height = 0; reject(d, 0, one);
    d = ordinary(); d.format = NVN_FORMAT_RGBA_DXT1; d.width = UINT32_MAX; reject(d, 0, one);
    d = ordinary(); d.format = NVN_FORMAT_RGBA32F; d.width = UINT32_MAX / 16u + 1; reject(d, 0, one);

    nvn_texwrite_plan p;
    // Maximum supported descriptors must remain arithmetically valid, without
    // actually allocating their potentially large texture storage.
    d = ordinary(); d.width = d.height = 16384; d.levels = 15;
    if (accept(d, 14, 0, one, &p)) CHECK(p.src_bytes == 4 && p.span_hi > p.span_lo);
    d.target = NVN_TEXTURE_TARGET_2D_ARRAY; d.depth = 2048;
    if (accept(d, 14, 2047, one, &p)) CHECK(p.layer == 2047 && p.src_bytes == 4);
    d = ordinary(); d.target = NVN_TEXTURE_TARGET_3D; d.width = d.height = d.depth = 2048; d.levels = 12;
    if (accept(d, 11, 0, one, &p)) CHECK(p.is3d && p.src_bytes == 4);
    d = ordinary(); d.target = NVN_TEXTURE_TARGET_1D_ARRAY; d.width = 16384; d.height = 2048; d.levels = 15;
    if (accept(d, 14, 2047, one, &p)) CHECK(p.layer == 2047 && p.height == 1);
    d = ordinary(); d.height = 512; d.pkg_block_h = 6;
    if (accept(d, 0, 0, one, &p)) CHECK(p.span_hi > p.span_lo);
    d = ordinary(); d.levels = 0;
    if (accept(d, 0, 0, one, &p)) CHECK(p.level == 0);

    // Preserve native empty-image behavior without entering layout arithmetic.
    d = ordinary(); d.levels = UINT32_MAX; d.pkg_block_h = INT_MAX;
    const char *why = NULL; NVNcopyRegion empty = {0, 0, 0, 4, 1, 0};
    CHECK(nvn_texwrite_plan_make(&d, 0, 0, -1, 0, &empty, 0, 0, &p, &why) == NVN_TEXWRITE_EMPTY);

    // Positive control: the reached 4x1 RGBA8 write touches exactly its 16
    // contiguous bytes; every padding byte and both allocation canaries survive.
    d = ordinary(); uint8_t allocation[576], src[16];
    memset(allocation, 0xA5, sizeof allocation);
    for (unsigned i = 0; i < sizeof src; i++) src[i] = (uint8_t)(17 + i);
    NVNcopyRegion row = {0, 0, 0, 4, 1, 1};
    if (accept(d, 0, 0, row, &p)) {
        CHECK(p.src_bytes == sizeof src && p.span_lo == 0 && p.span_hi == 512);
        nvn_texwrite_apply(&d, &p, src, allocation + 32);
        CHECK(!memcmp(allocation + 32, src, sizeof src));
        unsigned bad = 0;
        for (unsigned i = 0; i < sizeof allocation; i++) if ((i < 32 || i >= 48) && allocation[i] != 0xA5) bad++;
        CHECK(!bad);
    }
    printf("TEXWRITE BOUNDS: %u checks, %u failed\n", checks, failures);
    return failures != 0;
}
