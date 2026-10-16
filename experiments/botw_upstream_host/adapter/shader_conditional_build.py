"""Publish separately admitted conditional MSL from the complete pinned CPU tools.

No per-shader facts or review assertions are manufactured. Each explicit device
mode is freshly translated with empty texture/image query dictionaries. The
original analysis must accept the actual resulting resources and MSL before a
conditional contract is issued. Uncovered candidates remain explicit failures.
"""
from pathlib import Path
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.botw_native import upstream_shaders
from scripts.botw_native.shader_bindings import _Snapshot, _source_records

PROFILE = 'botw-switch-base-v0'
SCOPE = 'noattr-vs-output0-fs-nonqueried-v1'
HEX64 = re.compile(r'[0-9a-f]{64}')
KEY = re.compile(r'[0-9a-f]{16}')
LIMIT = 64 * 1024 * 1024
MODES = [(window, depth) for window in (0, 1) for depth in (0, 1)]
WINDOW = {0: 'lower_left', 1: 'upper_left'}
DEPTH = {0: 'near_is_minus_w', 1: 'near_is_zero'}
FORMAT_SHA256 = '3f46576221f48db69abb5b553529fea2dd5f9a2ac1d14a50d5e7c57a499a2865'


class ConditionalRejected(ValueError):
    def __init__(self, message, analysis):
        super().__init__(message)
        self.analysis = analysis


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked(path):
    path = Path(os.path.abspath(path))
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'symlink or traversal in conditional input/output path')
    return path


def read(path, limit=LIMIT):
    return upstream_shaders._read(checked(path), limit)


def document(path):
    return upstream_shaders._json(read(path))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def ports():
    # This is a trusted sibling source, never Python supplied by a caller.
    import importlib.util
    path = Path(__file__).with_name('shader_source_build.py')
    spec = importlib.util.spec_from_file_location('_conditional_trusted_source_ports', path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def requested_keys(path):
    raw = read(path)
    value = upstream_shaders._json(raw)
    require(isinstance(value, dict) and set(value) == {'schema', 'profile', 'keys'} and
            value['schema'] == 'botw-shader-conditional-candidates-v1' and value['profile'] == PROFILE,
            'conditional candidates are explicit requests, not review or facts admission')
    keys = value['keys']
    require(isinstance(keys, list) and 1 <= len(keys) <= 100000 and
            all(isinstance(k, str) and KEY.fullmatch(k) for k in keys) and len(set(keys)) == len(keys),
            'invalid or duplicate conditional candidate keys')
    return sorted(keys), digest(raw)


def format_contract():
    raw = read(Path(__file__).with_name('base_v0_graphics_formats.json'))
    require(digest(raw) == FORMAT_SHA256, 'fixed base format/implicit-bank proof SHA256 differs')
    value = upstream_shaders._json(raw)
    require(value.get('schema') == 'botw-base-v0-nvn-format-contracts-v1' and value.get('profile') == PROFILE and
            value.get('source', {}).get('sha256') == '58b6791aff6e5cb509c5347c1dc41d01a32c0a298c7f5da0c88ea56fc8a6eb17',
            'format proof is not the fixed base SDK')
    classes = value.get('fragment_output_classes')
    require(isinstance(classes, dict) and set(classes) == {'37', '41', '46', '154'} and
            all(row.get('fragment_output_type') == 'float' and row.get('nvn_format') == int(key)
                for key, row in classes.items()), 'fixed-base float output classes differ')
    implicit = value.get('implicit_texture_bank', {})
    narrow = implicit.get('narrow_admission_contract', {})
    require(implicit.get('schema') == 'botw-base-v0-implicit-texture-bank-v1' and
            implicit.get('source', {}).get('sha256') == value['source']['sha256'] and
            narrow.get('accepted_implicit_cbuf_slot') == -1 and narrow.get('resolved_constant_bank') == 2 and
            narrow.get('accepted_handle_type') == 0 and narrow.get('required_offset1_words') == 0 and
            narrow.get('required_array_length') == 1 and narrow.get('required_separate') is False,
            'implicit texture bank proof is outside the narrow combined-handle contract')
    return raw, value


def snapshot_capture(tool, directory, key):
    """Retain exactly the raw bytes accepted by the original capture reader."""
    raw = {suffix: read(directory / (key + suffix), limit) for suffix, limit in
           (('.json', tool.JSON_LIMIT), ('.control', tool.CONTROL_LIMIT), ('.code', tool.CODE_LIMIT))}
    with tempfile.TemporaryDirectory(prefix='botw-conditional-capture-') as temporary:
        for suffix, data in raw.items():
            (Path(temporary) / (key + suffix)).write_bytes(data)
        capture = tool.load_capture(temporary, key)
    return capture, raw


def bind_static_inputs(tool, extract, manifest_path, captures):
    """Reuse normal extraction readers; bind selected complete capture/C1 bytes.

    Schema 1 is the existing detached extraction format. The combined catalog
    is verified against its original SHARCB/BNSH container bytes with the same
    original parsers used to create it, including original stage pairs.
    """
    raw = read(manifest_path)
    manifest = upstream_shaders._json(raw)
    require(isinstance(manifest, dict), 'invalid static input manifest')
    matched = {}; paired_inputs = {key: set() for key in captures}; verified_pairs = set()
    if type(manifest.get('schema')) is int and manifest['schema'] == 1:
        rows = manifest.get('records')
        require(isinstance(rows, list) and 1 <= len(rows) <= 100000, 'invalid static extraction records')
        with ExitStack() as stack:
            snapshot = _Snapshot(stack)
            fd = snapshot.directory(checked(manifest_path).parent)
            sources = _source_records(snapshot, fd, rows)
            for key, capture in captures.items():
                identity = (capture.stage_name, digest(capture.control), digest(capture.code))
                require(identity in sources, 'capture does not belong to the static source inputs: ' + key)
                for index, row in sources[identity]:
                    require(row['c1_sha256'] == digest(capture.c1) and
                            row.get('c1_begin') == capture.fields['c1_offset'] and
                            row.get('c1_end') == capture.fields['code_size'] and
                            row.get('c1_bytes') == len(capture.c1), 'static C1 extent or bytes differ: ' + key)
                    matched.setdefault(key, []).append({'record': index, 'c1_sha256': row['c1_sha256']})
    else:
        require(manifest.get('schema') == 'botw-static-base-v0-combined-shader-inputs-v1',
                'unsupported static input manifest schema')
        sources, occurrences = manifest.get('inputs'), manifest.get('occurrences')
        require(isinstance(sources, list) and 1 <= len(sources) <= 100000 and
                isinstance(occurrences, list) and 1 <= len(occurrences) <= 100000,
                'invalid combined static source inventory')
        chosen = {}
        for row in occurrences:
            require(isinstance(row, dict) and isinstance(row.get('key'), str) and KEY.fullmatch(row['key']),
                    'malformed static occurrence')
            if row['key'] in captures:
                capture = captures[row['key']]
                require(row.get('sha256') == capture.sha256 and type(row.get('stage')) is int and
                        row['stage'] == capture.stage and row.get('control_version') == capture.fields['version'],
                        'static occurrence capture identity differs')
                chosen.setdefault(row.get('source_sha256'), []).append(row)
        require({r['key'] for rows in chosen.values() for r in rows} == set(captures),
                'candidate is missing from static source occurrences')
        seen = set()
        for source in sources:
            require(isinstance(source, dict) and isinstance(source.get('sha256'), str) and
                    HEX64.fullmatch(source['sha256']) and source['sha256'] not in seen,
                    'invalid or duplicate static container identity')
            seen.add(source['sha256'])
            if source['sha256'] not in chosen:
                continue
            container = read(source['path'], 256 * 1024 * 1024)
            require(digest(container) == source['sha256'] and type(source.get('bytes')) is int and
                    len(container) == source['bytes'], 'original static container SHA256 or size differs')
            decoded = {}; pairs = []
            if source.get('kind') == 'sharcb':
                from scripts.botw_native.resource_shaders import parse_sharcb_v9
                report = parse_sharcb_v9(container)
                by_index = {}
                for variation in report['variations']:
                    control = container[variation['control_offset']:variation['control_offset'] + variation['control_bytes']]
                    code = container[variation['code_offset']:variation['code_offset'] + variation['code_bytes']]
                    fields = tool.parse_control(control)
                    sha = tool.capture_digest(fields, control, code)
                    capture = tool.Capture('static source', sha[:16], sha, fields, control, code)
                    decoded[(variation['index'], capture.stage)] = capture
                    by_index[variation['index']] = capture
                for program in report['programs']:
                    for indices in program['variation_indices']:
                        stages = {by_index[i].stage: by_index[i] for i in indices}
                        if 0 in stages and 1 in stages:
                            pairs.append((stages[0], stages[1]))
            elif source.get('kind') == 'bnsh':
                stages, variations = extract.bnsh_stages(container, 0, Path(source['path']).name)
                by_key = {}
                for label, sha, fields, control, code in stages:
                    capture = tool.Capture('static source', sha[:16], sha, fields, control, code)
                    decoded[(label, capture.stage)] = capture; by_key[capture.key] = capture
                for _, shaders in variations:
                    if 'vs' in shaders and 'ps' in shaders:
                        pairs.append((by_key[shaders['vs']], by_key[shaders['ps']]))
            else:
                raise ValueError('unsupported original static container')
            for row in chosen[source['sha256']]:
                original = decoded.get((row.get('variation'), row['stage'])); capture = captures[row['key']]
                require(original is not None and original.sha256 == capture.sha256 and
                        original.control == capture.control and original.code == capture.code and
                        original.c1 == capture.c1, 'capture/C1 do not match the actual original static container')
                matched.setdefault(capture.key, []).append({'source_sha256': source['sha256'],
                    'variation': row['variation'], 'c1_sha256': digest(original.c1)})
            for vertex, fragment in pairs:
                verified_pairs.add((vertex.key, fragment.key))
                if vertex.key in paired_inputs:
                    paired_inputs[vertex.key].update(tool.sph_input_locations(fragment))
        require(set(chosen) <= seen, 'static occurrence has no original container')
    return {'input_manifest_sha256': digest(raw), 'sources': matched,
            'paired_vertex_outputs': {k: sorted(v) for k, v in paired_inputs.items()},
            'verified_pairs': [list(pair) for pair in sorted(verified_pairs)]}


def assess_mode(tool, capture, res, msl, device):
    """Use typed requests only inside original analysis; never serialize facts."""
    require(capture.stage in (0, 1), 'scope rejects compute and other non-graphics stages')
    require(device in MODES, 'unknown conditional device mode')
    require(isinstance(res, dict) and res.get('key') == capture.key and
            res.get('stage') == tool.XLATE_STAGE[capture.stage], 'fresh translator result identity differs')
    for name in ('notes', 'images', 'sbufs', 'textures', 'cbufs'):
        require(isinstance(res.get(name), list), 'missing actual translator resource list: ' + name)
    require(not res['notes'], 'translator query fallback is outside the nonqueried scope')
    require(not res['images'] and not res['sbufs'], 'image/storage-buffer resources are outside scope')
    request = tool.Facts()
    request.device_state = {'window_origin_mode': WINDOW[device[0]], 'depth_mode': DEPTH[device[1]]}
    request.constant_buffers = {bank: bank - 3 for bank in range(3, 17)}
    format_contract()
    if capture.stage == 1:
        request.outputs = {0: 'float'}
    for row in res['cbufs']:
        require(isinstance(row, dict) and type(row.get('slot')) is int, 'invalid actual constant bank record')
    texture_types = {1: '1d', 257: '1darray', 2: 'buffer', 3: '2d', 259: '2darray',
                     515: '2dms', 771: '2dmsarray', 4: '3d', 5: 'cube', 261: 'cubearray'}
    seen = set()
    for row in res['textures']:
        require(isinstance(row, dict) and type(row.get('handle')) is int and
                0 <= row['handle'] <= 0xffffffff and row['handle'] not in seen, 'invalid/duplicate actual texture handle')
        handle = row['handle']; seen.add(handle)
        require(type(row.get('array')) is int and row['array'] == 1 and row.get('separate') is False and
                handle >> 14 == 0, 'array/separate texture handle is not proven by this scope')
        require(type(row.get('cbuf_slot')) is int and row['cbuf_slot'] in (-1, 2),
                'texture constant bank is outside the proven exact C2 or implicit -1 sentinel')
        offset = handle * 4
        require(0x20 <= offset < 0x120 and (offset - 0x20) % 8 == 0,
                'texture handle offset is outside the exact fixed-base texture32 table')
        raw_type = row.get('type')
        require(type(raw_type) is int and raw_type & ~0x400 in texture_types, 'unsupported actual encoded texture type')
        texture_type = texture_types[raw_type & ~0x400]
        request.textures[handle] = {'slot': (offset - 0x20) // 8, 'type': texture_type}
    bindings, inputs, unknowns, conflicts, _, _ = tool.analyze(capture, request, res, msl)
    if unknowns or conflicts:
        raise ConditionalRejected('original analyze has unknown/conflicting resource bindings',
                                  {'unknowns': unknowns, 'conflicts': conflicts})
    require(all(row['slot'] in (1, 2, *range(3, 17)) for row in res['cbufs']),
            'constant bank is outside C1/C2 or the proven fourteen uniforms')
    require(not inputs, 'actual vertex attributes are outside the no-attribute scope')
    if capture.stage == 1:
        require(tool.fragment_raster(msl)['colors'] == [0], 'fragment must write exactly output0 with float type')
    for binding in bindings:
        kind = binding.get('kind')
        require(kind in ('support', 'c1', 'cbuf', 'tex', 'tbuf', 'samp_tex', 'drvcb'),
                'actual binding kind is outside scope')
        if kind == 'drvcb':
            require(binding.get('bank') == 2 and all(0x20 <= off <= 0x110 and off % 16 == 0
                    for off in binding.get('vec4_offsets', [])), 'non-C2 or unfilled driver bank read')
        if kind in ('tex', 'tbuf'):
            row = next(r for r in res['textures'] if r['handle'] == binding['handle'])
            require(('depth' in binding['msl_type']) == bool(row['type'] & 0x400),
                    'encoded texture shadow type differs from the actual MSL')
    entry = re.findall(r'(vertex|fragment|kernel)\s+\S+\s+(\w+)\s*\(', msl)
    require(len(entry) == 1 and entry[0][0] == ('vertex' if capture.stage == 0 else 'fragment'),
            'actual MSL entry stage is missing or ambiguous')
    return request, bindings, inputs, entry[0]


def logical_job(job, capture):
    value = {k: v for k, v in job.items() if k != 'code'}
    value.update(schema='botw-shader-conditional-logical-job-v1', code_sha256=digest(capture.translator_code))
    return value


def build(inputs, captures, candidates, destination, *, xlate, spirv_cross, jobs=1, pairs=None):
    require(type(jobs) is int and 1 <= jobs <= 256, 'invalid CPU worker count')
    destination = checked(destination)
    require(not os.path.lexists(destination), 'conditional output must be a fresh directory')
    selected, request_sha = requested_keys(candidates)
    source = ports()
    source.stage_source_toolchain(destination / 'toolchain')
    # Every Python byte is verified before importing this private local stage.
    staged_hashes = source.source_toolchain_hashes(destination / 'toolchain')
    tool = source.load(destination / 'toolchain/dm/gfx/tools/botw_shader_build.py', '_conditional_verified_builder')
    extract = source.load(destination / 'toolchain/dm/gfx/tools/botw_shader_extract.py', '_conditional_verified_extract')
    tool.check_capture_dir(str(checked(captures)))
    complete = {}; capture_bytes = {}; snapshot_used = 0
    for key in selected:
        complete[key], capture_bytes[key] = snapshot_capture(tool, checked(captures), key)
        snapshot_used += sum(len(raw) for raw in capture_bytes[key].values())
        require(snapshot_used <= 512 * 1024 * 1024, 'requested capture snapshots exceed byte budget')
    originals = dict(complete); requested_pairs = {}; pairs_sha = None
    if pairs is not None:
        pair_raw = read(pairs); pairs_sha = digest(pair_raw)
        pair_snapshot = destination / 'requested-pairs.tsv'; pair_snapshot.write_bytes(pair_raw)
        pair_map = tool.load_pairs(str(pair_snapshot))
        for key in selected:
            if key not in pair_map:
                continue
            require(complete[key].stage == 0, 'pair source is not an actual vertex capture')
            for partner in pair_map[key]:
                if partner not in originals:
                    originals[partner], raw = snapshot_capture(tool, checked(captures), partner)
                    snapshot_used += sum(len(data) for data in raw.values())
                    require(snapshot_used <= 512 * 1024 * 1024, 'paired capture snapshots exceed byte budget')
                require(originals[partner].stage == 1, 'pair destination is not an actual fragment capture')
            requested_pairs[key] = sorted(pair_map[key])
    static = bind_static_inputs(tool, extract, checked(inputs), originals)
    if pairs is not None:
        verified_pairs = {tuple(pair) for pair in static['verified_pairs']}
        require(all((key, partner) in verified_pairs for key, partners in requested_pairs.items() for partner in partners),
                'requested pair is not established by the original static container')
        for key in selected:
            want = set()
            for partner in requested_pairs.get(key, []):
                want.update(tool.sph_input_locations(originals[partner]))
            static['paired_vertex_outputs'][key] = sorted(want)
        static['pairs_sha256'] = pairs_sha
        static['requested_pairs'] = requested_pairs
    binding_raw = read(Path(__file__).with_name('base_v0_graphics_binding.json'))
    format_raw, _ = format_contract()
    binding = upstream_shaders._json(binding_raw)
    require(binding.get('schema') == 'botw-base-v0-graphics-binding-v1' and
            binding.get('source', {}).get('nso_sha256') == '58b6791aff6e5cb509c5347c1dc41d01a32c0a298c7f5da0c88ea56fc8a6eb17' and
            binding.get('stages') == [0, 1], 'fixed-base graphics binding contract differs')
    adapter_sha = digest(read(__file__))
    tools = tool.Tools(str(xlate), str(spirv_cross), 'none')
    try:
        tools.probe_xlate(); tools.probe_spirv_cross()
    except tool.ToolError as error:
        raise ValueError(str(error)) from error
    require(tools.identity['xlate']['built_from_repo_source'], 'translator does not match the complete staged source')
    tools.identity['metal'] = {'backend': 'device-source-required', 'xcrun_metal': None, 'xcrun_metallib': None}
    options = {'xlate_debug': False, 'xlate_reduced': False, 'conditional_scope': SCOPE,
        'source_adapter_sha256': adapter_sha, 'binding_contract_sha256': digest(binding_raw),
        'format_contract_sha256': digest(format_raw),
        'candidate_request_sha256': request_sha, 'input_manifest_sha256': static['input_manifest_sha256'],
        'complete_staged_source_sha256': digest(canonical(staged_hashes))}
    if pairs is not None:
        options['pairs_sha256'] = pairs_sha
    chain = tools.chain(options)
    results = {key: [] for key in selected}; failures = {key: [] for key in selected}
    admitted = destination / 'admitted'; admitted.mkdir()
    with tempfile.TemporaryDirectory(prefix='.conditional-cpu-', dir=destination) as temporary:
        for window, depth in MODES:
            work = Path(temporary) / f'{window}-{depth}'; work.mkdir()
            requests = []
            for key, capture in complete.items():
                if capture.stage not in (0, 1):
                    failures[key].append('scope rejects non-graphics stage'); continue
                request = tool.Facts(); request.device_state = {'window_origin_mode': WINDOW[window], 'depth_mode': DEPTH[depth]}
                if capture.stage == 1:
                    request.outputs = {0: 'float'}
                path = work / (key + '.code'); path.write_bytes(capture.translator_code)
                job = tool.make_job(capture, request, str(path), options)
                require(job['tex'] == {} and job['img'] == {} and job['attr'] == {},
                        'conditional translation may not answer texture/image/attribute queries')
                requests.append(job)
            actual_jobs, errors, retries = tool.translate_with_retry(tools, requests, str(work), jobs) if requests else ([], {}, {})
            for job in actual_jobs:
                key = job['key']; capture = complete[key]
                try:
                    require(not errors[key], 'actual fresh translation failed')
                    res_raw = read(work / (key + '.res.json'))
                    res = upstream_shaders._json(res_raw)
                    logical_raw = canonical(logical_job(job, capture))
                    res_file = f'modes/{key}/{window}-{depth}.res.json'
                    job_file = f'modes/{key}/{window}-{depth}.job.json'
                    (admitted / res_file).parent.mkdir(parents=True, exist_ok=True)
                    (admitted / res_file).write_bytes(res_raw)
                    (admitted / job_file).write_bytes(logical_raw)
                    msl_path = work / (key + '.metal')
                    require(tool.spirv_to_msl(tools, str(work / (key + '.spv')), str(msl_path)) is None,
                            'actual SPIR-V to MSL conversion failed')
                    raw_msl = read(msl_path, 16 * 1024 * 1024)
                    require(raw_msl and b'\0' not in raw_msl, 'actual MSL is empty or contains NUL')
                    msl = raw_msl.decode('utf-8', errors='strict'); padded = []
                    want = static['paired_vertex_outputs'][key]
                    if capture.stage == 0 and want:
                        msl, padded = tool.pad_vertex_outputs(msl, set(want))
                        require(padded is not None, 'original vertex output padding could not resolve the actual source pair')
                    request, bindings, vertex_inputs, entry = assess_mode(tool, capture, res, msl, (window, depth))
                    reflection = tool.reflection(capture, chain, entry, bindings, vertex_inputs, request, 'B')
                    reflection.pop('facts_sha256', None)
                    reflection.update(translator_options={'debug': job['debug'], 'reduced': job['reduced']}, vs_pad=padded)
                    reflection['raster'] = tool.fragment_raster(msl) if capture.stage == 1 else {'colors': []}
                    mode = {'window_origin_mode': window, 'depth_mode': depth,
                        'job_sha256': digest(logical_raw), 'res_sha256': digest(res_raw),
                        'res_file': res_file, 'job_file': job_file,
                        'translated_code_sha256': digest(capture.translator_code),
                        'spirv_sha256': digest(read(work / (key + '.spv'))),
                        'fresh_msl_sha256': digest(raw_msl), 'msl_sha256': digest(msl.encode()),
                        'reflection_core_sha256': digest(canonical(reflection)), 'vs_pad': padded,
                        'gates': {'original_analyze_unknowns': 0, 'original_analyze_conflicts': 0,
                                  'texture_queries': [], 'attributes': [], 'images': [], 'storage_buffers': []}}
                    results[key].append((mode, reflection, msl))
                except (ValueError, tool.ToolError, tool.CaptureError, KeyError, TypeError) as error:
                    failure = {'device': [window, depth], 'error': str(error)}
                    if isinstance(error, ConditionalRejected):
                        failure['original_analysis'] = error.analysis
                    failures[key].append(failure)
    accepted = {key: rows for key, rows in results.items() if len(rows) == 4 and not failures[key]}
    rejected = [{'key': key, 'capture': complete[key].summary(), 'reasons': failures[key]}
                for key in selected if key not in accepted]
    for key in selected:
        if key not in accepted:
            shutil.rmtree(admitted / 'modes' / key, ignore_errors=True)
    proof = {'schema': 'botw-shader-conditional-subset-proof-v1', 'profile': PROFILE, 'scope': SCOPE,
        'chain': chain, 'upstream_commit': upstream_shaders.COMMIT,
        'input_manifest_sha256': static['input_manifest_sha256'],
        'source_adapter_sha256': adapter_sha, 'binding_contract_sha256': digest(binding_raw),
        'format_contract_sha256': digest(format_raw),
        'candidate_request_sha256': request_sha, 'static_inputs': static, 'toolchain': staged_hashes,
        'tools': tools.identity, 'options': options, 'accepted': {
            key: {'capture_sha256': complete[key].sha256, 'stage_name': complete[key].stage_name,
                  'capture': complete[key].summary(), 'c1_sha256': digest(complete[key].c1),
                  'modes': [row[0] for row in rows]} for key, rows in accepted.items()},
        'rejected': rejected, 'entire_game_coverage': False, 'guest_or_gpu_executed': False}
    proof_raw = canonical(proof); proof_sha = digest(proof_raw)
    (admitted / 'subset-proof.json').write_bytes(proof_raw)
    (admitted / 'base-bindings.json').write_bytes(binding_raw)
    (admitted / 'base-formats.json').write_bytes(format_raw)
    if not accepted:
        return 1
    require(digest(read(__file__)) == adapter_sha and
            read(Path(__file__).with_name('base_v0_graphics_binding.json')) == binding_raw and
            read(Path(__file__).with_name('base_v0_graphics_formats.json')) == format_raw and
            source.source_toolchain_hashes(destination / 'toolchain') == staged_hashes,
            'publisher or verified source changed during actual CPU translation')
    audit_files = {}
    for key in accepted:
        for suffix, raw in capture_bytes[key].items():
            relative = 'captures/' + key + suffix
            (admitted / relative).parent.mkdir(parents=True, exist_ok=True)
            (admitted / relative).write_bytes(raw)
            audit_files[relative] = digest(raw)
        for mode, _, _ in accepted[key]:
            for field in ('res_file', 'job_file'):
                relative = mode[field]
                audit_files[relative] = digest(read(admitted / relative))
    package = destination / 'build' / ('botw-c-' + chain); package.mkdir(parents=True)
    published = []; identities = {}
    for key, rows in accepted.items():
        capture = complete[key]; variants = []
        for mode, reflection, msl in rows:
            contract = {'schema': 'botw-shader-conditional-translation-contract-v1', 'profile': PROFILE,
                'chain': chain, 'capture': capture.summary(), 'scope': SCOPE,
                'requirements': {'device': {name: mode[name] for name in ('window_origin_mode', 'depth_mode')},
                    'attributes': [], 'texture_queries': [],
                    'images': [], 'storage_buffers': [], 'fragment_output0':
                        {'type': 'float', 'nvn_formats': [37, 41, 46, 154]} if capture.stage == 1 else None},
                'proofs': {'binding_contract_sha256': digest(binding_raw), 'subset_sha256': proof_sha},
                'translation': {'source_adapter_sha256': adapter_sha, 'job_sha256': mode['job_sha256']}}
            contract_raw = canonical(contract); variant = digest(contract_raw); variants.append(variant)
            reflection.update(schema='botw-shader-conditional-reflection-v1', conditional_contract_sha256=variant,
                runnable=False, runtime_compile_required=True, runtime_compiler={'fast_math': False}, unknowns=[], conflicts=[])
            files = {}
            for kind, suffix, raw in [('contract', '.contract.json', contract_raw),
                                      ('json', '.json', canonical(reflection)), ('metal_source', '.metal', msl.encode())]:
                name = variant + suffix; (package / name).write_bytes(raw)
                files[kind] = {'path': name, 'sha256': digest(raw)}
            published.append({'variant_sha256': variant, 'status': 'conditional_source_ready',
                'runnable': False, 'runtime_compile_required': True, 'capture': capture.summary(),
                'files': files, 'errors': [], 'unknowns': [], 'conflicts': []})
        identities[key] = {'capture_sha256': capture.sha256, 'stage_name': capture.stage_name, 'variants': variants}
    admission = {'schema': 'botw-shader-conditional-admission-v1', 'profile': PROFILE,
        'upstream_commit': upstream_shaders.COMMIT, 'scope': SCOPE, 'subset_sha256': proof_sha,
        'binding_contract_sha256': digest(binding_raw), 'source_adapter_sha256': adapter_sha, 'captures': identities}
    admission['format_contract_sha256'] = digest(format_raw)
    admission['audit_files'] = audit_files
    admission_raw = canonical(admission); admission_sha = digest(admission_raw)
    (admitted / 'conditional-admission.json').write_bytes(admission_raw)
    manifest = {'schema': 'botw-shader-conditional-source-manifest-v1', 'mode': 'conditional_source',
        'output': '.', 'runnable': False, 'runtime_compile_required': True, 'profile': PROFILE, 'chain': chain,
        'source_adapter_sha256': adapter_sha, 'conditional_admission_sha256': admission_sha, 'results': published}
    manifest_raw = canonical(manifest); (package / 'manifest-build.json').write_bytes(manifest_raw)
    publication = {'schema': 'botw-shader-conditional-publication-v1', 'profile': PROFILE,
        'package': str(package.relative_to(destination)), 'manifest_sha256': digest(manifest_raw),
        'conditional_admission_sha256': admission_sha, 'subset_sha256': proof_sha,
        'binding_contract_sha256': digest(binding_raw), 'source_adapter_sha256': adapter_sha,
        'format_contract_sha256': digest(format_raw),
        'captures': len(accepted), 'variants': len(published), 'guest_or_gpu_executed': False,
        'device_compilation_tested': False}
    write(destination / 'conditional-publication.json', publication)
    print(json.dumps({'conditional_captures': len(accepted), 'variants': len(published),
                      'rejected': len(rejected), 'chain': chain, 'runnable': False}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'captures', 'candidates', 'destination'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--xlate', type=Path, required=True)
    parser.add_argument('--spirv-cross', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--pairs', type=Path)
    try:
        return build(**vars(parser.parse_args(argv)))
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print('conditional source publication refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
