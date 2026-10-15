/* Synthetic pools and commands; Metal owns per-invocation shader storage. */
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static sigjmp_buf recovery;
static const void *denied;
static int accessMemory(const void *p,size_t n,int write){(void)n;return !(p==denied&&write);}
static void error(const char *s){fprintf(stderr,"ERROR:%s\n",s);siglongjmp(recovery,1);}
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s %s\n",#x,botw_graphics_last_error());return 1;}}while(0)
#define REJECT(call,text) do{if(!sigsetjmp(recovery,1)){call;CHECK(0);}CHECK(strstr(botw_graphics_last_error(),text));}while(0)
int main(void){
 if(sigsetjmp(recovery,1)){fprintf(stderr,"unexpected failure: %s\n",botw_graphics_last_error());return 1;}
 if(!botw_graphics_init(NULL,error,NULL))return 77;
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceGetInteger);
 API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);API(nvnMemoryPoolMap);API(nvnMemoryPoolGetSize);API(nvnMemoryPoolGetBufferAddress);API(nvnMemoryPoolFlushMappedRange);API(nvnMemoryPoolInvalidateMappedRange);
 API(nvnTexturePoolInitialize);API(nvnTextureBuilderSetDefaults);API(nvnTextureBuilderSetDevice);API(nvnTextureBuilderSetSize2D);API(nvnTextureBuilderSetFormat);API(nvnTextureBuilderSetStorage);API(nvnTextureInitialize);
 API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueInitialize);API(nvnQueueSubmitCommands);API(nvnQueueFinish);
 API(nvnCommandBufferInitialize);API(nvnCommandBufferFinalize);API(nvnCommandBufferAddControlMemory);API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);API(nvnCommandBufferSetShaderScratchMemory);API(nvnCommandBufferGetControlMemoryUsed);API(nvnCommandBufferAddCommandMemory);
 NVNdeviceBuilder db;NVNdevice d,other;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&d,&db));CHECK(nvnDeviceInitialize(&other,&db));
 int cap=-1;nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_ALIGNMENT,&cap);CHECK(cap==4096);nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_GRANULARITY,&cap);CHECK(cap==131072);
 nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_SCALE_FACTOR_RECOMMENDED,&cap);CHECK(cap==0);nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_SCALE_FACTOR_MINIMUM,&cap);CHECK(cap==0);nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_SHADER_SCRATCH_MEMORY_COMPUTE_SCALE_FACTOR_MINIMUM,&cap);CHECK(cap==0);
 NVNmemoryPoolBuilder mb;NVNmemoryPool empty;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&d);nvnMemoryPoolBuilderSetFlags(&mb,0x21);nvnMemoryPoolBuilderSetStorage(&mb,(void*)0x55,0);CHECK(nvnMemoryPoolInitialize(&empty,&mb));CHECK(nvnMemoryPoolMap(&empty)==(void*)0x55&&nvnMemoryPoolGetSize(&empty)==0&&nvnMemoryPoolGetBufferAddress(&empty)==0x55);
 NVNtexturePool descriptors;REJECT((void)nvnTexturePoolInitialize(&descriptors,&empty,0,1),"descriptor pool bounds");
 NVNtextureBuilder tb;NVNtexture tex;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&d);nvnTextureBuilderSetSize2D(&tb,1,1);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&empty,0);REJECT((void)nvnTextureInitialize(&tex,&tb),"Texture storage bounds");
 nvnMemoryPoolFlushMappedRange(&empty,0,0);nvnMemoryPoolInvalidateMappedRange(&empty,0,0);REJECT(nvnMemoryPoolFlushMappedRange(&empty,0,1),"bounds");REJECT(nvnMemoryPoolInvalidateMappedRange(&empty,1,0),"bounds");
 NVNqueueBuilder qb;NVNqueue q;nvnQueueBuilderSetDefaults(&qb);nvnQueueBuilderSetDevice(&qb,&d);CHECK(nvnQueueInitialize(&q,&qb));NVNcommandBuffer cmd,foreign;CHECK(nvnCommandBufferInitialize(&cmd,&d));CHECK(nvnCommandBufferInitialize(&foreign,&other));
 unsigned char controls[256] __attribute__((aligned(8)));NVNmemoryPool tokenOnly;NVNcommandBuffer spanProbe;nvnMemoryPoolBuilderSetStorage(&mb,controls+128,0);CHECK(nvnMemoryPoolInitialize(&tokenOnly,&mb));CHECK(nvnCommandBufferInitialize(&spanProbe,&d));botw_graphics_set_memory_access_validator(accessMemory);nvnCommandBufferAddControlMemory(&spanProbe,controls,sizeof controls);nvnCommandBufferFinalize(&spanProbe);nvnMemoryPoolFinalize(&tokenOnly);botw_graphics_set_memory_access_validator(NULL);
 nvnCommandBufferBeginRecording(&cmd);nvnCommandBufferSetShaderScratchMemory(&cmd,&empty,0,0);CHECK(nvnCommandBufferGetControlMemoryUsed(&cmd)==16);NVNcommandHandle h=nvnCommandBufferEndRecording(&cmd);nvnQueueSubmitCommands(&q,1,&h);nvnQueueFinish(&q);
 nvnCommandBufferBeginRecording(&foreign);REJECT(nvnCommandBufferSetShaderScratchMemory(&foreign,&empty,0,0),"scratch device");
 nvnCommandBufferBeginRecording(&cmd);REJECT(nvnCommandBufferSetShaderScratchMemory(&cmd,&empty,0,131072),"scratch range");REJECT(nvnCommandBufferSetShaderScratchMemory(&cmd,&empty,-1,0),"scratch range");REJECT(nvnCommandBufferSetShaderScratchMemory(&cmd,NULL,0,131072),"scratch range");REJECT(nvnCommandBufferAddCommandMemory(&cmd,&empty,0,512),"bounds");nvnCommandBufferSetShaderScratchMemory(&cmd,NULL,0,0);NVNcommandHandle unbound=nvnCommandBufferEndRecording(&cmd);nvnQueueSubmitCommands(&q,1,&unbound);nvnQueueFinish(&q);
 void *memory=NULL;CHECK(!posix_memalign(&memory,(size_t)getpagesize(),262144));memset(memory,0x6a,262144);NVNmemoryPool nonempty;nvnMemoryPoolBuilderSetStorage(&mb,memory,262144);CHECK(nvnMemoryPoolInitialize(&nonempty,&mb));
 nvnCommandBufferBeginRecording(&cmd);REJECT(nvnCommandBufferSetShaderScratchMemory(&cmd,&nonempty,1,131072),"scratch range");REJECT(nvnCommandBufferSetShaderScratchMemory(&cmd,&nonempty,0,1),"scratch range");REJECT(nvnCommandBufferSetShaderScratchMemory(&cmd,&nonempty,4096,SIZE_MAX),"scratch range");
 nvnCommandBufferSetShaderScratchMemory(&cmd,&nonempty,4096,131072);NVNcommandHandle redundant=nvnCommandBufferEndRecording(&cmd);nvnQueueSubmitCommands(&q,1,&redundant);nvnMemoryPoolFinalize(&nonempty);for(size_t i=0;i<262144;i++)CHECK(((unsigned char*)memory)[i]==0x6a);free(memory);
 NVNmemoryPool readOnly;unsigned char snapshot[sizeof readOnly];memset(&readOnly,0xa5,sizeof readOnly);memcpy(snapshot,&readOnly,sizeof snapshot);nvnMemoryPoolBuilderSetStorage(&mb,NULL,0);denied=&readOnly;botw_graphics_set_memory_access_validator(accessMemory);REJECT((void)nvnMemoryPoolInitialize(&readOnly,&mb),"not fully writable");CHECK(!memcmp(snapshot,&readOnly,sizeof snapshot));denied=NULL;botw_graphics_set_memory_access_validator(NULL);
 nvnMemoryPoolFinalize(&empty);nvnMemoryPoolBuilderSetStorage(&mb,NULL,0);CHECK(nvnMemoryPoolInitialize(&empty,&mb));REJECT(nvnQueueSubmitCommands(&q,1,&h),"stale scratch");CHECK(!nvnMemoryPoolMap(&empty));denied=&empty;botw_graphics_set_memory_access_validator(accessMemory);REJECT(nvnMemoryPoolFinalize(&empty),"not fully writable");CHECK(!nvnMemoryPoolGetSize(&empty));denied=NULL;nvnMemoryPoolFinalize(&empty);
 botw_graphics_shutdown();puts("METAL_OWNED_SCRATCH_ZERO_POOL_RECORD_RANGE_DEVICE_AND_GENERATION_OK");return 0;
}
