/* Owned CPU-only refusals: no GPU or Guest execution. */
#include "virtual_fixture.inc"
static unsigned descriptor_effects;
void mtl_set_tex_pool_n(int n){(void)n;descriptor_effects++;}
#include "nvn_cmd_audit.h"
#include "command_boundary_support.inc"
/* Only call-boundary spies: never record a command or dereference donated
 * storage. The endpoint, object types and statistics declarations are real. */
static unsigned command_audit_calls,command_segment_calls,host_segment_calls;
static const void *audit_object;static cmd_t *segment_object;
static const uint8_t *audit_base,*audit_end,*audit_jump;
static uint8_t *segment_base;static size_t segment_size;static int audit_host;
static uint8_t host_storage[128];
void cmda_add(const void *object,const uint8_t *base,const uint8_t *end,int host,const uint8_t *jump,const void *return_address){
    (void)return_address;assert(command_audit_calls==command_segment_calls);
    command_audit_calls++;audit_object=object;audit_base=base;audit_end=end;audit_host=host;audit_jump=jump;
}
static uint8_t *hostseg_take(size_t need,size_t *size){
    assert(!need&&size);host_segment_calls++;*size=sizeof host_storage;return host_storage;
}
static void cmd_new_segment(cmd_t *object,uint8_t *memory,size_t size){
    assert(command_audit_calls==command_segment_calls+1);
    command_segment_calls++;segment_object=object;segment_base=memory;segment_size=size;
}
#include "cpu_boundary_entries.inc"
_Static_assert(__builtin_types_compatible_p(__typeof__(&h_nvnCommandBufferAddCommandMemory),NVNT_nvnCommandBufferAddCommandMemory),"actual AddCommandMemory ABI");
int main(int argc,char **argv){@autoreleasepool{
    assert(argc==2);const char *mode=argv[1];
    owned_size=NVN_VPOOL_PAGE*2;
    owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(owned!=MAP_FAILED);memset(owned,0x39,owned_size);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    unsigned flags=NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED;
    void *memory=owned;
    if(!strncmp(mode,"virtual-",8)){flags=NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS|NVN_MEMORY_POOL_FLAGS_GPU_CACHED|NVN_MEMORY_POOL_FLAGS_VIRTUAL;memory=NULL;}
    else if(!strncmp(mode,"physical-",9))flags=NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS|NVN_MEMORY_POOL_FLAGS_PHYSICAL;
    NVNmemoryPool pool;initialize_pool(&pool,&raw_device,flags,memory,owned_size);
    g_cs.on=0; /* The existing optional command-statistics path stays disabled. */
    int ordinary=!strcmp(mode,"ordinary");
    if(ordinary||strstr(mode,"descriptors")){
        NVNtexturePool texture,saved_texture;memset(&texture,0x6b,sizeof texture);saved_texture=texture;
        NVNsamplerPool sampler,saved_sampler;memset(&sampler,0x7c,sizeof sampler);saved_sampler=sampler;
        assert(h_nvnTexturePoolInitialize(&texture,&pool,16,2)==ordinary);
        assert(h_nvnSamplerPoolInitialize(&sampler,&pool,16,2)==ordinary);
        assert(descriptor_effects==(unsigned)ordinary);
        if(!ordinary){assert(!memcmp(&texture,&saved_texture,sizeof texture));assert(!memcmp(&sampler,&saved_sampler,sizeof sampler));}
    }
    if(ordinary||strstr(mode,"event")){
        NVNevent event,saved_event;memset(&event,0x8d,sizeof event);saved_event=event;
        ev_builder_t builder={.pool=(const pool_t*)&pool,.off=16};
        assert(h_nvnEventInitialize(&event,(const NVNeventBuilder*)&builder)==ordinary);
        if(ordinary)assert(((ev_t*)&event)->val==(volatile uint32_t*)((char*)owned+16));
        else assert(!memcmp(&event,&saved_event,sizeof event));
    }
    if(strstr(mode,"texture")){
#ifndef WHOLE_VIRTUAL_CPU_BASELINE
        tex_t texture={.pool=(pool_t*)&pool,.mem=((pool_t*)&pool)->mem};int stopped=0;
        @try{pool_texture_require_cpu(&texture);}@catch(NSException *exception){(void)exception;stopped=1;}
        assert(stopped&&"unsupported CPU texture source must stop before access");
#else
        assert(0&&"baseline has no CPU texture gate");
#endif
    }
    if(strstr(mode,"command")){
        NVNcommandBuffer command;memset(&command,0xa7,sizeof command);
        CB(&command)->recording=1;CB(&command)->cur=(uint8_t*)owned+24;
        NVNcommandBuffer saved_command=command;
        if(!strcmp(mode,"virtual-command")||!strcmp(mode,"physical-command")){
            int stopped=0;
            @try{h_nvnCommandBufferAddCommandMemory(&command,&pool,32,96);}
            @catch(NSException *exception){(void)exception;stopped=1;}
            assert(stopped&&"unsupported command memory must stop before its consumers");
            assert(!command_audit_calls&&!command_segment_calls&&!host_segment_calls);
        }else if(!strcmp(mode,"ordinary-command")){
            h_nvnCommandBufferAddCommandMemory(&command,&pool,32,96);
            assert(command_audit_calls==1&&command_segment_calls==1&&!host_segment_calls);
            assert(audit_object==&command&&segment_object==CB(&command)&&!audit_host);
            assert(audit_base==(uint8_t*)owned+32&&audit_end==audit_base+96);
            assert(segment_base==audit_base&&segment_size==96&&audit_jump==(uint8_t*)owned+24);
        }else{
            assert(!strcmp(mode,"fallback-command"));
            h_nvnCommandBufferAddCommandMemory(&command,NULL,0,128);
            assert(command_audit_calls==1&&command_segment_calls==1&&host_segment_calls==1&&audit_host==1);
            assert(audit_base==host_storage&&audit_end==host_storage+sizeof host_storage);
            assert(segment_base==host_storage&&segment_size==sizeof host_storage);
            size_t page=(size_t)getpagesize();void *unreadable=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(unreadable!=MAP_FAILED);
            h_nvnCommandBufferAddCommandMemory(&command,unreadable,0,63);
            assert(command_audit_calls==2&&command_segment_calls==2&&host_segment_calls==2&&audit_host==1);
            assert(audit_object==&command&&segment_object==CB(&command)&&audit_jump==(uint8_t*)owned+24);
            assert(segment_base==host_storage&&segment_size==sizeof host_storage);assert(!munmap(unreadable,page));
        }
        assert(!memcmp(&command,&saved_command,sizeof command));
    }
    h_nvnMemoryPoolFinalize(&pool);assert(!g_npool);assert(!munmap(owned,owned_size));
    puts("WHOLE_VIRTUAL_CPU_BOUNDARIES_OK");return 0;
}}
