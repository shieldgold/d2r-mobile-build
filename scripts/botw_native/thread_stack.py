"""Fixed base-v0 source-stack allocation alignment for native 16 KiB hosts.

Only the observed sead Thread allocation's alignment argument changes. Its
allocator, requested size, returned pointer and free path remain original.
The kernel still moves actual backing and refuses partial host-page mappings.
Apply last, after Host39 and optional bounded heap; undo in reverse order.
"""
import hashlib
import json
from pathlib import Path

POLICY = {'schema': 1, 'profile': 'botw-switch-base-v0-thread-stack-alignment-v1',
          'alignment_bytes': 0x4000, 'size_unchanged': True, 'original_allocator': True,
          'game_playable': False}
SITE = 0xa1d758
CONTEXT_OFFSET = SITE - 16
CONTEXT = bytes.fromhex('7bfa00b9735200f9a1590094177f4093e2031432e00317aae10316aa4057ff97607600f9')
ORIGINAL = bytes.fromhex('e2031432')  # mov w2,#0x1000
REPLACEMENT = bytes.fromhex('e2031232')  # mov w2,#0x4000, same non-flag-setting instruction form


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _contexts(image, *, adapted=False):
    expected = CONTEXT[:16] + (REPLACEMENT if adapted else ORIGINAL) + CONTEXT[20:]
    if image[CONTEXT_OFFSET:CONTEXT_OFFSET + len(CONTEXT)] != expected:
        raise ValueError('Thread stack instruction context differs or is truncated: main+0xa1d758')


def verify_source_modules(directory):
    """Verify all seven complete original NSO fingerprints before the opt-in."""
    from .rewrite import verify_profile
    profile = json.loads((Path(__file__).resolve().parents[2] / 'profiles/botw-switch-base-v0.json').read_text())
    modules = []
    for name in profile['modules']:
        path = Path(directory) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Thread stack policy requires all seven fixed original modules: ' + name)
        data = path.read_bytes()
        if len(data) < 0x100 or data[:4] != b'NSO0':
            raise ValueError('Thread stack policy requires a complete original NSO: ' + name)
        modules.append({'name': name, 'sha256': _digest(data), 'build_id': data[0x40:0x60].hex()})
    verify_profile(modules, profile)


def _manifest(original_digest, input_image, output_image):
    return {'schema': 1, 'profile': POLICY['profile'], 'module': 'main',
            'source_image_sha256': original_digest,
            'input_image_bytes': len(input_image), 'output_image_bytes': len(output_image),
            'input_image_sha256': _digest(input_image), 'output_image_sha256': _digest(output_image),
            'patches': [{'offset': SITE, 'context_offset': CONTEXT_OFFSET, 'context': CONTEXT.hex(),
                         'original': ORIGINAL.hex(), 'replacement': REPLACEMENT.hex(),
                         'argument_register': 'w2', 'original_alignment': 0x1000, 'host_alignment': 0x4000,
                         'allocator_call': 0xa1d764, 'create_thread_call': 0xa1d798}]}


def apply(original_image, rewritten_image, *, generic_site_offsets):
    """Pure transform; callers also verify the complete base-v0 source set."""
    if not isinstance(original_image, bytes) or not isinstance(rewritten_image, bytes):
        raise ValueError('Thread stack images must be immutable snapshots')
    _contexts(original_image); _contexts(rewritten_image)
    if (not isinstance(generic_site_offsets, (tuple, list, set)) or
            any(type(value) is not int or value < 0 or value % 4 for value in generic_site_offsets) or
            SITE in generic_site_offsets):
        raise ValueError('Thread stack site overlaps or invalidates the generic rewrite')
    output = rewritten_image[:SITE] + REPLACEMENT + rewritten_image[SITE + 4:]
    return output, _manifest(_digest(original_image), rewritten_image, output)


def verify(image, manifest, *, expected_input_sha256=None):
    """Verify executable bytes and recover the exact prior heap/Host39 image."""
    if not isinstance(image, bytes) or not isinstance(manifest, dict):
        raise ValueError('Invalid thread stack image/manifest')
    _contexts(image, adapted=True)
    digest = manifest.get('source_image_sha256')
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Invalid thread stack original image digest')
    recovered = image[:SITE] + ORIGINAL + image[SITE + 4:]
    expected = _manifest(digest, recovered, image)
    if (json.dumps(manifest, sort_keys=True) != json.dumps(expected, sort_keys=True) or
            expected_input_sha256 is not None and expected_input_sha256 != expected['input_image_sha256']):
        raise ValueError('Thread stack executable manifest or input digest differs')
    return recovered
