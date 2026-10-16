"""Publish explicit portable MSL packages through the complete pinned builder.

Only the final artifact publication/Metal-compiler port changes. Extraction,
capture/facts admission, translator identity, SPIR-V/MSL conversion, resource
analysis and reflection remain the original modules. No rule-derived/default
facts are admitted and no Metal compiler or GPU runs on this machine.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.botw_native.shader_bindings import prepare_release

SOURCE_LIMIT = 16 * 1024 * 1024
REFLECTION_LIMIT = 1024 * 1024
MANIFEST_LIMIT = 64 * 1024 * 1024


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class SourcePublication:
    def __init__(self, tool, admission, captures, admission_sha256):
        self.tool, self.admission = tool, admission
        if not isinstance(admission_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', admission_sha256):
            raise ValueError('source publication requires the detached admission file SHA256')
        self.admission_sha256 = admission_sha256
        self.captures = {key: tool.load_capture(str(captures), key)
                         for key in admission['captures']}
        self.chain = None

    def identity(self, key, reflection, result):
        """Bind publication to the detached complete capture, not nullable fields."""
        admitted = self.admission['captures'].get(key)
        capture = self.captures.get(key)
        summary = result.get('capture')
        if (not isinstance(key, str) or not self.tool.KEY_RE.fullmatch(key) or
                admitted is None or capture is None or result.get('key') != key or
                not isinstance(summary, dict) or summary != capture.summary() or
                any(type(summary.get(k)) is not type(v) for k, v in capture.summary().items()) or
                capture.sha256[:16] != key or
                capture.sha256 != admitted.get('capture_sha256') or
                result.get('facts', {}).get('sha256') != admitted.get('facts_sha256') or
                any(result.get(k) != [] for k in ('errors', 'unknowns', 'conflicts')) or
                reflection.get('route') != 'B' or reflection.get('notes') != [] or
                reflection.get('key') != key + '_b' or reflection.get('dm_ctl') != key or
                reflection.get('schema') != self.tool.REFLECTION_SCHEMA or
                reflection.get('sha256') != capture.sha256 or
                reflection.get('facts_sha256') != admitted.get('facts_sha256') or
                reflection.get('c1') != capture.c1.hex() or
                reflection.get('stage') != self.tool.XLATE_STAGE.get(capture.stage) or
                not isinstance(self.chain, str) or not re.fullmatch(r'[0-9a-f]{12}', self.chain) or
                reflection.get('chain') != self.chain):
            raise ValueError('source publication requires the complete admitted identity and original gates')

    def publish(self, root, work, key, reflection, result, unused_metal_identity):
        """A source-ready artifact is explicitly not a compiled library."""
        self.identity(key, reflection, result)
        name = key + '_b.metal'
        source = self.tool.read_regular(str(Path(work) / name), SOURCE_LIMIT)
        if not source or b'\0' in source:
            raise ValueError('source is not bounded NUL-free UTF-8')
        source.decode('utf-8', errors='strict')
        self.tool.write_atomic(str(Path(root) / name), source)
        reflection_name = key + '_b.json'
        document = dict(reflection, runnable=False, runtime_compile_required=True,
                        unknowns=[], conflicts=[], runtime_compiler={'fast_math': False})
        self.tool.write_json(str(Path(root) / reflection_name), document)
        result.update(status='source_ready', runnable=False, runtime_compile_required=True,
            files={'json': {'path': reflection_name, 'sha256': sha(Path(root) / reflection_name)},
                   'metal_source': {'path': name, 'sha256': sha(Path(root) / name)}})

    def check_batch(self, root, manifest):
        """The final batch marker acknowledges exactly two real relative artifacts."""
        if (manifest.get('schema') != 'botw-shader-source-manifest-v1' or
                manifest.get('mode') != 'source' or manifest.get('chain') != self.chain or
                manifest.get('runnable') is not False or
                manifest.get('runtime_compile_required') is not True or manifest.get('output') != '.' or
                manifest.get('release_admission_sha256') != self.admission_sha256 or
                manifest.get('source_adapter_sha256') != sha(__file__)):
            raise ValueError('source manifest identity or delayed-compiler contract differs')
        results = manifest.get('results')
        if (not isinstance(results, list) or len(results) != len(self.captures) or
                {r.get('key') for r in results} != set(self.captures)):
            raise ValueError('source batch did not retain every admitted result exactly once')
        expected_names = {'manifest-build.json'} | {
            key + '_b' + suffix for key in self.captures for suffix in ('.json', '.metal')}
        if {p.name for p in Path(root).iterdir()} != expected_names:
            raise ValueError('source package contains missing or unlisted artifacts')
        for result in results:
            key = result['key']
            if (result.get('status') != 'source_ready' or result.get('runnable') is not False or
                    result.get('runtime_compile_required') is not True or
                    set(result.get('files', {})) != {'json', 'metal_source'}):
                raise ValueError('source result is not a complete delayed-compiler package')
            artifacts = {}
            for kind, suffix, limit in (('json', '.json', REFLECTION_LIMIT),
                                        ('metal_source', '.metal', SOURCE_LIMIT)):
                item = result['files'][kind]
                expected = key + '_b' + suffix
                if not isinstance(item, dict) or item.get('path') != expected:
                    raise ValueError('source artifact path is not its exact relative filename')
                raw = self.tool.read_regular(str(Path(root) / expected), limit)
                if hashlib.sha256(raw).hexdigest() != item.get('sha256'):
                    raise ValueError('source artifact SHA256 differs from the batch')
                artifacts[kind] = raw
            reflection = self.tool.strict_json(artifacts['json'], key + '_b.json')
            self.identity(key, reflection, result)
            if (reflection.get('runnable') is not False or
                    reflection.get('runtime_compile_required') is not True or
                    reflection.get('runtime_compiler') != {'fast_math': False}):
                raise ValueError('source reflection may not claim compiled or runnable')

    def manifest(self, mode, chain, options, tools, captures, facts, output, results, **unused):
        results = list(results)
        if mode != 'build' or chain != self.chain:
            raise ValueError('source publication has no probe/default/merge release mode')
        return {'schema': 'botw-shader-source-manifest-v1', 'mode': 'source',
            'runnable': False, 'runtime_compile_required': True, 'chain': chain,
            'output': '.', 'results': results,
            'release_admission_sha256': self.admission_sha256,
            'source_adapter_sha256': sha(__file__), 'tools': tools, 'options': options}


def build(inputs, captures, facts, derive_report, evidence, destination, *, xlate, spirv_cross, jobs=1):
    if type(jobs) is not int or not 1 <= jobs <= 256:
        raise ValueError('jobs must be an integer in 1..256')
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    destination = destination.resolve()
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    stage = load(Path(__file__).with_name('shader_v8_control.py'), 'source_v8_stage')
    # Source verification and exact v8 adaptation stay in the existing module.
    # Staging itself rejects a destination inside any Git working tree.
    stage.stage_toolchain(destination / 'toolchain')
    tool = load(destination / 'toolchain/dm/gfx/tools/botw_shader_build.py', 'source_complete_builder')
    admission = prepare_release(tool, inputs, captures, facts, derive_report, evidence,
                                destination / 'admitted')
    admission_sha256 = sha(destination / 'admitted/release-admission.json')
    publisher = SourcePublication(tool, admission, destination / 'admitted/captures', admission_sha256)
    original_tools = tool.Tools
    adapter_digest = sha(__file__)

    class RuntimeSourceTools(original_tools):
        def probe_metal(self):
            self.metal = None
            self.identity['metal'] = {'backend': 'device-source-required', 'xcrun_metal': None,
                'xcrun_metallib': None, 'source_adapter_sha256': adapter_digest}
            return self.identity['metal']

        def chain(self, options):
            publisher.chain = super().chain(dict(options, source_adapter_sha256=adapter_digest,
                release_admission_sha256=admission_sha256))
            return publisher.chain

    # A fresh private imported module owns these explicit final-stage ports.
    # No vendor source or algorithm is changed, and upstream main still performs
    # real translation/resource analysis with the detached admitted facts.
    tool.Tools = RuntimeSourceTools
    tool.publish_ready, tool.make_manifest = publisher.publish, publisher.manifest
    arguments = ['build', str(destination / 'admitted/captures'), '--facts', str(destination / 'admitted/facts'),
        '--out', str(destination / 'build'), '--xlate', str(xlate), '--spirv-cross', str(spirv_cross),
        '--jobs', str(jobs)]
    code = tool.main(arguments)
    if code:
        return code
    outputs = list((destination / 'build').glob('botw-b-*'))
    if (len(outputs) != 1 or outputs[0].name != 'botw-b-' + publisher.chain or
            outputs[0].is_symlink() or not outputs[0].is_dir()):
        raise ValueError('builder did not produce exactly one complete source batch')
    old = outputs[0]
    manifest = tool.strict_json(tool.read_regular(str(old / 'manifest-build.json'), MANIFEST_LIMIT),
                                'manifest-build.json')
    publisher.check_batch(old, manifest)
    results = manifest['results']
    output = old.with_name('botw-s-' + manifest['chain'])
    old.rename(output)
    receipt = {'schema': 'botw-shader-source-publication-v1', 'profile': 'botw-switch-base-v0',
        'package': str(output.relative_to(destination)), 'manifest_sha256': sha(output / 'manifest-build.json'),
        'release_admission_sha256': sha(destination / 'admitted/release-admission.json'),
        'source_adapter_sha256': adapter_digest, 'captures': len(results),
        'input_manifest_sha256': admission['input_manifest_sha256'],
        'coverage': 'exact admitted static input batch; not a claim of all game shader coverage',
        'device_compilation_tested': False, 'guest_or_gpu_executed': False}
    tool.write_json(str(destination / 'source-publication.json'), receipt)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'captures', 'facts', 'derive_report', 'evidence', 'destination'):
        parser.add_argument(name)
    parser.add_argument('--xlate', required=True)
    parser.add_argument('--spirv-cross', required=True)
    parser.add_argument('--jobs', type=int, default=1)
    args = parser.parse_args(argv)
    return build(**vars(args))


if __name__ == '__main__':
    raise SystemExit(main())
