// BotW title identity, module order and explicitly selected shared startup services.
// SDK-dependent startup behavior is documented in docs/BOTW_HOST_BOOT.md.
#define HLE_FORCE_TITLES DM_TM_BOTW   // The forced takeover entries in this file belong to Breath of the Wild only
#include "hle.h"
#include "dm_title.h"

// Native time startup installs a libc callback backed by Horizon time services.
// Keep the SDK counter callback; botw_time.c hosts the imported nn::time APIs.
HLE_FORCE("nntimeStartup", "BotW: retain the SDK counter callback with host time APIs", void, botw_time_startup, void) {
    LOG("BotW nntimeStartup: retain SDK counter callback; nn::time APIs use host clocks");
}

static const char *const botw_force_also[] = {
    "nndiagStartup",
    "_ZN2nn2oe10InitializeEv",
    "_ZN2nn3aoc10InitializeEv",
    "_ZN2nn3err26InitializeContextWriterApiEv",
    "_ZN2nn2fs24InitializeForApplicationEv",
    "_ZN2nn4pctl10InitializeEv",
    "_ZN2nn4diag34InitializeApplicationAbortObserverEv",
    NULL,
};
static const char *const botw_modules[] = {"main", "subsdk0", "sdk", NULL};
static const dm_title_desc botw_title = {
    .id = DM_TITLE_BOTW, .name = "botw", .argv0 = "BotW", .program_id = 0x01007EF00011E000ull, .modules = botw_modules,
    .force_also = botw_force_also,
    // All other hooks empty: no Diablo II probes / tick gate / prefetch / cut host calls / thread parameter slots / App form
};
DM_TITLE_REGISTER(botw_title);
