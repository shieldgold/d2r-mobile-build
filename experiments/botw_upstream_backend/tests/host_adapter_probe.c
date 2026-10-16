/* Owned CPU contract probe. No backend object, Metal device or game is linked. */
#define DM_TITLE_STRONG 1
#include "../upstream/dm/host/dm.h"
#include "../adapter/host_adapter.h"
#include <assert.h>
#include <setjmp.h>
#include <stdatomic.h>

static jmp_buf boundary;
static char caught[512];
static unsigned prepare_calls, register_calls, resource_calls, wait_calls, manifest_calls;
static int reject_manifest, reject_prepare, reject_register, return_from_error;
static char storage[64];
static int fake_layer;
static botw_backend_fault registered_fault;
static botw_backend_prepare registered_prepare;
static void on_error(const char *why) { snprintf(caught, sizeof caught, "%s", why); if(!return_from_error)longjmp(boundary, 1); }
static int prepare(void *p, size_t n, char *e, size_t c) {
    prepare_calls++;
    if (reject_prepare || p != storage || n != sizeof storage) {
        snprintf(e, c, "owned preparation rejected"); return -1;
    }
    return 0;
}
static int tracking(botw_backend_fault f, botw_backend_prepare p) {
    register_calls++;
    if (reject_register) return -1;
    registered_fault=f; registered_prepare=p; return 0;
}
static int validate(const char *path, char *e, size_t c) {
    manifest_calls++;
    if (reject_manifest || strcmp(path, "/owned-test/manifest.json")) {
        snprintf(e,c,"owned validator rejected"); return 0;
    }
    return 1;
}
static int fault(void *p, int write, void *uc) { return p==storage && write==1 && uc==NULL; }
static int checked(void *p,size_t n,char *e,size_t c) { return prepare(p,n,e,c); }
static void legacy(void *p,size_t n) { (void)p;(void)n;assert(0); }
static void resource(uint32_t tick,const char *why) { assert(tick==7&&!strcmp(why,"owned"));resource_calls++; }
static void wait_report(void) { wait_calls++; }
static botw_backend_module module={"main",storage,sizeof storage};
static botw_backend_host_services config(void) {
    return (botw_backend_host_services){1,&fake_layer,prepare,tracking,validate,on_error,
        "/owned-test/manifest.json",&module,1};
}
static void install(void) {
    char error[256];botw_backend_host_services s=config();
    assert(botw_backend_host_install(&s,error,sizeof error)==1);
    assert(error[0]==0&&manifest_calls==1&&g_t0_ns&&g_dm_title==DM_TITLE_BOTW);
}
static void *concurrent(void *ignored) {
    (void)ignored;
    for (unsigned i=0;i<10000;i++) {
        module_t *m=module_of(storage+i%sizeof storage);
        assert(m&&!strcmp(m->name,"main")&&m->base==(uint8_t*)storage&&m->size==sizeof storage);
        assert(front_metal_layer()==&fake_layer&&now_ns()>=g_t0_ns);
    }
    return NULL;
}
int main(int argc,char **argv) {
    assert(argc==2);char error[256];
    if (!strcmp(argv[1],"invalid-install")) {
        botw_backend_host_services s=config();s.prepare_write=NULL;
        assert(!botw_backend_host_install(&s,error,sizeof error)&&manifest_calls==0);
        s=config();s.module_count=9;
        assert(!botw_backend_host_install(&s,error,sizeof error)&&manifest_calls==0);
        s=config();botw_backend_module bad={"main",(void*)(UINTPTR_MAX-1),8};s.modules=&bad;
        assert(!botw_backend_host_install(&s,error,sizeof error)&&manifest_calls==0);
        install();return 0;
    }
    if (!strcmp(argv[1],"manifest-reject")) {
        reject_manifest=1;botw_backend_host_services s=config();
        assert(!botw_backend_host_install(&s,error,sizeof error));
        assert(strstr(error,"validator rejected")&&manifest_calls==1);
        reject_manifest=0;manifest_calls=0;install();return 0;
    }
    install();
    if (!strcmp(argv[1],"forward")) {
        assert(front_metal_layer()==&fake_layer&&module_of(storage)==module_of(storage+63));
        assert(module_of(storage+64)==NULL&&module_of((void*)1)==NULL);
        dm_guest_write_prepare(storage,sizeof storage);assert(prepare_calls==1);
        assert(!botw_backend_host_register_checked_tracking(fault,checked)&&register_calls==1);
        assert(registered_fault==fault&&registered_prepare==checked&&registered_fault(storage,1,NULL));
        dm_res_report_register(resource);botw_backend_host_resource_report(7,"owned");
        crash_set_wait_dump(wait_report);assert(botw_backend_host_has_wait_report()&&wait_calls==0&&resource_calls==1);
        botw_backend_host_services s=config();assert(!botw_backend_host_install(&s,error,sizeof error));
    } else if (!strcmp(argv[1],"legacy-fault")) {
        if (!setjmp(boundary)) {dm_fault_hook_register(fault);assert(0);}
        assert(strstr(caught,"checked whole-module")&&register_calls==0);
    } else if (!strcmp(argv[1],"legacy-prepare")) {
        if (!setjmp(boundary)) {dm_guest_write_prep_register(legacy);assert(0);}
        assert(strstr(caught,"cannot represent")&&register_calls==0);
    } else if (!strcmp(argv[1],"prepare-failure")) {
        reject_prepare=1;
        if (!setjmp(boundary)) {dm_guest_write_prepare(storage,sizeof storage);assert(0);}
        assert(strstr(caught,"owned preparation rejected")&&prepare_calls==1);
    } else if (!strcmp(argv[1],"registration-failure")) {
        reject_register=1;
        assert(botw_backend_host_register_checked_tracking(fault,checked)==-1&&register_calls==1);
        assert(!registered_fault&&!registered_prepare);
    } else if (!strcmp(argv[1],"policy")) {
        setenv("DM_SHADER_CAPTURE","/must-not-exist",1);
        setenv("DM_SHADER_OBSERVATION","/must-not-exist",1);
        setenv("DM_NVN_SKIP_FS","all",1);
        setenv("DM_BOTW_SHADER_MANIFEST","/host-environment-tamper",1);
        assert(!botw_backend_compat_getenv("DM_SHADER_CAPTURE"));
        assert(!botw_backend_compat_getenv("DM_SHADER_OBSERVATION"));
        assert(!botw_backend_compat_getenv("DM_NVN_SKIP_FS"));
        assert(!strcmp(botw_backend_compat_getenv("DM_STOP_ON_UNIMPL"),"1"));
        assert(!strcmp(botw_backend_compat_getenv("DM_BOTW_SHADER_MANIFEST"),"/owned-test/manifest.json"));
        const char *zero[]={"DM_NVN_ZSKIP","DM_NVN_UNWRITTEN_DC","DM_NVN_TBDR","DM_NVN_XV","DM_NVN_DEFER","DM_NVN_BLOOM_Q","DM_RT_AB","DM_LONGFRAME","DM_NVN_FRAMES","DM_MEM_CAP_GB","DM_PRESENT_LINK"};
        for(unsigned i=0;i<sizeof zero/sizeof *zero;i++)assert(!strcmp(botw_backend_compat_getenv(zero[i]),"0"));
        assert(!strcmp(botw_backend_compat_getenv("DM_PSO_MANIFEST"),"off"));
        const char *disabled[]={"DM_BOTW_SHADER_MISSING","DM_NVN_ALIASLOG","DM_NVN_CMD_AUDIT","DM_NVN_CMD_AUDIT_TRACE","DM_NVN_TEXDUMP_SER","DM_SHADER_OBSERVATION_SHA256"};
        for(unsigned i=0;i<sizeof disabled/sizeof *disabled;i++){
            setenv(disabled[i],"1",1);assert(!botw_backend_compat_getenv(disabled[i]));
        }
        assert(botw_backend_compat_diagnostic_enabled()==0);
        assert(botw_backend_compat_backtrace((void**)1,32)==0);
        botw_backend_compat_backtrace_symbols_fd((void *const*)1,32,2);
    } else if (!strcmp(argv[1],"diagnostic-denied")) {
        if (!setjmp(boundary)) {botw_backend_host_diagnostic_denied("owned probe");assert(0);}
        assert(strstr(caught,"entry disabled: owned probe"));
    } else if (!strcmp(argv[1],"callback-return-aborts")) {
        return_from_error=1;
        botw_backend_host_diagnostic_denied("owned returning callback");
    } else if (!strcmp(argv[1],"concurrency")) {
        pthread_t threads[4];for(unsigned i=0;i<4;i++)assert(!pthread_create(&threads[i],NULL,concurrent,NULL));
        for(unsigned i=0;i<4;i++)assert(!pthread_join(threads[i],NULL));
    } else assert(0);
    puts("owned host-adapter contract passed; no backend/GPU linked");return 0;
}
