/* Real extracted title/thread/layout/SVC code over owned sparse mappings. */
#include "dm.h"
#include "whole_policy.h"
#include "whole_address_space.h"
#include "whole_transfer_internal.h"
#include "base_v0_heap.h"
#include <TargetConditionals.h>
#include <errno.h>
#include <setjmp.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <unistd.h>
#define MIB ((size_t)1<<20)
#define GIB ((size_t)1<<30)
#define H ((size_t)0xc2600000)
#define R ((size_t)0xc0600000)
#define CHECK(x) do{if(!(x)){fprintf(stderr,"LOGICAL_CHECK:%d %s\n",__LINE__,#x);return 91;}}while(0)
#undef LOG
#define LOG(...) ((void)0)
static jmp_buf fatal;
static int expected_fatal,fatal_seen,fail_protect;
static unsigned reserve_calls,protect_calls;
static size_t chosen_gib=4,live_available=1024*MIB;
static void owned_fatal(const char *fmt,...){(void)fmt;fatal_seen=1;if(expected_fatal)longjmp(fatal,1);exit(92);}
#undef die
#define die(...) owned_fatal(__VA_ARGS__)
static void *owned_reserve(void *p,size_t n,int prot,int flags,int fd,off_t offset){
    reserve_calls++;if(n>chosen_gib*GIB){errno=ENOMEM;return MAP_FAILED;}
    return mmap(p,n,prot,flags,fd,offset);
}
#define mmap owned_reserve
#include "whole_address_space.c"
#undef mmap
static int owned_protect(void *p,size_t n,int prot){
    protect_calls++;if(fail_protect){fail_protect=0;errno=ENOMEM;return -1;}return mprotect(p,n,prot);
}
static int owned_live(size_t *out,char *error,size_t cap){(void)error;(void)cap;*out=live_available;return 0;}
#include "base_v0_heap.c"
pthread_key_t g_key18,g_keytls,g_keytlr,g_keygate,g_keyres[2],g_keyaspect,g_keyaspectui;
void dm_gate(void){abort();}
module_t g_mod[MAX_MOD];int g_nmod=1;
#include "thread.c"
#undef TARGET_OS_IOS
#define TARGET_OS_IOS 1
#undef TARGET_OS_SIMULATOR
#define TARGET_OS_SIMULATOR 0
#undef TARGET_OS_MACCATALYST
#define TARGET_OS_MACCATALYST 0
#include "actual_memory_init.inc"
#include "actual_pool.inc"
#define botw_base_v0_heap_live_available owned_live
#include "actual_inputs.inc"
#undef botw_base_v0_heap_live_available
#include "actual_registry.inc"
#include "actual_shrink.inc"
#define CORE_PROCESS_MASK 15
static uint64_t dm_entropy64(void){return 1;}
static struct {uint64_t program_id;} owned_title={1};
#define dm_title() (&owned_title)
#define mprotect owned_protect
static uint64_t memory_svc(int num,uint64_t *regs){switch(num){
#include "actual_svc_cases.inc"
default:abort();}}
#undef mprotect
static uint64_t info(unsigned id){uint64_t regs[4]={0,id,0xffff8001,0};memory_svc(0x29,regs);if(regs[0])abort();return regs[1];}
static int keys(void){
    return pthread_key_create(&g_key18,NULL)||pthread_key_create(&g_keytls,NULL)||pthread_key_create(&g_keytlr,NULL)||pthread_key_create(&g_keygate,NULL)||pthread_key_create(&g_keyres[0],NULL)||pthread_key_create(&g_keyres[1],NULL);
}
static uint64_t regs[31];
int main(int argc,char **argv){
    CHECK(argc==2);g_mod[0].size=54*MIB;CHECK(!keys());
    if(!strcmp(argv[1],"plans")){
        size_t heap=1,root=2;char message[192];
        CHECK(BOTW_BASE_V0_ORIGINAL_HEAP_BYTES==H);
        CHECK(!botw_base_v0_heap_plan_original_logical(H,256*MIB,&heap,&root,message,sizeof message)&&heap==H&&root==R);
        heap=1;root=2;CHECK(botw_base_v0_heap_plan_original_logical(H-1,256*MIB,&heap,&root,message,sizeof message)&&heap==1&&root==2);
        CHECK(botw_base_v0_heap_plan_original_logical(H,256*MIB-1,&heap,&root,message,sizeof message)&&heap==1&&root==2);
        CHECK(botw_base_v0_heap_plan_original_logical(H,256*MIB,&heap,&heap,message,sizeof message)&&heap==1);
        CHECK(!botw_base_v0_heap_plan(H,1024*MIB,&heap,&root,message,sizeof message)&&heap==768*MIB&&root==736*MIB);
        CHECK(!botw_base_v0_heap_original_logical_enabled());puts("WHOLE_ORIGINAL_LOGICAL_CPU_OK");return 0;
    }
    if(!strcmp(argv[1],"immutable")){
        char message[192];CHECK(!botw_base_v0_heap_thread_init(g_keyres[0],g_keyres[1],dm_guest_heap_plan_inputs,message,sizeof message));
        CHECK((size_t)pthread_getspecific(g_keyres[0])==768*MIB);
        expected_fatal=1;if(!setjmp(fatal))botw_base_v0_heap_enable();
        CHECK(fatal_seen&&!botw_base_v0_heap_original_logical_enabled()&&!g_guest_thread_params);
        CHECK((size_t)pthread_getspecific(g_keyres[0])==768*MIB&&(size_t)pthread_getspecific(g_keyres[1])==736*MIB);
        CHECK(!munmap(g_as,g_as_size));puts("WHOLE_ORIGINAL_LOGICAL_CPU_OK");return 0;
    }
    if(!strcmp(argv[1],"three"))chosen_gib=3;
    if(!strcmp(argv[1],"two"))chosen_gib=2;
    if(!strcmp(argv[1],"five"))chosen_gib=5;
    if(!strcmp(argv[1],"three")||!strcmp(argv[1],"two")){
        expected_fatal=1;if(!setjmp(fatal)){botw_base_v0_heap_enable();guest_thread_init();}
        CHECK(fatal_seen&&!g_heap_cur&&!pthread_getspecific(g_keyres[0])&&!pthread_getspecific(g_keyres[1]));
    }else{
        live_available=1024*MIB;botw_base_v0_heap_enable();guest_thread_init();
        CHECK((size_t)pthread_getspecific(g_keyres[0])==H&&(size_t)pthread_getspecific(g_keyres[1])==R);
        size_t offset=chosen_gib==4?960*MIB:GIB;
        CHECK((uintptr_t)g_heap-(uintptr_t)g_as==offset&&g_heap_cap==chosen_gib*GIB-offset);
        CHECK(info(I_AslrAddr)==(uintptr_t)g_as&&info(I_AslrSize)==chosen_gib*GIB&&info(I_HeapSize)==g_heap_cap);
        CHECK(info(I_AliasSize)==512*MIB&&info(I_StackSize)==256*MIB);
        CHECK(info(I_AliasAddr)==(uintptr_t)g_as+(chosen_gib==4?192*MIB:256*MIB));
        CHECK(info(I_StackAddr)==info(I_AliasAddr)+512*MIB&&info(I_HeapAddr)==info(I_StackAddr)+256*MIB);
        CHECK(info(I_TotalMem)==H+54*MIB&&info(I_TotalNonSys)==info(I_TotalMem));
        CHECK(info(I_UsedMem)==54*MIB&&reserve_calls==6-chosen_gib);
        if(!strcmp(argv[1],"guard"))live_available=256*MIB-1;
        else live_available=256*MIB;
        if(!strcmp(argv[1],"protect"))fail_protect=1;
        regs[1]=H;expected_fatal=!strcmp(argv[1],"guard")||!strcmp(argv[1],"protect");
        if(!setjmp(fatal))memory_svc(1,regs);
        if(expected_fatal){CHECK(fatal_seen&&!g_heap_cur&&regs[1]==H);CHECK(protect_calls==(!strcmp(argv[1],"protect")?1u:0u));}
        else{
            CHECK(!regs[0]&&regs[1]==(uintptr_t)g_heap&&g_heap_cur==H&&protect_calls==1);
            CHECK(info(I_UsedMem)==H+54*MIB);g_heap[0]=0x3a;g_heap[H-1]=0x4b;
            regs[1]=H-2*MIB;memory_svc(1,regs);CHECK(!regs[0]&&g_heap_cur==H-2*MIB);
            regs[1]=H;memory_svc(1,regs);CHECK(!regs[0]&&g_heap_cur==H);
            CHECK(g_heap[0]==0x3a);
        }
    }
    if(g_as)CHECK(!munmap(g_as,g_as_size));
    puts("WHOLE_ORIGINAL_LOGICAL_CPU_OK");return 0;
}
