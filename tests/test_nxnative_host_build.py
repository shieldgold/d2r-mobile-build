"""Owned fixtures exercise the link-only planner; no host binary is executed."""
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
TOOL = COMPONENT / 'tools/build_link_only.py'


class NxnativeHostBuild(unittest.TestCase):
    def tool(self):
        self.assertTrue(TOOL.is_file(), 'owned link-only builder is absent')
        spec = importlib.util.spec_from_file_location('nxnative_host_build_under_test', TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nxnative-owned-link-only-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'owned source'
        self.source.mkdir()
        (self.source / 'owned.h').write_text('#define OWNED_VALUE 7\n')
        (self.source / 'owned.c').write_text('#include "owned.h"\nint owned(void) { return OWNED_VALUE; }\n')
        self.rows = {name: self.record(self.source / name) for name in ('owned.c', 'owned.h')}

    @staticmethod
    def record(path):
        raw = path.read_bytes()
        return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

    def test_owned_inventory_binds_input_bytes(self):
        tool = self.tool()
        self.assertEqual(tool.snapshot_files(self.source, self.rows), self.rows)
        (self.source / 'owned.h').write_text('#define OWNED_VALUE 8\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            tool.snapshot_files(self.source, self.rows)

    def test_owned_inventory_rejects_missing_traversal_and_symlink(self):
        tool = self.tool()
        with self.assertRaises(ValueError):
            tool.snapshot_files(self.source, {'../outside': self.rows['owned.c']})
        (self.source / 'owned.c').unlink()
        with self.assertRaises(ValueError):
            tool.snapshot_files(self.source, self.rows)
        (self.source / 'owned.c').symlink_to(self.source / 'owned.h')
        with self.assertRaises(ValueError):
            tool.snapshot_files(self.source, self.rows)

    def test_cli_requires_baseline_and_rejects_unreviewed_target_or_flags(self):
        tool = self.tool()
        good = ['--baseline-only', '--sdk', 'iphoneos', '--output', str(self.base / 'out')]
        self.assertEqual(tool.parse_args(good).jobs, 4)
        for extra in (['--target', 'arm64-apple-ios17.0'], ['--cflags=-mcpu=apple-a18'],
                      ['--run'], ['--install'], ['--jobs', '9'], ['--sdk', 'iphonesimulator']):
            with self.subTest(extra=extra), patch('sys.stderr'), self.assertRaises(SystemExit):
                tool.parse_args(good + extra)
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            tool.parse_args(['--output', str(self.base / 'out')])

    def test_output_must_be_new_and_outside_source_component(self):
        tool = self.tool()
        new = self.base / 'new output'
        tool.prepare_output(new, self.source)
        self.assertTrue(new.is_dir())
        with self.assertRaisesRegex(ValueError, 'empty|new'):
            tool.prepare_output(new, self.source)
        with self.assertRaises(ValueError):
            tool.prepare_output(self.source / 'out', self.source)
        alias = self.base / 'output-alias'
        alias.symlink_to(self.base / 'not-created', target_is_directory=True)
        with self.assertRaises(ValueError):
            tool.build(tool.parse_args(['--baseline-only', '--output', str(alias)]), self.source)

    def test_unfrozen_or_changed_verifier_fails_before_import(self):
        tool = self.tool()
        fixture = self.base / 'repo/experiments/component'
        fixture.mkdir(parents=True)
        lock = {'schema': 'nxnative-base-v0-source-v1', 'upstream_commit': tool.COMMIT,
                'upstream_repository': 'https://github.com/seedds/nxnative',
                'upstream_tree': tool.TREE, 'adapter_files': {}}
        (fixture / 'SOURCE.lock.json').write_text(json.dumps(lock))
        with patch.object(tool, 'load_module') as importer, self.assertRaisesRegex(ValueError, 'frozen'):
            tool.source_gate(fixture)
        importer.assert_not_called()
        for name in tool.REQUIRED_ADAPTERS:
            path = fixture / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('OWNED_VERIFIER_CANARY')
            lock['adapter_files'][name] = self.record(path)
        (fixture / 'SOURCE.lock.json').write_text(json.dumps(lock))
        (fixture / 'tools/verify_source.py').write_text('changed before import')
        with patch.object(tool, 'load_module') as importer, self.assertRaises(ValueError):
            tool.source_gate(fixture)
        importer.assert_not_called()

    def test_duplicate_json_keys_are_not_accepted(self):
        tool = self.tool()
        path = self.base / 'inventory.json'
        path.write_text('{"units":[],"units":["changed"]}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            tool.read_json(path)

    def test_verified_source_import_does_not_execute_unlocked_cached_bytecode(self):
        tool = self.tool()
        path = self.source / 'owned_module.py'
        cached = b"MARKER = 'OWNED_BYTECODE'\n"
        verified = b"MARKER = 'OWNED_SRCBYTES'\n"
        self.assertEqual(len(cached), len(verified))
        path.write_bytes(cached)
        before = path.stat()
        py_compile.compile(str(path), doraise=True)
        path.write_bytes(verified)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        expected = {'owned_module.py': self.record(path)}
        self.assertEqual(tool.snapshot_files(self.source, expected), expected)
        loaded = tool.load_module('owned_verified_source_module', path)
        self.assertEqual(loaded.MARKER, 'OWNED_SRCBYTES')

    def test_full_manifest_is_original_126_units_with_71_vpx(self):
        tool = self.tool()
        manifest = tool.read_json(COMPONENT / 'compile_sources.json')
        lock = tool.read_json(COMPONENT / 'SOURCE.lock.json')
        units = tool.validate_inventory(manifest, lock)
        self.assertEqual(len(units), 126)
        self.assertEqual(sum(u['role'] == 'vpx_decoder' for u in units), 71)
        self.assertEqual(len({u['path'] for u in units}), len(units))
        self.assertEqual({u['path']: u['defines'] for u in units if u['defines']}, {
            'dm/host/main.c': ['main=dm_main'],
            'dm/ios/Sources/main.m': ['main=nxnative_upstream_ios_main']})
        changed = json.loads(json.dumps(manifest))
        changed['translation_units'].pop()
        with self.assertRaisesRegex(ValueError, 'inventory|126'):
            tool.validate_inventory(changed, lock)

    def test_manifest_rejects_duplicate_extra_source_and_wrong_digest(self):
        tool = self.tool()
        manifest = tool.read_json(COMPONENT / 'compile_sources.json')
        lock = tool.read_json(COMPONENT / 'SOURCE.lock.json')
        for mutation in ('duplicate', 'digest', 'define'):
            changed = json.loads(json.dumps(manifest))
            if mutation == 'duplicate': changed['translation_units'][1] = changed['translation_units'][0]
            if mutation == 'digest': changed['translation_units'][0]['sha256'] = '0' * 64
            if mutation == 'define': changed['translation_units'][0]['defines'] = ['main=arbitrary']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                tool.validate_inventory(changed, lock)

    def test_compile_commands_target_a17_lse_and_record_actual_dependencies(self):
        tool = self.tool()
        manifest = tool.read_json(COMPONENT / 'compile_sources.json')
        sdk = {'clang': '/owned/Xcode/clang', 'sdk_path': '/owned/iPhoneOS26.5.sdk'}
        commands = tool.compile_commands(COMPONENT, self.base / 'out', manifest['translation_units'], sdk)
        self.assertEqual(len(commands), 127)
        for row in commands:
            argv = row['argv']
            self.assertIn('arm64-apple-ios26.0', argv)
            self.assertIn('-march=armv8.2-a', argv)
            self.assertIn('-MMD', argv)
            self.assertIn('-MF', argv)
            self.assertNotIn('-mcpu=apple-a18', argv)
            self.assertIn('-fno-modules', argv)
            self.assertIn('--no-default-config', argv)
        self.assertEqual(commands[-1]['source'], str(COMPONENT / 'adapter/link_only_main.c'))
        self.assertIn('-Dmain=dm_main', commands[0]['argv'])
        vpx = next(r for r in commands if '/libvpx/' in r['source'])
        self.assertIn(str(COMPONENT / 'upstream/dm/third_party/libvpx/config'), vpx['argv'])
        self.assertIn('-O2', vpx['argv'])
        self.assertNotIn('-fobjc-arc', vpx['argv'])

    def test_link_command_strongly_resolves_all_objects(self):
        tool = self.tool()
        sdk = {'clang': '/owned/Xcode/clang', 'sdk_path': '/owned/iPhoneOS26.5.sdk'}
        objects = [self.base / 'owned one.o', self.base / 'owned-two.o']
        argv, artifact = tool.link_command(self.base, objects, sdk)
        self.assertTrue('LINK-ONLY-DO-NOT-RUN' in artifact.name)
        self.assertIn('-Wl,-undefined,error', argv)
        self.assertIn('-Wl,-e,_main', argv)
        self.assertTrue(all(str(p) in argv for p in objects))
        self.assertNotIn('dynamic_lookup', ' '.join(argv))
        for framework in ('CoreText', 'CoreFoundation', 'CoreGraphics'):
            self.assertIn(framework, argv, 'disabled modules require explicit framework links')

    def test_depfile_handles_spaces_continuations_and_rejects_empty(self):
        tool = self.tool()
        depfile = self.base / 'owned.d'
        src = str(self.source / 'owned.c').replace(' ', '\\ ')
        header = str(self.source / 'owned.h').replace(' ', '\\ ')
        depfile.write_text('owned.o: ' + src + ' \\\n ' + header + '\n')
        self.assertEqual(tool.parse_depfile(depfile), [self.source / 'owned.c', self.source / 'owned.h'])
        depfile.write_text('owned.o:\n')
        with self.assertRaises(ValueError): tool.parse_depfile(depfile)

    def test_dependency_closure_rejects_unknown_and_changed_inputs(self):
        tool = self.tool()
        known = {str((self.source / n).resolve()): row for n, row in self.rows.items()}
        deps = [self.source / 'owned.c', self.source / 'owned.h']
        result = tool.verify_dependencies(deps, known, self.base / 'sdk', self.base / 'toolchain')
        self.assertEqual(len(result['bound_inputs']), 2)
        unknown = self.base / 'unknown.h'
        unknown.write_text('OWNED_EXTERNAL_CANARY')
        with self.assertRaisesRegex(ValueError, 'unlocked'):
            tool.verify_dependencies(deps + [unknown], known, self.base / 'sdk', self.base / 'toolchain')
        (self.source / 'owned.h').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed|differs'):
            tool.verify_dependencies(deps, known, self.base / 'sdk', self.base / 'toolchain')

    def test_real_owned_c_compile_depfile_binds_transitive_header(self):
        tool = self.tool()
        clang = shutil.which('clang')
        if not clang:
            self.skipTest('clang is unavailable for owned compile-only fixture')
        obj = self.base / 'owned.o'
        dep = self.base / 'owned.d'
        result = subprocess.run([clang, '--no-default-config', '-MMD', '-MF', str(dep),
                                 '-c', str(self.source / 'owned.c'), '-o', str(obj)],
                                env=tool.command_environment(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(obj.stat().st_size, 0)
        deps = tool.parse_depfile(dep)
        known = {str((self.source / n).resolve()): row for n, row in self.rows.items()}
        sdk_root = self.base / 'sdk'
        if Path('/usr/bin/xcrun').exists():
            sdk_root = Path(subprocess.check_output(['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'],
                                                   text=True, env=tool.command_environment()).strip())
        closure = tool.verify_dependencies(deps, known, sdk_root, self.base / 'toolchain')
        self.assertEqual({row['path'] for row in closure['bound_inputs']}, set(known))

    def test_toolchain_builtins_are_external_hashed_dependencies(self):
        tool = self.tool()
        builtin = self.base / 'toolchain/stdint.h'
        builtin.parent.mkdir()
        builtin.write_text('owned builtin fixture\n')
        result = tool.verify_dependencies([builtin], {}, self.base / 'sdk', builtin.parent)
        self.assertEqual(result['external_toolchain_inputs'][0]['sha256'], self.record(builtin)['sha256'])

    def test_post_build_source_change_is_detected(self):
        tool = self.tool()
        before = tool.snapshot_files(self.source, self.rows)
        (self.source / 'owned.c').write_text('changed during compile\n')
        with self.assertRaises(ValueError): tool.snapshot_files(self.source, before)

    def test_failed_real_command_is_receipted_without_build_success(self):
        tool = self.tool()
        row = tool.run_command([sys.executable, '-c', 'import sys; print("owned failure", file=sys.stderr); sys.exit(7)'],
                               self.base, 'owned-failure', self.base)
        self.assertEqual(row['exit_code'], 7)
        self.assertIn('owned failure', Path(row['stderr']['path']).read_text())
        self.assertEqual(row['argv'][0], sys.executable)

    def test_command_environment_ignores_injected_include_and_library_paths(self):
        tool = self.tool()
        with patch.dict(os.environ, {'CPATH': '/owned/untrusted', 'LIBRARY_PATH': '/owned/untrusted',
                                     'DYLD_INSERT_LIBRARIES': '/owned/untrusted', 'CFLAGS': '-include untrusted'}):
            env = tool.command_environment()
        for name in ('CPATH', 'LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'CFLAGS'):
            self.assertNotIn(name, env)
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')

    def test_receipt_boundary_and_link_entry_are_unambiguous(self):
        tool = self.tool()
        receipt = tool.new_receipt()
        self.assertEqual(receipt['baseline'], 'original-1.9-metadata-baseline-compile-only')
        for name in ('base_v0_configured', 'host_guest_gpu_executed', 'installable', 'gameplay_verified'):
            self.assertIs(receipt[name], False)
        source = (COMPONENT / 'adapter/link_only_main.c').read_text()
        self.assertIn('LINK-ONLY-DO-NOT-RUN', source)
        self.assertIn('return 78;', source)
        self.assertNotIn('dm_main(', source)
        self.assertNotIn('UIApplicationMain', source)


if __name__ == '__main__':
    unittest.main()
