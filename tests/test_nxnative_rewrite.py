"""Owned offline-runner boundaries. The vendor rewriter CLI is never invoked."""
import hashlib
import importlib._bootstrap_external
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/'experiments/nxnative_base_v0/tools/rewrite_base_v0.py'
R = types.ModuleType('owned_nxnative_runner')
R.__file__ = str(PATH)
exec(compile(PATH.read_bytes(),str(PATH),'exec'), R.__dict__)


def nso(build):
    image = bytearray(0x500)
    struct.pack_into('<I',image,4,0x80)
    image[0x20:0x24] = bytes.fromhex('004cb852')
    image[0x24:0x28] = bytes.fromhex('080cb852')
    image[0x80:0x84] = b'MOD0'
    struct.pack_into('<i',image,0x84,0x80)
    strings = b'\0owned\0'
    image[0x200:0x200+len(strings)] = strings
    for i, row in enumerate(((5,0x200),(6,0x240),(10,len(strings)),(11,24),(4,0x2c0),(0,0))):
        struct.pack_into('<QQ',image,0x100+16*i,*row)
    struct.pack_into('<II',image,0x2c0,1,1)
    header = bytearray(0x100)
    header[:4] = b'NSO0'
    header[0x40:0x60] = bytes([build])*32
    struct.pack_into('<I',header,0xc,0x38)
    struct.pack_into('<I',header,0x3c,0x80)
    parts = (image[:0x100],image[0x100:0x300],image[0x400:0x500])
    for i, (file, address, size) in enumerate(((0x100,0,0x100),(0x200,0x100,0x200),(0x400,0x400,0x100))):
        struct.pack_into('<III',header,0x10+16*i,file,address,size)
        struct.pack_into('<I',header,0x60+4*i,size)
        header[0xa0+32*i:0xc0+32*i] = hashlib.sha256(parts[i]).digest()
    return bytes(header)+parts[0]+parts[1]+bytes(0x100)+parts[2]


def poisoned_cache(path, raw):
    path.write_bytes(raw)
    cache = Path(importlib.util.cache_from_source(str(path)))
    cache.parent.mkdir(exist_ok=True)
    bad = compile("marker='UNLOCKED_PYC'\n",str(path),'exec')
    cache.write_bytes(importlib._bootstrap_external._code_to_timestamp_pyc(
        bad,int(path.stat().st_mtime),len(raw)))


class NxnativeRewrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='owned-nxnative-runner-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_locked_source_loader_never_executes_timestamp_valid_unlocked_pyc(self):
        path = self.root/'owned_module.py'
        raw = b"marker='LOCKED_SOURCE'\n"
        poisoned_cache(path,raw)
        # Counterexample is an actual owned Python cache, not a mocked result.
        spec = importlib.util.spec_from_file_location('owned_cache_counterexample',path)
        cached = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cached)
        self.assertEqual(cached.marker,'UNLOCKED_PYC')
        actual = R.source_module('owned_verified_source',path,R.sha(raw))
        self.assertEqual(actual.marker,'LOCKED_SOURCE')
        with self.assertRaisesRegex(ValueError,'source digest'):
            R.source_module('owned_wrong_source',path,'0'*64)

    def test_clean_environment_has_no_inherited_private_or_python_injection_flags(self):
        with mock.patch.dict(os.environ,{'DM_RULESET':'touch-run-v4','DM_PRIVATE_GATES':'1',
                                         'DM_DUMP':'1','PYTHONPATH':'/unlocked','DYLD_INSERT_LIBRARIES':'/unlocked'}):
            actual = R.child_environment(ROOT/'scripts',self.root)
        self.assertEqual(actual['DM_RULESET'],'native')
        self.assertEqual(actual['DM_PRIVATE_GATES'],'0')
        self.assertEqual(actual['PYTHONPATH'],str(ROOT/'scripts'))
        self.assertEqual(actual['PYTHONOPTIMIZE'],'0')
        self.assertNotIn('DM_DUMP',actual)
        self.assertNotIn('DYLD_INSERT_LIBRARIES',actual)
        self.assertFalse(any(k.startswith('DM_') and k not in ('DM_RULESET','DM_PRIVATE_GATES') for k in actual))

    def context(self):
        pre = R.source_module('owned_real_preflight',ROOT/'experiments/nxnative_base_v0/tools/preflight_profile.py',R.PREFLIGHT_SHA)
        reader = R.source_module('owned_real_nso',ROOT/'scripts/botw_native/nso.py',R.NSO_SHA)
        exefs = self.root/'exefs'
        exefs.mkdir()
        profile = {'modules':{}}
        for i,name in enumerate((*R.MODULES,'rtld'),1):
            raw = nso(i)
            (exefs/name).write_bytes(raw)
            profile['modules'][name] = {'bytes':len(raw),'sha256':R.sha(raw),
                                       'build_id':raw[0x40:0x60].hex(),'load':name!='rtld'}
        return exefs,profile,pre,reader

    def producer(self,reader,mutate=None):
        calls = []
        def owned_only(command,environment,log):
            self.assertEqual(command[1:3],['-B','-S'])
            name = command[-1]
            calls.append(name)
            raw = Path(command[-3]).read_bytes()
            destination = Path(command[-2])
            original = reader.initialized_image(raw,name)
            values = R.layout(raw)
            image = original+bytes(values['PATCH_OFF']-len(original))+bytes.fromhex('1f2003d5')
            (destination/(name+'.patched.bin')).write_bytes(image)
            text = ''.join(f'# {key}\t{value:#x}\n' for key,value in values.items())
            text += '# BUILD_ID\t'+raw[0x40:0x50].hex()+'\n'
            (destination/(name+'.fixups.tsv')).write_text(text)
            for kind in ('sites','gates'):
                (destination/f'{name}.{kind}.tsv').write_bytes(b'')
            for kind in ('cuts','obs','params'):
                (destination/f'{name}.{kind}.tsv').write_text('# BUILD_ID\t'+raw[0x40:0x50].hex()+'\n')
            (destination/'build_src.sha256').write_text(R.PATCHED_SHA+'  patch_nso.py\n')
            Path(log).write_text('owned synthetic producer; vendor CLI not run\n')
            if mutate:
                mutate(destination,name)
        return owned_only,calls

    def run_owned(self,mutation=None):
        exefs,profile,pre,reader = self.context()
        producer,calls = self.producer(reader,mutation)
        with mock.patch.object(R,'admit_source',return_value=(profile,pre,reader,{'source_lock_sha256':'owned-only'})), \
             mock.patch.object(R,'invoke_vendor',side_effect=producer), \
             mock.patch.object(R,'HEAP_SITES',((0x20,bytes.fromhex('004cb852')),(0x24,bytes.fromhex('080cb852')))):
            report = R.rewrite_bundle(exefs,self.root/'new-output')
        return report,calls

    def test_owned_complete_pipeline_snapshots_seven_and_dispatches_six_in_order(self):
        report,calls = self.run_owned()
        self.assertEqual(calls,list(R.MODULES))
        self.assertEqual(set(report['input_sha256']),set((*R.MODULES,'rtld')))
        self.assertFalse(report['rtld_loaded'])
        self.assertEqual(report['heap_policy'],'original-logical-literals')
        self.assertEqual(report['heap_bytes'],0xc2600000)
        self.assertFalse(report['old_guest29_outputs_reused'])
        self.assertFalse(report['guest_executed'])
        self.assertFalse(report['gameplay_verified'])
        self.assertFalse((self.root/'new-output/tool/dm/data').exists())

    def test_existing_or_symlink_output_is_rejected_before_source_or_vendor_entry(self):
        existing = self.root/'old-output'
        existing.mkdir()
        for target in (existing,self.root/'alias-output'):
            if target != existing:
                target.symlink_to(existing,target_is_directory=True)
            with mock.patch.object(R,'admit_source') as gate, mock.patch.object(R,'invoke_vendor') as call:
                with self.assertRaisesRegex(ValueError,'fresh'):
                    R.rewrite_bundle(self.root/'unused',target)
                gate.assert_not_called()
                call.assert_not_called()

    def test_bad_seventh_input_or_extra_member_never_reaches_child(self):
        exefs,profile,pre,reader = self.context()
        with mock.patch.object(R,'admit_source',return_value=(profile,pre,reader,{})),mock.patch.object(R,'invoke_vendor') as call:
            original = (exefs/'rtld').read_bytes()
            (exefs/'rtld').write_bytes(original[:-1]+bytes([original[-1]^1]))
            with self.assertRaises(ValueError):
                R.rewrite_bundle(exefs,self.root/'output')
            self.assertFalse((self.root/'output').exists())
            (exefs/'rtld').write_bytes(original)
            (exefs/'unknown').write_bytes(b'owned')
            with self.assertRaises(ValueError):
                R.rewrite_bundle(exefs,self.root/'output')
            call.assert_not_called()

    def test_output_contract_rejects_rules_identity_and_undeclared_mutation(self):
        def change_original(directory,name):
            path = directory/(name+'.patched.bin')
            raw = bytearray(path.read_bytes())
            raw[0x30] ^= 1
            path.write_bytes(raw)
        def change_heap_literal(directory,name):
            path = directory/(name+'.patched.bin')
            raw = bytearray(path.read_bytes())
            raw[0x20] ^= 1
            path.write_bytes(raw)
        mutations = (
            lambda d,n:(d/f'{n}.obs.tsv').write_text('# BUILD_ID\twrong\n'),
            lambda d,n:(d/f'{n}.cuts.tsv').write_text('0x0\tprivate\n'),
            lambda d,n:(d/f'{n}.fixups.tsv').write_text('# PATCH_OFF\t0x0\n'),
            lambda d,n:(d/f'{n}.patched.bin').write_bytes(b'bad'),
            change_original,
            change_heap_literal,
        )
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                # Each has its own fresh owned input/output tree.
                case = self.root/str(mutations.index(mutate))
                case.mkdir()
                saved = self.root
                self.root = case
                try:
                    with self.assertRaises((ValueError,struct.error)):
                        self.run_owned(mutate)
                    self.assertFalse((case/'new-output/report.json').exists())
                finally:
                    self.root = saved

    def test_self_must_be_in_the_final_source_lock_before_loading_helpers(self):
        component = self.root/'component'
        component.mkdir()
        (component/'SOURCE.lock.json').write_text(json.dumps({'upstream_commit':R.COMMIT,'upstream_tree':R.TREE,'adapter_files':{}}))
        with mock.patch.object(R,'COMPONENT',component),mock.patch.object(R,'source_module') as loader:
            with self.assertRaisesRegex(ValueError,'runner not source bound'):
                R.admit_source()
            loader.assert_not_called()

    def test_bootstrap_uses_verified_source_not_package_init_or_prover_pyc(self):
        scripts = self.root/'scripts'
        package = scripts/'botw_native'
        package.mkdir(parents=True)
        prover = (ROOT/'scripts/botw_native/branch_tables.py').read_bytes()
        poisoned_cache(package/'branch_tables.py',prover)
        (package/'__init__.py').write_text("raise RuntimeError('unlocked init must never execute')\n")
        tool = self.root/'owned_tool.py'
        raw = b"from botw_native.branch_tables import signed_jump_range\nassert callable(signed_jump_range)\nprint('owned source entry only')\n"
        tool.write_bytes(raw)
        with mock.patch.object(R,'PATCHED_SHA',R.sha(raw)):
            entry = self.root/'owned_entry.py'
            entry.write_text(R.bootstrap_source(scripts,R.dependency_context()['package_roots']))
        completed = subprocess.run([sys.executable,'-B','-S',str(entry),str(tool)],
                                   env=R.child_environment(scripts,self.root),capture_output=True)
        self.assertEqual(completed.returncode,0,completed.stderr.decode())
        self.assertEqual(completed.stdout,b'owned source entry only\n')


if __name__ == '__main__':
    unittest.main()
