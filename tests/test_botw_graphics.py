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

    def test_reflection_preserves_explicit_sampler_slots(self):
        data = bytearray(synthetic_bnsh() + bytes(0x400))
        struct.pack_into('<I', data, 0x1c, len(data))
        struct.pack_into('<Q', data, 0x178, 0x600)
        struct.pack_into('<Q', data, 0x620, 0x640)  # fragment reflection
        struct.pack_into('<Q', data, 0x650, 0x700)  # sampler dictionary
        struct.pack_into('<IIIIQ', data, 0x668, 0, 0, 1, 1, 0x780)
        data[0x700:0x704] = b'_DIC'
        struct.pack_into('<I', data, 0x704, 1)
        struct.pack_into('<Q', data, 0x720, 0x800)
        struct.pack_into('<H', data, 0x800, 5)
        data[0x802:0x807] = b'image'
        struct.pack_into('<i', data, 0x780, 3)
        reflection = self.parser().parse_bnsh(data)['variations'][0].get('reflection')
        self.assertIsNotNone(reflection, 'BNSH reflection parsing is missing')
        self.assertEqual(reflection['fragment']['samplers'], [{'name': 'image', 'slot': 3}])

    def test_texture_constraints_require_explicit_reflected_slots(self):
        module = self.parser()
        self.assertTrue(hasattr(module, 'texture_constraints'), 'Texture constraints are missing')
        rows = [{'reflection': {'samplers': [{'name': 'one', 'slot': 0}, {'name': 'two', 'slot': 1}]}}]
        result = module.texture_constraints(rows)
        self.assertEqual([(x['cbuf_index'], x['cbuf_offset'], x['nvn_slot']) for x in result],
                         [(0, 0x20, 0), (0, 0x28, 1)])
        self.assertTrue(all(x['runtime_required_type'] == '2d' for x in result))
        with self.assertRaisesRegex(ValueError, 'slot'):
            module.texture_constraints([{'reflection': {'samplers': [{'name': 'bad', 'slot': 128}]}}])

    def test_truncation_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError, 'size|bounds'):
            self.parser().parse_bnsh(synthetic_bnsh()[:-1])


class ShaderBundleTests(unittest.TestCase):
    def test_stager_cli_preserves_exact_stage_identities_and_counts(self):
        import json
        module = BnshTests().parser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            rows = module.extract_shader_inputs(synthetic_bnsh(), inputs)
            converted.mkdir()
            for row in rows:
                (converted / (row['code_sha256'] + '.metal')).write_text(
                    row['stage'] + ' float4 main0() {return 0;}')
            self.assertEqual(module.main(['--bundle-inputs', str(inputs), '--bundle-compiled', str(converted),
                                          '--bundle-dir', str(bundle)]), 0)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            self.assertEqual(len(manifest['entries']), 2)
            self.assertEqual(len(manifest['stage_records']), 2)
            self.assertTrue(all(x['status'] == 'ready' for x in manifest['entries']))
            for entry in manifest['entries']:
                self.assertEqual((bundle / entry['code']['path']).read_bytes(),
                                 (inputs / entry['code']['path']).read_bytes())

    def test_binding_map_requires_reflection_and_rejects_unknown_driver_banks(self):
        module = BnshTests().parser()
        self.assertTrue(hasattr(module, 'shader_binding_map'), 'Private bundle binding mapper is missing')
        reflection = {'uniforms': [{'name': 'ubo', 'slot': 0}],
                      'samplers': [{'name': 'image', 'slot': 1}]}
        msl = 'fragment float4 main0(constant Block& c3 [[buffer(2)]], texture2d<float> fs_tex0_28 [[texture(3)]], sampler fs_tex0_28Smplr [[sampler(0)]]) {return 0;}'
        result = module.shader_binding_map(msl, 'fragment', reflection)
        self.assertEqual([(x['kind'], x['nvn_slot'], x['metal_slot']) for x in result['bindings']],
                         [('uniform', 0, 2), ('texture', 1, 3), ('sampler', 1, 0)])
        with self.assertRaisesRegex(ValueError, 'c1|private|driver'):
            module.shader_binding_map(msl.replace('c3 [[', 'c1 [['), 'fragment', reflection)
        with self.assertRaisesRegex(ValueError, 'reflection'):
            module.shader_binding_map(msl, 'fragment', {'uniforms': [], 'samplers': []})


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

    def test_vertex_uniform_sampler_block_linear_and_depth_pixels(self):
        implemented = (ROOT / 'native/botw_graphics/implemented.inc').read_text()
        self.assertIn('NVN_BIND(nvnCommandBufferBindUniformBuffer)', implemented,
                      'GPU resource binding implementation is missing')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            source = ROOT / 'native/botw_graphics/graphics.m'
            harness = path / 'resources.c'
            harness.write_text(RESOURCE_SMOKE)
            exe = path / 'resources'
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fblocks',
                '-Werror', '-Wno-deprecated-declarations', '-I', str(source.parent),
                str(source), str(harness), '-framework', 'Foundation', '-framework',
                'Metal', '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK', result.stdout)
            for mode, expected in (('bad-bindings', 'binding map'),
                                   ('short-uniform', 'uniform range'),
                                   ('short-vertex', 'vertex range')):
                result = subprocess.run([str(exe), mode], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, mode)
                self.assertIn(expected, result.stderr)


    def test_private_bundle_loader_checks_hashes_paths_and_unsupported_counts(self):
        import json
        source = ROOT / 'native/botw_graphics/shader_bundle.m'
        self.assertTrue(source.exists(), 'Private shader loader is missing')
        module = BnshTests().parser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            inputs, converted, bundle = path / 'inputs', path / 'converted', path / 'bundle'
            rows = module.extract_shader_inputs(synthetic_bnsh(), inputs)
            converted.mkdir()
            for row in rows:
                if row['stage'] == 'fragment':
                    row['reflection'] = {'uniforms': [{'name': 'fixtureUbo', 'slot': 0}],
                                         'samplers': [{'name': 'fixtureTexture', 'slot': 1}]}
                msl = ('#include <metal_stdlib>\nusing namespace metal; vertex float4 main0(uint i [[vertex_id]]) {return float4(0,0,0,1);}'
                       if row['stage'] == 'vertex' else
                       '#include <metal_stdlib>\nusing namespace metal; struct Block {float4 tint;}; fragment float4 main0(constant Block& c3 [[buffer(2)]], texture2d<float> fs_tex0_28 [[texture(3)]], sampler fs_tex0_28Smplr [[sampler(0)]]) {return c3.tint*fs_tex0_28.sample(fs_tex0_28Smplr,float2(0));}')
                (converted / (row['code_sha256'] + '.metal')).write_text(msl)
            description = json.loads((inputs / 'inputs.json').read_text())
            description['records'] = rows
            (inputs / 'inputs.json').write_text(json.dumps(description))
            staged = module.stage_shader_bundle(inputs, converted, bundle)
            self.assertEqual((staged['ready'], staged['unsupported']), (2, 0))
            harness = path / 'bundle.c'
            harness.write_text(BUNDLE_SMOKE)
            exe = path / 'bundle-test'
            graphics = source.parent
            result = subprocess.run(['/usr/bin/xcrun', 'clang', '-fobjc-arc', '-fblocks', '-Wall', '-Wextra', '-Werror',
                '-I', str(graphics), str(graphics / 'graphics.m'), str(source), str(harness),
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            if result.returncode == 77:
                self.skipTest('No Metal device')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('REGISTERED=2 UNSUPPORTED=0 TOTAL=2', result.stdout)
            manifest = json.loads((bundle / 'manifest.json').read_text())
            entry = manifest['entries'][0]
            for field, invalid, expected in (
                    ('texture_types', [{'nvn_slot': 0, 'metal_slot': 0, 'type': 'cube'}], 'texture'),
                    ('vertex_inputs', [0], 'vertex')):
                saved = entry[field]; entry[field] = invalid
                (bundle / 'manifest.json').write_text(json.dumps(manifest))
                result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, field)
                self.assertIn(expected, result.stderr)
                entry[field] = saved
            fragment = next(x for x in manifest['entries'] if x['stage'] == 'fragment')
            fragment['reflection']['uniforms'].append({'name': 'misleadingSlot', 'slot': 1})
            fragment['bindings'][0]['nvn_slot'] = 1
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('source resource binding map', result.stderr)
            fragment['reflection']['uniforms'].pop()
            fragment['bindings'][0]['nvn_slot'] = 0
            record = manifest['stage_records'][0]
            saved = record['entry']; record['entry'] = 100
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('stage record', result.stderr)
            record['entry'] = saved
            manifest['entries'][0]['status'] = 'unsupported'
            manifest['entries'][0]['reason'] = 'Unknown private constant bank c1'
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('REGISTERED=1 UNSUPPORTED=1 TOTAL=2', result.stdout)
            self.assertIn('c1', result.stderr)
            manifest['entries'][0]['status'] = 'ready'
            entry = manifest['entries'][0]
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            code = bundle / entry['code']['path']
            original = code.read_bytes()
            damaged = bytearray(original); damaged[8] ^= 1; code.write_bytes(damaged)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SHA256', result.stderr)
            code.write_bytes(original)
            saved = entry['code']['path']; entry['code']['path'] = '../escape.nvn'
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('relative', result.stderr)
            entry['code']['path'] = saved
            (bundle / 'manifest.json').write_text(json.dumps(manifest))
            outside = path / 'outside.nvn'; outside.write_bytes(original)
            code.unlink(); code.symlink_to(outside)
            result = subprocess.run([str(exe), str(bundle)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('symlink', result.stderr)


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


RESOURCE_FEATURE = r"""
    API(nvnTexturePoolInitialize); API(nvnTexturePoolRegisterTexture);
    API(nvnSamplerPoolInitialize); API(nvnSamplerPoolRegisterSampler);
    API(nvnSamplerBuilderSetDefaults); API(nvnSamplerBuilderSetDevice);
    API(nvnSamplerBuilderSetMinMagFilter); API(nvnSamplerBuilderSetWrapMode); API(nvnSamplerInitialize);
    API(nvnDeviceGetTextureHandle); API(nvnCommandBufferSetTexturePool); API(nvnCommandBufferSetSamplerPool);
    API(nvnCommandBufferBindTexture); API(nvnCommandBufferBindUniformBuffer);
    API(nvnVertexAttribStateSetDefaults); API(nvnVertexAttribStateSetFormat); API(nvnVertexAttribStateSetStreamIndex);
    API(nvnVertexStreamStateSetDefaults); API(nvnVertexStreamStateSetStride);
    API(nvnCommandBufferBindVertexAttribState); API(nvnCommandBufferBindVertexStreamState); API(nvnCommandBufferBindVertexBuffer);
    API(nvnDepthStencilStateSetDefaults); API(nvnDepthStencilStateSetDepthTestEnable);
    API(nvnDepthStencilStateSetDepthWriteEnable); API(nvnDepthStencilStateSetDepthFunc);
    API(nvnCommandBufferBindDepthStencilState); API(nvnCommandBufferClearDepthStencil);
    NVNtexture tiled; nvnTextureBuilderSetDefaults(&tb); nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,2,2); nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_RGBA8);
    nvnTextureBuilderSetStorage(&tb,&pool,6144);
    /* Independent hand-calculated single-GOB addresses: x=0/4,y=0/1. */
    unsigned char *tile=memory+6144; memset(tile,0,512);
    const unsigned char texels[4][4]={{255,0,0,255},{0,255,0,255},{255,255,255,255},{0,0,255,255}};
    memcpy(tile,texels[0],4);memcpy(tile+4,texels[1],4);memcpy(tile+16,texels[2],4);memcpy(tile+20,texels[3],4);
    CHECK(nvnTextureInitialize(&tiled,&tb));
    NVNtexturePool tp; NVNsamplerPool sp;
    CHECK(nvnTexturePoolInitialize(&tp,&pool,0,16)); CHECK(nvnSamplerPoolInitialize(&sp,&pool,1024,16));
    nvnTexturePoolRegisterTexture(&tp,3,&tiled,NULL);
    NVNsamplerBuilder sb;NVNsampler sampler;nvnSamplerBuilderSetDefaults(&sb);nvnSamplerBuilderSetDevice(&sb,&device);
    nvnSamplerBuilderSetMinMagFilter(&sb,NVN_MIN_FILTER_NEAREST,NVN_MAG_FILTER_NEAREST);
    nvnSamplerBuilderSetWrapMode(&sb,NVN_WRAP_MODE_CLAMP_TO_EDGE,NVN_WRAP_MODE_CLAMP_TO_EDGE,NVN_WRAP_MODE_CLAMP_TO_EDGE);
    CHECK(nvnSamplerInitialize(&sampler,&sb)); nvnSamplerPoolRegisterSampler(&sp,4,&sampler);
    uint64_t handleTexture=nvnDeviceGetTextureHandle(&device,3,4);
    const float positions[]={-1,-1,3,-1,-1,3};memcpy(memory+512,positions,sizeof positions);
    float vsUniform[]={0,0,.25,0},fsUniform[]={1,1,.5,1};memcpy(memory+768,vsUniform,16);memcpy(memory+784,fsUniform,16);
    NVNvertexAttribState va;NVNvertexStreamState stream;
    nvnVertexAttribStateSetDefaults(&va);nvnVertexAttribStateSetFormat(&va,NVN_FORMAT_RG32F,0);nvnVertexAttribStateSetStreamIndex(&va,0);
    nvnVertexStreamStateSetDefaults(&stream);nvnVertexStreamStateSetStride(&stream,8);
    controls[0][8]=4;controls[1][8]=5;codes[0][8]=4;codes[1][8]=5;
    memcpy(memory+128,codes[0],128);memcpy(memory+256,codes[1],128);
    const char *resourceMSL="#include <metal_stdlib>\nusing namespace metal;struct In{float2 p [[attribute(0)]];};struct O{float4 p [[position]];};"
      "vertex O rv(In v [[stage_in]],constant float4& u [[buffer(0)]]) {return {float4(v.p+u.xy,u.z,1)};}"
      "fragment float4 rf(O v [[stage_in]],constant float4& c [[buffer(1)]],texture2d<float> t [[texture(3)]],sampler s [[sampler(0)]]) {return t.sample(s,float2(.75,.75))*c;}";
    botw_graphics_shader_binding vb[]={{BOTW_GRAPHICS_UNIFORM,5,0}};
    botw_graphics_shader_binding fb[]={{BOTW_GRAPHICS_UNIFORM,7,1},{BOTW_GRAPHICS_TEXTURE,9,3},{BOTW_GRAPHICS_SAMPLER,9,0}};
    if(argc>1&&!strcmp(argv[1],"bad-bindings"))fb[1].metal_slot=2;
    CHECK(botw_graphics_register_shader_with_bindings(controls[0],64,codes[0],128,NVN_SHADER_STAGE_VERTEX,resourceMSL,"rv",vb,1));
    CHECK(botw_graphics_register_shader_with_bindings(controls[1],64,codes[1],128,NVN_SHADER_STAGE_FRAGMENT,resourceMSL,"rf",fb,3));
    CHECK(nvnProgramSetShaders(&program,2,sd));
    NVNtexture depth;nvnTextureBuilderSetDefaults(&tb);nvnTextureBuilderSetDevice(&tb,&device);
    nvnTextureBuilderSetSize2D(&tb,4,4);nvnTextureBuilderSetFormat(&tb,NVN_FORMAT_DEPTH32F);nvnTextureBuilderSetStorage(&tb,&pool,8192);
    CHECK(nvnTextureInitialize(&depth,&tb));
    NVNdepthStencilState ds;nvnDepthStencilStateSetDefaults(&ds);nvnDepthStencilStateSetDepthTestEnable(&ds,1);
    nvnDepthStencilStateSetDepthWriteEnable(&ds,1);nvnDepthStencilStateSetDepthFunc(&ds,NVN_DEPTH_FUNC_LESS);
    for(int pass=0;pass<2;pass++){
      if(pass){vsUniform[2]=.75;fsUniform[0]=fsUniform[1]=fsUniform[2]=0;memcpy(memory+768,vsUniform,16);memcpy(memory+784,fsUniform,16);}
      nvnCommandBufferBeginRecording(&cb);nvnCommandBufferSetRenderTargets(&cb,1,targets,NULL,&depth,NULL);
      if(!pass)nvnCommandBufferClearDepthStencil(&cb,1,1,0,0);
      nvnCommandBufferBindDepthStencilState(&cb,&ds);nvnCommandBufferBindProgram(&cb,&program,NVN_SHADER_STAGE_ALL_GRAPHICS_BITS);
      nvnCommandBufferBindVertexAttribState(&cb,1,&va);nvnCommandBufferBindVertexStreamState(&cb,1,&stream);
      nvnCommandBufferBindVertexBuffer(&cb,0,gpu+512,(argc>1&&!strcmp(argv[1],"short-vertex"))?4:sizeof positions);
      nvnCommandBufferBindUniformBuffer(&cb,NVN_SHADER_STAGE_VERTEX,5,gpu+768,(argc>1&&!strcmp(argv[1],"short-uniform"))?4:16);
      nvnCommandBufferBindUniformBuffer(&cb,NVN_SHADER_STAGE_FRAGMENT,7,gpu+784,16);
      nvnCommandBufferSetTexturePool(&cb,&tp);nvnCommandBufferSetSamplerPool(&cb,&sp);
      nvnCommandBufferBindTexture(&cb,NVN_SHADER_STAGE_FRAGMENT,9,handleTexture);
      nvnCommandBufferDrawArrays(&cb,NVN_DRAW_PRIMITIVE_TRIANGLES,0,3);handle=nvnCommandBufferEndRecording(&cb);
      nvnQueueSubmitCommands(&queue,1,&handle);nvnQueueFinish(&queue);
      CHECK(botw_graphics_read_texture(&texture,pixels,sizeof pixels));
      CHECK(pixels[0]==0 && pixels[1]==0 && pixels[2]>=127 && pixels[2]<=128 && pixels[3]==255);
    }
    CHECK(botw_graphics_presented_frame_count()==0); /* Offscreen work is not a presented game frame. */
    puts("GPU_VERTEX_UNIFORM_SAMPLER_BLOCK_LINEAR_DEPTH_OK");
"""
RESOURCE_SMOKE = METAL_SMOKE.replace("    nvnMemoryPoolFinalize(&pool);", RESOURCE_FEATURE + "    nvnMemoryPoolFinalize(&pool);")


BUNDLE_SMOKE = r"""
#include "graphics.h"
#include "shader_bundle.h"
#include <stdio.h>
int main(int argc,char **argv){
    if(argc!=2)return 3;
    if(!botw_graphics_init(NULL,NULL,NULL))return 77;
    botw_graphics_shader_bundle_result result;char error[1024];
    int ok=botw_graphics_load_shader_bundle(argv[1],&result,error,sizeof error);
    if(!ok){fprintf(stderr,"%s\n",error);botw_graphics_shutdown();return 2;}
    printf("REGISTERED=%zu UNSUPPORTED=%zu TOTAL=%zu\n",result.registered_entries,result.unsupported_entries,result.declared_entries);
    botw_graphics_shutdown();return 0;
}
"""


if __name__ == '__main__':
    unittest.main()
