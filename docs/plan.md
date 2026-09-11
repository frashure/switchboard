# Project: AI Voice Assistant Device (High-Performance Architecture)

## Project Overview

A professional-grade, voice-activated AI appliance using an ESP32-S3 as a thin client and a custom Python-based AI Gateway to coordinate between a powerful GPU server and the hardware. This design prioritizes low latency, streaming text, and full support for LLM tool-calling.

## 1. Hardware Specifications (Thin Client)

*   **Controller:** ESP32-S3 (DevKitC-1 or ESP-S3-BOX).
    
*   **Audio Input:** INMP441 I2S Microphone (Streaming raw audio).
    
*   **Audio Output:** MAX98357A I2S Amp + Speaker.
    
*   **Display:** ILI9341 or ST7789 SPI Display.
    
*   **GUI Framework:** LVGL (Light and Versatile Graphics Library) for smooth text scrolling and persona indicators.
    

## 2. Software Architecture: The AI Gateway

Since Home Assistant lacks native WebSocket support for Open WebUI tool-calling, a custom **AI Gateway** is deployed as a Docker container on the server (AMD Radeon R9700 / 32GB VRAM).

### The Connection Matrix

*   **ESP32 \leftrightarrow Gateway:** Persistent WebSocket (Bi-directional, low latency).
    
*   **Gateway \leftrightarrow Wyoming Whisper:** TCP/Wyoming Protocol (STT).
    
*   **Gateway \leftrightarrow Open WebUI:** WebSocket/SSE (Streaming tokens & Tool-calling).
    
*   **Gateway \leftrightarrow Wyoming Piper:** TCP/Wyoming Protocol (TTS).
    

### The Data & Logic Flow

1.  **Wake Word:** Local detection on ESP32 \rightarrow sends `wake_word_id` + Audio to Gateway.
    
2.  **Routing:** Gateway maps `wake_word_id` to a Profile (Model, System Prompt, Voice).
    
3.  **STT:** Gateway routes audio to Wyoming Whisper \rightarrow receives text.
    
4.  **Reasoning Loop (ReAct):**
    *   Gateway sends text to Open WebUI.
        
    *   If LLM returns a `tool_call`, the Gateway executes the tool and feeds the result back to the LLM.
        
    *   Once a final response is reached, the Gateway proceeds to output.
        
5.  **Output:** Gateway streams tokens to the ESP32 screen (via WebSocket) and sends final text to Wyoming Piper \rightarrow streams audio back to ESP32.
    

## 3. Configuration (Persona Mapping)

The Gateway uses a mapping system to handle different wake words:

```yaml
profiles:
  "Hey Jarvis": 
    model: "llama3:latest"
    system_prompt: "Home Automation Expert"
    voice: "en_US-lessac-medium"
  "Hey Einstein": 
    model: "mistral:latest"
    system_prompt: "Scientific Academic"
    voice: "en_GB-vits-low"
```

## 4. Implementation Roadmap

1.  **Gateway Core:** Build the FastAPI/WebSocket server to handle the "Coordinator" logic and the ReAct tool-loop.
    
2.  **Voice Integration:** Connect the Gateway to Wyoming Whisper and Piper containers.
    
3.  **Device Firmware:** Flash ESP32-S3 to stream audio and render LVGL visuals based on Gateway pushes.
    
4.  **Optimization:** Tuning the WebSocket buffer for the R9700 GPU throughput.
    

## Shopping List

- [ ] ESP32-S3 DevKit
- [ ] INMP441 I2S Mic
- [ ] MAX98357A I2S Amp
- [ ] SPI Display (ILI9341/ST7789)
- [ ] 3D Printed Enclosure