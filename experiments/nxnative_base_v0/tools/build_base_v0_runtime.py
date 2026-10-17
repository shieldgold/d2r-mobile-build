#!/usr/bin/env python3
"""Build the pinned base-v0 host with the actual UIKit entry, without running it.

This host-only tool reuses the admitted complete compile/dependency helpers.
No game input, packaging, signing, install or execution interface is provided.
The original pure game.header(game.load(...)) consumes our dedicated profile.
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
TITLE_ID = '01007EF00011E000'
MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
PROFILE = 'profile/base_v0_runtime.tsv'
MEMORY_FILES = tuple('experiments/botw_upstream_host/adapter/' + name for name in (
    'base_v0_heap.c', 'base_v0_heap.h', 'whole_address_space.c',
    'whole_address_space.h', 'whole_policy.c', 'whole_policy.h'))
MEMORY_UNITS = ('adapter/base_v0_memory.c', 'memory_provider/base_v0_heap.c',
                'memory_provider/whole_address_space.c', 'memory_provider/whole_policy.c')
EXTRA_PATCHES = (
    ('patches/base-v0-logical-memory.patch', ('dm/host/hle_svc.c', 'dm/host/main.c')),
    ('patches/base-v0-normal-api-contracts.patch', ('dm/host/hle_account.c', 'dm/gfx/hle_vi.c',
        'dm/gfx/nvn/hle_nvn.c', 'dm/gfx/nvn/nvn_mtl.m')),
)
REQUIRED = ('tools/build_base_v0_runtime.py', PROFILE, 'profile/base_v0.json',
    'tools/generate_base_v0_contract.py', 'tools/build_link_only.py',
    'tools/build_adapted_link_only.py', 'tools/verify_source.py', 'compile_sources.json',
    'adapter/base_v0_memory.c', 'adapter/base_v0_memory.h', *(name for name, _ in EXTRA_PATCHES))
DEFINES = ('NXNATIVE_NORMAL_POLICY=1', 'DM_SHELL=1', 'NXNATIVE_BASE_V0_MEMORY=1',
           'NXNATIVE_BASE_V0_NORMAL_APIS=1')


def record(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def regular_bytes(path, limit=64 * 1024**2):
    """Read a bounded regular source, rejecting parent links and FIFO blocking."""
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('source path contains a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise ValueError('source must be a bounded regular file')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError('source changed while reading')
    return raw


def json_bytes(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate source JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def source_module(name, path, expected):
    raw = regular_bytes(path)
    if record(raw) != expected:
        raise ValueError('source differs from frozen lock: ' + str(path))
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def load_helpers(component):
    """Bind every added source before executing even the admitted helpers."""
    lock = json_bytes(regular_bytes(component / 'SOURCE.lock.json', 16 * 1024**2))
    adapters = lock.get('adapter_files', {})
    for name in REQUIRED:
        if name not in adapters:
            raise ValueError('runtime source is not frozen in lock: ' + name)
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or str(relative) != name:
            raise ValueError('noncanonical frozen runtime source')
        if record(regular_bytes(component / name)) != adapters[name]:
            raise ValueError('runtime source differs from frozen lock: ' + name)
    name = 'tools/build_adapted_link_only.py'
    adapted = source_module('nxnative_runtime_bound_adapted', component / name, adapters[name])
    base = adapted.load_baseline(component)
    before, verified_lock = base.source_gate(component)
    if verified_lock != lock:
        raise ValueError('source lock changed during helper bootstrap')
    for name in MEMORY_FILES:
        if name not in lock.get('external_code_files', {}):
            raise ValueError('complete memory provider source is not frozen: ' + name)
    return base, adapted, before, lock


def expected_profile(identity):
    """A complete explicit base0 profile; unsupported driver tables stay -1."""
    if (identity.get('schema') != 'nxnative-base-v0-profile-v1'
            or identity.get('profile_id') != 'botw-switch-base-v0'
            or identity.get('title_id') != TITLE_ID or identity.get('game_version') != '1.0.0'
            or identity.get('package_version') != 'v0'
            or identity.get('required_modules') != list(MODULES)
            or identity.get('nvn_api') != {'major': 52, 'minor': 32}):
        raise ValueError('base-v0 identity contract differs')
    builds = []
    for name in MODULES:
        item = identity['modules'][name]
        build = item.get('build_id', '')
        if (item.get('load') is not True or len(build) != 64
                or any(c not in '0123456789abcdef' for c in build)):
            raise ValueError('base-v0 module identity differs')
        builds.append(name + '=' + build[:32])
    return {
        'title_id': [TITLE_ID], 'program_id': [TITLE_ID],
        'name': ['The Legend of Zelda: Breath of the Wild'],
        'modules': [' '.join(MODULES)], 'romfs_marker': ['Pack/Bootup_Graphics.pack'],
        'build': ['v0', '1.0.0', *builds], 'package': ['v0'],
        'mem_reserve': ['0x140000000'], 'mem_alias': ['0x20000000'],
        'mem_stack': ['0x10000000'], 'app_pool': ['0xc5c00000'],
        'nvn_tex_base': ['0x20'], 'nvn_tex_slots': ['32'],
        'nvn_samp_base': ['-1'], 'nvn_img_base': ['0x120'], 'nvn_img_slots': ['8'],
        'nvn_ssbo_bases': ['vs=-1 ps=-1 gs=-1 hs=-1 ds=-1 cs=-1'],
        'device_api': ['52.32'], 'present_interval': ['game'], 'core_preempt': ['off'],
        'shared_font': ['bfttf'], 'pad_layout': ['full'], 'feedback_snap': [''],
        'alpha_from_red': [''],
    }


def validate_profile(raw, identity):
    rows = {}
    for line in raw.decode('utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        values = line.split('\t')
        if values[0] in rows:
            raise ValueError('duplicate base-v0 profile key')
        rows[values[0]] = values[1:]
    if rows != expected_profile(identity):
        raise ValueError('dedicated base-v0 profile differs from fixed identity and supported slots')
    return rows


def render_header(profile_raw, identity, game_raw, output):
    validate_profile(profile_raw, identity)
    directory = output / 'generated-profile' / TITLE_ID
    directory.mkdir(parents=True, exist_ok=False)
    tsv = directory / 'game.tsv'
    tsv.write_bytes(profile_raw)
    game = types.ModuleType('nxnative_bound_original_game_header')
    game.__file__ = str(directory.parent / 'game.py')
    exec(compile(game_raw, game.__file__, 'exec'), game.__dict__)
    header = game.header(game.load(str(directory)))
    if record(regular_bytes(tsv)) != record(profile_raw):
        raise ValueError('dedicated profile changed during original header generation')
    return header, {'generator_entry': 'header(load(dedicated_base_v0_profile))',
        'generator_source': record(game_raw), 'profile': {'path': str(tsv), **record(profile_raw)},
        'game_version': '1.0.0', 'module_order': list(MODULES), 'nvn_api': '52.32',
        'unsupported_driver_tables': {'sampler_base': -1, 'ssbo_bases': [-1] * 6},
        'original_H': '0xc2600000', 'original_R': '0xc0600000',
        'app_pool_source': 'original H plus fixed six-module 54-MiB aligned image footprint'}


def prepare_output(output, component):
    output = Path(output).absolute()
    if '..' in output.parts or any(c in str(output) for c in '\0\n\r:'):
        raise ValueError('output path must be canonical')
    for path in (output, *output.parents):
        if path.is_symlink():
            raise ValueError('output path contains a symlink')
    source = component.resolve()
    target = output.resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError('output overlaps the component source tree')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    return output.resolve()


def bounded_jobs(value):
    count = int(value)
    if not 1 <= count <= 8:
        raise argparse.ArgumentTypeError('jobs must be between 1 and 8')
    return count


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--host-only', action='store_true', required=True)
    parser.add_argument('--sdk', choices=('iphoneos',), default='iphoneos')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=bounded_jobs, default=4)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stage-only', action='store_true', help='bind/patch sources and generate metadata, without SDK tools')
    mode.add_argument('--plan-only', action='store_true', help='query SDK and record commands without compiling')
    return parser.parse_args(argv)


def copy_inputs(component, output, lock, base, adapted, receipt):
    # Select the reviewed helpers' fixed inputs in this fresh module instance.
    # Their original source and the baseline inventory remain unchanged.
    adapted.PATCHES = (*adapted.PATCHES, *EXTRA_PATCHES)
    adapted.COPIED_ADAPTERS = (*(n for n in adapted.COPIED_ADAPTERS if n != 'adapter/link_only_main.c'),
        'adapter/base_v0_memory.c', 'adapter/base_v0_memory.h', PROFILE)
    private, expected = adapted.copy_private_inputs(component, output, lock, base, receipt)
    for name in MEMORY_FILES:
        row = lock['external_code_files'][name]
        base.snapshot_files(component.parents[1], {name: row})
        relative = 'memory_provider/' + Path(name).name
        destination = private / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base.canonical_file(component.parents[1], name), destination, follow_symlinks=False)
        expected[relative] = row
    adapted.verify_private(private, expected, base)
    return private, expected


def compile_commands(private, output, units, sdk, base, adapted):
    commands = adapted.compile_commands(private, output, units, sdk, base)
    commands = [row for row in commands if Path(row['source']).name != 'link_only_main.c']
    template = next(row for row in commands if row['source'].endswith('/adapter/normal_policy.c'))
    for name in MEMORY_UNITS:
        source = private / name
        argv = list(template['argv'])
        argv[argv.index('-c') + 1] = str(source)
        commands.append({'source': str(source), 'role': 'base_v0_complete_memory', 'argv': argv})
    for index, row in enumerate(commands):
        source = Path(row['source'])
        obj = output / 'objects' / ('%03d-' % index + source.name + '.o')
        dep = obj.with_suffix('.d')
        argv = row['argv']
        if source.as_posix().endswith('/dm/ios/Sources/main.m'):
            argv[:] = [arg for arg in argv if arg != '-Dmain=nxnative_upstream_ios_main']
        for flag, value in (('-o', obj), ('-MF', dep), ('-MT', obj)):
            argv[argv.index(flag) + 1] = str(value)
        insert = [*('-D' + d for d in DEFINES if '-D' + d not in argv),
                  '-I', str(private / 'memory_provider')]
        argv[argv.index('-c'):argv.index('-c')] = insert
        row.update(object=str(obj), depfile=str(dep))
    if len(commands) != 139 or len({row['source'] for row in commands}) != 139:
        raise ValueError('base-v0 UIKit host must compile exactly 139 distinct units')
    return commands


def strong_symbols(adapted):
    return (adapted.STRONG_SYMBOLS - {'_nxnative_upstream_ios_main'}) | {
        '_nxnative_base_v0_memory_' + name for name in ('reserve', 'layout', 'admit', 'record',
        'verify', 'initial_pending', 'guard', 'failure')} | {
        '_botw_base_v0_heap_plan_original_logical', '_botw_whole_address_space_reserve',
        '_nxnative_base_v0_layer_scaling_mode', '_dm_guest_heap_current'}


def verify_symbols(text, required):
    actual = {fields[-1] for line in text.splitlines()
              if len(fields := line.split()) >= 3 and fields[-2] == 'T'}
    if not required <= actual:
        raise ValueError('required runtime strong symbols absent: ' + ', '.join(sorted(required - actual)))
    if '_nxnative_upstream_ios_main' in actual:
        raise ValueError('runtime retained the renamed link-only UIKit entry')
    return actual


def new_receipt(base=None):
    receipt = base.new_receipt() if base else {'steps': [], 'compiles': [], 'status': 'not-started'}
    receipt.update(schema='nxnative-base-v0-UIKit-host-build-v1',
        baseline='base-v0-original-1.0.0-UIKit-host-compile-only',
        metadata_game_version='1.0.0', metadata_nvn_api='52.32', entrypoint='UIKit _main',
        base_v0_configured=False, base_v0_host_metadata_configured=False,
        installable=False, installable_candidate=False, host_guest_gpu_executed=False,
        audio_runtime_closed=False, whole_host_policy_closed=False, gameplay_verified=False,
        game_payload_embedded=False, resources_validated=False,
        compile_defines=list(DEFINES), compile_link_succeeded=False,
        upstream_build_scripts_executed=False,
        pending=['complete frozen base-v0 audren bridge', 'local exact resource/Guest/registry contract',
                 'physical device memory, GPU, audio and gameplay verification'])
    return receipt


def inspect_entry_objects(commands, output, sdk, base, receipt, private):
    receipt['entry_object_symbols'] = []
    for suffix, symbol in (('/dm/ios/Sources/main.m', '_main'), ('/dm/host/main.c', '_dm_main')):
        command = next(row for row in commands if row['source'].endswith(suffix))
        step = base.run_command([sdk['nm'], '-gU', command['object']], output,
                                'entry-' + symbol.lstrip('_'), private)
        receipt['entry_object_symbols'].append(step)
        if step['exit_code']:
            raise ValueError('entry object symbol inspection failed')
        verify_symbols(regular_bytes(step['stdout']['path']).decode(), {symbol})


def build(args, component=COMPONENT):
    output = prepare_output(args.output, component)
    receipt = new_receipt()
    base = adapted = before = private = private_before = artifact = sdk = resources_before = None
    try:
        base, adapted, before, lock = load_helpers(component)
        receipt = new_receipt(base)
        receipt['original_source_before'] = before
        units = base.validate_inventory(base.read_json(component / 'compile_sources.json'), lock)
        private, private_before = copy_inputs(component, output, lock, base, adapted, receipt)
        receipt['private_inputs_before'] = private_before
        identity = json_bytes(regular_bytes(component / 'profile/base_v0.json'))
        header, generated = render_header(regular_bytes(private / PROFILE), identity,
            regular_bytes(private / 'upstream/dm/games/game.py'), output)
        header_path = output / 'generated/game_profile.h'
        header_path.parent.mkdir()
        header_path.write_text(header)
        receipt['metadata_header'] = {'path': str(header_path), **record(header.encode()), **generated}
        receipt['original_profile_header_entry_executed'] = True
        receipt['base_v0_host_metadata_configured'] = True
        receipt['unit_counts'] = {'upstream': 126, 'vpx_decoder': 71, 'UIKit_entry_in_upstream': 1,
            'return_only_entry': 0, 'normal_policy': 2, 'base_v0_abi': 1,
            'shader_provider': 6, 'complete_memory_provider': 4, 'total': 139}
        if args.stage_only:
            receipt['status'] = 'staged-and-profile-generated-without-compilation'
        else:
            sdk = base.query_toolchain(output, component, receipt)
            receipt['toolchain'] = sdk
            resources_before = base.resource_snapshot(Path(sdk['resource_dir']) / 'include')
            receipt['external_toolchain_headers_before'] = resources_before
            known = {str((private / name).resolve()): row for name, row in private_before.items()}
            known[str(header_path)] = record(header.encode())
            commands = compile_commands(private, output, units, sdk, base, adapted)
            for command in commands:
                command['source_record'] = known[str(Path(command['source']).resolve())]
            receipt['planned_compiles'] = commands
            argv, _ = base.link_command(output, [row['object'] for row in commands], sdk)
            artifact = output / 'nxnative-base-v0-UIKit-RUNTIME-DO-NOT-RUN'
            argv[argv.index('-o') + 1] = str(artifact)
            receipt['planned_link'] = argv
            if args.plan_only:
                receipt['status'] = 'plan-only-not-compiled'
            else:
                (output / 'objects').mkdir()
                with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                    for row in pool.map(lambda item: adapted.compile_one(item, output, private, known, sdk, base), enumerate(commands)):
                        receipt['compiles'].append(row)
                if any(row['compile']['exit_code'] or row.get('verification_error')
                       or row.get('undefined_symbols', {}).get('exit_code', 1) for row in receipt['compiles']):
                    raise ValueError('one or more runtime translation units failed; link was not attempted')
                objects = {row['object']: base.file_record(row['object']) for row in receipt['compiles']}
                if any(objects[row['object']] != row['object_record'] for row in receipt['compiles']):
                    raise ValueError('runtime object changed before link')
                receipt['objects_before_link'] = objects
                inspect_entry_objects(commands, output, sdk, base, receipt, private)
                receipt['link'] = base.run_command(argv, output, 'link', private)
                if receipt['link']['exit_code']:
                    raise ValueError('complete runtime strong link failed')
                artifact.chmod(0o444)
                receipt['artifact'] = {'path': str(artifact), **base.file_record(artifact), 'mode': '0444', 'executed': False}
                step = base.run_command([sdk['nm'], '-gU', artifact], output, 'final-strong-symbols', private)
                receipt['final_strong_symbols'] = step
                if step['exit_code']:
                    raise ValueError('runtime final symbol inspection failed')
                required = strong_symbols(adapted)
                verify_symbols(regular_bytes(step['stdout']['path']).decode(), required)
                receipt['required_strong_symbols'] = sorted(required)
                name = 'tools/generate_base_v0_contract.py'
                contract = source_module('nxnative_runtime_bound_registry_adapter', component / name, lock['adapter_files'][name])
                receipt['actual_force_registry'] = contract.read_registry24(artifact)
                receipt['force_registry_scope'] = 'Actual final binary; no fixed old row count or old six-image force manifest.'
                receipt['objects_after_link'] = {name: base.file_record(name) for name in objects}
                if receipt['objects_after_link'] != objects:
                    raise ValueError('runtime objects changed during link')
                receipt['link_map'] = {'path': str(output / 'link.map'), **base.file_record(output / 'link.map')}
                receipt['status'] = 'compiled-and-strongly-linked-without-execution'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        receipt.update(status='failed', error=str(error))
    finally:
        if artifact and artifact.exists() and not artifact.is_symlink():
            artifact.chmod(0o444)
        if base and before is not None:
            try:
                after, _ = base.source_gate(component)
                receipt['original_source_after'] = after
                if after != before:
                    raise ValueError('original runtime source closure changed')
                receipt['original_source_unchanged'] = True
                if private_before is not None:
                    receipt['private_inputs_after'] = adapted.verify_private(private, private_before, base)
                    receipt['private_inputs_unchanged'] = True
                if 'metadata_header' in receipt:
                    header = receipt['metadata_header']
                    for row in (header, header['profile']):
                        if record(regular_bytes(row['path'])) != {k: row[k] for k in ('bytes', 'sha256')}:
                            raise ValueError('generated base-v0 metadata changed')
                if sdk is not None:
                    for name, expected in ((sdk['clang'], sdk['clang_identity']), (sdk['nm'], sdk['nm_identity']),
                            (sdk['sdk_settings']['path'], {k: sdk['sdk_settings'][k] for k in ('bytes', 'sha256')})):
                        if base.file_record(name) != expected:
                            raise ValueError('runtime toolchain identity changed')
                if resources_before is not None:
                    receipt['external_toolchain_headers_after'] = base.resource_snapshot(Path(sdk['resource_dir']) / 'include')
                    if receipt['external_toolchain_headers_after'] != resources_before:
                        raise ValueError('runtime compiler builtin headers changed')
                if 'patch_tool' in receipt and base.file_record(adapted.PATCH_TOOL) != {k: receipt['patch_tool'][k] for k in ('bytes', 'sha256')}:
                    raise ValueError('runtime patch tool changed')
                if 'artifact' in receipt and base.file_record(artifact) != {k: receipt['artifact'][k] for k in ('bytes', 'sha256')}:
                    raise ValueError('runtime final binary changed during static inspection')
            except (OSError, ValueError, KeyError, TypeError) as error:
                receipt.update(status='failed', closure_after_error=str(error))
        receipt['sdk_system_inputs_excluded_by_mmd'] = True
        receipt['dependency_scope'] = 'Actual -MMD closure; bound private sources/postimages and metadata; separately bound clang builtins and SDK/compiler identity.'
        receipt['compile_link_succeeded'] = receipt['status'] == 'compiled-and-strongly-linked-without-execution'
        (output / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


def main(argv=None):
    args = parse_args(argv)
    try:
        receipt = build(args)
    except (OSError, ValueError) as error:
        print('base-v0 host preparation failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps({'status': receipt['status'], 'receipt': str(args.output.resolve() / 'receipt.json'),
        'entrypoint': receipt['entrypoint'], 'metadata_game_version': '1.0.0',
        'compile_link_succeeded': receipt['compile_link_succeeded'], 'installable': False,
        'host_guest_gpu_executed': False}, sort_keys=True))
    return 1 if receipt['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
