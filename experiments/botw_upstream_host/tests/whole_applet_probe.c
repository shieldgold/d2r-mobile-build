#include "whole_applet.h"
#include <stdio.h>
#include <string.h>
#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "owned applet behavior failed at %d: %s\n", __LINE__, #expr); return 1; } } while (0)
static struct { int suspended, reject, calls; } owned_execution;
static int execution(void *context, unsigned mode, int fg, int focused, int suspended, char *error, size_t cap) {
    (void)context; (void)mode; (void)fg; (void)focused; ++owned_execution.calls;
    if (owned_execution.reject) { if (error && cap) snprintf(error, cap, "owned execution transition rejected"); return -1; }
    owned_execution.suspended = suspended;
    return 0;
}
static int message(uint32_t expected) {
    uint32_t value = 0xa5a5a5a5;
    return whole_applet_try_pop(&value, NULL, 0) == 1 && value == expected;
}
int main(int argc, char **argv) {
    CHECK(argc == 2); char error[160] = {0}; whole_applet_snapshot state;
    if (!strcmp(argv[1], "initial")) {
        memset(&state, 0xa5, sizeof state); whole_applet_snapshot original = state;
        CHECK(whole_applet_query(&state, error, sizeof error) == -1 && error[0]);
        CHECK(!memcmp(&state, &original, sizeof state));
        uint32_t out = 77; CHECK(whole_applet_try_pop(&out, error, sizeof error) == -1 && out == 77);
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error));
        CHECK(whole_applet_try_pop(NULL, error, sizeof error) == -1);
        CHECK(message(15));
        CHECK(!whole_applet_query(&state, error, sizeof error) && state.focus_state == 1 && !state.policy_configured);
        CHECK(whole_applet_try_pop(&out, error, sizeof error) == 0 && out == 77);
        CHECK(whole_applet_publish(0, 1, 0, 0, error, sizeof error) == -1);
        CHECK(whole_applet_publish(1, 1, 2, 0, error, sizeof error) == -1);
        CHECK(whole_applet_publish(1, 1, 0, 2, error, sizeof error) == -1);
    } else if (!strcmp(argv[1], "notifications")) {
        CHECK(!whole_applet_set_notifications(WHOLE_APPLET_RESUME, 1, error, sizeof error));
        CHECK(!whole_applet_set_notifications(WHOLE_APPLET_OPERATION, 1, error, sizeof error));
        CHECK(!whole_applet_set_notifications(WHOLE_APPLET_PERFORMANCE, 1, error, sizeof error));
        CHECK(whole_applet_set_notifications(8, 1, error, sizeof error) == -1);
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error) && message(15));
        CHECK(!whole_applet_publish(1, 0, 0, 0, error, sizeof error) && message(15));
        CHECK(!whole_applet_publish(0, 0, 0, 0, error, sizeof error) && message(15));
        CHECK(!whole_applet_publish(1, 1, 1, 1, error, sizeof error));
        CHECK(message(15) && message(30) && message(31));
        uint32_t out = 77; CHECK(!whole_applet_try_pop(&out, error, sizeof error) && out == 77);
        CHECK(!whole_applet_query(&state, error, sizeof error) && !state.suspended && !state.policy_configured);
        CHECK(!whole_applet_set_notifications(WHOLE_APPLET_OPERATION, 0, error, sizeof error));
        CHECK(!whole_applet_set_notifications(WHOLE_APPLET_PERFORMANCE, 0, error, sizeof error));
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error));
        CHECK(!whole_applet_try_pop(&out, error, sizeof error));
    } else if (!strcmp(argv[1], "unsupported")) {
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error) && message(15));
        CHECK(!whole_applet_set_focus_policy(0, error, sizeof error));
        CHECK(whole_applet_publish(0, 0, 0, 0, error, sizeof error) == -1 && error[0]);
        CHECK(!whole_applet_query(&state, error, sizeof error) && state.focus_state == 1 && !state.suspended);
        CHECK(whole_applet_set_focus_policy(3, error, sizeof error) == -1);
        CHECK(!whole_applet_set_focus_policy(1, error, sizeof error));
        CHECK(!whole_applet_publish(0, 0, 0, 0, error, sizeof error) && message(15));
        CHECK(!whole_applet_query(&state, error, sizeof error) && state.focus_state == 3 && !state.suspended);
    } else if (!strcmp(argv[1], "execution")) {
        CHECK(!whole_applet_bind_execution(execution, &owned_execution, error, sizeof error));
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error) && message(15));
        CHECK(!whole_applet_set_focus_policy(0, error, sizeof error));
        CHECK(!whole_applet_set_notifications(WHOLE_APPLET_RESUME, 1, error, sizeof error));
        CHECK(!whole_applet_publish(0, 0, 0, 0, error, sizeof error) && message(15));
        CHECK(owned_execution.suspended);
        CHECK(!whole_applet_query(&state, error, sizeof error) && state.suspended && state.focus_state == 3);
        owned_execution.reject = 1;
        CHECK(whole_applet_publish(1, 1, 0, 0, error, sizeof error) == -1 && error[0]);
        CHECK(!whole_applet_query(&state, error, sizeof error) && state.suspended && state.focus_state == 3);
        owned_execution.reject = 0;
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error));
        CHECK(!owned_execution.suspended && message(15) && message(16));
        uint32_t out = 77; CHECK(!whole_applet_try_pop(&out, error, sizeof error));
    } else if (!strcmp(argv[1], "overflow")) {
        CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error) && message(15));
        for (int i = 0; i < 32; ++i) CHECK(!whole_applet_publish(1, i & 1, 0, 0, error, sizeof error));
        CHECK(whole_applet_publish(1, 0, 0, 0, error, sizeof error) == -1 && error[0]);
        CHECK(!whole_applet_query(&state, error, sizeof error) && state.focus_state == 1);
        for (int i = 0; i < 32; ++i) CHECK(message(15));
        uint32_t out = 77; CHECK(!whole_applet_try_pop(&out, error, sizeof error));
    } else return 64;
    return 0;
}
