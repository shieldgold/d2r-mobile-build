/* In original hle_thread.c: G -> dirty slot -> heap -> map. Preparation runs
 * after releasing every lock; the real preparing owner pins its g_map entry. */
#include "whole_heap_lease_internal.h"
struct botw_whole_heap_lease {botw_whole_transfer_object *memory;const void *producer;};
static int whole_heap_handle_problem(int code,char *error,size_t capacity,const char *text){
    errno=code;return whole_kernel_problem(error,capacity,text);
}
int botw_whole_heap_acquire_owned(uint64_t source,size_t size,uint32_t access,const void *producer,
        botw_whole_heap_lease **out,botw_whole_heap_view *view,char *error,size_t capacity){
    if(!out||*out||!view||access!=3)return whole_heap_handle_problem(EINVAL,error,capacity,"normal heap acquire requires empty owner, exact output and RW access");
    uint64_t base;size_t span,offset;
    if(botw_whole_heap_geometry(source,size,&base,&span,&offset,error,capacity))return -1;
    botw_whole_heap_lease *lease=calloc(1,sizeof *lease);
    if(!lease)return whole_heap_handle_problem(ENOMEM,error,capacity,"normal heap lease allocation failed");
    pthread_mutex_lock(&G);
    if(!botw_whole_dirty_untracked_begin(base,span)){
        pthread_mutex_unlock(&G);free(lease);
        return whole_heap_handle_problem(EBUSY,error,capacity,"normal writable alias overlaps actual dirty tracking");
    }
    botw_whole_transfer_object *memory=NULL;
    int result=botw_whole_heap_memory_begin(base,span,&memory,error,capacity);
    botw_whole_dirty_untracked_end();pthread_mutex_unlock(&G);
    if(result){int saved=errno;free(lease);errno=saved;return -1;}
    /* This successful internal producer reference prevents reap during callback. */
    result=botw_whole_heap_prepare_write(base,span,error,capacity);
    pthread_mutex_lock(&G);
    int excluded=botw_whole_dirty_untracked_begin(base,span);
    if(!excluded&&!result)result=whole_heap_handle_problem(EBUSY,error,capacity,"normal alias admission changed during checked preparation");
    if(!result)result=botw_whole_heap_memory_finish(memory,error,capacity);
    int saved=errno;
    if(result){if(botw_whole_heap_memory_abort(memory,error,capacity))saved=EIO;}
    else{
        lease->memory=memory;lease->producer=producer;
        *view=(botw_whole_heap_view){(unsigned char*)memory->backing+offset,size,3,memory->backing_id,memory->backing_offset+offset};
        *out=lease;
    }
    if(excluded)botw_whole_dirty_untracked_end();pthread_mutex_unlock(&G);
    if(result)free(lease);errno=saved;return result;
}
int botw_whole_heap_acquire(uint64_t source,size_t size,uint32_t access,
        botw_whole_heap_lease **out,botw_whole_heap_view *view,char *error,size_t capacity){
    return botw_whole_heap_acquire_owned(source,size,access,NULL,out,view,error,capacity);
}
int botw_whole_heap_release_owned(botw_whole_heap_lease **owner,const void *producer,char *error,size_t capacity){
    if(!owner||!*owner)return whole_heap_handle_problem(EINVAL,error,capacity,"normal heap lease owner is absent");
    pthread_mutex_lock(&G);botw_whole_heap_lease *lease=*owner;
    if(lease->producer!=producer){pthread_mutex_unlock(&G);return whole_heap_handle_problem(EXDEV,error,capacity,"normal heap lease producer does not match");}
    int result=whole_transfer_unref_locked(lease->memory,error,capacity);
    if(!result){free(lease);*owner=NULL;}
    pthread_mutex_unlock(&G);return result;
}
int botw_whole_heap_release(botw_whole_heap_lease **owner,char *error,size_t capacity){
    return botw_whole_heap_release_owned(owner,NULL,error,capacity);
}
int botw_whole_heap_reap_pending(char *error,size_t capacity){
    pthread_mutex_lock(&G);int result=botw_whole_heap_memory_reap(error,capacity);pthread_mutex_unlock(&G);return result;
}
