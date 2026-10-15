"""GetDesiredLanguage's real services transport; no private game data required."""
import ctypes as C
import struct
import unittest

import test_botw_services as baseline


class ApplicationLanguageTests(unittest.TestCase):
    # Reuse the real C services build and hand-built transport helpers without
    # inheriting or collecting the existing services test class a second time.
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
    application_functions = baseline.ServicesTests.application_functions
    applet_kernel = baseline.ServicesTests.applet_kernel
    applet_state = baseline.ServicesTests.applet_state
    applet_proxy = baseline.ServicesTests.applet_proxy
    proxy_child = baseline.ServicesTests.proxy_child

    def configure(self, code=b'en-US', source=b'test-profile:verified-USen', *, good=True):
        setter = getattr(self.lib, 'botw_services_set_application_language', None)
        self.assertIsNotNone(setter, 'missing explicit application-language configuration')
        setter.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p, C.c_size_t]
        result = setter(self.ctx, code, source, self.error, len(self.error))
        self.assertEqual(result, 0 if good else -1, self.error.value)

    def language(self, handle, object_id=0):
        raw, _ = self.send(handle, self.request(21, domain=object_id))
        data, handles, objects = self.response(raw, domain=bool(object_id))
        self.assertEqual((handles, objects), ([], 0))
        self.assertEqual(struct.unpack_from('<I', raw, 4)[0], 12 if object_id else 8)
        self.assertEqual(data[8:], bytes(len(data) - 8))
        return data[:8]

    def assert_rejected_unchanged(self, handle, request, diagnostic=None):
        raw, error = self.send(handle, request, good=False)
        self.assertEqual(raw[:len(request)], request)
        if diagnostic:
            self.assertIn(diagnostic, error)

    def test_unconfigured_language_fails_without_success_reply(self):
        for domain in (False, True):
            with self.subTest(domain=domain):
                handle, object_id = self.application_functions(domain)
                self.assert_rejected_unchanged(handle, self.request(21, domain=object_id),
                                               'explicit application language')

    def test_explicit_language_is_exact_eight_byte_code_in_session_and_domain(self):
        self.configure()
        for domain in (False, True):
            with self.subTest(domain=domain):
                handle, object_id = self.application_functions(domain)
                self.assertEqual(self.language(handle, object_id), b'en-US\0\0\0')
                self.assertEqual(self.language(handle, object_id), b'en-US\0\0\0')
        self.assertTrue(any('language=en-US' in line and 'test-profile:verified-USen' in line
                            for line in self.logs))

    def test_configuration_copies_inputs_is_idempotent_and_rejects_replacement(self):
        code = C.create_string_buffer(b'en-US')
        source = C.create_string_buffer(b'explicit-local-profile')
        self.configure(code, source)
        code.value, source.value = b'ja', b'changed'
        handle, object_id = self.application_functions(True)
        self.assertEqual(self.language(handle, object_id), b'en-US\0\0\0')
        self.configure(b'en-US', b'explicit-local-profile')
        self.configure(b'ja', b'explicit-local-profile', good=False)
        self.configure(b'en-US', b'another-source', good=False)
        self.assertEqual(self.language(handle, object_id), b'en-US\0\0\0')

    def test_invalid_code_and_source_leave_configuration_unset(self):
        for code, source in ((None, b'profile'), (b'', b'profile'), (b'enUS', b'profile'),
                             (b'en-XX', b'profile'), (b'en-USxx', b'profile'),
                             (b'12345678', b'profile'), (b'\xff', b'profile'),
                             (b'en-US', None), (b'en-US', b''),
                             (b'en-US', b'x' * 128), (b'en-US', b'line\nbreak')):
            with self.subTest(code=code, source=source):
                self.configure(code, source, good=False)
        handle, object_id = self.application_functions(True)
        self.assert_rejected_unchanged(handle, self.request(21, domain=object_id),
                                       'explicit application language')

    def test_canonical_codes_encode_safely_without_claiming_title_asset_support(self):
        # Generic Nintendo language registry, independent of this title's mask.
        codes = (b'ja', b'en-US', b'fr', b'de', b'it', b'es', b'zh-CN', b'ko',
                 b'nl', b'pt', b'ru', b'zh-TW', b'en-GB', b'fr-CA', b'es-419',
                 b'zh-Hans', b'zh-Hant', b'pt-BR')
        for code in codes:
            with self.subTest(code=code):
                original = self.ctx
                self.ctx = self.lib.botw_services_create(self.log, self.memory, None)
                self.assertTrue(self.ctx)
                try:
                    self.configure(code, b'test-only:canonical-encoding-not-title-support')
                    handle, object_id = self.application_functions(True)
                    self.assertEqual(self.language(handle, object_id), code.ljust(8, b'\0'))
                finally:
                    self.lib.botw_services_destroy(self.ctx)
                    self.ctx = original

    def test_command_rejects_payload_pid_handles_and_buffers_without_touching_output(self):
        self.configure()
        output = C.create_string_buffer(b'\xa5' * 32)
        self.ranges.append((C.addressof(output), len(output), True))
        handle, object_id = self.application_functions(True)
        for options in ({'payload': b'\1'}, {'payload': bytes(4)},
                        {'payload': bytes(20)}, {'pid': True}, {'copies': (0xffff8001,)},
                        {'buffer': (C.addressof(output), 16)},
                        {'receive': (C.addressof(output), 16)},
                        {'static': (0, 0)}, {'buffer': (0, 0)},
                        {'receive': (0, 0)}, {'receive_static': (0, 0)}):
            with self.subTest(options=options):
                self.assert_rejected_unchanged(handle, self.request(21, domain=object_id, **options))
                self.assertEqual(output.raw, b'\xa5' * 32 + b'\0')
        self.assertEqual(self.language(handle, object_id), b'en-US\0\0\0')

    def test_cloned_handle_keeps_the_same_explicit_context_language(self):
        self.configure(b'ja', b'test-profile:verified-JPja')
        handle, object_id = self.application_functions(True)
        raw, _ = self.send(handle, self.request(2, kind=5))
        _, handles, _ = self.response(raw)
        self.assertEqual(len(handles), 1)
        self.assertEqual(self.language(handles[0], object_id), b'ja\0\0\0\0\0\0')

    def test_zero_input_transport_padding_is_not_semantic_payload(self):
        self.configure()
        for domain in (False, True):
            with self.subTest(domain=domain):
                handle, object_id = self.application_functions(domain)
                request = bytearray(self.request(21, domain=object_id))
                # cmifMakeRequest reserves 16 alignment bytes. With no
                # descriptors, eight lie after the exact CMIF/domain data.
                request[-8:] = b'\xa5' * 8
                raw, _ = self.send(handle, bytes(request))
                data, handles, objects = self.response(raw, domain=domain)
                self.assertEqual((data[:8], handles, objects), (b'en-US\0\0\0', [], 0))


if __name__ == '__main__':
    unittest.main()
