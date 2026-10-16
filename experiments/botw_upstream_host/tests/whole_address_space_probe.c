/* Owned normal virtual allocations only; no guest/host execution or diagnostics. */
#define _DARWIN_C_SOURCE
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#undef TARGET_OS_IOS
#define TARGET_OS_IOS 1
#undef TARGET_OS_SIMULATOR
#define TARGET_OS_SIMULATOR 0
#undef TARGET_OS_MACCATALYST
#define TARGET_OS_MACCATALYST 0
#define GIB (UINT64_C(1)<<30)
static unsigned fail_attempts,calls;
static size_t requests[16];
static void *retained;
static size_t retained_size;
static void *owned_mmap(void *address,size_t size,int protection,int flags,int fd,off_t offset){
    assert(calls<16);requests[calls++]=size;
    if(calls<=fail_attempts){errno=ENOMEM;return MAP_FAILED;}
    void *result=mmap(address,size,protection,flags,fd,offset);
    if(result!=MAP_FAILED){retained=result;retained_size=size;}
    return result;
}
static int expected_terminal_failure;
static void owned_fatal(void);
#define LOG(...) ((void)0)
#define die(...) owned_fatal()
#define mmap owned_mmap
#ifndef WHOLE_AS_BASELINE
#include "whole_address_space.c"
#endif
#include "memory_init.inc"
#undef mmap
static struct {size_t size;} g_mod[1]={{64u<<20}};
static int g_nmod=1;
#include "memory_pool.inc"
#include "base_v0_heap.h"
/* Controlled owned planner input, not a claim about this Mac/iPhone's headroom. */
int botw_base_v0_heap_live_available(size_t *out,char *error,size_t capacity){
    (void)error;(void)capacity;*out=2*GIB;return 0;
}
#include "memory_plan.inc"
#include "original_heap_plan.inc"
static void owned_fatal(void){
    if(expected_terminal_failure){
        assert(!g_as&&!g_heap&&!g_alias&&!g_stack&&!g_heap_cap&&!retained);
#ifndef WHOLE_AS_BASELINE
        assert(!g_as_size);
#endif
        puts("WHOLE_ADDRESS_SPACE_CPU_OK");exit(0);
    }
    fputs("owned initializer rejected original reservation\n",stderr);exit(91);
}
#include "memory_range.inc"
#include "memory_registry.inc"
#define CORE_PROCESS_MASK 15
static uint64_t dm_entropy64(void){return 0;}
static struct {uint64_t program_id;} owned_title={1};
static void *unused;
#define dm_title() (&owned_title)
static uint64_t svc(int num,uint64_t *regs){
    switch(num){
#include "memory_cases.inc"
    default:return 1;
    }
}
static uint64_t info(uint64_t id){uint64_t regs[4]={0,id,0,0};assert(!svc(0x29,regs));assert(!regs[0]);return regs[1];}
static void cleanup(void){assert(retained&&retained!=MAP_FAILED);assert(!munmap(retained,retained_size));retained=NULL;retained_size=0;}
static void check_metadata(size_t expected){
    uint8_t *base=NULL;size_t bytes=0;svc_guest_range(&base,&bytes);
    assert(base==retained&&bytes==expected&&retained_size==expected);
    assert(info(I_AslrAddr)==(uintptr_t)retained&&info(I_AslrSize)==expected);
    assert(info(I_HeapAddr)==(uintptr_t)retained+GIB&&info(I_HeapSize)==expected-GIB);
    assert(info(I_AliasAddr)==(uintptr_t)retained+0x10000000u&&info(I_AliasSize)==0x20000000u);
    assert(info(I_StackAddr)==(uintptr_t)retained+0x30000000u&&info(I_StackSize)==0x10000000u);
    size_t physical=APP_POOL+((size_t)512<<20),actual=expected-GIB+image_bytes();
    assert(info(I_TotalMem)==(physical<actual?physical:actual));
    assert(info(I_TotalNonSys)==info(I_TotalMem));
    assert(info(I_UsedMem)==image_bytes()&&info(I_UsedNonSys)==image_bytes());
    botw_base_v0_heap_inputs inputs;char error[256];size_t planned,root;
    assert(!dm_guest_heap_plan_inputs(&inputs,error,sizeof error));
    assert(inputs.kernel_available==info(I_TotalMem)-image_bytes());
    assert(inputs.kernel_available<=expected-GIB);
    assert(!botw_base_v0_heap_plan(inputs.kernel_available,inputs.live_available,&planned,&root,error,sizeof error));
    assert(planned<=g_heap_cap&&root==planned-(32u<<20)&&root>=(512u<<20));
    uint64_t output[5]={0},regs[4]={(uintptr_t)output,0,(uintptr_t)retained+expected-4096,0};
    assert(!svc(0x06,regs)&&!regs[0]&&((uint32_t*)output)[4]==0);
    assert(output[0]+output[1]==(uintptr_t)retained+expected);
    memset(output,0,sizeof output);regs[0]=(uintptr_t)output;regs[2]=(uintptr_t)retained+expected;
    assert(!svc(0x06,regs)&&((uint32_t*)output)[4]!=0);
}
static void *initialize(void *argument){(void)argument;mem_init();return NULL;}
int main(int argc,char **argv){
    assert(argc==2);const char *mode=argv[1];
    if(!strcmp(mode,"fallback")){fail_attempts=3;mem_init();assert(calls==4);check_metadata(2*GIB);cleanup();}
    else if(!strcmp(mode,"original")){mem_init();assert(calls==1);check_metadata(5*GIB);cleanup();}
    else if(!strcmp(mode,"init-failure")){fail_attempts=16;expected_terminal_failure=1;mem_init();assert(!"failed initializer returned");}
    else if(!strcmp(mode,"uninitialized")){uint8_t *base=(void*)1;size_t size=1;svc_guest_range(&base,&size);assert(!base&&!size&&!calls);}
#ifndef WHOLE_AS_BASELINE
    else if(!strcmp(mode,"mac-failure")||!strcmp(mode,"all-failure")){
        fail_attempts=16;botw_whole_address_space output={(void*)0x1234,5678},before=output;int error=0;
        assert(botw_whole_address_space_reserve(&output,!strcmp(mode,"all-failure"),&error)==-1);
        assert(output.base==before.base&&output.size==before.size&&error==ENOMEM);
        assert(calls==(!strcmp(mode,"all-failure")?4u:1u));
        for(unsigned i=0;i<calls;i++)assert(requests[i]==(5-i)*GIB);
    }else if(!strcmp(mode,"invalid")){
        botw_whole_address_space output={(void*)1,1};int error=0;
        assert(botw_whole_address_space_reserve(NULL,1,&error)==-1&&error==EINVAL);
        assert(botw_whole_address_space_reserve(&output,2,&error)==-1&&error==EINVAL);
        assert(!calls&&output.base==(void*)1&&output.size==1);
    }else if(!strcmp(mode,"small")){
        botw_whole_address_space output={0};int error=0;
        assert(!reserve_one(&output,65536,&error)&&output.base==retained&&output.size==65536);
        assert(!mprotect(output.base,16384,PROT_READ|PROT_WRITE));
        memset(output.base,0x5a,16384);assert(((unsigned char*)output.base)[16383]==0x5a);cleanup();
    }else if(!strcmp(mode,"parallel")){
        pthread_t threads[12];for(unsigned i=0;i<12;i++)assert(!pthread_create(threads+i,NULL,initialize,NULL));
        for(unsigned i=0;i<12;i++)assert(!pthread_join(threads[i],NULL));
        assert(calls==1);check_metadata(5*GIB);cleanup();
    }
#endif
    else assert(!"unknown owned case");
    puts("WHOLE_ADDRESS_SPACE_CPU_OK");return 0;
}
