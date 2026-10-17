"""Source admission uses an independent Git tree and owned files only."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'experiments/nxnative_base_v0/tools/verify_source.py'


class NxnativeSource(unittest.TestCase):
    def tool(self):
        self.assertTrue(TOOL.is_file(), 'source admission implementation is absent')
        spec = importlib.util.spec_from_file_location('nxnative_source_under_test', TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nxnative-owned-source-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        oracle = self.base / 'oracle'
        oracle.mkdir()
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
        self.git(oracle, 'init', '-q')
        (oracle / 'a').mkdir()
        (oracle / 'a/x.txt').write_bytes(b'owned nested text\n')
        (oracle / 'a.b').write_bytes(b'owned\0binary')
        (oracle / 'z.sh').write_text('# owned fixture only\n')
        (oracle / 'z.sh').chmod(0o755)
        self.git(oracle, 'add', '--', 'a', 'a.b', 'z.sh')
        self.expected_tree = self.git(oracle, 'write-tree').strip()
        self.source = self.base / 'source'
        shutil.copytree(oracle, self.source, ignore=shutil.ignore_patterns('.git'))
        self.rows = {}
        for name in ('a/x.txt', 'a.b', 'z.sh'):
            raw = (self.source / name).read_bytes()
            self.rows[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                               'git_blob_sha1': self.git(oracle, 'hash-object', '--', name).strip(),
                               'git_mode': '100755' if name == 'z.sh' else '100644'}

    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args],
                                       env=self.env, stderr=subprocess.DEVNULL, text=True)

    def test_tree_matches_git_with_directory_order_binary_and_executable(self):
        self.assertEqual(self.tool().verify_tree(self.source, self.rows, self.expected_tree),
                         self.expected_tree)

    def test_self_consistent_file_and_digest_change_does_not_change_pinned_tree(self):
        raw = b'changed owned file'
        (self.source / 'a.b').write_bytes(raw)
        self.rows['a.b'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
            git_blob_sha1=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())
        with self.assertRaisesRegex(ValueError, 'tree'):
            self.tool().verify_tree(self.source, self.rows, self.expected_tree)

    def test_git_executable_mode_uses_owner_execute_bit(self):
        tool = self.tool()
        (self.source / 'z.sh').chmod(0o645)
        oracle = self.base / 'oracle'
        (oracle / 'z.sh').chmod(0o645)
        self.git(oracle, 'add', '--', 'z.sh')
        self.assertNotEqual(self.git(oracle, 'write-tree').strip(), self.expected_tree)
        with self.assertRaisesRegex(ValueError, 'mode'):
            tool.verify_tree(self.source, self.rows, self.expected_tree)

    def test_invalid_size_is_rejected_before_reading_that_member(self):
        tool = self.tool()
        member = self.source / 'a.b'
        self.rows['a.b']['bytes'] = 64 * 1024**2 + 1
        original = Path.read_bytes

        def guarded(path):
            self.assertNotEqual(path, member, 'invalid size must be rejected before reading')
            return original(path)

        with patch.object(Path, 'read_bytes', guarded), self.assertRaises(ValueError):
            tool.verify_tree(self.source, self.rows, self.expected_tree)

    def test_missing_extra_digest_and_mode_are_rejected(self):
        tool = self.tool()
        (self.source / 'extra').write_text('owned')
        with self.assertRaises(ValueError): tool.verify_tree(self.source, self.rows, self.expected_tree)
        (self.source / 'extra').unlink()
        (self.source / 'z.sh').chmod(0o644)
        with self.assertRaises(ValueError): tool.verify_tree(self.source, self.rows, self.expected_tree)
        (self.source / 'z.sh').chmod(0o755)
        self.rows['a.b']['sha256'] = '0' * 64
        with self.assertRaises(ValueError): tool.verify_tree(self.source, self.rows, self.expected_tree)
        (self.source / 'a.b').unlink()
        with self.assertRaises(ValueError): tool.verify_tree(self.source, self.rows, self.expected_tree)

    def test_symlink_and_noncanonical_paths_are_rejected(self):
        tool = self.tool()
        target = self.base / 'owned-other'
        target.write_text('OWNED_SOURCE_CANARY')
        (self.source / 'a.b').unlink()
        (self.source / 'a.b').symlink_to(target)
        with self.assertRaises(ValueError): tool.verify_tree(self.source, self.rows, self.expected_tree)
        for bad in ('../owned-other', '/absolute', 'a/../a.b', './a.b'):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                tool.verify_tree(self.source, {bad: self.rows['a.b']}, self.expected_tree)

    def test_duplicate_lock_keys_and_unexpected_upstream_identity_are_rejected(self):
        tool = self.tool()
        lock = self.base / 'lock.json'
        lock.write_text('{"upstream_commit":"one","upstream_commit":"two"}')
        with self.assertRaises(ValueError): tool.read_lock(lock)
        lock.write_text(json.dumps({'schema': 'nxnative-base-v0-source-v1',
                                  'upstream_commit': '0' * 40,
                                  'upstream_tree': self.expected_tree}))
        with self.assertRaises(ValueError): tool.read_lock(lock)

    def external_component(self):
        repository = self.base / 'repository'
        component = repository / 'experiments' / 'nxnative_base_v0'
        component.mkdir(parents=True)
        original = TOOL.parents[1]
        shutil.copytree(original / 'upstream', component / 'upstream')
        lock = json.loads((original / 'SOURCE.lock.json').read_bytes())
        lock['adapter_files'] = {}
        code = b'raise RuntimeError("owned dependency must never execute")\n'
        name = 'scripts/owned_dependency.py'
        member = repository / name
        member.parent.mkdir()
        member.write_bytes(code)
        lock['external_code_files'] = {
            name: {'bytes': len(code), 'sha256': hashlib.sha256(code).hexdigest()}}
        return repository, component, lock, name, member, code

    def test_external_repository_code_is_verified_without_executing_it(self):
        _, component, lock, _, _, _ = self.external_component()
        (component / 'SOURCE.lock.json').write_text(json.dumps(lock))
        report = self.tool().verify_component(component)
        self.assertEqual(report.get('external_code_files'), 1)
        self.assertFalse(report['upstream_executed'])

    def test_external_code_changes_paths_and_symlinks_are_rejected(self):
        repository, component, lock, name, member, code = self.external_component()
        tool = self.tool()

        def reject(records):
            lock['external_code_files'] = records
            (component / 'SOURCE.lock.json').write_text(json.dumps(lock))
            with self.assertRaises(ValueError): tool.verify_component(component)

        row = lock['external_code_files'][name]
        member.write_bytes(code.replace(b'owned', b'OWNED'))
        self.assertEqual(member.stat().st_size, row['bytes'])
        reject({name: row})
        member.write_bytes(code)
        for bad in ('/absolute.py', '../outside.py', './scripts/owned_dependency.py',
                    'scripts//owned_dependency.py',
                    'experiments/nxnative_base_v0/SOURCE.lock.json'):
            with self.subTest(path=bad): reject({bad: row})
        for size in (True, -1, 64 * 1024**2 + 1, len(code) + 1):
            with self.subTest(size=size): reject({name: {**row, 'bytes': size}})
        target = self.base / 'owned-external-target.py'
        target.write_bytes(code)
        member.unlink()
        member.symlink_to(target)
        reject({name: row})
        member.unlink()
        member.write_bytes(code)
        scripts = repository / 'scripts'
        external_scripts = self.base / 'owned-scripts'
        scripts.rename(external_scripts)
        scripts.symlink_to(external_scripts, target_is_directory=True)
        reject({name: row})


if __name__ == '__main__':
    unittest.main()
