/* SPDX-License-Identifier: MIT */
#define DM_TITLE_STRONG 1
#include "../upstream/dm/host/dm.h"
#include "host_adapter.h"
#include <stdatomic.h>
#include <limits.h>
#include <time.h>
#include <unistd.h>

enum { HOST_VERSION = 1, MAX_HOST_MODULES = 8, MANIFEST_CAP = 4096 };
static pthread_mutex_t install_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic int installed;
static botw_backend_host_services services;
static module_t modules[MAX_HOST_MODULES];
static char manifest[MANIFEST_CAP];
static _Atomic(void (*)(uint32_t, const char *)) report_callback;
static _Atomic(void (*)(void)) wait_callback;
uint64_t g_t0_ns;
int g_dm_title = DM_TITLE_BOTW;

static int fail(char *error, size_t capacity, const char *why) {
    if (error && capacity) snprintf(error, capacity, "%s", why);
    return 0;
}
static _Noreturn void host_failure(const char *why) {
    fprintf(stderr, "[botw whole-backend host] %s\n", why);
    if (atomic_load_explicit(&installed, memory_order_acquire)) services.error(why);
    /* A returning error callback must never turn a failed void operation into
     * successful rendering. No backtrace or guest diagnostic is collected. */
    abort();
}
uint64_t now_ns(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) || t.tv_sec < 0 || t.tv_nsec < 0 ||
        t.tv_nsec >= 1000000000 || (uint64_t)t.tv_sec > UINT64_MAX / 1000000000)
        host_failure("host monotonic clock unavailable");
    uint64_t sec = (uint64_t)t.tv_sec * 1000000000;
    if ((uint64_t)t.tv_nsec > UINT64_MAX - sec)
        host_failure("host monotonic clock overflow");
    return sec + (uint64_t)t.tv_nsec;
}
int botw_backend_host_install(const botw_backend_host_services *input,
                              char *error, size_t capacity) {
    if (error && capacity) error[0] = 0;
    if (!input || input->version != HOST_VERSION || !input->prepare_write ||
        !input->register_tracking || !input->validate_manifest || !input->error ||
        !input->manifest || input->manifest[0] != '/' ||
        strnlen(input->manifest, MANIFEST_CAP) == MANIFEST_CAP ||
        input->module_count > MAX_HOST_MODULES ||
        (input->module_count && !input->modules))
        return fail(error, capacity, "incomplete whole-backend host configuration");
    pthread_mutex_lock(&install_lock);
    if (atomic_load_explicit(&installed, memory_order_acquire)) {
        pthread_mutex_unlock(&install_lock);
        return fail(error, capacity, "whole-backend host configuration already published");
    }
    module_t next[MAX_HOST_MODULES] = {0};
    for (size_t i = 0; i < input->module_count; i++) {
        const botw_backend_module *m = &input->modules[i];
        if (!m->name || !m->name[0] || strnlen(m->name, sizeof next[i].name) == sizeof next[i].name ||
            !m->base || !m->size || m->size > UINTPTR_MAX - (uintptr_t)m->base) {
            pthread_mutex_unlock(&install_lock);
            return fail(error, capacity, "invalid host-owned module metadata");
        }
        for (size_t k = 0; k < i; k++)
            if ((uintptr_t)m->base < (uintptr_t)next[k].base + next[k].size &&
                (uintptr_t)next[k].base < (uintptr_t)m->base + m->size) {
                pthread_mutex_unlock(&install_lock);
                return fail(error, capacity, "overlapping host-owned module metadata");
            }
        memcpy(next[i].name, m->name, strlen(m->name) + 1);
        next[i].base = (uint8_t *)m->base;
        next[i].size = m->size;
    }
    /* The validator must use the complete upstream manifest parser. Tests use
     * an explicit owned callback, never a fabricated valid manifest file. */
    if (!input->validate_manifest(input->manifest, error, capacity)) {
        pthread_mutex_unlock(&install_lock);
        return 0;
    }
    if (setenv("DM_BOTW_SHADER_MANIFEST", input->manifest, 1)) {
        pthread_mutex_unlock(&install_lock);
        return fail(error, capacity, "cannot configure explicit shader manifest");
    }
    services = *input;
    memcpy(modules, next, sizeof modules);
    memcpy(manifest, input->manifest, strlen(input->manifest) + 1);
    services.manifest = manifest;
    services.modules = NULL;
    g_t0_ns = now_ns();
    atomic_store_explicit(&installed, 1, memory_order_release);
    pthread_mutex_unlock(&install_lock);
    return 1;
}
static void require_host(void) {
    if (!atomic_load_explicit(&installed, memory_order_acquire))
        host_failure("whole-backend host services were not configured before entry");
}
void *front_metal_layer(void) { require_host(); return services.layer; }
module_t *module_of(const void *p) {
    require_host();
    uintptr_t address = (uintptr_t)p;
    for (size_t i = 0; i < services.module_count; i++)
        if (address >= (uintptr_t)modules[i].base && address - (uintptr_t)modules[i].base < modules[i].size)
            return &modules[i];
    return NULL;
}
void dm_guest_write_prepare(void *address, size_t bytes) {
    require_host();
    char error[256] = {0};
    if (services.prepare_write(address, bytes, error, sizeof error))
        host_failure(error[0] ? error : "host write preparation failed");
}
int botw_backend_host_register_checked_tracking(botw_backend_fault fault,
                                               botw_backend_prepare prepare) {
    require_host();
    if (!fault || !prepare) return -1;
    return services.register_tracking(fault, prepare);
}
/* Raw 5f void pre-write registration cannot communicate mprotect failure.
 * The retained complete dirty module must use its audited checked host port.
 * These legacy signatures fail explicitly rather than publish a false pair. */
void dm_fault_hook_register(botw_backend_fault fault) {
    (void)fault; require_host();
    host_failure("legacy dirty-hook registration requires the checked whole-module host port");
}
void dm_guest_write_prep_register(void (*prepare)(void *, size_t)) {
    (void)prepare; require_host();
    host_failure("legacy void write preparation cannot represent checked host failure");
}
void dm_res_report_register(void (*fn)(uint32_t, const char *)) {
    require_host(); atomic_store_explicit(&report_callback, fn, memory_order_release);
}
void crash_set_wait_dump(void (*fn)(void)) {
    require_host(); atomic_store_explicit(&wait_callback, fn, memory_order_release);
}
void botw_backend_host_resource_report(uint32_t tick, const char *why) {
    require_host();
    void (*fn)(uint32_t, const char *) = atomic_load_explicit(&report_callback, memory_order_acquire);
    if (fn) fn(tick, why);
}
int botw_backend_host_has_wait_report(void) {
    require_host(); return atomic_load_explicit(&wait_callback, memory_order_acquire) != NULL;
}
const char *botw_backend_compat_getenv(const char *name) {
    if (!name) return NULL;
    /* No process environment switch may re-enable diagnostic capture, D2R
     * tables, shader substitution, skipped draws or synthetic time here. */
    if (!strcmp(name, "DM_STOP_ON_UNIMPL")) return "1";
    /* These exact consumers test atoi(value) or strcmp(value, "0"). NULL
     * enables their original defaults, so it is not a disabling value. */
    static const char *const zero[] = {
        "DM_NVN_ZSKIP", "DM_NVN_UNWRITTEN_DC", "DM_NVN_TBDR", "DM_NVN_XV",
        "DM_NVN_DEFER", "DM_NVN_BLOOM_Q", "DM_RT_AB", "DM_LONGFRAME",
        "DM_NVN_FRAMES", "DM_MEM_CAP_GB", "DM_PRESENT_LINK"
    };
    for (size_t i = 0; i < sizeof zero / sizeof *zero; i++)
        if (!strcmp(name, zero[i])) return "0";
    if (!strcmp(name, "DM_PSO_MANIFEST")) return "off";
    if (!strcmp(name, "DM_BOTW_SHADER_MANIFEST")) {
        require_host(); return manifest;
    }
    return NULL;
}
int botw_backend_compat_diagnostic_enabled(void) { return 0; }
int botw_backend_compat_backtrace(void **buffer, int size) {
    (void)buffer; (void)size; return 0;
}
void botw_backend_compat_backtrace_symbols_fd(void *const *buffer, int size, int fd) {
    (void)buffer; (void)size; (void)fd;
}
_Noreturn void botw_backend_host_terminate(const char *file, int value) {
    char error[256];
    snprintf(error, sizeof error, "upstream stopped at host source %s value=%d; guest diagnostics disabled", file ? file : "unknown", value);
    host_failure(error);
}
_Noreturn void botw_backend_host_diagnostic_denied(const char *operation) {
    char error[256];
    snprintf(error, sizeof error, "complete-backend diagnostic entry disabled: %s",
             operation ? operation : "unnamed");
    host_failure(error);
}
