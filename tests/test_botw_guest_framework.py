"""Mach-O packaging tests use invented module bytes, never game content."""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
try:
    from botw_native.guest_framework import build_framework
except ImportError:
    build_framework = None

NAMES = ('main', 'rtld', 'sdk', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3')
PG = 0x4000
COMMIT = 'e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8'


def fixture(root):
    rewrite = root / 'rewrite'
    rewrite.mkdir()
    report = {'schema': 1, 'status': 'rewritten', 'modules': [], 'rewrites': [],
              'rewriter': {'upstream_commit': COMMIT, 'ruleset': 'native', 'private_gates': False}}
    for index, name in enumerate(NAMES):
        image = bytearray(0x10004)
        struct.pack_into('<I', image, 0, 0x14004000)  # B +0x10000
        image[0x4000:0x4100] = bytes([index + 1]) * 0x100
        struct.pack_into('<I', image, 0x10000, 0xd65f03c0)  # RET, never executed
        (rewrite / f'{name}.patched.bin').write_bytes(image)
        layout = {'PATCH_OFF': 0x10000, 'KEY_PAGE_OFF': 0x8000,
                  'KEY_PAGE_SIZE': PG, 'TEXT_END': 0x100,
                  'RO_OFF': 0x4000, 'DATA_OFF': 0x6000}
        layout.update({f'KEY_{key}_IDX': i for i, key in enumerate(
            ('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI'))})
        (rewrite / f'{name}.fixups.tsv').write_text(''.join(
            f'# {k}\t{v if k.endswith("_IDX") else hex(v)}\n' for k, v in layout.items()))
        (rewrite / f'{name}.gates.tsv').write_text('0x10004\tsvc\t38\t0x0\n')
        (rewrite / f'{name}.sites.tsv').write_text('0x0\tx18\n')
        for kind in ('cuts', 'obs', 'params'):
            (rewrite / f'{name}.{kind}.tsv').write_text('# BUILD_ID\t' + '12' * 32 + '\n')
        report['modules'].append({'name': name, 'sha256': 'ab' * 32, 'build_id': '12' * 32,
                                  'memory_bytes': 0x8000, 'segments': [
                                      {'name': 'text', 'address': 0, 'bytes': 0x100},
                                      {'name': 'rodata', 'address': 0x4000, 'bytes': 0x100},
                                      {'name': 'data', 'address': 0x6000, 'bytes': 0x100}]})
        report['rewrites'].append({'name': name, 'status': 'rewritten',
                                   'image_sha256': hashlib.sha256(image).hexdigest(),
                                   'rewrite_site_count': 1,
                                   'generic_tables': {'cuts': 0, 'obs': 0, 'params': 0},
                                   'structure': {'branch_sites_checked': 1, 'host_gate_addresses_checked': 1}})
    path = root / 'report.json'
    path.write_text(json.dumps(report))
    return path, rewrite


def macho(data):
    """Independently parse load commands for loader-visible section checks."""
    header = struct.unpack_from('<IiiIIIII', data)
    cursor, segments, commands = 32, {}, {}
    for _ in range(header[4]):
        command, size = struct.unpack_from('<II', data, cursor)
        commands.setdefault(command, []).append(data[cursor:cursor + size])
        if command == 0x19:
            fields = struct.unpack_from('<II16sQQQQiiII', data, cursor)
            name = fields[2].rstrip(b'\0').decode()
            sections = {}
            for i in range(fields[9]):
                section = struct.unpack_from('<16s16sQQIIIIIIII', data, cursor + 72 + i * 80)
                sections[section[0].rstrip(b'\0').decode()] = {
                    'address': section[2], 'size': section[3], 'offset': section[4],
                    'align': section[5], 'flags': section[8]}
            segments[name] = {'vmaddr': fields[3], 'vmsize': fields[4],
                              'fileoff': fields[5], 'filesize': fields[6],
                              'maxprot': fields[7], 'initprot': fields[8], 'sections': sections}
        cursor += size
    if cursor != 32 + header[5]:
        raise AssertionError('load command byte count differs')
    return header, segments, commands


class GuestFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(build_framework, 'BOTW Guest.framework packager is missing')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.report, self.rewrite = fixture(self.root)
        self.output = self.root / 'bundle'

    def save_report(self, change):
        data = json.loads(self.report.read_text())
        change(data)
        self.report.write_text(json.dumps(data))

    def test_host39_rejects_partial_mislabeled_and_unknown_target_evidence(self):
        baseline = json.loads(self.report.read_text())
        cases = [
            ({'schema': 1, 'profile': 'botw-switch-base-v0-host39',
              'target_address_bits': 39, 'game_playable': False}, False),
            ({'schema': 1, 'profile': 'other', 'target_address_bits': 39, 'game_playable': False}, False),
            ({'schema': True, 'profile': 'botw-switch-base-v0-host39',
              'target_address_bits': 39, 'game_playable': False}, False),
            (None, True),
        ]
        for policy, adaptation in cases:
            changed = copy.deepcopy(baseline)
            if policy is not None:
                changed['host_address_policy'] = policy
            if adaptation:
                changed['rewrites'][0]['host_address_adaptation'] = {}
            self.report.write_text(json.dumps(changed))
            with self.subTest(policy=policy), self.assertRaisesRegex(ValueError, 'address|Host39|Address'):
                build_framework(self.report, self.rewrite, self.output)
            self.assertFalse(self.output.exists())

    def test_framework_reconstructs_all_seven_images_via_loader_section_names(self):
        result = build_framework(self.report, self.rewrite, self.output)
        self.assertEqual(result['guest_address_bits'], 36)
        binary = (self.output / 'Guest.framework/Guest').read_bytes()
        header, segments, commands = macho(binary)
        self.assertEqual(header[:4], (0xfeedfacf, 0x0100000c, 0, 6))
        self.assertEqual([n for n, s in segments.items() if s['initprot'] & 4], ['__TEXT'])
        self.assertEqual(segments['__TEXT']['initprot'], 5)
        for seg in segments.values():
            self.assertFalse(seg['maxprot'] & 2 and seg['maxprot'] & 4)
            self.assertFalse(seg['initprot'] & 2 and seg['initprot'] & 4)
        for name in NAMES:
            sections = [segments['__TEXT']['sections'][f'__dm{name}x0'],
                        segments[f'__DM{name}W']['sections']['__img'],
                        segments['__TEXT']['sections'][f'__dm{name}x1']]
            self.assertEqual(segments[f'__DM{name}W']['initprot'], 3)
            for section in sections:
                self.assertEqual(section['offset'] % PG, 0)
                self.assertEqual(section['address'] % PG, 0)
                self.assertEqual(section['size'] % PG, 0)
                self.assertEqual(section['align'], 14)
            reconstructed = b''.join(binary[s['offset']:s['offset'] + s['size']] for s in sections)
            original = (self.rewrite / f'{name}.patched.bin').read_bytes()
            self.assertEqual(reconstructed[:len(original)], original)
            self.assertEqual(reconstructed[len(original):], bytes(len(reconstructed) - len(original)))
        self.assertEqual(result['status'], 'framework_packaged')
        self.assertFalse(result['phone_ready'])
        self.assertFalse(result['guest_execution_tested'])

    def test_libsystem_ios_metadata_and_no_initializer_or_exported_macho_code(self):
        build_framework(self.report, self.rewrite, self.output, bundle_id='local.test.botw', minimum_ios='26.1')
        binary = (self.output / 'Guest.framework/Guest').read_bytes()
        _, segments, commands = macho(binary)
        self.assertIn(b'/usr/lib/libSystem.B.dylib\0', commands[0xc][0])
        self.assertIn(b'@rpath/Guest.framework/Guest\0', commands[0xd][0])
        self.assertEqual(struct.unpack('<IIIIII', commands[0x32][0])[2:5], (2, 0x1a0100, 0x1a0100))
        self.assertEqual(struct.unpack('<IIIIII', commands[2][0])[3], 0)
        self.assertEqual(struct.unpack('<II10I', commands[0x80000022][0])[2:], (0,) * 10)
        self.assertEqual(list(segments)[-1], '__LINKEDIT')
        self.assertFalse(any(s['flags'] & 0xff == 9 for seg in segments.values() for s in seg['sections'].values()))
        info = plistlib.loads((self.output / 'Guest.framework/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleIdentifier'], 'local.test.botw')
        self.assertEqual(info['MinimumOSVersion'], '26.1')

    def test_report_hashes_all_inputs_and_published_loader_tables(self):
        result = build_framework(self.report, self.rewrite, self.output)
        self.assertEqual(json.loads((self.output / 'guest-framework-report.json').read_text()), result)
        sources = {s['path']: s for s in result['sources']}
        expected = {'report.json'} | {f'rewrite/{n}.{ext}' for n in NAMES for ext in
                                     ('patched.bin', 'fixups.tsv', 'gates.tsv', 'sites.tsv', 'cuts.tsv', 'obs.tsv', 'params.tsv')}
        self.assertEqual(set(sources), expected)
        for relative, entry in sources.items():
            data = (self.root / relative).read_bytes()
            self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(entry['bytes'], len(data))
        for name in NAMES:
            for kind in ('fixups', 'gates'):
                self.assertEqual((self.output / f'dm/{name}.{kind}.tsv').read_bytes(),
                                 (self.rewrite / f'{name}.{kind}.tsv').read_bytes())
        binary = self.output / 'Guest.framework/Guest'
        self.assertEqual(result['framework']['sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_identical_inputs_produce_identical_binary_and_report(self):
        first = build_framework(self.report, self.rewrite, self.output)
        other = self.root / 'other'
        second = build_framework(self.report, self.rewrite, other)
        self.assertEqual((self.output / 'Guest.framework/Guest').read_bytes(), (other / 'Guest.framework/Guest').read_bytes())
        self.assertEqual(first, second)

    def test_host_can_choose_six_non_rtld_modules(self):
        selected = [n for n in NAMES if n != 'rtld']
        result = build_framework(self.report, self.rewrite, self.output, module_names=selected)
        _, segments, _ = macho((self.output / 'Guest.framework/Guest').read_bytes())
        self.assertNotIn('__DMrtldW', segments)
        self.assertEqual([m['name'] for m in result['modules']], selected)

    def test_rejects_changed_image_without_publishing_output(self):
        image = self.rewrite / 'subsdk3.patched.bin'
        image.write_bytes(image.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256|sha256|digest'):
            build_framework(self.report, self.rewrite, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_unfinished_report_and_non_generic_ruleset(self):
        original = self.report.read_text()
        mutations = [lambda d: d.update(status='rewrite_failed'),
                     lambda d: d['rewriter'].update(ruleset='touch-run-v4'),
                     lambda d: d['rewriter'].update(private_gates=True),
                     lambda d: d['rewriter'].update(upstream_commit='0' * 40),
                     lambda d: d['rewrites'][0].update(status='failed')]
        for mutation in mutations:
            self.report.write_text(original)
            self.save_report(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output)
            self.assertFalse(self.output.exists())

    def test_rejects_duplicate_missing_and_unsafe_module_names(self):
        original = self.report.read_text()
        mutations = [lambda d: d['modules'].append(copy.deepcopy(d['modules'][0])),
                     lambda d: d['rewrites'].pop(),
                     lambda d: d['modules'][0].update(name='../outside')]
        for mutation in mutations:
            self.report.write_text(original)
            self.save_report(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output)
        self.report.write_text(original)
        for names in ([], ['main', 'main'], ['../outside'], ['main', 'long-name']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output, module_names=names)

    def test_rejects_unaligned_overlapping_and_out_of_range_key_layouts(self):
        path = self.rewrite / 'main.fixups.tsv'
        original = path.read_text()
        for before, after in [('PATCH_OFF\t0x10000', 'PATCH_OFF\t0x10001'),
                              ('KEY_PAGE_OFF\t0x8000', 'KEY_PAGE_OFF\t0x0'),
                              ('KEY_PAGE_SIZE\t0x4000', 'KEY_PAGE_SIZE\t0x8000'),
                              ('KEY_K18_IDX\t0', 'KEY_K18_IDX\t2048'),
                              ('KEY_K18_IDX\t0', 'KEY_K18_IDX\t1'),
                              ('TEXT_END\t0x100', 'TEXT_END\t0x6000')]:
            path.write_text(original.replace(before, after))
            with self.subTest(after=after), self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output)
            self.assertFalse(self.output.exists())

    def test_rejects_old_fixup_rows_duplicate_headers_and_non_generic_tables(self):
        path = self.rewrite / 'main.fixups.tsv'
        original = path.read_text()
        for addition in ('0x40\twrite_code\n', '# PATCH_OFF\t0x10000\n'):
            path.write_text(original + addition)
            with self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output)
        path.write_text(original)
        for kind in ('cuts', 'obs', 'params'):
            table = self.rewrite / f'main.{kind}.tsv'
            table.write_text('0x40\tgame-specific\n')
            with self.assertRaisesRegex(ValueError, kind):
                build_framework(self.report, self.rewrite, self.output)
            table.write_text('# synthetic\n')

    def test_rejects_invalid_branch_sites_and_gate_addresses(self):
        path = self.rewrite / 'main.sites.tsv'
        original = path.read_text()
        for rows in ('0x4\tx18\n', '0x1\tx18\n', '0x0\tx18\n0x0\tx18\n'):
            path.write_text(rows)
            with self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output)
        path.write_text(original)
        for row in ('0x10008\tsvc\t38\t0x0\n', '0x10004\tsvc\t38\t0x100\n'):
            (self.rewrite / 'main.gates.tsv').write_text(row)
            with self.assertRaises(ValueError):
                build_framework(self.report, self.rewrite, self.output)

    def test_same_site_can_describe_a_register_rewrite_and_a_jump_table(self):
        # The generic rewriter records both classifications at some real sites.
        (self.rewrite / 'main.sites.tsv').write_text('0x0\tx18\n0x0\tJ\t0x4\t2\n')

        def update(data):
            rewrite = data['rewrites'][0]
            rewrite['rewrite_site_count'] = 2
            rewrite['structure']['branch_sites_checked'] = 2

        self.save_report(update)
        result = build_framework(self.report, self.rewrite, self.output)
        self.assertEqual(result['status'], 'framework_packaged')

    def test_rejects_symlinked_inputs_and_preserves_existing_output(self):
        original = self.rewrite / 'main.gates.tsv'
        alternate = self.root / 'alternate'
        original.rename(alternate)
        original.symlink_to(alternate)
        with self.assertRaisesRegex(ValueError, 'symlink|regular'):
            build_framework(self.report, self.rewrite, self.output)
        original.unlink()
        alternate.rename(original)
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            build_framework(self.report, self.rewrite, self.output)
        self.assertEqual(sentinel.read_bytes(), b'original')

    def test_failed_write_cleans_staging_directory(self):
        real_write = Path.write_bytes

        def fail_binary(path, data):
            if path.name == 'Guest':
                raise OSError('simulated storage failure')
            return real_write(path, data)

        with patch.object(Path, 'write_bytes', fail_binary), self.assertRaisesRegex(OSError, 'storage failure'):
            build_framework(self.report, self.rewrite, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['report.json', 'rewrite'])

    def test_cli_builds_local_framework_without_executing_guest(self):
        env = dict(__import__('os').environ, PYTHONPATH=str(Path(__file__).parents[1] / 'scripts'))
        result = subprocess.run([sys.executable, '-m', 'botw_native.guest_framework',
                                 '--report', str(self.report), '--rewrite', str(self.rewrite),
                                 '--output', str(self.output)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Guest.framework', result.stdout)
        self.assertTrue((self.output / 'guest-framework-report.json').is_file())


if __name__ == '__main__':
    unittest.main()
