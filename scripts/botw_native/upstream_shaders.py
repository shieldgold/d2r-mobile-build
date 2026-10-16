"""Pinned, offline BotW shader build module and bounded capture adapter.

This side-by-side tool does not change the active graphics bridge. Unsupported
control versions and unknown translator environment facts remain rejected.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

VENDOR = Path(__file__).resolve().parents[2] / 'native/botw_resources/shader_toolchain'
COMMIT = '21f026e0c7fd9fee575a1bfa8d925553bb4db88e'
STAGES = {'vertex': 0, 'fragment': 1, 'geometry': 2, 'tess_control': 3,
          'tess_evaluation': 4, 'compute': 5}
HEX64 = re.compile(r'[0-9a-f]{64}')
_tool = None


def _read(path, limit, *, directory_fd=None):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError(f'cannot open regular input {path}: {exc.strerror}') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= limit:
            raise ValueError(f'input is not a bounded regular file: {path}')
        result = bytearray()
        while len(result) <= info.st_size:
            block = os.read(fd, min(1024 * 1024, info.st_size + 1 - len(result)))
            if not block:
                break
            result.extend(block)
        if len(result) != info.st_size:
            raise ValueError(f'input size changed while reading: {path}')
        return bytes(result)
    finally:
        os.close(fd)


def _json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f'duplicate JSON key: {key}')
            out[key] = value
        return out
    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('non-finite JSON number')
        return result
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_float=number,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('input is not strict UTF-8 JSON') from exc


def verify_vendor():
    metadata = _json(_read(VENDOR / 'UPSTREAM.json', 1024 * 1024))
    if metadata.get('schema') != 1 or metadata.get('commit') != COMMIT:
        raise ValueError('unexpected pinned shader toolchain identity')
    files = metadata.get('files')
    if not isinstance(files, dict) or len(files) != 374:
        raise ValueError('invalid pinned shader toolchain file manifest')
    if (not isinstance(metadata.get('git_blobs'), dict) or
            set(metadata['git_blobs']) != set(files)):
        raise ValueError('incomplete pinned Git blob manifest')
    for name, digest in files.items():
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or not isinstance(digest, str)
                or not HEX64.fullmatch(digest)):
            raise ValueError('unsafe shader toolchain source manifest')
        path = VENDOR / relative
        if not path.resolve().is_relative_to(VENDOR.resolve()):
            raise ValueError('shader toolchain source escapes vendor directory')
        data = _read(path, 8 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f'pinned shader toolchain SHA256 mismatch: {name}')
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if metadata.get('git_blobs', {}).get(name) != blob:
            raise ValueError(f'pinned shader toolchain Git blob mismatch: {name}')
    return {'commit': COMMIT, 'verified_files': len(files)}


def load_tool():
    global _tool
    verify_vendor()
    if _tool is None:
        path = VENDOR / 'dm/gfx/tools/botw_shader_build.py'
        spec = importlib.util.spec_from_file_location('_botw_pinned_shader_build', path)
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        _tool = module
    return _tool


def export_captures(inputs_directory, destination, *, max_records=100000,
                    max_total_bytes=512 * 1024 * 1024):
    """Convert our hash-bound extraction records to the unmodified capture schema.

    Every source is checked before a fresh output directory is created. Rejected
    control profiles produce an explicit report and no runnable capture. Outputs
    follow upstream's outside-Git policy, including for ignored directories.
    """
    tool = load_tool()
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    try:
        tool.require_outside_git(str(destination), 'capture output')
    except tool.ToolError as exc:
        raise ValueError(str(exc)) from exc
    if max_records < 1 or max_total_bytes < 1:
        raise ValueError('invalid capture budget')
    try:
        root_fd = os.open(inputs_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError('cannot open regular extraction directory') from exc
    try:
        raw_manifest = _read('inputs.json', 64 * 1024 * 1024, directory_fd=root_fd)
        manifest = _json(raw_manifest)
        rows = manifest.get('records') if isinstance(manifest, dict) else None
        if (not isinstance(manifest, dict) or manifest.get('schema') != 1 or not isinstance(rows, list)
                or not 1 <= len(rows) <= max_records):
            raise ValueError('invalid extraction manifest or record budget')
        captures, rejected, seen_files, used = {}, [], set(), 0
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get('stage') not in STAGES:
                raise ValueError(f'invalid stage in extraction record {index}')
            payloads = {}
            for kind, suffix, limit in [('control', '.control', tool.CONTROL_LIMIT),
                                         ('code', '.nvn', tool.CODE_LIMIT)]:
                digest, name = row.get(kind + '_sha256'), row.get(kind + '_file')
                if (not isinstance(digest, str) or not HEX64.fullmatch(digest)
                        or name != digest + suffix):
                    raise ValueError(f'invalid {kind} filename or SHA256 in record {index}')
                data = _read(name, limit, directory_fd=root_fd)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f'{kind} SHA256 mismatch in record {index}')
                if name not in seen_files:
                    used += len(data)
                    seen_files.add(name)
                if used > max_total_bytes:
                    raise ValueError('extraction exceeds total byte budget')
                payloads[kind] = data
            try:
                fields = tool.parse_control(payloads['control'])
                if fields['stage'] != STAGES[row['stage']]:
                    raise ValueError(f'control stage differs from extraction record {index}')
                if len(payloads['code']) != fields['code_size']:
                    raise tool.CaptureError('code size differs from control profile')
                tool.check_code(fields, payloads['code'])
            except tool.CaptureError as exc:
                rejected.append({'index': index, 'reason': str(exc), 'source': row})
                continue
            digest = tool.capture_digest(fields, payloads['control'], payloads['code'])
            key = digest[:16]
            if key in captures:
                if captures[key]['sha256'] != digest:
                    raise ValueError('capture key collision')
                captures[key]['sources'].append(row)
            else:
                captures[key] = {'key': key, 'sha256': digest, 'fields': fields,
                                 'sources': [row], **payloads}
    finally:
        os.close(root_fd)
    report = {'schema': 1, 'upstream_commit': COMMIT,
              'source_manifest_sha256': hashlib.sha256(raw_manifest).hexdigest(),
              'input_records': len(rows), 'unique_source_bytes': used,
              'exported': len(captures), 'rejected': rejected, 'captures': []}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    for key, entry in sorted(captures.items()):
        for kind in ('control', 'code'):
            tool.write_atomic(str(destination / (key + '.' + kind)), entry[kind])
        text = tool.canonical_capture_json(key, entry['sha256'], entry['fields'],
                                          hashlib.sha256(entry['control']).hexdigest(),
                                          hashlib.sha256(entry['code']).hexdigest())
        tool.write_atomic(str(destination / (key + '.json')), text)
        report['captures'].append({k: v for k, v in entry.items() if k not in ('control', 'code')})
    tool.write_json(str(destination / 'capture-sources.json'), report)
    return report


def strict_release_build(inputs_directory, captures_directory, facts_directory,
                         derive_report_path, evidence_path, destination, *,
                         xlate=None, spirv_cross=None, jobs=1):
    """Admit and snapshot all inputs before the complete upstream build runs.

    No probe/default/merge/subset escape is provided by this release entry point.
    Upstream still checks translator source identity and every emitted resource.
    Its raw CLI remains available separately for research.
    """
    from .shader_bindings import prepare_release
    if type(jobs) is not int or not 1 <= jobs <= 256:
        raise ValueError('release jobs must be an integer in 1..256')
    tool = load_tool()
    destination = Path(destination)
    prepare_release(tool, inputs_directory, captures_directory, facts_directory,
                    derive_report_path, evidence_path, destination)
    arguments = ['build', str(destination / 'captures'), '--facts', str(destination / 'facts'),
                 '--out', str(destination / 'build'), '--jobs', str(jobs)]
    for flag, value in (('--xlate', xlate), ('--spirv-cross', spirv_cross)):
        if value is not None:
            arguments.extend((flag, str(value)))
    return tool.main(arguments)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('verify-source', help='verify all pinned public source hashes')
    export = commands.add_parser('export', help='export hash-bound extraction inputs to fresh captures')
    export.add_argument('inputs'); export.add_argument('destination')
    tool = commands.add_parser('tool', help='raw upstream build CLI; research results are not release authority')
    tool.add_argument('arguments', nargs=argparse.REMAINDER)
    for name in ('derive', 'extract'):
        raw = commands.add_parser('raw-' + name, help=f'run the complete upstream {name} research CLI')
        raw.add_argument('arguments', nargs=argparse.REMAINDER)
    for name in ('strict-check', 'strict-prepare', 'strict-build'):
        strict = commands.add_parser(name, help='require hash-bound base-v0 facts review for the whole batch')
        for argument in ('inputs', 'captures', 'facts', 'derive_report', 'evidence'):
            strict.add_argument(argument)
        if name != 'strict-check':
            strict.add_argument('destination')
        if name == 'strict-build':
            strict.add_argument('--xlate')
            strict.add_argument('--spirv-cross')
            strict.add_argument('--jobs', type=int, default=1)
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify-source':
            print(json.dumps(verify_vendor(), sort_keys=True))
            return 0
        if args.command == 'export':
            report = export_captures(args.inputs, args.destination)
            print(json.dumps({'input_records': report['input_records'], 'exported': report['exported'],
                              'rejected': len(report['rejected']),
                              'report': str(Path(args.destination) / 'capture-sources.json')}, sort_keys=True))
            return int(bool(report['rejected']))
        if args.command.startswith('strict-'):
            from . import shader_bindings as gate
            inputs = (args.inputs, args.captures, args.facts, args.derive_report, args.evidence)
            if args.command == 'strict-build':
                return strict_release_build(*inputs, args.destination, xlate=args.xlate,
                                            spirv_cross=args.spirv_cross, jobs=args.jobs)
            if args.command == 'strict-prepare':
                result = gate.prepare_release(load_tool(), *inputs, args.destination)
            else:
                result = gate.check_release(load_tool(), *inputs)
            print(json.dumps({'approved': result['approved'], 'profile': result['profile'],
                              'captures': len(result['captures']),
                              'input_manifest_sha256': result['input_manifest_sha256']}, sort_keys=True))
            return 0
        arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
        if args.command in ('raw-extract', 'raw-derive'):
            verify_vendor()
            script = VENDOR / ('dm/gfx/tools/botw_shader_' + args.command[4:] + '.py')
            return subprocess.run([sys.executable, '-B', str(script), *arguments], check=False).returncode
        return load_tool().main(arguments)
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
