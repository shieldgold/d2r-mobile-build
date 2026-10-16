/* Thin base-v0 metadata policy, not a GPU-format or mapping capability.
 * subsdk0 58b6791a: b6900 -> HAL31294/+58. GK100 and GM200 agree on
 * these five valid depth records for raw samples=0 and compression=0.
 * STENCIL8 differs between HALs and is deliberately outside this policy. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <limits.h>
#include "nvn.h"
static inline int bw_texture_depth_storage_format(uint32_t format){
    return format>=NVN_FORMAT_DEPTH16&&format<=NVN_FORMAT_DEPTH32F_STENCIL8;
}
static inline int bw_texture_depth_storage_kind(uint32_t format,uint32_t flags,int32_t samples,unsigned *kind){
    if(!kind||samples!=0||(flags&(NVN_TEXTURE_FLAGS_COMPRESSIBLE|0x50)))return 0;
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
