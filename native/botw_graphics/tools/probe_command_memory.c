/* Synthetic storage and synchronous callback contract; no game bytes. */
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#define API(name) static NVNT_##name name
API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);API(nvnDeviceGetInteger);API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueBuilderSetCommandMemorySize);API(nvnQueueBuilderSetControlMemorySize);API(nvnQueueBuilderSetQueueMemory);API(nvnQueueInitialize);
API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);
API(nvnCommandBufferInitialize);API(nvnCommandBufferFinalize);API(nvnCommandBufferSetMemoryCallback);API(nvnCommandBufferSetMemoryCallbackData);
API(nvnCommandBufferGetMemoryCallback);API(nvnCommandBufferGetMemoryCallbackData);
API(nvnCommandBufferAddCommandMemory);API(nvnCommandBufferAddControlMemory);API(nvnCommandBufferGetCommandMemorySize);API(nvnCommandBufferGetCommandMemoryUsed);API(nvnCommandBufferGetCommandMemoryFree);
API(nvnCommandBufferGetControlMemorySize);API(nvnCommandBufferGetControlMemoryUsed);API(nvnCommandBufferGetControlMemoryFree);
API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);API(nvnCommandBufferCopyBufferToBuffer);
static sigjmp_buf jump;
static unsigned char *memory;static unsigned char controls[128][256] __attribute__((aligned(16384)));
static const void *extra;static size_t extraBytes;
static const void *poolDefaultsWrite;
static void *reviewArena;static size_t reviewArenaBytes=135168;
static NVNmemoryPool pool;static int commandGrants,controlGrants,events[2],mode,denied;
static void error(const char *s){fprintf(stderr,"ERROR:%s\n",s);siglongjmp(jump,1);}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s error=%s\n",#x,botw_graphics_last_error());exit(1);}}while(0)
#define FAILS(op,message) do{if(!sigsetjmp(jump,1)){op;CHECK(0);}CHECK(strstr(botw_graphics_last_error(),message));}while(0)
static int contains(const void *p,size_t n,const void *base,size_t bytes){uintptr_t a=(uintptr_t)p,b=(uintptr_t)base;return a>=b&&a-b<=bytes&&n<=bytes-(a-b);}
static int access_memory(const void *p,size_t n,int write){if(!write)return p!=NULL;return !denied&&((poolDefaultsWrite&&p==poolDefaultsWrite&&n==20)||contains(p,n,&pool,sizeof pool)||contains(p,n,memory,65536)||contains(p,n,controls,sizeof controls)||(reviewArena&&contains(p,n,reviewArena,reviewArenaBytes))||contains(p,n,extra,extraBytes));}
static void refill(NVNcommandBuffer *c,NVNcommandBufferMemoryEvent event,size_t minimum,void *data){
 CHECK(data==&events);CHECK(event==0||event==1);CHECK(minimum==(event?256u:512u));events[event]++;
 if(mode==1)return;
 if(event==0){CHECK(commandGrants<16);nvnCommandBufferAddCommandMemory(c,&pool,8192+commandGrants++*512,512);}
 else{CHECK(controlGrants<16);nvnCommandBufferAddControlMemory(c,controls[controlGrants++],256);}
}
static void *other_worker(void *unused){(void)unused;NVNdeviceBuilder b;NVNdevice d;nvnDeviceBuilderSetDefaults(&b);CHECK(nvnDeviceInitialize(&d,&b));nvnDeviceFinalize(&d);return NULL;}
static int invoke(PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC f,NVNcommandBuffer *c,NVNcommandBufferMemoryEvent event,size_t minimum,void *data,char *message,size_t cap){
 pthread_t worker;CHECK(!pthread_create(&worker,NULL,other_worker,NULL));CHECK(!pthread_join(worker,NULL));
 if(mode==2){snprintf(message,cap,"synthetic protected guest failure");return 0;}
 f(c,event,minimum,data);return 1;
}
int main(void){
 if(!botw_graphics_init(NULL,error,NULL))return 77;botw_graphics_set_memory_access_validator(access_memory);botw_graphics_set_command_memory_invoker(invoke);
#define LOAD(name) name=(NVNT_##name)botw_nvn_bootstrap_loader(#name);CHECK(name)
 LOAD(nvnDeviceBuilderSetDefaults);LOAD(nvnDeviceInitialize);LOAD(nvnDeviceFinalize);LOAD(nvnDeviceGetInteger);LOAD(nvnQueueBuilderSetDefaults);LOAD(nvnQueueBuilderSetDevice);LOAD(nvnQueueBuilderSetCommandMemorySize);LOAD(nvnQueueBuilderSetControlMemorySize);LOAD(nvnQueueBuilderSetQueueMemory);LOAD(nvnQueueInitialize);
 LOAD(nvnMemoryPoolBuilderSetDefaults);LOAD(nvnMemoryPoolBuilderSetDevice);LOAD(nvnMemoryPoolBuilderSetStorage);LOAD(nvnMemoryPoolBuilderSetFlags);LOAD(nvnMemoryPoolInitialize);
 LOAD(nvnCommandBufferInitialize);LOAD(nvnCommandBufferFinalize);LOAD(nvnCommandBufferSetMemoryCallback);LOAD(nvnCommandBufferSetMemoryCallbackData);LOAD(nvnCommandBufferGetMemoryCallback);LOAD(nvnCommandBufferGetMemoryCallbackData);
 LOAD(nvnCommandBufferAddCommandMemory);LOAD(nvnCommandBufferAddControlMemory);LOAD(nvnCommandBufferGetCommandMemorySize);LOAD(nvnCommandBufferGetCommandMemoryUsed);LOAD(nvnCommandBufferGetCommandMemoryFree);
 LOAD(nvnCommandBufferGetControlMemorySize);LOAD(nvnCommandBufferGetControlMemoryUsed);LOAD(nvnCommandBufferGetControlMemoryFree);LOAD(nvnCommandBufferBeginRecording);LOAD(nvnCommandBufferEndRecording);LOAD(nvnCommandBufferCopyBufferToBuffer);
 NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));
 int value=-1;nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_COMMAND_BUFFER_COMMAND_ALIGNMENT,&value);CHECK(value==4);nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_COMMAND_BUFFER_CONTROL_ALIGNMENT,&value);CHECK(value==8);nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_COMMAND_BUFFER_MIN_COMMAND_SIZE,&value);CHECK(value==512);nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_COMMAND_BUFFER_MIN_CONTROL_SIZE,&value);CHECK(value==256);
 CHECK(!posix_memalign((void**)&memory,(size_t)getpagesize(),65536));memset(memory,0,65536);memset(controls,0xa5,sizeof controls);
 NVNmemoryPoolBuilder mb;poolDefaultsWrite=(char*)&mb+8;nvnMemoryPoolBuilderSetDefaults(&mb);poolDefaultsWrite=NULL;nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);nvnMemoryPoolBuilderSetStorage(&mb,memory,65536);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
 NVNcommandBuffer cb;CHECK(nvnCommandBufferInitialize(&cb,&device));nvnCommandBufferSetMemoryCallback(&cb,refill);nvnCommandBufferSetMemoryCallbackData(&cb,&events);
 CHECK(nvnCommandBufferGetMemoryCallback(&cb)==refill);CHECK(nvnCommandBufferGetMemoryCallbackData(&cb)==&events);CHECK(!events[0]&&!events[1]);
 nvnCommandBufferAddCommandMemory(&cb,&pool,8192+commandGrants++*512,512);nvnCommandBufferAddControlMemory(&cb,controls[controlGrants++],256);
 CHECK(nvnCommandBufferGetCommandMemorySize(&cb)==512&&nvnCommandBufferGetCommandMemoryUsed(&cb)==0&&nvnCommandBufferGetCommandMemoryFree(&cb)==512);
 CHECK(nvnCommandBufferGetControlMemorySize(&cb)==256&&nvnCommandBufferGetControlMemoryUsed(&cb)==0&&nvnCommandBufferGetControlMemoryFree(&cb)==256);
 nvnCommandBufferBeginRecording(&cb);for(int i=0;i<40;i++)nvnCommandBufferCopyBufferToBuffer(&cb,(uintptr_t)memory,(uintptr_t)(memory+64),8,0);
 CHECK(events[0]==1&&events[1]==2);CHECK(nvnCommandBufferGetCommandMemoryUsed(&cb)==128);CHECK(nvnCommandBufferGetControlMemoryUsed(&cb)==160);
 CHECK(nvnCommandBufferEndRecording(&cb));CHECK(nvnCommandBufferGetControlMemoryUsed(&cb)==168);
 /* Current-span counters continue across Begin; memory is not silently reset. */
 nvnCommandBufferBeginRecording(&cb);CHECK(nvnCommandBufferGetCommandMemoryUsed(&cb)==128);CHECK(nvnCommandBufferEndRecording(&cb));CHECK(nvnCommandBufferGetControlMemoryUsed(&cb)==176);
 NVNcommandBuffer bad;CHECK(nvnCommandBufferInitialize(&bad,&device));nvnCommandBufferSetMemoryCallback(&bad,refill);nvnCommandBufferSetMemoryCallbackData(&bad,&events);nvnCommandBufferBeginRecording(&bad);
 mode=1;FAILS(nvnCommandBufferCopyBufferToBuffer(&bad,(uintptr_t)memory,(uintptr_t)(memory+64),8,0),"did not provide");
 mode=2;FAILS(nvnCommandBufferCopyBufferToBuffer(&bad,(uintptr_t)memory,(uintptr_t)(memory+64),8,0),"synthetic protected guest failure");
 mode=0;nvnCommandBufferCopyBufferToBuffer(&bad,(uintptr_t)memory,(uintptr_t)(memory+64),8,0);CHECK(nvnCommandBufferEndRecording(&bad));nvnCommandBufferFinalize(&bad);
 denied=1;FAILS(nvnCommandBufferAddControlMemory(&cb,controls[15],256),"read/write");denied=0;

 /* Real required callbacks never bypass the protected invoker. */
 NVNcommandBuffer protected;CHECK(nvnCommandBufferInitialize(&protected,&device));nvnCommandBufferSetMemoryCallback(&protected,refill);nvnCommandBufferSetMemoryCallbackData(&protected,&events);nvnCommandBufferBeginRecording(&protected);
 botw_graphics_set_command_memory_invoker(NULL);FAILS(nvnCommandBufferCopyBufferToBuffer(&protected,(uintptr_t)memory,(uintptr_t)(memory+64),8,0),"protected host invoker");botw_graphics_set_command_memory_invoker(invoke);
 nvnCommandBufferCopyBufferToBuffer(&protected,(uintptr_t)memory,(uintptr_t)(memory+64),8,0);CHECK(nvnCommandBufferEndRecording(&protected));nvnCommandBufferFinalize(&protected);
 /* Explicit storage does not silently fall back to host capacity. */
 NVNcommandBuffer explicit;CHECK(nvnCommandBufferInitialize(&explicit,&device));nvnCommandBufferAddCommandMemory(&explicit,&pool,8192+commandGrants++*512,512);nvnCommandBufferBeginRecording(&explicit);
 FAILS(nvnCommandBufferCopyBufferToBuffer(&explicit,(uintptr_t)memory,(uintptr_t)(memory+64),8,0),"without a memory callback");CHECK(nvnCommandBufferGetCommandMemoryUsed(&explicit)==0);
 nvnCommandBufferAddControlMemory(&explicit,controls[controlGrants++],256);nvnCommandBufferCopyBufferToBuffer(&explicit,(uintptr_t)memory,(uintptr_t)(memory+64),8,0);CHECK(nvnCommandBufferEndRecording(&explicit));nvnCommandBufferFinalize(&explicit);
 /* Bounds/alignment and full writable access reject before any storage write. */
 FAILS(nvnCommandBufferAddCommandMemory(&cb,&pool,-1,512),"pool bounds");FAILS(nvnCommandBufferAddCommandMemory(&cb,&pool,65536-256,512),"pool bounds");FAILS(nvnCommandBufferAddCommandMemory(&cb,&pool,8193,512),"size/alignment");
 FAILS(nvnCommandBufferAddCommandMemory(&cb,&pool,8192,508),"size/alignment");FAILS(nvnCommandBufferAddCommandMemory(&cb,&pool,0,SIZE_MAX),"pool bounds");
 FAILS(nvnCommandBufferAddControlMemory(&cb,controls[15],255),"size/alignment");FAILS(nvnCommandBufferAddControlMemory(&cb,controls[15]+1,256),"size/alignment");FAILS(nvnCommandBufferAddControlMemory(&cb,controls[15],SIZE_MAX),"size/alignment");
 botw_graphics_set_memory_access_validator(NULL);FAILS(nvnCommandBufferAddControlMemory(&cb,controls[15],256),"read/write");botw_graphics_set_memory_access_validator(access_memory);
 FAILS(nvnCommandBufferAddControlMemory(&cb,memory+32768,256),"GPU memory pool");
 unsigned char objectRoom[512] __attribute__((aligned(8)));NVNcommandBuffer *inside=(NVNcommandBuffer*)objectRoom;CHECK(nvnCommandBufferInitialize(inside,&device));extra=objectRoom;extraBytes=sizeof objectRoom;
 FAILS(nvnCommandBufferAddControlMemory(inside,objectRoom,sizeof objectRoom),"live NVN object");nvnCommandBufferFinalize(inside);extra=NULL;extraBytes=0;
 /* Repeated use keeps current-span accounting; corrupt tokens never write End. */
 NVNcommandBuffer tamper;CHECK(nvnCommandBufferInitialize(&tamper,&device));size_t commandStart=8192+commandGrants++*512;unsigned char *controlStart=controls[controlGrants++];
 nvnCommandBufferAddCommandMemory(&tamper,&pool,(ptrdiff_t)commandStart,512);nvnCommandBufferAddControlMemory(&tamper,controlStart,256);nvnCommandBufferBeginRecording(&tamper);
 nvnCommandBufferCopyBufferToBuffer(&tamper,(uintptr_t)memory,(uintptr_t)(memory+64),8,0);unsigned char before[256];memcpy(before,controlStart,256);memory[commandStart+12]^=1;
 FAILS((void)nvnCommandBufferEndRecording(&tamper),"token was modified");CHECK(nvnCommandBufferGetControlMemoryUsed(&tamper)==16&&!memcmp(before,controlStart,256));memory[commandStart+12]^=1;
 controlStart[12]^=1;FAILS((void)nvnCommandBufferEndRecording(&tamper),"token was modified");controlStart[12]^=1;
 denied=1;FAILS((void)nvnCommandBufferEndRecording(&tamper),"read/write");CHECK(nvnCommandBufferGetControlMemoryUsed(&tamper)==16);denied=0;
 unsigned char *nextControl=controls[controlGrants++];nvnCommandBufferAddControlMemory(&tamper,nextControl,256);controlStart[16]^=1;FAILS((void)nvnCommandBufferEndRecording(&tamper),"link was modified");controlStart[16]^=1;
 CHECK(nvnCommandBufferEndRecording(&tamper));
 /* The reverse object/queue/pool overlap checks protect borrowed control memory. */
 FAILS((void)nvnDeviceInitialize((NVNdevice*)(nextControl+32),&db),"recording memory");
 NVNcommandBuffer large;CHECK(nvnCommandBufferInitialize(&large,&device));nvnCommandBufferAddControlMemory(&large,controls[64],16384);
 NVNqueueBuilder qb;NVNqueue q;nvnQueueBuilderSetDefaults(&qb);nvnQueueBuilderSetDevice(&qb,&device);nvnQueueBuilderSetCommandMemorySize(&qb,4096);nvnQueueBuilderSetControlMemorySize(&qb,4096);nvnQueueBuilderSetQueueMemory(&qb,controls[64],12288);
 FAILS((void)nvnQueueInitialize(&q,&qb),"recording storage");
 NVNmemoryPoolBuilder alias=mb;NVNmemoryPool aliasPool;nvnMemoryPoolBuilderSetStorage(&alias,controls[64],16384);FAILS((void)nvnMemoryPoolInitialize(&aliasPool,&alias),"recording memory");
 nvnCommandBufferFinalize(&large);nvnCommandBufferFinalize(&tamper);
 /* Host-only callers use the same finite storage, including a real segment link. */
 NVNcommandBuffer host;CHECK(nvnCommandBufferInitialize(&host,&device));nvnCommandBufferBeginRecording(&host);for(int i=0;i<4096;i++)nvnCommandBufferCopyBufferToBuffer(&host,(uintptr_t)memory,(uintptr_t)(memory+64),8,0);
 CHECK(nvnCommandBufferEndRecording(&host));CHECK(nvnCommandBufferGetControlMemorySize(&host)==65536&&nvnCommandBufferGetControlMemoryUsed(&host)==24);nvnCommandBufferFinalize(&host);
 CHECK(!posix_memalign(&reviewArena,4096,reviewArenaBytes));memset(reviewArena,0x6b,reviewArenaBytes);
 NVNqueueBuilder reviewBuilder;nvnQueueBuilderSetDefaults(&reviewBuilder);nvnQueueBuilderSetDevice(&reviewBuilder,&device);nvnQueueBuilderSetQueueMemory(&reviewBuilder,reviewArena,reviewArenaBytes);
 FAILS((void)nvnQueueInitialize((NVNqueue*)controls[0],&reviewBuilder),"recording memory");
 for(size_t i=0;i<reviewArenaBytes;i++)CHECK(((unsigned char*)reviewArena)[i]==0x6b);
 nvnCommandBufferFinalize(&cb);botw_graphics_shutdown();free(reviewArena);reviewArena=NULL;free(memory);CHECK(botw_graphics_init(NULL,error,NULL));botw_graphics_shutdown();
 puts("COMMAND_MEMORY_REAL_STORAGE_CALLBACK_ACCOUNTING_AND_UNLOCK_OK");return 0;
}
