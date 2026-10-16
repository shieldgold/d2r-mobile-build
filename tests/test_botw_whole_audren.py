"""Owned CPU: full CAPI and one original kernel/transfer table, no device/Guest."""
from pathlib import Path
import os
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/botw_upstream_host'
AUDIO = ROOT / 'experiments/botw_audio_renderer'


def checked(command, **kwargs):
    run = subprocess.run([str(x) for x in command], capture_output=True, text=True, **kwargs)
    if run.returncode:
        raise AssertionError(f'{command!r}\n{run.stdout}{run.stderr}')
    return run


@unittest.skipUnless(platform.system() == 'Darwin', 'actual shared vm_remap and original PCM mock-AU require Darwin')
class WholeAudrenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('BOTW_WHOLE_AUDREN_BUILD'):
            source=Path(os.environ['BOTW_WHOLE_AUDREN_BUILD']).resolve();source.mkdir(parents=True,exist_ok=True)
        else:
            cls.temp=tempfile.TemporaryDirectory(prefix='botw-whole-audren-');cls.addClassCleanup(cls.temp.cleanup)
            source=Path(cls.temp.name)
        cls.build=source/'build'
        baseline=os.environ.get('BOTW_WHOLE_AUDREN_BASELINE')=='1'
        extra_sources=[]; extra_cmake=''
        if not baseline:
            import test_botw_whole_transfer as transfer_fixtures
            transfer_fixtures.WholeTransfer.setUpClass()
            cls.addClassCleanup(transfer_fixtures.WholeTransfer.doClassCleanups)
            kernel_work=transfer_fixtures.WholeTransfer.binary.parent
            extra_sources=[EXP/'adapter/whole_audren.c',EXP/'tests/whole_audren_kernel_probe.c',
                           EXP/'adapter/whole_transfer_vm.c',EXP/'adapter/base_v0_heap.c',EXP/'adapter/whole_policy.c']
            extra_sources += [ROOT/path for path in ('native/botw_host/services.c','native/botw_resources/romfs.c',
                'native/botw_resources/savefs.c','native/botw_resources/local_user.c','native/botw_input/input.c')]
            extra_cmake='target_include_directories(owned_whole_audren PRIVATE '+ ' '.join('"'+str(x)+'"' for x in
                (kernel_work/'dm/host',kernel_work,EXP/'tests',ROOT/'native/botw_host'))+')\n'
            extra_cmake += 'set_source_files_properties("'+str(EXP/'adapter/whole_transfer_vm.c')+'" PROPERTIES COMPILE_OPTIONS "-include;'+str(EXP/'tests/whole_transfer_vm_hooks.h')+'")\n'
            extra_cmake += 'set_source_files_properties("'+str(EXP/'tests/whole_audren_kernel_probe.c')+'" PROPERTIES COMPILE_OPTIONS "-Wno-unused-function;-Wno-unused-variable;-Wno-format")\n'

        source.joinpath('CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.24)
project(owned_whole_audren LANGUAGES C CXX OBJC)
set(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE ON CACHE BOOL "" FORCE)
set(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS ON CACHE BOOL "" FORCE)
add_subdirectory("@NATIVE@" native)
add_executable(owned_whole_audren "@PROBE@" @SOURCES@)
target_include_directories(owned_whole_audren PRIVATE "@ADAPTER@")
target_compile_features(owned_whole_audren PRIVATE cxx_std_23)
target_compile_options(owned_whole_audren PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
target_link_options(owned_whole_audren PRIVATE -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
@BASELINE@
target_link_libraries(owned_whole_audren PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>" "-framework Security")
'''.replace('@NATIVE@',str(AUDIO/'native')).replace('@PROBE@',str(EXP/'tests/whole_audren_probe.cpp'))
            .replace('@ADAPTER@',str(EXP/'adapter')).replace('@SOURCES@',' '.join('"'+str(x)+'"' for x in extra_sources))
            .replace('@BASELINE@','target_compile_definitions(owned_whole_audren PRIVATE BOTW_WHOLE_AUDREN_BASELINE=1)' if baseline else extra_cmake))
        checked(['cmake','-S',source,'-B',cls.build,'-DCMAKE_BUILD_TYPE=Debug'],timeout=90)
        checked(['cmake','--build',cls.build,'--target','owned_whole_audren','--parallel','3'],timeout=300)

    def probe(self, mode):
        checked([self.build/'owned_whole_audren',mode],timeout=30,
                env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})

    def test_native_transfer_registration_and_owned_lifetime(self): self.probe('protocol')
    def test_mandatory_real_identity_failure_producer_and_diagnostics_gate(self): self.probe('configuration')
    def test_only_exact_current_process_pseudo_is_accepted(self): self.probe('process')
    def test_one_remaining_kernel_slot_cannot_partially_publish_event_or_session(self): self.probe('capacity')
    def test_in_flight_open_keeps_binding_and_service_context_alive(self): self.probe('busy')
    def test_guest_transfer_close_does_not_invalidate_the_native_strong_lease(self): self.probe('lease')
    def test_real_alias_unmap_failure_keeps_event_read_and_owner_until_checked_retry(self): self.probe('release-retry')
    def test_registered_transfer_does_not_fabricate_a_normal_heap_voice_pool(self): self.probe('missing-provider')
    def test_rebinding_and_wrong_service_owner_cannot_retire_the_live_context(self): self.probe('ownership')


if __name__=='__main__': unittest.main()
