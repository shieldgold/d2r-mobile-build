/* SPDX-License-Identifier: MIT */
#include "whole_heap_provider.h"
#include "whole_heap_lease_internal.h"
#include <stdatomic.h>
#include <limits.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
struct botw_whole_heap_provider {
    uint64_t process;
    _Atomic unsigned refs,active;
    _Atomic int closing,poisoned;
};
static int problem(char *error,size_t capacity,const char *text){
    if(error&&capacity)snprintf(error,capacity,"normal heap provider: %s",text);return -1;
}
static int retain(void *context){
    botw_whole_heap_provider *provider=context;
    if(!provider||atomic_load(&provider->closing)||atomic_load(&provider->poisoned))return -1;
    unsigned refs=atomic_load(&provider->refs);
    do{if(!refs||refs==UINT_MAX)return -1;}while(!atomic_compare_exchange_weak(&provider->refs,&refs,refs+1));
    if(atomic_load(&provider->closing)){atomic_fetch_sub(&provider->refs,1);return -1;}return 0;
}
static void release(void *context){
    botw_whole_heap_provider *provider=context;if(!provider)return;
    unsigned refs=atomic_load(&provider->refs);
    do{if(refs<=1){atomic_store(&provider->poisoned,1);return;}}while(!atomic_compare_exchange_weak(&provider->refs,&refs,refs-1));
}
static int32_t failure_status(int code){
    switch(code){
    case EINVAL:return BOTW_AUDREN_INVALID_ARGUMENT;
    case ENXIO:return BOTW_AUDREN_NOT_FOUND;
    case EACCES:return BOTW_AUDREN_PERMISSION_DENIED;
    case EFAULT:return BOTW_AUDREN_OUT_OF_BOUNDS;
    case EBUSY:return BOTW_AUDREN_OVERLAP;
    case ENOMEM:return BOTW_AUDREN_OUT_OF_MEMORY;
    default:return BOTW_AUDREN_SYSTEM_ERROR;
    }
}
static int32_t acquire(void *context,uint64_t process,uint64_t source,size_t size,uint32_t access,
        botw_audren_memory_lease *out,char *error,size_t capacity){
    botw_whole_heap_provider *provider=context;
    if(!provider||!out||out->owner||out->data||out->size||out->access||out->backing_id||out->backing_offset){
        problem(error,capacity,"acquire requires an empty exact lease output");return BOTW_AUDREN_INVALID_ARGUMENT;
    }
    if(process!=provider->process){problem(error,capacity,"actual process owner does not match");return BOTW_AUDREN_OWNER_MISMATCH;}
    if(atomic_load(&provider->closing)||atomic_load(&provider->poisoned)){
        problem(error,capacity,"context is closing or has an unbalanced callback");return BOTW_AUDREN_CLOSED;
    }
    unsigned active=atomic_load(&provider->active);
    do{if(active==UINT_MAX){problem(error,capacity,"lease capacity exhausted");return BOTW_AUDREN_OUT_OF_MEMORY;}}
    while(!atomic_compare_exchange_weak(&provider->active,&active,active+1));
    botw_whole_heap_lease *lease=NULL;botw_whole_heap_view view;
    if(botw_whole_heap_acquire_owned(source,size,access,provider,&lease,&view,error,capacity)){
        int code=errno;atomic_fetch_sub(&provider->active,1);return failure_status(code);
    }
    *out=(botw_audren_memory_lease){lease,view.data,view.size,view.access,view.backing_id,view.backing_offset};return BOTW_AUDREN_OK;
}
static int32_t release_lease(void *context,void **owner,char *error,size_t capacity){
    botw_whole_heap_provider *provider=context;
    if(!provider||!owner||!*owner){problem(error,capacity,"release requires a real owner");return BOTW_AUDREN_INVALID_ARGUMENT;}
    if(!atomic_load(&provider->active)){problem(error,capacity,"context owns no active lease");return BOTW_AUDREN_OWNER_MISMATCH;}
    botw_whole_heap_lease *lease=*owner;
    if(botw_whole_heap_release_owned(&lease,provider,error,capacity))return errno==EXDEV?BOTW_AUDREN_OWNER_MISMATCH:BOTW_AUDREN_SYSTEM_ERROR;
    *owner=NULL;atomic_fetch_sub(&provider->active,1);
    return BOTW_AUDREN_OK;
}
int botw_whole_heap_provider_create(uint64_t process,botw_whole_heap_provider **out,
        botw_audren_memory_provider *api,char *error,size_t capacity){
    if(!process||!out||*out||!api)return problem(error,capacity,"create requires verified process and fresh outputs");
    botw_whole_heap_provider *provider=calloc(1,sizeof *provider);
    if(!provider)return problem(error,capacity,"context allocation failed");
    provider->process=process;atomic_init(&provider->refs,1);atomic_init(&provider->active,0);
    atomic_init(&provider->closing,0);atomic_init(&provider->poisoned,0);
    *api=(botw_audren_memory_provider){BOTW_AUDREN_MEMORY_PROVIDER_ABI,sizeof *api,provider,retain,release,acquire,release_lease};
    *out=provider;return 0;
}
int botw_whole_heap_provider_destroy(botw_whole_heap_provider **owner,char *error,size_t capacity){
    if(!owner||!*owner)return problem(error,capacity,"destroy owner is absent");
    botw_whole_heap_provider *provider=*owner;int expected=0;
    if(!atomic_compare_exchange_strong(&provider->closing,&expected,1))return problem(error,capacity,"destroy already in progress");
    if(atomic_load(&provider->poisoned)||atomic_load(&provider->refs)!=1||atomic_load(&provider->active)){
        atomic_store(&provider->closing,0);return problem(error,capacity,"actual context references or lease owners remain");
    }
    if(botw_whole_heap_reap_pending(error,capacity)){atomic_store(&provider->closing,0);return -1;}
    free(provider);*owner=NULL;return 0;
}
