#pragma once

#include <stddef.h>
#include <stdint.h>

#define BOTW_SHADER_OBSERVATION_MAX_STAGES 6
#define BOTW_SHADER_OBSERVATION_MAX_ATTACHMENTS 9
#define BOTW_SHADER_OBSERVATION_MAX_BINDINGS (6 * (128 + 32))
#define BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS 16

typedef struct {
    char key[17];
    char sha256[65];
    uint32_t stage;
} botw_shader_observation_stage;

typedef struct {
    uint32_t slot;
    int32_t storage_target, storage_format;
    uint32_t width, height, depth, levels, samples;
    uint8_t has_view, view_set_target, view_set_format, view_set_levels, view_set_layers;
    int32_t view_target, view_format, view_base_level, view_level_count;
    int32_t view_min_layer, view_layer_count;
    int32_t effective_target, effective_format;
} botw_shader_observation_attachment;

typedef struct {
    uint32_t kind, stage, slot, texture_id, sampler_id;
    uint8_t registered;
    int32_t storage_target, storage_format;
    uint8_t has_view, view_set_target, view_set_format, view_set_levels, view_set_layers;
    int32_t view_target, view_format, view_base_level, view_level_count;
    int32_t view_min_layer, view_layer_count;
    int32_t effective_target, effective_format;
} botw_shader_observation_binding;

typedef struct {
    uint32_t slot, format, offset, stream;
} botw_shader_observation_attribute;

typedef struct {
    uint32_t slot, stride, divisor;
} botw_shader_observation_stream;

typedef struct {
    uint32_t slot;
    uint8_t bound;
    uint64_t size;
} botw_shader_observation_vertex_buffer;

typedef struct {
    uint32_t indexed, primitive, first, count, index_type, instances;
    int32_t base_vertex, base_instance;
    uint32_t origin_mode, depth_mode;
    uint32_t stage_count;
    botw_shader_observation_stage stages[BOTW_SHADER_OBSERVATION_MAX_STAGES];
    uint32_t color_count;
    botw_shader_observation_attachment colors[8];
    uint8_t has_depth;
    botw_shader_observation_attachment depth;
    uint32_t binding_count;
    botw_shader_observation_binding bindings[BOTW_SHADER_OBSERVATION_MAX_BINDINGS];
    uint32_t attribute_count;
    botw_shader_observation_attribute attributes[BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS];
    uint32_t stream_count;
    botw_shader_observation_stream streams[BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS];
    uint32_t vertex_buffer_count;
    botw_shader_observation_vertex_buffer vertex_buffers[BOTW_SHADER_OBSERVATION_MAX_VERTEX_SLOTS];
    uint8_t has_viewport, has_scissor;
    float viewport[4];
    int32_t scissor[4];
} botw_shader_observation;

enum {
    BOTW_SHADER_OBSERVATION_TEXTURE = 0,
    BOTW_SHADER_OBSERVATION_IMAGE = 1,
};

/* Returns 0 when observation is disabled, 1 when configured, and -1 for an
 * unsafe or unsupported configuration. The returned path and selector alias
 * getenv. The optional DM_SHADER_OBSERVATION_SHA256 selector enables targeted
 * observation: when NULL the existing first-draw mode is selected unchanged,
 * otherwise it is exactly 64 lowercase hex characters matched against the
 * full sha256 of any immutable program stage. */
int botw_shader_observation_environment(const char **path, const char **selector,
                                        char *error, size_t error_size);

/* Writes one JSON document atomically. Existing destinations are conflicts and
 * are never replaced. The destination must be absolute and outside a repository. */
int botw_shader_observation_write(const char *path, const botw_shader_observation *observation,
                                  char *error, size_t error_size);
