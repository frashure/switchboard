# Switchboard

A voice assistant for the desk: pick a persona on a touchscreen, tap to talk, and
hear the answer. Each persona is a model configured in [Open WebUI](https://github.com/open-webui/open-webui)
(system prompt, tools, knowledge, avatar), so everything you set up there works
by voice.

A thin client (the web app in a browser or on a tablet today, an ESP32 touchscreen
possibly later) captures audio and renders the UI. A Python **Gateway** does the rest: detects the end of
speech, transcribes it, runs the turn through Open WebUI, and speaks the answer.

```
 Client (web app / ESP32)                Gateway (FastAPI)                 Services
┌────────────────────────────┐   WS    ┌──────────────────────┐   ┌──────────────────────────┐
│ persona cards, tap-to-talk │◄───────►│ VAD (Silero)         │──►│ Whisper  (Wyoming STT)   │
│ mic capture, TTS playback  │  audio  │ session state machine│──►│ Piper    (Wyoming TTS)   │
│ transcript / response text │  + JSON │ persona discovery    │──►│ Open WebUI (LLM + tools) │
└────────────────────────────┘         └──────────────────────┘   └──────────────────────────┘
```

**Status:** the Gateway and the web app work end to end against live services.
ESP32 firmware has not been started (no hardware yet); an old tablet running the
web app is the current path to a real device. See
[`docs/design.md`](docs/design.md) for decisions, milestones and open items.

## Features

- **Personas come from Open WebUI.** Models marked as presets are discovered at
  runtime (refreshed every 5 minutes), including their avatars. Only the Piper
  voice per persona lives here, in `gateway/config/voices.yaml`.
- **Tap to talk, Cancel anywhere.** One tap starts listening; the Gateway's VAD
  decides when you stopped. Cancel interrupts speech capture, STT, a slow LLM/tool
  turn, or audio already playing.
- **Full Open WebUI tool use.** Turns run through Open WebUI's own chat pipeline,
  so tools, knowledge bases and memory behave as in its web UI. Multi-turn
  conversations continue in the same chat thread and show up in Open WebUI.
- **Speech-friendly output.** Markdown is stripped before text-to-speech; the raw
  text is still sent to the screen.
- **Streaming speech.** The answer is spoken sentence by sentence as it is
  synthesized, so audio starts within about a second regardless of answer length.
  Tap-to-stop interrupts it at any point.

## Repository layout

| Path | What it is |
|---|---|
| `gateway/` | The Python service (`switchboard/`), config, Dockerfile and compose stack |
| `web/` | **The web app** (Svelte PWA): persona picker, animated session screen, streaming playback. See [`web/README.md`](web/README.md) |
| `gateway/client/cli_client.py` | Feed WAV files through the real `/ws` endpoint, no hardware or browser |
| `gateway/client/virtual_device/` | Minimal plain-HTML client, kept as a protocol reference |
| `services/chatterbox/` | Optional higher-quality TTS service ([Chatterbox-Turbo](https://github.com/resemble-ai/chatterbox)) the Gateway can prefer over Piper |
| `scripts/chatterbox_bench/` | Benchmark used to evaluate Chatterbox on the server's GPU (speed, VRAM, streaming stalls) |
| `firmware/lvgl_sim/` | The UI as LVGL widgets compiled to WebAssembly: a prototype for future embedded (ESP32) devices, not the primary client |
| `docs/design.md` | Architecture, locked decisions, milestones, open items |
| `docs/protocol.md` | WebSocket protocol and the Open WebUI protocol findings |
| `scripts/` | Early protocol spikes (Open WebUI chat protocol, Wyoming) |

## Requirements

- Open WebUI (developed against v0.11.3) with at least one preset model
- A Wyoming **Whisper** (STT) and **Piper** (TTS) service, reachable over TCP
  (ports 10300 and 10200 by default)
- Python 3.12 or newer, or just Docker
- A browser with microphone access for the clients. Browsers only allow the mic
  on HTTPS or `localhost`, which is why the deployment below fronts the Gateway
  with HTTPS.

## Quick start (development)

```bash
git clone --recurse-submodules <repo-url> && cd switchboard
python3 -m venv .venv && source .venv/bin/activate
pip install -e gateway
```

Create `gateway/.env`:

```bash
SWITCHBOARD_OWUI_BASE_URL=https://your-open-webui
SWITCHBOARD_OWUI_EMAIL=you@example.com
SWITCHBOARD_OWUI_PASSWORD='your password'
SWITCHBOARD_WHISPER_URI=tcp://localhost:10300
SWITCHBOARD_PIPER_URI=tcp://localhost:10200
```

If Whisper and Piper run on another machine, forward their ports first, e.g.
`ssh -N -L 10300:localhost:10300 -L 10200:localhost:10200 user@server`.

Start the Gateway. Keep the `--ws-ping-*` flags: uvicorn's 40s default closes the
WebSocket during long tool-heavy turns.

```bash
cd gateway
python -m uvicorn switchboard.main:app --host 0.0.0.0 --port 8090 \
  --ws-ping-interval 120 --ws-ping-timeout 120
curl localhost:8090/profiles      # should list your personas
```

Then run the web app with hot reload (needs Node 22+):

```bash
cd web && npm install && npm run dev
#   -> http://localhost:5173  (proxies /profiles and /ws to the Gateway on :8090)
```

Other clients:

```bash
# Plain-HTML client
python -m http.server 8001 --directory gateway/client/virtual_device
#   -> http://localhost:8001/?gateway=localhost:8090

# LVGL client (builds in a container; nothing to install on the host)
firmware/lvgl_sim/build.sh
python -m http.server 8002 --directory firmware/lvgl_sim/build
#   -> http://localhost:8002/?gateway=localhost:8090

# No browser: push WAV files through the pipeline
GATEWAY_WS=ws://localhost:8090/ws python gateway/client/cli_client.py <persona_id> question.wav
```

## Deployment (Docker + Tailscale)

`gateway/docker-compose.yaml` runs the Gateway on the same Docker network as Open
WebUI, Whisper and Piper (reached by container name) behind a Tailscale sidecar
that provides HTTPS. The image also builds the web app and serves it from the
Gateway, so the whole client is a single `https://switchboard.<tailnet>.ts.net` URL
(the PWA at `/`, the LVGL prototype at `/lvgl/`, the plain client at `/virtual_device/`).

1. `git submodule update --init`
2. `cp gateway/.env.example gateway/.env` and fill it in (Docker network name,
   container names, Open WebUI login, Tailscale auth key)
3. In the Tailscale admin console, enable MagicDNS and HTTPS certificates
4. `cd gateway && docker compose up -d --build`

Wyoming services are plain TCP (`tcp://wyoming-whisper:10300`), not HTTP.

### Using a tablet as the device

Any tablet with a modern browser works, including an old Fire HD 8 without
replacing its OS. Sideload the Tailscale app and a Chromium-based browser (or
Fully Kiosk Browser), open the URL above, allow microphone access, and use
"Add to Home screen" / install: the app is a PWA, so it then launches fullscreen
and keeps the screen awake. It reconnects on its own after network drops and
restores the selected persona.

### Optional: Chatterbox voices

Chatterbox sounds considerably more natural than Piper and can give each persona
its own cloned voice. It runs as a separate GPU container and is entirely optional:
the Gateway prefers it when it reports ready and silently uses Piper otherwise
(while it warms up, if it is down, or if it fails on an answer's first chunk).

```bash
# in gateway/.env:  TTS_BACKEND=chatterbox   (and VIDEO_GID / RENDER_GID for GPU access)
docker compose --profile chatterbox up -d --build
curl localhost:8000/health        # from the host, or check the container logs
```

- **First start** downloads the model and pre-tunes ~20 GPU shape buckets, which takes a few
  minutes; the result is cached in volumes, so later restarts are quick. Until `/health`
  reports `ready`, answers are spoken by Piper.
- **Voices:** drop a ~10 s clean reference clip at `services/chatterbox/voices/<name>.wav`
  and map a persona to it under `chatterbox:` in `gateway/config/voices.yaml`. Without one a
  persona uses the model's built-in voice. Only clone voices you have the right to use; output
  carries Resemble's inaudible watermark.
- **AMD (ROCm) vs NVIDIA:** the image defaults to ROCm 7.2 PyTorch wheels (tested on a Radeon AI PRO
  R9700); set `CHATTERBOX_TORCH_INDEX` to a `cu12x` index and use `gpus: all` for NVIDIA.
- Measured on the R9700: ~2.7x real time with a ~2.1 s first chunk and ~3.4 GB VRAM. Why the shape
  bucketing and warm-up exist is in `docs/design.md`.

### Signing in

By default (`AUTH_MODE=owui`) people sign in with their **Open WebUI account** and see only the
models that account may use; their chats, memory and tools are their own. Create a dedicated
Open WebUI account for a shared tablet and grant it just the models you want there.

- Set `SESSION_SECRET` (`openssl rand -hex 32`) in `gateway/.env`, otherwise everyone is signed out
  whenever the Gateway restarts. Sessions last as long as the Open WebUI token (28 days by default),
  so a tablet asks for the password about once a month.
- `AUTH_MODE=none` turns login off and runs everything as the single account in `.env` (the original
  behaviour). The plain-HTML and LVGL clients and `cli_client.py` can't sign in on their own: the CLI
  accepts `GATEWAY_EMAIL` / `GATEWAY_PASSWORD`; the other two only work with `AUTH_MODE=none`.
- The Gateway forwards your password to Open WebUI once at sign-in and keeps only the resulting token,
  in an encrypted HttpOnly cookie. Run it behind HTTPS (the Tailscale setup above does).

## Configuration

Settings are `SWITCHBOARD_*` environment variables or entries in `gateway/.env`
(see `gateway/switchboard/config.py` for the full list and defaults):

| Variable | Purpose |
|---|---|
| `OWUI_BASE_URL`, `OWUI_EMAIL`, `OWUI_PASSWORD` | Open WebUI address and login |
| `WHISPER_URI`, `PIPER_URI` | Wyoming endpoints |
| `STATIC_DIR` | Directory of web client files to serve at `/` (set in the Docker image) |
| `LLM_REST_POLL_TIMEOUT` | How long to wait for a finished answer, default 480s |
| `VAD_PAUSE_THRESHOLD_MS` | Silence that ends an utterance, default 1200 |
| `AUTH_MODE` | `owui` (sign in with Open WebUI accounts, default) or `none` |
| `SESSION_SECRET` | Encrypts the login cookie so logins survive restarts |
| `TTS_BACKEND` | `piper` (default) or `chatterbox` (Piper remains the fallback) |
| `CHATTERBOX_URL` | Chatterbox service address (`http://chatterbox:8000` in the compose stack) |

## Documentation

- [`docs/design.md`](docs/design.md): architecture, decisions, milestones, open items
- [`docs/protocol.md`](docs/protocol.md): client WebSocket protocol and Open WebUI behaviour
  that is not obvious from its docs

## Known limitations

- Single device, one conversation at a time, trusted network, no authentication.
- Cancelling stops the Gateway from waiting on Open WebUI, but Open WebUI may
  keep generating in the background.
- The compose stack and the on-tablet experience (touch input, speaker-to-mic
  echo) have not been exercised on real hardware yet.
