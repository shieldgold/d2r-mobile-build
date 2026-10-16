// CPU texel writes into NVN texture storage (nvnTextureWriteTexels / WriteTexelsStrided).
// Semantics follow static analysis of the BotW 1.9.0 SDK writer (not yet confirmed by a successful runtime write):
// source data is block-row ordered with byte strides (0 = tightly packed: row = columns * bytes per block,
// image = row * rows); x/y address texels of the view's base level; z/depth select layers after the view's base
// layer, or 3D slices; a 1D array (effective target from the view, else the texture) takes its layers from
// yoffset/height and ignores depth; an empty image count, width or height writes nothing. Block counts round up
// (ceil). Host policy beyond the native writer: compressed origins must be block aligned, and pitch-linear,
// multisample, buffer, views whose format changes the block layout and negative strides stop as unsupported.
// Layout comes from nvn_tex.c and intra-level block-linear addressing from tex/nvn_deswizzle.c, so only the
// addressed bytes change; padding, other texels, levels and layers keep their previous contents.
// 3D byte addressing follows that host geometry; native block-depth behavior remains unverified.
#include "nvn_texwrite.h"
#include "../tex/nvn_deswizzle.h"
#include <string.h>

static uint32_t mip(uint32_t s, unsigned l) { s >>= l; return s ? s : 1; }
static uint32_t min_u32(uint32_t a, uint32_t b) { return a < b ? a : b; }
static int fail(const char **why, int code, const char *msg) { if (why) *why = msg; return code; }

// Bound the inputs BEFORE the shared layout helpers, whose uint32 arithmetic is
// not checked internally. Extent/layer/level limits match this host's advertised
// device limits. Packaged block heights above 32 GOBs are outside this write
// implementation's supported layout range, not a claim about native validation.
static int descriptor_ok(const nvn_texdesc *d, int bw, int bh, int bpb, const char **why) {
    uint32_t limit = d->target == 2 ? 2048u : 16384u;
    int one_d = d->target == 0 || d->target == 3;
    if (!d->width || d->width > limit || (!one_d && (!d->height || d->height > limit)))
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "texture dimensions exceed the supported write extent");
    if ((d->target == 2 || d->target == 4 || d->target == 9) && (!d->depth || d->depth > 2048u))
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "texture depth or array count exceeds the supported write extent");
    if (d->target == 3 && (!d->height || d->height > 2048u))
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "1D array count exceeds the supported write extent");
    if (d->samples < 0) return fail(why, NVN_TEXWRITE_INVALID, "negative texture sample count");
    if (d->pkg_block_h < 0 || d->pkg_block_h > 6)
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "packaged block height exceeds the supported write layout");

    uint32_t largest = d->width;
    if (!one_d && d->height > largest) largest = d->height;
    if (d->target == 2 && d->depth > largest) largest = d->depth;
    unsigned chain = 1;
    while (largest >>= 1) chain++;
    unsigned levels = d->levels ? d->levels : 1;
    if (levels > 15 || levels > chain)
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "texture mip count exceeds the supported write chain");

    uint64_t columns = ((uint64_t)d->width + (unsigned)bw - 1) / (unsigned)bw;
    uint64_t rows = one_d ? 1 : ((uint64_t)d->height + (unsigned)bh - 1) / (unsigned)bh;
    uint64_t row_bytes = columns * (unsigned)bpb;
    // Shared helpers add 63 before rounding byte rows to GOBs, and use uint32
    // row counts for block-height selection and padding. Keep those exact sums safe.
    if (row_bytes > UINT32_MAX - 63u || rows + rows / 2 + 255u > UINT32_MAX)
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "texture block geometry exceeds the supported arithmetic range");
    return NVN_TEXWRITE_OK;
}

// Storage bytes occupied by one level of one layer (all slices for 3D), from the same geometry used to address it.
static int level_bytes(uint32_t row_bytes, uint32_t rows, uint32_t slices, unsigned th, unsigned td, uint64_t *out) {
    if (th > 5 || td > 4 || !row_bytes || !rows || !slices) return 0;
    uint64_t gobs_x = ((uint64_t)row_bytes + 63) / 64;
    uint64_t blocks_y = ((uint64_t)rows + (8u << th) - 1) / (8u << th);
    uint64_t blocks_z = ((uint64_t)slices + (1u << td) - 1) >> td;
    uint64_t n;
    unsigned shift = 9 + th + td;
    if (__builtin_mul_overflow(gobs_x, blocks_y, &n) || __builtin_mul_overflow(n, blocks_z, &n) || n > (UINT64_MAX >> shift)) return 0;
    *out = n << shift;
    return 1;
}

int nvn_texwrite_plan_make(const nvn_texdesc *d, unsigned base_level, int min_layer, int view_target, int view_format,
                           const NVNcopyRegion *r, ptrdiff_t row, ptrdiff_t img, nvn_texwrite_plan *p, const char **why) {
    memset(p, 0, sizeof *p);
    if (why) *why = NULL;
    if (!d || !r) return fail(why, NVN_TEXWRITE_INVALID, "missing texture or copy region");
    int bw, bh, bpb = nvn_fmt_bpb(d->format, &bw, &bh);
    if (!bpb) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "texture format has no known block size");
    if (view_format && view_format != d->format) {   //Native keeps the texture's bytes per block but may take block dimensions from the view
        int vbw, vbh, vbpb = nvn_fmt_bpb(view_format, &vbw, &vbh);
        if (vbpb != bpb || vbw != bw || vbh != bh) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "view format block layout differs from the texture format");
    }
    int target = d->target;
    if (d->samples > 1 || target == 5 || target == 6) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "multisample texture write");
    if (target == 10) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "buffer texture write");
    if (target < 0 || target > 9) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "unknown texture target");
    if (d->flags & 0x10) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "pitch-linear texture write");
    if (view_target >= 0 && view_target != target && (view_target == 3 || target == 3))
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "view target changes 1D array addressing");
    int array1d = target == 3;
    int images = array1d ? r->height : r->depth;
    if (images <= 0) return NVN_TEXWRITE_EMPTY;   //Native skips the image loop for zero and negative image counts
    if (r->xoffset < 0 || r->yoffset < 0 || r->zoffset < 0 || r->width < 0 || r->height < 0)
        return fail(why, NVN_TEXWRITE_INVALID, "negative region coordinate or size");
    if (min_layer < 0) return fail(why, NVN_TEXWRITE_INVALID, "negative view layer");
    if (row < 0 || img < 0) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "negative source stride");
    if (!r->width || !r->height) return NVN_TEXWRITE_EMPTY;
    int bounds = descriptor_ok(d, bw, bh, bpb, why);
    if (bounds) return bounds;
    unsigned levels = d->levels ? d->levels : 1;
    if (base_level >= levels) return fail(why, NVN_TEXWRITE_INVALID, "view level outside the texture");

    uint32_t nz = (uint32_t)images, x0 = (uint32_t)r->xoffset, y0 = array1d ? 0 : (uint32_t)r->yoffset;
    uint32_t w = (uint32_t)r->width, h = array1d ? 1 : (uint32_t)r->height, first = array1d ? (uint32_t)r->yoffset : (uint32_t)r->zoffset;
    uint32_t lw = mip(d->width, base_level), lh = target == 0 || array1d ? 1 : mip(d->height, base_level);
    if ((uint64_t)x0 + w > lw || (uint64_t)y0 + h > lh) return fail(why, NVN_TEXWRITE_INVALID, "region exceeds the level extent");
    nvn_texlayout L; nvn_tex_layout(d, &L);
    p->is3d = target == 2;
    if (p->is3d) {
        if (min_layer) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "3D texture view with a layer offset");
        if ((uint64_t)first + nz > mip(d->depth ? d->depth : 1, base_level)) return fail(why, NVN_TEXWRITE_INVALID, "region exceeds the 3D level depth");
    } else if ((uint64_t)min_layer + first + nz > L.layers) return fail(why, NVN_TEXWRITE_INVALID, "region exceeds the texture layers");
    if (x0 % (uint32_t)bw || y0 % (uint32_t)bh) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "compressed region origin is not block aligned");

    uint32_t cols = (w + (uint32_t)bw - 1) / (uint32_t)bw, rows = (h + (uint32_t)bh - 1) / (uint32_t)bh;
    p->level = base_level; p->layer = p->is3d ? 0 : (unsigned)min_layer + first;
    p->x0 = x0; p->y0 = y0; p->z0 = p->is3d ? first : 0; p->depth = nz;
    p->width = min_u32(cols * (uint32_t)bw, lw - x0); p->height = min_u32(rows * (uint32_t)bh, lh - y0);   //Whole written blocks, clipped at the level edge
    p->xb = x0 / (uint32_t)bw * (uint32_t)bpb; p->yb = y0 / (uint32_t)bh;
    p->wb = cols * (uint32_t)bpb; p->hb = rows;

    size_t srow = row ? (size_t)row : p->wb, simg, last_row, total;
    if (__builtin_mul_overflow((size_t)(p->hb - 1), srow, &last_row) || __builtin_add_overflow(last_row, (size_t)p->wb, &last_row))
        return fail(why, NVN_TEXWRITE_INVALID, "source image extent overflows");
    if (img) simg = (size_t)img;
    else if (__builtin_mul_overflow(srow, (size_t)p->hb, &simg)) return fail(why, NVN_TEXWRITE_INVALID, "source image stride overflows");
    if (__builtin_mul_overflow((size_t)(nz - 1), simg, &total) || __builtin_add_overflow(total, last_row, &total))
        return fail(why, NVN_TEXWRITE_INVALID, "source extent overflows");
    p->src_row = srow; p->src_img = simg; p->src_bytes = total;

    uint32_t row_bytes, lrows, slices, ebpb; uint64_t base_lo, base_hi; unsigned th, td;
    if (nvn_deswizzle_geom(d, p->level, p->layer, &row_bytes, &lrows, &slices, &base_lo, &th, &td, &ebpb) ||
        nvn_deswizzle_geom(d, p->level, p->layer + (p->is3d ? 0 : nz - 1), &row_bytes, &lrows, &slices, &base_hi, &th, &td, &ebpb))
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "block-linear geometry unavailable for this texture");
    uint64_t bytes;
    if ((uint64_t)p->xb + p->wb > row_bytes || (uint64_t)p->yb + p->hb > lrows ||
        (p->is3d && (uint64_t)p->z0 + p->depth > slices) || base_lo > base_hi ||
        !level_bytes(row_bytes, lrows, slices, th, td, &bytes) || __builtin_add_overflow(base_hi, bytes, &p->span_hi))
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "write geometry exceeds the supported arithmetic range");
    p->span_lo = base_lo;
    if (p->span_hi > L.size) return fail(why, NVN_TEXWRITE_UNSUPPORTED, "write span exceeds the texture storage size");
    return NVN_TEXWRITE_OK;
}

int nvn_texwrite_plan_level(const nvn_texdesc *d, unsigned level, nvn_texwrite_plan *p, const char **why) {
    memset(p, 0, sizeof *p);
    if (!d) return fail(why, NVN_TEXWRITE_INVALID, "missing texture");
    nvn_texlayout L; nvn_tex_layout(d, &L);
    uint32_t lw = mip(d->width, level), lh = mip(d->height, level), ld = mip(d->depth ? d->depth : 1, level);
    if (lw > INT32_MAX || lh > INT32_MAX || ld > INT32_MAX || L.layers > INT32_MAX)
        return fail(why, NVN_TEXWRITE_UNSUPPORTED, "texture level extent exceeds the copy region range");
    NVNcopyRegion r = {0, 0, 0, (int)lw, (int)lh, (int)L.layers};
    if (d->target == 0) r.height = 1;
    else if (d->target == 3) { r.height = (int)L.layers; r.depth = 1; }   //1D array layers come from height
    else if (d->target == 2) r.depth = (int)ld;
    int st = nvn_texwrite_plan_make(d, level, 0, -1, 0, &r, 0, 0, p, why);
    return st == NVN_TEXWRITE_EMPTY ? fail(why, NVN_TEXWRITE_INVALID, "texture level has no texels") : st;
}

// Copies the planned region between tightly addressed linear rows and block-linear storage in either direction.
static void copy_runs(const nvn_texdesc *d, const nvn_texwrite_plan *p, uint8_t *lin, uint8_t *storage, int to_storage) {
    for (uint32_t z = 0; z < p->depth; z++) {
        uint32_t row_bytes, rows, slices, bpb; uint64_t base; unsigned th, td;
        unsigned layer = p->is3d ? 0 : p->layer + z;
        uint32_t slice = p->is3d ? p->z0 + z : 0;
        if (nvn_deswizzle_geom(d, p->level, layer, &row_bytes, &rows, &slices, &base, &th, &td, &bpb)) return;   //Validated by the plan
        for (uint32_t y = 0; y < p->hb; y++) {
            uint8_t *line = lin + z * p->src_img + y * p->src_row;
            for (uint32_t x = 0; x < p->wb;) {   //Bytes stay contiguous up to the next 16-byte boundary inside a GOB
                uint32_t bx = p->xb + x, n = 16 - bx % 16;
                if (n > p->wb - x) n = p->wb - x;
                uint8_t *at = storage + base + nvn_bl_offset(bx, p->yb + y, slice, row_bytes, rows, th, td);
                if (to_storage) memcpy(at, line + x, n); else memcpy(line + x, at, n);
                x += n;
            }
        }
    }
}

void nvn_texwrite_extract(const nvn_texdesc *d, const nvn_texwrite_plan *p, const uint8_t *storage, void *dst) {
    copy_runs(d, p, dst, (uint8_t *)storage, 0);
}

void nvn_texwrite_apply(const nvn_texdesc *d, const nvn_texwrite_plan *p, const void *src, uint8_t *storage) {
    copy_runs(d, p, (uint8_t *)src, storage, 1);
}
