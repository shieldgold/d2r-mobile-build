/* Actual original XQ, present worker/link callback and suspend admission.
 * Owned CPU queues only. The drawable/encoder seam can hold a CPU borrow or
 * explicitly delay the exact original completion body; no GPU is created. */
#import <Foundation/Foundation.h>
#include <assert.h>
#include <errno.h>
#include <mach/mach.h>
#include <pthread.h>
#include <pthread/qos.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "whole_pool_backing_gate.h"
#define LOG(...) ((void)0)
#define MLOG(...) ((void)0)
#define PQ 8
#define PRING 16
#define CHECK(x) do { if(!(x)){fprintf(stderr,"owned suspend behavior:%d: %s\n",__LINE__,#x);_Exit(91);} }while(0)
typedef struct {uint64_t serial;} tex_t; /* Queue payload identity, not a Guest ABI. */
typedef struct {tex_t t;double period;uint64_t frame;int crop[4];double t_in;} pq_item;
typedef struct {uint64_t n;double t;} pres_ent;
static pq_item g_pq[PQ];static pres_ent g_pres[PRING];
static pthread_mutex_t g_pq_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pq_cv=PTHREAD_COND_INITIALIZER;
static pthread_mutex_t g_link_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_link_cv=PTHREAD_COND_INITIALIZER;
static uint64_t g_pq_read,g_pq_in,g_pq_done,g_link_dropped,g_link_tick_ns,g_link_tick_n;
static int g_pq_inflight,g_link_owns_layer,g_classic_busy,g_dm_present_link;
static unsigned g_pool_present_busy;
static _Atomic uint32_t g_present_qos;
static _Atomic int g_present_discard;
static _Atomic uint64_t g_cur_frame;
static uint64_t g_xv_present[128];static unsigned g_xv_npresent;
static uint64_t g_stall_t0;static double g_link_it_tin;
static _Atomic unsigned present_calls,drain_calls,suspend_returned,producer_entered,producer_returned;
static _Atomic int worker_allowed,hold_cpu,cpu_entered,hold_gpu,gpu_entered,hold_link,link_entered;
static uint64_t gpu_ticket,late_ticket;
static uint64_t now_ns(void){struct timespec n;clock_gettime(CLOCK_MONOTONIC,&n);return(uint64_t)n.tv_sec*1000000000ull+n.tv_nsec;}
static double CACurrentMediaTime(void){return(double)now_ns()/1e9;}
static void nap(void){struct timespec d={0,1000000};nanosleep(&d,NULL);}
static int await_unsigned(_Atomic unsigned *value,unsigned target){for(int i=0;i<1000;i++){if(atomic_load(value)>=target)return 1;nap();}return 0;}
static int await_int(_Atomic int *value){for(int i=0;i<1000;i++){if(atomic_load(value))return 1;nap();}return 0;}
_Noreturn static void die(const char *why){(void)why;abort();}
#define MDIE die
static void xv_check(uint64_t s,int a,int b,const char *why){(void)s;(void)a;(void)b;(void)why;}
static void link_sync(void){atomic_fetch_add(&present_calls,1);atomic_store(&link_entered,1);while(atomic_load(&hold_link))nap();}
static int link_on(void){return g_dm_present_link;}
static uint64_t dm_focus_in_since(void){return 0;}
static void mtl_link_pause(int on){(void)on;CHECK(!"diagnostic stall control is not enabled");}
static int present_now(const tex_t *t,double p,uint64_t f,uint64_t ticket,const int crop[4],id drawable,double ts){
 (void)t;(void)p;(void)f;(void)crop;(void)drawable;(void)ts;
 atomic_store(&cpu_entered,1);while(atomic_load(&hold_cpu))nap();
 if(atomic_load(&hold_gpu)){
  pthread_mutex_lock(&g_pq_mu);g_pq_inflight++;gpu_ticket=ticket;pthread_mutex_unlock(&g_pq_mu);
  atomic_store(&gpu_entered,1);return 1;
 }
 return 0; /* The real no-drawable/error path: CPU borrow ends without GPU work. */
}
/* A real pthread is created by the exact production present_start. Delay only
 * its initial scheduling so queued-only pressure is deterministic in both modes. */
static void *present_main(void *);
typedef struct {void *(*entry)(void*);void *arg;} owned_start;
static void *owned_boot(void *arg){owned_start s=*(owned_start*)arg;free(arg);while(!atomic_load(&worker_allowed))nap();return s.entry(s.arg);}
static int owned_present_create(pthread_t *t,const pthread_attr_t *a,void *(*entry)(void*),void *arg){
 CHECK(entry==present_main);owned_start *s=malloc(sizeof *s);CHECK(s);*s=(owned_start){entry,arg};
 int result=pthread_create(t,a,owned_boot,s);if(result)free(s);return result;
}
#define pthread_create owned_present_create
#include "actual_present_queue.inc"
#undef pthread_create
/* Namespace the CPU protocol objects away from classes indirectly loaded by Foundation. */
#define CAMetalDisplayLink OwnedMetalDisplayLink
#define CAMetalDisplayLinkUpdate OwnedMetalDisplayLinkUpdate
@interface CAMetalDisplayLink:NSObject @end
@implementation CAMetalDisplayLink @end
@interface CAMetalDisplayLinkUpdate:NSObject
@property(nonatomic,strong) id drawable;
@property(nonatomic) double targetPresentationTimestamp;
@end
@implementation CAMetalDisplayLinkUpdate @end
@interface NvMetalDisplayLinkDelegate:NSObject
- (void)metalDisplayLink:(CAMetalDisplayLink*)link needsUpdate:(CAMetalDisplayLinkUpdate*)update;
@end
#include "actual_link_delegate.inc"
static void gpu_complete(void){uint64_t ticket=gpu_ticket;
#include "actual_present_completion.inc"
}
typedef struct dtex_overlay dtex_overlay;
static _Atomic uint64_t g_x_subs;static struct {int unused;} g_rt_xlat;
static void x_submit(const uint64_t *h,int n,uint64_t sub,uint64_t host,const dtex_overlay *d){(void)h;(void)n;(void)sub;(void)host;(void)d;CHECK(!"no submit job is selected");}
static void dtex_free(dtex_overlay *d){(void)d;CHECK(!"no submit job is selected");}
static void mtl_set_frame(uint64_t f){(void)f;CHECK(!"no frame job is selected");}
static int dm_detclock_enabled(void){return 0;}
static void set_time_constraint(void *p,uint64_t n){(void)p;(void)n;CHECK(!"no frame job is selected");}
static void rt_comp_tick(void *p,uint64_t n){(void)p;(void)n;CHECK(!"no frame job is selected");}
#include "actual_xq.inc"
static botw_whole_pool_gate g_pool_gate=BOTW_WHOLE_POOL_GATE_INITIALIZER;
static void mtl_drain(void){
 atomic_fetch_add(&drain_calls,1);present_drop_for_suspend();
 /* Remaining real GPU commit/drain is deliberately not executed. The exact
  * first production drop/wait must already retain CPU/GPU ownership here. */
}
#include "actual_suspend.inc"
static tex_t texture={1};
static void *suspend_main(void *unused){(void)unused;dm_gfx_suspend(1);atomic_store(&suspend_returned,1);return NULL;}
static void *late_present(void *unused){(void)unused;late_ticket=mtl_present(&texture,1.0/60.0,NULL);return NULL;}
static void *reader_enqueue(void *unused){
 (void)unused;CHECK(botw_whole_pool_gate_read(&g_pool_gate));atomic_store(&producer_entered,1);
 xjob j={.kind=XJ_PRESENT,.tex=&texture,.period=1.0/60.0};x_push(&j);
 botw_whole_pool_gate_leave(&g_pool_gate,1);atomic_store(&producer_returned,1);return NULL;
}
static void *link_callback(void *unused){(void)unused;@autoreleasepool{
 NvMetalDisplayLinkDelegate *delegate=[NvMetalDisplayLinkDelegate new];
 [delegate metalDisplayLink:nil needsUpdate:[CAMetalDisplayLinkUpdate new]];
 }return NULL;}
static void wait_done(uint64_t n){
 for(int i=0;i<1000;i++){pthread_mutex_lock(&g_pq_mu);int ok=g_pq_done>=n;pthread_mutex_unlock(&g_pq_mu);if(ok)return;nap();}CHECK(!"present did not finish");
}
static void resume_and_use(uint64_t previous){
 dm_gfx_suspend(0);CHECK(!atomic_load(&g_present_discard));
 pthread_mutex_lock(&g_pool_gate.mutex);CHECK(!g_pool_gate.suspended&&!g_pool_gate.writer);pthread_mutex_unlock(&g_pool_gate.mutex);
 CHECK(mtl_present(&texture,1.0/60.0,NULL)==previous+1);
 if(g_dm_present_link)link_callback(NULL);else atomic_store(&worker_allowed,1);
 wait_done(previous+1);CHECK(g_pq_in==previous+1&&g_pq_done>=previous+1);
}
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2&&!getenv("DM_NVN_SYNC")&&!getenv("DM_TEST_LINK_STALL"));const char *mode=argv[1];
 int link=strstr(mode,"-link")!=NULL;g_dm_present_link=link;g_link_owns_layer=link;
 if(!strcmp(mode,"empty")){dm_gfx_suspend(1);CHECK(atomic_load(&drain_calls)==1);dm_gfx_suspend(0);CHECK(!atomic_load(&present_calls));goto done;}
 if(!strncmp(mode,"late-",5)){
  atomic_store(&hold_link,1);pthread_t p;CHECK(!pthread_create(&p,NULL,late_present,NULL));CHECK(await_int(&link_entered));
  mtl_present_discard(1);atomic_store(&hold_link,0);CHECK(!pthread_join(p,NULL));
  CHECK(late_ticket==0&&g_pq_in==0&&g_pq_done==0);mtl_present_discard(0);goto done;
 }
 if(!strncmp(mode,"queued-",7)||!strncmp(mode,"xq-reader-",10)){
  for(unsigned i=0;i<PQ;i++)CHECK(mtl_present(&texture,1.0/60.0,NULL)==i+1);
  xjob j={.kind=XJ_PRESENT,.tex=&texture,.period=1.0/60.0};CHECK(x_push(&j)==1);CHECK(await_unsigned(&present_calls,PQ+1));
  int fullx=!strncmp(mode,"xq-reader-",10);pthread_t producer;
  if(fullx){for(int i=1;i<XQ;i++)x_push(&j);CHECK(!pthread_create(&producer,NULL,reader_enqueue,NULL));CHECK(await_unsigned(&producer_entered,1));}
  pthread_t stop;CHECK(!pthread_create(&stop,NULL,suspend_main,NULL));CHECK(await_unsigned(&suspend_returned,1));CHECK(!pthread_join(stop,NULL));
  if(fullx){CHECK(!pthread_join(producer,NULL));CHECK(atomic_load(&producer_returned));}
  CHECK(g_pq_read==PQ&&g_pq_done==PQ&&g_link_dropped==PQ&&atomic_load(&g_present_discard));
  CHECK(g_xq_done==g_xq_in);resume_and_use(PQ);goto done;
 }
 CHECK(!strncmp(mode,"cpu-",4)||!strncmp(mode,"gpu-",4));int cpu=!strncmp(mode,"cpu-",4);
 atomic_store(&hold_cpu,cpu);atomic_store(&hold_gpu,!cpu);CHECK(mtl_present(&texture,1.0/60.0,NULL)==1);
 pthread_t callback;
 if(link)CHECK(!pthread_create(&callback,NULL,link_callback,NULL));else atomic_store(&worker_allowed,1);
 CHECK(cpu?await_int(&cpu_entered):await_int(&gpu_entered));
 if(!cpu&&link)CHECK(!pthread_join(callback,NULL));
 if(cpu){for(int i=0;i<7;i++)CHECK(mtl_present(&texture,1.0/60.0,NULL)==(uint64_t)i+2);}
 pthread_t stop;CHECK(!pthread_create(&stop,NULL,suspend_main,NULL));
 for(int i=0;i<100;i++)nap();
 pthread_mutex_lock(&g_pool_gate.mutex);int admitted=g_pool_gate.suspended&&g_pool_gate.writer;pthread_mutex_unlock(&g_pool_gate.mutex);
 CHECK(admitted&&atomic_load(&g_present_discard)&&!atomic_load(&suspend_returned));
 pthread_mutex_lock(&g_pq_mu);CHECK(g_pq_done==0&&g_pq_read==g_pq_in);pthread_mutex_unlock(&g_pq_mu);
 if(cpu){atomic_store(&hold_cpu,0);if(link)CHECK(!pthread_join(callback,NULL));}
 else {atomic_store(&hold_gpu,0);gpu_complete();}
 CHECK(await_unsigned(&suspend_returned,1));CHECK(!pthread_join(stop,NULL));CHECK(g_pq_done==g_pq_in&&!g_pool_present_busy&&!g_pq_inflight);
 resume_and_use(g_pq_in);
 done:puts("WHOLE_PRESENT_SUSPEND_CPU_OK");return 0;
}}
