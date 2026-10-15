"""Real runtime input backing, display separation and teardown races."""
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
#include "RUNTIME_SOURCE"
#include <assert.h>
#include <stdatomic.h>
static void (*sample_tick)(void);
static _Atomic unsigned calls;
static _Atomic int running;
static uint64_t count(botw_input *input) {
    unsigned char *p = botw_input_shared_memory(input, NULL);
    uint64_t n; memcpy(&n, p + BOTW_HID_NPAD_OFFSET + 0x28 + 0x18, 8); return n;
}
static void *feed(void *unused) {
    (void)unused;
    while (atomic_load(&running)) { sample_tick(); atomic_fetch_add(&calls, 1); }
    return NULL;
}
int main(int argc, char **argv) {
    assert(argc == 3);
    sample_tick = (void (*)(void))dlsym(RTLD_DEFAULT, "botw_host_sample_input");
    assert(sample_tick && "runtime is missing the host input cadence entry");
    state.owner = pthread_self();
    botw_input *input = botw_input_create(); assert(input);
    assert(!botw_host_set_input(input));
    state.kernel = botw_kernel_create(NULL, NULL); assert(state.kernel);
    state.services = botw_services_create(services_log, services_memory, NULL); assert(state.services);
    configure_resource_backends(argv[1]);
    assert(state.input == input && count(input) == 1);
    if (!strcmp(argv[2], "cadence")) {
        sample_tick(); assert(count(input) == 2);
        botw_host_display_tick(1); botw_host_refresh_input(); assert(count(input) == 2);
        botw_input_sample(input, botw_input_monotonic_ticks()); assert(count(input) == 3);
        pthread_mutex_lock(&lifecycle_lock); host_foreground = 0; pthread_mutex_unlock(&lifecycle_lock);
        sample_tick(); assert(count(input) == 3);
        pthread_mutex_lock(&lifecycle_lock); host_foreground = 1; host_focused = 0; pthread_mutex_unlock(&lifecycle_lock);
        sample_tick(); assert(count(input) == 3);
        pthread_mutex_lock(&lifecycle_lock); host_focused = 1; pthread_mutex_unlock(&lifecycle_lock);
        sample_tick(); assert(count(input) == 4);
    } else {
        pthread_t thread; atomic_store(&running, 1); assert(!pthread_create(&thread, NULL, feed, NULL));
        while (atomic_load(&calls) < 100) sched_yield();
        assert(!cleanup());
        unsigned at = atomic_load(&calls);
        while (atomic_load(&calls) - at < 100) sched_yield();
        atomic_store(&running, 0); assert(!pthread_join(thread, NULL));
        assert(!count(input)); /* Unbound owned buffer must stay untouched. */
        for (unsigned i = 0; i < 100; i++) sample_tick();
        assert(!count(input)); botw_input_destroy(input); return 0;
    }
    assert(!cleanup()); sample_tick(); assert(!count(input));
    botw_input_destroy(input); return 0;
}
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native runtime requires Apple ARM64')
class RuntimeInputCadenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-input-cadence-')
        cls.addClassCleanup(cls.build.cleanup)
        folder = Path(cls.build.name)
        driver = folder / 'cadence.c'
        driver.write_text(DRIVER.replace('RUNTIME_SOURCE', str(ROOT / 'native/botw_host/runtime.c')))
        cls.exe = folder / 'cadence'
        sources = [driver, *(ROOT / 'native/botw_host' / name for name in
                   ('entry.S', 'services.c', 'kernel.c', 'hipc_address.c', 'address_space.c')),
                   ROOT / 'native/botw_input/input.c', *(ROOT / 'native/botw_resources' / name
                   for name in ('romfs.c', 'savefs.c', 'local_user.c'))]
        built = subprocess.run(['xcrun', 'clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                                '-Wl,-export_dynamic', '-framework', 'Security',
                                *(str(s) for s in sources), '-o', str(cls.exe)],
                               capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def check(self, mode):
        with tempfile.TemporaryDirectory(prefix='botw-empty-documents-') as documents:
            result = subprocess.run([str(self.exe), documents, mode], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_display_notifications_do_not_double_sample_and_background_pauses(self):
        self.check('cadence')

    def test_cleanup_serializes_inflight_samples_and_blocks_later_writes(self):
        self.check('cleanup')
