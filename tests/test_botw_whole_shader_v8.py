"""Exact base-v0 v8 shader control; static inputs and owned EXIT CPU translation."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPERIMENT = ROOT / 'experiments/botw_upstream_host'
VENDOR = ROOT / 'native/botw_resources/shader_toolchain'
BASELINE = os.environ.get('BOTW_SHADER_V8_BASELINE') == '1'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def owned(version=8, stage=0):
    control = bytearray(2000 if version == 8 else 2120)
    for off, value in ((0, 0x98761234), (4, 1), (8, version), (12, 0x120), (16, 11),
                       (20, len(control)), (0x20, len(control)), (0x6f0, len(control)),
                       (0x700, 0x100), (0x704, 0x200), (0x708, 0x30), (0x714, stage)):
        struct.pack_into('<I', control, off, value)
    code = bytearray(0x200)
    struct.pack_into('<I', code, 0, 0x12345678)
    struct.pack_into('<I', code, 0x30, 1 | {0: 1, 1: 5, 2: 4}[stage] << 10)
    # Owned Maxwell scheduling word, EXIT, BRA-to-self, NOP (upstream test program).
    struct.pack_into('<4Q', code, 0x80, 0x001ffc00fd4007ef, 0xe30000000007000f,
                     0xe2400fffff87000f, 0x50b0000000070f00)
    code[0x100:] = bytes(range(256))
    return bytes(control), bytes(code)


class WholeShaderV8(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-v8-cpu-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.work = Path(cls.scratch.name)
        source = VENDOR
        if not BASELINE:
            cls.adapter = load('botw_whole_shader_v8_adapter', EXPERIMENT / 'adapter/shader_v8_control.py')
            source = cls.work / 'staged'
            cls.receipt = cls.adapter.stage_toolchain(source)
        cls.tool = load('botw_whole_shader_v8_build', source / 'dm/gfx/tools/botw_shader_build.py')
        cls.extract = load('botw_whole_shader_v8_extract', source / 'dm/gfx/tools/botw_shader_extract.py')

    def accepted(self, control):
        try:
            return self.tool.parse_control(control)
        except self.tool.CaptureError:
            return None

    def test_accepts_exact_v8_profile_without_changing_version_or_bytes(self):
        control, code = owned()
        fields = self.accepted(control)
        self.assertIsNotNone(fields, 'exact base-v0 control version 8 remains rejected')
        self.assertEqual(fields, {'version': 8, 'control_size': 2000, 'stage': 0, 'code_size': 512,
                                  'code_offset': 48, 'c1_offset': 256, 'c1_size': 256})
        self.tool.check_code(fields, code)
        self.assertEqual(control, owned()[0])

    def test_capture_preserves_exact_c1_sph_and_versioned_digest(self):
        control, code = owned()
        fields = self.accepted(control)
        self.assertIsNotNone(fields, 'v8 capture cannot be created')
        digest = self.tool.capture_digest(fields, control, code)
        capture = self.tool.Capture('owned', digest[:16], digest, fields, control, code)
        self.assertEqual(capture.c1, bytes(range(256)))
        self.assertEqual(capture.translator_code, code[0x30:])
        self.assertEqual(capture.fields['version'], 8)
        changed = bytearray(control); struct.pack_into('<I', changed, 8, 9)
        other = self.tool.parse_control(bytes(changed))
        self.assertNotEqual(digest, self.tool.capture_digest(other, bytes(changed), code))

    def test_native_field_profile_size_and_code_bounds_fail_closed(self):
        control, _ = owned()
        # The v8 profile is grounded in this fixed graphics source set only.
        for off, value in ((12, 0x121), (16, 12), (20, 2001), (0x20, 2001),
                           (0x6f0, 2001), (0x708, 0), (0x714, 5), (0x704, 0),
                           (0x700, 0x208), (0x700, 0x101)):
            with self.subTest(offset=off, value=value):
                changed = bytearray(control); struct.pack_into('<I', changed, off, value)
                self.assertIsNone(self.accepted(bytes(changed)))
        self.assertIsNone(self.accepted(control[:-1]))
        self.assertIsNone(self.accepted(control + b'\0'))

    def test_existing_v9_and_v14_control_paths_are_preserved(self):
        for version in (9, 14):
            control, code = owned(version)
            fields = self.tool.parse_control(control)
            self.assertEqual(fields['version'], version)
            self.tool.check_code(fields, code)

    def test_complete_original_bahs_extractor_admits_v8_without_relabel(self):
        control, code = owned()
        region = 0x800
        archive = bytearray(region + len(code)); archive[:4] = b'BAHS'
        struct.pack_into('<II', archive, 0x18, len(code), region)
        struct.pack_into('<IIIi', archive, 0x20, 0, 0, len(control), 16)
        archive[0x30:0x30 + len(control)] = control; archive[region:] = code
        stages, rejected = self.extract.bahs_stages(bytes(archive), 'owned-BAHS')
        self.assertEqual((len(stages), rejected), (1, 0))
        self.assertEqual(stages[0][2]['version'], 8)
        self.assertEqual(stages[0][3:], (control, code))

    @unittest.skipIf(BASELINE, 'baseline has no isolated v8 predicate')
    def test_runtime_profile_predicate_uses_owned_cpu_bytes(self):
        out = self.work / 'v8-profile-probe'
        run = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                              '-fsanitize=address,undefined', '-I', str(EXPERIMENT / 'adapter'),
                              str(EXPERIMENT / 'tests/shader_v8_control_probe.c'), '-o', str(out)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        run = subprocess.run([str(out)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('V8_PROFILE_CPU_OK', run.stdout)

    @unittest.skipIf(BASELINE, 'baseline has no staged source provenance')
    def test_staging_preserves_all_374_originals_and_records_patch_hashes(self):
        from scripts.botw_native import upstream_shaders
        self.assertEqual(upstream_shaders.verify_vendor()['verified_files'], 374)
        self.assertEqual(self.receipt['original_files_verified'], 374)
        self.assertEqual(set(self.receipt['changed_files']), {'dm/gfx/tools/botw_shader_build.py'})
        self.assertEqual(self.receipt['profile'], 'botw-base-v0-v8-2000-graphics')
        self.assertFalse(self.receipt['version_relabelled'])
        self.assertFalse(self.receipt['control_bytes_rewritten'])

    def test_all_fixed_static_sharcb_stages_match_exact_unmodified_control(self):
        from scripts.botw_native.resource_shaders import parse_sharcb_v9
        folder = ROOT / 'outputs/botw-resource-shaders-base-verified'
        paths = sorted(folder.glob('*.sharcb'))
        if not paths:
            self.skipTest('private static SHARCB set unavailable')
        accepted, total = 0, 0
        for path in paths:
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), path.stem)
            report = parse_sharcb_v9(data)
            for row in report['variations']:
                total += 1
                control = data[row['control_offset']:row['control_offset'] + row['control_bytes']]
                code = data[row['code_offset']:row['code_offset'] + row['code_bytes']]
                fields = self.accepted(control)
                if fields is None:
                    continue
                self.tool.check_code(fields, code)
                self.assertEqual(fields['version'], 8)
                self.assertEqual(fields['stage'], {'vertex': 0, 'fragment': 1, 'geometry': 2}[row['stage']])
                self.assertEqual((fields['code_size'], fields['c1_offset']), (row['code_bytes'], row['c1_begin']))
                self.assertEqual(hashlib.sha256(control).hexdigest(), row['control_sha256'])
                self.assertEqual(hashlib.sha256(code).hexdigest(), row['code_sha256'])
                accepted += 1
        self.assertGreater(total, 0)
        self.assertEqual(accepted, total, 'fixed static v8 stage admission remains blocked')

    def test_owned_exit_translates_with_real_original_ryujinx_and_no_query_defaults(self):
        dotnet = Path(os.environ.get('BOTW_V8_DOTNET', ROOT / '.tools/dotnet-10.0.402/dotnet'))
        xlate = Path(os.environ.get('BOTW_V8_XLATE', ROOT / '.tools/botw-ryujinx-xlate-21f/dmsh_xlate.dll'))
        cross = Path(os.environ.get('BOTW_V8_SPIRV_CROSS', ROOT / '.tools/spirv-cross-65d7393-build/spirv-cross'))
        if not all(p.is_file() for p in (dotnet, xlate, cross)):
            self.skipTest('real local dotnet/Ryujinx/SPIRV-Cross tools unavailable')
        control, code = owned(); fields = self.accepted(control)
        self.assertIsNotNone(fields, 'v8 owned EXIT cannot enter translation')
        digest = self.tool.capture_digest(fields, control, code)
        capture = self.tool.Capture('owned', digest[:16], digest, fields, control, code)
        env = os.environ.copy(); env.update(DOTNET_ROOT=str(dotnet.parent),
                                          DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
        check = subprocess.run([str(dotnet), str(xlate), '--source-sha'], env=env,
                               capture_output=True, text=True, timeout=30)
        tools = VENDOR / 'dm/gfx/tools/dmsh_xlate'
        source = (tools / 'Program.cs').read_bytes() + (tools / 'dmsh_xlate.csproj').read_bytes()
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(check.stdout.strip(), hashlib.sha256(source).hexdigest()[:16])
        source_path = self.work / 'owned-exit.code'; source_path.write_bytes(capture.translator_code)
        # This owned program has no resource instructions. No scene facts are invented.
        job = {'key': capture.key, 'stage': 'vs', 'code': str(source_path), 'c1': capture.c1.hex(),
               'tex': {}, 'img': {}, 'attr': {}, 'fout': {}, 'debug': False, 'reduced': False,
               'device_state': {'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_zero'}}
        jobs = self.work / 'owned-jobs.json'; jobs.write_text(json.dumps([job]))
        out = self.work / 'xlate'
        run = subprocess.run([str(dotnet), str(xlate), str(jobs), str(out)], env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        reflection = json.loads((out / (capture.key + '.res.json')).read_text())
        self.assertEqual(reflection['notes'], [])
        for name in ('textures', 'images', 'sbufs', 'cbufs'):
            self.assertEqual(reflection[name], [])
        spv = out / (capture.key + '.spv')
        self.assertEqual(struct.unpack_from('<I', spv.read_bytes())[0], 0x07230203)
        run = subprocess.run([str(cross), str(spv), '--msl', '--msl-version', '20300'],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('vertex ', run.stdout)


if __name__ == '__main__':
    unittest.main()
