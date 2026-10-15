"""Bounded container readers. Extract data only; never load guest code."""
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
import re
import struct
import tempfile


@dataclass(frozen=True)
class Member:
    name: str
    offset: int
    size: int


def exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Truncated container')
    return data


def read_pfs0(stream, offset, size):
    if offset < 0 or size < 16:
        raise ValueError('Invalid PFS0 bounds')
    stream.seek(offset)
    magic, count, strings_size, reserved = struct.unpack('<4sIII', exact(stream, 16))
    header_size = 16 + count * 24 + strings_size
    if magic != b'PFS0' or reserved or not 0 < count <= 10000 or strings_size > 4 * 1024**2 or header_size > size:
        raise ValueError('Invalid PFS0 header')
    entries = [struct.unpack('<QQII', exact(stream, 24)) for _ in range(count)]
    strings = exact(stream, strings_size)
    result, names, ranges = [], set(), []
    for pos, length, nameoff, unused in entries:
        end = strings.find(b'\0', nameoff)
        if unused or nameoff >= len(strings) or end < 0 or pos + length > size - header_size:
            raise ValueError('PFS0 member exceeds container bounds')
        try:
            name = strings[nameoff:end].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid PFS0 name encoding') from exc
        if not name or name in ('.', '..') or any(c in name for c in '/\\:\x00') or name in names:
            raise ValueError('Unsafe or duplicate PFS0 member name')
        names.add(name)
        if length:
            ranges.append((pos, pos + length))
        result.append(Member(name, offset + header_size + pos, length))
    ranges.sort()
    if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
        raise ValueError('Overlapping PFS0 members')
    return result


class LimitedReader:
    def __init__(self, stream, end):
        self.stream, self.end = stream, end

    def read(self, size=-1):
        remaining = self.end - self.stream.tell()
        return self.stream.read(remaining if size < 0 else min(size, remaining))


def exefs_members(data):
    # NCA hash tables precede the PFS0. Do not mistake logo/data sections for ExeFS.
    for offset in range(0, min(len(data) - 15, 1024**2), 4):
        if data[offset:offset + 4] != b'PFS0':
            continue
        members = read_pfs0(io.BytesIO(data), offset, len(data) - offset)
        main = next((m for m in members if m.name == 'main'), None)
        if main and data[main.offset:main.offset + 4] == b'NSO0':
            if any(not re.fullmatch(r'(main|rtld|sdk|subsdk\d+|main\.npdm)', m.name) for m in members):
                raise ValueError('Unexpected member in ExeFS')
            return members
    return None


def extract_exefs(package, destination):
    import zstandard

    package, destination = Path(package), Path(destination)
    if destination.exists():
        raise FileExistsError('Refusing to replace an existing ExeFS directory')
    matches = []
    with package.open('rb') as stream:
        for member in read_pfs0(stream, 0, package.stat().st_size):
            if not member.name.endswith('.ncz'):
                continue
            if member.size < 0x4050:
                raise ValueError('Truncated NCZ')
            stream.seek(member.offset + 0x4000)
            if exact(stream, 8) != b'NCZSECTN':
                raise ValueError('Unsupported NCZ section format')
            count = struct.unpack('<Q', exact(stream, 8))[0]
            if not 0 < count <= 64 or 0x4010 + count * 64 >= member.size:
                raise ValueError('Invalid NCZ section count')
            sections, previous = [], 0x4000
            for _ in range(count):
                # Key material in the remainder is deliberately never retained or logged.
                offset, length, crypto = struct.unpack('<QQQ', exact(stream, 64)[:24])
                if offset != previous or not 0 < length <= 64 * 1024**3 or crypto not in (1, 3, 4):
                    raise ValueError('Unsupported or non-contiguous NCZ section')
                sections.append(length)
                previous += length
                if sum(sections) > 64 * 1024**3:
                    raise ValueError('NCZ aggregate expansion limit exceeded')
            start = stream.tell()
            if exact(stream, 4) != b'\x28\xb5\x2f\xfd':
                raise ValueError('Only solid Zstandard NCZ streams are supported')
            stream.seek(start)
            try:
                with zstandard.ZstdDecompressor(max_window_size=128 * 1024**2).stream_reader(
                    LimitedReader(stream, member.offset + member.size)
                ) as reader:
                    for length in sections:
                        chunks, remaining = [], length
                        collect = length <= 64 * 1024**2
                        while remaining:
                            chunk = reader.read(min(16 * 1024**2, remaining))
                            if not chunk:
                                raise ValueError('Truncated NCZ data section')
                            if collect:
                                chunks.append(chunk)
                            remaining -= len(chunk)
                        if collect:
                            data = b''.join(chunks)
                            members = exefs_members(data)
                            if members:
                                if matches:
                                    raise ValueError('Multiple ExeFS sections in package')
                                matches.append({m.name: data[m.offset:m.offset + m.size] for m in members})
                    if reader.read(1):
                        raise ValueError('NCZ stream exceeds declared section sizes')
            except zstandard.ZstdError as exc:
                raise ValueError('Invalid NCZ compressed stream') from exc
    if len(matches) != 1:
        raise ValueError(f'Expected one ExeFS containing NSO main, found {len(matches)}')
    files = matches[0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.botw-exefs-', dir=destination.parent) as temp:
        stage = Path(temp) / 'ExeFS'
        stage.mkdir(mode=0o700)
        report = []
        for name, data in sorted(files.items()):
            path = stage / name
            with path.open('xb') as out:
                path.chmod(0o600)
                out.write(data)
            report.append({'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        stage.rename(destination)
    return {'files': report, 'source_bytes': package.stat().st_size}
