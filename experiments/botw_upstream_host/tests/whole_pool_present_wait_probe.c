/* The actual original watchdog with its actual 100ms timeout. Owned CPU queue
 * state only: no drawable, GPU, display link or complete host is instantiated. */
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <errno.h>
#include <string.h>
#define PRING 16
#define STALL_NS 100000000ull
#define STALL_RED_NS 2000000000ull
#define MLOG(...) ((void)0)
typedef struct {uint64_t ticket;int status;} pres_ent;
static pres_ent g_pres[PRING];
static pthread_mutex_t g_pq_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pq_cv=PTHREAD_COND_INITIALIZER;
static uint64_t g_pq_in=2,g_pq_read=1,g_pq_done,g_link_tick_ns,g_link_dropped;
static int g_link_owns_layer=1,g_pq_inflight;
static unsigned g_pool_present_busy;
static _Atomic int g_present_discard,returned;
static uint64_t g_stall_t0,g_stall_drop,g_stall_n;
static int g_stall_red,g_stall_logged;
static uint64_t now_ns(void){struct timespec now;clock_gettime(CLOCK_MONOTONIC,&now);return now.tv_sec*1000000000ull+now.tv_nsec;}
static int dm_focus_now(void){return 0;}
static uint64_t dm_focus_in_since(void){return 0;}
#include "actual_present_wait.inc"
static void pause_cpu(void){struct timespec delay={0,1000000};nanosleep(&delay,NULL);}
static void *finish(void *unused){(void)unused;mtl_present_wait(g_pq_in);atomic_store(&returned,1);return NULL;}
int main(int argc,char **argv){
    if(argc!=2)return 2;
    int cpu=!strcmp(argv[1],"cpu"),gpu=!strcmp(argv[1],"gpu");
    if(!cpu&&!gpu&&strcmp(argv[1],"queued"))return 2;
    g_pool_present_busy=cpu;g_pq_inflight=gpu;
    pthread_t thread;if(pthread_create(&thread,NULL,finish,NULL))return 3;
    int observed=0;
    /* Observe the original watchdog's actual drop, then prove it has not
     * published completion while an earlier dequeued borrow still lives. */
    for(int i=0;i<3000;i++){
        pthread_mutex_lock(&g_pq_mu);observed=g_pq_read==2;pthread_mutex_unlock(&g_pq_mu);
        if(observed)break;pause_cpu();
    }
    for(int i=0;i<30;i++)pause_cpu();
    int early=(cpu||gpu)&&atomic_load(&returned);
    pthread_mutex_lock(&g_pq_mu);int false_done=(cpu||gpu)&&g_pq_done>=2;
    g_pool_present_busy=0;g_pq_inflight=0;pthread_cond_broadcast(&g_pq_cv);pthread_mutex_unlock(&g_pq_mu);
    pthread_join(thread,NULL);
    if(early||false_done){fprintf(stderr,"actual watchdog ACK precedes earlier dequeued CPU/GPU borrow completion\n");return 91;}
    if(!observed||!atomic_load(&returned)||g_pq_done!=2||g_link_dropped!=1)return 92;
    puts("WHOLE_POOL_PRESENT_WAIT_CPU_OK");return 0;
}
