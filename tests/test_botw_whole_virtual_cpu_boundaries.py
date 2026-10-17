"""Actual HLE CPU boundaries over the complete owned virtual-pool fixture."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
SPEC = importlib.util.spec_from_file_location('whole_virtual_cpu_fixture', ROOT / 'tests/test_botw_whole_virtual_pools.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
BASELINE = os.environ.get('BOTW_WHOLE_VIRTUAL_CPU_BASELINE') == '1'


@unittest.skipUnless(sys.platform == 'darwin', 'requires actual Darwin VM and Objective-C')
class WholeVirtualCpuBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FIXTURE.WholeVirtualPools.setUpClass.__func__(cls)
        has_guard = 'static int pool_cpu_kind_supported(' in (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        if has_guard == BASELINE:
            subprocess.run(['patch', '--fuzz=0', '-p1', *(['-R'] if BASELINE else []),
                            '-i', str(EXP / 'patches/virtual-pool-cpu-boundaries.patch')],
                           cwd=cls.source, check=True, capture_output=True)
        hle = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        entries = ''
        if not BASELINE:
            for name in ('read_texels', 'write_texels', 'flush_texels'):
                body = FIXTURE.BACKING.function(hle, 'static void ' + name + '(')
                assert body.count('pool_texture_require_cpu(o);') == 1
                assert 'POOL_CAPTURE();pool_texture_require_cpu(o);' in body
            for name in ('static int pool_cpu_kind_supported(', 'static void pool_texture_require_cpu('):
                entries += FIXTURE.BACKING.function(hle, name) + '\n'
        support = ''
        for prefix in ('#define CB(c)', 'static struct { int on;', 'static pthread_mutex_t g_cs_mu'):
            lines = [line for line in hle.splitlines() if line.startswith(prefix)]
            assert len(lines) == 1
            support += lines[0] + '\n'
        support += FIXTURE.BACKING.function(hle, 'static int cs_on(') + '\n'
        (cls.work / 'command_boundary_support.inc').write_text(support)
        for name in ('nvnTexturePoolInitialize', 'nvnSamplerPoolInitialize', 'nvnEventInitialize',
                     'nvnCommandBufferAddCommandMemory'):
            entries += FIXTURE.BACKING.function(hle, 'NVN(' + name + ',') + '\n'
        (cls.work / 'cpu_boundary_entries.inc').write_text(entries)
        virtual = (EXP / 'tests/whole_virtual_pools_probe.m').read_text()
        assert virtual.count('int main(') == 1
        (cls.work / 'virtual_fixture.inc').write_text(virtual.replace('int main(', 'static int virtual_fixture_main(', 1))
        cls.binary = cls.work / 'cpu-boundaries'
        command = ['xcrun', 'clang', '-std=gnu11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                   '-Wno-format', '-pthread', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.source / 'dm/gfx/nvn'), '-I', str(cls.source / 'dm/gfx/nvn/include'),
                   '-I', str(EXP / 'adapter'), '-I', str(cls.work),
                   str(EXP / 'tests/whole_virtual_cpu_boundaries_probe.m'),
                   str(cls.source / 'dm/gfx/nvn/nvn_vpool.c'),
                   str(EXP / 'adapter/whole_pool_backing_memory.m'),
                   str(EXP / 'adapter/whole_pool_backing_gate.c'), str(EXP / 'adapter/whole_policy.c'),
                   '-framework', 'Foundation', '-framework', 'Metal', '-o', str(cls.binary)]
        if BASELINE:
            command.append('-DWHOLE_VIRTUAL_CPU_BASELINE=1')
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, 'WHOLE_VIRTUAL_CPU_BOUNDARIES_OK\n')

    def test_virtual_descriptor_pool_outputs_are_untouched(self): self.case('virtual-descriptors')
    def test_physical_descriptor_pool_outputs_are_untouched(self): self.case('physical-descriptors')
    def test_virtual_event_output_is_untouched(self): self.case('virtual-event')
    def test_physical_event_output_is_untouched(self): self.case('physical-event')
    def test_ordinary_descriptor_and_event_behavior_is_retained(self): self.case('ordinary')
    def test_virtual_cpu_texture_gate_stops_before_token_access(self): self.case('virtual-texture')
    def test_physical_cpu_texture_gate_stops_before_backing_access(self): self.case('physical-texture')
    def test_virtual_command_memory_stops_before_segment_and_audit_spies(self): self.case('virtual-command')
    def test_physical_command_memory_stops_before_segment_and_audit_spies(self): self.case('physical-command')
    def test_ordinary_command_memory_passes_the_original_pointer_and_offset(self): self.case('ordinary-command')
    def test_null_pool_and_short_command_memory_keep_the_host_fallback(self): self.case('fallback-command')


if __name__ == '__main__': unittest.main()
