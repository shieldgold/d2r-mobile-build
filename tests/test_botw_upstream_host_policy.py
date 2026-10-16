"""CPU-only policy contracts; never start the host, guest, frontend or GPU."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/botw_upstream_host"
PINNED = ROOT / ".tools/botw-upstream-live-21f026e/TommyLau-botw-mobile-21f026e"
SOURCE = EXPERIMENT / "upstream"
BASELINE = os.environ.get("BOTW_HOST_POLICY_BASELINE") == "1"


@unittest.skipUnless(sys.platform == "darwin", "policy fixtures use actual Apple signal and Mach-O host ABI")
class WholeHostPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="botw-policy-cpu-")
        cls.work = Path(cls.scratch.name)
        cls.source = cls.work / "source"
        # Keep a small source-only fixture, with no game artifacts or runtime.
        source = SOURCE if SOURCE.exists() else PINNED
        shutil.copytree(source / "dm/host", cls.source / "dm/host")
        (cls.source / "dm/gfx/nvn").mkdir(parents=True)
        for name in ("nvn_mtl.m", "hle_nvn.c", "botw_shader_observation.c", "botw_shader.c"):
            shutil.copyfile(source / "dm/gfx/nvn" / name,
                            cls.source / "dm/gfx/nvn" / name)
        if not BASELINE:
            patch = EXPERIMENT / "patches/host-policy.patch"
            subprocess.run(["patch", "-p1", "-i", str(patch)], cwd=cls.source,
                           check=True, capture_output=True, text=True)
        cls.executables = {}
        for kind in (("crash", "count") if BASELINE else ("crash", "count", "env")):
            out = cls.work / kind
            cmd = ["xcrun", "clang", "-std=gnu11", "-O0", "-Wno-format",
                   "-Wno-unused-function", "-I", str(cls.source / "dm/host"),
                   "-I", str(EXPERIMENT / "adapter"),
                   "-DBOTW_PROBE_" + kind.upper(),
                   str(EXPERIMENT / "tests/host_policy_probe.c"), "-o", str(out)]
            if not BASELINE:
                cmd[2:2] = ["-include", str(EXPERIMENT / "adapter/whole_policy.h")]
                cmd.append(str(EXPERIMENT / "adapter/whole_policy.c"))
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            cls.executables[kind] = out
        if not BASELINE:
            # Compile only diagnostic entry bodies from the patched real
            # source. No whole host, scheduler, allocator or backend is linked.
            entries = {
                "dm/host/hle_thread.c": ("thr_list", "thr_name_of", "thr_find_by_name", "svc_thread_dump"),
                "dm/host/hle_svc.c": ("dm_guest_heap_stat", "dm_guest_heap_last", "svc_unknown_strict"),
                "dm/host/hle.c": ("gate_obs_set",),
                "dm/host/dm_title.c": ("dm_unimpl_hit",),
                "dm/gfx/nvn/nvn_mtl.m": ("ub_var",),
                "dm/gfx/nvn/botw_shader_observation.c": ("botw_shader_observation_environment",),
            }
            definitions = []
            for path, names in entries.items():
                text = (cls.source / path).read_text()
                for name in names:
                    match = re.search(r"(?m)^[^\n;]*\b" + name + r"\([^\n]*?\)\s*\{", text)
                    if not match:
                        raise AssertionError("missing real entry " + name)
                    end = text.index("\n}", match.end()) + 2
                    definitions.append(text[match.start():end])
            svc = (cls.source / "dm/host/hle_svc.c").read_text()
            start = svc.index("    case 0x26: {")
            end = svc.index("    case 0x1E:", start)
            definitions.append("static uint64_t isolated_break(uint64_t *regs) { switch (0x26) {\n" +
                               svc[start:end] + "} return 4; }")
            thread = (cls.source / "dm/host/hle_thread.c").read_text()
            start = thread.index("int svc_thread(int num, uint64_t *regs) {")
            start = thread.index("{", start) + 1
            end = thread.index("    switch (num)", start)
            definitions.append('''typedef struct { int last_svc; uint64_t last_lr, nsvc; uint32_t handle; } thr_t;
static pthread_key_t g_keythr;
static struct { int thr; } g_flag;
static void isolated_svc_metadata(int num, uint64_t *regs) {
''' + thread[start:end] + "}\n")
            fixture = cls.work / "entries.c"
            fixture.write_text('''#include <stdint.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include "whole_policy.h"
#define LOG(...) do { fprintf(stderr, "[dm] " __VA_ARGS__); fputc('\\n', stderr); } while (0)
static int g_bloom_q, g_dm_present_link, g_frz_on, g_fx_dedup, g_la_cache, g_tbdr_rt, g_blit_bar;
static int fail(char *error, size_t n, const char *why, ...) { (void)error; (void)n; (void)why; return -1; }
static int exact_zero(const char *name) { (void)name; return 1; }
''' + "\n".join(definitions) + '''
int main(int argc, char **argv) {
    if (argc != 2) return 5;
    if (!strcmp(argv[1], "list")) thr_list((pthread_t *)1, 1);
    if (!strcmp(argv[1], "name")) thr_name_of((pthread_t)1, (char *)1, 100);
    if (!strcmp(argv[1], "find")) thr_find_by_name((const char *)1, (pthread_t *)1);
    if (!strcmp(argv[1], "dump")) svc_thread_dump();
    if (!strcmp(argv[1], "heap")) dm_guest_heap_stat((void *)1, (void *)1, (void *)1);
    if (!strcmp(argv[1], "heaplast")) dm_guest_heap_last((void *)1);
    if (!strcmp(argv[1], "observe")) gate_obs_set(-1, (void *)1);
    if (!strcmp(argv[1], "runtime-var")) ub_var("g_bloom_q");
    if (!strcmp(argv[1], "observation-config")) {
        const char *owned_path = (const char *)1, *owned_selector = (const char *)1;
        char owned_error = 'x';
        int mode = botw_shader_observation_environment(&owned_path, &owned_selector, &owned_error, 1);
        return mode || owned_path != (const char *)1 || owned_selector != (const char *)1 || owned_error != 'x';
    }
    if (!strcmp(argv[1], "unknown")) svc_unknown_strict(0x1F, (void *)1, 1);
    if (!strcmp(argv[1], "unimpl")) dm_unimpl_hit((const char *)1, 1, 1);
    if (!strcmp(argv[1], "break-fatal")) { uint64_t owned[31] = {0}; return isolated_break(owned); }
    if (!strcmp(argv[1], "break-notify")) { uint64_t owned[31] = {0x80000000u}; return isolated_break(owned); }
    if (!strcmp(argv[1], "svc-metadata")) {
        thr_t owned_thread = {0}; uint64_t owned_regs[31] = {0}; owned_regs[30] = 1;
        pthread_key_create(&g_keythr, NULL); pthread_setspecific(g_keythr, &owned_thread);
        isolated_svc_metadata(0x1F, owned_regs);
        return owned_thread.last_lr ? 1 : 0;
    }
    return 4;
}
''')
            out = cls.work / "entries"
            subprocess.run(["xcrun", "clang", "-std=gnu11", "-I", str(EXPERIMENT / "adapter"),
                            str(fixture), str(EXPERIMENT / "adapter/whole_policy.c"), "-o", str(out)],
                           check=True, capture_output=True, text=True)
            cls.executables["entries"] = out

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def probe(self, kind, *args):
        return subprocess.run([str(self.executables[kind]), *args],
                              capture_output=True, text=True)

    def test_dump_refuses_before_owned_callback(self):
        run = self.probe("crash", "dump")
        self.assertEqual(run.returncode, 77, run.stderr)
        self.assertEqual(run.stderr, "[botw whole-host] diagnostic entry disabled\n")

    def test_counting_never_allocates_runtime_code(self):
        run = self.probe("count")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("no allocation", run.stdout)

    @unittest.skipIf(BASELINE, "baseline RED covers only self-owned callback and intercepted mmap")
    def test_functional_host_writes_still_prepare_owned_memory(self):
        run = self.probe("crash", "write")
        self.assertEqual(run.returncode, 0, run.stderr)

    @unittest.skipIf(BASELINE, "never run the original crash diagnostic path")
    def test_handled_dirty_fault_still_resumes(self):
        run = self.probe("crash", "fault")
        self.assertEqual(run.returncode, 0, run.stderr)

    @unittest.skipIf(BASELINE, "never run the original crash diagnostic path")
    def test_unhandled_signal_fails_without_context_or_callback_output(self):
        run = self.probe("crash", "unhandled")
        self.assertEqual(run.returncode, 139, run.stderr)
        self.assertEqual(run.stderr, "[botw whole-host] unhandled host signal\n")

    @unittest.skipIf(BASELINE, "original diagnostic entry may dereference inputs")
    def test_diagnostic_entries_refuse_before_reading_arguments(self):
        for mode in ("register", "wait", "trap", "backtrace", "offset"):
            with self.subTest(mode=mode):
                run = self.probe("crash", mode)
                self.assertEqual(run.returncode, 77, run.stderr)
                self.assertEqual(run.stderr, "[botw whole-host] diagnostic entry disabled\n")

    @unittest.skipIf(BASELINE, "baseline RED does not run unfiltered diagnostic inputs")
    def test_normal_product_configuration_reaches_real_environment(self):
        names = ("HOME", "TMPDIR", "TZ", "DM_ROOT", "DM_ROMFS", "DM_SAVE", "DM_TEMP",
                 "DM_GAME_VERSION", "DM_BOTW_SHADER_MANIFEST", "DM_ARGV0", "DM_MODULES",
                 "DM_AUDIO", "DM_BOTW_AUDIO_CPU", "DM_BOTW_POOL_POLICY", "DM_LANG", "DM_OPMODE",
                 "DM_WINDOW", "DM_PRESENT_HZ", "DM_PRESENT_INTERVAL", "DM_PRESENT_LATENCY",
                 "DM_HID_NOPAD", "DM_HID_MOUSE_GAIN", "DM_HID_PAD_LAYOUT", "DM_DLC_1", "DM_DLC_99")
        env = dict(os.environ, **{name: "owned-" + name for name in names})
        run = subprocess.run([str(self.executables["env"]), *names], env=env,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.splitlines(), ["owned-" + name for name in names])

    @unittest.skipIf(BASELINE, "baseline RED does not run unfiltered diagnostic inputs")
    def test_diagnostic_and_future_switches_are_disabled(self):
        names = ("DM_WATCH", "DM_WATCH_STALL", "DM_WATCH_SAMPLE", "DM_HLECOUNT",
                 "DM_THR", "DM_SVC", "DM_DBG", "DM_IMPORTS", "DM_SAMPLE", "DM_PROBE",
                 "DM_PRELOAD", "DM_TEST_KEYRACE", "DM_KEYS", "DM_DETCLOCK", "DM_FIXED_TIME",
                 "DM_SHADER_CAPTURE", "DM_SHADER_OBSERVATION", "DM_NVN_CMD_AUDIT",
                 "DM_NVN_TSV", "DM_NVN_CBDUMP", "DM_NVN_TEXRAW", "DM_NVN_SNAPRAW",
                 "DM_NVN_REGLUM", "DM_NVN_SHOT", "DM_BOTW_SHADER_MISSING", "DM_FUTURE_UNREVIEWED",
                 "DM_DLC_0", "DM_DLC_01", "DM_DLC_100", "DM_DLC_1_BAD")
        env = dict(os.environ, **{name: "1" for name in names})
        run = subprocess.run([str(self.executables["env"]), *names], env=env,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.splitlines(), ["<NULL>"] * len(names))

    @unittest.skipIf(BASELINE, "baseline RED does not run unfiltered diagnostic inputs")
    def test_null_default_enabled_consumers_get_explicit_zero_or_off(self):
        zero = ("DM_NVN_ZSKIP", "DM_NVN_UNWRITTEN_DC", "DM_NVN_TBDR", "DM_NVN_XV",
                "DM_NVN_DEFER", "DM_NVN_BLOOM_Q", "DM_RT_AB", "DM_LONGFRAME",
                "DM_NVN_FRAMES", "DM_MEM_CAP_GB", "DM_PRESENT_LINK", "DM_APP_POOL_ADD_MB", "DM_PERF_FILE")
        names = (*zero, "DM_PSO_MANIFEST", "DM_STOP_ON_UNIMPL", "DM_TITLE")
        env = dict(os.environ, **{name: "unsafe-override" for name in names})
        run = subprocess.run([str(self.executables["env"]), *names], env=env,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.splitlines(), ["0"] * len(zero) + ["off", "1", "botw"])

    @unittest.skipIf(BASELINE, "baseline RED never calls an unprotected backtrace")
    def test_backtrace_never_reads_or_writes_supplied_memory(self):
        run = self.probe("env", "backtrace")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout + run.stderr, "")
        run = self.probe("env", "denial")
        self.assertEqual(run.returncode, 77, run.stderr)
        self.assertEqual(run.stderr, "[botw whole-host] diagnostic entry disabled\n")

    @unittest.skipIf(BASELINE, "never execute original guest-reading diagnostics")
    def test_thread_heap_and_observation_entries_refuse_invalid_inputs(self):
        for mode in ("list", "name", "find", "dump", "heap", "heaplast", "observe"):
            with self.subTest(mode=mode):
                run = self.probe("entries", mode)
                self.assertEqual(run.returncode, 77, run.stderr)
                self.assertEqual(run.stderr, "[botw whole-host] diagnostic entry disabled\n")

    @unittest.skipIf(BASELINE, "never execute original PC/register/IPC diagnostics")
    def test_unknown_service_and_import_fail_without_dereferencing_guest(self):
        for mode in ("unknown", "unimpl"):
            with self.subTest(mode=mode):
                run = self.probe("entries", mode)
                self.assertEqual(run.returncode, 3, run.stderr)
                self.assertNotRegex(run.stderr, r"x[0-9]+=|caller|hdr=|port=")
                self.assertTrue(run.stderr.endswith("[botw whole-host] upstream stopped\n"))

    @unittest.skipIf(BASELINE, "never execute the original guest Break diagnostics")
    def test_break_preserves_fatal_and_notification_semantics_without_dump(self):
        run = self.probe("entries", "break-fatal")
        self.assertEqual(run.returncode, 42, run.stderr)
        self.assertEqual(run.stderr, "[dm] [ERROR] guest requested fatal Break\n[botw whole-host] upstream stopped\n")
        run = self.probe("entries", "break-notify")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout + run.stderr, "")

    @unittest.skipIf(BASELINE, "baseline RED uses only self-owned callback and allocation intercept")
    def test_normal_svc_metadata_does_not_capture_guest_lr(self):
        run = self.probe("entries", "svc-metadata")
        self.assertEqual(run.returncode, 0, "normal SVC metadata captured a register")

    @unittest.skipIf(BASELINE, "baseline RED does not execute an unfiltered host")
    def test_every_vendored_literal_environment_consumer_has_safe_semantics(self):
        source = SOURCE if SOURCE.exists() else PINNED
        names = set()
        for path in source.rglob("*"):
            if path.suffix in (".c", ".m", ".h", ".inc"):
                names.update(re.findall(r'getenv\s*\(\s*"([A-Z0-9_]+)"', path.read_text()))
        # The runtime uses a named macro rather than a literal at its callsite.
        names.add("DM_BOTW_SHADER_MANIFEST")
        passthrough = {"HOME", "TMPDIR", "TZ", "DM_ROOT", "DM_ROMFS", "DM_SAVE", "DM_TEMP",
                       "DM_GAME_VERSION", "DM_BOTW_SHADER_MANIFEST", "DM_ARGV0", "DM_MODULES",
                       "DM_AUDIO", "DM_BOTW_AUDIO_CPU", "DM_BOTW_POOL_POLICY", "DM_LANG", "DM_OPMODE",
                       "DM_WINDOW", "DM_PRESENT_HZ", "DM_PRESENT_INTERVAL", "DM_PRESENT_LATENCY",
                       "DM_HID_NOPAD", "DM_HID_MOUSE_GAIN", "DM_HID_PAD_LAYOUT"}
        zero = {"DM_NVN_ZSKIP", "DM_NVN_UNWRITTEN_DC", "DM_NVN_TBDR", "DM_NVN_XV",
                "DM_NVN_DEFER", "DM_NVN_BLOOM_Q", "DM_RT_AB", "DM_LONGFRAME",
                "DM_NVN_FRAMES", "DM_MEM_CAP_GB", "DM_PRESENT_LINK", "DM_APP_POOL_ADD_MB", "DM_PERF_FILE"}
        fixed = dict.fromkeys(zero, "0")
        fixed.update(DM_PSO_MANIFEST="off", DM_STOP_ON_UNIMPL="1", DM_TITLE="botw")
        names = sorted(names)
        self.assertGreater(len(names), 100, "environment inventory was unexpectedly truncated")
        env = dict(os.environ, **{name: "owned-environment-value" for name in names})
        run = subprocess.run([str(self.executables["env"]), *names], env=env,
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        expected = [fixed.get(name, "owned-environment-value" if name in passthrough else "<NULL>")
                    for name in names]
        self.assertEqual(run.stdout.splitlines(), expected)

    @unittest.skipIf(BASELINE, "baseline RED uses owned callback and allocation intercept")
    def test_graphics_config_refuses_before_output_memory_and_symbol_lookup(self):
        run = self.probe("entries", "observation-config")
        self.assertEqual(run.returncode, 0, "disabled observation touched caller output memory")
        run = self.probe("entries", "runtime-var")
        self.assertEqual(run.returncode, 77, "runtime diagnostic accessed its symbol table")


if __name__ == "__main__":
    unittest.main()
