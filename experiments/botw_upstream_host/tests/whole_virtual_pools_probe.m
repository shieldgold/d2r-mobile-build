/* The original complete backing fixture supplies owned protocol objects.
 * No Metal device, GPU command, Guest or private virtual-pool hook is run. */
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include "nvn_vpool.h"
#include "whole_pool_policy.h"
#define WHOLE_POOL_OWNED_MAP_HOOKS 1
#define main ordinary_backing_fixture_main
#include "owned_backing_protocols.inc"
#undef main
/* Owned CPU Map transaction hooks only. Real shader/private authority guards
 * are independently exercised by the complete consumer fixture. */
static unsigned preflight_calls,committed_calls;
static int reject_preflight;
static void hook_scope(void) {
    assert(!pthread_mutex_trylock(&g_mu));pthread_mutex_unlock(&g_mu);
    assert(!pthread_mutex_trylock(&g_pool_gate.mutex));assert(g_pool_gate.writer);
    pthread_mutex_unlock(&g_pool_gate.mutex);
}
int botw_whole_pool_map_preflight(const void *owner,const NVNmappingRequest *requests,int count,char *error,size_t capacity) {
    assert(owner&&count>=0&&(!count||requests));hook_scope();preflight_calls++;
    if(reject_preflight){if(error&&capacity)snprintf(error,capacity,"owned CPU transaction preflight rejected");return 0;}
    return 1;
}
void botw_whole_pool_map_committed(const void *owner){assert(owner);hook_scope();committed_calls++;}
_Static_assert(__builtin_types_compatible_p(__typeof__(&h_nvnMemoryPoolInitialize),NVNT_nvnMemoryPoolInitialize),"actual Initialize ABI");
_Static_assert(__builtin_types_compatible_p(__typeof__(&h_nvnMemoryPoolFinalize),NVNT_nvnMemoryPoolFinalize),"actual Finalize ABI");
#if WHOLE_VIRTUAL_MAP_AVAILABLE
_Static_assert(__builtin_types_compatible_p(__typeof__(&h_nvnMemoryPoolMapVirtual),NVNT_nvnMemoryPoolMapVirtual),"actual MapVirtual ABI");
#endif
static void initialize_pool(NVNmemoryPool *pool,NVNdevice *device,unsigned flags,void *memory,size_t size) {
    memset(pool,0x5a,sizeof *pool);
    pool_builder_t builder={.dev=device,.mem=memory,.size=size,.flags=flags};
    assert(h_nvnMemoryPoolInitialize(pool,(NVNmemoryPoolBuilder*)&builder)==1);
    assert(((pool_t*)pool)->magic==MAGIC('P','O','O','L'));
    assert(h_nvnMemoryPoolGetSize(pool)==size);
}
static void assert_alias(uint64_t address,size_t offset,unsigned char value) {
    @autoreleasepool {
    void *buffer=NULL;uint64_t actual=UINT64_MAX;
    assert(mtl_addr(address,1,&buffer,&actual)==1&&buffer);
    id<MTLBuffer> object=(__bridge id<MTLBuffer>)buffer;
    assert(actual==offset&&((unsigned char*)object.contents)[actual]==value);
    }
}
static void assert_lease_status(uint64_t address,int expected) {
    nvn_vpool_lease lease={0};int status=nvn_vpool_lease_acquire(&lease,address,1);
    assert(status==expected&&!lease.held_);
}
int main(int argc,char **argv){@autoreleasepool{
    assert(argc==2);
    const size_t bytes=NVN_VPOOL_PAGE*2;owned_size=bytes*2;
    owned=mmap(NULL,owned_size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(owned!=MAP_FAILED);memset(owned,0x39,owned_size);
    OwnedDevice *device=[OwnedDevice new];device.maxBufferLength=owned_size*4;g_dev=(id<MTLDevice>)device;
    g_keep=[NSMutableArray new];g_res=(id<MTLResidencySet>)[OwnedResidency new];g_res_objects=[NSHashTable weakObjectsHashTable];
    NVNdevice raw_device={0};((dev_t_*)&raw_device)->magic=MAGIC('D','E','V',0);
    const unsigned physical_flags=NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS|NVN_MEMORY_POOL_FLAGS_PHYSICAL;
    const unsigned virtual_flags=NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS|NVN_MEMORY_POOL_FLAGS_GPU_CACHED|NVN_MEMORY_POOL_FLAGS_VIRTUAL;
    NVNmemoryPool physical,virtual_pool;int physical_live=0,virtual_live=0;
    if(!strcmp(argv[1],"physical")||!strcmp(argv[1],"virtual")) {
        int is_virtual=!strcmp(argv[1],"virtual");
        initialize_pool(&physical,&raw_device,is_virtual?virtual_flags:physical_flags,is_virtual?NULL:owned,bytes);
        if(is_virtual)assert(h_nvnMemoryPoolGetBufferAddress(&physical));
        else assert(!h_nvnMemoryPoolGetBufferAddress(&physical));
        h_nvnMemoryPoolFinalize(&physical);
    } else {
#if WHOLE_VIRTUAL_MAP_AVAILABLE
        initialize_pool(&physical,&raw_device,physical_flags,owned,bytes);physical_live=1;
        initialize_pool(&virtual_pool,&raw_device,virtual_flags,NULL,bytes);virtual_live=1;
        uint64_t address=h_nvnMemoryPoolGetBufferAddress(&virtual_pool);assert(address);
        ((unsigned char*)owned)[NVN_VPOOL_PAGE]=0x71;
        NVNmappingRequest requests[2]={{.physicalPool=&physical,.physicalOffset=0,.virtualOffset=0,.size=NVN_VPOOL_PAGE,.storageClass=0x100},
            {.physicalPool=&physical,.physicalOffset=NVN_VPOOL_PAGE,.virtualOffset=NVN_VPOOL_PAGE,.size=NVN_VPOOL_PAGE,.storageClass=0x100}};
        if(!strcmp(argv[1],"zero")) {
            size_t page=(size_t)getpagesize();void *unreadable=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(unreadable!=MAP_FAILED);
            uint64_t generation=nvn_vpool_generation();unsigned callbacks=preflight_calls+committed_calls;
            assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,0,unreadable)==1);
            assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,-1,unreadable)==0);
            assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,BOTW_WHOLE_POOL_MAX_MAPPING_REQUESTS+1,unreadable)==0);
            assert(generation==nvn_vpool_generation()&&callbacks==preflight_calls+committed_calls);assert(!munmap(unreadable,page));
        } else if(!strcmp(argv[1],"invalid-inputs")) {
            NVNdevice other={0};((dev_t_*)&other)->magic=MAGIC('D','E','V',0);NVNmemoryPool second;
            initialize_pool(&second,&other,physical_flags,(char*)owned+bytes,bytes);
            requests[0].physicalPool=&second;uint64_t generation=nvn_vpool_generation();
            assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,1,requests)==0&&nvn_vpool_generation()==generation);
            h_nvnMemoryPoolFinalize(&second);
            requests[0].physicalPool=&physical;
            unsigned bad[]={0x121,0x108,physical_flags|NVN_MEMORY_POOL_FLAGS_VIRTUAL};
            for(size_t i=0;i<sizeof bad/sizeof *bad;i++){
                assert(!botw_whole_pool_mapping_request_valid(&requests[0],bytes,bytes,bad[i]));
                NVNmemoryPool out;memset(&out,0xa5,sizeof out);NVNmemoryPool before=out;
                pool_builder_t builder={.dev=&raw_device,.mem=owned,.size=bytes,.flags=bad[i]};
                assert(h_nvnMemoryPoolInitialize(&out,(NVNmemoryPoolBuilder*)&builder)==0&&!memcmp(&out,&before,sizeof out));
            }
        } else {
            assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,2,requests)==1&&preflight_calls==1&&committed_calls==1);
            assert_alias(address,0,0x39);assert_alias(address+NVN_VPOOL_PAGE,NVN_VPOOL_PAGE,0x71);
            if(!strcmp(argv[1],"contiguous")) {
                botw_whole_pool_identity identity;char error[192];
                assert(mtl_pool_resolve_identity(address,bytes,&identity,error,sizeof error));
                assert(identity.physical_owner==&physical&&identity.canonical==owned&&identity.physical_offset==0);
                NVNmappingRequest before[2];memcpy(before,requests,sizeof before);
                assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,2,requests)==1&&!memcmp(before,requests,sizeof before));
            } else if(!strcmp(argv[1],"fragmented")) {
                requests[0].physicalOffset=NVN_VPOOL_PAGE;requests[1].physicalOffset=0;
                assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,2,requests)==1);
                assert_alias(address,NVN_VPOOL_PAGE,0x71);assert_alias(address+NVN_VPOOL_PAGE,0,0x39);
                void *buffer=NULL;uint64_t offset=0;assert(!mtl_addr(address,bytes,&buffer,&offset));
                unsigned char *copy=malloc(bytes);assert(copy);char error[192];
                assert(mtl_pool_read_address(address,copy,bytes,error,sizeof error));
                assert(copy[0]==0x71&&copy[NVN_VPOOL_PAGE]==0x39);free(copy);
            } else if(!strcmp(argv[1],"atomic")||!strcmp(argv[1],"preflight-denied")) {
                uint64_t generation=nvn_vpool_generation();unsigned commits=committed_calls;
                requests[0].physicalOffset=NVN_VPOOL_PAGE;
                if(!strcmp(argv[1],"atomic"))requests[1].size=0;else reject_preflight=1;
                NVNmappingRequest before[2];memcpy(before,requests,sizeof before);
                assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,2,requests)==0);
                assert(!memcmp(before,requests,sizeof before)&&generation==nvn_vpool_generation()&&commits==committed_calls);
                reject_preflight=0;assert_alias(address,0,0x39);
            } else if(!strcmp(argv[1],"unmap-stale")) {
                NVNmappingRequest unmap={.virtualOffset=0,.size=NVN_VPOOL_PAGE,.storageClass=(NVNstorageClass)0x7fffffff};
                assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,1,&unmap)==1);assert_lease_status(address,NVN_VPOOL_UNMAPPED);
                h_nvnMemoryPoolFinalize(&virtual_pool);virtual_live=0;assert_lease_status(address,NVN_VPOOL_STALE);
                void *buffer=NULL;uint64_t offset=0;assert(!mtl_addr(address,1,&buffer,&offset));
            } else if(!strcmp(argv[1],"unrelated-finalize")) {
                NVNmemoryPool unrelated;
                initialize_pool(&unrelated,&raw_device,physical_flags,(char*)owned+bytes,bytes);
                unsigned calls=preflight_calls,commits=committed_calls;reject_preflight=1;
                h_nvnMemoryPoolFinalize(&unrelated);
                assert(preflight_calls==calls&&committed_calls==commits);
                NVNmemoryPool before=physical;uint64_t generation=nvn_vpool_generation();int refused=0;
                @try{h_nvnMemoryPoolFinalize(&physical);}
                @catch(NSException *exception){(void)exception;refused=1;}
                assert(refused&&preflight_calls==calls+1&&committed_calls==commits);
                assert(!memcmp(&before,&physical,sizeof physical)&&generation==nvn_vpool_generation());
                assert_alias(address,0,0x39);
                reject_preflight=0;h_nvnMemoryPoolFinalize(&physical);physical_live=0;
                assert(committed_calls==commits+1);assert_lease_status(address,NVN_VPOOL_UNMAPPED);
            } else if(!strcmp(argv[1],"physical-finalize")) {
                botw_whole_pool_identity first,next;char error[192];
                assert(mtl_pool_resolve_identity(address,1,&first,error,sizeof error));
                h_nvnMemoryPoolFinalize(&physical);physical_live=0;assert_lease_status(address,NVN_VPOOL_UNMAPPED);
                initialize_pool(&physical,&raw_device,physical_flags,owned,bytes);physical_live=1;
                assert_lease_status(address,NVN_VPOOL_UNMAPPED);
                assert(h_nvnMemoryPoolMapVirtual(&virtual_pool,1,requests)==1);
                assert(mtl_pool_resolve_identity(address,1,&next,error,sizeof error)&&next.physical_id!=first.physical_id);
            } else if(!strcmp(argv[1],"physical-ack")) {
                @autoreleasepool{held=pool_backing(&physical).buffer;}
                assert(held&&acks==0);pthread_t writer;
                assert(!pthread_create(&writer,NULL,retire_worker,&physical));assert(await_flag(&retire_entered));
                for(int i=0;i<5;i++)pause_cpu();assert(!atomic_load(&writer_done)&&acks==0);
                assert_lease_status(address,NVN_VPOOL_UNMAPPED);
                held=nil;assert(!pthread_join(writer,NULL));assert(atomic_load(&writer_done)==1&&acks==1);physical_live=0;
            } else assert(!"unknown owned Map case");
        }
#else
        assert(!"actual MapVirtual entry is not implemented in this source closure");
#endif
        if(virtual_live)h_nvnMemoryPoolFinalize(&virtual_pool);
        if(physical_live)h_nvnMemoryPoolFinalize(&physical);
    }
    assert(!g_npool);assert(!munmap(owned,owned_size));
    puts("WHOLE_VIRTUAL_POOLS_CPU_OK");return 0;
}}
