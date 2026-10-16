/* Owned normal API/TSD fixtures only. No Guest entry, GPU or device exists. */
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if defined(OWNED_STATUS_MAP)
#define block inherited_transfer_abort_block
#define main inherited_transfer_fixture_main
#include "whole_transfer_probe.c"
#undef main
#undef block
#include "actual_status_wait.inc"
static int actual_status_word_svc(int number,uint64_t *regs) {switch(number){
#include "original_word_cases.inc"
default:abort();}}
static int status_lock_checks;
static int status_write_errno;
static unsigned dispatch_calls;
static uint64_t svc_dispatch_impl(int number,uint64_t *frame,uint64_t original) {
    (void)original;dispatch_calls++;
    if(number==4||number==5)return owned_memory_svc(number,frame);
    if(number==0x1c||number==0x1d||number==0x34||number==0x35) {
        assert(actual_status_word_svc(number,frame)==1);return 0;
    }
    frame[0]=0;frame[1]=0x1234;return 0x5a; /* Owned excluded-API stand-in. */
}
#if defined(BOTW_NORMAL_API_STATUS_BASELINE)
#define svc_dispatch svc_dispatch_impl
#else
#include "actual_status_wrapper.inc"
#endif
ssize_t owned_status_write(int fd,const void *data,size_t size) {
    if(status_lock_checks) {
        assert(!pthread_mutex_trylock(&G));pthread_mutex_unlock(&G);
        assert(!pthread_mutex_trylock(&g_heap_resize_lock));pthread_mutex_unlock(&g_heap_resize_lock);
        assert(!pthread_mutex_trylock(&g_map_lock));pthread_mutex_unlock(&g_map_lock);
    }
    ssize_t result=write(fd,data,size);if(status_write_errno)errno=EIO;return result;
}
static void status_thread_prepare(thr_t *thread) {
    memset(thread,0,sizeof *thread);assert(!pthread_cond_init(&thread->cv,NULL));
    pthread_mutex_lock(&G);thread->handle=h_new(O_THREAD,thread);pthread_mutex_unlock(&G);
}
static void status_thread_finish(thr_t *thread) {
    assert(!thread->wkind&&!thread->next&&!pthread_cond_destroy(&thread->cv));
    pthread_mutex_lock(&G);unsigned slot=thread->handle-0x1000;
    assert(g_h[slot].type==O_THREAD&&g_h[slot].obj==thread);
    memset(&g_h[slot],0,sizeof g_h[slot]);pthread_mutex_unlock(&G);
}
typedef struct {thr_t thread;int number;uint64_t frame[31],before[31],returned;int saved_errno;} status_wait_job;
static void *status_wait_worker(void *context) {
    status_wait_job *job=context;assert(!pthread_setspecific(g_keythr,&job->thread));
    errno=ENOTTY;job->returned=svc_dispatch(job->number,job->frame,0);job->saved_errno=errno;
    assert(!pthread_setspecific(g_keythr,NULL));return NULL;
}
static void status_wait_until_linked(thr_t *thread,int kind) {
    uint64_t deadline=now_ns()+UINT64_C(2000000000);
    for(;;) {
        pthread_mutex_lock(&G);int linked=thread->wkind==kind&&g_wait==thread;pthread_mutex_unlock(&G);
        if(linked)return;
        assert(now_ns()<deadline);sched_yield();
    }
}
static void status_wait_success(int number) {
    status_wait_job job={.number=number};status_thread_prepare(&job.thread);
    uint32_t *word=(uint32_t *)(g_heap+2*MIB),*key=word+1;
    *word=number==0x1c?job.thread.handle:7;*key=0;
    for(unsigned i=0;i<31;i++)job.frame[i]=UINT64_C(0xfeed0000)+i;
    job.frame[0]=(uintptr_t)word;job.frame[1]=number==0x1c?(uintptr_t)key:2;
    job.frame[2]=number==0x1c?job.thread.handle:7;job.frame[3]=UINT64_MAX;
    memcpy(job.before,job.frame,sizeof job.frame);pthread_t worker;
    assert(!pthread_create(&worker,NULL,status_wait_worker,&job));
    status_wait_until_linked(&job.thread,number==0x1c?2:3);
    uint64_t signal[31]={0};signal[0]=(uintptr_t)(number==0x1c?key:word);
    signal[1]=number==0x1c?UINT64_MAX:0;signal[3]=1;
    assert(!svc_dispatch(number==0x1c?0x1d:0x35,signal,0)&&!signal[0]);
    assert(!pthread_join(worker,NULL)&&!job.returned&&!job.frame[0]&&job.saved_errno==ENOTTY);
    assert(!memcmp(job.frame+1,job.before+1,sizeof job.frame-sizeof *job.frame)&&!g_wait);
    if(number==0x1c)assert(*word==job.thread.handle&&!*key);
    status_thread_finish(&job.thread);
}
static void status_wait_errors(int number) {
    thr_t self;status_thread_prepare(&self);assert(!pthread_setspecific(g_keythr,&self));
    uint32_t *word=(uint32_t *)(g_heap+2*MIB),*key=word+1;
    for(unsigned n=0;n<3;n++) {
        uint64_t frame[31],before[31];for(unsigned i=0;i<31;i++)frame[i]=UINT64_C(0xfeed0000)+i;
        *word=number==0x1c?self.handle:7;*key=0;
        frame[0]=(uintptr_t)word+(n==0?1:0);
        frame[1]=number==0x1c?(uintptr_t)key:2;
        frame[2]=number==0x1c?self.handle:(n==1?8:7);frame[3]=0;
        memcpy(before,frame,sizeof frame);errno=ENOTTY;
        assert(!svc_dispatch(number,frame,0)&&errno==ENOTTY);
        assert(frame[0]==(n==0?0xd401u:number==0x34&&n==1?R_BAD_STATE:R_TIMEOUT));
        assert(!memcmp(frame+1,before+1,sizeof frame-sizeof *frame)&&!self.wkind&&!g_wait);
        if(number==0x1c&&n)assert(!*word&&*key==1);
    }
    assert(!pthread_setspecific(g_keythr,NULL));status_thread_finish(&self);
}
int main(int argc,char **argv) {
    CHECK(argc==2);CHECK(!initialize());status_lock_checks=1;
    if(!strncmp(argv[1],"wait-",5)) {
        status_write_errno=1;int condition=!strncmp(argv[1],"wait-key-",9);
        if(!strcmp(argv[1],condition?"wait-key-ok":"wait-address-ok"))status_wait_success(condition?0x1c:0x34);
        else {assert(!strcmp(argv[1],condition?"wait-key-error":"wait-address-error"));status_wait_errors(condition?0x1c:0x34);}
        return 0;
    }
    uint64_t frame[31],before[31];for(unsigned i=0;i<31;i++)frame[i]=UINT64_C(0xfeed0000)+i;
    if(!strcmp(argv[1],"map")) {
        size_t page=(size_t)getpagesize();frame[0]=0;frame[1]=(uintptr_t)(g_heap+2*MIB);frame[2]=page;
        memcpy(before,frame,sizeof frame);errno=EINTR;
        CHECK(svc_dispatch(4,frame,0)==0&&frame[0]==0xd401&&errno==EINTR);
        CHECK(!memcmp(frame+1,before+1,sizeof frame-sizeof *frame)&&!g_nmap);
        CHECK(svc_dispatch(4,frame,0)==0&&frame[0]==0xd401); /* Same outcome is bounded. */
        uint8_t *source=g_heap+2*MIB,*destination=g_stack+page;source[0]=0x71;
        frame[0]=(uintptr_t)destination;frame[1]=(uintptr_t)source;frame[2]=page;
        memcpy(before,frame,sizeof frame);errno=ENOTTY;
        CHECK(svc_dispatch(4,frame,0)==0&&!frame[0]&&errno==ENOTTY&&g_nmap==1&&destination[0]==0x71);
        CHECK(!memcmp(frame+1,before+1,sizeof frame-sizeof *frame));
        frame[0]=(uintptr_t)destination;frame[1]=(uintptr_t)source;frame[2]=page;
        CHECK(svc_dispatch(5,frame,0)==0&&!frame[0]&&!g_nmap&&source[0]==0x71);
        CHECK(dispatch_calls==4);
    } else {
        CHECK(!strcmp(argv[1],"excluded"));
        const int excluded[]={0x7f,0x0a,0x0b,0x10,0x1e,0x26,0x27};
        for(unsigned i=0;i<sizeof excluded/sizeof *excluded;i++) {
            memcpy(before,frame,sizeof frame);CHECK(svc_dispatch(excluded[i],frame,0)==0x5a);
            CHECK(!frame[0]&&frame[1]==0x1234&&!memcmp(frame+2,before+2,sizeof frame-2*sizeof *frame));
        }
    }
    return 0;
}
#elif defined(OWNED_STATUS_HEAP)
#include "base_v0_heap.c"
#define MIB ((size_t)1024*1024)
static pthread_key_t heap_key,root_key;
static unsigned query_calls;
static int query_failure,check_plan_lock;
static int owned_query(botw_base_v0_heap_inputs *out,char *message,size_t capacity) {
    (void)message;(void)capacity;query_calls++;
    if(query_failure)return -1;
    *out=(botw_base_v0_heap_inputs){640*MIB,896*MIB,4*MIB};return 0;
}
ssize_t owned_status_write(int fd,const void *data,size_t size) {
    if(check_plan_lock){assert(!pthread_mutex_trylock(&plan_lock));pthread_mutex_unlock(&plan_lock);}
    return write(fd,data,size);
}
static void *initialize_owned_thread(void *unused) {
    (void)unused;char message[256];
    if(botw_base_v0_heap_thread_init(heap_key,root_key,owned_query,message,sizeof message))return (void*)1;
    if((uintptr_t)pthread_getspecific(heap_key)!=640*MIB||(uintptr_t)pthread_getspecific(root_key)!=608*MIB)return (void*)1;
    return NULL;
}
int main(int argc,char **argv) {
    assert(argc==2&&!pthread_key_create(&heap_key,NULL)&&!pthread_key_create(&root_key,NULL));
    char message[256];
    if(!strcmp(argv[1],"heap-parallel")) {
        pthread_t threads[16];for(unsigned i=0;i<16;i++)assert(!pthread_create(&threads[i],NULL,initialize_owned_thread,NULL));
        for(unsigned i=0;i<16;i++){void *result=(void*)1;assert(!pthread_join(threads[i],&result)&&!result);}
        assert(query_calls==1);
    } else {
        if(!strcmp(argv[1],"heap-query-retry"))query_failure=1;
        else {assert(!strcmp(argv[1],"heap-key-retry"));assert(!pthread_setspecific(heap_key,(void*)13)&&!pthread_key_delete(root_key));}
        assert(botw_base_v0_heap_thread_init(heap_key,root_key,owned_query,message,sizeof message));
        assert(!selected&&query_calls==1);
        if(query_failure)query_failure=0;
        else {assert(pthread_getspecific(heap_key)==(void*)13);assert(!pthread_key_create(&root_key,NULL));}
        check_plan_lock=1;assert(!initialize_owned_thread(NULL));check_plan_lock=0;
        for(unsigned i=0;i<4;i++)assert(!initialize_owned_thread(NULL));
        assert(query_calls==2);
    }
    return 0;
}
#else
static _Atomic unsigned writes;
static int fail_write;
ssize_t owned_status_write(int fd,const void *data,size_t size) {
    atomic_fetch_add(&writes,1);
    if(fail_write){errno=EBADF;return -1;}
    return write(fd,data,size);
}
#if !defined(BOTW_NORMAL_API_STATUS_BASELINE)
static void *repeat_owned(void *unused) {
    (void)unused;
    for(unsigned i=0;i<100;i++) {
        errno=EINTR;botw_whole_host_api_result(BOTW_HOST_API_MAP_MEMORY,BOTW_HOST_API_OK);
        if(errno!=EINTR)return (void*)1;
        botw_whole_host_api_result(BOTW_HOST_API_MAP_MEMORY,BOTW_HOST_API_ERROR);
        if(errno!=EINTR)return (void*)1;
    }
    return NULL;
}
#endif
int main(int argc,char **argv) {
    assert(argc==2);
#if !defined(BOTW_NORMAL_API_STATUS_BASELINE)
    if(!strcmp(argv[1],"enum")) {
        for(unsigned i=0;i<BOTW_HOST_API_COUNT;i++)for(unsigned n=0;n<3;n++) {
            errno=EAGAIN;botw_whole_host_api_result((botw_whole_host_api)i,BOTW_HOST_API_OK);assert(errno==EAGAIN);
            botw_whole_host_api_result((botw_whole_host_api)i,BOTW_HOST_API_ERROR);assert(errno==EAGAIN);
        }
        assert(atomic_load(&writes)==2*BOTW_HOST_API_COUNT);
    } else if(!strcmp(argv[1],"appended-api")) {
        const botw_whole_host_api original[]={
            BOTW_HOST_API_SET_HEAP_SIZE,BOTW_HOST_API_SET_MEMORY_PERMISSION,
            BOTW_HOST_API_SET_MEMORY_ATTRIBUTE,BOTW_HOST_API_MAP_MEMORY,
            BOTW_HOST_API_UNMAP_MEMORY,BOTW_HOST_API_QUERY_MEMORY,
            BOTW_HOST_API_CREATE_THREAD,BOTW_HOST_API_START_THREAD,
            BOTW_HOST_API_CREATE_TRANSFER_MEMORY,BOTW_HOST_API_CLOSE_HANDLE,
            BOTW_HOST_API_WAIT_SYNCHRONIZATION,BOTW_HOST_API_GET_INFO,
            BOTW_HOST_API_CONNECT_TO_NAMED_PORT,BOTW_HOST_API_SEND_SYNC_REQUEST};
        for(unsigned i=0;i<sizeof original/sizeof *original;i++)assert((unsigned)original[i]==i);
        assert((unsigned)BOTW_HOST_API_NVN_MEMORY_POOL_INITIALIZE==14);
        for(unsigned i=0;i<3;i++) {
            errno=ENOTTY;
            botw_whole_host_api_result((botw_whole_host_api)14,BOTW_HOST_API_OK);
            botw_whole_host_api_result((botw_whole_host_api)14,BOTW_HOST_API_ERROR);
            botw_whole_host_api_result(BOTW_HOST_API_COUNT,BOTW_HOST_API_OK);
            botw_whole_host_api_result((botw_whole_host_api)14,(botw_whole_host_api_outcome)2);
            assert(errno==ENOTTY);
        }
        assert(atomic_load(&writes)==2);
    } else if(!strcmp(argv[1],"early-graphics-apis")) {
        const botw_whole_host_api original[]={
            BOTW_HOST_API_SET_HEAP_SIZE,BOTW_HOST_API_SET_MEMORY_PERMISSION,
            BOTW_HOST_API_SET_MEMORY_ATTRIBUTE,BOTW_HOST_API_MAP_MEMORY,
            BOTW_HOST_API_UNMAP_MEMORY,BOTW_HOST_API_QUERY_MEMORY,
            BOTW_HOST_API_CREATE_THREAD,BOTW_HOST_API_START_THREAD,
            BOTW_HOST_API_CREATE_TRANSFER_MEMORY,BOTW_HOST_API_CLOSE_HANDLE,
            BOTW_HOST_API_WAIT_SYNCHRONIZATION,BOTW_HOST_API_GET_INFO,
            BOTW_HOST_API_CONNECT_TO_NAMED_PORT,BOTW_HOST_API_SEND_SYNC_REQUEST,
            BOTW_HOST_API_NVN_MEMORY_POOL_INITIALIZE};
        for(unsigned i=0;i<sizeof original/sizeof *original;i++)assert((unsigned)original[i]==i);
        const botw_whole_host_api appended[]={
            BOTW_HOST_API_NVN_QUEUE_ACQUIRE_TEXTURE,
            BOTW_HOST_API_NVN_COMMAND_BUFFER_INITIALIZE};
        assert((unsigned)appended[0]==15&&(unsigned)appended[1]==16);
        for(unsigned i=0;i<sizeof appended/sizeof *appended;i++)for(unsigned n=0;n<3;n++) {
            errno=ENOTTY;
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_OK);
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_ERROR);
            botw_whole_host_api_result(appended[i],(botw_whole_host_api_outcome)2);
            assert(errno==ENOTTY);
        }
        botw_whole_host_api_result(BOTW_HOST_API_COUNT,BOTW_HOST_API_OK);
        assert(atomic_load(&writes)==4);
    } else if(!strcmp(argv[1],"buffer-wait-apis")) {
        assert((unsigned)BOTW_HOST_API_NVN_COMMAND_BUFFER_INITIALIZE==16);
        const botw_whole_host_api appended[]={BOTW_HOST_API_NVN_BUFFER_INITIALIZE,
            BOTW_HOST_API_WAIT_PROCESS_WIDE_KEY_ATOMIC,BOTW_HOST_API_WAIT_FOR_ADDRESS};
        for(unsigned i=0;i<sizeof appended/sizeof *appended;i++)assert((unsigned)appended[i]==17+i);
        assert(BOTW_HOST_API_COUNT==23);
        for(unsigned i=0;i<sizeof appended/sizeof *appended;i++)for(unsigned n=0;n<3;n++) {
            errno=ENOTTY;
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_OK);
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_ERROR);
            botw_whole_host_api_result(appended[i],(botw_whole_host_api_outcome)2);
            assert(errno==ENOTTY);
        }
        botw_whole_host_api_result(BOTW_HOST_API_COUNT,BOTW_HOST_API_OK);
        assert(atomic_load(&writes)==6&&BOTW_HOST_API_COUNT==23);
    } else if(!strcmp(argv[1],"account-apis")) {
        assert((unsigned)BOTW_HOST_API_WAIT_FOR_ADDRESS==19);
        const botw_whole_host_api appended[]={BOTW_HOST_API_ACCOUNT_OPEN_PRESELECTED_USER,
            BOTW_HOST_API_ACCOUNT_GET_USER_ID,BOTW_HOST_API_ACCOUNT_LIST_ALL_USERS};
        for(unsigned i=0;i<sizeof appended/sizeof *appended;i++)assert((unsigned)appended[i]==20+i);
        assert(BOTW_HOST_API_COUNT==23);
        for(unsigned i=0;i<sizeof appended/sizeof *appended;i++)for(unsigned n=0;n<3;n++) {
            errno=ENOTTY;
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_OK);
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_ERROR);
            botw_whole_host_api_result(appended[i],(botw_whole_host_api_outcome)2);
            assert(errno==ENOTTY);
        }
        botw_whole_host_api_result(BOTW_HOST_API_COUNT,BOTW_HOST_API_OK);
        assert(atomic_load(&writes)==6);
    } else if(!strcmp(argv[1],"invalid")) {
        errno=ENOTTY;botw_whole_host_api_result((botw_whole_host_api)-1,BOTW_HOST_API_OK);
        botw_whole_host_api_result(BOTW_HOST_API_COUNT,BOTW_HOST_API_ERROR);
        botw_whole_host_api_result(BOTW_HOST_API_MAP_MEMORY,(botw_whole_host_api_outcome)-1);
        botw_whole_host_api_result(BOTW_HOST_API_MAP_MEMORY,(botw_whole_host_api_outcome)2);
        botw_whole_host_heap_capacities(0,1);botw_whole_host_heap_capacities(1,0);
        botw_whole_host_heap_capacities(1,1);botw_whole_host_heap_capacities(1,2);
        assert(errno==ENOTTY&&!atomic_load(&writes));
    } else if(!strcmp(argv[1],"write-failure")) {
        fail_write=1;errno=EINTR;
        botw_whole_host_api_result(BOTW_HOST_API_MAP_MEMORY,BOTW_HOST_API_OK);
        botw_whole_host_api_result(BOTW_HOST_API_MAP_MEMORY,BOTW_HOST_API_OK);
        assert(errno==EINTR&&atomic_load(&writes)==1);
        botw_whole_host_heap_capacities((size_t)640*1024*1024,(size_t)608*1024*1024);
        botw_whole_host_heap_capacities((size_t)640*1024*1024,(size_t)608*1024*1024);
        assert(errno==EINTR&&atomic_load(&writes)==2);
    } else {
        assert(!strcmp(argv[1],"race"));pthread_t threads[16];
        for(unsigned i=0;i<16;i++)assert(!pthread_create(&threads[i],NULL,repeat_owned,NULL));
        for(unsigned i=0;i<16;i++){void *result=(void*)1;assert(!pthread_join(threads[i],&result)&&!result);}
        assert(atomic_load(&writes)==2);
    }
#else
    (void)argv;(void)fail_write;
#endif
    return 0;
}
#endif
