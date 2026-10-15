"""Synthetic-only fixtures; no game bytecode or derived shader is checked in."""
import importlib.util
from pathlib import Path
import struct
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def synthetic_bnsh():
    data = bytearray(0x600)
    data[:8] = b'BNSH\0\0\0\0'
    struct.pack_into('<I', data, 8, 0x00020102)
    data[12:16] = b'\xff\xfe\x0c\0'
    struct.pack_into('<IHHII', data, 0x10, 0x500, 0, 0x60, 0, len(data))
    data[0x60:0x64] = b'grsc'
    struct.pack_into('<III', data, 0x64, 0, 0x4a0, 0)
    struct.pack_into('<HHHHIIQQ', data, 0x70, 4, 0, 0, 0, 0x800, 1, 0xc0, 0)
    struct.pack_into('<QQQQ', data, 0xc0, 0, 0, 0x100, 0x60)
    struct.pack_into('<H', data, 0x100, 2)
    struct.pack_into('<Q', data, 0x108, 0x1a0)
    struct.pack_into('<Q', data, 0x128, 0x1e0)
    for descriptor, control, code, tag in ((0x1a0, 0x240, 0x300, 1),
                                            (0x1e0, 0x280, 0x400, 2)):
        struct.pack_into('<QQQII', data, descriptor, 0, control, code, 0x80, 0x40)
        struct.pack_into('<II', data, control, 0x98761234, tag)
        struct.pack_into('<II', data, code, 0x12345678, tag)
    data[0x500:0x508] = b'fixture\0'
    return bytes(data)


class BnshTests(unittest.TestCase):
    def parser(self):
        spec = importlib.util.find_spec('scripts.botw_native.shaders')
        self.assertIsNotNone(spec, 'BNSH parser has not been implemented')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_parses_real_layout_and_stable_stage_identity(self):
        result = self.parser().parse_bnsh(synthetic_bnsh())
        self.assertEqual(result['name'], 'fixture')
        self.assertEqual(result['version'], '2.1.2')
        self.assertEqual([s['stage'] for s in result['variations'][0]['shaders']],
                         ['vertex', 'fragment'])
        shader = result['variations'][0]['shaders'][0]
        self.assertEqual((shader['control_offset'], shader['code_offset']), (0x240, 0x300))
        self.assertEqual((shader['control_bytes'], shader['code_bytes']), (0x40, 0x80))
        self.assertEqual(len(shader['control_sha256']), 64)

    def test_embedded_scan_uses_declared_container_boundary(self):
        module = self.parser()
        data = b'prefix' + synthetic_bnsh() + b'non-shader-trailer'
        result = module.inspect_embedded_bnsh(data)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['offset'], result[0]['file_bytes']), (6, 0x600))

    def test_out_of_file_stage_pointer_is_rejected(self):
        data = bytearray(synthetic_bnsh())
        struct.pack_into('<Q', data, 0x108, 0x5f8)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.parser().parse_bnsh(data)

    def test_relocated_container_is_not_treated_as_file_offsets(self):
        data = bytearray(synthetic_bnsh())
        struct.pack_into('<H', data, 0x14, 1)
        with self.assertRaisesRegex(ValueError, 'relocat'):
            self.parser().parse_bnsh(data)

    def test_wrong_control_magic_is_rejected(self):
        data = bytearray(synthetic_bnsh())
        data[0x240:0x244] = b'nope'
        with self.assertRaisesRegex(ValueError, 'control'):
            self.parser().parse_bnsh(data)

    def test_extracts_exact_shader_spans_without_executable_or_unknown_bytes(self):
        module = self.parser()
        self.assertTrue(hasattr(module, 'extract_shader_inputs'), 'Local extraction is missing')
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'inputs'
            result = module.extract_shader_inputs(b'prefix' + synthetic_bnsh(), destination)
            self.assertEqual(len(result), 2)
            first = result[0]
            self.assertEqual((destination / first['code_file']).read_bytes(),
                             synthetic_bnsh()[0x300:0x380])
            self.assertEqual((destination / first['control_file']).read_bytes(),
                             synthetic_bnsh()[0x240:0x280])
            with self.assertRaises(FileExistsError):
                module.extract_shader_inputs(synthetic_bnsh(), destination)

    def test_truncation_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError, 'size|bounds'):
            self.parser().parse_bnsh(synthetic_bnsh()[:-1])


@unittest.skipUnless(platform.system() == 'Darwin', 'Metal requires macOS')
class MetalBridgeTests(unittest.TestCase):
    def test_real_gpu_copy_clear_and_pipeline_and_strict_unsupported_api(self):
        source = ROOT / 'native/botw_graphics/graphics.m'
        self.assertTrue(source.exists(), 'Native Metal bridge is missing')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            harness = path / 'smoke.c'
            harness.write_text(METAL_SMOKE)
            exe = path / 'smoke'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fblocks',
                '-Werror', '-Wno-deprecated-declarations', '-I', str(source.parent),
                str(source), str(harness), '-framework', 'Foundation', '-framework',
                'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device is available in this execution context')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_COPY_CLEAR_DRAW_OK', result.stdout)
            unsupported = subprocess.run([str(exe), 'unsupported'], capture_output=True, text=True)
            self.assertNotEqual(unsupported.returncode, 0)
            self.assertIn('nvnCommandBufferDispatchCompute', unsupported.stderr)
            partial = subprocess.run([str(exe), 'partial-clear'], capture_output=True, text=True)
            self.assertNotEqual(partial.returncode, 0)
            self.assertIn('scissored clear', partial.stderr)


METAL_SMOKE = r"""
#include "graphics.h"
#include "nvn_protos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define API(name) NVNT_##name name = (NVNT_##name)botw_nvn_bootstrap_loader(#name)
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"CHECK failed: %s (%s)\n", #x, botw_graphics_last_error()); return 1; } } while(0)
static const void *ctl_ptrs[2];
static int valid(const void *p, size_t n) { return n <= 64 && (p == ctl_ptrs[0] || p == ctl_ptrs[1]); }
int main(int argc, char **argv) {
    if (!botw_graphics_init(NULL, NULL, valid)) return 77;
    if (argc > 1 && !strcmp(argv[1],"unsupported")) {
        void (*bad)(void) = botw_nvn_bootstrap_loader("nvnCommandBufferDispatchCompute");
        CHECK(bad); bad(); return 2;
    }
    API(nvnDeviceBuilderSetDefaults); API(nvnDeviceInitialize);
    API(nvnMemoryPoolBuilderSetDefaults); API(nvnMemoryPoolBuilderSetDevice);
    API(nvnMemoryPoolBuilderSetStorage); API(nvnMemoryPoolInitialize);
    API(nvnMemoryPoolGetBufferAddress); API(nvnMemoryPoolFinalize);
    API(nvnMemoryPoolInvalidateMappedRange); API(nvnMemoryPoolFlushMappedRange);
    API(nvnQueueBuilderSetDefaults); API(nvnQueueBuilderSetDevice); API(nvnQueueInitialize);
    API(nvnQueueSubmitCommands); API(nvnQueueFinish); API(nvnQueueFenceSync);
    API(nvnSyncInitialize); API(nvnSyncWait);
    API(nvnCommandBufferInitialize); API(nvnCommandBufferBeginRecording);
    API(nvnCommandBufferEndRecording); API(nvnCommandBufferCopyBufferToBuffer);
    API(nvnCommandBufferSetRenderTargets); API(nvnCommandBufferClearColor);
    API(nvnCommandBufferBindProgram); API(nvnCommandBufferDrawArrays); API(nvnCommandBufferSetScissor);
    API(nvnTextureBuilderSetDefaults); API(nvnTextureBuilderSetDevice);
    API(nvnTextureBuilderSetSize2D); API(nvnTextureBuilderSetFormat);
    API(nvnTextureBuilderSetFlags); API(nvnTextureBuilderSetStorage);
    API(nvnTextureBuilderSetStride); API(nvnTextureInitialize);
    API(nvnProgramInitialize); API(nvnProgramSetShaders);
    NVNdeviceBuilder db; NVNdevice device;
    nvnDeviceBuilderSetDefaults(&db); CHECK(nvnDeviceInitialize(&device,&db));
    size_t page=(size_t)getpagesize(); unsigned char *memory=NULL;
    CHECK(posix_memalign((void**)&memory,page,page*2)==0); memset(memory,0,page*2);
    memcpy(memory,"real gpu copy",14);
    NVNmemoryPoolBuilder mb; NVNmemoryPool pool;
    nvnMemoryPoolBuilderSetDefaults(&mb); nvnMemoryPoolBuilderSetDevice(&mb,&device);
    nvnMemoryPoolBuilderSetStorage(&mb,memory,page*2); CHECK(nvnMemoryPoolInitialize(&pool,&mb));
    NVNqueueBuilder qb; NVNqueue queue; nvnQueueBuilderSetDefaults(&qb);
    nvnQueueBuilderSetDevice(&qb,&device); CHECK(nvnQueueInitialize(&queue,&qb));
    NVNcommandBuffer cb; CHECK(nvnCommandBufferInitialize(&cb,&device));
    memory[0]='R'; /* Uncached/shared writes become GPU-visible without a flush. */
    nvnCommandBufferBeginRecording(&cb);
    uint64_t gpu=nvnMemoryPoolGetBufferAddress(&pool);
    nvnCommandBufferCopyBufferToBuffer(&cb,gpu,gpu+64,14,0);
    NVNcommandHandle handle=nvnCommandBufferEndRecording(&cb);
    CHECK(!memory[64]); nvnQueueSubmitCommands(&queue,1,&handle);
    NVNsync pending; CHECK(nvnSyncInitialize(&pending,&device));
    nvnQueueFenceSync(&queue,&pending,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    NVNsyncWaitResult waited=nvnSyncWait(&pending,10000000000ull);
    CHECK(waited==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED || waited==NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED);
    nvnQueueFinish(&queue);
    nvnMemoryPoolInvalidateMappedRange(&pool,64,14); CHECK(!memcmp(memory+64,"Real gpu copy",14));
    NVNsync sync; CHECK(nvnSyncInitialize(&sync,&device));
    CHECK(nvnSyncWait(&sync,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
    nvnQueueFenceSync(&queue,&sync,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    CHECK(nvnSyncWait(&sync,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
    NVNtextureBuilder tb; NVNtexture texture;
    nvnTextureBuilderSetDefaults(&tb); nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,4,4); nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);
    nvnTextureBuilderSetFlags(&tb,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET);
    nvnTextureBuilderSetStride(&tb,256); nvnTextureBuilderSetStorage(&tb,&pool,4096);
    CHECK(nvnTextureInitialize(&texture,&tb));
    const NVNtexture *targets[]={&texture}; float color[]={0,1,0,1};
    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    if (argc > 1 && !strcmp(argv[1],"partial-clear")) nvnCommandBufferSetScissor(&cb,1,1,2,2);
    nvnCommandBufferClearColor(&cb,0,color,15); handle=nvnCommandBufferEndRecording(&cb);
    nvnQueueSubmitCommands(&queue,1,&handle); nvnQueueFinish(&queue);
    unsigned char pixels[64]; CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==0 && pixels[1]==255 && pixels[2]==0 && pixels[3]==255);
    unsigned char controls[2][64]={{0}}, codes[2][128]={{0}};
    for (int i=0;i<2;i++) { *(uint32_t*)controls[i]=0x98761234; *(uint32_t*)codes[i]=0x12345678;
        controls[i][8]=(unsigned char)i; codes[i][8]=(unsigned char)i; ctl_ptrs[i]=controls[i];
        memcpy(memory+128+i*128,codes[i],128); }
    const char *msl="#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)}; return float4(p[v],0,1); }\n"
        "fragment float4 fs() { return float4(1,0,0,1); }\n";
    CHECK(botw_graphics_register_shader(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,msl,"vs"));
    CHECK(botw_graphics_register_shader(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,msl,"fs"));
    nvnMemoryPoolFlushMappedRange(&pool,128,256);
    NVNprogram program; CHECK(nvnProgramInitialize(&program,&device));
    NVNshaderData sd[]={{gpu+128,controls[0]},{gpu+256,controls[1]}};
    NVNshaderData bad[]={{gpu+128,NULL},{gpu+256,controls[1]}};
    CHECK(!nvnProgramSetShaders(&program,2,bad));
    CHECK(nvnProgramSetShaders(&program,2,sd));
    nvnCommandBufferBeginRecording(&cb); nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,NULL,NULL);
    nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
    nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);
    handle=nvnCommandBufferEndRecording(&cb); nvnQueueSubmitCommands(&queue,1,&handle); nvnQueueFinish(&queue);
    CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
    CHECK(pixels[0]==255 && pixels[1]==0 && pixels[2]==0 && pixels[3]==255);
    nvnMemoryPoolFinalize(&pool); botw_graphics_shutdown(); free(memory);
    puts("GPU_COPY_CLEAR_DRAW_OK"); return 0;
}
"""


if __name__ == '__main__':
    unittest.main()
