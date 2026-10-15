"""Package audited local BOTW images into an unsigned iOS Guest.framework.

The pinned upstream DM_SHELL loader uses getsectiondata, not Mach-O exports:
__TEXT,__dm<name>x0 / __DM<name>W,__img / __TEXT,__dm<name>x1 are remapped
into each module's original address layout. NSO symbols remain in those bytes.
This tool never loads the dylib or executes guest instructions or initializers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import struct
import sys
import tempfile

from .rewrite import UPSTREAM_COMMIT

PAGE = 0x4000
MODULE_NAMES = ('main', 'rtld', 'sdk', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3')
TABLE_KINDS = ('fixups', 'gates', 'sites', 'cuts', 'obs', 'params')
KEYS = ('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI')
MAX_IMAGE_BYTES = 512 * 1024**2
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 16 * 1024**2


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _round_page(value):
    return (value + PAGE - 1) & ~(PAGE - 1)


def _read_file(path, limit):
    """Read one bounded, regular, non-symlink file into an immutable snapshot."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Input must be a regular non-symlink file: {path}')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f'Input exceeds regular file size limit: {path.name}')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(data) > limit or len(data) != before.st_size or
            (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
        raise ValueError(f'Input changed while reading: {path.name}')
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate report field: ' + key)
        result[key] = value
    return result


def _module_records(report):
    if not isinstance(report, dict) or report.get('schema') != 1 or report.get('status') != 'rewritten':
        raise ValueError('A completed schema 1 rewritten preflight report is required')
    rewriter = report.get('rewriter', {})
    if not isinstance(rewriter, dict) or (rewriter.get('upstream_commit') != UPSTREAM_COMMIT or
            rewriter.get('ruleset') != 'native' or rewriter.get('private_gates') is not False):
        raise ValueError('Report must use the pinned generic native rewriter without private gates')
    records = []
    for key in ('modules', 'rewrites'):
        entries = report.get(key)
        if not isinstance(entries, list) or len(entries) != len(MODULE_NAMES):
            raise ValueError('Report must contain all seven unique BOTW ' + key)
        mapping = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('name') not in MODULE_NAMES or entry['name'] in mapping:
                raise ValueError('Duplicate or unsafe module name in ' + key)
            mapping[entry['name']] = entry
        records.append(mapping)
    for name, record in records[0].items():
        for key in ('sha256', 'build_id'):
            if not isinstance(record.get(key), str) or not re.fullmatch('[0-9a-f]{64}', record[key]):
                raise ValueError('Invalid source module ' + key + ': ' + name)
        rewrite = records[1][name]
        if (rewrite.get('status') != 'rewritten' or
                not isinstance(rewrite.get('image_sha256'), str) or
                not re.fullmatch('[0-9a-f]{64}', rewrite['image_sha256'])):
            raise ValueError('Incomplete rewrite or invalid image digest: ' + name)
        if rewrite.get('generic_tables') != {'cuts': 0, 'obs': 0, 'params': 0}:
            raise ValueError('Report includes non-generic rewrite tables: ' + name)
    return records


def _text(data, label):
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Invalid UTF-8 table: ' + label) from exc


def _layout(data, module, image_size):
    result = {}
    expected = {'PATCH_OFF', 'KEY_PAGE_OFF', 'KEY_PAGE_SIZE', 'TEXT_END', 'RO_OFF', 'DATA_OFF'}
    expected.update(f'KEY_{key}_IDX' for key in KEYS)
    for line in _text(data, 'fixups').splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] != '#' or fields[1] not in expected or fields[1] in result:
            raise ValueError('Invalid, duplicate or old-format fixups header: ' + module['name'])
        key, value = fields[1:]
        if key.endswith('_IDX') and not re.fullmatch(r'(0|[1-9][0-9]*)', value):
            raise ValueError('Key indices must be decimal for the upstream loader')
        try:
            result[key] = int(value, 10 if key.endswith('_IDX') else 0)
        except ValueError as exc:
            raise ValueError('Invalid fixups integer: ' + key) from exc
    if set(result) != expected:
        raise ValueError('Incomplete fixups layout: ' + module['name'])
    text, patch = result['TEXT_END'], result['PATCH_OFF']
    key, size = result['KEY_PAGE_OFF'], result['KEY_PAGE_SIZE']
    if not 0 < text <= _round_page(text) <= key < key + size <= patch < image_size:
        raise ValueError('Overlapping or out-of-bounds image/key layout: ' + module['name'])
    if patch % PAGE or key % PAGE or size != PAGE:
        raise ValueError('Unaligned patch or key page: ' + module['name'])
    indices = [result[f'KEY_{name}_IDX'] for name in KEYS]
    if len(set(indices)) != len(KEYS) or any(index < 0 or index >= PAGE // 8 for index in indices):
        raise ValueError('Duplicate or out-of-range key index: ' + module['name'])
    segments = module.get('segments')
    if not isinstance(segments, list) or len(segments) != 3:
        raise ValueError('Audited NSO segment metadata is required')
    memory = module.get('memory_bytes')
    if type(memory) is not int or not 0 < memory <= key:
        raise ValueError('Invalid audited module memory bounds')
    previous = 0
    for segment, kind in zip(segments, ('text', 'rodata', 'data')):
        if (not isinstance(segment, dict) or segment.get('name') != kind or
                type(segment.get('address')) is not int or type(segment.get('bytes')) is not int):
            raise ValueError('Invalid audited NSO segment metadata')
        address, count = segment['address'], segment['bytes']
        if address < previous or count <= 0 or address + count > memory:
            raise ValueError('Audited NSO segments overlap or exceed memory bounds')
        previous = address + count
    if (segments[0]['address'] != 0 or segments[0]['bytes'] != text or
            segments[1]['address'] != result['RO_OFF'] or segments[2]['address'] != result['DATA_OFF'] or
            _round_page(memory) != key):
        raise ValueError('Fixups layout does not match audited NSO segments')
    return result


def _audit_tables(files, image, layout, rewrite):
    name = rewrite['name']
    text, patch = layout['TEXT_END'], layout['PATCH_OFF']
    key = layout['KEY_PAGE_OFF']
    if any(image[key:key + PAGE]):
        raise ValueError('Expected an uninitialized zero key page: ' + name)
    for kind in ('cuts', 'obs', 'params'):
        if any(row.strip() and not row.startswith('#') for row in _text(files[kind], kind).splitlines()):
            raise ValueError('Unexpected game-specific ' + kind + ': ' + name)
    sites = set()
    for row in _text(files['sites'], 'sites').splitlines():
        fields = row.split('\t')
        try:
            address = int(fields[0], 0)
        except ValueError as exc:
            raise ValueError('Invalid rewrite site address: ' + name) from exc
        if len(fields) < 2 or row in sites or address % 4 or not 0 <= address < text or address + 4 > len(image):
            raise ValueError('Duplicate or out-of-bounds rewrite site: ' + name)
        word = struct.unpack_from('<I', image, address)[0]
        immediate = word & 0x3ffffff
        if immediate & 0x2000000:
            immediate -= 0x4000000
        target = address + immediate * 4
        if word & 0xfc000000 != 0x14000000 or not patch <= target < len(image):
            raise ValueError('Rewrite branch leaves the patch region: ' + name)
        # A site can also carry a jump-table classification (x18 and J rows).
        sites.add(row)
    gates = set()
    for row in _text(files['gates'], 'gates').splitlines():
        fields = row.split('\t')
        if len(fields) != 4 or not re.fullmatch('[a-zA-Z0-9_]{1,15}', fields[1]):
            raise ValueError('Invalid gate table: ' + name)
        try:
            ret, argument, original = int(fields[0], 0), int(fields[2], 10), int(fields[3], 0)
        except ValueError as exc:
            raise ValueError('Invalid gate table integer: ' + name) from exc
        if (ret in gates or ret % 4 or original % 4 or not patch < ret <= len(image) or
                not 0 <= original < text or not -(2**31) <= argument < 2**31):
            raise ValueError('Duplicate or out-of-bounds host gate: ' + name)
        gates.add(ret)
    structure = rewrite.get('structure', {})
    if (rewrite.get('rewrite_site_count') != len(sites) or
            structure.get('branch_sites_checked') != len(sites) or
            structure.get('host_gate_addresses_checked') != len(gates)):
        raise ValueError('Rewrite table counts differ from the audited report: ' + name)


def _name16(name):
    encoded = name.encode('ascii')
    if len(encoded) > 16:
        raise ValueError('Mach-O segment or section name is too long')
    return encoded.ljust(16, b'\0')


def _segment(name, address, size, fileoff, filesize, protection, sections):
    command = struct.pack('<II16sQQQQiiII', 0x19, 72 + 80 * len(sections), _name16(name),
                          address, size, fileoff, filesize, protection, protection, len(sections), 0)
    for section, offset, count, flags in sections:
        command += struct.pack('<16s16sQQIIIIIIII', _name16(section), _name16(name),
                               offset, count, offset, 14, 0, 0, flags, 0, 0, 0)
    return command


def _dylib_command(command, name):
    encoded = name.encode('ascii') + b'\0'
    encoded = encoded.ljust((len(encoded) + 7) & ~7, b'\0')
    return struct.pack('<IIIIII', command, 24 + len(encoded), 24, 2, 0x10000, 0x10000) + encoded


def _macho(modules, minimum):
    text_sections, writable, body = [], [], bytearray()
    offset = PAGE
    for module in modules:
        name, image, layout = module['name'], module['image'], module['layout']
        split, patch = _round_page(layout['TEXT_END']), layout['PATCH_OFF']
        end = _round_page(len(image))
        for suffix, start, finish in (('x0', 0, split), ('x1', patch, end)):
            section = image[start:finish].ljust(finish - start, b'\0')
            text_sections.append((f'__dm{name}{suffix}', offset, len(section), 0x80000400))
            body.extend(section)
            offset += len(section)
        writable.append((name, image[split:patch]))
    commands = [_segment('__TEXT', 0, offset, 0, offset, 5, text_sections)]
    for name, data in writable:
        commands.append(_segment(f'__DM{name}W', offset, len(data), offset, len(data), 3,
                                 [('__img', offset, len(data), 0)]))
        body.extend(data)
        offset += len(data)
    string_table = bytes(8)
    commands.extend([
        _segment('__LINKEDIT', offset, PAGE, offset, len(string_table), 1, []),
        _dylib_command(0xd, '@rpath/Guest.framework/Guest'),
        struct.pack('<II10I', 0x80000022, 48, *([0] * 10)),
        struct.pack('<IIIIII', 2, 24, offset, 0, offset, len(string_table)),
        struct.pack('<II18I', 0xb, 80, *([0] * 18)),
        struct.pack('<II16s', 0x1b, 24, hashlib.sha256(body).digest()[:16]),
        struct.pack('<IIIIII', 0x32, 24, 2, minimum, minimum, 0),
        _dylib_command(0xc, '/usr/lib/libSystem.B.dylib')])
    load_commands = b''.join(commands)
    if 32 + len(load_commands) > PAGE or offset + len(string_table) > MAX_TOTAL_BYTES:
        raise ValueError('Framework exceeds Mach-O size or header bounds')
    header = struct.pack('<IiiIIIII', 0xfeedfacf, 0x0100000c, 0, 6, len(commands),
                         len(load_commands), 0x00100085, 0)
    return (header + load_commands).ljust(PAGE, b'\0') + body + string_table


def build_framework(report_path, rewrite_dir, output_dir, *, module_names=None,
                    bundle_id='local.botw.guest', minimum_ios='26.0'):
    """Atomically publish Guest.framework, dm loader tables, and a digest report.

    output_dir must not exist. Default includes all seven modules; module_names
    can select the host's six non-rtld modules. The report is returned as a dict.
    Output is unsigned and does not assert service, graphics or phone readiness.
    """
    report_path, rewrite_dir, output_dir = map(Path, (report_path, rewrite_dir, output_dir))
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError('Output already exists: ' + str(output_dir))
    if rewrite_dir.is_symlink() or not rewrite_dir.is_dir():
        raise ValueError('Rewrite directory must be a regular non-symlink directory')
    if not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', bundle_id):
        raise ValueError('Invalid framework bundle identifier')
    if not isinstance(minimum_ios, str) or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', minimum_ios):
        raise ValueError('Invalid minimum iOS version')
    version = [int(piece) for piece in minimum_ios.split('.')]
    version += [0] * (3 - len(version))
    if not 1 <= version[0] <= 65535 or any(not 0 <= piece <= 255 for piece in version[1:]):
        raise ValueError('Minimum iOS version exceeds Mach-O version bounds')
    minimum = version[0] << 16 | version[1] << 8 | version[2]
    report_bytes = _read_file(report_path, MAX_METADATA_BYTES)
    report = json.loads(_text(report_bytes, 'report'), object_pairs_hook=_unique_object)
    originals, rewrites = _module_records(report)
    names = list(MODULE_NAMES if module_names is None else module_names)
    if not names or len(set(names)) != len(names) or any(name not in MODULE_NAMES for name in names):
        raise ValueError('Select unique known BOTW module names')
    sources = [{'path': 'report.json', 'bytes': len(report_bytes), 'sha256': _digest(report_bytes)}]
    modules, total = [], 0
    for name in names:
        image = _read_file(rewrite_dir / f'{name}.patched.bin', MAX_IMAGE_BYTES)
        total += len(image)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Aggregate rewritten image size exceeds limit')
        if _digest(image) != rewrites[name]['image_sha256']:
            raise ValueError('Rewritten image SHA256 mismatch: ' + name)
        files = {'patched.bin': image}
        files.update({kind: _read_file(rewrite_dir / f'{name}.{kind}.tsv', MAX_METADATA_BYTES) for kind in TABLE_KINDS})
        layout = _layout(files['fixups'], originals[name], len(image))
        _audit_tables(files, image, layout, rewrites[name])
        for kind, data in files.items():
            extension = kind if kind == 'patched.bin' else kind + '.tsv'
            sources.append({'path': f'rewrite/{name}.{extension}', 'bytes': len(data), 'sha256': _digest(data)})
        modules.append({'name': name, 'image': image, 'files': files, 'layout': layout})
    binary = _macho(modules, minimum)
    info = plistlib.dumps({'CFBundleExecutable': 'Guest', 'CFBundleIdentifier': bundle_id,
                          'CFBundleInfoDictionaryVersion': '6.0', 'CFBundleName': 'Guest',
                          'CFBundlePackageType': 'FMWK', 'CFBundleShortVersionString': '1.0',
                          'CFBundleVersion': '1', 'MinimumOSVersion': minimum_ios,
                          'CFBundleSupportedPlatforms': ['iPhoneOS']}, sort_keys=True)
    sources.sort(key=lambda entry: entry['path'])
    result = {'schema': 1, 'status': 'framework_packaged', 'unsigned': True, 'phone_ready': False,
              'guest_execution_tested': False, 'graphics_implemented': False,
              'host_service_overrides_audited': False, 'semantic_equivalence_verified': False,
              'loader_contract': 'DM_SHELL/getsectiondata', 'upstream_commit': UPSTREAM_COMMIT,
              'source_digest_sha256': _digest(json.dumps(sources, sort_keys=True, separators=(',', ':')).encode()),
              'sources': sources, 'framework': {'path': 'Guest.framework/Guest',
                                               'bytes': len(binary), 'sha256': _digest(binary)},
              'info_plist_sha256': _digest(info),
              'modules': [{'name': m['name'], 'source_sha256': originals[m['name']]['sha256'],
                           'build_id': originals[m['name']]['build_id'],
                           'image_sha256': rewrites[m['name']]['image_sha256'],
                           'image_bytes': len(m['image']), 'layout': m['layout']} for m in modules]}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{output_dir.name}.tmp-', dir=output_dir.parent))
    try:
        framework = staging / 'Guest.framework'
        framework.mkdir()
        (framework / 'Guest').write_bytes(binary)
        (framework / 'Guest').chmod(0o755)
        (framework / 'Info.plist').write_bytes(info)
        (staging / 'dm').mkdir()
        for module in modules:
            for kind in ('fixups', 'gates'):
                (staging / 'dm' / f'{module["name"]}.{kind}.tsv').write_bytes(module['files'][kind])
        (staging / 'guest-framework-report.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError('Output appeared during build: ' + str(output_dir))
        staging.rename(output_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--rewrite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--modules', nargs='+', choices=MODULE_NAMES)
    parser.add_argument('--bundle-id', default='local.botw.guest')
    parser.add_argument('--minimum-ios', default='26.0')
    args = parser.parse_args(argv)
    try:
        result = build_framework(args.report, args.rewrite, args.output, module_names=args.modules,
                                 bundle_id=args.bundle_id, minimum_ios=args.minimum_ios)
    except (OSError, ValueError) as exc:
        print('Guest framework packaging failed: ' + str(exc), file=sys.stderr)
        return 1
    print(f'Packaged {args.output / "Guest.framework"}: {result["framework"]["bytes"]} bytes; unsigned, guest not executed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
