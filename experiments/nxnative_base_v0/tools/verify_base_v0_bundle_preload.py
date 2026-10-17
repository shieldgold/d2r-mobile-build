#!/usr/bin/env python3
"""Run the complete frozen preload validator on local file operands only.

The owned CPU probe calls only loader_preload. It never loads a Mach-O image,
enters the Host/Guest/SDK, verifies a cryptographic signature or signs a file.
Its canonical eighteen-section proof is independent of signing's file envelope.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import stat
import sys
import types

sys.dont_write_bytecode = True
COMPONENT = Path(__file__).resolve().parents[1]
NAME = 'tools/verify_base_v0_bundle_preload.py'
RUNTIME = 'tools/build_base_v0_runtime.py'
DRIVER = r'''#include "base_v0_loader_guard.h"
#include <stdio.h>
int main(int argc,char **argv){
 if(argc!=2)return 2;
 if(nxnative_base_v0_loader_preload(argv[1])){
  fputs("static preload rejected\n",stderr);return 1;
 }
 puts("static preload accepted; no image was loaded");return 0;
}
'''


def record(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def regular(path, limit=64*1024**2):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('input path contains a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise ValueError('bounded regular input required')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError('input changed while reading')
    return raw


def load_helpers(component):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate source lock key')
            value[key] = item
        return value
    lock = json.loads(regular(component/'SOURCE.lock.json', 16*1024**2), object_pairs_hook=unique)
    sources = {}
    for name in (NAME, RUNTIME):
        expected = lock.get('adapter_files', {}).get(name)
        if expected is None:
            raise ValueError('static verifier source is not frozen: ' + name)
        raw = regular(component/name, 256*1024)
        if record(raw) != expected:
            raise ValueError('static verifier source changed: ' + name)
        sources[name] = raw
    runtime = types.ModuleType('nxnative_bound_runtime_for_static_preload')
    runtime.__file__ = str(component/RUNTIME)
    exec(compile(sources[RUNTIME], runtime.__file__, 'exec'), runtime.__dict__)
    base, _, before, verified = runtime.load_helpers(component)
    if verified != lock:
        raise ValueError('source lock changed during static verifier bootstrap')
    return runtime, base, before


def prepare_output(output, component, bundle):
    output, bundle = Path(output).absolute(), Path(bundle).absolute()
    for path in (output, bundle):
        if '..' in path.parts or any(c in str(path) for c in '\0\n\r:'):
            raise ValueError('canonical bundle/output path required')
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('bundle/output path contains a symlink')
    if not bundle.is_dir():
        raise ValueError('bundle must be an existing regular directory')
    target = output.resolve()
    for source in (bundle.resolve(), component.resolve()):
        if target == source or source in target.parents or target in source.parents:
            raise ValueError('output overlaps bundle or component')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    return output.resolve(), bundle.resolve()


def query_toolchain(output, base, receipt):
    def query(argv, label):
        row = base.run_command(argv, output, label, output)
        receipt['steps'].append(row)
        if row['exit_code']:
            raise ValueError('static verifier compiler query failed')
        return regular(row['stdout']['path'], 65536).decode().strip()
    sdk = Path(query(['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'], 'mac-sdk')).resolve()
    clang = Path(query(['/usr/bin/xcrun', '--sdk', 'macosx', '--find', 'clang'], 'mac-clang')).resolve()
    resource = Path(query([clang, '-print-resource-dir'], 'mac-clang-resource')).resolve()
    version = query([clang, '--version'], 'mac-clang-version')
    settings = sdk/'SDKSettings.json'
    return {'sdk_path': str(sdk), 'sdk_settings': {'path': str(settings), **record(regular(settings))},
        'clang': str(clang), 'clang_identity': base.file_record(clang),
        'resource_dir': str(resource), 'compiler_version': version}


def compile_probe(component, output, header, sdk, base, receipt):
    generated = output/'generated';generated.mkdir()
    header_path = generated/'game_profile.h';header_path.write_text(header)
    driver = output/'owned_static_preload.c';driver.write_text(DRIVER)
    guard = component/'adapter/base_v0_loader_guard.m'
    known = {str(p.resolve()): record(regular(p)) for p in (
        driver, header_path, guard, component/'adapter/base_v0_loader_guard.h')}
    receipt['probe_compilation_inputs'] = known
    objects = []
    for index, source in enumerate((driver, guard)):
        obj = output/('preload-%d.o' % index);dep = obj.with_suffix('.d')
        argv = [sdk['clang'], '--no-default-config', '-isysroot', sdk['sdk_path'],
            '-std=gnu11', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
            '-DNXNATIVE_BASE_V0_LOADER_GUARD=1', '-DNXNATIVE_NORMAL_POLICY=1', '-DNXNATIVE_BASE_V0_MEMORY=1',
            '-I', str(generated), '-I', str(component/'adapter'),
            '-MMD', '-MF', str(dep), '-MT', str(obj), '-c', str(source), '-o', str(obj)]
        step = base.run_command(argv, output, 'static-preload-compile-%d' % index, output)
        receipt['steps'].append(step)
        if step['exit_code']:
            raise ValueError('owned static preload compile failed')
        closure = base.verify_dependencies(base.parse_depfile(dep), known, Path(sdk['sdk_path']), Path(sdk['resource_dir']))
        if str(source.resolve()) not in {row['path'] for row in closure['bound_inputs']}:
            raise ValueError('actual static preload source absent from MMD closure')
        receipt.setdefault('compile_dependencies', []).append(closure)
        objects.append(obj)
    receipt['objects_before_link'] = {str(p): base.file_record(p) for p in objects}
    probe = output/'OWNED-STATIC-PRELOAD-VERIFIER'
    step = base.run_command([sdk['clang'], '--no-default-config', '-isysroot', sdk['sdk_path'],
        *objects, '-framework', 'Foundation', '-Wl,-undefined,error', '-o', probe], output, 'static-preload-link', output)
    receipt['steps'].append(step)
    if step['exit_code'] or {str(p): base.file_record(p) for p in objects} != receipt['objects_before_link']:
        raise ValueError('owned static preload strong link failed or objects changed')
    probe.chmod(0o500)
    receipt['owned_probe'] = {'path': str(probe), **base.file_record(probe), 'host_guest_executable': False}
    return probe


def bundle_inputs(bundle, modules):
    names = ['dm/base_v0_loader_contract.json', 'Frameworks/Guest.framework/Guest']
    names += ['dm/' + name + '.' + kind + '.tsv' for name in modules for kind in ('fixups', 'gates', 'cuts', 'params', 'obs')]
    return {name: record(regular(bundle/name)) for name in names}


def verify(args, component=COMPONENT):
    output, bundle = prepare_output(args.output, component, args.bundle)
    receipt = {'schema': 'nxnative-base-v0-static-bundle-preload-verification-v1', 'steps': [],
        'bundle': str(bundle), 'status': 'not-started', 'static_preload_passed': False,
        'host_guest_gpu_executed': False, 'image_loaded': False, 'signing_performed': False,
        'cryptographic_signature_verified': False, 'installable': False,
        'scope': 'complete frozen loader_preload only: thirty tables and eighteen canonical payload sections; signature authentication and final host registry remain separate'}
    runtime = base = before = sdk = probe = builtins = inputs = None
    try:
        runtime, base, before = load_helpers(component)
        receipt['source_before'] = before
        inputs = bundle_inputs(bundle, runtime.MODULES)
        receipt['bundle_inputs_before'] = inputs
        contract = runtime.json_bytes(regular(bundle/'dm/base_v0_loader_contract.json', 2*1024**2))
        receipt['original_packed_guest_sha256_from_contract'] = contract.get('guest_framework_sha256')
        receipt['actual_guest_file'] = inputs['Frameworks/Guest.framework/Guest']
        header, metadata = runtime.render_header(regular(component/runtime.PROFILE),
            runtime.json_bytes(regular(component/'profile/base_v0.json')),
            regular(component/'upstream/dm/games/game.py'), output)
        receipt['metadata_header'] = metadata
        sdk = query_toolchain(output, base, receipt);receipt['toolchain'] = sdk
        builtins = base.resource_snapshot(Path(sdk['resource_dir'])/'include')
        receipt['compiler_builtin_headers_before'] = builtins
        probe = compile_probe(component, output, header, sdk, base, receipt)
        step = base.run_command([probe, bundle], output, 'static-preload-result', output)
        receipt['steps'].append(step)
        receipt['owned_static_preload_executed'] = True
        if step['exit_code']:
            raise ValueError('complete frozen static preload refused this bundle')
        receipt['status'] = 'complete-static-preload-passed-without-loading-images'
    except (OSError, ValueError, KeyError, TypeError) as error:
        receipt.update(status='failed', error=str(error))
    finally:
        try:
            if runtime:
                if inputs is not None:
                    receipt['bundle_inputs_after'] = bundle_inputs(bundle, runtime.MODULES)
                    if receipt['bundle_inputs_after'] != inputs:
                        raise ValueError('bundle operands changed during static preload')
                after, _ = base.source_gate(component);receipt['source_after'] = after
                if after != before:
                    raise ValueError('static verifier source closure changed')
            if sdk:
                if base.file_record(sdk['clang']) != sdk['clang_identity'] or record(regular(sdk['sdk_settings']['path'])) != {k:sdk['sdk_settings'][k] for k in ('bytes','sha256')}:
                    raise ValueError('static verifier compiler/SDK changed')
                receipt['compiler_builtin_headers_after'] = base.resource_snapshot(Path(sdk['resource_dir'])/'include')
                if receipt['compiler_builtin_headers_after'] != builtins:
                    raise ValueError('static verifier compiler headers changed')
            if probe:
                for path, expected in receipt['probe_compilation_inputs'].items():
                    if record(regular(path)) != expected:
                        raise ValueError('static probe compilation input changed')
                profile = receipt['metadata_header']['profile']
                if record(regular(profile['path'])) != {k:profile[k] for k in ('bytes','sha256')}:
                    raise ValueError('static probe generated profile changed')
                if base.file_record(probe) != {k:receipt['owned_probe'][k] for k in ('bytes','sha256')}:
                    raise ValueError('owned static preload probe changed')
                probe.chmod(0o400)
        except (OSError, ValueError, KeyError, TypeError) as error:
            receipt.update(status='failed', closure_error=str(error))
        receipt['static_preload_passed'] = receipt['status'] == 'complete-static-preload-passed-without-loading-images'
        (output/'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2)+'\n')
    return receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        receipt = verify(args)
    except (OSError, ValueError) as error:
        print('static preload preparation refused: ' + str(error), file=sys.stderr);return 1
    print(json.dumps({'status':receipt['status'], 'static_preload_passed':receipt['static_preload_passed'],
        'receipt':str(args.output.absolute()/'receipt.json'), 'image_loaded':False, 'signing_performed':False}, sort_keys=True))
    return 0 if receipt['static_preload_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
