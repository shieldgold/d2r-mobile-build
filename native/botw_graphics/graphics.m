/* NVN ABI types and object-size limits come from the MIT upstream named in
 * THIRD_PARTY_LICENSE.txt. This implementation has no D2R shader identities,
 * game offsets, or fallback shader substitutions. Build with ARC and blocks. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <CommonCrypto/CommonDigest.h>
#import <objc/runtime.h>
#include "graphics.h"
#include "nvn_protos.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

@interface BWDevice : NSObject
@property int flags;
@end
@implementation BWDevice
@end
@interface BWPool : NSObject
@property void *memory;
@property size_t size;
@property int flags;
@property size_t bufferOffset;
@property BOOL coherent;
@property(strong) id<MTLBuffer> buffer;
@end
@implementation BWPool
@end
@interface BWTexture : NSObject
@property(strong) id<MTLTexture> texture;
@property(strong) BWPool *pool;
@property size_t offset, stride;
@property int width, height, format;
@end
@implementation BWTexture
@end
@interface BWProgram : NSObject
@property(strong) NSDictionary<NSNumber *, id<MTLFunction>> *functions;
@end
@implementation BWProgram
@end
@interface BWCommands : NSObject
@property(strong) NSMutableArray<NSDictionary *> *commands;
@property BOOL recording;
@end
@implementation BWCommands
@end
@interface BWQueue : NSObject
@property(strong) id<MTLCommandBuffer> latest;
@end
@implementation BWQueue
@end
@interface BWSync : NSObject
@property(strong) id<MTLCommandBuffer> buffer;
@property BOOL fenced;
@end
@implementation BWSync
@end
@interface BWWindow : NSObject
@property(strong) NSArray<NSNumber *> *textures;
@property(strong) NSMutableDictionary<NSNumber *, id<MTLCommandBuffer>> *busy;
@property int next, interval;
@property void *native;
@end
@implementation BWWindow
@end
@interface BWCompletion : NSObject
@property(strong) dispatch_group_t group;
@end
@implementation BWCompletion
@end
@interface BWShader : NSObject
@property(strong) NSData *controlHash, *codeHash;
@property size_t controlSize, codeSize;
@property NVNshaderStage stage;
@property(strong) id<MTLFunction> function;
@end
@implementation BWShader
@end

typedef struct { uint64_t serial; } BWObject;
typedef struct { NVNdevice *device; void *memory; size_t size; int flags; } PoolBuilder;
typedef struct { NVNdevice *device; int flags; } DeviceBuilder;
typedef struct { NVNdevice *device; int flags; size_t command, control, compute; } QueueBuilder;
typedef struct { NVNdevice *device; NVNmemoryPool *pool; ptrdiff_t offset; size_t size; } BufferBuilder;
typedef struct { NVNdevice *device; NVNmemoryPool *pool; ptrdiff_t offset, stride; int width,height,depth,levels,samples,format,target,flags; } TextureBuilder;
typedef struct { NVNdevice *device; void *native; NVNtexture *textures[4]; int count, interval; } WindowBuilder;
_Static_assert(sizeof(PoolBuilder)<=sizeof(NVNmemoryPoolBuilder),"pool builder ABI");
_Static_assert(sizeof(TextureBuilder)<=sizeof(NVNtextureBuilder),"texture builder ABI");
_Static_assert(sizeof(WindowBuilder)<=sizeof(NVNwindowBuilder),"window builder ABI");
static id<MTLDevice> gpu;
static id<MTLCommandQueue> commandQueue;
static CAMetalLayer *displayLayer;
static NSMutableDictionary<NSNumber *, id> *objects;
static NSMutableDictionary<NSNumber *, NSArray *> *handles;
static NSMutableDictionary<NSString *, id<MTLRenderPipelineState>> *pipelines;
static NSMutableArray<BWPool *> *pools;
static NSMutableArray<BWShader *> *shaders;
static uint64_t serial;
static botw_graphics_error errorCallback;
static botw_graphics_memory_range memoryValidator;
static char lastError[1024];
static id<MTLRenderPipelineState> presentPipeline;
static char completionKey;

static int report(const char *format, ...) {
    va_list args; va_start(args,format); vsnprintf(lastError,sizeof lastError,format,args); va_end(args);
    fprintf(stderr,"[botw graphics] %s\n",lastError);
    if(errorCallback)errorCallback(lastError); return 0;
}
__attribute__((noreturn)) static void fatal(const char *name) {
    report("Unsupported or invalid NVN operation: %s",name); abort();
}
const char *botw_graphics_last_error(void) { return lastError; }
static NSData *digest(const void *bytes,size_t size) {
    unsigned char hash[CC_SHA256_DIGEST_LENGTH]; CC_SHA256(bytes,(CC_LONG)size,hash);
    return [NSData dataWithBytes:hash length:sizeof hash];
}
static void put(void *raw,id value) { ((BWObject*)raw)->serial=++serial; objects[@(serial)]=value; }
static id get(const void *raw,Class expected) {
    id value=raw?objects[@(((const BWObject*)raw)->serial)]:nil;
    if(!value || ![value isKindOfClass:expected])fatal("object lifetime/type"); return value;
}
static void removeObject(void *raw) {
    [objects removeObjectForKey:@(((BWObject*)raw)->serial)]; ((BWObject*)raw)->serial=0;
}
static BWPool *address(uint64_t addr,size_t bytes,size_t *offset) {
    for(BWPool *pool in pools) {
        uintptr_t begin=(uintptr_t)pool.memory;
        if(addr>=begin && addr-begin<=pool.size && bytes<=pool.size-(addr-begin)) {
            *offset=(size_t)(addr-begin); return pool;
        }
    }
    return nil;
}
static BOOL range(size_t offset,size_t bytes,size_t size) { return offset<=size && bytes<=size-offset; }
static void finishBuffer(id<MTLCommandBuffer> buffer) {
    if(!buffer)return; [buffer waitUntilCompleted];
    if(buffer.status==MTLCommandBufferStatusError)fatal(buffer.error.localizedDescription.UTF8String);
}
/* Completion handlers must be registered before commit. Sync waits retain the
 * group through the command buffer rather than registering a late handler. */
static void commitBuffer(id<MTLCommandBuffer> cb) {
    BWCompletion *completion=[BWCompletion new];completion.group=dispatch_group_create();
    dispatch_group_enter(completion.group);
    objc_setAssociatedObject(cb,&completionKey,completion,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> b){(void)b;dispatch_group_leave(completion.group);}];
    [cb commit];
}
int botw_graphics_init(void *layer,botw_graphics_error error,botw_graphics_memory_range validator) {
    if(gpu)return report("Graphics already initialized");
    errorCallback=error; memoryValidator=validator; lastError[0]=0;
    gpu=MTLCreateSystemDefaultDevice(); if(!gpu)return report("No Metal device");
    commandQueue=[gpu newCommandQueue]; if(!commandQueue){gpu=nil;return report("No Metal command queue");}
    objects=[NSMutableDictionary new]; handles=[NSMutableDictionary new]; pipelines=[NSMutableDictionary new];
    pools=[NSMutableArray new]; shaders=[NSMutableArray new]; serial=0;
    displayLayer=(__bridge CAMetalLayer *)layer;
    if(displayLayer){displayLayer.device=gpu;displayLayer.pixelFormat=MTLPixelFormatBGRA8Unorm;}
    return 1;
}
void botw_graphics_shutdown(void) {
    for(id object in objects.allValues)if([object isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)object).latest);
    objects=nil;handles=nil;pipelines=nil;pools=nil;shaders=nil;presentPipeline=nil;
    displayLayer=nil;commandQueue=nil;gpu=nil;memoryValidator=NULL;
}
int botw_graphics_register_shader(const void *control,size_t cs,const void *code,size_t ds,
                                  NVNshaderStage stage,const char *source,const char *entry) {
    if(!gpu || !control || !code || !source || !entry || cs<4 || ds<0x80 || cs>1<<20 || ds>16<<20 ||
       (stage!=NVN_SHADER_STAGE_VERTEX && stage!=NVN_SHADER_STAGE_FRAGMENT))
        return report("Invalid shader registration or unsupported shader stage");
    uint32_t cm,dm;memcpy(&cm,control,4);memcpy(&dm,code,4);
    if(cm!=0x98761234 || dm!=0x12345678)return report("Invalid shader input magic");
    NSError *error=nil; id<MTLLibrary> library=[gpu newLibraryWithSource:@(source) options:nil error:&error];
    if(!library)return report("Metal shader compilation failed: %s",error.localizedDescription.UTF8String);
    id<MTLFunction> function=[library newFunctionWithName:@(entry)];
    if(!function || (stage==NVN_SHADER_STAGE_VERTEX?function.functionType!=MTLFunctionTypeVertex:
                    function.functionType!=MTLFunctionTypeFragment))return report("Metal shader entry/stage mismatch");
    BWShader *shader=[BWShader new];shader.controlHash=digest(control,cs);shader.codeHash=digest(code,ds);
    shader.controlSize=cs;shader.codeSize=ds;shader.stage=stage;shader.function=function;[shaders addObject:shader];return 1;
}
int botw_graphics_read_texture(const NVNtexture *raw,void *pixels,size_t bytes) {
    BWTexture *texture=get(raw,[BWTexture class]);
    if(!pixels || !range(0,(size_t)texture.width*texture.height*4,bytes))return report("Texture readback buffer bounds");
    for(id object in objects.allValues)if([object isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)object).latest);
    [texture.texture getBytes:pixels bytesPerRow:texture.width*4 fromRegion:MTLRegionMake2D(0,0,texture.width,texture.height) mipmapLevel:0];
    return 1;
}
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__); \
    _Static_assert(__builtin_types_compatible_p(__typeof__(&h_##name), NVNT_##name), "NVN signature: " #name); \
    static ret h_##name(__VA_ARGS__)
NVN(nvnDeviceBuilderSetDefaults,void,NVNdeviceBuilder *b){memset(b,0,sizeof *b);}
NVN(nvnDeviceBuilderSetFlags,void,NVNdeviceBuilder *b,int flags){((DeviceBuilder*)b)->flags=flags;}
NVN(nvnDeviceInitialize,NVNboolean,NVNdevice *d,const NVNdeviceBuilder *b){if(!gpu)return report("Metal not initialized");BWDevice *device=[BWDevice new];device.flags=((const DeviceBuilder*)b)->flags;put(d,device);return 1;}
NVN(nvnDeviceFinalize,void,NVNdevice *d){get(d,[BWDevice class]);removeObject(d);}
NVN(nvnDeviceGetProcAddress,PFNNVNGENERICFUNCPTRPROC,const NVNdevice *d,const char *name){get(d,[BWDevice class]);return (PFNNVNGENERICFUNCPTRPROC)botw_nvn_bootstrap_loader(name);}
NVN(nvnDeviceGetInteger,void,const NVNdevice *d,NVNdeviceInfo query,int *value){
    get(d,[BWDevice class]);if(!value)fatal("nvnDeviceGetInteger null output");
    switch(query){case NVN_DEVICE_INFO_API_MAJOR_VERSION:*value=53;break;
    case NVN_DEVICE_INFO_API_MINOR_VERSION:*value=105;break;
    case NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE:*value=4096;break;
    case NVN_DEVICE_INFO_COMMAND_BUFFER_COMMAND_ALIGNMENT:case NVN_DEVICE_INFO_COMMAND_BUFFER_CONTROL_ALIGNMENT:*value=8;break;
    case NVN_DEVICE_INFO_COMMAND_BUFFER_MIN_COMMAND_SIZE:case NVN_DEVICE_INFO_COMMAND_BUFFER_MIN_CONTROL_SIZE:*value=256;break;
    case NVN_DEVICE_INFO_UNIFORM_BUFFER_ALIGNMENT:*value=256;break;
    case NVN_DEVICE_INFO_COLOR_BUFFER_BINDINGS:*value=1;break;
    default:fatal("nvnDeviceGetInteger unsupported capability");}}
NVN(nvnDeviceFinalizeCommandHandle,void,NVNdevice *d,NVNcommandHandle h){get(d,[BWDevice class]);if(!handles[@(h)])fatal("invalid command handle");[handles removeObjectForKey:@(h)];}
NVN(nvnMemoryPoolBuilderSetDefaults,void,NVNmemoryPoolBuilder *b){memset(b,0,sizeof *b);}
NVN(nvnMemoryPoolBuilderSetDevice,void,NVNmemoryPoolBuilder *b,NVNdevice *d){((PoolBuilder*)b)->device=d;}
NVN(nvnMemoryPoolBuilderSetStorage,void,NVNmemoryPoolBuilder *b,void *memory,size_t size){((PoolBuilder*)b)->memory=memory;((PoolBuilder*)b)->size=size;}
NVN(nvnMemoryPoolBuilderSetFlags,void,NVNmemoryPoolBuilder *b,int flags){((PoolBuilder*)b)->flags=flags;}
NVN(nvnMemoryPoolInitialize,NVNboolean,NVNmemoryPool *raw,const NVNmemoryPoolBuilder *builder){
    const PoolBuilder *b=(const PoolBuilder*)builder;get(b->device,[BWDevice class]);
    if(!b->memory || !b->size || b->size>gpu.maxBufferLength || (b->flags&(NVN_MEMORY_POOL_FLAGS_VIRTUAL|NVN_MEMORY_POOL_FLAGS_PHYSICAL)))return report("Unsupported/invalid memory pool");
    for(BWPool *p in pools){uintptr_t a=(uintptr_t)p.memory,c=(uintptr_t)b->memory;if(c<=a?a-c<b->size:c-a<p.size)return report("Memory pool ranges overlap");}
    BWPool *p=[BWPool new];p.memory=b->memory;p.size=b->size;p.flags=b->flags;
    size_t page=(size_t)getpagesize();uintptr_t begin=(uintptr_t)b->memory;
    if(b->size>UINTPTR_MAX-begin)return report("Memory pool address overflow");
    uintptr_t aligned=begin&~(uintptr_t)(page-1),end=begin+b->size;
    if(end>UINTPTR_MAX-(page-1))return report("Memory pool page overflow");
    size_t span=((end+page-1)&~(uintptr_t)(page-1))-aligned;
    BOOL exact=begin==aligned && span==b->size;
    if(exact || (memoryValidator && memoryValidator((void*)aligned,span))){
        p.buffer=[gpu newBufferWithBytesNoCopy:(void*)aligned length:span options:MTLResourceStorageModeShared deallocator:nil];
        p.bufferOffset=begin-aligned;p.coherent=YES;
    }else if(b->flags&NVN_MEMORY_POOL_FLAGS_CPU_CACHED){
        p.buffer=[gpu newBufferWithBytes:b->memory length:b->size options:MTLResourceStorageModeShared];
    }else{return report("Uncached pool must have readable host-page-aligned backing");}
    if(!p.buffer)return report("Metal memory pool allocation failed");put(raw,p);[pools addObject:p];return 1;}
NVN(nvnMemoryPoolFinalize,void,NVNmemoryPool *raw){BWPool *p=get(raw,[BWPool class]);for(id o in objects.allValues)if([o isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)o).latest);[pools removeObject:p];removeObject(raw);}
NVN(nvnMemoryPoolMap,void*,const NVNmemoryPool *p){return ((BWPool*)get(p,[BWPool class])).memory;}
NVN(nvnMemoryPoolGetBufferAddress,NVNbufferAddress,const NVNmemoryPool *p){return (uintptr_t)((BWPool*)get(p,[BWPool class])).memory;}
NVN(nvnMemoryPoolGetSize,size_t,const NVNmemoryPool *p){return ((BWPool*)get(p,[BWPool class])).size;}
NVN(nvnMemoryPoolGetFlags,NVNmemoryPoolFlags,const NVNmemoryPool *p){return (NVNmemoryPoolFlags)((BWPool*)get(p,[BWPool class])).flags;}
NVN(nvnMemoryPoolFlushMappedRange,void,const NVNmemoryPool *raw,ptrdiff_t offset,size_t bytes){BWPool *p=get(raw,[BWPool class]);if(offset<0||!range(offset,bytes,p.size))fatal("nvnMemoryPoolFlushMappedRange bounds");if(!p.coherent)memcpy((char*)p.buffer.contents+p.bufferOffset+offset,(char*)p.memory+offset,bytes);}
NVN(nvnMemoryPoolInvalidateMappedRange,void,const NVNmemoryPool *raw,ptrdiff_t offset,size_t bytes){BWPool *p=get(raw,[BWPool class]);if(offset<0||!range(offset,bytes,p.size))fatal("nvnMemoryPoolInvalidateMappedRange bounds");for(id o in objects.allValues)if([o isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)o).latest);if(!p.coherent)memcpy((char*)p.memory+offset,(char*)p.buffer.contents+p.bufferOffset+offset,bytes);}
NVN(nvnQueueBuilderSetDefaults,void,NVNqueueBuilder *b){memset(b,0,sizeof *b);}
NVN(nvnQueueBuilderSetDevice,void,NVNqueueBuilder *b,NVNdevice *d){((QueueBuilder*)b)->device=d;}
NVN(nvnQueueInitialize,NVNboolean,NVNqueue *raw,const NVNqueueBuilder *b){get(((const QueueBuilder*)b)->device,[BWDevice class]);put(raw,[BWQueue new]);return 1;}
NVN(nvnQueueFinish,void,NVNqueue *q){finishBuffer(((BWQueue*)get(q,[BWQueue class])).latest);}
NVN(nvnQueueFlush,void,NVNqueue *q){/* Submission commits immediately; no deferred queue exists. */get(q,[BWQueue class]);}
NVN(nvnQueueFinalize,void,NVNqueue *q){h_nvnQueueFinish(q);removeObject(q);}
NVN(nvnSyncInitialize,NVNboolean,NVNsync *s,NVNdevice *d){get(d,[BWDevice class]);put(s,[BWSync new]);return 1;}
NVN(nvnSyncFinalize,void,NVNsync *s){get(s,[BWSync class]);removeObject(s);}
NVN(nvnQueueFenceSync,void,NVNqueue *q,NVNsync *s,NVNsyncCondition condition,int flags){
    if(condition!=NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE || flags)fatal("nvnQueueFenceSync condition/flags");
    BWSync *sync=get(s,[BWSync class]);sync.fenced=YES;sync.buffer=((BWQueue*)get(q,[BWQueue class])).latest;}
NVN(nvnSyncWait,NVNsyncWaitResult,const NVNsync *raw,uint64_t timeout){BWSync *s=get(raw,[BWSync class]);
    if(!s.fenced)return NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;
    if(!s.buffer || s.buffer.status==MTLCommandBufferStatusCompleted)return NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED;
    if(s.buffer.status==MTLCommandBufferStatusError)return NVN_SYNC_WAIT_RESULT_FAILED;
    if(!timeout)return NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;
    BWCompletion *completion=objc_getAssociatedObject(s.buffer,&completionKey);
    if(!completion)fatal("missing GPU completion fence");
    int64_t ns=timeout>INT64_MAX?INT64_MAX:(int64_t)timeout;
    if(dispatch_group_wait(completion.group,dispatch_time(DISPATCH_TIME_NOW,ns)))return NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;
    return s.buffer.status==MTLCommandBufferStatusCompleted?NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED:NVN_SYNC_WAIT_RESULT_FAILED;}
NVN(nvnTextureBuilderSetDefaults,void,NVNtextureBuilder *raw){memset(raw,0,sizeof *raw);TextureBuilder *b=(TextureBuilder*)raw;b->depth=1;b->levels=1;b->samples=1;b->target=NVN_TEXTURE_TARGET_2D;}
NVN(nvnTextureBuilderSetDevice,void,NVNtextureBuilder *b,NVNdevice *d){((TextureBuilder*)b)->device=d;}
NVN(nvnTextureBuilderSetFlags,void,NVNtextureBuilder *b,int f){((TextureBuilder*)b)->flags=f;}
NVN(nvnTextureBuilderSetTarget,void,NVNtextureBuilder *b,NVNtextureTarget t){((TextureBuilder*)b)->target=t;}
NVN(nvnTextureBuilderSetSize2D,void,NVNtextureBuilder *b,int w,int h){((TextureBuilder*)b)->width=w;((TextureBuilder*)b)->height=h;}
NVN(nvnTextureBuilderSetWidth,void,NVNtextureBuilder *b,int w){((TextureBuilder*)b)->width=w;}
NVN(nvnTextureBuilderSetHeight,void,NVNtextureBuilder *b,int h){((TextureBuilder*)b)->height=h;}
NVN(nvnTextureBuilderSetDepth,void,NVNtextureBuilder *b,int d){((TextureBuilder*)b)->depth=d;}
NVN(nvnTextureBuilderSetLevels,void,NVNtextureBuilder *b,int n){((TextureBuilder*)b)->levels=n;}
NVN(nvnTextureBuilderSetSamples,void,NVNtextureBuilder *b,int n){((TextureBuilder*)b)->samples=n;}
NVN(nvnTextureBuilderSetFormat,void,NVNtextureBuilder *b,NVNformat f){((TextureBuilder*)b)->format=f;}
NVN(nvnTextureBuilderSetStride,void,NVNtextureBuilder *b,ptrdiff_t s){((TextureBuilder*)b)->stride=s;}
NVN(nvnTextureBuilderSetStorage,void,NVNtextureBuilder *b,NVNmemoryPool *p,ptrdiff_t o){((TextureBuilder*)b)->pool=p;((TextureBuilder*)b)->offset=o;}
static size_t textureBytes(const TextureBuilder *b){if(b->width<1 || b->height<1 || b->width>16384 || b->height>16384 || b->stride<(ptrdiff_t)b->width*4)return 0;return (size_t)b->stride*b->height;}
NVN(nvnTextureBuilderGetStorageSize,size_t,const NVNtextureBuilder *raw){return textureBytes((const TextureBuilder*)raw);}
NVN(nvnTextureBuilderGetStorageAlignment,size_t,const NVNtextureBuilder *raw){(void)raw;return 256;}
NVN(nvnTextureInitialize,NVNboolean,NVNtexture *raw,const NVNtextureBuilder *builder){
    const TextureBuilder *b=(const TextureBuilder*)builder;get(b->device,[BWDevice class]);
    if(!b->pool || b->target!=NVN_TEXTURE_TARGET_2D || b->depth!=1 || b->levels!=1 || b->samples!=1 ||
       !(b->flags&(NVN_TEXTURE_FLAGS_LINEAR|NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET)) ||
       (b->flags&~(NVN_TEXTURE_FLAGS_LINEAR|NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET|NVN_TEXTURE_FLAGS_DISPLAY)))
        return report("Only linear single-level 2D color textures are implemented");
    MTLPixelFormat format=b->format==NVN_FORMAT_RGBA8?MTLPixelFormatRGBA8Unorm:b->format==NVN_FORMAT_BGRA8?MTLPixelFormatBGRA8Unorm:MTLPixelFormatInvalid;
    BWPool *pool=get(b->pool,[BWPool class]);size_t size=textureBytes(b);
    if(!size||b->offset<0||!range(b->offset,size,pool.size)||format==MTLPixelFormatInvalid)return report("Texture format/storage bounds unsupported");
    MTLTextureDescriptor *desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:b->width height:b->height mipmapped:NO];
    desc.storageMode=MTLStorageModeShared;desc.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    BWTexture *t=[BWTexture new];t.texture=[pool.buffer newTextureWithDescriptor:desc offset:pool.bufferOffset+b->offset bytesPerRow:b->stride];
    if(!t.texture)return report("Metal rejected linear texture storage alignment");
    t.pool=pool;t.offset=b->offset;t.stride=b->stride;t.width=b->width;t.height=b->height;t.format=b->format;put(raw,t);return 1;}
NVN(nvnTextureFinalize,void,NVNtexture *t){get(t,[BWTexture class]);removeObject(t);}
NVN(nvnTextureGetWidth,int,const NVNtexture *t){return ((BWTexture*)get(t,[BWTexture class])).width;}
NVN(nvnTextureGetHeight,int,const NVNtexture *t){return ((BWTexture*)get(t,[BWTexture class])).height;}
NVN(nvnTextureGetFormat,NVNformat,const NVNtexture *t){return (NVNformat)((BWTexture*)get(t,[BWTexture class])).format;}
NVN(nvnProgramInitialize,NVNboolean,NVNprogram *p,NVNdevice *d){get(d,[BWDevice class]);put(p,[BWProgram new]);return 1;}
NVN(nvnProgramFinalize,void,NVNprogram *p){get(p,[BWProgram class]);removeObject(p);}
NVN(nvnProgramSetShaders,NVNboolean,NVNprogram *raw,int count,const NVNshaderData *data){
    BWProgram *program=get(raw,[BWProgram class]);if(count<1||count>2||!data)return report("Unsupported program stage count");
    NSMutableDictionary *functions=[NSMutableDictionary new];
    for(int i=0;i<count;i++){
        BWShader *found=nil;
        for(BWShader *s in shaders){size_t offset=0;BWPool *p=address(data[i].data,s.codeSize,&offset);
            if(p && memoryValidator && memoryValidator(data[i].control,s.controlSize) &&
               [digest(data[i].control,s.controlSize) isEqual:s.controlHash] &&
               [digest((char*)p.memory+offset,s.codeSize) isEqual:s.codeHash]){found=s;break;}}
        if(!found)return report("No exact, validated local Metal translation for NVN shader");
        if(functions[@(found.stage)])return report("Program has duplicate shader stages");functions[@(found.stage)]=found.function;
    }
    program.functions=functions;return 1;}
NVN(nvnCommandBufferInitialize,NVNboolean,NVNcommandBuffer *c,NVNdevice *d){get(d,[BWDevice class]);put(c,[BWCommands new]);return 1;}
NVN(nvnCommandBufferFinalize,void,NVNcommandBuffer *c){BWCommands *cmd=get(c,[BWCommands class]);if(cmd.recording)fatal("finalize recording command buffer");removeObject(c);}
NVN(nvnCommandBufferBeginRecording,void,NVNcommandBuffer *c){BWCommands *cmd=get(c,[BWCommands class]);if(cmd.recording)fatal("nested recording");cmd.commands=[NSMutableArray new];cmd.recording=YES;}
NVN(nvnCommandBufferEndRecording,NVNcommandHandle,NVNcommandBuffer *c){BWCommands *cmd=get(c,[BWCommands class]);if(!cmd.recording)fatal("end without recording");cmd.recording=NO;uint64_t h=++serial;handles[@(h)]=[cmd.commands copy];cmd.commands=nil;return h;}
static void record(NVNcommandBuffer *raw,NSDictionary *command){BWCommands *cmd=get(raw,[BWCommands class]);if(!cmd.recording)fatal("command outside recording");[cmd.commands addObject:command];}
NVN(nvnCommandBufferCopyBufferToBuffer,void,NVNcommandBuffer *c,NVNbufferAddress src,NVNbufferAddress dst,size_t bytes,int flags){if(flags || !bytes)fatal("nvnCommandBufferCopyBufferToBuffer flags/size");record(c,@{@"op":@"copy",@"src":@(src),@"dst":@(dst),@"size":@(bytes)});}
NVN(nvnCommandBufferSetRenderTargets,void,NVNcommandBuffer *c,int n,const NVNtexture *const *colors,const NVNtextureView *const *views,const NVNtexture *depth,const NVNtextureView *dv){if(n!=1||!colors||depth||dv||(views&&views[0]))fatal("nvnCommandBufferSetRenderTargets supports one color/no view/depth");get(colors[0],[BWTexture class]);record(c,@{@"op":@"target",@"id":@(((const BWObject*)colors[0])->serial)});}
NVN(nvnCommandBufferClearColor,void,NVNcommandBuffer *c,int index,const float *color,int mask){if(index||!color||mask!=15)fatal("nvnCommandBufferClearColor color index/mask");record(c,@{@"op":@"clear",@"color":[NSData dataWithBytes:color length:16]});}
NVN(nvnCommandBufferBindProgram,void,NVNcommandBuffer *c,const NVNprogram *p,int stages){if(stages!=NVN_SHADER_STAGE_ALL_GRAPHICS_BITS && stages!=(NVN_SHADER_STAGE_VERTEX_BIT|NVN_SHADER_STAGE_FRAGMENT_BIT))fatal("nvnCommandBufferBindProgram stages");get(p,[BWProgram class]);record(c,@{@"op":@"program",@"id":@(((const BWObject*)p)->serial)});}
NVN(nvnCommandBufferDrawArrays,void,NVNcommandBuffer *c,NVNdrawPrimitive mode,int first,int count){if(mode!=NVN_DRAW_PRIMITIVE_TRIANGLES||first<0||count<0)fatal("nvnCommandBufferDrawArrays primitive/range");record(c,@{@"op":@"draw",@"first":@(first),@"count":@(count)});}
NVN(nvnCommandBufferSetViewport,void,NVNcommandBuffer *c,int x,int y,int w,int h){if(x<0||y<0||w<0||h<0)fatal("nvnCommandBufferSetViewport bounds");record(c,@{@"op":@"viewport",@"x":@(x),@"y":@(y),@"w":@(w),@"h":@(h)});}
NVN(nvnCommandBufferSetScissor,void,NVNcommandBuffer *c,int x,int y,int w,int h){if(x<0||y<0||w<0||h<0)fatal("nvnCommandBufferSetScissor bounds");record(c,@{@"op":@"scissor",@"x":@(x),@"y":@(y),@"w":@(w),@"h":@(h)});}
static id<MTLRenderPipelineState> pipeline(BWProgram *p,BWTexture *target){
    id<MTLFunction> vertex=p.functions[@(NVN_SHADER_STAGE_VERTEX)],fragment=p.functions[@(NVN_SHADER_STAGE_FRAGMENT)];
    if(!vertex||!fragment)fatal("draw without exact translated vertex/fragment shaders");
    NSString *key=[NSString stringWithFormat:@"%p:%p:%lu",vertex,fragment,(unsigned long)target.texture.pixelFormat];
    id<MTLRenderPipelineState> result=pipelines[key];if(result)return result;
    MTLRenderPipelineDescriptor *desc=[MTLRenderPipelineDescriptor new];desc.vertexFunction=vertex;desc.fragmentFunction=fragment;desc.colorAttachments[0].pixelFormat=target.texture.pixelFormat;
    NSError *error=nil;result=[gpu newRenderPipelineStateWithDescriptor:desc error:&error];
    if(!result)fatal(error.localizedDescription.UTF8String);pipelines[key]=result;return result;}
NVN(nvnQueueSubmitCommands,void,NVNqueue *raw,int count,const NVNcommandHandle *input){
    BWQueue *queue=get(raw,[BWQueue class]);if(count<0||(!input&&count))fatal("nvnQueueSubmitCommands handle bounds");
    id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)fatal("Metal command allocation");
    BWTexture *target=nil;BWProgram *program=nil;NSDictionary *viewport=nil,*scissor=nil;
    for(int i=0;i<count;i++){
        NSArray *commands=handles[@(input[i])];if(!commands)fatal("invalid/finalized command handle");
        for(NSDictionary *c in commands){NSString *op=c[@"op"];
            if([op isEqual:@"target"]){target=objects[c[@"id"]];if(![target isKindOfClass:[BWTexture class]])fatal("stale render target");continue;}
            if([op isEqual:@"program"]){program=objects[c[@"id"]];if(![program isKindOfClass:[BWProgram class]])fatal("stale program");continue;}
            if([op isEqual:@"viewport"]){viewport=c;continue;}if([op isEqual:@"scissor"]){scissor=c;continue;}
            if([op isEqual:@"copy"]){size_t a=0,b=0,n=[c[@"size"] unsignedLongLongValue];
                BWPool *src=address([c[@"src"] unsignedLongLongValue],n,&a),*dst=address([c[@"dst"] unsignedLongLongValue],n,&b);
                if(!src||!dst||(src==dst && a<b+n && b<a+n))fatal("GPU copy bounds/overlap");
                id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];[blit copyFromBuffer:src.buffer sourceOffset:src.bufferOffset+a toBuffer:dst.buffer destinationOffset:dst.bufferOffset+b size:n];[blit endEncoding];continue;}
            if(!target)fatal("render operation without target");
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target.texture;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            BOOL clear=[op isEqual:@"clear"];pass.colorAttachments[0].loadAction=clear?MTLLoadActionClear:MTLLoadActionLoad;
            if(clear && scissor && ([scissor[@"x"] intValue]!=0 || [scissor[@"y"] intValue]!=0 || [scissor[@"w"] intValue]!=target.width || [scissor[@"h"] intValue]!=target.height))fatal("scissored clear is not yet implemented");
            if(clear){float color[4];[c[@"color"] getBytes:color length:16];pass.colorAttachments[0].clearColor=MTLClearColorMake(color[0],color[1],color[2],color[3]);}
            id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];if(!encoder)fatal("Metal render pass creation");
            if(!clear){if(![op isEqual:@"draw"] || !program)fatal("unsupported replay operation / missing program");[encoder setRenderPipelineState:pipeline(program,target)];
                if(viewport)[encoder setViewport:(MTLViewport){[viewport[@"x"] doubleValue],[viewport[@"y"] doubleValue],[viewport[@"w"] doubleValue],[viewport[@"h"] doubleValue],0,1}];
                if(scissor){NSUInteger x=[scissor[@"x"] unsignedIntegerValue],y=[scissor[@"y"] unsignedIntegerValue],w=[scissor[@"w"] unsignedIntegerValue],h=[scissor[@"h"] unsignedIntegerValue];if(x>(NSUInteger)target.width||y>(NSUInteger)target.height||w>(NSUInteger)target.width-x||h>(NSUInteger)target.height-y)fatal("scissor outside target");[encoder setScissorRect:(MTLScissorRect){x,y,w,h}];}
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:[c[@"first"] unsignedIntegerValue] vertexCount:[c[@"count"] unsignedIntegerValue]];}
            [encoder endEncoding];
        }
    }
    queue.latest=cb;commitBuffer(cb);}
NVN(nvnWindowBuilderSetDefaults,void,NVNwindowBuilder *raw){memset(raw,0,sizeof *raw);((WindowBuilder*)raw)->interval=1;}
NVN(nvnWindowBuilderSetDevice,void,NVNwindowBuilder *b,NVNdevice *d){((WindowBuilder*)b)->device=d;}
NVN(nvnWindowBuilderSetNativeWindow,void,NVNwindowBuilder *b,NVNnativeWindow w){((WindowBuilder*)b)->native=w;}
NVN(nvnWindowBuilderSetTextures,void,NVNwindowBuilder *raw,int n,NVNtexture *const *textures){WindowBuilder *b=(WindowBuilder*)raw;if(n<2||n>4||!textures)fatal("swapchain texture count");b->count=n;for(int i=0;i<n;i++){get(textures[i],[BWTexture class]);b->textures[i]=textures[i];}}
NVN(nvnWindowBuilderSetPresentInterval,void,NVNwindowBuilder *b,int interval){if(interval<0||interval>4)fatal("present interval");((WindowBuilder*)b)->interval=interval;}
NVN(nvnWindowInitialize,NVNboolean,NVNwindow *raw,const NVNwindowBuilder *builder){const WindowBuilder *b=(const WindowBuilder*)builder;get(b->device,[BWDevice class]);if(!displayLayer || b->count<2)return report("No host CAMetalLayer or valid swapchain");BWWindow *w=[BWWindow new];NSMutableArray *textures=[NSMutableArray new];for(int i=0;i<b->count;i++)[textures addObject:@(((BWObject*)b->textures[i])->serial)];w.textures=textures;w.busy=[NSMutableDictionary new];w.native=b->native;w.interval=b->interval;put(raw,w);return 1;}
NVN(nvnWindowFinalize,void,NVNwindow *raw){BWWindow *w=get(raw,[BWWindow class]);for(id<MTLCommandBuffer> b in w.busy.allValues)finishBuffer(b);removeObject(raw);}
NVN(nvnWindowGetNativeWindow,NVNnativeWindow,const NVNwindow *w){return ((BWWindow*)get(w,[BWWindow class])).native;}
NVN(nvnWindowGetPresentInterval,int,const NVNwindow *w){return ((BWWindow*)get(w,[BWWindow class])).interval;}
NVN(nvnWindowSetPresentInterval,void,NVNwindow *raw,int interval){if(interval<0||interval>4)fatal("present interval");((BWWindow*)get(raw,[BWWindow class])).interval=interval;}
NVN(nvnQueueAcquireTexture,NVNqueueAcquireTextureResult,NVNqueue *q,NVNwindow *raw,int *index){get(q,[BWQueue class]);BWWindow *w=get(raw,[BWWindow class]);if(!index)fatal("acquire texture output");*index=w.next;finishBuffer(w.busy[@(*index)]);w.next=(w.next+1)%(int)w.textures.count;return NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS;}
NVN(nvnWindowAcquireTexture,NVNwindowAcquireTextureResult,NVNwindow *raw,NVNsync *s,int *index){BWWindow *w=get(raw,[BWWindow class]);if(!index)fatal("acquire texture output");*index=w.next;BWSync *sync=get(s,[BWSync class]);sync.fenced=YES;sync.buffer=w.busy[@(*index)];w.next=(w.next+1)%(int)w.textures.count;return NVN_WINDOW_ACQUIRE_TEXTURE_RESULT_SUCCESS;}
NVN(nvnQueuePresentTexture,void,NVNqueue *q,NVNwindow *raw,int index){BWQueue *queue=get(q,[BWQueue class]);BWWindow *w=get(raw,[BWWindow class]);if(index<0||index>=(int)w.textures.count)fatal("present texture index");BWTexture *t=objects[w.textures[index]];if(!t)fatal("stale swapchain texture");id<CAMetalDrawable> drawable=[displayLayer nextDrawable];if(!drawable)fatal("CAMetalLayer has no drawable");
    if(!presentPipeline){const char *source="#include <metal_stdlib>\nusing namespace metal; struct V{float4 p [[position]];float2 uv;}; vertex V pv(uint i [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return {float4(p[i],0,1),float2((p[i].x+1)*.5,(1-p[i].y)*.5)};} fragment float4 pf(V v [[stage_in]],texture2d<float> t [[texture(0)]]){constexpr sampler s(filter::linear);return t.sample(s,v.uv);}";
        NSError *e=nil;id<MTLLibrary> l=[gpu newLibraryWithSource:@(source) options:nil error:&e];if(!l)fatal(e.localizedDescription.UTF8String);MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[l newFunctionWithName:@"pv"];d.fragmentFunction=[l newFunctionWithName:@"pf"];d.colorAttachments[0].pixelFormat=displayLayer.pixelFormat;presentPipeline=[gpu newRenderPipelineStateWithDescriptor:d error:&e];if(!presentPipeline)fatal(e.localizedDescription.UTF8String);}
    id<MTLCommandBuffer> cb=[commandQueue commandBuffer];MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];p.colorAttachments[0].texture=drawable.texture;p.colorAttachments[0].loadAction=MTLLoadActionDontCare;p.colorAttachments[0].storeAction=MTLStoreActionStore;id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:p];[e setRenderPipelineState:presentPipeline];[e setFragmentTexture:t.texture atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb presentDrawable:drawable afterMinimumDuration:w.interval/60.0];w.busy[@(index)]=cb;queue.latest=cb;commitBuffer(cb);}

/* A public NVN name can be queried speculatively. Unsupported entries remain
 * callable only to give a named fatal error when actually invoked. */
#define NVN_UNSUPPORTED(name) __attribute__((noreturn)) static void unsupported_##name(void){fatal(#name);}
#include "unsupported.inc"
#undef NVN_UNSUPPORTED
void *botw_nvn_bootstrap_loader(const char *name){if(!name)return NULL;
#define NVN_BIND(name) if(!strcmp(#name,name_))return (void*)h_##name;
    const char *name_=name;
    if(!strcmp(name,"nvnBootstrapLoader"))return (void*)botw_nvn_bootstrap_loader;
#include "implemented.inc"
#undef NVN_BIND
#define NVN_UNSUPPORTED(name) if(!strcmp(#name,name_))return (void*)unsupported_##name;
#include "unsupported.inc"
#undef NVN_UNSUPPORTED
    report("Unknown NVN entry point: %s",name);return NULL;}
