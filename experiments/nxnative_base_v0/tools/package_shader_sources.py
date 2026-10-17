"""Package local conditional sources through the complete existing CPU validator.

No translation, Metal compilation, game execution or signing occurs. The
original validator gets a fresh, hash-bound source tree rather than Python
cache files. Only its exact public output and the reviewed identity bridge
enter the bundle; captures and its 374-file toolchain stay on this Mac.
"""
import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[3]
BRIDGE_SHA256 = '72affdfd7628c1ea813e02cb208d9917803f7ab12031b5ac5907a95618a6ad8a'
PUBLICATION_SHA256 = '7aa508e282eef9e0fcb558354b45bea1154101da88e470ef62f76fa298512939'
REUSE_PINS = {
    'scripts/botw_native/__init__.py': '5029578e70eaf1233a549057e4323eb38e527963de0257d9658736f2fa2c3c8a',
    'scripts/botw_native/upstream_shaders.py': 'f72c980ef7d3f220fa9ad00aad5b9f5e0cd091d53d098cf78a45192498632fc4',
    'scripts/botw_native/shader_bindings.py': '6f7b0316541dd0225c656c5eddb68ff4bb2b10b313bb2b424a22c1f2d374a935',
    'scripts/botw_native/ios.py': 'f551febb55e4b987cfe70f131f9263b1af222ba11327ae03598b7c9314fb0373',
    'experiments/botw_upstream_host/scripts/package_conditional.py': 'b31cadabff5fdea65b95ac432d671fa4b64744ee6a7bf8be6349509ff24d49a7',
    'experiments/botw_upstream_host/adapter/shader_source_build.py': 'eff60d07e85a420c039dada90362770231ff16a933dd731c45a329ff67cee529',
    'experiments/botw_upstream_host/adapter/shader_conditional_build.py': 'a91e56c76b22571bba5985acf168694ba84445d256a6e56b0660237171647fe6',
    'experiments/botw_upstream_host/adapter/shader_v8_control.json': '90c6fe0d77ae2fc684959ce255029d9210a76565774a5ff90c8cf887bb5ad30f',
    'experiments/botw_upstream_host/adapter/base_v0_graphics_binding.json': '480e8ed95dc002acc574d90294234e11aaf99caf2afa647c8950ccffe9abe8a3',
    'experiments/botw_upstream_host/adapter/base_v0_graphics_formats.json': '3f46576221f48db69abb5b553529fea2dd5f9a2ac1d14a50d5e7c57a499a2865',
    'native/botw_resources/shader_toolchain/UPSTREAM.json': '89b362bf6a18153840ece18a3ab3e616d535dbcf9f01a917406c05e2842775ed',
}
HEX = re.compile('[0-9a-f]{64}')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def checked_path(path):
    path = Path(path)
    require('..' not in path.parts, 'path traversal')
    path = Path(os.path.abspath(path))
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink path')
    return path


def read(path, limit=64 * 1024 * 1024):
    path = checked_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit, 'bounded regular input required')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        require(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k)
                for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'input changed')
        return raw
    finally:
        if fd is not None:
            os.close(fd)


def document(raw):
    def unique(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate JSON key')
            value[key] = item
        return value
    def invalid(_):
        raise ValueError('nonfinite JSON')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def load_source(path, name):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(read(path), str(path), 'exec'), module.__dict__)
    return module


@contextmanager
def original_validator(temporary):
    """Keep complete validator/build modules; port only their file loader and IO."""
    snapshot = checked_path(temporary) / 'trusted-source'
    snapshot.mkdir()
    for name, expected in REUSE_PINS.items():
        raw = read(ROOT / name)
        require(digest(raw) == expected, 'reused source identity: ' + name)
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    vendor = 'native/botw_resources/shader_toolchain'
    metadata = document(read(snapshot / vendor / 'UPSTREAM.json'))
    require(metadata.get('commit') == '21f026e0c7fd9fee575a1bfa8d925553bb4db88e' and
            len(metadata.get('files', {})) == 374, 'complete shader source identity')
    for name, expected in metadata['files'].items():
        require(name and not name.startswith('/') and all(p not in ('', '.', '..')
                for p in name.split('/')) and '\\' not in name, 'source relative path')
        raw = read(ROOT / vendor / name, 8 * 1024 * 1024)
        require(digest(raw) == expected, 'original shader source hash: ' + name)
        target = snapshot / vendor / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    previous_path = list(sys.path)
    previous_modules = dict(sys.modules)
    previous_cache = sys.dont_write_bytecode
    try:
        for name in tuple(sys.modules):
            if name == 'scripts' or name.startswith('scripts.'):
                del sys.modules[name]
        sys.path.insert(0, str(snapshot))
        sys.dont_write_bytecode = True
        component = snapshot / 'experiments/botw_upstream_host'
        builder = load_source(component / 'adapter/shader_source_build.py', '_native_package_source_builder')
        validator = load_source(component / 'scripts/package_conditional.py', '_native_package_conditional_validator')
        # Reuse both complete generic path helpers without importing the old
        # app/Host39 packager or granting authority to its runtime policies.
        path = snapshot / 'scripts/botw_native/ios.py'
        tree = ast.parse(read(path), filename=str(path))
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('_relative', '_tree')]
        require({node.name for node in helpers} == {'_relative', '_tree'}, 'generic IO helper closure')
        namespace = {'Path': Path, 'PurePosixPath': PurePosixPath}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), str(path), 'exec'), namespace)
        ports = types.SimpleNamespace(checked_path=checked_path, digest=digest,
            source_builder=builder, read=read, COMPONENT=component, load=load_source,
            ios=types.SimpleNamespace(_json=document, _relative=namespace['_relative'], _tree=namespace['_tree']))
        yield validator, ports
    finally:
        sys.path[:] = previous_path
        sys.dont_write_bytecode = previous_cache
        for name in tuple(sys.modules):
            if (name == 'scripts' or name.startswith('scripts.') or name.startswith('_native_package_')
                    or name.startswith('_package_')):
                sys.modules.pop(name, None)
        for name, value in previous_modules.items():
            if name == 'scripts' or name.startswith('scripts.') or name.startswith(('_native_package_', '_package_')):
                sys.modules[name] = value


def combine_verified(files, index, bridge_raw, *, expected_bridge_sha256=BRIDGE_SHA256):
    """Join an already validated publication with its reviewed complete bridge."""
    require(digest(bridge_raw) == expected_bridge_sha256, 'bridge identity')
    bridge = document(bridge_raw)
    require(index.get('schema') == 'botw-whole-conditional-shader-package-v1' and
            index.get('profile') == 'botw-switch-base-v0' and
            index.get('directory') == 'botw-c-' + index.get('chain', '') and
            re.fullmatch('[0-9a-f]{12}', index.get('chain', '')) is not None, 'conditional index')
    require(document(files['shader-package.json']) == index, 'index byte identity')
    for field, name in (('publication_sha256', 'conditional-publication.json'),
                        ('admission_sha256', 'conditional-admission.json'), ('subset_sha256', 'subset-proof.json'),
                        ('binding_contract_sha256', 'base-bindings.json'), ('format_contract_sha256', 'base-formats.json')):
        require(index[field] == digest(files[name]), 'conditional source hash: ' + name)
    require(bridge.get('schema') == 'nxnative-base-v0-shader-key-bridge-v1' and
            bridge.get('profile') == 'botw-switch-base-v0' and bridge.get('game_version') == '1.0.0' and
            all(bridge.get(k) is False for k in ('catalog_complete_for_entire_game', 'native_loader_admitted',
                                               'guest_or_gpu_executed', 'gameplay_verified')), 'bridge scope')
    conditional = bridge.get('conditional_source', {})
    require(all(conditional.get(k) == index[k] for k in ('publication_sha256', 'admission_sha256', 'subset_sha256')),
            'bridge publication identity')
    captures = document(files['conditional-admission.json'])['captures']
    selected = {}
    native_keys = {}
    variants = set()
    rows = bridge.get('rows')
    require(isinstance(rows, list) and type(bridge.get('captures')) is int and len(rows) == bridge['captures'], 'bridge rows')
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('native_sha256'), str) and
                HEX.fullmatch(row['native_sha256']) and row.get('native_key') == row['native_sha256'][:16], 'native identity')
        require(row['native_key'] not in native_keys, 'native key collision or duplicate')
        native_keys[row['native_key']] = row['native_sha256']
        if row.get('status') != 'conditional_reference_only':
            continue
        key = row.get('capture_key')
        require(key in captures and key not in selected and row.get('capture_sha256') == captures[key]['capture_sha256'] and
                key == row['capture_sha256'][:16] and type(row.get('stage')) is int and
                row['stage'] in (0, 1) and row.get('stage_name') == ('vertex', 'fragment')[row['stage']] == captures[key]['stage_name'],
                'capture full identity or stage')
        modes = row.get('conditional_modes')
        require(isinstance(modes, list) and len(modes) == 4, 'complete conditional modes')
        seen = set()
        for mode in modes:
            require(all(type(mode.get(k)) is int and mode[k] in (0, 1) for k in ('window_origin_mode', 'depth_mode')), 'mode type')
            pair = (mode['window_origin_mode'], mode['depth_mode'])
            variant = mode.get('variant_sha256')
            require(pair not in seen and variant in captures[key]['variants'] and variant not in variants and
                    mode.get('contract_sha256') == variant and
                    mode.get('contract_path') == 'build/' + index['directory'] + '/' + variant + '.contract.json' and
                    digest(files[index['directory'] + '/' + variant + '.contract.json']) == variant, 'mode/variant identity')
            seen.add(pair); variants.add(variant)
        require({m['variant_sha256'] for m in modes} == set(captures[key]['variants']), 'variant coverage')
        selected[key] = row
    require(set(selected) == set(captures) and conditional.get('captures') == len(selected) and
            conditional.get('variants') == len(variants) == 4 * len(selected) and bridge.get('native_keys') == len(native_keys),
            'bridge complete admitted coverage')
    allowed = {'conditional-publication.json', 'conditional-admission.json', 'subset-proof.json',
               'base-bindings.json', 'base-formats.json', 'shader-package.json', index['directory'] + '/manifest-build.json'}
    allowed.update(index['directory'] + '/' + v + suffix for v in variants for suffix in ('.contract.json', '.json', '.metal'))
    require(set(files) == allowed, 'unexpected source package member')
    result = dict(files)
    result['native-key-bridge.json'] = bridge_raw
    result['native-source-package.json'] = canonical(dict(schema='nxnative-base-v0-source-package-v1',
        profile='botw-switch-base-v0', game_version='1.0.0', bridge_sha256=digest(bridge_raw),
        conditional_publication_sha256=index['publication_sha256'], conditional_index_sha256=digest(files['shader-package.json'])))
    return result


def publish(files, output):
    output = checked_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)  # Claim the fresh directory without replacing any existing path.
    for name, raw in sorted(files.items(), key=lambda row: row[0] == 'native-source-package.json'):
        target = output / name
        require(not Path(name).is_absolute() and all(p not in ('', '.', '..') for p in name.split('/')), 'publication path')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
        target.chmod(0o444)


def prepare(source, bridge, output):
    source, bridge, output = map(checked_path, (source, bridge, output))
    require(not (output.is_relative_to(source) or source.is_relative_to(output) or bridge.is_relative_to(output)), 'output aliases inputs')
    if output.exists():
        raise FileExistsError('source package output already exists')
    bridge_raw = read(bridge, 32 * 1024 * 1024)
    require(digest(bridge_raw) == BRIDGE_SHA256, 'fixed reviewed bridge required')
    require(digest(read(source / 'conditional-publication.json')) == PUBLICATION_SHA256, 'fixed reviewed publication required')
    with tempfile.TemporaryDirectory(prefix='nxnative-source-package-') as temporary:
        with original_validator(Path(temporary).resolve()) as (validator, ports):
            files, index = validator.shader_files(source, ports)
    result = combine_verified(files, index, bridge_raw)
    publish(result, output)
    return dict(schema='nxnative-base-v0-source-package-receipt-v1', files=len(result),
                bytes=sum(map(len, result.values())), output=str(output), bridge_sha256=digest(bridge_raw),
                reused_source_sha256=REUSE_PINS, compiled_for_metal=False, guest_or_gpu_executed=False,
                gameplay_verified=False, entire_game_shader_coverage=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'bridge', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.bridge, args.output), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'schema': 'nxnative-source-package-error-v1', 'reason': str(exc)}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
