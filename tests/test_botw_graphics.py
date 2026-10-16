"""Synthetic-only fixtures; no game bytecode or derived shader is checked in."""
import importlib.util
from pathlib import Path
import struct
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def synthetic_bnsh():
    data = bytearray(0x600)
    data[:8] = b'BNSH\0\0\0\0'
    struct.pack_into('<I', data, 8, 0x00020102)
    data[12:16] = b'\xff\xfe\x0c\0'
    struct.pack_into('<IHHII', data, 0x10, 0x500, 0, 0x60, 0, len(data))
    data[0x60:0x64] = b'grsc'
    struct.pack_into('<III', data, 0x64, 0, 0x4a0, 0)
    struct.pack_into('<HHHHIIQQ', data, 0x70, 4, 0, 0, 0, 0x800, 1, 0xc0, 0)
    struct.pack_into('<QQQQ', data, 0xc0, 0, 0, 0x100, 0x60)
    struct.pack_into('<H', data, 0x100, 2)
    struct.pack_into('<Q', data, 0x108, 0x1a0)
    struct.pack_into('<Q', data, 0x128, 0x1e0)
    for descriptor, control, code, tag in ((0x1a0, 0x240, 0x300, 1),
                                            (0x1e0, 0x280, 0x400, 2)):
        struct.pack_into('<QQQII', data, descriptor, 0, control, code, 0x80, 0x40)
        struct.pack_into('<II', data, control, 0x98761234, tag)
        struct.pack_into('<II', data, code, 0x12345678, tag)
    data[0x500:0x508] = b'fixture\0'
    return bytes(data)


def synthetic_storage_index_shader():
    control, code = bytearray(0x708), bytearray(0x180)
    struct.pack_into('<II', control, 0x700, 0x80, 0x180)
    source = ('struct C{uint data[16384];};struct S{uint _m0[1];};struct V{uint4 _m0[1];}; '
              'vertex float4 main0(uint gl_InstanceIndex [[instance_id]],uint gl_BaseInstance [[base_instance]],'
              'constant C& c0 [[buffer(0)]],device S& ssbo0 [[buffer(4)]],device V& ssbo0_1 [[buffer(6)]]) {'
              'uint _10=gl_InstanceIndex-gl_BaseInstance;uint _11=_10*64u;'
              'uint _12=uint(max(int((_11+c0.data[68u])-(c0.data[68u]&4294967295u)),int(0u)))>>2u;'
              'return float4(ssbo0._m0[_12]+ssbo0_1._m0[uint(max(int(((_11+48u)+c0.data[68u])-(c0.data[68u]&4294967295u)),int(0u)))>>4u].x);}')
    return source, control, code, {'storage': [{'slot': 0, 'name': 'characters'}]}, [
        {'descriptor_index': 0, 'cbuf_index': 0, 'cbuf_offset': 0x110, 'count': 1, 'is_written': False}]


def synthetic_finite_shader():
    """Artificial constants/IDs mirror no game bytes or translated shader."""
    control, code = bytearray(0x708), bytearray(0x180)
    struct.pack_into('<I', control, 0, 0x98761234)
    struct.pack_into('<I', code, 0, 0x12345678)
    control[8] = code[8] = 6
    struct.pack_into('<II', control, 0x700, 0x80, len(code))
    for index, (x, y) in enumerate(((-1, -1), (1, -1), (-1, 1), (-1, 1), (1, -1), (1, 1))):
        struct.pack_into('<ff', code, 0x80 + index*16, x, y)
    source = ('#include <metal_stdlib>\nusing namespace metal;struct C{uint data[16384];};'
              'struct S{uint4 _m0[1];};struct O{float4 p [[position]];float4 color;};'
              'vertex O main0(uint gl_VertexIndex [[vertex_id]],uint gl_BaseVertex [[base_vertex]],'
              'uint gl_InstanceIndex [[instance_id]],uint gl_BaseInstance [[base_instance]],'
              'constant C& c0 [[buffer(0)]],constant C& c1 [[buffer(1)]],device S& ssbo0 [[buffer(4)]]) {'
              'uint _1=gl_InstanceIndex-gl_BaseInstance;uint _2=_1*16u;'
              'uint _3=uint(max(int((_2+c0.data[68u])-(c0.data[68u]&4294967295u)),int(0u)))>>4u;'
              'uint _4=gl_VertexIndex-gl_BaseVertex;uint _5=_4*4u;uint _6=_5+1u;'
              'float x=as_type<float>(c1.data[_5]);float y=as_type<float>(c1.data[_6]);'
              'return {float4(x*.5+as_type<float>(ssbo0._m0[_3].x),y,0,1),'
              'float4(as_type<float2>(ssbo0._m0[_3].yz),0,1)};}')
    reflection = {'storage': [{'name': 'fixture_instances', 'slot': 0}]}
    descriptors = [{'descriptor_index': 0, 'cbuf_index': 0, 'cbuf_offset': 0x110, 'count': 1, 'is_written': False}]
    return source, control, code, reflection, descriptors


class BnshTests(unittest.TestCase):
    def parser(self):
        spec = importlib.util.find_spec('scripts.botw_native.shaders')
        self.assertIsNotNone(spec, 'BNSH parser has not been implemented')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_parses_real_layout_and_stable_stage_identity(self):
        result = self.parser().parse_bnsh(synthetic_bnsh())
        self.assertEqual(result['name'], 'fixture')
        self.assertEqual(result['version'], '2.1.2')
        self.assertEqual([s['stage'] for s in result['variations'][0]['shaders']],
                         ['vertex', 'fragment'])
        shader = result['variations'][0]['shaders'][0]
        self.assertEqual((shader['control_offset'], shader['code_offset']), (0x240, 0x300))
        self.assertEqual((shader['control_bytes'], shader['code_bytes']), (0x40, 0x80))
        self.assertEqual(len(shader['control_sha256']), 64)

    def test_embedded_scan_uses_declared_container_boundary(self):
        module = self.parser()
        data = b'prefix' + synthetic_bnsh() + b'non-shader-trailer'
        result = module.inspect_embedded_bnsh(data)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['offset'], result[0]['file_bytes']), (6, 0x600))

    def test_out_of_file_stage_pointer_is_rejected(self):
        data = bytearray(synthetic_bnsh())
        struct.pack_into('<Q', data, 0x108, 0x5f8)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.parser().parse_bnsh(data)

    def test_relocated_container_is_not_treated_as_file_offsets(self):
        data = bytearray(synthetic_bnsh())
        struct.pack_into('<H', data, 0x14, 1)
        with self.assertRaisesRegex(ValueError, 'relocat'):
            self.parser().parse_bnsh(data)

    def test_wrong_control_magic_is_rejected(self):
        data = bytearray(synthetic_bnsh())
        data[0x240:0x244] = b'nope'
        with self.assertRaisesRegex(ValueError, 'control'):
            self.parser().parse_bnsh(data)

    def test_extracts_exact_shader_spans_without_executable_or_unknown_bytes(self):
        module = self.parser()
        self.assertTrue(hasattr(module, 'extract_shader_inputs'), 'Local extraction is missing')
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'inputs'
            result = module.extract_shader_inputs(b'prefix' + synthetic_bnsh(), destination)
            self.assertEqual(len(result), 2)
            first = result[0]
            self.assertEqual((destination / first['code_file']).read_bytes(),
                             synthetic_bnsh()[0x300:0x380])
            self.assertEqual((destination / first['control_file']).read_bytes(),
                             synthetic_bnsh()[0x240:0x280])
            with self.assertRaises(FileExistsError):
                module.extract_shader_inputs(synthetic_bnsh(), destination)

    def test_reflection_preserves_explicit_sampler_slots(self):
        data = bytearray(synthetic_bnsh() + bytes(0x400))
        struct.pack_into('<I', data, 0x1c, len(data))
        struct.pack_into('<Q', data, 0x178, 0x600)
        struct.pack_into('<Q', data, 0x620, 0x640)  # fragment reflection
        struct.pack_into('<Q', data, 0x650, 0x700)  # sampler dictionary
        struct.pack_into('<IIIIQ', data, 0x668, 0, 0, 1, 1, 0x780)
        data[0x700:0x704] = b'_DIC'
        struct.pack_into('<I', data, 0x704, 1)
        struct.pack_into('<Q', data, 0x720, 0x800)
        struct.pack_into('<H', data, 0x800, 5)
        data[0x802:0x807] = b'image'
        struct.pack_into('<i', data, 0x780, 3)
        reflection = self.parser().parse_bnsh(data)['variations'][0].get('reflection')
        self.assertIsNotNone(reflection, 'BNSH reflection parsing is missing')
        self.assertEqual(reflection['fragment']['samplers'], [{'name': 'image', 'slot': 3}])

    def test_texture_constraints_require_explicit_reflected_slots(self):
        module = self.parser()
        self.assertTrue(hasattr(module, 'texture_constraints'), 'Texture constraints are missing')
        rows = [{'reflection': {'samplers': [{'name': 'one', 'slot': 0}, {'name': 'two', 'slot': 1}]}}]
        result = module.texture_constraints(rows)
        self.assertEqual([(x['cbuf_index'], x['cbuf_offset'], x['nvn_slot']) for x in result],
                         [(0, 0x20, 0), (0, 0x28, 1)])
        self.assertTrue(all(x['runtime_required_type'] == '2d' for x in result))
        with self.assertRaisesRegex(ValueError, 'slot'):
            module.texture_constraints([{'reflection': {'samplers': [{'name': 'bad', 'slot': 128}]}}])

    def test_truncation_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError, 'size|bounds'):
            self.parser().parse_bnsh(synthetic_bnsh()[:-1])


class ShaderBundleTests(unittest.TestCase):
    def test_stager_keeps_distinct_controls_sharing_code_separate(self):
        import hashlib
        import json
        module = BnshTests().parser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            inputs.mkdir(); converted.mkdir(); records = []
            code = bytearray(0x180); struct.pack_into('<I', code, 0, 0x12345678)
            struct.pack_into('<II', code, 0x80, 0x3e800000, 0)
            struct.pack_into('<II', code, 0x100, 0x3f000000, 0)
            code_hash = hashlib.sha256(code).hexdigest(); (inputs / (code_hash + '.nvn')).write_bytes(code)
            for offset in (0x80, 0x100):
                control = bytearray(0x708); struct.pack_into('<I', control, 0, 0x98761234)
                struct.pack_into('<II', control, 0x700, offset, len(code))
                control_hash = hashlib.sha256(control).hexdigest(); (inputs / (control_hash + '.control')).write_bytes(control)
                records.append({'control_sha256': control_hash, 'code_sha256': code_hash, 'stage': 'fragment',
                                'container': 'synthetic', 'variation': len(records)})
            (inputs / 'inputs.json').write_text(json.dumps({'records': records}))
            (converted / (code_hash + '.metal')).write_text('struct C{float data[16384];};'
                'fragment float4 fs(constant C& c1 [[buffer(0)]]){return float4(c1.data[0u]);}')
            self.assertEqual(module.stage_shader_bundle(inputs, converted, bundle)['ready'], 2)
            entries = json.loads((bundle / 'manifest.json').read_text())['entries']
            self.assertEqual(len({row['msl']['path'] for row in entries}), 2)
            self.assertEqual({row['constant_specializations'][0]['bits'] for row in entries}, {'3e800000', '3f000000'})
            for row in entries:
                self.assertEqual(hashlib.sha256((bundle / row['msl']['path']).read_bytes()).hexdigest(), row['msl']['sha256'])

    def test_finite_storage_specialization_preserves_original_constants_and_readonly_types(self):
        module = BnshTests().parser()
        source, control, code, reflection, descriptors = synthetic_finite_shader()
        transformed, profile = module.specialize_finite_storage_shader(source, control, code, 'vertex', reflection, descriptors)
        self.assertIn('constant uint botw_c1_words[64]', transformed)
        self.assertIn('device const uint4* ssbo0 [[buffer(4)]]', transformed)
        self.assertNotIn('c0.data', transformed)
        self.assertNotIn('c1.data', transformed)
        self.assertNotIn('botw_storage_base_', transformed)
        self.assertEqual(profile['storage_slot'], 0)
        self.assertEqual(module.shader_binding_map(transformed, 'vertex', reflection, descriptors)['bindings'],
                         [{'kind': 'storage', 'nvn_slot': 0, 'metal_slot': 4}])
        with self.assertRaisesRegex(ValueError, 'readonly'):
            module.specialize_finite_storage_shader(source, control, code, 'vertex', reflection,
                                                   [dict(descriptors[0], is_written=True)])
        with self.assertRaisesRegex(ValueError, 'assignment'):
            module.specialize_finite_storage_shader(source.replace('float x=', '_4+=1u;float x='), control, code,
                                                   'vertex', reflection, descriptors)
        for changed in (source.replace('float x=', '(_4)+=100u;float x='),
                        source.replace('float x=', 'thread uint& alias=_4;alias+=100u;float x='),
                        source.replace('c1.data[_5]', '(*(&(c1.data[_5])+100u))'),
                        source.replace('ssbo0._m0[_3].x', '(*(&(ssbo0._m0[_3])+100u)).x'),
                        source.replace('(_2+c0.data[68u])', '(_2&3u+c0.data[68u])')):
            with self.assertRaises(ValueError):
                module.specialize_finite_storage_shader(changed, control, code, 'vertex', reflection, descriptors)
    def test_storage_index_probe_closes_instance_dependencies_after_address_proof(self):
        module = BnshTests().parser()
        source, control, code, reflection, descriptors = synthetic_storage_index_shader()
        probe = module.build_storage_index_probe(source, control, code, 'vertex', reflection, descriptors, 4)
        self.assertEqual(probe['relative_instance_count'], 4)
        self.assertEqual([x['stride'] for x in probe['accesses']], [4, 16])
        self.assertTrue(probe['guest_base_cancellation_proven'])
        self.assertNotIn('c0.data', probe['msl'])
        self.assertNotIn('gl_InstanceIndex', probe['msl'])
        with self.assertRaisesRegex(ValueError, 'external'):
            module.build_storage_index_probe(source.replace('gl_InstanceIndex-gl_BaseInstance', 'in.in_attr0.x'),
                                            control, code, 'vertex', reflection, descriptors, 4)
        with self.assertRaisesRegex(ValueError, 'external'):
            module.build_storage_index_probe(source.replace('gl_InstanceIndex-gl_BaseInstance', 'gl_InstanceIndex'),
                                            control, code, 'vertex', reflection, descriptors, 4)
        with self.assertRaisesRegex(ValueError, 'external'):
            module.build_storage_index_probe(source.replace('gl_InstanceIndex-gl_BaseInstance', 'gl_InstanceIndex-gl_BaseInstance*2u'),
                                            control, code, 'vertex', reflection, descriptors, 4)
    def test_storage_address_cancellation_requires_exact_unsigned_identity(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'cancel_u32_storage_bases'), 'Storage address cancellation proof is missing')
        expression = 'uint(max(int(((_20+16u)+base)-(base & 4294967295u)),int(0u))) >> 4u'
        reduced = module.cancel_u32_storage_bases(expression, {'base'}, {'_20'})
        self.assertNotIn('base', reduced)
        self.assertIn('_20+16u', reduced)
        carried = module.cancel_u32_storage_bases(expression.replace('((_20+16u)+base)', '(((_20+16u)+base)+12u)'), {'base'}, {'_20'})
        self.assertNotIn('base', carried)
        self.assertIn('12u', carried)
        with self.assertRaisesRegex(ValueError, 'unsigned'):
            module.cancel_u32_storage_bases(expression, {'base'}, set())
        mismatched = module.cancel_u32_storage_bases(expression.replace('base &', 'other &'), {'base'}, {'_20'})
        self.assertIn('base', mismatched)
        for operator in ('&', '|', '^', '<<', '>>'):
            invalid = f'(_20{operator}3u+base)-base'
            self.assertIn('base', module.cancel_u32_storage_bases(invalid, {'base'}, {'_20'}))

    def test_dynamic_c1_probe_closes_dependencies_and_rejects_other_inputs(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'build_dynamic_c1_probe'), 'Finite c1 proof generation is missing')
        control, code = bytearray(0x708), bytearray(0x180)
        struct.pack_into('<II', control, 0x700, 0x80, 0x180)
        struct.pack_into('<f', code, 0x88, 1.0)
        source = ('struct U{uint data[64];};struct F{float data[64];}; vertex float4 main0('
                  'uint gl_VertexIndex [[vertex_id]],uint gl_BaseVertex [[base_vertex]],'
                  'constant U& c1 [[buffer(1)]],constant F& c1_1 [[buffer(2)]]) {'
                  'uint _1=gl_VertexIndex-gl_BaseVertex;uint _2=_1*4u;uint _3=_1+uint(c1_1.data[2u]);'
                  'return float4(c1.data[_2]+c1.data[_3]);}')
        probe = module.build_dynamic_c1_probe(source, control, code, 4)
        self.assertEqual(probe['expressions'], ['_2', '_3'])
        self.assertIn('as_type<float>(0x3f800000u)', probe['msl'])
        self.assertNotIn('gl_BaseVertex', probe['msl'])
        self.assertEqual(probe['relative_vertex_count'], 4)
        with self.assertRaisesRegex(ValueError, 'external|input'):
            module.build_dynamic_c1_probe(source.replace('_1*4u', 'in.in_attr0.x'), control, code, 4)
        with self.assertRaisesRegex(ValueError, 'external|input'):
            module.build_dynamic_c1_probe(source.replace('gl_VertexIndex-gl_BaseVertex', 'gl_VertexIndex'), control, code, 4)
        with self.assertRaisesRegex(ValueError, 'external|input'):
            module.build_dynamic_c1_probe(source.replace('gl_VertexIndex-gl_BaseVertex', 'gl_VertexIndex-gl_BaseVertex*2u'), control, code, 4)
        with self.assertRaisesRegex(ValueError, 'ambiguous|assignment'):
            module.build_dynamic_c1_probe(source.replace('return float4', '_2=0u;return float4'), control, code, 4)

    def test_storage_audit_requires_compiler_descriptor_and_bnsh_slot(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'audit_storage_resources'), 'Storage descriptor audit is missing')
        source = ('struct C{uint data[16384];}; struct S{uint _m0[1];}; struct V{uint4 _m0[1];}; '
                  'vertex float4 main0(constant C& c0 [[buffer(0)]],device S& ssbo0 [[buffer(4)]],'
                  'device V& ssbo0_1 [[buffer(6)]]){return float4(c0.data[68u]+ssbo0._m0[i]+ssbo0_1._m0[i].x);}')
        reflection = {'storage': [{'name': 'characters', 'slot': 0}]}
        descriptors = [{'descriptor_index': 0, 'cbuf_index': 0, 'cbuf_offset': 0x110,
                        'count': 1, 'is_written': False}]
        audit = module.audit_storage_resources(source, 'vertex', reflection, descriptors)
        self.assertEqual([(x['nvn_slot'], x['metal_slot'], x['stride']) for x in audit['bindings']],
                         [(0, 4, 4), (0, 6, 16)])
        self.assertEqual(audit['driver_reads'], [{'bank': 0, 'offset': 0x110, 'nvn_slot': 0, 'field': 'address_low'}])
        with self.assertRaisesRegex(ValueError, 'descriptor'):
            module.audit_storage_resources(source, 'vertex', reflection, [])
        with self.assertRaisesRegex(ValueError, 'reflection'):
            module.audit_storage_resources(source, 'vertex', {'storage': []}, descriptors)
        with self.assertRaisesRegex(ValueError, 'driver'):
            module.audit_storage_resources(source.replace('68u', '71u'), 'vertex', reflection, descriptors)

    def test_uniform_array_bounds_are_narrowed_only_for_fixed_reads(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'bound_static_uniforms'), 'Uniform footprint analysis is missing')
        source = ('struct C {float data[16384];}; fragment float4 fs('
                  'constant C& c3 [[buffer(0)]], constant C& c3_1 [[buffer(1)]]) '
                  '{uint i=0; return float4(c3.data[2u], c3_1.data[i],0,1);}')
        transformed, ranges = module.bound_static_uniforms(source)
        self.assertIn('float data[3];', transformed)
        self.assertIn('constant C& c3_1 [[buffer(1)]]', transformed)
        self.assertEqual(ranges, [{'metal_slot': 0, 'nvn_slot': 0, 'bytes': 12, 'proof': 'fixed_indices'},
                                  {'metal_slot': 1, 'nvn_slot': 0, 'bytes': 65536, 'proof': 'declared_dynamic'}])
        _, vector_ranges = module.bound_static_uniforms(source.replace('float data[16384]', 'uint2 data[8192]'))
        self.assertEqual([x['bytes'] for x in vector_ranges], [24, 65536])
        with self.assertRaisesRegex(ValueError, 'bounds'):
            module.bound_static_uniforms(source.replace('c3.data[2u]', 'c3.data[16384u]'))

    def test_static_c1_specialization_uses_original_bits_and_rejects_unknown_indices(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'specialize_static_c1'), 'Exact c1 specialization is missing')
        control, code = bytearray(0x708), bytearray(0x100)
        struct.pack_into('<II', control, 0x700, 0x80, 0x100)
        struct.pack_into('<I', code, 0x80, 0x3e800000)  # Exactly 0.25f.
        source = ('struct C {float data[16384];}; fragment float4 fs(constant C& c1 [[buffer(0)]]) '
                  '{return float4(c1.data[0u],0,0,1);}')
        transformed, uses = module.specialize_static_c1(source, control, code)
        self.assertNotIn('[[buffer', transformed)
        self.assertIn('as_type<float>(0x3e800000u)', transformed)
        self.assertEqual(uses, [{'bank': 1, 'offset': 0, 'bits': '3e800000', 'type': 'float', 'count': 1}])
        with self.assertRaisesRegex(ValueError, 'dynamic|index'):
            module.specialize_static_c1(source.replace('[0u]', '[i]'), control, code)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            module.specialize_static_c1(source.replace('[0u]', '[64u]'), control, code)
        struct.pack_into('<I', control, 0x704, 0x101)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            module.specialize_static_c1(source, control, code)

    def test_stager_cli_preserves_exact_stage_identities_and_counts(self):
        import json
        module = BnshTests().parser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            rows = module.extract_shader_inputs(synthetic_bnsh(), inputs)
            converted.mkdir()
            for row in rows:
                (converted / (row['code_sha256'] + '.metal')).write_text(
                    row['stage'] + ' float4 main0() {return 0;}')
            self.assertEqual(module.main(['--bundle-inputs', str(inputs), '--bundle-compiled', str(converted),
                                          '--bundle-dir', str(bundle)]), 0)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            self.assertEqual(len(manifest['entries']), 2)
            self.assertEqual(len(manifest['stage_records']), 2)
            self.assertTrue(all(x['status'] == 'ready' for x in manifest['entries']))
            for entry in manifest['entries']:
                self.assertEqual((bundle / entry['code']['path']).read_bytes(),
                                 (inputs / entry['code']['path']).read_bytes())

    def test_binding_map_requires_reflection_and_rejects_unknown_driver_banks(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'shader_binding_map'), 'Private bundle binding mapper is missing')
        reflection = {'uniforms': [{'name': 'ubo', 'slot': 0}],
                      'samplers': [{'name': 'image', 'slot': 1}]}
        msl = 'fragment float4 main0(constant Block& c3 [[buffer(2)]], texture2d<float> fs_tex0_28 [[texture(3)]], sampler fs_tex0_28Smplr [[sampler(0)]]) {return 0;}'
        result = module.shader_binding_map(msl, 'fragment', reflection)
        self.assertEqual([(x['kind'], x['nvn_slot'], x['metal_slot']) for x in result['bindings']],
                         [('uniform', 0, 2), ('texture', 1, 3), ('sampler', 1, 0)])
        with self.assertRaisesRegex(ValueError, 'c1|private|driver'):
            module.shader_binding_map(msl.replace('c3 [[', 'c1 [['), 'fragment', reflection)
        with self.assertRaisesRegex(ValueError, 'reflection'):
            module.shader_binding_map(msl, 'fragment', {'uniforms': [], 'samplers': []})


def synthetic_scratch_spirv(stage='vertex', private=False, version=0x10300, extra_op=None):
    def op(code, *args):
        return [((len(args)+1) << 16) | code, *args]
    words = [0x07230203, version, 0, 16, 0]
    words += op(17, 1) + op(14, 0, 1) + op(15, 0 if stage == 'vertex' else 4, 3, 0x6e69616d, 0)
    words += op(19, 1) + op(33, 2, 1)
    if private:
        words += op(59, 8, 9, 6)
    words += op(54, 1, 3, 0, 2) + op(248, 4)
    if extra_op is not None:
        words += op(extra_op)
    words += op(253) + op(56)
    return struct.pack('<' + 'I'*len(words), *words)


def synthetic_scratch_bundle(folder):
    from scripts.botw_native import shaders
    path=Path(folder);data=bytearray(synthetic_bnsh())
    struct.pack_into('<I', data, 0x330, 1 << 10);struct.pack_into('<I', data, 0x430, 5 << 10)
    inputs=path/'inputs';converted=path/'converted';proofs=path/'proofs';converted.mkdir();proofs.mkdir()
    records=shaders.extract_shader_inputs(bytes(data), inputs)
    for record in records:
        stage=record['stage'];key=record['code_sha256']
        source='#include <metal_stdlib>\nusing namespace metal;'+stage+' float4 main0(){return float4(1);}'
        (converted/(key+'.metal')).write_text(source);(proofs/(key+'.spv')).write_bytes(synthetic_scratch_spirv(stage))
    bundle=path/'bundle';result=shaders.stage_shader_bundle(inputs, converted, bundle, scratch_spirv=proofs)
    return bundle, result


class ShaderScratchProofTests(unittest.TestCase):
    def test_python_proof_rejects_fallback_stage_unknown_and_malformed_modules(self):
        from scripts.botw_native.shaders import shader_scratch_proof
        code = bytearray(0x80);struct.pack_into('<I', code, 0x30, 1 << 10)
        spv = synthetic_scratch_spirv()
        facts = shader_scratch_proof(code, spv, 'vertex')
        self.assertEqual((facts['backend'], facts['sph_low'], facts['private_variables']), ('metal_thread_private', 0, 0))
        self.assertEqual(shader_scratch_proof(code, synthetic_scratch_spirv(version=0x10400), 'vertex'), facts)
        fragment = bytearray(code);struct.pack_into('<I', fragment, 0x30, 5 << 10)
        self.assertEqual(shader_scratch_proof(fragment, synthetic_scratch_spirv('fragment', version=0x10400, extra_op=5380), 'fragment'), facts)
        for payload, reason in ((spv[:20], 'incomplete'), (spv[:-1], 'bounds'), (spv[:-4], 'incomplete'),
                                (synthetic_scratch_spirv('fragment'), 'stage'),
                                (synthetic_scratch_spirv(private=True), 'fallback'),
                                (synthetic_scratch_spirv(version=0x10500), 'version'),
                                (synthetic_scratch_spirv(extra_op=5380), 'fragment'),
                                (synthetic_scratch_spirv(extra_op=5381), 'unsupported')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                shader_scratch_proof(code, payload, 'vertex')
        struct.pack_into('<I', code, 0x34, 64)
        with self.assertRaisesRegex(ValueError, 'range/initialization'):
            shader_scratch_proof(code, synthetic_scratch_spirv(private=True), 'vertex')

    def test_stager_pairs_exact_code_with_explicit_bounded_spirv_assets(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            bundle,result=synthetic_scratch_bundle(folder)
            self.assertEqual((result['ready'],result['unsupported']), (2,0))
            manifest=json.loads((bundle/'manifest.json').read_text())
            for entry in manifest['entries']:
                self.assertEqual((entry['scratch']['sph_low'],entry['scratch']['private_variables']), (0,0))
                self.assertTrue((bundle/entry['scratch']['spirv']['path']).is_file())

    def test_c_proof_checks_actual_asset_bounds_and_silent_fallback(self):
        code = bytearray(0x80);struct.pack_into('<I', code, 0x30, 1 << 10)
        def array(name, data):
            return 'unsigned char '+name+'[]={'+','.join(map(str, data))+'};\n'
        source = '#include "shader_scratch_proof.h"\n#include <stdio.h>\nint main(void){char e[256];\n'
        source += array('code', code) + array('spv', synthetic_scratch_spirv()) + array('fallback', synthetic_scratch_spirv(private=True))
        source += array('demote', synthetic_scratch_spirv('fragment', version=0x10400, extra_op=5380))
        source += 'if(!bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,spv,sizeof spv,e,sizeof e))return 1;\n'
        source += 'spv[6]=1;spv[5]=4;if(!bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,spv,sizeof spv,e,sizeof e))return 6;\n'
        source += 'spv[5]=5;if(bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,spv,sizeof spv,e,sizeof e)||!strstr(e,"version"))return 7;spv[5]=3;\n'
        source += 'code[0x31]=20;if(!bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_FRAGMENT,demote,sizeof demote,e,sizeof e))return 8;code[0x31]=4;\n'
        source += 'if(bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,demote,sizeof demote,e,sizeof e))return 9;\n'
        source += 'if(bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,fallback,sizeof fallback,e,sizeof e)||!strstr(e,"fallback"))return 2;\n'
        source += 'if(bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,spv,20,e,sizeof e))return 3;\n'
        source += 'if(bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_FRAGMENT,spv,sizeof spv,e,sizeof e))return 4;\n'
        source += 'if(bwScratchProof(code,sizeof code,NVN_SHADER_STAGE_VERTEX,spv,sizeof spv-4,e,sizeof e))return 5;\n'
        source += 'puts("BOUNDED_C_SCRATCH_PROOF_OK");return 0;}\n'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'proof.c').write_text(source)
            build=subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'native/botw_graphics'), str(path/'proof.c'), '-o', str(path/'proof')], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run=subprocess.run([str(path/'proof')], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            self.assertIn('BOUNDED_C_SCRATCH_PROOF_OK', run.stdout)


class BufferAbiTests(unittest.TestCase):
    def test_upstream_buffer_module_base52_layout_pointer_abi_and_exact_slices(self):
        source = r"""
#include "nvn_protos.h"
#include "vendor/botw-buffer/buffer.h"
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
_Static_assert(__builtin_types_compatible_p(NVNT_nvnBufferBuilderGetMemoryPool,NVNmemoryPool*(*)(const NVNbufferBuilder*)),"pool pointer ABI");
_Static_assert(__builtin_types_compatible_p(PFNNVNBUFFERBUILDERGETMEMORYPOOLPROC,NVNmemoryPool*(*)(const NVNbufferBuilder*)),"public pool pointer ABI");
_Static_assert(offsetof(struct bwu_builder,device)==0&&offsetof(struct bwu_builder,pool)==8&&offsetof(struct bwu_builder,offset)==16&&offsetof(struct bwu_builder,size)==24,"base52 fields");
static uint64_t bwu_new_serial(void){static uint64_t serial;return ++serial;}
#define BWU_MAGIC(a,b,c,d) ((uint32_t)(a)|((uint32_t)(b)<<8)|((uint32_t)(c)<<16)|((uint32_t)(d)<<24))
#define BWU_LOG(...) ((void)0)
#define BWU_DIE(message) abort()
#define BUFFER_UPSTREAM(name,ret,...) static __attribute__((unused)) ret bwu_##name(__VA_ARGS__)
#include "vendor/botw-buffer/buffer.inc"
#define CHECK(x) do{if(!(x))return __LINE__;}while(0)
int main(void){NVNbufferBuilder builder;memset(&builder,0xa5,sizeof builder);NVNdevice *device=(void*)(uintptr_t)0x1234;
 bwu_nvnBufferBuilderSetDevice(&builder,device);bwu_nvnBufferBuilderSetDefaults(&builder);
 CHECK(((struct bwu_builder*)&builder)->device==device);for(size_t i=32;i<sizeof builder;i++)CHECK((unsigned char)builder.reserved[i]==0xa5);
 unsigned char memory[16]={0};bwu_pool pool={BWU_MAGIC('P','O','O','L'),NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED,memory,sizeof memory};
 bwu_nvnBufferBuilderSetStorage(&builder,(NVNmemoryPool*)&pool,3,7);NVNbuffer object;CHECK(bwu_nvnBufferInitialize(&object,&builder));
 CHECK(bwu_nvnBufferMap(&object)==memory+3&&bwu_nvnBufferGetAddress(&object)==(uintptr_t)(memory+3));
 CHECK(bwu_nvnBufferBuilderGetMemoryPool(&builder)==(NVNmemoryPool*)&pool&&bwu_nvnBufferGetMemoryPool(&object)==(NVNmemoryPool*)&pool);
 CHECK(bwu_nvnBufferBuilderGetMemoryOffset(&builder)==3&&bwu_nvnBufferGetMemoryOffset(&object)==3);
 CHECK(bwu_nvnBufferBuilderGetSize(&builder)==7&&bwu_nvnBufferGetSize(&object)==7&&bwu_nvnBufferGetDebugID(&object));
 bwu_nvnBufferFlushMappedRange(&object,7,0);bwu_nvnBufferInvalidateMappedRange(&object,1,6);bwu_nvnBufferFinalize(&object);CHECK(!bwu_nvnBufferMap(&object));
 puts("UPSTREAM_BUFFER_BASE52_PORTABLE_ABI_AND_SLICE_OK");return 0;}
"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'buffer.c').write_text(source)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'native/botw_graphics'),str(path/'buffer.c'),'-o',str(path/'buffer')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(path/'buffer')],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('UPSTREAM_BUFFER_BASE52_PORTABLE_ABI_AND_SLICE_OK',run.stdout)


@unittest.skipUnless(platform.system() == 'Darwin', 'requires macOS Foundation ABI')
class BufferStorageDiagnosticCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-buffer-diagnostic-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        graphics = ROOT / 'native/botw_graphics'
        source = folder / 'diagnostic.m'
        source.write_text(r'''
#import <Foundation/Foundation.h>
#include <mach/mach.h>
#include <setjmp.h>
static unsigned vmQueries,accessCalls,callbacks;
static kern_return_t diagnosticVMQuery(vm_map_read_t task,vm_address_t *address,vm_size_t *size,natural_t *depth,vm_region_recurse_info_t info,mach_msg_type_number_t *count){(void)task;(void)address;(void)size;(void)depth;(void)info;(void)count;vmQueries++;return KERN_FAILURE;}
#define vm_region_recurse_64 diagnosticVMQuery
#include "graphics.m"
#undef vm_region_recurse_64
static sigjmp_buf recovery;
static void diagnosticError(const char *error){(void)error;callbacks++;siglongjmp(recovery,1);}
static int diagnosticAccess(const void *address,size_t bytes,int write){(void)address;(void)bytes;(void)write;accessCalls++;return 1;}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"BUFFER_DIAGNOSTIC_CHECK line%d %s\n",__LINE__,#x);return 91;}}while(0)
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2&&!gpu&&!commandQueue);objects=[NSMutableDictionary new];objectSpans=[NSMutableDictionary new];uint64_t allocation[96];memset(allocation,0xa5,sizeof allocation);unsigned char before[sizeof allocation];memcpy(before,allocation,sizeof before);unsigned char *base=(unsigned char*)allocation+256;void *destination=base;size_t spanBytes=sizeof(NVNtexture);NSNumber *key=@101;BWTexture *texture=[BWTexture new];texture.identity=101;texture.storageMoveEpoch=7;id value=texture;
 if(!strcmp(argv[1],"inside"))destination=base+16;else if(!strcmp(argv[1],"prefix"))destination=base-16;else if(!strcmp(argv[1],"exact-span"))spanBytes=sizeof(NVNbufferBuilder);else if(!strcmp(argv[1],"selected"))texture.base=[BWTexture new];else if(!strcmp(argv[1],"wrong-identity"))texture.identity=99;else if(!strcmp(argv[1],"other-class")){value=[BWSampler new];spanBytes=sizeof(NVNsampler);}else if(!strcmp(argv[1],"missing"))value=nil;
 size_t page=getpagesize();void *none=NULL;if(!strcmp(argv[1],"unreadable")){none=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(none!=MAP_FAILED);base=none;destination=none;}
 if(value)objects[key]=value;objectSpans[key]=[NSValue valueWithRange:NSMakeRange((NSUInteger)base,spanBytes)];uint64_t serialBefore=serial;NSUInteger count=objects.count;void *map=(__bridge void*)objectSpans;errorCallback=diagnosticError;memoryAccessValidator=diagnosticAccess;
 if(!strcmp(argv[1],"nonoverlap")){destination=(unsigned char*)allocation;NVNdevice rawDevice={0};BWDevice *device=[[BWDevice alloc]initWithTextureKindPolicy:BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH];memoryAccessValidator=NULL;put(&rawDevice,device);serialBefore=serial;count=objects.count;memoryAccessValidator=diagnosticAccess;w_nvnBufferBuilderSetDevice(destination,&rawDevice);CHECK(!callbacks&&!vmQueries&&accessCalls==2&&serial==serialBefore&&objects.count==count&&(__bridge void*)objectSpans==map);CHECK(*(NVNdevice**)destination==&rawDevice);CHECK(!memcmp((char*)destination+8,before+8,sizeof allocation-8));puts("BUFFER_STORAGE_DIAGNOSTIC_CPU_OK positive");return 0;}
 if(!sigsetjmp(recovery,1)){w_nvnBufferBuilderSetDevice(destination,(NVNdevice*)(uintptr_t)1);CHECK(0);}
 CHECK(callbacks==1&&accessCalls==1&&!vmQueries&&!graphicsDepth&&!pendingError&&!pendingFatal);CHECK(serial==serialBefore&&objects.count==count&&objectSpans.count==1&&(__bridge void*)objectSpans==map&&objects[key]==value);CHECK(!memcmp(before,allocation,sizeof allocation));
 CHECK(strstr(lastError,"entry=nvnBufferBuilderSetDevice: Unsupported or invalid NVN operation: buffer storage overlaps live NVN object"));char field[128];snprintf(field,sizeof field,"existing_class=%s",value?class_getName([value class]):"<none>");CHECK(strstr(lastError,field));snprintf(field,sizeof field,"span_bytes=%zu",spanBytes);CHECK(strstr(lastError,field));snprintf(field,sizeof field,"requested_bytes=%zu",sizeof(NVNbufferBuilder));CHECK(strstr(lastError,field));BOOL exact=destination==base;CHECK(strstr(lastError,exact?"exact_base=true":"exact_base=false"));CHECK(strstr(lastError,exact&&spanBytes==sizeof(NVNbufferBuilder)?"exact_span=true":"exact_span=false"));CHECK(strstr(lastError,[value isKindOfClass:[BWTexture class]]?(texture.base||texture.identity!=101?"existing_primary=false":"existing_primary=true"):"existing_primary=not_texture"));CHECK(strstr(lastError,[value isKindOfClass:[BWTexture class]]?"existing_move_epoch=7":"existing_move_epoch=0"));CHECK(strstr(lastError,!strcmp(argv[1],"prefix")?"relative_offset=-16":!strcmp(argv[1],"inside")?"relative_offset=+16":"relative_offset=+0"));CHECK(strlen(lastError)<512);CHECK(!pthread_mutex_trylock(&graphicsLock));pthread_mutex_unlock(&graphicsLock);if(none)CHECK(!munmap(none,page));puts("BUFFER_STORAGE_DIAGNOSTIC_CPU_OK refused_unchanged");return 0;
}}
''')
        cls.executable = folder / 'diagnostic'
        build = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
            '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-O1', '-g', '-fsanitize=address,undefined',
            '-I', str(graphics), str(source), '-framework', 'Foundation',
            '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=120)
        if build.returncode:
            raise AssertionError(build.stdout+build.stderr)

    def mode(self, mode):
        run = subprocess.run([str(self.executable), mode], capture_output=True,
                             text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertIn('BUFFER_STORAGE_DIAGNOSTIC_CPU_OK', run.stdout)
        self.assertNotIn('runtime error:', run.stderr)
        self.assertNotIn('ERROR: AddressSanitizer', run.stderr)

    def test_rejected_buffer_builder_reports_only_owned_conflict_metadata(self):
        for mode in ['exact', 'inside', 'prefix', 'exact-span', 'selected',
                     'wrong-identity', 'other-class', 'missing', 'unreadable']:
            with self.subTest(mode=mode):
                self.mode(mode)

    def test_nonoverlapping_buffer_builder_preserves_original_write_and_access_count(self):
        self.mode('nonoverlap')


@unittest.skipUnless(platform.system() == 'Darwin', 'requires macOS Foundation ABI')
class TextureHandleCodecCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-handle-cpu-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        graphics = ROOT / 'native/botw_graphics'
        source = folder / 'codec.m'
        # Independent arbitrary-precision oracle emits C golden constants;
        # production uses fixed-width unsigned operations, not this formula.
        values = [-2147483648, -1048576, -4096, -1, 0, 1, 0x101, 0x7ff,
                  0x800, 0xfff, 0x1000, 0x7ffff, 0x80000, 0xfffff,
                  0x100000, 0x12345678, 2147483647]
        pairs = [(0, 0)] + [(t, s) for t in values for s in values if (t, s) != (0, 0)]
        golden = []
        for texture, sampler in pairs:
            low = (texture | (sampler << 20)) & 0xffffffff
            signed = low if low < 0x80000000 else low - 0x100000000
            expected = (signed & 0xffffffffffffffff) | 0x100000000
            golden.append(f' {{{texture},{sampler},UINT64_C(0x{expected:016x})}},')
        source.write_text(r"""
#import <Foundation/Foundation.h>
#include <mach/mach.h>
#include <stdint.h>
#include <setjmp.h>
static unsigned vmQueries,validatorCalls,callbacks;
static kern_return_t codecVMQuery(vm_map_read_t task,vm_address_t *address,vm_size_t *size,natural_t *depth,vm_region_recurse_info_t info,mach_msg_type_number_t *count){(void)task;(void)address;(void)size;(void)depth;(void)info;(void)count;vmQueries++;return KERN_FAILURE;}
#define vm_region_recurse_64 codecVMQuery
#include "graphics.m"
#undef vm_region_recurse_64
static sigjmp_buf recovery;
static void codecError(const char *error){fprintf(stderr,"UNEXPECTED_CODEC_CALLBACK %s\n",error);callbacks++;siglongjmp(recovery,1);}
static int codecAccess(const void *address,size_t bytes,int write){(void)address;(void)bytes;(void)write;validatorCalls++;return 1;}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"HANDLE_CPU_CHECK line%d %s\n",__LINE__,#x);return 91;}}while(0)
static const struct{int32_t texture,sampler;uint64_t expected;} golden[]={
""" + "\n".join(golden) + r"""
};
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2&&!gpu&&!commandQueue);objects=[NSMutableDictionary new];objectSpans=[NSMutableDictionary new];NVNdevice raw={0};BWDevice *device=[[BWDevice alloc]initWithTextureKindPolicy:BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH];put(&raw,device);uint64_t beforeSerial=serial;NSUInteger objectCount=objects.count,spanCount=objectSpans.count;void *objectMap=(__bridge void*)objects,*spanMap=(__bridge void*)objectSpans;
 memoryAccessValidator=codecAccess;errorCallback=codecError;strcpy(lastError,"owned-codec-sentinel");const char *entry=currentEntry;
 size_t page=getpagesize();void *none=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(none!=MAP_FAILED);NVNtexture wrong={0};objects[@(beforeSerial+100)]=[BWTexture new];((BWObject*)&wrong)->serial=beforeSerial+100;objectCount++;
 const NVNdevice *arguments[]={&raw,NULL,none,(const NVNdevice*)(uintptr_t)1,(const NVNdevice*)&wrong};unsigned limit=!strcmp(argv[1],"golden")?1:5;
 unsigned count=limit==1?sizeof golden/sizeof *golden:1;for(unsigned d=0;d<limit;d++)for(unsigned i=0;i<count;i++){
  if(sigsetjmp(recovery,1)){fprintf(stderr,"CODEC_REJECTED d=%u case=%u\n",d,i);return 91;}
  uint64_t value=w_nvnDeviceGetTextureHandle(arguments[d],golden[i].texture,golden[i].sampler);CHECK(value==golden[i].expected);CHECK(textureDescriptorIndex(value)==((uint32_t)value&0xfffff)&&samplerDescriptorIndex(value)==(((uint32_t)value>>20)&0xfff));
 }
 CHECK(!validatorCalls&&!vmQueries&&!callbacks&&!graphicsDepth&&!pendingError&&!pendingFatal);CHECK(currentEntry==entry&&!strcmp(lastError,"owned-codec-sentinel"));CHECK(serial==beforeSerial&&objects.count==objectCount&&objectSpans.count==spanCount&&(__bridge void*)objects==objectMap&&(__bridge void*)objectSpans==spanMap&&!gpu&&!commandQueue);CHECK(!munmap(none,page));printf("HANDLE_CODEC_CPU_OK cases=%zu devices=%u validator=0 vm=0 callbacks=0 no_real_gpu=true\n",sizeof golden/sizeof *golden,limit);return 0;
}}
""")
        cls.executable = folder / 'codec'
        build = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
            '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-O1', '-g', '-fsanitize=address,undefined',
            '-I', str(graphics), str(source), '-framework', 'Foundation',
            '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=120)
        if build.returncode:
            raise AssertionError(build.stdout+build.stderr)

    def mode(self, mode):
        run = subprocess.run([str(self.executable), mode], capture_output=True,
                             text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertIn('HANDLE_CODEC_CPU_OK', run.stdout)
        self.assertEqual(run.stderr, '')

    def test_full_signed_indices_follow_fixed_original_packed_bit_contract(self):
        self.mode('golden')

    def test_device_is_ignored_without_header_validator_vm_callback_or_registry_effect(self):
        self.mode('ignored-device')


@unittest.skipUnless(platform.system() == 'Darwin', 'Metal requires macOS')
class MetalBridgeTests(unittest.TestCase):
    def run_graphics_probe(self, name, expected):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); graphics = ROOT / 'native/botw_graphics'; exe = path / 'queue'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(graphics / 'tools' / name),
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(expected, result.stdout)

    def compile_render_probe(self, source, cases, extra_sources=()):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); graphics = ROOT / 'native/botw_graphics'; exe = path / 'state'
            harness = path / 'state.c'; harness.write_text(source)
            build = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(harness), *[str(graphics/name) for name in extra_sources], '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            for mode, expected, accepted in cases:
                with self.subTest(mode=mode):
                    run = subprocess.run([str(exe)] + ([mode] if mode else []), capture_output=True, text=True, timeout=30)
                    if run.returncode == 77:
                        self.skipTest('No Metal device')
                    if accepted:
                        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                        self.assertIn(expected, run.stdout)
                    else:
                        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
                        self.assertIn(expected, run.stderr)

    def test_fixed_upstream_buffer_family_base52_bounds_identity_and_gpu_copy(self):
        self.run_graphics_probe('probe_buffer_family.c', 'UPSTREAM_BUFFER_FAMILY_BASE52_BOUNDS_IDENTITY_AND_REAL_METAL_COPY_OK')

    def test_buffer_slice_addresses_reach_vertex_uniform_storage_and_two_metal_queues(self):
        helper = r"""
static uint64_t sliceAddress(NVNdevice *device,NVNmemoryPool *pool,ptrdiff_t offset,size_t bytes){
 API(nvnBufferBuilderSetDevice);API(nvnBufferBuilderSetDefaults);API(nvnBufferBuilderSetStorage);
 API(nvnBufferInitialize);API(nvnBufferGetAddress);API(nvnBufferFinalize);
 NVNbufferBuilder b;NVNbuffer object;nvnBufferBuilderSetDevice(&b,device);nvnBufferBuilderSetDefaults(&b);
 nvnBufferBuilderSetStorage(&b,pool,offset,bytes);CHECK(nvnBufferInitialize(&object,&b));
 uint64_t address=nvnBufferGetAddress(&object);nvnBufferFinalize(&object);return address;
}
"""
        resources = RESOURCE_FEATURE.replace('gpu+512', 'sliceAddress(&device,&pool,512,sizeof positions)')
        resources = resources.replace('gpu+768', 'sliceAddress(&device,&pool,768,16)').replace('gpu+784', 'sliceAddress(&device,&pool,784,16)')
        resources = resources.replace('    for(int pass=0;pass<2;pass++){', '    NVNqueue secondQueue;CHECK(nvnQueueInitialize(&secondQueue,&qb));for(int pass=0;pass<2;pass++){')
        resources = resources.replace('nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);', 'nvnQueueSubmitCommands(pass?&secondQueue:&queue,1,&handle);nvnQueueFinish(pass?&secondQueue:&queue);')
        source = METAL_SMOKE.replace('int main(int argc, char **argv)', helper+'int main(int argc, char **argv)')
        source = source.replace('    nvnMemoryPoolFinalize(&pool);', resources+'    nvnMemoryPoolFinalize(&pool);')
        # The native depth allocation and pool-relative offset each need 64KiB.
        source = _depth_storage_capacity(source).replace('page*2', 'page*4')
        self.compile_render_probe(source, [('', 'GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', True)])
        storage = STORAGE_FEATURE.replace('3,gpu+1024,', '3,sliceAddress(&device,&pool,1024,sizeof instances),')
        storage = storage.replace('NVNbufferRange writable={gpu+1280,48}', 'NVNbufferRange writable={sliceAddress(&device,&pool,1280,48),48}')
        source = METAL_SMOKE.replace('int main(int argc, char **argv)', helper+'int main(int argc, char **argv)')
        source = source.replace('    nvnMemoryPoolFinalize(&pool);', storage+'    nvnMemoryPoolFinalize(&pool);')
        self.compile_render_probe(source, [('', 'GPU_STORAGE_INSTANCING_BASE_IDS_OK', True)])

    def test_actually_consumed_uniform_pool_generation_is_checked_before_gpu_draw(self):
        setup = ('unsigned char *uniformMemory=NULL;CHECK(!posix_memalign((void**)&uniformMemory,page,page));'
                 'memset(uniformMemory,0,page);NVNmemoryPool uniformPool;'
                 'nvnMemoryPoolBuilderSetStorage(&mb,uniformMemory,page);'
                 'CHECK(nvnMemoryPoolInitialize(&uniformPool,&mb));')
        feature = RESOURCE_FEATURE.replace('    for(int pass=0;pass<2;pass++){', '    '+setup+'for(int pass=0;pass<2;pass++){')
        feature = feature.replace('NVN_SHADER_STAGE_VERTEX,5,gpu+768,', 'NVN_SHADER_STAGE_VERTEX,5,(uintptr_t)uniformMemory,')
        feature = feature.replace('nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=',
                                  'nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);nvnMemoryPoolFinalize(&uniformPool);handle=')
        source = _depth_storage_capacity(METAL_SMOKE).replace('page*2','page*4').replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        self.compile_render_probe(source, [('', 'stale buffer address pool generation', False)])

    def test_unused_uniform_and_storage_pool_lifetimes_do_not_reject_unrelated_draw(self):
        setup = ('API(nvnCommandBufferBindUniformBuffer);API(nvnCommandBufferBindStorageBuffer);'
                 'unsigned char *unusedMemory=NULL;CHECK(!posix_memalign((void**)&unusedMemory,page,page));'
                 'NVNmemoryPool unusedPool;nvnMemoryPoolBuilderSetStorage(&mb,unusedMemory,page);'
                 'CHECK(nvnMemoryPoolInitialize(&unusedPool,&mb));')
        source = METAL_SMOKE.replace('    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);\n    nvnCommandBufferBindProgram',
                                    '    '+setup+'nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);\n    nvnCommandBufferBindProgram')
        source = source.replace('    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);',
            '    nvnCommandBufferBindUniformBuffer(&cb,NVN_SHADER_STAGE_VERTEX,127,(uintptr_t)unusedMemory,64);'
            'nvnCommandBufferBindStorageBuffer(&cb,NVN_SHADER_STAGE_FRAGMENT,15,(uintptr_t)unusedMemory,64);'
            'nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);nvnMemoryPoolFinalize(&unusedPool);')
        source = source.replace('    nvnMemoryPoolFinalize(&pool);', '    free(unusedMemory);nvnMemoryPoolFinalize(&pool);')
        self.compile_render_probe(source, [('', 'GPU_COPY_CLEAR_DRAW_OK', True)])

    def test_fixed_upstream_six_state_families_typed_bounds_rw_and_error_recovery(self):
        self.run_graphics_probe('probe_render_states.c', 'FIXED_UPSTREAM_SIX_STATE_FAMILIES_TYPED_BOUNDS_RW_AND_UNLOCK_OK')

    def test_upstream_states_blending_masks_snapshots_cache_and_unsupported_draw_semantics(self):
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', RENDER_STATE_FEATURE + '    nvnMemoryPoolFinalize(&pool);')
        self.compile_render_probe(source, [(mode, 'UPSTREAM_STATE_BLEND_MASK_POLYGON_MS_DEPTH_GPU_OK', True)
            for mode in ('', 'mask', 'disabled', 'both')] + [
            ('stencil', 'stencil attachment/render semantics', False),
            ('samples', 'multisample coverage/sample locations', False),
            ('coverage', 'multisample coverage/sample locations', False),
            ('locations', 'multisample coverage/sample locations', False),
            ('point', 'polygon point rasterization', False)])

    def test_front_and_back_cull_retains_real_vertex_storage_side_effects(self):
        feature = ("API(nvnPolygonStateSetDefaults);API(nvnPolygonStateSetCullFace);API(nvnCommandBufferBindPolygonState);"
                   "NVNpolygonState discard;nvnPolygonStateSetDefaults(&discard);nvnPolygonStateSetCullFace(&discard,NVN_FACE_FRONT_AND_BACK);") + STORAGE_FEATURE
        feature = feature.replace('nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);',
            'float greenDiscard[]={0,1,0,1};nvnCommandBufferClearColor(&cb,0,greenDiscard,15);nvnCommandBufferBindPolygonState(&cb,&discard);'
            'nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);')
        feature = feature.replace('CHECK(pixels[0]==255&&pixels[1]==0&&pixels[12]==0&&pixels[13]==255);',
            'for(int p=0;p<16;p++)CHECK(pixels[p*4]==0&&pixels[p*4+1]==255&&pixels[p*4+2]==0&&pixels[p*4+3]==255);')
        feature = feature.replace('GPU_STORAGE_INSTANCING_BASE_IDS_OK', 'CULL_BOTH_PRESERVES_REAL_VERTEX_STORAGE_WRITES_OK')
        source = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature + '    nvnMemoryPoolFinalize(&pool);')
        self.compile_render_probe(source, [('', 'CULL_BOTH_PRESERVES_REAL_VERTEX_STORAGE_WRITES_OK', True)])

    def test_upstream_depth_ranges_bias_masks_front_back_and_winding_reach_gpu_pixels(self):
        source = _depth_storage_capacity(METAL_SMOKE).replace('    nvnMemoryPoolFinalize(&pool);', DEPTH_RASTER_FEATURE + '    nvnMemoryPoolFinalize(&pool);')
        source = source.replace('return float4(p[v],0,1);', 'return float4(p[v],.25,1);')
        self.compile_render_probe(source, [('', 'UPSTREAM_DEPTH_RANGE_BIAS_FACE_WINDING_REAL_PIXELS_OK', True)])

    def test_metal_owned_scratch_zero_pool_typed_record_range_device_and_stale_generations(self):
        self.run_graphics_probe('probe_shader_scratch.c', 'METAL_OWNED_SCRATCH_ZERO_POOL_RECORD_RANGE_DEVICE_AND_GENERATION_OK')

    def test_proven_zero_scratch_draws_two_queues_and_rejects_unproved_or_stale_shaders(self):
        source=METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', SCRATCH_GPU_FEATURE+'    nvnMemoryPoolFinalize(&pool);')
        self.compile_render_probe(source, [('', 'PROVEN_SHADER_SCRATCH_ZERO_POOL_TWO_QUEUES_GPU_PIXELS_OK', True),
            ('large-private', 'PROVEN_SHADER_SCRATCH_ZERO_POOL_TWO_QUEUES_GPU_PIXELS_OK', True),
            ('null-scratch', 'PROVEN_SHADER_SCRATCH_ZERO_POOL_TWO_QUEUES_GPU_PIXELS_OK', True),
            ('unproved', 'shader lacks independently validated Metal-private scratch lowering proof', False),
            ('stale-scratch', 'stale scratch pool generation', False)])

    def test_bundle_loader_recomputes_scratch_proof_and_rejects_hidden_private_fallback(self):
        import json,hashlib,shutil
        with tempfile.TemporaryDirectory() as folder:
            bundle,_=synthetic_scratch_bundle(folder);bad=Path(folder)/'bad';shutil.copytree(bundle,bad)
            manifest=json.loads((bad/'manifest.json').read_text());entry=manifest['entries'][0]
            payload=synthetic_scratch_spirv(entry['stage'],private=True);asset=entry['scratch']['spirv']
            (bad/asset['path']).write_bytes(payload);asset.update(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest())
            (bad/'manifest.json').write_text(json.dumps(manifest))
            self.compile_render_probe(BUNDLE_SMOKE, [(str(bundle),'REGISTERED=2 UNSUPPORTED=0',True),
                (str(bad),'implicit 128-byte local-memory fallback',False)], extra_sources=('shader_bundle.m',))

    def test_object_errors_include_abi_entry_type_raw_identity_and_release_locks(self):
        self.run_graphics_probe('probe_object_diagnostic.c', 'NVN_ENTRY_OBJECT_DIAGNOSTIC_AND_ERROR_ESCAPE_OK')

    def test_texture_handle_codec_keeps_valid_sampling_and_rejects_bad_use_before_counter(self):
        import os
        from botw_metal_counter_capability import require_counter_sampling
        require_counter_sampling()
        feature = RESOURCE_FEATURE.replace(
            'uint64_t handleTexture=nvnDeviceGetTextureHandle(&device,3,4);',
            'int rejectHandle=argc>1&&strcmp(argv[1],"valid");'
            'uint64_t handleTexture=nvnDeviceGetTextureHandle(NULL,'
            '(argc>1&&!strcmp(argv[1],"sentinel"))?-1:'
            '(argc>1&&!strcmp(argv[1],"unregistered"))?5:3,4);'
            'if(argc>1&&!strcmp(argv[1],"sentinel"))CHECK(handleTexture==UINT64_MAX);')
        feature = feature.replace('    for(int pass=0;pass<2;pass++){',
            '    API(nvnCommandBufferReportCounter);unsigned char priorPixels[64];memcpy(priorPixels,pixels,sizeof priorPixels);'
            'memset(memory+16384,0x3c,32);memset(memory+16448,0xa5,32);memset(memory+16512,0xa5,16);'
            '    for(int pass=0;pass<2;pass++){')
        feature = feature.replace('nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets',
            'nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,gpu+16384,gpu+16448,32,0);'
            'nvnCommandBufferReportCounter(&cb,NVN_COUNTER_TYPE_TIMESTAMP_TOP,gpu+16512);nvnCommandBufferSetRenderTargets')
        feature = feature.replace('nvnCommandBufferSetTexturePool(&cb,&tp);',
            'if(!(argc>1&&!strcmp(argv[1],"missing-pool")))nvnCommandBufferSetTexturePool(&cb,&tp);')
        feature = feature.replace('      nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);',r'''
      if(rejectHandle){
        BWQueue *owned=get(&queue,[BWQueue class]);NSData *arenaBefore=digest(owned.arena,owned.arenaBytes);codecEscaping=1;
        if(!sigsetjmp(codecRecovery,1)){nvnQueueSubmitCommands(&queue,1,&handle);CHECK(0);}codecEscaping=0;
        CHECK(codecCallbacks==1&&strstr(botw_graphics_last_error(),!strcmp(argv[1],"sentinel")?"descriptor slot index bounds":!strcmp(argv[1],"missing-pool")?"stale texture descriptor pool":"unbound texture descriptor"));
        nvnQueueFinish(&queue);CHECK(dispatch_group_wait(allGPU,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0);
        CHECK([arenaBefore isEqual:digest(owned.arena,owned.arenaBytes)]);for(unsigned i=0;i<32;i++)CHECK(memory[16448+i]==0xa5);for(unsigned i=0;i<16;i++)CHECK(memory[16512+i]==0xa5);
        CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels)&&!memcmp(priorPixels,pixels,sizeof pixels));CHECK(!graphicsDepth&&!pendingError&&!pendingFatal);
        char error[512];CHECK(botw_graphics_shutdown_checked(error,sizeof error));free(memory);puts("HANDLE_USE_REJECTED_WHOLE_BATCH_BEFORE_COPY_COUNTER_ARENA_AND_PIXELS_OK");return 0;
      }
      nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);''')
        source = _depth_storage_capacity(METAL_SMOKE).replace('    nvnMemoryPoolFinalize(&pool);',
            feature+'    nvnMemoryPoolFinalize(&pool);')
        source = source.replace('#include <unistd.h>',r'''#include <unistd.h>
#include <setjmp.h>
static sigjmp_buf codecRecovery;static int codecEscaping,codecCallbacks;
static void codecUseError(const char *message){if(codecEscaping){codecCallbacks++;siglongjmp(codecRecovery,1);}fprintf(stderr,"EXPECTED_OR_UNHANDLED %s\n",message);}
''').replace('botw_graphics_init(NULL, NULL, valid)', 'botw_graphics_init(NULL, codecUseError, valid)')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory);graphics=ROOT/'native/botw_graphics'
            harness=path/'handles.m';exe=path/'handles'
            harness.write_text('#include "graphics.m"\n'+source)
            built=subprocess.run(['xcrun','clang','-fobjc-arc','-fobjc-arc-exceptions','-fblocks',
                '-Wall','-Wextra','-Werror','-Wno-unused-function','-I',str(graphics),str(harness),
                '-framework','Foundation','-framework','Metal','-framework','QuartzCore','-o',str(exe)],
                capture_output=True,text=True,timeout=120)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            for mode in ['valid','sentinel','missing-pool','unregistered']:
                with self.subTest(mode=mode):
                    run=subprocess.run([str(exe),mode],capture_output=True,text=True,timeout=30,
                        env={**os.environ,'MTL_DEBUG_LAYER':'1'})
                    if run.returncode==77:self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                    self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK' if mode=='valid' else 'HANDLE_USE_REJECTED_WHOLE_BATCH_BEFORE_COPY_COUNTER_ARENA_AND_PIXELS_OK',run.stdout)
                    self.assertIn('Metal API Validation Enabled',run.stderr)
                    self.assertNotIn('failed assertion',run.stderr)

    def test_texture_handle_bit32_signed_low_word_and_descriptor_domain(self):
        self.run_graphics_probe('probe_texture_handle.c', 'TEXTURE_HANDLE_SIGN_EXTENSION_BIT32_AND_DOMAIN_OK')

    def test_command_memory_refill_accounting_rw_tokens_and_error_unlock(self):
        self.run_graphics_probe('probe_command_memory.c', 'COMMAND_MEMORY_REAL_STORAGE_CALLBACK_ACCOUNTING_AND_UNLOCK_OK')

    def test_command_callback_nested_error_native_exception_and_busy_worker_restore_state(self):
        self.run_graphics_probe('probe_command_callback_recovery.m', 'ACTUAL_NVN_ERROR_AND_OBJC_EXCEPTION_RESTORE_RECORDING_LOCKS_OK')

    def test_borrowed_command_control_storage_records_real_gpu_copy_sampling_and_depth(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'command-memory.c'; harness.write_text(COMMAND_MEMORY_SMOKE); exe = path / 'command-memory'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', result.stdout)
            self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)

    def test_queue_budgets_use_real_validated_storage_for_gpu_copy_and_draw(self):
        self.run_graphics_probe('probe_queue_arena_gpu.c', 'GPU_COPY_CLEAR_DRAW_OK')

    def test_queue_arena_exact_capacity_rw_snapshot_overlap_and_preflight(self):
        self.run_graphics_probe('probe_queue_arena.c', 'CPU_QUEUE_ARENA_EXACT_CAPACITY_RW_OVERLAP_AND_PREFLIGHT_OK')

    def test_queue_flags_use_real_ordered_metal_queue_and_reject_unimplemented_policies(self):
        smoke = METAL_SMOKE.replace('API(nvnQueueBuilderSetDefaults);', 'API(nvnQueueBuilderSetDefaults);API(nvnQueueBuilderSetFlags);')
        smoke = smoke.replace('nvnQueueBuilderSetDevice(&qb,&device);',
            'nvnQueueBuilderSetFlags(&qb,(argc>1&&!strcmp(argv[1],"bad-flags"))?NVN_QUEUE_BUILDER_FLAGS_NO_ZCULL:NVN_QUEUE_BUILDER_FLAGS_NO_FRAGMENT_INTERLOCK);nvnQueueBuilderSetDevice(&qb,&device);')
        smoke = smoke.replace('    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs"));',
                              QUEUE_INTERLOCK_FEATURE + '    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs"));')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); graphics = ROOT / 'native/botw_graphics'
            harness = path / 'flags.c'; harness.write_text(smoke); exe = path / 'flags'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(harness), '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)
            self.assertIn('Fragment interlock', result.stderr)
            # The public readback promises completion even without QueueFinish;
            # its transfer queue must wait for the separate NVN producer queue.
            harness.write_text(smoke.replace('nvnQueueFinish(&queue);', '').replace('API(nvnQueueFinish);', ''))
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(harness), '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for _ in range(3):
                result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)
            result = subprocess.run([str(exe), 'bad-flags'], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('NO_FRAGMENT_INTERLOCK=1', result.stderr)

    def test_all_astc_formats_upload_partial_blocks_and_cross_gobs_to_gpu_pixels(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); graphics = ROOT / 'native/botw_graphics'
            exe = path / 'astc'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(graphics / 'tools/probe_astc.m'),
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            if result.returncode in (77, 78):
                self.skipTest('No Metal device with ASTC family support')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cases = [json.loads(row) for row in result.stdout.splitlines()]
            self.assertEqual(len(cases), 112)
            self.assertEqual(sum(row['pixels'] for row in cases), 81634)
            self.assertEqual(len({tuple(row['footprint']) for row in cases}), 14)
            self.assertEqual({(row['srgb'], row['size_case']) for row in cases},
                             {(srgb, size) for srgb in (False, True) for size in range(4)})
            self.assertTrue(all(row['pool_end_exact'] and row['max_channel_error'] <= 1 for row in cases))
            self.assertEqual({tuple(row['first_rgba']) for row in cases if not row['srgb']}, {(64, 128, 192, 255)})
            self.assertEqual({tuple(row['first_rgba']) for row in cases if row['srgb']}, {(13, 55, 134, 255)})
            self.assertIn('ASTC_NVN_METAL_SAMPLING_OK cases=112 pixels=81634', result.stderr)

    def test_capability_queries_report_unsupported_features_and_preserve_unknown_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'capabilities.c'; harness.write_text(CAPABILITY_SMOKE); exe = path / 'capabilities'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('NVN_UNKNOWN_QUERY_DIAGNOSTIC_PRESERVES_OUTPUT_OK', result.stdout)

    def test_descriptor_abi_reserved_indices_and_sampler_count_reach_gpu_pixels(self):
        smoke = RESOURCE_SMOKE.replace('page*2', 'page*16')
        smoke = smoke.replace('API(nvnDeviceGetTextureHandle);', 'API(nvnDeviceGetTextureHandle);API(nvnDeviceGetInteger);')
        smoke = smoke.replace('    NVNtexturePool tp; NVNsamplerPool sp;', DESCRIPTOR_QUERY_FEATURE + '    NVNtexturePool tp; NVNsamplerPool sp;')
        smoke = smoke.replace('nvnTexturePoolInitialize(&tp,&pool,0,16)', 'nvnTexturePoolInitialize(&tp,&pool,163840,reservedTextures+1)')
        smoke = smoke.replace('nvnSamplerPoolInitialize(&sp,&pool,1024,16)',
            'nvnSamplerPoolInitialize(&sp,&pool,32768,(argc>1&&!strcmp(argv[1],"oversized-sampler"))?4097:4096)')
        smoke = smoke.replace('nvnTexturePoolRegisterTexture(&tp,3,&tiled,NULL);',
            'nvnTexturePoolRegisterTexture(&tp,reservedTextures+(argc>1&&!strcmp(argv[1],"texture-index")),&tiled,NULL);')
        smoke = smoke.replace('nvnSamplerPoolRegisterSampler(&sp,4,&sampler);',
            'nvnSamplerPoolRegisterSampler(&sp,reservedSamplers,&sampler);nvnSamplerPoolRegisterSampler(&sp,(argc>1&&!strcmp(argv[1],"sampler-index"))?4096:4095,&sampler);')
        smoke = smoke.replace('nvnDeviceGetTextureHandle(&device,3,4)', 'nvnDeviceGetTextureHandle(&device,reservedTextures,4095)')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'descriptors.c'; harness.write_text(smoke); exe = path / 'descriptors'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', result.stdout)
            for mode, expected in (('oversized-sampler', 'sampler descriptor count exceeds 4096'),
                                   ('sampler-index', 'sampler descriptor index/type'),
                                   ('texture-index', 'texture descriptor index/view')):
                result = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_texture_defaults_preserve_device_set_first_through_gpu_sampling_and_depth(self):
        smoke = RESOURCE_SMOKE.replace(
            'nvnTextureBuilderSetDefaults(&tb); nvnTextureBuilderSetDevice(&tb,&device);',
            'nvnTextureBuilderSetDevice(&tb,&device); nvnTextureBuilderSetDefaults(&tb);').replace(
            'nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);',
            'nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetDefaults(&tb);')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'texture-defaults.c'; harness.write_text(smoke); exe = path / 'texture-defaults'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', result.stdout)
            self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)

    def test_graphics_texture_handles_decode_low_word_for_vertex_and_fragment_gpu_sampling(self):
        smoke = RESOURCE_SMOKE.replace('page*2', '(64u*1024u*1024u)')
        smoke = smoke.replace('    NVNtexturePool tp; NVNsamplerPool sp;',
            '    int textureIndex=(int)strtoul(argv[1],NULL,0),samplerIndex=(int)strtoul(argv[2],NULL,0);\n'
            '    NVNtexturePool tp; NVNsamplerPool sp;')
        smoke = smoke.replace('nvnTexturePoolInitialize(&tp,&pool,0,16)', 'nvnTexturePoolInitialize(&tp,&pool,262144,0x100000)')
        smoke = smoke.replace('nvnSamplerPoolInitialize(&sp,&pool,1024,16)', 'nvnSamplerPoolInitialize(&sp,&pool,131072,4096)')
        smoke = smoke.replace('nvnTexturePoolRegisterTexture(&tp,3,&tiled,NULL);', 'nvnTexturePoolRegisterTexture(&tp,textureIndex,&tiled,NULL);')
        smoke = smoke.replace('nvnSamplerPoolRegisterSampler(&sp,4,&sampler);', 'nvnSamplerPoolRegisterSampler(&sp,samplerIndex,&sampler);')
        smoke = smoke.replace('nvnDeviceGetTextureHandle(&device,3,4);',
            'nvnDeviceGetTextureHandle(&device,textureIndex,samplerIndex);\n'
            '    if(argc>3)handleTexture=((uint64_t)(uint32_t)strtoul(argv[3],NULL,0)<<32)|(uint32_t)handleTexture;')
        smoke = smoke.replace('constant float4& u [[buffer(0)]]) {return {float4(v.p+u.xy,u.z,1)};}',
            'constant float4& u [[buffer(0)]],texture2d<float> t [[texture(3)]],sampler s [[sampler(0)]]) '
            '{return {float4(v.p+u.xy,u.z*t.sample(s,float2(.75,.75),level(0)).b,1)};}')
        smoke = smoke.replace('vb[]={{BOTW_GRAPHICS_UNIFORM,5,0}};',
            'vb[]={{BOTW_GRAPHICS_UNIFORM,5,0},{BOTW_GRAPHICS_TEXTURE,9,3},{BOTW_GRAPHICS_SAMPLER,9,0}};')
        smoke = smoke.replace('resourceMSL,"rv",vb,1)', 'resourceMSL,"rv",vb,3)')
        smoke = smoke.replace('nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,9,handleTexture);',
            'nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_VERTEX,9,handleTexture);'
            'nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,9,handleTexture);')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'texture-handles.c'; harness.write_text(smoke); exe = path / 'texture-handles'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for texture, sampler in ((0x7ffff, 0x7ff), (0x80000, 0x800), (0xfffff, 0xfff)):
                for upper in (None, 0, 1, 0xffffffff, 0x13579bdf, 0x80000001):
                    args = [str(exe), hex(texture), hex(sampler)] + ([] if upper is None else [hex(upper)])
                    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
                    if result.returncode == 77:
                        self.skipTest('No Metal device')
                    self.assertEqual(result.returncode, 0, f'{args}\n{result.stdout}{result.stderr}')
                    self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', result.stdout)

    def test_upper_left_window_origin_matches_ndc_viewport_and_scissor_pixels(self):
        smoke = METAL_SMOKE.replace('API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);',
            'API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize); API(nvnDeviceSetWindowOriginMode); API(nvnCommandBufferSetViewport);')
        smoke = smoke.replace('CHECK(nvnDeviceInitialize(&device,&db));',
            'CHECK(nvnDeviceInitialize(&device,&db)); nvnDeviceSetWindowOriginMode(&device,argc>1?NVN_WINDOW_ORIGIN_MODE_LOWER_LEFT:NVN_WINDOW_ORIGIN_MODE_UPPER_LEFT);')
        smoke = smoke.replace('float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};',
            'float2 p[3]={float2(-1,-1),float2(1,1),float2(-1,1)};')
        smoke = smoke.replace('    nvnMemoryPoolFinalize(&pool);', ORIGIN_FEATURE + '    nvnMemoryPoolFinalize(&pool);')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'origin.c'; harness.write_text(smoke); exe = path / 'origin'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_UPPER_LEFT_NDC_VIEWPORT_SCISSOR_OK', result.stdout)
            result = subprocess.run([str(exe), 'lower-left'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('UpperLeft=1', result.stderr)

    def test_vi_display_lifetime_and_generic_nvn_lookup_match_game_boot_abi(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'platform.m'; harness.write_text(PLATFORM_SMOKE)
            exe = path / 'platform'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for mode, expected in (([], 'VI_METAL_DISPLAY_LIFETIME_AND_DIMENSIONS_OK'),
                                   (['lookup'], 'NVN_GENERIC_LOOKUP_AND_52_32_OK'),
                                   (['version'], 'NVN_GENERIC_LOOKUP_AND_52_32_OK')):
                result = subprocess.run([str(exe), *mode], capture_output=True, text=True, timeout=20)
                if result.returncode == 77:
                    self.skipTest('No Metal device')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)

    def test_triangle_strip_topology_reaches_full_quad_pixels_and_instance_writeback(self):
        ordinary = METAL_SMOKE.replace('float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};',
            'float2 p[4]={float2(-1,-1),float2(1,-1),float2(-1,1),float2(1,1)};')
        ordinary = ordinary.replace('nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);',
                                    'nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLE_STRIP,0,4);')
        strip = STORAGE_FEATURE.replace('corner>=6', 'corner>=4').replace('uint value[12]', 'uint value[8]')
        strip = strip.replace('float2 p[6]={float2(-1,-1),float2(1,-1),float2(-1,1),float2(-1,1),float2(1,-1),float2(1,1)};',
                              'float2 p[4]={float2(-1,-1),float2(1,-1),float2(-1,1),float2(1,1)};')
        strip = strip.replace('index*6+corner', 'index*4+corner').replace('i<12', 'i<8').replace('(i/6+1)*10+i%6+1', '(i/4+1)*10+i%4+1')
        strip = strip.replace('writable={gpu+1280,48}', 'writable={gpu+1280,32}').replace('profile={6,2,3', 'profile={4,2,3')
        strip = strip.replace('NVN_DRAW_PRIMITIVE_TRIANGLES,3,6,5', 'NVN_DRAW_PRIMITIVE_TRIANGLE_STRIP,3,4,5')
        instanced = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', strip + '    nvnMemoryPoolFinalize(&pool);')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            for name, smoke in (('ordinary', ordinary), ('instanced', instanced)):
                harness = path / (name + '.c'); harness.write_text(smoke); exe = path / name
                result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                    '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                    '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True)
                if result.returncode == 77:
                    self.skipTest('No Metal device')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)
                if name == 'instanced':
                    self.assertIn('GPU_STORAGE_INSTANCING_BASE_IDS_OK', result.stdout)

    def test_array_texture_layers_mips_scalar_types_and_cpu_updates_reach_gpu_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'array.c'
            smoke = METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', ARRAY_FEATURE + '    nvnMemoryPoolFinalize(&pool);')
            harness.write_text(smoke.replace('free(memory);', 'free(arrayMemory);free(memory);'))
            exe = path / 'array'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for args in ([], ['r8']):
                result = subprocess.run([str(exe), *args], capture_output=True, text=True)
                if result.returncode == 77:
                    self.skipTest('No Metal device')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('GPU_ARRAY_LAYER_MIP_COLORS_OK', result.stdout)
            for mode in ('linear-array', 'bad-layers', 'bad-levels'):
                result = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, mode + result.stderr)
                self.assertIn('Unsupported texture dimension', result.stdout)
            for mode, expected in (('array-target', 'array render targets'), ('wrong-scalar', 'texture scalar type')):
                result = subprocess.run([str(exe), mode], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_finite_bundle_recomputes_bounds_and_draws_with_nonzero_base_ids(self):
        import hashlib
        import json
        module = BnshTests().parser()
        original, control, code, reflection, descriptors = synthetic_finite_shader()
        fragment_control, fragment_code = bytearray(64), bytearray(128)
        struct.pack_into('<I', fragment_control, 0, 0x98761234)
        struct.pack_into('<I', fragment_code, 0, 0x12345678)
        fragment_control[8] = fragment_code[8] = 7
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            inputs.mkdir(); converted.mkdir()
            records = []
            for stage, ctl, binary, source, reflected in (
                    ('vertex', control, code, original, reflection),
                    ('fragment', fragment_control, fragment_code,
                     '#include <metal_stdlib>\nusing namespace metal;struct O{float4 p [[position]];float4 color;};'
                     'fragment float4 fs(O in [[stage_in]]){return in.color;}', {})):
                ctl_hash, code_hash = hashlib.sha256(ctl).hexdigest(), hashlib.sha256(binary).hexdigest()
                (inputs / (ctl_hash + '.control')).write_bytes(ctl)
                (inputs / (code_hash + '.nvn')).write_bytes(binary)
                (converted / (code_hash + '.metal')).write_text(source)
                records.append({'control_sha256': ctl_hash, 'code_sha256': code_hash, 'stage': stage,
                                'container': 'synthetic', 'variation': 0, 'reflection': reflected})
            (inputs / 'inputs.json').write_text(json.dumps({'records': records}))
            (converted / 'translation.json').write_text(json.dumps({'results': [
                {'code_sha256': records[0]['code_sha256'], 'storage_descriptors': descriptors}]}))
            self.assertEqual(module.stage_shader_bundle(inputs, converted, bundle)['ready'], 2)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            vertex = next(row for row in manifest['entries'] if row['stage'] == 'vertex')
            # A forged length claim must not weaken the independently computed
            # profile. The short-storage draw below still requires 32 bytes.
            vertex['draw_profile']['minimum_storage_bytes'] = [0, 1, 1]
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            source = ROOT / 'native/botw_graphics'
            feature = r'''
    API(nvnCommandBufferBindStorageBuffer);API(nvnCommandBufferDrawArraysInstanced);
    const float instances[]={-.5,1,0,0,.5,0,1,0};memcpy(memory+1024,instances,sizeof instances);
    memcpy(controls[0],VERTEX_CONTROL,sizeof VERTEX_CONTROL-1);memcpy(controls[1],FRAGMENT_CONTROL,sizeof FRAGMENT_CONTROL-1);
    memcpy(memory+128,VERTEX_CODE,sizeof VERTEX_CODE-1);memcpy(memory+768,FRAGMENT_CODE,sizeof FRAGMENT_CODE-1);sd[1].data=gpu+768;
    botw_graphics_shader_bundle_result loaded;char error[1024];
    if(!botw_graphics_load_shader_bundle(argv[1],&loaded,error,sizeof error)){fprintf(stderr,"%s\n",error);return 3;}
    CHECK(loaded.registered_entries==2&&loaded.unsupported_entries==0);CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
    nvnCommandBufferBindStorageBuffer(&cb,NVN_SHADER_STAGE_VERTEX,0,gpu+1024,(argc>2&&!strcmp(argv[2],"short"))?16:32);
    int first=(argc>2&&!strcmp(argv[2],"large-base"))?107:3;
    int base=(argc>2&&!strcmp(argv[2],"large-base"))?105:5;
    nvnCommandBufferDrawArraysInstanced(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,first,
      (argc>2&&!strcmp(argv[2],"vertices"))?17:6,base,(argc>2&&!strcmp(argv[2],"instances"))?65:2);
    handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==255&&pixels[1]==0&&pixels[12]==0&&pixels[13]==255);
    puts("FINITE_BUNDLE_GPU_BASE_IDS_OK");
'''
            for name, value in (('VERTEX_CONTROL', control), ('VERTEX_CODE', code),
                                ('FRAGMENT_CONTROL', fragment_control), ('FRAGMENT_CODE', fragment_code)):
                feature = '    static const char ' + name + '[]="' + ''.join('\\x%02x' % x for x in value) + '";\n' + feature
            smoke = METAL_SMOKE.replace('#include "graphics.h"', '#include "graphics.h"\n#include "shader_bundle.h"')
            smoke = smoke.replace('return n <= 64', 'return n <= 0x708')
            smoke = smoke.replace('controls[2][64]', 'controls[2][0x708]')
            smoke = smoke.replace('    nvnMemoryPoolFinalize(&pool);', feature + '    nvnMemoryPoolFinalize(&pool);')
            harness = path / 'finite.c'; harness.write_text(smoke)
            exe = path / 'finite'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source), str(source / 'graphics.m'), str(source / 'shader_bundle.m'), str(harness),
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for args in ([], ['large-base']):
                result = subprocess.run([str(exe), str(bundle), *args], capture_output=True, text=True)
                if result.returncode == 77:
                    self.skipTest('No Metal device')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('FINITE_BUNDLE_GPU_BASE_IDS_OK', result.stdout)
                self.assertIn('relative_vertices=16 relative_instances=64 storage_slot=0 bytes_at_max=1024', result.stderr)
            for mode, expected in (('short', 'storage range'), ('vertices', 'vertex count'), ('instances', 'instance count')):
                result = subprocess.run([str(exe), str(bundle), mode], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, mode)
                self.assertIn(expected, result.stderr)
            asset = bundle / vertex['msl']['path']
            saved = asset.read_text()
            for replacement, expected in (
                    (saved.replace('0xbf800000u', '0xbf800001u', 1), 'Original finite c1 bits'),
                    (saved.replace('gl_VertexIndex-gl_BaseVertex', 'gl_VertexIndex'), 'external finite profile input'),
                    (saved.replace('gl_InstanceIndex-gl_BaseInstance', 'gl_InstanceIndex'), 'external finite profile input'),
                    (saved.replace('gl_VertexIndex-gl_BaseVertex', 'gl_VertexIndex-gl_BaseVertex*2u'), 'external finite profile input'),
                    (saved.replace('gl_InstanceIndex-gl_BaseInstance', 'gl_InstanceIndex-gl_BaseInstance*2u'), 'external finite profile input'),
                    (saved.replace('float x=', '_4+=1u;float x='), 'Ambiguous finite profile assignment'),
                    (saved.replace('ssbo0[_3].x', '(&ssbo0[_3])->x'), 'address escape'),
                    (saved.replace('ssbo0[_3].x', '(*(&(ssbo0[_3])+100u)).x'), 'address escape'),
                    (saved.replace('botw_c1_words[_5]', '(*(&(botw_c1_words[_5])+100u))'), 'address escape'),
                    (saved.replace('float x=', 'thread uint& alias=_4;alias+=100u;float x='), 'address escape'),
                    (saved.replace('float x=', '(_4)+=100u;float x='), 'Ambiguous finite profile assignment'),
                    (saved.replace('float x=', 'if(_1){uint _999=_4;}float x=').replace('_5 = _4*4u', '_5 = _999*4u'), 'Conditional finite profile assignment'),
                    (saved.replace('using namespace metal;', 'using namespace metal;float abs(float x){return x+100;}'), 'helper definition'),
                    (saved.replace('float x=', 'frexp(1.0,_4);float x='), 'mutable finite source function'),
                    (saved.replace('#include <metal_stdlib>', '#include <metal_stdlib>\n#define abs(x) (x+100)'), 'preprocessor directive'),
                    (saved.replace('float x=', 'float unproven[6];float x='), 'Unproven extra array')):
                self.assertNotEqual(replacement, saved)
                data = replacement.encode(); asset.write_bytes(data)
                vertex['msl'].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                (bundle / 'manifest.json').write_text(json.dumps(manifest))
                result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_dynamic_c1_index_probe_executes_exact_finite_vertex_inputs(self):
        import json
        tool = ROOT / 'native/botw_graphics/tools/probe_msl_indices.m'
        self.assertTrue(tool.exists(), 'Offline Metal index runner is missing')
        module = BnshTests().parser()
        control, code = bytearray(0x708), bytearray(0x180)
        struct.pack_into('<II', control, 0x700, 0x80, 0x180); struct.pack_into('<f', code, 0x88, 1.0)
        source = ('struct U{uint data[64];};struct F{float data[64];}; vertex float4 main0('
                  'uint gl_VertexIndex [[vertex_id]],uint gl_BaseVertex [[base_vertex]],'
                  'constant U& c1 [[buffer(1)]],constant F& c1_1 [[buffer(2)]]) {'
                  'uint _1=gl_VertexIndex-gl_BaseVertex;uint _2=_1*4u;uint _3=_1+uint(c1_1.data[2u]);'
                  'return float4(c1.data[_2]+c1.data[_3]);}')
        probe = module.build_dynamic_c1_probe(source, control, code, 4)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); msl = path / 'probe.metal'; msl.write_text(probe['msl'])
            exe = path / 'probe'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                str(tool), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe), str(msl), '4', '2'], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['indices'], [[0, 1], [4, 2], [8, 3], [12, 4]])
            source, control, code, reflection, descriptors = synthetic_storage_index_shader()
            storage = module.build_storage_index_probe(source, control, code, 'vertex', reflection, descriptors, 4)
            msl.write_text(storage['msl'])
            result = subprocess.run([str(exe), str(msl), '4', str(len(storage['expressions']))], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['indices'], [[0, 3], [16, 7], [32, 11], [48, 15]])

    def test_storage_buffer_and_instanced_draw_use_real_bindings_and_ids(self):
        implemented = (ROOT / 'native/botw_graphics/implemented.inc').read_text()
        self.assertIn('NVN_BIND(nvnCommandBufferBindStorageBuffer)', implemented)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'storage.c'
            harness.write_text(METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);',
                                                  STORAGE_FEATURE + '    nvnMemoryPoolFinalize(&pool);'))
            exe = path / 'storage'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_STORAGE_INSTANCING_BASE_IDS_OK', result.stdout)
            for mode, expected in (('short-storage', 'storage range'), ('unbound-storage', 'unbound storage'),
                                   ('negative-instance', 'instance range'), ('duplicate-storage', 'Duplicate Metal'),
                                   ('wide-driver', 'exactly 32 bits'), ('unbound-driver', 'unbound storage driver'),
                                   ('profile-vertices', 'draw profile vertex count'), ('profile-instances', 'draw profile instance count'),
                                   ('profile-storage', 'draw profile storage range'), ('invalid-profile', 'Invalid shader draw profile storage minimum')):
                result = subprocess.run([str(exe), mode], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, mode)
                self.assertIn(expected, result.stderr)

    def test_concurrent_nvn_objects_and_error_callback_escape_release_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'race.c'; harness.write_text(CONCURRENT_SMOKE)
            exe = path / 'race'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-pthread', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_CONCURRENT_16000_OBJECTS_AND_ERROR_ESCAPE_OK', result.stdout)

    def test_static_c1_bundle_loader_matches_original_bits(self):
        import hashlib
        import json
        module = BnshTests().parser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            inputs.mkdir(); converted.mkdir()
            control, code = bytearray(0x708), bytearray(0x100)
            struct.pack_into('<I', control, 0, 0x98761234)
            struct.pack_into('<II', control, 0x700, 0x80, 0x100)
            struct.pack_into('<I', code, 0, 0x12345678)
            struct.pack_into('<I', code, 0x80, 0x3e800000)
            ctl_hash, code_hash = hashlib.sha256(control).hexdigest(), hashlib.sha256(code).hexdigest()
            (inputs / (ctl_hash + '.control')).write_bytes(control)
            (inputs / (code_hash + '.nvn')).write_bytes(code)
            (inputs / 'inputs.json').write_text(json.dumps({'records': [{'control_sha256': ctl_hash,
                'code_sha256': code_hash, 'stage': 'fragment', 'container': 'fixture', 'variation': 0}]}))
            (converted / (code_hash + '.metal')).write_text('#include <metal_stdlib>\nusing namespace metal; '
                'struct C {float data[16384];}; fragment float4 fs(constant C& c1 [[buffer(0)]]) '
                '{return float4(c1.data[0u],0,0,1);}')
            self.assertEqual(module.stage_shader_bundle(inputs, converted, bundle)['ready'], 1)
            harness = path / 'bundle.c'; harness.write_text(BUNDLE_SMOKE)
            source = ROOT / 'native/botw_graphics'
            exe = path / 'bundle-test'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source), str(source / 'graphics.m'), str(source / 'shader_bundle.m'), str(harness),
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('REGISTERED=1 UNSUPPORTED=0 TOTAL=1', result.stdout)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            item = manifest['entries'][0]['constant_specializations'][0]
            for key, value, expected in (('bits', '3e800001', 'bits mismatch'), ('count', 2, 'expression/count')):
                saved = item[key]; item[key] = value
                (bundle / 'manifest.json').write_text(json.dumps(manifest))
                result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                item[key] = saved

    def test_original_static_c1_constant_reaches_gpu_pixels(self):
        module = BnshTests().parser()
        control, code = bytearray(0x708), bytearray(0x100)
        struct.pack_into('<II', control, 0x700, 0x80, 0x100)
        struct.pack_into('<I', code, 0x80, 0x3e800000)
        fragment = ('struct C {float data[16384];}; fragment float4 fs(constant C& c1 [[buffer(0)]]) '
                    '{return float4(c1.data[0u],0,0,1);}')
        specialized, _ = module.specialize_static_c1(fragment, control, code)
        smoke = METAL_SMOKE.replace('fragment float4 fs() { return float4(1,0,0,1); }', specialized)
        smoke = smoke.replace('pixels[0]==255 && pixels[1]==0', 'pixels[0]>=63 && pixels[0]<=64 && pixels[1]==0')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            harness = path / 'constant.c'; harness.write_text(smoke)
            source = ROOT / 'native/botw_graphics/graphics.m'
            exe = path / 'constant'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(source.parent), str(source), str(harness), '-framework', 'Foundation', '-framework', 'Metal',
                '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)

    def test_real_gpu_copy_clear_and_pipeline_and_strict_unsupported_api(self):
        source = ROOT / 'native/botw_graphics/graphics.m'
        self.assertTrue(source.exists(), 'Native Metal bridge is missing')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            harness = path / 'smoke.c'
            harness.write_text(METAL_SMOKE)
            exe = path / 'smoke'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                '-Werror', '-Wno-deprecated-declarations', '-I', str(source.parent),
                str(source), str(harness), '-framework', 'Foundation', '-framework',
                'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device is available in this execution context')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)
            unsupported = subprocess.run([str(exe), 'unsupported'], capture_output=True, text=True)
            self.assertNotEqual(unsupported.returncode, 0)
            self.assertIn('nvnCommandBufferDispatchCompute', unsupported.stderr)
            partial = subprocess.run([str(exe), 'partial-clear'], capture_output=True, text=True)
            self.assertNotEqual(partial.returncode, 0)
            self.assertIn('scissored clear', partial.stderr)

    def test_vertex_uniform_sampler_block_linear_and_depth_pixels(self):
        implemented = (ROOT / 'native/botw_graphics/implemented.inc').read_text()
        self.assertIn('NVN_BIND(nvnCommandBufferBindUniformBuffer)', implemented,
                      'GPU resource binding implementation is missing')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'resources.c'
            fragment = ('struct C {float data[16384];}; fragment float4 rf(O v [[stage_in]],'
                        'constant C& c3 [[buffer(1)]],texture2d<float> t [[texture(3)]],sampler s [[sampler(0)]]) '
                        '{return t.sample(s,float2(.75,.75))*float4(c3.data[0u],c3.data[1u],c3.data[2u],c3.data[3u]);}')
            narrowed, ranges = BnshTests().parser().bound_static_uniforms(fragment)
            self.assertEqual(ranges[0]['bytes'], 16)
            original = ('fragment float4 rf(O v [[stage_in]],constant float4& c [[buffer(1)]],'
                        'texture2d<float> t [[texture(3)]],sampler s [[sampler(0)]]) '
                        '{return t.sample(s,float2(.75,.75))*c;}')
            self.assertIn(original, RESOURCE_SMOKE)
            harness.write_text(RESOURCE_SMOKE.replace(original, narrowed.replace('\n', '\\n')))
            exe = path / 'resources'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                '-Werror', '-Wno-deprecated-declarations', '-I', str(source.parent),
                str(source), str(harness), '-framework', 'Foundation', '-framework',
                'Metal', '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', result.stdout)
            for mode, expected in (('bad-bindings', 'binding map'),
                                   ('short-uniform', 'uniform range'),
                                   ('short-vertex', 'vertex range')):
                result = subprocess.run([str(exe), mode], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, mode)
                self.assertIn(expected, result.stderr)


    def test_private_bundle_loader_checks_hashes_paths_and_unsupported_counts(self):
        import json
        source = ROOT / 'native/botw_graphics/shader_bundle.m'
        self.assertTrue(source.exists(), 'Private shader loader is missing')
        module = BnshTests().parser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            rows = module.extract_shader_inputs(synthetic_bnsh(), inputs)
            converted.mkdir()
            for row in rows:
                if row['stage'] == 'fragment':
                    row['reflection'] = {'uniforms': [{'name': 'fixtureUbo', 'slot': 0}],
                                         'samplers': [{'name': 'fixtureTexture', 'slot': 1}]}
                msl = ('#include <metal_stdlib>\nusing namespace metal; vertex float4 main0(uint i [[vertex_id]]) {return float4(0,0,0,1);}'
                       if row['stage'] == 'vertex' else
                       '#include <metal_stdlib>\nusing namespace metal; struct Block {float data[16384];}; fragment float4 main0(constant Block& c3 [[buffer(2)]], texture2d<float> fs_tex0_28 [[texture(3)]], sampler fs_tex0_28Smplr [[sampler(0)]]) {return float4(c3.data[0u],c3.data[1u],c3.data[2u],c3.data[3u])*fs_tex0_28.sample(fs_tex0_28Smplr,float2(0));}')
                (converted / (row['code_sha256'] + '.metal')).write_text(msl)
            description = json.loads((inputs / 'inputs.json').read_text())
            description['records'] = rows
            (inputs / 'inputs.json').write_text(json.dumps(description))
            staged = module.stage_shader_bundle(inputs, converted, bundle)
            self.assertEqual((staged['ready'], staged['unsupported']), (2, 0))
            harness = path / 'bundle.c'
            harness.write_text(BUNDLE_SMOKE)
            exe = path / 'bundle-test'
            graphics = source.parent
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(source), str(harness),
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('REGISTERED=2 UNSUPPORTED=0 TOTAL=2', result.stdout)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            entry = manifest['entries'][0]
            for field, invalid, expected in (
                    ('texture_types', [{'nvn_slot': 0, 'metal_slot': 0, 'type': 'cube'}], 'texture'),
                    ('vertex_inputs', [0], 'vertex'),
                    ('uniform_ranges', [{'metal_slot': 2, 'nvn_slot': 0, 'bytes': 4, 'proof': 'fixed_indices'}], 'uniform'),
                    ('constant_specializations', [{'bank': 1, 'offset': 0, 'bits': '3e800000', 'type': 'float', 'count': 1}], 'c1')):
                saved = entry.get(field); entry[field] = invalid
                (bundle / 'manifest.json').write_text(json.dumps(manifest))
                result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, field)
                self.assertIn(expected, result.stderr)
                if saved is None:
                    entry.pop(field)
                else:
                    entry[field] = saved
            fragment = next(x for x in manifest['entries'] if x['stage'] == 'fragment')
            fragment['reflection']['uniforms'].append({'name': 'misleadingSlot', 'slot': 1})
            fragment['bindings'][0]['nvn_slot'] = 1
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('source resource binding map', result.stderr)
            fragment['reflection']['uniforms'].pop()
            fragment['bindings'][0]['nvn_slot'] = 0
            record = manifest['stage_records'][0]
            saved = record['entry']; record['entry'] = 100
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('stage record', result.stderr)
            record['entry'] = saved
            manifest['entries'][0]['status'] = 'unsupported'
            manifest['entries'][0]['reason'] = 'Unknown private constant bank c1'
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('REGISTERED=1 UNSUPPORTED=1 TOTAL=2', result.stdout)
            self.assertIn('c1', result.stderr)
            manifest['entries'][0]['status'] = 'ready'
            entry = manifest['entries'][0]
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            code = bundle / entry['code']['path']
            original = code.read_bytes()
            damaged = bytearray(original); damaged[8] ^= 1; code.write_bytes(damaged)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SHA256', result.stderr)
            code.write_bytes(original)
            saved = entry['code']['path']; entry['code']['path'] = '../escape.nvn'
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('relative', result.stderr)
            entry['code']['path'] = saved
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            outside = path / 'outside.nvn'; outside.write_bytes(original)
            code.unlink(); code.symlink_to(outside)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('symlink', result.stderr)


QUEUE_INTERLOCK_FEATURE = r"""
    const char *interlockMSL="#include <metal_stdlib>\nusing namespace metal;fragment float4 interlock(device uint* b [[buffer(0),raster_order_group(0)]]) {b[0]+=1;return float4(1);}";
    botw_graphics_shader_binding interlockBinding={BOTW_GRAPHICS_STORAGE,0,0};
    CHECK(!botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,interlockMSL,"interlock",&interlockBinding,1));
    CHECK(strstr(botw_graphics_last_error(),"Fragment interlock"));
"""

CAPABILITY_SMOKE = r"""
#include "graphics.h"
#include "nvn_protos.h"
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
static sigjmp_buf jump;
static int output=0x12345678;
static unsigned errors;
static void error(const char *s){(void)s;++errors;siglongjmp(jump,1);}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK line %d: %s\n",__LINE__,#x);return 1;}}while(0)
int main(void){
 if(!botw_graphics_init(NULL,error,NULL))return 77;
 NVNT_nvnDeviceBuilderSetDefaults defaults=(NVNT_nvnDeviceBuilderSetDefaults)botw_nvn_bootstrap_loader("nvnDeviceBuilderSetDefaults");
 NVNT_nvnDeviceInitialize initialize=(NVNT_nvnDeviceInitialize)botw_nvn_bootstrap_loader("nvnDeviceInitialize");
 NVNT_nvnDeviceGetInteger integer=(NVNT_nvnDeviceGetInteger)botw_nvn_bootstrap_loader("nvnDeviceGetInteger");
 NVNdeviceBuilder b;NVNdevice d;defaults(&b);CHECK(initialize(&d,&b));
 static const NVNdeviceInfo unavailable[]={NVN_DEVICE_INFO_SUPPORTS_MIN_MAX_FILTERING,NVN_DEVICE_INFO_SUPPORTS_STENCIL8_FORMAT,
 NVN_DEVICE_INFO_SUPPORTS_CONSERVATIVE_RASTER,NVN_DEVICE_INFO_SUPPORTS_ZERO_FROM_UNMAPPED_VIRTUAL_POOL_PAGES,
 NVN_DEVICE_INFO_SUPPORTS_PASSTHROUGH_GEOMETRY_SHADERS,NVN_DEVICE_INFO_SUPPORTS_VIEWPORT_SWIZZLE,
 NVN_DEVICE_INFO_SUPPORTS_SPARSE_TILED_PACKAGED_TEXTURES,NVN_DEVICE_INFO_SUPPORTS_ADVANCED_BLEND_MODES,
 NVN_DEVICE_INFO_SUPPORTS_DRAW_TEXTURE,NVN_DEVICE_INFO_SUPPORTS_TARGET_INDEPENDENT_RASTERIZATION,
 NVN_DEVICE_INFO_SUPPORTS_FRAGMENT_COVERAGE_TO_COLOR,NVN_DEVICE_INFO_SUPPORTS_POST_DEPTH_COVERAGE,
 NVN_DEVICE_INFO_SUPPORTS_IMAGES_USING_TEXTURE_HANDLES,NVN_DEVICE_INFO_SUPPORTS_SAMPLE_LOCATIONS};
 if(sigsetjmp(jump,1)){fprintf(stderr,"availability query unexpectedly failed: %s\n",botw_graphics_last_error());return 1;}
 for(unsigned i=0;i<sizeof unavailable/sizeof *unavailable;i++){output=0x12345678;integer(&d,unavailable[i],&output);CHECK(output==0);}
 static const NVNdeviceInfo sizes[]={NVN_DEVICE_INFO_TEXTURE_DESCRIPTOR_SIZE,NVN_DEVICE_INFO_SAMPLER_DESCRIPTOR_SIZE,
 NVN_DEVICE_INFO_RESERVED_TEXTURE_DESCRIPTORS,NVN_DEVICE_INFO_RESERVED_SAMPLER_DESCRIPTORS};
 for(unsigned i=0;i<4;i++){output=0x12345678;integer(&d,sizes[i],&output);CHECK(output==(i<2?32:256));}
 output=0x12345678;
 if(!sigsetjmp(jump,1)){integer(&d,(NVNdeviceInfo)0x7ffe,&output);CHECK(0);}
 CHECK(output==0x12345678);CHECK(errors==1);
 CHECK(strstr(botw_graphics_last_error(),"query=0x7ffe"));
 botw_graphics_shutdown();puts("NVN_UNKNOWN_QUERY_DIAGNOSTIC_PRESERVES_OUTPUT_OK");return 0;
}
"""

DESCRIPTOR_QUERY_FEATURE = r"""
    int textureBytes=0,samplerBytes=0,reservedTextures=0,reservedSamplers=0;
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_TEXTURE_DESCRIPTOR_SIZE,&textureBytes);
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_SAMPLER_DESCRIPTOR_SIZE,&samplerBytes);
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_RESERVED_TEXTURE_DESCRIPTORS,&reservedTextures);
    nvnDeviceGetInteger(&device,NVN_DEVICE_INFO_RESERVED_SAMPLER_DESCRIPTORS,&reservedSamplers);
    CHECK(textureBytes==32&&samplerBytes==32&&reservedTextures==256&&reservedSamplers==256);
"""

ORIGIN_FEATURE = r"""
    CHECK(pixels[0]==255 && pixels[1]==0);
    CHECK(pixels[60]==0 && pixels[61]==255);
    nvnCommandBufferBeginRecording(&cb);
    nvnCommandBufferClearColor(&cb,0,color,15);
    nvnCommandBufferSetViewport(&cb,0,0,4,2);
    nvnCommandBufferSetScissor(&cb,0,0,4,1);
    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
    handle=nvnCommandBufferEndRecording(&cb); nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    for(unsigned y=0;y<4;y++){unsigned p=y*16;CHECK(pixels[p]==(y?0:255));CHECK(pixels[p+1]==(y?255:0));}
    puts("GPU_UPPER_LEFT_NDC_VIEWPORT_SCISSOR_OK");
"""

PLATFORM_SMOKE = r"""
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#include "graphics.h"
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#include <math.h>
#define CHECK(x) do{if(!(x)){fprintf(stderr,"CHECK line %d: %s\n",__LINE__,#x);return 1;}}while(0)
static int errors;
static jmp_buf escape;
static void error(const char *s){(void)s;errors++;longjmp(escape,1);}
int main(int argc,char **argv){@autoreleasepool{
 if(!MTLCreateSystemDefaultDevice())return 77;
 if(argc>1 && (!strcmp(argv[1],"lookup")||!strcmp(argv[1],"version"))){
  CHECK(botw_graphics_init(NULL,error,NULL));
  PFNNVNDEVICEGETPROCADDRESSPROC lookup=(PFNNVNDEVICEGETPROCADDRESSPROC)botw_nvn_bootstrap_loader("nvnDeviceGetProcAddress");
  CHECK(lookup);
  if(setjmp(escape)){fprintf(stderr,"generic lookup failed: %s\n",botw_graphics_last_error());return 1;}
  if(!strcmp(argv[1],"lookup")){CHECK(lookup(NULL,"nvnDeviceBuilderSetDefaults"));CHECK(lookup(NULL,"nvnCommandBufferDispatchCompute"));CHECK(!lookup(NULL,"nvnDefinitelyUnknown"));CHECK(errors==0);CHECK(strstr(botw_graphics_last_error(),"Unknown NVN entry"));}
  NVNdevice device;NVNdeviceBuilder builder;
  PFNNVNDEVICEBUILDERSETDEFAULTSPROC defaults=(PFNNVNDEVICEBUILDERSETDEFAULTSPROC)botw_nvn_bootstrap_loader("nvnDeviceBuilderSetDefaults");
  defaults(&builder);
  PFNNVNDEVICEINITIALIZEPROC init=(PFNNVNDEVICEINITIALIZEPROC)botw_nvn_bootstrap_loader("nvnDeviceInitialize");CHECK(init(&device,&builder));
  PFNNVNDEVICEGETINTEGERPROC integer=(PFNNVNDEVICEGETINTEGERPROC)lookup(&device,"nvnDeviceGetInteger");
  int major=0,minor=0;integer(&device,NVN_DEVICE_INFO_API_MAJOR_VERSION,&major);integer(&device,NVN_DEVICE_INFO_API_MINOR_VERSION,&minor);
  CHECK(major==52&&minor==32);
  if(!setjmp(escape)){NVNdevice invalid={0};lookup(&invalid,"nvnDeviceInitialize");CHECK(0);}
  botw_graphics_shutdown();CHECK(errors==1);puts("NVN_GENERIC_LOOKUP_AND_52_32_OK");return 0;
 }
 CHECK(!botw_graphics_platform_initialize());
 CHECK(botw_graphics_init(NULL,error,NULL));
 CHECK(!botw_graphics_platform_initialize());CHECK(strstr(botw_graphics_last_error(),"CAMetalLayer"));
 uintptr_t display=123,layer=456,native=789;uint32_t width=11,height=22;
 CHECK(!botw_graphics_platform_open_default_display(&display));CHECK(display==123);CHECK(errors==0);
 botw_graphics_shutdown();
 CAMetalLayer *metal=[CAMetalLayer layer];metal.drawableSize=CGSizeMake(1280,720);
 CHECK(botw_graphics_init((__bridge void*)metal,error,NULL));
 CHECK(!botw_graphics_platform_open_default_display(&display));
 CHECK(botw_graphics_platform_initialize());CHECK(!botw_graphics_platform_initialize());
 CHECK(botw_graphics_platform_open_default_display(&display));CHECK(display!=0&&display!=123);
 uintptr_t duplicate=0;CHECK(!botw_graphics_platform_open_default_display(&duplicate));CHECK(!duplicate);
 CHECK(botw_graphics_platform_display_is_live(display));
 CHECK(botw_graphics_platform_display_size(display,&width,&height));CHECK(width==1280&&height==720);
 CHECK(!botw_graphics_platform_create_layer(display+1,&layer));CHECK(layer==456);
 CHECK(botw_graphics_platform_create_layer(display,&layer));CHECK(layer!=0&&layer!=456);
 CHECK(!botw_graphics_platform_create_layer(display,&duplicate));
 CHECK(!botw_graphics_platform_set_scaling_mode(layer,99));
 CHECK(!botw_graphics_platform_set_scaling_mode(layer+1,2));
 CHECK(botw_graphics_platform_set_scaling_mode(layer,2));
 CHECK([metal.contentsGravity isEqualToString:kCAGravityResize]);
 CHECK(!botw_graphics_platform_get_native_window(layer+1,&native));CHECK(native==789);
 CHECK(botw_graphics_platform_get_native_window(layer,&native));CHECK(native==(uintptr_t)(__bridge void*)metal);
 metal.drawableSize=CGSizeMake(640,480);CHECK(botw_graphics_platform_display_size(display,&width,&height));CHECK(width==640&&height==480);
 id<MTLDevice> savedDevice=metal.device;metal.device=nil;CHECK(!botw_graphics_platform_display_is_live(display));
 CHECK(!botw_graphics_platform_display_size(display,&width,&height));CHECK(width==640&&height==480);
 metal.device=savedDevice;CHECK(botw_graphics_platform_display_is_live(display));
 botw_graphics_shutdown();CHECK(!botw_graphics_platform_display_is_live(display));
 CHECK(!botw_graphics_platform_get_native_window(layer,&native));
 CHECK(botw_graphics_init((__bridge void*)metal,error,NULL));CHECK(botw_graphics_platform_initialize());
 uintptr_t next=0;CHECK(botw_graphics_platform_open_default_display(&next));CHECK(next!=display);
 CHECK(!botw_graphics_platform_create_layer(display,&duplicate));
 botw_graphics_shutdown();CHECK(errors==0);puts("VI_METAL_DISPLAY_LIFETIME_AND_DIMENSIONS_OK");return 0;
}}

"""

METAL_SMOKE = r"""
#include "graphics.h"
#include "nvn_protos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(name) NVNT_##name name = (NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"CHECK failed: %s (%s)\n", #x, botw_graphics_last_error()); return 1; } } while(0)
static const void *ctl_ptrs[2];
static int valid(const void *p, size_t n) { return n <= 64 && (p == ctl_ptrs[0] || p == ctl_ptrs[1]); }
int main(int argc, char **argv) {
    if (!botw_graphics_init(NULL, NULL, valid)) return 77;
    if (argc > 1 && !strcmp(argv[1],"unsupported")) {
        void (*bad)(void) = botw_nvn_bootstrap_loader("nvnCommandBufferDispatchCompute");
        CHECK(bad); bad(); return 2;
    }
    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);
    API(nvnMemoryPoolBuilderSetDefaults); API(nvnMemoryPoolBuilderSetDevice);
    API(nvnMemoryPoolBuilderSetStorage); API(nvnMemoryPoolBuilderSetFlags); API(nvnMemoryPoolInitialize);
    API(nvnMemoryPoolGetBufferAddress); API(nvnMemoryPoolFinalize);
    API(nvnMemoryPoolInvalidateMappedRange); API(nvnMemoryPoolFlushMappedRange);
    API(nvnQueueBuilderSetDefaults); API(nvnQueueBuilderSetDevice); API(nvnQueueInitialize);
    API(nvnQueueSubmitCommands); API(nvnQueueFinish); API(nvnQueueFenceSync);
    API(nvnSyncInitialize); API(nvnSyncWait);
    API(nvnCommandBufferInitialize); API(nvnCommandBufferBeginRecording);
    API(nvnCommandBufferEndRecording); API(nvnCommandBufferCopyBufferToBuffer);
    API(nvnCommandBufferSetRenderTargets); API(nvnCommandBufferClearColor);
    API(nvnCommandBufferBindProgram); API(nvnCommandBufferDrawArrays); API(nvnCommandBufferSetScissor);
    API(nvnTextureBuilderSetDefaults); API(nvnTextureBuilderSetDevice);
    API(nvnTextureBuilderSetSize2D); API(nvnTextureBuilderSetFormat);
    API(nvnTextureBuilderSetFlags); API(nvnTextureBuilderSetStorage);
    API(nvnTextureBuilderSetStride); API(nvnTextureInitialize);
    API(nvnProgramInitialize); API(nvnProgramSetShaders);
    NVNdeviceBuilder db; NVNdevice device;
    nvnDeviceBuilderSetDefaults(&db); CHECK(nvnDeviceInitialize(&device,&db));
    size_t page=(size_t)getpagesize(); unsigned char *memory=NULL;
    CHECK(posix_memalign((void**)&memory,page,page*2)==0); memset(memory,0,page*2);
    memcpy(memory,"real gpu copy",14);
    NVNmemoryPoolBuilder mb; NVNmemoryPool pool;
    nvnMemoryPoolBuilderSetDefaults(&mb); nvnMemoryPoolBuilderSetDevice(&mb,&device);
    nvnMemoryPoolBuilderSetFlags(&mb,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED);
    nvnMemoryPoolBuilderSetStorage(&mb,memory,page*2); CHECK(nvnMemoryPoolInitialize(&pool,&mb));
    NVNqueueBuilder qb; NVNqueue queue; nvnQueueBuilderSetDefaults(&qb);
    nvnQueueBuilderSetDevice(&qb,&device); CHECK(nvnQueueInitialize(&queue,&qb));
    NVNcommandBuffer cb; CHECK(nvnCommandBufferInitialize(&cb,&device));
    memory[0]='R'; /* Uncached/shared writes become GPU-visible without a flush. */
    nvnCommandBufferBeginRecording(&cb);
    uint64_t gpu=nvnMemoryPoolGetBufferAddress(&pool);
    nvnCommandBufferCopyBufferToBuffer(&cb,gpu,gpu+64,14,0);
    NVNcommandHandle handle=nvnCommandBufferEndRecording(&cb);
    CHECK(!memory[64]); nvnQueueSubmitCommands(&queue,1,&handle);
    NVNsync pending; CHECK(nvnSyncInitialize(&pending,&device));
    nvnQueueFenceSync(&queue,&pending,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    NVNsyncWaitResult waited=nvnSyncWait(&pending,10000000000ull);
    CHECK(waited==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED || waited==NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED);
    nvnQueueFinish(&queue);
    nvnMemoryPoolInvalidateMappedRange(&pool,64,14); CHECK(!memcmp(memory+64,"Real gpu copy",14));
    NVNsync sync; CHECK(nvnSyncInitialize(&sync,&device));
    CHECK(nvnSyncWait(&sync,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
    nvnQueueFenceSync(&queue,&sync,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    CHECK(nvnSyncWait(&sync,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
    NVNtextureBuilder tb; NVNtexture texture;
    nvnTextureBuilderSetDefaults(&tb); nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,4,4); nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);
    nvnTextureBuilderSetFlags(&tb,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);
    nvnTextureBuilderSetStride(&tb,256); nvnTextureBuilderSetStorage(&tb,&pool,4096);
    CHECK(nvnTextureInitialize(&texture,&tb));
    const NVNtexture *targets[]={&texture}; float color[]={0,1,0,1};
    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    if (argc > 1 && !strcmp(argv[1],"partial-clear")) nvnCommandBufferSetScissor(&cb,1,1,2,2);
    nvnCommandBufferClearColor(&cb,0,color,15); handle=nvnCommandBufferEndRecording(&cb);
    nvnQueueSubmitCommands(&queue,1,&handle); nvnQueueFinish(&queue);
    unsigned char pixels[64]; CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==0 && pixels[1]==255 && pixels[2]==0 && pixels[3]==255);
    unsigned char controls[2][64]={{0}}, codes[2][128]={{0}};
    for (int i=0;i<2;i++) { *(uint32_t*)controls[i]=0x98761234; *(uint32_t*)codes[i]=0x12345678;
        controls[i][8]=(unsigned char)i; codes[i][8]=(unsigned char)i; ctl_ptrs[i]=controls[i];
        memcpy(memory+128+i*128,codes[i],128); }
    const char *msl="#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return float4(p[v],0,1); }\n"
        "fragment float4 fs() { return float4(1,0,0,1); }\n";
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs"));
    CHECK(botw_graphics_register_shader(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,msl,"fs"));
    nvnMemoryPoolFlushMappedRange(&pool,128,256);
    NVNprogram program; CHECK(nvnProgramInitialize(&program,&device));
    NVNshaderData sd[]={{gpu+128,controls[0]},{gpu+256,controls[1]}};
    NVNshaderData bad[]={{gpu+128,NULL},{gpu+256,controls[1]}};
    CHECK(!nvnProgramSetShaders(&program,2,bad));
    CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
    handle=nvnCommandBufferEndRecording(&cb); nvnQueueSubmitCommands(&queue,1,&handle); nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==255 && pixels[1]==0 && pixels[2]==0 && pixels[3]==255);
    nvnMemoryPoolFinalize(&pool); botw_graphics_shutdown(); free(memory);
    puts("GPU_COPY_CLEAR_DRAW_OK"); return 0;
}
"""


RESOURCE_FEATURE = r"""
    API(nvnTexturePoolInitialize); API(nvnTexturePoolRegisterTexture);
    API(nvnSamplerPoolInitialize); API(nvnSamplerPoolRegisterSampler);
    API(nvnSamplerBuilderSetDefaults); API(nvnSamplerBuilderSetDevice);
    API(nvnSamplerBuilderSetMinMagFilter); API(nvnSamplerBuilderSetWrapMode); API(nvnSamplerInitialize);
    API(nvnDeviceGetTextureHandle); API(nvnCommandBufferSetTexturePool); API(nvnCommandBufferSetSamplerPool);
    API(nvnCommandBufferBindTexture); API(nvnCommandBufferBindUniformBuffer);
    API(nvnVertexAttribStateSetDefaults); API(nvnVertexAttribStateSetFormat); API(nvnVertexAttribStateSetStreamIndex);
    API(nvnVertexStreamStateSetDefaults); API(nvnVertexStreamStateSetStride);
    API(nvnCommandBufferBindVertexAttribState); API(nvnCommandBufferBindVertexStreamState); API(nvnCommandBufferBindVertexBuffer);
    API(nvnDepthStencilStateSetDefaults); API(nvnDepthStencilStateSetDepthTestEnable);
    API(nvnDepthStencilStateSetDepthWriteEnable); API(nvnDepthStencilStateSetDepthFunc);
    API(nvnCommandBufferBindDepthStencilState); API(nvnCommandBufferClearDepthStencil);
    NVNtexture tiled; nvnTextureBuilderSetDefaults(&tb); nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,2,2); nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);
    nvnTextureBuilderSetStorage(&tb,&pool,6144);
    /* Independent hand-calculated single-GOB addresses: x=0/4,y=0/1. */
    unsigned char *tile=memory+6144; memset(tile,0,512);
    const unsigned char texels[4][4]={{255,0,0,255},{0,255,0,255},{255,255,255,255},{0,0,255,255}};
    memcpy(tile,texels[0],4);memcpy(tile+4,texels[1],4);memcpy(tile+16,texels[2],4);memcpy(tile+20,texels[3],4);
    CHECK(nvnTextureInitialize(&tiled,&tb));
    NVNtexturePool tp; NVNsamplerPool sp;
    CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16)); CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));
    nvnTexturePoolRegisterTexture(&tp,3,&tiled,NULL);
    NVNsamplerBuilder sb;NVNsampler sampler;nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);
    nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST,NVN_MAG_FILTER_NEAREST);
    nvnSamplerBuilderSetWrapMode(&sb,NVN_WRAP_MODE_CLAMP_TO_EDGE,NVN_WRAP_MODE_CLAMP_TO_EDGE,NVN_WRAP_MODE_CLAMP_TO_EDGE);
    CHECK(nvnSamplerInitialize(&sampler,&sb)); nvnSamplerPoolRegisterSampler(&sp,4,&sampler);
    uint64_t handleTexture=nvnDeviceGetTextureHandle(&device,3,4);
    const float positions[]={-1,-1,3,-1,-1,3};memcpy(memory+512,positions,sizeof positions);
    float vsUniform[]={0,0,.25,0},fsUniform[]={1,1,.5,1};memcpy(memory+768,vsUniform,16);memcpy(memory+784,fsUniform,16);
    NVNvertexAttribState va;NVNvertexStreamState stream;
    nvnVertexAttribStateSetDefaults(&va);nvnVertexAttribStateSetFormat(&va,NVN_FORMAT_RG32F,0);nvnVertexAttribStateSetStreamIndex(&va,0);
    nvnVertexStreamStateSetDefaults(&stream);nvnVertexStreamStateSetStride(&stream,8);
    controls[0][8]=4;controls[1][8]=5;codes[0][8]=4;codes[1][8]=5;
    memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    const char *resourceMSL="#include <metal_stdlib>\nusing namespace metal;struct In{float2 p [[attribute(0)]];};struct O{float4 p [[position]];};"
      "vertex O rv(In v [[stage_in]],constant float4& u [[buffer(0)]]) {return {float4(v.p+u.xy,u.z,1)};}"
      "fragment float4 rf(O v [[stage_in]],constant float4& c [[buffer(1)]],texture2d<float> t [[texture(3)]],sampler s [[sampler(0)]]) {return t.sample(s,float2(.75,.75))*c;}";
    botw_graphics_shader_binding vb[]={{BOTW_GRAPHICS_UNIFORM,5,0}};
    botw_graphics_shader_binding fb[]={{BOTW_GRAPHICS_UNIFORM,7,1},{BOTW_GRAPHICS_TEXTURE,9,3},{BOTW_GRAPHICS_SAMPLER,9,0}};
    if(argc>1&&!strcmp(argv[1],"bad-bindings"))fb[1].metal_slot=2;
    CHECK(botw_graphics_register_shader_with_bindings(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,resourceMSL,"rv",vb,1));
    CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,resourceMSL,"rf",fb,3));
    CHECK(nvnProgramSetShaders(&program,2,sd));
    NVNtexture depth;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,4,4);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_DEPTH32F);nvnTextureBuilderSetStorage(&tb,&pool,65536);
    CHECK(nvnTextureInitialize(&depth,&tb));
    NVNdepthStencilState ds;nvnDepthStencilStateSetDefaults(&ds);nvnDepthStencilStateSetDepthTestEnable(&ds,1);
    nvnDepthStencilStateSetDepthWriteEnable(&ds,1);nvnDepthStencilStateSetDepthFunc(&ds,NVN_DEPTH_FUNC_LESS);
    for(int pass=0;pass<2;pass++){
      if(pass){vsUniform[2]=.75;fsUniform[0]=fsUniform[1]=fsUniform[2]=0;memcpy(memory+768,vsUniform,16);memcpy(memory+784,fsUniform,16);}
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,&depth,NULL);
      if(!pass)nvnCommandBufferClearDepthStencil(&cb,1,1,0,0);
      nvnCommandBufferBindDepthStencilState(&cb,&ds);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
      nvnCommandBufferBindVertexAttribState(&cb,1,&va);nvnCommandBufferBindVertexStreamState(&cb,1,&stream);
      nvnCommandBufferBindVertexBuffer(&cb,0,gpu+512,(argc>1&&!strcmp(argv[1],"short-vertex"))?4:sizeof positions);
      nvnCommandBufferBindUniformBuffer(&cb,NVN_SHADER_STAGE_VERTEX,5,gpu+768,(argc>1&&!strcmp(argv[1],"short-uniform"))?4:16);
      nvnCommandBufferBindUniformBuffer(&cb,NVN_SHADER_STAGE_FRAGMENT,7,gpu+784,16);
      nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);
      nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,9,handleTexture);
      nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);
      nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
      CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
      CHECK(pixels[0]==0 && pixels[1]==0 && pixels[2]>=127 && pixels[2]<=128 && pixels[3]==255);
    }
    CHECK(botw_graphics_presented_frame_count()==0); /* Offscreen work is not a presented game frame. */
    puts("GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK");
"""
def _depth_storage_capacity(source):
    # Keep the page*2 token available to deliberate downstream arena variants.
    # Native depth needs one 64KiB allocation at pool-relative offset 64KiB.
    return source.replace('page*2', '((page*2)>131072?(page*2):131072)')


RESOURCE_SMOKE = _depth_storage_capacity(METAL_SMOKE).replace("    nvnMemoryPoolFinalize(&pool);", RESOURCE_FEATURE + "    nvnMemoryPoolFinalize(&pool);")

COMMAND_MEMORY_CALLBACKS = r"""
static int callbackCount;static unsigned char *controlBase;
static int all_memory(const void *p,size_t n,int write){(void)p;(void)n;(void)write;return 1;}
static void callback(NVNcommandBuffer *c,NVNcommandBufferMemoryEvent event,size_t n,void *data){
    (void)c;(void)event;(void)n;if(data!=(void*)&callbackCount)abort();callbackCount++;
}
static int invoke(PFNNVNCOMMANDBUFFERMEMORYCALLBACKPROC f,NVNcommandBuffer *c,NVNcommandBufferMemoryEvent event,size_t n,void *data,char *e,size_t cap){
    (void)e;(void)cap;f(c,event,n,data);return 1;
}
"""
COMMAND_MEMORY_SETUP = r"""
    botw_graphics_set_memory_access_validator(all_memory);botw_graphics_set_command_memory_invoker(invoke);
    CHECK(!posix_memalign((void**)&controlBase,8,16384+16));memset(controlBase,0xa5,16384+16);
    nvnCommandBufferSetMemoryCallback(&cb,callback);nvnCommandBufferSetMemoryCallbackData(&cb,&callbackCount);
    /* Prove the advertised four/eight byte alignments, not stronger ones. */
    nvnCommandBufferAddCommandMemory(&cb,&pool,32772,16384);nvnCommandBufferAddControlMemory(&cb,controlBase+8,16384);
    CHECK(nvnCommandBufferGetCommandMemorySize(&cb)==16384&&nvnCommandBufferGetControlMemorySize(&cb)==16384);
"""
COMMAND_MEMORY_VERIFY = r"""
    CHECK(!callbackCount);CHECK(nvnCommandBufferGetCommandMemoryUsed(&cb)>0);CHECK(nvnCommandBufferGetControlMemoryUsed(&cb)>0);
    CHECK(nvnCommandBufferGetControlMemoryFree(&cb)+nvnCommandBufferGetControlMemoryUsed(&cb)==16384);
    for(int i=0;i<8;i++){CHECK(controlBase[i]==0xa5);CHECK(controlBase[16384+8+i]==0xa5);}
"""
COMMAND_MEMORY_SMOKE = RESOURCE_SMOKE.replace('page*2', 'page*16').replace(
    'int main(int argc, char **argv) {', COMMAND_MEMORY_CALLBACKS + 'int main(int argc, char **argv) {').replace(
    '    API(nvnCommandBufferInitialize);',
    '    API(nvnCommandBufferSetMemoryCallback);API(nvnCommandBufferSetMemoryCallbackData);'
    'API(nvnCommandBufferAddCommandMemory);API(nvnCommandBufferAddControlMemory);'
    'API(nvnCommandBufferGetCommandMemoryUsed);API(nvnCommandBufferGetCommandMemorySize);'
    'API(nvnCommandBufferGetControlMemoryUsed);API(nvnCommandBufferGetControlMemoryFree);'
    'API(nvnCommandBufferGetControlMemorySize);\n    API(nvnCommandBufferInitialize);').replace(
    "    memory[0]='R';", COMMAND_MEMORY_SETUP + "    memory[0]='R';").replace(
    '    nvnMemoryPoolFinalize(&pool);', COMMAND_MEMORY_VERIFY + '    nvnMemoryPoolFinalize(&pool);').replace(
    'botw_graphics_shutdown(); free(memory);', 'botw_graphics_shutdown();free(controlBase); free(memory);')

STORAGE_FEATURE = r"""
    API(nvnCommandBufferBindStorageBuffer);API(nvnCommandBufferBindStorageBuffers);API(nvnCommandBufferDrawArraysInstanced);
    const float instances[]={-.5,0,1,0,.5,0,0,1};memcpy(memory+1024,instances,sizeof instances);
    memset(memory+1280,0,48);
    controls[0][8]=6;controls[1][8]=7;codes[0][8]=6;codes[1][8]=7;
    memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    const char *storageMSL="#include <metal_stdlib>\nusing namespace metal;struct S{float4 instance[2];};struct U{uint4 instance[2];};struct W{uint value[12];};struct O{float4 p [[position]];float4 color;};"
      "vertex O sv(uint v [[vertex_id]],uint bv [[base_vertex]],uint i [[instance_id]],uint bi [[base_instance]],device const S& s [[buffer(2)]],device W& w [[buffer(3)]],device const U& alias [[buffer(4)]],constant uint& low [[buffer(5)]]) {"
      "uint index=i-bi,corner=v-bv;if(index>=2||corner>=6)return {float4(0),float4(0)};float2 p[6]={float2(-1,-1),float2(1,-1),float2(-1,1),float2(-1,1),float2(1,-1),float2(1,1)};"
      "w.value[index*6+corner]=low+(index+1)*10+corner+1;return {float4(p[corner]*float2(.5,1)+s.instance[index].xy,0,1),float4(as_type<float2>(alias.instance[index].zw),0,1)};}"
      "fragment float4 sf(O v [[stage_in]]){return v.color;}";
    botw_graphics_shader_binding sb[]={{BOTW_GRAPHICS_STORAGE,3,2},{BOTW_GRAPHICS_STORAGE,4,3},{BOTW_GRAPHICS_STORAGE,3,4},{BOTW_GRAPHICS_STORAGE_ADDRESS_LOW,3,5}};
    if(argc>1&&!strcmp(argv[1],"duplicate-storage"))sb[2].metal_slot=3;
    if(argc>1&&!strcmp(argv[1],"wide-driver"))sb[0].kind=BOTW_GRAPHICS_STORAGE_ADDRESS_LOW;
    if(argc>1&&!strcmp(argv[1],"unbound-driver"))sb[3].nvn_slot=7;
    size_t storageMinimum[]={0,16,32};
    botw_graphics_shader_draw_profile profile={6,2,3,storageMinimum};
    if(argc>1&&!strcmp(argv[1],"profile-vertices"))profile.max_vertex_count=5;
    if(argc>1&&!strcmp(argv[1],"profile-instances"))profile.max_instance_count=1;
    if(argc>1&&!strcmp(argv[1],"profile-storage"))storageMinimum[2]=64;
    if(argc>1&&!strcmp(argv[1],"invalid-profile"))storageMinimum[2]=8;
    CHECK(botw_graphics_register_shader_with_profile(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,storageMSL,"sv",sb,4,&profile));
    storageMinimum[1]=storageMinimum[2]=0;
    CHECK(botw_graphics_register_shader(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,storageMSL,"sf"));
    CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
    if(!(argc>1&&!strcmp(argv[1],"unbound-storage")))
      nvnCommandBufferBindStorageBuffer(&cb,NVN_SHADER_STAGE_VERTEX,3,gpu+1024,(argc>1&&!strcmp(argv[1],"short-storage"))?4:sizeof instances);
    NVNbufferRange writable={gpu+1280,48};nvnCommandBufferBindStorageBuffers(&cb,NVN_SHADER_STAGE_VERTEX,4,1,&writable);
    nvnCommandBufferDrawArraysInstanced(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,3,6,5,(argc>1&&!strcmp(argv[1],"negative-instance"))?-1:2);
    handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==255&&pixels[1]==0&&pixels[12]==0&&pixels[13]==255);
    uint32_t *written=(uint32_t*)(memory+1280);for(unsigned i=0;i<12;i++)CHECK(written[i]==(uint32_t)((uint32_t)(gpu+1024)+(i/6+1)*10+i%6+1));
    puts("GPU_STORAGE_INSTANCING_BASE_IDS_OK");
"""


BUNDLE_SMOKE = r"""
#include "graphics.h"
#include "shader_bundle.h"
#include <stdio.h>
int main(int argc,char **argv){
    if(argc!=2)return 3;
    if(!botw_graphics_init(NULL,NULL,NULL))return 77;
    botw_graphics_shader_bundle_result result;char error[1024];
    int ok=botw_graphics_load_shader_bundle(argv[1],&result,error,sizeof error);
    if(!ok){fprintf(stderr,"%s\n",error);botw_graphics_shutdown();return 2;}
    printf("REGISTERED=%zu UNSUPPORTED=%zu TOTAL=%zu\n",result.registered_entries,result.unsupported_entries,result.declared_entries);
    botw_graphics_shutdown();return 0;
}
"""

CONCURRENT_SMOKE = r"""
#include "graphics.h"
#include "nvn_protos.h"
#include <pthread.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
static NVNT_nvnDeviceBuilderSetDefaults defaults;
static NVNT_nvnDeviceInitialize initialize;
static NVNT_nvnDeviceFinalize finalize;
static NVNT_nvnDeviceGetProcAddress get_proc;
static NVNT_nvnDeviceFinalizeCommandHandle free_handle;
static NVNT_nvnCommandBufferInitialize init_command;
static NVNT_nvnCommandBufferBeginRecording begin_command;
static NVNT_nvnCommandBufferEndRecording end_command;
static NVNT_nvnCommandBufferFinalize free_command;
static sigjmp_buf recovery;
static int escaping;
static void report_error(const char *message){
    fprintf(stderr,"GFX:%s\n",message);
    if(escaping)siglongjmp(recovery,1);
}
static int readable(const void *p,size_t n){(void)p;(void)n;return 1;}
static void *worker(void *p){
    (void)p;
    for(unsigned i=0;i<2000;i++){
        NVNdeviceBuilder b;NVNdevice d;NVNcommandBuffer c;
        defaults(&b);if(!initialize(&d,&b)||!init_command(&c,&d))abort();
        begin_command(&c);NVNcommandHandle handle=end_command(&c);
        free_handle(&d,handle);free_command(&c);finalize(&d);
    }
    return NULL;
}
int main(void){
    if(!botw_graphics_init(NULL,report_error,readable))return 77;
#define LOAD(var,name) var=(NVNT_##name)botw_nvn_bootstrap_loader(#name)
    LOAD(defaults,nvnDeviceBuilderSetDefaults);LOAD(initialize,nvnDeviceInitialize);LOAD(finalize,nvnDeviceFinalize);
    LOAD(get_proc,nvnDeviceGetProcAddress);
    LOAD(free_handle,nvnDeviceFinalizeCommandHandle);LOAD(init_command,nvnCommandBufferInitialize);
    LOAD(begin_command,nvnCommandBufferBeginRecording);LOAD(end_command,nvnCommandBufferEndRecording);
    LOAD(free_command,nvnCommandBufferFinalize);
    pthread_t threads[8];
    for(unsigned i=0;i<8;i++)if(pthread_create(&threads[i],NULL,worker,NULL))return 3;
    for(unsigned i=0;i<8;i++)if(pthread_join(threads[i],NULL))return 4;
    for(int mode=0;mode<4;mode++){
        if(sigsetjmp(recovery,1)==0){
            escaping=1;
            if(mode==0){NVNdevice invalid={0};finalize(&invalid);}
            if(mode==1){NVNdevice invalid={0};(void)get_proc(&invalid,"nvnDeviceInitialize");}
            if(mode==2){void (*bad)(void)=botw_nvn_bootstrap_loader("nvnCommandBufferDispatchCompute");bad();}
            if(mode==3){NVNdeviceBuilder b;NVNdevice d;defaults(&b);if(!initialize(&d,&b))return 7;free_handle(&d,0);}
            return 5;
        }
        escaping=0;botw_graphics_shutdown();
        if(!botw_graphics_init(NULL,report_error,readable))return 6;
    }
    botw_graphics_shutdown();
    puts("GPU_CONCURRENT_16000_OBJECTS_AND_ERROR_ESCAPE_OK");return 0;
}
"""




ARRAY_FEATURE = r"""
    API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetSize3D);API(nvnTextureBuilderSetDepth);API(nvnTextureBuilderSetLevels);
    API(nvnTextureBuilderGetStorageSize);API(nvnTextureBuilderGetStorageAlignment);API(nvnTextureGetDepth);API(nvnTextureGetLevels);API(nvnTextureGetTarget);API(nvnTextureFinalize);
    API(nvnTexturePoolInitialize);API(nvnTexturePoolRegisterTexture);API(nvnSamplerPoolInitialize);API(nvnSamplerPoolRegisterSampler);
    API(nvnSamplerBuilderSetDefaults);API(nvnSamplerBuilderSetDevice);API(nvnSamplerBuilderSetMinMagFilter);API(nvnSamplerInitialize);
    API(nvnDeviceGetTextureHandle);API(nvnCommandBufferSetTexturePool);API(nvnCommandBufferSetSamplerPool);API(nvnCommandBufferBindTexture);
    unsigned char *arrayMemory=NULL;CHECK(posix_memalign((void**)&arrayMemory,page,page*4)==0);memset(arrayMemory,0,page*4);
    NVNmemoryPool arrayPool;nvnMemoryPoolBuilderSetStorage(&mb,arrayMemory,page*4);CHECK(nvnMemoryPoolInitialize(&arrayPool,&mb));
    int r8=(argc>1&&!strcmp(argv[1],"r8")),integer=(argc>1&&!strcmp(argv[1],"wrong-scalar"));
    const unsigned char gray[2][3]={{32,64,96},{128,160,192}};
    const unsigned char colors[2][3][4]={{{255,0,0,255},{0,0,255,255},{255,0,255,255}},{{0,255,0,255},{255,255,0,255},{0,255,255,255}}};
    /* Independent GOB address calculation: one 64-byte-wide GOB, base mip
       two GOBs high; RGBA base mip two GOBs wide, lower mips one GOB.
       RGBA layer stride = 2048+512+512; R8 stride = 1024+512+512. */
    size_t layerStride=(r8||integer)?2048:3072;const size_t levelOffsets[]={0,(r8||integer)?1024:2048,(r8||integer)?1536:2560};
    for(unsigned layer=0;layer<2;layer++)for(unsigned level=0;level<3;level++)for(unsigned y=0;y<(16u>>level);y++)for(unsigned x=0;x<(32u>>level);x++){
      unsigned xb=x*((r8||integer)?1:4);size_t gob=(xb/64)*(level?512:1024)+(y/8)*512+(xb%64/32)*256+(y%8/2)*64+(xb%32/16)*32+(y%2)*16+xb%16;
      if(r8||integer)arrayMemory[layer*layerStride+levelOffsets[level]+gob]=gray[layer][level];else memcpy(arrayMemory+layer*layerStride+levelOffsets[level]+gob,colors[layer][level],4);
    }
    NVNtexture arrayTexture;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetTarget(&tb,NVN_TEXTURE_TARGET_2D_ARRAY);nvnTextureBuilderSetSize3D(&tb,32,16,2);nvnTextureBuilderSetLevels(&tb,3);
    nvnTextureBuilderSetFormat(&tb,integer?NVN_FORMAT_R8UI:r8?NVN_FORMAT_R8:NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&arrayPool,0);
    if(argc>1&&!strcmp(argv[1],"linear-array")){nvnTextureBuilderSetFlags(&tb,NVN_TEXTURE_FLAGS_LINEAR);nvnTextureBuilderSetStride(&tb,256);CHECK(!nvnTextureInitialize(&arrayTexture,&tb));puts(botw_graphics_last_error());goto array_done;}
    if(argc>1&&!strcmp(argv[1],"bad-layers")){nvnTextureBuilderSetDepth(&tb,-1);CHECK(nvnTextureBuilderGetStorageSize(&tb)==0);CHECK(nvnTextureBuilderGetStorageAlignment(&tb)==0);CHECK(!nvnTextureInitialize(&arrayTexture,&tb));puts(botw_graphics_last_error());goto array_done;}
    if(argc>1&&!strcmp(argv[1],"bad-levels")){nvnTextureBuilderSetLevels(&tb,16);CHECK(nvnTextureBuilderGetStorageSize(&tb)==0);CHECK(nvnTextureBuilderGetStorageAlignment(&tb)==0);CHECK(!nvnTextureInitialize(&arrayTexture,&tb));puts(botw_graphics_last_error());goto array_done;}
    CHECK(nvnTextureBuilderGetStorageSize(&tb)==((r8||integer)?4096:6144));CHECK(nvnTextureBuilderGetStorageAlignment(&tb)==512);CHECK(nvnTextureInitialize(&arrayTexture,&tb));
    CHECK(nvnTextureGetDepth(&arrayTexture)==2&&nvnTextureGetLevels(&arrayTexture)==3&&nvnTextureGetTarget(&arrayTexture)==NVN_TEXTURE_TARGET_2D_ARRAY);
    if(argc>1&&!strcmp(argv[1],"array-target")){const NVNtexture *badTargets[]={&arrayTexture};nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,badTargets,NULL,NULL,NULL);return 2;}
    unsigned char mipPixels[32*16*4];if(!r8&&!integer){
    for(unsigned layer=0;layer<2;layer++)for(unsigned level=0;level<3;level++){CHECK(botw_graphics_read_texture_subresource(&arrayTexture,level,layer,mipPixels,sizeof mipPixels));for(unsigned pixel=0;pixel<((32u>>level)*(16u>>level));pixel++)CHECK(!memcmp(mipPixels+pixel*4,colors[layer][level],4));}
    CHECK(!botw_graphics_read_texture_subresource(&arrayTexture,3,0,mipPixels,sizeof mipPixels));CHECK(!botw_graphics_read_texture_subresource(&arrayTexture,0,2,mipPixels,sizeof mipPixels));}
    NVNtexturePool tp;NVNsamplerPool sp;CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16));CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));
    nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);NVNsamplerBuilder sb;NVNsampler sampler;
    nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST_MIPMAP_NEAREST,NVN_MAG_FILTER_NEAREST);
    CHECK(nvnSamplerInitialize(&sampler,&sb));nvnSamplerPoolRegisterSampler(&sp,4,&sampler);uint64_t textureHandle=nvnDeviceGetTextureHandle(&device,3,4);
    controls[0][8]=8;controls[1][8]=9;codes[0][8]=8;codes[1][8]=9;memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    char arrayMSL[1200];snprintf(arrayMSL,sizeof arrayMSL,"#include <metal_stdlib>\nusing namespace metal;vertex float4 av(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}"
      "fragment float4 af(float4 pos [[position]],texture2d_array<float> t [[texture(3)]],sampler s [[sampler(0)]]){float4 color=t.sample(s,float2(.01),uint(pos.x)>=2u?1u:0u,level(float(min(uint(pos.y),2u))));return float4(%s,1);}",r8?"color.rrr":"color.rgb");
    botw_graphics_shader_binding bindings[]={{BOTW_GRAPHICS_TEXTURE,0,3},{BOTW_GRAPHICS_SAMPLER,0,0}};
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,arrayMSL,"av"));
    CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,arrayMSL,"af",bindings,2));CHECK(nvnProgramSetShaders(&program,2,sd));
    for(unsigned pass=0;pass<2;pass++){
      if(pass){if(r8)arrayMemory[layerStride]=gray[0][0];else memcpy(arrayMemory+layerStride,colors[0][0],4);}
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
      nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,textureHandle);
      nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
      CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
      for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++){unsigned char *pixel=pixels+(y*4+x)*4;unsigned layer=x>=2,level=y<3?y:2;if(pass&&!y&&layer)layer=0;if(r8)CHECK(pixel[0]==gray[layer][level]&&pixel[1]==gray[layer][level]&&pixel[2]==gray[layer][level]&&pixel[3]==255);else CHECK(!memcmp(pixel,colors[layer][level],4));}
    }
    nvnTextureFinalize(&arrayTexture);puts("GPU_ARRAY_LAYER_MIP_COLORS_OK");
array_done:
    nvnMemoryPoolFinalize(&arrayPool);
"""


RENDER_STATE_FEATURE = r"""

    API(nvnBlendStateSetDefaults);API(nvnBlendStateSetBlendFunc);API(nvnBlendStateSetBlendEquation);API(nvnBlendStateSetBlendTarget);
    API(nvnColorStateSetDefaults);API(nvnColorStateSetBlendEnable);API(nvnChannelMaskStateSetDefaults);API(nvnChannelMaskStateSetChannelMask);
    API(nvnPolygonStateSetDefaults);API(nvnPolygonStateSetCullFace);API(nvnPolygonStateSetFrontFace);API(nvnPolygonStateSetPolygonMode);
    API(nvnMultisampleStateSetDefaults);API(nvnMultisampleStateSetSamples);API(nvnMultisampleStateSetAlphaToCoverageEnable);API(nvnMultisampleStateSetSampleLocationsEnable);API(nvnDepthStencilStateSetDefaults);API(nvnDepthStencilStateSetStencilTestEnable);
    API(nvnCommandBufferBindBlendState);API(nvnCommandBufferBindColorState);API(nvnCommandBufferBindChannelMaskState);API(nvnCommandBufferBindPolygonState);API(nvnCommandBufferBindMultisampleState);API(nvnCommandBufferBindDepthStencilState);API(nvnCommandBufferSetBlendColor);
    NVNblendState bs;NVNcolorState cs;NVNchannelMaskState cm;NVNpolygonState poly;NVNmultisampleState ms;NVNdepthStencilState ds;
    nvnBlendStateSetDefaults(&bs);nvnBlendStateSetBlendTarget(&bs,0);nvnBlendStateSetBlendFunc(&bs,NVN_BLEND_FUNC_CONSTANT_COLOR,NVN_BLEND_FUNC_ONE_MINUS_CONSTANT_COLOR,NVN_BLEND_FUNC_ONE,NVN_BLEND_FUNC_ZERO);nvnBlendStateSetBlendEquation(&bs,NVN_BLEND_EQUATION_ADD,NVN_BLEND_EQUATION_ADD);
    nvnColorStateSetDefaults(&cs);nvnColorStateSetBlendEnable(&cs,0,argc>1&&!strcmp(argv[1],"disabled")?0x100:0x101);
    nvnChannelMaskStateSetDefaults(&cm);if(argc>1&&!strcmp(argv[1],"mask"))nvnChannelMaskStateSetChannelMask(&cm,0,0,1,0,0);
    nvnPolygonStateSetDefaults(&poly);nvnPolygonStateSetCullFace(&poly,argc>1&&!strcmp(argv[1],"both")?NVN_FACE_FRONT_AND_BACK:NVN_FACE_NONE);nvnPolygonStateSetFrontFace(&poly,NVN_FRONT_FACE_CCW);nvnPolygonStateSetPolygonMode(&poly,NVN_POLYGON_MODE_FILL);
    nvnMultisampleStateSetDefaults(&ms);nvnMultisampleStateSetSamples(&ms,1);nvnDepthStencilStateSetDefaults(&ds);
    if(argc>1&&!strcmp(argv[1],"stencil"))nvnDepthStencilStateSetStencilTestEnable(&ds,1);
    if(argc>1&&!strcmp(argv[1],"samples"))nvnMultisampleStateSetSamples(&ms,2);
    if(argc>1&&!strcmp(argv[1],"coverage"))nvnMultisampleStateSetAlphaToCoverageEnable(&ms,1);
    if(argc>1&&!strcmp(argv[1],"locations"))nvnMultisampleStateSetSampleLocationsEnable(&ms,1);
    if(argc>1&&!strcmp(argv[1],"point"))nvnPolygonStateSetPolygonMode(&poly,NVN_POLYGON_MODE_POINT);
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);float green[]={0,1,0,1};nvnCommandBufferClearColor(&cb,0,green,15);
    nvnCommandBufferBindBlendState(&cb,&bs);nvnCommandBufferBindColorState(&cb,&cs);nvnCommandBufferBindChannelMaskState(&cb,&cm);nvnCommandBufferBindPolygonState(&cb,&poly);nvnCommandBufferBindMultisampleState(&cb,&ms);nvnCommandBufferBindDepthStencilState(&cb,&ds);
    float blendColor[]={.25,.5,.75,.5};nvnCommandBufferSetBlendColor(&cb,blendColor);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
    /* Binding snapshots survive source object changes before submit. */
    nvnColorStateSetBlendEnable(&cs,0,0);nvnChannelMaskStateSetDefaults(&cm);nvnPolygonStateSetCullFace(&poly,NVN_FACE_FRONT_AND_BACK);blendColor[0]=1;
    handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    unsigned r=64,g=128;if(argc>1&&!strcmp(argv[1],"mask"))r=0;if(argc>1&&!strcmp(argv[1],"disabled")){r=255;g=0;}if(argc>1&&!strcmp(argv[1],"both")){r=0;g=255;}
    for(int i=0;i<16;i++)CHECK(abs((int)pixels[i*4]-(int)r)<=1&&abs((int)pixels[i*4+1]-(int)g)<=1&&pixels[i*4+2]==0&&pixels[i*4+3]==255);
    /* Reuse this program/target after changing only blend enable and mask;
     * cached PSOs must incorporate the new state, while dynamic constants persist. */
    nvnPolygonStateSetCullFace(&poly,NVN_FACE_NONE);nvnColorStateSetBlendEnable(&cs,0,0);nvnChannelMaskStateSetDefaults(&cm);
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferClearColor(&cb,0,green,15);
    nvnCommandBufferBindColorState(&cb,&cs);nvnCommandBufferBindChannelMaskState(&cb,&cm);nvnCommandBufferBindPolygonState(&cb,&poly);
    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    for(int i=0;i<16;i++)CHECK(pixels[i*4]==255&&pixels[i*4+1]==0&&pixels[i*4+2]==0&&pixels[i*4+3]==255);
    puts("UPSTREAM_STATE_BLEND_MASK_POLYGON_MS_DEPTH_GPU_OK");
"""

DEPTH_RASTER_FEATURE = r"""
    API(nvnPolygonStateSetDefaults);API(nvnPolygonStateSetCullFace);API(nvnPolygonStateSetFrontFace);API(nvnPolygonStateSetPolygonMode);API(nvnPolygonStateSetPolygonOffsetEnables);API(nvnCommandBufferBindPolygonState);
    API(nvnDepthStencilStateSetDefaults);API(nvnDepthStencilStateSetDepthTestEnable);API(nvnDepthStencilStateSetDepthWriteEnable);API(nvnDepthStencilStateSetDepthFunc);API(nvnCommandBufferBindDepthStencilState);API(nvnCommandBufferClearDepthStencil);
    API(nvnCommandBufferSetDepthRange);API(nvnCommandBufferSetPolygonOffsetClamp);
    NVNtexture z;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetSize2D(&tb,4,4);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_DEPTH32F);nvnTextureBuilderSetStorage(&tb,&pool,65536);CHECK(nvnTextureInitialize(&z,&tb));
    unsigned red[9]={0};
    for(unsigned test=0;test<9;test++){
      NVNpolygonState raster;nvnPolygonStateSetDefaults(&raster);nvnPolygonStateSetPolygonMode(&raster,NVN_POLYGON_MODE_FILL);
      if(test>=5)nvnPolygonStateSetCullFace(&raster,(test&1)?NVN_FACE_FRONT:NVN_FACE_BACK);
      if(test>=7)nvnPolygonStateSetFrontFace(&raster,NVN_FRONT_FACE_CW);
      nvnPolygonStateSetPolygonOffsetEnables(&raster,test==3?NVN_POLYGON_OFFSET_ENABLE_FILL:test==4?NVN_POLYGON_OFFSET_ENABLE_LINE:0);
      NVNdepthStencilState depth;nvnDepthStencilStateSetDefaults(&depth);nvnDepthStencilStateSetDepthTestEnable(&depth,1);nvnDepthStencilStateSetDepthWriteEnable(&depth,1);nvnDepthStencilStateSetDepthFunc(&depth,NVN_DEPTH_FUNC_LESS);
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,&z,NULL);
      float green[]={0,1,0,1};nvnCommandBufferClearColor(&cb,0,green,15);nvnCommandBufferClearDepthStencil(&cb,.5,1,0,0);
      nvnCommandBufferBindDepthStencilState(&cb,&depth);nvnCommandBufferBindPolygonState(&cb,&raster);
      nvnCommandBufferSetDepthRange(&cb,test==1?.5:test==2?1:0,test==2?0:1);nvnCommandBufferSetPolygonOffsetClamp(&cb,0,100000000,.5);
      nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
      handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
      for(unsigned p=0;p<16;p++){CHECK(pixels[p*4+2]==0&&pixels[p*4+3]==255);if(pixels[p*4]==255&&pixels[p*4+1]==0)red[test]++;else CHECK(pixels[p*4]==0&&pixels[p*4+1]==255);}
      printf("raster_case=%u red=%u\n",test,red[test]);
    }
    CHECK(red[0]==16&&red[1]==0&&red[2]==0&&red[3]==0&&red[4]==16);
    CHECK(red[5]+red[6]==16&&red[5]==red[8]&&red[6]==red[7]);
    controls[0][8]=0x29;controls[1][8]=0x30;codes[0][8]=0x29;codes[1][8]=0x30;memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    const char *lineMSL="#include <metal_stdlib>\nusing namespace metal;vertex float4 lv(uint v [[vertex_id]]){float2 p[3]={float2(-.75,-.75),float2(.75,-.75),float2(-.75,.75)};return float4(p[v],0,1);}fragment float4 lf(){return float4(1,0,0,1);}";
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,lineMSL,"lv"));CHECK(botw_graphics_register_shader(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,lineMSL,"lf"));CHECK(nvnProgramSetShaders(&program,2,sd));
    NVNpolygonState wire;nvnPolygonStateSetDefaults(&wire);nvnPolygonStateSetPolygonMode(&wire,NVN_POLYGON_MODE_LINE);
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);float wireGreen[]={0,1,0,1};nvnCommandBufferClearColor(&cb,0,wireGreen,15);nvnCommandBufferBindPolygonState(&cb,&wire);nvnCommandBufferSetDepthRange(&cb,0,1);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    unsigned linePixels=0;for(unsigned p=0;p<16;p++)if(pixels[p*4])linePixels++;CHECK(linePixels>0&&linePixels<16);
    puts("UPSTREAM_DEPTH_RANGE_BIAS_FACE_WINDING_REAL_PIXELS_OK");
"""


SCRATCH_GPU_FEATURE = r"""

    API(nvnCommandBufferSetShaderScratchMemory);
    controls[0][8]=0x61;controls[1][8]=0x62;codes[0][8]=0x61;codes[1][8]=0x62;
    *(uint32_t*)(codes[0]+0x30)=1u<<10;*(uint32_t*)(codes[1]+0x30)=5u<<10;
    memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    char scratchMSL[2048];snprintf(scratchMSL,sizeof scratchMSL,
      "#include <metal_stdlib>\nusing namespace metal;\n#define N %u\n"
      "vertex float4 vs(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}"
      "fragment float4 fs(float4 p [[position]]){thread volatile uint a[N];uint seed=uint(p.x)+4u*uint(p.y);for(uint i=0;i<N;i++)a[i]=seed^(17u*i);uint actual=0,expected=0;for(uint i=0;i<N;i++){actual+=a[(i+(seed&(N-1)))&(N-1)];expected+=seed^(17u*i);}return actual==expected?float4(1,0,0,1):float4(0,0,1,1);}",argc>1&&!strcmp(argv[1],"large-private")?1024u:64u);
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,scratchMSL,"vs"));CHECK(botw_graphics_register_shader(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,scratchMSL,"fs"));CHECK(nvnProgramSetShaders(&program,2,sd));
static const unsigned char vertexProof[]={3,2,35,7,0,3,1,0,0,0,0,0,16,0,0,0,0,0,0,0,17,0,2,0,1,0,0,0,14,0,3,0,0,0,0,0,1,0,0,0,15,0,5,0,0,0,0,0,3,0,0,0,109,97,105,110,0,0,0,0,19,0,2,0,1,0,0,0,33,0,3,0,2,0,0,0,1,0,0,0,54,0,5,0,1,0,0,0,3,0,0,0,0,0,0,0,2,0,0,0,248,0,2,0,4,0,0,0,253,0,1,0,56,0,1,0};
static const unsigned char fragmentProof[]={3,2,35,7,0,3,1,0,0,0,0,0,16,0,0,0,0,0,0,0,17,0,2,0,1,0,0,0,14,0,3,0,0,0,0,0,1,0,0,0,15,0,5,0,4,0,0,0,3,0,0,0,109,97,105,110,0,0,0,0,19,0,2,0,1,0,0,0,33,0,3,0,2,0,0,0,1,0,0,0,54,0,5,0,1,0,0,0,3,0,0,0,0,0,0,0,2,0,0,0,248,0,2,0,4,0,0,0,253,0,1,0,56,0,1,0};

    if(!(argc>1&&!strcmp(argv[1],"unproved"))){CHECK(botw_graphics_register_shader_scratch_proof(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,vertexProof,sizeof vertexProof));CHECK(botw_graphics_register_shader_scratch_proof(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,fragmentProof,sizeof fragmentProof));}
    NVNmemoryPoolBuilder scratchBuilder;NVNmemoryPool scratchPool;nvnMemoryPoolBuilderSetDefaults(&scratchBuilder);nvnMemoryPoolBuilderSetDevice(&scratchBuilder,&device);nvnMemoryPoolBuilderSetFlags(&scratchBuilder,0x21);nvnMemoryPoolBuilderSetStorage(&scratchBuilder,NULL,0);CHECK(nvnMemoryPoolInitialize(&scratchPool,&scratchBuilder));
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);float scratchGreen[]={0,1,0,1};nvnCommandBufferClearColor(&cb,0,scratchGreen,15);nvnCommandBufferSetShaderScratchMemory(&cb,argc>1&&!strcmp(argv[1],"null-scratch")?NULL:&scratchPool,0,0);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);
    if(argc>1&&!strcmp(argv[1],"stale-scratch")){nvnMemoryPoolFinalize(&scratchPool);CHECK(nvnMemoryPoolInitialize(&scratchPool,&scratchBuilder));}
    NVNqueueBuilder secondBuilder;NVNqueue secondQueue;nvnQueueBuilderSetDefaults(&secondBuilder);nvnQueueBuilderSetDevice(&secondBuilder,&device);CHECK(nvnQueueInitialize(&secondQueue,&secondBuilder));
    nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueSubmitCommands(&secondQueue,1,&handle);nvnMemoryPoolFinalize(&scratchPool);nvnQueueFinish(&queue);nvnQueueFinish(&secondQueue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    for(unsigned i=0;i<16;i++)CHECK(pixels[i*4]==255&&pixels[i*4+1]==0&&pixels[i*4+2]==0&&pixels[i*4+3]==255);
    puts("PROVEN_SHADER_SCRATCH_ZERO_POOL_TWO_QUEUES_GPU_PIXELS_OK");
"""

if __name__ == '__main__':
    unittest.main()
