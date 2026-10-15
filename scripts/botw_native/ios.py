"""Assemble and sign a local BOTW development app without uploading game inputs.

The cloud-built shell is restricted to three independently verified files.
Guest.framework is rebuilt from the audited local inputs before publication,
so a self-consistent but replaced framework/report pair cannot bypass checks.
This module never installs, launches, or exports a private signing key.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

from ci import macho_minimum_os
from .guest_framework import build_framework, MAX_METADATA_BYTES, MAX_TOTAL_BYTES

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ID = 'local.botw.native'
SHELL_BASE = 'Payload/BOTWNative.app/'
SHELL_FILES = {'Info.plist', 'BOTWNative', 'shell-manifest.json'}
SUPPORTED_HOST_MINIMUMS = {'18.0': (18, 0, 0), '26.0': (26, 0, 0)}
REQUIRED_SOURCES = {'native/botw_host/runtime.c', 'native/botw_host/runtime.h',
                    'native/botw_host/entry.S', 'native/botw_app/main.m'}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('Duplicate JSON field: ' + name)
            result[name] = value
        return result
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid UTF-8 JSON metadata') from exc


def _read(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular non-symlink file: ' + path.name)
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('Input file exceeds size limit: ' + path.name)
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(data) != before.st_size or len(data) > limit or
            (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
        raise ValueError('Input changed during bounded read: ' + path.name)
    return data


def _relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\0' in name or
            name.startswith('/') or any(piece in ('', '.', '..') for piece in name.split('/'))):
        raise ValueError('Unsafe input path')
    return PurePosixPath(name)


def _tree(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Expected a regular non-symlink directory')
    files = {}
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Symlink or special file in input tree')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path
    return files


def _shell(archive_path, source_root, limit, expected_digest):
    if type(limit) is not int or limit <= 0:
        raise ValueError('Invalid shell expansion limit')
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size > limit:
        raise ValueError('Shell archive exceeds regular file size limit')
    archive_bytes = _read(archive_path, limit)
    if _digest(archive_bytes) != expected_digest:
        raise ValueError('Shell IPA SHA256 differs from the independently trusted build artifact')
    files, seen, total = {}, set(), 0
    try:
        # Parse the exact bytes that matched the trusted digest, not a reopened path.
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            if len(archive.infolist()) > 16:
                raise ValueError('Shell archive member count exceeds limit')
            for entry in archive.infolist():
                name = entry.filename[:-1] if entry.is_dir() else entry.filename
                _relative(name)
                normalized = unicodedata.normalize('NFC', name).casefold()
                if normalized in seen:
                    raise ValueError('Duplicate or colliding shell archive path')
                seen.add(normalized)
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if (kind not in (0, stat.S_IFREG, stat.S_IFDIR) or
                        kind and (kind == stat.S_IFDIR) != entry.is_dir()):
                    raise ValueError('Symlink or special member in shell archive')
                if entry.flag_bits & 1:
                    raise ValueError('Encrypted shell archive member')
                if entry.is_dir():
                    if name not in ('Payload', SHELL_BASE.rstrip('/')):
                        raise ValueError('Unexpected shell directory')
                    continue
                if not name.startswith(SHELL_BASE) or name[len(SHELL_BASE):] not in SHELL_FILES:
                    raise ValueError('Unexpected file in content-free shell')
                total += entry.file_size
                if entry.file_size > limit or total > limit:
                    raise ValueError('Shell archive expansion exceeds limit')
                with archive.open(entry) as stream:
                    data = stream.read(entry.file_size + 1)
                if len(data) != entry.file_size:
                    raise ValueError('Shell archive member size mismatch')
                files[name[len(SHELL_BASE):]] = data
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError('Invalid shell ZIP archive') from exc
    if set(files) != SHELL_FILES:
        raise ValueError('The shell must contain exactly the three host files')
    try:
        info = plistlib.loads(files['Info.plist'])
    except Exception as exc:
        raise ValueError('Invalid shell Info.plist') from exc
    if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != BUNDLE_ID or
            info.get('CFBundleExecutable') != 'BOTWNative' or not isinstance(info.get('MinimumOSVersion'), str) or
            info['MinimumOSVersion'] not in SUPPORTED_HOST_MINIMUMS or
            info.get('CFBundlePackageType') != 'APPL'):
        raise ValueError('Shell bundle identifier, executable or minimum iOS mismatch')
    binary = files['BOTWNative']
    if (macho_minimum_os(binary) != SUPPORTED_HOST_MINIMUMS[info['MinimumOSVersion']] or
            struct.unpack_from('<I', binary, 12)[0] != 2):
        raise ValueError('Shell must be a thin arm64 iOS executable with matching minimum OS metadata')
    manifest = _json(files['shell-manifest.json'])
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1 or
            manifest.get('role') != 'botw-native-host' or manifest.get('game_content_included') is not False or
            manifest.get('executable_sha256') != _digest(binary) or
            not isinstance(manifest.get('sdk_version'), str)):
        raise ValueError('Shell manifest or executable digest mismatch')
    sources = manifest.get('source_sha256')
    if not isinstance(sources, dict) or not REQUIRED_SOURCES <= sources.keys() or len(sources) > 100:
        raise ValueError('Shell manifest omits required native source provenance')
    source_root = Path(source_root).resolve()
    for relative, digest in sources.items():
        parts = _relative(relative)
        if len(parts.parts) < 3 or parts.parts[:2] not in (
                ('native', 'botw_host'), ('native', 'botw_app'), ('native', 'botw_graphics'),
                ('native', 'botw_input'), ('native', 'botw_resources'), ('native', 'botw_audio')):
            raise ValueError('Shell manifest references an unexpected source path')
        path = source_root.joinpath(*parts.parts)
        if not path.resolve().is_relative_to(source_root) or any(
                parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(source_root)):
            raise ValueError('Shell source provenance includes a symlink')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or _digest(_read(path, MAX_METADATA_BYTES)) != digest:
            raise ValueError('Local shell source SHA256 mismatch: ' + relative)
    return files


def assemble_app(shell_ipa, guest_dir, report_path, rewrite_dir, output_app, *, expected_shell_sha256,
                 source_root=ROOT, max_shell_bytes=256 * 1024**2):
    """Verify the content-free shell and local source provenance, then publish an unsigned .app.

    guest_dir is the output root returned by guest_framework.build_framework.
    output_app must not exist. Only fixups/gates and reports enter app/botw;
    rewritten program bytes exist solely in the signed-code framework location.
    expected_shell_sha256 is required and must come from a trusted build artifact
    channel verified independently of this IPA, never from its own manifest.
    """
    if (not isinstance(expected_shell_sha256, str) or
            not re.fullmatch('[0-9a-fA-F]{64}', expected_shell_sha256)):
        raise ValueError('A complete independently trusted shell IPA SHA256 is required')
    output_app, guest_dir = Path(output_app), Path(guest_dir)
    if output_app.exists() or output_app.is_symlink():
        raise FileExistsError('Output app already exists')
    if output_app.name != 'BOTWNative.app':
        raise ValueError('Output app must be named BOTWNative.app')
    shell = _shell(shell_ipa, source_root, max_shell_bytes, expected_shell_sha256.lower())
    published = _tree(guest_dir)
    metadata_bytes = _read(guest_dir / 'guest-framework-report.json', MAX_METADATA_BYTES)
    metadata = _json(metadata_bytes)
    if not isinstance(metadata, dict) or not isinstance(metadata.get('modules'), list):
        raise ValueError('Invalid guest framework metadata')
    try:
        names = [module['name'] for module in metadata['modules']]
    except (KeyError, TypeError) as exc:
        raise ValueError('Invalid guest framework module metadata') from exc
    expected_paths = {'Guest.framework/Guest', 'Guest.framework/Info.plist', 'guest-framework-report.json'}
    expected_paths.update(f'dm/{name}.{kind}.tsv' for name in names for kind in ('fixups', 'gates'))
    if set(published) != expected_paths:
        raise ValueError('Unexpected or missing files in local guest output')
    guest_info = plistlib.loads(_read(guest_dir / 'Guest.framework/Info.plist', MAX_METADATA_BYTES))
    if not isinstance(guest_info, dict):
        raise ValueError('Invalid guest Info.plist')
    minimum_ios = guest_info.get('MinimumOSVersion')
    if not isinstance(minimum_ios, str) or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', minimum_ios):
        raise ValueError('Invalid guest minimum iOS metadata')
    guest_minimum = tuple(map(int, minimum_ios.split('.')))
    guest_minimum += (0,) * (3 - len(guest_minimum))
    host_minimum = SUPPORTED_HOST_MINIMUMS[plistlib.loads(shell['Info.plist'])['MinimumOSVersion']]
    if guest_minimum > host_minimum:
        raise ValueError('Guest minimum iOS exceeds host minimum')
    report_bytes = _read(report_path, MAX_METADATA_BYTES)
    output_app.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.botw-assembly-', dir=output_app.parent))
    try:
        regenerated = staging / 'verified-guest'
        expected = build_framework(report_path, rewrite_dir, regenerated, module_names=names, minimum_ios=minimum_ios)
        if metadata != expected:
            raise ValueError('Guest metadata or source digest mismatch; regenerate from audited inputs')
        if not any(s['path'] == 'report.json' and s['sha256'] == _digest(report_bytes) for s in expected['sources']):
            raise ValueError('Preflight report changed while assembling')
        for relative, path in published.items():
            limit = MAX_TOTAL_BYTES if relative == 'Guest.framework/Guest' else MAX_METADATA_BYTES
            if relative != 'guest-framework-report.json' and _read(path, limit) != _read(regenerated / relative, limit):
                raise ValueError('Published guest framework or runtime table differs from source: ' + relative)
        app = staging / 'BOTWNative.app'
        app.mkdir()
        for name, data in shell.items():
            (app / name).write_bytes(data)
        info = plistlib.loads((app / 'Info.plist').read_bytes())
        info['BOTWGuestAddressBits'] = expected['guest_address_bits']
        if 'guest_heap_policy' in expected:
            info['BOTWGuestHeapPolicy'] = expected['guest_heap_policy']['profile']
        if 'guest_thread_stack_policy' in expected:
            info['BOTWGuestThreadStackPolicy'] = expected['guest_thread_stack_policy']['profile']
        (app / 'Info.plist').write_bytes(plistlib.dumps(info, sort_keys=True))
        (app / 'BOTWNative').chmod(0o755)
        (app / 'Frameworks').mkdir()
        (regenerated / 'Guest.framework').rename(app / 'Frameworks/Guest.framework')
        (regenerated / 'dm').rename(app / 'botw')
        (app / 'botw/guest-framework-report.json').write_bytes(metadata_bytes)
        (app / 'botw/preflight-report.json').write_bytes(report_bytes)
        if output_app.exists() or output_app.is_symlink():
            raise FileExistsError('Output appeared during assembly')
        app.rename(output_app)
    finally:
        shutil.rmtree(staging)
    return output_app


def validate_profile(profile, identity, udid, *, now=None):
    """Return minimal development entitlements after certificate/device/app matching."""
    if not isinstance(profile, dict):
        raise ValueError('Provisioning CMS must contain a profile dictionary')
    if not isinstance(identity, str) or not re.fullmatch('[0-9A-Fa-f]{40}', identity):
        raise ValueError('Signing identity must be an explicit certificate SHA1')
    now = now or datetime.datetime.now(datetime.timezone.utc)
    expiry = profile.get('ExpirationDate')
    if not isinstance(expiry, datetime.datetime):
        raise ValueError('Provisioning profile has no expiration date')
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    if expiry <= now:
        raise ValueError('Provisioning profile has expired')
    devices = profile.get('ProvisionedDevices', [])
    if (not isinstance(devices, list) or not isinstance(udid, str) or not udid or
            udid not in devices):
        raise ValueError('Provisioning profile does not include the target device')
    certificates = profile.get('DeveloperCertificates', [])
    if not isinstance(certificates, list) or not any(
            isinstance(cert, bytes) and hashlib.sha1(cert).hexdigest().upper() == identity.upper() for cert in certificates):
        raise ValueError('Provisioning profile does not include the signing certificate')
    teams = profile.get('TeamIdentifier', [])
    entitlements = profile.get('Entitlements', {})
    if (not isinstance(teams, list) or len(teams) != 1 or not isinstance(teams[0], str) or
            not re.fullmatch('[A-Z0-9]{10}', teams[0]) or not isinstance(entitlements, dict) or
            entitlements.get('com.apple.developer.team-identifier') != teams[0]):
        raise ValueError('Provisioning profile team identifier mismatch')
    team = teams[0]
    wanted = team + '.' + BUNDLE_ID
    permitted = entitlements.get('application-identifier')
    if not isinstance(permitted, str) or not (permitted == wanted or
            permitted.endswith('.*') and wanted.startswith(permitted[:-1])):
        raise ValueError('Provisioning profile does not allow the app bundle identifier')
    groups = entitlements.get('keychain-access-groups', [])
    if not isinstance(groups, list) or not any(group == wanted or group == team + '.*' for group in groups):
        raise ValueError('Provisioning profile does not allow the app keychain group')
    if type(entitlements.get('get-task-allow')) is not bool:
        raise ValueError('Provisioning profile has invalid development entitlement')
    return {'application-identifier': wanted, 'com.apple.developer.team-identifier': team,
            'keychain-access-groups': [wanted], 'get-task-allow': entitlements['get-task-allow']}


def _run(arguments):
    return subprocess.run(arguments, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def sign_app(app, profile_path, identity, udid, output_ipa):
    """Sign an assembled local app in place and atomically write an IPA; never install it.

    Provisioning CMS and signing identities are parsed internally. No private
    key is exported and no signing material is sent to a remote build runner.
    On signing failure no IPA is published; the local app may be partly signed.
    """
    app, profile_path, output_ipa = map(Path, (app, profile_path, output_ipa))
    if output_ipa.exists() or output_ipa.is_symlink():
        raise FileExistsError('Output IPA already exists')
    if output_ipa.resolve().is_relative_to(app.resolve()):
        raise ValueError('IPA output must be outside the app')
    files = _tree(app)
    info = plistlib.loads(_read(app / 'Info.plist', MAX_METADATA_BYTES))
    if (app.name != 'BOTWNative.app' or info.get('CFBundleIdentifier') != BUNDLE_ID or
            info.get('CFBundleExecutable') != 'BOTWNative' or not isinstance(info.get('MinimumOSVersion'), str) or
            info['MinimumOSVersion'] not in SUPPORTED_HOST_MINIMUMS or
            'BOTWNative' not in files or 'Frameworks/Guest.framework/Guest' not in files):
        raise ValueError('Unexpected development app layout or bundle identifier')
    profile_bytes = _read(profile_path, 1024**2)
    profile = plistlib.loads(_run(['security', 'cms', '-D', '-i', str(profile_path)]))
    entitlements = validate_profile(profile, identity, udid)
    identities = _run(['security', 'find-identity', '-v', '-p', 'codesigning']).decode('utf-8')
    if identity.upper() not in identities.upper():
        raise ValueError('The requested signing identity has no valid local private key')
    output_ipa.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.botw-sign-', dir=output_ipa.parent) as temp:
        temp = Path(temp)
        entitlement_path = temp / 'entitlements.plist'
        entitlement_path.write_bytes(plistlib.dumps(entitlements))
        (app / 'embedded.mobileprovision').write_bytes(profile_bytes)
        _run(['codesign', '--force', '--sign', identity, '--timestamp=none', str(app / 'Frameworks/Guest.framework')])
        _run(['codesign', '--force', '--sign', identity, '--timestamp=none', '--entitlements', str(entitlement_path), str(app)])
        _run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)])
        temporary_ipa = temp / 'signed.ipa'
        with zipfile.ZipFile(temporary_ipa, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for relative, path in sorted(_tree(app).items()):
                archive.write(path, f'Payload/{app.name}/{relative}')
        if output_ipa.exists() or output_ipa.is_symlink():
            raise FileExistsError('Output IPA appeared while signing')
        temporary_ipa.rename(output_ipa)
    return {'signed': True, 'device_tested': False, 'phone_ready': False,
            'ipa_path': str(output_ipa), 'ipa_bytes': output_ipa.stat().st_size,
            'ipa_sha256': _digest(_read(output_ipa, MAX_TOTAL_BYTES)),
            'team': entitlements['com.apple.developer.team-identifier'], 'bundle_id': BUNDLE_ID}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    assemble = subcommands.add_parser('assemble', help='Verify local provenance and assemble an unsigned app')
    for flag in ('shell', 'guest', 'report', 'rewrite', 'output-app'):
        assemble.add_argument('--' + flag, type=Path, required=True)
    assemble.add_argument('--source-root', type=Path, default=ROOT)
    assemble.add_argument('--expected-shell-sha256', required=True,
                          help='SHA256 from an independently verified build artifact channel, not the IPA manifest')
    sign = subcommands.add_parser('sign', help='Sign locally using an installed identity; no install or launch')
    for flag in ('app', 'profile', 'output-ipa'):
        sign.add_argument('--' + flag, type=Path, required=True)
    sign.add_argument('--identity', required=True, help='Explicit SHA1 of a local codesigning identity')
    sign.add_argument('--udid', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'assemble':
            path = assemble_app(args.shell, args.guest, args.report, args.rewrite, args.output_app,
                                expected_shell_sha256=args.expected_shell_sha256, source_root=args.source_root)
            print(f'Assembled {path}; unsigned, guest not executed')
        else:
            result = sign_app(args.app, args.profile, args.identity, args.udid, args.output_ipa)
            print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, subprocess.CalledProcessError, plistlib.InvalidFileException) as exc:
        print('Local iOS packaging failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
