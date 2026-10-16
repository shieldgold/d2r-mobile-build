"""Owned EXIT only: strict source publication, complete real translator CLI."""
from pathlib import Path
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import test_botw_shader_bindings as release_fixtures
import test_botw_whole_shader_v8 as v8_fixtures

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
PUBLISHER=EXP/'adapter/shader_source_build.py'
H=lambda data:hashlib.sha256(data).hexdigest()


def load(name,path):
    return v8_fixtures.load(name,path)


class WholeShaderSourceBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-owned-source-stage-');cls.addClassCleanup(cls.temp.cleanup)
        cls.stage=Path(cls.temp.name)/'toolchain'
        stage=load('owned_source_v8',EXP/'adapter/shader_v8_control.py')
        cls.stage_receipt=stage.stage_toolchain(cls.stage)
        cls.tool=load('owned_source_complete_builder',cls.stage/'dm/gfx/tools/botw_shader_build.py')
        cls.publisher=load('owned_source_publisher',PUBLISHER)

    def setUp(self):
        # Reuse the actual gate fixture's source/C1/reflection/review construction;
        # replace its synthetic program with the existing exact v8 owned EXIT.
        f=release_fixtures.ShaderReleaseGateTests('test_explicit_scoped_established_facts_pass_and_preserve_all_hashes')
        f.setUp();self.addCleanup(f.doCleanups);self.f=f
        for path in f.caps.iterdir():path.unlink()
        f.fact_path.unlink();(f.established/f.fact_path.name).unlink()
        control,code=v8_fixtures.owned();fields=self.tool.parse_control(control)
        digest=self.tool.capture_digest(fields,control,code);f.key=digest[:16]
        for kind,data,suffix in (('control',control,'.control'),('code',code,'.nvn')):
            name=H(data)+suffix;(f.source/name).write_bytes(data)
            f.manifest['records'][0].update({kind+'_sha256':H(data),kind+'_file':name})
        row=f.manifest['records'][0];row['container']='owned EXIT fixture; no game bytes'
        row.update(c1_begin=fields['c1_offset'],c1_end=fields['code_size'],c1_bytes=fields['c1_size'])
        c1=code[fields['c1_offset']:];row.update(c1_sha256=H(c1),c1_file=H(c1)+'.cbuf1')
        (f.source/row['c1_file']).write_bytes(c1);f.write(f.source/'inputs.json',f.manifest)
        (f.caps/(f.key+'.control')).write_bytes(control);(f.caps/(f.key+'.code')).write_bytes(code)
        (f.caps/(f.key+'.json')).write_text(self.tool.canonical_capture_json(f.key,digest,fields,H(control),H(code)))
        f.cap=self.tool.load_capture(str(f.caps),f.key);f.tool=self.tool
        f.doc={'schema':'botw-shader-facts-v1','key':f.key,'sha256':digest,'stage':'vertex',
            'provenance':'reviewed owned EXIT fixture modes only; no game or hardware authority',
            'device_state':{'window_origin_mode':'upper_left','depth_mode':'near_is_zero'}}
        f.fact_path=f.facts/(f.key+'.facts.json');f.write(f.fact_path,f.doc)
        (f.established/f.fact_path.name).write_bytes(f.fact_path.read_bytes())
        f.probe['results']=[{'key':f.key,'status':'probe','capture':f.cap.summary(),'errors':[],'conflicts':[]}]
        f.write(f.probe_path,f.probe)
        f.review.update(capture_sha256=digest,facts_sha256=H(f.fact_path.read_bytes()),fields=['device_state'],
                        provenance='owned EXIT fixture field review only; not real game approval')
        f.evidence.update(input_manifest_sha256=H((f.source/'inputs.json').read_bytes()),captures={})
        f.sync_review()
        self.admission=release_fixtures.gate.check_release(self.tool,f.source,f.caps,f.facts,f.derive_path,f.evidence_path)

    def publication(self):
        admission_path=self.f.root/'release-admission.json';self.tool.write_json(str(admission_path),self.admission)
        publisher=self.publisher.SourcePublication(self.tool,self.admission,self.f.caps,H(admission_path.read_bytes()))
        publisher.chain='a'*12
        facts=self.tool.load_facts(str(self.f.fact_path),self.f.cap)
        reflection=self.tool.reflection(self.f.cap,publisher.chain,('vertex','main0'),[],[],facts,'B')
        result={'key':self.f.key,'capture':self.f.cap.summary(),'facts':{'sha256':facts.sha256},
                'errors':[],'unknowns':[],'conflicts':[]}
        work=self.f.root/'work';out=self.f.root/'published';work.mkdir();out.mkdir()
        (work/(self.f.key+'_b.metal')).write_text('vertex void main0() {}\n')
        return publisher,reflection,result,work,out

    def test_unknown_nullable_partial_or_probe_identity_never_publishes(self):
        publisher,reflection,result,work,out=self.publication()
        bad=[]
        bad.append(('unadmitted', '0'*16,dict(reflection,sha256=None,facts_sha256=None,stage=None),dict(result,capture={})))
        bad.append(('wrong-result-key',self.f.key,reflection,dict(result,key='0'*16)))
        bad.append(('partial-capture',self.f.key,reflection,dict(result,capture={'sha256':self.f.cap.sha256})))
        cap=dict(result['capture'],stage=False);bad.append(('bool-stage',self.f.key,reflection,dict(result,capture=cap)))
        for field,value in (('sha256','0'*64),('facts_sha256','0'*64),('c1',''),('chain','b'*12),('route','probe'),('notes',['unresolved'])):
            bad.append((field,self.f.key,dict(reflection,**{field:value}),result))
        for field in ('errors','unknowns','conflicts'):
            bad.append((field,self.f.key,reflection,dict(result,**{field:['not acknowledged']})))
        for name,key,refl,row in bad:
            with self.subTest(name=name),self.assertRaises(ValueError):publisher.publish(out,work,key,refl,row,None)
            self.assertEqual(list(out.iterdir()),[])

    def test_batch_checks_exact_relative_files_sha_and_delayed_compile_contract(self):
        publisher,reflection,result,work,out=self.publication()
        publisher.publish(out,work,self.f.key,reflection,result,None)
        manifest=publisher.manifest('build',publisher.chain,{}, {},str(self.f.caps),str(self.f.facts),str(out),[result])
        self.tool.write_json(str(out/'manifest-build.json'),manifest)
        publisher.check_batch(out,manifest)
        self.assertEqual(set(result['files']),{'json','metal_source'})
        extra=out/(self.f.key+'_b.metallib');extra.write_bytes(b'not a compiled library')
        with self.assertRaises(ValueError):publisher.check_batch(out,manifest)
        extra.unlink()
        for kind in result['files']:
            for bad_path in ('../escape','/tmp/absolute',self.f.key+'_other.metal'):
                changed=copy.deepcopy(manifest);changed['results'][0]['files'][kind]['path']=bad_path
                with self.subTest(kind=kind,path=bad_path),self.assertRaises(ValueError):publisher.check_batch(out,changed)
        for change in ({'results':[result,result]},{'mode':'probe'},{'runnable':True},{'runtime_compile_required':False},{'chain':'b'*12}):
            with self.subTest(change=change),self.assertRaises(ValueError):publisher.check_batch(out,dict(manifest,**change))
        metal=out/(self.f.key+'_b.metal');raw=metal.read_bytes();metal.write_bytes(raw+b'changed')
        with self.assertRaises(ValueError):publisher.check_batch(out,manifest)
        metal.unlink();elsewhere=self.f.root/'same-source';elsewhere.write_bytes(raw);metal.symlink_to(elsewhere)
        with self.assertRaises(self.tool.CaptureError):publisher.check_batch(out,manifest)
        with self.assertRaises(ValueError):publisher.manifest('probe',publisher.chain,{}, {},'', '',str(out),[result])

    def test_source_is_regular_bounded_utf8_and_never_contains_nul(self):
        publisher,reflection,result,work,out=self.publication();source=work/(self.f.key+'_b.metal')
        for raw in (b'',b'\xff',b'vertex\0void'):
            source.write_bytes(raw)
            with self.subTest(raw=raw),self.assertRaises(ValueError):publisher.publish(out,work,self.f.key,reflection,result,None)
            self.assertEqual(list(out.iterdir()),[])

    def test_missing_facts_unreviewed_probe_and_each_derive_risk_block_the_whole_batch(self):
        f=self.f;facts=f.fact_path.read_bytes();probe=f.probe_path.read_bytes();review=copy.deepcopy(f.review)
        cases=['no-facts','unreviewed','probe-build']+list(release_fixtures.gate.RISK_TABLES)
        for i,case in enumerate(cases):
            f.fact_path.write_bytes(facts);f.probe_path.write_bytes(probe);f.review=copy.deepcopy(review);f.sync_review();f.write(f.derive_path,f.derive)
            if case=='no-facts':f.fact_path.unlink()
            elif case=='unreviewed':f.review['status']='pending';f.sync_review()
            elif case=='probe-build':f.write(f.probe_path,dict(f.probe,mode='build'))
            else:f.write(f.derive_path,dict(f.derive,**{case:{f.key:['owned risk']}}))
            out=f.root/('denied-'+str(i))
            with self.subTest(case=case),self.assertRaises(ValueError):
                self.publisher.build(f.source,f.caps,f.facts,f.derive_path,f.evidence_path,out,xlate='must-not-run',spirv_cross='must-not-run')
            self.assertFalse((out/'source-publication.json').exists())
            self.assertEqual(list(out.glob('build/botw-s-*')),[])

    def test_owned_v8_exit_real_complete_cli_source_only_and_originals_unchanged(self):
        dotnet=Path(os.environ.get('BOTW_V8_DOTNET',ROOT/'.tools/dotnet-10.0.402/dotnet'))
        xlate=Path(os.environ.get('BOTW_V8_XLATE',ROOT/'.tools/botw-ryujinx-xlate-21f/dmsh_xlate.dll'))
        cross=Path(os.environ.get('BOTW_V8_SPIRV_CROSS',ROOT/'.tools/spirv-cross-65d7393-build/spirv-cross'))
        if not all(p.is_file() for p in (dotnet,xlate,cross)):self.skipTest('actual pinned local translator tools unavailable')
        f=self.f;origin={str(p):H(p.read_bytes()) for folder in (f.source,f.caps,f.facts,f.established,f.evidence_dir) for p in folder.iterdir()}
        from scripts.botw_native import upstream_shaders
        before=upstream_shaders.verify_vendor();out=f.root/'source-build'
        command=[sys.executable,str(PUBLISHER),str(f.source),str(f.caps),str(f.facts),str(f.derive_path),str(f.evidence_path),str(out),'--xlate',str(xlate),'--spirv-cross',str(cross)]
        env={**os.environ,'DOTNET_ROOT':str(dotnet.parent),'DOTNET_CLI_TELEMETRY_OPTOUT':'1','DOTNET_NOLOGO':'1','PATH':str(dotnet.parent)+os.pathsep+os.environ.get('PATH','')}
        run=subprocess.run(command,capture_output=True,text=True,env=env,timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        receipt=json.loads((out/'source-publication.json').read_text());package=out/receipt['package']
        manifest=json.loads((package/'manifest-build.json').read_text())
        self.assertEqual((manifest['mode'],manifest['runnable'],manifest['runtime_compile_required']),('source',False,True))
        self.assertEqual(manifest['tools']['metal']['backend'],'device-source-required')
        self.assertTrue(manifest['tools']['xlate']['built_from_repo_source'])
        for name,digest in manifest['tools']['xlate']['dll_sha16'].items():
            self.assertEqual(H((xlate.parent/name).read_bytes())[:16],digest)
        check_tool=load('owned_source_chain_check',out/'toolchain/dm/gfx/tools/botw_shader_build.py')
        tools=check_tool.Tools();tools.identity=manifest['tools']
        options=dict(manifest['options'],source_adapter_sha256=receipt['source_adapter_sha256'],
                     release_admission_sha256=receipt['release_admission_sha256'])
        self.assertEqual(tools.chain(options),manifest['chain'])
        self.assertEqual(len(manifest['results']),1);result=manifest['results'][0]
        self.assertEqual((result['status'],result['capture']),('source_ready',f.cap.summary()))
        self.assertEqual(set(p.name for p in package.iterdir()),{f.key+'_b.metal',f.key+'_b.json','manifest-build.json'})
        self.assertIn('vertex ',(package/(f.key+'_b.metal')).read_text())
        self.assertFalse(list(out.rglob('*.metallib')))
        self.assertFalse(receipt['device_compilation_tested']);self.assertFalse(receipt['guest_or_gpu_executed'])
        for kind in ('json','metal_source'):
            item=result['files'][kind];self.assertEqual(item['sha256'],H((package/item['path']).read_bytes()))
        self.assertEqual(receipt['manifest_sha256'],H((package/'manifest-build.json').read_bytes()))
        self.assertEqual(manifest['release_admission_sha256'],receipt['release_admission_sha256'])
        self.assertEqual(receipt['release_admission_sha256'],H((out/'admitted/release-admission.json').read_bytes()))
        self.assertEqual(receipt['input_manifest_sha256'],H((f.source/'inputs.json').read_bytes()))
        staged=json.loads((out/'toolchain/v8-stage-receipt.json').read_text())
        self.assertEqual(staged['original_files_verified'],374)
        expected_sources=self.publisher.source_toolchain_hashes(out/'toolchain')
        for name,expected in expected_sources.items():
            self.assertEqual(H((out/'toolchain'/name).read_bytes()),expected)
        self.assertEqual(upstream_shaders.verify_vendor(),before)
        self.assertEqual({p:H(Path(p).read_bytes()) for p in origin},origin)
        if os.environ.get('BOTW_SHADER_SOURCE_SMOKE_RECEIPT'):
            audit={'command':command,'returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr,'publication':receipt,
                'manifest':manifest,'stage':staged,'vendor_verified_files':before['verified_files'],'originals_byte_stable':True,
                'owned_program':'existing exact v8 EXIT; no resource instructions or scene facts','metal_compiler_or_gpu_invoked':False}
            Path(os.environ['BOTW_SHADER_SOURCE_SMOKE_RECEIPT']).write_text(json.dumps(audit,indent=2)+'\n')


if __name__=='__main__':unittest.main()
