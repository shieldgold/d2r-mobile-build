"""Typed vertex recipes are draw predicates, never inferred game facts."""
import copy
import hashlib
import inspect
import importlib.util
import json
import os
import py_compile
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest import mock

import test_botw_whole_shader_v8 as v8

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'experiments/botw_upstream_host'
C = ROOT / 'experiments/nxnative_base_v0'
PATCH = C / 'patches/base-v0-vertex-shader-pipeline.patch'
BASELINE = os.environ.get('NX_VERTEX_PIPELINE_BASELINE') == '1'


class VertexShaderContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='nx-owned-vertex-contract-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name).resolve()
        for relative in ('adapter/shader_conditional_build.py', 'adapter/shader_source_build.py',
                         'adapter/base_v0_graphics_formats.json', 'scripts/package_conditional.py'):
            path = cls.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((OLD / relative).read_bytes())
        if not BASELINE:
            p = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                               cwd=cls.work, capture_output=True, text=True)
            if p.returncode:
                raise AssertionError(p.stdout + p.stderr)
        cls.adapter = v8.load('nx_vertex_contract_owned', cls.work / 'adapter/shader_conditional_build.py')
        source = v8.load('nx_vertex_source_owned', OLD / 'adapter/shader_source_build.py')
        cls.stage = cls.work / 'toolchain'
        source.stage_source_toolchain(cls.stage)
        cls.tool = v8.load('nx_vertex_original_tool', cls.stage / 'dm/gfx/tools/botw_shader_build.py')
        control, code = v8.owned()
        fields = cls.tool.parse_control(control)
        full = cls.tool.capture_digest(fields, control, code)
        cls.capture = cls.tool.Capture('owned EXIT, no game content', full[:16], full, fields, control, code)
        cls.result = dict(key=full[:16], stage='vs', notes=[], images=[], sbufs=[], textures=[], cbufs=[])

    def assess(self, recipe, text, result=None):
        args = (self.tool, self.capture, result or self.result, text, (0, 0))
        # The original API compiles and runs, then rejects the actual attribute.
        if 'attributes' in inspect.signature(self.adapter.assess_mode).parameters:
            return self.adapter.assess_mode(*args, attributes=recipe)
        return self.adapter.assess_mode(*args)

    def test_complete_original_analysis_accepts_exact_float_recipe(self):
        text = 'struct main0_in { float4 position [[attribute(0)]]; };\nvertex void main0() {}\n'
        request, bindings, inputs, entry = self.assess({0: 'float'}, text)
        self.assertEqual(request.attributes, {0: 'float'})
        self.assertEqual(inputs, [{'location': 0, 'msl_type': 'float4'}])
        self.assertEqual(entry, ('vertex', 'main0'))
        self.assertEqual(bindings, [])

    def test_original_typed_analysis_accepts_integer_and_rejects_contradiction(self):
        text = 'struct main0_in { uint4 bones [[attribute(5)]]; };\nvertex void main0() {}\n'
        self.assertEqual(self.assess({5: 'uint'}, text)[0].attributes, {5: 'uint'})
        with self.assertRaises(ValueError):
            self.assess({5: 'float'}, text)

    def test_recipe_requires_unique_exact_locations_and_supported_types(self):
        text = 'struct main0_in { float4 a [[attribute(0)]]; };\nvertex void main0() {}\n'
        for recipe in ({}, {0: 'double'}, {16: 'float'}, {True: 'float'}, {0: 'float', 1: 'float'}):
            with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                self.assess(recipe, text)
        for changed in (text.replace('float4', 'float5'), text.replace('float4', 'half4'),
                        text.replace('};', 'float4 b [[attribute(0)]]; };')):
            with self.subTest(text=changed), self.assertRaises(ValueError):
                self.assess({0: 'float'}, changed)

    def test_no_texture_query_or_driver_fallback_is_promoted_by_recipe(self):
        text = 'struct main0_in { float4 a [[attribute(0)]]; };\nvertex void main0() {}\n'
        bad = copy.deepcopy(self.result)
        bad['notes'] = ['owned unknown query']
        with self.assertRaises(ValueError):
            self.assess({0: 'float'}, text, bad)
        driver = 'struct spvDescriptorSetBuffer0 {\nconstant float4* vp_c0 [[id(0)]];\n};\n'
        driver += text.replace('{}', '{auto v=(*vp_c0)._m0[97];}')
        with self.assertRaises(ValueError):
            self.assess({0: 'float'}, driver)

    def test_request_is_one_explicit_recipe_per_capture_and_binds_raw_bytes(self):
        value = dict(schema='nxnative-base-v0-vertex-recipes-v1', profile='botw-switch-base-v0',
                     recipes={self.capture.key: [{'location': 0, 'component_type': 'float'}]})
        request = self.work / 'owned-recipes.json'
        raw = (json.dumps(value) + '\n').encode()
        request.write_bytes(raw)
        selected = self.adapter.requested_keys(request)
        self.assertEqual(selected[0], [self.capture.key])
        self.assertEqual(selected[1], hashlib.sha256(raw).hexdigest())
        self.assertEqual(selected[2], {self.capture.key: {0: 'float'}})
        for changed in (dict(value, facts=True), dict(value, profile='botw-1.9'),
                        dict(value, recipes={self.capture.key: value['recipes'][self.capture.key] * 2}),
                        dict(value, recipes={self.capture.key: []})):
            request.write_text(json.dumps(changed))
            with self.subTest(value=changed), self.assertRaises(ValueError):
                self.adapter.requested_keys(request)

    @unittest.skipIf(BASELINE, 'separate thin pipeline wrapper is not the original baseline')
    def test_complete_native_integer_helper_produces_only_explicit_conditions(self):
        tool = v8.load('nx_vertex_wrapper_owned', C / 'tools/build_vertex_shader_contracts.py')
        package = tool.bootstrap()
        integer = tool.integer_recipe_function(package)
        source = ('struct main0_in { float4 position [[attribute(0)]]; float4 bones [[attribute(5)]]; };\n'
                  'vertex main0_out main0(main0_in in) {\n'
                  '  float x = in.position.x + 1.0;\n'
                  '  uint4 indices = as_type<uint>(in.bones);\n}\n')
        self.assertEqual(tool.recipe_from_source(source, integer), [
            {'location': 0, 'component_type': 'float'}, {'location': 5, 'component_type': 'uint'}])
        for changed in (source.replace('attribute(5)', 'attribute(0)'),
                        source.replace('attribute(5)', 'attribute(16)'),
                        source.replace('float4 bones', 'double4 bones')):
            with self.subTest(source=changed), self.assertRaises(ValueError):
                tool.recipe_from_source(changed, integer)

    @unittest.skipIf(BASELINE, 'separate thin pipeline wrapper is not the original baseline')
    def test_private_pipeline_keeps_original_sources_and_complete_validator(self):
        tool = v8.load('nx_vertex_pipeline_wrapper_owned', C / 'tools/build_vertex_shader_contracts.py')
        paths = [OLD / 'adapter/shader_conditional_build.py', OLD / 'scripts/package_conditional.py']
        before = [p.read_bytes() for p in paths]
        with tempfile.TemporaryDirectory() as temporary:
            with tool.staged_pipeline(Path(temporary).resolve()) as (_, publisher, validator, ports):
                self.assertEqual(publisher.SCOPE, 'typed-vs-nonqueried-v1')
                self.assertTrue(callable(validator.shader_files))
                source = (ports.COMPONENT / 'scripts/package_conditional.py').read_text()
                self.assertIn('job_request.attributes = publisher.attribute_map', source)
                self.assertIn('all four modes must retain the same vertex recipe', source)
        self.assertEqual([p.read_bytes() for p in paths], before)

    @unittest.skipIf(BASELINE, 'new source-only bootstrap')
    def test_timestamp_valid_owned_cache_cannot_replace_locked_source(self):
        tool=v8.load('nx_vertex_cache_wrapper_owned',C/'tools/build_vertex_shader_contracts.py')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();path=root/tool.PACKAGE_SOURCE;path.parent.mkdir(parents=True)
            original=(ROOT/tool.PACKAGE_SOURCE).read_bytes()
            canary=b'raise RuntimeError("owned cache canary")\n'
            path.write_bytes(canary+b' '*(len(original)-len(canary)))
            py_compile.compile(str(path),doraise=True);metadata=path.stat()
            path.write_bytes(original);os.utime(path,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
            spec=importlib.util.spec_from_file_location('nx_owned_cache_control',path)
            with self.assertRaisesRegex(RuntimeError,'owned cache canary'):
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
            with mock.patch.object(tool,'ROOT',root):
                module=tool.bootstrap()
            self.assertEqual(Path(module.__file__),path)
            self.assertTrue(callable(module.original_validator))

    @unittest.skipIf(BASELINE, 'new source-only bootstrap')
    def test_bridge_rejects_foreign_or_unpinned_profile_bytes(self):
        tool=v8.load('nx_vertex_bridge_wrapper_owned',C/'tools/build_vertex_shader_contracts.py')
        package=tool.bootstrap()
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary).resolve()/'bridge.json'
            path.write_text(json.dumps(dict(schema='nxnative-base-v0-shader-key-bridge-v1',
                profile='botw-switch-base-v0',game_version='1.9.0',rows=[])))
            with self.assertRaisesRegex(ValueError,'bridge identity'):
                tool.checked_bridge(package,path)

    @unittest.skipIf(BASELINE, 'complete typed CPU publication is the new explicit route')
    def test_owned_attribute_load_store_uses_all_four_modes_and_complete_reaudit(self):
        dotnet=ROOT/'.tools/dotnet-10.0.402/dotnet'
        xlate=ROOT/'.tools/botw-ryujinx-xlate-21f/dmsh_xlate.dll'
        cross=ROOT/'.tools/spirv-cross-65d7393-build/spirv-cross'
        if not all(p.is_file() for p in (dotnet,xlate,cross)):
            self.skipTest('pinned CPU translator tools unavailable')
        wrapper=v8.load('nx_vertex_full_owned_wrapper',C/'tools/build_vertex_shader_contracts.py')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            with wrapper.staged_pipeline(root) as (package,publisher,validator,ports):
                source=root/'input';source.mkdir();caps=root/'captures';caps.mkdir()
                # Owned ALD attribute0 -> AST position -> EXIT. Opcodes are
                # constructed from the fixed complete Ryujinx decoder fields;
                # no original shader/code bytes are used or executed.
                control,raw=v8.owned();code=bytearray(raw)
                ald=(int('1110111111011',2)<<51)|(255<<8)|(7<<16)|(0x80<<20)|(255<<39)|(3<<47)
                ast_=(int('1110111111110',2)<<51)|(255<<8)|(7<<16)|(0x70<<20)|(255<<39)|(3<<47)
                struct.pack_into('<4Q',code,0x80,0x001ffc00fd4007ef,ald,ast_,0xe30000000007000f)
                code=bytes(code);fields=self.tool.parse_control(control);full=self.tool.capture_digest(fields,control,code)
                extract=v8.load('nx_vertex_full_owned_extract',self.stage/'dm/gfx/tools/botw_shader_extract.py')
                extract.write_capture(str(caps),full[:16],full,fields,control,code)
                row=dict(stage='vertex',container='owned ALD/AST CPU translation')
                for kind,data,suffix in [('control',control,'.control'),('code',code,'.nvn'),
                                          ('reflection',b'{}','.reflection.json'),('c1',code[fields['c1_offset']:],'.cbuf1')]:
                    row[kind+'_sha256']=hashlib.sha256(data).hexdigest();row[kind+'_file']=row[kind+'_sha256']+suffix
                    (source/row[kind+'_file']).write_bytes(data)
                row.update(c1_begin=fields['c1_offset'],c1_end=len(code),c1_bytes=len(code)-fields['c1_offset'])
                inputs=source/'inputs.json';inputs.write_text(json.dumps(dict(schema=1,records=[row])))
                request=root/'recipes.json';request.write_text(json.dumps(dict(schema='nxnative-base-v0-vertex-recipes-v1',
                    profile='botw-switch-base-v0',recipes={full[:16]:[dict(location=0,component_type='float')]})))
                output=root/'publication'
                with mock.patch.dict(os.environ,dict(DOTNET_ROOT=str(dotnet.parent),DOTNET_CLI_TELEMETRY_OPTOUT='1',
                       DOTNET_NOLOGO='1',PATH=str(dotnet.parent)+os.pathsep+os.environ.get('PATH',''))):
                    self.assertEqual(publisher.build(inputs,caps,request,output,xlate=xlate,spirv_cross=cross),0)
                files,index=validator.shader_files(output,ports)
                self.assertEqual(index['profile'],'botw-switch-base-v0')
                admission=json.loads(files['conditional-admission.json'])
                self.assertEqual(set(admission['captures']),{full[:16]})
                self.assertEqual(len(admission['captures'][full[:16]]['variants']),4)
                for variant in admission['captures'][full[:16]]['variants']:
                    contract=json.loads(files[index['directory']+'/'+variant+'.contract.json'])
                    reflection=json.loads(files[index['directory']+'/'+variant+'.json'])
                    self.assertEqual(contract['requirements']['attributes'],[dict(location=0,component_type='float')])
                    self.assertEqual(reflection['vertex_inputs'],[dict(location=0,msl_type='float4')])


if __name__ == '__main__':
    unittest.main()
