/* Synthetic CPU queue arena regression. No game bytes or derived shaders.
 * The callback mutation deterministically models a concurrent guest Builder
 * change between permission validation and storage consumption. */
#include "graphics.h"
#include "queue_arena.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK line %d: %s [%s]\n",__LINE__,#x,botw_graphics_last_error());return 1;}}while(0)
static sigjmp_buf recovery;static int escaping,writable=1;static const void *unreadable;
static void *allocation,*arena,*other,*poolMemory,*rawStorage;static size_t arenaBytes,poolBytes;static unsigned char *saved;
static int mutateBuilder;static NVNT_nvnQueueBuilderSetQueueMemory setMemory;
static NVNdevice device;static NVNqueue queue,second;static NVNqueueBuilder builder,temporary;static NVNcommandBuffer command;
static int inside(const void *p,size_t n,const void *a,size_t bytes){uintptr_t x=(uintptr_t)p,y=(uintptr_t)a;return x>=y&&x-y<=bytes&&n<=bytes-(x-y);}
static void *poolObject;
static const void *poolDefaultsWrite;
static int access_memory(const void *p,size_t n,int write){if(!write)return p!=unreadable;int ok=writable&&((poolDefaultsWrite&&p==poolDefaultsWrite&&n==20)||inside(p,n,poolObject,sizeof(NVNmemoryPool))||inside(p,n,arena,arenaBytes)||inside(p,n,other,arenaBytes)||inside(p,n,poolMemory,poolBytes)||inside(p,n,rawStorage,arenaBytes));if(mutateBuilder){mutateBuilder=0;setMemory(&builder,other,arenaBytes);}return ok;}
static void error(const char *s){fprintf(stderr,"ERROR:%s\n",s);if(escaping)siglongjmp(recovery,1);}
#define FAILS(operation,reason) do{escaping=1;if(sigsetjmp(recovery,1)==0){operation;CHECK(0);}escaping=0;CHECK(strstr(botw_graphics_last_error(),reason));}while(0)
#define PRESERVES(operation,reason) do{memcpy(saved,arena,arenaBytes);FAILS(operation,reason);CHECK(!memcmp(saved,arena,arenaBytes));}while(0)
int main(void){
 if(!botw_graphics_init(NULL,error,NULL))return 77;
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueBuilderSetFlags);
 API(nvnQueueBuilderSetCommandMemorySize);API(nvnQueueBuilderSetControlMemorySize);API(nvnQueueBuilderSetComputeMemorySize);API(nvnQueueBuilderGetQueueMemorySize);API(nvnQueueBuilderSetQueueMemory);
 API(nvnQueueInitialize);API(nvnQueueFinalize);API(nvnQueueFinish);API(nvnQueueSubmitCommands);
 API(nvnCommandBufferInitialize);API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);API(nvnCommandBufferSetViewport);
 API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);
 NVNdeviceBuilder db;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));
 nvnQueueBuilderSetDevice(&builder,&device);nvnQueueBuilderSetDefaults(&builder); /* preserves device */
 nvnQueueBuilderSetFlags(&builder,1);nvnQueueBuilderSetCommandMemorySize(&builder,4096);nvnQueueBuilderSetControlMemorySize(&builder,4096);nvnQueueBuilderSetComputeMemorySize(&builder,0);
 arenaBytes=nvnQueueBuilderGetQueueMemorySize(&builder);CHECK(arenaBytes==12288);
 CHECK(!posix_memalign(&allocation,4096,arenaBytes+8192));memset(allocation,0xa5,arenaBytes+8192);arena=(char*)allocation+4096;
 CHECK(!posix_memalign(&other,4096,arenaBytes));memset(other,0x6b,arenaBytes);saved=malloc(arenaBytes);CHECK(saved);
 nvnQueueBuilderSetQueueMemory(&builder,arena,arenaBytes);
 FAILS((void)nvnQueueInitialize(&queue,&builder),"read/write validation");CHECK(((unsigned char*)arena)[0]==0xa5);
 botw_graphics_set_memory_access_validator(access_memory);writable=0;
 FAILS((void)nvnQueueInitialize(&queue,&builder),"read/write validation");CHECK(((unsigned char*)arena)[0]==0xa5);writable=1;
 setMemory=nvnQueueBuilderSetQueueMemory;mutateBuilder=1;CHECK(nvnQueueInitialize(&queue,&builder));CHECK(((unsigned char*)other)[0]==0x6b);nvnQueueBuilderSetQueueMemory(&builder,arena,arenaBytes);BWQueueArenaHeader *header=arena;CHECK(header->magic==BW_QUEUE_ARENA_MAGIC&&header->commandBytes==4096&&header->controlBytes==4096);
 CHECK(nvnCommandBufferInitialize(&command,&device));nvnCommandBufferBeginRecording(&command);NVNcommandHandle empty=nvnCommandBufferEndRecording(&command);
 NVNcommandHandle inputs[513];for(unsigned i=0;i<513;i++)inputs[i]=empty;
 nvnQueueSubmitCommands(&queue,512,inputs);CHECK(header->commandUsed==4096&&header->controlUsed==0);
 memcpy((char*)arena+8192,inputs,4096);nvnQueueSubmitCommands(&queue,512,(NVNcommandHandle*)((char*)arena+8192)); /* source overlaps control */
 PRESERVES(nvnQueueSubmitCommands(&queue,513,inputs),"handle budget");
 NVNcommandHandle bad[]={empty,UINT64_MAX};PRESERVES(nvnQueueSubmitCommands(&queue,2,bad),"invalid/finalized command handle");
 unreadable=inputs;PRESERVES(nvnQueueSubmitCommands(&queue,1,inputs),"not fully readable");unreadable=NULL;
 writable=0;PRESERVES(nvnQueueSubmitCommands(&queue,1,inputs),"lost full read/write");writable=1;
 botw_graphics_set_memory_access_validator(NULL);PRESERVES(nvnQueueSubmitCommands(&queue,1,inputs),"lost full read/write");botw_graphics_set_memory_access_validator(access_memory);
 unsigned char magic=((unsigned char*)arena)[0];((unsigned char*)arena)[0]^=1;PRESERVES(nvnQueueSubmitCommands(&queue,1,inputs),"header was modified");((unsigned char*)arena)[0]=magic;
 nvnCommandBufferBeginRecording(&command);for(unsigned i=0;i<256;i++)nvnCommandBufferSetViewport(&command,0,0,1,1);NVNcommandHandle exact=nvnCommandBufferEndRecording(&command);
 nvnQueueSubmitCommands(&queue,1,&exact);CHECK(header->controlUsed==4096);
 BWQueueReplayIndex record;memcpy(&record,(char*)arena+8192+255*sizeof record,sizeof record);CHECK(record.handleIndex==0&&record.commandIndex==255&&!record.reserved);
 memcpy((char*)arena+4096,&exact,sizeof exact);nvnQueueSubmitCommands(&queue,1,(NVNcommandHandle*)((char*)arena+4096)); /* source overlaps command */
 nvnCommandBufferBeginRecording(&command);for(unsigned i=0;i<257;i++)nvnCommandBufferSetViewport(&command,0,0,1,1);NVNcommandHandle excess=nvnCommandBufferEndRecording(&command);
 PRESERVES(nvnQueueSubmitCommands(&queue,1,&excess),"control replay budget");
 PRESERVES((void)nvnQueueInitialize(&second,&builder),"overlaps live queue");
 PRESERVES((void)nvnQueueInitialize((NVNqueue*)arena,&builder),"object storage overlaps live CPU queue arena");
 memcpy(&temporary,&builder,sizeof temporary);nvnQueueBuilderSetQueueMemory(&temporary,other,arenaBytes);
 PRESERVES((void)nvnQueueInitialize((NVNqueue*)arena,&temporary),"object storage overlaps live CPU queue arena");CHECK(((unsigned char*)other)[0]==0x6b);
 NVNmemoryPoolBuilder mb;NVNmemoryPool pool;poolObject=&pool;poolDefaultsWrite=(char*)&mb+8;nvnMemoryPoolBuilderSetDefaults(&mb);poolDefaultsWrite=NULL;nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);nvnMemoryPoolBuilderSetStorage(&mb,arena,arenaBytes);
 PRESERVES((void)nvnMemoryPoolInitialize(&pool,&mb),"overlaps live CPU queue arena");
 memcpy(&temporary,&builder,sizeof temporary);nvnQueueBuilderSetQueueMemory(&temporary,other,arenaBytes-4096);
 FAILS((void)nvnQueueInitialize(&second,&temporary),"smaller than");CHECK(((unsigned char*)other)[0]==0x6b);
 unsigned char before[sizeof builder];memcpy(before,&builder,sizeof before);
 FAILS(nvnQueueBuilderSetCommandMemorySize(&builder,SIZE_MAX),"command budget");CHECK(!memcmp(before,&builder,sizeof before));
 FAILS(nvnQueueBuilderSetControlMemorySize(&builder,4095),"control budget");CHECK(!memcmp(before,&builder,sizeof before));
 FAILS(nvnQueueBuilderSetComputeMemorySize(&builder,16384),"compute budget must be zero");CHECK(!memcmp(before,&builder,sizeof before));
 FAILS(nvnQueueBuilderSetQueueMemory(&builder,(char*)arena+1,arenaBytes),"page-aligned");CHECK(!memcmp(before,&builder,sizeof before));
 poolBytes=(size_t)getpagesize()*2;CHECK(!posix_memalign(&poolMemory,(size_t)getpagesize(),poolBytes));memset(poolMemory,0x7c,poolBytes);
 nvnMemoryPoolBuilderSetStorage(&mb,poolMemory,poolBytes);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
 memcpy(&temporary,&builder,sizeof temporary);nvnQueueBuilderSetQueueMemory(&temporary,poolMemory,arenaBytes);
 FAILS((void)nvnQueueInitialize(&second,&temporary),"overlaps GPU memory pool");CHECK(((unsigned char*)poolMemory)[0]==0x7c);nvnMemoryPoolFinalize(&pool);
 CHECK(!posix_memalign(&rawStorage,4096,arenaBytes));memset(rawStorage,0x4d,arenaBytes);
 memcpy(&temporary,&builder,sizeof temporary);nvnQueueBuilderSetQueueMemory(&temporary,other,arenaBytes);CHECK(nvnQueueInitialize((NVNqueue*)rawStorage,&temporary));
 uint64_t rawSerial;memcpy(&rawSerial,rawStorage,sizeof rawSerial);nvnQueueBuilderSetQueueMemory(&temporary,rawStorage,arenaBytes);
 PRESERVES((void)nvnQueueInitialize(&second,&temporary),"overlaps live NVN object storage");CHECK(*(uint64_t*)rawStorage==rawSerial);nvnQueueFinalize((NVNqueue*)rawStorage);
 nvnMemoryPoolBuilderSetStorage(&mb,poolMemory,poolBytes);CHECK(nvnMemoryPoolInitialize((NVNmemoryPool*)rawStorage,&mb));memcpy(&rawSerial,rawStorage,sizeof rawSerial);
 PRESERVES((void)nvnQueueInitialize(&second,&temporary),"overlaps live NVN object storage");CHECK(*(uint64_t*)rawStorage==rawSerial);nvnMemoryPoolFinalize((NVNmemoryPool*)rawStorage);
 PRESERVES((void)nvnMemoryPoolInitialize((NVNmemoryPool*)arena,&mb),"object storage overlaps live CPU queue arena");
 nvnQueueFinish(&queue);nvnQueueFinalize(&queue); /* borrowed arena is not freed */
 CHECK(nvnQueueInitialize(&second,&builder));nvnQueueFinalize(&second);
 for(unsigned i=0;i<4096;i++){CHECK(((unsigned char*)allocation)[i]==0xa5);CHECK(((unsigned char*)allocation)[4096+arenaBytes+i]==0xa5);}
 botw_graphics_shutdown();free(rawStorage);free(poolMemory);free(saved);free(other);free(allocation);puts("CPU_QUEUE_ARENA_EXACT_CAPACITY_RW_OVERLAP_AND_PREFLIGHT_OK");return 0;
}
