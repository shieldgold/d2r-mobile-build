"""Release admission over complete upstream tools; synthetic owned inputs, no GPU."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.botw_native import upstream_shaders as adapter
from test_botw_upstream_shaders import inputs
try:
    from scripts.botw_native import shader_bindings as gate
except ImportError:
    gate=None

H=lambda b:hashlib.sha256(b).hexdigest()
class ShaderReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(gate,'strict complete-tool release gate is absent')
        self.tmp=tempfile.TemporaryDirectory(prefix='botw-owned-release-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'inputs';row=inputs(self.source)
        self.caps=self.root/'captures';report=adapter.export_captures(self.source,self.caps);self.key=report['captures'][0]['key'];self.tool=adapter.load_tool();self.cap=self.tool.load_capture(str(self.caps),self.key)
        refl=b'{}';rf=H(refl)+'.reflection.json';(self.source/rf).write_bytes(refl)
        c1_name=H(self.cap.c1)+'.cbuf1';(self.source/c1_name).write_bytes(self.cap.c1)
        row.update(c1_sha256=H(self.cap.c1),c1_file=c1_name,c1_begin=self.cap.fields['c1_offset'],
                   c1_end=self.cap.fields['code_size'],c1_bytes=len(self.cap.c1),
                   reflection_sha256=H(refl),reflection_file=rf)
        self.manifest={'schema':1,'records':[row]};self.write(self.source/'inputs.json',self.manifest)
        self.facts=self.root/'facts';self.facts.mkdir();self.established=self.root/'established';self.established.mkdir()
        self.doc={'schema':'botw-shader-facts-v1','key':self.key,'sha256':self.cap.sha256,'stage':'vertex','provenance':'reviewed owned fixture; no game evidence','constant_buffers':{'3':{'binding':0}},'device_state':{'window_origin_mode':'upper_left','depth_mode':'near_is_zero'}}
        self.fact_path=self.facts/(self.key+'.facts.json');self.write(self.fact_path,self.doc);(self.established/self.fact_path.name).write_bytes(self.fact_path.read_bytes())
        self.probe_path=self.root/'manifest-probe.json';self.probe={'schema':self.tool.MANIFEST_SCHEMA,'mode':'probe','captures':str(self.caps),'results':[{'key':self.key,'status':'probe','capture':self.cap.summary(),'errors':[],'conflicts':[]}]};self.write(self.probe_path,self.probe)
        self.derive_path=self.root/'derive-report.json';self.derive={'schema':'botw-shader-derive-report-v1','rules':'botw-derive-v5','probe_manifest':str(self.probe_path),'captures':str(self.caps),'established':str(self.established),'derived':0,'established_copied':1,'skipped':{},'unresolved':{},'established_differences':{},'integer_output_candidates':{},'assumed_texture_types':{}};self.write(self.derive_path,self.derive)
        self.evidence_dir=self.root/'evidence';self.evidence_dir.mkdir();self.review={'schema':'botw-shader-facts-review-v1','profile':'botw-switch-base-v0','status':'approved','capture_sha256':self.cap.sha256,'facts_sha256':H(self.fact_path.read_bytes()),'source_record_indices':[0],'reflection_sha256':[H(refl)],'fields':['constant_buffers.3','device_state'],'provenance':'owned fixture review only, not a real approval'}
        self.evidence={'schema':'botw-shader-release-evidence-v1','profile':'botw-switch-base-v0','input_manifest_sha256':H((self.source/'inputs.json').read_bytes()),'captures':{}}
        self.evidence_path=self.evidence_dir/'evidence.json';self.sync_review()
    def write(self,p,d):p.write_text(json.dumps(d,sort_keys=True)+'\n')
    def sync_review(self):
        raw=(json.dumps(self.review,sort_keys=True)+'\n').encode();name=H(raw)+'.review.json';(self.evidence_dir/name).write_bytes(raw);self.evidence['captures'][self.key]={'file':name,'sha256':H(raw)};self.write(self.evidence_path,self.evidence)
    def check(self):return gate.check_release(self.tool,self.source,self.caps,self.facts,self.derive_path,self.evidence_path)
    def test_explicit_scoped_established_facts_pass_and_preserve_all_hashes(self):
        r=self.check();self.assertTrue(r['approved']);self.assertEqual(r['profile'],'botw-switch-base-v0');self.assertEqual(r['captures'][self.key]['facts_sha256'],H(self.fact_path.read_bytes()))
    def test_every_derive_risk_and_missing_or_mistyped_risk_table_refuses(self):
        for field in ('skipped','unresolved','established_differences','integer_output_candidates','assumed_texture_types'):
            for value in ({self.key:['unapproved']},[],None):
                d=copy.deepcopy(self.derive);d[field]=value;self.write(self.derive_path,d)
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.check()
            d=copy.deepcopy(self.derive);d.pop(field);self.write(self.derive_path,d)
            with self.assertRaises(ValueError):self.check()
        self.write(self.derive_path,self.derive)
    def test_derived_float_texture_or_19_device_defaults_cannot_become_authority(self):
        for provenance in ('botw-derive-v5: out=float-assumed','botw-derive-v5: tex-type=2d-assumed-where-queried','botw-derive-v5: device=upper_left,near_is_minus_w'):
            d=dict(self.doc,provenance=provenance);self.write(self.fact_path,d);(self.established/self.fact_path.name).write_bytes(self.fact_path.read_bytes());self.review['facts_sha256']=H(self.fact_path.read_bytes());self.sync_review()
            with self.subTest(provenance=provenance),self.assertRaises(ValueError):self.check()
    def test_approved_field_coverage_profile_and_source_variants_are_explicit(self):
        for field,value in [('profile','botw-1.9.0'),('status','pending'),('fields',[]),('source_record_indices',[]),('reflection_sha256',[])]:
            old=copy.deepcopy(self.review);self.review[field]=value;self.sync_review()
            with self.subTest(field=field),self.assertRaises(ValueError):self.check()
            self.review=old
        self.sync_review()
    def test_capture_facts_c1_reflection_and_review_tampering_refuse(self):
        for p in (self.caps/(self.key+'.code'),self.fact_path,self.source/self.manifest['records'][0]['reflection_file'],self.evidence_dir/self.evidence['captures'][self.key]['file']):
            raw=p.read_bytes();p.write_bytes(raw+b'x')
            with self.subTest(path=p.name),self.assertRaises(ValueError):self.check()
            p.write_bytes(raw)
        self.manifest['records'][0]['c1_sha256']='0'*64;self.write(self.source/'inputs.json',self.manifest);self.evidence['input_manifest_sha256']=H((self.source/'inputs.json').read_bytes());self.sync_review()
        with self.assertRaises(ValueError):self.check()
    def test_probe_identity_stage_and_all_batch_keys_refuse_mismatch(self):
        for field,value in [('schema','wrong'),('mode','build'),('captures',str(self.root/'other'))]:
            doc=copy.deepcopy(self.probe);doc[field]=value;self.write(self.probe_path,doc)
            with self.subTest(field=field),self.assertRaises(ValueError):self.check()
        self.write(self.probe_path,self.probe)
        self.derive['derived']=1;self.write(self.derive_path,self.derive)
        with self.assertRaises(ValueError):self.check()
    def test_conflicting_reflections_cannot_silently_choose_first(self):
        row=dict(self.manifest['records'][0]);raw=b'{"other":1}';rf=H(raw)+'.reflection.json';(self.source/rf).write_bytes(raw);row.update(reflection_sha256=H(raw),reflection_file=rf);self.manifest['records'].append(row);self.write(self.source/'inputs.json',self.manifest);self.evidence['input_manifest_sha256']=H((self.source/'inputs.json').read_bytes());self.sync_review()
        with self.assertRaises(ValueError):self.check()
        self.review['source_record_indices']=[0,1];self.review['reflection_sha256']=sorted([self.manifest['records'][0]['reflection_sha256'],H(raw)]);self.sync_review();self.assertTrue(self.check()['approved'])
    def test_prepare_publishes_only_after_whole_admission_and_uses_detached_bytes(self):
        out=self.root/'ready';r=gate.prepare_release(self.tool,self.source,self.caps,self.facts,self.derive_path,self.evidence_path,out);self.assertTrue(r['approved']);self.assertEqual((out/'facts'/self.fact_path.name).read_bytes(),self.fact_path.read_bytes())
        self.fact_path.write_text('changed');self.assertNotEqual((out/'facts'/self.fact_path.name).read_bytes(),self.fact_path.read_bytes())
        with self.assertRaises(FileExistsError):gate.prepare_release(self.tool,self.source,self.caps,self.facts,self.derive_path,self.evidence_path,out)
        denied=self.root/'denied'
        with self.assertRaises(ValueError):gate.prepare_release(self.tool,self.source,self.caps,self.facts,self.derive_path,self.evidence_path,denied)
        self.assertFalse(denied.exists())
    def test_symlink_review_traversal_and_bad_scalar_types_refuse(self):
        self.evidence['captures'][self.key]={'file':'../escape.review.json','sha256':'a'*64};self.write(self.evidence_path,self.evidence)
        with self.assertRaises(ValueError):self.check()
        self.sync_review();p=self.evidence_dir/self.evidence['captures'][self.key]['file'];raw=p.read_bytes();p.unlink();elsewhere=self.root/'same';elsewhere.write_bytes(raw);p.symlink_to(elsewhere)
        with self.assertRaises(ValueError):self.check()
    def test_probe_integer_fields_do_not_accept_boolean_or_float_substitutes(self):
        for field in ('version','stage','control_size','code_size','code_offset','c1_offset','c1_size'):
            for value in (True,float(self.probe['results'][0]['capture'][field])):
                doc=copy.deepcopy(self.probe);doc['results'][0]['capture'][field]=value;self.write(self.probe_path,doc)
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.check()
        self.write(self.probe_path,self.probe)
    def test_source_c1_offsets_are_bound_to_the_control_not_just_the_slice_digest(self):
        original=copy.deepcopy(self.manifest)
        for field in ('c1_begin','c1_end','c1_bytes'):
            self.manifest=copy.deepcopy(original);self.manifest['records'][0][field]=0
            self.write(self.source/'inputs.json',self.manifest)
            self.evidence['input_manifest_sha256']=H((self.source/'inputs.json').read_bytes());self.sync_review()
            with self.subTest(field=field),self.assertRaises(ValueError):self.check()
    def test_raw_upstream_tools_remain_available_but_no_false_strict_build(self):
        self.assertTrue(callable(self.tool.main));self.assertFalse(hasattr(self.tool,'check_release'))
        self.assertTrue(hasattr(adapter,'strict_release_build'))
        self.derive['assumed_texture_types']={self.key:[8]};self.write(self.derive_path,self.derive)
        out=self.root/'build-output'
        with self.assertRaises(ValueError):adapter.strict_release_build(self.source,self.caps,self.facts,self.derive_path,self.evidence_path,out,xlate='not-executed',spirv_cross='not-executed')
        self.assertFalse(out.exists())
    def test_strict_build_calls_complete_upstream_only_with_the_admitted_snapshot(self):
        out=self.root/'strict-build'
        def build(arguments):
            self.assertEqual(arguments,['build',str(out/'captures'),'--facts',str(out/'facts'),
                '--out',str(out/'build'),'--jobs','2','--xlate','owned-translator','--spirv-cross','owned-cross'])
            saved=(out/'facts'/self.fact_path.name).read_bytes()
            self.fact_path.write_text('source changed after admission')
            self.assertEqual((out/'facts'/self.fact_path.name).read_bytes(),saved)
            self.tool.load_facts(str(out/'facts'/self.fact_path.name),
                self.tool.load_capture(str(out/'captures'),self.key))
            return 1  # A real upstream failure must propagate, never become approval.
        with mock.patch.object(self.tool,'main',side_effect=build) as called:
            result=adapter.strict_release_build(self.source,self.caps,self.facts,self.derive_path,
                self.evidence_path,out,xlate='owned-translator',spirv_cross='owned-cross',jobs=2)
        self.assertEqual(result,1);called.assert_called_once()

if __name__=='__main__':unittest.main()
