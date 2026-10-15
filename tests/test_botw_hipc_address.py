"""Synthetic HIPC fields and ARM64 state tests; no game bytes are loaded."""
import importlib.util
import ctypes as C
import hashlib
import json
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from botw_native.hipc_address import (
    INLINE_SITES, HELPER_PATHS, apply, verify, audit_original_inline_sites, patch_inline_addresses,
    pack_send_static, unpack_send_static, pack_buffer, unpack_buffer,
    pack_recv_static, unpack_recv_static,
)


class DescriptorTests(unittest.TestCase):
    def test_static_39bit_boundaries_preserve_size_and_all_six_index_bits(self):
        for address in (0, (1 << 32) - 1, 1 << 32, (1 << 36) - 1,
                        1 << 36, 1 << 38, (1 << 39) - 1):
            for size in (0, 1, 0xffff):
                for index in (0, 15, 16, 63):
                    descriptor = pack_send_static(address, size, index)
                    self.assertEqual(unpack_send_static(descriptor), (address, size, index))
                    first, low = struct.unpack('<II', descriptor)
                    self.assertEqual(first & 63, index)
                    self.assertEqual(first >> 16, size)
                    self.assertEqual(low, address & 0xffffffff)

    def test_models_keep_full_protocol_decode_width_and_explicit_36bit_profile(self):
        for address in ((1 << 36) - 1,):
            self.assertEqual(pack_send_static(address, 7, 15, address_bits=36),
                             struct.pack('<II', 7 << 16 | 15 << 12 | 15, 0xffffffff))
        self.assertEqual(unpack_send_static(pack_send_static((1 << 42) - 1, 0xffff, 63, address_bits=42)),
                         ((1 << 42) - 1, 0xffff, 63))
        self.assertEqual(unpack_buffer(pack_buffer((1 << 58) - 1, (1 << 36) - 1, 3, address_bits=58)),
                         ((1 << 58) - 1, (1 << 36) - 1, 3))
        self.assertEqual(unpack_recv_static(pack_recv_static((1 << 48) - 1, 0xffff, address_bits=48)),
                         ((1 << 48) - 1, 0xffff))

    def test_map_and_recv_fields_remain_disjoint(self):
        rng = random.Random(19)
        for _ in range(200):
            address, size = rng.randrange(1 << 39), rng.randrange(1 << 36)
            for mode in range(4):
                self.assertEqual(unpack_buffer(pack_buffer(address, size, mode)), (address, size, mode))
            size &= 0xffff
            self.assertEqual(unpack_recv_static(pack_recv_static(address, size)), (address, size))

    def test_invalid_widths_ranges_and_truncated_descriptors_are_rejected(self):
        for call in (lambda: pack_send_static(1 << 39, 0, 0),
                     lambda: pack_send_static(1 << 36, 0, 0, address_bits=36),
                     lambda: pack_send_static(0, 1 << 16, 0),
                     lambda: pack_send_static(0, 0, 64),
                     lambda: pack_buffer(0, 1 << 36, 0),
                     lambda: pack_buffer(0, 0, 4),
                     lambda: pack_buffer(-1, 0, 0),
                     lambda: pack_buffer(0, 0, 0, address_bits=59),
                     lambda: pack_recv_static(0, -1),
                     lambda: unpack_send_static(bytes(7)),
                     lambda: unpack_buffer(bytes(13)),
                     lambda: unpack_recv_static(bytes(9))):
            with self.assertRaises(ValueError):
                call()


def synthetic_image(module, rewritten=False):
    sites = [site for site in INLINE_SITES if site.module == module]
    size = max((site.offset + 0x100 for site in sites), default=0x100)
    if module == 'sdk' and rewritten:
        size = 0xb1000
    image = bytearray(size)
    for site in sites:
        image[site.offset - 8:site.offset + 12] = bytes.fromhex(site.context)
    if module == 'sdk' and rewritten:
        # Pinned generic rewriter site_plain, S=x0, T=x1, K18 index=0.
        site, target, key = 0x93af4, 0xa0000, 0x98000
        delta = (key & ~0xfff) - (target + 8 & ~0xfff)
        imm = delta >> 12
        adrp = 0x90000000 | (imm & 3) << 29 | ((imm >> 2) & 0x7ffff) << 5
        words = [0xa93f07e0, 0xd53bd061, adrp, 0xf9400000, 0x8b000021,
                 0xf9400020, 0x33002d20, 0xf9000020, 0xa97f07e0,
                 0x14000000 | (((site + 4 - (target + 36)) // 4) & 0x3ffffff)]
        struct.pack_into('<10I', image, target, *words)
        struct.pack_into('<I', image, site, 0x14000000 | ((target - site) // 4))
    return bytes(image)


class InlineAuditTests(unittest.TestCase):
    def test_apply_and_independent_verify_recover_only_the_known_base_prefix(self):
        for module in ('sdk', 'subsdk0', 'subsdk1', 'main'):
            source = synthetic_image(module)
            generic = synthetic_image(module, rewritten=True)
            report = {'generic_site_offsets': [0x93af4] if module == 'sdk' else []}
            patched, manifest = apply(module, source, generic, report)
            original_manifest = json.loads(json.dumps(manifest))
            result = verify(patched, original_manifest, expected_input_sha256=hashlib.sha256(generic).hexdigest())
            self.assertEqual(result['inline_patch_count'], len(manifest['inline_patches']))
            with self.assertRaisesRegex(ValueError, 'base image hash'):
                verify(patched, original_manifest, expected_input_sha256='0' * 64)
            changed = bytearray(patched)
            changed[0] ^= 1
            damaged = dict(original_manifest, output_image_sha256=hashlib.sha256(changed).hexdigest())
            with self.assertRaisesRegex(ValueError, 'base image hash'):
                verify(changed, damaged)
            if manifest['inline_patches']:
                changed = bytearray(patched)
                changed[-1] ^= 1
                damaged = dict(original_manifest, output_image_sha256=hashlib.sha256(changed).hexdigest())
                with self.assertRaisesRegex(ValueError, 'independently rebuilt'):
                    verify(changed, damaged)
            if module == 'sdk':
                self.assertEqual(patched[0x93af4:0x93af8], generic[0x93af4:0x93af8])
                with self.assertRaisesRegex(ValueError, 'coverage'):
                    apply(module, source, generic, {'generic_site_offsets': []})

    def test_fixed_manifest_and_original_contexts_are_complete(self):
        self.assertEqual(len(INLINE_SITES), 36)
        self.assertEqual({m: sum(s.module == m for s in INLINE_SITES)
                          for m in ('sdk', 'subsdk0', 'subsdk1')},
                         {'sdk': 12, 'subsdk0': 16, 'subsdk1': 8})
        for module in ('sdk', 'subsdk0', 'subsdk1'):
            image = synthetic_image(module)
            self.assertEqual(len(audit_original_inline_sites(module, image)),
                             sum(s.module == module for s in INLINE_SITES))
            mutated = bytearray(image)
            site = next(s for s in INLINE_SITES if s.module == module)
            mutated[site.offset - 4] ^= 1
            with self.assertRaisesRegex(ValueError, 'context'):
                audit_original_inline_sites(module, mutated)

    def test_patch_is_transactional_and_rejects_unknown_or_unvirtualized_x18(self):
        original = synthetic_image('sdk')
        with self.assertRaisesRegex(ValueError, 'x18'):
            patch_inline_addresses('sdk', original)
        for module in ('sdk', 'subsdk0', 'subsdk1'):
            original = synthetic_image(module, rewritten=True)
            patched, records = patch_inline_addresses(module, original)
            self.assertEqual(len(records), sum(s.module == module for s in INLINE_SITES))
            self.assertGreater(len(patched), len(original))
            with self.assertRaises(ValueError):
                patch_inline_addresses(module, patched)
            damaged = bytearray(original)
            first = next(s for s in INLINE_SITES if s.module == module)
            damaged[first.offset] ^= 1
            before = bytes(damaged)
            with self.assertRaises(ValueError):
                patch_inline_addresses(module, damaged)
            self.assertEqual(bytes(damaged), before)
        with self.assertRaises(ValueError):
            patch_inline_addresses('unknown', bytes(256))


class PointerResult(C.Structure):
    _fields_ = [('address', C.c_uint64), ('size', C.c_uint64)]


class BufferResult(C.Structure):
    _fields_ = [('address', C.c_uint64), ('size', C.c_uint64),
                ('mode', C.c_uint32), ('reserved', C.c_uint32)]


class HostHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.source = Path(__file__).resolve().parents[1] / 'native/botw_host/hipc_address.c'
        cls.library = Path(cls.directory.name) / 'hipc.dylib'
        subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', str(cls.source), '-o', str(cls.library)], check=True)
        cls.lib = C.CDLL(str(cls.library))
        for name in ('botw_hipc_set_pointer', 'botw_hipc_set_receive_list'):
            getattr(cls.lib, name).argtypes = [C.c_void_p, C.c_int, C.c_uint64, C.c_uint64] + ([C.c_int] if name.endswith('_pointer') else [])
        cls.lib.botw_hipc_get_pointer.argtypes = [C.c_void_p, C.c_int]
        cls.lib.botw_hipc_get_pointer.restype = PointerResult
        for name in ('botw_hipc_get_send', 'botw_hipc_get_receive'):
            getattr(cls.lib, name).argtypes = [C.c_void_p, C.c_int]
            getattr(cls.lib, name).restype = BufferResult
        cls.lib.botw_hipc_address_resolve.argtypes = [C.c_char_p]
        cls.lib.botw_hipc_address_resolve.restype = C.c_size_t
        cls.lib.botw_hipc_address_audit_image.argtypes = [C.c_char_p, C.c_void_p, C.c_size_t, C.c_size_t, C.c_size_t, C.c_char_p, C.c_size_t]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_actual_image_audit_rejects_missing_transport_or_corrupt_code(self):
        error = C.create_string_buffer(256)
        def audit(module, image, text, patch):
            buffer = C.create_string_buffer(bytes(image), len(image))
            return self.lib.botw_hipc_address_audit_image(module.encode(), buffer, len(image),
                                                        text, patch, error, len(error))
        for module in ('sdk', 'subsdk0', 'subsdk1'):
            source = synthetic_image(module, rewritten=True)
            text = 0x94000 if module == 'sdk' else 0x4e000 if module == 'subsdk0' else 0xc000
            patch = 0xa0000 if module == 'sdk' else 0x50000 if module == 'subsdk0' else 0x10000
            generic = source.ljust(max(len(source), patch + 0x4000), b'\0')
            updated, records = patch_inline_addresses(module, generic)
            self.assertEqual(audit(module, updated, text, patch), 0, error.value)
            self.assertEqual(audit(module, generic, text, patch), -1)
            self.assertIn(b'Host39 HIPC', error.value)
            for row in records:
                for offset in (row['offset'], row['thunk_offset'] + 4, row['thunk_offset'] + 20):
                    changed = bytearray(updated)
                    changed[offset + 3] ^= 1
                    self.assertEqual(audit(module, changed, text, patch), -1,
                                     (module, hex(offset), error.value))
            if module == 'sdk':
                word, = struct.unpack_from('<I', generic, 0x93af4)
                immediate = word & 0x3ffffff
                if immediate & 0x2000000:
                    immediate -= 0x4000000
                block = 0x93af4 + immediate * 4
                for i in range(10):
                    changed = bytearray(updated)
                    changed[block + i * 4 + 3] ^= 1
                    self.assertEqual(audit(module, changed, text, patch), -1,
                                     ('nested x18', i, error.value))
                # Preserve the ADRP opcode but point away from the actual key
                # page; a structurally valid outer thunk is insufficient.
                changed = bytearray(updated)
                changed[block + 11] ^= 0x20
                self.assertEqual(audit(module, changed, text, patch), -1)
            self.assertEqual(audit(module, updated, text, len(updated)), -1)
            self.assertEqual(audit(module, updated, 0x100, patch), -1)
        unchanged = bytes(0x200)
        for module in ('main', 'rtld', 'subsdk2', 'subsdk3'):
            self.assertEqual(audit(module, unchanged, 0x80, 0x100), 0, error.value)
        self.assertEqual(audit('unknown', unchanged, 0x80, 0x100), -1)
        self.assertEqual(audit('main', unchanged, 0x101, 0x100), -1)

    def test_only_the_five_proven_symbols_are_overridden(self):
        for path in HELPER_PATHS:
            self.assertEqual(self.lib.botw_hipc_address_resolve(path['name'].encode()),
                             C.cast(getattr(self.lib, path['host']), C.c_void_p).value)
        for name in (None, b'', b'memset', b'_ZN2nn2sf4hipc6detail25HipcMessageBufferAccessor7SetSendEimmj'):
            self.assertEqual(self.lib.botw_hipc_address_resolve(name), 0)

    def test_real_c_helpers_use_protocol_fields_and_indexed_accessor_offsets(self):
        accessor = C.create_string_buffer(0x70)
        static = C.create_string_buffer(bytes([0xa5]) * 24, 24)
        send = C.create_string_buffer(bytes([0x5a]) * 36, 36)
        receive = C.create_string_buffer(bytes([0x3c]) * 36, 36)
        recv_list = C.create_string_buffer(bytes([0x69]) * 24, 24)
        for offset, buffer in ((0x40, static), (0x48, send), (0x50, receive), (0x68, recv_list)):
            struct.pack_into('<Q', accessor, offset, C.addressof(buffer))
        for address in (0, (1 << 36) - 1, 1 << 36, (1 << 39) - 1, (1 << 42) - 1):
            for index in (0, 15, 16, 63):
                self.lib.botw_hipc_set_pointer(accessor, 1, address, 0xfedc, index)
                self.assertEqual(bytes(static)[8:16], pack_send_static(address, 0xfedc, index, address_bits=42))
                result = self.lib.botw_hipc_get_pointer(accessor, 1)
                self.assertEqual((result.address, result.size), (address, 0xfedc))
                self.assertEqual(bytes(static)[:8] + bytes(static)[16:], bytes([0xa5]) * 16)
        for address in (0, (1 << 36) - 1, 1 << 36, (1 << 39) - 1, (1 << 58) - 1):
            for mode in range(4):
                for buffer, function, size in ((send, self.lib.botw_hipc_get_send, 0xa12345678),
                                               (receive, self.lib.botw_hipc_get_receive, 0x1fedcba98)):
                    C.memmove(C.addressof(buffer) + 12, pack_buffer(address, size, mode, address_bits=58), 12)
                    result = function(accessor, 1)
                    self.assertEqual((result.address, result.size, result.mode, result.reserved),
                                     (address, size, mode, 0))
        for address in (0, (1 << 36) - 1, 1 << 36, (1 << 39) - 1, (1 << 48) - 1):
            self.lib.botw_hipc_set_receive_list(accessor, 1, address, 0xabcd)
            self.assertEqual(bytes(recv_list)[8:16], pack_recv_static(address, 0xabcd, address_bits=48))
            self.assertEqual(bytes(recv_list)[:8] + bytes(recv_list)[16:], bytes([0x69]) * 16)


@unittest.skipUnless(importlib.util.find_spec('unicorn'), 'Install requirements.botw.txt for ARM64 CPU tests')
class Arm64InlineTests(unittest.TestCase):
    def test_compiled_host_helpers_obey_actual_aapcs64_argument_and_result_abi(self):
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
        from unicorn import arm64_const as arm
        source = Path(__file__).resolve().parents[1] / 'native/botw_host/hipc_address.c'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'helpers.o'
            subprocess.run([shutil.which('clang') or 'clang', '-target', 'aarch64-none-elf',
                            '-ffreestanding', '-ffixed-x18', '-fno-stack-protector', '-O2',
                            '-std=c11', '-Wall', '-Wextra', '-Werror', '-c', str(source), '-o', str(path)], check=True)
            elf = path.read_bytes()
        self.assertEqual(elf[:6], b'\x7fELF\x02\x01')
        section_offset, = struct.unpack_from('<Q', elf, 0x28)
        section_size, section_count, names_index = struct.unpack_from('<HHH', elf, 0x3a)
        sections = [struct.unpack_from('<IIQQQQIIQQ', elf, section_offset + i * section_size)
                    for i in range(section_count)]
        def section_bytes(section):
            return elf[section[4]:section[4] + section[5]]
        names = section_bytes(sections[names_index])
        def name_at(table, offset):
            return table[offset:table.index(0, offset)].decode()
        text_index = next(i for i, sec in enumerate(sections) if name_at(names, sec[0]) == '.text')
        text = section_bytes(sections[text_index])
        symbols = {}
        for section in sections:
            if section[1] != 2:
                continue
            strings = section_bytes(sections[section[6]])
            for offset in range(section[4], section[4] + section[5], section[9]):
                name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', elf, offset)
                if index == text_index:
                    symbols[name_at(strings, name)] = (value, size)
        # These five leaf helpers need no linker relocation; refuse to silently
        # emulate unlinked code if compiler output changes to external calls.
        for section in sections:
            if section[1] == 4 and section[7] == text_index:
                for offset in range(section[4], section[4] + section[5], section[9]):
                    target, = struct.unpack_from('<Q', elf, offset)
                    self.assertFalse(any(value <= target < value + size for name, (value, size) in symbols.items()
                                         if name in {path['host'] for path in HELPER_PATHS}))
        register_ids = [getattr(arm, 'UC_ARM64_REG_X' + str(i)) for i in range(31)]
        checked = 0
        for helper in HELPER_PATHS:
            for address in (0, (1 << 36) - 1, 1 << 36, 1 << 38, (1 << 39) - 1,
                            (1 << (58 if helper['host'] in ('botw_hipc_get_send', 'botw_hipc_get_receive') else
                                   48 if helper['host'] == 'botw_hipc_set_receive_list' else 42)) - 1):
                cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                code, data, stack = 0x1000000, 0x5000000, 0x6000000
                cpu.mem_map(code, (len(text) + 0x1fff) & ~0xfff)
                cpu.mem_write(code, text)
                cpu.mem_map(data, 0x1000)
                cpu.mem_map(stack, 0x1000)
                for i, reg in enumerate(register_ids):
                    cpu.reg_write(reg, 0xfeed000000000000 + i)
                stop = code + len(text) + 0x100
                cpu.reg_write(arm.UC_ARM64_REG_LR, stop)
                cpu.reg_write(arm.UC_ARM64_REG_SP, stack + 0x800)
                cpu.reg_write(arm.UC_ARM64_REG_X0, data)
                cpu.reg_write(arm.UC_ARM64_REG_X1, 1)
                cpu.reg_write(arm.UC_ARM64_REG_X2, address)
                cpu.reg_write(arm.UC_ARM64_REG_X3, 0xfedc)
                cpu.reg_write(arm.UC_ARM64_REG_X4, 63)
                cpu.reg_write(arm.UC_ARM64_REG_X8, data + 0x300)
                cpu.reg_write(arm.UC_ARM64_REG_NZCV, 0xa0000000)
                cpu.mem_write(data, bytes([0xa5]) * 0x400)
                for field, pointer in ((0x40, data + 0x100), (0x48, data + 0x140),
                                       (0x50, data + 0x180), (0x68, data + 0x1c0)):
                    cpu.mem_write(data + field, struct.pack('<Q', pointer))
                cpu.mem_write(data + 0x108, pack_send_static(address & ((1 << 42) - 1), 0xfedc, 63, address_bits=42))
                for field in (0x14c, 0x18c):
                    cpu.mem_write(data + field, pack_buffer(address, 0xabcdef123, 3, address_bits=58))
                before = bytes(cpu.mem_read(data, 0x400))
                value, size = symbols[helper['host']]
                cpu.emu_start(code + value, stop, count=1000)
                self.assertEqual(cpu.reg_read(arm.UC_ARM64_REG_PC), stop)
                for index in range(18, 30):
                    self.assertEqual(cpu.reg_read(register_ids[index]), 0xfeed000000000000 + index)
                self.assertEqual(cpu.reg_read(arm.UC_ARM64_REG_SP), stack + 0x800)
                self.assertEqual(cpu.reg_read(arm.UC_ARM64_REG_NZCV), 0xa0000000)
                expected_memory = bytearray(before)
                if helper['host'] == 'botw_hipc_set_pointer':
                    expected_memory[0x108:0x110] = pack_send_static(address, 0xfedc, 63, address_bits=42)
                elif helper['host'] == 'botw_hipc_get_pointer':
                    self.assertEqual((cpu.reg_read(arm.UC_ARM64_REG_X0), cpu.reg_read(arm.UC_ARM64_REG_X1)),
                                     (address, 0xfedc))
                elif helper['host'] == 'botw_hipc_set_receive_list':
                    expected_memory[0x1c8:0x1d0] = pack_recv_static(address, 0xfedc, address_bits=48)
                else:
                    expected_memory[0x300:0x318] = struct.pack('<QQII', address, 0xabcdef123, 3, 0)
                self.assertEqual(bytes(cpu.mem_read(data, 0x400)), bytes(expected_memory))
                checked += 1
        self.assertEqual(checked, 30)

    def test_every_inline_thunk_preserves_cpu_state_and_protocol_fields(self):
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn import arm64_const as arm
        registers = [getattr(arm, 'UC_ARM64_REG_X' + str(i)) for i in range(31)]
        rng = random.Random(39)
        checked = 0
        for module in ('sdk', 'subsdk0', 'subsdk1'):
            image = synthetic_image(module, rewritten=True)
            patched, _ = patch_inline_addresses(module, image)
            for site in (s for s in INLINE_SITES if s.module == module):
                for address in (0, (1 << 32) - 1, (1 << 36) - 1, 1 << 36,
                                1 << 38, (1 << 39) - 1, (1 << 42) - 1):
                    initial = [rng.randrange(1 << 64) for _ in registers]
                    source = address
                    if site.kind == 'decode_buffer_upper':
                        source = struct.unpack('<III', pack_buffer(address, 0xa12345678, 3, address_bits=58))[2]
                    elif site.kind == 'decode_static':
                        source = int.from_bytes(pack_send_static(address, 0xfedc, 37, address_bits=42), 'little')
                    initial[site.source] = source
                    base, stack, tls = 0x1000000, 0x3000000, 0x4000000

                    def cpu(code):
                        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                        uc.mem_map(base, (len(code) + 0xfff) & ~0xfff)
                        uc.mem_write(base, code)
                        uc.mem_map(stack, 0x2000)
                        uc.mem_map(tls, 0x1000)
                        for reg, value in zip(registers, initial):
                            uc.reg_write(reg, value)
                        uc.reg_write(arm.UC_ARM64_REG_SP, stack + 0x1000)
                        uc.reg_write(arm.UC_ARM64_REG_NZCV, 0xa0000000)
                        uc.reg_write(arm.UC_ARM64_REG_TPIDRRO_EL0, tls)
                        return uc

                    if site.kind == 'pack_static_virtual_x18':
                        initial[9] = 37
                        original_header = address >> 20
                        expected = original_header & ~0xfff | 37
                        expected = expected & ~(63 << 6) | ((address >> 36) & 63) << 6
                        expected &= 0xffffffff
                        uc = cpu(patched)
                        uc.mem_write(base + 0x98000, struct.pack('<Q', 0x100))
                        uc.mem_write(tls + 0x100, struct.pack('<Q', original_header))
                        expected_registers = initial
                    else:
                        # Obtain effects of the exact original instruction from
                        # the CPU, then independently overlay only its address field.
                        original = bytearray(image)
                        original[site.offset:site.offset + 4] = bytes.fromhex(site.original)
                        before = cpu(bytes(original))
                        before.emu_start(base + site.offset, base + site.offset + 4, count=1)
                        expected_registers = [before.reg_read(reg) for reg in registers]
                        value = expected_registers[site.target]
                        if site.kind == 'pack_buffer':
                            value = value & ~(0x3fffff << 2) | ((address >> 36) & 0x3fffff) << 2
                        elif site.kind == 'pack_static':
                            value = value & ~(63 << 6) | ((address >> 36) & 63) << 6
                        elif site.kind == 'decode_buffer_upper':
                            value = value & ~(0x3fffff << 4) | ((source >> 2) & 0x3fffff) << 4
                        else:
                            value = value & ~(63 << 36) | ((source >> 6) & 63) << 36
                        expected_registers[site.target] = value
                        uc = cpu(patched)
                    stop = base + site.offset + 4
                    uc.hook_add(UC_HOOK_CODE, lambda c, a, z, _: c.emu_stop() if a == stop else None)
                    uc.emu_start(base + site.offset, base + len(patched), count=100)
                    self.assertEqual(uc.reg_read(arm.UC_ARM64_REG_PC), stop)
                    self.assertEqual([uc.reg_read(reg) for reg in registers], expected_registers,
                                     (module, hex(site.offset), site.kind, hex(address)))
                    self.assertEqual(uc.reg_read(arm.UC_ARM64_REG_SP), stack + 0x1000)
                    self.assertEqual(uc.reg_read(arm.UC_ARM64_REG_NZCV), 0xa0000000)
                    if site.kind == 'pack_static_virtual_x18':
                        self.assertEqual(struct.unpack('<Q', uc.mem_read(tls + 0x100, 8))[0], expected)
                    checked += 1
        self.assertEqual(checked, 252)


if __name__ == '__main__':
    unittest.main()
