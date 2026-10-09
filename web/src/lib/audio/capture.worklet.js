// Runs on the audio rendering thread. Batches the 128-sample render
// quanta into ~50 ms chunks (at 48 kHz) before posting to the main thread,
// instead of 375 messages a second. Plain JS on purpose: AudioWorklet
// modules are loaded by URL and bypass the bundler's TypeScript pipeline.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2400);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    let offset = 0;
    while (offset < channel.length) {
      const take = Math.min(channel.length - offset, this.buffer.length - this.filled);
      this.buffer.set(channel.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.buffer.length) {
        const out = this.buffer.slice();
        this.port.postMessage(out.buffer, [out.buffer]);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
