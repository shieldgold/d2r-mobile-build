/* Included once beside the original g_map. It remains the only range catalog.
 * G (if needed) -> g_heap_resize_lock -> g_map_lock; never the reverse. */
#include "whole_thread_memory.h"
static int whole_range_contains(uint64_t base,uint64_t length,uint64_t address,size_t size){
    return address>=base&&size<=length&&address-base<=length-size;
}
static int whole_range_overlap(uint64_t a,uint64_t n,uint64_t b,uint64_t m){
    return n&&m&&(a<=b?b-a<n:a-b<m);
}
static int whole_transfer_problem(char *error,size_t capacity,const char *message){
    if(error&&capacity)snprintf(error,capacity,"transfer memory: %s",message);return -1;
}
static int whole_map_conflict_locked(uint64_t address,size_t size){
    for(int i=0;i<g_nmap;i++)if(whole_range_overlap(address,size,g_map[i].base,g_map[i].size)||
        whole_range_overlap(address,size,g_map[i].src,g_map[i].size))return 1;
    return 0;
}
static int whole_heap_shrink_conflict_locked(size_t size){
    uint64_t end=(uintptr_t)g_heap+size;
    for(int i=0;i<g_nmap;i++)if(whole_range_overlap(end,g_heap_cur-size,g_map[i].src,g_map[i].size))return 1;
    return 0;
}
static int whole_plain_heap_locked(uint64_t address,size_t size){
    return g_heap&&whole_range_contains((uintptr_t)g_heap,g_heap_cur,address,size)&&!whole_map_conflict_locked(address,size);
}
static int whole_logical_overlay_locked(uint64_t address,size_t size,int writable,int valid){
    for(int i=0;i<g_nmap;i++){
        typeof(g_map[0]) *map=&g_map[i];
        if(!map->transfer&&whole_range_contains(map->base,map->size,address,size))valid=1;
    }
    for(int i=0;i<g_nmap;i++){
        typeof(g_map[0]) *map=&g_map[i];
        if(!whole_range_overlap(address,size,map->src,map->size))continue;
        if(!map->transfer||map->transfer->blocked||!map->transfer->permission||
            (writable&&map->transfer->permission!=3))return 0;
    }
    return valid;
}
int dm_guest_heap_readable(uint64_t address,size_t size){
    if(!address||size>UINT64_MAX-address)return 0;
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int valid=g_heap&&whole_range_contains((uintptr_t)g_heap,g_heap_cur,address,size);
    valid=valid&&whole_logical_overlay_locked(address,size,0,valid);
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return valid;
}
int botw_whole_memory_classify(uint64_t address,size_t size,int writable){
    if(!address||size>UINT64_MAX-address||(writable!=0&&writable!=1))return -1;
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int valid=g_heap&&whole_range_contains((uintptr_t)g_heap,g_heap_cur,address,size);
    int managed=g_heap&&whole_range_overlap(address,size,(uintptr_t)g_heap,g_heap_cap);
    for(int i=0;!managed&&i<g_nmap;i++)managed=
        whole_range_overlap(address,size,g_map[i].base,g_map[i].size)||
        whole_range_overlap(address,size,g_map[i].src,g_map[i].size);
    valid=whole_logical_overlay_locked(address,size,writable,valid);
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);
    return valid?1:managed?-1:0;
}
int botw_whole_memory_access(uint64_t address,size_t size,int writable){
    return botw_whole_memory_classify(address,size,writable)==1;
}
int dm_guest_memory_access(uint64_t address,size_t size,int writable){
    if(!address||size>UINT64_MAX-address||(writable!=0&&writable!=1))return 0;
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int valid=g_heap&&whole_range_contains((uintptr_t)g_heap,g_heap_cur,address,size);
    for(int i=0;!valid&&i<g_nmod;i++){
        module_t *m=&g_mod[i];
        if(!whole_range_contains((uintptr_t)m->base,m->size,address,size))continue;
        uint64_t offset=address-(uintptr_t)m->base,rx=(m->text_end+0x3fffu)&~UINT64_C(0x3fff);
        valid=!writable||(offset>=rx&&offset<=m->patch_off&&size<=m->patch_off-offset&&
            !whole_range_overlap(offset,size,m->key_page_off,m->key_page_size));
    }
    /* Normal calling-thread ownership metadata, never a VM/address scan. */
    uint64_t top=(uintptr_t)pthread_get_stackaddr_np(pthread_self());size_t stack_size=pthread_get_stacksize_np(pthread_self());
    if(top>=stack_size&&whole_range_contains(top-stack_size,stack_size,address,size))valid=1;
    if(botw_whole_current_thread_memory(address,size))valid=1;
    valid=whole_logical_overlay_locked(address,size,writable,valid);
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return valid;
}
static int whole_transfer_dispose_locked(botw_whole_transfer_object *memory,char *error,size_t capacity){
    int row=-1;for(int i=0;i<g_nmap;i++)if(g_map[i].transfer==memory){row=i;break;}
    if(row<0)return whole_transfer_problem(error,capacity,"backing has no owned mapping record");
    if(memory->owner_applied&&mprotect((void*)(uintptr_t)memory->source,memory->size,PROT_READ|PROT_WRITE))
        return whole_transfer_problem(error,capacity,"source permission restore failed; owner retained");
    if(memory->backing&&munmap(memory->backing,memory->size)){
        int protection=memory->permission?PROT_READ|(memory->permission==3?PROT_WRITE:0):PROT_NONE;
        if(memory->owner_applied&&mprotect((void*)(uintptr_t)memory->source,memory->size,protection))memory->blocked=1;
        return whole_transfer_problem(error,capacity,"alias release failed; owner retained");
    }
    g_map[row]=g_map[--g_nmap];free(memory);return 0;
}
int botw_whole_transfer_dispose(botw_whole_transfer_object *memory,char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int result=whole_transfer_dispose_locked(memory,error,capacity);
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return result;
}
int botw_whole_transfer_pending_cleanup(char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int result=0;
    for(int i=0;i<g_nmap;){
        botw_whole_transfer_object *memory=g_map[i].transfer;
        if(!memory||memory->published){i++;continue;}
        if(whole_transfer_dispose_locked(memory,error,capacity)){result=-1;break;}
    }
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return result;
}
int botw_whole_transfer_prepare(uint64_t source,size_t size,unsigned permission,
        botw_whole_transfer_object **out,uint32_t *result,char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int status=0;botw_whole_transfer_object *memory=NULL;*result=0;
    if(!whole_plain_heap_locked(source,size)){*result=0xd401;goto done;}
    int page=getpagesize();
    if(page<=0||((unsigned)page&((unsigned)page-1))||((source|size)&((unsigned)page-1))){
        *result=0xd401;goto done; /* Exact host-page permission granularity. */
    }
    if(g_nmap==MAX_MAP){*result=0xce01;goto done;}
    if(!botw_whole_transfer_vm_rw((void*)(uintptr_t)source,size)){
        status=whole_transfer_problem(error,capacity,"source is not actually readable and writable");goto done;
    }
    memory=calloc(1,sizeof *memory);
    if(!memory){*result=0xd001;goto done;}
    *memory=(botw_whole_transfer_object){.source=source,.size=size,.refs=1,.permission=permission,
        .backing_id=(uintptr_t)&g_heap,.backing_offset=source-(uintptr_t)g_heap};
    g_map[g_nmap++]=(typeof(g_map[0])){.base=source,.size=size,.src=source,.state=5,.transfer=memory};
    if(botw_whole_transfer_vm_alias(source,size,0,&memory->backing,error,capacity)){
        if(whole_transfer_dispose_locked(memory,error,capacity))memory->blocked=1;
        status=-1;goto done;
    }
    int protection=permission?PROT_READ|(permission==3?PROT_WRITE:0):PROT_NONE;
    if(mprotect((void*)(uintptr_t)source,size,protection)){
        if(whole_transfer_dispose_locked(memory,error,capacity))memory->blocked=1;
        status=whole_transfer_problem(error,capacity,"source permission change failed");goto done;
    }
    memory->owner_applied=1;*out=memory;
done:
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return status;
}
/* Query boundaries are trimmed at both ends of every existing map/borrow. */
static int whole_query_overlay_locked(uint64_t address,uint64_t *base,uint64_t *size,uint64_t *state,uint64_t *attribute,uint64_t *permission){
    for(int i=0;i<g_nmap;i++){
        typeof(g_map[0]) *map=&g_map[i];
        if(whole_range_contains(map->src,map->size,address,1)){
            *base=map->src;*size=map->size;*state=5;*attribute=1;
            *permission=map->transfer&&!map->transfer->blocked?map->transfer->permission:0;return 1;
        }
        if(!map->transfer&&whole_range_contains(map->base,map->size,address,1)){
            *base=map->base;*size=map->size;*state=map->state;*attribute=0;*permission=3;return 1;
        }
    }
    return 0;
}
static void whole_query_trim_locked(uint64_t address,uint64_t *base,uint64_t *size){
    uint64_t end=*base+*size;
    for(int i=0;i<g_nmap;i++){
        uint64_t starts[2]={g_map[i].base,g_map[i].src};
        for(unsigned j=0;j<2;j++){
            uint64_t lo=starts[j],hi=lo+g_map[i].size;
            if(lo>address&&lo<end)end=lo;
            if(hi<=address&&hi>*base)*base=hi;
        }
    }
    *size=end-*base;
}
