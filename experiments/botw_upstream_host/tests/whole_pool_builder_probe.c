/* Exact NVN entry bodies and the single production checked-prep registry.
 * All protected operands are owned mmap or bounded host locals. No GPU/guest. */
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "dm.h"
#include "nvn_obj.h"
#include "nvn_protos.h"
static sigjmp_buf controlled;
static volatile sig_atomic_t failed, faulted;
#undef die
#define die(...) do { failed++; siglongjmp(controlled,1); } while(0)
#include "normal_registry.inc"
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__); static const NVNT_##name check_##name __attribute__((unused))=h_##name; static ret h_##name(__VA_ARGS__)
#include "builder_entries.inc"
static const struct { const char *name; void *fn; } entries[]={
#include "builder_registry.inc"
};
static void *lookup(const char *suffix) {
    char name[96]; snprintf(name,sizeof name,"nvnMemoryPoolBuilder%s",suffix);
    for(size_t i=0;i<sizeof entries/sizeof *entries;i++) if(!strcmp(name,entries[i].name)) return entries[i].fn;
    return NULL;
}
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"POOL_BUILDER_CHECK %d: %s\n",__LINE__,#x); return 91; } } while(0)
static unsigned prep_calls;
static int prep_denied;
static const void *expected_write;
static size_t expected_bytes;
static int prepare(void *address,size_t bytes) {
    prep_calls++;
    return !prep_denied&&(!expected_write||(address==expected_write&&bytes==expected_bytes));
}
static void fault(int number,siginfo_t *info,void *context) {
    (void)number;(void)info;(void)context;faulted++;siglongjmp(controlled,1);
}
int main(int argc,char **argv) {
    CHECK(argc==2);size_t page=(size_t)getpagesize();
    struct sigaction action={0};action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO;
    CHECK(!sigaction(SIGSEGV,&action,NULL)&&!sigaction(SIGBUS,&action,NULL));
    CHECK(dm_guest_write_prep_register_checked(prepare));
    unsigned char *allocation=mmap(NULL,2*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(allocation!=MAP_FAILED);
    memset(allocation,0xa5,2*page);unsigned char *raw=allocation+128;
    void (*defaults)(NVNmemoryPoolBuilder*)=lookup("SetDefaults");
    void (*device)(NVNmemoryPoolBuilder*,NVNdevice*)=lookup("SetDevice");
    void (*storage)(NVNmemoryPoolBuilder*,void*,size_t)=lookup("SetStorage");
    void (*flags)(NVNmemoryPoolBuilder*,int)=lookup("SetFlags");
    void *(*getmem)(const NVNmemoryPoolBuilder*)=lookup("GetMemory");
    size_t (*getsize)(const NVNmemoryPoolBuilder*)=lookup("GetSize");
    NVNmemoryPoolFlags (*getflags)(const NVNmemoryPoolBuilder*)=lookup("GetFlags");
    CHECK(defaults&&device&&storage&&flags);
    if(!strcmp(argv[1],"defaults")||!strcmp(argv[1],"guard")||!strcmp(argv[1],"device-unreadable")) {
        if(!strcmp(argv[1],"guard")) raw=allocation+page-28;
        if(!strcmp(argv[1],"device-unreadable")) raw=allocation+page-8;
        unsigned char before[64];memcpy(before,raw,sizeof before);
        if(!strcmp(argv[1],"guard")) CHECK(!mprotect(allocation+page,page,PROT_NONE));
        if(!strcmp(argv[1],"device-unreadable")) CHECK(!mprotect(allocation,page,PROT_NONE));
        expected_write=raw+8;expected_bytes=20;
        if(!sigsetjmp(controlled,1)) defaults((NVNmemoryPoolBuilder*)raw);
        CHECK(!mprotect(allocation,2*page,PROT_READ|PROT_WRITE));
        CHECK(!failed&&!faulted&&prep_calls==1);
        unsigned char expected[64];memcpy(expected,before,sizeof expected);memset(expected+8,0,16);uint32_t f=0x21;memcpy(expected+24,&f,4);
        CHECK(!memcmp(expected,raw,sizeof expected));
    } else if(!strcmp(argv[1],"setters")||!strcmp(argv[1],"getters")) {
        unsigned char before[64];memcpy(before,raw,sizeof before);
        expected_write=raw;expected_bytes=8;device((NVNmemoryPoolBuilder*)raw,(NVNdevice*)(uintptr_t)0x123456789abULL);
        expected_write=raw+8;expected_bytes=16;storage((NVNmemoryPoolBuilder*)raw,allocation+123,UINT64_C(0x100000001));
        expected_write=raw+24;expected_bytes=4;flags((NVNmemoryPoolBuilder*)raw,(int)0x87654321u);
        if(!strcmp(argv[1],"getters")) {
            CHECK(getmem&&getsize&&getflags);
            CHECK(__builtin_types_compatible_p(NVNT_nvnMemoryPoolBuilderGetMemory,void*(*)(const NVNmemoryPoolBuilder*)));
            CHECK(getmem((NVNmemoryPoolBuilder*)raw)==allocation+123);
            CHECK(getsize((NVNmemoryPoolBuilder*)raw)==UINT64_C(0x100000001));
            CHECK((uint32_t)getflags((NVNmemoryPoolBuilder*)raw)==0x87654321u);
        }
        CHECK(!memcmp(raw+28,before+28,36));CHECK(!failed&&!faulted&&prep_calls==3);
        pool_builder_t *b=(pool_builder_t*)raw;CHECK(b->dev==(void*)(uintptr_t)0x123456789abULL&&b->mem==allocation+123&&b->size==UINT64_C(0x100000001)&&b->flags==0x87654321u);
    } else if(!strcmp(argv[1],"readonly")||!strcmp(argv[1],"noaccess")||!strcmp(argv[1],"partial-readonly")||!strcmp(argv[1],"prep-denied")) {
        if(!strcmp(argv[1],"partial-readonly")) raw=allocation+page-20;
        unsigned char before[64];memcpy(before,raw,sizeof before);
        if(!strcmp(argv[1],"readonly")) CHECK(!mprotect(allocation,page,PROT_READ));
        if(!strcmp(argv[1],"noaccess")) CHECK(!mprotect(allocation,page,PROT_NONE));
        if(!strcmp(argv[1],"partial-readonly")) CHECK(!mprotect(allocation+page,page,PROT_READ));
        prep_denied=!strcmp(argv[1],"prep-denied");
        if(!sigsetjmp(controlled,1)) { defaults((NVNmemoryPoolBuilder*)raw);CHECK(0); }
        CHECK(!mprotect(allocation,2*page,PROT_READ|PROT_WRITE));
        CHECK(failed==1&&!faulted&&prep_calls==1&&!memcmp(raw,before,sizeof before));
    } else if(!strcmp(argv[1],"bounds")) {
        if(!sigsetjmp(controlled,1)) { defaults(NULL);CHECK(0); }
        CHECK(failed==1&&!faulted&&!prep_calls);
        if(!sigsetjmp(controlled,1)) { defaults((NVNmemoryPoolBuilder*)(UINTPTR_MAX-15));CHECK(0); }
        CHECK(failed==2&&!faulted&&!prep_calls);
    } else if(!strcmp(argv[1],"getter-unreadable")) {
        CHECK(getmem);CHECK(!mprotect(allocation,page,PROT_NONE));
        if(!sigsetjmp(controlled,1)) { (void)getmem((NVNmemoryPoolBuilder*)raw);CHECK(0); }
        CHECK(failed==1&&!faulted&&!prep_calls);
    } else if(!strcmp(argv[1],"field-guards")) {
        CHECK(getmem&&getsize&&getflags);
        CHECK(!mprotect(allocation+page,page,PROT_NONE));
        raw=allocation+page-8;expected_write=raw;expected_bytes=8;device((NVNmemoryPoolBuilder*)raw,(NVNdevice*)allocation);
        raw=allocation+page-24;expected_write=raw+8;expected_bytes=16;storage((NVNmemoryPoolBuilder*)raw,allocation+1,0x100000001ULL);
        CHECK(getsize((NVNmemoryPoolBuilder*)raw)==0x100000001ULL);
        raw=allocation+page-16;CHECK(getmem((NVNmemoryPoolBuilder*)raw)==(void*)(uintptr_t)0x100000001ULL);
        CHECK(!mprotect(allocation+page,page,PROT_READ|PROT_WRITE));CHECK(!mprotect(allocation,page,PROT_NONE));
        raw=allocation+page-24;expected_write=raw+24;expected_bytes=4;flags((NVNmemoryPoolBuilder*)raw,0x1234);CHECK(getflags((NVNmemoryPoolBuilder*)raw)==0x1234);
        CHECK(prep_calls==3&&!failed&&!faulted);
    } else return 2;
    CHECK(!munmap(allocation,2*page));puts("WHOLE_POOL_BUILDER_CPU_OK");return 0;
}
