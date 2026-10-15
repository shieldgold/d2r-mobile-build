"""Synthetic inputs only: no game code, keys or copyrighted fixtures."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
try:
    from botw_native.archive import read_pfs0, extract_exefs
    from botw_native.nso import inspect_nso
    from botw_native.rewrite import check_generic_outputs, verify_profile, verified_tool
except ModuleNotFoundError:
    read_pfs0 = extract_exefs = inspect_nso = None
    check_generic_outputs = verify_profile = verified_tool = None
try:
    from botw_native.rewrite import validate_structure
except ImportError:
    validate_structure = None
try:
    from botw_native.branch_tables import signed_jump_range
except ImportError:
    signed_jump_range = None


def pfs0(files):
    names = bytearray()
    entries = bytearray()
    data = bytearray()
    for name, content in files:
        entries += struct.pack('<QQII', len(data), len(content), len(names), 0)
        names += name.encode() + b'\0'
        data += content
    return struct.pack('<4sIII', b'PFS0', len(files), len(names), 0) + entries + names + data


def nso():
    image = bytearray(0x500)
    struct.pack_into('<I', image, 4, 0x80)
    image[0x80:0x84] = b'MOD0'
    struct.pack_into('<i', image, 0x84, 0x80)
    strings = b'\0nvnBootstrapLoader\0local_function\0'
    image[0x200:0x200 + len(strings)] = strings
    tags = [(5, 0x200), (6, 0x240), (10, len(strings)), (11, 24), (4, 0x2c0), (0, 0)]
    for i, tag in enumerate(tags):
        struct.pack_into('<QQ', image, 0x100 + i * 16, *tag)
    struct.pack_into('<IBBHQQ', image, 0x258, 1, 0x12, 0, 0, 0, 0)
    struct.pack_into('<IBBHQQ', image, 0x270, 20, 0x12, 0, 1, 0x40, 4)
    struct.pack_into('<II', image, 0x2c0, 1, 3)
    header = bytearray(0x100)
    header[:4] = b'NSO0'
    struct.pack_into('<I', header, 0xc, 0x38)
    header[0x40:0x60] = bytes.fromhex('12' * 32)
    parts = [image[:0x100], image[0x100:0x300], image[0x400:0x500]]
    for i, (fileoff, memoff, size) in enumerate([(0x100, 0, 0x100), (0x200, 0x100, 0x200), (0x400, 0x400, 0x100)]):
        struct.pack_into('<III', header, 0x10 + i * 16, fileoff, memoff, size)
        struct.pack_into('<I', header, 0x60 + i * 4, size)
        header[0xa0 + i * 32:0xc0 + i * 32] = hashlib.sha256(parts[i]).digest()
    struct.pack_into('<I', header, 0x3c, 0x80)
    return bytes(header) + b''.join(parts[:2]) + bytes(0x100) + parts[2]


def nsz(content, expected_size=None, window_descriptor=None):
    import zstandard
    size = len(content) if expected_size is None else expected_size
    section = struct.pack('<QQQQ16s16s', 0x4000, size, 3, 0, bytes(16), bytes(16))
    ncz = bytes(0x4000) + b'NCZSECTN' + struct.pack('<Q', 1) + section
    frame = bytearray(zstandard.ZstdCompressor(write_content_size=False).compress(content))
    if window_descriptor is not None:
        assert frame[4] == 0
        frame[5] = window_descriptor
    ncz += frame
    return pfs0([('program.ncz', ncz)])


class NativePreflightTests(unittest.TestCase):
    def need(self, fn):
        self.assertIsNotNone(fn, 'BOTW native preflight implementation is missing')

    def test_pfs0_offsets_include_embedded_header(self):
        self.need(read_pfs0)
        data = b'padding' + pfs0([('main', b'NSO0test'), ('sdk', b'data')])
        files = read_pfs0(io.BytesIO(data), 7, len(data) - 7)
        self.assertEqual(data[files[0].offset:files[0].offset + files[0].size], b'NSO0test')

    def test_pfs0_rejects_path_traversal_duplicates_and_overlaps(self):
        self.need(read_pfs0)
        overlap = bytearray(pfs0([('main', b'ab'), ('sdk', b'cd')]))
        struct.pack_into('<Q', overlap, 40, 1)
        for data in [pfs0([('../escape', b'a')]), pfs0([('main', b'a'), ('main', b'b')]), bytes(overlap)]:
            with self.subTest(data=data[:16]), self.assertRaises(ValueError):
                read_pfs0(io.BytesIO(data), 0, len(data))

    def test_pfs0_rejects_truncation_and_oversized_header(self):
        self.need(read_pfs0)
        data = pfs0([('main', b'a')])
        for bad in [data[:-1], b'PFS0' + struct.pack('<III', 0xffffffff, 0, 0)]:
            with self.assertRaises(ValueError):
                read_pfs0(io.BytesIO(bad), 0, len(bad))

    @unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Install requirements.botw.txt for NCZ tests')
    def test_extracts_only_exefs_and_preserves_original(self):
        self.need(extract_exefs)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'game.nsz'
            original = nsz(pfs0([('main', nso()), ('sdk', nso()), ('main.npdm', b'METAdata')]))
            p.write_bytes(original)
            report = extract_exefs(p, Path(tmp) / 'out')
            self.assertEqual((Path(tmp) / 'out/main').read_bytes(), nso())
            self.assertEqual(p.read_bytes(), original)
            self.assertEqual(len(report['files']), 3)
            with self.assertRaises(FileExistsError):
                extract_exefs(p, Path(tmp) / 'out')

    @unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Install requirements.botw.txt for NCZ tests')
    def test_truncated_ncz_does_not_publish_partial_exefs(self):
        self.need(extract_exefs)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'game.nsz'
            content = pfs0([('main', nso())])
            p.write_bytes(nsz(content, len(content) + 100))
            out = Path(tmp) / 'out'
            with self.assertRaises(ValueError):
                extract_exefs(p, out)
            self.assertFalse(out.exists())

    @unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Install requirements.botw.txt for NCZ tests')
    def test_ncz_accepts_a_bounded_large_zstandard_window(self):
        self.need(extract_exefs)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'game.nsz'
            p.write_bytes(nsz(pfs0([('main', nso())]), window_descriptor=0x88))
            extract_exefs(p, Path(tmp) / 'out')
            self.assertEqual((Path(tmp) / 'out/main').read_bytes(), nso())

    @unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Install requirements.botw.txt for NCZ tests')
    def test_ncz_rejects_aggregate_decompression_bomb_before_reading(self):
        import zstandard
        self.need(extract_exefs)
        with tempfile.TemporaryDirectory() as tmp:
            length = 40 * 1024**3
            sections = b''.join(struct.pack('<QQQQ16s16s', 0x4000 + i * length, length, 3, 0, bytes(16), bytes(16))
                                for i in range(2))
            ncz = bytes(0x4000) + b'NCZSECTN' + struct.pack('<Q', 2) + sections
            ncz += zstandard.ZstdCompressor().compress(b'x')
            p = Path(tmp) / 'bomb.nsz'
            p.write_bytes(pfs0([('program.ncz', ncz)]))
            with self.assertRaisesRegex(ValueError, 'expansion limit'):
                extract_exefs(p, Path(tmp) / 'out')

    def test_nso_validates_hashes_and_reads_imports_exports(self):
        self.need(inspect_nso)
        report = inspect_nso(nso(), 'main')
        self.assertEqual(report['build_id'], '12' * 32)
        self.assertEqual(report['imports'], ['nvnBootstrapLoader'])
        self.assertEqual(report['exports'], ['local_function'])
        self.assertEqual(report['memory_bytes'], 0x580)

    def test_nso_rejects_hash_corruption_and_overlapping_segments(self):
        self.need(inspect_nso)
        corrupt = bytearray(nso())
        corrupt[0x180] ^= 1
        overlap = bytearray(nso())
        struct.pack_into('<I', overlap, 0x24, 0)
        for data in [bytes(corrupt), bytes(overlap), nso()[:100]]:
            with self.assertRaises(ValueError):
                inspect_nso(data, 'main')

    def test_symbol_tables_cannot_point_into_bss(self):
        self.need(inspect_nso)
        data = bytearray(nso())
        struct.pack_into('<Q', data, 0x218, 0x560)
        struct.pack_into('<I', data, 0xc, 0)  # This fixture tests bounds, not hashes.
        with self.assertRaises(ValueError):
            inspect_nso(data, 'main')

    def test_profile_rejects_same_name_different_code(self):
        self.need(verify_profile)
        profile = {'modules': {'main': {'sha256': 'a' * 64, 'build_id': 'b' * 64}}}
        with self.assertRaisesRegex(ValueError, 'main'):
            verify_profile([{'name': 'main', 'sha256': 'c' * 64, 'build_id': 'b' * 64}], profile)

    def test_generic_rewrite_rejects_game_specific_patches(self):
        self.need(check_generic_outputs)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for ext in ('cuts', 'obs', 'params'):
                (root / f'main.{ext}.tsv').write_text('# BUILD_ID\t1234\n')
            self.assertEqual(check_generic_outputs(root, 'main'), {'cuts': 0, 'obs': 0, 'params': 0})
            (root / 'main.cuts.tsv').write_text('0x100\told\tnew\n')
            with self.assertRaisesRegex(ValueError, 'cuts'):
                check_generic_outputs(root, 'main')

    def test_unverified_upstream_script_is_never_returned(self):
        self.need(verified_tool)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'patch_nso.py'
            p.write_text('raise RuntimeError("do not execute")')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                verified_tool(p)

    def test_structural_gate_rejects_branch_outside_patch_area(self):
        self.need(validate_structure)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'main.fixups.tsv').write_text('# PATCH_OFF\t0x1000\n# TEXT_END\t0x80\n')
            (p / 'main.sites.tsv').write_text('0x20\tx18\n')
            (p / 'main.gates.tsv').write_text('0x1008\tsvc\t1\t0x20\n')
            image = bytearray(0x1020)
            struct.pack_into('<I', image, 0x20, 0x14000000 | ((0x1000 - 0x20) // 4))
            (p / 'main.patched.bin').write_bytes(image)
            self.assertEqual(validate_structure(p, 'main')['branch_sites_checked'], 1)
            struct.pack_into('<I', image, 0x20, 0x14000001)
            (p / 'main.patched.bin').write_bytes(image)
            with self.assertRaisesRegex(ValueError, 'branch'):
                validate_structure(p, 'main')

    def test_cli_reports_audit_without_claiming_phone_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exefs = root / 'ExeFS'
            exefs.mkdir()
            data = nso()
            (exefs / 'main').write_bytes(data)
            profile = root / 'profile.json'
            profile.write_text(json.dumps({'modules': {'main': {
                'sha256': hashlib.sha256(data).hexdigest(), 'build_id': '12' * 32}}}))
            out = root / 'output'
            result = subprocess.run([sys.executable, '-m', 'botw_native', '--exefs', str(exefs),
                                     '--profile', str(profile), '--output', str(out)],
                                    env=dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'scripts')),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((out / 'report.json').read_text())
            self.assertFalse(report['phone_ready'])
            self.assertFalse(report['guest_execution_tested'])
            self.assertEqual(report['status'], 'audited')
            self.assertIn('nvnBootstrapLoader', report['unresolved_imports'])

    @unittest.skipUnless(importlib.util.find_spec('capstone'), 'Install requirements.botw.txt for instruction tests')
    def test_signed_jump_table_range_is_proven_from_guard_and_readonly_data(self):
        self.need(signed_jump_range)
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        words = [0x2a0003f2, 0x71000a5f, 0x540001c8, 0xb0000008, 0x91040108,
                 0xb8b27912, 0x8b080252] + [0xd503201f] * 8 + [0xd61f0240, 0xd65f03c0]
        code = struct.pack('<17I', *words)
        image = bytearray(0x2000)
        image[:len(code)] = code
        for i, destination in enumerate((0x100, 0x180, 0x200)):
            struct.pack_into('<i', image, 0x1100 + i * 4, destination - 0x1100)
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        md.detail = True
        instructions = list(md.disasm(code, 0))[:-1]
        self.assertEqual(signed_jump_range(instructions, image, 0x400, 0x1000, 0x2000), (0x100, 65))
        incoming_word = 0x14000000 | (((8 - len(code)) // 4) & 0x3ffffff)
        incoming = list(md.disasm(code + struct.pack('<I', incoming_word), 0))
        self.assertIsNone(signed_jump_range(instructions, image, 0x400, 0x1000, 0x2000,
                                           all_instructions=incoming))
        self.assertIsNone(signed_jump_range(instructions[2:], image, 0x400, 0x1000, 0x2000))
        alternate = words[:]
        alternate[5] = 0xb8b2790d  # ldrsw x13, [x8, x18, lsl #2]
        alternate[6] = 0x8b0801b2  # add x18, x13, x8
        alternate.insert(2, 0xd503201f)  # Non-flag-setting work between cmp and b.hi.
        alt_code = struct.pack('<' + 'I' * len(alternate), *alternate)
        self.assertEqual(signed_jump_range(list(md.disasm(alt_code, 0))[:-1], image, 0x400, 0x1000, 0x2000), (0x100, 65))
        struct.pack_into('<i', image, 0x1100, 0x8000)
        self.assertIsNone(signed_jump_range(instructions, image, 0x400, 0x1000, 0x2000))

    @unittest.skipUnless(os.environ.get('BOTW_REWRITER_PATH') and importlib.util.find_spec('unicorn'),
                         'Set BOTW_REWRITER_PATH to the verified tool for CPU equivalence integration')
    def test_new_jump_forms_preserve_cpu_state_in_48_synthetic_cases(self):
        from botw_native.rewrite import rewrite_module
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn import arm64_const as regs
        import random

        register_ids = [getattr(regs, f'UC_ARM64_REG_X{i}') for i in range(31)]
        base, tls, stack = 0x100000, 0x400000, 0x800000
        cases_checked = 0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for alternate in (False, True):
                raw = bytearray(nso())
                struct.pack_into('<I', raw, 0xc, 0)
                words = [0x2a0003f2, 0x71000a5f, 0x540001c8, 0x90000008, 0x91080108,
                         0xb8b27912, 0x8b080252] + [0xd503201f] * 8 + [0xd61f0240, 0xd65f03c0]
                if alternate:
                    words[5], words[6] = 0xb8b2790d, 0x8b0801b2
                    words.insert(2, 0xd503201f)
                code = struct.pack('<' + 'I' * len(words), *words)
                raw[0x110:0x110 + len(code)] = code
                default = 0x10 + len(code) - 4
                for i, target in enumerate((0x60, 0x68, 0x70)):
                    struct.pack_into('<i', raw, 0x300 + i * 4, target - 0x200)
                struct.pack_into('<i', raw, 0x190, 0x2e0 - 0x80)
                raw[0x3e0:0x3f4] = struct.pack('<4B4I', 1, 3, 3, 3, 0, 1, 0x10, 0)
                module = root / 'main'
                module.write_bytes(raw)
                output = root / ('alternate' if alternate else 'direct')
                result = rewrite_module(Path(os.environ['BOTW_REWRITER_PATH']), module, output)
                self.assertEqual(result['status'], 'rewritten', (output / 'main.rewrite.log').read_text())
                original = bytearray(0x500)
                for i in range(3):
                    offset, address, size = struct.unpack_from('<III', raw, 0x10 + i * 16)
                    original[address:address + size] = raw[offset:offset + size]
                patched = (output / 'main.patched.bin').read_bytes()
                key_page = next(int(row.split('\t')[1], 0) for row in
                                (output / 'main.fixups.tsv').read_text().splitlines()
                                if row.startswith('# KEY_PAGE_OFF\t'))

                def emulate(image, initial, native):
                    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                    cpu.mem_map(base, (len(image) + 0xffff) & ~0xffff)
                    cpu.mem_write(base, bytes(image))
                    cpu.mem_map(tls, 0x1000)
                    cpu.mem_map(stack, 0x2000)
                    for reg, value in zip(register_ids, initial):
                        cpu.reg_write(reg, value)
                    cpu.reg_write(regs.UC_ARM64_REG_SP, stack + 0x1000)
                    cpu.reg_write(regs.UC_ARM64_REG_NZCV, 0xa0000000)
                    cpu.reg_write(regs.UC_ARM64_REG_TPIDRRO_EL0, tls)
                    if native:
                        cpu.mem_write(base + key_page, struct.pack('<8Q', *range(0x100, 0x140, 8)))
                        cpu.mem_write(tls + 0x100, struct.pack('<Q', initial[18]))
                    stops = {base + t for t in (default, 0x60, 0x68, 0x70)}
                    cpu.hook_add(UC_HOOK_CODE, lambda uc, address, size, _: uc.emu_stop() if address in stops else None)
                    cpu.emu_start(base + 0x10, base + len(image), count=3000)
                    self.assertIn(cpu.reg_read(regs.UC_ARM64_REG_PC), stops)
                    state = [cpu.reg_read(reg) for reg in register_ids]
                    if native:
                        state[18] = struct.unpack('<Q', cpu.mem_read(tls + 0x100, 8))[0]
                    return state + [cpu.reg_read(reg) for reg in
                                    (regs.UC_ARM64_REG_SP, regs.UC_ARM64_REG_NZCV, regs.UC_ARM64_REG_PC)]

                for case in (0, 1, 2, 3, 0xffffffff, 0xffff000000000002):
                    for seed in range(4):
                        rng = random.Random(seed)
                        initial = [rng.getrandbits(64) for _ in register_ids]
                        initial[0] = case
                        self.assertEqual(emulate(original, initial, False), emulate(patched, initial, True))
                        cases_checked += 1
        self.assertEqual(cases_checked, 48)


if __name__ == '__main__':
    unittest.main()
