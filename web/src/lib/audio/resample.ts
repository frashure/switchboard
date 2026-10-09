/** Streaming linear-interpolation resampler. Unlike resampling each chunk
 *  independently, it carries the fractional read position and the last
 *  sample across chunk boundaries, so there are no discontinuities at the
 *  seams between worklet chunks. */
export class LinearResampler {
  private readonly ratio: number;
  private position = 0;
  private last = 0;

  constructor(inputRate: number, outputRate: number) {
    this.ratio = inputRate / outputRate;
  }

  process(input: Float32Array): Float32Array {
    if (this.ratio === 1) return input;
    // Virtual signal: [last, ...input]; `position` indexes into it.
    const length = input.length + 1;
    const at = (i: number) => (i === 0 ? this.last : input[i - 1]);
    const out = new Float32Array(Math.max(0, Math.ceil((length - 1 - this.position) / this.ratio)));
    let n = 0;
    while (this.position + 1 < length) {
      const i0 = Math.floor(this.position);
      const frac = this.position - i0;
      out[n++] = at(i0) * (1 - frac) + at(i0 + 1) * frac;
      this.position += this.ratio;
    }
    this.position -= length - 1;
    this.last = input[input.length - 1] ?? this.last;
    return out.subarray(0, n);
  }
}

export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function rmsLevel(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}
