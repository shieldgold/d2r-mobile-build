/* The baseline is an owned passthrough replay of fixed SDK ctor arguments,
 * not execution or reimplementation of the Guest SDK allocator. The original
 * callee owns opaque writes; the production adapter owns only forwarding. */
#include "dm.h"
#include "hle.h"
#include "base_v0_stack_allocator.h"
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <sys/mman.h>
#include <unistd.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>

module_t g_mod[MAX_MOD];
int g_nmod;
static sigjmp_buf stopped;
static int armed,fatal_calls,missing_original,opaque_unreadable,recursive_original,failed_query;
static unsigned resolves,queries,original_calls,generic_resolves;
static uint64_t host_base=UINT64_C(0x280000000),host_size=UINT64_C(0x10000000);
static uint64_t captured_start,captured_end,captured_guard;
static void *captured_self;
static unsigned char result_token;
static unsigned char *expected_object;
static size_t expected_object_size;
static const hle_force_ent *registered(void);

static _Noreturn void owned_failure(const char *format,...){
    (void)format;fatal_calls++;
    if(armed)siglongjmp(stopped,1);
    abort();
}
#undef die
#define die(...) owned_failure(__VA_ARGS__)
static void *owned_original_ctor(void *self,uint64_t start,uint64_t end,uint64_t guard){
    assert(errno==EDOM);original_calls++;
    assert(self==expected_object);
    if(!opaque_unreadable){
        for(size_t i=0;i<expected_object_size;i++)assert(expected_object[i]==0xa5);
        memset(self,0x6d,32); /* Only this owned normal callee writes the object. */
    }
    captured_self=self;captured_start=start;captured_end=end;captured_guard=guard;
    errno=ERANGE;return &result_token;
}
void *module_sym(module_t *module,const char *name){
    assert(module==g_mod+5&&!strcmp(module->name,"sdk"));
    assert(!strcmp(name,BOTW_BASE_V0_STACK_ALLOCATOR_SYMBOL));resolves++;
    errno=EAGAIN;
    if(recursive_original)return (void*)registered()->fn;
    return missing_original?NULL:(void*)owned_original_ctor;
}
void *guest_sym(const char *name){
    (void)name;generic_resolves++;
    /* A same-named earlier-module definition must never be selected by the
     * actual adapter; this is only a detectable owned resolver boundary. */
    return NULL;
}
uint64_t svc_dispatch(int number,uint64_t *regs,uint64_t original_address){
    assert(number==0x29&&!original_address);
    uint64_t id=regs[1];assert(id==14||id==15);
    for(unsigned i=0;i<31;i++)if(i!=1&&i!=2)assert(!regs[i]);
    assert(regs[2]==UINT64_C(0xffff8001));
    assert(id==14+(queries%2));queries++;
    regs[0]=0;regs[1]=id==14?host_base:host_size;
    if((int)id==failed_query)regs[0]=0xd401;
    errno=EIO;return 0;
}
#include "normal_stack_allocator.inc"

static const hle_force_ent *registered(void){
    unsigned long bytes=0;
    const hle_force_ent *entries=(const void*)getsectiondata(&_mh_execute_header,"__DATA","swforce",&bytes);
    if(!entries)return NULL;
    assert(!(bytes%sizeof *entries));
    const hle_force_ent *found=NULL;
    for(size_t i=0;i<bytes/sizeof *entries;i++)if(!strcmp(entries[i].name,BOTW_BASE_V0_STACK_ALLOCATOR_SYMBOL)){
        assert(!found);found=entries+i;
    }
    return found;
}
static void setup(void){
    static const char *names[]={"main","subsdk0","subsdk1","subsdk2","subsdk3","sdk"};
    g_nmod=6;for(int i=0;i<g_nmod;i++)strcpy(g_mod[i].name,names[i]);
}
static void *call(void *object,uint64_t start,uint64_t end,uint64_t guard){
    const hle_force_ent *entry=registered();
    botw_base_v0_stack_constructor constructor;
    if(entry){
        assert(entry->titles==DM_TM_BOTW&&!strcmp(entry->why,BOTW_BASE_V0_STACK_ALLOCATOR_WHY));
        constructor=(botw_base_v0_stack_constructor)entry->fn;
    }else{
        /* Normal missing-force fallback resolves the original definition.
         * Its fixed low range is captured, never executed as Guest code. */
        constructor=(botw_base_v0_stack_constructor)module_sym(g_mod+5,BOTW_BASE_V0_STACK_ALLOCATOR_SYMBOL);
    }
    errno=EDOM;return constructor(object,start,end,guard);
}
static void invoke(uint64_t start,uint64_t end,uint64_t guard){
    unsigned char object[64];memset(object,0xa5,sizeof object);
    expected_object=object;expected_object_size=sizeof object;
    assert(call(object,start,end,guard)==&result_token&&errno==ERANGE);
    assert(captured_self==object);
    for(unsigned i=0;i<sizeof object;i++)assert(object[i]==(i<32?0x6d:0xa5));
}
static void exact(void){
    invoke(UINT64_C(0x08000000),UINT64_C(0x80000000),UINT64_C(0x4000));
    /* PAGEZERO is a declared owned interval, not a queried runtime mapping.
     * Baseline compilation and original callee both succeed; this range
     * assertion is the behavioral RED for the fixed low SDK parameters. */
    assert(captured_start>=UINT64_C(0x100000000));
    assert(captured_start==host_base&&captured_end==host_base+host_size-0x4000&&captured_guard==0x4000);
    assert(queries==2&&resolves==1&&original_calls==1);
}
static void passthrough(void){
    static const uint64_t values[][3]={
        {0,UINT64_C(0x1000000000),0},
        {UINT64_C(0x08000000),UINT64_C(0x80000000),UINT64_MAX},
        {UINT64_C(0x08000000),UINT64_C(0x80000000),0x1000},
        {UINT64_C(0x08001000),UINT64_C(0x80000000),0x4000},
        {UINT64_C(0x08000000),UINT64_C(0x80001000),0x4000}
    };
    for(unsigned i=0;i<sizeof values/sizeof *values;i++){
        invoke(values[i][0],values[i][1],values[i][2]);
        assert(captured_start==values[i][0]&&captured_end==values[i][1]&&captured_guard==values[i][2]);
    }
    assert(!queries&&original_calls==5&&resolves==5);
}
static void registry(void){
    const hle_force_ent *entry=registered();
    assert(entry&&entry->fn!=(void*)owned_original_ctor);
    assert(entry->titles==DM_TM_BOTW&&!strcmp(entry->why,BOTW_BASE_V0_STACK_ALLOCATOR_WHY));
    invoke(UINT64_C(0x08000000),UINT64_C(0x80000000),UINT64_C(0x4000));
    assert(resolves==1&&original_calls==1&&queries==2);
}
static void rejected(const char *mode){
    if(!strcmp(mode,"base-zero"))host_base=0;
    else if(!strcmp(mode,"size-zero"))host_size=0;
    else if(!strcmp(mode,"base-unaligned"))host_base++;
    else if(!strcmp(mode,"size-unaligned"))host_size++;
    else if(!strcmp(mode,"overflow")){host_base=UINT64_MAX-0xfff;host_size=0x1000;}
    else if(!strcmp(mode,"too-small"))host_size=0x8000;
    else if(!strcmp(mode,"equal-minimum"))host_size=0x9000;
    else if(!strcmp(mode,"query14"))failed_query=14;
    else if(!strcmp(mode,"query15"))failed_query=15;
    else if(!strcmp(mode,"bad-count"))g_nmod=5;
    else if(!strcmp(mode,"bad-sdk-name"))strcpy(g_mod[5].name,"other");
    else if(!strcmp(mode,"duplicate-sdk"))strcpy(g_mod[1].name,"sdk");
    else if(!strcmp(mode,"missing-original"))missing_original=1;
    else if(!strcmp(mode,"self-original"))recursive_original=1;
    else abort();
    unsigned char object[64];memset(object,0xa5,sizeof object);
    expected_object=object;expected_object_size=sizeof object;armed=1;
    if(!sigsetjmp(stopped,1)){
        call(object,UINT64_C(0x08000000),UINT64_C(0x80000000),UINT64_C(0x4000));
        assert(!"invalid real host metadata/profile was delegated");
    }
    armed=0;assert(fatal_calls==1&&!original_calls);
    for(unsigned i=0;i<sizeof object;i++)assert(object[i]==0xa5);
}
static void opaque(void){
    size_t page=(size_t)getpagesize();
    void *object=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(object!=MAP_FAILED);
    expected_object=object;expected_object_size=page;opaque_unreadable=1;
    assert(call(object,UINT64_C(0x08000000),UINT64_C(0x80000000),UINT64_C(0x4000))==&result_token);
    assert(errno==ERANGE&&original_calls==1&&captured_self==object&&queries==2&&resolves==1);
    assert(!munmap(object,page));
}
static void guard_proof(void){
    /* The fixed ctor's SHA-checked stores are lo=start>>12,
     * hi=(end>>12)+ceil(guard/4096), G=ceil(guard/4096). This proves only
     * integer bounds for every p in [0,R), not SDK allocator execution. */
    const uint64_t G=4;
    for(uint64_t pages=10;pages<=36;pages++){
        host_size=pages*4096;
        invoke(UINT64_C(0x08000000),UINT64_C(0x80000000),UINT64_C(0x4000));
        uint64_t S=captured_start>>12,E=(captured_end>>12)+((captured_guard+4095)>>12);
        assert(S==host_base/4096&&E==(host_base+host_size)/4096);
        for(uint64_t N=1;N<pages-2*G;N++){
            uint64_t R=E-(N+2*G)-S;assert(R>0);
            for(uint64_t p=0;p<R;p++){
                uint64_t candidate=S+G+p;
                assert(candidate-G>=host_base/4096);
                assert(candidate+N+G<(host_base+host_size)/4096);
            }
        }
    }
    assert(queries==54&&original_calls==27&&resolves==27);
}
int main(int argc,char **argv){
    assert(argc==2);setup();
    if(!strcmp(argv[1],"exact"))exact();
    else if(!strcmp(argv[1],"passthrough"))passthrough();
    else if(!strcmp(argv[1],"registry"))registry();
    else if(!strcmp(argv[1],"opaque"))opaque();
    else if(!strcmp(argv[1],"guard-proof"))guard_proof();
    else rejected(argv[1]);
    assert(!generic_resolves);
    puts("WHOLE_STACK_ALLOCATOR_CPU_OK");return 0;
}
