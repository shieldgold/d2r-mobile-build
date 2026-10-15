"""Versioned stack alignment transform, inverse chains and actual A64 mappings."""
import copy
import contextlib
import ctypes
import hashlib
import io
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from botw_native import thread_stack as ts
from botw_native import heap_policy as hp


def fixture():
    original = bytearray(ts.SITE + 32)
    original[ts.CONTEXT_OFFSET:ts.CONTEXT_OFFSET + len(ts.CONTEXT)] = ts.CONTEXT
    for site in hp.SITES:
        original[site['offset'] - 8:site['offset'] + 12] = bytes.fromhex(site['context'])
    image = bytes(original).ljust(0xa28000, b'\0')
    layout = {'KEY_PAGE_OFF': 0xa20000, 'PATCH_OFF': 0xa24000,
              'KEY_KRES0_IDX': 4, 'KEY_KRES1_IDX': 5}
    return bytes(original), image, layout


class ThreadStackTests(unittest.TestCase):
    def test_single_instruction_transform_is_transactional_and_exactly_reversible(self):
        original, image, _ = fixture()
        patched, manifest = ts.apply(original, image, generic_site_offsets=[])
        self.assertEqual(len(patched), len(image))
        self.assertEqual(patched[:ts.SITE], image[:ts.SITE])
        self.assertEqual(patched[ts.SITE + 4:], image[ts.SITE + 4:])
        self.assertEqual(patched[ts.SITE:ts.SITE + 4], ts.REPLACEMENT)
        self.assertEqual(ts.verify(patched, manifest, expected_input_sha256=hashlib.sha256(image).hexdigest()), image)
        self.assertEqual(image[ts.SITE:ts.SITE + 4], ts.ORIGINAL)

    def test_unknown_context_truncation_generic_overlap_and_mutable_inputs_are_rejected(self):
        original, image, _ = fixture()
        bad = bytearray(original); bad[ts.SITE - 4] ^= 1
        for source, rewritten, sites in [(bytes(bad), image, []), (original, bytes(bad), []),
                                          (original[:ts.SITE + 2], image, []), (original, image[:ts.SITE + 2], []),
                                          (original, image, [ts.SITE]), (original, image, [True]),
                                          (original, bytearray(image), [])]:
            with self.subTest(sites=sites), self.assertRaises(ValueError):
                ts.apply(source, rewritten, generic_site_offsets=sites)
        patched, _ = ts.apply(original, image, generic_site_offsets=[])
        with self.assertRaises(ValueError): ts.apply(original, patched, generic_site_offsets=[])

    def test_image_and_manifest_tampering_or_wrong_input_anchor_are_rejected(self):
        original, image, _ = fixture(); patched, manifest = ts.apply(original, image, generic_site_offsets=[])
        for offset in [ts.SITE - 4, ts.SITE, ts.SITE + 4, len(patched) - 1]:
            bad = bytearray(patched); bad[offset] ^= 1
            with self.assertRaises(ValueError): ts.verify(bytes(bad), manifest)
        for key, value in [('schema', True), ('profile', 'other'), ('input_image_bytes', True),
                           ('patches', []), ('source_image_sha256', 'XX' * 32), ('extra', 1)]:
            bad = copy.deepcopy(manifest); bad[key] = value
            with self.assertRaises(ValueError): ts.verify(patched, bad)
        with self.assertRaises(ValueError): ts.verify(patched[:-4], manifest)
        with self.assertRaises(ValueError): ts.verify(patched, manifest, expected_input_sha256='00' * 32)

    def test_heap_then_stack_chain_recovers_in_reverse_order_and_rejects_wrong_order(self):
        original, image, layout = fixture()
        heap_image, hm = hp.apply(original, image, layout, generic_site_offsets=[])
        final, sm = ts.apply(original, heap_image, generic_site_offsets=[])
        recovered_heap = ts.verify(final, sm, expected_input_sha256=hm['output_image_sha256'])
        self.assertEqual(hp.verify(recovered_heap, hm, expected_input_sha256=hashlib.sha256(image).hexdigest()), image)
        with self.assertRaises(ValueError): hp.verify(final, hm)
        stack_first, early = ts.apply(original, image, generic_site_offsets=[])
        wrong_final, _ = hp.apply(original, stack_first, layout, generic_site_offsets=[])
        with self.assertRaises(ValueError): ts.verify(wrong_final, early)

    def test_policy_requires_explicit_host39_and_complete_fixed_seven_module_identity(self):
        from botw_native.guest_framework import _thread_stack_policy
        originals = {name: {'name': name, **record} for name, record in json.loads(
            (ROOT / 'profiles/botw-switch-base-v0.json').read_text())['modules'].items()}
        rewrites = {name: {} for name in originals}
        self.assertIsNone(_thread_stack_policy({}, originals, rewrites, 39))
        rewrites['main'] = {'thread_stack_adaptation': {}, 'thread_stack_input_image_sha256': '00' * 32}
        for report, bits in [({}, 39), ({'guest_thread_stack_policy': ts.POLICY}, 36),
                             ({'guest_thread_stack_policy': dict(ts.POLICY, schema=True)}, 39)]:
            with self.assertRaises(ValueError): _thread_stack_policy(report, originals, rewrites, bits)
        self.assertEqual(_thread_stack_policy({'guest_thread_stack_policy': ts.POLICY}, originals, rewrites, 39), ts.POLICY)
        bad = copy.deepcopy(originals); bad['sdk']['sha256'] = '00' * 32
        with self.assertRaises(ValueError): _thread_stack_policy({'guest_thread_stack_policy': ts.POLICY}, bad, rewrites, 39)
        rewrites['sdk']['thread_stack_adaptation'] = {}
        with self.assertRaises(ValueError): _thread_stack_policy({'guest_thread_stack_policy': ts.POLICY}, originals, rewrites, 39)

    def test_cli_requires_host39_and_rewrite_before_creating_output(self):
        from botw_native.__main__ import main
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'out'
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(['--exefs', folder, '--output', str(output), '--thread-stack-policy', 'host-page-alignment-v1'])
            self.assertEqual(caught.exception.code, 2); self.assertFalse(output.exists())

    def test_framework_verifies_heap_stack_digest_order_and_rejects_dropped_or_forged_policy(self):
        import test_botw_guest_framework as baseline
        from botw_native.guest_framework import build_framework, HOST39_POLICY
        from botw_native.hipc_address import apply as address_apply
        for heap_enabled in (False, True):
            with self.subTest(heap=heap_enabled), tempfile.TemporaryDirectory() as folder:
                folder = Path(folder); report_path, rewrite = baseline.fixture(folder)
                report = json.loads(report_path.read_text())
                fingerprints = json.loads((ROOT / 'profiles/botw-switch-base-v0.json').read_text())['modules']
                for record in report['modules']: record.update(fingerprints[record['name']])
                for record in report['rewrites']: record['host_address_adaptation'] = {}
                report['host_address_policy'] = HOST39_POLICY.copy()
                report['guest_thread_stack_policy'] = ts.POLICY.copy()
                original, _, _ = fixture(); original = original.ljust(0xa24100, b'\0')
                generic = bytearray(original.ljust(0xa30000, b'\0'))
                struct.pack_into('<I', generic, 0, 0x14000000 | (0xa2c000 // 4))
                struct.pack_into('<I', generic, 0xa2c000, 0xd65f03c0)
                layout = {'KEY_PAGE_OFF': 0xa28000, 'PATCH_OFF': 0xa2c000,
                          'KEY_PAGE_SIZE': 0x4000, 'TEXT_END': ts.SITE + 32,
                          'RO_OFF': 0xa20000, 'DATA_OFF': 0xa24000}
                for i, key in enumerate(('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI')):
                    layout[f'KEY_{key}_IDX'] = i
                (rewrite / 'main.fixups.tsv').write_text(''.join(
                    f'# {k}\t{v if k.endswith("_IDX") else hex(v)}\n' for k, v in layout.items()))
                (rewrite / 'main.gates.tsv').write_text('0xa2c004\tsvc\t38\t0x0\n')
                main_source = next(record for record in report['modules'] if record['name'] == 'main')
                main_source.update(memory_bytes=0xa28000, segments=[
                    {'name': 'text', 'address': 0, 'bytes': ts.SITE + 32},
                    {'name': 'rodata', 'address': 0xa20000, 'bytes': 0x100},
                    {'name': 'data', 'address': 0xa24000, 'bytes': 0x100}])
                image, am = address_apply('main', original, bytes(generic), {'generic_site_offsets': [0]})
                main = report['rewrites'][0]
                main.update(host_address_adaptation=am, generic_image_sha256=hashlib.sha256(generic).hexdigest())
                if heap_enabled:
                    image, hm = hp.apply(original, image, layout, generic_site_offsets=[0])
                    main.update(heap_adaptation=hm, host39_image_sha256=hm['input_image_sha256'])
                    report['guest_heap_policy'] = hp.POLICY.copy()
                final, sm = ts.apply(original, image, generic_site_offsets=[0])
                main.update(thread_stack_adaptation=sm, thread_stack_input_image_sha256=sm['input_image_sha256'],
                            image_sha256=hashlib.sha256(final).hexdigest())
                (rewrite / 'main.patched.bin').write_bytes(final)
                report_path.write_text(json.dumps(report))
                good = build_framework(report_path, rewrite, folder / 'good', module_names=['main'])
                self.assertEqual(good['guest_thread_stack_policy'], ts.POLICY)
                for mode in ('drop_policy', 'drop_all_metadata', 'wrong_input', 'wrong_order'):
                    bad = copy.deepcopy(report)
                    if mode == 'drop_policy': del bad['guest_thread_stack_policy']
                    elif mode == 'drop_all_metadata':
                        del bad['guest_thread_stack_policy']
                        del bad['rewrites'][0]['thread_stack_adaptation']
                        del bad['rewrites'][0]['thread_stack_input_image_sha256']
                    elif mode == 'wrong_input': bad['rewrites'][0]['thread_stack_input_image_sha256'] = '00' * 32
                    else: bad['rewrites'][0]['thread_stack_adaptation']['input_image_bytes'] -= 64
                    report_path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError):
                        build_framework(report_path, rewrite, folder / mode, module_names=['main'])
                    self.assertFalse((folder / mode).exists())

    def test_low_level_source_check_rejects_partial_fixed_module_set(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'main').write_bytes(b'NSO0')
            with self.assertRaises(ValueError): ts.verify_source_modules(Path(folder))

    def test_runtime_audit_checks_actual_context_bounded_layout_and_partial_images(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'policy.so'
            subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                            str(ROOT / 'native/botw_host/address_space.c'), '-o', str(binary)], check=True)
            lib = ctypes.CDLL(str(binary)); audit = lib.botw_address_space_audit_thread_stack_image
            audit.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            audit.restype = ctypes.c_int
            original, image, _ = fixture(); patched, _ = ts.apply(original, image, generic_site_offsets=[])
            def check(data, text=23280432):
                raw = ctypes.create_string_buffer(data); error = ctypes.create_string_buffer(512)
                return audit(raw, len(data), text, error, 512)
            self.assertEqual(check(b'\0' * 4096, 0x100), 0)
            self.assertEqual(check(image), 0)
            self.assertEqual(check(patched.ljust(23280432, b'\0')), 1)
            self.assertEqual(check(patched[:ts.SITE + 2]), -1)
            self.assertEqual(check(patched[:ts.SITE + 4]), -1)
            self.assertEqual(check(patched, 0x1000), -1)
            for offset in [ts.SITE - 4, ts.SITE + 4]:
                bad = bytearray(patched); bad[offset] ^= 1
                self.assertEqual(check(bytes(bad)), -1)

    @unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Actual A64 host execution')
    def test_actual_a64_alignment_uses_original_allocator_and_real_kernel_move_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            assembly = '.text\n.p2align 2\n'
            for name, instruction in [('original', ts.ORIGINAL), ('adapted', ts.REPLACEMENT)]:
                assembly += f'''.globl _stack_alignment_{name}
_stack_alignment_{name}:
 stp x22, x23, [sp, #-48]!
 stp x24, x29, [sp, #16]
 str x30, [sp, #32]
 mov w24, w0
 mov x22, x1
 sxtw x23, w24
 .long {struct.unpack('<I', instruction)[0]}
 mov x0, x23
 mov x1, x22
 bl _stack_allocate_capture
 ldr x30, [sp, #32]
 ldp x24, x29, [sp, #16]
 ldp x22, x23, [sp], #48
 ret
'''
            (folder / 'probe.S').write_text(assembly)
            (folder / 'probe.c').write_text(NATIVE_FIXTURE)
            binary = folder / 'probe'
            subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                            '-I', str(ROOT / 'native/botw_host'), str(folder / 'probe.c'), str(folder / 'probe.S'),
                            str(ROOT / 'native/botw_host/kernel.c'), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('map_unmap_bytes_round_trip=true', result.stdout)


NATIVE_FIXTURE = r'''
#include "kernel.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
#include <signal.h>
static botw_kernel *k;
static char error[1024];
static uint64_t recorded_size, recorded_heap, recorded_alignment;
uint64_t stack_allocate_capture(uint64_t bytes,uint64_t heap,uint64_t alignment) {
 recorded_size=bytes;recorded_heap=heap;recorded_alignment=alignment;
 return (heap+0x3000+alignment-1)&~(alignment-1);
}
extern uint64_t stack_alignment_original(uint32_t bytes,uint64_t heap);
extern uint64_t stack_alignment_adapted(uint32_t bytes,uint64_t heap);
static void svc(unsigned n,uint64_t*r) { assert(botw_kernel_dispatch(k,n,r,error,sizeof error)==BOTW_KERNEL_HANDLED);assert(!r[0]); }
static uint64_t info(unsigned id){ uint64_t r[32]={0,id,BOTW_KERNEL_CURRENT_PROCESS};svc(0x29,r);return r[1];}
static void denied(int s) { (void)s; _exit(44); }
static int readable(uint64_t p) {
 pid_t child=fork();assert(child>=0);if(!child){signal(SIGBUS,denied);signal(SIGSEGV,denied);volatile unsigned char v=*(volatile unsigned char*)(uintptr_t)p;(void)v;_exit(0);}
 int status;assert(waitpid(child,&status,0)==child);return WIFEXITED(status)&&WEXITSTATUS(status)==0;
}
int main(void){
 assert(sysconf(_SC_PAGESIZE)==0x4000);k=botw_kernel_create(NULL,NULL);assert(k);
 assert(!botw_kernel_configure_memory(k,0x200000,0x200000,0x200000,0x200000,0,error,sizeof error));
 uint64_t r[32]={0,0x200000};svc(1,r);uint64_t heap=r[1],stack=info(14);
 memset((void*)(uintptr_t)heap,0x3e,0x10000);
 uint64_t source=stack_alignment_original(0x4000,heap);
 assert(source==heap+0x3000 && recorded_size==0x4000 && recorded_heap==heap && recorded_alignment==0x1000);
 r[0]=stack;r[1]=source;r[2]=0x4000;
 assert(botw_kernel_dispatch(k,4,r,error,sizeof error)==BOTW_KERNEL_ERROR);
 assert(botw_kernel_memory(k,heap,0x10000,1) && !botw_kernel_memory(k,stack,0x4000,0));
 source=stack_alignment_adapted(0x4000,heap);
 assert(source==heap+0x4000 && recorded_size==0x4000 && recorded_heap==heap && recorded_alignment==0x4000);
 r[0]=stack;r[1]=source;r[2]=0x4000;svc(4,r);
 assert(!botw_kernel_memory(k,source,0x4000,0) && botw_kernel_memory(k,heap,0x4000,1) && botw_kernel_memory(k,heap+0x8000,0x4000,1));
 assert(!botw_kernel_memory(k,stack+0x4000,0x4000,0));
 assert(!readable(source) && readable(heap) && readable(heap+0x8000) && !readable(stack+0x4000));
 for(unsigned i=0;i<0x4000;i++)assert(*(unsigned char*)(uintptr_t)(stack+i)==0x3e);
 memset((void*)(uintptr_t)stack,0xa5,0x4000);
 r[0]=stack;r[1]=source;r[2]=0x4000;svc(5,r);
 for(unsigned i=0;i<0xc000;i++)assert(*(unsigned char*)(uintptr_t)(heap+i)==(i>=0x4000&&i<0x8000?0xa5:0x3e));
 assert(botw_kernel_memory(k,source,0x4000,1) && !botw_kernel_memory(k,stack,0x4000,0));
 assert(readable(source) && !readable(stack));
 botw_kernel_destroy(k);puts("actual_a64=true size_heap_unchanged=true map_unmap_bytes_round_trip=true neighboring_protection_preserved=true");
}
'''

if __name__ == '__main__':
    unittest.main()
