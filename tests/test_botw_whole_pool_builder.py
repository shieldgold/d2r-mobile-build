"""Seven statically identified base-v0 pool-builder leaves; owned CPU only."""
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
BASELINE = os.environ.get('BOTW_WHOLE_POOL_BUILDER_BASELINE') == '1'
NAMES = ('SetDefaults', 'SetDevice', 'SetStorage', 'SetFlags', 'GetMemory', 'GetSize', 'GetFlags')


def function(source, start):
    a = source.index(start)
    b = source.index('{', a)
    depth, end = 1, b + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[a:end]


@unittest.skipUnless(sys.platform == 'darwin', 'requires real Darwin protected-copy APIs')
class WholePoolBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-pool-builder-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream/dm', cls.source / 'dm')
        patches = ['checked-dirty-write.patch']
        if not BASELINE:
            patches += ['base-v0-pool-builder.patch']
        for name in patches:
            subprocess.run(['patch', '-p1', '-i', str(EXP / 'patches' / name)], cwd=cls.source,
                           check=True, capture_output=True, text=True)
        crash = (cls.source / 'dm/host/crash.c').read_text()
        a = crash.index('#define MAX_FAULT_HOOK')
        b = crash.index('// The diagnostics module', a)
        (cls.work / 'normal_registry.inc').write_text(crash[a:b])
        source = (cls.source / 'dm/gfx/nvn/hle_nvn.c').read_text()
        actual, entries = [], []
        for name in NAMES:
            public = 'nvnMemoryPoolBuilder' + name
            start = 'NVN(' + public + ','
            if start in source:
                actual.append(function(source, start))
                entries.append('{"' + public + '",(void*)h_' + public + '},')
        (cls.work / 'builder_entries.inc').write_text('\n'.join(actual))
        (cls.work / 'builder_registry.inc').write_text('\n'.join(entries))
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-Wno-format', '-Wno-unused-function', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.source / 'dm/host'), '-I', str(cls.source / 'dm/gfx/nvn'),
                   '-I', str(cls.source / 'dm/gfx/nvn/include'), '-I', str(EXP / 'adapter'),
                   '-I', str(cls.work), str(EXP / 'tests/whole_pool_builder_probe.c'), '-o', str(cls.binary)]
        if not BASELINE:
            command += [str(EXP / 'adapter/whole_pool_builder.c')]
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def case(self, mode):
        run = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_defaults_preserve_device_tail_and_write_original_flags(self): self.case('defaults')
    def test_all_seven_actual_entries_and_real_getter_return_types(self): self.case('getters')
    def test_setters_preserve_other_bytes_and_opaque_64_bit_values(self): self.case('setters')
    def test_defaults_stop_at_original_twenty_byte_guard_boundary(self): self.case('guard')
    def test_defaults_do_not_read_inaccessible_device(self): self.case('device-unreadable')
    def test_setters_and_getters_touch_only_their_exact_field(self): self.case('field-guards')
    def test_readonly_destination_rejects_before_write_without_signal_fault(self): self.case('readonly')
    def test_noaccess_destination_rejects_without_signal_fault(self): self.case('noaccess')
    def test_partial_readonly_destination_is_checked_before_first_store(self): self.case('partial-readonly')
    def test_null_and_wrapping_input_fail_before_prep_or_memory_access(self): self.case('bounds')
    def test_real_checked_registry_failure_blocks_write(self): self.case('prep-denied')
    def test_unreadable_getter_input_leaves_failure_controlled(self): self.case('getter-unreadable')

    @unittest.skipIf(BASELINE, 'baseline has no source profile')
    def test_exact_static_leaf_and_reused_copy_provenance(self):
        evidence = json.loads((EXP / 'adapter/whole_pool_builder.json').read_text())
        header = ROOT / evidence['protected_copy']['path']
        self.assertEqual(hashlib.sha256(header.read_bytes()).hexdigest(), evidence['protected_copy']['sha256'])
        nso = ROOT / 'outputs/botw-native-base/ExeFS/subsdk0'
        if not nso.exists():
            self.skipTest('private fixed NSO unavailable')
        # The private artifact is static source material; no instruction executes.
        from scripts.botw_native.nso import initialized_image
        data = nso.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), evidence['source']['nso_sha256'])
        image = initialized_image(data, 'subsdk0')
        self.assertEqual(hashlib.sha256(image).hexdigest(), evidence['source']['initialized_image_sha256'])
        self.assertEqual(set(evidence['leaves']), set(NAMES))
        for name, row in evidence['leaves'].items():
            with self.subTest(leaf=name):
                self.assertEqual(hashlib.sha256(image[row['offset']:row['offset'] + row['bytes']]).hexdigest(), row['sha256'])


if __name__ == '__main__': unittest.main()
