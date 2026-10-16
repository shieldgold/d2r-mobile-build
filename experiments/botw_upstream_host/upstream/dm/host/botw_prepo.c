// BotW play reports are unavailable on the offline host; no telemetry is saved.
#define HLE_FORCE_TITLES DM_TM_BOTW
#include "hle.h"
#include <stdatomic.h>

static Result prepo_unavailable(void) {
    static atomic_flag logged = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&logged))
        LOG("BotW play reports: telemetry disabled; report not saved (host failure %#x)", RES_FAIL);
    return RES_FAIL;
}

// Refuse before attempting persistence and leave the report and UID untouched.
// RES_FAIL is host policy, not a recovered native prepo error. The reached BotW
// caller ignores this failure and destroys its temporary report immediately.
HLE_FORCE("_ZN2nn5prepo10PlayReport4SaveERKNS_7account3UidE", "BotW: play-report storage unavailable", Result,
          botw_prepo_save_user, void *report, const void *uid) {
    (void)report; (void)uid;
    return prepo_unavailable();
}
HLE_FORCE("_ZN2nn5prepo10PlayReport4SaveEv", "BotW: play-report storage unavailable", Result,
          botw_prepo_save, void *report) {
    (void)report;
    return prepo_unavailable();
}
