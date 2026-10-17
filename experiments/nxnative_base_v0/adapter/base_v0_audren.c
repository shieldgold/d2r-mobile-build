/* SPDX-License-Identifier: MIT */
#include "base_v0_audren.h"
#include "normal_policy.h"
#include "whole_audren.h"
#include "whole_audio_platform.h"
#include "whole_guest_memory.h"
#include "whole_host_memory.h"
#include "whole_heap_provider.h"
#include "whole_ipc.h"
#include "whole_kernel.h"
#include "whole_transfer.h"
#include <dispatch/dispatch.h>
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>

enum { COLD, PREPARING, READY, FAILED, CLOSED };
typedef struct {
    pthread_mutex_t lock;
    _Atomic int state, fatal;
    uint64_t process_owner;
    int foreground,focused,audio_available,ipc_bound;
    botw_whole_guest_memory memory;
    botw_services *services;
    botw_whole_audren *audren;
    botw_whole_heap_provider *heap_provider;
} audio_runtime;
static audio_runtime runtime={.lock=PTHREAD_MUTEX_INITIALIZER};
/* Same exact public h_applet_Aruid in the pinned complete upstream. */
#define BASE_V0_ARUID UINT64_C(0x80)
static int identity(void *p,uint64_t owner,uint64_t aruid,char *e,size_t n) {
    (void)e;(void)n;audio_runtime *r=p;
    return !r||!r->services||!owner||owner!=(uint64_t)(unsigned)getpid()||owner!=r->process_owner||aruid!=BASE_V0_ARUID ? -1:0;
}
static int process_handle(void *p,uint32_t handle) {
    audio_runtime *r=p;
    return r&&r->process_owner==(uint64_t)(unsigned)getpid()&&handle==UINT32_C(0xffff8001);
}
static int kernel(void *p,uint64_t a,size_t n,int w) {
    (void)p;return botw_whole_memory_classify(a,n,w);
}
static int prepare_write(void *p,uint64_t a,size_t n,char *e,size_t cap) {
    (void)p;(void)e;(void)cap;return dm_guest_write_prepare_checked((void*)(uintptr_t)a,n)?0:-1;
}
static void terminal_main(void *p) {(void)p;nxnative_normal_failure();}
static void terminal(void *p,int32_t status) {
    (void)status;audio_runtime *r=p;int expected=0;
    /* The worker never joins/reenters its own CAPI. No status/operand content
     * is printed; the normal terminal owner exits on the ordinary main queue. */
    if(atomic_compare_exchange_strong(&r->fatal,&expected,1))
        dispatch_async_f(dispatch_get_main_queue(),NULL,terminal_main);
}
static int result_ok(botw_audren_result result) {
    return !result.host_status&&!result.service_result_valid;
}
/* Runtime lock held, no audio callback enters it. Correct pause ordering is
 * real engine round ACK -> PCM quiesce; resume is the reverse ordering. */
static int apply_locked(audio_runtime *r) {
    char error[256];int active=r->foreground&&r->focused&&r->audio_available;
    if(atomic_load(&r->fatal))return -1;
    if(!active) {
        if(!result_ok(botw_whole_audren_set_active(r->audren,0,error,sizeof error)))return -1;
        if(whole_audio_platform_set_active(0,error,sizeof error))return -1;
    } else {
        if(whole_audio_platform_set_active(1,error,sizeof error))return -1;
        if(!result_ok(botw_whole_audren_set_active(r->audren,1,error,sizeof error))) {
            (void)whole_audio_platform_set_active(0,error,sizeof error);return -1;
        }
    }
    return botw_services_set_applet_state(r->services,BASE_V0_ARUID,r->foreground,r->focused,0,error,sizeof error);
}
static int cleanup_locked(audio_runtime *r) {
    char error[256];
    if(r->ipc_bound) {
        if(botw_whole_ipc_unbind(r->services,error,sizeof error))return -1;
        r->ipc_bound=0;
    }
    if(r->audren) {
        if(botw_whole_audren_destroy(&r->audren,&r->services,error,sizeof error))return -1;
    } else if(r->services) {
        if(botw_services_destroy_checked(r->services,error,sizeof error))return -1;
        r->services=NULL;
    }
    if(r->heap_provider&&botw_whole_heap_provider_destroy(&r->heap_provider,error,sizeof error))return -1;
    return 0;
}
int nxnative_base_v0_audren_prepare(void) {
    audio_runtime *r=&runtime;char error[256];int code=-1;
    pthread_mutex_lock(&r->lock);
    if(atomic_load(&r->state)!=COLD)goto done;
    atomic_store(&r->state,PREPARING);r->process_owner=(uint64_t)(unsigned)getpid();
    if(!r->process_owner)goto failed;
    r->memory=(botw_whole_guest_memory){BOTW_WHOLE_GUEST_MEMORY_ABI,sizeof r->memory,r,kernel,
        botw_whole_host_module_classify,botw_whole_host_thread_classify,prepare_write};
    /* Complete original parser, with output/error logging deliberately absent. */
    r->services=botw_services_create(NULL,botw_whole_guest_memory_access,&r->memory);
    if(!r->services)goto failed;
    botw_services_set_memory_write_preparer(r->services,botw_whole_guest_memory_prepare,&r->memory);
    botw_services_set_memory_copiers(r->services,botw_whole_guest_memory_read,botw_whole_guest_memory_write,&r->memory);
    botw_services_kernel_api api={r,botw_whole_kernel_create_event,botw_whole_kernel_duplicate_event,
        botw_whole_kernel_signal_event,botw_whole_kernel_clear_event,botw_whole_kernel_close_event,process_handle};
    botw_services_set_kernel_api(r->services,&api);
    botw_whole_audren_configuration c={0};
    c.abi_version=BOTW_WHOLE_AUDREN_ABI;c.struct_size=sizeof c;c.process_owner=r->process_owner;
    c.applet_resource_user_id=BASE_V0_ARUID;c.identity_context=r;c.validate_identity=identity;
    c.host.abi_version=BOTW_AUDREN_HOST_ABI;c.host.struct_size=sizeof c.host;
    c.host.initially_active=0;c.host.failure_context=r;c.host.failed=terminal;
    if(botw_whole_heap_provider_create(r->process_owner,&r->heap_provider,&c.host.memory_provider,error,sizeof error))goto failed;
    if(botw_whole_audren_create(&c,&r->audren,error,sizeof error)||
       botw_whole_audren_attach(r->audren,r->services,error,sizeof error))goto failed;
    botw_whole_ipc_configuration ipc={r->services,&r->memory,botw_whole_guest_memory_read,botw_whole_host_current_tlr};
    if(botw_whole_ipc_bind(&ipc,error,sizeof error))goto failed;
    r->ipc_bound=1;
    if(apply_locked(r)||atomic_load(&r->fatal))goto failed;
    atomic_store_explicit(&r->state,READY,memory_order_release);code=0;goto done;
failed:
    atomic_store(&r->state,FAILED);
    /* Cleanup failures retain their true aliases/event/worker owners. */
    (void)cleanup_locked(r);
done:
    pthread_mutex_unlock(&r->lock);return code;
}
static int state_change(int which,int a,int b) {
    if((a!=0&&a!=1)||(which&&b!=0&&b!=1))return -1;
    audio_runtime *r=&runtime;int code=0;pthread_mutex_lock(&r->lock);
    int state=atomic_load(&r->state);
    if(state==FAILED||state==CLOSED||atomic_load(&r->fatal))code=-1;
    else {
        if(which){r->foreground=a;r->focused=b;}else r->audio_available=a;
        if(state==READY&&apply_locked(r)){atomic_store(&r->state,FAILED);code=-1;}
    }
    pthread_mutex_unlock(&r->lock);return code;
}
int nxnative_base_v0_audren_audio_available(int active){return state_change(0,active,0);}
int nxnative_base_v0_audren_focus(int foreground,int focused){return state_change(1,foreground,focused);}
int nxnative_base_v0_audren_ready(void){return atomic_load_explicit(&runtime.state,memory_order_acquire)==READY&&!atomic_load(&runtime.fatal);}
int nxnative_base_v0_audren_shutdown(void) {
    audio_runtime *r=&runtime;int code=-1;pthread_mutex_lock(&r->lock);
    if(atomic_load(&r->state)==READY) {
        r->audio_available=0;if(apply_locked(r))goto done;
        atomic_store(&r->state,FAILED);
    }
    if(atomic_load(&r->state)==FAILED&&!cleanup_locked(r)){atomic_store(&r->state,CLOSED);code=0;}
done:
    pthread_mutex_unlock(&r->lock);return code;
}
