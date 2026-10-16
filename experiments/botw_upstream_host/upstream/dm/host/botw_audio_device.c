// BotW 1.9.0 AudioDevice service, backed by a fixed stereo host default route.
// SDK 20.5.22 ABI: names occupy 256 bytes; notification acquisition returns Result.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include "botw_audio_renderer.h"
#include <math.h>
#include <stdatomic.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

enum { DEVICE_LIST, DEVICE_VOLUME, DEVICE_CHANNELS, DEVICE_NOTIFY, DEVICE_CREATE_EVENT, DEVICE_FUNCTIONS };
static const struct { const char *name; uint64_t offset; } device_exports[DEVICE_FUNCTIONS] = {
    {"_ZN2nn5audio19ListAudioDeviceNameEPNS0_15AudioDeviceNameEi", 0x1f5f60},
    {"_ZN2nn5audio26SetAudioDeviceOutputVolumeEPKNS0_15AudioDeviceNameEf", 0x1f6040},
    {"_ZN2nn5audio41GetActiveAudioDeviceChannelCountForOutputEv", 0x1f6c10},
    {"_ZN2nn5audio39AcquireAudioDeviceNotificationForOutputEPNS_2os11SystemEventE", 0x1f6b50},
    {"_ZN2nn2os17CreateSystemEventEPNS0_15SystemEventTypeENS0_14EventClearModeEb", 0x13cc90},
};
static _Atomic(void *) device_native[DEVICE_FUNCTIONS];
static void *device_original(int index) {
    void *fn = atomic_load(&device_native[index]);
    if (fn) return fn;
    for (int i = 0; i < g_nmod; i++) if (!strcmp(g_mod[i].name, "sdk")) {
        fn = module_sym(&g_mod[i], device_exports[index].name);
        if (!fn || (botw_audio_renderer_cpu_enabled() && (uint8_t *)fn != g_mod[i].base + device_exports[index].offset))
            die("BotW AudioDevice: unsupported SDK export layout for %s", device_exports[index].name);
        atomic_store(&device_native[index], fn);
        return fn;
    }
    die("BotW AudioDevice: SDK module is unavailable");
}
static int device_read(const void *source, void *out, size_t size) {
    mach_vm_size_t got = 0;
    return source && mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)(uintptr_t)source, size,
                                            (mach_vm_address_t)(uintptr_t)out, &got) == KERN_SUCCESS && got == size;
}
enum { DEVICE_NAME_SIZE = 256, DEVICE_ROUTES = 3, DEVICE_ACTIVE = 2 };
// These are SDK logical route identifiers, not a claim that all three physical devices exist.
// The host default output is exposed through the recognized built-in alias. Inactive route
// gains are retained independently; changing them never changes the macOS system volume.
static const char *const device_names[DEVICE_ROUTES] = {
    "AudioTvOutput", "AudioStereoJackOutput", "AudioBuiltInSpeakerOutput"
};
static float device_volumes[DEVICE_ROUTES] = {1, 1, 1};
static pthread_mutex_t device_lock = PTHREAD_MUTEX_INITIALIZER;
static int notification_acquired;

HLE_FORCE("_ZN2nn5audio19ListAudioDeviceNameEPNS0_15AudioDeviceNameEi", "BotW: host logical output routes", int,
          botw_device_list, void *names, int capacity) {
    if (!botw_audio_renderer_cpu_enabled()) return ((int (*)(void *, int))device_original(DEVICE_LIST))(names, capacity);
    if (capacity <= 0 || !names) return 0;   // Host policy for an empty/invalid destination.
    int count = capacity < DEVICE_ROUTES ? capacity : DEVICE_ROUTES;
    for (int i = 0; i < count; i++) {
        char *record = (char *)names + i * DEVICE_NAME_SIZE;
        memset(record, 0, DEVICE_NAME_SIZE);
        memcpy(record, device_names[i], strlen(device_names[i]) + 1);
    }
    LOG("BotW AudioDevice: listed %d logical routes; host default output is stereo", count);
    return count;
}
HLE_FORCE("_ZN2nn5audio26SetAudioDeviceOutputVolumeEPKNS0_15AudioDeviceNameEf", "BotW: per-route host PCM gain", void,
          botw_device_volume, const void *name, float volume) {
    if (!botw_audio_renderer_cpu_enabled()) { ((void (*)(const void *, float))device_original(DEVICE_VOLUME))(name, volume); return; }
    char copy[DEVICE_NAME_SIZE];
    if (!device_read(name, copy, sizeof copy) || !memchr(copy, 0, sizeof copy)) die("BotW AudioDevice: unreadable or unterminated device name");
    int route = -1;
    for (int i = 0; i < DEVICE_ROUTES; i++) if (!strcmp(copy, device_names[i])) route = i;
    if (route < 0) die("BotW AudioDevice: unsupported logical output route");
    if (!isfinite(volume)) die("BotW AudioDevice: non-finite output gain is unsupported");
    if (volume < 0) volume = 0;
    if (volume > 128) volume = 128;
    pthread_mutex_lock(&device_lock);
    device_volumes[route] = volume;
    if (route == DEVICE_ACTIVE) botw_audio_renderer_set_output_volume(volume);
    pthread_mutex_unlock(&device_lock);
    LOG("BotW AudioDevice: %s gain %.6g (%s)", device_names[route], (double)volume,
        route == DEVICE_ACTIVE ? "active host output" : "inactive logical route");
}
HLE_FORCE("_ZN2nn5audio41GetActiveAudioDeviceChannelCountForOutputEv", "BotW: fixed stereo host output", int,
          botw_device_channels, void) {
    if (!botw_audio_renderer_cpu_enabled()) return ((int (*)(void))device_original(DEVICE_CHANNELS))();
    return 2;
}
HLE_FORCE("_ZN2nn5audio39AcquireAudioDeviceNotificationForOutputEPNS_2os11SystemEventE", "BotW: fixed-route local notification event", Result,
          botw_device_notification, void *event) {
    if (!botw_audio_renderer_cpu_enabled()) return ((Result (*)(void *))device_original(DEVICE_NOTIFY))(event);
    pthread_mutex_lock(&device_lock);
    if (notification_acquired) { pthread_mutex_unlock(&device_lock); return 0x499; }
    uintptr_t address = (uintptr_t)event; uint8_t tag = 0;
    if (!event || address > UINTPTR_MAX - 0x28 || !device_read((const void *)(address + 0x28), &tag, 1) || tag)
        die("BotW AudioDevice: notification event is unreadable or already initialized");
    Result result = ((Result (*)(void *, int, unsigned))device_original(DEVICE_CREATE_EVENT))(event, 1, 0);
    if (!result) notification_acquired = 1;
    pthread_mutex_unlock(&device_lock);
    LOG("BotW AudioDevice: local auto-clear notification event result %#x; logical output route stays fixed", result);
    return result;   // Caller owns destruction; destroying it does not reset this singleton.
}
