"""Exact original PCM/consumer conservation with an unobserved restart tail."""
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/botw_upstream_host'
PATCH = COMPONENT / 'patches/pcm-stress-frontier.patch'


def checked(command, **kwargs):
    result = subprocess.run([str(x) for x in command], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


OLD_ASSERTIONS = '''    CHECK_EQ(c.nonzero_frames, next - flush_dropped);
    CHECK_EQ(c.gap_total, flush_dropped - final_flush);
    CHECK_EQ(c.backwards, 0);
    CHECK_EQ(c.order_errors, 0);
    CHECK_EQ(c.silent_flags_wrong, 0);
'''


def build(work, baseline=False):
    source = work / ('baseline' if baseline else 'fixed')
    tests = source / 'dm/host/tests'; tests.mkdir(parents=True)
    for name in ('botw_pcm.c', 'botw_pcm.h', 'tests/t_botw_pcm.c'):
        shutil.copyfile(COMPONENT / 'upstream/dm/host' / name, source / 'dm/host' / name)
    for name in ('ios-audio-output.patch', 'audio-write-observed.patch'):
        checked(['patch', '--fuzz=0', '-p1', '-i', COMPONENT / 'patches' / name], cwd=source)
    fixture = tests / 't_botw_pcm.c'
    if baseline:
        text = fixture.read_text()
        helper = ('static void check_stress_sequence(const consumer_ctx *c,uint64_t next,'
                  'uint64_t flush_dropped,uint64_t final_flush){\n' +
                  OLD_ASSERTIONS.replace('c.', 'c->') + '}\n\n')
        text = text.replace('static void test_stress(void) {', helper + 'static void test_stress(void) {')
        fixture.write_text(text)
    else:
        checked(['patch', '--fuzz=0', '-p1', '-i', PATCH], cwd=source)
    # The owned test scheduler only gates iterations, outside pcm_render. The
    # original actual consumer/sequence/flags algorithm is compiled unchanged.
    text = fixture.read_text()
    loop = '    while (!atomic_load_explicit(&c->stop, memory_order_acquire)) {\n'
    assert text.count(loop) == 2  # sequence + gain consumer; gate only sequence.
    text = text.replace(loop, loop + '        if (!owned_pcm_consumer_step(c)) break;\n', 1)
    fixture.write_text(text)
    binary = source / 'frontier'
    command = ['xcrun', 'clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-DBOTW_PCM_MOCK_AU=1', '-pthread', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=all', '-I', source, '-I', COMPONENT / 'adapter',
               COMPONENT / 'tests/whole_pcm_stress_frontier_probe.c',
               COMPONENT / 'adapter/whole_audio_platform.m', '-framework', 'AudioToolbox', '-o', binary]
    checked(command)
    # Compile the complete original suite without the deterministic scheduler.
    # No source/counter/ring/gain/callback/lifecycle algorithm is replaced.
    suite_source = tests / 'whole_pcm_complete_suite.c'
    suite_source.write_text(text.replace('        if (!owned_pcm_consumer_step(c)) break;\n', ''))
    suite = source / 'complete-suite'
    suite_command = command.copy()
    suite_command[suite_command.index(COMPONENT / 'tests/whole_pcm_stress_frontier_probe.c')] = suite_source
    suite_command[-1] = suite
    checked(suite_command)
    return source, binary, suite


@unittest.skipUnless(platform.system() == 'Darwin', 'actual macOS SDK required')
class WholePCMStressFrontierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-pcm-stress-frontier-')
        cls.source, cls.binary, cls.suite = build(Path(cls.scratch.name))

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_actual_periodic_flush_unobserved_until_final_stop_is_exact_tail_gap(self):
        checked([self.binary, 'tail'], timeout=20)

    def test_actual_periodic_flush_followed_by_consumed_data_is_observed_gap(self):
        checked([self.binary, 'observed'], timeout=20)

    def test_incorrect_gap_accounting_still_fails_exact_assertion(self):
        result = subprocess.run([str(self.binary), 'corrupt-gap'], text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('c->gap_total', result.stderr)

    def test_complete_original_ring_gain_quiescence_and_concurrent_stress_after_patch(self):
        result = checked([self.suite], timeout=30)
        self.assertIn('t_botw_pcm ok', result.stdout)

    def test_patch_changes_only_fixture_sequence_check_not_pcm_or_callback(self):
        text = PATCH.read_text()
        files = [line.split('\t')[0][6:] for line in text.splitlines() if line.startswith('--- a/')]
        self.assertEqual(files, ['dm/host/tests/t_botw_pcm.c'])
        original = (COMPONENT / 'upstream/dm/host/tests/t_botw_pcm.c').read_text()
        fixed = (self.source / 'dm/host/tests/t_botw_pcm.c').read_text()
        first = 'static void *consumer_main(void *p) {'
        last = 'static void test_stress(void) {'
        actual = fixed[fixed.index(first):fixed.index('static void check_stress_sequence(')]
        actual = actual.replace('        if (!owned_pcm_consumer_step(c)) break;\n', '')
        expected = original[original.index(first):original.index(last)]
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
