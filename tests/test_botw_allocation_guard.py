"""Owned non-executable byte fixtures for one opt-in normal allocation guard.

The baseline is an explicit replay of the prior pass-through byte contract.
Neither path loads, emits to disk, or executes an actual game image.
"""
import copy
import hashlib
import os
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
BASELINE = os.environ.get('BOTW_ALLOCATION_GUARD_BASELINE') == '1'
if not BASELINE:
    from botw_native import allocation_guard as guard

SITE = 0xdb93a8
OLD_SIZE = 37723336
KEY = 0x2384000
REFERENCE = 0x23e5d6c
CONTEXT_START = 0xdb9380
CONTEXT = bytes.fromhex(
    '394741f928430091a8573aa9620240f9a18301d1e3031d32e4030032e0031faa'
    'e5031f2a663ff197f60300aa3f1a0c9448a600f0088940f9000140f9e10316aa'
    '861a0c94c80240f9081540f9e00316aa00013fd6e00315aa')
GATE_TEMPLATE = bytes.fromhex(
    'fd7bbfa97ed03bd5fdfcfff0bd0f40f9de031d8bde0340f9fd0340f9'
    'c0033fd6fd7bc1a8189bc017')
FIXUPS = (
    b'# PATCH_OFF\t0x2390000\n# KEY_PAGE_OFF\t0x2384000\n# KEY_PAGE_SIZE\t0x4000\n'
    b'# TEXT_END\t0x1633b30\n# RO_OFF\t0x1634000\n# DATA_OFF\t0x2068000\n'
    b'# KEY_K18_IDX\t0\n# KEY_KTLS_IDX\t1\n# KEY_KTLR_IDX\t2\n# KEY_KGATE_IDX\t3\n'
    b'# KEY_KRES0_IDX\t4\n# KEY_KRES1_IDX\t5\n# KEY_KASPECT_IDX\t6\n# KEY_KASPECTUI_IDX\t7\n')
OLD_GATES = b'0x23e5d8c\tsvc\t38\t0x140c9ec\n'
OLD_SITES = b'0x140c9ec\tsvc\n'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def branch_target(word, pc):
    return pc + signed(word & 0x3ffffff, 26) * 4


class AllocationGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # All bytes belong to this fixture. Known instruction contexts are
        # structural tokens; the rest is zero, not a copied game/ELF image.
        owned = bytearray(OLD_SIZE)
        owned[CONTEXT_START:CONTEXT_START + len(CONTEXT)] = CONTEXT
        owned[REFERENCE:REFERENCE + len(GATE_TEMPLATE)] = GATE_TEMPLATE
        cls.owned = bytes(owned)

    def apply_owned(self, image=None, fixups=FIXUPS, gates=OLD_GATES, sites=OLD_SITES):
        image = self.owned if image is None else image
        if BASELINE:
            return image, gates, sites, {'baseline': 'owned v4 pass-through replay'}
        return guard._apply_admitted(image, fixups, gates, sites)

    def test_owned_old_output_is_replaced_by_one_guard_and_normal_128_gate(self):
        image, gates, sites, _ = self.apply_owned()
        self.assertNotEqual(image[SITE:SITE + 4], self.owned[SITE:SITE + 4])
        self.assertEqual(len(image), OLD_SIZE + 52)
        self.assertEqual(gates, OLD_GATES + f'0x{OLD_SIZE + 44:x}\tsvc\t128\t0x{SITE:x}\n'.encode())
        self.assertEqual(sites, OLD_SITES + f'0x{SITE:x}\tnormal-allocation-guard-v1\n'.encode())
        self.assertEqual(image[:SITE], self.owned[:SITE])
        self.assertEqual(image[SITE + 4:OLD_SIZE], self.owned[SITE + 4:])

    def test_success_path_preserves_original_mov_flags_frame_lr_and_x18(self):
        image, _, _, _ = self.apply_owned()
        jump = struct.unpack_from('<I', image, SITE)[0]
        self.assertEqual(jump >> 26, 0b000101)  # B, never BL
        self.assertEqual(branch_target(jump, SITE), OLD_SIZE)
        cbz, original, back = struct.unpack_from('<III', image, OLD_SIZE)
        self.assertEqual(cbz & 0xff00001f, 0xb4000000)  # CBZ x0, no NZCV update
        self.assertEqual(OLD_SIZE + signed((cbz >> 5) & 0x7ffff, 19) * 4, OLD_SIZE + 12)
        self.assertEqual(original, 0xaa0003f6)  # actual original MOV x22,x0
        self.assertEqual(back >> 26, 0b000101)
        self.assertEqual(branch_target(back, OLD_SIZE + 8), SITE + 4)
        # Those three exact operations have no stack, LR/x18 or NZCV write.
        self.assertEqual(image[OLD_SIZE + 4:OLD_SIZE + 8], self.owned[SITE:SITE + 4])

    def test_failure_gate_uses_the_complete_existing_kgate_pattern(self):
        image, gates, _, _ = self.apply_owned()
        code = image[OLD_SIZE + 12:]
        self.assertEqual(code[:8], GATE_TEMPLATE[:8])
        self.assertEqual(code[12:36], GATE_TEMPLATE[12:36])
        word = struct.unpack_from('<I', code, 8)[0]
        self.assertEqual(word & 0x9f00001f, 0x9000001d)  # ADRP x29
        page_delta = signed(((word >> 29) & 3) | (((word >> 5) & 0x7ffff) << 2), 21)
        self.assertEqual(((OLD_SIZE + 20) & ~0xfff) + page_delta * 4096, KEY)
        self.assertEqual(branch_target(struct.unpack_from('<I', code, 36)[0], OLD_SIZE + 48), SITE + 4)
        self.assertIn(b'\tsvc\t128\t', gates)
        # Same original current-key load and x29/x30 save/restore, not a new ABI.
        self.assertEqual(code[12:16], bytes.fromhex('bd0f40f9'))

    def test_owned_roundtrip_checks_all_bytes_metadata_and_manifest(self):
        image, gates, sites, manifest = self.apply_owned()
        self.assertEqual(guard._verify_admitted(image, FIXUPS, gates, sites, manifest), self.owned)
        self.assertEqual(manifest['input_image_sha256'], digest(self.owned))
        self.assertEqual(manifest['output_image_sha256'], digest(image))
        self.assertEqual(manifest['schema'], 'normal-allocation-guard-v1')
        self.assertEqual(manifest['policy'], guard.POLICY)
        for key in ('output_image_sha256', 'gate_return', 'input_image_bytes'):
            bad = copy.deepcopy(manifest)
            bad[key] = True
            with self.subTest(field=key), self.assertRaises(ValueError):
                guard._verify_admitted(image, FIXUPS, gates, sites, bad)
        bad = copy.deepcopy(manifest)
        bad['policy']['normal_hostcall'] = 129
        with self.assertRaises(ValueError):
            guard._verify_admitted(image, FIXUPS, gates, sites, bad)

    def test_public_entry_requires_opt_in_and_fixed_full_v4_identity(self):
        for enabled in (False, None, 1, 'true'):
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                guard.apply(self.owned, FIXUPS, OLD_GATES, OLD_SITES, enabled=enabled)
        with self.assertRaises(ValueError):
            guard.apply(self.owned, FIXUPS, OLD_GATES, OLD_SITES, enabled=True)
        image, gates, sites, manifest = self.apply_owned()
        with self.assertRaises(ValueError):
            guard.verify(image, FIXUPS, gates, sites, manifest)

    def test_context_layout_gate_and_site_collisions_fail_before_transformation(self):
        bad = bytearray(self.owned)
        bad[CONTEXT_START] ^= 1
        with self.assertRaises(ValueError):
            self.apply_owned(bytes(bad))
        with self.assertRaises(ValueError):
            self.apply_owned(fixups=FIXUPS.replace(b'0x2384000', b'0x2388000'))
        with self.assertRaises(ValueError):
            self.apply_owned(sites=OLD_SITES + f'0x{SITE:x}\tx18\n'.encode())
        with self.assertRaises(ValueError):
            self.apply_owned(gates=OLD_GATES + OLD_GATES)
        with self.assertRaises(ValueError):
            self.apply_owned(gates=OLD_GATES + b'0x23e5dc0\tsvc\t128\t0x140c9f0\n')
        bad = bytearray(self.owned)
        bad[REFERENCE] ^= 1
        with self.assertRaises(ValueError):
            self.apply_owned(bytes(bad))

    def test_inverse_rejects_thunk_metadata_unrelated_byte_and_extra_suffix(self):
        image, gates, sites, manifest = self.apply_owned()
        for offset in (0, SITE, OLD_SIZE, OLD_SIZE + 8, OLD_SIZE + 20, OLD_SIZE + 44, OLD_SIZE + 51):
            changed = bytearray(image)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                guard._verify_admitted(bytes(changed), FIXUPS, gates, sites, manifest)
        for bad_gates, bad_sites in [(gates + b'\n', sites),
                                      (gates.replace(b'\tsvc\t128\t', b'\tsvc\t38\t'), sites),
                                      (gates, sites + b'\n')]:
            with self.assertRaises(ValueError):
                guard._verify_admitted(image, FIXUPS, bad_gates, bad_sites, manifest)
        with self.assertRaises(ValueError):
            guard._verify_admitted(image + b'\0\0\0\0', FIXUPS, gates, sites, manifest)
        with self.assertRaises(ValueError):
            self.apply_owned(image=image, gates=gates, sites=sites)


if __name__ == '__main__':
    unittest.main()
