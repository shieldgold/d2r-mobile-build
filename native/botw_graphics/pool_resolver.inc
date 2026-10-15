/* Current-mapping resolver and GPU lifetime bookkeeping. No guest callbacks run
 * under a registry lease; completion handlers never enter graphics/registry locks. */
@interface BWPoolRange ()
- (void)retireBinding;
- (instancetype)initWithPool:(BWPool*)pool physical:(BWPool*)physical offset:(size_t)offset
                     length:(size_t)length physicalOffset:(size_t)physicalOffset
               logicalAddress:(uint64_t)logicalAddress generation:(uint64_t)generation
                   physicalID:(uint64_t)physicalID kind:(unsigned)kind;
@end
@implementation BWPoolRange
- (void)retireBinding{_buffer=nil;_canonical=NULL;}
- (instancetype)initWithPool:(BWPool*)pool physical:(BWPool*)physical offset:(size_t)offset
                     length:(size_t)length physicalOffset:(size_t)physicalOffset
               logicalAddress:(uint64_t)logicalAddress generation:(uint64_t)generation
                   physicalID:(uint64_t)physicalID kind:(unsigned)kind {
    if((self=[super init])){_pool=pool;_physical=physical;_offset=offset;_length=length;
        _physicalOffset=physicalOffset;_metalOffset=physical.bufferOffset+physicalOffset;
        _logicalAddress=logicalAddress;_mappingGeneration=generation;_physicalID=physicalID;
        _rawKind=kind;_buffer=physical.buffer;_canonical=(char*)physical.memory+physicalOffset;}
    return self;
}
@end
static NSHashTable<BWPoolRange*> *poolAllRanges;
static NSMutableDictionary<NSArray*,BWPoolRange*> *poolBatchRanges;
static BOOL poolBatchSealed;
static NSMutableArray<id<MTLCommandBuffer>> *poolGPURecords;
static char poolGPUUsesKey,poolGPUPreparedKey;
static BOOL poolHostLive(BWPool *p){
    return p&&p.live&&p.generation&&objects[@(p.generation)]==p&&
        p.device&&[objects.allValues containsObject:p.device];
}
static BWPool *poolAddress(uint64_t addr,size_t bytes,size_t *offset){
    if(!bytes||bytes>UINT64_MAX-addr)return nil;
    for(BWPool *p in pools){
        if(!poolHostLive(p)||p.retiring||p.kind==BW_POOL_PHYSICAL||p.kind==BW_POOL_INVALID)continue;
        uint64_t base=p.kind==BW_POOL_VIRTUAL?p.gpuBase:(uint64_t)(uintptr_t)p.memory;
        if(addr>=base&&addr-base<=p.size&&bytes<=p.size-(addr-base)){
            if(offset)*offset=(size_t)(addr-base);return p;
        }
    }
    return nil;
}
static BWPoolRange *poolTryResolve(BWPool *p,size_t off,size_t bytes,int expected,const char **failure){
#define POOL_RESOLVE_FAIL(message) do{if(failure)*failure=(message);return nil;}while(0)
    if(failure)*failure=NULL;
    if(!poolHostLive(p)||p.retiring)POOL_RESOLVE_FAIL("pool lifetime/retiring");
    if(!bytes||off>p.size||bytes>p.size-off)POOL_RESOLVE_FAIL("pool range bounds");
    if(expected!=-1&&expected!=0&&expected!=0xfe)POOL_RESOLVE_FAIL("unknown required raw kind");
    if(p.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS)POOL_RESOLVE_FAIL("pool has GPU_NO_ACCESS");
    if(p.kind==BW_POOL_PHYSICAL)POOL_RESOLVE_FAIL("physical pool cannot be consumed directly by GPU");
    BWPool *physical=p;size_t physicalOffset=off;uint64_t gen=p.generation,id=p.generation;
    unsigned kind=0;uint64_t logical=p.kind==BW_POOL_VIRTUAL?p.gpuBase:(uint64_t)(uintptr_t)p.memory;
    if(off>UINT64_MAX-logical)POOL_RESOLVE_FAIL("logical address overflow");logical+=off;
    if(p.kind==BW_POOL_VIRTUAL){
        nvn_vpool_lease lease;int status=nvn_vpool_lease_acquire(&lease,logical,bytes);
        if(status!=NVN_VPOOL_OK){
            switch(status){case NVN_VPOOL_UNMAPPED:POOL_RESOLVE_FAIL("unmapped virtual pool range");
                case NVN_VPOOL_STALE:POOL_RESOLVE_FAIL("stale virtual pool token");
                default:POOL_RESOLVE_FAIL("invalid virtual pool range");}
        }
        @try{
            if(!lease.contiguous)POOL_RESOLVE_FAIL("fragmented GPU range unsupported");
            physical=nil;
            for(BWPool *candidate in pools)if((__bridge const void*)candidate==lease.phys_owner){physical=candidate;break;}
            if(!poolHostLive(physical)||physical.retiring||physical.kind!=BW_POOL_PHYSICAL||
               !physical.registered||physical.device!=p.device)POOL_RESOLVE_FAIL("physical backing lifetime/device");
            uintptr_t base=(uintptr_t)physical.memory,at=(uintptr_t)lease.cpu;
            if(at<base||at-base>physical.size||bytes>physical.size-(at-base))POOL_RESOLVE_FAIL("physical backing range");
            physicalOffset=(size_t)(at-base);gen=lease.gen;id=lease.phys_id;kind=lease.raw_kind;
        }@finally{nvn_vpool_lease_release(&lease);}
    }else if(p.kind!=BW_POOL_ORDINARY)POOL_RESOLVE_FAIL("invalid pool kind");
    if(expected!=-1&&kind!=(unsigned)expected)POOL_RESOLVE_FAIL("mapped raw kind mismatch");
    if(!physical.buffer||!physical.memory)POOL_RESOLVE_FAIL("pool has no Metal backing");
    if(physicalOffset>SIZE_MAX-physical.bufferOffset)POOL_RESOLVE_FAIL("Metal buffer offset overflow");
    size_t metalOffset=physical.bufferOffset+physicalOffset;
    if(metalOffset>physical.buffer.length||bytes>physical.buffer.length-metalOffset)POOL_RESOLVE_FAIL("Metal buffer range bounds");
    if(physicalOffset>UINTPTR_MAX-(uintptr_t)physical.memory)POOL_RESOLVE_FAIL("canonical address overflow");
    BWPoolRange *result=[[BWPoolRange alloc]initWithPool:p physical:physical offset:off length:bytes physicalOffset:physicalOffset
        logicalAddress:logical generation:gen physicalID:id kind:kind];
    if(!poolAllRanges)poolAllRanges=[NSHashTable weakObjectsHashTable];[poolAllRanges addObject:result];return result;
#undef POOL_RESOLVE_FAIL
}
static NSArray *poolRangeKey(BWPool *p,size_t off,size_t bytes,int expected){
    return @[[NSValue valueWithPointer:(__bridge const void*)p],@(p.generation),@(off),@(bytes),@(expected)];
}
static BWPoolRange *poolResolve(BWPool *p,size_t off,size_t bytes,int expected,const char *reason){
    NSArray *key=poolBatchRanges?poolRangeKey(p,off,bytes,expected):nil;
    BWPoolRange *cached=key?poolBatchRanges[key]:nil;
    if(cached)return cached;
    if(poolBatchSealed)fatal("sealed pool batch has no preflight snapshot");
    const char *failure=NULL;BWPoolRange *r=poolTryResolve(p,off,bytes,expected,&failure);
    if(!r){char detail[512];snprintf(detail,sizeof detail,"%s: %s",reason?reason:"pool resolve",failure?failure:"snapshot allocation failed");fatal(detail);}
    if(key)poolBatchRanges[key]=r;return r;
}
static BWPoolRange *poolResolveAddress(uint64_t addr,size_t bytes,int expected,const char *reason){
    size_t off=0;BWPool *p=poolAddress(addr,bytes,&off);
    if(!p){nvn_vpool_lease lease;int st=nvn_vpool_lease_acquire(&lease,addr,bytes);
        if(st==NVN_VPOOL_OK)nvn_vpool_lease_release(&lease);
        if(st==NVN_VPOOL_STALE)fatal("stale virtual pool token");
        fatal(reason?reason:"unknown/physical direct pool address");}
    return poolResolve(p,off,bytes,expected,reason);
}
static void poolPruneCompletedGPU(void){
    NSString *failure=nil;
    for(NSInteger i=(NSInteger)poolGPURecords.count-1;i>=0;i--){id<MTLCommandBuffer> cb=poolGPURecords[(NSUInteger)i];
        if(cb.status<MTLCommandBufferStatusCompleted)continue;
        if(cb.status==MTLCommandBufferStatusError&&!failure)failure=cb.error.localizedDescription?:@"committed GPU error";
        objc_setAssociatedObject(cb,&poolGPUUsesKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);[poolGPURecords removeObjectAtIndex:(NSUInteger)i];
    }
    if(failure){char detail[512];snprintf(detail,sizeof detail,"committed GPU error: %s",failure.UTF8String);fatal(detail);}
}
static void poolRetireRanges(BWPool *p){
    for(BWPoolRange *r in poolAllRanges)if(r.pool==p||r.physical==p)[r retireBinding];
}
static void poolBeginBatch(void){if(poolBatchRanges||poolBatchSealed)fatal("nested pool submission batch");poolBatchRanges=[NSMutableDictionary new];}
static void poolSealBatch(void){if(!poolBatchRanges||poolBatchSealed)fatal("invalid pool submission seal");poolBatchSealed=YES;}
static void poolEndBatch(void){
    poolBatchSealed=NO;poolBatchRanges=nil;
    for(NSInteger i=(NSInteger)poolGPURecords.count-1;i>=0;i--){id<MTLCommandBuffer> cb=poolGPURecords[(NSUInteger)i];
        if(cb.status<MTLCommandBufferStatusCommitted){objc_setAssociatedObject(cb,&poolGPUUsesKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);[poolGPURecords removeObjectAtIndex:(NSUInteger)i];}}
    poolPruneCompletedGPU();
}
static void poolRetainForGPU(id<MTLCommandBuffer> cb,BWPoolRange *r,BOOL writes){
    if(!cb||!r||!r.buffer||!r.canonical||cb.status>=MTLCommandBufferStatusCommitted)fatal("invalid/retired pool GPU capture");
    if(!poolHostLive(r.pool)||!poolHostLive(r.physical)||r.pool.retiring||r.physical.retiring)fatal("pool GPU capture is stale/retiring");
    NSMutableArray *uses=objc_getAssociatedObject(cb,&poolGPUUsesKey);
    if(!uses){uses=[NSMutableArray new];objc_setAssociatedObject(cb,&poolGPUUsesKey,uses,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
    [uses addObject:r];if(writes)poolPrepareGPUWrite(r);
}
static void poolPrepareCommit(id<MTLCommandBuffer> cb){
    if(!cb||cb.status>=MTLCommandBufferStatusCommitted)fatal("invalid pool prepare commit");
    if(objc_getAssociatedObject(cb,&poolGPUPreparedKey))return;
    poolPruneCompletedGPU();
    if(!poolGPURecords)poolGPURecords=[NSMutableArray new];
    [poolGPURecords addObject:cb];objc_setAssociatedObject(cb,&poolGPUPreparedKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done){objc_setAssociatedObject(done,&poolGPUUsesKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}];
}
static void poolNoteCommitted(id<MTLCommandBuffer> cb){
    if(!cb||!objc_getAssociatedObject(cb,&poolGPUPreparedKey)||cb.status<MTLCommandBufferStatusCommitted)fatal("pool commit was not prepared/committed");
}
static void poolDrainGPU(void){
    NSString *failure=nil;
    for(id<MTLCommandBuffer> cb in poolGPURecords)if(cb.status>=MTLCommandBufferStatusCommitted){
        [cb waitUntilCompleted];if(cb.status==MTLCommandBufferStatusError&&!failure)failure=cb.error.localizedDescription?:@"committed GPU error";
    }
    for(id<MTLCommandBuffer> cb in poolGPURecords)objc_setAssociatedObject(cb,&poolGPUUsesKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    [poolGPURecords removeAllObjects];
    if(failure){char detail[512];snprintf(detail,sizeof detail,"committed GPU error: %s",failure.UTF8String);fatal(detail);}
}
