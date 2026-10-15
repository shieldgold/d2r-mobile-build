"""Real native loader boundary tests using tiny synthetic NSO memory images."""
import platform
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')


def image(root, name, *, bad_type=False, text_reloc=False, invoke_missing=False,
          entropy_service=False, query_stack=False, priority_service=False,
          query_zero=False, corrupt_stack=False, symbol_interposition=False,
          protected_override=False, signal_condition=False, invalid_condition=False,
          constructors=False, failing_constructor=False, check_condition_clear=False,
          entropy_check=None, query_address=None):
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
    if name == 'sdk' and (query_stack or query_zero or signal_condition or query_address is not None):
        literal = 0x58000000 | (((0x8208 - 0x104) // 4) << 5)
        branch = 0x14000000 | ((0x10000 - 0x108) // 4)
        first = 0x92800001 if signal_condition else 0xaa1f03e2 if query_zero else 0x910003e2
        if query_address is not None:
            struct.pack_into('<Q', raw, 0x8230, query_address)
            first = 0x58000002 | (((0x8230 - 0x100) // 4) << 5)
        struct.pack_into('<IIII', raw, 0x100, first, literal, branch, 0xd65f03c0)
        imm = (-4) & ((1 << 21) - 1)
        adrp = 0x9000001d | ((imm & 3) << 29) | ((imm >> 2) << 5)
        back = 0x14000000 | (((0x10c - 0x10024) // 4) & 0x3ffffff)
        gate = (0xa9bf7bfd, 0xd53bd07e, adrp, 0xf9400fbd, 0x8b1d03de,
                0xf94003de, 0xf94003fd, 0xd63f03c0, 0xa8c17bfd, back)
        struct.pack_into('<' + 'I' * len(gate), raw, 0x10000, *gate)
    if name == 'sdk' and (entropy_service or entropy_check):
        # Run several calls through one genuine TSD host gate, preserving the
        # caller's callee-saved registers and LR across the generated probe.
        words = [0xa9bf7bfd, 0xa9bf53f3]
        def call_entropy(index, handle=0):
            words.extend((0xd2800161, 0xd2800002 | (handle << 5), 0xd2800003 | (index << 5)))
            pc = 0x400 + len(words) * 4
            words.append(0x94000000 | (((0x10000 - pc) // 4) & 0x3ffffff))
        def require_equal(register):
            words.extend((0xeb00003f | (register << 16), 0x54000040, 0xd4200000))
        if entropy_check == 'invalid_handle':
            call_entropy(0, 1)
        elif entropy_check == 'invalid_index':
            call_entropy(4)
        else:
            call_entropy(0)
            words.append(0xaa0103f3)  # mov x19,x1
            call_entropy(0)
            require_equal(19)
            if entropy_check == 'slots':
                call_entropy(1)
                words.append(0xaa0103f4)  # mov x20,x1
                words.extend((0xeb13003f, 0x54000041, 0xd4200000))  # slot 1 differs
                call_entropy(0)
                require_equal(19)
                call_entropy(1)
                require_equal(20)
        words.extend((0xa8c153f3, 0xa8c17bfd, 0xd65f03c0))
        struct.pack_into('<I', raw, 0x100, 0x140000c0)
        struct.pack_into('<' + 'I' * len(words), raw, 0x400, *words)
        imm = (-4) & ((1 << 21) - 1)
        adrp = 0x9000001d | ((imm & 3) << 29) | ((imm >> 2) << 5)
        gate = (0xa9bf7bfd, 0xd53bd07e, adrp, 0xf9400fbd, 0x8b1d03de,
                0xf94003de, 0xf94003fd, 0xd63f03c0, 0xa8c17bfd, 0xd65f03c0)
        struct.pack_into('<' + 'I' * len(gate), raw, 0x10000, *gate)
    if name == 'sdk' and check_condition_clear:
        struct.pack_into('<I', raw, 0x8220, 1)
        literal = 0x58000002 | (((0x8208 - 0x110) // 4) << 5)
        struct.pack_into('<IIII', raw, 0x110, literal, 0xb9400042, 0x34000042, 0xd4200000)
    if name == 'sdk' and query_address is not None:
        # The following SDK entry reads the QueryMemory record written by the
        # guest SVC. Validate state and the interval endpoint without ASLR guesses.
        upper = 1 << 36
        expected_type = 0x10 if query_address >= upper else 0
        expected_end = 0 if query_address >= upper else upper
        struct.pack_into('<QQQ', raw, 0x8280, expected_type, expected_end, upper)
        words = []
        def load_expected(register, offset):
            pc = 0x600 + len(words) * 4
            words.append(0x58000000 | register | (((offset - pc) // 4) << 5))
        load_expected(2, 0x8208)
        words.append(0xf9400843)  # ldr x3,[x2,#16]: state+zero attributes
        load_expected(4, 0x8280)
        words.extend((0xeb04007f, 0x54000040, 0xd4200000))
        words.extend((0xf9400043, 0xf9400444, 0x8b040063))
        load_expected(4, 0x8288)
        words.extend((0xeb04007f, 0x54000040, 0xd4200000))
        if query_address >= upper:
            words.append(0xf9400043)
            load_expected(4, 0x8290)
            words.extend((0xeb04007f, 0x54000040, 0xd4200000))
        words.append(0xd65f03c0)
        struct.pack_into('<I', raw, 0x110, 0x1400013c)
        struct.pack_into('<' + 'I' * len(words), raw, 0x600, *words)
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
    if query_stack or query_zero or signal_condition or query_address is not None:
        relocs.append((0x8208, 1027, 0x100 if invalid_condition else 0x8220))
    if invoke_missing:
        relocs.append((0x8200, ((len(symbols) - 2) << 32) | 1026, 0))
    rel = b''.join(struct.pack('<QQq', *r) for r in relocs)
    raw[0x8b00:0x8b00 + len(rel)] = rel
    tags = [(5, 0x8800), (6, 0x8400), (10, len(strings)), (11, 24), (4, 0x8a00),
            (7, 0x8b00), (8, len(rel)), (0, 0)]
    if constructors:
        tags.insert(-1, (12, 0x300))
        struct.pack_into('<I', raw, 0x300, 0xd4200000 if name == 'sdk' and failing_constructor else 0xd65f03c0)
    for i, pair in enumerate(tags):
        struct.pack_into('<QQ', raw, 0x8000 + i * 16, *pair)
    (out / f'{name}.patched.bin').write_bytes(raw)
    hdr = {'PATCH_OFF': '0x10000', 'KEY_PAGE_OFF': '0xc000', 'KEY_PAGE_SIZE': '0x4000',
           'TEXT_END': '0x1000', 'RO_OFF': '0x1000', 'DATA_OFF': '0x8000'}
    hdr.update({f'KEY_{n}_IDX': str(i) for i, n in enumerate(
        ('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI'))})
    (out / f'{name}.fixups.tsv').write_text(''.join(f'# {k}\t{v}\n' for k, v in hdr.items()))
    (out / f'{name}.gates.tsv').write_text('0x10020\tsvc\t12\t0x104\n' if name == 'sdk' and priority_service else '0x10020\tsvc\t41\t0x104\n' if name == 'sdk' and (entropy_service or entropy_check) else '0x10020\tsvc\t29\t0x108\n' if name == 'sdk' and signal_condition else '0x10020\tsvc\t6\t0x108\n' if name == 'sdk' and (query_stack or query_zero or query_address is not None) else '')


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
        sources = [ROOT / 'native/botw_host' / f for f in ('runtime.c', 'entry.S', 'cli.c', 'services.c', 'kernel.c')]
        sources += [ROOT / 'native/botw_input/input.c', ROOT / 'native/botw_resources/romfs.c']
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
                image(root, name, **(options if name == 'sdk' or options.get('symbol_interposition') or options.get('constructors') else {}))
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

    def test_condition_broadcast_with_no_registered_waiters_wakes_zero(self):
        p = self.run_host('--sdk-probe', signal_condition=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('condition_signal requested=-1 waiters=0 woken=0', p.stdout)
        self.assertIn('sdk_probe_completed=true', p.stdout)

    def test_condition_signal_clears_guest_has_waiter_flag(self):
        p = self.run_host('--sdk-probe', signal_condition=True, check_condition_clear=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('sdk_probe_completed=true', p.stdout)

    def test_entropy_slots_are_stable_and_selected_by_subtype(self):
        p = self.run_host('--sdk-probe', entropy_service=True, entropy_check='slots')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('sdk_probe_completed=true', p.stdout)

    def test_entropy_rejects_nonzero_handle(self):
        p = self.run_host('--sdk-probe', entropy_service=True, entropy_check='invalid_handle')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('RandomEntropy requires handle=0 and subtype<4', p.stdout)

    def test_entropy_rejects_invalid_subtype(self):
        p = self.run_host('--sdk-probe', entropy_service=True, entropy_check='invalid_index')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('RandomEntropy requires handle=0 and subtype<4', p.stdout)

    def test_query_memory_36_bit_boundary_is_inaccessible(self):
        p = self.run_host('--sdk-probe', query_address=1 << 36)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('query_memory=inaccessible', p.stdout)

    def test_query_memory_last_36_bit_address_free_interval_ends_at_boundary(self):
        p = self.run_host('--sdk-probe', query_address=(1 << 36) - 1)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('query_memory=free', p.stdout)

    def test_partial_condition_signal_preserves_flag_until_last_waiter(self):
        # Exercise the production registry with two registered condition nodes.
        # Guest WaitProcessWideKeyAtomic remains deliberately unsupported.
        source = Path(self.build.name) / 'condition-registry.c'
        executable = Path(self.build.name) / 'condition-registry'
        source.write_text('#include "' + str(ROOT / 'native/botw_host/runtime.c') + '"\n' + r'''
int main(void) {
    uint32_t flag = 1;
    ConditionWaiter first = {.key = (uint64_t)&flag};
    ConditionWaiter second = {.key = (uint64_t)&flag};
    pthread_cond_init(&first.condition, NULL);
    pthread_cond_init(&second.condition, NULL);
    first.next = &second;
    state.condition_waiters = &first;
    state.gates[0] = (Gate){.ret = 0x100, .kind = 0, .arg = 0x1d};
    state.gate_count = 1;
    if (sigsetjmp(state.recovery, 1)) return 15;
    uint64_t regs[31] = {(uint64_t)&flag, 1};
    botw_gate_dispatch(0x100, regs);
    if (flag != 1 || !first.signalled || second.signalled) return 11;
    regs[0] = (uint64_t)&flag; regs[1] = (uint64_t)-1;
    botw_gate_dispatch(0x100, regs);
    if (flag != 0 || !second.signalled) return 12;
    pthread_cond_destroy(&first.condition);
    pthread_cond_destroy(&second.condition);
    return 0;
}
''')
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                str(source), str(ROOT / 'native/botw_host/entry.S'),
                                str(ROOT / 'native/botw_host/services.c'), str(ROOT / 'native/botw_host/kernel.c'),
                                str(ROOT / 'native/botw_input/input.c'), str(ROOT / 'native/botw_resources/romfs.c'), '-o', str(executable)],
                               capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_condition_key_cannot_point_to_signed_code(self):
        p = self.run_host('--sdk-probe', signal_condition=True, invalid_condition=True)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('condition key is not aligned guest writable memory', p.stdout)

    def test_boot_game_runs_reverse_module_constructors_before_nnmain(self):
        p = self.run_host('--boot-game', constructors=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        markers = [f'constructor_returned={name}' for name in reversed(MODULES)]
        positions = [p.stdout.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(positions[-1], p.stdout.index('entry_called=nnMain'))
        self.assertIn('nnMain_returned=true', p.stdout)
        self.assertIn('phone_ready=false', p.stdout)

    def test_failed_constructor_prevents_nnmain(self):
        p = self.run_host('--boot-game', constructors=True, failing_constructor=True)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('constructor_called=sdk', p.stdout)
        self.assertNotIn('entry_called=nnMain', p.stdout)

    def test_unimplemented_strong_call_stops_instead_of_returning_success(self):
        p = self.run_host('--sdk-probe', invoke_missing=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('unsupported import: unimplemented_real_api', p.stdout)
        self.assertNotIn('sdk_probe_completed=true', p.stdout)


if __name__ == '__main__':
    unittest.main()
