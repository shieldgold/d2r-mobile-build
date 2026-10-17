/* Backing state belongs to the one existing g_pools entry. This module has no
 * object/address catalog. Normal operands require caller-owned lifetimes. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "nvn_vpool.h"
int botw_whole_pool_read(void *destination,const void *source,size_t size);
int botw_whole_pool_write(void *destination,const void *source,size_t size);
int botw_whole_pool_writable(void *destination,size_t size);
int botw_whole_pool_readable(const void *source,size_t size);
/* Callers hold the existing POOL_CAPTURE (or drained POOL_MUTATION) for the
 * entire use of a returned identity. No CPU pointer/lease crosses that scope. */
typedef struct {
    const void *logical_owner,*physical_owner;
    void *canonical;
    uint64_t logical_address,length,physical_offset,physical_id,mapping_generation;
    unsigned storage_class;
} botw_whole_pool_identity;
/* physical_id is the existing immutable catalog birth serial; mapping_generation
 * is the original vpool lease generation. Neither is an address or new catalog. */
int mtl_pool_prepare_typed(const void*,const void*,void*,uint64_t,unsigned,uint64_t,
    int(*)(const void*,size_t),int*,char*,size_t);
uint64_t mtl_pool_buffer_address(const void *owner);
void *mtl_pool_logical_memory(const void *owner);
int mtl_pool_map_virtual(const void*,int,const NVNmappingRequest*,char*,size_t);
int mtl_pool_resolve_identity(uint64_t,uint64_t,botw_whole_pool_identity*,char*,size_t);
int mtl_pool_physical_identity(const void*,uint64_t,uint64_t,botw_whole_pool_identity*,char*,size_t);
int mtl_pool_visit_virtual(const void*,int(*)(const botw_whole_pool_identity*,void*),void*,char*,size_t);
int mtl_pool_read_address(uint64_t,void*,size_t,char*,size_t);
/* Strong consumer hooks, outside g_mu/vpool locks but under POOL_MUTATION.
 * Preflight is read-only; committed retires caches, never authority claims. */
int botw_whole_pool_map_preflight(const void*,const NVNmappingRequest*,int,char*,size_t);
void botw_whole_pool_map_committed(const void*);
#ifdef __OBJC__
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
@interface BotwWholePoolSegment:NSObject
@property void *base;
@property size_t length;
@property(strong) id<MTLBuffer> buffer;
@end
@interface BotwWholePoolBacking:NSObject
@property const void *owner;
@property const void *device;
@property void *memory;
@property size_t size,bufferOffset;
@property unsigned flags;
@property unsigned kind;
@property uint64_t generation;
@property uint64_t gpuBase;
@property BOOL live,retiring,coherent,noCopyDeferred,registered;
@property(strong) id<MTLBuffer> buffer;
@property(strong) NSArray<BotwWholePoolSegment*> *segments;
@property(strong) dispatch_group_t group;
@end
int botw_whole_pool_backing_create(BotwWholePoolBacking *backing,id<MTLDevice> device,
    int(*logical_range)(const void*,size_t),char *error,size_t capacity);
int botw_whole_pool_backing_ensure(BotwWholePoolBacking *backing,id<MTLDevice> device,
    char *error,size_t capacity);
void botw_whole_pool_backing_drop(BotwWholePoolBacking *backing);
int botw_whole_pool_backing_ack(BotwWholePoolBacking *backing,char *error,size_t capacity);
int botw_whole_pool_backing_flush(BotwWholePoolBacking *backing,size_t offset,size_t size,int invalidate,
    char *error,size_t capacity);
#endif
