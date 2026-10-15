"""Base-v0 pool ABI and bounded CPU entrypoints; no GPU/device initialization."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = ROOT / 'native/botw_graphics'


@unittest.skipUnless(shutil.which('clang'), 'C11 compiler required')
class MemoryPoolPurePolicyTests(unittest.TestCase):
    def test_native_flags_kind_and_mapping_ranges(self):
        header = GRAPHICS / 'pool_policy.h'
        self.assertTrue(header.is_file(), 'typed pool policy is not implemented')
        source = r'''
#include "pool_policy.h"
#include <stdio.h>
#include <string.h>
#define CHECK(x) do{if(!(x)){fprintf(stderr,"policy line%d: %s\n",__LINE__,#x);return 91;}}while(0)
int main(void){
 BWPoolPolicy p=bw_pool_policy();
 CHECK(p.cpuBackingAlignment==4096&&p.ordinarySizeAlignment==4096&&p.mappingPageBytes==65536);
 CHECK(p.preferredTokenLimit==(UINT64_C(1)<<40));
 CHECK(sizeof(NVNmappingRequest)==40&&offsetof(NVNmappingRequest,physicalOffset)==8&&offsetof(NVNmappingRequest,virtualOffset)==16&&offsetof(NVNmappingRequest,size)==24&&offsetof(NVNmappingRequest,storageClass)==32);
 unsigned cpu[]={1,2,4},gpu[]={8,16,32};unsigned legal[60],n=0;
 for(unsigned extra=0;extra<4;extra++){
  for(unsigned c=0;c<3;c++)for(unsigned g=0;g<3;g++)legal[n++]=cpu[c]|gpu[g]|(extra<<6);
  for(unsigned c=0;c<3;c++)legal[n++]=0x100|cpu[c]|8|(extra<<6);
  for(unsigned g=0;g<3;g++)legal[n++]=0x200|1|gpu[g]|(extra<<6);
 }
 CHECK(n==60);
 for(unsigned flags=0;flags<1024;flags++){int want=0;for(unsigned i=0;i<n;i++)if(legal[i]==flags)want=1;CHECK(bw_pool_flags_valid(flags)==want);}
 CHECK(!bw_pool_flags_valid(0x400|0x21)&&!bw_pool_flags_valid(0xffffffffu));
 CHECK(bw_pool_kind(0x22)==BW_POOL_ORDINARY&&bw_pool_kind(0x189)==BW_POOL_PHYSICAL&&bw_pool_kind(0x221)==BW_POOL_VIRTUAL&&bw_pool_kind(0x321)==BW_POOL_INVALID);
 CHECK(bw_pool_initialization_valid(&p,0x22,(void*)(uintptr_t)0x1000,4096));
 CHECK(!bw_pool_initialization_valid(&p,0x22,(void*)(uintptr_t)0x1001,4096));
 CHECK(bw_pool_initialization_valid(&p,0x189,(void*)(uintptr_t)0x1000,65536));
 CHECK(!bw_pool_initialization_valid(&p,0x189,(void*)(uintptr_t)0x1000,4096));
 CHECK(!bw_pool_initialization_valid(&p,0x189,NULL,65536));
 CHECK(bw_pool_initialization_valid(&p,0x221,NULL,65536));
 CHECK(!bw_pool_initialization_valid(&p,0x221,(void*)(uintptr_t)0x1000,65536));
 CHECK(!bw_pool_initialization_valid(&p,0x221,NULL,0));
 CHECK(!bw_pool_initialization_valid(&p,0x189,(void*)(uintptr_t)(UINTPTR_MAX-4095),65536));
 NVNmappingRequest r={(NVNmemoryPool*)(uintptr_t)0x1234,0,0,65536,0x100,0};
 CHECK(bw_pool_mapping_request_valid(&p,&r,131072,65536,0x189));
 unsigned kind=99;CHECK(bw_pool_storage_class_kind(0x100,&kind)&&kind==0);CHECK(bw_pool_storage_class_kind(0x1fe,&kind)&&kind==0xfe);
 r.storageClass=0x1fe;CHECK(bw_pool_mapping_request_valid(&p,&r,131072,65536,0x189));
 int bad[]={0,1,0xfe,0x101,0x1ff,0x200,-1};
 for(unsigned i=0;i<sizeof bad/sizeof *bad;i++){kind=99;r.storageClass=bad[i];CHECK(!bw_pool_mapping_request_valid(&p,&r,131072,65536,0x189));CHECK(!bw_pool_storage_class_kind(bad[i],&kind)&&kind==99);}
 r.storageClass=0x100;r.physicalOffset=4096;CHECK(!bw_pool_mapping_request_valid(&p,&r,131072,65536,0x189));
 r.physicalOffset=0;r.virtualOffset=4096;CHECK(!bw_pool_mapping_request_valid(&p,&r,131072,65536,0x189));
 r.virtualOffset=65536;r.size=131072;CHECK(!bw_pool_mapping_request_valid(&p,&r,131072,65536,0x189));
 r.virtualOffset=0;r.size=65536;CHECK(!bw_pool_mapping_request_valid(&p,&r,131072,65536,0x22));
 r.physicalPool=NULL;r.storageClass=-1;CHECK(bw_pool_mapping_request_valid(&p,&r,131072,0,0));
 r.physicalOffset=65536;CHECK(!bw_pool_mapping_request_valid(&p,&r,131072,0,0));
 r.physicalOffset=0;r.virtualOffset=UINT64_MAX-65535;r.size=131072;CHECK(!bw_pool_mapping_request_valid(&p,&r,UINT64_MAX,0,0));
 CHECK(!bw_pool_mapping_request_valid(&p,NULL,131072,65536,0x189));
 CHECK(!bw_pool_storage_class_kind(0x100,NULL));
 puts("POOL_TYPED_FLAGS_PAGES_KINDS_MAPPING_BOUNDS_OK");return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='botw-pool-policy-') as directory:
            directory = Path(directory)
            unit, executable = directory / 'policy.c', directory / 'policy'
            unit.write_text(source)
            built = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                    '-O1', '-g', '-fsanitize=address,undefined',
                                    '-I', str(GRAPHICS), str(unit), '-o', str(executable)],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([executable], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('POOL_TYPED_FLAGS_PAGES_KINDS_MAPPING_BOUNDS_OK', result.stdout)
            self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
            self.assertNotIn('runtime error:', result.stderr)


@unittest.skipUnless(platform.system() == 'Darwin' and shutil.which('xcrun'),
                     'Darwin public frameworks required; no GPU is used')
class MemoryPoolCPUEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='botw-pool-cpu-')
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        source = work / 'pool.m'
        source.write_text(f'#include "{GRAPHICS / "graphics.m"}"\n'
                          f'#include "{GRAPHICS / "tools/probe_memory_pool_policy.c"}"\n')
        cls.executable = work / 'pool'
        built = subprocess.run(['xcrun', 'clang', '-std=c11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                                '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                                '-fsanitize=address,undefined', str(source),
                                '-framework', 'Foundation', '-framework', 'Metal',
                                '-framework', 'QuartzCore', '-o', str(cls.executable)],
                               capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_mode(self, mode, marker):
        result = subprocess.run([self.executable, mode], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(marker, result.stdout)
        self.assertNotIn('ERROR: AddressSanitizer', result.stderr)
        self.assertNotIn('runtime error:', result.stderr)

    def test_mapping_request_is_40_bytes(self):
        self.run_mode('abi', 'POOL_REQUEST_ABI_40_OK')

    def test_page_query_uses_nvn_mapping_policy_without_gpu(self):
        self.run_mode('query', 'POOL_PAGE_QUERY_CPU_ONLY_65536_OK')

    def test_defaults_preserve_device_tail_and_set_flags21(self):
        self.run_mode('defaults', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_touch_only_original_write_span_at_guard_page(self):
        self.run_mode('bounded', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_validate_original_write_span_once(self):
        self.run_mode('validator', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_fail_before_writing_when_validator_denies(self):
        self.run_mode('validator-deny', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_reject_actual_readonly_memory(self):
        self.run_mode('readonly', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_reject_actual_noaccess_memory(self):
        self.run_mode('none', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_reject_null_builder(self):
        self.run_mode('null', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_reject_address_wrap_before_access(self):
        self.run_mode('overflow', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_check_entire_write_span_before_first_store(self):
        self.run_mode('partial-readonly', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_set_device_storage_flags_then_defaults_order(self):
        self.run_mode('setter-order', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_defaults_do_not_read_device_field_on_inaccessible_page(self):
        self.run_mode('unreadable-device', 'POOL_DEFAULTS_CPU_BOUNDED')

    def test_map_virtual_rejects_missing_pool_before_gpu(self):
        self.run_mode('invalid-map', 'POOL_INVALID_MAPPING_ABI_REJECTED_BEFORE_GPU_OK')

    def test_physical_invalid_backing_rejects_before_allocation(self):
        self.run_mode('physical-invalid-backing', 'POOL_INVALID_MAPPING_ABI_REJECTED_BEFORE_GPU_OK')

    def test_virtual_nonnull_backing_rejects_before_allocation(self):
        self.run_mode('virtual-invalid-backing', 'POOL_INVALID_MAPPING_ABI_REJECTED_BEFORE_GPU_OK')
