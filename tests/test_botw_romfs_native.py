"""Compile the iOS-compatible C RomFS backend against hand-built fixtures."""
import ctypes
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_botw_romfs import fixture, EMPTY

ROOT = Path(__file__).parents[1]


class Error(ctypes.Structure):
    _fields_ = [('code', ctypes.c_int), ('message', ctypes.c_char * 192)]


class Entry(ctypes.Structure):
    _fields_ = [('offset', ctypes.c_uint64), ('size', ctypes.c_uint64), ('is_directory', ctypes.c_int)]


CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(Entry))


@unittest.skipUnless(shutil.which('clang'), 'Requires Clang for the portable native backend')
class NativeRomFSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.lib = None
        if not (ROOT / 'native/botw_resources/romfs.c').exists(): return
        binary = Path(cls.temp.name) / 'romfs.dylib'
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-Wshadow', '-pedantic', '-shared', '-fPIC', '-O2', str(ROOT / 'native/botw_resources/romfs.c'), '-o', str(binary)], check=True, capture_output=True, text=True)
        cls.lib = ctypes.CDLL(str(binary))
        cls.lib.botw_romfs_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(Error)]
        cls.lib.botw_romfs_open.restype = ctypes.c_void_p
        cls.lib.botw_romfs_close.argtypes = [ctypes.c_void_p]
        cls.lib.botw_romfs_stat.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(Entry), ctypes.POINTER(Error)]
        cls.lib.botw_romfs_pread.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(Error)]
        cls.lib.botw_romfs_iterate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, CALLBACK, ctypes.c_void_p, ctypes.POINTER(Error)]
        if hasattr(cls.lib, 'botw_romfs_image_size'):
            cls.lib.botw_romfs_image_size.argtypes = [ctypes.c_void_p]
            cls.lib.botw_romfs_image_size.restype = ctypes.c_uint64
        if hasattr(cls.lib, 'botw_romfs_raw_pread'):
            cls.lib.botw_romfs_raw_pread.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(Error)]

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def mount(self, tmp, data):
        self.assertIsNotNone(self.lib, 'Native RomFS backend is missing')
        path = Path(tmp) / 'romfs.bin'; path.write_bytes(data)
        error = Error()
        mount = self.lib.botw_romfs_open(str(path).encode(), ctypes.byref(error))
        self.assertTrue(mount, error.message.decode())
        self.addCleanup(self.lib.botw_romfs_close, mount)
        return mount

    def read(self, mount, path, offset, size):
        buffer, actual, error = ctypes.create_string_buffer(max(1, size)), ctypes.c_size_t(999), Error()
        result = self.lib.botw_romfs_pread(mount, path, offset, buffer, size, ctypes.byref(actual), ctypes.byref(error))
        return result, buffer.raw[:actual.value] if actual.value <= size else b'', actual.value, error

    def test_indexes_reads_and_iterates_immediate_children(self):
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, data)
            entry, error = Entry(), Error()
            self.assertEqual(self.lib.botw_romfs_stat(mount, b'/System', ctypes.byref(entry), ctypes.byref(error)), 0)
            self.assertTrue(entry.is_directory)
            self.assertEqual(self.lib.botw_romfs_stat(mount, b'/System/Actor.byml', ctypes.byref(entry), ctypes.byref(error)), 0)
            self.assertEqual((entry.offset, entry.size, entry.is_directory), (521, 10, 0))
            result, content, actual, _ = self.read(mount, b'/System/Actor.byml', 2, 4)
            self.assertEqual((result, content, actual), (0, b'tor ', 4))
            self.assertEqual(self.read(mount, b'/System/Actor.byml', 8, 10)[:3], (0, b'ta', 2))
            self.assertEqual(self.read(mount, b'/System/Actor.byml', 2**64 - 1, 2)[:3], (0, b'', 0))
            names = []
            @CALLBACK
            def collect(user, name, found):
                names.append((name.decode(), bool(found.contents.is_directory))); return 0
            self.assertEqual(self.lib.botw_romfs_iterate(mount, b'/', collect, None, ctypes.byref(error)), 0)
            self.assertEqual(names, [('Bootup.pack', False), ('System', True)])
            names.clear()
            self.assertEqual(self.lib.botw_romfs_iterate(mount, b'/System', collect, None, ctypes.byref(error)), 0)
            self.assertEqual(names, [('Actor.byml', False)])

    def test_rejects_unsafe_guest_paths_and_invalid_read_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, fixture()[0])
            error, entry = Error(), Entry()
            for path in [b'/../escape', b'/System//Actor.byml', b'file', b'/System\\file', b'/x/./y', b'/bad\xff', b'/' + b'a'*769]:
                with self.subTest(path=path):
                    self.assertNotEqual(self.lib.botw_romfs_stat(mount, path, ctypes.byref(entry), ctypes.byref(error)), 0)
            self.assertNotEqual(self.lib.botw_romfs_stat(mount, b'/missing', ctypes.byref(entry), ctypes.byref(error)), 0)
            self.assertEqual(self.read(mount, b'/System', 0, 1)[2], 0)
            actual = ctypes.c_size_t(99)
            self.assertNotEqual(self.lib.botw_romfs_pread(mount, b'/Bootup.pack', 0, None, 1, ctypes.byref(actual), ctypes.byref(error)), 0)
            self.assertEqual(actual.value, 0)
            self.assertNotEqual(self.lib.botw_romfs_pread(mount, b'/Bootup.pack', 0, None, 17*1024**2, ctypes.byref(actual), ctypes.byref(error)), 0)

    def test_rejects_corrupt_tables_cycles_names_ranges_and_overflow(self):
        self.assertIsNotNone(self.lib, 'Native RomFS backend is missing')
        original, loc = fixture()
        cases = [original[:79], original[:-1]]
        for position, fmt, value in [(0, '<Q', 81), (32, '<Q', 80*1024**2), (24, '<Q', loc['fm']), (loc['dm'] + 8, '<I', 1), (loc['dm'] + loc['dirpos']['/System'] + 8, '<I', loc['dirpos']['/System']), (loc['fm'], '<I', loc['dirpos']['/System']), (loc['fm'] + 8, '<Q', 2**64-1), (loc['fm'] + 16, '<Q', 2**64-1), (loc['fm'] + 24, '<I', 0), (loc['dh'], '<I', 1), (loc['dm'] + 8, '<I', EMPTY)]:
            bad = bytearray(original); struct.pack_into(fmt, bad, position, value); cases.append(bad)
        for offset in [loc['dh'] - 0x200, 0]:
            bad = bytearray(original)
            struct.pack_into('<Q', bad, loc['fm'] + loc['filepos']['/System/Actor.byml'] + 8, offset)
            cases.append(bad)
        for content in [b'../', b'a\0x', b'\xf5\x80\x80']:
            bad, off = fixture([('/abc', b'a')]); bad[off['fm']+32:off['fm']+35] = content; cases.append(bad)
        duplicate, off = fixture([('/abc', b'a'), ('/def', b'b')])
        duplicate[off['fm'] + off['filepos']['/def'] + 32:off['fm'] + off['filepos']['/def'] + 35] = b'abc'; cases.append(duplicate)
        for i, data in enumerate(cases):
            with self.subTest(case=i), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'bad.bin'; path.write_bytes(data); error = Error()
                mount = self.lib.botw_romfs_open(str(path).encode(), ctypes.byref(error))
                if mount: self.lib.botw_romfs_close(mount)
                self.assertFalse(mount, 'Corrupt image unexpectedly mounted')
                self.assertNotEqual(error.code, 0)

    def test_raw_image_size_and_reads_clamp_at_eof_without_overflow(self):
        self.assertIsNotNone(self.lib)
        self.assertTrue(hasattr(self.lib, 'botw_romfs_image_size'), 'Raw image size API is missing')
        self.assertTrue(hasattr(self.lib, 'botw_romfs_raw_pread'), 'Raw image pread API is missing')
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, data)
            self.assertEqual(self.lib.botw_romfs_image_size(mount), len(data))
            self.assertEqual(self.lib.botw_romfs_image_size(None), 0)
            buffer, actual, error = ctypes.create_string_buffer(80), ctypes.c_size_t(999), Error()
            for offset, size, expected in [(0, 80, data[:80]), (len(data)-2, 8, data[-2:]), (len(data), 8, b''), (2**64-1, 8, b'')]:
                with self.subTest(offset=offset):
                    self.assertEqual(self.lib.botw_romfs_raw_pread(mount, offset, buffer, size, ctypes.byref(actual), ctypes.byref(error)), 0)
                    self.assertEqual(actual.value, len(expected))
                    self.assertEqual(buffer.raw[:actual.value], expected)
                    self.assertEqual(error.code, 0)
            self.assertEqual(self.lib.botw_romfs_raw_pread(mount, 0, None, 0, ctypes.byref(actual), None), 0)
            self.assertEqual(actual.value, 0)
            full_buffer = ctypes.create_string_buffer(len(data))
            self.assertEqual(self.lib.botw_romfs_raw_pread(mount, 0, full_buffer, 16*1024**2, ctypes.byref(actual), None), 0)
            self.assertEqual((actual.value, full_buffer.raw), (len(data), data))

    def test_raw_image_reads_reject_invalid_requests_and_real_host_short_reads(self):
        self.assertIsNotNone(self.lib)
        self.assertTrue(hasattr(self.lib, 'botw_romfs_raw_pread'), 'Raw image pread API is missing')
        data, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, data)
            buffer, actual, error = ctypes.create_string_buffer(16), ctypes.c_size_t(999), Error()
            for context, target, size in [(None, buffer, 1), (mount, None, 1), (mount, buffer, 16*1024**2+1), (mount, buffer, ctypes.c_size_t(-1).value)]:
                with self.subTest(size=size):
                    self.assertEqual(self.lib.botw_romfs_raw_pread(context, 0, target, size, ctypes.byref(actual), ctypes.byref(error)), 1)
                    self.assertEqual(actual.value, 0)
                    self.assertEqual(error.code, 1)
            self.assertEqual(self.lib.botw_romfs_raw_pread(mount, 0, buffer, 1, None, None), 1)
            (Path(tmp) / 'romfs.bin').write_bytes(data[:513])
            self.assertEqual(self.lib.botw_romfs_raw_pread(mount, 512, buffer, 10, ctypes.byref(actual), ctypes.byref(error)), 2)
            self.assertEqual(actual.value, 0)
            self.assertEqual(error.code, 2)

    def test_unicode_names_and_zero_length_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = fixture([('/资源/空', b''), ('/资源/数据', b'content')])
            mount = self.mount(tmp, data)
            self.assertEqual(self.read(mount, '/资源/空'.encode(), 0, 16)[:3], (0, b'', 0))
            self.assertEqual(self.read(mount, '/资源/数据'.encode(), 0, 16)[:3], (0, b'content', 7))

    def test_optional_error_output_does_not_change_error_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, fixture()[0]); entry = Entry()
            self.assertEqual(self.lib.botw_romfs_stat(mount, b'/../escape', ctypes.byref(entry), None), 1)
            actual = ctypes.c_size_t()
            self.assertEqual(self.lib.botw_romfs_pread(mount, b'/../escape', 0, None, 0, ctypes.byref(actual), None), 1)

    def test_directory_errors_callback_stop_and_zero_length_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = self.mount(tmp, fixture()[0]); error = Error()
            names = []
            @CALLBACK
            def stop(user, name, entry): names.append(name); return 1
            self.assertEqual(self.lib.botw_romfs_iterate(mount, b'/', stop, None, ctypes.byref(error)), 0)
            self.assertEqual(names, [b'Bootup.pack'])
            self.assertNotEqual(self.lib.botw_romfs_iterate(mount, b'/Bootup.pack', stop, None, ctypes.byref(error)), 0)
            self.assertNotEqual(self.lib.botw_romfs_iterate(mount, b'/missing', stop, None, ctypes.byref(error)), 0)
            actual = ctypes.c_size_t(99)
            self.assertEqual(self.lib.botw_romfs_pread(mount, b'/Bootup.pack', 0, None, 0, ctypes.byref(actual), ctypes.byref(error)), 0)
            self.assertEqual(actual.value, 0)
            self.assertEqual(error.code, 0)

    def test_sanitizers_accept_deterministically_mutated_metadata_without_crashes(self):
        self.assertIsNotNone(self.lib, 'Native RomFS backend is missing')
        source = r'''#include "romfs.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int visit(void *user, const char *name, const botw_romfs_entry *entry) {
    (void)user; (void)name; (void)entry; return 0;
}
int main(int argc, char **argv) {
    unsigned char original[4096], mutated[4096], bytes[64];
    FILE *input; size_t size; uint32_t seed = 0x9e3779b9u;
    if (argc != 3 || !(input = fopen(argv[1], "rb"))) return 2;
    size = fread(original, 1, sizeof(original), input); fclose(input);
    if (!size || size >= sizeof(original)) return 3;
    for (size_t i = 0; i < 2048; ++i) {
        FILE *output; botw_romfs *fs; botw_romfs_error error;
        botw_romfs_entry entry; size_t actual = 0;
        memcpy(mutated, original, size);
        for (size_t j = 0; j < 1 + i % 4; ++j) {
            seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
            mutated[seed % size] ^= (unsigned char)(1u << (seed % 8));
        }
        output = fopen(argv[2], "wb"); if (!output) return 4;
        if (fwrite(mutated, 1, size, output) != size) return 5;
        fclose(output); fs = botw_romfs_open(argv[2], &error);
        if (fs) {
            (void)botw_romfs_stat(fs, "/System/Actor.byml", &entry, &error);
            (void)botw_romfs_pread(fs, "/System/Actor.byml", 0, bytes, sizeof(bytes), &actual, &error);
            (void)botw_romfs_iterate(fs, "/", visit, NULL, &error);
            (void)botw_romfs_raw_pread(fs, UINT64_MAX, bytes, sizeof(bytes), &actual, &error);
            (void)botw_romfs_raw_pread(fs, 0, bytes, sizeof(bytes), &actual, &error);
            botw_romfs_close(fs);
        }
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); c = root / 'mutate.c'; c.write_text(source)
            base = root / 'base.bin'; base.write_bytes(fixture()[0])
            binary = root / 'sanitize'
            subprocess.run(['clang', '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g', '-I', str(ROOT / 'native/botw_resources'), str(c), str(ROOT / 'native/botw_resources/romfs.c'), '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary), str(base), str(root / 'mutated.bin')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')

    def test_read_fails_if_image_is_truncated_after_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = fixture(); mount = self.mount(tmp, data)
            (Path(tmp) / 'romfs.bin').write_bytes(data[:513])
            result, _, actual, error = self.read(mount, b'/System/Actor.byml', 0, 10)
            self.assertNotEqual(result, 0); self.assertEqual(actual, 0); self.assertNotEqual(error.code, 0)

    def test_native_backend_matches_prepared_local_base_resources(self):
        image = ROOT / 'outputs/botw-romfs-base/romfs.bin'
        manifest = image.parent / 'manifest.json'
        if not image.exists() or not manifest.exists(): self.skipTest('Local game resources are not committed')
        self.assertIsNotNone(self.lib, 'Native RomFS backend is missing')
        error = Error(); mount = self.lib.botw_romfs_open(str(image).encode(), ctypes.byref(error))
        self.assertTrue(mount, error.message.decode()); self.addCleanup(self.lib.botw_romfs_close, mount)
        expected = {e['path']:e for e in json.loads(manifest.read_text())['files']}
        self.assertEqual(self.lib.botw_romfs_image_size(mount), 14420017152)
        with image.open('rb') as stream:
            for path in ['/System/Version.txt', '/Pack/Bootup.pack']:
                entry = Entry()
                self.assertEqual(self.lib.botw_romfs_stat(mount, path.encode(), ctypes.byref(entry), ctypes.byref(error)), 0)
                self.assertEqual((entry.offset, entry.size), (expected[path]['offset'], expected[path]['size']))
                stream.seek(entry.offset); content = stream.read(min(64, entry.size))
                self.assertEqual(self.read(mount, path.encode(), 0, 64)[:3], (0, content, len(content)))
                buffer, actual = ctypes.create_string_buffer(64), ctypes.c_size_t()
                self.assertEqual(self.lib.botw_romfs_raw_pread(mount, entry.offset, buffer, 64, ctypes.byref(actual), ctypes.byref(error)), 0)
                stream.seek(entry.offset)
                self.assertEqual(buffer.raw[:actual.value], stream.read(64))


if __name__ == '__main__': unittest.main()
