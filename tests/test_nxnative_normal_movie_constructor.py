"""Run the actual movie constructor before an owned CPU program's main."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest

from test_nxnative_normal_hle_crash import function_span

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'experiments/nxnative_base_v0'
MOVIE = 'dm/host/hle_movie.c'
SIGNATURE = '__attribute__((constructor)) static void movie_ring_gates(void)'
MEMBERS = ('dm/host/dm.h', 'dm/host/hle.c', 'dm/host/hle_svc.c',
           'dm/host/hle_sdk16.c', 'dm/host/crash.c', 'dm/ios/Sources/main.m', MOVIE)

PROBE = r'''
#include <stdint.h>
#include <unistd.h>
#include "normal_failures.h"
enum { OBS_MOVIE_RING_LINK = 19, OBS_MOVIE_RING_UNLINK = 20 };
static int registrations;
static void movie_ring_wait(const uint64_t *regs) { (void)regs; _exit(91); }
static void gate_obs_set(int id, void (*callback)(const uint64_t *)) {
#if defined(NXNATIVE_NORMAL_POLICY) && NXNATIVE_NORMAL_POLICY
    (void)id; (void)callback;
    nxnative_normal_fail(NXNATIVE_NORMAL_DIAGNOSTIC_DISABLED);
#else
    int expected = registrations == 0 ? OBS_MOVIE_RING_LINK : OBS_MOVIE_RING_UNLINK;
    if (registrations >= 2 || id != expected || callback != movie_ring_wait) _exit(92);
    registrations++;
#endif
}
/* ACTUAL_CONSTRUCTOR */
int main(void) {
#if defined(NXNATIVE_NORMAL_POLICY) && NXNATIVE_NORMAL_POLICY
    return registrations == 0 ? 0 : 93;
#else
    return registrations == 2 ? 0 : 94;
#endif
}
'''


class NormalMovieConstructor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='nxnative-owned-movie-constructor-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.stage = Path(cls.temp.name)
        cls.before = {}
        for name in MEMBERS:
            source = COMPONENT / 'upstream' / name
            cls.before[name] = hashlib.sha256(source.read_bytes()).hexdigest()
            destination = cls.stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for name in ('normal-entry.patch', 'normal-hle-crash.patch',
                     'base-v0-normal-movie-constructor.patch'):
            patch = COMPONENT / 'patches' / name
            result = subprocess.run(
                ['patch', '-p1', '--batch', '--fuzz=0', '-i', str(patch)],
                cwd=cls.stage, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError('actual predecessor patch failed: ' + result.stdout + result.stderr)
        cls.movie = (cls.stage / MOVIE).read_text()
        start, end = function_span(cls.movie, SIGNATURE)
        cls.constructor = cls.movie[start:end]

    def execute_constructor(self, policy):
        source = self.stage / ('constructor-' + str(policy) + '.c')
        executable = source.with_suffix('')
        source.write_text(PROBE.replace('/* ACTUAL_CONSTRUCTOR */', self.constructor))
        command = ['clang', '-std=gnu11', '-I', str(COMPONENT / 'adapter'),
                   str(source), str(COMPONENT / 'adapter/normal_failures.c'), '-o', str(executable)]
        if policy is not None:
            command.insert(1, '-DNXNATIVE_NORMAL_POLICY=' + str(policy))
        built = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return subprocess.run([str(executable)], capture_output=True, timeout=10)

    def test_normal_constructor_reaches_main_without_observer_registration(self):
        result = self.execute_constructor(1)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout + result.stderr, b'')

    def test_explicit_non_normal_constructor_preserves_both_registrations(self):
        result = self.execute_constructor(0)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_original_configuration_preserves_both_registrations(self):
        result = self.execute_constructor(None)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_movie_code_outside_constructor_and_vendor_sources_are_unchanged(self):
        original = (COMPONENT / 'upstream' / MOVIE).read_text()
        old_start, old_end = function_span(original, SIGNATURE)
        new_start, new_end = function_span(self.movie, SIGNATURE)
        self.assertEqual(original[:old_start] + original[old_end:],
                         self.movie[:new_start] + self.movie[new_end:])
        for name, digest in self.before.items():
            self.assertEqual(hashlib.sha256((COMPONENT / 'upstream' / name).read_bytes()).hexdigest(), digest)
