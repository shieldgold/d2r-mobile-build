/* SPDX-License-Identifier: MIT */
#include "whole_audren.h"
#include "whole_kernel.h"
#include "whole_transfer.h"
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if BOTW_AUDREN_HOST_ABI != 2
#error "whole audren binding requires the provenance-aware canonical C API ABI 2"
#endif

typedef struct whole_audren_owner whole_audren_owner;
struct botw_whole_audren {
    pthread_mutex_t lock;
    botw_whole_audren_configuration configuration;
    botw_audren_host *host;
    botw_services *services;
    whole_audren_owner *owners;
    unsigned calls, attaching, destroying;
    _Atomic int failure;
};
struct whole_audren_owner {
    botw_whole_audren *binding;
    whole_audren_owner *next;
    botw_audren_session *session;
    botw_whole_transfer_lease *transfer;
    botw_whole_transfer_view view;
    botw_whole_event_producer *producer;
    uint32_t write_event, read_event;
    unsigned busy, registered;
    _Atomic unsigned backing_borrows, event_borrows, callbacks;
    _Atomic int closing, callback_error;
};
static int problem(char *error,size_t capacity,const char *message) {
    if(error&&capacity)snprintf(error,capacity,"%s",message);return -1;
}
static void clear_error(char *error,size_t capacity){if(error&&capacity)error[0]=0;}
static botw_audren_result failed(int status,char *error,size_t capacity,const char *message) {
    problem(error,capacity,message);
    return (botw_audren_result){status,0,0};
}
/* Use only after an actual kernel callback, with a cleared caller buffer. */
static botw_audren_result kernel_failed(int status,char *error,size_t capacity,const char *message) {
    if(!error||!capacity||!error[0])problem(error,capacity,message);
    return (botw_audren_result){status,0,0};
}
static int control_ok(botw_audren_result result) {
    return !result.host_status&&!result.service_result_valid;
}
static void terminal(void *context,int32_t status) {
    botw_whole_audren *binding=context;int expected=0;
    int actual=status>0?status:BOTW_AUDREN_SYSTEM_ERROR;
    if(atomic_compare_exchange_strong(&binding->failure,&expected,actual))
        binding->configuration.host.failed(binding->configuration.host.failure_context,actual);
}
static int borrow(_Atomic unsigned *count,whole_audren_owner *owner) {
    if(atomic_load(&owner->closing))return -1;
    unsigned value=atomic_load(count);
    do {if(value==UINT_MAX)return -1;}while(!atomic_compare_exchange_weak(count,&value,value+1));
    return 0;
}
static void unborrow(_Atomic unsigned *count,whole_audren_owner *owner) {
    unsigned value=atomic_load(count);
    do {
        if(!value){atomic_store(&owner->callback_error,1);terminal(owner->binding,BOTW_AUDREN_SYSTEM_ERROR);return;}
    }while(!atomic_compare_exchange_weak(count,&value,value-1));
}
static int backing_retain(void *context) {
    whole_audren_owner *owner=context;return owner->transfer?borrow(&owner->backing_borrows,owner):-1;
}
static void backing_release(void *context) {
    whole_audren_owner *owner=context;unborrow(&owner->backing_borrows,owner);
}
static int event_retain(void *context) {
    whole_audren_owner *owner=context;return owner->producer?borrow(&owner->event_borrows,owner):-1;
}
static void event_release(void *context) {
    whole_audren_owner *owner=context;unborrow(&owner->event_borrows,owner);
}
static int event_signal(void *context) {
    whole_audren_owner *owner=context;atomic_fetch_add(&owner->callbacks,1);
    char error[128];int result=botw_whole_kernel_producer_signal(owner->producer,error,sizeof error);
    atomic_fetch_sub(&owner->callbacks,1);return result;
}
static int event_clear(void *context) {
    whole_audren_owner *owner=context;atomic_fetch_add(&owner->callbacks,1);
    char error[128];int result=botw_whole_kernel_producer_clear(owner->producer,error,sizeof error);
    atomic_fetch_sub(&owner->callbacks,1);return result;
}
static whole_audren_owner *find_owner(botw_whole_audren *binding,const void *pointer) {
    for(whole_audren_owner *owner=binding->owners;owner;owner=owner->next)if(owner==pointer)return owner;
    return NULL;
}
static int begin(botw_whole_audren *binding,void *pointer,int closing,
        whole_audren_owner **out,char *error,size_t capacity) {
    clear_error(error,capacity);
    pthread_mutex_lock(&binding->lock);
    whole_audren_owner *owner=pointer?find_owner(binding,pointer):NULL;
    int status=0;
    if((binding->destroying&&!closing)||!binding->host||binding->calls==UINT_MAX)
        status=BOTW_AUDREN_BUSY;
    else if(pointer&&(!owner||owner->busy))status=owner?BOTW_AUDREN_BUSY:BOTW_AUDREN_NOT_FOUND;
    else if(!closing&&atomic_load(&binding->failure))status=atomic_load(&binding->failure);
    else if(owner&&!closing&&(!owner->session||atomic_load(&owner->closing)))status=BOTW_AUDREN_CLOSED;
    if(!status){binding->calls++;if(owner)owner->busy=1;if(out)*out=owner;}
    pthread_mutex_unlock(&binding->lock);
    if(status)problem(error,capacity,"audren binding/owner is unavailable, closed or Busy");
    return status;
}
static void end(botw_whole_audren *binding,whole_audren_owner *owner,int retire) {
    pthread_mutex_lock(&binding->lock);
    binding->calls--;
    if(owner){
        owner->busy=0;
        if(retire){whole_audren_owner **entry=&binding->owners;while(*entry!=owner)entry=&(*entry)->next;*entry=owner->next;}
    }
    pthread_mutex_unlock(&binding->lock);
    if(retire)free(owner);
}
/* CAPI callbacks merely balance strong binding borrows. Every actual kernel
 * cleanup is checked here, after quiesce/unregister and outside registry locks.
 * The private read endpoint retires last: services keeps its number on a failed
 * Close, so no later failure may permit that number to be reused for an alien. */
static botw_audren_result cleanup(whole_audren_owner *owner,char *error,size_t capacity) {
    botw_whole_audren *binding=owner->binding;atomic_store(&owner->closing,1);
    if(owner->session){
        botw_audren_result result=botw_audren_close(&owner->session,error,capacity);
        if(!control_ok(result))return result;
        if(owner->session)return failed(BOTW_AUDREN_SYSTEM_ERROR,error,capacity,"native Close did not clear its owner");
    }
    if(owner->registered){
        botw_audren_result result=botw_audren_unregister_region(binding->host,
            binding->configuration.process_owner,owner->view.source,error,capacity);
        if(!control_ok(result))return result;
        owner->registered=0;
    }
    if(atomic_load(&owner->backing_borrows)||atomic_load(&owner->event_borrows)||
            atomic_load(&owner->callbacks)||atomic_load(&owner->callback_error))
        return failed(BOTW_AUDREN_BUSY,error,capacity,"native resource callbacks/borrows have not quiesced");
    if(owner->transfer&&botw_whole_transfer_release(&owner->transfer,error,capacity))
        return kernel_failed(BOTW_AUDREN_SYSTEM_ERROR,error,capacity,"checked transfer lease release failed");
    if(owner->producer&&botw_whole_kernel_producer_release(&owner->producer,error,capacity))
        return kernel_failed(BOTW_AUDREN_SYSTEM_ERROR,error,capacity,"checked native event producer release failed");
    if(owner->write_event){
        if(botw_whole_kernel_close_event(NULL,owner->write_event,error,capacity))
            return kernel_failed(BOTW_AUDREN_SYSTEM_ERROR,error,capacity,"checked native event write close failed");
        owner->write_event=0;
    }
    if(owner->read_event){
        if(botw_whole_kernel_close_event(NULL,owner->read_event,error,capacity))
            return kernel_failed(BOTW_AUDREN_SYSTEM_ERROR,error,capacity,"checked native event read close failed");
        owner->read_event=0;
    }
    return (botw_audren_result){0,0,0};
}
static botw_audren_result work_size(void *context,const void *wire,size_t size,
        uint64_t *out,char *error,size_t capacity) {
    botw_whole_audren *binding=context;int status=begin(binding,NULL,0,NULL,error,capacity);
    if(status)return failed(status,error,capacity,"audren work-size bridge is unavailable");
    botw_audren_result result=botw_audren_get_work_buffer_size(wire,size,out,error,capacity);
    end(binding,NULL,0);return result;
}
static botw_audren_result open_renderer(void *context,const void *wire,size_t wire_size,
        uint32_t transfer_handle,uint32_t process_handle,uint64_t size,uint64_t aruid,
        void **out,uint32_t *event,char *error,size_t capacity) {
    clear_error(error,capacity);
    botw_whole_audren *binding=context;
    if(!out||*out||!event||process_handle!=0xffff8001u||!size||size>SIZE_MAX||
            (size&0xfffu)||wire_size!=BOTW_AUDREN_WIRE_PARAMETER_SIZE||!wire||
            aruid!=binding->configuration.applet_resource_user_id)
        return failed(BOTW_AUDREN_INVALID_ARGUMENT,error,capacity,"audren Open requires current-process identity and exact normal parameters");
    int status=begin(binding,NULL,0,NULL,error,capacity);
    if(status)return failed(status,error,capacity,"audren Open bridge is unavailable");
    whole_audren_owner *owner=calloc(1,sizeof *owner);
    if(!owner){end(binding,NULL,0);return failed(BOTW_AUDREN_OUT_OF_MEMORY,error,capacity,"allocate strong audren resource owner failed");}
    owner->binding=binding;owner->busy=1;
    atomic_init(&owner->backing_borrows,0);atomic_init(&owner->event_borrows,0);
    atomic_init(&owner->callbacks,0);atomic_init(&owner->closing,0);atomic_init(&owner->callback_error,0);
    pthread_mutex_lock(&binding->lock);owner->next=binding->owners;binding->owners=owner;pthread_mutex_unlock(&binding->lock);
    botw_audren_result result={BOTW_AUDREN_SYSTEM_ERROR,0,0};
    if(binding->configuration.validate_identity(binding->configuration.identity_context,
            binding->configuration.process_owner,aruid,error,capacity))goto rollback;
    if(botw_whole_transfer_acquire(transfer_handle,(size_t)size,3,&owner->transfer,&owner->view,error,capacity))goto rollback;
    if(!owner->view.rw_alias||owner->view.requested_size!=(size_t)size||owner->view.alias_size<(size_t)size||
            !owner->view.source||!owner->view.backing_id||((uintptr_t)owner->view.rw_alias&0xfffu)||
            (owner->view.source&0xfffu)){
        problem(error,capacity,"kernel transfer view has no exact aligned backing provenance");goto rollback;
    }
    if(botw_whole_kernel_create_event(NULL,0,&owner->write_event,&owner->read_event,error,capacity))goto rollback;
    if(botw_whole_kernel_retain_event_producer(owner->write_event,&owner->producer,error,capacity))goto rollback;
    botw_audren_backing backing={owner->view.rw_alias,(size_t)size,owner,backing_retain,backing_release};
    result=botw_audren_register_region_provenance(binding->host,binding->configuration.process_owner,
        owner->view.source,backing,3,owner->view.backing_id,owner->view.backing_offset,error,capacity);
    if(!control_ok(result))goto rollback;
    owner->registered=1;
    botw_audren_event port={owner,event_signal,event_clear,event_retain,event_release};
    result=botw_audren_open(binding->host,binding->configuration.process_owner,wire,wire_size,
        owner->view.source,size,aruid,port,&owner->session,error,capacity);
    if(!result.host_status&&result.service_result_valid==1&&!result.service_result&&owner->session){
        *out=owner;*event=owner->read_event;end(binding,owner,0);return result;
    }
rollback: {
        char original_error[256]={0};
        if(error&&capacity){size_t length=capacity<sizeof original_error?capacity:sizeof original_error-1;
            size_t used=0;while(used<length&&error[used])used++;memcpy(original_error,error,used);}
        botw_audren_result retired=cleanup(owner,error,capacity);
        if(!control_ok(retired)){
            *out=owner;*event=owner->read_event;terminal(binding,retired.host_status);end(binding,owner,0);return retired;
        }
        end(binding,owner,1);
        if(error&&capacity&&original_error[0])snprintf(error,capacity,"%s",original_error);
        if(!result.host_status&&result.service_result_valid==1&&result.service_result)return result;
        if(!result.host_status)result=failed(BOTW_AUDREN_SYSTEM_ERROR,error,capacity,"audren Open returned no complete native session");
        else if(error&&capacity&&!error[0])problem(error,capacity,"kernel/native audren Open failed before publication");
        return result;
    }
}
static botw_audren_result get_info(void *context,void *pointer,botw_audren_session_info *out,char *error,size_t capacity) {
    botw_whole_audren *binding=context;whole_audren_owner *owner=NULL;int status=begin(binding,pointer,0,&owner,error,capacity);
    if(status)return failed(status,error,capacity,"audren query owner is unavailable");
    botw_audren_result result=botw_audren_get_info(owner->session,out,error,capacity);end(binding,owner,0);return result;
}
static botw_audren_result start(void *context,void *pointer,char *error,size_t capacity) {
    botw_whole_audren *binding=context;whole_audren_owner *owner=NULL;int status=begin(binding,pointer,0,&owner,error,capacity);
    if(status)return failed(status,error,capacity,"audren Start owner is unavailable");
    botw_audren_result result=botw_audren_start(owner->session,error,capacity);end(binding,owner,0);return result;
}
static botw_audren_result stop(void *context,void *pointer,char *error,size_t capacity) {
    botw_whole_audren *binding=context;whole_audren_owner *owner=NULL;int status=begin(binding,pointer,0,&owner,error,capacity);
    if(status)return failed(status,error,capacity,"audren Stop owner is unavailable");
    botw_audren_result result=botw_audren_stop(owner->session,error,capacity);end(binding,owner,0);return result;
}
static botw_audren_result update(void *context,void *pointer,const void *input,size_t input_size,
        void *performance,size_t performance_size,void *output,size_t output_size,char *error,size_t capacity) {
    botw_whole_audren *binding=context;whole_audren_owner *owner=NULL;int status=begin(binding,pointer,0,&owner,error,capacity);
    if(status)return failed(status,error,capacity,"audren Update owner is unavailable");
    botw_audren_result result=botw_audren_update(owner->session,input,input_size,performance,performance_size,output,output_size,error,capacity);
    end(binding,owner,0);return result;
}
static botw_audren_result set_time(void *context,void *pointer,uint32_t time,char *error,size_t capacity) {
    botw_whole_audren *binding=context;whole_audren_owner *owner=NULL;int status=begin(binding,pointer,0,&owner,error,capacity);
    if(status)return failed(status,error,capacity,"audren rendering-time owner is unavailable");
    botw_audren_result result=botw_audren_set_rendering_time_limit(owner->session,time,error,capacity);end(binding,owner,0);return result;
}
static botw_audren_result close_renderer(void *context,void **pointer,char *error,size_t capacity) {
    clear_error(error,capacity);
    if(!pointer||!*pointer)return failed(BOTW_AUDREN_INVALID_ARGUMENT,error,capacity,"audren Close owner is absent");
    botw_whole_audren *binding=context;whole_audren_owner *owner=NULL;int status=begin(binding,*pointer,1,&owner,error,capacity);
    if(status)return failed(status,error,capacity,"audren Close owner is unavailable");
    botw_audren_result result=cleanup(owner,error,capacity);
    if(control_ok(result)){*pointer=NULL;end(binding,owner,1);}else{terminal(binding,result.host_status);end(binding,owner,0);}
    return result;
}
int botw_whole_audren_create(const botw_whole_audren_configuration *configuration,
        botw_whole_audren **out,char *error,size_t capacity) {
    clear_error(error,capacity);
    if(!configuration||!out||*out||configuration->abi_version!=BOTW_WHOLE_AUDREN_ABI||
            configuration->struct_size!=sizeof *configuration||!configuration->process_owner||
            !configuration->applet_resource_user_id||!configuration->validate_identity||
            configuration->host.abi_version!=BOTW_AUDREN_HOST_ABI||
            configuration->host.struct_size!=sizeof configuration->host||configuration->host.initially_active>1||
            configuration->host.dump_audio_commands||configuration->host.inspect_guest_stack||!configuration->host.failed)
        return problem(error,capacity,"whole audren requires explicit validated identities, real failure producer and diagnostics disabled");
    if(configuration->validate_identity(configuration->identity_context,configuration->process_owner,
            configuration->applet_resource_user_id,error,capacity)){
        if(error&&capacity&&!error[0])problem(error,capacity,"normal process/applet identity validation failed");return -1;
    }
    botw_whole_audren *binding=calloc(1,sizeof *binding);
    if(!binding)return problem(error,capacity,"allocate whole audren binding failed");
    if(pthread_mutex_init(&binding->lock,NULL)){free(binding);return problem(error,capacity,"initialize whole audren ownership lock failed");}
    binding->configuration=*configuration;
    atomic_init(&binding->failure,0);
    botw_audren_configuration native=configuration->host;native.failure_context=binding;native.failed=terminal;
    botw_audren_result result=botw_audren_host_create(&native,&binding->host,error,capacity);
    if(!control_ok(result)||!binding->host){
        if(error&&capacity&&!error[0])problem(error,capacity,"native audren Host initialization was not acknowledged");
        pthread_mutex_destroy(&binding->lock);free(binding);return -1;
    }
    *out=binding;return 0;
}
int botw_whole_audren_attach(botw_whole_audren *binding,botw_services *services,char *error,size_t capacity) {
    clear_error(error,capacity);
    if(!binding||!services)return problem(error,capacity,"whole audren attach requires live binding and services");
    pthread_mutex_lock(&binding->lock);
    if(binding->destroying||binding->services||binding->attaching||atomic_load(&binding->failure)){
        pthread_mutex_unlock(&binding->lock);return problem(error,capacity,"whole audren binding was already attached or is unavailable");
    }
    binding->attaching=1;binding->calls++;pthread_mutex_unlock(&binding->lock);
    botw_services_audren_api api={binding,work_size,open_renderer,get_info,start,stop,update,set_time,close_renderer};
    int result=botw_services_set_audren_api(services,&api,error,capacity);
    pthread_mutex_lock(&binding->lock);if(!result)binding->services=services;binding->attaching=0;binding->calls--;pthread_mutex_unlock(&binding->lock);
    return result;
}
botw_audren_result botw_whole_audren_set_active(botw_whole_audren *binding,int active,char *error,size_t capacity) {
    clear_error(error,capacity);
    if(!binding||(active!=0&&active!=1))return failed(BOTW_AUDREN_INVALID_ARGUMENT,error,capacity,"audren lifecycle requires measured boolean availability");
    int status=begin(binding,NULL,0,NULL,error,capacity);
    if(status)return failed(status,error,capacity,"audren lifecycle binding is unavailable");
    botw_audren_result result=botw_audren_host_set_active(binding->host,active,error,capacity);end(binding,NULL,0);return result;
}
int botw_whole_audren_destroy(botw_whole_audren **owner,botw_services **services,char *error,size_t capacity) {
    clear_error(error,capacity);
    if(!owner||!*owner)return problem(error,capacity,"whole audren binding owner is absent");
    botw_whole_audren *binding=*owner;pthread_mutex_lock(&binding->lock);
    if(binding->calls||binding->destroying||(binding->services&&(!services||*services!=binding->services))||
            (!binding->services&&services&&*services)){
        pthread_mutex_unlock(&binding->lock);return problem(error,capacity,"whole audren destroy requires quiescent callers and its exact service owner");
    }
    binding->destroying=1;botw_services *attached=binding->services;pthread_mutex_unlock(&binding->lock);
    if(attached&&botw_services_destroy_checked(attached,error,capacity))goto retry;
    if(attached){pthread_mutex_lock(&binding->lock);binding->services=NULL;*services=NULL;pthread_mutex_unlock(&binding->lock);}
    pthread_mutex_lock(&binding->lock);int pending=binding->owners!=NULL||binding->calls!=0;pthread_mutex_unlock(&binding->lock);
    if(pending){problem(error,capacity,"audren resource owners remain after service destruction");goto retry;}
    if(!control_ok(botw_audren_host_destroy(&binding->host,error,capacity)))goto retry;
    pthread_mutex_destroy(&binding->lock);free(binding);*owner=NULL;return 0;
retry:
    if(error&&capacity&&!error[0])problem(error,capacity,"native audren checked destruction was not acknowledged");
    pthread_mutex_lock(&binding->lock);binding->destroying=0;pthread_mutex_unlock(&binding->lock);return -1;
}
