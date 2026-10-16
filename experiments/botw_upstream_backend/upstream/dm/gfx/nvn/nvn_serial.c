// Shared object serials used by NVN objects and backend-only test builds.
#include <stdatomic.h>
#include <stdint.h>

uint64_t nvn_new_serial(void) {
    static _Atomic uint64_t serial;
    return ++serial;
}
