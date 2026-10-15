#!/usr/bin/env python3
"""Offline BotW shader build: runtime shader captures + explicit per-shader facts
-> SPIR-V (dmsh_xlate, the Ryujinx translator) -> MSL (spirv-cross) -> Metal
library + reflection JSON in the nvn_refl.m format.

Inputs
  Captures are written by the host (dm/gfx/nvn/botw_shader.c, DM_SHADER_CAPTURE):
  <key>.control, <key>.code and <key>.json, where key = first 16 hex digits of
  SHA256("botw-shader-v1\\0" || seven LE32 fields || control || code). Every
  capture is re-derived from its bytes and must match the runtime contract
  exactly before anything else runs.

  Facts are explicit per-shader JSON files <facts>/<key>.facts.json (schema
  botw-shader-facts-v1, see FACTS_HELP). They bind hardware resources seen by
  the translator to NVN binding slots and supply query answers that the code
  does not encode (vertex attribute / fragment output component types, compute
  workgroup size and shared memory). Nothing is inferred from D2R tables or PC
  reflection.

Modes
  build          Runnable output. Every gate must pass: valid capture, facts
                 file bound to the capture digest, no translator query fallback,
                 every binding resolved by a fact, Apple metal/metallib present.
                 Ready shaders go to <out>/botw-b-<chain>/<key>_b.{metallib,json,metal}.
  build --probe  Non-runnable diagnostics. Runs every stage that can run, records
                 each unknown, and writes <out>/probe-<chain>/<key>/ with
                 route "probe" reflection that nvn_refl_from_json rejects.

The output directory and the capture directory must be outside any Git work
tree; generated shader bytes never belong in the repository. This tool never
writes shader_map.tsv or any other runtime table.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

TOOL_PATH = os.path.abspath(__file__)
GFX_DIR = os.path.dirname(os.path.dirname(TOOL_PATH))
XLATE_SOURCES = [os.path.join(GFX_DIR, 'tools', 'dmsh_xlate', n) for n in ('Program.cs', 'dmsh_xlate.csproj')]

# Runtime capture contract (dm/gfx/nvn/botw_shader.c).
SCHEMA = 'botw-shader-v1'
DOMAIN = SCHEMA.encode() + b'\0'
SHADER_MAGIC = 0x98761234
CODE_MAGIC = 0x12345678
CONTROL_SIZES = {9: 0x7d1, 14: 0x848}
CODE_LIMIT = 16 * 1024 * 1024
JSON_LIMIT = 1536
FIELDS = ('version', 'stage', 'control_size', 'code_size', 'code_offset', 'c1_offset', 'c1_size')
SPH_STAGE = {0: 1, 1: 5, 2: 4, 3: 2, 4: 3}
STAGE_NAMES = {0: 'vertex', 1: 'fragment', 2: 'geometry', 3: 'tess_control', 4: 'tess_evaluation', 5: 'compute'}
XLATE_STAGE = {0: 'vs', 1: 'ps', 5: 'cs'}
KEY_RE = re.compile(r'[0-9a-f]{16}')
HEX64_RE = re.compile(r'[0-9a-f]{64}')

# nvn_refl.h limits.
REFL_MAX_BINDINGS = 64
REFL_MAX_C1 = 4096

# Same spirv-cross options as the existing route B build (dmsh_build.py).
SPIRV_CROSS_ARGS = ['--msl', '--msl-version', '20300', '--msl-argument-buffers',
                    '--msl-texture-buffer-native', '--msl-pad-fragment-output']

FACTS_SCHEMA = 'botw-shader-facts-v1'
MANIFEST_SCHEMA = 'botw-shader-build-manifest-v1'
REFLECTION_SCHEMA = 'botw-shader-reflection-v1'
TEXTURE_TYPES = {
    '1d': ('texture1d<',), '1darray': ('texture1d_array<',),
    '2d': ('texture2d<', 'depth2d<'), '2darray': ('texture2d_array<', 'depth2d_array<'),
    '2dms': ('texture2d_ms<', 'depth2d_ms<'), '2dmsarray': ('texture2d_ms_array<', 'depth2d_ms_array<'),
    '3d': ('texture3d<',), 'cube': ('texturecube<', 'depthcube<'),
    'cubearray': ('texturecube_array<', 'depthcube_array<'), 'buffer': ('texture_buffer<',),
}
COMPONENT_TYPES = {'float': ('float', 'half'), 'sint': ('int', 'short', 'char'), 'uint': ('uint', 'ushort', 'uchar')}
# Graphics state passed to dmsh_xlate. Older jobs that omit device_state keep these
# defaults; BotW facts explicitly override them because they affect translated code.
DEVICE_STATE_VALUES = {'window_origin_mode': ('upper_left', 'lower_left'), 'depth_mode': ('near_is_zero', 'near_is_minus_w')}
DEFAULT_DEVICE_STATE = {'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_zero'}

FACTS_HELP = """\
Facts file <facts-dir>/<key>.facts.json (all numbers are JSON integers; map keys
are decimal strings):
{
  "schema": "botw-shader-facts-v1",
  "key": "<16 hex>", "sha256": "<64 hex>",    # must equal the capture identity
  "stage": "vertex" | "fragment" | "compute",  # must equal the capture stage
  "provenance": "<where these facts were established>",
  "constant_buffers": {"<hw cbuf slot 3..17>": {"binding": <NVN uniform binding>}},
  "textures": {"<translator handle>": {"slot": <NVN texture binding>, "type": "<type>",
                                        "sampler_slot": <NVN sampler binding, separate handles only>}},
  "images": {"<translator handle>": {"slot": <NVN image binding>, "type": "<type>", "format": "<format>"}},
  "storage_buffers": {"<c0 byte offset>": {"slot": <NVN storage buffer binding>}},
  "attributes": {"<location 0..31>": "float" | "sint" | "uint"},   # vertex only
  "outputs": {"<render target 0..7>": "float" | "sint" | "uint"},  # fragment only
  "compute": {"local_size": [x, y, z], "shared_memory": <bytes>}, # compute only
  "device_state": {"window_origin_mode": "upper_left" | "lower_left",
                   "depth_mode": "near_is_zero" | "near_is_minus_w"}  # vertex/fragment
}
device_state records the NVN device window origin and depth modes in effect
when BotW uses the shader. Validated facts are forwarded to dmsh_xlate.
Missing facts remain unresolved; only diagnostic jobs use the legacy defaults.
Texture types: """ + ', '.join(sorted(TEXTURE_TYPES)) + """.
Translator handles are the "handle" values in <key>.res.json (the hex suffix of
the MSL resource name). A probe writes "required_facts" listing every entry the
shader needs, with null values that must be replaced by established facts.
"""


class CaptureError(Exception):
    pass


class FactsError(Exception):
    pass


class ToolError(Exception):
    pass


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


# Path safety ---------------------------------------------------------------

def git_ancestor(path):
    """Returns the first directory at or above path that contains .git, or None."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    probe = os.path.realpath(probe)
    while True:
        if os.path.lexists(os.path.join(probe, '.git')):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent


def require_outside_git(path, what):
    if not os.path.isabs(path):
        raise ToolError(f'{what} must be an absolute path: {path}')
    repo = git_ancestor(path)
    if repo:
        raise ToolError(f'{what} {path} is inside the Git work tree {repo}; game-derived data must stay outside repositories')


def make_private_dir(path, what):
    require_outside_git(path, what)
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.path.isdir(path):
        raise ToolError(f'{what} is not a directory: {path}')
    require_outside_git(os.path.realpath(path), what)
    return os.path.realpath(path)


def read_regular(path, limit):
    """Reads a regular file without following a final symlink, bounded by limit."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError as e:
        raise CaptureError(f'cannot open {os.path.basename(path)}: {e.strerror}')
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CaptureError(f'{os.path.basename(path)} is not a regular file')
        if st.st_size > limit:
            raise CaptureError(f'{os.path.basename(path)} is {st.st_size} bytes, above the {limit}-byte limit')
        chunks, left = [], st.st_size + 1
        while left > 0:
            chunk = os.read(fd, min(left, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        data = b''.join(chunks)
        if len(data) != st.st_size:
            raise CaptureError(f'{os.path.basename(path)} changed size while being read')
        return data
    finally:
        os.close(fd)


def write_atomic(path, data, mode=0o600):
    if isinstance(data, str):
        data = data.encode()
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write_json(path, value):
    write_atomic(path, json.dumps(value, indent=1, sort_keys=False, ensure_ascii=True) + '\n')


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def strict_json(data, what):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError(f'duplicate key {k!r}')
            out[k] = v
        return out
    try:
        text = data.decode('utf-8')
        return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (UnicodeDecodeError, ValueError) as e:
        raise CaptureError(f'{what} is not strict JSON: {e}')


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# Capture contract ----------------------------------------------------------

def capture_digest(fields, control, code):
    h = hashlib.sha256()
    h.update(DOMAIN)
    h.update(struct.pack('<7I', *(fields[n] for n in FIELDS)))
    h.update(control)
    h.update(code)
    return h.hexdigest()


def canonical_capture_json(key, sha256, fields, control_sha256, code_sha256):
    """Byte-exact replica of the JSON written by botw_shader.c capture()."""
    f = fields
    meta = ', '.join(str(f[n]) for n in FIELDS)
    return (
        '{\n  "schema": "%s",\n  "key": "%s",\n  "sha256": "%s",\n'
        '  "control_sha256": "%s",\n  "code_sha256": "%s",\n'
        '  "version": %u,\n  "stage": %u,\n  "control_size": %u,\n  "code_size": %u,\n'
        '  "code_offset": %u,\n  "c1_offset": %u,\n  "c1_size": %u,\n'
        '  "hash_metadata_le32": [%s],\n'
        '  "control_file": "%s.control",\n  "code_file": "%s.code"\n}\n'
    ) % (SCHEMA, key, sha256, control_sha256, code_sha256, f['version'], f['stage'], f['control_size'],
         f['code_size'], f['code_offset'], f['c1_offset'], f['c1_size'], meta, key, key)


def parse_control(control):
    """Applies the runtime acceptance checks and returns the seven hashed fields."""
    if len(control) < 0x718:
        raise CaptureError(f'control is {len(control)} bytes, shorter than the 0x718-byte header')
    version, size = u32(control, 8), u32(control, 0x20)
    if u32(control, 0) != SHADER_MAGIC or u32(control, 4) != 1 or version not in CONTROL_SIZES:
        raise CaptureError('unsupported shader control magic or version')
    if size != CONTROL_SIZES[version]:
        raise CaptureError(f'unexpected shader control size {size:#x} for version {version}')
    if len(control) != size:
        raise CaptureError(f'control file has {len(control)} bytes, header says {size}')
    f = {'version': version, 'control_size': size, 'c1_offset': u32(control, 0x700),
         'code_size': u32(control, 0x704), 'code_offset': u32(control, 0x708), 'stage': u32(control, 0x714)}
    graphics = f['stage'] != 5
    if (f['stage'] > 5 or not f['code_size'] or f['code_size'] > CODE_LIMIT or f['code_size'] & 7 or
            f['c1_offset'] > f['code_size'] or f['code_offset'] > f['c1_offset'] or f['c1_offset'] & 7 or
            (f['code_offset'] != 0x30 or f['c1_offset'] < 0x88 if graphics else f['code_offset'] != 0 or f['c1_offset'] < 8)):
        raise CaptureError('invalid shader stage, code size, header offset, or C1 bounds')
    f['c1_size'] = f['code_size'] - f['c1_offset']
    return f


def check_code(fields, code):
    if fields['stage'] == 5:
        return
    if u32(code, 0) != CODE_MAGIC or (u32(code, fields['code_offset']) >> 10) & 15 != SPH_STAGE[fields['stage']]:
        raise CaptureError('shader code wrapper or SPH stage does not match control')


class Capture:
    def __init__(self, directory, key, sha256, fields, control, code):
        self.directory, self.key, self.sha256, self.fields = directory, key, sha256, fields
        self.control, self.code = control, code

    @property
    def stage(self):
        return self.fields['stage']

    @property
    def stage_name(self):
        return STAGE_NAMES[self.stage]

    @property
    def c1(self):
        return self.code[self.fields['c1_offset']:self.fields['code_size']]

    @property
    def translator_code(self):
        """Graphics: SPH + instructions (+ trailing C1); compute: instructions (+ C1)."""
        return self.code[self.fields['code_offset']:]

    def summary(self):
        return dict({'key': self.key, 'sha256': self.sha256, 'stage_name': self.stage_name}, **self.fields)


def load_capture(directory, key):
    if not KEY_RE.fullmatch(key):
        raise CaptureError(f'invalid capture key {key!r}')
    raw = read_regular(os.path.join(directory, key + '.json'), JSON_LIMIT)
    meta = strict_json(raw, key + '.json')
    expected = {'schema', 'key', 'sha256', 'control_sha256', 'code_sha256', 'hash_metadata_le32',
                'control_file', 'code_file'} | set(FIELDS)
    if not isinstance(meta, dict) or set(meta) != expected:
        got = set(meta) if isinstance(meta, dict) else set()
        raise CaptureError(f'metadata fields differ from {SCHEMA}: missing {sorted(expected - got)}, extra {sorted(got - expected)}')
    if meta['schema'] != SCHEMA:
        raise CaptureError(f'schema {meta["schema"]!r} is not {SCHEMA}')
    for name in ('key', 'sha256', 'control_sha256', 'code_sha256', 'control_file', 'code_file'):
        if not isinstance(meta[name], str):
            raise CaptureError(f'{name} is not a string')
    for name in FIELDS:
        if not is_int(meta[name]) or not 0 <= meta[name] <= 0xffffffff:
            raise CaptureError(f'{name} is not a 32-bit unsigned integer')
    if meta['key'] != key:
        raise CaptureError(f'metadata key {meta["key"]} does not match file name')
    for name in ('sha256', 'control_sha256', 'code_sha256'):
        if not HEX64_RE.fullmatch(meta[name]):
            raise CaptureError(f'{name} is not 64 lowercase hex digits')
    if meta['sha256'][:16] != key:
        raise CaptureError('key is not the first 16 digits of sha256')
    if meta['control_file'] != key + '.control' or meta['code_file'] != key + '.code':
        raise CaptureError('control_file / code_file do not name this capture')
    control = read_regular(os.path.join(directory, key + '.control'), max(CONTROL_SIZES.values()))
    code = read_regular(os.path.join(directory, key + '.code'), CODE_LIMIT)
    if sha256_hex(control) != meta['control_sha256']:
        raise CaptureError('control_sha256 does not match the control file')
    if sha256_hex(code) != meta['code_sha256']:
        raise CaptureError('code_sha256 does not match the code file')
    fields = parse_control(control)
    for name in FIELDS:
        if meta[name] != fields[name]:
            raise CaptureError(f'{name} is {meta[name]} in metadata but {fields[name]} in the control')
    if len(code) != fields['code_size']:
        raise CaptureError(f'code file has {len(code)} bytes, control says {fields["code_size"]}')
    hm = meta['hash_metadata_le32']
    if not isinstance(hm, list) or len(hm) != 7 or any(not is_int(v) for v in hm) or hm != [fields[n] for n in FIELDS]:
        raise CaptureError('hash_metadata_le32 is not the seven hashed fields in order')
    check_code(fields, code)
    digest = capture_digest(fields, control, code)
    if digest != meta['sha256']:
        raise CaptureError(f'digest mismatch: recomputed {digest}')
    canonical = canonical_capture_json(key, digest, fields, meta['control_sha256'], meta['code_sha256'])
    if raw != canonical.encode():
        raise CaptureError('metadata is not the exact runtime serialization')
    return Capture(directory, key, digest, fields, control, code)


def capture_keys(directory):
    """Complete captures (JSON present) and incomplete leftovers."""
    names = os.listdir(directory)
    keys = sorted(n[:-5] for n in names if n.endswith('.json') and KEY_RE.fullmatch(n[:-5]))
    stems = {n.rsplit('.', 1)[0] for n in names if n.endswith(('.control', '.code'))}
    return keys, sorted(s for s in stems if KEY_RE.fullmatch(s) and s not in keys)


# Facts --------------------------------------------------------------------

def _index(value, what, lo, hi):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value) or not lo <= int(value) <= hi:
        raise FactsError(f'{what} key {value!r} is not a decimal integer in {lo}..{hi}')
    return int(value)


def _slot(entry, name, what, hi=255):
    v = entry.get(name)
    if not is_int(v) or not 0 <= v <= hi:
        raise FactsError(f'{what}.{name} must be an integer in 0..{hi}')
    return v


def _map(doc, name):
    value = doc.get(name, {})
    if not isinstance(value, dict):
        raise FactsError(f'{name} must be an object')
    return value


def _object(value, what, allowed, required=()):
    if not isinstance(value, dict):
        raise FactsError(f'{what} must be an object')
    extra = set(value) - set(allowed)
    missing = set(required) - set(value)
    if extra or missing:
        raise FactsError(f'{what}: unexpected {sorted(extra)}, missing {sorted(missing)}')
    return value


class Facts:
    def __init__(self, path=None, sha256=None, provenance=None):
        self.path, self.sha256, self.provenance = path, sha256, provenance
        self.constant_buffers, self.textures, self.images, self.storage_buffers = {}, {}, {}, {}
        self.attributes, self.outputs, self.compute, self.device_state = {}, {}, None, None


def load_facts(path, capture):
    try:
        raw = read_regular(path, 1 << 20)
        doc = strict_json(raw, os.path.basename(path))
    except CaptureError as e:
        raise FactsError(str(e))
    top = {'schema', 'key', 'sha256', 'stage', 'provenance', 'constant_buffers', 'textures', 'images',
           'storage_buffers', 'attributes', 'outputs', 'compute', 'device_state'}
    _object(doc, 'facts', top, ('schema', 'key', 'sha256', 'stage', 'provenance'))
    if doc['schema'] != FACTS_SCHEMA:
        raise FactsError(f'schema must be {FACTS_SCHEMA}')
    if doc['key'] != capture.key or doc['sha256'] != capture.sha256:
        raise FactsError('facts key / sha256 are not bound to this capture digest')
    if doc['stage'] != capture.stage_name:
        raise FactsError(f'facts stage {doc["stage"]!r} differs from capture stage {capture.stage_name!r}')
    if not isinstance(doc['provenance'], str) or not doc['provenance'].strip():
        raise FactsError('provenance must be a non-empty string')
    f = Facts(path, sha256_hex(raw), doc['provenance'])
    for k, v in _map(doc, 'constant_buffers').items():
        slot = _index(k, 'constant_buffers', 3, 17)
        _object(v, f'constant_buffers[{k}]', ('binding',), ('binding',))
        f.constant_buffers[slot] = _slot(v, 'binding', f'constant_buffers[{k}]')
    for k, v in _map(doc, 'textures').items():
        h = _index(k, 'textures', 0, 0xffffffff)
        _object(v, f'textures[{k}]', ('slot', 'type', 'sampler_slot'), ('slot', 'type'))
        if v['type'] not in TEXTURE_TYPES:
            raise FactsError(f'textures[{k}].type {v["type"]!r} is not one of {sorted(TEXTURE_TYPES)}')
        f.textures[h] = {'slot': _slot(v, 'slot', f'textures[{k}]'), 'type': v['type']}
        if 'sampler_slot' in v:
            f.textures[h]['sampler_slot'] = _slot(v, 'sampler_slot', f'textures[{k}]', 4095)
    for k, v in _map(doc, 'images').items():
        h = _index(k, 'images', 0, 0xffffffff)
        _object(v, f'images[{k}]', ('slot', 'type', 'format'), ('slot', 'type', 'format'))
        if v['type'] not in TEXTURE_TYPES or not isinstance(v['format'], str) or not re.fullmatch(r'[A-Za-z0-9]+', v['format']):
            raise FactsError(f'images[{k}] has an invalid type or format')
        f.images[h] = {'slot': _slot(v, 'slot', f'images[{k}]'), 'type': v['type'], 'format': v['format']}
    for k, v in _map(doc, 'storage_buffers').items():
        off = _index(k, 'storage_buffers', 0, 0xffff)
        _object(v, f'storage_buffers[{k}]', ('slot',), ('slot',))
        f.storage_buffers[off] = _slot(v, 'slot', f'storage_buffers[{k}]')
    for table, lo_hi, stage in (('attributes', 31, 'vertex'), ('outputs', 7, 'fragment')):
        values = _map(doc, table)
        if values and capture.stage_name != stage:
            raise FactsError(f'{table} facts apply only to {stage} shaders')
        for k, v in values.items():
            loc = _index(k, table, 0, lo_hi)
            if v not in COMPONENT_TYPES:
                raise FactsError(f'{table}[{k}] must be one of {sorted(COMPONENT_TYPES)}')
            getattr(f, table)[loc] = v
    if 'compute' in doc:
        if capture.stage_name != 'compute':
            raise FactsError('compute facts apply only to compute shaders')
        c = _object(doc['compute'], 'compute', ('local_size', 'shared_memory'), ('local_size', 'shared_memory'))
        ls = c['local_size']
        if not isinstance(ls, list) or len(ls) != 3 or any(not is_int(x) or not 1 <= x <= 1024 for x in ls) or ls[0] * ls[1] * ls[2] > 1024:
            raise FactsError('compute.local_size must be three integers >= 1 with a product <= 1024')
        if not is_int(c['shared_memory']) or not 0 <= c['shared_memory'] <= 0xc000:
            raise FactsError('compute.shared_memory must be an integer in 0..49152')
        f.compute = {'local_size': list(ls), 'shared_memory': c['shared_memory']}
    if 'device_state' in doc:
        if capture.stage_name == 'compute':
            raise FactsError('device_state facts apply only to graphics shaders')
        d = _object(doc['device_state'], 'device_state', DEVICE_STATE_VALUES, DEVICE_STATE_VALUES)
        for k, allowed in DEVICE_STATE_VALUES.items():
            if d[k] not in allowed:
                raise FactsError(f'device_state.{k} must be one of {list(allowed)}')
        f.device_state = dict(d)
    return f


# Tools ----------------------------------------------------------------------

def run(argv, timeout, **kw):
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout, **kw)
    except FileNotFoundError:
        raise ToolError(f'tool not found: {argv[0]}')
    except subprocess.TimeoutExpired:
        raise ToolError(f'tool timed out after {timeout}s: {" ".join(argv[:3])}')


def file_sha16(path):
    with open(path, 'rb') as f:
        return sha256_hex(f.read())[:16]


def repo_xlate_source_sha():
    """Same convention as dmsh_xlate --source-sha and dmsh_chain.xlate_source_sha."""
    return sha256_hex(b''.join(open(p, 'rb').read() for p in XLATE_SOURCES))[:16]


class Tools:
    def __init__(self, xlate=None, spirv_cross=None, metal='auto', helper_dir=None):
        self.xlate_path = xlate
        self.spirv_cross_path = spirv_cross
        self.metal_choice = metal
        self.helper_dir = helper_dir
        self.identity = {}
        self.metal = None
        self.helper = None

    def xlate_argv(self):
        if not self.xlate_path:
            raise ToolError('no translator: pass --xlate or set BOTW_XLATE (dmsh_xlate executable or dmsh_xlate.dll)')
        p = os.path.abspath(self.xlate_path)
        if not os.path.isfile(p):
            raise ToolError(f'translator not found: {p}')
        return (['dotnet', p] if p.endswith('.dll') else [p]), p

    def probe_xlate(self):
        argv, p = self.xlate_argv()
        r = run(argv + ['--source-sha'], 120, text=True)
        if r.returncode:
            raise ToolError(f'translator did not run: {r.stderr.strip()[:200]}')
        d = os.path.dirname(p)
        dlls = {}
        for name in ('dmsh_xlate.dll', 'Ryujinx.Graphics.Shader.dll', 'Ryujinx.Common.dll', 'Spv.Generator.dll'):
            path = os.path.join(d, name)
            dlls[name] = file_sha16(path) if os.path.exists(path) else 'missing'
        embedded, repo = r.stdout.strip(), repo_xlate_source_sha()
        ident = {'path': p, 'embedded_source_sha16': embedded, 'repo_source_sha16': repo,
                 'built_from_repo_source': embedded == repo, 'dll_sha16': dlls}
        if p.endswith('.dll'):
            v = run(['dotnet', '--version'], 60, text=True)
            ident['dotnet'] = v.stdout.strip()
        self.identity['xlate'] = ident
        return ident

    def probe_spirv_cross(self):
        p = self.spirv_cross_path or shutil.which('spirv-cross')
        if not p:
            raise ToolError('spirv-cross not found: pass --spirv-cross or set SPIRV_CROSS')
        p = os.path.abspath(p)
        r = run([p, '--revision'], 60, text=True)
        rev = (r.stdout + r.stderr).strip()
        self.spirv_cross_path = p
        self.identity['spirv_cross'] = {'path': p, 'revision': rev, 'sha16': file_sha16(p)}
        return self.identity['spirv_cross']

    def probe_metal(self):
        info = {'xcrun_metal': None, 'xcrun_metallib': None}
        r = run(['xcrun', '-sdk', 'macosx', 'metal', '--version'], 60, text=True)
        if r.returncode == 0:
            info['xcrun_metal'] = r.stdout.splitlines()[0] if r.stdout else 'unknown'
            m = run(['xcrun', '-sdk', 'macosx', '-f', 'metallib'], 60, text=True)
            if m.returncode == 0:
                info['xcrun_metallib'] = m.stdout.strip()
        else:
            info['xcrun_error'] = (r.stderr or r.stdout).strip().splitlines()[-1:] or ['unknown']
            info['xcrun_error'] = info['xcrun_error'][0]
        xcrun_ok = bool(info['xcrun_metal'] and info['xcrun_metallib'])
        if self.metal_choice == 'xcrun' and not xcrun_ok:
            raise ToolError('Apple metal/metallib are unavailable: ' + info.get('xcrun_error', 'metallib missing'))
        self.metal = 'xcrun' if self.metal_choice in ('auto', 'xcrun') and xcrun_ok else 'runtime'
        if self.metal_choice == 'none':
            self.metal = None
        info['backend'] = self.metal
        if self.metal == 'runtime':
            info['runtime_helper'] = self.build_helper()
        self.identity['metal'] = info
        return info

    def build_helper(self):
        """Compiles a small Metal.framework source compiler. It validates MSL with
        the OS compiler; it cannot produce a metallib."""
        if not self.helper_dir:
            raise ToolError('no helper directory for the Metal runtime check')
        src_hash = sha256_hex(MSL_CHECK_SOURCE.encode())[:16]
        os.makedirs(self.helper_dir, mode=0o700, exist_ok=True)
        exe = os.path.join(self.helper_dir, f'msl_check-{src_hash}')
        if not os.path.exists(exe):
            src = exe + '.m'
            write_atomic(src, MSL_CHECK_SOURCE)
            r = run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-framework', 'Foundation', '-framework', 'Metal',
                     src, '-o', exe + '.tmp'], 300, text=True)
            if r.returncode:
                raise ToolError('cannot build the Metal runtime check helper: ' + r.stderr.strip()[:300])
            os.replace(exe + '.tmp', exe)
        self.helper = exe
        return {'path': exe, 'source_sha16': src_hash}

    def chain(self, options):
        """Build identity for runnable output: source and tool fingerprints."""
        h = hashlib.sha256()
        h.update(open(TOOL_PATH, 'rb').read())
        h.update(json.dumps(self.identity.get('xlate', {}).get('dll_sha16'), sort_keys=True).encode())
        h.update(self.identity.get('xlate', {}).get('embedded_source_sha16', '').encode())
        sc = self.identity.get('spirv_cross', {})
        h.update((sc.get('revision', '') + sc.get('sha16', '')).encode())
        h.update(json.dumps(SPIRV_CROSS_ARGS).encode())
        h.update(str(self.identity.get('metal', {}).get('xcrun_metal')).encode())
        h.update(json.dumps(options, sort_keys=True).encode())
        return h.hexdigest()[:12]


MSL_CHECK_SOURCE = r'''// Validates Metal Shading Language with the Metal.framework compiler and
// prints a JSON summary of the compiled functions. Generated by botw_shader_build.py.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 2) { fprintf(stderr, "usage: msl_check FILE.metal\n"); return 2; }
        NSError *e = nil;
        NSString *src = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&e];
        if (!src) { fprintf(stderr, "cannot read MSL source\n"); return 2; }
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { fprintf(stderr, "no Metal device\n"); return 2; }
        MTLCompileOptions *o = [MTLCompileOptions new];
        o.languageVersion = MTLLanguageVersion2_3;
        id<MTLLibrary> lib = [dev newLibraryWithSource:src options:o error:&e];
        NSMutableDictionary *r = [NSMutableDictionary dictionary];
        r[@"device"] = dev.name;
        r[@"ok"] = @(lib != nil);
        r[@"language_version"] = @"2.3";
        if (e) r[@"diagnostics"] = e.localizedDescription;
        NSMutableArray *fs = [NSMutableArray array];
        for (NSString *n in lib.functionNames) {
            id<MTLFunction> f = [lib newFunctionWithName:n];
            NSMutableArray *va = [NSMutableArray array];
            for (MTLVertexAttribute *a in f.vertexAttributes)
                [va addObject:@{@"name": a.name, @"index": @(a.attributeIndex), @"data_type": @(a.attributeType), @"active": @(a.active)}];
            [fs addObject:@{@"name": n, @"function_type": @(f.functionType), @"vertex_attributes": va}];
        }
        r[@"functions"] = fs;
        NSData *j = [NSJSONSerialization dataWithJSONObject:r options:NSJSONWritingSortedKeys error:nil];
        fwrite(j.bytes, 1, j.length, stdout);
        fputc('\n', stdout);
        return lib ? 0 : 1;
    }
}
'''

# MTLDataType values for vertex attribute cross-checks.
MTL_SCALAR = {3: 'float', 4: 'float', 5: 'float', 6: 'float', 16: 'float', 17: 'float', 18: 'float', 19: 'float',
              29: 'sint', 30: 'sint', 31: 'sint', 32: 'sint', 33: 'uint', 34: 'uint', 35: 'uint', 36: 'uint'}


# Translation and analysis --------------------------------------------------

def make_job(capture, facts, code_path, options):
    job = {'key': capture.key, 'stage': XLATE_STAGE[capture.stage], 'code': code_path, 'c1': capture.c1.hex(),
           # Texture and image facts are not passed: Program.cs resolves them only through the
           # D2R handle table window (c[2] 0x168 / 0x568), which is not a BotW fact.
           'tex': {}, 'img': {},
           'attr': {str(k): v for k, v in sorted(facts.attributes.items())},
           'fout': {str(k): v for k, v in sorted(facts.outputs.items())},
           'debug': options['xlate_debug'], 'reduced': options['xlate_reduced']}
    if facts.compute:
        job['local'] = facts.compute['local_size']
        job['shared'] = facts.compute['shared_memory']
    if facts.device_state is not None:
        job['device_state'] = dict(facts.device_state)
    return job


def translate(tools, jobs, work):
    """Runs one translator process over all jobs. Returns {key: error or None}."""
    argv, _ = tools.xlate_argv()
    job_path = os.path.join(work, 'jobs.json')
    write_json(job_path, jobs)
    r = run(argv + [job_path, work], 1800)
    stderr = r.stderr.decode('utf-8', 'replace').splitlines()
    out = {}
    for job in jobs:
        key = job['key']
        lines = [ln for ln in stderr if key in ln]
        missing = [s for s in ('.spv', '.res.json') if not os.path.exists(os.path.join(work, key + s))]
        out[key] = None if not lines and not missing else {'missing': missing, 'translator_stderr': lines}
    return out


def tex_handle(name):
    """MSL resource name -> (kind, handle, cbuf) for Ryujinx texture/image names."""
    m = re.fullmatch(r'[a-z]+_([ti])\w*?_(?:cb(\d+)|tcb)_([0-9A-F]+)(Smplr)?', name)
    if not m:
        return None
    kind = 'samp' if m.group(4) else ('img' if m.group(1) == 'i' else 'tex')
    return kind, int(m.group(3), 16), int(m.group(2)) if m.group(2) else None


def analyze(capture, facts, res, msl):
    """Resolves every MSL binding, vertex input and fragment output against explicit
    facts. Returns (bindings, unknowns, conflicts, required, used)."""
    unknowns, conflicts = [], []
    required = {'constant_buffers': {}, 'textures': {}, 'images': {}, 'storage_buffers': {},
                'attributes': {}, 'outputs': {}}
    # Facts files always need these identity fields; a probe lists them for convenience.
    required_identity = {'schema': FACTS_SCHEMA, 'key': capture.key, 'sha256': capture.sha256,
                         'stage': capture.stage_name, 'provenance': None}
    used = {'constant_buffers': set(), 'textures': set(), 'images': set(), 'storage_buffers': set(),
            'attributes': set(), 'outputs': set()}
    for note in res.get('notes', []):
        unknowns.append({'what': 'translator query fallback', 'detail': 'translator answered a query with a default',
                         'raw_note': note})
    tex_info = {t['handle']: t for t in res.get('textures', [])}
    img_info = {t['handle']: t for t in res.get('images', [])}
    sbufs = sorted(res.get('sbufs', []), key=lambda b: b['binding'])
    body = re.search(r'struct spvDescriptorSetBuffer0\s*\{(.*?)\};', msl, re.S)
    bindings = []
    for m in re.finditer(r'^\s*(.+?)\s+(\w+)\s*\[\[id\((\d+)\)\]\];', body.group(1) if body else '', re.M):
        ty, name, aid = m.group(1).strip(), m.group(2), int(m.group(3))
        e = {'msl_name': name, 'msl_type': ty, 'arg_id': aid}
        bindings.append(e)

        def unknown(why):
            e['unknown'] = why
            unknowns.append({'what': name, 'detail': why})

        if ty.startswith('array<'):
            e['kind'] = '?'
            unknown('resource array; nvn_refl has no array binding kind')
            continue
        cb = re.fullmatch(r'[a-z]+_c(\d+)', name)
        sb = re.fullmatch(r'[a-z]+_s(\d+)', name)
        th = tex_handle(name)
        if name == 'support_buffer':
            e['kind'] = 'support'
        elif cb:
            n = int(cb.group(1))
            if n == 1:
                e['kind'] = 'c1'
                e['bank'] = 1
            elif n in (0, 2):
                e.update(kind='drvcb', bank=n)
                unknown(f'reads NVN driver constant bank c{n}; the BotW driver bank layout is not established')
            else:
                e['kind'] = 'cbuf'
                used['constant_buffers'].add(n)
                if n in facts.constant_buffers:
                    e['reg'] = facts.constant_buffers[n]
                else:
                    required['constant_buffers'][str(n)] = {'binding': None}
                    unknown(f'hardware constant buffer c{n} has no NVN uniform binding fact')
        elif sb:
            e['kind'] = 'sbuf'
            i = int(sb.group(1))
            b = sbufs[i] if i < len(sbufs) else None
            e['rw'] = bool(b and b['flags'] & 2)
            if not b or b.get('cb_slot') != 0:
                unknown('storage buffer address is not read from c0; no binding rule is established')
            else:
                off = b['cb_offset'] * 4
                e['c0_offset'] = off
                used['storage_buffers'].add(off)
                if off in facts.storage_buffers:
                    e['reg'] = facts.storage_buffers[off]
                else:
                    required['storage_buffers'][str(off)] = {'slot': None}
                    unknown(f'storage buffer address at c0+{off:#x} has no NVN storage slot fact')
        elif th:
            kind, handle, cbuf = th
            e['handle'] = handle
            if cbuf is not None:
                e['kind'] = '?'
                unknown(f'bindless handle read from constant buffer c{cbuf}; no binding rule is established')
                continue
            table = facts.images if kind == 'img' else facts.textures
            fact = table.get(handle)
            if kind == 'samp':
                separate = (handle >> 28) != 0
                used['textures'].add(handle)
                if not fact:
                    e['kind'] = 'samp' if separate else 'samp_tex'
                    required['textures'].setdefault(str(handle), {'slot': None, 'type': None})
                    if separate:
                        required['textures'][str(handle)]['sampler_slot'] = None
                    unknown(f'sampler for handle {handle:#x} has no NVN binding fact')
                elif separate:
                    e['kind'] = 'samp'
                    if 'sampler_slot' in fact:
                        e['reg'] = fact['sampler_slot']
                    else:
                        required['textures'][str(handle)] = dict(fact, sampler_slot=None)
                        unknown(f'separate sampler for handle {handle:#x} has no sampler_slot fact')
                else:
                    # Combined handle: the sampler comes with the bound texture slot.
                    e['kind'] = 'samp_tex'
                    e['reg'] = fact['slot']
                continue
            info = (img_info if kind == 'img' else tex_info).get(handle)
            if kind == 'img':
                e['kind'] = 'img'
            else:
                e['kind'] = 'tbuf' if 'texture_buffer' in ty else 'tex'
            used['images' if kind == 'img' else 'textures'].add(handle)
            if info is None:
                conflicts.append({'what': name, 'detail': 'MSL resource has no matching translator record'})
            if not fact:
                required['images' if kind == 'img' else 'textures'][str(handle)] = (
                    {'slot': None, 'type': None, 'format': None} if kind == 'img' else {'slot': None, 'type': None})
                unknown(f'{"image" if kind == "img" else "texture"} handle {handle:#x} has no NVN binding fact')
                continue
            e['reg'] = fact['slot']
            if not ty.startswith(TEXTURE_TYPES[fact['type']]):
                conflicts.append({'what': name, 'detail': f'fact type {fact["type"]} contradicts translated MSL type {ty}'})
        else:
            e['kind'] = '?'
            unknown('unrecognized MSL resource name')
    if len(bindings) > REFL_MAX_BINDINGS:
        unknowns.append({'what': 'bindings', 'detail': f'{len(bindings)} bindings exceed the nvn_refl limit {REFL_MAX_BINDINGS}'})
    vertex_inputs = []
    if capture.stage == 0:
        for m in re.finditer(r'(\w+)\s+\w+\s*\[\[attribute\((\d+)\)\]\]', msl):
            loc, ty = int(m.group(2)), m.group(1)
            vertex_inputs.append({'location': loc, 'msl_type': ty})
            used['attributes'].add(loc)
            fact = facts.attributes.get(loc)
            if fact is None:
                required['attributes'][str(loc)] = None
                unknowns.append({'what': f'attribute {loc}', 'detail': 'vertex attribute component type has no fact; the translator assumed float'})
            elif not re.sub(r'\d+$', '', ty) in COMPONENT_TYPES[fact]:
                conflicts.append({'what': f'attribute {loc}', 'detail': f'fact {fact} contradicts MSL type {ty}'})
    if capture.stage == 1:
        out = re.search(r'struct main0_out\s*\{(.*?)\};', msl, re.S)
        for m in re.finditer(r'(\w+)\s+\w+\s*\[\[color\((\d+)\)\]\]', out.group(1) if out else ''):
            rt, ty = int(m.group(2)), m.group(1)
            used['outputs'].add(rt)
            fact = facts.outputs.get(rt)
            if fact is None:
                required['outputs'][str(rt)] = None
                unknowns.append({'what': f'output {rt}', 'detail': 'render target component type has no fact; the translator assumed float'})
            elif not re.sub(r'\d+$', '', ty) in COMPONENT_TYPES[fact]:
                conflicts.append({'what': f'output {rt}', 'detail': f'fact {fact} contradicts MSL type {ty}'})
    if capture.stage != 5:
        if facts.device_state is None:
            required['device_state'] = {k: None for k in DEVICE_STATE_VALUES}
            unknowns.append({'what': 'device_state', 'detail': 'window origin / depth mode not established; dmsh_xlate defaults to '
                             + ', '.join(f'{k}={v}' for k, v in DEFAULT_DEVICE_STATE.items())})
    if capture.stage == 5 and not facts.compute:
        required['compute'] = {'local_size': None, 'shared_memory': None}
        unknowns.append({'what': 'compute', 'detail': 'workgroup size and shared memory have no fact; the translator assumed 1x1x1 and 0'})
    if len(capture.c1) > REFL_MAX_C1:
        unknowns.append({'what': 'c1', 'detail': f'C1 segment is {len(capture.c1)} bytes, above the nvn_refl limit {REFL_MAX_C1}'})
    unused = []
    for table in ('constant_buffers', 'textures', 'images', 'storage_buffers', 'attributes', 'outputs'):
        for k in sorted(set(getattr(facts, table)) - used[table]):
            unused.append(f'{table}[{k}]')
    required = dict(required_identity, **{k: v for k, v in required.items() if v})
    return bindings, vertex_inputs, unknowns, conflicts, required, unused


def spirv_to_msl(tools, spv, msl):
    r = run([tools.spirv_cross_path, *SPIRV_CROSS_ARGS, spv, '--output', msl], 300, text=True)
    if r.returncode or not os.path.exists(msl):
        return (r.stderr or r.stdout).strip()[:400] or 'spirv-cross failed'
    return None


def metal_compile(tools, msl, lib):
    """xcrun backend: MSL -> AIR -> metallib. Returns an error string or None."""
    air = msl[:-len('.metal')] + '.air'
    try:
        for argv in (['xcrun', '-sdk', 'macosx', 'metal', '-c', msl, '-o', air],
                     ['xcrun', '-sdk', 'macosx', 'metallib', air, '-o', lib + '.tmp']):
            r = run(argv, 600, text=True)
            if r.returncode:
                return (r.stderr or r.stdout).strip()[:400]
        os.replace(lib + '.tmp', lib)
        return None
    finally:
        for p in (air, lib + '.tmp'):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def metal_check(tools, msl):
    r = run([tools.helper, msl], 300, text=True)
    try:
        value = json.loads(r.stdout)
    except ValueError:
        value = {'ok': False, 'diagnostics': (r.stderr or r.stdout).strip()[:400]}
    value['ok'] = bool(value.get('ok')) and r.returncode == 0
    return value


def reflection(capture, chain, entry, bindings, vertex_inputs, facts, route):
    return {
        'schema': REFLECTION_SCHEMA,
        'key': capture.key + '_b', 'route': route, 'title': 'botw', 'chain': chain, 'dm_ctl': capture.key,
        'sha256': capture.sha256, 'source': 'capture:' + capture.key, 'stage': XLATE_STAGE[capture.stage],
        'entry': entry[1] if entry else 'main0', 'msl_entry_kind': entry[0] if entry else None,
        'threadgroup_size': facts.compute['local_size'] if facts.compute else None,
        'c1': capture.c1.hex(), 'notes': [], 'drv_reads': {}, 'drv_unfillable': [],
        'bindings': [{k: v for k, v in b.items() if k != 'unknown'} for b in bindings],
        'vertex_inputs': vertex_inputs,
        'facts_sha256': facts.sha256,
        # Preserve compile-time device modes so the runtime can reject incompatible live state.
        'device_state': dict(facts.device_state) if facts.device_state is not None else None,
    }


def publish_ready(root, work, key, reflection_doc, result, metal_identity):
    """Publishes one runnable triple and records the two runtime artifact digests."""
    if any(result.get(name) != [] for name in ('errors', 'unknowns', 'conflicts')) or reflection_doc.get('route') != 'B':
        raise ToolError('internal error: attempted to publish a shader that did not pass every runnable gate')
    if (result.get('key') != key or reflection_doc.get('schema') != REFLECTION_SCHEMA or
            reflection_doc.get('key') != key + '_b' or reflection_doc.get('dm_ctl') != key or
            not HEX64_RE.fullmatch(reflection_doc.get('sha256', '')) or
            reflection_doc['sha256'][:16] != key or reflection_doc.get('stage') not in XLATE_STAGE.values() or
            reflection_doc.get('notes') != []):
        raise ToolError('internal error: runnable reflection identity or translator-note gate is invalid')
    capture = result.get('capture')
    if (not isinstance(capture, dict) or capture.get('key') != key or
            reflection_doc['sha256'] != capture.get('sha256') or
            reflection_doc['stage'] != XLATE_STAGE.get(capture.get('stage'))):
        raise ToolError('internal error: runnable reflection does not match its source capture')
    for name in (key + '_b.metallib', key + '_b.metal'):
        os.replace(os.path.join(work, name), os.path.join(root, name))
    reflection_path = os.path.join(root, key + '_b.json')
    write_json(reflection_path, dict(reflection_doc, runnable=True, unknowns=[], conflicts=[],
                                     chain_metal=metal_identity))
    metallib_path = os.path.join(root, key + '_b.metallib')
    msl_path = os.path.join(root, key + '_b.metal')
    result['files'] = {
        'metallib': {'path': metallib_path, 'sha256': file_sha256(metallib_path)},
        'json': {'path': reflection_path, 'sha256': file_sha256(reflection_path)},
        'msl': {'path': msl_path},
    }
    result['runnable'] = True
    result['status'] = 'ready'


def result_is_runnable(result, output):
    key = result.get('key')
    capture = result.get('capture')
    files = result.get('files')
    if (not isinstance(key, str) or not KEY_RE.fullmatch(key) or result.get('status') != 'ready' or
            result.get('runnable') is not True or any(result.get(name) != [] for name in ('errors', 'unknowns', 'conflicts')) or
            not isinstance(capture, dict) or capture.get('key') != key or
            not HEX64_RE.fullmatch(capture.get('sha256', '')) or capture['sha256'][:16] != key or
            capture.get('stage_name') not in ('vertex', 'fragment', 'compute') or
            capture.get('stage') != {'vertex': 0, 'fragment': 1, 'compute': 5}.get(capture.get('stage_name')) or
            not isinstance(files, dict)):
        return False
    expected = {'metallib': key + '_b.metallib', 'json': key + '_b.json'}
    for kind, name in expected.items():
        metadata = files.get(kind)
        if (not isinstance(metadata, dict) or metadata.get('path') != os.path.join(output, name) or
                not HEX64_RE.fullmatch(metadata.get('sha256', ''))):
            return False
    return True


def make_manifest(mode, chain, options, tools, captures, facts, output, results,
                  argv=None, timestamp=None):
    ordered = list(results)
    keys = [r.get('key') for r in ordered]
    runnable = (bool(ordered) and mode == 'build' and isinstance(chain, str) and
                re.fullmatch(r'[0-9a-f]{12}', chain) is not None and isinstance(output, str) and
                os.path.basename(output) == 'botw-b-' + chain and
                all(result_is_runnable(r, output) for r in ordered) and len(set(keys)) == len(keys))
    return {
        'schema': MANIFEST_SCHEMA,
        'runnable': runnable,
        'tool': TOOL_PATH,
        'mode': mode,
        'argv': list(sys.argv if argv is None else argv),
        'time': timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'chain': chain,
        'options': options,
        'spirv_cross_args': SPIRV_CROSS_ARGS,
        'tools': tools,
        'captures': captures,
        'facts': facts,
        'output': output,
        'results': ordered,
    }


# Commands -------------------------------------------------------------------

def select_keys(captures, keys):
    complete, incomplete = capture_keys(captures)
    if incomplete:
        print(f'note: {len(incomplete)} incomplete capture(s) without metadata ignored: {" ".join(incomplete)}')
    chosen = keys or complete
    if not chosen:
        raise ToolError(f'no complete captures in {captures}')
    return chosen


def check_capture_dir(path):
    if not os.path.isabs(path):
        raise ToolError(f'capture directory must be an absolute path: {path}')
    if not os.path.isdir(path):
        raise ToolError(f'capture directory not found: {path}')
    require_outside_git(path, 'capture directory')


def cmd_validate(a):
    check_capture_dir(a.captures)
    bad = 0
    for key in select_keys(a.captures, a.keys):
        try:
            c = load_capture(a.captures, key)
            f = c.fields
            print(f'ok   {key} {c.stage_name} v{f["version"]} code={f["code_size"]:#x} '
                  f'code_offset={f["code_offset"]:#x} c1={f["c1_offset"]:#x}+{f["c1_size"]:#x} sha256={c.sha256}')
        except CaptureError as e:
            bad += 1
            print(f'FAIL {key}: {e}')
    return 1 if bad else 0


def cmd_facts(a):
    check_capture_dir(a.captures)
    bad = 0
    for key in select_keys(a.captures, a.keys):
        try:
            c = load_capture(a.captures, key)
            path = os.path.join(a.facts, key + '.facts.json')
            if not os.path.exists(path):
                raise FactsError(f'missing {path}')
            f = load_facts(path, c)
            print(f'ok   {key} {c.stage_name} facts sha256={f.sha256[:16]} provenance={f.provenance!r}')
        except (CaptureError, FactsError) as e:
            bad += 1
            print(f'FAIL {key}: {e}')
    return 1 if bad else 0


def cmd_tools(a):
    tools = Tools(a.xlate, a.spirv_cross, a.metal, a.helper_dir)
    report = {}
    for name, fn in (('xlate', tools.probe_xlate), ('spirv_cross', tools.probe_spirv_cross), ('metal', tools.probe_metal)):
        try:
            report[name] = fn()
        except ToolError as e:
            report[name] = {'error': str(e)}
    print(json.dumps(report, indent=1))
    return 0 if all('error' not in v for v in report.values()) else 1


def cmd_build(a):
    check_capture_dir(a.captures)
    if not a.probe and not a.facts:
        raise ToolError('runnable builds require --facts; use --probe for non-runnable diagnostics')
    out = make_private_dir(a.out, 'output directory')
    options = {'xlate_debug': a.xlate_debug, 'xlate_reduced': a.xlate_reduced}
    tools = Tools(a.xlate, a.spirv_cross, 'xcrun' if not a.probe else a.metal, os.path.join(out, '.tools'))
    tools.probe_xlate()
    tools.probe_spirv_cross()
    tools.probe_metal()
    if not a.probe and not tools.identity['xlate']['built_from_repo_source']:
        raise ToolError('translator was not built from this repository\'s dmsh_xlate sources; rebuild it')
    chain = tools.chain(options)
    mode = 'probe' if a.probe else 'build'
    root = os.path.join(out, f'probe-{chain}' if a.probe else f'botw-b-{chain}')
    os.makedirs(root, mode=0o700, exist_ok=True)
    keys = select_keys(a.captures, a.keys)
    print(f'{mode}: {len(keys)} shader(s), chain {chain}, metal backend {tools.metal or "none"} -> {root}')

    results, captures, facts_of, jobs = {}, {}, {}, []
    work = tempfile.mkdtemp(prefix='.work-', dir=out)
    try:
        for key in keys:
            r = results[key] = {'key': key, 'status': None, 'runnable': False,
                                'errors': [], 'unknowns': [], 'conflicts': [], 'files': {}}
            try:
                c = captures[key] = load_capture(a.captures, key)
            except CaptureError as e:
                r.update(status='failed', errors=[f'capture: {e}'])
                continue
            r['capture'] = c.summary()
            if c.stage not in XLATE_STAGE:
                r.update(status='failed', errors=[f'stage {c.stage_name} is not supported by this pipeline'])
                continue
            path = os.path.join(a.facts, key + '.facts.json') if a.facts else None
            try:
                if path and os.path.exists(path):
                    facts_of[key] = load_facts(path, c)
                elif a.probe:
                    facts_of[key] = Facts()
                    r['unknowns'].append({'what': 'facts', 'detail': 'no facts file; every binding is unresolved'})
                else:
                    raise FactsError(f'missing facts file {path}')
            except FactsError as e:
                r.update(status='failed', errors=[f'facts: {e}'])
                continue
            r['facts'] = {'path': facts_of[key].path, 'sha256': facts_of[key].sha256, 'provenance': facts_of[key].provenance}
            code_path = os.path.join(work, key + '.code.bin')
            write_atomic(code_path, c.translator_code)
            jobs.append(make_job(c, facts_of[key], code_path, options))
        xerr = translate(tools, jobs, work) if jobs else {}
        for job in jobs:
            key = job['key']
            c, f, r = captures[key], facts_of[key], results[key]
            dest = os.path.join(root, key) if a.probe else work
            os.makedirs(dest, mode=0o700, exist_ok=True)
            if xerr[key]:
                r['status'] = 'failed'
                r['errors'].append('translation failed')
                r['translator'] = xerr[key]
                continue
            spv, res_path = os.path.join(work, key + '.spv'), os.path.join(work, key + '.res.json')
            res = json.load(open(res_path))
            msl = os.path.join(dest, key + ('.metal' if a.probe else '_b.metal'))
            err = spirv_to_msl(tools, spv, msl)
            if err:
                r['status'] = 'failed'
                r['errors'].append(f'spirv-cross: {err}')
                continue
            os.chmod(msl, 0o600)
            src = open(msl).read()
            bindings, vin, unknowns, conflicts, required, unused = analyze(c, f, res, src)
            r['unknowns'] += unknowns
            r['conflicts'] = conflicts
            r['unused_facts'] = unused
            entry = re.search(r'(vertex|fragment|kernel)\s+\S+\s+(\w+)\s*\(', src)
            entry = (entry.group(1), entry.group(2)) if entry else None
            if a.probe:
                for path, name in ((spv, key + '.spv'), (res_path, key + '.res.json')):
                    shutil.copyfile(path, os.path.join(dest, name))
                    os.chmod(os.path.join(dest, name), 0o600)
                r['files'].update(spv=os.path.join(dest, key + '.spv'), msl=msl)
            lib = os.path.join(dest, key + ('.probe.metallib' if a.probe else '_b.metallib'))
            if tools.metal == 'xcrun':
                err = metal_compile(tools, msl, lib)
                r['metal'] = {'backend': 'xcrun', 'ok': err is None, 'error': err}
                if err is None and a.probe:
                    os.chmod(lib, 0o600)
                    r['files']['metallib'] = lib
            elif tools.metal == 'runtime':
                r['metal'] = dict(metal_check(tools, msl), backend='runtime', metallib=False)
                for fn in r['metal'].get('functions', []):
                    for va in fn.get('vertex_attributes', []):
                        fact = f.attributes.get(va['index'])
                        if fact and MTL_SCALAR.get(va['data_type']) not in (fact, None):
                            conflicts.append({'what': f'attribute {va["index"]}', 'detail': f'Metal reflects data type {va["data_type"]}, fact is {fact}'})
            if r.get('metal') and not r['metal']['ok']:
                r['errors'].append('metal compilation failed')
            refl = reflection(c, chain, entry, bindings, vin, f, 'probe' if a.probe else 'B')
            if a.probe:
                refl.update(runnable=False, unknowns=r['unknowns'], conflicts=conflicts, required_facts=required,
                            metal=r.get('metal'), translator_notes=res.get('notes', []))
                refl['bindings'] = bindings
                path = os.path.join(dest, key + '.probe.json')
                write_json(path, refl)
                r['files']['probe_json'] = path
                r['required_facts'] = required
                r['status'] = 'failed' if r['errors'] else 'probe'
                continue
            if r['errors'] or r['unknowns'] or conflicts:
                r['status'] = 'failed'
                continue
            # Every gate passed: publish the runnable triple.
            publish_ready(root, work, key, refl, r, tools.identity['metal']['xcrun_metal'])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    manifest = make_manifest(mode, chain, options, tools.identity,
                             os.path.realpath(a.captures), os.path.realpath(a.facts) if a.facts else None,
                             root, [results[k] for k in keys])
    write_json(os.path.join(root, f'manifest-{mode}.json'), manifest)
    failed = 0
    for key in keys:
        r = results[key]
        print(f'{r["status"]:7} {key} ' + ', '.join(filter(None, [
            f'{len(r["unknowns"])} unknown' if r['unknowns'] else '',
            f'{len(r["conflicts"])} conflict' if r['conflicts'] else '',
            '; '.join(r['errors'])])))
        for u in r['unknowns']:
            print(f'          unknown {u["what"]}: {u["detail"]}')
        for u in r['conflicts']:
            print(f'          conflict {u["what"]}: {u["detail"]}')
        failed += r['status'] == 'failed'
    print(f'manifest: {os.path.join(root, f"manifest-{mode}.json")}')
    if a.probe:
        print('probe artifacts are not runnable and use route "probe"; nothing was published for the runtime')
    return 1 if failed else 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog='botw_shader_build.py', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Offline BotW shader build from runtime captures and explicit per-shader facts.',
        epilog=FACTS_HELP + '\nExit status: 0 success, 1 a capture/facts/shader gate failed, 2 usage or tool error.')
    sub = p.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('validate', help='verify captures against the runtime contract')
    v.add_argument('captures', help='absolute capture directory (DM_SHADER_CAPTURE)')
    v.add_argument('keys', nargs='*', help='capture keys (default: every complete capture)')
    f = sub.add_parser('facts', help='verify facts files against their captures')
    f.add_argument('captures')
    f.add_argument('facts', help='directory holding <key>.facts.json')
    f.add_argument('keys', nargs='*')

    def tool_args(q):
        q.add_argument('--xlate', default=os.environ.get('BOTW_XLATE'),
                       help='dmsh_xlate executable or dmsh_xlate.dll (env BOTW_XLATE)')
        q.add_argument('--spirv-cross', default=os.environ.get('SPIRV_CROSS'),
                       help='spirv-cross executable (env SPIRV_CROSS, else PATH)')
    t = sub.add_parser('tools', help='print translator, spirv-cross and Metal identity')
    tool_args(t)
    t.add_argument('--metal', choices=('auto', 'xcrun', 'runtime', 'none'), default='auto')
    t.add_argument('--helper-dir', default=os.path.join(tempfile.gettempdir(), 'botw-shader-build-tools'),
                   help='where the Metal runtime check helper is built')
    b = sub.add_parser('build', help='translate and compile captures', formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog='Runnable mode requires facts for every shader and Apple metal/metallib.\n'
                              '--probe writes non-runnable diagnostics; --metal selects the probe backend\n'
                              '(runtime = Metal.framework source compile check, no metallib).')
    b.add_argument('captures')
    b.add_argument('keys', nargs='*')
    b.add_argument('--facts', help='directory holding <key>.facts.json (required unless --probe)')
    b.add_argument('--out', required=True, help='absolute output directory outside any Git work tree')
    b.add_argument('--probe', action='store_true', help='non-runnable diagnostic build')
    b.add_argument('--metal', choices=('auto', 'xcrun', 'runtime', 'none'), default='auto',
                   help='probe Metal backend (runnable builds always require xcrun)')
    b.add_argument('--xlate-debug', action='store_true', help='keep translator DebugMode (skips its AST optimizer)')
    b.add_argument('--xlate-reduced', action='store_true', help='relaxed precision (NoContraction only where required)')
    tool_args(b)
    a = p.parse_args(argv)
    try:
        return {'validate': cmd_validate, 'facts': cmd_facts, 'tools': cmd_tools, 'build': cmd_build}[a.cmd](a)
    except ToolError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
