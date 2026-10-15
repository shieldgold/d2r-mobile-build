"""Synthetic base SDK texture ABI, lifetime and real Metal regressions."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class TextureFamilyTests(unittest.TestCase):
    def test_native_builder_view_and_pool_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'texture-family'
            command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks',
                       '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native/botw_graphics'),
                       str(ROOT / 'native/botw_graphics/graphics.m'),
                       str(ROOT / 'native/botw_graphics/tools/probe_texture_family.c'),
                       '-framework', 'Foundation', '-framework', 'Metal',
                       '-framework', 'QuartzCore', '-o', str(executable)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for mode in ('', 'readonly', 'linear-readonly'):
                with self.subTest(mode=mode):
                    run = subprocess.run([str(executable)] + ([mode] if mode else []), capture_output=True, text=True, timeout=60)
                    if run.returncode == 77:
                        self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    self.assertIn('TEXTURE_BASE52_ABI_VIEW_AND_LIFETIME_OK', run.stdout)


    def test_views_reach_mip_layer_sampling_and_render_attachments(self):
        import test_botw_graphics as baseline
        feature = baseline.ARRAY_FEATURE
        feature = feature.replace('nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,NULL);',
            'API(nvnTextureViewSetDefaults);API(nvnTextureViewSetLevels);API(nvnTextureViewSetLayers);'
            'API(nvnTextureViewSetTarget);API(nvnTextureViewSetFormat);API(nvnTextureViewSetSwizzle);NVNtextureView view;nvnTextureViewSetDefaults(&view);'
            'nvnTextureViewSetLevels(&view,1,0);nvnTextureViewSetLayers(&view,1,1);'
            'nvnTextureViewSetTarget(&view,NVN_TEXTURE_TARGET_2D);'
            'if(argc>1&&!strcmp(argv[1],"bad-view-level"))nvnTextureViewSetLevels(&view,3,1);'
            'if(argc>1&&!strcmp(argv[1],"bad-view-layer"))nvnTextureViewSetLayers(&view,2,1);'
            'if(argc>1&&!strcmp(argv[1],"swizzle"))nvnTextureViewSetSwizzle(&view,NVN_TEXTURE_SWIZZLE_B,NVN_TEXTURE_SWIZZLE_ONE,NVN_TEXTURE_SWIZZLE_ZERO,NVN_TEXTURE_SWIZZLE_R);'
            'nvnTexturePoolRegisterTexture(&tp,3,&arrayTexture,&view);'
            'nvnTextureViewSetDefaults(&view);')
        feature = feature.replace('return float4(%s,1);', '/*%s*/return color;')
        feature = feature.replace('texture2d_array<float>', 'texture2d<float>').replace(
            'uint(pos.x)>=2u?1u:0u,level(float(min(uint(pos.y),2u)))',
            'level(float(min(uint(pos.y),1u)))')
        feature = feature.replace('if(pass&&!y&&layer)layer=0;',
            '(void)layer;layer=1;level=(y<2?y:1)+1;')
        feature = feature.replace('    nvnTextureFinalize(&arrayTexture);', r"""
    if(argc>1&&!strcmp(argv[1],"stale-view")){nvnMemoryPoolFinalize(&arrayPool);nvnMemoryPoolBuilderSetStorage(&mb,arrayMemory,page*4);CHECK(nvnMemoryPoolInitialize(&arrayPool,&mb));nvnQueueSubmitCommands(&queue,1,&handle);return 0;}
    nvnTextureViewSetLevels(&view,1,1);nvnTextureViewSetLayers(&view,1,1);nvnTextureViewSetTarget(&view,NVN_TEXTURE_TARGET_2D);
    if(argc>1&&!strcmp(argv[1],"srgb"))nvnTextureViewSetFormat(&view,NVN_FORMAT_RGBA8_SRGB);
    const NVNtexture *arrayTargets[]={&arrayTexture};const NVNtextureView *targetViews[]={&view};
    float clearView[]={.25f,.5f,.75f,1};nvnCommandBufferBeginRecording(&cb);
    nvnCommandBufferSetRenderTargets(&cb,1,arrayTargets,targetViews,NULL,NULL);
    nvnTextureViewSetDefaults(&view); /* Recorded commands own their selected view. */
    nvnCommandBufferClearColor(&cb,0,clearView,15);handle=nvnCommandBufferEndRecording(&cb);
    nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture_subresource(&arrayTexture,1,1,mipPixels,sizeof mipPixels));
    if(argc>1&&!strcmp(argv[1],"srgb")){for(unsigned i=0;i<16*8;i++)CHECK(abs((int)mipPixels[i*4]-137)<=1&&abs((int)mipPixels[i*4+1]-188)<=1&&abs((int)mipPixels[i*4+2]-225)<=1);puts("TEXTURE_SRGB_ATTACHMENT_ENCODING_OK");return 0;}
    for(unsigned i=0;i<16*8;i++){CHECK(abs((int)mipPixels[i*4]-64)<=1&&abs((int)mipPixels[i*4+1]-128)<=1&&abs((int)mipPixels[i*4+2]-191)<=1&&mipPixels[i*4+3]==255);}
    CHECK(botw_graphics_read_texture_subresource(&arrayTexture,2,1,mipPixels,sizeof mipPixels));CHECK(!memcmp(mipPixels,colors[1][2],4));
    if(argc>1&&(!strcmp(argv[1],"live-alias")||!strcmp(argv[1],"retired-alias"))){
      if(!strcmp(argv[1],"retired-alias"))nvnTextureFinalize(&arrayTexture);
      NVNtexture alias;CHECK(nvnTextureInitialize(&alias,&tb));CHECK(botw_graphics_read_texture_subresource(&alias,1,1,mipPixels,sizeof mipPixels));return 0;
    }
    if(argc>1&&!strcmp(argv[1],"buffer-alias")){nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,(uintptr_t)arrayMemory+layerStride,gpu+2048,16,0);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);return 0;}
    API(nvnTextureReadTexels);API(nvnTextureWriteTexels);
    if(argc>1&&!strncmp(argv[1],"tracking-",9)){
      API(nvnTextureFlushTexels);API(nvnTextureInvalidateTexels);
      if(!strcmp(argv[1],"tracking-restored")){volatile unsigned char *p=arrayMemory+layerStride;unsigned char old=*p;*p=old^1;*p=old;CHECK(textureTestFaults>0);}
      if(!strcmp(argv[1],"tracking-invalidate")){nvnTextureInvalidateTexels(&arrayTexture,NULL,(const NVNcopyRegion*)1);CHECK(abs((int)arrayMemory[layerStride+levelOffsets[1]]-64)<=1);}
      nvnTextureFlushTexels(&arrayTexture,NULL,(const NVNcopyRegion*)1);
      puts("TEXTURE_TRACKED_GPU_COHERENCE_OK");return 0;
    }
    nvnTextureViewSetLevels(&view,1,1);nvnTextureViewSetLayers(&view,1,1);
    NVNcopyRegion one={0,0,0,1,1,1};unsigned char nativeRead[4];nvnTextureReadTexels(&arrayTexture,&view,&one,nativeRead);CHECK(abs((int)nativeRead[0]-64)<=1&&abs((int)nativeRead[1]-128)<=1);
    unsigned char replacement[4]={11,22,33,255};nvnTextureWriteTexels(&arrayTexture,&view,&one,replacement);
    CHECK(botw_graphics_read_texture_subresource(&arrayTexture,1,1,mipPixels,sizeof mipPixels));CHECK(!memcmp(mipPixels,replacement,4));CHECK(abs((int)mipPixels[4]-64)<=1);
    puts("TEXTURE_VIEW_NONZERO_MIP_LAYER_SAMPLE_AND_ATTACHMENT_OK");
    nvnTextureFinalize(&arrayTexture);""")
        feature = feature.replace('else CHECK(!memcmp(pixel,colors[layer][level],4));',
            'else if(argc>1&&!strcmp(argv[1],"swizzle")){CHECK(pixel[0]==colors[layer][level][2]&&pixel[1]==255&&pixel[2]==0&&pixel[3]==colors[layer][level][0]);}else CHECK(!memcmp(pixel,colors[layer][level],4));')
        source = baseline.METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);', feature + '    nvnMemoryPoolFinalize(&pool);')
        source = source.replace('#include "graphics.h"', '#include "graphics.h"\n#include "tools/texture_test_bridge.h"')
        source = source.replace('    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);',
            '    if(argc>1&&!strncmp(argv[1],"tracking-",9))botw_graphics_set_memory_tracking_registration(textureTestRegistration);\n'
            '    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);')
        baseline.MetalBridgeTests.compile_render_probe(self, source,
            [('', 'TEXTURE_VIEW_NONZERO_MIP_LAYER_SAMPLE_AND_ATTACHMENT_OK', True),
             ('live-alias','texture storage authority',False),('retired-alias','texture storage authority',False),
             ('swizzle','TEXTURE_VIEW_NONZERO_MIP_LAYER_SAMPLE_AND_ATTACHMENT_OK',True),
             ('srgb','TEXTURE_SRGB_ATTACHMENT_ENCODING_OK',True),
             ('bad-view-level','texture view level/layer bounds',False),('bad-view-layer','texture view level/layer bounds',False),
             ('stale-view','stale texture pool',False),('buffer-alias','buffer address aliases private texture',False),
             ('tracking-clean','TEXTURE_TRACKED_GPU_COHERENCE_OK',True),
             ('tracking-restored','texture flush CPU storage changed',False),
             ('tracking-invalidate','TEXTURE_TRACKED_GPU_COHERENCE_OK',True)])


if __name__ == '__main__':
    unittest.main()
