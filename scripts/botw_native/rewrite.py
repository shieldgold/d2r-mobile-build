"""Run a pinned, verified source tool. Guest modules are never executed."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import urllib.request

UPSTREAM_COMMIT = 'e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8'
TOOL_SHA256 = 'c9a0c7574547ab945e4648bd6664e86fa4a77de12253a55e7b464232b2cdf274'
TOOL_URL = f'https://raw.githubusercontent.com/jhaizhou-ops/d2r-mobile/{UPSTREAM_COMMIT}/dm/tools/patch_nso.py'


def verify_profile(modules, profile):
    expected = profile['modules']
    actual = {m['name']: m for m in modules}
    if set(actual) != set(expected):
        raise ValueError('NSO module set does not match version profile')
    for name, item in expected.items():
        if any(actual[name][key] != item[key] for key in ('sha256', 'build_id')):
            raise ValueError('NSO version profile mismatch: ' + name)


def verified_tool(path):
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != TOOL_SHA256:
        raise ValueError('Upstream rewrite tool SHA256 mismatch; not executing')
    return path


def fetch_tool(cache):
    path = Path(cache) / UPSTREAM_COMMIT / 'patch_nso.py'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(TOOL_URL, headers={'User-Agent': 'botw-native-preflight'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(1024**2 + 1)
        if len(data) > 1024**2 or hashlib.sha256(data).hexdigest() != TOOL_SHA256:
            raise ValueError('Downloaded rewrite tool SHA256 mismatch')
        temporary = path.with_suffix('.part')
        temporary.write_bytes(data)
        temporary.replace(path)
    return verified_tool(path)


def check_generic_outputs(directory, name):
    counts = {}
    for kind in ('cuts', 'obs', 'params'):
        rows = [r for r in (Path(directory) / f'{name}.{kind}.tsv').read_text().splitlines()
                if r.strip() and not r.startswith('#')]
        if rows:
            raise ValueError(f'Unexpected game-specific {kind} in generic BOTW rewrite')
        counts[kind] = len(rows)
    return counts


def validate_structure(directory, name):
    directory = Path(directory)
    headers = {}
    for row in (directory / f'{name}.fixups.tsv').read_text().splitlines():
        fields = row.split('\t')
        if fields[0] in ('# PATCH_OFF', '# TEXT_END'):
            headers[fields[0][2:]] = int(fields[1], 0)
    image = (directory / f'{name}.patched.bin').read_bytes()
    patch, text = headers['PATCH_OFF'], headers['TEXT_END']
    if not 0 < text <= patch < len(image):
        raise ValueError('Invalid rewritten image layout')
    checked = 0
    for row in (directory / f'{name}.sites.tsv').read_text().splitlines():
        fields = row.split('\t')
        address = int(fields[0], 0)
        if address % 4 or not 0 <= address < text or address + 4 > len(image):
            raise ValueError('Invalid rewrite site address')
        word = struct.unpack_from('<I', image, address)[0]
        immediate = word & 0x3ffffff
        if immediate & 0x2000000:
            immediate -= 0x4000000
        target = address + immediate * 4
        if word & 0xfc000000 != 0x14000000 or not patch <= target < len(image):
            raise ValueError('Rewrite branch is outside the patch area')
        checked += 1
    gates = 0
    for row in (directory / f'{name}.gates.tsv').read_text().splitlines():
        ret, _, _, original = row.split('\t')
        if not patch <= int(ret, 0) <= len(image) or not 0 <= int(original, 0) < text:
            raise ValueError('Invalid host gate address')
        gates += 1
    return {'branch_sites_checked': checked, 'host_gate_addresses_checked': gates,
            'semantic_equivalence_verified': False}


def rewrite_module(tool, module, output, timeout=300, *, host39=False, heap_policy=False):
    if heap_policy and not host39:
        raise ValueError('The explicit heap policy requires the audited Host39 pipeline')
    tool, module, output = verified_tool(tool), Path(module), Path(output)
    if heap_policy:
        from .nso import inspect_nso
        profile = json.loads((Path(__file__).resolve().parents[2] / 'profiles/botw-switch-base-v0.json').read_text())
        expected = profile['modules'].get(module.name)
        actual = inspect_nso(module.read_bytes(), module.name)
        if expected is None or any(actual[key] != expected[key] for key in ('sha256', 'build_id')):
            raise ValueError('The explicit heap policy requires the fixed original module fingerprint: ' + module.name)
    output.mkdir(parents=True, exist_ok=True)
    log = output / f'{module.name}.rewrite.log'
    env = dict(os.environ, DM_RULESET='native', DM_PRIVATE_GATES='0')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    # Do not inherit flags that could enable game-specific experiments.
    for key in list(env):
        if key.startswith('DM_') and key not in ('DM_RULESET', 'DM_PRIVATE_GATES'):
            del env[key]
    try:
        with log.open('wb') as stream:
            result = subprocess.run([sys.executable, '-m', 'botw_native.rewriter_driver', str(tool.resolve()), str(module.resolve()),
                                     str(output.resolve()), module.name], env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'name': module.name, 'status': 'failed', 'reason': 'rewrite timeout', 'log': log.name}
    if result.returncode:
        return {'name': module.name, 'status': 'failed', 'reason': f'rewriter exit {result.returncode}', 'log': log.name}
    counts = check_generic_outputs(output, module.name)
    image = output / f'{module.name}.patched.bin'
    sites = output / f'{module.name}.sites.tsv'
    adaptation = None
    heap_adaptation = None
    generic_digest = hashlib.sha256(image.read_bytes()).hexdigest()
    if host39:
        from .hipc_address import apply, verify
        from .nso import initialized_image
        generic_bytes = image.read_bytes()
        generic_digest = hashlib.sha256(generic_bytes).hexdigest()
        updated, adaptation = apply(module.name, initialized_image(module.read_bytes(), module.name),
            generic_bytes, {'generic_site_offsets': [int(row.split('\t')[0], 0) for row in sites.read_text().splitlines()]})
        verify(updated, adaptation, expected_input_sha256=generic_digest)
        if heap_policy and module.name == 'main':
            from .heap_policy import apply as apply_heap, verify as verify_heap
            layout = {}
            for row in (output / 'main.fixups.tsv').read_text().splitlines():
                fields = row.split()
                if len(fields) == 3 and fields[0] == '#':
                    layout[fields[1]] = int(fields[2], 0)
            host39_digest = hashlib.sha256(updated).hexdigest()
            updated, heap_adaptation = apply_heap(initialized_image(module.read_bytes(), module.name),
                updated, layout, generic_site_offsets=[int(row.split('\t')[0], 0) for row in sites.read_text().splitlines()])
            verify_heap(updated, heap_adaptation, expected_input_sha256=host39_digest)
        temporary = image.with_suffix('.host39.part')
        temporary.write_bytes(updated)
        temporary.replace(image)
    structure = validate_structure(output, module.name)
    result = {'name': module.name, 'status': 'rewritten', 'log': log.name, 'generic_tables': counts,
            'structure': structure,
            'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
            'rewrite_site_count': len(sites.read_text().splitlines()),
            'semantic_equivalence_verified': False}
    if adaptation is not None:
        result['generic_image_sha256'] = generic_digest
        result['host_address_adaptation'] = adaptation
    if heap_adaptation is not None:
        result['host39_image_sha256'] = heap_adaptation['input_image_sha256']
        result['heap_adaptation'] = heap_adaptation
    return result
