"""Owned HIPC transport lifetimes using the real complete service module."""
import ctypes as C
from concurrent.futures import ThreadPoolExecutor
import struct
import unittest
import test_botw_services as service_fixtures

ALLOCATE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p,
                      C.POINTER(C.c_uint32), C.c_void_p, C.c_size_t)
RELEASE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_uint32,
                     C.c_void_p, C.c_size_t)


class TransportAPI(C.Structure):
    _fields_ = [('opaque', C.c_void_p), ('allocate', ALLOCATE), ('release', RELEASE)]


class ServicesTransportTests(unittest.TestCase):
    # Reuse the real service fixture compiler and HIPC packet construction;
    # no second parser or fake service implementation is substituted.
    connect = service_fixtures.ServicesTests.connect
    close = service_fixtures.ServicesTests.close
    request = staticmethod(service_fixtures.ServicesTests.request)
    send = service_fixtures.ServicesTests.send
    response = service_fixtures.ServicesTests.response

    @classmethod
    def setUpClass(cls):
        service_fixtures.ServicesTests.setUpClass.__func__(cls)
        cls.addClassCleanup(cls.build.cleanup)

    def setUp(self):
        service_fixtures.ServicesTests.setUp(self)
        self.owners, self.released = {}, []
        self.next_id, self.allocate_error, self.release_error = 0x2001, False, False
        self.forced_id = None

        def allocate(_, token, out, error, capacity):
            if self.allocate_error:
                return -1
            identity = self.forced_id if self.forced_id is not None else self.next_id
            self.next_id += 17
            # An intentionally broken producer may return an alien ID; it does
            # not get permission to replace another owner's kernel object.
            if identity and identity not in self.owners:
                self.owners[identity] = token
            out[0] = identity
            return 0

        def release(_, token, identity, error, capacity):
            if self.release_error:
                return -1
            if self.owners.get(identity) != token:
                # No borrowed token remains. This acknowledges only the new
                # token's absence; an alien ID's actual owner stays untouched.
                return 0 if token not in self.owners.values() else -1
            del self.owners[identity]
            self.released.append((identity, token))
            return 0

        self.allocate_callback, self.release_callback = ALLOCATE(allocate), RELEASE(release)
        self.api = TransportAPI(None, self.allocate_callback, self.release_callback)

    def tearDown(self):
        self.release_error = False
        if self.ctx:
            if hasattr(self.lib, 'botw_services_destroy_checked'):
                rc = self.lib.botw_services_destroy_checked(self.ctx, self.error, 1024)
                self.assertEqual(rc, 0, self.error.value)
            else:
                self.lib.botw_services_destroy(self.ctx)
            self.ctx = None

    def bind(self, api=None):
        self.assertTrue(hasattr(self.lib, 'botw_services_set_transport_api'),
                        'complete services still allocate a separate numeric kernel namespace')
        self.assertTrue(hasattr(self.lib, 'botw_services_destroy_checked'),
                        'transport release failures cannot preserve the owning service context')
        self.lib.botw_services_set_transport_api.argtypes = [C.c_void_p, C.POINTER(TransportAPI), C.c_char_p, C.c_size_t]
        self.lib.botw_services_destroy_checked.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
        return self.lib.botw_services_set_transport_api(self.ctx, C.byref(api or self.api), self.error, 1024)

    def query_capacity(self, handle):
        raw, _ = self.send(handle, self.request(3, kind=5))
        payload, _, _ = self.response(raw)
        self.assertEqual(struct.unpack_from('<H', payload)[0], 0x500)

    def test_connect_and_clone_use_actual_external_ids_and_distinct_owner_tokens(self):
        self.assertEqual(self.bind(), 0, self.error.value)
        first = self.connect()
        self.assertEqual(first, 0x2001)
        self.query_capacity(first)
        raw, _ = self.send(first, self.request(2, kind=5))
        _, handles, _ = self.response(raw)
        self.assertEqual(handles, [0x2012])
        second = handles[0]
        self.assertNotEqual(self.owners[first], self.owners[second])
        self.assertEqual(self.close(first), 0)
        self.query_capacity(second)
        self.assertEqual(self.close(second), 0)
        self.assertEqual(len(self.released), 2)
        self.assertFalse(self.owners)

    def test_partial_api_is_rejected_before_any_binding_or_handle_changes(self):
        self.assertNotEqual(self.bind(TransportAPI(None, self.allocate_callback, RELEASE())), 0)
        self.assertEqual(self.bind(), 0)
        self.assertEqual(self.connect(), 0x2001)

    def test_binding_cannot_replace_a_used_namespace_even_after_last_close(self):
        self.assertEqual(self.bind(), 0)
        handle = self.connect()
        self.assertNotEqual(self.bind(), 0)
        self.assertEqual(self.close(handle), 0)
        self.assertNotEqual(self.bind(), 0)
        self.assertFalse(self.owners)

    def test_alloc_failure_preserves_output_and_never_falls_back_to_legacy_ids(self):
        self.assertEqual(self.bind(), 0)
        self.allocate_error = True
        handle = C.c_uint32(0xfeed)
        self.assertEqual(self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(handle), self.error, 1024), -1)
        self.assertEqual(handle.value, 0xfeed)
        self.assertFalse(self.owners)
        self.allocate_error = False
        self.assertEqual(self.connect(), 0x2001)

    def test_bad_or_duplicate_id_never_retires_another_owner(self):
        self.assertEqual(self.bind(), 0)
        first = self.connect()
        for identity in (0, 0xffff8000, 0xffff8001, first):
            self.forced_id = identity
            out = C.c_uint32(0xfeed)
            self.assertEqual(self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(out), self.error, 1024), -1)
            self.assertEqual(out.value, 0xfeed)
            self.query_capacity(first)
            self.assertIn(first, self.owners)
        self.forced_id = None
        self.assertEqual(self.close(first), 0)

    def test_failed_release_keeps_session_and_pointer_arena_available_for_retry(self):
        self.assertEqual(self.bind(), 0)
        handle = self.connect()
        self.release_error = True
        self.assertEqual(self.close(handle), -1)
        self.query_capacity(handle)
        self.assertIn(handle, self.owners)
        self.release_error = False
        self.assertEqual(self.close(handle), 0)
        self.assertEqual(self.close(handle), -1)
        self.assertEqual(len(self.released), 1)

    def test_invalid_allocation_cleanup_failure_retains_token_and_blocks_new_allocations(self):
        self.assertEqual(self.bind(), 0)
        first = self.connect()
        self.forced_id, self.release_error = 0, True
        output = C.c_uint32(0xfeed)
        self.assertEqual(self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(output), self.error, 1024), -1)
        self.assertEqual(output.value, 0xfeed)
        self.forced_id, self.release_error = None, False
        self.query_capacity(first)
        self.assertEqual(self.lib.botw_services_connect(self.ctx, b'sm:', 3, C.byref(output), self.error, 1024), -1)
        self.assertIn(b'cleanup is pending', self.error.value)
        self.assertEqual(self.lib.botw_services_destroy_checked(self.ctx, self.error, 1024), 0)
        self.ctx = None
        self.assertFalse(self.owners)

    def test_server_close_keeps_kernel_endpoint_until_explicit_handle_close(self):
        self.assertEqual(self.bind(), 0)
        handle = self.connect()
        self.send(handle, struct.pack('<II', 2, 0))
        self.assertIn(handle, self.owners)
        self.assertFalse(self.released)
        self.assertEqual(self.close(handle), 0)
        self.assertFalse(self.owners)

    def test_failed_checked_destruction_retains_context_and_can_be_retried(self):
        self.assertEqual(self.bind(), 0)
        first, second = self.connect(), self.connect()
        self.release_error = True
        self.assertEqual(self.lib.botw_services_destroy_checked(self.ctx, self.error, 1024), -1)
        self.query_capacity(first)
        self.query_capacity(second)
        self.release_error = False
        self.assertEqual(self.lib.botw_services_destroy_checked(self.ctx, self.error, 1024), 0)
        self.ctx = None
        self.assertFalse(self.owners)
        self.assertEqual(len(self.released), 2)

    def test_concurrent_connect_close_stays_in_one_serialized_transport_namespace(self):
        self.assertEqual(self.bind(), 0)
        def cycle(_):
            handle = self.connect()
            self.assertEqual(self.close(handle), 0)
            return handle
        with ThreadPoolExecutor(max_workers=8) as workers:
            handles = list(workers.map(cycle, range(64)))
        self.assertEqual(len(set(handles)), 64)
        self.assertFalse(self.owners)
        self.assertEqual(len(self.released), 64)


if __name__ == '__main__':
    unittest.main()
