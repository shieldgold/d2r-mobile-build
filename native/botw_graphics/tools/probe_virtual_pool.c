/* Included after graphics.m in a synthetic-only Objective-C translation unit.
 * Real GPU commands consume owned test bytes; no guest image or diagnostics. */
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <dlfcn.h>
#define VP_CHECK(x) do{if(!(x)){fprintf(stderr,"VP CHECK line%d: %s (%s)\n",__LINE__,#x,botw_graphics_last_error());return 91;}}while(0)
static sigjmp_buf vpJump;
static _Thread_local int vpIsWorker;
static unsigned vpNotifications;
static void vpError(const char *reason){vpNotifications++;fprintf(stderr,"expected-or-fatal:%s\n",reason);if(vpIsWorker)abort();siglongjmp(vpJump,1);}
#define VP_API(n) static NVNT_##n vp_##n
VP_API(nvnDeviceBuilderSetDefaults);VP_API(nvnDeviceInitialize);
VP_API(nvnMemoryPoolBuilderSetDefaults);VP_API(nvnMemoryPoolBuilderSetDevice);VP_API(nvnMemoryPoolBuilderSetStorage);VP_API(nvnMemoryPoolBuilderSetFlags);VP_API(nvnMemoryPoolInitialize);VP_API(nvnMemoryPoolFinalize);VP_API(nvnMemoryPoolMap);VP_API(nvnMemoryPoolGetBufferAddress);VP_API(nvnMemoryPoolMapVirtual);
VP_API(nvnBufferBuilderSetDefaults);VP_API(nvnBufferBuilderSetDevice);VP_API(nvnBufferBuilderSetStorage);VP_API(nvnBufferInitialize);VP_API(nvnBufferGetAddress);VP_API(nvnBufferMap);
VP_API(nvnQueueBuilderSetDefaults);VP_API(nvnQueueBuilderSetDevice);VP_API(nvnQueueInitialize);VP_API(nvnQueueSubmitCommands);VP_API(nvnQueueFinish);
VP_API(nvnCommandBufferInitialize);VP_API(nvnCommandBufferBeginRecording);VP_API(nvnCommandBufferEndRecording);VP_API(nvnCommandBufferCopyBufferToBuffer);
#undef VP_API
#define VP_LOAD(n) vp_##n=(NVNT_##n)botw_nvn_bootstrap_loader(#n)
static int vpPool(NVNmemoryPool *pool,NVNdevice *device,void *memory,size_t bytes,int flags){
    NVNmemoryPoolBuilder b;memset(&b,0,sizeof b);vp_nvnMemoryPoolBuilderSetDefaults(&b);vp_nvnMemoryPoolBuilderSetDevice(&b,device);vp_nvnMemoryPoolBuilderSetStorage(&b,memory,bytes);vp_nvnMemoryPoolBuilderSetFlags(&b,flags);return vp_nvnMemoryPoolInitialize(pool,&b);
}
static int vpMap(NVNmemoryPool *virt,NVNmemoryPool *physical,uint64_t poff,uint64_t voff,uint64_t bytes,int storageClass){
    NVNmappingRequest request={physical,poff,voff,bytes,storageClass,0};return vp_nvnMemoryPoolMapVirtual(virt,1,&request);
}
static NVNcommandHandle vpCopy(NVNcommandBuffer *command,uint64_t source,uint64_t destination,size_t bytes){
    vp_nvnCommandBufferBeginRecording(command);vp_nvnCommandBufferCopyBufferToBuffer(command,source,destination,bytes,0);return vp_nvnCommandBufferEndRecording(command);
}
static unsigned vpOwnedVMLeaves(void *memory,size_t bytes){
    uintptr_t at=(uintptr_t)memory,end=at+bytes;unsigned leaves=0;
    while(at<end){natural_t depth=0;vm_address_t region=at;vm_size_t span=0;vm_region_submap_info_data_64_t info={0};
        for(;;){mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;
            if(vm_region_recurse_64(mach_task_self(),&region,&span,&depth,(vm_region_recurse_info_t)&info,&count)!=KERN_SUCCESS||count<VM_REGION_SUBMAP_INFO_COUNT_64||region>at||!span||at-region>=span)return 0;
            if(info.is_submap){depth++;region=at;continue;}break;}
        at+=MIN((size_t)(end-at),(size_t)(span-(at-region)));leaves++;
    }return leaves;
}
/* Delegates to the real device. Every admission is checked against actual VM
 * metadata before asking Metal; failure/retention are explicit test controls. */
@interface VPSegmentDevice:NSObject
@property(strong) id<MTLDevice> real;
@property NSUInteger calls,created,released,failAt,holdAt;
@property(strong) id<MTLBuffer> held;
@end
@implementation VPSegmentDevice
- (id)forwardingTargetForSelector:(SEL)selector{return self.real;}
- (id<MTLBuffer>)newBufferWithBytesNoCopy:(void*)bytes length:(NSUInteger)length options:(MTLResourceOptions)options deallocator:(void(^)(void*,NSUInteger))deallocator{
    self.calls++;if(self.calls==self.failAt)return nil;
    if(vpOwnedVMLeaves(bytes,length)!=1)@throw [NSException exceptionWithName:@"InvalidFixtureAdmission" reason:@"NoCopy request was not one actual VM leaf" userInfo:nil];
    __weak VPSegmentDevice *weak=self;
    id<MTLBuffer> buffer=[self.real newBufferWithBytesNoCopy:bytes length:length options:options deallocator:^(void *memory,NSUInteger size){weak.released++;if(deallocator)deallocator(memory,size);}];
    if(buffer)self.created++;if(self.calls==self.holdAt)self.held=buffer;return buffer;
}
@end
/* A real shared-event wait keeps the command in flight until this fixture
 * explicitly releases it. Finalize runs on a cooperating bounded worker. */
@interface VPGatedQueue : NSObject
@property(strong) id<MTLCommandQueue> queue;
@property(strong) id<MTLSharedEvent> event;
@end
@implementation VPGatedQueue
- (id<MTLCommandBuffer>)commandBuffer{id<MTLCommandBuffer> cb=[self.queue commandBuffer];[cb encodeWaitForEvent:self.event value:1];return cb;}
@end
@interface VPFailingEncoder : NSObject
@property(strong) id<MTLRenderCommandEncoder> real;
@end
@implementation VPFailingEncoder
- (id)forwardingTargetForSelector:(SEL)selector{return self.real;}
- (void)setFragmentBuffer:(id<MTLBuffer>)buffer offset:(NSUInteger)offset atIndex:(NSUInteger)index{(void)buffer;(void)offset;(void)index;@throw [NSException exceptionWithName:@"SyntheticEncoderFailure" reason:@"synthetic encoder failure after actual resource capture" userInfo:nil];}
@end
@interface VPFailingBlit : NSObject
@property(strong) id<MTLBlitCommandEncoder> real;
@end
@implementation VPFailingBlit
- (id)forwardingTargetForSelector:(SEL)selector{return self.real;}
- (void)copyFromBuffer:(id<MTLBuffer>)source sourceOffset:(NSUInteger)sourceOffset toBuffer:(id<MTLBuffer>)destination destinationOffset:(NSUInteger)destinationOffset size:(NSUInteger)bytes{
    [self.real copyFromBuffer:source sourceOffset:sourceOffset toBuffer:destination destinationOffset:destinationOffset size:bytes];
    @throw [NSException exceptionWithName:@"SyntheticBlitFailure" reason:@"synthetic blit failure after actual resource capture" userInfo:nil];
}
@end
@interface VPFailingBuffer : NSObject
@property(strong) id<MTLCommandBuffer> real;
@end
@implementation VPFailingBuffer
- (id)forwardingTargetForSelector:(SEL)selector{return self.real;}
- (id<MTLBlitCommandEncoder>)blitCommandEncoder{VPFailingBlit *encoder=[VPFailingBlit new];encoder.real=[self.real blitCommandEncoder];return (id<MTLBlitCommandEncoder>)encoder;}
- (id<MTLRenderCommandEncoder>)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor*)descriptor{VPFailingEncoder *encoder=[VPFailingEncoder new];encoder.real=[self.real renderCommandEncoderWithDescriptor:descriptor];return (id<MTLRenderCommandEncoder>)encoder;}
@end
@interface VPFailingQueue : NSObject
@property(strong) id<MTLCommandQueue> real;
@end
@implementation VPFailingQueue
- (id<MTLCommandBuffer>)commandBuffer{VPFailingBuffer *buffer=[VPFailingBuffer new];buffer.real=[self.real commandBuffer];return (id<MTLCommandBuffer>)buffer;}
@end
struct VPFinalizeJob {NVNmemoryPool *pool,*virtualPool,*other;pthread_mutex_t lock;pthread_cond_t changed;int done,remap,result;};
static void *vpFinalizeWorker(void *raw){struct VPFinalizeJob *job=raw;vpIsWorker=1;@autoreleasepool{if(job->remap)job->result=vpMap(job->virtualPool,job->other,0,0,131072,0x100);else{vp_nvnMemoryPoolFinalize(job->pool);job->result=1;}}pthread_mutex_lock(&job->lock);job->done=1;pthread_cond_signal(&job->changed);pthread_mutex_unlock(&job->lock);return NULL;}
static int vpWaitFinalize(struct VPFinalizeJob *job,pthread_t thread){struct timespec deadline;clock_gettime(CLOCK_REALTIME,&deadline);deadline.tv_sec+=5;pthread_mutex_lock(&job->lock);while(!job->done){int status=pthread_cond_timedwait(&job->changed,&job->lock,&deadline);if(status){pthread_mutex_unlock(&job->lock);return 0;}}pthread_mutex_unlock(&job->lock);return !pthread_join(thread,NULL);}
int main(int argc,char **argv){
    alarm(30);VP_CHECK(argc==2);const char *mode=argv[1];
    if(sigsetjmp(vpJump,1)){fprintf(stderr,"unexpected:%s\n",botw_graphics_last_error());return 92;}
    if(!botw_graphics_init(NULL,vpError,NULL))return 77;
    VP_LOAD(nvnDeviceBuilderSetDefaults);VP_LOAD(nvnDeviceInitialize);
    VP_LOAD(nvnMemoryPoolBuilderSetDefaults);VP_LOAD(nvnMemoryPoolBuilderSetDevice);VP_LOAD(nvnMemoryPoolBuilderSetStorage);VP_LOAD(nvnMemoryPoolBuilderSetFlags);VP_LOAD(nvnMemoryPoolInitialize);VP_LOAD(nvnMemoryPoolFinalize);VP_LOAD(nvnMemoryPoolMap);VP_LOAD(nvnMemoryPoolGetBufferAddress);VP_LOAD(nvnMemoryPoolMapVirtual);
    VP_LOAD(nvnBufferBuilderSetDefaults);VP_LOAD(nvnBufferBuilderSetDevice);VP_LOAD(nvnBufferBuilderSetStorage);VP_LOAD(nvnBufferInitialize);VP_LOAD(nvnBufferGetAddress);VP_LOAD(nvnBufferMap);
    VP_LOAD(nvnQueueBuilderSetDefaults);VP_LOAD(nvnQueueBuilderSetDevice);VP_LOAD(nvnQueueInitialize);VP_LOAD(nvnQueueSubmitCommands);VP_LOAD(nvnQueueFinish);
    VP_LOAD(nvnCommandBufferInitialize);VP_LOAD(nvnCommandBufferBeginRecording);VP_LOAD(nvnCommandBufferEndRecording);VP_LOAD(nvnCommandBufferCopyBufferToBuffer);
    size_t page=65536;unsigned char *p=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0),*q=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0),*out=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    VP_CHECK(p!=MAP_FAILED&&q!=MAP_FAILED&&out!=MAP_FAILED);for(size_t i=0;i<page*2;i++){p[i]=(unsigned char)(i*13+7);q[i]=(unsigned char)(i*17+29);}memset(out,0xa5,page*2);
    if(!strcmp(mode,"nocopy-entry-metadata")){
        unsigned before=vpOwnedVMLeaves(p,page*2);VP_CHECK(before==1);dispatch_group_t released=dispatch_group_create();dispatch_group_enter(released);__weak id<MTLBuffer> weak;
        unsigned during=0;@autoreleasepool{id<MTLBuffer> resource=[gpu newBufferWithBytesNoCopy:p+getpagesize() length:page options:MTLResourceStorageModeShared deallocator:^(void *bytes,NSUInteger length){(void)bytes;(void)length;dispatch_group_leave(released);}];VP_CHECK(resource);weak=resource;during=vpOwnedVMLeaves(p,page*2);resource=nil;}
        VP_CHECK(!dispatch_group_wait(released,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC))&&!weak);unsigned after=vpOwnedVMLeaves(p,page*2);VP_CHECK(during&&after);
        fprintf(stderr,"NOCOPY_DRIVER_VM_ENTRIES before=%u during=%u after_release=%u actual_deallocator_ack=1\n",before,during,after);botw_graphics_shutdown();VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));puts("VIRTUAL_POOL_REAL_GPU_nocopy-entry-metadata_OK");return 0;
    }
    if(!strcmp(mode,"graphics-copy-coherence")){
        size_t offset=(size_t)getpagesize()/2;textureReadBytes(q+offset,p+offset,page,"graphics bulk read before NoCopy");textureWriteBytes(p+offset,q+offset,page,"graphics bulk write before NoCopy");VP_CHECK(!textureWriteRun(p,offset,q+offset,page));
        unsigned pLeaves=vpOwnedVMLeaves(p,page*2),qLeaves=vpOwnedVMLeaves(q,page*2);fprintf(stderr,"GRAPHICS_COPY_BEFORE_NOCOPY p_leaves=%u q_leaves=%u\n",pLeaves,qLeaves);VP_CHECK(pLeaves==1&&qLeaves==1);
    }
    BOOL segmented=!strncmp(mode,"segments-",9);size_t hostPage=(size_t)getpagesize();
    if(segmented){VP_CHECK(vm_inherit(mach_task_self(),(vm_address_t)(p+hostPage),hostPage,VM_INHERIT_NONE)==KERN_SUCCESS);VP_CHECK(vpOwnedVMLeaves(p,page*2)==3);}
    NVNdeviceBuilder db;NVNdevice device;vp_nvnDeviceBuilderSetDefaults(&db);VP_CHECK(vp_nvnDeviceInitialize(&device,&db));
    if(!strcmp(mode,"physical-trace")){
        NVNmemoryPool traced;unsigned notifications=vpNotifications;
        for(unsigned i=0;i<6;i++){VP_CHECK(vpPool(&traced,&device,p,page,0x189));vp_nvnMemoryPoolFinalize(&traced);}
        VP_CHECK(vpNotifications==notifications);
        if(!sigsetjmp(vpJump,1)){vpPool(&traced,&device,NULL,page,0x189);VP_CHECK(0);}
        VP_CHECK(vpNotifications==notifications+1);
        char error[512];VP_CHECK(botw_graphics_shutdown_checked(error,sizeof error)&&!error[0]);
        VP_CHECK(botw_graphics_init(NULL,vpError,NULL));vp_nvnDeviceBuilderSetDefaults(&db);VP_CHECK(vp_nvnDeviceInitialize(&device,&db));
        VP_CHECK(vpPool(&traced,&device,p,page,0x189));vp_nvnMemoryPoolFinalize(&traced);
        VP_CHECK(vpNotifications==notifications+1&&botw_graphics_shutdown_checked(error,sizeof error)&&!error[0]);
        VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));puts("VIRTUAL_POOL_REAL_GPU_physical-trace_OK");return 0;
    }
    NVNmemoryPool physical,other,virtualPool,destination;VPSegmentDevice *segmentDevice=nil;
    if(segmented){segmentDevice=[VPSegmentDevice new];segmentDevice.real=gpu;gpu=(id<MTLDevice>)segmentDevice;}
    if(!strcmp(mode,"segments-partial-nil")){
        segmentDevice.holdAt=1;segmentDevice.failAt=2;
        if(!sigsetjmp(vpJump,1)){vpPool(&physical,&device,p,page*2,0x189);VP_CHECK(0);}
        gpu=segmentDevice.real;VP_CHECK(strstr(botw_graphics_last_error(),"borrowed")&&segmentDevice.created==1&&!segmentDevice.released&&poolUnpublishedPools.count==1);
        int (*checked)(char*,size_t)=dlsym(RTLD_DEFAULT,"botw_graphics_shutdown_checked");VP_CHECK(checked);char error[512];unsigned notifications=vpNotifications;VP_CHECK(!checked(error,sizeof error)&&strstr(error,"borrowed")&&vpNotifications==notifications);
        segmentDevice.held=nil;VP_CHECK(checked(error,sizeof error)&&segmentDevice.released==1&&!poolUnpublishedPools.count);
        VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));puts("VIRTUAL_POOL_REAL_GPU_segments-partial-nil_OK");return 0;
    }
    VP_CHECK(vpPool(&physical,&device,p,page*2,0x189));if(segmented){gpu=segmentDevice.real;VP_CHECK(segmentDevice.created==3&&((BWPool*)get(&physical,[BWPool class])).segments.count==3);}
    VP_CHECK(vpPool(&other,&device,q,page*2,0x189));VP_CHECK(vpPool(&virtualPool,&device,NULL,page*2,0x221));VP_CHECK(vpPool(&destination,&device,out,page*2,0x22));
    VP_CHECK(!vp_nvnMemoryPoolMap(&physical)&&!vp_nvnMemoryPoolGetBufferAddress(&physical)&&!vp_nvnMemoryPoolMap(&virtualPool));
    uint64_t base=vp_nvnMemoryPoolGetBufferAddress(&virtualPool);VP_CHECK(base&&base!=((uintptr_t)p)&&base!=((uintptr_t)q));
    VP_CHECK(vpMap(&virtualPool,&physical,0,0,page*2,!strcmp(mode,"fe-buffer")?0x1fe:0x100));
    NVNbufferBuilder bb;NVNbuffer buffer;memset(&bb,0,sizeof bb);vp_nvnBufferBuilderSetDefaults(&bb);vp_nvnBufferBuilderSetDevice(&bb,&device);vp_nvnBufferBuilderSetStorage(&bb,&virtualPool,0,page*2);VP_CHECK(vp_nvnBufferInitialize(&buffer,&bb));VP_CHECK(vp_nvnBufferGetAddress(&buffer)==base&&!vp_nvnBufferMap(&buffer));
    NVNqueueBuilder qb;NVNqueue queue;vp_nvnQueueBuilderSetDefaults(&qb);vp_nvnQueueBuilderSetDevice(&qb,&device);VP_CHECK(vp_nvnQueueInitialize(&queue,&qb));NVNcommandBuffer command;VP_CHECK(vp_nvnCommandBufferInitialize(&command,&device));
    size_t bytes=512;NVNcommandHandle handle=vpCopy(&command,base,(uintptr_t)out,bytes);
    if(!strcmp(mode,"copy-encode-error")){
        BWQueue *object=get(&queue,[BWQueue class]);VPFailingQueue *failing=[VPFailingQueue new];failing.real=object.native;object.native=(id<MTLCommandQueue>)failing;
        if(!sigsetjmp(vpJump,1)){vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(0);}VP_CHECK(strstr(botw_graphics_last_error(),"synthetic blit failure"));vp_nvnQueueFinish(&queue);dispatch_group_wait(allGPU,DISPATCH_TIME_FOREVER);
        int (*checked)(char*,size_t)=dlsym(RTLD_DEFAULT,"botw_graphics_shutdown_checked");VP_CHECK(checked);char error[512];VP_CHECK(checked(error,sizeof error)&&!error[0]);for(size_t i=0;i<page*2;i++)VP_CHECK(out[i]==0xa5);
        VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));puts("VIRTUAL_POOL_REAL_GPU_copy-encode-error_OK");return 0;
    }
    if(!strcmp(mode,"checked-shutdown")){
        VP_CHECK(dlsym(RTLD_DEFAULT,"botw_graphics_init"));
        int (*checked)(char*,size_t)=dlsym(RTLD_DEFAULT,"botw_graphics_shutdown_checked");VP_CHECK(checked);
        char nested[64];graphicsBegin();pendingError=YES;pendingFatal=YES;const char *entry=currentEntry;
        int nestedResult=checked(nested,sizeof nested);VP_CHECK(!nestedResult&&pendingError&&pendingFatal&&currentEntry==entry);pendingError=NO;pendingFatal=NO;graphicsEnd();
        id<MTLBuffer> retained=((BWPool*)get(&physical,[BWPool class])).buffer;unsigned before=vpNotifications;
        char bounded[12];memset(bounded,0xa5,sizeof bounded);int result=checked(bounded+2,8);
        VP_CHECK(!result&&gpu&&objects&&pools.count&&vpNotifications==before&&((unsigned char)bounded[0])==0xa5&&((unsigned char)bounded[1])==0xa5&&((unsigned char)bounded[10])==0xa5&&((unsigned char)bounded[11])==0xa5&&!bounded[9]);
        VP_CHECK(strstr(botw_graphics_last_error(),"borrowed")&&retained);retained=nil;char error[512];VP_CHECK(checked(error,sizeof error)&&!error[0]&&!gpu&&!objects&&!pools&&vpNotifications==before);
        VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));puts("VIRTUAL_POOL_REAL_GPU_checked-shutdown_OK");return 0;
    }
    if(!strcmp(mode,"sampler-device")){
#define SD_API(n) NVNT_##n n=(NVNT_##n)botw_nvn_bootstrap_loader(#n)
        SD_API(nvnSamplerBuilderSetDefaults);SD_API(nvnSamplerBuilderSetDevice);SD_API(nvnSamplerInitialize);SD_API(nvnSamplerFinalize);SD_API(nvnSamplerPoolInitialize);SD_API(nvnSamplerPoolRegisterSampler);
        NVNdevice foreign;VP_CHECK(vp_nvnDeviceInitialize(&foreign,&db));NVNsamplerPool descriptors;VP_CHECK(nvnSamplerPoolInitialize(&descriptors,&destination,8192,16));
        NVNsamplerBuilder builder;NVNsampler bad,good;nvnSamplerBuilderSetDefaults(&builder);nvnSamplerBuilderSetDevice(&builder,&foreign);VP_CHECK(nvnSamplerInitialize(&bad,&builder));
        if(!sigsetjmp(vpJump,1)){nvnSamplerPoolRegisterSampler(&descriptors,3,&bad);VP_CHECK(0);}VP_CHECK(strstr(botw_graphics_last_error(),"device"));
        nvnSamplerBuilderSetDevice(&builder,&device);VP_CHECK(nvnSamplerInitialize(&good,&builder));nvnSamplerPoolRegisterSampler(&descriptors,3,&good);BWDescriptors *pool=get(&descriptors,[BWDescriptors class]);id entry=descriptorEntry(pool,3);VP_CHECK(entry);nvnSamplerFinalize(&good);VP_CHECK(descriptorEntry(pool,3)==entry);
#undef SD_API
    }
    void *cachedAllocation=NULL;int copiedCode=!strcmp(mode,"copied-code");
    int encodeError=!strcmp(mode,"encode-error"),shortUniform=!strcmp(mode,"short-uniform-preflight");
    BOOL segmentedShader=!strcmp(mode,"segments-shader")||!strcmp(mode,"segments-linear")||!strcmp(mode,"segments-vertex-cross")||!strcmp(mode,"segments-uniform-cross")||!strcmp(mode,"segments-storage-cross")||!strcmp(mode,"segments-linear-cross");
    BOOL segmentShaderFailure=segmentedShader&&strstr(mode,"-cross");
    if(!strcmp(mode,"shader-buffers")||encodeError||shortUniform||copiedCode||segmentedShader){
#define SB_API(n) NVNT_##n n=(NVNT_##n)botw_nvn_bootstrap_loader(#n)
        SB_API(nvnTextureBuilderSetDefaults);SB_API(nvnTextureBuilderSetDevice);SB_API(nvnTextureBuilderSetSize2D);SB_API(nvnTextureBuilderSetFormat);SB_API(nvnTextureBuilderSetFlags);SB_API(nvnTextureBuilderSetStorage);SB_API(nvnTextureBuilderSetStride);SB_API(nvnTextureInitialize);
        SB_API(nvnMemoryPoolFlushMappedRange);SB_API(nvnCommandBufferReportCounter);SB_API(nvnProgramInitialize);SB_API(nvnProgramSetShaders);SB_API(nvnCommandBufferSetRenderTargets);SB_API(nvnCommandBufferBindProgram);SB_API(nvnCommandBufferDrawArrays);
        SB_API(nvnVertexAttribStateSetDefaults);SB_API(nvnVertexAttribStateSetFormat);SB_API(nvnVertexAttribStateSetStreamIndex);SB_API(nvnVertexStreamStateSetDefaults);SB_API(nvnVertexStreamStateSetStride);SB_API(nvnCommandBufferBindVertexAttribState);SB_API(nvnCommandBufferBindVertexStreamState);SB_API(nvnCommandBufferBindVertexBuffer);SB_API(nvnCommandBufferBindUniformBuffer);SB_API(nvnCommandBufferBindStorageBuffer);
        float positions[]={-1,-1,3,-1,-1,3},red[]={1,0,0,1},green[]={0,1,0,1};memcpy(p,positions,sizeof positions);memcpy(q,positions,sizeof positions);memcpy(p+256,red,sizeof red);memcpy(q+256,green,sizeof green);
        unsigned char controls[2][64]={{0}},codes[2][128]={{0}};for(unsigned i=0;i<2;i++){*(uint32_t*)controls[i]=0x98761234;*(uint32_t*)codes[i]=0x12345678;controls[i][8]=codes[i][8]=(unsigned char)(0x51+i);memcpy(out+page+i*128,codes[i],128);}
        const char *msl="#include <metal_stdlib>\nusing namespace metal;struct I{float2 p [[attribute(0)]];};vertex float4 vs(I v [[stage_in]],uint id [[vertex_id]],device uint* out [[buffer(2)]]){out[id]=0x600u+id;return float4(v.p,0,1);}fragment float4 fs(constant float4& c [[buffer(1)]]){return c;}";
        botw_graphics_shader_binding store={BOTW_GRAPHICS_STORAGE,3,2},uniform={BOTW_GRAPHICS_UNIFORM,5,1};VP_CHECK(botw_graphics_register_shader_with_bindings(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs",&store,1));VP_CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,msl,"fs",&uniform,1));
        NVNmemoryPool cachedPool;unsigned char *cached=NULL;uint64_t codeBase=(uintptr_t)out;
        if(copiedCode){cachedAllocation=malloc(page*2+1);VP_CHECK(cachedAllocation);cached=(unsigned char*)cachedAllocation+1;memset(cached,0x6c,page*2);VP_CHECK(vpPool(&cachedPool,&device,cached,page*2,0x24));BWPool *object=get(&cachedPool,[BWPool class]);VP_CHECK(!object.coherent);codeBase=vp_nvnMemoryPoolGetBufferAddress(&cachedPool);NVNcommandHandle producer=vpCopy(&command,(uintptr_t)out+page,codeBase+page,256);vp_nvnQueueSubmitCommands(&queue,1,&producer);}
        NVNprogram program;VP_CHECK(nvnProgramInitialize(&program,&device));NVNshaderData shaders[]={{codeBase+page,controls[0]},{codeBase+page+128,controls[1]}};
        int bound=0;if(!sigsetjmp(vpJump,1))bound=nvnProgramSetShaders(&program,2,shaders);if(copiedCode){BWPool *object=get(&cachedPool,[BWPool class]);uint32_t cpuWord,gpuWord;memcpy(&cpuWord,cached+page,4);memcpy(&gpuWord,(char*)object.buffer.contents+object.bufferOffset+page,4);fprintf(stderr,"COPIED_CODE bound=%d cpu=0x%x gpu=0x%x\n",bound,cpuWord,gpuWord);VP_CHECK(cpuWord==0x6c6c6c6c&&gpuWord==0x12345678);}VP_CHECK(bound);
        NVNtextureBuilder tb;NVNtexture target;memset(&tb,0,sizeof tb);nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,4,4);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetFlags(&tb,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);nvnTextureBuilderSetStride(&tb,128);nvnTextureBuilderSetStorage(&tb,(!strcmp(mode,"segments-linear")||!strcmp(mode,"segments-linear-cross"))?&virtualPool:&destination,!strcmp(mode,"segments-linear-cross")?(ptrdiff_t)(hostPage-256):!strcmp(mode,"segments-linear")?(ptrdiff_t)hostPage:0);VP_CHECK(nvnTextureInitialize(&target,&tb));
        NVNvertexAttribState attribute;NVNvertexStreamState stream;nvnVertexAttribStateSetDefaults(&attribute);nvnVertexAttribStateSetFormat(&attribute,NVN_FORMAT_RG32F,0);nvnVertexAttribStateSetStreamIndex(&attribute,0);nvnVertexStreamStateSetDefaults(&stream);nvnVertexStreamStateSetStride(&stream,8);
        const NVNtexture *targets[]={&target};vp_nvnCommandBufferBeginRecording(&command);if(shortUniform)nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,(uintptr_t)out+page+4096);nvnCommandBufferSetRenderTargets(&command,1,targets,NULL,NULL,NULL);nvnCommandBufferBindProgram(&command,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferBindVertexAttribState(&command,1,&attribute);nvnCommandBufferBindVertexStreamState(&command,1,&stream);nvnCommandBufferBindVertexBuffer(&command,0,base+(!strcmp(mode,"segments-vertex-cross")?hostPage-8:0),sizeof positions);nvnCommandBufferBindUniformBuffer(&command,NVN_SHADER_STAGE_FRAGMENT,5,base+(!strcmp(mode,"segments-uniform-cross")?hostPage-8:256),shortUniform?4:16);nvnCommandBufferBindStorageBuffer(&command,NVN_SHADER_STAGE_VERTEX,3,base+(!strcmp(mode,"segments-storage-cross")?hostPage-8:4096),12);nvnCommandBufferDrawArrays(&command,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=vp_nvnCommandBufferEndRecording(&command);
        if(encodeError){BWQueue *object=get(&queue,[BWQueue class]);VPFailingQueue *failing=[VPFailingQueue new];failing.real=object.native;object.native=(id<MTLCommandQueue>)failing;}
        BWQueue *probeQueue=get(&queue,[BWQueue class]);NSData *arenaBefore=(shortUniform||segmentShaderFailure)?digest(probeQueue.arena,probeQueue.arenaBytes):nil;NSData *outputBefore=segmentShaderFailure?digest(out,page*2):nil;
        if(encodeError||shortUniform||segmentShaderFailure){if(!sigsetjmp(vpJump,1)){vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(0);}VP_CHECK(strstr(botw_graphics_last_error(),encodeError?"synthetic encoder failure":segmentShaderFailure?"crosses Metal backing segments":"uniform range"));vp_nvnQueueFinish(&queue);dispatch_group_wait(allGPU,DISPATCH_TIME_FOREVER);if(segmentShaderFailure){VP_CHECK([outputBefore isEqual:digest(out,page*2)]&&[arenaBefore isEqual:digest(probeQueue.arena,probeQueue.arenaBytes)]);}if(shortUniform){for(unsigned i=0;i<16;i++)VP_CHECK(out[page+4096+i]==0xa5);VP_CHECK([arenaBefore isEqual:digest(probeQueue.arena,probeQueue.arenaBytes)]);}}
        NVNqueue second;VP_CHECK(vp_nvnQueueInitialize(&second,&qb));for(unsigned pass=0;!encodeError&&!shortUniform&&!segmentShaderFailure&&pass<(copiedCode?1u:2u);pass++){if(pass)VP_CHECK(vpMap(&virtualPool,&other,0,0,page*2,0x100));vp_nvnQueueSubmitCommands(pass?&second:&queue,1,&handle);vp_nvnQueueFinish(pass?&second:&queue);unsigned char pixels[64];VP_CHECK(botw_graphics_read_texture(&target,pixels,sizeof pixels));for(unsigned i=0;i<16;i++)VP_CHECK(pixels[i*4]==(pass?0:255)&&pixels[i*4+1]==(pass?255:0)&&!pixels[i*4+2]&&pixels[i*4+3]==255);uint32_t *written=(void*)((pass?q:p)+4096);for(unsigned i=0;i<3;i++)VP_CHECK(written[i]==0x600+i);}
        if(copiedCode){memset(cached+page,0x7e,128);vp_nvnQueueSubmitCommands(&queue,1,&handle);vp_nvnQueueFinish(&queue);unsigned char pixels[64];VP_CHECK(botw_graphics_read_texture(&target,pixels,sizeof pixels)&&pixels[0]==255&&!pixels[1]);nvnMemoryPoolFlushMappedRange(&cachedPool,page,128);if(!sigsetjmp(vpJump,1)){vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(0);}VP_CHECK(strstr(botw_graphics_last_error(),"program code bytes changed"));vp_nvnQueueFinish(&queue);dispatch_group_wait(allGPU,DISPATCH_TIME_FOREVER);VP_CHECK(botw_graphics_read_texture(&target,pixels,sizeof pixels)&&pixels[0]==255&&!pixels[1]);}
#undef SB_API
    }
    if(encodeError||shortUniform||segmentShaderFailure){int (*checked)(char*,size_t)=dlsym(RTLD_DEFAULT,"botw_graphics_shutdown_checked");VP_CHECK(checked);char error[512];int result=checked(error,sizeof error);fprintf(stderr,"CHECKED_SHUTDOWN_AFTER_ERROR=%d %s\n",result,error);VP_CHECK(result);VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));printf("VIRTUAL_POOL_REAL_GPU_%s_OK\n",mode);return 0;}
    int physicalRetired=0;

    if(!strcmp(mode,"segments-copy")||!strcmp(mode,"segments-preflight")||!strcmp(mode,"segments-counter-preflight")){
        if(!strcmp(mode,"segments-copy")){
            size_t offsets[]={0,hostPage,2*hostPage};for(unsigned i=0;i<3;i++){handle=vpCopy(&command,base+offsets[i],(uintptr_t)out+offsets[i],512);vp_nvnQueueSubmitCommands(&queue,1,&handle);}vp_nvnQueueFinish(&queue);for(unsigned i=0;i<3;i++)VP_CHECK(!memcmp(out+offsets[i],p+offsets[i],512));
            BWPool *object=get(&physical,[BWPool class]);__weak id first=((BWPoolSegment*)object.segments[0]).buffer,second=((BWPoolSegment*)object.segments[1]).buffer,third=((BWPoolSegment*)object.segments[2]).buffer;
            vp_nvnMemoryPoolFinalize(&physical);physicalRetired=1;VP_CHECK(!first&&!second&&!third&&segmentDevice.released==3);VP_CHECK(!munmap(p,page*2));p=MAP_FAILED;
        }else{
            vp_nvnCommandBufferBeginRecording(&command);vp_nvnCommandBufferCopyBufferToBuffer(&command,base,(uintptr_t)out,512,0);
            if(!strcmp(mode,"segments-counter-preflight")){NVNT_nvnCommandBufferReportCounter counter=(NVNT_nvnCommandBufferReportCounter)botw_nvn_bootstrap_loader("nvnCommandBufferReportCounter");counter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,(uintptr_t)out+page);}
            vp_nvnCommandBufferCopyBufferToBuffer(&command,base+hostPage-8,(uintptr_t)out+1024,16,0);handle=vp_nvnCommandBufferEndRecording(&command);
            BWQueue *object=get(&queue,[BWQueue class]);NSData *arena=digest(object.arena,object.arenaBytes);if(!sigsetjmp(vpJump,1)){vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(0);}VP_CHECK(strstr(botw_graphics_last_error(),"crosses Metal backing segments"));vp_nvnQueueFinish(&queue);dispatch_group_wait(allGPU,DISPATCH_TIME_FOREVER);VP_CHECK([arena isEqual:digest(object.arena,object.arenaBytes)]);for(size_t i=0;i<page*2;i++)VP_CHECK(out[i]==0xa5);
        }
    }else if(!strcmp(mode,"segments-checked-shutdown")){
        BWPool *object=get(&physical,[BWPool class]);__attribute__((objc_precise_lifetime)) id retained=((BWPoolSegment*)object.segments[1]).buffer;
        int (*checked)(char*,size_t)=dlsym(RTLD_DEFAULT,"botw_graphics_shutdown_checked");char error[512];VP_CHECK(checked&&!checked(error,sizeof error)&&strstr(error,"borrowed")&&segmentDevice.released==2&&object.registered&&object.live&&object.retiring);
        retained=nil;VP_CHECK(checked(error,sizeof error)&&segmentDevice.released==3&&!gpu);VP_CHECK(!munmap(p,page*2)&&!munmap(q,page*2)&&!munmap(out,page*2));puts("VIRTUAL_POOL_REAL_GPU_segments-checked-shutdown_OK");return 0;
    }else if(!strcmp(mode,"graphics-copy-coherence")){
        __weak id<MTLBuffer> borrowed;@autoreleasepool{borrowed=((BWPool*)get(&physical,[BWPool class])).buffer;}VP_CHECK(borrowed);size_t offset=(size_t)getpagesize()/2;
        memset(q+offset,0x4b,page);textureReadBytes(q+offset,p+offset,page,"graphics live NoCopy read");textureWriteBytes(p+offset,q+offset,page,"graphics live NoCopy write");VP_CHECK(!textureWriteRun(p,offset,q+offset,page));
        VP_CHECK(vpOwnedVMLeaves(p,page*2)==1&&vpOwnedVMLeaves(q,page*2)==1);handle=vpCopy(&command,base,(uintptr_t)out,page*2);vp_nvnQueueSubmitCommands(&queue,1,&handle);vp_nvnQueueFinish(&queue);VP_CHECK(!memcmp(out,p,page*2));
        vp_nvnMemoryPoolFinalize(&physical);physicalRetired=1;VP_CHECK(!borrowed);VP_CHECK(!munmap(p,page*2));p=MAP_FAILED;
    }else if(!strcmp(mode,"shader-buffers")||!strcmp(mode,"sampler-device")||copiedCode||segmentedShader){/* Completed in the resource fixture above. */}
    else if(!strcmp(mode,"inflight-finalize")||!strcmp(mode,"inflight-remap")||!strcmp(mode,"segments-inflight-finalize")){
        int remap=!strcmp(mode,"inflight-remap");uint64_t oldGeneration=nvn_vpool_generation();
        BWQueue *queueObject=get(&queue,[BWQueue class]);BWPool *physicalObject=get(&physical,[BWPool class]);__weak id<MTLBuffer> weakBacking=physicalObject.buffer?:((BWPoolSegment*)physicalObject.segments[0]).buffer;
        VPGatedQueue *gated=[VPGatedQueue new];gated.queue=queueObject.native;gated.event=[gpu newSharedEvent];VP_CHECK(gated.event);queueObject.native=(id<MTLCommandQueue>)gated;
        vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(queueObject.latest.status>=MTLCommandBufferStatusCommitted&&queueObject.latest.status<MTLCommandBufferStatusCompleted);
        struct VPFinalizeJob job={.pool=&physical,.virtualPool=&virtualPool,.other=&other,.remap=remap,.lock=PTHREAD_MUTEX_INITIALIZER,.changed=PTHREAD_COND_INITIALIZER};pthread_t thread;VP_CHECK(!pthread_create(&thread,NULL,vpFinalizeWorker,&job));
        int entered=0;for(unsigned i=0;i<2000;i++){if(remap){if(pthread_mutex_trylock(&graphicsLock)){entered=1;break;}pthread_mutex_unlock(&graphicsLock);}else if(physicalObject.retiring){entered=1;break;}usleep(1000);}
        usleep(20000);pthread_mutex_lock(&job.lock);int early=job.done;pthread_mutex_unlock(&job.lock);BOOL inFlight=queueObject.latest.status<MTLCommandBufferStatusCompleted;int unchanged=nvn_vpool_generation()==oldGeneration;
        /* Release even on a failed assertion, so the test never leaves a GPU
         * wait behind while diagnosing a lifecycle bug. */
        gated.event.signaledValue=1;int joined=vpWaitFinalize(&job,thread);VP_CHECK(entered&&!early&&inFlight&&joined&&job.result&&unchanged);
        VP_CHECK(!memcmp(out,p,bytes));if(remap){VP_CHECK(weakBacking&&nvn_vpool_generation()>oldGeneration);vp_nvnQueueSubmitCommands(&queue,1,&handle);vp_nvnQueueFinish(&queue);VP_CHECK(!memcmp(out,q,bytes));}
        else{physicalRetired=1;VP_CHECK(!weakBacking);VP_CHECK(!munmap(p,page*2));p=MAP_FAILED;
            if(!sigsetjmp(vpJump,1)){vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(0);}VP_CHECK(strstr(botw_graphics_last_error(),"unmapped"));}
        pthread_cond_destroy(&job.changed);pthread_mutex_destroy(&job.lock);
    }else if(!strcmp(mode,"bounded-records")){
        for(unsigned i=0;i<128;i++){vp_nvnQueueSubmitCommands(&queue,1,&handle);vp_nvnQueueFinish(&queue);VP_CHECK(poolGPURecords.count<=1&&!memcmp(out,p,bytes));}
        poolBeginBatch();poolEndBatch();VP_CHECK(!poolGPURecords.count);
    }else if(!strcmp(mode,"copy-remap")){
        VP_CHECK(vpMap(&virtualPool,&other,0,0,page*2,0x100));vp_nvnQueueSubmitCommands(&queue,1,&handle);vp_nvnQueueFinish(&queue);VP_CHECK(!memcmp(out,q,bytes));
        VP_CHECK(vpMap(&virtualPool,&physical,0,0,page*2,0x100));vp_nvnQueueSubmitCommands(&queue,1,&handle);vp_nvnQueueFinish(&queue);VP_CHECK(!memcmp(out,p,bytes));
    }else{
        if(!strcmp(mode,"fragmented")){VP_CHECK(vpMap(&virtualPool,&other,0,page,page,0x100));handle=vpCopy(&command,base,(uintptr_t)out,page*2);}
        else if(!strcmp(mode,"unmap-preflight")){
            VP_CHECK(vpMap(&virtualPool,NULL,0,page,page,0));vp_nvnCommandBufferBeginRecording(&command);vp_nvnCommandBufferCopyBufferToBuffer(&command,base,(uintptr_t)out,512,0);vp_nvnCommandBufferCopyBufferToBuffer(&command,base+page,(uintptr_t)out+page,512,0);handle=vp_nvnCommandBufferEndRecording(&command);
        }
        if(!sigsetjmp(vpJump,1)){vp_nvnQueueSubmitCommands(&queue,1,&handle);VP_CHECK(0);}
        const char *why=!strcmp(mode,"fragmented")?"fragmented":!strcmp(mode,"fe-buffer")?"kind":"unmapped";
        VP_CHECK(strstr(botw_graphics_last_error(),why));
        /* An unexpectedly committed prefix must complete before checking bytes. */
        if(sigsetjmp(vpJump,1)){fprintf(stderr,"unexpected failure while draining rejected submission\n");return 93;}
        vp_nvnQueueFinish(&queue);dispatch_group_wait(allGPU,DISPATCH_TIME_FOREVER);
        for(size_t i=0;i<page*2;i++)VP_CHECK(out[i]==0xa5);
    }
    vp_nvnMemoryPoolFinalize(&virtualPool);if(!physicalRetired)vp_nvnMemoryPoolFinalize(&physical);vp_nvnMemoryPoolFinalize(&other);vp_nvnMemoryPoolFinalize(&destination);botw_graphics_shutdown();VP_CHECK((p==MAP_FAILED||!munmap(p,page*2))&&!munmap(q,page*2)&&!munmap(out,page*2));
    free(cachedAllocation);printf("VIRTUAL_POOL_REAL_GPU_%s_OK\n",mode);return 0;
}
