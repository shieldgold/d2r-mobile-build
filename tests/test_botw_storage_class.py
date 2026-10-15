"""Base-v0 pitch/generic metadata and explicit native-kind boundaries."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal host objects')
class StorageClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.directory.name) / 'storage-class'
        graphics = ROOT / 'native/botw_graphics'
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks',
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

    def test_compression_depth_and_unknown_formats_require_native_kind(self):
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

    def test_raw_and_virtual_mapping_families_still_reject(self):
        for mode in ('unsupported-raw-builder', 'unsupported-raw-texture', 'unsupported-map'):
            with self.subTest(mode=mode):
                self.probe(mode, 'STORAGE_CLASS_OTHER_FAMILIES_REMAIN_UNSUPPORTED_OK')
