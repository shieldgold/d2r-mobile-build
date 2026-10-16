"""Owned ctor forwarding; baseline replays fixed SDK args, never Guest code."""
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
PATCH = 'patches/base-v0-stack-allocator.patch'
BASELINE = os.environ.get('BOTW_WHOLE_STACK_ALLOCATOR_BASELINE') == '1'
HEADER = '''#pragma once
#include <stdint.h>
#define BOTW_BASE_V0_STACK_ALLOCATOR_SYMBOL "_ZN2nn2os6detail21AddressSpaceAllocatorC1Emmm"
#define BOTW_BASE_V0_STACK_ALLOCATOR_WHY "base-v0 SDK stack allocator uses the retained host stack region"
typedef void *(*botw_base_v0_stack_constructor)(void *self,uint64_t start,uint64_t end,uint64_t guard);
'''


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'real Mach-O HLE registry')
class WholeStackAllocator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-stack-allocator-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream', cls.source)
        patches = json.loads((EXP / 'SOURCE.lock.json').read_text())['patches']
        for name in patches:
            cls.apply(name)
        if PATCH in patches:
            cls.apply(PATCH, reverse=True)
        cls.has_adapter = not BASELINE and (EXP / PATCH).is_file()
        if cls.has_adapter:
            cls.apply(PATCH)
        text = (cls.source / 'dm/host/hle_os.c').read_text()
        if cls.has_adapter:
            start = text.index('/* BASE_V0_STACK_ALLOCATOR_BEGIN */')
            end = text.index('/* BASE_V0_STACK_ALLOCATOR_END */', start)
            block = text[start:end]
            scopes = [line for line in text[:start].splitlines()
                      if line.startswith('#define HLE_FORCE_TITLES ')]
            if scopes != ['#define HLE_FORCE_TITLES DM_TM_BOTW']:
                raise AssertionError('actual ctor registration scope changed')
            block = ('#include "base_v0_stack_allocator.h"\n#undef HLE_FORCE_TITLES\n'
                     + scopes[0] + '\n' + block)
        else:
            block = ''
            # Exact approved ABI/constants only. The baseline has no adapter;
            # missing status/handler declarations are never its RED condition.
            (cls.work / 'base_v0_stack_allocator.h').write_text(HEADER)
        (cls.work / 'normal_stack_allocator.inc').write_text(block)
        ignore = cls.work / 'metadata-only.ignore'
        ignore.write_text('global:hle_force_*\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-fsanitize-ignorelist=' + str(ignore),
                   '-DBOTW_HAS_STACK_ALLOCATOR=' + str(int(cls.has_adapter)),
                   '-I', str(cls.source / 'dm/host'), '-I', str(EXP / 'adapter'),
                   '-I', str(cls.work),
                   str(EXP / 'tests/whole_stack_allocator_probe.c'), '-o', str(cls.binary)]
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

    def case(self, mode):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout, 'WHOLE_STACK_ALLOCATOR_CPU_OK\n')
        self.assertEqual(run.stderr, '')

    def test_exact_low_sdk_triplet_uses_retained_host_stack(self):
        self.case('exact')

    def test_unknown_wide_and_unknown_guard_are_original_passthrough_without_queries(self):
        self.case('passthrough')

    def test_exact_real_force_name_title_reason_and_unique_sdk_resolution(self):
        self.case('registry')

    def test_invalid_host_metadata_or_failed_getinfo_never_enters_original(self):
        for mode in ('base-zero', 'size-zero', 'base-unaligned', 'size-unaligned',
                     'overflow', 'too-small', 'equal-minimum', 'query14', 'query15'):
            with self.subTest(mode=mode):
                self.case(mode)

    def test_wrong_module_profile_or_missing_or_recursive_original_is_rejected(self):
        for mode in ('bad-count', 'bad-sdk-name', 'duplicate-sdk', 'missing-original', 'self-original'):
            with self.subTest(mode=mode):
                self.case(mode)

    def test_adapter_does_not_read_or_write_an_opaque_object(self):
        self.case('opaque')

    def test_original_ctor_page_formula_keeps_both_guards_inside_host_region(self):
        self.case('guard-proof')

    def test_private_fixed_sdk_constructor_span_matches_original_static_contract(self):
        sdk = ROOT / 'outputs/botw-native-base/ExeFS/sdk'
        python = ROOT / '.tools/botw-venv/bin/python'
        if not sdk.is_file() or not python.is_file():
            self.skipTest('private fixed SDK/NSO decoder unavailable; owned CPU behavior is separate')
        script = '''from pathlib import Path
import hashlib
from scripts.botw_native.nso import initialized_image
p=Path('outputs/botw-native-base/ExeFS/sdk');raw=p.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='0abff9d26d975cb17c24ddfecca488a4900e20ffd7ab9bb642e6df667dde9f31'
image=initialized_image(raw,'sdk')
assert hashlib.sha256(image[0x8a308:0x8a308+76]).hexdigest()=='4bf65a5c1b5ca447edcf2797129f7f8b5dbae283357e06776d4ae34924e7ee5b'
'''
        run = subprocess.run([str(python), '-c', script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
