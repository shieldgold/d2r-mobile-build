/* Complete original WindowAcquire and the actual candidate QueueAcquire bodies.
 * Owned CPU objects/clock/callbacks only; no Guest instructions or GPU work. */
#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvn_protos.h"
#include "normal_queue_types.inc"
_Static_assert(NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS==0,"Queue result is not a boolean");
_Static_assert(NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_NATIVE_ERROR==1,"Queue native error ABI");
_Static_assert(NVN_WINDOW_ACQUIRE_TEXTURE_RESULT_SUCCESS==0,"Window success ABI");
static rt_decl g_rt_main;
static uint64_t g_frame=7,g_acq_wait_ns,g_pq_wait_ns;
enum event { PERIOD=1,CONSTRAINT,RT_TICK,LINK_ACTIVE,LINK_WAIT,SLEEP,STAT,TICKET,PRESENT_WAIT,FENCE };
static enum event events[64];static unsigned event_count;
static uint64_t clock_now=100,period=100,observed_ticket,observed_present,sleep_deadline;
static unsigned clock_calls,ticket_calls,present_calls,sleep_calls,link_calls,fence_calls;
static int link_active,link_ready,ticket_dropped;
static void event(enum event value) { assert(event_count<64);events[event_count++]=value; }
static int dm_detclock_enabled(void) { return 0; } /* Ordinary mode only. */
static uint64_t guest_ns(void) { assert(!"diagnostic clock branch entered");return 0; }
static uint64_t now_ns(void) { clock_calls++;return clock_now; }
static uint64_t present_period(const win_t *window) { assert(window);event(PERIOD);return period; }
static void set_time_constraint(rt_decl *decl,uint64_t value) { assert(decl==&g_rt_main&&value==period);event(CONSTRAINT); }
static void rt_comp_tick(rt_decl *decl,uint64_t value) { assert(decl==&g_rt_main&&value==period);event(RT_TICK); }
static int mtl_link_active(void) { event(LINK_ACTIVE);return link_active; }
static int mtl_link_wait_tick(uint64_t timeout) { assert(timeout==(period?2*period:33333333ull));link_calls++;event(LINK_WAIT);return link_ready; }
static void sleep_until_ns(uint64_t deadline) { sleep_calls++;sleep_deadline=deadline;clock_now=deadline;event(SLEEP); }
static void trow(uint64_t frame,const char *name,double value) { assert(frame==g_frame&&!strcmp(name,"@late_ms")&&value>=0);event(STAT); }
static uint64_t x_present_ticket(uint64_t ticket) { ticket_calls++;observed_ticket=ticket;event(TICKET);return ticket_dropped?0:ticket+1000; }
static void mtl_present_wait(uint64_t ticket) { present_calls++;observed_present=ticket;clock_now+=7;event(PRESENT_WAIT); }
static uint64_t x_fence(void) { fence_calls++;event(FENCE);return UINT64_C(0xabc); }
#define NVN(name,ret,...) static ret h_##name(__VA_ARGS__)
#include "normal_queue_functions.inc"
static const NVNT_nvnWindowAcquireTexture window_acquire=h_nvnWindowAcquireTexture;
#if BOTW_HAS_QUEUE_ACQUIRE
static const NVNT_nvnQueueAcquireTexture queue_acquire=h_nvnQueueAcquireTexture;
#endif
static NVNqueue queue_object;static NVNwindow window_object;
typedef struct { uint32_t before;int index;uint32_t after; } guarded_index;
static guarded_index output;
static void reset_events(void) { event_count=0;clock_calls=ticket_calls=present_calls=sleep_calls=link_calls=fence_calls=0;observed_ticket=observed_present=sleep_deadline=0; }
static void setup(void) {
    memset(&queue_object,0xa5,sizeof queue_object);memset(&window_object,0xa5,sizeof window_object);
    queue_t *q=(void *)&queue_object;win_t *w=(void *)&window_object;
    *q=(queue_t){.magic=MAGIC('Q','U','E','U'),.submits=17};
    *w=(win_t){.magic=MAGIC('W','I','N','D'),.ntex=3,.interval=1,.avail={1000,2000,3000,4000},.ticket={11,22,33,44},.pace=123,.presents=6};
    output=(guarded_index){0x12345678,-77,0x87654321};reset_events();
}
static void guards(void) { assert(output.before==0x12345678&&output.after==0x87654321); }
static int position(enum event value) { for(unsigned i=0;i<event_count;i++)if(events[i]==value)return (int)i;return -1; }
static void normal_wait_order(uint64_t ticket) {
    assert(position(PERIOD)>=0&&position(PERIOD)<position(RT_TICK));
    assert(position(RT_TICK)<position(LINK_ACTIVE)&&position(LINK_ACTIVE)<position(STAT));
    assert(position(STAT)<position(TICKET)&&position(TICKET)<position(PRESENT_WAIT));
    assert(ticket_calls==1&&present_calls==1&&observed_ticket==ticket&&observed_present==ticket+1000);
}
static void original_window(void) {
    setup();NVNsync sync;memset(&sync,0,sizeof sync);
    assert(window_acquire(&window_object,&sync,&output.index)==NVN_WINDOW_ACQUIRE_TEXTURE_RESULT_SUCCESS);
    assert(output.index==0&&((win_t *)&window_object)->next==1);guards();normal_wait_order(11);
    assert(sleep_calls==1&&sleep_deadline==1000&&g_acq_wait_ns==900&&g_pq_wait_ns==7);
    assert(((sync_t *)&sync)->fenced==1&&((sync_t *)&sync)->fence==0xabc&&fence_calls==1);
    assert(position(PRESENT_WAIT)<position(FENCE));
}
#if BOTW_HAS_QUEUE_ACQUIRE
static void queue_success(void) {
    setup();NVNqueue q_before=queue_object;win_t before=*(win_t *)&window_object;
    assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS);
    assert(output.index==0&&((win_t *)&window_object)->next==1);guards();normal_wait_order(11);
    assert(sleep_calls==1&&sleep_deadline==1000&&!fence_calls);
    assert(!memcmp(&queue_object,&q_before,sizeof q_before));before.next=1;assert(!memcmp((win_t *)&window_object,&before,sizeof before));
    const enum event expected[]={PERIOD,CONSTRAINT,RT_TICK,LINK_ACTIVE,SLEEP,STAT,TICKET,PRESENT_WAIT};
    assert(event_count==sizeof expected/sizeof expected[0]&&!memcmp(events,expected,sizeof expected));
}
static void interleaved(void) {
    setup();const int indices[]={0,1,2,0};const uint64_t tickets[]={11,22,33,11};
    for(unsigned i=0;i<4;i++) {
        reset_events();
        if(i&1)assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS);
        else assert(window_acquire(&window_object,NULL,&output.index)==NVN_WINDOW_ACQUIRE_TEXTURE_RESULT_SUCCESS);
        assert(output.index==indices[i]&&((win_t *)&window_object)->next==(int)i+1);normal_wait_order(tickets[i]);guards();assert(!fence_calls);
    }
}
static void pacing(const char *mode) {
    setup();link_active=1;link_ready=!strcmp(mode,"link-ready");if(!strcmp(mode,"no-period"))period=0;
    assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS);
    assert(link_calls==1&&position(LINK_WAIT)>position(LINK_ACTIVE)&&position(LINK_WAIT)<position(STAT));
    assert(sleep_calls==(unsigned)!link_ready);if(sleep_calls)assert(position(LINK_WAIT)<position(SLEEP)&&position(SLEEP)<position(STAT));normal_wait_order(11);assert(!fence_calls);
}
static void no_ticket(int dropped) {
    setup();if(dropped)ticket_dropped=1;else ((win_t *)&window_object)->ticket[0]=0;
    assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS);
    assert(ticket_calls==(unsigned)dropped&&!present_calls&&!fence_calls&&position(STAT)>=0);guards();
}
static void invalid(const char *mode) {
    setup();queue_t *q=(void *)&queue_object;win_t *w=(void *)&window_object;
    NVNqueue *qp=&queue_object;NVNwindow *wp=&window_object;int *ip=&output.index;
    if(!strcmp(mode,"null-q"))qp=NULL;else if(!strcmp(mode,"null-w"))wp=NULL;else if(!strcmp(mode,"null-out"))ip=NULL;
    else if(!strcmp(mode,"bad-q"))q->magic=0;else if(!strcmp(mode,"bad-w"))w->magic=0;
    else if(!strcmp(mode,"count-zero"))w->ntex=0;else if(!strcmp(mode,"count-negative"))w->ntex=-1;else if(!strcmp(mode,"count-five"))w->ntex=5;else if(!strcmp(mode,"count-max"))w->ntex=INT32_MAX;
    else if(!strcmp(mode,"next-negative"))w->next=-1;else if(!strcmp(mode,"next-min"))w->next=INT32_MIN;else if(!strcmp(mode,"next-max"))w->next=INT32_MAX;else assert(!"unknown invalid mode");
    NVNqueue qb=queue_object;NVNwindow wb=window_object;guarded_index old=output;
    assert(queue_acquire(qp,wp,ip)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_NATIVE_ERROR);
    assert(!memcmp(&queue_object,&qb,sizeof qb)&&!memcmp(&window_object,&wb,sizeof wb)&&!memcmp(&output,&old,sizeof old));
    assert(!event_count&&!clock_calls&&!ticket_calls&&!present_calls&&!sleep_calls&&!link_calls&&!fence_calls);
}
static void counts(void) {
    const int expected[4][4]={{0,0,0,0},{0,1,0,1},{0,1,2,0},{0,1,2,3}};
    for(int count=1;count<=4;count++) {
        setup();((win_t *)&window_object)->ntex=count;
        for(int i=0;i<4;i++){reset_events();assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS);assert(output.index==expected[count-1][i]);guards();}
    }
}
static void overflow_boundary(void) {
    setup();win_t *w=(void *)&window_object;w->next=INT32_MAX-1;w->ntex=1;
    assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_SUCCESS);assert(w->next==INT32_MAX&&output.index==0);
    reset_events();NVNwindow before=window_object;guarded_index old=output;
    assert(queue_acquire(&queue_object,&window_object,&output.index)==NVN_QUEUE_ACQUIRE_TEXTURE_RESULT_NATIVE_ERROR);
    assert(!memcmp(&window_object,&before,sizeof before)&&!memcmp(&output,&old,sizeof old)&&!event_count&&!clock_calls);
}
#endif
int main(int argc,char **argv) {
    assert(argc==2);if(!strcmp(argv[1],"window")){original_window();return 0;}
#if BOTW_HAS_QUEUE_ACQUIRE
    if(!strcmp(argv[1],"queue"))queue_success();else if(!strcmp(argv[1],"interleaved"))interleaved();else if(!strcmp(argv[1],"link-ready")||!strcmp(argv[1],"link-fallback")||!strcmp(argv[1],"no-period"))pacing(argv[1]);else if(!strcmp(argv[1],"no-ticket"))no_ticket(0);else if(!strcmp(argv[1],"dropped-ticket"))no_ticket(1);else if(!strcmp(argv[1],"counts"))counts();else if(!strcmp(argv[1],"overflow"))overflow_boundary();else invalid(argv[1]);
#else
    assert(!"QueueAcquire implementation is absent; no fallback/stub supplied");
#endif
    return 0;
}
