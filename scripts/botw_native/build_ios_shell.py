"""Compile only the generic iOS host. Game files never enter this artifact."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import zipfile

SOURCES = ('native/botw_host/runtime.c', 'native/botw_host/services.c', 'native/botw_host/kernel.c',
           'native/botw_host/entry.S', 'native/botw_resources/romfs.c',
           'native/botw_input/input.c', 'native/botw_input/BOTWInputOverlay.m',
           'native/botw_graphics/graphics.m', 'native/botw_app/main.m')
SOURCE_DIRECTORIES = ('botw_host', 'botw_app', 'botw_resources', 'botw_input', 'botw_graphics')


def package_shell(app_dir, output_ipa, source_sha256, sdk_version):
    app, output = Path(app_dir), Path(output_ipa)
    if set(p.name for p in app.iterdir()) != {'BOTWNative', 'Info.plist'}:
        raise ValueError('Shell contains unexpected files; refusing cloud artifact')
    for name in ('BOTWNative', 'Info.plist'):
        p = app / name
        if p.is_symlink() or not p.is_file():
            raise ValueError('Shell input must be a regular file')
    binary = (app / 'BOTWNative').read_bytes()
    info = plistlib.loads((app / 'Info.plist').read_bytes())
    if (binary[:4] != b'\xcf\xfa\xed\xfe' or
            info.get('CFBundleIdentifier') != 'local.botw.native' or
            info.get('CFBundleExecutable') != 'BOTWNative'):
        raise ValueError('Unexpected shell executable or bundle identity')
    manifest = {'schema': 1, 'role': 'botw-native-host', 'game_content_included': False,
                'source_sha256': source_sha256, 'sdk_version': sdk_version,
                'executable_sha256': hashlib.sha256(binary).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.ipa', delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ('BOTWNative', 'Info.plist'):
                entry = zipfile.ZipInfo('Payload/BOTWNative.app/' + name)
                entry.external_attr = (0o100755 if name == 'BOTWNative' else 0o100644) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, (app / name).read_bytes())
            archive.writestr('Payload/BOTWNative.app/shell-manifest.json',
                             json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def build_shell(repo_root, output_dir):
    root, output = Path(repo_root).resolve(), Path(output_dir).resolve()
    sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
    version = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-version'], text=True).strip()
    clang = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--find', 'clang'], text=True).strip()
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCES}
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((root / 'native' / directory).rglob('*')):
            if path.is_file() and path.suffix in ('.h', '.inc', '.c', '.m', '.S') and 'tools' not in path.relative_to(root).parts:
                hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='botw-ios-shell-') as directory:
        work = Path(directory)
        app = work / 'BOTWNative.app'
        app.mkdir()
        objects = []
        base = [clang, '-target', 'arm64-apple-ios26.0', '-isysroot', sdk,
                '-O2', '-Wall', '-Wextra', '-Werror', '-DBOTW_WITH_GRAPHICS=1', '-fblocks',
                *[argument for directory in SOURCE_DIRECTORIES for argument in ('-I', str(root / 'native' / directory))]]
        for index, source in enumerate(SOURCES):
            obj = work / f'{index}.o'
            language = ['-fobjc-arc'] if source.endswith('.m') else (['-std=c11'] if source.endswith('.c') else [])
            subprocess.run([*base, *language, '-c', str(root / source), '-o', str(obj)], check=True)
            objects.append(str(obj))
        subprocess.run([*base, *objects, '-framework', 'UIKit', '-framework', 'Foundation',
                        '-framework', 'Metal', '-framework', 'QuartzCore', '-framework', 'GameController',
                        '-framework', 'CoreGraphics', '-lobjc',
                        '-Wl,-rpath,@executable_path/Frameworks', '-o', str(app / 'BOTWNative')], check=True)
        info = {'CFBundleIdentifier': 'local.botw.native', 'CFBundleExecutable': 'BOTWNative',
                'CFBundleName': 'BOTWNative', 'CFBundleDisplayName': 'BOTW Native',
                'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '0.1',
                'MinimumOSVersion': '26.0', 'LSRequiresIPhoneOS': True,
                'UIDeviceFamily': [1, 2], 'UILaunchScreen': {},
                'UIApplicationSceneManifest': {'UIApplicationSupportsMultipleScenes': False},
                'UIFileSharingEnabled': True, 'LSSupportsOpeningDocumentsInPlace': True,
                'UISupportedInterfaceOrientations': ['UIInterfaceOrientationLandscapeLeft',
                    'UIInterfaceOrientationLandscapeRight']}
        (app / 'Info.plist').write_bytes(plistlib.dumps(info))
        return package_shell(app, output / 'BOTWNative-shell.ipa', hashes, version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build_shell(Path(__file__).resolve().parents[2], args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
