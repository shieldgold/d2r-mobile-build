"""Synthetic tests for tools/botw_shader_derive.py. No game bytes are embedded or
required; temporary files live in the system temporary directory, outside the
repository.

python3 test_botw_shader_derive.py
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True  # keep bytecode caches out of the repository
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bsd = load('botw_shader_derive', HERE.parent / 'tools/botw_shader_derive.py')
bsb = bsd.bsb
helpers = load('test_botw_shader_build', HERE / 'test_botw_shader_build.py')
control, code, write_capture = helpers.control, helpers.code, helpers.write_capture


VERTEX_MSL = '''
struct main0_in
{
    float4 m_0 [[attribute(0)]];
    float4 m_1 [[attribute(1)]];
    float4 m_2 [[attribute(2)]];
    float4 m_3 [[attribute(3)]];
};

vertex main0_out main0(main0_in in [[stage_in]])
{
    main0_out out = {};
    float _1 = in.m_0.x;
    int _2 = as_type<int>(_1) * 48;
    float _3 = in.m_1.x;
    out.gl_Position.x = _3 * 2.0;
    float _4 = in.m_2.y;
    uint _5 = as_type<uint>(_4);
    out.gl_Position.y = _4 + 1.0;
    float _6 = in.m_3.z;
    uint _7 = as_type<uint>(_6);
    out.m_9.x = _6;
}
'''

FRAGMENT_MSL = '''
struct main0_out
{
    float4 m_32 [[color(0)]];
    float4 m_33 [[color(1)]];
};

fragment main0_out main0()
{
    main0_out out = {};
    out.m_32.x = as_type<float>(_109);
    out.m_32.y = as_type<float>(_97);
    out.m_33.x = as_type<float>(_1);
    out.m_33.y = _2 * 0.5;
}
'''


class Derive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='botw_shader_derive_')
        self.dir = os.path.realpath(self.tmp.name)
        self.caps = os.path.join(self.dir, 'caps'); os.mkdir(self.caps)

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, ctl, cod):
        key, _ = write_capture(self.caps, ctl, cod)
        return bsb.load_capture(self.caps, key)

    def test_driver_table_slot(self):
        self.assertEqual([bsd.driver_table_slot(h) for h in (8, 10, 12, 14, 44)], [0, 1, 2, 3, 18])
        self.assertEqual([bsd.driver_table_slot(h) for h in (7, 9, 0x48, 0x10000008)], [None, None, None, None])

    def test_msl_texture_type(self):
        cases = {'texture2d<float>': '2d', 'depth2d<float>': '2d', 'texture2d_array<float>': '2darray',
                 'texturecube_array<float>': 'cubearray', 'texture3d<float>': '3d', 'texture_buffer<float>': 'buffer',
                 'sampler': None}
        self.assertEqual({k: bsd.msl_texture_type(k) for k in cases}, cases)

    def test_integer_inputs_and_outputs(self):
        # m_0: bit cast only; m_1: float arithmetic; m_2: bit cast plus float arithmetic;
        # m_3: bit cast plus a plain copy, which keeps the bits under either declaration.
        self.assertEqual(bsd.integer_inputs(VERTEX_MSL), {0, 3})
        self.assertEqual(bsd.integer_output_candidates(FRAGMENT_MSL), [0])

    def test_vertex_rules(self):
        cap = self.capture(control(14, 0), code())
        required = {'constant_buffers': {'3': {'binding': None}, '13': {'binding': None}},
                    'textures': {'8': {'slot': None, 'type': None}, '10': {'slot': None, 'type': None},
                                 str(0x10000008): {'slot': None, 'type': None, 'sampler_slot': None}},
                    'attributes': {'0': None, '1': None}, 'device_state': {'window_origin_mode': None, 'depth_mode': None}}
        bindings = [{'kind': 'tex', 'handle': 8, 'msl_type': 'texture2d_array<float>'},
                    {'kind': 'samp_tex', 'handle': 8, 'msl_type': 'sampler'},
                    {'kind': 'tex', 'handle': 10, 'msl_type': 'texture3d<float>'}]
        doc, notes = bsd.derive_facts(cap, required, bindings, VERTEX_MSL)
        self.assertEqual(doc['constant_buffers'], {'3': {'binding': 0}, '13': {'binding': 10}})
        self.assertEqual(doc['textures'], {'8': {'slot': 0, 'type': '2darray'}, '10': {'slot': 1, 'type': '3d'}})
        self.assertEqual(doc['attributes'], {'0': 'uint', '1': 'float'})
        self.assertEqual(doc['device_state'], {'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_minus_w'})
        self.assertEqual(notes, ['texture handle 0x10000008 is not a combined driver-table handle'])
        self.assertTrue(doc['provenance'].startswith('botw-derive-v5: '))
        path = os.path.join(self.dir, cap.key + '.facts.json')
        Path(path).write_text(json.dumps(doc))
        f = bsb.load_facts(path, cap)  # derived documents satisfy the facts schema
        self.assertEqual(f.textures[8], {'slot': 0, 'type': '2darray'})

    def test_compute_rule_reads_control(self):
        ctl = control(14, 5, c1_offset=0x20, code_size=0x20)
        struct.pack_into('<4I', ctl, bsd.COMPUTE_LOCAL_OFFSET, 32, 2, 1, 0)
        struct.pack_into('<I', ctl, bsd.COMPUTE_SHARED_OFFSET, 0x17c)
        cap = self.capture(ctl, code(0x20, graphics=False))
        doc, notes = bsd.derive_facts(cap, {'compute': {'local_size': None, 'shared_memory': None}}, [], '')
        self.assertEqual((doc['compute'], notes), ({'local_size': [32, 2, 1], 'shared_memory': 0x180}, []))
        struct.pack_into('<3I', ctl, bsd.COMPUTE_LOCAL_OFFSET, 64, 32, 1)  # 2048 threads
        cap = self.capture(ctl, code(0x20, graphics=False))
        doc, notes = bsd.derive_facts(cap, {'compute': {'local_size': None, 'shared_memory': None}}, [], '')
        self.assertNotIn('compute', doc)
        self.assertIn('out of range', notes[0])

    def test_compare_facts(self):
        est = {'constant_buffers': {'3': {'binding': 0}}, 'outputs': {'0': 'uint'},
               'device_state': {'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_minus_w'}}
        der = {'constant_buffers': {'3': {'binding': 0}}, 'outputs': {'0': 'float'},
               'device_state': {'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_minus_w'}}
        self.assertEqual(bsd.compare_facts(der, est), ["outputs[0]: established uint, derived float"])
        self.assertEqual(bsd.compare_facts(est, est), [])

    def test_derive_command(self):
        vs = self.capture(control(14, 0), code())
        ps = self.capture(control(9, 1), code(sph_type=5))
        probe = os.path.join(self.dir, 'probe-000000000000'); os.mkdir(probe)
        for cap, msl, req in ((vs, VERTEX_MSL, {'attributes': {'0': None, '1': None}}),
                              (ps, FRAGMENT_MSL, {'outputs': {'0': None, '1': None}})):
            os.mkdir(os.path.join(probe, cap.key))
            Path(probe, cap.key, cap.key + '.probe.json').write_text(json.dumps(
                {'sha256': cap.sha256, 'required_facts': req, 'bindings': []}))
            Path(probe, cap.key, cap.key + '.metal').write_text(msl)
        Path(probe, 'manifest-probe.json').write_text(json.dumps({
            'schema': bsb.MANIFEST_SCHEMA, 'mode': 'probe', 'captures': self.caps,
            'results': [{'key': vs.key, 'status': 'probe'}, {'key': ps.key, 'status': 'probe'},
                        {'key': 'f' * 16, 'status': 'failed', 'errors': ['translation failed']}]}))
        est = os.path.join(self.dir, 'established'); os.mkdir(est)
        established = {'schema': 'botw-shader-facts-v1', 'key': ps.key, 'sha256': ps.sha256, 'stage': 'fragment',
                       'provenance': 'synthetic observation', 'outputs': {'0': 'uint', '1': 'float'}}
        Path(est, ps.key + '.facts.json').write_text(json.dumps(established))
        out = os.path.join(self.dir, 'facts')
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            rc = bsd.main([self.caps, '--probe-manifest', os.path.join(probe, 'manifest-probe.json'),
                           '--facts-out', out, '--established', est])
        self.assertEqual(rc, 0, stdout.getvalue())
        report = json.loads(Path(out, 'derive-report.json').read_text())
        self.assertEqual((report['derived'], report['established_copied']), (1, 1))
        self.assertEqual(report['skipped'], {'f' * 16: ['translation failed']})
        self.assertEqual(report['established_differences'], {ps.key: ['outputs[0]: established uint, derived float']})
        self.assertEqual(report['integer_output_candidates'], {ps.key: [0]})
        # Established facts are copied byte for byte and take precedence.
        self.assertEqual(Path(out, ps.key + '.facts.json').read_text(), json.dumps(established))
        derived = json.loads(Path(out, vs.key + '.facts.json').read_text())
        self.assertEqual(derived['attributes'], {'0': 'uint', '1': 'float'})
        bsb.load_facts(os.path.join(out, vs.key + '.facts.json'), vs)


class QueriedTextureTypes(unittest.TestCase):
    def test_queried_handles_are_assumed_2d_and_reported(self):
        notes = ['texture handle 8 (cbuf -1, slot 0) has no PC type, assuming 2D', 'unrelated note',
                 'texture handle A (cbuf 2, slot 1) has no PC type, assuming 2D']
        self.assertEqual(bsd.queried_texture_handles(notes), {8, 10})
        with tempfile.TemporaryDirectory(prefix='botw_shader_derive_') as tmp:
            key, _ = write_capture(tmp, control(14, 0), code())
            vs = bsb.load_capture(tmp, key)
            bindings = [{'kind': 'tex', 'handle': 8, 'msl_type': 'texture2d<float>'},
                        {'kind': 'tex', 'handle': 10, 'msl_type': 'texture2d_ms<float>'},
                        {'kind': 'tex', 'handle': 12, 'msl_type': 'texture3d<float>'}]
            doc, notes = bsd.derive_facts(vs, {'textures': {'8': {}, '10': {}, '12': {}}}, bindings, '', {8, 10})
            # The translator's 2D answer combines with what the instruction encodes (multisample).
            self.assertEqual(doc['textures'], {'8': {'slot': 0, 'type': '2d'}, '10': {'slot': 1, 'type': '2dms'},
                                               '12': {'slot': 2, 'type': '3d'}})
            self.assertEqual(notes, [{'review': 'assumed_texture_type', 'handle': 8}, {'review': 'assumed_texture_type', 'handle': 10}])
            self.assertIn('tex-type=2d-assumed-where-queried', doc['provenance'])


class ComputeTables(unittest.TestCase):
    def test_uniform_pointers_storage_and_images(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_derive_') as tmp:
            key, _ = write_capture(tmp, control(14, 5, code_size=0x180), code(graphics=False))
            cs = bsb.load_capture(tmp, key)
            required = {'uniform_pointers': {str(0x260): {}, str(0x2d0): {}, str(0x208): {}},
                        'storage_buffers': {str(0x310): {}, str(0x380): {}},
                        'images': {str(0x48): {}, str(0x4a): {}, str(0x4c): {}, str(0x10000048): {}}}
            bindings = [{'kind': 'img', 'handle': 0x48, 'msl_name': 'cp_i_tcb_48_', 'msl_type': 'texture2d<uint, access::read_write>'},
                        {'kind': 'img', 'handle': 0x4a, 'msl_name': 'cp_i_tcb_4A_r32ui', 'msl_type': 'texture2d<uint, access::read_write>'},
                        {'kind': 'img', 'handle': 0x4c, 'msl_name': 'cp_i_tcb_4C_bogus', 'msl_type': 'texture2d<uint, access::write>'}]
            doc, notes = bsd.derive_facts(cs, required, bindings, '')
            self.assertEqual(doc['uniform_pointers'], {str(0x260): {'binding': 5}, str(0x2d0): {'binding': 12}})
            self.assertEqual(doc['storage_buffers'], {str(0x310): {'slot': 0}, str(0x380): {'slot': 7}})
            self.assertEqual(doc['images'], {str(0x48): {'slot': 0, 'type': '2d', 'format': 'Unknown'},
                                             str(0x4a): {'slot': 1, 'type': '2d', 'format': 'R32Uint'}})
            self.assertEqual(sorted(notes), sorted([f'uniform_pointers[{0x208}] has no derivation rule',
                                                    f'images[{0x4c}] is not an image-table handle with a known type and format',
                                                    f'images[{0x10000048}] is not an image-table handle with a known type and format']))
            path = os.path.join(tmp, key + '.facts.json')
            Path(path).write_text(json.dumps(dict(doc, compute={'local_size': [8, 8, 1], 'shared_memory': 0})))
            bsb.load_facts(path, cs)


class StorageBuffers(unittest.TestCase):
    def test_graphics_storage_buffer_table(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_derive_') as tmp:
            key, _ = write_capture(tmp, control(14, 0), code())
            vs = bsb.load_capture(tmp, key)
            doc, notes = bsd.derive_facts(vs, {'storage_buffers': {'272': {'slot': None}, '288': {'slot': None},
                                                                   '264': {'slot': None}, '528': {'slot': None}}}, [], '')
            self.assertEqual(doc['storage_buffers'], {'272': {'slot': 0}, '288': {'slot': 1}})
            self.assertEqual(len(notes), 2)  # 0x108 is unaligned, 0x210 is past the 16 vertex slots
            key, _ = write_capture(tmp, control(9, 1), code(sph_type=5))
            ps = bsb.load_capture(tmp, key)
            doc, _ = bsd.derive_facts(ps, {'storage_buffers': {str(0x510 + 32): {'slot': None}}}, [], '')
            self.assertEqual(doc['storage_buffers'], {str(0x510 + 32): {'slot': 2}})


if __name__ == '__main__':
    unittest.main()
