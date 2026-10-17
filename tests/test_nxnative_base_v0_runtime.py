"""Actual profile macro compilation and strict native host planning; no Host execution."""
from pathlib import Path
import json
import contextlib
import io
import os
import py_compile
import shutil
import subprocess
import tempfile
import types
import unittest
import sys

ROOT=Path(__file__).resolve().parents[1]
C=ROOT/'experiments/nxnative_base_v0'
TOOL=C/'tools/build_base_v0_runtime.py'
PROFILE=C/'profile/base_v0_runtime.tsv'


def load_source(path):
    module=types.ModuleType('owned_'+path.stem);module.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
    return module


class BaseV0Runtime(unittest.TestCase):
    def tool(self):
        self.assertTrue(TOOL.is_file(),'dedicated true-UIKit runtime builder is absent')
        return load_source(TOOL)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='nxnative-owned-runtime-')
        self.addCleanup(self.temp.cleanup);self.work=Path(self.temp.name).resolve()

    def test_full_original_header_generator_emits_compilable_exact_base0_macros(self):
        tool=self.tool();identity=json.loads((C/'profile/base_v0.json').read_bytes())
        raw=(C/'upstream/dm/games/game.py').read_bytes()
        header,record=tool.render_header(PROFILE.read_bytes(),identity,raw,self.work)
        self.assertEqual(record['game_version'],'1.0.0')
        self.assertEqual(record['module_order'],list(tool.MODULES))
        self.assertEqual(record['generator_entry'],'header(load(dedicated_base_v0_profile))')
        self.assertNotIn('1.9.0',header)
        (self.work/'game_profile.h').write_text(header)
        source=self.work/'profile.c';source.write_text('''#include "game_profile.h"
        _Static_assert(DM_GAME_NMOD==6,"six original modules");
        _Static_assert(DM_GAME_NVN_API_MAJOR==52&&DM_GAME_NVN_API_MINOR==32,"52.32");
        _Static_assert(DM_GAME_MEM_RESERVE==0x140000000ull,"real5GiB");
        _Static_assert(DM_GAME_MEM_ALIAS==0x20000000ull&&DM_GAME_MEM_STACK==0x10000000ull,"base0 layout");
        _Static_assert(DM_GAME_APP_POOL==0xc5c00000ull,"H plus original image footprint");
        _Static_assert(DM_GAME_NVN_TEX_BASE==32&&DM_GAME_NVN_TEX_SLOTS==32,"proved texture slots");
        _Static_assert(DM_GAME_NVN_IMG_BASE==288&&DM_GAME_NVN_IMG_SLOTS==8,"proved image slots");
        _Static_assert(DM_GAME_NVN_SAMP_BASE==-1,"unproved sampler unsupported");
        _Static_assert(DM_GAME_NVN_SSBO_BASE(0)==-1&&DM_GAME_NVN_SSBO_BASE(5)==-1,"unproved SSBO unsupported");
        const char *modules[]=DM_GAME_MODULES;
        _Static_assert(sizeof modules/sizeof *modules==6,"six names");
        ''')
        compiler=shutil.which('clang');self.assertIsNotNone(compiler)
        result=subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-c',str(source),'-o',str(self.work/'profile.o')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_profile_rejects_stale_identity_borrowed_values_and_duplicate_keys(self):
        tool=self.tool();identity=json.loads((C/'profile/base_v0.json').read_bytes());raw=PROFILE.read_bytes()
        for changed in (raw.replace(b'1.0.0',b'1.9.0'),raw.replace(b'nvn_samp_base\t-1',b'nvn_samp_base\t0'),
                        raw.replace(b'main subsdk0 subsdk1 subsdk2 subsdk3 sdk',b'main subsdk0 sdk'),
                        raw+b'\nnvn_samp_base\t-1\n',raw.replace(b'vs=-1',b'vs=0x110')):
            with self.subTest(changed=changed[-70:]),self.assertRaises(ValueError):tool.validate_profile(changed,identity)

    def test_cli_is_host_only_and_rejects_unreviewed_flags_or_resource_inputs(self):
        tool=self.tool();args=tool.parse_args(['--host-only','--sdk','iphoneos','--output',str(self.work/'out'),'--stage-only'])
        self.assertTrue(args.host_only);self.assertTrue(args.stage_only)
        for extra in (['--target','arm64-apple-ios18.0'],['--extra-cflags','-DFAKE=1'],['--guest','private-game'],['--jobs','0'],['--jobs','9'],['--stage-only','--plan-only']):
            with self.subTest(extra=extra),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):tool.parse_args(['--host-only','--output',str(self.work/'out'),*extra])
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):tool.parse_args(['--output',str(self.work/'out')])

    def test_real_inventory_plan_uses_uikit_main_and_no_link_only_entry(self):
        tool=self.tool();base=load_source(C/'tools/build_link_only.py');adapted=load_source(C/'tools/build_adapted_link_only.py')
        units=json.loads((C/'compile_sources.json').read_bytes())['translation_units']
        commands=tool.compile_commands(self.work/'inputs',self.work,units,{'clang':'/owned/clang','sdk_path':'/owned/sdk'},base,adapted)
        self.assertEqual(len(commands),235)
        self.assertEqual(len({row['source'] for row in commands}),235)
        self.assertFalse(any('link_only_main.c' in row['source'] for row in commands))
        ios=next(row for row in commands if row['source'].endswith('/dm/ios/Sources/main.m'))
        host=next(row for row in commands if row['source'].endswith('/dm/host/main.c'))
        self.assertFalse(any(v.startswith('-Dmain=') for v in ios['argv']))
        self.assertIn('-Dmain=dm_main',host['argv'])
        for row in commands:
            self.assertIn('-DNXNATIVE_NORMAL_POLICY=1',row['argv'])
            self.assertIn('-DNXNATIVE_BASE_V0_MEMORY=1',row['argv'])
            self.assertIn('-DNXNATIVE_BASE_V0_NORMAL_APIS=1',row['argv'])
            self.assertIn('-DNXNATIVE_BASE_V0_LOADER_GUARD=1',row['argv'])
            self.assertIn('-DNXNATIVE_BASE_V0_AUDREN=1',row['argv'])
            self.assertIn('-DNXNATIVE_BASE_V0_DEBUG_PAD=1',row['argv'])
            self.assertIn('-march=armv8.2-a',row['argv'])
            self.assertNotIn('-mcpu=apple-a18',row['argv'])
        self.assertFalse(any('nxnative_upstream_ios_main' in arg for row in commands for arg in row['argv']))
        guard=next(row for row in commands if row['source'].endswith('/adapter/base_v0_loader_guard.m'))
        self.assertIn('-fobjc-arc',guard['argv']);self.assertIn('-fblocks',guard['argv'])
        argv,_=base.link_command(self.work,[row['object'] for row in commands],{'clang':'/owned/clang','sdk_path':'/owned/sdk'})
        self.assertIn('-Wl,-undefined,error',argv);self.assertIn('-Wl,-e,_main',argv)
        self.assertFalse(any('dynamic_lookup' in arg for arg in argv))

    def test_complete_audio_inventory_uses_prepared_service_and_cxx_link(self):
        tool=self.tool();base=load_source(C/'tools/build_link_only.py');adapted=load_source(C/'tools/build_adapted_link_only.py')
        units=json.loads((C/'compile_sources.json').read_bytes())['translation_units']
        sdk={'clang':'/owned/clang','sdk_path':'/owned/sdk'}
        commands=tool.compile_commands(self.work/'inputs',self.work,units,sdk,base,adapted)
        audio=[r for r in commands if r['role'].startswith('audren:')]
        self.assertEqual(len(audio),95)
        cpp=[r for r in audio if r['source'].endswith('.cpp')]
        self.assertEqual(len(cpp),79)
        for r in cpp:
            self.assertIn('-std=c++23',r['argv']);self.assertIn('-fexceptions',r['argv'])
            self.assertIn('-DFMT_HEADER_ONLY=1',r['argv']);self.assertNotIn('-std=gnu11',r['argv'])
        services=[r for r in audio if r['source'].endswith('/native/botw_host/services.c')]
        self.assertEqual(len(services),1);self.assertIn('/audio/services/',services[0]['source'])
        self.assertFalse(any(r['source'].endswith('/kernel.c') or 'mock' in r['source'] for r in audio))
        argv,_=tool.link_command(self.work,[r['object'] for r in commands],sdk,base)
        self.assertIn('-lc++',argv);self.assertIn('Security',argv)
        self.assertIn('-Wl,-undefined,error',argv)
        self.assertEqual(sum(n for role,n in tool.verify_compile_roles(commands).items() if role.startswith('audren:')),95)
        with self.assertRaises(ValueError):tool.verify_compile_roles(commands[:-1])
        cpp[0]['argv'].remove('-fexceptions')
        with self.assertRaises(ValueError):tool.verify_compile_roles(commands)

    def test_audio_dependency_normalization_binds_duplicates_and_rejects_aliases(self):
        tool=self.tool();root=self.work/'repo';(root/'native').mkdir(parents=True);(root/'platform').mkdir()
        source=root/'platform/memory.h';source.write_text('/* actual owned header */\n')
        row=tool.record(source.read_bytes())
        normalized=tool.normalize_audio_dependencies(root,[{'path':'native/../platform/memory.h',**row},{'path':'platform/memory.h',**row}])
        self.assertEqual(normalized,{'platform/memory.h':row})
        with self.assertRaises(ValueError):tool.normalize_audio_dependencies(root,[{'path':'../escape',**row}])
        (root/'linked').symlink_to(root/'native',target_is_directory=True)
        with self.assertRaises(ValueError):tool.normalize_audio_dependencies(root,[{'path':'linked/../platform/memory.h',**row}])
        with self.assertRaises(ValueError):tool.normalize_audio_dependencies(root,[{'path':'platform/memory.h',**row},{'path':'platform/memory.h',**{**row,'sha256':'0'*64}}])

    def cxx_layout(self):
        toolchain=self.work/'selected-toolchain/usr';(toolchain/'bin').mkdir(parents=True)
        clang=toolchain/'bin/clang';clang.write_text('owned path identity; never a compiler stand-in\n')
        sdk=self.work/'SDKs/iPhoneOS-owned.sdk';sdk.mkdir(parents=True)
        return {'clang':str(clang),'sdk_path':str(sdk)},toolchain,sdk

    def cxx_root(self,root):
        root.mkdir(parents=True);(root/'__config').write_text('/* owned config */\n');(root/'vector').write_text('/* owned vector */\n')
        return root

    def cxx_search(self,*roots):
        return '#include "..." search starts here:\n#include <...> search starts here:\n'+''.join(' '+str(p)+'\n' for p in roots)+'End of search list.\n'

    def test_cxx_driver_search_admits_both_real_toolchain_and_new_sdk_layouts(self):
        tool=self.tool();sdk,toolchain,sdkroot=self.cxx_layout()
        old=self.cxx_root(toolchain/'include/c++/v1')
        chosen,proof=tool.cxx_headers(sdk,self.cxx_search(old,sdkroot/'usr/include'))
        self.assertEqual(chosen,old);self.assertEqual(proof['reported_root'],str(old))
        shutil.rmtree(old)
        new=self.cxx_root(sdkroot/'usr/include/c++/v1')
        chosen,proof=tool.cxx_headers(sdk,self.cxx_search(new,sdkroot/'usr/include'))
        self.assertEqual(chosen,new);self.assertEqual(proof['resolved_root'],str(new))
        self.assertEqual(set(proof['marker_files']),{'__config','vector'})

    def test_cxx_search_rejects_external_ambiguous_missing_and_nonregular_roots(self):
        tool=self.tool();sdk,toolchain,sdkroot=self.cxx_layout();inside=self.cxx_root(sdkroot/'usr/include/c++/v1')
        outside=self.cxx_root(self.work/'external/include/c++/v1');other=self.cxx_root(toolchain/'include/c++/v1')
        for roots in ((outside,),(inside,other),(sdkroot/'missing/c++/v1',),()):
            with self.subTest(roots=roots),self.assertRaises(ValueError):tool.cxx_headers(sdk,self.cxx_search(*roots))
        (inside/'vector').unlink();os.mkfifo(inside/'vector')
        with self.assertRaises(ValueError):tool.cxx_headers(sdk,self.cxx_search(inside))

    def test_cxx_sdk_alias_is_allowed_but_extra_or_internal_links_refuse(self):
        tool=self.tool();sdk,_,sdkroot=self.cxx_layout();inside=self.cxx_root(sdkroot/'usr/include/c++/v1')
        alias=sdkroot.parent/'iPhoneOS.sdk';alias.symlink_to(sdkroot,target_is_directory=True)
        reported=alias/'usr/include/c++/v1';chosen,proof=tool.cxx_headers(sdk,self.cxx_search(reported))
        self.assertEqual(chosen,inside);self.assertEqual(proof['reported_root'],str(reported))
        extra=self.work/'extra';extra.symlink_to(inside,target_is_directory=True)
        with self.assertRaises(ValueError):tool.cxx_headers(sdk,self.cxx_search(extra))
        (inside/'nested').mkdir();(inside/'nested/header').symlink_to(inside/'vector')
        with self.assertRaises(ValueError):tool.cxx_header_snapshot(inside)

    def test_real_local_mac_driver_query_records_search_and_full_header_closure(self):
        # Only an empty native-Mac preprocessing query. This never substitutes
        # for the fixed production iPhoneOS target or an actual SDK build.
        tool=self.tool();base=load_source(C/'tools/build_link_only.py')
        found=subprocess.run(['/usr/bin/xcrun','--sdk','macosx','--find','clang'],capture_output=True,text=True)
        self.assertEqual(found.returncode,0,found.stderr)
        selected=subprocess.run(['/usr/bin/xcrun','--sdk','macosx','--show-sdk-path'],capture_output=True,text=True)
        self.assertEqual(selected.returncode,0,selected.stderr)
        sdk={'clang':str(Path(found.stdout.strip()).resolve()),'sdk_path':str(Path(selected.stdout.strip()).resolve())}
        base.TARGET='arm64-apple-macos14.0';receipt={'steps':[]}
        root=tool.query_cxx_headers(self.work,sdk,base,receipt)
        proof=receipt['libcxx_header_selection'];self.assertEqual(proof['resolved_root'],str(root))
        self.assertEqual(proof['query']['exit_code'],0)
        self.assertIn(base.TARGET,proof['query']['argv']);self.assertIn('--no-default-config',proof['query']['argv'])
        self.assertIn('End of search list.',Path(proof['query']['stderr']['path']).read_text())
        first=tool.cxx_header_snapshot(root);self.assertIn(str(root/'vector'),first)
        self.assertEqual(first,tool.cxx_header_snapshot(root))

    def test_final_symbols_require_real_entry_memory_provider_and_base0_api(self):
        tool=self.tool();adapted=load_source(C/'tools/build_adapted_link_only.py')
        required=tool.strong_symbols(adapted)
        self.assertIn('_main',required);self.assertIn('_dm_main',required)
        self.assertNotIn('_nxnative_upstream_ios_main',required)
        self.assertIn('_nxnative_base_v0_memory_reserve',required)
        self.assertIn('_botw_base_v0_heap_plan_original_logical',required)
        self.assertIn('_nxnative_base_v0_layer_scaling_mode',required)
        self.assertIn('_whole_runtime_fragment_shader_publication',required)
        self.assertIn('_botw_base_v0_debug_pad_tick',required)
        for suffix in ('preload','begin','layout','image','gate_count','registry','reference','complete','entry'):
            self.assertIn('_nxnative_base_v0_loader_'+suffix,required)
        for symbol in ('_nxnative_base_v0_audren_prepare','_botw_audren_update','_botw_pcm_write','_botw_whole_kernel_producer_signal'):
            self.assertIn(symbol,required)
        text='\n'.join('0000000000001000 T '+symbol for symbol in required)
        self.assertEqual(tool.verify_symbols(text,required),required)
        for symbol in ('_main','_dm_main','_nxnative_base_v0_memory_verify','_nxnative_base_v0_loader_preload','_nxnative_base_v0_loader_entry','_nxnative_base_v0_audren_prepare','_botw_audren_update','_botw_pcm_write'):
            changed='\n'.join(line for line in text.splitlines() if line.split()[-1]!=symbol)
            with self.subTest(symbol=symbol),self.assertRaises(ValueError):tool.verify_symbols(changed,required)

    def test_actual_fixed_patch_chain_copies_complete_memory_provider_and_detects_mutation(self):
        tool=self.tool();base=load_source(C/'tools/build_link_only.py');adapted=load_source(C/'tools/build_adapted_link_only.py')
        # Owned composition fixture: the production load_helpers gate is tested
        # separately. Newly frozen copied adapters may await root registration;
        # only absent rows enter this owned fixture, never the production lock.
        lock=base.read_json(C/'SOURCE.lock.json')
        for name in tool.REQUIRED:
            lock['adapter_files'].setdefault(name,base.file_record(C/name))
        for name in tool.AUDIO_EXTRA_FILES:
            lock['external_code_files'].setdefault(name,base.file_record(ROOT/name))
        receipt=tool.new_receipt();private,expected=tool.copy_inputs(C,self.work,lock,base,adapted,receipt)
        self.assertFalse((private/'adapter/link_only_main.c').exists())
        self.assertEqual(len(receipt['patches']),14)
        self.assertEqual(receipt['patches'][-4]['changed_members'],['dm/host/hle_audren.c','dm/host/main.c','dm/ios/Sources/front_ios.m'])
        self.assertEqual(receipt['patches'][-3]['changed_members'],['dm/host/hle_movie.c'])
        self.assertEqual(receipt['patches'][-2]['changed_members'],['dm/host/dm.h','dm/host/hle.c','dm/host/loader.c'])
        self.assertEqual(receipt['patches'][-1]['changed_members'],['dm/host/hle_os.c'])
        self.assertEqual((private/'adapter/base_v0_stack_allocator.h').read_bytes(),(C/'adapter/base_v0_stack_allocator.h').read_bytes())
        self.assertEqual(len(receipt['audren_source_inventory']['translation_units']),95)
        self.assertEqual(len(receipt['audio_staging_patches']),6)
        self.assertEqual(len(receipt['typed_vertex_patch']['changed_members']),7)
        self.assertEqual(len(receipt['fragment_shader_patch']['changed_members']),6)
        self.assertEqual(receipt['normal_graphics_counter_patch']['changed_members'],['dm/gfx/nvn/nvn_mtl.m'])
        self.assertEqual(receipt['debug_pad_patch']['changed_members'],['dm/host/hle_hid.c','dm/host/hle_sdk16.c'])
        for name in ('typed-vertex-private-append','fragment-shader-private-append','normal-graphics-counter-patch','base-v0-debug-pad-patch'):
            stdout=(self.work/'logs'/(name+'.stdout')).read_text()
            self.assertNotIn('offset',stdout);self.assertNotIn('fuzz',stdout)
        self.assertEqual((private/'adapter/base_v0_loader_guard.m').read_bytes(),(C/'adapter/base_v0_loader_guard.m').read_bytes())
        self.assertEqual((private/'adapter/base_v0_loader_guard.h').read_bytes(),(C/'adapter/base_v0_loader_guard.h').read_bytes())
        self.assertEqual(len(list((private/'memory_provider').iterdir())),6)
        header,record=tool.render_header(PROFILE.read_bytes(),json.loads((C/'profile/base_v0.json').read_bytes()),
            (private/'upstream/dm/games/game.py').read_bytes(),self.work)
        generated=self.work/'generated';generated.mkdir();(generated/'game_profile.h').write_text(header)
        known={str(private/name):row for name,row in expected.items()}
        known[str(generated/'game_profile.h')]=tool.record(header.encode())
        main=private/'upstream/dm/host/main.c';svc=private/'upstream/dm/host/hle_svc.c'
        for source in (main,svc,private/'upstream/dm/host/hle_hid.c',private/'upstream/dm/host/hle_sdk16.c'):
            dep=self.work/(source.name+'.d')
            command=['clang','-std=gnu11','-c','-Wall','-Wno-unused-function',
                *['-D'+d for d in tool.DEFINES],'-Dmain=dm_main',
                '-I'+str(generated),'-I'+str(private/'adapter'),'-I'+str(private/'memory_provider'),
                *tool.audio_include_flags(private,receipt['audren_source_inventory']),
                '-I'+str(private/'upstream/dm/gfx/nvn/include'),
                '-MMD','-MF',str(dep),
                str(source),'-o',str(self.work/(source.name+'.o'))]
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            actual=base.parse_depfile(dep)
            sdk=next((p.parent for p in actual if p.name=='SDKSettings.json'),self.work/'sdk')
            dependencies=base.verify_dependencies(actual,known,sdk,self.work/'builtins')
            if source.name=='hle_hid.c':
                self.assertIn(str(private/'audio/repository/native/botw_input/input.h'),{r['path'] for r in dependencies['bound_inputs']})
                symbols=subprocess.run(['nm','-gU',str(self.work/(source.name+'.o'))],capture_output=True,text=True)
                self.assertEqual(symbols.returncode,0,symbols.stdout+symbols.stderr)
                tool.verify_symbols(symbols.stdout,{'_botw_base_v0_debug_pad_tick'})
        # Consume the actual builder recipes with the local Mac target only.
        # This verifies C++/prepared-header integration, never iPhone admission.
        units=json.loads((C/'compile_sources.json').read_bytes())['translation_units']
        plans=tool.compile_commands(private,self.work,units,{'clang':'clang','sdk_path':'/owned/sdk'},base,adapted)
        for suffix in ('/audio/eden/src/audio_core/audio_render_manager.cpp','/audio/repository/experiments/botw_audio_renderer/native/renderer_host.cpp'):
            plan=next(row for row in plans if row['source'].endswith(suffix));command=list(plan['argv'])
            for flag in ('-target','-isysroot'):
                index=command.index(flag);del command[index:index+2]
            obj=self.work/(Path(plan['source']).name+'.o');dep=obj.with_suffix('.d')
            for flag,value in (('-o',obj),('-MF',dep),('-MT',obj)):
                command[command.index(flag)+1]=str(value)
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            actual=base.parse_depfile(dep)
            sdk=next((p.parent for p in actual if p.name=='SDKSettings.json'),self.work/'sdk')
            dependencies=base.verify_dependencies(actual,known,sdk,self.work/'builtins')
            self.assertIn(plan['source'],{r['path'] for r in dependencies['bound_inputs']})
        target=private/'memory_provider/base_v0_heap.c';target.write_bytes(target.read_bytes()+b'\n/* owned mutation */\n')
        with self.assertRaisesRegex(ValueError,'changed'):adapted.verify_private(private,expected,base)

    def test_receipt_never_claims_installable_or_device_execution(self):
        report=self.tool().new_receipt()
        self.assertIs(report['installable'],False)
        self.assertIs(report['host_guest_gpu_executed'],False)
        self.assertIs(report['audio_runtime_closed'],False)
        self.assertEqual(report['metadata_game_version'],'1.0.0')
        self.assertEqual(report['entrypoint'],'UIKit _main')

    def test_unfrozen_runtime_source_refuses_before_copy_or_sdk_query(self):
        tool=self.tool();component=self.work/'component';component.mkdir()
        (component/'SOURCE.lock.json').write_text('{"adapter_files":{},"external_code_files":{}}')
        with self.assertRaisesRegex(ValueError,'frozen|source|lock'):tool.load_helpers(component)

    def test_bound_source_loader_ignores_real_pyc_and_refuses_changed_source(self):
        tool=self.tool();source=self.work/'module.py';source.write_text('value=2\n')
        stamp=source.stat();py_compile.compile(str(source),doraise=True)
        source.write_text('value=1\n');os.utime(source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        expected=tool.record(source.read_bytes())
        self.assertEqual(tool.source_module('owned',source,expected).value,1)
        source.write_text('value=3\n')
        with self.assertRaisesRegex(ValueError,'frozen lock'):tool.source_module('owned',source,expected)

    def test_regular_source_fifo_refuses_in_bounded_real_child(self):
        self.tool();fifo=self.work/'source-fifo';os.mkfifo(fifo)
        script='''from pathlib import Path
import sys
p=Path(sys.argv[1]);scope={"__file__":str(p),"__name__":"owned_fifo"}
exec(compile(p.read_bytes(),str(p),"exec"),scope)
try:scope["regular_bytes"](Path(sys.argv[2]))
except ValueError:sys.exit(0)
sys.exit(91)
'''
        result=subprocess.run([sys.executable,'-c',script,str(TOOL),str(fifo)],capture_output=True,text=True,timeout=1.2)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_actual_compiler_dependency_closure_rejects_missing_and_changed_header(self):
        self.tool();base=load_source(C/'tools/build_link_only.py')
        source=self.work/'owned.c';header=self.work/'owned.h'
        source.write_text('#include "owned.h"\nint owned_value(void){return OWNED_VALUE;}\n');header.write_text('#define OWNED_VALUE 7\n')
        dep=self.work/'owned.d';obj=self.work/'owned.o'
        command=['clang','-std=c11','-Wall','-Wextra','-Werror','-MMD','-MF',str(dep),'-c',str(source),'-o',str(obj)]
        result=subprocess.run(command,capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        known={str(p):base.file_record(p) for p in (source,header)};dependencies=base.parse_depfile(dep)
        sdk=next((p.parent for p in dependencies if p.name=='SDKSettings.json'),self.work/'sdk')
        verified=base.verify_dependencies(dependencies,known,sdk,self.work/'builtins')
        self.assertEqual({r['path'] for r in verified['bound_inputs']},set(known))
        with self.assertRaisesRegex(ValueError,'unlocked'):base.verify_dependencies(dependencies,{str(source):known[str(source)]},sdk,self.work/'builtins')
        header.write_text('#define OWNED_VALUE 8\n')
        with self.assertRaisesRegex(ValueError,'changed'):base.verify_dependencies(dependencies,known,sdk,self.work/'builtins')

    def test_output_rejects_parent_symlink_and_component_overlap(self):
        tool=self.tool();component=self.work/'component';component.mkdir();alias=self.work/'alias';alias.symlink_to(component,target_is_directory=True)
        dangling=self.work/'dangling';dangling.symlink_to(self.work/'absent',target_is_directory=True)
        for output in (component/'out',alias/'out',component.parent,dangling/'out'):
            with self.subTest(output=output),self.assertRaises((ValueError,FileExistsError)):tool.prepare_output(output,component)
        self.assertFalse((component/'out').exists())

if __name__=='__main__':unittest.main()
