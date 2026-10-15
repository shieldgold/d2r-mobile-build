"""Bounded, read-only SARC/Yaz0 shader discovery with exact source provenance.

Format references: ZeldaMods/oead Yaz0 and ZeldaMods/sarc SARC definitions;
Switch-Toolbox SHARCFBNX.cs for the independently validated SHARCB v9 profile:
https://github.com/KillzXGaming/Switch-Toolbox/blob/master/File_Format_Library/FileFormats/Shader/SHARC/SHARCFBNX.cs
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


def parse_sharcb_v9(data, *, max_records=MAX_NODES, max_symbols=1_000_000, max_string_bytes=64 * 1024**2):
    """Inspect the bounded LE v9/flags=5 profile found in the selected archives.

    Layout reference: Switch-Toolbox SHARCFBNX.cs. Signed shared-control offsets
    and the low-word reflection offsets are additionally checked against this
    profile's declared section spans. The other offset words remain opaque.
    Code boundaries come from the declared pool offset and control c1 end;
    magic and SPH stage fields only cross-check those already bounded spans.
    Macro/default selection and sampler dimensionality are not inferred.
    """
    if (not 0 <= max_records <= MAX_NODES or not 0 <= max_symbols <= 1_000_000 or
            not 0 <= max_string_bytes <= 64 * 1024**2 or
            not 344 <= len(data) <= MAX_CONTAINER):
        raise ValueError('Invalid SHARCB input or inspection limits')
    def span(offset, size, lower=0, upper=None):
        upper = len(data) if upper is None else upper
        if offset < lower or size < 0 or offset > upper or size > upper - offset:
            raise ValueError('SHARCB span exceeds declared section bounds')
        return offset + size
    def fields(fmt, offset, lower=0, upper=None):
        span(offset, struct.calcsize(fmt), lower, upper)
        return struct.unpack_from(fmt, data, offset)
    def digest(offset, size):
        span(offset, size)
        return hashlib.sha256(memoryview(data)[offset:offset + size]).hexdigest()
    def text(offset, size):
        span(offset, size)
        raw = data[offset:offset + size]
        if not raw or raw[-1] or b'\0' in raw[:-1]:
            raise ValueError('Invalid SHARCB terminated string')
        try:
            return raw[:-1].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid SHARCB string UTF-8') from exc

    magic, version, size, flags, reserved, string_size, pool_size, pool = fields('<8I', 0)
    if (magic != 0x53484142 or version != 9 or flags != 5 or reserved or size != len(data) or
            not 1 <= string_size <= MAX_METADATA or not pool_size or pool + pool_size != size):
        raise ValueError('Unsupported or invalid SHARCB v9 profile header')
    string_base, metadata = 336, 336 + string_size
    span(string_base, string_size, 336, min(pool, 64 * 1024**2))
    table_relative, count = fields('<II', metadata, metadata, pool)
    table = metadata + table_relative
    span(table, 8, metadata + 8, min(pool, 64 * 1024**2))
    if not 1 <= count <= max_records:
        raise ValueError('SHARCB variation count limit exceeded')
    symbols_left, string_bytes_left = max_symbols, max_string_bytes
    control_ranges, code_ranges, variations = set(), set(), []
    position = metadata + 8
    kinds = {0: ('vertex', 1), 1: ('fragment', 5), 2: ('geometry', 4)}
    for index in range(count):
        section_size, kind, zero, sub_size = fields('<4I', position, metadata + 8, table)
        section_end = span(position, section_size, position, table)
        p = position + 16
        # The fixed descriptor ends at p+68. Some tables start there; owned
        # controls start at p+72. Those four bytes are not an unknown field.
        span(p, 68, position, section_end)
        if zero or sub_size != section_size - 16 or kind not in kinds:
            raise ValueError('Unsupported SHARCB variation descriptor')
        code_relative, control_size, control_relative, control_upper = fields('<QIiI', p)
        control = p + control_relative
        local_spans = []
        if control_relative == 72:
            span(control, control_size, p + 68, section_end)
            control_ranges.add((control, control_size)); local_spans.append((control, control + control_size))
        elif control_relative >= 0 or (control, control_size) not in control_ranges:
            raise ValueError('SHARCB control alias does not match an earlier owned control')
        if (control_size != 2000 or fields('<I', control)[0] != 0x98761234 or
                fields('<I', control + 20)[0] != control_size):
            raise ValueError('Unsupported or invalid SHARCB NVN control profile')
        c1_begin, c1_end = fields('<II', control + 0x700)
        if not 0 <= c1_begin <= c1_end or c1_end < 0x34:
            raise ValueError('Invalid SHARCB control code/constant bounds')
        code = pool + code_relative
        code_end = span(code, c1_end, pool, size)
        stage, sph_kind = kinds[kind]
        if fields('<I', code)[0] != 0x12345678 or (fields('<I', code + 0x30)[0] >> 10) & 15 != sph_kind:
            raise ValueError('SHARCB declared stage disagrees with bounded NVN header')
        code_ranges.add((code, code_end))
        reflection, reflection_spans, upper_words = {}, {}, {}
        for name, count_at, offset_at, stride in [('uniform_blocks', 20, 24, 16), ('attributes', 32, 36, 12),
                                                  ('uniforms', 44, 48, 12), ('samplers', 56, 60, 12)]:
            entries, = fields('<I', p + count_at)
            relative, upper = fields('<II', p + offset_at)
            if entries > symbols_left:
                raise ValueError('SHARCB reflection expansion limit exceeded')
            symbols_left -= entries; upper_words[name] = upper
            offset = p + relative
            if entries:
                end = span(offset, entries * stride, p + 68, section_end)
                local_spans.append((offset, end))
            reflection_spans[name] = {'offset': offset if entries else None, 'count': entries, 'stride': stride,
                                      'relative_offset_low_word': relative}
            rows = []
            for entry in range(entries):
                entry_offset = offset + entry * stride
                name_relative, name_upper = fields('<II', entry_offset)
                name_offset = string_base + name_relative
                if name_upper or not string_base <= name_offset < metadata:
                    raise ValueError('SHARCB symbol name pointer exceeds string region')
                terminator = data.find(b'\0', name_offset, min(metadata, name_offset + 4096))
                if terminator < 0 or terminator + 1 - name_offset > string_bytes_left:
                    raise ValueError('SHARCB symbol string expansion limit exceeded')
                name_bytes = terminator + 1 - name_offset; string_bytes_left -= name_bytes
                row = {'name': text(name_offset, name_bytes), 'name_offset_upper_word': name_upper}
                if name == 'uniform_blocks':
                    row['location'], row['size'] = fields('<iI', entry_offset + 8)
                else:
                    row['offset' if name == 'uniforms' else 'location'] = fields('<I' if name == 'uniforms' else '<i', entry_offset + 8)[0]
                rows.append(row)
            reflection[name] = rows
        local_spans.sort()
        if any(a[1] > b[0] for a, b in zip(local_spans, local_spans[1:])):
            raise ValueError('Overlapping SHARCB control/reflection spans')
        reflection_sha = hashlib.sha256(json.dumps(reflection, sort_keys=True).encode()).hexdigest()
        variations.append({'index': index, 'record_offset': position, 'record_bytes': section_size, 'stage': stage,
                           'stage_type': kind, 'control_offset': control, 'control_bytes': control_size,
                           'control_relative_offset': control_relative, 'control_offset_upper_word': control_upper,
                           'control_sha256': digest(control, control_size), 'code_pool_relative_offset': code_relative,
                           'code_offset': code, 'code_bytes': c1_end, 'code_sha256': digest(code, c1_end),
                           'c1_begin': c1_begin, 'c1_end': c1_end, 'c1_offset': code + c1_begin,
                           'c1_bytes': c1_end - c1_begin, 'c1_sha256': digest(code + c1_begin, c1_end - c1_begin),
                           'reflection': reflection, 'reflection_sha256': reflection_sha,
                           'reflection_spans': reflection_spans, 'reflection_offset_upper_words': upper_words})
        position = section_end
    if position != table:
        raise ValueError('SHARCB variation chain does not end at its program table')
    ordered_codes = sorted(code_ranges)
    if any(a[1] > b[0] for a, b in zip(ordered_codes, ordered_codes[1:])):
        raise ValueError('Partially overlapping SHARCB code spans')
    table_size, program_count = fields('<II', table)
    table_end = span(table, table_size, table, min(pool, 64 * 1024**2))
    if table_size < 8 or not 1 <= program_count <= max_records:
        raise ValueError('SHARCB program count limit exceeded')
    position, programs, program_names = table + 8, [], set()
    for index in range(program_count):
        section_size, name_size, mask, first = fields('<4I', position, table + 8, table_end)
        end = span(position, section_size, position, table_end)
        if section_size < 16 or mask not in (3, 7) or not 1 <= name_size <= min(4096, string_bytes_left):
            raise ValueError('Unsupported SHARCB program stage mask or name length')
        string_bytes_left -= name_size
        tail = span(position + 16, name_size, position + 16, end)
        name = text(position + 16, name_size)
        if not name or name in program_names or first >= count:
            raise ValueError('Invalid SHARCB program name or base variation index')
        program_names.add(name)
        programs.append({'index': index, 'name': name, 'record_offset': position, 'record_bytes': section_size,
                         'stage_mask': mask, 'base_index': first, 'tail_offset': tail,
                         'tail_bytes': end - tail, 'tail_sha256': digest(tail, end - tail),
                         'macro_environment': 'unparsed'})
        position = end
    if position != table_end or programs[0]['base_index'] != 0:
        raise ValueError('SHARCB program chain does not exactly cover its table/variations')
    for index, program in enumerate(programs):
        end = programs[index + 1]['base_index'] if index + 1 < len(programs) else count
        first, stage_order = program['base_index'], [k for k in kinds if program['stage_mask'] & (1 << k)]
        if end <= first or (end - first) % len(stage_order):
            raise ValueError('SHARCB program range does not contain complete declared stage groups')
        if any(variations[v]['stage_type'] != stage_order[(v - first) % len(stage_order)] for v in range(first, end)):
            raise ValueError('SHARCB variation order disagrees with program stage mask')
        program['variation_indices'] = [list(range(v, v + len(stage_order))) for v in range(first, end, len(stage_order))]
    stages = Counter(v['stage'] for v in variations)
    return {'schema': 1, 'format': 'SHARCB', 'version': version, 'profile_flags': flags, 'bytes': size,
            'sha256': digest(0, size), 'string_region': {'offset': string_base, 'bytes': string_size},
            'header_opaque_region': {'offset': 32, 'bytes': 304, 'sha256': digest(32, 304)},
            'metadata_offset': metadata, 'program_table_offset': table, 'program_table_bytes': table_size,
            'code_pool': {'offset': pool, 'bytes': pool_size}, 'variations': variations, 'programs': programs,
            'summary': {'stage_records': count, 'stage_counts': dict(stages), 'programs': program_count,
                        'unique_code_sha256': len({v['code_sha256'] for v in variations}),
                        'unique_control_sha256': len({v['control_sha256'] for v in variations}),
                        'shared_control_records': sum(v['control_relative_offset'] < 0 for v in variations),
                        'nonzero_control_offset_upper_words': sum(bool(v['control_offset_upper_word']) for v in variations),
                        'nonzero_reflection_offset_upper_words': sum(bool(w) for v in variations for w in v['reflection_offset_upper_words'].values()),
                        'reflection_items': max_symbols - symbols_left,
                        'expanded_string_bytes': max_string_bytes - string_bytes_left,
                        'c1_byte_counts': {str(k): v for k, v in sorted(Counter(v['c1_bytes'] for v in variations).items())}},
            'compatibility': {'existing_bnsh_parser': False, 'runtime_stage_kinds': set(stages) <= {'vertex', 'fragment'},
                              'texture_types': 'unverified', 'c0_environment': 'unverified', 'macro_environment': 'unparsed',
                              'opaque_offset_words': 'preserved, semantics unverified', 'main_only_2d_assumption_applied': False}}


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
    report = {'schema': 1, 'image_bytes': image.size, 'roots': [], 'containers': [], 'bnsh': [], 'sharcb': [], 'unclassified': []}
    expanded, visited, programs, sharcb_records = 0, 0, 0, 0
    reflection_budget = {'items': max_reflection_items, 'strings': 64 * 1024**2}
    with workspace as temp:
        stage = Path(temp) / 'resources' if temp is not None else None
        if stage is not None: stage.mkdir(mode=0o700)
        def publish(filename, data):
            if stage is None: return None
            if not (stage / filename).exists():
                with (stage / filename).open('xb') as out:
                    (stage / filename).chmod(0o600); out.write(data)
            return filename
        def visit(blob, depth):
            nonlocal expanded, visited, programs, sharcb_records
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
            if prefix[:4] == b'BAHS' and len(data) >= 344:
                archive = parse_sharcb_v9(data, max_records=MAX_NODES - programs - sharcb_records,
                                           max_symbols=reflection_budget['items'], max_string_bytes=reflection_budget['strings'])
                sharcb_records += archive['summary']['stage_records']
                reflection_budget['items'] -= archive['summary']['reflection_items']
                reflection_budget['strings'] -= archive['summary']['expanded_string_bytes']
                archive['name'] = blob.name
                archive['file'] = publish(archive['sha256'] + '.sharcb', data)
                archive['source_chain'] = blob.chain + [{'kind': 'sharcb', 'offset': 0, 'bytes': len(data),
                                                        'sha256': archive['sha256'], 'image_offset': blob.absolute}]
                for variation in archive['variations']:
                    for kind, suffix in [('control', '.control'), ('code', '.nvn'), ('c1', '.cbuf1')]:
                        offset, size = variation[kind + '_offset'], variation[kind + '_bytes']
                        filename = variation[kind + '_sha256'] + suffix
                        variation[kind + '_file'] = publish(filename, memoryview(data)[offset:offset + size])
                report['sharcb'].append(archive)
                return
            if prefix[:4] in (b'FSHA', b'FRES'):
                if len(data) < 32 or data[12:14] != b'\xff\xfe' or struct.unpack_from('<I', data, 28)[0] != len(data):
                    raise ValueError('Invalid FSHA/FRES common file header bounds')
                version, = struct.unpack_from('<I', data, 8)
                report['containers'].append({'format': prefix[:4].decode(), 'version': f'{version >> 16}.{version >> 8 & 255}.{version & 255}',
                                             'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'source_chain': blob.chain})
            programs += _guard_bnsh_expansion(data, reflection_budget, MAX_NODES - programs - sharcb_records)
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
                             'sharcb_containers': len(report['sharcb']), 'sharcb_stage_records': sharcb_records,
                             'sharcb_programs': sum(c['summary']['programs'] for c in report['sharcb']),
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
