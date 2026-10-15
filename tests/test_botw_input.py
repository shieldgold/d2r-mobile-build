"""Real host input to the old SDK's HID shared-memory Npad rings."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include "input.h"
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#ifndef MAP_ANONYMOUS
#define MAP_ANONYMOUS MAP_ANON
#endif
static botw_input *input;
static unsigned char *shared;
static uint64_t u64(size_t p) { uint64_t v; memcpy(&v, shared+p, 8); return v; }
static uint32_t u32(size_t p) { uint32_t v; memcpy(&v, shared+p, 4); return v; }
static size_t slot(unsigned id) { return BOTW_HID_NPAD_OFFSET + id * BOTW_HID_NPAD_STRIDE; }
static size_t latest(unsigned id, unsigned ring) {
    size_t p=slot(id)+0x28+ring*0x350; return p+0x20+u64(p+0x10)*0x30;
}
static void setup(void) {
    input=botw_input_create(); assert(input);
    size_t size=0; shared=botw_input_shared_memory(input,&size); assert(shared && size==0x40000);
    uint32_t ids[]={0,0x20}; assert(!botw_input_configure(input,3,ids,2));
    botw_input_set_active(input,1);
}
static void baseline(void) {
    setup(); botw_input_sample(input,19200000);
    assert(!u32(slot(0)) && !u32(slot(8)));
    assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,0,0,0,0,0,1));
    botw_input_sample(input,19200001);
    assert(u32(slot(8))==BOTW_NPAD_STYLE_HANDHELD && !u32(slot(0)));
    size_t e=latest(8,1); assert(u64(e)==2 && u64(e+8)==2 && !u64(e+16));
    assert(u32(e+40)&BOTW_NPAD_ATTRIBUTE_CONNECTED);
    assert(!u32(e+24) && !u32(e+28) && !u32(e+32) && !u32(e+36));
    botw_input_destroy(input);
}
static void buttons(void) {
    setup();
    assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,BOTW_BUTTON_A|BOTW_BUTTON_ZL,40000,-40000,33,-55,1));
    botw_input_sample(input,10); size_t e=latest(8,1);
    assert(u64(e+16)==(BOTW_BUTTON_A|BOTW_BUTTON_ZL|BOTW_BUTTON_STICK_L_RIGHT|BOTW_BUTTON_STICK_L_DOWN));
    assert((int32_t)u32(e+24)==32767 && (int32_t)u32(e+28)==-32768);
    assert((int32_t)u32(e+32)==33 && (int32_t)u32(e+36)==-55);
    assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,0,0,0,0,0,1));
    botw_input_sample(input,11); e=latest(8,1); assert(!u64(e+16));
    assert(botw_input_set_source(input,BOTW_INPUT_SOURCE_COUNT,1,0,0,0,0,1)==-1);
    assert(botw_input_set_source(input,BOTW_INPUT_TOUCH,UINT64_C(1)<<63,0,0,0,0,1)==-1);
    botw_input_destroy(input);
}
static void merge(void) {
    setup();
    assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,BOTW_BUTTON_A,100,200,0,0,1));
    assert(!botw_input_set_source(input,BOTW_INPUT_GAMECONTROLLER,BOTW_BUTTON_B,-200,0,50,60,1));
    botw_input_sample(input,1); size_t e=latest(0,0);
    assert(u32(slot(0))==BOTW_NPAD_STYLE_FULLKEY && !u32(slot(8)));
    assert(u64(e+16)==(BOTW_BUTTON_A|BOTW_BUTTON_B));
    assert((int32_t)u32(e+24)==-200 && (int32_t)u32(e+28)==200);
    assert(!botw_input_set_source(input,BOTW_INPUT_GAMECONTROLLER,UINT64_C(0xffff),999,999,999,999,0));
    botw_input_sample(input,2); e=latest(8,1);
    assert(!u32(slot(0)) && u32(slot(8))==2 && u64(e+16)==BOTW_BUTTON_A);
    botw_input_set_active(input,0); botw_input_sample(input,3); e=latest(8,1);
    assert(!u64(e+16) && !u32(e+40) && !u32(slot(8)));
    botw_input_set_active(input,1); botw_input_sample(input,4); e=latest(8,1);
    assert(!u64(e+16)); /* losing focus releases old held state */
    botw_input_destroy(input);
}
static void ring(void) {
    setup(); assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,0,0,0,0,0,1));
    for(unsigned n=1;n<=65;n++) { botw_input_sample(input,n*100); size_t p=slot(8)+0x378,e=latest(8,1);
        assert(u64(p)==n*100 && u64(p+8)==17 && u64(p+24)==(n<16?n:16));
        assert(u64(e)==n && u64(e+8)==n);
        if(n>1) { size_t prev=p+0x20+((u64(p+16)+16)%17)*0x30; assert(u64(prev+8)==n-1); }
    }
    botw_input_destroy(input);
}
static void timing(void) {
    uint64_t first=botw_input_monotonic_ticks(); assert(first);
    for(unsigned i=0;i<1000;i++)assert(botw_input_monotonic_ticks()>=first);
}
static void config(void) {
    setup(); uint32_t bad[]={0,8}; assert(botw_input_configure(input,3,bad,2)==-1);
    uint32_t duplicate[]={0,0}; assert(botw_input_configure(input,3,duplicate,2)==-1);
    assert(botw_input_configure(input,0x80000000,bad,1)==-1);
    uint32_t ids[]={2}; assert(!botw_input_configure(input,1,ids,1));
    assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,BOTW_BUTTON_X,0,0,0,0,1));
    botw_input_sample(input,1); assert(u32(slot(2))==1 && !u32(slot(8)));
    size_t e=latest(2,0); assert(u64(e+16)==BOTW_BUTTON_X);
    assert(botw_input_style(input,2)==1 && !botw_input_style(input,0));
    assert(!botw_input_disconnect(input,2)); botw_input_sample(input,2); assert(!u32(slot(2)));
    assert(botw_input_disconnect(input,8)==-1);
    unsigned char *mapped=calloc(1,0x40000); assert(mapped);
    assert(botw_input_bind_shared_memory(input,mapped,0x3ffff)==-1);
    assert(!botw_input_bind_shared_memory(input,mapped,0x40000)); shared=mapped;
    assert(!botw_input_configure(input,1,ids,1)); botw_input_sample(input,3);
    assert(u32(slot(2))==1 && u64(latest(2,0)+16)==BOTW_BUTTON_X);
    botw_input_destroy(input); mapped[0]=1; free(mapped); /* externally owned mapping */
}
static void unbind(void) {
    setup(); unsigned char *owned=shared;
    unsigned char *mapped=mmap(NULL,0x40000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(mapped!=MAP_FAILED);
    assert(!botw_input_bind_shared_memory(input,mapped,0x40000)); shared=mapped;
    assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,BOTW_BUTTON_Y,123,-456,0,0,1));
    botw_input_sample(input,7); assert(u64(latest(8,1)+16)==BOTW_BUTTON_Y);
    unsigned char *snapshot=malloc(0x40000); assert(snapshot); memcpy(snapshot,mapped,0x40000);
    botw_input_unbind_shared_memory(input);
    size_t size=0; shared=botw_input_shared_memory(input,&size);
    assert(shared==owned && size==0x40000);
    assert(!u64(slot(8)+0x378+24)); /* ring and sampling reset */
    botw_input_sample(input,8); assert(u64(latest(8,1)+8)==1 && u64(latest(8,1)+16)==BOTW_BUTTON_Y);
    for(unsigned n=0;n<64;n++)botw_input_sample(input,9+n);
    assert(!memcmp(snapshot,mapped,0x40000)); free(snapshot);
    assert(!munmap(mapped,0x40000)); /* fail deterministically if sampling retained backing */
    for(unsigned n=0;n<64;n++)botw_input_sample(input,100+n);
    botw_input_unbind_shared_memory(input);botw_input_unbind_shared_memory(input);
    botw_input_sample(input,200);assert(botw_input_shared_memory(input,NULL)==owned);
    botw_input_unbind_shared_memory(NULL);botw_input_destroy(input);
}
static _Atomic int stop_sampling, samples_started;
static void *continuous_sampler(void *unused) {
    (void)unused; uint64_t tick=0;
    while(!atomic_load(&stop_sampling)) {
        botw_input_sample(input,++tick); atomic_store(&samples_started,1);
    }
    return NULL;
}
static void concurrent_unbind(void) {
    setup(); unsigned char *owned=shared;
    unsigned char *mapped=mmap(NULL,0x40000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(mapped!=MAP_FAILED);
    assert(!botw_input_bind_shared_memory(input,mapped,0x40000));
    pthread_t thread; assert(!pthread_create(&thread,NULL,continuous_sampler,NULL));
    while(!atomic_load(&samples_started)) { }
    botw_input_unbind_shared_memory(input);
    assert(botw_input_shared_memory(input,NULL)==owned);
    unsigned char *snapshot=malloc(0x40000); assert(snapshot);memcpy(snapshot,mapped,0x40000);
    for(unsigned n=0;n<100;n++)botw_input_sample(input,n);
    assert(!memcmp(snapshot,mapped,0x40000));free(snapshot);
    assert(!munmap(mapped,0x40000));
    for(unsigned n=0;n<100;n++)botw_input_sample(input,n);
    atomic_store(&stop_sampling,1);assert(!pthread_join(thread,NULL));
    botw_input_destroy(input);
}
static _Atomic int finished;
static void *producer(void *unused) {
    (void)unused;
    for(int i=0;i<20000;i++) assert(!botw_input_set_source(input,BOTW_INPUT_TOUCH,i&1?BOTW_BUTTON_A:BOTW_BUTTON_B,i&1?111:-222,i&1?333:-444,0,0,1));
    atomic_store(&finished,1); return NULL;
}
static void concurrent(void) {
    setup(); pthread_t thread; assert(!pthread_create(&thread,NULL,producer,NULL)); uint64_t tick=0;
    while(!atomic_load(&finished)) {
        botw_input_sample(input,++tick); size_t e=latest(8,1); uint64_t buttons=u64(e+16);
        if(buttons==BOTW_BUTTON_A) assert((int32_t)u32(e+24)==111 && (int32_t)u32(e+28)==333);
        else if(buttons==BOTW_BUTTON_B) assert((int32_t)u32(e+24)==-222 && (int32_t)u32(e+28)==-444);
        else assert(!buttons);
    }
    assert(!pthread_join(thread,NULL)); botw_input_destroy(input);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"baseline"))baseline(); else if(!strcmp(argv[1],"buttons"))buttons();
    else if(!strcmp(argv[1],"merge"))merge(); else if(!strcmp(argv[1],"ring"))ring();
    else if(!strcmp(argv[1],"unbind"))unbind(); else if(!strcmp(argv[1],"concurrent_unbind"))concurrent_unbind();
    else if(!strcmp(argv[1],"timing"))timing(); else if(!strcmp(argv[1],"config"))config(); else if(!strcmp(argv[1],"concurrent"))concurrent(); else abort();
    puts("ok");
}
'''


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-input-')
        cls.exe = Path(cls.build.name) / 'input-fixture'
        source = ROOT / 'native/botw_input/input.c'
        if not source.exists():
            raise AssertionError('Real host input / old SDK HID bridge is missing')
        fixture = Path(cls.build.name) / 'fixture.c'
        fixture.write_text(FIXTURE)
        subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                        '-Werror', '-pthread', '-I', str(source.parent), str(fixture),
                        str(source), '-o', str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True,
                                timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'ok')

    def test_no_input_is_released(self): self.case('baseline')
    def test_real_buttons_axes_and_release(self): self.case('buttons')
    def test_touch_and_controller_merge_then_disconnect(self): self.case('merge')
    def test_old_sdk_ring_repeated_wrap_and_sequence(self): self.case('ring')
    def test_actual_monotonic_tick_source(self): self.case('timing')
    def test_supported_profiles_and_bound_shared_memory(self): self.case('config')
    def test_unbind_then_release_backing_and_continue_sampling(self): self.case('unbind')
    def test_unbind_serializes_with_inflight_sampling(self): self.case('concurrent_unbind')
    def test_source_update_and_sampling_are_coherent(self): self.case('concurrent')


if __name__ == '__main__':
    unittest.main()
