/* SPDX-License-Identifier: MIT
 * Force-include only in the complete host/gfx C and Objective-C closure.
 * Real host module, clock, dirty-page and write-preparation ownership stays
 * upstream. This policy owns no module registry and supplies no host stubs. */
#pragma once
#include <stdlib.h>
#include <unistd.h>
#include <execinfo.h>

#define BOTW_WHOLE_HOST_POLICY 1
/* Only normal, identified host APIs with a Horizon/NVN Result or NVNboolean belong here.
 * Callers pass no guest frame, address, raw Result or diagnostic context. */
typedef enum {
    BOTW_HOST_API_SET_HEAP_SIZE,
    BOTW_HOST_API_SET_MEMORY_PERMISSION,
    BOTW_HOST_API_SET_MEMORY_ATTRIBUTE,
    BOTW_HOST_API_MAP_MEMORY,
    BOTW_HOST_API_UNMAP_MEMORY,
    BOTW_HOST_API_QUERY_MEMORY,
    BOTW_HOST_API_CREATE_THREAD,
    BOTW_HOST_API_START_THREAD,
    BOTW_HOST_API_CREATE_TRANSFER_MEMORY,
    BOTW_HOST_API_CLOSE_HANDLE,
    BOTW_HOST_API_WAIT_SYNCHRONIZATION,
    BOTW_HOST_API_GET_INFO,
    BOTW_HOST_API_CONNECT_TO_NAMED_PORT,
    BOTW_HOST_API_SEND_SYNC_REQUEST,
    BOTW_HOST_API_NVN_MEMORY_POOL_INITIALIZE,
    BOTW_HOST_API_NVN_QUEUE_ACQUIRE_TEXTURE,
    BOTW_HOST_API_NVN_COMMAND_BUFFER_INITIALIZE,
    BOTW_HOST_API_COUNT
} botw_whole_host_api;
typedef enum {
    BOTW_HOST_API_OK,
    BOTW_HOST_API_ERROR
} botw_whole_host_api_outcome;
void botw_whole_host_api_result(botw_whole_host_api api, botw_whole_host_api_outcome outcome);
void botw_whole_host_heap_capacities(size_t heap, size_t root);
char *botw_whole_host_getenv(const char *name);
int botw_backend_compat_diagnostic_enabled(void);
int botw_whole_host_backtrace(void **buffer, int size);
void botw_whole_host_backtrace_symbols_fd(void *const *buffer, int size, int fd);
_Noreturn void botw_backend_host_diagnostic_denied(const char *operation);
_Noreturn void botw_whole_host_failure(void);
_Noreturn void botw_whole_host_signal_failure(int signal_number);
_Noreturn void botw_whole_host_exit(int status);

#ifndef BOTW_WHOLE_POLICY_IMPLEMENTATION
#define getenv(name) botw_whole_host_getenv(name)
#define backtrace(buffer, size) botw_whole_host_backtrace((buffer), (size))
#define backtrace_symbols_fd(buffer, size, fd) botw_whole_host_backtrace_symbols_fd((buffer), (size), (fd))
#define abort() botw_whole_host_failure()
#define exit(status) botw_whole_host_exit(status)
#define _exit(status) botw_whole_host_exit(status)
#endif
