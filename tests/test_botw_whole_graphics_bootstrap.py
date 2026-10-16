"""Three literal normal host checkpoints over the original owned CPU bodies."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
PATCH = 'patches/graphics-bootstrap-status.patch'
VERSION = 'patches/base-v0-nvn-version.patch'
BASELINE = os.environ.get('BOTW_WHOLE_GRAPHICS_BOOTSTRAP_BASELINE') == '1'


def definition(text, anchor):
    start = text.index(anchor)
    opening = text.index('{', start)
    depth = 0
    i = opening
    quote = None
    while i < len(text):
        char = text[i]
        if quote:
            if char == '\\':
                i += 2
                continue
            if char == quote:
                quote = None
        elif text.startswith('//', i):
            i = text.index('\n', i)
            continue
        elif text.startswith('/*', i):
            i = text.index('*/', i) + 2
            continue
        elif char in ('"', "'"):
            quote = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise AssertionError('unclosed actual normal function')


@unittest.skipUnless(shutil.which('clang'), 'actual owned C body compilation requires clang')
class WholeGraphicsBootstrap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-graphics-bootstrap-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream', cls.source)
        patches = json.loads((EXP / 'SOURCE.lock.json').read_text())['patches']
        for name in patches:
            if name in (PATCH, VERSION):
                continue
            cls.apply(name)
        cls.apply(VERSION)
        if not BASELINE:
            cls.apply(PATCH)
        nvn = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        graphics = (cls.source / 'dm/host/botw_graphics.c').read_text()
        actual = definition(graphics, 'HLE_FORCE("nvnBootstrapLoader",')
        actual += '\n' + definition(nvn, 'static void nvn_init_slots(void) {')
        actual += '\n' + definition(nvn, 'NVN(nvnDeviceInitialize,') + '\n'
        (cls.work / 'normal_graphics_functions.inc').write_text(actual)
        objects = (cls.source / 'dm/gfx/nvn/nvn_obj.h').read_text()
        start = objects.index('#define MAGIC(')
        end = objects.index('typedef struct { NVNdevice *dev;', start)
        (cls.work / 'normal_graphics_types.inc').write_text(objects[start:end])
        slot_type = next(line for line in nvn.splitlines() if line.endswith('} nvn_slot_t;'))
        (cls.work / 'normal_graphics_slot_type.inc').write_text(slot_type + '\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-I', str(cls.work),
                   '-I', str(cls.source / 'dm/gfx/nvn'),
                   '-I', str(cls.source / 'dm/gfx/nvn/include'),
                   str(EXP / 'tests/whole_graphics_bootstrap_probe.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def apply(cls, name):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(EXP / name)],
                                cwd=cls.source, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lookup_entry_precedes_original_call_and_preserves_return(self):
        self.case('bootstrap')

    def test_dispatch_ready_follows_actual_slot_publication_once(self):
        self.case('slots')

    def test_empty_owned_metadata_retains_original_complete_dispatch(self):
        self.case('slots-empty')

    def test_failed_dispatch_does_not_report_ready(self):
        self.case('slots-failed')

    def test_device_completion_preserves_real_layout_and_runs_after_unlock(self):
        self.case('device')

    def test_concurrent_lookup_entry_reports_only_once(self):
        self.case('bootstrap-concurrent')

    def test_concurrent_device_completion_reports_only_once(self):
        self.case('device-concurrent')


if __name__ == '__main__':
    unittest.main()
