"""Signed-style owned loader operands only; no dlopen, Host or Guest execution."""
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest

ROOT=Path(__file__).resolve().parents[1];C=ROOT/'experiments/nxnative_base_v0'
H=C/'adapter/base_v0_loader_guard.h';M=C/'adapter/base_v0_loader_guard.m'
PATCH=C/'patches/base-v0-normal-loader-guard.patch'
NAMES=('main','subsdk0','subsdk1','subsdk2','subsdk3','sdk')
ENTRY=('_init_libc0','nnosInitialize','_init_libc1','nninitInitializeSdkModule','nninitStartup','_init_libc2','_ZN2nn2os11SetHostArgcEi','_ZN2nn2os11SetHostArgvEPPc','nnMain')

def load(path):
    m=types.ModuleType('owned_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m

def sha(raw):return hashlib.sha256(raw).hexdigest()

PROBE=r'''
#include "base_v0_loader_guard.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* These are CPU ports for complete original force/req functions; never called
 * through the arbitrary implementation pointer values. */
typedef struct {char name[16];unsigned char *base;} module_t;
typedef nxnative_base_v0_loader_force hle_force_ent;
static struct {int imports;} g_flag;
static int _mh_execute_header;
static hle_force_ent owned_entries[]={{"ownedForce",(void*)1,"owned reason"},{"notSelected",(void*)2,"unused reason"}};
static unsigned wraps;
static const void *getsectiondata(const void *h,const char *seg,const char *sec,unsigned long *size){(void)h;(void)seg;(void)sec;*size=sizeof owned_entries;return owned_entries;}
void *hlecount_wrap(const char *type,const char *name,void *fn){(void)type;(void)name;wraps++;return fn;}
#define LOG(...) ((void)0)
#define die(...) exit(71)
#include "actual_force.inc"
static unsigned char owned_image[0x100];
static module_t owned_module={"main",owned_image};
static unsigned symbol_offset;
static void *guest_sym(const char *name){(void)name;return owned_image+symbol_offset;}
static module_t *module_of(const void *p){(void)p;return &owned_module;}
#include "actual_req.inc"
static const char *names[]={"main","subsdk0","subsdk1","subsdk2","subsdk3","sdk"};
static const char *entries[]={"_init_libc0","nnosInitialize","_init_libc1","nninitInitializeSdkModule","nninitStartup","_init_libc2","_ZN2nn2os11SetHostArgcEi","_ZN2nn2os11SetHostArgvEPPc","nnMain"};
static const char *ids[]={"2094f152836683a3632c6153fd84ab80","9f7e69a5c5e8aa0e61199ac33dd0d2d1","fc3d9aae8b6769e2d69961c005e239f5","2ba5fcc00ba28cdda26c792e934949cb","aa72f973a902c24747af4522ee67ea9a","99d55fb2a2ce9e14bd5d56428ec0397b"};
#define CHECK(x) do{if(!(x)){fprintf(stderr,"owned loader line %d\n",__LINE__);return 91;}}while(0)
int main(int argc,char **argv){CHECK(argc==3);int mode=atoi(argv[2]);char dm[4096];snprintf(dm,sizeof dm,"%s/dm",argv[1]);
 int pre=nxnative_base_v0_loader_preload(argv[1]);if(mode==1)return pre?0:91;CHECK(!pre);
 if(mode==2){const char *bad[]={"main","sdk","subsdk1","subsdk2","subsdk3","subsdk0"};CHECK(nxnative_base_v0_loader_begin(dm,bad,6));return 0;}
 CHECK(!nxnative_base_v0_loader_begin(dm,names,6));
 unsigned keys[]={0,1,2,3,4,5,6,7};uint64_t layout[]={0x100,0x4000,0x4000,0x8000,0x4000,0x4000};
 for(unsigned i=0;i<6;i++){if(mode==3&&i==0)layout[3]+=0x4000;
  int e=nxnative_base_v0_loader_layout(names[i],layout,keys,ids[i]);if(mode==3){CHECK(e);return 0;}CHECK(!e);}
 for(unsigned i=0;i<6;i++){unsigned char pages[3][0x4000];for(unsigned p=0;p<3;p++)memset(pages[p],16+i,sizeof pages[p]);
  if(mode==4&&i==0)pages[0][0]^=1;const void *parts[]={pages[0],pages[1],pages[2]};size_t lengths[]={0x4000,0x4000,0x4000};
  int e=nxnative_base_v0_loader_image(names[i],parts,lengths);if(mode==4){CHECK(e);return 0;}CHECK(!e);
  CHECK(!nxnative_base_v0_loader_gate_count(names[i],i==0?93:i==5?137:0));}
 if(mode>=20){
  if(mode==21)owned_entries[0].why="wrong reason";if(mode==22)owned_entries[0].fn=NULL;
  force_init();module_t m={"main",NULL};CHECK(force_lookup(&m,"ownedForce")== (void*)1);CHECK(force_lookup(&m,"ownedForce")== (void*)1);
  CHECK(!force_lookup(&m,"notSelected"));strcpy(m.name,"sdk");CHECK(force_lookup(&m,"ownedForce")== (void*)1);CHECK(wraps==3);force_check();
  for(unsigned i=0;i<9;i++){symbol_offset=4*(i+1)+(mode==23?4:0);CHECK(req(entries[i])==owned_image+symbol_offset);}return 0;}
 nxnative_base_v0_loader_force actual[]={{"ownedForce",(void*)1,"owned reason"},{"notSelected",(void*)2,"unused reason"}};uint8_t selected[2]={77,88};
 if(mode==5)actual[0].why="changed reason";if(mode==6)actual[0].fn=NULL;
 int e=nxnative_base_v0_loader_registry(actual,2,selected);if(mode==5||mode==6){CHECK(e&&selected[0]==77&&selected[1]==88);return 0;}CHECK(!e&&selected[0]&&!selected[1]);
 CHECK(!nxnative_base_v0_loader_reference("main","ownedForce"));CHECK(!nxnative_base_v0_loader_reference("main","ownedForce"));
 if(mode==7){CHECK(nxnative_base_v0_loader_reference("main","ownedForce"));return 0;}
 if(mode==8){CHECK(nxnative_base_v0_loader_complete());return 0;}
 CHECK(!nxnative_base_v0_loader_reference("sdk","ownedForce"));CHECK(!nxnative_base_v0_loader_complete());
 for(unsigned i=0;i<9;i++){e=nxnative_base_v0_loader_entry(entries[i],"main",4*(i+1)+(mode==9?4:0));if(mode==9){CHECK(e);return 0;}CHECK(!e);}
 CHECK(nxnative_base_v0_loader_begin(dm,names,6));return 0;}
'''

class BaseV0LoaderGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not M.is_file() or not H.is_file():return
        cls.temp=tempfile.TemporaryDirectory(prefix='nx-loader-owned-');cls.addClassCleanup(cls.temp.cleanup);cls.work=Path(cls.temp.name).resolve()
        tool=load(C/'tools/build_base_v0_runtime.py');identity=json.loads((C/'profile/base_v0.json').read_bytes())
        header,_=tool.render_header((C/tool.PROFILE).read_bytes(),identity,(C/'upstream/dm/games/game.py').read_bytes(),cls.work)
        base=load(C/'tools/build_link_only.py');adapted=load(C/'tools/build_adapted_link_only.py')
        # Keep this independent guard fixture on its fixed eight-patch
        # predecessor as the production runtime builder gains later adapters.
        adapted.PATCHES=(*adapted.PATCHES,
            ('patches/base-v0-logical-memory.patch',('dm/host/hle_svc.c','dm/host/main.c')),
            ('patches/base-v0-normal-api-contracts.patch',('dm/host/hle_account.c','dm/gfx/hle_vi.c',
                'dm/gfx/nvn/hle_nvn.c','dm/gfx/nvn/nvn_mtl.m')))
        private,_=adapted.copy_private_inputs(C,cls.work,base.read_json(C/'SOURCE.lock.json'),base,tool.new_receipt())
        cls.native=private/'upstream'
        patched=subprocess.run(['patch','--fuzz=0','--batch','-p1','-i',str(PATCH)],cwd=cls.native,capture_output=True,text=True)
        assert patched.returncode==0,patched.stdout+patched.stderr
        loader=(cls.native/'dm/host/loader.c').read_text();first=loader.index('static const hle_force_ent *g_force;');last=loader.index('// Read the rewriter table header',first)
        (cls.work/'actual_force.inc').write_text(loader[first:last])
        main=(cls.native/'dm/host/main.c').read_text();first=main.index('static void *req(');last=main.index('// Game arguments:',first)
        (cls.work/'actual_req.inc').write_text(main[first:last])
        (cls.work/'game_profile.h').write_text(header);(cls.work/'probe.c').write_text(PROBE);cls.binary=cls.work/'owned-loader'
        command=['clang','-DNXNATIVE_BASE_V0_LOADER_GUARD=1','-DNXNATIVE_NORMAL_POLICY=1','-DNXNATIVE_BASE_V0_MEMORY=1','-fobjc-arc','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(C/'adapter'),'-I'+str(cls.work),str(cls.work/'probe.c'),str(M),'-framework','Foundation','-o',str(cls.binary)]
        p=subprocess.run(command,capture_output=True,text=True);assert p.returncode==0,p.stdout+p.stderr

    def setUp(self):
        self.assertTrue(M.is_file() and H.is_file(),'normal loader positive guard is absent')
        self.bundle=Path(tempfile.mkdtemp(prefix='bundle-',dir=self.work));self.addCleanup(shutil.rmtree,self.bundle)
        dm=self.bundle/'dm';dm.mkdir();identity=json.loads((C/'profile/base_v0.json').read_bytes());modules=[];images=[];tables={}
        for i,name in enumerate(NAMES):
            layout=dict(zip(('TEXT_END','RO_OFF','DATA_OFF','PATCH_OFF','KEY_PAGE_OFF','KEY_PAGE_SIZE'),(0x100,0x4000,0x4000,0x8000,0x4000,0x4000)))
            layout.update({'KEY_'+key+'_IDX':k for k,key in enumerate(('K18','KTLS','KTLR','KGATE','KRES0','KRES1','KASPECT','KASPECTUI'))})
            image=bytes([16+i])*0xc000;images.append({'name':name,'image':image,'image_sha256':sha(image),'layout':layout})
            modules.append({'name':name,'source':identity['modules'][name],'layout':layout,'padded_bytes':len(image),'image_bytes':len(image),'padded_sha256':sha(image),'image_sha256':sha(image),'section_sizes':[0x4000]*3,'normal_gate_count':93 if i==0 else 137 if i==5 else 0})
            for kind in ('fixups','gates','cuts','params','obs'):
                raw=((''.join(f'0x{0x8000+4*j:x}\tsvc\t1\t0x44\n' for j in range(modules[-1]['normal_gate_count']))) if kind=='gates' else '# owned exact table\n').encode()
                member=name+'.'+kind+'.tsv';(dm/member).write_bytes(raw);tables[member]={'bytes':len(raw),'sha256':sha(raw)}
        # Complete original generic emitter: data-only owned container, never loaded.
        pack=load(C/'tools/pack_guest.py');guest=pack.emit_framework(images);f=self.bundle/'Frameworks/Guest.framework';f.mkdir(parents=True);(f/'Guest').write_bytes(guest)
        self.contract={'schema':'nxnative-base-v0-loader-guard-contract-v1','profile_id':'botw-switch-base-v0','title_id':'01007EF00011E000','game_version':'1.0.0','nvn_api':{'major':52,'minor':32},'original_heap_bytes':0xc2600000,'original_root_bytes':0xc0600000,'module_order':list(NAMES),'rtld_loaded':False,'normal_gate_count':230,'modules':modules,'loader_tables':tables,'force_registry':{'count':2},'force_rules':[{'name':'ownedForce','why':'owned reason','selected':True,'references':{'main':2,'sdk':1}},{'name':'notSelected','why':'unused reason','selected':False,'references':{}}],'entry_symbols':{n:{'module':'main','offset':4*(i+1),'binding':1,'visibility':0,'bytes':4,'symbol_index':i+1} for i,n in enumerate(ENTRY)}}
        self.write()

    def write(self):(self.bundle/'dm/base_v0_loader_contract.json').write_text(json.dumps(self.contract,sort_keys=True))
    def case(self,mode=0,timeout=3,expected=0):
        p=subprocess.run([str(self.binary),str(self.bundle),str(mode)],capture_output=True,text=True,timeout=timeout);self.assertEqual(p.returncode,expected,p.stdout+p.stderr)
    def test_actual_guard_positive_complete_sequence(self):self.case()
    def test_original_contract_identity_and_30_tables_are_required(self):
        for mutate in (lambda c:c.update(game_version='1.9.0'),lambda c:c['loader_tables'].pop('sdk.gates.tsv'),lambda c:c['modules'][0]['source'].update(build_id='0'*64),lambda c:c.update(normal_gate_count=137)):
            old=copy.deepcopy(self.contract);mutate(self.contract);self.write();self.case(1);self.contract=old
    def test_wrong_json_member_types_refuse_without_objc_exception(self):
        for mutate in (lambda c:c['modules'].__setitem__(0,None),lambda c:c.update(force_rules=[None]),lambda c:c.update(entry_symbols=[])):
            old=copy.deepcopy(self.contract);mutate(self.contract);self.write();self.case(1);self.contract=old
    def test_real_table_mutation_and_missing_gate_are_rejected(self):
        p=self.bundle/'dm/main.gates.tsv';p.write_bytes(p.read_bytes()+b'0x8000\tobs\t1\t0x44\n');self.case(1)
    def test_guest_section_payload_checked_before_any_loader_call(self):
        p=self.bundle/'Frameworks/Guest.framework/Guest';raw=bytearray(p.read_bytes());raw[0x4000]^=1;p.write_bytes(raw);self.case(1)
    def test_live_layout_order_images_and_actual_registry_failures(self):
        for mode in (2,3,4,5,6):self.case(mode)
    def test_actual_resolver_reference_counts_and_entry_offsets(self):
        for mode in (7,8,9):self.case(mode)
    def test_complete_original_force_and_req_follow_selected_actual_branch(self):
        self.case(20)
        for mode in (21,22,23):self.case(mode,expected=71)
    def test_fifo_and_parent_alias_refuse_without_waiting(self):
        p=self.bundle/'dm/main.gates.tsv';p.unlink();os.mkfifo(p);self.case(1,timeout=1.2)
    def test_real_directory_alias_is_rejected_before_guest_load(self):
        p=self.bundle/'dm';other=self.bundle/'metadata';p.rename(other);p.symlink_to(other,target_is_directory=True);self.case(1,timeout=1.2)
    def test_real_missing_empty_gate_table_is_not_silently_accepted(self):
        (self.bundle/'dm/subsdk0.gates.tsv').unlink();self.case(1)
    def test_data_only_guest_rejects_initializer_load_command(self):
        p=self.bundle/'Frameworks/Guest.framework/Guest';raw=bytearray(p.read_bytes());ncmds=struct.unpack_from('<I',raw,16)[0];off=32
        for _ in range(ncmds):
            kind,size=struct.unpack_from('<II',raw,off)
            if kind==0x1b:struct.pack_into('<I',raw,off,0x1a);break
            off+=size
        else:self.fail('original complete emitter UUID command is absent')
        p.write_bytes(raw);self.case(1)
    def test_current_registry_source_binding_preserves_strict_admission(self):
        producer=load(C/'tools/generate_base_v0_loader_contract.py')
        source=b'''REGISTRY_SHA='old'
def admit(registry):
 if registry['count'] != 614 or registry['canonical_metadata_sha256'] != REGISTRY_SHA:raise ValueError('registry mismatch')
 return registry['count']
'''
        current={'count':2,'canonical_metadata_sha256':'a'*64}
        module,binding=producer.bind_current_registry(source,C/'tools/generate_base_v0_contract.py',current)
        self.assertEqual(module.admit(current),2)
        for bad in ({**current,'count':614},{**current,'canonical_metadata_sha256':'b'*64}):
            with self.assertRaises(ValueError):module.admit(bad)
        with self.assertRaises(ValueError):producer.bind_current_registry(source.replace(b'!= 614',b'!= 615'),C/'tools/generate_base_v0_contract.py',current)
        self.assertNotEqual(binding['original_source_sha256'],binding['adapted_source_sha256'])
    def test_unfrozen_producer_refuses_before_creating_any_output(self):
        producer=load(C/'tools/generate_base_v0_loader_contract.py');source=self.bundle/'source';source.mkdir()
        (source/'SOURCE.lock.json').write_text('{"adapter_files":{}}');out=self.bundle/'out'
        with self.assertRaises(ValueError):producer.generate(self.bundle,self.bundle,self.bundle/'absent',self.bundle/'receipt',out,source)
        self.assertFalse(out.exists())
    def test_patch_hooks_original_loader_not_a_parallel_resolver(self):
        self.assertTrue(PATCH.is_file());raw=PATCH.read_text()
        self.assertIn('force_lookup(m,',raw);self.assertIn('nxnative_base_v0_loader_reference',raw)
        self.assertIn('nxnative_base_v0_loader_preload',raw);self.assertIn('nxnative_base_v0_loader_entry',raw)

if __name__=='__main__':unittest.main()
