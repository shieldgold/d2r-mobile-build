/* Standalone CPU adapter fixture. All mappings/objects are owned here. Metal
 * protocol stand-ins record calls; no Metal device or GPU command is created. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <mach/mach.h>
#include <sys/mman.h>
#include <stdarg.h>
#include <unistd.h>
#include "../nvn_protos.h"
#include "../pool_policy.h"
#include "../vendor/botw-vpool/modules.inc"
@interface BWDevice:NSObject @end
@implementation BWDevice @end
@interface BWPool:NSObject
@property void *memory;@property size_t size,bufferOffset;@property int flags;
@property(strong) BWDevice *device;@property uint64_t generation,gpuBase;
@property BWPoolKind kind;@property BOOL live,coherent,tracked,retiring,registered;
@property BOOL noCopyDeferred;@property(strong) NSArray *segments;
@property(strong) id<MTLBuffer> buffer;
@end
static __weak BWPool *lastCreatedPool;
@implementation BWPool
- (instancetype)init{if((self=[super init]))lastCreatedPool=self;return self;}
@end
#include "../pool_runtime.h"
typedef struct {uint64_t serial;} BWObject;
typedef struct {NVNdevice *device;void *memory;size_t size;int flags;} PoolBuilder;
static NSMutableDictionary<NSNumber*,id> *objects;static NSMutableDictionary<NSNumber*,NSValue*> *objectSpans;static NSMutableArray<BWPool*> *pools;
static uint64_t serial;static id<MTLDevice> gpu;static BOOL textureTrackingAvailable;
static int (*memoryValidator)(const void*,size_t);static int (*memoryAccessValidator)(const void*,size_t,int);
static char lastError[512];static int dirtyFail,writePreparations,guardCalls,retireReferences;static id consumerBuffer;
static unsigned snapshotAllocations,noCopyCreated,noCopyReleased;static BOOL rejectOversizedSnapshot,dirtyAddFail;
static int admissionVMMode;static unsigned admissionVMQueries,admissionLogicalCalls,admissionWriteCalls;
static char admissionOrder[32];static unsigned admissionOrderLength;
static void admissionEvent(char event){if(admissionOrderLength+1<sizeof admissionOrder){admissionOrder[admissionOrderLength++]=event;admissionOrder[admissionOrderLength]=0;}}
static BOOL holdNewNoCopy;static id heldNoCopy;
static unsigned char *allocation;static size_t allocationBytes;static NVNdevice rawDevice;static NVNmemoryPool rawPools[12];
static int report(const char *fmt,...){va_list a;va_start(a,fmt);vsnprintf(lastError,sizeof lastError,fmt,a);va_end(a);return 0;}
__attribute__((noreturn))static void fatal(const char *why){report("%s",why);@throw [NSException exceptionWithName:@"PoolFixtureFailure" reason:@(why) userInfo:nil];}
static BOOL range(size_t off,size_t n,size_t size){return off<=size&&n<=size-off;}
static BOOL memoryOverlap(const void *a,size_t n,const void *b,size_t m){uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;if(n>UINTPTR_MAX-x||m>UINTPTR_MAX-y)return YES;return n&&m&&(x<=y?y-x<n:x-y<m);}
static BOOL overlapsQueueArena(const void *p,size_t n){(void)p;(void)n;return NO;}
static BOOL overlapsCommandMemory(const void *p,size_t n){(void)p;(void)n;return NO;}
static void validateObjectStorage(void *p,size_t n){if(!p||n>UINTPTR_MAX-(uintptr_t)p)fatal("invalid object output");}
static void put(void *raw,id value){uint64_t key=++serial;memcpy(raw,&key,sizeof key);objects[@(key)]=value;objectSpans[@(key)]=[NSValue valueWithRange:NSMakeRange((uintptr_t)raw,[value isKindOfClass:[BWPool class]]?sizeof(NVNmemoryPool):sizeof(NVNdevice))];}
static void removeObject(void *raw){uint64_t key;memcpy(&key,raw,8);[objects removeObjectForKey:@(key)];[objectSpans removeObjectForKey:@(key)];memset(raw,0,8);}
static void textureVMAccess(const void *raw,size_t bytes,BOOL write){
 uintptr_t at=(uintptr_t)raw;if(!raw||bytes>UINTPTR_MAX-at)fatal("VM address overflow");uintptr_t end=at+bytes;
 while(at<end){vm_address_t r=at;vm_size_t length=0;natural_t depth=0;vm_region_submap_info_data_64_t info;
  for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;if(vm_region_recurse_64(mach_task_self(),&r,&length,&depth,(vm_region_recurse_info_t)&info,&count)!=KERN_SUCCESS||r>at||!length||at-r>=length)fatal(write?"VM destination not writable":"VM source not readable");if(info.is_submap){depth++;r=at;continue;}if(!(info.protection&(write?VM_PROT_WRITE:VM_PROT_READ)))fatal(write?"VM destination not writable":"VM source not readable");break;}
  at+=MIN((size_t)(end-at),(size_t)(length-(at-r)));}
}
static void texturePrepareWrite(void *dst,size_t n){if(memoryAccessValidator&&!memoryAccessValidator(dst,n,1))fatal("logical write denied");textureVMAccess(dst,n,YES);}
static void textureReadBytes(const void *src,void *dst,size_t n,const char *why){if(!src||n>UINTPTR_MAX-(uintptr_t)src||(memoryAccessValidator&&!memoryAccessValidator(src,n,0)))fatal(why);vm_size_t got=0;if(vm_read_overwrite(mach_task_self(),(vm_address_t)src,n,(vm_address_t)dst,&got)!=KERN_SUCCESS||got!=n)fatal(why);}
static void textureWriteBytes(void *dst,const void *src,size_t n,const char *why){(void)why;texturePrepareWrite(dst,n);memcpy(dst,src,n);}
static int fixtureDirtyAdd(const void *p,size_t n,char *error,size_t cap){(void)p;(void)n;if(dirtyAddFail){snprintf(error,cap,"synthetic checked dirty registration failed");return -1;}return 0;}
static int fixtureDirtyRemove(const void *p,char *error,size_t cap){(void)p;if(dirtyFail){snprintf(error,cap,"synthetic checked dirty retirement failed");return -1;}return 0;}
@interface PoolFixtureBuffer:NSObject
@property void *contents,*owned;@property NSUInteger length;@property(copy)void(^released)(void*,NSUInteger);
@end
@implementation PoolFixtureBuffer
- (void)dealloc{if(_released)_released(_contents,_length);free(_owned);}
@end
@interface PoolFixtureDevice:NSObject
@property NSUInteger maxBufferLength,allocationCalls,failAllocationAt,holdAllocationAt,throwAllocationAt;@property BOOL failAllocation;
@end
@implementation PoolFixtureDevice
- (id<MTLBuffer>)newBufferWithBytesNoCopy:(void*)p length:(NSUInteger)n options:(MTLResourceOptions)o deallocator:(void(^)(void*,NSUInteger))d{(void)o;self.allocationCalls++;if(self.failAllocation||self.failAllocationAt==self.allocationCalls)return nil;PoolFixtureBuffer *b=[PoolFixtureBuffer new];b.contents=p;b.length=n;noCopyCreated++;b.released=^(void *address,NSUInteger bytes){noCopyReleased++;if(d)d(address,bytes);};if(holdNewNoCopy||self.holdAllocationAt==self.allocationCalls)heldNoCopy=b;if(self.throwAllocationAt==self.allocationCalls)@throw [NSException exceptionWithName:@"PoolFixtureAllocation" reason:@"synthetic allocation throw after resource retention" userInfo:nil];return (id<MTLBuffer>)b;}
- (id<MTLBuffer>)newBufferWithBytes:(const void*)p length:(NSUInteger)n options:(MTLResourceOptions)o{(void)o;self.allocationCalls++;if(self.failAllocation)return nil;PoolFixtureBuffer *b=[PoolFixtureBuffer new];b.owned=malloc(n);memcpy(b.owned,p,n);b.contents=b.owned;b.length=n;return (id<MTLBuffer>)b;}
@end
@interface PoolFixtureCB:NSObject
@property MTLCommandBufferStatus status;@property(strong) NSError *error;@property BOOL failWait;@property unsigned waits;@property(strong)NSMutableArray *handlers;
@end
@implementation PoolFixtureCB
- (void)addCompletedHandler:(MTLCommandBufferHandler)h{if(!self.handlers)self.handlers=[NSMutableArray new];[self.handlers addObject:[h copy]];}
- (void)commit{self.status=MTLCommandBufferStatusCommitted;}
- (void)waitUntilCompleted{self.waits++;self.status=self.failWait?MTLCommandBufferStatusError:MTLCommandBufferStatusCompleted;if(self.failWait)self.error=[NSError errorWithDomain:@"SyntheticCPURecord" code:1 userInfo:nil];for(MTLCommandBufferHandler h in self.handlers)h((id<MTLCommandBuffer>)self);[self.handlers removeAllObjects];}
@end
static void finishBuffer(id<MTLCommandBuffer> cb){if(!cb)return;[cb waitUntilCompleted];if(cb.status==MTLCommandBufferStatusError)fatal("synthetic committed GPU error");}
static void poolPrepareGPUWrite(BWPoolRange *r){(void)r;writePreparations++;}
static void poolGuardMappingChange(BWPool *v,const NVNmappingRequest *r,int n){(void)v;(void)r;(void)n;guardCalls++;}
static void poolRetireReferences(BWPool *p){(void)p;retireReferences++;consumerBuffer=nil;}
#include "../pool_resolver.inc"
@interface PoolFixtureData:NSMutableData @end
@implementation PoolFixtureData
+ (instancetype)dataWithLength:(NSUInteger)n{snapshotAllocations++;if(rejectOversizedSnapshot&&n>4096*sizeof(NVNmappingRequest))fatal("synthetic oversized snapshot allocation");return (PoolFixtureData*)[NSMutableData dataWithLength:n];}
@end
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
#define nvn_dirty_add_range_checked fixtureDirtyAdd
#define nvn_dirty_remove_range_checked fixtureDirtyRemove
/* Only the existing NoCopy admission VM query is intercepted. Other owned VM
 * access checks keep their real public calls; this never creates a GPU device. */
static kern_return_t admissionVMQuery(vm_map_t task,vm_address_t *address,vm_size_t *size,natural_t *depth,vm_region_recurse_info_t info,mach_msg_type_number_t *count){
    admissionVMQueries++;admissionEvent('V');
    if(!admissionVMMode)return vm_region_recurse_64(task,address,size,depth,info,count);
    if(admissionVMMode==1){*count=3;return KERN_INVALID_ADDRESS;}
    vm_region_submap_info_data_64_t output={0};output.protection=VM_PROT_READ|VM_PROT_WRITE;
    *size=BW_POOL_MAPPING_PAGE_BYTES;*count=VM_REGION_SUBMAP_INFO_COUNT_64;
    if(admissionVMMode==2)*address+=(vm_address_t)getpagesize();
    else if(admissionVMMode==3)*size=0;
    else if(admissionVMMode==4){*address-=(vm_address_t)getpagesize();*size=(vm_size_t)getpagesize();}
    else if(admissionVMMode==5)output.protection=VM_PROT_READ;
    else if(admissionVMMode==6){if(admissionVMQueries==1)output.is_submap=1;else output.protection=VM_PROT_READ;}
    else if(admissionVMMode>=7){
        *size=(vm_size_t)getpagesize();
        if(admissionVMMode==7)*count=VM_REGION_SUBMAP_INFO_COUNT_64-1;
        if(admissionVMQueries>1){
            if(admissionVMMode==8)*address+=(vm_address_t)getpagesize();
            if(admissionVMMode==9)output.protection=VM_PROT_READ;
            if(admissionVMMode==10)return KERN_INVALID_ADDRESS;
            if(admissionVMMode==11)*size=0;
            if(admissionVMMode==13)*size=UINTPTR_MAX-*address+1;
            if(admissionVMMode==14)output.protection=VM_PROT_NONE;
        }
        if(admissionVMMode==15)output.is_submap=1;
        if(admissionVMMode==16){*depth=0;output.is_submap=1;}
    }
    memcpy(info,&output,sizeof output);return KERN_SUCCESS;
}
#define vm_region_recurse_64 admissionVMQuery
#define NSMutableData PoolFixtureData
#include "../memory_pool_family.inc"
#undef NSMutableData
#undef vm_region_recurse_64
#undef nvn_dirty_remove_range_checked
#undef nvn_dirty_add_range_checked
#undef NVN
#define PG ((size_t)BW_POOL_MAPPING_PAGE_BYTES)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK line %d: %s [%s]\n",__LINE__,#x,lastError);return 91;}}while(0)
#define REJECT(operation,text) do{BOOL didReject=NO;@try{operation;}@catch(NSException *e){(void)e;didReject=YES;}CHECK(didReject&&strstr(lastError,text));}while(0)
static int owned(const void *p,size_t n){uintptr_t at=(uintptr_t)p,base=(uintptr_t)allocation;return at>=base&&at-base<=allocationBytes&&n<=allocationBytes-(at-base);}
static void setup(void){objects=[NSMutableDictionary new];objectSpans=[NSMutableDictionary new];pools=[NSMutableArray new];PoolFixtureDevice *d=[PoolFixtureDevice new];d.maxBufferLength=8*PG;gpu=(id<MTLDevice>)d;put(&rawDevice,[BWDevice new]);allocationBytes=12*PG;allocation=mmap(NULL,allocationBytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);if(allocation==MAP_FAILED)abort();memset(allocation,0x11,allocationBytes);memset(allocation+PG,0x22,PG);memoryValidator=owned;}
static int create(unsigned index,unsigned flags,void *memory,size_t bytes){NVNmemoryPoolBuilder raw;memset(&raw,0,sizeof raw);PoolBuilder *b=(PoolBuilder*)&raw;b->device=&rawDevice;b->memory=memory;b->size=bytes;b->flags=(int)flags;return h_nvnMemoryPoolInitialize(&rawPools[index],&raw);}
static BWPool *host(unsigned i){return poolGuestObject(&rawPools[i],[BWPool class]);}
static NVNmappingRequest request(unsigned p,uint64_t poff,uint64_t voff,uint64_t bytes,int cls){return(NVNmappingRequest){&rawPools[p],poff,voff,bytes,cls,0};}
static int mappedSetup(void){if(!create(0,0x189,allocation,2*PG)||!create(1,0x221,NULL,4*PG))return 0;NVNmappingRequest r=request(0,0,0,2*PG,0x100);return h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r);}
static int testResolve(void){CHECK(mappedSetup());uint64_t base=h_nvnMemoryPoolGetBufferAddress(&rawPools[1]);size_t off=99;CHECK(poolAddress(base+PG+5,16,&off)==host(1)&&off==PG+5);CHECK(poolAddress((uintptr_t)allocation,1,&off)==nil);BWPoolRange *r=poolResolveAddress(base+PG+5,16,0,"resolve");CHECK(r.physical==host(0)&&r.canonical==allocation+PG+5&&r.metalOffset==host(0).bufferOffset+PG+5&&r.rawKind==0);uint64_t gen=r.mappingGeneration;NVNmappingRequest extra=request(0,0,3*PG,PG,0x100);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&extra));CHECK(poolResolveAddress(base+PG+5,16,0,"stable").mappingGeneration==gen);CHECK(create(2,0x221,NULL,PG));extra=request(0,PG,0,PG,0x100);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[2],1,&extra));BWPoolRange *alias=poolResolve(host(2),5,16,0,"alias");CHECK(alias.canonical==r.canonical&&alias.physicalID==r.physicalID);return 0;}
static int testResolveNegative(void){CHECK(mappedSetup());const char *why=NULL;CHECK(!poolTryIdentity(host(0),0,8,-1,&why)&&strstr(why,"physical pool has no logical consumer identity"));CHECK(!poolTryResolve(host(1),0,8,0xfe,&why)&&strstr(why,"kind"));CHECK(!poolTryResolve(host(1),2*PG,8,-1,&why)&&strstr(why,"unmapped"));CHECK(!poolTryResolve(host(0),0,8,-1,&why));CHECK(create(2,0x189,allocation+4*PG,PG));NVNmappingRequest r=request(2,0,PG,PG,0x100);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));CHECK(!poolTryResolve(host(1),0,2*PG,-1,&why)&&strstr(why,"fragmented"));uint64_t base=host(1).gpuBase;h_nvnMemoryPoolFinalize(&rawPools[1]);REJECT(poolResolveAddress(base,8,-1,"stale"),"stale");return 0;}
static int testBatch(void){CHECK(mappedSetup());poolBeginBatch();BWPoolRange *r=poolResolve(host(1),0,8,0,"preflight");poolSealBatch();CHECK(poolResolve(host(1),0,8,0,"encode")==r);const char *why=NULL;CHECK(poolTryResolve(host(1),1,8,0,&why));REJECT(poolResolve(host(1),1,8,0,"miss"),"sealed");NVNmappingRequest q=request(0,PG,0,PG,0x100);CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&q));poolEndBatch();CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&q));CHECK(poolResolve(host(1),0,8,0,"new").canonical==allocation+PG);return 0;}
static int testFamily(void){CHECK(create(0,0x189,allocation+4096,PG));CHECK(host(0).coherent&&host(0).bufferOffset==4096%getpagesize());CHECK(h_nvnMemoryPoolMap(&rawPools[0])==NULL&&h_nvnMemoryPoolGetBufferAddress(&rawPools[0])==0);((PoolFixtureDevice*)gpu).maxBufferLength=PG;CHECK(create(1,0x221,NULL,8*PG));CHECK(h_nvnMemoryPoolMap(&rawPools[1])==NULL&&h_nvnMemoryPoolGetBufferAddress(&rawPools[1])!=0&&host(1).buffer==nil);CHECK(h_nvnMemoryPoolGetSize(&rawPools[1])==8*PG&&h_nvnMemoryPoolGetFlags(&rawPools[0])==0x189);CHECK(((PoolFixtureDevice*)gpu).allocationCalls==1);((PoolFixtureDevice*)gpu).maxBufferLength=8*PG;CHECK(create(2,0x18a,allocation+4*PG,PG));CHECK(h_nvnMemoryPoolMap(&rawPools[2])==allocation+4*PG&&h_nvnMemoryPoolGetBufferAddress(&rawPools[2])==0);return 0;}
static int testMapping(void){CHECK(mappedSetup());NVNmappingRequest batch[]={request(0,PG,0,PG,0x100),request(0,0,PG,PG,0x107)};uint64_t gen=nvn_vpool_generation();CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch));CHECK(strstr(lastError,"MapVirtual request class/page/bounds/flags index=1 storageClass=0x107"));CHECK(nvn_vpool_generation()==gen&&poolResolve(host(1),0,8,0,"old").canonical==allocation);for(long after=0;after<2;after++){nvn_vpool_test_fail_allocation_after(after);batch[1].storageClass=0x1fe;CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch));nvn_vpool_test_fail_allocation_after(-1);CHECK(nvn_vpool_generation()==gen&&poolResolve(host(1),0,8,0,"old").canonical==allocation);}CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch));CHECK(poolResolve(host(1),PG,8,0xfe,"FE").rawKind==0xfe);return 0;}
static int testDepthKinds(void){
    CHECK(mappedSetup());CHECK(create(2,0x221,NULL,2*PG));
    int classes[]={0x101,0x17b};unsigned kinds[]={1,0x7b};
    for(unsigned i=0;i<2;i++){
        NVNmappingRequest r=request(0,PG,0,PG,classes[i]);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));
        BWPoolRange *resolved=poolResolve(host(1),8,16,kinds[i],"typed depth");
        CHECK(resolved.canonical==allocation+PG+8&&resolved.rawKind==kinds[i]);
        const char *why=NULL;CHECK(!poolTryResolve(host(1),8,16,0,&why)&&strstr(why,"kind"));
        CHECK(!poolTryResolve(host(1),8,16,0xfe,&why)&&strstr(why,"kind"));
        CHECK(!poolTryResolve(host(1),8,16,kinds[1-i],&why)&&strstr(why,"kind"));
        BWPoolIdentity *cpu=poolTryIdentity(host(1),8,16,-1,&why);CHECK(cpu&&cpu.canonical==resolved.canonical);
        CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[2],1,&r));BWPoolRange *alias=poolResolve(host(2),8,16,kinds[i],"alias");CHECK(alias.canonical==resolved.canonical&&alias.physicalID==resolved.physicalID);
        uint64_t gen=nvn_vpool_generation();NVNmappingRequest batch[]={request(0,0,0,PG,classes[1-i]),request(0,0,PG,PG,0x107)};
        CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch)&&nvn_vpool_generation()==gen);
        CHECK(poolResolve(host(1),8,16,kinds[i],"unchanged").mappingGeneration==resolved.mappingGeneration);
        batch[1].storageClass=classes[1-i];for(long failure=0;failure<2;failure++){nvn_vpool_test_fail_allocation_after(failure);CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch));nvn_vpool_test_fail_allocation_after(-1);CHECK(nvn_vpool_generation()==gen&&poolResolve(host(1),8,16,kinds[i],"allocation rollback").canonical==resolved.canonical);}
        NVNmappingRequest unmap={NULL,0,0,PG,-1,0};CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&unmap));CHECK(!poolTryResolve(host(1),8,16,kinds[i],&why)&&strstr(why,"unmapped"));CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));CHECK(poolResolve(host(1),8,16,kinds[i],"ABA new generation").mappingGeneration!=resolved.mappingGeneration);
    }
    BWPoolRange *before=poolResolve(host(1),8,16,0x7b,"old physical");h_nvnMemoryPoolFinalize(&rawPools[0]);const char *why=NULL;CHECK(!poolTryResolve(host(1),8,16,0x7b,&why)&&strstr(why,"unmapped"));size_t bias=8192;if(((uintptr_t)allocation+bias)%PG==0)bias=4096;CHECK(create(0,0x189,allocation+bias,4*PG));NVNmappingRequest newMap=request(0,PG,0,PG,0x17b);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&newMap));BWPoolRange *after=poolResolve(host(1),8,16,0x7b,"physical ABA");CHECK(after.physicalID!=before.physicalID&&after.canonical==allocation+bias+PG+8&&(uintptr_t)after.canonical%PG!=8);return 0;
}
static int testRaw46Identity(void){
    CHECK(mappedSetup());CHECK(create(2,0x221,NULL,2*PG));
    PoolFixtureDevice *device=(PoolFixtureDevice*)gpu;NSUInteger allocations=device.allocationCalls;
    NVNmappingRequest r=request(0,0,0,2*PG,0x146);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));
    const char *why=NULL;BWPoolIdentity *cpu=poolTryIdentity(host(1),5,16,0x46,&why),*any=poolTryIdentity(host(1),5,16,-1,&why);
    CHECK(cpu&&any&&cpu.canonical==allocation+5&&cpu.rawKind==0x46&&any.rawKind==0x46);
    CHECK(h_nvnMemoryPoolMap(&rawPools[1])==NULL&&device.allocationCalls==allocations);
    int gpuKinds[]={0,0xfe,1,0x7b};for(unsigned i=0;i<sizeof gpuKinds/sizeof *gpuKinds;i++){CHECK(!poolTryIdentity(host(1),5,16,gpuKinds[i],&why)&&strstr(why,"kind"));}
    CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[2],1,&r));BWPoolIdentity *alias=poolTryIdentity(host(2),5,16,0x46,&why);CHECK(alias&&alias.canonical==cpu.canonical&&alias.physicalID==cpu.physicalID);
    uint64_t generation=nvn_vpool_generation();NVNmappingRequest batch[]={request(0,PG,0,PG,0x146),request(0,0,PG,PG,0x107)};
    CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch)&&generation==nvn_vpool_generation());CHECK(poolTryIdentity(host(1),5,16,0x46,&why).mappingGeneration==cpu.mappingGeneration);
    batch[1].storageClass=0x146;for(long failure=0;failure<2;failure++){nvn_vpool_test_fail_allocation_after(failure);CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],2,batch));nvn_vpool_test_fail_allocation_after(-1);CHECK(nvn_vpool_generation()==generation&&poolTryIdentity(host(1),5,16,0x46,&why).canonical==cpu.canonical);}
    NVNmappingRequest unmap={NULL,0,0,PG,0x1c3,0};CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&unmap));CHECK(!poolTryIdentity(host(1),5,16,-1,&why)&&strstr(why,"unmapped"));CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));CHECK(poolTryIdentity(host(1),5,16,0x46,&why).mappingGeneration!=cpu.mappingGeneration);
    uint64_t oldID=cpu.physicalID;h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(!poolTryIdentity(host(1),5,16,-1,&why)&&!poolTryIdentity(host(2),5,16,0x46,&why));CHECK(create(0,0x189,allocation+4096,2*PG));CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));CHECK(poolTryIdentity(host(1),5,16,0x46,&why).physicalID!=oldID);return 0;
}
static int testRaw46GPURefusal(void){
    CHECK(mappedSetup());NVNmappingRequest r=request(0,0,0,PG,0x146);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r));
    const char *why=NULL;CHECK(poolTryIdentity(host(1),5,16,0x46,&why));
    PoolFixtureDevice *device=(PoolFixtureDevice*)gpu;NSUInteger allocations=device.allocationCalls;
    CHECK(!poolTryResolve(host(1),5,16,0x46,&why)&&strstr(why,"raw kind 0x46"));
    CHECK(!poolTryResolve(host(1),5,16,-1,&why)&&strstr(why,"raw kind 0x46"));
    poolBeginBatch();REJECT(poolResolve(host(1),5,16,0x46,"not a GPU representation"),"raw kind 0x46");CHECK(!poolBatchRanges.count);poolEndBatch();
    CHECK(device.allocationCalls==allocations);return 0;
}
static int testMappingNegative(void){CHECK(mappedSetup());size_t page=getpagesize();NVNmappingRequest *r=mmap(NULL,2*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(r!=MAP_FAILED);*r=request(0,0,0,PG,0x100);CHECK(!mprotect(r,page,PROT_READ));CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,r));CHECK(!mprotect(r,page,PROT_NONE));uint64_t gen=nvn_vpool_generation();CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],0,r));REJECT((void)h_nvnMemoryPoolMapVirtual(&rawPools[1],1,r),"readable");CHECK(nvn_vpool_generation()==gen);CHECK(!mprotect(r,page,PROT_READ|PROT_WRITE));*r=(NVNmappingRequest){NULL,1,0,PG,-1,0};CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],1,r));r->physicalOffset=0;CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,r));const char *why=NULL;CHECK(!poolTryResolve(host(1),0,8,-1,&why)&&strstr(why,"unmapped"));CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],-1,r));CHECK(!munmap(r,2*page));return 0;}
static int testInitializeNegative(void){PoolFixtureDevice *device=(PoolFixtureDevice*)gpu;uint64_t gen=nvn_vpool_generation();CHECK(!create(0,0x189,NULL,PG));CHECK(!create(0,0x189,allocation+1,PG));CHECK(!create(0,0x321,NULL,PG));CHECK(!create(0,0x221,allocation,PG));CHECK(!device.allocationCalls&&!pools.count&&nvn_vpool_generation()==gen);device.maxBufferLength=PG;CHECK(!create(0,0x189,allocation+4096,PG));CHECK(!device.allocationCalls);device.maxBufferLength=8*PG;CHECK(!mprotect(allocation,PG,PROT_READ));CHECK(!create(0,0x189,allocation,PG));CHECK(!device.allocationCalls);CHECK(!mprotect(allocation,PG,PROT_READ|PROT_WRITE));device.failAllocation=YES;CHECK(!create(0,0x189,allocation,PG));CHECK(!pools.count&&!((BWObject*)&rawPools[0])->serial&&nvn_vpool_generation()==gen);return 0;}
static int testFinalize(void){CHECK(mappedSetup());BWPool *p=host(0);p.tracked=YES;dirtyFail=1;uint64_t gen=nvn_vpool_generation();REJECT(h_nvnMemoryPoolFinalize(&rawPools[0]),"dirty retirement");CHECK(p.retiring&&p.live&&p.registered&&((BWObject*)&rawPools[0])->serial&&nvn_vpool_generation()==gen);const char *why=NULL;CHECK(!poolTryResolve(host(1),0,8,0,&why));dirtyFail=0;h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(!p.live&&!p.registered&&!((BWObject*)&rawPools[0])->serial);CHECK(!poolTryResolve(host(1),0,8,0,&why)&&strstr(why,"unmapped"));return 0;}
static int testProvenance(void){NVNdevice copied=rawDevice;NVNmemoryPoolBuilder b;memset(&b,0,sizeof b);PoolBuilder *fields=(PoolBuilder*)&b;fields->device=(NVNdevice*)(uintptr_t)0xdeadbeef000;fields->memory=allocation;fields->size=PG;fields->flags=0x189;REJECT((void)h_nvnMemoryPoolInitialize(&rawPools[0],&b),"provenance");fields->device=&copied;REJECT((void)h_nvnMemoryPoolInitialize(&rawPools[0],&b),"provenance");CHECK(mappedSetup());NVNmemoryPool fake=rawPools[0];REJECT((void)h_nvnMemoryPoolGetFlags(&fake),"provenance");NVNmappingRequest r=request(0,0,0,PG,0x100);r.physicalPool=(NVNmemoryPool*)(uintptr_t)0xdeadbeef000;uint64_t gen=nvn_vpool_generation();REJECT((void)h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&r),"provenance");CHECK(nvn_vpool_generation()==gen);return 0;}
static int testCPUAccess(void){CHECK(create(3,0x21,(void*)0x55,0));CHECK(h_nvnMemoryPoolMap(&rawPools[3])==(void*)0x55&&h_nvnMemoryPoolGetBufferAddress(&rawPools[3])==0x55);CHECK(mappedSetup());CHECK(h_nvnMemoryPoolMap(&rawPools[0])==NULL&&h_nvnMemoryPoolMap(&rawPools[1])==NULL);h_nvnMemoryPoolFlushMappedRange(&rawPools[1],0,PG);h_nvnMemoryPoolInvalidateMappedRange(&rawPools[1],0,PG);memoryValidator=NULL;CHECK(create(2,4,allocation+4*PG+1,PG-2));memoryValidator=owned;BWPool *p=host(2);CHECK(!p.coherent&&h_nvnMemoryPoolMap(&rawPools[2])==p.memory);((unsigned char*)p.memory)[0]=0x81;h_nvnMemoryPoolFlushMappedRange(&rawPools[2],0,1);CHECK(((unsigned char*)p.buffer.contents)[0]==0x81);((unsigned char*)p.buffer.contents)[1]=0x92;h_nvnMemoryPoolInvalidateMappedRange(&rawPools[2],1,1);CHECK(((unsigned char*)p.memory)[1]==0x92);return 0;}
static int testGPURecords(void){
 CHECK(mappedSetup());PoolFixtureCB *cb=[PoolFixtureCB new];__weak BWPoolRange *weak;
 @autoreleasepool{BWPoolRange *r=poolResolve(host(1),0,16,0,"capture");weak=r;poolRetainForGPU((id<MTLCommandBuffer>)cb,r,YES);}
 CHECK(weak&&writePreparations==1);poolPrepareCommit((id<MTLCommandBuffer>)cb);[cb commit];poolNoteCommitted((id<MTLCommandBuffer>)cb);poolDrainGPU();CHECK(cb.waits==1&&!weak&&!poolGPURecords.count);
 cb=[PoolFixtureCB new];cb.failWait=YES;poolPrepareCommit((id<MTLCommandBuffer>)cb);[cb commit];poolNoteCommitted((id<MTLCommandBuffer>)cb);REJECT(poolDrainGPU(),"committed GPU error");CHECK(!poolGPURecords.count);
 cb=[PoolFixtureCB new];poolPrepareCommit((id<MTLCommandBuffer>)cb);poolEndBatch();CHECK(!poolGPURecords.count);return 0;
}
static int testBoundedRecords(void){CHECK(mappedSetup());for(unsigned i=0;i<64;i++){PoolFixtureCB *cb=[PoolFixtureCB new];poolPrepareCommit((id<MTLCommandBuffer>)cb);[cb commit];poolNoteCommitted((id<MTLCommandBuffer>)cb);[cb waitUntilCompleted];CHECK(poolGPURecords.count<=2);}poolDrainGPU();CHECK(!poolGPURecords.count);return 0;}
static int testRetireReferences(void){CHECK(mappedSetup());BWPoolRange *r=poolResolve(host(1),0,8,0,"retire capture");__weak id weak;@autoreleasepool{consumerBuffer=r.buffer;weak=r.buffer;}uint64_t identity=r.physicalID;CHECK(weak);@autoreleasepool{h_nvnMemoryPoolFinalize(&rawPools[0]);}CHECK(retireReferences==1&&!weak&&!consumerBuffer&&!r.buffer&&!r.canonical);CHECK(r.physicalID==identity&&r.length==8&&r.offset==0);return 0;}
static int testBorrowedPinned(void){CHECK(mappedSetup());BWPool *p=host(0);__attribute__((objc_precise_lifetime)) id retained;@autoreleasepool{retained=p.buffer;}uint64_t generation=nvn_vpool_generation();REJECT(h_nvnMemoryPoolFinalize(&rawPools[0]),"borrowed Metal backing");CHECK(retained&&p.retiring&&p.live&&p.registered&&nvn_vpool_generation()==generation);retained=nil;h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(!p.live&&!p.registered);return 0;}
static int testMappingAllocationBounds(void){CHECK(mappedSetup());uint64_t generation=nvn_vpool_generation();unsigned before=snapshotAllocations;rejectOversizedSnapshot=YES;const NVNmappingRequest *invalid=(const NVNmappingRequest*)(uintptr_t)0xdeadbeef000;CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],INT_MAX,invalid));CHECK(strstr(lastError,"request count exceeds")&&snapshotAllocations==before);CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],4097,invalid));CHECK(strstr(lastError,"request count exceeds")&&snapshotAllocations==before);size_t page=getpagesize();void *none=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(none!=MAP_FAILED);CHECK(!h_nvnMemoryPoolMapVirtual(&rawPools[1],INT_MAX,none));CHECK(strstr(lastError,"request count exceeds")&&snapshotAllocations==before);REJECT((void)h_nvnMemoryPoolMapVirtual(&rawPools[1],1,none),"VM");CHECK(snapshotAllocations==before&&nvn_vpool_generation()==generation);CHECK(!munmap(none,page));NVNmappingRequest *maximum=calloc(4096,sizeof *maximum);CHECK(maximum);for(unsigned i=0;i<4096;i++)maximum[i]=(NVNmappingRequest){NULL,0,0,PG,-1,0};CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],4096,maximum));free(maximum);return 0;}
static int testUnpublishedCleanup(void){uint64_t generation=nvn_vpool_generation();nvn_vpool_test_fail_allocation_after(0);CHECK(!create(0,0x189,allocation,PG));nvn_vpool_test_fail_allocation_after(-1);CHECK(noCopyCreated==1&&noCopyReleased==1&&!pools.count&&!((BWObject*)&rawPools[0])->serial&&nvn_vpool_generation()==generation);dirtyAddFail=YES;textureTrackingAvailable=YES;CHECK(!create(0,0x189,allocation,PG));CHECK(strstr(lastError,"dirty registration")&&noCopyCreated==2&&noCopyReleased==2&&!pools.count&&!((BWObject*)&rawPools[0])->serial);nvn_vpool_stats_t stats;nvn_vpool_stats(&stats);CHECK(!stats.phys_live&&!stats.virt_live);dirtyAddFail=NO;CHECK(create(0,0x189,allocation,PG));h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(noCopyCreated==3&&noCopyReleased==3);return 0;}
static int testUnpublishedPinned(void){
 @autoreleasepool{
 dirtyAddFail=YES;textureTrackingAvailable=YES;holdNewNoCopy=YES;
 @autoreleasepool{REJECT((void)create(0,0x189,allocation,PG),"borrowed Metal backing");}
 nvn_vpool_stats_t state;nvn_vpool_stats(&state);CHECK(state.phys_live==1&&!pools.count&&!((BWObject*)&rawPools[0])->serial&&heldNoCopy);
 CHECK(lastCreatedPool&&lastCreatedPool.retiring&&lastCreatedPool.registered&&!lastCreatedPool.live);const char *failure=NULL;CHECK(!poolTryResolve(lastCreatedPool,0,8,-1,&failure));
 unsigned created=noCopyCreated;CHECK(!create(0,0x189,allocation,PG));CHECK(strstr(lastError,"quarantined")&&noCopyCreated==created);
 REJECT(poolShutdownPools(),"borrowed Metal backing");nvn_vpool_stats(&state);CHECK(state.phys_live==1&&lastCreatedPool);
 @autoreleasepool{heldNoCopy=nil;poolShutdownPools();}nvn_vpool_stats(&state);CHECK(!state.phys_live&&!poolUnpublishedPools.count&&noCopyReleased==1);
 }CHECK(!lastCreatedPool);
 dirtyAddFail=NO;holdNewNoCopy=NO;CHECK(create(0,0x189,allocation,PG));h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(noCopyCreated==2&&noCopyReleased==2);return 0;
}
static int testUnpublishedRetry(BOOL registryFailure){
 dirtyAddFail=!registryFailure;textureTrackingAvailable=YES;holdNewNoCopy=YES;
 if(registryFailure)nvn_vpool_test_fail_allocation_after(0);
 @autoreleasepool{REJECT((void)create(0,0x189,allocation,PG),"borrowed Metal backing");}
 nvn_vpool_test_fail_allocation_after(-1);
 CHECK(poolUnpublishedPools.count==1&&lastCreatedPool.retiring&&lastCreatedPool.registered==!registryFailure);
 dirtyAddFail=NO;holdNewNoCopy=NO;heldNoCopy=nil;
 CHECK(create(0,0x189,allocation,PG));nvn_vpool_stats_t state;nvn_vpool_stats(&state);
 CHECK(state.phys_live==1&&!poolUnpublishedPools.count&&noCopyCreated==2&&noCopyReleased==1);
 h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(noCopyReleased==2);return 0;
}
static int testOrdinaryProtection(void){CHECK(!mprotect(allocation,PG,PROT_READ));CHECK(create(0,0x22,allocation,PG));CHECK(host(0).coherent);h_nvnMemoryPoolFlushMappedRange(&rawPools[0],0,8);REJECT(h_nvnMemoryPoolInvalidateMappedRange(&rawPools[0],0,8),"VM");CHECK(!mprotect(allocation,PG,PROT_READ|PROT_WRITE));h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(!mprotect(allocation+2*PG,PG,PROT_NONE));CHECK(create(1,0x22,allocation+2*PG,PG));REJECT(h_nvnMemoryPoolFlushMappedRange(&rawPools[1],0,8),"VM");CHECK(!mprotect(allocation+2*PG,PG,PROT_READ|PROT_WRITE));return 0;}
static int admissionDenyLogical(const void *p,size_t n){(void)p;(void)n;admissionLogicalCalls++;admissionEvent('L');return 0;}
static int admissionWriteDenied;
static int admissionAccess(const void *p,size_t n,int write){if(p==allocation&&n==PG&&write){admissionWriteCalls++;admissionEvent('W');return !admissionWriteDenied;}return 1;}
static void admissionReset(void){admissionVMMode=0;admissionVMQueries=admissionLogicalCalls=admissionWriteCalls=admissionOrderLength=0;admissionOrder[0]=lastError[0]=0;admissionWriteDenied=0;}
static int admissionReject(unsigned flags,void *memory,size_t bytes,const char *stage){
    unsigned noCopyBefore=noCopyCreated,releasedBefore=noCopyReleased,allocations=(unsigned)((PoolFixtureDevice*)gpu).allocationCalls,snapshots=snapshotAllocations;
    NSUInteger objectCount=objects.count,spanCount=objectSpans.count;uint64_t generation=nvn_vpool_generation();NVNmemoryPool output=rawPools[0];nvn_vpool_stats_t before,after;nvn_vpool_stats(&before);
    CHECK(!create(0,flags,memory,bytes));CHECK(strstr(lastError,"Physical/uncached pool requires coherent host-page-aligned single-VM-region backing"));CHECK(strstr(lastError,stage));
    CHECK(strstr(lastError,"flags=0x")&&strstr(lastError,"bytes=")&&strstr(lastError,"page=")&&strstr(lastError,"mod=")&&strstr(lastError,"logical=")&&strstr(lastError,"access=")&&strstr(lastError,"vm_fields=")&&strstr(lastError,"vm_remaining="));
    CHECK(strlen(lastError)<sizeof lastError-1&&!strstr(lastError,"address=")&&!strstr(lastError,"pointer="));nvn_vpool_stats(&after);
    CHECK(noCopyCreated==noCopyBefore&&noCopyReleased==releasedBefore&&((PoolFixtureDevice*)gpu).allocationCalls==allocations&&snapshotAllocations==snapshots);
    CHECK(!memcmp(&output,&rawPools[0],sizeof output)&&objects.count==objectCount&&objectSpans.count==spanCount&&!pools.count&&!poolUnpublishedPools.count&&nvn_vpool_generation()==generation&&before.phys_live==after.phys_live&&before.virt_live==after.virt_live);return 0;
}
static int testAdmissionLogical(void){
    admissionReset();memoryValidator=NULL;memoryAccessValidator=admissionAccess;
    CHECK(!admissionReject(0x21,allocation+1,PG-2,"stage=range_validator_missing"));CHECK(!admissionOrderLength&&!admissionVMQueries&&!admissionWriteCalls);CHECK(strstr(lastError,"logical=missing"));
    admissionReset();memoryValidator=admissionDenyLogical;CHECK(!admissionReject(0x21,allocation+1,PG-2,"stage=range_denied"));CHECK(!strcmp(admissionOrder,"L")&&admissionLogicalCalls==1&&!admissionWriteCalls&&!admissionVMQueries);CHECK(strstr(lastError,"logical=deny"));
    admissionReset();memoryValidator=NULL;memoryAccessValidator=NULL;CHECK(create(0,0x24,allocation+1,PG-2));CHECK(!lastError[0]&&!host(0).coherent&&noCopyCreated==0);h_nvnMemoryPoolFinalize(&rawPools[0]);return 0;
}
static int testAdmissionWrite(void){admissionReset();memoryAccessValidator=admissionAccess;admissionWriteDenied=1;CHECK(!admissionReject(0x189,allocation,PG,"stage=write_denied"));CHECK(!strcmp(admissionOrder,"W")&&admissionWriteCalls==1&&!admissionVMQueries&&!admissionLogicalCalls&&strstr(lastError,"access=deny"));return 0;}
static int testAdmissionVMQuery(void){admissionReset();memoryAccessValidator=admissionAccess;admissionVMMode=1;CHECK(!admissionReject(0x189,allocation,PG,"stage=vm_query"));CHECK(!strcmp(admissionOrder,"WV")&&admissionWriteCalls==1&&admissionVMQueries==1&&strstr(lastError,"vm_fields=0x1")&&strstr(lastError,"vm_result=1")&&strstr(lastError,"vm_count=3"));return 0;}
static int testAdmissionVMRegion(void){const char *stages[]={"stage=vm_gap","stage=vm_empty","stage=vm_start"};for(int mode=2;mode<=4;mode++){admissionReset();memoryAccessValidator=admissionAccess;admissionVMMode=mode;CHECK(!admissionReject(0x189,allocation,PG,stages[mode-2]));CHECK(!strcmp(admissionOrder,"WV")&&admissionVMQueries==1);}return 0;}
static int testAdmissionVMProtection(void){admissionReset();memoryAccessValidator=admissionAccess;admissionVMMode=5;CHECK(!admissionReject(0x189,allocation,PG,"stage=vm_protection"));CHECK(!strcmp(admissionOrder,"WV")&&strstr(lastError,"vm_prot=0x1"));admissionReset();admissionVMMode=6;CHECK(!admissionReject(0x189,allocation,PG,"stage=vm_protection"));CHECK(!strcmp(admissionOrder,"WVV")&&admissionVMQueries==2&&strstr(lastError,"vm_depth=1"));return 0;}
static int testAdmissionVMSpan(void){admissionReset();memoryAccessValidator=admissionAccess;size_t page=(size_t)getpagesize();CHECK(!mprotect(allocation+page,page,PROT_READ));int result=admissionReject(0x189,allocation,PG,"stage=vm_protection");CHECK(!mprotect(allocation+page,page,PROT_READ|PROT_WRITE));CHECK(!result&&admissionWriteCalls==1&&admissionVMQueries==2&&admissionOrder[0]=='W'&&strstr(lastError,"vm_fields=0xf"));return 0;}
static int testAdmissionRounding(void){admissionReset();CHECK(!admissionReject(0x21,(void*)(UINTPTR_MAX-8),4,"stage=rounding_overflow"));CHECK(!admissionVMQueries&&!admissionLogicalCalls&&!admissionWriteCalls);return 0;}
/* A different inheritance value creates real adjacent RW leaf entries without
 * changing access or substituting backing. The fixture owns every byte. */
static int splitOwnedRW(void){
 size_t page=(size_t)getpagesize();CHECK(vm_inherit(mach_task_self(),(vm_address_t)(allocation+page),page,VM_INHERIT_NONE)==KERN_SUCCESS);
 vm_address_t at=(vm_address_t)allocation;vm_size_t length=0;natural_t depth=0;vm_region_submap_info_data_64_t info={0};mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;
 CHECK(vm_region_recurse_64(mach_task_self(),&at,&length,&depth,(vm_region_recurse_info_t)&info,&count)==KERN_SUCCESS);
 CHECK(count>=VM_REGION_SUBMAP_INFO_COUNT_64&&at==(vm_address_t)allocation&&length==page&&(info.protection&3)==3);return 0;
}
static int testSegmentReal(void){
 CHECK(!splitOwnedRW());CHECK(create(0,0x189,allocation,PG));CHECK(noCopyCreated==3&&((PoolFixtureDevice*)gpu).allocationCalls==3);
 CHECK(create(1,0x221,NULL,PG));NVNmappingRequest request0=request(0,0,0,PG,0x100);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[1],1,&request0));
 size_t page=(size_t)getpagesize();BWPoolRange *a=poolResolve(host(1),page-8,8,0,"first segment"),*b=poolResolve(host(1),page,8,0,"second segment");
 CHECK(a.buffer!=b.buffer&&a.physicalID==b.physicalID&&a.canonical==allocation+page-8&&b.canonical==allocation+page);
 const char *why=NULL;CHECK(!poolTryResolve(host(1),page-8,16,0,&why)&&strstr(why,"crosses Metal backing segments"));
 BWPoolIdentity *identity=poolTryIdentity(host(1),0,PG,0,&why);CHECK(identity&&identity.canonical==allocation&&identity.length==PG&&identity.physicalID==a.physicalID);
 host(1).flags|=NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS;CHECK(poolTryIdentity(host(1),0,PG,0,&why));CHECK(!poolTryResolve(host(1),0,8,0,&why)&&strstr(why,"GPU_NO_ACCESS"));host(1).flags&=~NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS;
 a=nil;b=nil;h_nvnMemoryPoolFinalize(&rawPools[0]);CHECK(noCopyReleased==3);
 /* Actual boot rounds a 4 KiB-aligned pointer into a larger host page. Bias
  * belongs only to the first segment, never to later segment-local offsets. */
 size_t bias=page/2;CHECK(create(2,0x189,allocation+bias,PG));CHECK(create(3,0x221,NULL,PG));request0=request(2,0,0,PG,0x100);CHECK(h_nvnMemoryPoolMapVirtual(&rawPools[3],1,&request0));
 BWPoolRange *first=poolResolve(host(3),0,8,0,"biased first"),*second=poolResolve(host(3),page-bias,8,0,"biased second");CHECK(first.metalOffset==bias&&second.metalOffset==0&&first.buffer!=second.buffer&&second.canonical==allocation+page);
 CHECK(poolTryIdentity(host(3),0,PG,0,&why).canonical==allocation+bias);CHECK(!poolTryResolve(host(3),page-bias-8,16,0,&why)&&strstr(why,"crosses Metal backing segments"));
 first=nil;second=nil;h_nvnMemoryPoolFinalize(&rawPools[2]);CHECK(noCopyReleased==6);return 0;
}
static int testSegmentMetadata(void){
 const char *stages[]={"vm_info_short","vm_gap","vm_protection","vm_query","vm_empty","vm_region_overflow","vm_protection","vm_submap_limit"};int modes[]={7,8,9,10,11,13,14,15};
 for(unsigned i=0;i<sizeof modes/sizeof modes[0];i++){admissionReset();admissionVMMode=modes[i];CHECK(!admissionReject(0x189,allocation,PG,stages[i]));CHECK(admissionVMQueries==(modes[i]==7?1:modes[i]==15?65:2));}
 admissionReset();admissionVMMode=12;void *old=allocation;size_t oldBytes=allocationBytes;allocationBytes=257*(size_t)getpagesize();allocation=mmap(NULL,allocationBytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(allocation!=MAP_FAILED);((PoolFixtureDevice*)gpu).maxBufferLength=allocationBytes;
 /* Round up to the NVN 64 KiB granularity while still exceeding 256 leaves. */
 size_t bytes=(allocationBytes+PG-1)&~(PG-1);CHECK(!munmap(allocation,allocationBytes));allocationBytes=bytes;allocation=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(allocation!=MAP_FAILED);((PoolFixtureDevice*)gpu).maxBufferLength=bytes;
 CHECK(!admissionReject(0x189,allocation,bytes,"vm_segment_limit"));CHECK(admissionVMQueries==256);CHECK(!munmap(allocation,bytes));allocation=old;allocationBytes=oldBytes;admissionReset();return 0;
}
static int testSegmentPinned(BOOL partialFailure,BOOL dirtyFailure){
 CHECK(!splitOwnedRW());PoolFixtureDevice *device=(PoolFixtureDevice*)gpu;
 device.holdAllocationAt=partialFailure?1:2;if(partialFailure)device.failAllocationAt=2;dirtyAddFail=dirtyFailure;textureTrackingAvailable=dirtyFailure;
 if(partialFailure||dirtyFailure){@autoreleasepool{REJECT((void)create(0,0x189,allocation,PG),"borrowed Metal backing");}CHECK(poolUnpublishedPools.count==1&&!pools.count&&!((BWObject*)&rawPools[0])->serial);}
 else{CHECK(create(0,0x189,allocation,PG));REJECT(h_nvnMemoryPoolFinalize(&rawPools[0]),"borrowed Metal backing");CHECK(host(0).retiring&&host(0).registered&&host(0).live);}
 CHECK(noCopyCreated==(partialFailure?1:3)&&noCopyReleased==(partialFailure?0:2)&&heldNoCopy);
 REJECT(poolShutdownPools(),"borrowed Metal backing");CHECK(noCopyReleased==(partialFailure?0:2));
 @autoreleasepool{heldNoCopy=nil;poolShutdownPools();}CHECK(noCopyReleased==noCopyCreated&&!poolUnpublishedPools.count&&!pools.count);nvn_vpool_stats_t state;nvn_vpool_stats(&state);CHECK(!state.phys_live);return 0;
}
static int testOrdinaryDeferred(void){
 size_t page=(size_t)getpagesize();CHECK(!mprotect(allocation,PG,PROT_READ));CHECK(create(0,0x22,allocation,PG));BWPool *p=host(0);CHECK(p.noCopyDeferred&&p.coherent&&!p.buffer&&!noCopyCreated);
 const char *why=NULL;CHECK(poolTryIdentity(p,0,8,0,&why)&&!noCopyCreated);CHECK(!poolTryResolve(p,0,8,0,&why)&&strstr(why,"deferred ordinary"));CHECK(!noCopyCreated&&!p.retiring);
 CHECK(!mprotect(allocation,PG,PROT_READ|PROT_WRITE));CHECK(poolTryResolve(p,0,8,0,&why)&&!p.noCopyDeferred&&noCopyCreated==1);h_nvnMemoryPoolFinalize(&rawPools[0]);
 CHECK(vm_inherit(mach_task_self(),(vm_address_t)(allocation+page),page,VM_INHERIT_NONE)==KERN_SUCCESS);unsigned before=noCopyCreated;CHECK(!admissionReject(0x22,allocation,PG,"vm_span"));CHECK(noCopyCreated==before);
 CHECK(create(1,0x24,allocation,PG));CHECK(!host(1).coherent&&noCopyCreated==before);h_nvnMemoryPoolFinalize(&rawPools[1]);
 CHECK(!mprotect(allocation+4*PG,PG,PROT_READ));CHECK(create(2,0x24,allocation+4*PG,PG));CHECK(!host(2).coherent&&!host(2).noCopyDeferred&&host(2).buffer&&noCopyCreated==before);CHECK(!mprotect(allocation+4*PG,PG,PROT_READ|PROT_WRITE));h_nvnMemoryPoolFinalize(&rawPools[2]);
 CHECK(!mprotect(allocation+6*PG,PG,PROT_NONE));CHECK(create(3,0x24,allocation+6*PG,PG));CHECK(host(3).noCopyDeferred&&host(3).coherent&&!host(3).buffer&&noCopyCreated==before);CHECK(!poolTryResolve(host(3),0,8,0,&why)&&strstr(why,"deferred ordinary")&&noCopyCreated==before);
 CHECK(!mprotect(allocation+6*PG,PG,PROT_READ|PROT_WRITE));CHECK(poolTryResolve(host(3),0,8,0,&why)&&!host(3).noCopyDeferred&&noCopyCreated==before+1);h_nvnMemoryPoolFinalize(&rawPools[3]);return 0;
}
static int testDeferredQuarantineReentry(void){
 CHECK(!mprotect(allocation,PG,PROT_READ));CHECK(create(0,0x22,allocation,PG));BWPool *p=host(0);CHECK(p.noCopyDeferred&&!noCopyCreated);CHECK(!mprotect(allocation,PG,PROT_READ|PROT_WRITE));
 PoolFixtureDevice *device=(PoolFixtureDevice*)gpu;device.holdAllocationAt=1;device.throwAllocationAt=1;const char *failure=NULL;
 REJECT((void)poolEnsureOrdinaryBacking(p,&failure),"borrowed Metal backing");CHECK(p.retiring&&p.noCopyDeferred&&heldNoCopy&&noCopyCreated==1&&!noCopyReleased);
 id group=objc_getAssociatedObject(p,&poolBorrowedLifetimeKey);device.throwAllocationAt=device.holdAllocationAt=0;unsigned calls=(unsigned)device.allocationCalls;
 CHECK(!poolEnsureOrdinaryBacking(p,&failure)&&failure&&strstr(failure,"retiring"));CHECK(device.allocationCalls==calls&&objc_getAssociatedObject(p,&poolBorrowedLifetimeKey)==group);
 heldNoCopy=nil;poolShutdownPools();CHECK(noCopyReleased==1&&!p.live&&!pools.count);return 0;
}
static int testSubmapNoProgress(void){alarm(3);admissionReset();admissionVMMode=16;CHECK(!admissionReject(0x189,allocation,PG,"vm_submap_limit"));CHECK(admissionVMQueries==65);return 0;}
static int testPhysicalSuccessTrace(void){
 CHECK(!poolPhysicalSuccessTraceCount);unsigned before=admissionVMQueries;
 CHECK(!create(0,0x189,NULL,PG)&&!poolPhysicalSuccessTraceCount&&admissionVMQueries==before);
 textureTrackingAvailable=YES;dirtyAddFail=YES;CHECK(!create(0,0x189,allocation,PG));
 CHECK(!poolPhysicalSuccessTraceCount&&!pools.count&&!poolUnpublishedPools.count&&noCopyCreated==1&&noCopyReleased==1);
 textureTrackingAvailable=NO;dirtyAddFail=NO;before=admissionVMQueries;
 for(unsigned i=0;i<6;i++){CHECK(create(i,0x189,allocation,PG));CHECK(poolPhysicalSuccessTraceCount==MIN(i+1,4));h_nvnMemoryPoolFinalize(&rawPools[i]);}
 CHECK(admissionVMQueries==before+6&&noCopyCreated==7&&noCopyReleased==7);
 CHECK(create(6,0x22,allocation,PG)&&poolPhysicalSuccessTraceCount==4);h_nvnMemoryPoolFinalize(&rawPools[6]);
 CHECK(!pools.count&&!poolUnpublishedPools.count&&noCopyCreated==noCopyReleased);return 0;
}
int main(int argc,char **argv){@autoreleasepool{alarm(25);if(argc!=2)return 2;setup();int result=0;@try{
 if(!strcmp(argv[1],"physical-trace"))result=testPhysicalSuccessTrace();else
 if(!strcmp(argv[1],"segments-real"))result=testSegmentReal();else if(!strcmp(argv[1],"segments-metadata"))result=testSegmentMetadata();else if(!strcmp(argv[1],"segments-pinned"))result=testSegmentPinned(NO,NO);else if(!strcmp(argv[1],"segments-partial"))result=testSegmentPinned(YES,NO);else if(!strcmp(argv[1],"segments-dirty"))result=testSegmentPinned(NO,YES);else if(!strcmp(argv[1],"ordinary-deferred"))result=testOrdinaryDeferred();else if(!strcmp(argv[1],"deferred-quarantine-reentry"))result=testDeferredQuarantineReentry();else if(!strcmp(argv[1],"submap-no-progress"))result=testSubmapNoProgress();else
 if(!strcmp(argv[1],"raw46-identity"))result=testRaw46Identity();else if(!strcmp(argv[1],"raw46-gpu-refusal"))result=testRaw46GPURefusal();else if(!strcmp(argv[1],"depth-kinds"))result=testDepthKinds();else if(!strcmp(argv[1],"resolve"))result=testResolve();else if(!strcmp(argv[1],"resolve-negative"))result=testResolveNegative();else if(!strcmp(argv[1],"batch"))result=testBatch();else if(!strcmp(argv[1],"family"))result=testFamily();else if(!strcmp(argv[1],"mapping"))result=testMapping();else if(!strcmp(argv[1],"mapping-negative"))result=testMappingNegative();else if(!strcmp(argv[1],"initialize-negative"))result=testInitializeNegative();else if(!strcmp(argv[1],"finalize"))result=testFinalize();else if(!strcmp(argv[1],"provenance"))result=testProvenance();else if(!strcmp(argv[1],"cpu-access"))result=testCPUAccess();else if(!strcmp(argv[1],"gpu-records"))result=testGPURecords();else if(!strcmp(argv[1],"bounded-records"))result=testBoundedRecords();else if(!strcmp(argv[1],"retire-references"))result=testRetireReferences();else if(!strcmp(argv[1],"ordinary-protection"))result=testOrdinaryProtection();else if(!strcmp(argv[1],"borrowed-pinned"))result=testBorrowedPinned();else if(!strcmp(argv[1],"mapping-allocation-bounds"))result=testMappingAllocationBounds();else if(!strcmp(argv[1],"unpublished-cleanup"))result=testUnpublishedCleanup();else if(!strcmp(argv[1],"unpublished-pinned"))result=testUnpublishedPinned();else if(!strcmp(argv[1],"unpublished-retry"))result=testUnpublishedRetry(NO);else if(!strcmp(argv[1],"unpublished-register-failure"))result=testUnpublishedRetry(YES);else if(!strcmp(argv[1],"admission-logical"))result=testAdmissionLogical();else if(!strcmp(argv[1],"admission-write"))result=testAdmissionWrite();else if(!strcmp(argv[1],"admission-vm-query"))result=testAdmissionVMQuery();else if(!strcmp(argv[1],"admission-vm-region"))result=testAdmissionVMRegion();else if(!strcmp(argv[1],"admission-vm-protection"))result=testAdmissionVMProtection();else if(!strcmp(argv[1],"admission-vm-span"))result=testAdmissionVMSpan();else if(!strcmp(argv[1],"admission-rounding"))result=testAdmissionRounding();else return 2;
 if(!result){poolShutdownPools();CHECK(!munmap(allocation,allocationBytes));puts("POOL_ADAPTER_CPU_OK");}}
 @catch(NSException *e){fprintf(stderr,"unexpected %s [%s]\n",e.reason.UTF8String,lastError);return 92;}return result;}}
