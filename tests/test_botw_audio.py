"""Reused AudioOut module contracts. Simulated transport tests are not sound proof.

The separate Apple integration case requires actual AudioUnit callbacks. No game
bytes, AudioRenderer mixer or silent timer is used by these tests.
"""
from pathlib import Path
import platform
import importlib.util
import re
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define _POSIX_C_SOURCE 200809L
#define BOTW_AUDIO_TEST_BACKEND 1
#include "AUDIO_SOURCE"
#include <assert.h>
#include <time.h>

static int transport_error[4], transport_calls[4];
int botw_audio_test_transport(void *output, int operation) {
    (void)output; assert(operation >= 0 && operation < 4);
    transport_calls[operation]++; return transport_error[operation];
}
static unsigned char memory[4096];
static unsigned faults, signals, closes, attaches, creates, logs;
static uint32_t next_handle = 10;
static int read_error, write_error, event_error, attach_error, signal_error, close_error;
static char fault_message[512];
static int copy_in(void *ctx, uint64_t source, void *dest, size_t bytes, char *error, size_t cap) {
    (void)ctx;
    if (read_error || source < 0x10000 || bytes > sizeof memory || source - 0x10000 > sizeof memory - bytes) {
        snprintf(error,cap,"checked guest read rejected"); return -1;
    }
    memcpy(dest, memory + (source - 0x10000), bytes); return 0;
}
static int copy_out(void *ctx, uint64_t target, const void *source, size_t bytes, char *error, size_t cap) {
    (void)ctx;
    if (write_error || target < 0x10000 || bytes > sizeof memory || target - 0x10000 > sizeof memory - bytes) {
        snprintf(error,cap,"checked guest write rejected"); return -1;
    }
    memcpy(memory + (target - 0x10000), source, bytes); return 0;
}
static int create_event(void *ctx, int initial, uint32_t *w, uint32_t *r, char *e, size_t n) {
    (void)ctx; creates++; assert(!initial);
    if (event_error) { snprintf(e,n,"kernel create rejected"); return -1; }
    *w=next_handle++; *r=next_handle++; return 0;
}
static int signal_event(void *ctx, uint32_t w, char *e, size_t n) {
    (void)ctx; assert(w >= 10); __atomic_fetch_add(&signals,1,__ATOMIC_RELAXED);
    if (signal_error) { snprintf(e,n,"kernel signal rejected"); return -1; } return 0;
}
static int close_handle(void *ctx, uint32_t h, char *e, size_t n) {
    (void)ctx; assert(h>=10); closes++; if(close_error){snprintf(e,n,"kernel close rejected");return -1;} return 0;
}
static int attach_event(void *ctx, uint64_t address, uint32_t r, char *e, size_t n) {
    (void)ctx; assert(address==0x10200 && r>=10); attaches++;
    if (attach_error) { snprintf(e,n,"original SDK attach rejected"); return -1; } return 0;
}
static void fault(void *ctx,const char *message) {
    (void)ctx; faults++; snprintf(fault_message,sizeof fault_message,"%s",message);
    /* A runtime may longjmp here. The module must release its API lock first. */
    botw_audio_stats stats; assert(!botw_audio_get_stats(g_audio,&stats));
}
static void log_message(void *ctx,const char *message) { (void)ctx; assert(message && *message); logs++; }
static botw_audio_callbacks callbacks(void) {
    return (botw_audio_callbacks){.read_guest=copy_in,.write_guest=copy_out,.create_event=create_event,
        .signal_event=signal_event,.close_handle=close_handle,.attach_system_event=attach_event,
        .fault=fault,.log=log_message};
}
static void sleep_ms(unsigned ms) { struct timespec t={ms/1000,(long)(ms%1000)*1000000}; nanosleep(&t,NULL); }
static void param(int channels) { uint32_t words[]={0,0xcafe0000u | (uint32_t)channels}; memcpy(memory+0x100,words,8); }
static void buffer(uint64_t token,uint64_t pointer,uint64_t capacity,uint64_t size) {
    uint64_t words[]={0x5a5a5a5a5a5a5a5aull,pointer,capacity,size};
    memcpy(memory+(token-0x10000),words,sizeof words);
}
static void *ptr(uint64_t a) { return (void *)(uintptr_t)a; }
static botw_audio *setup(void) {
    char error[512]; botw_audio_callbacks cb=callbacks();
    botw_audio *ctx=botw_audio_create(&cb,1,error,sizeof error); assert(ctx && !*error);
    param(2); return ctx;
}
static void open_default(void) { assert(!sdk_open_default(ptr(0x10000),ptr(0x10100))); }
static void render(unsigned bytes) {
    unsigned char output[1024]; assert(bytes<=sizeof output);
    render_pcm(g_audio->outputs[0],output,bytes);
}
static _Atomic int consumer_running;
static void *consume_many(void *opaque) {
    aout *a=opaque;unsigned char output[64];
    while(atomic_load(&consumer_running))render_pcm(a,output,sizeof output);
    return NULL;
}
int main(int argc,char **argv) {
    assert(argc==2); const char *which=argv[1]; char error[512]; botw_audio *ctx=setup();
    if (!strcmp(which,"profile")) {
        void *fn=(void *)0x1234;
        for (size_t i=0;i<sizeof audio_profile/sizeof *audio_profile;i++) {
            const audio_export *p=&audio_profile[i];
            assert(botw_audio_resolve_symbol("sdk",5,p->name,p->offset,p->size,0x340000,&fn,error,sizeof error)==1 && fn==p->function);
            assert(botw_audio_resolve_symbol("sdk",4,p->name,p->offset,p->size,0x340000,&fn,error,sizeof error)==-1 && strstr(error,"profile"));
            assert(botw_audio_resolve_symbol("main",5,p->name,p->offset,p->size,0x340000,&fn,error,sizeof error)==-1);
            assert(botw_audio_resolve_symbol("sdk",5,p->name,p->offset+4,p->size,0x340000,&fn,error,sizeof error)==-1);
            assert(botw_audio_resolve_symbol("sdk",5,p->name,p->offset,p->size+4,0x340000,&fn,error,sizeof error)==-1);
            assert(botw_audio_resolve_symbol("sdk",5,p->name,p->offset,p->size,p->offset+p->size-1,&fn,error,sizeof error)==-1);
        }
        fn=(void *)0x1234;
        assert(!botw_audio_resolve_symbol("sdk",5,"_ZN2nn5audio18StartAudioRendererEPNS0_13AudioRendererE",0,0,0,&fn,error,sizeof error) && fn==(void *)0x1234);
    } else if (!strcmp(which,"parameter-layout")) {
        memset(memory,0x5a,sizeof memory); sdk_init_param(ptr(0x10100)); uint32_t words[2]; memcpy(words,memory+0x100,8);
        assert(!words[0] && words[1]==0xcafe0000u && memory[0xff]==0x5a && memory[0x108]==0x5a);
        sdk_set_buffer(ptr(0x10300),ptr(0x10400),64,16);
        uint64_t raw[4];memcpy(raw,memory+0x300,sizeof raw);
        assert(raw[0]==0x5a5a5a5a5a5a5a5aull && raw[1]==0x10400 && raw[2]==64 && raw[3]==16);
        assert(sdk_buffer_pointer(ptr(0x10300))==ptr(0x10400) && sdk_buffer_size(ptr(0x10300))==16);
    } else if (!strcmp(which,"copy-and-completion")) {
        open_default(); assert(sdk_rate(ptr(0x10000))==48000 && sdk_channels(ptr(0x10000))==2 && sdk_format(ptr(0x10000))==2);
        memset(memory+0x400,0x31,16); buffer(0x10300,0x10400,16,16);
        assert(sdk_append(ptr(0x10000),ptr(0x10300))); memset(memory+0x400,0x7f,16);
        assert(!sdk_start(ptr(0x10000))); unsigned char output[32]; render_pcm(ctx->outputs[0],output,8);
        for(unsigned i=0;i<8;i++) assert(output[i]==0x31);
        assert(!sdk_released(ptr(0x10000))); render_pcm(ctx->outputs[0],output,32);
        for(unsigned i=0;i<8;i++) assert(output[i]==0x31);for(unsigned i=8;i<32;i++) assert(!output[i]);
        assert(sdk_released(ptr(0x10000))==ptr(0x10300) && !sdk_released(ptr(0x10000)));
        botw_audio_stats stats; assert(!botw_audio_get_stats(ctx,&stats) && stats.completed_buffers==1 && !stats.queued_bytes);
    } else if (!strcmp(which,"pause")) {
        open_default(); buffer(0x10300,0x10400,16,16);assert(sdk_append(ptr(0x10000),ptr(0x10300)));
        assert(!sdk_start(ptr(0x10000))); assert(!botw_audio_set_active(ctx,0,error,sizeof error));
        for(unsigned i=0;i<10;i++)render(64);
        assert(!sdk_released(ptr(0x10000)) && sdk_state(ptr(0x10000))==0);
        assert(!botw_audio_set_active(ctx,1,error,sizeof error));render(16);assert(sdk_released(ptr(0x10000))==ptr(0x10300));
        sdk_stop(ptr(0x10000)); assert(sdk_state(ptr(0x10000))==1);
    } else if (!strcmp(which,"event")) {
        assert(!sdk_open_default_event(ptr(0x10000),ptr(0x10200),ptr(0x10100)));
        assert(creates==1 && attaches==1 && !closes);
        buffer(0x10300,0x10400,16,16); assert(sdk_append(ptr(0x10000),ptr(0x10300))); assert(!sdk_start(ptr(0x10000)));
        render(8);sleep_ms(10);assert(!signals);render(8);
        for(unsigned i=0;i<100 && !__atomic_load_n(&signals,__ATOMIC_RELAXED);i++)sleep_ms(1);
        assert(signals==1);sdk_close(ptr(0x10000));assert(closes==1);
    } else if (!strcmp(which,"bounds")) {
        open_default();
        const uint64_t bad[][3]={{0,16,16},{0x10400,8,16},{UINT64_MAX-7,16,16},{0x10400,16,0},{0x10400,16,6}};
        for(unsigned i=0;i<sizeof bad/sizeof *bad;i++) { buffer(0x10300,bad[i][0],bad[i][1],bad[i][2]);unsigned f=faults;
            assert(!sdk_append(ptr(0x10000),ptr(0x10300)) && faults==f+1 && strstr(fault_message,"buffer")); }
        buffer(0x10300,0x10400,16,16);read_error=1;assert(!sdk_append(ptr(0x10000),ptr(0x10300)));read_error=0;
        assert(!ctx->outputs[0]->nodes && !ctx->queued_bytes);
    } else if (!strcmp(which,"queue-bound")) {
        open_default();buffer(0x10300,0x10400,4,4);assert(sdk_append(ptr(0x10000),ptr(0x10300)));
        assert(!sdk_append(ptr(0x10000),ptr(0x10300)) && strstr(fault_message,"already queued"));
        for(unsigned i=1;i<QMAX;i++) { uint64_t token=0x10800+i*32;buffer(token,0x10400,4,4);assert(sdk_append(ptr(0x10000),ptr(token))); }
        buffer(0x10700,0x10400,4,4);unsigned f=faults;assert(!sdk_append(ptr(0x10000),ptr(0x10700)) && faults==f);
        assert(!sdk_start(ptr(0x10000)));render(QMAX*4);assert(!sdk_append(ptr(0x10000),ptr(0x10700)));
        assert(sdk_released(ptr(0x10000))==ptr(0x10300));assert(sdk_append(ptr(0x10000),ptr(0x10700)));
    } else if (!strcmp(which,"open-errors")) {
        transport_error[0]=99;assert(sdk_open_default(ptr(0x10000),ptr(0x10100)) && faults==1 && !ctx->outputs[0]);transport_error[0]=0;
        event_error=1;assert(sdk_open_default_event(ptr(0x10000),ptr(0x10200),ptr(0x10100)) && faults==2 && !ctx->outputs[0]);event_error=0;
        attach_error=1;assert(sdk_open_default_event(ptr(0x10000),ptr(0x10200),ptr(0x10100)) && faults==3 && closes==2 && !ctx->outputs[0]);
        attach_error=0;open_default();assert(sdk_open_default(ptr(0x10000),ptr(0x10100)) && strstr(fault_message,"already open"));
    } else if (!strcmp(which,"start-errors")) {
        open_default();transport_error[1]=42;assert(sdk_start(ptr(0x10000)) && strstr(fault_message,"start"));assert(sdk_state(ptr(0x10000))==1);
        transport_error[1]=0;assert(!sdk_start(ptr(0x10000)));assert(!botw_audio_set_active(ctx,0,error,sizeof error));
        transport_error[1]=43;assert(botw_audio_set_active(ctx,1,error,sizeof error)==-1 && strstr(error,"resume"));
        unsigned f=faults;assert(!sdk_rate(ptr(0x10000)) && faults==f+1 && strstr(fault_message,"resume"));transport_error[1]=0;
    } else if (!strcmp(which,"shutdown-retry")) {
        open_default();assert(!sdk_start(ptr(0x10000)));transport_error[3]=44;
        assert(botw_audio_shutdown(ctx,error,sizeof error)==-1 && strstr(error,"dispose") && g_audio==ctx && ctx->outputs[0]);
        transport_error[3]=0;assert(!botw_audio_shutdown(ctx,error,sizeof error));assert(!g_audio);ctx=NULL;
        assert(!botw_audio_shutdown(NULL,error,sizeof error));ctx=setup();open_default();
    } else if (!strcmp(which,"spsc")) {
        open_default();for(unsigned i=0;i<QMAX;i++){uint64_t token=0x10800+i*32;buffer(token,0x10400,4,4);assert(sdk_append(ptr(0x10000),ptr(token)));}
        assert(!sdk_start(ptr(0x10000)));atomic_store(&consumer_running,1);pthread_t thread;
        assert(!pthread_create(&thread,NULL,consume_many,ctx->outputs[0]));
        for(unsigned i=0;i<100000;i++){
            void *released;while(!(released=sdk_released(ptr(0x10000))))sched_yield();
            assert(released==ptr(0x10800+(i%QMAX)*32));
            while(!sdk_append(ptr(0x10000),released))sched_yield();
        }
        atomic_store(&consumer_running,0);assert(!pthread_join(thread,NULL));assert(!faults);
        botw_audio_stats stats;assert(!botw_audio_get_stats(ctx,&stats)&&stats.completed_buffers>=100000);
    } else if (!strcmp(which,"failed-reader-cleanup")) {
        attach_error=1;close_error=1;
        assert(sdk_open_default_event(ptr(0x10000),ptr(0x10200),ptr(0x10100)));
        assert(ctx->outputs[0]&&ctx->outputs[0]->retiring&&ctx->outputs[0]->unattached_reader&&ctx->outputs[0]->writer);
        assert(botw_audio_shutdown(ctx,error,sizeof error)==-1&&strstr(error,"kernel close"));
        close_error=0;assert(!botw_audio_shutdown(ctx,error,sizeof error));ctx=NULL;
    } else if (!strcmp(which,"validation")) {
        botw_audio_callbacks cb=callbacks();assert(!botw_audio_create(&cb,1,error,sizeof error)&&strstr(error,"already exists"));
        assert(sdk_open_default_event(ptr(0x10000),NULL,ptr(0x10100))&&strstr(fault_message,"SystemEvent"));
        param(3);assert(sdk_open_default(ptr(0x10000),ptr(0x10100))&&strstr(fault_message,"channel count"));
        param(2);uint32_t rate=44100;memcpy(memory+0x100,&rate,4);
        assert(sdk_open_default(ptr(0x10000),ptr(0x10100))&&strstr(fault_message,"sample rate"));
        param(2);write_error=1;assert(sdk_open_default(ptr(0x10000),ptr(0x10100)));write_error=0;
        open_default();sdk_close(ptr(0x10000));unsigned f=faults;sdk_close(ptr(0x10000));assert(faults==f+1);
        assert(botw_audio_set_active(ctx,2,error,sizeof error)==-1);assert(botw_audio_get_stats(NULL,NULL)==-1);
    } else if (!strcmp(which,"downmix")) {
        int16_t in[6]={0,0,10000,0,0,0},out[2];downmix51(in,out,1);assert(out[0]==5000 && out[1]==5000);
        for(unsigned i=0;i<6;i++)in[i]=32767;downmix51(in,out,1);assert(out[0]==32767 && out[1]==32767);
    } else if (!strcmp(which,"deferred-notify-error")) {
        assert(!sdk_open_default_event(ptr(0x10000),ptr(0x10200),ptr(0x10100)));signal_error=1;
        buffer(0x10300,0x10400,4,4);assert(sdk_append(ptr(0x10000),ptr(0x10300)));assert(!sdk_start(ptr(0x10000)));render(4);
        for(unsigned i=0;i<100 && !__atomic_load_n(&signals,__ATOMIC_RELAXED);i++)sleep_ms(1);sleep_ms(10);assert(!faults);
        assert(!sdk_rate(ptr(0x10000)) && faults==1 && strstr(fault_message,"kernel signal rejected"));
    } else {assert(!"unknown case");}
    assert(!botw_audio_shutdown(ctx,error,sizeof error));assert(!g_audio);return 0;
}
'''


REAL_DRIVER = r'''
#define _POSIX_C_SOURCE 200809L
#include "AUDIO_HEADER"
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static unsigned char guest[128*1024];
static int in_range(uint64_t a,size_t n) {return a>=(uintptr_t)guest&&n<=sizeof guest&&a-(uintptr_t)guest<=sizeof guest-n;}
static int read_guest(void *ctx,uint64_t a,void *out,size_t n,char *e,size_t cap) {
 (void)ctx;if(!in_range(a,n)){snprintf(e,cap,"unmapped fixture");return -1;}memcpy(out,(void *)(uintptr_t)a,n);return 0;
}
static int write_guest(void *ctx,uint64_t a,const void *in,size_t n,char *e,size_t cap) {
 (void)ctx;if(!in_range(a,n)){snprintf(e,cap,"unmapped fixture");return -1;}memcpy((void *)(uintptr_t)a,in,n);return 0;
}
static int create_event(void *ctx,int initial,uint32_t *w,uint32_t *r,char *e,size_t n) {
 (void)ctx;(void)initial;(void)w;(void)r;snprintf(e,n,"fixture uses eventless public ABI");return -1;
}
static int signal_event(void *ctx,uint32_t h,char *e,size_t n) {(void)ctx;(void)h;(void)e;(void)n;abort();}
static int attach_event(void *ctx,uint64_t a,uint32_t h,char *e,size_t n) {(void)a;return signal_event(ctx,h,e,n);}
static void fault(void *ctx,const char *message) {(void)ctx;fprintf(stderr,"AudioOut fault: %s\n",message);abort();}
static void log_message(void *ctx,const char *message) {(void)ctx;puts(message);}
static void sleep_ms(unsigned ms) {struct timespec t={ms/1000,(long)(ms%1000)*1000000};nanosleep(&t,NULL);}
static void *resolve(const char *name,uint64_t offset,uint64_t size) {
 char error[512];void *f=NULL;assert(botw_audio_resolve_symbol("sdk",5,name,offset,size,0x340000,&f,error,sizeof error)==1&&f);return f;
}
int main(void) {
 botw_audio_callbacks callbacks={.read_guest=read_guest,.write_guest=write_guest,.create_event=create_event,
 .signal_event=signal_event,.close_handle=signal_event,.attach_system_event=attach_event,.fault=fault,.log=log_message};
 char error[512];botw_audio *ctx=botw_audio_create(&callbacks,1,error,sizeof error);
#if defined(NON_APPLE_REJECTION)
 assert(!ctx&&strstr(error,"no silent backend"));puts("non_apple_backend=strict_rejection");return 0;
#else
 assert(ctx);
 void (*init)(void *)=resolve("_ZN2nn5audio27InitializeAudioOutParameterEPNS0_17AudioOutParameterE",0x1b1f24,0xc);
 uint32_t (*open)(void *,const void *)=resolve("_ZN2nn5audio19OpenDefaultAudioOutEPNS0_8AudioOutERKNS0_17AudioOutParameterE",0x1c6604,0x28);
 void (*set_buffer)(void *,void *,uint64_t,uint64_t)=resolve("_ZN2nn5audio21SetAudioOutBufferInfoEPNS0_14AudioOutBufferEPvmm",0x1b1f3c,0xc);
 unsigned (*append)(void *,void *)=resolve("_ZN2nn5audio20AppendAudioOutBufferEPNS0_8AudioOutEPNS0_14AudioOutBufferE",0x1c6c78,0xa8);
 uint32_t (*start)(void *)=resolve("_ZN2nn5audio13StartAudioOutEPNS0_8AudioOutE",0x1c6a7c,0x80);
 void *(*released)(void *)=resolve("_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE",0x1c6d20,0xa0);
 void (*stop)(void *)=resolve("_ZN2nn5audio12StopAudioOutEPNS0_8AudioOutE",0x1c6afc,0x78);
 void (*close)(void *)=resolve("_ZN2nn5audio13CloseAudioOutEPNS0_8AudioOutE",0x1c69a4,0xd8);
 init(guest+0x100);assert(!open(guest,guest+0x100));set_buffer(guest+0x200,guest+0x1000,9600,9600);
 assert(append(guest,guest+0x200));assert(!start(guest));void *returned=NULL;
 for(unsigned i=0;i<800&&!(returned=released(guest));i++)sleep_ms(5);assert(returned==guest+0x200);
 botw_audio_stats first;assert(!botw_audio_get_stats(ctx,&first)&&first.render_callbacks&&first.rendered_frames&&first.completed_buffers==1);
 assert(!botw_audio_set_active(ctx,0,error,sizeof error));assert(append(guest,guest+0x200));sleep_ms(80);
 assert(!released(guest));botw_audio_stats paused;assert(!botw_audio_get_stats(ctx,&paused)&&paused.completed_buffers==1);
 assert(!botw_audio_set_active(ctx,1,error,sizeof error));returned=NULL;
 for(unsigned i=0;i<800&&!(returned=released(guest));i++)sleep_ms(5);assert(returned==guest+0x200);
 stop(guest);close(guest);botw_audio_stats final;assert(!botw_audio_get_stats(ctx,&final)&&!final.live_outputs&&!final.queued_bytes);
 assert(final.completed_buffers==2&&final.render_callbacks>first.render_callbacks);
 assert(!botw_audio_shutdown(ctx,error,sizeof error));
 printf("actual_audio_unit_callbacks=%" PRIu64 " completed_buffers=%" PRIu64 " zero_pcm_fixture=true game_audio_proof=false\n",final.render_callbacks,final.completed_buffers);
 return 0;
#endif
}
'''


RENDER_BOUNDARY_MAIN = r'''
int main(void) {
 char error[512];botw_audio *ctx=setup();open_default();aout *a=ctx->outputs[0];
 memset(memory+0x400,0x31,64);buffer(0x10300,0x10400,64,64);assert(sdk_append(ptr(0x10000),ptr(0x10300)));
 atomic_store(&a->state,STATE_STARTED);unsigned char output[64];memset(output,0xa5,sizeof output);
 AudioBufferList list={.mNumberBuffers=1,.mBuffers={{.mNumberChannels=2,.mDataByteSize=64,.mData=output}}};
 AudioUnitRenderActionFlags flags=0;AudioTimeStamp timestamp={0};
 assert(!au_render(a,&flags,&timestamp,0,4,&list));
 botw_audio_stats stats;assert(!botw_audio_get_stats(ctx,&stats));
 assert(stats.rendered_frames==4&&!stats.completed_buffers&&!sdk_released(ptr(0x10000)));
 assert(list.mBuffers[0].mDataByteSize==16);for(unsigned i=0;i<16;i++)assert(output[i]==0x31);
 for(unsigned i=16;i<64;i++)assert(output[i]==0xa5);
 /* Insufficient output capacity and wrong channel metadata consume no PCM. */
 list.mBuffers[0].mDataByteSize=12;
 assert(au_render(a,&flags,&timestamp,0,4,&list)==kAudio_ParamError);
 assert(!botw_audio_get_stats(ctx,&stats)&&stats.rendered_frames==4&&!stats.completed_buffers);
 assert(!sdk_rate(ptr(0x10000))&&strstr(fault_message,"capacity"));
 list.mBuffers[0].mDataByteSize=16;list.mBuffers[0].mNumberChannels=1;
 assert(au_render(a,&flags,&timestamp,0,4,&list)==kAudio_ParamError);
 assert(!sdk_rate(ptr(0x10000))&&strstr(fault_message,"channel"));
 list.mBuffers[0].mDataByteSize=64;list.mBuffers[0].mNumberChannels=2;
 assert(au_render(a,&flags,&timestamp,0,UINT32_MAX,&list)==kAudio_ParamError);
 assert(!sdk_rate(ptr(0x10000))&&strstr(fault_message,"capacity"));
 list.mBuffers[0].mDataByteSize=48;
 assert(!au_render(a,&flags,&timestamp,0,12,&list));
 assert(sdk_released(ptr(0x10000))==ptr(0x10300));
 assert(!botw_audio_get_stats(ctx,&stats)&&stats.rendered_frames==16&&stats.completed_buffers==1);
 atomic_store(&a->state,STATE_STOPPED);assert(!botw_audio_shutdown(ctx,error,sizeof error));return 0;
}
'''


class AudioOutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-audio-')
        source = Path(cls.build.name) / 'audio_test.c'
        source.write_text(DRIVER.replace('AUDIO_SOURCE', str(ROOT / 'native/botw_audio/audio.c')))
        cls.executable = Path(cls.build.name) / 'audio_test'
        result = subprocess.run(['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                                 '-pthread', str(source), '-o', str(cls.executable)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check(self, name):
        result = subprocess.run([str(self.executable), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_complete_audited_symbol_profile(self): self.check('profile')
    def test_base_sdk_parameter_and_buffer_layout(self): self.check('parameter-layout')
    def test_owned_pcm_copy_real_consumption_and_release(self): self.check('copy-and-completion')
    def test_inactive_output_retains_pcm_without_fake_completions(self): self.check('pause')
    def test_kernel_event_signals_only_complete_buffers_and_writer_lifetime(self): self.check('event')
    def test_guest_spans_sizes_frame_alignment_and_failed_copy(self): self.check('bounds')
    def test_pending_plus_released_bounded_to_upstream_queue_capacity(self): self.check('queue-bound')
    def test_backend_and_event_failures_never_open_successfully(self): self.check('open-errors')
    def test_start_and_resume_failures_remain_observable(self): self.check('start-errors')
    def test_failed_dispose_retains_backing_for_shutdown_retry(self): self.check('shutdown-retry')
    def test_reused_upstream_downmix_center_and_saturation(self): self.check('downmix')
    def test_notifier_error_is_deferred_to_guest_thread(self): self.check('deferred-notify-error')
    def test_reused_spsc_pipeline_transfers_100000_buffers_concurrently(self): self.check('spsc')
    def test_failed_event_cleanup_retains_both_owned_handles_for_retry(self): self.check('failed-reader-cleanup')
    def test_invalid_parameters_contexts_and_double_close(self): self.check('validation')

class AudioOutOriginalProfileTests(unittest.TestCase):
    def test_profile_matches_optional_original_private_sdk_and_subsdk3_imports(self):
        sdk=ROOT/'outputs/botw-native-base/ExeFS/sdk';subsdk=ROOT/'outputs/botw-native-base/ExeFS/subsdk3'
        if not sdk.exists() or not subsdk.exists(): self.skipTest('private original NSO fixtures unavailable')
        try: import lz4.block
        except ImportError: self.skipTest('private NSO decompression requires lz4')
        spec=importlib.util.spec_from_file_location('botw_audio_nso',ROOT/'scripts/botw_native/nso.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        image=module.initialized_image(sdk.read_bytes(),'sdk')
        mod=struct.unpack_from('<I',image,4)[0];dynamic=mod+struct.unpack_from('<i',image,mod+4)[0];tags={}
        for index in range(65536):
            tag,value=struct.unpack_from('<QQ',image,dynamic+index*16)
            if not tag: break
            tags[tag]=value
        count=struct.unpack_from('<I',image,tags[4]+4)[0];symbols={}
        for index in range(count):
            offset,info,other,section,value,size=struct.unpack_from('<IBBHQQ',image,tags[6]+index*24)
            if section and info&15==2:
                name=image[tags[5]+offset:].split(b'\0',1)[0].decode()
                symbols[name]=(value,size)
        entries=re.findall(r'EXPORT\("([^"]+)",(0x[0-9a-f]+),(0x[0-9a-f]+),',
                           (ROOT/'native/botw_audio/audio.c').read_text())
        self.assertEqual(len(entries),18)
        for name,value,size in entries:self.assertEqual(symbols[name],(int(value,16),int(size,16)),name)
        imports=module.inspect_nso(subsdk.read_bytes(),'subsdk3')['imports']
        needed={name for name in imports if 'AudioOut' in name}
        self.assertEqual(len(needed),13);self.assertTrue(needed<={e[0] for e in entries})


class AudioOutProductionTests(unittest.TestCase):
    def build_and_run(self, *, reject=False):
        with tempfile.TemporaryDirectory(prefix='botw-audio-production-') as directory:
            source=Path(directory)/'driver.c';executable=Path(directory)/'driver'
            source.write_text(REAL_DRIVER.replace('AUDIO_HEADER',str(ROOT/'native/botw_audio/audio.h')))
            command=['clang','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-pthread']
            if reject: command+=['-U__APPLE__','-DNON_APPLE_REJECTION']
            elif platform.system()=='Darwin': command+=['-framework','AudioToolbox','-framework','CoreAudio','-framework','CoreFoundation']
            result=subprocess.run([*command,str(source),str(ROOT/'native/botw_audio/audio.c'),'-o',str(executable)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return result.stdout

    def test_non_apple_product_has_no_silent_success_fallback(self):
        self.assertIn('non_apple_backend=strict_rejection',self.build_and_run(reject=True))

    @unittest.skipUnless(platform.system()=='Darwin','actual AudioUnit callback boundary requires Apple headers')
    def test_production_callback_consumes_requested_frames_and_rejects_short_capacity(self):
        with tempfile.TemporaryDirectory(prefix='botw-audio-render-') as directory:
            source=Path(directory)/'driver.c';executable=Path(directory)/'driver'
            prefix=DRIVER.replace('#define BOTW_AUDIO_TEST_BACKEND 1\n','').split('int main(int argc,char **argv) {',1)[0]
            source.write_text((prefix+RENDER_BOUNDARY_MAIN).replace('AUDIO_SOURCE',str(ROOT/'native/botw_audio/audio.c')))
            result=subprocess.run(['clang','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function',
                '-pthread',str(source),'-framework','AudioToolbox','-framework','CoreAudio','-framework','CoreFoundation','-o',str(executable)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(platform.system()=='Darwin','actual AudioUnit output requires Apple host')
    def test_actual_coreaudio_callbacks_complete_zero_pcm_and_pause_retains_queue(self):
        result=self.build_and_run();self.assertIn('actual_audio_unit_callbacks=',result)
        self.assertIn('game_audio_proof=false',result)
        print(result.strip())

if __name__ == '__main__': unittest.main()
