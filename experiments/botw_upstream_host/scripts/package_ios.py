#!/usr/bin/env python3
"""Prepare an unsigned configured whole-host app. Never install, sign or run it.

Build receipts are supplied through the caller's trusted artifact channel. They
are checked against local source locks and the actual executable, not treated as
cryptographic proof of how an arbitrary third party produced that executable.
Game resources, Documents, saves and user identities are never copied.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import re
import shutil
import struct
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from ci import macho_minimum_os
from scripts.botw_native import ios, guest_framework

COMPONENT=Path(__file__).resolve().parents[1]
NAMES=('main','sdk','subsdk0','subsdk1','subsdk2','subsdk3')
META=64*1024**2
BINARY=512*1024**2


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    old=sys.dont_write_bytecode;sys.dont_write_bytecode=True
    try:spec.loader.exec_module(module)
    finally:sys.dont_write_bytecode=old
    return module


driver=load(COMPONENT/'scripts/build_link_only.py','whole_package_build_contract')
registry_reader=load(COMPONENT/'scripts/read_swforce_registry.py','whole_package_registry')
source_builder=load(COMPONENT/'adapter/shader_source_build.py','whole_package_source_contract')


def digest(data):return hashlib.sha256(data).hexdigest()


def checked_path(path):
    path=Path(path)
    if '..' in path.parts:raise ValueError('path traversal is not allowed')
    path=Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('symlink input or output path')
    return path


def read(path,limit=META):return ios._read(checked_path(path),limit)


def document(path):return ios._json(read(path))


def write_json(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def executable_layout(data):
    """Supplement the existing target and registry parsers with entry/subtype checks."""
    if macho_minimum_os(data)!=(18,0,0):raise ValueError('whole executable must target iPhoneOS 18.0')
    _,_,subtype,filetype,count,size,flags,reserved=struct.unpack_from('<8I',data)
    if subtype!=0 or filetype!=2 or reserved or not flags&0x200000:
        raise ValueError('whole executable must be ARM64_ALL MH_EXECUTE PIE')
    cursor=32;segments=[];entries=[];text_entries=[]
    for _ in range(count):
        command,length=struct.unpack_from('<II',data,cursor)
        # macho_minimum_os has already checked all command framing/bounds.
        if command==0x80000028:
            if length!=24:raise ValueError('invalid LC_MAIN size')
            entries.append(struct.unpack_from('<QQ',data,cursor+8)[0])
        elif command in (0x24,0x25,0x2f,0x30):
            raise ValueError('legacy/conflicting platform commands are not a modern iOS18 target')
        elif command==0x19:
            if length<72:raise ValueError('truncated executable segment')
            row=struct.unpack_from('<II16sQQQQiiII',data,cursor)
            _,_,name,vm,vmsize,offset,filesize,maximum,protection,nsects,_=row
            if (length!=72+80*nsects or filesize>vmsize or offset>len(data) or filesize>len(data)-offset or
                    vmsize>2**64-1-vm or protection&~maximum or maximum&~7 or
                    maximum&2 and maximum&4):raise ValueError('invalid executable segment bounds/protection')
            segments.append((vm,vmsize,offset,filesize))
            for i in range(nsects):
                section=struct.unpack_from('<16s16sQQIIIIIIII',data,cursor+72+i*80)
                sn,sg,address,span,location,alignment=section[:6];kind=section[8]&0xff
                if (sg!=name or address<vm or span>vmsize or address-vm>vmsize-span or
                        alignment>31 or address&((1<<alignment)-1)):
                    raise ValueError('invalid executable section VM bounds/alignment')
                if kind not in (1,0xc,0x12) and (location<offset or span>filesize or
                        location-offset>filesize-span or address-vm!=location-offset):
                    raise ValueError('invalid executable section file bounds')
                if name.rstrip(b'\0')==b'__TEXT' and offset==0 and protection==5 and sn.rstrip(b'\0')==b'__text':
                    if not span or location<32+size or kind:raise ValueError('invalid file-backed executable entry section')
                    text_entries.append((location,span))
        cursor+=length
    for i,(vm,size,offset,span) in enumerate(segments):
        for other,other_size,at,length in segments[:i]:
            if (size and other_size and vm<other+other_size and other<vm+size or
                    span and length and offset<at+length and at<offset+span):
                raise ValueError('overlapping executable segments')
    if (len(entries)!=1 or entries[0]&3 or len(text_entries)!=1 or
            not text_entries[0][0]<=entries[0] or entries[0]+4>sum(text_entries[0])):
        raise ValueError('LC_MAIN must point inside the unique RX __TEXT,__text')
    return {'cpu':'arm64','subtype':0,'filetype':'MH_EXECUTE','minimum_ios':'18.0','entry_file_offset':entries[0]}


def verify_build(binary,receipt_path,commands_path,repository):
    component=repository/'experiments/botw_upstream_host';lock_raw=read(component/'SOURCE.lock.json')
    lock=ios._json(lock_raw);receipt_raw=read(receipt_path);receipt=ios._json(receipt_raw)
    if (receipt.get('schema')!='botw-whole-host-link-only-v1' or receipt.get('sdk')!='iphoneos' or
            receipt.get('target')!='arm64-apple-ios18.0' or receipt.get('runtime_configured') is not True or
            receipt.get('source_stable') is not True or receipt.get('host_or_guest_or_GPU_executed') is not False or
            any(type(receipt.get(k)) is not int or receipt[k]!=0 for k in ('compile_exit','link_exit')) or
            receipt.get('link_executable_sha256')!=digest(binary) or receipt.get('source_lock_sha256')!=digest(lock_raw)):
        raise ValueError('requires a successful source-stable configured physical iOS whole-host receipt')
    sources=driver.verify_sources(component,lock)
    external=lock.get('external_files',{})
    if receipt.get('source_sha256')!=sources or receipt.get('external_source_sha256')!={p:r['sha256'] for p,r in external.items()}:
        raise ValueError('whole build source provenance differs from the local lock')
    for name,row in external.items():
        ios._relative(name)
        if digest(read(repository/name,BINARY))!=row['sha256']:raise ValueError('external source SHA256 mismatch')
    if receipt.get('patched_source_sha256')!=lock['patched_source_sha256']:
        raise ValueError('compiled upstream patch identity differs')
    configured=driver.configured_runtime_units(lock,'iphoneos',True)
    units=document(component/'UPSTREAM_UNITS.json')['ios_translation_units']
    if type(receipt.get('expected_upstream_translation_units')) is not int or receipt['expected_upstream_translation_units']!=len(units):
        raise ValueError('upstream translation-unit count differs')
    required=set(units+lock['adapter_translation_units']+configured+lock.get('external_translation_units',[])+
                 ['frontend/'+p for p in ('whole_main.m','whole_frontend.m','whole_front_state.c')])
    audio=lock.get('audio_renderer')
    if audio:
        audio_sources=load(COMPONENT/'scripts/audio_sources.py','whole_package_audio_inventory')
        audio_sources.verify_inventory(repository,audio)
        if receipt.get('audio_renderer')!=audio:raise ValueError('complete audio source contract differs')
        original=[p for p in audio['prepared_source_sha256'] if p.endswith('.cpp') and
                  (p.startswith('src/audio_core/renderer/') or p.startswith('src/audio_core/adsp/apps/audio_renderer/'))]
        original+=['src/audio_core/audio_render_manager.cpp','src/audio_core/sink/sink_stream.cpp']
        if len(original)!=audio['expected_original_translation_units']:raise ValueError('incomplete original audio closure')
        audio_units=original+audio['adapter_translation_units']
        if len(audio_units)!=audio['expected_total_translation_units']:raise ValueError('incomplete complete audio closure')
        required.update('audio/'+p for p in audio_units)
    if any(type(receipt.get(k)) is not int or receipt[k]!=len(required) for k in
           ('compiled_translation_units','expected_total_translation_units')):
        raise ValueError('whole translation-unit closure differs')
    dependencies=receipt.get('compiler_dependency_manifest')
    if (not isinstance(dependencies,list) or type(receipt.get('actual_compiler_dependency_count')) is not int or
            receipt['actual_compiler_dependency_count']!=len(dependencies)):
        raise ValueError('missing actual compiler dependency provenance')
    covered=set()
    for row in dependencies:
        if not isinstance(row,dict) or set(row) not in ({'component_path','compiled_sha256','role'},{'repository_path','compiled_sha256','role'}):
            raise ValueError('malformed compiler dependency provenance')
        name=row.get('component_path',row.get('repository_path'));ios._relative(name)
        if name in covered:raise ValueError('duplicate compiler dependency')
        covered.add(name)
        if 'component_path' in row:
            expected=lock['patched_source_sha256'].get(name.removeprefix('upstream/'),sources.get(name))
            role=('patched-upstream' if name.removeprefix('upstream/') in lock['patched_source_sha256'] else
                  'original-upstream') if name.startswith('upstream/') else 'thin-platform-adapter'
        else:
            expected=external.get(name,{}).get('sha256')
            prefix=audio['component']+'/vendor/eden/' if audio else ''
            role='existing-complete-resource-provider'
            if audio and name.startswith(prefix):
                expected=audio['prepared_source_sha256'].get(name[len(prefix):],expected)
                role='complete-audio-original-or-patched-source'
        if not expected or row.get('compiled_sha256')!=expected or row.get('role')!=role:
            raise ValueError('unlocked actual compiler dependency')
    if not set(configured)<=covered:raise ValueError('configured provider was not an actual compiler dependency')
    for name in required:
        if name in units:dependency='upstream/'+name
        elif name.startswith('audio/'):
            relative=name.removeprefix('audio/')
            dependency=audio['component']+('/vendor/eden/' if relative in original else '/')+relative
        else:dependency=name
        if dependency not in covered:raise ValueError('translation unit absent from actual compiler dependencies: '+name)
    command_raw=read(commands_path);commands=ios._json(command_raw)
    if receipt.get('commands_sha256')!=digest(command_raw) or not isinstance(commands,list):raise ValueError('compiler command receipt SHA256 differs')
    labels={}
    for command in commands:
        if command.get('exit')!=0:raise ValueError('failed command in successful build receipt')
        label=command.get('label')
        if label in labels:raise ValueError('duplicate build command label')
        labels[label]=command
    compiled={label for label,row in labels.items() if '-c' in row.get('argv',[])}
    wanted={p.replace('/','__') for p in required}
    if compiled!=wanted:raise ValueError('actual compile commands omit or add a translation unit')
    for label in wanted|{'whole-host-link-only'}:
        argv=labels.get(label,{}).get('argv',[])
        if (not isinstance(argv,list) or not all(isinstance(v,str) for v in argv) or
                argv.count('-target')!=1 or argv.index('-target')+1>=len(argv) or
                argv[argv.index('-target')+1]!='arm64-apple-ios18.0' or
                not {'-DDM_SHELL=1','-DBOTW_WHOLE_RUNTIME_CONFIGURED=1','-DBOTW_WHOLE_SHADER_PUBLICATION=1',
                     '-Werror=unguarded-availability','-Werror=unguarded-availability-new'}<=set(argv)):
            raise ValueError('actual command lacks the configured iPhoneOS target/macros')
    for name in required:
        argv=labels[name.replace('/','__')]['argv'];at=argv.index('-c')+1
        if argv.count('-c')!=1 or at>=len(argv) or not argv[at].endswith('/'+name.removeprefix('audio/')):
            raise ValueError('compile label is not its actual source translation unit')
    linked=[Path(v).name for v in labels['whole-host-link-only']['argv'] if v.endswith('.o')]
    if len(linked)!=len(wanted) or set(linked)!={p+'.o' for p in wanted}:
        raise ValueError('actual whole link did not retain the complete object closure')
    return lock,{'receipt_sha256':digest(receipt_raw),'commands_sha256':digest(command_raw),
                 'source_lock_sha256':digest(lock_raw),'translation_units':len(required)}


def verify_guest(guest,report,rewrite,profile,repository,temporary):
    report=checked_path(report);rewrite=checked_path(rewrite);ios._tree(rewrite)
    component=repository/'experiments/botw_upstream_host'
    header=read(component/'adapter/whole_runtime_resource_manifest.h').decode('utf-8')
    pins={name:(int(size),sha) for name,size,sha in re.findall(r'\{"([^"/]+)", (\d+)u, "([0-9a-f]{64})"\}',header)}
    expected={'base-profile.json','guest-framework-report.json'}|{n+'.'+k+'.tsv' for n in NAMES for k in ('fixups','gates')}
    if set(pins)!=expected:raise ValueError('runtime fixed metadata authority is incomplete')
    files=ios._tree(checked_path(guest));wanted={'Guest.framework/Guest','Guest.framework/Info.plist','guest-framework-report.json'}|{'dm/'+n+'.'+k+'.tsv' for n in NAMES for k in ('fixups','gates')}
    if set(files)!=wanted:raise ValueError('Guest root includes missing, unexpected or user-data files')
    profile_raw=None
    for name,(size,sha) in pins.items():
        path=profile if name=='base-profile.json' else Path(guest)/('' if name=='guest-framework-report.json' else 'dm')/name
        raw=read(path)
        if len(raw)!=size or digest(raw)!=sha:raise ValueError('fixed v4/base metadata identity differs: '+name)
        if name=='base-profile.json':profile_raw=raw
    metadata=document(Path(guest)/'guest-framework-report.json');names=[r['name'] for r in metadata['modules']]
    if len(names)!=6 or set(names)!=set(NAMES):raise ValueError('requires exactly the fixed six-module Guest')
    rebuilt=temporary/'verified-guest'
    generated=guest_framework.build_framework(report,rewrite,rebuilt,module_names=names,minimum_ios='18.0')
    if generated!=metadata:raise ValueError('Guest report differs from original regenerated source evidence')
    for name in files:
        if read(files[name],BINARY)!=read(rebuilt/name,BINARY):raise ValueError('Guest content differs from original regenerated inputs: '+name)
    if macho_minimum_os(read(rebuilt/'Guest.framework/Guest',BINARY))!=(18,0,0):raise ValueError('Guest target must be iPhoneOS 18')
    return rebuilt,profile_raw,{'report_sha256':digest(read(rebuilt/'guest-framework-report.json')),
                                'framework_sha256':generated['framework']['sha256'],'modules':names}


def shader_files(source):
    source=checked_path(source);ios._tree(source)
    # Execute only hash-verified private copies of the pinned builder. The
    # provided tree can change after validation; no unchecked reopen may feed
    # Python execution or the final bundle bytes.
    with tempfile.TemporaryDirectory(prefix='botw-package-shader-snapshot-') as temporary:
        return shader_snapshot(source,Path(temporary).resolve())


def shader_snapshot(source,temporary):
    publication_raw=read(source/'source-publication.json');publication=ios._json(publication_raw)
    admission_raw=read(source/'admitted/release-admission.json');admission=ios._json(admission_raw)
    if (publication.get('schema')!='botw-shader-source-publication-v1' or publication.get('profile')!='botw-switch-base-v0' or
            publication.get('device_compilation_tested') is not False or publication.get('guest_or_gpu_executed') is not False or
            publication.get('release_admission_sha256')!=digest(admission_raw) or
            publication.get('source_adapter_sha256')!=source_builder.sha(COMPONENT/'adapter/shader_source_build.py') or
            admission.get('schema')!='botw-shader-release-admission-v1' or admission.get('approved') is not True or
            admission.get('profile')!='botw-switch-base-v0' or publication.get('input_manifest_sha256')!=admission.get('input_manifest_sha256')):
        raise ValueError('requires a complete reviewed source publication, not a probe or compiled claim')
    used=0
    def save(relative,raw):
        nonlocal used
        used+=len(raw)
        if used>512*1024**2:raise ValueError('shader snapshot exceeds bounded batch budget')
        path=temporary/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    for name,sha in admission['files'].items():
        ios._relative(name)
        raw=read(source/'admitted'/name)
        if digest(raw)!=sha:raise ValueError('admitted detached source snapshot changed')
        save('admitted/'+name,raw)
    if digest(read(temporary/'admitted/inputs/inputs.json'))!=admission.get('input_manifest_sha256'):
        raise ValueError('admission input manifest identity differs from its exact snapshot')
    stage=source/'toolchain'
    # The complete private v8 + graphics image-table stage is independently
    # verified before importing any supplied Python byte. Re-check each exact
    # copied byte too; later changes to source cannot enter the owned snapshot.
    expected_sources=source_builder.source_toolchain_hashes(stage)
    for name,expected in expected_sources.items():
        ios._relative(name)
        raw=read(stage/name)
        if digest(raw)!=expected:raise ValueError('staged original shader source differs')
        save('toolchain/'+name,raw)
    tool=source_builder.load(temporary/'toolchain/dm/gfx/tools/botw_shader_build.py','whole_package_complete_shader_tool')
    package_name=publication.get('package');ios._relative(package_name)
    if not re.fullmatch(r'build/botw-s-[0-9a-f]{12}',package_name):raise ValueError('unexpected source package path')
    package=source/package_name
    for name,path in ios._tree(package).items():
        ios._relative(name);save(package_name+'/'+name,read(path))
    package=temporary/package_name;manifest_raw=read(package/'manifest-build.json')
    if digest(manifest_raw)!=publication.get('manifest_sha256'):raise ValueError('source manifest SHA256 differs')
    manifest=ios._json(manifest_raw)
    publisher=source_builder.SourcePublication(tool,admission,temporary/'admitted/captures',digest(admission_raw))
    publisher.chain=manifest['chain'];tools=tool.Tools();tools.identity=manifest['tools']
    options=dict(manifest['options'],source_adapter_sha256=publication['source_adapter_sha256'],release_admission_sha256=digest(admission_raw))
    if tools.chain(options)!=publisher.chain:raise ValueError('complete original tool/source/module chain differs')
    publisher.check_batch(package,manifest)
    if (package.name!='botw-s-'+publisher.chain or type(publication.get('captures')) is not int or
            publication['captures']!=len(manifest['results'])):raise ValueError('source batch coverage differs')
    result={'source-publication.json':publication_raw,'release-admission.json':admission_raw}
    for key,identity in admission['captures'].items():
        fact=temporary/'admitted/facts'/(key+'.facts.json');raw=read(fact)
        if digest(raw)!=identity['facts_sha256']:raise ValueError('admitted facts SHA256 differs')
        tool.load_facts(str(fact),publisher.captures[key])
        result['facts/'+key+'.facts.json']=raw
    if set(ios._tree(temporary/'admitted/facts'))!={key+'.facts.json' for key in admission['captures']}:
        raise ValueError('extra or missing source facts')
    for name in ios._tree(package):result[package.name+'/'+name]=read(package/name)
    index={'schema':'botw-whole-shader-package-v1','profile':'botw-switch-base-v0','chain':publisher.chain,
           'directory':package.name,'publication_sha256':digest(publication_raw),'admission_sha256':digest(admission_raw)}
    result['shader-package.json']=(json.dumps(index,indent=2,sort_keys=True)+'\n').encode()
    return result,index


def prepare(binary,receipt,commands,guest,preflight_report,rewrite,source_publication,info_plist,output,*,build_version):
    output=checked_path(output)
    if os.path.lexists(output):raise FileExistsError('output app already exists')
    if output.name!='BOTWNative.app':raise ValueError('output must be named BOTWNative.app')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}',build_version):raise ValueError('explicit numeric debug build version required')
    raw=read(binary,BINARY);layout=executable_layout(raw)
    lock,build=verify_build(raw,receipt,commands,ROOT)
    shaders,shader_index=shader_files(source_publication)
    info=plistlib.loads(read(info_plist))
    if (info.get('CFBundleIdentifier')!=ios.BUNDLE_ID or info.get('CFBundleExecutable')!='BOTWNative' or
            info.get('CFBundlePackageType')!='APPL' or info.get('MinimumOSVersion')!='18.0' or
            info.get('UIDeviceFamily')!=[1,2] or any(type(v) is not int for v in info['UIDeviceFamily']) or
            info.get('CFBundleVersion')==build_version):
        raise ValueError('requires the true iOS18 BOTW plist template and a new build version')
    info.update(CFBundleVersion=build_version,CFBundleShortVersionString='0.2.0',
                CFBundleDisplayName='BOTW Native Debug',BOTWWholeBuild='configured-source-debug')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.botw-whole-package-',dir=output.parent) as temporary:
        temporary=Path(temporary);app=temporary/'BOTWNative.app';app.mkdir()
        (app/'BOTWNative').write_bytes(raw);(app/'BOTWNative').chmod(0o755)
        actual=registry_reader.audit_registry(app/'BOTWNative',lock['swforce_metadata_sha256'])
        if actual['count']!=lock['swforce_record_count']:raise ValueError('actual whole binary registry closure differs')
        profile=ROOT/'profiles/botw-switch-base-v0.json'
        rebuilt,profile_raw,guest_identity=verify_guest(guest,preflight_report,rewrite,profile,ROOT,temporary)
        (app/'Info.plist').write_bytes(plistlib.dumps(info,sort_keys=True))
        (app/'Frameworks').mkdir();shutil.copytree(rebuilt/'Guest.framework',app/'Frameworks/Guest.framework')
        shutil.copytree(rebuilt/'dm',app/'botw')
        (app/'botw/base-profile.json').write_bytes(profile_raw)
        (app/'botw/guest-framework-report.json').write_bytes(read(rebuilt/'guest-framework-report.json'))
        for name,data in shaders.items():
            target=app/'botw/shaders'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        files={name:{'bytes':path.stat().st_size,'sha256':digest(read(path,BINARY))} for name,path in ios._tree(app).items()}
        audit={'schema':'botw-whole-ios-package-v1','bundle_id':ios.BUNDLE_ID,'build_version':build_version,
               'unsigned':True,'installed':False,'executed':False,'phone_ready':False,'game_playable':False,
               'runtime_configured':True,'runtime_shader_compilation_required':True,'executable':layout,
               'build':build,'guest':guest_identity,'shader':shader_index,'files':files,
               'scope':'exact admitted shader batch only; unsigned owned preparation is not device or gameplay acceptance'}
        write_json(app/'package-manifest.json',audit)
        if os.path.lexists(output):raise FileExistsError('output appeared during packaging')
        app.rename(output)
    return audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','receipt','guest','source-publication','info-plist','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--commands',type=Path)
    parser.add_argument('--preflight-report',type=Path,default=ROOT/'outputs/botw-native-host39-bounded-pthread-stack-v4/report.json')
    parser.add_argument('--rewrite',type=Path,default=ROOT/'outputs/botw-native-host39-bounded-pthread-stack-v4/rewrite')
    parser.add_argument('--build-version',required=True)
    args=vars(parser.parse_args(argv));args['commands']=args['commands'] or args['receipt'].parent/'commands.json'
    try:result=prepare(**args)
    except (OSError,ValueError,KeyError,TypeError,IndexError,AttributeError,struct.error) as error:
        print('unsigned whole iOS packaging refused: '+str(error),file=sys.stderr);return 1
    print(json.dumps({'app':str(args['output']),'unsigned':True,'installed':False,'phone_ready':False,
                      'files':len(result['files']),'shader_chain':result['shader']['chain']}));return 0


if __name__=='__main__':raise SystemExit(main())
