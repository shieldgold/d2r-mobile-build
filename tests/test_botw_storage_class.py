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
        const int samples[]={-1,1,2,4,8,16,INT32_MAX};
        for(unsigned j=0;j<sizeof samples/sizeof *samples;j++) {kind=0xdead;CHECK(!bw_texture_depth_storage_kind(formats[i],0,samples[j],&kind)&&kind==0xdead);}
    }
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

    def test_compression_msaa_stencil_and_unknown_formats_require_native_kind(self):
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
