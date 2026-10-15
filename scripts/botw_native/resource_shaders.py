"""Bounded, read-only SARC/Yaz0 shader discovery with exact source provenance.

Format references: ZeldaMods/oead Yaz0 and ZeldaMods/sarc SARC definitions.
No upstream implementation or game bytes are included. BNSH validation delegates
only to the existing local parser; parser acceptance is not runtime validation.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import tempfile

from .romfs import RomFSImage, MAX_READ
from .shaders import inspect_embedded_bnsh

MAX_CONTAINER = 256 * 1024**2
MAX_METADATA = 4 * 1024**2
MAX_NODES = 100000
SHADER_SUFFIXES = {'.bfsha', '.sbfsha', '.bnsh', '.sharc', '.sharcb'}
CONTAINER_SUFFIXES = SHADER_SUFFIXES | {'.bfres', '.sbfres', '.sarc', '.ssarc', '.pack', '.blarc', '.sblarc'}
KNOWN_MAGIC = (b'SARC', b'FSHA', b'FRES', b'BNSH')


def _exact(reader, offset, size, total):
    if offset < 0 or size < 0 or offset > total or size > total - offset:
        raise ValueError('Resource span exceeds container bounds')
    result = reader(offset, size)
    if len(result) != size:
        raise ValueError('Truncated resource container')
    return result


def _member_name(raw):
    try:
        name = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Invalid SARC name UTF-8') from exc
    if len(raw) > 768 or name.startswith('/'):
        raise ValueError('Unsafe SARC member path')
    for part in name.split('/'):
        if (not part or part in ('.', '..') or len(part.encode()) > 255 or
                any(c in part for c in '\\:\0') or any(ord(c) < 32 or ord(c) == 127 for c in part)):
            raise ValueError('Unsafe SARC member path')
    return name


@dataclass(frozen=True)
class SarcMember:
    name: str
    offset: int
    size: int


def read_sarc(reader, size):
    """Read metadata only; reader(offset,length) addresses this SARC's bytes."""
    if size > MAX_CONTAINER:
        raise ValueError('SARC container exceeds configured size limit')
    header = _exact(reader, 0, 32, size)
    endian = {b'\xff\xfe': '<', b'\xfe\xff': '>'}.get(header[6:8])
    if header[:4] != b'SARC' or not endian:
        raise ValueError('Invalid SARC magic or byte order')
    header_size, = struct.unpack_from(endian + 'H', header, 4)
    declared, data_offset, version, reserved = struct.unpack_from(endian + 'IIHH', header, 8)
    sfat_size, count, multiplier = struct.unpack_from(endian + 'HHI', header, 24)
    if (header_size != 20 or declared != size or version != 0x100 or reserved or
            header[20:24] != b'SFAT' or sfat_size != 12 or count >= 16384 or not multiplier):
        raise ValueError('Invalid SARC/SFAT metadata header')
    names_base = 40 + count * 16
    if not names_base <= data_offset <= min(size, MAX_METADATA):
        raise ValueError('SARC name table exceeds metadata bounds')
    metadata = _exact(reader, 0, data_offset, size)
    sfnt = names_base - 8
    if metadata[sfnt:sfnt + 4] != b'SFNT' or struct.unpack_from(endian + 'HH', metadata, sfnt + 4) != (8, 0):
        raise ValueError('Invalid SARC SFNT header')
    members, names, spans, previous_hash = [], set(), [], -1
    for i in range(count):
        name_hash, attr, start, end = struct.unpack_from(endian + '4I', metadata, 32 + i * 16)
        if attr >> 24 != 1 or name_hash < previous_hash or start > end or end > size - data_offset:
            raise ValueError('Invalid SARC member flags, order or data bounds')
        previous_hash = name_hash
        name_offset = names_base + (attr & 0xffffff) * 4
        if name_offset >= data_offset:
            raise ValueError('SARC member name exceeds metadata bounds')
        name_end = metadata.find(b'\0', name_offset, min(data_offset, name_offset + 769))
        if name_end < 0:
            raise ValueError('Unterminated SARC member name')
        raw = metadata[name_offset:name_end]
        name = _member_name(raw)
        computed = 0
        for byte in raw:
            computed = (computed * multiplier + (byte if byte < 128 else byte - 256)) & 0xffffffff
        if computed != name_hash or name in names:
            raise ValueError('SARC name hash mismatch or duplicate path')
        names.add(name)
        if end > start: spans.append((start, end))
        members.append(SarcMember(name, data_offset + start, end - start))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError('Overlapping SARC file payloads')
    return members


def decompress_yaz0(data, *, max_output=MAX_CONTAINER):
    """Decode bounded literals/backrefs, including overlapping LZ copies."""
    if len(data) < 16 or len(data) > MAX_CONTAINER or data[:4] != b'Yaz0':
        raise ValueError('Invalid or oversized Yaz0 input')
    expected, = struct.unpack_from('>I', data, 4)
    if not 0 <= expected <= min(max_output, MAX_CONTAINER):
        raise ValueError('Yaz0 expansion limit exceeded')
    output, cursor = bytearray(), 16
    def take(size):
        nonlocal cursor
        if size > len(data) - cursor:
            raise ValueError('Truncated Yaz0 token')
        value = data[cursor:cursor + size]; cursor += size
        return value
    while len(output) < expected:
        flags = take(1)[0]
        for bit in range(7, -1, -1):
            if len(output) == expected: break
            if flags & (1 << bit):
                output.extend(take(1))
                continue
            pair = int.from_bytes(take(2), 'big')
            distance, count = (pair & 0xfff) + 1, pair >> 12
            count = count + 2 if count else take(1)[0] + 18
            if distance > len(output) or count > expected - len(output):
                raise ValueError('Yaz0 back-reference exceeds output bounds')
            start = len(output) - distance
            pattern = output[start:start + min(count, distance)]
            output.extend((pattern * ((count + len(pattern) - 1) // len(pattern)))[:count])
    trailer = data[cursor:]
    if len(trailer) > 15 or any(trailer):
        raise ValueError('Unexpected bytes after Yaz0 stream')
    return bytes(output)


def _magic(prefix):
    raw = prefix[:4]
    return raw.decode('ascii') if raw and all(32 <= n < 127 for n in raw) else raw.hex()


def _yaz0_prefix(data):
    """Decode at most four output bytes from a small encoded header sample."""
    if len(data) < 16 or data[:4] != b'Yaz0':
        raise ValueError('Invalid Yaz0 prefix header')
    expected, = struct.unpack_from('>I', data, 4)
    target, position, output = min(4, expected), 16, bytearray()
    def take(size):
        nonlocal position
        if size > len(data) - position: raise ValueError('Truncated Yaz0 prefix token')
        result = data[position:position + size]; position += size
        return result
    while len(output) < target:
        flags = take(1)[0]
        for bit in range(7, -1, -1):
            if len(output) == target: break
            if flags & (1 << bit): output.extend(take(1)); continue
            pair = int.from_bytes(take(2), 'big')
            distance, count = (pair & 4095) + 1, pair >> 12
            count = count + 2 if count else take(1)[0] + 18
            if distance > len(output) or count > expected - len(output):
                raise ValueError('Invalid Yaz0 prefix back-reference')
            for _ in range(min(count, target - len(output))): output.append(output[-distance])
    return bytes(output)


def _interesting(name, prefix):
    return (PurePosixPath(name).suffix.lower() in CONTAINER_SUFFIXES or 'shader' in name.lower() or
            prefix[:4] in KNOWN_MAGIC or (prefix[:4] == b'Yaz0' and prefix[17:21] in KNOWN_MAGIC))


def catalogue_romfs(image):
    """Inventory all outer headers and raw-SARC metadata, without decompression."""
    image = RomFSImage(image)
    extensions, magics, inner, decoded_magics, member_magics = Counter(), Counter(), Counter(), Counter(), Counter()
    candidates, archives, formats = [], [], []
    for entry in sorted(image.files.values(), key=lambda e:e.path):
        prefix = image.read_file(entry.path, 0, min(32, entry.size))
        extensions[PurePosixPath(entry.path).suffix.lower()] += 1; magics[_magic(prefix)] += 1
        decoded = _yaz0_prefix(prefix) if prefix[:4] == b'Yaz0' else prefix[:4]
        if prefix[:4] == b'Yaz0': decoded_magics[_magic(decoded)] += 1
        if decoded in KNOWN_MAGIC:
            formats.append({'romfs_path': entry.path, 'image_offset': entry.offset, 'bytes': entry.size,
                            'format': _magic(decoded), 'wrapper': 'Yaz0' if prefix[:4] == b'Yaz0' else None})
        if PurePosixPath(entry.path).suffix.lower() in SHADER_SUFFIXES or 'shader' in entry.path.lower():
            candidates.append({'romfs_path': entry.path, 'image_offset': entry.offset, 'bytes': entry.size, 'magic': _magic(prefix)})
        if prefix[:4] != b'SARC': continue
        rows = read_sarc(lambda off, size: image.read_file(entry.path, off, size), entry.size)
        archives.append({'romfs_path': entry.path, 'image_offset': entry.offset, 'bytes': entry.size, 'members': len(rows)})
        for row in rows:
            extension = PurePosixPath(row.name).suffix.lower(); inner[extension] += 1
            sample = image.read_file(entry.path, row.offset, min(32, row.size))
            decoded = _yaz0_prefix(sample) if sample[:4] == b'Yaz0' else sample[:4]
            member_magics[('Yaz0->' if sample[:4] == b'Yaz0' else '') + _magic(decoded)] += 1
            if decoded in KNOWN_MAGIC:
                formats.append({'romfs_path': entry.path, 'member': row.name,
                                'image_offset': entry.offset + row.offset, 'bytes': row.size,
                                'format': _magic(decoded), 'wrapper': 'Yaz0' if sample[:4] == b'Yaz0' else None})
            if extension in SHADER_SUFFIXES or 'shader' in row.name.lower() or decoded in (b'FSHA', b'BNSH'):
                candidates.append({'romfs_path': entry.path, 'member': row.name,
                                   'image_offset': entry.offset + row.offset, 'bytes': row.size,
                                   'magic': _magic(sample), 'decoded_prefix_magic': _magic(decoded)})
    return {'schema': 1, 'file_count': len(image.files), 'image_bytes': image.size,
            'extensions': dict(sorted(extensions.items())), 'root_magics': dict(sorted(magics.items())),
            'yaz0_prefix_magics': dict(sorted(decoded_magics.items())), 'sarc_member_prefix_magics': dict(sorted(member_magics.items())),
            'format_candidates': formats,
            'raw_sarc_archives': archives, 'sarc_member_extensions': dict(sorted(inner.items())),
            'shader_candidates': candidates,
            'coverage': 'All outer headers and four-byte Yaz0 output prefixes; all raw SARC names/member prefixes. Full compressed/BFRES bodies require explicit bounded scans.'}


class _Blob:
    def __init__(self, name, size, reader, chain, absolute=None):
        self.name, self.size, self.reader, self.chain, self.absolute = name, size, reader, chain, absolute
    def read(self, offset, size):
        return _exact(self.reader, offset, size, self.size)
    def all(self):
        if self.size > MAX_CONTAINER: raise ValueError('Resource container exceeds size limit')
        return b''.join(self.read(p, min(MAX_READ, self.size - p)) for p in range(0, self.size, MAX_READ))
    def digest(self):
        h = hashlib.sha256()
        for p in range(0, self.size, MAX_READ): h.update(self.read(p, min(MAX_READ, self.size - p)))
        return h.hexdigest()


def _guard_bnsh_expansion(data, budget, program_limit):
    """Bound repeated reflection pointers before the existing parser allocates."""
    cursor, total_programs = 0, 0
    while (cursor := data.find(b'BNSH\0\0\0\0', cursor)) >= 0:
        if len(data) - cursor < 32: raise ValueError('Truncated BNSH header')
        size, = struct.unpack_from('<I', data, cursor + 28)
        if size < 32 or size > min(MAX_CONTAINER, len(data) - cursor):
            raise ValueError('BNSH container exceeds resource bounds')
        view = memoryview(data)[cursor:cursor + size]
        def fields(fmt, offset):
            needed = struct.calcsize(fmt)
            if offset < 0 or offset > size or needed > size - offset:
                raise ValueError('BNSH metadata exceeds resource bounds')
            return struct.unpack_from(fmt, view, offset)
        first, = fields('<H', 22)
        count, array, _ = fields('<IQQ', first + 28)
        total_programs += count
        if total_programs > program_limit or array > size or count * 64 > size - array:
            raise ValueError('Aggregate BNSH program count or array bounds exceeded')
        for index in range(count):
            program, = fields('<Q', array + index * 64 + 16)
            reflection, = fields('<Q', program + 0x78)
            if not reflection: continue
            for record in fields('<6Q', reflection):
                if not record: continue
                for dictionary in fields('<5Q', record):
                    if not dictionary: continue
                    count_names, = fields('<I', dictionary + 4)
                    budget['items'] -= count_names
                    if count_names > 4096 or budget['items'] < 0:
                        raise ValueError('BNSH reflection expansion item limit exceeded')
                    for name_index in range(count_names):
                        string, = fields('<Q', dictionary + 0x20 + name_index * 16)
                        length, = fields('<H', string)
                        budget['strings'] -= length
                        if budget['strings'] < 0:
                            raise ValueError('BNSH reflection string expansion limit exceeded')
        cursor += size
    return total_programs


def _summarize_bnsh(container, data):
    stages, c1, unique = Counter(), Counter(), {}
    for variation in container['variations']:
        for shader in variation['shaders']:
            stages[shader['stage']] += 1
            reflected = variation.get('reflection', {}).get(shader['stage'], {})
            reflection_hash = hashlib.sha256(json.dumps(reflected, sort_keys=True).encode()).hexdigest()
            key = (shader['stage'], shader['control_sha256'], shader['code_sha256'], reflection_hash)
            if key not in unique:
                control = data[shader['control_offset']:shader['control_offset'] + shader['control_bytes']]
                if len(control) >= 0x708:
                    begin, end = struct.unpack_from('<II', control, 0x700)
                    c1_range = {'begin': begin, 'end': end, 'code_bytes': shader['code_bytes'],
                                'within_code': begin <= end <= shader['code_bytes']}
                else:
                    c1_range = None
                unique[key] = {**shader, 'reflection': reflected, 'reflection_sha256': reflection_hash,
                               'c1_range': c1_range, 'variations': []}
            unique[key]['variations'].append(variation['index'])
            r = unique[key]['c1_range']
            c1['missing' if r is None else 'bounded' if r['within_code'] else 'outside_code'] += 1
    return {'version': container['version'], 'name': container['name'], 'bytes': container['file_bytes'],
            'sha256': container['sha256'], 'programs': len(container['variations']),
            'target_api': container['target_api'], 'target_api_version': container['target_api_version'],
            'target_code_type': container['target_code_type'], 'stage_counts': dict(stages),
            'unique_stages': sorted(unique.values(), key=lambda s:(s['stage'], s['code_sha256'], s['control_sha256'], s['reflection_sha256'])),
            'c1_range_counts': dict(c1), 'compatibility': {
                'existing_bnsh_parser': 'accepted', 'runtime_stage_kinds': set(stages) <= {'vertex', 'fragment'},
                'resource_bindings': 'unverified', 'main_only_2d_assumption_applied': False}}


def scan_romfs(image, paths, destination=None, *, max_expanded_bytes=512 * 1024**2, max_depth=8, max_reflection_items=1_000_000):
    """Inspect selected resources recursively; optionally publish private BNSHs.

    Original physical image offsets stop at a compression boundary. Each later
    decoded offset remains attached to the exact encoded+decoded hash chain.
    """
    image = RomFSImage(image)
    destination = Path(destination) if destination is not None else None
    if destination is not None and (destination.exists() or destination.is_symlink()):
        raise FileExistsError('Refusing to replace existing resource shader output')
    if max_expanded_bytes < 0 or not 0 <= max_depth <= 32 or not 0 <= max_reflection_items <= 1_000_000:
        raise ValueError('Invalid resource scan limits')
    if destination is not None: destination.parent.mkdir(parents=True, exist_ok=True)
    workspace = tempfile.TemporaryDirectory(prefix='.botw-resource-shaders-', dir=destination.parent) if destination is not None else nullcontext(None)
    report = {'schema': 1, 'image_bytes': image.size, 'roots': [], 'containers': [], 'bnsh': [], 'unclassified': []}
    expanded, visited, programs = 0, 0, 0
    reflection_budget = {'items': max_reflection_items, 'strings': 64 * 1024**2}
    with workspace as temp:
        stage = Path(temp) / 'resources' if temp is not None else None
        if stage is not None: stage.mkdir(mode=0o700)
        def visit(blob, depth):
            nonlocal expanded, visited, programs
            visited += 1
            if depth > max_depth or visited > MAX_NODES:
                raise ValueError('Resource nesting or node count limit exceeded')
            prefix = blob.read(0, min(32, blob.size))
            if prefix[:4] == b'Yaz0':
                if len(prefix) < 16: raise ValueError('Truncated Yaz0 header')
                declared, = struct.unpack_from('>I', prefix, 4)
                if declared > max_expanded_bytes - expanded:
                    raise ValueError('Aggregate Yaz0 expansion limit exceeded')
                encoded = blob.all(); decoded = decompress_yaz0(encoded, max_output=max_expanded_bytes - expanded)
                expanded += len(decoded)
                origin = {'kind': 'yaz0', 'encoded_bytes': len(encoded), 'encoded_sha256': hashlib.sha256(encoded).hexdigest(),
                          'decoded_bytes': len(decoded), 'decoded_sha256': hashlib.sha256(decoded).hexdigest(), 'image_offset': blob.absolute}
                visit(_Blob(blob.name, len(decoded), lambda off, size: decoded[off:off + size], blob.chain + [origin]), depth + 1)
                return
            if prefix[:4] == b'SARC':
                rows = read_sarc(blob.read, blob.size)
                report['containers'].append({'format': 'SARC', 'members': len(rows), 'source_chain': blob.chain})
                for row in rows:
                    subprefix = blob.read(row.offset, min(32, row.size))
                    if not _interesting(row.name, subprefix): continue
                    child = _Blob(row.name, row.size,
                                  lambda off, size, row=row: blob.read(row.offset + off, size), [],
                                  blob.absolute + row.offset if blob.absolute is not None else None)
                    child.chain = blob.chain + [{'kind': 'sarc_member', 'name': row.name, 'offset': row.offset,
                                                  'image_offset': child.absolute, 'bytes': row.size, 'sha256': child.digest()}]
                    visit(child, depth + 1)
                return
            data = blob.all()
            if prefix[:4] in (b'FSHA', b'FRES'):
                if len(data) < 32 or data[12:14] != b'\xff\xfe' or struct.unpack_from('<I', data, 28)[0] != len(data):
                    raise ValueError('Invalid FSHA/FRES common file header bounds')
                version, = struct.unpack_from('<I', data, 8)
                report['containers'].append({'format': prefix[:4].decode(), 'version': f'{version >> 16}.{version >> 8 & 255}.{version & 255}',
                                             'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'source_chain': blob.chain})
            programs += _guard_bnsh_expansion(data, reflection_budget, MAX_NODES - programs)
            embedded = inspect_embedded_bnsh(data)
            if not embedded and prefix[:4] not in (b'FSHA', b'FRES'):
                identity = hashlib.sha256(data).hexdigest()
                suffix = PurePosixPath(blob.name).suffix.lower()
                if suffix not in SHADER_SUFFIXES | {'.gsh'}: suffix = '.bin'
                filename = identity + suffix
                if stage is not None and not (stage / filename).exists():
                    with (stage / filename).open('xb') as out:
                        (stage / filename).chmod(0o600); out.write(data)
                opaque = {'name': blob.name, 'magic': _magic(prefix), 'bytes': blob.size,
                          'sha256': identity, 'file': filename if stage is not None else None,
                          'bnsh_parser_compatible': False,
                          'nvn_control_magic_candidates': data.count(b'\x34\x12\x76\x98'),
                          'nvn_code_magic_candidates': data.count(b'\x78\x56\x34\x12'),
                          'source_chain': blob.chain}
                if prefix[:4] == b'BAHS' and len(data) >= 12:
                    opaque['header_version_u32'], opaque['header_size_u32'] = struct.unpack_from('<II', data, 4)
                    opaque['declared_size_matches'] = opaque['header_size_u32'] == len(data)
                report['unclassified'].append(opaque)
            for container in embedded:
                start, size = container['offset'], container['file_bytes']
                payload = data[start:start + size]
                filename = container['sha256'] + '.bnsh'
                if stage is not None and not (stage / filename).exists():
                    with (stage / filename).open('xb') as out:
                        (stage / filename).chmod(0o600); out.write(payload)
                origin = {'kind': 'bnsh', 'offset': start, 'bytes': size, 'sha256': container['sha256'],
                          'image_offset': blob.absolute + start if blob.absolute is not None else None}
                report['bnsh'].append({**_summarize_bnsh(container, payload), 'file': filename if stage is not None else None,
                                       'source_chain': blob.chain + [origin]})
        for specification in paths:
            parts = specification.split('::')
            if len(parts) > 2: raise ValueError('Only one raw SARC member selector is supported')
            path = parts[0]
            selected = _member_name(parts[1].encode()) if len(parts) == 2 else None
            entry = image.lookup(path)
            if entry.size > MAX_CONTAINER: raise ValueError('Selected RomFS resource exceeds size limit')
            blob = _Blob(path, entry.size, lambda off, size, path=path: image.read_file(path, off, size), [], entry.offset)
            root = {'kind': 'romfs', 'romfs_path': path, 'image_offset': entry.offset, 'bytes': entry.size, 'sha256': blob.digest()}
            blob.chain = [root]; report['roots'].append(root)
            if selected is None:
                visit(blob, 0)
            else:
                rows = read_sarc(blob.read, blob.size)
                row = next((member for member in rows if member.name == selected), None)
                if row is None: raise FileNotFoundError(selected)
                child = _Blob(row.name, row.size, lambda off, size, row=row, blob=blob: blob.read(row.offset + off, size), [], entry.offset + row.offset)
                child.chain = [root, {'kind': 'sarc_member', 'name': row.name, 'offset': row.offset,
                                      'image_offset': child.absolute, 'bytes': row.size, 'sha256': child.digest()}]
                visit(child, 1)
        report['summary'] = {'visited_nodes': visited, 'decoded_bytes': expanded, 'bnsh_containers': len(report['bnsh']),
                             'programs': programs, 'stage_records': sum(sum(row['stage_counts'].values()) for row in report['bnsh']),
                             'unique_code_sha256': len({s['code_sha256'] for c in report['bnsh'] for s in c['unique_stages']}),
                             'container_formats': dict(Counter(c['format'] for c in report['containers']))}
        if stage is not None:
            (stage / 'report.json').write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
            (stage / 'report.json').chmod(0o600)
            stage.rename(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    cat = commands.add_parser('catalogue'); cat.add_argument('image'); cat.add_argument('output')
    scan = commands.add_parser('scan'); scan.add_argument('image'); scan.add_argument('destination'); scan.add_argument('paths', nargs='+', help='RomFS path, optionally followed by ::exact/raw/SARC/member')
    args = parser.parse_args()
    if args.command == 'catalogue':
        result = catalogue_romfs(args.image)
        output = Path(args.output)
        with output.open('x') as stream:
            output.chmod(0o600); stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        print(json.dumps({key: result[key] for key in ('file_count', 'root_magics', 'yaz0_prefix_magics', 'extensions', 'sarc_member_extensions')}, sort_keys=True))
    else:
        result = scan_romfs(args.image, args.paths, args.destination)
        print(json.dumps(result['summary'], sort_keys=True))


if __name__ == '__main__': main()
