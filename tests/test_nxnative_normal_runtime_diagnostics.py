"""Normal diagnostic closure on owned CPU state; never run Guest/Metal/device."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
VENDOR = COMPONENT / 'upstream'
ADAPTER = COMPONENT / 'adapter'
PATCH = COMPONENT / 'patches/normal-runtime-diagnostics.patch'
PREDECESSORS = ('normal-entry.patch', 'normal-hle-crash.patch',
                'horizon-address-signal.patch', 'base-v0-texture-view.patch')


def function(source, signature):
    match = re.search(r'(?m)^' + re.escape(signature) + r'[^;{\n]*\{', source)
    if not match: raise AssertionError('actual function definition missing: ' + signature)
    start, opening = match.start(), match.end() - 1
    state, depth, i = 'code', 0, opening
    while i < len(source):
        c, pair = source[i], source[i:i + 2]
        if state == 'code':
            if pair in ('/*', '//'):
                state = 'block' if pair == '/*' else 'line'; i += 2; continue
            if c in ('"', "'"): state = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if not depth: return source[start:i + 1]
        elif state == 'block' and pair == '*/': state = 'code'; i += 2; continue
        elif state == 'line' and c == '\n': state = 'code'
        elif state in ('"', "'"):
            if c == '\\': i += 2; continue
            if c == state: state = 'code'
        i += 1
    raise AssertionError('unterminated actual function: ' + signature)


GPU_PROBE = r'''
#include "dm.h"
#include "normal_failures.h"
#include <errno.h>
#include <signal.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include <unistd.h>
#include <execinfo.h>
#include <AudioToolbox/AudioToolbox.h>
#include <dispatch/dispatch.h>
#include "nvn_count.h"
/* ACTUAL_GPU_MACROS */
/* ACTUAL_PASSDUMP */
typedef struct { _Atomic uint64_t cnt; void *fn; } nvn_slot_t;
static nvn_slot_t nvn_slot[NVN_NFUNC];
static const char *nvn_names[NVN_NFUNC];
static uint8_t g_unimpl_seen[NVN_NFUNC];
/* ACTUAL_UNIMPLEMENTED */
static pthread_t g_sub_thread; static _Atomic int g_sub_thread_set;
static int forbidden_name(pthread_t thread, char *out, size_t n) {
    (void)thread; (void)out; (void)n; _exit(95);
}
#define pthread_getname_np forbidden_name
/* ACTUAL_UPLOAD_OWNER */
#undef pthread_getname_np
#define SHOTQ_MAX 8
static struct { uint64_t frame; char path[1024]; } g_shotq[SHOTQ_MAX]; static int g_nshotq;
static uint64_t g_present_n;
static pthread_mutex_t g_shotq_mu = PTHREAD_MUTEX_INITIALIZER;
/* ACTUAL_SHOT_REQUEST */
/* ACTUAL_AUDIO_NOTE */
module_t g_mod[MAX_MOD]; int g_nmod;
static int time_calls;
static int64_t owned_sdk_time(int64_t *out) { time_calls++; if (out) *out=903; return 903; }
void *module_sym(module_t *m, const char *name) {
    if (m!=g_mod || strcmp(name,"time")) _exit(96);
    return owned_sdk_time;
}
#define HLE_FORCE(symbol, why, result, name, ...) static result name(__VA_ARGS__)
/* ACTUAL_TIME_FORWARD */
#undef HLE_FORCE
static void tripwire(int sig) { (void)sig; _exit(95); }
static void *owned_wait(void *p) { (void)p; for (;;) pause(); }
int main(int argc, char **argv) {
    if (argc != 2) return 90;
    signal(SIGSEGV, tripwire); signal(SIGBUS, tripwire); signal(SIGABRT, tripwire);
    void *bad = mmap(NULL, 4u << 20, PROT_NONE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (bad == MAP_FAILED) return 94;
    const char *mode = argv[1];
    if (!strcmp(mode, "macro")) { int changed = 0; MLOG("owned %d", ++changed); return changed ? 91 : 0; }
    if (!strcmp(mode, "pass-macro")) { int changed = 0; g_kd_force = 1; PD("owned %d", ++changed); return changed ? 91 : 0; }
    if (!strcmp(mode, "unimplemented")) { nvn_unimpl_c(nvn_slot, 0, 0, 0); return 91; }
    if (!strcmp(mode, "shot")) { dm_gfx_shot_at(1, bad); return 91; }
    if (!strcmp(mode, "audio-note")) { au_note(bad, bad, bad); return 0; }
    if (!strcmp(mode, "time-forward")) {
        g_nmod=2; strcpy(g_mod[0].name,"sdk"); strcpy(g_mod[1].name,"main");
        strcpy(g_mod[1].build_id,DM_D2R_BUILD); g_mod[1].base=bad;
        int64_t value=0;
        if (h_time_time(&value)!=903 || value!=903 || time_calls!=1) return 97;
        return 0;
    }
    if (!strcmp(mode, "upload-owner")) {
        if (pthread_create(&g_sub_thread, NULL, owned_wait, NULL)) return 93;
        atomic_store(&g_sub_thread_set, 1); up_owner_check(bad); return 91;
    }
    if (!strcmp(mode, "upload-owner-valid")) {
        g_sub_thread = pthread_self(); atomic_store(&g_sub_thread_set, 1);
        up_owner_check(bad); return 0;
    }
    return 92;
}
'''

THREAD_PROBE = r'''
#include <signal.h>
#include <sys/mman.h>
#include <stdio.h>
#include <stdatomic.h>
#include <unistd.h>
#include "dm.h"
static _Noreturn int forbidden_output(void) { _exit(95); }
#define fprintf(...) forbidden_output()
#include "actual_hle_thread.c"
#undef fprintf
module_t g_mod[MAX_MOD]; int g_nmod;
static void tripwire(int sig) { (void)sig; _exit(95); }
int main(int argc, char **argv) {
    if (argc != 2) return 90;
    signal(SIGSEGV, tripwire); signal(SIGBUS, tripwire);
    void *bad = mmap(NULL, (size_t)getpagesize(), PROT_NONE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (bad == MAP_FAILED) return 94;
    const char *mode = argv[1];
    if (!strcmp(mode, "backtrace")) { thr_bt(bad); return 91; }
    if (!strcmp(mode, "dump")) { svc_thread_dump(); return 91; }
    if (!strcmp(mode, "name")) { thr_name(bad, bad, 1); return 91; }
    if (!strcmp(mode, "list")) { thr_list(bad, 1); return 91; }
    if (!strcmp(mode, "name-of")) { thr_name_of(pthread_self(), bad, 1); return 91; }
    if (!strcmp(mode, "find")) { thr_find_by_name(bad, bad); return 91; }
    if (!strcmp(mode, "counts")) { thr_counts(bad, bad, bad); return 91; }
    if (!strcmp(mode, "normal-context")) {
        if (pthread_key_create(&g_keythr, NULL)) return 96;
        thr_t owned = {.handle = 0x1234};
        uint64_t frame[100]; for (unsigned i=0; i<100; i++) frame[i]=i+7;
        hz_ctx_t out; pthread_setspecific(g_keythr, &owned);
        thr_svc_enter(frame, 0x5678); pthread_mutex_lock(&G); ctx_fill(&owned, &out); pthread_mutex_unlock(&G);
        if (out.r[0] != frame[0] || out.r[28] != frame[28] || out.fp != frame[29] ||
            out.lr != frame[30] || out.pc != 0x5678 || out.sp != (uint64_t)frame+800 ||
            out.pstate != (uint32_t)frame[96] || out.fpsr != frame[97] || out.fpcr != frame[98] ||
            memcmp(out.v, frame+32, sizeof out.v) || thr_cur_handle() != 0x1234) return 97;
        thr_svc_leave(); if (atomic_load(&owned.svc_regs) || owned.paused) return 98;
        pthread_key_delete(g_keythr); return 0;
    }
    return 92;
}
'''


class NormalRuntimeDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nxnative-owned-normal-runtime-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name); cls.stage = cls.base / 'staged'
        names = {'dm/host/dm.h', 'dm/host/hle.h', 'dm/host/hle_thread.c', 'dm/host/tests/t_condvar_stub.c',
                 'dm/host/hle_audio.c', 'dm/host/hle_time.c',
                 'dm/gfx/nvn/nvn_mtl.m', 'dm/gfx/nvn/hle_nvn.c', 'dm/gfx/nvn/nvn_count.h'}
        patches = [COMPONENT/'patches'/p for p in PREDECESSORS]
        if PATCH.exists(): patches.append(PATCH)
        for patch in patches:
            for line in patch.read_text().splitlines():
                if line.startswith('--- a/'): names.add(line[6:])
        cls.originals = {}
        for name in sorted(names):
            raw=(VENDOR/name).read_bytes(); cls.originals[name]=hashlib.sha256(raw).hexdigest()
            dest=cls.stage/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(raw)
        cls.applied = patches
        for patch in patches:
            r=subprocess.run(['patch','-p1','--batch','--fuzz=0','-i',str(patch)],cwd=cls.stage,capture_output=True,text=True)
            if r.returncode: raise AssertionError(r.stdout+r.stderr)
        host=cls.stage/'dm/host'; gpu=cls.stage/'dm/gfx/nvn'
        (host/'game_profile.h').write_text('#define DM_GAME_CORE_PREEMPT 0\n')
        mtl=(gpu/'nvn_mtl.m').read_text(); hle=(gpu/'hle_nvn.c').read_text()
        if '/* NXNATIVE_NORMAL_GPU_LOG_BEGIN */' in mtl:
            macros=mtl.split('/* NXNATIVE_NORMAL_GPU_LOG_BEGIN */',1)[1].split('/* NXNATIVE_NORMAL_GPU_LOG_END */',1)[0]
        else:
            macros='\n'.join(l for l in mtl.splitlines() if l.startswith(('#define MLOG(', '#define MDIE(')))
        passdump='static int g_kd_force; static _Atomic uint64_t g_cur_frame;\n'+function(mtl,'static int passdump(void)')+'\n'
        if '/* NXNATIVE_NORMAL_PASS_LOG_BEGIN */' in mtl:
            passdump+=mtl.split('/* NXNATIVE_NORMAL_PASS_LOG_BEGIN */',1)[1].split('/* NXNATIVE_NORMAL_PASS_LOG_END */',1)[0]
        else: passdump+='\n'.join(l for l in mtl.splitlines() if l.startswith('#define PD('))+'\n'
        owner=function(mtl,'static int on_sub_thread(void)')+'\n'+function(mtl,'static void up_owner_check(')
        probe=GPU_PROBE.replace('/* ACTUAL_GPU_MACROS */',macros).replace('/* ACTUAL_PASSDUMP */',passdump)
        probe=probe.replace('/* ACTUAL_UNIMPLEMENTED */',function(hle,'uint64_t nvn_unimpl_c('))
        probe=probe.replace('/* ACTUAL_UPLOAD_OWNER */',owner).replace('/* ACTUAL_SHOT_REQUEST */',function(mtl,'void dm_gfx_shot_at('))
        audio=(host/'hle_audio.c').read_text()
        audio_types=audio[audio.index('typedef struct { void *ptr;'):audio.index('#define AOMAX')]
        probe=probe.replace('/* ACTUAL_AUDIO_NOTE */',audio_types+'\n'+function(audio,'static const char *site(')+'\n'+function(audio,'static void au_note('))
        time=(host/'hle_time.c').read_text()
        probe=probe.replace('/* ACTUAL_TIME_FORWARD */',function(time,'static int64_t fixed_time(void)')+'\n'+function(time,'HLE_FORCE("time",'))
        (cls.base/'gpu.c').write_text(probe)
        shutil.copyfile(host/'hle_thread.c',cls.base/'actual_hle_thread.c')
        (cls.base/'thread.c').write_text(THREAD_PROBE)
        clang=subprocess.check_output(['xcrun','--find','clang'],text=True).strip()
        sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
        common=[clang,'-isysroot',sdk,'-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function',
                '-Wno-unused-variable','-Wno-unused-parameter','-Wno-unused-but-set-variable','-pthread',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer',
                '-DNXNATIVE_NORMAL_POLICY=1','-I',str(cls.base),'-I',str(host),'-I',str(gpu),'-I',str(ADAPTER)]
        cls.commands=[]; cls.binaries={}; cls.runtime_cases=[]
        for name, extras in [('gpu',[]),('thread',[str(host/'tests/t_condvar_stub.c')])]:
            exe=cls.base/name; command=common+[str(cls.base/(name+'.c')),*extras,str(ADAPTER/'normal_policy.c'),
                       str(ADAPTER/'normal_failures.c'),'-o',str(exe)]
            r=subprocess.run(command,capture_output=True,text=True); cls.commands.append({'argv':command,'exit_code':r.returncode})
            if r.returncode: raise AssertionError(r.stderr)
            cls.binaries[name]=exe

    def owned(self, binary, mode, code=0, message=b''):
        r=subprocess.run([str(self.binaries[binary]),mode],capture_output=True,timeout=5,
            env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','ASAN_OPTIONS':'detect_leaks=0:handle_segv=0:handle_sigbus=0',
                 'UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=0'})
        self.runtime_cases.append({'binary':binary,'mode':mode,'exit_code':r.returncode,'stderr_sha256':hashlib.sha256(r.stderr).hexdigest()})
        self.assertEqual(r.returncode,code,f'{binary}/{mode}: exit={r.returncode}, stderr_sha256={hashlib.sha256(r.stderr).hexdigest()}')
        self.assertEqual(r.stdout,b'')
        self.assertEqual(hashlib.sha256(r.stderr).digest(),hashlib.sha256(message).digest())

    def test_gpu_log_macros_do_not_evaluate_operands(self):
        self.owned('gpu','macro'); self.owned('gpu','pass-macro')

    def test_gpu_unimplemented_is_a_fixed_failure(self):
        self.owned('gpu','unimplemented',3,b'[nxnative normal] unimplemented import\n')

    def test_upload_owner_failure_precedes_name_and_backtrace_but_owner_success_remains(self):
        self.owned('gpu','upload-owner',70,b'[nxnative normal] host failure\n')
        self.owned('gpu','upload-owner-valid')

    def test_screenshot_request_rejects_before_path_access(self):
        self.owned('gpu','shot',77,b'[nxnative normal] diagnostic entry disabled\n')

    def test_thread_diagnostics_fail_before_context_or_output_access(self):
        for mode in ('backtrace','dump','name','list','name-of','find','counts'):
            with self.subTest(mode=mode): self.owned('thread',mode,77,b'[nxnative normal] diagnostic entry disabled\n')

    def test_normal_thread_context_and_tsd_service_remains(self):
        self.owned('thread','normal-context')

    def test_audio_note_does_not_read_output_or_caller(self):
        self.owned('gpu','audio-note')

    def test_normal_time_calls_original_sdk_without_private_instruction_scan(self):
        self.owned('gpu','time-forward')

    def test_new_patch_inverse_restores_complete_predecessor_bytes(self):
        self.assertTrue(PATCH.exists(),'normal runtime diagnostic implementation is absent')
        target=self.base/'reverse'; shutil.copytree(self.stage,target)
        for patch in reversed(self.applied):
            r=subprocess.run(['patch','-R','-p1','--batch','--fuzz=0','-i',str(patch)],cwd=target,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        for name in self.originals:
            self.assertEqual((target/name).read_bytes(),(VENDOR/name).read_bytes(),name)


if __name__ == '__main__': unittest.main()
