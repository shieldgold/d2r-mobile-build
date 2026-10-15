"""Ensure the cloud artifact contains only a generic host and its provenance."""
import hashlib
import json
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts.botw_native import build_ios_shell
from scripts.botw_native.build_ios_shell import package_shell


class ShellPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / 'BOTWNative.app'
        self.app.mkdir()
        self.binary = b'\xcf\xfa\xed\xfe' + b'generic-arm64-host'
        (self.app / 'BOTWNative').write_bytes(self.binary)
        (self.app / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.botw.native', 'CFBundleExecutable': 'BOTWNative',
            'MinimumOSVersion': '26.0'}))

    def test_cloud_package_has_no_guest_and_records_binary_digest(self):
        ipa = self.root / 'host.ipa'
        package_shell(self.app, ipa, {'native/botw_host/runtime.c': 'a' * 64}, '26.5')
        with zipfile.ZipFile(ipa) as z:
            self.assertEqual(set(z.namelist()), {
                'Payload/BOTWNative.app/BOTWNative', 'Payload/BOTWNative.app/Info.plist',
                'Payload/BOTWNative.app/shell-manifest.json'})
            m = json.loads(z.read('Payload/BOTWNative.app/shell-manifest.json'))
        self.assertFalse(m['game_content_included'])
        self.assertEqual(m['executable_sha256'], hashlib.sha256(self.binary).hexdigest())

    def test_extra_resource_or_game_file_is_rejected_before_publishing(self):
        (self.app / 'game.nso').write_bytes(b'never-upload')
        ipa = self.root / 'host.ipa'
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            package_shell(self.app, ipa, {}, '26.5')
        self.assertFalse(ipa.exists())

    def test_symlink_binary_is_rejected(self):
        (self.app / 'BOTWNative').unlink()
        (self.root / 'external').write_bytes(self.binary)
        (self.app / 'BOTWNative').symlink_to(self.root / 'external')
        with self.assertRaisesRegex(ValueError, 'regular'):
            package_shell(self.app, self.root / 'host.ipa', {}, '26.5')


class ShellCompilerLifetimeTests(unittest.TestCase):
    def compile_commands(self):
        sources = ('native/probe/lifetime.m', 'native/probe/host.c', 'native/probe/entry.S')
        commands = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'native/probe').mkdir(parents=True)
            for name in sources:
                (root / name).write_text('/* synthetic compiler input */\n')

            def sdk(command, **unused):
                if '--show-sdk-path' in command:
                    return '/synthetic/iphoneos-sdk\n'
                if '--show-sdk-version' in command:
                    return '18.0\n'
                return '/synthetic/clang\n'

            def compile(command, **unused):
                commands.append(command)
                Path(command[command.index('-o') + 1]).write_bytes(b'\xcf\xfa\xed\xfe' + b'generic-host-output')
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(build_ios_shell, 'SOURCES', sources), \
                    mock.patch.object(build_ios_shell, 'SOURCE_DIRECTORIES', ('probe',)), \
                    mock.patch.object(build_ios_shell.subprocess, 'check_output', side_effect=sdk), \
                    mock.patch.object(build_ios_shell.subprocess, 'run', side_effect=compile):
                build_ios_shell.build_shell(root, root / 'published')
        return commands

    def test_c_and_assembly_do_not_receive_objective_c_ownership_options(self):
        commands = self.compile_commands()
        native = [command for command in commands if '-c' in command]
        self.assertEqual(len(native), 3)
        for command in native:
            source = command[command.index('-c') + 1]
            if source.endswith(('.c', '.S')):
                self.assertFalse(any(flag.startswith('-fobjc-arc') for flag in command))

    @unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                         'real Objective-C exception cleanup requires Apple Foundation')
    def test_emitted_objective_c_options_release_owned_resources_on_exception(self):
        commands = self.compile_commands()
        objc = next(command for command in commands if '-c' in command and
                    command[command.index('-c') + 1].endswith('.m'))
        ownership = [flag for flag in objc if flag.startswith('-fobjc-arc')]
        with tempfile.TemporaryDirectory() as folder:
            source, binary = Path(folder) / 'lifetime.m', Path(folder) / 'lifetime'
            source.write_text(r'''
#import <Foundation/Foundation.h>
#include <stdio.h>
static __weak NSObject *observed;
__attribute__((noinline)) static void failWithOwnedResource(int shouldFail) {
    NSObject *resource = [NSObject new]; observed = resource;
    if (shouldFail) @throw [NSException exceptionWithName:@"SyntheticOwnedResource" reason:nil userInfo:nil];
    NSLog(@"%@", resource);
}
int main(int argc, char **argv) {
    (void)argv;
    @autoreleasepool { @try { failWithOwnedResource(argc); } @catch (NSException *error) { (void)error; } }
    printf("owned_object_alive_after_unwind=%d\n", observed != nil);
    return observed ? 3 : 0;
}
''')
            built = subprocess.run(['xcrun', 'clang', '-O2', '-Wall', '-Wextra', '-Werror',
                                    *ownership, str(source), '-framework', 'Foundation', '-o', str(binary)],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('owned_object_alive_after_unwind=0', result.stdout)


if __name__ == '__main__':
    unittest.main()
