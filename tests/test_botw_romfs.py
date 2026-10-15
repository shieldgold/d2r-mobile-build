"""Hand-built RomFS/NCZ fixtures; no game content or keys."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
try:
    from botw_native.romfs import RomFSImage, extract_romfs
except ModuleNotFoundError:
    RomFSImage = extract_romfs = None

EMPTY = 0xffffffff


def fixture(files=None):
    files = files or [('/Bootup.pack', b'boot data'), ('/System/Actor.byml', b'actor data')]
    directories = {'/'}
    for path, _ in files:
        parent = str(Path(path).parent)
        while parent != '/':
            directories.add(parent)
            parent = str(Path(parent).parent)
    dirs = sorted(directories, key=lambda p: (p.count('/'), p))
    dirpos, dtable = {}, bytearray()
    for path in dirs:
        dirpos[path] = len(dtable)
        name = b'' if path == '/' else Path(path).name.encode()
        dtable += bytes((24 + len(name) + 3) & ~3)
    filepos, ftable, data = {}, bytearray(), bytearray()
    for path, content in files:
        filepos[path] = len(ftable)
        name = Path(path).name.encode()
        ftable += struct.pack('<IIQQII', dirpos[str(Path(path).parent)], EMPTY, len(data), len(content), EMPTY, len(name)) + name
        ftable += bytes(-len(ftable) % 4)
        data += content
    for i, (path, _) in enumerate(files):
        siblings = [p for p, _ in files if Path(p).parent == Path(path).parent]
        j = siblings.index(path)
        struct.pack_into('<I', ftable, filepos[path] + 4, filepos[siblings[j + 1]] if j + 1 < len(siblings) else EMPTY)
        struct.pack_into('<I', ftable, filepos[path] + 24, filepos[files[i + 1][0]] if i + 1 < len(files) else EMPTY)
    for i, path in enumerate(dirs):
        children = [p for p in dirs if p != '/' and str(Path(p).parent) == path]
        siblings = [p for p in dirs if p != '/' and Path(p).parent == Path(path).parent]
        j = siblings.index(path) if path != '/' else -1
        local_files = [p for p, _ in files if str(Path(p).parent) == path]
        name = b'' if path == '/' else Path(path).name.encode()
        struct.pack_into('<IIIIII', dtable, dirpos[path], 0 if path == '/' else dirpos[str(Path(path).parent)], dirpos[siblings[j + 1]] if j >= 0 and j + 1 < len(siblings) else EMPTY, dirpos[children[0]] if children else EMPTY, filepos[local_files[0]] if local_files else EMPTY, dirpos[dirs[i + 1]] if i + 1 < len(dirs) else EMPTY, len(name))
        dtable[dirpos[path] + 24:dirpos[path] + 24 + len(name)] = name
    image = bytearray(0x200) + data
    image += bytes(-len(image) % 4)
    dh = len(image); image += struct.pack('<I', 0)
    dm = len(image); image += dtable
    fh = len(image); image += struct.pack('<I', 0 if files else EMPTY)
    fm = len(image); image += ftable
    struct.pack_into('<10Q', image, 0, 80, dh, 4, dm, len(dtable), fh, 4, fm, len(ftable), 0x200)
    return image, {'dh': dh, 'dm': dm, 'fh': fh, 'fm': fm, 'dirpos': dirpos, 'filepos': filepos}


def pfs0(files):
    names, entries, data = bytearray(), bytearray(), bytearray()
    for name, content in files:
        entries += struct.pack('<QQII', len(data), len(content), len(names), 0)
        names += name.encode() + b'\0'; data += content
    return struct.pack('<4sIII', b'PFS0', len(files), len(names), 0) + entries + names + data


def package(contents, declared_extra=0, checksum=False, truncate=0):
    import zstandard
    sections, offset = bytearray(), 0x4000
    for i, content in enumerate(contents):
        length = len(content) + (declared_extra if i + 1 == len(contents) else 0)
        sections += struct.pack('<QQQQ16s16s', offset, length, 3, 0, bytes(16), bytes(16)); offset += length
    ncz = bytes(0x4000) + b'NCZSECTN' + struct.pack('<Q', len(contents)) + sections
    frame = zstandard.ZstdCompressor(write_checksum=checksum).compress(b''.join(contents))
    ncz += frame[:-truncate] if truncate else frame
    return pfs0([('program.ncz', ncz)])


class RomFSParserTests(unittest.TestCase):
    def need(self):
        self.assertIsNotNone(RomFSImage, 'RomFS implementation is missing')

    def mount(self, directory, data):
        self.need(); path = Path(directory) / 'romfs.bin'; path.write_bytes(data)
        return RomFSImage(path)

    def test_indexes_nested_files_and_reads_bounded_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, fixture()[0])
            self.assertEqual(mount.lookup('/System/Actor.byml').size, 10)
            self.assertEqual(mount.read_file('/System/Actor.byml', 2, 4), b'tor ')
            self.assertEqual(mount.read_file('/Bootup.pack', 9, 4), b'')
            self.assertEqual(mount.iterdir('/'), ['Bootup.pack', 'System'])
            self.assertEqual(mount.iterdir('/System'), ['Actor.byml'])
            for path in ['/../escape', '/System//Actor.byml', '/System/./Actor.byml', '/System\\Actor.byml', 'Bootup.pack']:
                with self.subTest(path=path), self.assertRaises(ValueError): mount.lookup(path)
            for start, size in [(-1, 3), (0, -1), (0, 17 * 1024**2)]:
                with self.assertRaises(ValueError): mount.read_file('/Bootup.pack', start, size)
            with self.assertRaises(FileNotFoundError): mount.lookup('/unknown')

    def test_rejects_unsafe_names_duplicate_paths_and_child_cycles(self):
        for mode in ['unsafe', 'duplicate', 'cycle', 'wrong_parent', 'orphan']:
            data, loc = fixture([('/abc', b'a'), ('/def', b'b'), ('/System/x', b'x')])
            if mode == 'unsafe': data[loc['fm'] + 32:loc['fm'] + 35] = b'../'
            elif mode == 'duplicate':
                pos = loc['fm'] + loc['filepos']['/def']; data[pos + 32:pos + 35] = b'abc'
            elif mode == 'cycle': struct.pack_into('<I', data, loc['dm'] + loc['dirpos']['/System'] + 8, loc['dirpos']['/System'])
            elif mode == 'wrong_parent': struct.pack_into('<I', data, loc['fm'], loc['dirpos']['/System'])
            else: struct.pack_into('<I', data, loc['dm'] + 8, EMPTY)
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError): self.mount(tmp, data)

    def test_rejects_bad_headers_ranges_pointers_hash_cycles_and_truncation(self):
        original, loc = fixture()
        cases = []
        for position, fmt, value in [(0, '<Q', 81), (32, '<Q', 80 * 1024**2), (24, '<Q', loc['fm']), (loc['dm'] + 8, '<I', 1), (loc['fm'] + 8, '<Q', len(original)), (loc['fm'] + 24, '<I', 0), (loc['dh'], '<I', 1)]:
            bad = bytearray(original); struct.pack_into(fmt, bad, position, value); cases.append(bad)
        cases += [original[:79], original[:-1]]
        for i, bad in enumerate(cases):
            with self.subTest(case=i), tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError): self.mount(tmp, bad)

    def test_rejects_payloads_overlapping_metadata_or_other_files(self):
        original, loc = fixture()
        for offset in [loc['dh'] - 0x200, 0]:
            bad = bytearray(original)
            struct.pack_into('<Q', bad, loc['fm'] + loc['filepos']['/System/Actor.byml'] + 8, offset)
            with self.subTest(offset=offset), tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError): self.mount(tmp, bad)

    def test_manifest_is_reproducible_and_export_is_atomic(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, data)
            first = mount.manifest()
            self.assertEqual(first, mount.manifest())
            self.assertEqual(first['image']['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(first['file_count'], 2)
            self.assertEqual(first['payload_bytes'], 19)
            self.assertEqual(first['files'][0]['sha256'], hashlib.sha256(b'boot data').hexdigest())
            destination = Path(tmp) / 'export'
            mount.export(destination)
            self.assertEqual((destination / 'System/Actor.byml').read_bytes(), b'actor data')
            with self.assertRaises(FileExistsError): mount.export(destination)
            with self.assertRaises(ValueError): mount.export(Path(tmp) / 'bad', ['/../escape'])
            self.assertFalse((Path(tmp) / 'bad').exists())

    def test_export_rejects_directory_aliases_on_casefolding_hosts(self):
        data, _ = fixture([('/Case/one', b'a'), ('/case/two', b'b')])
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, data)
            with self.assertRaises(ValueError): mount.export(Path(tmp) / 'export')
            self.assertFalse((Path(tmp) / 'export').exists())

    def test_detects_source_truncation_after_mount(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, data)
            mount.path.write_bytes(data[:513])
            with self.assertRaises(ValueError): mount.read_file('/System/Actor.byml', 0, 10)


@unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Install requirements.botw.txt')
class RomFSExtractionTests(unittest.TestCase):
    def test_extracts_romfs_after_hash_prefix_from_contiguous_solid_sections(self):
        self.assertIsNotNone(extract_romfs, 'RomFS extraction implementation is missing')
        image, _ = fixture()
        original = package([b'plain section', bytes(0x4000) + image, b'PFS0tail'])
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / 'base.nsz', Path(tmp) / 'output'
            source.write_bytes(original)
            report = extract_romfs(source, out)
            self.assertEqual((out / 'romfs.bin').read_bytes(), image)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(report, json.loads((out / 'manifest.json').read_text()))
            self.assertEqual(report['source']['romfs_section_offset'], 0x4000)
            self.assertEqual(report['file_count'], 2)
            with self.assertRaises(FileExistsError): extract_romfs(source, out)

    def test_rejects_missing_checksum_even_when_decoded_payload_is_complete(self):
        self.assertIsNotNone(extract_romfs)
        image, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / 'base.nsz', Path(tmp) / 'output'
            source.write_bytes(package([image], checksum=True, truncate=1))
            with self.assertRaises(ValueError): extract_romfs(source, out)
            self.assertFalse(out.exists())

    def test_does_not_publish_truncated_or_ambiguous_resources(self):
        self.assertIsNotNone(extract_romfs, 'RomFS extraction implementation is missing')
        image, _ = fixture()
        for content in [package([image], 100), package([image, image]), package([b'no RomFS'])]:
            with tempfile.TemporaryDirectory() as tmp:
                source, out = Path(tmp) / 'base.nsz', Path(tmp) / 'output'
                source.write_bytes(content)
                with self.assertRaises(ValueError): extract_romfs(source, out)
                self.assertFalse(out.exists())


if __name__ == '__main__': unittest.main()
