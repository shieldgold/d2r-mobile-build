// Minimal host diagnostics for BotW, without inherited game-specific observation hooks.
#include "dm.h"

void dm_prof_add(int id, uint64_t ns, uint64_t bytes) { (void)id; (void)ns; (void)bytes; }
void dm_prof_mark(const char *segment) {
    if (g_flag.bootprof) LOG("Boot %.3f s: %s", (now_ns() - g_t0_ns) / 1e9, segment);
}
void uimsg_subscribe(void (*callback)(const char *, const char *, const char *)) {
    (void)callback;
    die("BotW has no game-specific UI observation bus");
}
