// WS + audio bridge between the real Gateway protocol and the LVGL/WASM
// UI (ui.c). This is gateway/client/virtual_device/app.js's logic, ported
// to drive LVGL widgets via Module.ccall() instead of DOM elements --
// see that file for the protocol notes this mirrors.
//
// By default the page talks to whatever origin served it (the Gateway
// serves this client itself in the deployed setup, over HTTPS via
// tailscale serve -- so the WS scheme must follow the page's, or the
// browser blocks it as mixed content). ?gateway=host:port overrides, for
// dev with a separate static server (e.g. ?gateway=localhost:8090).
const params = new URLSearchParams(window.location.search);
const secure = window.location.protocol === "https:";
const gatewayHost = params.get("gateway") || window.location.host;
const GATEWAY_HTTP = `${secure ? "https" : "http"}://${gatewayHost}`;
const GATEWAY_WS = `${secure ? "wss" : "ws"}://${gatewayHost}/ws`;

let ws;
let selectedPersona = null;
let audioContext;
let micStream;
let sourceNode;
let workletNode;
let pendingAudioHeader = null;
let audioChunks = [];
let talking = false;
let currentAudioSource = null;

function setStatus(state, detail) {
  Module.ccall("sim_set_status", null, ["string", "string"], [state, detail || ""]);
}

// Sends are dropped (not thrown) while the socket is down -- a tap during
// a reconnect shouldn't surface as an uncaught exception.
function send(data) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
}

let reconnectDelayMs = 1000;
let hasConnectedBefore = false;

function connect() {
  ws = new WebSocket(GATEWAY_WS);
  ws.binaryType = "arraybuffer";
  ws.onopen = async () => {
    reconnectDelayMs = 1000;
    // A new WS is a new server-side session with no persona selected --
    // restore the one the UI is still showing, and refresh the persona
    // list (the Gateway may have restarted, or OWUI personas changed).
    if (hasConnectedBefore) {
      await loadPersonas();
      if (selectedPersona) ws.send(JSON.stringify({ type: "select_persona", id: selectedPersona }));
    }
    hasConnectedBefore = true;
    setStatus(selectedPersona ? "ready" : "connected", "");
  };
  ws.onclose = () => {
    stopMic();
    setStatus("disconnected", "");
    // Unattended kiosk use: keep retrying with backoff rather than
    // needing a manual page reload.
    setTimeout(connect, reconnectDelayMs);
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, 10000);
  };
  ws.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      handleIncomingAudio(event.data);
      return;
    }
    handleControl(JSON.parse(event.data));
  };
}

function handleControl(msg) {
  switch (msg.type) {
    case "status":
      setStatus(msg.state, msg.detail);
      if (talking) stopMic();
      break;
    case "transcript":
      Module.ccall("sim_set_transcript", null, ["string"], [msg.text]);
      break;
    case "llm_text":
      Module.ccall("sim_set_llm_text", null, ["string"], [msg.text]);
      break;
    case "audio_header":
      pendingAudioHeader = msg;
      audioChunks = [];
      break;
    case "done":
      // The Gateway is finished once the audio is delivered, but playback
      // runs on for as long as the answer is -- the UI stays in "speaking"
      // (with Cancel / tap-to-stop available) until it actually ends.
      if (pendingAudioHeader && audioChunks.length) {
        const { sample_rate, bits, channels } = pendingAudioHeader;
        playPCM(concatArrayBuffers(audioChunks), sample_rate, bits, channels);
        setStatus("speaking", "");
      } else {
        setStatus("ready", "");
      }
      pendingAudioHeader = null;
      audioChunks = [];
      break;
    case "stop_audio":
      stopPlayback();
      setStatus("ready", "");
      break;
  }
}

function handleIncomingAudio(arrayBuffer) {
  if (!pendingAudioHeader) return;
  audioChunks.push(arrayBuffer);
}

function concatArrayBuffers(buffers) {
  const totalLength = buffers.reduce((sum, b) => sum + b.byteLength, 0);
  const result = new Uint8Array(totalLength);
  let offset = 0;
  for (const b of buffers) {
    result.set(new Uint8Array(b), offset);
    offset += b.byteLength;
  }
  return result.buffer;
}

function playPCM(arrayBuffer, sampleRate, bits, channels) {
  if (!audioContext) audioContext = new AudioContext();
  const int16 = new Int16Array(arrayBuffer);
  const buffer = audioContext.createBuffer(channels, int16.length / channels, sampleRate);
  const channelData = buffer.getChannelData(0);
  for (let i = 0; i < int16.length; i++) {
    channelData[i] = int16[i] / 32768;
  }
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  source.onended = () => {
    // Only a natural end counts: stopPlayback() clears currentAudioSource
    // first, so a cancelled source's late onended can't flip the UI back to
    // "ready" in the middle of the next turn.
    if (currentAudioSource === source) {
      currentAudioSource = null;
      setStatus("ready", "");
    }
  };
  currentAudioSource = source;
  source.start();
}

function stopPlayback() {
  const source = currentAudioSource;
  currentAudioSource = null;
  if (source) source.stop();
  return source !== null;
}

// Decodes a persona's avatar (data: URI from OWUI, any format the browser
// can decode -- webp/png/jpeg all seen in practice) into a fixed-size
// square RGBA pixel buffer via an offscreen canvas (cover-fit crop, same
// idea as CSS object-fit: cover), so the LVGL side doesn't need to deal
// with varying source resolutions/formats at all.
function decodeAvatarPixels(dataUri, size) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d");
      const scale = Math.max(size / img.width, size / img.height);
      const w = img.width * scale;
      const h = img.height * scale;
      ctx.drawImage(img, (size - w) / 2, (size - h) / 2, w, h);
      resolve(ctx.getImageData(0, 0, size, size).data);
    };
    img.onerror = () => resolve(null);
    img.src = dataUri;
  });
}

const AVATAR_SIZE = 96;

async function registerPersona(p) {
  const rgba = p.avatar ? await decodeAvatarPixels(p.avatar, AVATAR_SIZE) : null;
  if (!rgba) {
    Module.ccall(
      "sim_add_persona", null,
      ["string", "string", "number", "number", "number"],
      [p.id, p.display_name, 0, 0, 0],
    );
    return;
  }
  // LVGL's ARGB8888 buffers are BGRA in memory (lv_color32_t: blue, green,
  // red, alpha), but canvas ImageData is RGBA -- swap R and B per pixel.
  const n = AVATAR_SIZE * AVATAR_SIZE;
  const bgra = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    bgra[o] = rgba[o + 2];
    bgra[o + 1] = rgba[o + 1];
    bgra[o + 2] = rgba[o];
    bgra[o + 3] = rgba[o + 3];
  }
  // Never freed -- lives for the page's lifetime, same as the persona
  // card widget that references it. A few KB per persona.
  const ptr = Module._malloc(bgra.length);
  Module.HEAPU8.set(bgra, ptr);
  Module.ccall(
    "sim_add_persona", null,
    ["string", "string", "number", "number", "number"],
    [p.id, p.display_name, ptr, AVATAR_SIZE, AVATAR_SIZE],
  );
}

async function loadPersonas() {
  let personas;
  for (;;) {
    try {
      const res = await fetch(`${GATEWAY_HTTP}/profiles`);
      ({ personas } = await res.json());
      break;
    } catch (e) {
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  Module.ccall("sim_clear_personas", null, [], []);
  // Sequential, not Promise.all -- add order must match fetch order, since
  // ui.c indexes personas by the order sim_add_persona was called.
  for (const p of personas) {
    await registerPersona(p);
  }
}

// Naive linear-interpolation resampler -- see app.js, same tradeoff.
function resampleTo16k(float32, inputRate) {
  if (inputRate === 16000) return float32;
  const ratio = inputRate / 16000;
  const outLength = Math.floor(float32.length / ratio);
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIndex = i * ratio;
    const i0 = Math.floor(srcIndex);
    const i1 = Math.min(i0 + 1, float32.length - 1);
    const frac = srcIndex - i0;
    out[i] = float32[i0] * (1 - frac) + float32[i1] * frac;
  }
  return out;
}

function floatTo16BitPCM(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

async function startMic() {
  if (!audioContext) audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("pcm-worklet.js");

  micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  sourceNode = audioContext.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-worklet");
  workletNode.port.onmessage = (e) => {
    const resampled = resampleTo16k(e.data, audioContext.sampleRate);
    const pcm16 = floatTo16BitPCM(resampled);
    send(pcm16.buffer);
  };
  sourceNode.connect(workletNode);
}

function stopMic() {
  talking = false;
  if (sourceNode) sourceNode.disconnect();
  if (workletNode) workletNode.disconnect();
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
}

// ---- Exposed to ui.c's EM_ASM button callbacks ----

window.simSelectPersona = function (id) {
  selectedPersona = id;
  send(JSON.stringify({ type: "select_persona", id }));
  setStatus("ready", "");
};

window.simStartTalking = function () {
  if (!selectedPersona || talking) return;
  talking = true;
  send(JSON.stringify({ type: "talk_start" }));
  setStatus("capturing", "");
  startMic();
};

window.simCancel = function () {
  stopMic();
  // Stop local playback directly -- by the time the user is listening to
  // an answer the Gateway has already delivered all of it, so there is
  // nothing server-side left to cancel (and the WS may be down anyway).
  if (stopPlayback()) setStatus("ready", "");
  send(JSON.stringify({ type: "cancel" }));
};

// NOT Module.onRuntimeInitialized -- that fires *before* main() runs
// (Emscripten calls it, then callMain()), which is before ui_init() has
// created any of the screens/widgets these calls touch. main.c calls this
// explicitly once ui_init() is actually done.
window.simReady = () => {
  connect();
  loadPersonas();
};
