"""Host kernel behavior through hand-written C SVC fixtures, no game assets."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "kernel.h"
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include <unistd.h>
static botw_kernel *ctx;
static char error[512];
static pthread_key_t test_tls;
static _Atomic unsigned entered;
static _Atomic int validation_pause, validation_entered, validation_release;
static uint64_t validation_key;
static uint64_t callback_stack_low, callback_stack_top;
static uint64_t now(void) {
    struct timespec t; assert(!clock_gettime(CLOCK_MONOTONIC, &t));
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static int memory(void *o, uint64_t a, size_t n, int w) {
    (void)o; (void)w;
    if (atomic_load(&validation_pause) && a == validation_key) {
        atomic_store(&validation_entered, 1); uint64_t deadline = now() + 1000000000;
        while (!atomic_load(&validation_release) && now() < deadline) sched_yield();
        assert(atomic_load(&validation_release));
    }
    return a > 0x10000 && n <= 4096 && a <= UINT64_MAX - n;
}
static void svc(unsigned number, uint64_t *r) {
    int result = botw_kernel_dispatch(ctx, number, r, error, sizeof error);
    if (result != BOTW_KERNEL_HANDLED) fprintf(stderr, "svc %x: %s (%d)\n", number, error, result);
    assert(result == BOTW_KERNEL_HANDLED);
}
static uint32_t wait_one(uint32_t h, int64_t timeout) {
    uint64_t r[32] = {0, (uintptr_t)&h, 1, (uint64_t)timeout};
    svc(0x18, r); if (!r[0]) assert(r[1] == 0); return (uint32_t)r[0];
}
static void *signal_later(void *p) {
    struct timespec t = {0, 20000000}; nanosleep(&t, NULL);
    assert(!botw_kernel_signal_event(ctx, (uint32_t)(uintptr_t)p, error, sizeof error));
    return NULL;
}
static void events(void) {
    uint32_t w, r, duplicate;
    assert(!botw_kernel_create_event(ctx, 0, &w, &r, error, sizeof error));
    assert(w >= 0x20000 && r > w);
    assert(wait_one(r, 0) == BOTW_KERNEL_RESULT_TIMED_OUT);
    uint64_t before = now();
    assert(wait_one(r, 15000000) == BOTW_KERNEL_RESULT_TIMED_OUT);
    assert(now() - before >= 10000000);
    pthread_t producer; assert(!pthread_create(&producer, NULL, signal_later, (void *)(uintptr_t)w));
    before = now(); assert(wait_one(r, -1) == 0);
    assert(now() - before >= 10000000); assert(!pthread_join(producer, NULL));
    assert(wait_one(r, 0) == 0); /* manual reset event remains signaled */
    assert(!botw_kernel_duplicate_handle(ctx, r, &duplicate, error, sizeof error));
    assert(!botw_kernel_close_handle(ctx, r, error, sizeof error));
    assert(wait_one(r, 0) == BOTW_KERNEL_RESULT_INVALID_HANDLE);
    assert(wait_one(duplicate, 0) == 0);
    assert(botw_kernel_signal_event(ctx, duplicate, error, sizeof error) == -1);
    uint64_t reset[32] = {duplicate}; svc(0x17, reset); assert(reset[0] == 0);
    assert(wait_one(duplicate, 0) == BOTW_KERNEL_RESULT_TIMED_OUT);
    reset[0] = duplicate; svc(0x17, reset); assert(reset[0] == BOTW_KERNEL_RESULT_INVALID_STATE);
    assert(!botw_kernel_signal_event(ctx, w, error, sizeof error));
    assert(!botw_kernel_close_handle(ctx, w, error, sizeof error));
    assert(wait_one(duplicate, 0) == 0);
    assert(!botw_kernel_close_handle(ctx, duplicate, error, sizeof error));
    assert(botw_kernel_close_handle(ctx, duplicate, error, sizeof error) == -1);
    uint64_t create[32] = {0}; svc(0x45, create);
    assert(create[0] == 0 && create[1] != create[2]);
    assert(!botw_kernel_close_handle(ctx, (uint32_t)create[1], error, sizeof error));
    assert(!botw_kernel_close_handle(ctx, (uint32_t)create[2], error, sizeof error));
}
static void multi_wait(void) {
    uint32_t w[2], h[2];
    for (unsigned i = 0; i < 2; i++) assert(!botw_kernel_create_event(ctx, i == 1, &w[i], &h[i], error, sizeof error));
    uint64_t r[32] = {0, (uintptr_t)h, 2, 0}; svc(0x18, r);
    assert(r[0] == 0 && r[1] == 1);
    r[1] = 1; r[2] = 2; r[3] = 0; svc(0x18, r);
    assert(r[0] == BOTW_KERNEL_RESULT_INVALID_ADDRESS);
    r[1] = (uintptr_t)h; r[2] = 65; svc(0x18, r);
    assert(r[0] == BOTW_KERNEL_RESULT_OUT_OF_RANGE);
    h[0] = 0x10000; r[1] = (uintptr_t)h; r[2] = 2; svc(0x18, r);
    assert(r[0] == BOTW_KERNEL_RESULT_INVALID_HANDLE); /* all validated before first signal */
    for (unsigned i = 0; i < 2; i++) {
        assert(!botw_kernel_close_handle(ctx, w[i], error, sizeof error));
        if (i) assert(!botw_kernel_close_handle(ctx, h[i], error, sizeof error));
    }
}
static void timing(void) {
    uint64_t r[32] = {0}; svc(0x1e, r); uint64_t tick = r[0], before = now();
    r[0] = 15000000; svc(0x0b, r); assert(now() - before >= 10000000);
    svc(0x1e, r); assert(r[0] > tick + 192000);
    r[1] = BOTW_KERNEL_CURRENT_THREAD; svc(0x25, r); assert(!r[0] && r[1]);
    uint64_t id = r[1]; r[1] = BOTW_KERNEL_CURRENT_THREAD; svc(0x25, r); assert(r[1] == id);
    r[1] = 0x1001; svc(0x25, r); assert(!r[0] && r[1] == id);
    r[1] = 0x10000; svc(0x25, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_HANDLE);
    r[0] = 0; svc(0x0b, r); r[0] = (uint64_t)-1; svc(0x0b, r);
    r[0] = (uint64_t)-2; svc(0x0b, r);
    uint64_t saved[32] = {1,2,3}; memcpy(r, saved, sizeof saved);
    assert(botw_kernel_dispatch(ctx, 0x7f, r, error, sizeof error) == BOTW_KERNEL_UNSUPPORTED);
    assert(!memcmp(saved, r, sizeof saved));
    assert(botw_kernel_is_process_handle(ctx, BOTW_KERNEL_CURRENT_PROCESS));
    assert(!botw_kernel_is_process_handle(ctx, 0x10000));
}
static uint64_t info(unsigned id) {
    uint64_t r[32] = {0, id, BOTW_KERNEL_CURRENT_PROCESS, 0}; svc(0x29, r);
    assert(r[0] == 0); return r[1];
}
static void protection_fault(int number) { (void)number; _exit(44); }
static int readable(uint64_t address) {
    pid_t child = fork(); assert(child >= 0);
    if (!child) {
        signal(SIGSEGV, protection_fault); signal(SIGBUS, protection_fault);
        volatile unsigned char value = *(volatile unsigned char *)(uintptr_t)address;
        (void)value; _exit(0);
    }
    int status; assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status)); return WEXITSTATUS(status) == 0;
}
static int resident(uint64_t address) {
#ifdef __APPLE__
    char vector = 0;
#else
    unsigned char vector = 0;
#endif
    assert(!mincore((void *)(uintptr_t)address, (size_t)sysconf(_SC_PAGESIZE), &vector));
    return vector & 1;
}
static void memory_mapping(void) {
    const size_t unit = 0x200000;
    uint64_t base, size;
    assert(!botw_kernel_reservation(ctx, &base, &size));
    assert(!botw_kernel_configure_memory(ctx, unit * 4, unit * 2, unit, unit * 3, unit, error, sizeof error));
    uint64_t heap = info(4), stack = info(14);
    assert(info(5) == unit * 4 && info(15) == unit * 2);
    assert(info(6) == unit * 3 && info(7) == unit);
    assert(botw_kernel_reservation(ctx, &base, &size));
    assert(base < (UINT64_C(1) << 36) && size <= (UINT64_C(1) << 36) - base);
    assert(!botw_kernel_memory(ctx, heap, 1, 1) && !readable(heap));
    uint64_t r[32] = {0, 4096}; svc(1, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_SIZE);
    r[1] = unit * 3; svc(1, r); assert(r[0] == BOTW_KERNEL_RESULT_OUT_OF_MEMORY);
    r[1] = unit * 2; svc(1, r); assert(!r[0] && r[1] == heap);
    assert(info(7) == unit * 3 && readable(heap));
    assert(botw_kernel_memory(ctx, heap + unit * 2 - 1, 1, 1));
    assert(!botw_kernel_memory(ctx, heap + unit * 2 - 1, 2, 0));
    memset((void *)(uintptr_t)heap, 0x6a, 0x10000);
    uint64_t mapping[32] = {stack + 0x4000, heap + 0x4000, 0x4000};
    svc(4, mapping); assert(!mapping[0]);
    assert(!readable(heap + 0x4000) && readable(heap) && readable(heap + 0x8000));
    assert(*(unsigned char *)(uintptr_t)(stack + 0x4000) == 0x6a);
    assert(resident(stack + 0x4000) && !resident(heap + 0x4000));
    *(unsigned char *)(uintptr_t)(stack + 0x4000) = 0xb7;
    assert(!botw_kernel_memory(ctx, heap + 0x4000, 1, 0));
    botw_kernel_memory_info region;
    assert(botw_kernel_query_memory(ctx, stack + 0x4000, &region) == 1);
    assert(region.base == stack + 0x4000 && region.size == 0x4000 && region.type == 0xb && region.permission == 3);
    assert(botw_kernel_query_memory(ctx, heap + 0x4000, &region) == 1);
    assert(region.permission == 0 && (region.attribute & 1));
    r[1] = 0; svc(1, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE);
    mapping[0] = stack + 0x4000; svc(4, mapping); assert(mapping[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE);
    mapping[0] = stack + 0x4000; mapping[1] = heap; svc(5, mapping);
    assert(mapping[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE);
    mapping[0] = stack + 0x4000; mapping[1] = heap + 0x4000; svc(5, mapping); assert(!mapping[0]);
    assert(!readable(stack + 0x4000) && readable(heap + 0x4000));
    assert(*(unsigned char *)(uintptr_t)(heap + 0x4000) == 0xb7);
    assert(resident(heap + 0x4000) && !resident(stack + 0x4000));
    /* A valid Horizon 4K page cannot revoke just one quarter of an Apple 16K
       page. Refuse it without changing either neighboring 4K region. */
    mapping[0] = stack + 0x1000; mapping[1] = heap + 0x1000; mapping[2] = 0x1000;
    int result = botw_kernel_dispatch(ctx, 4, mapping, error, sizeof error);
    if (sysconf(_SC_PAGESIZE) > 4096) {
        assert(result == BOTW_KERNEL_ERROR && strstr(error, "host page"));
        assert(readable(heap) && readable(heap + 0x1000) && !readable(stack + 0x1000));
    } else {
        assert(result == BOTW_KERNEL_HANDLED && !mapping[0]);
        mapping[0] = stack + 0x1000; svc(5, mapping); assert(!mapping[0]);
    }
    *(unsigned char *)(uintptr_t)(heap + unit) = 0x81;
    r[1] = unit; svc(1, r); assert(!r[0]);
    assert(!botw_kernel_memory(ctx, heap + unit, 1, 0) && !readable(heap + unit));
    r[1] = unit * 2; svc(1, r); assert(!r[0]);
    assert(*(unsigned char *)(uintptr_t)(heap + unit) == 0); /* reclaimed pages are zero */
    assert(botw_kernel_query_memory(ctx, base - 1, &region) == 0);
}
static int executable(void *opaque, uint64_t address) { (void)opaque; return address >= 0x10000 && !(address & 3); }
static void guest_thread(uint64_t argument, uint32_t handle) {
    assert(pthread_getspecific(test_tls) == (void *)0x1234);
    assert(handle >= 0x20000);
    atomic_fetch_add(&entered, 1);
    uint64_t r[32] = {0, BOTW_KERNEL_CURRENT_THREAD}; svc(0x25, r);
    uint64_t tid = r[1]; assert(!r[0] && tid > 1);
    r[1] = handle; svc(0x25, r); assert(!r[0] && r[1] == tid);
    r[0] = argument; svc(0x11, r); assert(!r[0]);
    r[0] = 0; svc(0x0a, r); abort();
}
static int execute_thread(void *opaque, uint64_t entry, uint64_t argument,
                          uint64_t top, uint32_t handle, char *message, size_t cap) {
    (void)opaque; (void)message; (void)cap;
    unsigned char on_stack;
    assert((uintptr_t)&on_stack >= callback_stack_low && (uintptr_t)&on_stack < callback_stack_top);
    assert(top <= callback_stack_top && (uintptr_t)&on_stack < top);
    assert(!pthread_setspecific(test_tls, (void *)0x1234));
    ((void (*)(uint64_t, uint32_t))(uintptr_t)entry)(argument, handle);
    return 0;
}
static uint32_t create_worker(uint64_t entry, uint64_t argument, uint64_t top) {
    uint64_t r[32] = {0, entry, argument, top, 44, (uint64_t)-2}; svc(0x08, r);
    assert(!r[0]); return (uint32_t)r[1];
}
static void threads(void) {
    const size_t unit = 0x200000;
    assert(!pthread_key_create(&test_tls, NULL));
    assert(!botw_kernel_configure_memory(ctx, unit * 4, unit * 2, unit, unit * 4, 0, error, sizeof error));
    uint64_t heap = info(4), stack = info(14), r[32] = {0, unit};
    svc(1, r); assert(!r[0]);
    r[0] = stack; r[1] = heap; r[2] = unit; svc(4, r); assert(!r[0]);
    callback_stack_low = stack; callback_stack_top = stack + unit;
    uint32_t w, read; assert(!botw_kernel_create_event(ctx, 0, &w, &read, error, sizeof error));
    uint32_t worker = create_worker((uintptr_t)guest_thread, w, stack + unit);
    assert(!atomic_load(&entered) && wait_one(worker, 0) == BOTW_KERNEL_RESULT_TIMED_OUT);
    r[0] = stack; r[1] = heap; r[2] = unit; svc(5, r);
    assert(r[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE); /* stack lifetime held by thread */
    r[0] = worker; svc(9, r); assert(!r[0]);
    r[0] = worker; svc(9, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_STATE);
    assert(wait_one(read, 1000000000) == 0);
    assert(wait_one(worker, 1000000000) == 0 && atomic_load(&entered) == 1);
    assert(!botw_kernel_close_handle(ctx, worker, error, sizeof error));
    r[0] = stack; r[1] = heap; r[2] = unit; svc(5, r); assert(!r[0]);
    assert(!botw_kernel_shutdown(ctx, error, sizeof error));
    assert(!pthread_key_delete(test_tls));
}
static _Atomic int running, release_cpu;
static void cpu_thread(uint64_t argument, uint32_t handle) {
    (void)argument; (void)handle; atomic_store(&running, 1);
    while (!atomic_load(&release_cpu)) { }
    uint64_t r[32] = {0}; svc(0xa, r); abort();
}
static void shutdown_busy(void) {
    const size_t unit = 0x200000;
    assert(!pthread_key_create(&test_tls, NULL));
    assert(!botw_kernel_configure_memory(ctx, unit * 2, unit * 2, unit, unit * 2, 0, error, sizeof error));
    uint64_t heap = info(4), stack = info(14), r[32] = {0, unit}; svc(1, r);
    r[0] = stack; r[1] = heap; r[2] = unit; svc(4, r); assert(!r[0]);
    callback_stack_low = stack; callback_stack_top = stack + unit;
    uint32_t worker = create_worker((uintptr_t)cpu_thread, 0, stack + unit);
    r[0] = worker; svc(9, r); assert(!r[0]);
    uint64_t deadline = now() + 1000000000;
    while (!atomic_load(&running) && now() < deadline) sched_yield();
    assert(atomic_load(&running));
    uint64_t start = now(); assert(botw_kernel_shutdown(ctx, error, sizeof error) == -1);
    assert(now() - start < 100000000 && strstr(error, "running"));
    assert(botw_kernel_memory(ctx, stack, unit, 1)); /* retained while guest executes */
    atomic_store(&release_cpu, 1);
    int rc;
    do { rc = botw_kernel_shutdown(ctx, error, sizeof error); if (rc) sched_yield(); }
    while (rc && now() < deadline);
    assert(!rc); assert(!pthread_key_delete(test_tls));
}
static int writable(uint64_t address) {
    pid_t child = fork(); assert(child >= 0);
    if (!child) {
        signal(SIGSEGV, protection_fault); signal(SIGBUS, protection_fault);
        *(volatile unsigned char *)(uintptr_t)address = 1; _exit(0);
    }
    int status; assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status)); return WEXITSTATUS(status) == 0;
}
static void shared_memory(void) {
    const size_t unit = 0x200000, length = 0x40000;
    assert(!botw_kernel_configure_memory(ctx, unit, unit, unit, unit, 0, error, sizeof error));
    uint64_t alias = info(2); uint32_t handle, duplicate; void *host;
    assert(!botw_kernel_create_shared_memory(ctx, length, 1, &handle, &host, error, sizeof error));
    assert(host); memset(host, 0x39, length);
    uint64_t r[32] = {handle, alias, length, 3}; svc(0x13, r); assert(r[0] == 0xd801);
    r[0] = handle; r[3] = 1; svc(0x13, r); assert(!r[0]);
    assert(*(unsigned char *)(uintptr_t)alias == 0x39 && readable(alias) && !writable(alias));
    assert(botw_kernel_memory(ctx, alias, length, 0) && !botw_kernel_memory(ctx, alias, length, 1));
    ((unsigned char *)host)[0x3ffff] = 0xbc;
    assert(*(unsigned char *)(uintptr_t)(alias + 0x3ffff) == 0xbc);
    r[0] = handle; r[1] = alias + length; svc(0x13, r); assert(!r[0]);
    ((unsigned char *)host)[0] = 0x97;
    assert(*(unsigned char *)(uintptr_t)alias == 0x97 && *(unsigned char *)(uintptr_t)(alias + length) == 0x97);
    assert(!botw_kernel_duplicate_handle(ctx, handle, &duplicate, error, sizeof error));
    assert(!botw_kernel_close_handle(ctx, handle, error, sizeof error));
    assert(*(unsigned char *)(uintptr_t)alias == 0x97);
    botw_kernel_memory_info region; assert(botw_kernel_query_memory(ctx, alias, &region) == 1);
    assert(region.type == 6 && region.permission == 1 && region.size == length);
    r[0] = duplicate; r[1] = alias; r[2] = length; svc(0x14, r); assert(!r[0]);
    assert(!readable(alias) && readable(alias + length));
    r[0] = duplicate; r[1] = alias + length; svc(0x14, r); assert(!r[0]);
    assert(!botw_kernel_close_handle(ctx, duplicate, error, sizeof error));
}
static void returned_thread(uint64_t argument, uint32_t handle) { (void)argument; (void)handle; }
static void unexpected_return(void) {
    const size_t unit = 0x200000;
    assert(!pthread_key_create(&test_tls, NULL));
    assert(!botw_kernel_configure_memory(ctx, unit, unit, unit, unit, 0, error, sizeof error));
    uint64_t heap = info(4), stack = info(14), r[32] = {0, unit}; svc(1, r);
    r[0] = stack; r[1] = heap; r[2] = unit; svc(4, r); assert(!r[0]);
    callback_stack_low = stack; callback_stack_top = stack + unit;
    uint32_t worker = create_worker((uintptr_t)returned_thread, 0, stack + unit);
    r[0] = worker; svc(9, r); assert(!r[0]);
    int rc; uint64_t deadline = now() + 1000000000;
    do { r[0] = 0; rc = botw_kernel_dispatch(ctx, 0x1e, r, error, sizeof error); }
    while (rc == BOTW_KERNEL_HANDLED && now() < deadline);
    assert(rc == BOTW_KERNEL_ERROR && strstr(error, "without SVC ExitThread"));
    do { rc = botw_kernel_shutdown(ctx, error, sizeof error); if (rc) sched_yield(); }
    while (rc && now() < deadline);
    assert(!rc); assert(!pthread_key_delete(test_tls));
}
static _Atomic int wait_entered;
static void *wait_until_stop(void *opaque) {
    uint32_t handle = (uint32_t)(uintptr_t)opaque; atomic_store(&wait_entered, 1);
    assert(wait_one(handle, -1) == BOTW_KERNEL_RESULT_CANCELLED); return NULL;
}
static void stop_wait(void) {
    uint32_t w, r; assert(!botw_kernel_create_event(ctx, 0, &w, &r, error, sizeof error));
    pthread_t waiter; assert(!pthread_create(&waiter, NULL, wait_until_stop, (void *)(uintptr_t)r));
    while (!atomic_load(&wait_entered)) sched_yield();
    int rc = botw_kernel_shutdown(ctx, error, sizeof error); assert(rc == 0 || rc == -1);
    assert(!pthread_join(waiter, NULL)); assert(!botw_kernel_shutdown(ctx, error, sizeof error));
}
static _Atomic uint32_t guest_mutex, guest_condition;
static _Atomic unsigned sync_prepared, sync_finished, sync_order_count;
static uint32_t sync_order[4], sync_observed_words[4];
#define MUTEX_WAIT UINT32_C(0x40000000)
static uint32_t thread_priority(uint32_t handle) {
    uint64_t r[32] = {0, handle}; svc(0xc, r); assert(!r[0]); return (uint32_t)r[1];
}
static void mutex_lock(uint32_t self) {
    for (;;) {
        uint32_t value = atomic_load(&guest_mutex);
        if (!value) {
            if (atomic_compare_exchange_weak(&guest_mutex, &value, self)) return;
            continue;
        }
        if (!(value & MUTEX_WAIT) && !atomic_compare_exchange_weak(&guest_mutex, &value, value | MUTEX_WAIT)) continue;
        uint64_t r[32] = {value & ~MUTEX_WAIT, (uintptr_t)&guest_mutex, self}; svc(0x1a, r); assert(!r[0]);
        if ((atomic_load(&guest_mutex) & ~MUTEX_WAIT) == self) return;
    }
}
static void mutex_unlock(uint32_t self) {
    uint32_t value = self;
    if (atomic_compare_exchange_strong(&guest_mutex, &value, 0)) return;
    assert((value & ~MUTEX_WAIT) == self && (value & MUTEX_WAIT));
    uint64_t r[32] = {(uintptr_t)&guest_mutex}; svc(0x1b, r); assert(!r[0]);
}
static uint64_t prepare_sync(unsigned workers) {
    const size_t unit = 0x200000;
    assert(!pthread_key_create(&test_tls, NULL));
    assert(!botw_kernel_configure_memory(ctx, unit * (workers + 1), unit * (workers + 1), unit,
        unit * (workers + 1), 0, error, sizeof error));
    uint64_t heap = info(4), stack = info(14), r[32] = {0, unit * workers}; svc(1, r); assert(!r[0]);
    for (unsigned i = 0; i < workers; i++) {
        r[0] = stack + unit * i; r[1] = heap + unit * i; r[2] = unit; svc(4, r); assert(!r[0]);
    }
    callback_stack_low = stack; callback_stack_top = stack + unit * workers;
    return stack;
}
static uint32_t sync_worker(uint64_t stack, unsigned index, uint64_t entry, uint64_t argument, unsigned priority) {
    uint64_t r[32] = {0, entry, argument, stack + (index + 1) * 0x200000, priority, (uint64_t)-2};
    svc(8, r); assert(!r[0]); uint32_t handle = (uint32_t)r[1];
    r[0] = handle; svc(9, r); assert(!r[0]); return handle;
}
static void exit_worker(void) { uint64_t r[32] = {0}; svc(0xa, r); abort(); }
static void mutex_contender(uint64_t argument, uint32_t self) {
    atomic_fetch_add(&sync_prepared, 1); mutex_lock(self);
    unsigned index = atomic_fetch_add(&sync_order_count, 1);
    sync_order[index] = (uint32_t)argument; sync_observed_words[index] = atomic_load(&guest_mutex);
    mutex_unlock(self); atomic_fetch_add(&sync_finished, 1); exit_worker();
}
static void until_priority(unsigned value) {
    uint64_t deadline = now() + 1000000000;
    while (thread_priority(BOTW_KERNEL_INITIAL_THREAD) != value && now() < deadline) sched_yield();
    assert(thread_priority(BOTW_KERNEL_INITIAL_THREAD) == value);
}
static void mutex_arbitration(void) {
    uint64_t stack = prepare_sync(2);
    atomic_store(&guest_mutex, BOTW_KERNEL_INITIAL_THREAD);
    uint32_t middle = sync_worker(stack, 0, (uintptr_t)mutex_contender, 20, 20); until_priority(20);
    uint32_t high = sync_worker(stack, 1, (uintptr_t)mutex_contender, 10, 10); until_priority(10);
    assert((atomic_load(&guest_mutex) & ~MUTEX_WAIT) == BOTW_KERNEL_INITIAL_THREAD);
    assert(atomic_load(&sync_finished) == 0);
    mutex_unlock(BOTW_KERNEL_INITIAL_THREAD);
    assert(wait_one(high, 1000000000) == 0 && wait_one(middle, 1000000000) == 0);
    assert(atomic_load(&sync_finished) == 2 && sync_order[0] == 10 && sync_order[1] == 20);
    assert(sync_observed_words[0] == (high | MUTEX_WAIT) && sync_observed_words[1] == middle);
    assert(atomic_load(&guest_mutex) == 0 && thread_priority(BOTW_KERNEL_INITIAL_THREAD) == 44);
    /* Changed guest owner short-circuits without trusting an obsolete handle. */
    uint64_t r[32] = {0x123456, (uintptr_t)&guest_mutex, BOTW_KERNEL_INITIAL_THREAD}; svc(0x1a, r); assert(!r[0]);
    r[0] = 0x123456; atomic_store(&guest_mutex, 0x123456 | MUTEX_WAIT); svc(0x1a, r);
    assert(r[0] == BOTW_KERNEL_RESULT_INVALID_HANDLE);
    r[0] = (uintptr_t)&guest_mutex + 1; svc(0x1b, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_ADDRESS);
}
static void condition_contender(uint64_t argument, uint32_t self) {
    mutex_lock(self); atomic_fetch_add(&sync_prepared, 1);
    uint64_t r[32] = {(uintptr_t)&guest_mutex, (uintptr_t)&guest_condition, self, (uint64_t)-1};
    svc(0x1c, r); assert(!r[0]);
    assert((atomic_load(&guest_mutex) & ~MUTEX_WAIT) == self);
    unsigned index = atomic_fetch_add(&sync_order_count, 1); sync_order[index] = (uint32_t)argument;
    mutex_unlock(self); atomic_fetch_add(&sync_finished, 1); exit_worker();
}
static void condition_relock(void) {
    uint64_t stack = prepare_sync(2);
    uint32_t low = sync_worker(stack, 0, (uintptr_t)condition_contender, 20, 20);
    uint32_t high = sync_worker(stack, 1, (uintptr_t)condition_contender, 10, 10);
    uint64_t deadline = now() + 1000000000;
    while (atomic_load(&sync_prepared) != 2 && now() < deadline) sched_yield();
    assert(atomic_load(&sync_prepared) == 2);
    mutex_lock(BOTW_KERNEL_INITIAL_THREAD); /* waits for final atomic enqueue/unlock */
    assert(atomic_load(&guest_condition) == 1);
    uint64_t r[32] = {(uintptr_t)&guest_condition, 1}; svc(0x1d, r);
    assert(atomic_load(&guest_condition) == 1 && atomic_load(&sync_finished) == 0);
    assert(atomic_load(&guest_mutex) == (BOTW_KERNEL_INITIAL_THREAD | MUTEX_WAIT));
    assert(thread_priority(BOTW_KERNEL_INITIAL_THREAD) == 10);
    mutex_unlock(BOTW_KERNEL_INITIAL_THREAD);
    assert(wait_one(high, 1000000000) == 0 && sync_order[0] == 10);
    assert(atomic_load(&guest_condition) == 1);
    mutex_lock(BOTW_KERNEL_INITIAL_THREAD);
    r[0] = (uintptr_t)&guest_condition + 1; r[1] = (uint64_t)-1; svc(0x1d, r); /* key align-down ABI */
    assert(atomic_load(&guest_condition) == 0);
    mutex_unlock(BOTW_KERNEL_INITIAL_THREAD);
    assert(wait_one(low, 1000000000) == 0 && sync_order[1] == 20 && atomic_load(&guest_mutex) == 0);
}
static void condition_timeout(void) {
    atomic_store(&guest_mutex, BOTW_KERNEL_INITIAL_THREAD);
    uint64_t before = now(), r[32] = {(uintptr_t)&guest_mutex, (uintptr_t)&guest_condition,
        BOTW_KERNEL_INITIAL_THREAD, 15000000};
    svc(0x1c, r); assert(r[0] == BOTW_KERNEL_RESULT_TIMED_OUT && now() - before >= 10000000);
    assert(atomic_load(&guest_mutex) == 0 && atomic_load(&guest_condition) == 1);
    mutex_lock(BOTW_KERNEL_INITIAL_THREAD); mutex_unlock(BOTW_KERNEL_INITIAL_THREAD);
    r[0] = (uintptr_t)&guest_condition; r[1] = 0; svc(0x1d, r); assert(atomic_load(&guest_condition) == 0);
    atomic_store(&guest_mutex, BOTW_KERNEL_INITIAL_THREAD);
    r[0] = (uintptr_t)&guest_mutex; r[1] = (uintptr_t)&guest_condition; r[2] = BOTW_KERNEL_INITIAL_THREAD; r[3] = 0;
    svc(0x1c, r); assert(r[0] == BOTW_KERNEL_RESULT_TIMED_OUT && atomic_load(&guest_mutex) == 0);
    r[0] = 1; r[1] = (uintptr_t)&guest_condition; r[2] = BOTW_KERNEL_INITIAL_THREAD; r[3] = 0;
    svc(0x1c, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_ADDRESS);
}
static _Atomic unsigned cancel_stage;
static uint32_t cancel_read_handle;
static void cancelled_waiter(uint64_t argument, uint32_t self) {
    (void)argument; (void)self;
    atomic_store(&cancel_stage, 1); assert(wait_one(cancel_read_handle, -1) == BOTW_KERNEL_RESULT_CANCELLED);
    atomic_store(&cancel_stage, 2); assert(wait_one(cancel_read_handle, 15000000) == BOTW_KERNEL_RESULT_TIMED_OUT);
    atomic_store(&cancel_stage, 3); exit_worker();
}
static void cancellation(void) {
    uint64_t stack = prepare_sync(1); uint32_t write;
    assert(!botw_kernel_create_event(ctx, 0, &write, &cancel_read_handle, error, sizeof error));
    uint32_t worker = sync_worker(stack, 0, (uintptr_t)cancelled_waiter, 0, 44);
    uint64_t deadline = now() + 1000000000;
    while (!atomic_load(&cancel_stage) && now() < deadline) sched_yield();
    assert(atomic_load(&cancel_stage));
    uint64_t r[32] = {worker}; svc(0x19, r); assert(!r[0]);
    assert(wait_one(worker, 1000000000) == 0 && atomic_load(&cancel_stage) == 3);
    r[0] = 0x10000; svc(0x19, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_HANDLE);
    /* A cancellation issued before a cancellable wait is retained once. */
    r[0] = BOTW_KERNEL_INITIAL_THREAD; svc(0x19, r); assert(!r[0]);
    assert(wait_one(cancel_read_handle, 0) == BOTW_KERNEL_RESULT_TIMED_OUT);
    assert(wait_one(cancel_read_handle, -1) == BOTW_KERNEL_RESULT_CANCELLED);
    assert(wait_one(cancel_read_handle, 15000000) == BOTW_KERNEL_RESULT_TIMED_OUT);
}
static void condition_cancel_worker(uint64_t argument, uint32_t self) {
    (void)argument; mutex_lock(self); atomic_store(&sync_prepared, 1);
    uint64_t r[32] = {(uintptr_t)&guest_mutex, (uintptr_t)&guest_condition, self, (uint64_t)-1};
    svc(0x1c, r); assert(!r[0] && (atomic_load(&guest_mutex) & ~MUTEX_WAIT) == self);
    mutex_unlock(self);
    assert(wait_one(cancel_read_handle, -1) == BOTW_KERNEL_RESULT_CANCELLED);
    assert(wait_one(cancel_read_handle, 0) == BOTW_KERNEL_RESULT_TIMED_OUT);
    atomic_store(&sync_finished, 1); exit_worker();
}
static void condition_cancel_is_deferred(void) {
    uint64_t stack = prepare_sync(1); uint32_t write;
    assert(!botw_kernel_create_event(ctx, 0, &write, &cancel_read_handle, error, sizeof error));
    uint32_t worker = sync_worker(stack, 0, (uintptr_t)condition_cancel_worker, 0, 44);
    uint64_t deadline = now() + 1000000000;
    while (!atomic_load(&sync_prepared) && now() < deadline) sched_yield();
    assert(atomic_load(&sync_prepared)); mutex_lock(BOTW_KERNEL_INITIAL_THREAD);
    uint64_t r[32] = {worker}; svc(0x19, r); assert(!r[0] && !atomic_load(&sync_finished));
    r[0] = (uintptr_t)&guest_condition; r[1] = 1; svc(0x1d, r);
    mutex_unlock(BOTW_KERNEL_INITIAL_THREAD);
    assert(wait_one(worker, 1000000000) == 0 && atomic_load(&sync_finished));
}
static void stopped_condition_worker(uint64_t argument, uint32_t self) {
    (void)argument; mutex_lock(self); atomic_fetch_add(&sync_prepared, 1);
    uint64_t r[32] = {(uintptr_t)&guest_mutex, (uintptr_t)&guest_condition, self, (uint64_t)-1};
    svc(0x1c, r); assert(r[0] == BOTW_KERNEL_RESULT_CANCELLED);
    atomic_fetch_add(&sync_finished, 1); exit_worker();
}
static void stopped_mutex_worker(uint64_t argument, uint32_t self) {
    (void)argument;
    atomic_fetch_or(&guest_mutex, MUTEX_WAIT);
    uint64_t r[32] = {BOTW_KERNEL_INITIAL_THREAD, (uintptr_t)&guest_mutex, self};
    svc(0x1a, r); assert(r[0] == BOTW_KERNEL_RESULT_CANCELLED);
    atomic_fetch_add(&sync_finished, 1); exit_worker();
}
static void shutdown_sync_waits(void) {
    uint64_t stack = prepare_sync(2);
    sync_worker(stack, 0, (uintptr_t)stopped_condition_worker, 0, 44);
    uint64_t deadline = now() + 1000000000;
    while (!atomic_load(&sync_prepared) && now() < deadline) sched_yield();
    assert(atomic_load(&sync_prepared)); mutex_lock(BOTW_KERNEL_INITIAL_THREAD);
    sync_worker(stack, 1, (uintptr_t)stopped_mutex_worker, 0, 20); until_priority(20);
    int rc;
    do { rc = botw_kernel_shutdown(ctx, error, sizeof error); if (rc) sched_yield(); }
    while (rc && now() < deadline);
    assert(!rc && atomic_load(&sync_finished) == 2);
}
static uint64_t owned_mutex, owned_key;
static void owned_condition_worker(uint64_t argument, uint32_t self) {
    (void)argument; __atomic_store_n((uint32_t *)(uintptr_t)owned_mutex, self, __ATOMIC_RELEASE);
    uint64_t r[32] = {owned_mutex, owned_key, self, (uint64_t)-1}; svc(0x1c, r);
    assert(r[0] == BOTW_KERNEL_RESULT_CANCELLED); exit_worker();
}
static void pending_wait_memory_is_retained(void) {
    const size_t unit = 0x200000; uint64_t stack = prepare_sync(1), heap = info(4);
    uint64_t r[32] = {0, unit * 2}; svc(1, r); assert(!r[0]);
    owned_mutex = heap + unit; owned_key = owned_mutex + 4;
    sync_worker(stack, 0, (uintptr_t)owned_condition_worker, 0, 44);
    uint64_t deadline = now() + 1000000000;
    while ((!__atomic_load_n((uint32_t *)(uintptr_t)owned_key, __ATOMIC_ACQUIRE) ||
            __atomic_load_n((uint32_t *)(uintptr_t)owned_mutex, __ATOMIC_ACQUIRE)) && now() < deadline) sched_yield();
    assert(__atomic_load_n((uint32_t *)(uintptr_t)owned_key, __ATOMIC_ACQUIRE) == 1);
    r[1] = unit; svc(1, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE);
    r[0] = stack + unit; r[1] = heap + unit; r[2] = unit; svc(4, r);
    assert(r[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE && readable(owned_mutex));
    int rc;
    do { rc = botw_kernel_shutdown(ctx, error, sizeof error); if (rc) sched_yield(); }
    while (rc && now() < deadline);
    assert(!rc);
}
static void *shrink_during_validation(void *opaque) {
    (void)opaque; uint64_t deadline = now() + 1000000000;
    while (!atomic_load(&validation_entered) && now() < deadline) sched_yield();
    assert(atomic_load(&validation_entered));
    uint64_t r[32] = {0, 0x200000}; char message[512];
    assert(botw_kernel_dispatch(ctx, 1, r, message, sizeof message) == BOTW_KERNEL_HANDLED && !r[0]);
    atomic_store(&validation_release, 1); return NULL;
}
static void revalidate_before_wait_registration(void) {
    const size_t unit = 0x200000;
    assert(!botw_kernel_configure_memory(ctx, unit * 2, unit, unit, unit * 2, 0, error, sizeof error));
    uint64_t heap = info(4), r[32] = {0, unit * 2}; svc(1, r); assert(!r[0]);
    uint64_t mutex = heap + unit;
    __atomic_store_n((uint32_t *)(uintptr_t)mutex, BOTW_KERNEL_INITIAL_THREAD, __ATOMIC_RELEASE);
    validation_key = (uintptr_t)&guest_condition; atomic_store(&validation_pause, 1);
    pthread_t mutation; assert(!pthread_create(&mutation, NULL, shrink_during_validation, NULL));
    r[0] = mutex; r[1] = validation_key; r[2] = BOTW_KERNEL_INITIAL_THREAD; r[3] = 0;
    /* The external key validator pauses after owned-mutex validation. Another
       real pthread removes that heap page before the kernel takes its lock. */
    svc(0x1c, r); assert(r[0] == BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE);
    assert(!pthread_join(mutation, NULL) && atomic_load(&guest_condition) == 0);
}
int main(int argc, char **argv) {
    assert(argc == 2); botw_kernel_callbacks callbacks = {0}; callbacks.memory = memory;
    callbacks.executable = executable; callbacks.execute_thread = execute_thread;
    ctx = botw_kernel_create(&callbacks, NULL); assert(ctx);
    if (!strcmp(argv[1], "events")) events();
    else if (!strcmp(argv[1], "multi")) multi_wait();
    else if (!strcmp(argv[1], "timing")) timing();
    else if (!strcmp(argv[1], "memory")) memory_mapping();
    else if (!strcmp(argv[1], "threads")) threads();
    else if (!strcmp(argv[1], "busy")) shutdown_busy();
    else if (!strcmp(argv[1], "shared")) shared_memory();
    else if (!strcmp(argv[1], "returned")) unexpected_return();
    else if (!strcmp(argv[1], "stopwait")) stop_wait();
    else if (!strcmp(argv[1], "mutex")) mutex_arbitration();
    else if (!strcmp(argv[1], "condition")) condition_relock();
    else if (!strcmp(argv[1], "cvtimeout")) condition_timeout();
    else if (!strcmp(argv[1], "cancel")) cancellation();
    else if (!strcmp(argv[1], "defercancel")) condition_cancel_is_deferred();
    else if (!strcmp(argv[1], "stopsync")) shutdown_sync_waits();
    else if (!strcmp(argv[1], "retainwait")) pending_wait_memory_is_retained();
    else if (!strcmp(argv[1], "validationrace")) revalidate_before_wait_registration();
    else abort();
    botw_kernel_destroy(ctx); puts("ok"); return 0;
}
'''


class KernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='botw-kernel-')
        cls.exe = Path(cls.build.name) / 'kernel-fixture'
        source = ROOT / 'native/botw_host/kernel.c'
        if not source.exists():
            raise AssertionError('Horizon kernel backend is missing')
        fixture = Path(cls.build.name) / 'fixture.c'
        fixture.write_text(FIXTURE)
        subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                        '-Werror', '-pthread', '-I', str(source.parent), str(fixture),
                        str(source), '-o', str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def check_case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'ok')

    def test_event_wait_wakeup_timeout_and_independent_handle_lifetimes(self):
        self.check_case('events')

    def test_wait_any_reports_index_and_rejects_invalid_guest_array(self):
        self.check_case('multi')

    def test_sleep_tick_thread_id_and_explicit_unsupported(self):
        self.check_case('timing')

    def test_heap_budget_alias_permissions_and_4k_16k_boundaries(self):
        self.check_case('memory')

    def test_create_start_exit_uses_mapped_stack_and_thread_local_context(self):
        self.check_case('threads')

    def test_shutdown_refuses_to_free_running_cpu_thread(self):
        self.check_case('busy')

    def test_shared_memory_has_real_aliases_and_enforced_guest_permissions(self):
        self.check_case('shared')

    def test_callback_return_without_exit_is_reported_to_owner(self):
        self.check_case('returned')

    def test_shutdown_cancels_infinite_event_wait(self):
        self.check_case('stopwait')

    def test_mutex_priority_handoff_waiter_bits_and_inheritance(self):
        self.check_case('mutex')

    def test_condition_partial_signal_relocks_mutex_before_return(self):
        self.check_case('condition')

    def test_condition_timeout_releases_mutex_for_sdk_to_relock(self):
        self.check_case('cvtimeout')

    def test_cancel_wait_and_pending_cancel_are_consumed_once(self):
        self.check_case('cancel')

    def test_cancel_condition_is_deferred_until_next_synchronization_wait(self):
        self.check_case('defercancel')

    def test_shutdown_wakes_condition_and_mutex_waiters(self):
        self.check_case('stopsync')

    def test_pending_wait_addresses_cannot_be_removed_or_borrowed(self):
        self.check_case('retainwait')

    def test_wait_revalidates_owned_mutex_after_concurrent_heap_shrink(self):
        self.check_case('validationrace')


if __name__ == '__main__':
    unittest.main()
