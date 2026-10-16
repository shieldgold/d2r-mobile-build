/* SPDX-License-Identifier: MIT
 * Thin BOTW base-v0 NVN52 TextureView ABI. See base_v0_abi.json for fixed
 * source identity and isolated static-function evidence. No runtime inspection.
 * All HLE and replay consumers must use this same value-copy schema. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "nvn.h"

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error The fixed base-v0 NVN ABI requires a little-endian target
#endif

typedef struct {
    union {
        uint8_t flags;
        struct {
            uint8_t set_levels:1, set_layers:1, set_format:1, set_swizzle:1,
                    set_depth_stencil:1, set_target:1, reserved_flags:2;
        };
    };
    uint8_t base_level, nlevels, reserved;
    uint16_t min_layer, nlayers;
    uint32_t format, swizzle[4], depth_stencil_mode, target;
} botw_base_v0_texture_view;

_Static_assert(sizeof(botw_base_v0_texture_view) == 36, "base-v0 view size");
_Static_assert(offsetof(botw_base_v0_texture_view, flags) == 0 &&
               offsetof(botw_base_v0_texture_view, base_level) == 1 &&
               offsetof(botw_base_v0_texture_view, nlevels) == 2 &&
               offsetof(botw_base_v0_texture_view, min_layer) == 4 &&
               offsetof(botw_base_v0_texture_view, nlayers) == 6 &&
               offsetof(botw_base_v0_texture_view, format) == 8 &&
               offsetof(botw_base_v0_texture_view, swizzle) == 12 &&
               offsetof(botw_base_v0_texture_view, depth_stencil_mode) == 28 &&
               offsetof(botw_base_v0_texture_view, target) == 32, "base-v0 view offsets");

void botw_base_v0_nvnTextureViewSetDefaults(NVNtextureView *v);
void botw_base_v0_nvnTextureViewSetLevels(NVNtextureView *v, int base, int count);
void botw_base_v0_nvnTextureViewSetLayers(NVNtextureView *v, int base, int count);
void botw_base_v0_nvnTextureViewSetFormat(NVNtextureView *v, NVNformat value);
void botw_base_v0_nvnTextureViewSetSwizzle(NVNtextureView *v, NVNtextureSwizzle r, NVNtextureSwizzle g, NVNtextureSwizzle b, NVNtextureSwizzle a);
void botw_base_v0_nvnTextureViewSetDepthStencilMode(NVNtextureView *v, NVNtextureDepthStencilMode value);
void botw_base_v0_nvnTextureViewSetTarget(NVNtextureView *v, NVNtextureTarget value);
NVNboolean botw_base_v0_nvnTextureViewGetLevels(const NVNtextureView *v, int *base, int *count);
NVNboolean botw_base_v0_nvnTextureViewGetLayers(const NVNtextureView *v, int *base, int *count);
NVNboolean botw_base_v0_nvnTextureViewGetFormat(const NVNtextureView *v, NVNformat *value);
NVNboolean botw_base_v0_nvnTextureViewGetSwizzle(const NVNtextureView *v, NVNtextureSwizzle *r, NVNtextureSwizzle *g, NVNtextureSwizzle *b, NVNtextureSwizzle *a);
NVNboolean botw_base_v0_nvnTextureViewGetDepthStencilMode(const NVNtextureView *v, NVNtextureDepthStencilMode *value);
NVNboolean botw_base_v0_nvnTextureViewGetTarget(const NVNtextureView *v, NVNtextureTarget *value);
NVNboolean botw_base_v0_nvnTextureViewCompare(const NVNtextureView *a, const NVNtextureView *b);
