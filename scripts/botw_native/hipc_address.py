"""Audited HIPC address fields and fixed BOTW inline host39 transformations.

Public field reference:
https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/hipc.h

These independently implemented transforms leave protocol widths unchanged:
X=42 address bits, A/B/W=58, C=48; the allocator selects a 39-bit guest range.
Only the fixed original contexts below are supported, never a global constant
replacement. The five exported helper PLT paths require hipc_address.c as well.
No guest image is read or written by this module's pure functions.
"""
from dataclasses import dataclass
import hashlib
import struct

MODULES = ('main', 'rtld', 'sdk', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3')
PROTOCOL_WIDTHS = {'static': 42, 'buffer': 58, 'receive_list': 48}


def _uint(value, bits, label):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f'{label} must fit unsigned {bits} bits')
    return value


def _address(address, bits, maximum):
    if type(bits) is not int or not 1 <= bits <= maximum:
        raise ValueError(f'address_bits must be in 1..{maximum}')
    return _uint(address, bits, 'address')


def _descriptor(data, length):
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != length:
        raise ValueError(f'descriptor must contain exactly {length} bytes')
    return data


def pack_send_static(address, size, index, *, address_bits=39):
    _address(address, address_bits, 42)
    _uint(size, 16, 'size'); _uint(index, 6, 'index')
    first = index | ((address >> 36) & 63) << 6 | ((address >> 32) & 15) << 12 | size << 16
    return struct.pack('<II', first, address & 0xffffffff)


def unpack_send_static(data):
    first, low = struct.unpack('<II', _descriptor(data, 8))
    return (low | ((first >> 12) & 15) << 32 | ((first >> 6) & 63) << 36,
            first >> 16, first & 63)


def pack_buffer(address, size, mode, *, address_bits=39):
    _address(address, address_bits, 58)
    _uint(size, 36, 'size'); _uint(mode, 2, 'mode')
    third = mode | ((address >> 36) & 0x3fffff) << 2 | (size >> 32) << 24 | ((address >> 32) & 15) << 28
    return struct.pack('<III', size & 0xffffffff, address & 0xffffffff, third)


def unpack_buffer(data):
    low_size, low_address, third = struct.unpack('<III', _descriptor(data, 12))
    return (low_address | (third >> 28) << 32 | ((third >> 2) & 0x3fffff) << 36,
            low_size | ((third >> 24) & 15) << 32, third & 3)


def pack_recv_static(address, size, *, address_bits=39):
    _address(address, address_bits, 48); _uint(size, 16, 'size')
    return struct.pack('<II', address & 0xffffffff, (address >> 32) | size << 16)


def unpack_recv_static(data):
    low, high = struct.unpack('<II', _descriptor(data, 8))
    return low | (high & 0xffff) << 32, high >> 16


@dataclass(frozen=True)
class InlineSite:
    module: str
    offset: int
    kind: str
    source: int
    target: int
    original: str
    context: str  # Original instructions at offset-8 through offset+11.
    function: str


INLINE_SITES = (
    InlineSite('sdk', 0x43958, 'pack_buffer', 0, 27,
               '7b0f0412', '1bfc44d35a0f08127b0f0412386440a95a031b2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x439e8, 'pack_buffer', 0, 27,
               '7b0f0412', '1bfc44d35a0f08127b0f0412386440a95a031b2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x43a5c, 'pack_static', 0, 22,
               'f602162a', '16fc54d3d60e1412f602162a376040a9f71e40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x43aec, 'pack_buffer', 23, 0,
               '000c0412', '3a0440f97b0f0812000c0412397f60d300001b2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x43b90, 'pack_buffer', 23, 0,
               '000c0412', '390440f95a0f0812000c0412187f60d300001a2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x43e3c, 'pack_static', 6, 7,
               'a73c1033', 'e700002a00f87fd3a73c1033254c40a9a51c40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x43f10, 'pack_buffer', 14, 4,
               '840c0412', 'c4fd44d3000c0812840c04120000042a247e7e93', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x43f94, 'pack_buffer', 14, 4,
               '840c0412', 'c4fd44d3000c0812840c04120000042a247e7e93', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('sdk', 0x936cc, 'decode_buffer_upper', 4, 4,
               '847c1c53', '86040012876c1853847c1c53657c40928380c393', 'Hipc2ServerMessage::GetBuffers'),
    InlineSite('sdk', 0x93718, 'decode_static', 3, 5,
               '637c50d3', '64fc4cd365fc60d3637c50d3850c60b3a400038b', 'Hipc2ServerMessage::GetBuffers'),
    InlineSite('sdk', 0x93768, 'decode_buffer_upper', 3, 7,
               '677c1c53', '640040f9630840b9677c1c53857c4092666c1853', 'Hipc2ServerMessage::GetBuffers'),
    InlineSite('sdk', 0x93af4, 'pack_static_virtual_x18', 16, 18,
               '322d0033', '710140b912fe54d3322d0033323e1033113440b9', 'Hipc2ServerMessage::SetBuffers'),
    InlineSite('subsdk0', 0xe5f8, 'pack_buffer', 9, 14,
               'ce0d0412', '2efd44d3ad0d0812ce0d04126b3340a9ad010e2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xe680, 'pack_buffer', 9, 14,
               'ce0d0412', '2efd44d3ad0d0812ce0d04126b3340a9ad010e2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xe6e4, 'pack_static', 8, 9,
               '6901092a', '09fd54d3290d14126901092a6a2f40a94a1d40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xe758, 'pack_buffer', 9, 14,
               'ce0d0412', '2efd44d3ad0d0812ce0d04126b3340a9cd010d2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xe7f0, 'pack_buffer', 9, 15,
               'ef0d0412', '2ffd44d3ce0d0812ef0d04126c3740a9ee010e2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xeab8, 'pack_static', 12, 13,
               '2d3d1033', 'ad0d14126d5d10332d3d1033690340f9291d40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xeb88, 'pack_buffer', 14, 1,
               '210c0412', 'c1fd44d3000c0812210c04120000012a1200122a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0xec04, 'pack_buffer', 14, 1,
               '210c0412', 'c1fd44d3000c0812210c04120000012a1200122a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 1)'),
    InlineSite('subsdk0', 0x4d908, 'pack_buffer', 9, 14,
               'ce0d0412', '2efd44d3ad0d0812ce0d04126b3340a9ad010e2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4d990, 'pack_buffer', 9, 14,
               'ce0d0412', '2efd44d3ad0d0812ce0d04126b3340a9ad010e2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4d9f4, 'pack_static', 8, 9,
               '6901092a', '09fd54d3290d14126901092a6a2f40a94a1d40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4da68, 'pack_buffer', 9, 14,
               'ce0d0412', '2efd44d3ad0d0812ce0d04126b3340a9cd010d2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4db00, 'pack_buffer', 9, 15,
               'ef0d0412', '2ffd44d3ce0d0812ef0d04126c3740a9ee010e2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4ddc8, 'pack_static', 12, 13,
               '2d3d1033', 'ad0d14126d5d10332d3d1033690340f9291d40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4de98, 'pack_buffer', 14, 1,
               '210c0412', 'c1fd44d3000c0812210c04120000012a1200122a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk0', 0x4df14, 'pack_buffer', 14, 1,
               '210c0412', 'c1fd44d3000c0812210c04120000012a1200122a', 'Hipc2ClientMessageBase::WriteBufferDataImpl (local copy 2)'),
    InlineSite('subsdk1', 0xb4d8, 'pack_buffer', 0, 27,
               '7b0f0412', '1bfc44d35a0f08127b0f0412386440a95a031b2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xb568, 'pack_buffer', 0, 27,
               '7b0f0412', '1bfc44d35a0f08127b0f0412386440a95a031b2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xb5dc, 'pack_static', 0, 22,
               'f602162a', '16fc54d3d60e1412f602162a376040a9f71e40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xb66c, 'pack_buffer', 23, 0,
               '000c0412', '3a0440f97b0f0812000c0412397f60d300001b2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xb710, 'pack_buffer', 23, 0,
               '000c0412', '390440f95a0f0812000c0412187f60d300001a2a', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xb9bc, 'pack_static', 6, 7,
               'a73c1033', 'e700002a00f87fd3a73c1033254c40a9a51c40b9', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xba90, 'pack_buffer', 14, 4,
               '840c0412', 'c4fd44d3000c0812840c04120000042a247e7e93', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
    InlineSite('subsdk1', 0xbb14, 'pack_buffer', 14, 4,
               '840c0412', 'c4fd44d3000c0812840c04120000042a247e7e93', 'Hipc2ClientMessageBase::WriteBufferDataImpl'),
)

# Full seven-module B/BL and ELF relocation scan: no direct helper branch or
# RELATIVE/addend reference. Only these five symbol-based SDK PLT paths exist.
HELPER_PATHS = (
    {'name': '_ZN2nn2sf4hipc6detail25HipcMessageBufferAccessor10SetPointerEimmi',
     'entry': 0x90f3c, 'plt': 0x34c314, 'calls': (0x92f9c,), 'host': 'botw_hipc_set_pointer'},
    {'name': '_ZNK2nn2sf4hipc6detail25HipcMessageBufferAccessor10GetPointerEi',
     'entry': 0x90f5c, 'plt': 0x34b5f4, 'calls': (0x92cc8,), 'host': 'botw_hipc_get_pointer'},
    {'name': '_ZNK2nn2sf4hipc6detail25HipcMessageBufferAccessor7GetSendEi',
     'entry': 0x90fcc, 'plt': 0x34e364, 'calls': (0x92ca8,), 'host': 'botw_hipc_get_send'},
    {'name': '_ZNK2nn2sf4hipc6detail25HipcMessageBufferAccessor10GetReceiveEi',
     'entry': 0x9103c, 'plt': 0x343674, 'calls': (0x92c84,), 'host': 'botw_hipc_get_receive'},
    {'name': '_ZN2nn2sf4hipc6detail25HipcMessageBufferAccessor14SetReceiveListEimm',
     'entry': 0x910e8, 'plt': 0x34e674, 'calls': (0x92214,), 'host': 'botw_hipc_set_receive_list'},
)
# Defined, but no call/relocation reference in the audited seven modules. Kept
# explicit so a new version cannot silently be called covered by this profile.
UNREFERENCED_HELPERS = {
    'GetPointerReceiveIndex': 0x90f7c, 'SetSend': 0x90f90,
    'SetReceive': 0x91000, 'SetExchange': 0x91070,
    'GetExchange': 0x910ac, 'GetReceiveList': 0x91104,
}
DIRECT_CLIENT_CALLS = {'subsdk0': {0xe3e0: (0xce60, 0xd09c), 0x4d6f0: (0x4d0ec,)}}


def _sites(module):
    if module not in MODULES:
        raise ValueError('Unknown module for fixed HIPC profile: ' + str(module))
    return tuple(site for site in INLINE_SITES if site.module == module)


def audit_original_inline_sites(module, image):
    """Independently check every original 20-byte context before any rewrite."""
    result = []
    for site in _sites(module):
        if image[site.offset - 8:site.offset + 12] != bytes.fromhex(site.context):
            raise ValueError(f'HIPC original context mismatch: {module}+0x{site.offset:x}')
        result.append({'offset': site.offset, 'kind': site.kind, 'function': site.function,
                       'original_bytes': site.original, 'original_context': site.context})
    return result


def _word(image, offset):
    if offset % 4 or offset < 0 or offset + 4 > len(image):
        raise ValueError('HIPC instruction offset is unaligned or out of bounds')
    return struct.unpack_from('<I', image, offset)[0]


def _branch(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < 1 << 27:
        raise ValueError('HIPC thunk branch exceeds aligned ARM64 B range')
    return 0x14000000 | ((delta >> 2) & 0x3ffffff)


def _branch_target(image, offset):
    value = _word(image, offset)
    if value & 0xfc000000 != 0x14000000:
        raise ValueError('HIPC virtual x18 site is not a generic rewrite branch')
    delta = value & 0x3ffffff
    if delta & 0x2000000:
        delta -= 0x4000000
    return offset + delta * 4


def _ubfx(target, source, lsb, width, *, wide=True):
    return (0xd3400000 if wide else 0x53000000) | lsb << 16 | (lsb + width - 1) << 10 | source << 5 | target


def _bfi(target, source, lsb, width, *, wide=True):
    bits = 64 if wide else 32
    return (0xb3400000 if wide else 0x33000000) | ((-lsb) % bits) << 16 | (width - 1) << 10 | source << 5 | target


def _pair(target, second, offset, *, load=False):
    return (0xa9400000 if load else 0xa9000000) | ((offset // 8) & 127) << 15 | second << 10 | 31 << 5 | target


def _virtual_x18_instruction(image, site):
    """Find the original rewriter's substituted w18 operation, never touch x18.

    The outer original site remains unchanged and tracked in generic sites.tsv.
    Validate all ten site_plain instructions before extending its inner BFXIL.
    K18 load/address construction and final store/restore remain original.
    """
    block = _branch_target(image, site.offset)
    words = [_word(image, block + i * 4) for i in range(10)]
    saved, temporary = words[0] & 31, words[0] >> 10 & 31
    if saved in (16, 18, 31) or temporary in (16, 18, 31) or saved == temporary:
        raise ValueError('Unsupported generic virtual x18 scratch registers')
    expected = {
        0: _pair(saved, temporary, -16),
        1: 0xd53bd060 | temporary,  # mrs temporary,tpidrro_el0
        4: 0x8b000000 | saved << 16 | temporary << 5 | temporary,
        5: 0xf9400000 | temporary << 5 | saved,
        6: 0x33002c00 | 9 << 5 | saved,  # bfxil wS,w9,#0,#12
        7: 0xf9000000 | temporary << 5 | saved,
        8: _pair(saved, temporary, -16, load=True),
        9: _branch(block + 36, site.offset + 4),
    }
    if any(words[index] != value for index, value in expected.items()):
        raise ValueError('HIPC generic x18 thunk structure differs from audited site_plain')
    # ADRP S and unsigned-immediate LDR S,[S,#key_offset]. Root's pinned
    # key-page audit validates the actual page/slot; retain both bytes unchanged.
    if words[2] & 0x9f00001f != 0x90000000 | saved or words[3] & 0xffc003ff != 0xf9400000 | saved << 5 | saved:
        raise ValueError('HIPC generic x18 key-page load structure differs')
    return block + 24, saved


def _thunk(site, original, address, resume, target, save_offset):
    # Preserve every architectural register except the intended original
    # destination, all flags and SP. Same private below-SP scratch convention
    # as the pinned generic rewriter; -32 avoids its already occupied -16 pair.
    used = {site.source, target, 18, 31}
    used.update((original >> shift) & 31 for shift in (0, 5, 10, 16))
    scratch = next(reg for reg in range(18) if reg not in used)
    kind = site.kind
    if kind == 'pack_buffer':
        extract = _ubfx(scratch, site.source, 36, 22)
        insert = _bfi(target, scratch, 2, 22, wide=False)
    elif kind in ('pack_static', 'pack_static_virtual_x18'):
        extract = _ubfx(scratch, site.source, 36, 6)
        insert = _bfi(target, scratch, 6, 6, wide=False)
    elif kind == 'decode_buffer_upper':
        extract = _ubfx(scratch, site.source, 2, 22)
        insert = _bfi(target, scratch, 4, 22, wide=False)
    elif kind == 'decode_static':
        extract = _ubfx(scratch, site.source, 6, 6)
        insert = _bfi(target, scratch, 36, 6)
    else:
        raise ValueError('Unknown HIPC inline transform: ' + kind)
    words = [_pair(scratch, 31, save_offset), extract, original, insert,
             _pair(scratch, 31, save_offset, load=True)]
    words.append(_branch(address + len(words) * 4, resume))
    return struct.pack('<6I', *words)


def patch_inline_addresses(module, image):
    """Apply fixed-site inline patches to an already generically rewritten image.

    Pure and transactional. The SDK x18 operation must already have the pinned
    generic thunk; an unrewritten x18 instruction or unknown thunk is rejected.
    No symbols are resolved here: host installs the five HELPER_PATHS separately.
    """
    sites = _sites(module)
    if len(image) % 4:
        raise ValueError('HIPC rewritten image must be instruction aligned')
    planned = []
    for site in sites:
        if site.kind == 'pack_static_virtual_x18':
            offset, target = _virtual_x18_instruction(image, site)
        else:
            offset, target = site.offset, site.target
            if image[offset:offset + 4] != bytes.fromhex(site.original):
                raise ValueError(f'HIPC rewritten instruction mismatch: {module}+0x{offset:x}')
        planned.append((site, offset, target, _word(image, offset)))
    result = bytearray(image)
    records = []
    for site, offset, target, original in planned:
        start = len(result)
        virtual = site.kind == 'pack_static_virtual_x18'
        payload = _thunk(site, original, start, offset + 4, target, -32 if virtual else -16)
        replacement = _branch(offset, start)
        result.extend(payload)
        struct.pack_into('<I', result, offset, replacement)
        records.append({'offset': site.offset, 'kind': site.kind, 'function': site.function,
                        'original_bytes': site.original, 'patched_offset': offset,
                        'patched_original_bytes': struct.pack('<I', original).hex(),
                        'branch_bytes': struct.pack('<I', replacement).hex(),
                        'thunk_offset': start, 'thunk_bytes': payload.hex(),
                        'generic_x18_site_kept': virtual})
    return bytes(result), records


def _generic_sites(report):
    if not isinstance(report, dict):
        raise ValueError('HIPC apply report must provide generic_site_offsets')
    values = report.get('generic_site_offsets', ())
    if not isinstance(values, (list, tuple, set)) or any(type(v) is not int or v < 0 or v % 4 for v in values):
        raise ValueError('HIPC generic_site_offsets must contain aligned integer offsets')
    return set(values)


def _host_paths(module):
    return [dict(path, calls=list(path['calls'])) for path in HELPER_PATHS] if module == 'sdk' else []


def apply(module_name, original_image, rewritten_image, report):
    """Return (updated_image, manifest), without mutating any input.

    report['generic_site_offsets'] is the audited sites.tsv offset list. An
    overlapping generic site is accepted only for the validated x18 operation;
    its original branch is preserved, so generic site verification still holds.
    Original bytes must be the NSO's decompressed initialized image. The caller
    also verifies its whole-source build ID/hash against the selected profile.
    """
    contexts = audit_original_inline_sites(module_name, original_image)
    generic = _generic_sites(report)
    for site in _sites(module_name):
        virtual = site.kind == 'pack_static_virtual_x18'
        if virtual != (site.offset in generic):
            raise ValueError(f'HIPC generic rewrite site coverage mismatch: {module_name}+0x{site.offset:x}')
    updated, records = patch_inline_addresses(module_name, rewritten_image)
    digest = lambda data: hashlib.sha256(data).hexdigest()
    return updated, {'schema': 1, 'module': module_name, 'target_address_bits': 39,
                     'protocol_address_bits': PROTOCOL_WIDTHS.copy(),
                     'input_image_bytes': len(rewritten_image), 'output_image_bytes': len(updated),
                     'source_image_sha256': digest(original_image),
                     'input_image_sha256': digest(rewritten_image),
                     'output_image_sha256': digest(updated),
                     'original_contexts': contexts, 'inline_patches': records,
                     'required_host_symbols': _host_paths(module_name),
                     'unreferenced_helpers': UNREFERENCED_HELPERS.copy() if module_name == 'sdk' else {}}


def verify(image, manifest, *, expected_input_sha256=None):
    """Independently reconstruct fixed patches and recover the original prefix.

    expected_input_sha256 may anchor the recovered bytes to the trusted generic
    rewrite report. The source-image digest is provenance metadata: the caller
    separately checks its source NSO hash/build ID against the version profile.
    JSON round-tripped manifests are accepted. No input is changed.
    """
    if not isinstance(manifest, dict) or manifest.get('schema') != 1:
        raise ValueError('Invalid HIPC address manifest schema')
    sites = _sites(manifest.get('module'))
    digest = lambda data: hashlib.sha256(data).hexdigest()
    contexts = [{'offset': site.offset, 'kind': site.kind, 'function': site.function,
                 'original_bytes': site.original, 'original_context': site.context} for site in sites]
    source_hash = manifest.get('source_image_sha256')
    if (not isinstance(source_hash, str) or len(source_hash) != 64 or
            any(char not in '0123456789abcdef' for char in source_hash) or
            manifest.get('original_contexts') != contexts or
            manifest.get('required_host_symbols') != _host_paths(manifest['module']) or
            manifest.get('unreferenced_helpers') != (UNREFERENCED_HELPERS if manifest['module'] == 'sdk' else {})):
        raise ValueError('HIPC fixed audit context or helper provenance differs')
    base_size = manifest.get('input_image_bytes')
    if (type(base_size) is not int or base_size < 0 or base_size % 4 or
            len(image) != base_size + 24 * len(sites) or manifest.get('output_image_bytes') != len(image) or
            manifest.get('target_address_bits') != 39 or manifest.get('protocol_address_bits') != PROTOCOL_WIDTHS):
        raise ValueError('HIPC manifest image layout or address widths differ')
    if digest(image) != manifest.get('output_image_sha256'):
        raise ValueError('HIPC output image hash mismatch')
    records = manifest.get('inline_patches')
    if not isinstance(records, list) or len(records) != len(sites):
        raise ValueError('HIPC manifest does not cover all fixed inline sites')
    restored = bytearray(image[:base_size])
    offsets = set()
    for site, record in zip(sites, records):
        if not isinstance(record, dict) or record.get('offset') != site.offset:
            raise ValueError('HIPC manifest fixed-site ordering differs')
        offset = record.get('patched_offset')
        if (type(offset) is not int or offset % 4 or not 0 <= offset <= base_size - 4 or offset in offsets or
                (site.kind != 'pack_static_virtual_x18' and offset != site.offset)):
            raise ValueError('HIPC manifest patch offset is not the fixed original site')
        offsets.add(offset)
        try:
            original = bytes.fromhex(record.get('patched_original_bytes', ''))
        except (TypeError, ValueError) as exc:
            raise ValueError('HIPC manifest has invalid original instruction') from exc
        if len(original) != 4:
            raise ValueError('HIPC manifest original instruction must contain four bytes')
        restored[offset:offset + 4] = original
    restored_hash = digest(restored)
    if restored_hash != manifest.get('input_image_sha256') or (expected_input_sha256 is not None and restored_hash != expected_input_sha256):
        raise ValueError('HIPC recovered base image hash mismatch')
    rebuilt, expected_records = patch_inline_addresses(manifest['module'], restored)
    if rebuilt != image or expected_records != records:
        raise ValueError('HIPC branch/thunk bytes differ from independently rebuilt fixed patches')
    return {'inline_patch_count': len(sites), 'recovered_input_sha256': restored_hash,
            'output_image_sha256': digest(image), 'generic_x18_sites_kept': [site.offset for site in sites if site.kind == 'pack_static_virtual_x18']}
