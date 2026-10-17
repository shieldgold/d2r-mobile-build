/* Owned CPU resources only. Reuse the complete pool backing fixture and its
 * one actual registry. No Metal device or GPU submission is created. */
#define main owned_pool_backing_original_main
#include "whole_pool_backing_probe.m"
#undef main

extern NVNstorageClass owned_storage_builder(const NVNtextureBuilder *);
extern NVNstorageClass owned_storage_texture(const NVNtexture *);

int main(int argc,char **argv){@autoreleasepool{
    if(argc!=2)return 91;
    const char *mode=argv[1];
    if(setenv("DM_BOTW_POOL_POLICY","ordinary-pools-base-v0-v1",1))return 92;
    if(!strcmp(mode,"old-probe"))setenv("DM_BOTW_POOL_POLICY","ordinary-pools-1.9.0-probe-v1",1);
    if(!strcmp(mode,"unknown"))setenv("DM_BOTW_POOL_POLICY","ordinary-pools-base-v0-v2",1);
    if(!strcmp(mode,"missing"))unsetenv("DM_BOTW_POOL_POLICY");
    tex_builder_t texture_builder={0};
    if(!strcmp(mode,"builder-empty")||!strcmp(mode,"old-probe")||!strcmp(mode,"unknown")||!strcmp(mode,"missing")){
        tex_builder_t before=texture_builder;
        if(owned_storage_builder((const NVNtextureBuilder*)&texture_builder)!=0x100||memcmp(&before,&texture_builder,sizeof before))return 93;
        puts("WHOLE_STORAGE_CLASS_CPU_OK");return 0;
    }
    if(!strcmp(mode,"null-builder")){(void)owned_storage_builder(NULL);return 94;}
    if(!strcmp(mode,"null-texture")){(void)owned_storage_texture(NULL);return 94;}
    NVNtexture texture={0};tex_t *t=(tex_t*)&texture;
    if(!strcmp(mode,"uninitialized")){(void)owned_storage_texture(&texture);return 94;}
    t->magic=MAGIC('T','E','X',0);
    if(!strcmp(mode,"missing-pool")){(void)owned_storage_texture(&texture);return 94;}

    size_t page=(size_t)getpagesize();owned_size=page*2;
    owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(owned==MAP_FAILED)return 95;memset(owned,0x39,owned_size);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];
    NVNmemoryPool pool={0};NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    pool_builder_t builder={.dev=&raw_device,.mem=owned,.size=owned_size,.flags=0x21};
    if(!h_nvnMemoryPoolInitialize(&pool,(NVNmemoryPoolBuilder*)&builder)||g_npool!=1)return 96;
    texture_builder.pool=(pool_t*)&pool;t->pool=(pool_t*)&pool;t->mem=owned;t->L.size=64;t->serial=nvn_new_serial();
    if(!mtl_pool_texture_valid(&pool,t->serial,t->mem,t->L.size))return 97;
    if(!strcmp(mode,"physical"))((pool_t*)&pool)->flags|=NVN_MEMORY_POOL_FLAGS_PHYSICAL;
    if(!strcmp(mode,"virtual"))((pool_t*)&pool)->flags|=NVN_MEMORY_POOL_FLAGS_VIRTUAL;
    if(!strcmp(mode,"bad-pool"))((pool_t*)&pool)->magic=0;
    if(!strcmp(mode,"finalized"))h_nvnMemoryPoolFinalize(&pool);
    if(strcmp(mode,"live")){
        if(!strcmp(mode,"texture-physical"))((pool_t*)&pool)->flags|=NVN_MEMORY_POOL_FLAGS_PHYSICAL;
        if(!strcmp(mode,"texture-virtual"))((pool_t*)&pool)->flags|=NVN_MEMORY_POOL_FLAGS_VIRTUAL;
        if(!strcmp(mode,"texture-bad-pool"))((pool_t*)&pool)->magic=0;
        if(!strncmp(mode,"texture-",8)||!strcmp(mode,"finalized")) (void)owned_storage_texture(&texture);
        else (void)owned_storage_builder((const NVNtextureBuilder*)&texture_builder);
        return 98;
    }
    NVNtexture before_texture=texture;tex_builder_t before_builder=texture_builder;NVNmemoryPool before_pool=pool;
    for(int i=0;i<2;i++){
        if(owned_storage_builder((const NVNtextureBuilder*)&texture_builder)!=0x100||owned_storage_texture(&texture)!=0x100)return 99;
    }
    if(memcmp(&texture,&before_texture,sizeof texture)||memcmp(&texture_builder,&before_builder,sizeof before_builder)||memcmp(&pool,&before_pool,sizeof pool))return 100;
    h_nvnMemoryPoolFinalize(&pool);
    if(g_npool||dirty_live||shader_live)return 101;
    munmap(owned,owned_size);puts("WHOLE_STORAGE_CLASS_CPU_OK");return 0;
}}
