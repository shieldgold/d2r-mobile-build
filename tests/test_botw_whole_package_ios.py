"""Owned tiny Mach-O/Guest/source packages. Nothing is signed, installed or run."""
from pathlib import Path
import copy
import hashlib
import json
import os
import plistlib
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import test_botw_guest_framework as guest_fixtures
import test_botw_whole_registry_audit as registry_fixtures
import test_botw_whole_shader_source_build as shader_fixtures

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'experiments/botw_upstream_host/scripts/package_ios.py'
H=lambda data:hashlib.sha256(data).hexdigest()
HOST_POLICY_PATH='adapter/base_v0_heap_runtime.json'
HOST_POLICY_PATCH='patches/original-logical-heap.patch'
HOST_POLICY={
    'schema':'botw-whole-base-v0-host-memory-runtime-policy-v1',
    'runtime_policy':'original-logical-v1','implementation':'reviewed-whole-port-os-proc-only-v1',
    'guest_parameter_carrier':'botw-switch-base-v0-bounded-heap-v1','guest_bytes_changed':False,
    'heap_bytes':3261071360,'root_bytes':3227516928,'startup_bytes':33554432,'unit_bytes':2097152,
    'live_guard_bytes':268435456,'live_query':'os_proc_available_memory','growth_bytes_compared_to_live':False,
    'image_accounting':'sum-module-size-round-up-2MiB','application_pool':'min-configured-retained-and-heap-plus-image',
    'resident_guaranteed':False,'original_native_task_vm_info_reused':False,'game_playable':False,
    'four_gib_layout':{'prefix_bytes':201326592,'alias_bytes':536870912,'stack_bytes':268435456,
                       'heap_offset_bytes':1006632960,'heap_capacity_bytes':3288334336},
    'five_gib_original_layout_preserved':True,'insufficient_capacity_bounded_fallback':False}


def owned_executable():
    original=registry_fixtures.macho_fixture();old_text=0x10000;old_page=0x1000
    image=bytearray(0x18000);image[:old_text+old_page]=original[:old_text+old_page]
    image[0x14000:0x15000]=original[old_text+old_page:]
    cursor=32;commands=[]
    for i in range(4):
        kind,size=struct.unpack_from('<II',original,cursor);command=bytearray(original[cursor:cursor+size]);cursor+=size
        if i==0:
            struct.pack_into('<I',command,4,size+80);struct.pack_into('<I',command,64,1)
            command+=struct.pack('<16s16sQQIIIIIIII',b'__text',b'__TEXT',0x100000400,4,0x400,2,0,0,0x80000400,0,0,0)
        elif i in (1,2):
            struct.pack_into('<Q',command,32,0x4000);struct.pack_into('<Q',command,48,0x4000)
            if i==2:struct.pack_into('<Q',command,24,0x100014000);struct.pack_into('<Q',command,40,0x14000)
        else:struct.pack_into('<I',command,8,0x14000)
        commands.append(command)
    commands += [struct.pack('<6I',0x32,24,2,0x120000,0x120000,0),struct.pack('<IIQQ',0x80000028,24,0x400,0)]
    raw=b''.join(commands);image[:32]=struct.pack('<8I',0xfeedfacf,0x100000c,0,2,6,len(raw),0x200000,0);image[32:32+len(raw)]=raw
    return image


class WholePackageIOS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package=shader_fixtures.load('owned_whole_ios_package',SCRIPT)
        shader_fixtures.WholeShaderSourceBuild.setUpClass()
        cls.addClassCleanup(shader_fixtures.WholeShaderSourceBuild.doClassCleanups)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='botw-owned-ios-package-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.repository=self.root/'owned-repository';self.component=self.repository/'experiments/botw_upstream_host'
        self.component.mkdir(parents=True);self.output=self.root/'result/BOTWNative.app'
        self.report,self.rewrite=guest_fixtures.fixture(self.root)
        self.guest=self.root/'guest';self.package.guest_framework.build_framework(self.report,self.rewrite,self.guest,module_names=list(self.package.NAMES))
        profile={'schema':1,'title_id':'owned fixture only','modules':{n:{'sha256':'ab'*32,'build_id':'12'*32} for n in self.package.NAMES}}
        (self.repository/'profiles').mkdir();self.profile=self.repository/'profiles/botw-switch-base-v0.json';self.json(self.profile,profile)
        self.pins()
        for name in ('frontend/whole_runtime_configuration.m','frontend/whole_main.m','frontend/whole_frontend.m','frontend/whole_front_state.c','upstream/dm/owned.c',
                     'adapter/base_v0_heap.c','adapter/base_v0_heap.h',HOST_POLICY_PATCH):
            path=self.component/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('/* owned receipt fixture, never compiled or executed */\n')
        self.host_policy=self.component/HOST_POLICY_PATH;self.json(self.host_policy,HOST_POLICY)
        self.policy_unit=True;self.policy_patch=True
        self.json(self.component/'UPSTREAM_UNITS.json',{'ios_translation_units':['dm/owned.c']})
        self.binary=self.root/'owned-MachO';self.binary.write_bytes(owned_executable())
        self.receipt=self.root/'receipt.json';self.commands=self.root/'commands.json'
        self.write_receipt()
        self.info=self.root/'Info.plist';self.info.write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.botw.native','CFBundleExecutable':'BOTWNative',
            'CFBundlePackageType':'APPL','CFBundleVersion':'1','MinimumOSVersion':'18.0','UIDeviceFamily':[1,2],
            'UIFileSharingEnabled':True,'LSSupportsOpeningDocumentsInPlace':True}))
        self.make_source()

    def json(self,path,value):path.write_text(json.dumps(value,sort_keys=True)+'\n')

    def pins(self):
        header=self.component/'adapter/whole_runtime_resource_manifest.h';header.parent.mkdir(exist_ok=True)
        names={'base-profile.json':self.profile,'guest-framework-report.json':self.guest/'guest-framework-report.json'}
        names.update({n+'.'+k+'.tsv':self.guest/'dm'/(n+'.'+k+'.tsv') for n in self.package.NAMES for k in ('fixups','gates')})
        header.write_text('/* Independent owned authority only; not real base-v0 game identities. */\n'+''.join(
            '{"'+name+'", '+str(path.stat().st_size)+'u, "'+H(path.read_bytes())+'"},\n' for name,path in names.items()))

    def write_receipt(self):
        sources={str(p.relative_to(self.component)):{'sha256':H(p.read_bytes())} for p in self.component.rglob('*') if p.is_file() and p.name!='SOURCE.lock.json'}
        metadata=self.package.registry_reader.read_registry(self.binary)
        self.lock={'files':sources,'external_files':{},'patched_source_sha256':{},
                   'patches':[HOST_POLICY_PATCH] if self.policy_patch else [],
                   'adapter_translation_units':['adapter/base_v0_heap.c'] if self.policy_unit else [],
                   'runtime_configuration_translation_units':['frontend/whole_runtime_configuration.m'],
                   'swforce_metadata_sha256':metadata['canonical_metadata_sha256'],'swforce_record_count':1}
        self.json(self.component/'SOURCE.lock.json',self.lock)
        self.units=['dm/owned.c','frontend/whole_runtime_configuration.m','frontend/whole_main.m','frontend/whole_frontend.m','frontend/whole_front_state.c']
        if self.policy_unit:self.units.append('adapter/base_v0_heap.c')
        common=['clang','-target','arm64-apple-ios18.0','-DDM_SHELL=1','-DBOTW_WHOLE_RUNTIME_CONFIGURED=1','-DBOTW_WHOLE_SHADER_PUBLICATION=1',
                '-Werror=unguarded-availability','-Werror=unguarded-availability-new']
        commands=[{'label':name.replace('/','__'),'argv':common+['-c','owned/'+name,'-o',name.replace('/','__')+'.o'],'exit':0} for name in self.units]
        commands.append({'label':'whole-host-link-only','argv':common+[name.replace('/','__')+'.o' for name in self.units]+['-o','owned-MachO'],'exit':0});self.json(self.commands,commands)
        dependencies=[{'component_path':name,'compiled_sha256':row['sha256'],'role':'original-upstream' if name.startswith('upstream/') else 'thin-platform-adapter'} for name,row in sources.items()]
        self.build={'schema':'botw-whole-host-link-only-v1','sdk':'iphoneos','target':'arm64-apple-ios18.0','runtime_configured':True,
                    'source_stable':True,'host_or_guest_or_GPU_executed':False,'compile_exit':0,'link_exit':0,
                    'link_executable_sha256':H(self.binary.read_bytes()),'source_lock_sha256':H((self.component/'SOURCE.lock.json').read_bytes()),
                    'source_sha256':{p:r['sha256'] for p,r in sources.items()},'external_source_sha256':{},'patched_source_sha256':{},
                    'compiled_translation_units':len(self.units),'expected_total_translation_units':len(self.units),'expected_upstream_translation_units':1,'compiler_dependency_manifest':dependencies,
                    'actual_compiler_dependency_count':len(dependencies),'commands_sha256':H(self.commands.read_bytes()),'fixture_only':True}
        self.json(self.receipt,self.build)

    def make_source(self):
        f=shader_fixtures.WholeShaderSourceBuild();f.setUp();self.addCleanup(f.doCleanups);self.source=self.root/'source'
        self.source.mkdir();admission=f.publisher.prepare_release(f.tool,f.f.source,f.f.caps,f.f.facts,f.f.derive_path,f.f.evidence_path,self.source/'admitted')
        self.package.source_builder.stage_source_toolchain(self.source/'toolchain')
        tool=self.package.source_builder.load(self.source/'toolchain/dm/gfx/tools/botw_shader_build.py','owned_package_image_tool')
        identity={'xlate':{'built_from_repo_source':True,'embedded_source_sha16':tool.repo_xlate_source_sha(),
            'repo_source_sha16':tool.repo_xlate_source_sha(),'dll_sha16':{name:'a'*16 for name in ('dmsh_xlate.dll','Ryujinx.Graphics.Shader.dll','Ryujinx.Common.dll','Spv.Generator.dll')}},
            'spirv_cross':{'revision':'owned fixture only','sha16':'b'*16},'metal':{'backend':'device-source-required','xcrun_metal':None,'xcrun_metallib':None}}
        tools=tool.Tools();tools.identity=identity;adapter_sha=H(self.package.COMPONENT.joinpath('adapter/shader_source_build.py').read_bytes())
        admission_sha=H((self.source/'admitted/release-admission.json').read_bytes());options={'xlate_debug':False,'xlate_reduced':False}
        publisher=f.publisher.SourcePublication(tool,admission,self.source/'admitted/captures',admission_sha)
        publisher.chain=tools.chain(dict(options,source_adapter_sha256=adapter_sha,release_admission_sha256=admission_sha))
        directory='botw-s-'+publisher.chain;out=self.source/'build'/directory;out.mkdir(parents=True);work=self.root/'msl';work.mkdir()
        key=f.f.key;facts=tool.load_facts(str(f.f.fact_path),f.f.cap);msl='vertex void main0() {}\n'
        (work/(key+'_b.metal')).write_text(msl)
        _,_,unknown,conflicts,_,_=tool.analyze(f.f.cap,facts,{'textures':[],'images':[],'sbufs':[],'cbufs':[],'notes':[]},msl)
        self.assertEqual((unknown,conflicts),([],[]))
        result={'key':key,'capture':f.f.cap.summary(),'facts':{'sha256':facts.sha256},'errors':[],'unknowns':[],'conflicts':[]}
        reflection=tool.reflection(f.f.cap,publisher.chain,('vertex','main0'),[],[],facts,'B');publisher.publish(out,work,key,reflection,result,None)
        manifest=publisher.manifest('build',publisher.chain,options,identity,'','',str(out),[result]);tool.write_json(str(out/'manifest-build.json'),manifest)
        publication={'schema':'botw-shader-source-publication-v1','profile':'botw-switch-base-v0','package':'build/'+directory,
            'manifest_sha256':H((out/'manifest-build.json').read_bytes()),'release_admission_sha256':admission_sha,'source_adapter_sha256':adapter_sha,
            'captures':1,'input_manifest_sha256':admission['input_manifest_sha256'],'device_compilation_tested':False,'guest_or_gpu_executed':False}
        self.json(self.source/'source-publication.json',publication)

    def prepare(self):
        # Only the trusted repository/authority is substituted. Real source,
        # receipt, Mach-O, original Guest regeneration and shader gates run.
        with mock.patch.object(self.package,'ROOT',self.repository):
            return self.package.prepare(self.binary,self.receipt,self.commands,self.guest,self.report,self.rewrite,self.source,self.info,self.output,build_version='42')

    def test_owned_end_to_end_unsigned_precise_runtime_bundle_and_bytes(self):
        originals={str(p):H(p.read_bytes()) for d in (self.source,self.guest,self.rewrite) for p in d.rglob('*') if p.is_file()}
        result=self.prepare();self.assertTrue(result['unsigned']);self.assertFalse(result['phone_ready']);self.assertFalse(result['game_playable'])
        self.assertIn('host_memory_runtime_policy',result)
        policy=result['host_memory_runtime_policy']
        self.assertEqual(policy['contract'],HOST_POLICY)
        self.assertEqual((policy['source'],policy['source_sha256']),(HOST_POLICY_PATH,H(self.host_policy.read_bytes())))
        self.assertEqual(policy['provider_sha256'],H((self.component/'adapter/base_v0_heap.c').read_bytes()))
        self.assertEqual(policy['patch_sha256'],H((self.component/HOST_POLICY_PATCH).read_bytes()))
        self.assertEqual(json.loads((self.output/'package-manifest.json').read_bytes())['host_memory_runtime_policy'],policy)
        self.assertEqual((self.output/'BOTWNative').read_bytes(),self.binary.read_bytes())
        info=plistlib.loads((self.output/'Info.plist').read_bytes());self.assertEqual((info['CFBundleIdentifier'],info['CFBundleVersion']),('local.botw.native','42'))
        self.assertEqual(info['UIDeviceFamily'],[1,2]);self.assertTrue(info['UIFileSharingEnabled'])
        for name in ('Documents','Saves','UID','botw-romfs.bin','toolchain','captures'):self.assertFalse(list(self.output.rglob(name)))
        self.assertEqual((self.output/'botw/shaders/source-publication.json').read_bytes(),(self.source/'source-publication.json').read_bytes())
        self.assertEqual((self.output/'botw/shaders/release-admission.json').read_bytes(),(self.source/'admitted/release-admission.json').read_bytes())
        self.assertEqual(set(self.package.ios._tree(self.output)),set(result['files'])|{'package-manifest.json'})
        for name,row in result['files'].items():self.assertEqual(H((self.output/name).read_bytes()),row['sha256'])
        self.assertEqual({p:H(Path(p).read_bytes()) for p in originals},originals)
        self.assertFalse((self.output/'_CodeSignature').exists())
        if os.environ.get('BOTW_WHOLE_PACKAGE_SMOKE_RECEIPT'):
            self.json(Path(os.environ['BOTW_WHOLE_PACKAGE_SMOKE_RECEIPT']),dict(result,fixture_only=True,owned_bundle_files=sorted(result['files'])))

    def test_locked_runtime_policy_rejects_missing_or_contradictory_contract_before_publication(self):
        changes=[('heap_bytes',1),('live_guard_bytes',0),('growth_bytes_compared_to_live',True),
                 ('guest_bytes_changed',True),('resident_guaranteed',True),('original_native_task_vm_info_reused',True),
                 ('game_playable',0),('runtime_policy','bounded-v1'),('unexpected',True),
                 ('four_gib_layout',dict(HOST_POLICY['four_gib_layout'],heap_capacity_bytes=0))]
        for field,value in changes:
            with self.subTest(field=field):
                self.json(self.host_policy,dict(HOST_POLICY,**{field:value}));self.write_receipt()
                try:
                    with self.assertRaises(ValueError):self.prepare()
                    self.assertFalse(self.output.exists())
                finally:shutil.rmtree(self.output,ignore_errors=True)
        self.host_policy.unlink();self.write_receipt()
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.output.exists())

    def test_runtime_contract_requires_the_compiled_provider_and_locked_after_patch(self):
        for field in ('policy_unit','policy_patch'):
            with self.subTest(field=field):
                setattr(self,field,False);self.write_receipt()
                try:
                    with self.assertRaises(ValueError):self.prepare()
                    self.assertFalse(self.output.exists())
                finally:
                    shutil.rmtree(self.output,ignore_errors=True);setattr(self,field,True)

    def test_legacy_shell_plist_key_remains_inert_template_metadata(self):
        info=plistlib.loads(self.info.read_bytes());info['BOTWHostMemoryPolicy']='bounded-v1'
        self.info.write_bytes(plistlib.dumps(info))
        result=self.prepare()
        self.assertEqual(plistlib.loads((self.output/'Info.plist').read_bytes())['BOTWHostMemoryPolicy'],'bounded-v1')
        self.assertEqual(result['host_memory_runtime_policy']['contract']['runtime_policy'],'original-logical-v1')

    def test_generic_or_failed_wrong_target_and_hash_receipts_never_publish(self):
        for field,value in (('runtime_configured',False),('compile_exit',1),('compile_exit',False),('link_exit',1),('source_stable',False),('sdk','macosx'),('target','arm64-apple-ios18.0-simulator'),('link_executable_sha256','0'*64)):
            self.json(self.receipt,dict(self.build,**{field:value}))
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.prepare()
            self.assertFalse(self.output.exists())

    def test_real_commands_require_both_runtime_macros_and_actual_configuration_tu(self):
        commands=json.loads(self.commands.read_text());commands[0]['argv'].remove('-DBOTW_WHOLE_SHADER_PUBLICATION=1');self.json(self.commands,commands)
        self.build['commands_sha256']=H(self.commands.read_bytes());self.json(self.receipt,self.build)
        with self.assertRaisesRegex(ValueError,'macros'):self.prepare()
        self.assertFalse(self.output.exists())

    def test_mislabeled_source_missing_real_dependency_and_partial_link_are_rejected(self):
        saved=copy.deepcopy(self.build)
        self.build['compiler_dependency_manifest']=[r for r in self.build['compiler_dependency_manifest'] if r['component_path']!='frontend/whole_runtime_configuration.m']
        self.build['actual_compiler_dependency_count']-=1;self.json(self.receipt,self.build)
        with self.assertRaisesRegex(ValueError,'actual compiler dependency'):self.prepare()
        self.build=saved;self.json(self.receipt,self.build)
        commands=json.loads(self.commands.read_text());original=copy.deepcopy(commands)
        commands[0]['argv'][commands[0]['argv'].index('-c')+1]='owned/another.c';self.json(self.commands,commands)
        self.build['commands_sha256']=H(self.commands.read_bytes());self.json(self.receipt,self.build)
        with self.assertRaisesRegex(ValueError,'actual source'):self.prepare()
        original[-1]['argv'].remove('dm__owned.c.o');self.json(self.commands,original)
        self.build['commands_sha256']=H(self.commands.read_bytes());self.json(self.receipt,self.build)
        with self.assertRaisesRegex(ValueError,'object closure'):self.prepare()
        self.assertFalse(self.output.exists())

    def test_static_whole_registry_is_rechecked_after_receipt_sha_matches(self):
        raw=bytearray(self.binary.read_bytes());raw[0x800]=ord('X');self.binary.write_bytes(raw)
        self.build['link_executable_sha256']=H(raw);self.json(self.receipt,self.build)
        with self.assertRaisesRegex(ValueError,'registry differs'):self.prepare()
        self.assertFalse(self.output.exists())

    def test_shader_loader_and_bundle_use_verified_private_bytes_after_input_changes(self):
        original_load=self.package.source_builder.load;observed=[]
        external=self.source/'toolchain/dm/gfx/tools/botw_shader_build.py'
        publication=json.loads((self.source/'source-publication.json').read_text())
        metal=next((self.source/publication['package']).glob('*.metal'));original_source=metal.read_bytes()
        def checked_load(path,name):
            if name=='whole_package_complete_shader_tool':
                observed.append(Path(path));self.assertFalse(Path(path).is_relative_to(self.source))
                external.write_text('raise RuntimeError("unchecked original must never execute")\n')
            return original_load(path,name)
        real_check=self.package.source_builder.SourcePublication.check_batch
        def checked_batch(publisher,root,manifest):
            real_check(publisher,root,manifest)
            metal.write_bytes(b'changed after snapshot; must never enter bundle')
        with mock.patch.object(self.package.source_builder,'load',side_effect=checked_load), \
                mock.patch.object(self.package.source_builder.SourcePublication,'check_batch',new=checked_batch):result=self.prepare()
        self.assertEqual(len(observed),1)
        copied=self.output/'botw/shaders'/result['shader']['directory']/metal.name
        self.assertEqual(copied.read_bytes(),original_source)

    def test_actual_macho_subtype_entry_and_platform_are_not_receipt_assertions(self):
        original=owned_executable()
        for offset,value in ((8,2),(12,6),(16,0)):
            raw=bytearray(original);struct.pack_into('<I',raw,offset,value)
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.package.executable_layout(raw)
        cursor=32;raw=bytearray(original)
        while struct.unpack_from('<I',raw,cursor)[0]!=0x80000028:cursor+=struct.unpack_from('<I',raw,cursor+4)[0]
        struct.pack_into('<Q',raw,cursor+8,0x10000)
        with self.assertRaisesRegex(ValueError,'LC_MAIN'):self.package.executable_layout(raw)
        # ARM64 entry admission covers the complete fixed-width instruction.
        for span in (1,2,3):
            raw=bytearray(original);struct.pack_into('<Q',raw,32+72+40,span)
            with self.subTest(entry_section_bytes=span),self.assertRaisesRegex(ValueError,'LC_MAIN'):
                self.package.executable_layout(raw)
        self.package.executable_layout(original)

    def test_guest_self_consistent_replacement_still_requires_original_regeneration(self):
        guest=self.guest/'Guest.framework/Guest';raw=bytearray(guest.read_bytes());raw[0x4000]^=1;guest.write_bytes(raw)
        metadata=json.loads((self.guest/'guest-framework-report.json').read_text());metadata['framework']['sha256']=H(raw);self.json(self.guest/'guest-framework-report.json',metadata)
        self.pins()  # Even a separately changed owned authority cannot replace source evidence.
        with tempfile.TemporaryDirectory(dir=self.root) as t,self.assertRaisesRegex(ValueError,'regenerated'):
            self.package.verify_guest(self.guest,self.report,self.rewrite,self.profile,self.repository,Path(t))

    def test_missing_admitted_facts_or_unknown_extra_shader_artifact_never_publish(self):
        fact=next((self.source/'admitted/facts').glob('*.facts.json'));raw=fact.read_bytes();fact.unlink()
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.output.exists());fact.write_bytes(raw)
        publication=json.loads((self.source/'source-publication.json').read_text());(self.source/publication['package']/'unexpected.metallib').write_bytes(b'not compiled')
        with self.assertRaisesRegex(ValueError,'unlisted'):self.prepare()
        self.assertFalse(self.output.exists())

    def test_symlink_traversal_and_existing_output_are_preserved(self):
        alias=self.root/'alias';alias.symlink_to(self.binary);original=self.binary;self.binary=alias
        with self.assertRaisesRegex(ValueError,'symlink'):self.prepare()
        self.binary=original
        with self.assertRaisesRegex(ValueError,'traversal'):self.package.checked_path(self.root/'unused/../owned-MachO')
        self.output.mkdir(parents=True);marker=self.output/'owned';marker.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.prepare()
        self.assertEqual(marker.read_bytes(),b'keep')


if __name__=='__main__':unittest.main()
