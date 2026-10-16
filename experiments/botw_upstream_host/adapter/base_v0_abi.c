/* SPDX-License-Identifier: MIT */
#include "base_v0_abi.h"
#include <string.h>

/* Keep the native scalar footprints. A setter must neither read an entire
 * opaque object nor rewrite padding/unrelated fields. memcpy also permits the
 * unaligned storage accepted by the native A64 scalar loads and stores. */
#define OFF(field) offsetof(botw_base_v0_texture_view, field)
#define WRITE(v, field, value) memcpy((unsigned char *)(v) + OFF(field), &(value), sizeof(value))
#define READ(v, field, value) memcpy(&(value), (const unsigned char *)(v) + OFF(field), sizeof(value))
static void set_flag(NVNtextureView *v, unsigned bit) { ((unsigned char *)v)[0] |= (unsigned char)bit; }
static NVNboolean has_flag(const NVNtextureView *v, unsigned bit) { return !!(((const unsigned char *)v)[0] & bit); }

void botw_base_v0_nvnTextureViewSetDefaults(NVNtextureView *v) {
    memset(v, 0, sizeof(botw_base_v0_texture_view));
}
void botw_base_v0_nvnTextureViewSetLevels(NVNtextureView *v, int base, int count) {
    uint8_t lo = (uint8_t)base, n = (uint8_t)count;
    set_flag(v, 1); WRITE(v, base_level, lo); WRITE(v, nlevels, n);
}
void botw_base_v0_nvnTextureViewSetLayers(NVNtextureView *v, int base, int count) {
    uint16_t lo = (uint16_t)base, n = (uint16_t)count;
    set_flag(v, 2); WRITE(v, min_layer, lo); WRITE(v, nlayers, n);
}
void botw_base_v0_nvnTextureViewSetFormat(NVNtextureView *v, NVNformat value) {
    uint32_t raw = (uint32_t)value; set_flag(v, 4); WRITE(v, format, raw);
}
void botw_base_v0_nvnTextureViewSetSwizzle(NVNtextureView *v, NVNtextureSwizzle r, NVNtextureSwizzle g, NVNtextureSwizzle b, NVNtextureSwizzle a) {
    uint32_t first[2] = {(uint32_t)r, (uint32_t)g}, last[2] = {(uint32_t)b, (uint32_t)a};
    memcpy((unsigned char *)v + OFF(swizzle), first, sizeof first);
    set_flag(v, 8);
    memcpy((unsigned char *)v + OFF(swizzle) + sizeof first, last, sizeof last);
}
void botw_base_v0_nvnTextureViewSetDepthStencilMode(NVNtextureView *v, NVNtextureDepthStencilMode value) {
    uint32_t raw = (uint32_t)value; set_flag(v, 16); WRITE(v, depth_stencil_mode, raw);
}
void botw_base_v0_nvnTextureViewSetTarget(NVNtextureView *v, NVNtextureTarget value) {
    uint32_t raw = (uint32_t)value; set_flag(v, 32); WRITE(v, target, raw);
}
NVNboolean botw_base_v0_nvnTextureViewGetLevels(const NVNtextureView *v, int *base, int *count) {
    uint8_t raw; READ(v, base_level, raw); *base = raw;
    READ(v, nlevels, raw); *count = raw; return has_flag(v, 1);
}
NVNboolean botw_base_v0_nvnTextureViewGetLayers(const NVNtextureView *v, int *base, int *count) {
    uint16_t raw; READ(v, min_layer, raw); *base = raw;
    READ(v, nlayers, raw); *count = raw; return has_flag(v, 2);
}
NVNboolean botw_base_v0_nvnTextureViewGetFormat(const NVNtextureView *v, NVNformat *value) {
    _Static_assert(sizeof *value == 4, "native format output width");
    memcpy(value, (const unsigned char *)v + OFF(format), 4); return has_flag(v, 4);
}
NVNboolean botw_base_v0_nvnTextureViewGetSwizzle(const NVNtextureView *v, NVNtextureSwizzle *r, NVNtextureSwizzle *g, NVNtextureSwizzle *b, NVNtextureSwizzle *a) {
    _Static_assert(sizeof *r == 4, "native swizzle output width");
    memcpy(r, (const unsigned char *)v + OFF(swizzle), 4);
    memcpy(g, (const unsigned char *)v + OFF(swizzle) + 4, 4);
    memcpy(b, (const unsigned char *)v + OFF(swizzle) + 8, 4);
    memcpy(a, (const unsigned char *)v + OFF(swizzle) + 12, 4);
    return has_flag(v, 8);
}
NVNboolean botw_base_v0_nvnTextureViewGetDepthStencilMode(const NVNtextureView *v, NVNtextureDepthStencilMode *value) {
    _Static_assert(sizeof *value == 4, "native depth/stencil mode output width");
    memcpy(value, (const unsigned char *)v + OFF(depth_stencil_mode), 4); return has_flag(v, 16);
}
NVNboolean botw_base_v0_nvnTextureViewGetTarget(const NVNtextureView *v, NVNtextureTarget *value) {
    _Static_assert(sizeof *value == 4, "native target output width");
    memcpy(value, (const unsigned char *)v + OFF(target), 4); return has_flag(v, 32);
}
NVNboolean botw_base_v0_nvnTextureViewCompare(const NVNtextureView *a, const NVNtextureView *b) {
    unsigned flags = ((const unsigned char *)a)[0] & 63;
    if (flags != (((const unsigned char *)b)[0] & 63)) return 0;
    const unsigned char *x = (const unsigned char *)a, *y = (const unsigned char *)b;
    if ((flags & 1) && memcmp(x + OFF(base_level), y + OFF(base_level), 2)) return 0;
    if ((flags & 2) && memcmp(x + OFF(min_layer), y + OFF(min_layer), 4)) return 0;
    if ((flags & 4) && memcmp(x + OFF(format), y + OFF(format), 4)) return 0;
    if ((flags & 8) && memcmp(x + OFF(swizzle), y + OFF(swizzle), 16)) return 0;
    if ((flags & 16) && memcmp(x + OFF(depth_stencil_mode), y + OFF(depth_stencil_mode), 4)) return 0;
    return !(flags & 32) || !memcmp(x + OFF(target), y + OFF(target), 4);
}
#undef READ
#undef WRITE
#undef OFF
