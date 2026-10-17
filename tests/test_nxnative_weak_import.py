"""Execute complete current import/relocation functions on owned CPU fixtures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_nxnative_normal_hle_crash import MEMBERS, function_span

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'


def function(source, marker):
    start, end = function_span(source, marker)
    return source[start:end]


class NxnativeWeakImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nxnative-actual-weak-import-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        stage = work / 'source'
        for name in (*MEMBERS, 'dm/host/loader.c', 'dm/host/main.c', 'dm/host/hle.h'):
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(COMPONENT / 'upstream' / name, destination)
        for name in ('normal-entry.patch', 'normal-hle-crash.patch', 'base-v0-logical-memory.patch',
                     'base-v0-normal-loader-guard.patch', 'base-v0-weak-import-resolution.patch'):
            patch = COMPONENT / 'patches' / name
            result = subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(patch)],
                                    cwd=stage, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        host = stage / 'dm/host'
        (host / 'game_profile.h').write_text('/* Owned fixture has no game metadata. */\n')
        loader = (host / 'loader.c').read_text()
        hle = (host / 'hle.c').read_text()
        start = hle.index('#define MAX_STUB')
        end = hle.index('// Per module', start)
        (work / 'original_import.inc').write_text(hle[start:end] + function(hle, 'uint64_t resolve_import('))
        markers = ('static void *module_sym_linear(', 'static uint32_t sym_hash(',
                   'static void build_symhash(module_t *m) {', 'void *module_sym(',
                   'static uint64_t resolve_sym(', 'static void *force_lookup(')
        (work / 'original_resolution.inc').write_text('\n'.join(function(loader, marker) for marker in markers))
        (work / 'original_relocation.inc').write_text(function(loader, 'static unsigned relr_apply(') +
                                                       function(loader, 'static void relocate('))
        # Reuse the complete owned probe; adapt only the current three-field
        # force registry, helper name, and newer defined-symbol interposition.
        probe = (ROOT / 'experiments/botw_upstream_host/tests/whole_weak_import_probe.c').read_text()
        replacements = (
            ('#include "relr.h"', '#include "normal_failures.h"'),
            (',DM_TM_BOTW}', '}'),
            ('*g_force_on=force_on', '*g_force_selected=force_on'),
            ('int base_v0_force_reference(', 'int nxnative_base_v0_loader_reference('),
            ('#include "original_stop.inc"', ''),
            ('assert(relocate_one(1,R_AARCH64_GLOB_DAT,0)==(uintptr_t)consumer_memory+768&&!g_nstub);',
             'assert(relocate_one(1,R_AARCH64_GLOB_DAT,0)==(uintptr_t)provider_memory+256&&!g_nstub);'),
        )
        for before, after in replacements:
            if probe.count(before) != 1:
                raise AssertionError('owned probe adaptation changed: ' + before)
            probe = probe.replace(before, after)
        (work / 'probe.c').write_text(probe)
        cls.binary = work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter',
                   '-DNXNATIVE_NORMAL_POLICY=1', '-DNXNATIVE_BASE_V0_LOADER_GUARD=1',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-pthread',
                   '-I', str(host), '-I', str(COMPONENT / 'adapter'), '-I', str(work),
                   str(work / 'probe.c'), str(COMPONENT / 'adapter/normal_failures.c'),
                   str(COMPONENT / 'adapter/normal_policy.c'), '-o', str(cls.binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('WHOLE_WEAK_IMPORT_CPU_OK', result.stdout)

    def test_missing_notype_weak_glob_dat_and_jump_slot_remain_zero(self): self.case('weak-zero')
    def test_missing_strong_still_reuses_stub_and_stops_with_exit_three(self): self.case('strong')
    def test_weak_after_same_name_strong_stays_zero(self): self.case('weak-after-strong')
    def test_real_hle_remains_callable_and_first_duplicate_wins(self): self.case('hle')
    def test_cross_module_definition_stays_ahead_of_hle(self): self.case('module')
    def test_defined_weak_preserves_current_global_interposition(self): self.case('defined-weak')
    def test_selected_force_stays_ahead_of_all_symbol_definitions(self): self.case('force')
    def test_addends_keep_abs_glob_and_jump_semantics(self): self.case('addends')
    def test_last_symbol_and_relative_bypass_boundaries(self): self.case('boundaries')
