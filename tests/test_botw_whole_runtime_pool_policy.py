"""Actual mandatory setup fragment and real owned-process environment only."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PART=ROOT/'experiments/botw_upstream_host'


@unittest.skipUnless(sys.platform=='darwin','Foundation and the normal Darwin toolchain')
class WholeRuntimePoolPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='botw-owned-product-env-')
        cls.addClassCleanup(cls.temp.cleanup);work=Path(cls.temp.name)
        source=(PART/'frontend/whole_runtime_configuration.m').read_text()
        start=source.index('    const char *keys[]=')
        end=source.index('    context->process_owner=',start)
        (work/'actual_product_environment.inc').write_text(source[start:end])
        cls.binary=work/'probe'
        command=['xcrun','clang','-fobjc-arc','-fblocks','-std=c11','-Wall','-Wextra','-Werror',
                 '-DBOTW_WHOLE_RUNTIME_CONFIGURED=1','-DBOTW_WHOLE_SHADER_PUBLICATION=1',
                 '-fsanitize=address,undefined','-fno-sanitize-recover=all','-g',
                 '-Wl,-dead_strip','-framework','Foundation','-I',str(work),
                 str(PART/'tests/whole_runtime_pool_policy_probe.m'),'-o',str(cls.binary)]
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    def run_case(self,mode):
        result=subprocess.run([str(self.binary),mode],capture_output=True,text=True,timeout=10,
                              env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('WHOLE_PRODUCT_POOL_POLICY_ENV_OK',result.stdout)

    def test_missing_policy_is_explicit_and_all_existing_mandatory_values_survive(self):self.run_case('missing')
    def test_inherited_legacy_probe_policy_is_replaced_without_enabling_its_identity(self):self.run_case('inherited')
    def test_unknown_or_empty_inherited_value_is_not_a_product_selection(self):
        for mode in ('unknown','empty'):
            with self.subTest(mode=mode):self.run_case(mode)
    def test_canonical_policy_set_failure_returns_the_actual_error_after_prior_values(self):self.run_case('policy-failed')
    def test_existing_mandatory_set_failure_still_stops_before_later_configuration(self):self.run_case('existing-failed')


if __name__=='__main__':unittest.main()
