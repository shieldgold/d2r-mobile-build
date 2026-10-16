/* Real normal host function bodies, extracted from the current patched source.
 * Owned metadata/object fixtures only; no Guest, Metal, drawable or GPU work. */
#include <assert.h>
#include <pthread.h>
#include <setjmp.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvn_protos.h"
#include "normal_graphics_types.inc"
#define NVN_NFUNC 3
#include "normal_graphics_slot_type.inc"
static nvn_slot_t nvn_slot[NVN_NFUNC];
static const char *nvn_names[NVN_NFUNC]={"owned_first","owned_second","owned_third"};
static unsigned g_nsem;
static int g_log;
static const int _mh_execute_header;
typedef struct { const char *name;void *fn; } nvn_impl;
static void owned_semantic(void) {}
static void nvn_unimpl_entry(void) {}
static nvn_impl implementation[2];
static unsigned metadata_calls,expected_hits=2;
static int metadata_missing,metadata_invalid;
static _Atomic unsigned log_counts[3],lookup_calls;
static int check_boot_order;
static const char *expected_name="owned_lookup";
static int owned_lookup_value;
static _Thread_local NVNdevice *current_device;
static _Thread_local uint32_t expected_flags;
static _Thread_local int mode_lock_held;
static pthread_mutex_t mode_lock=PTHREAD_MUTEX_INITIALIZER;
static jmp_buf failure;
static int failure_armed,failed;
static _Noreturn void owned_failure(const char *format,...) {
    (void)format;failed++;
    if(failure_armed)longjmp(failure,1);
    abort();
}
#define die(...) owned_failure(__VA_ARGS__)
static char *owned_getenv(const char *name) { assert(!strcmp(name,"DM_NVN_LOG"));return NULL; }
#define getenv owned_getenv
static const uint8_t *getsectiondata(const void *header,const char *segment,const char *section,unsigned long *size) {
    assert(header==&_mh_execute_header&&!strcmp(segment,"__DATA")&&!strcmp(section,"swnvn"));
    metadata_calls++;
    implementation[0]=(nvn_impl){metadata_invalid?"owned_absent":"owned_second",(void *)owned_semantic};
    implementation[1]=(nvn_impl){"owned_first",(void *)owned_semantic};
    *size=metadata_missing?0:sizeof implementation;
    return metadata_missing?NULL:(const void *)implementation;
}
static void botw_conditional_modes_lock(void) { assert(!pthread_mutex_lock(&mode_lock));mode_lock_held=1; }
static void botw_conditional_modes_unlock(void) { assert(mode_lock_held);mode_lock_held=0;assert(!pthread_mutex_unlock(&mode_lock)); }
static void *nvn_lookup(const char *name) {
    assert(name==expected_name);
    if(check_boot_order)assert(atomic_load(&log_counts[0])==1);
    atomic_fetch_add(&lookup_calls,1);return &owned_lookup_value;
}
static void owned_log(const char *literal,...) {
    assert(!strchr(literal,'%'));
    if(!strcmp(literal,"BotW graphics bootstrap: native lookup entered"))atomic_fetch_add(&log_counts[0],1);
    else if(!strcmp(literal,"BotW graphics bootstrap: native dispatch ready")) {
        assert(metadata_calls==1&&g_nsem==expected_hits&&!g_log);
        for(unsigned i=0;i<NVN_NFUNC;i++)assert(nvn_slot[i].fn==(i<expected_hits?(void *)owned_semantic:(void *)nvn_unimpl_entry));
        atomic_fetch_add(&log_counts[1],1);
    } else if(!strcmp(literal,"BotW graphics bootstrap: native device initialized")) {
        assert(current_device&&!mode_lock_held);
        const dev_t_ *device=(const void *)current_device;
        assert(device->magic==MAGIC('D','E','V',0)&&device->flags==expected_flags);
        assert(!device->origin&&!device->depth_mode&&!device->dbg_cb&&!device->dbg_data);
        atomic_fetch_add(&log_counts[2],1);
    } else assert(!"unexpected ordinary log");
    fprintf(stderr,"[dm] %s\n",literal);
}
#define LOG(...) owned_log(__VA_ARGS__)
#define OLOG(...) ((void)0) /* Ordinary fixture never enables the trace path. */
#define HLE_FORCE(name,why,ret,fn,...) static ret fn(__VA_ARGS__)
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
#include "normal_graphics_functions.inc"
static const NVNT_nvnDeviceInitialize device_prototype=h_nvnDeviceInitialize;
static void bootstrap(void) {
    check_boot_order=1;
    for(unsigned i=0;i<4;i++)assert(botw_nvn_bootstrap(expected_name)==&owned_lookup_value);
    assert(atomic_load(&log_counts[0])==1&&atomic_load(&lookup_calls)==4);
}
static void slots(void) {
    if(metadata_missing)expected_hits=0;
    nvn_init_slots();nvn_init_slots();
    assert(metadata_calls==1&&atomic_load(&log_counts[1])==1&&g_nsem==expected_hits);
}
static void slots_failed(void) {
    metadata_invalid=1;failure_armed=1;
    if(!setjmp(failure)){nvn_init_slots();assert(!"invalid metadata silently completed");}
    failure_armed=0;assert(failed==1&&!atomic_load(&log_counts[1]));
}
static void device(void) {
    NVNdevice object;NVNdeviceBuilder builder;
    for(unsigned i=0;i<4;i++) {
        memset(&object,0xa5,sizeof object);memset(&builder,0,sizeof builder);
        expected_flags=0xa0+i;((dev_builder_t *)&builder)->flags=expected_flags;current_device=&object;
        assert(device_prototype(&object,&builder)==1);
        const dev_t_ *state=(const void *)&object;
        assert(state->magic==MAGIC('D','E','V',0)&&state->flags==expected_flags&&!mode_lock_held);
        assert(!state->origin&&!state->depth_mode&&!state->dbg_cb&&!state->dbg_data);
    }
    assert(atomic_load(&log_counts[2])==1);
}
static void *bootstrap_worker(void *unused) {
    (void)unused;for(unsigned i=0;i<100;i++)assert(botw_nvn_bootstrap(expected_name)==&owned_lookup_value);return NULL;
}
static void *device_worker(void *unused) {
    (void)unused;NVNdevice object;NVNdeviceBuilder builder;
    expected_flags=0xb1;current_device=&object;memset(&builder,0,sizeof builder);((dev_builder_t *)&builder)->flags=expected_flags;
    for(unsigned i=0;i<100;i++)assert(device_prototype(&object,&builder)==1);return NULL;
}
static void concurrent(int is_device) {
    pthread_t workers[8];
    for(unsigned i=0;i<8;i++)assert(!pthread_create(workers+i,NULL,is_device?device_worker:bootstrap_worker,NULL));
    for(unsigned i=0;i<8;i++)assert(!pthread_join(workers[i],NULL));
    assert(atomic_load(&log_counts[is_device?2:0])==1);
    if(!is_device)assert(atomic_load(&lookup_calls)==800);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"bootstrap"))bootstrap();
    else if(!strcmp(argv[1],"slots"))slots();
    else if(!strcmp(argv[1],"slots-empty")){metadata_missing=1;slots();}
    else if(!strcmp(argv[1],"slots-failed"))slots_failed();
    else if(!strcmp(argv[1],"device"))device();
    else if(!strcmp(argv[1],"bootstrap-concurrent"))concurrent(0);
    else if(!strcmp(argv[1],"device-concurrent"))concurrent(1);
    else assert(!"unknown owned mode");
    return 0;
}
