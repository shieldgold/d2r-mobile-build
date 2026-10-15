/* Host input -> Horizon 1.x/2.x HID Npad shared memory. No game assets. */
#ifndef BOTW_INPUT_H
#define BOTW_INPUT_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define BOTW_HID_SHARED_MEMORY_SIZE 0x40000u
#define BOTW_HID_NPAD_OFFSET 0x9a00u
#define BOTW_HID_NPAD_STRIDE 0x5000u
#define BOTW_NPAD_STYLE_FULLKEY 1u
#define BOTW_NPAD_STYLE_HANDHELD 2u
#define BOTW_NPAD_STYLE_JOYDUAL 4u
#define BOTW_NPAD_ATTRIBUTE_CONNECTED 1u
/* Nintendo face-button positions: A=east, B=south, X=north, Y=west. */
enum {
    BOTW_BUTTON_A=1u<<0, BOTW_BUTTON_B=1u<<1, BOTW_BUTTON_X=1u<<2,
    BOTW_BUTTON_Y=1u<<3, BOTW_BUTTON_LSTICK=1u<<4, BOTW_BUTTON_RSTICK=1u<<5,
    BOTW_BUTTON_L=1u<<6, BOTW_BUTTON_R=1u<<7, BOTW_BUTTON_ZL=1u<<8,
    BOTW_BUTTON_ZR=1u<<9, BOTW_BUTTON_PLUS=1u<<10, BOTW_BUTTON_MINUS=1u<<11,
    BOTW_BUTTON_LEFT=1u<<12, BOTW_BUTTON_UP=1u<<13, BOTW_BUTTON_RIGHT=1u<<14,
    BOTW_BUTTON_DOWN=1u<<15, BOTW_BUTTON_STICK_L_LEFT=1u<<16,
    BOTW_BUTTON_STICK_L_UP=1u<<17, BOTW_BUTTON_STICK_L_RIGHT=1u<<18,
    BOTW_BUTTON_STICK_L_DOWN=1u<<19, BOTW_BUTTON_STICK_R_LEFT=1u<<20,
    BOTW_BUTTON_STICK_R_UP=1u<<21, BOTW_BUTTON_STICK_R_RIGHT=1u<<22,
    BOTW_BUTTON_STICK_R_DOWN=1u<<23
};
enum botw_input_source { BOTW_INPUT_TOUCH, BOTW_INPUT_GAMECONTROLLER, BOTW_INPUT_SOURCE_COUNT };
typedef struct botw_input botw_input;
/* Starts disconnected. Attach the actual frontend before setting a source connected.
 * The caller must stop frontend callbacks before destroy. All other calls serialize. */
botw_input *botw_input_create(void);
void botw_input_destroy(botw_input *input);
/* The bridge initially owns a zeroed buffer. A bound kernel shared-memory backing
 * remains owned by the kernel and must outlive the input. Bind before guest use. */
void *botw_input_shared_memory(botw_input *input, size_t *size);
int botw_input_bind_shared_memory(botw_input *input, void *backing, size_t size);
/* Source changes replace a coherent state. No synthetic/latching button presses.
 * Axes clamp to [-32768,32767], positive y means up. Disconnection clears state. */
int botw_input_set_source(botw_input *input, enum botw_input_source source,
    uint64_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry, int connected);
/* Inactivity immediately releases all sources; reconnecting never revives held keys. */
void botw_input_set_active(botw_input *input, int active);
/* Style bits 0..4 and Npad IDs 0..7 / 0x20 / 0x10 are the Horizon API values.
 * One real host control set is assigned to the first supported compatible Npad.
 * JoyLeft/Right are accepted as supported styles but never falsely connected. */
int botw_input_configure(botw_input *input, uint32_t supported_styles,
    const uint32_t *ids, size_t count);
uint32_t botw_input_style(botw_input *input, uint32_t npad_id);
int botw_input_disconnect(botw_input *input, uint32_t npad_id);
/* Call at the host display/input cadence with a real monotonic 19.2 MHz tick.
 * Samples advance even with all buttons released; producer serializes ring writes. */
void botw_input_sample(botw_input *input, uint64_t timestamp_ticks);
uint64_t botw_input_monotonic_ticks(void);
#ifdef __cplusplus
}
#endif
#endif
