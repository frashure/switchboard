/* LVGL-in-browser touchscreen UI prototype (docs/design.md Section 6).
 * Not the real firmware -- a WASM build of the actual widget tree
 * (ui.c) that will run on the ESP32, so the persona-select / tap-to-talk
 * / cancel screen flow can be reviewed and iterated on before hardware
 * exists. Runs against the real Gateway WS protocol via bridge.js. */

#include "lvgl/lvgl.h"
#include "lvgl/src/drivers/sdl/lv_sdl_window.h"
#include "lvgl/src/drivers/sdl/lv_sdl_mouse.h"

#include <emscripten.h>
#include <emscripten/emscripten.h>
#include <SDL2/SDL.h>

#include "ui.h"

/* Placeholder resolution -- no touch controller has been chosen yet
 * (docs/design.md Section 9 open items). 480x320 is a common small
 * touchscreen size; adjust once hardware is picked. */
#define SIM_HOR_RES 480
#define SIM_VER_RES 320

static void main_loop(void) {
    lv_timer_handler();
}

int main(void) {
    lv_init();
    lv_tick_set_cb(SDL_GetTicks);

    lv_display_t *disp = lv_sdl_window_create(SIM_HOR_RES, SIM_VER_RES);
    lv_sdl_window_set_title(disp, "Switchboard -- LVGL Simulator");
    lv_sdl_mouse_create();

    ui_init();

    /* Module.onRuntimeInitialized fires *before* main() runs (confirmed:
     * Emscripten's generated run() calls it, then callMain()) -- bridge.js
     * must not start calling sim_* functions until our screens actually
     * exist, so tell it explicitly once ui_init() is done instead. */
    EM_ASM({ if (window.simReady) window.simReady(); });

    emscripten_set_main_loop(main_loop, 0, true);
    return 0;
}
