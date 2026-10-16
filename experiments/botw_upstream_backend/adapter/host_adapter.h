/* SPDX-License-Identifier: MIT
 * Complete-backend host services. This does not select or start a renderer. */
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef int (*botw_backend_fault)(void *, int, void *);
typedef int (*botw_backend_prepare)(void *, size_t, char *, size_t);
typedef struct {
    const char *name;
    const void *base;
    size_t size;
} botw_backend_module;
typedef struct {
    uint32_t version;
    void *layer; /* Retained by the frontend for the complete backend lifetime. */
    int (*prepare_write)(void *, size_t, char *, size_t);
    int (*register_tracking)(botw_backend_fault, botw_backend_prepare);
    /* Validates the complete runtime schema before the first backend call. */
    int (*validate_manifest)(const char *, char *, size_t);
    void (*error)(const char *); /* May escape; returning leads to abort. */
    const char *manifest;
    const botw_backend_module *modules;
    size_t module_count;
} botw_backend_host_services;

/* Exactly once, before backend entry. Failure does not publish services. */
int botw_backend_host_install(const botw_backend_host_services *, char *, size_t);
int botw_backend_host_register_checked_tracking(botw_backend_fault,
                                               botw_backend_prepare);
void botw_backend_host_resource_report(uint32_t, const char *);
/* The callback is stored but never automatically executed by this adapter. */
int botw_backend_host_has_wait_report(void);
const char *botw_backend_compat_getenv(const char *);
int botw_backend_compat_diagnostic_enabled(void);
int botw_backend_compat_backtrace(void **, int);
void botw_backend_compat_backtrace_symbols_fd(void *const *, int, int);
_Noreturn void botw_backend_host_terminate(const char *, int);
_Noreturn void botw_backend_host_diagnostic_denied(const char *);

/* Original upstream host entry points, implemented by this adapter. */
uint64_t now_ns(void);
void *front_metal_layer(void);
void dm_guest_write_prepare(void *, size_t);
void dm_fault_hook_register(botw_backend_fault);
void dm_guest_write_prep_register(void (*)(void *, size_t));
void dm_res_report_register(void (*)(uint32_t, const char *));
void crash_set_wait_dump(void (*)(void));
extern uint64_t g_t0_ns;
