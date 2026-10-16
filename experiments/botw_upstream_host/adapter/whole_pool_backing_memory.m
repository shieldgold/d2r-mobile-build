/* Normal backing admission/lifetime port. VM leaf/geometry and once-token
 * algorithms come from native/botw_graphics/memory_pool_family.inc. Only
 * explicit operands are queried; no VM enumeration or diagnostic capture. */
#include "whole_pool_backing_memory.h"
#include "../../../native/botw_host/protected_copy.h"
#include <stdatomic.h>
#include <stdio.h>
int dm_guest_write_prepare_checked(void*,size_t);
@implementation BotwWholePoolSegment @end
@implementation BotwWholePoolBacking @end
int botw_whole_pool_read(void *d,const void *s,size_t n){return botw_protected_vm_read(d,s,n)==KERN_SUCCESS;}
int botw_whole_pool_write(void *d,const void *s,size_t n){return dm_guest_write_prepare_checked(d,n)&&botw_protected_vm_write(d,s,n)==KERN_SUCCESS;}
int botw_whole_pool_writable(void *d,size_t n){return dm_guest_write_prepare_checked(d,n)&&botw_protected_vm_span(d,n,VM_PROT_WRITE)==KERN_SUCCESS;}
int botw_whole_pool_readable(const void *s,size_t n){return botw_protected_vm_span(s,n,VM_PROT_READ)==KERN_SUCCESS;}
static int failed(char *error,size_t cap,const char *message){if(error&&cap)snprintf(error,cap,"ordinary pool: %s",message);return 0;}
@interface BotwWholePoolBorrowedLifetime:NSObject { @public atomic_bool acknowledged; }
@property(strong) dispatch_group_t group;
- (instancetype)initWithGroup:(dispatch_group_t)group;
@end
@implementation BotwWholePoolBorrowedLifetime
- (instancetype)initWithGroup:(dispatch_group_t)group{if((self=[super init])){atomic_init(&acknowledged,false);_group=group;dispatch_group_enter(group);}return self;}
@end
static void poolBorrowedReleased(BotwWholePoolBorrowedLifetime *lifetime){if(!atomic_exchange_explicit(&lifetime->acknowledged,true,memory_order_acq_rel))dispatch_group_leave(lifetime.group);}
typedef struct {
    const char *stage,*logical,*access;
    size_t page,mod,bias,span;BOOL strict,geometryValid,exact;
    unsigned vmQueries,vmFields;natural_t vmDepth;kern_return_t vmResult;
    mach_msg_type_number_t vmCount;vm_prot_t vmProtection;vm_size_t vmRemaining;
} BotwWholePoolNoCopyAdmission;
/* vmFields describes only the last existing query: result=1, containing region=2,
 * full info=4, leaf protection=8, remaining span=16. No address/content is logged.
 * Fields after a short-circuit stay invalid; diagnostics do not issue more calls. */
enum { BOTW_WHOLE_POOL_MAX_NOCOPY_SEGMENTS=256 };
/* Complete metadata validation precedes all resource creation. Each returned
 * interval lies inside one leaf and is host-page aligned. Adjacent leaves are
 * deliberately not coalesced, even when their current permissions match. */
static NSArray<BotwWholePoolSegment*> *poolVMRegions(void *memory,size_t bytes,BOOL multiple,BOOL requireRW,BotwWholePoolNoCopyAdmission *admission){
    uintptr_t at=(uintptr_t)memory;if(!memory||!bytes||bytes>UINTPTR_MAX-at){admission->stage="vm_input";return nil;}
    uintptr_t end=at+bytes;size_t page=(size_t)getpagesize();NSMutableArray *segments=[NSMutableArray new];
    while(at<end){
        if(segments.count==BOTW_WHOLE_POOL_MAX_NOCOPY_SEGMENTS){admission->stage="vm_segment_limit";return nil;}
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
            BotwWholePoolSegment *segment=[BotwWholePoolSegment new];segment.base=(void*)at;segment.length=take;[segments addObject:segment];at+=take;break;
        }
    }
    return [segments copy];
}
static BOOL poolNoCopyGeometry(int(*memoryValidator)(const void*,size_t),int(*memoryAccessValidator)(const void*,size_t,int),void *memory,size_t bytes,BOOL strict,void **base,size_t *span,size_t *bias,NSArray<BotwWholePoolSegment*> **segments,BotwWholePoolNoCopyAdmission *admission){
    size_t page=(size_t)getpagesize();uintptr_t begin=(uintptr_t)memory;
    *admission=(BotwWholePoolNoCopyAdmission){.stage="input",.logical="skipped",.access="skipped",.page=page,.mod=begin&(page-1),.strict=strict};
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
/* Same once-token and common group/deadline as the complete native provider.
 * A thrown factory call retains an unknown token; a nil return ACKs only that
 * call's absent resource. The caller keeps the one registry entry quarantined. */
static BOOL poolCreateNoCopySegments(BotwWholePoolBacking *p,id<MTLDevice> gpu,
        NSArray<BotwWholePoolSegment*> *segments,size_t bias){
    dispatch_group_t group=dispatch_group_create();if(!group)return NO;
    p.group=group;p.segments=segments;
    for(BotwWholePoolSegment *segment in segments){
        BotwWholePoolBorrowedLifetime *lifetime=[[BotwWholePoolBorrowedLifetime alloc]initWithGroup:group];
        if(!lifetime)return NO;
        segment.buffer=[gpu newBufferWithBytesNoCopy:segment.base length:segment.length options:MTLResourceStorageModeShared
            deallocator:^(void *address,NSUInteger length){(void)address;(void)length;poolBorrowedReleased(lifetime);}];
        if(!segment.buffer){poolBorrowedReleased(lifetime);return NO;}
    }
    p.buffer=segments.count==1?segments.firstObject.buffer:nil;p.bufferOffset=bias;p.coherent=YES;return YES;
}
void botw_whole_pool_backing_drop(BotwWholePoolBacking *p){
    p.buffer=nil;for(BotwWholePoolSegment *segment in p.segments)segment.buffer=nil;p.segments=nil;
}
int botw_whole_pool_backing_ack(BotwWholePoolBacking *p,char *error,size_t capacity){
    /* One deadline for the entire pool, independent of segment count. */
    if(p.group&&dispatch_group_wait(p.group,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC)))
        return failed(error,capacity,"borrowed backing still retained; entry quarantined");
    return 1;
}
int botw_whole_pool_backing_create(BotwWholePoolBacking *p,id<MTLDevice> gpu,
        int(*logical_range)(const void*,size_t),char *error,size_t capacity){
    if(error&&capacity)error[0]=0;
    if(!p||!gpu)return failed(error,capacity,"missing backing/device");
    unsigned cpu=p.flags&7,gpu_flags=p.flags&0x38;
    if((p.flags&~0xffu)||(cpu&&(cpu&(cpu-1)))||(gpu_flags&&(gpu_flags&(gpu_flags-1))))
        return failed(error,capacity,"unsupported or conflicting ordinary flags");
    if(!p.size)return 1;
    if(!cpu||!gpu_flags||!p.memory||p.size>UINTPTR_MAX-(uintptr_t)p.memory)
        return failed(error,capacity,"invalid ordinary backing/flags/range");
    if(!logical_range||!logical_range(p.memory,p.size))
        return failed(error,capacity,"backing is outside normal allocator/module ownership");
    if(p.size>gpu.maxBufferLength)return failed(error,capacity,"backing exceeds maxBufferLength");
    void *base=NULL;size_t span=0,bias=0;NSArray<BotwWholePoolSegment*> *segments=nil;
    BotwWholePoolNoCopyAdmission admission;
    BOOL noCopy=poolNoCopyGeometry(logical_range,NULL,p.memory,p.size,NO,&base,&span,&bias,&segments,&admission);
    BOOL ordinaryProtected=noCopy&&(admission.vmProtection&3)!=3;
    @try{
        if(noCopy&&!ordinaryProtected){
            if(span>gpu.maxBufferLength)return failed(error,capacity,"rounded span exceeds maxBufferLength");
            if(!poolCreateNoCopySegments(p,gpu,segments,bias))return failed(error,capacity,"NoCopy factory failed");
        }else if((p.flags&4)&&(!ordinaryProtected||(admission.vmProtection&VM_PROT_READ))){
            /* The full snapshot fallback is conditional on CPU_CACHED and
             * actual readability; it is never used for all pool creations. */
            if(!botw_whole_pool_readable(p.memory,p.size))return failed(error,capacity,"cached backing is not fully readable");
            NSMutableData *snapshot=[NSMutableData dataWithLength:p.size];
            if(!snapshot||!botw_whole_pool_read(snapshot.mutableBytes,p.memory,p.size))
                return failed(error,capacity,"cached snapshot is not fully readable");
            p.buffer=[gpu newBufferWithBytes:snapshot.bytes length:p.size options:MTLResourceStorageModeShared];
            if(!p.buffer)return failed(error,capacity,"snapshot-copy factory failed");
        }else if(ordinaryProtected&&admission.exact){p.noCopyDeferred=YES;p.coherent=YES;}
        else return failed(error,capacity,admission.stage);
    }@catch(NSException *exception){(void)exception;return failed(error,capacity,"factory raised; borrowed token retained until actual ACK");}
    return 1;
}
int botw_whole_pool_backing_ensure(BotwWholePoolBacking *p,id<MTLDevice> gpu,char *error,size_t capacity){
    @synchronized(p){
        if(!p.live||p.retiring)return failed(error,capacity,"backing is unpublished/retiring");
        if(!p.noCopyDeferred)return p.buffer!=nil;
        BotwWholePoolNoCopyAdmission admission={0};
        NSArray<BotwWholePoolSegment*> *segments=poolVMRegions(p.memory,p.size,NO,YES,&admission);
        if(!segments)return failed(error,capacity,"deferred backing needs a RW single VM leaf");
        if(!gpu||p.size>gpu.maxBufferLength)return failed(error,capacity,"deferred backing exceeds maxBufferLength");
        BOOL completed=NO;
        @try{completed=poolCreateNoCopySegments(p,gpu,segments,0);if(completed)p.noCopyDeferred=NO;}
        @catch(NSException *exception){(void)exception;}
        if(!completed){
            p.retiring=YES;@autoreleasepool{botw_whole_pool_backing_drop(p);}
            if(!botw_whole_pool_backing_ack(p,error,capacity))return 0;
            p.retiring=NO;return failed(error,capacity,"deferred NoCopy allocation failed");
        }
        return 1;
    }
}
int botw_whole_pool_backing_flush(BotwWholePoolBacking *p,size_t offset,size_t size,int invalidate,
        char *error,size_t capacity){
    if(!p.live||p.retiring||offset>p.size||size>p.size-offset)
        return failed(error,capacity,"stale pool or cache range outside backing");
    if(!size||!(p.flags&4))return 1;
    if(!p.memory)return failed(error,capacity,"cached pool has no CPU backing");
    if(!p.coherent){
        if(!p.buffer||!p.buffer.contents)return failed(error,capacity,"copied backing has no contents");
        void *canonical=(char*)p.memory+offset,*copy=(char*)p.buffer.contents+p.bufferOffset+offset;
        if(invalidate?!botw_whole_pool_write(canonical,copy,size):!botw_whole_pool_read(copy,canonical,size))
            return failed(error,capacity,"checked cached backing copy failed");
    }
    atomic_thread_fence(memory_order_seq_cst);return 1;
}
