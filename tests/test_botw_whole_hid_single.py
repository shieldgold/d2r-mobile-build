"""Exact base-v0 single-state ABI routed to the complete original pad producer.

Owned CPU buffers and normal checked write callbacks only; no guest/device/GPU.
"""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
COMPONENT=ROOT/'experiments/botw_upstream_host'
BASELINE=os.environ.get('BOTW_WHOLE_HID_SINGLE_BASELINE')=='1'

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('clang'),'real Darwin static registry')
class WholeHidSingle(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory(prefix='botw-hid-single-');cls.addClassCleanup(cls.temp.cleanup)
  cls.work=Path(cls.temp.name);cls.source=cls.work/'source'
  shutil.copytree(COMPONENT/'upstream',cls.source)
  patches=json.loads((COMPONENT/'SOURCE.lock.json').read_text())['patches']
  new='patches/base-v0-hid-single-state.patch'
  for name in patches:
   if name==new:continue
   r=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(COMPONENT/name)],cwd=cls.source,capture_output=True,text=True)
   if r.returncode:raise AssertionError(r.stdout+r.stderr)
  if not BASELINE:
   r=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(COMPONENT/new)],cwd=cls.source,capture_output=True,text=True)
   if r.returncode:raise AssertionError(r.stdout+r.stderr)
  crash=(cls.source/'dm/host/crash.c').read_text();a=crash.index('#define MAX_FAULT_HOOK');b=crash.index('// The diagnostics module',a)
  (cls.work/'normal_registry.inc').write_text(crash[a:b])
  # ASan inserts padding between separately declared globals. Preserve only
  # the original contiguous Mach-O metadata records; all producer code and
  # owned input/output buffers retain ASan/UBSan instrumentation.
  ignore=cls.work/'metadata-only.asan.ignore';ignore.write_text('global:hle_force_*\n')
  cls.binary=cls.work/'probe'
  command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-format','-Wno-unused-function',
   '-pthread','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fsanitize-ignorelist='+str(ignore),'-I',str(cls.source),'-I',str(cls.source/'dm/host'),'-I',str(cls.work),
   str(COMPONENT/'tests/whole_hid_single_probe.c'),'-o',str(cls.binary)]
  if BASELINE:command+=['-DBOTW_WHOLE_HID_SINGLE_BASELINE=1']
  r=subprocess.run(command,capture_output=True,text=True)
  if r.returncode:raise AssertionError(r.stdout+r.stderr)
 def case(self,mode):
  r=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=15)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_exact_five_single_state_entries_are_registered(self):self.case('registry')
 def test_empty_all_ten_legal_ids_and_five_styles_preserves_output(self):self.case('empty')
 def test_real_pad_buttons_axes_and_fullkey_handheld_commit_only_40_bytes(self):self.case('active')
 def test_invalid_id_and_uninitialized_slot_fail_without_output(self):
  for mode in ('bad-id','uninitialized','null-id','null-out'):self.case(mode)
 def test_real_checked_registry_rejection_does_not_commit_output(self):self.case('prepare-failed')
 def test_touch_and_gamecontroller_merge_and_focus_release(self):self.case('focus')
 def test_concurrent_actual_pad_state_remains_coherent(self):self.case('coherent')

if __name__=='__main__':unittest.main()
