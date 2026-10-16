/* SPDX-License-Identifier: MIT
 * The exact production lifecycle with owned ACK/failure producers only.
 * No runtime configuration/prepare/host entry, device, guest or Metal is run. */
#include "../frontend/whole_runtime_configuration.m"
#include <assert.h>
static char calls[64];static size_t used;static char fail_at;
static int record(char c) {assert(used+1<sizeof calls);calls[used++]=c;calls[used]=0;return c==fail_at;}
botw_audren_result botw_whole_audren_set_active(botw_whole_audren *owner,int active,char *error,size_t cap) {
    assert(owner);(void)error;(void)cap;
    return (botw_audren_result){record(active?'A':'a')?BOTW_AUDREN_DEVICE_ERROR:0,0,0};
}
int whole_audio_platform_set_active(int active,char *error,size_t cap) {
    (void)error;(void)cap;return record(active?'P':'p')?-1:0;
}
void dm_gfx_suspend(int suspend) {(void)record(suspend?'g':'G');}
int whole_applet_publish(int foreground,int focused,unsigned op,unsigned perf,char *error,size_t cap) {
    assert((foreground==0||foreground==1)&&(focused==0||focused==1)&&!op&&!perf);
    (void)error;(void)cap;return record('o')?-1:0;
}
int botw_services_set_applet_state(botw_services *owner,uint64_t id,int foreground,int focused,uint32_t op,char *error,size_t cap) {
    assert(owner&&id==(uint64_t)(unsigned)getpid()&&(foreground==0||foreground==1)&&(focused==0||focused==1)&&!op);
    (void)error;(void)cap;return record('s')?-1:0;
}
_Noreturn void botw_whole_host_failure(void) {_Exit(70);}
static void *owned_failure(void *opaque) {fatal(opaque,BOTW_AUDREN_DEVICE_ERROR);return NULL;}
int main(int argc,char **argv) {
    assert(argc==2);runtime_context context={.lock=PTHREAD_MUTEX_INITIALIZER};
    context.aruid=(uint64_t)(unsigned)getpid();context.services=(void*)&used;context.audren=(void*)&calls;
    atomic_init(&context.phase,RUNTIME_READY);atomic_init(&context.fatal,0);char error[256]={0};
    if(!strcmp(argv[1],"fatal-dispatch")) {
        pthread_t producer;assert(!pthread_create(&producer,NULL,owned_failure,&context));
        /* Completion must arrive via the normal main queue. No worker join,
         * engine teardown or fabricated guest completion is used to exit. */
        dispatch_main();
    } else if(!strcmp(argv[1],"cycle")) {
        assert(!lifecycle(&context,0,0,0,error,sizeof error));assert(!strcmp(calls,"apgos"));
        used=0;calls[0]=0;
        assert(!lifecycle(&context,1,1,1,error,sizeof error));assert(!strcmp(calls,"PGAos"));
        assert(!atomic_load(&context.fatal)&&atomic_load(&context.phase)==RUNTIME_READY);
    } else if(!strcmp(argv[1],"unprepared")) {
        atomic_store(&context.phase,RUNTIME_COLD);
        assert(lifecycle(&context,1,1,1,error,sizeof error));assert(!used&&error[0]);
    } else {
        fail_at=argv[1][0];int resume=fail_at=='P'||fail_at=='A';
        assert(lifecycle(&context,resume,resume,resume,error,sizeof error));
        assert(atomic_load(&context.fatal)==((fail_at=='a'||fail_at=='A')?BOTW_AUDREN_DEVICE_ERROR:BOTW_AUDREN_SYSTEM_ERROR));
        assert(atomic_load(&context.phase)==RUNTIME_FAILED&&error[0]);
        assert(calls[used-1]==fail_at); /* No later producer receives a false ACK. */
        size_t before=used;assert(lifecycle(&context,1,1,1,error,sizeof error));assert(used==before);
    }
    pthread_mutex_destroy(&context.lock);return 0;
}
