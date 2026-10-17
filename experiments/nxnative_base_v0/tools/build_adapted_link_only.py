#!/usr/bin/env python3
"""Link-only SDK check of fixed normal-runtime and NVN52 patches.

Reuses the source-bound baseline builder's compiler, SDK, inventory, dependency
and receipt helpers. Patches affect a fresh private copy only. Metadata remains
the original 1.9 profile; GPU/thread/memstat policy is still pending.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import types

sys.dont_write_bytecode = True
COMPONENT = Path(__file__).resolve().parents[1]
BASELINE = 'patched-host-original-1.9-metadata-link-only'
PATCH_TOOL = Path('/usr/bin/patch')
PATCHES = (
    ('patches/normal-entry.patch', ('dm/host/dm.h', 'dm/ios/Sources/main.m')),
    ('patches/base-v0-texture-view.patch', ('dm/gfx/nvn/include/nvn.h', 'dm/gfx/nvn/nvn_obj.h',
                                         'dm/gfx/nvn/hle_nvn.c', 'dm/gfx/nvn/nvn_mtl.m')),
    ('patches/normal-hle-crash.patch', ('dm/host/hle.c', 'dm/host/hle_svc.c',
                                      'dm/host/hle_sdk16.c', 'dm/host/crash.c')),
    ('patches/horizon-address-signal.patch', ('dm/host/hle_thread.c',)),
)
REQUIRED_ADAPTERS = ('tools/build_adapted_link_only.py', 'adapter/normal_policy.c',
                     'adapter/normal_policy.h', 'patches/normal-entry.patch',
                     'patches/base-v0-texture-view.patch', 'adapter/normal_failures.c',
                     'adapter/normal_failures.h', 'patches/normal-hle-crash.patch',
                     'patches/horizon-address-signal.patch')
COPIED_ADAPTERS = ('adapter/link_only_main.c', 'adapter/normal_policy.c', 'adapter/normal_policy.h',
                   'adapter/normal_failures.c', 'adapter/normal_failures.h')
ABI_FILES = tuple('experiments/botw_upstream_host/adapter/base_v0_abi.' + extension
                  for extension in ('c', 'h', 'json'))
STRONG_SYMBOLS = {'_main', '_dm_main', '_nxnative_upstream_ios_main',
                  '_nxnative_normal_getenv', '_nxnative_normal_environment_apply',
                  '_nxnative_normal_failure', '_nxnative_normal_entry_failed',
                  '_nxnative_normal_fail', '_nxnative_normal_signal_failure'} | {
    '_botw_base_v0_nvnTextureView' + name for name in (
        'SetDefaults', 'SetLevels', 'SetLayers', 'SetFormat', 'SetSwizzle',
        'SetDepthStencilMode', 'SetTarget', 'GetLevels', 'GetLayers', 'GetFormat',
        'GetSwizzle', 'GetDepthStencilMode', 'GetTarget', 'Compare')}


def load_python_source(name, path, expected):
    """Bootstrap from bound source bytes, without SourceFileLoader/pyc lookup."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Python source must be a regular file')
    raw = path.read_bytes()
    if expected != {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}:
        raise ValueError('Python source hash differs from lock')
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def load_baseline(component):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate source lock key')
            result[key] = value
        return result
    lock_path = component / 'SOURCE.lock.json'
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError('source lock must be a regular file')
    lock = json.loads(lock_path.read_text(), object_pairs_hook=unique)
    adapters = lock.get('adapter_files', {})
    for name in ('tools/build_link_only.py',) + REQUIRED_ADAPTERS:
        row = adapters.get(name)
        if row is None:
            raise ValueError('adapted build inputs are not frozen: ' + name)
        path = component
        for part in PurePosixPath(name).parts:
            path /= part
            if path.is_symlink():
                raise ValueError('symlinked adapted build input')
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('adapted build input must be regular: ' + name)
        raw = path.read_bytes()
        if row != {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}:
            raise ValueError('adapted build input differs from lock: ' + name)
    base_name = 'tools/build_link_only.py'
    return load_python_source('nxnative_source_bound_baseline_builder', component / base_name, adapters[base_name])


def bounded_jobs(value):
    count = int(value)
    if not 1 <= count <= 8:
        raise argparse.ArgumentTypeError('jobs must be between 1 and 8')
    return count


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--adapters-only', action='store_true', required=True)
    parser.add_argument('--sdk', choices=('iphoneos',), default='iphoneos')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=bounded_jobs, default=4)
    parser.add_argument('--plan-only', action='store_true')
    return parser.parse_args(argv)


def patch_members(path):
    old, new = [], []
    for line in Path(path).read_text().splitlines():
        if line.startswith('--- a/') or line.startswith('+++ b/'):
            name = line[6:].split('\t', 1)[0]
            relative = PurePosixPath(name)
            if (not name or relative.is_absolute() or '..' in relative.parts
                    or str(relative) != name or any(c in name for c in '\0\r\n')):
                raise ValueError('unsafe patch member')
            (old if line.startswith('--- ') else new).append(name)
        elif line.startswith(('--- ', '+++ ')):
            raise ValueError('patch must modify existing a/ and b/ members')
    if not old or old != new or len(set(old)) != len(old):
        raise ValueError('patch member pairs differ or repeat')
    return set(old)


def private_snapshot(root, base):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('private inputs must be a real directory')
    result = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            if not stat.S_ISDIR((Path(directory) / name).lstat().st_mode):
                raise ValueError('nonregular private input directory')
        for name in files:
            relative = str((Path(directory) / name).relative_to(root))
            result[relative] = base.file_record(base.canonical_file(root, relative))
    return dict(sorted(result.items()))


def verify_private(root, expected, base):
    actual = private_snapshot(root, base)
    if actual != expected:
        raise ValueError('private input file set or content changed')
    return actual


def apply_patch(upstream, patch, allowed, output, label, base, receipt):
    if patch_members(patch) != set(allowed):
        raise ValueError('patch modifies undeclared members')
    before = private_snapshot(upstream, base)
    if not set(allowed) <= set(before):
        raise ValueError('patch member is absent from copied upstream')
    step = base.run_command([PATCH_TOOL, '--fuzz=0', '--batch', '--forward',
                             '--no-backup-if-mismatch', '--reject-file=-', '-p1', '-i', patch],
                            output, label, upstream)
    receipt['steps'].append(step)
    if step['exit_code']:
        raise ValueError('fixed patch failed: ' + str(patch))
    after = private_snapshot(upstream, base)
    changed = {name for name in before if before[name] != after.get(name)}
    if set(before) != set(after) or changed != set(allowed):
        raise ValueError('patch changed an undeclared member or missed a declared member')
    return {'path': str(patch), **base.file_record(patch), 'changed_members': sorted(changed),
            'preimages': {name: before[name] for name in sorted(changed)},
            'postimages': {name: after[name] for name in sorted(changed)}}


def copy_private_inputs(component, output, lock, base, receipt):
    """Copy bound bytes, apply only the four fixed patches, and bind all postimages."""
    private = output / 'inputs'
    private.mkdir()
    upstream = private / 'upstream'
    upstream.mkdir()
    original = base.snapshot_files(component / 'upstream', lock['upstream_files'])
    for name in original:
        source = base.canonical_file(component / 'upstream', name)
        destination = upstream / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    base.snapshot_files(upstream, original)
    receipt['patch_tool'] = {'path': str(PATCH_TOOL), **base.file_record(PATCH_TOOL)}
    changed = set()
    receipt['patches'] = []
    for index, (name, allowed) in enumerate(PATCHES):
        if changed & set(allowed):
            raise ValueError('fixed patches overlap')
        changed.update(allowed)
        receipt['patches'].append(apply_patch(upstream, component / name, allowed,
                                              output, 'patch-%02d' % index, base, receipt))
    for name in COPIED_ADAPTERS:
        base.snapshot_files(component, {name: lock['adapter_files'][name]})
        destination = private / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base.canonical_file(component, name), destination, follow_symlinks=False)
    for name in ABI_FILES:
        row = lock.get('external_code_files', {}).get(name)
        if row is None:
            raise ValueError('reused ABI input is not frozen: ' + name)
        base.snapshot_files(component.parents[1], {name: row})
        destination = private / 'external_abi' / Path(name).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base.canonical_file(component.parents[1], name), destination, follow_symlinks=False)
    snapshot = private_snapshot(private, base)
    expected = {'upstream/' + name: row for name, row in original.items()}
    for patch in receipt['patches']:
        expected.update({'upstream/' + name: row for name, row in patch['postimages'].items()})
    expected.update({name: lock['adapter_files'][name] for name in COPIED_ADAPTERS})
    expected.update({'external_abi/' + Path(name).name: lock['external_code_files'][name] for name in ABI_FILES})
    if snapshot != expected:
        raise ValueError('copied input closure differs from locked originals and patch postimages')
    return private, snapshot


def compile_commands(private, output, units, sdk, base):
    commands = base.compile_commands(private, output, units, sdk)
    template = commands[-1]
    for index, (name, role) in enumerate((('adapter/normal_policy.c', 'owned_normal_policy'),
                                         ('adapter/normal_failures.c', 'owned_normal_failures'),
                                         ('external_abi/base_v0_abi.c', 'reused_base_v0_abi')), start=127):
        source = private / name
        obj = output / 'objects' / ('%03d-' % index + source.name + '.o')
        dep = obj.with_suffix('.d')
        argv = list(template['argv'])
        for flag, value in (('-c', source), ('-o', obj), ('-MF', dep), ('-MT', obj)):
            argv[argv.index(flag) + 1] = str(value)
        commands.append({'source': str(source), 'role': role, 'object': str(obj), 'depfile': str(dep), 'argv': argv})
    for command in commands:
        index = command['argv'].index('-c')
        command['argv'][index:index] = ['-DNXNATIVE_NORMAL_POLICY=1', '-DDM_SHELL=1', '-I', str(private / 'adapter'),
                                         '-I', str(private / 'external_abi')]
    if len(commands) != 130 or len({row['source'] for row in commands}) != 130:
        raise ValueError('adapted host must compile exactly 130 distinct units')
    return commands


def new_receipt(base=None):
    receipt = base.new_receipt() if base else {'steps': [], 'compiles': [], 'status': 'not-started'}
    receipt.update({'schema': 'nxnative-adapted-ios-host-link-only-receipt-v1', 'baseline': BASELINE,
                    'base_v0_configured': False, 'installable': False, 'installable_candidate': False,
                    'host_guest_gpu_executed': False, 'gameplay_verified': False, 'game_payload_embedded': False,
                    'whole_host_policy_closed': False, 'policy_scope_pending': ['GPU', 'thread', 'memstat'],
                    'compile_defines': ['NXNATIVE_NORMAL_POLICY=1', 'DM_SHELL=1'],
                    'compile_link_succeeded': False, 'upstream_build_scripts_executed': False})
    return receipt


def compile_one(item, output, private, known, sdk, base):
    index, command = item
    row = {**command, 'compile': base.run_command(command['argv'], output, 'compile-%03d' % index, private)}
    try:
        if not row['compile']['exit_code']:
            obj = Path(command['object'])
            row['object_record'] = base.file_record(obj)
            depfile = Path(command['depfile'])
            row['depfile_record'] = base.file_record(depfile)
            row['dependencies'] = base.verify_dependencies(base.parse_depfile(depfile), known,
                Path(sdk['sdk_path']), Path(sdk['resource_dir']))
            if str(Path(command['source']).resolve()) not in {r['path'] for r in row['dependencies']['bound_inputs']}:
                raise ValueError('source missing from actual compiler dependency closure')
            row['undefined_symbols'] = base.run_command([sdk['nm'], '-u', obj], output, 'undefined-%03d' % index, private)
    except (OSError, ValueError, KeyError, TypeError) as error:
        row['verification_error'] = str(error)
    return row


def build(args, component=COMPONENT):
    output = args.output.absolute()
    if (output.exists() or output.is_symlink() or any(c in str(output) for c in '\0\n\r:')
            or output.resolve() == component.resolve() or component.resolve() in output.resolve().parents):
        raise ValueError('output must be a fresh directory outside the component')
    output.mkdir(parents=True)
    output = output.resolve()
    receipt = new_receipt()
    base = before = private = private_before = artifact = sdk = resources_before = None
    try:
        base = load_baseline(component)
        receipt = new_receipt(base)
        before, lock = base.source_gate(component)
        receipt['original_source_before'] = before
        units = base.validate_inventory(base.read_json(component / 'compile_sources.json'), lock)
        private, private_before = copy_private_inputs(component, output, lock, base, receipt)
        receipt['private_inputs_before'] = private_before
        receipt['unit_counts'] = {'upstream': 126, 'vpx_decoder': 71, 'owned_entry': 1,
                                  'normal_policy': 1, 'normal_failures': 1, 'reused_base_v0_abi': 1, 'total': 130}
        sdk = base.query_toolchain(output, component, receipt)
        receipt['toolchain'] = sdk
        resources_before = base.resource_snapshot(Path(sdk['resource_dir']) / 'include')
        receipt['external_toolchain_headers_before'] = resources_before
        receipt['metadata_header'] = base.generate_metadata_header(private, output, lock)
        receipt['original_profile_header_entry_executed'] = True
        known = {str((private / name).resolve()): row for name, row in private_before.items()}
        header = receipt['metadata_header']
        known[header['path']] = {key: header[key] for key in ('bytes', 'sha256')}
        commands = compile_commands(private, output, units, sdk, base)
        for command in commands:
            command['source_record'] = known[str(Path(command['source']).resolve())]
        receipt['planned_compiles'] = commands
        argv, old_artifact = base.link_command(output, [row['object'] for row in commands], sdk)
        artifact = output / 'nxnative-adapted-original-1.9-LINK-ONLY-DO-NOT-RUN'
        argv[argv.index('-o') + 1] = str(artifact)
        receipt['planned_link'] = argv
        if args.plan_only:
            receipt['status'] = 'plan-only-not-compiled'
        else:
            (output / 'objects').mkdir()
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for row in pool.map(lambda item: compile_one(item, output, private, known, sdk, base), enumerate(commands)):
                    receipt['compiles'].append(row)
            if any(row['compile']['exit_code'] or row.get('verification_error')
                   or row.get('undefined_symbols', {}).get('exit_code', 1) for row in receipt['compiles']):
                raise ValueError('one or more adapted translation units failed; link was not attempted')
            objects = {row['object']: base.file_record(row['object']) for row in receipt['compiles']}
            if any(objects[row['object']] != row['object_record'] for row in receipt['compiles']):
                raise ValueError('compiled object changed before link')
            receipt['objects_before_link'] = objects
            receipt['link'] = base.run_command(argv, output, 'link', private)
            if receipt['link']['exit_code']:
                raise ValueError('adapted complete strong link failed')
            artifact.chmod(0o444)
            receipt['artifact'] = {'path': str(artifact), **base.file_record(artifact), 'mode': '0444', 'executed': False}
            step = base.run_command([sdk['nm'], '-gU', artifact], output, 'final-strong-symbols', private)
            receipt['final_strong_symbols'] = step
            if step['exit_code']:
                raise ValueError('adapted strong symbol inspection failed')
            symbols = {fields[-1] for line in Path(step['stdout']['path']).read_text().splitlines()
                       if len(fields := line.split()) >= 3 and fields[-2] == 'T'}
            receipt['required_strong_symbols'] = sorted(STRONG_SYMBOLS)
            if not STRONG_SYMBOLS <= symbols:
                raise ValueError('adapted strong symbols absent: ' + ', '.join(sorted(STRONG_SYMBOLS - symbols)))
            receipt['objects_after_link'] = {name: base.file_record(name) for name in objects}
            if receipt['objects_after_link'] != objects:
                raise ValueError('adapted objects changed during link')
            receipt['link_map'] = {'path': str(output / 'link.map'), **base.file_record(output / 'link.map')}
            receipt['status'] = 'compiled-and-strongly-linked-without-execution'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        receipt['status'] = 'failed'
        receipt['error'] = str(error)
    finally:
        if artifact and artifact.exists() and not artifact.is_symlink():
            artifact.chmod(0o444)
        if base and before is not None:
            try:
                after, _ = base.source_gate(component)
                receipt['original_source_after'] = after
                if after != before:
                    raise ValueError('original source closure changed')
                receipt['original_source_unchanged'] = True
                if private_before is not None:
                    receipt['private_inputs_after'] = verify_private(private, private_before, base)
                    receipt['private_inputs_unchanged'] = True
                if 'metadata_header' in receipt:
                    header = receipt['metadata_header']
                    if base.file_record(header['path']) != {key: header[key] for key in ('bytes', 'sha256')}:
                        raise ValueError('original metadata header changed')
                if sdk is not None:
                    for name, expected in ((sdk['clang'], sdk['clang_identity']), (sdk['nm'], sdk['nm_identity']),
                            (sdk['sdk_settings']['path'], {k: sdk['sdk_settings'][k] for k in ('bytes', 'sha256')})):
                        if base.file_record(name) != expected:
                            raise ValueError('toolchain identity changed')
                if resources_before is not None:
                    receipt['external_toolchain_headers_after'] = base.resource_snapshot(Path(sdk['resource_dir']) / 'include')
                    if receipt['external_toolchain_headers_after'] != resources_before:
                        raise ValueError('toolchain builtin headers changed')
                if 'patch_tool' in receipt and base.file_record(PATCH_TOOL) != {k: receipt['patch_tool'][k] for k in ('bytes', 'sha256')}:
                    raise ValueError('system patch tool changed')
            except (OSError, ValueError, KeyError, TypeError) as error:
                receipt['status'] = 'failed'
                receipt['closure_after_error'] = str(error)
        receipt['sdk_system_inputs_excluded_by_mmd'] = True
        receipt['dependency_scope'] = 'Actual -MMD dependencies bind every private source/header; clang builtin headers and SDKSettings/compiler versions are separately bound.'
        receipt['compile_link_succeeded'] = receipt['status'] == 'compiled-and-strongly-linked-without-execution'
        (output / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


def main(argv=None):
    args = parse_args(argv)
    try:
        receipt = build(args)
    except (OSError, ValueError) as error:
        print('adapted link-only preparation failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps({'status': receipt['status'], 'receipt': str(args.output.resolve() / 'receipt.json'),
                      'baseline': BASELINE, 'compile_link_succeeded': receipt['compile_link_succeeded'],
                      'base_v0_configured': False, 'installable': False, 'host_guest_gpu_executed': False}, sort_keys=True))
    return 1 if receipt['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
