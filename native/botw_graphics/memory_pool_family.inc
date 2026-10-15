/* Pool ABI adapter. Builder setters/query live in graphics.m. Virtual tokens
 * never serve as CPU pointers; only registered physical backing owns bytes. */
#include <stdatomic.h>
@interface BWPoolBorrowedLifetime:NSObject {
@public atomic_bool acknowledged;
}
@property(strong) dispatch_group_t group;
- (instancetype)initWithGroup:(dispatch_group_t)group;
@end
@implementation BWPoolBorrowedLifetime
- (instancetype)initWithGroup:(dispatch_group_t)group{if((self=[super init])){atomic_init(&acknowledged,false);_group=group;dispatch_group_enter(group);}return self;}
@end
static char poolBorrowedLifetimeKey;
/* The pool owns a shared group; each actual segment has its own once-token.
 * ACK blocks hold only the token/group, never a pool/segment or guest bytes. */
static NSMutableArray<BWPool*> *poolUnpublishedPools;
static void poolBorrowedReleased(BWPoolBorrowedLifetime *lifetime){
    if(!atomic_exchange_explicit(&lifetime->acknowledged,true,memory_order_acq_rel))dispatch_group_leave(lifetime.group);
}
static BOOL poolBorrowedIsReleased(BWPool *p){
    dispatch_group_t group=objc_getAssociatedObject(p,&poolBorrowedLifetimeKey);
    return !group||dispatch_group_wait(group,DISPATCH_TIME_NOW)==0;
}
static void poolWaitBorrowedRelease(BWPool *p){
    dispatch_group_t group=objc_getAssociatedObject(p,&poolBorrowedLifetimeKey);
    /* One bounded deadline for the entire pool, independent of segment count. */
    if(group&&dispatch_group_wait(group,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC)))
        fatal("borrowed Metal backing still retained; pool quarantined");
}
static void poolDropBorrowedBuffers(BWPool *p){
    p.buffer=nil;for(BWPoolSegment *segment in p.segments)segment.buffer=nil;p.segments=nil;
}
static id poolGuestObject(const void *raw,Class expected){
    if(raw)for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;
        id provider=objects[key];if(span.location!=(uintptr_t)raw||![provider isKindOfClass:expected])continue;
        uint64_t observed=0;textureReadBytes(raw,&observed,sizeof observed,"pool object header is unreadable");
        if(observed==key.unsignedLongLongValue)return provider;
    }
    fatal("pool object lifetime/type provenance");
}
typedef struct {
    const char *stage,*logical,*access;
    size_t page,mod,bias,span;BOOL strict,geometryValid,exact;
    unsigned vmQueries,vmFields;natural_t vmDepth;kern_return_t vmResult;
    mach_msg_type_number_t vmCount;vm_prot_t vmProtection;vm_size_t vmRemaining;
} BWPoolNoCopyAdmission;
/* vmFields describes only the last existing query: result=1, containing region=2,
 * full info=4, leaf protection=8, remaining span=16. No address/content is logged.
 * Fields after a short-circuit stay invalid; diagnostics do not issue more calls. */
enum { BW_POOL_MAX_NOCOPY_SEGMENTS=256 };
/* Complete metadata validation precedes all resource creation. Each returned
 * interval lies inside one leaf and is host-page aligned. Adjacent leaves are
 * deliberately not coalesced, even when their current permissions match. */
static NSArray<BWPoolSegment*> *poolVMRegions(void *memory,size_t bytes,BOOL multiple,BOOL requireRW,BWPoolNoCopyAdmission *admission){
    uintptr_t at=(uintptr_t)memory;if(!memory||!bytes||bytes>UINTPTR_MAX-at){admission->stage="vm_input";return nil;}
    uintptr_t end=at+bytes;size_t page=(size_t)getpagesize();NSMutableArray *segments=[NSMutableArray new];
    while(at<end){
        if(segments.count==BW_POOL_MAX_NOCOPY_SEGMENTS){admission->stage="vm_segment_limit";return nil;}
        vm_address_t region=at;vm_size_t length=0;natural_t depth=0;unsigned submapQueries=0;
        for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;vm_region_submap_info_data_64_t info={0};
            admission->vmQueries++;admission->vmFields=1;admission->vmProtection=0;admission->vmRemaining=0;
            kern_return_t result=vm_region_recurse_64(mach_task_self(),&region,&length,&depth,(vm_region_recurse_info_t)&info,&count);
            admission->vmResult=result;admission->vmCount=count;admission->vmDepth=depth;
            if(result!=KERN_SUCCESS){admission->stage="vm_query";return nil;}
            if(count<VM_REGION_SUBMAP_INFO_COUNT_64){admission->stage="vm_info_short";return nil;}admission->vmFields|=4;
            if(region>at){admission->stage="vm_gap";return nil;}
            if(!length){admission->stage="vm_empty";return nil;}
            if(length>UINTPTR_MAX-region){admission->stage="vm_region_overflow";return nil;}
            if(at-region>=length){admission->stage="vm_start";return nil;}admission->vmFields|=2;
            if(info.is_submap){if(++submapQueries>64||depth>=64){admission->stage="vm_submap_limit";return nil;}depth++;region=at;continue;}
            admission->vmProtection=info.protection;admission->vmFields|=8;
            if(requireRW&&(info.protection&(VM_PROT_READ|VM_PROT_WRITE))!=(VM_PROT_READ|VM_PROT_WRITE)){admission->stage="vm_protection";return nil;}
            admission->vmRemaining=length-(at-region);admission->vmFields|=16;
            if(!multiple&&end-at>admission->vmRemaining){admission->stage="vm_span";return nil;}
            size_t take=MIN((size_t)(end-at),(size_t)admission->vmRemaining);
            if(!take||(at&(page-1))||(take&(page-1))){admission->stage="vm_segment_alignment";return nil;}
            BWPoolSegment *segment=[BWPoolSegment new];segment.base=(void*)at;segment.length=take;[segments addObject:segment];at+=take;break;
        }
    }
    return [segments copy];
}
static BOOL poolNoCopyGeometry(void *memory,size_t bytes,BOOL strict,void **base,size_t *span,size_t *bias,NSArray<BWPoolSegment*> **segments,BWPoolNoCopyAdmission *admission){
    size_t page=(size_t)getpagesize();uintptr_t begin=(uintptr_t)memory;
    *admission=(BWPoolNoCopyAdmission){.stage="input",.logical="skipped",.access="skipped",.page=page,.mod=begin&(page-1),.strict=strict};
    if(!memory){admission->stage="null";return NO;}
    if(!bytes){admission->stage="empty";return NO;}
    if(bytes>UINTPTR_MAX-begin){admission->stage="address_overflow";return NO;}
    uintptr_t aligned=begin&~(uintptr_t)(page-1),end=begin+bytes;
    if(end>UINTPTR_MAX-(page-1)){admission->stage="rounding_overflow";return NO;}
    size_t length=((end+page-1)&~(uintptr_t)(page-1))-aligned;
    BOOL exact=begin==aligned&&length==bytes;admission->exact=exact;admission->geometryValid=YES;admission->span=length;admission->bias=begin-aligned;
    if(!exact){
        if(!memoryValidator){admission->logical="missing";admission->stage="range_validator_missing";return NO;}
        if(!memoryValidator((void*)aligned,length)){admission->logical="deny";admission->stage="range_denied";return NO;}
        admission->logical="allow";
    }else admission->logical="exact";
    if(strict){
        if(memoryAccessValidator){
            if(!memoryAccessValidator((void*)aligned,length,1)){admission->access="deny";admission->stage="write_denied";return NO;}
            admission->access="allow";
        }else admission->access="missing";
    }
    NSArray *regions=poolVMRegions((void*)aligned,length,strict,strict,admission);if(!regions)return NO;
    /* Ordinary exact RO/NONE objects retain metadata only. No illegal Metal
     * wrapper is created; true consumers must materialize a legal RW single leaf. */
    if(!strict&&!exact&&(admission->vmProtection&3)!=3){admission->stage="vm_protection";return NO;}
    admission->stage="admitted";*base=(void*)aligned;*span=length;*bias=begin-aligned;*segments=regions;return YES;
}
static BOOL poolCreateNoCopySegments(BWPool *p,NSArray<BWPoolSegment*> *segments,size_t bias){
    dispatch_group_t group=dispatch_group_create();if(!group)return NO;
    objc_setAssociatedObject(p,&poolBorrowedLifetimeKey,group,OBJC_ASSOCIATION_RETAIN_NONATOMIC);p.segments=segments;
    for(BWPoolSegment *segment in segments){
        BWPoolBorrowedLifetime *lifetime=[[BWPoolBorrowedLifetime alloc]initWithGroup:group];
        segment.buffer=[gpu newBufferWithBytesNoCopy:segment.base length:segment.length options:MTLResourceStorageModeShared
            deallocator:^(void *address,NSUInteger length){(void)address;(void)length;poolBorrowedReleased(lifetime);}];
        /* A returned nil created no resource. A thrown exception cannot provide
         * that guarantee, so its outstanding token remains quarantined. */
        if(!segment.buffer){poolBorrowedReleased(lifetime);return NO;}
    }
    p.buffer=segments.count==1?segments.firstObject.buffer:nil;p.bufferOffset=bias;p.coherent=YES;return YES;
}
static BOOL poolEnsureOrdinaryBacking(BWPool *p,const char **failure){
    if(!poolHostLive(p)||p.retiring){if(failure)*failure="ordinary pool lifetime/retiring";return NO;}
    if(!p.noCopyDeferred)return YES;
    BWPoolNoCopyAdmission admission={0};NSArray *segments=poolVMRegions(p.memory,p.size,NO,YES,&admission);
    if(!segments||(memoryAccessValidator&&!memoryAccessValidator(p.memory,p.size,1))){if(failure)*failure="deferred ordinary backing requires accessible RW single-VM-region storage";return NO;}
    BOOL completed=NO;
    @try{completed=poolCreateNoCopySegments(p,segments,0);if(completed)p.noCopyDeferred=NO;}
    @finally{if(!completed){p.retiring=YES;@autoreleasepool{poolDropBorrowedBuffers(p);}poolWaitBorrowedRelease(p);p.retiring=NO;}}
    if(!completed&&failure)*failure="Metal deferred ordinary pool allocation failed";return completed;
}
static int poolReportNoCopyAdmission(const PoolBuilder *builder,BWPoolKind kind,const BWPoolNoCopyAdmission *a){
    return report("Physical/uncached pool requires coherent host-page-aligned single-VM-region backing: stage=%s flags=0x%x kind=%u strict=%d bytes=%zu page=%zu mod=%zu bias=%zu span=%zu geom=%d exact=%d logical=%s access=%s vm_queries=%u vm_fields=0x%x vm_result=%d vm_count=%u vm_depth=%u vm_prot=0x%x vm_remaining=%llu",
        a->stage,(unsigned)builder->flags,(unsigned)kind,a->strict,builder->size,a->page,a->mod,a->bias,a->span,a->geometryValid,a->exact,a->logical,a->access,a->vmQueries,a->vmFields,a->vmResult,a->vmCount,a->vmDepth,(unsigned)a->vmProtection,(unsigned long long)a->vmRemaining);
}
static void poolRemoveRegistration(BWPool *p){
    if(!p.registered)return;
    int status=p.kind==BW_POOL_VIRTUAL?nvn_vpool_virt_finalize((__bridge const void*)p):
        nvn_vpool_phys_finalize((__bridge const void*)p,NULL);
    if(status!=NVN_VPOOL_OK)fatal("pool registry retirement failed; backing quarantined");
    p.registered=NO;
}
static void poolDiscardUnpublished(BWPool *p){
    p.retiring=YES;
    @autoreleasepool{poolDropBorrowedBuffers(p);}
    poolWaitBorrowedRelease(p);
    poolRemoveRegistration(p);
    [poolUnpublishedPools removeObjectIdenticalTo:p];
}
static void poolReapUnpublished(BOOL wait){
    for(NSInteger i=(NSInteger)poolUnpublishedPools.count-1;i>=0;i--){BWPool *p=poolUnpublishedPools[(NSUInteger)i];
        if(!p.retiring)continue;
        if(wait||poolBorrowedIsReleased(p))poolDiscardUnpublished(p);
    }
}
static void poolRetireHost(BWPool *p){
    p.retiring=YES;
    @autoreleasepool{
        poolDrainGPU();
        if(p.tracked){char detail[256];if(nvn_dirty_remove_range_checked(p.memory,detail,sizeof detail))fatal(detail);p.tracked=NO;}
        poolRetireReferences(p);poolRetireRanges(p);poolDropBorrowedBuffers(p);
    }
    poolWaitBorrowedRelease(p);
    poolRemoveRegistration(p);p.live=NO;[pools removeObjectIdenticalTo:p];
}
static void poolShutdownPools(void){
    poolEndBatch();poolDrainGPU();
    while(pools.count)poolRetireHost(pools.lastObject);
    poolReapUnpublished(YES);
}
static void poolObjectWrite(NVNmemoryPool *raw){
    if(memoryAccessValidator&&!memoryAccessValidator(raw,sizeof *raw,1))fatal("Memory pool object is not fully writable");
    texturePrepareWrite(raw,sizeof *raw);
}
NVN(nvnMemoryPoolInitialize,NVNboolean,NVNmemoryPool *raw,const NVNmemoryPoolBuilder *builder){
    PoolBuilder b;textureReadBytes(builder,&b,sizeof b,"Memory pool builder is not fully readable");
    BWDevice *device=poolGuestObject(b.device,[BWDevice class]);
    poolReapUnpublished(NO);
    validateObjectStorage(raw,sizeof *raw);
    for(NSValue *value in objectSpans.allValues){NSRange span=value.rangeValue;
        if(memoryOverlap(raw,sizeof *raw,(void*)span.location,span.length))return report("Memory pool object overlaps a live NVN object");}
    BWPoolKind kind=bw_pool_kind((unsigned)b.flags);BWPoolPolicy policy=bw_pool_policy();
    if(!b.size){poolObjectWrite(raw);unsigned flags=(unsigned)b.flags,cpu=flags&7,gpuFlags=flags&0x38;
        if((flags&~0xffu)||(cpu&&(cpu&(cpu-1)))||(gpuFlags&&(gpuFlags&(gpuFlags-1))))return report("Unsupported/invalid empty memory pool flags");
        BWPool *empty=[BWPool new];empty.kind=BW_POOL_ORDINARY;empty.memory=b.memory;empty.gpuBase=(uintptr_t)b.memory;
        empty.flags=b.flags;empty.device=device;empty.live=YES;put(raw,empty);empty.generation=((BWObject*)raw)->serial;[pools addObject:empty];return 1;
    }
    if(kind==BW_POOL_INVALID)return report("Unsupported/invalid memory pool kind");
    if(kind!=BW_POOL_ORDINARY&&!bw_pool_initialization_valid(&policy,(unsigned)b.flags,b.memory,b.size))return report("physical/virtual memory pool flags/size/backing alignment");
    if(kind==BW_POOL_ORDINARY&&(!b.memory||!gpu||b.size>gpu.maxBufferLength))return report("Unsupported/invalid memory pool backing/size");
    if(kind!=BW_POOL_VIRTUAL){
        if(b.size>UINTPTR_MAX-(uintptr_t)b.memory)return report("Memory pool address overflow");
        if(overlapsQueueArena(b.memory,b.size)||overlapsCommandMemory(b.memory,b.size))return report("Memory pool overlaps live CPU queue arena/recording memory");
        for(BWPool *other in pools)if(other.live&&other.kind!=BW_POOL_VIRTUAL&&other.size&&memoryOverlap(b.memory,b.size,other.memory,other.size))return report("Memory pool ranges overlap");
        for(BWPool *other in poolUnpublishedPools)if(other.kind!=BW_POOL_VIRTUAL&&other.size&&memoryOverlap(b.memory,b.size,other.memory,other.size))return report("Memory pool overlaps unpublished quarantined backing");
    }
    poolObjectWrite(raw);
    BWPool *p=[BWPool new];p.kind=kind;p.memory=kind==BW_POOL_VIRTUAL?NULL:b.memory;
    p.size=b.size;p.flags=b.flags;p.device=device;
    if(!poolUnpublishedPools)poolUnpublishedPools=[NSMutableArray new];
    if(!poolUnpublishedPools)return report("Memory pool unpublished anchor allocation failed");
    [poolUnpublishedPools addObject:p];BOOL published=NO;
    @try{
    if(kind!=BW_POOL_VIRTUAL)@autoreleasepool{
        void *base=NULL;size_t span=0,bias=0;NSArray<BWPoolSegment*> *segments=nil;BWPoolNoCopyAdmission admission;
        BOOL noCopy=poolNoCopyGeometry(b.memory,b.size,kind==BW_POOL_PHYSICAL,&base,&span,&bias,&segments,&admission);
        BOOL ordinaryProtected=noCopy&&kind==BW_POOL_ORDINARY&&(admission.vmProtection&3)!=3;
        if(noCopy&&!ordinaryProtected){
            if(!gpu||span>gpu.maxBufferLength)return report("Metal physical buffer span exceeds maxBufferLength");
            if(!poolCreateNoCopySegments(p,segments,bias))return report("Metal memory pool allocation failed");
        }else if(kind==BW_POOL_ORDINARY&&(b.flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED)&&(!ordinaryProtected||(admission.vmProtection&VM_PROT_READ))){
            NSMutableData *snapshot=[NSMutableData dataWithLength:b.size];if(!snapshot)return report("Memory pool CPU snapshot allocation failed");
            textureReadBytes(b.memory,snapshot.mutableBytes,b.size,"Memory pool CPU snapshot is unreadable");
            p.buffer=[gpu newBufferWithBytes:snapshot.bytes length:b.size options:MTLResourceStorageModeShared];
            if(!p.buffer)return report("Metal memory pool allocation failed");
        }else if(ordinaryProtected&&admission.exact){p.noCopyDeferred=YES;p.coherent=YES;}
        else return poolReportNoCopyAdmission(&b,kind,&admission);
    }
    int status=NVN_VPOOL_OK;
    if(kind==BW_POOL_PHYSICAL)status=nvn_vpool_phys_register((__bridge const void*)p,p.memory,p.size);
    else if(kind==BW_POOL_VIRTUAL){uint64_t token=0;status=nvn_vpool_virt_register_strict((__bridge const void*)p,p.size,&token);p.gpuBase=token;}
    else p.gpuBase=(uintptr_t)p.memory;
    if(status!=NVN_VPOOL_OK)return report("Memory pool registry registration: %s",nvn_vpool_status_name(status));
    p.registered=kind!=BW_POOL_ORDINARY;
    if(kind!=BW_POOL_VIRTUAL&&textureTrackingAvailable){char detail[256];if(nvn_dirty_add_range_checked(p.memory,p.size,detail,sizeof detail)){
            return report("texture memory tracking: %s",detail);}p.tracked=YES;}
    put(raw,p);p.generation=((BWObject*)raw)->serial;p.live=YES;[pools addObject:p];published=YES;
    [poolUnpublishedPools removeObjectIdenticalTo:p];return 1;
    }@finally{if(!published)poolDiscardUnpublished(p);}
}
NVN(nvnMemoryPoolFinalize,void,NVNmemoryPool *raw){
    BWPool *p=poolGuestObject(raw,[BWPool class]);
    if(poolBatchRanges)fatal("pool finalize during submission batch");
    poolObjectWrite(raw);poolRetireHost(p);removeObject(raw);
}
NVN(nvnMemoryPoolMap,void*,const NVNmemoryPool *raw){
    BWPool *p=poolGuestObject(raw,[BWPool class]);
    if(!p.live||p.retiring)fatal("memory pool is finalized/retiring");
    if(!p.size)return p.memory; /* Preserve the old empty ordinary metadata token. */
    return p.kind==BW_POOL_VIRTUAL||(p.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS)?NULL:p.memory;
}
NVN(nvnMemoryPoolGetBufferAddress,NVNbufferAddress,const NVNmemoryPool *raw){
    BWPool *p=poolGuestObject(raw,[BWPool class]);if(!p.live||p.retiring)fatal("memory pool is finalized/retiring");return p.kind==BW_POOL_PHYSICAL?0:p.gpuBase;
}
NVN(nvnMemoryPoolGetSize,size_t,const NVNmemoryPool *raw){BWPool *p=poolGuestObject(raw,[BWPool class]);if(!p.live||p.retiring)fatal("memory pool is finalized/retiring");return p.size;}
NVN(nvnMemoryPoolGetFlags,NVNmemoryPoolFlags,const NVNmemoryPool *raw){BWPool *p=poolGuestObject(raw,[BWPool class]);if(!p.live||p.retiring)fatal("memory pool is finalized/retiring");return (NVNmemoryPoolFlags)p.flags;}
NVN(nvnMemoryPoolFlushMappedRange,void,const NVNmemoryPool *raw,ptrdiff_t off,size_t bytes){
    BWPool *p=poolGuestObject(raw,[BWPool class]);if(!p.live||p.retiring)fatal("memory pool is finalized/retiring");
    if(off<0||!range((size_t)off,bytes,p.size))fatal("nvnMemoryPoolFlushMappedRange bounds");
    if(!bytes||p.kind==BW_POOL_VIRTUAL||(p.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS))return;
    if(memoryAccessValidator&&!memoryAccessValidator((char*)p.memory+off,bytes,0))fatal("CPU cache source is not readable");
    textureVMAccess((char*)p.memory+off,bytes,NO);
    if(!p.coherent)textureReadBytes((char*)p.memory+off,(char*)p.buffer.contents+p.bufferOffset+off,bytes,"CPU cache source copy failed");
}
NVN(nvnMemoryPoolInvalidateMappedRange,void,const NVNmemoryPool *raw,ptrdiff_t off,size_t bytes){
    BWPool *p=poolGuestObject(raw,[BWPool class]);if(!p.live||p.retiring)fatal("memory pool is finalized/retiring");
    if(off<0||!range((size_t)off,bytes,p.size))fatal("nvnMemoryPoolInvalidateMappedRange bounds");
    if(!bytes||p.kind==BW_POOL_VIRTUAL||(p.flags&NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS))return;
    if(memoryAccessValidator&&!memoryAccessValidator((char*)p.memory+off,bytes,1))fatal("CPU cache destination is not writable");
    poolDrainGPU();if(!p.coherent){texturePrepareWrite((char*)p.memory+off,bytes);textureWriteBytes((char*)p.memory+off,(char*)p.buffer.contents+p.bufferOffset+off,bytes,"CPU cache destination copy failed");}
    else textureVMAccess((char*)p.memory+off,bytes,YES);
}
NVN(nvnMemoryPoolMapVirtual,NVNboolean,NVNmemoryPool *raw,int count,const NVNmappingRequest *requests){
    BWPool *v=poolGuestObject(raw,[BWPool class]);if(!poolHostLive(v)||v.retiring||v.kind!=BW_POOL_VIRTUAL||!v.registered)return report("MapVirtual requires live virtual pool");
    if(count<0||(count>0&&!requests))return report("MapVirtual invalid request count/pointer");
    if(poolBatchRanges)return report("MapVirtual during submission batch");
    if(!count)return nvn_vpool_map((__bridge const void*)v,NULL,0,NULL,0)==NVN_VPOOL_OK;
    /* Bridge-owned transaction bound, not an NVN hardware limit. Two 40-byte
     * snapshots stay within 320 KiB and malformed counts cannot force host OOM. */
    enum { BW_POOL_MAX_MAPPING_REQUESTS=4096 };
    if(count>BW_POOL_MAX_MAPPING_REQUESTS)return report("MapVirtual request count exceeds bridge transaction limit (4096)");
    if((size_t)count>SIZE_MAX/sizeof *requests)return report("MapVirtual request byte count overflow");
    size_t bytes=(size_t)count*sizeof *requests;
    if(memoryAccessValidator&&!memoryAccessValidator(requests,bytes,0))fatal("MapVirtual request array is not fully readable");
    textureVMAccess(requests,bytes,NO);
    NSMutableData *snapshot=[NSMutableData dataWithLength:bytes];if(!snapshot)return report("MapVirtual request snapshot allocation failed");
    textureReadBytes(requests,snapshot.mutableBytes,bytes,"MapVirtual request array is not fully readable");
    NSMutableData *converted=[snapshot mutableCopy];if(!converted)return report("MapVirtual native request allocation failed");
    const NVNmappingRequest *original=snapshot.bytes;NVNmappingRequest *native=converted.mutableBytes;BWPoolPolicy policy=bw_pool_policy();
    for(int i=0;i<count;i++){
        BWPool *p=original[i].physicalPool?poolGuestObject(original[i].physicalPool,[BWPool class]):nil;
        if(p&&(!poolHostLive(p)||p.retiring||p.kind!=BW_POOL_PHYSICAL||!p.registered||p.device!=v.device))return report("MapVirtual physical pool lifetime/device");
        if(!bw_pool_mapping_request_valid(&policy,&original[i],v.size,p.size,(unsigned)p.flags))return report("MapVirtual request class/page/bounds/flags");
        native[i].physicalPool=p?(const NVNmemoryPool*)(__bridge const void*)p:NULL;
    }
    poolGuardMappingChange(v,original,count);poolDrainGPU();
    char detail[256];int status=nvn_vpool_map((__bridge const void*)v,native,count,detail,sizeof detail);
    if(status!=NVN_VPOOL_OK)return report("MapVirtual: %s (%s)",detail,nvn_vpool_status_name(status));return 1;
}
