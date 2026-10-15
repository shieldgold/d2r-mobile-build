"""Fixed base-v0 source-stack allocation alignment for native 16 KiB hosts.

V1 changes only the allocation alignment. V2 additionally adjusts the common
size before object storage, allocation and SDK creation: round to a host page
and add a full tail page for the SDK header outside the host pthread stack.
The allocator, ownership, returned pointer and free path remain original.
The kernel still moves actual backing and refuses partial host-page mappings.
V3 also budgets the one fixed Volante child heap for that complete stack
contract; its original parent allocator and free ownership are unchanged.
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
VOLANTE_POLICY = dict(NATIVE_POLICY, schema=3, profile='botw-switch-base-v0-pthread-stack-v3',
                      volante_original_heap_bytes=0x4000, volante_heap_bytes=0x10000)
POLICIES = {'host-page-alignment-v1': POLICY, 'host-pthread-stack-v2': NATIVE_POLICY,
            'host-pthread-stack-v3': VOLANTE_POLICY}
VOLANTE_SITE = 0x7268d8
VOLANTE_CONTEXT_OFFSET = 0x7268c0
VOLANTE_CONTEXT = bytes.fromhex('88a200d008412391600a01f9f4a314a968ae80b9c27a68f8e0031232e1230591e3031d32e4030032e5031f2a148a0b94601201f9')
VOLANTE_ORIGINAL = bytes.fromhex('e0031232')  # mov w0,#0x4000, complete child backing
VOLANTE_REPLACEMENT = bytes.fromhex('2000a052')  # mov w0,#1,lsl #16, complete child backing
VOLANTE_DATAFLOW_CONTEXTS = (
    (0x726884, bytes.fromhex('f5d900d0b54641f988a200d0080d23917622029100408152e1630591e3031d32e4030032b4420091f4a315a968ae80b9c27a68f8e5031f2a208a0b94')),  # parent array is this+0x88
    (0x726a90, bytes.fromhex('611241f9600e01f900218052e2031d3254320b9488a200d0080d2491621241f9770e41f9b5a330a9a8da0090084d42f9030140b9a1e303d1e4030032e57b0032e6031332e7031b32f40300aae8da0b94')),
    (0xa091e4, bytes.fromhex('680240f9081940f9a27e141be00313aae10318aa00013fd6')),  # original parent allocation
    (0xa09224, bytes.fromhex('e00314aae10316aae20313aae30314aae40318aa8a0e0094')),  # base/size/parent ownership
    (0xa093c8, bytes.fromhex('892240f929c104d1090d00f9')),  # heap free payload excludes 0x130
    (0xa1d724, bytes.fromhex('e0031caae103172aff0f00b9e90f40b90821c91ae20316aa7ffe0ea968da00b97f7200f97bfa00b9735200f9a1590094')),
    (0xa33df4, bytes.fromhex('ea031d328a7eca9b297c7d93ff030aeb0a0080924011899ae2031d32e10308aa94fdfe97')),  # 32-entry queue: 32*8, same heap
    (0xa1d76c, bytes.fromhex('e00b1a32e2031d32e10316aa1e57ff97607200f9')),  # SDK thread object 0x1c0
    (0x1b788d0, b'Volante\0'),
)


def volante_heap_budget():
    """Worst original ExpHeap split/alignment budget for this single caller.

    Every fresh block/header and 8-byte-aligned base residue was independently
    checked using the original allocator. The queue is allocated before stack;
    the SDK object is allocated afterwards. Initial 0x130 includes the original
    first free-block header; each of the four allocations may split another.
    """
    allocations = [0x108, 0x100, 0x8000, 0x1c0]
    required = 0x130
    for value in [*allocations, 4 * 0x20, 0x4000 - 8]:
        if value > (1 << 64) - 1 - required:
            raise ValueError('Volante child-heap capacity arithmetic overflow')
        required += value
    if required > (1 << 64) - 0x4000:
        raise ValueError('Volante child-heap rounding overflow')
    capacity = (required + 0x3fff) & ~0x3fff
    return {'original_heap_bytes': 0x4000, 'heap_header_bytes': 0x130,
            'allocation_bytes': allocations, 'allocation_alignment_bytes': [8, 8, 0x4000, 8],
            'split_header_bytes': 0x20, 'maximum_alignment_padding_bytes': 0x3ff8,
            'maximum_required_bytes': required, 'heap_bytes': capacity,
            'minimum_remaining_bytes': capacity - required}


def _volante_contexts(image, *, adapted=False):
    within = VOLANTE_SITE - VOLANTE_CONTEXT_OFFSET
    expected = (VOLANTE_CONTEXT[:within] + VOLANTE_REPLACEMENT + VOLANTE_CONTEXT[within + 4:]
                if adapted else VOLANTE_CONTEXT)
    for offset, context in ((VOLANTE_CONTEXT_OFFSET, expected), *VOLANTE_DATAFLOW_CONTEXTS):
        if image[offset:offset + len(context)] != context:
            raise ValueError(f'Volante child-heap ownership/caller context differs or is truncated: main+0x{offset:x}')


def _original_volante_word(image):
    if image[VOLANTE_SITE:VOLANTE_SITE + 4] != VOLANTE_ORIGINAL:
        raise ValueError('Volante child-heap change requires the complete explicit v3 stack policy')


def _native(policy):
    return policy in (NATIVE_POLICY, VOLANTE_POLICY)
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
    if _native(policy):
        start = len(input_image)
        result['patches'].append({'offset': CAPACITY_SITE, 'context_offset': CAPACITY_CONTEXT_OFFSET,
            'context': CAPACITY_CONTEXT.hex(), 'original': CAPACITY_ORIGINAL.hex(),
            'replacement': _branch(CAPACITY_SITE, start).hex(), 'thunk_offset': start,
            'thunk_bytes': _capacity_thunk(start).hex(), 'size_register': 'w24',
            'object_size_store': 0xa1d704, 'allocator_size_register': 'x23',
            'sdk_size_register': 'x4', 'pointer_free_call': 0xa1db18,
            'dataflow_contexts': [{'offset': off, 'context': data.hex()} for off, data in DATAFLOW_CONTEXTS]})
    if policy == VOLANTE_POLICY:
        result['patches'].append({'offset': VOLANTE_SITE, 'context_offset': VOLANTE_CONTEXT_OFFSET,
            'context': VOLANTE_CONTEXT.hex(), 'original': VOLANTE_ORIGINAL.hex(),
            'replacement': VOLANTE_REPLACEMENT.hex(), 'size_register': 'w0',
            'parent_allocator_call': 0x7268ec, 'owned_heap_store': 0x7268f0,
            'heap_budget': volante_heap_budget(),
            'dataflow_contexts': [{'offset': off, 'context': data.hex()} for off, data in VOLANTE_DATAFLOW_CONTEXTS]})
    return result


def apply(original_image, rewritten_image, *, generic_site_offsets, policy='host-page-alignment-v1'):
    """Pure transform; callers also verify the complete base-v0 source set."""
    if not isinstance(original_image, bytes) or not isinstance(rewritten_image, bytes):
        raise ValueError('Thread stack images must be immutable snapshots')
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValueError('Unknown explicit thread stack policy')
    policy = POLICIES[policy]
    _contexts(original_image); _contexts(rewritten_image)
    if policy == VOLANTE_POLICY:
        _volante_contexts(original_image); _volante_contexts(rewritten_image)
    else:
        _original_volante_word(original_image); _original_volante_word(rewritten_image)
    if _native(policy):
        _capacity_contexts(original_image); _capacity_contexts(rewritten_image)
        if len(rewritten_image) % 4:
            raise ValueError('Thread stack thunk requires an aligned input image')
    protected = (SITE, CAPACITY_SITE, VOLANTE_SITE) if policy == VOLANTE_POLICY else ((SITE, CAPACITY_SITE) if _native(policy) else (SITE,))
    if (not isinstance(generic_site_offsets, (tuple, list, set)) or
            any(type(value) is not int or value < 0 or value % 4 for value in generic_site_offsets) or
            any(site in generic_site_offsets for site in protected)):
        raise ValueError('Thread stack site overlaps or invalidates the generic rewrite')
    output = rewritten_image[:SITE] + REPLACEMENT + rewritten_image[SITE + 4:]
    if _native(policy):
        output = (output[:CAPACITY_SITE] + _branch(CAPACITY_SITE, len(output)) +
                  output[CAPACITY_SITE + 4:] + _capacity_thunk(len(output)))
    if policy == VOLANTE_POLICY:
        output = output[:VOLANTE_SITE] + VOLANTE_REPLACEMENT + output[VOLANTE_SITE + 4:]
    return output, _manifest(_digest(original_image), rewritten_image, output, policy)


def verify(image, manifest, *, expected_input_sha256=None):
    """Verify executable bytes and recover the exact prior heap/Host39 image."""
    if not isinstance(image, bytes) or not isinstance(manifest, dict):
        raise ValueError('Invalid thread stack image/manifest')
    policy = policy_from_manifest(manifest)
    if policy == VOLANTE_POLICY:
        _volante_contexts(image, adapted=True)
    else:
        _original_volante_word(image)
    if policy == POLICY and image[CAPACITY_SITE:CAPACITY_SITE + 4] != CAPACITY_ORIGINAL:
        raise ValueError('Legacy alignment policy cannot contain a modified capacity instruction')
    _contexts(image, adapted=True)
    digest = manifest.get('source_image_sha256')
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Invalid thread stack original image digest')
    working = image
    if _native(policy):
        size = manifest.get('input_image_bytes')
        if type(size) is not int or size <= CAPACITY_SITE or size % 4 or len(image) != size + len(CAPACITY_CODE) + 4:
            raise ValueError('Invalid thread stack capacity image bounds')
        branch = _branch(CAPACITY_SITE, size)
        _capacity_contexts(image, branch=branch)
        if image[size:] != _capacity_thunk(size):
            raise ValueError('Thread stack capacity executable thunk differs')
        working = image[:CAPACITY_SITE] + CAPACITY_ORIGINAL + image[CAPACITY_SITE + 4:size]
    if policy == VOLANTE_POLICY:
        working = working[:VOLANTE_SITE] + VOLANTE_ORIGINAL + working[VOLANTE_SITE + 4:]
    recovered = working[:SITE] + ORIGINAL + working[SITE + 4:]
    expected = _manifest(digest, recovered, image, policy)
    if (json.dumps(manifest, sort_keys=True) != json.dumps(expected, sort_keys=True) or
            expected_input_sha256 is not None and expected_input_sha256 != expected['input_image_sha256']):
        raise ValueError('Thread stack executable manifest or input digest differs')
    return recovered
