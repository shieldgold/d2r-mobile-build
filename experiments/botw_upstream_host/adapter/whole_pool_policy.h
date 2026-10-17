/* Pure admission shared by the existing HLE entrypoints and sole pool catalog.
 * Whole storage class 0x100 is one ordinary host kind, not native raw-kind parity. */
#pragma once
#include "nvn_vpool.h"
#include <stdint.h>
typedef enum {
    BOTW_WHOLE_POOL_ORDINARY,
    BOTW_WHOLE_POOL_PHYSICAL,
    BOTW_WHOLE_POOL_VIRTUAL,
    BOTW_WHOLE_POOL_INVALID
} botw_whole_pool_kind;
enum { BOTW_WHOLE_POOL_MAX_MAPPING_REQUESTS=4096,
    BOTW_WHOLE_POOL_MAX_VISIT_PAGES=65536 }; /* Metadata-work bound, not NVN hardware capacity. */
static inline botw_whole_pool_kind botw_whole_pool_kind_of(unsigned flags){
    unsigned kind=flags&0x300u;
    return kind==0?BOTW_WHOLE_POOL_ORDINARY:kind==0x100?BOTW_WHOLE_POOL_PHYSICAL:
        kind==0x200?BOTW_WHOLE_POOL_VIRTUAL:BOTW_WHOLE_POOL_INVALID;
}
static inline int botw_whole_pool_flags_valid(unsigned flags){
    unsigned cpu=flags&7u,gpu=flags&0x38u;
    botw_whole_pool_kind kind=botw_whole_pool_kind_of(flags);
    if((flags&~0x3ffu)||kind==BOTW_WHOLE_POOL_INVALID||
       (cpu&&(cpu&(cpu-1)))||(gpu&&(gpu&(gpu-1))))return 0;
    if(kind==BOTW_WHOLE_POOL_PHYSICAL)return cpu&&gpu==NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS;
    if(kind==BOTW_WHOLE_POOL_VIRTUAL)return cpu==NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS&&gpu;
    return 1;
}
static inline int botw_whole_pool_initialization_valid(unsigned flags,const void *memory,uint64_t size){
    if(!botw_whole_pool_flags_valid(flags)||size>UINTPTR_MAX-(uintptr_t)memory)return 0;
    botw_whole_pool_kind kind=botw_whole_pool_kind_of(flags);
    if(kind==BOTW_WHOLE_POOL_ORDINARY)return !size||((flags&7u)&&(flags&0x38u)&&memory);
    if(!size||(size&(NVN_VPOOL_PAGE-1)))return 0;
    if(kind==BOTW_WHOLE_POOL_PHYSICAL)return memory&&!((uintptr_t)memory&(NVN_VPOOL_PHYS_ALIGN-1))&&
        (flags&7u)&&(flags&0x38u)==NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS;
    return !memory&&(flags&7u)==NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS&&(flags&0x38u);
}
static inline int botw_whole_pool_mapping_request_valid(const NVNmappingRequest *r,
        uint64_t virtual_size,uint64_t physical_size,unsigned physical_flags){
    if(!r||!r->size||((r->size|r->virtualOffset)&(NVN_VPOOL_PAGE-1))||
       r->virtualOffset>virtual_size||r->size>virtual_size-r->virtualOffset)return 0;
    if(!r->physicalPool)return r->physicalOffset==0;
    return botw_whole_pool_kind_of(physical_flags)==BOTW_WHOLE_POOL_PHYSICAL&&
        botw_whole_pool_flags_valid(physical_flags)&&r->storageClass==0x100&&
        !(r->physicalOffset&(NVN_VPOOL_PAGE-1))&&r->physicalOffset<=physical_size&&
        r->size<=physical_size-r->physicalOffset;
}
