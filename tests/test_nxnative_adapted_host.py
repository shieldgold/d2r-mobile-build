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

    def test_fixed_patch_inventory_has_exact_ordered_overlap(self):
        tool = self.tool()
        base = self.base_tool()
        paths = set()
        for name, allowed in tool.PATCHES:
            touched = tool.patch_members(COMPONENT / name)
            self.assertEqual(touched, set(allowed))
            paths |= touched
        self.assertEqual([name for name, _ in tool.PATCHES], [
            'patches/normal-entry.patch','patches/base-v0-texture-view.patch',
            'patches/normal-hle-crash.patch','patches/horizon-address-signal.patch',
            'patches/normal-runtime-diagnostics.patch','patches/base-v0-conditional-shader-provider.patch'])
        self.assertEqual(len(paths), 18)
        lock = base.read_json(COMPONENT / 'SOURCE.lock.json')
        self.assertTrue(paths <= set(lock['upstream_files']))

    def test_provider_six_units_are_explicit_and_have_native_header_precedence(self):
        tool = self.tool()
        base = self.base_tool()
        manifest = base.read_json(COMPONENT / 'compile_sources.json')
        private = self.directory / 'inputs'
        commands = tool.compile_commands(private, self.directory, manifest['translation_units'],
            {'clang': '/owned/clang', 'sdk_path': '/owned/iPhoneOS26.5.sdk'}, base)
        self.assertEqual(len(commands), 136)
        expected = {
            'shader_provider/adapter/whole_shader_conditional.m',
            'shader_provider/adapter/whole_shader_publication.m',
            'shader_provider/adapter/whole_shader_bundle_publication.m',
            'shader_provider/dm/gfx/nvn/botw_shader_manifest.m',
            'shader_provider/dm/gfx/nvn/botw_shader_runtime.m',
            'adapter/base_v0_shader_provider.m',
        }
        actual = {str(Path(row['source']).relative_to(private)) for row in commands}
        self.assertTrue(expected <= actual)
        self.assertFalse(any(name.endswith('botw_shader.c') for name in actual))
        self.assertEqual(sum(name.endswith('/nvn_refl.m') for name in actual), 1)
        for row in commands:
            includes = [row['argv'][i+1] for i, arg in enumerate(row['argv'][:-1]) if arg == '-I']
            self.assertIn(str(private / 'shader_provider/adapter'), includes)
            self.assertIn(str(private / 'shader_provider/dm/gfx/nvn'), includes)
            self.assertLess(includes.index(str(private / 'upstream/dm/gfx/nvn/include')),
                            includes.index(str(private / 'shader_provider/dm/gfx/nvn')))
            if str(Path(row['source']).relative_to(private)) in expected:
                self.assertIn('-fobjc-arc', row['argv'])
                self.assertIn('-fblocks', row['argv'])

    def test_provider_required_symbols_cannot_be_satisfied_by_a_partial_link(self):
        tool = self.tool()
        required = {'_nx_base_shader_provider', '_nx_base_shader_info', '_nx_base_program_info',
                    '_nx_base_program_device', '_whole_runtime_shader_bundle_publication',
                    '_botw_shader_runtime_function', '_botw_conditional_modes_lock'}
        self.assertTrue(required <= tool.STRONG_SYMBOLS)
        complete = '\n'.join('0000000000000000 T ' + symbol for symbol in tool.STRONG_SYMBOLS)
        self.assertEqual(tool.verify_strong_symbols(complete), tool.STRONG_SYMBOLS)
        for symbol in required:
            with self.subTest(symbol=symbol), self.assertRaisesRegex(ValueError, 'strong symbols absent'):
                tool.verify_strong_symbols(complete.replace(' T ' + symbol + '\n', ' U ' + symbol + '\n')
                    if complete.endswith(symbol) is False else complete.rsplit(' T ' + symbol, 1)[0])

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
        with patch.object(tool, 'PATCHES', inventory), patch.object(tool, 'COPIED_ADAPTERS', ()), patch.object(tool, 'ABI_FILES', ()), patch.object(tool, 'SHADER_FILES', ()), patch.object(tool, 'SHADER_PATCHES', ()):
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
        for name in (*tool.ABI_FILES, *(source for source, _ in tool.SHADER_FILES),
                     *(source for source, _, _ in tool.SHADER_PATCHES)):
            lock.setdefault('external_code_files', {})[name] = record(ROOT / name)
        return lock

    def test_actual_patches_copy_full_original_tree_and_preserve_vendor(self):
        tool = self.tool()
        base = self.base_tool()
        lock = self.fixture_lock(base, tool)
        vendor_before = base.snapshot_files(COMPONENT / 'upstream', lock['upstream_files'])
        receipt = tool.new_receipt(base)
        private, snapshot = tool.copy_private_inputs(COMPONENT, self.directory, lock, base, receipt)
        self.assertEqual(len(snapshot), 978)
        changed = set()
        for name, original in lock['upstream_files'].items():
            if snapshot['upstream/' + name] != {k: original[k] for k in ('bytes', 'sha256')}:
                changed.add(name)
        self.assertEqual(changed, set().union(*(set(allowed) for _, allowed in tool.PATCHES)))
        self.assertEqual(vendor_before, base.snapshot_files(COMPONENT / 'upstream', lock['upstream_files']))
        tool.verify_private(private, snapshot, base)

    def test_commands_compile_136_actual_units_with_macro_and_private_includes(self):
        tool = self.tool()
        base = self.base_tool()
        manifest = base.read_json(COMPONENT / 'compile_sources.json')
        private = self.directory / 'inputs'
        sdk = {'clang': '/owned/clang', 'sdk_path': '/owned/iPhoneOS26.5.sdk'}
        commands = tool.compile_commands(private, self.directory, manifest['translation_units'], sdk, base)
        self.assertEqual(len(commands), 136)
        self.assertEqual(sum(row['role'] == 'vpx_decoder' for row in commands), 71)
        self.assertEqual(len({row['object'] for row in commands}), 136)
        for row in commands:
            self.assertTrue(Path(row['source']).is_relative_to(private))
            self.assertIn('-DNXNATIVE_NORMAL_POLICY=1', row['argv'])
            self.assertIn('-DDM_SHELL=1', row['argv'])
            self.assertIn(str(private / 'adapter'), row['argv'])
            self.assertIn(str(private / 'external_abi'), row['argv'])
            self.assertIn('arm64-apple-ios26.0', row['argv'])
            self.assertIn('-march=armv8.2-a', row['argv'])
            self.assertIn('-MMD', row['argv'])
            self.assertEqual(row['argv'][row['argv'].index('-MF') + 1], row['depfile'])
            self.assertEqual(row['argv'][row['argv'].index('-MT') + 1], row['object'])
        roles = {row['role'] for row in commands}
        self.assertTrue({'owned_normal_policy', 'owned_normal_failures', 'reused_base_v0_abi', 'owned_link_only_entry'} <= roles)
        self.assertTrue({'_nxnative_normal_fail', '_nxnative_normal_signal_failure'} <= tool.STRONG_SYMBOLS)

    def test_complete_provider_staging_uses_original_modules_and_only_selected_patch_sections(self):
        tool = self.tool()
        base = self.base_tool()
        lock = self.fixture_lock(base, tool)
        receipt = tool.new_receipt(base)
        private, snapshot = tool.copy_private_inputs(COMPONENT, self.directory, lock, base, receipt)
        shader = private / 'shader_provider'
        self.assertEqual(len(tool.SHADER_FILES), 13)
        self.assertEqual(len(tool.SHADER_PATCHES), 2)
        self.assertEqual(set(tool.private_snapshot(shader, base)), {target for _, target in tool.SHADER_FILES})
        self.assertFalse((shader / 'dm/gfx/nvn/include/nvn.h').exists())
        self.assertFalse((shader / 'dm/gfx/nvn/botw_shader.c').exists())
        self.assertIn('NxBaseOpaqueShaderDataExt', (private / 'upstream/dm/gfx/nvn/include/nvn.h').read_text())
        changed = {name for patch_record in receipt['shader_provider_patches'] for name in patch_record['changed_members']}
        self.assertEqual(changed, {'dm/gfx/nvn/botw_shader_manifest.h','dm/gfx/nvn/botw_shader_manifest.m','dm/gfx/nvn/botw_shader_runtime.m'})
        self.assertEqual(receipt['shader_provider_patches'][1]['changed_members'], ['dm/gfx/nvn/botw_shader_manifest.m'])
        self.assertEqual(set(receipt['shader_provider_patches'][1]['original_patch']['all_members']),
            {'dm/gfx/nvn/botw_shader_manifest.m','dm/gfx/nvn/hle_nvn.c','dm/gfx/nvn/nvn_mtl.m'})
        for source, target in tool.SHADER_FILES:
            if target not in changed:
                self.assertEqual((shader / target).read_bytes(), (ROOT / source).read_bytes())
        for source, _, _ in tool.SHADER_PATCHES:
            self.assertEqual((private / 'shader_provider_patches' / Path(source).name).read_bytes(), (ROOT / source).read_bytes())
        tool.verify_private(private, snapshot, base)

    def test_provider_missing_or_wrong_source_hash_is_rejected_before_patch(self):
        tool = self.tool()
        base = self.base_tool()
        for index, name in enumerate((tool.SHADER_FILES[0][0], tool.SHADER_PATCHES[0][0])):
            for broken in ('missing', 'changed'):
                with self.subTest(source=name, broken=broken):
                    lock = self.fixture_lock(base, tool)
                    if broken == 'missing':
                        del lock['external_code_files'][name]
                    else:
                        lock['external_code_files'][name] = {'bytes': (ROOT / name).stat().st_size, 'sha256': '0' * 64}
                    private = self.directory / ('%d-%s' % (index, broken))
                    private.mkdir()
                    with patch.object(base, 'run_command') as command, self.assertRaises(ValueError):
                        tool.copy_shader_provider(COMPONENT, private, self.directory, lock, base, tool.new_receipt(base))
                    command.assert_not_called()

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'Foundation compile-only objects')
    def test_all_six_provider_translation_units_compile_without_host_execution(self):
        tool = self.tool()
        base = self.base_tool()
        private, _ = tool.copy_private_inputs(COMPONENT, self.directory, self.fixture_lock(base, tool), base, tool.new_receipt(base))
        names = ['shader_provider/' + name for name in tool.SHADER_UNITS] + ['adapter/base_v0_shader_provider.m']
        for index, name in enumerate(names):
            obj = self.directory / ('owned-provider-%d.o' % index)
            command = ['clang','--no-default-config','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror',
                       '-Wno-deprecated-declarations','-Wno-unused-function','-DNXNATIVE_NORMAL_POLICY=1',
                       '-I',str(private / 'adapter'),'-I',str(private / 'upstream/dm/gfx/nvn'),
                       '-I',str(private / 'upstream/dm/gfx/nvn/include'),'-I',str(private / 'shader_provider/adapter'),
                       '-I',str(private / 'shader_provider/dm/gfx/nvn'),'-c',str(private / name),'-o',str(obj)]
            result = subprocess.run(command,env=base.command_environment(),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertGreater(obj.stat().st_size,0)

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
