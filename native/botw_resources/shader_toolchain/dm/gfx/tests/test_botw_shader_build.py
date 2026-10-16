"""Synthetic tests for tools/botw_shader_build.py. No game bytes are embedded or
required; temporary files live in the system temporary directory, outside the
repository. The end-to-end probe test runs only when BOTW_XLATE and SPIRV_CROSS
name a dmsh_xlate build and a spirv-cross binary.

python3 test_botw_shader_build.py
"""
import contextlib
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True  # keep bytecode caches out of the repository
TOOL = Path(__file__).resolve().parents[1] / 'tools/botw_shader_build.py'
spec = importlib.util.spec_from_file_location('botw_shader_build', TOOL)
bsb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsb)

# Digest asserted by t_botw_shader.c for the same synthetic version 9 vertex capture.
C_RUNTIME_DIGEST = '1cf01bc1758a927a6b3dad6770774b446fb7f8c8a33fa9f4caba94ac013ce56b'


def control(version, stage, c1_offset=0x100, code_size=0x180, size=None):
    if size is None:
        size = {9: 0x7d1, 14: 0x848}[version]
    c = bytearray(size)
    for off, value in ((0, 0x98761234), (4, 1), (8, version), (0x20, size), (0x700, c1_offset),
                       (0x704, code_size), (0x708, 0 if stage == 5 else 0x30), (0x714, stage)):
        struct.pack_into('<I', c, off, value)
    return c


def code(size=0x180, sph_type=1, graphics=True):
    b = bytearray((17 * j + 3) & 0xff for j in range(size))
    if graphics:
        struct.pack_into('<I', b, 0, 0x12345678)
        struct.pack_into('<I', b, 0x30, 1 | sph_type << 10)
    return b


def write_capture(directory, ctl, cod, mutate=None):
    """Writes a capture exactly as botw_shader.c would; mutate edits the metadata text."""
    fields = bsb.parse_control(bytes(ctl))
    digest = bsb.capture_digest(fields, bytes(ctl), bytes(cod))
    key = digest[:16]
    text = bsb.canonical_capture_json(key, digest, fields, bsb.sha256_hex(bytes(ctl)), bsb.sha256_hex(bytes(cod)))
    if mutate:
        text = mutate(text)
    for suffix, data in (('.control', bytes(ctl)), ('.code', bytes(cod)), ('.json', text.encode())):
        Path(directory, key + suffix).write_bytes(data)
    return key, digest


class Captures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='botw_shader_build_test_')
        self.dir = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_digest_matches_c_runtime(self):
        key, digest = write_capture(self.dir, control(9, 0), code())
        self.assertEqual(digest, C_RUNTIME_DIGEST)
        c = bsb.load_capture(self.dir, key)
        self.assertEqual((c.stage_name, c.fields['c1_size'], len(c.c1)), ('vertex', 0x80, 0x80))
        self.assertEqual(c.c1, bytes(code())[0x100:0x180])
        self.assertEqual(c.translator_code, bytes(code())[0x30:])

    def test_compute_version14(self):
        key, _ = write_capture(self.dir, control(14, 5, c1_offset=0x40, code_size=0x80), code(0x80, graphics=False))
        c = bsb.load_capture(self.dir, key)
        self.assertEqual((c.stage_name, c.fields['control_size'], c.fields['code_offset']), ('compute', 0x848, 0))
        self.assertEqual(c.translator_code, bytes(code(0x80, graphics=False)))

    def expect_error(self, key, text):
        with self.assertRaises(bsb.CaptureError) as e:
            bsb.load_capture(self.dir, key)
        self.assertIn(text, str(e.exception))

    def test_tampered_code(self):
        key, _ = write_capture(self.dir, control(9, 0), code())
        p = Path(self.dir, key + '.code')
        b = bytearray(p.read_bytes()); b[0x90] ^= 1; p.write_bytes(b)
        self.expect_error(key, 'code_sha256')

    def test_digest_recomputed_not_trusted(self):
        key, _ = write_capture(self.dir, control(9, 0), code())
        b = bytearray(code()); b[0x90] ^= 1
        Path(self.dir, key + '.code').write_bytes(b)
        meta = Path(self.dir, key + '.json')
        meta.write_text(meta.read_text().replace(bsb.sha256_hex(bytes(code())), bsb.sha256_hex(bytes(b))))
        self.expect_error(key, 'digest mismatch')

    def test_metadata_must_be_canonical(self):
        key, _ = write_capture(self.dir, control(9, 0), code(), lambda t: t.replace('{\n  "schema"', '{\n "schema"'))
        self.expect_error(key, 'exact runtime serialization')

    def test_metadata_fields(self):
        key, _ = write_capture(self.dir, control(9, 0), code(), lambda t: t.replace('"stage": 0', '"stage": 1'))
        self.expect_error(key, 'stage is 1 in metadata')
        key, _ = write_capture(self.dir, control(9, 1, c1_offset=0x88), code(sph_type=5),
                               lambda t: t.replace('"code_file"', '"extra": 1,\n  "code_file"'))
        self.expect_error(key, 'extra')

    def test_hash_metadata_order(self):
        key, _ = write_capture(self.dir, control(9, 0), code(), lambda t: t.replace('[1, 0, 2001', '[0, 1, 2001').replace('[9, 0, 2001', '[0, 9, 2001'))
        self.expect_error(key, 'hash_metadata_le32')

    def test_duplicate_json_key(self):
        key, _ = write_capture(self.dir, control(9, 0), code(), lambda t: t.replace('"version": 9,', '"version": 9,\n  "version": 9,'))
        self.expect_error(key, 'duplicate')

    def test_key_file_name(self):
        key, _ = write_capture(self.dir, control(9, 0), code())
        other = 'f' * 16
        for suffix in ('.control', '.code', '.json'):
            os.rename(os.path.join(self.dir, key + suffix), os.path.join(self.dir, other + suffix))
        self.expect_error(other, 'does not match file name')

    def test_symlinked_blob(self):
        key, _ = write_capture(self.dir, control(9, 0), code())
        p = os.path.join(self.dir, key + '.code')
        os.rename(p, p + '.real'); os.symlink(p + '.real', p)
        self.expect_error(key, 'cannot open')

    def test_control_contract(self):
        for edit, text in (((8, 10), 'magic or version'), ((0x20, 0x7d0), 'header says'),
                           ((0x708, 0x40), 'C1 bounds'), ((0x700, 0x188), 'C1 bounds'), ((0x714, 6), 'C1 bounds')):
            c = control(9, 0)
            struct.pack_into('<I', c, *edit)
            with self.assertRaises(bsb.CaptureError) as e:
                bsb.parse_control(bytes(c))
            self.assertIn(text, str(e.exception))
        with self.assertRaises(bsb.CaptureError):
            bsb.check_code(bsb.parse_control(bytes(control(9, 0))), bytes(code(sph_type=5)))

    def test_variable_control_lengths(self):
        for version, stage in ((9, 0), (14, 5)):
            for size in (bsb.CONTROL_HEADER_SIZE, 0x3ac3, bsb.CONTROL_LIMIT):
                ctl = control(version, stage, size=size)
                cod = code(graphics=stage != 5)
                key, digest = write_capture(self.dir, ctl, cod)
                loaded = bsb.load_capture(self.dir, key)
                self.assertEqual(loaded.fields['control_size'], size)
                self.assertEqual(loaded.control, bytes(ctl))
                self.assertEqual(loaded.sha256, digest)
        ctl = control(9, 0, size=0x3ac3)
        _, first = write_capture(self.dir, ctl, code())
        self.assertEqual(first, '3a437479a5035ba706bc5a7817d14cd8331a241d93a5b074b1f4399160495d6b')
        ctl[-1] ^= 1
        _, second = write_capture(self.dir, ctl, code())
        self.assertNotEqual(first, second)

    def test_control_length_bounds(self):
        for advertised in (bsb.CONTROL_HEADER_SIZE - 1, bsb.CONTROL_LIMIT + 1, 0xffffffff):
            ctl = control(9, 0)
            struct.pack_into('<I', ctl, 0x20, advertised)
            with self.assertRaisesRegex(bsb.CaptureError, 'control size'):
                bsb.parse_control(bytes(ctl))
        ctl = control(9, 0, size=0x3ac3)
        for malformed in (ctl[:-1], ctl + b'\0'):
            with self.assertRaisesRegex(bsb.CaptureError, 'header says'):
                bsb.parse_control(bytes(malformed))
        with self.assertRaisesRegex(bsb.CaptureError, 'shorter'):
            bsb.parse_control(bytes(bsb.CONTROL_HEADER_SIZE - 1))
        key, _ = write_capture(self.dir, ctl, code())
        path = Path(self.dir, key + '.control')
        with path.open('r+b') as stream:
            stream.truncate(bsb.CONTROL_LIMIT + 1)
        with self.assertRaises(bsb.CaptureError):
            bsb.load_capture(self.dir, key)

    def test_capture_listing(self):
        key, _ = write_capture(self.dir, control(9, 0), code())
        Path(self.dir, 'a' * 16 + '.code').write_bytes(b'x')
        self.assertEqual(bsb.capture_keys(self.dir), ([key], ['a' * 16]))


class Paths(unittest.TestCase):
    def test_git_guard(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_build_git_') as tmp:
            os.mkdir(os.path.join(tmp, '.git'))
            with self.assertRaises(bsb.ToolError):
                bsb.make_private_dir(os.path.join(tmp, 'a', 'b'), 'output')
            self.assertFalse(os.path.exists(os.path.join(tmp, 'a')))
            with self.assertRaises(bsb.ToolError):
                bsb.require_outside_git('relative/out', 'output')
        with tempfile.TemporaryDirectory(prefix='botw_shader_build_out_') as tmp:
            self.assertTrue(os.path.isdir(bsb.make_private_dir(os.path.join(tmp, 'out'), 'output')))
        with self.assertRaises(bsb.ToolError):
            bsb.require_outside_git(str(TOOL.parent), 'output')


SYNTHETIC_MSL = '''
struct spvDescriptorSetBuffer0
{
    constant _16* fp_c3 [[id(0)]];
    constant _16* fp_c1 [[id(1)]];
    constant _16* fp_c2 [[id(2)]];
    texture2d<float> fp_t_tcb_8 [[id(3)]];
    sampler fp_t_tcb_8Smplr [[id(4)]];
    texture2d<float> fp_t_cb4_1C [[id(5)]];
    constant spvSupport* support_buffer [[id(6)]];
    texture2d<float> fp_t_cb2_8 [[id(7)]];
    sampler fp_t_cb2_8Smplr [[id(8)]];
};

struct main0_out
{
    float4 m_1 [[color(0)]];
    uint4 m_2 [[color(2)]];
};

fragment main0_out main0(constant spvDescriptorSetBuffer0& spvDescriptorSet0 [[buffer(0)]])
'''
SYNTHETIC_RES = {'notes': [], 'cbufs': [], 'sbufs': [], 'images': [],
                 'textures': [{'binding': 3, 'handle': 8, 'cbuf_slot': -1}, {'binding': 5, 'handle': 0x1c, 'cbuf_slot': 4},
                              {'binding': 7, 'handle': 8, 'cbuf_slot': 2}]}


class FactsAndAnalysis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='botw_shader_build_facts_')
        self.dir = os.path.realpath(self.tmp.name)
        key, _ = write_capture(self.dir, control(9, 1, c1_offset=0x100), code(sph_type=5))
        self.cap = bsb.load_capture(self.dir, key)

    def tearDown(self):
        self.tmp.cleanup()

    def facts(self, **extra):
        doc = {'schema': 'botw-shader-facts-v1', 'key': self.cap.key, 'sha256': self.cap.sha256,
               'stage': 'fragment', 'provenance': 'synthetic test'}
        doc.update(extra)
        p = os.path.join(self.dir, self.cap.key + '.facts.json')
        Path(p).write_text(json.dumps(doc))
        return bsb.load_facts(p, self.cap)

    def test_facts_binding(self):
        for bad in ({'sha256': '0' * 64}, {'stage': 'vertex'}, {'provenance': ' '}, {'surprise': 1},
                    {'attributes': {'0': 'float'}}, {'compute': {'local_size': [1, 1, 1], 'shared_memory': 0}},
                    {'constant_buffers': {'2': {'binding': 0}}}, {'constant_buffers': {'03': {'binding': 0}}},
                    {'constant_buffers': {'3': {'binding': True}}}, {'textures': {'8': {'slot': 0, 'type': '2D'}}},
                    {'outputs': {'8': 'float'}}, {'device_state': {'window_origin_mode': 'upper_left'}},
                    {'device_state': {'window_origin_mode': 'top', 'depth_mode': 'near_is_zero'}},
                    {'device_state': {'window_origin_mode': 'upper_left', 'depth_mode': 'minus_one_to_one'}}):
            with self.assertRaises(bsb.FactsError, msg=bad):
                self.facts(**bad)

    def test_unknowns_without_facts(self):
        _, _, unknowns, conflicts, required, _ = bsb.analyze(self.cap, bsb.Facts(), SYNTHETIC_RES, SYNTHETIC_MSL)
        what = {u['what'] for u in unknowns}
        # fp_c2 is declared but never read: nothing outside the driver tables, so it is resolved.
        self.assertEqual(what, {'fp_c3', 'fp_t_tcb_8', 'fp_t_tcb_8Smplr', 'fp_t_cb2_8', 'fp_t_cb2_8Smplr',
                                'fp_t_cb4_1C', 'output 0', 'output 2', 'device_state'})
        self.assertEqual(conflicts, [])
        self.assertEqual(required['constant_buffers'], {'3': {'binding': None}})
        self.assertEqual(required['textures'], {'8': {'slot': None, 'type': None}})
        self.assertEqual(required['outputs'], {'0': None, '2': None})
        self.assertEqual(required['key'], self.cap.key)

    def test_facts_resolve_bindings(self):
        f = self.facts(constant_buffers={'3': {'binding': 0}}, textures={'8': {'slot': 1, 'type': '2d'}},
                       outputs={'0': 'float', '2': 'uint'},
                       device_state={'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_zero'})
        bindings, _, unknowns, conflicts, _, unused = bsb.analyze(self.cap, f, SYNTHETIC_RES, SYNTHETIC_MSL)
        by = {b['msl_name']: b for b in bindings}
        self.assertEqual((by['fp_c3']['kind'], by['fp_c3']['reg']), ('cbuf', 0))
        self.assertEqual(by['fp_c1']['kind'], 'c1')
        self.assertEqual((by['fp_t_tcb_8']['kind'], by['fp_t_tcb_8']['reg']), ('tex', 1))
        self.assertEqual((by['fp_t_tcb_8Smplr']['kind'], by['fp_t_tcb_8Smplr']['reg']), ('samp_tex', 1))
        # An explicit c2 handle names the same driver texture table entry as tcb.
        self.assertEqual((by['fp_t_cb2_8']['kind'], by['fp_t_cb2_8']['reg']), ('tex', 1))
        self.assertEqual((by['fp_t_cb2_8Smplr']['kind'], by['fp_t_cb2_8Smplr']['reg']), ('samp_tex', 1))
        self.assertEqual(by['support_buffer']['kind'], 'support')
        # Driver bank reads and bindless handles stay unknown even with facts.
        self.assertEqual({u['what'] for u in unknowns}, {'fp_t_cb4_1C'})
        self.assertEqual((conflicts, unused), ([], []))
        refl = bsb.reflection(self.cap, 'synthetic', ('fragment', 'main0'), bindings, [], f, 'B')
        self.assertEqual(refl['schema'], bsb.REFLECTION_SCHEMA)
        self.assertEqual((refl['route'], refl['stage'], refl['c1'], refl['key']), ('B', 'ps', self.cap.c1.hex(), self.cap.key + '_b'))
        self.assertTrue(all('unknown' not in b for b in refl['bindings']))

    def test_conflicts(self):
        f = self.facts(textures={'8': {'slot': 1, 'type': 'cube'}, '9': {'slot': 2, 'type': '2d'}},
                       outputs={'0': 'sint', '2': 'uint'},
                       device_state={'window_origin_mode': 'lower_left', 'depth_mode': 'near_is_zero'})
        _, _, _, conflicts, _, unused = bsb.analyze(self.cap, f, SYNTHETIC_RES, SYNTHETIC_MSL)
        self.assertEqual({c['what'] for c in conflicts}, {'fp_t_tcb_8', 'fp_t_cb2_8', 'output 0'})
        self.assertEqual(unused, ['textures[9]'])

    def test_translator_notes_block(self):
        res = dict(SYNTHETIC_RES, notes=['synthetic fallback note'])
        _, _, unknowns, _, _, _ = bsb.analyze(self.cap, bsb.Facts(), res, SYNTHETIC_MSL)
        self.assertIn('translator query fallback', {u['what'] for u in unknowns})

    def test_job_passes_texture_types_by_table_index(self):
        # Handle words 8 and 0xA are bytes 0x20 and 0x28 of the c2 table: translator indices 0 and 1.
        f = self.facts(textures={'8': {'slot': 1, 'type': '2d'}, '10': {'slot': 2, 'type': 'cube'}}, outputs={'0': 'float'})
        job = bsb.make_job(self.cap, f, '/nonexistent', {'xlate_debug': False, 'xlate_reduced': False})
        self.assertEqual((job['tex'], job['img'], job['fout'], job['stage']), ({'0': '2d', '1': 'cube'}, {}, {'0': 'float'}, 'ps'))
        self.assertEqual([bsb.xlate_table_index(h) for h in (8, 9, 7, 0x48)], [0, None, None, 32])
        self.assertEqual(job['c1'], self.cap.c1.hex())
        self.assertNotIn('device_state', job)

    def test_job_device_state_forwarding(self):
        for origin in ('upper_left', 'lower_left'):
            for depth in ('near_is_zero', 'near_is_minus_w'):
                state = {'window_origin_mode': origin, 'depth_mode': depth}
                f = self.facts(outputs={'0': 'float'}, device_state=state)
                job = bsb.make_job(self.cap, f, '/nonexistent', {'xlate_debug': False, 'xlate_reduced': False})
                self.assertEqual(job['device_state'], state)
                refl = bsb.reflection(self.cap, 'synthetic', ('fragment', 'main0'), [], [], f, 'B')
                self.assertEqual(refl['device_state'], state)
                f.device_state['depth_mode'] = 'changed_after_publication'
                self.assertEqual(refl['device_state'], state)


class FragmentRaster(unittest.TestCase):
    def test_written_outputs_discard_and_sample_mask(self):
        msl = '''struct main0_out
{
    float4 m_0 [[color(0)]];
    float4 m_2 [[color(2)]];
};
fragment main0_out main0() { main0_out out = {}; if (x) discard_fragment(); return out; }
'''
        self.assertEqual(bsb.fragment_raster(msl), {'discard': 1, 'sample_mask': 0, 'colors': [0, 2]})
        msl2 = 'struct main0_out\n{\n    float4 m_1 [[color(1)]];\n    uint gl_SampleMask [[sample_mask]];\n};\n'
        self.assertEqual(bsb.fragment_raster(msl2), {'discard': 0, 'sample_mask': 1, 'colors': [1]})
        self.assertEqual(bsb.fragment_raster('fragment void main0() {}')['colors'], [])


class DriverBanks(unittest.TestCase):
    def test_reads_inside_and_outside_the_filled_tables(self):
        msl = '''int a = as_type<int>((*spvDescriptorSet0.fp_c2)._m0[5].z);
float b = (*spvDescriptorSet0.fp_c2)._m0[8u].x;'''
        self.assertEqual(bsb.driver_bank_reads(msl, 'fp_c2', 2, 1), ([0x50, 0x80], []))
        self.assertEqual(bsb.driver_bank_reads('(*s.fp_c2)._m0[20].x', 'fp_c2', 2, 1), ([0x140], ['c2+0x140']))
        self.assertEqual(bsb.driver_bank_reads('(*s.fp_c2)._m0[i].x', 'fp_c2', 2, 1), ([], ['dynamic index']))
        compute = '(*s.cp_c0)._m0[38].x; (*s.cp_c0)._m0[49].y; (*s.cp_c0)._m0[65].x'
        self.assertEqual(bsb.driver_bank_reads(compute, 'cp_c0', 0, 5), ([0x260, 0x310, 0x410], ['c0+0x410']))
        self.assertEqual(bsb.driver_bank_reads('(*s.vp_c0)._m0[97].x', 'vp_c0', 0, 0), ([0x610], ['c0+0x610']))

    def test_compute_uniform_pointers_and_reflection_reads(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_drv_') as tmp:
            key, _ = write_capture(tmp, control(14, 5, c1_offset=0x20, code_size=0x20), code(0x20, graphics=False))
            cap = bsb.load_capture(tmp, key)
            msl = '''struct spvDescriptorSetBuffer0
{
    device _1* cp_s0 [[id(0)]];
    device _1* cp_s1 [[id(1)]];
    constant _2* cp_c0 [[id(2)]];
};
int x = as_type<int>((*spvDescriptorSet0.cp_c0)._m0[38].x);
'''
            res = {'notes': [], 'textures': [], 'images': [], 'cbufs': [],
                   'sbufs': [{'binding': 0, 'cb_slot': 0, 'cb_offset': 0x260 // 4, 'flags': 0},
                             {'binding': 1, 'cb_slot': 0, 'cb_offset': 0x310 // 4, 'flags': 2}]}
            bindings, _, unknowns, _, required, _ = bsb.analyze(cap, bsb.Facts(), res, msl)
            self.assertEqual(required['uniform_pointers'], {str(0x260): {'binding': None}})
            self.assertEqual(required['storage_buffers'], {str(0x310): {'slot': None}})
            doc = {'schema': 'botw-shader-facts-v1', 'key': cap.key, 'sha256': cap.sha256, 'stage': 'compute',
                   'provenance': 'synthetic', 'uniform_pointers': {str(0x260): {'binding': 5}},
                   'storage_buffers': {str(0x310): {'slot': 0}}, 'compute': {'local_size': [1, 1, 1], 'shared_memory': 0}}
            path = os.path.join(tmp, key + '.facts.json'); Path(path).write_text(json.dumps(doc))
            f = bsb.load_facts(path, cap)
            bindings, _, unknowns, conflicts, _, unused = bsb.analyze(cap, f, res, msl)
            by = {b['msl_name']: b for b in bindings}
            self.assertEqual((by['cp_s0']['kind'], by['cp_s0']['reg']), ('ubo_ptr', 5))
            self.assertEqual((by['cp_s1']['kind'], by['cp_s1']['reg'], by['cp_s1']['rw']), ('sbuf', 0, True))
            self.assertEqual((by['cp_c0']['kind'], by['cp_c0']['vec4_offsets']), ('drvcb', [0x260]))
            self.assertEqual((unknowns, conflicts, unused), ([], [], []))
            refl = bsb.reflection(cap, 'synthetic', ('kernel', 'main0'), bindings, [], f, 'B')
            self.assertEqual(refl['drv_reads'], {'0': {'vec4_offsets': [0x260], 'dynamic': False}})
            Path(path).write_text(json.dumps(dict(doc, stage='vertex')))
            with self.assertRaises(bsb.FactsError):
                bsb.load_facts(path, cap)


class RunnablePublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='botw_shader_publish_')
        base = os.path.realpath(self.tmp.name)  # builds record resolved output paths
        self.root = os.path.join(base, 'botw-b-0123456789ab')
        self.work = os.path.join(base, 'work')
        os.mkdir(self.root); os.mkdir(self.work)
        self.key = '0123456789abcdef'
        Path(self.work, self.key + '_b.metallib').write_bytes(b'MTLB synthetic metallib')
        Path(self.work, self.key + '_b.metal').write_text('// synthetic MSL\n')
        self.reflection = {
            'schema': bsb.REFLECTION_SCHEMA, 'key': self.key + '_b', 'route': 'B', 'title': 'botw',
            'chain': '0123456789ab', 'dm_ctl': self.key, 'sha256': self.key + '0' * 48,
            'stage': 'vs', 'entry': 'main0', 'notes': [], 'bindings': [],
        }
        self.result = {'key': self.key, 'status': None, 'runnable': False,
                       'errors': [], 'unknowns': [], 'conflicts': [], 'files': {},
                       'capture': {'key': self.key, 'sha256': self.key + '0' * 48,
                                   'stage_name': 'vertex', 'stage': 0}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_ready_reflection_and_manifest_are_bound_to_artifact_hashes(self):
        bsb.publish_ready(self.root, self.work, self.key, self.reflection, self.result, 'synthetic metal')
        self.assertEqual((self.result['status'], self.result['runnable']), ('ready', True))
        for name in ('metallib', 'json'):
            metadata = self.result['files'][name]
            self.assertEqual(metadata['sha256'], hashlib.sha256(Path(metadata['path']).read_bytes()).hexdigest())
        doc = json.loads(Path(self.result['files']['json']['path']).read_text())
        self.assertEqual(doc['schema'], bsb.REFLECTION_SCHEMA)
        self.assertEqual((doc['key'], doc['dm_ctl'], doc['sha256'], doc['stage']),
                         (self.key + '_b', self.key, self.key + '0' * 48, 'vs'))
        self.assertEqual((doc['route'], doc['runnable'], doc['unknowns'], doc['conflicts'], doc['notes']),
                         ('B', True, [], [], []))
        manifest = bsb.make_manifest('build', '0123456789ab', {}, {}, '/caps', '/facts', self.root,
                                     [self.result], argv=['synthetic'], timestamp='2026-10-15T00:00:00+00:00')
        self.assertEqual((manifest['schema'], manifest['runnable']), (bsb.MANIFEST_SCHEMA, True))
        duplicate = bsb.make_manifest('build', '0123456789ab', {}, {}, '/caps', '/facts', self.root,
                                      [self.result, self.result], argv=['synthetic'],
                                      timestamp='2026-10-15T00:00:00+00:00')
        self.assertFalse(duplicate['runnable'])

    def test_publication_rejects_mixed_capture_identity_and_missing_gates(self):
        for reflection in (dict(self.reflection, sha256=self.key + '1' * 48),
                           dict(self.reflection, stage='ps')):
            with self.assertRaises(bsb.ToolError):
                bsb.publish_ready(self.root, self.work, self.key, reflection, self.result, 'synthetic metal')
        for field in ('errors', 'unknowns', 'conflicts'):
            missing = dict(self.result)
            missing.pop(field)
            with self.assertRaises(bsb.ToolError):
                bsb.publish_ready(self.root, self.work, self.key, self.reflection, missing, 'synthetic metal')
        self.assertFalse(self.result['runnable'])
        self.assertEqual(list(Path(self.root).iterdir()), [])

    def test_probe_and_failed_results_never_make_a_runnable_manifest(self):
        probe = {'key': self.key, 'status': 'probe', 'runnable': False,
                 'errors': [], 'unknowns': [], 'conflicts': [], 'files': {}}
        failed = {'key': self.key, 'status': 'failed', 'runnable': False,
                  'errors': ['synthetic failure'], 'unknowns': [], 'conflicts': [], 'files': {}}
        for mode, result in (('probe', probe), ('build', failed), ('build', probe)):
            manifest = bsb.make_manifest(mode, '0123456789ab', {}, {}, '/caps', '/facts', self.root,
                                         [result], argv=['synthetic'], timestamp='2026-10-15T00:00:00+00:00')
            self.assertEqual(manifest['schema'], bsb.MANIFEST_SCHEMA)
            self.assertFalse(manifest['runnable'])
            self.assertIs(result['runnable'], False)
        blocked = dict(self.result, unknowns=[{'what': 'synthetic', 'detail': 'unresolved'}])
        with self.assertRaises(bsb.ToolError):
            bsb.publish_ready(self.root, self.work, self.key, self.reflection, blocked, 'synthetic metal')
        self.assertFalse(blocked['runnable'])

    def test_merge_replaces_only_rebuilt_keys(self):
        old_a = {'key': 'a' * 16, 'status': 'ready'}
        old_b = {'key': 'b' * 16, 'status': 'failed'}
        bsb.write_json(os.path.join(self.root, 'manifest-build.json'),
                       bsb.make_manifest('build', '0123456789ab', {}, {}, '/caps', '/facts', self.root, [old_b, old_a]))
        new_b = {'key': 'b' * 16, 'status': 'ready'}
        new_c = {'key': 'c' * 16, 'status': 'ready'}
        merged = bsb.merge_results(self.root, '0123456789ab', [new_c, new_b])
        self.assertEqual(merged, [old_a, new_b, new_c])
        # A published build keeps its complete results in build-report.json; merge reads that.
        bsb.write_json(os.path.join(self.root, 'build-report.json'),
                       bsb.make_manifest('build', '0123456789ab', {}, {}, '/caps', '/facts', self.root, [old_a, old_b, new_c]))
        self.assertEqual(bsb.merge_results(self.root, '0123456789ab', [new_b]), [old_a, new_b, new_c])
        with self.assertRaises(bsb.ToolError):
            bsb.merge_results(self.root, 'ffffffffffff', [new_b])

    def test_publish_keeps_only_ready_results(self):
        bsb.publish_ready(self.root, self.work, self.key, self.reflection, self.result, 'synthetic metal')
        failed = {'key': 'f' * 16, 'status': 'failed', 'runnable': False,
                  'errors': ['synthetic failure'], 'unknowns': [], 'conflicts': [], 'files': {}}
        full = bsb.make_manifest('build', '0123456789ab', {}, {}, '/caps', '/facts', self.root,
                                 [self.result, failed], argv=['synthetic'])
        self.assertFalse(full['runnable'])
        path = os.path.join(self.root, 'manifest-build.json')
        bsb.write_json(path, full)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bsb.main(['publish', path]), 0)
            published = json.loads(Path(path).read_text())
            self.assertTrue(published['runnable'])
            self.assertEqual([r['key'] for r in published['results']], [self.key])
            self.assertEqual(published['published']['excluded'], {'f' * 16: 'failed'})
            report = json.loads(Path(self.root, 'build-report.json').read_text())
            self.assertEqual(len(report['results']), 2)
            # Publishing again reads the full report, not the already reduced manifest.
            self.assertEqual(bsb.main(['publish', path]), 0)
            self.assertEqual(json.loads(Path(path).read_text())['published']['results'], 2)
            # A published file that changed after the build is refused.
            Path(self.root, self.key + '_b.metallib').write_bytes(b'MTLB changed')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bsb.main(['publish', path]), 2)


PAD_MSL = '''struct main0_out
{
    float4 gl_Position [[position]];
    float4 m_1 [[user(locn1)]];
};

vertex main0_out main0(main0_in in [[stage_in]])
{
    main0_out out = {};
    out.m_1 = float4(1.0);
    return out;
}
'''


class VertexPadding(unittest.TestCase):
    def test_sph_input_locations(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_pad_') as tmp:
            cod = code(sph_type=5)
            for word in range(6, 14):
                struct.pack_into('<I', cod, 0x30 + 4 * word, 0)
            struct.pack_into('<I', cod, 0x30 + 4 * 6, 0x0000ff00)   # attribute 1
            struct.pack_into('<I', cod, 0x30 + 4 * 7, 0xaa000000)   # attribute 7
            struct.pack_into('<I', cod, 0x30 + 4 * 13, 0x00010000)  # attribute 30
            key, _ = write_capture(tmp, control(9, 1), cod)
            self.assertEqual(bsb.sph_input_locations(bsb.load_capture(tmp, key)), {1, 7, 30})

    def test_pad_vertex_outputs(self):
        src, pad = bsb.pad_vertex_outputs(PAD_MSL, {1, 3, 7})
        self.assertEqual(pad, [3, 7])
        self.assertIn('    float4 dm_pad3 [[user(locn3)]];\n    float4 dm_pad7 [[user(locn7)]];\n};', src)
        self.assertIn('main0_out out = {};\n    out.dm_pad3 = float4(0.0, 0.0, 0.0, 1.0);', src)
        self.assertEqual(bsb.pad_vertex_outputs(PAD_MSL, {1}), (PAD_MSL, []))
        self.assertEqual(bsb.pad_vertex_outputs('kernel void main0() {}', {1}), ('kernel void main0() {}', None))

    def test_load_pairs(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_pad_') as tmp:
            p = Path(tmp, 'pairs.tsv')
            p.write_text('# comment\n' + 'a' * 16 + '\t' + 'b' * 16 + '\n' + 'a' * 16 + '\t' + 'c' * 16 + '\n')
            self.assertEqual(bsb.load_pairs(str(p)), {'a' * 16: {'b' * 16, 'c' * 16}})
            p.write_text('A' * 16 + '\t' + 'b' * 16 + '\n')
            with self.assertRaises(bsb.ToolError):
                bsb.load_pairs(str(p))


FAKE_XLATE = '''#!/usr/bin/env python3
# Synthetic translator: fails every non-debug job whose key starts with "f",
# and records the chunk it ran in.
import json, os, sys
jobs = json.load(open(sys.argv[1]))
for job in jobs:
    if job['key'].startswith('f') and not job['debug']:
        print(f"[FAIL] {job['key']}: InvalidCastException: synthetic optimizer failure", file=sys.stderr)
        continue
    for ext in ('.spv', '.res.json'):
        with open(os.path.join(sys.argv[2], job['key'] + ext), 'w') as f:
            f.write(json.dumps({'chunk': os.path.basename(sys.argv[1]), 'debug': job['debug']}))
'''


class Translation(unittest.TestCase):
    def test_parallel_chunks_and_debug_retry(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_xlate_') as tmp:
            exe = Path(tmp, 'fake_xlate')
            exe.write_text(FAKE_XLATE)
            exe.chmod(0o700)
            work = os.path.join(tmp, 'work'); os.mkdir(work)
            tools = bsb.Tools(xlate=str(exe))
            keys = ['a000000000000000', 'b000000000000000', 'f000000000000000', 'c000000000000000']
            jobs = [{'key': k, 'debug': False, 'reduced': False} for k in keys]
            final, xerr, retries = bsb.translate_with_retry(tools, jobs, work, parallel=3)
            self.assertEqual({k: v is None for k, v in xerr.items()}, dict.fromkeys(keys, True))
            self.assertEqual(list(retries), ['f000000000000000'])
            self.assertTrue(retries['f000000000000000']['recovered'])
            self.assertIn('synthetic optimizer failure', retries['f000000000000000']['first_error']['translator_stderr'][0])
            self.assertEqual([j['debug'] for j in final], [False, False, True, False])
            # Chunks are jobs[i::3]: a and c share the first, b has the second, f the third;
            # f is then retried on its own with debug enabled.
            chunk = {k: json.loads(Path(work, k + '.res.json').read_text()) for k in keys}
            self.assertEqual({k: v['chunk'] for k, v in chunk.items()},
                             {'a000000000000000': 'jobs-0.json', 'b000000000000000': 'jobs-1.json',
                              'f000000000000000': 'retry-0.json', 'c000000000000000': 'jobs-0.json'})
            self.assertTrue(Path(work, 'jobs-2.json').exists())

    def test_unrecovered_failure_is_reported(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_xlate_') as tmp:
            exe = Path(tmp, 'fake_xlate')
            exe.write_text(FAKE_XLATE.replace("and not job['debug']", ''))
            exe.chmod(0o700)
            work = os.path.join(tmp, 'work'); os.mkdir(work)
            _, xerr, retries = bsb.translate_with_retry(bsb.Tools(xlate=str(exe)),
                                                        [{'key': 'f000000000000000', 'debug': False, 'reduced': False}], work)
            self.assertFalse(retries['f000000000000000']['recovered'])
            self.assertEqual(xerr['f000000000000000']['missing'], ['.spv', '.res.json'])


class Cli(unittest.TestCase):
    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                              env=dict(os.environ, **(env or {})))

    def test_help(self):
        p = self.cli('--help')
        self.assertEqual(p.returncode, 0)
        self.assertIn('botw-shader-facts-v1', p.stdout)

    def test_validate_and_build_gates(self):
        with tempfile.TemporaryDirectory(prefix='botw_shader_build_cli_') as tmp:
            caps = os.path.join(tmp, 'caps'); os.mkdir(caps)
            key, _ = write_capture(caps, control(9, 0), code())
            p = self.cli('validate', caps)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn(C_RUNTIME_DIGEST, p.stdout)
            p = self.cli('build', caps, '--out', os.path.join(tmp, 'out'))
            self.assertEqual(p.returncode, 2)
            self.assertIn('require --facts', p.stderr)
            os.mkdir(os.path.join(tmp, 'repo')); os.mkdir(os.path.join(tmp, 'repo', '.git'))
            p = self.cli('build', caps, '--probe', '--out', os.path.join(tmp, 'repo', 'out'))
            self.assertEqual(p.returncode, 2)
            self.assertIn('inside the Git work tree', p.stderr)
            Path(caps, key + '.code').write_bytes(b'\0' * 0x180)
            p = self.cli('validate', caps)
            self.assertEqual(p.returncode, 1)
            self.assertIn('FAIL', p.stdout)

    @unittest.skipUnless(os.environ.get('BOTW_XLATE') and os.environ.get('SPIRV_CROSS'), 'BOTW_XLATE / SPIRV_CROSS not set')
    def test_probe_end_to_end_synthetic_compute(self):
        # Minimal hand-written Maxwell program: scheduling word, EXIT, BRA to self, NOP.
        prog = struct.pack('<4Q', 0x001ffc00fd4007ef, 0xe30000000007000f, 0xe2400fffff87000f, 0x50b0000000070f00)
        with tempfile.TemporaryDirectory(prefix='botw_shader_build_e2e_') as tmp:
            caps, facts, out = (os.path.join(tmp, n) for n in ('caps', 'facts', 'out'))
            os.mkdir(caps); os.mkdir(facts)
            key, digest = write_capture(caps, control(14, 5, c1_offset=0x20, code_size=0x20), prog)
            Path(facts, key + '.facts.json').write_text(json.dumps({
                'schema': 'botw-shader-facts-v1', 'key': key, 'sha256': digest, 'stage': 'compute',
                'provenance': 'synthetic test', 'compute': {'local_size': [8, 4, 1], 'shared_memory': 0}}))
            p = self.cli('build', caps, '--probe', '--facts', facts, '--out', out, '--metal', 'runtime')
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            probe = next(Path(out).glob(f'probe-*/{key}/{key}.probe.json'))
            refl = json.loads(probe.read_text())
            self.assertEqual((refl['route'], refl['runnable'], refl['stage'], refl['threadgroup_size']), ('probe', False, 'cs', [8, 4, 1]))
            self.assertEqual(refl['unknowns'], [])
            self.assertTrue(refl['metal']['ok'], refl['metal'])
            self.assertEqual(refl['msl_entry_kind'], 'kernel')
            self.assertIsNone(refl['device_state'])


if __name__ == '__main__':
    unittest.main()
