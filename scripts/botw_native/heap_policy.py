"""Explicit base-v0 heap parameters using the pinned rewriter's TSD contract.

The two bounded thunks reuse patch_nso.py's PARAMS register-save/key-page/TSD
load pattern (MIT, e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8). They replace
only the audited startup heap and root heap instructions. The caller must
verify the complete original NSO set against botw-switch-base-v0.json first.
No SVC, GetInfo value, original module or runtime writable code is modified.
"""
import hashlib
import json
import struct

UNIT = 2 * 1024**2
STARTUP_BLOCK = 32 * 1024**2
HOST_RESERVE = 256 * 1024**2
# This is an explicit experimental execution guard, not a claim about the
# complete game's minimum working set. The observed fixed graphics child is
# 48 MiB; later resource/subheap allocation still requires actual execution.
MIN_ROOT = 512 * 1024**2
ORIGINAL_HEAP = 0xc2600000
ORIGINAL_ROOT = 0xc0600000
POLICY = {'schema': 1, 'profile': 'botw-switch-base-v0-bounded-heap-v1',
          'host_reserve_bytes': HOST_RESERVE, 'minimum_root_bytes': MIN_ROOT,
          'startup_block_bytes': STARTUP_BLOCK, 'alignment_bytes': UNIT,
          'original_heap_bytes': ORIGINAL_HEAP, 'original_root_bytes': ORIGINAL_ROOT,
          'game_playable': False}
SITES = (
    {'offset': 0x735304, 'register': 0, 'slot': 'KRES0',
     'context': 'fd030091ff4300d1004cb85222f43b94e0230091'},
    {'offset': 0x7353b0, 'register': 8, 'slot': 'KRES1',
     'context': 'e0430091a2230b94080cb852e0430091e80b00f9'},
)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _uint(value, label):
    if type(value) is not int or not 0 <= value < 1 << 64:
        raise ValueError(label + ' must be an unsigned 64-bit integer')
    return value


def plan(kernel_available, live_available):
    """Freeze one consistent heap/root pair within two actual host limits."""
    _uint(kernel_available, 'kernel_available'); _uint(live_available, 'live_available')
    if live_available < HOST_RESERVE:
        raise ValueError('Live host memory is below the experimental host reserve')
    heap = min(ORIGINAL_HEAP, kernel_available, live_available - HOST_RESERVE) & -UNIT
    if heap < STARTUP_BLOCK + MIN_ROOT:
        raise ValueError('Host memory cannot fit the guarded experimental root heap')
    return heap, heap - STARTUP_BLOCK


def _layout(layout, image_bytes):
    if not isinstance(layout, dict):
        raise ValueError('Heap adaptation requires a verified key-page layout')
    names = ('KEY_PAGE_OFF', 'PATCH_OFF', 'KEY_KRES0_IDX', 'KEY_KRES1_IDX')
    values = {key: layout.get(key) for key in names}
    if any(type(v) is not int or v < 0 for v in values.values()):
        raise ValueError('Invalid heap parameter layout integer')
    key, patch = values['KEY_PAGE_OFF'], values['PATCH_OFF']
    if (key % 0x4000 or patch % 0x4000 or key < SITES[-1]['offset'] + 12 or
            key + 0x4000 > patch or patch >= image_bytes or image_bytes % 4 or
            values['KEY_KRES0_IDX'] != 4 or values['KEY_KRES1_IDX'] != 5):
        raise ValueError('Unsupported heap parameter key-page layout')
    return values


def _branch(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < 1 << 27:
        raise ValueError('Heap parameter branch exceeds aligned ARM64 range')
    return 0x14000000 | ((delta >> 2) & 0x3ffffff)


def _thunk(site, start, layout):
    reg, temporary = site['register'], 16
    key_pc = start + 8
    delta = (layout['KEY_PAGE_OFF'] >> 12) - (key_pc >> 12)
    if not -(1 << 20) <= delta < 1 << 20:
        raise ValueError('Heap parameter ADRP exceeds its signed page range')
    immediate = delta & 0x1fffff
    adrp = 0x90000000 | (immediate & 3) << 29 | (immediate >> 2) << 5 | temporary
    index = layout['KEY_' + site['slot'] + '_IDX']
    return struct.pack('<8I',
        0xf8000000 | ((-16 & 0x1ff) << 12) | 31 << 5 | temporary,
        0xd53bd060 | reg, adrp,
        0xf9400000 | index << 10 | temporary << 5 | temporary,
        0x8b000000 | temporary << 16 | reg << 5 | reg,
        0xf9400000 | reg << 5 | reg,
        0xf8400000 | ((-16 & 0x1ff) << 12) | 31 << 5 | temporary,
        _branch(start + 28, site['offset'] + 4))


def _contexts(image):
    for site in SITES:
        offset = site['offset']
        if image[offset - 8:offset + 12] != bytes.fromhex(site['context']):
            raise ValueError(f'Heap original instruction context mismatch: main+0x{offset:x}')


def apply(original_image, rewritten_image, layout, *, generic_site_offsets):
    """Pure postprocessing of an already verified base-v0 Host39 main image."""
    if not isinstance(original_image, bytes) or not isinstance(rewritten_image, bytes):
        raise ValueError('Heap images must be immutable byte snapshots')
    _contexts(original_image); _contexts(rewritten_image)
    layout = _layout(layout, len(rewritten_image))
    if (not isinstance(generic_site_offsets, (tuple, list, set)) or
            any(type(value) is not int or value < 0 or value % 4 for value in generic_site_offsets) or
            any(site['offset'] in generic_site_offsets for site in SITES)):
        raise ValueError('Heap parameter sites overlap or invalidate the generic rewrite')
    result = bytearray(rewritten_image); records = []
    for site in SITES:
        start = len(result); thunk = _thunk(site, start, layout)
        branch = struct.pack('<I', _branch(site['offset'], start))
        result.extend(thunk); result[site['offset']:site['offset'] + 4] = branch
        records.append({**site, 'thunk_offset': start, 'thunk_bytes': thunk.hex(), 'branch_bytes': branch.hex()})
    manifest = {'schema': 1, 'profile': POLICY['profile'], 'module': 'main',
                'source_image_sha256': _digest(original_image),
                'input_image_bytes': len(rewritten_image), 'output_image_bytes': len(result),
                'input_image_sha256': _digest(rewritten_image), 'output_image_sha256': _digest(result),
                'slots': {'heap': 'KRES0', 'root': 'KRES1'}, 'layout': layout, 'patches': records}
    return bytes(result), manifest


def verify(image, manifest, *, expected_input_sha256=None):
    """Reconstruct exact thunks and return the independently recovered input."""
    if not isinstance(image, bytes) or not isinstance(manifest, dict):
        raise ValueError('Invalid heap parameter image/manifest')
    size = manifest.get('input_image_bytes')
    if type(size) is not int or size <= 0 or size % 4 or len(image) != size + 64:
        raise ValueError('Invalid heap parameter image bounds')
    layout = _layout(manifest.get('layout'), size)
    recovered = bytearray(image[:size]); patches = []
    for index, site in enumerate(SITES):
        start = size + index * 32; thunk = _thunk(site, start, layout)
        branch = struct.pack('<I', _branch(site['offset'], start))
        if image[start:start + 32] != thunk or image[site['offset']:site['offset'] + 4] != branch:
            raise ValueError('Heap parameter executable branch/thunk differs from fixed policy')
        patches.append({**site, 'thunk_offset': start, 'thunk_bytes': thunk.hex(), 'branch_bytes': branch.hex()})
        recovered[site['offset']:site['offset'] + 4] = bytes.fromhex(site['context'])[8:12]
    _contexts(recovered)
    source_hash = manifest.get('source_image_sha256')
    if (not isinstance(source_hash, str) or len(source_hash) != 64 or
            any(c not in '0123456789abcdef' for c in source_hash)):
        raise ValueError('Invalid heap parameter original image digest')
    recovered = bytes(recovered)
    expected = {'schema': 1, 'profile': POLICY['profile'], 'module': 'main',
                'source_image_sha256': source_hash, 'input_image_bytes': size, 'output_image_bytes': len(image),
                'input_image_sha256': _digest(recovered), 'output_image_sha256': _digest(image),
                'slots': {'heap': 'KRES0', 'root': 'KRES1'}, 'layout': layout, 'patches': patches}
    if (json.dumps(manifest, sort_keys=True) != json.dumps(expected, sort_keys=True) or
            expected_input_sha256 is not None and expected_input_sha256 != expected['input_image_sha256']):
        raise ValueError('Heap parameter manifest or Host39 input digest differs')
    return recovered
