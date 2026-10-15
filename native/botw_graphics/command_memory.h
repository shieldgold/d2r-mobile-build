/* Bounded CPU recording representation; not NVIDIA command encoding. */
#ifndef BOTW_COMMAND_MEMORY_H
#define BOTW_COMMAND_MEMORY_H
#include <stdint.h>
enum {
    BW_COMMAND_MEMORY_ALIGNMENT=4, BW_CONTROL_MEMORY_ALIGNMENT=8,
    BW_MIN_COMMAND_MEMORY=512, BW_MIN_CONTROL_MEMORY=256,
    BW_DEFAULT_RECORDING_MEMORY=65536, BW_MAX_RECORDING_MEMORY=16*1024*1024,
    BW_CONTROL_LINK_RESERVE=16, BW_MAX_RECORDING_RECORDS=1024*1024,
    BW_MAX_RECORDING_SPANS=4096
};
typedef struct { uint64_t identity; uint32_t index,reserved; } BWRecordingToken;
typedef struct { uint64_t magic,next; } BWRecordingLink;
#define BW_RECORDING_LINK_MAGIC UINT64_C(0x314b4e494c524342)
#define BW_RECORDING_END_MAGIC UINT64_C(0x31444e454d434242)
_Static_assert(sizeof(BWRecordingToken)==16,"CPU recording token ABI");
_Static_assert(sizeof(BWRecordingLink)==BW_CONTROL_LINK_RESERVE,"CPU control link ABI");
#endif
