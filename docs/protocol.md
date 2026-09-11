# Switchboard — Protocol Notes

> **M0: CONFIRMED WORKING END-TO-END** (2026-09-10) — `scripts/m0_owui_spike.py` successfully drove a real tool call (`openweathermap_forecast`) through Open WebUI **v0.11.3** from a plain Python client and received the correct final answer ("It's currently 103°F in Phoenix with scattered clouds..."). Supersedes speculation in design.md Section 5 about the exact frame schema — the mechanism is fully confirmed, not just "the closest path to the browser." Proceed to M1.

## 1. Open WebUI chat / tool-loop protocol (Option C)

### Transport
- **Auth:** JWT bearer token (normal login; exact login endpoint assumed `POST /api/v1/auths/signin`, not yet directly captured/tested).
- **Realtime:** Socket.IO (Engine.IO v4) at `wss://<host>/ws/socket.io/?EIO=4&transport=websocket`.
- **Completion kickoff:** `POST /api/chat/completions` (REST). **This does not stream the answer.**

### Confirmed socket handshake sequence (from a real browser capture)
1. Engine.IO `OPEN` (`0{"sid":"<engine.io sid>", "pingTimeout":20000, "pingInterval":25000, ...}`).
2. Socket.IO `CONNECT` (`40{"token":"<JWT>"}`) — the bearer token rides in the CONNECT packet payload. This matches passing `auth={"token": token}` to a socket.io client library (confirmed correct).
3. Server acks with its own Socket.IO-level sid (`40{"sid":"<socket.io sid>"}`) — **note this is a different value from the Engine.IO sid in step 1.** This is the sid a client library's `.sid` property exposes, and the one that should be used as `session_id` in the completions payload.
4. **Client then explicitly emits `user-join`: `42["user-join",{"auth":{"token":"<JWT>"}}]`.** This step is easy to miss (it's a separate app-level event, not part of the Engine.IO/Socket.IO handshake) but appears to be **required** — without it, the socket is likely not subscribed to whatever room/scope the server uses to deliver this user's own chat events, which is the suspected cause of an early spike run receiving an unrelated chat's completion event instead of its own (see "Open questions" below — not yet 100% confirmed, but strongly implicated: server logs confirmed `generate_chat_completion` ran cleanly for that request, meaning the backend did the work; the miss was on the delivery/subscription side).
5. Ordinary event traffic afterward is a mix of `events` (generic channel: `chat:title`, `chat:completion`, `status`, `chat:list`, ...) and a second, narrower `events:chat` channel (seen carrying `last_read_at` read-receipt-style updates) — `events` is the one that matters for the completion loop.

### Turn sequence
1. Client connects Socket.IO, obtains its Engine.IO **`sid`**.
2. Client `POST /api/chat/completions` with a body including at least:
   - `stream: true`
   - `model`: model id
   - `session_id`: the Socket.IO **`sid`** from step 1 — this is what ties the REST call to the socket connection. Confirmed via capture (`session_id` value matched the connection's `sid` format).
   - `chat_id`: existing chat id, or omit to let the server create a new one (returned in the ack)
   - `id` / `message_ids` / `parent_id` / `user_message`: message-tree bookkeeping — **OWUI chats are a tree of messages, not a flat `messages: []` array.** The user's message and the assistant's (not-yet-generated) reply are **two distinct, pre-linked tree nodes**: top-level `id` and `message_ids[].message_id` are the **assistant's** future message id; `user_message.id` is a **different** id for the user's own message; `user_message.childrenIds` pre-links to that assistant id; `parent_id` (top-level, mirrored as `user_message.parentId`) is the id of whichever message preceded this exchange (presumably `null` for the very first message of a new chat — worth confirming, see below). A client that collapses these into one shared id (as an early version of the spike script did) produces a malformed tree — suspected to cause the backend to fall back to a generic response instead of answering the actual prompt (see the "Fixed" section below).
   - `tool_ids`: array of enabled tool ids for this model
   - `features`, `variables`, `model_item`, `background_tasks`: additional frontend context observed in the capture; how much is strictly required vs. safely omittable is **untested**.
3. REST response is just an ack, no SSE body:
   ```json
   {"status": true, "task_ids": ["..."], "chat_id": "..."}
   ```
4. **All actual generation streams over the Socket.IO connection**, as Socket.IO `"events"` frames (raw wire form `42["events", {...}]`), each shaped:
   ```json
   {"chat_id": "...", "message_id": "...", "data": {"type": "<subtype>", "data": {...}}}
   ```
   **⚠️ Confirmed the hard way:** these frames are delivered broadly for the authenticated user/session, not scoped to the REST call that opened them — background tasks and other/lingering chats' events show up on the same socket. **The client must filter every frame by `chat_id == ack.chat_id` itself**; the server does not scope this for you. A first spike run without this filter picked up an unrelated chat's completion.

   Observed subtypes, in order, for a tool-calling turn:
   | `data.type` | Meaning |
   |---|---|
   | `chat:title` | background auto-title generation |
   | `response:completion` | raw pass-through of the underlying model runtime's native streaming format — matches OpenAI **Responses API** event shapes (`response.output_item.added`, `response.function_call_arguments.delta`/`.done`, `response.output_text.delta`, ...) |
   | `chat:completion` | OWUI's own normalized turn state, emitted at multiple points, not just the end |
   | `status` | human-readable progress string + `done` flag, e.g. `"Fetching weather for Washington DC...", done:false` → `"Weather data loaded!", done:true` — good candidate for surfacing to the device screen as a "thinking" indicator |
   | `heartbeat` | app-level keepalive, separate from Engine.IO's own ping/pong (`2`/`3`) frames |

5. **Terminal event:** `chat:completion` where `data.data.done === true`. Its `data.data.output` array contains the full turn's output items in order — `function_call`, `function_call_output` (the tool result, delivered as an already-natural-language string for the model to read, not raw JSON), and finally:
   ```json
   {"type": "message", "id": "...", "status": "completed", "role": "assistant",
    "content": [{"type": "output_text", "text": "<final answer>"}]}
   ```
   **This one event carries the entire final answer, already assembled.** A client that doesn't need live token streaming (ours doesn't for v1, per design.md Section 2) can ignore every other event type and just wait for this one, then read `content[0].text` off the `message`-type item.

   `data.data.usage` on this same event carries full generation telemetry (`response_token/s`, `total_duration`, `prompt_eval_duration`, etc.) — useful for M6 latency tuning for free.

### Gateway algorithm (confirmed working end-to-end)
1. Connect Socket.IO → get its Socket.IO-level `sid`.
2. Emit `user-join` with `{"auth": {"token": <same JWT>}}` — **required**, confirmed by testing (omitting it caused events from an unrelated chat to arrive instead of the real answer).
3. `POST /api/chat/completions` with `session_id=sid`, a **correctly-structured message tree** (distinct user/assistant message ids — see below, this was the second bug found), and the resolved profile's `model` / system prompt / `tool_ids`.
4. Listen for `"events"` frames matching the returned `chat_id`.
5. On `data.type == "chat:completion" && data.data.done == true` → find the `message`-type item in `data.data.output`, read `content[0].text`. That's the final LLM text → screen + Piper.
6. Disconnect (or keep the socket open and reuse it for the next turn/persona in the same device session — worth trying once basic turns work).

**Two bugs found and fixed during the spike, both worth remembering when this logic gets ported into the real Gateway (`gateway/switchboard/llm/owui_client.py`):**
- Forgetting the `user-join` emit → socket receives events not scoped to your own request.
- Collapsing the user message and the assistant's (pre-assigned) message id into a single shared id, with empty `childrenIds` → backend produced a generic system-prompt/memory-acknowledgment response instead of answering the actual prompt, consistently, with no error. This is a dangerous failure mode because it fails *silently and plausibly* rather than erroring — worth a sanity check in the real client (e.g. flag/log if the returned answer looks suspiciously like a canned response) rather than assuming any 200 + final-text means success.

### Resolved during the spike
- [x] Socket.IO auth mechanism: bearer token in the CONNECT packet payload (`auth={"token": ...}`), confirmed by browser capture.
- [x] `user-join` emit required after connecting — confirmed necessary (its absence caused unrelated-chat events to arrive instead of the real answer).
- [x] Message-tree shape for the first message of a brand-new chat (`chat_id` omitted): `parent_id`/`user_message.parentId` = `null`; user message and assistant message need **distinct** ids, with `user_message.childrenIds` pre-linking to the assistant's id. Confirmed working — a malformed/collapsed version of this tree caused a silent generic-response failure mode (see above), not an error.
- [x] End-to-end: a real tool-eligible prompt (`openweathermap_forecast`) correctly executed the tool and returned the right final answer, driven entirely by a non-browser Python client.

### Still open (not blocking M1, revisit opportunistically)
- [ ] Exact login endpoint — assumed `POST /api/v1/auths/signin` and it worked, but wasn't itself captured from the browser; low risk, already validated empirically by the script.
- [ ] Minimal required fields for `/api/chat/completions` — the spike sends less than the full browser payload (no `model_item`/`features`/`variables`) and it worked, but which fields are truly load-bearing vs. coincidentally fine to omit for this persona/model isn't fully mapped.
- [ ] Terminal-event shape for a turn with **no** tool call (expect the same `chat:completion`/`done:true` shape minus the `function_call`/`function_call_output` items — plausible but unverified).
- [ ] `GET /api/tasks/chat/{chat_id}` as a reconnect/resume mechanism if the socket drops mid-generation — worth testing deliberately for the error-handling design (design.md open item).
- [x] Design implication decided in M1: `Session` reuses one persistent `chat_id` per persona across turns (reset on persona switch), matching the multi-turn design in design.md Section 4.
- [x] **Bug found via real browser testing (virtual device) and fixed**: `chat_id` alone is not enough to continue a conversation thread. `send_turn` also needs `parent_id` set to the *previous turn's assistant message id* — omitting it (as an earlier version did) makes OWUI treat every subsequent turn as a **sibling edit/branch of the first message** ("4/4" in the OWUI UI) rather than appending to the thread, even though `chat_id` was correctly reused. `send_turn` now returns `assistant_message_id` alongside `final_text`/`chat_id`, and `Session` threads it through as the next call's `parent_id`. Confirmed fixed with a real two-turn test ("Who is the 27th president?" → "What state was he from?" correctly resolved "he" using conversation context) and verified via REST that the resulting OWUI chat is a proper linear 4-message thread (no branching).
- [ ] Housekeeping: earlier spike runs left a couple of stray test chats in the live OWUI instance — fine to delete once convenient; consider a throwaway test account/model for future spikes against this instance.

## 2. M1 implementation findings: Socket.IO delivery reliability

Building `gateway/switchboard/llm/owui_client.py` against the confirmed protocol surfaced a real reliability issue that the M0 spike (single manual runs) never hit:

- **Two implementation bugs found while porting the spike into reusable code** (both fixed): (1) `send_turn`'s `httpx.post()` call wasn't wrapped in `asyncio.to_thread`, blocking the event loop during the POST; (2) `EndOfSpeechDetector.process()` (silero VAD) ran synchronous CPU-bound inference directly on the event loop for every ~100ms audio chunk during CAPTURE, cumulatively starving the Socket.IO client's background task for the whole capture duration. Both are now off-thread.
- **A shared, app-lifetime Socket.IO connection reused across many turns was unreliable** — intermittently failed to deliver the terminal event on later turns. Switched to opening a **fresh Socket.IO connection per turn** (`connect()`/`disconnect()` per `send_turn()` call); the login token is still cached at app lifetime since that's a stateless REST call.
- **Even with a fresh connection per turn, delivery is still intermittently flaky** (confirmed via `docker logs` on the OWUI container during a live failure): **the OWUI backend always completes generation successfully** — the failure is 100% on `python-socketio`'s/`engineio`'s async client occasionally failing to deliver the terminal `chat:completion`/`done:true` frame to our handler. This looks like a library-level reliability issue, not an application bug on either side; not investigated further at the library-internals level.
- **Fix: REST-based recovery, not blind retry.** A naive retry-on-timeout would create a *second* chat/response every time this fires (confirmed: OWUI had already generated a real answer in the "failed" chat every time we checked) — doubling clutter without fixing anything. Instead, `send_turn` falls back to polling `GET /api/v1/chats/{chat_id}` (reading `chat.history.messages[chat.history.currentId].content`) for up to 60s when the socket wait times out, recovering the already-generated answer. Logged at `WARNING` when this fires, for operational visibility. Confirmed working (reproduced the failure, confirmed the fallback found and returned the real answer).
- **Worst-case turn latency is ~95s** (35s socket wait + 60s REST-fallback poll) on the rare turns that hit this path — down from an initial 120s (60s+60s); `llm_timeout` lowered to 35s since real successful direct-delivery turns have consistently finished in 15-35s, so 60s was mostly just delaying the fallback unnecessarily.
- **Real disconnect found via the virtual device**: a turn that needed the REST fallback took long enough (>60s with zero WS traffic) that the browser's WebSocket was found disconnected by the time the answer was ready, even though generation succeeded server-side. Added a heartbeat (`Session._finish_capture` re-emits a `thinking` status every 15s while waiting on `send_turn`) to keep traffic flowing during long waits.
- **The heartbeat alone didn't stop the disconnect** — recurred even with the tab kept in the foreground (ruling out browser tab-backgrounding). **Root cause confirmed**: Chrome DevTools showed the WS connection lasted exactly `40.00s` — uvicorn's default `--ws-ping-interval`/`--ws-ping-timeout` are both 20s, and a missed pong closes the connection after exactly `20+20=40s`. This is a protocol-level keepalive our app-level JSON heartbeat never touches. **Fixed**: both the dev run command and the Dockerfile's `CMD` now pass `--ws-ping-interval 120 --ws-ping-timeout 120`, comfortably clearing the worst-case turn latency.
- **Fixed the resulting crash regardless of root cause**: `WebSocketEmitter` (`server/ws.py`) previously let `send()` failures propagate as unhandled exceptions, crashing the whole connection handler with an ugly traceback. Now catches and logs instead — there's nothing meaningful to do once the client is gone, so this just fails gracefully. This was a real gap: the WS handler only ever handled the *receive*-side disconnect (`websocket.disconnect` message type), never a send-side failure.
- **Duplicate `talk_end` bug found alongside this**: the virtual device's talk button wires both `pointerup` and `pointerleave` to `stopTalking()` — if the pointer left the button right as it was released, both fired, sending `talk_end` twice ~0.5s apart. Fixed with a `talking` boolean guard in `app.js`.
- **Startup flakiness found and made resilient**: the OWUI login call intermittently fails with a transient connection error specifically during process startup in this dev environment (10 retries at 3s apart sometimes still not enough), even though the identical request succeeds instantly as a standalone script and the endpoint is reachable via curl moments before/after. Root cause not pinned down (environment-specific, not an application bug). Rather than keep fighting the exact timing window, made the app resilient to it instead: `main.py`'s lifespan no longer fails startup if the eager `owui.connect()` fails (logs and continues); `OwuiClient.send_turn()` now connects lazily on first real use if `connect()` was never called successfully. The Gateway can now start and serve `/profiles` even if OWUI is momentarily unreachable at the exact instant of process launch.
- **Real truncation bug found and fixed, twice**: a long (4000+ character) answer was arriving at the client silently cut off mid-sentence, confirmed via REST that OWUI's own stored copy had the complete text. First hypothesis (indexing `content[0]` of a multi-segment content list) was *not* the actual cause — joining all segments didn't fix it. **Actual root cause**: multiple `chat:completion`/`done:true` events fire per turn, and taking the *first* one's embedded text (via `queue.get()`) sometimes captured an interim/partial answer, not the true final one. **Fix — a bigger architectural change**: `send_turn` no longer trusts any embedded text from socket events at all. The socket is now only used as a rough "has generation started" gate; the actual final text always comes from polling `GET /api/v1/chats/{chat_id}` until the assistant message's text length is *stable across two consecutive checks* (a still-generating answer keeps growing) — up to 90 attempts × 2s = 180s budget, since long essay-style answers have been observed genuinely still growing past 60s of real generation time.
- **Audio-frame-too-big bug found and fixed**: sending a long answer's full synthesized PCM as one WebSocket frame hit the `websockets` library's default 1MB frame-size limit (`ConnectionClosedError: sent 1009 (message too big) frame with 3370948 bytes exceeds limit of 1048576 bytes`) and closed the connection outright — a real ESP32 client couldn't handle a multi-MB single frame either way. **Fixed**: `WebSocketEmitter.audio()` (`server/ws.py`) now chunks PCM into ≤32KB frames; both `app.js` and `cli_client.py` accumulate frames until `done` rather than assuming exactly one frame follows `audio_header`. `design.md` Section 7 updated to reflect this.
- **Product-level finding, not a bug**: a single long-form question produced a **4109-character, markdown-formatted, multi-section essay** answer, which synthesized to **284.8 seconds (4.7 minutes) of audio**. All of the above fixes make this pipeline technically correct end-to-end, but a nearly 5-minute spoken answer to one question is a bad fit for a voice interface regardless. Recommend addressing this via persona `system_prompt` (ask for concise, spoken-style answers, no markdown/headers) rather than continuing to extend timeouts to accommodate arbitrarily long generations — not yet done, flagged for follow-up.
- **Separate finding, not blocking v1 (single-conversation-at-a-time scope):** running **4 concurrent** `send_turn()` calls from one process resulted in only 2 of the 4 Socket.IO connections ever completing their handshake — the other 2 appear to hang before even reaching `user-join`/the POST. Concurrent turns are not part of v1's design (design.md Section 2: "one active conversation at a time"), so this wasn't pursued further, but avoid assuming this path is safe if that scope ever changes.
- **Chat titles:** OWUI's own auto-titling (`chat:title` socket event) never fired for any of our programmatically-created chats, only ones driven from a real browser tab — root cause not tracked down (not worth the effort for a cosmetic issue). Fixed by having the Gateway set its own title after the first turn of a new conversation: `OwuiClient.set_chat_title()` does `GET` then `POST /api/v1/chats/{chat_id}` with the full chat object (only `title` changed) — confirmed working. `Session._finish_capture` fires this as a background task (doesn't block the turn) using the transcript truncated to 50 chars as the title. **Side effect:** `scripts/cleanup_test_chats.py`'s "New Chat" filter now only catches chats where title-setting itself failed, not the general case of test debris — test chats get real-looking titles now too, same as real usage, so bulk-identifying test debris by title alone no longer works going forward.
- **Housekeeping:** `scripts/cleanup_test_chats.py` deletes OWUI chats still titled the default "New Chat" — dry-run by default, `--yes` to actually delete. Used repeatedly during this debugging session to clean up test debris.
- **⚠️ Caution learned the hard way:** a REST test script targeting "the most recently updated chat" briefly renamed a real chat from the user's own prior usage (`DC Weather Forecast`) instead of a test artifact, since no fresh test chats existed at that exact moment. Reverted immediately, but the lesson stands: any script that mutates a chat_id must use an ID captured directly from its own test run, never inferred by recency/position in a list.

## 3. Wyoming v2 (Whisper / Piper) — CONFIRMED WORKING

Confirmed via `scripts/wyoming_test.py` (2026-09-11) against the running containers:

- **Whisper:** `faster-whisper` program, `medium-int8` model, at `tcp://localhost:10300`.
- **Piper:** `piper` program, wide voice catalog (100+ voices spanning many languages) at `tcp://localhost:10200`.
- **Round trip:** synthesized "The quick brown fox jumps over the lazy dog" with Piper (22050Hz/16-bit/mono, i.e. a `medium` voice), fed that audio straight into Whisper, got back an exact transcript match.

**Notable finding:** Whisper transcribed the 22050Hz audio correctly with **no resampling** — it evidently handles rates other than 16kHz fine (likely resampling internally), softening the "Input = 16-bit/16kHz/mono/PCM (Wyoming standard)" assumption in design.md Section 2. That assumption still stands as the right choice for the **ESP32→Gateway mic path** (16kHz keeps WiFi bandwidth down, independent of what Whisper can technically accept), but the Gateway doesn't need to be defensive about exact sample-rate matching when talking to Whisper.

No further Wyoming-side spike work needed before M1 — `stt/whisper.py` and `tts/piper.py` can be built directly against the `wyoming` Python package's `AsyncTcpClient` using the same event sequence as the spike script (`Transcribe`/`AudioStart`/`AudioChunk`/`AudioStop` → `Transcript` for STT; `Synthesize` → `AudioStart`/`AudioChunk`/`AudioStop` for TTS).

## 4. WebSocket protocol (ESP32 ↔ Gateway)

See design.md Section 7 — unaffected by the above; this section will be filled in once M1's `/ws` endpoint is implemented and exercised by the CLI client / virtual device.
