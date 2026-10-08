/* Persona-select + tap-to-talk screens (docs/design.md Section 4 device
 * state machine), built from real LVGL widgets with a modern dark-theme
 * look: avatar cards, a circular talk button, and an animated push/pop
 * transition between the persona picker and the session screen. Wired
 * to the real Gateway WS protocol via bridge.js (EM_ASM calls out,
 * exported sim_* functions called in). */

#include "ui.h"
#include "lvgl/lvgl.h"

#include <emscripten/emscripten.h>
#include <string.h>
#include <ctype.h>
#include <stdio.h>
#include <stdint.h>

#define MAX_PERSONAS 16
#define PERSONA_ID_LEN 32
#define PERSONA_NAME_LEN 48

typedef struct {
    char id[PERSONA_ID_LEN];
    char display_name[PERSONA_NAME_LEN];
    lv_image_dsc_t img_dsc;
    bool has_avatar;
} persona_t;

static persona_t personas[MAX_PERSONAS];
static int persona_count = 0;
static char selected_persona[PERSONA_ID_LEN] = "";

/* ---- Design tokens ---- */
#define COLOR_BG           lv_color_hex(0x14141a)
#define COLOR_CARD         lv_color_hex(0x1f1f27)
#define COLOR_CARD_PRESS   lv_color_hex(0x2a2a35)
#define COLOR_ACCENT       lv_color_hex(0x6c63ff)
#define COLOR_ACCENT_PRESS lv_color_hex(0x5a52d9)
#define COLOR_RECORDING    lv_color_hex(0xef4444)
#define COLOR_TEXT         lv_color_hex(0xf5f5f7)
#define COLOR_SUBTEXT      lv_color_hex(0x9a9aa5)
#define COLOR_BUBBLE       lv_color_hex(0x24242e)

static lv_obj_t *select_scr;
static lv_obj_t *session_scr;
static lv_obj_t *persona_grid;

static lv_obj_t *header_avatar_holder;
static lv_obj_t *header_name;
static lv_obj_t *talk_btn;
static lv_obj_t *talk_label;
static lv_obj_t *cancel_btn;
static lv_obj_t *status_label;
static lv_obj_t *transcript_bubble;
static lv_obj_t *transcript_label;
static lv_obj_t *llm_bubble;
static lv_obj_t *llm_label;

static bool is_idle_state(const char *state) {
    return strcmp(state, "ready") == 0 || strcmp(state, "done") == 0 ||
           strcmp(state, "listening") == 0;
}

/* Tracks idle-ness explicitly, updated from sim_set_status()'s raw state
 * string. Deliberately not re-derived by reading back status_label's
 * rendered text -- app.js was already burned once this session by
 * conflating "what the UI displays" with "what the actual state is"
 * (the capturing/listening naming-collision bug); same trap here. */
static bool g_session_idle = true;

/* Builds a circular avatar: the real image if available, else a
 * monogram (first letter of the name) on an accent-colored circle --
 * the same fallback pattern Slack/Discord/etc. use for missing avatars. */
static lv_obj_t *create_avatar(lv_obj_t *parent, int32_t size, const persona_t *p) {
    lv_obj_t *c = lv_obj_create(parent);
    lv_obj_remove_style_all(c);
    lv_obj_set_size(c, size, size);
    lv_obj_set_style_radius(c, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_clip_corner(c, true, 0);
    lv_obj_remove_flag(c, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(c, LV_OBJ_FLAG_CLICKABLE);

    if (p->has_avatar) {
        lv_obj_set_style_bg_opa(c, LV_OPA_TRANSP, 0);
        lv_obj_t *img = lv_image_create(c);
        lv_image_set_src(img, &p->img_dsc);
        uint32_t zoom = (uint32_t)((size * 256) / p->img_dsc.header.w);
        lv_image_set_scale(img, zoom);
        lv_obj_center(img);
    } else {
        lv_obj_set_style_bg_color(c, COLOR_ACCENT, 0);
        lv_obj_set_style_bg_opa(c, LV_OPA_COVER, 0);
        lv_obj_t *letter = lv_label_create(c);
        char buf[2] = {(char)toupper((unsigned char)p->display_name[0]), '\0'};
        lv_label_set_text(letter, buf);
        lv_obj_set_style_text_color(letter, COLOR_TEXT, 0);
        lv_obj_set_style_text_font(letter, &lv_font_montserrat_24, 0);
        lv_obj_center(letter);
    }
    return c;
}

static void go_to_select_cb(lv_event_t *e) {
    (void)e;
    if (!g_session_idle) return; /* don't bail mid-turn */
    lv_screen_load_anim(select_scr, LV_SCREEN_LOAD_ANIM_OVER_RIGHT, 280, 0, false);
}

static void persona_card_cb(lv_event_t *e) {
    int idx = (int)(intptr_t)lv_event_get_user_data(e);
    if (idx < 0 || idx >= persona_count) return;
    persona_t *p = &personas[idx];

    strncpy(selected_persona, p->id, sizeof(selected_persona) - 1);
    selected_persona[sizeof(selected_persona) - 1] = '\0';

    lv_obj_clean(header_avatar_holder);
    create_avatar(header_avatar_holder, 40, p);
    lv_label_set_text(header_name, p->display_name);

    EM_ASM({ window.simSelectPersona(UTF8ToString($0)); }, p->id);

    lv_screen_load_anim(session_scr, LV_SCREEN_LOAD_ANIM_OVER_LEFT, 280, 0, false);
}

static void talk_btn_cb(lv_event_t *e) {
    (void)e;
    if (strlen(selected_persona) == 0) return;
    EM_ASM({ window.simStartTalking(); });
}

static void cancel_btn_cb(lv_event_t *e) {
    (void)e;
    EM_ASM({ window.simCancel(); });
}

/* ---- Exported entry points, called from bridge.js ---- */

void sim_clear_personas(void) {
    lv_obj_clean(persona_grid);
    persona_count = 0;
}

void sim_add_persona(const char *id, const char *display_name,
                      const uint8_t *avatar_bgra, int32_t w, int32_t h) {
    if (persona_count >= MAX_PERSONAS) return;
    persona_t *p = &personas[persona_count];
    strncpy(p->id, id, sizeof(p->id) - 1);
    p->id[sizeof(p->id) - 1] = '\0';
    strncpy(p->display_name, display_name, sizeof(p->display_name) - 1);
    p->display_name[sizeof(p->display_name) - 1] = '\0';

    p->has_avatar = avatar_bgra != NULL && w > 0 && h > 0;
    if (p->has_avatar) {
        p->img_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
        p->img_dsc.header.cf = LV_COLOR_FORMAT_ARGB8888;
        p->img_dsc.header.w = (uint32_t)w;
        p->img_dsc.header.h = (uint32_t)h;
        p->img_dsc.header.stride = (uint32_t)(w * 4);
        p->img_dsc.data_size = (uint32_t)(w * h * 4);
        p->img_dsc.data = avatar_bgra;
    }

    lv_obj_t *card = lv_obj_create(persona_grid);
    lv_obj_set_size(card, 128, 148);
    lv_obj_set_style_radius(card, 16, 0);
    lv_obj_set_style_bg_color(card, COLOR_CARD, 0);
    lv_obj_set_style_bg_color(card, COLOR_CARD_PRESS, LV_STATE_PRESSED);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_shadow_width(card, 10, 0);
    lv_obj_set_style_shadow_opa(card, LV_OPA_20, 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(card, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_all(card, 10, 0);
    lv_obj_set_style_pad_row(card, 8, 0);
    lv_obj_add_event_cb(card, persona_card_cb, LV_EVENT_CLICKED, (void *)(intptr_t)persona_count);

    create_avatar(card, 64, p);

    lv_obj_t *name = lv_label_create(card);
    lv_label_set_text(name, p->display_name);
    lv_obj_set_style_text_color(name, COLOR_TEXT, 0);
    lv_obj_set_style_text_font(name, &lv_font_montserrat_16, 0);

    persona_count++;
}

void sim_set_status(const char *state, const char *detail) {
    if (detail && strlen(detail) > 0) {
        lv_label_set_text_fmt(status_label, "%s (%s)", state, detail);
    } else {
        lv_label_set_text(status_label, state);
    }

    bool idle = is_idle_state(state);
    bool capturing = strcmp(state, "capturing") == 0;
    g_session_idle = idle;

    if (idle) {
        lv_obj_set_style_bg_color(talk_btn, COLOR_ACCENT, 0);
        lv_obj_set_style_bg_color(talk_btn, COLOR_ACCENT_PRESS, LV_STATE_PRESSED);
        lv_obj_remove_state(talk_btn, LV_STATE_DISABLED);
    } else if (capturing) {
        lv_obj_set_style_bg_color(talk_btn, COLOR_RECORDING, 0);
        lv_obj_add_state(talk_btn, LV_STATE_DISABLED);
    } else {
        lv_obj_add_state(talk_btn, LV_STATE_DISABLED);
    }

    if (capturing) {
        lv_label_set_text(talk_label, "Listening...");
    } else if (idle) {
        lv_label_set_text(talk_label, "Tap to talk");
    } else {
        char buf[48];
        char cap_state[32];
        strncpy(cap_state, state, sizeof(cap_state) - 1);
        cap_state[sizeof(cap_state) - 1] = '\0';
        cap_state[0] = (char)toupper((unsigned char)cap_state[0]);
        snprintf(buf, sizeof(buf), "%s...", cap_state);
        lv_label_set_text(talk_label, buf);
    }

    if (idle) {
        lv_obj_add_flag(cancel_btn, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_remove_flag(cancel_btn, LV_OBJ_FLAG_HIDDEN);
    }
}

void sim_set_transcript(const char *text) {
    if (!text || strlen(text) == 0) {
        lv_obj_add_flag(transcript_bubble, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_label_set_text(transcript_label, text);
    lv_obj_remove_flag(transcript_bubble, LV_OBJ_FLAG_HIDDEN);
}

void sim_set_llm_text(const char *text) {
    if (!text || strlen(text) == 0) {
        lv_obj_add_flag(llm_bubble, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_label_set_text(llm_label, text);
    lv_obj_remove_flag(llm_bubble, LV_OBJ_FLAG_HIDDEN);
}

/* Styled "chat bubble": a rounded container holding a wrapped label,
 * used for both the transcript ("you said") and the response. Starts
 * hidden -- shown once there's real text (avoids empty gray boxes). */
static lv_obj_t *create_bubble(lv_obj_t *parent, const char *prefix, lv_obj_t **out_label) {
    lv_obj_t *bubble = lv_obj_create(parent);
    lv_obj_set_width(bubble, LV_PCT(100));
    lv_obj_set_height(bubble, LV_SIZE_CONTENT);
    lv_obj_set_style_radius(bubble, 12, 0);
    lv_obj_set_style_bg_color(bubble, COLOR_BUBBLE, 0);
    lv_obj_set_style_border_width(bubble, 0, 0);
    lv_obj_set_style_pad_all(bubble, 10, 0);
    lv_obj_remove_flag(bubble, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(bubble, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *tag = lv_label_create(bubble);
    lv_label_set_text(tag, prefix);
    lv_obj_set_style_text_color(tag, COLOR_SUBTEXT, 0);
    lv_obj_set_style_text_font(tag, &lv_font_montserrat_14, 0);
    lv_obj_align(tag, LV_ALIGN_TOP_LEFT, 0, 0);

    lv_obj_t *label = lv_label_create(bubble);
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(label, LV_PCT(100));
    lv_obj_set_style_text_color(label, COLOR_TEXT, 0);
    lv_obj_set_style_text_font(label, &lv_font_montserrat_14, 0);
    lv_obj_align_to(label, tag, LV_ALIGN_OUT_BOTTOM_LEFT, 0, 4);

    *out_label = label;
    return bubble;
}

static lv_obj_t *build_select_screen(void) {
    lv_obj_t *scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, COLOR_BG, 0);
    lv_obj_set_flex_flow(scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(scr, 16, 0);
    lv_obj_set_style_pad_row(scr, 12, 0);

    lv_obj_t *title = lv_label_create(scr);
    lv_label_set_text(title, "Choose a persona");
    lv_obj_set_style_text_color(title, COLOR_TEXT, 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_24, 0);

    persona_grid = lv_obj_create(scr);
    lv_obj_remove_style_all(persona_grid);
    lv_obj_set_size(persona_grid, LV_PCT(100), LV_PCT(100));
    lv_obj_set_flex_flow(persona_grid, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_style_pad_column(persona_grid, 12, 0);
    lv_obj_set_style_pad_row(persona_grid, 12, 0);

    return scr;
}

static lv_obj_t *build_session_screen(void) {
    lv_obj_t *scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, COLOR_BG, 0);
    lv_obj_set_flex_flow(scr, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(scr, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_all(scr, 16, 0);
    lv_obj_set_style_pad_row(scr, 12, 0);

    /* Header: an explicit "change persona" button (back to the picker),
     * plus the current persona's avatar/name for identity. */
    lv_obj_t *header = lv_obj_create(scr);
    lv_obj_remove_style_all(header);
    lv_obj_set_size(header, LV_PCT(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(header, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(header, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(header, 12, 0);

    lv_obj_t *back_btn = lv_button_create(header);
    lv_obj_set_height(back_btn, 32);
    lv_obj_set_style_radius(back_btn, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(back_btn, COLOR_CARD, 0);
    lv_obj_set_style_bg_color(back_btn, COLOR_CARD_PRESS, LV_STATE_PRESSED);
    lv_obj_set_style_pad_hor(back_btn, 14, 0);
    lv_obj_add_event_cb(back_btn, go_to_select_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, LV_SYMBOL_LEFT " Change persona");
    lv_obj_set_style_text_color(back_label, COLOR_TEXT, 0);
    lv_obj_set_style_text_font(back_label, &lv_font_montserrat_14, 0);
    lv_obj_center(back_label);

    header_avatar_holder = lv_obj_create(header);
    lv_obj_remove_style_all(header_avatar_holder);
    lv_obj_set_size(header_avatar_holder, 40, 40);
    lv_obj_remove_flag(header_avatar_holder, LV_OBJ_FLAG_SCROLLABLE);

    header_name = lv_label_create(header);
    lv_obj_set_style_text_color(header_name, COLOR_TEXT, 0);
    lv_obj_set_style_text_font(header_name, &lv_font_montserrat_20, 0);

    /* Big circular talk button. */
    talk_btn = lv_button_create(scr);
    lv_obj_set_size(talk_btn, 150, 150);
    lv_obj_set_style_radius(talk_btn, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(talk_btn, COLOR_ACCENT, 0);
    lv_obj_set_style_shadow_width(talk_btn, 20, 0);
    lv_obj_set_style_shadow_color(talk_btn, COLOR_ACCENT, 0);
    lv_obj_set_style_shadow_opa(talk_btn, LV_OPA_30, 0);
    lv_obj_add_state(talk_btn, LV_STATE_DISABLED);
    lv_obj_add_event_cb(talk_btn, talk_btn_cb, LV_EVENT_CLICKED, NULL);
    talk_label = lv_label_create(talk_btn);
    lv_label_set_text(talk_label, "Tap to talk");
    lv_obj_set_style_text_color(talk_label, COLOR_TEXT, 0);
    lv_obj_set_style_text_font(talk_label, &lv_font_montserrat_16, 0);
    lv_obj_set_width(talk_label, 110);
    lv_label_set_long_mode(talk_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(talk_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(talk_label);

    cancel_btn = lv_button_create(scr);
    lv_obj_set_size(cancel_btn, 120, 40);
    lv_obj_set_style_radius(cancel_btn, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(cancel_btn, COLOR_CARD, 0);
    lv_obj_set_style_bg_color(cancel_btn, COLOR_CARD_PRESS, LV_STATE_PRESSED);
    lv_obj_add_flag(cancel_btn, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(cancel_btn, cancel_btn_cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *cancel_label = lv_label_create(cancel_btn);
    lv_label_set_text(cancel_label, "Cancel");
    lv_obj_set_style_text_color(cancel_label, COLOR_TEXT, 0);
    lv_obj_center(cancel_label);

    status_label = lv_label_create(scr);
    lv_label_set_text(status_label, "disconnected");
    lv_obj_set_style_text_color(status_label, COLOR_SUBTEXT, 0);
    lv_obj_set_style_text_font(status_label, &lv_font_montserrat_14, 0);

    lv_obj_t *transcript_wrap = lv_obj_create(scr);
    lv_obj_remove_style_all(transcript_wrap);
    lv_obj_set_size(transcript_wrap, LV_PCT(100), LV_SIZE_CONTENT);
    transcript_bubble = create_bubble(transcript_wrap, "You", &transcript_label);

    lv_obj_t *llm_wrap = lv_obj_create(scr);
    lv_obj_remove_style_all(llm_wrap);
    lv_obj_set_size(llm_wrap, LV_PCT(100), LV_SIZE_CONTENT);
    llm_bubble = create_bubble(llm_wrap, "Assistant", &llm_label);

    return scr;
}

void ui_init(void) {
    select_scr = build_select_screen();
    session_scr = build_session_screen();
    lv_screen_load(select_scr);
}
