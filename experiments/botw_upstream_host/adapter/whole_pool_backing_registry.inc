/* Included once by original nvn_mtl.m. g_pools/g_keep remain its sole catalog.
 * The original g_mu protects catalog edits, never a factory, drain or ACK wait. */
#include <limits.h>
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
int mtl_pool_known(const void *owner){return pool_backing(owner)!=nil;}
int mtl_pool_available(const void *owner,const void *memory,uint64_t size){
    if(size>UINTPTR_MAX-(uintptr_t)memory)return 0;
    int available=1;pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner||pool_overlap((uintptr_t)memory,size,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo)){available=0;break;}
    pthread_mutex_unlock(&g_mu);return available;
}
int mtl_pool_prepare(const void *owner,void *memory,uint64_t size,unsigned flags,uint64_t generation,
        int(*logical_range)(const void*,size_t),int *anchored,char *error,size_t capacity){
    *anchored=0;
    mtl_init();
    if(!g_dev||!g_keep){snprintf(error,capacity,"Metal pool catalog is not initialized");return 0;}
    BotwWholePoolBacking *backing=[BotwWholePoolBacking new];
    if(!backing){snprintf(error,capacity,"pool backing metadata allocation failed");return 0;}
    backing.owner=owner;backing.memory=memory;backing.size=size;backing.flags=flags;backing.generation=generation;
    if(size>UINTPTR_MAX-(uintptr_t)memory){snprintf(error,capacity,"pool backing overflow");return 0;}
    pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner||
        pool_overlap((uintptr_t)memory,size,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo)){
        pthread_mutex_unlock(&g_mu);snprintf(error,capacity,"pool overlaps live/unpublished backing or object");return 0;
    }
    if(g_npool==g_cap){
        if(g_cap>INT_MAX/2){pthread_mutex_unlock(&g_mu);snprintf(error,capacity,"pool catalog capacity overflow");return 0;}
        int capacity_next=g_cap?g_cap*2:64;
        if(capacity_next<g_cap||(size_t)capacity_next>SIZE_MAX/sizeof *g_pools){pthread_mutex_unlock(&g_mu);return 0;}
        pool_ent *next=realloc(g_pools,(size_t)capacity_next*sizeof *g_pools);
        if(!next){pthread_mutex_unlock(&g_mu);snprintf(error,capacity,"pool catalog allocation failed");return 0;}
        g_pools=next;g_cap=capacity_next;
    }
    /* Strong anchoring precedes any factory. The unpublished entry is never
     * address-resolvable, and remains here if actual release ACK is missing. */
    [g_keep addObject:backing];
    g_pools[g_npool++]=(pool_ent){.lo=(uintptr_t)memory,.hi=(uintptr_t)memory+size,.key=owner,.backing=backing};
    *anchored=1;
    pthread_mutex_unlock(&g_mu);
    return botw_whole_pool_backing_create(backing,g_dev,logical_range,error,capacity);
}
int mtl_pool_publish(const void *owner){
    BotwWholePoolBacking *backing=pool_backing(owner);
    if(!backing||backing.live||backing.retiring)return 0;
    pthread_mutex_lock(&g_mu);backing.live=YES;g_pool_adds++;g_reg_gen++;pthread_mutex_unlock(&g_mu);return 1;
}
static int pool_remove_backing(const void *owner,int retire,char *error,size_t capacity){
    BotwWholePoolBacking *backing=pool_backing(owner);if(!backing)return 1;
    backing.retiring=YES;
    @autoreleasepool{
        if(retire)pool_retire_references(owner);
        if(backing.buffer)res_remove(backing.buffer);
        for(BotwWholePoolSegment *segment in backing.segments)if(segment.buffer&&segment.buffer!=backing.buffer)res_remove(segment.buffer);
        pthread_mutex_lock(&g_mu);
        for(int i=0;i<g_npool;i++)if(g_pools[i].key==owner){g_pools[i].buf=nil;g_pools[i].gpu=0;g_pools[i].res=0;break;}
        pthread_mutex_unlock(&g_mu);
        botw_whole_pool_backing_drop(backing);
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
    if(!backing){snprintf(error,capacity,"unknown pool owner");return 0;}
    return botw_whole_pool_backing_flush(backing,offset,size,invalidate,error,capacity);
}
static int pool_address(uint64_t address,uint64_t length,void **buffer,uint64_t *offset,uint64_t *gpu){
    BotwWholePoolBacking *backing=nil;
    pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(pool_bounds(address,length,g_pools[i].lo,g_pools[i].hi-g_pools[i].lo)&&
        g_pools[i].backing.live&&!g_pools[i].backing.retiring){backing=g_pools[i].backing;break;}
    pthread_mutex_unlock(&g_mu);
    if(!backing||(backing.flags&8))return 0;
    char error[192];if(!botw_whole_pool_backing_ensure(backing,g_dev,error,sizeof error)){
        if(backing.retiring)MDIE("%s",error);return 0;
    }
    pthread_mutex_lock(&g_mu);
    for(int i=0;i<g_npool;i++)if(g_pools[i].backing==backing){
        g_pools[i].buf=backing.buffer;g_pools[i].base=g_pools[i].lo-backing.bufferOffset;g_pools[i].gpu=backing.buffer.gpuAddress;
        pool_res(i);*buffer=(__bridge void*)backing.buffer;*offset=address-g_pools[i].base;
        if(gpu)*gpu=backing.buffer.gpuAddress+*offset;
        pthread_mutex_unlock(&g_mu);return 1;
    }
    pthread_mutex_unlock(&g_mu);return 0;
}
int mtl_addr(uint64_t address,uint64_t length,void **buffer,uint64_t *offset){return pool_address(address,length,buffer,offset,NULL);}
int mtl_gpu_addr(uint64_t address,uint64_t length,void **buffer,uint64_t *offset,uint64_t *gpu){return pool_address(address,length,buffer,offset,gpu);}
