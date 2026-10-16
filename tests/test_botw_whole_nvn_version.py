"""Fixed base NVN version reports; complete original query, owned CPU only."""
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
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_NVN_VERSION_BASELINE') == '1'
PATCH = 'patches/base-v0-nvn-version.patch'


def query_parts(source):
    start = source.index('static const int g_devinfo[] = {')
    end = source.index('\n};', start) + len('\n};')
    body = source[end:source.index('// Optional debug labels', end)]
    return source[start:end], body


class WholeNvnVersion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-nvn-version-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream', cls.source)
        for patch in json.loads((EXP / 'SOURCE.lock.json').read_text())['patches']:
            if patch == PATCH:
                continue
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(EXP / patch)],
                                    cwd=cls.source, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        path = cls.source / 'dm/gfx/nvn/hle_nvn.c'
        original_table, original_body = query_parts(path.read_text())
        if not BASELINE:
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(EXP / PATCH)],
                                    cwd=cls.source, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        actual_table, actual_body = query_parts(path.read_text())
        if actual_body != original_body:
            raise AssertionError('original DeviceGetInteger implementation changed')
        (cls.work / 'original_device_info.inc').write_text(original_table.replace('g_devinfo', 'original_devinfo'))
        (cls.work / 'actual_device_integer.inc').write_text(actual_table + actual_body)
        prototype = [line for line in (cls.source / 'dm/gfx/nvn/nvn_protos.h').read_text().splitlines()
                     if '(*NVNT_nvnDeviceGetInteger)' in line]
        if len(prototype) != 1:
            raise AssertionError('missing unique actual DeviceGetInteger prototype')
        (cls.work / 'actual_device_integer_proto.inc').write_text(prototype[0] + '\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.work), '-I', str(cls.source / 'dm/gfx/nvn'),
                   '-I', str(ROOT / 'native/botw_graphics'),
                   str(EXP / 'tests/whole_nvn_version_probe.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15,
                                env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
                                     'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=0'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'WHOLE_NVN_VERSION_CPU_OK\n')
        self.assertEqual(result.stderr, '')

    def test_fixed_base_major_minor_and_null_device_query(self):
        self.case('version')

    def test_all_other_original_capability_reports_and_device_bytes_are_unchanged(self):
        self.case('capabilities')

    def test_last_known_and_unknown_signed_info_items_preserve_original_behavior(self):
        self.case('bounds')

    @unittest.skipIf(BASELINE, 'baseline does not implement the fixed base report')
    def test_original_query_body_and_pinned_static_version_contract(self):
        evidence = json.loads((EXP / 'adapter/base_v0_nvn_version.json').read_text())
        original = (EXP / 'upstream/dm/gfx/nvn/hle_nvn.c').read_text()
        table, body = query_parts(original)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), evidence['query_body_sha256'])
        directory = ROOT / 'outputs/botw-native-base/ExeFS'
        if not all((directory / name).is_file() for name in evidence['modules']):
            self.skipTest('private fixed base NSO files unavailable')
        sys.path.insert(0, str(ROOT))
        from scripts.botw_native.nso import initialized_image
        for name, module in evidence['modules'].items():
            raw = (directory / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), module['source_sha256'])
            image = initialized_image(raw, name)
            for span in module['spans'].values():
                start = int(span['offset'], 16)
                self.assertEqual(hashlib.sha256(image[start:start + span['bytes']]).hexdigest(), span['sha256'])


if __name__ == '__main__':
    unittest.main()
