"""Fixed base graphics bank2 bytes, using the actual replay helper on owned state."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_IMAGE_BANK_BASELINE') == '1'
OLD_LOOP = 'for (int r = 0; r < 32; r++) { uint32_t th = (uint32_t)S.tex[nvn_stage][r]; memcpy(d + 0x20 + 8 * r, &th, 4); }'
OLD_IMAGE_COMMENT = '// Image (writable texture): handle = texture pool index (GetImageHandle returns it as-is; the game does not call RegisterImage, images and textures share registration)'
NEW_IMAGE_COMMENT = '// Image handles retain the base-v0 marker; the low texture-pool index shares normal image/texture registration.'


def function(source, signature):
    start = source.index(signature)
    left = source.index('{', start)
    depth, end = 1, left + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class WholeImageBank(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-owned-image-bank-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        path = Path('dm/gfx/nvn/nvn_mtl.m')
        original = (EXP / 'upstream' / path).read_text()
        target = cls.work / path
        target.parent.mkdir(parents=True)
        target.write_text(original)
        hle_path = Path('dm/gfx/nvn/hle_nvn.c')
        original_hle = (EXP / 'upstream' / hle_path).read_text()
        (cls.work / hle_path).write_text(original_hle)
        if not BASELINE:
            run = subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(EXP / 'patches/base-v0-image-bank.patch')],
                                 cwd=cls.work, capture_output=True, text=True)
            if run.returncode:
                raise AssertionError(run.stdout + run.stderr)
        actual = target.read_text()
        actual_hle = (cls.work / hle_path).read_text()
        signature = 'NVN(nvnDeviceGetImageHandle,'
        getter = function(actual_hle, signature)
        restored_hle = actual_hle.replace(getter, function(original_hle, signature), 1)
        restored_hle = restored_hle.replace(NEW_IMAGE_COMMENT, OLD_IMAGE_COMMENT, 1)
        if restored_hle != original_hle:
            raise AssertionError('image patch changed another normal NVN interface')
        (cls.work / 'actual_get_image_handle.inc').write_text(getter + '\n')
        arrays = re.findall(r'^    uint64_t tex\[6\]\[128\], samp\[6\]\[128\], img\[6\]\[32\];$', original, re.M)
        if len(arrays) != 1:
            raise AssertionError('original replay handle-array declaration changed')
        (cls.work / 'actual_handle_arrays.inc').write_text(arrays[0] + '\n')
        if BASELINE:
            if actual.count(OLD_LOOP) != 1:
                raise AssertionError('original bank2 fill block changed')
            helper = 'static void fill_driver_bank2(uint8_t *d, int nvn_stage) {\n    ' + OLD_LOOP + '\n}'
        else:
            helper = function(actual, 'static void fill_driver_bank2(')
            # Every other original byte, including bank0, lock scope, zero-fill,
            # and resource/encoder handling, must remain unchanged.
            restored = actual.replace(helper + '\n', '', 1)
            restored = restored.replace('fill_driver_bank2(d, nvn_stage);', OLD_LOOP, 1)
            if restored != original:
                raise AssertionError('image-bank patch changed code outside its isolated helper and call')
            encode = function(actual, 'static int encode_args(')
            if not (encode.index('pthread_mutex_lock(&g_rmu)') < encode.index('fill_driver_bank2(d, nvn_stage)')
                    < encode.index('pthread_mutex_unlock(&g_rmu)')):
                raise AssertionError('real bank2 helper is no longer under the original resource lock')
        (cls.work / 'actual_fill_driver_bank2.inc').write_text(helper + '\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-I', str(cls.work),
                   '-I', str(EXP / 'upstream/dm/gfx/nvn/include'),
                   str(EXP / 'tests/whole_image_bank_probe.c'), '-o', str(cls.binary)]
        run = subprocess.run(command, capture_output=True, text=True)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)

    def case(self, stage):
        run = subprocess.run([str(self.binary), str(stage)], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_vertex_full_64_bit_image8_and_original_texture32_bytes(self): self.case(0)
    def test_fragment_full_64_bit_image8_and_original_texture32_bytes(self): self.case(1)
    def test_stage2_keeps_original_zero_image_bank(self): self.case(2)
    def test_stage3_keeps_original_zero_image_bank(self): self.case(3)
    def test_stage4_keeps_original_zero_image_bank(self): self.case(4)
    def test_compute_keeps_original_zero_image_bank(self): self.case(5)
    def test_image_handle_zero_preserves_base_marker(self): self.case('handle-0')
    def test_image_handle_positive_preserves_base_marker(self): self.case('handle-1')
    def test_image_handle_maximum_positive_int32(self): self.case('handle-2')
    def test_image_handle_minimum_negative_int32(self): self.case('handle-3')
    def test_image_handle_all_32_bits_set(self): self.case('handle-4')
    def test_image_handle_negative_sign_extension(self): self.case('handle-5')

    @unittest.skipIf(BASELINE, 'baseline has no fixed-base graphics contract')
    def test_static_profile_has_exact_graphics_limits_and_no_shader_facts(self):
        profile = json.loads((EXP / 'adapter/base_v0_graphics_binding.json').read_text())
        self.assertEqual(profile['schema'], 'botw-base-v0-graphics-binding-v1')
        self.assertEqual(profile['stages'], [0, 1])
        self.assertEqual(profile['uniform_buffers'], {'count': 14, 'nvn_first': 0, 'hardware_first': 3})
        self.assertEqual(profile['textures'], {'count': 32, 'bank': 2, 'offset': 0x20, 'stride': 8, 'payload_bytes': 4})
        self.assertEqual(profile['images'], {'count': 8, 'bank': 2, 'offset': 0x120, 'stride': 8, 'payload_bytes': 8})
        self.assertEqual(profile['unsupported'], ['compute', 'storage_buffers'])
        self.assertFalse(profile['shader_facts'])
        self.assertEqual(profile['source']['nso_sha256'], '58b6791aff6e5cb509c5347c1dc41d01a32c0a298c7f5da0c88ea56fc8a6eb17')

    @unittest.skipIf(BASELINE, 'baseline has no fixed-base graphics contract')
    def test_exact_fixed_nso_and_bounded_function_hashes(self):
        profile = json.loads((EXP / 'adapter/base_v0_graphics_binding.json').read_text())
        nso = ROOT / 'outputs/botw-native-base/ExeFS/subsdk0'
        if not nso.exists():
            self.skipTest('private fixed NSO unavailable; owned byte tests still run')
        from scripts.botw_native.nso import initialized_image, inspect_nso
        data = nso.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), profile['source']['nso_sha256'])
        self.assertEqual(inspect_nso(data, 'subsdk0')['build_id'], profile['source']['build_id'])
        image = initialized_image(data, 'subsdk0')
        self.assertEqual(hashlib.sha256(image).hexdigest(), profile['source']['initialized_image_sha256'])
        for name, item in profile['functions'].items():
            self.assertEqual(hashlib.sha256(image[item['offset']:item['offset'] + item['bytes']]).hexdigest(), item['sha256'], name)


if __name__ == '__main__':
    unittest.main()
