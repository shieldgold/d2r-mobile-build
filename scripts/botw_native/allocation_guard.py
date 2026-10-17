"""One explicit offline NULL-return guard; no code execution or file writes.

The public boundary accepts only the immutable, complete base-v0 v4 product.
The private byte core exists for owned, non-executable fixtures and is not an
alternate publication/admission API. It cannot certify a game image.
"""
import hashlib
import json
import re
import struct

SCHEMA = 'normal-allocation-guard-v1'
PROFILE = 'botw-switch-base-v0-pthread-stack-v4-normal-allocation-guard-v1'
INPUT_PROFILE = 'botw-switch-base-v0-pthread-stack-v4'
INPUT_BYTES = 37723336
INPUT_SHA256 = 'cba191507c56577c3b81d61f7c32c3873a3bcfd980537a43b806bf6ab38458f7'
FIXUPS_SHA256 = 'd16b439edde318861a04ac1e9641a273110fbcd890275d5863f0acc21f8d20f3'
GATES_SHA256 = '55fed8f4e6f224a6e1fc80daca03d76434c6c8c5ed96dc98e3bbedd1be94e857'
SITES_SHA256 = 'd071392b2c769a34cda1a529214c3b954fe6953c814d12084966327542da3461'
REWRITER_SHA256 = 'c9a0c7574547ab945e4648bd6664e86fa4a77de12253a55e7b464232b2cdf274'
SITE = 0xdb93a8
CONTEXT_START = 0xdb9380
CONTEXT = bytes.fromhex(
    '394741f928430091a8573aa9620240f9a18301d1e3031d32e4030032e0031faa'
    'e5031f2a663ff197f60300aa3f1a0c9448a600f0088940f9000140f9e10316aa'
    '861a0c94c80240f9081540f9e00316aa00013fd6e00315aa')
ORIGINAL = bytes.fromhex('f60300aa')  # Original MOV x22,x0, after factory BL.
KEY_PAGE = 0x2384000
PATCH_START = 0x2390000
TEXT_END = 0x1633b30
REFERENCE_GATE = 0x23e5d6c
REFERENCE_RETURN = REFERENCE_GATE + 32
REFERENCE_SITE = 0x140c9ec
REFERENCE_SHA256 = 'd6b09ef6fd96cf3795b17cc735fb91fd480c55bc312e75fbf710892583e239bf'
THUNK_BYTES = 52
POLICY = {
    'schema': 1, 'profile': PROFILE, 'input_profile': INPUT_PROFILE,
    'allocation_site': SITE, 'normal_hostcall': 128,
    'original_allocator': True, 'game_playable': False,
}

_FIXUPS = {
    'PATCH_OFF': PATCH_START, 'KEY_PAGE_OFF': KEY_PAGE, 'KEY_PAGE_SIZE': 0x4000,
    'TEXT_END': TEXT_END, 'RO_OFF': 0x1634000, 'DATA_OFF': 0x2068000,
    'KEY_K18_IDX': 0, 'KEY_KTLS_IDX': 1, 'KEY_KTLR_IDX': 2, 'KEY_KGATE_IDX': 3,
    'KEY_KRES0_IDX': 4, 'KEY_KRES1_IDX': 5, 'KEY_KASPECT_IDX': 6, 'KEY_KASPECTUI_IDX': 7,
}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _lines(value, kind):
    if type(value) is not bytes or not value or not value.endswith(b'\n') or b'\0' in value or b'\r' in value:
        raise ValueError('Invalid ' + kind + ' bytes')
    try:
        lines = value.decode('ascii').splitlines()
    except UnicodeError as exc:
        raise ValueError('Non-ASCII ' + kind) from exc
    if any(not line for line in lines) or len(lines) != len(set(lines)):
        raise ValueError('Empty or duplicate ' + kind + ' row')
    return lines


def _metadata(fixups, gates, sites):
    layout = {}
    for line in _lines(fixups, 'fixups'):
        match = re.fullmatch(r'# ([A-Z0-9_]+)\t(0x[0-9a-f]+|[0-9]+)', line)
        if not match or match[1] in layout:
            raise ValueError('Invalid fixup row')
        layout[match[1]] = int(match[2], 0)
    if layout != _FIXUPS:
        raise ValueError('Fixed v4 key/layout contract differs')
    gate_rows, returns, origins = [], set(), set()
    for line in _lines(gates, 'gates'):
        match = re.fullmatch(r'(0x[0-9a-f]+)\t([A-Za-z0-9_]{1,15})\t(-?[0-9]+)\t(0x[0-9a-f]+)', line)
        if not match:
            raise ValueError('Invalid normal gate row')
        ret, kind, argument, origin = int(match[1], 16), match[2], int(match[3]), int(match[4], 16)
        if (ret % 4 or not PATCH_START < ret < INPUT_BYTES or ret in returns or
                origin % 4 or not 0 <= origin < TEXT_END or origin in origins or
                not -(1 << 31) <= argument < (1 << 31)):
            raise ValueError('Invalid or colliding normal gate')
        if origin == SITE or (kind == 'svc' and argument == 128):
            raise ValueError('Normal allocation gate already claimed')
        returns.add(ret)
        origins.add(origin)
        gate_rows.append((ret, kind, argument, origin))
    if (REFERENCE_RETURN, 'svc', 38, REFERENCE_SITE) not in gate_rows:
        raise ValueError('Original complete normal SVC gate is absent')
    site_addresses = set()
    site_lines = _lines(sites, 'sites')
    for line in site_lines:
        fields = line.split('\t')
        if len(fields) < 2 or not re.fullmatch(r'0x[0-9a-f]+', fields[0]):
            raise ValueError('Invalid original rewrite site')
        address = int(fields[0], 16)
        if address % 4 or not 0 <= address < TEXT_END or address == SITE:
            raise ValueError('Colliding or out-of-bounds rewrite site')
        site_addresses.add(address)
    if REFERENCE_SITE not in site_addresses:
        raise ValueError('Original gate rewrite site is absent')
    return len(gate_rows), len(site_lines)


def _admit(image, fixups, gates, sites):
    if type(image) is not bytes or len(image) != INPUT_BYTES or _sha(image) != INPUT_SHA256:
        raise ValueError('Only the complete fixed base-v0 v4 image is admitted')
    for value, expected in [(fixups, FIXUPS_SHA256), (gates, GATES_SHA256), (sites, SITES_SHA256)]:
        if type(value) is not bytes or _sha(value) != expected:
            raise ValueError('Complete fixed v4 metadata identity differs')


def _branch(pc, target):
    delta = target - pc
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('Normal branch is out of range')
    return struct.pack('<I', 0x14000000 | ((delta >> 2) & 0x3ffffff))


def _adrp(register, pc, target):
    # Same bounded encoding as the pinned upstream ADRP encoder. The normal
    # gate instruction sequence itself is copied from its verified byte image.
    delta = ((target & ~0xfff) - (pc & ~0xfff)) >> 12
    if not -(1 << 20) <= delta < (1 << 20):
        raise ValueError('Normal key-page ADRP is out of range')
    return struct.pack('<I', 0x90000000 | ((delta & 3) << 29) |
                       (((delta >> 2) & 0x7ffff) << 5) | register)


def _thunk(image):
    start, gate = len(image), len(image) + 12
    template = image[REFERENCE_GATE:REFERENCE_GATE + 40]
    if _sha(template) != REFERENCE_SHA256:
        raise ValueError('Verified complete upstream KGATE template differs')
    # Exact upstream site_gate: save x29/x30; normal current KGATE lookup;
    # BLR; registered return at LDP; normal branch to original continuation.
    normal_gate = (template[:8] + _adrp(29, gate + 8, KEY_PAGE) + template[12:36] +
                   _branch(gate + 36, SITE + 4))
    cbz_x0_to_failure = struct.pack('<I', 0xb4000000 | (3 << 5))
    return cbz_x0_to_failure + ORIGINAL + _branch(start + 8, SITE + 4) + normal_gate


def _apply_admitted(image, fixups, gates, sites):
    """Internal pure byte transform; caller must first establish provenance.

    Tests call this only with their own non-executable structural tokens. A
    manifest from an owned image is rejected by the public verify boundary.
    """
    if (type(image) is not bytes or len(image) != INPUT_BYTES or len(image) % 4 or
            image[CONTEXT_START:CONTEXT_START + len(CONTEXT)] != CONTEXT or
            image[SITE:SITE + 4] != ORIGINAL or any(image[KEY_PAGE:KEY_PAGE + 0x4000])):
        raise ValueError('Incomplete or modified original allocation context')
    gate_count, site_count = _metadata(fixups, gates, sites)
    code = _thunk(image)
    if len(code) != THUNK_BYTES:
        raise ValueError('Incomplete normal allocation thunk')
    output = image[:SITE] + _branch(SITE, len(image)) + image[SITE + 4:] + code
    ret = len(image) + 44
    gate_row = f'0x{ret:x}\tsvc\t128\t0x{SITE:x}\n'.encode('ascii')
    site_row = f'0x{SITE:x}\t{SCHEMA}\n'.encode('ascii')
    new_gates, new_sites = gates + gate_row, sites + site_row
    manifest = {
        'schema': SCHEMA, 'profile': PROFILE, 'input_profile': INPUT_PROFILE,
        'policy': dict(POLICY),
        'module': 'main', 'game_playable': False,
        'input_image_bytes': len(image), 'input_image_sha256': _sha(image),
        'output_image_bytes': len(output), 'output_image_sha256': _sha(output),
        'fixups_sha256': _sha(fixups),
        'input_gates_sha256': _sha(gates), 'output_gates_sha256': _sha(new_gates),
        'input_sites_sha256': _sha(sites), 'output_sites_sha256': _sha(new_sites),
        'site': SITE, 'original': ORIGINAL.hex(), 'replacement': output[SITE:SITE + 4].hex(),
        'thunk_offset': len(image), 'thunk_bytes': code.hex(),
        'gate_entry': len(image) + 12, 'gate_return': ret, 'normal_hostcall': 128,
        'appended_gate_row': gate_row.decode('ascii'), 'appended_site_row': site_row.decode('ascii'),
        'input_gate_count': gate_count, 'output_gate_count': gate_count + 1,
        'input_site_count': site_count, 'output_site_count': site_count + 1,
        'normal_gate_source': {'pinned_rewriter_sha256': REWRITER_SHA256,
                               'reference_gate_offset': REFERENCE_GATE,
                               'reference_gate_sha256': REFERENCE_SHA256},
    }
    return output, new_gates, new_sites, manifest


def apply(image, fixups, gates, sites, *, enabled=False):
    """Return guarded bytes, gates, sites and manifest; never write/execute them."""
    if enabled is not True:
        raise ValueError('Normal allocation guard requires explicit opt-in')
    _admit(image, fixups, gates, sites)
    return _apply_admitted(image, fixups, gates, sites)


def _verify_admitted(image, fixups, gates, sites, manifest):
    if type(image) is not bytes or len(image) != INPUT_BYTES + THUNK_BYTES or type(manifest) is not dict:
        raise ValueError('Invalid guarded image/manifest')
    gate_row = f'0x{INPUT_BYTES + 44:x}\tsvc\t128\t0x{SITE:x}\n'.encode('ascii')
    site_row = f'0x{SITE:x}\t{SCHEMA}\n'.encode('ascii')
    if (type(gates) is not bytes or not gates.endswith(gate_row) or
            type(sites) is not bytes or not sites.endswith(site_row)):
        raise ValueError('Exact appended normal metadata is missing')
    old_gates, old_sites = gates[:-len(gate_row)], sites[:-len(site_row)]
    original = image[:SITE] + ORIGINAL + image[SITE + 4:INPUT_BYTES]
    expected_image, expected_gates, expected_sites, expected_manifest = _apply_admitted(
        original, fixups, old_gates, old_sites)
    try:
        actual = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False)
        expected = json.dumps(expected_manifest, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid manifest values') from exc
    if image != expected_image or gates != expected_gates or sites != expected_sites or actual != expected:
        raise ValueError('Guard code, complete byte identity or manifest differs')
    return original


def verify(image, fixups, gates, sites, manifest):
    """Recover and authenticate the complete original fixed v4 input bytes."""
    original = _verify_admitted(image, fixups, gates, sites, manifest)
    gate_row = manifest['appended_gate_row'].encode('ascii')
    site_row = manifest['appended_site_row'].encode('ascii')
    _admit(original, fixups, gates[:-len(gate_row)], sites[:-len(site_row)])
    return original
