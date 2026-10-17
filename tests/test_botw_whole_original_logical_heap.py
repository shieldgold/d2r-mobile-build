"""Actual title enable, normal TSD, retained layout and original SVC bodies."""
from pathlib import Path
import importlib.util,json,os,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
PATCH='patches/original-logical-heap.patch'
BASELINE=os.environ.get('BOTW_WHOLE_ORIGINAL_LOGICAL_BASELINE')=='1'
spec=importlib.util.spec_from_file_location('logical_source_functions',ROOT/'tests/test_botw_whole_pool_backing.py')
source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)
@unittest.skipUnless(sys.platform=='darwin','uses real Darwin original thread/SVC and owned mmap')
class WholeOriginalLogicalHeap(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory(prefix='botw-whole-original-logical-');cls.addClassCleanup(cls.temp.cleanup);cls.work=Path(cls.temp.name)
  shutil.copytree(EXP/'upstream',cls.work/'source')
  patches=json.loads((EXP/'SOURCE.lock.json').read_bytes())['patches']
  for name in patches+([] if BASELINE or PATCH in patches or not (EXP/PATCH).exists() else [PATCH]):
   if BASELINE and name==PATCH:continue
   p=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(EXP/name)],cwd=cls.work/'source',capture_output=True,text=True)
   if p.returncode:raise AssertionError(p.stdout+p.stderr)
  text=(cls.work/'source/dm/host/hle_svc.c').read_text()
  a=text.index('static uint8_t *g_as;');b=text.index('// Application memory pool',a)
  (cls.work/'actual_memory_init.inc').write_text(text[a:b])
  a=b;b=text.index('/* Normal allocator capacities',a);(cls.work/'actual_pool.inc').write_text(text[a:b])
  (cls.work/'actual_inputs.inc').write_text(source.function(text,'int dm_guest_heap_plan_inputs('))
  a=text.index('enum { I_CoreMask');b=text.index('#include "whole_transfer_memory.inc"',a)
  (cls.work/'actual_registry.inc').write_text(text[a:b])
  transfer=(EXP/'adapter/whole_transfer_memory.inc').read_text()
  (cls.work/'actual_shrink.inc').write_text(source.function(transfer,'static int whole_range_overlap(')+'\n'+source.function(transfer,'static int whole_heap_shrink_conflict_locked('))
  (cls.work/'actual_svc_cases.inc').write_text(source.function(text,'case 0x01:')+'\n'+source.function(text,'case 0x29:'))
  cls.binary=cls.work/'probe'
  command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-format','-pthread','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(cls.work/'source/dm/host'),'-I',str(EXP/'adapter'),'-I',str(cls.work),str(EXP/'tests/whole_original_logical_heap_probe.c'),str(EXP/'adapter/whole_policy.c'),'-o',str(cls.binary)]
  p=subprocess.run(command,capture_output=True,text=True)
  if p.returncode:raise AssertionError(p.stdout+p.stderr)
 def case(self,name):
  p=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=20)
  self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertIn('WHOLE_ORIGINAL_LOGICAL_CPU_OK',p.stdout)
 def test_actual_title_enable_selects_original_H_R_in_four_gib_sparse_heap(self):self.case('four')
 def test_five_gib_keeps_original_layout_but_reports_logical_H_plus_image(self):self.case('five')
 def test_three_gib_does_not_silently_select_bounded_heap(self):self.case('three')
 def test_two_gib_does_not_silently_select_bounded_heap(self):self.case('two')
 def test_fresh_guard_failure_does_not_publish_or_change_permissions(self):self.case('guard')
 def test_real_permission_failure_preserves_initial_heap_transaction(self):self.case('protect')
 def test_pure_original_plan_has_exact_capacity_guard_and_transactional_outputs(self):self.case('plans')
 def test_title_cannot_replace_an_immutable_legacy_bounded_plan(self):self.case('immutable')
if __name__=='__main__':unittest.main()
