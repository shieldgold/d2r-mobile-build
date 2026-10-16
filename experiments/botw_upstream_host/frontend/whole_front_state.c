#include "whole_front_state.h"
#include "whole_frontend.h"
#include <math.h>
#include <string.h>

int whole_frontend_runtime_contract_valid(const whole_frontend_runtime *runtime) {
    if (!runtime || runtime->abi != WHOLE_FRONTEND_RUNTIME_ABI || !runtime->prepare ||
        !runtime->lifecycle || !runtime->host_main || runtime->host_argc < 2 ||
        runtime->host_argc > 64 || !runtime->host_argv) return 0;
    for (int i = 0; i < runtime->host_argc; ++i)
        if (!runtime->host_argv[i] || !runtime->host_argv[i][0]) return 0;
    return 1;
}

static int ready_locked(const whole_front_state *s) {
    return s->runtime_ready && s->surface_ready && s->active && s->focused &&
           s->audio_ready && s->platform_ready && !s->failed && !s->stopped;
}
static void release_locked(whole_front_state *s) { memset(&s->touch, 0, sizeof s->touch); }

void whole_front_state_runtime(whole_front_state *s, int ready) {
    pthread_mutex_lock(&s->lock);
    s->runtime_ready = !!ready;
    if (!ready_locked(s)) release_locked(s);
    pthread_cond_broadcast(&s->changed);
    pthread_mutex_unlock(&s->lock);
}
void whole_front_state_surface(whole_front_state *s, int ready) {
    pthread_mutex_lock(&s->lock);
    s->surface_ready = !!ready;
    if (!ready_locked(s)) release_locked(s);
    pthread_cond_broadcast(&s->changed);
    pthread_mutex_unlock(&s->lock);
}
void whole_front_state_lifecycle(whole_front_state *s, int foreground, int focused,
                                 int audio_ready, int platform_result) {
    pthread_mutex_lock(&s->lock);
    s->active = !!foreground;
    s->focused = foreground && focused;
    s->audio_ready = foreground && audio_ready;
    s->platform_ready = platform_result == 0;
    if (platform_result) s->failed = 1;
    if (!ready_locked(s)) release_locked(s);
    pthread_cond_broadcast(&s->changed);
    pthread_mutex_unlock(&s->lock);
}
int whole_front_state_claim_start(whole_front_state *s) {
    pthread_mutex_lock(&s->lock);
    int start = ready_locked(s) && !s->started;
    if (start) s->started = 1;
    pthread_mutex_unlock(&s->lock);
    return start;
}
int whole_front_state_wait_ready(whole_front_state *s) {
    pthread_mutex_lock(&s->lock);
    int error = 0;
    while (!ready_locked(s) && !s->failed && !s->stopped && !error)
        error = pthread_cond_wait(&s->changed, &s->lock);
    if (error) {
        s->failed = 1;
        release_locked(s);
        pthread_cond_broadcast(&s->changed);
    }
    int result = ready_locked(s) ? 0 : -1;
    pthread_mutex_unlock(&s->lock);
    return result;
}
void whole_front_state_button(whole_front_state *s, uint64_t button, int down) {
    pthread_mutex_lock(&s->lock);
    if (ready_locked(s)) {
        if (down) s->touch.buttons |= button;
        else s->touch.buttons &= ~button;
    }
    pthread_mutex_unlock(&s->lock);
}
void whole_front_state_stick(whole_front_state *s, int right, float x, float y) {
    if (!isfinite(x) || !isfinite(y)) x = y = 0;
    double length = hypot((double)x, (double)y);
    if (length > 1) { x = (float)(x / length); y = (float)(y / length); }
    if (length < 0.10) x = y = 0;
    int32_t ix = (int32_t)lroundf(x * 32767), iy = (int32_t)lroundf(y * 32767);
    pthread_mutex_lock(&s->lock);
    if (ready_locked(s)) {
        if (right) { s->touch.rx = ix; s->touch.ry = iy; }
        else { s->touch.lx = ix; s->touch.ly = iy; }
    }
    pthread_mutex_unlock(&s->lock);
}
int whole_front_state_touch(whole_front_state *s, whole_front_pad *out) {
    pthread_mutex_lock(&s->lock);
    int ready = ready_locked(s);
    if (out) {
        if (ready) *out = s->touch;
        else memset(out, 0, sizeof *out);
    }
    pthread_mutex_unlock(&s->lock);
    return ready;
}
void whole_front_state_bind_vsync(whole_front_state *s, void (*callback)(void)) {
    pthread_mutex_lock(&s->lock);
    if (!s->stopped) s->vsync = callback;
    pthread_mutex_unlock(&s->lock);
}
void whole_front_state_display_tick(whole_front_state *s) {
    pthread_mutex_lock(&s->lock);
    void (*callback)(void) = ready_locked(s) ? s->vsync : NULL;
    pthread_mutex_unlock(&s->lock);
    if (callback) callback();
}
void whole_front_state_stop(whole_front_state *s) {
    pthread_mutex_lock(&s->lock);
    s->stopped = 1;
    s->vsync = NULL;
    release_locked(s);
    pthread_cond_broadcast(&s->changed);
    pthread_mutex_unlock(&s->lock);
}
