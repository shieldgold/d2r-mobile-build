// Local account hosting for the five imports in BotW 1.9.0.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include "botw_account.h"
#include <stdatomic.h>
#include <stddef.h>

// The SDK handle contains a Uid followed by an opaque manager word. The host
// owns this word because all imported handle consumers are implemented here.
typedef struct { botw_uid uid; uint64_t token; } botw_user_handle;
_Static_assert(sizeof(botw_user_handle) == 24, "BotW UserHandle ABI");
_Static_assert(offsetof(botw_user_handle, token) == 16, "BotW UserHandle manager offset");
#define LOCAL_USER_TOKEN UINT64_C(0x4254575553455231)
static _Atomic int g_account_initialized;
// Host policy: one preselected user; the fallback transfers it only until opened.
static _Atomic int g_account_user_open;

static void write_handle(botw_user_handle *out) {
    const botw_user_handle handle = {botw_local_uid, LOCAL_USER_TOKEN};
    dm_guest_write_prepare(out, sizeof handle);
    memcpy(out, &handle, sizeof handle);
}
HLE_FORCE("_ZN2nn7account10InitializeEv", "BotW: initialize one host-local user", void, botw_account_initialize, void) {
    if (!atomic_exchange(&g_account_initialized, 1))
        LOG("BotW account: one local profile, save storage selected by DM_SAVE");
}
HLE_FORCE("_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE", "BotW: preselect the local user", unsigned, botw_account_try_open, botw_user_handle *out) {
    if (!atomic_load(&g_account_initialized) || !out) return 0;
    write_handle(out);
    atomic_store(&g_account_user_open, 1);
    return 1;
}
HLE_FORCE("_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE", "BotW: resolve a local user handle", Result, botw_account_get_uid, botw_uid *out, const botw_user_handle *handle) {
    if (!atomic_load(&g_account_initialized) || !out || !handle) return RES_FAIL;
    botw_user_handle value;
    memcpy(&value, handle, sizeof value);
    if (value.token != LOCAL_USER_TOKEN || !botw_uid_is_local(&value.uid)) return RES_FAIL;
    dm_guest_write_prepare(out, sizeof *out);
    memcpy(out, &botw_local_uid, sizeof *out);
    return RES_OK;
}
HLE_FORCE("_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi", "BotW: transfer the local startup user", void, botw_account_pop_users, int *count, botw_user_handle *out, int capacity) {
    if (!atomic_load(&g_account_initialized) || !count || capacity < 0 || (capacity > 0 && !out))
        die("BotW account: invalid PopOpenUsers arguments or uninitialized account service");
    if (!capacity && !atomic_load(&g_account_user_open))
        die("BotW account: PopOpenUsers capacity is too small for the startup user");
    int available = !atomic_exchange(&g_account_user_open, 1);
    if (available) write_handle(out);
    dm_guest_write_prepare(count, sizeof *count);
    *count = available;
}
HLE_FORCE("_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi", "BotW: enumerate the local profile", Result, botw_account_list_users, int *count, botw_uid *out, int capacity) {
    if (!atomic_load(&g_account_initialized) || !count || capacity < 0 || (capacity > 0 && !out)) return RES_FAIL;
    if (capacity > 0) {
        size_t bytes = (size_t)capacity * sizeof *out;
        dm_guest_write_prepare(out, bytes);
        memset(out, 0, bytes);
        memcpy(out, &botw_local_uid, sizeof *out);
    }
    dm_guest_write_prepare(count, sizeof *count);
    *count = 1; // The SDK reports total users even when capacity is zero.
    return RES_OK;
}
