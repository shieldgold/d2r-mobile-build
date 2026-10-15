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
        variations.append({'index': index, 'program_offset': program, 'shaders': shaders})
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
                row = {'container': container['name'], 'variation': variation['index'], **shader}
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


def compile_shader_inputs(records, inputs, destination, compiler):
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
        result = subprocess.run([str(compiler), str(source), str(glsl)],
                                capture_output=True, text=True, timeout=120)
        (destination / (identity + '.log')).write_text(result.stdout + result.stderr)
        outputs = [glsl, glsl.with_suffix('.spv'), glsl.with_suffix('.metal')]
        ok = result.returncode == 0 and all(p.is_file() and p.stat().st_size for p in outputs)
        results.append({'code_sha256': identity, 'exit_code': result.returncode,
                        'converted': bool(ok), 'nvn_bindings_validated': False,
                        'outputs': [p.name for p in outputs if p.exists()]})
        # A partial run remains inspectable even if a later compiler process fails.
        (destination / 'translation.json').write_text(json.dumps({'schema': 1, 'results': results}, indent=2) + '\n')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True,
                        help='Local, unrelocated NSO memory image or BNSH file')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--extract-dir', type=Path,
                        help='Fresh ignored directory for local shader inputs')
    parser.add_argument('--compiler', type=Path, help='Fixed, patched shader-compiler-cli binary')
    parser.add_argument('--compile-dir', type=Path, help='Fresh ignored directory for local converted output')
    args = parser.parse_args(argv)
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
            results = compile_shader_inputs(records, args.extract_dir, args.compile_dir, args.compiler)
            converted = sum(row['converted'] for row in results)
            print(f'Converted {converted}/{len(results)} unique code blobs; NVN bindings remain unvalidated')
            return int(converted != len(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
