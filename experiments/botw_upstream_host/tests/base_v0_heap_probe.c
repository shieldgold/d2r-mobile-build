/* Owned normal pthread/TSD + exact production SetHeapSize body. No guest code,
 * graphics backend, second kernel or VM inspection is linked or executed. */
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "dm.h"
static sigjmp_buf owned_thread_failure;
static char thread_failure_message[256];
#undef die
#define die(...) do { snprintf(thread_failure_message,sizeof thread_failure_message,__VA_ARGS__); siglongjmp(owned_thread_failure,1); } while (0)
#include "thread.c"
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
#include "base_v0_heap.h"
#endif
#define MIB ((size_t)1024 * 1024)
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"HEAP_CHECK %d: %s\n",__LINE__,#x); return 91; } } while (0)
pthread_key_t g_key18, g_keytls, g_keytlr, g_keygate, g_keyres[2], g_keyaspect, g_keyaspectui;
void dm_gate(void) { abort(); } /* Function identity only; executing a gate is forbidden. */
static size_t owned_kernel = 640 * MIB, owned_live = 896 * MIB, owned_image = 4 * MIB;
static unsigned query_calls;
static int query_failure;
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
int dm_guest_heap_plan_inputs(botw_base_v0_heap_inputs *out, char *error, size_t capacity) {
    query_calls++;
    if (query_failure) { if (error && capacity) snprintf(error,capacity,"owned live query unavailable"); return -1; }
    *out = (botw_base_v0_heap_inputs){owned_kernel,owned_live,owned_image}; return 0;
}
#endif
static unsigned char *g_heap;
static _Atomic size_t g_heap_cur;
static size_t g_heap_cap;
static pthread_mutex_t g_heap_resize_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_heap_rejects;
struct dm_flags g_flag;
static size_t image_bytes(void) { return owned_image; }
static uint64_t app_pool(void) { return owned_kernel + owned_image; }
static sigjmp_buf owned_failure;
static char failure_message[256];
#undef die
#define die(...) do { snprintf(failure_message,sizeof failure_message,__VA_ARGS__); siglongjmp(owned_failure,1); } while (0)
static uint64_t actual_set_heap(uint64_t *regs) {
#include "set_heap_entries.inc"
}
static void make_keys(void) {
    assert(!pthread_key_create(&g_key18,NULL)); assert(!pthread_key_create(&g_keytls,NULL));
    assert(!pthread_key_create(&g_keytlr,NULL)); assert(!pthread_key_create(&g_keygate,NULL));
    assert(!pthread_key_create(&g_keyres[0],NULL)); assert(!pthread_key_create(&g_keyres[1],NULL));
}
static void release_owned_thread(void) {
    void *own = pthread_getspecific(g_keyown);
    pthread_setspecific(g_keyown,NULL); pthread_setspecific(g_keytls,NULL); pthread_setspecific(g_keytlr,NULL);
    if (own) own_free(own);
}
static void *owned_thread(void *unused) {
    (void)unused; guest_thread_init();
    size_t heap=(size_t)(uintptr_t)pthread_getspecific(g_keyres[0]);
    size_t root=(size_t)(uintptr_t)pthread_getspecific(g_keyres[1]);
    return heap==640*MIB && root==608*MIB ? NULL : (void*)1;
}
static void enable(void) {
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
    botw_base_v0_heap_enable();
#endif
}
int main(int argc,char **argv) {
    CHECK(argc==2); (void)owned_live; (void)query_calls; (void)query_failure; (void)failure_message; (void)thread_failure_message; (void)owned_thread_failure; (void)g_heap_resize_lock; make_keys(); enable();
    if (!strcmp(argv[1],"threads")) {
        /* Race the first real initializers too: no thread may select its own
         * plan, and the parent receives the same frozen pair afterwards. */
        pthread_t threads[16]; for(unsigned i=0;i<16;i++) CHECK(!pthread_create(&threads[i],NULL,owned_thread,NULL));
        for(unsigned i=0;i<16;i++) { void *result=(void*)1; CHECK(!pthread_join(threads[i],&result)&&!result); }
        guest_thread_init();
        CHECK((size_t)(uintptr_t)pthread_getspecific(g_keyres[0])==640*MIB);
        CHECK((size_t)(uintptr_t)pthread_getspecific(g_keyres[1])==608*MIB);
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
        CHECK(query_calls==1);
#endif
        release_owned_thread();
    } else if (!strcmp(argv[1],"mprotect-failure")) {
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
        guest_thread_init(); release_owned_thread();
#endif
        g_heap_cap=640*MIB; g_heap=mmap(NULL,g_heap_cap,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
        CHECK(g_heap!=MAP_FAILED); CHECK(!munmap(g_heap,g_heap_cap));
        uint64_t regs[31]={0}; regs[1]=g_heap_cap;
        if (!sigsetjmp(owned_failure,1)) actual_set_heap(regs);
        CHECK(regs[0]!=0 && g_heap_cur==0);
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
        CHECK(strstr(failure_message,"initial") && query_calls>=2);
#endif
#ifdef BOTW_BASE_V0_HEAP_ADAPTED
    } else if (!strcmp(argv[1],"plan")) {
        size_t heap=11,root=22; char error[256];
        CHECK(!botw_base_v0_heap_plan(2191604224,2191604224,&heap,&root,error,sizeof error));
        CHECK(heap==1923088384 && root==1889533952);
        CHECK(!botw_base_v0_heap_plan(SIZE_MAX,SIZE_MAX,&heap,&root,error,sizeof error));
        CHECK(heap==0xc2600000 && root==0xc0600000);
        heap=11;root=22;
        CHECK(botw_base_v0_heap_plan(1,896*MIB,&heap,&root,error,sizeof error) && heap==11&&root==22);
        CHECK(botw_base_v0_heap_plan(640*MIB,1,&heap,&root,error,sizeof error) && heap==11&&root==22);
        CHECK(botw_base_v0_heap_plan(640*MIB,896*MIB,&heap,&heap,error,sizeof error));
    } else if (!strcmp(argv[1],"query-failure")) {
        query_failure=1;
        if (!sigsetjmp(owned_thread_failure,1)) { guest_thread_init(); CHECK(0); }
        CHECK(!pthread_getspecific(g_keyres[0])&&!pthread_getspecific(g_keyres[1]));
        query_failure=0; guest_thread_init();
        CHECK((size_t)(uintptr_t)pthread_getspecific(g_keyres[0])==640*MIB);
        CHECK((size_t)(uintptr_t)pthread_getspecific(g_keyres[1])==608*MIB);
        release_owned_thread();
    } else if (!strcmp(argv[1],"key-failure")) {
        CHECK(!pthread_setspecific(g_keyres[0],(void*)13));
        CHECK(!pthread_key_delete(g_keyres[1]));
        if (!sigsetjmp(owned_thread_failure,1)) { guest_thread_init(); CHECK(0); }
        CHECK(pthread_getspecific(g_keyres[0])==(void*)13);
        CHECK(strstr(thread_failure_message,"TSD"));
    } else if (!strcmp(argv[1],"mprotect-success") || !strcmp(argv[1],"live-decline") || !strcmp(argv[1],"bad-request")) {
        guest_thread_init(); release_owned_thread();
        char error[256]; CHECK(botw_base_v0_heap_verify(0,error,sizeof error));
        g_heap_cap=640*MIB; g_heap=mmap(NULL,g_heap_cap,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0); CHECK(g_heap!=MAP_FAILED);
        uint64_t regs[31]={0}; regs[1]=g_heap_cap;
        if (!strcmp(argv[1],"live-decline")) {
            owned_live=800*MIB;
            if (!sigsetjmp(owned_failure,1)) { actual_set_heap(regs); CHECK(0); }
            CHECK(regs[0]!=0&&g_heap_cur==0);
        } else if (!strcmp(argv[1],"bad-request")) {
            regs[1]=g_heap_cap-2*MIB;
            if (!sigsetjmp(owned_failure,1)) { actual_set_heap(regs); CHECK(0); }
            CHECK(regs[0]!=0&&g_heap_cur==0);
            regs[1]=1; if (!sigsetjmp(owned_failure,1)) actual_set_heap(regs);
            CHECK(regs[0]!=0&&g_heap_cur==0);
        } else {
            CHECK(!actual_set_heap(regs)&&regs[0]==0&&regs[1]==(uintptr_t)g_heap&&g_heap_cur==g_heap_cap);
            CHECK(!botw_base_v0_heap_verify(g_heap_cur,error,sizeof error));
            CHECK(botw_base_v0_heap_verify(g_heap_cur-2*MIB,error,sizeof error));
        }
        CHECK(!munmap(g_heap,g_heap_cap));
#endif
    } else return 2;
    puts("BASE_V0_HEAP_CPU_OK"); return 0;
}
