"""Actual original VI owner and presentation math; self-owned CPU only."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import json

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_VI_LAYER_BASELINE') == '1'
PATCH = 'patches/base-v0-vi-layer.patch'


def body(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    while 0 <= source.find(';', start, opening) < opening:
        start = source.index(signature, start + len(signature))
        opening = source.index('{', start)
    depth = 1
    cursor = opening + 1
    while depth:
        depth += (source[cursor] == '{') - (source[cursor] == '}')
        cursor += 1
    return source[start:cursor]


@unittest.skipUnless(sys.platform == 'darwin', 'actual Darwin VI registry and public Metal viewport type')
class WholeViLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-vi-layer-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.source = cls.work / 'source'
        shutil.copytree(EXP / 'upstream', cls.source)
        patches = list(json.loads((EXP / 'SOURCE.lock.json').read_text())['patches'])
        for precursor in ('patches/base-v0-nvn-version.patch', 'patches/graphics-bootstrap-status.patch'):
            if precursor not in patches:
                patches.append(precursor)
        patches = [p for p in patches if p != PATCH]
        if not BASELINE:
            patches.append(PATCH)
        for patch in patches:
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(EXP / patch)],
                                    cwd=cls.source, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        cls.mtl = (cls.source / 'dm/gfx/nvn/nvn_mtl.m').read_text()
        if BASELINE:
            # Wrap the original, unchanged viewport statements from real
            # present_encode. No mirrored math or absent-symbol RED.
            encode = body(cls.mtl, 'static int present_encode(')
            start = encode.index('    double dw = dst.width, dh = dst.height, s = ')
            stop = encode.index('    int full = ', start)
            leaf = ('static MTLViewport owned_viewport(double cw, double ch, double width, double height) {\n'
                    '    struct { double width, height; } dst = {width, height};\n' + encode[start:stop] +
                    '    return vp;\n}\n')
        else:
            leaf = '#include "whole_vi_layer.h"\n' + body(cls.mtl, 'static MTLViewport vi_layer_viewport(')
            leaf += '\n#define owned_viewport vi_layer_viewport\n'
        (cls.work / 'actual_vi_viewport.inc').write_text(leaf)
        ignore = cls.work / 'metadata-only.asan.ignore'
        ignore.write_text('global:hle_force_*\n')
        cls.binary = cls.work / 'probe'
        command = ['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                   '-Wno-format', '-pthread', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-fsanitize-ignorelist=' + str(ignore), '-I', str(cls.source), '-I', str(EXP / 'adapter'),
                   '-I', str(cls.work), str(EXP / 'tests/whole_vi_layer_probe.m'), '-framework', 'Foundation',
                   '-o', str(cls.binary)]
        if BASELINE:
            command.append('-DBOTW_VI_LAYER_BASELINE=1')
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=15,
            env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
                 'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=0'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')

    def test_public_scaling_api_is_in_actual_same_owner_registry(self): self.case('registry')
    def test_unselected_mode_preserves_original_aspect_math(self): self.case('default')
    def test_mode2_reaches_full_canvas_from_actual_viewport_leaf(self): self.case('fit')
    def test_mode4_keeps_original_centering_and_one_percent_tolerance(self): self.case('preserve')
    def test_unknown_modes_and_fake_null_tokens_preserve_owner_state(self): self.case('invalid')
    def test_concurrent_mode_changes_produce_one_complete_viewport_snapshot(self): self.case('concurrent')

    @unittest.skipIf(BASELINE, 'original source has separate unconditional aspect math')
    def test_both_actual_metal_present_paths_consume_the_same_owner_leaf(self):
        for signature in ('static int present_encode(', 'static int present_encode_m4('):
            actual = body(self.mtl, signature)
            self.assertEqual(actual.count('vi_layer_viewport(cw, ch, dw, dh)'), 1)
            self.assertNotIn('fmin(', actual)
        leaf = body(self.mtl, 'static MTLViewport vi_layer_viewport(')
        self.assertEqual(leaf.count('botw_vi_layer_scaling_mode()'), 1)

    @unittest.skipIf(BASELINE, 'static contract belongs to the new native VI selection')
    def test_complete_fixed_vi_import_inventory_and_exact_bounded_contract(self):
        contract = json.loads((EXP / 'adapter/base_v0_vi_layer.json').read_text())
        directory = ROOT / 'outputs/botw-native-base/ExeFS'
        if not all((directory / name).is_file() for name in contract['all_seven_module_sha256']):
            self.skipTest('private fixed base NSOs unavailable; owned contracts still run')
        sys.path.insert(0, str(ROOT / 'scripts'))
        from botw_native.nso import initialized_image, inspect_nso
        imported = set()
        for name, expected in contract['all_seven_module_sha256'].items():
            raw = (directory / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
            metadata = inspect_nso(raw, name)
            imported.update(n for n in metadata['imports'] if n.startswith(('_ZN2nn2vi', '_ZNK2nn2vi')))
            if name in contract['modules']:
                image = initialized_image(raw, name)
                for span in contract['modules'][name]['spans'].values():
                    start = int(span['offset'], 16)
                    self.assertEqual(hashlib.sha256(image[start:start + span['bytes']]).hexdigest(), span['sha256'])
        self.assertEqual(imported, set(contract['imported_vi_apis']))
        self.assertEqual(len(imported), 6)
        from test_botw_whole_force_profile import source_registry
        selected = {name for name, _, titles in source_registry(self.source) if titles == 4}
        self.assertTrue(imported <= selected)
        self.assertEqual(contract['force_reference_mask'], 1)


if __name__ == '__main__':
    unittest.main()
