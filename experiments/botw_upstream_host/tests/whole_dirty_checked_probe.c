/* Exact normal crash registry + whole original dirty algorithm + exact NVN pool
 * initialization body. All storage is owned mmap. No guest or GPU executes. */
#include <assert.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <unistd.h>
#include <mach/mach_time.h>
#include "dm.h"
#include "nvn_obj.h"
static sigjmp_buf controlled_failure;
static unsigned failed;
#undef die
#define die(...) do { failed++; siglongjmp(controlled_failure, 1); } while (0)
#include "normal_registry.inc"
static _Atomic size_t calls, outstanding;
static size_t fail_at;
static void *owned_calloc(size_t n, size_t z) {
    if (atomic_fetch_add(&calls,1)+1==fail_at) return NULL;
    void *p=calloc(n,z); if(p) atomic_fetch_add(&outstanding,1); return p;
}
static void owned_free(void *p) { if(p) atomic_fetch_sub(&outstanding,1); free(p); }
/* Deterministic scheduling at the first real BUSY wait clock read. The owner
 * completes the original unprotect_page function with a real mprotect result. */
static void (*owned_clock_step)(void);
static uint64_t controlled_time(void) {
    void (*step)(void)=owned_clock_step;
    if(step) { owned_clock_step=NULL; step(); }
    return mach_absolute_time();
}
#define calloc owned_calloc
#define free owned_free
#define mach_absolute_time controlled_time
#include "nvn_dirty.c"
#undef mach_absolute_time
#undef calloc
#undef free
#ifndef BOTW_WHOLE_DIRTY_CHECKED
/* Observe the original void boundaries exactly as its successful caller did. */
static int baseline_add(const void *p,size_t n) { nvn_dirty_add_range(p,n); return 1; }
static int baseline_prep(void *p,size_t n) { dm_guest_write_prepare(p,n); return 1; }
#define nvn_dirty_add_range_checked baseline_add
#define dm_guest_write_prepare_checked baseline_prep
#endif
#include "shader_pool_registry.inc"
static unsigned metal_calls, metal_order_bad;
static const NVNmemoryPool *expected_pool;
static void mtl_pool_add(void *mem,size_t size) {
    /* CPU boundary witness only: reaching it on a failure is itself a failure. */
    metal_calls++;
    const pool_t *p=(const pool_t*)expected_pool;
    if (!p || p->magic!=MAGIC('P','O','O','L') || !nvn_dirty_tracked(mem,size) || !pools)
        metal_order_bad++;
}
static void botw_shader_unsupported(const char *error,const void *unused) { (void)error; (void)unused; failed++; }
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
#define OLOG(...) ((void)0)
#include "pool_initialize.inc"
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"DIRTY_CHECK %d: %s\n",__LINE__,#x); return 91; } } while(0)
static size_t page;
static unsigned char *transition_page;
static int transition_result;
static void finish_owned_transition(void) {
    transition_result=unprotect_page(atomic_load(&g_r[0])->page[0],(uintptr_t)transition_page);
}
static unsigned char *owned_pages(size_t n) {
    void *p=mmap(NULL,n*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(p!=MAP_FAILED); return p;
}
static int stat_live(void) { int live,peak,cap; nvn_dirty_stat(&live,&peak,&cap); return live; }
static int pool_try(unsigned char *mem,size_t n) {
    NVNmemoryPool object; memset(&object,0xa5,sizeof object);
    NVNmemoryPool before=object;
    NVNmemoryPoolBuilder builder={0}; pool_builder_t *b=(pool_builder_t*)&builder;
    b->mem=mem; b->size=n; expected_pool=&object;
    int result=-1;
    if (!sigsetjmp(controlled_failure,1)) result=h_nvnMemoryPoolInitialize(&object,&builder);
    if (failed || result || memcmp(&before,&object,sizeof object) || metal_calls || pools) return 1;
    return 0;
}
#ifdef BOTW_WHOLE_DIRTY_CHECKED
static _Atomic unsigned first_calls,second_calls,third_calls,fourth_calls;
static int second(void *p,size_t n) { (void)p; (void)n; atomic_fetch_add(&second_calls,1); return 1; }
static int third(void *p,size_t n) { (void)p; (void)n; atomic_fetch_add(&third_calls,1); return 1; }
static int fourth(void *p,size_t n) { (void)p; (void)n; atomic_fetch_add(&fourth_calls,1); return 1; }
static int first(void *p,size_t n) {
    (void)p; (void)n;
    if (!atomic_fetch_add(&first_calls,1) && !dm_guest_write_prep_register_checked(second)) return 0;
    return 1;
}
static int rejected(void *p,size_t n) { (void)p; (void)n; return 0; }
static void unchecked(void *p,size_t n) { (void)p; (void)n; }
static void *prepare_loop(void *p) {
    for(unsigned i=0;i<2000;i++) if(!dm_guest_write_prepare_checked(p,1)) return (void*)1;
    return NULL;
}
static void *register_more(void *p) {
    (void)p;
    return dm_guest_write_prep_register_checked(third) && dm_guest_write_prep_register_checked(fourth) ? NULL : (void*)1;
}
static void owned_signal(int sig,siginfo_t *info,void *context) {
    (void)sig;
    for(int i=0;i<g_nfault_hook;i++) if(g_fault_hook[i](info->si_addr,1,context)) return;
    _exit(88);
}
#endif
int main(int argc,char **argv) {
    CHECK(argc==2); page=(size_t)getpagesize();
    if (!strcmp(argv[1],"prep-failure")) {
        unsigned char *mem=owned_pages(1);
        CHECK(nvn_dirty_add_range_checked(mem,page)); CHECK(nvn_dirty_protect_checked(mem,page));
        CHECK(!munmap(mem,page));
        CHECK(!dm_guest_write_prepare_checked(mem,page));
        CHECK(atomic_load(&atomic_load(&g_r[0])->page[0]->state)==PG_PROT);
        CHECK(!atomic_load(&atomic_load(&g_r[0])->page[0]->gen));
        /* Re-create only our exact unmapped owned range for ordinary removal. */
        CHECK(mmap(mem,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0)==mem);
        nvn_dirty_remove_range(mem); CHECK(!munmap(mem,page));
    } else if (!strcmp(argv[1],"capacity") || !strcmp(argv[1],"pool-capacity")) {
        unsigned char *mem=owned_pages(1);
        for(unsigned i=0;i<4096;i++) CHECK(nvn_dirty_add_range_checked(mem,page));
        CHECK(stat_live()==4096);
        size_t refs=g_pages_shared,live=g_pages_live,allocs=atomic_load(&outstanding);
        if (!strcmp(argv[1],"capacity")) CHECK(!nvn_dirty_add_range_checked(mem,page));
        else CHECK(!pool_try(mem,page));
        CHECK(stat_live()==4096&&g_pages_shared==refs&&g_pages_live==live&&atomic_load(&outstanding)==allocs);
        for(unsigned i=0;i<4096;i++) nvn_dirty_remove_range(mem);
        CHECK(!stat_live()&&!g_pages_live&&!g_pages_shared&&!atomic_load(&outstanding));
        CHECK(!munmap(mem,page));
    } else if (!strncmp(argv[1],"alloc-",6)) {
        unsigned char *mem=owned_pages(2); CHECK(nvn_dirty_add_range_checked(mem,page));
        size_t allocs=atomic_load(&outstanding); uint64_t stamp=nvn_dirty_now();
        fail_at=atomic_load(&calls)+(size_t)(argv[1][6]-'0');
        CHECK(!pool_try(mem,2*page));
        CHECK(stat_live()==1&&g_pages_live==1&&!g_pages_shared&&atomic_load(&outstanding)==allocs&&nvn_dirty_now()==stamp);
        nvn_dirty_remove_range(mem); CHECK(!atomic_load(&outstanding)); CHECK(!munmap(mem,2*page));
    } else if (!strcmp(argv[1],"invalid")) {
        CHECK(!nvn_dirty_add_range_checked(NULL,page));
        CHECK(!nvn_dirty_add_range_checked((void*)1,0));
        CHECK(!nvn_dirty_add_range_checked((void*)(UINTPTR_MAX-1),page));
        CHECK(!stat_live()&&!atomic_load(&outstanding));
#ifdef BOTW_WHOLE_DIRTY_CHECKED
    } else if (!strcmp(argv[1],"busy-failure") || !strcmp(argv[1],"busy-success")) {
        unsigned char *mem=owned_pages(1); transition_page=mem;
        CHECK(nvn_dirty_add_range_checked(mem,page)); CHECK(nvn_dirty_protect_checked(mem,page));
        page_t *pg=atomic_load(&g_r[0])->page[0];
        int failure=!strcmp(argv[1],"busy-failure");
        if(failure) CHECK(!munmap(mem,page));
        atomic_store(&pg->state,PG_BUSY); owned_clock_step=finish_owned_transition;
        int handled=on_fault(mem,1,NULL);
        CHECK(transition_result==!failure);
        if(failure) {
            CHECK(!handled&&atomic_load(&pg->state)==PG_PROT&&!atomic_load(&pg->gen));
            CHECK(atomic_load(&g_mp_calls)==3); // protect, failed owner, failed fault recovery
            CHECK(mmap(mem,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0)==mem);
        } else CHECK(handled&&atomic_load(&pg->state)==PG_NONE&&atomic_load(&pg->gen)&&atomic_load(&g_mp_calls)==2);
        nvn_dirty_remove_range(mem); CHECK(!munmap(mem,page));
    } else if (!strcmp(argv[1],"snapshot")) {
        char p=0; CHECK(dm_guest_write_prep_register_checked(first));
        CHECK(dm_guest_write_prepare_checked(&p,1)); CHECK(first_calls==1&&!second_calls);
        CHECK(dm_guest_write_prepare_checked(&p,1)); CHECK(first_calls==2&&second_calls==1);
        CHECK(dm_guest_write_prepare_checked(NULL,0));
        CHECK(!dm_guest_write_prepare_checked((void*)(UINTPTR_MAX-1),3));
        CHECK(!dm_guest_write_prep_register_checked(NULL));
    } else if (!strcmp(argv[1],"concurrent")) {
        char p=0; CHECK(dm_guest_write_prep_register_checked(first));
        CHECK(dm_guest_write_prepare_checked(&p,1));
        pthread_t readers[8],writer; CHECK(!pthread_create(&writer,NULL,register_more,NULL));
        for(unsigned i=0;i<8;i++) CHECK(!pthread_create(&readers[i],NULL,prepare_loop,&p));
        void *result; CHECK(!pthread_join(writer,&result)&&!result);
        for(unsigned i=0;i<8;i++) CHECK(!pthread_join(readers[i],&result)&&!result);
        CHECK(first_calls==16001&&second_calls==16000);
        CHECK(!dm_guest_write_prep_register_checked(rejected));
    } else if (!strcmp(argv[1],"legacy")) {
        char p=0; CHECK(dm_guest_write_prep_register_checked(rejected));
        if (!sigsetjmp(controlled_failure,1)) { dm_guest_write_prepare(&p,1); CHECK(0); }
        CHECK(failed==1);
        if (!sigsetjmp(controlled_failure,1)) { dm_guest_write_prep_register(unchecked); CHECK(0); }
        CHECK(failed==2);
        if (!sigsetjmp(controlled_failure,1)) { nvn_dirty_add_range(NULL,1); CHECK(0); }
        CHECK(failed==3);
    } else if (!strcmp(argv[1],"fault-overlap")) {
        unsigned char *mem=owned_pages(3);
        CHECK(nvn_dirty_add_range_checked(mem,2*page)); CHECK(nvn_dirty_add_range_checked(mem+page,2*page));
        CHECK(g_pages_live==3&&g_pages_shared==1);
        struct sigaction action={0},oldsegv,oldbus; action.sa_sigaction=owned_signal; action.sa_flags=SA_SIGINFO;
        CHECK(!sigaction(SIGSEGV,&action,&oldsegv)); CHECK(!sigaction(SIGBUS,&action,&oldbus));
        uint64_t stamp=nvn_dirty_now(); CHECK(nvn_dirty_protect_checked(mem,2*page));
        /* The real fault hook must remain lock-free even if a normal prep
         * snapshot owns its registry mutex on this very thread. */
        CHECK(!pthread_mutex_lock(&g_write_prep_mu));
        ((volatile unsigned char*)mem)[page]=7;
        CHECK(!pthread_mutex_unlock(&g_write_prep_mu));
        CHECK(nvn_dirty_since(mem+page,page,stamp));
        CHECK(nvn_dirty_protect_checked(mem+page,2*page)); nvn_dirty_remove_range(mem);
        ((volatile unsigned char*)mem)[page]=9;
        CHECK(atomic_load(&g_faults)==2);
        nvn_dirty_remove_range(mem+page);
        CHECK(!stat_live()&&!g_pages_live&&!g_pages_shared&&!atomic_load(&outstanding));
        CHECK(!sigaction(SIGSEGV,&oldsegv,NULL)); CHECK(!sigaction(SIGBUS,&oldbus,NULL)); CHECK(!munmap(mem,3*page));
    } else if (!strcmp(argv[1],"pool-success")) {
        unsigned char *mem=owned_pages(1); NVNmemoryPool p={0}; NVNmemoryPoolBuilder b={0};
        ((pool_builder_t*)&b)->mem=mem; ((pool_builder_t*)&b)->size=page; expected_pool=&p;
        CHECK(h_nvnMemoryPoolInitialize(&p,&b)); CHECK(metal_calls==1&&!metal_order_bad&&pools&&stat_live()==1);
        botw_shader_pool_remove(&p); nvn_dirty_remove_range(mem); CHECK(!pools&&!atomic_load(&outstanding)); CHECK(!munmap(mem,page));
#endif
    } else return 2;
    puts("WHOLE_DIRTY_CHECKED_CPU_OK"); return 0;
}
