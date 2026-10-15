"""Validate NSO0 modules and inspect dynamic symbols without executing code."""
import hashlib
import struct


def initialized_image(data, name):
    """Return verified initialized segments at their original virtual offsets.

    BSS is deliberately omitted: the offline instruction transforms may read
    original code/data, while runtime BSS is represented by the rewrite layout.
    """
    metadata = inspect_nso(data, name)
    segments = metadata['segments']
    image = bytearray(max(s['address'] + s['bytes'] for s in segments))
    flags = struct.unpack_from('<I', data, 0xc)[0]
    for index, segment in enumerate(segments):
        offset = struct.unpack_from('<I', data, 0x10 + index * 16)[0]
        raw = data[offset:offset + segment['stored_bytes']]
        if flags & (1 << index):
            import lz4.block
            raw = lz4.block.decompress(raw, uncompressed_size=segment['bytes'])
        address = segment['address']
        image[address:address + len(raw)] = raw
    return bytes(image)


def inspect_nso(data, name):
    if len(data) < 0x100 or data[:4] != b'NSO0':
        raise ValueError('Invalid or truncated NSO: ' + name)
    flags = struct.unpack_from('<I', data, 0xc)[0]
    segments, ranges, chunks = [], [], []
    for i, kind in enumerate(('text', 'rodata', 'data')):
        off, addr, size = struct.unpack_from('<III', data, 0x10 + i * 16)
        compressed_size = struct.unpack_from('<I', data, 0x60 + i * 4)[0]
        stored = compressed_size if flags & (1 << i) else size
        if off < 0x100 or off + stored > len(data) or addr + size > 512 * 1024**2:
            raise ValueError('NSO segment exceeds bounds: ' + kind)
        raw = data[off:off + stored]
        if flags & (1 << i):
            import lz4.block
            try:
                raw = lz4.block.decompress(raw, uncompressed_size=size)
            except Exception as exc:
                raise ValueError('Invalid compressed NSO segment: ' + kind) from exc
        if len(raw) != size:
            raise ValueError('NSO segment length mismatch: ' + kind)
        if flags & (1 << (i + 3)) and hashlib.sha256(raw).digest() != data[0xa0 + i * 32:0xc0 + i * 32]:
            raise ValueError('NSO segment SHA256 mismatch: ' + kind)
        segments.append({'name': kind, 'address': addr, 'bytes': size, 'stored_bytes': stored,
                         'hash_verified': bool(flags & (1 << (i + 3)))})
        ranges.append((addr, addr + size))
        chunks.append(raw)
    ordered = sorted(ranges)
    if ranges[0][0] != 0 or any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('NSO memory segments overlap or text is not at zero')
    bss = struct.unpack_from('<I', data, 0x3c)[0]
    memory_size = ranges[2][1] + bss
    if memory_size > 512 * 1024**2 or memory_size < max(end for _, end in ranges):
        raise ValueError('Invalid NSO BSS bounds')
    image = bytearray(max(end for _, end in ranges))
    for (start, end), raw in zip(ranges, chunks):
        image[start:end] = raw

    def read(address, size):
        if size < 0 or not any(start <= address and address + size <= end for start, end in ranges):
            raise ValueError('NSO metadata exceeds initialized segment bounds')
        return image[address:address + size]

    mod = struct.unpack('<I', read(4, 4))[0]
    if read(mod, 4) != b'MOD0':
        raise ValueError('NSO MOD0 header missing')
    dynamic = mod + struct.unpack('<i', read(mod + 4, 4))[0]
    tags = {}
    for i in range(65536):
        tag, value = struct.unpack('<QQ', read(dynamic + i * 16, 16))
        if tag == 0:
            break
        tags[tag] = value
    else:
        raise ValueError('NSO dynamic table is unterminated')
    if not all(key in tags for key in (5, 6, 10, 11)) or tags[11] != 24:
        raise ValueError('NSO lacks a supported dynamic symbol table')
    strings = bytes(read(tags[5], tags[10]))
    if 4 in tags:
        _, count = struct.unpack('<II', read(tags[4], 8))
    elif tags[5] >= tags[6] and (tags[5] - tags[6]) % 24 == 0:
        count = (tags[5] - tags[6]) // 24
    else:
        raise ValueError('Cannot determine bounded NSO symbol count')
    if count > 1000000:
        raise ValueError('NSO symbol count exceeds limit')
    symbols = read(tags[6], count * 24)
    imports, exports = set(), set()
    for i in range(count):
        offset, info, _, section, _, _ = struct.unpack_from('<IBBHQQ', symbols, i * 24)
        if offset >= len(strings) or strings.find(b'\0', offset) < 0:
            raise ValueError('Invalid NSO symbol name bounds')
        try:
            symbol = strings[offset:strings.index(0, offset)].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid NSO symbol name encoding') from exc
        if symbol and info >> 4 in (1, 2):
            (exports if section else imports).add(symbol)
    return {'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'build_id': data[0x40:0x60].hex(), 'segments': segments, 'memory_bytes': memory_size,
            'imports': sorted(imports), 'exports': sorted(exports), 'symbol_count': count}
