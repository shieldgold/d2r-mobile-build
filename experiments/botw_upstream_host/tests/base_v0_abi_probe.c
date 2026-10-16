/* Owns every object and output. No host/backend startup or GPU operations. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvn_protos.h"
#include "nvn_obj.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "VIEW_CHECK line %d: %s\n", __LINE__, #x); return 91; } } while (0)
static void die(const char *format, ...) { va_list args; va_start(args, format); vfprintf(stderr, format, args); va_end(args); abort(); }
static uint16_t checked_swizzle(NVNtextureSwizzle r, NVNtextureSwizzle g, NVNtextureSwizzle b, NVNtextureSwizzle a) {
    if ((unsigned)r > 5 || (unsigned)g > 5 || (unsigned)b > 5 || (unsigned)a > 5)
        die("invalid NVN texture swizzle (%d,%d,%d,%d)", r, g, b, a);
    return nvn_swizzle_pack(r, g, b, a);
}
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__); \
    static const NVNT_##name chk_##name __attribute__((used)) = h_##name; \
    static ret h_##name(__VA_ARGS__)
#include "view_entries.inc"

static void store32(unsigned char *p, uint32_t value) { memcpy(p, &value, 4); }
static uint32_t load32(const unsigned char *p) { uint32_t value; memcpy(&value, p, 4); return value; }
static int common(const char *mode) {
    _Alignas(8) unsigned char bytes[80], expected[80];
    memset(bytes, 0xa5, sizeof bytes); bytes[8] = 0xc0; memcpy(expected, bytes, sizeof bytes);
    NVNtextureView *v = (NVNtextureView *)(bytes + 8);
    if (!strcmp(mode, "defaults")) {
        memset(expected + 8, 0, 36); h_nvnTextureViewSetDefaults(v);
    } else if (!strcmp(mode, "levels")) {
        expected[8] |= 1; expected[9] = 0x23; expected[10] = 0xfe;
        h_nvnTextureViewSetLevels(v, 0x123, -2);
    } else if (!strcmp(mode, "layers")) {
        expected[8] |= 2; uint16_t a = 0x8123, b = 0xfffe;
        memcpy(expected + 12, &a, 2); memcpy(expected + 14, &b, 2);
        h_nvnTextureViewSetLayers(v, 0x18123, -2); int lo = -1, n = -1;
        CHECK(h_nvnTextureViewGetLayers(v, &lo, &n) == 1);
        CHECK(lo == 0x8123 && n == 0xfffe);
    } else if (!strcmp(mode, "format")) {
        expected[8] |= 4; store32(expected + 16, 0x81234567);
        h_nvnTextureViewSetFormat(v, (NVNformat)0x81234567);
    } else if (!strcmp(mode, "swizzle")) {
        expected[8] |= 8;
        for (unsigned i = 0; i < 4; ++i) store32(expected + 20 + i * 4, i + 2);
        h_nvnTextureViewSetSwizzle(v, NVN_TEXTURE_SWIZZLE_R, NVN_TEXTURE_SWIZZLE_G, NVN_TEXTURE_SWIZZLE_B, NVN_TEXTURE_SWIZZLE_A);
    } else if (!strcmp(mode, "depth")) {
        expected[8] |= 16; store32(expected + 36, 0x81234567);
        h_nvnTextureViewSetDepthStencilMode(v, (NVNtextureDepthStencilMode)0x81234567);
    } else if (!strcmp(mode, "target")) {
        expected[8] |= 32; store32(expected + 40, 0x81234567);
        h_nvnTextureViewSetTarget(v, (NVNtextureTarget)0x81234567);
    } else return 2;
    CHECK(!memcmp(bytes, expected, sizeof bytes)); return 0;
}

#ifdef BOTW_BASE_V0_ADAPTED
static int adapted(const char *mode) {
    _Alignas(8) unsigned char bytes[80]; memset(bytes, 0, sizeof bytes);
    NVNtextureView *v = (NVNtextureView *)(bytes + 8);
    if (!strcmp(mode, "schema")) {
        CHECK(sizeof(NVNtextureView) == 36 && sizeof(view_t) == 36);
        CHECK(offsetof(view_t, base_level) == 1 && offsetof(view_t, min_layer) == 4);
        CHECK(offsetof(view_t, swizzle) == 12 && offsetof(view_t, depth_stencil_mode) == 28 && offsetof(view_t, target) == 32);
        h_nvnTextureViewSetDefaults(v); h_nvnTextureViewSetLevels(v, 2, 3); h_nvnTextureViewSetLayers(v, 32769, 65535);
        h_nvnTextureViewSetFormat(v, NVN_FORMAT_RGBA8); h_nvnTextureViewSetSwizzle(v, 5, 4, 3, 2);
        h_nvnTextureViewSetDepthStencilMode(v, 0x12345678); h_nvnTextureViewSetTarget(v, 0x1234);
        const view_t *view = (const view_t *)v;
        CHECK(view->set_levels && view->set_layers && view->set_format && view->set_swizzle && view->set_depth_stencil && view->set_target);
        CHECK(view->base_level == 2 && view->nlevels == 3 && view->min_layer == 32769 && view->nlayers == 65535);
        CHECK(view->swizzle[0] == 5 && view->swizzle[3] == 2 && view->depth_stencil_mode == 0x12345678 && view->target == 0x1234);
    } else if (!strcmp(mode, "getters")) {
        /* Native getters write payload even when the corresponding bit is unset. */
        memset(bytes + 8, 0x5a, 36); bytes[8] = 0xc0;
        for (unsigned flag = 0; flag < 64; ++flag) {
            bytes[8] = (unsigned char)(0xc0 | flag); int base = -1, n = -1;
            NVNformat format; NVNtextureSwizzle r, g, b, a; NVNtextureDepthStencilMode depth; NVNtextureTarget target;
            CHECK(h_nvnTextureViewGetLevels(v, &base, &n) == !!(flag & 1) && base == 90 && n == 90);
            CHECK(h_nvnTextureViewGetLayers(v, &base, &n) == !!(flag & 2) && base == 0x5a5a && n == 0x5a5a);
            CHECK(h_nvnTextureViewGetFormat(v, &format) == !!(flag & 4) && (uint32_t)format == 0x5a5a5a5a);
            CHECK(h_nvnTextureViewGetSwizzle(v, &r, &g, &b, &a) == !!(flag & 8));
            CHECK((uint32_t)r == 0x5a5a5a5a && r == g && g == b && b == a);
            CHECK(h_nvnTextureViewGetDepthStencilMode(v, &depth) == !!(flag & 16) && (uint32_t)depth == 0x5a5a5a5a);
            CHECK(h_nvnTextureViewGetTarget(v, &target) == !!(flag & 32) && (uint32_t)target == 0x5a5a5a5a);
        }
        h_nvnTextureViewSetSwizzle(v, 0x12345678, 0x23456789, 0x3456789a, 0x456789ab);
        CHECK(load32(bytes + 20) == 0x12345678 && load32(bytes + 32) == 0x456789ab);
    } else if (!strcmp(mode, "compare")) {
        _Alignas(8) unsigned char other[36]; NVNtextureView *w = (NVNtextureView *)other;
        const unsigned starts[] = {1, 4, 8, 12, 28, 32}, sizes[] = {2, 4, 4, 16, 4, 4};
        for (unsigned flags = 0; flags < 64; ++flags) {
            memset(bytes + 8, 0x5a, 36); bytes[8] = flags; memcpy(other, bytes + 8, 36); other[0] |= 0xc0;
            CHECK(h_nvnTextureViewCompare(v, w) && h_nvnTextureViewCompare(w, v));
            other[3] ^= 0xff; CHECK(h_nvnTextureViewCompare(v, w));
            for (unsigned bit = 0; bit < 6; ++bit) {
                other[0] ^= 1u << bit; CHECK(!h_nvnTextureViewCompare(v, w)); other[0] ^= 1u << bit;
                for (unsigned j = 0; j < sizes[bit]; ++j) {
                    unsigned off = starts[bit] + j; other[off] ^= 0xff;
                    CHECK(h_nvnTextureViewCompare(v, w) == !(flags & (1u << bit))); other[off] ^= 0xff;
                }
            }
        }
    } else if (!strcmp(mode, "payload")) {
        h_nvnTextureViewSetDefaults(v); h_nvnTextureViewSetLayers(v, 40000, 2);
        h_nvnTextureViewSetSwizzle(v, 5, 4, 3, 2); h_nvnTextureViewSetTarget(v, 8);
        p_set_rt recorded = {0}; copy_view(&recorded.cview[0], &recorded.has_cview[0], v);
        h_nvnTextureViewSetDefaults(v);
        CHECK(recorded.cview[0].set_layers && recorded.cview[0].min_layer == 40000);
        CHECK(recorded.cview[0].set_swizzle && recorded.cview[0].swizzle[0] == 5 && recorded.cview[0].swizzle[3] == 2);
        CHECK(recorded.cview[0].set_target && recorded.cview[0].target == 8);
        CHECK(sizeof(recorded.cview[0]) == 36);
    } else if (!strcmp(mode, "unaligned")) {
        v = (NVNtextureView *)(bytes + 1); h_nvnTextureViewSetDefaults(v);
        h_nvnTextureViewSetLevels(v, -1, 258); h_nvnTextureViewSetLayers(v, -1, 65538);
        h_nvnTextureViewSetSwizzle(v, 2, 3, 4, 5); int base, n;
        CHECK(h_nvnTextureViewGetLevels(v, &base, &n) && base == 255 && n == 2);
        CHECK(h_nvnTextureViewGetLayers(v, &base, &n) && base == 65535 && n == 2);
        CHECK(bytes[0] == 0 && bytes[37] == 0); /* Bounds remain owned and untouched. */
    } else return 2;
    return 0;
}
#endif

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int result = common(argv[1]);
#ifdef BOTW_BASE_V0_ADAPTED
    if (result == 2) result = adapted(argv[1]);
#endif
    if (!result) printf("BASE_V0_VIEW_OK %s\n", argv[1]);
    return result;
}
