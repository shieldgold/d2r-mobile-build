#!/usr/bin/env python3
"""Compile/link the complete pinned iPhoneOS host, without executing it.

The one admitted upstream Python entry is game.header(game.load(TITLE_ID)).
It generates the original 1.9 metadata header, never a base-v0 runtime profile.
No upstream build/generator script, guest, shader, IPA or device tool is run.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
COMPONENT = Path(__file__).resolve().parents[1]
COMMIT = '65fa9caab486768f70da98c51c2421aff76f676d'
TREE = '5b1545c089970a2aca0325aa31fa71bce217813f'
TITLE_ID = '01007EF00011E000'
TARGET = 'arm64-apple-ios26.0'
BASELINE = 'original-1.9-metadata-baseline-compile-only'
FRAMEWORKS = ('UIKit', 'Foundation', 'QuartzCore', 'Metal', 'AudioToolbox',
              'AVFAudio', 'ImageIO', 'UniformTypeIdentifiers', 'GameController',
              'CoreText', 'CoreFoundation', 'CoreGraphics')
REQUIRED_ADAPTERS = ('tools/verify_source.py', 'tools/build_link_only.py',
                     'compile_sources.json', 'adapter/link_only_main.c')


def file_record(path):
    raw = Path(path).read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def canonical_file(root, name):
    if not isinstance(name, str) or not name or any(c in name for c in '\0\n\r'):
        raise ValueError('invalid input path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or str(relative) != name:
        raise ValueError('noncanonical input path')
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('input root must be a real directory')
    path = root
    for i, part in enumerate(relative.parts):
        path = path / part
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            raise ValueError('missing input: ' + name) from error
        expected = stat.S_ISREG if i == len(relative.parts) - 1 else stat.S_ISDIR
        if not expected(mode):
            raise ValueError('nonregular input: ' + name)
    return path


def snapshot_files(root, expected):
    if not isinstance(expected, dict):
        raise ValueError('invalid input inventory')
    result = {}
    for name, row in sorted(expected.items()):
        if (not isinstance(row, dict) or type(row.get('bytes')) is not int
                or not 0 <= row['bytes'] <= 64 * 1024**2
                or not isinstance(row.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('invalid input hash record')
        path = canonical_file(root, name)
        if path.stat().st_size != row['bytes']:
            raise ValueError('input size differs: ' + name)
        actual = file_record(path)
        if actual != {key: row[key] for key in ('bytes', 'sha256')}:
            raise ValueError('input differs: ' + name)
        result[name] = actual
    return result


def read_json(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate JSON key: ' + key)
            out[key] = value
        return out
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('JSON input must be a regular file')
    return json.loads(path.read_text(), object_pairs_hook=unique)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # The source was hash-bound by the caller; never consume an unlisted .pyc.
    exec(compile(Path(path).read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def source_gate(component):
    """Bind the verifier before importing it, then verify the independent Git tree."""
    lock_path = component / 'SOURCE.lock.json'
    lock = read_json(lock_path)
    if (lock.get('schema') != 'nxnative-base-v0-source-v1'
            or lock.get('upstream_repository') != 'https://github.com/seedds/nxnative'
            or lock.get('upstream_commit') != COMMIT or lock.get('upstream_tree') != TREE):
        raise ValueError('unexpected source identity')
    adapters = lock.get('adapter_files', {})
    if not all(name in adapters for name in REQUIRED_ADAPTERS):
        raise ValueError('link-only adapter inventory is not frozen in SOURCE.lock.json')
    adapter_snapshot = snapshot_files(component, adapters)
    external = snapshot_files(component.parents[1], lock.get('external_code_files', {}))
    verifier = load_module('nxnative_locked_source_verifier', component / 'tools/verify_source.py')
    verification = verifier.verify_component(component)
    return {'verification': verification, 'lock': file_record(lock_path),
            'upstream': snapshot_files(component / 'upstream', lock['upstream_files']),
            'adapters': adapter_snapshot, 'external_code_files': external}, lock


def expected_units(lock):
    """Owned static expansion of the frozen Makefile/gen_build exclusion rules."""
    names = set(lock['upstream_files'])
    host = ['main.c', 'loader.c', 'hle.c', 'host_stubs.c', 'crash.c', 'probe.c',
            'thread.c', 'memstat.m']
    host = ['dm/host/' + name for name in host]
    host += sorted(n for n in names if PurePosixPath(n).parent == PurePosixPath('dm/host')
                   and PurePosixPath(n).match('hle_*.c'))
    host += ['dm/host/entry.S', 'dm/host/dm_i18n.c']
    gfx = sorted(n for n in names if
                 (PurePosixPath(n).parent == PurePosixPath('dm/gfx') and n.endswith('.c')) or
                 (PurePosixPath(n).parent == PurePosixPath('dm/gfx/nvn') and n.endswith(('.c', '.m', '.S'))) or
                 (PurePosixPath(n).parent == PurePosixPath('dm/gfx/tex') and n.endswith(('.c', '.m'))))
    vpx = sorted(n for n in names if n.startswith('dm/third_party/libvpx/') and n.endswith('.c'))
    ios = ['dm/ios/Sources/' + n for n in ('DmGamepad.m', 'dm_touch_controls.c', 'front_ios.m', 'main.m')]
    return [(n, role) for group, role in ((host, 'host'), (gfx, 'gfx'), (vpx, 'vpx_decoder'), (ios, 'ios_app'))
            for n in group]


def validate_inventory(manifest, lock):
    if (manifest.get('schema') != 'nxnative-original-ios-host-link-only-inventory-v1'
            or manifest.get('upstream_commit') != COMMIT or manifest.get('upstream_tree') != TREE
            or manifest.get('baseline') != BASELINE or manifest.get('target') != TARGET
            or manifest.get('isa') != 'armv8.2-a' or manifest.get('upstream_unit_count') != 126
            or manifest.get('libvpx_unit_count') != 71):
        raise ValueError('unexpected compile inventory identity')
    units = manifest.get('translation_units')
    expected = expected_units(lock)
    if (not isinstance(units, list) or len(units) != 126 or len(expected) != 126
            or [(u.get('path'), u.get('role')) for u in units] != expected
            or len({u['path'] for u in units}) != 126
            or sum(u['role'] == 'vpx_decoder' for u in units) != 71):
        raise ValueError('complete original 126-unit inventory differs')
    renames = {'dm/host/main.c': ['main=dm_main'],
               'dm/ios/Sources/main.m': ['main=nxnative_upstream_ios_main']}
    for unit in units:
        row = lock['upstream_files'][unit['path']]
        if (unit.get('defines') != renames.get(unit['path'], [])
                or any(unit.get(k) != row[k] for k in ('bytes', 'sha256'))):
            raise ValueError('compile input differs from locked source: ' + unit['path'])
    return units


def bounded_jobs(value):
    count = int(value)
    if not 1 <= count <= 8:
        raise argparse.ArgumentTypeError('jobs must be between 1 and 8')
    return count


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--baseline-only', action='store_true', required=True,
                        help='required acknowledgement: original 1.9 metadata compile baseline only')
    parser.add_argument('--sdk', choices=('iphoneos',), default='iphoneos')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=bounded_jobs, default=4)
    parser.add_argument('--plan-only', action='store_true', help='write commands without compiling/linking')
    return parser.parse_args(argv)


def prepare_output(output, component):
    output = Path(output)
    if any(c in str(output) for c in '\0\n\r:'):
        raise ValueError('unsupported output path')
    if output.is_symlink() or output.exists():
        raise ValueError('output must be a new directory')
    resolved = output.resolve()
    if resolved == component.resolve() or component.resolve() in resolved.parents:
        raise ValueError('output must be outside source component')
    output.mkdir(parents=True, exist_ok=False)


def command_environment():
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C',
           'PYTHONDONTWRITEBYTECODE': '1'}
    for name in ('DEVELOPER_DIR', 'HOME', 'TMPDIR'):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def run_command(argv, output, label, cwd):
    log_dir = Path(output) / 'logs'
    log_dir.mkdir(exist_ok=True)
    stdout = log_dir / (label + '.stdout')
    stderr = log_dir / (label + '.stderr')
    start = time.monotonic()
    with stdout.open('wb') as out, stderr.open('wb') as err:
        try:
            completed = subprocess.run([str(a) for a in argv], cwd=cwd,
                                       env=command_environment(), stdout=out, stderr=err, check=False)
            code = completed.returncode
        except OSError as error:
            err.write(str(error).encode())
            code = 127
    return {'argv': [str(a) for a in argv], 'cwd': str(cwd), 'exit_code': code,
            'duration_seconds': round(time.monotonic() - start, 6),
            'stdout': {'path': str(stdout), **file_record(stdout)},
            'stderr': {'path': str(stderr), **file_record(stderr)}}


def query_toolchain(output, component, receipt):
    def query(argv, label):
        step = run_command(argv, output, label, component)
        receipt['steps'].append(step)
        if step['exit_code']:
            raise ValueError('toolchain query failed: ' + label)
        return Path(step['stdout']['path']).read_text().strip()
    sdk_path = Path(query(['/usr/bin/xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], 'sdk-path')).resolve()
    version = query(['/usr/bin/xcrun', '--sdk', 'iphoneos', '--show-sdk-version'], 'sdk-version')
    if not re.fullmatch(r'\d+(?:\.\d+)*', version) or int(version.split('.')[0]) < 26:
        raise ValueError('iPhoneOS SDK 26 or later is required for the original Metal 4 backend')
    clang = Path(query(['/usr/bin/xcrun', '--sdk', 'iphoneos', '--find', 'clang'], 'find-clang')).resolve()
    nm = Path(query(['/usr/bin/xcrun', '--sdk', 'iphoneos', '--find', 'nm'], 'find-nm')).resolve()
    compiler_version = query([clang, '--version'], 'clang-version')
    resource = Path(query([clang, '-print-resource-dir'], 'clang-resource-dir')).resolve()
    settings = sdk_path / 'SDKSettings.json'
    return {'sdk': 'iphoneos', 'sdk_path': str(sdk_path), 'sdk_version': version,
            'sdk_settings': {'path': str(settings), **file_record(settings)},
            'clang': str(clang), 'clang_identity': file_record(clang),
            'nm': str(nm), 'nm_identity': file_record(nm),
            'compiler_version': compiler_version, 'resource_dir': str(resource)}


def generate_metadata_header(component, output, lock):
    # source_gate has verified the complete independent upstream Git tree.
    generator = canonical_file(component / 'upstream', 'dm/games/game.py')
    profile_name = 'dm/games/' + TITLE_ID + '/game.tsv'
    snapshot_files(component / 'upstream', {name: lock['upstream_files'][name]
                   for name in ('dm/games/game.py', profile_name)})
    game = load_module('nxnative_locked_original_profile_header', generator)
    header = game.header(game.load(TITLE_ID))
    generated = output / 'generated'
    generated.mkdir()
    path = generated / 'game_profile.h'
    path.write_text(header)
    return {'path': str(path), **file_record(path), 'generator': 'dm/games/game.py',
            'entry': "header(load('01007EF00011E000'))", 'baseline': BASELINE,
            'base_v0_configured': False, 'original_profile': profile_name,
            'original_profile_sha256': lock['upstream_files'][profile_name]['sha256']}


def compile_commands(component, output, units, sdk):
    upstream = component / 'upstream'
    includes = [upstream / 'dm/gfx/nvn/include', output / 'generated',
                upstream / ('dm/games/' + TITLE_ID), upstream / 'dm/third_party/libvpx',
                upstream / 'dm/third_party/stb_vorbis', upstream / 'dm/third_party/libvpx/config']
    for unit in units:
        directory = (upstream / unit['path']).parent
        if directory not in includes:
            includes.append(directory)
    all_units = [{'source': upstream / u['path'], **u} for u in units]
    all_units.append({'source': component / 'adapter/link_only_main.c',
                      'path': 'adapter/link_only_main.c', 'role': 'owned_link_only_entry', 'defines': []})
    commands = []
    for index, unit in enumerate(all_units):
        obj = output / 'objects' / (str(index).zfill(3) + '-' + Path(unit['path']).name + '.o')
        depfile = obj.with_suffix('.d')
        argv = [sdk['clang'], '--no-default-config', '-target', TARGET, '-isysroot', sdk['sdk_path'],
                '-march=armv8.2-a', '-std=gnu11', '-O2' if unit['role'] == 'vpx_decoder' else '-O1',
                '-g', '-Wall', '-Wno-unused-function', '-fno-modules', '-fno-implicit-modules',
                '-MMD', '-MF', str(depfile), '-MT', str(obj)]
        if unit['source'].suffix == '.m':
            argv.append('-fobjc-arc')
        for directory in includes:
            argv.extend(['-I', str(directory)])
        argv += ['-D' + define for define in unit['defines']]
        argv += ['-c', str(unit['source']), '-o', str(obj)]
        commands.append({'source': str(unit['source']), 'role': unit['role'],
                         'object': str(obj), 'depfile': str(depfile), 'argv': argv})
    return commands


def link_command(output, objects, sdk):
    artifact = output / 'nxnative-original-1.9-LINK-ONLY-DO-NOT-RUN'
    argv = [sdk['clang'], '--no-default-config', '-target', TARGET, '-isysroot', sdk['sdk_path'],
            '-march=armv8.2-a', '-Wl,-undefined,error', '-Wl,-e,_main',
            '-Wl,-map,' + str(output / 'link.map'), '-Wl,-t', '-o', str(artifact)]
    argv += [str(obj) for obj in objects]
    for framework in FRAMEWORKS:
        argv += ['-framework', framework]
    return argv, artifact


def parse_depfile(path):
    raw = Path(path).read_text().replace('\\\n', '')
    target, separator, deps = raw.partition(':')
    if not separator or not target.strip() or not deps.strip():
        raise ValueError('empty or malformed compiler depfile')
    return list(dict.fromkeys(Path(name) for name in shlex.split(deps)))


def verify_dependencies(deps, known, sdk_root, resource_root):
    result = {'bound_inputs': [], 'sdk_inputs': [], 'external_toolchain_inputs': []}
    for dep in deps:
        if not dep.is_absolute():
            raise ValueError('compiler dependency must be absolute')
        path = dep.resolve(strict=True)
        actual = file_record(path)
        row = {'path': str(path), **actual}
        if str(path) in known:
            if known[str(path)] != actual:
                raise ValueError('compiler dependency changed: ' + str(path))
            result['bound_inputs'].append(row)
        elif sdk_root.resolve() in path.parents:
            result['sdk_inputs'].append(row)
        elif resource_root.resolve() in path.parents:
            result['external_toolchain_inputs'].append(row)
        else:
            raise ValueError('unlocked compiler dependency: ' + str(path))
    return result


def resource_snapshot(directory):
    return {str(p.resolve()): file_record(p) for p in sorted(directory.rglob('*')) if p.is_file()}


def new_receipt():
    return {'schema': 'nxnative-original-ios-full-host-link-only-receipt-v1',
            'upstream_commit': COMMIT, 'upstream_tree': TREE, 'baseline': BASELINE,
            'target': TARGET, 'isa': 'armv8.2-a', 'base_v0_configured': False,
            'host_guest_gpu_executed': False, 'installable': False,
            'installable_candidate': False, 'gameplay_verified': False,
            'game_payload_embedded': False, 'upstream_build_scripts_executed': False,
            'original_profile_header_entry_executed': False, 'compile_link_succeeded': False,
            'steps': [], 'compiles': [], 'status': 'not-started'}


def build(args, component=COMPONENT):
    prepare_output(args.output.absolute(), component)
    output = args.output.resolve()
    receipt = new_receipt()
    before = None
    artifact = None
    try:
        before, lock = source_gate(component)
        receipt['source_before'] = before
        manifest = read_json(component / 'compile_sources.json')
        units = validate_inventory(manifest, lock)
        receipt['unit_counts'] = {'upstream': len(units), 'vpx_decoder': 71, 'owned_entry': 1, 'total': 127}
        sdk = query_toolchain(output, component, receipt)
        receipt['toolchain'] = sdk
        resources_before = resource_snapshot(Path(sdk['resource_dir']) / 'include')
        receipt['external_toolchain_headers_before'] = resources_before
        receipt['metadata_header'] = generate_metadata_header(component, output, lock)
        receipt['original_profile_header_entry_executed'] = True
        commands = compile_commands(component, output, units, sdk)
        argv, artifact = link_command(output, [row['object'] for row in commands], sdk)
        receipt['planned_compiles'] = commands
        receipt['planned_link'] = argv
        if args.plan_only:
            receipt['status'] = 'plan-only-not-compiled'
        else:
            (output / 'objects').mkdir()
            known = {str((component / 'upstream' / n).resolve()): row for n, row in before['upstream'].items()}
            known.update({str((component / n).resolve()): row for n, row in before['adapters'].items()})
            known[receipt['metadata_header']['path']] = {k: receipt['metadata_header'][k] for k in ('bytes', 'sha256')}

            def compile_one(item):
                index, command = item
                row = {**command, 'compile': run_command(command['argv'], output, 'compile-%03d' % index, component)}
                try:
                    if not row['compile']['exit_code']:
                        obj = Path(command['object'])
                        row['object_record'] = file_record(obj)
                        depfile = Path(command['depfile'])
                        row['depfile_record'] = file_record(depfile)
                        row['dependencies'] = verify_dependencies(parse_depfile(depfile), known,
                            Path(sdk['sdk_path']), Path(sdk['resource_dir']))
                        if str(Path(command['source']).resolve()) not in {r['path'] for r in row['dependencies']['bound_inputs']}:
                            raise ValueError('source missing from compiler depfile: ' + command['source'])
                        row['undefined_symbols'] = run_command([sdk['nm'], '-u', obj], output,
                                                              'undefined-%03d' % index, component)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    row['verification_error'] = str(error)
                return row

            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for row in pool.map(compile_one, enumerate(commands)):
                    receipt['compiles'].append(row)
            if any(row['compile']['exit_code'] or row.get('verification_error')
                   or row.get('undefined_symbols', {}).get('exit_code', 1)
                   for row in receipt['compiles']):
                raise ValueError('one or more original translation units failed; link was not attempted')
            receipt['objects_before_link'] = {row['object']: file_record(row['object']) for row in receipt['compiles']}
            if any(receipt['objects_before_link'][row['object']] != row['object_record'] for row in receipt['compiles']):
                raise ValueError('compiled object changed before link')
            receipt['link'] = run_command(argv, output, 'link', component)
            if receipt['link']['exit_code']:
                raise ValueError('complete strong link failed')
            # Revoke execution immediately, before any inspection tool is invoked.
            artifact.chmod(0o444)
            receipt['artifact'] = {'path': str(artifact), **file_record(artifact), 'mode': '0444', 'executed': False}
            receipt['final_strong_symbols'] = run_command([sdk['nm'], '-gU', artifact], output, 'final-strong-symbols', component)
            symbols_step = receipt['final_strong_symbols']
            if symbols_step['exit_code']:
                raise ValueError('final strong symbol inspection failed')
            symbols = {fields[-1] for line in Path(symbols_step['stdout']['path']).read_text().splitlines()
                       if len(fields := line.split()) >= 3 and fields[-2] == 'T'}
            if not {'_main', '_dm_main', '_nxnative_upstream_ios_main'} <= symbols:
                raise ValueError('original entry or owned strong main is absent from final link')
            receipt['link_map'] = {'path': str(output / 'link.map'), **file_record(output / 'link.map')}
            receipt['objects_after_link'] = {row['object']: file_record(row['object']) for row in receipt['compiles']}
            if receipt['objects_after_link'] != receipt['objects_before_link']:
                raise ValueError('compiled objects changed during link')
            receipt['status'] = 'compiled-and-strongly-linked-without-execution'
        receipt['external_toolchain_headers_after'] = resource_snapshot(Path(sdk['resource_dir']) / 'include')
        if receipt['external_toolchain_headers_after'] != resources_before:
            raise ValueError('toolchain builtin headers changed during build')
        if file_record(receipt['metadata_header']['path']) != {k: receipt['metadata_header'][k] for k in ('bytes', 'sha256')}:
            raise ValueError('generated original metadata header changed during build')
        for name, expected in ((sdk['clang'], sdk['clang_identity']), (sdk['nm'], sdk['nm_identity']),
                               (sdk['sdk_settings']['path'], {k: sdk['sdk_settings'][k] for k in ('bytes', 'sha256')})):
            if file_record(name) != expected:
                raise ValueError('toolchain identity changed during build')
        receipt['sdk_system_inputs_excluded_by_mmd'] = True
        receipt['dependency_scope'] = 'Actual -MMD user dependencies plus complete clang builtin-header snapshot; SDK identity binds SDKSettings.json and compiler/sdk version, not every SDK file.'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        receipt['status'] = 'failed'
        receipt['error'] = str(error)
    finally:
        if artifact and artifact.exists() and not artifact.is_symlink():
            artifact.chmod(0o444)
        if before is not None:
            try:
                after, _ = source_gate(component)
                receipt['source_after'] = after
                if after != before:
                    raise ValueError('source snapshot changed during build')
                receipt['source_unchanged'] = True
            except (OSError, ValueError, KeyError, TypeError) as error:
                receipt['source_unchanged'] = False
                receipt['status'] = 'failed'
                receipt['source_after_error'] = str(error)
        receipt['compile_link_succeeded'] = receipt['status'] == 'compiled-and-strongly-linked-without-execution'
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


def main(argv=None):
    args = parse_args(argv)
    try:
        receipt = build(args)
    except (OSError, ValueError) as error:
        print('link-only preparation failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps({'status': receipt['status'], 'receipt': str(args.output.resolve() / 'receipt.json'),
                      'compile_link_succeeded': receipt['compile_link_succeeded'], 'installable': False,
                      'base_v0_configured': False, 'host_guest_gpu_executed': False}, sort_keys=True))
    return 1 if receipt['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
