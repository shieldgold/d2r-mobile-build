"""Actual mounted runtime setup publishes an explicit, resource-audited language."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

import test_botw_romfs as romfs_fixture

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#include "RUNTIME_SOURCE"
#include <assert.h>

static uint8_t *ipc;
static void observe(const char *message) { puts(message); }
static uint32_t read32(const void *p) { uint32_t n; memcpy(&n,p,4); return n; }
static void write32(void *p,uint32_t n) { memcpy(p,&n,4); }
static size_t reply_body(void) {
    size_t raw=8;
    if (read32(ipc+4)>>31) {
        uint32_t special=read32(ipc+raw); raw+=4;
        if (special&1) raw+=8;
        raw+=4*((special>>1&15)+(special>>5&15));
    }
    assert(!(read32(ipc)>>16));
    return (raw+15)&~(size_t)15;
}
static int send(uint32_t handle,unsigned id,int pid,int copy,const void *payload,size_t bytes) {
    memset(ipc,0,256);write32(ipc,4);
    size_t raw=8;
    if (pid||copy) {
        write32(ipc+8,(pid?1u:0u)|(copy?2u:0u));raw+=4;
        if (pid) { uint64_t caller=getpid();memcpy(ipc+raw,&caller,8);raw+=8; }
        if (copy) { write32(ipc+raw,UINT32_C(0xffff8001));raw+=4; }
    }
    size_t at=(raw+15)&~(size_t)15;
    memcpy(ipc+at,"SFCI",4);write32(ipc+at+8,id);
    if (bytes) memcpy(ipc+at+16,payload,bytes);
    size_t raw_bytes=(32+bytes+3)&~(size_t)3;
    write32(ipc+4,(uint32_t)(raw_bytes/4)|((pid||copy)?UINT32_C(0x80000000):0));
    char error[512];
    int result=botw_services_send(state.services,handle,ipc,256,error,sizeof error);
    if (result) fprintf(stderr,"%s\n",error);
    return result;
}
static uint32_t moved(void) {
    assert(read32(ipc+4)>>31);
    assert(read32(ipc+8)==1u<<5);
    assert(!memcmp(ipc+reply_body(),"SFCO",4));
    return read32(ipc+12);
}
static uint32_t application_functions(void) {
    char error[512];uint32_t sm;uint64_t zero=0;
    assert(!botw_services_connect(state.services,"sm:",3,&sm,error,sizeof error));
    assert(!send(sm,0,1,0,&zero,8));
    assert(!send(sm,1,0,0,"appletOE",8));uint32_t oe=moved();
    assert(!send(oe,0,1,1,&zero,8));uint32_t proxy=moved();
    assert(!send(proxy,20,0,0,NULL,0));return moved();
}
static void language(int configured,const char expected[8]) {
    uint32_t functions=application_functions();
    if (!configured) {
        /* Failed replies must retain the original request, not expose zero as
         * a language or claim a fallback on a missing/unknown resource set. */
        assert(send(functions,21,0,0,NULL,0)==-1);
        assert(!memcmp(ipc+16,"SFCI",4));return;
    }
    for (unsigned i=0;i<2;i++) {
        assert(!send(functions,21,0,0,NULL,0));
        size_t body=reply_body();assert(!memcmp(ipc+body,"SFCO",4));
        assert(!read32(ipc+body+8));
        assert(!memcmp(ipc+body+16,expected,8));
        assert(!(read32(ipc+4)>>31));
    }
}
int main(int argc,char **argv) {
    assert(argc==3);state.owner=pthread_self();state.event=observe;
    state.kernel=botw_kernel_create(NULL,NULL);assert(state.kernel);
    char error[512];size_t bytes=0x200000;
    assert(!botw_kernel_configure_memory(state.kernel,bytes,bytes,bytes,bytes,0,error,sizeof error));
    uint64_t registers[32]={0,bytes};
    assert(botw_kernel_dispatch(state.kernel,1,registers,error,sizeof error)==BOTW_KERNEL_HANDLED&&!registers[0]);
    ipc=(uint8_t *)(uintptr_t)registers[1];
    state.services=botw_services_create(services_log,services_memory,NULL);assert(state.services);
    botw_services_set_memory_write_preparer(state.services,services_prepare_write,NULL);
    const botw_services_kernel_api api={.opaque=state.kernel,.create_event=kernel_event_create,
        .duplicate_handle=kernel_duplicate,.signal_event=kernel_signal,.clear_event=kernel_clear,
        .close_handle=kernel_close,.is_process_handle=kernel_process};
    botw_services_set_kernel_api(state.services,&api);
    assert(!botw_services_set_applet_state(state.services,1,1,1,0,error,sizeof error));
    int conflict=!strcmp(argv[2],"conflict");
    if (conflict) assert(!botw_services_set_application_language(state.services,"ja","test-conflict",error,sizeof error));
    if (sigsetjmp(state.recovery,1)) {
        assert(conflict);language(1,"ja\0\0\0\0\0\0");
        assert(!cleanup());puts("runtime_language_registration_refusal_preserved=true");return 0;
    }
    configure_resource_backends(argv[1]);
    assert(!conflict&&"runtime ignored application-language registration failure");
    language(!strcmp(argv[2],"ready"),"en-US\0\0\0");
    assert(!cleanup());puts("runtime_language_transport_verified=true");return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'actual resource runtime uses Apple ARM64')
class RuntimeLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-runtime-language-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'language.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = Path(cls.build.name) / 'language'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                                 '-framework', 'Security', *(str(p) for p in sources),
                                 '-o', str(cls.exe)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, root, mode):
        result = subprocess.run([str(self.exe), str(root), mode], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_mounted_base_version_publishes_exact_explicit_language_to_real_ipc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'botw-romfs.bin').write_bytes(romfs_fixture.fixture([('/System/Version.txt', b'1.0.0')])[0])
            result = self.run_case(root, 'ready')
            self.assertIn('language=en-US source=fixed-base-v0:USen:explicit-fallback', result.stdout)
            self.assertIn('device_locale_backend=false', result.stdout)
            self.assertIn('runtime_language_transport_verified=true', result.stdout)

    def test_missing_romfs_does_not_publish_a_resource_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);result = self.run_case(root, 'missing')
            self.assertIn('application_language_configured=false reason=missing_romfs', result.stdout)
            self.assertNotIn('language=en-US', result.stdout)
            self.assertFalse((root / 'botw-profile').exists())

    def test_unknown_version_stays_unconfigured_instead_of_claiming_base_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'botw-romfs.bin').write_bytes(romfs_fixture.fixture([('/System/Version.txt', b'9.9.9')])[0])
            result = self.run_case(root, 'unknown')
            self.assertIn('application_language_configured=false reason=unsupported_resource_version', result.stdout)
            self.assertNotIn('language=en-US', result.stdout)

    def test_service_registration_refusal_stops_setup_and_retains_original_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'botw-romfs.bin').write_bytes(romfs_fixture.fixture([('/System/Version.txt', b'1.0.0')])[0])
            result = self.run_case(root, 'conflict')
            self.assertIn('configure explicit application language:', result.stdout)
            self.assertIn('runtime_language_registration_refusal_preserved=true', result.stdout)
            self.assertFalse((root / 'botw-profile').exists())


if __name__ == '__main__':
    unittest.main()
