"""Release admission for the unmodified upstream shader toolchain.

This module does not infer bindings or translate shaders. A derivation report is
research output, even when the upstream command exits successfully. Release
requires an explicit base-v0 review of every facts field and every source
variant, bound to the exact capture, C1, reflection and facts bytes. Such a
review is an auditable input, not a proof that the reviewer's claims are true.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

from .upstream_shaders import _json, _read, COMMIT

PROFILE = 'botw-switch-base-v0'
RISK_TABLES = ('skipped', 'unresolved', 'established_differences',
               'integer_output_candidates', 'assumed_texture_types')
FACT_MAPS = ('constant_buffers', 'textures', 'images', 'storage_buffers',
             'uniform_pointers', 'attributes', 'outputs')
HEX64 = re.compile(r'[0-9a-f]{64}')
KEY = re.compile(r'[0-9a-f]{16}')
JSON_LIMIT = 64 * 1024 * 1024
TOTAL_LIMIT = 512 * 1024 * 1024
RECORD_LIMIT = 100000


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _digest(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _object(value, schema, what):
    _require(isinstance(value, dict) and value.get('schema') == schema,
             f'invalid {what} schema')
    return value


def _same_directory(value, expected, what):
    _require(isinstance(value, str) and Path(value).is_absolute() and
             Path(value).resolve() == Path(expected).resolve(), f'{what} directory mismatch')


def _fields(doc):
    """Exact field coverage; whole entries include type/slot/format/sampler."""
    result = [f'{table}.{key}' for table in FACT_MAPS for key in doc.get(table, {})]
    result.extend(name for name in ('compute', 'device_state') if name in doc)
    return sorted(result)


class _Snapshot:
    def __init__(self, stack):
        self.stack = stack
        self.files = {}
        self.used = 0

    def directory(self, path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError(f'cannot open regular input directory: {path}') from exc
        self.stack.callback(os.close, fd)
        return fd

    def read(self, target, path, limit, fd=None):
        if target in self.files:
            return self.files[target]
        data = _read(path, limit, directory_fd=fd)
        self.used += len(data)
        _require(self.used <= TOTAL_LIMIT, 'release inputs exceed total byte budget')
        self.files[target] = data
        return data


def _source_records(snapshot, inputs_fd, rows):
    sources = {}
    for index, row in enumerate(rows):
        _require(isinstance(row, dict) and row.get('stage') in ('vertex', 'fragment', 'compute'),
                 f'unsupported or invalid source stage at record {index}')
        for kind, suffix, limit in (('control', '.control', 1024 * 1024),
                                    ('code', '.nvn', 16 * 1024 * 1024),
                                    ('reflection', '.reflection.json', JSON_LIMIT)):
            digest, name = row.get(kind + '_sha256'), row.get(kind + '_file')
            _require(_digest(digest) and name == digest + suffix,
                     f'invalid {kind} source filename or digest at record {index}')
            data = snapshot.read('inputs/' + name, name, limit, inputs_fd)
            _require(_sha(data) == digest, f'{kind} source digest mismatch at record {index}')
        _require(_digest(row.get('c1_sha256')), f'missing C1 source digest at record {index}')
        name = row.get('c1_file')
        _require(name == row['c1_sha256'] + '.cbuf1', f'invalid C1 source filename at record {index}')
        data = snapshot.read('inputs/' + name, name, 16 * 1024 * 1024, inputs_fd)
        _require(_sha(data) == row['c1_sha256'], f'C1 source digest mismatch at record {index}')
        identity = (row['stage'], row['control_sha256'], row['code_sha256'])
        sources.setdefault(identity, []).append((index, row))
    return sources


def _admit(tool, inputs, captures, facts, derive_path, evidence_path):
    """Read once, validate detached bytes with upstream APIs, retain publication snapshot."""
    with ExitStack() as stack:
        snapshot = _Snapshot(stack)
        inputs_fd, caps_fd, facts_fd = (snapshot.directory(p) for p in (inputs, captures, facts))
        raw_inputs = snapshot.read('inputs/inputs.json', 'inputs.json', JSON_LIMIT, inputs_fd)
        source = _object(_json(raw_inputs), 1, 'source manifest')
        rows = source.get('records')
        _require(type(source['schema']) is int and isinstance(rows, list) and
                 1 <= len(rows) <= RECORD_LIMIT, 'invalid source record count')
        sources = _source_records(snapshot, inputs_fd, rows)

        derive = _object(_json(snapshot.read('derive-report.json', derive_path, JSON_LIMIT)),
                         'botw-shader-derive-report-v1', 'derive report')
        _require(derive.get('rules') == 'botw-derive-v5', 'unknown derivation rules')
        _same_directory(derive.get('captures'), captures, 'derive capture')
        for field in RISK_TABLES:
            _require(type(derive.get(field)) is dict and not derive[field],
                     f'derivation risk table is missing, invalid or nonempty: {field}')
        _require(type(derive.get('derived')) is int and derive['derived'] == 0,
                 'rule-derived facts cannot authorize release')
        established = derive.get('established')
        _require(isinstance(established, str) and Path(established).is_absolute(),
                 'established facts directory is required')
        established_fd = snapshot.directory(established)
        probe_path = derive.get('probe_manifest')
        _require(isinstance(probe_path, str) and Path(probe_path).is_absolute(),
                 'probe manifest is required')
        probe = _object(_json(snapshot.read('manifest-probe.json', probe_path, JSON_LIMIT)),
                        tool.MANIFEST_SCHEMA, 'probe manifest')
        _require(probe.get('mode') == 'probe', 'expected non-runnable probe manifest')
        _same_directory(probe.get('captures'), captures, 'probe capture')
        results = probe.get('results')
        _require(isinstance(results, list) and len(results) <= RECORD_LIMIT, 'invalid probe results')
        probe_by_key = {}
        for result in results:
            _require(isinstance(result, dict) and isinstance(result.get('key'), str) and
                     KEY.fullmatch(result['key']) and result['key'] not in probe_by_key,
                     'invalid or duplicate probe result key')
            _require(result.get('status') == 'probe' and result.get('errors') == [] and
                     result.get('conflicts') == [], 'probe errors or conflicts prevent release')
            probe_by_key[result['key']] = result

        evidence = _object(_json(snapshot.read('evidence/evidence.json', evidence_path, JSON_LIMIT)),
                           'botw-shader-release-evidence-v1', 'release evidence')
        _require(evidence.get('profile') == PROFILE and
                 evidence.get('input_manifest_sha256') == _sha(raw_inputs),
                 'release evidence is not bound to this base-v0 source manifest')
        evidence_fd = snapshot.directory(Path(evidence_path).parent)
        reviews = evidence.get('captures')
        _require(isinstance(reviews, dict), 'missing per-capture review evidence')
        names = os.listdir(caps_fd)
        keys = sorted(name[:-5] for name in names if name.endswith('.json') and KEY.fullmatch(name[:-5]))
        leftovers = {name.rsplit('.', 1)[0] for name in names if name.endswith(('.code', '.control')) and
                     KEY.fullmatch(name.rsplit('.', 1)[0])} - set(keys)
        _require(1 <= len(keys) <= RECORD_LIMIT and not leftovers, 'empty or incomplete capture batch')
        _require(set(keys) == set(reviews) == set(probe_by_key), 'capture/probe/review batch keys differ')
        _require(type(derive.get('established_copied')) is int and
                 derive['established_copied'] == len(keys), 'not every capture has established facts')

        admitted, covered = {}, set()
        with tempfile.TemporaryDirectory(prefix='botw-release-validation-') as private:
            private = Path(private)
            for key in keys:
                for suffix, limit in (('.json', 65536), ('.control', tool.CONTROL_LIMIT),
                                      ('.code', tool.CODE_LIMIT)):
                    name = key + suffix
                    data = snapshot.read('captures/' + name, name, limit, caps_fd)
                    (private / name).write_bytes(data)
                facts_name = key + '.facts.json'
                raw_facts = snapshot.read('facts/' + facts_name, facts_name, 1024 * 1024, facts_fd)
                # Strict JSON additionally rejects overflow-to-infinity floats.
                doc = _json(raw_facts)
                (private / facts_name).write_bytes(raw_facts)
                try:
                    cap = tool.load_capture(str(private), key)
                    _require(cap.stage in tool.XLATE_STAGE, 'unsupported release shader stage')
                    tool.load_facts(str(private / facts_name), cap)
                except (tool.CaptureError, tool.FactsError) as exc:
                    raise ValueError(f'{key}: {exc}') from exc
                _require(not doc['provenance'].strip().lower().startswith('botw-derive-'),
                         'upstream rule-derived provenance is not release authority')
                raw_established = snapshot.read('established/' + facts_name, facts_name,
                                                1024 * 1024, established_fd)
                _require(raw_established == raw_facts, 'facts differ from their established source')
                summary = cap.summary()
                observed = probe_by_key[key].get('capture')
                _require(isinstance(observed, dict) and observed == summary and
                         all(type(observed[k]) is type(v) for k, v in summary.items()),
                         f'probe capture identity differs: {key}')
                identity = (cap.stage_name, _sha(cap.control), _sha(cap.code))
                variants = sources.get(identity, [])
                _require(variants and all(row['c1_sha256'] == _sha(cap.c1) for _, row in variants),
                         f'capture/C1 has no matching source records: {key}')
                for index, row in variants:
                    for name, expected in (('c1_begin', cap.fields['c1_offset']),
                                           ('c1_end', cap.fields['code_size']),
                                           ('c1_bytes', len(cap.c1))):
                        _require(type(row.get(name)) is int and row[name] == expected,
                                 f'C1 source range differs at record {index}: {name}')
                indices = [index for index, _ in variants]
                reflections = sorted({row['reflection_sha256'] for _, row in variants})
                covered.update(indices)
                reference = reviews[key]
                _require(isinstance(reference, dict) and _digest(reference.get('sha256')) and
                         reference.get('file') == reference['sha256'] + '.review.json',
                         'unsafe or invalid review filename')
                name = reference['file']
                raw_review = snapshot.read('evidence/' + name, name, 1024 * 1024, evidence_fd)
                _require(_sha(raw_review) == reference['sha256'], 'review digest mismatch')
                review = _object(_json(raw_review), 'botw-shader-facts-review-v1', 'facts review')
                _require(review.get('profile') == PROFILE and review.get('status') == 'approved' and
                         review.get('capture_sha256') == cap.sha256 and
                         review.get('facts_sha256') == _sha(raw_facts), 'review identity/status mismatch')
                source_indices = review.get('source_record_indices')
                _require(isinstance(source_indices, list) and
                         all(type(i) is int for i in source_indices) and source_indices == indices and
                         review.get('reflection_sha256') == reflections,
                         'review must cover every source and reflection variant')
                _require(review.get('fields') == _fields(doc) and
                         isinstance(review.get('provenance'), str) and review['provenance'].strip(),
                         'review must explicitly cover all facts fields and provenance')
                admitted[key] = {'capture_sha256': cap.sha256, 'facts_sha256': _sha(raw_facts),
                                 'review_sha256': _sha(raw_review), 'source_record_indices': indices,
                                 'reflection_sha256': reflections, 'fields': _fields(doc)}
                for suffix in ('.json', '.control', '.code', '.facts.json'):
                    (private / (key + suffix)).unlink()
        _require(covered == set(range(len(rows))), 'source manifest has uncaptured or unsupported records')
        receipt = {'schema': 'botw-shader-release-admission-v1', 'approved': True, 'profile': PROFILE,
                   'upstream_commit': COMMIT, 'input_manifest_sha256': _sha(raw_inputs),
                   'captures': admitted, 'files': {name: _sha(raw) for name, raw in snapshot.files.items()},
                   'review_scope': 'explicit supplied evidence; not inferred hardware or runtime authority'}
        return receipt, snapshot.files


def check_release(tool, inputs_directory, captures_directory, facts_directory,
                  derive_report_path, evidence_path):
    """Validate a complete batch without publishing files or invoking a translator."""
    return _admit(tool, inputs_directory, captures_directory, facts_directory,
                  derive_report_path, evidence_path)[0]


def prepare_release(tool, inputs_directory, captures_directory, facts_directory,
                    derive_report_path, evidence_path, destination):
    """Publish detached admitted inputs to a fresh private directory outside Git."""
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    try:
        tool.require_outside_git(str(destination), 'release preparation')
    except tool.ToolError as exc:
        raise ValueError(str(exc)) from exc
    receipt, files = _admit(tool, inputs_directory, captures_directory, facts_directory,
                            derive_report_path, evidence_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    try:
        for name, data in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tool.write_atomic(str(path), data)
        tool.write_json(str(destination / 'release-admission.json'), receipt)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return receipt
