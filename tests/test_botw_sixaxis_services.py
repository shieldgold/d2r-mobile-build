"""HID Start/Stop use actual kernel shared-memory aliases and input producers."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_botw_hid_shared as shared


ROOT = Path(__file__).resolve().parents[1]
# Reuse only the existing actual-kernel transport setup, not its tests or its
# DebugPad-specific cases. This keeps one implementation of SVC13/14 adapters.
FIXTURE = shared.FIXTURE.split('static void empty_debug', 1)[0] + r'''
static const unsigned offsets[6]={0x1758,0x1e60,0x2568,0x2c70,0x3378,0x3a80};
static const unsigned styles[6]={3,4,5,5,6,7}, sensors[6]={2,2,0,1,0,1};
static unsigned domain_id;
static unsigned char *six(unsigned slot,unsigned which) {
 return (unsigned char *)(uintptr_t)(alias+BOTW_HID_NPAD_OFFSET+slot*BOTW_HID_NPAD_STRIDE+offsets[which]);
}
static uint32_t sensor_handle(unsigned id,unsigned which) {
 return styles[which]|id<<8|sensors[which]<<16;
}
static void six_request(unsigned command,uint32_t handle) {
 unsigned char payload[16]={0};put32(payload,handle);put64(payload+8,aruid);
 if(!domain_id){request(command,payload,sizeof payload,1);memset(ipc+64,0xa5,4);return;}
 memset(ipc,0,sizeof ipc);put32(ipc,4);put32(ipc+4,16u|UINT32_C(0x80000000));
 put32(ipc+8,1); /* PID placeholder and eight host alignment bytes remain zero. */
 ipc[32]=1;ipc[34]=32;put32(ipc+36,domain_id);
 memcpy(ipc+48,"SFCI",4);put32(ipc+56,command);memcpy(ipc+64,payload,16);
 memset(ipc+80,0xa5,4); /* Outside domain data_size, never business data. */
}
static void six_ok(unsigned command,uint32_t handle) {
 six_request(command,handle);send_ok(hid);
 unsigned cmif=domain_id?32:16;
 assert(get32(ipc)==0&&get32(ipc+4)==(domain_id?10u:6u));
 assert(!memcmp(ipc+cmif,"SFCO",4)&&!get32(ipc+cmif+8));
 for(unsigned i=cmif+16;i<sizeof ipc;i++)assert(ipc[i]==0);
}
static void as_domain(void) {
 request(0,NULL,0,0);put32(ipc,5);send_ok(hid);domain_id=get32(ipc+32);assert(domain_id);
}
static void state(const unsigned char *ring,uint64_t sequence) {
 assert(get64(ring+8)==17&&get64(ring+16)==(sequence-1)%17);
 assert(get64(ring+24)==(sequence<16?sequence:16));
 const unsigned char *entry=ring+32+get64(ring+16)*0x68;
 assert(get64(entry)==sequence&&get64(entry+8+8)==sequence);
 assert(get64(entry+8)==0); /* No measured motion delta or pose is invented. */
 for(unsigned i=0x10;i<0x60;i++)assert(entry[8+i]==0);
}
static void sample(void) {botw_input_sample(input,botw_input_monotonic_ticks());}
static void finished(void) {unmap(copy_handle,alias);close_guest(copy_handle);cleanup();}
static void rejected(void) {
 unsigned char original[256],before[SHARED];memcpy(original,ipc,sizeof ipc);memcpy(before,backing,SHARED);
 assert(botw_services_send(services,hid,ipc,sizeof ipc,error,sizeof error)==-1);
 assert(!memcmp(original,ipc,sizeof ipc)&&!memcmp(before,backing,SHARED));
}
static void roundtrip(int domain) {
 setup();activate();if(domain)as_domain();
 unsigned char debug[DEBUG_BYTES],npad[0x1758];memcpy(debug,backing,sizeof debug);
 memcpy(npad,backing+BOTW_HID_NPAD_OFFSET,sizeof npad);
 uint32_t h=sensor_handle(0,0);six_ok(66,h);state(six(0,0),1);
 assert(!memcmp(debug,backing,sizeof debug));
 assert(!memcmp(npad,backing+BOTW_HID_NPAD_OFFSET,sizeof npad));
 unsigned char first[0x708];memcpy(first,six(0,0),sizeof first);
 six_ok(66,h);assert(!memcmp(first,six(0,0),sizeof first));
 for(uint64_t n=2;n<=40;n++){sample();state(six(0,0),n);}
 six_ok(67,h);memcpy(first,six(0,0),sizeof first);six_ok(67,h);sample();
 assert(!memcmp(first,six(0,0),sizeof first));six_ok(66,h);state(six(0,0),41);
 assert(actual_access(alias,0)&&!actual_access(alias,1));finished();
}
static void all_handles(void) {
 setup();unsigned ids[]={0,1,2,3,4,5,6,7,0x20,0x10};
 for(unsigned n=0;n<10;n++)for(unsigned r=0;r<6;r++){
  six_ok(66,sensor_handle(ids[n],r));state(six(n,r),1);
 }
 sample();
 for(unsigned n=0;n<10;n++)for(unsigned r=0;r<6;r++){
  state(six(n,r),2);six_ok(67,sensor_handle(ids[n],r));
 }
 sample();for(unsigned n=0;n<10;n++)for(unsigned r=0;r<6;r++)state(six(n,r),2);
 finished();
}
static void malformed(void) {
 setup();uint32_t h=sensor_handle(0,0);
 for(unsigned command=66;command<=67;command++){
  unsigned char payload[16]={0};put32(payload,h);put64(payload+8,aruid);
  request(command,payload,sizeof payload,0);rejected(); /* Valid framing, missing PID. */
  six_request(command,h);put64(ipc+56,aruid+1);rejected();
  six_request(command,h);put32(ipc+52,1);rejected(); /* Fixed SDK internal reserved pad. */
  six_request(command,h);put32(ipc+4,10u|UINT32_C(0x80000000));rejected();
  six_request(command,h);put32(ipc+4,13u|UINT32_C(0x80000000));rejected();
  for(unsigned kind=16;kind<=24;kind+=4){
   six_request(command,h);put32(ipc,get32(ipc)|(1u<<kind));rejected(); /* Inactive X/A/B. */
  }
  six_request(command,h);put32(ipc+4,get32(ipc+4)|(3u<<10));rejected(); /* Inactive C. */
  six_request(command,h);put32(ipc+8,3);put32(ipc+20,BOTW_KERNEL_CURRENT_PROCESS);rejected();
  uint32_t invalid[]={0,1,2,8,0x00000303,0x00010303,0x01020003,0x00000003,0x00010006,0x00000007};
  for(unsigned n=0;n<sizeof invalid/sizeof invalid[0];n++){six_request(command,invalid[n]);rejected();}
 }
 sample();for(unsigned r=0;r<6;r++)assert(get64(six(0,r)+24)==0);
 finished();
}
static void malformed_stop(void) {
 setup();uint32_t h=sensor_handle(0,0);six_ok(66,h);state(six(0,0),1);
 six_request(67,h);put64(ipc+56,aruid+1);rejected();sample();state(six(0,0),2);
 as_domain();six_request(67,h);ipc[34]=36;rejected();sample();state(six(0,0),3);
 six_ok(67,h);sample();state(six(0,0),3);finished();
}
static int deny_prepare,prepare_calls;
static int prepare(void *o,uint64_t a,size_t n,char *e,size_t cap) {
 (void)o;assert(a==(uintptr_t)ipc&&n==sizeof ipc);prepare_calls++;
 if(!deny_prepare)return 0;snprintf(e,cap,"synthetic checked IPC prepare refusal");return -1;
}
static int read_guest(void *o,uint64_t a,void *p,size_t n,char *e,size_t cap) {
 (void)o;(void)e;(void)cap;assert(memory(NULL,a,n,0));memcpy(p,(void*)(uintptr_t)a,n);return 0;
}
static int write_guest(void *o,uint64_t a,const void *p,size_t n,char *e,size_t cap) {
 (void)o;(void)e;(void)cap;assert(memory(NULL,a,n,1));memcpy((void*)(uintptr_t)a,p,n);return 0;
}
static void checked_prepare(void) {
 setup();botw_services_set_memory_copiers(services,read_guest,write_guest,NULL);
 botw_services_set_memory_write_preparer(services,prepare,NULL);uint32_t h=sensor_handle(0,0);
 deny_prepare=1;six_request(66,h);rejected();assert(prepare_calls==1);sample();assert(!get64(six(0,0)+24));
 deny_prepare=0;six_ok(66,h);state(six(0,0),1);
 deny_prepare=1;six_request(67,h);rejected();sample();state(six(0,0),2);
 deny_prepare=0;six_ok(67,h);sample();state(six(0,0),2);finished();
}
static void framing(void) {
 setup();uint32_t h=sensor_handle(0,0);six_ok(66,h);state(six(0,0),1);
 /* Session semantic16 without optional four framing bytes is also valid. */
 six_request(67,h);put32(ipc+4,11u|UINT32_C(0x80000000));send_ok(hid);
 sample();state(six(0,0),1);as_domain();six_ok(66,h);state(six(0,0),2);
 six_request(67,h);ipc[34]=36;rejected();sample();state(six(0,0),3);
 six_ok(67,h);finished();
}
static void lifetimes(void) {
 setup();uint32_t h=sensor_handle(0,0);six_ok(66,h);state(six(0,0),1);
 stop_services();close_guest(source_handle);sample();state(six(0,0),2);
 unsigned char previous[0x708];memcpy(previous,six(0,0),sizeof previous);
 botw_input_unbind_shared_memory(input);sample();assert(!memcmp(previous,six(0,0),sizeof previous));
 unmap(copy_handle,alias);close_guest(copy_handle);cleanup();
}
int main(int argc,char **argv) {
 assert(argc==2);
 if(!strcmp(argv[1],"session"))roundtrip(0);
 else if(!strcmp(argv[1],"domain"))roundtrip(1);
 else if(!strcmp(argv[1],"handles"))all_handles();
 else if(!strcmp(argv[1],"malformed"))malformed();
 else if(!strcmp(argv[1],"malformed-stop"))malformed_stop();
 else if(!strcmp(argv[1],"prepare"))checked_prepare();
 else if(!strcmp(argv[1],"framing"))framing();
 else if(!strcmp(argv[1],"lifetimes"))lifetimes();
 else assert(!"unknown case");
 puts("SIX_AXIS_REAL_KERNEL_RO_ALIAS_SERVICE_CONTRACT_OK");return 0;
}
'''


class SixAxisServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-sixaxis-services-')
        cls.exe = Path(cls.build.name) / 'sixaxis-services'
        fixture = Path(cls.build.name) / 'fixture.c'
        fixture.write_text(FIXTURE)
        flags = [shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                 '-Werror', '-pthread', '-I', str(ROOT / 'native/botw_host'),
                 '-I', str(ROOT / 'native/botw_input')]
        if sys.platform == 'darwin':
            flags += ['-framework', 'Security']
        sources = ['botw_host/kernel.c', 'botw_host/services.c', 'botw_input/input.c',
                   'botw_resources/romfs.c', 'botw_resources/savefs.c',
                   'botw_resources/local_user.c']
        built = subprocess.run(flags + [str(fixture)] + [str(ROOT / 'native' / p) for p in sources]
                               + ['-o', str(cls.exe)], capture_output=True, text=True)
        if built.returncode:
            cls.build.cleanup()
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check_case(self, mode):
        result = subprocess.run([str(self.exe), mode], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'SIX_AXIS_REAL_KERNEL_RO_ALIAS_SERVICE_CONTRACT_OK')

    def test_session_start_stop_restart_wrap_and_real_readonly_alias(self):
        self.check_case('session')

    def test_domain_start_stop_reply_has_no_inline_data_or_objects(self):
        self.check_case('domain')

    def test_all_sixty_canonical_handles_start_without_connected_motion(self):
        self.check_case('handles')

    def test_malformed_shape_and_identity_cannot_publish_or_change_ipc(self):
        self.check_case('malformed')

    def test_rejected_stop_cannot_deactivate_a_running_stream(self):
        self.check_case('malformed-stop')

    def test_checked_ipc_preparation_failure_precedes_start_and_stop_side_effects(self):
        self.check_case('prepare')

    def test_framing_tail_is_distinct_from_exact_domain_business_bytes(self):
        self.check_case('framing')

    def test_source_close_and_service_destroy_keep_mapping_until_unbind(self):
        self.check_case('lifetimes')


if __name__ == '__main__':
    unittest.main()
