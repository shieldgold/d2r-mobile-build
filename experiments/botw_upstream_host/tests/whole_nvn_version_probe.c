/* Owned CPU buffers and the complete original DeviceGetInteger body only. */
#include "nvn.h"
#include "actual_device_integer_proto.inc"
#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static unsigned unknown_items;
static void owned_log(const char *format, ...) {
    assert(!strcmp(format, "[ERROR] nvnDeviceGetInteger unknown item %#x, returning 0"));
    unknown_items++;
}
#define LOG(...) owned_log(__VA_ARGS__)
#define OLOG(...) ((void)0)
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__)
#include "original_device_info.inc"
#include "actual_device_integer.inc"
static const NVNT_nvnDeviceGetInteger checked_prototype = h_nvnDeviceGetInteger;
static int integer(int item) {
    unsigned char device[128], before[128];
    memset(device, 0xa7, sizeof device);memcpy(before, device, sizeof device);
    struct { uint32_t low; int value; uint32_t high; } output = {0x13572468, -91, 0x24681357};
    checked_prototype((const NVNdevice *)(const void *)device, (NVNdeviceInfo)item, &output.value);
    assert(output.low == 0x13572468 && output.high == 0x24681357);
    assert(!memcmp(device, before, sizeof device));
    return output.value;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "version")) {
        int major = integer(0), minor = integer(1);
        assert(major == 52 && minor == 32);
        assert(major == 52 && minor >= 32); /* Fixed disk consumer predicate. */
        int null_device = -1;checked_prototype(NULL, (NVNdeviceInfo)0, &null_device);
        assert(null_device == 52 && !unknown_items);
    } else if (!strcmp(argv[1], "capabilities")) {
        assert(sizeof g_devinfo == sizeof original_devinfo);
        for (unsigned item = 2; item < sizeof g_devinfo / sizeof *g_devinfo; item++)
            assert(integer((int)item) == original_devinfo[item]);
        assert(!unknown_items);
    } else {
        assert(!strcmp(argv[1], "bounds"));
        assert(sizeof g_devinfo / sizeof *g_devinfo == 0x60);
        assert(integer(0x5f) == original_devinfo[0x5f]);
        const int rejected[] = {0x60, 0x61, -1, INT_MIN, INT_MAX};
        for (unsigned i = 0; i < sizeof rejected / sizeof *rejected; i++) assert(!integer(rejected[i]));
        assert(unknown_items == sizeof rejected / sizeof *rejected);
    }
    puts("WHOLE_NVN_VERSION_CPU_OK");return 0;
}
