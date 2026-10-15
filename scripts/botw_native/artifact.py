"""Fetch a generic host only after verifying its GitHub run and official digest.

The IPA's own manifest is not a trust anchor. Expected commit and repository
come from the caller; run and artifact identities/digest come from gh api.
No game files or signing identities are used; credentials and signed URLs are
never exposed in errors, provenance or console output.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ARTIFACT_NAME = 'BOTWNative-generic-ios-host'
SHELL_NAME = 'BOTWNative-shell.ipa'
MAX_ARCHIVE_BYTES = 256 * 1024**2
MAX_METADATA_BYTES = 4 * 1024**2
MAX_ARTIFACTS = 1000


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _integer(value):
    return type(value) is int and value > 0


def _gh(endpoint, limit):
    try:
        result = subprocess.run(['gh', 'api', '--method', 'GET', endpoint],
                                capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        # gh may print a redirected, signed URL. Never relay its stderr.
        raise RuntimeError('GitHub API request could not complete') from None
    if result.returncode:
        raise RuntimeError(f'GitHub API request failed (exit {result.returncode})')
    if not isinstance(result.stdout, bytes) or len(result.stdout) > limit:
        raise ValueError('GitHub API response exceeds the permitted size')
    return result.stdout


def _metadata(endpoint):
    try:
        data = json.loads(_gh(endpoint, MAX_METADATA_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('GitHub returned invalid JSON metadata') from exc
    if not isinstance(data, dict):
        raise ValueError('GitHub metadata must be an object')
    return data


def _verify_run(run, repository, run_id, expected_commit):
    if (run.get('id') != run_id or run.get('status') != 'completed' or
            run.get('conclusion') != 'success' or run.get('head_sha') != expected_commit):
        raise ValueError('Run must have succeeded at exactly the requested commit')
    branch = run.get('head_branch')
    if not isinstance(branch, str) or not branch or len(branch) > 255 or any(ord(c) < 32 for c in branch):
        raise ValueError('Run has no valid branch identity')
    for key in ('repository', 'head_repository'):
        identity = run.get(key)
        if (not isinstance(identity, dict) or not _integer(identity.get('id')) or
                not isinstance(identity.get('full_name'), str) or
                identity['full_name'].lower() != repository.lower()):
            raise ValueError('Run repository or head repository identity differs from the requested repository')
    if run['repository']['id'] != run['head_repository']['id']:
        raise ValueError('Run head repository id differs from its repository id')


def _find_artifact(repository, run_id):
    artifacts, total = [], None
    for page in range(1, MAX_ARTIFACTS // 100 + 1):
        record = _metadata(f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100&page={page}')
        count, entries = record.get('total_count'), record.get('artifacts')
        if (type(count) is not int or not 0 <= count <= MAX_ARTIFACTS or
                not isinstance(entries, list) or len(entries) > 100 or
                any(not isinstance(entry, dict) for entry in entries) or
                (total is not None and count != total)):
            raise ValueError('Invalid or changing run artifact listing')
        total = count
        artifacts.extend(entries)
        if len(artifacts) >= total:
            if len(artifacts) != total:
                raise ValueError('Artifact listing count differs from its metadata')
            matches = [entry for entry in artifacts if entry.get('name') == ARTIFACT_NAME]
            if len(matches) != 1:
                raise ValueError('Run must contain exactly one unique generic host artifact')
            return matches[0]
        if not entries:
            raise ValueError('Truncated run artifact listing')
    raise ValueError('Run artifact listing exceeds limit')


def _verify_artifact(artifact, run):
    if (not _integer(artifact.get('id')) or artifact.get('expired') is not False or
            not _integer(artifact.get('size_in_bytes')) or artifact['size_in_bytes'] > MAX_ARCHIVE_BYTES):
        raise ValueError('Artifact is expired or exceeds the archive size limit')
    digest = artifact.get('digest')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Artifact has no official SHA256 digest')
    workflow = artifact.get('workflow_run')
    expected = {'id': run['id'], 'repository_id': run['repository']['id'],
                'head_repository_id': run['head_repository']['id'],
                'head_branch': run['head_branch'], 'head_sha': run['head_sha']}
    if not isinstance(workflow, dict) or any(workflow.get(key) != value for key, value in expected.items()):
        raise ValueError('Artifact run, repository, branch or commit identity differs from the verified run')


def _extract_shell(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                raise ValueError('Artifact ZIP must contain exactly one regular shell IPA')
            item = entries[0]
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (item.filename != SHELL_NAME or item.orig_filename != SHELL_NAME or item.is_dir() or
                    kind not in (0, stat.S_IFREG) or item.external_attr & 0x10 or item.flag_bits & 1 or
                    item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                    not 0 < item.file_size <= MAX_ARCHIVE_BYTES or item.compress_size > len(data)):
                raise ValueError('Artifact ZIP contains an unsafe, oversized or unexpected member')
            with archive.open(item) as stream:
                shell = stream.read(MAX_ARCHIVE_BYTES + 1)
            if len(shell) != item.file_size or len(shell) > MAX_ARCHIVE_BYTES:
                raise ValueError('Extracted shell IPA size differs from ZIP metadata')
            return shell
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError('Artifact is not a valid supported ZIP') from exc


def fetch_shell(repository, run_id, expected_commit, output_dir):
    """Verify one successful run and atomically publish its IPA and provenance.

    output_dir must be new. The return value includes path and shell_sha256 for
    local assembly. No assertion is made about guest execution or installation.
    """
    if (not isinstance(repository, str) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository)):
        raise ValueError('Repository must be an explicit owner/name')
    if not _integer(run_id):
        raise ValueError('Run id must be a positive integer')
    if not isinstance(expected_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_commit):
        raise ValueError('Expected commit must be an explicit lowercase 40-character hexadecimal SHA')
    requested_output = Path(output_dir).absolute()
    if requested_output.exists() or requested_output.is_symlink():
        raise FileExistsError('Output already exists: ' + str(requested_output))
    output = requested_output.resolve()
    run = _metadata(f'repos/{repository}/actions/runs/{run_id}')
    _verify_run(run, repository, run_id, expected_commit)
    artifact = _find_artifact(repository, run_id)
    _verify_artifact(artifact, run)
    binary = _gh(f'repos/{repository}/actions/artifacts/{artifact["id"]}/zip', MAX_ARCHIVE_BYTES)
    if len(binary) != artifact['size_in_bytes']:
        raise ValueError('Downloaded ZIP size differs from official artifact metadata')
    digest = _digest(binary)
    if digest != artifact['digest'][7:]:
        raise ValueError('Downloaded ZIP SHA256 digest differs from official artifact metadata')
    shell = _extract_shell(binary)
    provenance = {'schema': 1, 'status': 'verified_host_artifact', 'repository': repository,
                  'run_id': run_id, 'head_sha': expected_commit, 'head_branch': run['head_branch'],
                  'repository_id': run['repository']['id'], 'head_repository_id': run['head_repository']['id'],
                  'artifact_id': artifact['id'], 'artifact_name': ARTIFACT_NAME,
                  'outer_zip_sha256': digest, 'outer_zip_bytes': len(binary),
                  'digest_source': 'GitHub Actions artifact API',
                  'shell_sha256': _digest(shell), 'shell_bytes': len(shell),
                  'path': str(output / SHELL_NAME),
                  'provenance_path': str(output / 'shell-provenance.json')}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{output.name}.tmp-', dir=output.parent))
    try:
        (staging / SHELL_NAME).write_bytes(shell)
        (staging / 'shell-provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        if output.exists() or output.is_symlink():
            raise FileExistsError('Output appeared during download: ' + str(output))
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        provenance = fetch_shell(args.repository, args.run_id, args.commit, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        print('Verified host artifact download failed: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(provenance, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
