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
static const void *unregisteredProvider;
static unsigned unregisteredProviderReads;
static int providerAccess(const void *address,size_t bytes,int writable){(void)bytes;(void)writable;if(address==unregisteredProvider){unregisteredProviderReads++;return 0;}return 1;}

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
 API(nvnTextureBuilderGetStorageAlignment);API(nvnTextureBuilderGetStorageSize);API(nvnTextureGetStorageSize);
 if(!strncmp(mode,"unsupported-",12)){
  const char *name=!strcmp(mode,"unsupported-raw-builder")?"nvnTextureBuilderGetRawStorageClass":"nvnTextureGetRawStorageClass";
  void (*unimplemented)(void)=(void(*)(void))botw_nvn_bootstrap_loader(name);CHECK(unimplemented);
  if(!sigsetjmp(recovery,1)){unimplemented();CHECK(0);}CHECK(strstr(botw_graphics_last_error(),name));puts("STORAGE_CLASS_OTHER_FAMILIES_REMAIN_UNSUPPORTED_OK");botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"invalid-map-pool")){API(nvnMemoryPoolMapVirtual);if(!sigsetjmp(recovery,1)){nvnMemoryPoolMapVirtual(NULL,0,NULL);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"provenance"));puts("STORAGE_CLASS_MAP_INVALID_POOL_REJECTED_OK");botw_graphics_shutdown();return 0;}
 size_t page=(size_t)getpagesize();NVNtextureBuilder *builder=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(builder!=MAP_FAILED);memset(builder,0xa5,page);
 nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);nvnTextureBuilderSetDefaults(builder);
 if(!strcmp(mode,"depth-query")||!strcmp(mode,"depth-query-readonly")||!strcmp(mode,"depth-alignment-only")||!strcmp(mode,"depth-size-only")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceGetInteger);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);
  int mapPage=0;nvnDeviceGetInteger(&device,(NVNdeviceInfo)0x3a,&mapPage);CHECK(mapPage==65536);const unsigned format[]={0x32,0x33,0x34,0x35,0x36},kind[]={1,0x46,0x7b,0x46,0xc3};unsigned cases=0;
  for(unsigned f=0;f<5;f++)for(unsigned bit=33;bit-->0;){
   unsigned flags=bit==32?0:1u<<bit;if(flags&(0x50|8))continue;nvnTextureBuilderSetFlags(builder,flags);nvnTextureBuilderSetFormat(builder,(NVNformat)format[f]);nvnTextureBuilderSetSamples(builder,0);
   unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ));
   if(!strcmp(mode,"depth-alignment-only")){size_t observed=nvnTextureBuilderGetStorageAlignment(builder);if(!cases)fprintf(stderr,"DEPTH_ALIGNMENT observed=%zu expected=%d\n",observed,mapPage);CHECK(observed==(size_t)mapPage);}
   else if(!strcmp(mode,"depth-size-only")){size_t observed=nvnTextureBuilderGetStorageSize(builder);if(!cases)fprintf(stderr,"DEPTH_SIZE observed=%zu expected=%d\n",observed,mapPage);CHECK(observed==(size_t)mapPage);}
   else{CHECK(nvnTextureBuilderGetStorageClass(builder)==(int)(0x100|kind[f]));CHECK(nvnTextureBuilderGetStorageAlignment(builder)==(size_t)mapPage);CHECK(nvnTextureBuilderGetStorageSize(builder)==(size_t)mapPage);}CHECK(!memcmp(before,builder,sizeof before));
   if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ|PROT_WRITE));cases++;
  }
  for(size_t i=sizeof *builder;i<page;i++)CHECK(((unsigned char*)builder)[i]==0xa5);printf("STORAGE_DEPTH_QUERY_READONLY_IMMUTABLE_OK cases=%u\n",cases);munmap(builder,page);botw_graphics_shutdown();return 0;
 }

 if(!strcmp(mode,"compression-query")||!strcmp(mode,"compression-query-readonly")||!strcmp(mode,"compression-size-domain")||!strcmp(mode,"compression-gl-size")||!strcmp(mode,"compression-live-align")||!strcmp(mode,"compression-live-size")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnTextureBuilderGetFlags);API(nvnTextureBuilderSetGLTextureName);API(nvnTextureBuilderSetPackagedTextureData);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_R8);
  if(!strcmp(mode,"compression-live-align")||!strcmp(mode,"compression-live-size")){
   nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);
   if(!sigsetjmp(recovery,1)){if(!strcmp(mode,"compression-live-align"))nvnTextureBuilderGetStorageAlignment(builder);else nvnTextureBuilderGetStorageSize(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object lifetime/type"));puts("COMPRESSIBLE_COLOR_LIVE_QUERY_REQUIRED_OK");
  }else if(!strcmp(mode,"compression-gl-size")){
   nvnTextureBuilderSetGLTextureName(builder,42);nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);nvnTextureBuilderSetSize2D(builder,INT32_MAX,INT32_MAX);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);
   nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetPackagedTextureData(builder,(void*)(uintptr_t)0xdeadbeef000);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);nvnTextureBuilderSetGLTextureName(builder,0);CHECK(nvnTextureBuilderGetStorageSize(builder)==512);CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe&&nvnTextureBuilderGetStorageAlignment(builder)==512);puts("COMPRESSIBLE_COLOR_GL_EARLY_ZERO_OPAQUE_POINTER_OK");
  }else if(!strcmp(mode,"compression-size-domain")){
   const unsigned flags[]={0x8,0x9,0xc,0xd,0xa,0x28,0x108,0x208,0x80000008};const int samples[]={0,1,2,-1,8};
   for(unsigned i=0;i<sizeof flags/sizeof *flags;i++)for(unsigned j=0;j<sizeof samples/sizeof *samples;j++){
    nvnTextureBuilderSetFlags(builder,flags[i]);nvnTextureBuilderSetSamples(builder,samples[j]);size_t expected=(i<4&&!samples[j])?512:0;CHECK(nvnTextureBuilderGetStorageSize(builder)==expected);
    CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe&&nvnTextureBuilderGetStorageAlignment(builder)==512);
   }
   puts("COMPRESSIBLE_COLOR_SIZE_SCOPE_IS_NOT_CLASS_SCOPE_OK");
  }else{
   unsigned cases=0;for(unsigned f=0;f<sizeof ordinaryColorFormats/sizeof *ordinaryColorFormats;f++){
    unsigned format=ordinaryColorFormats[f];int eligible=(format<=9)||(format>=0xd&&format<=0x10)||(format>=0x39&&format<=0x3c)||(format>=0x41&&format<=0x50)||(format>=0x79&&format<=0x98);if(!eligible)continue;
    const unsigned flags[]={8,9,12,13};for(unsigned k=0;k<4;k++){
     nvnTextureBuilderSetFormat(builder,(NVNformat)format);nvnTextureBuilderSetFlags(builder,flags[k]);unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ));
     CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe&&nvnTextureBuilderGetStorageAlignment(builder)==512&&nvnTextureBuilderGetStorageSize(builder)==512&&(unsigned)nvnTextureBuilderGetFlags(builder)==flags[k]);CHECK(!memcmp(before,builder,sizeof before));if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ|PROT_WRITE));cases++;
    }
   }CHECK(cases==260);for(size_t i=sizeof *builder;i<page;i++)CHECK(((unsigned char*)builder)[i]==0xa5);puts("COMPRESSIBLE_COLOR_METADATA65_IMMUTABLE_OK cases=260");
  }
  munmap(builder,page);botw_graphics_shutdown();return 0;
 }

 if(!strncmp(mode,"plain-depth-",12)){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnTextureBuilderSetGLTextureName);API(nvnTextureBuilderSetPackagedTextureData);API(nvnTextureBuilderGetFlags);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);
  const unsigned formats[]={0x32,0x33,0x34,0x35,0x36},kinds[]={1,0x46,0x7b,0x46,0xc3};
  for(unsigned f=0;f<5;f++)for(unsigned fast=0;fast<2;fast++){
   unsigned flags=fast?0x108:8;nvnTextureBuilderSetFlags(builder,flags);nvnTextureBuilderSetFormat(builder,(NVNformat)formats[f]);nvnTextureBuilderSetSamples(builder,0);unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ));
   CHECK(nvnTextureBuilderGetStorageClass(builder)==(int)(0x100|kinds[f]));CHECK(nvnTextureBuilderGetStorageAlignment(builder)==65536);CHECK(nvnTextureBuilderGetStorageSize(builder)==65536);CHECK((unsigned)nvnTextureBuilderGetFlags(builder)==flags&&!memcmp(before,builder,sizeof before));if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ|PROT_WRITE));
  }
  nvnTextureBuilderSetGLTextureName(builder,42);nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetGLTextureName(builder,0);nvnTextureBuilderSetPackagedTextureData(builder,(void*)(uintptr_t)0xdeadbeef000);CHECK(nvnTextureBuilderGetStorageSize(builder)==65536);
  const unsigned noSize[]={0xa,0x28,0x208,0xc,0x9,0x80000008};for(unsigned i=0;i<sizeof noSize/sizeof *noSize;i++){nvnTextureBuilderSetFlags(builder,noSize[i]);CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1c3&&nvnTextureBuilderGetStorageAlignment(builder)==65536);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);}
  nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetSamples(builder,1);if(!sigsetjmp(recovery,1)){nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"ChooseKind"));puts("PLAIN_DEPTH_FIVE_METADATA_FLAGS_GL_AND_SIZE_SCOPE_OK");munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strncmp(mode,"policy-init-provider-",21)||!strncmp(mode,"depth-init-provider-",20)){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnTextureInitialize);NVNdeviceBuilder db;NVNdevice device,copy;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));memcpy(&copy,&device,sizeof copy);NVNdevice *inaccessible=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(inaccessible!=MAP_FAILED);unregisteredProvider=strstr(mode,"copy")?(const void*)&copy:strstr(mode,"inaccessible")?(const void*)inaccessible:(const void*)(uintptr_t)0xdeadbeef000;
  nvnTextureBuilderSetDevice(builder,(NVNdevice*)unregisteredProvider);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetFormat(builder,!strncmp(mode,"depth-init-",11)?NVN_FORMAT_DEPTH16:NVN_FORMAT_R32UI);NVNtexture texture;memset(&texture,0xa5,sizeof texture);unsigned char before[sizeof texture];memcpy(before,&texture,sizeof texture);botw_graphics_set_memory_access_validator(providerAccess);
  if(!sigsetjmp(recovery,1)){nvnTextureInitialize(&texture,builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object lifetime/type"));fprintf(stderr,"UNREGISTERED_PROVIDER_HEADER_READS observed=%u expected=0\n",unregisteredProviderReads);CHECK(!unregisteredProviderReads&&!memcmp(before,&texture,sizeof texture));botw_graphics_set_memory_access_validator(NULL);botw_graphics_shutdown();munmap(inaccessible,page);munmap(builder,page);puts("METAL_PLAIN_COLOR_INIT_REGISTRY_BEFORE_HEADER_OK");return 0;
 }
 if(!strncmp(mode,"policy-",7)&&strcmp(mode,"policy-create")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);API(nvnTextureBuilderGetFlags);API(nvnTextureBuilderSetGLTextureName);API(nvnTextureBuilderSetPackagedTextureData);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_R32UI);
  if(!strcmp(mode,"policy-gl")){
   nvnTextureBuilderSetGLTextureName(builder,42);nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);nvnTextureBuilderSetSize2D(builder,INT32_MAX,INT32_MAX);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetPackagedTextureData(builder,(void*)(uintptr_t)0xdeadbeef000);CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe&&nvnTextureBuilderGetStorageAlignment(builder)==512);nvnTextureBuilderSetGLTextureName(builder,0);CHECK(nvnTextureBuilderGetStorageSize(builder)==512);
  }else if(!strncmp(mode,"policy-live-",12)){
   nvnDeviceFinalize(&device);nvnTextureBuilderSetDevice(builder,&device);if(!sigsetjmp(recovery,1)){if(strstr(mode,"align"))nvnTextureBuilderGetStorageAlignment(builder);else if(strstr(mode,"size"))nvnTextureBuilderGetStorageSize(builder);else nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object lifetime/type"));
  }else if(!strcmp(mode,"policy-domain")){
   const unsigned flags[]={8,9,12,13,10,0x28,0x108,0x208,0x80000008};const int samples[]={0,1,2,-1,8};
   for(unsigned i=0;i<sizeof flags/sizeof *flags;i++)for(unsigned j=0;j<sizeof samples/sizeof *samples;j++){nvnTextureBuilderSetFlags(builder,flags[i]);nvnTextureBuilderSetSamples(builder,samples[j]);CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe&&nvnTextureBuilderGetStorageAlignment(builder)==512);CHECK(nvnTextureBuilderGetStorageSize(builder)==(size_t)((i<4&&!samples[j])?512:0));}
  }else{
   unsigned cases=0;const unsigned flags[]={8,9,12,13};
   for(unsigned f=0;f<sizeof ordinaryColorFormats/sizeof *ordinaryColorFormats;f++)for(unsigned k=0;k<4;k++){
    unsigned format=ordinaryColorFormats[f];nvnTextureBuilderSetFormat(builder,(NVNformat)format);nvnTextureBuilderSetFlags(builder,flags[k]);unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ));
    if(!strcmp(mode,"policy-class")||strstr(mode,"readonly"))CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe);
    if(!strcmp(mode,"policy-align")||strstr(mode,"readonly"))CHECK(nvnTextureBuilderGetStorageAlignment(builder)==512);
    if(!strcmp(mode,"policy-size")||strstr(mode,"readonly"))CHECK(nvnTextureBuilderGetStorageSize(builder)==(size_t)(((format>=0x2e&&format<=0x30)||(format>=0x76&&format<=0x78))?1024:512));
    CHECK((unsigned)nvnTextureBuilderGetFlags(builder)==flags[k]&&!memcmp(before,builder,sizeof before));if(strstr(mode,"readonly"))CHECK(!mprotect(builder,page,PROT_READ|PROT_WRITE));cases++;
   }CHECK(cases==452);for(size_t i=sizeof *builder;i<page;i++)CHECK(((unsigned char*)builder)[i]==0xa5);
  }
  printf("METAL_PLAIN_COLOR_POLICY_%s_OK\n",mode);munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"policy-create")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);API(nvnTextureInitialize);API(nvnTextureFinalize);API(nvnTextureGetFlags);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));void *memory=mmap(NULL,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(memory!=MAP_FAILED);NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,0x22);nvnMemoryPoolBuilderSetStorage(&mb,memory,65536);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
  const unsigned formats[]={NVN_FORMAT_R32UI,NVN_FORMAT_RG32UI,NVN_FORMAT_RGBA32UI};for(unsigned i=0;i<3;i++){
   nvnTextureBuilderSetDefaults(builder);nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetFormat(builder,(NVNformat)formats[i]);nvnTextureBuilderSetStorage(builder,&pool,0);NVNtexture texture;CHECK(nvnTextureInitialize(&texture,builder));CHECK(nvnTextureGetStorageClass(&texture)==0x1fe&&nvnTextureGetFlags(&texture)==8&&nvnTextureGetStorageSize(&texture)==(i==2?1024:512));nvnTextureBuilderSetFlags(builder,0);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_DEPTH16);CHECK(nvnTextureGetStorageClass(&texture)==0x1fe&&nvnTextureGetFlags(&texture)==8);nvnTextureFinalize(&texture);
  }
  nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGB32F);CHECK(nvnTextureBuilderGetStorageClass(builder)==0x1fe);NVNtexture texture;memset(&texture,0xa5,sizeof texture);unsigned char before[sizeof texture];memcpy(before,&texture,sizeof texture);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(strstr(botw_graphics_last_error(),"Texture format")&&!memcmp(before,&texture,sizeof texture));botw_graphics_shutdown();CHECK(!munmap(memory,65536));munmap(builder,page);puts("METAL_PLAIN_COLOR_ORDINARY_SNAPSHOT_AND_UNSUPPORTED_FORMAT_OK");return 0;
 }
 if(!strcmp(mode,"raw46-create-context")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnTextureInitialize);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetStorage(builder,(NVNmemoryPool*)(uintptr_t)0xdeadbeef000,0);
  const unsigned formats[]={NVN_FORMAT_DEPTH24,NVN_FORMAT_DEPTH24_STENCIL8};for(unsigned f=0;f<2;f++)for(unsigned compressed=0;compressed<2;compressed++){
   nvnTextureBuilderSetFormat(builder,(NVNformat)formats[f]);nvnTextureBuilderSetFlags(builder,compressed?8:0);NVNtexture output;memset(&output,0xa5,sizeof output);unsigned char previous[sizeof output];memcpy(previous,&output,sizeof previous);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&output,builder));}CHECK(!memcmp(previous,&output,sizeof output));CHECK(strstr(botw_graphics_last_error(),compressed?"hardware compression":"Texture format"));char detail[80];snprintf(detail,sizeof detail,"target=1 flags=0x%x format=0x%x samples=0",compressed?8:0,formats[f]);CHECK(strstr(botw_graphics_last_error(),detail));
  }
  puts("RAW46_CREATION_STILL_REFUSED_WITH_NORMAL_BUILDER_SCALARS_OK");munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"compression-unknown-create")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);API(nvnTextureInitialize);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));void *memory=mmap(NULL,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(memory!=MAP_FAILED);NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,0x22);nvnMemoryPoolBuilderSetStorage(&mb,memory,65536);CHECK(nvnMemoryPoolInitialize(&pool,&mb));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFlags(builder,8);nvnTextureBuilderSetStorage(builder,&pool,0);
  const unsigned unknown[]={0,NVN_FORMAT_RGB8,NVN_FORMAT_R8_UI2F,0x9d,0xffffffff};
  for(unsigned i=0;i<sizeof unknown/sizeof *unknown;i++){nvnTextureBuilderSetFormat(builder,(NVNformat)unknown[i]);NVNtexture texture;memset(&texture,0xa5,sizeof texture);unsigned char before[sizeof texture];memcpy(before,&texture,sizeof texture);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(!memcmp(before,&texture,sizeof texture)&&strstr(botw_graphics_last_error(),"hardware compression"));}
  const unsigned formats[]={NVN_FORMAT_STENCIL8,NVN_FORMAT_DEPTH24,NVN_FORMAT_DEPTH24_STENCIL8,NVN_FORMAT_DEPTH32F_STENCIL8};
  for(unsigned i=0;i<sizeof formats/sizeof *formats;i++){nvnTextureBuilderSetFormat(builder,(NVNformat)formats[i]);NVNtexture texture;memset(&texture,0xa5,sizeof texture);unsigned char before[sizeof texture];memcpy(before,&texture,sizeof texture);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(!memcmp(before,&texture,sizeof texture)&&strstr(botw_graphics_last_error(),"hardware compression"));}
  const unsigned flags[]={8,0xa,0x28,0x108,0x208,0x80000008};for(unsigned i=0;i<6;i++){
   nvnTextureBuilderSetFormat(builder,NVN_FORMAT_R8);nvnTextureBuilderSetFlags(builder,flags[i]);nvnTextureBuilderSetSamples(builder,i?0:1);NVNtexture texture;memset(&texture,0xa5,sizeof texture);unsigned char before[sizeof texture];memcpy(before,&texture,sizeof texture);if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(!memcmp(before,&texture,sizeof texture)&&strstr(botw_graphics_last_error(),"hardware compression"));
  }
  botw_graphics_shutdown();CHECK(!munmap(memory,65536));munmap(builder,page);puts("COMPRESSIBLE_UNKNOWN_CREATION_REJECTED_BEFORE_PUBLICATION_OK");return 0;
 }
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
 if(!strncmp(mode,"builder-invalid-device-",23)||!strncmp(mode,"depth-invalid-device-",21)||!strncmp(mode,"compression-invalid-device-",27)||!strncmp(mode,"backend-invalid-device-",23)){
  const char *state=mode+(!strncmp(mode,"depth-",6)?21:!strncmp(mode,"compression-",12)?27:23);
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnDeviceFinalize);NVNdeviceBuilder db;NVNdevice device,original;memset(&device,0,sizeof device);NVNdevice *inaccessible=NULL;
  if(!strcmp(state,"finalized")){nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnDeviceFinalize(&device);}
  if(!strcmp(state,"copy")){nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&original,&db));memcpy(&device,&original,sizeof device);}
  if(!strcmp(state,"inaccessible")){inaccessible=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(inaccessible!=MAP_FAILED);}
  nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetFlags(builder,(!strncmp(mode,"compression-",12)||!strncmp(mode,"backend-",8))?8:0);nvnTextureBuilderSetFormat(builder,!strncmp(mode,"depth-",6)?NVN_FORMAT_DEPTH16:!strncmp(mode,"compression-",12)?NVN_FORMAT_R8:!strncmp(mode,"backend-",8)?NVN_FORMAT_R32UI:NVN_FORMAT_RGBA8);nvnTextureBuilderSetDevice(builder,!strcmp(state,"null")?NULL:!strcmp(state,"foreign")?(NVNdevice*)(uintptr_t)0xdeadbeef000:inaccessible?inaccessible:&device);
  if(!sigsetjmp(recovery,1)){nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object lifetime/type"));puts("STORAGE_CLASS_GENERIC_LIVE_DEVICE_REQUIRED_OK");if(inaccessible)munmap(inaccessible,page);munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"depth-import-metadata")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnTextureBuilderSetGLTextureName);API(nvnTextureBuilderSetPackagedTextureData);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetSize2D(builder,5,3);
  const unsigned formats[]={0x32,0x33,0x34,0x35,0x36},kinds[]={1,0x46,0x7b,0x46,0xc3};
  const void *opaque=(void*)(uintptr_t)0xdeadbeef000;
  for(unsigned i=0;i<5;i++){
   nvnTextureBuilderSetFormat(builder,(NVNformat)formats[i]);nvnTextureBuilderSetGLTextureName(builder,42);nvnTextureBuilderSetPackagedTextureData(builder,opaque);unsigned char before[sizeof *builder];memcpy(before,builder,sizeof before);
   CHECK(nvnTextureBuilderGetStorageClass(builder)==(int)(0x100|kinds[i])&&nvnTextureBuilderGetStorageAlignment(builder)==65536);size_t observed=nvnTextureBuilderGetStorageSize(builder);if(!i)fprintf(stderr,"DEPTH_GL_SIZE observed=%zu expected=0\n",observed);CHECK(observed==0&&!memcmp(before,builder,sizeof before));
   nvnTextureBuilderSetGLTextureName(builder,0);CHECK(nvnTextureBuilderGetStorageSize(builder)==65536);
  }
  nvnTextureBuilderSetGLTextureName(builder,42);nvnTextureBuilderSetDevice(builder,NULL);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);nvnTextureBuilderSetSize2D(builder,INT32_MAX,INT32_MAX);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);puts("STORAGE_DEPTH_GL_SIZE_ZERO_AND_OPAQUE_PACKAGED_METADATA_OK");munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"depth-size-cases")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnTextureBuilderSetDepth);API(nvnTextureBuilderSetLevels);NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));nvnTextureBuilderSetDevice(builder,&device);
  const unsigned formats[]={0x32,0x33,0x34,0x35,0x36},bpp[]={2,4,4,4,8};
  for(unsigned i=0;i<5;i++){
   nvnTextureBuilderSetFormat(builder,(NVNformat)formats[i]);nvnTextureBuilderSetFlags(builder,0);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetSize2D(builder,512,256);nvnTextureBuilderSetSamples(builder,0);
   CHECK(nvnTextureBuilderGetStorageSize(builder)==(size_t)512*256*bpp[i]);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D_ARRAY);nvnTextureBuilderSetDepth(builder,2);nvnTextureBuilderSetLevels(builder,2);
   CHECK(nvnTextureBuilderGetStorageSize(builder)==(size_t)512*256*bpp[i]*5/4*2);
   nvnTextureBuilderSetSize2D(builder,16384,16384);nvnTextureBuilderSetDepth(builder,2048);nvnTextureBuilderSetLevels(builder,1);CHECK(nvnTextureBuilderGetStorageSize(builder)>UINT32_MAX);
   nvnTextureBuilderSetSize2D(builder,INT32_MAX,1);CHECK(nvnTextureBuilderGetStorageSize(builder)==0);
   nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_3D);CHECK(nvnTextureBuilderGetStorageClass(builder)>0x100);nvnTextureBuilderSetFlags(builder,NVN_TEXTURE_FLAGS_LINEAR);nvnTextureBuilderSetDevice(builder,(NVNdevice*)(uintptr_t)0xdeadbeef000);CHECK(nvnTextureBuilderGetStorageClass(builder)==0x100);nvnTextureBuilderSetDevice(builder,&device);
  }
  puts("STORAGE_DEPTH_SIZE_GEOMETRY_AND_WIDE_BUILDER_OK");munmap(builder,page);botw_graphics_shutdown();return 0;
 }
 if(!strcmp(mode,"depth-snapshot")||!strcmp(mode,"depth-fastclear-create")||!strcmp(mode,"depth-combined-create")||!strcmp(mode,"depth-stale")||!strcmp(mode,"depth-storage-bounds")){
  API(nvnDeviceBuilderSetDefaults);API(nvnDeviceInitialize);API(nvnMemoryPoolBuilderSetDefaults);API(nvnMemoryPoolBuilderSetDevice);API(nvnMemoryPoolBuilderSetStorage);API(nvnMemoryPoolBuilderSetFlags);API(nvnMemoryPoolInitialize);API(nvnMemoryPoolFinalize);API(nvnTextureInitialize);API(nvnTextureFinalize);
  NVNdeviceBuilder db;NVNdevice device;nvnDeviceBuilderSetDefaults(&db);CHECK(nvnDeviceInitialize(&device,&db));size_t bytes=131072;void *memory=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(memory!=MAP_FAILED);
  NVNmemoryPoolBuilder mb;NVNmemoryPool pool;nvnMemoryPoolBuilderSetDefaults(&mb);nvnMemoryPoolBuilderSetDevice(&mb,&device);nvnMemoryPoolBuilderSetFlags(&mb,0x22);nvnMemoryPoolBuilderSetStorage(&mb,memory,bytes);CHECK(nvnMemoryPoolInitialize(&pool,&mb));
  nvnTextureBuilderSetDefaults(builder);nvnTextureBuilderSetDevice(builder,&device);nvnTextureBuilderSetStorage(builder,&pool,0);nvnTextureBuilderSetSize2D(builder,5,3);nvnTextureBuilderSetFormat(builder,!strcmp(mode,"depth-combined-create")?NVN_FORMAT_DEPTH24_STENCIL8:NVN_FORMAT_DEPTH16);
  if(!strcmp(mode,"depth-fastclear-create"))nvnTextureBuilderSetFlags(builder,NVN_TEXTURE_FLAGS_DEPTH16_PREFER_FAST_CLEAR);
  CHECK(nvnTextureBuilderGetStorageClass(builder)==(!strcmp(mode,"depth-combined-create")?0x146:0x101));CHECK(nvnTextureBuilderGetStorageSize(builder)==65536);NVNtexture texture;memset(&texture,0xa5,sizeof texture);unsigned char before[sizeof texture];memcpy(before,&texture,sizeof texture);
  if(!strcmp(mode,"depth-storage-bounds")){
   nvnMemoryPoolFinalize(&pool);nvnMemoryPoolBuilderSetStorage(&mb,memory,page*2);CHECK(page*2<65536&&nvnMemoryPoolInitialize(&pool,&mb));
   if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(strstr(botw_graphics_last_error(),"Texture storage bounds")&&!memcmp(before,&texture,sizeof texture));
   nvnMemoryPoolFinalize(&pool);nvnMemoryPoolBuilderSetStorage(&mb,memory,bytes);CHECK(nvnMemoryPoolInitialize(&pool,&mb));nvnTextureBuilderSetStorage(builder,&pool,8192);
   if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(strstr(botw_graphics_last_error(),"depth storage offset")&&!memcmp(before,&texture,sizeof texture));
   nvnTextureBuilderSetStorage(builder,&pool,65536);CHECK(nvnTextureInitialize(&texture,builder)&&nvnTextureGetStorageSize(&texture)==65536);puts("STORAGE_DEPTH_BOUNDS_AND_OFFSET_REFUSE_BEFORE_OBJECT_PUBLICATION_OK");
   botw_graphics_shutdown();CHECK(!munmap(memory,bytes));munmap(builder,page);return 0;
  }
  if(strstr(mode,"-create")){
   if(!sigsetjmp(recovery,1)){CHECK(!nvnTextureInitialize(&texture,builder));}CHECK(!memcmp(before,&texture,sizeof texture));CHECK(strstr(botw_graphics_last_error(),!strcmp(mode,"depth-fastclear-create")?"Unsupported texture":"conversion")||strstr(botw_graphics_last_error(),"Combined depth"));puts("STORAGE_DEPTH_METADATA_IS_NOT_GPU_CREATION_OK");
  }else{
   CHECK(nvnTextureInitialize(&texture,builder));CHECK(nvnTextureGetStorageClass(&texture)==0x101&&nvnTextureGetStorageSize(&texture)==65536);
   nvnTextureBuilderSetFlags(builder,NVN_TEXTURE_FLAGS_LINEAR);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_3D);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_RGBA8);CHECK(nvnTextureGetStorageClass(&texture)==0x101&&nvnTextureGetStorageSize(&texture)==65536);
   if(!strcmp(mode,"depth-stale")){nvnMemoryPoolFinalize(&pool);CHECK(nvnMemoryPoolInitialize(&pool,&mb));if(!sigsetjmp(recovery,1)){nvnTextureGetStorageSize(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture pool"));}
   else{nvnTextureFinalize(&texture);nvnTextureBuilderSetFlags(builder,0);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_DEPTH32F);CHECK(nvnTextureInitialize(&texture,builder));CHECK(nvnTextureGetStorageClass(&texture)==0x17b&&nvnTextureGetStorageSize(&texture)==65536);}
   puts("STORAGE_DEPTH_SNAPSHOT_AND_LIFETIME_OK");
  }
  botw_graphics_shutdown();CHECK(!munmap(memory,bytes));munmap(builder,page);return 0;
 }
 if(!strcmp(mode,"builder-block")||!strcmp(mode,"builder-rejected-formats")){
  const unsigned formats[]={NVN_FORMAT_DEPTH16,NVN_FORMAT_DEPTH32F,NVN_FORMAT_DEPTH32F_STENCIL8,NVN_FORMAT_STENCIL8,NVN_FORMAT_DEPTH16,NVN_FORMAT_DEPTH24,NVN_FORMAT_DEPTH32F,NVN_FORMAT_DEPTH24_STENCIL8,NVN_FORMAT_DEPTH32F_STENCIL8,0,NVN_FORMAT_RGB8,NVN_FORMAT_RGB16I,NVN_FORMAT_R8_UI2F,NVN_FORMAT_RGB10A2_I2F,0x9d,0xa0,0xffffffff,NVN_FORMAT_DEPTH16,NVN_FORMAT_DEPTH32F_STENCIL8};unsigned cases=0;
  nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_2D);
  for(unsigned f=0;f<(!strcmp(mode,"builder-block")?1:sizeof formats/sizeof *formats);f++){
   unsigned flag=f<3?NVN_TEXTURE_FLAGS_COMPRESSIBLE:f>=17?(NVN_TEXTURE_FLAGS_COMPRESSIBLE|NVN_TEXTURE_FLAGS_DEPTH16_PREFER_FAST_CLEAR):0;int samples=(f<3||(f>=4&&f<=8)||f>=17)?1:0;nvnTextureBuilderSetSamples(builder,samples);nvnTextureBuilderSetFlags(builder,flag);nvnTextureBuilderSetFormat(builder,(NVNformat)formats[f]);
   if(!sigsetjmp(recovery,1)){nvnTextureBuilderGetStorageClass(builder);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"ChooseKind"));CHECK(strstr(botw_graphics_last_error(),"target=1"));char detail[64];snprintf(detail,sizeof detail,"flags=0x%x",flag);CHECK(strstr(botw_graphics_last_error(),detail));snprintf(detail,sizeof detail,"format=0x%x",formats[f]);CHECK(strstr(botw_graphics_last_error(),detail));snprintf(detail,sizeof detail,"samples=%d",samples);CHECK(strstr(botw_graphics_last_error(),detail));cases++;
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
  CHECK(nvnTextureInitialize(&texture,builder));CHECK(nvnTextureGetStorageSize(&texture)>0);
   int expected=generic?0x1fe:0x100;CHECK(nvnTextureBuilderGetStorageClass(builder)==expected&&nvnTextureGetStorageClass(&texture)==expected);nvnTextureBuilderSetFlags(builder,generic?NVN_TEXTURE_FLAGS_LINEAR:0);nvnTextureBuilderSetTarget(builder,NVN_TEXTURE_TARGET_3D);nvnTextureBuilderSetFormat(builder,NVN_FORMAT_DEPTH32F);CHECK(nvnTextureGetStorageClass(&texture)==expected);
   if(!strcmp(mode,"texture-stale")||!strcmp(mode,"generic-stale")){nvnMemoryPoolFinalize(&pool);CHECK(nvnMemoryPoolInitialize(&pool,&mb));if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture pool"));puts("STORAGE_CLASS_STALE_GENERATION_REJECTED_OK");}
   else if(!strcmp(mode,"device-stale")||!strcmp(mode,"generic-device-stale")){nvnDeviceFinalize(&device);if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"stale texture device"));puts("STORAGE_CLASS_STALE_DEVICE_REJECTED_OK");}
   else if(!strcmp(mode,"texture-finalized")||!strcmp(mode,"generic-finalized")){nvnTextureFinalize(&texture);if(!sigsetjmp(recovery,1)){nvnTextureGetStorageClass(&texture);CHECK(0);}CHECK(strstr(botw_graphics_last_error(),"object"));puts("STORAGE_CLASS_FINALIZED_OBJECT_REJECTED_OK");}
   else puts("STORAGE_CLASS_TEXTURE_SNAPSHOT_OK");
 }
 botw_graphics_shutdown();munmap(builder,page);free(memory);return 0;
}
