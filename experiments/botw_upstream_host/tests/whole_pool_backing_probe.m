/* Owned CPU contract fixture. No Metal device or GPU submission is created.
 * The generated includes are the actual staged pool registry and NVN entries. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <pthread.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stdarg.h>
#include <time.h>
#include <mach/mach.h>
#include "nvn_protos.h"
#include "nvn_obj.h"
#include "nvn_mtl.h"
#include "nvn_hmap.h"
#ifndef WHOLE_POOL_BASELINE
#include "whole_pool_backing_memory.h"
#include "whole_pool_backing_gate.h"
#endif
static char failure[256];
__attribute__((noreturn)) static void stop(const char *fmt,...) {
    va_list args;va_start(args,fmt);vsnprintf(failure,sizeof failure,fmt,args);va_end(args);
    @throw [NSException exceptionWithName:@"OwnedPoolStop" reason:@(failure) userInfo:nil];
}
#define MDIE(...) stop(__VA_ARGS__)
#define die(...) stop(__VA_ARGS__)
#define LOG(...) ((void)0)
#define MLOG(...) ((void)0)
#define OLOG(...) ((void)0)
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
static pthread_mutex_t g_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_cv=PTHREAD_COND_INITIALIZER;
static unsigned g_reg_gen;
static uint64_t g_fence,g_done;
static unsigned factories,acks,copies,shader_live,dirty_live,dirty_add_calls,drains;
static int fail_factory,fail_dirty,fail_publish,fail_catalog,throw_factory;
static void *publication;
static void *owned;static size_t owned_size;
static id held;
static _Atomic int retire_entered,writer_started,writer_done,reader_done,queue_pending,queue_done,drain_entered;
static pthread_mutex_t g_pq_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pq_cv=PTHREAD_COND_INITIALIZER;
static unsigned g_pool_present_busy;
static int g_pq_inflight;
static uint64_t g_pq_read,g_pq_in;
static _Atomic int g_up_pending,g_present_discard,g_pool_commit_error;
static pthread_mutex_t g_res_mu=PTHREAD_MUTEX_INITIALIZER;
static NSHashTable *g_res_objects,*g_res_ever;
static id<MTLResidencySet> g_res;
static int g_res_dirty,g_res_census=-1;
static void pause_cpu(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static int await_flag(_Atomic int *flag){for(int i=0;i<3000;i++){if(atomic_load(flag))return 1;pause_cpu();}return 0;}
static int logical_range(const void *memory,size_t size) {
    uintptr_t a=(uintptr_t)memory,b=(uintptr_t)owned;
    return a>=b&&a-b<=owned_size&&size<=owned_size-(a-b);
}
int dm_guest_heap_readable(uint64_t address,size_t size){return logical_range((void*)(uintptr_t)address,size);}
int dm_guest_write_prepare_checked(void *memory,size_t size){(void)memory;(void)size;return 1;}
uint64_t nvn_new_serial(void){static uint64_t serial;return ++serial;}
static int botw_shader_pool_add(const void *key,const void *memory,uint64_t addr,size_t size,char *error,size_t cap) {
    (void)key;(void)memory;(void)addr;(void)size;(void)error;(void)cap;shader_live++;return 1;
}
static void botw_shader_pool_remove(const void *key){(void)key;if(shader_live)shader_live--;}
static void botw_shader_unsupported(const char *message,const char *key){(void)message;(void)key;}
static int nvn_dirty_add_range_checked(const void *memory,size_t size){(void)memory;(void)size;dirty_add_calls++;if(fail_dirty)return 0;dirty_live++;return 1;}
static void nvn_dirty_remove_range(const void *memory){(void)memory;if(dirty_live)dirty_live--;}
static void res_add(id<MTLResource> resource);
static void res_remove(id<MTLResource> resource);
static void res_commit(void){}
#ifndef WHOLE_POOL_BASELINE
static void actual_pool_retire_references(const void *owner);
static void pool_retire_references(const void *owner){atomic_store(&retire_entered,1);actual_pool_retire_references(owner);}
#else
static void pool_retire_references(const void *owner){(void)owner;atomic_store(&retire_entered,1);}
void mtl_alias_drop_pool(const void *owner){(void)owner;atomic_store(&retire_entered,1);}
#endif
void nvn_texown_retire(uint64_t serial){(void)serial;}
void mtl_finish(void){drains++;}
void mtl_drain(void){drains++;}
static void x_drain(void){drains++;atomic_store(&drain_entered,1);while(atomic_load(&queue_pending)&&!atomic_load(&queue_done))pause_cpu();}
void mtl_present_flush(void){drains++;}
int mtl_wait(uint64_t fence,uint64_t timeout){(void)timeout;return fence<=g_done;}
int mtl_last_commit_error(void){return 0;}
static void present_drop_for_suspend(void){pthread_mutex_lock(&g_pq_mu);g_pq_read=g_pq_in;pthread_mutex_unlock(&g_pq_mu);}
static int g_nmod;
static struct {void *base;size_t size;} g_mod[1];
@interface OwnedBuffer:NSObject
@property void *contents,*allocation;@property NSUInteger length;@property uint64_t gpuAddress;
@property(copy) void (^released)(void*,NSUInteger);
@end
@implementation OwnedBuffer
- (BOOL)conformsToProtocol:(Protocol*)protocol{return protocol==@protocol(MTLResource)||protocol==@protocol(MTLBuffer)||[super conformsToProtocol:protocol];}
- (void)dealloc{if(_released)_released(_contents,_length);free(_allocation);}
@end
@interface OwnedTexture:NSObject
@property(strong) id<MTLBuffer> buffer;
@end
@implementation OwnedTexture
- (BOOL)conformsToProtocol:(Protocol*)protocol{return protocol==@protocol(MTLResource)||protocol==@protocol(MTLTexture)||[super conformsToProtocol:protocol];}
@end
@interface OwnedResidency:NSObject
@property(strong) NSMutableSet *allocations;
@end
@implementation OwnedResidency
- (instancetype)init{if((self=[super init]))_allocations=[NSMutableSet new];return self;}
- (void)addAllocation:(id)resource{[self.allocations addObject:resource];}
- (void)removeAllocation:(id)resource{if(![self.allocations containsObject:resource])stop("residency removal without an actual registration");[self.allocations removeObject:resource];}
@end
@interface OwnedDevice:NSObject
@property NSUInteger maxBufferLength;
@end
@implementation OwnedDevice
- (id<MTLBuffer>)newBufferWithBytesNoCopy:(void*)memory length:(NSUInteger)size options:(MTLResourceOptions)options deallocator:(void(^)(void*,NSUInteger))released {
    (void)options;factories++;if(fail_factory)return nil;
    OwnedBuffer *buffer=[OwnedBuffer new];buffer.contents=memory;buffer.length=size;buffer.gpuAddress=(uintptr_t)memory;
    if(released)buffer.released=^(void *p,NSUInteger n){acks++;released(p,n);};
    if(fail_publish)mprotect(publication,(size_t)getpagesize(),PROT_READ);
    if(throw_factory){held=buffer;@throw [NSException exceptionWithName:@"OwnedFactoryFailure" reason:@"actual borrowed object was retained before throwing" userInfo:nil];}
    return (id<MTLBuffer>)buffer;
}
- (id<MTLBuffer>)newBufferWithBytes:(const void*)memory length:(NSUInteger)size options:(MTLResourceOptions)options {
    (void)options;factories++;copies++;if(fail_factory)return nil;
    OwnedBuffer *buffer=[OwnedBuffer new];buffer.allocation=malloc(size);memcpy(buffer.allocation,memory,size);
    buffer.contents=buffer.allocation;buffer.length=size;buffer.gpuAddress=(uintptr_t)buffer.contents;return (id<MTLBuffer>)buffer;
}
@end
static id<MTLDevice> g_dev;
void mtl_init(void){}
#include "pool_residency.inc"
#include "pool_drains.inc"
#include "pool_references.inc"
static void *catalog_realloc(void *memory,size_t bytes){if(fail_catalog)return NULL;return realloc(memory,bytes);}
#define realloc catalog_realloc
#include "pool_registry.inc"
#undef realloc
#include "pool_gate.inc"
#include "pool_entries.inc"
static void *retire_worker(void *raw){@autoreleasepool{
    atomic_store(&writer_started,1);
    @try{h_nvnMemoryPoolFinalize(raw);atomic_store(&writer_done,1);}
    @catch(NSException *exception){(void)exception;atomic_store(&writer_done,2);}
    return NULL;
}}
static void *reader_worker(void *unused){(void)unused;gfx_enter();atomic_store(&reader_done,1);gfx_leave();return NULL;}
static void *queue_worker(void *unused){(void)unused;if(!await_flag(&drain_entered))return NULL;atomic_store(&queue_done,1);return NULL;}
static int check(int ok,const char *message){if(!ok){fprintf(stderr,"FAILED %s\n",message);return 1;}return 0;}
int main(int argc,char **argv){@autoreleasepool{
    if(argc!=2)return 2;
    size_t page=(size_t)getpagesize();owned_size=page*2;
    owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(owned==MAP_FAILED)return 3;memset(owned,0x39,owned_size);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];
    NVNmemoryPool pool;memset(&pool,0x5a,sizeof pool);NVNmemoryPool before=pool;
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    pool_builder_t builder={.dev=&raw_device,.mem=owned,.size=owned_size,.flags=0x21};
    int result=0;
    @try{
        if(!strcmp(argv[1],"readonly-deferred")){
            builder.flags=0x21;if(mprotect(owned,owned_size,PROT_READ))return 4;
            int initialized=h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder);
            result|=check(initialized==1,"exact readonly ordinary metadata remains legal");
            result|=check(factories==0,"readonly ordinary must not create illegal NoCopy resource");
            result|=check(h_nvnMemoryPoolMap(&pool)==NULL,"CPU_NO_ACCESS remains unmapped");
            h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"factory-rollback")){
            fail_factory=1;int initialized=0;
            @try{initialized=h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder);}
            @catch(NSException *exception){(void)exception;}
            result|=check(initialized==0,"failed backing creation returns false");
            result|=check(!memcmp(&pool,&before,sizeof pool),"factory failure never publishes object bytes");
            result|=check(!shader_live&&!dirty_live&&!g_npool,"factory failure rolls back all registrations");
        }else if(!strcmp(argv[1],"publish-rollback")){
            publication=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
            if(publication==MAP_FAILED)return 4;memcpy(publication,&before,sizeof before);fail_publish=1;
            int initialized=0;@try{initialized=h_nvnMemoryPoolInitialize(publication,(NVNmemoryPoolBuilder*)&builder);}@catch(NSException *exception){(void)exception;}
            result|=check(initialized==0,"failed checked publication returns false");
            result|=check(!memcmp(publication,&before,sizeof before),"failed protected publication keeps object bytes");
            result|=check(!g_npool&&!shader_live&&!dirty_live&&acks==1,"failed publication rolls back registrations after actual ACK");
            mprotect(publication,page,PROT_READ|PROT_WRITE);munmap(publication,page);
        }else if(!strcmp(argv[1],"mutation-barrier")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            gfx_enter();pthread_t writer,reader,queue;
            atomic_store(&drain_entered,0);atomic_store(&queue_pending,1);
            pthread_create(&writer,NULL,retire_worker,&pool);if(!await_flag(&writer_started))return 7;
#ifndef WHOLE_POOL_BASELINE
            for(int i=0;i<3000;i++){pthread_mutex_lock(&g_pool_gate.mutex);int waiting=g_pool_gate.waiting;pthread_mutex_unlock(&g_pool_gate.mutex);if(waiting)break;pause_cpu();}
#endif
            pthread_create(&reader,NULL,reader_worker,NULL);
            for(int i=0;i<15;i++)pause_cpu();
            result|=check(!atomic_load(&writer_done)&&!atomic_load(&reader_done),"mutation waits for existing capture and blocks new reader");
            pthread_create(&queue,NULL,queue_worker,NULL);gfx_leave();
            pthread_join(writer,NULL);pthread_join(reader,NULL);pthread_join(queue,NULL);
            result|=check(atomic_load(&writer_done)==1&&atomic_load(&queue_done)&&atomic_load(&reader_done),"queued CPU work drains without entering its writer gate");
        }else if(!strcmp(argv[1],"late-ack")||!strcmp(argv[1],"quarantine")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            void *buffer=NULL;uint64_t offset=0;
#ifdef WHOLE_POOL_BASELINE
            held=g_pools[0].buf;
#else
            if(!mtl_addr((uintptr_t)owned,1,&buffer,&offset))return 8;held=(__bridge id)buffer;
#endif
            (void)buffer;(void)offset;pthread_t writer;pthread_create(&writer,NULL,retire_worker,&pool);
            if(!strcmp(argv[1],"quarantine")){
                if(!await_flag(&writer_done))return 9;
                result|=check(atomic_load(&writer_done)==2&&((pool_t*)&pool)->magic==MAGIC('P','O','O','L')&&g_npool==1,"missing ACK cannot clear published object or discard backing entry");
                held=nil;pthread_join(writer,NULL);
#ifndef WHOLE_POOL_BASELINE
                char error[192];result|=check(mtl_pool_retire(&pool,error,sizeof error),"actual late ACK allows owned quarantined cleanup");
#endif
            }else{
                if(!await_flag(&retire_entered))return 9;
                for(int i=0;i<10;i++)pause_cpu();
                result|=check(!atomic_load(&writer_done)&&((pool_t*)&pool)->magic==MAGIC('P','O','O','L')&&g_npool==1,"last frame remains published and pinned while real ACK is late");
                held=nil;pthread_join(writer,NULL);
                result|=check(atomic_load(&writer_done)==1&&acks==1&&!g_npool,"last frame retires after its real deallocator without a future fence");
            }
        }else if(!strcmp(argv[1],"suspend")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            dm_gfx_suspend(1);pthread_t writer;pthread_create(&writer,NULL,retire_worker,&pool);
            if(!await_flag(&writer_started))return 9;for(int i=0;i<15;i++)pause_cpu();
            result|=check(!atomic_load(&writer_done),"pool mutation cannot clear app suspension reason");
            dm_gfx_suspend(0);pthread_join(writer,NULL);result|=check(atomic_load(&writer_done)==1,"mutation continues after actual app resume");
        }else if(!strcmp(argv[1],"upgrade")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            int stopped=0;gfx_enter();@try{h_nvnMemoryPoolFinalize(&pool);}@catch(NSException *exception){(void)exception;stopped=1;}gfx_leave();
            result|=check(stopped&&g_npool==1&&((pool_t*)&pool)->magic==MAGIC('P','O','O','L'),"reader upgrade rejects without waiting for itself or mutating");
            if(g_npool)h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"copied-cache")){
            builder.flags=0x24;if(mprotect(owned,owned_size,PROT_READ))return 4;
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            result|=check(copies==1,"readable cached RO uses conditional copied fallback");
#ifndef WHOLE_POOL_BASELINE
            void *buffer;uint64_t offset;if(!mtl_addr((uintptr_t)owned,owned_size,&buffer,&offset))return 8;
            unsigned char *bytes=(unsigned char*)((__bridge id<MTLBuffer>)buffer).contents+offset;
            mprotect(owned,owned_size,PROT_READ|PROT_WRITE);((unsigned char*)owned)[5]=0x91;
            h_nvnMemoryPoolFlushMappedRange(&pool,5,1);result|=check(bytes[5]==0x91,"flush copies actual canonical byte");
            bytes[7]=0x42;h_nvnMemoryPoolInvalidateMappedRange(&pool,7,1);result|=check(((unsigned char*)owned)[7]==0x42,"invalidate copies actual completed buffer byte");
#endif
            h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"overlap")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            NVNmemoryPool first=pool;
            result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!memcmp(&first,&pool,sizeof pool),"duplicate owner rejection preserves actual original object");
            NVNmemoryPool second;memset(&second,0x6a,sizeof second);NVNmemoryPool saved=second;
            result|=check(!h_nvnMemoryPoolInitialize(&second,(NVNmemoryPoolBuilder*)&builder),"overlapping backing is refused");
            result|=check(dirty_add_calls==1&&dirty_live==1&&shader_live==1&&factories==1&&g_npool==1&&!memcmp(&second,&saved,sizeof second),"overlap refusal cannot add/remove another pool's mem-keyed dirty owner");
            h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"invalid-input")){
            unsigned flags[]={0x121,0x221,0x25,0x20,0x801};
            for(unsigned i=0;i<sizeof flags/sizeof flags[0];i++){builder.flags=flags[i];result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!memcmp(&before,&pool,sizeof pool),"unsupported/conflicting flags remain unpublished");}
            builder.flags=0x21;builder.mem=(void*)(UINTPTR_MAX-2);builder.size=16;
            result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!factories&&!g_npool&&!dirty_add_calls&&!shader_live,"overflow rejects before registry/preparation/factory");
        }else if(!strcmp(argv[1],"max-cap")){
            device.maxBufferLength=owned_size-1;
            result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!factories&&!g_npool&&!dirty_live&&!shader_live&&!memcmp(&before,&pool,sizeof pool),"actual maxBufferLength refusal rolls back before factory");
        }else if(!strcmp(argv[1],"dirty-failure")){
            fail_dirty=1;
            result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!factories&&!g_npool&&!dirty_live&&!shader_live&&!memcmp(&before,&pool,sizeof pool),"checked dirty failure precedes Metal and object publication");
        }else if(!strcmp(argv[1],"catalog-failure")){
            fail_catalog=1;
            result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!factories&&!g_npool&&!dirty_live&&!shader_live&&!memcmp(&before,&pool,sizeof pool),"actual catalog realloc failure does not replace catalog or publish object");
        }else if(!strcmp(argv[1],"factory-throws")){
            throw_factory=1;int stopped=0;
            @try{(void)h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder);}@catch(NSException *exception){(void)exception;stopped=1;}
            result|=check(stopped&&g_npool==1&&held&&acks==0&&!memcmp(&before,&pool,sizeof pool),"thrown factory cannot synthesize absent-resource ACK or discard anchor");
            held=nil;
#ifndef WHOLE_POOL_BASELINE
            char error[192];result|=check(mtl_pool_abort(&pool,error,sizeof error)&&acks==1&&!g_npool,"actual late callback permits owned unpublished cleanup");
#endif
        }else if(!strcmp(argv[1],"split-owned")){
            if(vm_inherit(mach_task_self(),(vm_address_t)((char*)owned+page),page,VM_INHERIT_NONE)!=KERN_SUCCESS)return 4;
            builder.flags=0x22;
            result|=check(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&!factories&&!g_npool,"uncached ordinary cannot bridge two actual VM leaves");
            builder.flags=0x24;
            result|=check(h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)&&factories==1&&copies==1,"cached ordinary crosses actual leaves through its conditional owned copy");
            if(g_npool)h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"texture-birth")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            uint64_t old_texture=nvn_new_serial();h_nvnMemoryPoolFinalize(&pool);
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
#ifndef WHOLE_POOL_BASELINE
            result|=check(!mtl_pool_texture_valid(&pool,old_texture,owned,1)&&mtl_pool_texture_valid(&pool,nvn_new_serial(),owned,1),"same-address pool reuse rejects the older texture initialization serial");
            void *buffer=(void*)0x55;uint64_t offset=99;
            result|=check(!mtl_addr(UINT64_MAX-2,8,&buffer,&offset)&&buffer==(void*)0x55&&offset==99,"address lookup rejects wrap without changing outputs");
#else
            (void)old_texture;result|=check(0,"baseline lacks pool birth validation");
#endif
            h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"readonly-invalidate")){
            builder.flags=0x24;if(mprotect(owned,owned_size,PROT_READ))return 4;
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            int stopped=0;@try{h_nvnMemoryPoolInvalidateMappedRange(&pool,0,1);}@catch(NSException *exception){(void)exception;stopped=1;}
            result|=check(stopped&&((unsigned char*)owned)[0]==0x39&&g_npool==1,"actual readonly canonical destination refuses checked copied invalidation");
            h_nvnMemoryPoolFinalize(&pool);
        }else if(!strcmp(argv[1],"wrapper-references")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
#ifndef WHOLE_POOL_BASELINE
            @autoreleasepool{
                void *buffer;uint64_t offset;if(!mtl_addr((uintptr_t)owned,1,&buffer,&offset))return 8;
                OwnedTexture *texture=[OwnedTexture new];texture.buffer=(__bridge id<MTLBuffer>)buffer;
                NvPkg *package=[NvPkg new];package->tex=(id<MTLTexture>)texture;res_add((id<MTLResource>)texture);
                serial_put_pool(71,package,&pool);
                g_bloomq_base=(id<MTLTexture>)texture;g_bloomq_view=(id<MTLTexture>)texture;
                g_pool_present_sources[0]=(id<MTLTexture>)texture;g_pool_present_views[0]=(id<MTLTexture>)texture;
            }
            h_nvnMemoryPoolFinalize(&pool);
            result|=check(acks==1&&!g_npool&&!g_by_serial.n&&!g_bloomq_base&&!g_bloomq_view&&!g_pool_present_sources[0]&&!g_pool_present_views[0],"NvPkg wrapper retirement releases actual texture caches and borrowed backing");
#else
            result|=check(0,"baseline has no complete packaged wrapper retirement");
#endif
        }else if(!strcmp(argv[1],"all-references")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
#ifndef WHOLE_POOL_BASELINE
            @autoreleasepool{
                void *buffer;uint64_t offset;if(!mtl_addr((uintptr_t)owned,1,&buffer,&offset))return 8;
                OwnedTexture *texture=[OwnedTexture new];texture.buffer=(__bridge id<MTLBuffer>)buffer;
                objc_setAssociatedObject(texture,&pool_owner_key,[NSValue valueWithPointer:&pool],OBJC_ASSOCIATION_RETAIN_NONATOMIC);
                uint64_t key=41;hm_put_id(&g_by_serial,&key,texture);res_add((id<MTLResource>)texture);
                alias_key alias={.pool=(uintptr_t)&pool};alias_insert(&alias,(id<MTLTexture>)texture,key);
                g_treg=calloc(1,sizeof *g_treg);g_ntreg=1;g_treg[0].ok=1;g_treg[0].t.pool=(pool_t*)&pool;
                g_treg[0].view=(void*)CFBridgingRetain(texture);g_treg[0].storage_view=(void*)CFBridgingRetain(texture);
                g_treg[0].alt=(void*)CFBridgingRetain(texture);
                g_bloomq_base=(id<MTLTexture>)texture;g_bloomq_view=(id<MTLTexture>)texture;
                g_pool_present_sources[0]=(id<MTLTexture>)texture;g_pool_present_views[0]=(id<MTLTexture>)texture;
                defer_obj_unref(texture);
                OwnedTexture *other=[OwnedTexture new];objc_setAssociatedObject(other,&pool_owner_key,[NSValue valueWithPointer:&before],OBJC_ASSOCIATION_RETAIN_NONATOMIC);
                key=42;hm_put_id(&g_by_serial,&key,other);res_add((id<MTLResource>)other);
            }
            h_nvnMemoryPoolFinalize(&pool);
            uint64_t other_key=42;
            result|=check(acks==1&&!g_npool&&!g_alias.n&&g_by_serial.n==1&&nvn_hm_get(&g_by_serial,&other_key),"original caches release borrowed owner while unrelated live serial survives");
            result|=check(!g_treg[0].view&&!g_treg[0].storage_view&&!g_treg[0].alt&&!g_bloomq_view&&!g_bloomq_base&&!g_pool_present_sources[0]&&!g_deferred.count,"all existing descriptor/present/bloom/deferred references retire");
#else
            result|=check(0,"baseline has no complete pool reference retirement");
#endif
        }else if(!strcmp(argv[1],"present-cpu-borrow")){
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            pthread_mutex_lock(&g_pq_mu);g_pool_present_busy=1;pthread_mutex_unlock(&g_pq_mu);
            atomic_store(&drain_entered,0);pthread_t writer;pthread_create(&writer,NULL,retire_worker,&pool);
            if(!await_flag(&writer_started))return 9;for(int i=0;i<15;i++)pause_cpu();
            result|=check(!atomic_load(&writer_done)&&((pool_t*)&pool)->magic==MAGIC('P','O','O','L'),"CPU-dequeued present retains pool before GPU inflight exists");
            pthread_mutex_lock(&g_pq_mu);g_pool_present_busy=0;pthread_cond_broadcast(&g_pq_cv);pthread_mutex_unlock(&g_pq_mu);
            pthread_join(writer,NULL);result|=check(atomic_load(&writer_done)==1,"present CPU borrow completion releases actual drain");
        }else if(!strcmp(argv[1],"deferred-consumer")){
            if(mprotect(owned,owned_size,PROT_NONE))return 4;
            if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder))return 6;
            result|=check(!factories,"noaccess metadata allocates no resource");
#ifndef WHOLE_POOL_BASELINE
            void *buffer=NULL;uint64_t offset=0;result|=check(!mtl_addr((uintptr_t)owned,1,&buffer,&offset)&&!factories,"consumer refuses actual NONE leaf");
            mprotect(owned,owned_size,PROT_READ|PROT_WRITE);
            result|=check(mtl_addr((uintptr_t)owned,1,&buffer,&offset)&&factories==1,"consumer materializes only after real RW leaf transition");
#endif
            h_nvnMemoryPoolFinalize(&pool);
        }else return 5;
    }@catch(NSException *exception){fprintf(stderr,"unexpected stop: %s\n",exception.reason.UTF8String);result=1;}
    (void)held;(void)fail_publish;(void)g_cv;(void)g_fence;(void)g_done;
    mprotect(owned,owned_size,PROT_READ|PROT_WRITE);munmap(owned,owned_size);
    if(!result)puts("WHOLE_POOL_BACKING_CPU_OK");return result;
}}
