"""Fixed-upstream texture algorithms and real Darwin dirty-page tracking.

All data and mappings are synthetic. Native outputs live in a temporary directory;
no ignored upstream checkout or game content is required to run these tests.
"""
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'native/botw_graphics/vendor'
TEXTURE = VENDOR / 'botw-texture'
CLANG = shutil.which('clang')
FLAGS = ['-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror',
         '-O1', '-g', '-fsanitize=address,undefined', '-pthread']


def compile_fixture(output, name, sources):
    target = output / name
    result = subprocess.run([CLANG, *FLAGS, *map(str, sources), '-o', str(target)],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(f'{name} compile failed:\n{result.stdout}\n{result.stderr}')
    return target


def run_fixture(test, target, *args, expected=0):
    result = subprocess.run([str(target), *args], cwd=ROOT, capture_output=True,
                            text=True, timeout=25)
    test.assertEqual(result.returncode, expected,
                     f'{target.name} {args}:\n{result.stdout}\n{result.stderr}')
    test.assertNotIn('runtime error:', result.stderr)
    test.assertNotIn('ERROR: AddressSanitizer', result.stderr)
    return result.stdout


@unittest.skipUnless(CLANG, 'native texture fixtures require clang')
class TextureModuleAlgorithmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='botw-texture-algorithms-')
        cls.addClassCleanup(cls.folder.cleanup)
        out = Path(cls.folder.name)
        cls.write = compile_fixture(out, 'texwrite-bounds', [
            TEXTURE / 'tests/t_texwrite_bounds.c', TEXTURE / 'nvn_texwrite.c',
            VENDOR / 'nvn/nvn_tex.c', VENDOR / 'tex/nvn_deswizzle.c'])
        cls.checked = compile_fixture(out, 'texwrite-checked', [TEXTURE / 'tests/t_texwrite_checked.c',
            TEXTURE / 'nvn_texwrite.c', VENDOR / 'nvn/nvn_tex.c', VENDOR / 'tex/nvn_deswizzle.c'])
        cls.own = compile_fixture(out, 'texown', [TEXTURE / 'tests/t_texown.c',
                                                TEXTURE / 'nvn_texown.c'])

    def test_upstream_write_bounds_and_exact_storage_roundtrips(self):
        self.assertIn('373 checks, 0 failed', run_fixture(self, self.write))

    def test_checked_write_visits_exact_runs_and_stops_on_copy_failure(self):
        self.assertIn('CHECKED_TEXWRITE_EXACT_RUNS_AND_FAILURE_STOP_OK', run_fixture(self, self.checked))

    def test_upstream_live_retired_byte_claims_and_racing_fresh_claims(self):
        self.assertIn('43 checks, 0 failed', run_fixture(self, self.own))


@unittest.skipUnless(CLANG and platform.system() == 'Darwin',
                     'real dirty protection requires Darwin Mach VM')
class TextureModuleDirtyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='botw-texture-dirty-')
        cls.addClassCleanup(cls.folder.cleanup)
        out = Path(cls.folder.name)
        cls.dirty = compile_fixture(out, 'dirty-port', [TEXTURE / 'tests/t_dirty_port.c'])
        cls.shared = compile_fixture(out, 'dirty-shared', [TEXTURE / 'tests/t_dirty_unambiguous.c'])
        cls.flush = compile_fixture(out, 'texflush-rules', [
            TEXTURE / 'tests/t_texflush_rules.c', TEXTURE / 'nvn_texflush.c', TEXTURE / 'nvn_dirty.c'])
        cls.allocation = compile_fixture(out, 'checked-allocation', [
            TEXTURE / 'tests/t_allocation.c', VENDOR / 'nvn/nvn_tex.c', VENDOR / 'tex/nvn_deswizzle.c'])
        # The production shell builds one Objective-C translation unit. Exercise
        # the exact aggregation with the existing layout/deswizzle include order.
        source = out / 'amalgamation.m'
        source.write_text('#import <Foundation/Foundation.h>\n'
                          f'#include "{TEXTURE / "modules.inc"}"\n'
                          f'#include "{VENDOR / "nvn/nvn_tex.c"}"\n'
                          '#define mip fixture_deswizzle_mip\n'
                          f'#include "{VENDOR / "tex/nvn_deswizzle.c"}"\n'
                          '#undef mip\nint main(void){return nvn_texown_count(0)!=0;}\n')
        cls.amalgamation = compile_fixture(out, 'amalgamation', [source, '-framework', 'Foundation'])

    def test_upstream_flush_rules_and_dirty_backed_certificates(self):
        self.assertIn('5022 checks, 0 failed', run_fixture(self, self.flush))

    def test_single_objc_translation_unit(self):
        run_fixture(self, self.amalgamation)

    def test_real_write_fault_and_two_already_faulted_writers(self):
        for case in ('fault', 'stale-two-writers'):
            with self.subTest(case=case):
                run_fixture(self, self.dirty, case)

    def test_upstream_shared_pages_retirement_and_racing_writers(self):
        output = run_fixture(self, self.shared)
        match = re.search(r': \d+ checks, 0 failed \(host page \d+ bytes, (\d+) stress writes', output)
        self.assertIsNotNone(match, output)
        self.assertGreaterEqual(int(match[1]), 20000)

    def test_unowned_faults_reach_previous_disposition(self):
        # 88 is the previous-disposition sentinel; a swallowed retry loop exits
        # 77. These are real faulting stores/loads, not direct hook invocations.
        for case in ('stale-none', 'stale-read', 'owned-none', 'read-fault', 'untracked-write'):
            with self.subTest(case=case):
                run_fixture(self, self.dirty, case, expected=88)

    def test_checked_bridge_prepare_registration_and_retirement_failures(self):
        for case in ('unconfigured', 'prepare-failure', 'retirement-failure',
                     'retirement-drains', 'allocation-failure', 'allocation-failure2',
                     'allocation-failure3', 'shared-reregister', 'invalid', 'query-failure',
                     'prepare-protect-aba', 'submap-read'):
            with self.subTest(case=case):
                run_fixture(self, self.dirty, case)

    def test_allocation_failures_leave_no_false_coherence_or_ownership(self):
        cases = [('owner', '1'), *[('flush', str(n)) for n in range(1, 5)],
                 ('invalidate', '1'), ('verify', '1'), ('hmap', '1'), ('hmap', '2')]
        for case in cases:
            with self.subTest(case=case):
                run_fixture(self, self.allocation, *case)

    def test_no_certificate_without_host_bridge_and_quiescent_context_reuse(self):
        for case in ('unconfigured', 'reset'):
            with self.subTest(case=case):
                run_fixture(self, self.allocation, case, '1')


if __name__ == '__main__':
    unittest.main()
