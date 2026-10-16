"""Validate a complete conditional CPU publication for the unsigned iOS packager.

The caller first verifies its configured host really compiled the conditional
runtime. Only metadata and source artifacts are returned; private audit inputs
and the complete translator source remain on the packaging host.
"""
from pathlib import Path
import re
import sys
import tempfile

PROFILE = 'botw-switch-base-v0'
BINDING_SHA = '480e8ed95dc002acc574d90294234e11aaf99caf2afa647c8950ccffe9abe8a3'
FORMAT_SHA = '3f46576221f48db69abb5b553529fea2dd5f9a2ac1d14a50d5e7c57a499a2865'
HEX64 = re.compile(r'[0-9a-f]{64}')
KEY = re.compile(r'[0-9a-f]{16}')
BUDGET = 512 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError('conditional package refused: ' + message)


def hexadecimal(value, pattern=HEX64):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def shader_files(source, package_module):
    """Return exact bundle bytes and index after independent CPU audit checks."""
    source = package_module.checked_path(source)
    package_module.ios._tree(source)
    with tempfile.TemporaryDirectory(prefix='botw-conditional-package-snapshot-') as temporary:
        return _snapshot(source, Path(temporary).resolve(), package_module)


def _snapshot(source, temporary, package):
    digest = package.digest
    parse = package.ios._json
    source_ports = package.source_builder
    used = 0

    def snapshot(relative, expected=None, limit=64 * 1024 * 1024):
        nonlocal used
        package.ios._relative(relative)
        raw = package.read(source / relative, limit)
        require(expected is None or digest(raw) == expected, 'snapshot SHA256 differs: ' + relative)
        used += len(raw)
        require(used <= BUDGET, 'complete private snapshot exceeds bounded batch budget')
        target = temporary / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return raw

    publication_raw = snapshot('conditional-publication.json')
    admission_raw = snapshot('admitted/conditional-admission.json')
    proof_raw = snapshot('admitted/subset-proof.json')
    binding_raw = snapshot('admitted/base-bindings.json', BINDING_SHA)
    format_raw = snapshot('admitted/base-formats.json', FORMAT_SHA)
    publication, admission, proof = map(parse, (publication_raw, admission_raw, proof_raw))
    require(all(isinstance(v, dict) for v in (publication, admission, proof)), 'metadata must be objects')
    require(set(publication) == {'schema', 'profile', 'package', 'manifest_sha256', 'conditional_admission_sha256',
                'subset_sha256', 'binding_contract_sha256', 'format_contract_sha256', 'source_adapter_sha256',
                'captures', 'variants', 'guest_or_gpu_executed', 'device_compilation_tested'} and
            set(admission) == {'schema', 'profile', 'upstream_commit', 'scope', 'subset_sha256',
                'binding_contract_sha256', 'format_contract_sha256', 'source_adapter_sha256', 'captures', 'audit_files'} and
            set(proof) == {'schema', 'profile', 'scope', 'chain', 'upstream_commit', 'input_manifest_sha256',
                'source_adapter_sha256', 'binding_contract_sha256', 'format_contract_sha256',
                'candidate_request_sha256', 'static_inputs', 'toolchain', 'tools', 'options', 'accepted',
                'rejected', 'entire_game_coverage', 'guest_or_gpu_executed'}, 'conditional metadata fields differ')
    require(publication.get('schema') == 'botw-shader-conditional-publication-v1' and
            admission.get('schema') == 'botw-shader-conditional-admission-v1' and
            proof.get('schema') == 'botw-shader-conditional-subset-proof-v1', 'missing complete conditional admission/proof')
    require(all(v.get('profile') == PROFILE for v in (publication, admission, proof)), 'fixed base profile differs')
    require(publication.get('guest_or_gpu_executed') is False and publication.get('device_compilation_tested') is False and
            proof.get('guest_or_gpu_executed') is False and proof.get('entire_game_coverage') is False,
            'CPU source may not claim device compilation, execution or complete coverage')
    for name, raw in (('base_v0_graphics_binding.json', binding_raw), ('base_v0_graphics_formats.json', format_raw)):
        require(package.read(package.COMPONENT / 'adapter' / name) == raw, 'local fixed base authority differs')
    for value in (publication, admission, proof):
        require(value.get('binding_contract_sha256') == digest(binding_raw) and
                value.get('format_contract_sha256') == digest(format_raw), 'base contract identity differs')
    require(publication.get('conditional_admission_sha256') == digest(admission_raw) and
            publication.get('subset_sha256') == admission.get('subset_sha256') == digest(proof_raw),
            'publication/admission/subset raw SHA256 differs')
    adapter_path = package.COMPONENT / 'adapter/shader_conditional_build.py'
    adapter_raw = package.read(adapter_path)
    adapter_sha = digest(adapter_raw)
    require(all(v.get('source_adapter_sha256') == adapter_sha for v in (publication, admission, proof)),
            'conditional source publisher identity differs')
    # This is trusted repository code; supplied Python is imported only below,
    # after all 374 original/adapted files have been copied and checked again.
    private_adapter = temporary / 'trusted/experiments/botw_upstream_host/adapter/shader_conditional_build.py'
    private_adapter.parent.mkdir(parents=True)
    private_adapter.write_bytes(adapter_raw)
    (private_adapter.parent / 'base_v0_graphics_formats.json').write_bytes(format_raw)
    search_path = list(sys.path)
    try:
        publisher = package.load(private_adapter, '_package_trusted_conditional_publisher')
    finally:
        sys.path[:] = search_path
    require(proof.get('scope') == admission.get('scope') == publisher.SCOPE and
            proof.get('upstream_commit') == admission.get('upstream_commit') == publisher.upstream_shaders.COMMIT,
            'fixed original source scope or commit differs')
    stage = source / 'toolchain'
    hashes = source_ports.source_toolchain_hashes(stage)
    require(len(hashes) == 374 and proof.get('toolchain') == hashes, 'complete staged source closure differs')
    for name, expected in sorted(hashes.items()):
        snapshot('toolchain/' + name, expected, 8 * 1024 * 1024)
    for name in ('v8-stage-receipt.json', 'base-graphics-stage-receipt.json'):
        snapshot('toolchain/' + name)
    require(source_ports.source_toolchain_hashes(temporary / 'toolchain') == hashes,
            'private toolchain bytes differ from their checked source')
    tool = source_ports.load(temporary / 'toolchain/dm/gfx/tools/botw_shader_build.py',
                             '_package_verified_conditional_tool')
    chain = proof.get('chain')
    require(hexadecimal(chain, re.compile(r'[0-9a-f]{12}')), 'invalid complete source chain')
    options, identity = proof.get('options'), proof.get('tools')
    require(isinstance(options, dict) and isinstance(identity, dict), 'missing original tool options/identity')
    require(options.get('xlate_debug') is False and options.get('xlate_reduced') is False and
            options.get('conditional_scope') == publisher.SCOPE and
            options.get('source_adapter_sha256') == adapter_sha and
            options.get('binding_contract_sha256') == BINDING_SHA and
            options.get('format_contract_sha256') == FORMAT_SHA and
            options.get('input_manifest_sha256') == proof.get('input_manifest_sha256') and
            hexadecimal(options.get('input_manifest_sha256')) and
            options.get('candidate_request_sha256') == proof.get('candidate_request_sha256') and
            hexadecimal(options.get('candidate_request_sha256')) and
            options.get('complete_staged_source_sha256') == digest(publisher.canonical(hashes)),
            'complete conditional source options differ')
    xlate, cross = identity.get('xlate', {}), identity.get('spirv_cross', {})
    expected_dlls = {'dmsh_xlate.dll', 'Ryujinx.Graphics.Shader.dll', 'Ryujinx.Common.dll', 'Spv.Generator.dll'}
    require(isinstance(xlate, dict) and isinstance(cross, dict) and xlate.get('built_from_repo_source') is True and
            xlate.get('embedded_source_sha16') == xlate.get('repo_source_sha16') == tool.repo_xlate_source_sha() and
            isinstance(xlate.get('dll_sha16'), dict) and set(xlate['dll_sha16']) == expected_dlls and
            all(hexadecimal(v, re.compile(r'[0-9a-f]{16}')) for v in xlate['dll_sha16'].values()) and
            isinstance(cross.get('revision'), str) and bool(cross['revision']) and
            hexadecimal(cross.get('sha16'), re.compile(r'[0-9a-f]{16}')) and
            identity.get('metal') == {'backend': 'device-source-required', 'xcrun_metal': None, 'xcrun_metallib': None},
            'original translator/module identity or deferred compiler port differs')
    tools = tool.Tools(); tools.identity = identity
    require(tools.chain(options) == chain, 'recomputed original source/module chain differs')
    directory = 'botw-c-' + chain
    require(publication.get('package') == 'build/' + directory, 'package is not its exact chain-relative directory')
    captures, accepted = admission.get('captures'), proof.get('accepted')
    require(isinstance(captures, dict) and 1 <= len(captures) <= 100000 and
            isinstance(accepted, dict) and set(captures) == set(accepted) and
            all(hexadecimal(k, KEY) for k in captures), 'accepted full capture batch differs')
    require(type(publication.get('captures')) is int and publication['captures'] == len(captures) and
            type(publication.get('variants')) is int and publication['variants'] == 4 * len(captures),
            'publication capture/variant count differs')
    require(isinstance(proof.get('rejected'), list), 'missing explicit rejected candidate list')
    rejected = [r.get('key') for r in proof['rejected'] if isinstance(r, dict)]
    require(len(rejected) == len(proof['rejected']) and all(hexadecimal(k, KEY) for k in rejected) and
            len(set(rejected)) == len(rejected) and not set(rejected) & set(captures), 'ambiguous rejected candidate batch')
    audit = admission.get('audit_files')
    expected_audit = {f'captures/{key}{suffix}' for key in captures for suffix in ('.json', '.control', '.code')}
    expected_audit.update(f'modes/{key}/{w}-{d}{suffix}' for key in captures
                          for w, d in publisher.MODES for suffix in ('.res.json', '.job.json'))
    require(isinstance(audit, dict) and set(audit) == expected_audit and
            all(hexadecimal(value) for value in audit.values()), 'missing, extra or unsafe accepted audit files')
    for name, expected in sorted(audit.items()):
        snapshot('admitted/' + name, expected)
    expected_admitted = expected_audit | {'conditional-admission.json', 'subset-proof.json', 'base-bindings.json', 'base-formats.json'}
    require(set(package.ios._tree(source / 'admitted')) == expected_admitted, 'admitted audit directory has unlisted files')
    by_variant = {}
    for key, row in accepted.items():
        require(isinstance(row, dict), 'invalid accepted capture proof')
        try:
            capture = tool.load_capture(str(temporary / 'admitted/captures'), key)
        except tool.CaptureError as error:
            raise ValueError('conditional full capture validation failed: ' + str(error)) from error
        require(capture.stage in (0, 1) and row.get('capture') == capture.summary() and
                row.get('capture_sha256') == capture.sha256 and row.get('stage_name') == capture.stage_name and
                row.get('c1_sha256') == digest(capture.c1), 'full capture/C1 identity differs from original load_capture')
        admitted = captures[key]
        require(isinstance(admitted, dict), 'invalid admitted capture identity')
        require(admitted.get('capture_sha256') == capture.sha256 and admitted.get('stage_name') == capture.stage_name,
                'admission capture full SHA or stage differs')
        variants, modes = admitted.get('variants'), row.get('modes')
        require(isinstance(variants, list) and len(variants) == 4 and len(set(variants)) == 4 and
                all(hexadecimal(v) for v in variants) and isinstance(modes, list) and len(modes) == 4,
                'capture must retain all four unique variants/modes')
        seen = set()
        for mode in modes:
            require(isinstance(mode, dict) and all(type(mode.get(n)) is int for n in ('window_origin_mode', 'depth_mode')),
                    'invalid exact integer device mode')
            device = mode['window_origin_mode'], mode['depth_mode']
            require(device in publisher.MODES and device not in seen, 'duplicate or unknown conditional device mode')
            seen.add(device)
            for kind in ('res', 'job'):
                name = f'modes/{key}/{device[0]}-{device[1]}.{kind}.json'
                require(mode.get(kind + '_file') == name and mode.get(kind + '_sha256') == audit[name],
                        'actual mode audit file identity differs')
            require(mode.get('translated_code_sha256') == digest(capture.translator_code), 'actual translated code SHA differs')
            require(publisher.canonical(mode.get('gates')) == publisher.canonical(
                    {'original_analyze_unknowns': 0, 'original_analyze_conflicts': 0,
                     'texture_queries': [], 'attributes': [], 'images': [], 'storage_buffers': []}),
                    'mode has an unresolved original analysis/query/resource gate')
        require(seen == set(publisher.MODES), 'incomplete four-mode conditional capture')
        for variant in variants:
            require(variant not in by_variant, 'variant is reused by multiple full capture identities')
            by_variant[variant] = (capture, modes)
    package_name = 'build/' + directory
    source_tree = package.ios._tree(source / package_name)
    expected_package = {'manifest-build.json'} | {v + suffix for v in by_variant for suffix in ('.contract.json', '.json', '.metal')}
    require(set(source_tree) == expected_package, 'package has missing or unlisted source artifacts')
    artifacts = {name: snapshot(package_name + '/' + name, limit=16 * 1024 * 1024) for name in source_tree}
    manifest_raw = artifacts['manifest-build.json']; manifest = parse(manifest_raw)
    require(isinstance(manifest, dict) and digest(manifest_raw) == publication.get('manifest_sha256') and
            manifest.get('schema') == 'botw-shader-conditional-source-manifest-v1' and
            manifest.get('mode') == 'conditional_source' and manifest.get('profile') == PROFILE and
            manifest.get('output') == '.' and manifest.get('chain') == chain and
            manifest.get('runnable') is False and manifest.get('runtime_compile_required') is True and
            manifest.get('source_adapter_sha256') == adapter_sha and
            manifest.get('conditional_admission_sha256') == digest(admission_raw), 'complete source manifest differs')
    results = manifest.get('results')
    require(isinstance(results, list) and len(results) == len(by_variant), 'manifest coverage differs')
    seen_variants = set(); seen_capture_modes = set()
    for result in results:
        require(isinstance(result, dict), 'invalid manifest result')
        variant = result.get('variant_sha256')
        require(variant in by_variant and variant not in seen_variants, 'duplicate or unadmitted manifest variant')
        seen_variants.add(variant); capture, modes = by_variant[variant]
        require(result.get('capture') == capture.summary() and result.get('status') == 'conditional_source_ready' and
                result.get('runnable') is False and result.get('runtime_compile_required') is True and
                all(result.get(n) == [] for n in ('errors', 'unknowns', 'conflicts')),
                'variant identity or delayed-source gates differ')
        records = result.get('files')
        require(isinstance(records, dict) and set(records) == {'contract', 'json', 'metal_source'}, 'requires exact three source artifacts')
        values = {}
        for kind, suffix in (('contract', '.contract.json'), ('json', '.json'), ('metal_source', '.metal')):
            name = variant + suffix; record = records[kind]
            require(isinstance(record, dict) and set(record) == {'path', 'sha256'} and record['path'] == name and
                    record['sha256'] == digest(artifacts[name]), 'artifact exact relative path or SHA256 differs')
            values[kind] = artifacts[name]
        require(digest(values['contract']) == variant, 'variant is not the actual raw contract SHA256')
        contract = parse(values['contract'])
        require(isinstance(contract, dict) and isinstance(contract.get('requirements'), dict), 'invalid conditional contract')
        requirements = contract['requirements']
        device_map = requirements.get('device', {})
        require(isinstance(device_map, dict) and set(device_map) == {'window_origin_mode', 'depth_mode'} and
                all(type(v) is int for v in device_map.values()), 'contract device mode is not exact')
        device = device_map['window_origin_mode'], device_map['depth_mode']
        require((capture.key, device) not in seen_capture_modes, 'multiple variants repeat the same capture device mode')
        seen_capture_modes.add((capture.key, device))
        mode = next((row for row in modes if (row['window_origin_mode'], row['depth_mode']) == device), None)
        require(mode is not None, 'contract mode has no admitted actual CPU proof')
        expected_contract = {'schema': 'botw-shader-conditional-translation-contract-v1', 'profile': PROFILE,
            'chain': chain, 'capture': capture.summary(), 'scope': publisher.SCOPE,
            'requirements': {'device': device_map, 'attributes': [], 'texture_queries': [], 'images': [],
                'storage_buffers': [], 'fragment_output0': {'type': 'float', 'nvn_formats': [37, 41, 46, 154]} if capture.stage == 1 else None},
            'proofs': {'binding_contract_sha256': BINDING_SHA, 'subset_sha256': digest(proof_raw)},
            'translation': {'source_adapter_sha256': adapter_sha, 'job_sha256': mode['job_sha256']}}
        require(publisher.canonical(contract) == publisher.canonical(expected_contract), 'contract scope/full identity/proof differs')
        require(values['metal_source'] and b'\0' not in values['metal_source'] and
                digest(values['metal_source']) == mode.get('msl_sha256') and
                all(hexadecimal(mode.get(n)) for n in ('fresh_msl_sha256', 'spirv_sha256', 'reflection_core_sha256')),
                'actual MSL bytes or CPU proof hashes differ')
        padded = mode.get('vs_pad')
        require(isinstance(padded, list) and all(type(v) is int and 0 <= v < 32 for v in padded) and
                len(set(padded)) == len(padded) and (capture.stage == 0 or not padded) and
                (padded or mode['fresh_msl_sha256'] == mode['msl_sha256']), 'fresh MSL/padding proof differs')
        msl = values['metal_source'].decode('utf-8', errors='strict')
        res = parse((temporary / 'admitted' / mode['res_file']).read_bytes())
        request, bindings, vertex_inputs, entry = publisher.assess_mode(tool, capture, res, msl, device)
        job_raw = (temporary / 'admitted' / mode['job_file']).read_bytes(); job = parse(job_raw)
        require(type(job.get('debug')) is bool and job.get('reduced') is False, 'invalid final translator retry options')
        job_request = tool.Facts()
        job_request.device_state = {'window_origin_mode': publisher.WINDOW[device[0]], 'depth_mode': publisher.DEPTH[device[1]]}
        if capture.stage == 1: job_request.outputs = {0: 'float'}
        expected_job = tool.make_job(capture, job_request, '/private/verified-code', options)
        expected_job['debug'] = job['debug']
        require(job_raw == publisher.canonical(publisher.logical_job(expected_job, capture)) and
                digest(job_raw) == mode['job_sha256'], 'actual final logical job/C1/queries/options differ')
        core = tool.reflection(capture, chain, entry, bindings, vertex_inputs, request, 'B')
        core.pop('facts_sha256', None)
        core.update(translator_options={'debug': job['debug'], 'reduced': job['reduced']}, vs_pad=mode.get('vs_pad'))
        core['raster'] = tool.fragment_raster(msl) if capture.stage == 1 else {'colors': []}
        require(digest(publisher.canonical(core)) == mode['reflection_core_sha256'], 'original analysis/reflection core differs')
        core.update(schema='botw-shader-conditional-reflection-v1', conditional_contract_sha256=variant,
            runnable=False, runtime_compile_required=True, runtime_compiler={'fast_math': False}, unknowns=[], conflicts=[])
        require(publisher.canonical(parse(values['json'])) == publisher.canonical(core), 'published reflection differs from actual original analysis/C1')
    require(seen_capture_modes == {(key, mode) for key in captures for mode in publisher.MODES}, 'manifest lacks an actual capture/mode')
    files = {'conditional-publication.json': publication_raw, 'conditional-admission.json': admission_raw,
             'subset-proof.json': proof_raw, 'base-bindings.json': binding_raw, 'base-formats.json': format_raw}
    files.update({directory + '/' + name: raw for name, raw in artifacts.items()})
    index = {'schema': 'botw-whole-conditional-shader-package-v1', 'profile': PROFILE, 'chain': chain,
        'directory': directory, 'publication_sha256': digest(publication_raw), 'admission_sha256': digest(admission_raw),
        'subset_sha256': digest(proof_raw), 'binding_contract_sha256': BINDING_SHA, 'format_contract_sha256': FORMAT_SHA}
    files['shader-package.json'] = publisher.canonical(index)
    return files, index
