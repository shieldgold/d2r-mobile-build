"""One fixed SZS child budget extends v3; no other heap or size is changed."""
import copy
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import test_botw_thread_stack_v3 as previous
from botw_native import thread_stack as ts

ROOT = Path(__file__).resolve().parents[1]
SZS_SITE = 0xe5ded4
SZS_ORIGINAL = bytes.fromhex('08c48352')
SZS_REPLACEMENT = bytes.fromhex('08d99f52')


def fixture():
    original, image = previous.v3_fixture()
    source = bytearray(original)
    for offset, context in ((ts.SZS_CONTEXT_OFFSET, ts.SZS_CONTEXT), *ts.SZS_DATAFLOW_CONTEXTS):
        source[offset:offset + len(context)] = context
    return bytes(source), bytes(source).ljust(len(image), b'\0')


class ThreadStackV4Tests(unittest.TestCase):
    def require_v4(self):
        self.assertIn('host-pthread-stack-v4', ts.POLICIES,
                      'Explicit v4 must budget the proven SZS child, without altering v1-v3')

    def test_v4_adds_exactly_one_word_to_v3_and_recovers_original_parent_image(self):
        self.require_v4()
        original, image = fixture()
        v3, old = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v3')
        final, manifest = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v4')
        self.assertEqual(len(final), len(v3))
        self.assertEqual(final[:SZS_SITE], v3[:SZS_SITE])
        self.assertEqual(final[SZS_SITE + 4:], v3[SZS_SITE + 4:])
        self.assertEqual(final[SZS_SITE:SZS_SITE + 4], SZS_REPLACEMENT)
        self.assertEqual(v3[SZS_SITE:SZS_SITE + 4], SZS_ORIGINAL)
        self.assertEqual(ts.verify(final, manifest), image)
        self.assertEqual(ts.verify(v3, old), image)
        self.assertEqual(len(manifest['patches']), 4)
        self.assertEqual(manifest['patches'][-1]['size_register'], 'w8')
        self.assertEqual(manifest['patches'][-1]['heap_budget'], {
            'original_heap_bytes': 0x1f58, 'heap_header_bytes': 0x130,
            'size_helper_argument_bytes': 8, 'original_addition_bytes': 0x1e20,
            'addition_bytes': 0xfec8, 'allocation_bytes': [0x220, 0x100, 0x8000, 0x1c0],
            'allocation_alignment_bytes': [8, 8, 0x4000, 8], 'split_header_bytes': 0x20,
            'maximum_alignment_padding_bytes': 0x3ff8, 'maximum_required_bytes': 0xc688,
            'heap_bytes': 0x10000, 'minimum_remaining_bytes': 0x3978})

    def test_context_parent_helper_queue_object_sdk_name_and_overlap_fail_closed(self):
        self.require_v4()
        original, image = fixture()
        for at in [SZS_SITE - 4, SZS_SITE, *(a for a, _ in ts.SZS_DATAFLOW_CONTEXTS)]:
            for kind in ('source', 'rewritten'):
                broken = bytearray(original if kind == 'source' else image); broken[at] ^= 1
                args = (bytes(broken), image) if kind == 'source' else (original, bytes(broken))
                with self.subTest(at=hex(at), kind=kind), self.assertRaises(ValueError):
                    ts.apply(*args, generic_site_offsets=[], policy='host-pthread-stack-v4')
        for source, working in [(original[:SZS_SITE + 2], image), (original, image[:SZS_SITE + 2])]:
            with self.assertRaises(ValueError):
                ts.apply(source, working, generic_site_offsets=[], policy='host-pthread-stack-v4')
        with self.assertRaises(ValueError):
            ts.apply(original, image, generic_site_offsets=[SZS_SITE], policy='host-pthread-stack-v4')

    def test_old_profiles_reject_new_unknown_or_truncated_szs_parameter(self):
        original, image = previous.v3_fixture()
        for name in ('host-page-alignment-v1', 'host-pthread-stack-v2', 'host-pthread-stack-v3'):
            for word in (SZS_REPLACEMENT, b'\0' * 4, SZS_ORIGINAL[:2]):
                source, working = bytearray(original), bytearray(image)
                source[SZS_SITE:SZS_SITE + 4] = word; working[SZS_SITE:SZS_SITE + 4] = word
                with self.subTest(profile=name, word=word.hex()), self.assertRaises(ValueError):
                    ts.apply(bytes(source), bytes(working), generic_site_offsets=[], policy=name)

    def test_v4_manifest_cannot_be_wrapped_in_old_profile_or_forged_budget(self):
        self.require_v4()
        original, image = fixture()
        final, manifest = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v4')
        for policy in (ts.POLICY, ts.NATIVE_POLICY, ts.VOLANTE_POLICY):
            bad = copy.deepcopy(manifest); bad.update(profile=policy['profile'], schema=policy['schema'])
            with self.assertRaises(ValueError): ts.verify(final, bad)
        for field in ('heap_budget', 'parent_allocator_call', 'context', 'replacement'):
            bad = copy.deepcopy(manifest)
            if field == 'heap_budget': bad['patches'][-1][field]['allocation_bytes'][1] = 0
            elif field == 'parent_allocator_call': bad['patches'][-1][field] += 4
            else: bad['patches'][-1][field] = '00'
            with self.subTest(field=field), self.assertRaises(ValueError): ts.verify(final, bad)
        for at in (SZS_SITE, SZS_SITE - 4, *[p for p, _ in ts.SZS_DATAFLOW_CONTEXTS]):
            data = bytearray(final); data[at] ^= 1
            with self.assertRaises(ValueError): ts.verify(bytes(data), manifest)
        with self.assertRaises(ValueError): ts.verify(final[:-4], manifest)

    def test_prior_full_contract_bytes_and_metadata_match_pre_v4_golden(self):
        self.require_v4()
        original, image = fixture()
        golden = {'host-page-alignment-v1': {'image_sha256': '84a77b2a7793a027be83642f8cdf58814f5db15a26747ef69c9c947c1642f89c', 'manifest_sha256': '2c4c0323e7c7a194a7879eedc4a536a471a969094729427333ae12770a552306'}, 'host-pthread-stack-v2': {'image_sha256': '19f7c3a7171a02e1ea9778aa88df82c17b8e3ada6ecb407a786389e1a68a8c06', 'manifest_sha256': 'bc3017cc8774ad3d54ca861479328ec28674673e4235e8946d95257290ce6405'}, 'host-pthread-stack-v3': {'image_sha256': 'f52d46f39c0bba1fa2d8747a881dec5f729f110cc0f97399097f165acc89d69e', 'manifest_sha256': '457816e5d2e227e0db62ccda621d9c8775c147294c6d06ca1d9321a6914641a9'}}
        for policy, expected in golden.items():
            final, manifest = ts.apply(original, image, generic_site_offsets=[], policy=policy)
            self.assertEqual(hashlib.sha256(final).hexdigest(), expected['image_sha256'])
            self.assertEqual(hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                             expected['manifest_sha256'])

    def test_loaded_modes_zero_to_three_reject_v4_without_complete_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'audit.so'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                            str(ROOT / 'native/botw_host/address_space.c'), '-o', str(binary)], check=True)
            audit = ctypes.CDLL(str(binary)).botw_address_space_audit_thread_stack_image
            audit.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            audit.restype = ctypes.c_int
            def check(data):
                raw = ctypes.create_string_buffer(data); error = ctypes.create_string_buffer(512)
                return audit(raw, len(data), 23280432, error, 512)
            original, image = previous.v3_fixture()
            original = bytearray(original); original[SZS_SITE:SZS_SITE + 4] = SZS_ORIGINAL
            image = bytes(original).ljust(len(image), b'\0')
            for mode, name in enumerate((None, 'host-page-alignment-v1', 'host-pthread-stack-v2', 'host-pthread-stack-v3')):
                final = image if name is None else ts.apply(bytes(original), image, generic_site_offsets=[], policy=name)[0]
                self.assertEqual(check(final), mode)
                for word in (SZS_REPLACEMENT, b'\0' * 4):
                    data = bytearray(final); data[SZS_SITE:SZS_SITE + 4] = word
                    with self.subTest(mode=mode, word=word.hex()): self.assertEqual(check(bytes(data)), -1)
            self.require_v4()
            original, image = fixture()
            final, _ = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v4')
            self.assertEqual(check(final), 4)
            for at in [ts.VOLANTE_SITE, ts.CAPACITY_SITE, ts.SITE, SZS_SITE,
                       *(a for a, _ in ts.SZS_DATAFLOW_CONTEXTS)]:
                data = bytearray(final); data[at] ^= 1
                self.assertEqual(check(bytes(data)), -1)
            self.assertEqual(check(final[:-4]), -1)


if __name__ == '__main__':
    unittest.main()
