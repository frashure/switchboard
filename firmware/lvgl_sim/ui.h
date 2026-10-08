#ifndef SIM_UI_H
#define SIM_UI_H

#include <stdint.h>

/* Builds the persona-select + tap-to-talk screens (docs/design.md
 * Section 4 device state machine) as real LVGL widgets, so it's
 * representative of what will run on the actual touchscreen firmware.
 * Called once from main() after the SDL display/indev are ready. */
void ui_init(void);

/* ---- Called from bridge.js once personas are known (GET /profiles) ---- */

/* Resets the persona picker screen -- call before re-adding personas. */
void sim_clear_personas(void);

/* Adds one persona card. avatar_bgra may be NULL (no avatar configured in
 * OWUI, or still decoding) -- a monogram fallback is drawn instead. When
 * non-NULL, it must point at w*h*4 bytes in BGRA8888 byte order (LVGL's
 * lv_color32_t layout: blue, green, red, alpha) and must stay valid for
 * the lifetime of the page (bridge.js allocates it with Module._malloc
 * and never frees it -- a few KB per persona, fine for a prototype). */
void sim_add_persona(const char *id, const char *display_name,
                      const uint8_t *avatar_bgra, int32_t w, int32_t h);

/* ---- Called from bridge.js on Gateway WS events ---- */

void sim_set_status(const char *state, const char *detail);
void sim_set_transcript(const char *text);
void sim_set_llm_text(const char *text);

#endif
