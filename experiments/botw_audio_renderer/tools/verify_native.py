#!/usr/bin/env python3
"""Pin/recheck the native integration and its canonical real PCM dependencies."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def snapshot(component):
    files = []
    for folder in ('native', 'adapter', 'patches', 'tests'):
        files += [path for path in (component / folder).rglob('*') if path.is_file()
                  and path.suffix in ('.h', '.cpp', '.c', '.py', '.patch', '.md', '.txt')]
    files += [component / 'tools/prepare_eden.py', component / 'tools/verify_native.py',
              component / 'platform/platform.h', component / 'platform/platform.cpp',
              component / 'platform/memory_provider.h']
    external = ['../botw_upstream_host/upstream/dm/host/botw_pcm.c',
                '../botw_upstream_host/upstream/dm/host/botw_pcm.h',
                '../botw_upstream_host/adapter/whole_audio_platform.h',
                '../botw_upstream_host/adapter/whole_audio_platform.m',
                '../botw_upstream_host/patches/ios-audio-output.patch',
                '../botw_upstream_host/patches/audio-write-observed.patch']
    return {
        'format': 1,
        'source_lock_sha256': digest(component / 'SOURCE.lock.json')['sha256'],
        'dependencies_lock_sha256': digest(component / 'DEPENDENCIES.lock.json')['sha256'],
        'upstream_native_tus': {'renderer': 67, 'adsp_renderer': 2, 'render_manager': 1, 'sink_stream': 1},
        'files': [{'path': str(path.relative_to(component)), **digest(path)} for path in sorted(set(files))],
        'external_files': [{'path': path, **digest(component / path)} for path in external],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-lock', action='store_true', help='refresh reviewed native integration lock')
    args = parser.parse_args()
    component = Path(__file__).resolve().parents[1]
    path = component / 'NATIVE.lock.json'
    actual = snapshot(component)
    if args.write_lock:
        path.write_text(json.dumps(actual, indent=2) + '\n')
    elif json.loads(path.read_text()) != actual:
        raise ValueError('native integration/real PCM dependency identity changed; review source and lock')


if __name__ == '__main__':
    main()
