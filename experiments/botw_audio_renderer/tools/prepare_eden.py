#!/usr/bin/env python3
"""Verify original blobs, then patch a private build copy; never mutate vendor."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--patch', action='append', default=[])
    parser.add_argument('--reuse', action='store_true', help='verify and reuse an identical private copy')
    args = parser.parse_args()
    component = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(component.resolve()) or (output.exists() and not args.reuse):
        raise ValueError('destination must be a new directory outside the source component')
    lock = json.loads((component / 'SOURCE.lock.json').read_text())
    source = component / 'vendor/eden'
    for entry in lock['files']:
        data = (source / entry['path']).read_bytes()
        if (len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256'] or
                hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != entry['git_blob']):
            raise ValueError(f"upstream identity changed: {entry['path']}")
    dependencies_path = component / 'DEPENDENCIES.lock.json'
    dependencies = json.loads(dependencies_path.read_text())
    if dependencies['eden_commit'] != lock['commit']:
        raise ValueError('dependency source commit mismatch')
    dependency_files = [(component / 'vendor/fmt' / entry['path'], entry)
                        for entry in dependencies['fmt']['files']]
    dependency_files += [(component / entry['destination'], entry)
                         for entry in dependencies['eden_additional_original_blobs']]
    for path, entry in dependency_files:
        data = path.read_bytes()
        if (len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256'] or
                hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != entry['git_blob']):
            raise ValueError(f'dependency identity changed: {path.relative_to(component)}')
    patch_paths = []
    for patch in args.patch:
        path = component / 'patches' / patch
        if path.parent != component / 'patches' or not path.is_file():
            raise ValueError('invalid local patch')
        patch_paths.append(path)
    identity = {
        'source_lock_sha256': hashlib.sha256((component / 'SOURCE.lock.json').read_bytes()).hexdigest(),
        'dependencies_lock_sha256': hashlib.sha256(dependencies_path.read_bytes()).hexdigest(),
        'patches': [{'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in patch_paths],
    }
    def files():
        return {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.rglob('*') if path.is_file() and path.name != '.prepared.json'}
    if output.exists():
        recorded = json.loads((output / '.prepared.json').read_text())
        if recorded['identity'] != identity or recorded['files'] != files():
            raise ValueError('private build copy identity changed')
        return
    shutil.copytree(source, output)
    for path in patch_paths:
        subprocess.run(['patch', '--fuzz=0', '-p1', '-i', str(path)], cwd=output, check=True)
    (output / '.prepared.json').write_text(json.dumps({'identity': identity, 'files': files()}, indent=2) + '\n')


if __name__ == '__main__':
    main()
