1. What's already deployed (blocking)
- **Q1. What's running on the server right now, and where?** OpenWebUI, Ollama, Wyoming Piper, Wyoming Whisper are all running as Docker containers on the Ubuntu Server 24.04 unit, which is powered by the R9700 GPU. Home Assistant should not be factored in, although it is also running, but it's integration with Open WebUI is too limited.
- **Q2. Should the Gateway be a Docker container on the same host, with the ESP32 on the same LAN? ** Yes, it should be a container. The ESP32 will be on the same LAN.

## 2. The LLM & tool-calling path (most important)
- **Q3. What are the "tools" the ReAct loop executes?** Most of the tools are either native to Open WebUI or custom Python tools that call other services (generally via the API for that service).

- **Q4. What's Open WebUI's actual role?** Two clean options:
  - **A)** Gateway calls Open WebUI's OpenAI-compatible chat-completions API for each LLM turn and *the Gateway runs the ReAct loop* (tool execution + feeding results back).
  - **B)** Gateway talks **directly to Ollama** (native `tools` + streaming), and Open WebUI is just your human UI.
  → *My honest read: (B) is simpler and lower-latency for tool-calling/ReAct; (A) only makes sense if you rely on Open WebUI-specific features (RAG, plugins, saved prompts). Tell me which value you're getting from Open WebUI and I'll design to it.*
  - Open WebUI is where workspace models, tools, and memory are managed. Bypassing that and linking directly to Ollama removes those benefits.

- **Q5.** Token streaming to the device screen — required for v1, or a "nice-to-have" we can defer?
  - I want the output sent to the device as some of it will be lengthy text the user should be able to review. It does not need to be streamed real-time, but the final output should be sent and viewable at least after the voice data is read aloud.

## 3. Audio pipeline & endpointing
- **Q6. How does a turn end** (when you stop talking)? Options: VAD on the ESP32 (device sends a bounded clip) vs **continuous streaming + Gateway-side VAD/endpointing** vs fixed-length capture. → *I recommend continuous streaming + Gateway VAD — it's the most robust for streaming STT and keeps the device dumb.*
- I'm not sure. Can you give me more details?

- **Q7. Formats:** Whisper input = 16-bit mono **16 kHz** (Wyoming standard) — fine? For Piper, **medium (22050 Hz)** or **low (16000 Hz)**? The ESP32 playback rate must match Piper's, so this is a real constraint.
- I'm not sure. Can you give me more details?

- **Q8.** Which **Wyoming protocol version** are your containers serving — **v1 or v2**? → *Recommend v2 (newer, JSON headers).*
- I'm not sure. Probably safe to assume v2.

## 4. Firmware / hardware
- **Q9. Form factor:** **ESP-S3-BOX-3** (onboard mic + speaker + display — least wiring) vs **bare DevKitC-1 + INMP441 + MAX98357A + SPI display** (more control). This heavily shapes the firmware. Do you have either yet?
- I do not have the hardware currently.

- **Q10. Wake words:** You list *"Hey Jarvis"* and *"Hey Einstein."* Do you want **both active simultaneously** (device detects *which* one → picks persona)? That's the clean UX but the heaviest on the S3. I'd use **OpenWakeWord** for custom wake words (not ESP-SR). Or a single wake word + "who do you want?" follow-up?
- I want all configured wake words to be active simultaneously.

- **Q11. Firmware toolchain:** **PlatformIO** (fast prototyping) vs **ESP-IDF** (more control, better for LVGL + heavy audio)?
- No preference. 

- **Q12. Display priority:** It's not purchased yet. Build **audio-only first** (mic in → spoken answer out) and add LVGL later, or is the screen central to v1?
- Iterative development with audio first is fine, as along as we architect everything in a way that makes it easy to add the screen data later.

## 5. Project logistics
- **Q13.** Scope v1 as **single device, one active conversation at a time, trusted LAN, no auth token**? → *I recommend yes.*
- Yes, at least for now.

- **Q14. First milestone:** Given the hardware isn't bought yet, shall we start with the **Gateway (Python) + a simple test client** (CLI or browser) to prove the full STT→LLM→TTS→audio loop server-side, and treat **ESP32 firmware as a later phase**? → *I strongly recommend this.*
- Yes

- **Q15.** Any constraints on the Gateway runtime — **Python 3.11/3.12**, FastAPI + uvicorn + pydantic?
- Whatever is best/most efficient.
