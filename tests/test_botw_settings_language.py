"""Old SDK settings language catalogue through the actual bounded transport."""
import ctypes as C
import struct
import unittest

import test_botw_services as baseline
import test_botw_pointer_buffer as pointers


CODES = (b'ja', b'en-US', b'fr', b'de', b'it', b'es', b'zh-CN', b'ko',
         b'nl', b'pt', b'ru', b'zh-TW', b'en-GB', b'fr-CA', b'es-419')
CATALOGUE = b''.join(code.ljust(8, b'\0') for code in CODES)


class SettingsLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline.ServicesTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        baseline.ServicesTests.tearDownClass.__func__(cls)

    setUp = baseline.ServicesTests.setUp
    tearDown = baseline.ServicesTests.tearDown
    connect = baseline.ServicesTests.connect
    send = baseline.ServicesTests.send
    response = baseline.ServicesTests.response
    request = staticmethod(baseline.ServicesTests.request)
    initialized_sm = baseline.ServicesTests.initialized_sm
    service = baseline.ServicesTests.service
    static_buffer = baseline.ServicesTests.static_buffer

    def settings(self, domain=False):
        handle = self.service(self.initialized_sm(), b'set')
        if not domain:
            return handle, 0
        raw, _ = self.send(handle, self.request(0, kind=5))
        data, handles, objects = self.response(raw)
        self.assertEqual((handles, objects), ([], 0))
        return handle, struct.unpack_from('<I', data)[0]

    def output(self, slots):
        backing = self.static_buffer(b'\xa5' * (slots * 8 + 24))
        address = C.addressof(backing) + 8
        self.ranges.append((address, slots * 8, True))
        return backing, address

    def rejected(self, handle, wire):
        raw, error = self.send(handle, wire, good=False)
        self.assertEqual(raw, wire.ljust(256, b'\0'))
        return error

    def test_catalogue_returns_old_sdk_enum_order_and_exact_pointer_reply(self):
        for domain in (False, True):
            with self.subTest(domain=domain):
                handle, obj = self.settings(domain)
                backing, address = self.output(15)
                wire = pointers.request(1, domain=obj, cs=((address, 120),), pointer_sizes=(120,))
                raw, _ = self.send(handle, wire)
                data, handles, objects = self.response(raw, domain)
                self.assertEqual((struct.unpack_from('<i', data)[0], handles, objects), (15, [], 0))
                self.assertEqual(data[4:], bytes(len(data) - 4))
                self.assertEqual(bytes(backing), b'\xa5' * 8 + CATALOGUE + b'\xa5' * 16 + b'\0')
                descriptor, low = struct.unpack_from('<II', raw, 8)
                decoded = low | ((descriptor >> 12) & 15) << 32 | ((descriptor >> 6) & 63) << 36
                self.assertEqual(((struct.unpack_from('<I', raw)[0] >> 16) & 15,
                                  descriptor >> 16, decoded), (1, 120, address))

    def test_short_arrays_return_only_the_fitting_prefix(self):
        handle, obj = self.settings()
        for slots in (1, 3, 14):
            with self.subTest(slots=slots):
                backing, address = self.output(slots)
                raw, _ = self.send(handle, pointers.request(1, cs=((address, slots * 8),),
                                                            pointer_sizes=(slots * 8,)))
                data, _, _ = self.response(raw)
                self.assertEqual(struct.unpack_from('<i', data)[0], slots)
                self.assertEqual(bytes(backing), b'\xa5' * 8 + CATALOGUE[:slots * 8] +
                                 b'\xa5' * 16 + b'\0')

    def test_empty_array_returns_zero_without_publishing_a_pointer(self):
        for domain in (False, True):
            handle, obj = self.settings(domain)
            # Original proxy retains the client pointer even with a zero span.
            # No memory authority, dereference or X reply is needed in that case.
            for address in (0, 0x1234, 1 << 46):
                raw, _ = self.send(handle, pointers.request(1, domain=obj, cs=((address, 0),), pointer_sizes=(0,)))
                data, handles, objects = self.response(raw, domain)
                self.assertEqual((struct.unpack_from('<i', data)[0], handles, objects), (0, [], 0))
                self.assertEqual((struct.unpack_from('<I', raw)[0] >> 16) & 15, 0)

    def test_count_is_registry_size_without_device_or_asset_claim(self):
        for domain in (False, True):
            handle, obj = self.settings(domain)
            raw, _ = self.send(handle, self.request(3, domain=obj))
            data, handles, objects = self.response(raw, domain)
            self.assertEqual((struct.unpack_from('<i', data)[0], handles, objects), (15, [], 0))

    def test_foreign_catalogue_shapes_do_not_write_any_output(self):
        handle, obj = self.settings(True)
        backing, address = self.output(16)
        original = bytes(backing)
        shapes = [pointers.request(1, domain=obj, cs=((address, 120),), pointer_sizes=(119,)),
                  pointers.request(1, domain=obj, cs=((address, 119),), pointer_sizes=(119,)),
                  pointers.request(1, domain=obj, cs=((address, 128),), pointer_sizes=(128,)),
                  pointers.request(1, b'\0' * 4, domain=obj, cs=((address, 120),), pointer_sizes=(120,)),
                  pointers.request(1, pid=True, domain=obj, cs=((address, 120),), pointer_sizes=(120,)),
                  self.request(1, domain=obj, receive=(address, 120)),
                  self.request(1, domain=obj),
                  self.request(1, domain=obj, copies=(0xffff8001,))]
        for wire in shapes:
            with self.subTest(wire=wire.hex()):
                self.rejected(handle, wire)
                self.assertEqual(bytes(backing), original)

    def test_readonly_and_straddling_destinations_are_rejected(self):
        handle, obj = self.settings()
        backing = self.static_buffer(b'\xa5' * 144)
        address = C.addressof(backing)
        self.ranges.append((address, 120, False))
        self.rejected(handle, pointers.request(1, cs=((address, 120),), pointer_sizes=(120,)))
        self.ranges.append((address, 119, True))
        self.rejected(handle, pointers.request(1, cs=((address, 120),), pointer_sizes=(120,)))
        self.assertEqual(bytes(backing), b'\xa5' * 144 + b'\0')

    def test_count_rejects_payload_pid_handles_and_even_empty_descriptors(self):
        handle, obj = self.settings(True)
        for options in ({'payload': b'\0' * 4}, {'pid': True}, {'copies': (0xffff8001,)},
                        {'receive': (0, 0)}, {'static': (0, 0)}, {'receive_static': (0, 0)}):
            self.rejected(handle, self.request(3, domain=obj, **options))

    def test_original_variable_pointer_framing_padding_can_be_uninitialized(self):
        handle, obj = self.settings()
        backing, address = self.output(15)
        wire = bytearray(pointers.request(1, cs=((address, 120),), pointer_sizes=(120,)))
        wire[32:40] = b'\xa5' * 8
        wire[42:44] = b'\xa5' * 2
        raw, _ = self.send(handle, bytes(wire))
        self.assertEqual(struct.unpack_from('<i', self.response(raw)[0])[0], 15)
        self.assertEqual(bytes(backing)[8:128], CATALOGUE)

    def test_protected_copyback_prepares_entire_destination_before_any_write(self):
        handle, obj = self.settings()
        backing, address = self.output(15)
        prepares, writes = [], []

        @pointers.READ
        def read(opaque, source, destination, length, error, capacity):
            C.memmove(destination, source, length)
            return 0

        @pointers.WRITE
        def write(opaque, destination, source, length, error, capacity):
            writes.append((destination, length))
            C.memmove(destination, source, length)
            return 0

        @pointers.PREPARE
        def prepare(opaque, destination, length, error, capacity):
            prepares.append((destination, length))
            return -1 if destination == address else 0

        self.lib.botw_services_set_memory_copiers.argtypes = [C.c_void_p, pointers.READ, pointers.WRITE, C.c_void_p]
        self.lib.botw_services_set_memory_copiers(self.ctx, read, write, None)
        self.lib.botw_services_set_memory_write_preparer.argtypes = [C.c_void_p, pointers.PREPARE, C.c_void_p]
        self.lib.botw_services_set_memory_write_preparer(self.ctx, prepare, None)
        # Keep callbacks alive through context cleanup as well as this send.
        self.copy_callbacks = read, write, prepare
        self.rejected(handle, pointers.request(1, cs=((address, 120),), pointer_sizes=(120,)))
        self.assertIn((address, 120), prepares)
        self.assertEqual(writes, [])
        self.assertEqual(bytes(backing), b'\xa5' * 144 + b'\0')

    def test_original_count_proxy_keeps_unspecified_session_tail(self):
        handle, obj = self.settings()
        wire = bytearray(self.request(3))
        wire[32:40] = b'\xa5' * 8
        raw, _ = self.send(handle, bytes(wire))
        self.assertEqual(struct.unpack_from('<i', self.response(raw)[0])[0], 15)

    def test_device_language_region_and_newer_catalogue_commands_remain_explicit(self):
        handle, obj = self.settings()
        for command in (0, 2, 4, 5, 6):
            self.assertIn('unsupported', self.rejected(handle, self.request(command)))


if __name__ == '__main__':
    unittest.main()
