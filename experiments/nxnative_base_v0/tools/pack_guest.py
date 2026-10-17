"""Package the fixed six-module offline rewrite; never load or execute it."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import types

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
EMITTER = ROOT / 'scripts/botw_native/guest_framework.py'
EMITTER_SHA = 'fe6fef1ede112aa567dd39e1d285da3fc9c59403438386297c81a30f57dfb495'
RUNNER_SHA = 'e781867a076d49a79169c86689f2d6fb203b9ee4a9a08a122809f8daba39ac2e'
REPORT_SHA = 'faf9e7e0084cbe72563d89164a8908737ef059b5593200692fe5956003d3cf7e'
MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
PAGE = 0x4000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_bytes(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256 * 1024:
            raise ValueError('source must be a bounded regular file')
        raw = stream.read(256 * 1024 + 1)
    if len(raw) != info.st_size or digest(raw) != expected:
        raise ValueError('source digest differs')
    return raw


def emit_framework(modules):
    if [m.get('name') for m in modules] != list(MODULES):
        raise ValueError('exact six-module order is required')
    for module in modules:
        image, layout = module['image'], module['layout']
        text, patch = layout['TEXT_END'], layout['PATCH_OFF']
        if (type(text) is not int or type(patch) is not int or not isinstance(image, bytes)
                or not 0 < text <= ((text + PAGE - 1) & -PAGE) <= patch < len(image) <= 128 * 1024**2
                or patch % PAGE or digest(image) != module.get('image_sha256')):
            raise ValueError('invalid NEAR image layout')
    # Reuse every function of the existing generic Mach-O emitter. Its old
    # version-specific admission/Host39 policies and package imports are not run.
    names = {'_round_page', '_name16', '_segment', '_dylib_command', '_macho'}
    tree = ast.parse(source_bytes(EMITTER, EMITTER_SHA), filename=str(EMITTER))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in functions} != names or len(functions) != len(names):
        raise ValueError('complete generic emitter is absent')
    namespace = {'struct': struct, 'hashlib': hashlib, 'PAGE': PAGE, 'MAX_TOTAL_BYTES': 2 * 1024**3}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(EMITTER), 'exec'), namespace)
    return namespace['_macho'](modules, 0x1a0000)


def admit_payload(directory):
    path = COMPONENT / 'tools/rewrite_base_v0.py'
    module = types.ModuleType('_nxnative_guest_pack_rewrite_gate')
    module.__file__ = str(path)
    exec(compile(source_bytes(path, RUNNER_SHA), str(path), 'exec'), module.__dict__)
    profile, preflight, nso, source = module.admit_source()
    lock = json.loads(module.read_file(COMPONENT / 'SOURCE.lock.json'))
    raw = module.read_file(Path(__file__))
    if lock['adapter_files'].get('tools/pack_guest.py') != {'bytes': len(raw), 'sha256': digest(raw)}:
        raise ValueError('packer is not source bound')
    if lock['external_code_files'].get('scripts/botw_native/guest_framework.py', {}).get('sha256') != EMITTER_SHA:
        raise ValueError('generic emitter is not source bound')
    directory = Path(directory)
    report_raw = module.read_file(directory / 'report.json', 1024 * 1024)
    if digest(report_raw) != REPORT_SHA:
        raise ValueError('fixed offline rewrite report differs')
    report = json.loads(report_raw)
    if (report['module_order'] != list(MODULES) or report['guest_executed'] is not False
            or report['private_gates'] is not False or report['ruleset'] != 'native'
            or report['patched_tool_sha256'] != module.PATCHED_SHA):
        raise ValueError('offline rewrite contract differs')
    if digest(module.read_file(directory / 'source-lock-used.json')) != report['source_lock_sha256']:
        raise ValueError('rewrite source snapshot differs')
    preflight.inspect_exefs(directory / 'inputs', profile['modules'])
    modules, tables = [], {}
    for name in MODULES:
        original_raw = module.read_file(directory / 'inputs' / name)
        if digest(original_raw) != profile['modules'][name]['sha256']:
            raise ValueError('original module changed after admission')
        original = nso.initialized_image(original_raw, name)
        checked = module.validate_module(directory / 'rewrite', name, original_raw, original)
        if checked != report['modules'][name]:
            raise ValueError('rewrite artifact differs from fixed report')
        image = module.read_file(directory / 'rewrite' / (name + '.patched.bin'))
        modules.append({'name': name, 'image': image, 'image_sha256': checked['image_sha256'],
                        'layout': checked['layout']})
        for kind in ('fixups', 'gates', 'cuts', 'params', 'obs'):
            member = f'{name}.{kind}.tsv'
            tables[member] = module.read_file(directory / 'rewrite' / member, 1024 * 1024)
            if digest(tables[member]) != checked['artifacts'][kind + '.tsv']:
                raise ValueError('loader table changed after admission')
    return modules, tables, {**source, 'report_sha256': REPORT_SHA,
                            'generic_emitter_sha256': EMITTER_SHA}


def pack(directory, output):
    output = Path(output).absolute()
    resolved = output.resolve()
    if output.exists() or output.is_symlink() or COMPONENT == resolved or COMPONENT in resolved.parents:
        raise ValueError('output must be fresh and outside source component')
    output = resolved
    modules, tables, evidence = admit_payload(directory)
    binary = emit_framework(modules)
    output.mkdir(parents=True, exist_ok=False)
    framework = output / 'Guest.framework'
    framework.mkdir()
    (output / 'dm').mkdir()
    executable = framework / 'Guest'
    executable.write_bytes(binary)
    executable.chmod(0o444)
    info = {'CFBundleExecutable': 'Guest', 'CFBundleIdentifier': 'local.botw.nxnative.guest',
            'CFBundleInfoDictionaryVersion': '6.0', 'CFBundleName': 'Guest',
            'CFBundlePackageType': 'FMWK', 'CFBundleVersion': '1',
            'CFBundleShortVersionString': '1.0', 'MinimumOSVersion': '26.0',
            'CFBundleSupportedPlatforms': ['iPhoneOS']}
    (framework / 'Info.plist').write_bytes(plistlib.dumps(info))
    for name, raw in tables.items():
        (output / 'dm' / name).write_bytes(raw)
    report = {'schema': 'nxnative-base-v0-six-module-guest-package-v1',
              'status': 'packaged-offline', **evidence, 'module_order': list(MODULES),
              'rtld_loaded': False, 'minimum_ios': '26.0',
              'framework': {'bytes': len(binary), 'sha256': digest(binary), 'mode': '0444'},
              'modules': {m['name']: {'bytes': len(m['image']), 'sha256': digest(m['image'])} for m in modules},
              'loader_tables': {n: {'bytes': len(raw), 'sha256': digest(raw)} for n, raw in tables.items()},
              'signed': False, 'guest_executed': False, 'phone_ready': False,
              'runtime_compatibility_verified': False, 'gameplay_verified': False}
    (output / 'package-report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--rewrite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = pack(args.rewrite, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'offline Guest packaging failed: ' + type(error).__name__ + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'signed', 'guest_executed', 'phone_ready')}))


if __name__ == '__main__':
    main()
