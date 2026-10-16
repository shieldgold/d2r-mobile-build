/* Owned CPU integration with the original kernel structs/table/consumers.
 * No guest code, mapped game bytes, GPU or platform audio is executed. */
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "hle_thread_core.h"
#include "whole_kernel.h"
#include "services.h"
typedef uint32_t mach_port_t; // Only the unexecuted original thread metadata field.
#define H_CUR_THREAD UINT32_C(0xffff8000)
#define LOG(...) ((void)0)
_Noreturn static void die(const char *message) { fprintf(stderr, "unexpected fatal allocator: %s\n", message); abort(); }
#include "original_kernel_registry.inc"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "kernel integration:%d: %s\n", __LINE__, #x); return 1; } } while (0)
static char error[256];
#define E error, sizeof error
static int event_state(uint32_t handle, int *bad) {
    pthread_mutex_lock(&G); int state = sig_obj(handle, bad); pthread_mutex_unlock(&G); return state;
}
static unsigned live(void) {
    unsigned result = 0; pthread_mutex_lock(&G);
    for (unsigned i = 1; i < MAX_H; ++i) result += g_h[i].type != O_FREE;
    pthread_mutex_unlock(&G); return result;
}
static int endpoints(uint32_t *write, uint32_t *read, int state) {
    return botw_whole_kernel_create_event(NULL, state, write, read, E);
}
static int close_event(uint32_t handle) { return botw_whole_kernel_close_event(NULL, handle, E); }
static atomic_int ready;
static void *waiter(void *argument) {
    thr_t *thread = argument;
    pthread_mutex_lock(&G);
    thread->wkind = 4; thread->next = g_wait; g_wait = thread;
    atomic_store(&ready, 1);
    struct timespec deadline; clock_gettime(CLOCK_REALTIME, &deadline); deadline.tv_sec += 5;
    while (!thread->woke) {
        if (pthread_cond_timedwait(&thread->cv, &G, &deadline)) break;
    }
    if (!thread->woke) { wait_unlink(thread); thread->wkind = 0; }
    pthread_mutex_unlock(&G); return NULL;
}
static int wake_case(int close_it) {
    uint32_t write, read; CHECK(!endpoints(&write, &read, 0));
    thr_t thread = {0}; CHECK(!pthread_cond_init(&thread.cv, NULL));
    pthread_t native; atomic_store(&ready, 0);
    CHECK(!pthread_create(&native, NULL, waiter, &thread));
    while (!atomic_load(&ready)) { struct timespec ts = {0,100000}; nanosleep(&ts, NULL); }
    CHECK(!(close_it ? close_event(read) : botw_whole_kernel_signal_event(NULL, write, E)));
    CHECK(!pthread_join(native, NULL));
    CHECK(thread.woke && !thread.wkind && thread.wres == -1 && !g_wait);
    int bad = 0; CHECK(event_state(read, &bad) == !close_it && bad == close_it);
    if (!close_it) CHECK(!close_event(read));
    CHECK(!close_event(write)); CHECK(!pthread_cond_destroy(&thread.cv)); return 0;
}
static int parallel_tokens[8];
static void *parallel(void *argument) {
    for (int n = 0; n < 200; ++n) {
        uint32_t handle = 0; char message[128];
        if (botw_whole_kernel_transport_allocate(NULL, argument, &handle, message, sizeof message)) return (void *)1;
        if (botw_whole_kernel_handle_kind(handle) != BOTW_WHOLE_HANDLE_TRANSPORT) return (void *)1;
        if (botw_whole_kernel_transport_release(NULL, argument, handle, message, sizeof message)) return (void *)1;
    }
    return NULL;
}
static unsigned char ipc[256];
static int ipc_memory(void *opaque, uint64_t address, size_t length, int writable) {
    (void)opaque; (void)writable; uintptr_t base = (uintptr_t)ipc;
    return address >= base && length <= sizeof ipc && address - base <= sizeof ipc - length;
}
static void put32(size_t offset, uint32_t value) { memcpy(ipc + offset, &value, sizeof value); }
static uint32_t get32(size_t offset) { uint32_t value; memcpy(&value, ipc + offset, sizeof value); return value; }
static void request(uint32_t command, int pid, const void *payload, size_t size, unsigned type) {
    memset(ipc, 0, sizeof ipc);
    put32(0, type); put32(4, (uint32_t)((32 + size + 3) / 4) | (pid ? 0x80000000u : 0));
    size_t offset = 16;
    if (pid) { put32(8, 1); offset = 32; }
    put32(offset, 0x49434653); put32(offset + 8, command);
    if (size) memcpy(ipc + offset + 16, payload, size);
}
static int services_case(void) {
    botw_services *services = botw_services_create(NULL, ipc_memory, NULL); CHECK(services);
    botw_services_transport_api api = {NULL, botw_whole_kernel_transport_allocate, botw_whole_kernel_transport_release};
    CHECK(!botw_services_set_transport_api(services, &api, E));
    uint32_t sm = 0; CHECK(!botw_services_connect(services, "sm:", 3, &sm, E));
    CHECK(botw_whole_kernel_handle_kind(sm) == BOTW_WHOLE_HANDLE_TRANSPORT);
    uint64_t zero = 0; request(0, 1, &zero, sizeof zero, 4);
    CHECK(!botw_services_send(services, sm, ipc, sizeof ipc, E)); CHECK(get32(16) == 0x4f434653 && !get32(24));
    request(1, 0, "lm\0\0\0\0\0\0", 8, 4);
    CHECK(!botw_services_send(services, sm, ipc, sizeof ipc, E)); uint32_t lm = get32(12);
    CHECK(lm != sm && botw_whole_kernel_handle_kind(lm) == BOTW_WHOLE_HANDLE_TRANSPORT);
    request(0, 1, &zero, sizeof zero, 4);
    CHECK(!botw_services_send(services, lm, ipc, sizeof ipc, E)); uint32_t logger = get32(12);
    CHECK(logger != sm && logger != lm && botw_whole_kernel_handle_kind(logger) == BOTW_WHOLE_HANDLE_TRANSPORT);
    request(2, 0, NULL, 0, 5);
    CHECK(!botw_services_send(services, logger, ipc, sizeof ipc, E)); uint32_t clone = get32(12);
    CHECK(botw_whole_kernel_handle_kind(clone) == BOTW_WHOLE_HANDLE_TRANSPORT);
    CHECK(!botw_services_close(services, logger, E));
    CHECK(botw_whole_kernel_handle_kind(logger) == BOTW_WHOLE_HANDLE_NONE);
    uint32_t destination = 1; request(1, 0, &destination, 4, 4);
    CHECK(!botw_services_send(services, clone, ipc, sizeof ipc, E)); CHECK(!get32(24));
    CHECK(!botw_services_destroy_checked(services, E)); CHECK(live() == 1); return 0;
}
int main(int argc, char **argv) {
    CHECK(argc == 2); CHECK(!pthread_key_create(&g_keythr, NULL));
    thr_t thread = {0}; pthread_mutex_lock(&G); uint32_t th = h_new(O_THREAD, &thread); pthread_mutex_unlock(&G);
    CHECK(th == 0x1001);
    if (!strcmp(argv[1], "registry")) {
        int owner = 0; uint32_t ipc_handle = 0, write, read;
        CHECK(!botw_whole_kernel_transport_allocate(NULL, &owner, &ipc_handle, E));
        CHECK(!endpoints(&write, &read, 0));
        CHECK(th != ipc_handle && ipc_handle != write && write != read);
        CHECK(live() == 4 && botw_whole_kernel_handle_kind(th) == BOTW_WHOLE_HANDLE_THREAD);
        CHECK(botw_whole_kernel_handle_kind(read) == BOTW_WHOLE_HANDLE_EVENT_READ);
        CHECK(botw_whole_kernel_handle_kind(write) == BOTW_WHOLE_HANDLE_EVENT_WRITE);
        CHECK(!botw_whole_kernel_transport_release(NULL, &owner, ipc_handle, E));
        CHECK(!close_event(write) && !close_event(read));
    } else if (!strcmp(argv[1], "owner")) {
        int first = 0, second = 0; uint32_t handle, unchanged = 0xfeed;
        CHECK(!botw_whole_kernel_transport_allocate(NULL, &first, &handle, E));
        CHECK(botw_whole_kernel_transport_allocate(NULL, &first, &unchanged, E) && unchanged == 0xfeed);
        CHECK(botw_whole_kernel_transport_release(NULL, &second, handle, E));
        CHECK(close_event(handle) && live() == 2);
        CHECK(!botw_whole_kernel_transport_release(NULL, &first, handle, E));
        CHECK(botw_whole_kernel_transport_release(NULL, &first, handle, E));
    } else if (!strcmp(argv[1], "capacity")) {
        int owners[MAX_H] = {0}; uint32_t handles[MAX_H], write = 123, read = 456;
        for (int i = 0; i < MAX_H - 3; ++i) CHECK(!botw_whole_kernel_transport_allocate(NULL, &owners[i], &handles[i], E));
        CHECK(live() == MAX_H - 2);
        CHECK(endpoints(&write, &read, 0) && write == 123 && read == 456 && live() == MAX_H - 2);
        CHECK(!botw_whole_kernel_transport_allocate(NULL, &owners[MAX_H - 3], &handles[MAX_H - 3], E));
        uint32_t unchanged = 123; CHECK(botw_whole_kernel_transport_allocate(NULL, &owners[MAX_H - 2], &unchanged, E) && unchanged == 123);
        for (int i = 0; i < MAX_H - 2; ++i) CHECK(!botw_whole_kernel_transport_release(NULL, &owners[i], handles[i], E));
        CHECK(!endpoints(&write, &read, 1));
        for (int i = 0; i < MAX_H - 4; ++i) CHECK(!botw_whole_kernel_transport_allocate(NULL, &owners[i], &handles[i], E));
        unchanged = 123; CHECK(botw_whole_kernel_duplicate_event(NULL, read, &unchanged, E) && unchanged == 123);
        for (int i = 0; i < MAX_H - 4; ++i) CHECK(!botw_whole_kernel_transport_release(NULL, &owners[i], handles[i], E));
        CHECK(!close_event(write) && !close_event(read));
    } else if (!strcmp(argv[1], "events")) {
        uint32_t write, read, copy; CHECK(!endpoints(&write, &read, 0));
        CHECK(botw_whole_kernel_signal_event(NULL, read, E));
        CHECK(!botw_whole_kernel_duplicate_event(NULL, read, &copy, E)); CHECK(!close_event(read));
        CHECK(!botw_whole_kernel_signal_event(NULL, write, E)); int bad = 0;
        CHECK(event_state(copy, &bad) == 1 && !bad); CHECK(event_state(copy, &bad) == 1 && !bad);
        CHECK(!botw_whole_kernel_clear_event(NULL, copy, E)); CHECK(!event_state(copy, &bad) && !bad);
        CHECK(!close_event(write) && !close_event(copy)); CHECK(close_event(copy));
    } else if (!strcmp(argv[1], "wake") || !strcmp(argv[1], "close-wake")) {
        CHECK(!wake_case(!strcmp(argv[1], "close-wake")));
    } else if (!strcmp(argv[1], "producer")) {
        uint32_t write, read, second_write, second_read; botw_whole_event_producer *producer = NULL;
        CHECK(!endpoints(&write, &read, 0));
        CHECK(!botw_whole_kernel_retain_event_producer(write, &producer, E)); CHECK(producer);
        CHECK(!close_event(write)); CHECK(!endpoints(&second_write, &second_read, 0)); CHECK(second_write == write);
        CHECK(!botw_whole_kernel_producer_signal(producer, E)); int bad = 0;
        CHECK(event_state(read, &bad) && !bad); CHECK(!event_state(second_read, &bad) && !bad);
        CHECK(!botw_whole_kernel_producer_clear(producer, E)); CHECK(!event_state(read, &bad) && !bad);
        CHECK(!close_event(read)); CHECK(!botw_whole_kernel_producer_signal(producer, E));
        CHECK(!botw_whole_kernel_producer_release(&producer, E) && !producer);
        CHECK(botw_whole_kernel_producer_release(&producer, E));
        CHECK(!close_event(second_write) && !close_event(second_read));
    } else if (!strcmp(argv[1], "invalid")) {
        int owner = 0; uint32_t out = 123, read = 456;
        CHECK(botw_whole_kernel_transport_allocate(NULL, NULL, &out, E) && out == 123);
        CHECK(botw_whole_kernel_transport_allocate(NULL, &owner, NULL, E));
        CHECK(endpoints(&out, &out, 0) && out == 123); CHECK(endpoints(&out, &read, 2) && out == 123 && read == 456);
        CHECK(botw_whole_kernel_duplicate_event(NULL, th, &out, E) && out == 123);
        CHECK(close_event(th) && botw_whole_kernel_handle_kind(th) == BOTW_WHOLE_HANDLE_THREAD);
        CHECK(botw_whole_kernel_handle_kind(0xffff8000u) == BOTW_WHOLE_HANDLE_NONE);
        CHECK(botw_whole_kernel_handle_kind(0) == BOTW_WHOLE_HANDLE_NONE);
        botw_whole_event_producer *producer = NULL; CHECK(botw_whole_kernel_retain_event_producer(th, &producer, E) && !producer);
        CHECK(botw_whole_kernel_producer_signal(NULL, E) && botw_whole_kernel_producer_clear(NULL, E));
    } else if (!strcmp(argv[1], "parallel")) {
        pthread_t workers[8];
        for (unsigned i = 0; i < 8; ++i) CHECK(!pthread_create(&workers[i], NULL, parallel, &parallel_tokens[i]));
        for (unsigned i = 0; i < 8; ++i) { void *result; CHECK(!pthread_join(workers[i], &result) && !result); }
    } else if (!strcmp(argv[1], "services")) { CHECK(!services_case());
    } else return 64;
    CHECK(live() == 1); CHECK(!pthread_key_delete(g_keythr)); return 0;
}
