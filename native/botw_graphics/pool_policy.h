/* Base-v0 pool metadata policy. This does not enable physical/virtual pools or
 * make a logical range accessible to a GPU. See MEMORY_POOLS.md for provenance. */
#pragma once
#include "nvn.h"
#include <stddef.h>
#include <stdint.h>

typedef enum {
    BW_POOL_ORDINARY, BW_POOL_PHYSICAL, BW_POOL_VIRTUAL, BW_POOL_INVALID
} BWPoolKind;
typedef struct {
    size_t cpuBackingAlignment, ordinarySizeAlignment, mappingPageBytes;
    uint64_t preferredTokenLimit;
} BWPoolPolicy;
enum { BW_POOL_DEFAULT_FLAGS=0x21, BW_POOL_DEFAULT_WRITE_OFFSET=8,
       BW_POOL_DEFAULT_WRITE_BYTES=20, BW_POOL_CPU_BACKING_ALIGNMENT=4096,
       BW_POOL_ORDINARY_SIZE_ALIGNMENT=4096, BW_POOL_MAPPING_PAGE_BYTES=65536 };
#define BW_POOL_PREFERRED_TOKEN_LIMIT (UINT64_C(1)<<40)

static inline BWPoolPolicy bw_pool_policy(void){
    /* The 40-bit window is a preferred host token policy, not a GPU VA width.
     * None of these values is the runtime's CPU/HIPC Host39 address policy. */
    return (BWPoolPolicy){BW_POOL_CPU_BACKING_ALIGNMENT,BW_POOL_ORDINARY_SIZE_ALIGNMENT,
                          BW_POOL_MAPPING_PAGE_BYTES,BW_POOL_PREFERRED_TOKEN_LIMIT};
}
static inline BWPoolKind bw_pool_kind(unsigned flags){
    switch(flags&(NVN_MEMORY_POOL_FLAGS_PHYSICAL|NVN_MEMORY_POOL_FLAGS_VIRTUAL)){
        case 0:return BW_POOL_ORDINARY;
        case NVN_MEMORY_POOL_FLAGS_PHYSICAL:return BW_POOL_PHYSICAL;
        case NVN_MEMORY_POOL_FLAGS_VIRTUAL:return BW_POOL_VIRTUAL;
        default:return BW_POOL_INVALID;
    }
}
static inline int bw_pool_flags_valid(unsigned flags){
    unsigned cpu=flags&7u,gpuFlags=flags&0x38u;
    BWPoolKind kind=bw_pool_kind(flags);
    if((flags&~0x3ffu)||!cpu||(cpu&(cpu-1))||!gpuFlags||
       (gpuFlags&(gpuFlags-1))||kind==BW_POOL_INVALID)return 0;
    if(kind==BW_POOL_PHYSICAL&&gpuFlags!=NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)return 0;
    if(kind==BW_POOL_VIRTUAL&&cpu!=NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)return 0;
    return 1;
}
static inline int bw_pool_initialization_valid(const BWPoolPolicy *policy,
                                              unsigned flags,const void *memory,size_t size){
    if(!policy||!policy->cpuBackingAlignment||!policy->ordinarySizeAlignment||
       !policy->mappingPageBytes||!size||!bw_pool_flags_valid(flags))return 0;
    BWPoolKind kind=bw_pool_kind(flags);
    size_t page=kind==BW_POOL_ORDINARY?policy->ordinarySizeAlignment:policy->mappingPageBytes;
    if(size%page)return 0;
    if(kind==BW_POOL_VIRTUAL)return memory==NULL;
    return memory&&(uintptr_t)memory%policy->cpuBackingAlignment==0&&
           size<=UINTPTR_MAX-(uintptr_t)memory;
}
static inline int bw_pool_storage_class_kind(NVNstorageClass storageClass,unsigned *kind){
    if(!kind)return 0;
    if(storageClass==0x100){*kind=0;return 1;}
    if(storageClass==0x1fe){*kind=0xfe;return 1;}
    if(storageClass==0x101){*kind=1;return 1;}
    if(storageClass==0x17b){*kind=0x7b;return 1;}
    if(storageClass==0x146){*kind=0x46;return 1;}
    if(storageClass==0x1c3){*kind=0xc3;return 1;}
    return 0;
}
static inline int bw_pool_mapping_request_valid(const BWPoolPolicy *policy,
                                               const NVNmappingRequest *request,
                                               uint64_t virtualSize,uint64_t physicalSize,
                                               unsigned physicalFlags){
    if(!policy||!policy->mappingPageBytes||!request)return 0;
    uint64_t page=policy->mappingPageBytes;
    if(!request->size||request->size%page||request->virtualOffset%page||
       request->virtualOffset>virtualSize||request->size>virtualSize-request->virtualOffset)return 0;
    /* The native unmap branch does not consume storageClass. */
    if(!request->physicalPool)return request->physicalOffset==0;
    unsigned kind;
    return bw_pool_flags_valid(physicalFlags)&&bw_pool_kind(physicalFlags)==BW_POOL_PHYSICAL&&
           bw_pool_storage_class_kind(request->storageClass,&kind)&&
           request->physicalOffset%page==0&&request->physicalOffset<=physicalSize&&
           request->size<=physicalSize-request->physicalOffset;
}
_Static_assert(sizeof(NVNmappingRequest)==40,"base-v0 mapping request size");
_Static_assert(offsetof(NVNmappingRequest,physicalOffset)==8&&
               offsetof(NVNmappingRequest,virtualOffset)==16&&
               offsetof(NVNmappingRequest,size)==24&&
               offsetof(NVNmappingRequest,storageClass)==32,"base-v0 mapping request fields");
