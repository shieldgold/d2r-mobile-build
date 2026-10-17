/* SPDX-License-Identifier: MIT
 * Explicit base-v0/v4 compile candidate. No default runtime or Guest fallback. */
#if !defined(BOTW_WHOLE_RUNTIME_CONFIGURED) || !BOTW_WHOLE_RUNTIME_CONFIGURED
#error "Compile this provider only with explicit BOTW_WHOLE_RUNTIME_CONFIGURED=1"
#endif
#import <Foundation/Foundation.h>
#import <TargetConditionals.h>
#include "whole_frontend.h"
#include "whole_runtime_configuration_private.h"
#include "../adapter/whole_runtime_resources.h"
#include "../adapter/whole_identity.h"
#include "../adapter/whole_guest_memory.h"
#include "../adapter/whole_host_memory.h"
#include "../adapter/whole_transfer.h"
#include "../adapter/whole_kernel.h"
#include "../adapter/whole_ipc.h"
#include "../adapter/whole_heap_provider.h"
#include "../adapter/whole_audren.h"
#include "../adapter/whole_applet.h"
#include "../adapter/whole_audio_platform.h"
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* These are the real patched original host exports. Never preload or initialize
 * the guest here: the original renamed main retains TLS/SDK/init ownership. */
int botw_whole_host_main(int,char **);
int dm_guest_write_prepare_checked(void *,size_t);
void dm_gfx_suspend(int);
_Noreturn void botw_whole_host_failure(void);

enum { RUNTIME_COLD, RUNTIME_PREPARING, RUNTIME_READY, RUNTIME_FAILED };
typedef struct {
    pthread_mutex_t lock;
    _Atomic int phase, fatal;
    uint64_t process_owner, aruid;
    char bundle[PATH_MAX], documents[PATH_MAX], executable[PATH_MAX], temporary[PATH_MAX];
    char metadata[PATH_MAX], romfs[PATH_MAX], manifest[PATH_MAX];
    const char *arguments[8];
    botw_whole_guest_memory memory;
    botw_services *services;
    botw_whole_heap_provider *heap_provider;
    botw_whole_audren *audren;
    int ipc_bound;
} runtime_context;
static runtime_context g_configuration={.lock=PTHREAD_MUTEX_INITIALIZER};
static whole_frontend_runtime g_runtime;

static int problem(char *error,size_t capacity,int code,const char *message) {
    if(error&&capacity)snprintf(error,capacity,"whole runtime: %s",message);
    return code?code:EIO;
}
static int copy_path(char *out,size_t capacity,NSString *path) {
    const char *value=path.fileSystemRepresentation;
    if(!value||value[0]!='/'||strlen(value)>=capacity)return -1;
    strcpy(out,value);return 0;
}
static int identity(void *opaque,uint64_t process,uint64_t aruid,char *error,size_t capacity) {
    runtime_context *context=opaque;
    uint64_t actual=(uint64_t)(unsigned)getpid();
    /* botw_oe.c's static botw_applet_instance_id uses this same process identity.
     * Both values are immutable, also published to the real services instance;
     * nonzero alone never authorizes another process or fabricated ARUID. */
    if(!context||!context->services||!actual||process!=actual||aruid!=actual||
        context->process_owner!=actual||context->aruid!=actual)
        return problem(error,capacity,EPERM,"current process/applet identity does not match");
    return 0;
}
static int kernel_classify(void *opaque,uint64_t address,size_t length,int writable) {
    (void)opaque;return botw_whole_memory_classify(address,length,writable);
}
static int prepare_write(void *opaque,uint64_t address,size_t length,char *error,size_t capacity) {
    (void)opaque;
    return dm_guest_write_prepare_checked((void*)(uintptr_t)address,length)?0:
        problem(error,capacity,EIO,"normal checked write preparation was not acknowledged");
}
static int is_process(void *opaque,uint32_t handle) {
    runtime_context *context=opaque;
    return context&&context->process_owner==(uint64_t)(unsigned)getpid()&&handle==0xffff8001u;
}
static void log_service(void *opaque,const char *message) {
    (void)opaque;
    /* Ordinary bounded service error, with no diagnostic observation producer. */
    fprintf(stderr,"[botw whole service] %.512s\n",message?message:"unknown error");
}
static void fatal(void *opaque,int32_t status) {
    runtime_context *context=opaque;int expected=0;
    int actual=status>0?status:BOTW_AUDREN_SYSTEM_ERROR;
    if(context&&atomic_compare_exchange_strong(&context->fatal,&expected,actual)) {
        atomic_store_explicit(&context->phase,RUNTIME_FAILED,memory_order_release);
        /* The worker only latches/dispatches. The ordinary main terminal path
         * exits the whole host; it does not reenter CAPI, join this worker, signal
         * a false completion, or unload mappings still used by a live worker. */
        dispatch_async(dispatch_get_main_queue(),^{botw_whole_host_failure();});
    }
}
static int set_environment(const char *key,const char *value,char *error,size_t capacity) {
    if(setenv(key,value,1))return problem(error,capacity,errno,"setting explicit product environment failed");
    return 0;
}
static int cleanup_unpublished(runtime_context *context,char *error,size_t capacity) {
    if(context->ipc_bound) {
        if(botw_whole_ipc_unbind(context->services,error,capacity))return EIO;
        context->ipc_bound=0;
    }
    if(context->audren) {
        if(botw_whole_audren_destroy(&context->audren,&context->services,error,capacity))return EIO;
    } else if(context->services) {
        if(botw_services_destroy_checked(context->services,error,capacity))return EIO;
        context->services=NULL;
    }
    if(context->heap_provider&&botw_whole_heap_provider_destroy(&context->heap_provider,error,capacity))return EIO;
    return 0;
}
static int prepare(void *opaque,char *error,size_t capacity) {
    runtime_context *context=opaque;
    if(!context||(capacity&&!error))return problem(error,capacity,EINVAL,"invalid prepare arguments");
    int expected=RUNTIME_COLD;
    if(!atomic_compare_exchange_strong(&context->phase,&expected,RUNTIME_PREPARING))
        return problem(error,capacity,EBUSY,"prepare is one-shot; no preload/SDK retry is permitted");
    int code=0;
#if !TARGET_OS_IOS || TARGET_OS_SIMULATOR
    code=problem(error,capacity,ENOTSUP,"this runtime requires the explicitly packaged physical iPhoneOS target");
    goto failed;
#endif
    whole_runtime_resource_paths paths;
    if((code=whole_runtime_resources_verify(context->bundle,&paths,error,capacity)))goto failed;
    if(strcmp(paths.metadata,context->metadata)) {code=problem(error,capacity,EINVAL,"metadata argument changed");goto failed;}
    if((code=whole_runtime_romfs_verify(context->documents,context->romfs,sizeof context->romfs,error,capacity)))goto failed;
#if defined(BOTW_WHOLE_SHADER_PUBLICATION) && BOTW_WHOLE_SHADER_PUBLICATION
    if((code=whole_runtime_shader_bundle_publication(context->bundle,context->manifest,sizeof context->manifest,error,capacity)))goto failed;
    if(context->manifest[0]!='/') {code=problem(error,capacity,EINVAL,"shader publication did not supply an absolute verified path");goto failed;}
#else
    code=problem(error,capacity,ENOTSUP,"no verified shader publication is configured; host/Guest entry is blocked");
    goto failed;
#endif
    if((code=whole_identity_bind_documents(context->documents,error,capacity)))goto failed;
    const char *save=whole_identity_save_directory();
    if(!save) {code=problem(error,capacity,EPERM,"existing UID/save binding was not established");goto failed;}
    const char *keys[]={"DM_ROOT","DM_ROMFS","DM_SAVE","DM_TEMP","DM_ARGV0","DM_GAME_VERSION","DM_LANG","DM_OPMODE",
        "DM_BOTW_AUDIO_CPU","DM_AUDIO","DM_BOTW_SHADER_MANIFEST","DM_BOTW_POOL_POLICY"};
    const char *values[]={context->documents,context->romfs,save,context->temporary,"BotW","1.0.0","en-US","0","0","1",context->manifest,
        "ordinary-pools-base-v0-v1"};
    for(size_t i=0;i<sizeof keys/sizeof *keys;i++)if((code=set_environment(keys[i],values[i],error,capacity)))goto failed;
    context->process_owner=context->aruid=(uint64_t)(unsigned)getpid();
    if(!context->process_owner) {code=problem(error,capacity,EPERM,"current process identity unavailable");goto failed;}
    context->memory=(botw_whole_guest_memory){BOTW_WHOLE_GUEST_MEMORY_ABI,sizeof context->memory,context,
        kernel_classify,botw_whole_host_module_classify,botw_whole_host_thread_classify,prepare_write};
    context->services=botw_services_create(log_service,botw_whole_guest_memory_access,&context->memory);
    if(!context->services) {code=problem(error,capacity,ENOMEM,"real services allocation failed");goto failed;}
    botw_services_set_memory_write_preparer(context->services,botw_whole_guest_memory_prepare,&context->memory);
    botw_services_set_memory_copiers(context->services,botw_whole_guest_memory_read,botw_whole_guest_memory_write,&context->memory);
    botw_services_kernel_api kernel={context,botw_whole_kernel_create_event,botw_whole_kernel_duplicate_event,
        botw_whole_kernel_signal_event,botw_whole_kernel_clear_event,botw_whole_kernel_close_event,is_process};
    botw_services_set_kernel_api(context->services,&kernel);
    /* Foreground/focus are first measured and published by lifecycle on main.
     * No synthetic initial applet state or focus suspension policy is installed.
     * The bridge adds only supported services; FS/HID/capture/performance remain
     * unsupported unless their actual normal producers are separately attached. */
    if((code=botw_services_set_application_language(context->services,"en-US","fixed-base-v0-language",error,capacity)))goto failed;
    botw_whole_audren_configuration configuration={0};
    configuration.abi_version=BOTW_WHOLE_AUDREN_ABI;configuration.struct_size=sizeof configuration;
    configuration.process_owner=context->process_owner;configuration.applet_resource_user_id=context->aruid;
    configuration.identity_context=context;configuration.validate_identity=identity;
    configuration.host.abi_version=BOTW_AUDREN_HOST_ABI;configuration.host.struct_size=sizeof configuration.host;
    /* No AVAudioSession or measured lifecycle has been accepted yet. */
    configuration.host.initially_active=0;configuration.host.failure_context=context;configuration.host.failed=fatal;
    if((code=botw_whole_heap_provider_create(context->process_owner,&context->heap_provider,
        &configuration.host.memory_provider,error,capacity)))goto failed;
    if((code=botw_whole_audren_create(&configuration,&context->audren,error,capacity)))goto failed;
    if((code=botw_whole_audren_attach(context->audren,context->services,error,capacity)))goto failed;
    botw_whole_ipc_configuration ipc={context->services,&context->memory,
        botw_whole_guest_memory_read,botw_whole_host_current_tlr};
    if((code=botw_whole_ipc_bind(&ipc,error,capacity)))goto failed;
    context->ipc_bound=1;
    if(atomic_load(&context->fatal)) {code=problem(error,capacity,EIO,"terminal audio failure during preparation");goto failed;}
    atomic_store_explicit(&context->phase,RUNTIME_READY,memory_order_release);
    if(error&&capacity)error[0]=0;return 0;
failed:
    atomic_store_explicit(&context->phase,RUNTIME_FAILED,memory_order_release);
    /* No host/guest caller exists before successful prepare. On an actual
     * cleanup failure preserve every still-owned provider for process lifetime;
     * do not free a borrowed event, transfer alias or worker's resources. */
    char cleanup_error[256]={0};
    if(cleanup_unpublished(context,cleanup_error,sizeof cleanup_error)) {
        if(error&&capacity) {
            size_t n=strnlen(error,capacity);if(n<capacity)snprintf(error+n,capacity-n,"; retained cleanup owner: %s",cleanup_error);
        }
    }
    return code?code:EIO;
}
static int lifecycle(void *opaque,int foreground,int focused,int audio_available,char *error,size_t capacity) {
    runtime_context *context=opaque;
    if(!context||(capacity&&!error)||foreground<0||foreground>1||focused<0||focused>1||audio_available<0||audio_available>1)
        return problem(error,capacity,EINVAL,"invalid measured lifecycle");
    pthread_mutex_lock(&context->lock);
    int code=0,terminal_status=BOTW_AUDREN_SYSTEM_ERROR;
    if(atomic_load_explicit(&context->phase,memory_order_acquire)!=RUNTIME_READY||atomic_load(&context->fatal)) {
        code=problem(error,capacity,EIO,"lifecycle requires an actually prepared live runtime");goto done;
    }
    int active=foreground&&focused&&audio_available;
    if(!active) {
        botw_audren_result result=botw_whole_audren_set_active(context->audren,0,error,capacity);
        if(result.host_status||result.service_result_valid) {
            if(result.host_status>0)terminal_status=result.host_status;
            code=EIO;goto failed;
        }
        if((code=whole_audio_platform_set_active(0,error,capacity)))goto failed;
        dm_gfx_suspend(1); /* Real admission/borrow/GPU drain; no CPU suspension. */
    } else {
        /* The UIKit caller has already activated its real AVAudioSession. */
        if((code=whole_audio_platform_set_active(1,error,capacity)))goto failed;
        dm_gfx_suspend(0);
        botw_audren_result result=botw_whole_audren_set_active(context->audren,1,error,capacity);
        if(result.host_status||result.service_result_valid) {
            if(result.host_status>0)terminal_status=result.host_status;
            code=EIO;goto failed;
        }
    }
    if((code=whole_applet_publish(foreground,focused,0,0,error,capacity)))goto failed;
    if((code=botw_services_set_applet_state(context->services,context->aruid,foreground,focused,0,error,capacity)))goto failed;
    if(atomic_load(&context->fatal)) {code=problem(error,capacity,EIO,"terminal producer rejected lifecycle");goto failed;}
    if(error&&capacity)error[0]=0;goto done;
failed:
    /* There may already be a live host worker. A terminal error cannot merely
     * stop the UI while guest execution continues on a partially resumed host. */
    fatal(context,terminal_status);
    if(error&&capacity&&!error[0])problem(error,capacity,EIO,"real lifecycle operation was not acknowledged");
done:
    pthread_mutex_unlock(&context->lock);return code;
}
const whole_frontend_runtime *whole_runtime_configuration(void) {
    static dispatch_once_t once;static int valid;
    dispatch_once(&once,^{
        @autoreleasepool {
            runtime_context *context=&g_configuration;
            NSArray<NSURL*> *documents=[[NSFileManager defaultManager] URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask];
            /* Canonicalize only the OS-provided trusted bundle root, before
             * forming any artifact path. System /var aliases must not become
             * artifact symlink exemptions; bundle children remain no-follow. */
            NSString *trusted_bundle=NSBundle.mainBundle.bundlePath.stringByResolvingSymlinksInPath;
            if(documents.count!=1||copy_path(context->documents,sizeof context->documents,documents[0].path)||
                copy_path(context->bundle,sizeof context->bundle,trusted_bundle)||
                copy_path(context->temporary,sizeof context->temporary,NSTemporaryDirectory())||
                copy_path(context->executable,sizeof context->executable,NSBundle.mainBundle.executablePath))return;
            int n=snprintf(context->metadata,sizeof context->metadata,"%s/botw",context->bundle);
            if(n<0||(size_t)n>=sizeof context->metadata)return;
            const char *modules[]={"main","subsdk0","subsdk1","subsdk2","subsdk3","sdk"};
            context->arguments[0]=context->executable;context->arguments[1]=context->metadata;
            for(unsigned i=0;i<6;i++)context->arguments[i+2]=modules[i];
            g_runtime=(whole_frontend_runtime){WHOLE_FRONTEND_RUNTIME_ABI,context,8,
                context->arguments,prepare,lifecycle,botw_whole_host_main};valid=1;
        }
    });
    return valid?&g_runtime:NULL;
}
