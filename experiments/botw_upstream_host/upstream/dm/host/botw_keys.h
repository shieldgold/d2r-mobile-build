// DM_KEYS scripted controller input for unattended runs (botw_keys.c).
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct { double at, hold; uint64_t buttons; int32_t lx, ly, rx, ry; } botw_key_step;

// Parses "seconds:key[+key][~hold seconds][*count/interval seconds],..." into at most cap steps (repeats expanded).
// Returns the step count, or -1 with err filled for an invalid specification.
int botw_keys_parse(const char *spec, botw_key_step *out, int cap, char *err, size_t err_size);
// Starts the script thread once if DM_KEYS is set; an invalid script stops the run. Times count from host start.
void botw_keys_start(void);
