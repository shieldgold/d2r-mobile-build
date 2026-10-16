"""Actual owned four-mode CPU publication through the native bundle gate."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import test_botw_whole_shader_conditional_build as producer

ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/botw_upstream_host'
H=lambda raw:hashlib.sha256(raw).hexdigest()


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'Foundation CPU publication audit')
class ShaderBundlePublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-bundle-publication-');cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name).resolve()
        producer.WholeShaderConditionalBuild.setUpClass()
        cls.addClassCleanup(producer.WholeShaderConditionalBuild.doClassCleanups)
        fixture=producer.WholeShaderConditionalBuild('test_owned_exit_complete_real_cli_four_modes_without_facts')
        fixture.setUp();cls.addClassCleanup(fixture.doCleanups)
        fixture.test_owned_exit_complete_real_cli_four_modes_without_facts()
        cls.source=fixture.out
        nvn=cls.work/'upstream/dm/gfx/nvn';shutil.copytree(C/'upstream',cls.work/'upstream')
        run=subprocess.run(['patch','--fuzz=0','-p1','-i',str(C/'patches/portable-shader-source.patch')],cwd=cls.work/'upstream',capture_output=True,text=True)
        if run.returncode:raise AssertionError(run.stdout+run.stderr)
        cls.binary=cls.work/'probe'
        files=[C/'tests/whole_shader_bundle_publication_probe.m',C/'adapter/whole_shader_bundle_publication.m',
            C/'adapter/whole_shader_publication.m',C/'adapter/whole_shader_conditional.m',nvn/'botw_shader_manifest.m',nvn/'nvn_refl.m']
        command=['clang','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(C/'adapter'),'-I'+str(nvn),'-I'+str(nvn/'include'),
            *map(str,files),'-framework','Foundation','-framework','Metal','-o',str(cls.binary)]
        run=subprocess.run(command,capture_output=True,text=True)
        if run.returncode:raise AssertionError(run.stdout+run.stderr)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=self.work);self.addCleanup(self.temp.cleanup)
        self.bundle=Path(self.temp.name)/'Owned.app';self.root=self.bundle/'botw/shaders';self.root.mkdir(parents=True)
        self.publication=json.loads((self.source/'conditional-publication.json').read_bytes())
        self.directory=Path(self.publication['package']).name
        shutil.copytree(self.source/self.publication['package'],self.root/self.directory)
        shutil.copyfile(self.source/'conditional-publication.json',self.root/'conditional-publication.json')
        for name in ('conditional-admission.json','subset-proof.json','base-bindings.json','base-formats.json'):
            shutil.copyfile(self.source/'admitted'/name,self.root/name)
        self.index={'schema':'botw-whole-conditional-shader-package-v1','profile':'botw-switch-base-v0',
            'chain':self.directory.removeprefix('botw-c-'),'directory':self.directory,
            'publication_sha256':H((self.root/'conditional-publication.json').read_bytes()),
            'admission_sha256':H((self.root/'conditional-admission.json').read_bytes()),
            'subset_sha256':H((self.root/'subset-proof.json').read_bytes()),
            'binding_contract_sha256':H((self.root/'base-bindings.json').read_bytes()),
            'format_contract_sha256':H((self.root/'base-formats.json').read_bytes())}
        self.write_index()

    def write_index(self):
        (self.root/'shader-package.json').write_text(json.dumps(self.index))

    def run_gate(self,*,success=True,legacy=False,capacity=None):
        command=[str(self.binary),str(self.bundle),'legacy' if legacy else 'bundle']
        if capacity is not None:command.append(str(capacity))
        run=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0 if success else 2,run.stdout+run.stderr)
        if success:self.assertEqual(Path(run.stdout.strip()),self.root/self.directory/'manifest-build.json')
        return run

    def test_real_owned_translator_publication_passes_new_gate_only(self):
        self.run_gate(legacy=True,success=False)
        self.run_gate()

    def test_ios_packager_dispatch_requires_the_actual_conditional_provider(self):
        package=producer.v8.load('bundle_actual_ios_package_dispatch',C/'scripts/package_ios.py')
        with self.assertRaisesRegex(ValueError,'did not compile the conditional'):
            package.shader_files(self.source)
        files,index=package.shader_files(self.source,conditional_supported=True)
        self.assertEqual(index['schema'],'botw-whole-conditional-shader-package-v1')
        self.assertEqual(set(files),{'shader-package.json','conditional-publication.json','conditional-admission.json',
            'subset-proof.json','base-bindings.json','base-formats.json'}|
            {self.directory+'/'+p.name for p in (self.source/self.publication['package']).iterdir()})
        marker=self.source/'source-publication.json';marker.write_bytes(b'{}')
        try:
            with self.assertRaisesRegex(ValueError,'ambiguous'):
                package.shader_files(self.source,conditional_supported=True)
        finally:marker.unlink()

    def test_each_detached_static_proof_and_receipt_is_required(self):
        for name in ('conditional-publication.json','conditional-admission.json','subset-proof.json','base-bindings.json','base-formats.json'):
            path=self.root/name;original=path.read_bytes()
            try:path.write_bytes(original+b' ');self.run_gate(success=False)
            finally:path.write_bytes(original)

    def test_actual_source_contract_and_reflection_are_rechecked(self):
        manifest=json.loads((self.root/self.directory/'manifest-build.json').read_bytes())
        for kind in ('metal_source','contract','json'):
            path=self.root/self.directory/manifest['results'][0]['files'][kind]['path']
            original=path.read_bytes()
            try:path.write_bytes(original+b' ');self.run_gate(success=False)
            finally:path.write_bytes(original)

    def test_uncompiled_publication_cannot_claim_device_execution(self):
        path=self.root/'conditional-publication.json';value=json.loads(path.read_bytes())
        value['guest_or_gpu_executed']=True;path.write_text(json.dumps(value))
        self.index['publication_sha256']=H(path.read_bytes());self.write_index();self.run_gate(success=False)

    def test_original_cpu_proof_binds_c1_and_reflection_core_after_outer_rehash(self):
        path=self.root/self.directory/'manifest-build.json';original_manifest=path.read_bytes()
        result=json.loads(original_manifest)['results'][0]
        reflection_path=self.root/self.directory/result['files']['json']['path']
        original_reflection=reflection_path.read_bytes()
        publication_path=self.root/'conditional-publication.json';original_publication=publication_path.read_bytes()
        for field in ('c1','entry'):
            with self.subTest(field=field):
                reflection=json.loads(original_reflection)
                reflection[field]=('ff'+reflection['c1'][2:]) if field=='c1' else 'wrong_owned_entry'
                reflection_path.write_text(json.dumps(reflection))
                manifest=json.loads(original_manifest);manifest['results'][0]['files']['json']['sha256']=H(reflection_path.read_bytes())
                path.write_text(json.dumps(manifest))
                publication=json.loads(original_publication);publication['manifest_sha256']=H(path.read_bytes())
                publication_path.write_text(json.dumps(publication))
                self.index['publication_sha256']=H(publication_path.read_bytes());self.write_index()
                # Static proof, admission, contracts and MSL stay byte-identical.
                self.run_gate(success=False)

    def test_other_profile_and_unpinned_protocol_are_rejected(self):
        for field,value in (('profile','other-game'),('binding_contract_sha256','0'*64),('format_contract_sha256','0'*64)):
            original=self.index[field];self.index[field]=value;self.write_index();self.run_gate(success=False)
            self.index[field]=original

    def test_bundle_children_cannot_redirect_artifact_or_static_proof(self):
        for path in (self.root/self.directory,self.root/'subset-proof.json'):
            original=path.with_name(path.name+'-actual');path.rename(original);path.symlink_to(original,target_is_directory=original.is_dir())
            try:self.run_gate(success=False)
            finally:path.unlink();original.rename(path)

    def test_output_capacity_failure_keeps_the_callers_original_path(self):
        self.run_gate(capacity=1,success=False)


if __name__=='__main__':unittest.main()
