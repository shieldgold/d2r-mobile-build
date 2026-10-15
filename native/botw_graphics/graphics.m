/* NVN ABI types and object-size limits come from the MIT upstream named in
 * THIRD_PARTY_LICENSE.txt. This implementation has no D2R shader identities,
 * game offsets, or fallback shader substitutions. Build with ARC and blocks. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <CommonCrypto/CommonDigest.h>
#import <objc/runtime.h>
#include "graphics.h"
#include "queue_arena.h"
#include "command_memory.h"
#include "shader_scratch_proof.h"
#include "vendor/d2r-state/states.h"
#include "nvn_protos.h"
#include "texture_abi.h"
#include "linear_texture.h"
#include "pool_policy.h"
#include "vendor/botw-buffer/buffer.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <math.h>
#include <mach/mach.h>
#include "vendor/nvn/nvn_tex.c"
#define mip deswizzle_mip
#include "vendor/tex/nvn_deswizzle.c"
#undef mip
#include "vendor/tex/nvn_fmt_mtl.c"
#include "vendor/botw-texture/modules.inc"
#include "vendor/botw-vpool/modules.inc"

@interface BWDevice : NSObject
@property int flags;
@property NVNwindowOriginMode windowOrigin;
@property(strong) id<MTLCounterSet> timestampSet;
@property BOOL timestampStages;
@property unsigned timestampReports;
@property BWLinearPolicy linearPolicy;
@end
@implementation BWDevice
@end
@interface BWPool : NSObject
@property void *memory;
@property size_t size;
@property int flags;
@property(strong) BWDevice *device;
@property uint64_t generation;
@property BOOL live;
@property BWPoolKind kind;
@property uint64_t gpuBase;
@property BOOL retiring,registered;
@property size_t bufferOffset;
@property BOOL coherent,tracked;
@property(strong) id<MTLBuffer> buffer;
@property(strong) NSMutableDictionary *descriptorSlots;
@end
@implementation BWPool
@end
#include "pool_runtime.h"
@interface BWBuffer : NSObject { @public bwu_pool backing; }
@property struct bwu_buffer value;
@property(strong) BWPool *pool;
@property(strong) BWDevice *device;
@property NVNmemoryPool *rawPool;
@property uint64_t poolGeneration;
@property(strong) NSString *label;
@end
@implementation BWBuffer
@end
@interface BWAddressRecord : NSObject
@property(strong) BWPool *pool;
@property(strong) BWDevice *device;
@property size_t offset,bytes;
@property uint64_t generation;
@end
@implementation BWAddressRecord
@end
@interface BWTexture : NSObject
@property(strong) id<MTLTexture> texture;
@property(strong) BWPool *pool;
@property size_t offset, stride;
@property TextureBuilder metadata;
@property(strong) BWDevice *device;
@property NVNmemoryPool *rawPool;
@property uint64_t poolGeneration,identity;
@property(strong) BWTexture *base;
@property(strong) BWPoolRange *mapping;
@property TextureView selection;
@property BOOL selectedAttachment;
@property(strong) NSString *label;
@property int width, height, format;
@property nvn_texdesc descriptor;
@property nvn_texlayout layout;
@property BOOL blockLinear, gpuWritten, depth;
@property(strong) NSData *uploadHash;
@property uint64_t drawEpoch, presentedEpoch;
@end
@implementation BWTexture
@end
@interface BWProgram : NSObject
@property(strong) BWDevice *device;
@property BOOL codeRetired;
@property(strong) NSDictionary<NSNumber *, id<MTLFunction>> *functions;
@property(strong) NSDictionary *stageShaders;
@property(strong) NSArray<NSDictionary *> *codeSources;
@end
@implementation BWProgram
@end
@interface BWCommandMemory : NSObject
@property void *memory,*owned;
@property size_t size,used;
@property(strong) BWPool *pool;
@end
@implementation BWCommandMemory
- (void)dealloc { free(_owned); }
@end
@interface BWRecordedCommand : NSObject
@property(strong) BWCommandMemory *command,*control;
@property size_t commandOffset,controlOffset;
@end
@implementation BWRecordedCommand
@end
@interface BWRecordedLink : NSObject
@property(strong) BWCommandMemory *previous,*next;
@property size_t offset;
@end
@implementation BWRecordedLink
@end
@interface BWCommands : NSObject
@property(strong) BWDevice *device;
@property(strong) NSMutableArray<NSDictionary *> *commands;
@property(strong) NSMutableArray<BWRecordedCommand *> *records;
@property(strong) NSMutableArray<BWRecordedLink *> *links;
@property(strong) BWCommandMemory *commandMemory,*controlMemory;
@property PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC callback;
@property void *callbackData;
@property uint64_t generation;
@property pthread_t callbackOwner;
@property BOOL callbackActive,externalMemory;
@property BOOL recording;
@end
@implementation BWCommands
@end
@class BWReplay;
@interface BWQueue : NSObject
@property(strong) BWDevice *device;
@property(strong) id<MTLCommandQueue> native;
@property(strong) id<MTLCommandBuffer> latest;
@property(strong) BWReplay *replay;
@property int flags;
@property void *ownedArena;
@property void *arena,*guestObject;
@property size_t arenaBytes;
@property BWQueueArenaLayout arenaLayout;
@property BOOL borrowedArena;
@property uint64_t arenaIdentity,preparedSubmissions,commandUsed,controlUsed;
@end
@implementation BWQueue
- (void)dealloc { free(_ownedArena); }
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
@property(strong) NSData *bindings;
@property unsigned maxVertices,maxInstances,storageSlot;
@property(strong) NSData *storageMinimum;
@property BOOL metalPrivateScratchVerified;
@end
@implementation BWShader
@end

@interface BWDescriptorSlot : NSObject
@property(strong) id value;
@property(strong) NSData *witness;
@property size_t physicalOffset;
@end
@implementation BWDescriptorSlot
@end
@interface BWDescriptors : NSObject
@property(strong) BWPool *pool;
@property size_t offset;
@property int count;
@property BOOL sampler;
@property(strong) NSMutableDictionary *entries;
@end
@implementation BWDescriptors
@end
@interface BWSampler : NSObject
@property(strong) BWDevice *device;
@property(strong) id<MTLSamplerState> state;
@end
@implementation BWSampler
@end
@interface BWScratchRecord : NSObject
@property(strong) BWPool *pool;
@property(strong) BWDevice *device;
@property uint64_t generation;
@property size_t offset,bytes;
@end
@implementation BWScratchRecord
@end
@interface BWReplay : NSObject
@property(strong) BWTexture *target, *depth;
@property(strong) BWProgram *program;
@property(strong) BWScratchRecord *scratch;
@property(strong) NSDictionary *viewport, *scissor;
@property(strong) NSData *attributes, *streams, *depthState;
@property(strong) NSData *colorState,*channelState,*polygonState,*multisampleState,*blendColor;
@property(strong) NSMutableDictionary<NSNumber *,NSData *> *blendStates;
@property(strong) NSDictionary *depthRange,*polygonOffset;
@property(strong) BWDescriptors *texturePool, *samplerPool;
@property(strong) NSMutableDictionary *uniforms, *storage, *textures, *vertices;
@end
@implementation BWReplay
- (instancetype)init { if((self=[super init])){_uniforms=[NSMutableDictionary new];_storage=[NSMutableDictionary new];_textures=[NSMutableDictionary new];_vertices=[NSMutableDictionary new];_blendStates=[NSMutableDictionary new];}return self;}
@end
@interface BWPipeline : NSObject
@property(strong) id<MTLRenderPipelineState> state;
@property(strong) MTLRenderPipelineReflection *reflection;
@end
@implementation BWPipeline
@end
typedef struct { uint64_t serial; } BWObject;
typedef struct { NVNdevice *device; void *memory; size_t size; int flags; } PoolBuilder;
_Static_assert(offsetof(PoolBuilder,memory)==BW_POOL_DEFAULT_WRITE_OFFSET&&
               offsetof(PoolBuilder,size)==16&&offsetof(PoolBuilder,flags)==24&&
               sizeof(((PoolBuilder*)0)->flags)==4,"base-v0 pool builder defaults span");
typedef struct { NVNdevice *device; int flags; } DeviceBuilder;
typedef struct { NVNdevice *device; int flags; size_t command, control, compute; void *memory; size_t memoryBytes; } QueueBuilder;
typedef struct { uint32_t format:8,stream:4,offset:20; } VertexAttribute;
typedef struct { uint32_t stride,divisor; } VertexStream;


typedef struct { NVNdevice *device; void *native; NVNtexture *textures[4]; int count, interval; } WindowBuilder;
enum { BWDescriptorBytes=32, BWReservedDescriptors=256, BWMaxSamplerDescriptors=4096 };
_Static_assert(sizeof(PoolBuilder)<=sizeof(NVNmemoryPoolBuilder),"pool builder ABI");
_Static_assert(sizeof(TextureBuilder)<=sizeof(NVNtextureBuilder),"texture builder ABI");
_Static_assert(sizeof(WindowBuilder)<=sizeof(NVNwindowBuilder),"window builder ABI");
_Static_assert(sizeof(QueueBuilder)<=sizeof(NVNqueueBuilder),"queue builder ABI");
static id<MTLDevice> gpu;
static id<MTLCommandQueue> commandQueue;
static CAMetalLayer *displayLayer;
static BOOL platformInitialized;
static uintptr_t platformDisplay,platformLayer,platformSequence;
static NSMutableDictionary<NSNumber *, id> *objects;
static NSMutableDictionary<NSNumber *, NSValue *> *objectSpans;
static NSMutableDictionary<NSNumber *, NSArray *> *handles;
static NSMutableDictionary<NSArray *,BWPipeline *> *pipelines;
static NSMutableDictionary<NSNumber *,id<MTLDepthStencilState>> *depthStates;
static dispatch_group_t allGPU;
static NSMutableArray<BWPool *> *pools;
static NSMutableArray<BWShader *> *shaders;
static uint64_t serial;
static botw_graphics_error errorCallback;
static botw_graphics_memory_range memoryValidator;
static botw_graphics_memory_access memoryAccessValidator;
static botw_graphics_command_memory_invoker commandMemoryInvoker;
static BOOL textureTrackingAvailable;
static void texturePrepareWrite(void *dst,size_t bytes);
static void textureWriteBytes(void *destination,const void *source,size_t bytes,const char *reason);
static void textureReadBytes(const void *source,void *destination,size_t bytes,const char *reason);
static void counterConfigureDevice(BWDevice *device);
static void linearConfigureDevice(BWDevice *device);
static void validateCounterAddress(BWAddressRecord *record,BWDevice *device);
static _Thread_local char lastError[1024];
static _Thread_local const char *currentEntry;
static pthread_mutex_t graphicsLock=PTHREAD_MUTEX_INITIALIZER;
static _Thread_local unsigned graphicsDepth;
static _Thread_local BOOL pendingError,pendingFatal;
@interface BWGraphicsFailure : NSException
@end
@implementation BWGraphicsFailure
@end
/* Public calls serialize all object tables, recording state and GPU submission.
 * Errors unwind internally first. The host callback may siglongjmp, so it must
 * run after ARC locals/pools drain and the outermost graphics lock is released. */
static void graphicsBegin(void){
    if(graphicsDepth++==0){pthread_mutex_lock(&graphicsLock);pendingError=NO;pendingFatal=NO;}
}
static void graphicsEnd(void){
    if(--graphicsDepth)return;
    botw_graphics_error callback=errorCallback;BOOL notify=pendingError,terminate=pendingFatal;
    pendingError=NO;pendingFatal=NO;pthread_mutex_unlock(&graphicsLock);
    if(notify&&callback)callback(lastError);
    if(terminate)abort();
}
static id<MTLRenderPipelineState> presentPipeline;
static char completionKey;
@interface BWPresentedFrame : NSObject
@property(strong) id<MTLBuffer> pixels;
@property uint32_t width,height;
@property size_t pitch;
@property uint64_t number,epoch;
@property BOOL gpuDone,screenDone,failed,counted;
@end
@implementation BWPresentedFrame
@end
@interface BWPresentation : NSObject
@property(strong) NSLock *lock;
@property(strong) BWPresentedFrame *latest;
@property uint64_t completed;
@property BOOL capture,closed;
@end
@implementation BWPresentation
- (instancetype)init{if((self=[super init]))_lock=[NSLock new];return self;}
@end
static BWPresentation *presentation;
static pthread_mutex_t presentationPointerLock=PTHREAD_MUTEX_INITIALIZER;
/* ARC strong loads also need synchronization: retain the snapshot while the
 * global pointer cannot be replaced, then use its own lock after releasing. */
static BWPresentation *presentationSnapshot(void){
    pthread_mutex_lock(&presentationPointerLock);BWPresentation *state=presentation;
    pthread_mutex_unlock(&presentationPointerLock);return state;
}
static void replacePresentation(BWPresentation *state){
    pthread_mutex_lock(&presentationPointerLock);presentation=state;
    pthread_mutex_unlock(&presentationPointerLock);
}
static void presented(BWPresentation *state,BWPresentedFrame *frame,BOOL gpuDone,BOOL screenDone,BOOL failed){
    [state.lock lock];frame.gpuDone|=gpuDone;frame.screenDone|=screenDone;frame.failed|=failed;
    if(!state.closed&&!frame.counted&&!frame.failed&&frame.epoch&&frame.gpuDone&&frame.screenDone){
        frame.counted=YES;frame.number=++state.completed;if(frame.pixels)state.latest=frame;
    }
    [state.lock unlock];
}
uint64_t botw_graphics_presented_frame_count(void){BWPresentation *state=presentationSnapshot();[state.lock lock];uint64_t count=state.completed;[state.lock unlock];return count;}
void botw_graphics_set_capture_enabled(int enabled){BWPresentation *state=presentationSnapshot();[state.lock lock];state.capture=enabled!=0;[state.lock unlock];}
int botw_graphics_copy_latest_frame(void *rgba,size_t capacity,uint32_t *width,uint32_t *height,uint64_t *number){
    BWPresentation *state=presentationSnapshot();[state.lock lock];BWPresentedFrame *frame=state.latest;[state.lock unlock];
    if(!frame)return 0;if(width)*width=frame.width;if(height)*height=frame.height;if(number)*number=frame.number;
    if(!rgba||capacity<(size_t)frame.width*frame.height*4)return 0;
    for(uint32_t y=0;y<frame.height;y++){const uint8_t *src=(const uint8_t*)frame.pixels.contents+y*frame.pitch;uint8_t *dst=(uint8_t*)rgba+(size_t)y*frame.width*4;
        for(uint32_t x=0;x<frame.width;x++){dst[x*4]=src[x*4+2];dst[x*4+1]=src[x*4+1];dst[x*4+2]=src[x*4];dst[x*4+3]=src[x*4+3];}}
    return 1;
}


static int report(const char *format, ...) {
    const char *entry=currentEntry?currentEntry:"<host>";if(!strncmp(entry,"w_nvn",5))entry+=2;
    int prefix=snprintf(lastError,sizeof lastError,"entry=%s: ",entry);
    size_t offset=prefix>0?MIN((size_t)prefix,sizeof lastError-1):0;
    va_list args;va_start(args,format);vsnprintf(lastError+offset,sizeof lastError-offset,format,args);va_end(args);
    fprintf(stderr,"[botw graphics] %s\n",lastError);
    pendingError=YES;return 0;
}
__attribute__((noreturn)) static void fatal(const char *name) {
    report("Unsupported or invalid NVN operation: %s",name);pendingFatal=YES;
    @throw [[BWGraphicsFailure alloc] initWithName:@"BWGraphicsFailure" reason:nil userInfo:nil];
}
const char *botw_graphics_last_error(void) { return lastError; }
/* Apple Feature Set Tables specify ASTC LDR/sRGB from Apple2 onward.
 * The NVN upload/sample path is verified for every supported block footprint. */
static BOOL supportsASTC(void){return gpu&&[gpu supportsFamily:MTLGPUFamilyApple2];}
static NSData *digest(const void *bytes,size_t size) {
    unsigned char hash[CC_SHA256_DIGEST_LENGTH]; CC_SHA256(bytes,(CC_LONG)size,hash);
    return [NSData dataWithBytes:hash length:sizeof hash];
}
static BOOL overlapsQueueArena(const void *memory,size_t bytes);
static BOOL overlapsCommandMemory(const void *memory,size_t bytes);
static BOOL memoryOverlap(const void *a,size_t na,const void *b,size_t nb);
static size_t objectStorageSize(id value){
    if([value isKindOfClass:[BWDevice class]])return sizeof(NVNdevice);
    if([value isKindOfClass:[BWPool class]])return sizeof(NVNmemoryPool);
    if([value isKindOfClass:[BWQueue class]])return sizeof(NVNqueue);
    if([value isKindOfClass:[BWTexture class]])return sizeof(NVNtexture);
    if([value isKindOfClass:[BWBuffer class]])return sizeof(NVNbuffer);
    if([value isKindOfClass:[BWProgram class]])return sizeof(NVNprogram);
    if([value isKindOfClass:[BWCommands class]])return sizeof(NVNcommandBuffer);
    if([value isKindOfClass:[BWSync class]])return sizeof(NVNsync);
    if([value isKindOfClass:[BWSampler class]])return sizeof(NVNsampler);
    if([value isKindOfClass:[BWWindow class]])return sizeof(NVNwindow);
    if([value isKindOfClass:[BWDescriptors class]])return ((BWDescriptors*)value).sampler?sizeof(NVNsamplerPool):sizeof(NVNtexturePool);
    fatal("unknown object storage type");
}
static void validateObjectStorage(void *raw,size_t bytes){
    if(!raw||overlapsQueueArena(raw,bytes)||overlapsCommandMemory(raw,bytes))fatal("NVN object storage overlaps live CPU queue arena/recording memory");
    for(NSNumber *key in objectSpans){NSRange span=objectSpans[key].rangeValue;id existing=objects[key];
        if([existing isKindOfClass:[BWCommands class]]&&((BWCommands*)existing).callbackActive&&memoryOverlap(raw,bytes,(const void*)span.location,span.length))fatal("NVN object storage is busy in a memory callback");}
}
static void put(void *raw,id value) {
    size_t bytes=objectStorageSize(value);validateObjectStorage(raw,bytes);
    ((BWObject*)raw)->serial=++serial;objects[@(serial)]=value;
    objectSpans[@(serial)]=[NSValue valueWithRange:NSMakeRange((NSUInteger)raw,bytes)];
}
static id get(const void *raw,Class expected) {
    uint64_t key=0;char detail[384];
    if(raw&&memoryAccessValidator&&!memoryAccessValidator(raw,sizeof key,0)){
        snprintf(detail,sizeof detail,"object lifetime/type expected=%s raw=%p serial=<unreadable> actual=<none>",class_getName(expected),raw);fatal(detail);
    }
    if(raw)memcpy(&key,raw,sizeof key);id value=raw?objects[@(key)]:nil;
    if(!value||![value isKindOfClass:expected]){
        snprintf(detail,sizeof detail,"object lifetime/type expected=%s raw=%p serial=0x%llx actual=%s",class_getName(expected),raw,(unsigned long long)key,value?class_getName([value class]):"<none>");fatal(detail);
    }
    return value;
}
static void removeObject(void *raw) {
    NSNumber *key=@(((BWObject*)raw)->serial);[objects removeObjectForKey:key];[objectSpans removeObjectForKey:key];((BWObject*)raw)->serial=0;
}
static BWPool *address(uint64_t addr,size_t bytes,size_t *offset) { return poolAddress(addr,bytes,offset); }
static BOOL range(size_t offset,size_t bytes,size_t size) { return offset<=size && bytes<=size-offset; }
static BOOL memoryOverlap(const void *a,size_t na,const void *b,size_t nb){
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    if(na>UINTPTR_MAX-x||nb>UINTPTR_MAX-y)return YES;
    return na&&nb&&(x<=y?y-x<na:x-y<nb);
}
static BOOL overlapsQueueArena(const void *memory,size_t bytes){
    for(id value in objects.allValues)if([value isKindOfClass:[BWQueue class]]){
        BWQueue *queue=value;if(memoryOverlap(memory,bytes,queue.arena,queue.arenaBytes))return YES;
    }
    return NO;
}
static void finishBuffer(id<MTLCommandBuffer> buffer) {
    if(!buffer)return; [buffer waitUntilCompleted];
    if(buffer.status==MTLCommandBufferStatusError)fatal(buffer.error.localizedDescription.UTF8String);
}
#include "pool_resolver.inc"
/* Completion handlers must be registered before commit. Sync waits retain the
 * group through the command buffer rather than registering a late handler. */
static void texturePrepareCommit(id<MTLCommandBuffer> cb);
static void texturePublishCommitted(id<MTLCommandBuffer> cb);
static void commitBuffer(id<MTLCommandBuffer> cb) {
    if(!cb)fatal("Metal command allocation failed");texturePrepareCommit(cb);poolPrepareCommit(cb);
    BWCompletion *completion=[BWCompletion new];completion.group=dispatch_group_create();
    dispatch_group_enter(completion.group);
    objc_setAssociatedObject(cb,&completionKey,completion,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> b){(void)b;dispatch_group_leave(completion.group);}];
    dispatch_group_t group=allGPU;dispatch_group_enter(group);
    [cb addCompletedHandler:^(id<MTLCommandBuffer> b){(void)b;dispatch_group_leave(group);}];
    [cb commit];poolNoteCommitted(cb);texturePublishCommitted(cb);
}
/* A failed encoding/preparation must not replace the last submitted fence.
 * A post-commit publication error still retains the real in-flight buffer. */
static void commitQueueBuffer(BWQueue *queue,id<MTLCommandBuffer> cb){@try{commitBuffer(cb);}@finally{if(cb.status>=MTLCommandBufferStatusCommitted)queue.latest=cb;}}
static int initializeGraphicsImpl(void *layer,botw_graphics_error error,botw_graphics_memory_range validator) {
    if(gpu)return report("Graphics already initialized");
    errorCallback=error; memoryValidator=validator;memoryAccessValidator=NULL;commandMemoryInvoker=NULL;textureTrackingAvailable=NO;lastError[0]=0;
    gpu=MTLCreateSystemDefaultDevice(); if(!gpu)return report("No Metal device");
    commandQueue=[gpu newCommandQueue]; if(!commandQueue){gpu=nil;return report("No Metal command queue");}
    allGPU=dispatch_group_create();replacePresentation([BWPresentation new]);
    objects=[NSMutableDictionary new];objectSpans=[NSMutableDictionary new];handles=[NSMutableDictionary new]; pipelines=[NSMutableDictionary new];depthStates=[NSMutableDictionary new];
    pools=[NSMutableArray new]; shaders=[NSMutableArray new]; serial=0;
    displayLayer=(__bridge CAMetalLayer *)layer;
    if(displayLayer){displayLayer.device=gpu;displayLayer.pixelFormat=MTLPixelFormatBGRA8Unorm;displayLayer.framebufferOnly=NO;}
    return 1;
}
static void shutdownGraphicsImpl(void) {
    BWPresentation *state=presentationSnapshot();[state.lock lock];state.closed=YES;state.latest=nil;[state.lock unlock];
    if(allGPU)dispatch_group_wait(allGPU,DISPATCH_TIME_FOREVER);
    poolShutdownPools();
    for(id o in objects.allValues)if([o isKindOfClass:[BWTexture class]])nvn_texflush_forget(((BWTexture*)o).identity);
    nvn_texown_reset_quiescent();nvn_texflush_reset_quiescent();
    objects=nil;objectSpans=nil;handles=nil;pipelines=nil;depthStates=nil;pools=nil;shaders=nil;presentPipeline=nil;
    platformInitialized=NO;platformDisplay=0;platformLayer=0;
    displayLayer=nil;commandQueue=nil;gpu=nil;memoryValidator=NULL;memoryAccessValidator=NULL;commandMemoryInvoker=NULL;allGPU=nil;replacePresentation(nil);
}
/* The runtime owns guest output validation and the real kernel event. Keep
 * these failures return-only: its lifecycle mutex must never be bypassed by
 * the NVN error callback's permitted longjmp. All outputs commit on success. */
static int platformReject(const char *message){
    snprintf(lastError,sizeof lastError,"VI Metal backend: %s",message);
    fprintf(stderr,"[botw graphics] %s\n",lastError);return 0;
}
static BOOL platformSurfaceSize(uint32_t *width,uint32_t *height){
    if(!gpu||!commandQueue||!displayLayer||displayLayer.device!=gpu)return NO;
    CGSize size=displayLayer.drawableSize;
    if(!isfinite(size.width)||!isfinite(size.height)||size.width<1||size.height<1||
       size.width>16384||size.height>16384||floor(size.width)!=size.width||floor(size.height)!=size.height)return NO;
    if(width)*width=(uint32_t)size.width;if(height)*height=(uint32_t)size.height;return YES;
}
static uintptr_t platformToken(void){
    /* Never recycle across shutdown/reinit, including stale runtime tokens. */
    if(platformSequence==UINTPTR_MAX)return 0;return ++platformSequence;
}
static int platformInitializeImpl(void){
    if(platformInitialized)return platformReject("already initialized");
    if(!platformSurfaceSize(NULL,NULL))return platformReject("requires a live CAMetalLayer and drawable size");
    platformInitialized=YES;return 1;
}
static int platformOpenDisplayImpl(uintptr_t *out){
    if(!out)return platformReject("null display output");
    if(!platformInitialized||!platformSurfaceSize(NULL,NULL))return platformReject("display is not initialized/live");
    if(platformDisplay)return platformReject("default display already open");
    uintptr_t token=platformToken();if(!token)return platformReject("display identity exhausted");
    platformDisplay=token;*out=token;return 1;
}
static int platformDisplayLiveImpl(uintptr_t display){
    if(!platformInitialized||!display||display!=platformDisplay||!platformSurfaceSize(NULL,NULL))return platformReject("unknown, expired, or unavailable display");
    return 1;
}
static int platformDisplaySizeImpl(uintptr_t display,uint32_t *width,uint32_t *height){
    if(!width||!height)return platformReject("null display size output");
    if(!platformDisplayLiveImpl(display))return 0;
    return platformSurfaceSize(width,height);
}
static int platformCreateLayerImpl(uintptr_t display,uintptr_t *out){
    if(!out)return platformReject("null layer output");if(!platformDisplayLiveImpl(display))return 0;
    if(platformLayer)return platformReject("only one host layer is supported");
    uintptr_t token=platformToken();if(!token)return platformReject("layer identity exhausted");
    platformLayer=token;*out=token;return 1;
}
static int platformLayerLive(uintptr_t layer){
    if(!platformInitialized||!layer||layer!=platformLayer||!platformSurfaceSize(NULL,NULL))return platformReject("unknown, expired, or unavailable layer");
    return 1;
}
static int platformScalingImpl(uintptr_t layer,int mode){
    if(!platformLayerLive(layer))return 0;
    /* VI FitToLayer=2. Presentation renders the entire source texture onto
     * the drawable's complete quad, including unequal source/target aspect. */
    if(mode!=2)return platformReject("unsupported scaling mode (requires FitToLayer=2)");
    displayLayer.contentsGravity=kCAGravityResize;return 1;
}
static int platformNativeWindowImpl(uintptr_t layer,uintptr_t *out){
    if(!out)return platformReject("null native window output");if(!platformLayerLive(layer))return 0;
    *out=(uintptr_t)(__bridge void *)displayLayer;return 1;
}
int botw_graphics_register_shader(const void *control,size_t cs,const void *code,size_t ds,
                                  NVNshaderStage stage,const char *source,const char *entry) {
    return botw_graphics_register_shader_with_bindings(control,cs,code,ds,stage,source,entry,NULL,0);
}
static int registerShaderImpl(const void *control,size_t cs,const void *code,size_t ds,
    NVNshaderStage stage,const char *source,const char *entry,const botw_graphics_shader_binding *bindings,size_t count,const botw_graphics_shader_draw_profile *profile){
    if(count>64 || (count&&!bindings))return report("Invalid shader binding metadata");
    for(size_t i=0;i<count;i++){if(bindings[i].kind<0 || bindings[i].kind>BOTW_GRAPHICS_STORAGE_ADDRESS_LOW || bindings[i].nvn_slot>=(bindings[i].kind>=BOTW_GRAPHICS_STORAGE?16:128) || bindings[i].metal_slot>=(bindings[i].kind==BOTW_GRAPHICS_TEXTURE?128:16))return report("Shader binding metadata exceeds Metal limits");
        for(size_t j=0;j<i;j++){BOOL same=(bindings[i].kind==bindings[j].kind)||((bindings[i].kind==BOTW_GRAPHICS_UNIFORM||bindings[i].kind>=BOTW_GRAPHICS_STORAGE)&&(bindings[j].kind==BOTW_GRAPHICS_UNIFORM||bindings[j].kind>=BOTW_GRAPHICS_STORAGE));if(same&&bindings[i].metal_slot==bindings[j].metal_slot)return report("Duplicate Metal resource binding map slot");}}
    if(!gpu || !control || !code || !source || !entry || cs<4 || ds<0x80 || cs>1<<20 || ds>16<<20 ||
       (stage!=NVN_SHADER_STAGE_VERTEX && stage!=NVN_SHADER_STAGE_FRAGMENT))
        return report("Invalid shader registration or unsupported shader stage");
    uint32_t cm,dm;memcpy(&cm,control,4);memcpy(&dm,code,4);
    if(cm!=0x98761234 || dm!=0x12345678)return report("Invalid shader input magic");
    if(profile){
        if(stage!=NVN_SHADER_STAGE_VERTEX||!profile->max_vertex_count||profile->max_vertex_count>64||!profile->max_instance_count||profile->max_instance_count>64||profile->storage_slot>=16||!profile->minimum_storage_bytes||profile->minimum_storage_bytes[0])return report("Invalid shader draw profile");
        BOOL matched=NO;for(size_t i=0;i<count;i++)if(bindings[i].kind==BOTW_GRAPHICS_STORAGE&&bindings[i].nvn_slot==profile->storage_slot)matched=YES;
        if(!matched)return report("Shader draw profile has no matching storage binding");
        for(unsigned i=1;i<=profile->max_instance_count;i++)if(!profile->minimum_storage_bytes[i]||profile->minimum_storage_bytes[i]<profile->minimum_storage_bytes[i-1]||profile->minimum_storage_bytes[i]>gpu.maxBufferLength)return report("Invalid shader draw profile storage minimum");
    }
    if(strstr(source,"raster_order_group"))return report("Fragment interlock/raster-order-group shaders are not implemented");
    NSError *error=nil; id<MTLLibrary> library=[gpu newLibraryWithSource:@(source) options:nil error:&error];
    if(!library)return report("Metal shader compilation failed: %s",error.localizedDescription.UTF8String);
    id<MTLFunction> function=[library newFunctionWithName:@(entry)];
    if(!function || (stage==NVN_SHADER_STAGE_VERTEX?function.functionType!=MTLFunctionTypeVertex:
                    function.functionType!=MTLFunctionTypeFragment))return report("Metal shader entry/stage mismatch");
    BWShader *shader=[BWShader new];shader.controlHash=digest(control,cs);shader.codeHash=digest(code,ds);
    shader.controlSize=cs;shader.codeSize=ds;shader.stage=stage;shader.function=function;shader.bindings=[NSData dataWithBytes:bindings length:count*sizeof *bindings];
    if(profile){shader.maxVertices=profile->max_vertex_count;shader.maxInstances=profile->max_instance_count;shader.storageSlot=profile->storage_slot;shader.storageMinimum=[NSData dataWithBytes:profile->minimum_storage_bytes length:(profile->max_instance_count+1)*sizeof(size_t)];}
    [shaders addObject:shader];return 1;
}
static int registerScratchProofImpl(const void *control,size_t cs,const void *code,size_t ds,NVNshaderStage stage,const void *spv,size_t bytes){
    char detail[256]="Invalid shader scratch identity";if(!control||cs<4||cs>1u<<20||ds>16u<<20||!bwScratchProof(code,ds,stage,spv,bytes,detail,sizeof detail))return report("Shader scratch proof rejected: %s",detail);
    NSData *controlHash=digest(control,cs),*codeHash=digest(code,ds);BOOL found=NO;
    for(BWShader *s in shaders)if(s.stage==stage&&s.controlSize==cs&&s.codeSize==ds&&[s.controlHash isEqual:controlHash]&&[s.codeHash isEqual:codeHash]){s.metalPrivateScratchVerified=YES;found=YES;}
    return found?1:report("Shader scratch proof has no exact registered identity");
}
static void uploadTexture(BWTexture *texture,id<MTLCommandBuffer> command);
static void textureLive(BWTexture *texture);
static int readTextureImpl(const NVNtexture *raw,unsigned level,unsigned layer,void *pixels,size_t bytes) {
    BWTexture *t=get(raw,[BWTexture class]);textureLive(t);
    if(t.format!=NVN_FORMAT_RGBA8 && t.format!=NVN_FORMAT_BGRA8)return report("Readback requires RGBA8/BGRA8 color texture");
    if(level>=t.descriptor.levels||layer>=t.layout.layers)return report("Texture readback level/layer bounds");
    unsigned width=MAX(1,t.width>>level),height=MAX(1,t.height>>level);
    size_t size=(size_t)width*height*4,pitch=((size_t)width*4+255)&~255ull;
    if(!pixels || bytes<size)return report("Texture readback buffer bounds");
    /* Readback uses the backend transfer queue. Draw/present now use their
     * owning NVN Metal queues, so a transfer-queue wait alone is insufficient. */
    for(id value in objects.allValues)if([value isKindOfClass:[BWQueue class]])finishBuffer(((BWQueue*)value).latest);
    id<MTLBuffer> buffer=[gpu newBufferWithLength:pitch*height options:MTLResourceStorageModeShared];if(!buffer)return report("Texture readback allocation failed");
    id<MTLCommandBuffer> cb=[commandQueue commandBuffer];if(!cb)return report("Texture readback command allocation failed");uploadTexture(t,cb);id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];if(!b){t.uploadHash=nil;return report("Texture readback encoder allocation failed");}
    if(!b)fatal("texture readback blit encoder allocation");@try{[b copyFromTexture:t.texture sourceSlice:layer sourceLevel:level sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(width,height,1) toBuffer:buffer destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*height];}@finally{[b endEncoding];}commitBuffer(cb);finishBuffer(cb);
    for(unsigned y=0;y<height;y++)memcpy((char*)pixels+(size_t)y*width*4,(char*)buffer.contents+(size_t)y*pitch,width*4);return 1;
}

#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__); \
    _Static_assert(__builtin_types_compatible_p(__typeof__(&h_##name), NVNT_##name), "NVN signature: " #name); \
    static ret h_##name(__VA_ARGS__)
NVN(nvnDeviceBuilderSetDefaults,void,NVNdeviceBuilder *b){memset(b,0,sizeof *b);}
NVN(nvnDeviceBuilderSetFlags,void,NVNdeviceBuilder *b,int flags){((DeviceBuilder*)b)->flags=flags;}
/* Only ordinary, exact, identity-component formats admitted by the current
 * texture backend are valid arguments to Metal's linear alignment query. */
static BOOL linearColorFormat(int format,uint32_t *pixel){
    if(format>=NVN_FORMAT_STENCIL8&&format<=NVN_FORMAT_DEPTH32F_STENCIL8)return NO;
    int bw,bh;uint8_t swizzle[4];
    if(!nvn_fmt_bpb(format,&bw,&bh)||bw!=1||bh!=1||nvn_fmt_to_mtl(format,pixel,swizzle)!=1)return NO;
    for(unsigned i=0;i<4;i++)if(swizzle[i]!=i+NVN_SWZ_R)return NO;
    return YES;
}
static void linearConfigureDevice(BWDevice *device){
    size_t alignments[0x9c],count=0;
    for(int format=1;format<=0x9c;format++){uint32_t pixel;if(linearColorFormat(format,&pixel))alignments[count++]=[gpu minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)pixel];}
    BWLinearPolicy policy;bw_linear_policy_make(alignments,count,[gpu supportsFamily:MTLGPUFamilyApple1],&policy);device.linearPolicy=policy;
    fprintf(stderr,"[botw graphics] linear_storage_policy=%s source=Metal.minimumLinearTextureAlignmentForPixelFormat scope=exact_identity_ordinary_color formats=%zu host_format_max=%zu texture_stride_alignment=%zu render_stride_alignment=%zu apple_render_target=%s pool_scope=coherent_only\n",
            policy.valid?"available":"unavailable",policy.formatCount,policy.formatMax,policy.textureAlignment,policy.renderAlignment,policy.renderSupported?"supported":"unsupported");
}
NVN(nvnDeviceInitialize,NVNboolean,NVNdevice *d,const NVNdeviceBuilder *b){if(!gpu)return report("Metal not initialized");BWDevice *device=[BWDevice new];device.flags=((const DeviceBuilder*)b)->flags;device.windowOrigin=NVN_WINDOW_ORIGIN_MODE_UPPER_LEFT;counterConfigureDevice(device);linearConfigureDevice(device);put(d,device);return 1;}
NVN(nvnDeviceFinalize,void,NVNdevice *d){get(d,[BWDevice class]);removeObject(d);}
NVN(nvnDeviceSetWindowOriginMode,void,NVNdevice *raw,NVNwindowOriginMode mode){
    BWDevice *device=get(raw,[BWDevice class]);
    if(mode!=NVN_WINDOW_ORIGIN_MODE_UPPER_LEFT)fatal("window origin requires UpperLeft=1; other raster transforms are unverified");
    /* Metal's render-target, viewport and scissor coordinates already use
     * this origin. Preserve the actual device state without an extra Y flip. */
    device.windowOrigin=mode;
}
NVN(nvnDeviceGetProcAddress,PFNNVNGENERICFUNCPTRPROC,const NVNdevice *d,const char *name){if(d)get(d,[BWDevice class]);return (PFNNVNGENERICFUNCPTRPROC)botw_nvn_bootstrap_loader(name);}
NVN(nvnDeviceGetInteger,void,const NVNdevice *d,NVNdeviceInfo query,int *value){
    BWDevice *device=get(d,[BWDevice class]);if(!value)fatal("nvnDeviceGetInteger null output");
    switch(query){case NVN_DEVICE_INFO_API_MAJOR_VERSION:*value=52;break;
    case NVN_DEVICE_INFO_API_MINOR_VERSION:*value=32;break;
    case NVN_DEVICE_INFO_MEMORY_POOL_PAGE_SIZE:*value=(int)bw_pool_policy().mappingPageBytes;break;
    case NVN_DEVICE_INFO_COUNTER_ALIGNMENT:*value=16;break;
    case NVN_DEVICE_INFO_LINEAR_TEXTURE_STRIDE_ALIGNMENT:
    case NVN_DEVICE_INFO_LINEAR_RENDER_TARGET_STRIDE_ALIGNMENT:{
        BWLinearPolicy policy=device.linearPolicy;size_t alignment=bw_linear_alignment(&policy,query==NVN_DEVICE_INFO_LINEAR_TEXTURE_STRIDE_ALIGNMENT?0x10:0x40);
        if(!alignment)fatal("linear texture/linear render target policy is unavailable on this Metal device");*value=(int)alignment;break;}
    case NVN_DEVICE_INFO_COMMAND_BUFFER_COMMAND_ALIGNMENT:*value=BW_COMMAND_MEMORY_ALIGNMENT;break;
    case NVN_DEVICE_INFO_COMMAND_BUFFER_CONTROL_ALIGNMENT:*value=BW_CONTROL_MEMORY_ALIGNMENT;break;
    case NVN_DEVICE_INFO_COMMAND_BUFFER_MIN_COMMAND_SIZE:*value=BW_MIN_COMMAND_MEMORY;break;
    case NVN_DEVICE_INFO_COMMAND_BUFFER_MIN_CONTROL_SIZE:*value=BW_MIN_CONTROL_MEMORY;break;
    /* NVN compatibility constraints; the Metal backend requires no guest
     * backing for per-invocation private variables or compiler spills. */
    case NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_ALIGNMENT:*value=4096;break;
    case NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_GRANULARITY:*value=131072;break;
    case NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_SCALE_FACTOR_MINIMUM:
    case NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_SCALE_FACTOR_RECOMMENDED:
    case NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_COMPUTE_SCALE_FACTOR_MINIMUM:*value=0;break;
    case NVN_DEVICE_INFO_UNIFORM_BUFFER_ALIGNMENT:*value=256;break;
    case NVN_DEVICE_INFO_COLOR_BUFFER_BINDINGS:*value=1;break;
    case NVN_DEVICE_INFO_TEXTURE_DESCRIPTOR_SIZE:
    case NVN_DEVICE_INFO_SAMPLER_DESCRIPTOR_SIZE:*value=BWDescriptorBytes;break;
    case NVN_DEVICE_INFO_RESERVED_TEXTURE_DESCRIPTORS:
    case NVN_DEVICE_INFO_RESERVED_SAMPLER_DESCRIPTORS:*value=BWReservedDescriptors;break;
    case NVN_DEVICE_INFO_SUPPORTS_ASTC_FORMATS:*value=supportsASTC()?1:0;break;
    /* Actual CPU arena policy. Metal owns its separate driver allocations. */
    case NVN_DEVICE_INFO_QUEUE_COMMAND_MEMORY_DEFAULT_SIZE:*value=BW_QUEUE_DEFAULT_COMMAND_BYTES;break;
    case NVN_DEVICE_INFO_QUEUE_CONTROL_MEMORY_DEFAULT_SIZE:*value=BW_QUEUE_DEFAULT_CONTROL_BYTES;break;
    case NVN_DEVICE_INFO_QUEUE_COMPUTE_MEMORY_DEFAULT_SIZE:*value=0;break;
    case NVN_DEVICE_INFO_QUEUE_COMMAND_MEMORY_GRANULARITY:
    case NVN_DEVICE_INFO_QUEUE_COMMAND_MEMORY_MIN_SIZE:
    case NVN_DEVICE_INFO_QUEUE_CONTROL_MEMORY_MIN_SIZE:
    case NVN_DEVICE_INFO_QUEUE_CONTROL_MEMORY_GRANULARITY:*value=BW_QUEUE_ARENA_PAGE;break;
    /* These are bridge capabilities, not claims about the host GPU. Their
     * NVN state/command/shader semantics are not implemented. Querying false
     * lets applications select a supported path; executing them still fails. */
    case NVN_DEVICE_INFO_SUPPORTS_MIN_MAX_FILTERING:
    case NVN_DEVICE_INFO_SUPPORTS_STENCIL8_FORMAT:
    case NVN_DEVICE_INFO_SUPPORTS_CONSERVATIVE_RASTER:
    case NVN_DEVICE_INFO_SUPPORTS_ZERO_FROM_UNMAPPED_VIRTUAL_POOL_PAGES:
    case NVN_DEVICE_INFO_SUPPORTS_PASSTHROUGH_GEOMETRY_SHADERS:
    case NVN_DEVICE_INFO_SUPPORTS_VIEWPORT_SWIZZLE:
    case NVN_DEVICE_INFO_SUPPORTS_SPARSE_TILED_PACKAGED_TEXTURES:
    case NVN_DEVICE_INFO_SUPPORTS_ADVANCED_BLEND_MODES:
    case NVN_DEVICE_INFO_SUPPORTS_DRAW_TEXTURE:
    case NVN_DEVICE_INFO_SUPPORTS_TARGET_INDEPENDENT_RASTERIZATION:
    case NVN_DEVICE_INFO_SUPPORTS_FRAGMENT_COVERAGE_TO_COLOR:
    case NVN_DEVICE_INFO_SUPPORTS_POST_DEPTH_COVERAGE:
    case NVN_DEVICE_INFO_SUPPORTS_IMAGES_USING_TEXTURE_HANDLES:
    case NVN_DEVICE_INFO_SUPPORTS_SAMPLE_LOCATIONS:*value=0;break;
    default:{char detail[128];snprintf(detail,sizeof detail,"nvnDeviceGetInteger unsupported capability query=0x%x",(unsigned)query);fatal(detail);}}}
NVN(nvnDeviceFinalizeCommandHandle,void,NVNdevice *d,NVNcommandHandle h){get(d,[BWDevice class]);if(!handles[@(h)])fatal("invalid command handle");[handles removeObjectForKey:@(h)];}
NVN(nvnMemoryPoolBuilderSetDefaults,void,NVNmemoryPoolBuilder *b){
    uintptr_t address=(uintptr_t)b;
    if(!b||address>UINTPTR_MAX-(BW_POOL_DEFAULT_WRITE_OFFSET+BW_POOL_DEFAULT_WRITE_BYTES))fatal("memory pool builder defaults address overflow");
    void *destination=(void*)(address+BW_POOL_DEFAULT_WRITE_OFFSET);
    for(NSValue *value in objectSpans.allValues){NSRange span=value.rangeValue;if(memoryOverlap(destination,BW_POOL_DEFAULT_WRITE_BYTES,(void*)span.location,span.length))fatal("memory pool builder defaults overlap a live NVN object");}
    texturePrepareWrite(destination,BW_POOL_DEFAULT_WRITE_BYTES);
    unsigned char values[BW_POOL_DEFAULT_WRITE_BYTES]={0};int flags=BW_POOL_DEFAULT_FLAGS;
    memcpy(values+16,&flags,sizeof flags);memcpy(destination,values,sizeof values);
}
NVN(nvnMemoryPoolBuilderSetDevice,void,NVNmemoryPoolBuilder *b,NVNdevice *d){((PoolBuilder*)b)->device=d;}
NVN(nvnMemoryPoolBuilderSetStorage,void,NVNmemoryPoolBuilder *b,void *memory,size_t size){((PoolBuilder*)b)->memory=memory;((PoolBuilder*)b)->size=size;}
NVN(nvnMemoryPoolBuilderSetFlags,void,NVNmemoryPoolBuilder *b,int flags){((PoolBuilder*)b)->flags=flags;}
#include "memory_pool_family.inc"
NVN(nvnQueueBuilderSetDefaults,void,NVNqueueBuilder *raw){NVNdevice *device=((QueueBuilder*)raw)->device;memset(raw,0,sizeof *raw);QueueBuilder *b=(QueueBuilder*)raw;b->device=device;b->command=BW_QUEUE_DEFAULT_COMMAND_BYTES;b->control=BW_QUEUE_DEFAULT_CONTROL_BYTES;}
NVN(nvnQueueBuilderSetDevice,void,NVNqueueBuilder *b,NVNdevice *d){((QueueBuilder*)b)->device=d;}
static BOOL queueFlagsSupported(int flags){return flags==NVN_QUEUE_BUILDER_FLAGS_NONE||flags==NVN_QUEUE_BUILDER_FLAGS_NO_FRAGMENT_INTERLOCK;}
NVN(nvnQueueBuilderSetFlags,void,NVNqueueBuilder *raw,int flags){
    if(!queueFlagsSupported(flags))fatal("queue flags require NONE or NO_FRAGMENT_INTERLOCK=1; other policies are unverified");
    ((QueueBuilder*)raw)->flags=flags;
}
static void validateScratchSubmission(BWQueue *queue,NSArray<NSArray *> *groups);
#include "buffer_family.inc"
static void validateAddressSubmission(BWQueue *queue,NSArray<NSArray *> *groups);
static void validateTextureSubmission(BWQueue *queue,NSArray<NSArray *> *groups);
static void validateRenderSubmission(BWQueue *queue,NSArray<NSArray *> *groups);
static unsigned textureDescriptorIndex(uint64_t handle);
static unsigned samplerDescriptorIndex(uint64_t handle);
static NSString *resourceKey(NSNumber *stage,NSNumber *slot);
#include "queue_arena.inc"
NVN(nvnQueueInitialize,NVNboolean,NVNqueue *raw,const NVNqueueBuilder *builder){
    if(!builder||(memoryAccessValidator&&!memoryAccessValidator(builder,sizeof(QueueBuilder),0)))return report("CPU queue builder is not fully readable");
    QueueBuilder snapshot;memcpy(&snapshot,builder,sizeof snapshot);const QueueBuilder *b=&snapshot;get(b->device,[BWDevice class]);
    validateObjectStorage(raw,sizeof *raw);
    if(!queueFlagsSupported(b->flags))return report("Unsupported queue flags");
    BWQueue *queue=[BWQueue new];queue.device=get(b->device,[BWDevice class]);queue.flags=b->flags;
    /* Each NVN queue has its own ordered Metal submission stream. Normal
     * Metal fragment execution has no raster-order-group interlock; the
     * NO_FRAGMENT_INTERLOCK policy needs no added synchronization primitive. */
    queue.native=[gpu newCommandQueue];if(!queue.native)return report("Metal queue creation failed");
    queue.replay=[BWReplay new];if(!configureQueueArena(queue,b,raw,builder))return 0;
    put(raw,queue);return 1;
}
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
static id descriptorEntry(BWDescriptors *p,unsigned index);
#include "texture_family.inc"
NVN(nvnProgramInitialize,NVNboolean,NVNprogram *p,NVNdevice *d){BWProgram *program=[BWProgram new];program.device=get(d,[BWDevice class]);put(p,program);return 1;}
NVN(nvnProgramFinalize,void,NVNprogram *p){get(p,[BWProgram class]);removeObject(p);}
static const void *programCodeBytes(BWPoolRange *mapping){
    textureCheckBufferAuthority(mapping.canonical,mapping.length);
    if(mapping.physical.coherent){textureVMAccess(mapping.canonical,mapping.length,NO);return mapping.canonical;}
    /* Copied ordinary pools have distinct CPU cache and GPU-visible storage.
     * Matching code must observe Flush/Invalidate semantics, not refresh either. */
    const void *contents=mapping.buffer.contents;
    if(!contents||mapping.metalOffset>mapping.buffer.length||mapping.length>mapping.buffer.length-mapping.metalOffset)
        fatal("program code has no CPU-readable Metal storage");
    return (const char*)contents+mapping.metalOffset;
}
NVN(nvnProgramSetShaders,NVNboolean,NVNprogram *raw,int count,const NVNshaderData *data){
    BWProgram *program=get(raw,[BWProgram class]);if(count<1||count>2||!data)return report("Unsupported program stage count");
    NVNshaderData input[2];textureReadBytes(data,input,(size_t)count*sizeof *data,"shader data array is not fully readable");
    /* A preceding GPU store may have populated code before any program existed.
     * Bind only bytes whose actual producing submissions have completed. */
    poolDrainGPU();
    NSMutableDictionary *functions=[NSMutableDictionary new],*stageShaders=[NSMutableDictionary new];NSMutableArray *sources=[NSMutableArray new];
    for(int i=0;i<count;i++){
        BWShader *found=nil;BWPoolRange *mapping=nil;
        for(BWShader *shader in shaders){size_t offset=0;BWPool *pool=address(input[i].data,shader.codeSize,&offset);
            if(!pool||pool.device!=program.device||!input[i].control||(memoryValidator&&!memoryValidator(input[i].control,shader.controlSize)))continue;
            const char *failure=NULL;BWPoolRange *candidate=poolTryResolve(pool,offset,shader.codeSize,0,&failure);if(!candidate)continue;
            NSMutableData *control=[NSMutableData dataWithLength:shader.controlSize];
            textureReadBytes(input[i].control,control.mutableBytes,control.length,"shader control is not fully readable");
            if([digest(control.bytes,control.length) isEqual:shader.controlHash]){
                if([digest(programCodeBytes(candidate),candidate.length) isEqual:shader.codeHash]){found=shader;mapping=candidate;break;}
            }
        }
        if(!found)return report("No exact, validated local Metal translation for NVN shader");
        if(functions[@(found.stage)])return report("Program has duplicate shader stages");functions[@(found.stage)]=found.function;stageShaders[@(found.stage)]=found;
        [sources addObject:@{@"range":mapping,@"hash":found.codeHash}];
    }
    program.functions=functions;program.stageShaders=stageShaders;program.codeSources=sources;program.codeRetired=NO;return 1;}
static void validateProgramSources(BWProgram *program,BWDevice *device){
    if(![program isKindOfClass:[BWProgram class]]||program.device!=device||![objects.allValues containsObject:device]||program.codeRetired||!program.codeSources.count)fatal("program code device/lifetime is invalid");
    for(NSDictionary *source in program.codeSources){BWPoolRange *old=source[@"range"];
        BWPoolRange *now=poolResolve(old.pool,old.offset,old.length,0,"program code current mapping");
        if(now.physical!=old.physical||now.physicalID!=old.physicalID||now.physicalOffset!=old.physicalOffset)fatal("program code physical mapping changed");
        if(![digest(programCodeBytes(now),now.length) isEqual:source[@"hash"]])fatal("program code bytes changed after translation binding");
    }
}
#include "command_memory.inc"
#include "shader_scratch.inc"
#include "buffer_addresses.inc"
#include "counter.inc"
NVN(nvnCommandBufferCopyBufferToBuffer,void,NVNcommandBuffer *c,NVNbufferAddress src,NVNbufferAddress dst,size_t bytes,int flags){if(flags || !bytes)fatal("nvnCommandBufferCopyBufferToBuffer flags/size");BWAddressRecord *source=captureBufferAddress(c,src,bytes,"GPU copy bounds/overlap"),*destination=captureBufferAddress(c,dst,bytes,"GPU copy bounds/overlap");if(source.pool==destination.pool&&source.offset<destination.offset+bytes&&destination.offset<source.offset+bytes)fatal("GPU copy bounds/overlap");record(c,@{@"op":@"copy",@"src":@(src),@"dst":@(dst),@"size":@(bytes),@"source":source,@"destination":destination});}
NVN(nvnCommandBufferSetRenderTargets,void,NVNcommandBuffer *c,int n,const NVNtexture *const *colors,const NVNtextureView *const *views,const NVNtexture *depth,const NVNtextureView *dv){
    if(n!=1||!colors)fatal("nvnCommandBufferSetRenderTargets supports one color");textureMemory(colors,sizeof *colors,NO);if(views)textureMemory(views,sizeof *views,NO);
    BWTexture *color=textureObject(colors[0]),*z=depth?textureObject(depth):nil;
    BWTexture *selected=textureSelected(color,textureView(views?views[0]:NULL),YES),*depthSelected=z?textureSelected(z,textureView(dv),YES):nil;
    if(color.depth||(z&&!z.depth)|| (depthSelected&&(selected.width!=depthSelected.width||selected.height!=depthSelected.height)))fatal("incompatible render targets");
    NSMutableDictionary *op=[@{@"op":@"target",@"id":@(((const BWObject*)colors[0])->serial),@"depth":depth?@(((const BWObject*)depth)->serial):@0,@"selected":selected} mutableCopy];if(depthSelected)op[@"depthSelected"]=depthSelected;record(c,op);
}
NVN(nvnCommandBufferClearColor,void,NVNcommandBuffer *c,int index,const float *color,int mask){if(index||!color||mask!=15)fatal("nvnCommandBufferClearColor color index/mask");record(c,@{@"op":@"clear",@"color":[NSData dataWithBytes:color length:16]});}
NVN(nvnCommandBufferBindProgram,void,NVNcommandBuffer *c,const NVNprogram *p,int stages){if(stages!=NVN_SHADER_STAGE_ALL_GRAPHICS_BITS && stages!=(NVN_SHADER_STAGE_VERTEX_BIT|NVN_SHADER_STAGE_FRAGMENT_BIT))fatal("nvnCommandBufferBindProgram stages");get(p,[BWProgram class]);record(c,@{@"op":@"program",@"id":@(((const BWObject*)p)->serial)});}
NVN(nvnCommandBufferDrawArrays,void,NVNcommandBuffer *c,NVNdrawPrimitive mode,int first,int count){if((mode!=NVN_DRAW_PRIMITIVE_TRIANGLES&&mode!=NVN_DRAW_PRIMITIVE_TRIANGLE_STRIP)||first<0||count<0)fatal("nvnCommandBufferDrawArrays primitive/range");record(c,@{@"op":@"draw",@"primitive":@(mode==NVN_DRAW_PRIMITIVE_TRIANGLES?MTLPrimitiveTypeTriangle:MTLPrimitiveTypeTriangleStrip),@"first":@(first),@"count":@(count),@"baseInstance":@0,@"instances":@1});}
NVN(nvnCommandBufferDrawArraysInstanced,void,NVNcommandBuffer *c,NVNdrawPrimitive mode,int first,int count,int baseInstance,int instances){if((mode!=NVN_DRAW_PRIMITIVE_TRIANGLES&&mode!=NVN_DRAW_PRIMITIVE_TRIANGLE_STRIP)||first<0||count<0||baseInstance<0||instances<0)fatal("nvnCommandBufferDrawArraysInstanced primitive/instance range");record(c,@{@"op":@"draw",@"primitive":@(mode==NVN_DRAW_PRIMITIVE_TRIANGLES?MTLPrimitiveTypeTriangle:MTLPrimitiveTypeTriangleStrip),@"first":@(first),@"count":@(count),@"baseInstance":@(baseInstance),@"instances":@(instances)});}
NVN(nvnCommandBufferSetViewport,void,NVNcommandBuffer *c,int x,int y,int w,int h){if(x<0||y<0||w<0||h<0)fatal("nvnCommandBufferSetViewport bounds");record(c,@{@"op":@"viewport",@"x":@(x),@"y":@(y),@"w":@(w),@"h":@(h)});}
NVN(nvnCommandBufferSetScissor,void,NVNcommandBuffer *c,int x,int y,int w,int h){if(x<0||y<0||w<0||h<0)fatal("nvnCommandBufferSetScissor bounds");record(c,@{@"op":@"scissor",@"x":@(x),@"y":@(y),@"w":@(w),@"h":@(h)});}
typedef struct { NVNdevice *device; int min,mag,wrap[3],compareMode,compareFunc; float lodMin,lodMax,aniso; } SamplerBuilder;
typedef ds_st DepthState;
_Static_assert(sizeof(SamplerBuilder)<=sizeof(NVNsamplerBuilder),"sampler builder ABI");
_Static_assert(sizeof(VertexAttribute)==sizeof(NVNvertexAttribState),"attribute ABI");
_Static_assert(sizeof(VertexStream)==sizeof(NVNvertexStreamState),"stream ABI");
_Static_assert(sizeof(DepthState)==sizeof(NVNdepthStencilState),"depth ABI");
NVN(nvnDeviceGetTextureHandle,NVNtextureHandle,const NVNdevice *d,int texture,int sampler){
    get(d,[BWDevice class]);if(texture<0||texture>0xfffff||sampler<0||sampler>0xfff)fatal("texture/sampler descriptor index");
    uint32_t low=(uint32_t)texture|((uint32_t)sampler<<20);
    /* Original BOTW leaf: SXTW(low32) | (1ULL << 32). */
    uint64_t upper=(low&UINT32_C(0x80000000))?UINT64_C(0xffffffff00000000):UINT64_C(0x100000000);
    return upper|low;}
/* Original graphics BindTexture writes only the low word. Upper bits are not
 * an additional descriptor index or a canonical-form validation predicate. */
static unsigned textureDescriptorIndex(uint64_t handle){return (uint32_t)handle&0xfffff;}
static unsigned samplerDescriptorIndex(uint64_t handle){return ((uint32_t)handle>>20)&0xfff;}
static BWPoolRange *descriptorMapping(BWDescriptors *p,unsigned index){
    if(!p||!poolHostLive(p.pool)||index>=(unsigned)p.count)fatal("stale/out-of-range descriptor pool");
    return poolResolve(p.pool,p.offset+(size_t)index*BWDescriptorBytes,BWDescriptorBytes,0,"descriptor slot current mapping");
}
static NSArray *descriptorSlotKey(BWDescriptors *p,BWPoolRange *mapping){return @[@(mapping.physicalID),@(mapping.physicalOffset),@(p.sampler)];}
static id descriptorEntry(BWDescriptors *p,unsigned index){
    BWPoolRange *mapping=descriptorMapping(p,index);BWDescriptorSlot *slot=mapping.physical.descriptorSlots[descriptorSlotKey(p,mapping)];
    if(!slot)return nil;if(!slot.value)fatal("descriptor slot invalidated by GPU buffer write; register semantic descriptor again");
    if(p.sampler&&((BWSampler*)slot.value).device!=p.pool.device)fatal("sampler descriptor device mismatch");
    unsigned char bytes[BWDescriptorBytes];textureReadBytes(mapping.canonical,bytes,sizeof bytes,"descriptor slot bytes are not readable");
    if(![slot.witness isEqual:digest(bytes,sizeof bytes)])fatal("descriptor slot bytes changed; raw descriptor decoding is unsupported");return slot.value;
}
static void descriptorStore(BWDescriptors *p,unsigned index,id value){BWPoolRange *mapping=descriptorMapping(p,index);
    BWDevice *device=p.sampler?((BWSampler*)value).device:((BWTexture*)value).device;if(device!=p.pool.device)fatal("descriptor registration device mismatch");
    textureCheckBufferAuthority(mapping.canonical,mapping.length);poolDrainGPU();
    unsigned char bytes[BWDescriptorBytes];textureReadBytes(mapping.canonical,bytes,sizeof bytes,"descriptor registration bytes are not readable");
    BWDescriptorSlot *slot=[BWDescriptorSlot new];slot.value=value;slot.witness=digest(bytes,sizeof bytes);slot.physicalOffset=mapping.physicalOffset;
    if(!mapping.physical.descriptorSlots)mapping.physical.descriptorSlots=[NSMutableDictionary new];
    mapping.physical.descriptorSlots[descriptorSlotKey(p,mapping)]=slot;
}
static int descriptors(void *raw,const NVNmemoryPool *memory,ptrdiff_t offset,int count,BOOL sampler){
    BWPool *pool=get(memory,[BWPool class]);if(sampler&&count>BWMaxSamplerDescriptors)return report("sampler descriptor count exceeds 4096");
    if(offset<0||count<1||count>0x100000||!range(offset,(size_t)count*BWDescriptorBytes,pool.size)||pool.kind==BW_POOL_PHYSICAL)return report("descriptor pool bounds/kind");
    BWDescriptors *p=[BWDescriptors new];p.pool=pool;p.offset=offset;p.count=count;p.sampler=sampler;p.entries=[NSMutableDictionary new];put(raw,p);return 1;
}
static void poolPrepareGPUWrite(BWPoolRange *mapping){
    textureCheckBufferAuthority(mapping.canonical,mapping.length);
    if(memoryAccessValidator&&!memoryAccessValidator(mapping.canonical,mapping.length,1))fatal("GPU physical destination is not logically writable");
    texturePrepareWrite(mapping.canonical,mapping.length);textureInvalidateBufferCaches(mapping.canonical,mapping.length);
    if(poolBatchRanges&&!poolBatchSealed)return;
    for(BWDescriptorSlot *slot in mapping.physical.descriptorSlots.objectEnumerator)
        if(slot.physicalOffset<mapping.physicalOffset+mapping.length&&mapping.physicalOffset<slot.physicalOffset+BWDescriptorBytes){slot.value=nil;slot.witness=nil;}
}
static void poolGuardMappingChange(BWPool *v,const NVNmappingRequest *requests,int count){
    for(int i=0;i<count;i++){const NVNmappingRequest *request=requests+i;
        for(id object in objects.allValues)if([object isKindOfClass:[BWProgram class]])for(NSDictionary *source in ((BWProgram*)object).codeSources){BWPoolRange *code=source[@"range"];
            if(code.pool==v&&code.offset<request->virtualOffset+request->size&&request->virtualOffset<code.offset+code.length)fatal("MapVirtual overlaps active program code; rebind/retire program first");}
        for(uint64_t offset=request->virtualOffset;offset<request->virtualOffset+request->size;offset+=BW_POOL_MAPPING_PAGE_BYTES){
            const char *failure=NULL;BWPoolRange *old=poolTryResolve(v,(size_t)offset,BW_POOL_MAPPING_PAGE_BYTES,-1,&failure);
            if(old)textureCheckBufferAuthority(old.canonical,old.length);
        }
        if(request->physicalPool){BWPool *physical=get(request->physicalPool,[BWPool class]);textureCheckBufferAuthority((char*)physical.memory+request->physicalOffset,(size_t)request->size);}
    }
}
static void poolRetireReferences(BWPool *pool){
    textureRetirePoolReferences(pool);pool.descriptorSlots=nil;
    for(id object in objects.allValues){
        if([object isKindOfClass:[BWProgram class]]){BWProgram *program=object;BOOL affected=NO;
            for(NSDictionary *source in program.codeSources){BWPoolRange *code=source[@"range"];if(code.pool==pool||code.physical==pool){affected=YES;break;}}
            if(affected){program.codeRetired=YES;program.codeSources=nil;}
        }else if([object isKindOfClass:[BWQueue class]])((BWQueue*)object).latest=nil;
        else if([object isKindOfClass:[BWSync class]])((BWSync*)object).buffer=nil;
        else if([object isKindOfClass:[BWWindow class]])[((BWWindow*)object).busy removeAllObjects];
    }
}
NVN(nvnTexturePoolInitialize,NVNboolean,NVNtexturePool *p,const NVNmemoryPool *m,ptrdiff_t o,int n){return descriptors(p,m,o,n,NO);}
NVN(nvnSamplerPoolInitialize,NVNboolean,NVNsamplerPool *p,const NVNmemoryPool *m,ptrdiff_t o,int n){return descriptors(p,m,o,n,YES);}
NVN(nvnTexturePoolFinalize,void,NVNtexturePool *p){get(p,[BWDescriptors class]);removeObject(p);}
NVN(nvnSamplerPoolFinalize,void,NVNsamplerPool *p){get(p,[BWDescriptors class]);removeObject(p);}
NVN(nvnTexturePoolRegisterTexture,void,const NVNtexturePool *raw,int index,const NVNtexture *texture,const NVNtextureView *view){BWDescriptors *p=get(raw,[BWDescriptors class]);if(p.sampler||index<0||index>=p.count)fatal("texture descriptor index/view bounds/type");if(!p.pool.live||objects[@(p.pool.generation)]!=p.pool)fatal("stale texture descriptor pool");descriptorStore(p,(unsigned)index,textureSelected(textureObject(texture),textureView(view),NO));}
NVN(nvnSamplerPoolRegisterSampler,void,const NVNsamplerPool *raw,int index,const NVNsampler *sampler){BWDescriptors *p=get(raw,[BWDescriptors class]);if(!p.sampler||index<0||index>=p.count)fatal("sampler descriptor index/type");descriptorStore(p,(unsigned)index,get(sampler,[BWSampler class]));}
NVN(nvnSamplerBuilderSetDefaults,void,NVNsamplerBuilder *raw){memset(raw,0,sizeof *raw);SamplerBuilder *b=(SamplerBuilder*)raw;b->min=NVN_MIN_FILTER_NEAREST_MIPMAP_LINEAR;b->mag=NVN_MAG_FILTER_LINEAR;b->wrap[0]=b->wrap[1]=b->wrap[2]=NVN_WRAP_MODE_REPEAT;b->compareFunc=NVN_COMPARE_FUNC_LESS;b->lodMin=-1000;b->lodMax=1000;b->aniso=1;}
NVN(nvnSamplerBuilderSetDevice,void,NVNsamplerBuilder *b,NVNdevice *d){((SamplerBuilder*)b)->device=d;}
NVN(nvnSamplerBuilderSetMinMagFilter,void,NVNsamplerBuilder *raw,NVNminFilter min,NVNmagFilter mag){((SamplerBuilder*)raw)->min=min;((SamplerBuilder*)raw)->mag=mag;}
NVN(nvnSamplerBuilderSetWrapMode,void,NVNsamplerBuilder *raw,NVNwrapMode s,NVNwrapMode t,NVNwrapMode r){SamplerBuilder *b=(SamplerBuilder*)raw;b->wrap[0]=s;b->wrap[1]=t;b->wrap[2]=r;}
NVN(nvnSamplerBuilderSetLodClamp,void,NVNsamplerBuilder *raw,float min,float max){((SamplerBuilder*)raw)->lodMin=min;((SamplerBuilder*)raw)->lodMax=max;}
NVN(nvnSamplerBuilderSetMaxAnisotropy,void,NVNsamplerBuilder *raw,float a){((SamplerBuilder*)raw)->aniso=a;}
NVN(nvnSamplerBuilderSetCompare,void,NVNsamplerBuilder *raw,NVNcompareMode m,NVNcompareFunc f){((SamplerBuilder*)raw)->compareMode=m;((SamplerBuilder*)raw)->compareFunc=f;}
static MTLSamplerAddressMode wrapMode(int value){switch(value){case NVN_WRAP_MODE_REPEAT:return MTLSamplerAddressModeRepeat;case NVN_WRAP_MODE_MIRRORED_REPEAT:return MTLSamplerAddressModeMirrorRepeat;case NVN_WRAP_MODE_CLAMP_TO_EDGE:return MTLSamplerAddressModeClampToEdge;case NVN_WRAP_MODE_MIRROR_CLAMP_TO_EDGE:return MTLSamplerAddressModeMirrorClampToEdge;default:fatal("sampler wrap mode requires unimplemented border semantics");}}
NVN(nvnSamplerInitialize,NVNboolean,NVNsampler *raw,const NVNsamplerBuilder *builder){const SamplerBuilder *b=(const SamplerBuilder*)builder;get(b->device,[BWDevice class]);if(b->min<0||b->min>5||b->mag<0||b->mag>1||b->lodMax<b->lodMin||b->aniso<1||b->aniso>16||b->compareMode>1||b->compareFunc<1||b->compareFunc>8)return report("invalid sampler state");MTLSamplerDescriptor *d=[MTLSamplerDescriptor new];d.minFilter=(b->min&1)?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;d.magFilter=b->mag?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;d.mipFilter=b->min<2?MTLSamplerMipFilterNotMipmapped:b->min<4?MTLSamplerMipFilterNearest:MTLSamplerMipFilterLinear;d.sAddressMode=wrapMode(b->wrap[0]);d.tAddressMode=wrapMode(b->wrap[1]);d.rAddressMode=wrapMode(b->wrap[2]);d.lodMinClamp=MAX(0,b->lodMin);d.lodMaxClamp=MAX(0,b->lodMax);d.maxAnisotropy=b->aniso;d.compareFunction=b->compareMode?(MTLCompareFunction)(b->compareFunc-1):MTLCompareFunctionNever;BWSampler *sampler=[BWSampler new];sampler.device=get(b->device,[BWDevice class]);sampler.state=[gpu newSamplerStateWithDescriptor:d];if(!sampler.state)return report("Metal sampler creation failed");put(raw,sampler);return 1;}
NVN(nvnSamplerFinalize,void,NVNsampler *s){get(s,[BWSampler class]);removeObject(s);}
NVN(nvnVertexAttribStateSetDefaults,void,NVNvertexAttribState *s){memset(s,0,sizeof *s);}
NVN(nvnVertexAttribStateSetFormat,void,NVNvertexAttribState *s,NVNformat format,ptrdiff_t offset){if((unsigned)format>255||offset<0||offset>=1<<20)fatal("vertex attribute format/offset");((VertexAttribute*)s)->format=format;((VertexAttribute*)s)->offset=offset;}
NVN(nvnVertexAttribStateSetStreamIndex,void,NVNvertexAttribState *s,int stream){if(stream<0||stream>=15)fatal("vertex stream index");((VertexAttribute*)s)->stream=stream;}
NVN(nvnVertexStreamStateSetDefaults,void,NVNvertexStreamState *s){memset(s,0,sizeof *s);}
NVN(nvnVertexStreamStateSetStride,void,NVNvertexStreamState *s,ptrdiff_t stride){if(stride<0||stride>2048)fatal("vertex stride exceeds Metal limit");((VertexStream*)s)->stride=stride;}
NVN(nvnVertexStreamStateSetDivisor,void,NVNvertexStreamState *s,int divisor){if(divisor<0)fatal("negative vertex instance divisor");((VertexStream*)s)->divisor=divisor;}
#include "render_state.inc"
NVN(nvnCommandBufferBindVertexAttribState,void,NVNcommandBuffer *c,int n,const NVNvertexAttribState *s){if(n<0||n>16||(!s&&n))fatal("vertex attribute count");record(c,@{@"op":@"attributes",@"data":[NSData dataWithBytes:s length:n*sizeof *s]});}
NVN(nvnCommandBufferBindVertexStreamState,void,NVNcommandBuffer *c,int n,const NVNvertexStreamState *s){if(n<0||n>15||(!s&&n))fatal("vertex stream count");record(c,@{@"op":@"streams",@"data":[NSData dataWithBytes:s length:n*sizeof *s]});}
NVN(nvnCommandBufferBindDepthStencilState,void,NVNcommandBuffer *c,const NVNdepthStencilState *s){stateMemory(s,sizeof *s,NO);record(c,@{@"op":@"depthState",@"data":[NSData dataWithBytes:s length:sizeof *s]});}
NVN(nvnCommandBufferBindVertexBuffer,void,NVNcommandBuffer *c,int slot,NVNbufferAddress address,size_t size){if(slot<0||slot>=15)fatal("vertex buffer slot");BWAddressRecord *r=size?captureBufferAddress(c,address,size,"vertex buffer address bounds"):nil;NSMutableDictionary *op=[@{@"op":@"vertex",@"slot":@(slot),@"address":@(address),@"size":@(size)} mutableCopy];if(r)op[@"resource"]=r;record(c,op);}
NVN(nvnCommandBufferBindUniformBuffer,void,NVNcommandBuffer *c,NVNshaderStage stage,int slot,NVNbufferAddress address,size_t size){if(stage<0||stage>5||slot<0||slot>=128||size>65536)fatal("uniform buffer stage/slot/size");BWAddressRecord *r=size?captureBufferAddress(c,address,size,"unbound uniform buffer or address bounds"):nil;NSMutableDictionary *op=[@{@"op":@"uniform",@"stage":@(stage),@"slot":@(slot),@"address":@(address),@"size":@(size)} mutableCopy];if(r)op[@"resource"]=r;record(c,op);}
NVN(nvnCommandBufferBindStorageBuffer,void,NVNcommandBuffer *c,NVNshaderStage stage,int slot,NVNbufferAddress address,size_t size){if((stage!=NVN_SHADER_STAGE_VERTEX&&stage!=NVN_SHADER_STAGE_FRAGMENT)||slot<0||slot>=16)fatal("storage buffer stage/slot");BWAddressRecord *r=size?captureBufferAddress(c,address,size,"unbound storage buffer or address bounds"):nil;NSMutableDictionary *op=[@{@"op":@"storage",@"stage":@(stage),@"slot":@(slot),@"address":@(address),@"size":@(size)} mutableCopy];if(r)op[@"resource"]=r;record(c,op);}
NVN(nvnCommandBufferBindStorageBuffers,void,NVNcommandBuffer *c,NVNshaderStage stage,int first,int count,const NVNbufferRange *ranges){if(first<0||count<0||count>16||first>16-count||(!ranges&&count))fatal("storage buffer array range");for(int i=0;i<count;i++)h_nvnCommandBufferBindStorageBuffer(c,stage,first+i,ranges[i].address,ranges[i].size);}
NVN(nvnCommandBufferBindTexture,void,NVNcommandBuffer *c,NVNshaderStage stage,int slot,NVNtextureHandle handle){if(stage<0||stage>5||slot<0||slot>=128)fatal("texture stage/slot");record(c,@{@"op":@"texture",@"stage":@(stage),@"slot":@(slot),@"handle":@(handle)});}
NVN(nvnCommandBufferSetTexturePool,void,NVNcommandBuffer *c,const NVNtexturePool *p){BWDescriptors *pool=get(p,[BWDescriptors class]);if(pool.sampler)fatal("wrong texture descriptor pool type");record(c,@{@"op":@"texturePool",@"pool":pool});}
NVN(nvnCommandBufferSetSamplerPool,void,NVNcommandBuffer *c,const NVNsamplerPool *p){BWDescriptors *pool=get(p,[BWDescriptors class]);if(!pool.sampler)fatal("wrong sampler descriptor pool type");record(c,@{@"op":@"samplerPool",@"pool":pool});}
NVN(nvnCommandBufferClearDepthStencil,void,NVNcommandBuffer *c,float depth,uint32_t clearDepth,int stencil,int mask){(void)stencil;if(mask||(clearDepth&255)>1)fatal("stencil clear is not implemented");if(clearDepth&255)record(c,@{@"op":@"depthClear",@"depth":@(depth)});}
static NSString *resourceKey(NSNumber *stage,NSNumber *slot){return [NSString stringWithFormat:@"%@:%@",stage,slot];}
static MTLVertexFormat vertexFormat(unsigned f){switch(f){case NVN_FORMAT_R32F:return MTLVertexFormatFloat;case NVN_FORMAT_RG32F:return MTLVertexFormatFloat2;case NVN_FORMAT_RGB32F:return MTLVertexFormatFloat3;case NVN_FORMAT_RGBA32F:return MTLVertexFormatFloat4;case NVN_FORMAT_R16F:return MTLVertexFormatHalf;case NVN_FORMAT_RG16F:return MTLVertexFormatHalf2;case NVN_FORMAT_RGBA16F:return MTLVertexFormatHalf4;case NVN_FORMAT_RGBA8:return MTLVertexFormatUChar4Normalized;case NVN_FORMAT_RGBA8SN:return MTLVertexFormatChar4Normalized;default:fatal("vertex format requires additional conversion");}}
static BWPipeline *pipeline(BWReplay *state,BWRenderStates render){
    BWProgram *p=state.program;BWTexture *target=state.target;
    id<MTLFunction> vertex=p.functions[@(NVN_SHADER_STAGE_VERTEX)],fragment=p.functions[@(NVN_SHADER_STAGE_FRAGMENT)];
    if(!vertex||!fragment)fatal("draw without exact translated vertex/fragment shaders");
    NSArray *key=@[[NSValue valueWithPointer:(__bridge const void*)vertex],[NSValue valueWithPointer:(__bridge const void*)fragment],@(target.texture.pixelFormat),@(state.depth.texture.pixelFormat),state.attributes?:[NSData data],state.streams?:[NSData data],[NSData dataWithBytes:&render length:sizeof render]];
    BWPipeline *result=pipelines[key];if(result)return result;
    MTLRenderPipelineDescriptor *desc=[MTLRenderPipelineDescriptor new];desc.vertexFunction=vertex;desc.fragmentFunction=fragment;desc.colorAttachments[0].pixelFormat=target.texture.pixelFormat;
    if(state.depth)desc.depthAttachmentPixelFormat=state.depth.texture.pixelFormat;
    applyRenderPipeline(desc,render);
    if(state.attributes.length){MTLVertexDescriptor *vd=[MTLVertexDescriptor vertexDescriptor];const VertexAttribute *a=state.attributes.bytes;const VertexStream *v=state.streams.bytes;size_t n=state.attributes.length/sizeof *a,nv=state.streams.length/sizeof *v;
        for(size_t i=0;i<n;i++){if(!a[i].format)continue;if(a[i].stream>=nv)fatal("vertex stream layout is unbound");unsigned metal=16+a[i].stream;vd.attributes[i].format=vertexFormat(a[i].format);vd.attributes[i].offset=a[i].offset;vd.attributes[i].bufferIndex=metal;vd.layouts[metal].stride=v[a[i].stream].stride;vd.layouts[metal].stepFunction=v[a[i].stream].divisor?MTLVertexStepFunctionPerInstance:MTLVertexStepFunctionPerVertex;vd.layouts[metal].stepRate=v[a[i].stream].divisor?:1;}
        desc.vertexDescriptor=vd;
    }
    NSError *error=nil;MTLRenderPipelineReflection *reflection=nil;id<MTLRenderPipelineState> ps=[gpu newRenderPipelineStateWithDescriptor:desc options:MTLPipelineOptionBindingInfo reflection:&reflection error:&error];
    if(!ps)fatal(error.localizedDescription.UTF8String);
    for(NSNumber *stage in @[@(NVN_SHADER_STAGE_VERTEX),@(NVN_SHADER_STAGE_FRAGMENT)]){
        BWShader *shader=p.stageShaders[stage];const botw_graphics_shader_binding *b=shader.bindings.bytes;size_t n=shader.bindings.length/sizeof *b;
        NSArray *arguments=stage.intValue==NVN_SHADER_STAGE_VERTEX?reflection.vertexBindings:reflection.fragmentBindings;
        for(id<MTLBinding> argument in arguments){if(!argument.used)continue;BOOL matched=NO;
            if(stage.intValue==NVN_SHADER_STAGE_VERTEX && argument.type==MTLBindingTypeBuffer && argument.index>=16 && argument.index<31 && state.attributes.length)matched=YES;
            for(size_t i=0;i<n;i++){MTLBindingType type=(b[i].kind==BOTW_GRAPHICS_UNIFORM||b[i].kind>=BOTW_GRAPHICS_STORAGE)?MTLBindingTypeBuffer:b[i].kind==BOTW_GRAPHICS_TEXTURE?MTLBindingTypeTexture:MTLBindingTypeSampler;if(argument.type==type&&argument.index==b[i].metal_slot)matched=YES;}
            if(!matched)fatal("Metal resource has no explicitly validated NVN binding map");
        }
    }
    result=[BWPipeline new];result.state=ps;result.reflection=reflection;pipelines[key]=result;return result;
}
static void prepareResources(BWReplay *state,id<MTLCommandBuffer> cb){
    for(NSNumber *stage in state.program.stageShaders){BWShader *s=state.program.stageShaders[stage];const botw_graphics_shader_binding *b=s.bindings.bytes;size_t n=s.bindings.length/sizeof *b;
        for(size_t i=0;i<n;i++)if(b[i].kind==BOTW_GRAPHICS_TEXTURE){NSNumber *h=state.textures[resourceKey(stage,@(b[i].nvn_slot))];BWTexture *texture=descriptorEntry(state.texturePool,textureDescriptorIndex(h.unsignedLongLongValue));if(!h||!texture)fatal("unbound texture descriptor");uploadTexture(texture,cb);}}
}
static void validateDrawProfiles(BWReplay *state,NSDictionary *draw){
    for(BWShader *shader in state.program.stageShaders.allValues){
        if(!shader.maxVertices)continue;
        uint64_t vertices=[draw[@"count"] unsignedLongLongValue],instances=[draw[@"instances"] unsignedLongLongValue];
        if(vertices>shader.maxVertices)fatal("draw profile vertex count exceeded");
        if(instances>shader.maxInstances)fatal("draw profile instance count exceeded");
        const size_t *minimum=shader.storageMinimum.bytes;size_t need=minimum[instances];
        NSDictionary *bound=state.storage[resourceKey(@(shader.stage),@(shader.storageSlot))];size_t offset=0;
        if(!bound)fatal("draw profile has unbound storage");
        if([bound[@"size"] unsignedLongLongValue]<need||!address([bound[@"address"] unsignedLongLongValue],[bound[@"size"] unsignedLongLongValue],&offset))fatal("draw profile storage range is too short or outside memory pool");
    }
}
static BOOL textureScalarType(BWTexture *texture,MTLDataType scalar){
    switch(texture.format){
        case NVN_FORMAT_R8UI:case NVN_FORMAT_R16UI:case NVN_FORMAT_R32UI:case NVN_FORMAT_RG8UI:case NVN_FORMAT_RG16UI:case NVN_FORMAT_RG32UI:case NVN_FORMAT_RGBA8UI:case NVN_FORMAT_RGBA16UI:case NVN_FORMAT_RGBA32UI:return scalar==MTLDataTypeUInt;
        case NVN_FORMAT_R8I:case NVN_FORMAT_R16I:case NVN_FORMAT_R32I:case NVN_FORMAT_RG8I:case NVN_FORMAT_RG16I:case NVN_FORMAT_RG32I:case NVN_FORMAT_RGBA8I:case NVN_FORMAT_RGBA16I:case NVN_FORMAT_RGBA32I:return scalar==MTLDataTypeInt;
        default:return scalar==MTLDataTypeFloat||scalar==MTLDataTypeHalf;
    }
}
static void applyResources(BWReplay *state,BWPipeline *ps,id<MTLRenderCommandEncoder> encoder,id<MTLCommandBuffer> cb,NSDictionary *draw){
    const VertexAttribute *attributes=state.attributes.bytes;const VertexStream *streams=state.streams.bytes;
    for(size_t i=0;i<state.attributes.length/sizeof *attributes;i++)if(attributes[i].format){
        const VertexAttribute *a=attributes+i;if(a->stream>=state.streams.length/sizeof *streams)fatal("vertex range missing stream");
        NSDictionary *r=state.vertices[@(a->stream)];uint64_t n=[draw[@"count"] unsignedLongLongValue],first=[draw[@"first"] unsignedLongLongValue];
        uint64_t instances=[draw[@"instances"] unsignedLongLongValue],base=[draw[@"baseInstance"] unsignedLongLongValue];
        if(streams[a->stream].divisor>1&&(instances>1||base))fatal("instance divisor greater than one is not yet validated");
        uint64_t last=streams[a->stream].divisor?base+(instances?instances-1:0):first+(n?n-1:0),need=(uint64_t)a->offset+nvn_fmt_bpb(a->format,NULL,NULL);
        if(n&&streams[a->stream].stride && last>(UINT64_MAX-need)/streams[a->stream].stride)fatal("vertex range overflow");
        if(n)need+=last*streams[a->stream].stride;
        if(!r||[r[@"size"] unsignedLongLongValue]<need)fatal("vertex range is shorter than the draw requires");
    }
    for(NSNumber *slot in state.vertices){NSDictionary *r=state.vertices[slot];BWPoolRange *mapping=poolResolveAddress([r[@"address"] unsignedLongLongValue],[r[@"size"] unsignedLongLongValue],0,"vertex buffer address bounds");if(cb)poolRetainForGPU(cb,mapping,NO);[encoder setVertexBuffer:mapping.buffer offset:mapping.metalOffset atIndex:16+slot.unsignedIntegerValue];}
    for(NSNumber *stage in state.program.stageShaders){BOOL vertex=stage.intValue==NVN_SHADER_STAGE_VERTEX;BWShader *s=state.program.stageShaders[stage];const botw_graphics_shader_binding *b=s.bindings.bytes;size_t n=s.bindings.length/sizeof *b;
        for(size_t i=0;i<n;i++){NSString *key=resourceKey(stage,@(b[i].nvn_slot));
            if(b[i].kind==BOTW_GRAPHICS_STORAGE_ADDRESS_LOW){NSDictionary *r=state.storage[key];size_t offset=0;
                if(!r||![r[@"size"] unsignedLongLongValue]||!address([r[@"address"] unsignedLongLongValue],[r[@"size"] unsignedLongLongValue],&offset))fatal("unbound storage driver address");
                uint32_t low=(uint32_t)[r[@"address"] unsignedLongLongValue];NSArray *args=vertex?ps.reflection.vertexBindings:ps.reflection.fragmentBindings;
                for(id<MTLBinding> argument in args)if(argument.used&&argument.type==MTLBindingTypeBuffer&&argument.index==b[i].metal_slot&&((id<MTLBufferBinding>)argument).bufferDataSize!=sizeof low)fatal("storage driver address range must be exactly 32 bits");
                if(vertex)[encoder setVertexBytes:&low length:sizeof low atIndex:b[i].metal_slot];else[encoder setFragmentBytes:&low length:sizeof low atIndex:b[i].metal_slot];}
            else if(b[i].kind==BOTW_GRAPHICS_UNIFORM||b[i].kind==BOTW_GRAPHICS_STORAGE){BOOL storage=b[i].kind==BOTW_GRAPHICS_STORAGE;NSDictionary *r=(storage?state.storage:state.uniforms)[key];if(!r)fatal("unbound shader buffer");BWPoolRange *mapping=poolResolveAddress([r[@"address"] unsignedLongLongValue],[r[@"size"] unsignedLongLongValue],0,storage?"unbound storage buffer or address bounds":"unbound uniform buffer or address bounds");if(cb)poolRetainForGPU(cb,mapping,storage);
                NSArray *args=vertex?ps.reflection.vertexBindings:ps.reflection.fragmentBindings;
                for(id<MTLBinding> argument in args)if(argument.used&&argument.type==MTLBindingTypeBuffer&&argument.index==b[i].metal_slot){id<MTLBufferBinding> buffer=(id<MTLBufferBinding>)argument;if([r[@"size"] unsignedLongLongValue]<buffer.bufferDataSize || mapping.metalOffset%MAX(1,buffer.bufferAlignment))fatal(storage?"storage range size/alignment does not satisfy the shader":"uniform range size/alignment does not satisfy the shader");}
                if(vertex)[encoder setVertexBuffer:mapping.buffer offset:mapping.metalOffset atIndex:b[i].metal_slot];else[encoder setFragmentBuffer:mapping.buffer offset:mapping.metalOffset atIndex:b[i].metal_slot];}
            else{NSNumber *h=state.textures[key];if(!h)fatal("unbound combined texture handle");if(b[i].kind==BOTW_GRAPHICS_TEXTURE){BWTexture *t=descriptorEntry(state.texturePool,textureDescriptorIndex(h.unsignedLongLongValue));if(!t)fatal("texture descriptor missing");NSArray *args=vertex?ps.reflection.vertexBindings:ps.reflection.fragmentBindings;for(id<MTLBinding> a in args)if(a.used&&a.type==MTLBindingTypeTexture&&a.index==b[i].metal_slot){id<MTLTextureBinding> reflected=(id<MTLTextureBinding>)a;if(reflected.textureType!=t.texture.textureType)fatal("shader texture type differs from actual NVN descriptor");if(!textureScalarType(t,reflected.textureDataType))fatal("shader texture scalar type differs from actual NVN format");}if(vertex)[encoder setVertexTexture:t.texture atIndex:b[i].metal_slot];else[encoder setFragmentTexture:t.texture atIndex:b[i].metal_slot];}
                else{BWSampler *s=descriptorEntry(state.samplerPool,samplerDescriptorIndex(h.unsignedLongLongValue));if(!s)fatal("sampler descriptor missing");if(vertex)[encoder setVertexSamplerState:s.state atIndex:b[i].metal_slot];else[encoder setFragmentSamplerState:s.state atIndex:b[i].metal_slot];}}
        }
    }
}

static BOOL replayCommandState(BWReplay *state,NSDictionary *c){NSString *op=c[@"op"];
            if([op isEqual:@"target"]){if(![objects[c[@"id"]] isKindOfClass:[BWTexture class]]||([c[@"depth"] unsignedLongLongValue]&&![objects[c[@"depth"]] isKindOfClass:[BWTexture class]]))fatal("stale/incompatible render targets");state.target=c[@"selected"];state.depth=c[@"depthSelected"];textureLive(state.target);if(state.depth)textureLive(state.depth);return YES;}
            if([op isEqual:@"scratch"]){state.scratch=c[@"record"];return YES;}
            if([op isEqual:@"program"]){state.program=objects[c[@"id"]];if(![state.program isKindOfClass:[BWProgram class]])fatal("stale program");return YES;}
            if([op isEqual:@"renderState"]){replayRenderState(state,c);return YES;}
            if([op isEqual:@"depthRange"]){state.depthRange=c;return YES;}if([op isEqual:@"polygonOffset"]){state.polygonOffset=c;return YES;}
            if([op isEqual:@"viewport"]){state.viewport=c;return YES;}if([op isEqual:@"scissor"]){state.scissor=c;return YES;}
            if([op isEqual:@"attributes"]){state.attributes=c[@"data"];return YES;}if([op isEqual:@"streams"]){state.streams=c[@"data"];return YES;}if([op isEqual:@"depthState"]){state.depthState=c[@"data"];return YES;}
            if([op isEqual:@"texturePool"]){state.texturePool=c[@"pool"];return YES;}if([op isEqual:@"samplerPool"]){state.samplerPool=c[@"pool"];return YES;}
            if([op isEqual:@"uniform"]){state.uniforms[resourceKey(c[@"stage"],c[@"slot"])]=c;return YES;}
            if([op isEqual:@"storage"]){state.storage[resourceKey(c[@"stage"],c[@"slot"])]=c;return YES;}
            if([op isEqual:@"texture"]){state.textures[resourceKey(c[@"stage"],c[@"slot"])]=c[@"handle"];return YES;}
            if([op isEqual:@"vertex"]){state.vertices[c[@"slot"]]=c;return YES;}
    return NO;
}
static BWReplay *replaySnapshot(BWReplay *old){BWReplay *state=[BWReplay new];
    state.target=old.target;state.depth=old.depth;state.program=old.program;state.scratch=old.scratch;
    state.viewport=old.viewport;state.scissor=old.scissor;state.attributes=old.attributes;state.streams=old.streams;state.depthState=old.depthState;
    state.colorState=old.colorState;state.channelState=old.channelState;state.polygonState=old.polygonState;state.multisampleState=old.multisampleState;state.blendColor=old.blendColor;
    state.depthRange=old.depthRange;state.polygonOffset=old.polygonOffset;state.texturePool=old.texturePool;state.samplerPool=old.samplerPool;
    state.blendStates=[old.blendStates mutableCopy];state.uniforms=[old.uniforms mutableCopy];state.storage=[old.storage mutableCopy];state.textures=[old.textures mutableCopy];state.vertices=[old.vertices mutableCopy];return state;
}
static void validateRenderSubmission(BWQueue *queue,NSArray<NSArray *> *groups){BWReplay *state=replaySnapshot(queue.replay);
    for(NSArray *commands in groups)for(NSDictionary *c in commands){NSString *op=c[@"op"];
        if(replayCommandState(state,c)||[op isEqual:@"copy"]||[op isEqual:@"counter"])continue;
        if([op isEqual:@"draw"]&&(![c[@"count"] unsignedLongLongValue]||![c[@"instances"] unsignedLongLongValue]))continue;
        BOOL clear=[op isEqual:@"clear"],depthClear=[op isEqual:@"depthClear"];BWTexture *target=state.target;
        if(!target)fatal("render operation without target");if(depthClear&&!state.depth)fatal("depth clear without depth attachment");
        NSDictionary *scissor=state.scissor;if(scissor){NSUInteger x=[scissor[@"x"] unsignedIntegerValue],y=[scissor[@"y"] unsignedIntegerValue],w=[scissor[@"w"] unsignedIntegerValue],h=[scissor[@"h"] unsignedIntegerValue];
            if(x>(NSUInteger)target.width||y>(NSUInteger)target.height||w>(NSUInteger)target.width-x||h>(NSUInteger)target.height-y)fatal("scissor outside target");
            if((clear||depthClear)&&(x||y||w!=(NSUInteger)target.width||h!=(NSUInteger)target.height))fatal("scissored clear is not yet implemented");}
        if(!clear&&!depthClear){if(![op isEqual:@"draw"]||!state.program)fatal("unsupported replay operation / missing program");
            validateDrawProfiles(state,c);BWRenderStates render=renderStates(state);BWPipeline *ps=pipeline(state,render);
            /* The same reflected size/alignment/type path runs with no encoder
             * and no GPU captures. All static failures precede arena mutation. */
            applyResources(state,ps,nil,nil,c);applyRenderEncoder(state,nil,render);
        }
    }
}
NVN(nvnQueueSubmitCommands,void,NVNqueue *raw,int count,const NVNcommandHandle *input){
    BWQueue *queue=get(raw,[BWQueue class]);poolBeginBatch();@try{NSArray *commands=queueSubmissionCommands(queue,count,input);poolSealBatch();
    id<MTLCommandBuffer> cb=[queue.native commandBuffer];if(!cb)fatal("Metal command allocation");BWReplay *state=queue.replay;BWScratchRecord *initialScratch=state.scratch;NSArray *initialAddresses=addressReplayResources(state);
    for(NSDictionary *c in commands){NSString *op=c[@"op"];
            if([op isEqual:@"counter"]){cb=counterReplay(queue,cb,c,commands,initialScratch,initialAddresses,state);continue;}
            if(replayCommandState(state,c))continue;
            if([op isEqual:@"copy"]){size_t n=[c[@"size"] unsignedLongLongValue];BWPoolRange *src=poolResolveAddress([c[@"src"] unsignedLongLongValue],n,0,"GPU copy source"),*dst=poolResolveAddress([c[@"dst"] unsignedLongLongValue],n,0,"GPU copy destination");if(memoryOverlap(src.canonical,n,dst.canonical,n))fatal("GPU copy bounds/overlap");textureBufferCopy(cb,src.canonical,dst.canonical,n);poolRetainForGPU(cb,src,NO);poolRetainForGPU(cb,dst,YES);id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("GPU copy blit encoder allocation");@try{[blit copyFromBuffer:src.buffer sourceOffset:src.metalOffset toBuffer:dst.buffer destinationOffset:dst.metalOffset size:n];}@finally{[blit endEncoding];}continue;}
            if([op isEqual:@"draw"]&&(![c[@"count"] unsignedLongLongValue]||![c[@"instances"] unsignedLongLongValue]))continue;
            BWTexture *target=state.target;NSDictionary *viewport=state.viewport,*scissor=state.scissor;
            if(!target)fatal("render operation without target");BOOL clear=[op isEqual:@"clear"],depthClear=[op isEqual:@"depthClear"];
            if(!clear&&!depthClear){if(![op isEqual:@"draw"]||!state.program)fatal("unsupported replay operation / missing program");validateDrawProfiles(state,c);}
            uploadTexture(target,cb);if(state.depth)uploadTexture(state.depth,cb);
            textureGPUWrite(target,cb);if(state.depth)textureGPUWrite(state.depth,cb);
            if(!clear&&!depthClear){prepareResources(state,cb);for(NSDictionary *source in state.program.codeSources){BWPoolRange *old=source[@"range"];poolRetainForGPU(cb,poolResolve(old.pool,old.offset,old.length,0,"program code GPU retention"),NO);}}
            BWRenderStates render={0};BWPipeline *ps=nil;if(!clear&&!depthClear){render=renderStates(state);ps=pipeline(state,render);}
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target.texture;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].loadAction=clear?MTLLoadActionClear:MTLLoadActionLoad;
            if(state.depth){pass.depthAttachment.texture=state.depth.texture;pass.depthAttachment.loadAction=depthClear?MTLLoadActionClear:MTLLoadActionLoad;pass.depthAttachment.storeAction=MTLStoreActionStore;if(depthClear)pass.depthAttachment.clearDepth=[c[@"depth"] doubleValue];}
            else if(depthClear)fatal("depth clear without depth attachment");
            if((clear||depthClear)&&scissor&&([scissor[@"x"] intValue]!=0||[scissor[@"y"] intValue]!=0||[scissor[@"w"] intValue]!=target.width||[scissor[@"h"] intValue]!=target.height))fatal("scissored clear is not yet implemented");
            if(clear){float color[4];[c[@"color"] getBytes:color length:16];pass.colorAttachments[0].clearColor=MTLClearColorMake(color[0],color[1],color[2],color[3]);}
            id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];if(!encoder)fatal("Metal render pass creation");
            @try{if(!clear&&!depthClear){[encoder setRenderPipelineState:ps.state];applyResources(state,ps,encoder,cb,c);
                applyRenderEncoder(state,encoder,render);
                double near=state.depthRange?[state.depthRange[@"near"] doubleValue]:0,far=state.depthRange?[state.depthRange[@"far"] doubleValue]:1;
                [encoder setViewport:(MTLViewport){viewport?[viewport[@"x"] doubleValue]:0,viewport?[viewport[@"y"] doubleValue]:0,viewport?[viewport[@"w"] doubleValue]:target.width,viewport?[viewport[@"h"] doubleValue]:target.height,near,far}];
                if(scissor){NSUInteger x=[scissor[@"x"] unsignedIntegerValue],y=[scissor[@"y"] unsignedIntegerValue],w=[scissor[@"w"] unsignedIntegerValue],h=[scissor[@"h"] unsignedIntegerValue];if(x>(NSUInteger)target.width||y>(NSUInteger)target.height||w>(NSUInteger)target.width-x||h>(NSUInteger)target.height-y)fatal("scissor outside target");[encoder setScissorRect:(MTLScissorRect){x,y,w,h}];}
                if(render.polygon.cull==NVN_FACE_FRONT_AND_BACK)[encoder setScissorRect:(MTLScissorRect){0,0,0,0}];
                [encoder drawPrimitives:[c[@"primitive"] unsignedIntegerValue] vertexStart:[c[@"first"] unsignedIntegerValue] vertexCount:[c[@"count"] unsignedIntegerValue] instanceCount:[c[@"instances"] unsignedIntegerValue] baseInstance:[c[@"baseInstance"] unsignedIntegerValue]];texturePendingRecord(target.base?:target,cb,YES)[@"epoch"]=@(++serial);
            }else if(clear)texturePendingRecord(target.base?:target,cb,YES)[@"epoch"]=@0;
            }@finally{[encoder endEncoding];}
    }
    retainScratchUntilCompleted(cb,commands,initialScratch,state);retainAddressesUntilCompleted(cb,commands,initialAddresses,state);commitQueueBuffer(queue,cb);
    }@finally{poolEndBatch();}
}

NVN(nvnWindowBuilderSetDefaults,void,NVNwindowBuilder *raw){memset(raw,0,sizeof *raw);((WindowBuilder*)raw)->interval=1;}
NVN(nvnWindowBuilderSetDevice,void,NVNwindowBuilder *b,NVNdevice *d){((WindowBuilder*)b)->device=d;}
NVN(nvnWindowBuilderSetNativeWindow,void,NVNwindowBuilder *b,NVNnativeWindow w){((WindowBuilder*)b)->native=w;}
NVN(nvnWindowBuilderSetTextures,void,NVNwindowBuilder *raw,int n,NVNtexture *const *textures){WindowBuilder *b=(WindowBuilder*)raw;if(n<2||n>4||!textures)fatal("swapchain texture count");b->count=n;for(int i=0;i<n;i++){BWTexture *t=get(textures[i],[BWTexture class]);if(t.metadata.target!=NVN_TEXTURE_TARGET_2D)fatal("swapchain requires non-array 2D textures");b->textures[i]=textures[i];}}
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
    id<MTLCommandBuffer> cb=[queue.native commandBuffer];if(!cb)fatal("Metal presentation command allocation");uploadTexture(t,cb);MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];p.colorAttachments[0].texture=drawable.texture;p.colorAttachments[0].loadAction=MTLLoadActionDontCare;p.colorAttachments[0].storeAction=MTLStoreActionStore;id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:p];if(!e)fatal("presentation render encoder allocation");@try{[e setRenderPipelineState:presentPipeline];[e setFragmentTexture:t.texture atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];}@finally{[e endEncoding];}
    BWPresentation *state=presentationSnapshot();BWPresentedFrame *frame=[BWPresentedFrame new];
    frame.epoch=t.drawEpoch!=t.presentedEpoch?t.drawEpoch:0;t.presentedEpoch=t.drawEpoch;
    [state.lock lock];BOOL capture=state.capture;[state.lock unlock];
    if(capture&&frame.epoch){frame.width=(uint32_t)drawable.texture.width;frame.height=(uint32_t)drawable.texture.height;frame.pitch=((size_t)frame.width*4+255)&~255ull;
        frame.pixels=[gpu newBufferWithLength:frame.pitch*frame.height options:MTLResourceStorageModeShared];if(!frame.pixels)fatal("presentation capture allocation");
        id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];if(!blit)fatal("presentation capture blit encoder allocation");@try{[blit copyFromTexture:drawable.texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(frame.width,frame.height,1) toBuffer:frame.pixels destinationOffset:0 destinationBytesPerRow:frame.pitch destinationBytesPerImage:frame.pitch*frame.height];}@finally{[blit endEncoding];}}
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done){presented(state,frame,YES,NO,done.status!=MTLCommandBufferStatusCompleted);}];
    [drawable addPresentedHandler:^(id<MTLDrawable> shown){presented(state,frame,NO,shown.presentedTime>0,NO);}];
    [cb presentDrawable:drawable afterMinimumDuration:w.interval/60.0];w.busy[@(index)]=cb;commitQueueBuffer(queue,cb);}

/* A public NVN name can be queried speculatively. Unsupported entries remain
 * callable only to give a named fatal error when actually invoked. */
#define CATCH_GRAPHICS_FAILURE \
    @catch(BWGraphicsFailure *failure){(void)failure;} \
    @catch(NSException *exception){report("Native graphics exception: %s",exception.reason.UTF8String);pendingFatal=YES;}
#define WRAP_VALUE(scope,name,ret,parameters,operation) \
    scope ret name parameters {ret value={0};graphicsBegin();const char *previous=currentEntry;currentEntry=#name; \
        @autoreleasepool{@try{value=(operation);}CATCH_GRAPHICS_FAILURE}currentEntry=previous;graphicsEnd();return value;}
#define WRAP_VOID(scope,name,parameters,operation) \
    scope void name parameters {graphicsBegin();const char *previous=currentEntry;currentEntry=#name; \
        @autoreleasepool{@try{operation;}CATCH_GRAPHICS_FAILURE}currentEntry=previous;graphicsEnd();}
#define NVN_VALUE(name,ret,parameters,arguments) WRAP_VALUE(static,w_##name,ret,parameters,h_##name arguments) \
    _Static_assert(__builtin_types_compatible_p(__typeof__(&w_##name),NVNT_##name),"NVN wrapper signature: " #name);
#define NVN_VOID(name,parameters,arguments) WRAP_VOID(static,w_##name,parameters,h_##name arguments) \
    _Static_assert(__builtin_types_compatible_p(__typeof__(&w_##name),NVNT_##name),"NVN wrapper signature: " #name);
#include "entrypoints.inc"
#undef NVN_VALUE
#undef NVN_VOID
WRAP_VALUE(,botw_graphics_init,int,(void *layer,botw_graphics_error error,botw_graphics_memory_range validator),initializeGraphicsImpl(layer,error,validator))
/* Host cleanup must retain quarantined backing on failure and return to its
 * owner. It cannot reuse NVN's callback/abort error boundary during teardown. */
int botw_graphics_shutdown_checked(char *error,size_t capacity){
    if(graphicsDepth){if(error&&capacity)snprintf(error,capacity,"graphics shutdown requires an outermost host call");return 0;}
    graphicsBegin();const char *previous=currentEntry;currentEntry="botw_graphics_shutdown_checked";int success=0;
    @autoreleasepool{@try{shutdownGraphicsImpl();success=1;}
        @catch(BWGraphicsFailure *failure){(void)failure;}
        @catch(NSException *exception){report("Native graphics shutdown exception: %s",exception.reason.UTF8String);}}
    if(error&&capacity)snprintf(error,capacity,"%s",success?"":lastError);
    /* There was no outer transaction. Suppress only this cleanup call's flags;
     * graphicsEnd releases serialization without invoking the guest boundary. */
    pendingError=NO;pendingFatal=NO;currentEntry=previous;graphicsEnd();return success;
}
WRAP_VOID(,botw_graphics_shutdown,(void),shutdownGraphicsImpl())
static void textureTrackingRegistration(botw_graphics_memory_tracking_registration registration){if(pools.count)fatal("texture tracking must be configured before pools");if(!registration||nvn_dirty_configure_registration(registration))fatal("texture memory tracking registration failed");textureTrackingAvailable=YES;}
WRAP_VOID(,botw_graphics_set_memory_tracking_registration,(botw_graphics_memory_tracking_registration registration),textureTrackingRegistration(registration))
WRAP_VOID(,botw_graphics_set_memory_access_validator,(botw_graphics_memory_access validator),memoryAccessValidator=validator)
WRAP_VOID(,botw_graphics_set_command_memory_invoker,(botw_graphics_command_memory_invoker invoker),commandMemoryInvoker=invoker)
/* Platform calls may run under the runtime lifecycle lock. Never dispatch a
 * host error here, including Objective-C exceptions from the layer. */
#define PLATFORM_VALUE(name,parameters,operation) \
    int name parameters {int value=0;graphicsBegin(); \
        @autoreleasepool{@try{value=(operation);}@catch(NSException *exception){platformReject(exception.reason.UTF8String);}} \
        graphicsEnd();return value;}
PLATFORM_VALUE(botw_graphics_platform_initialize,(void),platformInitializeImpl())
PLATFORM_VALUE(botw_graphics_platform_open_default_display,(uintptr_t *out),platformOpenDisplayImpl(out))
PLATFORM_VALUE(botw_graphics_platform_create_layer,(uintptr_t display,uintptr_t *out),platformCreateLayerImpl(display,out))
PLATFORM_VALUE(botw_graphics_platform_set_scaling_mode,(uintptr_t layer,int mode),platformScalingImpl(layer,mode))
PLATFORM_VALUE(botw_graphics_platform_get_native_window,(uintptr_t layer,uintptr_t *out),platformNativeWindowImpl(layer,out))
PLATFORM_VALUE(botw_graphics_platform_display_size,(uintptr_t display,uint32_t *width,uint32_t *height),platformDisplaySizeImpl(display,width,height))
PLATFORM_VALUE(botw_graphics_platform_display_is_live,(uintptr_t display),platformDisplayLiveImpl(display))
#undef PLATFORM_VALUE
WRAP_VALUE(,botw_graphics_register_shader_with_bindings,int,(const void *control,size_t cs,const void *code,size_t ds,NVNshaderStage stage,const char *source,const char *entry,const botw_graphics_shader_binding *bindings,size_t count),registerShaderImpl(control,cs,code,ds,stage,source,entry,bindings,count,NULL))
WRAP_VALUE(,botw_graphics_register_shader_with_profile,int,(const void *control,size_t cs,const void *code,size_t ds,NVNshaderStage stage,const char *source,const char *entry,const botw_graphics_shader_binding *bindings,size_t count,const botw_graphics_shader_draw_profile *profile),registerShaderImpl(control,cs,code,ds,stage,source,entry,bindings,count,profile))
WRAP_VALUE(,botw_graphics_register_shader_scratch_proof,int,(const void *control,size_t cs,const void *code,size_t ds,NVNshaderStage stage,const void *spirv,size_t bytes),registerScratchProofImpl(control,cs,code,ds,stage,spirv,bytes))
WRAP_VALUE(,botw_graphics_read_texture,int,(const NVNtexture *texture,void *pixels,size_t bytes),readTextureImpl(texture,0,0,pixels,bytes))
WRAP_VALUE(,botw_graphics_read_texture_subresource,int,(const NVNtexture *texture,unsigned level,unsigned layer,void *pixels,size_t bytes),readTextureImpl(texture,level,layer,pixels,bytes))
#define NVN_UNSUPPORTED(name) __attribute__((noreturn)) static void unsupported_##name(void){graphicsBegin();const char *previous=currentEntry;currentEntry=#name;@autoreleasepool{@try{fatal(#name);}CATCH_GRAPHICS_FAILURE}currentEntry=previous;graphicsEnd();abort();}
#include "unsupported.inc"
#undef NVN_UNSUPPORTED
static void *lookupNVNImpl(const char *name){if(!name)return NULL;
#define NVN_BIND(name) if(!strcmp(#name,name_))return (void*)w_##name;
    const char *name_=name;
    if(!strcmp(name,"nvnBootstrapLoader"))return (void*)botw_nvn_bootstrap_loader;
#include "implemented.inc"
#include "counter_bindings.inc"
#undef NVN_BIND
#define NVN_UNSUPPORTED(name) if(!strcmp(#name,name_))return (void*)unsupported_##name;
#include "unsupported.inc"
#undef NVN_UNSUPPORTED
    /* Querying an extension is not execution. Preserve a diagnostic without
     * invoking the host's fatal callback for an ordinary NULL lookup result. */
    snprintf(lastError,sizeof lastError,"Unknown NVN entry point: %s",name);
    fprintf(stderr,"[botw graphics] %s\n",lastError);return NULL;}
WRAP_VALUE(,botw_nvn_bootstrap_loader,void *,(const char *name),lookupNVNImpl(name))
#undef WRAP_VALUE
#undef WRAP_VOID
#undef CATCH_GRAPHICS_FAILURE
