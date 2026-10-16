"""Owned-CPU TextureView ABI probes; no host, guest, VM inspection or GPU."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_BASE_V0_ABI_BASELINE') == '1'
APIS = ('SetDefaults', 'SetLevels', 'SetLayers', 'SetFormat', 'SetSwizzle',
        'SetDepthStencilMode', 'SetTarget', 'GetLevels', 'GetLayers',
        'GetFormat', 'GetSwizzle', 'GetDepthStencilMode', 'GetTarget', 'Compare')


class WholeBaseV0ABI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-base-v0-abi-')
        cls.addClassCleanup(cls.scratch.cleanup)
        work = Path(cls.scratch.name)
        cls.source = work / 'source'
        shutil.copytree(EXPERIMENT / 'upstream/dm/gfx/nvn', cls.source)
        if not BASELINE:
            # Patch a disposable source copy. The vendor originals stay untouched.
            stage = work / 'stage/dm/gfx'
            stage.mkdir(parents=True)
            shutil.move(str(cls.source), str(stage / 'nvn'))
            subprocess.run(['patch', '-p1', '-i', str(EXPERIMENT / 'patches/base-v0-abi.patch')],
                           cwd=work / 'stage', capture_output=True, text=True, check=True)
            cls.source = stage / 'nvn'
        cls.hle = (cls.source / 'hle_nvn.c').read_text()
        start = cls.hle.index('#define VW(v)')
        stop = cls.hle.index('NVN(nvnTextureGetViewOffset,', start)
        # These are actual upstream/patched production definitions, not test doubles.
        copy_view = re.search(r'^static void copy_view\(.*$', cls.hle, re.MULTILINE).group(0)
        (work / 'view_entries.inc').write_text(cls.hle[start:stop] + '\n' + copy_view + '\n')
        cls.executable = work / 'view-abi-probe'
        cmd = ['cc', '-std=gnu11', '-O0', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-fsanitize=address,undefined',
               '-I', str(cls.source / 'include'), '-I', str(cls.source),
               '-I', str(work), '-I', str(EXPERIMENT / 'adapter'),
               str(EXPERIMENT / 'tests/base_v0_abi_probe.c'), '-o', str(cls.executable)]
        if not BASELINE:
            cmd.extend(['-DBOTW_BASE_V0_ADAPTED=1', str(EXPERIMENT / 'adapter/base_v0_abi.c')])
        build = subprocess.run(cmd, capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def probe(self, mode):
        run = subprocess.run([str(self.executable), mode], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('BASE_V0_VIEW_OK ' + mode, run.stdout)

    def test_defaults_clear_exact_36_bytes(self):
        self.probe('defaults')

    def test_levels_set_native_byte_positions_and_preserve_other_bytes(self):
        self.probe('levels')

    def test_layers_keep_unsigned_16_bit_getter_contract(self):
        self.probe('layers')

    def test_format_set_flag_and_payload_at_native_offsets(self):
        self.probe('format')

    def test_swizzle_retains_four_native_32_bit_selectors(self):
        self.probe('swizzle')

    def test_depth_mode_retains_full_32_bits(self):
        self.probe('depth')

    def test_target_retains_full_32_bits(self):
        self.probe('target')

    @unittest.skipIf(BASELINE, 'baseline RED checks implemented upstream entry behavior')
    def test_all_14_entries_are_typed_and_share_the_consumer_schema(self):
        names = set(re.findall(r'NVN\(nvnTextureView(\w+),', self.hle))
        self.assertEqual(names, set(APIS))
        self.probe('schema')

    @unittest.skipIf(BASELINE, 'baseline lacks four TextureView accessors')
    def test_getters_write_unset_values_and_return_only_their_flag(self):
        self.probe('getters')

    @unittest.skipIf(BASELINE, 'baseline lacks TextureViewCompare')
    def test_compare_ignores_unset_fields_padding_and_upper_flag_bits(self):
        self.probe('compare')

    @unittest.skipIf(BASELINE, 'baseline lacks native schema')
    def test_recorded_payload_owns_full_native_view_copy(self):
        self.probe('payload')

    @unittest.skipIf(BASELINE, 'baseline lacks adapter')
    def test_unaligned_objects_use_exact_scalar_accesses(self):
        self.probe('unaligned')

    @unittest.skipIf(BASELINE, 'baseline lacks fixed-source evidence manifest')
    def test_fixed_nso_static_function_hashes_match_evidence(self):
        from scripts.botw_native.nso import initialized_image
        evidence = json.loads((EXPERIMENT / 'adapter/base_v0_abi.json').read_text())
        path = ROOT / 'outputs/botw-native-base/ExeFS/subsdk0'
        if not path.exists():
            self.skipTest('private fixed NSO unavailable; owned CPU tests remain active')
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), evidence['source']['nso_sha256'])
        image = initialized_image(data, 'subsdk0')
        self.assertEqual(set(evidence['functions']), {'TextureView' + name for name in APIS})
        for name, row in evidence['functions'].items():
            with self.subTest(function=name):
                raw = image[row['offset']:row['offset'] + row['bytes']]
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row['sha256'])


if __name__ == '__main__':
    unittest.main()
