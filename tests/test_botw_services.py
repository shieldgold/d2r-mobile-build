"""Hand-built Horizon IPC fixtures; no Nintendo binaries or headers needed."""
import ctypes as C
from concurrent.futures import ThreadPoolExecutor
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
        subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                        '-Werror', '-shared', '-fPIC', str(source), str(ROOT / 'native/botw_resources/romfs.c'), str(ROOT / 'native/botw_input/input.c'), '-o', str(cls.library)], check=True)
        cls.lib = C.CDLL(str(cls.library))
        cls.lib.botw_services_create.argtypes = [LOG, MEMORY, C.c_void_p]
        cls.lib.botw_services_create.restype = C.c_void_p
        cls.lib.botw_services_destroy.argtypes = [C.c_void_p]
        cls.lib.botw_services_connect.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t,
                                                C.POINTER(C.c_uint32), C.c_char_p, C.c_size_t]
        cls.lib.botw_services_close.argtypes = [C.c_void_p, C.c_uint32, C.c_char_p, C.c_size_t]
        cls.lib.botw_services_send.argtypes = [C.c_void_p, C.c_uint32, C.c_void_p, C.c_size_t,
                                             C.c_char_p, C.c_size_t]

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
    def request(command, payload=b'', kind=4, pid=False, domain=0, static=None, buffer=None, copies=(), receive=None):
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
                             words | (bool(pid or copies) << 31))
        message = header + special + descriptor + b'\0' * (-start % 16) + body
        return message.ljust(start + words * 4, b'\0')

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
