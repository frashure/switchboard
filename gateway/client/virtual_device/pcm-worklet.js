// Runs on the audio rendering thread; hands each render quantum of mic
// audio (Float32, at the AudioContext's native sample rate) back to the
// main thread for resampling + PCM16 conversion in app.js.
class PCMWorkletProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      this.port.postMessage(input[0]);
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PCMWorkletProcessor);
