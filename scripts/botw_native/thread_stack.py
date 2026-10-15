"""Fixed base-v0 source-stack allocation alignment for native 16 KiB hosts.

V1 changes only the allocation alignment. V2 additionally adjusts the common
size before object storage, allocation and SDK creation: round to a host page
and add a full tail page for the SDK header outside the host pthread stack.
The allocator, ownership, returned pointer and free path remain original.
The kernel still moves actual backing and refuses partial host-page mappings.
Apply last, after Host39 and optional bounded heap; undo in reverse order.
"""
import hashlib
import json
import struct
from pathlib import Path

POLICY = {'schema': 1, 'profile': 'botw-switch-base-v0-thread-stack-alignment-v1',
          'alignment_bytes': 0x4000, 'size_unchanged': True, 'original_allocator': True,
          'game_playable': False}
NATIVE_POLICY = {'schema': 2, 'profile': 'botw-switch-base-v0-pthread-stack-v2',
                 'alignment_bytes': 0x4000, 'capacity_round_bytes': 0x4000,
                 'reserved_tail_bytes': 0x4000, 'sdk_header_bytes': 32,
                 'maximum_original_bytes': 0x7fff8000, 'original_allocator': True,
                 'game_playable': False}
POLICIES = {'host-page-alignment-v1': POLICY, 'host-pthread-stack-v2': NATIVE_POLICY}
CAPACITY_SITE = 0xa1d6a0
CAPACITY_CONTEXT_OFFSET = 0xa1d690
CAPACITY_CONTEXT = bytes.fromhex('fd7b05a9fd430191ff4300d1f703072af803062af90305aafa03042afb03032af60302aaf50301aaf30300aaf6adff97')
CAPACITY_ORIGINAL = bytes.fromhex('f803062a')  # mov w24,w6, before all three size consumers
DATAFLOW_CONTEXTS = (
    (0xa1d700, bytes.fromhex('60e20291788a00b99f0e09f8')),  # object.size
    (0xa1d780, bytes.fromhex('01c300f0637640f965fa40b9219846f9e20313aae40317aa62543094')),  # SDK size x4=x23
    (0xa1db10, bytes.fromhex('607640f9400000b4fb56ff97')),  # original pointer-only free
)
# Replay MOV, save x8/x9/NZCV, validate signed positive 4KiB size and checked
# bound, then align_up(size,16KiB)+16KiB. Invalid inputs preserve original w24.
# Restore all scratch/flags and branch back to the original next instruction.
CAPACITY_WORDS = (
    0x2a0603f8,  # mov w24,w6 (original instruction)
    0xa9bf27e8,  # stp x8,x9,[sp,#-16]!
    0xd53b4208,  # mrs x8,nzcv
    0x72002f1f,  # tst w24,#0xfff (keep invalid non-4KiB requests unchanged)
    0x54000141,  # b.ne restore
    0x7100031f,  # cmp w24,#0
    0x5400010d,  # b.le restore
    0x32113fe9,  # mov w9,#0x7fff8000 (largest safely convertible request)
    0x6b09031f,  # cmp w24,w9
    0x540000a8,  # b.hi restore
    0x91400f18,  # add x24,x24,#3,lsl #12
    0x913fff18,  # add x24,x24,#4095
    0x9272c718,  # and x24,x24,#-16384
    0x91401318,  # add x24,x24,#4,lsl #12 (complete reserved tail page)
    0xd51b4208,  # restore: msr nzcv,x8
    0xa8c127e8,  # ldp x8,x9,[sp],#16
)
CAPACITY_CODE = struct.pack('<16I', *CAPACITY_WORDS)
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


def _branch(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < 1 << 27:
        raise ValueError('Thread stack branch exceeds aligned ARM64 range')
    return struct.pack('<I', 0x14000000 | ((delta >> 2) & 0x3ffffff))


def _capacity_contexts(image, *, branch=None):
    expected = CAPACITY_CONTEXT
    if branch is not None:
        expected = expected[:16] + branch + expected[20:]
    for offset, context in ((CAPACITY_CONTEXT_OFFSET, expected), *DATAFLOW_CONTEXTS):
        if image[offset:offset + len(context)] != context:
            raise ValueError(f'Thread stack capacity contract differs or is truncated: main+0x{offset:x}')


def _capacity_thunk(start):
    return CAPACITY_CODE + _branch(start + len(CAPACITY_CODE), CAPACITY_SITE + 4)


def policy_from_manifest(manifest):
    for policy in POLICIES.values():
        if isinstance(manifest, dict) and manifest.get('profile') == policy['profile']:
            return policy
    raise ValueError('Unsupported thread stack manifest profile')


def _manifest(original_digest, input_image, output_image, policy=POLICY):
    result = {'schema': policy['schema'], 'profile': policy['profile'], 'module': 'main',
            'source_image_sha256': original_digest,
            'input_image_bytes': len(input_image), 'output_image_bytes': len(output_image),
            'input_image_sha256': _digest(input_image), 'output_image_sha256': _digest(output_image),
            'patches': [{'offset': SITE, 'context_offset': CONTEXT_OFFSET, 'context': CONTEXT.hex(),
                         'original': ORIGINAL.hex(), 'replacement': REPLACEMENT.hex(),
                         'argument_register': 'w2', 'original_alignment': 0x1000, 'host_alignment': 0x4000,
                         'allocator_call': 0xa1d764, 'create_thread_call': 0xa1d798}]}
    if policy == NATIVE_POLICY:
        start = len(input_image)
        result['patches'].append({'offset': CAPACITY_SITE, 'context_offset': CAPACITY_CONTEXT_OFFSET,
            'context': CAPACITY_CONTEXT.hex(), 'original': CAPACITY_ORIGINAL.hex(),
            'replacement': _branch(CAPACITY_SITE, start).hex(), 'thunk_offset': start,
            'thunk_bytes': _capacity_thunk(start).hex(), 'size_register': 'w24',
            'object_size_store': 0xa1d704, 'allocator_size_register': 'x23',
            'sdk_size_register': 'x4', 'pointer_free_call': 0xa1db18,
            'dataflow_contexts': [{'offset': off, 'context': data.hex()} for off, data in DATAFLOW_CONTEXTS]})
    return result


def apply(original_image, rewritten_image, *, generic_site_offsets, policy='host-page-alignment-v1'):
    """Pure transform; callers also verify the complete base-v0 source set."""
    if not isinstance(original_image, bytes) or not isinstance(rewritten_image, bytes):
        raise ValueError('Thread stack images must be immutable snapshots')
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValueError('Unknown explicit thread stack policy')
    policy = POLICIES[policy]
    _contexts(original_image); _contexts(rewritten_image)
    if policy == NATIVE_POLICY:
        _capacity_contexts(original_image); _capacity_contexts(rewritten_image)
        if len(rewritten_image) % 4:
            raise ValueError('Thread stack thunk requires an aligned input image')
    protected = (SITE, CAPACITY_SITE) if policy == NATIVE_POLICY else (SITE,)
    if (not isinstance(generic_site_offsets, (tuple, list, set)) or
            any(type(value) is not int or value < 0 or value % 4 for value in generic_site_offsets) or
            any(site in generic_site_offsets for site in protected)):
        raise ValueError('Thread stack site overlaps or invalidates the generic rewrite')
    output = rewritten_image[:SITE] + REPLACEMENT + rewritten_image[SITE + 4:]
    if policy == NATIVE_POLICY:
        output = (output[:CAPACITY_SITE] + _branch(CAPACITY_SITE, len(output)) +
                  output[CAPACITY_SITE + 4:] + _capacity_thunk(len(output)))
    return output, _manifest(_digest(original_image), rewritten_image, output, policy)


def verify(image, manifest, *, expected_input_sha256=None):
    """Verify executable bytes and recover the exact prior heap/Host39 image."""
    if not isinstance(image, bytes) or not isinstance(manifest, dict):
        raise ValueError('Invalid thread stack image/manifest')
    policy = policy_from_manifest(manifest)
    if policy == POLICY and image[CAPACITY_SITE:CAPACITY_SITE + 4] != CAPACITY_ORIGINAL:
        raise ValueError('Legacy alignment policy cannot contain a modified capacity instruction')
    _contexts(image, adapted=True)
    digest = manifest.get('source_image_sha256')
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Invalid thread stack original image digest')
    working = image
    if policy == NATIVE_POLICY:
        size = manifest.get('input_image_bytes')
        if type(size) is not int or size <= CAPACITY_SITE or size % 4 or len(image) != size + len(CAPACITY_CODE) + 4:
            raise ValueError('Invalid thread stack capacity image bounds')
        branch = _branch(CAPACITY_SITE, size)
        _capacity_contexts(image, branch=branch)
        if image[size:] != _capacity_thunk(size):
            raise ValueError('Thread stack capacity executable thunk differs')
        working = image[:CAPACITY_SITE] + CAPACITY_ORIGINAL + image[CAPACITY_SITE + 4:size]
    recovered = working[:SITE] + ORIGINAL + working[SITE + 4:]
    expected = _manifest(digest, recovered, image, policy)
    if (json.dumps(manifest, sort_keys=True) != json.dumps(expected, sort_keys=True) or
            expected_input_sha256 is not None and expected_input_sha256 != expected['input_image_sha256']):
        raise ValueError('Thread stack executable manifest or input digest differs')
    return recovered
