#pragma once
#include <stddef.h>
#include <stdint.h>
#include "include/nvn.h"

/* Immutable shader snapshots are interned for the process lifetime. Command
 * buffers copy program descriptors, so guest buffer and program lifetimes must
 * not determine the lifetime of the captured shader bytes. */
typedef struct {
    char key[17];
    char sha256[65];
    uint32_t version, stage, control_size, code_size;
    uint32_t code_offset, c1_offset, c1_size;
} botw_shader_info;

int botw_shader_pool_add(const void *owner, const void *cpu, uint64_t gpu,
                         size_t size, char *error, size_t error_size);
void botw_shader_pool_remove(const void *owner);

/* Reads the descriptor and its buffers with fault-safe copies. The output
 * descriptor references owned snapshots, never the caller's storage. Optional
 * DM_SHADER_CAPTURE writes exact snapshots to an explicit absolute directory. */
int botw_shader_snapshot(const NVNshaderData *input, NVNshaderData *output,
                         char *error, size_t error_size);
/* Extended descriptors may reuse the normal snapshot only when their optional
 * metadata is absent or byte-identical to the validated control metadata. */
int botw_shader_snapshot_ext(const NVNshaderDataExt *input, NVNshaderData *output,
                             char *error, size_t error_size);
int botw_shader_get_info(const NVNshaderData *snapshot, botw_shader_info *info);

/* Unsupported rendering stops: exit 3 in bounded mode, abort otherwise. */
void botw_shader_unsupported(const char *reason, const char *key);
