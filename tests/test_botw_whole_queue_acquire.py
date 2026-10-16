"""Queue acquire must reuse the complete original WindowAcquire CPU contract."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/botw_upstream_host'
PATCH='patches/base-v0-queue-acquire.patch'
BASELINE=os.environ.get('BOTW_WHOLE_QUEUE_ACQUIRE_BASELINE')=='1'


def definition(text,anchor):
    start=text.index(anchor);i=text.index('{',start);depth=0;quote=None
    while i<len(text):
        c=text[i]
        if quote:
            if c=='\\':i+=2;continue
            if c==quote:quote=None
        elif text.startswith('//',i):i=text.index('\n',i);continue
        elif text.startswith('/*',i):i=text.index('*/',i)+2;continue
        elif c in ('"',"'"):quote=c
        elif c=='{':depth+=1
        elif c=='}':
            depth-=1
            if not depth:return text[start:i+1]
        i+=1
    raise AssertionError('actual NVN definition is incomplete')


@unittest.skipUnless(shutil.which('clang'),'owned actual C bodies require clang')
class WholeQueueAcquire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-queue-acquire-');cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name);cls.source=cls.work/'source';shutil.copytree(EXP/'upstream',cls.source)
        patches=json.loads((EXP/'SOURCE.lock.json').read_text())['patches']
        for name in patches:
            if name==PATCH:continue
            cls.apply(name)
        if not BASELINE and (EXP/PATCH).exists():cls.apply(PATCH)
        source=(cls.source/'dm/gfx/nvn/hle_nvn.c').read_text()
        cls.window=definition(source,'NVN(nvnWindowAcquireTexture,')
        cls.original_window=definition((EXP/'upstream/dm/gfx/nvn/hle_nvn.c').read_text(),'NVN(nvnWindowAcquireTexture,')
        cls.has_queue='NVN(nvnQueueAcquireTexture,' in source
        cls.queue=definition(source,'NVN(nvnQueueAcquireTexture,') if cls.has_queue else ''
        (cls.work/'normal_queue_functions.inc').write_text(cls.window+'\n'+cls.queue+'\n')
        objects=(cls.source/'dm/gfx/nvn/nvn_obj.h').read_text();lines=objects.splitlines()
        types=[next(line for line in lines if line.startswith('#define MAGIC(')),next(line for line in lines if line.startswith('#define FITS('))]
        for name in ('queue_t','sync_t','win_t'):
            index=next(i for i,line in enumerate(lines) if '} '+name+';' in line)
            types.append(lines[index])
            if name=='win_t':types.append(lines[index+1])
        types.append(next(line for line in source.splitlines() if '} rt_decl;' in line))
        (cls.work/'normal_queue_types.inc').write_text('\n'.join(types)+'\n')
        cls.binary=cls.work/'probe'
        command=['clang','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-pthread','-fsanitize=address,undefined','-fno-sanitize-recover=all','-DBOTW_HAS_QUEUE_ACQUIRE='+str(int(cls.has_queue)),'-I',str(cls.work),'-I',str(cls.source/'dm/gfx/nvn'),'-I',str(cls.source/'dm/gfx/nvn/include'),str(EXP/'tests/whole_queue_acquire_probe.c'),'-o',str(cls.binary)]
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def apply(cls,name):
        result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(EXP/name)],cwd=cls.source,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    def case(self,mode,queue=True):
        if queue and not self.has_queue:self.skipTest('actual QueueAcquire body absent; dedicated implementation review must fail')
        result=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_actual_queue_implementation_is_present(self):
        self.assertTrue(self.has_queue,'complete actual NVN source has no nvnQueueAcquireTexture implementation; missing symbol review is RED, not a compiler failure')

    def test_original_window_function_remains_byte_identical(self):
        self.assertEqual(self.window,self.original_window)

    def test_original_window_body_and_real_prototype_compile_and_preserve_wait_order(self):
        self.case('window',queue=False)

    def test_queue_success_is_enum_zero_and_reuses_the_complete_window_wait_sequence(self):
        self.case('queue')

    def test_window_and_queue_interleave_the_same_index_and_present_ticket(self):
        self.case('interleaved')

    def test_display_link_and_fallback_pacing_are_only_the_original_window_calls(self):
        for mode in ('link-ready','link-fallback','no-period'):self.case(mode)

    def test_zero_or_dropped_ticket_has_no_extra_gpu_or_cpu_wait(self):
        for mode in ('no-ticket','dropped-ticket'):self.case(mode)

    def test_all_four_legal_swapchain_counts_use_the_original_round_robin(self):
        self.case('counts')

    def test_invalid_parameters_preserve_queue_window_output_and_make_no_calls(self):
        for mode in ('null-q','null-w','null-out','bad-q','bad-w','count-zero','count-negative','count-five','count-max','next-negative','next-min','next-max'):self.case(mode)

    def test_last_safe_next_then_overflow_state_fails_without_mutation(self):
        self.case('overflow')


if __name__=='__main__':unittest.main()
