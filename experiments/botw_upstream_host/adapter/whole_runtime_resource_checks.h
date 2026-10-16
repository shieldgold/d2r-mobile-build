/* SPDX-License-Identifier: MIT
 * Internal, shared by the fixed package verifier and owned CPU fixtures. */
#pragma once
#include "base_v0_force.h"
typedef struct {
    const char *name;
    size_t bytes;
    const char *sha256;
} whole_runtime_file_identity;
/* directory is already opened and retained by the caller. Single components
 * only; no symlink or non-regular file is accepted, including empty gate files. */
int whole_runtime_verify_files(int directory,
    const whole_runtime_file_identity *, size_t count, char *, size_t);
/* fd is a regular immutable local Mach-O file, never a loaded image. Validates
 * ARM64/iOS dylib, bounded load commands, exact signed section layouts and their
 * canonical x0/w/x1 payload hash against the supplied immutable authority. */
int whole_runtime_verify_guest(int fd, const base_v0_force_module *, size_t count,
    char *, size_t);
