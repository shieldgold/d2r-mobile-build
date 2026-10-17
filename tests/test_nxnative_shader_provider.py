"""Actual native integration and original conditional provider, owned CPU ports."""
import hashlib
import copy
import struct
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import test_botw_whole_shader_conditional as owned
ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/nxnative_base_v0'
OLD=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('NX_PROVIDER_BASELINE')=='1'
PATCH=C/'patches/base-v0-conditional-shader-provider.patch'

def function(source, marker):
    start=source.index(marker);brace=source.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]

def run(command,**kw):
    p=subprocess.run(command,capture_output=True,text=True,**kw)
    if p.returncode:raise AssertionError(p.stdout+p.stderr)
    return p

def _prepare_runtime(cls):
    cls.old=cls.work/'old';shutil.copytree(OLD/'upstream',cls.old)
    for patch in ('portable-shader-source.patch','base-v0-image-bank.patch','conditional-shader-runtime.patch'):
        run(['patch','--fuzz=0','--batch','-p1','-i',str(OLD/'patches'/patch)],cwd=cls.old)
    oldnvn=cls.old/'dm/gfx/nvn'
    for name in ('botw_shader.h','botw_shader_manifest.h','botw_shader_manifest.m','botw_shader_runtime.h','botw_shader_runtime.m'):
        shutil.copyfile(oldnvn/name,cls.nvn/name)
    start=cls.mtl.index('/* NX_BASE_V0_CONDITIONAL_HELPERS_BEGIN')
    end=cls.mtl.index('/* NX_BASE_V0_CONDITIONAL_HELPERS_END */',start)
    (cls.work/'conditional_helpers.inc').write_text(cls.mtl[start:end].replace('nx_base_shader_provider()', 'fixture_provider()'))
    first=cls.mtl.index('/* NX_BASE_V0_ATTACHMENT_FORMAT_BEGIN */')
    last=cls.mtl.index('/* NX_BASE_V0_ATTACHMENT_FORMAT_END */',first)
    (cls.work/'attachment_format.inc').write_text(cls.mtl[first:last])
    hle=(cls.nvn/'hle_nvn.c').read_text();first=hle.index('// ---------- Programs ----------');last=hle.index('// ---------- Sync / events ----------',first)
    (cls.work/'program_entries.inc').write_text(hle[first:last])
    cls.runtime=cls.work/'runtime'
    _compile_runtime(cls,C/'tests/shader_provider_probe.m',cls.runtime)

def _compile_runtime(cls,probe,binary):
    sources=[probe,C/'adapter/base_v0_shader_provider.m',
             OLD/'adapter/whole_shader_conditional.m',OLD/'adapter/whole_shader_bundle_publication.m',
             OLD/'adapter/whole_shader_publication.m',cls.nvn/'botw_shader_manifest.m',cls.nvn/'botw_shader_runtime.m',cls.nvn/'nvn_refl.m',cls.native/'dm/gfx/tex/nvn_fmt_mtl.c']
    run(['clang','-DNXNATIVE_NORMAL_POLICY=1','-DNX_PROVIDER_RUNTIME=1','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror','-Wno-unused-function',
         '-Wno-deprecated-declarations','-Wno-ignored-qualifiers','-fsanitize=address,undefined','-fno-sanitize-recover=all',
         '-I'+str(cls.work),'-I'+str(C/'adapter'),'-I'+str(OLD/'adapter'),'-I'+str(cls.nvn),'-I'+str(cls.nvn/'include'),
         *map(str,sources),'-framework','Foundation','-framework','Metal','-o',str(binary)])

def _prepare_origin_encoding(cls):
    # Execute the complete native setter, viewport block, winding statement and
    # final guard in their actual production order, with CPU encoder ports.
    hle=(cls.nvn/'hle_nvn.c').read_text()
    draw=function(cls.mtl,'static void draw(const p_draw *dw, const p_draw_idx *di) {')
    viewport=function(draw,'    { MTLViewport v =')
    winding=next(line for line in draw.splitlines() if line.startswith('    enc_r_winding(en,'))
    guard_start=draw.index('    conditional_draw_guard conditional_guard ')
    guard='\n'.join(draw[guard_start:].splitlines()[:2])
    assert draw.count('atomic_load_explicit(&g_origin_ll,')==2
    ordered=sorted(((draw.index(viewport),viewport),(draw.index(winding),winding),(guard_start,guard)))
    body=[]
    for _,part in ordered:
        body.append(part)
        if part==viewport:body.append('    if(which==18)return 0; /* owned early encoder exit */')
    source=(C/'tests/shader_provider_probe.m').read_text()
    source=source.replace('const prog_t *bst[5];','const prog_t *bst[5];int has_vp;double vp[16][4],dr[2];poly_st poly;')
    setter=function(hle,'NVN(nvnDeviceSetWindowOriginMode,')
    origin=function(cls.mtl,'void mtl_set_origin_lower_left(')
    support=r"""
#include <errno.h>
static _Atomic int g_origin_ll;
"""+origin+'\n'+setter+r"""
static pthread_mutex_t owned_aba_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t owned_aba_cv=PTHREAD_COND_INITIALIZER;
static pthread_t owned_aba_thread;
static int owned_aba_stage,owned_aba_rmu_held,owned_encoding_mode;
static unsigned owned_viewport_calls,owned_winding_calls;
static MTLViewport owned_viewport;
static MTLWinding owned_winding;
static void *owned_origin_setter(void *unused){
    (void)unused;int status=pthread_mutex_trylock(&g_rmu);
    if(!status)pthread_mutex_unlock(&g_rmu);
    pthread_mutex_lock(&owned_aba_mu);owned_aba_rmu_held=status==EBUSY;owned_aba_stage=1;
    pthread_cond_broadcast(&owned_aba_cv);pthread_mutex_unlock(&owned_aba_mu);
    h_nvnDeviceSetWindowOriginMode((NVNdevice *)&device,NVN_WINDOW_ORIGIN_MODE_UPPER_LEFT);
    h_nvnDeviceSetWindowOriginMode((NVNdevice *)&device,NVN_WINDOW_ORIGIN_MODE_LOWER_LEFT);
    pthread_mutex_lock(&owned_aba_mu);owned_aba_stage=2;pthread_cond_broadcast(&owned_aba_cv);
    pthread_mutex_unlock(&owned_aba_mu);return NULL;
}
static void enc_r_viewport(id en,MTLViewport value){
    (void)en;owned_viewport=value;owned_viewport_calls++;
    if(owned_encoding_mode==15)
        h_nvnDeviceSetWindowOriginMode((NVNdevice *)&device,NVN_WINDOW_ORIGIN_MODE_LOWER_LEFT);
    if(owned_encoding_mode==16||owned_encoding_mode==18){
        if(pthread_create(&owned_aba_thread,NULL,owned_origin_setter,NULL))abort();
        pthread_mutex_lock(&owned_aba_mu);
        while(!owned_aba_stage)pthread_cond_wait(&owned_aba_cv,&owned_aba_mu);
        pthread_mutex_unlock(&owned_aba_mu);
    }
}
static void enc_r_winding(id en,MTLWinding value){(void)en;owned_winding=value;owned_winding_calls++;}
static int owned_actual_mode_encoding(NvProg *pr,const botw_conditional_draw_state *state,int which){
    int conditional_mode=which!=17;botw_conditional_draw_state conditional_snapshot=*state;
    id en=nil;long vo=0;double vs=1;NSUInteger tw=1,th=1;owned_encoding_mode=which;
    S.poly.front=NVN_FRONT_FACE_CCW;
    if(which==15)h_nvnDeviceSetWindowOriginMode((NVNdevice *)&device,NVN_WINDOW_ORIGIN_MODE_UPPER_LEFT);
"""+'\n'.join(body)+r"""
    if(which==15)return 91; /* A late guard accepted stale viewport encoding. */
    if(which==16){
        pthread_mutex_lock(&owned_aba_mu);int held=owned_aba_rmu_held,stage=owned_aba_stage;
        pthread_mutex_unlock(&owned_aba_mu);
        CHECK(conditional_guard.held&&held&&stage==1); /* setter is still outside the guarded draw */
    }
    CHECK(owned_viewport_calls==1&&owned_winding_calls==1&&owned_viewport.height==-1&&owned_winding==MTLWindingClockwise);
    if(which==17)CHECK(!conditional_guard.held);
    conditional_draw_unlock(&conditional_guard); /* owned actual-draw completion */
    if(which==16){CHECK(!pthread_join(owned_aba_thread,NULL));CHECK(owned_aba_stage==2&&device.origin==NVN_WINDOW_ORIGIN_MODE_LOWER_LEFT);}
    return 0;
}
static int owned_origin_case(NvProg *pr,const botw_conditional_draw_state *state,int which){
    int status=owned_actual_mode_encoding(pr,state,which);
    if(which==18){CHECK(!status);CHECK(!pthread_join(owned_aba_thread,NULL));CHECK(owned_aba_rmu_held&&owned_aba_stage==2);}
    CHECK(!pthread_mutex_trylock(&g_rmu));pthread_mutex_unlock(&g_rmu);
    return status;
}
"""
    source=source.replace('static NSDictionary *json(',support+'\nstatic NSDictionary *json(',1)
    marker=' botw_conditional_draw_state snapshot;NvProg *resolved=conditional_program_for_draw(&snapshot);CHECK(resolved&&compile_calls==2);'
    assert source.count(marker)==1
    source=source.replace(marker,' if(mutation>=15&&mutation<=18)h_nvnDeviceSetWindowOriginMode((NVNdevice *)&device,NVN_WINDOW_ORIGIN_MODE_LOWER_LEFT);\n'+marker+'\n if(mutation>=15&&mutation<=18)return owned_origin_case(resolved,&snapshot,(int)mutation);')
    probe=cls.work/'actual_mode_encoding_probe.m';probe.write_text(source)
    binary=cls.work/'actual-mode-encoding';_compile_runtime(cls,probe,binary)
    return binary

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'Foundation owned CPU shader provider contract')
class NxnativeShaderProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='nx-provider-');cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name).resolve();cls.native=cls.work/'native';shutil.copytree(C/'upstream',cls.native)
        for name in ('normal-entry.patch','base-v0-texture-view.patch','normal-hle-crash.patch','horizon-address-signal.patch','normal-runtime-diagnostics.patch'):
            run(['patch','--fuzz=0','--batch','-p1','-i',str(C/'patches'/name)],cwd=cls.native)
        if not BASELINE:run(['patch','--fuzz=0','--batch','-p1','-i',str(PATCH)],cwd=cls.native)
        nvn=cls.native/'dm/gfx/nvn';cls.nvn=nvn
        mtl=(nvn/'nvn_mtl.m').read_text();cls.mtl=mtl
        (cls.work/'bind_stages.inc').write_text(function(mtl,'static const prog_t *bind_stages('))
        (cls.work/'prewalk.inc').write_text(function(mtl,'static __unsafe_unretained NvProg *prog_get('))
        cls.bind=cls.work/'bind'
        run(['clang','-DNXNATIVE_NORMAL_POLICY=1','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-ignored-qualifiers',
             '-fsanitize=address,undefined','-fno-sanitize-recover=all',*(['-DNX_BASE_V0_PROVIDER=1'] if not BASELINE else []),
             '-I'+str(cls.work),'-I'+str(nvn),'-I'+str(nvn/'include'),'-I'+str(OLD/'adapter'),
             str(C/'tests/shader_provider_probe.m'),'-framework','Foundation','-framework','Metal','-o',str(cls.bind)])
        if not BASELINE:_prepare_runtime(cls)

    def package(self):
        p,sha,manifest=owned.ConditionalShaders.package(self,variants=((0,0),(0,1),(1,0),(1,1)))
        f,fs,fmanifest=owned.ConditionalShaders.package(self,stage=1,variants=((0,0),(0,1),(1,0),(1,1)),sha='123456789abcdef0'+'0'*48)
        for result in fmanifest['results']:
            for item in result['files'].values():shutil.copyfile(f.parent/item['path'],p.parent/item['path'])
            metal=p.parent/result['files']['metal_source']['path'];raw=metal.read_bytes().replace(b'vertex ',b'fragment ')
            metal.write_bytes(raw);result['files']['metal_source']['sha256']=hashlib.sha256(raw).hexdigest()
        manifest['results']+=fmanifest['results'];p.write_bytes(owned.encoded(manifest))
        rows=[];admission={'captures':{}}
        for st,full in enumerate((sha,fs)):
            results=[r for r in manifest['results'] if r['capture']['stage']==st]
            variants=[r['variant_sha256'] for r in results]
            admission['captures'][full[:16]]={'capture_sha256':full,'stage_name':'fragment' if st else 'vertex','variants':variants}
            row={'capture_key':full[:16],'capture_sha256':full,'native_key':str(st+1)*16,'native_sha256':str(st+1)*64,
                 'version':9,'stage':st,'stage_name':'fragment' if st else 'vertex','control_size':2001,'native_hashed_control_size':2001,
                 'code_size':16,'code_offset':0,'c1_offset':16,'c1_size':0,'status':'conditional_reference_only','conditional_modes':[]}
            for r in results:
                contract=json.loads((p.parent/r['files']['contract']['path']).read_bytes());mode=contract['requirements']['device']
                row['conditional_modes'].append(dict(mode,variant_sha256=r['variant_sha256'],contract_sha256=r['variant_sha256']))
            rows.append(row)
        control=bytearray(2001);struct.pack_into('<I',control,0x20,2001);struct.pack_into('<I',control,0x704,16)
        for st,row in enumerate(rows):
            code=bytes([st])+bytes(15);full=hashlib.sha256(control+code).hexdigest();row['native_key']=full[:16];row['native_sha256']=full
        bridge={'schema':'nxnative-base-v0-shader-key-bridge-v1','profile':'botw-switch-base-v0','game_version':'1.0.0','rows':rows}
        b=p.parent/'bridge.json';a=p.parent/'admission.json';b.write_bytes(owned.encoded(bridge));a.write_bytes(owned.encoded(admission));return p,b,a,bridge

    def runtime_case(self,mode,*,mutate=None,expected=0,driver=None):
        p,b,a,bridge=self.package()
        if driver:
            manifest=json.loads(p.read_bytes())
            for result in manifest['results']:
                path=p.parent/result['files']['json']['path'];reflection=json.loads(path.read_bytes())
                if driver=='missing':reflection.pop('drv_reads')
                else:reflection['drv_reads']={'2':{'vec4_offsets':[32],'dynamic':False}} if driver=='nonempty' else []
                raw=owned.encoded(reflection);path.write_bytes(raw);result['files']['json']['sha256']=hashlib.sha256(raw).hexdigest()
            p.write_bytes(owned.encoded(manifest))
        if mutate:mutate(bridge);b.write_bytes(owned.encoded(bridge))
        r=subprocess.run([str(self.runtime),str(p),str(b),str(a),str(mode)],capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,expected,r.stdout+r.stderr)

    def test_composition_remains_logical_until_actual_draw(self):
        p=subprocess.run([str(self.bind),'composition'],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_prewalk_does_not_select_or_compile(self):
        p=subprocess.run([str(self.bind),'prewalk'],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_complete_provider_and_full_tuple_cache(self):self.runtime_case(0)

    def test_changed_origin_is_rejected_before_any_mode_dependent_encode(self):
        if not hasattr(type(self),'origin_encoding'):type(self).origin_encoding=_prepare_origin_encoding(type(self))
        original=self.runtime
        try:
            self.runtime=self.origin_encoding
            self.runtime_case(15,expected=50)
        finally:self.runtime=original

    def test_origin_setter_waits_through_viewport_winding_and_early_exit(self):
        if not hasattr(type(self),'origin_encoding'):type(self).origin_encoding=_prepare_origin_encoding(type(self))
        original=self.runtime
        try:
            self.runtime=self.origin_encoding
            for mode in (16,17,18):self.runtime_case(mode)
        finally:self.runtime=original


    def test_all_four_modes_resolve_distinct_full_tuple(self):self.runtime_case(8)

    def test_device_or_registry_changed_during_source_compile_is_rejected(self):
        for mode in (1,2,3):self.runtime_case(mode)

    def test_final_attachment_requires_realized_same_effective_format(self):
        for mode in (4,5):self.runtime_case(mode)

    def test_source_compile_or_function_type_failure_is_terminal(self):
        for mode in (6,7):self.runtime_case(mode,expected=50)

    def test_composed_stage_unbind_keeps_vertex_identity(self):self.runtime_case(9)

    def test_duplicate_or_incomplete_bridge_rejected(self):
        for mutate in (lambda b:b['rows'].append(b['rows'][0]),lambda b:b['rows'][0]['conditional_modes'].pop(),lambda b:b.__setitem__('game_version','1.9.0'),lambda b:b['rows'][0]['conditional_modes'][2].__setitem__('window_origin_mode',True)):
            self.runtime_case(10,mutate=mutate)

    def test_complete_program_identity_transaction_and_freed_control(self):
        hle=(self.nvn/'hle_nvn.c').read_text();start=hle.index('// ---------- Programs ----------');end=hle.index('// ---------- Sync / events ----------',start)
        (self.work/'program_entries.inc').write_text(hle[start:end])
        binary=self.work/'program'
        run(['clang','-DNXNATIVE_NORMAL_POLICY=1','-DNX_PROVIDER_PROGRAM=1','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-deprecated-declarations',
             '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(self.work),'-I'+str(C/'adapter'),'-I'+str(OLD/'adapter'),
             '-I'+str(self.nvn),'-I'+str(self.nvn/'include'),str(C/'tests/shader_provider_probe.m'),
             '-framework','Foundation','-framework','Metal','-o',str(binary)])
        result=subprocess.run([str(binary),'identity'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_cached_tuple_does_not_bypass_full_capture_identity(self):self.runtime_case(11,expected=50)

    def test_effective_view_format_matches_realized_attachment(self):self.runtime_case(12)

    def test_identity_only_setshaders_and_prewalk_do_not_admit_variant(self):
        def identity(bridge):
            row=copy.deepcopy(bridge['rows'][0]);row['capture_sha256']='3'*64;row['capture_key']='3'*16;row['status']='only_identity';row['conditional_modes']=[]
            control=bytearray(2001);struct.pack_into('<I',control,0x20,2001);struct.pack_into('<I',control,0x704,16)
            full=hashlib.sha256(control+bytes([2])+bytes(15)).hexdigest();row['native_sha256']=full;row['native_key']=full[:16];bridge['rows'].append(row)
        self.runtime_case(14,mutate=identity,expected=50)

    def test_driver_constants_require_explicit_empty_read_set(self):
        for driver in ('nonempty','missing','wrong'):
            self.runtime_case(13,expected=50,driver=driver)

if __name__=='__main__':unittest.main()
