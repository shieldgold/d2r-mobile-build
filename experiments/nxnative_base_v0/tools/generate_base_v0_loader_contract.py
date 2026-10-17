#!/usr/bin/env python3
"""Bind the existing complete offline contract producer to the actual new host.

The original parser, packer, MOD0/relocation walker and source gate stay whole.
Only its obsolete614-row admission predicate is replaced by the independently
read current binary count/digest. No resources, binary or report are patched.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import stat
import sys
import types

sys.dont_write_bytecode=True
COMPONENT=Path(__file__).resolve().parents[1]
NAME='tools/generate_base_v0_loader_contract.py'
PARENT='tools/generate_base_v0_contract.py'
SCHEMA='nxnative-base-v0-loader-guard-contract-v1'


def digest(raw):return hashlib.sha256(raw).hexdigest()


def json_bytes(raw):
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError('duplicate source lock JSON key')
            out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=unique)


def regular(path,limit=64*1024**2):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('symlink input path')
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0<=before.st_size<=limit:raise ValueError('bounded regular input required')
        raw=f.read(limit+1);after=os.fstat(f.fileno())
    fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
    if len(raw)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in fields):raise ValueError('input changed')
    return raw


def source_module(raw,path):
    module=types.ModuleType('nxnative_bound_complete_contract_producer');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__);return module


def bind_current_registry(raw,path,registry):
    """One declared source-only predicate change; no parser algorithm changes."""
    if (type(registry.get('count')) is not int or not 1<=registry['count']<=4096
            or not isinstance(registry.get('canonical_metadata_sha256'),str)
            or len(registry['canonical_metadata_sha256'])!=64):raise ValueError('invalid actual force registry')
    old=b"registry['count'] != 614";new=b"registry['count'] != ADMITTED_REGISTRY_COUNT"
    if raw.count(old)!=1:raise ValueError('complete producer admission predicate changed')
    patched=raw.replace(old,new)
    module=source_module(patched,path)
    module.ADMITTED_REGISTRY_COUNT=registry['count'];module.REGISTRY_SHA=registry['canonical_metadata_sha256']
    return module,{'original_source_sha256':digest(raw),'adapted_source_sha256':digest(patched),
        'replacement':{'before':old.decode(),'after':new.decode(),'count':1},
        'admitted_actual_count':registry['count'],'admitted_actual_metadata_sha256':module.REGISTRY_SHA}


def generate(rewrite,guest,host_binary,host_receipt,output,component=COMPONENT):
    lock_raw=regular(component/'SOURCE.lock.json',16*1024**2)
    lock=json_bytes(lock_raw);adapters=lock['adapter_files']
    for name in (NAME,PARENT):
        if name not in adapters:raise ValueError('loader producer source is not frozen: '+name)
        raw=regular(component/name,256*1024)
        if adapters.get(name)!={'bytes':len(raw),'sha256':digest(raw)}:raise ValueError('loader producer source is not frozen: '+name)
    raw=regular(component/PARENT,256*1024);original=source_module(raw,component/PARENT)
    registry=original.read_registry24(host_binary)
    receipt_raw=regular(host_receipt,32*1024**2);receipt=original.json_bytes(receipt_raw)
    recorded=receipt.get('actual_force_registry',{})
    if (receipt.get('metadata_game_version')!='1.0.0' or receipt.get('entrypoint')!='UIKit _main'
            or recorded.get('count')!=registry['count']
            or recorded.get('canonical_metadata_sha256')!=registry['canonical_metadata_sha256']):
        raise ValueError('actual current registry lacks a matching true-UIKit base0 receipt')
    bound,binding=bind_current_registry(raw,component/PARENT,registry)
    # Calls the complete source-bound original producer: full packer admission,
    # six images/30 tables, real9 entries and every by-name relocation.
    result=bound.generate(rewrite,guest,host_binary,host_receipt,output)
    package=original.json_bytes(regular(guest/'package-report.json',65536))
    tables=package.get('loader_tables',{})
    expected={name+'.'+kind+'.tsv' for name in original.MODULES for kind in ('fixups','gates','cuts','params','obs')}
    if set(tables)!=expected:raise ValueError('complete thirty loader tables are absent')
    for name,row in tables.items():
        content=regular(guest/'dm'/name,1024*1024)
        if row!={'bytes':len(content),'sha256':digest(content)}:raise ValueError('actual loader table changed')
    result.update(schema=SCHEMA,title_id='01007EF00011E000',game_version='1.0.0',nvn_api={'major':52,'minor':32},
        loader_tables=tables,producer_binding=binding,
        force_policy='Authenticate every actual native24 row; select exact per-module by-name relocation counts. No old614 count, title masks or Guest29 hashes.',
        loader_guard_installed=False,installable=False,host_guest_gpu_executed=False,
        game_profile_h_generated=True,
        base_v0_host_metadata_configured=receipt.get('base_v0_host_metadata_configured') is True,
        pending=['normal loader guard integration and actual SDK build',
                 'complete base-v0 audio integration and device memory/GPU/audio/gameplay verification'],
        unproven_profile_fields=['sampler and SSBO driver tables explicitly unsupported'],
        status='static-final-host-loader-guard-contract-generated-without-execution')
    if regular(component/'SOURCE.lock.json',16*1024**2)!=lock_raw or regular(host_receipt,32*1024**2)!=receipt_raw:
        raise ValueError('loader producer closure changed')
    target=output/'base_v0_loader_contract.json';target.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    for name in ('rewrite','guest','host-binary','host-receipt','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(argv)
    try:result=generate(args.rewrite,args.guest,args.host_binary,args.host_receipt,args.output)
    except (OSError,ValueError,KeyError,TypeError) as error:parser.exit(1,'loader contract preparation failed: '+str(error)+'\n')
    print(json.dumps({k:result[k] for k in ('status','normal_gate_count','selected_force_entries','force_relocation_count','installable','host_guest_gpu_executed')},sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
