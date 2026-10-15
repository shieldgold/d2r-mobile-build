/* Synthetic base-v0 storage metadata. No game code, hardware kind emulation,
 * virtual mappings, or device/driver queries are performed by these getters. */
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
int main(int argc,char **argv){
 const char *mode=argc>1?argv[1]:"builder";
 if(sigsetjmp(recovery,1)){fprintf(stderr,"UNEXPECTED:%s\n",botw_graphics_last_error());return 1;}
 if(!botw_graphics_init(NULL,rejected,NULL))return 77;
 API(nvnTextureBuilderGetStorageClass);API(nvnTextureGetStorageClass);API(nvnTextureBuilderSetDefaults);API(nvnTextureBuilderSetDevice);API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetFlags);API(nvnTextureBuilderSetFormat);API(nvnTextureBuilderSetSamples);API(nvnTextureBuilderSetStorage);API(nvnTextureBuilderSetSize2D);API(nvnTextureBuilderSetStride);
 if(!strncmp(mode,"unsupported-",12)){
  const char *name=!strcmp(mode,"unsupported-raw-builder")?"nvnTextureBuilderGetRawStorageClass":!strcmp(mode,"unsupported-raw-texture")?"nvnTextureGetRawStorageClass":"nvnMemoryPoolMapVirtual";
  void (*unimplemented)(void)=(void(*)(void))botw_nvn_bootstrap_loader(name);CHECK(unimplemented);
  if(!sigsetjmp(recovery,1)){unimplemented();CHECK(0);}CHECK(strstr(botw_graphics_last_error(),name));puts("STORAGE_CLASS_OTHER_FAMILIES_REMAIN_UNSUPPORTED_OK");botw_graphics_shutdown();return 0;
 }
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
 if(!strcmp(mode,"builder-block")){
  nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetFlags(builder,0);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGBA8);
  if(!sigsetjmp(recovery,1)){nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"ChooseKind"));CHECK(strstr(botw_graphics_last_error(),"target=1"));CHECK(strstr(botw_graphics_last_error(),"flags=0x0"));CHECK(strstr(botw_graphics_last_error(),"format=0x25"));puts("STORAGE_CLASS_NATIVE_KIND_REQUIRED_REJECTED_OK");munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);API(nvnTextureInitialize);API(nvnTextureFinalize);
 NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));void *memory=NULL;CHECK(!posix_memalign(&memory,page,page));memset(memory,0,page);
 NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);nvnMemoryPoolBuilderSetStorage(&mb,memory,page);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
 nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetDefaults(builder);nvnTextureBuilderSetTarget(builder,!strcmp(mode,"buffer-create")?NVN_TEXTURE_TARGET_BUFFER:NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(builder,&pool,0);
 nvnTextureBuilderSetFlags(builder,!strcmp(mode,"texture-private")?0:!strcmp(mode,"texture-linear")?NVN_TEXTURE_FLAGS_LINEAR:!strcmp(mode,"texture-both-flags")?0x50:NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);nvnTextureBuilderSetStride(builder,128);
 NVNtexture texture;
 if(!strcmp(mode,"buffer-create")){CHECK(nvnTextureBuilderGetStorageClass(builder)==0x100);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(strstr(botw_graphics_last_error(),"Unsupported texture"));puts("STORAGE_CLASS_BUFFER_METADATA_IS_NOT_GPU_CREATION_OK");}
 else{
  CHECK(nvnTextureInitialize(&texture,builder));
  if(!strcmp(mode,"texture-private")){if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"ChooseKind"));puts("STORAGE_CLASS_NATIVE_KIND_REQUIRED_REJECTED_OK");}
  else{
   CHECK(nvnTextureBuilderGetStorageClass(builder)==0x100&&nvnTextureGetStorageClass(&texture)==0x100);nvnTextureBuilderSetFlags(builder,0);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_3D);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_DEPTH32F);CHECK(nvnTextureGetStorageClass(&texture)==0x100);
   if(!strcmp(mode,"texture-stale")){nvnMemoryPoolFinalize(&pool);CHECK(nvnMemoryPoolInitialize(&pool,&mb));if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture pool"));puts("STORAGE_CLASS_STALE_GENERATION_REJECTED_OK");}
   else if(!strcmp(mode,"device-stale")){nvnDeviceFinalize(&device);if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture device"));puts("STORAGE_CLASS_STALE_DEVICE_REJECTED_OK");}
   else if(!strcmp(mode,"texture-finalized")){nvnTextureFinalize(&texture);if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object"));puts("STORAGE_CLASS_FINALIZED_OBJECT_REJECTED_OK");}
   else puts("STORAGE_CLASS_TEXTURE_SNAPSHOT_OK");
  }
 }
 botw_graphics_shutdown();munmap(builder,page);free(memory);return 0;
}
