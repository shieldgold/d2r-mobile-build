
// NVN texture storage layout (Tegra X1 block-linear), see nvn_tex.c.
#pragma once
#include <stdint.h>

// Packed NVN channel selectors (ZERO=0, ONE=1, R/G/B/A=2..5). Zero-initialized host
// descriptors mean identity; the valid bit distinguishes an explicit all-ZERO mapping.
static inline uint16_t nvn_swizzle_pack(unsigned r, unsigned g, unsigned b, unsigned a) {
    if (r == 2 && g == 3 && b == 4 && a == 5) return 0;
    return (uint16_t)(0x1000u | r | (g << 3) | (b << 6) | (a << 9));
}
static inline unsigned nvn_swizzle_channel(uint16_t packed, unsigned channel) {
    return packed & 0x1000u ? (packed >> (3 * channel)) & 7u : channel + 2;
}
static inline int nvn_swizzle_identity(uint16_t packed) {
    return nvn_swizzle_channel(packed, 0) == 2 && nvn_swizzle_channel(packed, 1) == 3 &&
           nvn_swizzle_channel(packed, 2) == 4 && nvn_swizzle_channel(packed, 3) == 5;
}

typedef struct {
    int target, format, flags, samples;   //NVNtextureTarget / NVNformat / NVNtextureFlags / sample count (0 or 1 = single-sampled)
    uint32_t width, height, depth, levels, stride;
    int pkg_block_h;                      //Byte 0 of the packaged texture layout parameters (log2 of block height) + 1; 0 = use the automatic algorithm
} nvn_texdesc;

typedef struct {
    uint64_t size, layer_size;            //Total storage bytes, bytes per layer
    uint32_t align, layers, bpb;          //Alignment, layer count, bytes per block
    unsigned tileH, tileD;                //Block height / block depth (log2 of the GOB count)
} nvn_texlayout;

void nvn_tex_layout(const nvn_texdesc *d, nvn_texlayout *o);
uint64_t nvn_tex_level_offset(const nvn_texdesc *d, unsigned level);
int nvn_fmt_bpb(int format, int *bw, int *bh);   //Bytes per block (0 = unknown format), block width and height

// Block height / block depth actually used by level `level` (log2 of the GOB count): the per-level block height shrinks with that level's row count (block rows), and deswizzling uses it level by level
void nvn_tex_level_tiles(const nvn_texdesc *d, unsigned level, unsigned *tileH, unsigned *tileD);
