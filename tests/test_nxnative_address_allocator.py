"""Reuse the complete owned constructor-forwarding probe for the current ABI."""
from pathlib import Path
import shutil
import subprocess
import tempfile

import test_botw_whole_stack_allocator as reused

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'


class NxnativeAddressAllocator(reused.WholeStackAllocator):
    # Private source inspection is recorded separately on the actual local SDK.
    # All CI cases below use owned data and an owned callback, never Guest code.
    test_private_fixed_sdk_constructor_span_matches_original_static_contract = None
    test_exact_real_force_name_title_reason_and_unique_sdk_resolution = None

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nxnative-address-constructor-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        stage = work / 'source'
        for name in ('dm/host/hle_os.c', 'dm/host/dm.h', 'dm/host/hle.h', 'dm/ios/Sources/main.m'):
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(COMPONENT / 'upstream' / name, destination)
        patch = COMPONENT / 'patches/base-v0-address-allocator.patch'
        for source in (COMPONENT / 'patches/normal-entry.patch', patch):
            result = subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(source)],
                                    cwd=stage, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        text = (stage / 'dm/host/hle_os.c').read_text()
        start = text.index('/* BASE_V0_STACK_ALLOCATOR_BEGIN */')
        block = text[start:text.index('/* BASE_V0_STACK_ALLOCATOR_END */', start)]
        (work / 'normal_stack_allocator.inc').write_text(block)
        header = COMPONENT / 'adapter/base_v0_stack_allocator.h'
        shutil.copyfile(header, work / 'base_v0_stack_allocator.h')
        (stage / 'dm/host/game_profile.h').write_text('/* Owned metadata only. */\n')
        probe = (ROOT / 'experiments/botw_upstream_host/tests/whole_stack_allocator_probe.c').read_text()
        assert probe.count('entry->titles==DM_TM_BOTW&&') == 2
        probe = probe.replace('entry->titles==DM_TM_BOTW&&', '')
        changes = (
            ('static unsigned resolves,queries,original_calls,generic_resolves;',
             'static unsigned resolves,queries,original_calls,generic_resolves;\nstatic unsigned first_query=14;'),
            ('assert(id==14||id==15);', 'assert(id==first_query||id==first_query+1);'),
            ('assert(id==14+(queries%2));', 'assert(id==first_query+(queries%2));'),
            ('id==14?host_base:host_size', 'id==first_query?host_base:host_size'),
            ('else if(!strcmp(argv[1],"passthrough"))passthrough();',
             'else if(!strcmp(argv[1],"alias")){ first_query=2;\n'
             '    invoke(UINT64_C(0x08000000),UINT64_C(0x1000000000),UINT64_C(0x4000));\n'
             '    assert(captured_start==host_base&&captured_end==host_base+host_size-0x4000);\n'
             '    assert(captured_guard==0x4000&&queries==2&&resolves==1&&original_calls==1);\n'
             '}else if(!strcmp(argv[1],"passthrough"))passthrough();'),
        )
        for before, after in changes:
            assert probe.count(before) == 1, before
            probe = probe.replace(before, after)
        (work / 'probe.c').write_text(probe)
        ignore = work / 'metadata-only.ignore'
        ignore.write_text('global:hle_force_*\n')
        cls.binary = work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-DNXNATIVE_NORMAL_POLICY=1',
                   '-Wno-unused-function', '-pthread', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-fsanitize-ignorelist=' + str(ignore),
                   '-I', str(stage / 'dm/host'), '-I', str(COMPONENT / 'adapter'),
                   '-I', str(work), str(work / 'probe.c'), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_exact_real_alias_triplet_uses_retained_alias_region(self):
        self.case('alias')

    def test_actual_three_field_registry_resolves_only_the_fixed_sdk_original(self):
        self.case('registry')
