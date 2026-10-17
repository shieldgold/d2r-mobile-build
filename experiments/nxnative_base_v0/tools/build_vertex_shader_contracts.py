"""Build a separate base-1.0 typed vertex subset with the complete CPU pipeline.

Recipes condition later draws; they are not per-shader game facts. The old
3173-key publication stays untouched. No Metal, guest or host is executed.
"""
import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_SOURCE = 'experiments/nxnative_base_v0/tools/package_shader_sources.py'
PACKAGE_SHA = 'b76ca6f0773292d71bc5220e271915e1838d43ffc8f53a94014171df396cb784'
PIPELINE = 'experiments/nxnative_base_v0/patches/base-v0-vertex-shader-pipeline.patch'
PIPELINE_SHA = 'ef4193a5206aded1826838f7c44eee4c0cc1dac31f06f485df5367cd0ff30c6a'
NATIVE_BUILD = 'experiments/nxnative_base_v0/upstream/dm/gfx/tools/dmsh_build.py'
NATIVE_BUILD_SHA = 'b99fb8ae38aae5b389ac562358309081ccdc72f05764424847ce7d930e344a48'
BUILD_PINS = {
    'experiments/botw_upstream_host/adapter/shader_v8_control.py': 'c3f20f9db0525f2db46583308f93e598c6e3b47bb0da38c911c918b323c22145',
    'experiments/botw_upstream_host/patches/shader-v8-toolchain.patch': 'ff3ff1577682e2a1905437235de09e107eb3c1a12e9eb7828d1c0fd5370bb4fd',
    'experiments/botw_upstream_host/patches/base-v0-image-toolchain.patch': '4026b5553dfc8155d85f6460a4cdaace915a049aca77a8b5341a1298bbc3430a',
    'scripts/botw_native/resource_shaders.py': 'b4bb98f8f8d1e3ee488d91282862d079bf112050525502612237d21060835e90',
    'scripts/botw_native/romfs.py': '7ba025d205f09cec6af52e18ad3fe949b23c9fafb7b377a156e25951a16ac472',
    'scripts/botw_native/shaders.py': '346a2c8ae973af28051437021508c556b0e40a717127b03a297a05c2b2fb828a',
    'scripts/botw_native/archive.py': '7278abee3867ca212078c396e4d8948ad6b42df8052c58106b3276d95ac2bedb',
}


def bootstrap():
    # Bootstrap cannot depend on Python caches or the helper being loaded.
    path = ROOT / PACKAGE_SOURCE
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 65536:
            raise ValueError('bounded regular package source required')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(65537)
            after = os.fstat(stream.fileno())
        if (len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != PACKAGE_SHA or
                any(getattr(before, k) != getattr(after, k)
                    for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))):
            raise ValueError('package source identity changed')
    finally:
        if fd is not None:
            os.close(fd)
    module = types.ModuleType('_nx_vertex_complete_package_source')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


@contextmanager
def staged_pipeline(temporary):
    package = bootstrap()
    patch = package.read(ROOT / PIPELINE, 128 * 1024)
    package.require(package.digest(patch) == PIPELINE_SHA, 'typed vertex pipeline patch identity')
    with package.original_validator(temporary) as (_, ports):
        snapshot = Path(temporary) / 'trusted-source'
        for relative, expected in BUILD_PINS.items():
            raw = package.read(ROOT / relative)
            package.require(package.digest(raw) == expected, 'complete vertex build dependency: ' + relative)
            path = snapshot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        local_patch = Path(temporary) / 'typed-pipeline.patch'
        local_patch.write_bytes(patch)
        run = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(local_patch)],
                             cwd=ports.COMPONENT, capture_output=True, text=True)
        package.require(run.returncode == 0 and 'fuzz' not in run.stdout and 'offset' not in run.stdout,
                        'exact complete vertex source patch failed')
        publisher = ports.load(ports.COMPONENT / 'adapter/shader_conditional_build.py', '_native_package_vertex_builder')
        validator = ports.load(ports.COMPONENT / 'scripts/package_conditional.py', '_native_package_vertex_validator')
        package.require(publisher.SCOPE == 'typed-vs-nonqueried-v1', 'separate typed vertex scope required')
        yield package, publisher, validator, ports


def integer_recipe_function(package):
    # Reuse the entire fixed native dataflow helper, without executing that CLI
    # or its top-level game/1.9 configuration and shader compilation paths.
    path = ROOT / NATIVE_BUILD
    raw = package.read(path, 1024 * 1024)
    package.require(package.digest(raw) == NATIVE_BUILD_SHA, 'native integer-input helper source identity')
    tree = ast.parse(raw, filename=str(path))
    nodes = [node for node in tree.body if
             (isinstance(node, ast.FunctionDef) and node.name == 'int_inputs') or
             (isinstance(node, ast.Assign) and len(node.targets) == 1 and
              isinstance(node.targets[0], ast.Name) and node.targets[0].id == '_CAST')]
    package.require(len(nodes) == 2, 'complete native integer-input helper closure')
    namespace = {'re': re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['int_inputs']


def recipe_from_source(msl, integer_inputs):
    rows = re.findall(r'(\w+)\s+\w+\s*\[\[attribute\((\d+)\)\]\]', msl)
    attributes = {}
    for ty, location in rows:
        location = int(location)
        if location >= 16 or location in attributes or not re.fullmatch(r'(float|int|uint)[234]?', ty):
            raise ValueError('probe vertex input is not a strict native descriptor input')
        attributes[location] = re.sub(r'[234]$', '', ty)
    if not attributes:
        raise ValueError('vertex recipe requires actual MSL attributes')
    for location, ty in integer_inputs(msl).items():
        location = int(location)
        if location not in attributes or ty != 'uint':
            raise ValueError('integer-input helper returned an unbound input')
        attributes[location] = ty
    return [{'location': k, 'component_type': v} for k, v in sorted(attributes.items())]


def recipes(probe_manifest, output):
    package = bootstrap()
    manifest = package.document(package.read(probe_manifest))
    package.require(isinstance(manifest, dict) and isinstance(manifest.get('results'), list), 'probe result inventory required')
    integer_inputs = integer_recipe_function(package)
    selected = {}
    for row in manifest['results']:
        capture = row.get('capture', {})
        if capture.get('stage') != 0 or not row.get('required_facts', {}).get('attributes'):
            continue
        key = row.get('key')
        package.require(isinstance(key, str) and re.fullmatch('[0-9a-f]{16}', key) and
                        key == capture.get('key') and key not in selected, 'probe full vertex identity')
        if row.get('notes', []) or any(item.get('what') == 'translator query fallback'
                                      for item in row.get('unknowns', [])):
            continue  # Those keys need a separate proven resource contract.
        raw = package.read(row['files']['msl'], 16 * 1024 * 1024)
        package.require(raw and b'\0' not in raw, 'probe MSL must be bounded NUL-free source')
        recipe = recipe_from_source(raw.decode('utf-8', errors='strict'), integer_inputs)
        package.require(set(row['required_facts']['attributes']) == {str(r['location']) for r in recipe},
                        'probe metadata differs from complete MSL vertex inputs')
        selected[key] = recipe
    package.require(selected, 'no typed vertex recipes selected')
    value = dict(schema='nxnative-base-v0-vertex-recipes-v1', profile='botw-switch-base-v0', recipes=selected)
    output = package.checked_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(package.canonical(value))
    return len(selected)


def checked_bridge(package, path):
    raw=package.read(path,32*1024*1024)
    package.require(package.digest(raw)==package.BRIDGE_SHA256,'complete fixed base-1.0 bridge identity')
    value=package.document(raw)
    package.require(value.get('schema')=='nxnative-base-v0-shader-key-bridge-v1' and
                    value.get('profile')=='botw-switch-base-v0' and value.get('game_version')=='1.0.0',
                    'fixed base-1.0 bridge required')
    return {row['capture_key']:row for row in value['rows']}


def verified_files(package, validator, ports, source, identities):
    files,index=validator.shader_files(source,ports)
    admission=package.document(files['conditional-admission.json'])
    package.require(all(key in identities and row['capture_sha256']==identities[key]['capture_sha256'] and
                        row['stage_name']=='vertex' and identities[key]['stage']==0 and
                        identities[key]['status']=='only_identity' for key,row in admission['captures'].items()),
                    'supplement capture differs from the complete raw base-1.0 identity')
    files['vertex-source-package.json']=package.canonical(dict(
        schema='nxnative-base-v0-vertex-source-package-v1',profile='botw-switch-base-v0',
        game_version='1.0.0',scope='typed-vs-nonqueried-v1',bridge_sha256=package.BRIDGE_SHA256,
        conditional_index_sha256=package.digest(files['shader-package.json']),
        conditional_publication_sha256=index['publication_sha256']))
    return files,index,len(admission['captures'])


def audit(publication, bridge, package_output=None):
    with tempfile.TemporaryDirectory(prefix='nx-vertex-complete-audit-') as temporary:
        with staged_pipeline(Path(temporary).resolve()) as (package, _, validator, ports):
            identities=checked_bridge(package,bridge)
            files,index,count=verified_files(package,validator,ports,publication,identities)
            if package_output is not None:
                package.publish(files,package_output)
            print(json.dumps(dict(vertex_captures=count,variants=count*4,chain=index['chain'],
                                  complete_original_reaudit=True,game_execution_ready=False)))
    return 0


def build(inputs, captures, request, destination, *, bridge, xlate, spirv_cross, jobs=1, pairs=None, package_output=None):
    with tempfile.TemporaryDirectory(prefix='nx-vertex-complete-pipeline-') as temporary:
        with staged_pipeline(Path(temporary).resolve()) as (package, publisher, validator, ports):
            identities=checked_bridge(package,bridge)
            selected,_,_=publisher.requested_keys(request)
            package.require(all(key in identities and identities[key].get('stage')==0 and
                            identities[key].get('status')=='only_identity' for key in selected),
                            'supplement may only extend existing base-1.0 vertex identities')
            result = publisher.build(inputs, captures, request, destination,
                                     xlate=xlate, spirv_cross=spirv_cross, jobs=jobs, pairs=pairs)
            if result:
                return result
            # The same complete independent publisher re-audit reconstructs
            # every final typed job, reflection, C1, four modes and source SHA.
            files, index, _ = verified_files(package,validator,ports,destination,identities)
            if package_output is not None:
                package.publish(files, package_output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    recipe = commands.add_parser('recipes')
    recipe.add_argument('probe_manifest', type=Path)
    recipe.add_argument('output', type=Path)
    audit_=commands.add_parser('audit')
    audit_.add_argument('publication',type=Path)
    audit_.add_argument('--bridge',type=Path,required=True)
    audit_.add_argument('--package-output',type=Path)
    compile_ = commands.add_parser('build')
    for name in ('inputs', 'captures', 'request', 'destination'):
        compile_.add_argument(name, type=Path)
    compile_.add_argument('--xlate', type=Path, required=True)
    compile_.add_argument('--bridge', type=Path, required=True)
    compile_.add_argument('--spirv-cross', type=Path, required=True)
    compile_.add_argument('--jobs', type=int, default=1)
    compile_.add_argument('--pairs', type=Path)
    compile_.add_argument('--package-output', type=Path)
    options = vars(parser.parse_args(argv))
    command = options.pop('command')
    try:
        if command == 'recipes':
            count = recipes(**options)
            print(json.dumps(dict(typed_vertex_recipes=count, game_version='1.0.0', game_execution_ready=False)))
            return 0
        return (audit if command=='audit' else build)(**options)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print('typed vertex source refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
