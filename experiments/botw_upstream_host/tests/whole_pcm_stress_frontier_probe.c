/* Owned schedule around the original consumer loop and complete original PCM.
 * No hardware, guest, GPU, or replacement sample/counter algorithm. */
#include <pthread.h>
static pthread_mutex_t schedule_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t schedule_changed=PTHREAD_COND_INITIALIZER;
static unsigned permits,at_checkpoint;
static int quit;
static int owned_pcm_consumer_step(void *context){
    (void)context;
    pthread_mutex_lock(&schedule_mutex);at_checkpoint++;
    pthread_cond_broadcast(&schedule_changed);
    while(!permits&&!quit)pthread_cond_wait(&schedule_changed,&schedule_mutex);
    if(quit){pthread_mutex_unlock(&schedule_mutex);return 0;}
    permits--;pthread_mutex_unlock(&schedule_mutex);return 1;
}
#define main upstream_pcm_suite_main
#include "dm/host/tests/t_botw_pcm.c"
#undef main
static void wait_checkpoint(unsigned count){
    pthread_mutex_lock(&schedule_mutex);
    while(at_checkpoint<count)pthread_cond_wait(&schedule_changed,&schedule_mutex);
    pthread_mutex_unlock(&schedule_mutex);
}
static void consume_one_iteration(unsigned done){
    pthread_mutex_lock(&schedule_mutex);permits++;
    pthread_cond_broadcast(&schedule_changed);pthread_mutex_unlock(&schedule_mutex);
    wait_checkpoint(done);
}
int main(int argc,char **argv){
    const int observed=argc>1&&!strcmp(argv[1],"observed");
    const int corrupt=argc>1&&!strcmp(argv[1],"corrupt-gap");
    au_reset();botw_pcm_sink *s=botw_pcm_open(1);
    CHECK(s&&botw_pcm_set_active(s,1)==0);
    consumer_ctx c={.sink=s,.rng=12345};pthread_t thread;
    CHECK(!pthread_create(&thread,NULL,consumer_main,&c));
    g_au.consumer_to_join=&thread;g_au.consumer_stop=&c.stop;
    wait_checkpoint(1);
    int16_t first[2];fill_seq(first,0,1);CHECK_EQ(botw_pcm_write(s,first,1,2),1);
    consume_one_iteration(2);CHECK_EQ(c.nonzero_frames,1);CHECK_EQ(c.expect,1);
    int16_t *ring=malloc(PCM_RING_FRAMES*PCM_FRAME_BYTES);
    CHECK(ring!=NULL);fill_seq(ring,1,PCM_RING_FRAMES);
    CHECK_EQ(botw_pcm_write(s,ring,PCM_RING_FRAMES,2),PCM_RING_FRAMES);
    CHECK_EQ(botw_pcm_set_active(s,0),0);
    CHECK_EQ(counters(s).dropped_frames,PCM_RING_FRAMES);
    CHECK_EQ(botw_pcm_set_active(s,1),0);
    int16_t last[32];fill_seq(last,1+PCM_RING_FRAMES,16);
    CHECK_EQ(botw_pcm_write(s,last,16,2),16);
    if(observed)consume_one_iteration(3);
    botw_pcm_counters before=counters(s);
    CHECK_EQ(botw_pcm_set_active(s,0),0);
    botw_pcm_counters after=counters(s);
    const uint64_t final_flush=after.dropped_frames-before.dropped_frames;
    CHECK_EQ(after.submitted_frames,1+PCM_RING_FRAMES+16);
    CHECK_EQ(after.consumed_frames,observed?17:1);
    CHECK_EQ(after.dropped_frames,observed?PCM_RING_FRAMES:PCM_RING_FRAMES+16);
    atomic_store_explicit(&c.stop,1,memory_order_release);
    pthread_mutex_lock(&schedule_mutex);quit=1;
    pthread_cond_broadcast(&schedule_changed);pthread_mutex_unlock(&schedule_mutex);
    botw_pcm_close(s);CHECK(g_au.dispose_calls==1);
    if(corrupt)c.gap_total++;
    check_stress_sequence(&c,after.submitted_frames,after.dropped_frames,final_flush);
    CHECK(c.zero_frames>0&&c.renders>0);
    fprintf(stderr,"owned stress frontier: consumed=%llu gaps=%llu frontier=%lld submitted=%llu dropped=%llu final_flush=%llu\n",
        (unsigned long long)c.nonzero_frames,(unsigned long long)c.gap_total,(long long)c.expect,
        (unsigned long long)after.submitted_frames,(unsigned long long)after.dropped_frames,(unsigned long long)final_flush);
    free(ring);return g_fail?1:0;
}
