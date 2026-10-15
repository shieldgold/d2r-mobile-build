"""Real runtime cleanup retains owned VM until checked graphics retirement.

Only the graphics provider is simulated. The fixture uses the production
cleanup, kernel reservation/heap, module mappings and public Mach reads; it
never creates a Metal device or reads game memory.
"""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#include "RUNTIME_SOURCE"
#include <assert.h>

static botw_kernel *original_kernel;
static unsigned char *module;
static uint64_t heap;
static unsigned calls;
static int shutdown_result;
static int empty_error;
static char message[2048];
static void collect(const char *text) { snprintf(message,sizeof message,"%s",text); }
static int read_byte(uint64_t address,unsigned char *byte) {
    vm_size_t got=0;
    return vm_read_overwrite(mach_task_self(),(vm_address_t)address,1,
        (vm_address_t)(uintptr_t)byte,&got)==KERN_SUCCESS && got==1;
}
static void check_borrowed_vm(void) {
    unsigned char byte=0;
    assert(state.kernel==original_kernel && state.graphics_started);
    assert(botw_kernel_memory(state.kernel,heap,PAGE,1));
    assert(read_byte(heap,&byte) && byte==0x51);
    assert(read_byte((uintptr_t)module,&byte) && byte==0x62);
    assert(state.modules[0].base==module);
    assert(state.graphics_workspace==(void*)(uintptr_t)heap);
    assert(state.graphics_workspace_size==PAGE);
}
/* This typed adapter lets the regression execute against the previous void
 * provider too: it returned with a still-borrowed resource and cleanup then
 * incorrectly destroyed its memory. No incompatible function call is used. */
static void legacy_provider(void) { calls++; check_borrowed_vm(); }
static int checked_provider(char *error,size_t capacity) {
    calls++; check_borrowed_vm();
    if(shutdown_result!=1 && !empty_error)
        snprintf(error,capacity,"synthetic borrowed Metal resource still retained");
    return shutdown_result;
}
#define PROVIDER _Generic(graphics_backend_shutdown, \
    void (*)(void):legacy_provider, int (*)(char*,size_t):checked_provider)

static void setup(int started) {
    memset(&state,0,sizeof state); state.owner=pthread_self(); state.event=collect;
    original_kernel=state.kernel=botw_kernel_create(NULL,NULL); assert(state.kernel);
    char error[512]; const size_t unit=0x200000;
    assert(!botw_kernel_configure_address_space(state.kernel,39,error,sizeof error));
    assert(!botw_kernel_configure_memory(state.kernel,2*unit,unit,unit,2*unit,0,error,sizeof error));
    uint64_t registers[31]={0,unit};
    assert(botw_kernel_dispatch(state.kernel,1,registers,error,sizeof error)==BOTW_KERNEL_HANDLED);
    assert(!registers[0]); heap=registers[1]; assert(heap);
    memset((void*)(uintptr_t)heap,0x51,PAGE);
    module=mmap(NULL,PAGE,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(module!=MAP_FAILED); memset(module,0x62,PAGE);
    state.modules[0]=(Module){.name="synthetic",.base=module,.size=PAGE,.patch_off=PAGE};
    state.graphics_started=started; state.graphics_workspace=(void*)(uintptr_t)heap;
    state.graphics_workspace_size=PAGE; graphics_backend_shutdown=PROVIDER;
    assert(!pthread_mutex_lock(&run_lock));
}
static void retained(void) {
    check_borrowed_vm();
    assert(pthread_mutex_trylock(&run_lock)==EBUSY);
    /* The public entry cannot reset this retained state or start another run. */
    assert(botw_host_run_mode("","",NULL,BOTW_HOST_LOAD_ONLY)==2);
    assert(state.kernel==original_kernel && state.graphics_started);
}
static void finished(unsigned expected_calls) {
    assert(calls==expected_calls && !state.kernel && !state.graphics_started);
    assert(!state.graphics_workspace && !state.graphics_workspace_size);
    unsigned char byte;
    assert(!read_byte((uintptr_t)module,&byte));
    assert(!read_byte(heap,&byte));
    assert(!pthread_mutex_unlock(&run_lock));
}
int main(int argc,char **argv) {
    assert(argc==2); alarm(8);
    if(!strcmp(argv[1],"not-started")) {
        setup(0); shutdown_result=0; assert(!cleanup()); finished(0);
    } else if(!strcmp(argv[1],"success")) {
        setup(1); shutdown_result=1; assert(!cleanup()); finished(1);
    } else {
        setup(1);
        if(!strcmp(argv[1],"missing")) graphics_backend_shutdown=NULL;
        else if(!strcmp(argv[1],"invalid-status")) shutdown_result=2;
        else if(!strcmp(argv[1],"empty-error")) empty_error=1;
        else assert(!strcmp(argv[1],"retained"));
        if(sigsetjmp(state.recovery,1)) assert(!"cleanup must return, not jump into run recovery");
        assert(cleanup()==-1);
        assert(calls==(!strcmp(argv[1],"missing")?0u:1u));
        assert(strstr(message,"retaining") && strstr(message,"graphics"));
        retained();
        if(!strcmp(argv[1],"retained")) {
            /* A persistent failure returns once per explicit attempt. */
            assert(cleanup()==-1 && calls==2); retained();
        }
        graphics_backend_shutdown=PROVIDER; shutdown_result=1; empty_error=0;
        unsigned expected=calls+1; assert(!cleanup()); finished(expected);
    }
    puts("RUNTIME_CHECKED_SHUTDOWN_VERIFIED"); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'production runtime and public Mach VM require Apple ARM64')
class RuntimeShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-runtime-shutdown-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'shutdown.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.executable = folder / 'shutdown'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('services.c', 'kernel.c', 'hipc_address.c', 'address_space.c', 'entry.S')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name for name in
                   ('romfs.c', 'savefs.c', 'local_user.c'))]
        command = ['xcrun', 'clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                   '-framework', 'Security', *map(str, sources), '-o', str(cls.executable)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def check(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('RUNTIME_CHECKED_SHUTDOWN_VERIFIED', result.stdout)

    def test_failure_returns_once_and_retains_kernel_and_module_vm_until_retry(self): self.check('retained')
    def test_started_backend_without_checked_provider_retains_resources(self): self.check('missing')
    def test_success_retires_graphics_before_owned_vm_is_destroyed(self): self.check('success')
    def test_not_started_does_not_call_provider(self): self.check('not-started')
    def test_missing_diagnostic_still_fails_closed(self): self.check('empty-error')
    def test_only_exact_success_status_permits_releasing_backing(self): self.check('invalid-status')


if __name__ == '__main__': unittest.main()
