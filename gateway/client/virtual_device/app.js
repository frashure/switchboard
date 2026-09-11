// Browser-based hardware-free stand-in for the ESP32 (docs/design.md
// Section 6/1): persona select + tap-to-talk over the real WS protocol.
// Single tap starts listening; the Gateway's own VAD decides when you've
// stopped talking (no hold-to-talk, no manual "I'm done" signal needed).
// A separate Cancel button aborts the current turn.
//
// Serve this folder with any static file server (the Gateway itself
// doesn't serve static files in M1), e.g.:
//   python -m http.server 8001 --directory client/virtual_device
// then open http://localhost:8001/ while the Gateway runs on :8090.

const GATEWAY_HTTP = "http://localhost:8090";
const GATEWAY_WS = "ws://localhost:8090/ws";

const personasEl = document.getElementById("personas");
const talkEl = document.getElementById("talk");
const cancelEl = document.getElementById("cancel");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const llmTextEl = document.getElementById("llm_text");

let ws;
let selectedPersona = null;
let audioContext;
let micStream;
let sourceNode;
let workletNode;
let pendingAudioHeader = null;
let audioChunks = [];
let talking = false;

function connect() {
  ws = new WebSocket(GATEWAY_WS);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => (statusEl.textContent = "connected");
  ws.onclose = () => (statusEl.textContent = "disconnected");
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
      statusEl.textContent = `${msg.state}${msg.detail ? " (" + msg.detail + ")" : ""}`;
      updateButtons(msg.state);
      // The server's VAD (or max-duration cap) decided speech ended --
      // stop the local mic stream. Also covers the cancel path: cancel()
      // server-side emits status "done", which lands here too. The server
      // never re-sends "listening" mid-capture (see updateButtons), so any
      // status arriving while talking=true means the capture phase ended.
      if (talking) stopMic();
      break;
    case "transcript":
      transcriptEl.textContent = "you: " + msg.text;
      break;
    case "llm_text":
      llmTextEl.textContent = "assistant: " + msg.text;
      break;
    case "audio_header":
      // Long answers' audio is sent as multiple binary frames, not
      // necessarily just one (confirmed: a long response's PCM exceeded
      // common single-frame WS size limits) -- accumulate until `done`.
      pendingAudioHeader = msg;
      audioChunks = [];
      break;
    case "done":
      if (pendingAudioHeader && audioChunks.length) {
        const { sample_rate, bits, channels } = pendingAudioHeader;
        playPCM(concatArrayBuffers(audioChunks), sample_rate, bits, channels);
      }
      pendingAudioHeader = null;
      audioChunks = [];
      updateButtons("ready");
      break;
  }
}

function updateButtons(state) {
  // NOTE: server-emitted status "listening" (design.md Section 7) means
  // "persona selected, Gateway ready for you to talk" -- NOT "actively
  // capturing". It's only ever sent once, right after select_persona.
  // "capturing" is a client-only state (set optimistically in
  // startTalking(), since talk_start has no server status reply) meaning
  // the mic is actually open. Conflating the two (both as "listening")
  // previously left the button stuck disabled/red right after selecting a
  // persona, since the server's post-select "listening" status arrived
  // and overwrote the client's own "ready" state.
  const idle = state === "ready" || state === "done" || state === "listening";
  talkEl.disabled = !selectedPersona || !idle;
  talkEl.classList.toggle("listening", state === "capturing");
  // Avoid switching persona mid-turn -- the Gateway has no notion of
  // "change persona while capturing/thinking/speaking".
  for (const b of personasEl.children) b.disabled = !idle;
  talkEl.textContent = idle
    ? "Tap to talk"
    : state === "capturing"
      ? "Listening... (tap Cancel to stop)"
      : state[0].toUpperCase() + state.slice(1) + "...";
  cancelEl.classList.toggle("visible", !idle);
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
  source.start();
}

async function loadPersonas() {
  const res = await fetch(`${GATEWAY_HTTP}/profiles`);
  const { personas } = await res.json();
  personasEl.innerHTML = "";
  for (const id of personas) {
    const btn = document.createElement("button");
    btn.textContent = id;
    btn.onclick = () => selectPersona(id, btn);
    personasEl.appendChild(btn);
  }
}

function selectPersona(id, btn) {
  selectedPersona = id;
  for (const b of personasEl.children) b.classList.remove("selected");
  btn.classList.add("selected");
  ws.send(JSON.stringify({ type: "select_persona", id }));
  updateButtons("ready");
}

// Naive linear-interpolation resampler, chunk-by-chunk from the
// AudioWorklet's render-quantum callbacks. Introduces minor discontinuities
// at chunk boundaries -- fine for M1 testing, worth revisiting if audio
// quality into Whisper becomes an issue.
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

async function startTalking() {
  if (!selectedPersona || talking) return;
  talking = true;
  ws.send(JSON.stringify({ type: "talk_start" }));
  // talk_start has no server-side status reply (design.md's protocol only
  // defines status for select_persona/thinking/speaking/error) -- reflect
  // the capture start optimistically rather than waiting for one.
  updateButtons("capturing");

  if (!audioContext) audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("pcm-worklet.js");

  micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  sourceNode = audioContext.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-worklet");
  workletNode.port.onmessage = (e) => {
    const resampled = resampleTo16k(e.data, audioContext.sampleRate);
    const pcm16 = floatTo16BitPCM(resampled);
    ws.send(pcm16.buffer);
  };
  sourceNode.connect(workletNode);
  // Deliberately not connecting workletNode to destination -- we don't want
  // to hear our own mic locally.
}

function stopMic() {
  talking = false;
  if (sourceNode) sourceNode.disconnect();
  if (workletNode) workletNode.disconnect();
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
}

function cancelTurn() {
  stopMic();
  ws.send(JSON.stringify({ type: "cancel" }));
}

talkEl.addEventListener("click", startTalking);
cancelEl.addEventListener("click", cancelTurn);

connect();
loadPersonas();
