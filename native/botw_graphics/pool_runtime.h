/* Included after BWPool. All helpers are called under the public graphics lock.
 * A snapshot pins host objects/resources, never a CPU vpool lease. Borrowed bytes
 * still require the family Finalize/drain protocol before the guest may free.
 * Identity/range metadata is immutable; retirement explicitly revokes buffer and
 * canonical binding fields so historical snapshots cannot retain borrowed Metal. */
#pragma once
#include "pool_policy.h"
#include "vendor/botw-vpool/nvn_vpool.h"
@interface BWPoolRange : NSObject
@property(readonly,strong) BWPool *pool,*physical;
@property(readonly,strong) id<MTLBuffer> buffer;
@property(readonly) size_t offset,length,physicalOffset,metalOffset;
@property(readonly) uint64_t logicalAddress,mappingGeneration,physicalID;
@property(readonly) unsigned rawKind;
@property(readonly) void *canonical;
@end
static BWPool *poolAddress(uint64_t address,size_t bytes,size_t *offset);
static BWPoolRange *poolTryResolve(BWPool *pool,size_t offset,size_t bytes,int expectedKind,const char **failure);
static BWPoolRange *poolResolve(BWPool *pool,size_t offset,size_t bytes,int expectedKind,const char *reason);
static BWPoolRange *poolResolveAddress(uint64_t address,size_t bytes,int expectedKind,const char *reason);
static void poolBeginBatch(void);
static void poolSealBatch(void);
static void poolEndBatch(void);
static void poolRetainForGPU(id<MTLCommandBuffer> cb,BWPoolRange *range,BOOL writes);
static void poolPrepareCommit(id<MTLCommandBuffer> cb);
static void poolNoteCommitted(id<MTLCommandBuffer> cb);
static void poolDrainGPU(void);
static void poolShutdownPools(void);
/* Consumer-owner callbacks. They never hold a CPU vpool lease. Mapping guard
 * runs before drain/publish and must reject unsupported private/code authority. */
static void poolPrepareGPUWrite(BWPoolRange *range);
static void poolGuardMappingChange(BWPool *virtualPool,const NVNmappingRequest *requests,int count);
static void poolRetireReferences(BWPool *pool);
/* Defined by the texture family later in the same translation unit. */
static void textureVMAccess(const void *raw,size_t bytes,BOOL writable);
