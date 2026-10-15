/* Synthetic base-v0 storage metadata. No game code, virtual mapping bytes, or
 * device/driver queries are performed by these getters. */
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
static sigjmp_buf recovery;
static void rejected(const char *message){fprintf(stderr,"REJECT:%s\n",message);siglongjmp(recovery,1);}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK:%s (%s)\n",#x,botw_graphics_last_error());return 1;}}while(0)
#define API(name) NVNT_##name name=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
/* Independent fixture from the original 0x34-byte format records: nonzero
 * word at +0x30 and depth/stencil bytes at +0xe/+0xf both zero. This includes
 * formats which Metal cannot create. Do not derive it from the bridge mapper. */
static const unsigned ordinaryColorFormats[]={
 0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f,0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,0x18,
 0x22,0x23,0x24,0x25,0x26,0x27,0x28,0x29,0x2a,0x2b,0x2c,0x2d,0x2e,0x2f,0x30,
 0x37,0x38,0x39,0x3a,0x3b,0x3c,0x3d,0x3e,0x3f,0x40,0x41,0x42,0x43,0x44,0x45,0x46,0x47,0x48,0x49,0x4a,0x4b,0x4c,0x4d,0x4e,0x4f,0x50,
 0x6d,0x6e,0x6f,0x70,0x71,0x72,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7a,0x7b,0x7c,0x7d,0x7e,0x7f,0x80,0x81,0x82,0x83,0x84,0x85,0x86,0x87,0x88,0x89,0x8a,0x8b,0x8c,0x8d,0x8e,0x8f,0x90,0x91,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9a,0x9b,0x9c
};
int main(int argc,char **argv){
 const char *mode=argc>1?argv[1]:"builder";
 if(sigsetjmp(recovery,1)){fprintf(stderr,"UNEXPECTED:%s\n",botw_graphics_last_error());return 1;}
 if(!botw_graphics_init(NULL,rejected,NULL))return 77;
 API(nvnTextureBuilderGetStorageClass);API(nvnTextureGetStorageClass);API(nvnTextureBuilderSetDefaults);API(nvnTextureBuilderSetDevice);API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetFlags);API(nvnTextureBuilderSetFormat);API(nvnTextureBuilderSetSamples);API(nvnTextureBuilderSetStorage);API(nvnTextureBuilderSetSize2D);API(nvnTextureBuilderSetStride);
 if(!strncmp(mode,"unsupported-",12)){
  const char *name=!strcmp(mode,"unsupported-raw-builder")?"nvnTextureBuilderGetRawStorageClass":"nvnTextureGetRawStorageClass";
  void (*unimplemented)(void)=(void(*)(void))botw_nvn_bootstrap_loader(name);CHECK(unimplemented);
  if(!sigsetjmp(recovery,1)){unimplemented();CHECK(0);}CHECK(strstr(botw_graphics_last_error(),name));puts("STORAGE_CLASS_OTHER_FAMILIES_REMAIN_UNSUPPORTED_OK");botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"invalid-map-pool")){API(nvnMemoryPoolMapVirtual);if(!sigsetjmp(recovery,1)){nvnMemoryPoolMapVirtual(NULL,0,NULL);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"provenance"));puts("STORAGE_CLASS_MAP_INVALID_POOL_REJECTED_OK");botw_graphics_shutdown();return 0;}
 size_t page=(size_t)getpagesize();NVNtextureBuilder *builder=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(builder!=MAP_FAILED);memset(builder,0xa5,page);
 nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);nvnTextureBuilderSetDefaults(builder);
 if(!strcmp(mode,"builder")||!strcmp(mode,"builder-readonly")){
  const unsigned target[]={NVN_TEXTURE_TARGET_BUFFER,NVN_TEXTURE_TARGET_2D,NVN_TEXTURE_TARGET_3D,NVN_TEXTURE_TARGET_2D_ARRAY,0xffffffff};
  const unsigned flags[]={0,NVN_TEXTURE_FLAGS_COMPRESSIBLE,NVN_TEXTURE_FLAGS_LINEAR,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET,0x50,0xffffffff};
  const unsigned formats[]={0,NVN_FORMAT_RGBA8,NVN_FORMAT_DEPTH32F,NVN_FORMAT_RGBA_ASTC_4x4,0xffffffff};unsigned cases=0;
  for(unsigned t=0;t<sizeof target/sizeof *target;t++)for(unsigned f=0;f<sizeof flags/sizeof *flags;f++)if(target[t]==NVN_TEXTURE_TARGET_BUFFER||(flags[f]&0x50))for(unsigned k=0;k<sizeof formats/sizeof *formats;k++){
   nvnTextureBuilderSetTarget(builder,(NVNtextureTarget)target[t]);nvnTextureBuilderSetFlags(builder,flags[f]);nvnTextureBuilderSetFormat(builder,(NVNformat)formats[k]);nvnTextureBuilderSetSamples(builder,-1);nvnTextureBuilderSetStorage(builder,cases&1?(NVNmemoryPool*)(uintptr_t)0xbad000:NULL,-7);
   unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);if(!strcmp(mode,"builder-readonly"))CHECK(!mprotect(builder,page,PROT_READ));
   CHECK(nvnTextureBuilderGetStorageClass(builder)==0x100);CHECK(!memcmp(before,builder,sizeof before));if(!strcmp(mode,"builder-readonly"))CHECK(!mprotect(builder,page,PROT_READ|PROT_WRITE));cases++;
  }
  for(size_t i=sizeof *builder;i<page;i++)CHECK(((unsigned char*)builder)[i]==0xa5);printf("STORAGE_CLASS_PITCH_METADATA_IMMUTABLE_OK cases=%u\n",cases);munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"builder-generic")||!strcmp(mode,"builder-generic-readonly")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);
  const unsigned targets[]={NVN_TEXTURE_TARGET_2D,NVN_TEXTURE_TARGET_3D,0xffffffff};const unsigned flags[]={0,NVN_TEXTURE_FLAGS_IMAGE,NVN_TEXTURE_FLAGS_DEPTH16_PREFER_FAST_CLEAR,NVN_TEXTURE_FLAGS_SPARSE,0x80000000};const int samples[]={-1,0,1,2,8,0x7fffffff};unsigned cases=0;
  for(unsigned f=0;f<sizeof ordinaryColorFormats/sizeof *ordinaryColorFormats;f++)for(unsigned t=0;t<sizeof targets/sizeof *targets;t++)for(unsigned k=0;k<sizeof flags/sizeof *flags;k++)for(unsigned s=0;s<sizeof samples/sizeof *samples;s++){
   nvnTextureBuilderSetTarget(builder,(NVNtextureTarget)targets[t]);nvnTextureBuilderSetFlags(builder,flags[k]);nvnTextureBuilderSetFormat(builder,(NVNformat)ordinaryColorFormats[f]);nvnTextureBuilderSetSamples(builder,samples[s]);nvnTextureBuilderSetStorage(builder,NULL,-7);
   unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);if(!strcmp(mode,"builder-generic-readonly"))CHECK(!mprotect(builder,page,PROT_READ));
   CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe);CHECK(!memcmp(before,builder,sizeof before));if(!strcmp(mode,"builder-generic-readonly"))CHECK(!mprotect(builder,page,PROT_READ|PROT_WRITE));cases++;
  }
  for(size_t i=sizeof *builder;i<page;i++)CHECK(((unsigned char*)builder)[i]==0xa5);printf("STORAGE_CLASS_GENERIC_METADATA_IMMUTABLE_OK formats=113 cases=%u\n",cases);munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strncmp(mode,"builder-invalid-device-",23)){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);NVNdeviceBuilder db;NVNdevice device,original;memset(&device,0,sizeof device);NVNdevice *inaccessible=NULL;
  if(!strcmp(mode,"builder-invalid-device-finalized")){nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnDeviceFinalize(&device);}
  if(!strcmp(mode,"builder-invalid-device-copy")){nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&original,&db));memcpy(&device,&original,sizeof device);}
  if(!strcmp(mode,"builder-invalid-device-inaccessible")){inaccessible=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(inaccessible!=MAP_FAILED);}
  nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetFlags(builder,0);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGBA8);nvnTextureBuilderSetDevice(builder,!strcmp(mode,"builder-invalid-device-null")?NULL:!strcmp(mode,"builder-invalid-device-foreign")?(NVNdevice*)(uintptr_t)0xdeadbeef000:inaccessible?inaccessible:&device);
  if(!sigsetjmp(recovery,1)){nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object lifetime/type"));puts("STORAGE_CLASS_GENERIC_LIVE_DEVICE_REQUIRED_OK");if(inaccessible)munmap(inaccessible,page);munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"builder-block")||!strcmp(mode,"builder-rejected-formats")){
  const unsigned formats[]={NVN_FORMAT_RGBA8,NVN_FORMAT_R8,NVN_FORMAT_RGBA_ASTC_4x4,NVN_FORMAT_STENCIL8,NVN_FORMAT_DEPTH16,NVN_FORMAT_DEPTH24,NVN_FORMAT_DEPTH32F,NVN_FORMAT_DEPTH24_STENCIL8,NVN_FORMAT_DEPTH32F_STENCIL8,0,NVN_FORMAT_RGB8,NVN_FORMAT_RGB16I,NVN_FORMAT_R8_UI2F,NVN_FORMAT_RGB10A2_I2F,0x9d,0xa0,0xffffffff,NVN_FORMAT_DEPTH16,NVN_FORMAT_DEPTH32F_STENCIL8};unsigned cases=0;
  nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);
  for(unsigned f=0;f<(!strcmp(mode,"builder-block")?1:sizeof formats/sizeof *formats);f++){
   unsigned flag=f<3?NVN_TEXTURE_FLAGS_COMPRESSIBLE:f>=17?NVN_TEXTURE_FLAGS_DEPTH16_PREFER_FAST_CLEAR:0;nvnTextureBuilderSetFlags(builder,flag);nvnTextureBuilderSetFormat(builder,(NVNformat)formats[f]);
   if(!sigsetjmp(recovery,1)){nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"ChooseKind"));CHECK(strstr(botw_graphics_last_error(),"target=1"));char detail[64];snprintf(detail,sizeof detail,"flags=0x%x",flag);CHECK(strstr(botw_graphics_last_error(),detail));snprintf(detail,sizeof detail,"format=0x%x",formats[f]);CHECK(strstr(botw_graphics_last_error(),detail));CHECK(strstr(botw_graphics_last_error(),"samples=0"));cases++;
  }
  printf("STORAGE_CLASS_NATIVE_KIND_REQUIRED_REJECTED_OK cases=%u\n",cases);munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);API(nvnTextureInitialize);API(nvnTextureFinalize);
 NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));void *memory=NULL;CHECK(!posix_memalign(&memory,page,page));memset(memory,0,page);
 NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);nvnMemoryPoolBuilderSetStorage(&mb,memory,page);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
 nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetDefaults(builder);nvnTextureBuilderSetTarget(builder,!strcmp(mode,"buffer-create")?NVN_TEXTURE_TARGET_BUFFER:NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(builder,&pool,0);
 int generic=!strcmp(mode,"texture-private")||!strncmp(mode,"generic-",8);nvnTextureBuilderSetFlags(builder,generic?0:!strcmp(mode,"texture-linear")?NVN_TEXTURE_FLAGS_LINEAR:!strcmp(mode,"texture-both-flags")?0x50:NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);nvnTextureBuilderSetStride(builder,128);
 if(!strcmp(mode,"generic-msaa-create"))nvnTextureBuilderSetSamples(builder,2);if(!strcmp(mode,"generic-format-create"))nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGBA_DXT1);if(!strcmp(mode,"generic-target-create"))nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_3D);if(!strcmp(mode,"generic-flags-create"))nvnTextureBuilderSetFlags(builder,NVN_TEXTURE_FLAGS_SPARSE);
 NVNtexture texture;
 if(!strcmp(mode,"buffer-create")||strstr(mode,"-create")){CHECK(nvnTextureBuilderGetStorageClass(builder)==(!strcmp(mode,"buffer-create")?0x100:0x1fe));if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(strstr(botw_graphics_last_error(),!strcmp(mode,"generic-format-create")?"Texture format":"Unsupported texture"));puts(!strcmp(mode,"buffer-create")?"STORAGE_CLASS_BUFFER_METADATA_IS_NOT_GPU_CREATION_OK":"STORAGE_CLASS_GENERIC_METADATA_IS_NOT_GPU_CREATION_OK");}
 else{
  CHECK(nvnTextureInitialize(&texture,builder));
   int expected=generic?0x1fe:0x100;CHECK(nvnTextureBuilderGetStorageClass(builder)==expected&&nvnTextureGetStorageClass(&texture)==expected);nvnTextureBuilderSetFlags(builder,generic?NVN_TEXTURE_FLAGS_LINEAR:0);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_3D);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_DEPTH32F);CHECK(nvnTextureGetStorageClass(&texture)==expected);
   if(!strcmp(mode,"texture-stale")||!strcmp(mode,"generic-stale")){nvnMemoryPoolFinalize(&pool);CHECK(nvnMemoryPoolInitialize(&pool,&mb));if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture pool"));puts("STORAGE_CLASS_STALE_GENERATION_REJECTED_OK");}
   else if(!strcmp(mode,"device-stale")||!strcmp(mode,"generic-device-stale")){nvnDeviceFinalize(&device);if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture device"));puts("STORAGE_CLASS_STALE_DEVICE_REJECTED_OK");}
   else if(!strcmp(mode,"texture-finalized")||!strcmp(mode,"generic-finalized")){nvnTextureFinalize(&texture);if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object"));puts("STORAGE_CLASS_FINALIZED_OBJECT_REJECTED_OK");}
   else puts("STORAGE_CLASS_TEXTURE_SNAPSHOT_OK");
 }
 botw_graphics_shutdown();munmap(builder,page);free(memory);return 0;
}
