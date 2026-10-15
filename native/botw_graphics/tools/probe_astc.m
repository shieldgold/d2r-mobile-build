/* Synthetic ASTC blocks only. No game code, shaders, textures or assets.
 * Encoding: https://github.com/KhronosGroup/DataFormat/blob/main/astc.txt
 * Void-Extent Blocks; independently
 * checked against ARM astcenc_symbolic_physical.cpp symbolic_to_physical.
 * No encoder implementation is copied; blocks and color ramps are synthetic.
 * FC FD FF FF FF FF FF FF sets LDR + reserved bits + all-ones extents.
 * Bytes 8..15 hold four little-endian UNORM16 color components.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "graphics.h"
#include "nvn_protos.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do {if(!(x)){fprintf(stderr,"CHECK line %d: %s: %s\n",__LINE__,#x,botw_graphics_last_error());return 1;}}while(0)
static const void *controls_seen[2];
static int valid(const void *p,size_t size){return size<=64&&(p==controls_seen[0]||p==controls_seen[1]);}
static void color(unsigned bx,unsigned by,unsigned char rgba[4]) {
 rgba[0]=(unsigned char)(64+bx*17+by*5);rgba[1]=(unsigned char)(128+bx*7+by*3);
 rgba[2]=(unsigned char)(192-bx*13-by*5);rgba[3]=(unsigned char)(255-bx*8-by*7);
}
static void block(unsigned char *bytes,unsigned bx,unsigned by) {
 const unsigned char header[]={0xfc,0xfd,0xff,0xff,0xff,0xff,0xff,0xff};
 memcpy(bytes,header,8);unsigned char rgba[4];color(bx,by,rgba);
 for(unsigned c=0;c<4;c++){unsigned n=rgba[c]*257u;bytes[8+2*c]=(unsigned char)n;bytes[9+2*c]=(unsigned char)(n>>8);}
}
static unsigned expected(unsigned value,int srgb) {
 double c=(double)value/255.0;
 if(srgb)c=c<=0.04045?c/12.92:pow((c+0.055)/1.055,2.4);
 return (unsigned)floor(c*255.0+0.5);
}
/* Independent, single-GOB-high block-linear addressing. Packaged block height
 * is explicitly zero (log2), so both x/y GOB boundaries have fixed strides. */
static size_t address(unsigned xbytes,unsigned y,unsigned gobs_x) {
 return (size_t)(y/8)*gobs_x*512+(size_t)(xbytes/64)*512+
        (xbytes%64/32)*256+(y%8/2)*64+(xbytes%32/16)*32+(y%2)*16+xbytes%16;
}
int main(void){@autoreleasepool{
 id<MTLDevice> metal=MTLCreateSystemDefaultDevice();if(!metal)return 77;
 NSDictionary *deviceInfo=@{@"name":metal.name,@"unified_memory":@(metal.hasUnifiedMemory),
  @"Apple1":@([metal supportsFamily:MTLGPUFamilyApple1]),@"Apple2":@([metal supportsFamily:MTLGPUFamilyApple2]),
  @"Apple3":@([metal supportsFamily:MTLGPUFamilyApple3]),@"Apple4":@([metal supportsFamily:MTLGPUFamilyApple4]),
  @"Apple5":@([metal supportsFamily:MTLGPUFamilyApple5]),@"Apple6":@([metal supportsFamily:MTLGPUFamilyApple6]),
  @"Apple7":@([metal supportsFamily:MTLGPUFamilyApple7]),@"Apple8":@([metal supportsFamily:MTLGPUFamilyApple8]),
  @"Apple9":@([metal supportsFamily:MTLGPUFamilyApple9])};
 NSData *deviceJSON=[NSJSONSerialization dataWithJSONObject:deviceInfo options:NSJSONWritingSortedKeys error:NULL];
 fprintf(stderr,"DEVICE %s\n",[[[NSString alloc]initWithData:deviceJSON encoding:NSUTF8StringEncoding] UTF8String]);
 CHECK(botw_graphics_init(NULL,NULL,valid));
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceGetInteger);API(nvnDeviceSetWindowOriginMode);API(nvnMemoryPoolBuilderSetDefaults);
 API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolInitialize);
 API(nvnMemoryPoolGetBufferAddress);API(nvnMemoryPoolFinalize);API(nvnMemoryPoolFlushMappedRange);
 API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetDevice);API(nvnQueueInitialize);API(nvnQueueFinish);API(nvnQueueSubmitCommands);
 API(nvnCommandBufferInitialize);API(nvnCommandBufferBeginRecording);API(nvnCommandBufferEndRecording);
 API(nvnCommandBufferSetRenderTargets);API(nvnCommandBufferBindProgram);API(nvnCommandBufferDrawArrays);
 API(nvnCommandBufferSetViewport);API(nvnCommandBufferSetScissor);
 API(nvnTextureBuilderSetDefaults);API(nvnTextureBuilderSetDevice);API(nvnTextureBuilderSetSize2D);API(nvnTextureBuilderSetFormat);
 API(nvnTextureBuilderSetFlags);API(nvnTextureBuilderSetStride);API(nvnTextureBuilderSetStorage);
 API(nvnTextureBuilderSetPackagedTextureLayout);API(nvnTextureBuilderGetStorageSize);API(nvnTextureBuilderGetStorageAlignment);
 API(nvnTextureInitialize);API(nvnTextureFinalize);API(nvnTexturePoolInitialize);API(nvnTexturePoolRegisterTexture);
 API(nvnSamplerPoolInitialize);API(nvnSamplerPoolRegisterSampler);API(nvnSamplerBuilderSetDefaults);
 API(nvnSamplerBuilderSetDevice);API(nvnSamplerBuilderSetMinMagFilter);API(nvnSamplerBuilderSetWrapMode);API(nvnSamplerInitialize);
 API(nvnDeviceGetTextureHandle);API(nvnCommandBufferSetTexturePool);API(nvnCommandBufferSetSamplerPool);API(nvnCommandBufferBindTexture);
 API(nvnProgramInitialize);API(nvnProgramSetShaders);
 NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));
 int supported=-1;nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_SUPPORTS_ASTC_FORMATS,&supported);
 CHECK(supported==([metal supportsFamily:MTLGPUFamilyApple2]?1:0));
 if(!supported){botw_graphics_shutdown();return 78;}
 nvnDeviceSetWindowOriginMode(&device,NVN_WINDOW_ORIGIN_MODE_UPPER_LEFT);
 size_t allocation=1024u*1024u,page=(size_t)getpagesize();unsigned char *memory=NULL;
 CHECK(!posix_memalign((void**)&memory,page,allocation));memset(memory,0,allocation);
 NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&device);
 nvnMemoryPoolBuilderSetStorage(&mb,memory,allocation);CHECK(nvnMemoryPoolInitialize(&pool,&mb));uint64_t gpu=nvnMemoryPoolGetBufferAddress(&pool);
 NVNqueueBuilder qb;NVNqueue queue;nvnQueueBuilderSetDefaults(&qb);nvnQueueBuilderSetDevice(&qb,&device);CHECK(nvnQueueInitialize(&queue,&qb));
 NVNcommandBuffer cb;CHECK(nvnCommandBufferInitialize(&cb,&device));
 unsigned char controls[2][64]={{0}},codes[2][128]={{0}};
 for(unsigned i=0;i<2;i++){uint32_t cm=0x98761234,nm=0x12345678;memcpy(controls[i],&cm,4);memcpy(codes[i],&nm,4);
  controls[i][8]=codes[i][8]=(unsigned char)(i+11);controls_seen[i]=controls[i];memcpy(memory+2048+i*128,codes[i],128);}
 const char *msl="#include <metal_stdlib>\nusing namespace metal;"
 "vertex float4 vs(uint v [[vertex_id]]) {float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}"
 "fragment float4 fs(float4 p [[position]],texture2d<float> t [[texture(3)]],sampler s [[sampler(0)]])"
 "{return t.sample(s,p.xy/float2(t.get_width(),t.get_height()),level(0.0));}";
 botw_graphics_shader_binding bindings[]={{BOTW_GRAPHICS_TEXTURE,9,3},{BOTW_GRAPHICS_SAMPLER,9,0}};
 CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs"));
 CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,msl,"fs",bindings,2));
 NVNprogram program;CHECK(nvnProgramInitialize(&program,&device));NVNshaderData shaders[]={{gpu+2048,controls[0]},{gpu+2176,controls[1]}};
 CHECK(nvnProgramSetShaders(&program,2,shaders));
 NVNtexturePool tp;NVNsamplerPool sp;CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16));CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));
 NVNsamplerBuilder sb;NVNsampler sampler;nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);
 nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST,NVN_MAG_FILTER_NEAREST);
 nvnSamplerBuilderSetWrapMode(&sb,NVN_WRAP_MODE_CLAMP_TO_EDGE,NVN_WRAP_MODE_CLAMP_TO_EDGE,NVN_WRAP_MODE_CLAMP_TO_EDGE);
 CHECK(nvnSamplerInitialize(&sampler,&sb));nvnSamplerPoolRegisterSampler(&sp,4,&sampler);
 unsigned checked=0;unsigned long long pixels_checked=0;
 const unsigned footprints[14][2]={{4,4},{5,4},{5,5},{6,5},{6,6},{8,5},{8,6},{8,8},{10,5},{10,6},{10,8},{10,10},{12,10},{12,12}};
 for(unsigned srgb=0;srgb<2;srgb++)for(unsigned shape=0;shape<14;shape++)for(unsigned sizecase=0;sizecase<4;sizecase++){
  unsigned bw=footprints[shape][0],bh=footprints[shape][1];
  unsigned width=sizecase==0?1:sizecase==1?bw:sizecase==2?bw+1:bw*5+1;
  unsigned height=sizecase==0?1:sizecase==1?bh:sizecase==2?bh+1:bh*9+1;
  unsigned columns=(width+bw-1)/bw,rows=(height+bh-1)/bh,gobs_x=(columns*16+63)/64;
  size_t storage=(size_t)gobs_x*((rows+7)/8)*512,offset=allocation-storage;
  memset(memory+offset,0,storage);
  for(unsigned y=0;y<rows;y++)for(unsigned x=0;x<columns;x++){size_t a=address(x*16,y,gobs_x);CHECK(a+16<=storage);block(memory+offset+a,x,y);}
  NVNtextureBuilder tb;NVNtexture compressed,target;
  nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,(int)width,(int)height);
  nvnTextureBuilderSetFormat(&tb,(NVNformat)((srgb?0x87:0x79)+shape));nvnTextureBuilderSetStorage(&tb,&pool,(ptrdiff_t)offset);
  NVNpackagedTextureLayout packaged={0};nvnTextureBuilderSetPackagedTextureLayout(&tb,&packaged);
  CHECK(nvnTextureBuilderGetStorageAlignment(&tb)==512);CHECK(nvnTextureBuilderGetStorageSize(&tb)==storage);
  CHECK(nvnTextureInitialize(&compressed,&tb));nvnTexturePoolRegisterTexture(&tp,3,&compressed,NULL);
  nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,(int)width,(int)height);
  nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetFlags(&tb,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);
  nvnTextureBuilderSetStride(&tb,256);nvnTextureBuilderSetStorage(&tb,&pool,65536);CHECK(nvnTextureInitialize(&target,&tb));
  const NVNtexture *targets[]={&target};nvnMemoryPoolFlushMappedRange(&pool,0,allocation);
  nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
  nvnCommandBufferSetViewport(&cb,0,0,(int)width,(int)height);nvnCommandBufferSetScissor(&cb,0,0,(int)width,(int)height);
  nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
  nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);
  nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,9,nvnDeviceGetTextureHandle(&device,3,4));
  nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);NVNcommandHandle handle=nvnCommandBufferEndRecording(&cb);
  nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
  size_t bytes=(size_t)width*height*4;unsigned char *pixels=malloc(bytes);CHECK(pixels);CHECK(botw_graphics_read_texture(&target,pixels,bytes));
  unsigned maximum_error=0;
  for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++){
   unsigned char rgba[4];color(x/bw,y/bh,rgba);
   for(unsigned c=0;c<4;c++){
    unsigned want=expected(rgba[c],srgb&&c<3),got=pixels[((size_t)y*width+x)*4+c];unsigned delta=want>got?want-got:got-want;
    if(delta>maximum_error)maximum_error=delta;
    if(delta>1){fprintf(stderr,"pixel mismatch shape=%ux%u srgb=%u size=%ux%u xy=%u,%u channel=%u got=%u want=%u\n",bw,bh,srgb,width,height,x,y,c,got,want);return 1;}
   }
  }
  fprintf(stdout,"{\"footprint\":[%u,%u],\"srgb\":%s,\"size_case\":%u,\"size\":[%u,%u],\"storage_bytes\":%zu,\"pool_end_exact\":true,\"pixels\":%u,\"max_channel_error\":%u,\"first_rgba\":[%u,%u,%u,%u]}\n",
          bw,bh,srgb?"true":"false",sizecase,width,height,storage,width*height,maximum_error,pixels[0],pixels[1],pixels[2],pixels[3]);fflush(stdout);
  free(pixels);nvnTextureFinalize(&target);nvnTextureFinalize(&compressed);checked++;pixels_checked+=(unsigned long long)width*height;
 }
 fprintf(stderr,"ASTC_NVN_METAL_SAMPLING_OK cases=%u pixels=%llu\n",checked,pixels_checked);
 CHECK(botw_graphics_presented_frame_count()==0);nvnMemoryPoolFinalize(&pool);botw_graphics_shutdown();free(memory);return 0;
}}
