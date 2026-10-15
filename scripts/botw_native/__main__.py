"""Prepare and audit a fixed BOTW module set; this does not build a playable IPA."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from .archive import extract_exefs
from .nso import inspect_nso
from .rewrite import UPSTREAM_COMMIT, TOOL_SHA256, fetch_tool, rewrite_module, verify_profile

ROOT = Path(__file__).resolve().parents[2]


def write_report(output, report):
    temporary = output / 'report.json.part'
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(output / 'report.json')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--package', type=Path, help='Solid NCZ-compressed local base NSZ')
    source.add_argument('--exefs', type=Path, help='Already extracted, local ExeFS directory')
    parser.add_argument('--profile', type=Path, default=ROOT / 'profiles/botw-switch-base-v0.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rewrite', action='store_true', help='Run the pinned offline source rewriter')
    parser.add_argument('--host39', action='store_true', help='Apply the fixed base-v0 host address transport adaptation')
    parser.add_argument('--heap-policy', choices=('bounded-host-memory-v1',),
                        help='Explicit experimental base-v0 heap/root parameters within actual host memory')
    args = parser.parse_args(argv)
    if args.host39 and not args.rewrite:
        parser.error('--host39 requires --rewrite')
    if args.heap_policy and not args.host39:
        parser.error('--heap-policy requires --host39 and --rewrite')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema': 1, 'status': 'started', 'phone_ready': False, 'guest_execution_tested': False,
              'host_service_overrides_audited': False, 'graphics_implemented': False,
              'semantic_equivalence_verified': False, 'modules': [], 'rewrites': []}
    write_report(output, report)
    try:
        profile = json.loads(args.profile.read_text())
        report['profile'] = {k: v for k, v in profile.items() if k != 'modules'}
        if args.package:
            package = args.package.resolve()
            stat = package.stat()
            identity = {'path': str(package), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            exefs = output / 'ExeFS'
            extraction_record = output / 'extraction-report.json'
            if exefs.exists():
                if not extraction_record.exists() or json.loads(extraction_record.read_text()).get('source') != identity:
                    raise ValueError('Existing ExeFS has different provenance; select a fresh output directory or --exefs')
            else:
                print('Extracting ExeFS; preserving the source package', flush=True)
                extraction = extract_exefs(package, exefs)
                extraction['source'] = identity
                extraction_record.write_text(json.dumps(extraction, indent=2) + '\n')
        else:
            exefs = args.exefs.resolve()
        modules = [inspect_nso(p.read_bytes(), p.name) for p in sorted(exefs.iterdir())
                   if p.is_file() and re.fullmatch(r'main|rtld|sdk|subsdk\d+', p.name)]
        verify_profile(modules, profile)
        if args.host39:
            verify_profile(modules, json.loads((ROOT / 'profiles/botw-switch-base-v0.json').read_text()))
        report['modules'] = modules
        all_exports = {symbol for module in modules for symbol in module['exports']}
        all_imports = {symbol for module in modules for symbol in module['imports']}
        report['unresolved_imports'] = sorted(all_imports - all_exports)
        report['status'] = 'audited'
        for module in modules:
            print(f"Audited {module['name']}: {len(module['imports'])} imports, {len(module['exports'])} exports", flush=True)
        write_report(output, report)
        if args.rewrite:
            tool = fetch_tool(ROOT / '.tools/botw-native')
            extensions = ['branch_tables.py', 'rewriter_driver.py']
            if args.host39:
                extensions.extend(('hipc_address.py', 'nso.py', 'rewrite.py'))
            if args.heap_policy:
                extensions.append('heap_policy.py')
            report['rewriter'] = {'upstream_commit': UPSTREAM_COMMIT, 'sha256': TOOL_SHA256,
                                  'ruleset': 'native', 'private_gates': False,
                                  'local_extension_sha256': {
                                      name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                      for name in extensions}}
            for module in modules:
                print('Rewriting ' + module['name'], flush=True)
                result = rewrite_module(tool, exefs / module['name'], output / 'rewrite', host39=args.host39,
                                        heap_policy=bool(args.heap_policy))
                report['rewrites'].append(result)
                print(module['name'] + ': ' + result['status'], flush=True)
                write_report(output, report)
            report['status'] = 'rewrite_failed' if any(r['status'] == 'failed' for r in report['rewrites']) else 'rewritten'
            if args.host39 and report['status'] == 'rewritten':
                from .guest_framework import HOST39_POLICY
                report['host_address_policy'] = HOST39_POLICY.copy()
                if args.heap_policy:
                    from .heap_policy import POLICY
                    report['guest_heap_policy'] = POLICY.copy()
        write_report(output, report)
        print('Report: ' + str(output / 'report.json'), flush=True)
        return 1 if report['status'] == 'rewrite_failed' else 0
    except (ValueError, OSError, KeyError) as exc:
        report.update(status='failed', error=str(exc))
        write_report(output, report)
        print('Preflight failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
