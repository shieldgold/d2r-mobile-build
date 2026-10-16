"""Fixed heap thunks and real owned pthread/allocator CPU boundaries only."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_HEAP_BASELINE') == '1'


class WholeHeapProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-heap-cpu-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream/dm/host', cls.source / 'dm/host')
        if not BASELINE:
            subprocess.run(['patch', '-p1', '-i', str(EXP / 'patches/base-v0-heap.patch')],
                           cwd=cls.source, check=True, capture_output=True, text=True)
        svc = (cls.source / 'dm/host/hle_svc.c').read_text()
        a = svc.index('case 0x01: {') + len('case 0x01: {')
        b = svc.index('case 0x02:', a)
        body = svc[a:b].rsplit('}', 1)[0]
        (cls.work / 'set_heap_entries.inc').write_text(body)
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-format', '-Wno-unused-function', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.source / 'dm/host'), '-I', str(EXP / 'adapter'),
                   '-I', str(cls.work), str(EXP / 'tests/base_v0_heap_probe.c'), '-o', str(cls.binary)]
        if not BASELINE:
            command += ['-DBOTW_BASE_V0_HEAP_ADAPTED=1', str(EXP / 'adapter/base_v0_heap.c')]
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def case(self, mode):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_actual_guest_thread_initializer_publishes_both_capacity_slots(self):
        self.case('threads')

    def test_actual_svc_mprotect_failure_preserves_previous_accounting(self):
        self.case('mprotect-failure')

    @unittest.skipIf(BASELINE, 'baseline has no bounded provider')
    def test_shared_original_planner_obeys_both_limits_and_is_transactional(self):
        self.case('plan')

    @unittest.skipIf(BASELINE, 'baseline has no bounded provider')
    def test_unknown_live_query_blocks_before_slot_publication(self):
        self.case('query-failure')

    @unittest.skipIf(BASELINE, 'baseline has no bounded provider')
    def test_real_pthread_key_failure_restores_first_slot_and_blocks_entry(self):
        self.case('key-failure')

    @unittest.skipIf(BASELINE, 'baseline has no bounded provider')
    def test_svc_success_records_real_accepted_heap_before_main_check(self):
        self.case('mprotect-success')

    @unittest.skipIf(BASELINE, 'baseline has no bounded provider')
    def test_fresh_live_limit_decline_blocks_initial_sizing(self):
        self.case('live-decline')

    @unittest.skipIf(BASELINE, 'baseline has no bounded provider')
    def test_svc_wrong_initial_request_and_unaligned_size_are_rejected(self):
        self.case('bad-request')

    @unittest.skipIf(BASELINE, 'baseline has no source provenance')
    def test_planner_body_is_exact_original_normal_host_algorithm(self):
        source = (ROOT / 'native/botw_host/runtime.c').read_text()
        start = source.index('int botw_host_heap_policy_plan(')
        end = source.index('\nstatic uint32_t heap_policy_word', start)
        original = source[start:end]
        copied = (EXP / 'adapter/base_v0_heap.c').read_text()
        start = copied.index('int botw_base_v0_heap_plan(')
        end = copied.index('\n/* Provider state', start)
        copied = copied[start:end].replace('botw_base_v0_heap_plan', 'botw_host_heap_policy_plan')
        self.assertEqual(copied.rstrip(), original.rstrip())
        evidence = json.loads((EXP / 'adapter/base_v0_heap.json').read_text())
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), evidence['planner_body_sha256'])

    @unittest.skipIf(BASELINE, 'baseline has no static profile admission')
    def test_actual_disk_guest_unwinds_v4_then_both_exact_heap_thunks(self):
        from scripts.botw_native import heap_policy, thread_stack
        directory = ROOT / 'outputs/botw-native-host39-bounded-pthread-stack-v4'
        if not (directory / 'report.json').exists():
            self.skipTest('private fixed Guest artifact unavailable')
        report = json.loads((directory / 'report.json').read_text())
        row = next(item for item in report['rewrites'] if item['name'] == 'main')
        evidence = json.loads((EXP / 'adapter/base_v0_heap.json').read_text())
        self.assertEqual(evidence['heap_manifest'], row['heap_adaptation'])
        self.assertEqual(evidence['guest_final_image_sha256'], row['image_sha256'])
        image = (directory / 'rewrite/main.patched.bin').read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(), row['image_sha256'])
        no_stack = thread_stack.verify(image, row['thread_stack_adaptation'])
        no_heap = heap_policy.verify(no_stack, row['heap_adaptation'])
        self.assertEqual(hashlib.sha256(no_heap).hexdigest(), row['host39_image_sha256'])
        self.assertEqual(row['heap_adaptation']['slots'], {'heap': 'KRES0', 'root': 'KRES1'})


if __name__ == '__main__':
    unittest.main()
