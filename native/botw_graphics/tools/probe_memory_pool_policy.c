/* Synthetic CPU-only probe. Included after graphics.m by the test translation
 * unit so the query can use a registered host object without a Metal device. */
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

#define CHECK(x) do { if(!(x)){fprintf(stderr,"CHECK failed line %d: %s\n",__LINE__,#x);return 91;} } while(0)
static sigjmp_buf poolJump;
static const void *poolExpectedWrite;
static unsigned poolValidationCalls;
static int poolDenyWrite;
@interface PoolCPUOnlyDevice : NSObject
@property unsigned allocationCalls;
@end
@implementation PoolCPUOnlyDevice
- (NSUInteger)maxBufferLength{return NSUIntegerMax;}
- (id<MTLBuffer>)newBufferWithBytesNoCopy:(void*)bytes length:(NSUInteger)length options:(MTLResourceOptions)options deallocator:(void (^)(void *,NSUInteger))deallocator{
    (void)bytes;(void)length;(void)options;(void)deallocator;self.allocationCalls++;return nil;
}
@end
static void poolError(const char *reason){(void)reason;siglongjmp(poolJump,1);}
static int poolValidate(const void *at,size_t bytes,int write){
    poolValidationCalls++;
    return !poolDenyWrite&&at==poolExpectedWrite&&bytes==20&&write==1;
}
int main(int argc,char **argv){
    CHECK(argc==2);const char *mode=argv[1];
    errorCallback=poolError;
    NVNT_nvnMemoryPoolBuilderSetDefaults defaults=(NVNT_nvnMemoryPoolBuilderSetDefaults)botw_nvn_bootstrap_loader("nvnMemoryPoolBuilderSetDefaults");
    CHECK(defaults);
    if(!strcmp(mode,"abi")){
        CHECK(sizeof(NVNmappingRequest)==40);
        puts("POOL_REQUEST_ABI_40_OK");return 0;
    }
    if(!strcmp(mode,"query")){
        objects=[NSMutableDictionary new];objectSpans=[NSMutableDictionary new];
        BWDevice *host=[BWDevice new];NVNdevice device;memset(&device,0,sizeof device);put(&device,host);
        NVNT_nvnDeviceGetInteger query=(NVNT_nvnDeviceGetInteger)botw_nvn_bootstrap_loader("nvnDeviceGetInteger");
        int value=-1;query(&device,NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE,&value);CHECK(value==65536);
        CHECK(!gpu);puts("POOL_PAGE_QUERY_CPU_ONLY_65536_OK");return 0;
    }
    if(!strcmp(mode,"invalid-map")){
        NVNT_nvnMemoryPoolMapVirtual map=(NVNT_nvnMemoryPoolMapVirtual)botw_nvn_bootstrap_loader("nvnMemoryPoolMapVirtual");CHECK(map);
        if(!sigsetjmp(poolJump,1)){map(NULL,0,NULL);CHECK(0);}
        CHECK(strstr(botw_graphics_last_error(),"nvnMemoryPoolMapVirtual")&&strstr(botw_graphics_last_error(),"provenance"));CHECK(!gpu);
        puts("POOL_INVALID_MAPPING_ABI_REJECTED_BEFORE_GPU_OK");return 0;
    }
    if(!strcmp(mode,"physical-invalid-backing")||!strcmp(mode,"virtual-invalid-backing")){
        objects=[NSMutableDictionary new];objectSpans=[NSMutableDictionary new];
        NVNdevice device;memset(&device,0,sizeof device);put(&device,[BWDevice new]);
        PoolCPUOnlyDevice *synthetic=[PoolCPUOnlyDevice new];gpu=(id<MTLDevice>)synthetic;
        void *storage=mmap(NULL,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(storage!=MAP_FAILED);
        NVNmemoryPoolBuilder builder;memset(&builder,0,sizeof builder);PoolBuilder *fields=(PoolBuilder*)&builder;
        fields->device=&device;fields->memory=!strcmp(mode,"physical-invalid-backing")?(char*)storage+1:storage;fields->size=65536;fields->flags=!strcmp(mode,"physical-invalid-backing")?0x189:0x221;
        NVNmemoryPool pool;memset(&pool,0,sizeof pool);
        NVNT_nvnMemoryPoolInitialize initialize=(NVNT_nvnMemoryPoolInitialize)botw_nvn_bootstrap_loader("nvnMemoryPoolInitialize");CHECK(initialize);
        if(!sigsetjmp(poolJump,1)){initialize(&pool,&builder);CHECK(0);}
        CHECK(strstr(botw_graphics_last_error(),"physical/virtual memory pool flags/size/backing alignment"));CHECK(!synthetic.allocationCalls);CHECK(!((BWObject*)&pool)->serial);
        gpu=nil;CHECK(!munmap(storage,65536));puts("POOL_INVALID_MAPPING_ABI_REJECTED_BEFORE_GPU_OK");return 0;
    }
    if(!strcmp(mode,"overflow")){
        if(!sigsetjmp(poolJump,1)){defaults((NVNmemoryPoolBuilder*)(uintptr_t)(UINTPTR_MAX-15));CHECK(0);}
        CHECK(strstr(botw_graphics_last_error(),"nvnMemoryPoolBuilderSetDefaults"));
        CHECK(!gpu);puts("POOL_DEFAULTS_CPU_BOUNDED_IMMUTABLE_DEVICE_FLAGS21_OK");return 0;
    }
    size_t page=(size_t)getpagesize();unsigned char *allocation=mmap(NULL,2*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    CHECK(allocation!=MAP_FAILED);memset(allocation,0xa5,2*page);
    unsigned char *raw=!strcmp(mode,"bounded")?allocation+page-28:!strcmp(mode,"unreadable-device")?allocation+page-8:allocation+128;
    NVNdevice *device=(NVNdevice*)(uintptr_t)0x123456789abULL;memcpy(raw,&device,sizeof device);
    if(!strcmp(mode,"setter-order")){
        NVNT_nvnMemoryPoolBuilderSetDevice setDevice=(NVNT_nvnMemoryPoolBuilderSetDevice)botw_nvn_bootstrap_loader("nvnMemoryPoolBuilderSetDevice");
        NVNT_nvnMemoryPoolBuilderSetStorage setStorage=(NVNT_nvnMemoryPoolBuilderSetStorage)botw_nvn_bootstrap_loader("nvnMemoryPoolBuilderSetStorage");
        NVNT_nvnMemoryPoolBuilderSetFlags setFlags=(NVNT_nvnMemoryPoolBuilderSetFlags)botw_nvn_bootstrap_loader("nvnMemoryPoolBuilderSetFlags");
        setDevice((NVNmemoryPoolBuilder*)raw,device);setStorage((NVNmemoryPoolBuilder*)raw,allocation,65536);setFlags((NVNmemoryPoolBuilder*)raw,0x189);
    }
    unsigned char before[64];memcpy(before,raw,sizeof before);
    if(!strcmp(mode,"bounded"))CHECK(!mprotect(allocation+page,page,PROT_NONE));
    if(!strcmp(mode,"readonly"))CHECK(!mprotect(allocation,page,PROT_READ));
    if(!strcmp(mode,"none"))CHECK(!mprotect(allocation,page,PROT_NONE));
    if(!strcmp(mode,"unreadable-device"))CHECK(!mprotect(allocation,page,PROT_NONE));
    if(!strcmp(mode,"partial-readonly")){
        raw=allocation+page-20;memcpy(raw,&device,sizeof device);memcpy(before,raw,sizeof before);
        CHECK(!mprotect(allocation+page,page,PROT_READ));
    }
    if(!strcmp(mode,"validator")||!strcmp(mode,"validator-deny")){
        poolExpectedWrite=raw+8;poolDenyWrite=!strcmp(mode,"validator-deny");memoryAccessValidator=poolValidate;
    }
    int rejected=!strcmp(mode,"readonly")||!strcmp(mode,"none")||!strcmp(mode,"partial-readonly")||!strcmp(mode,"validator-deny")||!strcmp(mode,"null");
    if(rejected){
        if(!sigsetjmp(poolJump,1)){defaults(!strcmp(mode,"null")?NULL:(NVNmemoryPoolBuilder*)raw);CHECK(0);}
        CHECK(strstr(botw_graphics_last_error(),"nvnMemoryPoolBuilderSetDefaults")!=NULL);
        CHECK(!mprotect(allocation,page,PROT_READ|PROT_WRITE));CHECK(!memcmp(raw,before,sizeof before));
    }else{
        defaults((NVNmemoryPoolBuilder*)raw);
        if(!strcmp(mode,"bounded"))CHECK(!mprotect(allocation+page,page,PROT_READ|PROT_WRITE));
        if(!strcmp(mode,"unreadable-device"))CHECK(!mprotect(allocation,page,PROT_READ|PROT_WRITE));
        unsigned char expected[64];memcpy(expected,before,sizeof expected);memset(expected+8,0,16);int flags=0x21;memcpy(expected+24,&flags,4);
        CHECK(!memcmp(raw,expected,sizeof expected));
        for(size_t i=0;i<(size_t)(raw-allocation);i++)CHECK(allocation[i]==0xa5);
    }
    if(!strncmp(mode,"validator",9))CHECK(poolValidationCalls==1);
    CHECK(!gpu);CHECK(!munmap(allocation,2*page));puts("POOL_DEFAULTS_CPU_BOUNDED_IMMUTABLE_DEVICE_FLAGS21_OK");return 0;
}
