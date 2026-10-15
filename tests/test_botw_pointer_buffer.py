"""Bounded real HIPC pointer transfers, without Nintendo code or resources."""
import ctypes as C
import mmap
import struct
import unittest

import test_botw_services as fixtures


READ = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint64, C.c_void_p,
                  C.c_size_t, C.c_void_p, C.c_size_t)
WRITE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint64, C.c_void_p,
                   C.c_size_t, C.c_void_p, C.c_size_t)
PREPARE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint64, C.c_size_t,
                     C.c_void_p, C.c_size_t)


def request(command, payload=b'', *, kind=4, pid=False, domain=0,
            xs=(), sends=(), receives=(), cs=(), pointer_sizes=(), token=0):
    """Public HIPC/CMIF layout, including the variable output size table.

    Domain data_size describes only CMIF and semantic input. The u16 table
    follows the full raw-data padding, outside that domain data_size.
    """
    special = struct.pack('<IQ', 1, 0) if pid else b''
    descriptors = b''
    for address, size, index in xs:
        assert 0 <= address < 1 << 42 and 0 <= size < 1 << 16
        assert 0 <= index < 64
        descriptors += struct.pack('<II', size << 16 | index |
                                   ((address >> 36) & 63) << 6 |
                                   ((address >> 32) & 15) << 12,
                                   address & 0xffffffff)
    for address, size in (*sends, *receives):
        descriptors += struct.pack('<III', size & 0xffffffff,
                                   address & 0xffffffff,
                                   ((address >> 36) & 0x3fffff) << 2 |
                                   ((size >> 32) & 15) << 24 |
                                   ((address >> 32) & 15) << 28)
    cmif = b'SFCI' + struct.pack('<III', 0, command, token) + payload
    body = (struct.pack('<BBHIII', 1, 0, len(cmif), domain, 0, 0)
            if domain else b'') + cmif
    table_offset = (16 + len(body) + 1) & ~1
    raw_size = (table_offset + len(pointer_sizes) * 2 + 3) & ~3
    raw_start = 8 + len(special) + len(descriptors)
    data_start = (raw_start + 15) & ~15
    wire = bytearray(raw_start + raw_size + len(cs) * 8)
    struct.pack_into('<II', wire, 0,
                     kind | len(xs) << 16 | len(sends) << 20 | len(receives) << 24,
                     raw_size // 4 | bool(pid) << 31 |
                     ((2 + len(cs)) << 10 if cs else 0))
    wire[8:raw_start] = special + descriptors
    wire[data_start:data_start + len(body)] = body
    for i, size in enumerate(pointer_sizes):
        struct.pack_into('<H', wire, raw_start + table_offset + i * 2, size)
    for i, (address, size) in enumerate(cs):
        assert 0 <= address < 1 << 48 and 0 <= size < 1 << 16
        struct.pack_into('<II', wire, raw_start + raw_size + i * 8,
                         address & 0xffffffff, size << 16 | address >> 32)
    return bytes(wire)


class PointerBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Store the harness on this class, not at module scope: unittest must
        # collect only this suite, rather than every inherited service test.
        cls.harness = type('_PointerHarness', (fixtures.ServicesTests,), {})
        cls.harness.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.harness.tearDownClass()

    def setUp(self):
        self.f = self.harness('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)

    def checked_buffer(self, contents, writable=False):
        buffer = self.f.static_buffer(contents)
        self.f.ranges.append((C.addressof(buffer), len(buffer), writable))
        return buffer

    @staticmethod
    def log_packet(size, text=b''):
        chunks = bytes([2, len(text)]) + text if text else b''
        assert size >= 24 + len(chunks)
        return (struct.pack('<QQBBBBI', 1, 2, 7, 0, 1, 0, len(chunks)) +
                chunks).ljust(size, b'\xa5')

    def logger(self, domain=False):
        handle = self.f.service(self.f.initialized_sm())
        root = 0
        if domain:
            raw, _ = self.f.send(handle, request(0, kind=5))
            root = struct.unpack_from('<I', self.f.response(raw)[0])[0]
        raw, _ = self.f.send(handle, request(0, bytes(8), pid=True, domain=root))
        data, handles, objects = self.f.response(raw, domain)
        if domain:
            self.assertEqual((handles, objects), ([], 1))
            return handle, struct.unpack_from('<I', data)[0]
        self.assertEqual(len(handles), 1)
        return handles[0], 0

    def rejected_unchanged(self, handle, wire):
        raw, error = self.f.send(handle, wire, good=False)
        self.assertEqual(raw, wire.ljust(256, b'\0'))
        return error

    def copiers(self, read=None, write=None):
        self.assertTrue(hasattr(self.f.lib, 'botw_services_set_memory_copiers'),
                        'pointer staging needs checked guest copy callbacks')

        @READ
        def reading(opaque, source, destination, length, error, capacity):
            if read is not None:
                return read(source, destination, length, error, capacity)
            C.memmove(destination, source, length)
            return 0

        @WRITE
        def writing(opaque, destination, source, length, error, capacity):
            if write is not None:
                return write(destination, source, length, error, capacity)
            C.memmove(destination, source, length)
            return 0

        # Keep C callback objects alive until services has been destroyed.
        self.copy_callbacks = reading, writing
        self.f.lib.botw_services_set_memory_copiers.argtypes = [
            C.c_void_p, READ, WRITE, C.c_void_p]
        self.f.lib.botw_services_set_memory_copiers(self.f.ctx, reading, writing, None)

    def assert_reply_pointer(self, raw, address, size, domain=False, token=0):
        word0, word1 = struct.unpack_from('<II', raw)
        self.assertEqual((word0 >> 16) & 15, 1, 'one output X descriptor required')
        self.assertEqual(word1 >> 31, 0)
        descriptor, low = struct.unpack_from('<II', raw, 8)
        decoded = low | ((descriptor >> 12) & 15) << 32 | ((descriptor >> 6) & 63) << 36
        self.assertEqual((descriptor & 63, decoded, descriptor >> 16), (0, address, size))
        raw_start = 16  # header plus the single response X descriptor
        raw_end = raw_start + (word1 & 1023) * 4
        cmif = (raw_start + 15) & ~15
        if domain:
            self.assertEqual(struct.unpack_from('<I', raw, cmif)[0], 0)
            cmif += 16
        self.assertLessEqual(cmif + 16, raw_end)
        self.assertLessEqual(raw_end, len(raw))
        self.assertEqual(struct.unpack_from('<4sIII', raw, cmif), (b'SFCO', 0, 0, token))
        # This method has no scalar output count; all bytes after its CMIF
        # header belong to permitted framing padding and must not leak scratch.
        self.assertFalse(any(raw[cmif + 16:raw_end]))

    def test_control_query_reports_actual_1280_byte_capacity(self):
        handle = self.f.connect()
        raw, _ = self.f.send(handle, self.f.request(3, kind=5))
        data, handles, objects = self.f.response(raw)
        self.assertEqual((handles, objects), ([], 0))
        self.assertEqual(struct.unpack_from('<H', data)[0], 0x500)

    def test_x_capacity_boundary_and_large_alias_are_distinct(self):
        logger, _ = self.logger()
        packet = self.checked_buffer(self.log_packet(0x501, b'capacity-boundary'))
        address = C.addressof(packet)
        self.f.send(logger, request(0, xs=((address, 0x500, 0),)))
        before = sum('capacity-boundary' in line for line in self.f.logs)
        self.rejected_unchanged(logger, request(0, xs=((address, 0x501, 0),)))
        self.assertEqual(sum('capacity-boundary' in line for line in self.f.logs), before)
        # Map aliases do not consume the advertised pointer-transfer arena.
        self.f.send(logger, request(0, sends=((address, 0x501),)))
        self.assertEqual(sum('capacity-boundary' in line for line in self.f.logs), before + 1)

    def test_aggregate_budget_checks_alignment_before_service_dispatch(self):
        logger, _ = self.logger()
        incoming = self.checked_buffer(self.log_packet(800, b'aggregate-reject'))
        outgoing = self.checked_buffer(b'\x6d' * 1300, True)
        for in_size, out_size in ((800, 800), (33, 1247)):
            with self.subTest(in_size=in_size, out_size=out_size):
                wire = request(0, xs=((C.addressof(incoming), in_size, 0),),
                               cs=((C.addressof(outgoing), out_size),),
                               pointer_sizes=(out_size,))
                error = self.rejected_unchanged(logger, wire)
                self.assertRegex(error.lower(), r'(pointer|pool).*(budget|capacity|exceed|small|overlap)')
                self.assertEqual(bytes(outgoing)[:1300], b'\x6d' * 1300)
        self.assertFalse(any('aggregate-reject' in line for line in self.f.logs))

    def test_input_index_is_preserved_without_selecting_an_input_scratch_slot(self):
        logger, _ = self.logger()
        packet = self.checked_buffer(self.log_packet(64, b'index-thirty-seven'))
        self.f.send(logger, request(0, xs=((C.addressof(packet), 64, 37),)))
        self.assertTrue(any('index-thirty-seven' in line for line in self.f.logs))

    def test_real_variable_c_size_table_and_reply_x_in_plain_and_domain_sessions(self):
        _, user, _ = self.f.configure_local_user()
        for domain in (False, True):
            with self.subTest(domain=domain):
                account, root = self.f.account(domain)
                output = self.checked_buffer(b'\xa5' * 144, True)
                address = C.addressof(output)
                wire = request(2, domain=root, cs=((address, 128),),
                               pointer_sizes=(128,), token=0x13572468)
                # The original serializer does not initialize framing padding.
                # Its variable u16 table follows that padding, not the CMIF
                # semantic bytes or the domain data_size boundary.
                wire = bytearray(wire)
                semantic_end = 32 + (16 if domain else 0)
                table = 40 + (16 if domain else 0)
                wire[semantic_end:table] = b'\xa5' * (table - semantic_end)
                wire[table + 2:table + 4] = b'\xa5\xa5'
                wire = bytes(wire)
                raw, _ = self.f.send(account, wire)
                self.assertEqual(bytes(output)[:128], bytes(user.uid) + bytes(112))
                self.assertEqual(bytes(output)[128:144], b'\xa5' * 16)
                self.assert_reply_pointer(raw, address, 128, domain, 0x13572468)

    def test_missing_size_table_capacity_mismatch_and_extra_c_leave_outputs_unchanged(self):
        self.f.configure_local_user()
        for domain in (False, True):
            account, root = self.f.account(domain)
            output = self.checked_buffer(b'\xa5' * 160, True)
            address = C.addressof(output)
            wires = [request(2, domain=root, cs=((address, 128),)),
                     request(2, domain=root, cs=((address, 112),), pointer_sizes=(128,)),
                     request(2, domain=root, cs=((address, 128),), pointer_sizes=(144,)),
                     request(2, domain=root, cs=((address, 128), (address + 128, 16)),
                             pointer_sizes=(128, 16))]
            for i, wire in enumerate(wires):
                with self.subTest(domain=domain, malformed=i):
                    C.memset(address, 0xa5, 160)
                    self.rejected_unchanged(account, wire)
                    self.assertEqual(bytes(output)[:160], b'\xa5' * 160)

    def test_checked_x_copy_creates_a_snapshot_before_handler_reads(self):
        logger, _ = self.logger()
        packet = self.checked_buffer(self.log_packet(64, b'original-x-snapshot'))
        address, copies = C.addressof(packet), []

        def read(source, destination, length, error, capacity):
            C.memmove(destination, source, length)
            if source == address:
                copies.append((destination, length))
                C.memset(source, 0, length)
            return 0

        self.copiers(read=read)
        self.f.send(logger, request(0, xs=((address, 64, 0),)))
        self.assertEqual(len(copies), 1)
        self.assertNotEqual(copies[0][0], address)
        self.assertEqual((copies[0][0] & 15, copies[0][1]), (0, 64))
        self.assertTrue(any('original-x-snapshot' in line for line in self.f.logs))

    def test_checked_input_copy_failure_cannot_dispatch_or_change_ipc(self):
        logger, _ = self.logger()
        packet = self.checked_buffer(self.log_packet(64, b'copy-must-not-dispatch'))
        address, attempts = C.addressof(packet), []

        def read(source, destination, length, error, capacity):
            if source == address:
                attempts.append(length)
                return -1
            C.memmove(destination, source, length)
            return 0

        self.copiers(read=read)
        self.rejected_unchanged(logger, request(0, xs=((address, 64, 0),)))
        self.assertEqual(attempts, [64])
        self.assertFalse(any('copy-must-not-dispatch' in line for line in self.f.logs))

    def test_checked_output_copy_failure_cannot_publish_reply_or_partial_data(self):
        self.f.configure_local_user()
        account, root = self.f.account(True)
        output = self.checked_buffer(b'\xa5' * 144, True)
        address, attempts = C.addressof(output), []

        def write(destination, source, length, error, capacity):
            if destination == address:
                attempts.append((source, length))
                return -1
            C.memmove(destination, source, length)
            return 0

        self.copiers(write=write)
        self.rejected_unchanged(account, request(2, domain=root,
                                cs=((address, 128),), pointer_sizes=(128,)))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][1], 128)
        self.assertNotEqual(attempts[0][0], address)
        self.assertEqual(bytes(output)[:144], b'\xa5' * 144)

    def test_reply_preparation_failure_prevents_logger_side_effects(self):
        logger, _ = self.logger()
        packet = self.checked_buffer(self.log_packet(64, b'no-ipc-no-dispatch'))
        calls = []
        self.copiers()

        @PREPARE
        def prepare(opaque, address, length, error, capacity):
            calls.append((address, length))
            if length == 256:
                return -1
            return 0

        self.prepare_callback = prepare
        self.f.lib.botw_services_set_memory_write_preparer.argtypes = [
            C.c_void_p, PREPARE, C.c_void_p]
        self.f.lib.botw_services_set_memory_write_preparer(self.f.ctx, prepare, None)
        self.rejected_unchanged(logger, request(0, xs=((C.addressof(packet), 64, 0),)))
        self.assertEqual(calls, [(self.f.ranges[-1][0], 256)])
        self.assertFalse(any('no-ipc-no-dispatch' in line for line in self.f.logs))

    def test_domain_close_preparation_failure_keeps_the_object_alive(self):
        handle, object_id = self.logger(True)
        packet = self.checked_buffer(self.log_packet(64, b'domain-close-retained'))
        blocked, calls = [True], []
        self.copiers()

        @PREPARE
        def prepare(opaque, address, length, error, capacity):
            calls.append((address, length))
            return -1 if blocked[0] else 0

        self.prepare_callback = prepare
        self.f.lib.botw_services_set_memory_write_preparer.argtypes = [
            C.c_void_p, PREPARE, C.c_void_p]
        self.f.lib.botw_services_set_memory_write_preparer(self.f.ctx, prepare, None)
        close = (struct.pack('<II', 4, 8) + bytes(8) +
                 struct.pack('<BBHIII', 2, 0, 0, object_id, 0, 0))
        self.rejected_unchanged(handle, close)
        self.assertEqual(calls, [(self.f.ranges[-1][0], 256)])
        blocked[0] = False
        self.f.send(handle, request(0, domain=object_id,
                                   xs=((C.addressof(packet), 64, 0),)))
        self.assertTrue(any('domain-close-retained' in line for line in self.f.logs))

    def test_logical_permission_failure_precedes_pointer_copy_callbacks(self):
        logger, _ = self.logger()
        self.f.configure_local_user()
        account, root = self.f.account(True)
        unchecked = self.f.static_buffer(self.log_packet(64, b'unchecked-source'))
        readonly = self.checked_buffer(b'\xa5' * 144, False)
        reads, writes = [], []

        def read(source, destination, length, error, capacity):
            reads.append(source)
            C.memmove(destination, source, length)
            return 0

        def write(destination, source, length, error, capacity):
            writes.append(destination)
            C.memmove(destination, source, length)
            return 0

        self.copiers(read=read, write=write)
        self.rejected_unchanged(logger, request(0, xs=((C.addressof(unchecked), 64, 0),)))
        self.assertNotIn(C.addressof(unchecked), reads)
        self.rejected_unchanged(account, request(2, domain=root,
                                cs=((C.addressof(readonly), 128),), pointer_sizes=(128,)))
        self.assertEqual(writes, [])
        self.assertEqual(bytes(readonly)[:144], b'\xa5' * 144)

    def test_small_ipc_capacity_still_has_a_complete_dynamic_pointer_reply(self):
        _, user, _ = self.f.configure_local_user()
        for domain, capacity in ((False, 64), (True, 80)):
            account, root = self.f.account(domain)
            output = self.checked_buffer(b'\xa5' * 144, True)
            address = C.addressof(output)
            wire = request(2, domain=root, cs=((address, 128),), pointer_sizes=(128,))
            self.assertLessEqual(len(wire), capacity)
            raw, _ = self.f.send(account, wire, capacity=capacity)
            self.assertEqual(len(raw), capacity)
            self.assertEqual(bytes(output)[:128], bytes(user.uid) + bytes(112))
            self.assertEqual(bytes(output)[128:144], b'\xa5' * 16)
            self.assert_reply_pointer(raw, address, 128, domain)

    def test_c_address_outside_reply_x_width_is_rejected_without_truncation(self):
        self.f.configure_local_user()
        account, root = self.f.account(True)
        libc = C.CDLL(None, use_errno=True)
        libc.mmap.argtypes = [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_longlong]
        libc.mmap.restype = C.c_void_p
        libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
        # A non-fixed hint never replaces an existing mapping. C descriptors
        # encode 48 address bits, but a successful reply X can encode only 42.
        address = libc.mmap(1 << 43, mmap.PAGESIZE, mmap.PROT_READ | mmap.PROT_WRITE,
                            mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS, -1, 0)
        if address == C.c_void_p(-1).value:
            self.skipTest('host cannot allocate a high-address C descriptor fixture')
        self.f.addCleanup(libc.munmap, address, mmap.PAGESIZE)
        if not (1 << 42 <= address < (1 << 48) - mmap.PAGESIZE):
            self.skipTest('host did not honor a usable high-address mapping hint')
        C.memset(address, 0xa5, 144)
        self.f.ranges.append((address, mmap.PAGESIZE, True))
        self.rejected_unchanged(account, request(2, domain=root,
                                cs=((address, 128),), pointer_sizes=(128,)))
        self.assertEqual(C.string_at(address, 144), b'\xa5' * 144)

    def test_clone_and_domain_copy_own_arenas_until_their_transport_closes(self):
        logger, _ = self.logger()
        handles = [logger]
        for command, payload in ((2, b''), (4, bytes(4))):
            raw, _ = self.f.send(logger, request(command, payload, kind=5))
            handles.append(self.f.response(raw)[1][0])
        domain_handle, object_id = self.logger(True)
        raw, _ = self.f.send(domain_handle, request(1, struct.pack('<I', object_id), kind=5))
        copy = self.f.response(raw)[1][0]
        packet = self.checked_buffer(self.log_packet(64, b'arena-lifetime'))
        address, observed = C.addressof(packet), []

        def read(source, destination, length, error, capacity):
            C.memmove(destination, source, length)
            if source == address:
                observed.append(destination)
            return 0

        self.copiers(read=read)
        transports = [(h, 0) for h in handles] + [(domain_handle, object_id), (copy, 0)]
        arenas = []
        for handle, domain in transports:
            raw, _ = self.f.send(handle, request(3, kind=5))
            self.assertEqual(struct.unpack_from('<H', self.f.response(raw)[0])[0], 0x500)
            for _ in range(2):
                self.f.send(handle, request(0, domain=domain, xs=((address, 64, 0),)))
            self.assertEqual(observed[-1], observed[-2])
            self.assertEqual(observed[-1] & 15, 0)
            arenas.append(observed[-1])
        self.assertEqual(len(set(arenas)), len(transports))
        self.assertEqual(self.f.close(logger), 0)
        self.assertEqual(self.f.close(logger), -1)
        for handle in handles[1:] + [copy]:
            self.f.send(handle, request(0, xs=((address, 64, 0),)))
        self.f.send(handles[1], struct.pack('<II', 2, 0))
        self.rejected_unchanged(handles[1], request(3, kind=5))
        self.assertEqual(self.f.close(handles[1]), 0)
        self.assertEqual(self.f.close(handles[1]), -1)


if __name__ == '__main__':
    unittest.main()
