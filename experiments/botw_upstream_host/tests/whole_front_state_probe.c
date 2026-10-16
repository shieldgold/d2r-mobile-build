#include "whole_front_state.h"
#include "whole_frontend.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <time.h>

#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "behavior check failed at line %d: %s\n", __LINE__, #expr); return 1; } } while (0)
static unsigned ticks;
static void tick(void) { ++ticks; }
static int fixture_prepare(void *context, char *error, size_t capacity) {
    (void)context; (void)error; (void)capacity; return 0;
}
static int fixture_lifecycle(void *context, int fg, int focused, int audio, char *error, size_t capacity) {
    (void)context; (void)fg; (void)focused; (void)audio; (void)error; (void)capacity; return 0;
}
static int fixture_main(int argc, char **argv) { (void)argc; (void)argv; return 0; }
static void ready(whole_front_state *state) {
    whole_front_state_runtime(state, 1);
    whole_front_state_surface(state, 1);
    whole_front_state_lifecycle(state, 1, 1, 1, 0);
}
typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    whole_front_state *state;
    int entered, done, result;
} owned_waiter;
static void *waiter(void *opaque) {
    owned_waiter *w = opaque;
    pthread_mutex_lock(&w->lock); w->entered = 1;
    pthread_cond_broadcast(&w->changed); pthread_mutex_unlock(&w->lock);
    int result = whole_front_state_wait_ready(w->state);
    pthread_mutex_lock(&w->lock); w->result = result; w->done = 1;
    pthread_cond_broadcast(&w->changed); pthread_mutex_unlock(&w->lock);
    return NULL;
}
static int await_waiter(owned_waiter *w, int done, long milliseconds) {
    struct timespec until; clock_gettime(CLOCK_REALTIME, &until);
    until.tv_nsec += milliseconds * 1000000;
    until.tv_sec += until.tv_nsec / 1000000000; until.tv_nsec %= 1000000000;
    pthread_mutex_lock(&w->lock);
    int result = 0;
    while (!(done ? w->done : w->entered) && !result)
        result = pthread_cond_timedwait(&w->changed, &w->lock, &until);
    pthread_mutex_unlock(&w->lock);
    return result;
}
int main(int argc, char **argv) {
    if (argc != 2) return 64;
    whole_front_state state = WHOLE_FRONT_STATE_INITIALIZER;
    if (!strcmp(argv[1], "wait-resume")) {
        ready(&state); CHECK(whole_front_state_claim_start(&state));
        whole_front_state_lifecycle(&state, 1, 0, 0, 0);
        owned_waiter w = {.lock = PTHREAD_MUTEX_INITIALIZER, .changed = PTHREAD_COND_INITIALIZER, .state = &state};
        pthread_t thread; CHECK(!pthread_create(&thread, NULL, waiter, &w));
        CHECK(!await_waiter(&w, 0, 1000));
        CHECK(await_waiter(&w, 1, 20) == ETIMEDOUT);
        whole_front_state_lifecycle(&state, 1, 1, 1, 0);
        CHECK(!await_waiter(&w, 1, 1000) && !w.result);
        CHECK(!pthread_join(thread, NULL));
        CHECK(!whole_front_state_claim_start(&state));
        pthread_cond_destroy(&w.changed); pthread_mutex_destroy(&w.lock);
    } else if (!strcmp(argv[1], "wait-cancel")) {
        for (int failure = 0; failure < 2; ++failure) {
            whole_front_state waiting = WHOLE_FRONT_STATE_INITIALIZER;
            owned_waiter w = {.lock = PTHREAD_MUTEX_INITIALIZER, .changed = PTHREAD_COND_INITIALIZER, .state = &waiting};
            pthread_t thread; CHECK(!pthread_create(&thread, NULL, waiter, &w));
            CHECK(!await_waiter(&w, 0, 1000));
            CHECK(await_waiter(&w, 1, 20) == ETIMEDOUT);
            if (failure) whole_front_state_lifecycle(&waiting, 0, 0, 0, EIO);
            else whole_front_state_stop(&waiting);
            CHECK(!await_waiter(&w, 1, 1000) && w.result == -1);
            CHECK(!pthread_join(thread, NULL));
            pthread_cond_destroy(&w.changed); pthread_mutex_destroy(&w.lock);
            pthread_cond_destroy(&waiting.changed); pthread_mutex_destroy(&waiting.lock);
        }
    } else if (!strcmp(argv[1], "contract")) {
        const char *arguments[] = {"owned-fixture", "/owned-fixture/exefs"};
        whole_frontend_runtime runtime = {WHOLE_FRONTEND_RUNTIME_ABI, NULL, 2, arguments,
            fixture_prepare, fixture_lifecycle, fixture_main};
        CHECK(!whole_frontend_runtime_contract_valid(NULL));
        CHECK(whole_frontend_runtime_contract_valid(&runtime));
        whole_frontend_runtime invalid = runtime;
        invalid.abi = 0; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        invalid = runtime; invalid.prepare = NULL; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        invalid = runtime; invalid.lifecycle = NULL; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        invalid = runtime; invalid.host_main = NULL; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        invalid = runtime; invalid.host_argc = 1; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        invalid = runtime; invalid.host_argc = 65; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        invalid = runtime; invalid.host_argv = NULL; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
        const char *missing[] = {"owned-fixture", NULL};
        invalid = runtime; invalid.host_argv = missing; CHECK(!whole_frontend_runtime_contract_valid(&invalid));
    } else if (!strcmp(argv[1], "start")) {
        CHECK(!whole_front_state_claim_start(&state));
        whole_front_state_surface(&state, 1);
        whole_front_state_lifecycle(&state, 1, 1, 1, 0);
        CHECK(!whole_front_state_claim_start(&state));
        whole_front_state_runtime(&state, 1);
        whole_front_state_lifecycle(&state, 1, 1, 0, 0);
        CHECK(!whole_front_state_claim_start(&state));
        whole_front_state_lifecycle(&state, 1, 0, 1, 0);
        CHECK(!whole_front_state_claim_start(&state));
        whole_front_state_lifecycle(&state, 1, 1, 1, 0);
        CHECK(whole_front_state_claim_start(&state));
        CHECK(!whole_front_state_claim_start(&state));
    } else if (!strcmp(argv[1], "input")) {
        ready(&state);
        whole_front_state_button(&state, UINT64_C(1) << 9, 1);
        whole_front_state_stick(&state, 0, 2, 2);
        whole_front_state_stick(&state, 1, NAN, INFINITY);
        whole_front_pad pad;
        CHECK(whole_front_state_touch(&state, &pad));
        CHECK(pad.buttons == (UINT64_C(1) << 9));
        CHECK(pad.lx > 23000 && pad.lx < 23200 && pad.ly == pad.lx);
        CHECK(pad.rx == 0 && pad.ry == 0);
        whole_front_state_lifecycle(&state, 0, 0, 0, 0);
        whole_front_state_button(&state, 1, 1);
        whole_front_state_stick(&state, 0, 1, 0);
        CHECK(!whole_front_state_touch(&state, &pad));
        CHECK(!pad.buttons && !pad.lx && !pad.ly && !pad.rx && !pad.ry);
        whole_front_state_lifecycle(&state, 1, 1, 1, 0);
        CHECK(whole_front_state_touch(&state, &pad));
        CHECK(!pad.buttons && !pad.lx && !pad.ly);
    } else if (!strcmp(argv[1], "vsync")) {
        ready(&state);
        whole_front_state_display_tick(&state);
        CHECK(!ticks);
        whole_front_state_bind_vsync(&state, tick);
        whole_front_state_display_tick(&state);
        CHECK(ticks == 1);
        whole_front_state_lifecycle(&state, 1, 0, 0, 0);
        whole_front_state_display_tick(&state);
        CHECK(ticks == 1);
        whole_front_state_lifecycle(&state, 1, 1, 1, 0);
        whole_front_state_display_tick(&state);
        CHECK(ticks == 2);
        whole_front_state_bind_vsync(&state, NULL);
        whole_front_state_display_tick(&state);
        CHECK(ticks == 2);
        whole_front_state_bind_vsync(&state, tick);
        whole_front_state_stop(&state);
        ready(&state);
        whole_front_state_bind_vsync(&state, tick);
        whole_front_state_display_tick(&state);
        CHECK(ticks == 2 && !whole_front_state_claim_start(&state));
    } else if (!strcmp(argv[1], "failure")) {
        ready(&state);
        whole_front_state_lifecycle(&state, 1, 1, 1, 5);
        ready(&state);
        CHECK(!whole_front_state_claim_start(&state));
        whole_front_state running = WHOLE_FRONT_STATE_INITIALIZER;
        ready(&running);
        CHECK(whole_front_state_claim_start(&running));
        whole_front_state_bind_vsync(&running, tick);
        whole_front_state_button(&running, 1, 1);
        whole_front_state_lifecycle(&running, 0, 0, 0, 5);
        ready(&running);
        whole_front_pad pad;
        CHECK(!whole_front_state_touch(&running, &pad) && !pad.buttons);
        whole_front_state_display_tick(&running);
        CHECK(!ticks && !whole_front_state_claim_start(&running));
    } else return 64;
    pthread_cond_destroy(&state.changed); pthread_mutex_destroy(&state.lock);
    return 0;
}
