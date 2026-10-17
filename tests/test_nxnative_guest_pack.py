"""Owned Mach-O fixtures; no framework is loaded or executed."""
import hashlib
import importlib.util
import plistlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_botw_guest_framework import macho

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'experiments/nxnative_base_v0/tools/pack_guest.py'


class NxnativeGuestPack(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), 'base-v0 offline Guest packer is absent')
        spec = importlib.util.spec_from_file_location('owned_nxnative_guest_packer', TOOL)
        self.tool = importlib.util.module_from_spec(spec)
        # The test reads owned source, never a bytecode cache.
        exec(compile(TOOL.read_bytes(), str(TOOL), 'exec'), self.tool.__dict__)
        self.temp = tempfile.TemporaryDirectory(prefix='owned-nxnative-pack-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def modules(self):
        result = []
        for i, name in enumerate(self.tool.MODULES):
            image = bytearray(0x10004)
            image[:0x100] = bytes([i + 1]) * 0x100
            image[0x5000:0x5100] = bytes([i + 11]) * 0x100
            image[0x10000:] = struct.pack('<I', 0xd65f03c0)  # data, never executed
            result.append({'name': name, 'image': bytes(image),
                           'image_sha256': hashlib.sha256(image).hexdigest(),
                           'layout': {'TEXT_END': 0x100, 'PATCH_OFF': 0x10000}})
        return result

    def test_complete_legacy_emitter_reconstructs_all_six_loader_images(self):
        modules = self.modules()
        binary = self.tool.emit_framework(modules)
        header, segments, commands = macho(binary)
        self.assertEqual(header[:4], (0xfeedfacf, 0x0100000c, 0, 6))
        self.assertEqual([n for n, s in segments.items() if s['initprot'] & 4], ['__TEXT'])
        for segment in segments.values():
            self.assertFalse(segment['maxprot'] & 2 and segment['maxprot'] & 4)
        for module in modules:
            name = module['name']
            sections = [segments['__TEXT']['sections'][f'__dm{name}x0'],
                        segments[f'__DM{name}W']['sections']['__img'],
                        segments['__TEXT']['sections'][f'__dm{name}x1']]
            reconstructed = b''.join(binary[s['offset']:s['offset'] + s['size']] for s in sections)
            self.assertEqual(reconstructed[:len(module['image'])], module['image'])
            self.assertEqual(reconstructed[len(module['image']):], bytes(len(reconstructed) - len(module['image'])))
            self.assertTrue(all(s['offset'] % 0x4000 == s['size'] % 0x4000 == 0 for s in sections))
        self.assertEqual(struct.unpack('<IIIIII', commands[0x32][0])[2:5], (2, 0x1a0000, 0x1a0000))
        self.assertEqual(struct.unpack('<IIIIII', commands[2][0])[3], 0)

    def test_wrong_order_duplicate_extra_and_invalid_layout_are_rejected(self):
        cases = [self.modules()[::-1], self.modules()[:-1], self.modules() + [self.modules()[0]]]
        invalid = self.modules()
        invalid[0]['layout']['PATCH_OFF'] = 0x10001
        cases.append(invalid)
        invalid = self.modules()
        invalid[0]['image'] = b'bad'
        cases.append(invalid)
        for modules in cases:
            with self.subTest(case=cases.index(modules)), self.assertRaises(ValueError):
                self.tool.emit_framework(modules)

    def test_legacy_source_mismatch_is_rejected_before_ast_execution(self):
        source = self.root / 'untrusted.py'
        source.write_text("raise RuntimeError('UNLOCKED_SOURCE_MUST_NOT_EXECUTE')\n")
        with patch.object(self.tool, 'EMITTER', source), self.assertRaisesRegex(ValueError, 'source'):
            self.tool.emit_framework(self.modules())

    def test_fresh_output_required_before_any_source_or_payload_read(self):
        output = self.root / 'existing'
        output.mkdir()
        with patch.object(self.tool, 'admit_payload') as reader, self.assertRaises(ValueError):
            self.tool.pack(self.root / 'unused', output)
        reader.assert_not_called()
        alias = self.root / 'alias'
        alias.symlink_to(output, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.tool.pack(self.root / 'unused', alias)

    def test_published_owned_framework_is_unsigned_nonexecuted_and_complete(self):
        modules = self.modules()
        evidence = {'source_lock_sha256': 'owned-only', 'report_sha256': 'owned-only'}
        tables = {f'{n}.{kind}.tsv': b'' for n in self.tool.MODULES for kind in ('fixups', 'gates', 'cuts', 'params', 'obs')}
        output = self.root / 'fresh'
        with patch.object(self.tool, 'admit_payload', return_value=(modules, tables, evidence)):
            result = self.tool.pack(self.root / 'owned', output)
        binary = output / 'Guest.framework/Guest'
        self.assertEqual(binary.stat().st_mode & 0o777, 0o444)
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), result['framework']['sha256'])
        self.assertFalse(result['signed'])
        self.assertFalse(result['guest_executed'])
        self.assertFalse(result['phone_ready'])
        info = plistlib.loads((output / 'Guest.framework/Info.plist').read_bytes())
        self.assertEqual(info['MinimumOSVersion'], '26.0')
        self.assertEqual({p.name for p in (output / 'dm').iterdir()}, set(tables))

    def test_source_component_cannot_be_reached_through_a_parent_alias(self):
        alias = self.root / 'source-alias'
        alias.symlink_to(ROOT / 'experiments/nxnative_base_v0', target_is_directory=True)
        with patch.object(self.tool, 'admit_payload', side_effect=AssertionError('payload read before output validation')):
            with self.assertRaises(ValueError):
                self.tool.pack(self.root / 'unused', alias / 'must-not-be-created')

    def test_image_changed_after_admission_is_rejected_before_emission(self):
        modules = self.modules()
        altered = bytearray(modules[0]['image'])
        altered[0x40] ^= 1
        modules[0]['image'] = bytes(altered)
        with self.assertRaisesRegex(ValueError, 'image'):
            self.tool.emit_framework(modules)

    def test_output_created_during_admission_is_rejected_without_publishing(self):
        output = self.root / 'concurrently-created'
        def create_output(_):
            output.mkdir()
            (output / 'canary').write_text('preserve')
            return self.modules(), {}, {'source_lock_sha256': 'owned-only'}
        with patch.object(self.tool, 'admit_payload', side_effect=create_output):
            with self.assertRaises((ValueError, FileExistsError)):
                self.tool.pack(self.root / 'owned', output)
        self.assertEqual({p.name for p in output.iterdir()}, {'canary'})


if __name__ == '__main__':
    unittest.main()
