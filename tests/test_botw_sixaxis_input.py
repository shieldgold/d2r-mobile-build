"""Old SDK SixAxis rings: real disconnected state, activation and kernel aliases."""
import ctypes as C
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHARED = 0x40000
RINGS = (0x1758, 0x1e60, 0x2568, 0x2c70, 0x3378, 0x3a80)
IDS = (*range(8), 0x20, 0x10)
COMBINATIONS = ((3, 2), (4, 2), (5, 0), (5, 1), (6, 0), (7, 1))

HELPERS = r'''
#define _DARWIN_C_SOURCE 1
#define _DEFAULT_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "input.h"
#include "kernel.h"
#include <assert.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
void *sixaxis_kernel(uint64_t *alias,void **backing,uint32_t *handle,uint32_t *copy) {
    botw_kernel *kernel=botw_kernel_create(NULL,NULL);assert(kernel);
    char error[512];size_t unit=0x200000;
    assert(!botw_kernel_configure_memory(kernel,unit,unit,unit,unit,0,error,sizeof error));
    uint64_t regs[32]={0,2,BOTW_KERNEL_CURRENT_PROCESS};
    assert(botw_kernel_dispatch(kernel,0x29,regs,error,sizeof error)==BOTW_KERNEL_HANDLED&&!regs[0]);
    *alias=regs[1];
    assert(!botw_kernel_create_shared_memory(kernel,0x40000,1,handle,backing,error,sizeof error));
    assert(!botw_kernel_duplicate_handle(kernel,*handle,copy,error,sizeof error));
    regs[0]=*copy;regs[1]=*alias;regs[2]=0x40000;regs[3]=1;
    assert(botw_kernel_dispatch(kernel,0x13,regs,error,sizeof error)==BOTW_KERNEL_HANDLED&&!regs[0]);
    assert(botw_kernel_memory(kernel,*alias,0x40000,0));
    assert(!botw_kernel_memory(kernel,*alias,0x40000,1));return kernel;
}
static void denied(int signal) { (void)signal;_exit(44); }
int sixaxis_access(uint64_t address,int write) {
    pid_t child=fork();assert(child>=0);
    if (!child) {
        signal(SIGBUS,denied);signal(SIGSEGV,denied);
        if (write) *(volatile unsigned char *)(uintptr_t)address=0xff;
        else { volatile unsigned char value=*(volatile unsigned char *)(uintptr_t)address;(void)value; }
        _exit(0);
    }
    int status;assert(waitpid(child,&status,0)==child);assert(WIFEXITED(status));
    return WEXITSTATUS(status)==0;
}
typedef struct { botw_input *input;_Atomic int stop,started;pthread_t thread; } Sampler;
static void *sample(void *p) {
    Sampler *s=p;uint64_t ticks=100;
    while(!atomic_load(&s->stop)) { botw_input_sample(s->input,++ticks);atomic_store(&s->started,1); }
    return NULL;
}
void *sixaxis_sampler(botw_input *input) {
    Sampler *s=calloc(1,sizeof *s);assert(s);s->input=input;
    assert(!pthread_create(&s->thread,NULL,sample,s));
    while(!atomic_load(&s->started)) { } return s;
}
void sixaxis_sampler_stop(void *p) {
    Sampler *s=p;atomic_store(&s->stop,1);assert(!pthread_join(s->thread,NULL));free(s);
}
'''


def handle(style=3, npad=0, sensor=2):
    return style | npad << 8 | sensor << 16


class SixAxisInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-sixaxis-')
        cls.addClassCleanup(cls.build.cleanup)
        folder = Path(cls.build.name); helper = folder / 'helpers.c'; helper.write_text(HELPERS)
        binary = folder / 'sixaxis.so'
        subprocess.run(['cc', '-std=c11', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-pthread', '-I', str(ROOT / 'native/botw_input'),
                        '-I', str(ROOT / 'native/botw_host'), str(helper),
                        str(ROOT / 'native/botw_input/input.c'), str(ROOT / 'native/botw_host/kernel.c'),
                        '-o', str(binary)], check=True)
        cls.lib = C.CDLL(str(binary))
        cls.lib.botw_input_create.restype = C.c_void_p
        cls.lib.botw_input_destroy.argtypes = [C.c_void_p]
        cls.lib.botw_input_shared_memory.argtypes = [C.c_void_p, C.c_void_p]
        cls.lib.botw_input_shared_memory.restype = C.c_void_p
        cls.lib.botw_input_bind_shared_memory.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
        cls.lib.botw_input_unbind_shared_memory.argtypes = [C.c_void_p]
        cls.lib.botw_input_sample.argtypes = [C.c_void_p, C.c_uint64]
        cls.lib.botw_input_activate_debug_pad.argtypes = [C.c_void_p, C.c_uint64]
        cls.lib.botw_input_set_source.argtypes = [C.c_void_p, C.c_int, C.c_uint64,
                                               C.c_int32, C.c_int32, C.c_int32, C.c_int32, C.c_int]
        cls.lib.botw_input_set_active.argtypes = [C.c_void_p, C.c_int]
        cls.lib.sixaxis_kernel.argtypes = [C.c_void_p] * 4
        cls.lib.sixaxis_kernel.restype = C.c_void_p
        cls.lib.sixaxis_access.argtypes = [C.c_uint64, C.c_int]
        cls.lib.sixaxis_sampler.argtypes = [C.c_void_p]; cls.lib.sixaxis_sampler.restype = C.c_void_p
        cls.lib.sixaxis_sampler_stop.argtypes = [C.c_void_p]
        cls.lib.botw_kernel_close_handle.argtypes = [C.c_void_p, C.c_uint32, C.c_void_p, C.c_size_t]
        cls.lib.botw_kernel_dispatch.argtypes = [C.c_void_p, C.c_uint, C.c_void_p, C.c_void_p, C.c_size_t]
        cls.lib.botw_kernel_destroy.argtypes = [C.c_void_p]

    def setUp(self):
        self.input = self.lib.botw_input_create(); self.assertTrue(self.input)
        self.addCleanup(self.lib.botw_input_destroy, self.input)
        self.memory = self.lib.botw_input_shared_memory(self.input, None)

    def api(self):
        self.assertTrue(hasattr(self.lib, 'botw_input_start_six_axis'),
                        'SixAxis Start must persist a real ring activation, not return an empty success')
        self.assertTrue(hasattr(self.lib, 'botw_input_stop_six_axis'))
        start = self.lib.botw_input_start_six_axis; stop = self.lib.botw_input_stop_six_axis
        start.argtypes = [C.c_void_p, C.c_uint32, C.c_uint64]
        stop.argtypes = [C.c_void_p, C.c_uint32]
        return start, stop

    def u64(self, offset, memory=None):
        return C.c_uint64.from_address((memory or self.memory) + offset).value

    def ring(self, index=0, ring=0):
        return 0x9a00 + index * 0x5000 + RINGS[ring]

    def disconnected(self, ring, sequence, memory=None):
        base = memory or self.memory
        self.assertEqual(self.u64(ring + 8, base), 17)
        tail = self.u64(ring + 16, base); self.assertLess(tail, 17)
        self.assertLessEqual(self.u64(ring + 24, base), 16)
        entry = ring + 32 + tail * 0x68
        self.assertEqual(self.u64(entry, base), sequence)
        state = bytearray(C.string_at(base + entry + 8, 0x60))
        self.assertEqual(int.from_bytes(state[8:16], 'little'), sequence)
        state[8:16] = bytes(8)
        self.assertEqual(state, bytes(0x60), 'Disconnected has no measured pose/acceleration or Connected flag')

    def test_initial_all_six_rings_have_valid_empty_headers_without_implicit_motion(self):
        self.api()
        for index in range(10):
            for ring in range(6):
                p = self.ring(index, ring)
                self.assertEqual([self.u64(p+i) for i in (0, 8, 16, 24)], [0, 17, 16, 0])
        self.lib.botw_input_sample(self.input, 10)
        self.assertTrue(all(not self.u64(self.ring(i, r)+24) for i in range(10) for r in range(6)))

    def test_all_standard_disconnected_handles_start_their_own_ring(self):
        start, _ = self.api()
        for index, npad in enumerate(IDS):
            for ring, (style, sensor) in enumerate(COMBINATIONS):
                self.assertEqual(start(self.input, handle(style, npad, sensor), 123), 0)
                self.disconnected(self.ring(index, ring), 1)
        for i in range(10):
            for r in range(6): self.assertEqual(self.u64(self.ring(i, r)+24), 1)
        self.lib.botw_input_sample(self.input, 124)
        for i in range(10):
            for r in range(6): self.disconnected(self.ring(i, r), 2)

    def test_six_ring_bounds_preserve_each_npad_tail_and_other_shared_regions(self):
        start, _ = self.api()
        for i in range(10):
            C.memset(self.memory+0x9a00+i*0x5000+0x4188, 0xa5, 0xe78)
        C.memset(self.memory+0x3ba00, 0x5a, SHARED-0x3ba00)
        for npad in IDS:
            for style, sensor in COMBINATIONS:
                self.assertEqual(start(self.input, handle(style, npad, sensor), 1), 0)
        for ticks in range(2, 8): self.lib.botw_input_sample(self.input, ticks)
        for i in range(10):
            self.assertEqual(C.string_at(self.memory+0x9a00+i*0x5000+0x4188, 0xe78), b'\xa5'*0xe78)
        self.assertEqual(C.string_at(self.memory+0x3ba00, SHARED-0x3ba00), b'\x5a'*(SHARED-0x3ba00))

    def test_invalid_handle_or_clock_is_transactional(self):
        start, stop = self.api(); snapshot = C.string_at(self.memory, SHARED)
        bad = [0, 1, 2, 8, handle(3, 8, 2), handle(3, 9, 2), handle(3, 255, 2),
               handle(3, 0, 0), handle(4, 0, 1), handle(5, 0, 2), handle(6, 0, 1),
               handle(7, 0, 0), handle() | 0x1000000, 0xffffffff]
        for h in bad:
            with self.subTest(handle=hex(h)):
                self.assertEqual(start(self.input, h, 1), -1)
                self.assertEqual(stop(self.input, h), -1)
                self.assertEqual(C.string_at(self.memory, SHARED), snapshot)
        self.assertEqual(start(None, handle(), 1), -1); self.assertEqual(stop(None, handle()), -1)
        self.assertEqual(start(self.input, handle(), 0), -1)
        self.assertEqual(C.string_at(self.memory, SHARED), snapshot)

    def test_activation_is_idempotent_and_does_not_sample_npad_debugpad_or_neighbors(self):
        start, _ = self.api(); self.lib.botw_input_sample(self.input, 1)
        self.assertEqual(self.lib.botw_input_activate_debug_pad(self.input, 1), 0)
        C.memset(self.memory + self.ring() + 0x708, 0xa5, 16)  # Next ring header is a canary here.
        before = bytearray(C.string_at(self.memory, SHARED))
        self.assertEqual(start(self.input, handle(), 100), 0); self.disconnected(self.ring(), 1)
        after = bytearray(C.string_at(self.memory, SHARED));p = self.ring()
        before[p:p+0x708] = after[p:p+0x708];self.assertEqual(before, after)
        self.assertEqual(start(self.input, handle(), 999), 0)
        self.assertEqual(C.string_at(self.memory, SHARED), bytes(after))

    def test_sampling_wraps_and_button_connection_never_becomes_motion_connection(self):
        start, _ = self.api()
        self.assertEqual(self.lib.botw_input_set_source(self.input, 1, 1, 123, 456, 0, 0, 1), 0)
        self.assertEqual(start(self.input, handle(), 1), 0)
        for seq in range(2, 68):
            self.lib.botw_input_sample(self.input, seq * 100)
            self.disconnected(self.ring(), seq)
            self.assertEqual(self.u64(self.ring()+24), min(seq, 16))
            entry = self.ring()+32+((self.u64(self.ring()+16)+16)%17)*0x68
            self.assertEqual(self.u64(entry+16), seq-1)
        npad_entry = 0x9a00+0x48+self.u64(0x9a00+0x38)*0x30
        self.assertEqual(C.c_uint32.from_address(self.memory+npad_entry+40).value & 1, 1)
        self.lib.botw_input_set_active(self.input, 0);self.lib.botw_input_sample(self.input, 9999)
        self.disconnected(self.ring(), 68)

    def test_stop_freezes_ring_and_restart_continues_sequence(self):
        start, stop = self.api();p = self.ring()
        self.assertEqual(stop(self.input, handle()), 0)
        self.assertEqual(start(self.input, handle(), 1), 0)
        self.lib.botw_input_sample(self.input, 2);self.disconnected(p, 2)
        self.assertEqual(stop(self.input, handle()), 0); snapshot = C.string_at(self.memory+p, 0x708)
        self.assertEqual(stop(self.input, handle()), 0)
        for t in range(3, 20): self.lib.botw_input_sample(self.input, t)
        self.assertEqual(C.string_at(self.memory+p, 0x708), snapshot)
        self.assertEqual(start(self.input, handle(), 20), 0);self.disconnected(p, 3)

    def test_bind_and_unbind_reset_activation_and_sequence_without_retaining_old_backing(self):
        start, _ = self.api();owned = self.memory
        self.assertEqual(start(self.input, handle(), 1), 0)
        external = C.create_string_buffer(SHARED)
        self.assertEqual(self.lib.botw_input_bind_shared_memory(self.input, external, SHARED-1), -1)
        self.lib.botw_input_sample(self.input, 2);self.disconnected(self.ring(), 2)
        self.assertEqual(self.lib.botw_input_bind_shared_memory(self.input, external, SHARED), 0)
        self.memory = C.addressof(external)
        self.lib.botw_input_sample(self.input, 3);self.assertEqual(self.u64(self.ring()+24), 0)
        self.assertEqual(start(self.input, handle(), 4), 0);self.disconnected(self.ring(), 1)
        self.lib.botw_input_unbind_shared_memory(self.input);snapshot = external.raw
        self.memory = owned;self.lib.botw_input_sample(self.input, 5)
        self.assertEqual(self.u64(self.ring()+24), 0)
        self.assertEqual(start(self.input, handle(), 6), 0);self.disconnected(self.ring(), 1)
        self.assertEqual(external.raw, snapshot)

    def test_real_kernel_readonly_alias_lifetime_and_concurrent_unbind(self):
        start, _ = self.api();alias = C.c_uint64();backing = C.c_void_p();source = C.c_uint32();copy = C.c_uint32()
        kernel = self.lib.sixaxis_kernel(C.byref(alias), C.byref(backing), C.byref(source), C.byref(copy))
        self.assertTrue(kernel);error = C.create_string_buffer(512)
        self.assertEqual(self.lib.botw_input_bind_shared_memory(self.input, backing, SHARED), 0)
        self.assertEqual(start(self.input, handle(), 1), 0)
        self.disconnected(self.ring(), 1, alias.value)
        self.assertEqual(self.lib.sixaxis_access(alias.value+self.ring(), 1), 0)
        self.assertEqual(self.lib.botw_kernel_close_handle(kernel, source, error, 512), 0)
        sampler = self.lib.sixaxis_sampler(self.input)
        try:
            self.lib.botw_input_unbind_shared_memory(self.input)
            frozen = C.string_at(alias.value, SHARED)
            for t in range(100): self.lib.botw_input_sample(self.input, 100+t)
            self.assertEqual(C.string_at(alias.value, SHARED), frozen)
            regs = (C.c_uint64*32)(copy.value, alias.value, SHARED)
            self.assertEqual(self.lib.botw_kernel_dispatch(kernel, 0x14, regs, error, 512), 1)
            self.assertEqual(regs[0], 0)
            self.assertEqual(self.lib.sixaxis_access(alias.value, 0), 0)
            self.assertEqual(self.lib.botw_kernel_close_handle(kernel, copy, error, 512), 0)
            for t in range(100): self.lib.botw_input_sample(self.input, 200+t)
        finally:
            self.lib.sixaxis_sampler_stop(sampler)
            self.lib.botw_kernel_destroy(kernel)


if __name__ == '__main__':
    unittest.main()
