"""Actual manifest selection and native physical MRT predicates, CPU only."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_botw_whole_shader_conditional as owned
import test_nxnative_shader_provider as original
import test_nxnative_vertex_shader_provider as vertex

ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/nxnative_base_v0'
PATCH=C/'patches/base-v0-fragment-shader-provider.patch'
BASELINE=os.environ.get('NX_FRAGMENT_PROVIDER_BASELINE')=='1'


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'Foundation CPU fragment contract')
class FragmentShaderProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vertex.VertexShaderProvider.setUpClass();cls.addClassCleanup(vertex.VertexShaderProvider.doClassCleanups)
        parent=vertex.VertexShaderProvider
        cls.tmp=tempfile.TemporaryDirectory(prefix='nx-owned-fragment-provider-');cls.addClassCleanup(cls.tmp.cleanup)
        cls.work=Path(cls.tmp.name).resolve();cls.private=cls.work/'private';shutil.copytree(parent.private,cls.private)
        cls.nvn=cls.private/'upstream/dm/gfx/nvn';shader=cls.private/'shader_provider';adapter=cls.private/'adapter'
        if not BASELINE and PATCH.is_file():original.run(['patch','--batch','--fuzz=0','-p1','-i',str(PATCH)],cwd=cls.private)
        cls.mtl=(cls.nvn/'nvn_mtl.m').read_text();has_helpers='/* NX_FRAGMENT_FORMAT_BEGIN */' in cls.mtl
        if has_helpers:
            a=cls.mtl.index('/* NX_FRAGMENT_FORMAT_BEGIN */');b=cls.mtl.index('/* NX_FRAGMENT_FORMAT_END */',a)
            (cls.work/'fragment_helpers.inc').write_text(cls.mtl[a:b])
        cls.binary=cls.work/'probe'
        sources=[C/'tests/fragment_shader_provider_probe.m',shader/'adapter/whole_shader_conditional.m',
            shader/'adapter/whole_shader_publication.m',shader/'adapter/whole_shader_bundle_publication.m',
            adapter/'base_v0_shader_provider.m',cls.nvn/'botw_shader_manifest.m',cls.nvn/'botw_shader_runtime.m',
            cls.nvn/'nvn_refl.m',cls.nvn.parent/'tex/nvn_fmt_mtl.c']
        original.run(['clang','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-deprecated-declarations',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-DNX_FRAGMENT_HELPERS='+str(int(has_helpers)),
            '-I'+str(cls.work),'-I'+str(shader/'adapter'),'-I'+str(adapter),'-I'+str(cls.nvn),'-I'+str(cls.nvn/'include'),
            '-I'+str(cls.nvn.parent/'tex'),*map(str,sources),'-framework','Foundation','-framework','Metal','-o',str(cls.binary)])

    def package(self,outputs=None,mutate=None):
        outputs=outputs if outputs is not None else [dict(location=0,component_type='float'),dict(location=1,component_type='uint')]
        def typed(contract,reflection,index):
            contract['scope']='typed-fs-nonqueried-v1';contract['requirements']['fragment_output0']=None
            contract['requirements']['fragment_outputs']=outputs
            reflection['fragment_outputs']=[dict(location=r['location'],msl_type=r['component_type']+'4') for r in outputs]
            reflection['raster']['colors']=[r['location'] for r in outputs]
            if mutate:mutate(contract,reflection,index)
        return owned.ConditionalShaders.package(self,stage=1,variants=((0,0),(0,1),(1,0),(1,1)),mutate=typed)

    def test_complete_typed_fragment_manifest_uses_real_output_classes(self):
        path,sha,_=self.package()
        for mask,expected in ((4,True),(1,False),(2,False)):
            result=subprocess.run([str(self.binary),str(path),sha,str(mask),str(int(expected))],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_no_color_and_output_without_color_zero_remain_valid(self):
        for outputs in ([],[dict(location=1,component_type='uint')]):
            path,sha,_=self.package(outputs)
            result=subprocess.run([str(self.binary),str(path),sha,'4','1'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipIf(BASELINE,'the original runtime has no physical MRT admission helper')
    def test_actual_pso_tuple_map_type_override_and_cache_boundaries(self):
        for mode in range(22):
            with self.subTest(mode=mode):
                result=subprocess.run([str(self.binary),'guard',str(mode)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipIf(BASELINE,'new final physical tuple is not in the original runtime')
    def test_successful_pso_metadata_is_saved_before_global_mapping_clear_and_guarded(self):
        draw=original.function(self.mtl,'static void draw(const p_draw *dw, const p_draw_idx *di) {')
        self.assertLess(draw.index('conditional_fragment_map_shape('),draw.index('pso_get('))
        self.assertLess(draw.index('conditional_pso_capture('),draw.index('if (g_pso_lmap) { g_pso_lmap = NULL;'))
        self.assertLess(draw.index('conditional_draw_commit('),draw.index('conditional_fragment_draw_valid('))
        self.assertLess(draw.index('conditional_fragment_draw_valid('),draw.index('{ MTLViewport v ='))
        self.assertNotIn('g_fz',original.function(self.mtl,'static int conditional_fragment_draw_valid('))

    def test_contract_rejects_unknown_types_duplicates_locations_and_unproven_reflection(self):
        for mutate in (
            lambda c,r,i:c['requirements']['fragment_outputs'][0].__setitem__('component_type','half'),
            lambda c,r,i:r['fragment_outputs'][0].__setitem__('msl_type','half4'),
            lambda c,r,i:r['fragment_outputs'][0].__setitem__('location',2),
            lambda c,r,i:r['fragment_outputs'].append(dict(r['fragment_outputs'][0])),
            lambda c,r,i:r['raster'].__setitem__('colors',[1,0]),
            lambda c,r,i:c['requirements']['fragment_outputs'].append(dict(location=8,component_type='float')),
            lambda c,r,i:c['requirements'].__setitem__('fragment_output0',dict(type='float',nvn_formats=[37])),
            lambda c,r,i:r.__setitem__('drv_unfillable',['owned-unproved'])):
            path,sha,_=self.package(mutate=mutate)
            result=subprocess.run([str(self.binary),str(path),sha,'4','0'],capture_output=True,text=True,timeout=10)
            self.assertIn(result.returncode,(0,10),result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
