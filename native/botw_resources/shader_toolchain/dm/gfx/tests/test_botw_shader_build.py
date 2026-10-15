"""Synthetic tests for tools/botw_shader_build.py. No game bytes are embedded or
required; temporary files live in the system temporary directory, outside the
repository. The end-to-end probe test runs only when BOTW_XLATE and SPIRV_CROSS
name a dmsh_xlate build and a spirv-cross binary.

python3 test_botw_shader_build.py
"""
import importlib.util
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


def control(version, stage, c1_offset=0x100, code_size=0x180):
    size = bsb.CONTROL_SIZES[version]
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
        for edit, text in (((8, 10), 'magic or version'), ((0x20, 0x7d0), 'control size'),
                           ((0x708, 0x40), 'C1 bounds'), ((0x700, 0x188), 'C1 bounds'), ((0x714, 6), 'C1 bounds')):
            c = control(9, 0)
            struct.pack_into('<I', c, *edit)
            with self.assertRaises(bsb.CaptureError) as e:
                bsb.parse_control(bytes(c))
            self.assertIn(text, str(e.exception))
        with self.assertRaises(bsb.CaptureError):
            bsb.check_code(bsb.parse_control(bytes(control(9, 0))), bytes(code(sph_type=5)))

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
};

struct main0_out
{
    float4 m_1 [[color(0)]];
    uint4 m_2 [[color(2)]];
};

fragment main0_out main0(constant spvDescriptorSetBuffer0& spvDescriptorSet0 [[buffer(0)]])
'''
SYNTHETIC_RES = {'notes': [], 'cbufs': [], 'sbufs': [], 'images': [],
                 'textures': [{'binding': 3, 'handle': 8, 'cbuf_slot': -1}, {'binding': 5, 'handle': 0x1c, 'cbuf_slot': 4}]}


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
        self.assertEqual(what, {'fp_c3', 'fp_c2', 'fp_t_tcb_8', 'fp_t_tcb_8Smplr', 'fp_t_cb4_1C', 'output 0', 'output 2', 'device_state'})
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
        self.assertEqual(by['support_buffer']['kind'], 'support')
        # Driver bank reads and bindless handles stay unknown even with facts.
        self.assertEqual({u['what'] for u in unknowns}, {'fp_c2', 'fp_t_cb4_1C'})
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
        self.assertEqual({c['what'] for c in conflicts}, {'fp_t_tcb_8', 'output 0'})
        self.assertEqual(unused, ['textures[9]'])

    def test_translator_notes_block(self):
        res = dict(SYNTHETIC_RES, notes=['synthetic fallback note'])
        _, _, unknowns, _, _, _ = bsb.analyze(self.cap, bsb.Facts(), res, SYNTHETIC_MSL)
        self.assertIn('translator query fallback', {u['what'] for u in unknowns})

    def test_job_never_passes_texture_tables(self):
        f = self.facts(textures={'8': {'slot': 1, 'type': '2d'}}, outputs={'0': 'float'})
        job = bsb.make_job(self.cap, f, '/nonexistent', {'xlate_debug': False, 'xlate_reduced': False})
        self.assertEqual((job['tex'], job['img'], job['fout'], job['stage']), ({}, {}, {'0': 'float'}, 'ps'))
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


class RunnablePublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='botw_shader_publish_')
        self.root = os.path.join(self.tmp.name, 'botw-b-0123456789ab')
        self.work = os.path.join(self.tmp.name, 'work')
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
