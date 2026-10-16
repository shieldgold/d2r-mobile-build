/* SPDX-License-Identifier: MIT
 * Force-include only in the complete host/gfx C and Objective-C closure.
 * Real host module, clock, dirty-page and write-preparation ownership stays
 * upstream. This policy owns no module registry and supplies no host stubs. */
#pragma once
#include <stdlib.h>
#include <unistd.h>
#include <execinfo.h>

#define BOTW_WHOLE_HOST_POLICY 1
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
