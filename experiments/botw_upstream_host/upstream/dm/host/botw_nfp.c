// BotW NFP client lifecycle with no host NFC readers.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include <stdatomic.h>

static _Atomic int nfp_initialized;
static atomic_flag nfp_enumeration_logged = ATOMIC_FLAG_INIT;
// BotW 1.9.0 handles this Result as an unavailable reader. Ryubing's service
// names it DeviceNotFound; the SDK forwards the service result unchanged.
#define NFP_DEVICE_NOT_FOUND 0x8073u

HLE_FORCE("_ZN2nn3nfp10InitializeEv", "BotW: initialize NFP without NFC hardware", void,
          botw_nfp_initialize, void) {
    if (atomic_exchange(&nfp_initialized, 1)) die("BotW NFP: duplicate initialization");
    LOG("BotW NFP: initialized; no host NFC readers are available");
}
HLE_FORCE("_ZN2nn3nfp8FinalizeEv", "BotW: finalize the host NFP client", void,
          botw_nfp_finalize, void) {
    if (!atomic_exchange(&nfp_initialized, 0)) die("BotW NFP: finalization before initialization");
    LOG("BotW NFP: finalized");
}
HLE_FORCE("_ZN2nn3nfp8GetStateEv", "BotW: report host NFP client state", int,
          botw_nfp_get_state, void) {
    return atomic_load(&nfp_initialized);
}
HLE_FORCE("_ZN2nn3nfp11ListDevicesEPNS0_12DeviceHandleEPii", "BotW: report no NFC readers", Result,
          botw_nfp_list_devices, void *devices, int *count, int capacity) {
    if (!atomic_load(&nfp_initialized)) die("BotW NFP: device enumeration before initialization");
    // The SDK writes the count only on success. Leave both outputs untouched
    // on the unavailable-reader path; do not invent handles or tag events.
    (void)devices; (void)count; (void)capacity;
    if (!atomic_flag_test_and_set(&nfp_enumeration_logged))
        LOG("BotW NFP: device enumeration reports no reader (Result %#x)", NFP_DEVICE_NOT_FOUND);
    return NFP_DEVICE_NOT_FOUND;
}
