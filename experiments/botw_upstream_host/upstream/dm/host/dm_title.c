// Title selection (DM_TITLE), where the module list and argv[0] come from, bounded runs (DM_STOP_ON_UNIMPL). For the meaning of the descriptor fields see dm_title.h.
#define DM_TITLE_STRONG
#include "dm.h"
#include "dm_title.h"
#include <mach-o/getsect.h>
#include <mach-o/ldsyms.h>
#include <unistd.h>
#include <ctype.h>

int g_dm_title = DM_TITLE_BOTW;   // Strong definition, overrides the weak one in dm.h
static const dm_title_desc *g_cur;
static pthread_once_t g_once = PTHREAD_ONCE_INIT;

static void select_once(void) {
    unsigned long sz = 0;
    const dm_title_desc *const *tab = (const dm_title_desc *const *)getsectiondata(&_mh_execute_header, "__DATA", "swtitle", &sz);
    unsigned n = tab ? (unsigned)(sz / sizeof *tab) : 0;
    const char *want = getenv("DM_TITLE");
    if (!want || !*want) want = "botw";   // This fork builds and boots BotW by default
    for (unsigned i = 0; i < n; i++)
        if (!strcmp(tab[i]->name, want)) { g_cur = tab[i]; break; }
    if (!g_cur) {
        char have[128] = ""; size_t p = 0;
        for (unsigned i = 0; i < n && p < sizeof have - 16; i++) p += snprintf(have + p, sizeof have - p, "%s%s", i ? " " : "", tab[i]->name);
        die("[ERROR] DM_TITLE=%s: this host binary has no registered title of that name (registered: %s)", want, n ? have : "none");
    }
    g_dm_title = g_cur->id;
    if (g_cur->id != DM_TITLE_D2R) LOG("title: %s (ProgramId %016llx)", g_cur->name, (unsigned long long)g_cur->program_id);
    if (g_cur->on_select) g_cur->on_select();
}
void dm_title_select(void) { pthread_once(&g_once, select_once); }
const dm_title_desc *dm_title(void) { dm_title_select(); return g_cur; }

const char *dm_title_argv0(void) {
    const char *e = getenv("DM_ARGV0");
    return e && *e ? e : dm_title()->argv0;
}

int dm_title_modules(const char **names, int n_cmdline, int cap) {
    if (cap < 1 || n_cmdline < 0 || n_cmdline > cap) die("invalid module count");
    int n = n_cmdline;
    const char *e = getenv("DM_MODULES");
    if (!n && e && *e) {
        static char buf[256];
        if (strlcpy(buf, e, sizeof buf) >= sizeof buf) die("DM_MODULES is too long");
        for (char *s = buf, *tok; (tok = strsep(&s, ", :\t\n"));) if (*tok) {
            if (n == cap) die("too many modules (maximum %d)", cap);
            names[n++] = tok;
        }
        if (!n) die("DM_MODULES=%s: no module names", e);
    }
    if (!n) for (const char *const *m = dm_title()->modules; *m; m++) {
        if (n == cap) die("too many default modules (maximum %d)", cap);
        names[n++] = *m;
    }
    // The loader stores names in fixed-size arrays; reject truncation, duplicates and guest rtld.
    for (int i = 0; i < n; i++) {
        if (!names[i] || !*names[i] || strlen(names[i]) >= sizeof g_mod[0].name) die("invalid module name");
        if (!strcmp(names[i], "rtld")) die("rtld must not be loaded: the host provides it");
        for (const unsigned char *s = (const unsigned char *)names[i]; *s; s++)
            if (!isalnum(*s) && *s != '_') die("invalid module name: %s", names[i]);
        for (int j = 0; j < i; j++) if (!strcmp(names[i], names[j])) die("duplicate module: %s", names[i]);
    }
    return n;
}

int dm_stop_on_unimpl(void) {
    static int v = -1;
    if (v < 0) { const char *e = getenv("DM_STOP_ON_UNIMPL"); v = e && *e && strcmp(e, "0") != 0; }
    return v;
}
// A bounded run stops at the first unimplemented thing: first print the caller as "module+offset", then a frame-pointer backtrace; no graphics / audio teardown, exit directly with 3
void dm_unimpl_hit(const char *what, uint64_t lr, uint64_t fp) {
    if (!dm_stop_on_unimpl()) return;
    module_t *m = module_of((void *)lr);
    if (m) LOG("[STOP] DM_STOP_ON_UNIMPL: %s, caller %s+%#llx", what, m->name, (unsigned long long)(lr - (uint64_t)m->base));
    else LOG("[STOP] DM_STOP_ON_UNIMPL: %s, caller %#llx (not inside a guest module)", what, (unsigned long long)lr);
    if (fp) dm_backtrace(fp);
    fflush(stderr);
    _exit(3);
}
