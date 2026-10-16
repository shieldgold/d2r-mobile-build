"""Compile the real adapter against owned public SDK width models; no VM reads."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / 'experiments/botw_upstream_backend/adapter'
HEADER = ADAPTER / 'public_vm_read.h'
PROBE = ROOT / 'experiments/botw_upstream_backend/tests/public_vm_read_probe.c'

FAKE_MACH = r'''
#ifndef OWNED_FAKE_MACH_H
#define OWNED_FAKE_MACH_H
#include <stdint.h>
typedef int kern_return_t;
typedef uint32_t vm_map_t;
typedef uint64_t mach_vm_address_t;
typedef uint64_t mach_vm_size_t;
#if VM_TEST_BITS == 32
typedef uint32_t vm_address_t;
typedef uint32_t vm_size_t;
#else
typedef uint64_t vm_address_t;
typedef uint64_t vm_size_t;
#endif
#define KERN_SUCCESS 0
#define KERN_INVALID_ADDRESS 1
#define KERN_PROTECTION_FAILURE 2
#define KERN_INVALID_ARGUMENT 4
kern_return_t vm_read_overwrite(vm_map_t, vm_address_t, vm_size_t, vm_address_t, vm_size_t *);
#endif
'''
FAKE_MACH_VM = r'''
#if TARGET_OS_IPHONE
#error mach/mach_vm.h is unavailable in the public iPhoneOS SDK contract
#endif
#include <mach/mach.h>
kern_return_t mach_vm_read_overwrite(vm_map_t, mach_vm_address_t, mach_vm_size_t,
                                     mach_vm_address_t, mach_vm_size_t *);
'''


class UpstreamPublicVMReadTests(unittest.TestCase):
    def require_header(self):
        self.assertTrue(HEADER.is_file(), 'public VM read adapter contract is missing: ' + str(HEADER))

    def probe(self, bits, iphone):
        self.require_header()
        with tempfile.TemporaryDirectory(prefix='botw-owned-public-vm-read-') as name:
            root = Path(name)
            (root / 'mach').mkdir()
            (root / 'mach/mach.h').write_text(FAKE_MACH)
            (root / 'mach/mach_vm.h').write_text(FAKE_MACH_VM)
            (root / 'TargetConditionals.h').write_text('#undef TARGET_OS_IPHONE\n#define TARGET_OS_IPHONE ' + str(iphone) + '\n')
            executable = root / 'probe'
            command = ['/usr/bin/clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                       '-DVM_TEST_BITS=' + str(bits), '-I' + str(root), '-I' + str(ADAPTER),
                       str(PROBE), '-o', str(executable)]
            compiled = subprocess.run(command, text=True, capture_output=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual((data['vm_bits'], data['iphone']), (bits, iphone))
            self.assertGreaterEqual(data['checks'], 13)
            self.assertEqual(data['public_calls'], 8 if iphone else 0)
            self.assertEqual(data['mach_calls'], 0 if iphone else 10)

    def test_public_adapter_contract_is_available(self):
        self.require_header()

    def test_32bit_public_types_reject_narrowing_and_preserve_kernel_results(self):
        self.probe(32, 1)

    def test_64bit_public_types_preserve_full_width_and_exact_read_outcomes(self):
        self.probe(64, 1)

    def test_non_iphone_path_keeps_the_original_mach_vm_entrypoint(self):
        for bits in (32, 64):
            with self.subTest(public_width=bits):
                self.probe(bits, 0)


if __name__ == '__main__':
    unittest.main()
