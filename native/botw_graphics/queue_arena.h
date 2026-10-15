/* Private CPU queue storage contract. This is not NVIDIA GPU command memory. */
#ifndef BOTW_QUEUE_ARENA_H
#define BOTW_QUEUE_ARENA_H
#include <stdint.h>
#include <stddef.h>
enum {
    BW_QUEUE_ARENA_PAGE=4096,
    BW_QUEUE_DEFAULT_COMMAND_BYTES=65536,
    BW_QUEUE_DEFAULT_CONTROL_BYTES=65536,
    BW_QUEUE_MAX_PARTITION_BYTES=16*1024*1024
};
#define BW_QUEUE_ARENA_MAGIC UINT64_C(0x31414e4552415142)
typedef struct {
    uint64_t magic,version,totalBytes,commandBytes,controlBytes,identity;
    uint64_t preparedSubmissions,commandUsed,controlUsed;
} BWQueueArenaHeader;
typedef struct {
    uint32_t handleIndex,commandIndex;
    uint64_t reserved;
} BWQueueReplayIndex;
typedef struct { size_t totalBytes,commandBytes,controlBytes; } BWQueueArenaLayout;
_Static_assert(sizeof(BWQueueReplayIndex)==16,"CPU replay index ABI");
_Static_assert(sizeof(BWQueueArenaHeader)<=BW_QUEUE_ARENA_PAGE,"CPU queue header page");
_Static_assert(sizeof(uint64_t)==8,"CPU queue handle ABI");
_Static_assert(BW_QUEUE_MAX_PARTITION_BYTES/sizeof(uint64_t)<=UINT32_MAX,"CPU handle index width");
_Static_assert(BW_QUEUE_MAX_PARTITION_BYTES/sizeof(BWQueueReplayIndex)<=UINT32_MAX,"CPU replay index width");
#endif
