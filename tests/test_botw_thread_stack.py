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
    original = bytearray(0xa1db20)
    struct.pack_into('<I', original, 0x7268d8, 0x321203e0)  # Original Volante heap remains unchanged in v1/v2.
    original[ts.CONTEXT_OFFSET:ts.CONTEXT_OFFSET + len(ts.CONTEXT)] = ts.CONTEXT
    original[ts.CAPACITY_CONTEXT_OFFSET:ts.CAPACITY_CONTEXT_OFFSET + len(ts.CAPACITY_CONTEXT)] = ts.CAPACITY_CONTEXT
    for offset, data in ts.DATAFLOW_CONTEXTS:
        original[offset:offset + len(data)] = data
    for site in hp.SITES:
        original[site['offset'] - 8:site['offset'] + 12] = bytes.fromhex(site['context'])
    image = bytes(original).ljust(0xa28000, b'\0')
    layout = {'KEY_PAGE_OFF': 0xa20000, 'PATCH_OFF': 0xa24000,
              'KEY_KRES0_IDX': 4, 'KEY_KRES1_IDX': 5}
    return bytes(original), image, layout


class ThreadStackTests(unittest.TestCase):
    def test_native_capacity_policy_preserves_old_output_and_reverses_both_sites_and_thunk(self):
        original, image, _ = fixture()
        source = bytearray(original); source[ts.CAPACITY_CONTEXT_OFFSET:ts.CAPACITY_CONTEXT_OFFSET + len(ts.CAPACITY_CONTEXT)] = ts.CAPACITY_CONTEXT
        original = bytes(source)
        image = original + image[len(original):]
        legacy, _ = ts.apply(original, image, generic_site_offsets=[])
        final, manifest = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v2')
        self.assertEqual(final[ts.SITE:ts.SITE + 4], ts.REPLACEMENT)
        self.assertNotEqual(final[ts.CAPACITY_SITE:ts.CAPACITY_SITE + 4], ts.CAPACITY_ORIGINAL)
        self.assertGreater(len(final), len(image))
        self.assertEqual(ts.verify(final, manifest), image)
        self.assertEqual(legacy[ts.CAPACITY_SITE:ts.CAPACITY_SITE + 4], ts.CAPACITY_ORIGINAL)
        forged = ts._manifest(hashlib.sha256(original).hexdigest(), final, final, ts.POLICY)
        with self.assertRaises(ValueError): ts.verify(final, forged)
        for at in [ts.CAPACITY_SITE - 4, ts.CAPACITY_SITE, len(image), len(final) - 4]:
            bad = bytearray(final); bad[at] ^= 1
            with self.assertRaises(ValueError): ts.verify(bytes(bad), manifest)
        with self.assertRaises(ValueError): ts.apply(original, image, generic_site_offsets=[ts.CAPACITY_SITE], policy='host-pthread-stack-v2')

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
            for policy in ts.POLICIES:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    main(['--exefs', folder, '--output', str(output), '--thread-stack-policy', policy])
                self.assertEqual(caught.exception.code, 2); self.assertFalse(output.exists())

    def test_framework_verifies_heap_stack_digest_order_and_rejects_dropped_or_forged_policy(self):
        import test_botw_guest_framework as baseline
        from botw_native.guest_framework import build_framework, HOST39_POLICY
        from botw_native.hipc_address import apply as address_apply
        for heap_enabled, policy_name in [(heap, name) for heap in (False, True) for name in ts.POLICIES]:
            policy = ts.POLICIES[policy_name]
            with self.subTest(heap=heap_enabled, policy=policy_name), tempfile.TemporaryDirectory() as folder:
                folder = Path(folder); report_path, rewrite = baseline.fixture(folder)
                report = json.loads(report_path.read_text())
                fingerprints = json.loads((ROOT / 'profiles/botw-switch-base-v0.json').read_text())['modules']
                for record in report['modules']: record.update(fingerprints[record['name']])
                for record in report['rewrites']: record['host_address_adaptation'] = {}
                report['host_address_policy'] = HOST39_POLICY.copy()
                report['guest_thread_stack_policy'] = policy.copy()
                original, _, _ = fixture()
                if policy in (ts.VOLANTE_POLICY, ts.SZS_POLICY):
                    source = bytearray(original.ljust(0x1c04100, b'\0'))
                    source[ts.VOLANTE_CONTEXT_OFFSET:ts.VOLANTE_CONTEXT_OFFSET + len(ts.VOLANTE_CONTEXT)] = ts.VOLANTE_CONTEXT
                    for offset, context in ts.VOLANTE_DATAFLOW_CONTEXTS:
                        source[offset:offset + len(context)] = context
                    source[ts.SZS_SITE:ts.SZS_SITE + 4] = ts.SZS_ORIGINAL
                    if policy == ts.SZS_POLICY:
                        for offset, context in ((ts.SZS_CONTEXT_OFFSET, ts.SZS_CONTEXT), *ts.SZS_DATAFLOW_CONTEXTS):
                            source[offset:offset + len(context)] = context
                    original = bytes(source)
                    layout = {'KEY_PAGE_OFF': 0x1c08000, 'PATCH_OFF': 0x1c0c000,
                              'KEY_PAGE_SIZE': 0x4000, 'TEXT_END': 23280432,
                              'RO_OFF': 0x1b78000, 'DATA_OFF': 0x1c04000}
                else:
                    original = original.ljust(0xa24100, b'\0')
                    layout = {'KEY_PAGE_OFF': 0xa28000, 'PATCH_OFF': 0xa2c000,
                              'KEY_PAGE_SIZE': 0x4000, 'TEXT_END': ts.SITE + 32,
                              'RO_OFF': 0xa20000, 'DATA_OFF': 0xa24000}
                generic = bytearray(original.ljust(layout['PATCH_OFF'] + 0x4000, b'\0'))
                struct.pack_into('<I', generic, 0, 0x14000000 | (layout['PATCH_OFF'] // 4))
                struct.pack_into('<I', generic, layout['PATCH_OFF'], 0xd65f03c0)
                for i, key in enumerate(('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI')):
                    layout[f'KEY_{key}_IDX'] = i
                (rewrite / 'main.fixups.tsv').write_text(''.join(
                    f'# {k}\t{v if k.endswith("_IDX") else hex(v)}\n' for k, v in layout.items()))
                (rewrite / 'main.gates.tsv').write_text(f"{layout['PATCH_OFF'] + 4:#x}\tsvc\t38\t0x0\n")
                main_source = next(record for record in report['modules'] if record['name'] == 'main')
                main_source.update(memory_bytes=layout['KEY_PAGE_OFF'], segments=[
                    {'name': 'text', 'address': 0, 'bytes': layout['TEXT_END']},
                    {'name': 'rodata', 'address': layout['RO_OFF'], 'bytes': 0x1000},
                    {'name': 'data', 'address': layout['DATA_OFF'], 'bytes': 0x100}])
                image, am = address_apply('main', original, bytes(generic), {'generic_site_offsets': [0]})
                main = report['rewrites'][0]
                main.update(host_address_adaptation=am, generic_image_sha256=hashlib.sha256(generic).hexdigest())
                if heap_enabled:
                    image, hm = hp.apply(original, image, layout, generic_site_offsets=[0])
                    main.update(heap_adaptation=hm, host39_image_sha256=hm['input_image_sha256'])
                    report['guest_heap_policy'] = hp.POLICY.copy()
                final, sm = ts.apply(original, image, generic_site_offsets=[0], policy=policy_name)
                main.update(thread_stack_adaptation=sm, thread_stack_input_image_sha256=sm['input_image_sha256'],
                            image_sha256=hashlib.sha256(final).hexdigest())
                (rewrite / 'main.patched.bin').write_bytes(final)
                report_path.write_text(json.dumps(report))
                good = build_framework(report_path, rewrite, folder / 'good', module_names=['main'])
                self.assertEqual(good['guest_thread_stack_policy'], policy)
                for mode in ('drop_policy', 'drop_all_metadata', 'wrong_input', 'wrong_order', 'wrong_source', 'wrong_policy'):
                    bad = copy.deepcopy(report)
                    if mode == 'drop_policy': del bad['guest_thread_stack_policy']
                    elif mode == 'drop_all_metadata':
                        del bad['guest_thread_stack_policy']
                        del bad['rewrites'][0]['thread_stack_adaptation']
                        del bad['rewrites'][0]['thread_stack_input_image_sha256']
                    elif mode == 'wrong_input': bad['rewrites'][0]['thread_stack_input_image_sha256'] = '00' * 32
                    elif mode == 'wrong_source': bad['modules'][-1]['sha256'] = '00' * 32
                    elif mode == 'wrong_policy': bad['guest_thread_stack_policy'] = dict(policy, schema=True)
                    else: bad['rewrites'][0]['thread_stack_adaptation']['input_image_bytes'] -= 64
                    report_path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError):
                        build_framework(report_path, rewrite, folder / mode, module_names=['main'])
                    self.assertFalse((folder / mode).exists())
                if policy == ts.SZS_POLICY:
                    data = bytearray(ts.verify(final, sm))
                    data[ts.SZS_SITE:ts.SZS_SITE + 4] = ts.SZS_REPLACEMENT
                    bad = copy.deepcopy(report)
                    del bad['guest_thread_stack_policy']
                    del bad['rewrites'][0]['thread_stack_adaptation']
                    del bad['rewrites'][0]['thread_stack_input_image_sha256']
                    bad['rewrites'][0]['image_sha256'] = hashlib.sha256(data).hexdigest()
                    (rewrite / 'main.patched.bin').write_bytes(data)
                    report_path.write_text(json.dumps(bad))
                    with self.assertRaisesRegex(ValueError, 'explicit policy'):
                        build_framework(report_path, rewrite, folder / 'szs_only_no_policy', module_names=['main'])
                    self.assertFalse((folder / 'szs_only_no_policy').exists())

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
            def complete(data):
                padded = bytearray(data.ljust(23280432, b'\0'))
                padded[ts.SZS_SITE:ts.SZS_SITE + 4] = ts.SZS_ORIGINAL
                return bytes(padded)
            def check(data, text=23280432):
                raw = ctypes.create_string_buffer(data); error = ctypes.create_string_buffer(512)
                return audit(raw, len(data), text, error, 512)
            self.assertEqual(check(b'\0' * 4096, 0x100), 0)
            self.assertEqual(check(image), 0)
            self.assertEqual(check(complete(patched)), 1)
            self.assertEqual(check(patched[:ts.SITE + 2]), -1)
            self.assertEqual(check(patched[:ts.SITE + 4]), -1)
            self.assertEqual(check(patched, 0x1000), -1)
            native, _ = ts.apply(original, complete(image), generic_site_offsets=[], policy='host-pthread-stack-v2')
            self.assertEqual(check(native), 2)
            # Removing metadata cannot hide the early branch or substitute only alignment.
            for offset in [ts.CAPACITY_SITE - 4, ts.CAPACITY_SITE, ts.DATAFLOW_CONTEXTS[-1][0], len(native) - 8, len(native) - 4]:
                bad = bytearray(native); bad[offset] ^= 1
                self.assertEqual(check(bytes(bad)), -1)
            self.assertEqual(check(native[:-4]), -1)
            bad = bytearray(complete(image)); bad[ts.CAPACITY_SITE] ^= 1
            self.assertEqual(check(bytes(bad)), -1)
            bad = bytearray(native); bad[ts.SITE:ts.SITE + 4] = ts.ORIGINAL
            self.assertEqual(check(bytes(bad)), -1)
            for offset in [ts.SITE - 4, ts.SITE + 4]:
                bad = bytearray(patched); bad[offset] ^= 1
                self.assertEqual(check(bytes(bad)), -1)

    @unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Actual A64 host pthread execution')
    def test_actual_a64_checked_capacity_reaches_all_consumers_and_preserves_sdk_header(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            code = '\n'.join(f' .long {word[0]}' for word in struct.iter_unpack('<I', ts.CAPACITY_CODE))
            assembly = CAPACITY_ASSEMBLY.replace('CAPACITY_CODE', code)
            (folder / 'capacity.S').write_text(assembly)
            (folder / 'capacity.c').write_text(CAPACITY_NATIVE_FIXTURE)
            binary = folder / 'capacity'
            subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                            '-I', str(ROOT / 'native/botw_host'), str(folder / 'capacity.c'),
                            str(folder / 'capacity.S'), str(ROOT / 'native/botw_host/kernel.c'), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('actual_a64_capacity_header_pthread=true', result.stdout)

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


CAPACITY_ASSEMBLY = r"""
.text
.p2align 2
.globl _stack_capacity_capture
_stack_capacity_capture:
 stp x19,x20,[sp,#-48]!
 stp x23,x24,[sp,#16]
 stp x29,x30,[sp,#32]
 mov x19,x1
 mov w6,w0
 mov x8,#0x1357
 mov x9,#0x2468
 mov x10,#0xa0000000
 msr nzcv,x10
 mov x20,sp
 b 2f
1:
 str w24,[x19]
 sxtw x23,w24
 str x23,[x19,#8]
 mov x4,x23
 str x4,[x19,#16]
 stp x8,x9,[x19,#24]
 mrs x10,nzcv
 str x10,[x19,#40]
 mov x10,sp
 sub x10,x10,x20
 str x10,[x19,#48]
 str x6,[x19,#56]
 mov x0,x23
 ldp x29,x30,[sp,#32]
 ldp x23,x24,[sp,#16]
 ldp x19,x20,[sp],#48
 ret
2:
CAPACITY_CODE
 b 1b
"""
CAPACITY_NATIVE_FIXTURE = r"""
#include "kernel.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>
#include <unistd.h>
static botw_kernel *k;
static char error[1024];
static uint64_t low, end;
extern uint64_t stack_capacity_capture(uint32_t size, uint64_t *consumers);
static int executable(void *p, uint64_t a){(void)p;return a>=0x10000 && !(a&3);}
static int memory(void*p,uint64_t a,size_t n,int w){(void)p;(void)w;return a>=0x10000 && n<=4096 && a<=UINT64_MAX-n;}
static void svc(unsigned n,uint64_t*r){int rc=botw_kernel_dispatch(k,n,r,error,sizeof error);if(rc!=BOTW_KERNEL_HANDLED||r[0])fprintf(stderr,"svc%x rc%d result%llx: %s\n",n,rc,(unsigned long long)r[0],error);assert(rc==BOTW_KERNEL_HANDLED&&!r[0]);}
static void sdk_thread_entry(uint64_t argument,uint32_t handle){
 (void)handle;uint64_t header=*(uint64_t*)(uintptr_t)(end-32);assert(header==argument);
 char local;assert((uintptr_t)&local>=low && (uintptr_t)&local<end-0x4000);
 assert((uintptr_t)pthread_get_stackaddr_np(pthread_self())==end-0x4000);
 assert(pthread_get_stacksize_np(pthread_self())==0x4000);
 uint64_t r[32]={0};svc(0xa,r);assert(0);
}
static int execute(void*p,uint64_t entry,uint64_t arg,uint64_t top,uint32_t h,char*e,size_t c){
 (void)p;(void)e;(void)c;assert(top==end-32);((void(*)(uint64_t,uint32_t))(uintptr_t)entry)(arg,h);return -1;
}
int main(void){
 uint32_t requests[]={0,1,4095,4096,8192,16384,32768,524288,0x7fff8000,0x7fffc000,0x7fffffff,0x80000000,0xffffffff};
 for(unsigned i=0;i<sizeof requests/sizeof *requests;i++){
  uint32_t n=requests[i];uint64_t out[8]={0};
  uint64_t expected=(uint64_t)(int64_t)(int32_t)n;
  if(n && n<=0x7fff8000 && !(n&0xfff))expected=((n+UINT64_C(0x3fff))&~UINT64_C(0x3fff))+0x4000;
  assert(stack_capacity_capture(n,out)==expected);
  assert(out[0]==(uint32_t)expected && out[1]==expected && out[2]==expected);
  assert(out[3]==0x1357 && out[4]==0x2468 && out[5]==0xa0000000 && !out[6] && out[7]==n);
 }
 assert(sysconf(_SC_PAGESIZE)==0x4000);
 uint64_t out[8]={0}, capacity=stack_capacity_capture(0x4000,out);assert(capacity==0x8000);
 botw_kernel_callbacks cb={.memory=memory,.executable=executable,.execute_thread=execute};k=botw_kernel_create(&cb,NULL);assert(k);
 assert(!botw_kernel_configure_core_mask(k,7,error,sizeof error));
 assert(!botw_kernel_configure_thread_priority_mask(k,UINT64_C(0x0ffffffff0000000),error,sizeof error));
 assert(!botw_kernel_configure_memory(k,0x200000,0x200000,0x200000,0x200000,0,error,sizeof error));
 uint64_t r[32]={0,0x200000};svc(1,r);uint64_t heap=r[1];
 r[1]=14;r[2]=BOTW_KERNEL_CURRENT_PROCESS;svc(0x29,r);low=r[1];end=low+capacity;
 memset((void*)(uintptr_t)heap,0x3e,capacity+0x4000);
 r[0]=low;r[1]=heap;r[2]=capacity;svc(4,r);
 unsigned char tail[0x4000];memset(tail,0xa5,sizeof tail);uint64_t arg=0x987654321;
 memcpy(tail+sizeof tail-32,&arg,sizeof arg);memcpy((void*)(uintptr_t)(end-sizeof tail),tail,sizeof tail);
 r[1]=(uintptr_t)sdk_thread_entry;r[2]=arg;r[3]=end-32;r[4]=35;r[5]=(uint32_t)-2;svc(8,r);uint32_t h=(uint32_t)r[1];
 r[0]=h;svc(9,r);r[1]=(uintptr_t)&h;r[2]=1;r[3]=1000000000;svc(0x18,r);
 assert(!memcmp((void*)(uintptr_t)(end-sizeof tail),tail,sizeof tail));
 assert(!botw_kernel_close_handle(k,h,error,sizeof error));
 r[0]=low;r[1]=heap;r[2]=capacity;svc(5,r);
 assert(!memcmp((void*)(uintptr_t)(heap+capacity-sizeof tail),tail,sizeof tail));
 for(unsigned i=0;i<0x4000;i++)assert(*(unsigned char*)(uintptr_t)(heap+capacity+i)==0x3e);
 assert(!botw_kernel_shutdown(k,error,sizeof error));botw_kernel_destroy(k);
 puts("actual_a64_capacity_header_pthread=true checked_size_consumers=true flags_scratch_preserved=true");
}
"""

if __name__ == '__main__':
    unittest.main()
