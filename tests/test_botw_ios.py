"""Local iOS packaging checks use invented source, binaries and certificates."""
import datetime
import hashlib
import io
import json
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from test_botw_guest_framework import fixture
from botw_native.guest_framework import build_framework
try:
    from botw_native.ios import assemble_app, sign_app, validate_profile, main
except ImportError:
    assemble_app = sign_app = validate_profile = main = None


class IOSAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(assemble_app, 'Local iOS assembler is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report, self.rewrite = fixture(self.root)
        self.guest = self.root / 'guest'
        build_framework(self.report, self.rewrite, self.guest)
        self.sources = self.root / 'sources'
        paths = ('native/botw_host/runtime.c', 'native/botw_host/runtime.h',
                 'native/botw_host/entry.S', 'native/botw_app/main.m')
        hashes = {}
        for relative in paths:
            path = self.sources / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/* synthetic source */\n')
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        command = struct.pack('<IIIIII', 0x32, 24, 2, 26 << 16, 26 << 16, 0)
        self.executable = struct.pack('<IiiIIIII', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0) + command
        self.info = {'CFBundleExecutable': 'BOTWNative', 'CFBundleIdentifier': 'local.botw.native',
                     'MinimumOSVersion': '26.0', 'CFBundlePackageType': 'APPL'}
        self.manifest = {'schema': 1, 'role': 'botw-native-host', 'game_content_included': False,
                         'sdk_version': '26.5', 'source_sha256': hashes,
                         'executable_sha256': hashlib.sha256(self.executable).hexdigest()}
        self.shell = self.root / 'shell.ipa'
        self.app = self.root / 'BOTWNative.app'
        self.write_shell()

    def write_shell(self, extra=()):
        base = 'Payload/BOTWNative.app/'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.shell, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(base + 'Info.plist', plistlib.dumps(self.info))
                archive.writestr(base + 'BOTWNative', self.executable)
                archive.writestr(base + 'shell-manifest.json', json.dumps(self.manifest))
                for path, data in extra:
                    archive.writestr(path, data)

    def assemble(self, **kwargs):
        kwargs.setdefault('expected_shell_sha256', hashlib.sha256(self.shell.read_bytes()).hexdigest())
        return assemble_app(self.shell, self.guest, self.report, self.rewrite, self.app,
                            source_root=self.sources, **kwargs)

    def test_assembly_verifies_inputs_and_publishes_only_runtime_tables(self):
        result = self.assemble()
        self.assertEqual(result, self.app)
        self.assertEqual((result / 'BOTWNative').read_bytes(), self.executable)
        self.assertEqual(plistlib.loads((result / 'Info.plist').read_bytes())['BOTWGuestAddressBits'], 36)
        self.assertEqual((result / 'BOTWNative').stat().st_mode & 0o777, 0o755)
        self.assertEqual((result / 'Frameworks/Guest.framework/Guest').read_bytes(),
                         (self.guest / 'Guest.framework/Guest').read_bytes())
        self.assertEqual((result / 'botw/main.fixups.tsv').read_bytes(),
                         (self.rewrite / 'main.fixups.tsv').read_bytes())
        self.assertFalse(list((result / 'botw').glob('*.patched.bin')))
        self.assertFalse((result / 'embedded.mobileprovision').exists())
        with self.assertRaises(FileExistsError):
            self.assemble()

    def test_audio_module_source_provenance_is_verified(self):
        relative = 'native/botw_audio/audio.c'
        source = self.sources / relative
        source.parent.mkdir()
        source.write_text('/* synthetic AudioOut backend */\n')
        self.manifest['source_sha256'][relative] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.write_shell()
        self.assemble()
        source.write_text('/* changed after the shell build */\n')
        self.app = self.root / 'changed-audio' / 'BOTWNative.app'
        with self.assertRaisesRegex(ValueError, 'Local shell source SHA256 mismatch: ' + relative):
            self.assemble()

    def test_shell_rejects_traversal_duplicate_case_collision_symlink_and_extra_payload(self):
        link = zipfile.ZipInfo('Payload/BOTWNative.app/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [('../outside', b'bad'), ('/absolute', b'bad'),
                 ('Payload/BOTWNative.app/BOTWNative', b'duplicate'),
                 ('Payload/BOTWNative.app/info.plist', b'case collision'),
                 ('Payload\\BOTWNative.app\\evil', b'backslash'),
                 (link, b'../../outside'), ('Payload/BOTWNative.app/romfs/data', b'game')]
        for entry in cases:
            with self.subTest(path=str(entry[0])):
                self.write_shell([entry])
                with self.assertRaises(ValueError):
                    self.assemble()
                self.assertFalse(self.app.exists())
        self.assertFalse((self.root / 'outside').exists())

    def test_shell_enforces_uncompressed_size_limit_before_publication(self):
        self.executable += bytes(64 * 1024)
        self.manifest['executable_sha256'] = hashlib.sha256(self.executable).hexdigest()
        self.write_shell()
        self.assertLess(self.shell.stat().st_size, 4096)
        with self.assertRaisesRegex(ValueError, 'limit'):
            self.assemble(max_shell_bytes=4096)
        self.assertFalse(self.app.exists())

    def test_shell_rejects_changed_binary_wrong_bundle_minimum_and_sources(self):
        changes = [lambda: self.manifest.update(executable_sha256='00' * 32),
                   lambda: self.info.update(CFBundleIdentifier='com.other.app'),
                   lambda: self.info.update(MinimumOSVersion='18.0'),
                   lambda: self.manifest.update(game_content_included=True),
                   lambda: self.manifest['source_sha256'].update({'native/botw_host/runtime.c': '00' * 32})]
        for change in changes:
            old_info, old_manifest = dict(self.info), json.loads(json.dumps(self.manifest))
            change()
            self.write_shell()
            with self.assertRaises(ValueError):
                self.assemble()
            self.assertFalse(self.app.exists())
            self.info, self.manifest = old_info, old_manifest

    def test_metadata_refuses_tampered_source_table_framework_and_digest(self):
        candidates = [self.rewrite / 'main.patched.bin', self.guest / 'dm/main.fixups.tsv',
                      self.guest / 'Guest.framework/Guest', self.report]
        for path in candidates:
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.assemble()
            self.assertFalse(self.app.exists())
            path.write_bytes(original)
        metadata = self.guest / 'guest-framework-report.json'
        original = metadata.read_bytes()
        report = json.loads(original)
        report['source_digest_sha256'] = '00' * 32
        metadata.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.assemble()

    def test_metadata_refuses_symlink_and_unexpected_source_paths(self):
        table = self.guest / 'dm/main.fixups.tsv'
        table.unlink()
        table.symlink_to(self.rewrite / 'main.fixups.tsv')
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.app.exists())

    def test_matching_forged_framework_and_report_cannot_replace_audited_program(self):
        binary = self.guest / 'Guest.framework/Guest'
        data = binary.read_bytes() + b'forged executable content'
        binary.write_bytes(data)
        metadata = self.guest / 'guest-framework-report.json'
        report = json.loads(metadata.read_text())
        report['framework'].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        metadata.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'metadata|digest'):
            self.assemble()
        self.assertFalse(self.app.exists())

    def test_assemble_cli_uses_the_same_verified_local_flow(self):
        self.assertIsNotNone(main, 'Local iOS command-line interface is missing')
        with patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(main(['assemble', '--shell', str(self.shell), '--guest', str(self.guest),
                                   '--report', str(self.report), '--rewrite', str(self.rewrite),
                                   '--output-app', str(self.app), '--source-root', str(self.sources),
                                   '--expected-shell-sha256', hashlib.sha256(self.shell.read_bytes()).hexdigest()]), 0)
        self.assertTrue((self.app / 'botw/main.gates.tsv').is_file())

    def test_independent_shell_digest_rejects_replaced_binary_and_matching_manifest(self):
        trusted_digest = hashlib.sha256(self.shell.read_bytes()).hexdigest()
        self.executable += b'changed-binary-with-unchanged-source-hashes'
        self.manifest['executable_sha256'] = hashlib.sha256(self.executable).hexdigest()
        self.write_shell()
        with self.assertRaisesRegex(ValueError, 'shell.*SHA256|Shell.*SHA256'):
            self.assemble(expected_shell_sha256=trusted_digest)
        self.assertFalse(self.app.exists())

    def test_independent_shell_digest_is_required_and_must_be_complete(self):
        for digest in ('', None, '00', 'g' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.assemble(expected_shell_sha256=digest)
        with self.assertRaisesRegex(TypeError, 'expected_shell_sha256'):
            assemble_app(self.shell, self.guest, self.report, self.rewrite, self.app,
                         source_root=self.sources)


class IOSSigningTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(validate_profile, 'Local iOS signer is missing')
        self.cert = b'invented certificate, not DER and never used for signing'
        self.identity = hashlib.sha1(self.cert).hexdigest().upper()
        self.udid = '00000000-0000000000000001'
        self.profile = {'TeamIdentifier': ['TESTTEAM01'], 'ApplicationIdentifierPrefix': ['TESTTEAM01'],
                        'ExpirationDate': datetime.datetime(2099, 1, 1),
                        'ProvisionedDevices': [self.udid], 'DeveloperCertificates': [self.cert],
                        'Entitlements': {'application-identifier': 'TESTTEAM01.*',
                                         'com.apple.developer.team-identifier': 'TESTTEAM01',
                                         'get-task-allow': True, 'keychain-access-groups': ['TESTTEAM01.*'],
                                         'com.apple.developer.associated-domains': '*'}}

    def test_profile_matches_device_certificate_bundle_and_limits_entitlements(self):
        entitlements = validate_profile(self.profile, self.identity, self.udid)
        self.assertEqual(entitlements['application-identifier'], 'TESTTEAM01.local.botw.native')
        self.assertEqual(entitlements['keychain-access-groups'], ['TESTTEAM01.local.botw.native'])
        self.assertTrue(entitlements['get-task-allow'])
        self.assertNotIn('com.apple.developer.associated-domains', entitlements)

    def test_profile_rejects_expired_wrong_device_certificate_and_app_identifier(self):
        for key, value in [('ExpirationDate', datetime.datetime(2020, 1, 1)),
                           ('ProvisionedDevices', []), ('DeveloperCertificates', [b'other'])]:
            profile = dict(self.profile, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_profile(profile, self.identity, self.udid)
        profile = dict(self.profile, Entitlements=dict(self.profile['Entitlements'],
                                                      **{'application-identifier': 'TESTTEAM01.com.other'}))
        with self.assertRaises(ValueError):
            validate_profile(profile, self.identity, self.udid)

    def test_profile_rejects_malformed_cms_structures_with_clear_error(self):
        for profile in [[], dict(self.profile, ProvisionedDevices=self.udid),
                        dict(self.profile, DeveloperCertificates=None)]:
            with self.subTest(profile=type(profile)), self.assertRaises(ValueError):
                validate_profile(profile, self.identity, self.udid)

    def test_signing_orders_framework_app_verification_and_writes_installable_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'BOTWNative.app'
            framework = app / 'Frameworks/Guest.framework'
            framework.mkdir(parents=True)
            (framework / 'Guest').write_bytes(b'invented unsigned framework')
            (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'local.botw.native',
                                                           'CFBundleExecutable': 'BOTWNative',
                                                           'MinimumOSVersion': '26.0'}))
            (app / 'BOTWNative').write_bytes(b'invented unsigned app')
            profile = root / 'test.mobileprovision'
            profile.write_bytes(b'invented CMS')
            output = root / 'signed.ipa'
            calls = []

            def command(args, **kwargs):
                calls.append(args)
                if args[:3] == ['security', 'cms', '-D']:
                    return subprocess.CompletedProcess(args, 0, plistlib.dumps(self.profile), b'')
                if args[:2] == ['security', 'find-identity']:
                    return subprocess.CompletedProcess(args, 0, (self.identity + ' identity').encode(), b'')
                return subprocess.CompletedProcess(args, 0, b'', b'')

            with patch('botw_native.ios.subprocess.run', side_effect=command):
                report = sign_app(app, profile, self.identity, self.udid, output)
            signatures = [args for args in calls if args[0] == 'codesign']
            self.assertEqual(signatures[0][-1], str(framework))
            self.assertEqual(signatures[1][-1], str(app))
            self.assertEqual(signatures[2][1:4], ['--verify', '--deep', '--strict'])
            self.assertEqual((app / 'embedded.mobileprovision').read_bytes(), b'invented CMS')
            self.assertTrue(report['signed'])
            self.assertFalse(report['device_tested'])
            with zipfile.ZipFile(output) as archive:
                self.assertIn('Payload/BOTWNative.app/Frameworks/Guest.framework/Guest', archive.namelist())
            self.assertEqual(report['ipa_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
