"""Controlled offline NSO rewrite. This never executes a Guest or a device API."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[3]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
COMMIT = '65fa9caab486768f70da98c51c2421aff76f676d'
TREE = '5b1545c089970a2aca0325aa31fa71bce217813f'
TOOL_SHA = '2b3c034c9151d9b20e860ae374e96f4ee56c009f98bddd34bbc5adc3adf9ff36'
PATCH_SHA = '4bcb82fb7385e9105e8d42fb090dace02bcbed02679a7176c00ffa5ce5481f21'
PATCHED_SHA = 'a69fc5527fa7ea76a9d1b46cccd63e603627711bd7ef4be0a9cb9e944f8f866f'
PROFILE_SHA = '1269ab1df53a1a19852cb7c65dcd5da1baa49b23e96e5d691b005fc4bbf26312'
PROVER_SHA = 'a935a62f2640763ec2f11569a8ffcbcb794cb63efd69409bb21fc4d23dd8b9fa'
PREFLIGHT_SHA = 'f3f44bb41c94a1d07c3819c49458f713237e7e657e204d964ee1bb4b324ec096'
VERIFIER_SHA = 'bc7baca8c9eba20ff53ee4c4031fa159d685fb785204d1132aef3b4a92d5c62f'
NSO_SHA = '8f5b491b1718d88b96ac24892c4816512e71f173e07718ade49399e698f6a272'
MODULES = ('main', 'subsdk0', 'subsdk1', 'subsdk2', 'subsdk3', 'sdk')
VERSIONS = {'capstone': '5.0.9', 'lz4': '4.4.5'}
KEYS = ('K18', 'KTLS', 'KTLR', 'KGATE', 'KRES0', 'KRES1', 'KASPECT', 'KASPECTUI')
HEAP_SITES = ((0x735304, bytes.fromhex('004cb852')), (0x7353b0, bytes.fromhex('080cb852')))
MAX_FILE = 128 * 1024**2


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read_file(path, maximum=MAX_FILE):
    path = Path(path)
    require(not path.is_symlink(), 'nonregular input')
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, 'nonregular input')
        raw = stream.read(maximum+1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    require(len(raw) <= maximum and all(getattr(before, k) == getattr(after, k) for k in fields), 'input changed')
    return raw


def checked(path, expected):
    raw = read_file(path)
    require(sha(raw) == expected, 'source digest')
    return raw


def source_module(name, path, expected):
    raw = checked(path, expected)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


def child_environment(scripts, temporary):
    return {'PATH': os.defpath, 'LC_ALL': 'C', 'TMPDIR': str(temporary),
            'PYTHONPATH': str(scripts), 'PYTHONNOUSERSITE': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONOPTIMIZE': '0',
            'DM_RULESET': 'native', 'DM_PRIVATE_GATES': '0'}


def admit_source():
    lock_raw = read_file(COMPONENT/'SOURCE.lock.json', 16*1024**2)
    lock = json.loads(lock_raw)
    require(lock['upstream_commit'] == COMMIT and lock['upstream_tree'] == TREE, 'source identity')
    own = read_file(Path(__file__))
    own_row = lock.get('adapter_files', {}).get('tools/rewrite_base_v0.py')
    require(own_row == {'bytes': len(own), 'sha256': sha(own)}, 'runner not source bound')
    for name, expected in (('scripts/botw_native/branch_tables.py', PROVER_SHA),
                           ('scripts/botw_native/nso.py', NSO_SHA)):
        row = lock.get('external_code_files', {}).get(name)
        require(row is not None and row.get('sha256') == expected, 'dependency not source bound')
        checked(ROOT/name, expected)
    verifier = source_module('_nxnative_source_verifier', COMPONENT/'tools/verify_source.py', VERIFIER_SHA)
    closure = verifier.verify_component(COMPONENT)
    preflight = source_module('_nxnative_profile_preflight', COMPONENT/'tools/preflight_profile.py', PREFLIGHT_SHA)
    profile = preflight.load_profile(COMPONENT/'profile/base_v0.json')
    checked(COMPONENT/'profile/base_v0.json', PROFILE_SHA)
    checked(COMPONENT/'upstream/dm/tools/patch_nso.py', TOOL_SHA)
    checked(COMPONENT/'patches/base-v0-signed-jump-table.patch', PATCH_SHA)
    require(read_file(COMPONENT/'SOURCE.lock.json', 16*1024**2) == lock_raw, 'source lock changed')
    nso = source_module('_nxnative_nso_reader', ROOT/'scripts/botw_native/nso.py', NSO_SHA)
    return profile, preflight, nso, {'source_lock_sha256': sha(lock_raw), 'closure': closure}


def dependency_context():
    require(sys.flags.optimize == 0, 'optimized Python disables vendor assertions')
    versions = {name: metadata.version(name) for name in VERSIONS}
    require(versions == VERSIONS, 'Python dependency versions')
    package_roots = {name:str(Path(metadata.distribution(name).locate_file('')).resolve()) for name in VERSIONS}
    return {'python': str(Path(sys.executable).absolute()), 'python_version': sys.version.split()[0],
            'package_roots': package_roots, 'versions': versions}


def layout(raw):
    data_address, data_size = struct.unpack_from('<II', raw, 0x34)
    memory = data_address + data_size + struct.unpack_from('<I', raw, 0x3c)[0]
    key = (memory+0x3fff) & ~0x3fff
    patch = (memory+0x4000+0xffff) & ~0xffff
    require(patch <= 0x7000000, 'fixed profile unexpectedly requires FAR layout')
    values = {'PATCH_OFF': patch, 'KEY_PAGE_OFF': key, 'KEY_PAGE_SIZE': 0x4000,
              'TEXT_END': struct.unpack_from('<I', raw, 0x18)[0],
              'RO_OFF': struct.unpack_from('<I', raw, 0x24)[0],
              'DATA_OFF': data_address}
    values.update({'KEY_'+name+'_IDX': index for index, name in enumerate(KEYS)})
    return values


def bootstrap_source(scripts, package_roots):
    # -S suppresses site hooks; only the reviewed venv dependency directory is added.
    return ("import hashlib,pathlib,sys,types\n"
            "sys.path.extend("+repr(list(dict.fromkeys(package_roots.values())))+")\n"
            "from importlib.metadata import version\n"
            "assert {n:version(n) for n in ('capstone','lz4')} == "+repr(VERSIONS)+"\n"
            "p=pathlib.Path("+repr(str(scripts/'botw_native/branch_tables.py'))+")\n"
            "s=p.read_bytes();assert hashlib.sha256(s).hexdigest()=="+repr(PROVER_SHA)+"\n"
            "package=types.ModuleType('botw_native');package.__path__=[str(p.parent)]\n"
            "sys.modules['botw_native']=package\n"
            "m=types.ModuleType('botw_native.branch_tables');m.__file__=str(p)\n"
            "sys.modules[m.__name__]=m;exec(compile(s,str(p),'exec'),m.__dict__)\n"
            "p=pathlib.Path(sys.argv[1]);s=p.read_bytes()\n"
            "assert hashlib.sha256(s).hexdigest()=="+repr(PATCHED_SHA)+"\n"
            "sys.argv=sys.argv[1:]\n"
            "exec(compile(s,str(p),'exec'),{'__name__':'__main__','__file__':str(p),'__package__':None})\n")


def invoke_vendor(command, environment, log):
    with Path(log).open('xb') as stream:
        subprocess.run(command, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=600)


def lines(raw):
    require(b'\0' not in raw and b'\r' not in raw and (not raw or raw.endswith(b'\n')), 'invalid table')
    return raw.decode('ascii').splitlines()


def branch_target(image, address):
    word = struct.unpack_from('<I', image, address)[0]
    require(word & 0xfc000000 == 0x14000000, 'site is not a branch')
    relative = word & 0x3ffffff
    return address + (relative-(1 << 26) if relative & (1 << 25) else relative)*4


def validate_module(directory, name, raw, original):
    directory = Path(directory)
    expected = layout(raw)
    build = raw[0x40:0x50].hex()
    headers = {}
    for line in lines(read_file(directory/(name+'.fixups.tsv'), 65536)):
        match = re.fullmatch(r'# ([A-Z0-9_]+)\t([^\s]+)', line)
        require(match is not None and match[1] not in headers, 'invalid layout header')
        headers[match[1]] = match[2]
    require(headers.pop('BUILD_ID', None) == build, 'layout build identity')
    require(set(headers) == set(expected) and {k:int(v, 0) for k,v in headers.items()} == expected, 'derived layout mismatch')
    for kind in ('cuts', 'obs', 'params'):
        require(read_file(directory/f'{name}.{kind}.tsv', 65536) == f'# BUILD_ID\t{build}\n'.encode(), 'unexpected native rules')
    image = read_file(directory/(name+'.patched.bin'))
    patch, key, text = expected['PATCH_OFF'], expected['KEY_PAGE_OFF'], expected['TEXT_END']
    require(patch < len(image) < 0x8000000 and len(image)%4 == 0 and
            image[key:key+0x4000] == bytes(0x4000), 'image/key bounds')
    allowed, basic, extra = set(), {}, set()
    for line in lines(read_file(directory/(name+'.sites.tsv'), 4*1024**2)):
        fields = line.split('\t')
        address = int(fields[0], 0)
        require(address%4 == 0 and 0 <= address < text, 'site bounds')
        kind = fields[1]
        if kind in ('W', 'J', 'T'):
            require(address not in extra, 'duplicate extended site')
            extra.add(address)
            if kind == 'W':
                require(len(fields) == 3 and address < int(fields[2],0) <= text, 'window bounds')
            elif kind == 'J':
                require(len(fields) == 4, 'dense jump row')
                base, count = int(fields[2],0), int(fields[3])
                require(base%4 == 0 and 0 < count <= 65536 and 0 <= base <= base+4*(count-1) < text, 'dense target bounds')
            else:
                require(len(fields) == 3, 'signed jump row')
                targets = [int(v,0) for v in fields[2].split(' ')]
                require(0 < len(targets) <= 4096 and all(t%4 == 0 and 0 <= t < text for t in targets), 'signed target bounds')
        else:
            require(len(fields) == 2 and address not in basic and kind in
                    ('x18','svc','tpidr_r','tpidr_w','tpidrro_r','cntpct','cntvct','ctr'), 'invalid basic site')
            basic[address] = kind
        if kind == 'ctr':
            word = struct.unpack_from('<I', original, address)[0]
            require(word & 0xffffffe0 == 0xd53b0020 and (word&31) != 18 and
                    struct.unpack_from('<I', image, address)[0] == (0xd2a00080 | (word&31)), 'CTR replacement')
        else:
            target = branch_target(image, address)
            require(patch <= target < len(image), 'rewrite branch bounds')
        allowed.add(address)
    restored = bytearray(image[:len(original)])
    for address in allowed:
        restored[address:address+4] = original[address:address+4]
    require(bytes(restored) == original, 'original image changed outside declared sites')
    require(image[len(original):patch] == bytes(patch-len(original)), 'BSS/key/padding changed')
    returns, origins, gate_count = set(), set(), 0
    for line in lines(read_file(directory/(name+'.gates.tsv'), 1024**2)):
        fields = line.split('\t')
        require(len(fields) == 4, 'gate columns')
        ret, kind, arg, origin = int(fields[0],0), fields[1], int(fields[2]), int(fields[3],0)
        require(ret%4 == origin%4 == 0 and patch < ret < len(image)-4 and 0 <= origin < text and
                ret not in returns and origin not in origins and basic.get(origin) == kind, 'gate bounds/identity')
        word = struct.unpack_from('<I', original, origin)[0]
        require((kind == 'svc' and word & 0xffe0001f == 0xd4000001 and arg == (word>>5)&0xffff and 0 <= arg < 128) or
                (kind in ('cntpct','cntvct') and word & 0xffffffe0 == (0xd53be020 if kind == 'cntpct' else 0xd53be040) and arg == word&31), 'non-normal gate')
        require(struct.unpack_from('<I',image,ret)[0] == 0xa8c17bfd and branch_target(image,ret+4) == origin+4, 'gate return structure')
        returns.add(ret); origins.add(origin); gate_count += 1
    require(origins == {a for a,k in basic.items() if k in ('svc','cntpct','cntvct')}, 'gate coverage')
    if name == 'main':
        require(all(original[a:a+4] == image[a:a+4] == word for a,word in HEAP_SITES), 'original H/R literals changed')
    return {'image_bytes':len(image), 'image_sha256':sha(image), 'layout':expected,
            'normal_gate_count':gate_count, 'site_origins':len(allowed),
            'artifacts':{kind:sha(read_file(directory/f'{name}.{kind}')) for kind in
                         ('patched.bin','fixups.tsv','gates.tsv','sites.tsv','cuts.tsv','obs.tsv','params.tsv')}}


def rewrite_bundle(exefs, output):
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), 'output must be fresh')
    profile, preflight, nso, binding = admit_source()
    preflight.inspect_exefs(exefs, profile['modules'])
    dependencies = dependency_context()
    snapshots = {name:read_file(Path(exefs)/name) for name in (*MODULES, 'rtld')}
    for name, raw in snapshots.items():
        row = profile['modules'][name]
        require(len(raw) == row['bytes'] and sha(raw) == row['sha256'] and raw[0x40:0x60].hex() == row['build_id'], 'snapshot identity')
    originals = {name:nso.initialized_image(raw,name) for name,raw in snapshots.items() if name in MODULES}
    require(all(originals['main'][a:a+4] == word for a,word in HEAP_SITES), 'fixed main H/R context')
    output.mkdir(parents=False, exist_ok=False)
    inputs, work, artifacts, logs = (output/name for name in ('inputs','tool','rewrite','logs'))
    for directory in (inputs, work/'dm/tools', artifacts, logs, output/'tmp'):
        directory.mkdir(parents=True, exist_ok=False)
    for name, raw in snapshots.items():
        (inputs/name).write_bytes(raw)
    preflight.inspect_exefs(inputs, profile['modules'])
    tool = work/'dm/tools/patch_nso.py'
    tool.write_bytes(checked(COMPONENT/'upstream/dm/tools/patch_nso.py', TOOL_SHA))
    subprocess.run(['patch','--fuzz=0','--batch','-p1','-i',str(COMPONENT/'patches/base-v0-signed-jump-table.patch')],
                   cwd=work, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    checked(tool, PATCHED_SHA)
    bootstrap = work/'source_entry.py'
    bootstrap.write_text(bootstrap_source(ROOT/'scripts',dependencies['package_roots']))
    environment = child_environment(ROOT/'scripts', output/'tmp')
    results = {}
    for name in MODULES:
        invoke_vendor([dependencies['python'],'-B','-S',str(bootstrap),str(tool),str(inputs/name),str(artifacts),name],
                      environment, logs/(name+'.log'))
        results[name] = validate_module(artifacts,name,snapshots[name],originals[name])
    expected = {f'{name}.{kind}' for name in MODULES for kind in
                ('patched.bin','fixups.tsv','gates.tsv','sites.tsv','cuts.tsv','obs.tsv','params.tsv')} | {'build_src.sha256'}
    require({p.name for p in artifacts.iterdir()} == expected and
            read_file(artifacts/'build_src.sha256',256) == (PATCHED_SHA+'  patch_nso.py\n').encode(), 'unexpected output members/source')
    preflight.inspect_exefs(inputs, profile['modules'])
    report = {'schema':'nxnative-base-v0-offline-rewrite-v1','status':'rewritten_offline',
              **binding,'upstream_commit':COMMIT,'profile_sha256':PROFILE_SHA,
              'original_tool_sha256':TOOL_SHA,'patched_tool_sha256':PATCHED_SHA,
              'jump_patch_sha256':PATCH_SHA,'prover_sha256':PROVER_SHA,
              'bootstrap_sha256':sha(read_file(bootstrap)), 'dependencies':dependencies,
              'module_order':list(MODULES),'rtld_loaded':False,'input_sha256':{n:sha(v) for n,v in snapshots.items()},
              'modules':results,'ruleset':'native','private_gates':False,
              'heap_policy':'original-logical-literals','heap_bytes':0xc2600000,'root_bytes':0xc0600000,
              'old_guest29_outputs_reused':False,'guest_executed':False,'device_accessed':False,
              'gameplay_verified':False,'runtime_compatibility_verified':False}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exefs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = rewrite_bundle(args.exefs,args.output)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        parser.exit(1,'offline rewrite failed: '+type(error).__name__+'\n')
    print(json.dumps({'status':report['status'],'modules':report['module_order'],'guest_executed':False}))


if __name__ == '__main__':
    main()
