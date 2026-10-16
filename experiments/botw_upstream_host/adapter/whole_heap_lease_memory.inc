/* Beside original g_map; no additional address table. The normal_pin field
 * distinguishes unchanged-permission heap owners from transfer/move borrows. */
#include "whole_heap_lease_internal.h"
static int whole_heap_problem(int code,char *error,size_t capacity,const char *text){
    if(error&&capacity)snprintf(error,capacity,"normal heap lease: %s",text);errno=code;return -1;
}
int botw_whole_heap_pin_overlap(uint64_t source,size_t size){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);int overlap=0;
    for(int i=0;i<g_nmap;i++)if(g_map[i].normal_pin&&whole_range_overlap(source,size,g_map[i].src,g_map[i].size)){overlap=1;break;}
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return overlap;
}
static int whole_heap_admit_locked(uint64_t source,size_t size,char *error,size_t capacity){
    if(!g_heap||!whole_range_contains((uintptr_t)g_heap,g_heap_cap,source,size))
        return whole_heap_problem(ENXIO,error,capacity,"source is outside the one actual process heap");
    if(!whole_range_contains((uintptr_t)g_heap,g_heap_cur,source,size))
        return whole_heap_problem(EFAULT,error,capacity,"complete host-page envelope is not committed");
    for(int i=0;i<g_nmap;i++){
        if(!whole_range_overlap(source,size,g_map[i].src,g_map[i].size)&&
           !whole_range_overlap(source,size,g_map[i].base,g_map[i].size))continue;
        if(!g_map[i].normal_pin||!g_map[i].transfer||g_map[i].transfer->blocked||!g_map[i].transfer->published)
            return whole_heap_problem(EACCES,error,capacity,"source envelope has a transfer, move or quarantined borrow");
    }
    return 0;
}
int botw_whole_heap_memory_begin(uint64_t source,size_t size,
        botw_whole_transfer_object **out,char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int result=whole_heap_admit_locked(source,size,error,capacity);
    if(!result&&g_nmap>=MAX_MAP)result=whole_heap_problem(ENOMEM,error,capacity,"original mapping capacity exhausted");
    botw_whole_transfer_object *memory=NULL;
    if(!result){memory=calloc(1,sizeof *memory);if(!memory)result=whole_heap_problem(ENOMEM,error,capacity,"allocate actual kernel backing owner failed");}
    if(!result){
        *memory=(botw_whole_transfer_object){.source=source,.size=size,.refs=1,.permission=3,.published=1,
            .backing_id=(uintptr_t)&g_heap,.backing_offset=source-(uintptr_t)g_heap};
        g_map[g_nmap++]=(typeof(g_map[0])){.base=source,.size=size,.src=source,.state=5,.transfer=memory,.normal_pin=1};
        *out=memory;
    }
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return result;
}
int botw_whole_heap_memory_finish(botw_whole_transfer_object *memory,char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    int result=whole_heap_admit_locked(memory->source,memory->size,error,capacity);
    if(!result&&!botw_whole_transfer_vm_rw((void*)(uintptr_t)memory->source,memory->size))
        result=whole_heap_problem(EACCES,error,capacity,"source envelope is not physically RW after checked preparation");
    if(!result&&botw_whole_transfer_vm_alias(memory->source,memory->size,0,&memory->backing,error,capacity)){errno=EIO;result=-1;}
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return result;
}
int botw_whole_heap_memory_abort(botw_whole_transfer_object *memory,char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);
    memory->published=0;int result=whole_transfer_dispose_locked(memory,error,capacity);
    if(result)memory->blocked=1;
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return result;
}
int botw_whole_heap_memory_reap(char *error,size_t capacity){
    pthread_mutex_lock(&g_heap_resize_lock);pthread_mutex_lock(&g_map_lock);int result=0;
    for(int i=0;i<g_nmap;){
        botw_whole_transfer_object *memory=g_map[i].transfer;
        if(!g_map[i].normal_pin||!memory||memory->published){i++;continue;}
        if(whole_transfer_dispose_locked(memory,error,capacity)){result=-1;break;}
    }
    pthread_mutex_unlock(&g_map_lock);pthread_mutex_unlock(&g_heap_resize_lock);return result;
}
