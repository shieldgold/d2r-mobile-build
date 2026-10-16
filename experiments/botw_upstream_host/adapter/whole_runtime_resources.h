/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>
#include <limits.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    char metadata[PATH_MAX];
    char guest[PATH_MAX];
} whole_runtime_resource_paths;
/* Read-only verification of the fixed base-v0/v4 package. Does not dlopen,
 * initialize force state, execute code, create an identity or open SaveFS.
 * The caller keeps its signed, read-only bundle unchanged through host preload.
 * Outputs are published only on complete success. This is not render readiness. */
int whole_runtime_resources_verify(const char *bundle_directory,
    whole_runtime_resource_paths *out, char *error, size_t error_capacity);
/* Uses the existing bounded RomFS reader; verifies the exact base image length
 * and /System/Version.txt == "1.0.0". No extraction, writes or save mount. */
int whole_runtime_romfs_verify(const char *documents_directory,
    char *image_path, size_t path_capacity, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
