"""Bounded BNSH/NVN inspection and local-only offline shader preparation.

Layout references (no SDK implementation is copied):
  maierfelix/bnsh-decoder BNSH.ksy (MIT, 2020 Felix Maier), and the locally
  validated BNSH 2.1.2 containers. See native/botw_graphics/THIRD_PARTY_LICENSE.txt.
A hash identifies original input; it does not assert that a translated shader
has correct resource bindings. Game code and translated outputs stay local.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import struct
import subprocess

MAGIC = b'BNSH\0\0\0\0'
STAGES = ('vertex', 'tess_control', 'tess_evaluation', 'geometry', 'fragment', 'compute')
MAX_CONTAINER_BYTES = 256 * 1024 * 1024


def parse_bnsh(data):
    """Read unrelocated file offsets, returning metadata without shader bytes."""
    data = bytes(data)
    if len(data) < 0x20 or data[:8] != MAGIC:
        raise ValueError('Invalid BNSH header bounds or magic')
    version = struct.unpack_from('<I', data, 8)[0]
    if data[12:14] != b'\xff\xfe':
        raise ValueError('Unsupported BNSH byte order')
    name, flags, first, relocation, size = struct.unpack_from('<IHHII', data, 0x10)
    if size != len(data) or size > MAX_CONTAINER_BYTES:
        raise ValueError('BNSH declared file size does not match input bounds')
    if flags:
        raise ValueError('BNSH is relocated; expected file-relative offsets')
    if version >> 16 != 2:
        raise ValueError('Unsupported BNSH major version')

    def read(offset, count, label):
        if offset < 0 or count < 0 or offset > size or count > size - offset:
            raise ValueError(label + ' exceeds BNSH file bounds')
        return data[offset:offset + count]

    def unpack(fmt, offset, label):
        return struct.unpack(fmt, read(offset, struct.calcsize(fmt), label))

    def dictionary(pointer):
        if not pointer:
            return []
        if read(pointer, 4, 'reflection dictionary') != b'_DIC':
            raise ValueError('Invalid BNSH reflection dictionary')
        count, = unpack('<I', pointer + 4, 'reflection dictionary count')
        if count > 4096:
            raise ValueError('BNSH reflection dictionary count exceeds limit')
        read(pointer + 0x18, count * 16, 'reflection dictionary nodes')
        result = []
        for i in range(count):
            text, = unpack('<Q', pointer + 0x20 + i * 16, 'reflection dictionary key')
            length, = unpack('<H', text, 'reflection string length')
            try:
                result.append(read(text + 2, length, 'reflection string').decode('utf-8'))
            except UnicodeDecodeError as exc:
                raise ValueError('Invalid reflection UTF-8') from exc
        return result

    def reflection(pointer):
        if not pointer:
            return {}
        result = {}
        for stage, record in zip(STAGES, unpack('<6Q', pointer, 'program reflection')):
            if not record:
                continue
            pointers = unpack('<5Q', record, 'stage reflection dictionaries')
            output, sampler, uniform, storage = unpack('<4I', record + 0x28, 'reflection resource indices')
            slots, = unpack('<Q', record + 0x38, 'reflection slot array')
            groups = {}
            for kind, names, base in zip(('inputs', 'outputs', 'samplers', 'uniforms', 'storage'),
                                         map(dictionary, pointers), (0, output, sampler, uniform, storage)):
                if base > 16384:
                    raise ValueError('Reflection resource index exceeds limit')
                groups[kind] = [{'name': name, 'slot': unpack('<i', slots + (base + i) * 4,
                                                             'reflection resource slot')[0]}
                                for i, name in enumerate(names)]
            result[stage] = groups
        return result

    if relocation:
        if read(relocation, 4, 'relocation table') != b'_RLT':
            raise ValueError('BNSH relocation table magic is invalid')
    if name:
        read(name, 1, 'file name')
        end = data.find(b'\0', name, min(size, name + 4096))
        if end < 0:
            raise ValueError('BNSH file name exceeds bounds')
        try:
            filename = data[name:end].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('BNSH file name is not UTF-8') from exc
    else:
        filename = ''
    if read(first, 4, 'grsc block') != b'grsc':
        raise ValueError('BNSH first block is not grsc')
    _, block_bytes = unpack('<II', first + 4, 'grsc block length')
    read(first, block_bytes, 'grsc block')
    api, api_version, code_type = unpack('<HHH', first + 16, 'target metadata')
    count, array, pool = unpack('<IQQ', first + 28, 'shader variations')
    if count > 100000:
        raise ValueError('BNSH shader variation count exceeds limit')
    read(array, count * 0x40, 'shader variation array')
    if pool:
        _, pool_bytes, pool_offset = unpack('<IIQ', pool, 'shader binary pool')
        read(pool_offset, pool_bytes, 'shader binary pool data')
    variations = []
    for index in range(count):
        _, _, program, _ = unpack('<QQQQ', array + index * 0x40, 'shader variation')
        if not program:
            raise ValueError('BNSH has no binary program for variation ' + str(index))
        read(program, 0x60, 'shader program')
        pointers = unpack('<6Q', program + 8, 'shader stages')
        shaders = []
        for stage, pointer in zip(STAGES, pointers):
            if not pointer:
                continue
            read(pointer, 0x40, 'NVN shader descriptor')
            address, control, code, code_size, control_size = unpack(
                '<QQQII', pointer, 'NVN shader descriptor')
            if address:
                raise ValueError('NVN shader address is relocated')
            ctl = read(control, control_size, 'NVN control section')
            binary = read(code, code_size, 'NVN GPU code')
            if len(ctl) < 4 or ctl[:4] != b'\x34\x12\x76\x98':
                raise ValueError('Invalid NVN control section magic')
            if len(binary) < 0x80 or binary[:4] != b'\x78\x56\x34\x12':
                raise ValueError('Invalid NVN GPU code header')
            shaders.append({'stage': stage, 'descriptor_offset': pointer,
                            'control_offset': control, 'control_bytes': control_size,
                            'code_offset': code, 'code_bytes': code_size,
                            'control_sha256': hashlib.sha256(ctl).hexdigest(),
                            'code_sha256': hashlib.sha256(binary).hexdigest()})
        if not shaders:
            raise ValueError('BNSH binary program has no shader stages')
        reflection_pointer, = unpack('<Q', program + 0x78, 'program reflection pointer')
        variations.append({'index': index, 'program_offset': program, 'shaders': shaders,
                           'reflection': reflection(reflection_pointer)})
    return {'name': filename, 'version': f'{version >> 16}.{version >> 8 & 255}.{version & 255}',
            'file_bytes': size, 'sha256': hashlib.sha256(data).hexdigest(),
            'target_api': api, 'target_api_version': api_version,
            'target_code_type': code_type, 'variations': variations}


def inspect_embedded_bnsh(data):
    """Locate complete BNSH files in a bounded local image, rejecting bad candidates."""
    result, cursor = [], 0
    while (cursor := data.find(MAGIC, cursor)) >= 0:
        if len(data) - cursor < 0x20:
            raise ValueError('Embedded BNSH header exceeds image bounds')
        size = struct.unpack_from('<I', data, cursor + 0x1c)[0]
        if size < 0x20 or size > len(data) - cursor or size > MAX_CONTAINER_BYTES:
            raise ValueError('Embedded BNSH declared size exceeds image bounds')
        result.append({'offset': cursor, **parse_bnsh(data[cursor:cursor + size])})
        cursor += size
    return result


def extract_shader_inputs(image, destination):
    """Write only verified NVN control/code spans into a fresh local directory."""
    containers = inspect_embedded_bnsh(image)
    if not containers:
        raise ValueError('No complete BNSH containers found')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for container in containers:
        base = container['offset']
        for variation in container['variations']:
            for shader in variation['shaders']:
                row = {'container': container['name'], 'variation': variation['index'],
                       'reflection': variation['reflection'].get(shader['stage'], {}), **shader}
                for kind in ('control', 'code'):
                    filename = shader[kind + '_sha256'] + ('.control' if kind == 'control' else '.nvn')
                    start = base + shader[kind + '_offset']
                    payload = image[start:start + shader[kind + '_bytes']]
                    path = destination / filename
                    if not path.exists():
                        path.write_bytes(payload)
                    row[kind + '_file'] = filename
                records.append(row)
    (destination / 'inputs.json').write_text(json.dumps({'schema': 1, 'records': records}, indent=2) + '\n')
    return records


def texture_constraints(records):
    """Build explicit conditional type requirements, never fabricate cbuf data.

    For this audited NVN build the GPU c0 offsets 0x20/0x28 correspond exactly
    to the BNSH sampler slots 0/1 across every queried shader. Applying the same
    bank/offset ABI to another build requires a new audit. Metal's binding
    reflection must enforce the required type against the real NVN descriptor.
    """
    slots = {}
    for record in records:
        for sampler in record.get('reflection', {}).get('samplers', []):
            slot = sampler['slot']
            if slot == -1:  # Inactive resource, not a bound descriptor.
                continue
            if not isinstance(slot, int) or not 0 <= slot < 128:
                raise ValueError('Reflected texture slot exceeds supported range')
            slots.setdefault(slot, set()).add(sampler['name'])
    return [{'nvn_slot': slot, 'names': sorted(names), 'cbuf_index': 0,
             'cbuf_offset': 0x20 + slot * 8, 'runtime_required_type': '2d'}
            for slot, names in sorted(slots.items())]


def compile_shader_inputs(records, inputs, destination, compiler, *, require_runtime_2d=False):
    """Run the fixed external offline compiler; preserve every failure and log.

    Successful syntax conversion does not validate NVN bindings or semantics.
    This deliberately does not register generated shaders with the runtime.
    """
    destination, inputs, compiler = Path(destination), Path(inputs), Path(compiler).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for identity in sorted({row['code_sha256'] for row in records}):
        source = inputs / (identity + '.nvn')
        glsl = destination / (identity + '.glsl')
        constraints = texture_constraints([r for r in records if r['code_sha256'] == identity]) if require_runtime_2d else []
        environment = dict(os.environ)
        environment.pop('BOTW_SHADER_2D_CBUFS', None)
        if constraints:
            environment['BOTW_SHADER_2D_CBUFS'] = ''.join(
                f";{q['cbuf_index']}:{q['cbuf_offset']};" for q in constraints)
        result = subprocess.run([str(compiler), str(source), str(glsl)], env=environment,
                                capture_output=True, text=True, timeout=120)
        (destination / (identity + '.log')).write_text(result.stdout + result.stderr)
        outputs = [glsl, glsl.with_suffix('.spv'), glsl.with_suffix('.metal')]
        ok = result.returncode == 0 and all(p.is_file() and p.stat().st_size for p in outputs)
        traces = {}
        for prefix, key in (('BOTW_ENV ', 'environment_queries'),
                            ('BOTW_UNRESOLVED_CBUF ', 'unresolved_cbuf'),
                            ('BOTW_UNRESOLVED_TEXTURE ', 'unresolved_texture'),
                            ('BOTW_TEXTURE_CONSTRAINT ', 'used_texture_constraints')):
            traces[key] = [json.loads(line[len(prefix):]) for line in result.stderr.splitlines()
                           if line.startswith(prefix)]
        results.append({'code_sha256': identity, 'exit_code': result.returncode,
                        'converted': bool(ok), 'nvn_bindings_validated': False, 'texture_type_constraints': constraints, **traces,
                        'outputs': [p.name for p in outputs if p.exists()]})
        # A partial run remains inspectable even if a later compiler process fails.
        (destination / 'translation.json').write_text(json.dumps({'schema': 1, 'results': results}, indent=2) + '\n')
    return results


def shader_binding_map(msl, stage, reflection):
    """Match this compiler's named resources to explicit BNSH NVN slots.

    c[3+r] -> uniform slot r is the fixed NVN ABI documented by the upstream
    Maxwell reference reader. Every proposed slot must also occur in this
    program's actual BNSH reflection. Private c0/c1 and SSBOs are rejected.
    """
    entry = re.search(r'\b(vertex|fragment)\s+\w+\s+(\w+)\s*\(', msl)
    if not entry or entry[1] != stage:
        raise ValueError('Missing or mismatched Metal entry stage')
    end = msl.find('{', entry.end())
    if end < 0:
        raise ValueError('Metal entry body is missing')
    signature = msl[entry.start():end]
    uniforms = {x['slot'] for x in reflection.get('uniforms', []) if x['slot'] >= 0}
    samplers = {x['slot'] for x in reflection.get('samplers', []) if x['slot'] >= 0}
    result, covered, types = [], set(), []
    for space, variable, metal in re.findall(r'\b(constant|device)\s+\w+\s*[&*]\s*(\w+)\s+\[\[buffer\((\d+)\)\]\]', signature):
        bank = re.fullmatch(r'c(\d+)(?:_\d+)?', variable)
        if space != 'constant' or not bank or not 3 <= int(bank[1]) <= 16:
            raise ValueError('Unknown private/driver constant or storage resource: ' + variable)
        slot = int(bank[1]) - 3
        if slot not in uniforms:
            raise ValueError('Uniform slot absent from BNSH reflection')
        result.append({'kind': 'uniform', 'nvn_slot': slot, 'metal_slot': int(metal)})
        covered.add(('buffer', int(metal)))
    for kind, variable, metal in re.findall(r'\b(texture\w+|sampler)(?:<[^>]+>)?\s+(\w+)\s+\[\[(?:texture|sampler)\((\d+)\)\]\]', signature):
        name = re.fullmatch(r'(?:fs|vs)_tex0_([0-9a-f]+)(Smplr)?', variable)
        if not name:
            raise ValueError('Unknown translated texture resource: ' + variable)
        offset = int(name[1], 16)
        if offset < 0x20 or (offset - 0x20) % 8:
            raise ValueError('Unknown NVN texture constant offset')
        slot = (offset - 0x20) // 8
        if slot not in samplers:
            raise ValueError('Texture/sampler slot absent from BNSH reflection')
        binding_kind = 'sampler' if kind == 'sampler' else 'texture'
        if kind not in ('sampler', 'texture2d', 'texture2d_array'):
            raise ValueError('Texture type is not supported by this bundle schema')
        result.append({'kind': binding_kind, 'nvn_slot': slot, 'metal_slot': int(metal)})
        covered.add((binding_kind, int(metal)))
        if binding_kind == 'texture':
            types.append({'nvn_slot': slot, 'metal_slot': int(metal), 'type': kind[7:]})
    declared = {(kind, int(index)) for kind, index in re.findall(r'\[\[(buffer|texture|sampler)\((\d+)\)\]\]', signature)}
    if declared != covered:
        raise ValueError('Unrecognized Metal resource declaration')
    inputs = sorted({int(x) for x in re.findall(r'\[\[attribute\((\d+)\)\]\]', msl)})
    reflected_inputs = {x['slot'] for x in reflection.get('inputs', [])}
    if stage == 'vertex' and not set(inputs) <= reflected_inputs:
        raise ValueError('Vertex input absent from BNSH reflection')
    return {'entry': entry[2], 'bindings': result, 'texture_types': types, 'vertex_inputs': inputs}


def stage_shader_bundle(inputs, converted, destination):
    """Prepare a private local bundle; unknown resources remain unsupported."""
    inputs, converted, destination = Path(inputs), Path(converted), Path(destination)
    records = json.loads((inputs / 'inputs.json').read_text())['records']
    groups = {}
    for record in records:
        identity = (record['control_sha256'], record['code_sha256'], record['stage'])
        groups.setdefault(identity, []).append(record)
    entries, files, indices = [], {}, {}
    for identity, rows in sorted(groups.items()):
        control_hash, code_hash, stage = identity
        row = rows[0]
        entry = {'stage': stage, 'control_sha256': control_hash, 'code_sha256': code_hash}
        for kind, directory, filename in (('control', inputs, control_hash + '.control'),
                                          ('code', inputs, code_hash + '.nvn'),
                                          ('msl', converted, code_hash + '.metal')):
            if not re.fullmatch(r'[0-9a-f]{64}\.(?:control|nvn|metal)', filename):
                raise ValueError('Unsafe shader input identity')
            payload = (directory / filename).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if kind != 'msl' and digest != entry[kind + '_sha256']:
                raise ValueError('Original shader SHA256 mismatch: ' + filename)
            files[filename] = payload
            entry[kind] = {'path': filename, 'bytes': len(payload), 'sha256': digest}
        try:
            maps = [shader_binding_map(files[code_hash + '.metal'].decode('utf-8'), stage,
                                       candidate.get('reflection', {})) for candidate in rows]
            if any(mapping != maps[0] for mapping in maps):
                raise ValueError('Conflicting BNSH resource reflection for one exact identity')
            entry.update({'status': 'ready', **maps[0], 'reflection': row.get('reflection', {})})
        except ValueError as exc:
            entry.update({'status': 'unsupported', 'reason': str(exc)})
        indices[identity] = len(entries)
        entries.append(entry)
    manifest = {'schema': 1, 'entries': entries,
                'stage_records': [{'entry': indices[(x['control_sha256'], x['code_sha256'], x['stage'])],
                                   'container': x['container'], 'variation': x['variation'], 'stage': x['stage']}
                                  for x in records]}
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (destination / name).write_bytes(data)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'unique_entries': len(entries), 'stage_records': len(records),
            'ready': sum(x['status'] == 'ready' for x in entries),
            'unsupported': sum(x['status'] == 'unsupported' for x in entries)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path,
                        help='Local, unrelocated NSO memory image or BNSH file')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--extract-dir', type=Path,
                        help='Fresh ignored directory for local shader inputs')
    parser.add_argument('--compiler', type=Path, help='Fixed, patched shader-compiler-cli binary')
    parser.add_argument('--compile-dir', type=Path, help='Fresh ignored directory for local converted output')
    parser.add_argument('--require-runtime-2d-textures', action='store_true',
                        help='Compile under explicit BNSH slot-based 2D requirements; runtime must check descriptors')
    parser.add_argument('--bundle-inputs', type=Path, help='Existing local extraction directory for private bundling')
    parser.add_argument('--bundle-compiled', type=Path, help='Existing local converted MSL directory for private bundling')
    parser.add_argument('--bundle-dir', type=Path, help='Fresh ignored directory for the private runtime bundle')
    args = parser.parse_args(argv)
    bundle_args = (args.bundle_inputs, args.bundle_compiled, args.bundle_dir)
    if any(bundle_args):
        if not all(bundle_args) or any((args.image, args.report, args.extract_dir, args.compiler,
                                       args.compile_dir, args.require_runtime_2d_textures)):
            parser.error('Bundling requires exactly --bundle-inputs, --bundle-compiled and --bundle-dir')
        print(json.dumps(stage_shader_bundle(*bundle_args), sort_keys=True))
        return 0
    if not args.image or not args.report:
        parser.error('--image and --report are required for inspection/extraction')
    if bool(args.compiler) != bool(args.compile_dir) or (args.compiler and not args.extract_dir):
        parser.error('--compiler and --compile-dir require --extract-dir')
    image = args.image.read_bytes()
    containers = inspect_embedded_bnsh(image)
    if not containers:
        parser.error('No complete BNSH containers found')
    report = {'schema': 1, 'image_sha256': hashlib.sha256(image).hexdigest(),
              'containers': containers}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    stages = sum(len(v['shaders']) for c in containers for v in c['variations'])
    print(f'Validated {len(containers)} BNSH containers, {stages} shader stage records', flush=True)
    if args.extract_dir:
        records = extract_shader_inputs(image, args.extract_dir)
        if args.compiler:
            results = compile_shader_inputs(records, args.extract_dir, args.compile_dir, args.compiler,
                                            require_runtime_2d=args.require_runtime_2d_textures)
            converted = sum(row['converted'] for row in results)
            print(f'Converted {converted}/{len(results)} unique code blobs; NVN bindings remain unvalidated')
            return int(converted != len(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
