"""Full frozen preload on owned file operands; no signing, dlopen or Guest."""
from pathlib import Path
import contextlib,hashlib,io,json,os,struct,subprocess,sys,tempfile,types,unittest

ROOT=Path(__file__).resolve().parents[1];C=ROOT/'experiments/nxnative_base_v0'
TOOL=C/'tools/verify_base_v0_bundle_preload.py'

def load(path):
    module=types.ModuleType('owned_'+path.stem);module.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__);return module

def signature_envelope(raw,size=128):
    """Owned structural fixture, deliberately no valid cryptographic signature."""
    data=bytearray(raw);count,length=struct.unpack_from('<II',data,16);end=32+length
    assert end+16<=16384
    offset=(len(data)+15)&~15;blob=b'OWNED-NOT-A-SIGNATURE'.ljust(size,b'!')
    data.extend(b'\0'*(offset-len(data))+blob)
    struct.pack_into('<IIII',data,end,0x1d,16,offset,len(blob))
    struct.pack_into('<II',data,16,count+1,length+16)
    command=32
    for _ in range(count):
        kind,n=struct.unpack_from('<II',data,command)
        if kind==0x19 and data[command+8:command+24].rstrip(b'\0')==b'__LINKEDIT':
            start=struct.unpack_from('<Q',data,command+40)[0];span=len(data)-start
            struct.pack_into('<Q',data,command+32,(span+16383)&~16383)
            struct.pack_into('<Q',data,command+48,span)
        command+=n
    return bytes(data)

class BundlePreloadVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TOOL.is_file():return
        cls.temp=tempfile.TemporaryDirectory(prefix='owned-preload-verifier-');cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name).resolve();cls.tool=load(TOOL)
        runtime=load(C/'tools/build_base_v0_runtime.py');cls.base=load(C/'tools/build_link_only.py')
        header,_=runtime.render_header((C/runtime.PROFILE).read_bytes(),json.loads((C/'profile/base_v0.json').read_bytes()),(C/'upstream/dm/games/game.py').read_bytes(),cls.work)
        cls.receipt={'steps':[]};cls.sdk=cls.tool.query_toolchain(cls.work,cls.base,cls.receipt)
        cls.probe=cls.tool.compile_probe(C,cls.work,header,cls.sdk,cls.base,cls.receipt)
        cls.fixture_module=load(ROOT/'tests/test_nxnative_base_v0_loader_guard.py')

    def setUp(self):
        self.assertTrue(TOOL.is_file(),'full frozen preload verifier CLI is absent')
        fixture=self.fixture_module.BaseV0LoaderGuard('test_actual_guard_positive_complete_sequence')
        fixture.work=self.work;fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.bundle=fixture.bundle;self.guest=self.bundle/'Frameworks/Guest.framework/Guest'

    def run_probe(self,expected=0):
        result=subprocess.run([str(self.probe),str(self.bundle)],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,expected,result.stdout+result.stderr)

    def test_original_and_signature_envelopes_keep_same_canonical_payload(self):
        original=self.guest.read_bytes();self.run_probe()
        for size in (128,50000):
            changed=signature_envelope(original,size)
            self.assertNotEqual(hashlib.sha256(original).hexdigest(),hashlib.sha256(changed).hexdigest())
            self.guest.write_bytes(changed);self.run_probe()

    def test_signed_style_payload_mutation_still_refuses(self):
        changed=bytearray(signature_envelope(self.guest.read_bytes()));changed[16384]^=1
        self.guest.write_bytes(changed);self.run_probe(1)

    def test_missing_empty_table_still_refuses(self):
        (self.bundle/'dm/subsdk0.gates.tsv').unlink();self.run_probe(1)

    def test_cli_rejects_signing_and_execution_inputs(self):
        for extra in (['--sign','identity'],['--run'],['--host','artifact'],['--compiler','arbitrary']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                self.tool.parse_args(['--bundle',str(self.bundle),'--output',str(self.work/'out'),*extra])

    def test_output_cannot_enter_or_alias_bundle(self):
        alias=self.work/'bundle-alias';alias.symlink_to(self.bundle,target_is_directory=True);self.addCleanup(alias.unlink)
        for path in (self.bundle/'audit',alias/'audit',self.bundle.parent):
            with self.assertRaises((ValueError,FileExistsError)):
                self.tool.prepare_output(path,C,self.bundle)

    def test_unfrozen_verifier_refuses_before_compiler_query(self):
        fake=self.work/'component';fake.mkdir();(fake/'SOURCE.lock.json').write_text('{"adapter_files":{}}')
        with self.assertRaises(ValueError):self.tool.load_helpers(fake)

    def test_source_fifo_refuses_in_bounded_owned_child(self):
        fifo=self.bundle/'source-fifo';os.mkfifo(fifo)
        script='''from pathlib import Path
import sys,types
p=Path(sys.argv[1]);m=types.ModuleType("owned");m.__file__=str(p)
exec(compile(p.read_bytes(),str(p),"exec"),m.__dict__)
try:m.regular(Path(sys.argv[2]))
except ValueError:sys.exit(0)
sys.exit(91)
'''
        p=subprocess.run([sys.executable,'-c',script,str(TOOL),str(fifo)],capture_output=True,text=True,timeout=1.2)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

if __name__=='__main__':unittest.main()
