"""Two fixed base-v0 forces reuse the full existing native input module."""
from pathlib import Path
import os,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];C=ROOT/'experiments/nxnative_base_v0'
PATCH=C/'patches/base-v0-debug-pad-input.patch'
PREDECESSORS=('normal-entry.patch','base-v0-texture-view.patch','normal-hle-crash.patch',
'horizon-address-signal.patch','normal-runtime-diagnostics.patch','base-v0-conditional-shader-provider.patch',
'base-v0-logical-memory.patch','base-v0-normal-api-contracts.patch','base-v0-normal-loader-guard.patch',
'base-v0-audren-kernel.patch','base-v0-audren-runtime.patch')
BASELINE=os.environ.get('NXNATIVE_DEBUG_PAD_BASELINE')=='1'
# Only the owned lexical helper is loaded from current source bytes.
import types
p=ROOT/'tests/test_nxnative_normal_runtime_diagnostics.py';helper=types.ModuleType('owned_debug_lexical');helper.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),helper.__dict__)
class NxnativeDebugPad(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.t=tempfile.TemporaryDirectory(prefix='nxnative-owned-debugpad-');cls.addClassCleanup(cls.t.cleanup);w=Path(cls.t.name);s=w/'source';shutil.copytree(C/'upstream',s)
  for name in (*PREDECESSORS,*(()if BASELINE else(PATCH.name,))):
   r=subprocess.run(['patch','--batch','--fuzz=0','--no-backup-if-mismatch','-p1','-i',str(C/'patches'/name)],cwd=s,capture_output=True,text=True)
   if r.returncode:raise AssertionError(r.stdout+r.stderr)
  # This CPU HID translation unit consumes no game layout constants. Its
  # required generated include is therefore an explicitly empty owned header.
  (w/'game_profile.h').write_text('#pragma once\n/* Owned HID CPU fixture; no game layout or execution. */\n')
  (w/'actual_hid.c').write_bytes((s/'dm/host/hle_hid.c').read_bytes())
  probe=(C/'tests/debug_pad_probe.c').read_text().replace('/* ACTUAL_VSYNC_THREAD */',helper.function((s/'dm/host/hle_sdk16.c').read_text(),'static void *vsync_thread'))
  (w/'probe.c').write_text(probe);cls.exe=w/'probe'
  # ASan global redzones change the real packed Mach-O force-section layout;
  # retain its native layout, as in the existing normal-API registry fixture.
  cmd=['xcrun','clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter','-Wno-missing-field-initializers','-fsanitize=address,undefined','-mllvm','-asan-globals=0','-fno-sanitize-recover=all','-DNXNATIVE_NORMAL_POLICY=1','-DNXNATIVE_BASE_V0_MEMORY=1','-DNXNATIVE_BASE_V0_AUDREN=1','-DNXNATIVE_BASE_V0_DEBUG_PAD=1','-I',str(w),'-I',str(s/'dm/host'),'-I',str(C/'adapter'),'-I',str(ROOT/'native/botw_input'),'-I',str(ROOT/'native/botw_host'),str(w/'probe.c'),str(ROOT/'native/botw_input/input.c'),str(C/'adapter/normal_policy.c'),str(C/'adapter/normal_failures.c'),'-o',str(cls.exe)]
  r=subprocess.run(cmd,capture_output=True,text=True)
  if r.returncode:raise AssertionError(r.stdout+r.stderr)
 def run_owned(self,mode,code=0):
  p=subprocess.run([str(self.exe),mode],capture_output=True,text=True,timeout=10,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
  self.assertEqual(p.returncode,code,p.stdout+p.stderr);self.assertEqual(p.stdout,'')
 def test_exact_two_base_v0_imports_have_unique_real_forces(self):self.run_owned('registry')
 def test_real_32_byte_disconnected_state_idempotent_init_and_canaries(self):self.run_owned('roundtrip')
 def test_existing_vsync_cadence_samples_but_getter_does_not(self):self.run_owned('clock')
 def test_preinit_null_output_is_fixed_failure(self):
  for mode in ('preinit','null'):self.run_owned(mode,70)
 def test_rejected_normal_output_remains_entirely_untouched(self):self.run_owned('deny')
 def test_original_npad_buttons_and_dual_sticks_are_unchanged(self):self.run_owned('npad')
 def test_concurrent_init_and_read_uses_complete_native_mutex(self):self.run_owned('concurrent')
if __name__=='__main__':unittest.main()
