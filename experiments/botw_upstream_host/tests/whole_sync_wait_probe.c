/* Actual native Sync entries with owned CPU objects/callbacks only. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "nvn_protos.h"
#include "normal_sync_types.inc"
_Static_assert(NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED == 0, "already enum ABI");
_Static_assert(NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED == 1, "condition enum ABI");
_Static_assert(NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED == 2, "timeout enum ABI");
_Static_assert(NVN_SYNC_WAIT_RESULT_FAILED == 3, "failed enum ABI");
static unsigned fence_calls, wait_calls;
static uint64_t fence_value = UINT64_C(0xfedcba9876543210);
static int answers[2], callback_errno[2];
static uint64_t observed_fence[2], observed_timeout[2];
static uint64_t x_fence(void) { fence_calls++; return fence_value; }
static int mtl_wait(uint64_t fence, uint64_t timeout) {
    assert(wait_calls < 2);
    unsigned index = wait_calls++;
    observed_fence[index] = fence;
    observed_timeout[index] = timeout;
    errno = callback_errno[index];
    return answers[index];
}
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__)
#include "normal_sync_functions.inc"
static const NVNT_nvnSyncInitialize initialize = h_nvnSyncInitialize;
static const NVNT_nvnSyncFinalize finalize = h_nvnSyncFinalize;
static const NVNT_nvnQueueFenceSync queue_fence = h_nvnQueueFenceSync;
static const NVNT_nvnSyncWait sync_wait = h_nvnSyncWait;
typedef struct { uint64_t before; NVNsync object; uint64_t after; } guarded_sync;
static const uint64_t timeouts[] = {
    0, 1, 37, UINT64_C(1000000000), UINT64_C(3600000000001),
    UINT64_C(0x8000000000000000), UINT64_MAX
};
static guarded_sync sync_object;
static NVNdevice device;
static NVNqueue queue_object;
static void setup(void) {
    memset(&sync_object, 0xa5, sizeof sync_object);
    sync_object.before = UINT64_C(0x0123456789abcdef);
    sync_object.after = UINT64_C(0x76543210fedcba98);
    memset(&device, 0x39, sizeof device);
    memset(&queue_object, 0x61, sizeof queue_object);
    guarded_sync expected = sync_object;
    sync_t initial = {MAGIC('S', 'Y', 'N', 'C'), 0, 0};
    memcpy(&expected.object, &initial, sizeof initial);
    NVNdevice before_device = device;
    errno = EDOM;
    assert(initialize(&sync_object.object, &device) == 1 && errno == EDOM);
    assert(!memcmp(&sync_object, &expected, sizeof expected));
    assert(!memcmp(&device, &before_device, sizeof device));
    assert(!wait_calls && !fence_calls);
}
static int fresh(void) {
    setup();
    guarded_sync before = sync_object;
    for (unsigned i = 0; i < sizeof timeouts / sizeof timeouts[0]; i++) {
        errno = EDOM;
        NVNsyncWaitResult result = sync_wait(&sync_object.object, timeouts[i]);
        if (result != NVN_SYNC_WAIT_RESULT_FAILED) {
            fprintf(stderr, "fresh SyncWait expected FAILED3, got %d\n", (int)result);
            return 1;
        }
        assert(errno == EDOM && !wait_calls && !fence_calls);
        assert(!memcmp(&sync_object, &before, sizeof before));
    }
    finalize(&sync_object.object); /* No wait is performed after Finalize. */
    ((sync_t *)&before.object)->magic = 0;
    assert(!memcmp(&sync_object, &before, sizeof before));
    return 0;
}
static void fence(void) {
    guarded_sync expected = sync_object;
    NVNqueue before_queue = queue_object;
    ((sync_t *)&expected.object)->fenced = 1;
    ((sync_t *)&expected.object)->fence = fence_value;
    unsigned before_calls = fence_calls;
    errno = EDOM;
    queue_fence(&queue_object, &sync_object.object,
                NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE, 0);
    assert(errno == EDOM && fence_calls == before_calls + 1 && !wait_calls);
    assert(!memcmp(&sync_object, &expected, sizeof expected));
    assert(!memcmp(&queue_object, &before_queue, sizeof before_queue));
}
static void configure_wait(int first, int second) {
    wait_calls = 0;
    memset(observed_fence, 0, sizeof observed_fence);
    memset(observed_timeout, 0, sizeof observed_timeout);
    answers[0] = first;
    answers[1] = second;
    callback_errno[0] = ERANGE;
    callback_errno[1] = EAGAIN;
}
static void check_wait(uint64_t timeout, NVNsyncWaitResult expected, int first, int second) {
    guarded_sync before = sync_object;
    NVNdevice before_device = device;
    NVNqueue before_queue = queue_object;
    unsigned before_fences = fence_calls;
    configure_wait(first, second);
    errno = EDOM;
    assert(sync_wait(&sync_object.object, timeout) == expected);
    assert(wait_calls == (first ? 1u : 2u));
    assert(observed_fence[0] == fence_value && observed_timeout[0] == 0);
    if (!first) assert(observed_fence[1] == fence_value && observed_timeout[1] == timeout);
    assert(errno == callback_errno[wait_calls - 1] && fence_calls == before_fences);
    assert(!memcmp(&sync_object, &before, sizeof before));
    assert(!memcmp(&device, &before_device, sizeof device));
    assert(!memcmp(&queue_object, &before_queue, sizeof before_queue));
}
static void fenced(const char *mode) {
    setup(); fence();
    for (unsigned i = 0; i < sizeof timeouts / sizeof timeouts[0]; i++) {
        if (!strcmp(mode, "already"))
            check_wait(timeouts[i], NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED, 1, 0);
        else if (!strcmp(mode, "condition"))
            check_wait(timeouts[i], NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED, 0, 1);
        else {
            assert(!strcmp(mode, "timeout"));
            check_wait(timeouts[i], NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED, 0, 0);
        }
    }
}
static void repeated(void) {
    setup(); fence();
    check_wait(37, NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED, 0, 0);
    check_wait(UINT64_MAX, NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED, 0, 1);
    check_wait(1, NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED, 1, 0);
    wait_calls = 0;
    fence_value = UINT64_C(0x1234567890abcdef);
    fence();
    check_wait(0, NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED, 0, 0);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "fresh")) return fresh();
    if (!strcmp(argv[1], "already") || !strcmp(argv[1], "condition") || !strcmp(argv[1], "timeout")) {
        fenced(argv[1]); return 0;
    }
    if (!strcmp(argv[1], "repeated")) { repeated(); return 0; }
    return 2;
}
