"""Real bounded memory-provider leases over owned CPU backing only."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

COMPONENT=Path(__file__).resolve().parents[1]

class MemoryProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch=tempfile.TemporaryDirectory(prefix='botw-memory-provider-')
        cls.binary=Path(cls.scratch.name)/'probe'
        command=['clang++','-std=c++23','-O1','-g','-Wall','-Wextra','-Werror','-pthread',
                 '-I',str(COMPONENT/'platform'),str(COMPONENT/'tests/memory_provider_probe.cpp'),
                 str(COMPONENT/'platform/platform.cpp'),'-o',str(cls.binary)]
        if os.environ.get('BOTW_AUDIO_RENDERER_SANITIZERS')=='1':
            command+=['-fsanitize=address,undefined','-fno-sanitize-recover=all']
        result=subprocess.run(command,text=True,capture_output=True)
        if result.returncode: raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls): cls.scratch.cleanup()
    def case(self,name):
        result=subprocess.run([str(self.binary),name],text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_real_pool_resolves_once_and_last_slice_releases_actual_owner(self): self.case('resolve')
    def test_registered_permissions_owner_and_partial_overlap_never_fall_back(self): self.case('priority')
    def test_source_provenance_rejects_physical_overlap_across_distinct_aliases(self): self.case('physical')
    def test_racing_resolvers_share_winner_and_release_the_loser_outside_lock(self): self.case('concurrent')
    def test_registration_race_rechecks_permissions_before_publishing(self): self.case('registration-race')
    def test_release_failure_keeps_owner_and_retries_without_false_success(self): self.case('release-failure')
    def test_provider_failure_with_an_owner_is_quarantined_and_reported(self): self.case('failed-owner')
    def test_malformed_view_is_rejected_and_its_real_owner_released(self): self.case('bad-view')
    def test_last_owned_pin_keeps_provider_context_alive_beyond_memory_space(self): self.case('lifetime')
    def test_race_loser_release_failure_quarantines_and_dominates_primary_error(self): self.case('loser-failure')
    def test_source_owner_permission_and_bounds_failures_remain_real_failures(self): self.case('source-denials')
    def test_concurrent_cleanup_cannot_erase_the_failing_calls_original_release_error(self): self.case('loser-retry-race')
    def test_cleanup_does_not_ack_while_a_real_owner_release_is_still_inflight(self): self.case('cleanup-inflight')

if __name__=='__main__': unittest.main()
