/* Horizon kernel objects and host memory/thread integration.
 * Design derived from jhaizhou-ops/d2r-mobile (MIT), commit
 * e2a0c9a9b5d65f6cd505e967e4760e2745e90ca8. See THIRD_PARTY_LICENSE.txt.
 * ABI cross-checked against switchbrew/libnx nx/source/kernel/svc.s. */
#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "kernel.h"
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <sched.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/mman.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach/mach.h>
#include <mach/mach_vm.h>
#endif

#define MAX_HANDLES 2048
#define MAX_WAIT 64
#define HANDLE_BASE 0x20000u
#define MAX_MAPS 1024
#define MAX_SHARED_MAPS 64
#define HEAP_UNIT ((size_t)0x200000)
#define GUEST_PAGE ((size_t)0x1000)
#define ADDRESS_LIMIT (UINT64_C(1) << 36)
typedef struct { uint64_t destination, source; size_t size; unsigned users; } Mapping;
typedef struct Object Object;
typedef struct Thread Thread;
typedef struct SyncWaiter SyncWaiter;
typedef struct SharedMemory SharedMemory;
typedef struct { uint64_t address; SharedMemory *memory; unsigned permission; } SharedMapping;
typedef struct { uint32_t value; unsigned kind; Object *object; } Handle;
enum { EVENT_WRITE = 1, EVENT_READ = 2, THREAD = 3, SHARED_MEMORY = 4 };
struct Object { unsigned refs, type; int signaled; };
struct SharedMemory {
    Object object;
    FILE *file;
    void *backing;
    size_t size;
    unsigned remote_permission;
};
struct Thread {
    Object object;
    Thread *next;
    botw_kernel *kernel;
    pthread_t host;
    uint64_t entry, argument, stack_top, id;
    uint32_t handle;
    int priority, effective_priority, core, started, finished, joined, joining, exit_requested;
    int cancel_pending;
    SyncWaiter *sync_waiter;
    Mapping *mapping;
};
enum { WAIT_MUTEX = 1, WAIT_CONDITION = 2, WAIT_COMPLETE = 3 };
/* Wait nodes live on the waiting pthread's stack. The kernel lock protects
 * insertion, phase transitions, ownership handoff and final removal. */
struct SyncWaiter {
    SyncWaiter *next;
    Thread *thread, *owner;
    uint64_t mutex_address, condition_key, sequence;
    uint32_t tag, result;
    unsigned phase;
};
struct botw_kernel {
    botw_kernel_callbacks callbacks;
    void *opaque;
    pthread_mutex_t lock;
    pthread_cond_t changed;
    pthread_t owner;
    pthread_key_t current_thread;
    Thread *threads;
    Thread initial_thread;
    SyncWaiter *sync_waiters;
    uint64_t next_wait_sequence;
    uint64_t next_thread_id;
    char thread_failure[512];
    uint32_t next_handle;
    unsigned active_waits;
    int stopping;
    uint8_t *reservation;
    size_t reservation_size, heap_capacity, stack_capacity, alias_capacity;
    size_t physical_budget, image_bytes, heap_size, host_page;
    uint64_t heap_base, stack_base, alias_base;
    int memory_configured;
    Mapping mappings[MAX_MAPS];
    SharedMapping shared_mappings[MAX_SHARED_MAPS];
    Handle handles[MAX_HANDLES];
};
static int diagnostic(char *error, size_t capacity, const char *format, ...) {
    if (error && capacity) {
        va_list ap; va_start(ap, format); vsnprintf(error, capacity, format, ap); va_end(ap);
    }
    return -1;
}
static uint64_t monotonic_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
/* changed uses a monotonic clock on Linux; Darwin provides relative monotonic
 * waits directly. Wall-clock adjustments cannot extend a guest timeout. */
static int condition_wait(botw_kernel *k, uint64_t deadline, int infinite) {
    if (infinite) return pthread_cond_wait(&k->changed, &k->lock);
    uint64_t now = monotonic_ns();
    if (now >= deadline) return ETIMEDOUT;
    struct timespec t;
#ifdef __APPLE__
    uint64_t remaining = deadline - now;
    t.tv_sec = (time_t)(remaining / UINT64_C(1000000000));
    t.tv_nsec = (long)(remaining % UINT64_C(1000000000));
    return pthread_cond_timedwait_relative_np(&k->changed, &k->lock, &t);
#else
    t.tv_sec = (time_t)(deadline / UINT64_C(1000000000));
    t.tv_nsec = (long)(deadline % UINT64_C(1000000000));
    return pthread_cond_timedwait(&k->changed, &k->lock, &t);
#endif
}
static uint64_t timeout_deadline(int64_t timeout) {
    uint64_t now = monotonic_ns(), delta = timeout > 0 ? (uint64_t)timeout : 0;
    return delta > UINT64_MAX - now ? UINT64_MAX : now + delta;
}
static Handle *find_handle(botw_kernel *k, uint32_t value) {
    for (unsigned i = 0; i < MAX_HANDLES; i++)
        if (k->handles[i].object && k->handles[i].value == value) return &k->handles[i];
    return NULL;
}
static Handle *allocate_handle(botw_kernel *k, Object *object, unsigned kind) {
    if (k->next_handle >= UINT32_C(0x7fffffff)) return NULL;
    for (unsigned i = 0; i < MAX_HANDLES; i++) if (!k->handles[i].object) {
        Handle *h = &k->handles[i];
        *h = (Handle){k->next_handle++, kind, object}; object->refs++; return h;
    }
    return NULL;
}
static void release_object(Object *object) {
    if (--object->refs) return;
    if (object->type == SHARED_MEMORY) {
        SharedMemory *memory = (SharedMemory *)object;
        munmap(memory->backing, memory->size); fclose(memory->file);
    }
    free(object);
}
botw_kernel *botw_kernel_create(const botw_kernel_callbacks *callbacks, void *opaque) {
    botw_kernel *k = calloc(1, sizeof *k); if (!k) return NULL;
    if (callbacks) k->callbacks = *callbacks;
    k->opaque = opaque; k->owner = pthread_self(); k->next_handle = HANDLE_BASE;
    k->next_thread_id = 2;
    k->initial_thread.kernel = k; k->initial_thread.host = pthread_self();
    k->initial_thread.handle = BOTW_KERNEL_INITIAL_THREAD; k->initial_thread.id = 1;
    k->initial_thread.priority = k->initial_thread.effective_priority = 44;
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0 || (page & (page - 1))) { free(k); return NULL; }
    k->host_page = (size_t)page;
    if (pthread_mutex_init(&k->lock, NULL)) { free(k); return NULL; }
    pthread_condattr_t attr;
    if (pthread_condattr_init(&attr)) { pthread_mutex_destroy(&k->lock); free(k); return NULL; }
#ifndef __APPLE__
    if (pthread_condattr_setclock(&attr, CLOCK_MONOTONIC)) {
        pthread_condattr_destroy(&attr); pthread_mutex_destroy(&k->lock); free(k); return NULL;
    }
#endif
    int result = pthread_cond_init(&k->changed, &attr); pthread_condattr_destroy(&attr);
    if (result) { pthread_mutex_destroy(&k->lock); free(k); return NULL; }
    if (pthread_key_create(&k->current_thread, NULL)) {
        pthread_cond_destroy(&k->changed); pthread_mutex_destroy(&k->lock); free(k); return NULL;
    }
    return k;
}
/* Called with lock held, returns with lock held. A mapped pthread stack cannot
 * be released just because its cleanup handler ran: pthread/TSD destruction is
 * still using it. Only pthread_join releases this final mapping reference. */
static int join_finished_locked(botw_kernel *k, Thread *t) {
    if (t->joined) return 0;
    if (!t->started) { t->finished = 1; t->joined = 1; }
    else {
        if (!t->finished || t->joining) return EBUSY;
        t->joining = 1;
        pthread_mutex_unlock(&k->lock); int result = pthread_join(t->host, NULL); pthread_mutex_lock(&k->lock);
        t->joining = 0;
        if (result) return result;
        t->joined = 1;
    }
    if (t->mapping) { t->mapping->users--; t->mapping = NULL; }
    t->object.signaled = 1; pthread_cond_broadcast(&k->changed); return 0;
}
int botw_kernel_shutdown(botw_kernel *k, char *error, size_t capacity) {
    if (!k) return diagnostic(error, capacity, "kernel context is null");
    pthread_mutex_lock(&k->lock); k->stopping = 1; pthread_cond_broadcast(&k->changed);
    unsigned waiting = k->active_waits, running = 0;
    for (Thread *t = k->threads; t; t = t->next) if (t->started && !t->finished) running++;
    if (running) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "kernel still has %u running guest threads; resources retained", running); }
    for (Thread *t = k->threads; t; t = t->next) {
        int result = join_finished_locked(k, t);
        if (result) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "guest thread join not complete: %s", strerror(result)); }
    }
    pthread_mutex_unlock(&k->lock);
    if (waiting) return diagnostic(error, capacity, "kernel still has %u active wait/sleep calls", waiting);
    return 0;
}
void botw_kernel_destroy(botw_kernel *k) {
    if (!k) return;
    char error[256];
    if (botw_kernel_shutdown(k, error, sizeof error)) {
        if (k->callbacks.log) k->callbacks.log(k->opaque, error);
        return;
    }
    for (unsigned i = 0; i < MAX_HANDLES; i++) if (k->handles[i].object) release_object(k->handles[i].object);
    for (unsigned i = 0; i < MAX_SHARED_MAPS; i++) if (k->shared_mappings[i].memory)
        release_object(&k->shared_mappings[i].memory->object);
    Thread *thread = k->threads;
    while (thread) { Thread *next = thread->next; release_object(&thread->object); thread = next; }
    if (k->reservation) munmap(k->reservation, k->reservation_size);
    pthread_key_delete(k->current_thread);
    pthread_cond_destroy(&k->changed); pthread_mutex_destroy(&k->lock); free(k);
}
int botw_kernel_create_event(botw_kernel *k, int initial, uint32_t *w, uint32_t *r, char *error, size_t capacity) {
    if (!k || !w || !r) return diagnostic(error, capacity, "event creation requires kernel and outputs");
    Object *event = calloc(1, sizeof *event);
    if (!event) return diagnostic(error, capacity, "allocate event: %s", strerror(errno));
    event->signaled = !!initial;
    pthread_mutex_lock(&k->lock);
    Handle *write = k->stopping ? NULL : allocate_handle(k, event, EVENT_WRITE);
    Handle *read = write ? allocate_handle(k, event, EVENT_READ) : NULL;
    if (!read) {
        if (write) memset(write, 0, sizeof *write);
        pthread_mutex_unlock(&k->lock); free(event);
        return diagnostic(error, capacity, "kernel stopping or event handle table exhausted");
    }
    *w = write->value; *r = read->value; pthread_mutex_unlock(&k->lock); return 0;
}
int botw_kernel_duplicate_handle(botw_kernel *k, uint32_t h, uint32_t *d, char *e, size_t c) {
    if (!k || !d) return diagnostic(e, c, "duplicate requires kernel and output");
    pthread_mutex_lock(&k->lock);
    Handle *source = find_handle(k, h), *copy = NULL;
    if (source && !k->stopping) copy = allocate_handle(k, source->object, source->kind);
    if (copy) *d = copy->value;
    pthread_mutex_unlock(&k->lock);
    return copy ? 0 : diagnostic(e, c, "cannot duplicate kernel handle 0x%x", h);
}
int botw_kernel_signal_event(botw_kernel *k, uint32_t h, char *e, size_t c) {
    if (!k) return diagnostic(e, c, "kernel context is null");
    pthread_mutex_lock(&k->lock); Handle *handle = find_handle(k, h);
    int valid = handle && handle->kind == EVENT_WRITE;
    if (valid) { handle->object->signaled = 1; pthread_cond_broadcast(&k->changed); }
    pthread_mutex_unlock(&k->lock);
    return valid ? 0 : diagnostic(e, c, "handle 0x%x is not a writable event", h);
}
int botw_kernel_clear_event(botw_kernel *k, uint32_t h, char *e, size_t c) {
    if (!k) return diagnostic(e, c, "kernel context is null");
    pthread_mutex_lock(&k->lock); Handle *handle = find_handle(k, h);
    int valid = handle && (handle->kind == EVENT_READ || handle->kind == EVENT_WRITE);
    if (valid) handle->object->signaled = 0;
    pthread_mutex_unlock(&k->lock);
    return valid ? 0 : diagnostic(e, c, "handle 0x%x is not an event", h);
}
int botw_kernel_close_handle(botw_kernel *k, uint32_t h, char *e, size_t c) {
    if (!k) return diagnostic(e, c, "kernel context is null");
    pthread_mutex_lock(&k->lock); Handle *handle = find_handle(k, h);
    int valid = handle != NULL;
    if (valid) { release_object(handle->object); memset(handle, 0, sizeof *handle); }
    pthread_mutex_unlock(&k->lock);
    return valid ? 0 : diagnostic(e, c, "kernel handle 0x%x is invalid or closed", h);
}
int botw_kernel_is_process_handle(botw_kernel *k, uint32_t h) { return k && h == BOTW_KERNEL_CURRENT_PROCESS; }
int botw_kernel_create_shared_memory(botw_kernel *k, size_t size, unsigned permission,
    uint32_t *handle, void **backing, char *error, size_t capacity) {
    if (!k || !handle || !backing || !size || size > (size_t)INT64_MAX ||
        (size & (k->host_page - 1)) || (permission != 1 && permission != 3))
        return diagnostic(error, capacity, "shared memory requires whole host pages and remote Read/ReadWrite permission");
    SharedMemory *memory = calloc(1, sizeof *memory);
    if (!memory) return diagnostic(error, capacity, "allocate shared memory metadata: %s", strerror(errno));
    memory->file = tmpfile();
    if (!memory->file) { free(memory); return diagnostic(error, capacity, "create private shared-memory backing: %s", strerror(errno)); }
    if (ftruncate(fileno(memory->file), (off_t)size)) {
        fclose(memory->file); free(memory); return diagnostic(error, capacity, "size shared-memory backing: %s", strerror(errno));
    }
    memory->backing = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fileno(memory->file), 0);
    if (memory->backing == MAP_FAILED) {
        fclose(memory->file); free(memory); return diagnostic(error, capacity, "map shared-memory backing: %s", strerror(errno));
    }
    memory->size = size; memory->remote_permission = permission; memory->object.type = SHARED_MEMORY;
    pthread_mutex_lock(&k->lock);
    Handle *h = k->stopping ? NULL : allocate_handle(k, &memory->object, SHARED_MEMORY);
    if (!h) {
        pthread_mutex_unlock(&k->lock); memory->object.refs = 1; release_object(&memory->object);
        return diagnostic(error, capacity, "kernel stopping or shared-memory handle table exhausted");
    }
    *handle = h->value; *backing = memory->backing;
    pthread_mutex_unlock(&k->lock); return 0;
}
static int guest_memory(botw_kernel *k, uint64_t address, size_t length, int writable) {
    if (!address || address > UINT64_MAX - length) return 0;
    return botw_kernel_memory(k, address, length, writable) ||
        (k->callbacks.memory && k->callbacks.memory(k->opaque, address, length, writable));
}
static Thread *current_guest_thread(botw_kernel *k) {
    Thread *thread = pthread_getspecific(k->current_thread);
    return thread ? thread : pthread_equal(pthread_self(), k->owner) ? &k->initial_thread : NULL;
}
static Thread *thread_from_handle_locked(botw_kernel *k, uint32_t handle, int pseudo) {
    if (handle == BOTW_KERNEL_INITIAL_THREAD) return &k->initial_thread;
    if (pseudo && handle == BOTW_KERNEL_CURRENT_THREAD) return current_guest_thread(k);
    Handle *h = find_handle(k, handle);
    return h && h->kind == THREAD ? (Thread *)h->object : NULL;
}
/* Recompute the fixed point of virtual Horizon priority donation, including
 * chains of owners blocked on another mutex. Queue selection uses these
 * effective priorities; host QoS remains the pthread policy requested at start. */
static void recompute_priorities_locked(botw_kernel *k) {
    k->initial_thread.effective_priority = k->initial_thread.priority;
    unsigned count = 1;
    for (Thread *t = k->threads; t; t = t->next) { t->effective_priority = t->priority; count++; }
    for (unsigned pass = 0; pass < count; pass++) {
        int changed = 0;
        for (SyncWaiter *w = k->sync_waiters; w; w = w->next) if (w->phase == WAIT_MUTEX && w->owner &&
                w->owner->effective_priority > w->thread->effective_priority) {
            w->owner->effective_priority = w->thread->effective_priority; changed = 1;
        }
        if (!changed) break;
    }
}
static void link_sync_waiter_locked(botw_kernel *k, SyncWaiter *w) {
    w->sequence = k->next_wait_sequence++; w->next = k->sync_waiters; k->sync_waiters = w;
    w->thread->sync_waiter = w; k->active_waits++; recompute_priorities_locked(k);
}
static void unlink_sync_waiter_locked(botw_kernel *k, SyncWaiter *w) {
    SyncWaiter **cursor = &k->sync_waiters;
    while (*cursor && *cursor != w) cursor = &(*cursor)->next;
    if (*cursor) *cursor = w->next;
    w->thread->sync_waiter = NULL; k->active_waits--; recompute_priorities_locked(k);
}
static int waiter_precedes(const SyncWaiter *a, const SyncWaiter *b) {
    return !b || a->thread->effective_priority < b->thread->effective_priority ||
        (a->thread->effective_priority == b->thread->effective_priority && a->sequence < b->sequence);
}
/* Releasing and selecting the next owner occur under the same kernel lock as
 * enqueueing. A user-space CAS sees either the old owner or the fully written
 * next-owner tag, never an unlocked gap when a kernel waiter is selected. */
static void release_guest_mutex_locked(botw_kernel *k, uint64_t address, Thread *owner) {
    SyncWaiter *next = NULL;
    for (SyncWaiter *w = k->sync_waiters; w; w = w->next)
        if (w->phase == WAIT_MUTEX && w->mutex_address == address && w->owner == owner && waiter_precedes(w, next)) next = w;
    uint32_t value = 0;
    if (next) {
        value = next->tag; int remaining = 0;
        for (SyncWaiter *w = k->sync_waiters; w; w = w->next) if (w != next && w->phase == WAIT_MUTEX &&
                w->mutex_address == address && w->owner == owner) { w->owner = next->thread; remaining = 1; }
        if (remaining) value |= BOTW_KERNEL_MUTEX_HAS_WAITERS;
        next->phase = WAIT_COMPLETE; next->owner = NULL; next->result = 0;
    }
    __atomic_store_n((uint32_t *)(uintptr_t)address, value, __ATOMIC_RELEASE);
    recompute_priorities_locked(k); pthread_cond_broadcast(&k->changed);
}
static int await_sync_waiter_locked(botw_kernel *k, SyncWaiter *w, int64_t timeout,
                                    char *error, size_t capacity) {
    uint64_t deadline = timeout_deadline(timeout); int rc = 0;
    for (;;) {
        if (k->stopping) { w->result = BOTW_KERNEL_RESULT_CANCELLED; break; }
        if (w->phase == WAIT_COMPLETE) break;
        if (!timeout || rc == ETIMEDOUT) { w->result = BOTW_KERNEL_RESULT_TIMED_OUT; break; }
        if (rc) break;
        rc = condition_wait(k, deadline, timeout < 0);
    }
    unlink_sync_waiter_locked(k, w);
    if (rc && rc != ETIMEDOUT) return diagnostic(error, capacity, "host guest-mutex/condition wait failed: %s", strerror(rc));
    return 0;
}
static int arbitrate_lock(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    uint32_t owner_handle = (uint32_t)regs[0], tag = (uint32_t)regs[2]; uint64_t address = regs[1];
    if (address & 3) { regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED; }
    if (!guest_memory(k, address, 4, 1)) { regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED; }
    pthread_mutex_lock(&k->lock); Thread *current = current_guest_thread(k);
    if (!current) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "mutex arbitration requires a registered guest thread"); }
    if (k->stopping) { pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_CANCELLED; return BOTW_KERNEL_HANDLED; }
    if (__atomic_load_n((uint32_t *)(uintptr_t)address, __ATOMIC_ACQUIRE) != (owner_handle | BOTW_KERNEL_MUTEX_HAS_WAITERS)) {
        pthread_mutex_unlock(&k->lock); regs[0] = 0; return BOTW_KERNEL_HANDLED;
    }
    Thread *owner = thread_from_handle_locked(k, owner_handle, 0);
    if (!owner) { pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE; return BOTW_KERNEL_HANDLED; }
    SyncWaiter w = {.thread = current, .owner = owner, .mutex_address = address, .tag = tag, .phase = WAIT_MUTEX};
    link_sync_waiter_locked(k, &w);
    int result = await_sync_waiter_locked(k, &w, -1, error, capacity);
    pthread_mutex_unlock(&k->lock);
    if (result) return BOTW_KERNEL_ERROR;
    regs[0] = w.result; return BOTW_KERNEL_HANDLED;
}
static int arbitrate_unlock(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    uint64_t address = regs[0];
    if (address & 3) { regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED; }
    if (!guest_memory(k, address, 4, 1)) { regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED; }
    pthread_mutex_lock(&k->lock); Thread *current = current_guest_thread(k);
    if (!current) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "mutex unlock requires a registered guest thread"); }
    release_guest_mutex_locked(k, address, current); pthread_mutex_unlock(&k->lock);
    regs[0] = 0; return BOTW_KERNEL_HANDLED;
}
static int wait_process_key(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    uint64_t address = regs[0], key = regs[1] & ~UINT64_C(3); uint32_t tag = (uint32_t)regs[2];
    int64_t timeout = (int64_t)regs[3];
    if (address & 3) { regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED; }
    if (!guest_memory(k, address, 4, 1) || !guest_memory(k, key, 4, 1)) {
        regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    pthread_mutex_lock(&k->lock); Thread *current = current_guest_thread(k);
    if (!current) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "condition wait requires a registered guest thread"); }
    if (k->stopping) { pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_CANCELLED; return BOTW_KERNEL_HANDLED; }
    __atomic_store_n((uint32_t *)(uintptr_t)key, 1, __ATOMIC_RELEASE);
    release_guest_mutex_locked(k, address, current);
    if (!timeout) { pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_TIMED_OUT; return BOTW_KERNEL_HANDLED; }
    SyncWaiter w = {.thread = current, .mutex_address = address, .condition_key = key, .tag = tag, .phase = WAIT_CONDITION};
    link_sync_waiter_locked(k, &w);
    int result = await_sync_waiter_locked(k, &w, timeout, error, capacity);
    pthread_mutex_unlock(&k->lock);
    if (result) return BOTW_KERNEL_ERROR;
    regs[0] = w.result; return BOTW_KERNEL_HANDLED;
}
static void reacquire_condition_mutex_locked(botw_kernel *k, SyncWaiter *w) {
    uint32_t *mutex = (uint32_t *)(uintptr_t)w->mutex_address;
    uint32_t previous = __atomic_load_n(mutex, __ATOMIC_ACQUIRE);
    for (;;) {
        uint32_t replacement = previous ? previous | BOTW_KERNEL_MUTEX_HAS_WAITERS : w->tag;
        if (__atomic_compare_exchange_n(mutex, &previous, replacement, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) break;
    }
    if (!previous) { w->phase = WAIT_COMPLETE; w->result = 0; return; }
    Thread *owner = thread_from_handle_locked(k, previous & ~BOTW_KERNEL_MUTEX_HAS_WAITERS, 0);
    if (!owner) { w->phase = WAIT_COMPLETE; w->result = BOTW_KERNEL_RESULT_INVALID_STATE; return; }
    w->phase = WAIT_MUTEX; w->owner = owner;
}
static int signal_process_key(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    uint64_t key = regs[0] & ~UINT64_C(3); int32_t requested = (int32_t)regs[1];
    if (!guest_memory(k, key, 4, 1)) return diagnostic(error, capacity, "condition key is not aligned guest writable memory");
    pthread_mutex_lock(&k->lock);
    unsigned waiting = 0, woken = 0, remaining = 0;
    for (SyncWaiter *w = k->sync_waiters; w; w = w->next) if (w->phase == WAIT_CONDITION && w->condition_key == key) waiting++;
    while (requested <= 0 || woken < (unsigned)requested) {
        SyncWaiter *next = NULL;
        for (SyncWaiter *w = k->sync_waiters; w; w = w->next)
            if (w->phase == WAIT_CONDITION && w->condition_key == key && waiter_precedes(w, next)) next = w;
        if (!next) break;
        reacquire_condition_mutex_locked(k, next); woken++; recompute_priorities_locked(k);
    }
    for (SyncWaiter *w = k->sync_waiters; w; w = w->next) if (w->phase == WAIT_CONDITION && w->condition_key == key) remaining++;
    if (!remaining) __atomic_store_n((uint32_t *)(uintptr_t)key, 0, __ATOMIC_RELEASE);
    pthread_cond_broadcast(&k->changed); pthread_mutex_unlock(&k->lock);
    if (k->callbacks.log) {
        char message[160]; snprintf(message, sizeof message, "condition_signal requested=%d waiters=%u woken=%u remaining=%u", requested, waiting, woken, remaining);
        k->callbacks.log(k->opaque, message);
    }
    regs[0] = 0; return BOTW_KERNEL_HANDLED;
}
static int wait_synchronization(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    int32_t count = (int32_t)regs[2]; int64_t timeout = (int64_t)regs[3];
    if (count < 0 || count > MAX_WAIT) { regs[0] = BOTW_KERNEL_RESULT_OUT_OF_RANGE; return BOTW_KERNEL_HANDLED; }
    if (count && !guest_memory(k, regs[1], (size_t)count * 4, 0)) {
        regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED;
    }
    uint32_t handles[MAX_WAIT]; Object *objects[MAX_WAIT];
    if (count) memcpy(handles, (const void *)(uintptr_t)regs[1], (size_t)count * 4);
    pthread_mutex_lock(&k->lock);
    for (int32_t i = 0; i < count; i++) {
        Handle *handle = find_handle(k, handles[i]);
        if (!handle || (handle->kind != EVENT_READ && handle->kind != THREAD)) {
            pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE; return BOTW_KERNEL_HANDLED;
        }
        objects[i] = handle->object;
    }
    for (int32_t i = 0; i < count; i++) objects[i]->refs++;
    k->active_waits++;
    Thread *current = current_guest_thread(k);
    uint64_t deadline = timeout_deadline(timeout); uint32_t result = 0; int rc = 0;
    for (;;) {
        if (k->stopping) { result = BOTW_KERNEL_RESULT_CANCELLED; break; }
        for (int32_t i = 0; i < count; i++) if (objects[i]->type == THREAD) {
            Thread *t = (Thread *)objects[i];
            if (t->started && t->finished && !t->joined) {
                int joined = join_finished_locked(k, t);
                if (joined != 0 && joined != EBUSY) { rc = joined; break; }
            }
        }
        if (rc && rc != ETIMEDOUT) break;
        int32_t index;
        for (index = 0; index < count; index++) if (objects[index]->signaled) break;
        if (index < count) { regs[1] = (uint64_t)index; break; }
        if (!timeout || rc == ETIMEDOUT) { result = BOTW_KERNEL_RESULT_TIMED_OUT; break; }
        if (current && current->cancel_pending) { current->cancel_pending = 0; result = BOTW_KERNEL_RESULT_CANCELLED; break; }
        if (rc) break;
        rc = condition_wait(k, deadline, timeout < 0);
    }
    k->active_waits--;
    for (int32_t i = 0; i < count; i++) release_object(objects[i]);
    pthread_mutex_unlock(&k->lock);
    if (rc && rc != ETIMEDOUT) return diagnostic(error, capacity, "host synchronization wait failed: %s", strerror(rc));
    regs[0] = result; return BOTW_KERNEL_HANDLED;
}
static int region_contains(uint64_t base, size_t size, uint64_t address, size_t length) {
    return address >= base && address - base <= size && length <= size - (address - base);
}
static int overlaps(uint64_t a, size_t an, uint64_t b, size_t bn) {
    return a < b + bn && b < a + an;
}
static int sync_words_in_use_locked(botw_kernel *k, uint64_t address, size_t size) {
    for (SyncWaiter *w = k->sync_waiters; w; w = w->next)
        if (overlaps(address, size, w->mutex_address, 4) ||
            (w->condition_key && overlaps(address, size, w->condition_key, 4))) return 1;
    return 0;
}
/* All mappings are internal to the reserved block. Never use MAP_FIXED on a
 * caller-selected or unrelated host address. Nonfixed hints cannot evict VM. */
static int reserve_memory_locked(botw_kernel *k, char *error, size_t capacity) {
    if (k->reservation) return 0;
    if (!k->memory_configured) return diagnostic(error, capacity, "guest memory capacities/budget must be configured first");
    size_t total = k->heap_capacity + k->stack_capacity + k->alias_capacity;
    size_t request = total + HEAP_UNIT;
    for (unsigned attempt = 0; attempt < 16; attempt++) {
        uintptr_t hint = attempt ? UINT64_C(0x100000000) + (uint64_t)(attempt - 1) * UINT64_C(0x100000000) : 0;
        if (hint && request > ADDRESS_LIMIT - hint) continue;
        void *raw = mmap((void *)hint, request, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (raw == MAP_FAILED) continue;
        uintptr_t address = (uintptr_t)raw;
        uintptr_t aligned = (address + HEAP_UNIT - 1) & ~(uintptr_t)(HEAP_UNIT - 1);
        if (address >= ADDRESS_LIMIT || request > ADDRESS_LIMIT - address) { munmap(raw, request); continue; }
        size_t prefix = (size_t)(aligned - address), suffix = request - prefix - total;
        if (prefix) munmap(raw, prefix);
        if (suffix) munmap((void *)(aligned + total), suffix);
        k->reservation = (uint8_t *)aligned; k->reservation_size = total;
        k->alias_base = aligned; k->stack_base = aligned + k->alias_capacity;
        k->heap_base = k->stack_base + k->stack_capacity; return 0;
    }
    return diagnostic(error, capacity, "cannot reserve %zu bytes below 36-bit guest address limit: %s", total, strerror(errno));
}
static int set_heap_size(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    uint64_t requested = regs[1];
    if (requested & (HEAP_UNIT - 1)) { regs[0] = BOTW_KERNEL_RESULT_INVALID_SIZE; return BOTW_KERNEL_HANDLED; }
    pthread_mutex_lock(&k->lock);
    if (!k->memory_configured) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_UNSUPPORTED; }
    if (requested > k->heap_capacity || requested > k->physical_budget - k->image_bytes) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_OUT_OF_MEMORY; return BOTW_KERNEL_HANDLED;
    }
    if (reserve_memory_locked(k, error, capacity)) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_ERROR; }
    size_t size = (size_t)requested;
    if (size < k->heap_size) {
        if (sync_words_in_use_locked(k, k->heap_base + size, k->heap_size - size)) {
            pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
        }
        for (unsigned i = 0; i < MAX_MAPS; i++) if (k->mappings[i].size &&
                overlaps(k->heap_base + size, k->heap_size - size, k->mappings[i].source, k->mappings[i].size)) {
            pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
        }
        size_t remove = k->heap_size - size;
        void *address = (void *)(uintptr_t)(k->heap_base + size);
        if (mmap(address, remove, PROT_NONE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0) == MAP_FAILED) {
            pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "release owned heap pages: %s", strerror(errno));
        }
    } else if (size > k->heap_size && mprotect((void *)(uintptr_t)(k->heap_base + k->heap_size),
                size - k->heap_size, PROT_READ | PROT_WRITE)) {
        pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "commit guest heap: %s", strerror(errno));
    }
    k->heap_size = size; regs[0] = 0; regs[1] = k->heap_base;
    pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
}
/* Both ranges are whole host pages inside this kernel's reservation. Moving
 * their backing is necessary for the physical-memory budget: protecting the
 * old pages after memcpy would retain a second resident copy of every stack.
 * The old address becomes a new empty PROT_NONE placeholder. */
static int move_owned_pages(botw_kernel *k, uint64_t destination, uint64_t source,
                            size_t size, char *error, size_t capacity) {
#ifdef __APPLE__
    (void)k;
    mach_vm_address_t target = destination; vm_prot_t current = 0, maximum = 0;
    kern_return_t result = mach_vm_remap(mach_task_self(), &target, size, 0,
        VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE, mach_task_self(), source, FALSE,
        &current, &maximum, VM_INHERIT_COPY);
    if (result != KERN_SUCCESS || target != destination ||
        (current & (VM_PROT_READ | VM_PROT_WRITE)) != (VM_PROT_READ | VM_PROT_WRITE))
        return diagnostic(error, capacity, "move owned memory backing failed: kernel=%d protection=0x%x", result, current);
    if (mmap((void *)(uintptr_t)source, size, PROT_NONE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0) == MAP_FAILED)
        return diagnostic(error, capacity, "reset moved source placeholder: %s", strerror(errno));
#else
    /* Portable fallback limits transient duplication to one host page and
     * removes each old page before copying the next. */
    for (size_t offset = 0; offset < size; offset += k->host_page) {
        void *to = (void *)(uintptr_t)(destination + offset);
        void *from = (void *)(uintptr_t)(source + offset);
        if (mprotect(to, k->host_page, PROT_READ | PROT_WRITE))
            return diagnostic(error, capacity, "commit moved destination page: %s", strerror(errno));
        memcpy(to, from, k->host_page);
        if (mmap(from, k->host_page, PROT_NONE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0) == MAP_FAILED)
            return diagnostic(error, capacity, "release moved source page: %s", strerror(errno));
    }
#endif
    return 0;
}
static int map_memory(botw_kernel *k, int unmap, uint64_t *regs, char *error, size_t capacity) {
    uint64_t destination = regs[0], source = regs[1], length = regs[2];
    if ((destination | source) & (GUEST_PAGE - 1)) { regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED; }
    if (!length || (length & (GUEST_PAGE - 1))) { regs[0] = BOTW_KERNEL_RESULT_INVALID_SIZE; return BOTW_KERNEL_HANDLED; }
    if (source > UINT64_MAX - length || destination > UINT64_MAX - length || length > SIZE_MAX) {
        regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    pthread_mutex_lock(&k->lock);
    if (!k->memory_configured) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_UNSUPPORTED; }
    if (reserve_memory_locked(k, error, capacity)) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_ERROR; }
    size_t size = (size_t)length;
    if (!region_contains(k->heap_base, k->heap_size, source, size) ||
        !region_contains(k->stack_base, k->stack_capacity, destination, size) ||
        sync_words_in_use_locked(k, source, size) || sync_words_in_use_locked(k, destination, size)) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    if ((destination | source | size) & (k->host_page - 1)) {
        size_t page = k->host_page; pthread_mutex_unlock(&k->lock);
        return diagnostic(error, capacity, "guest MapMemory requires complete host page boundaries (%zu bytes); refusing partial protection", page);
    }
    Mapping *mapping = NULL, *free_slot = NULL;
    for (unsigned i = 0; i < MAX_MAPS; i++) {
        Mapping *m = &k->mappings[i];
        if (!m->size) { if (!free_slot) free_slot = m; continue; }
        if (m->source == source && m->destination == destination && m->size == size) mapping = m;
        if (!unmap && (overlaps(source, size, m->source, m->size) || overlaps(destination, size, m->destination, m->size))) {
            pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
        }
    }
    if ((unmap && (!mapping || mapping->users)) || (!unmap && !free_slot)) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    if (unmap) {
        if (move_owned_pages(k, source, destination, size, error, capacity)) {
            pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_ERROR;
        }
        memset(mapping, 0, sizeof *mapping);
    } else {
        if (move_owned_pages(k, destination, source, size, error, capacity)) {
            pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_ERROR;
        }
        *free_slot = (Mapping){destination, source, size, 0};
    }
    pthread_mutex_unlock(&k->lock); regs[0] = 0; return BOTW_KERNEL_HANDLED;
}
static int map_shared_memory(botw_kernel *k, int unmap, uint64_t *regs, char *error, size_t capacity) {
    uint32_t handle = (uint32_t)regs[0]; uint64_t address = regs[1], length = regs[2];
    unsigned permission = (uint32_t)regs[3];
    if (address & (GUEST_PAGE - 1)) { regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED; }
    if (!length || length > SIZE_MAX || (length & (GUEST_PAGE - 1))) { regs[0] = BOTW_KERNEL_RESULT_INVALID_SIZE; return BOTW_KERNEL_HANDLED; }
    pthread_mutex_lock(&k->lock); Handle *h = find_handle(k, handle);
    if (!h || h->kind != SHARED_MEMORY) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE; return BOTW_KERNEL_HANDLED;
    }
    SharedMemory *memory = (SharedMemory *)h->object;
    if (length != memory->size) { pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_SIZE; return BOTW_KERNEL_HANDLED; }
    if (!unmap && ((permission != 1 && permission != 3) || (permission & memory->remote_permission) != permission)) {
        pthread_mutex_unlock(&k->lock); regs[0] = 0xd801; return BOTW_KERNEL_HANDLED;
    }
    if (reserve_memory_locked(k, error, capacity)) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_ERROR; }
    if (!region_contains(k->alias_base, k->alias_capacity, address, memory->size)) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    if (address & (k->host_page - 1)) {
        size_t page = k->host_page; pthread_mutex_unlock(&k->lock);
        return diagnostic(error, capacity, "shared-memory address must align to host page (%zu bytes)", page);
    }
    SharedMapping *found = NULL, *slot = NULL;
    for (unsigned i = 0; i < MAX_SHARED_MAPS; i++) {
        SharedMapping *m = &k->shared_mappings[i];
        if (!m->memory) { if (!slot) slot = m; continue; }
        if (m->address == address && m->memory == memory) found = m;
        if (!unmap && overlaps(address, memory->size, m->address, m->memory->size)) {
            pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
        }
    }
    if ((unmap && !found) || (!unmap && !slot)) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    if (unmap && sync_words_in_use_locked(k, address, memory->size)) {
        pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE; return BOTW_KERNEL_HANDLED;
    }
    if (unmap) {
        if (mmap((void *)(uintptr_t)address, memory->size, PROT_NONE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0) == MAP_FAILED) {
            pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "unmap owned shared-memory alias: %s", strerror(errno));
        }
        memset(found, 0, sizeof *found); release_object(&memory->object);
    } else {
        int protection = PROT_READ | (permission == 3 ? PROT_WRITE : 0);
        if (mmap((void *)(uintptr_t)address, memory->size, protection, MAP_SHARED | MAP_FIXED, fileno(memory->file), 0) == MAP_FAILED) {
            pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "map real shared-memory alias: %s", strerror(errno));
        }
        *slot = (SharedMapping){address, memory, permission}; memory->object.refs++;
    }
    pthread_mutex_unlock(&k->lock); regs[0] = 0; return BOTW_KERNEL_HANDLED;
}
static int get_info(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    unsigned id = (uint32_t)regs[1];
    switch (id) { case 2: case 3: case 4: case 5: case 6: case 7:
        case 12: case 13: case 14: case 15: case 21: case 22: break;
        default: return BOTW_KERNEL_UNSUPPORTED;
    }
    if (!botw_kernel_is_process_handle(k, (uint32_t)regs[2])) { regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE; return BOTW_KERNEL_HANDLED; }
    if (regs[3]) { regs[0] = BOTW_KERNEL_RESULT_OUT_OF_RANGE; return BOTW_KERNEL_HANDLED; }
    pthread_mutex_lock(&k->lock);
    if (!k->memory_configured) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_UNSUPPORTED; }
    if (reserve_memory_locked(k, error, capacity)) { pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_ERROR; }
    uint64_t value = 0;
    switch (id) {
    case 2: value = k->alias_base; break; case 3: value = k->alias_capacity; break;
    case 4: value = k->heap_base; break; case 5: value = k->heap_capacity; break;
    case 6: case 21: value = k->physical_budget; break;
    case 7: case 22: value = k->image_bytes + k->heap_size; break;
    case 12: value = (uintptr_t)k->reservation; break; case 13: value = k->reservation_size; break;
    case 14: value = k->stack_base; break; case 15: value = k->stack_capacity; break;
    }
    pthread_mutex_unlock(&k->lock); regs[0] = 0; regs[1] = value; return BOTW_KERNEL_HANDLED;
}
static void thread_cleanup(void *opaque) {
    Thread *thread = opaque; botw_kernel *k = thread->kernel;
    pthread_mutex_lock(&k->lock); thread->finished = 1;
    if (!thread->exit_requested && !k->thread_failure[0]) {
        snprintf(k->thread_failure, sizeof k->thread_failure, "guest thread 0x%x returned without SVC ExitThread", thread->handle);
        k->stopping = 1;
    }
    pthread_cond_broadcast(&k->changed); pthread_mutex_unlock(&k->lock);
    pthread_setspecific(k->current_thread, NULL);
}
static void *thread_main(void *opaque) {
    Thread *thread = opaque; botw_kernel *k = thread->kernel;
    char error[512] = {0};
    pthread_setspecific(k->current_thread, thread);
    pthread_cleanup_push(thread_cleanup, thread);
    int result = k->callbacks.execute_thread(k->opaque, thread->entry, thread->argument,
        thread->stack_top, thread->handle, error, sizeof error);
    pthread_mutex_lock(&k->lock);
    if (!k->thread_failure[0]) snprintf(k->thread_failure, sizeof k->thread_failure,
        "guest thread 0x%x %s: %.400s", thread->handle,
        result ? "callback failed" : "entry returned without SVC ExitThread", error);
    k->stopping = 1; pthread_cond_broadcast(&k->changed); pthread_mutex_unlock(&k->lock);
    pthread_cleanup_pop(1);
    return NULL;
}
static int create_thread(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    if (!k->callbacks.execute_thread || !k->callbacks.executable) return BOTW_KERNEL_UNSUPPORTED;
    uint64_t entry = regs[1], argument = regs[2], stack_top = regs[3];
    int32_t priority = (int32_t)regs[4], core = (int32_t)regs[5];
    if (priority < 0 || priority > 63) { regs[0] = 0xe001; return BOTW_KERNEL_HANDLED; }
    if (core < -2 || core == -1 || core > 3) { regs[0] = 0xe201; return BOTW_KERNEL_HANDLED; }
    if ((entry & 3) || (stack_top & 15) || !k->callbacks.executable(k->opaque, entry)) {
        regs[0] = BOTW_KERNEL_RESULT_INVALID_ADDRESS; return BOTW_KERNEL_HANDLED;
    }
    Thread *t = calloc(1, sizeof *t);
    if (!t) return diagnostic(error, capacity, "allocate guest thread: %s", strerror(errno));
    pthread_mutex_lock(&k->lock); Mapping *mapping = NULL;
    for (unsigned i = 0; i < MAX_MAPS; i++) {
        Mapping *m = &k->mappings[i];
        if (m->size && stack_top > m->destination && stack_top <= m->destination + m->size &&
            stack_top - m->destination >= (size_t)PTHREAD_STACK_MIN && !m->users) { mapping = m; break; }
    }
    if (!mapping || k->stopping) {
        pthread_mutex_unlock(&k->lock); free(t);
        regs[0] = k->stopping ? BOTW_KERNEL_RESULT_CANCELLED : BOTW_KERNEL_RESULT_INVALID_MEMORY_STATE;
        return BOTW_KERNEL_HANDLED;
    }
    t->object.type = THREAD; t->object.refs = 1; t->kernel = k;
    t->entry = entry; t->argument = argument; t->stack_top = stack_top;
    t->priority = t->effective_priority = priority; t->core = core; t->id = k->next_thread_id++;
    Handle *h = allocate_handle(k, &t->object, THREAD);
    if (!h) { pthread_mutex_unlock(&k->lock); free(t); regs[0] = 0xd201; return BOTW_KERNEL_HANDLED; }
    t->handle = h->value; t->mapping = mapping; mapping->users++;
    t->next = k->threads; k->threads = t;
    regs[0] = 0; regs[1] = t->handle;
    pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
}
static int start_thread(botw_kernel *k, uint64_t *regs, char *error, size_t capacity) {
    pthread_mutex_lock(&k->lock); Handle *h = find_handle(k, (uint32_t)regs[0]);
    if (!h || h->kind != THREAD) { pthread_mutex_unlock(&k->lock); regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE; return BOTW_KERNEL_HANDLED; }
    Thread *t = (Thread *)h->object;
    if (t->started || t->finished || k->stopping) {
        regs[0] = k->stopping ? BOTW_KERNEL_RESULT_CANCELLED : BOTW_KERNEL_RESULT_INVALID_STATE;
        pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
    }
    pthread_attr_t attr; int result = pthread_attr_init(&attr);
    if (result) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "init thread attributes: %s", strerror(result)); }
    result = pthread_attr_setstack(&attr, (void *)(uintptr_t)t->mapping->destination,
        (size_t)(t->stack_top - t->mapping->destination));
    if (!result) result = pthread_attr_setguardsize(&attr, 0);
#ifdef __APPLE__
    /* Horizon priorities remain virtual scheduler metadata. Request the closest
     * host scheduling class rather than claiming physical-core affinity. */
    if (!result) result = pthread_attr_set_qos_class_np(&attr,
        t->priority < 16 ? QOS_CLASS_USER_INTERACTIVE : t->priority < 32 ? QOS_CLASS_USER_INITIATED :
        t->priority < 48 ? QOS_CLASS_DEFAULT : QOS_CLASS_UTILITY, 0);
#endif
    if (!result) result = pthread_create(&t->host, &attr, thread_main, t);
    pthread_attr_destroy(&attr);
    if (result) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "start guest pthread on mapped stack: %s", strerror(result)); }
    t->started = 1; pthread_mutex_unlock(&k->lock); regs[0] = 0; return BOTW_KERNEL_HANDLED;
}
int botw_kernel_dispatch(botw_kernel *k, unsigned number, uint64_t *regs, char *error, size_t capacity) {
    if (!k || !regs) return diagnostic(error, capacity, "kernel dispatch requires context and registers");
    if (number != 0xa) {
        pthread_mutex_lock(&k->lock); int failed = k->thread_failure[0] != 0;
        if (failed) diagnostic(error, capacity, "%s", k->thread_failure);
        pthread_mutex_unlock(&k->lock); if (failed) return BOTW_KERNEL_ERROR;
    }
    switch (number) {
    case 1: return set_heap_size(k, regs, error, capacity);
    case 4: return map_memory(k, 0, regs, error, capacity);
    case 5: return map_memory(k, 1, regs, error, capacity);
    case 0x29: return get_info(k, regs, error, capacity);
    case 0x13: return map_shared_memory(k, 0, regs, error, capacity);
    case 0x14: return map_shared_memory(k, 1, regs, error, capacity);
    case 8: return create_thread(k, regs, error, capacity);
    case 9: return start_thread(k, regs, error, capacity);
    case 0xc: {
        pthread_mutex_lock(&k->lock); Thread *t = thread_from_handle_locked(k, (uint32_t)regs[1], 1);
        if (t) { regs[0] = 0; regs[1] = (uint64_t)t->effective_priority; }
        else regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE;
        pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
    }
    case 0x19: {
        pthread_mutex_lock(&k->lock); Thread *t = thread_from_handle_locked(k, (uint32_t)regs[0], 1);
        if (t) { t->cancel_pending = 1; regs[0] = 0; pthread_cond_broadcast(&k->changed); }
        else regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE;
        pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
    }
    case 0x1a: return arbitrate_lock(k, regs, error, capacity);
    case 0x1b: return arbitrate_unlock(k, regs, error, capacity);
    case 0x1c: return wait_process_key(k, regs, error, capacity);
    case 0x1d: return signal_process_key(k, regs, error, capacity);
    case 0xa: {
        Thread *t = pthread_getspecific(k->current_thread);
        if (!t) return diagnostic(error, capacity, "SVC ExitThread on the unregistered initial host thread");
        pthread_mutex_lock(&k->lock); t->exit_requested = 1; pthread_mutex_unlock(&k->lock);
        pthread_exit(NULL);
    }
    case 0x11:
        regs[0] = botw_kernel_signal_event(k, (uint32_t)regs[0], error, capacity) ? BOTW_KERNEL_RESULT_INVALID_HANDLE : 0;
        return BOTW_KERNEL_HANDLED;
    case 0x12:
        regs[0] = botw_kernel_clear_event(k, (uint32_t)regs[0], error, capacity) ? BOTW_KERNEL_RESULT_INVALID_HANDLE : 0;
        return BOTW_KERNEL_HANDLED;
    case 0x16:
        /* Let the IPC owner handle its independent namespace. */
        if ((uint32_t)regs[0] < HANDLE_BASE || (uint32_t)regs[0] >= UINT32_C(0x80000000)) return BOTW_KERNEL_UNSUPPORTED;
        regs[0] = botw_kernel_close_handle(k, (uint32_t)regs[0], error, capacity) ? BOTW_KERNEL_RESULT_INVALID_HANDLE : 0;
        return BOTW_KERNEL_HANDLED;
    case 0x17: {
        pthread_mutex_lock(&k->lock); Handle *h = find_handle(k, (uint32_t)regs[0]);
        if (!h || h->kind != EVENT_READ) regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE;
        else if (!h->object->signaled) regs[0] = BOTW_KERNEL_RESULT_INVALID_STATE;
        else { h->object->signaled = 0; regs[0] = 0; }
        pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
    }
    case 0x18: return wait_synchronization(k, regs, error, capacity);
    case 0x0b: {
        int64_t timeout = (int64_t)regs[0];
        if (timeout <= 0) { sched_yield(); regs[0] = 0; return BOTW_KERNEL_HANDLED; }
        uint64_t deadline = timeout_deadline(timeout); int rc = 0;
        pthread_mutex_lock(&k->lock); k->active_waits++;
        while (!k->stopping && rc != ETIMEDOUT) { rc = condition_wait(k, deadline, 0); if (rc && rc != ETIMEDOUT) break; }
        k->active_waits--; int stopped = k->stopping; pthread_mutex_unlock(&k->lock);
        if (rc && rc != ETIMEDOUT) return diagnostic(error, capacity, "host sleep failed: %s", strerror(rc));
        regs[0] = stopped ? BOTW_KERNEL_RESULT_CANCELLED : 0; return BOTW_KERNEL_HANDLED;
    }
    case 0x1e: {
        uint64_t ns = monotonic_ns(); regs[0] = ns / 625 * 12 + ns % 625 * 12 / 625; return BOTW_KERNEL_HANDLED;
    }
    case 0x25: {
        uint32_t requested = (uint32_t)regs[1];
        if (requested == BOTW_KERNEL_INITIAL_THREAD) {
            regs[0] = 0; regs[1] = 1; return BOTW_KERNEL_HANDLED;
        }
        Thread *current = pthread_getspecific(k->current_thread);
        if (requested == BOTW_KERNEL_CURRENT_THREAD && (current || pthread_equal(k->owner, pthread_self()))) {
            regs[0] = 0; regs[1] = current ? current->id : 1; return BOTW_KERNEL_HANDLED;
        }
        pthread_mutex_lock(&k->lock); Handle *h = find_handle(k, requested);
        if (h && h->kind == THREAD) { regs[0] = 0; regs[1] = ((Thread *)h->object)->id; }
        else regs[0] = BOTW_KERNEL_RESULT_INVALID_HANDLE;
        pthread_mutex_unlock(&k->lock); return BOTW_KERNEL_HANDLED;
    }
    case 0x45: {
        uint32_t write, read;
        if (botw_kernel_create_event(k, 0, &write, &read, error, capacity)) return BOTW_KERNEL_ERROR;
        regs[0] = 0; regs[1] = write; regs[2] = read; return BOTW_KERNEL_HANDLED;
    }
    default: return BOTW_KERNEL_UNSUPPORTED;
    }
}
int botw_kernel_memory(botw_kernel *k, uint64_t address, size_t length, int writable) {
    if (!k || !address || address > UINT64_MAX - length) return 0;
    pthread_mutex_lock(&k->lock);
    int valid = k->reservation && region_contains(k->heap_base, k->heap_size, address, length);
    for (unsigned i = 0; i < MAX_MAPS; i++) {
        Mapping *m = &k->mappings[i]; if (!m->size) continue;
        if (valid && overlaps(address, length, m->source, m->size)) { valid = 0; break; }
        if (region_contains(m->destination, m->size, address, length)) { valid = 1; break; }
    }
    for (unsigned i = 0; i < MAX_SHARED_MAPS; i++) {
        SharedMapping *m = &k->shared_mappings[i];
        if (m->memory && region_contains(m->address, m->memory->size, address, length)) {
            valid = !writable || m->permission == 3; break;
        }
    }
    pthread_mutex_unlock(&k->lock); return valid;
}
int botw_kernel_query_memory(botw_kernel *k, uint64_t address, botw_kernel_memory_info *info) {
    if (!k || !info) return -1;
    pthread_mutex_lock(&k->lock);
    if (!k->reservation || !region_contains((uintptr_t)k->reservation, k->reservation_size, address, 1)) {
        pthread_mutex_unlock(&k->lock); return 0;
    }
    uint64_t lower = (uintptr_t)k->reservation, upper = lower + k->reservation_size;
    memset(info, 0, sizeof *info);
    if (region_contains(k->heap_base, k->heap_size, address, 1)) {
        lower = k->heap_base; upper = lower + k->heap_size; info->type = 5; info->permission = 3;
    } else if (address < k->heap_base) upper = k->heap_base;
    else lower = k->heap_base + k->heap_size;
    for (unsigned i = 0; i < MAX_MAPS; i++) {
        Mapping *m = &k->mappings[i]; if (!m->size) continue;
        uint64_t ranges[2] = {m->source, m->destination};
        for (unsigned j = 0; j < 2; j++) {
            uint64_t base = ranges[j], end = base + m->size;
            if (address >= base && address < end) {
                info->base = base; info->size = m->size; info->type = j ? 0xb : 5;
                info->attribute = j ? 0 : 1; info->permission = j ? 3 : 0;
                pthread_mutex_unlock(&k->lock); return 1;
            }
            if (end <= address && end > lower) lower = end;
            if (base > address && base < upper) upper = base;
        }
    }
    for (unsigned i = 0; i < MAX_SHARED_MAPS; i++) {
        SharedMapping *m = &k->shared_mappings[i]; if (!m->memory) continue;
        uint64_t end = m->address + m->memory->size;
        if (address >= m->address && address < end) {
            info->base = m->address; info->size = m->memory->size; info->type = 6; info->permission = m->permission;
            pthread_mutex_unlock(&k->lock); return 1;
        }
        if (end <= address && end > lower) lower = end;
        if (m->address > address && m->address < upper) upper = m->address;
    }
    info->base = lower; info->size = upper - lower;
    pthread_mutex_unlock(&k->lock); return 1;
}
int botw_kernel_reservation(botw_kernel *k, uint64_t *base, uint64_t *size) {
    if (!k || !base || !size) return 0;
    pthread_mutex_lock(&k->lock); int exists = k->reservation != NULL;
    if (exists) { *base = (uintptr_t)k->reservation; *size = k->reservation_size; }
    pthread_mutex_unlock(&k->lock); return exists;
}
int botw_kernel_configure_memory(botw_kernel *k, size_t heap, size_t stack, size_t alias, size_t budget, size_t image, char *error, size_t capacity) {
    if (!k || !heap || !stack || !alias || ((heap | stack | alias) & (HEAP_UNIT - 1)) ||
        budget < image || heap > ADDRESS_LIMIT - HEAP_UNIT || stack > ADDRESS_LIMIT - HEAP_UNIT - heap ||
        alias >= ADDRESS_LIMIT - HEAP_UNIT - heap - stack)
        return diagnostic(error, capacity, "invalid guest memory capacities/budget");
    pthread_mutex_lock(&k->lock);
    if (k->reservation || k->stopping) { pthread_mutex_unlock(&k->lock); return diagnostic(error, capacity, "cannot reconfigure active/stopping guest memory"); }
    k->heap_capacity = heap; k->stack_capacity = stack; k->alias_capacity = alias;
    k->physical_budget = budget; k->image_bytes = image; k->memory_configured = 1;
    pthread_mutex_unlock(&k->lock); return 0;
}
