"""Complete owned CAPI voice path with actual normal/transfer kernel aliases."""
from pathlib import Path
import json
import os
import platform
import subprocess
import tempfile
import unittest
import test_botw_whole_heap_lease as heap_fixtures

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
AUDIO=ROOT/'experiments/botw_audio_renderer'


def checked(command,**kwargs):
    result=subprocess.run([str(v) for v in command],capture_output=True,text=True,**kwargs)
    if result.returncode:raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


@unittest.skipUnless(platform.system()=='Darwin','actual shared Darwin VM and original PCM mock AU require Darwin')
class WholeHeapVoice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class PrivateHeapStaging(heap_fixtures.WholeHeapLease):pass
        PrivateHeapStaging.setUpClass()
        cls.addClassCleanup(PrivateHeapStaging.doClassCleanups)
        work=PrivateHeapStaging.binary.parent
        fixture=(EXP/'tests/whole_heap_lease_probe.c').read_text()
        original='int main(int argc,char **argv)'
        if fixture.count(original)!=1:raise AssertionError('frozen fixture entry point changed')
        (work/'owned_normal_fixture.inc').write_text(fixture.replace(original,'int frozen_normal_fixture_main(int argc,char **argv)'))
        if os.environ.get('BOTW_WHOLE_HEAP_VOICE_BUILD'):
            source=Path(os.environ['BOTW_WHOLE_HEAP_VOICE_BUILD']).resolve();source.mkdir(parents=True,exist_ok=True)
        else:
            cls.temp=tempfile.TemporaryDirectory(prefix='botw-whole-heap-voice-');cls.addClassCleanup(cls.temp.cleanup)
            source=Path(cls.temp.name)
        cls.build=source/'build'
        sources=[EXP/'tests/whole_heap_voice_probe.cpp',EXP/'tests/whole_heap_voice_kernel.c',
                 EXP/'adapter/whole_transfer_vm.c',EXP/'adapter/base_v0_heap.c',
                 EXP/'adapter/whole_heap_lease.c',EXP/'adapter/whole_heap_provider.c']
        includes=[EXP/'adapter',EXP/'tests',work,work/'dm/host',work/'dm/gfx/nvn']
        cmake='''cmake_minimum_required(VERSION 3.24)
project(owned_whole_heap_voice LANGUAGES C CXX OBJC)
set(BOTW_AUDIO_RENDERER_OWNED_UPDATE_FIXTURE ON CACHE BOOL "" FORCE)
set(BOTW_AUDIO_RENDERER_NATIVE_SANITIZERS ON CACHE BOOL "" FORCE)
add_subdirectory("@NATIVE@" native)
add_executable(owned_whole_heap_voice @SOURCES@)
target_include_directories(owned_whole_heap_voice PRIVATE @INCLUDES@)
target_compile_features(owned_whole_heap_voice PRIVATE cxx_std_23)
target_compile_options(owned_whole_heap_voice PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
target_link_options(owned_whole_heap_voice PRIVATE -fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all)
set_source_files_properties("@KERNEL@" PROPERTIES COMPILE_OPTIONS "-Wno-unused-function;-Wno-unused-variable;-Wno-format")
# The unchanged upstream Result specialization has two unused formal arguments.
set_source_files_properties("@PROBE@" PROPERTIES COMPILE_OPTIONS "-Wno-unused-parameter")
set_source_files_properties("@VM@" PROPERTIES COMPILE_OPTIONS "-include;@HOOKS@")
target_link_libraries(owned_whole_heap_voice PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,botw_audio_renderer_engine>")
'''
        quote=lambda values:' '.join('"'+str(v)+'"' for v in values)
        cmake=cmake.replace('@NATIVE@',str(AUDIO/'native')).replace('@SOURCES@',quote(sources)).replace('@INCLUDES@',quote(includes))
        cmake=cmake.replace('@KERNEL@',str(EXP/'tests/whole_heap_voice_kernel.c')).replace('@VM@',str(EXP/'adapter/whole_transfer_vm.c')).replace('@HOOKS@',str(EXP/'tests/whole_transfer_vm_hooks.h')).replace('@PROBE@',str(EXP/'tests/whole_heap_voice_probe.cpp'))
        (source/'CMakeLists.txt').write_text(cmake)
        checked(['cmake','-S',source,'-B',cls.build,'-DCMAKE_BUILD_TYPE=Debug'],timeout=90)
        checked(['cmake','--build',cls.build,'--target','owned_whole_heap_voice','--parallel','3'],timeout=300)

    def run_probe(self,mode):
        return subprocess.run([str(self.build/'owned_whole_heap_voice'),mode],capture_output=True,text=True,timeout=30,
            env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})

    def proof(self,mode):
        result=self.run_probe('missing-provider' if os.environ.get('BOTW_WHOLE_HEAP_VOICE_BASELINE')=='1' else mode)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        value=json.loads(result.stdout.strip())
        self.assertGreater(value['nonzero'],200)
        self.assertEqual((value['source_rate'],value['pcm_rate'],value['callback_frames']),(32000,48000,240))
        self.assertFalse(value['normal_pool_registered'])
        self.assertTrue(value['normal_kernel_pin_observed'])
        self.assertTrue(value['transfer_handle_closed_before_open'])
        self.assertEqual(value['cleanup_retry'],mode=='cleanup-retry')

    def test_normal_heap_provider_drives_complete_nonzero_voice_mix_resample_pcm(self):self.proof('voice')
    def test_failed_actual_normal_alias_cleanup_retains_session_until_retry(self):self.proof('cleanup-retry')
    def test_registered_transfer_alone_never_fabricates_a_normal_voice_pin(self):
        result=self.run_probe('missing-provider')
        self.assertEqual(result.returncode,91,result.stdout+result.stderr)
        self.assertIn('owned_heap_voice_maps()==2',result.stderr)


if __name__=='__main__':unittest.main()
