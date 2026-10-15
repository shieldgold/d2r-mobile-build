"""Real A64 faults preserve the interrupted frame, outside the signal stack."""
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
import unittest

import test_botw_runtime as fixture

ROOT = fixture.ROOT
DRIVER = r'''
#include <mach/mach.h>
kern_return_t diagnostic_task_info(task_name_t, task_flavor_t, task_info_t,
                                         mach_msg_type_number_t *);
#define task_info diagnostic_task_info
#include "RUNTIME_SOURCE"
#undef task_info
#include <assert.h>
#include <limits.h>
static uint64_t evidence[3]; /* Guest stores actual FP, return PC and SP before fault. */
static char fault_text[512], memory_text[2048];
static unsigned memory_calls;
static int memory_mode;
static void collect(const char *message) {
    stack_t stack; assert(!sigaltstack(NULL, &stack));
    assert(!(stack.ss_flags & SS_ONSTACK)); /* Formatting/reporting is after recovery. */
    if (strstr(message, "CPU fault signal=")) snprintf(fault_text, sizeof fault_text, "%s", message);
    if (strstr(message, "fault_memory_snapshot")) {
        snprintf(memory_text, sizeof memory_text, "%s", message); memory_calls++;
    }
    puts(message);
}
kern_return_t diagnostic_task_info(task_name_t task, task_flavor_t flavor,
        task_info_t data, mach_msg_type_number_t *count) {
    stack_t stack; assert(!sigaltstack(NULL, &stack)); assert(!(stack.ss_flags & SS_ONSTACK));
    assert(task == mach_task_self() && flavor == TASK_VM_INFO);
    assert(*count == TASK_VM_INFO_COUNT);
    if (!memory_mode) return task_info(task, flavor, data, count);
    /* Poison every unsupported field: short records must not expose it. */
    task_vm_info_t vm = (task_vm_info_t)data; memset(vm, 0xa5, sizeof *vm);
    vm->virtual_size=123; vm->resident_size=456; vm->phys_footprint=789; vm->limit_bytes_remaining=987;
    if (memory_mode == 1) { *count=TASK_VM_INFO_COUNT; return KERN_FAILURE; }
    *count = memory_mode == 2 ? 0 : memory_mode == 3 ? TASK_VM_INFO_REV0_COUNT :
             memory_mode == 4 ? TASK_VM_INFO_REV1_COUNT : TASK_VM_INFO_REV4_COUNT;
    return KERN_SUCCESS;
}
static void check_context(const char *text) {
    assert(evidence[0] && evidence[1] && evidence[2]);
    const char *names[]={"fp", "lr", "sp"};
    for (unsigned i=0;i<3;i++) {
        char expected[48]; snprintf(expected,sizeof expected," %s=0x%llx",names[i],(unsigned long long)evidence[i]);
        if (!strstr(text,expected)) { fprintf(stderr,"missing %s in %s\n",expected,text); abort(); }
    }
    assert(strstr(text,"address=0x9") && strstr(text,"offset=0x438"));
}
static void prepare_image(const char *root) {
    char path[4096]; snprintf(path,sizeof path,"%s/botw/sdk.patched.bin",root);
    int fd=open(path,O_WRONLY); assert(fd>=0);
    uintptr_t address=(uintptr_t)evidence;
    assert(pwrite(fd,&address,sizeof address,0x480)==sizeof address); assert(!close(fd));
}
static void setup(const char *root) {
    state.owner=pthread_self(); state.event=collect;
    install_signal_boundary(); state.active=1;
    for (unsigned i=0;i<MODULE_COUNT;i++) {
        Module *m=&state.modules[i]; m->name=module_names[i];
        read_layout(m,root); map_module(m,root);
    }
    initialize_keys();
}
static void *worker(void *opaque) {
    (void)opaque; char error[512];
    assert(execute_child(NULL,(uintptr_t)state.modules[5].base+0x400,0,0,0x20001,error,sizeof error)==-1);
    check_context(error); assert(!state.fault_signal); puts("CHILD_REAL_FAULT_CONTEXT"); return NULL;
}
int main(int argc,char **argv) {
    assert(argc==3); prepare_image(argv[1]);
    if (!strcmp(argv[2],"capacity")) {
        volatile uintptr_t regs[32]; for(unsigned i=0;i<32;i++) regs[i]=UINTPTR_MAX;
        char text[513]; memset(text,0xa5,sizeof text);
        describe_fault(text,512,"graphics callback ",INT_MIN,UINTPTR_MAX,UINTPTR_MAX,regs);
        assert(strlen(text)<511 && (unsigned char)text[512]==0xa5);
        assert(strstr(text,"fp=0xffffffffffffffff lr=0xffffffffffffffff sp=0xffffffffffffffff"));
        puts("MAXIMUM_DIAGNOSTIC_FITS_512"); return 0;
    }
    if (!strncmp(argv[2],"memory-",7)) memory_mode=atoi(argv[2]+7);
    if (!strcmp(argv[2],"owner") || memory_mode) {
        assert(botw_host_run_mode(argv[1],argv[1],collect,BOTW_HOST_SDK_PROBE)==1);
        check_context(fault_text); assert(memory_calls==1);
        if (memory_mode) {
            assert(strstr(memory_text,memory_mode>=3?"virtual_size=123 resident_size=456":"virtual_size=unavailable resident_size=unavailable"));
            assert(strstr(memory_text,memory_mode>=4?"phys_footprint=789":"phys_footprint=unavailable"));
            assert(strstr(memory_text,memory_mode>=5?"limit_bytes_remaining=987":"limit_bytes_remaining=unavailable"));
        }
        puts("OWNER_REAL_FAULT_CONTEXT"); return 0;
    }
    setup(argv[1]);
    if (!strcmp(argv[2],"child")) {
        pthread_t thread; assert(!pthread_create(&thread,NULL,worker,NULL)); assert(!pthread_join(thread,NULL));
        assert(memory_calls==1);
    } else {
        assert(!strcmp(argv[2],"callback"));
        CallbackContext *context=calloc(1,sizeof *context); assert(context);
        callback_context=context; context->active=1; state.guest_executing=1;
        if (!sigsetjmp(context->recovery,1)) {
            ((void (*)(void))(state.modules[5].base+0x400))(); assert(!"fault returned");
        }
        assert(context->fault_signal && !state.fault_signal);
        describe_fault(fault_text,sizeof fault_text,"graphics callback ",context->fault_signal,
            context->fault_pc,context->fault_address,context->fault_registers);
        check_context(fault_text); collect(fault_text);
        callback_context=NULL; free(context); state.guest_executing=0;
        puts("CALLBACK_REAL_FAULT_CONTEXT");
    }
    assert(!cleanup()); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'fault ucontext tests require Apple ARM64')
class RuntimeFaultContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-fault-context-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'fault.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = Path(cls.build.name) / 'fault'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-framework', 'Security', '-O2',
                                 '-Wall', '-Wextra', '-Werror', *(str(p) for p in sources),
                                 '-o', str(cls.exe)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def run_case(self, mode):
        with tempfile.TemporaryDirectory(prefix='botw-fault-images-') as directory:
            root = Path(directory)
            for name in fixture.MODULES:
                fixture.image(root, name)
            path = root / 'botw/sdk.patched.bin'
            raw = bytearray(path.read_bytes())
            # Save the interrupted values before a real NULL+9 read. The nested
            # BL makes LR different from FP/SP and from the host return address.
            words = (0x58000400, 0xa9be7bfd, 0x910003fd, 0xf900001d,
                     0x910003e9, 0xf9000809, 0x10000069, 0xf9000409,
                     0x94000004, 0xa8c27bfd, 0xd65f03c0, 0xd503201f,
                     0xaa0003e1, 0xd2800000, 0x39402409, 0xd65f03c0)
            struct.pack_into('<I', raw, 0x100, 0x140000c0)
            struct.pack_into('<' + 'I' * len(words), raw, 0x400, *words)
            path.write_bytes(raw)
            result = subprocess.run([str(self.exe), str(root), mode], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_owner_real_fault_reports_interrupted_fp_lr_sp_and_actual_memory(self):
        output = self.run_case('owner')
        self.assertIn('OWNER_REAL_FAULT_CONTEXT', output)
        self.assertIn('fault_memory_snapshot', output)
        self.assertNotIn('virtual_size=unavailable', output)

    def test_real_pthread_child_retains_its_own_interrupted_context(self):
        self.assertIn('CHILD_REAL_FAULT_CONTEXT', self.run_case('child'))

    def test_callback_fault_does_not_replace_owner_context(self):
        self.assertIn('CALLBACK_REAL_FAULT_CONTEXT', self.run_case('callback'))

    def test_maximum_register_values_and_longest_scope_fit_existing_error_capacity(self):
        self.assertIn('MAXIMUM_DIAGNOSTIC_FITS_512', self.run_case('capacity'))

    def test_failed_task_info_does_not_expose_poisoned_fields_or_mask_fault(self):
        self.run_case('memory-1')

    def test_short_task_info_does_not_read_unreturned_fields(self):
        self.run_case('memory-2')

    def test_rev0_task_info_exposes_only_virtual_and_resident_sizes(self):
        self.run_case('memory-3')

    def test_rev1_task_info_exposes_footprint_without_limit(self):
        self.run_case('memory-4')

    def test_rev4_task_info_exposes_remaining_limit(self):
        self.run_case('memory-5')


if __name__ == '__main__':
    unittest.main()
