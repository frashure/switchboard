import { describe, expect, it } from 'vitest';
import { LinearResampler, floatToPcm16, rmsLevel } from '../src/lib/audio/resample';

const sine = (n: number, rate: number, hz = 440) => Float32Array.from({ length: n }, (_, i) => Math.sin((2 * Math.PI * hz * i) / rate));

describe('LinearResampler', () => {
  it('passes through at equal rates', () => {
    const input = sine(100, 16000);
    expect(new LinearResampler(16000, 16000).process(input)).toBe(input);
  });

  it('produces the right amount of output', () => {
    const out = new LinearResampler(48000, 16000).process(sine(4800, 48000));
    expect(Math.abs(out.length - 1600)).toBeLessThanOrEqual(1);
  });

  it('chunked resampling matches resampling in one go (no seams)', () => {
    const input = sine(9600, 48000);
    const whole = new LinearResampler(48000, 16000).process(input);

    const chunked = new LinearResampler(48000, 16000);
    const pieces: number[] = [];
    for (let i = 0; i < input.length; i += 2400) pieces.push(...chunked.process(input.subarray(i, i + 2400)));

    expect(Math.abs(pieces.length - whole.length)).toBeLessThanOrEqual(1);
    for (let i = 0; i < Math.min(pieces.length, whole.length); i++) {
      expect(pieces[i]).toBeCloseTo(whole[i], 5);
    }
  });

  it('works for non-integer ratios (44.1 kHz)', () => {
    const r = new LinearResampler(44100, 16000);
    const total = [2205, 2205, 2205, 2205].reduce((n, len) => n + r.process(sine(len, 44100)).length, 0);
    expect(Math.abs(total - 3200)).toBeLessThanOrEqual(2);
  });
});

describe('pcm helpers', () => {
  it('converts and clamps float to int16', () => {
    expect(Array.from(floatToPcm16(Float32Array.from([0, 1, -1, 2, -2])))).toEqual([0, 32767, -32768, 32767, -32768]);
  });
  it('computes rms', () => {
    expect(rmsLevel(new Float32Array(0))).toBe(0);
    expect(rmsLevel(Float32Array.from([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5);
  });
});
