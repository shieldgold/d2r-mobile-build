"""Actual Mach copies, guest event calls and runtime audio lifetime wiring.

AudioUnit itself is tested by test_botw_audio; this fixture substitutes only
that boundary so recovery and teardown can be exercised without a sound card.
"""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#define BOTW_WITH_AUDIO 1
#include "RUNTIME_SOURCE"
#include <assert.h>
#include <libkern/OSCacheControl.h>
struct botw_audio { int active; };
static struct botw_audio output;
static botw_audio_callbacks saved;
static int retain_audio;
botw_audio *botw_audio_create(const botw_audio_callbacks *cb, int active, char *e, size_t n) {
    (void)e; (void)n; saved=*cb; output.active=active; return &output;
}
int botw_audio_set_active(botw_audio *a, int active, char *e, size_t n) {
    (void)e; (void)n; assert(a==&output); a->active=active; return 0;
}
int botw_audio_shutdown(botw_audio *a, char *e, size_t n) {
    if (!a) return 0;
    assert(a==&output && state.kernel);
    if (retain_audio) { snprintf(e,n,"fixture AudioUnit not quiescent"); return -1; }
    return 0;
}
int botw_audio_resolve_symbol(const char *m,unsigned i,const char *s,uint64_t v,uint64_t z,
        size_t t,void **f,char *e,size_t n) {
    (void)m;(void)i;(void)s;(void)v;(void)z;(void)t;(void)f;(void)e;(void)n; return 0;
}
static void setup(void) {
    memset(&state,0,sizeof state); state.owner=pthread_self(); state.active=state.guest_executing=1;
    state.kernel=botw_kernel_create(NULL,NULL); assert(state.kernel);
    host_foreground=host_focused=host_audio_session_active=1;
    initialize_audio(); assert(state.audio==&output && output.active);
}
static void copies(void) {
    size_t page=(size_t)getpagesize(), bytes=page*2; char error[512];
    unsigned char *p=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(p!=MAP_FAILED); memset(p,0x61,bytes);
    state.modules[0]=(Module){.name="main",.base=p,.size=bytes,.patch_off=bytes};
    unsigned char data[64]; memset(data,0,sizeof data);
    assert(!saved.read_guest(saved.opaque,(uintptr_t)p,data,sizeof data,error,sizeof error));
    for (unsigned i=0;i<sizeof data;i++) assert(data[i]==0x61);
    memset(data,0x37,sizeof data);
    assert(!saved.write_guest(saved.opaque,(uintptr_t)p,data,sizeof data,error,sizeof error));
    assert(!memcmp(p,data,sizeof data));
    assert(mprotect(p+page,page,PROT_NONE)==0);
    assert(saved.read_guest(saved.opaque,(uintptr_t)p+page,data,sizeof data,error,sizeof error)==-1);
    assert(strstr(error,"protected guest read"));
    assert(saved.write_guest(saved.opaque,(uintptr_t)p+page,data,sizeof data,error,sizeof error)==-1);
    assert(strstr(error,"protected guest write"));
    assert(saved.read_guest(saved.opaque,1,data,sizeof data,error,sizeof error)==-1);
    assert(!cleanup());
}
static void lifecycle(void) {
    botw_host_set_applet_state(1,0); assert(!output.active);
    botw_host_set_applet_state(1,1); assert(output.active);
    botw_host_set_audio_session_active(0); assert(!output.active);
    botw_host_set_applet_state(0,0); botw_host_set_applet_state(1,1); assert(!output.active);
    botw_host_set_audio_session_active(1); assert(output.active);
    retain_audio=1; botw_kernel *kernel=state.kernel;
    assert(cleanup()==-1 && state.audio==&output && state.kernel==kernel);
    retain_audio=0; assert(!cleanup() && !state.audio && !state.kernel);
}
static void attach(int fault) {
    char error[512]; const size_t bytes=0x100000;
    unsigned char *p=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(p!=MAP_FAILED);
    const char *name="_ZN2nn2os33AttachReadableHandleToSystemEventEPNS0_15SystemEventTypeEjbNS0_14EventClearModeE";
    Module *m=&state.modules[5];
    *m=(Module){.name="sdk",.base=p,.size=bytes,.text_end=0xa0000,.patch_off=bytes,
        .sym_off=0xb0000,.str_off=0xb1000,.str_size=strlen(name)+2,.sym_count=2};
    memcpy(p+m->str_off+1,name,strlen(name)+1);
    Sym symbol={.name=1,.info=0x12,.section=1,.value=0x851c8,.size=0x3c};
    memcpy(p+m->sym_off+sizeof(Sym),&symbol,sizeof symbol);
    uint32_t code[]={fault?0xd4200000:0xb9000001,0xb9000402,0xb9000803,0xd65f03c0};
    memcpy(p+symbol.value,code,sizeof code); sys_icache_invalidate(p+symbol.value,sizeof code);
    assert(!mprotect(p,m->text_end,PROT_READ|PROT_EXEC));
    install_signal_boundary();
    uint32_t writer=0,reader=0,duplicate=0; uint64_t event[6]={0};
    assert(!saved.create_event(saved.opaque,0,&writer,&reader,error,sizeof error));
    int rc=saved.attach_system_event(saved.opaque,(uintptr_t)event,reader,error,sizeof error);
    if (fault) {
        assert(rc==-1 && strstr(error,"audio attach") && !callback_context && state.guest_executing);
        assert(!event[0]);
    } else {
        assert(!rc && ((uint32_t*)event)[0]==reader && ((uint32_t*)event)[1]==1 && ((uint32_t*)event)[2]==1);
        symbol.size--; memcpy(p+m->sym_off+sizeof(Sym),&symbol,sizeof symbol);
        assert(saved.attach_system_event(saved.opaque,(uintptr_t)event,reader,error,sizeof error)==-1);
        assert(strstr(error,"audited SDK profile"));
    }
    assert(!botw_kernel_duplicate_handle(state.kernel,reader,&duplicate,error,sizeof error));
    assert(!saved.close_handle(saved.opaque,duplicate,error,sizeof error));
    assert(!saved.close_handle(saved.opaque,reader,error,sizeof error));
    assert(!saved.close_handle(saved.opaque,writer,error,sizeof error));
    assert(!cleanup());
}
int main(int argc,char **argv) {
    assert(argc==2); setup();
    if (!strcmp(argv[1],"copies")) copies();
    else if (!strcmp(argv[1],"lifecycle")) lifecycle();
    else attach(!strcmp(argv[1],"fault"));
    puts("AUDIO_RUNTIME_BOUNDARY_VERIFIED"); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native Mach and guest callback tests require Apple ARM64')
class RuntimeAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='botw-runtime-audio-')
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        source = root / 'driver.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = root / 'audio-runtime'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('services.c', 'kernel.c', 'hipc_address.c', 'address_space.c', 'entry.S')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name for name in
                   ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-framework', 'Security', '-O2', '-std=c11',
                                 '-Wall', '-Wextra', '-Werror', *map(str, sources), '-o', str(cls.exe)],
                                text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def check(self, case):
        result = subprocess.run([str(self.exe), case], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('AUDIO_RUNTIME_BOUNDARY_VERIFIED', result.stdout)

    def test_protected_guest_copy_rejects_inaccessible_vm(self): self.check('copies')
    def test_focus_interruption_and_shutdown_retain_kernel_until_quiescent(self): self.check('lifecycle')
    def test_original_event_attach_uses_owned_reader_and_exact_sdk_profile(self): self.check('attach')
    def test_guest_attach_fault_returns_to_audio_for_handle_cleanup(self): self.check('fault')
