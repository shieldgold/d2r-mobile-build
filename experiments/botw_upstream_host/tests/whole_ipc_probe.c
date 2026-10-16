/* Owned normal SVC/service integration only; no guest/renderer/GPU executes. */
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/mman.h>
#include <unistd.h>
#include "hle_thread_core.h"
#include "whole_kernel.h"
#include "services.h"
#ifdef __APPLE__
#include "protected_copy.h"
#endif
#ifndef OWNED_IPC_BASELINE
#include "whole_ipc.h"
#endif
typedef uint32_t mach_port_t; /* Unexecuted original thread metadata only. */
#define H_CUR_THREAD UINT32_C(0xffff8000)
#define LOG(...) ((void)0)
static sigjmp_buf failure_jump;
static int expect_fatal, fatal_count;
static char fatal_message[512];
_Noreturn static void die(const char *format, ...) {
    va_list args; va_start(args,format); vsnprintf(fatal_message,sizeof fatal_message,format,args); va_end(args);
    fatal_count++;
    if(expect_fatal) siglongjmp(failure_jump,1);
    fprintf(stderr,"unexpected owned normal error: %s\n",fatal_message);abort();
}
#include "original_kernel_registry.inc"
#define R_BAD_HANDLE UINT32_C(0xe401)
static int svc_thread(int number,uint64_t *regs) {
    switch(number) {
#include "original_close_case.inc"
    }
    return 0;
}
static uint64_t owned_svc_dispatch(int num,uint64_t *regs) {
#include "dispatch_prefix.inc"
    return UINT64_MAX;
}
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"WHOLE_IPC_CHECK %d: %s\n",__LINE__,#x);return 91; } } while(0)
static char error[512];
#define E error,sizeof error

typedef struct { uintptr_t address;size_t size;int writable; } owned_span;
static owned_span spans[32];
static pthread_mutex_t memory_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_key_t tlr_key,elf_key;
static atomic_uint read_calls,tlr_calls;
static atomic_int read_failure,tlr_failure,block_read,read_entered,release_read;
static int memory(void *opaque,uint64_t address,size_t size,int writable) {
    (void)opaque;int found=0;
    pthread_mutex_lock(&memory_lock);
    for(unsigned i=0;i<32;i++) if(spans[i].address&&address>=spans[i].address&&size<=spans[i].size&&
        address-spans[i].address<=spans[i].size-size&&(!writable||spans[i].writable)) found=1;
    pthread_mutex_unlock(&memory_lock);return found;
}
static int add_span(void *address,size_t size,int writable) {
    pthread_mutex_lock(&memory_lock);
    for(unsigned i=0;i<32;i++) if(!spans[i].address) {
        spans[i]=(owned_span){(uintptr_t)address,size,writable};pthread_mutex_unlock(&memory_lock);return 0;
    }
    pthread_mutex_unlock(&memory_lock);return -1;
}
static void remove_span(void *address) {
    pthread_mutex_lock(&memory_lock);
    for(unsigned i=0;i<32;i++) if(spans[i].address==(uintptr_t)address) memset(&spans[i],0,sizeof spans[i]);
    pthread_mutex_unlock(&memory_lock);
}
static int normal_read(void *opaque,uint64_t source,void *out,size_t size,char *message,size_t capacity) {
    (void)opaque;atomic_fetch_add(&read_calls,1);
    if(atomic_load(&read_failure)||!memory(NULL,source,size,0)) {
        if(message&&capacity)snprintf(message,capacity,"owned logical read denied");return -1;
    }
    if(atomic_load(&block_read)) {
        atomic_store(&read_entered,1);
        while(!atomic_load(&release_read)) { struct timespec pause={0,100000};nanosleep(&pause,NULL); }
    }
#ifdef __APPLE__
    if(botw_protected_vm_read(out,(const void*)(uintptr_t)source,size)!=KERN_SUCCESS) {
        if(message&&capacity)snprintf(message,capacity,"owned protected read failed");return -1;
    }
#else
    /* Fixture spans are owned stable host allocations; no arbitrary raw address. */
    memcpy(out,(const void*)(uintptr_t)source,size);
#endif
    return 0;
}
static int normal_write(void *opaque,uint64_t destination,const void *source,size_t size,char *message,size_t capacity) {
    (void)opaque;
    if(!memory(NULL,destination,size,1)) {
        if(message&&capacity)snprintf(message,capacity,"owned logical write denied");return -1;
    }
#ifdef __APPLE__
    if(botw_protected_vm_write((void*)(uintptr_t)destination,source,size)!=KERN_SUCCESS) {
        if(message&&capacity)snprintf(message,capacity,"owned protected write failed");return -1;
    }
#else
    memcpy((void*)(uintptr_t)destination,source,size);
#endif
    return 0;
}
#ifndef OWNED_IPC_BASELINE
static int current_tlr(void *opaque,void **out,size_t *capacity,char *message,size_t message_capacity) {
    (void)opaque;atomic_fetch_add(&tlr_calls,1);
    if(atomic_load(&tlr_failure)||!pthread_getspecific(tlr_key)) {
        if(message&&message_capacity)snprintf(message,message_capacity,"owned current TLR unavailable");return -1;
    }
    *out=pthread_getspecific(tlr_key);*capacity=0x200;return 0;
}
static int bind_services(botw_services *services) {
    botw_whole_ipc_configuration configuration={services,NULL,normal_read,current_tlr};
    return botw_whole_ipc_bind(&configuration,E);
}
#endif
static botw_services *make_services(void) {
    botw_services *services=botw_services_create(NULL,memory,NULL);
    if(services)botw_services_set_memory_copiers(services,normal_read,normal_write,NULL);
    return services;
}
static unsigned live(void) {
    unsigned count=0;pthread_mutex_lock(&G);
    for(unsigned i=1;i<MAX_H;i++)count+=g_h[i].type!=O_FREE;
    pthread_mutex_unlock(&G);return count;
}
static void put32(unsigned char *p,size_t at,uint32_t value) {memcpy(p+at,&value,4);}
static uint32_t get32(const unsigned char *p,size_t at) {uint32_t value;memcpy(&value,p+at,4);return value;}
static void request(unsigned char *ipc,uint32_t command,int pid,const void *payload,size_t size,unsigned type) {
    memset(ipc,0,256);put32(ipc,0,type);put32(ipc,4,(uint32_t)((32+size+3)/4)|(pid?0x80000000u:0));
    size_t offset=16;if(pid){put32(ipc,8,1);offset=32;}
    put32(ipc,offset,0x49434653);put32(ipc,offset+8,command);
    if(size)memcpy(ipc+offset+16,payload,size);
}
static const char port[]="sm:";
static int connect_port(uint32_t *handle) {
    uint64_t regs[31]={0};regs[0]=0x1234;regs[1]=(uintptr_t)port;
    CHECK(owned_svc_dispatch(0x1f,regs)==0&&regs[0]==0);
    *handle=(uint32_t)regs[1];CHECK(botw_whole_kernel_handle_kind(*handle)==BOTW_WHOLE_HANDLE_TRANSPORT);return 0;
}

#ifdef OWNED_IPC_BASELINE
int main(int argc,char **argv) {
    (void)argc;(void)argv;botw_services *services=make_services();CHECK(services);
    botw_services_transport_api api={NULL,botw_whole_kernel_transport_allocate,botw_whole_kernel_transport_release};
    CHECK(!botw_services_set_transport_api(services,&api,E));uint32_t sm=0;
    CHECK(!botw_services_connect(services,port,3,&sm,E));uint64_t regs[31]={sm};
    CHECK(owned_svc_dispatch(0x16,regs)==0&&regs[0]==0);
    if(botw_whole_kernel_handle_kind(sm)!=BOTW_WHOLE_HANDLE_NONE) {
        fprintf(stderr,"original CloseHandle falsely returned success while SM transport stayed live\n");return 91;
    }
    return 0;
}
#else
static int send(uint32_t handle,unsigned char *ipc,int tls) {
    uint64_t regs[31]={0};
    if(tls)regs[0]=handle;else {regs[0]=(uintptr_t)ipc;regs[1]=256;regs[2]=handle;}
    CHECK(owned_svc_dispatch(tls?0x21:0x22,regs)==0&&regs[0]==0);return 0;
}
static int close_handle(uint32_t handle) {
    uint64_t regs[31]={handle};CHECK(owned_svc_dispatch(0x16,regs)==0&&regs[0]==0);return 0;
}
static int protocol(unsigned char *ipc,int tls) {
    uint32_t sm;CHECK(!connect_port(&sm));uint64_t zero=0;
    request(ipc,0,1,&zero,8,4);CHECK(!send(sm,ipc,tls));CHECK(get32(ipc,16)==0x4f434653&&!get32(ipc,24));
    request(ipc,1,0,"lm\0\0\0\0\0\0",8,4);CHECK(!send(sm,ipc,tls));uint32_t lm=get32(ipc,12);
    request(ipc,0,1,&zero,8,4);CHECK(!send(lm,ipc,tls));uint32_t logger=get32(ipc,12);
    request(ipc,2,0,NULL,0,5);CHECK(!send(logger,ipc,tls));uint32_t clone=get32(ipc,12);
    CHECK(logger!=clone&&!close_handle(logger));
    request(ipc,1,0,"\1\0\0\0",4,4);CHECK(!send(clone,ipc,tls));CHECK(!get32(ipc,24));
    CHECK(!close_handle(clone)&&!close_handle(lm)&&!close_handle(sm));return 0;
}
static void *thread_protocol(void *unused) {
    (void)unused;unsigned char tlr[0x200]={0},elf[8192];memset(elf,0xa5,sizeof elf);
    if(add_span(tlr,sizeof tlr,1))return (void*)1;
    pthread_setspecific(tlr_key,tlr);pthread_setspecific(elf_key,elf);
    int status=protocol(tlr,1);for(size_t i=0;i<sizeof elf;i++)if(elf[i]!=0xa5)status=1;
    pthread_setspecific(tlr_key,NULL);pthread_setspecific(elf_key,NULL);remove_span(tlr);return (void*)(uintptr_t)status;
}
static botw_services *unbind_target;
static atomic_int unbound;
static void *unbind_thread(void *unused) {
    (void)unused;char message[128];
    int status=botw_whole_ipc_unbind(unbind_target,message,sizeof message);
    atomic_store(&unbound,status?-1:1);return NULL;
}
static void *connecting_thread(void *unused) {
    (void)unused;uint32_t handle;return (void*)(uintptr_t)connect_port(&handle);
}
int main(int argc,char **argv) {
    CHECK(argc==2);CHECK(!pthread_key_create(&tlr_key,NULL)&&!pthread_key_create(&elf_key,NULL));
    CHECK(!add_span((void*)port,sizeof port,0));
    unsigned char tlr[0x200]={0},elf[8192];memset(elf,0xa5,sizeof elf);
    CHECK(!add_span(tlr,sizeof tlr,1));pthread_setspecific(tlr_key,tlr);pthread_setspecific(elf_key,elf);
    if(!strcmp(argv[1],"unbound")) {
        int token=0;uint32_t handle;CHECK(!botw_whole_kernel_transport_allocate(NULL,&token,&handle,E));
        uint64_t regs[31],before[31];for(unsigned i=0;i<31;i++)regs[i]=0xfeed0000+i;regs[0]=handle;memcpy(before,regs,sizeof regs);
        CHECK(botw_whole_ipc_dispatch(0x21,regs,E)==-1&&!memcmp(regs,before,sizeof regs));
        CHECK(!atomic_load(&read_calls)&&!atomic_load(&tlr_calls));
        CHECK(!botw_whole_kernel_transport_release(NULL,&token,handle,E));
    } else {
        botw_services *services=make_services();CHECK(services);CHECK(!bind_services(services));
        if(!strcmp(argv[1],"protocol")) {CHECK(!protocol(tlr,1)&&!protocol(tlr,0));CHECK(live()==0);}
        else if(!strcmp(argv[1],"parallel-tlr")) {
            pthread_t workers[8];for(unsigned i=0;i<8;i++)CHECK(!pthread_create(&workers[i],NULL,thread_protocol,NULL));
            for(unsigned i=0;i<8;i++){void *result;CHECK(!pthread_join(workers[i],&result)&&!result);}CHECK(live()==0);
        } else if(!strcmp(argv[1],"close")) {
            uint32_t write,read;CHECK(!botw_whole_kernel_create_event(NULL,0,&write,&read,E));CHECK(!close_handle(read)&&!close_handle(write));
            thr_t thread={0};pthread_mutex_lock(&G);uint32_t handle=h_new(O_THREAD,&thread);pthread_mutex_unlock(&G);
            uint64_t regs[31]={handle};CHECK(botw_whole_ipc_dispatch(0x16,regs,E)==0&&regs[0]==handle);
            CHECK(owned_svc_dispatch(0x16,regs)==0&&thread.closed&&botw_whole_kernel_handle_kind(handle)==BOTW_WHOLE_HANDLE_NONE);
            regs[0]=handle;CHECK(owned_svc_dispatch(0x16,regs)==R_BAD_HANDLE&&regs[0]==R_BAD_HANDLE);
            regs[0]=0;CHECK(owned_svc_dispatch(0x16,regs)==R_BAD_HANDLE);
        } else if(!strcmp(argv[1],"invalid")) {
            uint64_t regs[31];for(unsigned i=0;i<31;i++)regs[i]=0xfeed0000+i;
            regs[0]=0x123;uint64_t before[31];memcpy(before,regs,sizeof regs);
            CHECK(botw_whole_ipc_dispatch(0x21,regs,E)==1&&regs[0]==R_BAD_HANDLE&&!memcmp(regs+1,before+1,sizeof regs-8));
            CHECK(!atomic_load(&tlr_calls)&&!atomic_load(&read_calls));
            CHECK(botw_whole_ipc_dispatch(0x15,regs,E)==0);
        } else if(!strcmp(argv[1],"read-failure")||!strcmp(argv[1],"too-long")) {
            char long_port[16];memset(long_port,'x',sizeof long_port);CHECK(!add_span(long_port,sizeof long_port,0));
            uint64_t regs[31]={0};regs[1]=(uintptr_t)(!strcmp(argv[1],"too-long")?long_port:port);uint64_t before[31];memcpy(before,regs,sizeof regs);
            if(!strcmp(argv[1],"read-failure"))atomic_store(&read_failure,1);
            CHECK(botw_whole_ipc_dispatch(0x1f,regs,E)==-1&&!memcmp(regs,before,sizeof regs)&&live()==0);
            if(!strcmp(argv[1],"too-long"))CHECK(atomic_load(&read_calls)==16);
            atomic_store(&read_failure,0);remove_span(long_port);
        } else if(!strcmp(argv[1],"send-failure")||!strcmp(argv[1],"tls-failure")||!strcmp(argv[1],"bounds")) {
            uint32_t handle;CHECK(!connect_port(&handle));uint64_t regs[31]={handle};uint64_t before[31];
            int number=0x21;
            if(!strcmp(argv[1],"tls-failure"))atomic_store(&tlr_failure,1);
            else if(!strcmp(argv[1],"bounds")){number=0x22;regs[0]=(uintptr_t)tlr;regs[1]=257;regs[2]=handle;}
            else memset(tlr,0,256);
            memcpy(before,regs,sizeof regs);CHECK(botw_whole_ipc_dispatch(number,regs,E)==-1&&!memcmp(regs,before,sizeof regs));
            atomic_store(&tlr_failure,0);CHECK(!close_handle(handle));
        } else if(!strcmp(argv[1],"protected-read")) {
#ifdef __APPLE__
            size_t size=(size_t)getpagesize();void *page=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
            CHECK(page!=MAP_FAILED&&!add_span(page,size,1));CHECK(!mprotect(page,size,PROT_NONE));
            uint64_t regs[31]={0},before[31];regs[1]=(uintptr_t)page;memcpy(before,regs,sizeof regs);
            CHECK(botw_whole_ipc_dispatch(0x1f,regs,E)==-1&&!memcmp(regs,before,sizeof regs));
            CHECK(!strcmp(error,"owned protected read failed")&&live()==0);
            CHECK(!mprotect(page,size,PROT_READ|PROT_WRITE));remove_span(page);CHECK(!munmap(page,size));
#else
            return 77;
#endif
        } else if(!strcmp(argv[1],"hook-failure")) {
            uint64_t regs[31]={0},before[31];regs[1]=(uintptr_t)port;memcpy(before,regs,sizeof regs);atomic_store(&read_failure,1);expect_fatal=1;
            if(!sigsetjmp(failure_jump,1)){owned_svc_dispatch(0x1f,regs);CHECK(0);}
            expect_fatal=0;CHECK(fatal_count==1&&!memcmp(regs,before,sizeof regs));
            CHECK(!strcmp(fatal_message,"host IPC service failed: owned logical read denied"));atomic_store(&read_failure,0);
        } else if(!strcmp(argv[1],"binding")) {
            botw_services *other=make_services();CHECK(other);CHECK(bind_services(other));CHECK(botw_whole_ipc_unbind(other,E));CHECK(!botw_services_destroy_checked(other,E));
        } else if(!strcmp(argv[1],"bind-failure")) {
            CHECK(!botw_whole_ipc_unbind(services,E));CHECK(!botw_services_destroy_checked(services,E));services=NULL;
            botw_services *selected=make_services();CHECK(selected);
            botw_services_transport_api api={NULL,botw_whole_kernel_transport_allocate,botw_whole_kernel_transport_release};
            CHECK(!botw_services_set_transport_api(selected,&api,E));CHECK(bind_services(selected));
            uint64_t regs[31]={0},before[31];regs[1]=(uintptr_t)port;memcpy(before,regs,sizeof regs);
            CHECK(botw_whole_ipc_dispatch(0x1f,regs,E)==-1&&!memcmp(regs,before,sizeof regs));
            CHECK(!atomic_load(&read_calls)&&!atomic_load(&tlr_calls));CHECK(!botw_services_destroy_checked(selected,E));
            botw_services *fresh=make_services();CHECK(fresh&&!bind_services(fresh));
            CHECK(!botw_whole_ipc_unbind(fresh,E)&&!botw_services_destroy_checked(fresh,E));
        } else if(!strcmp(argv[1],"borrow")) {
            atomic_store(&block_read,1);pthread_t reader,closer;CHECK(!pthread_create(&reader,NULL,connecting_thread,NULL));
            while(!atomic_load(&read_entered)){struct timespec wait={0,100000};nanosleep(&wait,NULL);}
            unbind_target=services;CHECK(!pthread_create(&closer,NULL,unbind_thread,NULL));
            struct timespec wait={0,20000000};nanosleep(&wait,NULL);CHECK(!atomic_load(&unbound));
            atomic_store(&release_read,1);void *status;CHECK(!pthread_join(reader,&status)&&!status);CHECK(!pthread_join(closer,NULL)&&atomic_load(&unbound)==1);
            atomic_store(&block_read,0);CHECK(!botw_services_destroy_checked(services,E));services=NULL;
        } else return 64;
        if(services){CHECK(!botw_whole_ipc_unbind(services,E));CHECK(!botw_services_destroy_checked(services,E));}
    }
    for(size_t i=0;i<sizeof elf;i++)CHECK(elf[i]==0xa5);
    CHECK(live()==0);CHECK(!pthread_key_delete(tlr_key)&&!pthread_key_delete(elf_key));return 0;
}
#endif
