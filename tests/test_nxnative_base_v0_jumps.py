"""Pure rewriter-function checks; no upstream CLI or Guest execution."""
import ast
import bisect
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from botw_native.nso import inspect_nso, initialized_image

PIN = '65fa9caab486768f70da98c51c2421aff76f676d'
TOOL = ROOT / 'experiments/nxnative_base_v0/upstream/dm/tools/patch_nso.py'
TOOL_SHA = '2b3c034c9151d9b20e860ae374e96f4ee56c009f98bddd34bbc5adc3adf9ff36'
PROVER = ROOT / 'scripts/botw_native/branch_tables.py'
PROVER_SHA = 'a935a62f2640763ec2f11569a8ffcbcb794cb63efd69409bb21fc4d23dd8b9fa'
PATCH = ROOT / 'experiments/nxnative_base_v0/patches/base-v0-signed-jump-table.patch'
PROFILE = ROOT / 'experiments/nxnative_base_v0/profile/base_v0.json'
EXEFS = Path(os.environ.get('NXNATIVE_FIXED_EXEFS', ROOT / 'outputs/botw-native-base/ExeFS'))
BASELINE = os.environ.get('NXNATIVE_JUMPS_BASELINE') == '1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def extract(source):
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'jump_offsets')
    # Execute only this reviewed, pure function definition, not top-level IO.
    return compile(ast.Module(body=[node], type_ignores=[]), '<owned-pure-jump_offsets>', 'exec')


def fixture(load=13):
    words = [0x2a0003f2, 0x71000a5f, 0x540001c8, 0xb0000008, 0x91040108,
             0xb8b27900 | load, 0x8b080012 | (load << 5)]
    words += [0xd503201f] * 8 + [0xd61f0240, 0xd65f03c0]
    image = bytearray(0x2000)
    for i, target in enumerate((0x100, 0x180, 0x200)):
        struct.pack_into('<i', image, 0x1100 + 4*i, target - 0x1100)
    return words, image


def function_starts(image, text_end):
    """Read the same fixed ELF .eh_frame_hdr bounds consumed by native CLI."""
    mod = struct.unpack_from('<I', image, 4)[0]
    if image[mod:mod+4] != b'MOD0':
        raise ValueError('Missing original MOD0')
    eh = mod + struct.unpack_from('<i', image, mod+16)[0]
    _, penc, cenc, tenc = image[eh:eh+4]
    def read(offset, encoding):
        fmt, application = encoding & 15, encoding & 0x70
        form, size = {3:('<I',4), 11:('<i',4), 4:('<Q',8)}[fmt]
        value = struct.unpack_from(form, image, offset)[0]
        return value + (offset if application == 0x10 else eh if application == 0x30 else 0), size
    offset = eh+4
    _, size = read(offset, penc)
    offset += size
    count, size = read(offset, cenc)
    offset += size
    if not 0 <= count <= 1000000:
        raise ValueError('Unbounded original function table')
    starts = set()
    for _ in range(count):
        address, size = read(offset, tenc)
        offset += size
        _, size = read(offset, tenc)
        offset += size
        if 0 <= address < text_end:
            starts.add(address)
    return sorted(starts)


class NxnativeBaseV0Jumps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = TOOL.read_bytes()
        if digest(cls.original) != TOOL_SHA or digest(PROVER.read_bytes()) != PROVER_SHA:
            raise RuntimeError('Pinned source/prover identity mismatch')
        cls.tmp = tempfile.TemporaryDirectory(prefix='nxnative-owned-jumps-')
        cls.directory = Path(cls.tmp.name)
        tool = cls.directory / 'dm/tools/patch_nso.py'
        tool.parent.mkdir(parents=True)
        tool.write_bytes(cls.original)
        if not BASELINE:
            subprocess.run(['patch', '--fuzz=0', '--batch', '-p1', '-i', str(PATCH)],
                           cwd=cls.directory, check=True, capture_output=True)
        cls.patched = tool.read_bytes()
        cls.code = extract(cls.patched)
        cls.md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        cls.md.detail = True

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_words(self, words, image, *, text=0x400, ro=0x1000, data=0x2000,
                  trailing=(), jump=None):
        code = struct.pack('<' + 'I'*len(words), *words)
        image = bytearray(image)
        image[:len(code)] = code
        all_code = code + struct.pack('<' + 'I'*len(trailing), *trailing)
        ins = list(self.md.disasm(all_code, 0))
        j = next(i for i, p in enumerate(ins) if p.mnemonic == 'br' and p.op_str == 'x18') if jump is None else jump
        namespace = {'re': __import__('re'), 'struct': struct, 'IMG': image,
                     'TEXT_END': text, 'RO_OFF': ro, 'DATA_OFF': data}
        exec(self.code, namespace)
        return namespace['jump_offsets'](ins, j)

    def test_aliases_return_the_exact_three_targets(self):
        for load in (0, 9, 13, 14, 17):
            with self.subTest(load=load):
                words, image = fixture(load)
                self.assertEqual(self.run_words(words, image), [0x100, 0x180, 0x200])

    def test_original_x18_case_preserves_native_result(self):
        words, image = fixture(18)
        self.assertEqual(self.run_words(words, image), [0x100, 0x180, 0x200])

    def test_alias_rejects_missing_or_wrong_unsigned_guard(self):
        for word in (0xd503201f, 0x540001c3):  # nop / b.lo instead of b.hi
            with self.subTest(word=word):
                words, image = fixture()
                words[2] = word
                self.assertIsNone(self.run_words(words, image))
        words, image = fixture()
        words[0] = 0xd503201f  # no preceding W18 definition / upper-zero proof
        self.assertIsNone(self.run_words(words, image))

    def test_alias_rejects_base_temporary_or_index_mismatch(self):
        cases = ((5, 0xb8b27908),  # load aliases table base x8
                 (6, 0x8b0901b2),  # add uses a different base x9
                 (5, 0xb8b1790d),  # load uses index x17 rather than guarded x18
                 (6, 0x8b0801d2))  # add uses x14, not loaded x13
        for index, word in cases:
            with self.subTest(index=index, word=word):
                words, image = fixture()
                words[index] = word
                self.assertIsNone(self.run_words(words, image))
        words, image = fixture()
        words.insert(6, 0xaa0003ed)  # loaded x13 clobbered before add
        self.assertIsNone(self.run_words(words, image))
        words, image = fixture()
        words[7] = 0x2a0103f2  # target x18 overwritten after the add
        self.assertIsNone(self.run_words(words, image))

    def test_alias_rejects_bypass_and_unguarded_branch_target(self):
        words, image = fixture()
        target, origin = 4, 4*len(words)
        incoming = 0x14000000 | (((target-origin)//4) & 0x3ffffff)
        self.assertIsNone(self.run_words(words, image, trailing=(incoming,)))
        words, image = fixture()
        words[2] = 0x54000048  # b.hi into the producer region
        self.assertIsNone(self.run_words(words, image))

    def test_alias_rejects_nonreadonly_or_outside_table_and_targets(self):
        words, image = fixture()
        self.assertIsNone(self.run_words(words, image, ro=0x1200))
        self.assertIsNone(self.run_words(words, image, data=0x1108))
        for target in (-4, 0x8000, 0x101):
            with self.subTest(target=target):
                words, image = fixture()
                struct.pack_into('<i', image, 0x1100, target-0x1100)
                self.assertIsNone(self.run_words(words, image))
        words, image = fixture()
        words[1] = 0x713ffe5f  # cmp w18,#4095; table exceeds readonly interval
        self.assertIsNone(self.run_words(words, image))

    def test_patch_is_exactly_reversible_and_keeps_other_algorithm_functions(self):
        if BASELINE:
            self.assertEqual(self.patched, self.original)
            return
        subprocess.run(['patch', '--fuzz=0', '--batch', '-R', '-p1', '-i', str(PATCH)],
                       cwd=self.directory, check=True, capture_output=True)
        self.assertEqual((self.directory/'dm/tools/patch_nso.py').read_bytes(), self.original)
        subprocess.run(['patch', '--fuzz=0', '--batch', '-p1', '-i', str(PATCH)],
                       cwd=self.directory, check=True, capture_output=True)
        old_tree, new_tree = ast.parse(self.original), ast.parse(self.patched)
        old = [ast.dump(n, include_attributes=False) for n in old_tree.body
               if not (isinstance(n, ast.FunctionDef) and n.name == 'jump_offsets')]
        new = [ast.dump(n, include_attributes=False) for n in new_tree.body
               if not (isinstance(n, ast.FunctionDef) and n.name == 'jump_offsets')]
        self.assertEqual(new, old)
        self.assertEqual(TOOL.read_bytes(), self.original)

    @unittest.skipUnless((EXEFS/'subsdk3').is_file(), 'Fixed private NSO inputs not supplied')
    def test_all_seven_input_identities_and_fourteen_fixed_disk_tables(self):
        profile = json.loads(PROFILE.read_text())
        images, before = {}, {}
        for name, expected in profile['modules'].items():
            raw = (EXEFS/name).read_bytes()
            before[name] = digest(raw)
            meta = inspect_nso(raw, name)
            self.assertEqual({k:meta[k] for k in ('bytes', 'sha256', 'build_id')},
                             {k:expected[k] for k in ('bytes', 'sha256', 'build_id')})
            images[name] = (initialized_image(raw, name), meta)
        sites = {'main': (0x12a0084, 0x12a1fe0, 0x12dec3c, 0x12df1d8,
                         0x12df79c, 0x12dfd30, 0x12e4714, 0x12e4b50,
                         0x12e4fac, 0x12e53e8),
                 'subsdk3': (0x18b1a4, 0x18c430, 0x18e898, 0x18fb2c)}
        for name, origins in sites.items():
            image, meta = images[name]
            starts = function_starts(image, meta['segments'][0]['bytes'])
            namespace = {'re': __import__('re'), 'struct': struct, 'IMG': image,
                         'TEXT_END': meta['segments'][0]['bytes'],
                         'RO_OFF': meta['segments'][1]['address'],
                         'DATA_OFF': meta['segments'][2]['address']}
            exec(self.code, namespace)
            for origin in origins:
                with self.subTest(module=name, origin=hex(origin)):
                    # Fixed disk instructions are disassembled as data, never executed.
                    index = bisect.bisect_right(starts, origin)-1
                    self.assertGreaterEqual(index, 0)
                    lo = starts[index]
                    hi = starts[index+1] if index+1 < len(starts) else meta['segments'][0]['bytes']
                    ins = list(self.md.disasm(image[lo:hi], lo))
                    j = next(k for k, p in enumerate(ins) if p.address == origin)
                    targets = namespace['jump_offsets'](ins, j)
                    self.assertIsNotNone(targets)
                    self.assertTrue(all(t % 4 == 0 and 0 <= t < meta['segments'][0]['bytes'] for t in targets))
        for name in before:
            self.assertEqual(digest((EXEFS/name).read_bytes()), before[name])


if __name__ == '__main__':
    unittest.main()
