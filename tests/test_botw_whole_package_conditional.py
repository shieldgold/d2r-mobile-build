"""Package only a complete real owned EXIT four-mode CPU publication."""
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_botw_whole_shader_conditional_build as conditional_fixtures
import test_botw_whole_shader_v8 as v8

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
SCRIPT = EXP / 'scripts/package_conditional.py'
H = lambda raw: hashlib.sha256(raw).hexdigest()
BASELINE = os.environ.get('BOTW_CONDITIONAL_PACKAGE_BASELINE') == '1'


class WholeConditionalPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tools = [ROOT / '.tools/dotnet-10.0.402/dotnet',
                 ROOT / '.tools/botw-ryujinx-xlate-21f/dmsh_xlate.dll',
                 ROOT / '.tools/spirv-cross-65d7393-build/spirv-cross']
        if not all(p.is_file() for p in tools):
            raise unittest.SkipTest('actual pinned owned CPU shader tools unavailable')
        cls.package = v8.load('conditional_package_original', EXP / 'scripts/package_ios.py')
        if not BASELINE:
            cls.adapter = v8.load('conditional_package_adapter', SCRIPT)
        conditional_fixtures.WholeShaderConditionalBuild.setUpClass()
        cls.addClassCleanup(conditional_fixtures.WholeShaderConditionalBuild.doClassCleanups)
        f = conditional_fixtures.WholeShaderConditionalBuild(); f.setUp()
        cls.addClassCleanup(f.doCleanups); cls.fixture = f
        command = [sys.executable, str(EXP / 'adapter/shader_conditional_build.py'),
                   str(f.manifest), str(f.caps), str(f.request), str(f.out),
                   '--xlate', str(tools[1]), '--spirv-cross', str(tools[2])]
        env = dict(os.environ, DOTNET_ROOT=str(tools[0].parent), DOTNET_CLI_TELEMETRY_OPTOUT='1',
                   DOTNET_NOLOGO='1', PATH=str(tools[0].parent) + os.pathsep + os.environ.get('PATH', ''))
        run = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
        if run.returncode:
            raise AssertionError('actual owned EXIT publication failed: ' + run.stdout + run.stderr)
        cls.command = command; cls.cli_run = run

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='botw-owned-conditional-package-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.source = self.root / 'source'
        shutil.copytree(self.fixture.out, self.source)

    def shader_files(self):
        if BASELINE:
            return self.package.shader_files(self.source)
        return self.adapter.shader_files(self.source, self.package)

    def publication(self):
        return json.loads((self.source / 'conditional-publication.json').read_bytes())

    def package_dir(self):
        return self.source / self.publication()['package']

    def change(self, relative, callback):
        path = self.source / relative; value = json.loads(path.read_bytes())
        callback(value); path.write_bytes((json.dumps(value, sort_keys=True) + '\n').encode())

    def reseal(self):
        """Keep outer digests self-consistent so negative tests reach real checks."""
        canonical = lambda v: (json.dumps(v, sort_keys=True, separators=(',', ':')) + '\n').encode()
        admitted = self.source / 'admitted'; package = self.package_dir()
        proof = json.loads((admitted / 'subset-proof.json').read_bytes())
        admission = json.loads((admitted / 'conditional-admission.json').read_bytes())
        publication = self.publication()
        manifest = json.loads((package / 'manifest-build.json').read_bytes())
        for key, accepted in proof['accepted'].items():
            for mode in accepted['modes']:
                for kind in ('job', 'res'):
                    name = mode[kind + '_file']; raw = (admitted / name).read_bytes()
                    mode[kind + '_sha256'] = admission['audit_files'][name] = H(raw)
        proof_raw = canonical(proof); (admitted / 'subset-proof.json').write_bytes(proof_raw)
        renamed = {}
        for result in manifest['results']:
            old = result['variant_sha256']; contract = json.loads((package / (old + '.contract.json')).read_bytes())
            contract['proofs']['subset_sha256'] = H(proof_raw)
            device = contract['requirements']['device']
            mode = next(m for m in proof['accepted'][contract['capture']['key']]['modes']
                        if all(m[k] == v for k,v in device.items()))
            contract['translation']['job_sha256'] = mode['job_sha256']
            raw = canonical(contract); new = H(raw); renamed[old] = new
            reflection = json.loads((package / (old + '.json')).read_bytes())
            reflection['conditional_contract_sha256'] = new
            content = {'.contract.json': raw, '.json': canonical(reflection),
                       '.metal': (package / (old + '.metal')).read_bytes()}
            for suffix, raw in content.items():
                (package / (old + suffix)).unlink(); (package / (new + suffix)).write_bytes(raw)
            result['variant_sha256'] = new
            for kind, suffix in (('contract', '.contract.json'), ('json', '.json'), ('metal_source', '.metal')):
                result['files'][kind] = {'path': new + suffix, 'sha256': H(content[suffix])}
        for capture in admission['captures'].values():
            capture['variants'] = [renamed.get(v, v) for v in capture['variants']]
        admission['subset_sha256'] = H(proof_raw)
        admission_raw = canonical(admission); (admitted / 'conditional-admission.json').write_bytes(admission_raw)
        manifest['conditional_admission_sha256'] = H(admission_raw)
        manifest_raw = canonical(manifest); (package / 'manifest-build.json').write_bytes(manifest_raw)
        publication.update(subset_sha256=H(proof_raw), conditional_admission_sha256=H(admission_raw),
                           manifest_sha256=H(manifest_raw))
        (self.source / 'conditional-publication.json').write_bytes(canonical(publication))

    def test_real_owned_cli_four_modes_exact_unsigned_bytes_no_tools_or_captures(self):
        before = {str(p): H(p.read_bytes()) for p in self.source.rglob('*') if p.is_file()}
        files, index = self.shader_files()
        self.assertEqual(index['schema'], 'botw-whole-conditional-shader-package-v1')
        self.assertEqual(index['profile'], 'botw-switch-base-v0')
        self.assertEqual(index['directory'], 'botw-c-' + index['chain'])
        self.assertEqual({n for n in files if '/' not in n},
            {'conditional-publication.json', 'conditional-admission.json', 'subset-proof.json',
             'base-bindings.json', 'base-formats.json', 'shader-package.json'})
        original = {'conditional-publication.json': self.source / 'conditional-publication.json'}
        original.update({n: self.source / 'admitted' / n for n in
            ('conditional-admission.json', 'subset-proof.json', 'base-bindings.json', 'base-formats.json')})
        original.update({index['directory'] + '/' + p.name: p for p in self.package_dir().iterdir()})
        for name, path in original.items(): self.assertEqual(files[name], path.read_bytes())
        self.assertEqual(len([n for n in files if n.endswith('.contract.json')]), 4)
        self.assertEqual(len(files), 19)
        self.assertEqual(json.loads(files['shader-package.json']), index)
        for field, name in [('publication_sha256', 'conditional-publication.json'),
                            ('admission_sha256', 'conditional-admission.json'), ('subset_sha256', 'subset-proof.json'),
                            ('binding_contract_sha256', 'base-bindings.json'), ('format_contract_sha256', 'base-formats.json')]:
            self.assertEqual(index[field], H(files[name]))
        self.assertFalse(any(any(part in n.split('/') for part in ('toolchain', 'captures', 'inputs', 'Documents', 'Saves', 'UID')) for n in files))
        self.assertEqual(before, {str(p): H(p.read_bytes()) for p in self.source.rglob('*') if p.is_file()})
        if os.environ.get('BOTW_CONDITIONAL_PACKAGE_SMOKE_RECEIPT'):
            Path(os.environ['BOTW_CONDITIONAL_PACKAGE_SMOKE_RECEIPT']).write_text(json.dumps({
                'command': self.command, 'cli_exit': self.cli_run.returncode, 'index': index,
                'files': {n: {'bytes': len(v), 'sha256': H(v)} for n,v in files.items()},
                'owned_only': True, 'guest_or_gpu_executed': False, 'unsigned': True,
                'installed': False, 'compiled_for_Metal': False}, indent=2) + '\n')

    def test_missing_proof_and_wrong_fixed_format_reject(self):
        for name in ('subset-proof.json', 'base-formats.json', 'conditional-admission.json'):
            path = self.source / 'admitted' / name; raw = path.read_bytes(); path.unlink()
            with self.subTest(missing=name), self.assertRaises((ValueError, OSError)): self.shader_files()
            path.write_bytes(raw)
        self.change('admitted/base-formats.json', lambda v: v.update(profile='wrong-base'))
        with self.assertRaises(ValueError): self.shader_files()

    def test_duplicate_modes_or_incomplete_capture_batch_reject(self):
        proof = self.source / 'admitted/subset-proof.json'; original = proof.read_bytes()
        def duplicate(v):
            modes = next(iter(v['accepted'].values()))['modes']; modes[-1] = copy.deepcopy(modes[0])
        self.change('admitted/subset-proof.json', duplicate)
        # A duplicate removes one device, so contracts for that missing mode
        # intentionally stay unchanged; the mode audit must reject first.
        self.reseal_metadata_only()
        with self.assertRaisesRegex(ValueError, 'duplicate|mode'): self.shader_files()
        proof.write_bytes(original)
        self.change('admitted/conditional-admission.json', lambda v: next(iter(v['captures'].values()))['variants'].pop())
        self.reseal_metadata_only()
        with self.assertRaisesRegex(ValueError, 'four unique'): self.shader_files()

    def test_full_capture_sha_and_logical_job_proof_must_match(self):
        self.change('admitted/subset-proof.json',
                    lambda v: next(iter(v['accepted'].values()))['capture'].update(sha256='0'*64))
        self.reseal_metadata_only()
        with self.assertRaisesRegex(ValueError, 'full capture/C1'): self.shader_files()

    def reseal_metadata_only(self):
        admission_path = self.source / 'admitted/conditional-admission.json'
        admission = json.loads(admission_path.read_bytes())
        admission['subset_sha256'] = H((self.source / 'admitted/subset-proof.json').read_bytes())
        admission_path.write_text(json.dumps(admission) + '\n')
        manifest_path = self.package_dir() / 'manifest-build.json'
        manifest = json.loads(manifest_path.read_bytes()); manifest['conditional_admission_sha256'] = H(admission_path.read_bytes())
        manifest_path.write_text(json.dumps(manifest) + '\n')
        publication = self.publication(); publication.update(subset_sha256=admission['subset_sha256'],
            conditional_admission_sha256=H(admission_path.read_bytes()), manifest_sha256=H(manifest_path.read_bytes()))
        (self.source / 'conditional-publication.json').write_text(json.dumps(publication) + '\n')

    def test_resealed_actual_job_requires_exact_original_c1_and_no_query_answers(self):
        proof = json.loads((self.source / 'admitted/subset-proof.json').read_bytes())
        mode = next(iter(proof['accepted'].values()))['modes'][0]
        self.change('admitted/' + mode['job_file'], lambda v: v.update(c1='00'))
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'actual final logical job'): self.shader_files()

    def test_resealed_actual_resource_query_fallback_is_rejected_by_original_analysis(self):
        proof = json.loads((self.source / 'admitted/subset-proof.json').read_bytes())
        mode = next(iter(proof['accepted'].values()))['modes'][0]
        self.change('admitted/' + mode['res_file'], lambda v: v.update(notes=['missing sampler type fallback']))
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'query fallback'): self.shader_files()

    def test_resealed_msl_unknown_driver_read_cannot_bypass_original_analysis(self):
        proof_path = self.source / 'admitted/subset-proof.json'
        proof = json.loads(proof_path.read_bytes())
        mode = next(iter(proof['accepted'].values()))['modes'][0]
        package = self.package_dir()
        result = next(r for r in json.loads((package / 'manifest-build.json').read_bytes())['results']
            if json.loads((package / (r['variant_sha256'] + '.contract.json')).read_bytes())['requirements']['device'] ==
               {name: mode[name] for name in ('window_origin_mode', 'depth_mode')})
        source = ('struct spvDescriptorSetBuffer0 {\nconstant float4* vp_c0 [[id(0)]];\n};\n'
                  'vertex void main0() { auto value = (*vp_c0)._m0[97]; }\n').encode()
        (package / result['files']['metal_source']['path']).write_bytes(source)
        mode['msl_sha256'] = H(source)
        mode['fresh_msl_sha256'] = H(source)
        proof_path.write_text(json.dumps(proof) + '\n')
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'unknown/conflicting|non-C2'): self.shader_files()

    def test_symlink_traversal_unlisted_or_changed_artifact_reject(self):
        package = self.package_dir(); metal = next(package.glob('*.metal')); raw = metal.read_bytes()
        target = self.root / 'external.metal'; target.write_bytes(raw); metal.unlink(); metal.symlink_to(target)
        with self.assertRaises(ValueError): self.shader_files()
        metal.unlink(); metal.write_bytes(raw + b'\n// changed bytes\n')
        with self.assertRaises(ValueError): self.shader_files()
        metal.write_bytes(raw); (package / 'unlisted.metal').write_bytes(raw)
        with self.assertRaises(ValueError): self.shader_files()
        (package / 'unlisted.metal').unlink()
        self.change('conditional-publication.json', lambda v: v.update(package='build/../escape'))
        with self.assertRaises(ValueError): self.shader_files()

    def test_full_stage_python_is_verified_before_import_and_copy_rechecked(self):
        path = self.source / 'toolchain/dm/gfx/tools/botw_shader_build.py'
        sentinel = self.root / 'executed-unverified-source'
        path.write_text("open(" + repr(str(sentinel)) + ", 'w').write('bad')\n")
        with self.assertRaises(ValueError): self.shader_files()
        self.assertFalse(sentinel.exists())

    def test_post_validation_source_change_cannot_enter_import_snapshot(self):
        if BASELINE: self.skipTest('conditional snapshot implementation required')
        original = self.package.source_builder.source_toolchain_hashes
        source = self.source / 'toolchain/dm/gfx/tools/botw_shader_build.py'
        sentinel = self.root / 'race-executed'
        def changed(stage):
            hashes = original(stage)
            if Path(stage) == self.source / 'toolchain':
                source.write_text("open(" + repr(str(sentinel)) + ", 'w').write('bad')\n")
            return hashes
        with mock.patch.object(self.package.source_builder, 'source_toolchain_hashes', side_effect=changed):
            with self.assertRaises(ValueError): self.shader_files()
        self.assertFalse(sentinel.exists())

    def test_source_module_chain_and_delayed_compiler_claims_cannot_be_relabelled(self):
        for relative, callback in (
            ('admitted/subset-proof.json', lambda v: v['tools']['xlate'].update(embedded_source_sha16='0'*16)),
            ('admitted/subset-proof.json', lambda v: v.update(chain='0'*12)),
            ('conditional-publication.json', lambda v: v.update(device_compilation_tested=True)),
            ('conditional-publication.json', lambda v: v.update(runnable=True))):
            shutil.rmtree(self.source); shutil.copytree(self.fixture.out, self.source)
            self.change(relative, callback)
            if relative.startswith('admitted/'): self.reseal_metadata_only()
            with self.subTest(metadata=relative), self.assertRaises(ValueError): self.shader_files()


if __name__ == '__main__': unittest.main()
