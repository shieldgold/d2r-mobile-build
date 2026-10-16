"""Base-v0 pitch/generic metadata and explicit native-kind boundaries."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('clang'), 'portable C11 compiler required')
class StorageMetadataPolicyCPUTests(unittest.TestCase):
    def test_original_depth_domain_rounding_failure_and_low_word_signed_abi(self):
        source = r'''
#include "texture_storage_policy.h"
#define CHECK(x) do { if(!(x)) return __LINE__; } while(0)
int main(void) {
    const unsigned formats[]={0x32,0x33,0x34,0x35,0x36},kinds[]={1,0x46,0x7b,0x46,0xc3};
    for(unsigned i=0;i<5;i++) {
        unsigned kind=0;CHECK(bw_texture_depth_storage_kind(formats[i],0,0,&kind)&&kind==kinds[i]);
        CHECK(bw_texture_depth_storage_kind(formats[i],0x100,0,&kind)&&kind==kinds[i]);
        for(unsigned flags=8;flags<=0x108;flags+=0x100) {kind=0xdead;CHECK(!bw_texture_depth_storage_kind(formats[i],flags,0,&kind)&&kind==0xdead);}
        for(unsigned flags=8;flags<=0x108;flags+=0x100){
            kind=0xdead;CHECK(!bw_texture_plain_depth_selected(BW_TEXTURE_KIND_METAL_PLAIN_COLOR,formats[i],flags,0,&kind)&&kind==0xdead);
            CHECK(!bw_texture_plain_depth_selected(BW_TEXTURE_KIND_UNKNOWN,formats[i],flags,0,&kind)&&kind==0xdead);
            CHECK(!bw_texture_plain_depth_selected((BWTextureKindPolicy)99,formats[i],flags,0,&kind)&&kind==0xdead);
            CHECK(bw_texture_plain_depth_selected(BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH,formats[i],flags,0,&kind)&&kind==kinds[i]);
            CHECK(bw_texture_plain_depth_layout_inputs(formats[i],flags,0));
            CHECK(bw_texture_plain_depth_gpu_inputs(formats[i],NVN_TEXTURE_TARGET_2D,flags,0)==((i==0||i==2)&&flags==8));
        }
        CHECK(!bw_texture_plain_depth_layout_inputs(formats[i],9,0)&&!bw_texture_plain_depth_gpu_inputs(formats[i],NVN_TEXTURE_TARGET_2D_ARRAY,8,0));
        const int samples[]={-1,1,2,4,8,16,INT32_MAX};
        for(unsigned j=0;j<sizeof samples/sizeof *samples;j++) {kind=0xdead;CHECK(!bw_texture_depth_storage_kind(formats[i],0,samples[j],&kind)&&kind==0xdead);}
    }
    const unsigned noCompression[]={1,2,3,4,5,6,7,8,9,13,14,15,16,57,58,59,60,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,121,122,123,124,125,126,127,128,129,130,131,132,133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152};
    for(unsigned format=0;format<0x100;format++) {
        unsigned known=0;for(unsigned i=0;i<sizeof noCompression/sizeof *noCompression;i++)known|=format==noCompression[i];
        CHECK(bw_texture_color_disables_compression(format)==(int)known);
        CHECK(bw_texture_color_compression_layout(format,8,0)==(int)known);
        CHECK(bw_texture_color_compression_layout(format,13,0)==(int)known);
        CHECK(!bw_texture_color_compression_layout(format,8,1));CHECK(!bw_texture_color_compression_layout(format,0xa,0));
    }
    unsigned colorCount=0;for(unsigned format=0;format<0x200;format++){
        int valid=(format>=1&&format<=0x18)||(format>=0x22&&format<=0x30)||(format>=0x37&&format<=0x50)||(format>=0x6d&&format<=0x9c);
        CHECK(bw_texture_ordinary_color_format(format)==valid);colorCount+=valid;
        CHECK(bw_texture_plain_color_selected(BW_TEXTURE_KIND_METAL_PLAIN_COLOR,format)==valid);
        CHECK(bw_texture_plain_color_selected(BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH,format)==valid);
        CHECK(!bw_texture_plain_color_selected(BW_TEXTURE_KIND_UNKNOWN,format));CHECK(!bw_texture_plain_color_selected((BWTextureKindPolicy)99,format));
        CHECK(bw_texture_plain_color_layout(BW_TEXTURE_KIND_METAL_PLAIN_COLOR,format,8,0)==valid);
        CHECK(bw_texture_plain_color_layout(BW_TEXTURE_KIND_METAL_PLAIN_COLOR,format,13,0)==valid);
        CHECK(!bw_texture_plain_color_layout(BW_TEXTURE_KIND_METAL_PLAIN_COLOR,format,8,1));CHECK(!bw_texture_plain_color_layout(BW_TEXTURE_KIND_METAL_PLAIN_COLOR,format,0xa,0));
    }CHECK(colorCount==113);
    CHECK(!bw_texture_color_disables_compression(UINT32_MAX));
    unsigned kind=0xdead;CHECK(!bw_texture_depth_storage_kind(0x31,0,0,&kind)&&kind==0xdead);
    CHECK(!bw_texture_depth_storage_kind(0xffffffff,0,0,&kind));CHECK(!bw_texture_depth_storage_kind(0x32,0x50,0,&kind));CHECK(!bw_texture_depth_storage_kind(0x32,0,0,0));
    size_t value=17;CHECK(bw_texture_storage_round_size(512,65536,&value)&&value==65536);
    CHECK(bw_texture_storage_round_size(262144,65536,&value)&&value==262144);
    CHECK(bw_texture_storage_round_size(SIZE_MAX-65535,65536,&value)&&value==SIZE_MAX-65535);
    value=17;CHECK(!bw_texture_storage_round_size(SIZE_MAX-65534,65536,&value)&&value==17);
    CHECK(!bw_texture_storage_round_size(1,0,&value)&&value==17);CHECK(!bw_texture_storage_round_size(1,3,&value)&&value==17);
    CHECK(bw_texture_storage_size32(UINT64_C(0x7fffffff))==INT32_MAX);
    CHECK(bw_texture_storage_size32(UINT64_C(0x80000000))==INT32_MIN);
    CHECK(bw_texture_storage_size32(UINT64_C(0xffffffffffffffff))==-1);
    CHECK(bw_texture_storage_size32(UINT64_C(0x100000000))==0);
    CHECK(bw_texture_storage_size32(UINT64_C(0x180000001))==INT32_MIN+1);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='botw-depth-policy-') as directory:
            path = Path(directory)
            (path / 'test.c').write_text(source)
            built = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                    '-fsanitize=address,undefined', '-I', str(ROOT / 'native/botw_graphics'),
                                    str(path / 'test.c'), '-o', str(path / 'test')],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(path / 'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'Darwin Objective-C CPU harness required; no Metal device is created')
class TextureLifecycleContextCPUTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-texture-lifecycle-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'lifecycle.m'
        graphics = ROOT / 'native/botw_graphics'
        source.write_text(r"""
#import <Metal/Metal.h>
@interface LifecycleThrowReferences:NSObject @end
@implementation LifecycleThrowReferences
- (void)addObject:(id)object{(void)object;@throw [NSException exceptionWithName:@"LifecycleReferenceFailure" reason:@"owned reference fixture" userInfo:nil];}
@end
@interface LifecycleCPUDevice:NSObject @end
@implementation LifecycleCPUDevice
- (id<MTLCommandQueue>)newCommandQueue{return (id<MTLCommandQueue>)[NSObject new];}
@end
static id<MTLDevice> lifecycleCPUDevice(void){return (id<MTLDevice>)[LifecycleCPUDevice new];}
#define MTLCreateSystemDefaultDevice lifecycleCPUDevice
#include "graphics.m"
#undef MTLCreateSystemDefaultDevice
#include <setjmp.h>
static sigjmp_buf recovery;
static unsigned notifications,accessCalls;
static BOOL recoverNow;
static const void *deniedAddress;
static void captureError(const char *error){(void)error;notifications++;if(recoverNow)siglongjmp(recovery,1);}
static int accessMetadata(const void *address,size_t bytes,int write){(void)bytes;(void)write;accessCalls++;return address!=deniedAddress;}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"LIFECYCLE_CPU_CHECK line%d %s\n",__LINE__,#x);return 91;}}while(0)
static NVNdevice rawDevice;
static NVNmemoryPool rawPool;
static int beginHost(void){
 CHECK(botw_graphics_init(NULL,captureError,NULL));CHECK([(id)gpu isKindOfClass:[LifecycleCPUDevice class]]);
 BWDevice *device=[[BWDevice alloc]initWithTextureKindPolicy:BW_TEXTURE_KIND_METAL_PLAIN_COLOR_DEPTH];put(&rawDevice,device);
 BWPool *pool=[BWPool new];pool.kind=BW_POOL_VIRTUAL;pool.device=device;pool.live=YES;pool.size=65536;pool.flags=0x221;put(&rawPool,pool);pool.generation=((BWObject*)&rawPool)->serial;
 return 0;
}
static NVNtextureBuilder ownedBuilder(void){NVNtextureBuilder raw={0};TextureBuilder *b=(TextureBuilder*)&raw;b->device=&rawDevice;b->pool=&rawPool;b->target=NVN_TEXTURE_TARGET_2D;b->format=NVN_FORMAT_RGBA8;b->width=b->height=b->depth=b->levels=1;for(unsigned i=0;i<4;i++)b->swizzle[i]=i+NVN_SWZ_R;return raw;}
static int badWrite(NVNtexture *raw){
 unsigned before=notifications;recoverNow=YES;if(!sigsetjmp(recovery,1)){w_nvnTextureWriteTexels(raw,NULL,NULL,NULL);CHECK(0);}recoverNow=NO;
 CHECK(notifications==before+1&&strstr(lastError,"entry=nvnTextureWriteTexels: Unsupported or invalid NVN operation: object lifetime/type expected=BWTexture"));
 CHECK(!graphicsDepth&&!pendingError&&!pendingFatal);CHECK(pthread_mutex_trylock(&graphicsLock)==0);pthread_mutex_unlock(&graphicsLock);return 0;
}
int main(int argc,char **argv){@autoreleasepool{
 CHECK(argc==2);CHECK(!beginHost());const char *mode=argv[1];NVNtexture raw={0},other={0};NVNtextureBuilder builder=ownedBuilder();
 memoryAccessValidator=accessMetadata;
 if(!strcmp(mode,"none")){CHECK(!badWrite(&raw));CHECK(accessCalls==1);}
 else if(!strcmp(mode,"false")){
  TextureBuilder *b=(TextureBuilder*)&builder;b->flags=8;b->format=0;CHECK(!w_nvnTextureInitialize(&raw,&builder));CHECK(notifications==1&&((BWObject*)&raw)->serial==0);CHECK(strstr(lastError,"Texture hardware compression/layout request is not implemented for this format/flags/samples target=1 flags=0x8 format=0x0 samples=0"));unsigned before=accessCalls;CHECK(!badWrite(&raw)&&accessCalls==before+1);CHECK(notifications==2);
 }else if(!strcmp(mode,"success")){CHECK(w_nvnTextureInitialize(&raw,&builder));CHECK(!notifications&&((BWObject*)&raw)->serial!=0);}
 else if(!strcmp(mode,"post-publish-exception")){
  textureReferences=(NSHashTable*)[LifecycleThrowReferences new];recoverNow=YES;if(!sigsetjmp(recovery,1)){w_nvnTextureInitialize(&raw,&builder);CHECK(0);}recoverNow=NO;CHECK(notifications==1&&((BWObject*)&raw)->serial!=0);CHECK(strstr(lastError,"Native graphics exception: owned reference fixture"));CHECK(!badWrite(&other)&&notifications==2);
 }else if(!strcmp(mode,"same-zero")||!strcmp(mode,"different")){
  CHECK(w_nvnTextureInitialize(&raw,&builder));CHECK(!notifications);printf("PUBLISHED_RAW=%p PUBLISHED_SERIAL=0x%llx OTHER_RAW=%p\n",&raw,(unsigned long long)((BWObject*)&raw)->serial,&other);
  if(!strcmp(mode,"same-zero")){memset(&raw,0,sizeof raw);CHECK(!badWrite(&raw));}else CHECK(!badWrite(&other));
 }else if(!strcmp(mode,"started")||!strcmp(mode,"invalid-builder")){
  if(!strcmp(mode,"started")){((TextureBuilder*)&builder)->device=(NVNdevice*)(uintptr_t)0xdeadbeef000;deniedAddress=((TextureBuilder*)&builder)->device;}else deniedAddress=&builder;
  recoverNow=YES;if(!sigsetjmp(recovery,1)){w_nvnTextureInitialize(&raw,&builder);CHECK(0);}recoverNow=NO;CHECK(notifications==1&&((BWObject*)&raw)->serial==0);
  if(!strcmp(mode,"invalid-builder"))CHECK(accessCalls==1);unsigned before=accessCalls;deniedAddress=NULL;CHECK(!badWrite(&raw)&&accessCalls==before+1);CHECK(notifications==2);
 }else if(!strcmp(mode,"unreadable-object")){
  size_t page=getpagesize();void *inaccessible=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(inaccessible!=MAP_FAILED);deniedAddress=inaccessible;CHECK(!badWrite(inaccessible));CHECK(accessCalls==1&&strstr(lastError,"serial=<unreadable>"));deniedAddress=NULL;CHECK(!munmap(inaccessible,page));
 }else if(!strcmp(mode,"wrap-cap")){
  NVNtexture textures[6]={0};for(unsigned i=0;i<6;i++)CHECK(w_nvnTextureInitialize(&textures[i],&builder));CHECK(!notifications);for(unsigned i=0;i<5;i++)CHECK(!badWrite(&raw));CHECK(notifications==5);
 }else if(!strcmp(mode,"reset")){
  CHECK(w_nvnTextureInitialize(&raw,&builder));CHECK(!botw_graphics_init(NULL,captureError,NULL));CHECK(notifications==1);CHECK(!badWrite(&other));
  BWPool *quarantined=[BWPool new];quarantined.kind=BW_POOL_ORDINARY;quarantined.retiring=YES;dispatch_group_t group=dispatch_group_create();dispatch_group_enter(group);objc_setAssociatedObject(quarantined,&poolBorrowedLifetimeKey,group,OBJC_ASSOCIATION_RETAIN_NONATOMIC);poolUnpublishedPools=[NSMutableArray arrayWithObject:quarantined];
  unsigned before=notifications;char error[256];CHECK(!botw_graphics_shutdown_checked(error,sizeof error));CHECK(strstr(error,"borrowed Metal backing still retained")&&notifications==before);CHECK(!badWrite(&other));dispatch_group_leave(group);CHECK(botw_graphics_shutdown_checked(error,sizeof error));CHECK(!beginHost());CHECK(!badWrite(&other));
 }else if(!strcmp(mode,"saturated")){
#ifdef BW_TEXTURE_LIFECYCLE_CAPACITY
  textureInitContext.sequence=UINT64_MAX-1;textureInitContext.entryCalls=textureInitContext.validatedAttempts=textureInitContext.publishedSuccesses=UINT64_MAX-1;
  CHECK(w_nvnTextureInitialize(&raw,&builder));uint64_t first=((BWObject*)&raw)->serial;CHECK(w_nvnTextureInitialize(&other,&builder));CHECK(textureInitContext.count==1&&textureInitContext.saturated&&textureInitContext.records[0].serial==first);NVNtexture missing={0};CHECK(!badWrite(&missing));
#else
  CHECK(!badWrite(&raw));
#endif
 }else CHECK(0);
 CHECK(botw_graphics_shutdown_checked(NULL,0));CHECK(!gpu&&!commandQueue);printf("TEXTURE_LIFECYCLE_CPU_OK mode=%s callbacks=%u no_real_gpu=true\n",mode,notifications);return 0;
}}
""")
        cls.executable = folder / 'lifecycle'
        build = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc',
            '-fobjc-arc-exceptions', '-fblocks', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-O1', '-g', '-fsanitize=address,undefined',
            '-I', str(graphics), str(source), '-framework', 'Foundation',
            '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=120)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def mode(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('TEXTURE_LIFECYCLE_CPU_OK', result.stdout)
        self.assertNotIn('runtime error:', result.stderr)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        return [s for s in result.stderr.splitlines()
                if s.startswith('[botw graphics] texture_lifecycle_context ')], result

    def test_no_init_entry_has_no_retained_history_and_preserves_one_callback(self):
        lines, _ = self.mode('none')
        self.assertEqual(len(lines), 1)
        self.assertIn('bridge_init_entry_calls=0 validated_init_attempts=0 published_successes=0', lines[0])
        self.assertIn('match=not_retained', lines[0])

    def test_false_and_interrupted_attempts_have_distinct_states_without_extra_reads(self):
        for mode, state, validated in [('false', 'returned_false', 1), ('started', 'started', 1),
                                       ('invalid-builder', None, 0)]:
            with self.subTest(mode=mode):
                lines, _ = self.mode(mode)
                self.assertEqual(len(lines), 1)
                self.assertIn(f'bridge_init_entry_calls=1 validated_init_attempts={validated} published_successes=0', lines[0])
                if state:
                    self.assertIn('result=' + state, lines[0])
                else:
                    self.assertIn('recent_count=0', lines[0])

    def test_success_only_records_owned_metadata_without_new_output(self):
        lines, result = self.mode('success')
        self.assertEqual(lines, [])
        self.assertEqual(result.stderr, '')

    def test_published_same_zeroed_raw_and_different_raw_are_not_conflated(self):
        for mode, match in [('same-zero', 'retained'), ('different', 'not_retained'), ('post-publish-exception', 'not_retained')]:
            with self.subTest(mode=mode):
                lines, _ = self.mode(mode)
                self.assertEqual(len(lines), 1)
                self.assertIn('bridge_init_entry_calls=1 validated_init_attempts=1 published_successes=1', lines[0])
                self.assertIn('match=' + match, lines[0])
                self.assertIn('result=published', lines[0])
                self.assertIn('observed_serial=0x0', lines[0])

    def test_unreadable_header_uses_original_get_readability_without_retry(self):
        lines, _ = self.mode('unreadable-object')
        self.assertEqual(len(lines), 1)
        self.assertIn('observed_serial=unreadable', lines[0])
        self.assertIn('recent_count=0', lines[0])

    def test_four_recent_records_and_four_failure_dumps_are_bounded(self):
        lines, _ = self.mode('wrap-cap')
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertIn('bridge_init_entry_calls=6 validated_init_attempts=6 published_successes=6', line)
            self.assertIn('recent_count=4', line)
            self.assertEqual(line.count('result=published'), 4)

    def test_failed_init_and_shutdown_preserve_history_then_success_resets(self):
        lines, _ = self.mode('reset')
        self.assertEqual(len(lines), 3)
        for line in lines[:2]:
            self.assertIn('bridge_init_entry_calls=1 validated_init_attempts=1 published_successes=1', line)
        self.assertIn('bridge_init_entry_calls=0 validated_init_attempts=0 published_successes=0', lines[-1])

    def test_saturated_sequence_stops_recording_instead_of_reusing_tokens(self):
        lines, _ = self.mode('saturated')
        self.assertEqual(len(lines), 1)
        self.assertIn('bridge_init_entry_calls=18446744073709551615 validated_init_attempts=18446744073709551615 published_successes=18446744073709551615', lines[0])
        self.assertIn('history_saturated=true', lines[0])
        self.assertIn('recent_count=1', lines[0])


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal host objects')
class StorageClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.directory.name) / 'storage-class'
        graphics = ROOT / 'native/botw_graphics'
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
            '-Wall', '-Wextra', '-Werror', '-I', str(graphics), str(graphics / 'graphics.m'),
            str(graphics / 'tools/probe_storage_class.c'), '-framework', 'Foundation',
            '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(cls.executable)],
            capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def probe(self, mode, expected):
        run = subprocess.run([str(self.executable), mode], capture_output=True, text=True, timeout=30)
        if run.returncode == 77:
            self.skipTest('Metal unavailable')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(expected, run.stdout)
        self.assertNotIn('failed assertion', run.stderr)

    def test_raw46_depth_creation_remains_refused_with_exact_normal_builder_error_context(self):
        self.probe('raw46-create-context', 'RAW46_CREATION_STILL_REFUSED_WITH_NORMAL_BUILDER_SCALARS_OK')

    def test_plain_depth_initialize_checks_live_registry_before_untrusted_provider_header(self):
        for state in ('foreign', 'inaccessible', 'copy'):
            with self.subTest(state=state):
                self.probe('depth-init-provider-' + state, 'METAL_PLAIN_COLOR_INIT_REGISTRY_BEFORE_HEADER_OK')

    def test_plain_depth_policy_unifies_five_formats_but_preserves_flags_gl_and_size_scope(self):
        for mode in ('plain-depth-query', 'plain-depth-readonly'):
            with self.subTest(mode=mode):
                self.probe(mode, 'PLAIN_DEPTH_FIVE_METADATA_FLAGS_GL_AND_SIZE_SCOPE_OK')

    def test_backend_plain_color_policy_unifies_all_113_queries_without_changing_flags(self):
        for mode in ('policy-class', 'policy-align', 'policy-size', 'policy-readonly'):
            with self.subTest(mode=mode):
                self.probe(mode, f'METAL_PLAIN_COLOR_POLICY_{mode}_OK')

    def test_backend_plain_color_preserves_size_domain_gl_and_exact_live_provider(self):
        for mode in ('policy-domain', 'policy-gl', 'policy-live-class', 'policy-live-align', 'policy-live-size'):
            with self.subTest(mode=mode):
                self.probe(mode, f'METAL_PLAIN_COLOR_POLICY_{mode}_OK')

    def test_plain_color_initialize_checks_registry_before_any_untrusted_provider_header_read(self):
        for state in ('foreign', 'inaccessible', 'copy'):
            with self.subTest(state=state):
                self.probe('policy-init-provider-' + state, 'METAL_PLAIN_COLOR_INIT_REGISTRY_BEFORE_HEADER_OK')

    def test_plain_color_ordinary_snapshot_and_metal_format_boundary(self):
        self.probe('policy-create', 'METAL_PLAIN_COLOR_ORDINARY_SNAPSHOT_AND_UNSUPPORTED_FORMAT_OK')

    def test_plain_color_uses_exact_live_registry_provider(self):
        for state in ('null', 'garbage', 'finalized', 'foreign', 'inaccessible', 'copy'):
            with self.subTest(state=state):
                self.probe('backend-invalid-device-' + state, 'STORAGE_CLASS_GENERIC_LIVE_DEVICE_REQUIRED_OK')

    def test_compressible_request_preserves_flags_for_all_65_original_ineligible_colors(self):
        for mode in ('compression-query', 'compression-query-readonly'):
            with self.subTest(mode=mode):
                self.probe(mode, 'COMPRESSIBLE_COLOR_METADATA65_IMMUTABLE_OK cases=260')

    def test_new_compressible_size_scope_does_not_expand_other_metadata_or_msaa(self):
        self.probe('compression-size-domain', 'COMPRESSIBLE_COLOR_SIZE_SCOPE_IS_NOT_CLASS_SCOPE_OK')

    def test_new_compressible_color_size_keeps_original_gl_early_zero(self):
        self.probe('compression-gl-size', 'COMPRESSIBLE_COLOR_GL_EARLY_ZERO_OPAQUE_POINTER_OK')

    def test_new_compressible_color_alignment_and_size_require_live_provider(self):
        for mode in ('compression-live-align', 'compression-live-size'):
            with self.subTest(mode=mode):
                self.probe(mode, 'COMPRESSIBLE_COLOR_LIVE_QUERY_REQUIRED_OK')

    def test_compressible_color_class_uses_exact_live_provider_not_copied_or_stale_headers(self):
        for state in ('null', 'garbage', 'finalized', 'foreign', 'inaccessible', 'copy'):
            with self.subTest(state=state):
                self.probe('compression-invalid-device-' + state, 'STORAGE_CLASS_GENERIC_LIVE_DEVICE_REQUIRED_OK')

    def test_unknown_color_and_combined_depth_compression_cannot_create_ordinary_texture(self):
        self.probe('compression-unknown-create', 'COMPRESSIBLE_UNKNOWN_CREATION_REJECTED_BEFORE_PUBLICATION_OK')

    def test_builder_pitch_class_is_pure_metadata_independent_of_gpu_creation(self):
        for mode in ('builder', 'builder-readonly'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_PITCH_METADATA_IMMUTABLE_OK cases=110')

    def test_original_single_sample_depth_class_alignment_and_size_are_immutable(self):
        for mode in ('depth-query', 'depth-query-readonly'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_DEPTH_QUERY_READONLY_IMMUTABLE_OK cases=150')

    def test_depth_alignment_is_independently_derived_from_native_mapping_page(self):
        self.probe('depth-alignment-only', 'STORAGE_DEPTH_QUERY_READONLY_IMMUTABLE_OK cases=150')

    def test_depth_size_is_independently_rounded_to_native_mapping_page(self):
        self.probe('depth-size-only', 'STORAGE_DEPTH_QUERY_READONLY_IMMUTABLE_OK cases=150')

    def test_depth_geometry_rounding_and_wide_builder_size_keep_32bit_getter_distinct(self):
        self.probe('depth-size-cases', 'STORAGE_DEPTH_SIZE_GEOMETRY_AND_WIDE_BUILDER_OK')

    def test_original_gl_size_short_circuit_and_opaque_packaged_pointer_do_not_enable_import(self):
        self.probe('depth-import-metadata', 'STORAGE_DEPTH_GL_SIZE_ZERO_AND_OPAQUE_PACKAGED_METADATA_OK')

    def test_depth_texture_size_and_kind_are_immutable_snapshots_with_live_generation(self):
        for mode in ('depth-snapshot', 'depth-stale'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_DEPTH_SNAPSHOT_AND_LIFETIME_OK')

    def test_fastclear_and_combined_metadata_do_not_enable_gpu_creation(self):
        for mode in ('depth-fastclear-create', 'depth-combined-create'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_DEPTH_METADATA_IS_NOT_GPU_CREATION_OK')

    def test_small_old_capacity_and_unaligned_offset_reject_before_texture_publication(self):
        self.probe('depth-storage-bounds', 'STORAGE_DEPTH_BOUNDS_AND_OFFSET_REFUSE_BEFORE_OBJECT_PUBLICATION_OK')

    def test_depth_queries_require_exact_live_provider(self):
        for state in ('null', 'garbage', 'finalized', 'foreign', 'inaccessible', 'copy'):
            with self.subTest(state=state):
                self.probe('depth-invalid-device-' + state, 'STORAGE_CLASS_GENERIC_LIVE_DEVICE_REQUIRED_OK')

    def test_initialized_texture_keeps_immutable_pitch_class(self):
        for mode in ('texture-live', 'texture-linear', 'texture-both-flags'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_TEXTURE_SNAPSHOT_OK')

    def test_all_original_valid_colors_have_immutable_generic_metadata(self):
        for mode in ('builder-generic', 'builder-generic-readonly'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_GENERIC_METADATA_IMMUTABLE_OK formats=113 cases=10170')

    def test_initialized_private_texture_keeps_immutable_generic_class(self):
        self.probe('texture-private', 'STORAGE_CLASS_TEXTURE_SNAPSHOT_OK')

    def test_generic_builder_requires_live_device_without_requiring_a_pool(self):
        for state in ('null', 'garbage', 'finalized', 'foreign', 'inaccessible', 'copy'):
            with self.subTest(state=state):
                self.probe('builder-invalid-device-' + state, 'STORAGE_CLASS_GENERIC_LIVE_DEVICE_REQUIRED_OK')

    def test_depth_msaa_stencil_and_unknown_formats_require_native_kind(self):
        for mode in ('builder-block', 'builder-rejected-formats'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_NATIVE_KIND_REQUIRED_REJECTED_OK')

    def test_buffer_metadata_does_not_claim_gpu_creation(self):
        self.probe('buffer-create', 'STORAGE_CLASS_BUFFER_METADATA_IS_NOT_GPU_CREATION_OK')

    def test_generic_metadata_does_not_claim_msaa_format_target_or_sparse_gpu_creation(self):
        for mode in ('generic-msaa-create', 'generic-format-create', 'generic-target-create', 'generic-flags-create'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_GENERIC_METADATA_IS_NOT_GPU_CREATION_OK')

    def test_texture_generation_device_and_finalize_are_checked(self):
        for mode, marker in [('texture-stale', 'STORAGE_CLASS_STALE_GENERATION_REJECTED_OK'),
                             ('device-stale', 'STORAGE_CLASS_STALE_DEVICE_REJECTED_OK'),
                             ('texture-finalized', 'STORAGE_CLASS_FINALIZED_OBJECT_REJECTED_OK'),
                             ('generic-stale', 'STORAGE_CLASS_STALE_GENERATION_REJECTED_OK'),
                             ('generic-device-stale', 'STORAGE_CLASS_STALE_DEVICE_REJECTED_OK'),
                             ('generic-finalized', 'STORAGE_CLASS_FINALIZED_OBJECT_REJECTED_OK')]:
            with self.subTest(mode=mode):
                self.probe(mode, marker)

    def test_mapping_requires_a_live_pool_with_typed_abi(self):
        self.probe('invalid-map-pool', 'STORAGE_CLASS_MAP_INVALID_POOL_REJECTED_OK')

    def test_unknown_raw_storage_class_families_still_reject(self):
        for mode in ('unsupported-raw-builder', 'unsupported-raw-texture'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_OTHER_FAMILIES_REMAIN_UNSUPPORTED_OK')
