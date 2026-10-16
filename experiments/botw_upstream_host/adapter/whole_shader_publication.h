/* SPDX-License-Identifier: MIT */
#pragma once
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Read only the signed bundle's explicit source package. Verify publication,
 * exact admitted batch, detached facts and every original artifact gate. This
 * neither compiles Metal nor claims complete game coverage. Failure leaves the
 * caller's output unchanged; unexpected runtime shader identities still fail.
 * The caller keeps this signed read-only bundle unchanged through preload. */
int whole_runtime_shader_publication(const char *bundle_directory,
    char *manifest_path,size_t manifest_capacity,char *error,size_t error_capacity);
#ifdef __cplusplus
}
#endif
