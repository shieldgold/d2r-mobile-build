#pragma once
#include <stdint.h>
#include <string.h>

// One host-local profile owns DM_SAVE across launches. This is not a console UID.
typedef struct { uint64_t low, high; } botw_uid;
static const botw_uid botw_local_uid = {1, 0};
_Static_assert(sizeof(botw_uid) == 16, "BotW account Uid ABI");
static inline int botw_uid_is_local(const void *value) {
    if (!value) return 0;
    botw_uid uid;
    memcpy(&uid, value, sizeof uid);
    return uid.low == botw_local_uid.low && uid.high == botw_local_uid.high;
}
