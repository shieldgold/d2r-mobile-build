"""Owned Mach-O metadata fixtures; no host/guest binary is ever executed."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PARSER = Path(os.environ.get('BOTW_SWFORCE_PARSER_PATH',
    ROOT / 'experiments/botw_upstream_host/scripts/read_swforce_registry.py'))


def macho_fixture(entries=None, pointer_format=6):
    """One owned __DATA page with local pointer chains and self-owned strings."""
    if entries is None:
        entries = [{'name': 'owned_symbol', 'why': 'owned reason', 'titles': 4}]
    # Keep the entire registry and every link in one real chained-fixup page.
    # The 132-entry profile occupies 4224 bytes and needs the supported 16K page.
    text_size, imagebase = 0x10000, 0x100000000
    page = 0x1000 if len(entries) * 32 <= 0x1000 else 0x4000
    if len(entries) * 32 > page:
        raise ValueError('owned fixture registry exceeds one 16K page')
    file = bytearray(text_size + 2 * page)
    strings = 0x800
    encoded = []
    for row in entries:
        addresses = []
        for value in (row['name'], row['why']):
            raw = value.encode() + b'\0'
            addresses.append(strings)
            file[strings:strings + len(raw)] = raw
            strings += len(raw)
        encoded.append(addresses)
    if strings > text_size:
        raise ValueError('owned fixture string table exceeds text')
    file[0x400:0x404] = bytes.fromhex('c0035fd6')  # self-owned ret, never executed

    def segment(name, vm, size, offset, prot, sections=()):
        command = struct.pack('<II16sQQQQiiII', 0x19, 72 + 80 * len(sections),
            name.encode(), vm, size, offset, size, prot, prot, len(sections), 0)
        for section, address, count, location in sections:
            command += struct.pack('<16s16sQQIIIIIIII', section.encode(), name.encode(),
                address, count, location, 3, 0, 0, 0, 0, 0, 0)
        return command

    # Header/commands live in text; all other bytes are ordinary fixture data.
    fixoff = text_size + page
    starts = struct.pack('<4I', 3, 0, 16, 0)
    starts += struct.pack('<IHHQIH', 24, page, pointer_format, text_size, 0, 1)
    starts += struct.pack('<H', 0 if entries else 0xffff)
    payload = struct.pack('<7I', 0, 28, 0, 0, 0, 0, 0) + starts
    commands = b''.join([
        segment('__TEXT', imagebase, text_size, 0, 5),
        segment('__DATA', imagebase + text_size, page, text_size, 3,
                [('swforce', imagebase + text_size, len(entries) * 32, text_size)]),
        segment('__LINKEDIT', imagebase + text_size + page, page, fixoff, 1),
        struct.pack('<4I', 0x80000034, 16, fixoff, len(payload))])
    file[:32] = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 4, len(commands), 0, 0)
    file[32:32 + len(commands)] = commands
    file[fixoff:fixoff + len(payload)] = payload
    for index, (row, (name, why)) in enumerate(zip(entries, encoded)):
        at = text_size + index * 32
        targets = (name, 0x400, why)
        for slot, target in enumerate(targets):
            if pointer_format == 2:
                target += imagebase
            next_units = 2 if slot < 2 else (4 if index + 1 < len(entries) else 0)
            struct.pack_into('<Q', file, at + slot * 8, target | (next_units << 51))
        struct.pack_into('<II', file, at + 24, row['titles'], 0)
    return file


class RegistryAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('owned_swforce_reader', PARSER)
        cls.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.reader)
        source = (ROOT / 'experiments/botw_upstream_host/adapter/base_v0_force.c').read_text()
        quoted = r'"(?:[^"\\]|\\.)*"'
        pattern = r'^\s*\{(' + quoted + r'), (' + quoted + r'), (\d+)u, \d+u, \d+u\},$'
        cls.expected_entries = [{'name': json.loads(name), 'why': json.loads(why), 'titles': int(titles)}
                                for name, why, titles in re.findall(pattern, source, re.M)]
        canonical = sorted(cls.expected_entries, key=lambda row: (row['name'], row['titles'], row['why']))
        cls.expected_sha256 = hashlib.sha256(json.dumps(canonical, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='botw-owned-swforce-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def file(self, data=None, name='owned-mach-o'):
        path = self.directory / name
        path.write_bytes(macho_fixture() if data is None else data)
        return path

    def cli(self, binary, output, expected=None):
        argv = [sys.executable, str(PARSER), '--input', str(binary), '--output', str(output)]
        if expected is not None: argv += ['--expect-metadata-sha256', expected]
        return subprocess.run(argv, capture_output=True, text=True, timeout=10)

    def test_cli_rejects_unknown_profile_without_writing_json(self):
        output = self.directory / 'audit.json'
        result = self.cli(self.file(), output, self.expected_sha256)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('swforce static audit failed:', result.stderr)
        self.assertFalse(output.exists())

    def test_local_chained_pointer_formats_6_and_2_decode_same_registry(self):
        expected = [{'name': 'owned_symbol', 'why': 'owned reason', 'titles': 4}]
        for fmt in (6, 2):
            for entries in (expected, self.expected_entries):
                with self.subTest(pointer_format=fmt, entries=len(entries)):
                    result = self.reader.read_registry(self.file(macho_fixture(entries, fmt)))
                    self.assertEqual(result['entries'], entries)
                    self.assertEqual(result['chained_pointer_formats'], [fmt])

    def test_fixed_expected_triples_match_the_existing_force_manifest(self):
        source = (ROOT / 'experiments/botw_upstream_host/adapter/base_v0_force.c').read_text()
        quoted = r'"(?:[^"\\]|\\.)*"'
        pattern = r'^\s*\{(' + quoted + r'), (' + quoted + r'), (\d+)u, \d+u, \d+u\},$'
        actual = [{'name': json.loads(name), 'why': json.loads(why), 'titles': int(titles)}
                  for name, why, titles in re.findall(pattern, source, re.M)]
        self.assertEqual(len(actual), 132)
        self.assertEqual(self.reader.canonical_entries(actual),
                         self.reader.canonical_entries(self.expected_entries))
        self.assertEqual(self.reader.metadata_sha256(actual), self.expected_sha256)

    def test_cli_accepts_owned_full_fixed_metadata_and_writes_canonical_sha(self):
        output = self.directory / 'audit.json'
        binary = self.file(macho_fixture(self.expected_entries))
        result = self.cli(binary, output, self.expected_sha256)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(output.read_text())
        self.assertEqual(evidence['count'], 132)
        self.assertEqual(evidence['canonical_metadata_sha256'], self.expected_sha256)
        self.assertEqual(evidence['binary_sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())
        self.assertTrue(evidence['expected_registry_verified'])

    def test_metadata_order_is_irrelevant_but_multiplicity_is_preserved(self):
        entries = list(reversed(self.expected_entries))
        result = self.reader.audit_registry(self.file(macho_fixture(entries)), self.expected_sha256)
        self.assertEqual(result['canonical_metadata_sha256'], self.expected_sha256)
        # Replacing one row with a duplicate must not collapse into a set.
        entries[0] = entries[1]
        with self.assertRaises(ValueError):
            self.reader.audit_registry(self.file(macho_fixture(entries)), self.expected_sha256)

    def test_changed_missing_and_added_metadata_never_match_the_fixed_profile(self):
        for field, value in (('name', 'unknown'), ('why', 'changed reason'), ('titles', 8)):
            entries = [dict(row) for row in self.expected_entries]
            entries[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.reader.audit_registry(self.file(macho_fixture(entries)), self.expected_sha256)
        entries = self.expected_entries[:-1]
        with self.assertRaises(ValueError):
            self.reader.audit_registry(self.file(macho_fixture(entries)), self.expected_sha256)
        entries = [*self.expected_entries, dict(self.expected_entries[0])]
        with self.assertRaises(ValueError):
            self.reader.audit_registry(self.file(macho_fixture(entries)), self.expected_sha256)

    def test_bind_unknown_format_and_missing_chain_membership_fail_closed(self):
        for mode in ('bind', 'format', 'missing'):
            data = macho_fixture(pointer_format=9 if mode == 'format' else 6)
            raw, = struct.unpack_from('<Q', data, 0x10000)
            if mode == 'bind': struct.pack_into('<Q', data, 0x10000, raw | (1 << 63))
            if mode == 'missing': struct.pack_into('<Q', data, 0x10000, raw & ((1 << 51) - 1))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.reader.read_registry(self.file(data))

    def test_registry_segment_must_match_the_actual_loader_lookup(self):
        data = macho_fixture()
        # Valid Mach-O relocation/section metadata in a different segment still
        # cannot satisfy getsectiondata(..., "__DATA", "swforce") in loader.c.
        segment_command = 32 + 72
        data[segment_command + 8:segment_command + 24] = b'__DATA_CONST'.ljust(16, b'\0')
        data[segment_command + 72 + 16:segment_command + 72 + 32] = b'__DATA_CONST'.ljust(16, b'\0')
        with self.assertRaises(ValueError):
            self.reader.read_registry(self.file(data))

    def test_function_target_must_be_local_executable_and_padding_zero(self):
        for mode in ('function', 'padding', 'title'):
            data = macho_fixture()
            if mode == 'function': struct.pack_into('<Q', data, 0x10008, 0x10000 | (2 << 51))
            if mode == 'padding': struct.pack_into('<I', data, 0x1001c, 1)
            if mode == 'title': struct.pack_into('<I', data, 0x10018, 0)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.reader.read_registry(self.file(data))

    def test_symlink_truncation_wrong_cpu_and_command_bounds_fail_closed(self):
        binary = self.file()
        link = self.directory / 'link'; link.symlink_to(binary)
        with self.assertRaises(ValueError): self.reader.read_registry(link)
        for mode in ('short', 'cpu', 'commands', 'magic'):
            data = macho_fixture()
            if mode == 'short': data = data[:32]
            if mode == 'cpu': struct.pack_into('<I', data, 4, 7)
            if mode == 'commands': struct.pack_into('<I', data, 20, len(data))
            if mode == 'magic': struct.pack_into('<I', data, 0, 0xcafebabe)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.reader.read_registry(self.file(data))

    def test_cli_without_expected_digest_is_explicitly_an_unverified_metadata_audit(self):
        output = self.directory / 'audit.json'
        result = self.cli(self.file(), output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        audit = json.loads(output.read_text())
        self.assertEqual(audit['count'], 1)
        self.assertFalse(audit['expected_registry_verified'])

    def test_invalid_expected_digest_and_input_output_alias_preserve_input(self):
        binary = self.file(); before = binary.read_bytes()
        for expected in ('bad', 'G' * 64):
            with self.subTest(expected=expected):
                result = self.cli(binary, self.directory / 'audit.json', expected)
                self.assertEqual(result.returncode, 1)
        result = self.cli(binary, binary)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(binary.read_bytes(), before)
        link = self.directory / 'hardlink'; os.link(binary, link)
        result = self.cli(binary, link)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(binary.read_bytes(), before)

    def test_cli_malformed_input_returns_failure_without_traceback_or_json(self):
        output = self.directory / 'audit.json'
        result = self.cli(self.file(b'owned-invalid'), output)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())

    def test_actual_mac_link_only_file_has_the_fixed_registry(self):
        binary = Path(os.environ.get('BOTW_WHOLE_REGISTRY_BINARY', ROOT /
            ('outputs/botw-whole-host-integration/mac-closure-first/'
             'whole-host-LINK-ONLY-DO-NOT-RUN')))
        if not binary.is_file():
            self.skipTest('private linked Mac artifact is unavailable; owned metadata tests still run')
        result = self.reader.audit_registry(binary, self.expected_sha256)
        self.assertEqual(result['count'], 132)
        self.assertEqual(result['canonical_metadata_sha256'], self.expected_sha256)


if __name__ == '__main__':
    unittest.main()
