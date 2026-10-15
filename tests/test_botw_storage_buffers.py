"""Storage's real backing and old SDK auto-select X/A and B/C transport."""
import ctypes as C
import struct
import unittest

import test_botw_services as baseline
import test_botw_pointer_buffer as pointers


def storage_request(command, offset, address, size, mode, domain=0):
    """Public wire layout; no Nintendo code. Keep inactive auto-select slots.

    The old writer leaves alignment bytes and the inactive C size slot alone.
    Those bytes deliberately contain A5, while semantic input is one s64.
    """
    options = {'domain': domain}
    if command == 10:
        options.update(xs=((address, size, 0),) if mode == 'X' else ((0, 0, 0),),
                       sends=((address, size),) if mode == 'A' else ((0, 0),))
        raw_start = 28
    else:
        options.update(receives=((address, size),) if mode == 'B' else ((0, 0),),
                       cs=((address, size),) if mode == 'C' else ((0, 0),),
                       pointer_sizes=(size if mode == 'C' else 0,))
        raw_start = 20
    wire = bytearray(pointers.request(command, struct.pack('<Q', offset & ((1 << 64) - 1)), **options))
    raw_end = raw_start + (struct.unpack_from('<I', wire, 4)[0] & 1023) * 4
    semantic_end = ((raw_start + 15) & ~15) + (16 if domain else 0) + 24
    if command == 10:
        wire[semantic_end:raw_end] = b'\xa5' * (raw_end - semantic_end)
    else:
        table = raw_start + 40 + (16 if domain else 0)
        wire[semantic_end:table] = b'\xa5' * (table - semantic_end)
        wire[table + 2:raw_end] = b'\xa5' * (raw_end - table - 2)
        if mode == 'B':
            wire[table:table + 2] = b'\xa5\xa5'
    return bytes(wire)


class StorageBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = type('_StorageHarness', (baseline.ServicesTests,), {})
        cls.harness.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.harness.tearDownClass()

    def setUp(self):
        self.f = self.harness('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.f.applet_kernel(); self.f.applet_state()
        self.callbacks = []

    def buffer(self, data, writable=False):
        backing = self.f.static_buffer(data)
        self.f.ranges.append((C.addressof(backing), len(data), writable))
        return backing

    def storage(self, size, domain=False):
        creator = self.f.proxy_child(self.f.applet_proxy(), 11)
        root = 0
        if domain:
            raw, _ = self.f.send(creator, self.f.request(0, kind=5))
            root = struct.unpack_from('<I', self.f.response(raw)[0])[0]
        raw, _ = self.f.send(creator, self.f.request(10, struct.pack('<q', size), domain=root))
        data, handles, _ = self.f.response(raw, domain)
        storage = struct.unpack_from('<I', data)[0] if domain else handles[0]
        raw, _ = self.f.send(creator if domain else storage, self.f.request(0, domain=storage if domain else 0))
        data, handles, _ = self.f.response(raw, domain)
        accessor = struct.unpack_from('<I', data)[0] if domain else handles[0]
        return (creator, storage, accessor) if domain else (storage, 0, accessor)

    def send_rw(self, handle, obj, command, offset, address, size, mode):
        raw, _ = self.f.send(handle, storage_request(command, offset, address, size, mode, obj))
        data, handles, objects = self.f.response(raw, bool(obj))
        self.assertEqual((handles, objects), ([], 0))
        self.assertFalse(any(data), 'Storage Read/Write has no scalar actual-count output')
        return raw

    def rejected(self, handle, wire):
        raw, error = self.f.send(handle, wire, good=False)
        self.assertEqual(raw, wire.ljust(256, b'\0'))
        return error

    def copiers(self, read=None, write=None, prepare=None):
        @pointers.READ
        def reading(opaque, source, destination, length, error, capacity):
            if read is not None:
                return read(source, destination, length)
            C.memmove(destination, source, length); return 0

        @pointers.WRITE
        def writing(opaque, destination, source, length, error, capacity):
            if write is not None:
                return write(destination, source, length)
            C.memmove(destination, source, length); return 0

        @pointers.PREPARE
        def preparing(opaque, address, length, error, capacity):
            return prepare(address, length) if prepare is not None else 0

        self.callbacks += [reading, writing, preparing]
        self.f.lib.botw_services_set_memory_copiers.argtypes = [C.c_void_p, pointers.READ, pointers.WRITE, C.c_void_p]
        self.f.lib.botw_services_set_memory_copiers(self.f.ctx, reading, writing, None)
        self.f.lib.botw_services_set_memory_write_preparer.argtypes = [C.c_void_p, pointers.PREPARE, C.c_void_p]
        self.f.lib.botw_services_set_memory_write_preparer(self.f.ctx, preparing, None)

    def readback(self, handle, obj, size):
        output = self.buffer(b'\xa5' * size, True)
        self.send_rw(handle, obj, 11, 0, C.addressof(output), size, 'B')
        return bytes(output)[:size]

    def test_session_and_domain_pointer_and_alias_roundtrip_preserve_canaries(self):
        for domain in (False, True):
            parent, storage, accessor = self.storage(32, domain)
            handle, obj = (parent, accessor) if domain else (accessor, 0)
            for incoming, outgoing in (('X', 'C'), ('A', 'B')):
                with self.subTest(domain=domain, incoming=incoming):
                    source = self.buffer(b'precise storage!')
                    target = self.buffer(b'\xa5' * 32, True); address = C.addressof(target) + 8
                    self.send_rw(handle, obj, 10, 3, C.addressof(source), 16, incoming)
                    raw = self.send_rw(handle, obj, 11, 3, address, 16, outgoing)
                    self.assertEqual(bytes(target)[:32], b'\xa5' * 8 + b'precise storage!' + b'\xa5' * 8)
                    if outgoing == 'C':
                        descriptor, low = struct.unpack_from('<II', raw, 8)
                        decoded = low | ((descriptor >> 12) & 15) << 32 | ((descriptor >> 6) & 63) << 36
                        self.assertEqual((raw[2] & 15, descriptor >> 16, decoded), (1, 16, address))
                    else:
                        self.assertEqual(struct.unpack_from('<I', raw)[0], 0)

    def test_1280_pointer_boundary_and_larger_map_alias_use_distinct_budgets(self):
        _, _, handle = self.storage(0x600)
        for size in (1, 0x500, 0x501, 0x510):
            with self.subTest(size=size):
                source = self.buffer(bytes(i % 251 for i in range(size)))
                target = self.buffer(b'\xa5' * size, True)
                static = size <= 0x500
                self.send_rw(handle, 0, 10, 0, C.addressof(source), size, 'X' if static else 'A')
                self.send_rw(handle, 0, 11, 0, C.addressof(target), size, 'C' if static else 'B')
                self.assertEqual(bytes(target)[:size], bytes(source)[:size])
        for command, mode in ((10, 'X'), (11, 'C')):
            b = self.buffer(b'x' * 0x501, command == 11)
            self.assertIn('aggregate', self.rejected(handle, storage_request(command, 0, C.addressof(b), 0x501, mode)))

    def test_empty_active_pointer_keeps_nonnull_address_without_dereference(self):
        for domain in (False, True):
            parent, _, accessor = self.storage(8, domain)
            handle, obj = (parent, accessor) if domain else (accessor, 0)
            accesses = []
            self.copiers(read=lambda a, p, n: (accesses.append((a, n)), C.memmove(p, a, n), 0)[2],
                         write=lambda a, p, n: (accesses.append((a, n)), C.memmove(a, p, n), 0)[2],
                         prepare=lambda a, n: (accesses.append((a, n)), 0)[1])
            for command, mode in ((10, 'X'), (11, 'C')):
                for address in (0, 0x1234):
                    raw = self.send_rw(handle, obj, command, 8, address, 0, mode)
                    self.assertEqual(struct.unpack_from('<I', raw)[0], 0)
            self.assertTrue(all(n == 256 for _, n in accesses), 'zero spans must not enter memory copiers or prepare')
            self.rejected(handle, storage_request(10, 9, 0x1234, 0, 'X', obj))

    def test_negative_overflow_and_terminal_ranges_preserve_backing_and_destination(self):
        _, _, handle = self.storage(16)
        source = self.buffer(b'new contents!!!!')
        target = self.buffer(b'\xa5' * 16, True)
        for offset, size in ((-1, 1), ((1 << 64) - 1, 1), (17, 0), (16, 1), (15, 2)):
            for command, mode, address in ((10, 'X', C.addressof(source)), (10, 'A', C.addressof(source)),
                                            (11, 'C', C.addressof(target)), (11, 'B', C.addressof(target))):
                self.rejected(handle, storage_request(command, offset, address, size, mode))
        self.assertEqual(bytes(target)[:16], b'\xa5' * 16)
        self.assertEqual(self.readback(handle, 0, 16), bytes(16))

    def test_rejected_partial_protected_alias_read_cannot_partially_change_storage(self):
        _, _, handle = self.storage(16)
        source = self.buffer(b'changed contents'); address = C.addressof(source); denied = [True]

        def read(a, p, n):
            if a == address and denied[0]:
                C.memmove(p, a, n // 2)
                return -1
            C.memmove(p, a, n); return 0

        self.copiers(read=read)
        for mode in ('A', 'X'):
            self.rejected(handle, storage_request(10, 0, address, 16, mode))
            self.assertEqual(self.readback(handle, 0, 16), bytes(16))
        denied[0] = False
        self.send_rw(handle, 0, 10, 0, address, 16, 'A')
        self.assertEqual(self.readback(handle, 0, 16), b'changed contents')

    def test_preparation_refusal_preserves_entire_alias_and_pointer_destination(self):
        _, _, handle = self.storage(16)
        target = self.buffer(b'\xa5' * 16, True); address = C.addressof(target)
        prepared, written = [], []

        def write(a, p, n):
            written.append((a, n)); C.memmove(a, p, n); return 0

        self.copiers(write=write, prepare=lambda a, n: (prepared.append((a, n)), -1 if a == address else 0)[1])
        for mode in ('B', 'C'):
            self.rejected(handle, storage_request(11, 0, address, 16, mode))
            self.assertIn((address, 16), prepared)
            self.assertNotIn((address, 16), written)
            self.assertEqual(bytes(target)[:16], b'\xa5' * 16)

    def test_protected_alias_and_pointer_write_failure_keeps_request_and_destination(self):
        _, _, handle = self.storage(16)
        target = self.buffer(b'\xa5' * 16, True); address = C.addressof(target); writes = []

        def write(a, p, n):
            writes.append((a, n))
            if a == address:
                return -1
            C.memmove(a, p, n); return 0

        self.copiers(write=write)
        for mode in ('B', 'C'):
            self.rejected(handle, storage_request(11, 0, address, 16, mode))
            self.assertIn((address, 16), writes)
            self.assertEqual(bytes(target)[:16], b'\xa5' * 16)

    def test_reply_preparation_refusal_precedes_storage_or_output_side_effects(self):
        _, _, handle = self.storage(16)
        source = self.buffer(b'changed contents'); target = self.buffer(b'\xa5' * 16, True)
        deny = [True]
        self.copiers(prepare=lambda a, n: -1 if deny[0] and n == 256 else 0)
        for command, mode, address in ((10, 'A', C.addressof(source)), (10, 'X', C.addressof(source)),
                                       (11, 'B', C.addressof(target)), (11, 'C', C.addressof(target))):
            self.rejected(handle, storage_request(command, 0, address, 16, mode))
        deny[0] = False
        self.assertEqual(self.readback(handle, 0, 16), bytes(16))
        self.assertEqual(bytes(target)[:16], b'\xa5' * 16)

    def test_dual_active_halves_foreign_directions_and_changed_size_table_fail_closed(self):
        parent, _, obj = self.storage(32, True)
        source = self.buffer(b'x' * 16); target = self.buffer(b'\xa5' * 16, True)
        ia, oa = C.addressof(source), C.addressof(target)
        bad = [pointers.request(10, bytes(8), domain=obj, xs=((ia, 16, 0),), sends=((ia, 16),)),
               pointers.request(11, bytes(8), domain=obj, receives=((oa, 16),), cs=((oa, 16),), pointer_sizes=(16,)),
               pointers.request(10, bytes(8), domain=obj, receives=((oa, 16),)),
               pointers.request(11, bytes(8), domain=obj, sends=((ia, 16),)),
               pointers.request(11, bytes(8), domain=obj, receives=((0, 0),), cs=((oa, 16),), pointer_sizes=(15,)),
               pointers.request(10, bytes(16), domain=obj, xs=((ia, 16, 0),), sends=((0, 0),)),
               pointers.request(11, bytes(16), domain=obj, receives=((0, 0),), cs=((oa, 16),), pointer_sizes=(16,))]
        for wire in bad:
            self.rejected(parent, wire)
        self.assertEqual(bytes(target)[:16], b'\xa5' * 16)
        self.assertEqual(self.readback(parent, obj, 32), bytes(32))

    def test_aggregate_pointer_budget_is_checked_before_foreign_business_dispatch(self):
        _, _, handle = self.storage(0x600)
        source = self.buffer(b'x' * 0x300); target = self.buffer(b'\xa5' * 0x300, True)
        wire = pointers.request(10, bytes(8), xs=((C.addressof(source), 0x300, 0),),
                                sends=((0, 0),), receives=((0, 0),),
                                cs=((C.addressof(target), 0x300),), pointer_sizes=(0x300,))
        self.assertIn('aggregate', self.rejected(handle, wire))
        self.assertEqual(bytes(target)[:0x300], b'\xa5' * 0x300)

    def test_getsize_and_open_reject_foreign_descriptors_and_payload(self):
        parent, _, accessor = self.storage(16)
        for command, handle in ((0, parent), (0, accessor)):
            for wire in (pointers.request(command, b'x'), pointers.request(command, xs=((0, 0, 0),)),
                         pointers.request(command, cs=((0, 0),), pointer_sizes=(0,))):
                self.rejected(handle, wire)

    def test_cloned_accessor_keeps_shared_backing_with_independent_pointer_arenas(self):
        storage, _, accessor = self.storage(16)
        raw, _ = self.f.send(accessor, self.f.request(2, kind=5)); clone = self.f.response(raw)[1][0]
        source = self.buffer(b'shared clone!!!!'); address = C.addressof(source); arenas = []

        def read(a, p, n):
            if a == address:
                arenas.append(p)
            C.memmove(p, a, n); return 0

        self.copiers(read=read)
        self.send_rw(accessor, 0, 10, 0, address, 16, 'X')
        self.send_rw(clone, 0, 10, 0, address, 16, 'X')
        self.assertEqual(len(arenas), 2); self.assertNotEqual(arenas[0], arenas[1])
        self.assertEqual(self.f.close(storage), 0); self.assertEqual(self.f.close(accessor), 0)
        self.assertEqual(self.readback(clone, 0, 16), b'shared clone!!!!')
        self.assertEqual(self.f.close(clone), 0)

    def test_domain_copy_accessor_outlives_original_storage_object_and_transport(self):
        handle, storage, accessor = self.storage(16, True)
        source = self.buffer(b'domain backing!!')
        self.send_rw(handle, accessor, 10, 0, C.addressof(source), 16, 'X')
        raw, _ = self.f.send(handle, self.f.request(1, struct.pack('<I', accessor), kind=5))
        copy = self.f.response(raw)[1][0]
        for obj in (storage, accessor):
            close = struct.pack('<II', 4, 8) + bytes(8) + struct.pack('<BBHIII', 2, 0, 0, obj, 0, 0)
            self.f.send(handle, close)
        self.assertEqual(self.f.close(handle), 0)
        self.assertEqual(self.readback(copy, 0, 16), b'domain backing!!')
        self.assertEqual(self.f.close(copy), 0)

    def test_zero_nonnull_x_cannot_bypass_storage_schema_via_domain_close(self):
        handle, _, accessor = self.storage(16, True)
        # This parser exception belongs only to Write10. Domain-close is an
        # earlier dispatch path and must reject even zero-length descriptors.
        wire = (struct.pack('<II', 4 | 1 << 16, 8) + struct.pack('<II', 0, 0x1234) +
                struct.pack('<BBHIII', 2, 0, 0, accessor, 0, 0) + bytes(16))
        self.rejected(handle, wire)
        raw, _ = self.f.send(handle, self.f.request(0, domain=accessor))
        self.assertEqual(struct.unpack_from('<Q', self.f.response(raw, True)[0])[0], 16)

    def test_read_alias_and_pointer_cannot_overlap_the_original_reply_span(self):
        _, _, handle = self.storage(16)
        source = self.buffer(b'alias-overlap!!!')
        self.send_rw(handle, 0, 10, 0, C.addressof(source), 16, 'A')
        for mode in ('B', 'C'):
            with self.subTest(mode=mode):
                ipc = self.buffer(bytes(256), True); address = C.addressof(ipc)
                wire = storage_request(11, 0, address + 128, 16, mode)
                C.memmove(ipc, wire, len(wire))
                result = self.f.lib.botw_services_send(self.f.ctx, handle, ipc, 256, self.f.error, 1024)
                self.assertEqual(result, -1, 'A later IPC reply must not erase successful Read output')
                self.assertEqual(bytes(ipc)[:256], wire.ljust(256, b'\0'))


if __name__ == '__main__':
    unittest.main()
