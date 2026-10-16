/* Actual staged lifecycle/drop functions, with owned CPU queue state only. */
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "whole_pool_backing_gate.h"
#define PRING 16
typedef struct {uint64_t ticket;int status;} pres_ent;
static pres_ent g_pres[PRING];
static pthread_mutex_t g_pq_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pq_cv=PTHREAD_COND_INITIALIZER;
static int g_link_owns_layer=1,g_pq_inflight;
static unsigned g_pool_present_busy;
static uint64_t g_pq_read,g_pq_in,g_pq_done,g_link_dropped;
static _Atomic int entered,finished,flushes;
#define MLOG(...) ((void)0)
static void die(const char *s){fprintf(stderr,"%s\n",s);abort();}
static void x_drain(void){}
void mtl_pool_drain(void){}
#include "suspend_drop.inc"
static void mtl_drain(void){present_drop_for_suspend();atomic_fetch_add(&flushes,1);}
#include "suspend_gate.inc"
static void pause_cpu(void){struct timespec t={0,1000000};nanosleep(&t,NULL);}
static void *suspend_thread(void *unused){(void)unused;atomic_store(&entered,1);dm_gfx_suspend(1);atomic_store(&finished,1);return NULL;}
int main(int argc,char **argv){
    if(argc!=2)return 2;
    int cpu=!strcmp(argv[1],"cpu"),gpu=!strcmp(argv[1],"gpu");
    if(!cpu&&!gpu&&strcmp(argv[1],"queued"))return 2;
    g_pq_in=2;g_pq_read=1;g_pool_present_busy=cpu;g_pq_inflight=gpu;
    pthread_t thread;if(pthread_create(&thread,NULL,suspend_thread,NULL))return 3;
    while(!atomic_load(&entered))pause_cpu();
    for(int i=0;i<30;i++)pause_cpu();
    int early=(cpu||gpu)&&(atomic_load(&finished)||atomic_load(&flushes));
    pthread_mutex_lock(&g_pq_mu);
    g_pool_present_busy=0;g_pq_inflight=0;
    pthread_cond_broadcast(&g_pq_cv);pthread_mutex_unlock(&g_pq_mu);
    pthread_join(thread,NULL);
    dm_gfx_suspend(0);
    if(early){fprintf(stderr,"suspend returned before actual dequeued CPU/GPU borrow completed\n");return 91;}
    if(!atomic_load(&finished)||atomic_load(&flushes)!=1||g_pq_done!=2||g_link_dropped!=1)return 92;
    puts("WHOLE_POOL_SUSPEND_CPU_OK");return 0;
}
