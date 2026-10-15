"""Synthetic container fixtures only; no game shaders or resources."""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).parent))
from test_botw_romfs import fixture
from test_botw_graphics import synthetic_bnsh
try:
    from botw_native.resource_shaders import read_sarc, decompress_yaz0, scan_romfs, catalogue_romfs
except ModuleNotFoundError:
    read_sarc = decompress_yaz0 = scan_romfs = catalogue_romfs = None

try:
    from botw_native.resource_shaders import parse_sharcb_v9
except ImportError:
    parse_sharcb_v9 = None


def sharcb_v9(geometry=False):
    string_base, string_size = 336, 256
    strings = bytearray(b'fixture\0'); names = {}
    for name in ['Camera','inPos','Proj','albedo']:
        names[name] = len(strings); strings += name.encode()+b'\0'
    base = string_base + string_size; records = bytearray(); locations = []
    types = [0,1,2] if geometry else [0,1]
    first_control = None
    for index, kind in enumerate(types):
        start = base + 8 + len(records); q = start + 16
        control_bytes = bytearray(2000)
        struct.pack_into('<I',control_bytes,0,0x98761234)
        struct.pack_into('<I',control_bytes,20,len(control_bytes))
        struct.pack_into('<II',control_bytes,0x700,256,512)
        own = index == 0
        record = bytearray(88)
        if own: first_control = start+88; record += control_bytes
        block = len(record);record += struct.pack('<QiI',names['Camera'],2,64)
        attr = len(record);record += struct.pack('<Qi',names['inPos'],0)
        uniform = len(record);record += struct.pack('<QI',names['Proj'],16)
        sampler = len(record);record += struct.pack('<Qi',names['albedo'],3)
        record += bytes(-len(record)%8)
        struct.pack_into('<4I',record,0,len(record),kind,0,len(record)-16)
        struct.pack_into('<QI',record,16,index*512,2000)
        struct.pack_into('<iI',record,28,first_control-q,0 if own else 0xdeadbeef)
        for cp,op,where in [(20,24,block),(32,36,attr),(44,48,uniform),(56,60,sampler)]:
            struct.pack_into('<I',record,16+cp,1)
            struct.pack_into('<II',record,16+op,where-16,0x12345678 if index else 0)
        locations.append(start); records += record
    program_name = b'draw\0';program = struct.pack('<4I',16+len(program_name),len(program_name),7 if geometry else 3,0)+program_name
    table = struct.pack('<II',8+len(program),1)+program
    metadata = struct.pack('<II',8+len(records),len(types))+records+table
    pool_offset = (base+len(metadata)+4095)&~4095
    image=bytearray(pool_offset+len(types)*512)
    struct.pack_into('<8I',image,0,0x53484142,9,len(image),5,0,string_size,len(types)*512,pool_offset)
    image[string_base:string_base+len(strings)]=strings;image[base:base+len(metadata)]=metadata
    for index,kind in enumerate(types):
        off=pool_offset+index*512;struct.pack_into('<I',image,off,0x12345678)
        struct.pack_into('<I',image,off+0x30,{0:1,1:5,2:4}[kind]<<10)
        image[off+256:off+512]=bytes([index+1])*256
    return bytes(image),{'base':base,'records':locations,'control':first_control,'pool':pool_offset}


def sarc(files, endian='<'):
    names, payload, rows = bytearray(), bytearray(), []
    for name, data in files:
        encoded = name.encode(); h = 0
        for b in encoded: h = (h * 101 + (b if b < 128 else b - 256)) & 0xffffffff
        rows.append((h, name, data))
    rows.sort(key=lambda r:r[0])
    nodes = bytearray()
    for h, name, data in rows:
        nodes += struct.pack(endian+'4I', h, 0x1000000 + len(names)//4, len(payload), len(payload)+len(data))
        names += name.encode()+b'\0'; names += bytes(-len(names)%4); payload += data
    data_offset = 40 + len(nodes) + len(names)
    header = b'SARC' + struct.pack(endian+'HHIIHH', 20, 0xfeff, data_offset+len(payload), data_offset, 0x100, 0)
    return header + b'SFAT' + struct.pack(endian+'HHI', 12, len(rows), 101) + nodes + b'SFNT' + struct.pack(endian+'HH', 8, 0) + names + payload


def yaz0(data):
    result = bytearray(b'Yaz0' + struct.pack('>III', len(data), 0, 0))
    for p in range(0, len(data), 8): result += b'\xff'+data[p:p+8]
    return bytes(result)


def fsha(data=None):
    data = data or synthetic_bnsh(); result = bytearray(64)+data
    result[:8] = b'FSHA    '; struct.pack_into('<I', result, 8, 0x00030001)
    result[12:16] = b'\xff\xfe\x0c\0'; struct.pack_into('<I', result, 28, len(result))
    return bytes(result)


class ResourceShaderTests(unittest.TestCase):
    def need(self): self.assertIsNotNone(read_sarc, 'Resource shader inspection is missing')

    def test_sharcb_v9_reads_declared_stage_control_code_constants_and_reflection(self):
        self.assertIsNotNone(parse_sharcb_v9,'SHARCB v9 metadata parser is missing')
        data,loc=sharcb_v9();result=parse_sharcb_v9(data)
        self.assertEqual(result['version'],9)
        self.assertEqual(result['summary']['stage_counts'],{'vertex':1,'fragment':1})
        vertex,fragment=result['variations']
        self.assertEqual(vertex['control_offset'],fragment['control_offset'])
        self.assertEqual(fragment['control_offset_upper_word'],0xdeadbeef)
        self.assertEqual(vertex['code_offset'],loc['pool'])
        self.assertEqual((vertex['code_bytes'],vertex['c1_bytes']),(512,256))
        self.assertEqual(vertex['c1_sha256'],hashlib.sha256(bytes([1])*256).hexdigest())
        self.assertEqual(vertex['reflection']['samplers'],[{'name':'albedo','location':3,'name_offset_upper_word':0}])
        self.assertEqual(result['programs'][0]['name'],'draw')
        self.assertEqual(result['programs'][0]['variation_indices'],[[0,1]])
        self.assertEqual(result['compatibility']['texture_types'],'unverified')

    def test_sharcb_v9_rejects_corrupt_offsets_aliases_stage_mismatch_and_truncation(self):
        self.assertIsNotNone(parse_sharcb_v9,'SHARCB v9 metadata parser is missing')
        data,loc=sharcb_v9();cases=[data[:-1]]
        for pos,fmt,value in [(4,'<I',8),(8,'<I',len(data)+1),(28,'<I',loc['base']), (loc['records'][1]+28,'<i',-2**31), (loc['control']+0x704,'<I',len(data)),(loc['pool']+0x30,'<I',5<<10),(loc['records'][0]+16+24,'<I',2**32-1)]:
            bad=bytearray(data);struct.pack_into(fmt,bad,pos,value);cases.append(bytes(bad))
        for index,bad in enumerate(cases):
            with self.subTest(case=index),self.assertRaises(ValueError):parse_sharcb_v9(bad)

    def test_sharcb_geometry_comes_from_declared_metadata_and_is_not_runtime_enabled(self):
        self.assertIsNotNone(parse_sharcb_v9,'SHARCB v9 metadata parser is missing')
        result=parse_sharcb_v9(sharcb_v9(geometry=True)[0])
        self.assertEqual(result['variations'][2]['stage'],'geometry')
        self.assertEqual(result['programs'][0]['variation_indices'],[[0,1,2]])
        self.assertFalse(result['compatibility']['runtime_stage_kinds'])

    def test_sharcb_rejects_name_pointers_overlaps_program_group_corruption_and_limits(self):
        self.assertIsNotNone(parse_sharcb_v9,'SHARCB v9 metadata parser is missing')
        data,loc=sharcb_v9();record=loc['records'][0];p=record+16
        table=loc['base']+struct.unpack_from('<I',data,loc['base'])[0]
        attr=p+struct.unpack_from('<I',data,p+36)[0]
        cases=[]
        for pos,fmt,value in [(12,'<I',1),(loc['base']+4,'<I',2**32-1),(record,'<I',16),
                              (p+12,'<i',76),(loc['records'][1]+24,'<I',1999),
                              (loc['records'][1]+16,'<Q',128),(p+36,'<I',72),
                              (attr,'<Q',2**32),(attr,'<Q',256),
                              (table,'<I',8),(table+8+8,'<I',7),(table+8+12,'<I',1),
                              (table+8+4,'<I',4097)]:
            bad=bytearray(data);struct.pack_into(fmt,bad,pos,value);cases.append(bytes(bad))
        bad=bytearray(data);bad[336:592]=b'x'*256;cases.append(bytes(bad))
        for index,bad in enumerate(cases):
            with self.subTest(case=index),self.assertRaises(ValueError):parse_sharcb_v9(bad)
        for limits in [{'max_records':0},{'max_symbols':0},{'max_string_bytes':0},{'max_symbols':1000001}]:
            with self.subTest(limits=limits),self.assertRaises(ValueError):parse_sharcb_v9(data,**limits)

    def test_sharcb_scan_exports_declared_slices_with_exact_archive_provenance(self):
        self.need();data,loc=sharcb_v9();pack=sarc([('Shader/sample.sharcb',yaz0(data))])
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Pack/test.pack',pack)])[0]);output=Path(tmp)/'out'
            result=scan_romfs(image,['/Pack/test.pack'],output)
            self.assertIn('sharcb',result,'SHARCB export/provenance is missing')
            archive=result['sharcb'][0]
            self.assertEqual(result['summary']['sharcb_stage_records'],2)
            self.assertEqual(result['summary']['sharcb_programs'],1)
            self.assertEqual((output/archive['file']).read_bytes(),data)
            self.assertEqual(archive['source_chain'][-1]['kind'],'sharcb')
            self.assertEqual(archive['source_chain'][-1]['sha256'],hashlib.sha256(data).hexdigest())
            self.assertIsNone(archive['source_chain'][-1]['image_offset'])
            self.assertEqual(archive['source_chain'][0]['image_offset'],512)
            self.assertTrue(any(row['kind']=='yaz0' for row in archive['source_chain']))
            for variation in archive['variations']:
                for kind in ['control','code','c1']:
                    content=(output/variation[kind+'_file']).read_bytes()
                    self.assertEqual(content,data[variation[kind+'_offset']:variation[kind+'_offset']+variation[kind+'_bytes']])
                    self.assertEqual(hashlib.sha256(content).hexdigest(),variation[kind+'_sha256'])
            self.assertEqual(archive['variations'][0]['control_file'],archive['variations'][1]['control_file'])
            self.assertEqual(result,json.loads((output/'report.json').read_text()))

    def test_sharcb_reflection_budget_and_complete_invalid_archive_never_publish(self):
        self.need();data,loc=sharcb_v9();bad=bytearray(data);struct.pack_into('<I',bad,loc['records'][0]+16+20,2**32-1)
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/shader.sharcb',data),('/bad.sharcb',bad)])[0])
            for path,budget in [('/shader.sharcb',0),('/bad.sharcb',100)]:
                output=Path(tmp)/('out'+path[1:])
                with self.assertRaises(ValueError):scan_romfs(image,[path],output,max_reflection_items=budget)
                self.assertFalse(output.exists())

    def test_sarc_reads_both_endian_variants_with_exact_member_spans(self):
        self.need()
        for endian in ('<','>'):
            data = sarc([('Shader/test.bfsha', b'FSHA123'), ('目录/a', b'value')], endian)
            rows = read_sarc(lambda off,size:data[off:off+size], len(data))
            self.assertEqual({r.name:data[r.offset:r.offset+r.size] for r in rows}, {'Shader/test.bfsha':b'FSHA123','目录/a':b'value'})

    def test_sarc_rejects_paths_duplicates_overlap_bad_hash_and_truncation(self):
        self.need(); cases = [sarc([('../escape',b'a')]), sarc([('a',b'a'),('a',b'b')])]
        original = sarc([('a',b'abc'),('b',b'def')])
        for pos,value in [(8,len(original)+1),(12,20),(32,0),(36,0),(40,100000),(48,0)]:
            bad = bytearray(original); struct.pack_into('<I',bad,pos,value); cases.append(bytes(bad))
        bad=bytearray(original);struct.pack_into('<I',bad,56,1);cases.append(bytes(bad))
        cases += [original[:19], original[:-1]]
        for index,data in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError): read_sarc(lambda off,size:data[off:off+size],len(data))

    def test_yaz0_literals_overlapping_backrefs_and_output_limit(self):
        self.need()
        self.assertEqual(decompress_yaz0(yaz0(b'example payload')), b'example payload')
        self.assertEqual(decompress_yaz0(b'Yaz0'+struct.pack('>III',9,0,0)+b'\xe0ABC\x40\x02'), b'ABCABCABC')
        self.assertEqual(decompress_yaz0(b'Yaz0'+struct.pack('>III',274,0,0)+b'\x80a\0\0\xff'), b'a'*274)
        for data in [yaz0(b'abc')[:-1], b'Yaz0'+struct.pack('>III',3,0,0)+b'\0\x10\0', b'Yaz0'+struct.pack('>III',2**30,0,0), yaz0(b'abc')+b'unknown']:
            with self.assertRaises(ValueError): decompress_yaz0(data,max_output=1024)

    def test_nested_scan_preserves_compressed_physical_and_decoded_logical_provenance(self):
        self.need()
        program = synthetic_bnsh(); archive = sarc([('Shader/sample.sbfsha',yaz0(fsha(program)))])
        outer = sarc([('System/public.ssarc',yaz0(archive)),('unrelated.bin',b'not a shader')])
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Pack/Bootup_Graphics.pack',outer)])[0])
            output=Path(tmp)/'shaders'
            report=scan_romfs(image,['/Pack/Bootup_Graphics.pack'],output)
            self.assertEqual(report['summary']['bnsh_containers'],1)
            self.assertEqual(report['summary']['stage_records'],2)
            container=report['bnsh'][0]
            self.assertEqual(container['version'],'2.1.2')
            self.assertEqual(container['stage_counts'],{'vertex':1,'fragment':1})
            self.assertEqual((output/container['file']).read_bytes(),program)
            chain=container['source_chain']
            self.assertEqual(chain[0]['romfs_path'],'/Pack/Bootup_Graphics.pack')
            self.assertEqual(chain[0]['image_offset'],512)
            self.assertEqual(chain[0]['sha256'],hashlib.sha256(outer).hexdigest())
            self.assertIsNone(chain[-1]['image_offset'])
            self.assertEqual(chain[-1]['offset'],64)
            self.assertTrue(any(row['kind']=='yaz0' for row in chain))
            self.assertEqual(report,json.loads((output/'report.json').read_text()))
            with self.assertRaises(FileExistsError): scan_romfs(image,['/Pack/Bootup_Graphics.pack'],output)

    def test_direct_member_selector_avoids_unrelated_archive_expansion(self):
        self.need()
        pack=sarc([('Shader/good.sbfsha',yaz0(fsha())),('bad.ssarc',b'Yaz0'+struct.pack('>III',2**30,0,0))])
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Pack/test.pack',pack)])[0])
            try: result=scan_romfs(image,['/Pack/test.pack::Shader/good.sbfsha'],Path(tmp)/'out')
            except (FileNotFoundError,ValueError): self.fail('Direct SARC member selectors are missing')
            self.assertEqual(result['summary']['bnsh_containers'],1)
            self.assertEqual(result['bnsh'][0]['source_chain'][1]['name'],'Shader/good.sbfsha')

    def test_non_bnsh_shader_archive_is_preserved_with_explicit_unsupported_status(self):
        self.need(); opaque=bytearray(64);opaque[:4]=b'BAHS'
        struct.pack_into('<II',opaque,4,9,len(opaque));opaque[32:36]=bytes.fromhex('34127698');opaque[40:44]=bytes.fromhex('78563412')
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/common.sharcb',bytes(opaque))])[0]);output=Path(tmp)/'out'
            result=scan_romfs(image,['/common.sharcb'],output)
            row=result['unclassified'][0]
            self.assertIn('file',row,'Opaque shader archive extraction is missing')
            self.assertEqual((output/row['file']).read_bytes(),opaque)
            self.assertEqual(row['nvn_code_magic_candidates'],1)
            self.assertEqual(row['bnsh_parser_compatible'],False)

    def test_catalogue_counts_root_formats_and_sarc_names_without_decoding_payloads(self):
        self.need()
        pack=sarc([('Shader/sample.sbfsha',yaz0(fsha())),('Model/sample.sbfres',yaz0(b'FRESdata'))])
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Pack/a.pack',pack),('/Model/a.sbfres',yaz0(b'FRESsample'))])[0])
            report=catalogue_romfs(image)
            self.assertEqual(report['file_count'],2)
            self.assertEqual(report['extensions'],{'.pack':1,'.sbfres':1})
            self.assertEqual(report['root_magics'],{'SARC':1,'Yaz0':1})
            self.assertIn('yaz0_prefix_magics',report,'Bounded compressed-prefix inventory is missing')
            self.assertEqual(report['yaz0_prefix_magics'],{'FRES':1})
            self.assertEqual(report['sarc_member_extensions'],{'.sbfres':1,'.sbfsha':1})
            self.assertEqual(len(report['shader_candidates']),1)

    def test_nesting_limit_and_unsafe_member_selector_do_not_publish_outputs(self):
        self.need(); outer=sarc([('public.ssarc',yaz0(sarc([('Shader/a.bfsha',fsha())])))])
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Pack/a.pack',outer)])[0])
            for specification, depth in [('/Pack/a.pack',1),('/Pack/a.pack::../escape',8)]:
                output=Path(tmp)/('out'+str(depth))
                with self.assertRaises(ValueError):scan_romfs(image,[specification],output,max_depth=depth)
                self.assertFalse(output.exists())

    def test_reflection_expansion_budget_is_checked_before_building_large_metadata(self):
        self.need(); data=bytearray(synthetic_bnsh()+bytes(0x400))
        struct.pack_into('<I',data,0x1c,len(data));struct.pack_into('<Q',data,0x178,0x600)
        struct.pack_into('<Q',data,0x620,0x640);struct.pack_into('<Q',data,0x650,0x700)
        struct.pack_into('<IIIIQ',data,0x668,0,0,1,1,0x780)
        data[0x700:0x704]=b'_DIC';struct.pack_into('<I',data,0x704,1)
        struct.pack_into('<Q',data,0x720,0x800);struct.pack_into('<H',data,0x800,5)
        data[0x802:0x807]=b'image';struct.pack_into('<i',data,0x780,3)
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Shader.bnsh',bytes(data))])[0]);output=Path(tmp)/'out'
            try:
                with self.assertRaises(ValueError):scan_romfs(image,['/Shader.bnsh'],output,max_reflection_items=0)
            except TypeError:self.fail('Reflection expansion budgeting is missing')
            self.assertFalse(output.exists())

    def test_invalid_bnsh_and_expansion_budget_never_publish_partial_outputs(self):
        self.need()
        bad=bytearray(synthetic_bnsh());struct.pack_into('<I',bad,28,len(bad)+100)
        for data,budget in [(yaz0(fsha(bytes(bad))),4096),(yaz0(fsha()),32)]:
            with tempfile.TemporaryDirectory() as tmp:
                image=Path(tmp)/'romfs.bin';image.write_bytes(fixture([('/Shader.sbfsha',data)])[0]);output=Path(tmp)/'out'
                with self.assertRaises(ValueError):scan_romfs(image,['/Shader.sbfsha'],output,max_expanded_bytes=budget)
                self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
