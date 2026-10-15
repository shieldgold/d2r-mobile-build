"""Real runtime resource setup must publish the persistent SaveFS user to IPC."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from test_botw_romfs import fixture

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#include "RUNTIME_SOURCE"
#include <assert.h>

enum { USER_BYTES = 128, IPC_BYTES = 256 };
static uint8_t *users, *ipc;
static void observe(const char *message) { puts(message); }
static uint32_t read32(const void *address) { uint32_t n; memcpy(&n, address, 4); return n; }
static void write32(void *address, uint32_t n) { memcpy(address, &n, 4); }
static uint32_t command(uint32_t handle, unsigned type, uint32_t id, int pid,
                    uint32_t domain, int enumerate, const void *payload, size_t payload_size) {
    memset(ipc, 0, IPC_BYTES);
    size_t raw = 8;
    if (pid) { write32(ipc + 8, 1); raw += 12; }
    size_t body_size = (domain ? 16 : 0) + 16 + payload_size;
    size_t table_offset = (16 + body_size + 1) & ~(size_t)1;
    size_t raw_size = (table_offset + (enumerate ? 2 : 0) + 3) & ~(size_t)3;
    size_t raw_end = raw + raw_size;
    assert(raw_end + (enumerate ? 8 : 0) <= IPC_BYTES);
    size_t at = (raw + 15) & ~(size_t)15;
    if (domain) {
        ipc[at] = 1; ipc[at + 2] = (uint8_t)(16 + payload_size);
        write32(ipc + at + 4, domain); at += 16;
    }
    memcpy(ipc + at, "SFCI", 4); write32(ipc + at + 8, id);
    at += 16;
    if (payload_size) memcpy(ipc + at, payload, payload_size);
    write32(ipc, type);
    write32(ipc + 4, (uint32_t)(raw_size / 4) |
        (pid ? UINT32_C(0x80000000) : 0) | (enumerate ? 3u << 10 : 0));
    if (enumerate) {
        /* Variable OutPointer sizes follow the full raw alignment budget,
         * outside the domain's semantic data_size. */
        uint16_t size = USER_BYTES;
        memcpy(ipc + raw + table_offset, &size, sizeof size);
        uintptr_t address = (uintptr_t)users;
        assert(address < (UINT64_C(1) << 48));
        write32(ipc + raw_end, (uint32_t)address);
        write32(ipc + raw_end + 4, (uint32_t)(address >> 32) | USER_BYTES << 16);
    }
    char error[512] = {0};
    int result = botw_services_send(state.services, handle, ipc, IPC_BYTES, error, sizeof error);
    if (result) fprintf(stderr, "%s\n", error);
    assert(!result);

    uint32_t header = read32(ipc + 4), moved = 0;
    at = 8;
    if (header >> 31) {
        assert(read32(ipc + at) == 1u << 5);
        moved = read32(ipc + at + 4); at += 8;
    }
    unsigned pointers = (read32(ipc) >> 16) & 15;
    assert(pointers == (unsigned)enumerate);
    if (pointers) {
        uint32_t descriptor = read32(ipc + at);
        uint64_t address = read32(ipc + at + 4) |
            (uint64_t)((descriptor >> 12) & 15) << 32 |
            (uint64_t)((descriptor >> 6) & 63) << 36;
        assert(!(descriptor & 63) && descriptor >> 16 == USER_BYTES);
        assert(address == (uintptr_t)users); at += 8;
    }
    raw_end = at + (header & 1023) * 4;
    at = (at + 15) & ~(size_t)15;
    if (domain) { assert(!read32(ipc + at)); at += 16; }
    assert(at + 16 <= raw_end && raw_end <= IPC_BYTES);
    assert(!memcmp(ipc + at, "SFCO", 4) && !read32(ipc + at + 8));
    if (type == 5) assert(read32(ipc + at + 16) == 1);
    return moved;
}
int main(int argc, char **argv) {
    assert(argc == 3);
    state.owner = pthread_self(); state.event = observe;
    state.kernel = botw_kernel_create(NULL, NULL); assert(state.kernel);
    /* The production copy callbacks independently validate guest ownership.
     * Use actual kernel-owned memory for both IPC and its C output. */
    char memory_error[512]; const size_t heap_size = 0x200000;
    assert(!botw_kernel_configure_memory(state.kernel, heap_size, heap_size,
        heap_size, heap_size, 0, memory_error, sizeof memory_error));
    uint64_t registers[32] = {0, heap_size};
    assert(botw_kernel_dispatch(state.kernel, 1, registers, memory_error,
        sizeof memory_error) == BOTW_KERNEL_HANDLED && !registers[0]);
    users = (uint8_t *)(uintptr_t)registers[1]; ipc = users + 0x1000;
    state.services = botw_services_create(services_log, services_memory, NULL); assert(state.services);
    botw_services_set_memory_write_preparer(state.services, services_prepare_write, NULL);
    if (sigsetjmp(state.recovery, 1)) {
        assert(!strcmp(argv[2], "corrupt"));
        assert(!cleanup()); return 0;
    }
    configure_resource_backends(argv[1]);
    assert(strcmp(argv[2], "corrupt"));
    if (!strcmp(argv[2], "missing")) {
        assert(!state.romfs && !state.savefs);
        char error[512];
        assert(botw_services_set_preselected_local_user(state.services, state.local_user.uid,
            error, sizeof error));
    } else {
        assert(state.romfs && state.savefs);
        assert(!strcmp(botw_savefs_user_id(state.savefs), state.local_user.uid_hex));
        char error[512]; uint32_t manager = 0;
        const uint64_t zero = 0;
        const uint8_t name[8] = {'a', 'c', 'c', ':', 'u', '0', 0, 0};
        assert(!botw_services_connect(state.services, "sm:", 3, &manager, error, sizeof error));
        command(manager, 4, 0, 1, 0, 0, &zero, sizeof zero);
        uint32_t account = command(manager, 4, 1, 0, 0, 0, name, sizeof name);
        assert(account);
        command(account, 5, 0, 0, 0, 0, NULL, 0); /* Real session-to-domain conversion. */
        command(account, 4, 100, 1, 1, 0, &zero, sizeof zero);
        memset(users, 0xa5, USER_BYTES);
        command(account, 4, 2, 0, 1, 1, NULL, 0);
        assert(!memcmp(users, state.local_user.uid, 16));
        for (size_t i = 16; i < USER_BYTES; i++) assert(!users[i]);
        assert(botw_services_set_preselected_local_user(state.services, state.local_user.uid,
            error, sizeof error)); /* The runtime already armed it exactly once. */
        assert(!botw_services_close(state.services, account, error, sizeof error));
        assert(!botw_services_close(state.services, manager, error, sizeof error));
        puts("runtime_persistent_user_enumerated=true runtime_preselection_configured=true");
    }
    assert(!cleanup()); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native runtime setup requires Apple ARM64')
class RuntimeResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-runtime-resources-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(cls.build.name) / 'resources.c'
        source.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = Path(cls.build.name) / 'resources'
        sources = [source, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        result = subprocess.run(['xcrun', 'clang', '-framework', 'Security', '-O2',
                                 '-Wall', '-Wextra', '-Werror', *(str(p) for p in sources),
                                 '-o', str(cls.exe)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def run_setup(self, root, mode='ready'):
        result = subprocess.run([str(self.exe), str(root), mode], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_real_persistent_identity_reaches_account_after_resource_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'botw-romfs.bin').write_bytes(fixture([('/System/Version.txt', b'1.0.0')])[0])
            first = self.run_setup(root)
            identity = (root / 'botw-profile/uid.bin').read_bytes()
            second = self.run_setup(root)
            self.assertEqual((root / 'botw-profile/uid.bin').read_bytes(), identity)
            for result in (first, second):
                self.assertIn('runtime_persistent_user_enumerated=true', result.stdout)
                self.assertIn('runtime_preselection_configured=true', result.stdout)
            self.assertEqual(len(list((root / 'botw-saves/01007ef00011e000').iterdir())), 1)

    def test_missing_romfs_does_not_create_or_publish_an_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_setup(root, 'missing')
            self.assertFalse((root / 'botw-profile').exists())
            self.assertFalse((root / 'botw-saves').exists())

    def test_corrupt_identity_stops_without_replacing_it_or_creating_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'botw-romfs.bin').write_bytes(fixture([('/System/Version.txt', b'1.0.0')])[0])
            (root / 'botw-profile').mkdir(mode=0o700)
            identity = root / 'botw-profile/uid.bin'
            identity.write_bytes(b'corrupt local identity')
            identity.chmod(0o600)
            result = self.run_setup(root, 'corrupt')
            self.assertIn('open local offline user:', result.stdout)
            self.assertEqual(identity.read_bytes(), b'corrupt local identity')
            self.assertFalse((root / 'botw-saves').exists())
