"""Patched-host link-only admission; owned patch/compile fixtures never run hosts."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
TOOL = COMPONENT / 'tools/build_adapted_link_only.py'
BASE = COMPONENT / 'tools/build_link_only.py'


def record(path):
    raw = Path(path).read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


class AdaptedHost(unittest.TestCase):
    def tool(self):
        self.assertTrue(TOOL.is_file(), 'adapted link-only wrapper is absent')
        spec = importlib.util.spec_from_file_location('adapted_host_under_test', TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def base_tool(self):
        tool = self.tool()
        return tool.load_python_source('owned_baseline_under_test', BASE, record(BASE))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nxnative-owned-adapted-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_cli_requires_adapters_only_and_rejects_override_or_execution(self):
        tool = self.tool()
        good = ['--adapters-only', '--sdk', 'iphoneos', '--output', str(self.directory / 'out')]
        self.assertEqual(tool.parse_args(good).jobs, 4)
        for flags in (['--baseline-only'], ['--patch', '/owned/other.patch'], ['--run'],
                      ['--target', 'arm64-apple-ios17.0'], ['--sdk', 'iphonesimulator'], ['--jobs', '9']):
            with self.subTest(flags=flags), patch('sys.stderr'), self.assertRaises(SystemExit):
                tool.parse_args(good + flags)
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            tool.parse_args(['--output', str(self.directory / 'out')])

    def test_source_loader_ignores_valid_timestamp_pyc_and_checks_source_hash(self):
        tool = self.tool()
        source = self.directory / 'owned.py'
        source.write_text('MARKER = "cache"\n')
        py_compile.compile(str(source), doraise=True)
        old = source.stat()
        source.write_text('MARKER = "bound"\n')
        os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(tool.load_python_source('owned_bound_python', source, record(source)).MARKER, 'bound')
        with self.assertRaisesRegex(ValueError, 'hash|differs'):
            tool.load_python_source('owned_bad_hash', source, {'bytes': source.stat().st_size, 'sha256': '0' * 64})

    def test_baseline_bootstrap_rejects_real_fifo_before_reading(self):
        tool = self.tool()
        component = self.directory / 'owned-component'
        adapters = {}
        for name in ('tools/build_link_only.py',) + tool.REQUIRED_ADAPTERS:
            path = component / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('OWNED_BOOTSTRAP_CANARY = 1\n')
            adapters[name] = record(path)
        (component / 'SOURCE.lock.json').write_text(json.dumps({'adapter_files': adapters}))
        fifo = component / 'adapter/normal_policy.c'
        fifo.unlink()
        os.mkfifo(fifo)
        child = (
            'import sys, types\n'
            'from pathlib import Path\n'
            'module = types.ModuleType("owned_fifo_wrapper")\n'
            'module.__file__ = sys.argv[1]\n'
            'exec(compile(Path(sys.argv[1]).read_bytes(), sys.argv[1], "exec"), module.__dict__)\n'
            'try: module.load_baseline(Path(sys.argv[2]))\n'
            'except ValueError as error: print(str(error)); sys.exit(0)\n'
            'sys.exit(9)\n'
        )
        try:
            result = subprocess.run([sys.executable, '-c', child, str(TOOL), str(component)],
                                    capture_output=True, text=True, timeout=0.8)
        except subprocess.TimeoutExpired:
            self.fail('FIFO admission blocked waiting for a writer instead of rejecting before read')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('regular', result.stdout)

    def test_fixed_patch_inventory_is_exact_and_disjoint(self):
        tool = self.tool()
        base = self.base_tool()
        paths = set()
        for name, allowed in tool.PATCHES:
            touched = tool.patch_members(COMPONENT / name)
            self.assertEqual(touched, set(allowed))
            self.assertFalse(paths & touched)
            paths |= touched
        self.assertEqual(len(tool.PATCHES), 4)
        self.assertEqual(len(paths), 11)
        lock = base.read_json(COMPONENT / 'SOURCE.lock.json')
        self.assertTrue(paths <= set(lock['upstream_files']))

    def owned_patch(self, filename='owned.txt', old='before', new='after'):
        path = self.directory / ('change-' + filename.replace('/', '-') + '.patch')
        path.write_text('--- a/' + filename + '\n+++ b/' + filename + '\n@@ -1 +1 @@\n-' + old + '\n+' + new + '\n')
        return path

    def test_real_system_patch_changes_only_declared_owned_member(self):
        tool = self.tool()
        base = self.base_tool()
        inputs = self.directory / 'inputs'
        inputs.mkdir()
        (inputs / 'owned.txt').write_text('before\n')
        (inputs / 'keep.txt').write_text('untouched\n')
        before = tool.private_snapshot(inputs, base)
        receipt = tool.new_receipt(base)
        result = tool.apply_patch(inputs, self.owned_patch(), {'owned.txt'}, self.directory, 'owned-patch', base, receipt)
        self.assertEqual(result['changed_members'], ['owned.txt'])
        self.assertEqual((inputs / 'owned.txt').read_text(), 'after\n')
        self.assertEqual(before['keep.txt'], tool.private_snapshot(inputs, base)['keep.txt'])
        self.assertEqual(receipt['steps'][0]['exit_code'], 0)
        self.assertIn('--fuzz=0', receipt['steps'][0]['argv'])

    def test_ordered_overlapping_patches_bind_each_predecessor_and_final_copy(self):
        tool = self.tool()
        base = self.base_tool()
        component = self.directory / 'repository/experiments/component'
        source = component / 'upstream/owned.txt'
        source.parent.mkdir(parents=True)
        source.write_text('before\n')
        patches = component / 'patches'; patches.mkdir()
        for label, old, new in (('one', 'before', 'middle'), ('two', 'middle', 'after')):
            (patches / (label + '.patch')).write_text(
                '--- a/owned.txt\n+++ b/owned.txt\n@@ -1 +1 @@\n-' + old + '\n+' + new + '\n')
        inventory = (('patches/one.patch', ('owned.txt',)), ('patches/two.patch', ('owned.txt',)))
        lock = {'upstream_files': {'owned.txt': record(source)}}
        output = self.directory / 'ordered'; output.mkdir()
        receipt = tool.new_receipt(base)
        with patch.object(tool, 'PATCHES', inventory), patch.object(tool, 'COPIED_ADAPTERS', ()), patch.object(tool, 'ABI_FILES', ()):
            private, snapshot = tool.copy_private_inputs(component, output, lock, base, receipt)
        self.assertEqual((private / 'upstream/owned.txt').read_text(), 'after\n')
        self.assertEqual(source.read_text(), 'before\n')
        self.assertEqual(receipt['patches'][1]['preimages']['owned.txt'], receipt['patches'][0]['postimages']['owned.txt'])
        self.assertEqual(snapshot['upstream/owned.txt'], record(private / 'upstream/owned.txt'))

    def test_ordered_chain_refuses_changed_predecessor_before_second_patch(self):
        tool = self.tool()
        base = self.base_tool()
        inputs = self.directory / 'chain'; inputs.mkdir()
        member = inputs / 'owned.txt'; member.write_text('middle\n')
        expected = tool.private_snapshot(inputs, base)
        member.write_text('injected\n')
        receipt = tool.new_receipt(base)
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            tool.apply_patch(inputs, self.owned_patch(old='injected', new='after'), {'owned.txt'},
                             self.directory, 'must-not-run', base, receipt, expected_preimages=expected)
        self.assertEqual(member.read_text(), 'injected\n')
        self.assertEqual(receipt['steps'], [])

    def test_patch_paths_traversal_and_undeclared_member_are_rejected(self):
        tool = self.tool()
        base = self.base_tool()
        inputs = self.directory / 'inputs'
        inputs.mkdir()
        (inputs / 'owned.txt').write_text('before\n')
        for name in ('../outside', '/absolute', 'other.txt'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tool.apply_patch(inputs, self.owned_patch(name), {'owned.txt'}, self.directory,
                                 'denied', base, tool.new_receipt(base))
        self.assertEqual((inputs / 'owned.txt').read_text(), 'before\n')

    def test_patch_failure_preserves_exit_and_never_claims_compile_success(self):
        tool = self.tool()
        base = self.base_tool()
        inputs = self.directory / 'inputs'
        inputs.mkdir()
        (inputs / 'owned.txt').write_text('wrong context\n')
        receipt = tool.new_receipt(base)
        with self.assertRaisesRegex(ValueError, 'patch failed'):
            tool.apply_patch(inputs, self.owned_patch(), {'owned.txt'}, self.directory,
                             'failed-patch', base, receipt)
        self.assertNotEqual(receipt['steps'][0]['exit_code'], 0)
        self.assertIs(receipt['compile_link_succeeded'], False)

    def test_private_closure_rejects_changed_extra_or_symlinked_inputs(self):
        tool = self.tool()
        base = self.base_tool()
        inputs = self.directory / 'inputs'
        inputs.mkdir()
        member = inputs / 'owned.txt'
        member.write_text('owned\n')
        before = tool.private_snapshot(inputs, base)
        tool.verify_private(inputs, before, base)
        member.write_text('changed\n')
        with self.assertRaises(ValueError): tool.verify_private(inputs, before, base)
        member.write_text('owned\n')
        extra = inputs / 'extra.txt'
        extra.write_text('unlisted\n')
        with self.assertRaises(ValueError): tool.verify_private(inputs, before, base)
        extra.unlink()
        member.unlink()
        member.symlink_to(BASE)
        with self.assertRaises(ValueError): tool.private_snapshot(inputs, base)

    def fixture_lock(self, base, tool):
        # Direct helper tests bind actual inputs locally; this is not production admission.
        lock = base.read_json(COMPONENT / 'SOURCE.lock.json')
        for name in tool.REQUIRED_ADAPTERS:
            lock['adapter_files'][name] = record(COMPONENT / name)
        for name in tool.ABI_FILES:
            lock.setdefault('external_code_files', {})[name] = record(ROOT / name)
        return lock

    def test_actual_patches_copy_full_original_tree_and_preserve_vendor(self):
        tool = self.tool()
        base = self.base_tool()
        lock = self.fixture_lock(base, tool)
        vendor_before = base.snapshot_files(COMPONENT / 'upstream', lock['upstream_files'])
        receipt = tool.new_receipt(base)
        private, snapshot = tool.copy_private_inputs(COMPONENT, self.directory, lock, base, receipt)
        self.assertEqual(len(snapshot), 961)
        changed = set()
        for name, original in lock['upstream_files'].items():
            if snapshot['upstream/' + name] != {k: original[k] for k in ('bytes', 'sha256')}:
                changed.add(name)
        self.assertEqual(changed, set().union(*(set(allowed) for _, allowed in tool.PATCHES)))
        self.assertEqual(vendor_before, base.snapshot_files(COMPONENT / 'upstream', lock['upstream_files']))
        tool.verify_private(private, snapshot, base)

    def test_commands_compile_130_actual_units_with_macro_and_private_includes(self):
        tool = self.tool()
        base = self.base_tool()
        manifest = base.read_json(COMPONENT / 'compile_sources.json')
        private = self.directory / 'inputs'
        sdk = {'clang': '/owned/clang', 'sdk_path': '/owned/iPhoneOS26.5.sdk'}
        commands = tool.compile_commands(private, self.directory, manifest['translation_units'], sdk, base)
        self.assertEqual(len(commands), 130)
        self.assertEqual(sum(row['role'] == 'vpx_decoder' for row in commands), 71)
        self.assertEqual(len({row['object'] for row in commands}), 130)
        for row in commands:
            self.assertTrue(Path(row['source']).is_relative_to(private))
            self.assertIn('-DNXNATIVE_NORMAL_POLICY=1', row['argv'])
            self.assertIn('-DDM_SHELL=1', row['argv'])
            self.assertIn(str(private / 'adapter'), row['argv'])
            self.assertIn(str(private / 'external_abi'), row['argv'])
            self.assertIn('arm64-apple-ios26.0', row['argv'])
            self.assertIn('-march=armv8.2-a', row['argv'])
        roles = {row['role'] for row in commands}
        self.assertTrue({'owned_normal_policy', 'owned_normal_failures', 'reused_base_v0_abi', 'owned_link_only_entry'} <= roles)
        self.assertTrue({'_nxnative_normal_fail', '_nxnative_normal_signal_failure'} <= tool.STRONG_SYMBOLS)

    def test_actual_adapters_compile_to_objects_without_execution(self):
        tool = self.tool()
        base = self.base_tool()
        clang = shutil.which('clang')
        if not clang:
            self.skipTest('clang is unavailable for compile-only CPU fixture')
        lock = self.fixture_lock(base, tool)
        private, _ = tool.copy_private_inputs(COMPONENT, self.directory, lock, base, tool.new_receipt(base))
        for name in ('adapter/normal_policy.c', 'adapter/normal_failures.c', 'external_abi/base_v0_abi.c'):
            obj = self.directory / (Path(name).stem + '.o')
            argv = [clang, '--no-default-config', '-std=gnu11', '-DNXNATIVE_NORMAL_POLICY=1',
                    '-I', str(private / 'adapter'), '-I', str(private / 'external_abi'),
                    '-I', str(private / 'upstream/dm/gfx/nvn/include'), '-c', str(private / name), '-o', str(obj)]
            result = subprocess.run(argv, env=base.command_environment(), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(obj.stat().st_size, 0)

    def test_receipt_declares_partial_policy_and_original_metadata_only(self):
        tool = self.tool()
        receipt = tool.new_receipt(self.base_tool())
        self.assertEqual(receipt['baseline'], 'patched-host-original-1.9-metadata-link-only')
        for name in ('base_v0_configured', 'installable', 'host_guest_gpu_executed', 'whole_host_policy_closed'):
            self.assertIs(receipt[name], False)
        self.assertEqual(receipt['policy_scope_pending'], ['GPU', 'thread', 'memstat'])

    def test_missing_source_admission_writes_failed_receipt_without_compiler(self):
        tool = self.tool()
        component = self.directory / 'component'
        component.mkdir()
        output = self.directory / 'failed-output'
        args = tool.parse_args(['--adapters-only', '--output', str(output)])
        receipt = tool.build(args, component)
        self.assertEqual(receipt['status'], 'failed')
        self.assertIs(receipt['compile_link_succeeded'], False)
        self.assertIs(receipt['host_guest_gpu_executed'], False)
        self.assertEqual(receipt['compiles'], [])
        saved = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(saved, receipt)


if __name__ == '__main__':
    unittest.main()
