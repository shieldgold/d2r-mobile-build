"""Hand-built Horizon IPC fixtures; no Nintendo binaries or headers needed."""
import ctypes as C
from concurrent.futures import ThreadPoolExecutor
import errno
import mmap
import platform
import shutil
import struct
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_botw_romfs import fixture as romfs_fixture


class RomfsError(C.Structure):
    _fields_ = [('code', C.c_int), ('message', C.c_char * 192)]


class LocalUser(C.Structure):
    _fields_ = [('uid', C.c_ubyte * 16), ('uid_hex', C.c_char * 33)]

LOG = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p)
MEMORY = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint64, C.c_size_t, C.c_int)
EVENT_CREATE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32), C.c_void_p, C.c_size_t)
EVENT_DUP = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p, C.c_size_t)
EVENT_ACTION = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_void_p, C.c_size_t)
PROCESS_CHECK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32)


class KernelAPI(C.Structure):
    _fields_ = [('opaque', C.c_void_p), ('create', EVENT_CREATE), ('duplicate', EVENT_DUP),
                ('signal', EVENT_ACTION), ('clear', EVENT_ACTION), ('close', EVENT_ACTION),
                ('is_process', PROCESS_CHECK)]


FOCUS = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_size_t)
PERFORMANCE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_uint32, C.c_int, C.c_void_p, C.c_size_t)


class ServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.library = Path(cls.build.name) / 'services.dylib'
        source = ROOT / 'native/botw_host/services.c'
        if not source.exists():
            raise AssertionError('Horizon services implementation is missing')
        # The real backend still performs every host syscall by default. This
        # narrow fsync seam counts durability attempts and can return one EIO;
        # no SaveFS operation or IPC reply is replaced by a fake backend.
        savefs_probe = Path(cls.build.name) / 'savefs-probe.c'
        savefs_probe.write_text(r'''
#define _GNU_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <unistd.h>
#include <errno.h>
#include <stdatomic.h>
static atomic_uint sync_count;
static atomic_int fail_sync;
static int observed_fsync(int fd) {
    atomic_fetch_add(&sync_count, 1);
    if (atomic_exchange(&fail_sync, 0)) { errno = EIO; return -1; }
    return fsync(fd);
}
#define fsync observed_fsync
#include "SAVEFS_SOURCE"
#undef fsync
void botw_test_savefs_sync_reset(int fail_next) {
    atomic_store(&sync_count, 0); atomic_store(&fail_sync, fail_next);
}
unsigned botw_test_savefs_sync_count(void) { return atomic_load(&sync_count); }
'''.replace('SAVEFS_SOURCE', str(ROOT / 'native/botw_resources/savefs.c')))
        flags = [shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                 '-Werror', '-shared', '-fPIC', '-pthread']
        if platform.system() == 'Darwin':
            flags += ['-framework', 'Security']
        subprocess.run(flags + [str(source), str(ROOT / 'native/botw_resources/romfs.c'),
                                str(savefs_probe),
                                str(ROOT / 'native/botw_resources/local_user.c'),
                                str(ROOT / 'native/botw_input/input.c'), '-o', str(cls.library)], check=True)
        cls.lib = C.CDLL(str(cls.library))
        cls.lib.botw_services_create.argtypes = [LOG, MEMORY, C.c_void_p]
        cls.lib.botw_services_create.restype = C.c_void_p
        cls.lib.botw_services_destroy.argtypes = [C.c_void_p]
        cls.lib.botw_services_connect.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t,
                                                C.POINTER(C.c_uint32), C.c_char_p, C.c_size_t]
        cls.lib.botw_services_close.argtypes = [C.c_void_p, C.c_uint32, C.c_char_p, C.c_size_t]
        cls.lib.botw_services_send.argtypes = [C.c_void_p, C.c_uint32, C.c_void_p, C.c_size_t,
                                             C.c_char_p, C.c_size_t]
        cls.lib.botw_services_set_savefs.argtypes = [C.c_void_p, C.c_void_p]
        cls.lib.botw_services_set_local_user.argtypes = [C.c_void_p, C.c_void_p, C.c_char_p,
                                                        C.c_char_p, C.c_size_t]
        cls.lib.botw_services_set_preselected_local_user.argtypes = [C.c_void_p, C.c_void_p,
                                                                    C.c_char_p, C.c_size_t]
        cls.lib.botw_local_user_open.argtypes = [C.c_char_p, C.POINTER(LocalUser), C.POINTER(RomfsError)]
        cls.lib.botw_savefs_open.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p, C.c_uint64, C.POINTER(RomfsError)]
        cls.lib.botw_savefs_open.restype = C.c_void_p
        cls.lib.botw_savefs_close.argtypes = [C.c_void_p]
        cls.lib.botw_test_savefs_sync_reset.argtypes = [C.c_int]
        cls.lib.botw_test_savefs_sync_count.restype = C.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.logs, self.ranges = [], []
        self.log = LOG(lambda _, msg: self.logs.append(msg.decode('utf8', 'replace')))
        self.memory = MEMORY(lambda _, address, length, write: int(any(
            address >= start and address - start <= size and length <= size - (address - start)
            and (not write or writable) for start, size, writable in self.ranges)))
        self.ctx = self.lib.botw_services_create(self.log, self.memory, None)
        self.lib.botw_test_savefs_sync_reset(0)
        self.error = C.create_string_buffer(1024)
        self.assertTrue(self.ctx)

    def tearDown(self):
        self.lib.botw_services_destroy(self.ctx)

    def connect(self, name=b'sm:'):
        handle = C.c_uint32()
        rc = self.lib.botw_services_connect(self.ctx, name, len(name), C.byref(handle), self.error, 1024)
        self.assertEqual(rc, 0, self.error.value)
        return handle.value

    def close(self, handle):
        return self.lib.botw_services_close(self.ctx, handle, self.error, 1024)

    def static_buffer(self, data):
        # HIPC X pointers have exactly 42 address bits, unlike 64-bit A/B buffers.
        # Linux x86-64 ctypes allocations normally sit above that range. Request
        # a separate anonymous low mapping without MAP_FIXED or replacing pages.
        if platform.system() != 'Linux':
            buffer = C.create_string_buffer(data)
        else:
            libc = C.CDLL(None, use_errno=True)
            libc.mmap.argtypes = [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_longlong]
            libc.mmap.restype = C.c_void_p
            libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
            size = len(data) + 1
            span = (size + mmap.PAGESIZE - 1) & -mmap.PAGESIZE
            flags = mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS
            hint = None
            if platform.machine().lower() in ('x86_64', 'amd64'):
                flags |= getattr(mmap, 'MAP_32BIT', 0x40)
            else:
                hint = 0x10000000  # non-fixed hint; actual result is checked below
            address = libc.mmap(hint, span, mmap.PROT_READ | mmap.PROT_WRITE, flags, -1, 0)
            if address == C.c_void_p(-1).value:
                raise OSError(C.get_errno(), 'allocate addressable HIPC static fixture')
            self.addCleanup(libc.munmap, address, span)
            buffer = (C.c_char * size).from_address(address)
            C.memmove(buffer, data + b'\0', size)
        self.assertLessEqual(C.addressof(buffer) + len(buffer), 1 << 42,
                             'HIPC static fixture does not fit the real descriptor')
        return buffer

    @staticmethod
    def request(command, payload=b'', kind=4, pid=False, domain=0, static=None, buffer=None, copies=(), receive=None, receive_static=None):
        descriptor = b''
        if static:
            address, size = static
            descriptor += struct.pack('<II', size << 16 | ((address >> 36) & 63) << 6 |
                                      ((address >> 32) & 15) << 12, address & 0xffffffff)
        if buffer:
            address, size = buffer
            descriptor += struct.pack('<III', size & 0xffffffff, address & 0xffffffff,
                                      ((address >> 36) & 0x3fffff) << 2 |
                                      ((size >> 32) & 15) << 24 | ((address >> 32) & 15) << 28)
        if receive:
            address, size = receive
            descriptor += struct.pack('<III', size & 0xffffffff, address & 0xffffffff,
                                      ((address >> 36) & 0x3fffff) << 2 |
                                      ((size >> 32) & 15) << 24 | ((address >> 32) & 15) << 28)
        special = b''
        if pid or copies:
            special = struct.pack('<I', bool(pid) | len(copies) << 1)
            if pid:
                special += struct.pack('<Q', 0)
            special += struct.pack('<' + 'I' * len(copies), *copies)
        start = 8 + len(special) + len(descriptor)
        body = b'SFCI' + struct.pack('<III', 0, command, 0) + payload
        if domain:
            body = struct.pack('<BBHIII', 1, 0, len(body), domain, 0, 0) + body
        words = (16 + len(body) + 3) // 4
        header = struct.pack('<II', kind | bool(static) << 16 | bool(buffer) << 20 | bool(receive) << 24,
                             words | (bool(pid or copies) << 31) | (3 << 10 if receive_static else 0))
        message = header + special + descriptor + b'\0' * (-start % 16) + body
        message = message.ljust(start + words * 4, b'\0')
        if receive_static:
            address, size = receive_static
            assert 0 <= address < 1 << 48 and 0 <= size < 1 << 16
            message += struct.pack('<II', address & 0xffffffff, size << 16 | address >> 32)
        return message

    def send(self, handle, request, good=True, capacity=256):
        ipc = C.create_string_buffer(max(capacity, len(request)))
        C.memmove(ipc, request, len(request))
        self.ranges.append((C.addressof(ipc), len(ipc), True))
        rc = self.lib.botw_services_send(self.ctx, handle, ipc, capacity, self.error, 1024)
        if good:
            self.assertEqual(rc, 0, self.error.value)
        else:
            self.assertEqual(rc, -1)
        return bytes(ipc), self.error.value.decode()

    def response(self, raw, domain=False):
        _, word1 = struct.unpack_from('<II', raw)
        offset, handles = 8, []
        if word1 >> 31:
            special, = struct.unpack_from('<I', raw, offset)
            offset += 4
            self.assertEqual(special & 1, 0)
            count = ((special >> 1) & 15) + ((special >> 5) & 15)
            handles = list(struct.unpack_from('<' + 'I' * count, raw, offset))
            offset += 4 * count
        offset = (offset + 15) & ~15
        if domain:
            nobjects, = struct.unpack_from('<I', raw, offset)
            offset += 16
        else:
            nobjects = 0
        magic, version, result, token = struct.unpack_from('<4sIII', raw, offset)
        self.assertEqual((magic, version, result, token), (b'SFCO', 0, 0, 0))
        return raw[offset + 16:], handles, nobjects

    def initialized_sm(self):
        sm = self.connect()
        self.send(sm, self.request(0, b'\0' * 8, pid=True))
        return sm

    def service(self, sm, name=b'lm'):
        raw, _ = self.send(sm, self.request(1, name.ljust(8, b'\0')))
        _, handles, _ = self.response(raw)
        self.assertEqual(len(handles), 1)
        return handles[0]

    def local_savefs(self, uid_hex=None, title=b'01007ef00011e000', quota=0):
        directory = tempfile.TemporaryDirectory(prefix='botw-account-services-')
        self.addCleanup(directory.cleanup)
        documents = Path(directory.name) / 'Documents'
        documents.mkdir(mode=0o700)
        user, reopened, error = LocalUser(), LocalUser(), RomfsError()
        self.assertEqual(self.lib.botw_local_user_open(str(documents).encode(), C.byref(user), C.byref(error)), 0, error.message)
        self.assertEqual(self.lib.botw_local_user_open(str(documents).encode(), C.byref(reopened), C.byref(error)), 0, error.message)
        self.assertEqual(bytes(user), bytes(reopened))
        self.assertEqual(user.uid_hex.decode(), bytes(user.uid).hex())
        store = self.lib.botw_savefs_open(str(documents / 'botw-saves').encode(), title,
                                         user.uid_hex if uid_hex is None else uid_hex, quota, C.byref(error))
        self.assertTrue(store, error.message)
        self.addCleanup(self.lib.botw_savefs_close, store)
        return store, user, documents

    def configure_local_user(self, preselect=True, quota=0):
        store, user, documents = self.local_savefs(quota=quota)
        self.lib.botw_services_set_savefs(self.ctx, store)
        self.assertEqual(self.lib.botw_services_set_local_user(self.ctx, user.uid,
                         b'app-documents:botw-profile/uid.bin', self.error, 1024), 0, self.error.value)
        if preselect:
            self.assertEqual(self.lib.botw_services_set_preselected_local_user(self.ctx, user.uid,
                             self.error, 1024), 0, self.error.value)
        return store, user, documents

    def account(self, domain=False, initialize=True):
        handle = self.service(self.initialized_sm(), b'acc:u0')
        root = 0
        if domain:
            raw, _ = self.send(handle, self.request(0, kind=5))
            data, _, _ = self.response(raw)
            root, = struct.unpack_from('<I', data)
        if initialize:
            self.send(handle, self.request(100, bytes(8), pid=True, domain=root))
        return handle, root

    def application_functions(self, domain=False):
        self.applet_kernel()
        self.applet_state()
        proxy = self.applet_proxy()
        if not domain:
            return self.proxy_child(proxy, 20), 0
        raw, _ = self.send(proxy, self.request(0, kind=5))
        data, _, _ = self.response(raw)
        proxy_root, = struct.unpack_from('<I', data)
        raw, _ = self.send(proxy, self.request(20, domain=proxy_root))
        data, handles, count = self.response(raw, domain=True)
        self.assertEqual((handles, count), ([], 1))
        return proxy, struct.unpack_from('<I', data)[0]

    def assert_am_unavailable(self, raw, domain=False):
        self.assertEqual(struct.unpack_from('<I', raw, 4)[0], 10 if domain else 6)
        offset = 32 if domain else 16
        self.assertEqual(struct.unpack_from('<4sIII', raw, offset), (b'SFCO', 0, 0x480, 0))
        if domain:
            self.assertEqual(struct.unpack_from('<I', raw, 16)[0], 0)

    def test_guest_termination_reports_original_result_without_success_reply(self):
        for domain in (False, True):
            app, root = self.application_functions(domain)
            for result in (0, 0x2a2, 0x7d402, 0xffffffff):
                request = self.request(22, struct.pack('<I', result), domain=root)
                raw, error = self.send(app, request, good=False)
                self.assertIn('ApplicationFunctions.SetTerminateResult', error)
                self.assertIn(f'result=0x{result:08x}', error)
                self.assertEqual(raw[:len(request)], request)

    def test_guest_termination_rejects_malformed_payload_before_reading_result(self):
        app, root = self.application_functions(True)
        for payload in (b'', bytes(8)):
            _, error = self.send(app, self.request(22, payload, domain=root), good=False)
            self.assertIn('ApplicationFunctions.SetTerminateResult', error)
            self.assertIn('requires 4 input bytes', error)
            self.assertNotIn('result=0x', error)

    def test_local_user_configuration_requires_persistent_matching_savefs_and_is_once_only(self):
        store, user, _ = self.local_savefs()
        configure = self.lib.botw_services_set_local_user
        preselect = self.lib.botw_services_set_preselected_local_user
        self.assertEqual(configure(self.ctx, user.uid, b'local-file', self.error, 1024), -1)
        self.assertIn(b'SaveFS', self.error.value)
        self.lib.botw_services_set_savefs(self.ctx, store)
        self.assertEqual(preselect(self.ctx, user.uid, self.error, 1024), -1)
        for uid, source in ((None, b'local-file'), ((C.c_ubyte * 16)(), b'local-file'),
                            (user.uid, None), (user.uid, b''), (user.uid, b'x' * 128),
                            (user.uid, b'fake\nsource')):
            self.assertEqual(configure(self.ctx, uid, source, self.error, 1024), -1)
        wrong = (C.c_ubyte * 16).from_buffer_copy(bytes([user.uid[0] ^ 1]) + bytes(user.uid)[1:])
        self.assertEqual(configure(self.ctx, wrong, b'local-file', self.error, 1024), -1)
        wrong_store, _, _ = self.local_savefs(uid_hex=user.uid_hex, title=b'0100000000000001')
        self.lib.botw_services_set_savefs(self.ctx, wrong_store)
        self.assertEqual(configure(self.ctx, user.uid, b'local-file', self.error, 1024), -1)
        self.lib.botw_services_set_savefs(self.ctx, store)
        self.assertEqual(configure(self.ctx, user.uid, b'local-file', self.error, 1024), 0, self.error.value)
        self.assertEqual(configure(self.ctx, user.uid, b'local-file', self.error, 1024), -1)
        self.assertEqual(preselect(self.ctx, wrong, self.error, 1024), -1)
        self.assertEqual(preselect(self.ctx, user.uid, self.error, 1024), 0, self.error.value)
        self.assertEqual(preselect(self.ctx, user.uid, self.error, 1024), -1)
        self.assertFalse(any(user.uid_hex.decode() in line for line in self.logs))

    def test_account_defaults_unconfigured_and_requires_pid_zero_placeholder_and_initialization(self):
        account, _ = self.account(initialize=False)
        self.send(account, self.request(100, bytes(8), pid=True), good=False)
        _, user, _ = self.configure_local_user()
        output = C.create_string_buffer(128)
        self.ranges.append((C.addressof(output), 128, True))
        self.send(account, self.request(2, receive_static=(C.addressof(output), 128)), good=False)
        self.send(account, self.request(101, bytes(user.uid)), good=False)
        self.send(account, self.request(100, bytes(8)), good=False)
        self.send(account, self.request(100, struct.pack('<Q', 123), pid=True), good=False)
        self.send(account, self.request(100, bytes(8) + b'\1', pid=True), good=False)
        self.send(account, self.request(100, bytes(8), pid=True, copies=(0xffff8001,)), good=False)
        # The real SDK writes PID=0 both as payload and as special-header placeholder.
        # The host binds the session to its current process instead of trusting either.
        self.send(account, self.request(100, bytes(8), pid=True))
        self.send(account, self.request(100, bytes(8), pid=True), good=False)
        self.assertTrue(any('pid_source=host_process' in line for line in self.logs), self.logs)

    def test_account_exact_c128_enumeration_zero_terminates_and_has_no_reply_count(self):
        _, user, _ = self.configure_local_user()
        for domain in (False, True):
            account, root = self.account(domain)
            output = C.create_string_buffer(b'\xa5' * 144, 144)
            self.ranges.append((C.addressof(output), 144, True))
            raw, _ = self.send(account, self.request(2, domain=root,
                              receive_static=(C.addressof(output), 128)))
            data, handles, objects = self.response(raw, domain)
            self.assertEqual((handles, objects), ([], 0))
            self.assertEqual(struct.unpack_from('<I', raw, 4)[0], 10 if domain else 6)
            self.assertEqual(bytes(output)[:128], bytes(user.uid) + bytes(112))
            self.assertEqual(bytes(output)[128:], b'\xa5' * 16)
            self.assertFalse(any(data))
            for kwargs in ({}, {'receive_static': (C.addressof(output), 112)},
                           {'receive_static': (C.addressof(output), 144)},
                           {'receive': (C.addressof(output), 128)},
                           {'receive_static': (C.addressof(output), 128), 'pid': True}):
                self.send(account, self.request(2, domain=root, **kwargs), good=False)
            self.send(account, self.request(2, b'\1', domain=root,
                      receive_static=(C.addressof(output), 128)), good=False)
            if domain:
                self.send(account, self.request(2, b'\0', domain=root,
                          receive_static=(C.addressof(output), 128)), good=False)
            self.send(account, self.request(2, domain=root,
                      receive_static=(C.addressof(output) + 256, 128)), good=False)
            self.send(account, self.request(9999, domain=root), good=False)

    def test_account_context_matches_uid_has_real_object_lifetime_and_rejects_online_commands(self):
        _, user, _ = self.configure_local_user()
        for domain in (False, True):
            account, root = self.account(domain)
            for uid in (bytes(16), bytes([user.uid[0] ^ 1]) + bytes(user.uid)[1:], bytes(user.uid) + b'\1'):
                self.send(account, self.request(101, uid, domain=root), good=False)
            self.send(account, self.request(101, bytes(user.uid), domain=root, pid=True), good=False)
            output = C.create_string_buffer(b'\xa5' * 128, 128)
            self.ranges.append((C.addressof(output), 128, True))
            for key in ('receive', 'receive_static'):
                self.error.value = b''
                _, error = self.send(account, self.request(101, bytes(user.uid), domain=root,
                                     **{key: (C.addressof(output), 128)}), good=False)
                self.assertIn('GetBaasAccountManagerForApplication does not accept output buffers', error)
                self.assertEqual(bytes(output), b'\xa5' * 128)
            raw, _ = self.send(account, self.request(101, bytes(user.uid), domain=root))
            data, handles, objects = self.response(raw, domain)
            self.assertEqual((len(handles), objects), (0, 1) if domain else (1, 0))
            if domain:
                child, = struct.unpack_from('<I', data)
                raw, _ = self.send(account, self.request(1, struct.pack('<I', child), kind=5))
                _, handles, _ = self.response(raw)
                # The copied context survives domain-object close.
                close = struct.pack('<II', 4, 8) + bytes(8) + struct.pack('<BBHIII', 2, 0, 0, child, 0, 0)
                self.send(account, close)
                self.send(account, self.request(0, domain=child), good=False)
            context = handles[0]
            for command in (0, 1, 2, 3, 130):
                _, error = self.send(context, self.request(command), good=False)
                self.assertIn('online', error)
                self.assertIn(str(command), error)
            self.assertEqual(self.close(context), 0)
            self.send(context, self.request(0), good=False)
            self.assertEqual(self.close(context), -1)

    def test_published_account_rechecks_savefs_mount_after_configuration(self):
        store, user, _ = self.configure_local_user()
        account, root = self.account(True)
        other, _, _ = self.local_savefs()
        output = C.create_string_buffer(b'\xa5' * 128, 128)
        self.ranges.append((C.addressof(output), 128, True))
        for changed in (None, other):
            self.lib.botw_services_set_savefs(self.ctx, changed)
            self.send(account, self.request(2, domain=root,
                      receive_static=(C.addressof(output), 128)), good=False)
            self.assertEqual(bytes(output), b'\xa5' * 128)
            self.send(account, self.request(101, bytes(user.uid), domain=root), good=False)
        self.lib.botw_services_set_savefs(self.ctx, store)
        self.send(account, self.request(2, domain=root, receive_static=(C.addressof(output), 128)))
        self.assertEqual(bytes(output), bytes(user.uid) + bytes(112))

    def test_preselection_launch_storage_exact_bytes_and_accessor_outlives_storage(self):
        _, user, documents = self.configure_local_user()
        before = (documents / 'botw-profile/uid.bin').read_bytes()
        app, root = self.application_functions()
        # An empty kind1 slot neither returns nor consumes the kind2 selection.
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 1)))
        self.assert_am_unavailable(raw)
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 2)))
        _, handles, _ = self.response(raw)
        storage = handles[0]
        raw, _ = self.send(storage, self.request(0))
        _, handles, _ = self.response(raw)
        accessor = handles[0]
        self.assertEqual(self.close(storage), 0)
        raw, _ = self.send(accessor, self.request(0))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<Q', data)[0], 0x88)
        output = C.create_string_buffer(0x88)
        self.ranges.append((C.addressof(output), 0x88, True))
        self.send(accessor, self.request(11, bytes(8), receive=(C.addressof(output), 0x88)))
        expected = struct.pack('<IB3x', 0xc79497ca, 1) + bytes(user.uid) + bytes(0x70)
        self.assertEqual(bytes(output), expected)
        self.assertEqual(self.close(accessor), 0)
        for kind in (2, 1, 2):
            raw, _ = self.send(app, self.request(1, struct.pack('<I', kind)))
            self.assert_am_unavailable(raw)
        self.assertEqual((documents / 'botw-profile/uid.bin').read_bytes(), before)
        self.assertEqual(self.lib.botw_services_set_preselected_local_user(self.ctx, user.uid, self.error, 1024), -1)

    def test_preselection_is_explicit_strict_and_errors_do_not_consume_it(self):
        store, user, _ = self.configure_local_user(preselect=False)
        app, root = self.application_functions(True)
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 2), domain=root))
        self.assert_am_unavailable(raw, True)
        self.assertEqual(self.lib.botw_services_set_preselected_local_user(self.ctx, user.uid, self.error, 1024), 0)
        for payload in (b'', struct.pack('<I', 0), struct.pack('<I', 3), struct.pack('<I', 2) + b'\0',
                        struct.pack('<I', 2) + b'\1'):
            self.send(app, self.request(1, payload, domain=root), good=False)
        self.send(app, self.request(1, struct.pack('<I', 2), domain=root, pid=True), good=False)
        self.lib.botw_services_set_savefs(self.ctx, None)
        self.send(app, self.request(1, struct.pack('<I', 2), domain=root), good=False)
        self.lib.botw_services_set_savefs(self.ctx, store)
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 2), domain=root))
        data, handles, count = self.response(raw, True)
        self.assertEqual((handles, count), ([], 1))
        child, = struct.unpack_from('<I', data)
        self.assertNotEqual(child, root)
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 2), domain=root))
        self.assert_am_unavailable(raw, True)

    def test_preselection_allocation_failure_preserves_pending_kind2_domain_object(self):
        self.configure_local_user()
        app, root = self.application_functions(True)
        # Proxy root=1 and ApplicationFunctions=2; 62 actual children fill table.
        children = []
        for _ in range(62):
            raw, _ = self.send(app, self.request(0, domain=1))
            data, _, _ = self.response(raw, True)
            children.append(struct.unpack_from('<I', data)[0])
        _, error = self.send(app, self.request(1, struct.pack('<I', 2), domain=root), good=False)
        self.assertIn('exhausted', error)
        close = struct.pack('<II', 4, 8) + bytes(8) + struct.pack('<BBHIII', 2, 0, 0, children[0], 0, 0)
        self.send(app, close)
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 2), domain=root))
        _, _, count = self.response(raw, True)
        self.assertEqual(count, 1)
        raw, _ = self.send(app, self.request(1, struct.pack('<I', 2), domain=root))
        self.assert_am_unavailable(raw, True)

    def test_preselection_concurrent_ipc_delivers_exactly_one_storage_object(self):
        self.configure_local_user()
        app, _ = self.application_functions()
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda _: self.send(app, self.request(1, struct.pack('<I', 2)))[0], range(16)))
        successes = []
        for raw in replies:
            # All HIPC requests succeed; the empty queue is a CMIF result.
            result = struct.unpack_from('<I', raw, 24)[0]
            if result == 0:
                _, handles, _ = self.response(raw)
                successes += handles
            else:
                self.assert_am_unavailable(raw)
        self.assertEqual(len(successes), 1)
        self.assertEqual(self.close(successes[0]), 0)

    def test_account_and_preselection_reuse_ipc_buffer_and_preserve_nonzero_token(self):
        _, user, _ = self.configure_local_user()
        account, root = self.account(True)
        app, app_root = self.application_functions(True)
        output, ipc = C.create_string_buffer(128), C.create_string_buffer(256)
        self.ranges.extend(((C.addressof(output), 128, True), (C.addressof(ipc), 256, True)))
        for handle, request, result in (
                (account, self.request(2, domain=root, receive_static=(C.addressof(output), 128)), 0),
                (app, self.request(1, struct.pack('<I', 2), domain=app_root), 0),
                (app, self.request(1, struct.pack('<I', 2), domain=app_root), 0x480),
                (account, self.request(2, domain=root, receive_static=(C.addressof(output), 128)), 0)):
            # The SDK uses the same TLR message buffer for requests and replies.
            # Do not clear bytes outside the newly built request.
            request = bytearray(request)
            struct.pack_into('<I', request, request.index(b'SFCI') + 12, 0x55667788)
            C.memmove(ipc, bytes(request), len(request))
            self.assertEqual(self.lib.botw_services_send(self.ctx, handle, ipc, 256, self.error, 1024), 0, self.error.value)
            self.assertEqual(struct.unpack_from('<4sIII', bytes(ipc), 32), (b'SFCO', 0, result, 0x55667788))
        self.assertEqual(bytes(output), bytes(user.uid) + bytes(112))

    @staticmethod
    def save_mount_payload(uid):
        payload = bytearray(0x48)
        payload[0] = 1  # User space; current application ID=0.
        payload[16:32] = bytes(uid)
        payload[40] = 1  # Account save type; rank/index/system ID/reserved=0.
        return bytes(payload)

    def save_mount(self, user, domain=False, fsp=None):
        fsp = self.service(self.initialized_sm(), b'fsp-srv') if fsp is None else fsp
        root = 0
        if domain:
            raw, _ = self.send(fsp, self.request(0, kind=5))
            data, _, _ = self.response(raw)
            root, = struct.unpack_from('<I', data)
        self.send(fsp, self.request(1, bytes(8), pid=True, domain=root))
        raw, _ = self.send(fsp, self.request(51, self.save_mount_payload(user.uid), domain=root))
        data, handles, count = self.response(raw, domain)
        self.assertEqual((len(handles), count), (0, 1) if domain else (1, 0))
        return (fsp, struct.unpack_from('<I', data)[0]) if domain else (handles[0], 0)

    def save_path(self, handle, root, command, path, payload=b'', good=True):
        if isinstance(path, str):
            path = path.encode()
        name = self.static_buffer(path)
        self.ranges.append((C.addressof(name), len(name), False))
        return self.send(handle, self.request(command, payload, domain=root,
                         static=(C.addressof(name), len(name))), good=good)

    def save_file(self, fs, root, path='/slot', mode=3):
        raw, _ = self.save_path(fs, root, 8, path, struct.pack('<I', mode))
        data, handles, count = self.response(raw, bool(root))
        self.assertEqual((len(handles), count), (0, 1) if root else (1, 0))
        return (fs, struct.unpack_from('<I', data)[0]) if root else (handles[0], 0)

    def save_write(self, file, root, data, offset=0, option=0, good=True):
        buffer = C.create_string_buffer(data)
        self.ranges.append((C.addressof(buffer), len(buffer), False))
        return self.send(file, self.request(1, struct.pack('<IIQQ', option, 0, offset, len(data)),
                         domain=root, buffer=(C.addressof(buffer), len(data)) if data else None), good=good)

    def save_read(self, file, root, size, offset=0, good=True):
        buffer = C.create_string_buffer(b'\xa5' * max(size, 1), max(size, 1))
        self.ranges.append((C.addressof(buffer), len(buffer), True))
        raw, error = self.send(file, self.request(0, struct.pack('<IIQQ', 0, 0, offset, size),
                               domain=root, receive=(C.addressof(buffer), size) if size else None), good=good)
        if not good:
            return raw, error
        data, _, _ = self.response(raw, bool(root))
        actual, = struct.unpack_from('<Q', data)
        self.assertLessEqual(actual, size)
        self.assertEqual(bytes(buffer)[actual:size], b'\xa5' * (size - actual))
        return bytes(buffer)[:actual]

    def assert_fs_result(self, raw, description, domain=False):
        at = 32 if domain else 16
        self.assertEqual(struct.unpack_from('<4sIII', raw, at), (b'SFCO', 0, 2 | description << 9, 0))

    def test_save_mount_requires_exact_current_title_account_and_persistent_user_binding(self):
        fsp = self.service(self.initialized_sm(), b'fsp-srv')
        store, user, _ = self.local_savefs()
        self.send(fsp, self.request(51, self.save_mount_payload(user.uid)), good=False)
        self.send(fsp, self.request(1, bytes(8), pid=True))
        self.send(fsp, self.request(51, self.save_mount_payload(user.uid)), good=False)
        self.lib.botw_services_set_savefs(self.ctx, store)
        self.assertEqual(self.lib.botw_services_set_local_user(self.ctx, user.uid, b'local-file', self.error, 1024), 0)
        original = self.save_mount_payload(user.uid)
        for offset in (0, 1, 8, 16, 32, 40, 41, 42, 44, 48, 56, 64, 71):
            payload = bytearray(original)
            payload[offset] ^= 1
            self.send(fsp, self.request(51, payload), good=False)
        self.send(fsp, self.request(51, original + b'\1'), good=False)
        self.send(fsp, self.request(51, original, pid=True), good=False)
        raw, _ = self.send(fsp, self.request(51, original))
        _, handles, _ = self.response(raw)
        self.assertEqual(len(handles), 1)

    def test_save_files_create_write_flush_resize_commit_and_reopen_real_persisted_bytes(self):
        store, user, documents = self.configure_local_user()
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode()
        for domain in (False, True):
            fs, root = self.save_mount(user, domain)
            directory = '/domain' if domain else '/normal'
            self.save_path(fs, root, 2, directory)
            path = directory + '/slot'
            self.save_path(fs, root, 0, path, struct.pack('<IIQ', 0, 0, 9))
            for name, expected in ((directory, 0), (path, 1)):
                raw, _ = self.save_path(fs, root, 7, name)
                data, _, _ = self.response(raw, domain)
                self.assertEqual(struct.unpack_from('<I', data)[0], expected)
            file, child = self.save_file(fs, root, path)
            self.save_write(file, child, b'save-data', option=1)
            self.assertEqual(self.save_read(file, child, 16), b'save-data')
            self.assertEqual(self.save_read(file, child, 8, offset=99), b'')
            self.send(file, self.request(3, struct.pack('<Q', 4), domain=child))
            raw, _ = self.send(file, self.request(4, domain=child))
            data, _, _ = self.response(raw, domain)
            self.assertEqual(struct.unpack_from('<Q', data)[0], 4)
            self.send(file, self.request(2, domain=child))
            self.send(fs, self.request(10, domain=root))
            self.assertEqual((host / path[1:]).read_bytes(), b'save')
            # An independent file object observes the actual committed bytes.
            reopened, other = self.save_file(fs, root, path, mode=1)
            self.assertEqual(self.save_read(reopened, other, 16), b'save')
        self.assertTrue(store)

    def test_save_file_access_modes_and_allow_append_are_enforced_before_mutation(self):
        _, user, documents = self.configure_local_user()
        fs, root = self.save_mount(user)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode() / 'slot'
        for mode in (0, 4, 5, 8, 0xffffffff):
            self.save_path(fs, root, 8, '/slot', struct.pack('<I', mode), good=False)
            self.save_path(fs, root, 8, '/missing', struct.pack('<I', mode), good=False)
        read, _ = self.save_file(fs, root, mode=1)
        raw, _ = self.save_write(read, 0, b'a')
        self.assert_fs_result(raw, 6203)
        raw, _ = self.send(read, self.request(3, struct.pack('<Q', 8)))
        self.assert_fs_result(raw, 6203)
        write, _ = self.save_file(fs, root, mode=2)
        buffer = C.create_string_buffer(4)
        self.ranges.append((C.addressof(buffer), 4, True))
        raw, _ = self.send(write, self.request(0, struct.pack('<IIQQ', 0, 0, 0, 4), receive=(C.addressof(buffer), 4)))
        self.assert_fs_result(raw, 6202)
        raw, _ = self.save_write(write, 0, b'ab', offset=3)
        self.assert_fs_result(raw, 6201)
        self.assertEqual(host.read_bytes(), bytes(4))
        self.save_write(write, 0, b'abcd')
        append, _ = self.save_file(fs, root, mode=6)
        self.save_write(append, 0, b'ef', offset=4)
        # AllowAppend grants growth; it does not force offsets to end-of-file.
        self.save_write(append, 0, b'Z', offset=1)
        self.assertEqual(host.read_bytes(), b'aZcdef')

    def test_save_objects_keep_original_mount_and_files_outlive_filesystem_sessions(self):
        store, user, documents = self.configure_local_user()
        first = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode()
        fs, root = self.save_mount(user)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        file, child = self.save_file(fs, root)
        other, other_user, other_documents = self.local_savefs()
        self.lib.botw_services_set_savefs(self.ctx, other)
        self.save_write(file, child, b'old!')
        self.save_path(fs, root, 2, '/original')
        self.assertEqual(self.close(fs), 0)
        self.assertEqual(self.save_read(file, child, 8), b'old!')
        self.assertEqual(self.close(file), 0)
        self.send(file, self.request(4), good=False)
        self.assertEqual((first / 'slot').read_bytes(), b'old!')
        second = other_documents / 'botw-saves' / '01007ef00011e000' / other_user.uid_hex.decode()
        self.assertEqual(list(second.iterdir()), [])
        self.lib.botw_services_set_savefs(self.ctx, store)

    def test_save_domain_file_copy_survives_domain_object_and_session_close(self):
        store, user, _ = self.configure_local_user()
        fs, root = self.save_mount(user, True)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        _, child = self.save_file(fs, root)
        raw, _ = self.send(fs, self.request(1, struct.pack('<I', child), kind=5))
        _, handles, _ = self.response(raw)
        copy = handles[0]
        for object_id in (child, root):
            close = struct.pack('<II', 4, 8) + bytes(8) + struct.pack('<BBHIII', 2, 0, 0, object_id, 0, 0)
            self.send(fs, close)
            self.send(fs, self.request(4, domain=object_id), good=False)
        self.assertEqual(self.close(fs), 0)
        other, _, _ = self.local_savefs()
        self.lib.botw_services_set_savefs(self.ctx, other)
        self.save_write(copy, 0, b'live')
        self.assertEqual(self.save_read(copy, 0, 8), b'live')
        self.assertEqual(self.close(copy), 0)
        self.assertEqual(self.close(copy), -1)
        self.lib.botw_services_set_savefs(self.ctx, store)

    def test_save_missing_exists_paths_unknown_options_and_unsupported_operations_are_not_success(self):
        _, user, documents = self.configure_local_user()
        fs, root = self.save_mount(user, True)
        raw, _ = self.save_path(fs, root, 7, '/missing')
        self.assert_fs_result(raw, 1, True)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        raw, _ = self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        self.assert_fs_result(raw, 2, True)
        for payload in (struct.pack('<IIQ', 1, 0, 4), struct.pack('<IIQ', 0, 0, 1 << 63), bytes(8)):
            self.save_path(fs, root, 0, '/unsupported', payload, good=False)
        for path in (b'/../outside', b'relative', b'/a//b', b'/bad\xff'):
            self.save_path(fs, root, 0, path, struct.pack('<IIQ', 0, 0, 4), good=False)
        for command in (1, 3, 4, 5, 6, 9, 11, 12, 999):
            _, error = self.save_path(fs, root, command, '/slot', good=False)
            self.assertIn('SaveFS', error)
            self.assertIn(str(command), error)
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode()
        self.assertEqual(sorted(x.name for x in host.iterdir()), ['slot'])

    def test_save_file_bounds_unknown_options_and_bad_full_span_do_not_partly_write(self):
        _, user, documents = self.configure_local_user()
        fs, root = self.save_mount(user, True)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 8))
        file, child = self.save_file(fs, root)
        buffer = C.create_string_buffer(b'12345678')
        self.ranges.append((C.addressof(buffer), len(buffer), False))
        for option, offset, size in ((2, 0, 8), (0, 1 << 63, 8), (0, (1 << 63) - 4, 8), (0, 0, 9)):
            self.send(file, self.request(1, struct.pack('<IIQQ', option, 0, offset, size), domain=child,
                      buffer=(C.addressof(buffer), 8)), good=False)
        self.send(file, self.request(1, struct.pack('<IIQQ', 0, 0, 0, 8), domain=child,
                  buffer=(C.addressof(buffer) + 4, 8)), good=False)
        for command, payload in ((2, b'\0'), (3, struct.pack('<Q', 1 << 63)), (4, b'\0'), (99, b'')):
            self.send(file, self.request(command, payload, domain=child), good=False)
        self.assertEqual(self.save_read(file, child, 0), b'')
        self.save_write(file, child, b'')
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode() / 'slot'
        self.assertEqual(host.read_bytes(), bytes(8))

    def test_save_read_rejects_entire_output_descriptor_overlapping_ipc_before_io(self):
        _, user, _ = self.configure_local_user()
        for domain in (False, True):
            fs, root = self.save_mount(user, domain)
            path = '/domain' if domain else '/normal'
            self.save_path(fs, root, 0, path, struct.pack('<IIQ', 0, 0, 4))
            file, child = self.save_file(fs, root, path)
            self.save_write(file, child, b'DATA')
            for relative, size in ((128, 4), (0, 4), (-1, 4), (255, 4), (-8, 272)):
                arena = C.create_string_buffer(b'\xa5' * 1024, 1024)
                base = C.addressof(arena)
                ipc = base + 256
                self.ranges.append((base, 1024, True))
                request = self.request(0, struct.pack('<IIQQ', 0, 0, 0, 4), domain=child,
                                       receive=(ipc + relative, size))
                C.memmove(ipc, request, len(request))
                before = bytes(arena)
                self.error.value = b''
                rc = self.lib.botw_services_send(self.ctx, file, ipc, 256, self.error, 1024)
                self.assertEqual(rc, -1, (relative, size, bytes(arena)[256 + relative:260 + relative]))
                self.assertIn(b'SaveFS IFile.Read output descriptor overlaps IPC message buffer', self.error.value)
                self.assertEqual(bytes(arena), before, 'read or response changed the aliased span before rejection')
            # The end of each range is exclusive: adjacent spans stay valid.
            for relative in (-4, 256):
                arena = C.create_string_buffer(1024)
                base = C.addressof(arena)
                ipc = base + 256
                self.ranges.append((base, 1024, True))
                request = self.request(0, struct.pack('<IIQQ', 0, 0, 0, 4), domain=child,
                                       receive=(ipc + relative, 4))
                C.memmove(ipc, request, len(request))
                self.assertEqual(self.lib.botw_services_send(self.ctx, file, ipc, 256, self.error, 1024), 0, self.error.value)
                data, _, _ = self.response(C.string_at(ipc, 256), domain)
                self.assertEqual(struct.unpack_from('<Q', data)[0], 4)
                self.assertEqual(C.string_at(ipc + relative, 4), b'DATA')
            # A descriptor near HIPC's maximum address still fails checked
            # memory validation before any backend access.
            self.send(file, self.request(0, struct.pack('<IIQQ', 0, 0, 0, 4), domain=child,
                      receive=((1 << 58) - 2, 4)), good=False)

    def test_save_large_io_is_chunked_after_whole_descriptor_validation(self):
        _, user, documents = self.configure_local_user()
        fs, root = self.save_mount(user)
        size = 16 * 1024 * 1024 + 37
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, size))
        file, child = self.save_file(fs, root)
        data = b'A' * (16 * 1024 * 1024) + b'B' * 37
        self.save_write(file, child, data)
        self.assertEqual(self.save_read(file, child, size + 31), data)
        self.send(file, self.request(2))
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode() / 'slot'
        self.assertEqual(host.read_bytes(), data)

    def test_save_quota_and_commit_failures_preserve_error_and_actual_storage(self):
        _, user, documents = self.configure_local_user(quota=4)
        fs, root = self.save_mount(user)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        file, child = self.save_file(fs, root, mode=7)
        raw, _ = self.save_write(file, child, b'12345')
        self.assert_fs_result(raw, 30)
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode()
        self.assertEqual((host / 'slot').read_bytes(), bytes(4))
        self.save_write(file, child, b'abcd')
        (host / 'unsafe').symlink_to(host / 'slot')
        for handle, object_id, command in ((file, child, 2), (fs, root, 10)):
            _, error = self.send(handle, self.request(command, domain=object_id), good=False)
            self.assertIn('SaveFS', error)
            self.assertIn('errno=', error)
        _, error = self.save_write(file, child, b'efgh', option=1, good=False)
        self.assertIn('actual_bytes=4', error)
        self.assertEqual((host / 'slot').read_bytes(), b'efgh')
        # Closing does not pretend to commit (nor traverse the unsafe entry).
        self.assertEqual(self.close(file), 0)
        self.assertEqual(self.close(fs), 0)

    def test_save_flush_propagates_host_fsync_failure_and_close_never_implicitly_commits(self):
        _, user, documents = self.configure_local_user()
        fs, root = self.save_mount(user)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        file, _ = self.save_file(fs, root)
        self.save_write(file, 0, b'init')
        self.lib.botw_test_savefs_sync_reset(0)
        self.assertEqual(self.close(fs), 0)
        self.assertEqual(self.lib.botw_test_savefs_sync_count(), 0)
        self.lib.botw_test_savefs_sync_reset(1)
        _, error = self.send(file, self.request(2), good=False)
        self.assertIn('IFile.Flush errno=' + str(errno.EIO), error)
        self.assertEqual(self.lib.botw_test_savefs_sync_count(), 1)
        self.lib.botw_test_savefs_sync_reset(1)
        _, error = self.save_write(file, 0, b'real', option=1, good=False)
        self.assertIn('IFile.Write Flush errno=' + str(errno.EIO), error)
        self.assertIn('actual_bytes=4', error)
        host = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode() / 'slot'
        self.assertEqual(host.read_bytes(), b'real')
        fs, root = self.save_mount(user)
        self.lib.botw_test_savefs_sync_reset(1)
        _, error = self.send(fs, self.request(10), good=False)
        self.assertIn('IFileSystem.Commit errno=' + str(errno.EIO), error)
        self.lib.botw_test_savefs_sync_reset(0)
        self.send(file, self.request(2))
        self.assertGreaterEqual(self.lib.botw_test_savefs_sync_count(), 2)
        self.lib.botw_test_savefs_sync_reset(1)
        self.assertEqual(self.close(file), 0)
        self.assertEqual(self.close(fs), 0)
        self.assertEqual(self.lib.botw_test_savefs_sync_count(), 0)
        self.lib.botw_test_savefs_sync_reset(0)

    def test_save_committed_bytes_survive_service_and_backend_close_then_remount(self):
        store, user, documents = self.configure_local_user()
        fs, root = self.save_mount(user)
        self.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 8))
        file, _ = self.save_file(fs, root)
        self.save_write(file, 0, b'persist!')
        self.send(fs, self.request(10))
        self.lib.botw_services_destroy(self.ctx)
        self.ctx = None
        # Release the actual mount's exclusive lock before opening it again.
        for i, (callback, args, _) in enumerate(self._cleanups):
            if callback == self.lib.botw_savefs_close and args == (store,):
                self._cleanups.pop(i)
                break
        else:
            self.fail('missing actual SaveFS cleanup')
        self.lib.botw_savefs_close(store)
        error = RomfsError()
        reopened_user = LocalUser()
        self.assertEqual(self.lib.botw_local_user_open(str(documents).encode(), C.byref(reopened_user), C.byref(error)), 0)
        self.assertEqual(bytes(reopened_user), bytes(user))
        reopened = self.lib.botw_savefs_open(str(documents / 'botw-saves').encode(),
                    b'01007ef00011e000', reopened_user.uid_hex, 0, C.byref(error))
        self.assertTrue(reopened, error.message)
        self.addCleanup(self.lib.botw_savefs_close, reopened)
        self.ctx = self.lib.botw_services_create(self.log, self.memory, None)
        self.assertTrue(self.ctx)
        self.lib.botw_services_set_savefs(self.ctx, reopened)
        self.assertEqual(self.lib.botw_services_set_local_user(self.ctx, reopened_user.uid,
                         b'reopened-local-file', self.error, 1024), 0, self.error.value)
        fs, root = self.save_mount(reopened_user)
        file, _ = self.save_file(fs, root, mode=1)
        self.assertEqual(self.save_read(file, 0, 16), b'persist!')

    def test_save_real_partial_host_write_keeps_error_and_changed_byte_count(self):
        # RLIMIT_FSIZE is process-wide, so isolate it from the test runner and
        # other agents. This calls the same real IPC + SaveFS chain in a child.
        script = r'''
import resource, signal, sys
sys.path.insert(0, sys.argv[1])
from test_botw_services import ServicesTests
ServicesTests.setUpClass()
t = ServicesTests()
t.setUp()
try:
    _, user, documents = t.configure_local_user()
    fs, root = t.save_mount(user)
    t.save_path(fs, root, 0, '/slot', __import__('struct').pack('<IIQ', 0, 0, 0))
    file, _ = t.save_file(fs, root, mode=6)
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (4, 4))
    _, error = t.save_write(file, 0, b'12345678', good=False)
    assert 'actual_bytes=4' in error, error
    assert 'errno=' + str(__import__('errno').EFBIG) in error, error
    path = documents / 'botw-saves' / '01007ef00011e000' / user.uid_hex.decode() / 'slot'
    assert path.read_bytes() == b'1234'
finally:
    t.tearDown()
    t.doCleanups()
    ServicesTests.tearDownClass()
'''
        result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'tests')], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def performance_handler(self):
        calls = []
        def apply(_, mode, config, active, error, capacity):
            if config not in (0x00010000, 0x00020000, 0x92220008):
                message = b'unsupported test host performance configuration\0'
                C.memmove(error, message, min(len(message), capacity))
                return -1
            calls.append((mode, config, active))
            return 0
        self.performance = PERFORMANCE(apply)
        self.lib.botw_services_set_performance_handler.argtypes = [C.c_void_p, PERFORMANCE, C.c_void_p]
        self.lib.botw_services_set_performance_handler(self.ctx, self.performance, None)
        return calls

    def apm_session(self, apm):
        raw, _ = self.send(apm, self.request(0))
        _, handles, _ = self.response(raw)
        self.assertEqual(len(handles), 1)
        return handles[0]

    def test_repeated_ipc_trace_is_bounded_without_hiding_command_errors(self):
        sm = self.initialized_sm()
        for _ in range(3):
            apm = self.service(sm, b'apm')
            self.send(apm, self.request(1))
            self.send(apm, self.request(1))
        matching = [line for line in self.logs if line.startswith(
            'ipc_request service=apm.IPerformanceManager command=1 ')]
        self.assertEqual(len(matching), 1, matching)
        for _ in range(2):
            _, error = self.send(apm, self.request(0xffffffff), good=False)
            self.assertIn('4294967295', error)
            self.assertIn('apm.IPerformanceManager', error)

    def test_apm_mode_and_configuration_have_host_backed_state(self):
        calls = self.performance_handler()
        apm = self.service(self.initialized_sm(), b'apm')
        raw, _ = self.send(apm, self.request(1))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 0)
        session = self.apm_session(apm)
        self.send(session, self.request(1, struct.pack('<I', 0)), good=False)
        self.send(session, self.request(0, struct.pack('<II', 0, 0x00010000)))
        self.send(session, self.request(0, struct.pack('<II', 1, 0x00020000)))
        self.assertEqual(calls, [(0, 0x00010000, 1), (1, 0x00020000, 0)])
        self.assertEqual(self.close(session), 0)
        session = self.apm_session(apm)
        for mode, expected in ((0, 0x00010000), (1, 0x00020000)):
            raw, _ = self.send(session, self.request(1, struct.pack('<I', mode)))
            data, _, _ = self.response(raw)
            self.assertEqual(struct.unpack_from('<I', data)[0], expected)

    def test_apm_rejected_host_configuration_preserves_prior_state(self):
        calls = self.performance_handler()
        session = self.apm_session(self.service(self.initialized_sm(), b'apm'))
        self.send(session, self.request(0, struct.pack('<II', 0, 0x92220008)))
        _, error = self.send(session, self.request(0, struct.pack('<II', 0, 0xbad)), good=False)
        self.assertIn('unsupported test host', error)
        self.send(session, self.request(0, struct.pack('<II', 2, 0x00010000)), good=False)
        self.send(session, self.request(1, struct.pack('<I', 2)), good=False)
        self.send(session, self.request(99), good=False)
        raw, _ = self.send(session, self.request(1, struct.pack('<I', 0)))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 0x92220008)
        self.assertEqual(calls, [(0, 0x92220008, 1)])

    def test_apm_set_fails_without_host_policy_handler(self):
        session = self.apm_session(self.service(self.initialized_sm(), b'apm'))
        _, error = self.send(session, self.request(0, struct.pack('<II', 0, 0x00010000)), good=False)
        self.assertIn('host performance handler', error)
        self.send(session, self.request(1, struct.pack('<I', 0)), good=False)

    def applet_kernel(self):
        self.kernel_handles, self.kernel_events = {}, []
        def allocate(event):
            handle = 0x20000 + len(self.kernel_handles)
            self.kernel_handles[handle] = event
            return handle
        def create(_, signaled, writer, reader, error, cap):
            event = {'signaled': bool(signaled)}
            self.kernel_events.append(event)
            writer[0], reader[0] = allocate(event), allocate(event)
            return 0
        def duplicate(_, handle, out, error, cap):
            if self.kernel_handles.get(handle) is None:
                return -1
            out[0] = allocate(self.kernel_handles[handle])
            return 0
        def action(name):
            def perform(_, handle, error, cap):
                event = self.kernel_handles.get(handle)
                if event is None:
                    return -1
                if name == 'close':
                    self.kernel_handles[handle] = None
                else:
                    event['signaled'] = name == 'signal'
                return 0
            return EVENT_ACTION(perform)
        self.kernel_api = KernelAPI(None, EVENT_CREATE(create), EVENT_DUP(duplicate),
                                    action('signal'), action('clear'), action('close'),
                                    PROCESS_CHECK(lambda _, handle: int(handle == 0xffff8001)))
        self.lib.botw_services_set_kernel_api.argtypes = [C.c_void_p, C.POINTER(KernelAPI)]
        self.lib.botw_services_set_kernel_api(self.ctx, C.byref(self.kernel_api))
        self.lib.botw_services_set_applet_state.argtypes = [C.c_void_p, C.c_uint64, C.c_int,
                                                          C.c_int, C.c_uint32, C.c_char_p, C.c_size_t]

    def applet_state(self, foreground=1, focused=1, mode=0, user=0x12345678):
        rc = self.lib.botw_services_set_applet_state(self.ctx, user, foreground, focused,
                                                   mode, self.error, 1024)
        self.assertEqual(rc, 0, self.error.value)

    def applet_proxy(self):
        oe = self.service(self.initialized_sm(), b'appletOE')
        raw, _ = self.send(oe, self.request(0, b'\0' * 8, pid=True, copies=(0xffff8001,)))
        _, handles, _ = self.response(raw)
        return handles[0]

    def proxy_child(self, proxy, command):
        raw, _ = self.send(proxy, self.request(command))
        _, handles, _ = self.response(raw)
        return handles[0]

    def test_applet_proxy_requires_measured_state_and_current_process_handle(self):
        self.applet_kernel()
        oe = self.service(self.initialized_sm(), b'appletOE')
        self.send(oe, self.request(0, b'\0' * 8, pid=True, copies=(0xffff8001,)), good=False)
        self.applet_state()
        self.send(oe, self.request(0, b'\0' * 8, pid=True), good=False)
        self.send(oe, self.request(0, b'\0' * 8, pid=True, copies=(0xbad,)), good=False)
        self.send(oe, self.request(0, b'\0' * 8, pid=True, copies=(0xffff8001,)))

    def test_applet_window_and_common_queries_match_host_lifecycle(self):
        self.applet_kernel()
        self.applet_state()
        proxy = self.applet_proxy()
        window = self.proxy_child(proxy, 2)
        common = self.proxy_child(proxy, 0)
        app = self.proxy_child(proxy, 20)
        raw, _ = self.send(window, self.request(1))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<Q', data)[0], 0x12345678)
        self.send(window, self.request(10))
        raw, _ = self.send(app, self.request(40))
        data, _, _ = self.response(raw)
        self.assertEqual(data[0], 1)
        self.applet_state(foreground=0, focused=0)
        raw, _ = self.send(common, self.request(9))
        data, _, _ = self.response(raw)
        self.assertEqual(data[0], 3)
        self.send(window, self.request(10), good=False)
        self.applet_state()
        for command, expected in ((5, 0), (6, 0), (9, 1)):
            raw, _ = self.send(common, self.request(command))
            data, _, _ = self.response(raw)
            self.assertEqual(data[0], expected)
        self.send(proxy, self.request(999), good=False)

    def test_applet_message_event_tracks_queue_and_notification_flags(self):
        self.applet_kernel()
        self.applet_state()
        proxy = self.applet_proxy()
        common, controller = self.proxy_child(proxy, 0), self.proxy_child(proxy, 1)
        raw, _ = self.send(common, self.request(0))
        _, handles, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', raw, 8)[0], 2)  # one HIPC copy handle
        event = self.kernel_handles[handles[0]]
        self.assertTrue(event['signaled'])  # measured initial focus is announced
        raw, _ = self.send(common, self.request(1))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 15)
        self.assertFalse(event['signaled'])
        self.applet_state()  # no duplicate notification for unchanged host state
        self.assertFalse(event['signaled'])
        self.send(controller, self.request(11, b'\1'))
        self.applet_state(mode=1)
        self.assertTrue(event['signaled'])
        raw, _ = self.send(common, self.request(1))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 30)
        self.assertFalse(event['signaled'])
        raw, _ = self.send(common, self.request(1))
        self.assertEqual(struct.unpack_from('<I', raw, 24)[0], 0x680)
        self.applet_state(foreground=0, focused=0, mode=1)
        self.assertTrue(event['signaled'])
        raw, _ = self.send(common, self.request(1))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 15)
        self.assertFalse(event['signaled'])

    def test_library_applet_storage_retains_checked_bytes_across_object_lifetimes(self):
        self.applet_kernel()
        self.applet_state()
        creator = self.proxy_child(self.applet_proxy(), 11)
        raw, _ = self.send(creator, self.request(10, struct.pack('<q', 16)))
        _, handles, _ = self.response(raw)
        storage = handles[0]
        raw, _ = self.send(storage, self.request(0))
        _, handles, _ = self.response(raw)
        accessor = handles[0]
        self.assertEqual(self.close(storage), 0)
        raw, _ = self.send(accessor, self.request(0))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<q', data)[0], 16)
        original, readback = C.create_string_buffer(b'hello applet'), C.create_string_buffer(16)
        self.ranges += [(C.addressof(original), len(original), False),
                        (C.addressof(readback), len(readback), True)]
        self.send(accessor, self.request(10, struct.pack('<q', 3), buffer=(C.addressof(original), 12)))
        self.send(accessor, self.request(11, struct.pack('<q', 0), receive=(C.addressof(readback), 16)))
        self.assertEqual(bytes(readback), b'\0\0\0hello applet\0')
        self.send(accessor, self.request(10, struct.pack('<q', -1), buffer=(C.addressof(original), 1)), good=False)
        self.send(accessor, self.request(11, struct.pack('<q', 15), receive=(C.addressof(readback), 2)), good=False)
        self.send(accessor, self.request(11, struct.pack('<q', 0), receive=(C.addressof(original), 4)), good=False)
        self.send(creator, self.request(10, struct.pack('<q', -1)), good=False)
        self.send(creator, self.request(10, struct.pack('<q', 17 * 1024 * 1024)), good=False)
        _, error = self.send(creator, self.request(0, struct.pack('<II', 0x11, 0)), good=False)
        self.assertIn('applet_id=0x11', error)

    def test_applet_audio_volume_state_is_readable_by_guest_and_host_mixer(self):
        self.applet_kernel()
        self.applet_state()
        audio = self.proxy_child(self.applet_proxy(), 3)
        for command in (1, 2):
            raw, _ = self.send(audio, self.request(command))
            data, _, _ = self.response(raw)
            self.assertEqual(struct.unpack_from('<f', data)[0], 1.0)
        self.send(audio, self.request(0, struct.pack('<ff', 0.25, 0.5)))
        self.send(audio, self.request(4, struct.pack('<f', 0.75)))
        self.send(audio, self.request(0, struct.pack('<ff', float('nan'), 0.5)), good=False)
        self.send(audio, self.request(4, struct.pack('<f', 2.0)), good=False)
        raw, _ = self.send(audio, self.request(1))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<f', data)[0], 0.25)
        self.lib.botw_services_get_audio_gain.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.POINTER(C.c_float), C.POINTER(C.c_float)]
        main, library, transparent = C.c_float(), C.c_float(), C.c_float()
        self.lib.botw_services_get_audio_gain(self.ctx, C.byref(main), C.byref(library), C.byref(transparent))
        self.assertEqual((main.value, library.value, transparent.value), (0.25, 0.5, 0.75))

    def test_display_capture_reports_availability_and_copies_real_host_snapshot(self):
        self.applet_kernel()
        self.applet_state()
        display = self.proxy_child(self.applet_proxy(), 4)
        output = C.create_string_buffer(1280 * 720 * 4)
        self.ranges.append((C.addressof(output), len(output), True))
        raw, _ = self.send(display, self.request(5, receive=(C.addressof(output), len(output))))
        data, _, _ = self.response(raw)
        self.assertEqual(data[0], 0)
        self.lib.botw_services_set_capture_image.argtypes = [C.c_void_p, C.c_uint, C.c_void_p, C.c_size_t, C.c_char_p, C.c_size_t]
        snapshot = C.create_string_buffer(bytes(range(256)) * (len(output) // 256))
        self.assertEqual(self.lib.botw_services_set_capture_image(self.ctx, 0, snapshot, len(output), self.error, 1024), 0)
        raw, _ = self.send(display, self.request(5, receive=(C.addressof(output), len(output))))
        data, _, _ = self.response(raw)
        self.assertEqual(data[0], 1)
        self.assertEqual(bytes(output), bytes(snapshot)[:len(output)])
        self.send(display, self.request(5, receive=(C.addressof(output), 4)), good=False)
        self.assertEqual(self.lib.botw_services_set_capture_image(self.ctx, 0, None, 0, self.error, 1024), 0)
        raw, _ = self.send(display, self.request(5, receive=(C.addressof(output), len(output))))
        data, _, _ = self.response(raw)
        self.assertEqual(data[0], 0)

    def attach_romfs(self, image):
        self.romfs_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.romfs_temp.cleanup)
        path = Path(self.romfs_temp.name) / 'romfs.bin'
        path.write_bytes(image)
        self.lib.botw_romfs_open.argtypes = [C.c_char_p, C.POINTER(RomfsError)]
        self.lib.botw_romfs_open.restype = C.c_void_p
        self.lib.botw_romfs_close.argtypes = [C.c_void_p]
        error = RomfsError()
        mount = self.lib.botw_romfs_open(str(path).encode(), C.byref(error))
        self.assertTrue(mount, error.message)
        self.addCleanup(self.lib.botw_romfs_close, mount)
        self.lib.botw_services_set_romfs.argtypes = [C.c_void_p, C.c_void_p]
        self.lib.botw_services_set_romfs(self.ctx, mount)
        return mount

    def filesystem(self):
        fsp = self.service(self.initialized_sm(), b'fsp-srv')
        self.send(fsp, self.request(1, b'\0' * 8, pid=True))
        return fsp

    def test_fs_access_log_mode_reports_disabled_policy_and_strict_no_input_abi(self):
        fsp = self.service(self.initialized_sm(), b'fsp-srv')
        _, error = self.send(fsp, self.request(1005), good=False)
        self.assertIn('initialized', error)
        self.send(fsp, self.request(1, b'\0' * 8, pid=True))
        for padding in (0, 4, 8, 12):
            request = struct.pack('<II', 4, 6 + padding // 4) + b'\0' * 8 + b'SFCI' + struct.pack('<III', 0, 1005, 0) + bytes(padding)
            raw, _ = self.send(fsp, request)
            data, handles, count = self.response(raw)
            self.assertEqual((struct.unpack_from('<I', data)[0], handles, count), (0, [], 0))
        self.send(fsp, self.request(1005, b'\1'), good=False)
        self.send(fsp, self.request(1005, bytes(16)), good=False)
        self.send(fsp, self.request(1005, pid=True), good=False)
        self.send(fsp, self.request(1005, copies=(0xffff8001,)), good=False)
        input = C.create_string_buffer(b'access log')
        self.ranges.append((C.addressof(input), len(input), True))
        self.send(fsp, self.request(1005, buffer=(C.addressof(input), len(input))), good=False)
        self.send(fsp, self.request(1005, receive=(C.addressof(input), len(input))), good=False)
        # No actual SDK Log/SD sink is connected. Settings and SD output cannot
        # report success, and none of these refusals changes the getter policy.
        for mode in (0, 1, 2, 3):
            self.send(fsp, self.request(1004, struct.pack('<I', mode)), good=False)
        self.send(fsp, self.request(1006, buffer=(C.addressof(input), len(input))), good=False)
        self.send(fsp, self.request(1007), good=False)
        raw, _ = self.send(fsp, self.request(1005))
        self.assertEqual(struct.unpack_from('<I', self.response(raw)[0])[0], 0)

    def test_domain_fs_log_mode_has_no_objects_and_internal_read_diagnostics_are_independent(self):
        image, _ = romfs_fixture()
        self.attach_romfs(image)
        fsp = self.filesystem()
        raw, _ = self.send(fsp, self.request(0, kind=5))
        root, = struct.unpack_from('<I', self.response(raw)[0])
        raw, _ = self.send(fsp, self.request(1005, domain=root))
        data, handles, count = self.response(raw, domain=True)
        self.assertEqual((struct.unpack_from('<I', data)[0], handles, count), (0, [], 0))
        self.send(fsp, self.request(1005, b'\0', domain=root), good=False)
        raw, _ = self.send(fsp, self.request(200, domain=root))
        storage, = struct.unpack_from('<I', self.response(raw, domain=True)[0])
        self.assertEqual(storage, root + 1)
        output = C.create_string_buffer(80)
        self.ranges.append((C.addressof(output), len(output), True))
        self.send(fsp, self.request(0, struct.pack('<qQ', 0, len(output)), domain=storage,
                                    receive=(C.addressof(output), len(output))))
        self.assertEqual(bytes(output), image[:80])
        self.assertTrue(any(line.startswith('romfs_read ') for line in self.logs), self.logs)

    def test_patch_storage_unknown_is_distinct_from_verified_not_installed(self):
        fsp = self.filesystem()
        _, error = self.send(fsp, self.request(203), good=False)
        self.assertIn('installation state is unknown', error)
        self.lib.botw_services_set_patch_absent.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_size_t]
        for context, absent in ((None, 1), (self.ctx, 2), (self.ctx, -1)):
            self.assertEqual(self.lib.botw_services_set_patch_absent(context, absent, self.error, 1024), -1)
        self.assertEqual(self.lib.botw_services_set_patch_absent(self.ctx, 1, self.error, 1024), 0)
        for _ in range(3):
            raw, _ = self.send(fsp, self.request(203))
            self.assertEqual(raw[16:20], b'SFCO')
            self.assertEqual(struct.unpack_from('<I', raw, 24)[0], 0x7d402)
            self.assertEqual(struct.unpack_from('<I', raw, 4)[0] >> 31, 0)
        self.send(fsp, self.request(203, b'\1'), good=False)
        self.send(fsp, self.request(203, pid=True), good=False)
        _, error = self.send(fsp, self.request(204), good=False)
        self.assertIn('unsupported', error)
        self.assertEqual(self.lib.botw_services_set_patch_absent(self.ctx, 0, self.error, 1024), 0)
        _, error = self.send(fsp, self.request(203), good=False)
        self.assertIn('installation state is unknown', error)

    def test_domain_missing_patch_returns_no_object_and_keeps_base_storage_readable(self):
        image, _ = romfs_fixture()
        self.attach_romfs(image)
        fsp = self.filesystem()
        self.lib.botw_services_set_patch_absent.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_size_t]
        self.assertEqual(self.lib.botw_services_set_patch_absent(self.ctx, 1, self.error, 1024), 0)
        raw, _ = self.send(fsp, self.request(0, kind=5))
        data, _, _ = self.response(raw)
        root, = struct.unpack_from('<I', data)
        raw, _ = self.send(fsp, self.request(203, domain=root))
        self.assertEqual(struct.unpack_from('<I', raw, 16)[0], 0)
        self.assertEqual(raw[32:36], b'SFCO')
        self.assertEqual(struct.unpack_from('<I', raw, 40)[0], 0x7d402)
        self.assertEqual(struct.unpack_from('<I', raw, 4)[0] >> 31, 0)
        raw, _ = self.send(fsp, self.request(200, domain=root))
        data, handles, count = self.response(raw, domain=True)
        self.assertEqual((handles, count), ([], 1))
        storage, = struct.unpack_from('<I', data)
        self.assertEqual(storage, root + 1)
        output = C.create_string_buffer(80)
        self.ranges.append((C.addressof(output), len(output), True))
        self.send(fsp, self.request(0, struct.pack('<qQ', 0, len(output)), domain=storage,
                                    receive=(C.addressof(output), len(output))))
        self.assertEqual(bytes(output), image[:80])

    def test_domain_read_reuses_missing_patch_response_without_clearing_domain_padding(self):
        image, _ = romfs_fixture()
        self.attach_romfs(image)
        fsp = self.filesystem()
        raw, _ = self.send(fsp, self.request(0, kind=5))
        root, = struct.unpack_from('<I', self.response(raw)[0])
        raw, _ = self.send(fsp, self.request(200, domain=root))
        storage, = struct.unpack_from('<I', self.response(raw, domain=True)[0])
        self.lib.botw_services_set_patch_absent.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_size_t]
        self.assertEqual(self.lib.botw_services_set_patch_absent(self.ctx, 1, self.error, 1024), 0)
        ipc, output = C.create_string_buffer(256), C.create_string_buffer(80)
        self.ranges.extend([(C.addressof(ipc), len(ipc), True),
                            (C.addressof(output), len(output), True)])
        patch_request = self.request(203, domain=root)
        C.memmove(ipc, patch_request, len(patch_request))
        self.assertEqual(self.lib.botw_services_send(self.ctx, fsp, ipc, len(ipc), self.error, 1024), 0,
                         self.error.value)
        self.assertEqual(struct.unpack_from('<I', ipc, 40)[0], 0x7d402)

        # A B descriptor moves the next domain header from IPC+16 to IPC+32.
        # BOTW's SDK writes the fields but leaves its padding at IPC+40 intact:
        # that word still contains the preceding error response's Result.
        read_request = self.request(0, struct.pack('<qQ', 0, len(output)), domain=storage,
                                    receive=(C.addressof(output), len(output)))
        C.memmove(ipc, read_request[:40], 40)
        C.memmove(C.addressof(ipc) + 44, read_request[44:], len(read_request) - 44)
        self.assertEqual(struct.unpack_from('<BBHIII', ipc, 32), (1, 0, 32, storage, 0x7d402, 0))
        self.assertEqual(self.lib.botw_services_send(self.ctx, fsp, ipc, len(ipc), self.error, 1024), 0,
                         self.error.value)
        self.assertEqual(bytes(output), image[:80])
        _, handles, count = self.response(bytes(ipc), domain=True)
        self.assertEqual((handles, count), ([], 0))

    def test_domain_padding_and_tokens_do_not_replace_real_input_object_count(self):
        fsp = self.filesystem()
        raw, _ = self.send(fsp, self.request(0, kind=5))
        root, = struct.unpack_from('<I', self.response(raw)[0])
        for kind, version in ((4, 0), (6, 1)):
            request = bytearray(self.request(1005, kind=kind, domain=root))
            struct.pack_into('<II', request, 24, 0xffffffff, 0x87654321)  # padding, domain token
            struct.pack_into('<I', request, 36, version)
            struct.pack_into('<I', request, 44, 0x12345678)  # CMIF token, distinct from domain token
            raw, _ = self.send(fsp, bytes(request))
            self.assertEqual(struct.unpack_from('<I', raw, 16)[0], 0)
            self.assertEqual(struct.unpack_from('<4sIII', raw, 32), (b'SFCO', 0, 0, 0x12345678))
            self.assertEqual(struct.unpack_from('<I', raw, 48)[0], 0)
            request[17] = 1  # This field really is num_in_objects, not padding/token.
            _, error = self.send(fsp, bytes(request), good=False)
            self.assertIn('input objects', error)
            self.assertIn('objects=1', error)

        # Ignoring domain padding does not relax independent header validation.
        request = bytearray(self.request(1005, domain=root))
        struct.pack_into('<I', request, 24, 0x7d402)
        for offset, value in ((4, struct.unpack_from('<I', request, 4)[0] | (1 << 14)),
                              (16, 3 | (16 << 16)), (16, 1 | (15 << 16)),
                              (16, 1 | (0xffff << 16)), (36, 2)):
            invalid = bytearray(request)
            struct.pack_into('<I', invalid, offset, value)
            self.send(fsp, bytes(invalid), good=False)

    def test_fsp_raw_storage_reads_actual_romfs_in_chunks_larger_than_backend_limit(self):
        payload = bytes(range(256)) * (27 * 1024 * 1024 // 256)
        image, _ = romfs_fixture([('/Bootup.pack', payload)])
        self.attach_romfs(image)
        fsp = self.filesystem()
        raw, _ = self.send(fsp, self.request(200))
        _, handles, _ = self.response(raw)
        storage = handles[0]
        raw, _ = self.send(storage, self.request(4))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<Q', data)[0], len(image))
        output = C.create_string_buffer(len(payload))
        self.ranges.append((C.addressof(output), len(output), True))
        self.send(storage, self.request(0, struct.pack('<qQ', 0x200, len(output)), receive=(C.addressof(output), len(output))))
        self.assertEqual(bytes(output), payload)
        self.send(storage, self.request(0, struct.pack('<qQ', -1, 1), receive=(C.addressof(output), 1)), good=False)
        self.send(storage, self.request(0, struct.pack('<qQ', len(image) - 1, 2), receive=(C.addressof(output), 2)), good=False)
        self.send(storage, self.request(1, struct.pack('<qQ', 0, 1), buffer=(C.addressof(output), 1)), good=False)

    def test_fsp_file_path_and_read_only_handle_use_same_backend(self):
        image, _ = romfs_fixture()
        self.attach_romfs(image)
        raw, _ = self.send(self.filesystem(), self.request(2))
        _, handles, _ = self.response(raw)
        fs = handles[0]
        path = self.static_buffer(b'/System/Actor.byml')
        self.ranges.append((C.addressof(path), len(path), False))
        raw, _ = self.send(fs, self.request(7, static=(C.addressof(path), len(path))))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 1)
        self.send(fs, self.request(8, struct.pack('<I', 3), static=(C.addressof(path), len(path))), good=False)
        raw, _ = self.send(fs, self.request(8, struct.pack('<I', 1), static=(C.addressof(path), len(path))))
        _, handles, _ = self.response(raw)
        file = handles[0]
        output = C.create_string_buffer(16)
        self.ranges.append((C.addressof(output), len(output), True))
        raw, _ = self.send(file, self.request(0, struct.pack('<IIqQ', 0, 0, 2, 16), receive=(C.addressof(output), 16)))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<Q', data)[0], 8)
        self.assertEqual(bytes(output)[:8], b'tor data')
        self.assertEqual(self.close(fs), 0)
        raw, _ = self.send(file, self.request(4))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<Q', data)[0], 10)

    def test_debug_invalidation_clears_actual_display_transition_backing(self):
        self.applet_kernel()
        self.applet_state()
        proxy = self.applet_proxy()
        display, debug = self.proxy_child(proxy, 4), self.proxy_child(proxy, 1000)
        self.send(display, self.request(21, struct.pack('<I', 0x44332211)))
        self.lib.botw_services_get_transition_image.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_uint64)]
        pixels = C.create_string_buffer(1280 * 720 * 4)
        generation = C.c_uint64()
        self.assertEqual(self.lib.botw_services_get_transition_image(self.ctx, pixels, len(pixels), C.byref(generation)), 1)
        self.assertEqual(bytes(pixels)[:8], bytes.fromhex('1122334411223344'))
        self.assertEqual(generation.value, 1)
        self.send(debug, self.request(20))
        self.assertEqual(self.lib.botw_services_get_transition_image(self.ctx, pixels, len(pixels), C.byref(generation)), 0)
        self.assertEqual(generation.value, 2)
        self.send(debug, self.request(0xbad), good=False)

    def test_hid_maps_same_input_backing_and_applies_real_npad_configuration(self):
        self.applet_kernel()
        self.applet_state()
        self.lib.botw_input_create.restype = C.c_void_p
        self.lib.botw_input_destroy.argtypes = [C.c_void_p]
        self.lib.botw_input_set_source.argtypes = [C.c_void_p, C.c_int, C.c_uint64, C.c_int32, C.c_int32, C.c_int32, C.c_int32, C.c_int]
        self.lib.botw_input_shared_memory.argtypes = [C.c_void_p, C.POINTER(C.c_size_t)]
        self.lib.botw_input_shared_memory.restype = C.c_void_p
        input = self.lib.botw_input_create()
        self.assertTrue(input)
        self.addCleanup(self.lib.botw_input_destroy, input)
        self.kernel_handles[0x30000] = {'shared_memory': True}
        self.lib.botw_services_set_input.argtypes = [C.c_void_p, C.c_void_p, C.c_uint32]
        self.lib.botw_services_set_input(self.ctx, input, 0x30000)
        hid = self.service(self.initialized_sm(), b'hid')
        self.send(hid, self.request(0, struct.pack('<Q', 0xbad), pid=True), good=False)
        raw, _ = self.send(hid, self.request(0, struct.pack('<Q', 0x12345678), pid=True))
        _, handles, _ = self.response(raw)
        resource = handles[0]
        raw, _ = self.send(resource, self.request(0))
        _, handles, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', raw, 8)[0], 2)
        self.assertIs(self.kernel_handles[handles[0]], self.kernel_handles[0x30000])
        self.send(hid, self.request(100, struct.pack('<IIQ', 3, 0, 0x12345678), pid=True))
        ids = self.static_buffer(struct.pack('<II', 0, 0x20))
        self.ranges.append((C.addressof(ids), len(ids), False))
        self.send(hid, self.request(102, struct.pack('<Q', 0x12345678), static=(C.addressof(ids), 8), pid=True))
        self.assertEqual(self.lib.botw_input_set_source(input, 0, 1, 123, -234, 0, 0, 1), 0)
        self.send(hid, self.request(103, struct.pack('<Q', 0x12345678), pid=True))
        memory = self.lib.botw_input_shared_memory(input, None)
        raw = C.string_at(memory + 0x9a00 + 8 * 0x5000, 0x700)
        self.assertEqual(struct.unpack_from('<I', raw)[0], 2)
        ring = 0x28 + 0x350
        tail = struct.unpack_from('<Q', raw, ring + 16)[0]
        self.assertEqual(struct.unpack_from('<Q', raw, ring + 32 + tail * 0x30 + 16)[0], 1)
        event_request = struct.pack('<IIQQ', 0x20, 0, 0x12345678, 0)
        raw, _ = self.send(hid, self.request(106, event_request, pid=True))
        _, handles, _ = self.response(raw)
        event = self.kernel_handles[handles[0]]
        event['signaled'] = False
        self.send(hid, self.request(107, struct.pack('<IIQ', 0x20, 0, 0x12345678), pid=True))
        self.assertTrue(event['signaled'])
        self.send(hid, self.request(100, struct.pack('<IIQ', 0x80000000, 0, 0x12345678), pid=True), good=False)

    def test_self_controller_focus_policy_requires_actual_host_handler(self):
        self.applet_kernel()
        self.applet_state()
        controller = self.proxy_child(self.applet_proxy(), 1)
        _, error = self.send(controller, self.request(13, bytes([0, 0, 1])), good=False)
        self.assertIn('host focus handler', error)
        calls = []
        self.focus = FOCUS(lambda _, mode, foreground, focused, suspended, error, cap:
                           calls.append((mode, foreground, focused, suspended)) or 0)
        self.lib.botw_services_set_focus_handler.argtypes = [C.c_void_p, FOCUS, C.c_void_p]
        self.lib.botw_services_set_focus_handler(self.ctx, self.focus, None)
        self.send(controller, self.request(13, bytes([0, 0, 1])))
        self.assertEqual(calls[-1], (0, 1, 1, 0))
        self.applet_state(foreground=0, focused=0)
        self.assertEqual(calls[-1], (0, 0, 0, 1))
        self.send(controller, self.request(13, bytes([1, 1, 0])))
        self.assertEqual(calls[-1], (1, 0, 0, 0))
        self.send(controller, self.request(16, b'\1'))
        self.assertEqual(calls[-1], (3, 0, 0, 1))
        self.applet_state()
        self.assertEqual(calls[-1], (3, 1, 1, 0))
        before = len(calls)
        self.send(controller, self.request(13, bytes([1, 0, 0])), good=False)
        self.assertEqual(len(calls), before)

    def test_restart_message_reports_real_execution_resume_and_preserves_queued_messages(self):
        self.applet_kernel()
        self.applet_state()
        proxy = self.applet_proxy()
        common, controller = self.proxy_child(proxy, 0), self.proxy_child(proxy, 1)
        raw, _ = self.send(common, self.request(0))
        _, handles, _ = self.response(raw)
        event = self.kernel_handles[handles[0]]

        def message():
            raw, _ = self.send(common, self.request(1))
            data, _, _ = self.response(raw)
            return struct.unpack_from('<I', data)[0]

        self.assertEqual(message(), 15)
        calls = []
        self.focus = FOCUS(lambda _, mode, foreground, focused, suspended, error, cap:
                           calls.append((mode, foreground, focused, suspended)) or 0)
        self.lib.botw_services_set_focus_handler.argtypes = [C.c_void_p, FOCUS, C.c_void_p]
        self.lib.botw_services_set_focus_handler(self.ctx, self.focus, None)
        self.send(controller, self.request(13, bytes([0, 0, 1])))
        before = len(calls)
        self.send(controller, self.request(14, b'\1'))
        self.assertEqual(len(calls), before)  # subscription does not change focus policy
        self.assertFalse(event['signaled'])  # enabling does not fabricate a resume
        self.send(controller, self.request(14, b'\2'), good=False)
        self.applet_state(foreground=0, focused=0)
        self.assertEqual(calls[-1], (0, 0, 0, 1))
        self.assertEqual(message(), 15)
        self.assertFalse(event['signaled'])
        self.applet_state()
        self.assertEqual(calls[-1], (0, 1, 1, 0))
        self.assertEqual(message(), 15)
        self.assertTrue(event['signaled'])  # Resume is independently queued
        self.send(controller, self.request(14, b'\0'))
        self.assertEqual(message(), 16)  # disable only filters future notifications
        self.assertFalse(event['signaled'])
        self.applet_state(foreground=0, focused=0)
        self.assertEqual(message(), 15)
        self.applet_state()
        self.assertEqual(message(), 15)
        self.assertFalse(event['signaled'])

    def test_restart_notification_requires_suspension_and_accepted_host_resume(self):
        self.applet_kernel()
        self.applet_state()
        proxy = self.applet_proxy()
        common, controller = self.proxy_child(proxy, 0), self.proxy_child(proxy, 1)
        self.send(common, self.request(1))  # initial focus notification
        self.send(controller, self.request(14, b'\1'))

        def messages():
            values = []
            while True:
                raw, _ = self.send(common, self.request(1))
                if struct.unpack_from('<I', raw, 24)[0] == 0x680:
                    return values
                data, _, _ = self.response(raw)
                values.append(struct.unpack_from('<I', data)[0])

        self.applet_state(foreground=0, focused=0)
        self.assertEqual(messages(), [15])
        self.applet_state()
        self.assertEqual(messages(), [15])  # no execution policy was installed
        reject = [False]
        self.focus = FOCUS(lambda *_: -1 if reject[0] else 0)
        self.lib.botw_services_set_focus_handler.argtypes = [C.c_void_p, FOCUS, C.c_void_p]
        self.lib.botw_services_set_focus_handler(self.ctx, self.focus, None)
        self.send(controller, self.request(13, bytes([1, 1, 0])))
        self.applet_state(foreground=0, focused=0)
        self.assertEqual(messages(), [15])
        self.applet_state()
        self.assertEqual(messages(), [15])  # NoSuspend has no resume edge
        self.send(controller, self.request(13, bytes([0, 0, 1])))
        self.applet_state(foreground=0, focused=0)
        self.assertEqual(messages(), [15])
        reject[0] = True
        rc = self.lib.botw_services_set_applet_state(self.ctx, 0x12345678, 1, 1, 0,
                                                    self.error, 1024)
        self.assertEqual(rc, -1)
        self.assertEqual(messages(), [])  # rejected transition publishes no lifecycle change
        reject[0] = False
        self.applet_state()
        self.assertEqual(messages(), [15, 16])

    def addon_state(self, ids=(), app=0x01007ef00011e000, base=0):
        self.lib.botw_services_set_addon_content.argtypes = [C.c_void_p, C.c_uint64, C.c_uint64,
            C.POINTER(C.c_uint32), C.c_size_t, C.c_char_p, C.c_size_t]
        values = (C.c_uint32 * len(ids))(*ids)
        return self.lib.botw_services_set_addon_content(self.ctx, app, base, values, len(ids), self.error, 1024)

    def test_aoc_enumerates_only_installed_content_and_signals_actual_set_changes(self):
        self.applet_kernel()
        self.assertEqual(self.addon_state(), 0)
        aoc = self.service(self.initialized_sm(), b'aoc:u')
        raw, _ = self.send(aoc, self.request(2, b'\0' * 8, pid=True))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 0)
        raw, _ = self.send(aoc, self.request(8))
        _, handles, _ = self.response(raw)
        event = self.kernel_handles[handles[0]]
        self.assertFalse(event['signaled'])
        output = C.create_string_buffer(b'\xaa' * 16)
        self.ranges.append((C.addressof(output), len(output), True))
        raw, _ = self.send(aoc, self.request(3, struct.pack('<IIQ', 0, 4, 0), pid=True,
                                           receive=(C.addressof(output), 16)))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 0)
        self.assertEqual(bytes(output)[:16], b'\xaa' * 16)
        self.assertEqual(self.addon_state((2, 1)), 0)
        self.assertTrue(event['signaled'])
        event['signaled'] = False
        self.assertEqual(self.addon_state((1, 2)), 0)
        self.assertFalse(event['signaled'])
        raw, _ = self.send(aoc, self.request(3, struct.pack('<IIQ', 1, 2, 0), pid=True,
                                           receive=(C.addressof(output), 16)))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<I', data)[0], 1)
        self.assertEqual(struct.unpack_from('<I', output)[0], 2)
        self.assertEqual(self.addon_state((1, 1)), -1)
        self.send(aoc, self.request(5, b'\0' * 8, pid=True), good=False)
        self.assertEqual(self.addon_state((1, 2), base=0x01007ef00011f000), 0)
        raw, _ = self.send(aoc, self.request(5, b'\0' * 8, pid=True))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<Q', data)[0], 0x01007ef00011f000)
        self.send(aoc, self.request(0, struct.pack('<Q', 0xbad)), good=False)

    def parental_policy(self, communication=0, sns=0, stereo=0):
        self.lib.botw_services_set_parental_policy.argtypes = [C.c_void_p, C.c_char_p,
            C.c_int, C.c_int, C.c_int, C.c_char_p, C.c_size_t]
        return self.lib.botw_services_set_parental_policy(self.ctx, b'local-user-profile',
            communication, sns, stereo, self.error, 1024)

    def test_parental_service_reflects_local_policy_without_claiming_account_rights(self):
        self.applet_kernel()
        factory = self.service(self.initialized_sm(), b'pctl:a')
        self.send(factory, self.request(0, b'\0' * 8, pid=True), good=False)
        self.assertEqual(self.parental_policy(), 0)
        raw, _ = self.send(factory, self.request(0, b'\0' * 8, pid=True))
        _, handles, _ = self.response(raw)
        service = handles[0]
        for command in (1006, 1031, 1403):
            raw, _ = self.send(service, self.request(command))
            data, _, _ = self.response(raw)
            self.assertEqual(data[0], 0)
        self.send(service, self.request(1001))
        raw, _ = self.send(service, self.request(1432))
        _, handles, _ = self.response(raw)
        event = self.kernel_handles[handles[0]]
        self.assertFalse(event['signaled'])
        self.assertEqual(self.parental_policy(communication=1, sns=1, stereo=1), 0)
        self.assertTrue(event['signaled'])
        raw, _ = self.send(service, self.request(1031))
        data, _, _ = self.response(raw)
        self.assertEqual(data[0], 1)
        raw, _ = self.send(service, self.request(1001))
        self.assertEqual(struct.unpack_from('<I', raw, 24)[0], 142 | (133 << 9))
        raw, _ = self.send(service, self.request(1013))
        self.assertEqual(struct.unpack_from('<I', raw, 24)[0], 142 | (104 << 9))
        raw, _ = self.send(factory, self.request(1, b'\0' * 8, pid=True))
        _, handles, _ = self.response(raw)
        uninitialized = handles[0]
        self.send(uninitialized, self.request(1031), good=False)
        self.send(uninitialized, self.request(1))
        self.send(uninitialized, self.request(1031))
        self.send(service, self.request(1201), good=False)  # no fabricated PIN unlock

    def test_sm_requires_initialize_then_returns_real_lm_session(self):
        sm = self.connect()
        self.send(sm, self.request(1, b'lm\0\0\0\0\0\0'), good=False)
        self.send(sm, self.request(0, b'\0' * 8, pid=True))
        lm = self.service(sm)
        raw, _ = self.send(lm, self.request(0, b'\0' * 8, pid=True))
        _, handles, _ = self.response(raw)
        self.assertEqual(len(handles), 1)
        self.assertNotEqual(handles[0], lm)

    def test_unknown_named_port_and_service_are_rejected(self):
        handle = C.c_uint32(0xdead)
        self.assertEqual(self.lib.botw_services_connect(self.ctx, b'fake:', 5, C.byref(handle),
                                                       self.error, 1024), -1)
        self.assertEqual(handle.value, 0xdead)
        sm = self.initialized_sm()
        _, error = self.send(sm, self.request(1, b'unknown\0'), good=False)
        self.assertIn('unknown', error)

    def test_initialize_requires_pid_and_repeated_initialization_rejected(self):
        sm = self.connect()
        self.send(sm, self.request(0, b'\0' * 8), good=False)
        self.send(sm, self.request(0, b'\0' * 8, pid=True))
        self.send(sm, self.request(0, b'\0' * 8, pid=True), good=False)

    def test_handle_close_rejects_stale_handle_and_keeps_clone_alive(self):
        sm = self.initialized_sm()
        raw, _ = self.send(sm, self.request(2, kind=5))
        _, handles, _ = self.response(raw)
        clone = handles[0]
        self.assertEqual(self.close(sm), 0)
        self.assertEqual(self.close(sm), -1)
        self.send(sm, self.request(3, kind=5), good=False)
        self.service(clone)
        new = self.connect()
        self.assertNotEqual(new, sm)

    def test_control_pointer_size_reports_zero_when_no_server_pointer_pool(self):
        sm = self.connect()
        raw, _ = self.send(sm, self.request(3, kind=5))
        data, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<H', data)[0], 0)
        self.send(sm, self.request(77, kind=5), good=False)

    def test_domain_child_and_close_lifetime(self):
        lm = self.service(self.initialized_sm())
        raw, _ = self.send(lm, self.request(0, kind=5))
        data, _, _ = self.response(raw)
        root, = struct.unpack_from('<I', data)
        raw, _ = self.send(lm, self.request(0, b'\0' * 8, pid=True, domain=root))
        data, handles, count = self.response(raw, domain=True)
        self.assertEqual(handles, [])
        self.assertEqual(count, 1)
        logger, = struct.unpack_from('<I', data)
        self.assertNotEqual(root, logger)
        raw, _ = self.send(lm, self.request(1, struct.pack('<I', logger), kind=5))
        _, handles, _ = self.response(raw)
        copy = handles[0]
        close = struct.pack('<II', 4, 8) + b'\0' * 8 + struct.pack('<BBHIII', 2, 0, 0, logger, 0, 0)
        self.send(lm, close)
        self.send(lm, self.request(1, struct.pack('<I', 1), domain=logger), good=False)
        self.send(copy, self.request(1, struct.pack('<I', 1)))

    def test_real_log_packet_text_is_emitted_from_checked_static_descriptor(self):
        lm = self.service(self.initialized_sm())
        raw, _ = self.send(lm, self.request(0, b'\0' * 8, pid=True))
        _, handles, _ = self.response(raw)
        text = b'BOTW SDK logger reached'
        chunks = bytes([2, len(text)]) + text
        packet = self.static_buffer(struct.pack('<QQBBBBI', 1, 2, 7, 0, 1, 0, len(chunks)) + chunks)
        self.ranges.append((C.addressof(packet), len(packet), False))
        self.send(handles[0], self.request(0, static=(C.addressof(packet), len(packet) - 1)))
        self.send(handles[0], self.request(0, static=(C.addressof(packet), len(packet) - 1)))
        self.assertEqual(sum(text.decode() in line for line in self.logs), 2, self.logs)

    def test_sm_get_service_returns_kernel_move_handle_even_in_domain(self):
        sm = self.initialized_sm()
        raw, _ = self.send(sm, self.request(0, kind=5))
        data, _, _ = self.response(raw)
        root, = struct.unpack_from('<I', data)
        raw, _ = self.send(sm, self.request(1, b'lm'.ljust(8, b'\0'), domain=root))
        _, handles, count = self.response(raw, domain=True)
        self.assertEqual(count, 0)
        self.assertEqual(len(handles), 1)
        self.send(handles[0], self.request(0, b'\0' * 8, pid=True))

    def test_log_destination_is_global_and_zero_disables_host_sink(self):
        lm = self.service(self.initialized_sm())
        loggers = []
        for _ in range(2):
            raw, _ = self.send(lm, self.request(0, b'\0' * 8, pid=True))
            _, handles, _ = self.response(raw)
            loggers.append(handles[0])
        self.send(loggers[0], self.request(1, struct.pack('<I', 8)), good=False)
        self.send(loggers[0], self.request(1, struct.pack('<I', 0)))
        text = b'disabled sink must stay silent'
        chunks = bytes([2, len(text)]) + text
        packet = C.create_string_buffer(struct.pack('<QQBBBBI', 1, 2, 7, 0, 1, 0, len(chunks)) + chunks)
        self.ranges.append((C.addressof(packet), len(packet), False))
        self.send(loggers[1], self.request(0, buffer=(C.addressof(packet), len(packet) - 1)))
        self.assertFalse(any(text.decode() in line for line in self.logs))
        self.send(loggers[0], self.request(1, struct.pack('<I', 2)))
        self.send(loggers[1], self.request(0, buffer=(C.addressof(packet), len(packet) - 1)))
        self.assertTrue(any(text.decode() in line for line in self.logs))

    def test_invalid_log_buffer_and_truncated_packet_are_rejected(self):
        lm = self.service(self.initialized_sm())
        raw, _ = self.send(lm, self.request(0, b'\0' * 8, pid=True))
        _, handles, _ = self.response(raw)
        _, error = self.send(handles[0], self.request(0, static=(0x123400, 24)), good=False)
        self.assertIn('guest', error)
        packet = C.create_string_buffer(struct.pack('<QQBBBBI', 1, 2, 7, 0, 1, 0, 100))
        self.ranges.append((C.addressof(packet), len(packet), False))
        self.send(handles[0], self.request(0, buffer=(C.addressof(packet), 24)), good=False)

    def test_malformed_headers_cannot_escape_ipc_capacity(self):
        sm = self.connect()
        fixtures = [b'', struct.pack('<II', 4, 0x3ff), struct.pack('<II', 0xffff0004, 8),
                    struct.pack('<III', 4, 0x80000008, 0x1ff),
                    self.request(0).replace(b'SFCI', b'BAD!')]
        for fixture in fixtures:
            with self.subTest(fixture=fixture.hex()):
                self.send(sm, fixture, good=False)
        self.send(sm, self.request(3, kind=5), capacity=31, good=False)

    def test_ipc_memory_callback_required_and_checked(self):
        sm = self.connect()
        ipc = C.create_string_buffer(self.request(3, kind=5).ljust(256, b'\0'))
        self.assertEqual(self.lib.botw_services_send(self.ctx, sm, ipc, 256, self.error, 1024), -1)

    def test_hipc_close_ends_session_before_kernel_handle_is_closed(self):
        sm = self.initialized_sm()
        self.send(sm, struct.pack('<II', 2, 0))
        _, error = self.send(sm, self.request(3, kind=5), good=False)
        self.assertIn('closed', error)
        self.assertEqual(self.close(sm), 0)
        self.assertEqual(self.close(sm), -1)

    def test_hipc_close_does_not_close_independent_clone_endpoint(self):
        sm = self.initialized_sm()
        raw, _ = self.send(sm, self.request(2, kind=5))
        _, handles, _ = self.response(raw)
        clone = handles[0]
        self.send(sm, struct.pack('<II', 2, 0))
        self.send(sm, self.request(3, kind=5), good=False)
        self.service(clone)
        self.assertEqual(self.close(sm), 0)
        self.service(clone)
        self.assertEqual(self.close(clone), 0)

    def test_parallel_handle_creation_and_close_are_serialized(self):
        def work(_):
            handles = []
            for _ in range(64):
                handle, error = C.c_uint32(), C.create_string_buffer(256)
                rc = self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(handle), error, 256)
                if rc:
                    return error.value
                handles.append(handle.value)
                if self.lib.botw_services_close(self.ctx, handle.value, error, 256):
                    return error.value
            return handles
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(8)))
        self.assertTrue(all(isinstance(result, list) for result in results), results)
        handles = [handle for result in results for handle in result]
        self.assertEqual(len(set(handles)), 512)

    def test_ipc_identity_exhaustion_never_collides_with_kernel_namespace(self):
        self.lib.botw_services_destroy(self.ctx)
        self.ctx = self.lib.botw_services_create(LOG(), self.memory, None)
        allocated = 0
        while allocated <= 0x10000:
            handle = C.c_uint32()
            rc = self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(handle), self.error, 1024)
            if rc:
                break
            self.assertLess(handle.value, 0x20000)
            allocated += 1
            self.assertEqual(self.close(handle.value), 0)
        self.assertEqual(allocated, 0x10000)
        self.assertIn(b'identity space exhausted', self.error.value)

    def test_handle_table_exhaustion_is_bounded(self):
        handles = []
        while len(handles) < 1024:
            handle = C.c_uint32()
            rc = self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(handle), self.error, 1024)
            if rc:
                break
            handles.append(handle.value)
        self.assertLess(len(handles), 1024)
        self.assertGreater(len(handles), 8)
        for handle in handles:
            self.assertEqual(self.close(handle), 0)
        self.connect()


if __name__ == '__main__':
    unittest.main()
