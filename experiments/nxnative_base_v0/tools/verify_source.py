#!/usr/bin/env python3
"""Verify the original upstream tree without executing any upstream code."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import re
import stat

COMMIT = '65fa9caab486768f70da98c51c2421aff76f676d'
TREE = '5b1545c089970a2aca0325aa31fa71bce217813f'
REPOSITORY = 'https://github.com/seedds/nxnative'
COMPONENT = Path(__file__).resolve().parents[1]
MAX_MEMBER = 64 * 1024**2


def relative_name(name):
    if not isinstance(name, str) or not name or '\0' in name:
        raise ValueError('invalid source path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('noncanonical source path')
    return path


def regular_file(root, name):
    path = root
    if root.is_symlink() or not root.is_dir():
        raise ValueError('source root must be a real directory')
    parts = relative_name(name).parts
    for index, part in enumerate(parts):
        path = path / part
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            raise ValueError('source member is missing: ' + name) from error
        expected = stat.S_ISREG if index == len(parts) - 1 else stat.S_ISDIR
        if not expected(mode):
            raise ValueError('source member is not regular: ' + name)
    return path


def object_hash(kind, payload):
    return hashlib.sha1(kind + b' ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()


def verify_tree(root, records, expected_tree):
    root = Path(root)
    if not isinstance(records, dict) or not records or not re.fullmatch('[0-9a-f]{40}', expected_tree):
        raise ValueError('invalid source tree metadata')
    # Validate all declared names before opening any member.
    for name in records:
        relative_name(name)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('source root must be a real directory')
    actual = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        for child in directories:
            if not stat.S_ISDIR((Path(directory) / child).lstat().st_mode):
                raise ValueError('source directory is not regular')
        for child in files:
            actual.add(str((Path(directory) / child).relative_to(root)))
    if actual != set(records):
        raise ValueError('source file set differs from lock')
    nodes = {}
    for name, row in records.items():
        if not isinstance(row, dict) or row.get('git_mode') not in ('100644', '100755'):
            raise ValueError('invalid Git mode metadata')
        if type(row.get('bytes')) is not int or not 0 <= row['bytes'] <= MAX_MEMBER:
            raise ValueError('invalid source member size')
        path = regular_file(root, name)
        if path.stat().st_size != row['bytes']:
            raise ValueError('source size differs from lock: ' + name)
        raw = path.read_bytes()
        blob = object_hash(b'blob', raw)
        git_mode = '100755' if path.stat().st_mode & stat.S_IXUSR else '100644'
        if (type(row.get('bytes')) is not int or row['bytes'] != len(raw)
                or row.get('sha256') != hashlib.sha256(raw).hexdigest()
                or row.get('git_blob_sha1') != blob or row['git_mode'] != git_mode):
            raise ValueError('source content or mode differs from lock: ' + name)
        node = nodes
        parts = relative_name(name).parts
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError('source path conflicts with a file')
        if parts[-1] in node:
            raise ValueError('source path conflicts with a directory')
        node[parts[-1]] = (git_mode, blob)

    def tree_hash(node):
        # Git sorts a directory name as if it ended with '/'.
        ordered = sorted(node, key=lambda name: name.encode() + (b'/' if isinstance(node[name], dict) else b''))
        entries = []
        for name in ordered:
            value = node[name]
            mode, digest = ('40000', tree_hash(value)) if isinstance(value, dict) else value
            entries.append(mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(digest))
        return object_hash(b'tree', b''.join(entries))

    digest = tree_hash(nodes)
    if digest != expected_tree:
        raise ValueError('source Git tree differs from pinned tree')
    return digest


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate source lock key')
        result[key] = value
    return result


def read_lock(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('source lock must be a regular file')
    lock = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if (not isinstance(lock, dict) or lock.get('schema') != 'nxnative-base-v0-source-v1'
            or lock.get('upstream_repository') != REPOSITORY
            or lock.get('upstream_commit') != COMMIT or lock.get('upstream_tree') != TREE):
        raise ValueError('unexpected upstream source identity')
    return lock


def verify_digest_files(root, records, kind):
    for name, row in records.items():
        if (not isinstance(row, dict) or type(row.get('bytes')) is not int
                or not 0 <= row['bytes'] <= MAX_MEMBER
                or not isinstance(row.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('invalid ' + kind + ' metadata')
        path = regular_file(root, name)
        if path.stat().st_size != row['bytes']:
            raise ValueError(kind + ' size differs from lock: ' + name)
        raw = path.read_bytes()
        if row['sha256'] != hashlib.sha256(raw).hexdigest() or row['bytes'] != len(raw):
            raise ValueError(kind + ' differs from lock: ' + name)


def verify_component(component=COMPONENT):
    component = Path(component)
    lock = read_lock(component / 'SOURCE.lock.json')
    digest = verify_tree(component / 'upstream', lock['upstream_files'], TREE)
    adapters = lock.get('adapter_files', {})
    if not isinstance(adapters, dict):
        raise ValueError('invalid adapter metadata')
    for name in adapters:
        relative_name(name)
        if name == 'SOURCE.lock.json' or name.startswith('upstream/'):
            raise ValueError('adapter path overlaps source identity')
    verify_digest_files(component, adapters, 'adapter')
    external = lock.get('external_code_files', {})
    if not isinstance(external, dict):
        raise ValueError('invalid external code metadata')
    repository = component.parents[1]
    source_lock = (component / 'SOURCE.lock.json').relative_to(repository).as_posix()
    for name in external:
        relative_name(name)
        if name in ('SOURCE.lock.json', source_lock):
            raise ValueError('external code path overlaps source identity')
    verify_digest_files(repository, external, 'external code')
    return {'schema': 'nxnative-source-verification-v1', 'upstream_commit': COMMIT,
            'upstream_tree': digest, 'upstream_files': len(lock['upstream_files']),
            'adapter_files': len(adapters), 'external_code_files': len(external),
            'upstream_executed': False,
            'gameplay_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', type=Path, default=COMPONENT)
    args = parser.parse_args()
    try:
        report = verify_component(args.component)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'source verification failed: ' + str(error) + '\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
