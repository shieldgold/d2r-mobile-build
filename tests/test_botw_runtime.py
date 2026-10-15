"""Real native loader boundary tests using tiny synthetic NSO memory images."""
import os
import platform
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')


def image(root, name, *, bad_type=False, text_reloc=False, invoke_missing=False, entropy_service=False, query_stack=False, priority_service=False, query_zero=False, corrupt_stack=False, symbol_interposition=False, protected_override=False):
    out = root / 'botw'
    out.mkdir(exist_ok=True)
    raw = bytearray(0x14000)
    struct.pack_into('<I', raw, 4, 0x20)
    raw[0x20:0x24] = b'MOD0'
    struct.pack_into('<i', raw, 0x24, 0x8000 - 0x20)
    names = ['']
    symbols = [(0, 0, 0, 0, 0, 0)]
    if name == 'sdk':
        for i, symbol in enumerate(('_init_libc0', 'nnosInitialize', '_init_libc1',
                                    'nndiagStartup', 'nninitInitializeSdkModule',
                                    'nninitStartup', '_init_libc2', 'nnMain')):
            names.append(symbol)
            symbols.append((sum(len(s) + 1 for s in names[:-1]), 0x12, 0, 1, 0x100 + i * 16, 4))
            struct.pack_into('<I', raw, 0x100 + i * 16, 0xd65f03c0)  # ret
        if invoke_missing:
            names.append('unimplemented_real_api')
            symbols.append((sum(len(s) + 1 for s in names[:-1]), 0x12, 0, 0, 0, 0))
            delta = (0x8200 - 0x100) // 4
            struct.pack_into('<II', raw, 0x100, 0x58000010 | (delta << 5), 0xd61f0200)
    if name == 'sdk' and corrupt_stack:
        struct.pack_into('<III', raw, 0x100, 0xd2800000, 0x9100001f, 0xd61f0000)
    if name == 'sdk' and (entropy_service or priority_service):
        # A genuine rewritten SVC gate obtains the host entry from the TSD key page.
        struct.pack_into('<IIIII', raw, 0x100, 0xd2820021 if priority_service else 0xd2800161, 0x14003fbf,
                         0xb5000041, 0xd4200000, 0xd65f03c0)
        imm = (-4) & ((1 << 21) - 1)
        adrp = 0x9000001d | ((imm & 3) << 29) | ((imm >> 2) << 5)
        gate = (0xa9bf7bfd, 0xd53bd07e, adrp, 0xf9400fbd, 0x8b1d03de,
                0xf94003de, 0xf94003fd, 0xd63f03c0, 0xa8c17bfd, 0x17ffc039)
        struct.pack_into('<' + 'I' * len(gate), raw, 0x10000, *gate)
    if name == 'sdk' and (query_stack or query_zero):
        literal = 0x58000000 | (((0x8208 - 0x104) // 4) << 5)
        branch = 0x14000000 | ((0x10000 - 0x108) // 4)
        struct.pack_into('<IIII', raw, 0x100, 0xaa1f03e2 if query_zero else 0x910003e2, literal, branch, 0xd65f03c0)
        imm = (-4) & ((1 << 21) - 1)
        adrp = 0x9000001d | ((imm & 3) << 29) | ((imm >> 2) << 5)
        back = 0x14000000 | (((0x10c - 0x10024) // 4) & 0x3ffffff)
        gate = (0xa9bf7bfd, 0xd53bd07e, adrp, 0xf9400fbd, 0x8b1d03de,
                0xf94003de, 0xf94003fd, 0xd63f03c0, 0xa8c17bfd, back)
        struct.pack_into('<' + 'I' * len(gate), raw, 0x10000, *gate)
    override_index = None
    if symbol_interposition and name in ('main', 'sdk'):
        names.append('application_override')
        override_index = len(symbols)
        symbols.append((sum(len(s) + 1 for s in names[:-1]), 0x22 if name == 'sdk' else 0x12,
                        3 if name == 'sdk' and protected_override else 0, 1, 0x200, 4))
        struct.pack_into('<I', raw, 0x200, 0xd4200000 if name == 'sdk' else 0xd65f03c0)
        if name == 'sdk':
            delta = (0x8200 - 0x100) // 4
            struct.pack_into('<II', raw, 0x100, 0x58000010 | (delta << 5), 0xd61f0200)
    names.append('optional_weak')
    symbols.append((sum(len(s) + 1 for s in names[:-1]), 0x22, 0, 0, 0, 0))
    strings = ('\0'.join(names) + '\0').encode()
    symtab = b''.join(struct.pack('<IBBHQQ', *s) for s in symbols)
    raw[0x8400:0x8400 + len(symtab)] = symtab
    raw[0x8800:0x8800 + len(strings)] = strings
    struct.pack_into('<II', raw, 0x8a00, 1, len(symbols))
    relocs = [(0x100 if text_reloc else 0x8210, 999 if bad_type else 1027, 0x120),
              (0x8218, ((len(symbols) - 1) << 32) | 1025, 0)]
    if symbol_interposition and name == 'sdk':
        relocs.append((0x8200, (override_index << 32) | 1025, 0))
    if query_stack or query_zero:
        relocs.append((0x8208, 1027, 0x8220))
    if invoke_missing:
        relocs.append((0x8200, ((len(symbols) - 2) << 32) | 1026, 0))
    rel = b''.join(struct.pack('<QQq', *r) for r in relocs)
    raw[0x8b00:0x8b00 + len(rel)] = rel
    tags = [(5, 0x8800), (6, 0x8400), (10, len(strings)), (11, 24), (4, 0x8a00),
            (7, 0x8b00), (8, len(rel)), (0, 0)]
    for i, pair in enumerate(tags):
        struct.pack_into('<QQ', raw, 0x8000 + i * 16, *pair)
    (out / f'{name}.patched.bin').write_bytes(raw)
    hdr = {'PATCH_OFF': '0x10000', 'KEY_PAGE_OFF': '0xc000', 'KEY_PAGE_SIZE': '0x4000',
           'TEXT_END': '0x1000', 'RO_OFF': '0x1000', 'DATA_OFF': '0x8000'}
    hdr.update({f'KEY_{n}_IDX': str(i) for i, n in enumerate(
        ('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI'))})
    (out / f'{name}.fixups.tsv').write_text(''.join(f'# {k}\t{v}\n' for k, v in hdr.items()))
    (out / f'{name}.gates.tsv').write_text('0x10020\tsvc\t12\t0x104\n' if name == 'sdk' and priority_service else '0x10020\tsvc\t41\t0x104\n' if name == 'sdk' and entropy_service else '0x10020\tsvc\t6\t0x108\n' if name == 'sdk' and (query_stack or query_zero) else '')


class RuntimeSourceTests(unittest.TestCase):
    def test_native_runtime_sources_exist(self):
        for name in ('runtime.h', 'runtime.c', 'entry.S', 'cli.c'):
            self.assertTrue((ROOT / 'native/botw_host' / name).is_file(), name)


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native runtime tests require Apple ARM64')
class NativeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-runtime-tests-')
        cls.exe = Path(cls.build.name) / 'botw-host'
        sources = [ROOT / 'native/botw_host' / f for f in ('runtime.c', 'entry.S', 'cli.c')]
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                '-O1', '-g', *(str(s) for s in sources), '-o', str(cls.exe)],
                               text=True, capture_output=True)
        assert built.returncode == 0, built.stderr

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_host(self, *flags, **options):
        with tempfile.TemporaryDirectory(prefix='botw-synthetic-') as d:
            root = Path(d)
            for name in MODULES:
                image(root, name, **(options if name == 'sdk' or options.get('symbol_interposition') else {}))
            return subprocess.run([str(self.exe), str(root), *flags], capture_output=True,
                                  text=True, timeout=10)

    def test_maps_and_relocates_six_modules_and_resolves_sdk(self):
        p = self.run_host('--load-only')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('modules_loaded=6', p.stdout)
        self.assertIn('weak_undefined_zero=', p.stdout)
        self.assertIn('entry_resolved=nnMain', p.stdout)
        self.assertIn('guest_execution_tested=false', p.stdout)

    def test_unknown_relocation_is_rejected(self):
        p = self.run_host('--load-only', bad_type=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('unsupported relocation type 999', p.stdout)

    def test_relocation_cannot_write_signed_text(self):
        p = self.run_host('--load-only', text_reloc=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('relocation target outside writable data', p.stdout)

    def test_sdk_probe_executes_code_in_order_without_claiming_game(self):
        p = self.run_host('--sdk-probe')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertLess(p.stdout.index('entry_called=_init_libc0'), p.stdout.index('entry_called=nnosInitialize'))
        self.assertIn('sdk_probe_completed=true', p.stdout)
        self.assertIn('nnMain_called=false', p.stdout)

    def test_random_entropy_service_has_a_real_nonzero_result(self):
        p = self.run_host('--sdk-probe', entropy_service=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('sdk_probe_completed=true', p.stdout)

    def test_query_memory_reports_the_actual_current_stack(self):
        p = self.run_host('--sdk-probe', query_stack=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('query_memory=stack', p.stdout)
        self.assertIn('sdk_probe_completed=true', p.stdout)

    def test_main_thread_priority_matches_botw_npdm(self):
        p = self.run_host('--sdk-probe', priority_service=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('main_thread_priority=44', p.stdout)

    def test_query_memory_reports_free_gap_from_address_zero(self):
        p = self.run_host('--sdk-probe', query_zero=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('query_memory=free', p.stdout)

    def test_guest_corrupt_stack_fault_is_recovered_on_signal_stack(self):
        p = self.run_host('--sdk-probe', corrupt_stack=True)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('error=guest CPU fault signal=', p.stdout)
        self.assertIn('host_stage=stopped', p.stdout)
        self.assertNotIn('restore alternate signal stack failed', p.stdout)

    def test_sdk_weak_definition_uses_main_application_override(self):
        p = self.run_host('--sdk-probe', symbol_interposition=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('sdk_probe_completed=true', p.stdout)

    def test_protected_definition_stays_local(self):
        p = self.run_host('--sdk-probe', symbol_interposition=True, protected_override=True)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('error=guest CPU fault signal=', p.stdout)

    def test_unimplemented_strong_call_stops_instead_of_returning_success(self):
        p = self.run_host('--sdk-probe', invoke_missing=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('unsupported import: unimplemented_real_api', p.stdout)
        self.assertNotIn('sdk_probe_completed=true', p.stdout)


if __name__ == '__main__':
    unittest.main()
