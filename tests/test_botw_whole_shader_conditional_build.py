"""Conditional candidates are fresh CPU translations, never facts or GPU runs."""
from pathlib import Path
import hashlib
import copy
import json
import os
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock
import types

import test_botw_whole_shader_v8 as v8

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
SCRIPT = EXP / 'adapter/shader_conditional_build.py'
BASELINE = os.environ.get('BOTW_CONDITIONAL_BASELINE') == '1'
H = lambda data: hashlib.sha256(data).hexdigest()


class WholeShaderConditionalBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-owned-conditional-stage-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.stage = Path(cls.temp.name) / 'toolchain'
        cls.source = v8.load('conditional_source_ports', EXP / 'adapter/shader_source_build.py')
        cls.source.stage_source_toolchain(cls.stage)
        cls.tool = v8.load('conditional_owned_original_tool', cls.stage / 'dm/gfx/tools/botw_shader_build.py')
        cls.extract = v8.load('conditional_owned_original_extract', cls.stage / 'dm/gfx/tools/botw_shader_extract.py')
        if not BASELINE:
            cls.adapter = v8.load('conditional_adapter_test', SCRIPT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='botw-owned-conditional-input-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / 'inputs'; self.inputs.mkdir()
        self.caps = self.root / 'captures'; self.caps.mkdir()
        self.out = self.root / 'publication'
        control, code = v8.owned()
        fields = self.tool.parse_control(control)
        digest = self.tool.capture_digest(fields, control, code)
        self.key = digest[:16]
        self.extract.write_capture(str(self.caps), self.key, digest, fields, control, code)
        c1 = code[fields['c1_offset']:]
        row = {'stage': 'vertex', 'container': 'owned EXIT fixture; no game content'}
        for kind, data, suffix in [('control', control, '.control'), ('code', code, '.nvn'),
                                   ('reflection', b'{}', '.reflection.json'), ('c1', c1, '.cbuf1')]:
            row[kind + '_sha256'] = H(data)
            row[kind + '_file'] = H(data) + suffix
            (self.inputs / row[kind + '_file']).write_bytes(data)
        row.update(c1_begin=fields['c1_offset'], c1_end=len(code), c1_bytes=len(c1))
        self.manifest = self.inputs / 'inputs.json'
        self.manifest.write_text(json.dumps({'schema': 1, 'records': [row]}) + '\n')
        self.request = self.root / 'request.json'
        self.request.write_text(json.dumps({'schema': 'botw-shader-conditional-candidates-v1',
            'profile': 'botw-switch-base-v0', 'keys': [self.key]}) + '\n')

    def actual(self):
        capture = self.tool.load_capture(str(self.caps), self.key)
        result = {'key': self.key, 'stage': 'vs', 'notes': [], 'images': [], 'sbufs': [], 'textures': [], 'cbufs': []}
        return capture, result, 'vertex void main0() {}\n'

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_original_analysis_rejects_all_unknown_driver_reads(self):
        capture, result, msl = self.actual()
        for bank, location in ((0, 0x610), (2, 0x160)):
            source = 'struct spvDescriptorSetBuffer0 {\nconstant float4* vp_c%d [[id(0)]];\n};\n' % bank
            source += 'vertex void main0() { auto x = (*vp_c%d)._m0[%d]; }\n' % (bank, location // 16)
            with self.subTest(bank=bank), self.assertRaises(ValueError):
                self.adapter.assess_mode(self.tool, capture, result, source, (0, 0))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_fresh_query_notes_images_storage_and_attributes_reject(self):
        capture, result, msl = self.actual()
        for table in ('notes', 'images', 'sbufs'):
            bad = copy.deepcopy(result); bad[table] = ['actual unknown query or resource']
            with self.subTest(table=table), self.assertRaises(ValueError):
                self.adapter.assess_mode(self.tool, capture, bad, msl, (0, 0))
        source = 'vertex void main0(float4 in [[attribute(0)]]) {}\n'
        with self.assertRaises(ValueError): self.adapter.assess_mode(self.tool, capture, result, source, (0, 0))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_only_fixed_texture32_offsets_and_exact_c2_encoded_types_pass(self):
        capture, result, _ = self.actual()
        record = {'binding': 0, 'type': 3, 'format': 'Unknown', 'cbuf_slot': 2,
                  'handle': 8, 'array': 1, 'separate': False, 'flags': 0}
        source = 'struct spvDescriptorSetBuffer0 {\ntexture2d<float> vp_t_cb2_8 [[id(0)]];\n};\nvertex void main0() {}\n'
        result['textures'] = [record]
        request, bindings, _, _ = self.adapter.assess_mode(self.tool, capture, result, source, (0, 0))
        self.assertEqual(request.textures[8], {'slot': 0, 'type': '2d'})
        self.assertEqual(bindings[0]['reg'], 0)
        for field, value in (('handle', 9), ('handle', 72), ('handle', 0x10000008),
                             ('cbuf_slot', -2), ('cbuf_slot', 3), ('array', 2),
                             ('separate', True), ('type', 4), ('type', 3 | 0x400)):
            bad = copy.deepcopy(result); bad['textures'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.adapter.assess_mode(self.tool, capture, bad, source, (0, 0))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_wrong_capture_result_entry_and_resource_arrays_reject(self):
        capture, result, source = self.actual()
        for field, value in (('key', '0' * 16), ('stage', 'ps'), ('notes', None)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.adapter.assess_mode(self.tool, capture, dict(result, **{field: value}), source, (0, 0))
        for text in ('fragment void main0() {}', 'vertex void main0() {} vertex void extra() {}',
                     'struct spvDescriptorSetBuffer0 {\narray<texture2d<float>, 2> vp_t_tcb_8 [[id(0)]];\n};\nvertex void main0() {}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.adapter.assess_mode(self.tool, capture, result, text, (0, 0))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_regular_uniform_member_array_is_not_a_resource_binding_array(self):
        capture, result, _ = self.actual()
        result['cbufs'] = [{'binding': 0, 'slot': 3}]
        source = 'struct constant_data { array<float4, 4> _m0; };\n'
        source += 'struct spvDescriptorSetBuffer0 {\nconstant constant_data* vp_c3 [[id(0)]];\n};\nvertex void main0() {}\n'
        _, bindings, _, _ = self.adapter.assess_mode(self.tool, capture, result, source, (0, 0))
        self.assertEqual((bindings[0]['kind'], bindings[0]['reg']), ('cbuf', 0))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_original_extractor_uses_only_the_verified_private_builder_module(self):
        foreign = types.ModuleType('botw_shader_build')
        with mock.patch.dict(sys.modules, {'botw_shader_build': foreign}):
            extract = self.source.load(self.stage / 'dm/gfx/tools/botw_shader_extract.py', 'owned_cache_probe')
            actual = Path(extract.bsb.__file__)
            self.assertEqual(actual.resolve(), (self.stage / 'dm/gfx/tools/botw_shader_build.py').resolve())
            self.assertEqual(H(actual.read_bytes()), self.source.IMAGE_AFTER)
            self.assertIs(sys.modules['botw_shader_build'], foreign)

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_candidate_request_has_no_approval_or_duplicate_key_escape(self):
        original = json.loads(self.request.read_text())
        for value in (dict(original, approved=True), dict(original, keys=[self.key, self.key]),
                      dict(original, keys=['../bad']), dict(original, profile='unknown')):
            self.request.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError): self.adapter.requested_keys(self.request)

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_real_static_c1_and_source_bytes_are_required_before_translation(self):
        capture, _, _ = self.actual()
        manifest = json.loads(self.manifest.read_text())
        manifest['records'][0]['c1_bytes'] -= 1; self.manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'C1'):
            self.adapter.bind_static_inputs(self.tool, self.extract, self.manifest, {self.key: capture})

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_logical_job_digest_is_path_independent_and_binds_actual_code(self):
        capture, _, _ = self.actual()
        job = {'key': self.key, 'stage': 'vs', 'code': '/private/temp/owned', 'tex': {}, 'img': {}, 'debug': False}
        normalized = self.adapter.logical_job(job, capture)
        changed = self.adapter.logical_job(dict(job, code='/another/temp/owned'), capture)
        self.assertEqual(normalized, changed)
        self.assertEqual(normalized['code_sha256'], H(capture.translator_code))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_fixed_format_bytes_and_implicit_minus_one_bank_are_exact(self):
        raw, profile = self.adapter.format_contract()
        self.assertEqual(H(raw), '3f46576221f48db69abb5b553529fea2dd5f9a2ac1d14a50d5e7c57a499a2865')
        self.assertEqual(set(profile['fragment_output_classes']), {'37', '41', '46', '154'})
        capture, result, _ = self.actual()
        result['textures'] = [{'binding': 0, 'type': 3, 'format': 'Unknown', 'cbuf_slot': -1,
                               'handle': 8, 'array': 1, 'separate': False, 'flags': 0}]
        source = 'struct spvDescriptorSetBuffer0 {\ntexture2d<float> vp_t_tcb_8 [[id(0)]];\n};\nvertex void main0() {}\n'
        request, bindings, _, _ = self.adapter.assess_mode(self.tool, capture, result, source, (0, 1))
        self.assertEqual(request.textures[8]['slot'], 0)
        self.assertEqual(bindings[0]['reg'], 0)

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_fragment_output_requires_only_zero_and_a_real_float_type(self):
        control, code = v8.owned(stage=1)
        fields = self.tool.parse_control(control); sha = self.tool.capture_digest(fields, control, code)
        capture = self.tool.Capture('owned', sha[:16], sha, fields, control, code)
        result = {'key': capture.key, 'stage': 'ps', 'notes': [], 'images': [], 'sbufs': [], 'textures': [], 'cbufs': []}
        source = 'struct main0_out { float4 value [[color(0)]]; };\nfragment main0_out main0() {return {};}\n'
        request, _, _, _ = self.adapter.assess_mode(self.tool, capture, result, source, (1, 1))
        self.assertEqual(request.outputs, {0: 'float'})
        for changed in (source.replace('color(0)', 'color(1)'), source.replace('float4', 'uint4'),
                        source.replace('value [[color(0)]];', 'value [[color(0)]]; float4 other [[color(1)]];')):
            with self.subTest(source=changed), self.assertRaises(ValueError):
                self.adapter.assess_mode(self.tool, capture, result, changed, (1, 1))

    @unittest.skipIf(BASELINE, 'baseline has no conditional admission')
    def test_original_bnsh_shared_variation_label_keeps_both_stage_identities(self):
        data = bytearray(0x2000); data[0x60:0x64] = b'grsc'
        struct.pack_into('<IQ', data, 0x7c, 1, 0x100)
        struct.pack_into('<Q', data, 0x110, 0x200)
        captures = {}; rows = []
        slots = list(self.extract.BNSH_SLOTS)
        for stage, slot, descriptor, control_at, code_at in [(0, 'vs', 0x300, 0x400, 0x1400),
                                                            (1, 'ps', 0x340, 0xc00, 0x1600)]:
            control, code = v8.owned(stage=stage)
            struct.pack_into('<Q', data, 0x208 + slots.index(slot) * 8, descriptor)
            struct.pack_into('<QQII', data, descriptor + 8, control_at, code_at, len(code), len(control))
            data[control_at:control_at + len(control)] = control; data[code_at:code_at + len(code)] = code
            fields = self.tool.parse_control(control); sha = self.tool.capture_digest(fields, control, code)
            captures[sha[:16]] = self.tool.Capture('owned', sha[:16], sha, fields, control, code)
            rows.append({'key': sha[:16], 'sha256': sha, 'stage': stage, 'control_version': 8})
        raw = bytes(data); source_sha = H(raw); path = self.root / (source_sha + '.bnsh'); path.write_bytes(raw)
        label = path.name + '#BNSH@0x0#var0'
        for row in rows: row.update(source_sha256=source_sha, container='bnsh', variation=label)
        manifest = self.root / 'static-inputs.json'
        manifest.write_text(json.dumps({'schema': 'botw-static-base-v0-combined-shader-inputs-v1',
            'inputs': [{'path': str(path), 'sha256': source_sha, 'bytes': len(raw), 'kind': 'bnsh'}],
            'occurrences': rows}))
        evidence = self.adapter.bind_static_inputs(self.tool, self.extract, manifest, captures)
        self.assertEqual(set(evidence['sources']), set(captures))
        self.assertEqual(len(evidence['verified_pairs']), 1)

    def test_owned_exit_complete_real_cli_four_modes_without_facts(self):
        dotnet = ROOT / '.tools/dotnet-10.0.402/dotnet'
        xlate = ROOT / '.tools/botw-ryujinx-xlate-21f/dmsh_xlate.dll'
        cross = ROOT / '.tools/spirv-cross-65d7393-build/spirv-cross'
        if not all(p.is_file() for p in (dotnet, xlate, cross)):
            self.skipTest('actual pinned CPU translator tools unavailable')
        before = {str(p): H(p.read_bytes()) for directory in (self.inputs, self.caps)
                  for p in directory.iterdir()}
        if BASELINE:
            command = [sys.executable, str(self.stage / 'dm/gfx/tools/botw_shader_build.py'), 'build',
                       str(self.caps), '--out', str(self.out), '--xlate', str(xlate), '--spirv-cross', str(cross)]
        else:
            command = [sys.executable, str(SCRIPT), str(self.manifest), str(self.caps), str(self.request),
                       str(self.out), '--xlate', str(xlate), '--spirv-cross', str(cross)]
        env = dict(os.environ, DOTNET_ROOT=str(dotnet.parent), DOTNET_CLI_TELEMETRY_OPTOUT='1',
                   DOTNET_NOLOGO='1', PATH=str(dotnet.parent) + os.pathsep + os.environ.get('PATH', ''))
        run = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        publication = json.loads((self.out / 'conditional-publication.json').read_text())
        package = self.out / publication['package']
        manifest = json.loads((package / 'manifest-build.json').read_text())
        self.assertEqual(manifest['schema'], 'botw-shader-conditional-source-manifest-v1')
        self.assertEqual(len(manifest['results']), 4)
        modes = set()
        for result in manifest['results']:
            variant = result['variant_sha256']
            self.assertEqual(len(variant), 64)
            contract_raw = (package / (variant + '.contract.json')).read_bytes()
            self.assertEqual(H(contract_raw), variant)
            contract = json.loads(contract_raw)
            self.assertEqual(contract['capture']['sha256'], self.tool.load_capture(str(self.caps), self.key).sha256)
            self.assertEqual(contract['requirements']['attributes'], [])
            self.assertEqual(contract['requirements']['texture_queries'], [])
            self.assertIsNone(contract['requirements']['fragment_output0'])
            modes.add(tuple(contract['requirements']['device'].values()))
            self.assertIn('vertex ', (package / (variant + '.metal')).read_text())
            reflection = json.loads((package / (variant + '.json')).read_text())
            self.assertNotIn('facts_sha256', reflection)
            self.assertEqual(reflection['conditional_contract_sha256'], variant)
        self.assertEqual(modes, {(0, 0), (0, 1), (1, 0), (1, 1)})
        self.assertFalse(list(self.out.rglob('*.facts.json')))
        self.assertFalse(list(self.out.rglob('*.metallib')))
        self.assertEqual({p: H(Path(p).read_bytes()) for p in before}, before)
        proof = json.loads((self.out / 'admitted/subset-proof.json').read_text())
        self.assertEqual(len(proof['accepted']), 1)
        self.assertEqual(proof['rejected'], [])
        admission = json.loads((self.out / 'admitted/conditional-admission.json').read_text())
        self.assertEqual(len(admission['audit_files']), 11)
        for name, sha in admission['audit_files'].items():
            self.assertEqual(H((self.out / 'admitted' / name).read_bytes()), sha)
        for suffix in ('.json', '.control', '.code'):
            self.assertEqual((self.out / 'admitted/captures' / (self.key + suffix)).read_bytes(),
                             (self.caps / (self.key + suffix)).read_bytes())
        for mode in proof['accepted'][self.key]['modes']:
            for field, sha in (('res_file', 'res_sha256'), ('job_file', 'job_sha256')):
                self.assertEqual(H((self.out / 'admitted' / mode[field]).read_bytes()), mode[sha])
        self.assertEqual(publication['format_contract_sha256'], self.adapter.FORMAT_SHA256)
        if os.environ.get('BOTW_CONDITIONAL_SMOKE_RECEIPT'):
            Path(os.environ['BOTW_CONDITIONAL_SMOKE_RECEIPT']).write_text(json.dumps(
                {'command': command, 'exit': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
                 'publication': publication, 'manifest': manifest, 'proof': proof,
                 'owned_only': True, 'guest_or_gpu_executed': False}, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
