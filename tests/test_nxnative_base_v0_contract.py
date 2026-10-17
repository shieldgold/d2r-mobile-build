"""Owned static metadata and Mach-O fixtures; no Host/Guest execution."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
TOOL = COMPONENT / 'tools/generate_base_v0_contract.py'


def owned_image():
    data = bytearray(0x400)
    struct.pack_into('<I', data, 4, 0x40)
    data[0x40:0x44] = b'MOD0'
    struct.pack_into('<i', data, 0x44, 0x40)
    strings = b'\0nnMain\0owned_force\0'
    tags = ((6, 0x200), (5, 0x248), (10, len(strings)), (11, 24),
            (7, 0x300), (8, 48), (23, 0x330), (2, 24), (0, 0))
    for index, pair in enumerate(tags): struct.pack_into('<QQ', data, 0x80 + index * 16, *pair)
    data[0x248:0x248 + len(strings)] = strings
    struct.pack_into('<IBBHQQ', data, 0x218, 1, 0x12, 0, 1, 0x20, 4)
    struct.pack_into('<IBBHQQ', data, 0x230, 8, 0x12, 0, 0, 0, 0)
    struct.pack_into('<QQq', data, 0x300, 0x380, 1027, 4)
    struct.pack_into('<QQq', data, 0x318, 0x388, (2 << 32) | 1026, 0)
    struct.pack_into('<QQq', data, 0x330, 0x390, (2 << 32) | 1025, 0)
    return data


def owned_registry_fixture():
    # Reuse the complete existing owned chained-fixup fixture, changing only
    # the one declared registry section's length from 32 to 24 bytes.
    path = ROOT / 'tests/test_botw_whole_registry_audit.py'
    tree = ast.parse(path.read_bytes())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'macho_fixture')
    namespace = {'struct': struct}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    data = namespace['macho_fixture']()
    cursor = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, cursor)
        if command == 0x19:
            for index in range(struct.unpack_from('<I', data, cursor + 64)[0]):
                section = cursor + 72 + index * 80
                if data[section:section + 16].rstrip(b'\0') == b'swforce':
                    struct.pack_into('<Q', data, section + 40, 24)
        cursor += size
    return data


class BaseV0Contract(unittest.TestCase):
    def tool(self):
        self.assertTrue(TOOL.is_file(), 'dedicated base-v0 contract generator is absent')
        spec = importlib.util.spec_from_file_location('base_v0_contract_under_test', TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nxnative-owned-contract-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()

    def test_identity_header_has_exact_six_modules_52_32_and_original_literals(self):
        tool = self.tool()
        profile = json.loads((COMPONENT / 'profile/base_v0.json').read_text())
        header = tool.identity_header(profile)
        self.assertIn('"main", "subsdk0", "subsdk1", "subsdk2", "subsdk3", "sdk"', header)
        self.assertIn('#define DM_GAME_NMOD 6', header)
        self.assertIn('#define DM_GAME_NVN_API_MAJOR 52', header)
        self.assertIn('#define DM_GAME_NVN_API_MINOR 32', header)
        self.assertIn('0xc2600000', header)
        self.assertIn('0xc0600000', header)
        self.assertIn('"1.0.0"', header)
        self.assertNotIn('1.9.0', header)
        for unknown in ('DM_GAME_MEM_RESERVE', 'DM_GAME_NVN_SAMP_BASE', 'DM_GAME_NVN_SSBO_BASE'):
            self.assertNotIn('#define ' + unknown, header)

    def test_identity_header_rejects_renamed_19_or_changed_module_order(self):
        tool = self.tool()
        profile = json.loads((COMPONENT / 'profile/base_v0.json').read_text())
        for field, value in (('game_version', '1.9.0'), ('required_modules', ['main', 'subsdk0', 'sdk'])):
            changed = json.loads(json.dumps(profile)); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): tool.identity_header(changed)

    def test_complete_dynamic_symbols_and_both_relocation_tables_are_inspected(self):
        tool = self.tool()
        result = tool.inspect_image(bytes(owned_image()), 0x80, {'owned_force'})
        self.assertEqual(result['force_reference_counts'], {'owned_force': 2})
        self.assertEqual(result['defined_functions']['nnMain']['offset'], 0x20)
        self.assertEqual(result['symbol_count'], 3)
        self.assertEqual(result['relative_relocations'], 1)

    def test_repeated_needed_tags_are_valid_but_critical_symbol_tags_are_unique(self):
        tool = self.tool()
        data = owned_image()
        for offset, pair in ((0x100, (1, 0)), (0x110, (1, 0)), (0x120, (0, 0))):
            struct.pack_into('<QQ', data, offset, *pair)
        self.assertEqual(tool.inspect_image(bytes(data), 0x80, set())['symbol_count'], 3)
        struct.pack_into('<QQ', data, 0x110, 6, 0x200)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            tool.inspect_image(bytes(data), 0x80, set())

    def test_dynamic_geometry_string_and_export_entry_fail_closed(self):
        tool = self.tool()
        for mode in ('dynamic', 'symbol', 'entry', 'string', 'relocation'):
            data = owned_image()
            if mode == 'dynamic': struct.pack_into('<i', data, 0x44, 0x1000)
            if mode == 'symbol': struct.pack_into('<QQ', data, 0x90, 5, 0x249)
            if mode == 'entry': struct.pack_into('<Q', data, 0x220, 0x300)
            if mode == 'string': struct.pack_into('<I', data, 0x230, 999)
            if mode == 'relocation': struct.pack_into('<Q', data, 0x320, (99 << 32) | 1026)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                tool.inspect_image(bytes(data), 0x80, {'owned_force'})

    def test_entry_resolution_uses_full_module_order_and_rejects_missing_entry(self):
        tool = self.tool()
        first = tool.inspect_image(bytes(owned_image()), 0x80, {'owned_force'})
        contracts = {'main': first, 'sdk': first}
        result = tool.entry_contract(contracts, required=('nnMain',))
        self.assertEqual(result['nnMain']['module'], 'main')
        with self.assertRaisesRegex(ValueError, 'entry'):
            tool.entry_contract(contracts, required=('missing_entry',))

    def test_existing_complete_macho_reader_handles_actual_24_byte_contract(self):
        tool = self.tool()
        path = self.directory / 'owned-mach-o'
        path.write_bytes(owned_registry_fixture())
        result = tool.read_registry24(path)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['entries'], [{'name': 'owned_symbol', 'why': 'owned reason'}])
        self.assertEqual(result['chained_pointer_formats'], [6])
        self.assertNotIn('titles', result['entries'][0])

    def test_registry_external_bind_or_noncode_target_is_rejected(self):
        tool = self.tool()
        for mode in ('bind', 'noncode'):
            data = owned_registry_fixture()
            raw, = struct.unpack_from('<Q', data, 0x10008)
            if mode == 'bind': raw |= 1 << 63
            else: raw = (raw & ~((1 << 36) - 1)) | 0x10000
            struct.pack_into('<Q', data, 0x10008, raw)
            path = self.directory / mode
            path.write_bytes(data)
            with self.subTest(mode=mode), self.assertRaises(ValueError): tool.read_registry24(path)

    def test_registry_parser_consumes_only_private_bounded_snapshot(self):
        tool = self.tool()
        path = self.directory / 'owned-mach-o'; path.write_bytes(owned_registry_fixture())
        original = Path.read_bytes
        def reject_unbounded_input(target):
            if target == path: raise AssertionError('parser reopened original binary without bounds')
            return original(target)
        with mock.patch.object(Path, 'read_bytes', reject_unbounded_input):
            self.assertEqual(tool.read_registry24(path)['count'], 1)

    def test_force_manifest_preserves_every_registry_entry_and_exact_counts(self):
        tool = self.tool()
        entries = [{'name': 'owned_force', 'why': 'owned'}, {'name': 'unused', 'why': 'kept'}]
        contracts = {'main': tool.inspect_image(bytes(owned_image()), 0x80, {'owned_force', 'unused'})}
        rules = tool.force_contract(entries, contracts)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]['references'], {'main': 2})
        self.assertIs(rules[0]['selected'], True)
        self.assertIs(rules[1]['selected'], False)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            tool.force_contract(entries + [entries[0]], contracts)

    def test_safe_reader_rejects_fifo_symlink_and_oversize(self):
        tool = self.tool()
        path = self.directory / 'owned'
        path.write_bytes(b'owned')
        self.assertEqual(tool.read_regular(path, 5), b'owned')
        with self.assertRaises(ValueError): tool.read_regular(path, 4)
        path.unlink(); os.mkfifo(path)
        with self.assertRaises(ValueError): tool.read_regular(path, 10)
        path.unlink(); path.symlink_to(TOOL)
        with self.assertRaises(ValueError): tool.read_regular(path, 100000)

    def test_pending_runtime_parameters_cannot_be_claimed_as_complete_config(self):
        result = self.tool().runtime_gaps()
        self.assertIs(result['base_v0_configured'], False)
        self.assertIs(result['game_profile_h_generated'], False)
        self.assertIn('nvn_samp_base', result['unproven_profile_fields'])
        self.assertIn('nvn_ssbo_bases', result['unproven_profile_fields'])
        self.assertIs(result['host_guest_gpu_executed'], False)

    def test_gate_aggregation_binds_every_module_and_preserves_sdk_count(self):
        tool = self.tool()
        counts = dict(zip(tool.MODULES, (93, 0, 0, 0, 0, 137)))
        report = {'modules': {name: {'normal_gate_count': count} for name, count in counts.items()}}
        self.assertEqual(tool.normal_gate_contract(report), {'total': 230, 'modules': counts})
        for changed in ({**counts, 'main': 0}, {**counts, 'sdk': 44}, {**counts, 'subsdk0': 1}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                tool.normal_gate_contract({'modules': {n: {'normal_gate_count': c} for n, c in changed.items()}})

    def test_output_is_created_exclusively_outside_all_protected_trees(self):
        tool = self.tool()
        source = self.directory / 'source'; source.mkdir()
        output = self.directory / 'fresh' / 'contract'
        self.assertEqual(tool.checked_output(output, [source]), output)
        self.assertTrue(output.is_dir())
        with self.assertRaises((ValueError, FileExistsError)):
            tool.checked_output(output, [source])

    def test_output_rejects_input_and_source_ancestors_and_descendants(self):
        tool = self.tool()
        for name in ('source', 'rewrite', 'guest'):
            protected = self.directory / name / 'owned'; protected.mkdir(parents=True)
            for output in (protected, protected / 'new', protected.parent):
                with self.subTest(name=name, output=str(output)), self.assertRaises(ValueError):
                    tool.checked_output(output, [protected])
                self.assertFalse((protected / 'new').exists())

    def test_output_rejects_symlink_parent_and_dangling_output(self):
        tool = self.tool()
        target = self.directory / 'target'; target.mkdir()
        link = self.directory / 'link'; link.symlink_to(target, target_is_directory=True)
        dangling = self.directory / 'dangling'; dangling.symlink_to(self.directory / 'absent')
        for output in (link / 'new', dangling, link / '..' / 'new'):
            with self.subTest(output=str(output)), self.assertRaises(ValueError):
                tool.checked_output(output, [])
        self.assertFalse((target / 'new').exists())

    def test_output_rejects_symlink_alias_to_protected_input(self):
        tool = self.tool()
        source = self.directory / 'source'; source.mkdir()
        link = self.directory / 'alias'; link.symlink_to(source, target_is_directory=True)
        with self.assertRaises(ValueError): tool.checked_output(link / 'new', [source])
        self.assertFalse((source / 'new').exists())

    def test_output_mkdir_does_not_accept_a_concurrent_claim(self):
        tool = self.tool()
        output = self.directory / 'race'
        original = Path.mkdir
        def competing_claim(path, *args, **kwargs):
            if path == output:
                original(path)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'mkdir', competing_claim):
            with self.assertRaises(FileExistsError): tool.checked_output(output, [])


if __name__ == '__main__':
    unittest.main()
