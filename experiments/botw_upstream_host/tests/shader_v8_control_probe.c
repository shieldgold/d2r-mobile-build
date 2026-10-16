/* Owns all bytes; links no runtime, renderer, guest or VM interface. */
#include "shader_v8_control.h"
#include <stdio.h>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "V8_CHECK line %d: %s\n", __LINE__, #x); return 91; } } while (0)
static void put32(uint8_t *p, uint32_t value) { for (unsigned i=0; i<4; ++i) p[i] = (uint8_t)(value >> (8*i)); }
int main(void) {
    uint8_t storage[2002], before[2002]; memset(storage, 0xa5, sizeof storage);
    uint8_t *c = storage + 1; memset(c, 0, 2000);
    const unsigned offsets[] = {0,4,8,12,16,20,0x20,0x6f0,0x708,0x714};
    const uint32_t values[] = {0x98761234,1,8,0x120,11,2000,2000,2000,0x30,0};
    for (unsigned i=0; i<10; ++i) put32(c+offsets[i], values[i]);
    memcpy(before, storage, sizeof storage);
    CHECK(botw_base_v0_shader_control_v8(c,2000));
    CHECK(!botw_base_v0_shader_control_v8(NULL,2000));
    CHECK(!botw_base_v0_shader_control_v8(c,1999));
    CHECK(!botw_base_v0_shader_control_v8(c,2001));
    for (unsigned i=0; i<9; ++i) {
        c[offsets[i]] ^= 1; CHECK(!botw_base_v0_shader_control_v8(c,2000)); c[offsets[i]] ^= 1;
    }
    for (unsigned stage=0; stage<6; ++stage) { put32(c+0x714,stage); CHECK(botw_base_v0_shader_control_v8(c,2000) == (stage<=2)); }
    put32(c+0x714,0); CHECK(!memcmp(before,storage,sizeof storage));
    puts("V8_PROFILE_CPU_OK"); return 0;
}
