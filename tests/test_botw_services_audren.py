"""Owned CPU HIPC audren contracts; no guest, GPU, or audio device execution."""
from pathlib import Path
import os
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_audio_renderer'


def checked(command, **kwargs):
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


@unittest.skipUnless(platform.system() == 'Darwin', 'actual Apple PCM and owned mock-AU fixture require Darwin')
class ServicesAudrenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('BOTW_SERVICES_AUDREN_BUILD'):
            source = Path(os.environ['BOTW_SERVICES_AUDREN_BUILD']).resolve()
            source.mkdir(parents=True, exist_ok=True)
        else:
            cls.scratch = tempfile.TemporaryDirectory(prefix='botw-owned-services-audren-')
            cls.addClassCleanup(cls.scratch.cleanup)
            source = Path(cls.scratch.name)
        cls.build = source / 'build'
        native_sources = ['native/botw_host/services.c', 'native/botw_resources/romfs.c',
                          'native/botw_resources/savefs.c', 'native/botw_resources/local_user.c',
                          'native/botw_input/input.c']
        source.joinpath('CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.24)
project(owned_services_audren LANGUAGES C CXX OBJC)
set(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE ON CACHE BOOL "" FORCE)
set(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS ON CACHE BOOL "" FORCE)
add_subdirectory("@NATIVE@" native)
add_executable(owned_services_audren "@PROBE@" @SOURCES@)
target_include_directories(owned_services_audren PRIVATE "@HOST@")
target_compile_features(owned_services_audren PRIVATE cxx_std_20)
target_compile_options(owned_services_audren PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined,float-cast-overflow -fno-omit-frame-pointer)
target_link_options(owned_services_audren PRIVATE -fsanitize=address,undefined,float-cast-overflow)
target_link_libraries(owned_services_audren PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>" "-framework Security")
'''.replace('@NATIVE@', str(COMPONENT / 'native')).replace('@PROBE@', str(ROOT / 'experiments/botw_upstream_host/tests/services_audren_probe.cpp'))
            .replace('@SOURCES@', ' '.join('"'+str(ROOT / p)+'"' for p in native_sources))
            .replace('@HOST@', str(ROOT / 'native/botw_host')))
        checked(['cmake', '-S', source, '-B', cls.build, '-DCMAKE_BUILD_TYPE=Debug'], timeout=90)
        checked(['cmake', '--build', cls.build, '--target', 'owned_services_audren', '--parallel', '3'], timeout=300)

    def run_mode(self, mode):
        checked([self.build / 'owned_services_audren', mode], timeout=30,
                env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
                     'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=0'})

    def test_real_sm_open_wire_queries_update_and_controls(self): self.run_mode('protocol')
    def test_unbound_audio_is_not_a_successful_service(self): self.run_mode('unbound')
    def test_close_failure_keeps_owner_and_handle_for_retry(self): self.run_mode('close-retry')
    def test_domain_close_failure_preserves_shared_object(self): self.run_mode('domain-close-retry')
    def test_cmif_close_failure_keeps_server_endpoint(self): self.run_mode('cmif-close-retry')
    def test_clone_last_reference_closes_the_real_renderer_once(self): self.run_mode('clone')
    def test_domain_copy_owns_the_same_backend_until_last_close(self): self.run_mode('domain-copy')
    def test_unlocked_backend_call_borrows_handle_arena_and_session(self): self.run_mode('borrow')
    def test_domain_copy_backend_borrow_prevents_cross_session_release(self): self.run_mode('domain-borrow')
    def test_partial_open_retains_native_owner_for_checked_destroy_retry(self): self.run_mode('partial-open')
    def test_failed_handle_reservation_never_calls_native_open(self): self.run_mode('reserve-failure')
    def test_late_transport_failure_retries_without_closing_native_owner_twice(self): self.run_mode('transport-retry')
    def test_unknown_shapes_and_wrong_result_contract_preserve_ipc(self): self.run_mode('schemas')
    def test_only_second_performance_b_accepts_empty_span_without_inspection(self): self.run_mode('empty-perf')
    def test_actual_renderer_nintendo_error_is_preserved_without_output_publication(self): self.run_mode('renderer-result')
    def test_open_borrows_its_shared_manager_as_well_as_reserved_renderer(self): self.run_mode('open-borrow')


if __name__ == '__main__':
    unittest.main()
