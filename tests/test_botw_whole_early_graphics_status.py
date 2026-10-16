"""Actual early graphics API results; original Queue/Window providers retained."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/early-graphics-initialize-status.patch'
BASELINE = os.environ.get('BOTW_WHOLE_EARLY_GRAPHICS_STATUS_BASELINE') == '1'
SPEC = importlib.util.spec_from_file_location('early_status_queue_fixture',
                                            ROOT / 'tests/test_botw_whole_queue_acquire.py')
QUEUE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUEUE)


class WholeEarlyGraphicsStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class ActualQueueFixture(QUEUE.WholeQueueAcquire):
            pass
        ActualQueueFixture.setUpClass()
        cls.addClassCleanup(ActualQueueFixture.doClassCleanups)
        cls.work, cls.source = ActualQueueFixture.work, ActualQueueFixture.source
        if PATCH in ActualQueueFixture.applied_patches:
            run = subprocess.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-i', str(EXP / PATCH)],
                                 cwd=cls.source, capture_output=True, text=True)
            if run.returncode:
                raise AssertionError(run.stdout + run.stderr)
        original = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        cls.original_entries = {name: QUEUE.definition(original, 'NVN(' + name + ',')
                                for name in ('nvnQueueAcquireTexture', 'nvnCommandBufferInitialize')}
        cls.has_status = not BASELINE and (EXP / PATCH).is_file()
        if cls.has_status:
            ActualQueueFixture.apply(PATCH)
        source = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        cls.actual = source
        (cls.work / 'normal_queue_functions.inc').write_text(QUEUE.queue_definitions(source))
        objects = (cls.source / 'dm/gfx/nvn/nvn_obj.h').read_text()
        end = objects.index('} cmd_t;')
        start = objects.rfind('typedef struct {', 0, end)
        end = objects.index('\n', end)
        (cls.work / 'normal_early_types.inc').write_text(objects[start:end] + '\n')
        functions = ''
        private = 'static NVNboolean botw_nvn_command_buffer_initialize_impl('
        if private in source:
            functions += QUEUE.definition(source, private) + '\n'
        functions += QUEUE.definition(source, 'NVN(nvnCommandBufferInitialize,') + '\n'
        (cls.work / 'normal_early_functions.inc').write_text(functions)
        common = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                  '-fno-sanitize-recover=all', '-I', str(cls.work),
                  '-I', str(cls.source / 'dm/gfx/nvn'),
                  '-I', str(cls.source / 'dm/gfx/nvn/include'),
                  '-I', str(EXP / 'adapter'), '-I', str(EXP / 'tests')]
        policy = cls.work / 'early_policy.o'
        run = subprocess.run(common + ['-Dwrite=owned_early_status_write', '-c',
                                      str(EXP / 'adapter/whole_policy.c'), '-o', str(policy)],
                             capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)
        cls.binary = cls.work / 'early_probe'
        run = subprocess.run(common + ['-DBOTW_HAS_QUEUE_ACQUIRE=1',
                                      str(EXP / 'tests/whole_early_graphics_status_probe.c'),
                                      str(policy), '-o', str(cls.binary)], capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)

    def status_case(self, mode, *results):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout, '')
        baseline = '' if self.has_status else 'actual unpatched APIs complete, but no status: '
        self.assertEqual(run.stderr, ''.join(
            '[botw whole-host] api=' + api + ' result=' + outcome + '\n'
            for api, outcome in results), baseline)

    def test_queue_enum_zero_reports_ok_after_original_window_sequence(self):
        self.status_case('queue', ('NvnQueueAcquireTexture', 'ok'))

    def test_queue_native_error_enum_one_reports_error_without_window_mutation(self):
        self.status_case('count-zero', ('NvnQueueAcquireTexture', 'error'))

    def test_actual_command_buffer_boolean_one_reports_ok_after_cmda_boundary(self):
        self.status_case('initialize', ('NvnCommandBufferInitialize', 'ok'))

    def test_repeated_real_queue_success_and_failure_each_log_once(self):
        self.status_case('queue-once', ('NvnQueueAcquireTexture', 'ok'),
                         ('NvnQueueAcquireTexture', 'error'))

    def test_repeated_real_command_initialization_logs_only_one_ok(self):
        self.status_case('initialize-once', ('NvnCommandBufferInitialize', 'ok'))

    def test_original_complete_bodies_are_preserved_and_wrappers_call_once(self):
        private_names = {
            'nvnQueueAcquireTexture': 'static NVNqueueAcquireTextureResult botw_nvn_queue_acquire_texture_impl(',
            'nvnCommandBufferInitialize': 'static NVNboolean botw_nvn_command_buffer_initialize_impl('}
        for name, prefix in private_names.items():
            original = self.original_entries[name]
            if self.has_status:
                actual = QUEUE.definition(self.actual, prefix)
                self.assertEqual(actual[actual.index('{'):], original[original.index('{'):])
                wrapper = QUEUE.definition(self.actual, 'NVN(' + name + ',')
                self.assertEqual(wrapper.count(prefix.split(' ')[-1]), 1)
            else:
                self.assertEqual(QUEUE.definition(self.actual, 'NVN(' + name + ','), original)


if __name__ == '__main__':
    unittest.main()
