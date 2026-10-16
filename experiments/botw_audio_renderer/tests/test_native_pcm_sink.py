"""Real upstream conversion + existing PCM callback, owned CPU hardware seam only."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

COMPONENT = Path(__file__).resolve().parents[1]
WHOLE = COMPONENT.parent / 'botw_upstream_host'
SINKS = ('sink.h', 'sink_stream.h', 'sink_stream.cpp')


def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], text=True, capture_output=True, **kwargs)


def checked(command, **kwargs):
    result = run(command, **kwargs)
    if result.returncode:
        raise AssertionError(f'{command!r}\n{result.stdout}{result.stderr}')
    return result


def host_port(name, text):
    """Baseline only: mechanical real host port, without any queue behavior change."""
    text = text.replace('Core::System', 'botw::audio::RendererHost')
    text = text.replace('namespace Core {\nclass System;\n}',
                        'namespace botw::audio {\nclass RendererHost;\n}')
    if name == 'sink_stream.cpp':
        for include in ('audio_core/audio_core.h', 'common/settings.h', 'core/core.h', 'core/core_timing.h'):
            text = text.replace(f'#include "{include}"\n', '')
        text = '#include "renderer_host.h"\n#include <cmath>\n' + text
        text = text.replace('Settings::Volume()', 'system.master_volume()')
        text = text.replace('system.IsPaused()', 'system.is_paused()')
        text = text.replace('system.IsShuttingDown()', 'system.is_shutting_down()')
        text = text.replace('system.CoreTiming().GetGlobalTimeNs()', 'std::chrono::nanoseconds(system.clock_ns())')
    return text


def compile_sink(work, baseline=False):
    source = work / ('baseline-source' if baseline else 'source')
    sink_dir = source / 'src/audio_core/sink'
    sink_dir.mkdir(parents=True)
    for name in SINKS:
        original = (COMPONENT / 'vendor/eden/src/audio_core/sink' / name).read_text()
        (sink_dir / name).write_text(host_port(name, original) if baseline else original)
    if not baseline:
        checked(['patch', '--fuzz=0', '-p1', '-i', COMPONENT / 'patches/native-pcm-sink.patch'], cwd=source)
    pcm = source / 'dm/host'; pcm.mkdir(parents=True)
    for name in ('botw_pcm.c', 'botw_pcm.h'):
        shutil.copyfile(WHOLE / 'upstream/dm/host' / name, pcm / name)
    checked(['patch', '--fuzz=0', '-p1', '-i', WHOLE / 'patches/ios-audio-output.patch'], cwd=source)
    if not baseline:
        checked(['patch', '--fuzz=0', '-p1', '-i', WHOLE / 'patches/audio-write-observed.patch'], cwd=source)
    san = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    cbase = ['xcrun', 'clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
             '-DBOTW_PCM_MOCK_AU', '-pthread', '-I', pcm, '-I', WHOLE / 'adapter'] + san
    objects = []
    for i, path in enumerate((pcm / 'botw_pcm.c', WHOLE / 'adapter/whole_audio_platform.m',
                              COMPONENT / 'tests/pcm_mock_au.c')):
        obj = source / f'pcm-{i}.o'; checked(cbase + ['-c', path, '-o', obj]); objects.append(obj)
    binary = source / 'native-sink-probe'
    # Deterministic scheduling seam on a private copy of the real Host getter.
    # Keep its atomic read and real setter; no fake Core/System or volume source.
    host = (COMPONENT / 'native/renderer_host.cpp').read_text()
    getter = 'float RendererHost::master_volume() const noexcept { return volume_; }'
    assert host.count(getter) == 1
    host = 'extern "C" void owned_master_read_after(float);\n' + host.replace(getter,
        'float RendererHost::master_volume() const noexcept { '
        'const float snapshot = volume_; owned_master_read_after(snapshot); return snapshot; }')
    host_source = source / 'owned-renderer-host.cpp'; host_source.write_text(host)
    cpp = ['xcrun', 'clang++', '-std=c++23', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
           '-pthread', '-DFMT_HEADER_ONLY=1', '-include', 'array',
           '-I', source / 'src', '-I', COMPONENT / 'vendor/eden/src',
           '-I', COMPONENT / 'vendor/fmt/include', '-I', COMPONENT / 'native',
           '-I', COMPONENT / 'platform', '-I', COMPONENT / 'adapter', '-I', WHOLE / 'adapter'] + san + ['-fsanitize=float-cast-overflow']
    sources = [COMPONENT / 'tests/native_sink_stream_probe.cpp', sink_dir / 'sink_stream.cpp',
               host_source, COMPONENT / 'platform/platform.cpp',
               COMPONENT / 'platform/pcm_output.cpp']
    if baseline:
        cpp += ['-DOWNED_EAGER_BASELINE=1', '-Wno-unused-parameter']
    else:
        sources += [COMPONENT / 'adapter/native_sink_stream.cpp']
    checked(cpp + sources + objects + ['-I', pcm, '-framework', 'AudioToolbox', '-o', binary])
    return binary, source


class SourceContractTests(unittest.TestCase):
    def test_original_append_algorithm_is_complete_and_only_submission_is_dispatched(self):
        with tempfile.TemporaryDirectory(prefix='botw-sink-source-') as directory:
            source = Path(directory)
            sink = source / 'src/audio_core/sink'; sink.mkdir(parents=True)
            for name in SINKS:
                shutil.copyfile(COMPONENT / 'vendor/eden/src/audio_core/sink' / name, sink / name)
            checked(['patch', '--fuzz=0', '-p1', '-i', COMPONENT / 'patches/native-pcm-sink.patch'], cwd=source)
            original = (COMPONENT / 'vendor/eden/src/audio_core/sink/sink_stream.cpp').read_text()
            changed = (sink / 'sink_stream.cpp').read_text()
            start = 'void SinkStream::AppendBuffer('
            expected = original[original.index(start):original.index('std::vector<s16> SinkStream::ReleaseBuffer')]
            actual = changed[changed.index(start):changed.index('void SinkStream::SubmitBuffer')]
            actual = actual.replace('GetMasterVolume()', 'Settings::Volume()')
            for span in ('samples.subspan(0, samples.size() / system_channels * device_channels)',
                         'new_samples', 'samples'):
                actual = actual.replace(f'SubmitBuffer(buffer, {span});',
                                        f'queue.EmplaceWait(buffer);\n        samples_buffer.Push({span});')
            self.assertTrue(actual.endswith('}\n\n'))
            actual = actual[:-3] + '    ++queued_buffers;\n}\n\n'
            self.assertEqual(actual, expected)
            headers = [line.split('\t')[0][6:] for line in (COMPONENT / 'patches/native-pcm-sink.patch').read_text().splitlines()
                       if line.startswith('--- a/')]
            self.assertEqual(headers, ['src/audio_core/sink/' + name for name in SINKS])

    def test_observed_patch_preserves_complete_original_pcm_and_realtime_callback(self):
        with tempfile.TemporaryDirectory(prefix='botw-observed-source-') as directory:
            source = Path(directory); host = source / 'dm/host'; host.mkdir(parents=True)
            for name in ('botw_pcm.c', 'botw_pcm.h'):
                shutil.copyfile(WHOLE / 'upstream/dm/host' / name, host / name)
            checked(['patch', '--fuzz=0', '-p1', '-i', WHOLE / 'patches/ios-audio-output.patch'], cwd=source)
            original = {name: (host / name).read_text() for name in ('botw_pcm.c', 'botw_pcm.h')}
            checked(['patch', '--fuzz=0', '-p1', '-i', WHOLE / 'patches/audio-write-observed.patch'], cwd=source)
            changed = (host / 'botw_pcm.c').read_text()
            first = changed.index('// Snapshot old FIFO retirement')
            last = changed.index('int botw_pcm_set_volume(', first)
            addition = changed[first:last]
            self.assertEqual(changed[:first] + changed[last:], original['botw_pcm.c'])
            self.assertLess(addition.index('whole_audio_platform_lock();'), addition.index('pcm_get_counters_impl(s, before);'))
            self.assertLess(addition.index('pcm_get_counters_impl(s, before);'), addition.index('pcm_write_impl('))
            self.assertLess(addition.index('pcm_write_impl('), addition.index('whole_audio_platform_unlock();'))
            header = (host / 'botw_pcm.h').read_text()
            first = header.index('// As write, with a mandatory counter snapshot')
            last = header.index('// Per-sink output gain,', first)
            self.assertEqual(header[:first] + header[last:], original['botw_pcm.h'])


@unittest.skipUnless(platform.system() == 'Darwin', 'actual macOS PCM/AudioToolbox ABI required')
class NativePcmSinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='botw-native-sink-')
        cls.work = Path(cls.scratch.name)
        cls.baseline = os.environ.get('BOTW_SINK_EAGER_BASELINE') == '1'
        cls.binary, cls.source = compile_sink(cls.work, cls.baseline)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def probe(self, case):
        result = run([self.binary, case], timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_submission_does_not_retire_before_actual_pcm_callback(self): self.probe('consumption')
    def test_fifo_duplicate_tags_and_partial_consumption(self): self.probe('fifo')
    def test_wait_is_released_by_actual_completion_cancel_or_real_failure(self): self.probe('wait')
    def test_clear_stop_and_pending_are_cancellation_not_consumption(self): self.probe('clear')
    def test_external_platform_flush_cancels_without_faking_consumption(self): self.probe('lifecycle')
    def test_lifecycle_between_counter_read_and_write_preserves_real_fifo_identity(self): self.probe('lifecycle-interleave')
    def test_observed_write_distinguishes_refused_suffix_and_lifecycle_flush(self): self.probe('observed-refusal')
    def test_complete_original_gain_and_channel_conversion(self): self.probe('conversion')
    def test_input_stream_and_unknown_channels_rejected_before_device_open(self): self.probe('validation')
    def test_malformed_buffer_fails_before_conversion(self): self.probe('invalid-buffer')
    def test_nonfinite_volume_rejected(self): self.probe('invalid-volume')
    def test_master_volume_validation_and_conversion_use_one_actual_snapshot(self): self.probe('master-snapshot')
    def test_real_device_open_failure_reaches_host(self): self.probe('open-failure')
    def test_real_device_start_failure_reaches_host(self): self.probe('start-failure')
    def test_real_pcm_rejected_write_reaches_host(self): self.probe('write-failure')
    def test_real_device_stop_failure_reaches_host(self): self.probe('stop-failure')
    def test_pending_storage_is_bounded(self): self.probe('capacity')


if __name__ == '__main__': unittest.main()
