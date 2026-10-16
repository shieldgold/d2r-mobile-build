#!/usr/bin/env python3
"""Static arm64 Mach-O swforce metadata. Never load or execute the file.

Chained layouts follow Apple's SDK mach-o/fixup-chains.h. Only pointer formats
2 (64 absolute VM target) and 6 (64 image-relative target) are implemented;
other formats, external binds in registry fields and missing chain membership
fail rather than being decoded speculatively.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile


class RegistryFormatError(ValueError):
    pass


def canonical_entries(entries):
    result = []
    for row in entries:
        if (set(row) != {'name', 'why', 'titles'} or
                not isinstance(row['name'], str) or not row['name'] or
                not isinstance(row['why'], str) or not row['why'] or
                type(row['titles']) is not int or not 0 < row['titles'] < 1 << 32):
            raise RegistryFormatError('invalid registry metadata triple')
        result.append(dict(name=row['name'], why=row['why'], titles=row['titles']))
    return sorted(result, key=lambda row: (row['name'], row['titles'], row['why']))


def metadata_sha256(entries):
    encoded = json.dumps(canonical_entries(entries), sort_keys=True,
                         separators=(',', ':'), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_registry(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RegistryFormatError('expected one regular, non-symlink Mach-O file')
    data = path.read_bytes()

    def bounded(offset, count, limit=None):
        end = len(data) if limit is None else limit
        if offset < 0 or count < 0 or offset > end or count > end - offset:
            raise RegistryFormatError('Mach-O metadata exceeds file/payload bounds')
        return data[offset:offset + count]

    def unpack(fmt, offset, limit=None):
        return struct.unpack(fmt, bounded(offset, struct.calcsize(fmt), limit))

    magic, cpu, subtype, filetype, ncmds, sizecmds, flags, reserved = unpack('<8I', 0)
    if magic != 0xfeedfacf or cpu != 0x0100000c or filetype not in (2, 6):
        raise RegistryFormatError('requires thin little-endian arm64 executable/dylib Mach-O')
    bounded(32, sizecmds)
    cursor, segments, sections, chained = 32, [], [], None
    for _ in range(ncmds):
        command, size = unpack('<II', cursor, 32 + sizecmds)
        if size < 8 or size % 8:
            raise RegistryFormatError('invalid Mach-O load-command size')
        bounded(cursor, size, 32 + sizecmds)
        if command == 0x19:
            if size < 72:
                raise RegistryFormatError('truncated segment command')
            row = unpack('<II16sQQQQiiII', cursor)
            _, _, name, vm, vmsize, fileoff, filesize, maxprot, initprot, nsects, segflags = row
            name = name.rstrip(b'\0').decode('ascii')
            if size != 72 + nsects * 80 or filesize > vmsize:
                raise RegistryFormatError('invalid segment/section bounds')
            bounded(fileoff, filesize)
            segment = dict(name=name, vm=vm, vmsize=vmsize, fileoff=fileoff,
                           filesize=filesize, initprot=initprot)
            segments.append(segment)
            for index in range(nsects):
                row = unpack('<16s16sQQIIIIIIII', cursor + 72 + 80 * index)
                sectname, segname, address, length, offset = row[:5]
                sectname = sectname.rstrip(b'\0').decode('ascii')
                segname = segname.rstrip(b'\0').decode('ascii')
                sections.append(dict(name=sectname, segment=segname, address=address,
                                     size=length, offset=offset))
        elif command == 0x80000034:
            if size != 16 or chained is not None:
                raise RegistryFormatError('invalid or duplicate chained-fixups command')
            chained = unpack('<II', cursor + 8)
        cursor += size
    if cursor != 32 + sizecmds or chained is None:
        raise RegistryFormatError('incomplete load commands or missing chained fixups')
    text = [s for s in segments if s['name'] == '__TEXT' and s['fileoff'] == 0]
    registry = [s for s in sections if s['name'] == 'swforce']
    if len(text) != 1 or len(registry) != 1:
        raise RegistryFormatError('requires unique __TEXT image base and swforce section')
    imagebase = text[0]['vm']
    section = registry[0]
    if section['segment'] != '__DATA':
        raise RegistryFormatError('swforce must match the actual loader __DATA section lookup')
    if not section['size'] or section['size'] % 32:
        raise RegistryFormatError('swforce is not a nonempty array of 32-byte records')

    def vm_file(address, count):
        matches = [s for s in segments if address >= s['vm'] and
                   count <= s['filesize'] and address - s['vm'] <= s['filesize'] - count]
        if len(matches) != 1:
            raise RegistryFormatError('local pointer target does not map to one file segment')
        segment = matches[0]
        return segment['fileoff'] + address - segment['vm'], segment

    registry_offset, registry_segment = vm_file(section['address'], section['size'])
    if section['segment'] != registry_segment['name'] or registry_offset != section['offset']:
        raise RegistryFormatError('swforce section VM/file mapping differs')
    fixoff, fixsize = chained
    bounded(fixoff, fixsize)
    fixend = fixoff + fixsize
    version, starts, imports, symbols, import_count, import_format, symbol_format = unpack('<7I', fixoff, fixend)
    if version != 0 or symbol_format != 0:
        raise RegistryFormatError('unsupported chained-fixups version or symbol format')
    startoff = fixoff + starts
    seg_count, = unpack('<I', startoff, fixend)
    if seg_count != len(segments):
        raise RegistryFormatError('chained-fixups segment inventory differs')
    offsets = unpack('<' + 'I' * seg_count, startoff + 4, fixend)
    chains, formats = {}, set()
    for segment_index, relative in enumerate(offsets):
        if not relative:
            continue
        at = startoff + relative
        size, page_size, pointer_format, segment_offset, max_pointer, page_count = unpack('<IHHQIH', at, fixend)
        if size < 22 + page_count * 2 or at + size > fixend or page_size not in (0x1000, 0x4000):
            raise RegistryFormatError('invalid chained segment/page layout')
        if pointer_format not in (2, 6):
            raise RegistryFormatError('unsupported chained pointer format: ' + str(pointer_format))
        formats.add(pointer_format)
        segment = segments[segment_index]
        if segment['vm'] - imagebase != segment_offset:
            raise RegistryFormatError('chained segment VM offset differs')
        pages = unpack('<' + 'H' * ((size - 22) // 2), at + 22, at + size)
        for page_index in range(page_count):
            start = pages[page_index]
            if start == 0xffff:
                continue
            if start & 0x8000:
                overflow, starts_here = start & 0x7fff, []
                while True:
                    if overflow < page_count or overflow >= len(pages):
                        raise RegistryFormatError('invalid multi-start page index')
                    entry = pages[overflow]
                    starts_here.append(entry & 0x7fff)
                    overflow += 1
                    if entry & 0x8000:
                        break
            else:
                starts_here = [start]
            for beginning in starts_here:
                offset_in_page = beginning
                while True:
                    if offset_in_page % 4 or offset_in_page + 8 > page_size:
                        raise RegistryFormatError('chained pointer leaves its page')
                    address = segment['vm'] + page_index * page_size + offset_in_page
                    offset, owner = vm_file(address, 8)
                    if owner is not segment or address in chains:
                        raise RegistryFormatError('duplicate or cross-segment chained pointer')
                    raw, = unpack('<Q', offset)
                    bind = raw >> 63
                    if not bind and (raw >> 44) & 0x7f:
                        raise RegistryFormatError('nonzero reserved rebase pointer bits')
                    target = (raw & ((1 << 36) - 1)) | (((raw >> 36) & 0xff) << 56)
                    if pointer_format == 6:
                        target += imagebase
                    chains[address] = dict(bind=bind, target=target, format=pointer_format)
                    next_units = (raw >> 51) & 0xfff
                    if not next_units:
                        break
                    offset_in_page += next_units * 4

    def local_pointer(address):
        pointer = chains.get(address)
        if pointer is None or pointer['bind']:
            raise RegistryFormatError('registry field is not a local chained rebase')
        return pointer['target']

    def cstring(address):
        offset, segment = vm_file(address, 1)
        end = min(segment['fileoff'] + segment['filesize'], offset + 4096)
        stop = data.find(b'\0', offset, end)
        if stop < 0:
            raise RegistryFormatError('unterminated registry string')
        return data[offset:stop].decode('utf-8')

    entries = []
    for index in range(section['size'] // 32):
        address = section['address'] + index * 32
        name = cstring(local_pointer(address))
        function = local_pointer(address + 8)
        why = cstring(local_pointer(address + 16))
        _, owner = vm_file(function, 4)
        if not name or not why or function % 4 or not owner['initprot'] & 4:
            raise RegistryFormatError('invalid registry name/reason/function target')
        offset, _ = vm_file(address + 24, 8)
        titles, padding = unpack('<II', offset)
        if not titles or padding:
            raise RegistryFormatError('invalid title mask or registry padding')
        entries.append(dict(name=name, why=why, titles=titles))
    return dict(schema=1, binary_path=str(path), binary_sha256=hashlib.sha256(data).hexdigest(),
                filetype=filetype, cpu='arm64', chained_pointer_formats=sorted(formats),
                registry_segment=section['segment'], count=len(entries),
                canonical_metadata_sha256=metadata_sha256(entries), entries=entries,
                scope='static file metadata only; no executable load/run or VM/register reads')


def audit_registry(path, expected_metadata_sha256=None):
    if expected_metadata_sha256 is not None:
        if (not isinstance(expected_metadata_sha256, str) or len(expected_metadata_sha256) != 64 or
                any(c not in '0123456789abcdef' for c in expected_metadata_sha256)):
            raise RegistryFormatError('expected metadata SHA256 must be 64 lowercase hex digits')
    result = read_registry(path)
    if (expected_metadata_sha256 is not None and
            result['canonical_metadata_sha256'] != expected_metadata_sha256):
        raise RegistryFormatError('registry differs from the expected metadata SHA256')
    result['expected_registry_verified'] = expected_metadata_sha256 is not None
    return result


def write_result(source, destination, result):
    # Preserve the inspected input and any existing output on validation errors.
    # Output is a separate JSON artifact, published only after a complete parse.
    if destination.is_symlink() or destination.resolve() == source.resolve():
        raise RegistryFormatError('output must be a separate non-symlink JSON path')
    if destination.exists() and os.path.samefile(source, destination):
        raise RegistryFormatError('output must not share the input inode')
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    descriptor, temporary = tempfile.mkstemp(prefix='.' + destination.name + '.',
                                             dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expect-metadata-sha256')
    args = parser.parse_args()
    try:
        result = audit_registry(args.input, args.expect_metadata_sha256)
        write_result(args.input, args.output, result)
    except (OSError, UnicodeError, struct.error, RegistryFormatError) as error:
        parser.exit(1, 'swforce static audit failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
