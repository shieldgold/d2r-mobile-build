"""Reuse one existing offline identity; never make a replacement profile."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'
BASELINE = os.environ.get('BOTW_WHOLE_IDENTITY_BASELINE') == '1'
PROBE = r'''
#include "botw_account.h"
#include "local_user.h"
#include <assert.h>
#include <string.h>
#if !defined(IDENTITY_BASELINE)
#include "whole_identity.h"
#if defined(__APPLE__)
#include "botw_fs.c"
struct dm_flags g_flag;
void dm_guest_write_prepare(void *p, size_t n) { assert(p && n); }
#endif
#endif
int main(int argc, char **argv) {
    assert(argc == 3);
    const unsigned char expected[16] = {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
#if defined(IDENTITY_BASELINE)
    botw_local_user loaded; botw_local_user_error error;
    assert(botw_local_user_load_existing(argv[1], &loaded, &error) == 0);
    assert(!memcmp(&botw_local_uid, expected, 16));
#else
    if (!strcmp(argv[2], "unbound")) { (void)whole_identity_uid(); return 2; }
    char error[192] = {0};
    int result = whole_identity_bind_documents(argv[1], error, sizeof error);
    if (!strcmp(argv[2], "reject")) {
        assert(result != 0 && error[0]);
        assert(whole_identity_save_directory() == NULL);
        return 0;
    }
    assert(result == 0 && !error[0]);
    assert(!memcmp(&botw_local_uid, expected, 16));
    assert(botw_uid_is_local(expected));
    unsigned char other[16] = {1}; assert(!botw_uid_is_local(other));
    assert(strstr(whole_identity_save_directory(),
        "/botw-saves/01007ef00011e000/000102030405060708090a0b0c0d0e0f"));
    assert(whole_identity_bind_documents(argv[1], error, sizeof error) == 0);
#if defined(__APPLE__)
    if (!strcmp(argv[2], "save")) {
        char decoy[4096];
        snprintf(decoy, sizeof decoy, "%s/decoy", argv[1]);
        assert(!setenv("DM_SAVE", decoy, 1));
        assert(botw_fs_MountSaveData("save", &botw_local_uid) == RES_OK);
        int mount = mount_find("save", 4);
        char canonical[4096];
        assert(realpath(whole_identity_save_directory(), canonical));
        assert(mount >= 0 && !strcmp(g_mnt[mount].dir, canonical));
        botw_fs_Unmount("save");
    }
#endif
#endif
    return 0;
}
'''


class WholeIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-whole-identity-')
        cls.addClassCleanup(cls.build.cleanup)
        directory = Path(cls.build.name)
        source = directory / 'stage'
        shutil.copytree(COMPONENT / 'upstream', source)
        if not BASELINE:
            subprocess.run(['patch', '--batch', '-p1', '-d', str(source), '-i',
                str(COMPONENT / 'patches/persistent-identity.patch')], check=True,
                capture_output=True, text=True)
        probe = directory / 'probe.c'
        probe.write_text(PROBE)
        cls.binary = directory / 'probe'
        command = [shutil.which('clang') or 'cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-Wno-format',
            '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I', str(source / 'dm/host'), '-I', str(ROOT / 'native/botw_resources'),
            '-I', str(COMPONENT / 'adapter'), str(probe),
            str(ROOT / 'native/botw_resources/local_user.c'), '-o', str(cls.binary)]
        if BASELINE:
            command += ['-DIDENTITY_BASELINE=1']
        else:
            command += [str(COMPONENT / 'adapter/whole_identity.c'),
                        str(COMPONENT / 'adapter/whole_policy.c')]
        if platform.system() == 'Darwin':
            command += ['-framework', 'Security']
        subprocess.run(command, check=True, capture_output=True, text=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='botw-whole-identity-data-')
        self.addCleanup(self.temp.cleanup)
        self.documents = Path(self.temp.name) / 'Documents'
        self.documents.mkdir(mode=0o700)
        self.profile = self.documents / 'botw-profile'
        self.profile.mkdir(mode=0o700)
        self.uid = self.profile / 'uid.bin'
        prefix = b'BOTWUID1' + bytes(range(16)) + bytes(4)
        self.uid.write_bytes(prefix + zlib.crc32(prefix).to_bytes(4, 'little'))
        self.uid.chmod(0o600)
        (self.profile / '.lock').touch(mode=0o600)
        self.save = self.documents / 'botw-saves/01007ef00011e000/000102030405060708090a0b0c0d0e0f'
        for path in (self.save.parents[1], self.save.parent, self.save):
            path.mkdir(mode=0o700)

    def run_probe(self, mode, expected=0):
        run = subprocess.run([str(self.binary), str(self.documents), mode], capture_output=True, text=True)
        self.assertEqual(run.returncode, expected, run.stdout + run.stderr)

    def test_existing_identity_binds_same_uid_and_same_save_path(self):
        before = self.uid.read_bytes()
        self.run_probe('bind')
        self.assertEqual(self.uid.read_bytes(), before)
        self.assertEqual(set(p.name for p in self.profile.iterdir()), {'uid.bin', '.lock'})

    @unittest.skipIf(BASELINE or platform.system() != 'Darwin', 'actual upstream save mount requires Apple FS APIs')
    def test_actual_save_mount_uses_bound_account_even_with_different_environment(self):
        self.run_probe('save')
        self.assertFalse((self.documents / 'decoy').exists())

    @unittest.skipIf(BASELINE, 'baseline has no fail-closed identity provider')
    def test_missing_uid_or_save_root_is_rejected_without_creation(self):
        self.uid.unlink()
        self.run_probe('reject')
        self.assertFalse(self.uid.exists())
        self.setUp()
        self.save.rmdir()
        self.run_probe('reject')
        self.assertFalse(self.save.exists())

    @unittest.skipIf(BASELINE, 'baseline has no fail-closed identity provider')
    def test_symlinked_save_directory_is_rejected_without_touching_target(self):
        self.save.rmdir()
        target = Path(self.temp.name) / 'outside'
        target.mkdir(mode=0o700)
        self.save.symlink_to(target, target_is_directory=True)
        self.run_probe('reject')
        self.assertTrue(self.save.is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    @unittest.skipIf(BASELINE, 'baseline always supplies a fallback UID')
    def test_unconfigured_account_access_fails_before_any_uid_is_returned(self):
        self.run_probe('unbound', 70)


if __name__ == '__main__':
    unittest.main()
