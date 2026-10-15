"""Real pread into protected guest output pages, with checked preparation."""
import ctypes as C
import mmap
import struct
import unittest

import test_botw_services as fixtures

PREPARE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint64, C.c_size_t, C.c_void_p, C.c_size_t)


class ServiceWritePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ServicesTests.setUpClass()
        cls.lib = fixtures.ServicesTests.lib

    @classmethod
    def tearDownClass(cls):
        fixtures.ServicesTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.ServicesTests()
        self.f.setUp()
        self.calls, self.reject = [], False
        self.page = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE)
        self.address = C.addressof(C.c_char.from_buffer(self.page))
        self.page[:] = b'\xa5' * len(self.page)
        self.f.ranges.append((self.address, len(self.page), True))
        self.libc = C.CDLL(None, use_errno=True)
        self.libc.mprotect.argtypes = [C.c_void_p, C.c_size_t, C.c_int]
        self.libc.mprotect.restype = C.c_int
        self.addCleanup(self.close_page)
        self.assertTrue(hasattr(self.lib, 'botw_services_set_memory_write_preparer'),
                        'services needs a separate checked host-write preparer')
        self.lib.botw_services_set_memory_write_preparer.argtypes = [C.c_void_p, PREPARE, C.c_void_p]

        @PREPARE
        def prepare(opaque, address, length, error, capacity):
            self.calls.append((opaque, address, length))
            if self.reject:
                message = b'probe refused host I/O write\0'
                if error and capacity:
                    C.memmove(error, message, min(len(message), capacity))
                return -1
            return self.libc.mprotect(self.address, len(self.page), mmap.PROT_READ | mmap.PROT_WRITE)
        self.prepare = prepare
        self.lib.botw_services_set_memory_write_preparer(self.f.ctx, prepare, 0x1234)

    def close_page(self):
        self.libc.mprotect(self.address, len(self.page), mmap.PROT_READ | mmap.PROT_WRITE)
        self.page.close()

    def tearDown(self):
        self.f.tearDown()
        self.f.doCleanups()

    def protect(self):
        self.assertEqual(self.libc.mprotect(self.address, len(self.page), mmap.PROT_READ), 0)

    def storage(self):
        image, _ = fixtures.romfs_fixture()
        self.f.attach_romfs(image)
        raw, _ = self.f.send(self.f.filesystem(), self.f.request(200))
        storage = self.f.response(raw)[1][0]
        return storage, image

    def save_file(self):
        _, user, _ = self.f.configure_local_user()
        fs, root = self.f.save_mount(user)
        self.f.save_path(fs, root, 0, '/slot', struct.pack('<IIQ', 0, 0, 4))
        file, child = self.f.save_file(fs, root)
        self.f.save_write(file, child, b'DATA')
        return file, child

    def test_raw_romfs_pread_prepares_owned_readonly_page_before_real_io(self):
        storage, image = self.storage()
        self.protect()
        self.f.send(storage, self.f.request(0, struct.pack('<qQ', 0, 64), receive=(self.address, 64)))
        self.assertEqual(self.page[:64], image[:64])
        self.assertEqual(self.page[64:80], b'\xa5' * 16)
        self.assertEqual(self.calls, [(0x1234, self.address, 64)])

    def test_savefs_pread_prepares_owned_readonly_page_and_preserves_short_eof(self):
        file, child = self.save_file()
        self.protect()
        raw, _ = self.f.send(file, self.f.request(0, struct.pack('<IIQQ', 0, 0, 0, 16),
                            domain=child, receive=(self.address, 16)))
        self.assertEqual(struct.unpack_from('<Q', self.f.response(raw)[0])[0], 4)
        self.assertEqual(self.page[:16], b'DATA' + b'\xa5' * 12)
        self.assertEqual(self.calls, [(0x1234, self.address, 16)])

    def test_failed_preparation_keeps_romfs_and_savefs_destination_unchanged(self):
        storage, _ = self.storage()
        file, child = self.save_file()
        self.reject = True
        self.protect()
        for handle, request in [(storage, self.f.request(0, struct.pack('<qQ', 0, 4), receive=(self.address, 4))),
                                (file, self.f.request(0, struct.pack('<IIQQ', 0, 0, 0, 4),
                                                    domain=child, receive=(self.address, 4)))]:
            with self.subTest(handle=handle):
                _, error = self.f.send(handle, request, good=False)
                self.assertIn('probe refused host I/O write', error)
                self.assertEqual(self.page[:], b'\xa5' * len(self.page))
        self.assertEqual(self.calls, [(0x1234, self.address, 4)] * 2)

    def test_empty_read_and_invalid_entire_span_do_not_call_preparer(self):
        storage, _ = self.storage()
        file, child = self.save_file()
        self.f.send(storage, self.f.request(0, struct.pack('<qQ', 0, 0)))
        self.f.send(file, self.f.request(0, struct.pack('<IIQQ', 0, 0, 0, 0), domain=child))
        self.f.send(storage, self.f.request(0, struct.pack('<qQ', 0, 64), receive=(self.address, 32)), good=False)
        self.f.send(file, self.f.request(0, struct.pack('<IIQQ', 0, 0, 0, 4), domain=child,
                                         receive=(self.address + len(self.page) - 2, 4)), good=False)
        self.assertFalse(self.calls, 'permission validation is separate from actual write preparation')

    def test_romfs_file_prepares_only_bounded_actual_eof_span(self):
        image, _ = fixtures.romfs_fixture()
        self.f.attach_romfs(image)
        raw, _ = self.f.send(self.f.filesystem(), self.f.request(2))
        fs = self.f.response(raw)[1][0]
        path = self.f.static_buffer(b'/System/Actor.byml')
        self.f.ranges.append((C.addressof(path), len(path), False))
        raw, _ = self.f.send(fs, self.f.request(8, struct.pack('<I', 1),
                                              static=(C.addressof(path), len(path))))
        file = self.f.response(raw)[1][0]
        self.protect()
        raw, _ = self.f.send(file, self.f.request(0, struct.pack('<IIqQ', 0, 0, 2, 16),
                                                receive=(self.address, 16)))
        self.assertEqual(struct.unpack_from('<Q', self.f.response(raw)[0])[0], 8)
        self.assertEqual(self.page[8:16], b'\xa5' * 8)
        self.assertEqual(self.calls, [(0x1234, self.address, 8)])

    def test_chunked_romfs_read_prepares_the_whole_span_before_first_chunk(self):
        payload = bytes(range(256)) * (17 * 1024 * 1024 // 256)
        image, _ = fixtures.romfs_fixture([('/Bootup.pack', payload)])
        self.f.attach_romfs(image)
        raw, _ = self.f.send(self.f.filesystem(), self.f.request(200))
        storage = self.f.response(raw)[1][0]
        page = mmap.mmap(-1, len(payload), prot=mmap.PROT_READ | mmap.PROT_WRITE)
        address = C.addressof(C.c_char.from_buffer(page))
        self.f.ranges.append((address, len(page), True))
        def close():
            self.libc.mprotect(address, len(page), mmap.PROT_READ | mmap.PROT_WRITE)
            page.close()
        self.addCleanup(close)
        calls = []
        @PREPARE
        def prepare(opaque, target, size, error, capacity):
            del opaque, error, capacity
            calls.append((target, size))
            return self.libc.mprotect(address, len(page), mmap.PROT_READ | mmap.PROT_WRITE)
        self.lib.botw_services_set_memory_write_preparer(self.f.ctx, prepare, None)
        self.assertEqual(self.libc.mprotect(address, len(page), mmap.PROT_READ), 0)
        self.f.send(storage, self.f.request(0, struct.pack('<qQ', 0x200, len(payload)),
                                          receive=(address, len(payload))))
        self.assertEqual(page[:], payload)
        self.assertEqual(calls, [(address, len(payload))])
