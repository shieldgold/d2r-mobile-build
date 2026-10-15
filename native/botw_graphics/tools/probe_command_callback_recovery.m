/* Independent synthetic callback recovery review; no game content. */
#import <Foundation/Foundation.h>
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(n) static NVNT_##n n
API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueBuilderSetQueueMemory);API(nvnQueueInitialize);
API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolInitialize);
API(nvnCommandBufferInitialize);API(nvnCommandBufferFinalize);API(nvnCommandBufferSetMemoryCallback);API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);API(nvnCommandBufferCopyBufferToBuffer);API(nvnCommandBufferAddCommandMemory);API(nvnCommandBufferAddControlMemory);API(nvnCommandBufferGetCommandMemoryFree);
static _Thread_local sigjmp_buf *escape;
static unsigned char *memory;static unsigned char controls[16][256] __attribute__((aligned(8)));
static void *arena;static NVNdevice *deviceRef;static const size_t arenaBytes=135168;
static NVNmemoryPool pool;static int mode,grants;
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s at %d error=%s\n",#x,__LINE__,botw_graphics_last_error());exit(1);}}while(0)
static void error(const char *s){fprintf(stderr,"EXPECTED:%s\n",s);if(escape)siglongjmp(*escape,1);abort();}
static int inside(const void *p,size_t n,const void *b,size_t bytes){uintptr_t a=(uintptr_t)p,x=(uintptr_t)b;return a>=x&&a-x<=bytes&&n<=bytes-(a-x);}
static int access_memory(const void *p,size_t n,int write){return write?(inside(p,n,memory,65536)||inside(p,n,controls,sizeof controls)||inside(p,n,arena,arenaBytes)):p!=NULL;}
static void refill(NVNcommandBuffer *c,NVNcommandBufferMemoryEvent event,size_t n,void *data){(void)n;(void)data;
 if(mode==4){NVNqueueBuilder b;nvnQueueBuilderSetDefaults(&b);nvnQueueBuilderSetDevice(&b,deviceRef);nvnQueueBuilderSetQueueMemory(&b,arena,arenaBytes);(void)nvnQueueInitialize((NVNqueue*)c,&b);CHECK(0);}
 if(mode==1){nvnCommandBufferAddCommandMemory(c,&pool,-1,512);CHECK(0);}
 if(event==NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY)nvnCommandBufferAddCommandMemory(c,&pool,8192+(grants++)*512,512);
 else nvnCommandBufferAddControlMemory(c,controls[grants++],256);
}
typedef struct{NVNcommandBuffer *c;int rejected;} Worker;
static void *busy_worker(void *opaque){Worker *w=opaque;sigjmp_buf local;escape=&local;
 if(!sigsetjmp(local,1)){(void)nvnCommandBufferGetCommandMemoryFree(w->c);CHECK(0);}
 CHECK(strstr(botw_graphics_last_error(),"busy in a memory callback"));w->rejected=1;escape=NULL;return NULL;
}
static int invoke(PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC f,NVNcommandBuffer *c,NVNcommandBufferMemoryEvent event,size_t n,void *data,char *message,size_t cap){
 if(mode==2)@throw [NSException exceptionWithName:@"Synthetic" reason:@"native invoker exception" userInfo:nil];
 if(mode==3){Worker w={c,0};pthread_t thread;CHECK(!pthread_create(&thread,NULL,busy_worker,&w));CHECK(!pthread_join(thread,NULL));CHECK(w.rejected);}
 sigjmp_buf local;sigjmp_buf *saved=escape;escape=&local;
 if(sigsetjmp(local,1)){snprintf(message,cap,"contained nested error: %s",botw_graphics_last_error());escape=saved;return 0;}
 f(c,event,n,data);escape=saved;return 1;
}
int main(void){if(!botw_graphics_init(NULL,error,NULL))return 77;botw_graphics_set_memory_access_validator(access_memory);botw_graphics_set_command_memory_invoker(invoke);
#define LOAD(n) n=(NVNT_##n)botw_nvn_bootstrap_loader(#n);CHECK(n)
 LOAD(nvnQueueBuilderSetDefaults);LOAD(nvnQueueBuilderSetDevice);LOAD(nvnQueueBuilderSetQueueMemory);LOAD(nvnQueueInitialize);
 LOAD(nvnDeviceBuilderSetDefaults);LOAD(nvnDeviceInitialize);LOAD(nvnMemoryPoolBuilderSetDefaults);LOAD(nvnMemoryPoolBuilderSetDevice);LOAD(nvnMemoryPoolBuilderSetStorage);LOAD(nvnMemoryPoolInitialize);LOAD(nvnCommandBufferInitialize);LOAD(nvnCommandBufferFinalize);LOAD(nvnCommandBufferSetMemoryCallback);LOAD(nvnCommandBufferBeginRecording);LOAD(nvnCommandBufferEndRecording);LOAD(nvnCommandBufferCopyBufferToBuffer);LOAD(nvnCommandBufferAddCommandMemory);LOAD(nvnCommandBufferAddControlMemory);LOAD(nvnCommandBufferGetCommandMemoryFree);
 NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));deviceRef=&device;CHECK(!posix_memalign(&arena,4096,arenaBytes));memset(arena,0x6b,arenaBytes);CHECK(!posix_memalign((void**)&memory,getpagesize(),65536));memset(memory,0,65536);
 NVNmemoryPoolBuilder pb;nvnMemoryPoolBuilderSetDefaults(&pb);nvnMemoryPoolBuilderSetDevice(&pb,&device);nvnMemoryPoolBuilderSetStorage(&pb,memory,65536);CHECK(nvnMemoryPoolInitialize(&pool,&pb));
 for(int badMode=1;badMode<=3;badMode++){NVNcommandBuffer c;CHECK(nvnCommandBufferInitialize(&c,&device));nvnCommandBufferSetMemoryCallback(&c,refill);nvnCommandBufferBeginRecording(&c);mode=badMode==3?4:badMode;sigjmp_buf outer;escape=&outer;
  if(!sigsetjmp(outer,1)){nvnCommandBufferCopyBufferToBuffer(&c,(uintptr_t)memory,(uintptr_t)memory+64,8,0);CHECK(0);}
  CHECK(strstr(botw_graphics_last_error(),badMode==1?"contained nested error":badMode==2?"native invoker exception":"busy in a memory callback"));
  if(badMode==3)for(size_t i=0;i<arenaBytes;i++)CHECK(((unsigned char*)arena)[i]==0x6b);
  mode=3;nvnCommandBufferCopyBufferToBuffer(&c,(uintptr_t)memory,(uintptr_t)memory+64,8,0);CHECK(nvnCommandBufferEndRecording(&c));nvnCommandBufferFinalize(&c);escape=NULL;
 }
 botw_graphics_shutdown();free(arena);free(memory);puts("ACTUAL_NVN_ERROR_AND_OBJC_EXCEPTION_RESTORE_RECORDING_LOCKS_OK");return 0;
}
