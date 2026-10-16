/* SPDX-License-Identifier: MIT
 * Force-include in the adapted complete C/ObjC closure, never assembler.
 * Functional guest accesses still require the checked resource/ABI adapter. */
#pragma once
#include <stdlib.h>
#include <unistd.h>
#include <execinfo.h>
#include "host_adapter.h"
#define getenv(name) botw_backend_compat_getenv(name)
#define backtrace(buffer, size) botw_backend_compat_backtrace((buffer), (size))
#define backtrace_symbols_fd(buffer, size, fd) botw_backend_compat_backtrace_symbols_fd((buffer), (size), (fd))
#define abort() botw_backend_host_terminate(__FILE__, __LINE__)
#define exit(value) botw_backend_host_terminate(__FILE__, (value))
#define _exit(value) botw_backend_host_terminate(__FILE__, (value))
