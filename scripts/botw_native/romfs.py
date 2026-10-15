"""Read-only Switch RomFS mount and bounded solid-NCZ resource preparation.

Format references: nicoboss/nsz docs/formats.md, SciresM/hactool ivfc.h,
and RFC 8878 section 3.1.1 (Zstandard frame geometry).
This module never decrypts NCA headers, executes guest code or retains NCZ keys.
"""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import tempfile

from .archive import LimitedReader, exact, read_pfs0

EMPTY = 0xffffffff
HEADER_SIZE = 0x50
MAX_METADATA = 64 * 1024**2
MAX_ENTRIES = 1_000_000
MAX_IMAGE = 64 * 1024**3
MAX_READ = 16 * 1024**2
SCAN_PREFIX = 64 * 1024**2


@dataclass(frozen=True)
class RomFSFile:
    path: str
    offset: int  # Absolute byte offset in the saved RomFS image, not the NCZ.
    size: int


def _name(raw, root=False):
    try:
        name = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Invalid RomFS UTF-8 name') from exc
    if root and not name:
        return name
    if (not name or len(raw) > 255 or name in ('.', '..') or
            any(c in name for c in '/\\:\0') or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('Unsafe RomFS entry name')
    return name


def _path(path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('RomFS paths must be absolute POSIX paths')
    if path == '/':
        return path
    for part in path[1:].split('/'):
        _name(part.encode('utf-8'))
    if len(path.encode('utf-8')) > 0x300:
        raise ValueError('RomFS path length limit exceeded')
    return path


def _layout(header, image_size):
    if len(header) != HEADER_SIZE:
        raise ValueError('Truncated RomFS header')
    values = struct.unpack('<10Q', header)
    if values[0] != HEADER_SIZE or not HEADER_SIZE <= values[9] <= image_size <= MAX_IMAGE:
        raise ValueError('Invalid RomFS header or image bounds')
    ranges, total = [(0, HEADER_SIZE)], 0
    for pos in (1, 3, 5, 7):
        start, size = values[pos:pos + 2]
        if start < HEADER_SIZE or start % 4 or size % 4 or start > image_size or size > image_size - start:
            raise ValueError('RomFS metadata exceeds image bounds')
        if pos in (1, 5) and not size:
            raise ValueError('RomFS hash table is empty')
        total += size
        if total > MAX_METADATA:
            raise ValueError('RomFS metadata expansion limit exceeded')
        if size:
            ranges.append((start, start + size))
    ranges.sort()
    if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
        raise ValueError('Overlapping RomFS metadata tables')
    if any(start <= values[9] < end for start, end in ranges):
        raise ValueError('RomFS data starts inside metadata')
    return values, ranges


def _entries(table, directory=False):
    result, pos, fixed = {}, 0, 24 if directory else 32
    while pos < len(table):
        if len(result) >= MAX_ENTRIES or len(table) - pos < fixed:
            raise ValueError('RomFS metadata entry limit or truncation')
        values = struct.unpack_from('<6I' if directory else '<IIQQII', table, pos)
        size = values[-1]
        end = pos + ((fixed + size + 3) & ~3)
        if end > len(table):
            raise ValueError('RomFS entry name exceeds metadata bounds')
        name = _name(table[pos + fixed:pos + fixed + size], directory and pos == 0)
        result[pos] = (*values[:-1], name)
        pos = end
    return result


def _validate_hash_table(table, entries, link_index):
    visited = set()
    for (head,) in struct.iter_unpack('<I', table):
        while head != EMPTY:
            if head not in entries or head in visited:
                raise ValueError('Invalid, duplicate or cyclic RomFS hash chain')
            visited.add(head)
            head = entries[head][link_index]
    if visited != entries.keys():
        raise ValueError('Unindexed RomFS metadata entry')


class RomFSImage:
    """Validated image index suitable for an IPC file service.

    lookup(path) returns a RomFSFile whose offset is usable with pread on the
    same image. read_file clamps reads at EOF and caps each request at 16 MiB.
    No file content is cached, and directory traversal never follows host paths.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        with self.path.open('rb') as stream:
            values, ranges = _layout(exact(stream, HEADER_SIZE), self.size)
            self.data_offset = values[9]
            tables = []
            for pos in (1, 3, 5, 7):
                stream.seek(values[pos]); tables.append(exact(stream, values[pos + 1]))
        dirs, files = _entries(tables[1], True), _entries(tables[3])
        if 0 not in dirs or dirs[0][0] not in (0, EMPTY) or dirs[0][1] != EMPTY or dirs[0][-1]:
            raise ValueError('Invalid RomFS root directory')
        if len(dirs) + len(files) > MAX_ENTRIES:
            raise ValueError('RomFS entry limit exceeded')
        _validate_hash_table(tables[0], dirs, 4)
        _validate_hash_table(tables[2], files, 4)
        self.files, self.directories = {}, {'/'}
        seen_dirs, seen_files = set(), set()
        pending = [(0, None, '/')]
        occupied = []
        while pending:
            position, parent, parent_path = pending.pop()
            if position not in dirs or position in seen_dirs:
                raise ValueError('Invalid, duplicate or cyclic RomFS directory link')
            seen_dirs.add(position)
            dparent, sibling, child, file, _, name = dirs[position]
            if parent is None:
                path = '/'
            else:
                if dparent != parent:
                    raise ValueError('RomFS directory parent mismatch')
                path = _path(parent_path.rstrip('/') + '/' + name)
                if path in self.directories or path in self.files:
                    raise ValueError('Duplicate RomFS path')
                self.directories.add(path)
            if sibling != EMPTY:
                pending.append((sibling, parent, parent_path))
            if child != EMPTY:
                pending.append((child, position, path))
            while file != EMPTY:
                if file not in files or file in seen_files:
                    raise ValueError('Invalid, duplicate or cyclic RomFS file link')
                seen_files.add(file)
                fparent, next_file, offset, size, _, name = files[file]
                if fparent != position:
                    raise ValueError('RomFS file parent mismatch')
                filepath = _path(path.rstrip('/') + '/' + name)
                if filepath in self.files or filepath in self.directories:
                    raise ValueError('Duplicate RomFS path')
                absolute = self.data_offset + offset
                if absolute > self.size or size > self.size - absolute:
                    raise ValueError('RomFS file exceeds image bounds')
                if size:
                    end = absolute + size
                    if any(absolute < upper and end > lower for lower, upper in ranges):
                        raise ValueError('RomFS file overlaps metadata')
                    occupied.append((absolute, end))
                self.files[filepath] = RomFSFile(filepath, absolute, size)
                file = next_file
        if seen_dirs != dirs.keys() or seen_files != files.keys():
            raise ValueError('Unreachable RomFS metadata entry')
        occupied.sort()
        if any(a[1] > b[0] for a, b in zip(occupied, occupied[1:])):
            raise ValueError('Overlapping RomFS file payloads')
        self._children = {path: [] for path in self.directories}
        for path in self.directories.union(self.files):
            if path != '/':
                self._children[str(PurePosixPath(path).parent)].append(PurePosixPath(path).name)
        for names in self._children.values(): names.sort()

    def lookup(self, path):
        path = _path(path)
        if path in self.directories:
            raise IsADirectoryError(path)
        try:
            return self.files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def iterdir(self, path='/'):
        path = _path(path)
        if path in self.files:
            raise NotADirectoryError(path)
        try:
            return list(self._children[path])
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def read_file(self, path, offset=0, size=MAX_READ):
        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or not 0 <= size <= MAX_READ:
            raise ValueError('RomFS read must be a nonnegative bounded range (at most 16 MiB)')
        entry = self.lookup(path)
        length = min(size, max(0, entry.size - offset))
        if not length: return b''
        with self.path.open('rb') as stream:
            stream.seek(entry.offset + offset)
            return exact(stream, length)

    def manifest(self, progress=None):
        digest = hashlib.sha256()
        with self.path.open('rb') as stream:
            remaining = self.size
            while remaining:
                chunk = exact(stream, min(MAX_READ, remaining))
                digest.update(chunk); remaining -= len(chunk)
        entries = []
        with self.path.open('rb') as stream:
            for file in sorted(self.files.values(), key=lambda f: f.offset):
                stream.seek(file.offset)
                file_digest, remaining = hashlib.sha256(), file.size
                while remaining:
                    chunk = exact(stream, min(MAX_READ, remaining))
                    file_digest.update(chunk); remaining -= len(chunk)
                entries.append({**asdict(file), 'sha256': file_digest.hexdigest()})
        if progress: progress('hashed', self.size, self.size)
        return {'schema': 1, 'format': 'switch-romfs',
                'image': {'name': 'romfs.bin', 'bytes': self.size, 'sha256': digest.hexdigest()},
                'data_offset': self.data_offset, 'directory_count': len(self.directories),
                'directories': sorted(self.directories), 'file_count': len(entries),
                'payload_bytes': sum(e['size'] for e in entries),
                'files': sorted(entries, key=lambda e: e['path'])}

    def export(self, destination, paths=None):
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('Refusing to replace an existing RomFS export')
        selected = [self.lookup(p) for p in paths] if paths is not None else list(self.files.values())
        # Host filesystems can fold case or normalize Unicode; reject aliases before writing.
        import unicodedata
        aliases, chosen = {}, set()
        for entry in selected:
            if entry.path in chosen: raise ValueError('Duplicate RomFS export selection')
            chosen.add(entry.path)
            parts = PurePosixPath(entry.path)
            for candidate in (parts, *parts.parents):
                original = str(candidate)
                alias = unicodedata.normalize('NFD', original).casefold()
                if alias in aliases and aliases[alias] != original:
                    raise ValueError('RomFS paths alias on the host filesystem')
                aliases[alias] = original
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.botw-romfs-export-', dir=destination.parent) as temp:
            stage = Path(temp) / 'RomFS'; stage.mkdir(mode=0o700)
            for entry in selected:
                outpath = stage.joinpath(*PurePosixPath(entry.path).parts[1:])
                outpath.parent.mkdir(parents=True, exist_ok=True)
                with outpath.open('xb') as out:
                    outpath.chmod(0o600)
                    position = 0
                    while position < entry.size:
                        data = self.read_file(entry.path, position, MAX_READ)
                        out.write(data); position += len(data)
            stage.rename(destination)


def _candidate_offsets(prefix, size):
    candidates, start, needle = [], 0, struct.pack('<Q', HEADER_SIZE)
    while (position := prefix.find(needle, start)) >= 0:
        start = position + 1
        if position % 4 or position + HEADER_SIZE > len(prefix): continue
        try:
            _layout(prefix[position:position + HEADER_SIZE], size - position)
        except ValueError:
            continue
        candidates.append(position)
    return candidates


def _validate_solid_frame(stream, start, end, expected_size):
    # stream_reader accepts an unfinished frame after all payload bytes are
    # decoded. Check frame/block geometry first so a missing checksum or final
    # block cannot be silently accepted. libzstd verifies compressed content.
    stream.seek(start)
    bounded = LimitedReader(stream, end)
    if exact(bounded, 4) != b'\x28\xb5\x2f\xfd':
        raise ValueError('Only solid Zstandard NCZ streams are supported')
    descriptor = exact(bounded, 1)[0]
    if descriptor & 0x18:
        raise ValueError('Invalid Zstandard frame descriptor')
    single = bool(descriptor & 0x20)
    window = None
    if not single:
        wd = exact(bounded, 1)[0]
        base = 1 << (10 + (wd >> 3))
        window = base + (base >> 3) * (wd & 7)
    dictionary_size = (0, 1, 2, 4)[descriptor & 3]
    exact(bounded, dictionary_size)
    content_size_bytes = ((1 if single else 0), 2, 4, 8)[descriptor >> 6]
    if content_size_bytes:
        content_size = int.from_bytes(exact(bounded, content_size_bytes), 'little')
        if content_size_bytes == 2: content_size += 256
        if content_size != expected_size:
            raise ValueError('Zstandard frame content size differs from NCZ sections')
        if single: window = content_size
    if window is None or window > 128 * 1024**2:
        raise ValueError('Zstandard window expansion limit exceeded')
    for _ in range(1_000_000):
        block = int.from_bytes(exact(bounded, 3), 'little')
        kind, size = (block >> 1) & 3, block >> 3
        if kind == 3 or size > min(window, 128 * 1024):
            raise ValueError('Invalid Zstandard block type or size')
        length = 1 if kind == 1 else size
        if length > end - stream.tell():
            raise ValueError('Truncated Zstandard block')
        stream.seek(length, 1)
        if block & 1:
            if descriptor & 4: exact(bounded, 4)
            if stream.tell() != end:
                raise ValueError('Trailing bytes after solid Zstandard frame')
            stream.seek(start)
            return
    raise ValueError('Zstandard block count limit exceeded')


def _sections(stream, member):
    if member.size < 0x4050:
        raise ValueError('Truncated NCZ')
    stream.seek(member.offset + 0x4000)
    if exact(stream, 8) != b'NCZSECTN':
        raise ValueError('Unsupported NCZ section format')
    count = struct.unpack('<Q', exact(stream, 8))[0]
    if not 0 < count <= 64 or 0x4010 + count * 64 >= member.size:
        raise ValueError('Invalid NCZ section count')
    sections, previous, total = [], 0x4000, 0
    for _ in range(count):
        # The remaining descriptor bytes are transient and are not saved or logged.
        offset, length, crypto = struct.unpack('<QQQ', exact(stream, 64)[:24])
        if offset != previous or not 0 < length <= MAX_IMAGE or crypto not in (1, 3, 4):
            raise ValueError('Unsupported or non-contiguous NCZ section')
        previous += length; total += length
        if total > MAX_IMAGE: raise ValueError('NCZ aggregate expansion limit exceeded')
        sections.append((offset, length))
    start = stream.tell()
    _validate_solid_frame(stream, start, member.offset + member.size, total)
    return sections


def extract_romfs(package, destination, progress=None):
    """Prepare one validated RomFS image and deterministic per-file manifest.

    NCZ sections must be contiguous and total at most 64 GiB. RomFS discovery
    examines at most the first 64 MiB per section; the image is copied in 16 MiB
    chunks. All declared compressed data is consumed before atomic publication.
    """
    import zstandard
    package, destination = Path(package), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Refusing to replace existing RomFS resources')
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_size = package.stat().st_size
    with tempfile.TemporaryDirectory(prefix='.botw-romfs-', dir=destination.parent) as temp:
        stage = Path(temp) / 'resources'; stage.mkdir(mode=0o700)
        match = None
        with package.open('rb') as stream:
            for member in read_pfs0(stream, 0, source_size):
                if not member.name.endswith('.ncz'): continue
                sections = _sections(stream, member)
                try:
                    with zstandard.ZstdDecompressor(max_window_size=128 * 1024**2).stream_reader(
                            LimitedReader(stream, member.offset + member.size)) as reader:
                        for index, (section_start, length) in enumerate(sections):
                            prefix = exact(reader, min(SCAN_PREFIX, length))
                            candidates = _candidate_offsets(prefix, length)
                            if len(candidates) > 1 or (candidates and match is not None):
                                raise ValueError('Multiple possible RomFS images in package')
                            remaining = length - len(prefix)
                            if candidates:
                                offset = candidates[0]
                                match = {'package_bytes': source_size, 'ncz_member': member.name,
                                         'section_index': index, 'section_nca_offset': section_start,
                                         'section_bytes': length, 'romfs_section_offset': offset}
                                out = (stage / 'romfs.bin').open('xb')
                                (stage / 'romfs.bin').chmod(0o600)
                            else:
                                out = None
                            try:
                                if out: out.write(prefix[offset:])
                                while remaining:
                                    chunk = exact(reader, min(MAX_READ, remaining))
                                    if out: out.write(chunk)
                                    remaining -= len(chunk)
                                    if progress: progress('decompress', length - remaining, length)
                            finally:
                                if out: out.close()
                        if reader.read(1):
                            raise ValueError('NCZ stream exceeds declared section sizes')
                except zstandard.ZstdError as exc:
                    raise ValueError('Invalid NCZ compressed stream') from exc
        if match is None: raise ValueError('No supported RomFS image found in package')
        image = RomFSImage(stage / 'romfs.bin')
        report = image.manifest(progress)
        report['source'] = match
        (stage / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        (stage / 'manifest.json').chmod(0o600)
        stage.rename(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    extract = sub.add_parser('extract'); extract.add_argument('package'); extract.add_argument('destination')
    inspect = sub.add_parser('inspect'); inspect.add_argument('image')
    export = sub.add_parser('export'); export.add_argument('image'); export.add_argument('destination'); export.add_argument('paths', nargs='*')
    args = parser.parse_args()
    if args.command == 'extract':
        last = [None, -1]
        def progress(phase, done, total):
            percent = done * 100 // total
            if phase != last[0] or percent // 5 > last[1] // 5:
                print(f'{phase}: {percent}% ({done}/{total})', flush=True); last[:] = [phase, percent]
        result = extract_romfs(args.package, args.destination, progress)
        print(json.dumps({k: v for k, v in result.items() if k not in ('files', 'directories')}, sort_keys=True))
    elif args.command == 'inspect':
        print(json.dumps(RomFSImage(args.image).manifest(), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        RomFSImage(args.image).export(args.destination, args.paths or None)


if __name__ == '__main__': main()
