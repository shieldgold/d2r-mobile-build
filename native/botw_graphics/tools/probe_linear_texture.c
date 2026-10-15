/* Synthetic fixtures only: no game input, translated shader, or fixed game offset. */
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static sigjmp_buf recovery;
static const void *controlPointers[2];
static int validControl(const void *p,size_t n){return n<=64&&(p==controlPointers[0]||p==controlPointers[1]);}
static void error(const char *s){fprintf(stderr,"ERROR:%s\n",s);siglongjmp(recovery,1);}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s (%s)\n",#x,botw_graphics_last_error());return 1;}}while(0)
#define API(n) static NVNT_##n n
API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceGetInteger);
API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);
API(nvnTextureBuilderSetDevice);API(nvnTextureBuilderSetDefaults);API(nvnTextureBuilderSetSize2D);API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetDepth);API(nvnTextureBuilderSetLevels);API(nvnTextureBuilderSetSamples);API(nvnTextureBuilderSetFormat);API(nvnTextureBuilderSetStorage);API(nvnTextureBuilderSetFlags);API(nvnTextureBuilderSetStride);API(nvnTextureBuilderGetStorageSize);API(nvnTextureBuilderGetStorageAlignment);API(nvnTextureInitialize);API(nvnTextureFinalize);API(nvnTextureGetStride);API(nvnTextureReadTexels);API(nvnTextureWriteTexels);
API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueInitialize);API(nvnQueueSubmitCommands);API(nvnQueueFinish);API(nvnCommandBufferInitialize);API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);API(nvnCommandBufferSetRenderTargets);API(nvnCommandBufferClearColor);
API(nvnProgramInitialize);API(nvnProgramSetShaders);API(nvnCommandBufferBindProgram);API(nvnCommandBufferDrawArrays);API(nvnTexturePoolInitialize);API(nvnTexturePoolRegisterTexture);API(nvnDeviceGetTextureHandle);API(nvnCommandBufferSetTexturePool);API(nvnCommandBufferBindTexture);API(nvnCommandBufferBindStorageBuffer);API(nvnCommandBufferReportCounter);
API(nvnCommandBufferBindVertexBuffer);
#undef API
#define LOAD(n) n=(NVNT_##n)botw_nvn_bootstrap_loader(#n)
static void builder(NVNtextureBuilder *b,NVNdevice *d,NVNmemoryPool *p,int flags,size_t stride,ptrdiff_t offset){
 memset(b,0,sizeof *b);nvnTextureBuilderSetDevice(b,d);nvnTextureBuilderSetDefaults(b);nvnTextureBuilderSetSize2D(b,5,3);nvnTextureBuilderSetFormat(b,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(b,p,offset);nvnTextureBuilderSetFlags(b,flags);nvnTextureBuilderSetStride(b,stride);
}
int main(int argc,char **argv){
 const char *mode=argc>1?argv[1]:"query";
 if(sigsetjmp(recovery,1)){fprintf(stderr,"unexpected:%s\n",botw_graphics_last_error());return 1;}
 if(!botw_graphics_init(NULL,error,validControl))return 77;
 LOAD(nvnDeviceBuilderSetDefaults);LOAD(nvnDeviceInitialize);LOAD(nvnDeviceGetInteger);
 LOAD(nvnMemoryPoolBuilderSetDefaults);LOAD(nvnMemoryPoolBuilderSetDevice);LOAD(nvnMemoryPoolBuilderSetStorage);LOAD(nvnMemoryPoolBuilderSetFlags);LOAD(nvnMemoryPoolInitialize);LOAD(nvnMemoryPoolFinalize);
 LOAD(nvnTextureBuilderSetDevice);LOAD(nvnTextureBuilderSetDefaults);LOAD(nvnTextureBuilderSetSize2D);LOAD(nvnTextureBuilderSetTarget);LOAD(nvnTextureBuilderSetDepth);LOAD(nvnTextureBuilderSetLevels);LOAD(nvnTextureBuilderSetSamples);LOAD(nvnTextureBuilderSetFormat);LOAD(nvnTextureBuilderSetStorage);LOAD(nvnTextureBuilderSetFlags);LOAD(nvnTextureBuilderSetStride);LOAD(nvnTextureBuilderGetStorageSize);LOAD(nvnTextureBuilderGetStorageAlignment);LOAD(nvnTextureInitialize);LOAD(nvnTextureFinalize);LOAD(nvnTextureGetStride);LOAD(nvnTextureReadTexels);LOAD(nvnTextureWriteTexels);
 LOAD(nvnQueueBuilderSetDefaults);LOAD(nvnQueueBuilderSetDevice);LOAD(nvnQueueInitialize);LOAD(nvnQueueSubmitCommands);LOAD(nvnQueueFinish);LOAD(nvnCommandBufferInitialize);LOAD(nvnCommandBufferBeginRecording);LOAD(nvnCommandBufferEndRecording);LOAD(nvnCommandBufferSetRenderTargets);LOAD(nvnCommandBufferClearColor);
 LOAD(nvnProgramInitialize);LOAD(nvnProgramSetShaders);LOAD(nvnCommandBufferBindProgram);LOAD(nvnCommandBufferDrawArrays);LOAD(nvnTexturePoolInitialize);LOAD(nvnTexturePoolRegisterTexture);LOAD(nvnDeviceGetTextureHandle);LOAD(nvnCommandBufferSetTexturePool);LOAD(nvnCommandBufferBindTexture);LOAD(nvnCommandBufferBindStorageBuffer);LOAD(nvnCommandBufferReportCounter);
 LOAD(nvnCommandBufferBindVertexBuffer);
 NVNdeviceBuilder db;NVNdevice d;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&d,&db));
 int a=0,r=0;if(!strcmp(mode,"query")){nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_LINEAR_TEXTURE_STRIDE_ALIGNMENT,&a);nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_LINEAR_RENDER_TARGET_STRIDE_ALIGNMENT,&r);CHECK(a>=32&&r>=128&&!(a&(a-1))&&!(r&(r-1)));printf("LINEAR_POLICY_QUERY_OK texture=%d render=%d\n",a,r);botw_graphics_shutdown();return 0;}
 void *memory=NULL;size_t bytes=(size_t)getpagesize()*4;CHECK(!posix_memalign(&memory,getpagesize(),bytes));memset(memory,0xa5,bytes);
 int copied=!strcmp(mode,"copied-pool");NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&d);nvnMemoryPoolBuilderSetFlags(&mb,(copied?NVN_MEMORY_POOL_FLAGS_CPU_CACHED:NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED)|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);nvnMemoryPoolBuilderSetStorage(&mb,(char*)memory+(copied?4:0),bytes-(copied?4:0));CHECK(nvnMemoryPoolInitialize(&pool,&mb));
 NVNtextureBuilder b;NVNtexture texture;builder(&b,&d,&pool,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET,128,0);
 int invalid=0;const char *why="linear";
 if(!strcmp(mode,"zero-stride"))nvnTextureBuilderSetStride(&b,0);
 else if(!strcmp(mode,"bad-stride")){nvnTextureBuilderSetStride(&b,48);invalid=1;}
 else if(!strcmp(mode,"bad-offset")){nvnTextureBuilderSetStorage(&b,&pool,16);invalid=1;}
 else if(!strcmp(mode,"bad-depth")){nvnTextureBuilderSetFormat(&b,NVN_FORMAT_DEPTH32F);invalid=1;}
 else if(!strcmp(mode,"bad-compressed")){nvnTextureBuilderSetFormat(&b,NVN_FORMAT_RGBA_ASTC_4x4);invalid=1;}
 else if(!strcmp(mode,"bad-array")){nvnTextureBuilderSetTarget(&b,NVN_TEXTURE_TARGET_2D_ARRAY);invalid=1;}
 else if(!strcmp(mode,"bad-levels")){nvnTextureBuilderSetLevels(&b,2);invalid=1;}
 else if(!strcmp(mode,"bad-samples")){nvnTextureBuilderSetSamples(&b,2);invalid=1;}
 else if(!strcmp(mode,"copied-pool")){invalid=1;why="coherent";}
 else if(!strcmp(mode,"plain-attachment"))nvnTextureBuilderSetFlags(&b,NVN_TEXTURE_FLAGS_LINEAR);
 if(invalid){if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,&b));}CHECK(strstr(botw_graphics_last_error(),why));puts("LINEAR_INVALID_REJECTED_OK");nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);return 0;}
 CHECK(nvnTextureInitialize(&texture,&b));
 NVNqueueBuilder qb;NVNqueue queue;nvnQueueBuilderSetDefaults(&qb);nvnQueueBuilderSetDevice(&qb,&d);CHECK(nvnQueueInitialize(&queue,&qb));NVNcommandBuffer command;CHECK(nvnCommandBufferInitialize(&command,&d));const NVNtexture *targets[]={&texture};float red[]={1,0,0,1};
 if(!strncmp(mode,"feedback-",9)||!strcmp(mode,"sample-draw")||!strcmp(mode,"unused-vertex")){
  unsigned char controls[2][64]={{0}},codes[2][128]={{0}};size_t codeOffset=(size_t)getpagesize()*2,descriptorOffset=(size_t)getpagesize()*3;
  for(unsigned i=0;i<2;i++){*(uint32_t*)controls[i]=0x98761234;*(uint32_t*)codes[i]=0x12345678;controls[i][8]=codes[i][8]=(unsigned char)(30+i);controlPointers[i]=controls[i];memcpy((char*)memory+codeOffset+i*128,codes[i],128);}
  int storage=!strcmp(mode,"feedback-storage"),sample=!strcmp(mode,"sample-draw"),unused=!strcmp(mode,"unused-vertex");
  const char *msl=storage?"#include <metal_stdlib>\nusing namespace metal;vertex float4 vs(uint v [[vertex_id]],device uint& out [[buffer(2)]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};out=42;return float4(p[v],0,1);}fragment float4 fs(){return float4(1,0,0,1);}":"#include <metal_stdlib>\nusing namespace metal;vertex float4 vs(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}fragment float4 fs(texture2d<float> t [[texture(0)]]){return t.read(uint2(0,0));}";
  if(unused)msl="#include <metal_stdlib>\nusing namespace metal;vertex float4 vs(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}fragment float4 fs(){return float4(1,0,0,1);}";
  botw_graphics_shader_binding writeBinding={BOTW_GRAPHICS_STORAGE,3,2},sampleBinding={BOTW_GRAPHICS_TEXTURE,0,0};
  CHECK(botw_graphics_register_shader_with_bindings(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs",storage?&writeBinding:NULL,storage?1:0));
  CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,msl,"fs",storage||unused?NULL:&sampleBinding,storage||unused?0:1));
  NVNprogram program;CHECK(nvnProgramInitialize(&program,&d));NVNshaderData sd[]={{(uintptr_t)memory+codeOffset,controls[0]},{(uintptr_t)memory+codeOffset+128,controls[1]}};CHECK(nvnProgramSetShaders(&program,2,sd));
  NVNtexturePool tp;CHECK(nvnTexturePoolInitialize(&tp,&pool,descriptorOffset,16));nvnTexturePoolRegisterTexture(&tp,3,&texture,NULL);uint64_t handle=nvnDeviceGetTextureHandle(&d,3,0);
  NVNtexture output;if(sample){builder(&b,&d,&pool,0,0,4096);CHECK(nvnTextureInitialize(&output,&b));targets[0]=&output;unsigned char texel[]={7,19,31,255};memcpy(memory,texel,4);}
  unsigned char before[384];memcpy(before,memory,sizeof before);size_t counterOffset=descriptorOffset+1024;
  nvnCommandBufferBeginRecording(&command);nvnCommandBufferSetRenderTargets(&command,1,targets,NULL,NULL,NULL);nvnCommandBufferClearColor(&command,0,red,15);
  NVNcommandHandle submit[2];int submitCount=1;
  if(!strcmp(mode,"feedback-two-handles")){submit[0]=nvnCommandBufferEndRecording(&command);nvnCommandBufferBeginRecording(&command);submitCount=2;}
  if(!strcmp(mode,"feedback-after-counter"))nvnCommandBufferReportCounter(&command,NVN_COUNTER_TYPE_TIMESTAMP_TOP,(uintptr_t)memory+counterOffset);
  nvnCommandBufferBindProgram(&command,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
  if(unused)nvnCommandBufferBindVertexBuffer(&command,14,(uintptr_t)memory,4);
  if(storage)nvnCommandBufferBindStorageBuffer(&command,NVN_SHADER_STAGE_VERTEX,3,(uintptr_t)memory,4);
  else{nvnCommandBufferSetTexturePool(&command,&tp);nvnCommandBufferBindTexture(&command,NVN_SHADER_STAGE_FRAGMENT,0,handle);}
  nvnCommandBufferDrawArrays(&command,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);submit[submitCount-1]=nvnCommandBufferEndRecording(&command);
  if(!sample&&!unused){if(!sigsetjmp(recovery,1)){nvnQueueSubmitCommands(&queue,submitCount,submit);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"linear render pass feedback"));CHECK(!memcmp(before,memory,sizeof before));for(unsigned i=0;i<16;i++)CHECK(((unsigned char*)memory)[counterOffset+i]==0xa5);puts("LINEAR_FEEDBACK_PREFLIGHT_PRESERVES_POOL_AND_COUNTER_OK");}
  else{nvnQueueSubmitCommands(&queue,submitCount,submit);nvnQueueFinish(&queue);unsigned char result[60];CHECK(botw_graphics_read_texture(sample?&output:&texture,result,sizeof result));for(unsigned i=0;i<15;i++)CHECK(result[i*4]==(sample?7:255)&&result[i*4+1]==(sample?19:0)&&result[i*4+2]==(sample?31:0)&&result[i*4+3]==255);puts(unused?"LINEAR_UNUSED_VERTEX_SLOT_IS_NOT_FEEDBACK_OK":"LINEAR_SAMPLING_REACHES_REAL_DRAW_OK");}
  botw_graphics_shutdown();free(memory);return 0;
 }
 if(!strcmp(mode,"plain-attachment")){if(!sigsetjmp(recovery,1)){nvnCommandBufferBeginRecording(&command);nvnCommandBufferSetRenderTargets(&command,1,targets,NULL,NULL,NULL);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"linear render target"));puts("LINEAR_SAMPLED_ATTACHMENT_REJECTED_OK");botw_graphics_shutdown();free(memory);return 0;}
 if(!strcmp(mode,"live-alias")||!strcmp(mode,"live-private-alias")||!strcmp(mode,"private-linear-alias")||!strcmp(mode,"private-retired")){
  if(!strcmp(mode,"private-linear-alias")){nvnTextureFinalize(&texture);builder(&b,&d,&pool,0,0,0);CHECK(nvnTextureInitialize(&texture,&b));}
  if(!strcmp(mode,"private-retired")){nvnTextureFinalize(&texture);builder(&b,&d,&pool,0,0,0);CHECK(nvnTextureInitialize(&texture,&b));nvnCommandBufferBeginRecording(&command);nvnCommandBufferSetRenderTargets(&command,1,targets,NULL,NULL,NULL);nvnCommandBufferClearColor(&command,0,red,15);NVNcommandHandle h=nvnCommandBufferEndRecording(&command);nvnQueueSubmitCommands(&queue,1,&h);nvnQueueFinish(&queue);nvnTextureFinalize(&texture);}
  NVNtexture alias;builder(&b,&d,&pool,!strcmp(mode,"live-private-alias")?0:NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET,128,0);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&alias,&b));}CHECK(strstr(botw_graphics_last_error(),"authority")||strstr(botw_graphics_last_error(),"overlap"));puts("LINEAR_STORAGE_ALIAS_REJECTED_OK");botw_graphics_shutdown();free(memory);return 0;
 }
 nvnDeviceGetInteger(&d,NVN_DEVICE_INFO_LINEAR_RENDER_TARGET_STRIDE_ALIGNMENT,&r);CHECK(nvnTextureBuilderGetStorageAlignment(&b)==(size_t)r);size_t stride=(20u+(size_t)r-1)&~((size_t)r-1);CHECK(nvnTextureBuilderGetStorageSize(&b)==stride*3);CHECK(nvnTextureGetStride(&texture)==(!strcmp(mode,"zero-stride")?0:128));
 nvnCommandBufferBeginRecording(&command);nvnCommandBufferSetRenderTargets(&command,1,targets,NULL,NULL,NULL);nvnCommandBufferClearColor(&command,0,red,15);NVNcommandHandle h=nvnCommandBufferEndRecording(&command);nvnQueueSubmitCommands(&queue,1,&h);nvnQueueFinish(&queue);
 unsigned char pixels[60];CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));for(unsigned y=0;y<3;y++){for(unsigned x=0;x<5;x++){unsigned char *p=(unsigned char*)memory+y*stride+x*4;CHECK(p[0]==255&&!p[1]&&!p[2]&&p[3]==255);CHECK(!memcmp(p,pixels+(y*5+x)*4,4));}for(size_t x=20;x<stride;x++)CHECK(((unsigned char*)memory)[y*stride+x]==0xa5);}
 unsigned char replacement[]={7,19,31,255};NVNcopyRegion one={2,1,0,1,1,1};nvnTextureWriteTexels(&texture,NULL,&one,replacement);unsigned char result[4];nvnTextureReadTexels(&texture,NULL,&one,result);CHECK(!memcmp(result,replacement,4));CHECK(!memcmp((char*)memory+stride+8,replacement,4));
 nvnTextureFinalize(&texture);nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);puts("LINEAR_GPU_POOL_CPU_AND_PADDING_OK");return 0;
}
