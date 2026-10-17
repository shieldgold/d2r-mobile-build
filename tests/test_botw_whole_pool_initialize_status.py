"""Ordinary pool result reporting over the complete owned backing fixture."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'whole_pool_status_backing_fixture', ROOT / 'tests/test_botw_whole_pool_backing.py')
BACKING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKING)
BASELINE = os.environ.get('BOTW_WHOLE_POOL_INITIALIZE_STATUS_BASELINE') == '1'

OBSERVE_IMPL = r'''
static NVNboolean owned_observe_pool_initialize(NVNmemoryPool *p, const NVNmemoryPoolBuilder *b) {
    status_impl_calls++;
    NVNboolean result = botw_nvn_memory_pool_initialize_impl(p, b);
    status_impl_errno = errno;
    status_impl_result = result;
    status_impl_returned = 1;
    return result;
}
#define botw_nvn_memory_pool_initialize_impl owned_observe_pool_initialize
'''

CHECK_CALL = r'''
/* Only the owned callers are observed; the actual implementation and public
 * wrapper remain the extracted production bytes. */
static NVNboolean owned_check_pool_initialize(NVNmemoryPool *p, const NVNmemoryPoolBuilder *b) {
    unsigned before = status_impl_calls;
    status_impl_returned = 0;
    errno = EDOM;
    NVNboolean result = h_nvnMemoryPoolInitialize(p, b);
#ifndef WHOLE_POOL_STATUS_BASELINE
    if (!status_impl_returned || status_impl_calls != before + 1 ||
        result != status_impl_result || errno != status_impl_errno) status_violation = 1;
#else
    (void)before;
#endif
    return result;
}
#define h_nvnMemoryPoolInitialize owned_check_pool_initialize
'''

PROBE_PREFIX = r'''
#define BOTW_WHOLE_POLICY_IMPLEMENTATION
#include "whole_policy.h"
#include <errno.h>
static unsigned status_impl_calls;
static int status_impl_errno, status_impl_returned, status_impl_result, status_violation;
ssize_t owned_pool_status_write(int, const void *, size_t);
'''

PROBE_SUFFIX = r'''
/* The real logger's sole write is redirected at compile time to this owned
 * observer. It checks admission/registry locks and then performs the real
 * bounded stderr write, deliberately changing errno before returning. */
ssize_t owned_pool_status_write(int fd, const void *bytes, size_t size) {
    static const char ok[] = "[botw whole-host] api=NvnMemoryPoolInitialize result=ok\n";
    static const char error[] = "[botw whole-host] api=NvnMemoryPoolInitialize result=error\n";
    if (fd != STDERR_FILENO || !status_impl_returned ||
        !((size == sizeof ok - 1 && !memcmp(bytes, ok, size)) ||
          (size == sizeof error - 1 && !memcmp(bytes, error, size)))) status_violation = 1;
    if (pthread_mutex_trylock(&g_pool_gate.mutex)) status_violation = 1;
    else {
        if (g_pool_gate.writer) status_violation = 1;
        pthread_mutex_unlock(&g_pool_gate.mutex);
    }
    if (pthread_mutex_trylock(&g_mu)) status_violation = 1;
    else pthread_mutex_unlock(&g_mu);
    ssize_t result = write(fd, bytes, size);
    errno = EIO;
    return result;
}
int main(int argc, char **argv) {
    int result = backing_fixture_main(argc, argv);
    if (status_violation) {
        fputs("FAILED status changed result/errno or ran before cleanup\n", stderr);
        return 30;
    }
    return result;
}
'''


@unittest.skipUnless(sys.platform == 'darwin', 'requires real Darwin VM and Objective-C')
class WholePoolInitializeStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the original providers and real staged entry bodies unchanged.
        if BACKING.BASELINE:
            raise AssertionError('this fixture requires complete ordinary pool backing')
        BACKING.WholePoolBacking.setUpClass.__func__(cls)
        hle_path = cls.source / 'dm/gfx/nvn/hle_nvn.c'
        original = hle_path.read_text()
        cls.original_initialize = BACKING.function(original, 'NVN(nvnMemoryPoolInitialize,')
        if not BASELINE:
            subprocess.run(['patch', '-p1', '-i', str(BACKING.EXP / 'patches/pool-initialize-status.patch')],
                           cwd=cls.source, check=True, capture_output=True, text=True)
        hle = hle_path.read_text()
        cls.actual_hle = hle
        entries = hle[hle.index('// WHOLE_POOL_BACKING_ENTRIES_BEGIN'):
                      hle.index('// WHOLE_POOL_BACKING_ENTRIES_END')]
        if not BASELINE:
            entries += '\n' + BACKING.function(
                hle, 'static NVNboolean botw_nvn_memory_pool_initialize_impl(')
            entries += '\n' + OBSERVE_IMPL
        entries += '\n' + BACKING.function(hle, 'NVN(nvnMemoryPoolInitialize,')
        if not BASELINE:
            entries += '\n#undef botw_nvn_memory_pool_initialize_impl\n'
        for name in ('Finalize', 'Map', 'GetSize', 'GetFlags', 'GetBufferAddress',
                     'FlushMappedRange', 'InvalidateMappedRange'):
            entries += '\n' + BACKING.function(hle, 'NVN(nvnMemoryPool' + name + ',')
        (cls.work / 'pool_entries.inc').write_text(entries)
        original_probe = (BACKING.EXP / 'tests/whole_pool_backing_probe.m').read_text()
        marker = '#include "pool_entries.inc"'
        if original_probe.count(marker) != 1 or original_probe.count('int main(') != 1:
            raise AssertionError('original fixture seam changed')
        probe = PROBE_PREFIX + original_probe.replace(marker, marker + '\n' + CHECK_CALL)
        probe = probe.replace('int main(', 'static int backing_fixture_main(', 1) + PROBE_SUFFIX
        probe_path = cls.work / 'status_probe.m'
        probe_path.write_text(probe)
        logger_path = cls.work / 'status_logger.c'
        logger_path.write_text('#include <unistd.h>\n'
                              'ssize_t owned_pool_status_write(int, const void *, size_t);\n'
                              '#define write owned_pool_status_write\n'
                              '#include "whole_policy.c"\n')
        command = ['clang', '-std=gnu11', '-fobjc-arc', '-fobjc-arc-exceptions', '-fblocks',
                   '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                   '-Wno-unused-variable', '-Wno-format', '-pthread',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I', str(cls.source / 'dm/gfx/nvn'),
                   '-I', str(cls.source / 'dm/gfx/nvn/include'),
                   '-I', str(BACKING.EXP / 'adapter'), '-I', str(cls.work),
                   str(probe_path), str(logger_path),
                   str(cls.source / 'dm/gfx/nvn/nvn_vpool.c'),
                   str(BACKING.EXP / 'adapter/whole_pool_backing_memory.m'),
                   str(BACKING.EXP / 'adapter/whole_pool_backing_gate.c'),
                   '-framework', 'Foundation', '-framework', 'Metal', '-o', str(cls.binary)]
        if BASELINE:
            command += ['-DWHOLE_POOL_STATUS_BASELINE']
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def status_case(self, mode, *outcomes):
        run = subprocess.run([str(self.binary), mode], capture_output=True,
                             text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout, 'WHOLE_POOL_BACKING_CPU_OK\n')
        self.assertEqual(run.stderr, ''.join(
            '[botw whole-host] api=NvnMemoryPoolInitialize result=' + outcome + '\n'
            for outcome in outcomes))

    def test_real_success_reports_ok_after_original_checks(self):
        self.status_case('readonly-deferred', 'ok')

    def test_real_factory_failure_reports_error_after_original_rollback(self):
        self.status_case('factory-rollback', 'error')

    def test_checked_dirty_failure_preserves_original_no_factory_rollback(self):
        self.status_case('dirty-failure', 'error')

    def test_repeated_invalid_inputs_report_error_once_without_publication(self):
        self.status_case('invalid-input', 'error')

    def test_success_and_overlap_failures_report_each_outcome_once(self):
        self.status_case('overlap', 'ok', 'error')

    def test_reinitialize_success_reports_ok_once(self):
        self.status_case('texture-birth', 'ok')

    def test_actual_publication_failure_rolls_back_before_error_status(self):
        self.status_case('publish-rollback', 'error')

    def test_thrown_factory_does_not_invent_a_return_status(self):
        self.status_case('factory-throws')

    @unittest.skipIf(BASELINE, 'baseline has no private implementation wrapper')
    def test_complete_original_body_is_unchanged_and_wrapper_calls_it_once(self):
        actual = BACKING.function(self.actual_hle,
                                  'static NVNboolean botw_nvn_memory_pool_initialize_impl(')
        self.assertEqual(actual[actual.index('{'):],
                         self.original_initialize[self.original_initialize.index('{'):])
        wrapper = BACKING.function(self.actual_hle, 'NVN(nvnMemoryPoolInitialize,')
        self.assertEqual(wrapper.count('botw_nvn_memory_pool_initialize_impl(p, b)'), 1)


if __name__ == '__main__':
    unittest.main()
