/* Owned normal API/TSD fixtures only. No Guest entry, GPU or device exists. */
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if defined(OWNED_STATUS_MAP)
#define main inherited_transfer_fixture_main
#include "whole_transfer_probe.c"
#undef main
static int status_lock_checks;
static unsigned dispatch_calls;
static uint64_t svc_dispatch_impl(int number,uint64_t *frame,uint64_t original) {
    (void)original;dispatch_calls++;
    if(number==4||number==5)return owned_memory_svc(number,frame);
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
    return write(fd,data,size);
}
int main(int argc,char **argv) {
    CHECK(argc==2);CHECK(!initialize());status_lock_checks=1;
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
        assert(BOTW_HOST_API_COUNT==17);
        for(unsigned i=0;i<sizeof appended/sizeof *appended;i++)for(unsigned n=0;n<3;n++) {
            errno=ENOTTY;
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_OK);
            botw_whole_host_api_result(appended[i],BOTW_HOST_API_ERROR);
            botw_whole_host_api_result(appended[i],(botw_whole_host_api_outcome)2);
            assert(errno==ENOTTY);
        }
        botw_whole_host_api_result(BOTW_HOST_API_COUNT,BOTW_HOST_API_OK);
        assert(atomic_load(&writes)==4);
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
