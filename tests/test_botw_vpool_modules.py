"""Full vendored interval/lease algorithm and synthetic host-port contracts.

No ignored upstream checkout, game content, Metal or graphics context is used.
The test-only controls are absent from the separately compiled production object.
"""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'native/botw_graphics/vendor/botw-vpool'
CLANG = shutil.which('clang')
FLAGS = ['-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror',
         '-O1', '-g', '-pthread', '-fsanitize=address,undefined']


@unittest.skipUnless(CLANG and platform.system() == 'Darwin',
                     'full vpool fixture requires clang and Darwin VM queries')
class VirtualPoolModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='botw-vpool-')
        cls.addClassCleanup(cls.folder.cleanup)
        cls.binaries = {}
        for name, source in [('baseline', 't_vpool.c'), ('port', 't_vpool_port.c')]:
            target = Path(cls.folder.name) / name
            flags = [*FLAGS, *(['-DNVN_VPOOL_TESTING'] if name == 'port' else [])]
            result = subprocess.run([CLANG, *flags,
                                     str(VENDOR / 'tests' / source), str(VENDOR / 'nvn_vpool.c'),
                                     '-o', str(target)], cwd=ROOT, capture_output=True,
                                    text=True, timeout=60)
            if result.returncode:
                raise AssertionError(f'{name} compile failed:\n{result.stdout}{result.stderr}')
            cls.binaries[name] = target

    def run_fixture(self, name, *args):
        result = subprocess.run([str(self.binaries[name]), *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=150)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('runtime error:', result.stderr)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        return result.stdout

    def test_full_upstream_random_and_concurrent_lifetimes(self):
        text = self.run_fixture('baseline')
        self.assertIn('t_vpool: 109 checks, 0 failed', text)
        self.assertRegex(text, r'stress: 20000 remaps, [1-9][0-9]* consistent reads')

    def test_color_raw_kind_boundaries_generations_and_legacy_contract(self):
        self.assertIn('0 failed', self.run_fixture('port', 'kind'))

    def test_registration_allocator_failures_publish_no_objects_or_capacity(self):
        for mode in ('alloc-physical', 'alloc-virtual'):
            for after in ('0', '1'):
                with self.subTest(mode=mode, after=after):
                    self.assertIn('0 failed', self.run_fixture('port', mode, after))

    def test_existing_registry_capacity_does_not_grow_on_failed_registration(self):
        for mode in ('grow-physical', 'grow-virtual'):
            for after in ('0', '1'):
                with self.subTest(mode=mode, after=after):
                    self.assertIn('0 failed', self.run_fixture('port', mode, after))

    def test_test_controls_are_absent_from_production_object(self):
        target = Path(self.folder.name) / 'production.o'
        compiled = subprocess.run([CLANG, *FLAGS, '-c', str(VENDOR / 'nvn_vpool.c'),
                                   '-o', str(target)], cwd=ROOT, capture_output=True,
                                  text=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        symbols = subprocess.run(['nm', '-g', str(target)], capture_output=True,
                                 text=True, timeout=10)
        self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
        self.assertIn('nvn_vpool_virt_register_strict', symbols.stdout)
        self.assertNotIn('nvn_vpool_test_', symbols.stdout)

    def test_both_map_scratch_allocation_failures_preserve_current_backing(self):
        for after in ('0', '1'):
            with self.subTest(after=after):
                self.assertIn('0 failed', self.run_fixture('port', 'alloc-map', after))

    def test_failed_reservations_and_partial_retry_leave_no_mapping(self):
        for after in ('0', '1'):
            with self.subTest(after=after):
                self.assertIn('0 failed', self.run_fixture('port', 'reserve', after))

    def test_legacy_high_token_fallback_and_strict_cleanup_use_real_vm(self):
        self.assertIn('0 failed', self.run_fixture('port', 'strict'))

    def test_strict_invalid_input_and_nested_writer_do_not_allocate(self):
        self.assertIn('0 failed', self.run_fixture('port', 'invalid'))


if __name__ == '__main__':
    unittest.main()
