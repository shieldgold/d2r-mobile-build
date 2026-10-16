#pragma once
#include <pthread.h>
#include <stdint.h>

typedef struct {
    uint64_t buttons;
    int32_t lx, ly, rx, ry;
} whole_front_pad;

typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    int runtime_ready, surface_ready, active, focused, audio_ready, platform_ready;
    int started, failed, stopped;
    whole_front_pad touch;
    void (*vsync)(void);
} whole_front_state;

#define WHOLE_FRONT_STATE_INITIALIZER { .lock = PTHREAD_MUTEX_INITIALIZER, .changed = PTHREAD_COND_INITIALIZER }

void whole_front_state_runtime(whole_front_state *state, int ready);
void whole_front_state_surface(whole_front_state *state, int ready);
void whole_front_state_lifecycle(whole_front_state *state, int foreground, int focused,
                                 int audio_ready, int platform_result);
int whole_front_state_claim_start(whole_front_state *state);
/* Worker threads wait through temporary inactivity. Returns 0 when ready or -1
 * after platform failure, permanent stop, or a condition-variable failure. */
int whole_front_state_wait_ready(whole_front_state *state);
void whole_front_state_button(whole_front_state *state, uint64_t button, int down);
void whole_front_state_stick(whole_front_state *state, int right, float x, float y);
int whole_front_state_touch(whole_front_state *state, whole_front_pad *out);
void whole_front_state_bind_vsync(whole_front_state *state, void (*callback)(void));
void whole_front_state_display_tick(whole_front_state *state);
void whole_front_state_stop(whole_front_state *state);
