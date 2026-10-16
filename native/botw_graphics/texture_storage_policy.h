/* Thin base-v0 metadata policy, not a GPU-format or mapping capability.
 * subsdk0 58b6791a: b6900 -> HAL31294/+58. GK100 and GM200 agree on
 * these five valid depth records for raw samples=0 and compression=0.
 * STENCIL8 differs between HALs and is deliberately outside this policy. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <limits.h>
#include "nvn.h"
/* Original base-v0 valid color records: no depth/stencil, and blockWidth>1
 * or bytesPerBlock<4. Every original provider configuration selects effective
 * compression=0 for these 65 formats, even with request flag COMPRESSIBLE. */
static inline int bw_texture_color_disables_compression(uint32_t format){
    return (format>=0x01&&format<=0x09)||(format>=0x0d&&format<=0x10)||
        (format>=0x39&&format<=0x3c)||(format>=0x41&&format<=0x50)||
        (format>=0x79&&format<=0x98);
}
/* Explicit bridge representation policy, not an original NV configuration
 * word or a physical GPU feature query. Unknown is never a usable policy. */
typedef enum { BW_TEXTURE_KIND_UNKNOWN=0, BW_TEXTURE_KIND_METAL_PLAIN_COLOR=1, BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH=2 } BWTextureKindPolicy;
/* Original 113 valid ordinary-color records, independent of Metal support. */
static inline int bw_texture_ordinary_color_format(uint32_t format){
    return (format>=0x01&&format<=0x18)||(format>=0x22&&format<=0x30)||
        (format>=0x37&&format<=0x50)||(format>=0x6d&&format<=0x9c);
}
/* Only the supported raw single-sample, DISPLAY/IMAGE request layout. */
static inline int bw_texture_color_layout_inputs(uint32_t format,uint32_t flags,int32_t samples){
    return bw_texture_ordinary_color_format(format)&&(flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&samples==0&&
        !(flags&~(uint32_t)(NVN_TEXTURE_FLAGS_COMPRESSIBLE|NVN_TEXTURE_FLAGS_DISPLAY|NVN_TEXTURE_FLAGS_IMAGE));
}
/* Historical provider-independent subset, distinct from backend policy. */
static inline int bw_texture_color_compression_layout(uint32_t format,uint32_t flags,int32_t samples){
    return bw_texture_color_disables_compression(format)&&bw_texture_color_layout_inputs(format,flags,samples);
}
/* The backend cannot transport native NV hardware-compression kinds. Select
 * the original driver's effective-compression=0 color branch while retaining
 * the request. The provider-independent 65-format fact above stays separate. */
static inline int bw_texture_plain_color_selected(BWTextureKindPolicy policy,uint32_t format){
    return (policy==BW_TEXTURE_KIND_METAL_PLAIN_COLOR||policy==BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH)&&bw_texture_ordinary_color_format(format);
}
static inline int bw_texture_plain_color_layout(BWTextureKindPolicy policy,uint32_t format,uint32_t flags,int32_t samples){
    return bw_texture_plain_color_selected(policy,format)&&bw_texture_color_layout_inputs(format,flags,samples);
}
static inline int bw_texture_depth_storage_format(uint32_t format){
    return format>=NVN_FORMAT_DEPTH16&&format<=NVN_FORMAT_DEPTH32F_STENCIL8;
}
/* Original compression=0 depth kind, independently of the bridge policy. */
static inline int bw_texture_plain_depth_kind(uint32_t format,int32_t samples,unsigned *kind){
    if(!kind||samples!=0)return 0;
    unsigned value;
    switch(format){
        case NVN_FORMAT_DEPTH16:value=1;break;
        case NVN_FORMAT_DEPTH24:case NVN_FORMAT_DEPTH24_STENCIL8:value=0x46;break;
        case NVN_FORMAT_DEPTH32F:value=0x7b;break;
        case NVN_FORMAT_DEPTH32F_STENCIL8:value=0xc3;break;
        default:return 0;
    }
    *kind=value;return 1;
}
/* Keep the historical no-COMPRESSIBLE domain distinct from host policy. */
static inline int bw_texture_depth_storage_kind(uint32_t format,uint32_t flags,int32_t samples,unsigned *kind){
    return !(flags&(NVN_TEXTURE_FLAGS_COMPRESSIBLE|0x50))&&bw_texture_plain_depth_kind(format,samples,kind);
}
static inline int bw_texture_plain_depth_selected(BWTextureKindPolicy policy,uint32_t format,uint32_t flags,int32_t samples,unsigned *kind){
    return policy==BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH&&(flags&NVN_TEXTURE_FLAGS_COMPRESSIBLE)&&!(flags&0x50)&&bw_texture_plain_depth_kind(format,samples,kind);
}
/* Original flags 0x100 changes a ChooseKind parameter unused by the plain
 * HAL leaf. Its layout chain does not consume it. VIDEO/SPARSE/MINIMAL and
 * other flags have separate layout semantics and are not admitted here. */
static inline int bw_texture_plain_depth_layout_inputs(uint32_t format,uint32_t flags,int32_t samples){
    return bw_texture_depth_storage_format(format)&&samples==0&&(flags==8||flags==0x108);
}
static inline int bw_texture_plain_depth_gpu_inputs(uint32_t format,uint32_t target,uint32_t flags,int32_t samples){
    return (format==NVN_FORMAT_DEPTH16||format==NVN_FORMAT_DEPTH32F)&&target==NVN_TEXTURE_TARGET_2D&&flags==8&&samples==0;
}
static inline int bw_texture_storage_round_size(size_t payload,size_t alignment,size_t *result){
    if(!result||!alignment||(alignment&(alignment-1))||payload>SIZE_MAX-(alignment-1))return 0;
    *result=(payload+alignment-1)&~(alignment-1);return 1;
}
/* Original initializer stores 64 bits at +78; the public int getter loads W0.
 * Spell out its low-word signed projection without implementation-defined casts. */
static inline int32_t bw_texture_storage_size32(uint64_t size){
    uint32_t low=(uint32_t)size;
    return low<=INT32_MAX?(int32_t)low:INT32_MIN+(int32_t)(low-UINT32_C(0x80000000));
}
