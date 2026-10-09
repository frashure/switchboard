# Switchboard — Design & Development Plan

> **Status:** Draft v7 (2026-09-17). **M0 through M3 are complete.** Gateway (`gateway/`) runs the full pipeline end-to-end against live OWUI/Whisper/Piper: dynamic persona discovery with periodic refresh (6 real OWUI personas, no hardcoded config), tap-to-talk + Cancel over the real WS protocol with true mid-turn interruption (CLI client and browser virtual device), VAD-driven turn-ending confirmed on all 3 paths, multi-turn conversation continuity, and a long list of reliability fixes documented in `docs/protocol.md`. A browser-based LVGL touchscreen UI prototype (`firmware/lvgl_sim/`) now lets the persona-select/tap-to-talk screen flow be reviewed pre-hardware. **Next: M4 (real firmware)** — blocked on hardware purchase (`plan.md` shopping list) and the touch-controller decision (open item below); nothing further to do Gateway-side until hardware arrives, beyond opportunistic hardening.
>
> **Companion docs:** [`plan.md`](./plan.md) (original concept) · [`q_and_a.md`](./q_and_a.md) (full Q&A). This file is the running reference.

---

## 1. TL;DR

A conversational AI assistant for the desk — a study aide, not a hands-free room assistant. An **ESP32-S3 thin client** with a **touchscreen** lets the user pick a persona and push-to-talk, handles mic capture and speaker playback, and renders transcript/response text via LVGL. A **Python AI Gateway** (Docker, FastAPI/WebSocket) on the GPU server coordinates everything: endpointing (VAD), STT (Wyoming Whisper), the LLM + tool loop (Open WebUI / Ollama), and TTS (Wyoming Piper). Profiles map **persona (screen-selected) → model, system prompt, voice**.

**Build order:** Gateway first, tested with a CLI batch client and a browser-based virtual device (no hardware needed — the virtual device speaks the real WS protocol and doubles as a UX preview of the touchscreen flow) → then ESP32 firmware + touchscreen UI together (the screen is the primary control surface, not an add-on).

---

## 2. Locked decisions

| Area | Decision |
|---|---|
| **Gateway runtime** | Python **3.12**, FastAPI + uvicorn + **pydantic v2** (async, WebSocket). |
| **Deployment** | Gateway = **Docker container** on the Ubuntu 24.04 host (R9700 GPU). ESP32 on the **same LAN**. |
| **STT / TTS** | Wyoming **Whisper** / **Piper**, spoken to over the **Wyoming protocol v2** (assumed; verify in M0). |
| **LLM** | Open WebUI is **central** (manages workspace models, tools, memory). Tool loop runs natively in OWUI via **Option C — OWUI WebSocket session** (closest to the browser UI→Server flow). See Section 5. |
| **OWUI environment** | Open WebUI **v0.11.3** · `ENABLE_PLUGINS=true` · Ollama behind OWUI (Gateway does **not** call Ollama directly on the main path). |
| **Tools** | Open WebUI-native tools + custom Python tools that call other services via their APIs. |
| **Device output** | Gateway sends **final LLM text** to the device screen (viewable after TTS). Real-time token streaming is **optional / deferred**. |
| **Trigger / persona select** | **No wake word in v1.** User selects a persona from a menu on the device touchscreen; a press/hold push-to-talk control starts listening. Wake word may be revisited later as an optional add-on, not a v1 requirement. |
| **Display** | **Touchscreen is a v1 requirement** — the primary control surface for persona selection and push-to-talk, not deferred. |
| **Scope (v1)** | A household (a few accounts, rarely more than one talking at a time), trusted network (Tailscale). Sign-in with Open WebUI accounts (`AUTH_MODE=owui`, default) or none (`AUTH_MODE=none`, single shared account). |
| **Endpointing (Q6)** | Push-to-talk starts capture; **Gateway-side VAD** (`silero-vad`) detects end-of-speech to cut the utterance — continuous streaming while the control is held/active. |
| **Audio formats (Q7)** | Input = **16-bit / 16 kHz / mono / PCM**. Output rate = **announced per stream**; default to **`medium` (22050 Hz)** voices. |
| **Order** | Gateway + test client first; firmware is a later phase (hardware not yet purchased). |

---

## 3. Architecture & connection matrix

```
 ESP32-S3 (thin client)          AI GATEWAY (Python, Docker)              Server services
 ┌──────────────────────┐   WebSocket    ┌───────────────────────────────┐   ┌────────────────────┐
 │ Touchscreen: persona │◄──────────────►│  session state machine        │   │ Open WebUI (LLM,   │
 │  select + push-talk  │  audio (bin)   │  VAD / endpointing            ├──►│  tools, memory)    │
 │ I2S mic (in)         │  text/status   │  STT client (Wyoming v2)      │   │ Ollama (models)    │
 │ I2S amp (out)        │                │  TTS client (Wyoming v2)      ├──►│ Wyoming Whisper    │
 │ LVGL (transcript/UI) │                │  LLM + ReAct tool loop        │   │ Wyoming Piper      │
 └──────────────────────┘                └───────────────────────────────┘   └────────────────────┘
```

- **ESP32 ↔ Gateway:** persistent WebSocket (audio = binary frames; control/text = JSON).
- **Gateway ↔ Whisper / Piper:** TCP, Wyoming protocol v2.
- **Gateway ↔ Open WebUI:** **WebSocket chat session (Option C)** — the same path the browser uses. OWUI runs the ReAct tool loop **server-side** and streams tokens + tool events back; the Gateway relays the final text to the device. Ollama sits behind OWUI (not called directly on the main path).

**Profile routing:** `persona_id` (screen-selected) → profile → `{ model, voice }`. **Personas are discovered dynamically from OWUI's own model list at runtime** (`OwuiClient.list_models()`, filtered to `preset: true` models) rather than hardcoded — a "thin client" decision: OWUI presets already carry their own system prompt and tool set server-side, so the Gateway no longer duplicates either. The only Gateway-side config left is which Piper voice each persona uses, since OWUI has no concept of that:

```yaml
# config/voices.yaml (real file, not a sketch)
default_voice: "en_US-lessac-medium"     # → 22050 Hz, used for any persona without an override
overrides:
  phil: "en_GB-alan-low"                 # → 16000 Hz
```

---

## 4. Audio pipeline & endpointing

**Model: Option 2 — device is a dumb audio pipe; the Gateway does VAD/endpointing.**

Rationale: keeps firmware minimal (touchscreen handles triggering; audio capture stays simple); VAD is trivial in Python (`silero-vad`) and tunable in one place; easy to test with a WAV file; future-proofs barge-in / streaming STT.

**Device state machine:**

```
IDLE (screen: persona list) ──user taps persona──► READY (persona locked, screen shows "hold to talk")
  │
  │  user presses/holds push-to-talk control
  ▼
CAPTURE ──Gateway VAD: end-of-speech──► (Gateway: STT → LLM → TTS) ──► PLAY ──done──► READY (same persona)
  │  device sends `select_persona` once (or on persona change), then `talk_start`/`talk_end` per turn,
  │  streaming raw PCM continuously while active (Gateway VAD, not device VAD, cuts the utterance)
  PLAY: mic muted, barge-in OFF for v1
```

After `PLAY`, the device returns to **`READY`** — not a cold `IDLE` — so follow-up turns with the same persona don't require re-selecting it on screen. The user only returns to `IDLE` by explicitly switching personas.

**Gateway per-turn flow:**
1. Receive `select_persona` (once per session or on persona change) → resolve profile; each `talk_start`/`talk_end` begins/hints a capture within that persona's session.
2. Accumulate incoming PCM into a ring buffer.
3. `silero-vad` detects end-of-speech → cut the utterance.
4. STT (Whisper) → transcript.
5. LLM + tool loop (Section 5) → final text.
6. Send `transcript` + final `llm_text` to device (screen).
7. TTS (Piper) → stream audio back with the voice's `sample_rate`.
8. `done` → device returns to READY (same persona, ready for a follow-up turn).

**Proposed VAD defaults (tune in M3):** pre-roll ~300 ms · pause threshold ~600 ms silence · max utterance ~15 s.

**Formats:**
- **Input (mic → Whisper):** 16-bit / 16 kHz / mono / PCM (Wyoming standard). INMP441 is 24-bit I2S; ESP32 I2S configured to 16-bit.
- **Output (Piper → amp):** rate is **per-voice** (`medium` = 22050 Hz, `low` = 16000 Hz). MAX98357A is a digital I2S amp (no DAC) — ESP32 is I2S master and plays any of these rates. **The Gateway announces `sample_rate` in the stream header; the device sets its clock to it.** Default to `medium` voices.

---

## 5. LLM & tool-calling path  — ✅ DECISION: Option C (OWUI WebSocket session)

**Locked.** The tool loop runs **natively inside Open WebUI** over its WebSocket chat session — the same path the browser UI uses. Rationale (user): it's "as close to the native behavior of the Open WebUI UI→Server flow," so tool execution/selection, memory, and streaming are all handled by OWUI exactly as a human chat would, and we don't re-implement the loop.

**Environment:** OWUI **v0.11.3** · `ENABLE_PLUGINS=true` · Ollama behind OWUI.

### The constraint (confirmed from Open WebUI discussion [#9435](https://github.com/open-webui/open-webui/discussions/9435))
- Long-lived bug (0.5.9 → 0.6.34, still reported Jan/Apr 2026): OWUI **executes** the tool but **never re-invokes the LLM** with the result → no final answer.
- Root cause (confirmed): **the native tool loop is driven by the frontend over WebSocket (`session_id`); REST `/api/chat/completions` has no server-side loop.**
- Community fix **PR #23581** adds a server-side loop for REST calls.
- Also: `ENABLE_PLUGINS` is a global gate — if false, `get_tools()` returns `{}` and **all** user-defined tools are blocked.

**Bottom line:** "Gateway drives the ReAct loop over OWUI **REST**" does **not** work out of the box. We therefore use the **WebSocket session path** (Option C), which OWUI already completes for the browser.

### Considered paths (C chosen; others documented as rejected/fallback)

| Option | How the tool loop runs | Keeps OWUI tools+memory? | Client complexity | Robustness (real-time) |
|---|---|---|---|---|
| **A′. REST + server-side loop** | OWUI runs the loop server-side — *requires* #23581 or a release that ships it | ✅ fully | **Low** (one REST call) | Medium (patch/version dependent) |
| **✅ C. WebSocket session** | OWUI runs the loop (native browser path) — **CHOSEN** | ✅ fully | Medium (implement OWUI WS chat protocol) | Medium (undocumented/fragile) |
| **B′. Ollama + Gateway tools** | **Gateway runs the loop**; tools = Gateway Python calling the same service APIs | ❌ OWUI = UI only; memory pulled separately | Low (Ollama native tools, clean streaming) | **High — most robust** |
| **D. Pipe/Function-as-tool** | One OWUI Function wraps tool execution → single REST-completable call | ✅ (tools stay in OWUI) | Medium (OWUI function plumbing) | Medium (bespoke; proven to work in the HA project) |

### Decision: Option C — OWUI WebSocket session
The Gateway acts as an **OWUI chat client over the browser's WebSocket path**:
1. Resolve the profile (screen-selected persona → `{ model, system_prompt, voice }`).
2. Open OWUI's **chat session** (establish `session_id` + auth, as the browser does).
3. Send the STT transcript as a user message with the profile's system prompt + model.
4. **OWUI runs the ReAct loop** (tool calls → execute → feed back → final answer) and **streams** the result.
5. Gateway collects the **final text**, then relays it to the device (screen) and to Piper (TTS).

> ⚠️ **The one real unknown:** OWUI's WebSocket chat frame/event schema is **not officially documented** and has changed across versions. **M0's job is to reverse-engineer it against v0.11.3** (session lifecycle, message frame, tool-call / tool-result events, final-answer event, streaming framing) and record it in `docs/protocol.md`. Everything else in the design is unaffected by this.

### Fallback (only if C proves unworkable)
**B′ (Ollama + Gateway-side tools):** Gateway talks to Ollama directly with native `tools` + streaming and runs the ReAct loop itself (tools re-expressed as Gateway Python calling the same service APIs). Trade-off: loses OWUI's tool/memory management. **Trigger:** M0 shows the v0.11.3 WS path can't complete a tool loop for a non-browser client.

---

## 6. Proposed repo layout

```
switchboard/
  docs/                 # plan.md, q_and_a.md, design.md (this file), protocol.md
  gateway/
    Dockerfile
    docker-compose.yaml         # gateway + refs to whisper / piper / openwebui
    pyproject.toml
    config/
      voices.yaml                # per-persona Piper voice only -- personas themselves come from OWUI
    switchboard/
      main.py                   # FastAPI app + /ws endpoint
      server/
        session.py              # per-session state machine (IDLE/READY/CAPTURE/THINK/PLAY)
        ws.py                   # ws handler; binary audio + json control framing
      audio/
        vad.py                  # silero-vad endpointer
        ring.py                 # ring buffer
      stt/
        whisper.py              # Wyoming v2 STT client
      tts/
        piper.py                # Wyoming v2 TTS client (announces sample_rate)
      llm/
        owui_client.py          # Option C — OWUI WebSocket chat-session client; streams tokens + tool events
        session.py              # OWUI session_id + auth + model management
        # B′ fallback (only if Option C is unworkable):
        ollama_client.py        # Gateway-side ReAct loop over Ollama native tools
        tools.py                # tool registry / executor
      profiles.py               # persona discovery from OWUI's model list + persona_id routing
      text.py                    # markdown stripping before TTS
      config.py                 # pydantic settings
    tests/
    client/
      cli_client.py             # WAV-in → transcript + text + TTS-out (batch/regression test harness)
      virtual_device/           # browser-based hardware-free stand-in for the ESP32
        index.html              # persona select + hold-to-talk button (mirrors the touchscreen UI)
        app.js                  # getUserMedia + AudioWorklet capture (16-bit/16kHz PCM), speaks the
                                 # real WS protocol (select_persona/talk_start/talk_end + binary audio),
                                 # plays back streamed TTS via Web Audio
  web/                    # THE client: Svelte 5 + TS PWA (persona picker, animated session screen,
                           # streaming playback, installable). Logic in lib/session.svelte.ts behind
                           # injected gateway/mic/player interfaces; components are presentation only.
                           # See web/README.md for the architecture and extension points.
  firmware/
    lvgl_sim/             # Pre-hardware UI prototype: real LVGL widget tree (ui.c),
                           # compiled to WASM (Emscripten, containerized build via
                           # build.sh), driving the real Gateway WS protocol in-browser.
                           # Not the real firmware -- see docs/design.md Section 9.
    # Phase 3 (real ESP32 firmware) — not started (PlatformIO or ESP-IDF; no hardware yet)
  README.md
```

---

## 7. WebSocket protocol (ESP32 ↔ Gateway)

Binary frames for audio, JSON frames for control/text.

**Client → Gateway**
- `{"type":"select_persona","id":"<persona_id>"}` — sent when the user taps a persona on the touchscreen menu.
- `{"type":"talk_start"}` — tap-to-talk: opens capture. `{"type":"talk_end"}` is now optional/client-dependent — the Gateway's VAD (or the `vad_max_utterance_s` cap) is what actually cuts the utterance; `client/virtual_device/` never sends it at all in the tap-to-talk UI, while `client/cli_client.py` still sends it explicitly (as a hint/cancel-safety with a grace period, not the sole end-of-turn signal) for scripted, non-interactive testing.
- `<binary>` — raw PCM 16-bit / 16 kHz / mono chunks
- `{"type":"cancel"}` — abort the current turn, at any point (capture, thinking, or speaking). Runs the turn as a cancellable background task under the hood; confirmed interrupting within ~0s at all three stages.

**Gateway → Client**
- `{"type":"status","state":"listening|thinking|speaking|done|error","detail":"?"}`
- `{"type":"transcript","text":"<stt result>"}`
- `{"type":"llm_text","text":"<final answer>"}`
- `{"type":"audio_header","sample_rate":..,"bits":..,"channels":..}` — sent **once per answer, when the first audio is ready**, immediately after `status: "speaking"` (so "speaking" now means audio is actually about to play, not that synthesis has begun).
- `<binary>` × many — TTS PCM, streamed **as it is synthesized**: the answer is split into sentence-sized chunks, each synthesized and sent as soon as it's ready, in ≤32KB frames. The client must play frames as they arrive (back-to-back, gapless) rather than wait for `done`; time-to-first-audio is then ~1 chunk of synthesis instead of the whole answer (measured: 2,100-char answer, 5.5s → 0.9s). Frames are still chunked because a single multi-MB frame exceeds common WS size limits and closes the connection.
- `{"type":"stop_audio"}` — sent when `cancel` arrives after TTS audio has already been delivered; the server can't un-send it, so this tells the client to stop local playback.
- `{"type":"done"}` — all audio for this answer has been **sent** (the client may still be playing it; it should stay in a "speaking" state until playback actually ends).

---

## 8. Milestones (all testable without the ESP32)

| # | Milestone | Exit criteria |
|---|---|---|
| **M0** | ✅ **Protocol feasibility spike (Option C)** | Against the **live OWUI v0.11.3**: (a) open the WebSocket chat session the browser uses; (b) send a tool-eligible prompt and confirm OWUI runs the tool **and** returns a final answer (streaming) — capturing the exact frame/event schema; (c) confirm Wyoming v2 Whisper + Piper endpoints respond. **Locks the Option C protocol; de-risks M1/M2.** |
| **M1** | ✅ Gateway skeleton | FastAPI + `/ws`; config/profile loading; session state machine; CLI test client (WAV → transcript + text + TTS out) **and** browser-based virtual device (persona select + push-to-talk over the real WS protocol) — both exercise the Gateway with zero hardware. Single LLM call, no tool loop yet. |
| **M2** | ✅ Tool loop (Option C) | Working tool execution + ReAct loop over the **OWUI WS session**; ≥ 1 real tool call returns a synthesized final answer (no Gateway-side loop needed). Confirmed repeatedly (`ophelia`'s weather tool, both in the M0 spike and again in Gateway testing). |
| **M3** | ✅ End-to-end voice + personas | Full STT → LLM/tools → TTS across two personas; VAD/turn/latency tuning. Exceeded scope: personas now discovered dynamically from OWUI (all 6 found: `ophelia`, `phil`, `aida`, `libby`, `pacbot`, `mallory`) rather than hardcoded; 4 of 6 confirmed working end-to-end via `cli_client.py` (`ophelia`, `phil` extensively; `aida`, `libby` at least once each, including their voice overrides resolving correctly). `pacbot`/`mallory` not yet individually tried, but exercise the identical code path. VAD confirmed on all 3 end-of-capture paths; latency/connection issues found and fixed (see `docs/protocol.md`). |
| **M4** | Firmware & touchscreen UI (when hardware arrives) | Touchscreen persona menu (LVGL), push-to-talk capture/stream, sample-rate-aware playback, transcript/response rendering on screen, device FSM (IDLE/READY/CAPTURE/PLAY) over the WS protocol. |
| **M5** | Optimization | Buffer / latency tuning against R9700 throughput. |

---

## 9. Open items / next actions

- [x] **Gating questions answered** → OWUI **v0.11.3** · `ENABLE_PLUGINS=true` · path = **Option C** (OWUI WebSocket session). *(locked — do not re-ask)*
- [x] **M0 spike:** confirmed end-to-end — `scripts/m0_owui_spike.py` drove a real tool call (`openweathermap_forecast`) through OWUI v0.11.3 from a plain Python client and got the correct final answer. Full frame/event schema recorded in `docs/protocol.md`, including two non-obvious bugs found along the way (missing `user-join` emit; malformed user/assistant message-tree ids causing a silent generic-response failure mode).
- [x] Reverse-engineer the **OWUI v0.11.3 WS chat protocol** → recorded in `docs/protocol.md`.
- [x] Confirm **Wyoming v2** on the running Whisper/Piper containers — confirmed via `scripts/wyoming_test.py`: `faster-whisper` (medium-int8) + `piper`, full synthesize→transcribe round trip matched exactly. Bonus finding: Whisper handled 22050Hz audio fine with no resampling, softening the strict-16kHz assumption for anything talking to Whisper (the ESP32→Gateway mic path still targets 16kHz for bandwidth reasons, independent of this).
- [ ] A few open questions remain in `docs/protocol.md` (non-blocking) — minimal required OWUI completion fields, no-tool-call terminal shape, `/api/tasks/chat/{chat_id}` as a reconnect path, and whether to reuse one persistent `chat_id` per persona vs. a fresh one per turn (decide explicitly in M1).
- [x] **M1 core pipeline confirmed end-to-end**: `gateway/` scaffolded (FastAPI `/ws`, config/profiles, session state machine, Wyoming STT/TTS clients, OWUI client) and tested for real, repeatedly — synthesized audio → Whisper → OWUI (`ophelia`/`phil` models) → Piper → valid response WAV, through the actual `/ws` protocol via `client/cli_client.py`. Several bugs found and fixed along the way: `ws.py` didn't handle client disconnect cleanly; event-loop-blocking calls in the OWUI client and VAD; a Socket.IO delivery reliability issue in `python-socketio`'s async client, mitigated with a REST-based recovery fallback (full writeup in `docs/protocol.md` Section 2). VAD/silero end-of-speech detection also now confirmed working independently: found and fixed a real gap where `client/cli_client.py` forwarded the source WAV's native sample rate instead of resampling to the protocol-mandated 16kHz (matching what the real device firmware must do), then confirmed with a silence-padded test WAV that the VAD fires on its own (`capture_end_reason=vad`) rather than always relying on the `talk_end` grace-period fallback (`capture_end_reason=talk_end_fallback`) — `Session._finish_capture` now reports which triggered each turn via the `thinking` status detail. The third path, the ring-buffer's `max_duration` safety cap, is also now confirmed (fed 18s of continuous non-speech tone against the 15s `vad_max_utterance_s` limit) — all three capture-ending paths in the state machine are verified working.
- [x] **Personas are now discovered dynamically from OWUI at runtime** (`OwuiClient.list_models()`, filtered to `preset: true`) instead of hardcoded in a config file — confirmed working (`GET /profiles` returns all 6 real personas: `phil`, `pacbot`, `aida`, `ophelia`, `libby`, `mallory`, live from OWUI). `tool_ids`/`system_prompt` dropped entirely from the Gateway's request payload — OWUI presets already carry their own, and the previous hardcoded `tool_ids` was actually *restricting* personas to a subset of their real configured tools (e.g. ophelia to 1 of ~9). `config/voices.yaml` replaces `config/profiles.yaml`, holding only the one thing OWUI has no concept of: Piper voice per persona. Both `main.py` and `ws.py` lazily re-discover personas on first use if startup discovery failed (mirrors the OWUI login resilience pattern).
- [x] **Interaction model changed from hold-to-talk to tap-to-talk + Cancel**, per user feedback that holding a button while speaking isn't practical: `client/virtual_device/` now has a single tap to start listening (the Gateway's own VAD decides when you've stopped, same mechanism already confirmed working) and a separate Cancel button. **Known limitation, since fixed**: Cancel previously only interrupted a turn during capture, since the WS message loop processed messages strictly sequentially and blocked on the whole turn otherwise. Fixed: `_finish_capture` now runs as a cancellable `asyncio.Task` (`Session._turn_task`) instead of being awaited directly from the message loop, so `cancel()` can `task.cancel()` + await it while the loop stays free to receive the cancel in the first place. Confirmed working at all three points a turn can be interrupted (STT, LLM wait, TTS/speaking) — cancel-to-done latency ~0s in each case, vs. previously waiting out the whole turn (up to ~215s worst case). Added `{"type":"stop_audio"}` (`Emitter.stop_audio`) for the one case cancellation genuinely can't undo — TTS audio already delivered to the client — telling the client to stop local playback; confirmed firing correctly. One caveat noted in code: cancelling a task blocked inside a synchronous network call running via `asyncio.to_thread` (used throughout `owui_client.py`) stops the Gateway from *waiting on or acting on* that call, but can't forcibly kill the underlying OS thread — OWUI may keep generating in the background regardless, same as an ordinary disconnect.
- [x] **Bug found and fixed right after shipping tap-to-talk**: the talk button appeared stuck disabled/red ("listening") immediately after selecting a persona, before any tap — not actually capturing. Root cause: a naming collision in `app.js` — the server's `status: "listening"` (emitted once, on `select_persona`, meaning "ready for you to talk") and the client's own optimistic local state for "mic is actually open" were both called `"listening"`. The server's status arrived just after `selectPersona()`'s own `updateButtons("ready")` call and overwrote it with the disabled/capturing styling. Fixed by renaming the client-only state to `"capturing"` and treating server status `"listening"` as idle/ready in `updateButtons()`.
- [x] **Markdown stripping added before TTS** (`switchboard/text.py`, `strip_markdown()`) — models return markdown-formatted text (headers, bold, bullets) which a TTS voice previously read out literally. Applied only to the text passed to Piper, not to the `llm_text` sent to the display, in case a future display renders markdown properly.
- [x] `client/virtual_device/` (browser UI) tested against the running Gateway with a real browser + real microphone — worked end-to-end. Found and fixed two real bugs in the process: (1) missing CORS headers, since the virtual device's static-file origin differs from the Gateway's (`main.py` now adds `CORSMiddleware`); (2) `chat_id` alone doesn't continue a conversation thread in OWUI — `parent_id` must also be threaded from the previous turn's assistant message id, or every turn after the first becomes a sibling branch of the first message instead of appending to it. Full writeup in `docs/protocol.md`. Confirmed fixed with a real multi-turn follow-up question test.
- [x] Chats created via the Gateway now get a real title (OWUI's own auto-titling never fired for programmatic chats) — the Gateway sets one itself from the transcript after the first turn. See `docs/protocol.md` Section 2.
- [x] **Persona list was only ever discovered once, at startup** — a persona added/renamed/removed in OWUI after Gateway startup wouldn't show up without a restart. Fixed: `ProfileRegistry.needs_refresh()` re-runs discovery every `REFRESH_INTERVAL_S` (300s), checked lazily on both `/profiles` and `/ws`.
- [x] **Multi-tool-call turns were incorrectly reported as `llm_failed`** — a real turn (calendar search + note lookup + knowledge grep + note edit, 4 sequential tool round-trips) completed correctly in OWUI, but the Gateway's REST stable-length poll (`_fetch_final_text_via_rest`) gave up after a hardcoded 180s and raised, even though the answer was sitting there finished. Fixed: budget is now `settings.llm_rest_poll_timeout` (480s, up from 180s) — tool-heavy turns are a normal case here, not an edge case. Worst-case LLM-turn latency is now up to ~515s (35s socket gate + up to 480s REST stable-length poll) for genuinely slow multi-tool turns (docs/protocol.md Section 2) — fine as a correctness-first safety net, worth tightening later if it becomes annoying in practice.
- [ ] **Recommend, not yet done**: constrain persona `system_prompt`s to request concise, spoken-style answers (no markdown/headers, short) — a real test question produced a 4109-character essay that synthesized to 4.7 minutes of audio. All M1 fixes make this pipeline technically correct end-to-end, but that response length is a bad fit for voice regardless of correctness.
- [x] uvicorn's default `--ws-ping-interval`/`--ws-ping-timeout` (20s/20s = 40s combined) were closing WS connections mid-turn on any wait longer than 40s — root cause of a real disconnect found via the virtual device (confirmed via exact `40.00s` connection duration in DevTools). Fixed: both the dev run command and `Dockerfile`'s `CMD` now pass `--ws-ping-interval 120 --ws-ping-timeout 120`.
- [x] Startup made resilient to OWUI being momentarily unreachable — `main.py`'s lifespan no longer fails hard if the eager connect fails; `OwuiClient` connects lazily on first real use otherwise. See docs/protocol.md Section 2.
- [x] **Tablet-as-device path (alternative to ESP32 firmware for M4)**: an old Fire HD 8 can run the existing LVGL-in-browser client (`firmware/lvgl_sim/`) as a kiosk (Fully Kiosk Browser, sideloaded; no OS replacement needed). Groundwork done: the Gateway now serves the web clients itself (`SWITCHBOARD_STATIC_DIR`; LVGL UI at `/`, plain-HTML device at `/virtual_device/`), clients default to the page's own origin and switch to `https://`/`wss://` on HTTPS pages (browsers require HTTPS for mic access), `bridge.js` auto-reconnects with backoff, and the page is fullscreen/scaled. Deployment is `gateway/docker-compose.yaml`: Gateway container on the same Docker network as OWUI/Whisper/Piper (reached by container name; Wyoming is raw `tcp://`, not HTTP) behind a Tailscale sidecar that terminates HTTPS (`tailscale-serve.json`). Multi-stage `gateway/Dockerfile` compiles the LVGL UI and uses CPU-only torch. **Verified**: image builds, serves `/`, `/virtual_device/`, `/profiles` (6 personas with avatars) and logs in to OWUI. **Not yet verified**: the compose stack on the real server (Tailscale sidecar/serve, Docker network), a full voice turn from the container, touch input and speaker-to-mic echo on the actual tablet.
- [x] OWUI login hardening: `connect()` retried 10x on any HTTP error and every request restarted the burst when no token was held, which tripped OWUI's sign-in rate limit (429) and kept it tripped. Now retries only connection errors/5xx, fails fast on 4xx, and waits `LOGIN_COOLDOWN_S` (20s) between bursts.
- [x] **Streaming TTS**: measured that the gap between "speaking" appearing and sound starting was the Gateway synthesizing the *entire* answer first (Piper ~27x real time, ~0.2s/100 chars; 2,530 chars -> 5.6s of silence, transfer only 0.15s). Now `text.split_for_speech()` splits the answer at paragraph/sentence boundaries (not inside abbreviations, initials or decimals; short fragments merged; first chunk capped at ~120 chars so it's quick, later chunks up to 350), `session.py` synthesizes and sends chunk by chunk, and both web clients schedule frames back-to-back via Web Audio. Time-to-first-audio on a 2,130-char answer: **0.87s** (was ~5.5s); the whole answer is delivered in ~4.8s but stays seconds ahead of playback (no underrun). Protocol change: `audio_header` is sent once when the first audio is ready, `status: "speaking"` moved to that moment, `done` means "all audio sent". Unit tests in `gateway/tests/test_text.py`; client playback logic checked in a mocked-Web-Audio harness (back-to-back scheduling, `ready` only after last frame ends + `done`, cancel discards in-flight frames and stale `onended`). Not yet measured over the tailnet / on the tablet.
- [x] **New web app (`web/`) is now the primary client**; the LVGL build is kept as a prototype for future embedded devices (ESP32) but is no longer developed for the tablet path. Why: LVGL-in-WASM rendered a 480x320 canvas scaled up (soft on a tablet), cost 1.4 MB + a permanent render loop, needed a C-to-JS bridge for everything (three bridge bugs this session), and every UI tweak was a container rebuild. The web app is Svelte 5 + TypeScript + Vite (~25 KB gzipped JS), built in the Docker image's Node stage and served by the Gateway at `/` (LVGL at `/lvgl/`, plain client at `/virtual_device/`). Highlights: per-persona accent colour derived from the avatar (one `--hue`, all tints derived in CSS so it works on older Chromium); shared-element avatar morph between picker and session via the View Transitions API (CSS fallback otherwise); orb that reacts to mic/voice level and shows listening/thinking/speaking states; markdown-rendered replies (HTML-escaped first, so model output can't inject markup); conversation history; tap-while-listening to send early (`talk_end`); PWA (installable, offline app shell, screen wake lock, auto-reconnect). Architecture is ports-and-adapters: `SessionStore` holds all logic and takes gateway/capture/player as injected interfaces, so 36 unit tests cover full turns, cancel, reconnect and error paths without a browser (including the cancel race: after a cancel the store ignores the cancelled turn's in-flight messages until the Gateway's ack). Verified in headless Chrome with a fake microphone against the live Gateway: full voice turn, tap-to-stop, back navigation, no console errors; PWA checks (service worker active, app shell loads offline, `/lvgl/` and `/virtual_device/` not hijacked by the SW navigation fallback). Not yet verified: on the actual tablet (touch feel, View Transitions support in its Silk version, echo).
- [x] **Chatterbox-Turbo TTS (optional, preferred when ready)** -- sound quality is a clear step up from Piper. Benchmarked on the server's AMD Radeon AI PRO R9700 (ROCm 7.2) via `scripts/chatterbox_bench`: ~2.7x real time, ~2.1 s to first audio, ~3.4 GB VRAM -- but a naive deployment *stalled* streaming (1.09x, -5.9 s slack) because MIOpen tunes S3Gen's convolutions per input shape (~4 s per never-seen chunk length; S3Gen 44 s cold vs 5.6 s warm over a 9-chunk answer) and shape follows the chunk's speech-token count, which varies continuously. Fix: pad speech tokens with Chatterbox's silence token to a multiple of 32, trim the padding's samples afterwards, and pre-tune the ~20 buckets at startup (one-time ~230 s, persisted by mounting MIOpen's cache dirs). Result: cold = warm = 2.73x, +2.4 s slack, S3Gen 6.0 s. Padding verified harmless on CPU (identical durations and Whisper transcripts, ~0.95 spectrogram correlation; waveforms differ only by the noise draw, whose shape depends on length). Parallel model instances were *not* needed (the GPU wasn't the bottleneck). Integration: `services/chatterbox` (FastAPI: `/synthesize`, `/health` with warm-up progress, per-voice conditioning cache, optional per-persona reference clips) + `gateway/switchboard/tts` (`TtsRouter`: Chatterbox when `/health` says ready, else Piper; decided **per answer** because an answer's audio is announced with one sample rate -- Chatterbox 24 kHz vs Piper 16/22.05 kHz -- so a first-chunk failure falls back for the whole answer, a mid-answer failure is a real error). Optional compose profile `chatterbox`. 20 gateway tests cover routing, caching of health checks and voice mapping. Verified end to end locally on CPU with the Nano model (real Gateway turn spoken at 24 kHz; killing the service made the next answer use Piper with no error). **Not yet verified on the server:** the compose profile with ROCm devices, startup warm-up under Docker, and listening to Turbo through the full app with persona reference voices.
- [x] **Sign-in with Open WebUI accounts (`AUTH_MODE=owui`, default; `none` keeps the old single-account behaviour).** Everyone (including a shared tablet, via a dedicated account) signs in with their own Open WebUI credentials, so they see only the models *that account* may use and their chats/memory/tools are theirs rather than a shared service account's. Design: `POST /auth/login` makes exactly one Open WebUI sign-in attempt (no retries: a person is waiting, and retrying a wrong password only trips OWUI's 429) and returns an **encrypted, signed, HttpOnly cookie that carries the user's OWUI token** (Fernet; secret from `SESSION_SECRET`) -- stateless, so Gateway restarts don't log anyone out (a tablet is only signed in about monthly; OWUI tokens last 28 days, capped by `session_max_age_days`). `/profiles` and `/ws` require it; a refused WebSocket is accepted then closed with **4401** (no/invalid session) or **4403** (cross-origin handshake) so the browser can see the code and stop reconnecting; an expired/revoked token surfaces as `OwuiUnauthorized` -> HTTP 401 or `status error session_expired` mid-turn -> the app returns to the login screen with a notice. Personas are discovered **per user** (`PersonaDirectory`), with an avatar cache shared safely across users (a user's registry only requests avatars of models OWUI listed for them). Login is throttled per (client, email) in front of OWUI's own limiter (5 failures / 5 min -> 429 + Retry-After); passwords are never stored or logged and the OWUI token is never sent to page JavaScript. Also fixed here: unknown persona -> `status error unknown_persona` instead of killing the socket. Tested: 21 auth tests (including per-user model isolation over HTTP *and* WebSocket, tampered/foreign/expired cookies, throttling, cross-origin WebSocket refusal) plus 15 store tests (including that logout wipes the previous user's conversation), with the key properties mutation-checked; and end to end in headless Chrome against the real Open WebUI (wrong password, login, HttpOnly cookie, WS accepted/refused, reload persistence, sign-out). **Not yet verified:** a non-admin account's *real* model visibility (only an admin account was available), a spoken turn under auth (the Whisper/Piper tunnel was down), and two people talking at once (the 2-concurrent-turn check could not run). The plain-HTML and LVGL clients have no login screen, so they stop working when `AUTH_MODE=owui`.
- [ ] Firmware: decide **PlatformIO vs ESP-IDF** when M4 starts (only if the tablet path doesn't pan out).
- [ ] Pick a touch-capable display/controller (e.g., resistive XPT2046 vs. capacitive) when hardware is purchased — deferred to hardware research, not a v1 blocker for the Gateway-side work.
- [x] **LVGL touchscreen UI prototype, pre-hardware** (`firmware/lvgl_sim/`) — the actual persona-select + tap-to-talk widget tree (`ui.c`), compiled to WebAssembly via Emscripten and run in a browser, driving the real Gateway WS protocol (audio capture/playback logic ported from `client/virtual_device/app.js` into `bridge.js`). Not the real firmware — a way to review/iterate on the touchscreen UX before hardware exists. Build is fully containerized (`build.sh` uses `emscripten/emsdk:6.0.9`, no Emscripten install on the host); output is a single static `build/index.html` (`SINGLE_FILE=1`, WASM inlined) plus `bridge.js`/`pcm-worklet.js`, servable with any static file server. LVGL pinned at `v9.5.0` via git submodule (`firmware/lvgl_sim/lvgl`). Verified: builds clean, all exported C↔JS bridge functions (`sim_set_status`, `sim_set_personas`, `sim_set_transcript`, `sim_set_llm_text`) present in the output and wired to real button callbacks.
