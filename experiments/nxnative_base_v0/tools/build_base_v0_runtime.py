#!/usr/bin/env python3
"""Build the pinned base-v0 host with the actual UIKit entry, without running it.

This host-only tool reuses the admitted complete compile/dependency helpers.
No game input, packaging, signing, install or execution interface is provided.
The original pure game.header(game.load(...)) consumes our dedicated profile.
"""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
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
AUDIO_INVENTORY = 'adapter/base_v0_audren_sources.json'
AUDIO_EXTRA_FILES = ('experiments/botw_upstream_host/upstream/dm/host/botw_pcm.h',)
AUDIO_ADAPTERS = tuple('adapter/' + name for name in ('base_v0_audren.c', 'base_v0_audren.h',
    'base_v0_audren_memory.c', 'base_v0_audren_memory.h', 'base_v0_audren_host_memory.c',
    'base_v0_audren_transfer_memory.inc', 'base_v0_audren_transfer_handles.inc'))
TYPED_PATCH = 'patches/base-v0-vertex-shader-provider.patch'
COUNTER_PATCH = 'patches/base-v0-normal-graphics-counters.patch'
TYPED_MEMBERS = ('adapter/base_v0_shader_provider.h', 'adapter/base_v0_shader_provider.m',
    'shader_provider/adapter/whole_shader_bundle_publication.h',
    'shader_provider/adapter/whole_shader_bundle_publication.m',
    'shader_provider/adapter/whole_shader_conditional.h',
    'shader_provider/adapter/whole_shader_conditional.m', 'upstream/dm/gfx/nvn/nvn_mtl.m')
MEMORY_FILES = tuple('experiments/botw_upstream_host/adapter/' + name for name in (
    'base_v0_heap.c', 'base_v0_heap.h', 'whole_address_space.c',
    'whole_address_space.h', 'whole_policy.c', 'whole_policy.h'))
MEMORY_UNITS = ('adapter/base_v0_memory.c', 'memory_provider/base_v0_heap.c',
                'memory_provider/whole_address_space.c', 'memory_provider/whole_policy.c')
EXTRA_PATCHES = (
    ('patches/base-v0-logical-memory.patch', ('dm/host/hle_svc.c', 'dm/host/main.c')),
    ('patches/base-v0-normal-api-contracts.patch', ('dm/host/hle_account.c', 'dm/gfx/hle_vi.c',
        'dm/gfx/nvn/hle_nvn.c', 'dm/gfx/nvn/nvn_mtl.m')),
    ('patches/base-v0-normal-loader-guard.patch', ('dm/host/loader.c', 'dm/host/main.c',
        'dm/ios/Sources/main.m')),
    ('patches/base-v0-audren-kernel.patch', ('dm/host/hle_svc.c', 'dm/host/hle_thread.c',
        'dm/gfx/nvn/nvn_dirty.c', 'dm/host/thread.c')),
    ('patches/base-v0-audren-runtime.patch', ('dm/host/hle_audren.c', 'dm/host/main.c',
        'dm/ios/Sources/front_ios.m')),
)
REQUIRED = ('tools/build_base_v0_runtime.py', PROFILE, 'profile/base_v0.json',
    'tools/generate_base_v0_contract.py', 'tools/build_link_only.py',
    'tools/build_adapted_link_only.py', 'tools/verify_source.py', 'compile_sources.json',
    'adapter/base_v0_memory.c', 'adapter/base_v0_memory.h',
    'adapter/base_v0_loader_guard.h', 'adapter/base_v0_loader_guard.m',
    AUDIO_INVENTORY, TYPED_PATCH, COUNTER_PATCH, *AUDIO_ADAPTERS,
    *(name for name, _ in EXTRA_PATCHES))
DEFINES = ('NXNATIVE_NORMAL_POLICY=1', 'DM_SHELL=1', 'NXNATIVE_BASE_V0_MEMORY=1',
           'NXNATIVE_BASE_V0_NORMAL_APIS=1', 'NXNATIVE_BASE_V0_LOADER_GUARD=1',
           'NXNATIVE_BASE_V0_AUDREN=1', 'FMT_HEADER_ONLY=1')


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
    for name in AUDIO_EXTRA_FILES:
        if name not in lock.get('external_code_files', {}):
            raise ValueError('complete PCM patch predecessor is not frozen: ' + name)
    audio_inventory(component, lock)
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


def normalize_audio_dependencies(repository, rows):
    """Normalize real MMD spelling without accepting links or escaping roots."""
    result = {}
    for row in rows:
        name = row['path']
        path = PurePosixPath(name)
        if path.is_absolute() or any(c in name for c in '\0\n\r:'):
            raise ValueError('audio dependency path is outside repository')
        original = repository / name
        if any(p.is_symlink() for p in (original, *original.parents)):
            raise ValueError('audio dependency path contains a symlink')
        canonical = os.path.normpath(name)
        if canonical in ('.', '..') or canonical.startswith('../'):
            raise ValueError('audio dependency escapes repository')
        bound = {key: row[key] for key in ('bytes', 'sha256')}
        if canonical in result and result[canonical] != bound:
            raise ValueError('normalized audio dependency identities differ')
        result[canonical] = bound
    return dict(sorted(result.items()))


def locked_repository_record(component, lock, name):
    prefix = str(component.relative_to(component.parents[1])) + '/'
    if name.startswith(prefix):
        return lock.get('adapter_files', {}).get(name[len(prefix):])
    return lock.get('external_code_files', {}).get(name)


def audio_inventory(component, lock=None):
    inventory = json_bytes(regular_bytes(component / AUDIO_INVENTORY))
    rows = inventory.get('translation_units', [])
    if (inventory.get('schema') != 'nxnative-base-v0-audren-source-inventory-v1'
            or inventory.get('new_translation_unit_count') != 95 or len(rows) != 95
            or len({row['source'] for row in rows}) != 95
            or inventory.get('cxx_standard') != 'c++23'
            or inventory.get('cxx_exceptions_required') is not True
            or inventory.get('required_link_libraries') != ['c++']
            or inventory.get('additional_link_frameworks') != ['Security']
            or inventory.get('compile_definitions') != {
                'NXNATIVE_NORMAL_POLICY': 1, 'NXNATIVE_BASE_V0_MEMORY': 1,
                'NXNATIVE_BASE_V0_AUDREN': 1, 'NXNATIVE_BASE_V0_NORMAL_APIS': 1,
                'NXNATIVE_BASE_V0_LOADER_GUARD': 1, 'FMT_HEADER_ONLY': 1}):
        raise ValueError('complete frozen audren inventory contract differs')
    for row in rows:
        for key in ('source', 'prepared_source'):
            if key in row:
                path = PurePosixPath(row[key])
                if path.is_absolute() or '..' in path.parts or str(path) != row[key]:
                    raise ValueError('noncanonical audio translation source')
        if row['source'] in inventory['forbidden_additions'] or row['source'] in inventory['existing_translation_units_not_duplicated']:
            raise ValueError('audio inventory duplicated a provider or added a forbidden kernel/mock')
        if row['language'] not in ('c', 'c++', 'objective-c'):
            raise ValueError('unreviewed audio source language')
    dependencies = normalize_audio_dependencies(component.parents[1], inventory['external_code_files'])
    for name, bound in normalize_audio_dependencies(component.parents[1],
            [{'path': row['source'], 'bytes': row['bytes'], 'sha256': row['sha256']} for row in rows]).items():
        if name in dependencies and dependencies[name] != bound:
            raise ValueError('audio TU differs from its dependency identity')
        dependencies[name] = bound
    patches = [*inventory['native_predecessor_patches'], *inventory['native_audio_patches'],
               *inventory['eden_patches'], *inventory['pcm_patches'], inventory['output_policy_patch']]
    if lock is not None:
        for name, expected in [*dependencies.items(), *((r['path'], {k: r[k] for k in ('bytes', 'sha256')}) for r in patches)]:
            if locked_repository_record(component, lock, name) != expected:
                raise ValueError('audio source or patch is not identically frozen: ' + name)
    return inventory


def audio_include_flags(private, inventory):
    audio = private / 'audio'
    roots = [audio / name for name in inventory['include_roots']['private_staged']]
    roots += [audio / 'repository' / name for name in inventory['include_roots']['repository']]
    return [value for root in roots for value in ('-I', str(root))]


def copy_audio_inputs(component, private, output, lock, base, adapted, receipt, expected):
    inventory = audio_inventory(component, lock)
    receipt['audren_source_inventory'] = inventory
    dependencies = normalize_audio_dependencies(component.parents[1], inventory['external_code_files'])
    for name in AUDIO_EXTRA_FILES:
        dependencies[name] = lock['external_code_files'][name]
    for row in inventory['translation_units']:
        name = row['source'];bound = {key: row[key] for key in ('bytes', 'sha256')}
        if name in dependencies and dependencies[name] != bound:
            raise ValueError('audio TU differs from its dependency identity')
        dependencies[name] = bound
    repository = component.parents[1]
    for name, bound in dependencies.items():
        raw = regular_bytes(repository / name)
        if record(raw) != bound:
            raise ValueError('audio source changed before private copy: ' + name)
        targets = ['repository/' + name]
        if name.startswith('experiments/botw_audio_renderer/vendor/eden/'):
            targets.append('eden/' + name.split('/vendor/eden/', 1)[1])
        if name.startswith('experiments/botw_upstream_host/upstream/dm/host/botw_pcm.'):
            targets.append('pcm/dm/host/' + Path(name).name)
        if name.startswith('native/'):
            targets.append('services/' + name)
        if name == 'experiments/botw_audio_renderer/native/common_ports.cpp':
            targets.append('native/common_ports.cpp')
        for target in targets:
            path = private / 'audio' / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            expected[str(path.relative_to(private))] = record(raw)
    adapted.verify_private(private, expected, base)
    receipt['audio_normalized_raw_dependencies'] = dependencies
    receipt['audio_staging_patches'] = []
    for index, (key, directory) in enumerate((('eden_patches', 'eden'), ('pcm_patches', 'pcm'), ('output_policy_patch', ''))):
        rows = inventory[key] if isinstance(inventory[key], list) else [inventory[key]]
        for ordinal, row in enumerate(rows):
            patch = repository / row['path']
            # The complete patch and exact member set are admitted by its source
            # lock and frozen inventory; zero-fuzz application retains postimages.
            members = adapted.patch_members(patch)
            prefix = 'audio/' + (directory + '/' if directory else '')
            preimages = {name[len(prefix):]: row for name, row in expected.items() if name.startswith(prefix)}
            result = adapted.apply_patch(private / 'audio' / directory, patch, members, output,
                'audio-patch-%d-%d' % (index, ordinal), base, receipt, expected_preimages=preimages)
            receipt['audio_staging_patches'].append(result)
            expected.update({prefix + name: row for name, row in result['postimages'].items()})
    for row in inventory['translation_units']:
        if 'prepared_source' in row:
            path = private / 'audio' / row['prepared_source']
            if base.file_record(path)['sha256'] != row['prepared_sha256']:
                raise ValueError('audio prepared TU differs from frozen postimage')
    receipt['typed_vertex_patch'] = adapted.apply_patch(private, component / TYPED_PATCH,
        TYPED_MEMBERS, output, 'typed-vertex-private-append', base, receipt, expected_preimages=expected)
    expected.update(receipt['typed_vertex_patch']['postimages'])
    preimages = {name[len('upstream/'):]: row for name, row in expected.items() if name.startswith('upstream/')}
    receipt['normal_graphics_counter_patch'] = adapted.apply_patch(private / 'upstream', component / COUNTER_PATCH,
        ('dm/gfx/nvn/nvn_mtl.m',), output, 'normal-graphics-counter-patch', base, receipt,
        expected_preimages=preimages)
    expected.update({'upstream/' + name: row for name, row in receipt['normal_graphics_counter_patch']['postimages'].items()})
    return adapted.verify_private(private, expected, base)


def copy_inputs(component, output, lock, base, adapted, receipt):
    # Select the reviewed helpers' fixed inputs in this fresh module instance.
    # Their original source and the baseline inventory remain unchanged.
    adapted.PATCHES = (*adapted.PATCHES, *EXTRA_PATCHES)
    adapted.COPIED_ADAPTERS = (*(n for n in adapted.COPIED_ADAPTERS if n != 'adapter/link_only_main.c'),
        'adapter/base_v0_memory.c', 'adapter/base_v0_memory.h',
        'adapter/base_v0_loader_guard.h', 'adapter/base_v0_loader_guard.m', PROFILE, *AUDIO_ADAPTERS)
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
    expected = copy_audio_inputs(component, private, output, lock, base, adapted, receipt, expected)
    adapted.verify_private(private, expected, base)
    return private, expected


def compile_commands(private, output, units, sdk, base, adapted, inventory=None):
    inventory = inventory or audio_inventory(COMPONENT)
    commands = adapted.compile_commands(private, output, units, sdk, base)
    commands = [row for row in commands if Path(row['source']).name != 'link_only_main.c']
    template = next(row for row in commands if row['source'].endswith('/adapter/normal_policy.c'))
    for name in (*MEMORY_UNITS, 'adapter/base_v0_loader_guard.m'):
        source = private / name
        argv = list(template['argv'])
        argv[argv.index('-c') + 1] = str(source)
        role = 'base_v0_complete_memory'
        if source.suffix == '.m':
            argv += ['-fobjc-arc', '-fblocks']
            role = 'base_v0_loader_guard'
        commands.append({'source': str(source), 'role': role, 'argv': argv})
    for row in inventory['translation_units']:
        name = ('audio/' + row['prepared_source']) if 'prepared_source' in row else ('audio/repository/' + row['source'])
        argv = list(template['argv'])
        argv[argv.index('-c') + 1] = str(private / name)
        if row['language'] == 'c++':
            argv = [value for value in argv if not value.startswith('-std=')]
            argv += ['--driver-mode=g++', '-std=c++23', '-stdlib=libc++', '-fexceptions']
        elif row['language'] == 'objective-c':
            argv += ['-fobjc-arc', '-fblocks']
        commands.append({'source': str(private / name), 'role': 'audren:' + row['group'],
            'language': row['language'], 'original_source': row['source'], 'argv': argv})
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
                  '-I', str(private / 'memory_provider'), *audio_include_flags(private, inventory)]
        argv[argv.index('-c'):argv.index('-c')] = insert
        row.update(object=str(obj), depfile=str(dep))
    if len(commands) != 235 or len({row['source'] for row in commands}) != 235:
        raise ValueError('base-v0 UIKit host must compile exactly 235 distinct units')
    return commands


def link_command(output, objects, sdk, base):
    argv, artifact = base.link_command(output, objects, sdk)
    argv[1:1] = ['--driver-mode=g++', '-stdlib=libc++']
    argv += ['-lc++', '-framework', 'Security']
    return argv, artifact


def strong_symbols(adapted):
    return (adapted.STRONG_SYMBOLS - {'_nxnative_upstream_ios_main'}) | {
        '_nxnative_base_v0_memory_' + name for name in ('reserve', 'layout', 'admit', 'record',
        'verify', 'initial_pending', 'guard', 'failure')} | {
        '_botw_base_v0_heap_plan_original_logical', '_botw_whole_address_space_reserve',
        '_nxnative_base_v0_layer_scaling_mode', '_dm_guest_heap_current'} | {
        '_nxnative_base_v0_loader_' + name for name in ('preload', 'begin', 'layout', 'image',
        'gate_count', 'registry', 'reference', 'complete', 'entry')} | {
        '_nxnative_base_v0_audren_' + name for name in ('prepare', 'audio_available', 'focus', 'shutdown', 'ready')} | {
        '_botw_whole_transfer_' + name for name in ('prepare', 'dispose', 'acquire', 'release', 'reap_pending')} | {
        '_botw_whole_kernel_producer_signal', '_botw_whole_kernel_retain_event_producer',
        '_botw_whole_heap_provider_create', '_botw_whole_guest_memory_access',
        '_botw_whole_audren_create', '_botw_whole_ipc_bind', '_dm_guest_write_prepare_checked',
        '_botw_audren_host_create', '_botw_audren_open', '_botw_audren_update',
        '_botw_audren_start', '_botw_audren_stop', '_botw_audren_close',
        '_botw_pcm_open', '_botw_pcm_write', '_botw_pcm_close', '_whole_runtime_vertex_shader_publication'}


def verify_symbols(text, required):
    actual = {fields[-1] for line in text.splitlines()
              if len(fields := line.split()) >= 3 and fields[-2] == 'T'}
    if not required <= actual:
        raise ValueError('required runtime strong symbols absent: ' + ', '.join(sorted(required - actual)))
    if '_nxnative_upstream_ios_main' in actual:
        raise ValueError('runtime retained the renamed link-only UIKit entry')
    return actual


def verify_compile_roles(commands):
    audio = [row for row in commands if row['role'].startswith('audren:')]
    cpp = [row for row in audio if row.get('language') == 'c++']
    if len(commands) != 235 or len(audio) != 95 or len(cpp) != 79:
        raise ValueError('actual complete host/audio compile roles differ')
    for row in cpp:
        if not {'--driver-mode=g++', '-std=c++23', '-stdlib=libc++', '-fexceptions',
                '-DFMT_HEADER_ONLY=1'} <= set(row['argv']):
            raise ValueError('actual C++ renderer compile recipe differs')
    return dict(sorted(Counter(row['role'] for row in commands).items()))


def new_receipt(base=None):
    receipt = base.new_receipt() if base else {'steps': [], 'compiles': [], 'status': 'not-started'}
    receipt.update(schema='nxnative-base-v0-UIKit-host-build-v1',
        baseline='base-v0-original-1.0.0-UIKit-host-compile-only',
        metadata_game_version='1.0.0', metadata_nvn_api='52.32', entrypoint='UIKit _main',
        base_v0_configured=False, base_v0_host_metadata_configured=False,
        installable=False, installable_candidate=False, host_guest_gpu_executed=False,
        audio_runtime_closed=False, whole_host_policy_closed=False, gameplay_verified=False,
        audio_source_integrated=False, typed_vertex_source_integrated=False,
        audio_runtime_closure_scope='complete admitted source/roles/macros/actual SDK compilation and C++ strong link; no physical audio claim',
        whole_host_policy_scope='normal diagnostic, loader, memory and fixed failure policy; not shader catalogue or gameplay coverage',
        game_payload_embedded=False, resources_validated=False,
        compile_defines=list(DEFINES), compile_link_succeeded=False,
        upstream_build_scripts_executed=False,
        pending=['actual SDK compile/link and static symbol/source verification', 'local exact resource/Guest/registry contract',
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


def cxx_headers(sdk):
    toolchain = Path(sdk['clang']).parent.parent / 'include/c++/v1'
    if not toolchain.is_dir():
        raise ValueError('selected compiler complete libc++ headers are unavailable')
    return toolchain


def build(args, component=COMPONENT):
    output = prepare_output(args.output, component)
    receipt = new_receipt()
    base = adapted = before = private = private_before = artifact = sdk = resources_before = cxx_before = None
    try:
        base, adapted, before, lock = load_helpers(component)
        receipt = new_receipt(base)
        receipt['original_source_before'] = before
        units = base.validate_inventory(base.read_json(component / 'compile_sources.json'), lock)
        private, private_before = copy_inputs(component, output, lock, base, adapted, receipt)
        receipt['private_inputs_before'] = private_before
        receipt['audio_source_integrated'] = True
        receipt['typed_vertex_source_integrated'] = True
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
            'shader_provider': 6, 'complete_memory_provider': 4, 'loader_guard': 1,
            'complete_audren_increment': 95, 'audren_cxx': 79, 'total': 235}
        if args.stage_only:
            receipt['status'] = 'staged-and-profile-generated-without-compilation'
        else:
            sdk = base.query_toolchain(output, component, receipt)
            receipt['toolchain'] = sdk
            resources_before = base.resource_snapshot(Path(sdk['resource_dir']) / 'include')
            receipt['external_toolchain_headers_before'] = resources_before
            cxx_before = base.resource_snapshot(cxx_headers(sdk))
            receipt['external_libcxx_headers_before'] = cxx_before
            known = {str((private / name).resolve()): row for name, row in private_before.items()}
            known[str(header_path)] = record(header.encode())
            known.update(cxx_before)
            commands = compile_commands(private, output, units, sdk, base, adapted, receipt['audren_source_inventory'])
            for command in commands:
                command['source_record'] = known[str(Path(command['source']).resolve())]
            receipt['planned_compiles'] = commands
            argv, _ = link_command(output, [row['object'] for row in commands], sdk, base)
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
                receipt['actual_compile_role_counts'] = verify_compile_roles(receipt['compiles'])
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
                if cxx_before is not None:
                    receipt['external_libcxx_headers_after'] = base.resource_snapshot(cxx_headers(sdk))
                    if receipt['external_libcxx_headers_after'] != cxx_before:
                        raise ValueError('runtime compiler libc++ headers changed')
                if 'patch_tool' in receipt and base.file_record(adapted.PATCH_TOOL) != {k: receipt['patch_tool'][k] for k in ('bytes', 'sha256')}:
                    raise ValueError('runtime patch tool changed')
                if 'artifact' in receipt and base.file_record(artifact) != {k: receipt['artifact'][k] for k in ('bytes', 'sha256')}:
                    raise ValueError('runtime final binary changed during static inspection')
            except (OSError, ValueError, KeyError, TypeError) as error:
                receipt.update(status='failed', closure_after_error=str(error))
        receipt['sdk_system_inputs_excluded_by_mmd'] = True
        receipt['dependency_scope'] = 'Actual -MMD closure; bound private sources/postimages and metadata; separately bound clang builtins and SDK/compiler identity.'
        receipt['compile_link_succeeded'] = receipt['status'] == 'compiled-and-strongly-linked-without-execution'
        if receipt['compile_link_succeeded']:
            # The flags describe newly compiled, checked source/link closure.
            # Game resources, signing and physical output remain separate facts.
            receipt['audio_runtime_closed'] = True
            receipt['whole_host_policy_closed'] = True
            receipt['pending'] = receipt['pending'][1:]
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
