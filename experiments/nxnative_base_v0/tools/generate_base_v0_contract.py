#!/usr/bin/env python3
"""Generate static base-v0 identity/loader contracts, without executing a Guest.

Reuse the complete admitted Guest packer and the complete existing Mach-O
chained-pointer reader (only its old 32-byte/title row is adapted to 24 bytes).
Never publish an invented complete game_profile.h or claim runtime admission.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import os
import stat
import struct
import sys
import tempfile
import types

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
ENTRY_SYMBOLS = ('_init_libc0', 'nnosInitialize', '_init_libc1', 'nninitInitializeSdkModule',
                 'nninitStartup', '_init_libc2', '_ZN2nn2os11SetHostArgcEi',
                 '_ZN2nn2os11SetHostArgvEPPc', 'nnMain')
PACKER_SHA = '03c8994dcf252ad1fe14769a34623d15df2d1dad2c4d71015fa8ec77ba6ab40b'
READER = ROOT / 'experiments/botw_upstream_host/scripts/read_swforce_registry.py'
READER_SHA = 'fb741c7f4aefec893096af7c213560a3035001e21f8e2bfe49dfab95ffdd3e86'
REGISTRY_SHA = 'dc95542c564a6602ee094ca07a0b2aff6002fb87d26eccd19dc1b767adcdeb72'
PACKAGE_SHA = 'b23d69bb23c17d43d1cc879bee2168bf1ce654ca75928cbac1b4c7d0742e815b'
PROFILE_SHA = '1269ab1df53a1a19852cb7c65dcd5da1baa49b23e96e5d691b005fc4bbf26312'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_regular(path, limit=128 * 1024**2):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('input must be regular, without symlinks')
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise ValueError('input must be a bounded regular file')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError('input changed while reading')
    return raw


def json_bytes(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out: raise ValueError('duplicate JSON key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique)


def metadata_hash(entries):
    ordered = sorted(entries, key=lambda row: (row['name'], row['why']))
    return digest(json.dumps(ordered, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode())


def read_registry24(path):
    """Keep the original complete bounds, chain, bind and code-target checks."""
    raw = read_regular(READER, 64 * 1024)
    if digest(raw) != READER_SHA:
        raise ValueError('complete registry reader source changed')
    nodes = [n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == 'read_registry']
    if len(nodes) != 1:
        raise ValueError('complete registry reader entry is absent')
    source = ast.get_source_segment(raw.decode(), nodes[0])
    replacements = [
        ("section['size'] % 32", "section['size'] % 24"),
        ('32-byte records', '24-byte records'),
        ("section['size'] // 32", "section['size'] // 24"),
        ('index * 32', 'index * 24'),
        ("        offset, _ = vm_file(address + 24, 8)\n"
         "        titles, padding = unpack('<II', offset)\n"
         "        if not titles or padding:\n"
         "            raise RegistryFormatError('invalid title mask or registry padding')\n"
         "        entries.append(dict(name=name, why=why, titles=titles))",
         '        entries.append(dict(name=name, why=why))'),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError('registry 24-byte adapter no longer matches complete reader')
        source = source.replace(old, new)
    namespace = {'Path': Path, 'struct': struct, 'hashlib': hashlib,
                 'RegistryFormatError': ValueError, 'metadata_sha256': metadata_hash}
    binary = read_regular(path)
    exec(compile(source, str(READER) + ':24-byte-adapter', 'exec'), namespace)
    with tempfile.TemporaryDirectory(prefix='nxnative-bounded-registry-') as directory:
        snapshot = Path(directory) / 'host-static-snapshot'
        snapshot.write_bytes(binary)
        snapshot.chmod(0o400)
        result = namespace['read_registry'](snapshot)
    if result['binary_sha256'] != digest(binary):
        raise ValueError('host binary changed during registry audit')
    return result


def inspect_image(image, text_end, force_names):
    """Exact complete MOD0 symbol table and both original loader RELA tables.

    Geometry mirrors 65fa loader.c parse_dynamic/relocate. Unlike the old
    one-bit-per-module policy, preserve every actual by-name relocation count.
    """
    def unpack(fmt, offset):
        size = struct.calcsize(fmt)
        if offset < 0 or offset > len(image) or size > len(image) - offset:
            raise ValueError('dynamic metadata exceeds initialized image')
        return struct.unpack_from(fmt, image, offset)
    if not isinstance(image, bytes) or not 0 < text_end <= len(image):
        raise ValueError('invalid initialized image/text geometry')
    mod0, = unpack('<I', 4)
    if image[mod0:mod0 + 4] != b'MOD0': raise ValueError('MOD0 is absent')
    dynamic = mod0 + unpack('<i', mod0 + 4)[0]
    tags = {}
    for index in range(65536):
        tag, value = unpack('<QQ', dynamic + index * 16)
        if tag == 0: break
        if tag in tags and tag in (5, 6, 10, 11, 7, 8, 23, 2, 12):
            raise ValueError('duplicate dynamic tag')
        tags[tag] = value
    else: raise ValueError('unterminated dynamic table')
    if not all(k in tags for k in (5, 6, 10, 11)) or tags[11] != 24:
        raise ValueError('unsupported dynamic symbol table')
    if tags[5] < tags[6] or (tags[5] - tags[6]) % 24:
        raise ValueError('symbol count differs from the complete upstream loader rule')
    count = (tags[5] - tags[6]) // 24
    if not 0 < count <= 1000000 or tags[10] > len(image) - tags[5]:
        raise ValueError('symbol/string table geometry exceeds bounds')
    strings = image[tags[5]:tags[5] + tags[10]]
    symbols, functions, imports = [], {}, set()
    for index in range(count):
        offset, info, other, section, value, size = unpack('<IBBHQQ', tags[6] + index * 24)
        end = strings.find(b'\0', offset)
        if offset >= len(strings) or end < 0:
            raise ValueError('symbol string exceeds table')
        name = strings[offset:end].decode('utf-8')
        symbols.append(name)
        if name and section and info & 15 == 2:
            if value % 4 or not value < text_end or size > text_end - value:
                raise ValueError('defined function entry exceeds original text')
            functions.setdefault(name, {'offset': value, 'bytes': size, 'symbol_index': index,
                                         'binding': info >> 4, 'visibility': other & 3})
        elif name and not section:
            imports.add(name)
    references, relative, total = {}, 0, 0
    for table, size in ((tags.get(7, 0), tags.get(8, 0)), (tags.get(23, 0), tags.get(2, 0))):
        if size % 24 or table > len(image) or size > len(image) - table:
            raise ValueError('relocation table geometry exceeds bounds')
        for offset in range(table, table + size, 24):
            destination, info, addend = unpack('<QQq', offset)
            if destination > len(image) or 8 > len(image) - destination:
                raise ValueError('relocation destination exceeds image')
            kind, symbol = info & 0xffffffff, info >> 32
            total += 1
            if kind == 1027:
                relative += 1
                continue
            if kind not in (257, 1025, 1026) or symbol >= count:
                raise ValueError('unsupported relocation or symbol index')
            name = symbols[symbol]
            if name in force_names:
                references[name] = references.get(name, 0) + 1
    init = tags.get(12, 0)
    if init and (init % 4 or init >= text_end):
        raise ValueError('module initializer entry exceeds original text')
    return {'symbol_count': count, 'defined_functions': functions, 'imports': sorted(imports),
            'force_reference_counts': dict(sorted(references.items())), 'relocations': total,
            'relative_relocations': relative, 'dt_init': init,
            'complete_defined_function_metadata_sha256': digest(json.dumps(functions, sort_keys=True).encode())}


def entry_contract(contracts, required=ENTRY_SYMBOLS):
    result = {}
    for name in required:
        for module in MODULES:
            row = contracts.get(module, {}).get('defined_functions', {}).get(name)
            if row is not None:
                result[name] = {'module': module, **row}
                break
        else: raise ValueError('required complete entry symbol is absent: ' + name)
    return result


def force_contract(entries, contracts):
    seen, rules = set(), []
    for entry in entries:
        if set(entry) != {'name', 'why'} or not entry['name'] or not entry['why']:
            raise ValueError('invalid complete registry entry')
        name = entry['name']
        if name in seen: raise ValueError('duplicate force registry symbol')
        seen.add(name)
        references = {module: contracts[module]['force_reference_counts'][name]
                      for module in MODULES if module in contracts and name in contracts[module]['force_reference_counts']}
        rules.append({**entry, 'references': references, 'selected': bool(references)})
    return rules


def normal_gate_contract(report):
    expected = dict(zip(MODULES, (93, 0, 0, 0, 0, 137)))
    counts = {name: row['normal_gate_count'] for name, row in report['modules'].items()}
    if counts != expected or any(type(value) is not int for value in counts.values()):
        raise ValueError('complete per-module normal gate coverage differs')
    return {'total': sum(counts.values()), 'modules': counts}


def identity_header(profile):
    if (profile.get('schema') != 'nxnative-base-v0-profile-v1' or profile.get('game_version') != '1.0.0'
            or profile.get('title_id') != '01007EF00011E000'
            or profile.get('required_modules') != list(MODULES)
            or profile.get('nvn_api') != {'major': 52, 'minor': 32}):
        raise ValueError('identity must be the fixed original six-module base-v0')
    q = json.dumps
    build = profile['modules']['main']['build_id'][:32]
    return '\n'.join([
        '/* Fixed 1.0.0 identity only; this is not a complete game_profile.h. */', '#pragma once',
        '#define NXNATIVE_BASE_V0_CONFIGURED 0', '#define DM_GAME_ID "01007EF00011E000"',
        '#define DM_GAME_NAME "The Legend of Zelda: Breath of the Wild"',
        '#define DM_GAME_PROGRAM_ID 0x01007EF00011E000ull',
        '#define DM_GAME_MODULES {' + ', '.join(q(n) for n in MODULES) + '}',
        '#define DM_GAME_NMOD 6', '#define DM_GAME_ROMFS_MARKER "Pack/Bootup_Graphics.pack"',
        '#define DM_GAME_DISPLAY_VERSIONS {' + q(build) + ', "1.0.0"}',
        '#define DM_GAME_NVN_API_MAJOR 52', '#define DM_GAME_NVN_API_MINOR 32',
        '#define NXNATIVE_BASE_V0_ORIGINAL_HEAP_BYTES 0xc2600000ull',
        '#define NXNATIVE_BASE_V0_ORIGINAL_ROOT_BYTES 0xc0600000ull', ''])


def runtime_gaps():
    return {'base_v0_configured': False, 'game_profile_h_generated': False,
            'host_guest_gpu_executed': False, 'installable': False, 'gameplay_verified': False,
            'unproven_profile_fields': ['mem_reserve', 'mem_alias', 'mem_stack', 'app_pool',
                                       'nvn_samp_base', 'nvn_ssbo_bases'],
            'pending': ['normal loader admission/force guard', 'original-logical memory port',
                        'base0 shader publication/provider', 'remaining normal GPU/thread/memstat policy'],
            'known_graphics_binding_only': {'texture_base': 32, 'texture_slots': 32, 'image_base': 288, 'image_slots': 8}}


def source_module(path, expected):
    raw = read_regular(path, 256 * 1024)
    if digest(raw) != expected: raise ValueError('source module differs from fixed digest')
    module = types.ModuleType('_nxnative_contract_source_' + Path(path).stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def checked_output(output, protected):
    """Claim a fresh directory without aliasing any admitted source/input."""
    output = Path(output).absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('output path must not contain symlinks')
    output = output.resolve()
    for path in protected:
        path = Path(path).resolve()
        if output == path or output in path.parents or path in output.parents:
            raise ValueError('output must not overlap source or input trees')
    if output.exists():
        raise ValueError('output must be fresh')
    output.mkdir(parents=True, exist_ok=False)
    return output


def generate(rewrite, guest, host_binary, host_receipt, output):
    lock_raw = read_regular(COMPONENT / 'SOURCE.lock.json', 16 * 1024**2)
    lock = json_bytes(lock_raw)
    own = read_regular(Path(__file__), 256 * 1024)
    own_name = 'tools/generate_base_v0_contract.py'
    if lock['adapter_files'].get(own_name) != {'bytes': len(own), 'sha256': digest(own)}:
        raise ValueError('contract generator is not frozen in source lock')
    if lock.get('external_code_files', {}).get(str(READER.relative_to(ROOT))) != {
            'bytes': 12881, 'sha256': READER_SHA}:
        raise ValueError('complete registry reader is not frozen in source lock')
    packer = source_module(COMPONENT / 'tools/pack_guest.py', PACKER_SHA)
    modules, tables, evidence = packer.admit_payload(rewrite)
    profile_raw = read_regular(COMPONENT / 'profile/base_v0.json', 65536)
    if digest(profile_raw) != PROFILE_SHA: raise ValueError('fixed base-v0 profile differs')
    profile = json_bytes(profile_raw)
    package_raw = read_regular(guest / 'package-report.json', 65536)
    if digest(package_raw) != PACKAGE_SHA: raise ValueError('fixed Guest package report differs')
    package = json_bytes(package_raw)
    binary = read_regular(guest / 'Guest.framework/Guest')
    if (digest(binary) != package['framework']['sha256'] or len(binary) != package['framework']['bytes']
            or binary != packer.emit_framework(modules)):
        raise ValueError('Guest signed-section payload differs from six admitted images')
    for name, raw in tables.items():
        if read_regular(guest / 'dm' / name, 1024 * 1024) != raw:
            raise ValueError('packaged loader table differs from admitted native rewrite')
    receipt_raw = read_regular(host_receipt, 32 * 1024**2)
    receipt = json_bytes(receipt_raw)
    host_raw = read_regular(host_binary)
    artifact = receipt.get('artifact', {})
    if (receipt.get('compile_link_succeeded') is not True or receipt.get('upstream_commit') != lock['upstream_commit']
            or receipt.get('host_guest_gpu_executed') is not False or artifact.get('executed') is not False
            or artifact.get('sha256') != digest(host_raw) or artifact.get('bytes') != len(host_raw)
            or Path(artifact.get('path', '')).name != host_binary.name
            or not (receipt.get('source_unchanged') is True or receipt.get('original_source_unchanged') is True)):
        raise ValueError('host binary lacks an unchanged complete compile/link receipt')
    registry = read_registry24(host_binary)
    if registry['count'] != 614 or registry['canonical_metadata_sha256'] != REGISTRY_SHA:
        raise ValueError('complete 65fa generic force registry differs')
    names = {entry['name'] for entry in registry['entries']}
    contracts = {module['name']: inspect_image(module['image'], module['layout']['TEXT_END'], names) for module in modules}
    entries = entry_contract(contracts)
    rules = force_contract(registry['entries'], contracts)
    report = json_bytes(read_regular(rewrite / 'report.json', 1024 * 1024))
    if (report['heap_bytes'], report['root_bytes'], report['private_gates']) != (0xc2600000, 0xc0600000, False):
        raise ValueError('original H/R/native gate contract differs')
    gate_contract = normal_gate_contract(report)
    gates = gate_contract['total']
    module_records = []
    for module in modules:
        name, image, layout = module['name'], module['image'], module['layout']
        text = (layout['TEXT_END'] + 0x3fff) & ~0x3fff
        padded = (len(image) + 0x3fff) & ~0x3fff
        module_records.append({'name': name, 'source': profile['modules'][name], 'image_bytes': len(image),
            'image_sha256': digest(image), 'padded_bytes': padded, 'padded_sha256': digest(image.ljust(padded, b'\0')),
            'section_sizes': [text, layout['PATCH_OFF'] - text, padded - layout['PATCH_OFF']], 'layout': layout,
            'normal_gate_count': report['modules'][name]['normal_gate_count'],
            'complete_symbol_count': contracts[name]['symbol_count'],
            'complete_function_count': len(contracts[name]['defined_functions']),
            'complete_defined_function_metadata_sha256': contracts[name]['complete_defined_function_metadata_sha256'],
            'dt_init': contracts[name]['dt_init']})
    result = {'schema': 'nxnative-base-v0-static-loader-contract-v1', 'profile_id': 'botw-switch-base-v0',
              'status': 'static-contract-generated-runtime-configuration-pending', **runtime_gaps(),
              'upstream_commit': lock['upstream_commit'], 'source_lock_sha256': digest(lock_raw),
              'source_closure': evidence['closure'], 'rewrite_report_sha256': packer.REPORT_SHA,
              'guest_package_report_sha256': PACKAGE_SHA, 'guest_framework_sha256': digest(binary),
              'host_binary_sha256': digest(host_raw), 'host_receipt_sha256': digest(receipt_raw),
              'module_order': list(MODULES), 'rtld_loaded': False, 'normal_gate_count': gates,
              'normal_gate_counts_by_module': gate_contract['modules'],
              'original_heap_bytes': 0xc2600000, 'original_root_bytes': 0xc0600000,
              'entry_symbols': entries, 'modules': module_records,
              'force_registry': {k: v for k, v in registry.items() if k != 'entries'},
              'force_rules': rules, 'selected_force_entries': sum(row['selected'] for row in rules),
              'force_relocation_count': sum(sum(row['references'].values()) for row in rules),
              'force_policy': 'Authenticate all 614 actual 24-byte entries; select only real by-name relocations; preserve exact counts per module, including multiple references. No old title masks or old Guest29 hashes.',
              'loader_guard_installed': False}
    if read_regular(COMPONENT / 'SOURCE.lock.json', 16 * 1024**2) != lock_raw:
        raise ValueError('source lock changed during contract generation')
    verifier_name = 'tools/verify_source.py'
    verifier = source_module(COMPONENT / verifier_name, lock['adapter_files'][verifier_name]['sha256'])
    result['source_closure_after'] = verifier.verify_component(COMPONENT)
    if result['source_closure_after'] != evidence['closure']:
        raise ValueError('source closure changed during contract generation')
    result['source_unchanged'] = True
    if read_regular(host_binary) != host_raw or read_regular(host_receipt, 32 * 1024**2) != receipt_raw:
        raise ValueError('host audit input changed during generation')
    output = checked_output(output, [COMPONENT, rewrite, guest, host_binary, host_receipt,
        *(ROOT / path for path in lock.get('external_code_files', {}))])
    (output / 'base_v0_identity.h').write_text(identity_header(profile))
    (output / 'base_v0_force_manifest.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('rewrite', 'guest', 'host-binary', 'host-receipt', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = generate(args.rewrite, args.guest, args.host_binary, args.host_receipt, args.output)
    except (OSError, ValueError, KeyError, TypeError, struct.error, UnicodeError) as error:
        parser.exit(1, 'base-v0 contract generation failed: ' + str(error) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'base_v0_configured', 'normal_gate_count',
        'selected_force_entries', 'force_relocation_count', 'host_guest_gpu_executed', 'installable')}, sort_keys=True))


if __name__ == '__main__':
    main()
