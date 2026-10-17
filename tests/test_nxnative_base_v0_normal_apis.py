"""Fixed base-v0 public API contracts over actual native bodies; owned CPU only."""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
VENDOR = COMPONENT / 'upstream'
ADAPTER = COMPONENT / 'adapter'
PATCH = COMPONENT / 'patches/base-v0-normal-api-contracts.patch'
PREDECESSORS = ('normal-entry.patch', 'base-v0-texture-view.patch', 'normal-hle-crash.patch',
                'horizon-address-signal.patch', 'normal-runtime-diagnostics.patch',
                'base-v0-conditional-shader-provider.patch')
BASELINE = os.environ.get('NXNATIVE_BASE_V0_APIS_BASELINE') == '1'

# Read the already owned lexical extractor from source, never a cached module.
HELPER = ROOT / 'tests/test_nxnative_normal_runtime_diagnostics.py'
spec = importlib.util.spec_from_file_location('normal_api_owned_lexical_helper', HELPER)
helper = importlib.util.module_from_spec(spec)
exec(compile(HELPER.read_bytes(), str(HELPER), 'exec'), helper.__dict__)
definition = helper.function

ACCOUNT = r'''
#include "actual_account.c"
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
#include <errno.h>
#include <assert.h>
void hle_signal_system_event(void *ev) { (void)ev; }
static void *lookup(const char *symbol) {
    unsigned long bytes=0;
    const hle_force_ent *rows=(const void *)getsectiondata(&_mh_execute_header,"__DATA","swforce",&bytes);
    void *result=NULL; unsigned matches=0;
    for (unsigned long i=0;i<bytes/sizeof *rows;i++) if (!strcmp(rows[i].name,symbol)) { result=rows[i].fn; matches++; }
    return matches==1?result:NULL;
}
int main(int argc,char **argv) {
    if(argc!=2) return 90;
    typedef Result (*open_t)(UserHandle *);
    open_t open=lookup("_ZN2nn7account19OpenPreselectedUserEPNS0_10UserHandleE");
    if(!strcmp(argv[1],"off")) return open?91:0;
    if(!open) return 91;
    _Static_assert(sizeof(Result)==4 && sizeof(UserHandle)==24 && sizeof(Uid)==16,"public ABI");
    struct { uint64_t before; UserHandle handle; uint64_t after; } value;
    memset(&value,0xa5,sizeof value); typeof(value) previous=value;
    errno=EDOM; h_acc_Init();
    if(open(&value.handle)!=RES_OK || errno!=EDOM) return 92;
    if(value.before!=previous.before || value.after!=previous.after) return 93;
    UserHandle original; if(h_acc_TryOpenPre(&original)!=1 || memcmp(&original,&value.handle,24)) return 94;
    Uid uid={0}; if(h_acc_GetUserId(&uid,&value.handle)!=RES_OK || memcmp(&uid,&k_uid,16)) return 95;
    Uid users[2]; memset(users,0x39,sizeof users); int count=-1;
    if(h_acc_ListAll(&count,users,2)!=RES_OK || count!=1 || memcmp(users,&uid,16)) return 96;
    UserHandle popped; memset(&popped,0x52,sizeof popped); UserHandle untouched=popped;
    h_acc_PopOpen(&count,&popped,1); if(count || memcmp(&popped,&untouched,24)) return 97;
    return 0;
}
'''

VI = r'''
#include "actual_vi.c"
#include <Metal/Metal.h>
#include <math.h>
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
static void *lookup(const char *symbol) {
    unsigned long bytes=0; const hle_force_ent *rows=(const void *)getsectiondata(&_mh_execute_header,"__DATA","swforce",&bytes);
    void *result=NULL; unsigned matches=0;
    for (unsigned long i=0;i<bytes/sizeof *rows;i++) if(!strcmp(rows[i].name,symbol)) { result=rows[i].fn; matches++; }
    return matches==1?result:NULL;
}
typedef struct { double width,height; } owned_surface;
static MTLViewport viewport3(double cw,double ch,owned_surface dst) {
/* ACTUAL_VIEWPORT_3 */
    return vp;
}
static MTLViewport viewport4(double cw,double ch,owned_surface dst) {
/* ACTUAL_VIEWPORT_4 */
    return vp;
}
static int same(MTLViewport v,double x,double y,double w,double h) {
    return v.originX==x && v.originY==y && v.width==w && v.height==h && v.znear==0 && v.zfar==1;
}
int main(int argc,char **argv) {
    if(argc!=2)return 90;
    typedef Result (*set_t)(void *,int); set_t set=lookup("_ZN2nn2vi19SetLayerScalingModeEPNS0_5LayerENS0_11ScalingModeE");
    if(!strcmp(argv[1],"off"))return set?91:0;
    if(!set)return 91;
    void *display=NULL,*layer=NULL;
    if(h_vi_OpenDisp(&display)!=RES_OK || h_vi_CreateLayer(&layer,display)!=RES_OK)return 92;
    owned_surface dst={2532,1170};
    if(!strcmp(argv[1],"invalid")) {
        if(set((void *)1,2)!=RES_FAIL || set(NULL,4)!=RES_FAIL)return 93;
        for(int mode=-1;mode<=6;mode++) if(mode!=2 && mode!=4 && set(layer,mode)!=RES_FAIL)return 94;
        if(!same(viewport3(1920,1080,dst),226,0,2080,1170))return 95;
        return 0;
    }
    if(set(layer,2)!=RES_OK || !same(viewport3(1920,1080,dst),0,0,2532,1170) || !same(viewport4(1920,1080,dst),0,0,2532,1170))return 96;
    if(set(layer,4)!=RES_OK || !same(viewport3(1920,1080,dst),226,0,2080,1170) || !same(viewport4(1920,1080,dst),226,0,2080,1170))return 97;
    dst=(owned_surface){1920,1080};
    if(!same(viewport3(1920,1080,dst),0,0,1920,1080) || !same(viewport4(1920,1080,dst),0,0,1920,1080))return 98;
    return 0;
}
'''

SYNC = r'''
#include "nvn_protos.h"
#include "nvn_obj.h"
#include <errno.h>
#include <string.h>
static uint64_t fence_value=0xfedcba9876543210ull; static int calls,answers[2]; static uint64_t waits[2];
static uint64_t mtl_last_fence(void){return fence_value;}
static int mtl_wait(uint64_t fence,uint64_t timeout) {
    if(fence!=fence_value || calls>=2)return 0;
    waits[calls]=timeout; errno=calls?EAGAIN:ERANGE; return answers[calls++];
}
/* ACTUAL_NVNMETA */
/* ACTUAL_SYNCS */
int main(int argc,char **argv) {
    if(argc!=2)return 90;
    _Static_assert(NVN_SYNC_WAIT_RESULT_FAILED==3 && NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED==2,"sync enum");
    struct { uint64_t before; NVNsync object; uint64_t after; } value;
    memset(&value,0xa5,sizeof value); NVNdevice device;
    if(!h_nvnSyncInitialize(&value.object,&device))return 91;
    typeof(value) previous=value;
    const uint64_t ts[]={0,1,37,0x8000000000000000ull,UINT64_MAX};
    for(unsigned i=0;i<sizeof ts/sizeof ts[0];i++) {
        errno=EDOM; calls=0;
        if(!strcmp(argv[1],"fresh")) {
            if(h_nvnSyncWait(&value.object,ts[i])!=NVN_SYNC_WAIT_RESULT_FAILED || calls || errno!=EDOM)return 92;
        } else if(!strcmp(argv[1],"off")) {
            if(h_nvnSyncWait(&value.object,ts[i])!=NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED || calls || errno!=EDOM)return 93;
        } else {
            h_nvnQueueFenceSync(NULL,&value.object,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0); previous=value;
            answers[0]=!strcmp(argv[1],"already"); answers[1]=!strcmp(argv[1],"condition");
            NVNsyncWaitResult expected=answers[0]?NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED:answers[1]?NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED:NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;
            if(h_nvnSyncWait(&value.object,ts[i])!=expected || calls!=(answers[0]?1:2) || waits[0]!=0 || (!answers[0] && waits[1]!=ts[i]) || errno!=(answers[0]?ERANGE:EAGAIN))return 94;
        }
        if(memcmp(&value,&previous,sizeof value))return 95;
    }
    return 0;
}
'''


class BaseV0NormalApis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='nxnative-owned-base-v0-apis-'); cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name); cls.stage=cls.work/'staged'; shutil.copytree(VENDOR,cls.stage)
        cls.applied=[COMPONENT/'patches'/n for n in PREDECESSORS]
        if PATCH.is_file() and not BASELINE:cls.applied.append(PATCH)
        cls.before_impl={}
        for patch in cls.applied:
            if patch==PATCH:
                cls.before_impl={name:(cls.stage/name).read_bytes() for name in ('dm/host/hle_account.c','dm/gfx/hle_vi.c','dm/gfx/nvn/hle_nvn.c','dm/gfx/nvn/nvn_mtl.m')}
            r=subprocess.run(['patch','-p1','--batch','--fuzz=0','-i',str(patch)],cwd=cls.stage,capture_output=True,text=True)
            if r.returncode:raise AssertionError(r.stdout+r.stderr)
        cls.originals={name:(cls.stage/name).read_bytes() for name in ('dm/host/hle_account.c','dm/gfx/hle_vi.c','dm/gfx/nvn/hle_nvn.c','dm/gfx/nvn/nvn_mtl.m')}
        # This fixture needs only the existing dm.h declaration switch, not a
        # fabricated complete base-v0 runtime game profile.
        (cls.stage/'dm/host/game_profile.h').write_text('#define DM_GAME_CORE_PREEMPT 0\n')
        shutil.copyfile(cls.stage/'dm/host/hle_account.c',cls.work/'actual_account.c')
        # Relative ../host/hle.h in the actual VI source remains real.
        vi=VI.replace('#include "actual_vi.c"','#include "'+str(cls.stage/'dm/gfx/hle_vi.c')+'"')
        mtl=(cls.stage/'dm/gfx/nvn/nvn_mtl.m').read_text()
        for n,sig in ((3,'static int present_encode('),(4,'static int present_encode_m4(')):
            body=definition(mtl,sig); start=body.index('    double dw ='); end=body.index('    int full =',start)
            vi=vi.replace('/* ACTUAL_VIEWPORT_'+str(n)+' */',body[start:end])
        hle=(cls.stage/'dm/gfx/nvn/hle_nvn.c').read_text()
        meta=hle[hle.index('typedef struct { const char *name; void *fn; } nvn_impl;'):hle.index('// Newer interfaces without prototypes:')]
        sync=SYNC.replace('/* ACTUAL_NVNMETA */',meta).replace('/* ACTUAL_SYNCS */','\n'.join(definition(hle,'NVN('+n+',') for n in ('nvnSyncInitialize','nvnSyncFinalize','nvnQueueFenceSync','nvnSyncWait')))
        sources={'account':ACCOUNT,'vi':vi,'sync':sync}; cls.commands=[]; cls.cases=[];cls.binaries={}
        clang=subprocess.check_output(['xcrun','--find','clang'],text=True).strip(); sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
        for name,source in sources.items():
            src=cls.work/(name+('.m' if name=='vi' else '.c'));src.write_text(source)
            for enabled in (0,1):
                # Mach-O registration arrays must retain their production
                # stride. ASan global redzones would pad every section row;
                # stack/heap checks and UBSan stay enabled for real API bodies.
                exe=cls.work/(name+str(enabled)); command=[clang,'-isysroot',sdk,'-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-unused-parameter','-pthread','-fsanitize=address,undefined','-mllvm','-asan-globals=0','-fno-sanitize-recover=all','-DNXNATIVE_NORMAL_POLICY=1','-DNXNATIVE_BASE_V0_NORMAL_APIS='+str(enabled),'-I',str(cls.work),'-I',str(cls.stage/'dm/host'),'-I',str(cls.stage/'dm/gfx/nvn'),'-I',str(cls.stage/'dm/gfx/nvn/include'),'-I',str(ADAPTER),'-I',str(ROOT/'experiments/botw_upstream_host/adapter'),str(src),str(ADAPTER/'normal_policy.c'),str(ADAPTER/'normal_failures.c'),'-o',str(exe)]
                if name=='vi':command+=['-framework','Foundation']
                r=subprocess.run(command,capture_output=True,text=True);cls.commands.append({'argv':command,'exit_code':r.returncode})
                if r.returncode:raise AssertionError(r.stderr)
                cls.binaries[name,enabled]=exe

    def case(self,name,mode,enabled=1):
        r=subprocess.run([str(self.binaries[name,enabled]),mode],capture_output=True,timeout=5,env={'PATH':'/usr/bin:/bin','ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
        self.cases.append({'name':name,'mode':mode,'enabled':enabled,'exit_code':r.returncode})
        self.assertEqual(r.returncode,0,f'actual {name}/{mode}: {r.returncode}')
        self.assertEqual(r.stdout,b'');self.assertEqual(r.stderr,b'')

    def test_account_result_entry_uses_actual_complete_local_handle_producer(self): self.case('account','roundtrip')
    def test_native_vi_scaling_applies_to_both_original_present_viewports(self): self.case('vi','scaling')
    def test_native_vi_rejects_foreign_identity_and_unknown_modes_without_changing_state(self): self.case('vi','invalid')
    def test_base_v0_unfenced_sync_is_failed_without_wait_or_errno_object_change(self):self.case('sync','fresh')
    def test_fenced_sync_original_0_1_2_and_full_timeout_mapping_remains(self):
        for mode in ('already','condition','timeout'):
            with self.subTest(mode=mode):self.case('sync',mode)
    def test_disabled_explicit_base_v0_branch_preserves_original_registry_and_sync_contract(self):
        for name in ('account','vi','sync'):
            with self.subTest(name=name):self.case(name,'off',0)
    def test_patch_inverse_restores_complete_predecessor_files(self):
        self.assertTrue(PATCH.is_file(),'base-v0 normal API patch is not present')
        r=subprocess.run(['patch','-R','-p1','--batch','--fuzz=0','-i',str(PATCH)],cwd=self.stage,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        for name,before in self.before_impl.items():self.assertEqual((self.stage/name).read_bytes(),before)
        # Reapply to leave shared test state intact.
        r=subprocess.run(['patch','-p1','--batch','--fuzz=0','-i',str(PATCH)],cwd=self.stage,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        for name,before in self.originals.items():self.assertEqual((self.stage/name).read_bytes(),before)

if __name__=='__main__':unittest.main()
