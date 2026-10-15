"""Persistent offline identity through actual private files and processes."""
import ctypes as C
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
import platform
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
CLI = r'''
#include "local_user.h"
#include <stdio.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    botw_local_user user; botw_local_user_error error;
    int result = botw_local_user_open(argv[1], &user, &error);
    if (result) { fprintf(stderr, "%d: %s\n", error.code, error.message); return 1; }
    puts(user.uid_hex); return 0;
}
'''


class User(C.Structure):
    _fields_ = [('uid', C.c_ubyte * 16), ('uid_hex', C.c_char * 33)]


class Error(C.Structure):
    _fields_ = [('code', C.c_int), ('message', C.c_char * 192)]


class LocalUserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-local-user-build-')
        cls.source = ROOT / 'native/botw_resources/local_user.c'
        if not cls.source.exists():
            raise AssertionError('local offline user backend is missing')
        directory = Path(cls.build.name)
        cls.library = directory / 'user.dylib'
        cls.cli_source = directory / 'cli.c'
        cls.cli_source.write_text(CLI)
        cls.cli = directory / 'user'
        flags = [shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                 '-Wconversion', '-Wshadow', '-pedantic', '-I', str(cls.source.parent)]
        if platform.system() == 'Darwin':
            flags += ['-framework', 'Security']
        subprocess.run(flags + ['-shared', '-fPIC', str(cls.source), '-o', str(cls.library)], check=True)
        subprocess.run(flags + [str(cls.cli_source), str(cls.source), '-o', str(cls.cli)], check=True)
        cls.flags = flags
        cls.lib = C.CDLL(str(cls.library))
        cls.lib.botw_local_user_open.argtypes = [C.c_char_p, C.POINTER(User), C.POINTER(Error)]
        cls.lib.botw_local_user_open.restype = C.c_int

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='botw-local-user-')
        self.addCleanup(self.temp.cleanup)
        self.documents = Path(self.temp.name) / 'Documents'
        self.documents.mkdir(mode=0o700)

    def open(self, documents=None, good=True):
        user, error = User(), Error()
        C.memset(C.byref(user), 0xa5, C.sizeof(user))
        before = bytes(user)
        path = str(self.documents if documents is None else documents).encode()
        result = self.lib.botw_local_user_open(path, C.byref(user), C.byref(error))
        if good:
            self.assertEqual(result, 0, error.message.decode())
            self.assertEqual(error.code, 0)
            self.assertNotEqual(bytes(user.uid), bytes(16))
            self.assertEqual(user.uid_hex.decode(), bytes(user.uid).hex())
        else:
            self.assertNotEqual(result, 0)
            self.assertEqual(error.code, result)
            self.assertTrue(error.message)
            self.assertEqual(bytes(user), before)
        return user

    @property
    def profile(self):
        return self.documents / 'botw-profile'

    def test_create_private_nonzero_uid_and_reopen_exact_bytes(self):
        first = self.open()
        content = (self.profile / 'uid.bin').read_bytes()
        self.assertEqual(len(content), 32)
        self.assertEqual(set(p.name for p in self.profile.iterdir()), {'.lock', 'uid.bin'})
        for path, mode in ((self.profile, 0o700), (self.profile / 'uid.bin', 0o600),
                           (self.profile / '.lock', 0o600)):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        self.assertEqual(bytes(self.open()), bytes(first))
        self.assertEqual((self.profile / 'uid.bin').read_bytes(), content)
        identities = {first.uid_hex}
        for index in range(8):
            documents = Path(self.temp.name) / f'Documents{index}'
            documents.mkdir(mode=0o700)
            identities.add(self.open(documents).uid_hex)
        self.assertEqual(len(identities), 9)

    def test_concurrent_process_creation_and_reopening_share_one_uid(self):
        processes = [subprocess.Popen([str(self.cli), str(self.documents)], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(16)]
        values = []
        for process in processes:
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error)
            values.append(output.strip())
        self.assertEqual(len(set(values)), 1)
        self.assertEqual(values[0], self.open().uid_hex.decode())
        again = subprocess.run([str(self.cli), str(self.documents)], capture_output=True, text=True, timeout=5)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout.strip(), values[0])

    def test_versioned_format_has_independent_crc_and_opaque_uid_byte_order(self):
        self.profile.mkdir(mode=0o700)
        identity = bytes(range(16))
        prefix = b'BOTWUID1' + identity + bytes(4)
        content = prefix + zlib.crc32(prefix).to_bytes(4, 'little')
        path = self.profile / 'uid.bin'
        path.write_bytes(content)
        path.chmod(0o600)
        user = self.open()
        self.assertEqual(bytes(user.uid), identity)
        self.assertEqual(user.uid_hex.decode(), '000102030405060708090a0b0c0d0e0f')
        self.assertEqual(path.read_bytes(), content)

    def test_flock_actually_serializes_reopening(self):
        first = self.open()
        with (self.profile / '.lock').open('r+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen([str(self.cli), str(self.documents)], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(0.1)
                self.assertIsNone(process.poll())
                fcntl.flock(lock, fcntl.LOCK_UN)
                output, error = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, error)
                self.assertEqual(output.strip(), first.uid_hex.decode())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_concurrent_threads_also_create_only_one_profile(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            identities = list(pool.map(lambda _: self.open().uid_hex, range(36)))
        self.assertEqual(len(set(identities)), 1)
        self.assertEqual(identities[0], self.open().uid_hex)

    def test_truncation_bad_version_zero_uid_and_every_byte_corruption_are_preserved(self):
        self.open()
        uid = self.profile / 'uid.bin'
        valid = uid.read_bytes()
        zero = b'BOTWUID1' + bytes(20)
        corruptions = [b'', valid[:-1], valid + b'\0', zero + zlib.crc32(zero).to_bytes(4, 'little')]
        for index in range(32):
            changed = bytearray(valid)
            changed[index] ^= 1
            corruptions.append(bytes(changed))
        for damaged in corruptions:
            uid.write_bytes(damaged)
            self.open(good=False)
            self.assertEqual(uid.read_bytes(), damaged)

    def test_symlink_documents_profile_uid_and_lock_never_follow_targets(self):
        target = Path(self.temp.name) / 'target'
        target.mkdir(mode=0o700)
        marker = target / 'marker'
        marker.write_bytes(b'outside')
        linked_documents = Path(self.temp.name) / 'DocumentsLink'
        linked_documents.symlink_to(target, target_is_directory=True)
        self.open(linked_documents, good=False)
        self.assertEqual(set(p.name for p in target.iterdir()), {'marker'})
        self.profile.symlink_to(target, target_is_directory=True)
        self.open(good=False)
        self.profile.unlink()
        self.open()
        for name in ('uid.bin', '.lock'):
            path = self.profile / name
            content = path.read_bytes()
            path.unlink()
            path.symlink_to(marker)
            self.open(good=False)
            self.assertTrue(path.is_symlink())
            self.assertEqual(marker.read_bytes(), b'outside')
            path.unlink()
            path.write_bytes(content)
            path.chmod(0o600)

    def test_hardlinks_special_files_and_nonprivate_modes_are_rejected_without_repair(self):
        self.open()
        for name in ('uid.bin', '.lock'):
            path = self.profile / name
            content = path.read_bytes()
            link = Path(self.temp.name) / f'{name}.external'
            os.link(path, link)
            self.open(good=False)
            self.assertEqual(path.read_bytes(), content)
            link.unlink()
            path.chmod(0o644)
            self.open(good=False)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            path.unlink()
            os.mkfifo(path, 0o600)
            result = subprocess.run([str(self.cli), str(self.documents)], capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 1)  # O_NONBLOCK avoids waiting for a FIFO peer
            path.unlink()
            path.write_bytes(content)
            path.chmod(0o600)
        self.profile.chmod(0o755)
        self.open(good=False)
        self.assertEqual(stat.S_IMODE(self.profile.stat().st_mode), 0o755)

    def test_invalid_paths_and_arguments_do_not_create_a_profile(self):
        for path in ('', 'relative/Documents', '/', str(self.documents) + '/',
                     str(self.documents) + '/.', str(self.documents) + '/../Documents',
                     str(self.documents).replace('/Documents', '//Documents')):
            self.open(path, good=False)
        self.open(Path(self.temp.name) / 'Missing', good=False)
        user, error = User(), Error()
        self.assertNotEqual(self.lib.botw_local_user_open(None, C.byref(user), C.byref(error)), 0)
        self.assertNotEqual(self.lib.botw_local_user_open(str(self.documents).encode(), None, C.byref(error)), 0)
        self.assertFalse(self.profile.exists())

    def test_sanitized_creation_reopening_and_corrupt_input(self):
        binary = Path(self.build.name) / 'user-sanitized'
        subprocess.run(self.flags + ['-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                                    str(self.cli_source), str(self.source), '-o', str(binary)], check=True)
        for _ in range(2):
            result = subprocess.run([str(binary), str(self.documents)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        (self.profile / 'uid.bin').write_bytes(b'corrupt')
        result = subprocess.run([str(binary), str(self.documents)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Sanitizer', result.stderr)

    def test_entropy_failure_or_zero_entropy_never_publishes_an_identity(self):
        for name, body in [('failed', 'errno=EIO; return -1;'),
                           ('zero', 'memset(buffer,0,length); return 0;')]:
            shim = Path(self.build.name) / f'entropy-{name}.c'
            if platform.system() == 'Darwin':
                shim.write_text('#include <errno.h>\n#include <string.h>\n#include <Security/SecRandom.h>\n'
                                'int fixture_entropy(SecRandomRef random,size_t length,void *buffer) { '
                                '(void)random; (void)buffer; (void)length; ' + body + ' }\n')
                replacement = '-DSecRandomCopyBytes=fixture_entropy'
            else:
                shim.write_text('#include <errno.h>\n#include <stddef.h>\n#include <string.h>\n'
                                'int fixture_entropy(void *buffer,size_t length) { '
                                '(void)buffer; (void)length; ' + body + ' }\n')
                replacement = '-Dgetentropy=fixture_entropy'
            binary = Path(self.build.name) / f'user-entropy-{name}'
            subprocess.run(self.flags + [replacement, str(self.cli_source),
                                        str(self.source), str(shim), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary), str(self.documents)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertFalse((self.profile / 'uid.bin').exists())
            self.assertEqual(set(p.name for p in self.profile.iterdir()), {'.lock'})
        self.open()  # a later real entropy source can create the still-missing UID


if __name__ == '__main__':
    unittest.main()
