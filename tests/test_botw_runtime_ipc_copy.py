"""Real public Mach copies registered for IPC without an AudioUnit build."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define botw_services_set_memory_copiers capture_memory_copiers
#include "RUNTIME_SOURCE"
#undef botw_services_set_memory_copiers
#include <assert.h>
static botw_services_read_guest_fn read_guest;
static botw_services_write_guest_fn write_guest;
static void *copy_opaque;
void capture_memory_copiers(botw_services *services, botw_services_read_guest_fn reader,
        botw_services_write_guest_fn writer, void *opaque) {
    assert(services == state.services); read_guest=reader; write_guest=writer; copy_opaque=opaque;
}
static unsigned char *memory;
static size_t page;
static unsigned prepares;
static char diagnostic[2048];
static void collect_report(const char *message) { snprintf(diagnostic,sizeof diagnostic,"%s",message); }
static void unexpected_signal(int number) { (void)number; _Exit(66); }
static int tracking_fault_test(void *address, int signal_number, void *context) {
    (void)address; (void)signal_number; (void)context; return 0;
}
static int tracking_prepare_test(void *address, size_t bytes, char *error, size_t capacity) {
    (void)error; (void)capacity;
    assert(address == memory && bytes == 64); prepares++;
    assert(!mprotect(memory, page, PROT_READ|PROT_WRITE)); return 0;
}
static void setup(const char *documents) {
    state.owner=pthread_self();
    state.kernel=botw_kernel_create(NULL,NULL); assert(state.kernel);
    state.services=botw_services_create(services_log,services_memory,NULL); assert(state.services);
    configure_resource_backends(documents);
    assert(read_guest && write_guest && "IPC lacks production protected memory copiers");
    page=(size_t)getpagesize();
    memory=mmap(NULL,2*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(memory!=MAP_FAILED); memset(memory,0x61,2*page);
    state.modules[0]=(Module){.name="main",.base=memory,.size=2*page,.patch_off=2*page};
}
static void copies(void) {
    char error[512]; unsigned char data[64];
    assert(!read_guest(copy_opaque,(uintptr_t)memory,data,sizeof data,error,sizeof error));
    for (unsigned i=0;i<sizeof data;i++) assert(data[i]==0x61);
    memset(data,0x37,sizeof data);
    assert(!write_guest(copy_opaque,(uintptr_t)memory,data,sizeof data,error,sizeof error));
    assert(!memcmp(memory,data,sizeof data));
    assert(!mprotect(memory+page,page,PROT_READ));
    assert(write_guest(copy_opaque,(uintptr_t)memory+page,data,sizeof data,error,sizeof error)==-1);
    assert(strstr(error,"protected guest write"));
    for (unsigned i=0;i<sizeof data;i++) assert(memory[page+i]==0x61);
    assert(!mprotect(memory+page,page,PROT_NONE));
    assert(read_guest(copy_opaque,(uintptr_t)memory+page,data,sizeof data,error,sizeof error)==-1);
    assert(strstr(error,"protected guest read"));
    assert(read_guest(copy_opaque,(uintptr_t)memory+page-16,data,32,error,sizeof error)==-1);
    memset(data,0x37,sizeof data);
    assert(write_guest(copy_opaque,(uintptr_t)memory+page-16,data,32,error,sizeof error)==-1);
    for (unsigned i=0;i<16;i++) assert(memory[page-16+i]==0x61);
    assert(!mprotect(memory+page,page,PROT_READ|PROT_WRITE));
    assert(mmap(memory+page,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0)==memory+page);
    memset(memory+page,0x61,page);
    memset(data,0x37,sizeof data);
    assert(!write_guest(copy_opaque,(uintptr_t)memory+page-16,data,32,error,sizeof error));
    assert(!memcmp(memory+page-16,data,32));
    assert(memory[page-17]==0x61 && memory[page+16]==0x61);
}
static void ownership(void) {
    char error[512]; unsigned char data[64]; memset(data,0x37,sizeof data);
    state.modules[0].text_end=page; /* Physically writable, logically guest code. */
    assert(write_guest(copy_opaque,(uintptr_t)memory,data,sizeof data,error,sizeof error)==-1);
    assert(strstr(error,"guest write range"));
    for (unsigned i=0;i<sizeof data;i++) assert(memory[i]==0x61);
    assert(!read_guest(copy_opaque,(uintptr_t)memory,data,sizeof data,error,sizeof error));
    void *foreign=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(foreign!=MAP_FAILED);
    assert(read_guest(copy_opaque,(uintptr_t)foreign,data,sizeof data,error,sizeof error)==-1);
    assert(write_guest(copy_opaque,(uintptr_t)foreign,data,sizeof data,error,sizeof error)==-1);
    assert(read_guest(copy_opaque,UINT64_MAX-15,data,32,error,sizeof error)==-1);
    assert(write_guest(copy_opaque,(uintptr_t)memory+page,data,SIZE_MAX,error,sizeof error)==-1);
    assert(!read_guest(copy_opaque,0,NULL,0,error,sizeof error));
    assert(!write_guest(copy_opaque,0,NULL,0,error,sizeof error));
    assert(!munmap(foreign,page));
}
static void prepare_once(void) {
    char error[512]; unsigned char data[64]; memset(data,0x37,sizeof data);
    assert(!botw_host_register_memory_tracking(tracking_fault_test,tracking_prepare_test));
    assert(!mprotect(memory,page,PROT_READ));
    /* Services performs preparation on the original guest output, exactly once.
     * The copy callback must not independently unprotect or repeat that step. */
    assert(write_guest(copy_opaque,(uintptr_t)memory,data,sizeof data,error,sizeof error)==-1);
    assert(!prepares);
    assert(!services_prepare_write(NULL,(uintptr_t)memory,sizeof data,error,sizeof error));
    assert(prepares==1);
    assert(!write_guest(copy_opaque,(uintptr_t)memory,data,sizeof data,error,sizeof error));
    assert(prepares==1 && !memcmp(memory,data,sizeof data));
}
static void ipc_gate(void) {
    signal(SIGBUS,unexpected_signal); signal(SIGSEGV,unexpected_signal);
    assert(!mprotect(memory+page,page,PROT_NONE));
    assert(services_memory(NULL,(uintptr_t)memory+page,256,1));
    state.active=1; state.event=collect_report;
    assert(!pthread_key_create(&state.keys[2],NULL));
    assert(!pthread_setspecific(state.keys[2],memory+page));
    for (unsigned number=0x21;number<=0x22;number++) {
        state.guest_executing=1; diagnostic[0]=0;
        state.gates[0]=(Gate){.ret=0x1234,.arg=number}; state.gate_count=1;
        uint64_t registers[32]={(uintptr_t)memory+page,256,0x10000};
        if (!sigsetjmp(state.recovery,1)) {
            botw_gate_dispatch(0x1234,registers); assert(!"unexpected IPC success");
        }
        assert(strstr(diagnostic,"protected guest read") && !state.guest_executing);
    }
    assert(!pthread_key_delete(state.keys[2]));
    assert(!mprotect(memory+page,page,PROT_READ|PROT_WRITE));
}
static void prepare_span(void) {
    char error[512];
    assert(!mprotect(memory+page,page,PROT_NONE));
    assert(services_memory(NULL,(uintptr_t)memory+page-64,128,1));
    assert(services_prepare_write(NULL,(uintptr_t)memory+page-64,128,error,sizeof error)==-1);
    for (unsigned i=0;i<64;i++) assert(memory[page-64+i]==0x61);
    assert(!mprotect(memory+page,page,PROT_READ));
    assert(services_prepare_write(NULL,(uintptr_t)memory+page-64,128,error,sizeof error)==-1);
    assert(!munmap(memory+page,page));
    assert(services_prepare_write(NULL,(uintptr_t)memory+page-64,128,error,sizeof error)==-1);
    assert(mmap(memory+page,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0)==memory+page);
    assert(!services_prepare_write(NULL,(uintptr_t)memory+page-64,128,error,sizeof error));
}
int main(int argc,char **argv) {
    assert(argc==3); setup(argv[1]);
    if (!strcmp(argv[2],"copies")) copies();
    else if (!strcmp(argv[2],"ownership")) ownership();
    else if (!strcmp(argv[2],"gate")) ipc_gate();
    else if (!strcmp(argv[2],"span")) prepare_span();
    else { assert(!strcmp(argv[2],"prepare")); prepare_once(); }
    assert(!cleanup()); puts("IPC_RUNTIME_COPY_VERIFIED"); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'public Mach guest copies require Apple ARM64')
class RuntimeIpcCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-ipc-runtime-')
        cls.addClassCleanup(cls.build.cleanup)
        folder = Path(cls.build.name)
        driver = folder / 'ipc-copy.c'
        driver.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = folder / 'ipc-copy'
        sources = [driver, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                                 '-framework', 'Security', *(str(s) for s in sources), '-o', str(cls.exe)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def check(self, mode):
        with tempfile.TemporaryDirectory(prefix='botw-ipc-documents-') as documents:
            result = subprocess.run([str(self.exe), documents, mode], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('IPC_RUNTIME_COPY_VERIFIED', result.stdout)

    def test_registered_copies_handle_real_ro_none_and_cross_page_failures(self):
        self.check('copies')

    def test_copies_respect_logical_guest_ownership_and_reject_overflow(self):
        self.check('ownership')

    def test_write_copy_does_not_repeat_original_guest_preparation(self):
        self.check('prepare')

    def test_svc_entry_does_not_dereference_an_inaccessible_ipc_header(self):
        self.check('gate')

    def test_output_preparation_rejects_a_ro_none_or_unmapped_tail(self):
        self.check('span')
