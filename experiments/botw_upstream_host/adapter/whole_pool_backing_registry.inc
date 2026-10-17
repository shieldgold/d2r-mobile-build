/* Included once by original nvn_mtl.m. g_pools/g_keep remain its sole resource
 * catalog. The original vpool module indexes only logical mapping intervals.
 * Callers hold POOL_CAPTURE or drained POOL_MUTATION; never wait with g_mu held. */
#include <limits.h>
#include "whole_pool_policy.h"
static int pool_error(char *error,size_t capacity,const char *message){
    if(error&&capacity)snprintf(error,capacity,"%s",message);return 0;
}
static BotwWholePoolBacking *pool_backing(const void *owner){
    BotwWholePoolBacking *result=nil;pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner){result=g_pools[i].backing;break;}
    pthread_mutex_unlock(&g_mu);return result;
}
static int pool_bounds(uintptr_t address,uint64_t bytes,uintptr_t base,uint64_t size){
    return address>=base&&address-base<=size&&bytes<=size-(address-base);
}
static int pool_overlap(uintptr_t a,uint64_t n,uintptr_t b,uint64_t m){
    return n&&m&&(a<=b?b-a<n:a-b<m);
}
static BotwWholePoolBacking *pool_containing(uint64_t address,uint64_t length){
    BotwWholePoolBacking *backing=nil;pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(pool_bounds(address,length,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo)&&
        g_pools[i].backing.live&&!g_pools[i].backing.retiring){backing=g_pools[i].backing;break;}
    pthread_mutex_unlock(&g_mu);return backing;
}
int mtl_pool_known(const void *owner){return pool_backing(owner)!=nil;}
int mtl_pool_available(const void *owner,const void *memory,uint64_t size){
    if(size>UINTPTR_MAX-(uintptr_t)memory)return 0;
    int available=1;pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner||(memory&&pool_overlap((uintptr_t)memory,size,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo))){available=0;break;}
    pthread_mutex_unlock(&g_mu);return available;
}
int mtl_pool_prepare_typed(const void *owner,const void *device,void *memory,uint64_t size,unsigned flags,uint64_t generation,
        int(*logical_range)(const void*,size_t),int *anchored,char *error,size_t capacity){
    if(!anchored)return pool_error(error,capacity,"missing pool anchor output");
    *anchored=0;
    if(!owner||!botw_whole_pool_initialization_valid(flags,memory,size))return pool_error(error,capacity,"invalid pool storage/flags");
    mtl_init();
    if(!g_dev||!g_keep)return pool_error(error,capacity,"Metal pool catalog is not initialized");
    BotwWholePoolBacking *backing=[BotwWholePoolBacking new];
    if(!backing)return pool_error(error,capacity,"pool backing metadata allocation failed");
    backing.owner=owner;backing.device=device;backing.memory=memory;backing.size=size;backing.flags=flags;
    backing.kind=botw_whole_pool_kind_of(flags);backing.generation=generation;
    pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner||
        (memory&&pool_overlap((uintptr_t)memory,size,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo))){
        pthread_mutex_unlock(&g_mu);return pool_error(error,capacity,"pool overlaps live/unpublished backing or object");
    }
    if(g_npool==g_cap){
        if(g_cap>INT_MAX/2){pthread_mutex_unlock(&g_mu);return pool_error(error,capacity,"pool catalog capacity overflow");}
        int capacity_next=g_cap?g_cap*2:64;
        if(capacity_next<g_cap||(size_t)capacity_next>SIZE_MAX/sizeof *g_pools){pthread_mutex_unlock(&g_mu);return 0;}
        pool_ent *next=realloc(g_pools,(size_t)capacity_next*sizeof *g_pools);
        if(!next){pthread_mutex_unlock(&g_mu);return pool_error(error,capacity,"pool catalog allocation failed");}
        g_pools=next;g_cap=capacity_next;
    }
    /* Anchor before any factory/reservation. Unpublished entries cannot resolve. */
    [g_keep addObject:backing];
    g_pools[g_npool++]=(pool_ent){.lo=(uintptr_t)memory,.hi=memory?(uintptr_t)memory+size:0,.key=owner,.backing=backing};
    *anchored=1;pthread_mutex_unlock(&g_mu);
    if(backing.kind==BOTW_WHOLE_POOL_VIRTUAL){
        uint64_t base=0;int status=nvn_vpool_virt_register_strict(owner,size,&base);
        if(status!=NVN_VPOOL_OK)return pool_error(error,capacity,"virtual pool reservation failed");
        backing.registered=YES;backing.gpuBase=base;backing.memory=(void*)(uintptr_t)base;
        pthread_mutex_lock(&g_mu);
        for(int i=0;i<g_npool;i++)if(g_pools[i].key!=owner&&pool_overlap(base,size,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo)){
            pthread_mutex_unlock(&g_mu);return pool_error(error,capacity,"virtual token overlaps existing pool metadata");
        }
        for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner){g_pools[i].lo=base;g_pools[i].hi=base+size;break;}
        pthread_mutex_unlock(&g_mu);
    }
    if(!botw_whole_pool_backing_create(backing,g_dev,logical_range,error,capacity))return 0;
    if(backing.kind==BOTW_WHOLE_POOL_PHYSICAL){
        if(nvn_vpool_phys_register(owner,memory,size)!=NVN_VPOOL_OK)return pool_error(error,capacity,"physical pool registration failed");
        backing.registered=YES;
    }
    return 1;
}
int mtl_pool_prepare(const void *owner,void *memory,uint64_t size,unsigned flags,uint64_t generation,
        int(*logical_range)(const void*,size_t),int *anchored,char *error,size_t capacity){
    /* Compatibility entry remains ordinary-only; typed production passes device. */
    if(botw_whole_pool_kind_of(flags)!=BOTW_WHOLE_POOL_ORDINARY){if(anchored)*anchored=0;return pool_error(error,capacity,"ordinary prepare requires ordinary flags");}
    return mtl_pool_prepare_typed(owner,NULL,memory,size,flags,generation,logical_range,anchored,error,capacity);
}
void *mtl_pool_logical_memory(const void *owner){
    BotwWholePoolBacking *backing=pool_backing(owner);return backing&&!backing.retiring?backing.memory:NULL;
}
uint64_t mtl_pool_buffer_address(const void *owner){
    BotwWholePoolBacking *backing=pool_backing(owner);
    return backing&&backing.live&&!backing.retiring&&backing.kind!=BOTW_WHOLE_POOL_PHYSICAL?(uintptr_t)backing.memory:0;
}
int mtl_pool_publish(const void *owner){
    BotwWholePoolBacking *backing=pool_backing(owner);
    if(!backing||backing.live||backing.retiring)return 0;
    pthread_mutex_lock(&g_mu);backing.live=YES;g_pool_adds++;g_reg_gen++;pthread_mutex_unlock(&g_mu);return 1;
}
static NSArray<BotwWholePoolBacking*> *pool_virtual_backings(void){
    NSMutableArray *owners=[NSMutableArray new];if(!owners)return nil;pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++){BotwWholePoolBacking *p=g_pools[i].backing;
        if(p.live&&!p.retiring&&p.kind==BOTW_WHOLE_POOL_VIRTUAL)[owners addObject:p];}
    pthread_mutex_unlock(&g_mu);return owners;
}
typedef struct { const void *physical;int found; } pool_physical_reference;
static int pool_find_physical_reference(const botw_whole_pool_identity *identity,void *context){
    pool_physical_reference *reference=context;
    if(identity->physical_owner==reference->physical)reference->found=1;
    return 1;
}
static int pool_remove_backing(const void *owner,int retire,char *error,size_t capacity){
    BotwWholePoolBacking *backing=pool_backing(owner);if(!backing)return 1;
    NSArray<BotwWholePoolBacking*> *affected=nil;
    if(retire&&backing.live&&backing.registered&&backing.kind==BOTW_WHOLE_POOL_PHYSICAL){
        NSMutableArray *references=[NSMutableArray new];
        NSArray *virtuals=pool_virtual_backings();
        if(!references||!virtuals)return pool_error(error,capacity,"physical retirement metadata allocation failed");
        for(BotwWholePoolBacking *v in virtuals){
            pool_physical_reference reference={.physical=owner};
            if(!mtl_pool_visit_virtual(v.owner,pool_find_physical_reference,&reference,error,capacity))return 0;
            if(reference.found)[references addObject:v];
        }
        affected=references;
        for(BotwWholePoolBacking *v in affected)if(!botw_whole_pool_map_preflight(v.owner,NULL,0,error,capacity))return 0;
    }
    /* Invalidate mapping dependencies before dropping borrowed Metal storage.
     * Virtual-owner caches may retain a physical buffer. Their already-approved
     * retirement must precede the actual deallocator ACK, never synthesize it. */
    if(backing.registered){
        int status=backing.kind==BOTW_WHOLE_POOL_VIRTUAL?nvn_vpool_virt_finalize(owner):nvn_vpool_phys_finalize(owner,NULL);
        if(status!=NVN_VPOOL_OK)return pool_error(error,capacity,"pool mapping retirement failed; owner retained");
        backing.registered=NO;
        pthread_mutex_lock(&g_mu);g_reg_gen++;pthread_mutex_unlock(&g_mu);
        for(BotwWholePoolBacking *v in affected)botw_whole_pool_map_committed(v.owner);
    }
    backing.retiring=YES;
    @autoreleasepool{
        if(retire)pool_retire_references(owner);
        if(backing.buffer)res_remove(backing.buffer);
        for(BotwWholePoolSegment *segment in backing.segments)if(segment.buffer&&segment.buffer!=backing.buffer)res_remove(segment.buffer);
        pthread_mutex_lock(&g_mu);
        for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner){g_pools[i].buf=nil;g_pools[i].gpu=0;g_pools[i].res=0;break;}
        pthread_mutex_unlock(&g_mu);botw_whole_pool_backing_drop(backing);
    }
    if(!botw_whole_pool_backing_ack(backing,error,capacity))return 0;
    pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner){g_pools[i]=g_pools[--g_npool];break;}
    [g_keep removeObjectIdenticalTo:backing];g_reg_gen++;
    pthread_mutex_unlock(&g_mu);return 1;
}
int mtl_pool_abort(const void *owner,char *error,size_t capacity){return pool_remove_backing(owner,0,error,capacity);}
int mtl_pool_retire(const void *owner,char *error,size_t capacity){return pool_remove_backing(owner,1,error,capacity);}
int mtl_pool_validate(const void *owner,const void *memory,uint64_t size,unsigned flags){
    BotwWholePoolBacking *backing=pool_backing(owner);
    return backing&&backing.live&&!backing.retiring&&backing.memory==memory&&backing.size==size&&backing.flags==flags;
}
int mtl_pool_texture_valid(const void *owner,uint64_t serial,const void *memory,uint64_t size){
    BotwWholePoolBacking *backing=pool_backing(owner);
    return backing&&backing.live&&!backing.retiring&&serial>backing.generation&&
        pool_bounds((uintptr_t)memory,size,(uintptr_t)backing.memory,backing.size);
}
int mtl_pool_flush(const void *owner,size_t offset,size_t size,int invalidate,char *error,size_t capacity){
    BotwWholePoolBacking *backing=pool_backing(owner);
    if(!backing)return pool_error(error,capacity,"unknown pool owner");
    return botw_whole_pool_backing_flush(backing,offset,size,invalidate,error,capacity);
}
int mtl_pool_map_virtual(const void *owner,int count,const NVNmappingRequest *owned,char *error,size_t capacity){
    BotwWholePoolBacking *target=pool_backing(owner);
    if(!target||!target.live||target.retiring||target.kind!=BOTW_WHOLE_POOL_VIRTUAL||!target.registered||
       count<0||count>BOTW_WHOLE_POOL_MAX_MAPPING_REQUESTS||(count&&!owned))return pool_error(error,capacity,"invalid virtual mapping target/batch");
    if(!count)return 1; /* No request access, generation change or cache callback. */
    for(int i=0;i<count;i++){
        BotwWholePoolBacking *source=owned[i].physicalPool?pool_backing(owned[i].physicalPool):nil;
        if(owned[i].physicalPool&&(!source||!source.live||source.retiring||!source.registered||
           !target.device||source.device!=target.device))return pool_error(error,capacity,"physical source is stale or belongs to another device");
        if(!botw_whole_pool_mapping_request_valid(&owned[i],target.size,source.size,source.flags))
            return pool_error(error,capacity,"unsupported or out-of-bounds mapping request");
    }
    if(!botw_whole_pool_map_preflight(owner,owned,count,error,capacity))return 0;
    if(nvn_vpool_map(owner,owned,count,error,capacity)!=NVN_VPOOL_OK)return 0;
    botw_whole_pool_map_committed(owner);
    pthread_mutex_lock(&g_mu);g_reg_gen++;pthread_mutex_unlock(&g_mu);return 1;
}
int mtl_pool_physical_identity(const void *owner,uint64_t offset,uint64_t length,botw_whole_pool_identity *out,char *error,size_t capacity){
    BotwWholePoolBacking *p=pool_backing(owner);
    if(!out||!length||!p||!p.live||p.retiring||!p.registered||p.kind!=BOTW_WHOLE_POOL_PHYSICAL||
       offset>p.size||length>p.size-offset)return pool_error(error,capacity,"physical identity is stale or outside backing");
    /* Existing immutable pool birth serial is the catalog's physical identity. */
    *out=(botw_whole_pool_identity){.logical_owner=owner,.physical_owner=owner,.canonical=(char*)p.memory+offset,
        .logical_address=(uintptr_t)p.memory+offset,.length=length,.physical_offset=offset,.physical_id=p.generation,.storage_class=0x100};return 1;
}
static int pool_identity_from_lease(BotwWholePoolBacking *logical,const nvn_vpool_lease *lease,botw_whole_pool_identity *out,char *error,size_t capacity){
    BotwWholePoolBacking *physical=pool_backing(lease->phys_owner);
    if(!logical||logical.kind!=BOTW_WHOLE_POOL_VIRTUAL||!logical.live||logical.retiring||!lease->contiguous||
       !physical||!physical.live||physical.retiring||physical.kind!=BOTW_WHOLE_POOL_PHYSICAL||!physical.registered||
       !pool_bounds((uintptr_t)lease->cpu,lease->len,(uintptr_t)physical.memory,physical.size))
        return pool_error(error,capacity,"virtual range is fragmented or has no live physical identity");
    *out=(botw_whole_pool_identity){.logical_owner=logical.owner,.physical_owner=physical.owner,.canonical=lease->cpu,
        .logical_address=lease->addr,.length=lease->len,.physical_offset=(uintptr_t)lease->cpu-(uintptr_t)physical.memory,
        .physical_id=physical.generation,.mapping_generation=lease->gen,.storage_class=0x100};return 1;
}
int mtl_pool_resolve_identity(uint64_t address,uint64_t length,botw_whole_pool_identity *out,char *error,size_t capacity){
    if(!out||!length||length>UINT64_MAX-address)return pool_error(error,capacity,"invalid identity range");
    nvn_vpool_lease lease={0};int status=nvn_vpool_lease_acquire(&lease,address,length);
    BotwWholePoolBacking *backing=pool_containing(address,length);botw_whole_pool_identity identity;
    if(status==NVN_VPOOL_OK){
        int ok=pool_identity_from_lease(backing,&lease,&identity,error,capacity);nvn_vpool_lease_release(&lease);
        if(ok)*out=identity;return ok;
    }
    if(status!=NVN_VPOOL_NOT_VIRTUAL)return pool_error(error,capacity,"virtual address is unmapped, stale or invalid");
    if(!backing||backing.kind!=BOTW_WHOLE_POOL_ORDINARY||(backing.flags&NVN_MEMORY_POOL_FLAGS_GPU_NO_ACCESS))
        return pool_error(error,capacity,"address has no GPU-visible ordinary backing");
    *out=(botw_whole_pool_identity){.logical_owner=backing.owner,.physical_owner=backing.owner,.canonical=(void*)(uintptr_t)address,
        .logical_address=address,.length=length,.physical_offset=address-(uintptr_t)backing.memory,
        .physical_id=backing.generation,.storage_class=0x100};return 1;
}
int mtl_pool_visit_virtual(const void *owner,int(*visit)(const botw_whole_pool_identity*,void*),void *context,char *error,size_t capacity){
    BotwWholePoolBacking *p=pool_backing(owner);
    if(!visit||!p||!p.live||p.retiring||p.kind!=BOTW_WHOLE_POOL_VIRTUAL||!p.registered)return pool_error(error,capacity,"invalid virtual visitor owner");
    if(p.size/NVN_VPOOL_PAGE>BOTW_WHOLE_POOL_MAX_VISIT_PAGES)return pool_error(error,capacity,"virtual authority metadata work exceeds bridge limit");
    /* Original mappings are 64KiB granular. A hole is skipped explicitly, never
     * mistaken for an empty whole pool; no interval algorithm is duplicated. */
    for(uint64_t offset=0;offset<p.size;offset+=NVN_VPOOL_PAGE){
        nvn_vpool_lease lease={0};int status=nvn_vpool_lease_acquire(&lease,p.gpuBase+offset,NVN_VPOOL_PAGE);
        if(status==NVN_VPOOL_UNMAPPED)continue;
        if(status!=NVN_VPOOL_OK)return pool_error(error,capacity,"virtual visitor found stale mapping metadata");
        botw_whole_pool_identity identity;int ok=pool_identity_from_lease(p,&lease,&identity,error,capacity);
        if(ok)ok=visit(&identity,context);nvn_vpool_lease_release(&lease);
        if(!ok)return pool_error(error,capacity,"virtual mapped span authority rejected");
    }
    return 1;
}
int mtl_pool_read_address(uint64_t address,void *output,size_t length,char *error,size_t capacity){
    if(!length)return 1;
    if(!output||length>UINT64_MAX-address)return pool_error(error,capacity,"invalid logical read range");
    nvn_vpool_lease lease={0};int status=nvn_vpool_lease_acquire(&lease,address,length);
    if(status==NVN_VPOOL_NOT_VIRTUAL)return botw_whole_pool_read(output,(void*)(uintptr_t)address,length)?1:pool_error(error,capacity,"normal source is not readable");
    if(status!=NVN_VPOOL_OK)return pool_error(error,capacity,"logical source is unmapped or stale");
    int ok=1;
    for(unsigned i=0;i<lease.nspans;i++){
        nvn_vpool_span span;
        if(nvn_vpool_lease_span(&lease,i,&span)!=NVN_VPOOL_OK||!botw_whole_pool_read((char*)output+span.off,span.cpu,span.len)){ok=0;break;}
    }
    nvn_vpool_lease_release(&lease);return ok?1:pool_error(error,capacity,"physical source is not readable");
}
static int pool_address(uint64_t address,uint64_t length,void **buffer,uint64_t *offset,uint64_t *gpu){
    botw_whole_pool_identity identity;char error[192];
    if(!buffer||!offset||!mtl_pool_resolve_identity(address,length,&identity,error,sizeof error))return 0;
    BotwWholePoolBacking *backing=pool_backing(identity.physical_owner);
    if(!backing||!botw_whole_pool_backing_ensure(backing,g_dev,error,sizeof error)){
        if(backing.retiring)MDIE("%s",error);return 0;
    }
    id<MTLBuffer> resolved=nil;uint64_t resolved_offset=0;
    if(backing.buffer){resolved=backing.buffer;resolved_offset=identity.physical_offset+backing.bufferOffset;}
    else for(BotwWholePoolSegment *segment in backing.segments)if(pool_bounds((uintptr_t)identity.canonical,length,(uintptr_t)segment.base,segment.length)){
        resolved=segment.buffer;resolved_offset=(uintptr_t)identity.canonical-(uintptr_t)segment.base;break;
    }
    if(!resolved||resolved_offset>resolved.length||length>resolved.length-resolved_offset)return 0;
    pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].backing==backing){
        /* Multi-leaf physical backing remains segmented; never advertise one
         * synthetic Metal buffer for the whole pool. */
        if(backing.buffer){g_pools[i].buf=backing.buffer;g_pools[i].base=g_pools[i].lo-backing.bufferOffset;g_pools[i].gpu=backing.buffer.gpuAddress;pool_res(i);}
        else{res_add(resolved);res_commit();g_pool_res_at=g_cb_commits+1;}
        *buffer=(__bridge void*)resolved;*offset=resolved_offset;if(gpu)*gpu=resolved.gpuAddress+resolved_offset;
        pthread_mutex_unlock(&g_mu);return 1;
    }
    pthread_mutex_unlock(&g_mu);return 0;
}
int mtl_addr(uint64_t address,uint64_t length,void **buffer,uint64_t *offset){return pool_address(address,length,buffer,offset,NULL);}
int mtl_gpu_addr(uint64_t address,uint64_t length,void **buffer,uint64_t *offset,uint64_t *gpu){return pool_address(address,length,buffer,offset,gpu);}
