"""Owned normal SVC bridge + complete real services + original one kernel table."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_IPC_BASELINE') == '1'


class WholeIpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='botw-whole-ipc-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name); source = work / 'source'
        shutil.copytree(EXP / 'upstream', source)
        patches = ['kernel-services.patch'] + ([] if BASELINE else ['ipc-services.patch'])
        for name in patches:
            result = subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(EXP / 'patches' / name)],
                                    cwd=source, capture_output=True, text=True)
            if result.returncode: raise AssertionError(result.stdout + result.stderr)
        text = (source / 'dm/host/hle_thread.c').read_text()
        a = text.index('enum { O_FREE,'); b = text.index('static thr_t *cur(void)', a)
        registry = text[a:b]
        a = text.index('static void thr_free('); b = text.index('static const char *thr_name(', a)
        thread_free = text[a:b]
        a = text.index('static void wait_unlink('); b = text.index('// Suspend until woken', a)
        waits = text[a:b]
        a = text.index('static int sig_obj('); b = text.index('static int64_t now_mono_ns(', a)
        consumers = text[a:b]
        a = text.index('// ---------- Checked native service kernel bridge ----------')
        (work / 'original_kernel_registry.inc').write_text(registry + thread_free + waits + consumers + text[a:])
        a = text.index('    case 0x16: {  // CloseHandle('); b = text.index('\n    }', a) + len('\n    }')
        close = text[a:b]
        # The fixture creates a live, unexited owned thread. Only the original
        # close entry's unexecuted exited-object cleanup needs this declaration.
        (work / 'original_close_case.inc').write_text(close)
        svc = (source / 'dm/host/hle_svc.c').read_text()
        a = svc.index('    if (svc_thread(num, regs))')
        if not BASELINE: a = svc.index('    { char ipc_error[')
        b = svc.index('    if (num >= 0x80)', a)
        (work / 'dispatch_prefix.inc').write_text(svc[a:b])
        cls.binary = work / 'probe'
        cmd = [shutil.which('clang') or 'cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-Wno-unused-variable', '-pthread',
               '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
               '-I', source / 'dm/host', '-I', EXP / 'adapter', '-I', work,
               '-I', ROOT / 'native/botw_host', EXP / 'tests/whole_ipc_probe.c',
               *[ROOT / p for p in ('native/botw_host/services.c', 'native/botw_resources/romfs.c',
                                   'native/botw_resources/savefs.c', 'native/botw_resources/local_user.c',
                                   'native/botw_input/input.c')], '-o', cls.binary]
        if BASELINE: cmd += ['-DOWNED_IPC_BASELINE=1']
        else: cmd += [EXP / 'adapter/whole_ipc.c']
        if platform.system() == 'Darwin': cmd += ['-framework', 'Security']
        result = subprocess.run([str(p) for p in cmd], capture_output=True, text=True)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)

    def probe(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_original_transport_close_must_retire_instead_of_faking_success(self): self.probe('close')
    def test_real_sm_lm_logger_clone_and_explicit_and_tlr_sends(self): self.probe('protocol')
    def test_tlr_is_per_thread_and_elf_tls_is_never_used(self): self.probe('parallel-tlr')
    def test_unbound_provider_fails_before_reading_or_changing_registers(self): self.probe('unbound')
    def test_invalid_handles_return_real_bad_handle_before_callbacks(self): self.probe('invalid')
    def test_named_port_logical_and_protected_read_failure_preserves_registers(self): self.probe('read-failure')
    def test_named_port_is_bounded_to_sixteen_checked_bytes(self): self.probe('too-long')
    def test_actual_service_parse_failure_preserves_registers(self): self.probe('send-failure')
    def test_missing_current_tlr_fails_without_register_publication(self): self.probe('tls-failure')
    def test_user_ipc_size_respects_real_service_admission(self): self.probe('bounds')
    def test_actual_hook_propagates_fixed_failure_without_register_dump(self): self.probe('hook-failure')
    def test_binding_replacement_and_wrong_unbind_are_rejected(self): self.probe('binding')
    def test_failed_transport_configuration_never_publishes_half_binding(self): self.probe('bind-failure')
    @unittest.skipUnless(platform.system() == 'Darwin', 'actual Darwin protected VM copy failure required')
    def test_real_owned_inaccessible_page_fails_without_raw_cpu_fallback(self): self.probe('protected-read')
    def test_unbind_waits_for_actual_inflight_provider_borrow(self): self.probe('borrow')


if __name__ == '__main__': unittest.main()
