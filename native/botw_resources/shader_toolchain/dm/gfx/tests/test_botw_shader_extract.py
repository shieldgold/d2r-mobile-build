"""Synthetic tests for tools/botw_shader_extract.py. No game bytes are embedded or
required: shaders, BNSH / BAHS containers, SARC archives and Yaz0 streams are
built here, and temporary files live in the system temporary directory,
outside the repository. The native Yaz0 helper is exercised when xcrun clang
is available.

python3 test_botw_shader_extract.py
"""
import importlib.util
import io
import contextlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True  # keep bytecode caches out of the repository
TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bse = load('botw_shader_extract')
bsb = bse.bsb


def control(stage, version=14, code_size=0x180, seed=0):
    size = {9: 0x7d1, 14: 0x848}[version]
    c = bytearray((seed + 5 * j) & 0xff for j in range(size))
    for off, value in ((0, 0x98761234), (4, 1), (8, version), (0x20, size), (0x700, 0x100 if stage != 5 else 0x80),
                       (0x704, code_size), (0x708, 0 if stage == 5 else 0x30), (0x714, stage)):
        struct.pack_into('<I', c, off, value)
    return bytes(c)


def code(stage, size=0x180, seed=0):
    b = bytearray((seed + 17 * j + 3) & 0xff for j in range(size))
    if stage != 5:
        struct.pack_into('<I', b, 0, 0x12345678)
        sph = {0: 1, 1: 5}[stage]
        struct.pack_into('<I', b, 0x30, (struct.unpack_from('<I', b, 0x30)[0] & ~(15 << 10)) | sph << 10)
    return bytes(b)


def shader(stage, seed=0):
    return control(stage, seed=seed), code(stage, seed=seed)


def bnsh(variations):
    """variations: list of {slot: (control, code)}; returns a BNSH container."""
    out = bytearray(0x100)
    out[:8] = bse.BNSH_MAGIC
    out[0x60:0x64] = b'grsc'

    def alloc(data, align=0x10):
        while len(out) % align:
            out.append(0)
        at = len(out)
        out.extend(data)
        return at

    table = alloc(bytes(0x40 * len(variations)))
    for v, stages in enumerate(variations):
        program = alloc(bytes(0x40))
        struct.pack_into('<Q', out, table + 0x40 * v + 0x10, program)
        for slot, (ctl, cd) in stages.items():
            pc, pk = alloc(ctl, 0x100), alloc(cd, 0x100)
            desc = alloc(bytes(0x20))
            struct.pack_into('<QQII', out, desc + 8, pc, pk, len(cd), len(ctl))
            struct.pack_into('<Q', out, program + 8 + 8 * bse.BNSH_SLOTS.index(slot), desc)
    struct.pack_into('<I', out, 0x1c, len(out))
    struct.pack_into('<IQ', out, 0x7c, len(variations), table)
    return bytes(out)


def bahs(entries):
    """entries: list of (control, code); controls are shared when equal."""
    controls, table = [], []
    for ctl, _ in entries:
        if ctl not in controls:
            controls.append(ctl)
    head = bytearray(0x40)
    head[:4] = b'BAHS'
    entry_at = len(head)
    head.extend(bytes(16 * len(entries)))
    ctl_at = {}
    for ctl in controls:
        while len(head) % 0x10:
            head.append(0)
        ctl_at[ctl] = len(head)
        head.extend(ctl)
    while len(head) % 0x1000:
        head.append(0)
    region = len(head)
    codes = bytearray()
    for i, (ctl, cd) in enumerate(entries):
        while len(codes) % 0x100:
            codes.append(0)
        off = len(codes)
        codes.extend(cd)
        a = entry_at + 16 * i
        struct.pack_into('<IIIi', head, a, off, 0, len(ctl), ctl_at[ctl] - a)
    struct.pack_into('<II', head, 0x18, len(codes), region)
    return bytes(head + codes)


def sarc(members):
    names = bytearray()
    nodes = []
    blobs = bytearray()
    for name, data in members:
        offset = len(names)
        names.extend(name.encode() + b'\0')
        while len(names) % 4:
            names.append(0)
        while len(blobs) % 0x80:
            blobs.append(0)
        nodes.append((0x01000000 | offset // 4, len(blobs), len(blobs) + len(data)))
        blobs.extend(data)
    sfat = b'SFAT' + struct.pack('<HHI', 0xc, len(nodes), 0x65) + b''.join(
        struct.pack('<IIII', 0, a, s, e) for a, s, e in nodes)
    sfnt = b'SFNT' + struct.pack('<HH', 8, 0) + bytes(names)
    head_len = 0x14 + len(sfat) + len(sfnt)
    start = (head_len + 0x7f) & ~0x7f
    head = b'SARC' + struct.pack('<HHIIHH', 0x14, 0xfeff, start + len(blobs), start, 0x100, 0)
    return head + sfat + sfnt + bytes(start - head_len) + bytes(blobs)


def yaz0(data):
    """Greedy encoder; it emits back-references so the decoder's copy paths run."""
    out = bytearray(b'Yaz0' + struct.pack('>I', len(data)) + bytes(8))
    i = 0
    while i < len(data):
        flag_at = len(out)
        out.append(0)
        flags = 0
        for bit in range(7, -1, -1):
            if i >= len(data):
                break
            best, dist = 0, 0
            for j in range(max(0, i - 0x1000), i):
                n = 0
                while n < 0x111 and i + n < len(data) and data[j + n] == data[i + n]:
                    n += 1
                if n > best:
                    best, dist = n, i - j
            if best >= 3:
                d = dist - 1
                if best >= 0x12:
                    out += bytes((d >> 8, d & 0xff, best - 0x12))
                else:
                    out += bytes(((best - 2) << 4 | d >> 8, d & 0xff))
                i += best
            else:
                flags |= 1 << bit
                out.append(data[i])
                i += 1
        out[flag_at] = flags
    return bytes(out)


def native_yaz0():
    if not shutil.which('xcrun'):
        return None
    try:
        return bse.Yaz0Native(bse.build_yaz0(os.path.join(tempfile.gettempdir(), 'botw-shader-extract-test-tools')))
    except bse.ExtractError:
        return None


def read_json(path):
    with open(path) as f:
        return json.load(f)


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bse.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Yaz0Test(unittest.TestCase):
    SAMPLES = [b'', b'a', b'abcabcabcabcabcabcabcabcabc' * 9, bytes(range(256)) * 3, b'\0' * 5000]

    def test_python_round_trip(self):
        for s in self.SAMPLES:
            self.assertEqual(bse.yaz0_python(yaz0(s)), s)

    def test_native_matches_python(self):
        native = native_yaz0()
        if not native:
            self.skipTest('xcrun clang unavailable')
        for s in self.SAMPLES:
            self.assertEqual(native(yaz0(s)), s)

    def test_rejects_corrupt_streams(self):
        good = yaz0(b'xyzxyzxyzxyz' * 20)
        decoders = [bse.yaz0_python] + ([native_yaz0()] if native_yaz0() else [])
        for decode in decoders:
            with self.assertRaises(bse.ExtractError):
                decode(good[:20])  # truncated
            with self.assertRaises(bse.ExtractError):
                decode(b'Yaz0' + struct.pack('>I', 4) + bytes(8) + bytes((0, 0x10, 0x05)))  # reference before start


class ContainerTest(unittest.TestCase):
    def test_bnsh_variations_and_pairs(self):
        vs, ps = shader(0, 1), shader(1, 2)
        found = bse.Found()
        bse.scan_blob(bnsh([{'vs': vs, 'ps': ps}, {'vs': vs}]), 'x', bse.yaz0_python, found)
        self.assertEqual(len(found.stages), 3)
        keys = {st[1][:16] for st in found.stages}
        self.assertEqual(len(keys), 2)
        self.assertEqual(set(found.variations[0][1]), {'vs', 'ps'})
        for _, sha, fields, ctl, cd in found.stages:
            self.assertEqual(bsb.capture_digest(fields, ctl, cd), sha)

    def test_bnsh_slot_must_match_control_stage(self):
        with self.assertRaisesRegex(bse.ExtractError, 'ps slot holds a control for stage 0'):
            bse.scan_blob(bnsh([{'ps': shader(0)}]), 'x', bse.yaz0_python, bse.Found())

    def test_bnsh_bad_contract_stops(self):
        ctl, cd = shader(0)
        bad = bytearray(cd)
        struct.pack_into('<I', bad, 0, 0)  # code wrapper magic
        with self.assertRaisesRegex(bse.ExtractError, 'wrapper or SPH stage'):
            bse.scan_blob(bnsh([{'vs': (ctl, bytes(bad))}]), 'x', bse.yaz0_python, bse.Found())

    def test_bahs_shared_controls(self):
        ctl = control(0, version=9, seed=7)
        entries = [(ctl, code(0, seed=1)), (ctl, code(0, seed=2)), shader(1, 3)]
        stages, rejected = bse.bahs_stages(bahs(entries), 'agl')
        self.assertEqual(rejected, 0)
        self.assertEqual(len(stages), 3)
        self.assertEqual(len({s[1] for s in stages}), 3)
        self.assertEqual([s[2]['stage'] for s in stages], [0, 0, 1])

    def test_bahs_rejects_inconsistent_candidates(self):
        ctl, cd = shader(0)
        blob = bytearray(bahs([(ctl, cd)]))
        region = struct.unpack_from('<I', blob, 0x1c)[0]
        struct.pack_into('<I', blob, region, 0)  # break the code wrapper
        stages, rejected = bse.bahs_stages(bytes(blob), 'agl')
        self.assertEqual((len(stages), rejected), (0, 1))

    def test_nested_yaz0_sarc(self):
        inner = sarc([('a.bfsha', bnsh([{'vs': shader(0), 'ps': shader(1)}]))])
        outer = sarc([('pack/inner.ssarc', yaz0(inner)), ('agl.sharcb', bahs([shader(5)]))])
        found = bse.Found()
        bse.scan_blob(yaz0(outer), 'root', bse.yaz0_python, found)
        self.assertEqual(sorted(s[2]['stage'] for s in found.stages), [0, 1, 5])
        self.assertTrue(any(s[0].startswith('root/pack/inner.ssarc/a.bfsha#BNSH@') for s in found.stages))


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='botw-shader-extract-test-')
        self.romfs = os.path.join(self.tmp, 'romfs')
        os.makedirs(os.path.join(self.romfs, 'Pack'))
        self.vs, self.ps, self.cs = shader(0, 1), shader(1, 2), shader(5, 3)
        pack = sarc([('Shader/a.sbfsha', yaz0(bnsh([{'vs': self.vs, 'ps': self.ps}]))),
                     ('System/agl.sharcb', bahs([self.cs]))])
        with open(os.path.join(self.romfs, 'Pack', 'Graphics.pack'), 'wb') as f:
            f.write(pack)
        with open(os.path.join(self.romfs, 'unrelated.bin'), 'wb') as f:
            f.write(b'no shaders here' * 100)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def key(self, s):
        ctl, cd = s
        return bsb.capture_digest(bsb.parse_control(ctl), ctl, cd)[:16]

    def test_extract_writes_runtime_captures(self):
        out = os.path.join(self.tmp, 'out')
        code, stdout, _ = run_main('--romfs', self.romfs, '--out', out, '--python-yaz0', '-j', '1')
        self.assertEqual(code, 0, stdout)
        index = read_json(os.path.join(out, 'extract-index.json'))
        self.assertEqual(index['summary']['shaders'], 3)
        self.assertEqual(index['summary']['stages'], {'cs': 1, 'ps': 1, 'vs': 1})
        for s in (self.vs, self.ps, self.cs):
            cap = bsb.load_capture(out, self.key(s))  # the runtime capture contract, byte for byte
            self.assertEqual((cap.control, cap.code), s)
        with open(os.path.join(out, 'pairs.tsv')) as f:
            rows = [ln.split() for ln in f if not ln.startswith('#')]
        self.assertEqual(rows, [[self.key(self.vs), self.key(self.ps)]])
        # A second run over the same output verifies and keeps the existing files.
        self.assertEqual(run_main('--romfs', self.romfs, '--out', out, '--python-yaz0', '-j', '1')[0], 0)

    def test_compare_with_runtime_captures(self):
        out, caps = os.path.join(self.tmp, 'out'), os.path.join(self.tmp, 'captures')
        os.makedirs(caps)
        other = shader(0, 99)
        for s in (self.vs, other):
            k = self.key(s)
            f = bsb.parse_control(s[0])
            bse.write_capture(caps, k, bsb.capture_digest(f, *s), f, *s)
        code, stdout, _ = run_main('--romfs', self.romfs, '--out', out, '--python-yaz0', '-j', '1', '--compare', caps)
        self.assertEqual(code, 0)
        c = read_json(os.path.join(out, 'extract-index.json'))['compare']
        self.assertEqual((c['complete'], c['found'], c['missing']), (2, 1, [self.key(other)]))

    def test_existing_different_capture_is_an_error(self):
        out = os.path.join(self.tmp, 'out')
        os.makedirs(out)
        with open(os.path.join(out, self.key(self.vs) + '.code'), 'wb') as f:
            f.write(b'different')
        code, _, err = run_main('--romfs', self.romfs, '--out', out, '--python-yaz0', '-j', '1')
        self.assertEqual(code, 1)
        self.assertIn('differs from the extracted shader', err)

    def test_output_inside_git_is_rejected(self):
        code, _, err = run_main('--romfs', self.romfs, '--out', str(TOOLS / 'never-written'), '--python-yaz0')
        self.assertEqual(code, 2)
        self.assertIn('Git work tree', err)
        self.assertFalse((TOOLS / 'never-written').exists())


if __name__ == '__main__':
    unittest.main()
