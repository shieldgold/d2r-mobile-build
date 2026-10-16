"""Actual private TSD ownership export; no guest instructions or guest reads."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'


class WholeThreadMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-whole-thread-memory-')
        cls.addClassCleanup(cls.scratch.cleanup)
        work = Path(cls.scratch.name)
        shutil.copytree(COMPONENT / 'upstream/dm', work / 'dm')
        for name in ('base-v0-heap.patch', 'current-thread-ownership.patch'):
            result = subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(COMPONENT / 'patches' / name)],
                cwd=work, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        source = (work / 'dm/host/thread.c').read_text()
        start = source.index('int botw_whole_current_thread_memory(')
        body = source[start:]
        cls.binary = work / 'probe'
        probe = work / 'probe.c'
        probe.write_text('''#include <pthread.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#define TLS_SIZE 8192
#define TLR_SIZE 0x200
#define THREAD_OBJ_SIZE 0x400
static pthread_key_t g_keyown;
static _Thread_local int g_own_ready;
''' + body + '''
static void *other(void *arg){
    uint64_t *addresses=arg;
    return (void*)(uintptr_t)(botw_whole_current_thread_memory(addresses[0],1) ||
        botw_whole_current_thread_memory(addresses[1],1));
}
int main(int argc,char **argv){
    if(argc!=2)return 2;
    if(pthread_key_create(&g_keyown,NULL))return 3;
    void *own[2]={malloc(TLS_SIZE),malloc(TLR_SIZE+THREAD_OBJ_SIZE)};
    if(!own[0]||!own[1]||pthread_setspecific(g_keyown,own))return 4;
    uint64_t tls=(uintptr_t)own[0],tlr=(uintptr_t)own[1];
    if(!strcmp(argv[1],"unready")){int r=botw_whole_current_thread_memory(tls,1);pthread_setspecific(g_keyown,NULL);free(own[0]);free(own[1]);return r?91:0;}
    g_own_ready=1;
    int result=0;
    if(!strcmp(argv[1],"tls"))result=!botw_whole_current_thread_memory(tls,TLS_SIZE);
    else if(!strcmp(argv[1],"tlr"))result=!botw_whole_current_thread_memory(tlr,TLR_SIZE+THREAD_OBJ_SIZE);
    else if(!strcmp(argv[1],"bounds"))result=botw_whole_current_thread_memory(tls+TLS_SIZE-1,2)||botw_whole_current_thread_memory(tlr+TLR_SIZE+THREAD_OBJ_SIZE,1);
    else if(!strcmp(argv[1],"invalid"))result=botw_whole_current_thread_memory(0,1)||botw_whole_current_thread_memory(tls,0)||botw_whole_current_thread_memory(UINT64_MAX-1,8);
    else if(!strcmp(argv[1],"unknown")){char local;result=botw_whole_current_thread_memory((uintptr_t)&local,1);}
    else if(!strcmp(argv[1],"cleared")){pthread_setspecific(g_keyown,NULL);result=botw_whole_current_thread_memory(tls,1);}
    else if(!strcmp(argv[1],"thread")){uint64_t a[]={tls,tlr};pthread_t t;void *r;if(pthread_create(&t,NULL,other,a)||pthread_join(t,&r))return 5;result=(uintptr_t)r;}
    else if(!strcmp(argv[1],"rewritten-key")){pthread_key_t current;void *foreign=malloc(TLS_SIZE);if(!foreign||pthread_key_create(&current,NULL)||pthread_setspecific(current,foreign))return 6;result=botw_whole_current_thread_memory((uintptr_t)foreign,1)||!botw_whole_current_thread_memory(tls,TLS_SIZE);pthread_setspecific(current,NULL);pthread_key_delete(current);free(foreign);}
    else result=1;
    pthread_setspecific(g_keyown,NULL);free(own[0]);free(own[1]);return result?92:0;
}
''')
        command = ['clang', '-std=gnu11', '-pthread', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(probe), '-o', str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_uninitialized_private_key_never_borrows_another_keys_value(self): self.case('unready')
    def test_actual_tls_allocation_full_span(self): self.case('tls')
    def test_actual_tlr_and_thread_object_allocation_full_span(self): self.case('tlr')
    def test_end_crossing_and_adjacent_bytes_are_rejected(self): self.case('bounds')
    def test_null_empty_and_overflow_are_rejected(self): self.case('invalid')
    def test_unknown_local_memory_is_not_inferred_owned(self): self.case('unknown')
    def test_cleared_tsd_owner_is_rejected(self): self.case('cleared')
    def test_another_threads_raw_tsd_allocations_are_not_borrowed(self): self.case('thread')
    def test_rewritten_current_key_does_not_invent_native_allocation_ownership(self): self.case('rewritten-key')


if __name__ == '__main__':
    unittest.main()
