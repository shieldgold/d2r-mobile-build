"""Actual complete manifest parser and native descriptor helpers, CPU only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_botw_whole_shader_conditional as owned
import test_nxnative_shader_provider as original

ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/nxnative_base_v0'
OLD=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('NX_VERTEX_PROVIDER_BASELINE')=='1'


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'Foundation CPU vertex contract')
class VertexShaderProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='nx-owned-vertex-provider-');cls.addClassCleanup(cls.tmp.cleanup)
        cls.work=Path(cls.tmp.name).resolve();cls.private=cls.work/'private';cls.native=cls.private/'upstream'
        shutil.copytree(C/'upstream',cls.native)
        for name in ('normal-entry.patch','base-v0-texture-view.patch','normal-hle-crash.patch','horizon-address-signal.patch',
                     'normal-runtime-diagnostics.patch','base-v0-conditional-shader-provider.patch'):
            original.run(['patch','--batch','--fuzz=0','-p1','-i',str(C/'patches'/name)],cwd=cls.native)
        cls.shader=cls.private/'shader_provider';cls.adapter=cls.private/'adapter'
        shutil.copytree(OLD/'adapter',cls.shader/'adapter');shutil.copytree(C/'adapter',cls.adapter)
        legacy=cls.work/'legacy';shutil.copytree(OLD/'upstream',legacy)
        for name in ('portable-shader-source.patch','base-v0-image-bank.patch','conditional-shader-runtime.patch'):
            original.run(['patch','--batch','--fuzz=0','-p1','-i',str(OLD/'patches'/name)],cwd=legacy)
        nvn=cls.native/'dm/gfx/nvn';oldnvn=legacy/'dm/gfx/nvn';cls.nvn=nvn
        for name in ('botw_shader.h','botw_shader_manifest.h','botw_shader_manifest.m','botw_shader_runtime.h','botw_shader_runtime.m'):
            shutil.copyfile(oldnvn/name,nvn/name)
        if not BASELINE:
            original.run(['patch','--batch','--fuzz=0','-p1','-i',str(C/'patches/base-v0-vertex-shader-provider.patch')],cwd=cls.private)
        cls.mtl=(nvn/'nvn_mtl.m').read_text()
        if not BASELINE:
            fmt=original.function(cls.mtl,'static MTLVertexFormat vfmt(int f) {')
            marker=cls.mtl.index('/* NX_VERTEX_FORMAT_BEGIN */');end=cls.mtl.index('/* NX_VERTEX_FORMAT_END */',marker)
            convert=original.function(cls.mtl,'static MTLVertexFormat vfmt_for_ct(')
            (cls.work/'vertex_format.inc').write_text(convert+'\n'+fmt+'\n'+cls.mtl[marker:end])
            marker=cls.mtl.index('/* NX_VERTEX_BINDING_BEGIN */');end=cls.mtl.index('/* NX_VERTEX_BINDING_END */',marker)
            (cls.work/'vertex_binding.inc').write_text(cls.mtl[marker:end])
            (cls.work/'vertex_snapshot.inc').write_text(original.function(cls.mtl,'static botw_conditional_draw_state conditional_snapshot_locked('))
            descriptor=original.function(cls.mtl,'static MTLRenderPipelineDescriptor *pso_desc(')
            begin=descriptor.index('    const nvn_refl *rf = &vs->rf;')
            end=descriptor.index('    d.vertexDescriptor = vd;',begin)
            key=(nvn/'nvn_pso_skey.h').read_text();key=key[key.index('typedef struct {'):key.index('} pso_key;')+len('} pso_key;')]
            gate='static int conditional_vertex_shape_valid(' in cls.mtl
            (cls.work/'vertex_descriptor.inc').write_text('#define NX_SHAPE_GATE '+str(int(gate))+'\n'+key+'\n'+
                'static void owned_descriptor_loop(const pso_key *k,owned_shader *vs) { owned_descriptor vd={0};\n'+
                descriptor[begin:end]+'\n(void)vd; }\n'+
                original.function(cls.mtl,'static void pso_key_fill(').replace('NvShader *','owned_shader *').replace('(__bridge void *)','(void *)'))
        cls.binary=cls.work/'probe'
        sources=[C/'tests/vertex_shader_provider_probe.m',cls.shader/'adapter/whole_shader_conditional.m',
                 cls.shader/'adapter/whole_shader_publication.m',cls.shader/'adapter/whole_shader_bundle_publication.m',
                 cls.adapter/'base_v0_shader_provider.m',nvn/'botw_shader_manifest.m',nvn/'botw_shader_runtime.m',nvn/'nvn_refl.m']
        original.run(['clang','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror','-Wno-unused-function',
            '-Wno-deprecated-declarations','-fsanitize=address,undefined','-fno-sanitize-recover=all',
            *(['-DNX_TYPED_BASELINE=1'] if BASELINE else []),'-I'+str(cls.work),'-I'+str(cls.shader/'adapter'),'-I'+str(cls.adapter),
            '-I'+str(nvn),'-I'+str(nvn/'include'),*map(str,sources),'-framework','Foundation','-framework','Metal','-o',str(cls.binary)])

    def package(self,component='float',*,mutate=None):
        def typed(contract,reflection,index):
            contract['scope']='typed-vs-nonqueried-v1'
            contract['requirements']['attributes']=[dict(location=0,component_type=component)]
            reflection['vertex_inputs']=[dict(location=0,msl_type=component+'4')]
            if mutate:mutate(contract,reflection,index)
        return owned.ConditionalShaders.package(self,variants=((0,0),(0,1),(1,0),(1,1)),mutate=typed)

    def run_manifest(self,path,sha,mask=1,expected=True):
        result=subprocess.run([str(self.binary),str(path),sha,str(mask),str(int(expected))],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_complete_manifest_selects_typed_float_and_integer_predicates(self):
        path,sha,_=self.package();self.run_manifest(path,sha)
        path,sha,_=self.package('uint');self.run_manifest(path,sha,mask=4)
        self.run_manifest(path,sha,mask=1,expected=False)

    def test_contract_reflection_exact_types_locations_and_no_fallback(self):
        for mutate in (
            lambda c,r,i:r['vertex_inputs'][0].__setitem__('msl_type','half4'),
            lambda c,r,i:r['vertex_inputs'][0].__setitem__('location',16),
            lambda c,r,i:r['vertex_inputs'].append(dict(r['vertex_inputs'][0])),
            lambda c,r,i:c['requirements']['attributes'][0].__setitem__('component_type','double'),
            lambda c,r,i:r.__setitem__('drv_unfillable',['unproven'])):
            path,sha,_=self.package(mutate=mutate)
            result=subprocess.run([str(self.binary),str(path),sha,'1','0'],capture_output=True,text=True,timeout=10)
            self.assertIn(result.returncode,(0,10),result.stdout+result.stderr)

    @unittest.skipIf(BASELINE,'original runtime has no typed descriptor admission helper')
    def test_actual_descriptor_type_range_stride_and_draw_parameter_guards(self):
        for mode in range(25):
            with self.subTest(mode=mode):
                result=subprocess.run([str(self.binary),'guard',str(mode)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipIf(BASELINE,'original snapshot has no typed descriptor fields')
    def test_actual_snapshot_changes_invalidate_all_descriptor_and_binding_operands(self):
        result=subprocess.run([str(self.binary),'guard','25'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipIf(BASELINE,'new descriptor guard does not exist in the original runtime')
    def test_guard_precedes_viewport_and_keeps_original_origin_transaction(self):
        draw=original.function(self.mtl,'static void draw(const p_draw *dw, const p_draw_idx *di) {')
        self.assertLess(draw.index('conditional_draw_commit('),draw.index('conditional_vertex_draw_valid('))
        self.assertLess(draw.index('conditional_vertex_draw_valid('),draw.index('{ MTLViewport v ='))
        self.assertIn('conditional_draw_unlock(&conditional_guard); rp_end(); draw(dw, di);',draw)
        self.assertNotIn('getenv(',original.function(self.mtl,'static int conditional_vertex_draw_valid('))

    @unittest.skipIf(BASELINE,'original runtime has no typed descriptor admission')
    def test_actual_descriptor_loop_is_guarded_before_any_stream_indexing(self):
        for mode in range(26,36):
            with self.subTest(mode=mode):
                result=subprocess.run([str(self.binary),'guard',str(mode)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipIf(BASELINE,'original runtime has no typed descriptor admission')
    def test_pure_shape_gate_precedes_first_pso_without_registry_access(self):
        draw=original.function(self.mtl,'static void draw(const p_draw *dw, const p_draw_idx *di) {')
        self.assertLess(draw.index('conditional_vertex_shape_valid('),draw.index('pso_get('))
        shape=original.function(self.mtl,'static int conditional_vertex_shape_valid(')
        for forbidden in ('mtl_gpu_addr','pthread_mutex','g_rmu','getenv('):self.assertNotIn(forbidden,shape)


if __name__=='__main__':unittest.main()
