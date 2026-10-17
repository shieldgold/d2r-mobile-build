"""Actual normal graphics macro boundary; owned CPU only, no Metal calls."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
PATCH = COMPONENT / 'patches/base-v0-normal-graphics-counters.patch'
PREDECESSORS = (
    'normal-entry.patch', 'base-v0-texture-view.patch', 'normal-hle-crash.patch',
    'horizon-address-signal.patch', 'normal-runtime-diagnostics.patch',
    'base-v0-conditional-shader-provider.patch', 'base-v0-logical-memory.patch',
    'base-v0-normal-api-contracts.patch', 'base-v0-normal-loader-guard.patch',
)
BASELINE = os.environ.get('NXNATIVE_GRAPHICS_COUNTERS_BASELINE') == '1'
PROBE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* ACTUAL_LOG_MACROS */
/* ACTUAL_RED_MACRO */
int main(int argc, char **argv) {
    if (argc != 2) return 90;
    int effects = 0;
    if (!strcmp(argv[1], "effects")) {
        RED_ONCE(++effects, "%d", ++effects);
        return effects ? 91 : 0;
    }
    if (!strcmp(argv[1], "range")) {
        /* Volatile keeps the old index a runtime operand: baseline compilation
         * succeeds, then the real original array access fails under UBSan. */
        volatile int index = 84;
        RED_ONCE(index, "%d", ++effects);
        return effects ? 92 : 0;
    }
#if !defined(NXNATIVE_NORMAL_POLICY) || !NXNATIVE_NORMAL_POLICY
    if (!strcmp(argv[1], "original")) {
        RED_ONCE(3, "%d", ++effects);
        RED_ONCE(3, "%d", ++effects);
        return effects == 1 && g_red_once[3] == 2 ? 0 : 93;
    }
#endif
    return 90;
}
'''


class NormalGraphicsCounters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='nxnative-owned-red-macro-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        stage = cls.work / 'source'
        shutil.copytree(COMPONENT / 'upstream', stage)
        for name in (*PREDECESSORS, *(() if BASELINE else (PATCH.name,))):
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1', '-i',
                                     str(COMPONENT / 'patches' / name)], cwd=stage,
                                    capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        source = (stage / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        start = source.index('/* NXNATIVE_NORMAL_GPU_LOG_BEGIN */')
        end = source.index('/* NXNATIVE_NORMAL_GPU_LOG_END */', start)
        log = source[start:end]
        start = source.index('static int g_red_once[84];')
        # The appended patch preserves the original definitions inside #else.
        # Extract the entire actual conditional block when present.
        marker = '/* NXNATIVE_NORMAL_GRAPHICS_COUNTERS_BEGIN */'
        if marker in source:
            start = source.index(marker)
            end = source.index('/* NXNATIVE_NORMAL_GRAPHICS_COUNTERS_END */', start)
        else:
            end = source.index('\n', source.index('#define RED_ONCE', start)) + 1
        probe = PROBE.replace('/* ACTUAL_LOG_MACROS */', log).replace('/* ACTUAL_RED_MACRO */', source[start:end])
        path = cls.work / 'probe.c'
        path.write_text(probe)
        cls.binaries = {}
        for normal in (0, 1):
            binary = cls.work / f'probe-{normal}'
            command = ['xcrun', 'clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                       '-Wno-unused-variable', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                       f'-DNXNATIVE_NORMAL_POLICY={normal}', '-I', str(COMPONENT / 'adapter'),
                       str(path), '-o', str(binary)]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[normal] = binary
        cls.normal_preprocessed = subprocess.check_output([
            'xcrun', 'clang', '-E', '-P', '-DNXNATIVE_NORMAL_POLICY=1', '-I',
            str(COMPONENT / 'adapter'), str(path)], text=True)

    def run_owned(self, normal, mode):
        return subprocess.run([str(self.binaries[normal]), mode], capture_output=True, text=True,
                              timeout=5, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
                                              'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=0'})

    def test_normal_macro_does_not_evaluate_index_or_format_operands(self):
        result = self.run_owned(1, 'effects')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((result.stdout, result.stderr), ('', ''))

    def test_actual_index_84_is_not_accessed_in_normal_build(self):
        result = self.run_owned(1, 'range')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((result.stdout, result.stderr), ('', ''))

    def test_normal_build_has_no_diagnostic_counter_storage(self):
        self.assertFalse('g_red_once' in self.normal_preprocessed, 'normal build retains diagnostic counter storage')

    def test_non_normal_original_once_and_argument_semantics_remain(self):
        result = self.run_owned(0, 'original')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '[dm] [nvn-mtl] 【红】1\n')


if __name__ == '__main__':
    unittest.main()
