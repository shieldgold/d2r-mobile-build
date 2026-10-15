"""Ensure the cloud artifact contains only a generic host and its provenance."""
import hashlib
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile

from scripts.botw_native.build_ios_shell import package_shell


class ShellPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / 'BOTWNative.app'
        self.app.mkdir()
        self.binary = b'\xcf\xfa\xed\xfe' + b'generic-arm64-host'
        (self.app / 'BOTWNative').write_bytes(self.binary)
        (self.app / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.botw.native', 'CFBundleExecutable': 'BOTWNative',
            'MinimumOSVersion': '26.0'}))

    def test_cloud_package_has_no_guest_and_records_binary_digest(self):
        ipa = self.root / 'host.ipa'
        package_shell(self.app, ipa, {'native/botw_host/runtime.c': 'a' * 64}, '26.5')
        with zipfile.ZipFile(ipa) as z:
            self.assertEqual(set(z.namelist()), {
                'Payload/BOTWNative.app/BOTWNative', 'Payload/BOTWNative.app/Info.plist',
                'Payload/BOTWNative.app/shell-manifest.json'})
            m = json.loads(z.read('Payload/BOTWNative.app/shell-manifest.json'))
        self.assertFalse(m['game_content_included'])
        self.assertEqual(m['executable_sha256'], hashlib.sha256(self.binary).hexdigest())

    def test_extra_resource_or_game_file_is_rejected_before_publishing(self):
        (self.app / 'game.nso').write_bytes(b'never-upload')
        ipa = self.root / 'host.ipa'
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            package_shell(self.app, ipa, {}, '26.5')
        self.assertFalse(ipa.exists())

    def test_symlink_binary_is_rejected(self):
        (self.app / 'BOTWNative').unlink()
        (self.root / 'external').write_bytes(self.binary)
        (self.app / 'BOTWNative').symlink_to(self.root / 'external')
        with self.assertRaisesRegex(ValueError, 'regular'):
            package_shell(self.app, self.root / 'host.ipa', {}, '26.5')


if __name__ == '__main__':
    unittest.main()
