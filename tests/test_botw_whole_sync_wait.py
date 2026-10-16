"""Base NVN52 fresh-sync result over actual complete patched native bodies."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/base-v0-sync-wait.patch'
BASELINE = os.environ.get('BOTW_WHOLE_SYNC_WAIT_BASELINE') == '1'


def definition(text, anchor):
    start = text.index(anchor)
    i = text.index('{', start)
    depth, quote = 0, None
    while i < len(text):
        c = text[i]
        if quote:
            if c == '\\':
                i += 2
                continue
            if c == quote:
                quote = None
        elif text.startswith('//', i):
            i = text.index('\n', i)
            continue
        elif text.startswith('/*', i):
            i = text.index('*/', i) + 2
            continue
        elif c in ('"', "'"):
            quote = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if not depth:
                return text[start:i + 1]
        i += 1
    raise AssertionError('actual NVN definition is incomplete')


class WholeSyncWait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-sync-wait-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream', cls.source)
        locked = json.loads((EXP / 'SOURCE.lock.json').read_text())['patches']
        cls.applied = []
        for name in locked:
            if name != PATCH:
                cls.apply(name)
        hle_path = cls.source / 'dm/gfx/nvn/hle_nvn.c'
        cls.original = hle_path.read_text()
        cls.has_patch = not BASELINE and (EXP / PATCH).is_file()
        if cls.has_patch:
            cls.apply(PATCH)
        cls.actual = hle_path.read_text()
        names = ('nvnSyncInitialize', 'nvnSyncFinalize', 'nvnQueueFenceSync', 'nvnSyncWait')
        (cls.work / 'normal_sync_functions.inc').write_text('\n'.join(
            definition(cls.actual, 'NVN(' + name + ',') for name in names) + '\n')
        lines = (cls.source / 'dm/gfx/nvn/nvn_obj.h').read_text().splitlines()
        types = [next(line for line in lines if line.startswith('#define MAGIC(')),
                 next(line for line in lines if line.startswith('#define FITS(')),
                 next(line for line in lines if '} sync_t;' in line)]
        (cls.work / 'normal_sync_types.inc').write_text('\n'.join(types) + '\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-I', str(cls.work),
                   '-I', str(cls.source / 'dm/gfx/nvn'),
                   '-I', str(cls.source / 'dm/gfx/nvn/include'),
                   str(EXP / 'tests/whole_sync_wait_probe.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def apply(cls, name):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(EXP / name)],
                                cwd=cls.source, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.applied.append(name)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        status = 'patched source: ' if self.has_patch else 'explicit unpatched baseline: '
        self.assertEqual(result.returncode, 0, status + result.stdout + result.stderr)

    def test_fresh_initialized_never_fenced_sync_returns_failed_without_platform_wait(self):
        self.case('fresh')

    def test_fenced_already_signaled_returns_enum_zero_after_one_nonblocking_wait(self):
        self.case('already')

    def test_fenced_condition_returns_enum_one_with_exact_timeout_and_callback_errno(self):
        self.case('condition')

    def test_fenced_timeout_returns_enum_two_with_exact_timeout_and_callback_errno(self):
        self.case('timeout')

    def test_repeated_waits_and_refence_keep_the_original_stateless_result_mapping(self):
        self.case('repeated')

    def test_all_other_locked_patches_are_applied_and_only_this_patch_is_appended(self):
        locked = json.loads((EXP / 'SOURCE.lock.json').read_text())['patches']
        expected = [name for name in locked if name != PATCH]
        if self.has_patch:
            expected.append(PATCH)
        self.assertEqual(self.applied, expected)

    def test_original_initialize_finalize_fence_and_complete_fenced_wait_body_are_unchanged(self):
        for name in ('nvnSyncInitialize', 'nvnSyncFinalize', 'nvnQueueFenceSync'):
            self.assertEqual(definition(self.actual, 'NVN(' + name + ','),
                             definition(self.original, 'NVN(' + name + ','))
        original = definition(self.original, 'NVN(nvnSyncWait,')
        actual = definition(self.actual, 'NVN(nvnSyncWait,')
        if self.has_patch:
            line = 'if (!o->fenced) return NVN_SYNC_WAIT_RESULT_FAILED;'
            self.assertEqual(actual.count(line), 1)
            actual = actual.replace(line, 'if (!o->fenced) return NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;', 1)
        self.assertEqual(actual, original)

    def test_optional_fixed_disk_nso_identity_and_all_static_contract_spans(self):
        module = ROOT / 'outputs/botw-native-base/ExeFS/subsdk0'
        if not module.is_file():
            self.skipTest('private fixed NSO is absent; owned CPU behavior is still mandatory')
        python = ROOT / '.tools/botw-venv/bin/python'
        program = r'''
from pathlib import Path
import hashlib,json,sys
from scripts.botw_native.nso import initialized_image,inspect_nso
module,profile=map(Path,sys.argv[1:])
data=module.read_bytes();contract=json.loads(profile.read_text())
assert contract['upstream_commit']=='21f026e0c7fd9fee575a1bfa8d925553bb4db88e'
assert hashlib.sha256(data).hexdigest()==contract['module']['source_sha256']
assert inspect_nso(data,'subsdk0')['build_id']==contract['module']['build_id']
image=initialized_image(data,'subsdk0')
assert len(contract['spans'])==6
for row in contract['spans'].values():
 start=int(row['offset'],16);span=image[start:start+row['bytes']]
 assert len(span)==row['bytes'] and hashlib.sha256(span).hexdigest()==row['sha256']
assert contract['contracts']['fresh_state']==0
assert contract['contracts']['fresh_wait_result']==3
assert contract['contracts']['normal_enums']=={'already_signaled':0,'condition_satisfied':1,'timeout_expired':2,'failed':3}
'''
        result = subprocess.run([str(python) if python.is_file() else sys.executable, '-c', program,
                                 str(module), str(EXP / 'adapter/base_v0_sync_wait.json')],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
