#!/usr/bin/env python3
"""Copy fixed Eden Git blobs from a verified, isolated source receipt."""
import argparse
import hashlib
import json
from pathlib import Path

COMMIT = 'd8c6c59b679867d2595fdc0953df033475e23380'
ROOT_TREE = '52a17f2c311c5fab7890a46f4a329645f135bf8a'
AUDIO_TREE = '8f39f645392bca5d0b25d8490ac154bfcf5005e9'


def verify(data, entry):
    blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if (len(data) != entry['bytes'] or blob != entry['git_blob'] or
            hashlib.sha256(data).hexdigest() != entry['sha256']):
        raise ValueError(f"source identity mismatch: {entry['path']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit', type=Path)
    args = parser.parse_args()
    component = Path(__file__).resolve().parents[1]
    source = json.loads((args.audit / 'eden-source-receipt.json').read_text())
    if (source['commit'], source['root_tree'], source['audio_tree']) != (COMMIT, ROOT_TREE, AUDIO_TREE):
        raise ValueError('unexpected upstream identity')
    entries = {entry['path']: entry for entry in source['files']}
    for entry in json.loads((args.audit / 'eden-header-receipt.json').read_text()):
        if entry['path'] in entries and entries[entry['path']] != entry:
            raise ValueError(f"conflicting receipt: {entry['path']}")
        entries[entry['path']] = entry
    # Validate the complete transaction before publishing any source bytes.
    verified = []
    for path, entry in sorted(entries.items()):
        if Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('nonlocal source path')
        data = (args.audit / 'eden-audit-source' / path).read_bytes()
        verify(data, entry)
        verified.append((path, data))
    for path, data in verified:
        destination = component / 'vendor/eden' / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    lock = {
        'format': 1, 'repository': source['repository'], 'commit': COMMIT,
        'root_tree': ROOT_TREE, 'audio_tree': AUDIO_TREE,
        'license': 'vendor/eden/LICENSE.txt; per-file SPDX notices are preserved',
        'scope': 'Complete audio_core snapshot and audited Common/result header closure. '
                 'Only the independent host platform target is built in phase 1; the original '
                 'emulator CMakeLists, AudioIn/Out/Opus and Core-dependent TUs are not built.',
        'files': [entries[path] for path in sorted(entries)],
    }
    (component / 'SOURCE.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    print(f'copied and locked {len(verified)} original Git blobs')


if __name__ == '__main__':
    main()
