"""Owned non-executable framework bytes; no actual Guest is generated or run.

Only the four test-local public guard input fingerprints are replaced with this
fixture's hashes. All guard/stack/heap/Host39 inverse algorithms remain real.
"""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from botw_native import allocation_guard as guard
from botw_native import guest_framework as gf
from botw_native import heap_policy as heap
from botw_native import hipc_address as address
from botw_native import thread_stack as stack
import test_botw_guest_framework as baseline
import test_botw_thread_stack_v4 as v4_fixtures
from test_botw_allocation_guard import GATE_TEMPLATE


def digest(raw): return hashlib.sha256(raw).hexdigest()


class AllocationGuardFramework(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source, _ = v4_fixtures.fixture()
        source = bytearray(source.ljust(guard._FIXUPS['DATA_OFF'] + 256, b'\0'))
        source[guard.CONTEXT_START:guard.CONTEXT_START + len(guard.CONTEXT)] = guard.CONTEXT
        cls.original = bytes(source)
        generic = bytearray(cls.original.ljust(guard.INPUT_BYTES - 132, b'\0'))
        struct.pack_into('<I', generic, 0, 0x14000000 | (guard.PATCH_START // 4))
        struct.pack_into('<I', generic, guard.PATCH_START, 0xd65f03c0)
        struct.pack_into('<I', generic, guard.REFERENCE_SITE,
                         0x14000000 | (((guard.REFERENCE_GATE - guard.REFERENCE_SITE) // 4) & 0x3ffffff))
        generic[guard.REFERENCE_GATE:guard.REFERENCE_GATE + len(GATE_TEMPLATE)] = GATE_TEMPLATE
        cls.generic = bytes(generic)
        host39, cls.am = address.apply('main', cls.original, cls.generic,
                                       {'generic_site_offsets': [0, guard.REFERENCE_SITE]})
        after_heap, cls.hm = heap.apply(cls.original, host39, guard._FIXUPS,
                                       generic_site_offsets=[0, guard.REFERENCE_SITE])
        cls.v4, cls.sm = stack.apply(cls.original, after_heap,
                                     generic_site_offsets=[0, guard.REFERENCE_SITE], policy='host-pthread-stack-v4')
        assert len(cls.v4) == guard.INPUT_BYTES
        cls.fixups = ''.join(f'# {key}\t{value if key.endswith("_IDX") else hex(value)}\n'
                             for key, value in guard._FIXUPS.items()).encode()
        cls.gates = (f'0x{guard.PATCH_START + 4:x}\tsvc\t38\t0x0\n'
                     f'0x{guard.REFERENCE_RETURN:x}\tsvc\t38\t0x{guard.REFERENCE_SITE:x}\n').encode()
        cls.sites = f'0x0\tx18\n0x{guard.REFERENCE_SITE:x}\tsvc\n'.encode()
        cls.guarded, cls.new_gates, cls.new_sites, cls.manifest = guard._apply_admitted(
            cls.v4, cls.fixups, cls.gates, cls.sites)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='botw-owned-guard-framework-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report_path, self.rewrite = baseline.fixture(self.root)
        self.report = json.loads(self.report_path.read_text())
        fingerprints = json.loads((ROOT / 'profiles/botw-switch-base-v0.json').read_text())['modules']
        for row in self.report['modules']: row.update(fingerprints[row['name']])
        for row in self.report['rewrites']: row['host_address_adaptation'] = {}
        self.report.update(host_address_policy=gf.HOST39_POLICY.copy(), guest_heap_policy=heap.POLICY.copy(),
                           guest_thread_stack_policy=stack.SZS_POLICY.copy(),
                           guest_allocation_guard_policy=guard.POLICY.copy())
        original = self.report['modules'][0]
        original.update(memory_bytes=guard.KEY_PAGE, segments=[
            {'name':'text','address':0,'bytes':guard.TEXT_END},
            {'name':'rodata','address':guard._FIXUPS['RO_OFF'],'bytes':4096},
            {'name':'data','address':guard._FIXUPS['DATA_OFF'],'bytes':256}])
        main = self.report['rewrites'][0]
        main.update(host_address_adaptation=copy.deepcopy(self.am), generic_image_sha256=digest(self.generic),
                    host39_image_sha256=self.hm['input_image_sha256'], heap_adaptation=copy.deepcopy(self.hm),
                    thread_stack_input_image_sha256=self.sm['input_image_sha256'], thread_stack_adaptation=copy.deepcopy(self.sm),
                    normal_allocation_guard=copy.deepcopy(self.manifest),
                    normal_allocation_guard_input_image_sha256=digest(self.v4), image_sha256=digest(self.guarded),
                    rewrite_site_count=3, structure={'branch_sites_checked':3,'host_gate_addresses_checked':3})
        self.main = main
        for kind, data in [('patched.bin',self.guarded),('fixups.tsv',self.fixups),
                           ('gates.tsv',self.new_gates),('sites.tsv',self.new_sites)]:
            (self.rewrite / ('main.' + kind)).write_bytes(data)

    def build(self, report=None, name='out'):
        self.report_path.write_text(json.dumps(self.report if report is None else report))
        # Identity substitutions exist only in this owned fixture's context.
        with mock.patch.multiple(guard, INPUT_SHA256=digest(self.v4), FIXUPS_SHA256=digest(self.fixups),
                                 GATES_SHA256=digest(self.gates), SITES_SHA256=digest(self.sites)):
            return gf.build_framework(self.report_path, self.rewrite, self.root/name, module_names=['main'])

    def rejected(self, report, name):
        with self.assertRaises(ValueError): self.build(report, name)
        self.assertFalse((self.root/name).exists())

    def test_complete_owned_guard_is_unwrapped_before_stack_heap_and_host39(self):
        calls = []
        originals = [('guard',guard,'verify'),('stack',stack,'verify'),('heap',heap,'verify'),('host39',address,'verify')]
        with mock.patch.object(guard,'verify',wraps=guard.verify) as g, \
             mock.patch.object(stack,'verify',wraps=stack.verify) as s, \
             mock.patch.object(heap,'verify',wraps=heap.verify) as h, \
             mock.patch.object(address,'verify',wraps=address.verify) as a:
            for name, module, method in originals:
                function = getattr(module,method)
                real = function._mock_wraps
                def observe(*args,_name=name,_real=real,**kwargs):
                    calls.append((_name,digest(args[0])));return _real(*args,**kwargs)
                function.side_effect = observe
            try: result = self.build()
            except ValueError as error: self.fail('Complete owned normal guard was rejected: ' + str(error))
        self.assertEqual([row[0] for row in calls],['guard','stack','heap','host39'])
        self.assertEqual([row[1] for row in calls], [digest(self.guarded),digest(self.v4),
            self.sm['input_image_sha256'],self.hm['input_image_sha256']])
        self.assertEqual(result['guest_allocation_guard_policy'],guard.POLICY)
        self.assertEqual((self.root/'out/dm/main.gates.tsv').read_bytes(),self.new_gates)
        self.assertEqual((self.root/'out/guest-framework-report.json').read_bytes(),
                         (json.dumps(result,indent=2,sort_keys=True)+'\n').encode())

    def test_ordinary_v4_without_guard_still_uses_original_chain(self):
        del self.report['guest_allocation_guard_policy']
        for key in ['normal_allocation_guard','normal_allocation_guard_input_image_sha256']: del self.main[key]
        self.main.update(image_sha256=digest(self.v4),rewrite_site_count=2,
                         structure={'branch_sites_checked':2,'host_gate_addresses_checked':2})
        for kind,data in [('patched.bin',self.v4),('gates.tsv',self.gates),('sites.tsv',self.sites)]:
            (self.rewrite/('main.'+kind)).write_bytes(data)
        self.assertNotIn('guest_allocation_guard_policy',self.build())

    def test_closed_policy_requires_host39_bounded_heap_and_exact_v4(self):
        for mode in ['policy_missing','policy_bool','policy_extra','heap_missing','stack_legacy','address_missing']:
            bad=copy.deepcopy(self.report)
            if mode=='policy_missing':del bad['guest_allocation_guard_policy']
            elif mode=='policy_bool':bad['guest_allocation_guard_policy']['schema']=True
            elif mode=='policy_extra':bad['guest_allocation_guard_policy']['extra']=1
            elif mode=='heap_missing':del bad['guest_heap_policy']
            elif mode=='stack_legacy':bad['guest_thread_stack_policy']=stack.NATIVE_POLICY.copy()
            else:del bad['host_address_policy']
            with self.subTest(mode=mode):self.rejected(bad,mode)

    def test_manifest_is_main_only_and_requires_independently_fixed_v4_input(self):
        for mode in ['no_manifest','no_anchor','wrong_anchor','bool_anchor','sdk_manifest']:
            bad=copy.deepcopy(self.report);main=bad['rewrites'][0]
            if mode=='no_manifest':del main['normal_allocation_guard']
            elif mode=='no_anchor':del main['normal_allocation_guard_input_image_sha256']
            elif mode=='wrong_anchor':main['normal_allocation_guard_input_image_sha256']='00'*32
            elif mode=='bool_anchor':main['normal_allocation_guard_input_image_sha256']=True
            else:bad['rewrites'][2]['normal_allocation_guard']=copy.deepcopy(self.manifest)
            with self.subTest(mode=mode):self.rejected(bad,mode)

    def test_complete_code_metadata_counts_and_generic_tables_remain_strict(self):
        for mode in ['gate_missing','site_missing','gate_duplicate','wrong_count','obs','cuts','params']:
            bad=copy.deepcopy(self.report)
            (self.rewrite/'main.gates.tsv').write_bytes(self.new_gates)
            (self.rewrite/'main.sites.tsv').write_bytes(self.new_sites)
            for kind in ['obs','cuts','params']:(self.rewrite/('main.'+kind+'.tsv')).write_text('# owned\n')
            if mode=='gate_missing':(self.rewrite/'main.gates.tsv').write_bytes(self.gates)
            elif mode=='site_missing':(self.rewrite/'main.sites.tsv').write_bytes(self.sites)
            elif mode=='gate_duplicate':(self.rewrite/'main.gates.tsv').write_bytes(self.new_gates+self.manifest['appended_gate_row'].encode())
            elif mode=='wrong_count':bad['rewrites'][0]['structure']['host_gate_addresses_checked']=2
            else:(self.rewrite/('main.'+mode+'.tsv')).write_text('0x0\town\n')
            with self.subTest(mode=mode):self.rejected(bad,mode)

    def test_guard_bytes_cannot_be_self_relabelled_as_ordinary_v4(self):
        bad=copy.deepcopy(self.report);del bad['guest_allocation_guard_policy']
        del bad['rewrites'][0]['normal_allocation_guard'];del bad['rewrites'][0]['normal_allocation_guard_input_image_sha256']
        self.rejected(bad,'disguised')
        tampered=bytearray(self.guarded);tampered[-8]^=1
        (self.rewrite/'main.patched.bin').write_bytes(tampered)
        bad=copy.deepcopy(self.report);bad['rewrites'][0]['image_sha256']=digest(tampered)
        self.rejected(bad,'code-tampered')
        with self.assertRaises(ValueError):guard.verify(self.guarded,self.fixups,self.new_gates,self.new_sites,self.manifest)


if __name__=='__main__':unittest.main()
