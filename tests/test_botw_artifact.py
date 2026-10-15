"""Trusted GitHub metadata and artificial ZIPs; no game bytes or real network."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

try:
    from scripts.botw_native.artifact import fetch_shell
except ImportError:
    fetch_shell = None

COMMIT = 'a' * 40
REPOSITORY = 'owner/repo'


def archive(entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, data, mode in entries or [('BOTWNative-shell.ipa', b'artificial-host-ipa', 0o100644)]:
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(entry, data)
    return stream.getvalue()


class ArtifactDownloadTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(fetch_shell, 'Verified shell artifact downloader is missing')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / 'host'
        self.binary = archive()
        self.run = {'id': 42, 'status': 'completed', 'conclusion': 'success',
                    'head_sha': COMMIT, 'head_branch': 'codex/botw-native-preflight',
                    'repository': {'id': 10, 'full_name': REPOSITORY},
                    'head_repository': {'id': 10, 'full_name': REPOSITORY}}
        self.artifact = {'id': 73, 'name': 'BOTWNative-generic-ios-host', 'expired': False,
                         'size_in_bytes': len(self.binary),
                         'digest': 'sha256:' + hashlib.sha256(self.binary).hexdigest(),
                         'workflow_run': {'id': 42, 'repository_id': 10, 'head_repository_id': 10,
                                          'head_branch': self.run['head_branch'], 'head_sha': COMMIT}}
        self.artifacts = {'total_count': 1, 'artifacts': [self.artifact]}
        self.calls = []

    def fake_gh(self, arguments, **kwargs):
        self.calls.append(arguments)
        self.assertEqual(arguments[:4], ['gh', 'api', '--method', 'GET'])
        self.assertNotIn('shell', kwargs)
        endpoint = arguments[4]
        if endpoint == 'repos/owner/repo/actions/runs/42':
            data = json.dumps(self.run).encode()
        elif endpoint == 'repos/owner/repo/actions/runs/42/artifacts?per_page=100&page=1':
            data = json.dumps(self.artifacts).encode()
        elif endpoint == 'repos/owner/repo/actions/artifacts/73/zip':
            data = self.binary
        else:
            self.fail('Unexpected endpoint: ' + endpoint)
        return subprocess.CompletedProcess(arguments, 0, data, b'')

    def fetch(self):
        with patch('scripts.botw_native.artifact.subprocess.run', side_effect=self.fake_gh):
            return fetch_shell(REPOSITORY, 42, COMMIT, self.output)

    def test_validates_official_digest_and_publishes_ipa_with_provenance(self):
        result = self.fetch()
        ipa = self.output / 'BOTWNative-shell.ipa'
        self.assertEqual(ipa.read_bytes(), b'artificial-host-ipa')
        self.assertEqual(result['path'], str(ipa.resolve()))
        self.assertEqual(result['shell_sha256'], hashlib.sha256(ipa.read_bytes()).hexdigest())
        self.assertEqual(result['outer_zip_sha256'], self.artifact['digest'][7:])
        self.assertEqual(result['outer_zip_bytes'], len(self.binary))
        self.assertEqual(result['artifact_id'], 73)
        self.assertEqual(json.loads((self.output / 'shell-provenance.json').read_text()), result)
        self.assertEqual(len(self.calls), 3)

    def test_run_must_match_success_commit_and_repository_before_downloading(self):
        original = copy.deepcopy(self.run)
        mutations = [lambda d: d.update(status='in_progress'),
                     lambda d: d.update(conclusion='failure'),
                     lambda d: d.update(id=43),
                     lambda d: d.update(head_sha='b' * 40),
                     lambda d: d.update(head_branch=''),
                     lambda d: d['repository'].update(full_name='other/repo'),
                     lambda d: d['head_repository'].update(full_name='attacker/repo')]
        for change in mutations:
            self.run = copy.deepcopy(original)
            change(self.run)
            self.calls.clear()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.fetch()
            self.assertFalse(self.output.exists())
            self.assertEqual(len(self.calls), 1)

    def test_unique_unexpired_artifact_with_sha256_and_bounded_size_is_required(self):
        original = copy.deepcopy(self.artifact)
        mutations = [lambda a: a.update(expired=True), lambda a: a.pop('digest'),
                     lambda a: a.update(digest='md5:' + 'a' * 32),
                     lambda a: a.update(size_in_bytes=0),
                     lambda a: a.update(size_in_bytes=256 * 1024**2 + 1),
                     lambda a: a.update(name='other-name'),
                     lambda a: a['workflow_run'].update(id=43),
                     lambda a: a['workflow_run'].update(repository_id=11),
                     lambda a: a['workflow_run'].update(head_repository_id=11),
                     lambda a: a['workflow_run'].update(head_branch='main'),
                     lambda a: a['workflow_run'].update(head_sha='b' * 40)]
        for change in mutations:
            item = copy.deepcopy(original)
            change(item)
            self.artifacts = {'total_count': 1, 'artifacts': [item]}
            self.calls.clear()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.fetch()
            self.assertFalse(self.output.exists())
            self.assertEqual(len(self.calls), 2)
        self.artifacts = {'total_count': 2, 'artifacts': [original, copy.deepcopy(original)]}
        with self.assertRaisesRegex(ValueError, 'unique|exactly one'):
            self.fetch()

    def test_outer_zip_digest_and_size_mismatch_are_fatal(self):
        original = self.binary
        for bad in (original + b'extra', original[:-1], bytes([original[0] ^ 1]) + original[1:]):
            self.binary = bad
            with self.subTest(length=len(bad)), self.assertRaisesRegex(ValueError, 'digest|SHA256|size'):
                self.fetch()
            self.assertFalse(self.output.exists())

    def test_rejects_path_traversal_symlink_directory_extra_file_and_duplicate(self):
        entries = [[('../BOTWNative-shell.ipa', b'bad', 0o100644)],
                   [('/BOTWNative-shell.ipa', b'bad', 0o100644)],
                   [('BOTWNative-shell.ipa', b'target', 0o120777)],
                   [('BOTWNative-shell.ipa/', b'', 0o40755)],
                   [('BOTWNative-shell.ipa', b'ipa', 0o100644), ('other', b'bad', 0o100644)],
                   [('BOTWNative-shell.ipa', b'ipa', 0o100644), ('BOTWNative-shell.ipa', b'duplicate', 0o100644)]]
        for files in entries:
            with self.subTest(files=files):
                with __import__('warnings').catch_warnings():
                    __import__('warnings').simplefilter('ignore', UserWarning)
                    self.binary = archive(files)
                self.artifact.update(size_in_bytes=len(self.binary), digest='sha256:' + hashlib.sha256(self.binary).hexdigest())
                with self.assertRaises(ValueError):
                    self.fetch()
                self.assertFalse(self.output.exists())

    def test_official_digest_does_not_make_invalid_zip_or_oversized_inner_file_valid(self):
        for data in (b'not a ZIP', archive([('BOTWNative-shell.ipa', b'', 0o100644)])):
            self.binary = data
            self.artifact.update(size_in_bytes=len(data), digest='sha256:' + hashlib.sha256(data).hexdigest())
            with self.assertRaises(ValueError):
                self.fetch()
        # Alter just the central directory uncompressed length; no huge allocation.
        data = bytearray(archive())
        start = data.index(b'PK\x01\x02')
        __import__('struct').pack_into('<I', data, start + 24, 256 * 1024**2 + 1)
        self.binary = bytes(data)
        self.artifact.update(size_in_bytes=len(data), digest='sha256:' + hashlib.sha256(data).hexdigest())
        with self.assertRaises(ValueError):
            self.fetch()
        self.assertFalse(self.output.exists())

    def test_invalid_arguments_do_not_call_gh_or_overwrite_existing_output(self):
        with patch('scripts.botw_native.artifact.subprocess.run') as gh:
            for repo, run, commit in [('bad', 42, COMMIT), ('owner/repo;touch', 42, COMMIT),
                                      (REPOSITORY, -1, COMMIT), (REPOSITORY, 42, 'abc'),
                                      (REPOSITORY, 42, 'g' * 40)]:
                with self.subTest(repo=repo, run=run, commit=commit), self.assertRaises(ValueError):
                    fetch_shell(repo, run, commit, self.output)
            gh.assert_not_called()
        self.output.mkdir()
        (self.output / 'keep').write_text('original')
        with self.assertRaises(FileExistsError):
            self.fetch()
        self.assertEqual((self.output / 'keep').read_text(), 'original')

    def test_api_failure_does_not_expose_stderr_or_publish_partial_output(self):
        secret = b'https://signed-download.example/?secret=never-echo'
        with patch('scripts.botw_native.artifact.subprocess.run',
                   return_value=subprocess.CompletedProcess(['gh'], 1, b'', secret)):
            with self.assertRaises(RuntimeError) as error:
                fetch_shell(REPOSITORY, 42, COMMIT, self.output)
        self.assertNotIn('secret', str(error.exception))
        self.assertFalse(self.output.exists())

    def test_failed_output_write_cleans_staging_directory(self):
        real = Path.write_bytes

        def fail_ipa(path, data):
            if path.name == 'BOTWNative-shell.ipa':
                raise OSError('storage failed')
            return real(path, data)

        with patch.object(Path, 'write_bytes', fail_ipa), self.assertRaisesRegex(OSError, 'storage failed'):
            self.fetch()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
