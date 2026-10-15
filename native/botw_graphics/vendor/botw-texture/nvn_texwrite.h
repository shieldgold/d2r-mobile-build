// CPU texel writes into NVN texture storage (nvnTextureWriteTexels / WriteTexelsStrided), see nvn_texwrite.c.
// Pure C: validates a copy region against the texture layout and writes only the addressed bytes of the
// block-linear storage. Metal mirroring and pool lifetime checks belong to the callers.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../../nvn.h"
#include "../nvn/nvn_tex.h"

enum { NVN_TEXWRITE_OK = 0, NVN_TEXWRITE_EMPTY = 1, NVN_TEXWRITE_INVALID = -1, NVN_TEXWRITE_UNSUPPORTED = -2 };

typedef struct {
    unsigned level, layer;              // Destination level and first array layer (0 for 3D)
    uint32_t x0, y0, z0;                // Destination origin in texels / 3D slice (y0 = 0 for 1D arrays)
    uint32_t width, height, depth;      // Texels covered by the written blocks (clipped at the level edge); depth counts layers or 3D slices
    uint32_t xb, yb;                    // Origin in bytes within a block row / in block rows
    uint32_t wb, hb;                    // Bytes per block row of the region / block rows of the region
    int is3d;
    size_t src_row, src_img, src_bytes; // Source strides and the exact source extent that is read
    uint64_t span_lo, span_hi;          // Conservative storage byte span [lo, hi) touched by the write
} nvn_texwrite_plan;

// Builds a write plan. base_level / min_layer / view_target / view_format come from the texture view
// (view_target -1 and view_format 0 = unset). row / img are the caller strides (0 = tightly packed).
// Returns NVN_TEXWRITE_OK, _EMPTY (no image or no texel to write), or a negative status with a static reason in *why.
int nvn_texwrite_plan_make(const nvn_texdesc *d, unsigned base_level, int min_layer, int view_target, int view_format,
                           const NVNcopyRegion *r, ptrdiff_t row, ptrdiff_t img, nvn_texwrite_plan *p, const char **why);

// Writes the planned region from src into storage (the whole texture storage, at least the layout size).
void nvn_texwrite_apply(const nvn_texdesc *d, const nvn_texwrite_plan *p, const void *src, uint8_t *storage);

// Plan covering every texel of one level in every layer (or every 3D slice of that level), tightly packed, through
// the same validation as nvn_texwrite_plan_make. Returns OK or a negative status with a static reason.
int nvn_texwrite_plan_level(const nvn_texdesc *d, unsigned level, nvn_texwrite_plan *p, const char **why);

// Exact inverse of nvn_texwrite_apply: gathers the planned region of storage into dst with the plan's source strides.
void nvn_texwrite_extract(const nvn_texdesc *d, const nvn_texwrite_plan *p, const uint8_t *storage, void *dst);
