"""Synthetic linear layout and real Metal storage: no game input or shader."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from botw_metal_counter_capability import require_counter_sampling

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == 'Darwin', 'requires Metal')
class LinearTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.directory.name) / 'linear-texture'
        command = ['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fblocks', '-Wall',
                   '-Wextra', '-Werror', '-I', str(ROOT / 'native/botw_graphics'),
                   str(ROOT / 'native/botw_graphics/graphics.m'),
                   str(ROOT / 'native/botw_graphics/tools/probe_linear_texture.c'),
                   '-framework', 'Foundation', '-framework', 'Metal',
                   '-framework', 'QuartzCore', '-o', str(cls.executable)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def probe(self, mode, expected):
        run = subprocess.run([str(self.executable), mode], capture_output=True,
                             text=True, timeout=30)
        if run.returncode == 77:
            self.skipTest('Metal unavailable')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(expected, run.stdout)
        self.assertNotIn('failed assertion', run.stderr)

    def test_queries_describe_real_device_policy(self):
        self.probe('query', 'LINEAR_POLICY_QUERY_OK')

    def test_zero_stride_uses_same_policy_for_storage_and_metal(self):
        self.probe('zero-stride', 'LINEAR_GPU_POOL_CPU_AND_PADDING_OK')

    def test_real_gpu_render_cpu_region_and_padding(self):
        self.probe('render', 'LINEAR_GPU_POOL_CPU_AND_PADDING_OK')

    def test_sampled_linear_is_not_a_render_target(self):
        self.probe('plain-attachment', 'LINEAR_SAMPLED_ATTACHMENT_REJECTED_OK')

    def test_invalid_storage_never_reaches_metal(self):
        for mode in ('bad-stride', 'bad-offset', 'bad-depth', 'bad-compressed',
                     'bad-array', 'bad-levels', 'bad-samples', 'copied-pool'):
            with self.subTest(mode=mode):
                self.probe(mode, 'LINEAR_INVALID_REJECTED_OK')

    def test_live_and_retired_storage_authority(self):
        for mode in ('live-alias', 'live-private-alias', 'private-linear-alias', 'private-retired'):
            with self.subTest(mode=mode):
                self.probe(mode, 'LINEAR_STORAGE_ALIAS_REJECTED_OK')

    def test_linear_texture_sampling_reaches_a_real_draw(self):
        self.probe('sample-draw', 'LINEAR_SAMPLING_REACHES_REAL_DRAW_OK')

    def test_unused_vertex_binding_is_not_false_feedback(self):
        self.probe('unused-vertex', 'LINEAR_UNUSED_VERTEX_SLOT_IS_NOT_FEEDBACK_OK')

    def test_feedback_is_rejected_before_any_batch_publication(self):
        for mode in ('feedback-texture', 'feedback-storage', 'feedback-two-handles'):
            with self.subTest(mode=mode):
                self.probe(mode, 'LINEAR_FEEDBACK_PREFLIGHT_PRESERVES_POOL_AND_COUNTER_OK')

    def test_feedback_after_counter_is_rejected_before_any_batch_publication(self):
        require_counter_sampling()
        self.probe('feedback-after-counter',
                   'LINEAR_FEEDBACK_PREFLIGHT_PRESERVES_POOL_AND_COUNTER_OK')


class LinearPolicyArithmeticTests(unittest.TestCase):
    def test_checked_device_policy_and_layout_arithmetic(self):
        header = ROOT / 'native/botw_graphics/linear_texture.h'
        self.assertTrue(header.is_file(), 'shared linear device policy/layout is missing')
        source = r'''
#include "linear_texture.h"
#include <assert.h>
#include <limits.h>
int main(void){
 BWLinearPolicy p; BWLinearLayout l;
 size_t a[]={16,16,16}, bigger[]={16,256}, bad[]={0}, odd[]={24}, huge[]={((size_t)INT_MAX)+1};
 assert(bw_linear_policy_make(a,3,1,&p));assert(p.textureAlignment==32&&p.renderAlignment==128&&p.formatMax==16&&p.formatCount==3);
 assert(bw_linear_layout(&p,0x40,5,3,0,4,&l));assert(l.stride==128&&l.size==384&&l.alignment==128);
 assert(bw_linear_layout(&p,0x10,5,3,0,4,&l));assert(l.stride==32&&l.size==96);
 assert(!bw_linear_layout(&p,0x40,5,3,48,4,&l));assert(!bw_linear_layout(&p,0x40,5,3,16,4,&l));
 assert(!bw_linear_layout(&p,0x40,SIZE_MAX,3,0,4,&l));assert(!bw_linear_layout(&p,0x40,1,SIZE_MAX,0,4,&l));
 assert(!bw_linear_layout(&p,0x40,SIZE_MAX-3,1,0,1,&l));assert(!bw_linear_layout(&p,0x40,0,1,0,4,&l));
 assert(bw_linear_policy_make(bigger,2,1,&p));assert(p.textureAlignment==256&&p.renderAlignment==256);
 assert(bw_linear_layout(&p,0x40,5,3,0,4,&l)&&l.size==768);
 assert(!bw_linear_policy_make(a,0,1,&p));assert(!bw_linear_policy_make(bad,1,1,&p));
 assert(!bw_linear_policy_make(odd,1,1,&p));assert(!bw_linear_policy_make(huge,1,1,&p));
 assert(bw_linear_policy_make(a,3,0,&p));assert(bw_linear_alignment(&p,0x10)==32);assert(!bw_linear_alignment(&p,0x40));
 assert(!bw_linear_alignment(&p,0));return 0;
}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'policy.c').write_text(source)
            command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-I', str(header.parent),
                       str(path / 'policy.c'), '-o', str(path / 'policy')]
            built = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(path / 'policy')], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
