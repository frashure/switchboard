// Browser-based hardware-free stand-in for the ESP32 (docs/design.md
// Section 6/1): persona select + tap-to-talk over the real WS protocol.
// Single tap starts listening; the Gateway's own VAD decides when you've
// stopped talking (no hold-to-talk, no manual "I'm done" signal needed).
// A separate Cancel button aborts the current turn.
//
// Served by the Gateway itself when SWITCHBOARD_STATIC_DIR is set. In dev
// it can also be served separately:
//   python -m http.server 8001 --directory client/virtual_device
// then open http://localhost:8001/?gateway=localhost:8090

// Same origin as the page by default (the Gateway serves this client in
// the deployed setup); ?gateway=host:port overrides for a separate static
// server in dev. Scheme follows the page's so HTTPS pages get wss://.
const params = new URLSearchParams(window.location.search);
const secure = window.location.protocol === "https:";
const gatewayHost = params.get("gateway") || window.location.host;
const GATEWAY_HTTP = `${secure ? "https" : "http"}://${gatewayHost}`;
const GATEWAY_WS = `${secure ? "wss" : "ws"}://${gatewayHost}/ws`;

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
let audioFormat = null; // sample_rate/channels from the current answer's audio_header
let talking = false;
// Streamed playback: the Gateway sends an answer's audio as it is
// synthesized (a sentence or so at a time), so each binary frame is
// scheduled to start exactly when the previous one ends instead of
// waiting for the whole answer.
const playingSources = new Set();
let nextPlayTime = 0;
let streamDone = false; // the Gateway has sent `done` for this answer

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
      audioFormat = msg;
      streamDone = false;
      if (playingSources.size === 0) nextPlayTime = 0;
      break;
    case "done":
      // The Gateway is done once all audio is delivered, but playback runs
      // on for as long as the answer is -- stay in "speaking" (Cancel
      // visible) until it actually ends.
      streamDone = true;
      audioFormat = null;
      if (playingSources.size === 0) updateButtons("ready");
      break;
    case "stop_audio":
      // cancel() sent server-side after TTS audio was already delivered --
      // the server can't un-send it, so stop local playback instead.
      stopPlayback();
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
  if (!audioFormat) return; // stale frame after cancel, or no header yet
  enqueuePCM(arrayBuffer, audioFormat.sample_rate);
}

function enqueuePCM(arrayBuffer, sampleRate) {
  if (!audioContext) audioContext = new AudioContext();
  if (audioContext.state === "suspended") audioContext.resume();
  const int16 = new Int16Array(arrayBuffer);
  const buffer = audioContext.createBuffer(1, int16.length, sampleRate);
  const channelData = buffer.getChannelData(0);
  for (let i = 0; i < int16.length; i++) {
    channelData[i] = int16[i] / 32768;
  }
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  source.onended = () => {
    // stopPlayback() detaches this handler first, so a cancelled clip's
    // late onended can't reset the UI mid-way through the next turn.
    playingSources.delete(source);
    if (streamDone && playingSources.size === 0) updateButtons("ready");
  };
  // Back-to-back with the previous frame; if the stream ever falls behind,
  // restart with a small lead rather than scheduling in the past.
  const startAt = Math.max(audioContext.currentTime + 0.05, nextPlayTime);
  source.start(startAt);
  nextPlayTime = startAt + buffer.duration;
  playingSources.add(source);
}

// Returns true if anything was playing or still streaming in.
function stopPlayback() {
  const hadAudio = playingSources.size > 0 || audioFormat !== null;
  for (const source of playingSources) {
    source.onended = null;
    try { source.stop(); } catch (e) { /* already stopped */ }
  }
  playingSources.clear();
  nextPlayTime = 0;
  streamDone = false;
  audioFormat = null; // ignore frames still in flight from the cancelled answer
  return hadAudio;
}

async function loadPersonas() {
  const res = await fetch(`${GATEWAY_HTTP}/profiles`);
  const { personas } = await res.json();
  personasEl.innerHTML = "";
  for (const { id, avatar } of personas) {
    const btn = document.createElement("button");
    if (avatar) {
      const img = document.createElement("img");
      img.src = avatar;
      img.alt = id;
      btn.appendChild(img);
    }
    btn.appendChild(document.createTextNode(id));
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
  // The Gateway has already delivered the whole answer by the time it is
  // playing, so stop it locally rather than relying on a server message.
  if (stopPlayback()) updateButtons("ready");
  ws.send(JSON.stringify({ type: "cancel" }));
}

talkEl.addEventListener("click", startTalking);
cancelEl.addEventListener("click", cancelTurn);

connect();
loadPersonas();
