# Switchboard — Design & Development Plan

> **Status:** Draft v5 (2026-09-11). **All major decisions are now locked, and M0 is fully confirmed — LLM/tool path and Wyoming v2 both validated end-to-end.** LLM/tool path = **Option C (Open WebUI WebSocket session)**, validated against the live OWUI v0.11.3 instance (real tool call + correct final answer via `scripts/m0_owui_spike.py`; full schema in `docs/protocol.md`). Wyoming v2 confirmed working for both Whisper (`faster-whisper`) and Piper via `scripts/wyoming_test.py`. Interaction model = **touchscreen persona select + push-to-talk (no wake word in v1)**. Environment: OWUI **v0.11.3**, `ENABLE_PLUGINS=true`. Next: **M1** (Gateway skeleton).
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
| **Scope (v1)** | Single device, **one active conversation at a time**, trusted LAN, **no auth token**. |
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
  firmware/               # Phase 3 — not started (PlatformIO or ESP-IDF; no hardware yet)
  README.md
```

---

## 7. WebSocket protocol (ESP32 ↔ Gateway)

Binary frames for audio, JSON frames for control/text.

**Client → Gateway**
- `{"type":"select_persona","id":"<persona_id>"}` — sent when the user taps a persona on the touchscreen menu.
- `{"type":"talk_start"}` — tap-to-talk: opens capture. `{"type":"talk_end"}` is now optional/client-dependent — the Gateway's VAD (or the `vad_max_utterance_s` cap) is what actually cuts the utterance; `client/virtual_device/` never sends it at all in the tap-to-talk UI, while `client/cli_client.py` still sends it explicitly (as a hint/cancel-safety with a grace period, not the sole end-of-turn signal) for scripted, non-interactive testing.
- `<binary>` — raw PCM 16-bit / 16 kHz / mono chunks
- `{"type":"cancel"}` — abort the current turn. Only effective during capture (before the LLM/TTS turn starts) — see design.md open items on the message-loop sequencing limitation.

**Gateway → Client**
- `{"type":"status","state":"listening|thinking|speaking|done|error","detail":"?"}`
- `{"type":"transcript","text":"<stt result>"}`
- `{"type":"llm_text","text":"<final answer>"}`
- `<binary>` × 1 or more — TTS PCM, **preceded by an `audio_header`** giving `sample_rate`, `channels`, `bits`. Chunked into ≤32KB frames (confirmed necessary: a long answer's raw PCM can run to several MB, exceeding common single-frame WS size limits and closing the connection outright) — the client accumulates frames until `done` rather than assuming exactly one frame.
- `{"type":"done"}`

---

## 8. Milestones (all testable without the ESP32)

| # | Milestone | Exit criteria |
|---|---|---|
| **M0** | **Protocol feasibility spike (Option C)** | Against the **live OWUI v0.11.3**: (a) open the WebSocket chat session the browser uses; (b) send a tool-eligible prompt and confirm OWUI runs the tool **and** returns a final answer (streaming) — capturing the exact frame/event schema; (c) confirm Wyoming v2 Whisper + Piper endpoints respond. **Locks the Option C protocol; de-risks M1/M2.** |
| **M1** | Gateway skeleton | FastAPI + `/ws`; config/profile loading; session state machine; CLI test client (WAV → transcript + text + TTS out) **and** browser-based virtual device (persona select + push-to-talk over the real WS protocol) — both exercise the Gateway with zero hardware. Single LLM call, no tool loop yet. |
| **M2** | Tool loop (Option C) | Working tool execution + ReAct loop over the **OWUI WS session**; ≥ 1 real tool call returns a synthesized final answer (no Gateway-side loop needed). |
| **M3** | End-to-end voice + personas | Full STT → LLM/tools → TTS across two personas; VAD/turn/latency tuning. |
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
- [x] **Interaction model changed from hold-to-talk to tap-to-talk + Cancel**, per user feedback that holding a button while speaking isn't practical: `client/virtual_device/` now has a single tap to start listening (the Gateway's own VAD decides when you've stopped, same mechanism already confirmed working) and a separate Cancel button. **Known limitation**: because the WS message loop (`server/ws.py`) processes messages strictly sequentially, Cancel only actually interrupts a turn during capture — sent during "thinking"/"speaking" it's queued and silently absorbed once the turn naturally finishes, not a real mid-generation interrupt. True barge-in would need `_finish_capture` to run as a cancellable background task instead of blocking the message loop — not implemented, flagged for later.
- [x] **Bug found and fixed right after shipping tap-to-talk**: the talk button appeared stuck disabled/red ("listening") immediately after selecting a persona, before any tap — not actually capturing. Root cause: a naming collision in `app.js` — the server's `status: "listening"` (emitted once, on `select_persona`, meaning "ready for you to talk") and the client's own optimistic local state for "mic is actually open" were both called `"listening"`. The server's status arrived just after `selectPersona()`'s own `updateButtons("ready")` call and overwrote it with the disabled/capturing styling. Fixed by renaming the client-only state to `"capturing"` and treating server status `"listening"` as idle/ready in `updateButtons()`.
- [x] **Markdown stripping added before TTS** (`switchboard/text.py`, `strip_markdown()`) — models return markdown-formatted text (headers, bold, bullets) which a TTS voice previously read out literally. Applied only to the text passed to Piper, not to the `llm_text` sent to the display, in case a future display renders markdown properly.
- [x] `client/virtual_device/` (browser UI) tested against the running Gateway with a real browser + real microphone — worked end-to-end. Found and fixed two real bugs in the process: (1) missing CORS headers, since the virtual device's static-file origin differs from the Gateway's (`main.py` now adds `CORSMiddleware`); (2) `chat_id` alone doesn't continue a conversation thread in OWUI — `parent_id` must also be threaded from the previous turn's assistant message id, or every turn after the first becomes a sibling branch of the first message instead of appending to it. Full writeup in `docs/protocol.md`. Confirmed fixed with a real multi-turn follow-up question test.
- [x] Chats created via the Gateway now get a real title (OWUI's own auto-titling never fired for programmatic chats) — the Gateway sets one itself from the transcript after the first turn. See `docs/protocol.md` Section 2.
- [ ] Worst-case LLM-turn latency is now up to ~215s (35s socket gate + up to 180s REST stable-length poll) for genuinely long, slow-to-generate answers (docs/protocol.md Section 2) — fine as a correctness-first safety net for M1, worth tightening in M3/M6.
- [ ] **Recommend, not yet done**: constrain persona `system_prompt`s to request concise, spoken-style answers (no markdown/headers, short) — a real test question produced a 4109-character essay that synthesized to 4.7 minutes of audio. All M1 fixes make this pipeline technically correct end-to-end, but that response length is a bad fit for voice regardless of correctness.
- [x] uvicorn's default `--ws-ping-interval`/`--ws-ping-timeout` (20s/20s = 40s combined) were closing WS connections mid-turn on any wait longer than 40s — root cause of a real disconnect found via the virtual device (confirmed via exact `40.00s` connection duration in DevTools). Fixed: both the dev run command and `Dockerfile`'s `CMD` now pass `--ws-ping-interval 120 --ws-ping-timeout 120`.
- [x] Startup made resilient to OWUI being momentarily unreachable — `main.py`'s lifespan no longer fails hard if the eager connect fails; `OwuiClient` connects lazily on first real use otherwise. See docs/protocol.md Section 2.
- [ ] Firmware: decide **PlatformIO vs ESP-IDF** when M4 starts.
- [ ] Pick a touch-capable display/controller (e.g., resistive XPT2046 vs. capacitive) when hardware is purchased — deferred to hardware research, not a v1 blocker for the Gateway-side work.
