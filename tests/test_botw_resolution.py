import struct
import sys
import tempfile
import unittest
from pathlib import Path
import plistlib

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import botw_resolution as resolution


class ResolutionTests(unittest.TestCase):
    def original(self):
        data = bytearray(0x1266200)
        for offset, word in resolution.ORIGINAL.items():
            struct.pack_into('<I', data, offset, word)
        # Existing VFR/60 Hz patch must survive unchanged.
        data[0x112670c:0x1126710] = bytes.fromhex('4041201e')
        return bytes(data)

    def test_540p_changes_only_verified_resolution_words(self):
        original = self.original()
        patched = resolution.patch_text(original, 540)
        allowed = {i for offset in resolution.ORIGINAL for i in range(offset, offset + 4)}
        self.assertTrue(all(i in allowed for i, (a, b) in enumerate(zip(original, patched)) if a != b))
        self.assertEqual(len(patched), len(original))
        self.assertEqual(patched[0x112670c:0x1126710], bytes.fromhex('4041201e'))
        words = {offset: struct.unpack_from('<I', patched, offset)[0] for offset in resolution.ORIGINAL}
        # Decode the actual AArch64 MOVZ/MOVK immediates, not an implementation helper.
        self.assertEqual((words[0x1266120] >> 5) & 65535, 960)
        self.assertEqual((words[0x1266124] >> 5) & 65535, 540)
        self.assertEqual((words[0x1266180] >> 5) & 65535, 960)
        self.assertEqual((words[0x1266188] >> 5) & 65535, 540)
        self.assertEqual((words[0x1266114] >> 5) & 65535, 0x4470)
        self.assertEqual((words[0x1266134] >> 5) & 65535, 0x4407)

    def test_native_resolution_is_byte_identical(self):
        original = self.original()
        self.assertEqual(resolution.patch_text(original, 720), original)

    def test_mismatch_refuses_entire_patch(self):
        original = bytearray(self.original())
        original[0x1266188] ^= 1
        before = bytes(original)
        with self.assertRaisesRegex(ValueError, '1266188'):
            resolution.patch_text(original, 540)
        self.assertEqual(bytes(original), before)

    def test_rejects_unsupported_sizes_and_truncation(self):
        with self.assertRaises(ValueError):
            resolution.patch_text(self.original(), 481)
        with self.assertRaises(ValueError):
            resolution.patch_text(b'\0' * 100, 540)

    def test_prepare_rejects_other_app_versions_and_build_ids_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.app'
            destination = Path(directory) / 'trial.app'
            (source / 'dm').mkdir(parents=True)
            info = {'CFBundleIdentifier': 'local.botw.native',
                    'CFBundleShortVersionString': '1.9.0',
                    'CFBundleVersion': '1018.2', 'CFBundleExecutable': 'DmIOS'}
            (source / 'Info.plist').write_bytes(plistlib.dumps(info))
            with self.assertRaisesRegex(ValueError, '1018.3'):
                resolution.prepare(source, destination, 540, '1018.4')
            info['CFBundleVersion'] = '1018.3'
            (source / 'Info.plist').write_bytes(plistlib.dumps(info))
            (source / 'dm/main.fixups.tsv').write_text('# BUILD_ID\tother-build\n')
            with self.assertRaisesRegex(ValueError, 'build ID'):
                resolution.prepare(source, destination, 540, '1018.4')
            self.assertFalse(destination.exists())

    def test_432p_and_360p_preserve_exact_16_9_dimensions(self):
        original = self.original()
        for height in (432, 360):
            data = resolution.patch_text(original, height)
            width = (struct.unpack_from('<I', data, 0x1266120)[0] >> 5) & 65535
            output_height = (struct.unpack_from('<I', data, 0x1266124)[0] >> 5) & 65535
            self.assertEqual(width * 9, output_height * 16)
            self.assertEqual(output_height, height)

    def test_macho_section_is_bounded_and_unique(self):
        data = bytearray(512)
        struct.pack_into('<IIIIIIII', data, 0, 0xfeedfacf, 0x100000c, 0, 2, 1, 152, 0, 0)
        struct.pack_into('<II', data, 32, 25, 152)
        struct.pack_into('<I', data, 32 + 64, 1)
        offset = 32 + 72
        data[offset:offset + 16] = b'__dmmainx0'.ljust(16, b'\0')
        data[offset + 16:offset + 32] = b'__TEXT'.ljust(16, b'\0')
        struct.pack_into('<Q', data, offset + 40, 128)
        struct.pack_into('<I', data, offset + 48, 256)
        self.assertEqual(resolution.main_section(bytes(data)), (256, 128))
        struct.pack_into('<Q', data, offset + 40, 1000)
        with self.assertRaises(ValueError):
            resolution.main_section(bytes(data))


if __name__ == '__main__':
    unittest.main()
