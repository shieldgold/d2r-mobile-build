"""Hand-built Horizon IPC fixtures; no Nintendo binaries or headers needed."""
import ctypes as C
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p)
MEMORY = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint64, C.c_size_t, C.c_int)


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
    def request(command, payload=b'', kind=4, pid=False, domain=0, static=None, buffer=None):
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
        special = struct.pack('<IQ', 1, 0) if pid else b''
        start = 8 + len(special) + len(descriptor)
        body = b'SFCI' + struct.pack('<III', 0, command, 0) + payload
        if domain:
            body = struct.pack('<BBHIII', 1, 0, len(body), domain, 0, 0) + body
        words = (16 + len(body) + 3) // 4
        header = struct.pack('<II', kind | bool(static) << 16 | bool(buffer) << 20,
                             words | (bool(pid) << 31))
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

    def test_hipc_close_releases_session(self):
        sm = self.connect()
        self.send(sm, struct.pack('<II', 2, 0))
        self.assertEqual(self.close(sm), -1)

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
