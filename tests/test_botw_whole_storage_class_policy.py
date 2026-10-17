"""Two complete storage-class producers and the existing owned pool registry.

No query guarantee is asserted for a stale texture after same-address pool
reuse: the original query checks magic/flags; actual consumers own generation
validation. Physical/virtual operands here are rejection-only typed metadata,
not successfully created physical or virtual resources.
"""
from pathlib import Path
import os
import subprocess
import sys
import unittest

import test_botw_whole_pool_backing as pool_fixture

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_STORAGE_CLASS_BASELINE')=='1'

@unittest.skipUnless(sys.platform=='darwin','requires the actual Darwin owned backing provider')
class WholeStorageClassPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pool_fixture.WholePoolBacking.setUpClass()
        cls.addClassCleanup(pool_fixture.WholePoolBacking.doClassCleanups)
        cls.work=pool_fixture.WholePoolBacking.work;cls.source=pool_fixture.WholePoolBacking.source
        path=cls.source/'dm/gfx/nvn/botw_storage_class.c'
        cls.original=path.read_bytes()
        if not BASELINE:
            subprocess.run(['patch','--fuzz=0','-p1','-i',str(EXP/'patches/base-v0-storage-class-policy.patch')],
                           cwd=cls.source,check=True,capture_output=True,text=True)
        bridge=cls.work/'storage_bridge.c'
        bridge.write_text('#include "'+str(path)+'"\n'
                          'NVNstorageClass owned_storage_builder(const NVNtextureBuilder *p){return impl_nvnTextureBuilderGetStorageClass(p);}\n'
                          'NVNstorageClass owned_storage_texture(const NVNtexture *p){return impl_nvnTextureGetStorageClass(p);}\n')
        cls.binary=cls.work/'storage-probe'
        command=['clang','-std=gnu11','-fobjc-arc','-fobjc-arc-exceptions','-fblocks','-O1','-g',
                 '-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-format','-pthread',
                 '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                 '-I',str(cls.source/'dm/gfx/nvn'),'-I',str(cls.source/'dm/gfx/nvn/include'),
                 '-I',str(cls.source/'dm/host'),'-I',str(EXP/'adapter'),'-I',str(cls.work),
                 '-include',str(EXP/'adapter/whole_policy.h'),
                 str(EXP/'tests/whole_storage_class_policy_probe.m'),str(bridge),
                 str(EXP/'adapter/whole_pool_backing_memory.m'),str(EXP/'adapter/whole_pool_backing_gate.c'),
                 str(EXP/'adapter/whole_policy.c'),'-framework','Foundation','-framework','Metal','-o',str(cls.binary)]
        # The original fixture owns its normal LOG/die stand-ins. The production
        # storage TU and real policy emitter are separate translation units.
        command[command.index('-include'):command.index('-include')+2]=[]
        bridge.write_text('#include "whole_policy.h"\n'+bridge.read_text())
        built=subprocess.run(command,capture_output=True,text=True,timeout=60)
        if built.returncode:raise AssertionError(built.stdout+built.stderr)
        cls.actual=path.read_bytes()

    def mode(self,name,expected):
        p=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,expected,p.stdout+p.stderr)
        self.assertNotIn('ERROR: AddressSanitizer',p.stderr)
        self.assertNotIn('runtime error:',p.stderr)
        if expected==0:self.assertIn('WHOLE_STORAGE_CLASS_CPU_OK',p.stdout)
        else:self.assertIn('UNIMPL NVN texture storage class:',p.stderr)

    def test_builder_pre_storage_metadata_has_no_pool(self):self.mode('builder-empty',0)
    def test_both_actual_queries_use_existing_live_ordinary_backing(self):self.mode('live',0)
    def test_original_explicit_probe_policy_still_works(self):self.mode('old-probe',0)
    def test_missing_and_unknown_policy_reject(self):
        for m in ('missing','unknown'):
            with self.subTest(m=m):self.mode(m,3)
    def test_null_uninitialized_and_missing_pool_reject(self):
        for m in ('null-builder','null-texture','uninitialized','missing-pool'):
            with self.subTest(m=m):self.mode(m,3)
    def test_both_queries_reject_nonordinary_or_bad_pool_objects(self):
        for m in ('physical','virtual','bad-pool','texture-physical','texture-virtual','texture-bad-pool','finalized'):
            with self.subTest(m=m):self.mode(m,3)
    def test_patch_preserves_all_original_nonpolicy_bytes(self):
        if BASELINE:self.assertEqual(self.actual,self.original);return
        result=subprocess.run(['patch','--fuzz=0','-R','-p1','-i',str(EXP/'patches/base-v0-storage-class-policy.patch')],
                              cwd=self.source,check=True,capture_output=True,text=True)
        self.assertEqual((self.source/'dm/gfx/nvn/botw_storage_class.c').read_bytes(),self.original)
