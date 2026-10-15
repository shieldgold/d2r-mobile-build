"""Stateful HID vibration registration; no actuator or game assets are faked."""
import ctypes as C
import struct
import unittest

import test_botw_services as baseline
import test_botw_pointer_buffer as pointers


IDS = (*range(8), 0x10, 0x20)
STANDARD_HANDLES = tuple(style | player << 8 | device << 16
                         for player in IDS
                         for style, devices in ((3, (0, 1)), (4, (0, 1)),
                                                (5, (0, 1)), (6, (0,)), (7, (1,)))
                         for device in devices)


class VibrationServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline.ServicesTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        baseline.ServicesTests.tearDownClass.__func__(cls)

    setUp = baseline.ServicesTests.setUp
    tearDown = baseline.ServicesTests.tearDown
    connect = baseline.ServicesTests.connect
    close = baseline.ServicesTests.close
    send = baseline.ServicesTests.send
    response = baseline.ServicesTests.response
    request = staticmethod(baseline.ServicesTests.request)
    initialized_sm = baseline.ServicesTests.initialized_sm
    service = baseline.ServicesTests.service

    def hid(self, domain=False):
        handle = self.service(self.initialized_sm(), b'hid')
        if not domain:
            return handle, 0
        raw, _ = self.send(handle, self.request(0, kind=5))
        return handle, struct.unpack_from('<I', self.response(raw)[0])[0]

    def active_list(self, hid, root=0):
        wire = bytearray(self.request(203, domain=root))
        if not root:
            wire[32:40] = b'\xa5' * 8  # Original non-PID SDK framing tail.
        raw, _ = self.send(hid, bytes(wire))
        data, handles, count = self.response(raw, bool(root))
        if root:
            self.assertEqual((handles, count), ([], 1))
            obj, = struct.unpack_from('<I', data)
            self.assertTrue(obj)
            return hid, obj
        self.assertEqual((len(handles), count), (1, 0))
        self.assertEqual(data, bytes(len(data)))
        return handles[0], 0

    def registered(self, handle, context=None):
        query = getattr(self.lib, 'botw_services_vibration_device_active', None)
        self.assertIsNotNone(query, 'missing host query for actual live-list registration')
        query.argtypes, query.restype = [C.c_void_p, C.c_uint32], C.c_int
        return query(self.ctx if context is None else context, handle)

    def activate(self, target, handle=3, *, tail=True):
        transport, obj = target
        wire = bytearray(self.request(0, struct.pack('<I', handle), domain=obj))
        if not obj:
            if tail:
                wire[36:44] = b'\xa5' * 8
            else:
                struct.pack_into('<I', wire, 4, 7)  # Four semantic bytes only.
                del wire[36:]
        raw, _ = self.send(transport, bytes(wire))
        data, handles, count = self.response(raw, bool(obj))
        self.assertEqual((handles, count), ([], 0))
        self.assertEqual(data, bytes(len(data)))

    def reject(self, handle, wire):
        raw, error = self.send(handle, wire, good=False)
        self.assertEqual(raw, wire.ljust(256, b'\0'))
        return error

    def domain_close(self, transport, obj, good=True):
        wire = struct.pack('<II', 4, 8) + bytes(8) + struct.pack('<BBHIII', 2, 0, 0, obj, 0, 0)
        raw, error = self.send(transport, wire, good=good)
        if not good:
            self.assertEqual(raw, wire.ljust(256, b'\0'))
        return raw, error

    def test_original_session_203_and_child_zero_register_without_pid_or_aruid(self):
        hid, root = self.hid()
        target = self.active_list(hid)
        self.assertEqual(self.registered(3), 0)
        self.activate(target)
        self.assertEqual(self.registered(3), 1)
        self.activate(target, tail=False)
        self.assertEqual(self.registered(3), 1)
        self.assertTrue(any('haptics_backend=unavailable' in message for message in self.logs))

    def test_domain_returns_one_object_and_child_no_inline_output(self):
        hid, root = self.hid(True)
        target = self.active_list(hid, root)
        self.assertNotEqual(target[1], root)
        self.activate(target, 0x10003)
        self.assertEqual(self.registered(0x10003), 1)
        self.domain_close(*target)
        self.assertEqual(self.registered(0x10003), 0)
        self.reject(hid, self.request(0, struct.pack('<I', 3), domain=target[1]))

    def test_all_eighty_standard_handles_persist_and_duplicates_are_idempotent(self):
        hid, root = self.hid()
        target = self.active_list(hid)
        self.assertEqual(len(STANDARD_HANDLES), 80)
        for handle in STANDARD_HANDLES:
            self.assertEqual(self.registered(handle), 0)
            self.activate(target, handle)
        for handle in STANDARD_HANDLES:
            self.assertEqual(self.registered(handle), 1)
            self.activate(target, handle)
        self.assertEqual(self.close(target[0]), 0)
        self.assertTrue(all(not self.registered(handle) for handle in STANDARD_HANDLES))

    def test_invalid_and_out_of_profile_handles_never_register_or_modify_reply(self):
        hid, root = self.hid()
        target = self.active_list(hid)
        bad = (0, 1, 2, 8, 0xd, 0x20, 0x21, 0x903, 0x803, 0x2103,
               0x01000003, 0x20003, 0x10006, 7, 0xffffffff)
        for handle in bad:
            with self.subTest(handle=hex(handle)):
                self.reject(target[0], self.request(0, struct.pack('<I', handle)))
                self.assertEqual(self.registered(handle), 0)
        self.activate(target)
        self.assertEqual(self.registered(3), 1)

    def test_203_rejects_pid_payload_handles_and_even_empty_descriptors(self):
        hid, root = self.hid(True)
        for options in ({'pid': True}, {'payload': bytes(4)}, {'copies': (0xffff8001,)},
                        {'static': (0, 0)}, {'buffer': (0, 0)}, {'receive': (0, 0)},
                        {'receive_static': (0, 0)}):
            with self.subTest(options=options):
                self.reject(hid, self.request(203, domain=root, **options))
        # Session 0 or 8 trailing alignment bytes, never arbitrary extra input.
        plain, _ = self.hid()
        for words in (5, 7, 9):
            wire = bytearray(self.request(203))
            struct.pack_into('<I', wire, 4, words)
            self.reject(plain, bytes(wire))
        self.active_list(hid, root)

    def test_child_rejects_foreign_shapes_and_unrelated_commands(self):
        hid, root = self.hid(True)
        target = self.active_list(hid, root)
        for options in ({'pid': True}, {'payload': bytes(8)}, {'copies': (0xffff8001,)},
                        {'static': (0, 0)}, {'buffer': (0, 0)}, {'receive': (0, 0)},
                        {'receive_static': (0, 0)}):
            args = {'payload': struct.pack('<I', 3), **options}
            self.reject(hid, self.request(0, domain=target[1], **args))
        for command in (1, 66, 67, 200, 201, 203, 0xffffffff):
            self.reject(hid, self.request(command, struct.pack('<I', 3), domain=target[1]))
        self.assertEqual(self.registered(3), 0)
        self.activate(target)

    def test_independent_lists_keep_their_own_registration_and_reference_lifetimes(self):
        hid, root = self.hid()
        first, second = self.active_list(hid), self.active_list(hid)
        self.activate(first, 3)
        self.activate(second, 0x10003)
        self.activate(second, 3)
        self.assertEqual(self.close(first[0]), 0)
        self.assertEqual((self.registered(3), self.registered(0x10003)), (1, 1))
        self.assertEqual(self.close(second[0]), 0)
        self.assertEqual((self.registered(3), self.registered(0x10003)), (0, 0))

    def test_clone_and_hipc_close_release_only_their_own_reference(self):
        hid, root = self.hid()
        target = self.active_list(hid)
        self.activate(target)
        raw, _ = self.send(target[0], self.request(2, kind=5))
        clone = self.response(raw)[1][0]
        self.send(target[0], struct.pack('<II', 2, 0))
        self.assertEqual(self.registered(3), 1)
        self.assertEqual(self.close(target[0]), 0)
        self.activate((clone, 0), 0x10003)
        self.send(clone, struct.pack('<II', 2, 0))
        self.assertEqual((self.registered(3), self.registered(0x10003)), (0, 0))
        self.assertEqual(self.close(clone), 0)

    def test_domain_copy_retains_registration_after_domain_object_and_session_close(self):
        hid, root = self.hid(True)
        target = self.active_list(hid, root)
        self.activate(target)
        raw, _ = self.send(hid, self.request(1, struct.pack('<I', target[1]), kind=5))
        copied = self.response(raw)[1][0]
        self.domain_close(*target)
        self.assertEqual(self.registered(3), 1)
        self.assertEqual(self.close(hid), 0)
        self.activate((copied, 0), 0x10003)
        self.assertEqual(self.close(copied), 0)
        self.assertEqual((self.registered(3), self.registered(0x10003)), (0, 0))

    def test_handle_and_domain_exhaustion_cannot_leave_hidden_activation(self):
        hid, root = self.hid(True)
        targets = [self.active_list(hid, root) for _ in range(63)]
        self.reject(hid, self.request(203, domain=root))
        self.activate(targets[-1])
        for target in targets:
            self.domain_close(*target)
        self.assertEqual(self.registered(3), 0)
        fresh = self.active_list(hid, root)
        self.activate(fresh, 0x10003)
        self.assertEqual((self.registered(3), self.registered(0x10003)), (0, 1))
        self.assertEqual(self.close(hid), 0)
        self.assertEqual(self.registered(0x10003), 0)
        plain, _ = self.hid()
        live = []
        while True:
            wire = self.request(203)
            ipc = C.create_string_buffer(wire.ljust(256, b'\0'))
            self.ranges.append((C.addressof(ipc), len(ipc), True))
            result = self.lib.botw_services_send(self.ctx, plain, ipc, 256, self.error, 1024)
            if result:
                self.assertIn(b'table exhausted', self.error.value)
                self.assertEqual(bytes(ipc)[:256], wire.ljust(256, b'\0'))
                break
            live.append(self.response(bytes(ipc))[1][0])
            self.assertLess(len(live), 256)
        self.assertGreater(len(live), 240)
        self.activate((live[-1], 0))
        for handle in live:
            self.assertEqual(self.close(handle), 0)
        self.assertEqual(self.registered(3), 0)

    def test_protected_reply_refusal_precedes_create_activate_and_domain_close(self):
        hid, root = self.hid(True)
        target = self.active_list(hid, root)
        denied, writes = [True], []

        @pointers.READ
        def read(opaque, source, destination, length, error, capacity):
            C.memmove(destination, source, length)
            return 0

        @pointers.WRITE
        def write(opaque, destination, source, length, error, capacity):
            writes.append(length)
            C.memmove(destination, source, length)
            return 0

        @pointers.PREPARE
        def prepare(opaque, destination, length, error, capacity):
            return -1 if denied[0] else 0

        self.lib.botw_services_set_memory_copiers.argtypes = [C.c_void_p, pointers.READ, pointers.WRITE, C.c_void_p]
        self.lib.botw_services_set_memory_write_preparer.argtypes = [C.c_void_p, pointers.PREPARE, C.c_void_p]
        self.copy_callbacks = read, write, prepare
        self.lib.botw_services_set_memory_copiers(self.ctx, read, write, None)
        self.lib.botw_services_set_memory_write_preparer(self.ctx, prepare, None)
        self.reject(hid, self.request(203, domain=root))
        self.reject(hid, self.request(0, struct.pack('<I', 3), domain=target[1]))
        self.assertEqual((writes, self.registered(3)), ([], 0))
        denied[0] = False
        self.activate(target)
        denied[0] = True
        self.domain_close(*target, good=False)
        self.assertEqual(self.registered(3), 1)
        denied[0] = False
        self.domain_close(*target)
        self.assertEqual(self.registered(3), 0)

    def test_registration_does_not_claim_actuation_or_relax_other_hid_aruid_gates(self):
        hid, root = self.hid()
        target = self.active_list(hid)
        self.activate(target)
        for command in (1, 66, 67, 100, 103, 200, 201, 202, 204, 205, 206):
            self.reject(hid, self.request(command, bytes(32)))
        self.assertEqual(self.registered(3), 1)
        self.assertEqual(self.lib.botw_services_vibration_device_active(None, 3), 0)


if __name__ == '__main__':
    unittest.main()
