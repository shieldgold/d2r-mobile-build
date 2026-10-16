"""The import adapter uses normal call registers, never caller-memory reads.

Graphics linkage is synthetic. All executable test code is an owned A64
fixture, not bytes from the game. No Metal device is created.
"""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from test_botw_runtime_callbacks import DRIVER as CALLBACK_DRIVER

ROOT = Path(__file__).resolve().parents[1]
# Reuse only the existing headless graphics linkage, not its test cases.
STUBS = CALLBACK_DRIVER.split('typedef struct { NVNcommandBuffer *command;')[0]
DRIVER = STUBS + r'''
static char observed_error[2048];
static unsigned transfer_reports;
static void collect(const char *text) {
    if(!strncmp(text,"error=",6))snprintf(observed_error,sizeof observed_error,"%s",text);
    if(!strncmp(text,"texture_value_storage_transferred=",34)){
        assert(!strstr(text,"0x") && !strstr(text,"source") && !strstr(text,"destination"));transfer_reports++;
    }
    puts(text);
}
static void setup(void) {
    memset(&state, 0, sizeof state);
    state.owner = pthread_self(); state.active = 1; state.event = collect;
    state.address_bits = 39; state.host39_transport_audited = 1;
    Module *m = &state.modules[0]; m->name = "main";
    m->size = m->text_end = m->patch_off = 0x1800000;
    m->base = mmap(NULL,m->size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(m->base != MAP_FAILED);
    m = &state.modules[5]; m->name = "sdk";
    m->size = 0x800000; m->text_end = 0x350194; m->patch_off = 0x7a0000;
    m->base = mmap(NULL,m->size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(m->base != MAP_FAILED);
    m->sym_off = 0x1000; m->str_off = 0x2000; m->str_size = 8; m->sym_count = 2;
    memcpy(m->base + m->str_off + 1,"memcpy",7);
    ((Sym *)(m->base + m->sym_off))[1] = (Sym){.name=1,.info=0x12,.section=1,
        .value=0x33af80,.size=0x484};
}
static unsigned copied,preflights,commits,aborts,pending;
static int preflight_result=1,commit_result=1,local_expected,wrong_result;
static int abort_result __attribute__((unused))=1;
static void *owned_copy(void *dst,const void *src,size_t bytes) {
    assert(!!callback_context == !!local_expected); copied++;
    if(bytes)memmove(dst,src,bytes);return wrong_result?(char*)dst+1:dst;
}
#ifndef BOTW_TEST_NO_MOVE_PROVIDER
int botw_graphics_texture_move_preflight(const void *src,void *dst,size_t bytes,
        botw_graphics_texture_move_token *token,char *error,size_t capacity) {
    assert(src && dst && bytes==0xc0 && !callback_context && !pending);preflights++;
    if(preflight_result!=1){snprintf(error,capacity,"owned preflight rejection");return preflight_result;}
    token->opaque[0]=0x1234;pending=1;return 1;
}
int botw_graphics_texture_move_commit(const botw_graphics_texture_move_token *token,
        char *error,size_t capacity) {
    assert(token->opaque[0]==0x1234 && pending && !callback_context);commits++;pending=0;
    if(commit_result!=1)snprintf(error,capacity,"owned post-copy epoch rejection");return commit_result;
}
int botw_graphics_texture_move_abort(const botw_graphics_texture_move_token *token,
        char *error,size_t capacity) {
    assert(token->opaque[0]==0x1234 && pending && !callback_context);aborts++;pending=0;
    if(abort_result!=1)snprintf(error,capacity,"owned abort rejection");return abort_result;
}
#endif
static void jump(uint8_t *where,void *target) {
    const uint32_t code[]={0x58000050,0xd61f0200};
    memcpy(where,code,sizeof code);memcpy(where+8,&target,sizeof target);
}
static void digest_context(botw_texture_move_context *c,const uint8_t *image) {
    CC_SHA256(image+c->offset,(CC_LONG)c->bytes,c->sha256);
}
static botw_texture_move_profile owned_profile(void) {
    Module *main=&state.modules[0],*sdk=&state.modules[5];
    botw_texture_move_profile profile=botw_texture_move_base_v0;
    digest_context(&profile.main_function,main->base);
    digest_context(&profile.main_memcpy_plt,main->base);
    digest_context(&profile.sdk_memcpy,sdk->base);
    digest_context(&profile.sdk_thunks,sdk->base);
    return profile;
}
static void synthetic_profile(void) {
    Module *main=&state.modules[0],*sdk=&state.modules[5];
    /* Owned call/return shim, not game instructions. */
    const uint32_t call[]={0xd63f0200,0xd61f0280};
    memcpy(main->base+0xa37824,call,sizeof call);
    jump(sdk->base+0x33af80,owned_copy);
    botw_texture_move_profile profile=owned_profile();
    state.texture_move_memcpy=audit_texture_move_import(&profile);state.texture_move_audited=1;
    __builtin___clear_cache((char*)main->base,(char*)main->base+main->size);
    __builtin___clear_cache((char*)sdk->base+0x338000,(char*)sdk->base+0x33c000);
    assert(!mprotect(main->base,main->size,PROT_READ|PROT_EXEC));
    assert(!mprotect(sdk->base+0x338000,PAGE,PROT_READ|PROT_EXEC));
    state.guest_executing=1;state.current_entry="owned texture move";install_signal_boundary();
}
static void *dispatch(void *dst,const void *src,size_t n,uintptr_t ra,uint64_t flag) {
    return botw_texture_move_memcpy_dispatch(dst,src,n,ra,flag);
}
static uintptr_t matching(void){return (uintptr_t)state.modules[0].base+0xa37828;}
static unsigned char input[192],output[192];
static void reset_bytes(void){for(unsigned i=0;i<sizeof input;i++)input[i]=(unsigned char)i;memset(output,0xa5,sizeof output);}
static void reject(const char *reason) {
    assert(!pending);local_expected=1;observed_error[0]=0;state.guest_executing=1;
    if(!sigsetjmp(state.recovery,1)){dispatch(output,input,192,matching(),1);assert(!"unexpected copy success");}
    assert(!pending && !callback_context && !state.fault_signal);
    assert(strstr(observed_error,reason));
}
extern void owned_move_abi(void*,const void*,size_t,void*,void*,uint64_t,uint64_t*);
static _Atomic int worker_entered,worker_release;
static int worker_executable(void *opaque,uint64_t entry){(void)opaque;return entry==0x10000;}
static int worker_execute(void *opaque,uint64_t e,uint64_t a,uint64_t s,uint32_t h,char *err,size_t cap){
    (void)opaque;(void)e;(void)a;(void)s;(void)h;(void)err;(void)cap;
    atomic_store(&worker_entered,1);while(!atomic_load(&worker_release))sched_yield();return 0;
}
static void pending_join(void) {
    const size_t unit=0x200000;char error[512];
    botw_kernel_callbacks callbacks={.executable=worker_executable,.execute_thread=worker_execute};
    state.kernel=botw_kernel_create(&callbacks,NULL);assert(state.kernel);
    assert(!botw_kernel_configure_address_space(state.kernel,39,error,sizeof error));
    assert(!botw_kernel_configure_memory(state.kernel,unit*2,unit*2,unit,unit*2,0,error,sizeof error));
    uint64_t regs[31]={0,unit};assert(botw_kernel_dispatch(state.kernel,1,regs,error,sizeof error)==BOTW_KERNEL_HANDLED && !regs[0]);uint64_t heap=regs[1];
    memset(regs,0,sizeof regs);regs[1]=14;regs[2]=BOTW_KERNEL_CURRENT_PROCESS;
    assert(botw_kernel_dispatch(state.kernel,0x29,regs,error,sizeof error)==BOTW_KERNEL_HANDLED && !regs[0]);uint64_t stack=regs[1];
    regs[0]=stack;regs[1]=heap;regs[2]=unit;assert(botw_kernel_dispatch(state.kernel,4,regs,error,sizeof error)==BOTW_KERNEL_HANDLED && !regs[0]);
    memset(regs,0,sizeof regs);regs[1]=0x10000;regs[3]=stack+unit;regs[4]=44;regs[5]=(uint64_t)-2;
    assert(botw_kernel_dispatch(state.kernel,8,regs,error,sizeof error)==BOTW_KERNEL_HANDLED && !regs[0]);regs[0]=regs[1];
    assert(botw_kernel_dispatch(state.kernel,9,regs,error,sizeof error)==BOTW_KERNEL_HANDLED && !regs[0]);
    for(unsigned i=0;i<1000 && !atomic_load(&worker_entered);i++){struct timespec tick={.tv_nsec=1000000};nanosleep(&tick,NULL);}assert(atomic_load(&worker_entered));
    uint8_t *main=state.modules[0].base,*sdk=state.modules[5].base;botw_kernel *kernel=state.kernel;
    assert(cleanup()==-1 && state.kernel==kernel && state.modules[0].base==main && state.modules[5].base==sdk);
    uint8_t byte=0;vm_size_t got=0;assert(vm_read_overwrite(mach_task_self(),(vm_address_t)sdk+0x33af80,1,(vm_address_t)&byte,&got)==KERN_SUCCESS && got==1);
    atomic_store(&worker_release,1);assert(!cleanup() && !state.kernel);
    got=0;assert(vm_read_overwrite(mach_task_self(),(vm_address_t)sdk+0x33af80,1,(vm_address_t)&byte,&got)!=KERN_SUCCESS);
}
int main(int argc,char **argv) {
    assert(argc >= 2);setup();reset_bytes();
    if(!strcmp(argv[1],"unknown-context")) {
        if (!sigsetjmp(state.recovery,1)) {
            (void)find_symbol("memcpy");
            fprintf(stderr,"MISSING_TEXTURE_MOVE_CONTEXT_REJECTION\n"); return 91;
        }
        puts("RUNTIME_TEXTURE_MOVE_CONTEXT_REJECTED");return 0;
    }
    synthetic_profile();
    if(!strcmp(argv[1],"pin")) {
        pending_join();puts("RUNTIME_TEXTURE_MOVE_VERIFIED");return 0;
    } else if(!strcmp(argv[1],"profile")) {
        botw_texture_move_profile original=owned_profile();
        for(unsigned i=0;i<7;i++) {
            botw_texture_move_profile p=original;
            if(i==0)p.main_function.sha256[0]^=1;if(i==1)p.main_memcpy_plt.sha256[0]^=1;
            if(i==2)p.sdk_memcpy.sha256[0]^=1;if(i==3)p.sdk_thunks.sha256[0]^=1;
            if(i==4)p.main_function.bytes=SIZE_MAX;if(i==5)p.sdk_thunks.offset=0;
            if(i==6)p.return_offset=SIZE_MAX;
            if(!sigsetjmp(state.recovery,1)){(void)audit_texture_move_import(&p);assert(!"unknown synthetic profile accepted");}
        }
        assert(audit_texture_move_import(&original)==state.texture_move_memcpy);
    } else if(!strcmp(argv[1],"trace")) {
        local_expected=1;for(unsigned i=0;i<10;i++)assert(dispatch(output,input,192,matching(),1)==output);
        assert(copied==10 && commits==10 && !pending && transfer_reports==4 && state.texture_move_reports==4);
    } else if(!strcmp(argv[1],"bypass")) {
        assert(dispatch(output,input,17,matching(),1)==output && copied==1 && !preflights);
        assert(!memcmp(output,input,17));reset_bytes();
        assert(dispatch(output,input,192,matching()+4,1)==output && copied==2 && !preflights);reset_bytes();
        assert(dispatch(output,input,192,matching(),0)==output && copied==3 && !preflights);
        assert(dispatch(NULL,NULL,0,0,0)==NULL && copied==4 && !preflights);
    } else if(!strcmp(argv[1],"success")) {
        local_expected=1;assert(dispatch(output,input,192,matching(),0x123456789abcdef1)==output);
        assert(!memcmp(output,input,192) && copied==1 && preflights==1 && commits==1 && !aborts && !pending);
    } else if(!strcmp(argv[1],"abi")) {
        uint64_t seen[20]={0};local_expected=1;
        owned_move_abi(output,input,192,botw_texture_move_memcpy_entry,
            state.modules[0].base+0xa37824,UINT64_C(0x123456789abcdef1),seen);
        assert(seen[0]==(uintptr_t)output && seen[1]==19 && seen[2]==21 && seen[3]==22);
        assert(seen[4]==UINT64_C(0x123456789abcdef1));for(unsigned i=5;i<10;i++)assert(seen[i]==i+19);
        assert(seen[10]==1);for(unsigned i=11;i<19;i++)assert(seen[i]==i-3);assert(!seen[19]);
        assert(preflights==1 && commits==1 && copied==1 && !pending && !memcmp(output,input,192));
    } else if(!strcmp(argv[1],"missing") || !strcmp(argv[1],"missing-real")) {
        if(!strcmp(argv[1],"missing-real")) {
            assert(graphics_move_preflight==botw_texture_move_no_preflight);
            assert(graphics_move_commit==botw_texture_move_no_commit);
            assert(graphics_move_abort==botw_texture_move_no_abort);reject("requires preflight");
        } else {
            for(unsigned i=0;i<3;i++) {
                __typeof__(graphics_move_preflight) old_pre=graphics_move_preflight;
                __typeof__(graphics_move_commit) old_commit=graphics_move_commit;
                __typeof__(graphics_move_abort) old_abort=graphics_move_abort;
                if(!i)graphics_move_preflight=botw_texture_move_no_preflight;
                if(i==1)graphics_move_commit=NULL;if(i==2)graphics_move_abort=botw_texture_move_no_abort;
                reject("requires preflight");graphics_move_preflight=old_pre;graphics_move_commit=old_commit;graphics_move_abort=old_abort;
            }
        }
        assert(!preflights && !copied && !commits && !aborts && output[0]==0xa5);
    } else if(!strcmp(argv[1],"preflight")) {
        for(int value=0;value<=2;value+=2){preflight_result=value;reject("preflight rejection");}
        assert(preflights==2 && !copied && !commits && !aborts && output[0]==0xa5);
    } else if(!strcmp(argv[1],"commit")) {
        for(int value=0;value<=2;value+=2){commit_result=value;reject("epoch rejection");}
        assert(commits==2 && copied==2 && !aborts && !memcmp(output,input,192));
    } else if(!strcmp(argv[1],"wrong-return")) {
        wrong_result=1;reject("did not return destination");assert(aborts==1 && !commits && copied==1);
    } else if(!strcmp(argv[1],"fault")) {
        Module *sdk=&state.modules[5];assert(!mprotect(sdk->base+0x338000,PAGE,PROT_READ|PROT_WRITE));
        const uint32_t code[]={0xd2800009,0x39400129,0xd65f03c0}; /* owned null ldr, return */
        memcpy(sdk->base+0x33af80,code,sizeof code);
        __builtin___clear_cache((char*)sdk->base+0x33af80,(char*)sdk->base+0x33af8c);
        assert(!mprotect(sdk->base+0x338000,PAGE,PROT_READ|PROT_EXEC));reject("signal=");
        assert(preflights==1 && aborts==1 && !commits && !copied && output[0]==0xa5);
    } else assert(!"unknown fixture");
    assert(!cleanup());puts("RUNTIME_TEXTURE_MOVE_VERIFIED");return 0;
}
'''

ABI = r'''
.text
.globl _owned_move_abi
.p2align 2
_owned_move_abi:
 stp x29,x30,[sp,#-224]!
 mov x29,sp
 stp x19,x20,[sp,#16]
 stp x21,x22,[sp,#32]
 stp x23,x24,[sp,#48]
 stp x25,x26,[sp,#64]
 stp x27,x28,[sp,#80]
 stp d8,d9,[sp,#96]
 stp d10,d11,[sp,#112]
 stp d12,d13,[sp,#128]
 stp d14,d15,[sp,#144]
 str x6,[sp,#160]
 mov x16,x3
 mov x17,x4
 mov x23,x5
 mov x19,#19
 adr x20,1f
 mov x21,#21
 mov x22,#22
 mov x24,#24
 mov x25,#25
 mov x26,#26
 mov x27,#27
 mov x28,#28
 mov x9,#8
 fmov d8,x9
 mov x9,#9
 fmov d9,x9
 mov x9,#10
 fmov d10,x9
 mov x9,#11
 fmov d11,x9
 mov x9,#12
 fmov d12,x9
 mov x9,#13
 fmov d13,x9
 mov x9,#14
 fmov d14,x9
 mov x9,#15
 fmov d15,x9
 br x17
1:
 ldr x9,[sp,#160]
 str x0,[x9]
 stp x19,x21,[x9,#8]
 stp x22,x23,[x9,#24]
 stp x24,x25,[x9,#40]
 stp x26,x27,[x9,#56]
 str x28,[x9,#72]
 adr x10,1b
 cmp x20,x10
 cset x10,eq
 str x10,[x9,#80]
 stp d8,d9,[x9,#88]
 stp d10,d11,[x9,#104]
 stp d12,d13,[x9,#120]
 stp d14,d15,[x9,#136]
 mov x10,sp
 and x10,x10,#15
 str x10,[x9,#152]
 ldp x19,x20,[sp,#16]
 ldp x21,x22,[sp,#32]
 ldp x23,x24,[sp,#48]
 ldp x25,x26,[sp,#64]
 ldp x27,x28,[sp,#80]
 ldp d8,d9,[sp,#96]
 ldp d10,d11,[sp,#112]
 ldp d12,d13,[sp,#128]
 ldp d14,d15,[sp,#144]
 ldp x29,x30,[sp],#224
 ret
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'requires the actual Apple ARM64 runtime')
class RuntimeTextureMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-runtime-texture-move-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        source = folder / 'move.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        assembly = folder / 'abi.S'
        assembly.write_text(ABI)
        cls.executable = folder / 'move'
        sources = [source,assembly, *(ROOT / 'native/botw_host' / name for name in
                   ('services.c', 'kernel.c', 'hipc_address.c', 'address_space.c', 'entry.S')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name for name in
                   ('romfs.c', 'savefs.c', 'local_user.c'))]
        command = ['xcrun','clang','-std=c11','-O2','-Wall','-Wextra','-Werror',
                   '-framework','Security',*map(str,sources),'-o',str(cls.executable)]
        result = subprocess.run(command,
                                capture_output=True,text=True)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)
        cls.no_providers = folder / 'no-providers'
        command[2:2] = ['-DBOTW_TEST_NO_MOVE_PROVIDER=1']
        command[-1] = str(cls.no_providers)
        result = subprocess.run(command,capture_output=True,text=True)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)

    def test_unknown_loaded_context_is_rejected_before_import_interposition(self):
        result = subprocess.run([str(self.executable),'unknown-context'],
                                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout + result.stderr)
        self.assertIn('RUNTIME_TEXTURE_MOVE_CONTEXT_REJECTED',result.stdout)

    def check(self, mode, executable=None):
        result = subprocess.run([str(executable or self.executable),mode],
                                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout + result.stderr)
        self.assertIn('RUNTIME_TEXTURE_MOVE_VERIFIED',result.stdout)

    def test_all_nonmatching_calls_delegate_original_without_new_recovery_or_checks(self): self.check('bypass')
    def test_exact_context_copies_outside_graphics_before_single_commit(self): self.check('success')
    def test_actual_arm64_entry_captures_lr_ownership_and_preserves_callee_saved_state(self): self.check('abi')
    def test_each_missing_provider_rejects_before_preflight_or_copy(self): self.check('missing')
    def test_absent_weak_definitions_link_and_are_detected_under_optimization(self): self.check('missing-real',self.no_providers)
    def test_only_exact_preflight_success_starts_original_copy(self): self.check('preflight')
    def test_commit_rejection_keeps_original_copied_bytes_and_closes_token(self): self.check('commit')
    def test_abnormal_original_return_aborts_instead_of_committing(self): self.check('wrong-return')
    def test_real_owned_a64_copy_fault_aborts_token_and_preserves_outer_recovery(self): self.check('fault')
    def test_each_context_digest_range_and_return_boundary_is_checked(self): self.check('profile')
    def test_transfer_success_trace_is_scalar_only_and_capped_at_four(self): self.check('trace')
    def test_actual_kernel_pending_worker_keeps_sdk_mapping_until_join(self): self.check('pin')


if __name__ == '__main__': unittest.main()
