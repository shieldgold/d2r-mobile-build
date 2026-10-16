/* Owned metadata and bytes only; no encoder, Guest memory, or Metal device. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nvn.h"
#define NVN(name, result, ...) static result h_##name(__VA_ARGS__)
#include "actual_get_image_handle.inc"
#undef NVN

/* This declaration is copied verbatim from the original replay state. */
static struct {
#include "actual_handle_arrays.inc"
} S;
#include "actual_fill_driver_bank2.inc"

static int check(int condition, const char *message) {
    if (!condition) { fprintf(stderr, "%s\n", message); return 91; }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (!strncmp(argv[1], "handle-", 7)) {
        static const int ids[] = {0, 27, INT32_MAX, INT32_MIN, -1, -7};
        int index = atoi(argv[1] + 7);
        if (index < 0 || index >= (int)(sizeof ids / sizeof ids[0])) return 2;
        uint64_t expected = (uint64_t)(int64_t)(int32_t)ids[index] | UINT64_C(0x100000000);
        /* NULL is intentional: the real leaf never reads the device object. */
        return check(h_nvnDeviceGetImageHandle(NULL, ids[index]) == expected,
                     "actual GetImageHandle differs from fixed-base sign extension and marker");
    }
    int stage = atoi(argv[1]);
    int images = stage == 0 || stage == 1;
    if (stage < 0 || stage >= 6) return 2;
    for (int s = 0; s < 6; ++s) {
        for (int i = 0; i < 128; ++i)
            S.tex[s][i] = UINT64_C(0xabc0000000000000) | (uint64_t)s << 24 | (unsigned)i << 8 | 0x53;
        for (int i = 0; i < 32; ++i)
            S.img[s][i] = UINT64_C(0x8765432100000000) | (uint64_t)s << 24 | (unsigned)i << 8 | 0x97;
    }
    unsigned char before[sizeof S]; memcpy(before, &S, sizeof S);
    /* A one-byte offset proves that no new aligned uint64_t store is required. */
    uint8_t storage[0xa02], expected[0xa00];
    memset(storage, 0xa5, sizeof storage); memset(expected, 0, sizeof expected);
    uint8_t *bytes = storage + 1; memset(bytes, 0, sizeof expected);
    for (int i = 0; i < 32; ++i) {
        uint32_t handle = (uint32_t)S.tex[stage][i];
        memcpy(expected + 0x20 + 8 * i, &handle, sizeof handle);
    }
    if (images) for (int i = 0; i < 8; ++i)
        memcpy(expected + 0x120 + 8 * i, &S.img[stage][i], sizeof S.img[stage][i]);
    fill_driver_bank2(bytes, stage);
    int result = check(!memcmp(bytes, expected, sizeof expected),
                       "actual bank2 bytes differ from the fixed graphics handle contract");
    if (result) return result;
    if ((result = check(storage[0] == 0xa5 && storage[sizeof storage - 1] == 0xa5,
                        "bank2 helper wrote outside its arena"))) return result;
    if ((result = check(!memcmp(before, &S, sizeof S), "bank2 helper changed the original bindings"))) return result;
    /* The same helper must observe the next normal metadata snapshot. */
    S.tex[stage][0] = UINT64_C(0xfedcba9812345678);
    S.img[stage][0] = UINT64_C(0x123456789abcdef0);
    memset(bytes, 0, sizeof expected); fill_driver_bank2(bytes, stage);
    uint32_t texture; uint64_t image;
    memcpy(&texture, bytes + 0x20, sizeof texture);
    memcpy(&image, bytes + 0x120, sizeof image);
    if ((result = check(texture == UINT32_C(0x12345678), "texture metadata refresh was lost"))) return result;
    return check(image == (images ? S.img[stage][0] : 0), "image metadata refresh or stage restriction was lost");
}
