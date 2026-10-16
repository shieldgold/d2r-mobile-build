#!/usr/bin/env python3
"""Rule-derived BotW shader facts.

Fills each probe's required facts (botw_shader_build.py build --probe) with
explicit rules and writes <key>.facts.json files that botw_shader_build.py
build accepts. Established facts files (from targeted runtime observations)
are copied unchanged, take precedence, and are compared with what the rules
would derive; differences are reported. Anything the rules cannot decide is
left unresolved so the build still reports it.

The rules live outside botw_shader_build.py on purpose: the build's chain
covers the translation tools, not the facts, which every artifact already
binds by digest.

Usage: botw_shader_derive.py <captures> [keys...] --probe-manifest <manifest-probe.json>
                             --facts-out <dir> [--established <dir>] [--keys-file <file>]
"""
import argparse
import importlib.util
import json
import os
import re
import struct
import sys

sys.dont_write_bytecode = True  # keep bytecode caches out of the repository
_spec = importlib.util.spec_from_file_location(
    'botw_shader_build', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'botw_shader_build.py'))
bsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsb)

# Rules that turn a probe's required facts into a facts file without a runtime
# observation. Every rule here was checked against the facts established by
# targeted BotW 1.9.0 draw observations (see `derive --established`); anything
# the rules cannot decide stays unresolved, so the build still reports it.

DERIVE_RULES = 'botw-derive-v5'
# NVN driver texture handle table in bank c2: 8 bytes per texture slot from
# byte 0x20. The established facts map handle words 8, 10, 12, 14 to texture
# slots 0, 1, 2, 3; nxnative measures the same base over full NVN libraries.
DERIVE_TEX_BASE, DERIVE_TEX_SLOTS = 0x20, 32
# Hardware constant buffer cN (N >= 3) is NVN uniform binding N - 3.
DERIVE_CBUF_BASE = 3
# Every established graphics fact records this device state; the runtime still
# rejects an artifact whose modes differ from the live device.
DERIVE_DEVICE_STATE = {'window_origin_mode': 'upper_left', 'depth_mode': 'near_is_minus_w'}
# Storage buffer r's address is at c0 + base + 16 r for 16 slots: vertex 0x110, fragment 0x510. The same
# table is measured on D2R (dm/gfx/tools/maxwell_refs.py) and on full NVN libraries by nxnative, and the
# BotW vertex shaders that read storage buffers read exactly c0 + 0x110. Compute shaders also read
# uniform buffer pointers from c0, which the build does not resolve yet, so compute is left out.
DERIVE_SSBO_BASE = {0: 0x110, 1: 0x510, 5: 0x310}
# Compute reads uniform buffer r through its device pointer at c0 + 0x210 + 16 r (nxnative's measured table; BotW's
# compute shaders read exactly c0 + 0x260..0x2d0, uniform slots 5..12, next to the storage table at 0x310).
DERIVE_UBO_POINTER_BASE, DERIVE_UBO_POINTER_SLOTS = 0x210, 16
# Image r's handle is at c2 + 0x120 + 8 r, eight slots after the texture table (nxnative's measured table; BotW's
# compute images use handle words 0x48, 0x4a, 0x4c). The format comes from the translator's own resource name.
DERIVE_IMG_BASE, DERIVE_IMG_SLOTS = 0x120, 8
RYUJINX_TEXTURE_FORMAT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      '../../third_party/ryujinx/src/Ryujinx.Graphics.Shader/TextureFormat.cs')


def glsl_image_formats():
    """GLSL format name -> Ryujinx TextureFormat name, from the translator's own source ("" is Unknown)."""
    with open(RYUJINX_TEXTURE_FORMAT) as f:
        table = dict((g, e) for e, g in re.findall(r'TextureFormat\.(\w+)\s*=>\s*"([a-z0-9]+)"', f.read()))
    table[''] = 'Unknown'
    return table
DERIVE_SSBO_SLOTS = 16
# Control +0x748: compute workgroup size (u32 x3); +0x754: shared memory bytes.
COMPUTE_LOCAL_OFFSET, COMPUTE_SHARED_OFFSET = 0x748, 0x754
MSL_TEXTURE_TYPES = [(prefix, name) for name, prefixes in bsb.TEXTURE_TYPES.items() for prefix in prefixes]
_INT_CAST = ('as_type<int>(', 'as_type<uint>(')


def msl_texture_type(msl_type):
    """Longest matching MSL prefix wins (texture2d_array< before texture2d<)."""
    best = max((p for p, _ in MSL_TEXTURE_TYPES if msl_type.startswith(p)), key=len, default=None)
    return dict(MSL_TEXTURE_TYPES)[best] if best else None


def driver_table_slot(handle):
    """Texture slot for a combined handle in the driver table, or None."""
    if handle >> 28:
        return None  # separate sampler handle: its table base is not established for BotW
    byte = (handle & 0x3fff) * 4
    if byte < DERIVE_TEX_BASE or (byte - DERIVE_TEX_BASE) % 8:
        return None
    slot = (byte - DERIVE_TEX_BASE) // 8
    return slot if slot < DERIVE_TEX_SLOTS else None


def integer_inputs(msl):
    """Vertex inputs whose every use is an integer bit cast (or a plain copy).

    Maxwell attribute registers carry raw bits, so integer formats (bone
    indices in R8UI / RGBA8UI) reach the shader unconverted and the code
    reinterprets them with as_type<int/uint>. Declared as float, Metal would
    convert the integer to a float value first and the bit cast would read
    garbage. Port of nxnative's dmsh_build.py int_inputs."""
    inputs = {m.group(2): int(m.group(3)) for m in re.finditer(r'(\w+)\s+(\w+)\s*\[\[attribute\((\d+)\)\]\]', msl)}
    i = msl.find('vertex main0_out main0(')
    if i < 0:
        return set()
    body, out = msl[i:], set()
    for name, loc in inputs.items():
        ints, bad = 0, False
        for use in re.finditer(r'in\.%s\b' % re.escape(name), body):
            ls, le = body.rfind('\n', 0, use.start()) + 1, body.find('\n', use.end())
            m = re.match(r'\s*float\s+(_\w+)\s*=\s*in\.%s\.[xyzw];$' % re.escape(name), body[ls:le])
            if not m:
                if body[:use.start()].endswith(_INT_CAST):
                    ints += 1
                    continue
                bad = True
                break
            var = m.group(1)
            for o in list(re.finditer(r'\b%s\b' % re.escape(var), body))[1:]:
                if body[:o.start()].endswith(_INT_CAST):
                    ints += 1
                    continue
                l2s, l2e = body.rfind('\n', 0, o.start()) + 1, body.find('\n', o.end())
                if re.match(r'\s*[\w.\[\]]+\s*=\s*%s;$' % re.escape(var), body[l2s:l2e]):
                    continue  # a plain copy keeps the bits under either declaration
                bad = True
                break
            if bad:
                break
        if not bad and ints:
            out.add(loc)
    return out


def integer_output_candidates(msl):
    """Render targets whose every component write is an as_type<float> bit cast.
    Such a shader may target an integer format, but a float target written
    through integer arithmetic looks the same, so derive only reports these."""
    out = re.search(r'struct main0_out\s*\{(.*?)\};', msl, re.S)
    found = []
    for m in re.finditer(r'(\w+)\s+(\w+)\s*\[\[color\((\d+)\)\]\]', out.group(1) if out else ''):
        writes = re.findall(r'out\.%s(?:\.[xyzw]+)?\s*=\s*([^;]+);' % re.escape(m.group(2)), msl)
        if writes and all(w.strip().startswith('as_type<float>(') for w in writes):
            found.append(int(m.group(3)))
    return sorted(found)


QUERIED_TEXTURE_RE = re.compile(r'texture handle ([0-9A-F]+) \(cbuf [-0-9]+, slot [-0-9]+\) has no PC type')


def queried_texture_handles(notes):
    """Handles whose dimension the translator had to ask for (the instruction does not encode it)."""
    return {int(m.group(1), 16) for n in notes for m in [QUERIED_TEXTURE_RE.search(n)] if m}


def derive_facts(capture, required, bindings, msl, queried=frozenset()):
    """-> (facts document, [unresolved descriptions]). Only required entries are
    filled; anything outside the rules is left out and reported."""
    doc = {'schema': bsb.FACTS_SCHEMA, 'key': capture.key, 'sha256': capture.sha256, 'stage': capture.stage_name}
    rules, unresolved = set(), []
    cb = {}
    for n in required.get('constant_buffers', {}):
        cb[n] = {'binding': int(n) - DERIVE_CBUF_BASE}
    if cb:
        doc['constant_buffers'] = cb; rules.add('cbuf=cN-3')
    types = {}
    for b in bindings:
        if b.get('kind') in ('tex', 'tbuf') and 'handle' in b:
            types.setdefault(b['handle'], set()).add(msl_texture_type(b['msl_type']))
    tex = {}
    for h, want in required.get('textures', {}).items():
        handle, t = int(h), types.get(int(h), set())
        slot = driver_table_slot(handle)
        if 'sampler_slot' in want or slot is None:
            unresolved.append(f'texture handle {handle:#x} is not a combined driver-table handle')
        elif len(t) != 1 or None in t:
            unresolved.append(f'texture handle {handle:#x} has MSL types {sorted(map(str, t))}')
        elif handle in queried:
            # The instruction does not encode the dimension and the translator assumed 2D, combined with what the
            # instruction does encode (a multisample fetch yields 2dms). nxnative assumes the same; the translated type
            # is kept and reported for review.
            tex[h] = {'slot': slot, 'type': t.pop()}
            rules.add('tex-type=2d-assumed-where-queried')
            unresolved.append({'review': 'assumed_texture_type', 'handle': handle})
        else:
            tex[h] = {'slot': slot, 'type': t.pop()}
    if tex:
        doc['textures'] = tex; rules.add('tex=driver-table-0x20')
    img, formats = {}, None
    names = {b['handle']: b for b in bindings if b.get('kind') == 'img' and 'handle' in b}
    for h in required.get('images', {}):
        handle, b = int(h), names.get(int(h))
        byte = (handle & 0x3fff) * 4
        slot = (byte - DERIVE_IMG_BASE) // 8 if byte >= DERIVE_IMG_BASE and (byte - DERIVE_IMG_BASE) % 8 == 0 else None
        fmt = re.fullmatch(r'[a-z]+_i\w*?_(?:cb\d+|tcb)_[0-9A-F]+_([a-z0-9]*)', b['msl_name']) if b else None
        if formats is None:
            formats = glsl_image_formats()
        typ = msl_texture_type(b['msl_type']) if b else None
        if handle >> 28 or slot is None or slot >= DERIVE_IMG_SLOTS or not fmt or fmt.group(1) not in formats or not typ:
            unresolved.append(f'images[{h}] is not an image-table handle with a known type and format')
        else:
            img[h] = {'slot': slot, 'type': typ, 'format': formats[fmt.group(1)]}
    if img:
        doc['images'] = img; rules.add('img=driver-table-0x120,format-from-translator')
    ubo = {}
    for off in required.get('uniform_pointers', {}):
        rel = int(off) - DERIVE_UBO_POINTER_BASE
        if capture.stage == 5 and 0 <= rel < 16 * DERIVE_UBO_POINTER_SLOTS and rel % 16 == 0:
            ubo[off] = {'binding': rel // 16}
        else:
            unresolved.append(f'uniform_pointers[{off}] has no derivation rule')
    if ubo:
        doc['uniform_pointers'] = ubo; rules.add('ubo-pointer=c0-0x210')
    ssbo, base = {}, DERIVE_SSBO_BASE.get(capture.stage)
    for off in required.get('storage_buffers', {}):
        rel = int(off) - base if base is not None else -1
        if 0 <= rel < 16 * DERIVE_SSBO_SLOTS and rel % 16 == 0:
            ssbo[off] = {'slot': rel // 16}
        else:
            unresolved.append(f'storage_buffers[{off}] has no derivation rule for stage {capture.stage_name}')
    if ssbo:
        doc['storage_buffers'] = ssbo; rules.add('ssbo=c0-table(vs 0x110, fs 0x510, cs 0x310)')
    if 'attributes' in required:
        ints = integer_inputs(msl)
        doc['attributes'] = {n: 'uint' if int(n) in ints else 'float' for n in required['attributes']}
        rules.add('attr=float,uint-if-bitcast-only')
    if 'outputs' in required:
        doc['outputs'] = {n: 'float' for n in required['outputs']}
        rules.add('out=float-assumed')
        candidates = [rt for rt in integer_output_candidates(msl) if str(rt) in required['outputs']]
        if candidates:
            unresolved.append({'review': 'integer_output_candidate', 'outputs': candidates})
    if 'device_state' in required:
        doc['device_state'] = dict(DERIVE_DEVICE_STATE); rules.add('device=upper_left,near_is_minus_w')
    if 'compute' in required:
        x, y, z = struct.unpack_from('<3I', capture.control, COMPUTE_LOCAL_OFFSET)
        shared = struct.unpack_from('<I', capture.control, COMPUTE_SHARED_OFFSET)[0]
        if not (x and y and z) or x * y * z > 1024 or shared > 0xc000:
            unresolved.append(f'compute control fields {x}x{y}x{z}, {shared} bytes are out of range')
        else:
            doc['compute'] = {'local_size': [x, y, z], 'shared_memory': (shared + 15) // 16 * 16}
            rules.add('compute=control+0x748')
    doc['provenance'] = f'{DERIVE_RULES}: ' + (', '.join(sorted(rules)) or 'no facts required') + \
        '; derived offline by botw_shader_derive.py, not observed at run time'
    return doc, unresolved


FACT_TABLES = ('constant_buffers', 'textures', 'images', 'storage_buffers', 'attributes', 'outputs', 'compute', 'device_state')


def compare_facts(derived, established):
    """Entries where a derived fact differs from an established one."""
    diffs = []
    for t in FACT_TABLES:
        d, e = derived.get(t), established.get(t)
        if isinstance(e, dict) and t not in ('compute', 'device_state'):
            for k, v in e.items():
                if (d or {}).get(k) != v:
                    diffs.append(f'{t}[{k}]: established {v}, derived {(d or {}).get(k)}')
        elif e is not None and d != e:
            diffs.append(f'{t}: established {e}, derived {d}')
    return diffs


def cmd_derive(a):
    bsb.check_capture_dir(a.captures)
    probe_root = os.path.dirname(os.path.realpath(a.probe_manifest))
    with open(a.probe_manifest) as f:
        manifest = json.load(f)
    if manifest.get('schema') != bsb.MANIFEST_SCHEMA or manifest.get('mode') != 'probe':
        raise bsb.ToolError(f'{a.probe_manifest} is not a probe build manifest')
    if os.path.realpath(manifest.get('captures') or '') != os.path.realpath(a.captures):
        raise bsb.ToolError('the probe manifest was built from a different capture directory')
    out = bsb.make_private_dir(a.facts_out, 'facts output directory')
    if a.established:
        bsb.require_outside_git(os.path.realpath(a.established), 'established facts directory')
    wanted = set(bsb.read_keys(a)) or None
    report = {'schema': 'botw-shader-derive-report-v1', 'rules': DERIVE_RULES, 'probe_manifest': os.path.realpath(a.probe_manifest),
              'captures': os.path.realpath(a.captures), 'established': os.path.realpath(a.established) if a.established else None,
              'derived': 0, 'established_copied': 0, 'skipped': {}, 'unresolved': {}, 'established_differences': {},
              'integer_output_candidates': {}, 'assumed_texture_types': {}}
    for r in manifest['results']:
        key = r.get('key')
        if wanted is not None and key not in wanted:
            continue
        if r.get('status') != 'probe':
            report['skipped'][key] = r.get('errors') or [r.get('status')]
            continue
        capture = bsb.load_capture(a.captures, key)
        probe_json = os.path.join(probe_root, key, key + '.probe.json')
        with open(probe_json) as f:
            probe = json.load(f)
        if probe.get('sha256') != capture.sha256:
            raise bsb.ToolError(f'{probe_json} does not belong to capture {key}')
        with open(os.path.join(probe_root, key, key + '.metal')) as f:
            msl = f.read()
        doc, notes = derive_facts(capture, probe.get('required_facts', {}), probe.get('bindings', []), msl,
                                  queried_texture_handles(probe.get('translator_notes', [])))
        unresolved = [n for n in notes if isinstance(n, str)]
        if unresolved:
            report['unresolved'][key] = unresolved
        for n in notes:
            if isinstance(n, dict) and n['review'] == 'integer_output_candidate':
                report['integer_output_candidates'][key] = n['outputs']
            elif isinstance(n, dict) and n['review'] == 'assumed_texture_type':
                report['assumed_texture_types'].setdefault(key, []).append(n['handle'])
        est = os.path.join(a.established, key + '.facts.json') if a.established else None
        target = os.path.join(out, key + '.facts.json')
        if est and os.path.exists(est):
            raw = bsb.read_regular(est, 1 << 20)
            bsb.load_facts(est, capture)  # established facts must still bind to this capture
            diffs = compare_facts(doc, bsb.strict_json(raw, os.path.basename(est)))
            if diffs:
                report['established_differences'][key] = diffs
            bsb.write_atomic(target, raw)
            report['established_copied'] += 1
            continue
        bsb.write_json(target, doc)
        bsb.load_facts(target, capture)  # the derived document must satisfy the facts schema
        report['derived'] += 1
    bsb.write_json(os.path.join(out, 'derive-report.json'), report)
    print(f'derive {DERIVE_RULES}: {report["derived"]} derived, {report["established_copied"]} established copied, '
          f'{len(report["skipped"])} skipped (failed probe), {len(report["unresolved"])} with unresolved entries, '
          f'{len(report["established_differences"])} established facts differing from the rules, '
          f'{len(report["integer_output_candidates"])} with integer-looking outputs kept as float, '
          f'{len(report["assumed_texture_types"])} with an assumed 2D texture type')
    for key, diffs in sorted(report['established_differences'].items()):
        for d in diffs:
            print(f'  differs {key}: {d}')
    print(f'report: {os.path.join(out, "derive-report.json")}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='botw_shader_derive.py', description=__doc__.split('\n\n')[0])
    p.add_argument('captures')
    p.add_argument('keys', nargs='*')
    p.add_argument('--probe-manifest', required=True, help='manifest-probe.json of a probe build over these captures')
    p.add_argument('--facts-out', required=True, help='absolute output directory outside any Git work tree')
    p.add_argument('--established', help='directory of established <key>.facts.json files')
    p.add_argument('--keys-file', help='file with one capture key per line')
    a = p.parse_args(argv)
    try:
        return cmd_derive(a)
    except bsb.ToolError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
