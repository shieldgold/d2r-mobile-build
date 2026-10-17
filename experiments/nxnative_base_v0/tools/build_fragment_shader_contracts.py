"""Publish a separate base-1.0 fragment subset using the complete CPU chain.

Output recipes are actual-draw type conditions, never per-title format facts.
The old publication and typed vertex supplement remain unchanged. No Metal,
host, guest or shader execution is performed.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import types

ROOT=Path(__file__).resolve().parents[3]
VERTEX_SOURCE='experiments/nxnative_base_v0/tools/build_vertex_shader_contracts.py'
VERTEX_SHA='3d2d3054f5e1ba075d308fd0274ba3717c3a17bb5a109e61dc84c91979b31e21'
PIPELINE='experiments/nxnative_base_v0/patches/base-v0-fragment-shader-pipeline.patch'
PIPELINE_SHA='4b461632b09cc81f95786e6556621983b58b285425c4b8e8d125a0ffc647fcdd'


def bootstrap():
    path=ROOT/VERTEX_SOURCE
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=65536:raise ValueError('bounded regular vertex source required')
        with os.fdopen(fd,'rb') as stream:
            fd=None;raw=stream.read(65537);after=os.fstat(stream.fileno())
        if len(raw)!=before.st_size or hashlib.sha256(raw).hexdigest()!=VERTEX_SHA or any(
                getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):
            raise ValueError('complete vertex source identity changed')
    finally:
        if fd is not None:os.close(fd)
    module=types.ModuleType('_nx_fragment_complete_vertex_source');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


@contextmanager
def staged_pipeline(temporary):
    vertex=bootstrap()
    with vertex.staged_pipeline(temporary) as (package,_,_,ports):
        # Reuse the entire verified source/module snapshot. The fragment scope
        # starts from the unchanged original publisher, independently of VS.
        for reverse,relative,expected in ((True,vertex.PIPELINE,vertex.PIPELINE_SHA),(False,PIPELINE,PIPELINE_SHA)):
            raw=package.read(ROOT/relative,128*1024)
            package.require(package.digest(raw)==expected,'complete pipeline patch identity')
            path=Path(temporary)/('fragment-reverse.patch' if reverse else 'fragment-pipeline.patch');path.write_bytes(raw)
            cmd=['patch','--batch','--fuzz=0',*(['-R'] if reverse else []),'-p1','-i',str(path)]
            result=subprocess.run(cmd,cwd=ports.COMPONENT,capture_output=True,text=True)
            package.require(result.returncode==0 and 'fuzz' not in result.stdout and 'offset' not in result.stdout,
                            'exact complete fragment source patch failed')
        publisher=ports.load(ports.COMPONENT/'adapter/shader_conditional_build.py','_native_fragment_builder')
        validator=ports.load(ports.COMPONENT/'scripts/package_conditional.py','_native_fragment_validator')
        package.require(publisher.SCOPE=='typed-fs-nonqueried-v1','separate fragment scope required')
        yield package,publisher,validator,ports


def recipe_from_source(msl):
    blocks=re.findall(r'struct main0_out\s*\{(.*?)\};',msl,re.S)
    if len(blocks)>1:raise ValueError('ambiguous fragment output structure')
    outputs={}
    for ty,location in re.findall(r'(\w+)\s+\w+\s*\[\[color\((\d+)\)\]\]',blocks[0] if blocks else ''):
        location=int(location)
        if location>=8 or location in outputs or not re.fullmatch(r'(float|int|uint)[234]?',ty):
            raise ValueError('fragment output is not a strict native typed output')
        outputs[location]=re.sub('[234]$','',ty)
    return [dict(location=k,component_type=v) for k,v in sorted(outputs.items())]


def recipes(probe_manifest,output):
    vertex=bootstrap();package=vertex.bootstrap();manifest=package.document(package.read(probe_manifest))
    package.require(isinstance(manifest,dict) and isinstance(manifest.get('results'),list),'probe result inventory required')
    selected={}
    for row in manifest['results']:
        capture=row.get('capture',{})
        if capture.get('stage')!=1:continue
        key=row.get('key')
        package.require(isinstance(key,str) and re.fullmatch('[0-9a-f]{16}',key) and key==capture.get('key') and key not in selected,
                        'probe complete fragment identity')
        if row.get('notes',[]) or any(item.get('what')=='translator query fallback' for item in row.get('unknowns',[])):continue
        raw=package.read(row['files']['msl'],16*1024*1024)
        package.require(raw and b'\0' not in raw,'bounded NUL-free probe source required')
        recipe=recipe_from_source(raw.decode('utf-8',errors='strict'))
        if [r['location'] for r in recipe]==[0]:continue
        package.require(set(row.get('required_facts',{}).get('outputs',{}))=={str(r['location']) for r in recipe},
                        'probe output metadata differs from actual source')
        selected[key]=recipe
    package.require(selected,'no fragment recipes selected')
    value=dict(schema='nxnative-base-v0-fragment-recipes-v1',profile='botw-switch-base-v0',recipes=selected)
    output=package.checked_path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as stream:stream.write(package.canonical(value))
    return len(selected)


def verified_files(package,validator,ports,source,identities):
    files,index=validator.shader_files(source,ports)
    admission=package.document(files['conditional-admission.json'])
    package.require(all(key in identities and row['capture_sha256']==identities[key]['capture_sha256'] and
                        row['stage_name']=='fragment' and identities[key]['stage']==1 and
                        identities[key]['status']=='only_identity' for key,row in admission['captures'].items()),
                    'fragment supplement differs from complete raw base-1.0 identity')
    files['fragment-source-package.json']=package.canonical(dict(schema='nxnative-base-v0-fragment-source-package-v1',
        profile='botw-switch-base-v0',game_version='1.0.0',scope='typed-fs-nonqueried-v1',bridge_sha256=package.BRIDGE_SHA256,
        conditional_index_sha256=package.digest(files['shader-package.json']),conditional_publication_sha256=index['publication_sha256']))
    return files,index,len(admission['captures'])


def audit(publication,bridge,package_output=None):
    vertex=bootstrap()
    with tempfile.TemporaryDirectory(prefix='nx-fragment-complete-audit-') as temporary:
        with staged_pipeline(Path(temporary).resolve()) as (package,_,validator,ports):
            files,index,count=verified_files(package,validator,ports,publication,vertex.checked_bridge(package,bridge))
            if package_output is not None:package.publish(files,package_output)
            print(json.dumps(dict(fragment_captures=count,variants=count*4,chain=index['chain'],
                                  complete_original_reaudit=True,game_execution_ready=False)))
    return 0


def build(inputs,captures,request,destination,*,bridge,xlate,spirv_cross,jobs=1,package_output=None):
    vertex=bootstrap()
    with tempfile.TemporaryDirectory(prefix='nx-fragment-complete-pipeline-') as temporary:
        with staged_pipeline(Path(temporary).resolve()) as (package,publisher,validator,ports):
            identities=vertex.checked_bridge(package,bridge);selected,_,_=publisher.requested_keys(request)
            package.require(all(key in identities and identities[key].get('stage')==1 and
                                identities[key].get('status')=='only_identity' for key in selected),
                            'fragment supplement may only extend existing base-1.0 identities')
            result=publisher.build(inputs,captures,request,destination,xlate=xlate,spirv_cross=spirv_cross,jobs=jobs)
            if result:return result
            files,_,_=verified_files(package,validator,ports,destination,identities)
            if package_output is not None:package.publish(files,package_output)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    recipe=commands.add_parser('recipes');recipe.add_argument('probe_manifest',type=Path);recipe.add_argument('output',type=Path)
    audit_=commands.add_parser('audit');audit_.add_argument('publication',type=Path);audit_.add_argument('--bridge',type=Path,required=True);audit_.add_argument('--package-output',type=Path)
    compile_=commands.add_parser('build')
    for name in ('inputs','captures','request','destination'):compile_.add_argument(name,type=Path)
    for name in ('bridge','xlate','spirv-cross'):compile_.add_argument('--'+name,type=Path,required=True)
    compile_.add_argument('--jobs',type=int,default=1);compile_.add_argument('--package-output',type=Path)
    options=vars(parser.parse_args(argv));command=options.pop('command')
    try:
        if command=='recipes':
            count=recipes(**options);print(json.dumps(dict(typed_fragment_recipes=count,game_version='1.0.0',game_execution_ready=False)));return 0
        return (audit if command=='audit' else build)(**options)
    except (ValueError,OSError,KeyError,TypeError,AttributeError) as error:
        print('typed fragment source refused: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
