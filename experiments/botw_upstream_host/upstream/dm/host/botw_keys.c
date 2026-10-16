// DM_KEYS scripted controller input for unattended runs, a port of nxnative's hle_hid.c DM_KEYS:
//   "seconds:key[+key][~hold seconds][*count/interval seconds],..."
//   e.g. "40:A,42:LUp~3,50:A*20/1.5" (A at 40 s; left stick up for 3 s at 42 s; A every 1.5 s from 50 s, 20 times).
// Buttons: A B X Y LS RS L R ZL ZR Plus Minus Left Up Right Down. Sticks at full deflection: LUp LDown LLeft LRight
// RUp RDown RLeft RRight. Held 0.15 s by default; at most 1024 steps after repeats; times count from host start.
// The script is one more controller (botw_hid_pad_set), ORed with keyboard, mouse and real controllers.
#include "botw_keys.h"
#include "botw_hid.h"
#include "dm.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#define KEY_STEPS 1024
static const struct { const char *name; uint64_t button; int stick, x, y; } g_keys[] = {
    {"A", BOTW_BTN_A, 0, 0, 0}, {"B", BOTW_BTN_B, 0, 0, 0}, {"X", BOTW_BTN_X, 0, 0, 0}, {"Y", BOTW_BTN_Y, 0, 0, 0}, {"LS", BOTW_BTN_LS, 0, 0, 0}, {"RS", BOTW_BTN_RS, 0, 0, 0},
    {"L", BOTW_BTN_L, 0, 0, 0}, {"R", BOTW_BTN_R, 0, 0, 0}, {"ZL", BOTW_BTN_ZL, 0, 0, 0}, {"ZR", BOTW_BTN_ZR, 0, 0, 0}, {"Plus", BOTW_BTN_PLUS, 0, 0, 0},
    {"Minus", BOTW_BTN_MINUS, 0, 0, 0}, {"Left", BOTW_BTN_LEFT, 0, 0, 0}, {"Up", BOTW_BTN_UP, 0, 0, 0}, {"Right", BOTW_BTN_RIGHT, 0, 0, 0}, {"Down", BOTW_BTN_DOWN, 0, 0, 0},
    {"LUp", 0, 1, 0, 1}, {"LDown", 0, 1, 0, -1}, {"LLeft", 0, 1, -1, 0}, {"LRight", 0, 1, 1, 0},
    {"RUp", 0, 2, 0, 1}, {"RDown", 0, 2, 0, -1}, {"RLeft", 0, 2, -1, 0}, {"RRight", 0, 2, 1, 0},
};

static int fail(char *err, size_t n, const char *what, const char *tok) { if (err && n) snprintf(err, n, "%s: %s", what, tok); return -1; }

static int number(const char *s, double *v) { char *end; *v = strtod(s, &end); return end != s && !*end && *v >= 0 && *v < 1e6; }

int botw_keys_parse(const char *spec, botw_key_step *out, int cap, char *err, size_t err_size) {
    if (!spec) return 0;
    size_t len = strlen(spec);
    char *buf = malloc(len + 1); if (!buf) return fail(err, err_size, "out of memory", "");
    memcpy(buf, spec, len + 1);
    int n = 0, rc = 0;
    char *save = NULL;
    for (char *tok = strtok_r(buf, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
        char *colon = strchr(tok, ':');
        botw_key_step st = {0, 0.15, 0, 0, 0, 0, 0};
        double rep = 1, every = 0;
        if (!colon) { rc = fail(err, err_size, "step without seconds:", tok); break; }
        *colon = 0;
        if (!number(tok, &st.at)) { rc = fail(err, err_size, "invalid seconds", tok); break; }
        char *keys = colon + 1, *star = strchr(keys, '*'), *tilde;
        if (star) {
            *star = 0; char *slash = strchr(star + 1, '/');
            if (!slash) { rc = fail(err, err_size, "repeat needs count/interval", star + 1); break; }
            *slash = 0;
            if (!number(star + 1, &rep) || rep < 1 || rep != (int)rep || !number(slash + 1, &every) || every <= 0) { rc = fail(err, err_size, "invalid repeat", star + 1); break; }
        }
        if ((tilde = strchr(keys, '~'))) {
            *tilde = 0;
            if (!number(tilde + 1, &st.hold) || st.hold <= 0) { rc = fail(err, err_size, "invalid hold", tilde + 1); break; }
        }
        for (char *k = keys; k && *k;) {
            char *plus = strchr(k, '+'); if (plus) *plus = 0;
            size_t i = 0;
            while (i < sizeof g_keys / sizeof *g_keys && strcasecmp(k, g_keys[i].name)) i++;
            if (i == sizeof g_keys / sizeof *g_keys) { rc = fail(err, err_size, "unknown key", k); break; }
            st.buttons |= g_keys[i].button;
            if (g_keys[i].stick == 1) { st.lx = g_keys[i].x * BOTW_STICK_MAX; st.ly = g_keys[i].y * BOTW_STICK_MAX; }
            if (g_keys[i].stick == 2) { st.rx = g_keys[i].x * BOTW_STICK_MAX; st.ry = g_keys[i].y * BOTW_STICK_MAX; }
            k = plus ? plus + 1 : NULL;
        }
        if (rc) break;
        if (!st.buttons && !st.lx && !st.ly && !st.rx && !st.ry) { rc = fail(err, err_size, "step without keys", keys); break; }
        for (int i = 0; i < (int)rep; i++) {
            if (n == cap) { rc = fail(err, err_size, "too many steps", keys); break; }
            out[n] = st; out[n++].at = st.at + i * every;
        }
        if (rc) break;
    }
    free(buf);
    return rc ? rc : n;
}

static botw_key_step g_steps[KEY_STEPS];
static int g_nsteps;
extern uint64_t g_t0_ns;
uint64_t now_ns(void);

static void *keys_main(void *arg) {
    (void)arg;
    uintptr_t who = (uintptr_t)&g_steps;
    for (int i = 0; i < g_nsteps; i++) {
        while ((now_ns() - g_t0_ns) / 1e9 < g_steps[i].at) usleep(10000);
        const botw_key_step *s = &g_steps[i];
        LOG("DM_KEYS: %.2f s buttons %#llx sticks %d,%d %d,%d for %.2f s", s->at, (unsigned long long)s->buttons, s->lx, s->ly, s->rx, s->ry, s->hold);
        botw_pad_state p = {s->buttons, s->lx, s->ly, s->rx, s->ry};
        botw_hid_pad_set(who, &p);
        usleep((useconds_t)(s->hold * 1e6));
        botw_hid_pad_drop(who);
    }
    return NULL;
}

static void keys_start_once(void) {
    char err[256];
    int n = botw_keys_parse(getenv("DM_KEYS"), g_steps, KEY_STEPS, err, sizeof err);
    if (n < 0) die("DM_KEYS: %s", err);
    // Steps run in time order (insertion sort keeps equal times in script order).
    for (int i = 1; i < n; i++)
        for (int j = i; j > 0 && g_steps[j].at < g_steps[j - 1].at; j--) { botw_key_step t = g_steps[j]; g_steps[j] = g_steps[j - 1]; g_steps[j - 1] = t; }
    g_nsteps = n;
    pthread_t th;
    if (pthread_create(&th, NULL, keys_main, NULL)) die("DM_KEYS: cannot start the script thread");
    pthread_detach(th);
    LOG("DM_KEYS: %d steps", n);
}
void botw_keys_start(void) {
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    const char *spec = getenv("DM_KEYS");
    if (spec && *spec) pthread_once(&once, keys_start_once);
}
