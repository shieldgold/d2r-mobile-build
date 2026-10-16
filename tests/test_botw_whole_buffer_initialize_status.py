"""Buffer result logging over actual entries and the owned pool backing provider."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/buffer-initialize-status.patch'
BASELINE = os.environ.get('BOTW_WHOLE_BUFFER_INITIALIZE_STATUS_BASELINE') == '1'
SPEC = importlib.util.spec_from_file_location(
    'buffer_status_backing_fixture', ROOT / 'tests/test_botw_whole_pool_backing.py')
BACKING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKING)

OBSERVE_IMPL = r'''
static NVNboolean owned_observe_buffer_initialize(NVNbuffer *buffer, const NVNbufferBuilder *builder) {
    status_impl_calls++;
    NVNboolean result = botw_nvn_buffer_initialize_impl(buffer, builder);
    status_impl_result = result;
    status_impl_errno = errno;
    status_impl_returned = 1;
    return result;
}
#define botw_nvn_buffer_initialize_impl owned_observe_buffer_initialize
'''


@unittest.skipUnless(sys.platform == 'darwin', 'requires real Darwin VM and Objective-C')
class WholeBufferInitializeStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Preserve the original shared provider and its generated reference
        # lifetime seams. All tested entry bodies and headers below come from
        # the complete current SOURCE patch sequence, not a second formatter.
        if BACKING.BASELINE:
            raise AssertionError('this fixture requires the actual ordinary pool provider')
        BACKING.WholePoolBacking.setUpClass.__func__(cls)
        cls.source = cls.work / 'complete-source'
        shutil.copytree(EXP / 'upstream', cls.source)
        patches = json.loads((EXP / 'SOURCE.lock.json').read_text())['patches']
        cls.applied_patches = []
        for name in patches:
            cls.apply(name)
        if PATCH in cls.applied_patches:
            cls.apply(PATCH, reverse=True)
        buffer_path = cls.source / 'dm/gfx/nvn/botw_buffer.c'
        cls.original = BACKING.function(buffer_path.read_text(), 'API(nvnBufferInitialize,')
        cls.has_status = not BASELINE and (EXP / PATCH).is_file()
        if cls.has_status:
            cls.apply(PATCH)
        cls.actual = buffer_path.read_text()
        prefix = cls.actual[cls.actual.index('struct buffer_builder {'):
                            cls.actual.index('API(nvnBufferInitialize,')]
        private = 'static NVNboolean botw_nvn_buffer_initialize_impl('
        # The production private function precedes its public API, so do not
        # duplicate it in the copied declaration/builder prefix.
        if private in prefix:
            prefix = prefix[:prefix.index(private)]
        entries = prefix
        if cls.has_status:
            entries += BACKING.function(cls.actual, private) + '\n' + OBSERVE_IMPL
        entries += BACKING.function(cls.actual, 'API(nvnBufferInitialize,') + '\n'
        if cls.has_status:
            entries += '#undef botw_nvn_buffer_initialize_impl\n'
        (cls.work / 'buffer_entries.inc').write_text(entries)

        # Use the actual current pool entries, including their genuine status
        # wrapper. The original provider's factories and checked VM copies are
        # reused unchanged; no Metal device is created by this CPU fixture.
        hle = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        pool_entries = hle[hle.index('// WHOLE_POOL_BACKING_ENTRIES_BEGIN'):
                           hle.index('// WHOLE_POOL_BACKING_ENTRIES_END')]
        private_pool = 'static NVNboolean botw_nvn_memory_pool_initialize_impl('
        if private_pool in hle:
            pool_entries += '\n' + BACKING.function(hle, private_pool)
        for name in ('Initialize', 'Finalize', 'Map', 'GetSize', 'GetFlags', 'GetBufferAddress',
                     'FlushMappedRange', 'InvalidateMappedRange'):
            pool_entries += '\n' + BACKING.function(hle, 'NVN(nvnMemoryPool' + name + ',')
        (cls.work / 'pool_entries.inc').write_text(pool_entries)
        (cls.work / 'pool_gate.inc').write_text(
            hle[hle.index('// WHOLE_POOL_BACKING_GATE_BEGIN'):
                hle.index('// WHOLE_POOL_BACKING_GATE_END')])
        mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        (cls.work / 'pool_drains.inc').write_text(BACKING.function(mtl, 'void mtl_pool_drain('))
        common = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-function', '-Wno-unused-variable', '-Wno-format', '-pthread',
                  '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                  '-I', str(cls.work), '-I', str(cls.source / 'dm/gfx/nvn'),
                  '-I', str(cls.source / 'dm/gfx/nvn/include'), '-I', str(EXP / 'adapter'),
                  '-I', str(EXP / 'tests')]
        logger = cls.work / 'buffer_logger.o'
        run = subprocess.run(common + ['-Dwrite=owned_buffer_status_write', '-c',
                                      str(EXP / 'adapter/whole_policy.c'), '-o', str(logger)],
                             capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)
        cls.binary = cls.work / 'buffer_probe'
        command = common + ['-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                            '-DWHOLE_BUFFER_HAS_STATUS=' + str(int(cls.has_status)),
                            str(EXP / 'tests/whole_buffer_initialize_status_probe.m'),
                            str(logger), str(EXP / 'adapter/whole_pool_backing_memory.m'),
                            str(EXP / 'adapter/whole_pool_backing_gate.c'),
                            '-framework', 'Foundation', '-framework', 'Metal', '-o', str(cls.binary)]
        run = subprocess.run(command, capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)

    @classmethod
    def apply(cls, name, reverse=False):
        command = ['patch', '--batch', '--fuzz=0'] + (['-R'] if reverse else [])
        run = subprocess.run(command + ['-p1', '-i', str(EXP / name)], cwd=cls.source,
                             capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)
        if reverse:
            cls.applied_patches.remove(name)
        else:
            cls.applied_patches.append(name)

    def status_case(self, mode, outcomes, original_logs=()):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout, 'WHOLE_BUFFER_INITIALIZE_CPU_OK\n')
        actual = run.stderr.splitlines(keepends=True)
        pool_line = '[botw whole-host] api=NvnMemoryPoolInitialize result=ok\n'
        self.assertEqual(actual.count(pool_line), int(mode != 'physical'))
        actual = [line for line in actual if line != pool_line]
        expected_logs = ['[dm] ' + line + '\n' for line in original_logs]
        self.assertEqual([line for line in actual if line.startswith('[dm] ')], expected_logs)
        actual = [line for line in actual if not line.startswith('[dm] ')]
        self.assertEqual(actual, ['[botw whole-host] api=NvnBufferInitialize result=' + value + '\n'
                                  for value in outcomes],
                         'actual API completed; missing normal status is behavioral RED')

    def test_actual_backed_storage_success_reports_ok(self):
        self.status_case('success', ['ok'])

    def test_actual_storage_outside_pool_false_keeps_original_error_log(self):
        self.status_case('outside', ['error'],
                         ['[ERROR] NVN buffer storage lies outside its memory pool'])

    def test_actual_physical_only_false_keeps_original_error_log(self):
        self.status_case('physical', ['error'],
                         ['[ERROR] NVN buffers cannot use physical-only pools directly'])

    def test_repeated_real_success_and_false_each_log_once(self):
        self.status_case('once', ['ok', 'error'],
                         ['[ERROR] NVN buffer storage lies outside its memory pool'] * 2)

    def test_complete_original_body_and_typed_wrapper_call_are_unchanged(self):
        if self.has_status:
            actual = BACKING.function(self.actual, 'static NVNboolean botw_nvn_buffer_initialize_impl(')
            self.assertEqual(actual[actual.index('{'):], self.original[self.original.index('{'):])
            wrapper = BACKING.function(self.actual, 'API(nvnBufferInitialize,')
            self.assertEqual(wrapper.count('botw_nvn_buffer_initialize_impl(buffer, builder)'), 1)
        else:
            self.assertEqual(BACKING.function(self.actual, 'API(nvnBufferInitialize,'), self.original)


if __name__ == '__main__':
    unittest.main()
