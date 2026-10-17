/* Owned canonical consumer contracts; no Metal device or Guest is executed. */
#include "botw_shader.h"
#include "nvn_texown.h"
#define WHOLE_POOL_OWNED_MAP_HOOKS 1
#define main original_owned_pool_main
#include "owned_consumer_protocols.inc"
#undef main
#define PD(...) ((void)0)
#include "owned_counter_declarations.inc"
#ifndef WHOLE_VPOOL_CONSUMER_BASELINE
#include "pool_consumer_guard.inc"
#else
/* No production consumer hook exists in the baseline. These test-only hooks
 * let the new shared producer set up an owned virtual code source; only the
 * original complete shader snapshot is exercised by the baseline RED case. */
int botw_whole_pool_map_preflight(const void *o,const NVNmappingRequest *r,int n,char *e,size_t c){(void)o;(void)r;(void)n;(void)e;(void)c;return 1;}
void botw_whole_pool_map_committed(const void *o){(void)o;}
#endif
#include "owned_counter_replay.inc"
static void put32(void *raw,size_t offset,uint32_t value){memcpy((uint8_t*)raw+offset,&value,4);}
static void owned_control(uint8_t control[2000],uint8_t *code){
    memset(control,0,2000);memset(code,0,512);
    put32(control,0,0x98761234);put32(control,4,1);put32(control,8,8);put32(control,12,0x120);put32(control,16,11);
    put32(control,20,2000);put32(control,0x20,2000);put32(control,0x6f0,2000);
    put32(control,0x700,0x100);put32(control,0x704,0x200);put32(control,0x708,0x30);put32(control,0x714,0);
    put32(code,0,0x12345678);put32(code,0x30,1|(1<<10));
}
static void initialize(NVNmemoryPool *p,NVNmemoryPool *v,NVNdevice *device,unsigned virtual_extra,uint64_t physical_size){
    pool_builder_t builder={.dev=device,.mem=owned,.size=physical_size,.flags=0x10a};
    assert(h_nvnMemoryPoolInitialize(p,(NVNmemoryPoolBuilder*)&builder)==1);
    builder.mem=NULL;builder.size=owned_size;builder.flags=0x221|virtual_extra;
    assert(h_nvnMemoryPoolInitialize(v,(NVNmemoryPoolBuilder*)&builder)==1);
}
static int mapping(NVNmemoryPool *v,NVNmemoryPool *p,uint64_t poff,uint64_t voff,uint64_t length){
    NVNmappingRequest req={.physicalPool=p,.physicalOffset=poff,.virtualOffset=voff,.size=length,.storageClass=0x100};
    char error[192];POOL_MUTATION();return mtl_pool_map_virtual(v,1,&req,error,sizeof error);
}
static void snapshot_case(NVNmemoryPool *p,NVNmemoryPool *v,const char *mode){
    uint8_t control[2000];owned_control(control,owned);
    assert(mapping(v,p,0,0,NVN_VPOOL_PAGE)==1);
    uint64_t token=mtl_pool_buffer_address(v);NVNshaderData source={token,control},out={0};char error[192];
    POOL_CAPTURE();
    int ok=botw_shader_snapshot(&source,&out,error,sizeof error);
    assert(ok==1&&"complete shader snapshot must read actual mapped physical bytes, never the token");
    botw_shader_info info;assert(botw_shader_get_info(&out,&info)==1&&info.version==8&&info.code_size==512);
    assert(out.data!=token&&memcmp((void*)(uintptr_t)out.data,owned,512)==0);
#ifndef WHOLE_VPOOL_CONSUMER_BASELINE
    assert(!botw_shader_pool_can_remap(v,error,sizeof error));
    if(!strcmp(mode,"shader-dedup")){
        char candidate=0;assert(botw_shader_pool_add(&candidate,(void*)(uintptr_t)token,token,NVN_VPOOL_PAGE,error,sizeof error));
        NVNshaderData other={0};assert(botw_shader_snapshot(&source,&other,error,sizeof error));
        /* The source range candidate was newly added at the head. Interned
         * byte identity may be shared, but this owner still gains authority. */
        assert(other.data==out.data&&!botw_shader_pool_can_remap(&candidate,error,sizeof error));
        botw_shader_pool_remove(&candidate);
    }
#else
    (void)mode;
#endif
}
int main(int argc,char **argv){@autoreleasepool{
    assert(argc==2);owned_size=2*NVN_VPOOL_PAGE;owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(owned!=MAP_FAILED);memset(owned,0x39,owned_size);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=4*owned_size;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    NVNmemoryPool p={0},v={0};initialize(&p,&v,&raw_device,strncmp(argv[1],"shader",6)?0:0x40,(!strcmp(argv[1],"fragment")||!strcmp(argv[1],"cpu-ordinary")||!strcmp(argv[1],"samples-ordinary"))?NVN_VPOOL_PAGE:owned_size);
    if(!strncmp(argv[1],"shader",6)){snapshot_case(&p,&v,argv[1]);
#ifndef WHOLE_VPOOL_CONSUMER_BASELINE
        uint64_t generation=nvn_vpool_generation();assert(!mapping(&v,&p,NVN_VPOOL_PAGE,0,NVN_VPOOL_PAGE));assert(generation==nvn_vpool_generation());
#endif
    }
#ifndef WHOLE_VPOOL_CONSUMER_BASELINE
    else if(!strcmp(argv[1],"private-read")){
        uint8_t control[2000];owned_control(control,owned);char error[192];
        NVNmemoryPool other={0};pool_builder_t builder={.dev=&raw_device,.mem=NULL,.size=owned_size,.flags=0x221};
        assert(h_nvnMemoryPoolInitialize(&other,(NVNmemoryPoolBuilder*)&builder));
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE)&&mapping(&other,&p,0,0,NVN_VPOOL_PAGE));
        uint64_t token=mtl_pool_buffer_address(&other);
        assert(botw_shader_pool_add(&other,(void*)(uintptr_t)token,token,owned_size,error,sizeof error));
        /* Both aliases predate the private writer. The real ledger records
         * that physical CPU bytes can no longer establish source authority. */
        nvn_texown_claim(101,owned,512);
        NVNshaderData source={token,control},output={(NVNbufferAddress)0x1234,(void*)0x5678},before=output;
        {POOL_CAPTURE();assert(!botw_shader_snapshot(&source,&output,error,sizeof error));}
        assert(!memcmp(&output,&before,sizeof output));
        assert(botw_shader_pool_can_remap(&other,error,sizeof error));
        /* A real module writer detects leaked nested read leases. This is an
         * owned module-lifecycle check, not a bypass of production Map guards. */
        assert(nvn_vpool_virt_finalize(&other)==NVN_VPOOL_OK);
        assert(nvn_texown_find(owned,512,NULL));
    }else if(!strcmp(argv[1],"retired")||!strcmp(argv[1],"live")){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));
        nvn_texown_claim(79,owned+5,11);if(!strcmp(argv[1],"retired"))nvn_texown_retire(79);
        uint64_t generation=nvn_vpool_generation();size_t claims=nvn_texown_count(1)+nvn_texown_count(0);
        assert(!mapping(&v,&p,NVN_VPOOL_PAGE,0,NVN_VPOOL_PAGE));
        assert(generation==nvn_vpool_generation()&&claims==nvn_texown_count(1)+nvn_texown_count(0));
        nvn_vpool_lease lease={0};assert(nvn_vpool_lease_acquire(&lease,mtl_pool_buffer_address(&v),1)==NVN_VPOOL_OK&&lease.cpu==owned);nvn_vpool_lease_release(&lease);
    }else if(!strcmp(argv[1],"cpu-authority")||!strcmp(argv[1],"cpu-retired")||!strcmp(argv[1],"cpu-ordinary")){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));
        tex_t texture={.pool=(pool_t*)&v,.mem=(void*)(uintptr_t)mtl_pool_buffer_address(&v),.serial=103};texture.L.size=64;
        {POOL_CAPTURE();pool_texture_cpu_authority(&texture,pool_texture_canonical(&texture));}
        NVNmemoryPool ordinary={0};
        if(!strcmp(argv[1],"cpu-ordinary")){
            pool_builder_t builder={.dev=&raw_device,.mem=owned+NVN_VPOOL_PAGE,.size=NVN_VPOOL_PAGE,.flags=0x22};
            assert(h_nvnMemoryPoolInitialize(&ordinary,(NVNmemoryPoolBuilder*)&builder));
            texture.pool=(pool_t*)&ordinary;texture.mem=owned+NVN_VPOOL_PAGE;
        }
        nvn_texown_claim(texture.serial,texture.mem==owned+NVN_VPOOL_PAGE?texture.mem:owned,64);
        if(!strcmp(argv[1],"cpu-retired"))nvn_texown_retire(texture.serial);
        int rejected=0;
        @try{POOL_CAPTURE();
            /* Canonical identity remains usable by the earlier cache-hit path;
             * only an actual new CPU read must establish byte authority. */
            const uint8_t *canonical=pool_texture_canonical(&texture);
            assert(canonical==(!strcmp(argv[1],"cpu-ordinary")?owned+NVN_VPOOL_PAGE:owned));
            pool_texture_cpu_authority(&texture,canonical);
        }@catch(NSException *exception){(void)exception;rejected=1;}
        assert(rejected==(strcmp(argv[1],"cpu-ordinary")!=0));
        nvn_vpool_lease lease={0};assert(nvn_vpool_lease_acquire(&lease,mtl_pool_buffer_address(&v),1)==NVN_VPOOL_OK);nvn_vpool_lease_release(&lease);
    }else if(!strcmp(argv[1],"incoming")){
        nvn_texown_claim(81,owned+NVN_VPOOL_PAGE,32);nvn_texown_retire(81);
        uint64_t generation=nvn_vpool_generation();assert(!mapping(&v,&p,NVN_VPOOL_PAGE,0,NVN_VPOOL_PAGE));assert(generation==nvn_vpool_generation());
    }else if(!strcmp(argv[1],"partial-hole")){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));nvn_texown_claim(83,owned,1);nvn_texown_retire(83);
        char error[192];assert(!botw_whole_pool_map_preflight(&v,NULL,0,error,sizeof error));
    }else if(!strcmp(argv[1],"canonical")){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));tex_t texture={.pool=(pool_t*)&v,.mem=(void*)(uintptr_t)mtl_pool_buffer_address(&v),.serial=19};texture.L.size=64;
        {POOL_CAPTURE();assert(pool_texture_canonical(&texture)==owned);}
        assert(mapping(&v,&p,NVN_VPOOL_PAGE,0,NVN_VPOOL_PAGE));
        {POOL_CAPTURE();assert(pool_texture_canonical(&texture)==owned+NVN_VPOOL_PAGE);}
    }else if(!strcmp(argv[1],"fragment")){
        NVNmemoryPool q={0};pool_builder_t qb={.dev=&raw_device,.mem=owned+NVN_VPOOL_PAGE,.size=NVN_VPOOL_PAGE,.flags=0x10a};
        assert(h_nvnMemoryPoolInitialize(&q,(NVNmemoryPoolBuilder*)&qb));
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE)&&mapping(&v,&q,0,NVN_VPOOL_PAGE,NVN_VPOOL_PAGE));
        tex_t texture={.pool=(pool_t*)&v,.mem=(void*)(uintptr_t)mtl_pool_buffer_address(&v),.serial=21};texture.L.size=owned_size;
        int rejected=0;@try{POOL_CAPTURE();(void)pool_texture_canonical(&texture);}@catch(NSException *exception){(void)exception;rejected=1;}assert(rejected);
    }else if(!strcmp(argv[1],"hole")||!strcmp(argv[1],"stale")){
        uint8_t control[2000];owned_control(control,owned);uint64_t token=mtl_pool_buffer_address(&v);char error[192];
        assert(botw_shader_pool_add(&v,(void*)(uintptr_t)token,token,owned_size,error,sizeof error));
        if(!strcmp(argv[1],"stale")){h_nvnMemoryPoolFinalize(&v);assert(botw_shader_pool_add(&v,(void*)(uintptr_t)token,token,owned_size,error,sizeof error));}
        NVNshaderData source={token,control},output={(NVNbufferAddress)0x1234,(void*)0x5678},before=output;
        {POOL_CAPTURE();assert(!botw_shader_snapshot(&source,&output,error,sizeof error));}
        assert(!memcmp(&before,&output,sizeof output));assert(botw_shader_pool_can_remap(&v,error,sizeof error));
    }else if(!strncmp(argv[1],"samples-",8)){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));
        NVNmemoryPool ordinary={0};uint64_t address=mtl_pool_buffer_address(&v);
        int allowed=!strcmp(argv[1],"samples-ordinary");
        if(allowed){
            pool_builder_t builder={.dev=&raw_device,.mem=owned+NVN_VPOOL_PAGE,.size=NVN_VPOOL_PAGE,.flags=0x22};
            assert(h_nvnMemoryPoolInitialize(&ordinary,(NVNmemoryPoolBuilder*)&builder));address=(uintptr_t)builder.mem;
        }else if(!strcmp(argv[1],"samples-physical"))address=(uintptr_t)owned;
        uint64_t operands[2]={NVN_COUNTER_TYPE_SAMPLES_PASSED,address};g_vis_cur=19;int rejected=0;
        @try{POOL_CAPTURE();owned_replay_report(operands);}@catch(NSException *exception){(void)exception;rejected=1;}
        assert(rejected==!allowed);
        if(allowed){
            assert(g_npost==1&&g_vis_cur==-1&&g_post[0].kind==3&&g_post[0].addr==address&&((vis_list*)g_post[0].vis)->n==0);
            owned_complete_samples(g_post);uint64_t record[2];memcpy(record,(void*)(uintptr_t)address,sizeof record);
            assert(record[0]==0&&record[1]==711&&((const uint8_t*)(uintptr_t)address)[16]==0x39);
        }else assert(g_npost==0&&g_vis_cur==19);
        free(g_post);
    }else if(!strcmp(argv[1],"direct-virtual")){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));int rejected=0;
        @try{POOL_CAPTURE();pool_direct_cpu_only(mtl_pool_buffer_address(&v),4);}@catch(NSException *exception){(void)exception;rejected=1;}assert(rejected);
    }else if(!strcmp(argv[1],"direct-ordinary")){
        POOL_CAPTURE();pool_direct_cpu_only((uintptr_t)owned,16);
    }else if(!strcmp(argv[1],"nonauthority-cache")){
        assert(mapping(&v,&p,0,0,NVN_VPOOL_PAGE));
        OwnedTexture *cache=[OwnedTexture new];serial_put_pool(91,cache,&v);assert(g_by_serial.n==1);
        assert(mapping(&v,&p,NVN_VPOOL_PAGE,0,NVN_VPOOL_PAGE));assert(!g_by_serial.n);
    }else assert(0);
#endif
    /* Individual cases intentionally keep virtual reservations as original
     * permanent tombstones. The process owns all referenced backing to exit. */
    puts("WHOLE_VIRTUAL_CONSUMERS_OWNED_CPU_OK");return 0;
}}
