"""Fixed Volante child-heap budget extends v2 without changing its outputs."""
import copy
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import test_botw_thread_stack as baseline
from botw_native import thread_stack as ts

ROOT = Path(__file__).resolve().parents[1]


def v3_fixture():
    original, _, _ = baseline.fixture()
    original = bytearray(original.ljust(0x1c00100, b'\0'))
    original[ts.VOLANTE_CONTEXT_OFFSET:ts.VOLANTE_CONTEXT_OFFSET + len(ts.VOLANTE_CONTEXT)] = ts.VOLANTE_CONTEXT
    for offset, context in ts.VOLANTE_DATAFLOW_CONTEXTS:
        original[offset:offset + len(context)] = context
    return bytes(original), bytes(original).ljust(0x1c10000, b'\0')


class ThreadStackV3Tests(unittest.TestCase):
    def require_v3(self):
        self.assertIn('host-pthread-stack-v3', ts.POLICIES,
                      'Explicit v3 must own the one proven insufficient Volante child heap')

    def test_v3_changes_only_v2_contract_plus_one_owned_heap_budget_and_undoes_exactly(self):
        self.require_v3()
        original, image = v3_fixture()
        v2, old_manifest = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v2')
        final, manifest = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v3')
        self.assertEqual(len(final), len(v2))
        self.assertEqual(final[:ts.VOLANTE_SITE], v2[:ts.VOLANTE_SITE])
        self.assertEqual(final[ts.VOLANTE_SITE + 4:], v2[ts.VOLANTE_SITE + 4:])
        self.assertEqual(final[ts.VOLANTE_SITE:ts.VOLANTE_SITE + 4], ts.VOLANTE_REPLACEMENT)
        self.assertEqual(ts.verify(final, manifest), image)
        self.assertEqual(ts.verify(v2, old_manifest), image)
        budget = manifest['patches'][-1]['heap_budget']
        self.assertEqual(budget['allocation_bytes'], [0x108, 0x100, 0x8000, 0x1c0])
        self.assertEqual(budget['maximum_required_bytes'], 0xc570)
        self.assertEqual(budget['heap_bytes'], 0x10000)
        self.assertEqual(budget['minimum_remaining_bytes'], 0x3a90)

    def test_heap_context_caller_parent_allocation_truncation_and_overlap_are_strict(self):
        self.require_v3()
        original, image = v3_fixture()
        for offset in [ts.VOLANTE_SITE - 4, ts.VOLANTE_SITE,
                       *(at for at, _ in ts.VOLANTE_DATAFLOW_CONTEXTS)]:
            with self.subTest(offset=hex(offset)):
                broken = bytearray(original); broken[offset] ^= 1
                with self.assertRaises(ValueError):
                    ts.apply(bytes(broken), image, generic_site_offsets=[], policy='host-pthread-stack-v3')
                broken = bytearray(image); broken[offset] ^= 1
                with self.assertRaises(ValueError):
                    ts.apply(original, bytes(broken), generic_site_offsets=[], policy='host-pthread-stack-v3')
        for source, rewritten in [(original[:ts.VOLANTE_SITE + 2], image), (original, image[:ts.VOLANTE_SITE + 2])]:
            with self.assertRaises(ValueError):
                ts.apply(source, rewritten, generic_site_offsets=[], policy='host-pthread-stack-v3')
        with self.assertRaises(ValueError):
            ts.apply(original, image, generic_site_offsets=[ts.VOLANTE_SITE], policy='host-pthread-stack-v3')

    def test_old_profiles_cannot_smuggle_heap_patch_or_forge_v3_manifest(self):
        self.require_v3()
        original, image = v3_fixture()
        final, manifest = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v3')
        for policy in ['host-page-alignment-v1', 'host-pthread-stack-v2']:
            old, old_manifest = ts.apply(original, image, generic_site_offsets=[], policy=policy)
            patched = bytearray(old); patched[ts.VOLANTE_SITE:ts.VOLANTE_SITE + 4] = ts.VOLANTE_REPLACEMENT
            with self.assertRaises(ValueError): ts.verify(bytes(patched), old_manifest)
        for edit in ['profile', 'heap_budget', 'caller', 'truncated', 'unknown-capacity']:
            broken = copy.deepcopy(manifest); data = bytearray(final)
            if edit == 'profile': broken['profile'] = ts.NATIVE_POLICY['profile']
            elif edit == 'heap_budget': broken['patches'][-1]['heap_budget']['allocation_bytes'][1] = 0
            elif edit == 'caller': data[ts.VOLANTE_DATAFLOW_CONTEXTS[0][0]] ^= 1
            elif edit == 'truncated': data = data[:-4]
            else: data[ts.VOLANTE_SITE:ts.VOLANTE_SITE + 4] = bytes.fromhex('4000a052')
            with self.subTest(edit=edit), self.assertRaises(ValueError): ts.verify(bytes(data), broken)

    def test_published_v1_v2_bytes_and_manifests_remain_recomputable(self):
        self.require_v3()
        original, image, _ = baseline.fixture()
        # Golden receipts captured from the pre-v3 implementation. The only
        # extra original source word models the actual unmodified heap site.
        source, working = bytearray(original), bytearray(image)
        for data in (source, working): struct.pack_into('<I', data, 0x7268d8, 0x321203e0)
        expected = {
            'host-page-alignment-v1': ('02ce8e7c9a2b92a83b59070f150c06e8a5a074da7fa1adbaefc919db22e2fc0d',
                                       '2b1ad36dfbfc4400c16b687cf2c2eb2723df9bb828ea444d67401fa833b05488'),
            'host-pthread-stack-v2': ('ef043d7faef01fffff38b4fff9abbdd9366ea7b3c8fa119f111b37e44d95702f',
                                     '68e30de5fe2faea664e3435e737410a458009a398c8c3323c42d7ff21eeb69a0')}
        for policy, (image_sha, manifest_sha) in expected.items():
            result, manifest = ts.apply(bytes(source), bytes(working), generic_site_offsets=[], policy=policy)
            self.assertEqual(hashlib.sha256(result).hexdigest(), image_sha)
            self.assertEqual(hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(), manifest_sha)

    def test_runtime_checks_v3_actual_bytes_and_rejects_heap_only_or_unknown_context(self):
        self.require_v3()
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / 'audit.so'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                str(ROOT / 'native/botw_host/address_space.c'), '-o', str(library)], check=True)
            audit = ctypes.CDLL(str(library)).botw_address_space_audit_thread_stack_image
            audit.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            audit.restype = ctypes.c_int
            def check(data):
                raw = ctypes.create_string_buffer(data); error = ctypes.create_string_buffer(512)
                return audit(raw, len(data), 23280432, error, 512)
            original, image = v3_fixture()
            final, _ = ts.apply(original, image, generic_site_offsets=[], policy='host-pthread-stack-v3')
            self.assertEqual(check(final), 3)
            for at in [ts.VOLANTE_SITE, ts.VOLANTE_SITE - 4,
                       *(p for p, _ in ts.VOLANTE_DATAFLOW_CONTEXTS), ts.SITE, ts.CAPACITY_SITE]:
                broken = bytearray(final); broken[at] ^= 1
                self.assertEqual(check(bytes(broken)), -1)
            heap_only = bytearray(image); heap_only[ts.VOLANTE_SITE:ts.VOLANTE_SITE + 4] = ts.VOLANTE_REPLACEMENT
            self.assertEqual(check(bytes(heap_only)), -1)
            self.assertEqual(check(final[:-4]), -1)


if __name__ == '__main__':
    unittest.main()
