"""Synthetic base SDK texture ABI, lifetime and real Metal regressions."""
from pathlib import Path
import os
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class TextureFamilyTests(unittest.TestCase):
    def test_volume_3d_public_xyz_transfer_cache_mips_padding_and_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            graphics=ROOT/'native/botw_graphics';exe=Path(directory)/'volume-public'
            command=['xcrun','clang','-std=c11','-fobjc-arc','-fobjc-arc-exceptions','-fblocks',
                     '-Wall','-Wextra','-Werror','-I',str(graphics),str(graphics/'graphics.m'),
                     str(graphics/'tools/probe_texture_family.c'),'-framework','Foundation',
                     '-framework','Metal','-framework','QuartzCore','-o',str(exe)]
            built=subprocess.run(command,capture_output=True,text=True,timeout=120)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            for mode in ('volume-write','volume-cached','volume-mips','volume-empty'):
                with self.subTest(mode=mode):
                    run=subprocess.run([str(exe),mode],capture_output=True,text=True,timeout=60,
                                       env={**os.environ,'MTL_DEBUG_LAYER':'1'})
                    if run.returncode==77:self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                    self.assertIn('TEXTURE_VOLUME_PUBLIC_FULL_MIPS_PARTIAL_GOB_PADDING_CACHE_AND_EMPTY_OK',run.stdout)
                    self.assertIn('Metal API Validation Enabled',run.stderr)
                    self.assertNotIn('failed assertion',run.stderr)
                    self.assertNotIn('MTLReportFailure',run.stderr)

    def _volume_descriptor_probe(self, modes):
        """Exercise native volumes; the 32-cube importer uses an independent GOB oracle."""
        import test_botw_graphics as baseline
        feature = r'''
    API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetSize3D);API(nvnTextureBuilderSetLevels);
    API(nvnTextureBuilderGetStorageSize);API(nvnTextureBuilderSetPackagedTextureData);API(nvnTextureBuilderGetPackagedTextureData);
    API(nvnTextureWriteTexels);API(nvnTextureFinalize);
    API(nvnTextureViewSetDefaults);API(nvnTextureViewSetTarget);API(nvnTextureViewSetFormat);API(nvnTextureViewSetLevels);API(nvnTextureViewSetLayers);
    API(nvnTexturePoolInitialize);API(nvnTexturePoolRegisterTexture);API(nvnSamplerPoolInitialize);API(nvnSamplerPoolRegisterSampler);
    API(nvnSamplerBuilderSetDefaults);API(nvnSamplerBuilderSetDevice);API(nvnSamplerBuilderSetMinMagFilter);API(nvnSamplerInitialize);
    API(nvnDeviceGetTextureHandle);API(nvnCommandBufferSetTexturePool);API(nvnCommandBufferSetSamplerPool);API(nvnCommandBufferBindTexture);
    API(nvnMemoryPoolMapVirtual);API(nvnCommandBufferReportCounter);
    const char *mode=argc>1?argv[1]:"volume-sample";int npot=!strcmp(mode,"volume-npot");
    unsigned width=npot?17:32,height=npot?9:32,depth=npot?5:32;
    unsigned levels=npot?5:(!strcmp(mode,"volume-mips")||!strcmp(mode,"volume-view"))?6:1;
    int selected=!strcmp(mode,"volume-view"),virt=strstr(mode,"remap")||strstr(mode,"authority");
    int writeOnly=!strcmp(mode,"volume-write")||!strcmp(mode,"volume-write-noimage"),readWrite=!strcmp(mode,"volume-readwrite"),wrong=!strcmp(mode,"volume-wrong-type"),badView=!strcmp(mode,"volume-invalid-view");
    int image=strcmp(mode,"volume-read")&&strcmp(mode,"volume-write-noimage");size_t backingBytes=1024*1024;
    unsigned char *volumeMemory=mmap(NULL,backingBytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    unsigned char *secondMemory=virt?mmap(NULL,backingBytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0):NULL;
    CHECK(volumeMemory!=MAP_FAILED&&(!virt||secondMemory!=MAP_FAILED));memset(volumeMemory,0xa5,backingBytes);if(virt)memset(secondMemory,0xa5,backingBytes);
    NVNmemoryPool volumePool,physicalA,physicalB;nvnMemoryPoolBuilderSetStorage(&mb,volumeMemory,backingBytes);
    if(virt){
      nvnMemoryPoolBuilderSetFlags(&mb,0x189);CHECK(nvnMemoryPoolInitialize(&physicalA,&mb));
      nvnMemoryPoolBuilderSetStorage(&mb,secondMemory,backingBytes);CHECK(nvnMemoryPoolInitialize(&physicalB,&mb));
      nvnMemoryPoolBuilderSetFlags(&mb,0x221);nvnMemoryPoolBuilderSetStorage(&mb,NULL,backingBytes);CHECK(nvnMemoryPoolInitialize(&volumePool,&mb));
      NVNmappingRequest map={.physicalPool=&physicalA,.size=backingBytes,.storageClass=0x1fe};CHECK(nvnMemoryPoolMapVirtual(&volumePool,1,&map));
    }else CHECK(nvnMemoryPoolInitialize(&volumePool,&mb));
    /* Fixed 32^3 RGBA8 block-linear reference: 2 GOBs across X, four rows,
       16 slices per block. z15 and z16 land in different 64KiB blocks. */
    if(levels==1)for(unsigned z=0;z<32;z++)for(unsigned y=0;y<32;y++)for(unsigned x=0;x<32;x++){
      unsigned xb=x*4;size_t offset=(z/16)*65536+(y/8)*16384+(xb/64)*8192+(z%16)*512+
        (xb%64/32)*256+(y%8/2)*64+(xb%32/16)*32+(y%2)*16+xb%16;
      volumeColor(volumeMemory+offset,x,y,z,0,0);if(virt)volumeColor(secondMemory+offset,x,y,z,0,61);
    }
    NVNtexture volume;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetTarget(&tb,NVN_TEXTURE_TARGET_3D);nvnTextureBuilderSetSize3D(&tb,width,height,depth);nvnTextureBuilderSetLevels(&tb,levels);
    nvnTextureBuilderSetFlags(&tb,image?NVN_TEXTURE_FLAGS_IMAGE:0);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);
    nvnTextureBuilderSetPackagedTextureData(&tb,(const void*)1);nvnTextureBuilderSetStorage(&tb,&volumePool,0);
    CHECK(nvnTextureBuilderGetPackagedTextureData(&tb)==(const void*)1);CHECK(nvnTextureBuilderGetStorageSize(&tb)>0);
    if(levels==1)CHECK(nvnTextureBuilderGetStorageSize(&tb)==131072);CHECK(nvnTextureInitialize(&volume,&tb));
    if(!virt){CHECK(textureObject(&volume).texture.textureType==MTLTextureType3D);
      CHECK(textureObject(&volume).texture.depth==depth&&textureObject(&volume).texture.mipmapLevelCount==levels);
      CHECK((textureObject(&volume).texture.usage&MTLTextureUsageShaderWrite)==(image?MTLTextureUsageShaderWrite:0));}
    if(levels>1)for(unsigned level=0;level<levels;level++){
      unsigned w=width>>level?:1,h=height>>level?:1,d=depth>>level?:1;size_t count=(size_t)w*h*d*4;
      unsigned char *linear=malloc(count);CHECK(linear);for(unsigned z=0;z<d;z++)for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)volumeColor(linear+((z*h+y)*w+x)*4,x,y,z,level,0);
      NVNtextureView uploadView;nvnTextureViewSetDefaults(&uploadView);nvnTextureViewSetLevels(&uploadView,level,1);
      NVNcopyRegion region={0,0,0,(int)w,(int)h,(int)d};nvnTextureWriteTexels(&volume,&uploadView,&region,linear);free(linear);
    }
    NVNtexturePool tp;NVNsamplerPool sp;CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16));CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));
    NVNtextureView view;nvnTextureViewSetDefaults(&view);nvnTextureViewSetTarget(&view,NVN_TEXTURE_TARGET_3D);nvnTextureViewSetFormat(&view,NVN_FORMAT_RGBA8);
    if(selected)nvnTextureViewSetLevels(&view,1,0);if(badView)nvnTextureViewSetLayers(&view,1,1);
    if(badView){
      volumeEscaping=1;if(!sigsetjmp(volumeRecovery,1)){nvnTexturePoolRegisterTexture(&tp,3,&volume,&view);CHECK(0);}volumeEscaping=0;
      CHECK(volumeCallbacks==1&&strstr(botw_graphics_last_error(),"view"));
      char error[512];CHECK(botw_graphics_shutdown_checked(error,sizeof error));CHECK(!munmap(volumeMemory,backingBytes));free(memory);puts("VOLUME_INVALID_VIEW_REJECTED_OK");return 0;
    }
    nvnTexturePoolRegisterTexture(&tp,3,&volume,&view);nvnTextureViewSetLevels(&view,levels-1,1);nvnTextureViewSetTarget(&view,NVN_TEXTURE_TARGET_2D);
    NVNsamplerBuilder sb;NVNsampler sampler;nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);
    nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST_MIPMAP_NEAREST,NVN_MAG_FILTER_NEAREST);CHECK(nvnSamplerInitialize(&sampler,&sb));nvnSamplerPoolRegisterSampler(&sp,4,&sampler);
    uint64_t textureHandle=nvnDeviceGetTextureHandle(&device,3,4);controls[0][8]=84;controls[1][8]=85;codes[0][8]=84;codes[1][8]=85;
    memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    const char *readBody="return t.read(q,lod);";
    const char *sampleBody="return t.sample(s,(float3(q)+.5f)/float3(w,h,d),level(float(lod)));";
    const char *writeBody="t.write(float4(.8f,.1f,.6f,1),q);return float4(.8f,.1f,.6f,1);";
    const char *readWriteBody="float4 old=t.read(q);t.write(float4(.8f,.1f,.6f,1),q);return old;";
    const char *wrongBody="return t.sample(s,float2(.5f));";
    char volumeMSL[2400];snprintf(volumeMSL,sizeof volumeMSL,
      "#include <metal_stdlib>\nusing namespace metal;vertex float4 vv(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}"
      "fragment float4 vf(float4 p [[position]],%s t [[texture(3)]],sampler s [[sampler(0)]]){"
      "uint px=min(uint(p.x),7u),py=min(uint(p.y),7u),lod=min(py,%uu);uint w=max(%uu>>lod,1u),h=max(%uu>>lod,1u),d=max(%uu>>lod,1u);"
      "uint zs[8]={0u,1u,7u,8u,15u,16u,30u,31u};uint3 q=uint3(px*(w-1u)/7u,py*(h-1u)/7u,zs[(px+py)%%8u]*(d-1u)/31u);%s}",
      wrong?"texture2d<float>":writeOnly?"texture3d<float,access::write>":readWrite?"texture3d<float,access::read_write>":!strcmp(mode,"volume-read")?"texture3d<float,access::read>":"texture3d<float>",
      levels-1-selected,width>>selected,height>>selected,depth>>selected,
      wrong?wrongBody:writeOnly?writeBody:readWrite?readWriteBody:!strcmp(mode,"volume-read")?readBody:sampleBody);
    botw_graphics_shader_binding bindings[]={{BOTW_GRAPHICS_TEXTURE,0,3},{BOTW_GRAPHICS_SAMPLER,0,0}};
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,volumeMSL,"vv"));
    CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,volumeMSL,"vf",bindings,2));CHECK(nvnProgramSetShaders(&program,2,sd));
    /* Establish that this actual GPU copy/counter path can modify the canaries,
       then reset them so a rejected mixed batch must have no prefix effects. */
    if(writeOnly||readWrite||wrong){
      memset(memory+16384,0x3c,32);memset(memory+16448,0xa5,32);memset(memory+16512,0xa5,16);
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferCopyBufferToBuffer(&cb,gpu+16384,gpu+16448,32,0);nvnCommandBufferReportCounter(&cb,NVN_COUNTER_TYPE_TIMESTAMP_TOP,gpu+16512);
      handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(dispatch_group_wait(allGPU,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0);
      CHECK(!memcmp(memory+16384,memory+16448,32));unsigned changed=0;for(unsigned i=0;i<16;i++)changed|=memory[16512+i]!=0xa5;CHECK(changed);
      memset(memory+16448,0xa5,32);memset(memory+16512,0xa5,16);fprintf(stderr,"VOLUME_COPY_COUNTER_POSITIVE_CONTROL_OK\n");
    }
    for(unsigned pass=0;pass<(virt?2u:1u);pass++){
      if(pass){NVNmappingRequest map={.physicalPool=&physicalB,.size=backingBytes,.storageClass=0x1fe};CHECK(nvnMemoryPoolMapVirtual(&volumePool,1,&map));}
      if(pass&&strstr(mode,"authority")){
        NVNtexture alias;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetSize2D(&tb,32,32);nvnTextureBuilderSetStorage(&tb,&volumePool,0);CHECK(nvnTextureInitialize(&alias,&tb));
        const NVNtexture *aliasTarget[]={&alias};float aliasColor[]={.2f,.4f,.6f,1};nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,aliasTarget,NULL,NULL,NULL);nvnCommandBufferSetScissor(&cb,0,0,32,32);nvnCommandBufferClearColor(&cb,0,aliasColor,15);
        handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);if(strstr(mode,"retired"))nvnTextureFinalize(&alias);
      }
      unsigned char priorPixels[sizeof pixels];CHECK(botw_graphics_read_texture(&texture,priorPixels,sizeof priorPixels));
      nvnCommandBufferBeginRecording(&cb);
      if(writeOnly||readWrite||wrong){nvnCommandBufferCopyBufferToBuffer(&cb,gpu+16384,gpu+16448,32,0);nvnCommandBufferReportCounter(&cb,NVN_COUNTER_TYPE_TIMESTAMP_TOP,gpu+16512);}
      nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferSetScissor(&cb,0,0,8,8);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
      nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,textureHandle);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);
      int rejection=writeOnly||readWrite||wrong||(pass&&strstr(mode,"authority"));
      if(rejection){
        BWQueue *owned=get(&queue,[BWQueue class]);NSData *arenaBefore=digest(owned.arena,owned.arenaBytes);volumeEscaping=1;
        if(!sigsetjmp(volumeRecovery,1)){
          nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
          CHECK(dispatch_group_wait(allGPU,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0);
          CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
          fprintf(stderr,"VOLUME_UNGUARDED_SHADER_EXECUTED access=%s pixel=%u,%u,%u,%u\n",writeOnly?"write":readWrite?"read_write":"other",pixels[0],pixels[1],pixels[2],pixels[3]);
          return 91;
        }volumeEscaping=0;
        const char *expected=wrong?"shader texture type differs":writeOnly||readWrite?"3D texture shader access requires read-only bindings":"texture storage authority";
        CHECK(volumeCallbacks==1&&strstr(botw_graphics_last_error(),expected));nvnQueueFinish(&queue);CHECK(dispatch_group_wait(allGPU,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0);
        CHECK([arenaBefore isEqual:digest(owned.arena,owned.arenaBytes)]);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels)&&!memcmp(priorPixels,pixels,sizeof pixels));
        if(writeOnly||readWrite||wrong){for(unsigned i=0;i<32;i++)CHECK(memory[16448+i]==0xa5);for(unsigned i=0;i<16;i++)CHECK(memory[16512+i]==0xa5);}
        CHECK(!graphicsDepth&&!pendingError&&!pendingFatal);char error[512];CHECK(botw_graphics_shutdown_checked(error,sizeof error));
        CHECK(!munmap(volumeMemory,backingBytes));if(virt)CHECK(!munmap(secondMemory,backingBytes));free(memory);puts("VOLUME_SHADER_REJECTED_BEFORE_COPY_COUNTER_ARENA_PIXELS_AND_AUTHORITY_OK");return 0;
      }
      nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
      for(unsigned py=0;py<8;py++)for(unsigned px=0;px<8;px++){
        unsigned level=(py<levels-selected?py:levels-1-selected)+selected,w=width>>level?:1,h=height>>level?:1,d=depth>>level?:1;
        unsigned zs[]={0,1,7,8,15,16,30,31},x=px*(w-1)/7,y=py*(h-1)/7,z=zs[(px+py)%8]*(d-1)/31;unsigned char expected[4];volumeColor(expected,x,y,z,level,pass?61:0);
        for(unsigned c=0;c<4;c++)CHECK(abs((int)pixels[(py*8+px)*4+c]-(int)expected[c])<=1);
      }
      CHECK(textureObject(&volume).texture.textureType==MTLTextureType3D&&textureObject(&volume).texture.depth==depth);
      fprintf(stderr,"VOLUME_NATIVE_GPU_OK mode=%s pass=%u type3D=true dims=%ux%ux%u levels=%u xyz15_16_31=true\n",mode,pass,width,height,depth,levels);
    }
    nvnTextureFinalize(&volume);nvnMemoryPoolFinalize(&volumePool);if(virt){nvnMemoryPoolFinalize(&physicalA);nvnMemoryPoolFinalize(&physicalB);}
    CHECK(!munmap(volumeMemory,backingBytes));if(virt)CHECK(!munmap(secondMemory,backingBytes));puts("VOLUME_NATIVE_XYZ_MIPS_OPAQUE_REMAP_OK");
'''
        helpers = r'''
#include <sys/mman.h>
#include <setjmp.h>
static sigjmp_buf volumeRecovery;static int volumeEscaping,volumeCallbacks;
static void volumeError(const char *message){if(volumeEscaping){volumeCallbacks++;siglongjmp(volumeRecovery,1);}fprintf(stderr,"VOLUME_UNHANDLED %s\n",message);}
static void volumeColor(unsigned char *out,unsigned x,unsigned y,unsigned z,unsigned level,unsigned bias){
 out[0]=(unsigned char)(17+(x*5+level*9+bias)%200);out[1]=(unsigned char)(19+(y*7+level*11+bias)%200);out[2]=(unsigned char)(23+(z*11+level*13+bias)%200);out[3]=255;
}
'''
        source = baseline.METAL_SMOKE.replace('#include <unistd.h>', '#include <unistd.h>\n'+helpers)
        source = source.replace('botw_graphics_init(NULL, NULL, valid)', 'botw_graphics_init(NULL, volumeError, valid)')
        source = source.replace('page*2', 'page*16').replace('nvnTextureBuilderSetSize2D(&tb,4,4);', 'nvnTextureBuilderSetSize2D(&tb,8,8);')
        source = source.replace('unsigned char pixels[64];', 'unsigned char pixels[8*8*4];')
        source = source.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        graphics = ROOT / 'native/botw_graphics'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);harness=path/'volume.m';exe=path/'volume'
            harness.write_text('#include "graphics.m"\n'+source)
            command=['xcrun','clang','-fobjc-arc','-fobjc-arc-exceptions','-fblocks','-Wall','-Wextra','-Werror',
                     '-Wno-unused-function','-I',str(graphics),str(harness),'-framework','Foundation','-framework','Metal','-framework','QuartzCore','-o',str(exe)]
            build=subprocess.run(command,capture_output=True,text=True,timeout=120)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            for mode in modes:
                with self.subTest(mode=mode):
                    run=subprocess.run([str(exe),mode],capture_output=True,text=True,timeout=30,env={**os.environ,'MTL_DEBUG_LAYER':'1'})
                    if run.returncode==77:self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                    self.assertIn('VOLUME_',run.stdout)
                    self.assertIn('Metal API Validation Enabled',run.stderr)
                    self.assertNotIn('failed assertion',run.stderr)
                    self.assertNotIn('MTLReportFailure',run.stderr)

    def test_volume_3d_native_xyz_mips_selected_view_and_opaque_payload(self):
        self._volume_descriptor_probe(('volume-sample','volume-read','volume-mips','volume-npot','volume-view'))

    def test_volume_3d_image_access_is_rejected_before_counter_copy_arena_and_pixels(self):
        from botw_metal_counter_capability import require_counter_sampling
        require_counter_sampling()
        self._volume_descriptor_probe(('volume-write','volume-readwrite','volume-write-noimage','volume-wrong-type','volume-invalid-view'))

    def test_volume_3d_fe_remap_refreshes_snapshot_and_preserves_private_alias_authority(self):
        self._volume_descriptor_probe(('volume-remap','volume-live-authority','volume-retired-authority'))

    def _cube_descriptor_probe(self, cases):
        import test_botw_graphics as baseline
        from unittest.mock import patch
        feature = r'''
    API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetSize3D);API(nvnTextureBuilderSetLevels);
    API(nvnTextureBuilderGetStorageSize);API(nvnTextureBuilderGetStorageAlignment);API(nvnTextureGetDepth);API(nvnTextureFinalize);
    API(nvnTexturePoolInitialize);API(nvnTexturePoolRegisterTexture);API(nvnSamplerPoolInitialize);API(nvnSamplerPoolRegisterSampler);
    API(nvnSamplerBuilderSetDefaults);API(nvnSamplerBuilderSetDevice);API(nvnSamplerBuilderSetMinMagFilter);API(nvnSamplerInitialize);
    API(nvnTextureViewSetDefaults);API(nvnTextureViewSetTarget);API(nvnTextureViewSetFormat);API(nvnTextureViewSetLayers);API(nvnTextureViewSetLevels);
    API(nvnDeviceGetTextureHandle);API(nvnCommandBufferSetTexturePool);API(nvnCommandBufferSetSamplerPool);API(nvnCommandBufferBindTexture);
    unsigned char *cubeMemory=NULL;CHECK(!posix_memalign((void**)&cubeMemory,page,page*4));memset(cubeMemory,0xa5,page*4);
    NVNmemoryPool cubePool;nvnMemoryPoolBuilderSetStorage(&mb,cubeMemory,page*4);CHECK(nvnMemoryPoolInitialize(&cubePool,&mb));
    unsigned char colors[6][3][4];const size_t faceStride=2048,levelOffsets[]={0,1024,1536};
    /* Independent RGBA8 GOB reference: 16x16 base = two vertically stacked
       GOBs; 8x8 and 4x4 levels each occupy one GOB, so each face is 2048B. */
    for(unsigned face=0;face<6;face++)for(unsigned level=0;level<3;level++){
      colors[face][level][0]=(unsigned char)(20+face*30);colors[face][level][1]=(unsigned char)(30+level*70);
      colors[face][level][2]=(unsigned char)(40+face*13+level*17);colors[face][level][3]=255;
      for(unsigned y=0;y<(16u>>level);y++)for(unsigned x=0;x<(16u>>level);x++){
        unsigned xb=x*4;size_t gob=(y/8)*512+(xb/32)*256+(y%8/2)*64+(xb%32/16)*32+(y%2)*16+xb%16;
        memcpy(cubeMemory+face*faceStride+levelOffsets[level]+gob,colors[face][level],4);
      }
    }
    unsigned rawDepth=!strcmp(argv[1],"cube-selected-view")?1:6;
    NVNtexture cube;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetTarget(&tb,NVN_TEXTURE_TARGET_CUBEMAP);nvnTextureBuilderSetSize3D(&tb,16,16,rawDepth);nvnTextureBuilderSetLevels(&tb,3);
    nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&cubePool,0);
    if(!strcmp(argv[1],"cube-nonsquare")){
      nvnTextureBuilderSetSize3D(&tb,16,8,6);CHECK(!nvnTextureBuilderGetStorageSize(&tb));CHECK(!nvnTextureInitialize(&cube,&tb));
      nvnMemoryPoolFinalize(&cubePool);botw_graphics_shutdown();free(cubeMemory);free(memory);puts("CUBE_NONSQUARE_REJECTED_WITHOUT_GPU_RESOURCE_OK");return 0;
    }
    CHECK(nvnTextureBuilderGetStorageSize(&tb)==6*faceStride);CHECK(nvnTextureBuilderGetStorageAlignment(&tb)==512);CHECK(nvnTextureInitialize(&cube,&tb));CHECK(nvnTextureGetDepth(&cube)==(int)rawDepth);
    unsigned char facePixels[16*16*4];
    for(unsigned face=0;face<6;face++)for(unsigned level=0;level<3;level++){
      CHECK(botw_graphics_read_texture_subresource(&cube,level,face,facePixels,sizeof facePixels));
      for(unsigned pixel=0;pixel<((16u>>level)*(16u>>level));pixel++)CHECK(!memcmp(facePixels+pixel*4,colors[face][level],4));
    }
    if(strstr(argv[1],"authority")){
      NVNtexture alias;nvnTextureBuilderSetTarget(&tb,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetSize2D(&tb,16,16);nvnTextureBuilderSetLevels(&tb,1);CHECK(nvnTextureInitialize(&alias,&tb));
      const NVNtexture *aliasTargets[]={&alias};float aliasColor[]={.2f,.4f,.6f,1};nvnCommandBufferBeginRecording(&cb);
      nvnCommandBufferSetRenderTargets(&cb,1,aliasTargets,NULL,NULL,NULL);nvnCommandBufferSetScissor(&cb,0,0,16,16);nvnCommandBufferClearColor(&cb,0,aliasColor,15);
      handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
      if(strstr(argv[1],"retired"))nvnTextureFinalize(&alias);
      CHECK(botw_graphics_read_texture_subresource(&cube,0,0,facePixels,sizeof facePixels));return 2;
    }
    NVNtexturePool tp;NVNsamplerPool sp;CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16));CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));
    NVNtextureView view;nvnTextureViewSetDefaults(&view);int selected=!strcmp(argv[1],"cube-selected-view");
    if(selected||!strcmp(argv[1],"cube-partial-view")){
      nvnTextureViewSetTarget(&view,NVN_TEXTURE_TARGET_CUBEMAP);nvnTextureViewSetFormat(&view,NVN_FORMAT_RGBA8);
      nvnTextureViewSetLayers(&view,0,selected?6:1);nvnTextureViewSetLevels(&view,selected?1:0,0);
    }
    nvnTexturePoolRegisterTexture(&tp,3,&cube,selected||!strcmp(argv[1],"cube-partial-view")?&view:NULL);nvnTextureViewSetDefaults(&view);
    NVNsamplerBuilder sb;NVNsampler sampler;nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);
    nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST_MIPMAP_NEAREST,NVN_MAG_FILTER_NEAREST);CHECK(nvnSamplerInitialize(&sampler,&sb));
    nvnSamplerPoolRegisterSampler(&sp,4,&sampler);uint64_t cubeHandle=nvnDeviceGetTextureHandle(&device,3,4);
    controls[0][8]=50;controls[1][8]=51;codes[0][8]=50;codes[1][8]=51;memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    int wrong=!strcmp(argv[1],"cube-wrong-shader-type");char cubeMSL[1600];snprintf(cubeMSL,sizeof cubeMSL,
      "#include <metal_stdlib>\nusing namespace metal;vertex float4 cv(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}"
      "fragment float4 cf(float4 p [[position]],%s<float> t [[texture(3)]],sampler s [[sampler(0)]]){"
      "float3 directions[6]={float3(1,0,0),float3(-1,0,0),float3(0,1,0),float3(0,-1,0),float3(0,0,1),float3(0,0,-1)};"
      "uint face=min(uint(p.x),5u),lod=min(uint(p.y),2u);if(t.get_width()!=%uu||t.get_num_mip_levels()!=%uu)return float4(0);return t.sample(s,%s,level(float(lod)));}",
      wrong?"texture2d":"texturecube",selected?8u:16u,selected?2u:3u,wrong?"float2(.5f)":"directions[face]");
    botw_graphics_shader_binding bindings[]={{BOTW_GRAPHICS_TEXTURE,0,3},{BOTW_GRAPHICS_SAMPLER,0,0}};
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,cubeMSL,"cv"));
    CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,cubeMSL,"cf",bindings,2));CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
    nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,cubeHandle);
    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    for(unsigned y=0;y<4;y++)for(unsigned face=0;face<6;face++){
      unsigned level=selected?(y<1?1:2):(y<3?y:2);CHECK(!memcmp(pixels+(y*6+face)*4,colors[face][level],4));
    }
    nvnTextureFinalize(&cube);nvnMemoryPoolFinalize(&cubePool);free(cubeMemory);puts("GPU_NATIVE_CUBE_SIX_FACES_THREE_MIPS_AND_SELECTED_VIEW_OK");
'''
        source = baseline.METAL_SMOKE.replace('nvnTextureBuilderSetSize2D(&tb,4,4);',
            'nvnTextureBuilderSetSize2D(&tb,6,4);').replace('unsigned char pixels[64];',
            'unsigned char pixels[6*4*4];')
        source = source.replace('    nvnMemoryPoolFinalize(&pool);', feature+'    nvnMemoryPoolFinalize(&pool);')
        original_run = subprocess.run
        def checked_run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            self.assertNotIn('failed assertion', result.stderr)
            self.assertNotIn('MTLReportFailure', result.stderr)
            if result.args and str(result.args[0]).endswith('/state') and result.returncode != 77:
                self.assertIn('Metal API Validation Enabled', result.stderr)
            return result
        with patch.dict(os.environ, {'MTL_DEBUG_LAYER': '1'}), \
                patch.object(baseline.subprocess, 'run', side_effect=checked_run):
            baseline.MetalBridgeTests.compile_render_probe(self, source, cases)

    def test_cube_six_faces_three_mips_native_sampling_same_cube_view_and_dimension_boundaries(self):
        self._cube_descriptor_probe([
            ('cube-sample', 'GPU_NATIVE_CUBE_SIX_FACES_THREE_MIPS_AND_SELECTED_VIEW_OK', True),
            ('cube-selected-view', 'GPU_NATIVE_CUBE_SIX_FACES_THREE_MIPS_AND_SELECTED_VIEW_OK', True),
            ('cube-wrong-shader-type', 'shader texture type differs', False),
            ('cube-partial-view', 'view', False),
            ('cube-nonsquare', 'CUBE_NONSQUARE_REJECTED_WITHOUT_GPU_RESOURCE_OK', True)])

    def test_cube_six_face_import_keeps_live_and_retired_private_alias_authority(self):
        self._cube_descriptor_probe([
            ('cube-live-authority', 'texture storage authority', False),
            ('cube-retired-authority', 'texture storage authority', False)])

    def test_cube_public_six_face_write_read_padding_cache_and_explicit_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'texture-cube'
            graphics = ROOT / 'native/botw_graphics'
            built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
                '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'),
                str(graphics / 'tools/probe_texture_family.c'), '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(executable)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            for mode in ['cube-write', 'cube-cached', 'cube-boundaries', 'cube-empty']:
                with self.subTest(mode=mode):
                    run = subprocess.run([str(executable), mode], capture_output=True, text=True,
                        timeout=60, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
                    if run.returncode == 77:
                        self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                    self.assertIn('TEXTURE_CUBE_', run.stdout)
                    self.assertIn('Metal API Validation Enabled', run.stderr)
                    self.assertNotIn('failed assertion', run.stderr)
                    self.assertNotIn('MTLReportFailure', run.stderr)

    def test_single_slice_3d_public_write_read_gob_padding_and_explicit_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'texture-3d'
            graphics = ROOT / 'native/botw_graphics'
            built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
                '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'),
                str(graphics / 'tools/probe_texture_family.c'), '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(executable)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            for mode in ['3d', '3d-wide', '3d-boundaries', '3d-cached', '3d-empty']:
                with self.subTest(mode=mode):
                    run = subprocess.run([str(executable), mode], capture_output=True, text=True,
                        timeout=60, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
                    if run.returncode == 77:
                        self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                    self.assertIn('TEXTURE_3D_SINGLE_SLICE_', run.stdout)
                    self.assertIn('Metal API Validation Enabled', run.stderr)
                    self.assertNotIn('failed assertion', run.stderr)

    def test_single_layer_1d_array_public_writes_readback_and_explicit_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'texture-1d-array'
            graphics = ROOT / 'native/botw_graphics'
            built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
                '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'),
                str(graphics / 'tools/probe_texture_family.c'), '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(executable)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            for mode in ['1da', '1da-wide', '1da-boundaries', '1da-cached', '1da-empty']:
                with self.subTest(mode=mode):
                    run = subprocess.run([str(executable), mode], capture_output=True, text=True,
                        timeout=60, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
                    if run.returncode == 77:
                        self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                    self.assertIn('TEXTURE_1D_ARRAY_SINGLE_LAYER_', run.stdout)
                    self.assertIn('Metal API Validation Enabled', run.stderr)
                    self.assertNotIn('failed assertion', run.stderr)

    def test_non_array_1d_uses_real_private_texture_and_preserves_explicit_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'texture-1d'
            graphics = ROOT / 'native/botw_graphics'
            built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
                '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'),
                str(graphics / 'tools/probe_texture_family.c'), '-framework', 'Foundation',
                '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(executable)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for mode in ['1d-init', '1d', '1d-wide', '1d-packaged', '1d-cached', '1d-boundaries', '1d-views',
                         '1d-virtual', '1d-stale']:
                with self.subTest(mode=mode):
                    run = subprocess.run([str(executable), mode], capture_output=True,
                                         text=True, timeout=60,
                                         env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
                    if run.returncode == 77:
                        self.skipTest('Metal unavailable')
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    self.assertIn('TEXTURE_1D_', run.stdout)
                    self.assertNotIn('failed assertion', run.stderr)

    def _one_dimensional_descriptor_probe(self, array=False, volume=False):
        import test_botw_graphics as baseline
        from unittest.mock import patch
        feature = r'''
    API(nvnTextureBuilderSetTarget);API(nvnTextureBuilderSetSize1D);API(nvnTextureWriteTexels);API(nvnTextureFinalize);
    API(nvnTexturePoolInitialize);API(nvnTexturePoolRegisterTexture);API(nvnSamplerPoolInitialize);API(nvnSamplerPoolRegisterSampler);
    API(nvnSamplerBuilderSetDefaults);API(nvnSamplerBuilderSetDevice);API(nvnSamplerBuilderSetMinMagFilter);API(nvnSamplerInitialize);
    API(nvnTextureViewSetDefaults);API(nvnTextureViewSetTarget);API(nvnTextureViewSetSwizzle);
    API(nvnDeviceGetTextureHandle);API(nvnCommandBufferSetTexturePool);API(nvnCommandBufferSetSamplerPool);API(nvnCommandBufferBindTexture);
    unsigned char *oneMemory=NULL;CHECK(!posix_memalign((void**)&oneMemory,page,page*4));memset(oneMemory,0,page*4);NVNmemoryPool onePool;nvnMemoryPoolBuilderSetStorage(&mb,oneMemory,page*4);CHECK(nvnMemoryPoolInitialize(&onePool,&mb));
    int wide=argc>1&&!strcmp(argv[1],"1d-sample-wide");unsigned oneWidth=wide?33:4;NVNtexture one;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);nvnTextureBuilderSetTarget(&tb,NVN_TEXTURE_TARGET_1D);nvnTextureBuilderSetSize1D(&tb,oneWidth);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);nvnTextureBuilderSetStorage(&tb,&onePool,0);CHECK(nvnTextureInitialize(&one,&tb));
    unsigned char onePixels[132];for(unsigned x=0;x<oneWidth;x++)for(unsigned c=0;c<4;c++)onePixels[x*4+c]=c==3?255:(unsigned char)(1+(x*7+c*31+13)%230);NVNcopyRegion full={0,0,0,(int)oneWidth,1,1};nvnTextureWriteTexels(&one,NULL,&full,onePixels);
    if(argc>1&&(!strcmp(argv[1],"1d-live-authority")||!strcmp(argv[1],"1d-retired-authority"))){
      NVNtexture alias;nvnTextureBuilderSetTarget(&tb,NVN_TEXTURE_TARGET_2D);nvnTextureBuilderSetSize2D(&tb,4,1);CHECK(nvnTextureInitialize(&alias,&tb));const NVNtexture *aliasTarget[]={&alias};
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,aliasTarget,NULL,NULL,NULL);nvnCommandBufferSetScissor(&cb,0,0,4,1);float aliasColor[]={.2f,.4f,.6f,1};nvnCommandBufferClearColor(&cb,0,aliasColor,15);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
      if(!strcmp(argv[1],"1d-retired-authority"))nvnTextureFinalize(&alias);CHECK(botw_graphics_read_texture(&one,onePixels,sizeof onePixels));return 2;
    }
    NVNtexturePool tp;NVNsamplerPool sp;CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16));CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));NVNtextureView view;nvnTextureViewSetDefaults(&view);nvnTextureViewSetTarget(&view,NVN_TEXTURE_TARGET_1D);
    int swizzle=argc>1&&!strcmp(argv[1],"1d-sample-view");if(swizzle)nvnTextureViewSetSwizzle(&view,NVN_TEXTURE_SWIZZLE_B,NVN_TEXTURE_SWIZZLE_G,NVN_TEXTURE_SWIZZLE_R,NVN_TEXTURE_SWIZZLE_A);nvnTexturePoolRegisterTexture(&tp,3,&one,&view);nvnTextureViewSetDefaults(&view);
    NVNsamplerBuilder sb;NVNsampler sampler;nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST,NVN_MAG_FILTER_NEAREST);CHECK(nvnSamplerInitialize(&sampler,&sb));nvnSamplerPoolRegisterSampler(&sp,4,&sampler);uint64_t textureHandle=nvnDeviceGetTextureHandle(&device,3,4);
    controls[0][8]=32;controls[1][8]=33;codes[0][8]=32;codes[1][8]=33;memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    int wrong=argc>1&&!strcmp(argv[1],"1d-wrong-shader-type");char oneMSL[1200];snprintf(oneMSL,sizeof oneMSL,"#include <metal_stdlib>\nusing namespace metal;vertex float4 ov(uint v [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1);}fragment float4 of(float4 p [[position]],%s<float> t [[texture(3)]],sampler s [[sampler(0)]]){return t.sample(s,%s);}",wrong?"texture2d":"texture1d",wrong?"float2(p.x/4.0f,.5f)":wide?"(float((uint(p.x)*32u)/3u)+.5f)/33.0f":"p.x/4.0f");
    botw_graphics_shader_binding bindings[]={{BOTW_GRAPHICS_TEXTURE,0,3},{BOTW_GRAPHICS_SAMPLER,0,0}};CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,oneMSL,"ov"));CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,oneMSL,"of",bindings,2));CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,0,textureHandle);nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++)for(unsigned c=0;c<4;c++){unsigned component=swizzle&&c<3?2-c:c,sample=wide?x*32/3:x;CHECK(abs((int)pixels[(y*4+x)*4+c]-(int)onePixels[sample*4+component])<=1);}
    nvnTextureFinalize(&one);nvnMemoryPoolFinalize(&onePool);free(oneMemory);puts("GPU_NATIVE_TEXTURE1D_NONZERO_COLUMNS_AND_SELECTED_VIEW_OK");
'''
        source = baseline.METAL_SMOKE.replace('    nvnMemoryPoolFinalize(&pool);',
            feature + '    nvnMemoryPoolFinalize(&pool);')
        if array:
            source = source.replace('NVN_TEXTURE_TARGET_1D', 'NVN_TEXTURE_TARGET_1D_ARRAY')
            source = source.replace('wrong?"texture2d":"texture1d"', 'wrong?"texture2d":"texture1d_array"')
            source = source.replace('"(float((uint(p.x)*32u)/3u)+.5f)/33.0f":"p.x/4.0f"',
                                    '"(float((uint(p.x)*32u)/3u)+.5f)/33.0f,0u":"p.x/4.0f,0u"')
        if volume:
            source = source.replace('NVN_TEXTURE_TARGET_1D', 'NVN_TEXTURE_TARGET_3D')
            source = source.replace('API(nvnTextureBuilderSetSize1D);', 'API(nvnTextureBuilderSetSize3D);')
            source = source.replace('unsigned oneWidth=wide?33:4;', 'unsigned oneWidth=wide?33:4,oneHeight=wide?9:4;')
            source = source.replace('nvnTextureBuilderSetSize1D(&tb,oneWidth);', 'nvnTextureBuilderSetSize3D(&tb,oneWidth,oneHeight,1);')
            source = source.replace('unsigned char onePixels[132];for(unsigned x=0;x<oneWidth;x++)for(unsigned c=0;c<4;c++)onePixels[x*4+c]=c==3?255:(unsigned char)(1+(x*7+c*31+13)%230);NVNcopyRegion full={0,0,0,(int)oneWidth,1,1};', 'unsigned char onePixels[1188];for(unsigned y=0;y<oneHeight;y++)for(unsigned x=0;x<oneWidth;x++)for(unsigned c=0;c<4;c++)onePixels[(y*oneWidth+x)*4+c]=c==3?255:(unsigned char)(1+(x*7+y*19+c*31+13)%230);NVNcopyRegion full={0,0,0,(int)oneWidth,(int)oneHeight,1};')
            source = source.replace('wrong?"texture2d":"texture1d"', 'wrong?"texture2d":"texture3d"')
            source = source.replace('"(float((uint(p.x)*32u)/3u)+.5f)/33.0f":"p.x/4.0f"', '"float3((float((uint(p.x)*32u)/3u)+.5f)/33.0f,(float((uint(p.y)*8u)/3u)+.5f)/9.0f,.5f)":"float3(p.x/4.0f,p.y/4.0f,.5f)"')
            source = source.replace('sample=wide?x*32/3:x;', 'sample=(wide?y*8/3:y)*oneWidth+(wide?x*32/3:x);')
            source = source.replace('GPU_NATIVE_TEXTURE1D_NONZERO_COLUMNS_AND_SELECTED_VIEW_OK', 'GPU_NATIVE_TEXTURE3D_NONZERO_GRID_AND_SELECTED_VIEW_OK')
        original_run = subprocess.run
        def checked_run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            self.assertNotIn('failed assertion', result.stderr)
            if volume and result.args and str(result.args[0]).endswith('/state') and result.returncode != 77:
                self.assertIn('Metal API Validation Enabled', result.stderr)
            return result
        with patch.dict(os.environ, {'MTL_DEBUG_LAYER': '1'}), \
                patch.object(baseline.subprocess, 'run', side_effect=checked_run):
            baseline.MetalBridgeTests.compile_render_probe(self, source, [
                ('1d-sample', 'GPU_NATIVE_TEXTURE3D_NONZERO_GRID_AND_SELECTED_VIEW_OK' if volume else 'GPU_NATIVE_TEXTURE1D_NONZERO_COLUMNS_AND_SELECTED_VIEW_OK', True),
                ('1d-sample-wide', 'GPU_NATIVE_TEXTURE3D_NONZERO_GRID_AND_SELECTED_VIEW_OK' if volume else 'GPU_NATIVE_TEXTURE1D_NONZERO_COLUMNS_AND_SELECTED_VIEW_OK', True),
                ('1d-sample-view', 'GPU_NATIVE_TEXTURE3D_NONZERO_GRID_AND_SELECTED_VIEW_OK' if volume else 'GPU_NATIVE_TEXTURE1D_NONZERO_COLUMNS_AND_SELECTED_VIEW_OK', True),
                ('1d-wrong-shader-type', 'shader texture type differs', False),
                ('1d-live-authority', 'texture storage authority', False),
                ('1d-retired-authority', 'texture storage authority', False)])

    def test_single_slice_3d_native_shader_type_selected_view_and_private_alias_authority(self):
        self._one_dimensional_descriptor_probe(volume=True)

    def test_non_array_1d_descriptor_sampling_type_guard_and_private_alias_authority(self):
        self._one_dimensional_descriptor_probe()

    def test_single_layer_1d_array_uses_native_array_shader_type_and_layer_zero(self):
        self._one_dimensional_descriptor_probe(array=True)

    def test_native_builder_view_and_pool_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'texture-family'
            command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
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
