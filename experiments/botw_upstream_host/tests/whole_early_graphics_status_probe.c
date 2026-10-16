/* Reuse every real Queue/Window algorithm and owned callback from its fixture.
 * Only normal API-return output is observed; no Guest or GPU is executed. */
#include <errno.h>
#include <unistd.h>
#define main owned_queue_fixture_main
#include "whole_queue_acquire_probe.c"
#undef main
#include "normal_early_types.inc"
static const char *mode;
static NVNdevice early_device;
static struct { uint64_t before; NVNcommandBuffer object; uint64_t after; } early_command;
static unsigned cmda_calls;
static int cmda_completed;
static void cmda_cb_init(const void *command) {
    assert(command == &early_command.object);
    const cmd_t *object = command;
    assert(object->magic == MAGIC('C', 'M', 'D', 'B') && object->dev == &early_device);
    assert(!object->recording && !object->cur && !object->end && !object->begin);
    cmda_calls++;
    errno = ERANGE;
    cmda_completed = 1;
}
#define CB(c) ((cmd_t *)(c))
#include "normal_early_functions.inc"
static const NVNT_nvnCommandBufferInitialize command_initialize = h_nvnCommandBufferInitialize;

ssize_t owned_early_status_write(int fd, const void *bytes, size_t size) {
    const char *expected;
    assert(fd == STDERR_FILENO);
    if (!strcmp(mode, "queue")) {
        expected = "[botw whole-host] api=NvnQueueAcquireTexture result=ok\n";
        assert(output.index == 0 && ((win_t *)&window_object)->next == 1);
        assert(event_count && events[event_count - 1] == PRESENT_WAIT && !fence_calls);
    } else if (!strcmp(mode, "count-zero")) {
        expected = "[botw whole-host] api=NvnQueueAcquireTexture result=error\n";
        assert(output.index == -77 && !((win_t *)&window_object)->next && !event_count);
    } else {
        assert(!strcmp(mode, "initialize"));
        expected = "[botw whole-host] api=NvnCommandBufferInitialize result=ok\n";
        assert(cmda_calls == 1 && cmda_completed);
    }
    assert(strlen(expected) == size && !memcmp(expected, bytes, size));
    ssize_t result = write(fd, bytes, size);
    errno = EIO; /* Real logger and wrapper must retain the original API errno. */
    return result;
}

static void run_queue(const char *requested) {
    mode = requested;
    char *arguments[] = {"owned", (char *)requested};
    clock_now = 100;
    errno = EDOM;
    assert(owned_queue_fixture_main(2, arguments) == 0);
    assert(errno == EDOM);
}
static void run_initialize(void) {
    mode = "initialize";
    unsigned before_calls = cmda_calls;
    cmda_completed = 0;
    memset(&early_command, 0xa5, sizeof early_command);
    memset(&early_device, 0x39, sizeof early_device);
    unsigned char expected[sizeof early_command];
    memcpy(expected, &early_command, sizeof expected);
    cmd_t state = {.magic = MAGIC('C', 'M', 'D', 'B'), .dev = &early_device};
    memcpy(expected + offsetof(__typeof__(early_command), object), &state, sizeof state);
    NVNdevice before_device = early_device;
    errno = EDOM;
    assert(command_initialize(&early_command.object, &early_device) == 1);
    assert(errno == ERANGE && cmda_calls == before_calls + 1 && cmda_completed);
    assert(!memcmp(&early_command, expected, sizeof expected));
    assert(!memcmp(&early_device, &before_device, sizeof before_device));
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "queue-once")) {
        run_queue("queue");
        /* Existing mixed Window/Queue fixture handles the real function's
         * process-lifetime refresh-period cache without resetting it. */
        errno = EDOM; interleaved(); assert(errno == EDOM);
        run_queue("count-zero"); run_queue("count-zero");
    } else if (!strcmp(argv[1], "initialize-once")) {
        run_initialize(); run_initialize();
    } else if (!strcmp(argv[1], "initialize")) run_initialize();
    else run_queue(argv[1]);
    return 0;
}
