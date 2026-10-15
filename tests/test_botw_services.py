"""Hand-built Horizon IPC fixtures; no Nintendo binaries or headers needed."""
import ctypes as C
from concurrent.futures import ThreadPoolExecutor
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
                        '-Werror', '-shared', '-fPIC', str(source), '-o', str(cls.library)], check=True)
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
        _, error = self.send(sm, self.request(1, b'fsp-srv\0'), good=False)
        self.assertIn('fsp-srv', error)

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
        packet = C.create_string_buffer(struct.pack('<QQBBBBI', 1, 2, 7, 0, 1, 0, len(chunks)) + chunks)
        self.ranges.append((C.addressof(packet), len(packet), False))
        self.send(handles[0], self.request(0, static=(C.addressof(packet), len(packet) - 1)))
        self.assertTrue(any(text.decode() in line for line in self.logs), self.logs)

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
