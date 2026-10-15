/* Pool ABI adapter. Builder setters/query live in graphics.m. Virtual tokens
 * never serve as CPU pointers; only registered physical backing owns bytes. */
#include <stdatomic.h>
@interface BWPoolBorrowedLifetime:NSObject {
@public atomic_bool acknowledged;
}
@property(strong) dispatch_group_t group;
@end
@implementation BWPoolBorrowedLifetime
- (instancetype)init{if((self=[super init])){atomic_init(&acknowledged,false);_group=dispatch_group_create();dispatch_group_enter(_group);}return self;}
@end
static char poolBorrowedLifetimeKey;
/* Pin before any resource creation/registration. Failed unpublished objects are
 * not in the guest registry or pools, but their backing must remain owned until
 * both actual Metal release and vpool unregister have completed. */
static NSMutableArray<BWPool*> *poolUnpublishedPools;
static void poolBorrowedReleased(BWPoolBorrowedLifetime *lifetime){
    if(!atomic_exchange_explicit(&lifetime->acknowledged,true,memory_order_acq_rel))dispatch_group_leave(lifetime.group);
}
static void poolWaitBorrowedRelease(BWPool *p){
    BWPoolBorrowedLifetime *lifetime=objc_getAssociatedObject(p,&poolBorrowedLifetimeKey);
    /* GPU work was drained already. This bounded wait acknowledges actual Metal
     * resource destruction, not command completion. The block never frees bytes. */
    if(lifetime&&dispatch_group_wait(lifetime.group,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC)))
        fatal("borrowed Metal backing still retained; pool quarantined");
}
static id poolGuestObject(const void *raw,Class expected){
    if(raw)for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;
        id provider=objects[key];if(span.location!=(uintptr_t)raw||![provider isKindOfClass:expected])continue;
        uint64_t observed=0;textureReadBytes(raw,&observed,sizeof observed,"pool object header is unreadable");
        if(observed==key.unsignedLongLongValue)return provider;
    }
    fatal("pool object lifetime/type provenance");
}
static BOOL poolSingleVMRegion(void *memory,size_t bytes){
    uintptr_t at=(uintptr_t)memory;if(!memory||!bytes||bytes>UINTPTR_MAX-at)return NO;
    vm_address_t region=at;vm_size_t length=0;natural_t depth=0;vm_region_submap_info_data_64_t info;
    for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;
        if(vm_region_recurse_64(mach_task_self(),&region,&length,&depth,(vm_region_recurse_info_t)&info,&count)!=KERN_SUCCESS||
           region>at||!length||at-region>=length)return NO;
        if(info.is_submap){depth++;region=at;continue;}
        return (info.protection&(VM_PROT_READ|VM_PROT_WRITE))==(VM_PROT_READ|VM_PROT_WRITE)&&bytes<=length-(at-region);
    }
}
static BOOL poolNoCopyGeometry(void *memory,size_t bytes,BOOL strict,void **base,size_t *span,size_t *bias){
    size_t page=(size_t)getpagesize();uintptr_t begin=(uintptr_t)memory;
    if(!memory||!bytes||bytes>UINTPTR_MAX-begin)return NO;
    uintptr_t aligned=begin&~(uintptr_t)(page-1),end=begin+bytes;
    if(end>UINTPTR_MAX-(page-1))return NO;
    size_t length=((end+page-1)&~(uintptr_t)(page-1))-aligned;
    BOOL exact=begin==aligned&&length==bytes;
    if(!exact&&(!memoryValidator||!memoryValidator((void*)aligned,length)))return NO;
    /* Preserve the old ordinary metadata admission: exact NoCopy may be RO/NONE
     * and its actual consumer validates access. New physical backing is strict. */
    if(strict&&(memoryAccessValidator&&!memoryAccessValidator((void*)aligned,length,1)))return NO;
    if(strict&&!poolSingleVMRegion((void*)aligned,length))return NO;
    *base=(void*)aligned;*span=length;*bias=begin-aligned;return YES;
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
    @autoreleasepool{p.buffer=nil;}
    poolWaitBorrowedRelease(p);
    poolRemoveRegistration(p);
    [poolUnpublishedPools removeObjectIdenticalTo:p];
}
static void poolReapUnpublished(BOOL wait){
    for(NSInteger i=(NSInteger)poolUnpublishedPools.count-1;i>=0;i--){BWPool *p=poolUnpublishedPools[(NSUInteger)i];
        if(!p.retiring)continue;
        BWPoolBorrowedLifetime *lifetime=objc_getAssociatedObject(p,&poolBorrowedLifetimeKey);
        if(wait||!lifetime||atomic_load_explicit(&lifetime->acknowledged,memory_order_acquire))poolDiscardUnpublished(p);
    }
}
static void poolRetireHost(BWPool *p){
    p.retiring=YES;
    @autoreleasepool{
        poolDrainGPU();
        if(p.tracked){char detail[256];if(nvn_dirty_remove_range_checked(p.memory,detail,sizeof detail))fatal(detail);p.tracked=NO;}
        poolRetireReferences(p);poolRetireRanges(p);p.buffer=nil;
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
        void *base=NULL;size_t span=0,bias=0;BOOL noCopy=poolNoCopyGeometry(b.memory,b.size,kind==BW_POOL_PHYSICAL,&base,&span,&bias);
        if(noCopy){
            if(!gpu||span>gpu.maxBufferLength)return report("Metal physical buffer span exceeds maxBufferLength");
            BWPoolBorrowedLifetime *lifetime=[BWPoolBorrowedLifetime new];
            objc_setAssociatedObject(p,&poolBorrowedLifetimeKey,lifetime,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
            p.buffer=[gpu newBufferWithBytesNoCopy:base length:span options:MTLResourceStorageModeShared
                deallocator:^(void *address,NSUInteger length){(void)address;(void)length;poolBorrowedReleased(lifetime);}];
            if(!p.buffer)poolBorrowedReleased(lifetime);
            p.bufferOffset=bias;p.coherent=YES;
        }else if(kind==BW_POOL_ORDINARY&&(b.flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED)){
            NSMutableData *snapshot=[NSMutableData dataWithLength:b.size];if(!snapshot)return report("Memory pool CPU snapshot allocation failed");
            textureReadBytes(b.memory,snapshot.mutableBytes,b.size,"Memory pool CPU snapshot is unreadable");
            p.buffer=[gpu newBufferWithBytes:snapshot.bytes length:b.size options:MTLResourceStorageModeShared];
        }else return report("Physical/uncached pool requires coherent host-page-aligned single-VM-region backing");
        if(!p.buffer)return report("Metal memory pool allocation failed");
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
