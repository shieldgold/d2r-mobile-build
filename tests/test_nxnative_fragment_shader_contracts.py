"""Explicit fragment output predicates reuse the complete original CPU chain."""
import inspect
import importlib.util
import json
import os
import py_compile
import struct
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import test_botw_whole_shader_v8 as v8

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'experiments/botw_upstream_host'
C=ROOT/'experiments/nxnative_base_v0'
PATCH=C/'patches/base-v0-fragment-shader-pipeline.patch'


class FragmentShaderContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='nx-owned-fragment-contract-');cls.addClassCleanup(cls.tmp.cleanup)
        cls.work=Path(cls.tmp.name).resolve()
        for relative in ('adapter/shader_conditional_build.py','adapter/shader_source_build.py',
                         'adapter/base_v0_graphics_formats.json','scripts/package_conditional.py'):
            path=cls.work/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((OLD/relative).read_bytes())
        if PATCH.is_file():
            result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(PATCH)],cwd=cls.work,capture_output=True,text=True)
            if result.returncode:raise AssertionError(result.stdout+result.stderr)
        cls.publisher=v8.load('nx_fragment_owned_builder',cls.work/'adapter/shader_conditional_build.py')
        source=v8.load('nx_fragment_owned_source',OLD/'adapter/shader_source_build.py')
        cls.stage=cls.work/'toolchain';source.stage_source_toolchain(cls.stage)
        cls.tool=v8.load('nx_fragment_owned_tool',cls.stage/'dm/gfx/tools/botw_shader_build.py')
        control,code=v8.owned(stage=1);fields=cls.tool.parse_control(control)
        full=cls.tool.capture_digest(fields,control,code)
        cls.capture=cls.tool.Capture('owned fragment EXIT',full[:16],full,fields,control,code)
        cls.result=dict(key=full[:16],stage='ps',notes=[],images=[],sbufs=[],textures=[],cbufs=[])

    def assess(self,outputs,text,result=None):
        args=(self.tool,self.capture,result or self.result,text,(0,0))
        if 'outputs' in inspect.signature(self.publisher.assess_mode).parameters:
            return self.publisher.assess_mode(*args,outputs=outputs)
        return self.publisher.assess_mode(*args)

    def test_complete_original_analysis_accepts_two_typed_outputs(self):
        text='struct main0_out { float4 a [[color(0)]]; uint4 b [[color(3)]]; };\nfragment main0_out main0() {}\n'
        request,bindings,inputs,entry=self.assess({0:'float',3:'uint'},text)
        self.assertEqual(request.outputs,{0:'float',3:'uint'})
        self.assertEqual((bindings,inputs,entry),([],[],('fragment','main0')))

    def test_complete_analysis_preserves_no_color_depth_only(self):
        text='struct main0_out { float depth [[depth(any)]]; };\nfragment main0_out main0() {}\n'
        self.assertEqual(self.assess({},text)[0].outputs,{})

    def test_strict_output_locations_types_and_no_duplicate_or_query_fallback(self):
        text='struct main0_out { int4 a [[color(1)]]; };\nfragment main0_out main0() {}\n'
        self.assertEqual(self.assess({1:'int'},text)[0].outputs,{1:'sint'})
        for request in ({1:'uint'},{8:'int'},{True:'int'},{1:'double'},{1:'int',2:'float'}):
            with self.subTest(request=request),self.assertRaises(ValueError):self.assess(request,text)
        for source in (text.replace('int4','short4'),text.replace('};','int4 b [[color(1)]]; };')):
            with self.subTest(source=source),self.assertRaises(ValueError):self.assess({1:'int'},source)
        result=dict(self.result,notes=['owned unknown query'])
        with self.assertRaises(ValueError):self.assess({1:'int'},text,result)

    def test_request_is_one_canonical_recipe_and_not_game_facts(self):
        path=self.work/'recipes.json';value=dict(schema='nxnative-base-v0-fragment-recipes-v1',
            profile='botw-switch-base-v0',recipes={self.capture.key:[dict(location=3,component_type='float')]})
        path.write_text(json.dumps(value));keys,_,recipes=self.publisher.requested_keys(path)
        self.assertEqual(keys,[self.capture.key]);self.assertEqual(recipes,{self.capture.key:{3:'float'}})
        for recipe in ([dict(location=3,component_type='float')]*2,[dict(location=8,component_type='float')]):
            path.write_text(json.dumps(dict(value,recipes={self.capture.key:recipe})))
            with self.assertRaises(ValueError):self.publisher.requested_keys(path)

    def test_locked_vertex_helper_source_ignores_a_timestamp_valid_owned_cache(self):
        wrapper=v8.load('nx_fragment_cache_owned_wrapper',C/'tools/build_fragment_shader_contracts.py')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();path=root/wrapper.VERTEX_SOURCE;path.parent.mkdir(parents=True)
            raw=(ROOT/wrapper.VERTEX_SOURCE).read_bytes();canary=b'raise RuntimeError("owned cache canary")\n'
            path.write_bytes(canary+b' '*(len(raw)-len(canary)));py_compile.compile(str(path),doraise=True)
            metadata=path.stat();path.write_bytes(raw);os.utime(path,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
            spec=importlib.util.spec_from_file_location('nx_fragment_cache_control',path)
            with self.assertRaisesRegex(RuntimeError,'owned cache canary'):
                spec.loader.exec_module(importlib.util.module_from_spec(spec))
            with mock.patch.object(wrapper,'ROOT',root):module=wrapper.bootstrap()
            self.assertEqual(Path(module.__file__),path);self.assertTrue(callable(module.staged_pipeline))

    def test_complete_owned_cpu_translation_and_independent_four_mode_reaudit(self):
        wrapper=v8.load('nx_fragment_full_owned_wrapper',C/'tools/build_fragment_shader_contracts.py')
        dotnet=ROOT/'.tools/dotnet-10.0.402/dotnet';xlate=ROOT/'.tools/botw-ryujinx-xlate-21f/dmsh_xlate.dll'
        cross=ROOT/'.tools/spirv-cross-65d7393-build/spirv-cross'
        if not all(p.is_file() for p in (dotnet,xlate,cross)):self.skipTest('fixed complete CPU tools unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            with wrapper.staged_pipeline(root) as (_,publisher,validator,ports):
                source=root/'input';source.mkdir();caps=root/'captures';caps.mkdir()
                control,raw=v8.owned(stage=1);code=bytearray(raw)
                # Owned fragment header: all four components of outputs 0/1.
                # Header[18] is the complete Ryujinx OmapTargets field.
                struct.pack_into('<I',code,0x30+18*4,0xff);code=bytes(code)
                fields=self.tool.parse_control(control);full=self.tool.capture_digest(fields,control,code)
                extract=v8.load('nx_fragment_full_owned_extract',self.stage/'dm/gfx/tools/botw_shader_extract.py')
                extract.write_capture(str(caps),full[:16],full,fields,control,code)
                import hashlib
                row=dict(stage='fragment',container='owned two-output fragment CPU translation')
                for kind,data,suffix in [('control',control,'.control'),('code',code,'.nvn'),
                        ('reflection',b'{}','.reflection.json'),('c1',code[fields['c1_offset']:],'.cbuf1')]:
                    row[kind+'_sha256']=hashlib.sha256(data).hexdigest();row[kind+'_file']=row[kind+'_sha256']+suffix
                    (source/row[kind+'_file']).write_bytes(data)
                row.update(c1_begin=fields['c1_offset'],c1_end=len(code),c1_bytes=len(code)-fields['c1_offset'])
                inputs=source/'inputs.json';inputs.write_text(json.dumps(dict(schema=1,records=[row])))
                outputs=[dict(location=i,component_type='float') for i in (0,1)]
                request=root/'recipes.json';request.write_text(json.dumps(dict(schema='nxnative-base-v0-fragment-recipes-v1',
                    profile='botw-switch-base-v0',recipes={full[:16]:outputs})))
                output=root/'publication'
                with mock.patch.dict(os.environ,dict(DOTNET_ROOT=str(dotnet.parent),DOTNET_CLI_TELEMETRY_OPTOUT='1',
                        DOTNET_NOLOGO='1',PATH=str(dotnet.parent)+os.pathsep+os.environ.get('PATH',''))):
                    self.assertEqual(publisher.build(inputs,caps,request,output,xlate=xlate,spirv_cross=cross),0)
                files,index=validator.shader_files(output,ports)
                admission=json.loads(files['conditional-admission.json']);self.assertEqual(set(admission['captures']),{full[:16]})
                self.assertEqual(len(admission['captures'][full[:16]]['variants']),4)
                for variant in admission['captures'][full[:16]]['variants']:
                    contract=json.loads(files[index['directory']+'/'+variant+'.contract.json'])
                    reflection=json.loads(files[index['directory']+'/'+variant+'.json'])
                    self.assertEqual(contract['requirements']['fragment_outputs'],outputs)
                    self.assertEqual(reflection['fragment_outputs'],[dict(location=i,msl_type='float4') for i in (0,1)])


if __name__=='__main__':unittest.main()
