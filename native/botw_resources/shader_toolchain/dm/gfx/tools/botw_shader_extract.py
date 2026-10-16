#!/usr/bin/env python3
"""Offline BotW shader extraction: every shader stage shipped in the RomFS and
the ExeFS modules -> a capture directory in the exact runtime capture format.

The host writes one capture per shader the game hands to NVN
(dm/gfx/nvn/botw_shader.c, DM_SHADER_CAPTURE): <key>.control, <key>.code and
<key>.json. This tool produces the same three files from the game's own shader
containers, so `botw_shader_build.py validate` accepts them unchanged and the
same key names the same bytes whether it was observed at run time or extracted
here. Every stage passes the runtime acceptance checks (parse_control,
check_code) before it is written.

Sources (a full scan, not relying on file names)
  Every RomFS file. Yaz0 streams are decompressed and SARC archives are
  expanded recursively. Every ExeFS module given with --exefs is decoded from
  its NSO image (needs the lz4 module; use the dm venv python).
  BNSH containers ('BNSH\\0\\0\\0\\0' followed by a 'grsc' block at +0x60)
  are read wherever they occur: +0x7c variation count (u32), +0x80 variation
  table pointer. A variation is 0x40 bytes with its binary program pointer at
  +0x10; the program holds six stage pointers from +0x08 in the order vertex,
  hull, domain, geometry, pixel, compute. A stage descriptor holds the control
  pointer at +0x08, the code pointer at +0x10, the code size at +0x18 and the
  control size at +0x1c. All pointers are relative to the BNSH start. This is
  the layout nxnative's shader_split.py reads for other NVN titles.

Outputs (the output directory must be outside any Git work tree)
  <out>/<key>.{control,code,json}   one capture per unique shader stage
  <out>/extract-index.json          sources, variations and summary
  <out>/pairs.tsv                   vertex key <TAB> pixel key per variation

--compare DIR checks a runtime capture directory against the extracted set and
records which runtime captures were and were not found.

Exit status: 0 success, 1 a container or shader failed the contract, 2 usage
or tool error.
"""
import argparse
import collections
import concurrent.futures as cf
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # keep bytecode caches out of the repository
TOOL_PATH = os.path.abspath(__file__)
TOOLS_DIR = os.path.dirname(TOOL_PATH)
YAZ0_SOURCE = os.path.join(TOOLS_DIR, 'botw_yaz0.c')

_spec = importlib.util.spec_from_file_location('botw_shader_build', os.path.join(TOOLS_DIR, 'botw_shader_build.py'))
bsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsb)

INDEX_SCHEMA = 'botw-shader-extract-v1'
BNSH_MAGIC = b'BNSH\0\0\0\0'
BNSH_SLOTS = ('vs', 'hs', 'ds', 'gs', 'ps', 'cs')
# BNSH stage slot -> NVN stage number stored at control +0x714.
SLOT_STAGE = {'vs': 0, 'hs': 3, 'ds': 4, 'gs': 2, 'ps': 1, 'cs': 5}
STAGE_SLOT = {v: k for k, v in SLOT_STAGE.items()}
MAX_DEPTH = 8
EXEFS_MODULES = ('main', 'subsdk0', 'sdk')
SOURCES_KEPT = 4


class ExtractError(Exception):
    pass


# Yaz0 -------------------------------------------------------------------------

def yaz0_size(data):
    if len(data) < 16 or data[:4] != b'Yaz0':
        raise ExtractError('malformed Yaz0 header')
    return struct.unpack_from('>I', data, 4)[0]


def yaz0_python(data):
    """Reference decoder; the C helper must agree with it byte for byte."""
    want, out, i = yaz0_size(data), bytearray(), 16
    while len(out) < want:
        if i >= len(data):
            raise ExtractError('truncated Yaz0 stream')
        flags = data[i]; i += 1
        for bit in range(7, -1, -1):
            if len(out) >= want:
                break
            if flags >> bit & 1:
                if i >= len(data):
                    raise ExtractError('truncated Yaz0 stream')
                out.append(data[i]); i += 1
                continue
            if i + 2 > len(data):
                raise ExtractError('truncated Yaz0 stream')
            b1, b2 = data[i], data[i + 1]; i += 2
            dist, n = ((b1 & 0xf) << 8 | b2) + 1, b1 >> 4
            if n == 0:
                if i >= len(data):
                    raise ExtractError('truncated Yaz0 stream')
                n = data[i] + 0x12; i += 1
            else:
                n += 2
            if dist > len(out):
                raise ExtractError('Yaz0 back-reference before the start of the output')
            for _ in range(min(n, want - len(out))):
                out.append(out[-dist])
    return bytes(out)


_YAZ0_ERRORS = {-1: 'malformed Yaz0 header', -2: 'Yaz0 output buffer too small', -3: 'truncated Yaz0 stream',
                -4: 'Yaz0 back-reference before the start of the output'}


class Yaz0Native:
    def __init__(self, path):
        self.path = path
        self.lib = ctypes.CDLL(path)
        self.fn = self.lib.botw_yaz0_decode
        self.fn.restype = ctypes.c_long
        self.fn.argtypes = (ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t)

    def __call__(self, data):
        want = yaz0_size(data)
        buf = ctypes.create_string_buffer(want)
        n = self.fn(data, len(data), buf, want)
        if n < 0:
            raise ExtractError(_YAZ0_ERRORS.get(n, f'Yaz0 decoder error {n}'))
        if n != want:
            raise ExtractError(f'Yaz0 stream decoded to {n} bytes, header says {want}')
        return buf.raw


def build_yaz0(helper_dir):
    """Compiles botw_yaz0.c into a shared library named by its source digest."""
    with open(YAZ0_SOURCE, 'rb') as f:
        src = f.read()
    os.makedirs(helper_dir, mode=0o700, exist_ok=True)
    lib = os.path.join(helper_dir, f'botw_yaz0-{hashlib.sha256(src).hexdigest()[:16]}.dylib')
    if not os.path.exists(lib):
        r = subprocess.run(['xcrun', 'clang', '-O2', '-shared', '-fPIC', YAZ0_SOURCE, '-o', lib + '.tmp'],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise ExtractError('cannot build the Yaz0 helper: ' + (r.stderr or r.stdout).strip()[:300])
        os.replace(lib + '.tmp', lib)
    return lib


# Containers -----------------------------------------------------------------

def sarc_members(data, label):
    """Yields (name, bytes) for each SARC member, with every offset bounds-checked."""
    if len(data) < 0x14:
        raise ExtractError(f'{label}: truncated SARC header')
    header_size, bom = struct.unpack_from('<HH', data, 4)
    if bom != 0xfeff:
        raise ExtractError(f'{label}: big-endian SARC is not supported')
    data_start = struct.unpack_from('<I', data, 0xc)[0]
    sfat = header_size
    if data[sfat:sfat + 4] != b'SFAT':
        raise ExtractError(f'{label}: SARC has no SFAT block')
    count = struct.unpack_from('<H', data, sfat + 6)[0]
    nodes = sfat + 12
    sfnt = nodes + 16 * count
    if data[sfnt:sfnt + 4] != b'SFNT':
        raise ExtractError(f'{label}: SARC has no SFNT block')
    names = sfnt + 8
    for i in range(count):
        _, attr, start, end = struct.unpack_from('<IIII', data, nodes + 16 * i)
        if attr >> 24:
            at = names + (attr & 0xffffff) * 4
            stop = data.find(b'\0', at)
            name = data[at:stop if stop >= 0 else len(data)].decode('utf-8', 'replace')
        else:
            name = f'#{i}'
        if not start <= end or data_start + end > len(data):
            raise ExtractError(f'{label}/{name}: SARC member is outside the archive')
        yield name, data[data_start + start:data_start + end]


def bnsh_stages(data, base, label):
    """Every stage of every variation of the BNSH at data[base:]."""
    if data[base + 0x60:base + 0x64] != b'grsc':
        raise ExtractError(f'{label} BNSH @{base:#x}: no grsc block')

    def u64(offset):
        if offset < 0 or offset + 8 > len(data):
            raise ExtractError(f'{label} BNSH @{base:#x}: pointer table outside the container')
        return struct.unpack_from('<Q', data, offset)[0]

    nvar = struct.unpack_from('<I', data, base + 0x7c)[0]
    table = u64(base + 0x80)
    stages, variations = [], []
    for v in range(nvar):
        program = u64(base + table + 0x40 * v + 0x10)
        if not program:
            continue
        where = f'{label}#BNSH@{base:#x}#var{v}'
        variation = {}
        for si, slot in enumerate(BNSH_SLOTS):
            desc = u64(base + program + 8 + 8 * si)
            if not desc:
                continue
            at = base + desc + 8
            if at + 0x18 > len(data):
                raise ExtractError(f'{where} {slot}: stage descriptor outside the container')
            pctl, pcode, ncode, nctl = struct.unpack_from('<QQII', data, at)
            c0, k0 = base + pctl, base + pcode
            if c0 + nctl > len(data) or k0 + ncode > len(data):
                raise ExtractError(f'{where} {slot}: control or code outside the container')
            control, code = data[c0:c0 + nctl], data[k0:k0 + ncode]
            try:
                fields = bsb.parse_control(control)
                if len(code) != fields['code_size']:
                    raise bsb.CaptureError(f'code is {len(code):#x} bytes, control says {fields["code_size"]:#x}')
                bsb.check_code(fields, code)
            except bsb.CaptureError as e:
                raise ExtractError(f'{where} {slot}: {e}')
            if fields['stage'] != SLOT_STAGE[slot]:
                raise ExtractError(f'{where}: {slot} slot holds a control for stage {fields["stage"]}')
            sha = bsb.capture_digest(fields, control, code)
            stages.append((where, sha, fields, control, code))
            variation[slot] = sha[:16]
        if variation:
            variations.append((where, variation))
    return stages, variations


def bahs_stages(data, label):
    """Every program stage of an agl shader archive (BAHS, .sharcb).

    Header +0x18 is the code region size and +0x1c its start. Each binary entry
    in the table area is 16 bytes: {code offset in the code region (0x100
    aligned), 0, control size, signed offset from the entry to its control}.
    Programs share control sections, so stages are taken per entry and never
    by searching for code near a control. Entries are recognized by the zero
    word, the alignment, the control magic and a control size equal to the
    size the control itself records; a candidate that then fails the runtime
    contract is counted, not used."""
    if len(data) < 0x20:
        raise ExtractError(f'{label}: truncated BAHS header')
    size, region = struct.unpack_from('<II', data, 0x18)
    if region > len(data) or size > len(data) - region:
        raise ExtractError(f'{label}: BAHS code region is outside the archive')
    stages, rejected, seen = [], 0, set()
    magic = struct.pack('<I', bsb.SHADER_MAGIC)
    for a in range(0, region - 16 + 1, 4):
        off, zero, nctl, rel = struct.unpack_from('<IIIi', data, a)
        c = a + rel
        if (zero or off % 0x100 or not bsb.CONTROL_HEADER_SIZE <= nctl <= bsb.CONTROL_LIMIT or
                c < 0 or c + nctl > region or data[c:c + 4] != magic or
                struct.unpack_from('<I', data, c + 0x20)[0] != nctl):
            continue
        control = data[c:c + nctl]
        try:
            fields = bsb.parse_control(control)
            if off + fields['code_size'] > size:
                raise bsb.CaptureError('code is outside the code region')
            code = data[region + off:region + off + fields['code_size']]
            bsb.check_code(fields, code)
        except bsb.CaptureError:
            rejected += 1
            continue
        if (c, off) in seen:
            continue
        seen.add((c, off))
        stages.append((f'{label}#BAHS@{a:#x}', bsb.capture_digest(fields, control, code), fields, control, code))
    return stages, rejected


class Found:
    """Stages, BNSH variations and rejected BAHS entry candidates of one input."""
    def __init__(self):
        self.stages, self.variations, self.rejected = [], [], {}


def scan_blob(data, label, yaz0, found, depth=0):
    if depth > MAX_DEPTH:
        raise ExtractError(f'{label}: containers nested deeper than {MAX_DEPTH}')
    if data[:4] == b'Yaz0':
        try:
            data = yaz0(data)
        except ExtractError as e:
            raise ExtractError(f'{label}: {e}')
        return scan_blob(data, label, yaz0, found, depth + 1)
    if data[:4] == b'SARC':
        for name, member in sarc_members(data, label):
            scan_blob(member, f'{label}/{name}', yaz0, found, depth + 1)
        return
    if data[:4] == b'BAHS':
        s, rejected = bahs_stages(data, label)
        found.stages.extend(s)
        if rejected:
            found.rejected[label] = rejected
        return
    for m in re.finditer(re.escape(BNSH_MAGIC), data):
        if data[m.start() + 0x60:m.start() + 0x64] != b'grsc':
            continue  # the magic alone is eight bytes; require the grsc block too
        s, v = bnsh_stages(data, m.start(), label)
        found.stages.extend(s); found.variations.extend(v)


# Workers --------------------------------------------------------------------

_YAZ0 = None


def _init_worker(lib_path):
    global _YAZ0
    _YAZ0 = Yaz0Native(lib_path) if lib_path else yaz0_python


def _unique(stages, label):
    out = {}
    for where, sha, fields, control, code in stages:
        old = out.get(sha[:16])
        if old and old[0] != sha:
            raise ExtractError(f'{label}: key {sha[:16]} names two different shaders')
        if old:
            old[4] += 1
        else:
            out[sha[:16]] = [sha, fields, control, code, 1, where]
    return out


def scan_file(job):
    """-> (label, size, {key: [sha, fields, control, code, count, first]}, variations, rejected, error)."""
    path, label = job
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:4] not in (b'Yaz0', b'SARC', b'BAHS') and BNSH_MAGIC not in data:
            return label, len(data), {}, [], {}, None
        found = Found()
        scan_blob(data, label, _YAZ0, found)
        return label, len(data), _unique(found.stages, label), found.variations, found.rejected, None
    except (ExtractError, OSError) as e:
        return label, 0, {}, [], {}, str(e)


def scan_module(path, label):
    sys.path.insert(0, os.path.join(TOOLS_DIR, '../../verify'))
    try:
        import nsoimg
    except ImportError as e:
        raise ExtractError(f'decoding NSO modules needs lz4 ({e}); run with the dm venv python')
    image = nsoimg.load(path)
    found = Found()
    scan_blob(image, label, _YAZ0, found)
    return label, len(image), _unique(found.stages, label), found.variations, found.rejected, None


# Output ---------------------------------------------------------------------

def write_capture(out, key, sha, fields, control, code):
    """Writes one capture; an existing capture must already be byte-identical."""
    files = ((key + '.control', control), (key + '.code', code),
             (key + '.json', bsb.canonical_capture_json(key, sha, fields, bsb.sha256_hex(control),
                                                        bsb.sha256_hex(code)).encode()))
    for name, data in files:
        path = os.path.join(out, name)
        if os.path.lexists(path):
            with open(path, 'rb') as f:
                if f.read() != data:
                    raise ExtractError(f'existing {name} differs from the extracted shader')
            continue
        bsb.write_atomic(path, data)


def group_of(label):
    parts = label.split('/')
    return parts[0] if parts[0].startswith('exefs:') else '/'.join(parts[:2]) if parts[0] == 'Pack' else parts[0]


def compare(captures_dir, extracted):
    complete, incomplete = bsb.capture_keys(captures_dir)
    found, missing, invalid = [], [], []
    for key in complete:
        try:
            cap = bsb.load_capture(captures_dir, key)
        except bsb.CaptureError as e:
            invalid.append({'key': key, 'error': str(e)})
            continue
        (found if extracted.get(key) == cap.sha256 else missing).append(key)
    return {'captures': os.path.abspath(captures_dir), 'complete': len(complete), 'incomplete': len(incomplete),
            'found': len(found), 'missing': missing, 'invalid': invalid}


def main(argv=None):
    p = argparse.ArgumentParser(prog='botw_shader_extract.py', formatter_class=argparse.RawDescriptionHelpFormatter,
                                description=__doc__.split('\n\n')[0])
    p.add_argument('--romfs', required=True, help='merged 1.9.0 RomFS directory')
    p.add_argument('--exefs', help='ExeFS directory holding the original main / subsdk0 / sdk NSOs')
    p.add_argument('--out', required=True, help='absolute output directory outside any Git work tree')
    p.add_argument('--compare', help='runtime capture directory (DM_SHADER_CAPTURE) to check coverage against')
    p.add_argument('--helper-dir', default=os.path.join(tempfile.gettempdir(), 'botw-shader-build-tools'),
                   help='where the Yaz0 helper library is built')
    p.add_argument('--python-yaz0', action='store_true', help='use the pure Python Yaz0 decoder (slow)')
    p.add_argument('-j', type=int, default=os.cpu_count() or 4, help='worker processes')
    a = p.parse_args(argv)
    try:
        out = bsb.make_private_dir(a.out, 'output directory')
        romfs = os.path.realpath(a.romfs)
        if not os.path.isdir(romfs):
            raise ExtractError(f'RomFS directory not found: {a.romfs}')
        lib = None if a.python_yaz0 else build_yaz0(a.helper_dir)
        _init_worker(lib)
        files = sorted((os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), romfs))
                       for dp, _, fs in os.walk(romfs) for f in fs)
        results = []
        if a.exefs:
            for module in EXEFS_MODULES:
                path = os.path.join(a.exefs, module)
                if os.path.exists(path):
                    results.append(scan_module(path, 'exefs:' + module))
        if a.j <= 1:
            results += [scan_file(f) for f in files]
        else:
            with cf.ProcessPoolExecutor(a.j, initializer=_init_worker, initargs=(lib,)) as ex:
                results += list(ex.map(scan_file, files, chunksize=8))
    except (ExtractError, bsb.ToolError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2

    errors = [{'source': r[0], 'error': r[5]} for r in results if r[5]]
    rejected = {k: n for r in results for k, n in r[4].items()}
    shaders, by_group, scanned = {}, collections.Counter(), 0
    for label, size, uniq, _, _, _ in results:
        scanned += size
        for key, (sha, fields, control, code, count, first) in uniq.items():
            entry = shaders.get(key)
            if entry and entry['sha256'] != sha:
                errors.append({'source': first, 'error': f'key {key} names two different shaders'})
                continue
            if not entry:
                entry = shaders[key] = {'sha256': sha, 'stage': STAGE_SLOT[fields['stage']], 'version': fields['version'],
                                        'control_size': fields['control_size'], 'code_size': fields['code_size'],
                                        'sources': [], 'source_count': 0, '_bytes': (fields, control, code)}
                by_group[group_of(label)] += 1
            entry['source_count'] += count
            if len(entry['sources']) < SOURCES_KEPT:
                entry['sources'].append(first)
    for key in sorted(shaders):
        fields, control, code = shaders[key].pop('_bytes')
        try:
            write_capture(out, key, shaders[key]['sha256'], fields, control, code)
        except (ExtractError, OSError) as e:
            errors.append({'source': key, 'error': str(e)})

    variations, pairs = collections.Counter(), set()
    for _, _, _, var, _, _ in results:
        for _, stages in var:
            variations[tuple(sorted(stages.items()))] += 1
            if 'vs' in stages and 'ps' in stages:
                pairs.add((stages['vs'], stages['ps']))
    stage_counts = collections.Counter(s['stage'] for s in shaders.values())
    index = {
        'schema': INDEX_SCHEMA,
        'tool_sha256': bsb.file_sha256(TOOL_PATH),
        'capture_schema': bsb.SCHEMA,
        'inputs': {'romfs': romfs, 'exefs': os.path.realpath(a.exefs) if a.exefs else None,
                   'files': len(files), 'bytes_scanned': scanned, 'yaz0': 'native' if lib else 'python'},
        'summary': {'shaders': len(shaders), 'stages': dict(sorted(stage_counts.items())),
                    'groups': dict(by_group.most_common()), 'variations': sum(variations.values()),
                    'unique_variations': len(variations), 'pairs': len(pairs), 'errors': len(errors),
                    'rejected_bahs_candidates': sum(rejected.values())},
        'errors': errors,
        'rejected_bahs_candidates': rejected,
        'shaders': {k: shaders[k] for k in sorted(shaders)},
        'variations': [{'stages': dict(k), 'count': n} for k, n in sorted(variations.items())],
    }
    if a.compare:
        try:
            index['compare'] = compare(a.compare, {k: v['sha256'] for k, v in shaders.items()})
        except OSError as e:
            print(f'error: cannot read {a.compare}: {e}', file=sys.stderr)
            return 2
    bsb.write_atomic(os.path.join(out, 'pairs.tsv'),
                     '# vertex key <TAB> pixel key, one row per BNSH variation pair (botw_shader_extract.py)\n' +
                     ''.join(f'{v}\t{q}\n' for v, q in sorted(pairs)))
    bsb.write_json(os.path.join(out, 'extract-index.json'), index)

    s = index['summary']
    print(f'shaders {s["shaders"]} (' + ', '.join(f'{k} {n}' for k, n in s['stages'].items()) + f'); '
          f'{s["variations"]} variations, {s["pairs"]} vertex/pixel pairs; scanned {len(files)} files, '
          f'{scanned / 1e9:.1f} GB')
    for g, n in by_group.most_common(12):
        print(f'  {n:6d}  {g}')
    if a.compare:
        c = index['compare']
        print(f'runtime captures: {c["found"]} of {c["complete"]} found, {len(c["missing"])} missing, '
              f'{len(c["invalid"])} invalid')
    for e in errors[:20]:
        print(f'  error {e["source"]}: {e["error"]}', file=sys.stderr)
    print(f'index: {os.path.join(out, "extract-index.json")}')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
