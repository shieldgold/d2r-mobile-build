"""Real filesystem fixtures for the App-container-only save backend."""
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).parents[1]
TITLE = b'01007ef00011e000'
USER = b'00112233445566778899aabbccddeeff'


class Error(ctypes.Structure):
    _fields_ = [('code', ctypes.c_int), ('message', ctypes.c_char * 192)]


class Entry(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint64), ('is_directory', ctypes.c_int)]


CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(Entry))


@unittest.skipUnless(shutil.which('clang'), 'Requires Clang for the native save backend')
class SaveFSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.lib = None
        cls.binary = Path(cls.temp.name) / 'savefs.dylib'
        if not (ROOT / 'native/botw_resources/savefs.c').exists(): return
        subprocess.run(['clang', '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-Wshadow', '-pedantic', '-shared', '-fPIC', '-O2', '-pthread', str(ROOT / 'native/botw_resources/savefs.c'), '-o', str(cls.binary)], check=True, capture_output=True, text=True)
        cls.lib = ctypes.CDLL(str(cls.binary))
        cls.lib.botw_savefs_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(Error)]
        cls.lib.botw_savefs_open.restype = ctypes.c_void_p
        cls.lib.botw_savefs_close.argtypes = [ctypes.c_void_p]
        for name in ('title_id', 'user_id'):
            if hasattr(cls.lib, 'botw_savefs_' + name):
                getattr(cls.lib, 'botw_savefs_' + name).argtypes = [ctypes.c_void_p]
                getattr(cls.lib, 'botw_savefs_' + name).restype = ctypes.c_char_p
        cls.lib.botw_savefs_usage.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(Error)]
        cls.lib.botw_savefs_stat.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(Entry), ctypes.POINTER(Error)]
        cls.lib.botw_savefs_create.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(Error)]
        cls.lib.botw_savefs_mkdir.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(Error)]
        cls.lib.botw_savefs_truncate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(Error)]
        for name in ('pread', 'pwrite'):
            getattr(cls.lib, 'botw_savefs_' + name).argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(Error)]
        cls.lib.botw_savefs_rename.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(Error)]
        cls.lib.botw_savefs_remove.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(Error)]
        cls.lib.botw_savefs_iterate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, CALLBACK, ctypes.c_void_p, ctypes.POINTER(Error)]
        cls.lib.botw_savefs_commit.argtypes = [ctypes.c_void_p, ctypes.POINTER(Error)]

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def mount(self, tmp, quota=0):
        self.assertIsNotNone(self.lib, 'Native save backend is missing')
        base = Path(tmp) / 'botw-saves'; error = Error()
        store = self.lib.botw_savefs_open(str(base).encode(), TITLE, USER, quota, ctypes.byref(error))
        self.assertTrue(store, error.message.decode())
        self.addCleanup(self.lib.botw_savefs_close, store)
        return store, base / TITLE.decode() / USER.decode()

    def write(self, store, path, content, offset=0):
        actual, error = ctypes.c_size_t(999), Error()
        buffer = ctypes.create_string_buffer(content)
        result = self.lib.botw_savefs_pwrite(store, path, offset, buffer, len(content), ctypes.byref(actual), ctypes.byref(error))
        return result, actual.value, error

    def read(self, store, path, offset=0, size=64):
        actual, error = ctypes.c_size_t(999), Error(); buffer = ctypes.create_string_buffer(max(1, size))
        result = self.lib.botw_savefs_pread(store, path, offset, buffer, size, ctypes.byref(actual), ctypes.byref(error))
        return result, buffer.raw[:actual.value], error

    def usage(self, store):
        used, quota, error = ctypes.c_uint64(), ctypes.c_uint64(), Error()
        result = self.lib.botw_savefs_usage(store, ctypes.byref(used), ctypes.byref(quota), ctypes.byref(error))
        self.assertEqual(result, 0, error.message.decode())
        return used.value, quota.value

    def test_create_read_write_truncate_commit_and_reopen_persist_real_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp, 1024); error, entry = Error(), Entry()
            self.assertTrue(path.is_dir())
            self.assertEqual(self.lib.botw_savefs_mkdir(store, '/资源'.encode(), ctypes.byref(error)), 0)
            name = '/资源/save.bin'.encode()
            self.assertEqual(self.lib.botw_savefs_create(store, name, 3, ctypes.byref(error)), 0)
            self.assertEqual(self.read(store, name)[:2], (0, bytes(3)))
            self.assertEqual(self.write(store, name, b'game', 5)[:2], (0, 4))
            self.assertEqual(self.read(store, name)[:2], (0, bytes(5) + b'game'))
            self.assertEqual(self.read(store, name, 8, 8)[:2], (0, b'e'))
            self.assertEqual(self.read(store, name, 2**64-1, 8)[:2], (0, b''))
            self.assertEqual(self.lib.botw_savefs_truncate(store, name, 6, ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_stat(store, name, ctypes.byref(entry), ctypes.byref(error)), 0)
            self.assertEqual((entry.size, entry.is_directory), (6, 0))
            self.assertEqual(self.usage(store), (6, 1024))
            self.assertEqual(self.lib.botw_savefs_commit(store, ctypes.byref(error)), 0)
            self.assertEqual((path / '资源/save.bin').read_bytes(), bytes(5) + b'g')
            # Reopen only after the first instance releases its exclusive lock.
            self.lib.botw_savefs_close(store); self._cleanups.pop()
            reopened, _ = self.mount(tmp, 1024)
            self.assertEqual(self.read(reopened, name)[:2], (0, bytes(5) + b'g'))

    def test_identity_api_returns_validated_open_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self.mount(tmp)
            self.assertTrue(hasattr(self.lib, 'botw_savefs_title_id'), 'Save title identity API is missing')
            self.assertTrue(hasattr(self.lib, 'botw_savefs_user_id'), 'Save user identity API is missing')
            self.assertEqual(self.lib.botw_savefs_title_id(store), TITLE)
            self.assertEqual(self.lib.botw_savefs_user_id(store), USER)
            self.assertIsNone(self.lib.botw_savefs_title_id(None))
            self.assertIsNone(self.lib.botw_savefs_user_id(None))

    def test_quota_uses_current_file_sizes_and_allows_shrink_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp, 10); error = Error()
            self.assertEqual(self.lib.botw_savefs_create(store, b'/a', 8, ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_create(store, b'/b', 3, ctypes.byref(error)), errno.EDQUOT)
            self.assertFalse((path / 'b').exists())
            self.assertEqual(self.write(store, b'/a', b'123', 8)[:2], (errno.EDQUOT, 0))
            self.assertEqual(self.write(store, b'/a', b'new')[:2], (0, 3))
            self.assertEqual(self.lib.botw_savefs_truncate(store, b'/a', 11, ctypes.byref(error)), errno.EDQUOT)
            self.assertEqual(self.lib.botw_savefs_truncate(store, b'/a', 2, ctypes.byref(error)), 0)
            (path / 'external-fixture').write_bytes(b'12345678')
            self.assertEqual(self.usage(store), (10, 10))
            self.assertEqual(self.lib.botw_savefs_create(store, b'/b', 1, ctypes.byref(error)), errno.EDQUOT)
            self.assertEqual(self.lib.botw_savefs_remove(store, b'/external-fixture', ctypes.byref(error)), 0)
            self.assertEqual(self.usage(store), (2, 10))

    def test_existing_file_create_reports_exists_before_quota_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp, 4); error = Error()
            self.assertEqual(self.lib.botw_savefs_create(store, b'/slot', 4, ctypes.byref(error)), 0)
            self.assertEqual(self.write(store, b'/slot', b'abcd')[:2], (0, 4))
            self.assertEqual(self.lib.botw_savefs_create(store, b'/slot', 4, ctypes.byref(error)), errno.EEXIST)
            self.assertEqual((path / 'slot').read_bytes(), b'abcd')

    def test_directory_snapshot_is_sorted_reentrant_and_rename_does_not_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp); error = Error()
            for name in [b'/z', b'/a']:
                self.assertEqual(self.lib.botw_savefs_create(store, name, 0, ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_mkdir(store, b'/dir', ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_create(store, b'/dir/slot', 0, ctypes.byref(error)), 0)
            self.assertEqual(self.write(store, b'/dir/slot', b'data')[:2], (0, 4))
            found = []
            @CALLBACK
            def collect(user, name, entry):
                found.append((name.decode(), bool(entry.contents.is_directory))); self.usage(store); return 0
            self.assertEqual(self.lib.botw_savefs_iterate(store, b'/', collect, None, ctypes.byref(error)), 0)
            self.assertEqual(found, [('a', False), ('dir', True), ('z', False)])
            self.assertEqual(self.lib.botw_savefs_rename(store, b'/dir/slot', b'/dir/new', ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_rename(store, b'/dir/new', b'/a', ctypes.byref(error)), errno.EEXIST)
            self.assertEqual((path / 'a').read_bytes(), b'')
            self.assertEqual((path / 'dir/new').read_bytes(), b'data')
            self.assertEqual(self.lib.botw_savefs_remove(store, b'/dir', ctypes.byref(error)), errno.ENOTEMPTY)
            self.assertEqual(self.lib.botw_savefs_remove(store, b'/dir/new', ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_remove(store, b'/dir', ctypes.byref(error)), 0)

    def test_rejects_traversal_symlinks_hardlinks_and_invalid_ids_without_external_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp); error, entry = Error(), Entry()
            outside = Path(tmp) / 'outside'; outside.mkdir(); (outside / 'private').write_bytes(b'preserve')
            (path / 'link').symlink_to(outside, target_is_directory=True)
            (path / 'alias').symlink_to(outside / 'private')
            os.link(outside / 'private', path / 'hardlink')
            for name in [b'/../outside/private', b'/a//b', b'/a/./b', b'/a\\b', b'/a:b', b'relative', b'/a/', b'/link/private', b'/alias', b'/hardlink', b'/bad\xff']:
                with self.subTest(path=name):
                    self.assertNotEqual(self.lib.botw_savefs_stat(store, name, ctypes.byref(entry), ctypes.byref(error)), 0)
                    self.assertNotEqual(self.write(store, name, b'overwrite')[0], 0)
            self.assertEqual((outside / 'private').read_bytes(), b'preserve')
            for title, user in [(b'../../escape', USER), (TITLE, b'../outside'), (b'01007EF00011E000', USER)]:
                root = Path(tmp) / 'invalid-base'
                opened = self.lib.botw_savefs_open(str(root).encode(), title, user, 0, ctypes.byref(error))
                if opened: self.lib.botw_savefs_close(opened)
                self.assertFalse(opened); self.assertFalse(root.exists())
            linked_base = Path(tmp) / 'linked-base'; linked_base.symlink_to(outside, target_is_directory=True)
            self.assertFalse(self.lib.botw_savefs_open(str(linked_base).encode(), TITLE, USER, 0, ctypes.byref(error)))
            self.assertEqual(sorted(p.name for p in outside.iterdir()), ['private'])

    def test_concurrent_quota_mutations_are_serialized_and_second_mount_is_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp, 16); errors = []
            def create(index):
                error = Error(); errors.append(self.lib.botw_savefs_create(store, f'/slot{index}'.encode(), 4, ctypes.byref(error)))
            threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(errors.count(0), 4); self.assertEqual(errors.count(errno.EDQUOT), 4)
            self.assertEqual(self.usage(store), (16, 16))
            error = Error()
            second = self.lib.botw_savefs_open(str(path.parents[1]).encode(), TITLE, USER, 16, ctypes.byref(error))
            if second: self.lib.botw_savefs_close(second)
            self.assertFalse(second); self.assertIn(error.code, (errno.EWOULDBLOCK, errno.EAGAIN))

    def test_commit_and_usage_fail_on_unsafe_injected_entries_and_host_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp); error = Error()
            self.assertEqual(self.lib.botw_savefs_create(store, b'/slot', 1, ctypes.byref(error)), 0)
            if os.geteuid() != 0:
                (path / 'slot').chmod(0o400)
                self.assertEqual(self.write(store, b'/slot', b'x')[0], errno.EACCES)
                (path / 'slot').chmod(0o600)
            (path / 'symlink').symlink_to(path / 'slot')
            used, quota = ctypes.c_uint64(), ctypes.c_uint64()
            self.assertEqual(self.lib.botw_savefs_usage(store, ctypes.byref(used), ctypes.byref(quota), ctypes.byref(error)), errno.ELOOP)
            self.assertEqual(self.lib.botw_savefs_commit(store, ctypes.byref(error)), errno.ELOOP)
            self.assertEqual(error.code, errno.ELOOP)

    def test_host_close_error_after_successful_write_is_reported(self):
        self.assertIsNotNone(self.lib)
        source = r'''#include "savefs.h"
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>
static int armed = 0;
int savefs_test_close(int fd) {
    struct stat info; int regular = !fstat(fd,&info) && S_ISREG(info.st_mode);
    int result = close(fd);
    if (!result && regular && armed) { armed = 0; errno = EIO; return -1; }
    return result;
}
int main(int argc, char **argv) {
    botw_savefs *store; botw_savefs_error error; size_t actual;
    if (argc != 2) return 2;
    store = botw_savefs_open(argv[1],"01007ef00011e000","00112233445566778899aabbccddeeff",0,&error);
    if (!store || botw_savefs_create(store,"/slot",0,&error)) return 3;
    armed = 1;
    if (botw_savefs_pwrite(store,"/slot",0,"abc",3,&actual,&error) != EIO || actual != 3 || error.code != EIO) return 4;
    botw_savefs_close(store); return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); shim = root / 'close.c'; shim.write_text(source)
            object_file, binary = root / 'savefs.o', root / 'close-test'
            subprocess.run(['clang', '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Dclose=savefs_test_close', '-pthread', '-c', str(ROOT / 'native/botw_resources/savefs.c'), '-o', str(object_file)], check=True, capture_output=True, text=True)
            subprocess.run(['clang', '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-pthread', '-I', str(ROOT / 'native/botw_resources'), str(shim), str(object_file), '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary), str(root / 'saves')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sanitizers_check_filesystem_operations_and_invalid_paths(self):
        self.assertIsNotNone(self.lib)
        source = r'''#include "savefs.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int visit(void *user, const char *name, const botw_savefs_entry *entry) {
    (void)user; (void)name; (void)entry; return 0;
}
int main(int argc, char **argv) {
    botw_savefs_error error; botw_savefs_entry entry; botw_savefs *store;
    const char *invalid[] = {NULL,"/../escape","/a//b","/a/./b","/a/","relative","/bad\xff","/"};
    unsigned char buffer[128] = {1,2,3}; size_t actual; uint64_t used, quota;
    if (argc != 2) return 2;
    store = botw_savefs_open(argv[1],"01007ef00011e000","00112233445566778899aabbccddeeff",256,&error);
    if (!store) return 3;
    for (size_t i = 0; i < 128; ++i) {
        char name[64], renamed[64];
        (void)snprintf(name,sizeof(name),"/slot%zu",i);
        (void)snprintf(renamed,sizeof(renamed),"/moved%zu",i);
        (void)botw_savefs_create(store,name,4,&error);
        (void)botw_savefs_pwrite(store,name,8,buffer,sizeof(buffer),&actual,&error);
        (void)botw_savefs_pread(store,name,0,buffer,sizeof(buffer),&actual,&error);
        (void)botw_savefs_pwrite(store,name,UINT64_MAX,buffer,sizeof(buffer),&actual,&error);
        (void)botw_savefs_truncate(store,name,2,&error);
        (void)botw_savefs_rename(store,name,renamed,&error);
        (void)botw_savefs_remove(store,renamed,&error);
        for (size_t j = 0; j < sizeof(invalid)/sizeof(invalid[0]); ++j) {
            (void)botw_savefs_stat(store,invalid[j],&entry,&error);
            (void)botw_savefs_create(store,invalid[j],0,&error);
            (void)botw_savefs_pread(store,invalid[j],UINT64_MAX,buffer,sizeof(buffer),&actual,&error);
            (void)botw_savefs_rename(store,name,invalid[j],&error);
        }
        (void)botw_savefs_usage(store,&used,&quota,&error);
        (void)botw_savefs_iterate(store,"/",visit,NULL,&error);
    }
    if (botw_savefs_commit(store,&error)) return 4;
    botw_savefs_close(store); return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); c = root / 'sanitize.c'; c.write_text(source)
            binary = root / 'sanitize'
            subprocess.run(['clang', '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g', '-I', str(ROOT / 'native/botw_resources'), str(c), str(ROOT / 'native/botw_resources/savefs.c'), '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary), str(root / 'saves')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')

    def test_invalid_requests_size_overflow_and_host_write_errors_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, path = self.mount(tmp); error, actual = Error(), ctypes.c_size_t(999)
            self.assertEqual(self.lib.botw_savefs_create(store, b'/a', 0, ctypes.byref(error)), 0)
            self.assertEqual(self.lib.botw_savefs_create(store, b'/huge', 2**64-1, ctypes.byref(error)), errno.EFBIG)
            self.assertFalse((path / 'huge').exists())
            self.assertEqual(self.write(store, b'/a', b'x', 2**64-1)[:2], (errno.EFBIG, 0))
            self.assertEqual(self.lib.botw_savefs_pwrite(store, b'/a', 0, None, 1, ctypes.byref(actual), None), errno.EINVAL)
            self.assertEqual(actual.value, 0)
            buffer = ctypes.create_string_buffer(1)
            self.assertEqual(self.lib.botw_savefs_pwrite(store, b'/a', 0, buffer, 17*1024**2, ctypes.byref(actual), None), errno.EINVAL)
            self.assertEqual(self.lib.botw_savefs_remove(store, b'/', ctypes.byref(error)), errno.EBUSY)
        # RLIMIT_FSIZE generates a real partial pwrite followed by EFBIG.
        program = '''import ctypes, errno, json, resource, signal, sys
lib=ctypes.CDLL(sys.argv[1]); lib.botw_savefs_open.restype=ctypes.c_void_p
lib.botw_savefs_open.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint64,ctypes.c_void_p]
lib.botw_savefs_create.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint64,ctypes.c_void_p]
lib.botw_savefs_pwrite.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p]
lib.botw_savefs_close.argtypes=[ctypes.c_void_p]
store=lib.botw_savefs_open(sys.argv[2].encode(),b'01007ef00011e000',b'00112233445566778899aabbccddeeff',0,None)
assert store and lib.botw_savefs_create(store,b'/partial',0,None)==0
signal.signal(signal.SIGXFSZ,signal.SIG_IGN); resource.setrlimit(resource.RLIMIT_FSIZE,(4,4))
actual=ctypes.c_size_t(); result=lib.botw_savefs_pwrite(store,b'/partial',0,ctypes.create_string_buffer(b'12345678'),8,ctypes.byref(actual),None)
lib.botw_savefs_close(store); print(json.dumps([result,actual.value]))
'''
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(self.lib)
            result = subprocess.run([sys.executable, '-c', program, str(self.binary), str(Path(tmp) / 'saves')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [errno.EFBIG, 4])


if __name__ == '__main__': unittest.main()
