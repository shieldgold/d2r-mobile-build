/* Owned scalar fixture calling real typed service entry bodies only. */
#include <sys/resource.h>
#include "botw_oe.c"
#include "hle_sdkinit.c"
#include "whole_applet.h"

static uint32_t owned_output;
static unsigned prepares;
void dm_guest_write_prepare(void *out, size_t size) {
    if (out != &owned_output || size != sizeof owned_output) abort();
    ++prepares;
}
#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "owned typed applet entry failed at %d: %s\n", __LINE__, #expr); return 1; } } while (0)
int main(int argc, char **argv) {
    struct rlimit no_core = {0, 0}; setrlimit(RLIMIT_CORE, &no_core);
    CHECK(argc == 2);
    if (!strcmp(argv[1], "init-unconfigured")) { h_oe_Initialize(); return 0; }
    char error[160] = {0};
    CHECK(!whole_applet_publish(1, 1, 0, 0, error, sizeof error));
    h_oe_Initialize();
    CHECK(botw_oe_operation_mode() == 0 && botw_oe_performance_mode() == 0);
    owned_output = 0xa5a5a5a5;
    CHECK(botw_oe_try_pop_notification(&owned_output) == 1 && owned_output == 15 && prepares == 1);
    owned_output = 0xa5a5a5a5;
    CHECK(botw_oe_try_pop_notification(&owned_output) == 0 && owned_output == 0xa5a5a5a5 && prepares == 1);
    botw_oe_operation_notify(1); botw_oe_performance_notify(1); botw_oe_resume(1);
    botw_oe_focus_policy(1);
    whole_applet_snapshot state;
    CHECK(!whole_applet_query(&state, error, sizeof error) && state.policy_configured && state.focus_policy == 1);
    CHECK(!whole_applet_publish(1, 0, 1, 1, error, sizeof error));
    CHECK(botw_oe_try_pop_notification(&owned_output) == 1 && owned_output == 15);
    CHECK(botw_oe_try_pop_notification(&owned_output) == 1 && owned_output == 30);
    CHECK(botw_oe_try_pop_notification(&owned_output) == 1 && owned_output == 31);
    CHECK(botw_oe_try_pop_notification(&owned_output) == 0);
    CHECK(botw_oe_operation_mode() == 1 && botw_oe_performance_mode() == 1);
    botw_oe_operation_notify(0x100); botw_oe_performance_notify(0x100); botw_oe_resume(0x100);
    CHECK(!whole_applet_publish(1, 0, 0, 0, error, sizeof error));
    CHECK(botw_oe_try_pop_notification(&owned_output) == 0);
    return 0;
}
