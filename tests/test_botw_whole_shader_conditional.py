"""Conditional candidates stay distinct from facts; owned CPU metadata/source only."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/botw_upstream_host'
def digest(b):return hashlib.sha256(b).hexdigest()
def encoded(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'Foundation owned CPU contract')
class ConditionalShaders(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(prefix='botw-conditional-');cls.addClassCleanup(cls.tmp.cleanup)
  cls.work=Path(cls.tmp.name).resolve();shutil.copytree(C/'upstream',cls.work/'upstream')
  run=subprocess.run(['patch','--fuzz=0','-p1','-i',str(C/'patches/portable-shader-source.patch')],cwd=cls.work/'upstream',capture_output=True,text=True)
  if run.returncode:raise AssertionError(run.stdout+run.stderr)
  run=subprocess.run(['patch','--fuzz=0','-p1','-i',str(C/'patches/base-v0-image-bank.patch')],cwd=cls.work/'upstream',capture_output=True,text=True)
  if run.returncode:raise AssertionError(run.stdout+run.stderr)
  run=subprocess.run(['patch','--fuzz=0','-p1','-i',str(C/'patches/conditional-shader-runtime.patch')],cwd=cls.work/'upstream',capture_output=True,text=True)
  if run.returncode:raise AssertionError(run.stdout+run.stderr)
  nvn=cls.work/'upstream/dm/gfx/nvn';cls.binary=cls.work/'probe'
  run=subprocess.run(['clang','-fobjc-arc','-fblocks','-Werror','-Wno-deprecated-declarations','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(nvn),'-I'+str(nvn/'include'),'-I'+str(C/'adapter'),str(C/'tests/whole_shader_conditional_probe.m'),str(C/'adapter/whole_shader_conditional.m'),str(nvn/'botw_shader_manifest.m'),str(nvn/'botw_shader_runtime.m'),str(nvn/'nvn_refl.m'),'-framework','Foundation','-framework','Metal','-o',str(cls.binary)],capture_output=True,text=True)
  if run.returncode:raise AssertionError(run.stdout+run.stderr)
  source=(nvn/'nvn_mtl.m').read_text();start=source.index('/* WHOLE_CONDITIONAL_HELPERS_BEGIN');end=source.index('/* WHOLE_CONDITIONAL_HELPERS_END */',start)
  (cls.work/'conditional_runtime_helpers.inc').write_text(source[start:end])
  runtime=(nvn/'botw_shader_runtime.m').read_text().replace('BotwShaderManifest *botw_shader_runtime_manifest(void) { latch(); return g_manifest; }','')
  (cls.work/'owned-runtime.m').write_text(runtime)
  cls.runtime_binary=cls.work/'runtime-probe'
  run=subprocess.run(['clang','-fobjc-arc','-fblocks','-Werror','-Wno-deprecated-declarations','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(nvn),'-I'+str(nvn/'include'),'-I'+str(C/'adapter'),'-I'+str(cls.work),str(C/'tests/whole_shader_conditional_runtime_probe.m'),str(C/'adapter/whole_shader_conditional.m'),str(nvn/'botw_shader_manifest.m'),str(cls.work/'owned-runtime.m'),str(nvn/'nvn_refl.m'),'-framework','Foundation','-framework','Metal','-o',str(cls.runtime_binary)],capture_output=True,text=True)
  if run.returncode:raise AssertionError(run.stdout+run.stderr)
 def package(self,*,stage=0,variants=((0,0),),sha=None,mutate=None):
  folder=Path(tempfile.mkdtemp(dir=self.work))/'botw-c-0123456789ab';folder.mkdir()
  sha=sha or '0123456789abcdef'+'0'*48;capture={'key':sha[:16],'sha256':sha,'stage_name':'fragment' if stage else 'vertex','stage':stage}
  results=[]
  for index,(origin,depth) in enumerate(variants):
   contract={'schema':'botw-shader-conditional-translation-contract-v1','profile':'botw-switch-base-v0','chain':'0123456789ab','capture':capture,'scope':'noattr-vs-output0-fs-nonqueried-v1','requirements':{'device':{'window_origin_mode':origin,'depth_mode':depth},'attributes':[],'texture_queries':[],'images':[],'storage_buffers':[],'fragment_output0':{'type':'float','nvn_formats':[37,41,46,154]} if stage else None},'proofs':{'binding_contract_sha256':'b'*64,'subset_sha256':'c'*64},'translation':{'source_adapter_sha256':'a'*64,'job_sha256':str(index)*64}}
   reflection={'schema':'botw-shader-conditional-reflection-v1','conditional_contract_sha256':'','route':'B','runnable':False,'runtime_compile_required':True,'chain':'0123456789ab','key':sha[:16]+'_b','dm_ctl':sha[:16],'sha256':sha,'stage':'ps' if stage else 'vs','entry':'main0','unknowns':[],'conflicts':[],'notes':[],'bindings':[],'vertex_inputs':[],'drv_reads':{},'drv_unfillable':[],'c1':'','raster':{'colors':[0] if stage else []},'device_state':{'window_origin_mode':'upper_left' if origin else 'lower_left','depth_mode':'near_is_zero' if depth else 'near_is_minus_w'}}
   source=b'// owned source; no game evidence\nvertex float4 main0(){return float4(0);}\n'
   if mutate:mutate(contract,reflection,index)
   cb=encoded(contract);variant=digest(cb);reflection['conditional_contract_sha256']=variant;rb=encoded(reflection)
   files={}
   for kind,suffix,data in [('contract','.contract.json',cb),('json','.json',rb),('metal_source','.metal',source)]:
    name=variant+suffix;(folder/name).write_bytes(data);files[kind]={'path':name,'sha256':digest(data)}
   results.append({'variant_sha256':variant,'status':'conditional_source_ready','runnable':False,'runtime_compile_required':True,'capture':capture,'files':files,'errors':[],'unknowns':[],'conflicts':[]})
  manifest={'schema':'botw-shader-conditional-source-manifest-v1','mode':'conditional_source','output':'.','runnable':False,'runtime_compile_required':True,'profile':'botw-switch-base-v0','chain':'0123456789ab','source_adapter_sha256':'a'*64,'conditional_admission_sha256':'d'*64,'results':results}
  path=folder/'manifest-build.json';path.write_bytes(encoded(manifest));return path,sha,manifest
 def run_case(self,path,sha,*,stage=0,known=1,origin=0,depth=0,color_known=1,fmt=37,success=True):
  r=subprocess.run([str(self.binary),str(path),sha,str(stage),str(known),str(origin),str(depth),str(color_known),str(fmt),'4'],capture_output=True,text=True,timeout=10)
  if success:self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  else:self.assertIn(r.returncode,(10,12),r.stdout+r.stderr)
  return r
 def test_exact_candidate_selected_and_both_mode_changes_reselect(self):
  p,s,_=self.package(variants=((0,0),(0,1),(1,0),(1,1)));self.run_case(p,s)
 def test_zero_and_multiple_matches_are_failures(self):
  p,s,_=self.package();self.run_case(p,s,origin=1,success=False)
  p,s,_=self.package(variants=((0,0),(0,0)));self.run_case(p,s,success=False)
 def test_unknown_device_and_effective_attachment_fail(self):
  p,s,_=self.package(stage=1);self.run_case(p,s,stage=1,known=0,success=False);self.run_case(p,s,stage=1,color_known=0,success=False);self.run_case(p,s,stage=1,fmt=999,success=False);self.run_case(p,s,stage=1)
 def test_full_capture_hash_and_stage_are_required(self):
  p,s,_=self.package();self.run_case(p,s[:-1]+'1',success=False);self.run_case(p,s,stage=1,success=False)
 def test_contract_source_and_reflection_digests_are_owned(self):
  for kind in ('contract','json','metal_source'):
   p,s,m=self.package();f=p.parent/m['results'][0]['files'][kind]['path'];f.write_bytes(f.read_bytes()+b' ');self.run_case(p,s,success=False)
 def test_no_query_attribute_image_storage_or_unknown_driver_is_admitted(self):
  for field in ('attributes','texture_queries','images','storage_buffers'):
   p,s,_=self.package(mutate=lambda c,r,i:c['requirements'].__setitem__(field,[0]));self.run_case(p,s,success=False)
  p,s,_=self.package(mutate=lambda c,r,i:r.__setitem__('drv_unfillable',['c0+0x610']));self.run_case(p,s,success=False)
 def test_independent_schema_cannot_claim_game_facts_or_compiled_readiness(self):
  for field,value in (('facts_sha256','f'*64),('schema','botw-shader-reflection-v1'),('runnable',True),('unknowns',['missing'])):
   p,s,_=self.package(mutate=lambda c,r,i:r.__setitem__(field,value));self.run_case(p,s,success=False)
 def test_sibling_paths_and_all_directory_components_reject_symlinks(self):
  p,s,m=self.package();f=p.parent/m['results'][0]['files']['metal_source']['path'];actual=f.with_suffix('.actual');f.rename(actual);f.symlink_to(actual);self.run_case(p,s,success=False)
  p,s,_=self.package();alias=p.parent.parent.with_name(p.parent.parent.name+'-alias');alias.symlink_to(p.parent.parent,target_is_directory=True);self.run_case(alias/p.parent.name/p.name,s,success=False)
 def test_same_short_key_different_full_hash_never_shares_cache_identity(self):
  p,s,_=self.package();a=self.run_case(p,s).stdout;p,t,_=self.package(sha=s[:-1]+'1');b=self.run_case(p,t).stdout;self.assertNotEqual(a,b)

 def test_actual_draw_helpers_reject_compiler_time_mode_or_descriptor_changes(self):
  for mode in (0,1,2,3):
   p,s,_=self.package(variants=((0,0),(0,1),(1,0),(1,1)))
   r=subprocess.run([str(self.runtime_binary),str(p),s,str(mode)],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)

 def test_actual_draw_requires_realized_same_pass_output0_format(self):
  for mode in (4,6,7):
   p,s,m=self.package();f,_,fm=self.package(stage=1)
   for result in fm['results']:
    for file in result['files'].values():shutil.copy(f.parent/file['path'],p.parent/file['path'])
   m['results']+=fm['results'];p.write_bytes(encoded(m))
   r=subprocess.run([str(self.runtime_binary),str(p),s,str(mode)],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_actual_function_creation_failure_is_terminal(self):
  p,s,_=self.package();r=subprocess.run([str(self.runtime_binary),str(p),s,'5'],capture_output=True,text=True,timeout=10)
  self.assertEqual(r.returncode,50,r.stdout+r.stderr)

 def test_malformed_optional_reflection_cannot_escape_normal_parser(self):
  for field,value in (('fetch',{'calls':[None]}),('raster',{'colors':[],'discard':[]}),('vertex_inputs',[None]),('threadgroup_size',[None,None,None])):
   p,s,_=self.package(mutate=lambda c,r,i:r.__setitem__(field,value));self.run_case(p,s,success=False)

 def test_optional_original_capture_stage_must_agree_with_stage_name(self):
  p,s,_=self.package(mutate=lambda c,r,i:c['capture'].__setitem__('stage',5));self.run_case(p,s,success=False)

 def test_actual_program_and_shader_caches_keep_full_capture_and_variant_identity(self):
  p,s,m=self.package(variants=((0,0),(0,1),(1,0),(1,1)))
  f,_,fm=self.package(sha=s[:-1]+'1')
  for result in fm['results']:
   for file in result['files'].values():shutil.copy(f.parent/file['path'],p.parent/file['path'])
  m['results']+=fm['results'];p.write_bytes(encoded(m))
  for mode in (8,9):
   r=subprocess.run([str(self.runtime_binary),str(p),s,str(mode)],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
