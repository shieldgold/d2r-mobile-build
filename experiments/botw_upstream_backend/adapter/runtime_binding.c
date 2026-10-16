/* SPDX-License-Identifier: MIT
 * Real current-runtime mapping, not a fallback or test implementation. */
#include "host_adapter.h"
#include "../../../native/botw_host/runtime.h"

int botw_backend_runtime_prepare(void *address, size_t bytes, char *error, size_t capacity) {
    return botw_host_prepare_guest_write(address, bytes, error, capacity);
}
int botw_backend_runtime_register(botw_backend_fault fault, botw_backend_prepare prepare) {
    return botw_host_register_memory_tracking(fault, prepare);
}
