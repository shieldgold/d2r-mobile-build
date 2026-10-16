"""Stage the complete pinned shader toolchain with an exact v8 profile patch.

Only disposable copies change. Original capture identity, extraction, translation,
reflection and facts enforcement remain upstream. This module grants no facts.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_toolchain(destination):
    from scripts.botw_native import upstream_shaders
    verified = upstream_shaders.verify_vendor()
    original = upstream_shaders.VENDOR
    tool = upstream_shaders.load_tool()
    destination = Path(destination).resolve()
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    try:
        tool.require_outside_git(str(destination), 'patched shader toolchain')
    except tool.ToolError as exc:
        raise ValueError(str(exc)) from exc
    metadata = json.loads((original / 'UPSTREAM.json').read_text())
    profile = json.loads((COMPONENT / 'adapter/shader_v8_control.json').read_text())
    patch = COMPONENT / 'patches/shader-v8-toolchain.patch'
    if sha(patch) != profile['toolchain_patch_sha256']:
        raise ValueError('v8 toolchain patch identity differs from reviewed profile')
    destination.mkdir(mode=0o700, parents=True)
    for relative in metadata['files']:
        path = destination / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(original / relative, path)
    run = subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(patch)],
                         cwd=destination, capture_output=True, text=True)
    if run.returncode:
        raise ValueError('v8 toolchain patch failed: ' + run.stderr + run.stdout)
    actual = {name: sha(destination / name) for name in metadata['files']}
    changed = {name: {'before': metadata['files'][name], 'after': digest}
               for name, digest in actual.items() if digest != metadata['files'][name]}
    if changed != profile['toolchain_changed_files']:
        raise ValueError('v8 staged source differs from reviewed profile')
    receipt = {'schema': 1, 'profile': profile['profile'], 'upstream_commit': verified['commit'],
               'original_files_verified': verified['verified_files'], 'changed_files': changed,
               'toolchain_patch_sha256': sha(patch), 'version_relabelled': False,
               'control_bytes_rewritten': False, 'facts_invented': False}
    (destination / 'v8-stage-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(stage_toolchain(args.destination), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
