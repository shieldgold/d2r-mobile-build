"""Explicit base-v0 heap adaptation: synthetic images and real ARM64 thunks."""
import copy
import hashlib
from pathlib import Path
import platform
import random
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from botw_native import heap_policy as hp


def fixture():
    original = bytearray(0x735400)
    for site in hp.SITES:
        original[site['offset'] - 8:site['offset'] + 12] = bytes.fromhex(site['context'])
    image = bytes(original).ljust(0x740000, b'\0')
    layout = {'KEY_PAGE_OFF': 0x738000, 'PATCH_OFF': 0x73c000,
              'KEY_KRES0_IDX': 4, 'KEY_KRES1_IDX': 5}
    return bytes(original), image, layout


class HeapPolicyTests(unittest.TestCase):
    def test_budgets_obey_both_live_limit_and_kernel_limit_and_keep_root_delta(self):
        unit = 0x200000
        for kernel, live in [(2191604224, 2191604224), (4 << 30, 6 << 30),
                             (1800 << 20, 4 << 30), (4 << 30, 1701 << 20)]:
            heap, root = hp.plan(kernel, live)
            self.assertEqual(heap % unit, 0)
            self.assertEqual(heap - root, 32 << 20)
            self.assertLessEqual(heap, min(kernel, live - hp.HOST_RESERVE, hp.ORIGINAL_HEAP))
            self.assertGreaterEqual(root, hp.MIN_ROOT)
        self.assertEqual(hp.plan(4 << 30, 6 << 30), (hp.ORIGINAL_HEAP, hp.ORIGINAL_ROOT))

    def test_low_memory_types_and_underflow_are_rejected(self):
        for kernel, live in [(0, 4 << 30), (4 << 30, 0), (4 << 30, hp.HOST_RESERVE - 1),
                             (hp.MIN_ROOT, 4 << 30), (4 << 30, hp.MIN_ROOT + hp.HOST_RESERVE),
                             (-1, 4 << 30), (True, 4 << 30), (4 << 30, 1 << 65)]:
            with self.subTest(kernel=kernel, live=live), self.assertRaises(ValueError):
                hp.plan(kernel, live)

    def test_apply_verify_is_transactional_and_recovers_host39_input(self):
        original, image, layout = fixture()
        patched, manifest = hp.apply(original, image, layout, generic_site_offsets=[])
        self.assertEqual(image, bytes(original).ljust(len(image), b'\0'))
        self.assertEqual(hp.verify(patched, manifest, expected_input_sha256=hashlib.sha256(image).hexdigest()), image)
        self.assertEqual(len(patched), len(image) + 64)
        self.assertEqual(manifest['profile'], hp.POLICY['profile'])
        self.assertEqual(manifest['slots'], {'heap': 'KRES0', 'root': 'KRES1'})

    def test_tampered_context_layout_generic_overlap_and_chain_are_rejected(self):
        original, image, layout = fixture()
        bad = bytearray(original); bad[hp.SITES[0]['offset'] - 4] ^= 1
        for source, inputs, positions in [(bytes(bad), layout, []), (original, dict(layout, KEY_KRES0_IDX=5), []),
                                           (original, dict(layout, KEY_PAGE_OFF=3), []),
                                           (original, layout, [hp.SITES[1]['offset']])]:
            with self.assertRaises(ValueError):
                hp.apply(source, image, inputs, generic_site_offsets=positions)
        patched, manifest = hp.apply(original, image, layout, generic_site_offsets=[])
        for offset in [hp.SITES[0]['offset'], len(image), len(patched) - 1]:
            bad = bytearray(patched); bad[offset] ^= 4
            with self.assertRaises(ValueError):
                hp.verify(bytes(bad), manifest)
        for key, value in [('input_image_bytes', True), ('profile', 'other'), ('slots', {'heap': 'KGATE', 'root': 'KRES1'})]:
            changed = copy.deepcopy(manifest); changed[key] = value
            with self.assertRaises(ValueError): hp.verify(patched, changed)
        with self.assertRaises(ValueError): hp.verify(patched, manifest, expected_input_sha256='00' * 32)
        changed = copy.deepcopy(manifest); changed['patches'][0]['register'] = False
        with self.assertRaises(ValueError): hp.verify(patched, changed)

    def test_explicit_policy_requires_host39_and_complete_fixed_source_identity(self):
        from botw_native.guest_framework import _heap_policy
        originals = {name: {'name': name, **record} for name, record in __import__('json').loads(
            (ROOT / 'profiles/botw-switch-base-v0.json').read_text())['modules'].items()}
        rewrites = {name: {} for name in originals}
        self.assertIsNone(_heap_policy({}, originals, rewrites, 39))
        rewrites['main'] = {'heap_adaptation': {}, 'host39_image_sha256': '00' * 32}
        for report, bits in [({}, 39), ({'guest_heap_policy': hp.POLICY}, 36),
                             ({'guest_heap_policy': dict(hp.POLICY, schema=True)}, 39)]:
            with self.assertRaises(ValueError): _heap_policy(report, originals, rewrites, bits)
        self.assertEqual(_heap_policy({'guest_heap_policy': hp.POLICY}, originals, rewrites, 39), hp.POLICY)
        changed = copy.deepcopy(originals); changed['sdk']['sha256'] = '00' * 32
        with self.assertRaises(ValueError): _heap_policy({'guest_heap_policy': hp.POLICY}, changed, rewrites, 39)
        rewrites['sdk']['heap_adaptation'] = {}
        with self.assertRaises(ValueError): _heap_policy({'guest_heap_policy': hp.POLICY}, originals, rewrites, 39)

    def test_real_arm64_thunks_preserve_registers_flags_sp_and_use_both_tsd_slots(self):
        try:
            from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
            from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_NZCV, UC_ARM64_REG_TPIDRRO_EL0
        except ImportError:
            self.skipTest('Unicorn is unavailable')
        original, image, layout = fixture()
        patched, manifest = hp.apply(original, image, layout, generic_site_offsets=[])
        u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        u.mem_map(0, (len(patched) + 0xffff) & ~0xffff); u.mem_write(0, patched)
        u.mem_map(0x10000000, 0x10000); u.mem_map(0x11000000, 0x1000)
        u.reg_write(UC_ARM64_REG_TPIDRRO_EL0, 0x11000000)
        rng = random.Random(3912)
        for heap, root in [(hp.ORIGINAL_HEAP, hp.ORIGINAL_ROOT), hp.plan(2191604224, 2191604224)]:
            u.mem_write(layout['KEY_PAGE_OFF'] + 4 * 8, struct.pack('<QQ', 80, 88))
            u.mem_write(0x11000000 + 80, struct.pack('<QQ', heap, root))
            for site, wanted in zip(hp.SITES, [heap, root]):
                values = [rng.getrandbits(64) for _ in range(31)]
                # Unicorn register enum is consecutive only for x0..x28.
                from unicorn import arm64_const as c
                regs = [getattr(c, 'UC_ARM64_REG_X' + str(i)) for i in range(31)]
                for reg, value in zip(regs, values): u.reg_write(reg, value)
                u.reg_write(UC_ARM64_REG_SP, 0x10008000); u.reg_write(UC_ARM64_REG_NZCV, 0xa0000000)
                u.emu_start(site['offset'], site['offset'] + 4, count=32)
                self.assertEqual(u.reg_read(regs[site['register']]), wanted)
                for i, reg in enumerate(regs):
                    if i != site['register']: self.assertEqual(u.reg_read(reg), values[i], f'x{i}')
                self.assertEqual(u.reg_read(UC_ARM64_REG_SP), 0x10008000)
                self.assertEqual(u.reg_read(UC_ARM64_REG_NZCV), 0xa0000000)


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'requires Apple ARM64')
class RuntimeHeapPolicyTests(unittest.TestCase):
    def test_c_budget_audit_and_child_tsd_match_python_contract(self):
        original, image, layout = fixture()
        patched, manifest = hp.apply(original, image, layout, generic_site_offsets=[])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); (folder / 'image.bin').write_bytes(patched)
            source = folder / 'probe.c'
            source.write_text('#include "' + str(ROOT / 'native/botw_host/runtime.c') + '"\n' + r'''
static void child_entry(uint64_t argument, uint64_t handle) {
    (void)argument; (void)handle;
    if (pthread_getspecific(state.keys[4]) != (void *)(uintptr_t)state.heap_size ||
            pthread_getspecific(state.keys[5]) != (void *)(uintptr_t)state.root_size)
        fail("heap child slots missing");
    fail("heap child slots verified");
}
static void *worker(void *opaque) {
    (void)opaque;
    char error[512];
    int rc = execute_child(NULL, (uint64_t)child_entry, 0, 0, 0x2004, error, sizeof error);
    return rc == -1 && !strcmp(error, "heap child slots verified") &&
        !pthread_getspecific(state.keys[4]) && !pthread_getspecific(state.keys[5]) ? NULL : (void *)1;
}
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    size_t heap = 0, root = 0; char error[512];
    if (botw_host_heap_policy_plan(2191604224, 2191604224, &heap, &root, error, sizeof error)) return 2;
    if (heap != 1923088384 || root != heap - 33554432) return 3;
    if (!botw_host_heap_policy_plan(1, 2191604224, &heap, &root, error, sizeof error)) return 4;
    if (!botw_host_heap_policy_plan(SIZE_MAX, 1, &heap, &root, error, sizeof error)) return 5;
    if (heap != 1923088384 || root != heap - 33554432) return 16;
    if (!botw_host_heap_policy_plan(SIZE_MAX, SIZE_MAX, &heap, &heap, error, sizeof error)) return 17;
    state.kernel = botw_kernel_create(NULL, NULL); if (!state.kernel) return 18;
    if (botw_kernel_configure_address_space(state.kernel, 39, error, sizeof error) ||
            botw_kernel_configure_memory(state.kernel, 4ull<<30, 256ull<<20, 512ull<<20,
                2247358976, 55754752, error, sizeof error)) return 19;
    uint64_t r[31] = {0, heap};
    if (botw_kernel_dispatch(state.kernel, 1, r, error, sizeof error) != BOTW_KERNEL_HANDLED || r[0]) return 20;
    r[1]=6; r[2]=BOTW_KERNEL_CURRENT_PROCESS; r[3]=0;
    if (botw_kernel_dispatch(state.kernel, 0x29, r, error, sizeof error) != BOTW_KERNEL_HANDLED ||
            r[0] || r[1]!=2247358976) return 21;
    r[1]=7;
    if (botw_kernel_dispatch(state.kernel, 0x29, r, error, sizeof error) != BOTW_KERNEL_HANDLED ||
            r[0] || r[1]!=55754752+heap) return 22;
    botw_kernel_destroy(state.kernel); state.kernel=NULL;
    FILE *f = fopen(argv[1], "rb"); if (!f) return 6;
    if (fseek(f, 0, SEEK_END)) return 7; size_t size = (size_t)ftell(f); rewind(f);
    unsigned char *bytes = malloc(size); if (!bytes || fread(bytes, size, 1, f) != 1) return 8; fclose(f);
    Module m = {.name="main", .base=bytes, .size=size, .text_end=0x735400,
        .patch_off=0x73c000, .key_off=0x738000, .key_size=PAGE};
    for (unsigned i=0;i<8;i++) m.key_idx[i]=i;
    if (heap_policy_image_mode(&m, error, sizeof error) != 1) return 9;
    m.size = 0x73530c;
    if (heap_policy_image_mode(&m, error, sizeof error) != -1) return 23;
    m.size = 0x1000;
    if (heap_policy_image_mode(&m, error, sizeof error) != 0) return 24;
    m.size = size;
    size_t first = 0x740000; bytes[first + 4] ^= 4;
    if (heap_policy_image_mode(&m, error, sizeof error) != -1) return 10;
    bytes[first + 4] ^= 4;
    bytes[0x735304] = 0; bytes[0x735305]=0x4c; bytes[0x735306]=0xb8; bytes[0x735307]=0x52;
    if (heap_policy_image_mode(&m, error, sizeof error) != -1) return 11;
    bytes[0x7353b0]=8; bytes[0x7353b1]=0xc; bytes[0x7353b2]=0xb8; bytes[0x7353b3]=0x52;
    if (heap_policy_image_mode(&m, error, sizeof error)) return 12;
    state.heap_size=1923088384; state.root_size=1889533952; state.heap_policy_active=1;
    for (unsigned i=0;i<8;i++) if(pthread_key_create(&state.keys[i],NULL))return 13;
    state.key_count=8;
    install_heap_parameter_slots();
    if (pthread_getspecific(state.keys[4]) != (void *)(uintptr_t)state.heap_size ||
            pthread_getspecific(state.keys[5]) != (void *)(uintptr_t)state.root_size) return 14;
    state.modules[0].base=(unsigned char *)(uintptr_t)child_entry; state.modules[0].text_end=4096;
    state.owner=pthread_self();
    pthread_t thread; void *result;
    if (pthread_create(&thread,NULL,worker,NULL) || pthread_join(thread,&result) || result) return 15;
    for (unsigned i=0;i<8;i++) pthread_key_delete(state.keys[i]);
    free(bytes); return 0;
}
''')
            sources = [source, ROOT / 'native/botw_host/entry.S', ROOT / 'native/botw_host/services.c',
                       ROOT / 'native/botw_host/kernel.c', ROOT / 'native/botw_host/hipc_address.c',
                       ROOT / 'native/botw_host/address_space.c', ROOT / 'native/botw_resources/savefs.c',
                       ROOT / 'native/botw_resources/local_user.c', ROOT / 'native/botw_input/input.c',
                       ROOT / 'native/botw_resources/romfs.c']
            executable = folder / 'probe'
            result = subprocess.run(['xcrun','clang','-framework','Security','-O2','-Wall','-Wextra','-Werror',
                                     *(str(s) for s in sources),'-o',str(executable)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(executable), str(folder/'image.bin')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
