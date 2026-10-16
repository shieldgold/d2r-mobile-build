/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Select and verify the signed bundle's explicit established-source or
 * conditional-source publication. This never infers live game facts, compiles
 * Metal, or permits an artifact before its real draw predicate succeeds. */
int whole_runtime_shader_bundle_publication(const char *bundle_directory,
    char *manifest_path,size_t manifest_capacity,char *error,size_t error_capacity);
#ifdef __cplusplus
}
#endif
