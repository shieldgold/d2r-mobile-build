"""Explicit original logical pool, real sparse VM, and fresh host admission."""
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
import unittest

import test_botw_runtime as runtime_fixture
import test_botw_runtime_callbacks as callback_fixture

ROOT = runtime_fixture.ROOT
KERNEL = r'''
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE
#include <sys/mman.h>
#include <errno.h>
static int reject_protection;
static int observed_mprotect(void *address, size_t size, int protection) {
    if (reject_protection) { reject_protection=0; errno=ENOMEM; return -1; }
    return mprotect(address,size,protection);
}
#define mprotect observed_mprotect
#include "KERNEL_SOURCE"
#undef mprotect
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
static int (*logical_memory_api)(botw_kernel *,size_t,size_t,size_t,size_t,size_t,char *,size_t);
#define botw_kernel_configure_logical_memory logical_memory_api
static char error[512];
static unsigned admissions;
static int denied;
static int admit(void *opaque,size_t previous,size_t requested,char *message,size_t cap) {
    (void)opaque; assert(requested>=previous); admissions++;
    if (denied) { snprintf(message,cap,"fresh host dirty headroom denied"); return -1; }
    return 0;
}
static botw_kernel *create(int with_guard) {
    botw_kernel_callbacks cb={.heap_admission=with_guard?admit:NULL};
    botw_kernel *k=botw_kernel_create(&cb,NULL); assert(k);
    assert(!botw_kernel_configure_address_space(k,39,error,sizeof error)); return k;
}
static uint64_t info(botw_kernel *k,unsigned id) {
    uint64_t r[32]={0,id,BOTW_KERNEL_CURRENT_PROCESS};
    assert(botw_kernel_dispatch(k,0x29,r,error,sizeof error)==BOTW_KERNEL_HANDLED && !r[0]); return r[1];
}
static void resize(botw_kernel *k,size_t size,uint32_t expected) {
    uint64_t r[32]={0,size}; assert(botw_kernel_dispatch(k,1,r,error,sizeof error)==BOTW_KERNEL_HANDLED);
    assert(r[0]==expected);
}
static void configure(botw_kernel *k,size_t capacity,size_t pool,size_t image) {
    assert(!botw_kernel_configure_logical_memory(k,capacity,256ul<<20,512ul<<20,pool,image,error,sizeof error));
}
int main(int argc,char **argv) {
    assert(argc==2);
    logical_memory_api=dlsym(RTLD_DEFAULT,"botw_kernel_configure_logical_memory");
    assert(botw_kernel_configure_logical_memory && "missing explicit logical memory policy implementation");
    const size_t unit=0x200000,image=55754752,H=0xc2600000;
    botw_kernel *k=create(strcmp(argv[1],"missing-guard")!=0);
    if (!strcmp(argv[1],"large")) {
        configure(k,4ul<<30,image+H,image); assert(admissions==1);
        uint64_t heap=info(k,4),base=0,size=0;
        assert(info(k,5)==4ul<<30 && info(k,6)==image+H && info(k,21)==image+H);
        assert(info(k,7)==image && info(k,22)==image);
        resize(k,H,0); assert(admissions==2);
        assert(botw_kernel_reservation(k,&base,&size) && size==(4ul<<30)+(768ul<<20));
        assert(heap>=base && heap+H<=base+size && info(k,7)==image+H && info(k,22)==image+H);
        unsigned char *p=(void *)(uintptr_t)heap; p[0]=0x39; p[H-1]=0x71;
        assert(p[0]==0x39 && p[H-1]==0x71);
        unsigned char resident=0; size_t page=(size_t)sysconf(_SC_PAGESIZE);
        assert(!mincore(p+((H/2)&~(page-1)),page,(void *)&resident) && !(resident&1));
        botw_kernel_memory_info region; assert(botw_kernel_query_memory(k,heap+H-1,&region)==1);
        assert(region.base==heap && region.size==H && region.permission==3);
        assert(!botw_kernel_memory(k,heap+H,1,0));
        resize(k,H+unit,BOTW_KERNEL_RESULT_OUT_OF_MEMORY); assert(info(k,7)==image+H && admissions==2);
    } else if (!strcmp(argv[1],"missing-guard") || !strcmp(argv[1],"configuration-denied")) {
        denied=1;
        assert(botw_kernel_configure_logical_memory(k,unit*4,unit*2,unit,unit*5,unit,error,sizeof error)==-1);
        uint64_t base,size; assert(!botw_kernel_reservation(k,&base,&size));
        assert(admissions==(!strcmp(argv[1],"configuration-denied")?1u:0u));
    } else if (!strcmp(argv[1],"growth-denied") || !strcmp(argv[1],"vm-failure")) {
        configure(k,unit*4,unit*5,unit); uint64_t heap=info(k,4);
        resize(k,unit,0); *(unsigned char *)(uintptr_t)heap=0xa5;
        if (!strcmp(argv[1],"growth-denied")) denied=1; else reject_protection=1;
        uint64_t r[32]={0x1234,unit*2};
        assert(botw_kernel_dispatch(k,1,r,error,sizeof error)==BOTW_KERNEL_ERROR);
        assert(r[0]==0x1234 && r[1]==unit*2 && info(k,7)==unit*2);
        assert(*(unsigned char *)(uintptr_t)heap==0xa5 && !botw_kernel_memory(k,heap+unit,1,0));
        denied=0; resize(k,unit*2,0); assert(info(k,7)==unit*3);
    } else if (!strcmp(argv[1],"legacy")) {
        assert(!botw_kernel_configure_memory(k,unit*4,unit*2,unit,unit*3,unit,error,sizeof error));
        assert(!admissions && info(k,6)==unit*3); resize(k,unit*3,BOTW_KERNEL_RESULT_OUT_OF_MEMORY);
        resize(k,unit*2,0); assert(!admissions && info(k,7)==unit*3);
    } else if (!strcmp(argv[1],"pinned")) {
        configure(k,unit*4,unit*5,unit); uint64_t heap=info(k,4); resize(k,unit*2,0);
        size_t page=(size_t)sysconf(_SC_PAGESIZE);
        uint64_t r[32]={0,heap+unit,page,0};
        int rc=botw_kernel_dispatch(k,0x15,r,error,sizeof error);
#ifdef __APPLE__
        assert(rc==BOTW_KERNEL_HANDLED && !r[0]); uint32_t h=(uint32_t)r[1];
        resize(k,unit,BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE);
        assert(!botw_kernel_close_handle(k,h,error,sizeof error)); resize(k,unit,0);
#else
        assert(rc==BOTW_KERNEL_UNSUPPORTED);
#endif
    } else assert(!"unknown mode");
    botw_kernel_destroy(k); puts("LOGICAL_POOL_CONTRACT_OK"); return 0;
}
'''

RUNTIME = r'''
#include <mach/mach.h>
#include <pthread.h>
static int observed_focus_wait(pthread_cond_t *,pthread_mutex_t *);
kern_return_t admission_task_info(task_name_t,task_flavor_t,task_info_t,mach_msg_type_number_t *);
#define task_info admission_task_info
#define pthread_cond_wait observed_focus_wait
#include "RUNTIME_SOURCE"
#undef task_info
#undef pthread_cond_wait
#include <assert.h>
static int (*memory_policy_api)(enum botw_host_memory_policy);
#define botw_host_set_memory_policy memory_policy_api
static unsigned task_calls,available_calls;
static kern_return_t task_result=KERN_SUCCESS;
static mach_msg_type_number_t task_count=TASK_VM_INFO_COUNT;
static size_t live_available=(size_t)UINT64_C(2156622592);
static uint64_t live_limit=UINT64_C(2156622592);
static _Atomic unsigned entered_focus_wait,focus_worker_done;
static int observed_focus_wait(pthread_cond_t *condition,pthread_mutex_t *mutex) {
    if (condition==&focus_changed) atomic_fetch_add(&entered_focus_wait,1);
    return pthread_cond_wait(condition,mutex);
}
static size_t available_memory(void) { available_calls++; return live_available; }
kern_return_t admission_task_info(task_name_t task,task_flavor_t flavor,task_info_t data,
        mach_msg_type_number_t *count) {
    assert(task==mach_task_self() && flavor==TASK_VM_INFO && *count==TASK_VM_INFO_COUNT);
    task_calls++; task_vm_info_t info=(task_vm_info_t)data;
    memset(info,0xa5,sizeof *info); info->phys_footprint=43289856;
    info->resident_size=179617792; info->virtual_size=425875734528;
    info->limit_bytes_remaining=live_limit; *count=task_count; return task_result;
}
static void guarded_setup(const char *root) {
    state.owner=pthread_self(); state.memory_policy=BOTW_HOST_MEMORY_ORIGINAL_LOGICAL;
    state.address_bits=39; state.host39_transport_audited=1;
    state.heap_policy_active=1; state.thread_stack_mode=4;
    dirty_available_query=available_memory;
    for(unsigned i=0;i<MODULE_COUNT;i++) {
        Module *m=&state.modules[i]; m->name=module_names[i];
        read_layout(m,root); map_module(m,root); parse_dynamic(m);
    }
    initialize_keys();
    const botw_kernel_callbacks callbacks={.heap_admission=kernel_heap_admission};
    state.kernel=botw_kernel_create(&callbacks,NULL); assert(state.kernel);
}
static void *child_guard(void *opaque) {
    (void)opaque; char error[512];
    assert(execute_child(NULL,(uintptr_t)state.modules[5].base+0x100,0,0,0x20001,error,sizeof error)==-1);
    assert(strstr(error,"memory admission at child_entry") && !state.fault_signal);
    return NULL;
}
static void *focus_guard(void *opaque) {
    (void)opaque; ChildContext *context=calloc(1,sizeof *context); assert(context);
    child_context=context; context->active=1;
    if (!sigsetjmp(context->recovery,1)) {
        uint64_t regs[32]={0}; botw_gate_dispatch(0x1234,regs); assert(!"focus gate unexpectedly returned");
    }
    assert(strstr(context->error,"memory admission") && !context->fault_signal);
    child_context=NULL; free(context); atomic_store(&focus_worker_done,1); return NULL;
}
static uint64_t clock_ns(void) {
    struct timespec value; assert(!clock_gettime(CLOCK_MONOTONIC,&value));
    return (uint64_t)value.tv_sec*1000000000+(uint64_t)value.tv_nsec;
}
int main(int argc,char **argv) {
    assert(argc==3);
    memory_policy_api=dlsym(RTLD_DEFAULT,"botw_host_set_memory_policy");
    assert(botw_host_set_memory_policy && "missing explicit host memory policy implementation");
    if (strcmp(argv[2],"public")) {
        char error[512]; HostDirtyMemory observation,unchanged;
        memset(&observation,0x69,sizeof observation); unchanged=observation;
        if (!strcmp(argv[2],"mac-unavailable")) {
            assert(!dirty_available_query && read_dirty_admission(&observation,error,sizeof error)==-1);
            assert(strstr(error,"unavailable on this host") && !task_calls && !available_calls);
            assert(!memcmp(&observation,&unchanged,sizeof observation));
        } else if (!strcmp(argv[2],"query-failures")) {
            dirty_available_query=available_memory;
            const mach_msg_type_number_t counts[]={0,TASK_VM_INFO_REV0_COUNT,TASK_VM_INFO_REV1_COUNT,
                TASK_VM_INFO_REV3_COUNT,TASK_VM_INFO_COUNT+1};
            for(unsigned i=0;i<sizeof counts/sizeof *counts;i++) {
                task_count=counts[i];
                assert(read_dirty_admission(&observation,error,sizeof error)==-1);
                assert(strstr(error,"task_info unavailable") && !available_calls);
                assert(!memcmp(&observation,&unchanged,sizeof observation));
            }
            task_count=TASK_VM_INFO_COUNT; task_result=KERN_FAILURE;
            assert(read_dirty_admission(&observation,error,sizeof error)==-1 && !available_calls);
        } else if (!strcmp(argv[2],"fresh-guard")) {
            dirty_available_query=available_memory;
            assert(!read_dirty_admission(&observation,error,sizeof error));
            assert(observation.footprint==43289856 && observation.virtual_size==425875734528);
            live_available=HEAP_POLICY_RESERVE-1;
            assert(read_dirty_admission(&observation,error,sizeof error)==-1);
            live_available=HEAP_POLICY_RESERVE; live_limit=HEAP_POLICY_RESERVE-1;
            assert(read_dirty_admission(&observation,error,sizeof error)==-1);
            live_limit=HEAP_POLICY_RESERVE;
            assert(!read_dirty_admission(&observation,error,sizeof error));
            assert(task_calls==4 && available_calls==4);
        } else if (!strcmp(argv[2],"parameters")) {
            guarded_setup(argv[1]); configure_kernel_memory(); assert(task_calls==2 && available_calls==2);
            call_entry("nninitStartup",0);
            assert(state.heap_size==0xc2600000 && state.root_size==0xc0600000);
            assert(pthread_getspecific(state.keys[4])==(void *)(uintptr_t)0xc2600000);
            assert(pthread_getspecific(state.keys[5])==(void *)(uintptr_t)0xc0600000);
            state.gates[0]=(Gate){.ret=0x1234,.arg=1}; state.gate_count=1;
            state.current_entry="nninitStartup"; state.guest_executing=1;
            uint64_t regs[32]={0,0xc2600000}; botw_gate_dispatch(0x1234,regs);
            assert(!regs[0] && state.heap_initial_sized);
            state.current_entry=NULL; state.guest_executing=0;
            call_entry("nnMain",0);
            assert(allocator_kernel_info(6)==state.image_bytes+0xc2600000 &&
                allocator_kernel_info(7)==allocator_kernel_info(6));
            assert(task_calls==5 && available_calls==5); assert(!cleanup());
        } else if (!strcmp(argv[2],"constructor-refusal") || !strcmp(argv[2],"constructor-bounded")) {
            guarded_setup(argv[1]);
            if (!strcmp(argv[2],"constructor-bounded")) state.memory_policy=BOTW_HOST_MEMORY_BOUNDED;
            configure_kernel_memory(); live_available=0;
            state.modules[5].init=0x300; uint32_t *marker=(void *)(state.modules[5].base+0x8220);
            assert(!*marker); state.active=1;
            if (!sigsetjmp(state.recovery,1)) {
                run_module_constructors();
                if (state.memory_policy==BOTW_HOST_MEMORY_ORIGINAL_LOGICAL) {
                    fprintf(stderr,"constructor marker after refused headroom=%u\n",*marker);
                    assert(!*marker && "constructor executed before fresh admission");
                    assert(!"constructor admission refusal returned");
                }
                assert(state.memory_policy==BOTW_HOST_MEMORY_BOUNDED && *marker==1 && !task_calls);
            } else assert(state.memory_policy==BOTW_HOST_MEMORY_ORIGINAL_LOGICAL &&
                !*marker && !state.guest_started && atomic_load(&memory_policy_stopping));
            assert(!cleanup());
        } else if (!strcmp(argv[2],"focus-refusal")) {
            guarded_setup(argv[1]); configure_kernel_memory();
            pthread_mutex_lock(&focus_lock); focus_suspended=1; focus_stopping=0; pthread_mutex_unlock(&focus_lock);
            pthread_t thread; assert(!pthread_create(&thread,NULL,focus_guard,NULL));
            uint64_t end=clock_ns()+1000000000;
            while (!atomic_load(&entered_focus_wait) && clock_ns()<end) sched_yield();
            assert(atomic_load(&entered_focus_wait));
            /* The worker holds this mutex until its real cond_wait releases it. */
            pthread_mutex_lock(&focus_lock); assert(focus_suspended && !focus_stopping); pthread_mutex_unlock(&focus_lock);
            live_available=HEAP_POLICY_RESERVE-1;
            assert(kernel_heap_admission(NULL,0,0,error,sizeof error)==-1);
            end=clock_ns()+1000000000;
            while (!atomic_load(&focus_worker_done) && clock_ns()<end) sched_yield();
            assert(atomic_load(&focus_worker_done) && "memory-stop did not wake the actual suspended pthread");
            assert(!pthread_join(thread,NULL));
            assert(focus_suspended && !focus_stopping && !state.fault_signal); assert(!cleanup());
        } else if (!strcmp(argv[2],"entry-refusal") || !strcmp(argv[2],"child-refusal")) {
            guarded_setup(argv[1]); configure_kernel_memory(); live_available=HEAP_POLICY_RESERVE-1;
            if (!strcmp(argv[2],"child-refusal")) {
                pthread_t thread; assert(!pthread_create(&thread,NULL,child_guard,NULL)); assert(!pthread_join(thread,NULL));
                assert(atomic_load(&memory_policy_stopping) && !state.fault_signal);
            }
            state.active=1; state.guest_executing=1;
            if (!sigsetjmp(state.recovery,1)) {
                if (!strcmp(argv[2],"entry-refusal")) call_entry("nnMain",0);
                else { uint64_t regs[32]={0}; botw_gate_dispatch(0x5678,regs); }
                assert(!"fresh admission refusal returned to guest");
            }
            assert(atomic_load(&memory_policy_stopping) && !state.fault_signal);
            assert(!cleanup());
        } else assert(!"unknown mode");
        puts("FRESH_ADMISSION_BOUNDARY_OK"); return 0;
    }
    assert(!botw_host_set_memory_policy(BOTW_HOST_MEMORY_BOUNDED));
    assert(botw_host_set_memory_policy((enum botw_host_memory_policy)99)==1);
    assert(!pthread_mutex_lock(&run_lock));
    assert(botw_host_set_memory_policy(BOTW_HOST_MEMORY_ORIGINAL_LOGICAL)==2);
    assert(!pthread_mutex_unlock(&run_lock));
    assert(!botw_host_set_memory_policy(BOTW_HOST_MEMORY_ORIGINAL_LOGICAL));
    assert(botw_host_run_mode(argv[1],argv[1],NULL,BOTW_HOST_SDK_PROBE)==1);
    assert(!botw_host_set_memory_policy(BOTW_HOST_MEMORY_BOUNDED));
    assert(botw_host_run_mode(argv[1],argv[1],NULL,BOTW_HOST_SDK_PROBE)==0);
    puts("EXPLICIT_RUNTIME_POLICY_ISOLATED"); return 0;
}
'''


class LogicalKernelHeapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-logical-kernel-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'kernel.c'
        source.write_text(KERNEL.replace('KERNEL_SOURCE', str(ROOT / 'native/botw_host/kernel.c')))
        cls.exe = Path(cls.build.name) / 'kernel'
        export = '-Wl,-export_dynamic' if platform.system() == 'Darwin' else '-rdynamic'
        result = subprocess.run(['cc', export, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                                 str(source), '-pthread', '-o', str(cls.exe)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check(self, mode):
        result = subprocess.run([str(self.exe), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('LOGICAL_POOL_CONTRACT_OK', result.stdout)

    def test_exact_original_heap_is_real_sparse_vm_with_one_logical_account(self): self.check('large')
    def test_logical_policy_requires_admission_callback(self): self.check('missing-guard')
    def test_fresh_configuration_refusal_does_not_publish_a_policy(self): self.check('configuration-denied')
    def test_growth_refusal_preserves_old_vm_and_charge(self): self.check('growth-denied')
    def test_real_vm_error_does_not_publish_a_successful_heap_size(self): self.check('vm-failure')
    def test_original_configuration_keeps_its_old_budget_semantics(self): self.check('legacy')
    def test_logical_policy_keeps_transfer_memory_shrink_protection(self): self.check('pinned')


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'runtime policy requires Apple ARM64')
class LogicalRuntimeHeapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-logical-runtime-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'runtime.c'
        source.write_text(RUNTIME.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = Path(cls.build.name) / 'runtime'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-Wl,-export_dynamic', '-framework', 'Security', '-O2', '-Wall', '-Wextra',
                                 '-Werror', *(str(p) for p in sources), '-o', str(cls.exe)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def check(self, mode):
        with tempfile.TemporaryDirectory(prefix='botw-logical-images-') as directory:
            root = Path(directory)
            for name in runtime_fixture.MODULES:
                runtime_fixture.image(root, name)
            path=root/'botw/sdk.patched.bin'; raw=bytearray(path.read_bytes())
            # An actual mapped A64 constructor writes one marker only if entered.
            struct.pack_into('<4I',raw,0x300,0x1003f908,0x52800029,0xb9000109,0xd65f03c0)
            path.write_bytes(raw)
            result = subprocess.run([str(self.exe), str(root), mode], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_public_policy_is_explicit_active_safe_and_rejects_unknown_images(self):
        self.assertIn('EXPLICIT_RUNTIME_POLICY_ISOLATED', self.check('public'))

    def test_mac_does_not_substitute_physical_ram_for_missing_dirty_limit(self):
        self.check('mac-unavailable')

    def test_failed_short_and_oversized_public_records_fail_closed(self):
        self.check('query-failures')

    def test_each_guard_uses_fresh_minimum_of_both_public_headroom_readings(self):
        self.check('fresh-guard')

    def test_original_tsd_parameters_and_real_heap_share_one_pool_account(self):
        self.check('parameters')

    def test_primary_entry_refuses_low_headroom_before_guest_execution(self):
        self.check('entry-refusal')

    def test_real_child_entry_refusal_stops_other_gates_without_cross_thread_longjmp(self):
        self.check('child-refusal')

    def test_constructor_refuses_low_headroom_before_its_real_marker_write(self):
        self.check('constructor-refusal')

    def test_default_bounded_constructor_does_not_gain_a_new_host_limit_check(self):
        self.check('constructor-bounded')

    def test_guard_failure_wakes_a_pthread_already_waiting_for_focus(self):
        self.check('focus-refusal')


def graphics_callback_driver():
    """Reuse the existing real callback boundary; graphics linkage stays simulated."""
    driver = callback_fixture.DRIVER
    original = '#define BOTW_WITH_GRAPHICS 1\n#include "RUNTIME_SOURCE"\n'
    replacement = r'''
#include <mach/mach.h>
kern_return_t callback_task_info(task_name_t,task_flavor_t,task_info_t,mach_msg_type_number_t *);
#define task_info callback_task_info
#define BOTW_WITH_GRAPHICS 1
#include "RUNTIME_SOURCE"
#undef task_info
static size_t callback_available=0x40000000;
static unsigned callback_task_calls,callback_available_calls;
static size_t callback_live_available(void) { callback_available_calls++; return callback_available; }
kern_return_t callback_task_info(task_name_t task,task_flavor_t flavor,task_info_t data,
        mach_msg_type_number_t *count) {
    assert(task==mach_task_self() && flavor==TASK_VM_INFO && *count==TASK_VM_INFO_COUNT);
    callback_task_calls++; task_vm_info_t info=(task_vm_info_t)data;
    memset(info,0,sizeof *info); info->limit_bytes_remaining=0x40000000;
    *count=TASK_VM_INFO_COUNT; return KERN_SUCCESS;
}
'''
    # assert.h must precede the inserted query provider, rather than relying
    # on the existing driver's later include.
    replacement = '#include <assert.h>\n' + replacement
    assert driver.count(original) == 1
    driver = driver.replace(original, replacement)
    original = 'if (!strcmp(argv[1], "arguments")) check_good();'
    replacement = r'''
if (!strcmp(argv[1], "logical-callback")) {
    state.memory_policy=BOTW_HOST_MEMORY_ORIGINAL_LOGICAL;
    dirty_available_query=callback_live_available;
    Arguments args={&command_object,1,0,128}; char error[512];
    assert(graphics_invoke_memory_callback(good,&command_object,1,128,&args,error,sizeof error));
    assert(args.calls==1 && callback_task_calls==1 && callback_available_calls==1); check_outer();
    callback_available=HEAP_POLICY_RESERVE-1; args.calls=0;
    assert(!graphics_invoke_memory_callback(good,&command_object,1,128,&args,error,sizeof error));
    assert(strstr(error,"memory admission at guest_callback") && !args.calls);
    assert(callback_task_calls==2 && callback_available_calls==2); check_outer();
    callback_available=0x40000000;
    assert(!graphics_invoke_memory_callback(good,&command_object,1,128,&args,error,sizeof error));
    assert(strstr(error,"stopped") && !args.calls && callback_task_calls==2 && callback_available_calls==2);
    check_outer(); state.memory_policy=BOTW_HOST_MEMORY_BOUNDED; check_good();
    puts("ACTUAL_SYNC_CALLBACK_FRESH_ADMISSION_AND_OUTER_RECOVERY");
} else if (!strcmp(argv[1], "arguments")) check_good();
'''
    assert driver.count(original) == 1
    return driver.replace(original, replacement)


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'actual guest callback recovery requires Apple ARM64')
class LogicalGraphicsCallbackTests(unittest.TestCase):
    def test_graphics_enabled_callback_checks_fresh_headroom_before_actual_invocation(self):
        with tempfile.TemporaryDirectory(prefix='botw-logical-callback-') as temporary:
            folder=Path(temporary); source=folder/'callback.c'; executable=folder/'callback'
            source.write_text(graphics_callback_driver().replace('RUNTIME_SOURCE',str(ROOT/'native/botw_host/runtime.c')))
            sources=[source,*(ROOT/'native/botw_host'/name for name in
                     ('entry.S','services.c','kernel.c','hipc_address.c','address_space.c')),
                     ROOT/'native/botw_input/input.c',*(ROOT/'native/botw_resources'/name for name in
                     ('romfs.c','savefs.c','local_user.c'))]
            built=subprocess.run(['xcrun','clang','-O2','-Wall','-Wextra','-Werror','-framework','Security',
                                  *(str(p) for p in sources),'-o',str(executable)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            ran=subprocess.run([str(executable),'logical-callback'],capture_output=True,text=True,timeout=15)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)
            self.assertIn('ACTUAL_SYNC_CALLBACK_FRESH_ADMISSION_AND_OUTER_RECOVERY',ran.stdout)


if __name__ == '__main__':
    unittest.main()
