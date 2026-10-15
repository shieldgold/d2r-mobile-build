"""Real HID service handles and kernel RO aliases, without game assets."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#define _DARWIN_C_SOURCE 1
#define _DEFAULT_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "kernel.h"
#include "services.h"
#include "input.h"
#include <assert.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

enum { SHARED = 0x40000, DEBUG_BYTES = 0x400, DEBUG_RING_END = 0x2c8 };
static const uint64_t aruid = UINT64_C(0x12345678);
static botw_kernel *kernel;
static botw_services *services;
static botw_input *input;
static unsigned char ipc[256];
static uint32_t source_handle, copy_handle, hid, resource;
static unsigned char *backing;
static uint64_t alias;
static char error[512];

static uint32_t get32(const void *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint64_t get64(const void *p) { uint64_t v; memcpy(&v, p, 8); return v; }
static void put32(void *p, uint32_t v) { memcpy(p, &v, 4); }
static void put64(void *p, uint64_t v) { memcpy(p, &v, 8); }
static void require(int condition, const char *message) {
    if (!condition) { fprintf(stderr, "%s: %s\n", message, error); abort(); }
}
static void log_message(void *opaque, const char *message) { (void)opaque; (void)message; }
static int memory(void *opaque, uint64_t address, size_t length, int write) {
    (void)opaque; (void)write;
    return address >= (uintptr_t)ipc && address - (uintptr_t)ipc <= sizeof ipc &&
        length <= sizeof ipc - (address - (uintptr_t)ipc);
}
/* These adapters forward to the real kernel; no handles or mappings are mocked. */
static int event_create(void *k, int initial, uint32_t *w, uint32_t *r, char *e, size_t n) {
    return botw_kernel_create_event(k, initial, w, r, e, n);
}
static int duplicate(void *k, uint32_t h, uint32_t *out, char *e, size_t n) {
    return botw_kernel_duplicate_handle(k, h, out, e, n);
}
static int signal_event(void *k, uint32_t h, char *e, size_t n) {
    return botw_kernel_signal_event(k, h, e, n);
}
static int clear_event(void *k, uint32_t h, char *e, size_t n) {
    return botw_kernel_clear_event(k, h, e, n);
}
static int close_handle(void *k, uint32_t h, char *e, size_t n) {
    return botw_kernel_close_handle(k, h, e, n);
}
static int is_process(void *k, uint32_t h) { return botw_kernel_is_process_handle(k, h); }
static void svc(unsigned number, uint64_t regs[32]) {
    require(botw_kernel_dispatch(kernel, number, regs, error, sizeof error) == BOTW_KERNEL_HANDLED,
            "real kernel SVC dispatch");
}
static void close_guest(uint32_t handle) {
    uint64_t regs[32] = {handle}; svc(0x16, regs); require(!regs[0], "CloseHandle");
}
static void map(uint32_t handle, uint64_t address) {
    uint64_t regs[32] = {handle, address, SHARED, 1}; svc(0x13, regs);
    require(!regs[0], "MapSharedMemory Read");
    botw_kernel_memory_info region;
    assert(botw_kernel_query_memory(kernel, address, &region) == 1);
    assert(region.type == 6 && region.permission == 1 && region.size == SHARED);
    assert(botw_kernel_memory(kernel, address, SHARED, 0));
    assert(!botw_kernel_memory(kernel, address, SHARED, 1));
}
static void unmap(uint32_t handle, uint64_t address) {
    uint64_t regs[32] = {handle, address, SHARED}; svc(0x14, regs);
    require(!regs[0], "UnmapSharedMemory");
    assert(!botw_kernel_memory(kernel, address, SHARED, 0));
}
static void denied(int number) { (void)number; _exit(44); }
static int actual_access(uint64_t address, int write) {
    pid_t child = fork(); assert(child >= 0);
    if (!child) {
        signal(SIGBUS, denied); signal(SIGSEGV, denied);
        if (write) *(volatile unsigned char *)(uintptr_t)address = 0xff;
        else { volatile unsigned char value = *(volatile unsigned char *)(uintptr_t)address; (void)value; }
        _exit(0);
    }
    int status; assert(waitpid(child, &status, 0) == child);
    require(WIFEXITED(status), "permission probe child exited");
    return WEXITSTATUS(status) == 0;
}
static void request(unsigned command, const void *payload, size_t bytes, int pid) {
    assert(bytes <= 64); memset(ipc, 0, sizeof ipc);
    size_t start = pid ? 20 : 8, raw = (start + 15) & ~(size_t)15;
    size_t words = (16 + 16 + bytes + 3) / 4;
    put32(ipc, 4); put32(ipc + 4, (uint32_t)words | (pid ? UINT32_C(0x80000000) : 0));
    if (pid) { put32(ipc + 8, 1); put64(ipc + 12, 0); }
    memcpy(ipc + raw, "SFCI", 4); put32(ipc + raw + 8, command);
    if (bytes) memcpy(ipc + raw + 16, payload, bytes);
}
static void send_ok(uint32_t handle) {
    require(!botw_services_send(services, handle, ipc, sizeof ipc, error, sizeof error),
            "HID service request");
}
static uint32_t response_handle(int copy) {
    assert(get32(ipc + 4) >> 31);
    assert(get32(ipc + 8) == (copy ? 2u : 32u));
    assert(!memcmp(ipc + 16, "SFCO", 4) && !get32(ipc + 24));
    uint32_t result = get32(ipc + 12); assert(result); return result;
}
static uint32_t applet_resource(void) {
    request(0, &aruid, sizeof aruid, 1); send_ok(hid); return response_handle(0);
}
static uint32_t shared_handle(uint32_t r) {
    request(0, NULL, 0, 0); send_ok(r); return response_handle(1);
}
static void activate(void) {
    request(1, &aruid, sizeof aruid, 1); send_ok(hid);
    assert(!memcmp(ipc + 16, "SFCO", 4) && !get32(ipc + 24));
}
static void setup(void) {
    kernel = botw_kernel_create(NULL, NULL); assert(kernel);
    const size_t unit = 0x200000;
    require(!botw_kernel_configure_memory(kernel, unit, unit, unit, unit, 0, error, sizeof error),
            "configure actual kernel reservation");
    uint64_t regs[32] = {0, 2, BOTW_KERNEL_CURRENT_PROCESS}; svc(0x29, regs);
    require(!regs[0], "GetInfo AliasRegionAddress"); alias = regs[1];
    require(!botw_kernel_create_shared_memory(kernel, SHARED, 1, &source_handle,
            (void **)&backing, error, sizeof error), "create actual HID shared backing");
    input = botw_input_create(); assert(input);
    assert(!botw_input_bind_shared_memory(input, backing, SHARED));
    services = botw_services_create(log_message, memory, NULL); assert(services);
    botw_services_kernel_api api = {kernel, event_create, duplicate, signal_event,
                                    clear_event, close_handle, is_process};
    botw_services_set_kernel_api(services, &api);
    botw_services_set_input(services, input, source_handle);
    require(!botw_services_set_applet_state(services, aruid, 1, 1, 0, error, sizeof error),
            "publish actual applet resource identity");
    uint32_t sm; require(!botw_services_connect(services, "sm:", 3, &sm, error, sizeof error), "connect sm");
    uint64_t zero = 0; request(0, &zero, 8, 1); send_ok(sm);
    const unsigned char name[8] = {'h','i','d',0,0,0,0,0};
    request(1, name, sizeof name, 0); send_ok(sm); hid = response_handle(0);
    resource = applet_resource(); copy_handle = shared_handle(resource);
    assert(copy_handle != source_handle && copy_handle >= 0x20000);
    map(copy_handle, alias);
    size_t size = 0; assert(botw_input_shared_memory(input, &size) == backing && size == SHARED);
}
static void stop_services(void) {
    if (services) { botw_services_destroy(services); services = NULL; }
}
static void cleanup(void) {
    stop_services(); botw_input_unbind_shared_memory(input); botw_input_destroy(input); input = NULL;
    botw_kernel_destroy(kernel); kernel = NULL;
}
static void empty_debug(const unsigned char *p) {
    assert(get64(p + 8) == 17 && get64(p + 16) == 16 && get64(p + 24) == 0);
}
static uint64_t newest_npad(const unsigned char *p) {
    const unsigned char *r = p + BOTW_HID_NPAD_OFFSET + 0x28;
    uint64_t tail = get64(r + 16); assert(tail < 17);
    return get64(r + 32 + tail * 0x30 + 8);
}
static void debug_sample(const unsigned char *p, uint64_t sequence, uint64_t ticks) {
    assert(get64(p + 8) == 17);
    uint64_t tail = get64(p + 16), count = get64(p + 24);
    assert(tail == (sequence - 1) % 17 && count == (sequence < 16 ? sequence : 16));
    if (ticks) assert(get64(p) == ticks);
    for (uint64_t n = 0; n < count; n++) {
        const unsigned char *entry = p + 0x20 + ((tail + 17 - n) % 17) * 0x28;
        assert(get64(entry) == sequence - n && get64(entry + 8) == sequence - n);
        for (unsigned i = 0x10; i < 0x28; i++) assert(entry[i] == 0);
    }
}
static void activation(void) {
    setup();
    uint32_t id = 0; assert(!botw_input_configure(input, BOTW_NPAD_STYLE_FULLKEY, &id, 1));
    assert(!botw_input_set_source(input, BOTW_INPUT_GAMECONTROLLER, BOTW_BUTTON_A, 123, -234, 345, -456, 1));
    uint64_t npad_sequence = newest_npad((void *)(uintptr_t)alias);
    botw_input_sample(input, 40); assert(newest_npad((void *)(uintptr_t)alias) == ++npad_sequence);
    const unsigned char *ring = (void *)(uintptr_t)(alias + BOTW_HID_NPAD_OFFSET + 0x28);
    const unsigned char *state = ring + 32 + get64(ring + 16) * 0x30 + 8;
    assert(get64(state + 8) == BOTW_BUTTON_A && get32(state + 32) == BOTW_NPAD_ATTRIBUTE_CONNECTED);
    assert((int32_t)get32(state + 16) == 123 && (int32_t)get32(state + 28) == -456);
    unsigned char npad[BOTW_HID_NPAD_STRIDE];
    memcpy(npad, (void *)(uintptr_t)(alias + BOTW_HID_NPAD_OFFSET), sizeof npad);
    activate(); /* On the previous implementation this is a compiled unsupported-cmd1 RED. */
    assert(!memcmp(npad, (void *)(uintptr_t)(alias + BOTW_HID_NPAD_OFFSET), sizeof npad));
    debug_sample((void *)(uintptr_t)alias, 1, 0);
    assert(!memcmp(backing, (void *)(uintptr_t)alias, DEBUG_BYTES));
    unsigned char debug[DEBUG_BYTES]; memcpy(debug, backing, sizeof debug);
    activate(); assert(!memcmp(debug, backing, sizeof debug));
    request(1, &aruid, 8, 0);
    assert(botw_services_send(services, hid, ipc, sizeof ipc, error, sizeof error) == -1);
    assert(!memcmp(debug, backing, sizeof debug));
    assert(actual_access(alias, 0) && !actual_access(alias, 1));
    memset(backing + DEBUG_RING_END, 0xa5, DEBUG_BYTES - DEBUG_RING_END);
    memset(backing + DEBUG_BYTES, 0x5a, 64);
    for (uint64_t n = 2; n <= 43; n++) {
        if (n == 3) botw_input_set_active(input, 0);
        botw_input_sample(input, 1000 + n);
        debug_sample((void *)(uintptr_t)alias, n, 1000 + n);
        assert(newest_npad((void *)(uintptr_t)alias) == ++npad_sequence);
    }
    for (unsigned i = DEBUG_RING_END; i < DEBUG_BYTES; i++) assert(backing[i] == 0xa5);
    for (unsigned i = DEBUG_BYTES; i < DEBUG_BYTES + 64; i++) assert(backing[i] == 0x5a);
    cleanup();
}
static void references(int mapping_only) {
    setup(); activate(); debug_sample((void *)(uintptr_t)alias, 1, 0);
    uint32_t second_resource = applet_resource(), second = shared_handle(second_resource), retained;
    assert(second != copy_handle && second != source_handle); map(second, alias + SHARED);
    require(!botw_kernel_duplicate_handle(kernel, second, &retained, error, sizeof error), "real kernel duplicate");
    assert(retained != second && retained != copy_handle);
    close_guest(copy_handle); close_guest(second);
    uint64_t regs[32] = {copy_handle, alias + 2 * SHARED, SHARED, 1}; svc(0x13, regs);
    assert(regs[0] == BOTW_KERNEL_RESULT_INVALID_HANDLE);
    stop_services(); close_guest(source_handle);
    if (mapping_only) close_guest(retained);
    botw_input_sample(input, 202); debug_sample((void *)(uintptr_t)alias, 2, 202);
    debug_sample((void *)(uintptr_t)(alias + SHARED), 2, 202);
    assert(!memcmp(backing, (void *)(uintptr_t)alias, DEBUG_BYTES));
    unsigned char old[DEBUG_BYTES]; memcpy(old, backing, sizeof old);
    botw_input_unbind_shared_memory(input); botw_input_sample(input, 203);
    assert(!memcmp(old, (void *)(uintptr_t)alias, sizeof old));
    if (!mapping_only) {
        unmap(retained, alias); assert(!actual_access(alias, 0));
        debug_sample((void *)(uintptr_t)(alias + SHARED), 2, 202);
        unmap(retained, alias + SHARED); assert(!actual_access(alias + SHARED, 0));
        uint64_t host_address = (uintptr_t)backing;
        assert(actual_access(host_address, 0)); close_guest(retained);
        assert(!actual_access(host_address, 0));
    }
    cleanup();
    if (mapping_only) assert(!actual_access(alias, 0) && !actual_access(alias + SHARED, 0));
}
static void rebinding(void) {
    setup(); activate(); botw_input_sample(input, 302); debug_sample((void *)(uintptr_t)alias, 2, 302);
    unsigned char old[DEBUG_BYTES]; memcpy(old, backing, sizeof old);
    botw_input_unbind_shared_memory(input);
    unsigned char *owned = botw_input_shared_memory(input, NULL); assert(owned != backing); empty_debug(owned);
    botw_input_sample(input, 303); empty_debug(owned);
    assert(!memcmp(old, (void *)(uintptr_t)alias, sizeof old));
    assert(!botw_input_bind_shared_memory(input, backing, SHARED)); empty_debug((void *)(uintptr_t)alias);
    botw_input_sample(input, 304); empty_debug((void *)(uintptr_t)alias);
    assert(newest_npad((void *)(uintptr_t)alias) == 1);
    activate(); debug_sample((void *)(uintptr_t)alias, 1, 0);
    assert(newest_npad((void *)(uintptr_t)alias) == 1);
    memcpy(old, backing, sizeof old);
    uint32_t second_source; unsigned char *next;
    require(!botw_kernel_create_shared_memory(kernel, SHARED, 1, &second_source,
            (void **)&next, error, sizeof error), "create replacement kernel backing");
    assert(!botw_input_bind_shared_memory(input, next, SHARED)); empty_debug(next);
    botw_services_set_input(services, input, second_source);
    uint32_t next_resource = applet_resource(), next_copy = shared_handle(next_resource);
    map(next_copy, alias + SHARED); botw_input_sample(input, 305);
    empty_debug((void *)(uintptr_t)(alias + SHARED));
    assert(!memcmp(old, (void *)(uintptr_t)alias, sizeof old));
    activate(); debug_sample((void *)(uintptr_t)(alias + SHARED), 1, 0);
    botw_input_sample(input, 306); debug_sample((void *)(uintptr_t)(alias + SHARED), 2, 306);
    assert(!memcmp(old, (void *)(uintptr_t)alias, sizeof old));
    cleanup();
}
static void framing_padding(void) {
    setup(); request(1, &aruid, 8, 1);
    /* Original SDK semantics end at 56; raw-word framing extends to 60.
     * The TLS message buffer need not have initialized those last four bytes. */
    assert((get32(ipc + 4) & 0x3ff) * 4 + 20 == 60);
    memset(ipc + 56, 0xa5, 4); send_ok(hid);
    assert(!memcmp(ipc + 16, "SFCO", 4) && !get32(ipc + 24));
    debug_sample((void *)(uintptr_t)alias, 1, 0);
    cleanup();
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "activation")) activation();
    else if (!strcmp(argv[1], "references")) references(0);
    else if (!strcmp(argv[1], "mapping-only")) references(1);
    else if (!strcmp(argv[1], "rebinding")) rebinding();
    else if (!strcmp(argv[1], "framing-padding")) framing_padding();
    else assert(!"unknown fixture case");
    puts("ok"); return 0;
}
'''


class HidSharedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-hid-shared-')
        cls.exe = Path(cls.build.name) / 'hid-shared'
        fixture = Path(cls.build.name) / 'fixture.c'
        fixture.write_text(FIXTURE)
        flags = [shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                 '-Werror', '-pthread', '-I', str(ROOT / 'native/botw_host'),
                 '-I', str(ROOT / 'native/botw_input')]
        if sys.platform == 'darwin':
            flags += ['-framework', 'Security']
        sources = ['botw_host/kernel.c', 'botw_host/services.c', 'botw_input/input.c',
                   'botw_resources/romfs.c', 'botw_resources/savefs.c',
                   'botw_resources/local_user.c']
        subprocess.run(flags + [str(fixture)] + [str(ROOT / 'native' / p) for p in sources]
                       + ['-o', str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check_case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'ok')

    def test_activation_publishes_real_ro_alias_without_resetting_npad_and_wraps(self):
        self.check_case('activation')

    def test_service_copies_and_source_close_preserve_alias_until_unmap(self):
        self.check_case('references')

    def test_mappings_keep_backing_after_all_handles_close_until_kernel_destroy(self):
        self.check_case('mapping-only')

    def test_bind_unbind_reset_debug_activation_and_stop_old_backing_writes(self):
        self.check_case('rebinding')

    def test_sdk_nonzero_raw_word_padding_is_not_extra_activation_payload(self):
        self.check_case('framing-padding')


if __name__ == '__main__':
    unittest.main()
