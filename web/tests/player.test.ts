import { describe, expect, it } from 'vitest';
import { StreamPlayer } from '../src/lib/audio/player';

class FakeSource {
  onended: (() => void) | null = null;
  startedAt: number | null = null;
  stopped = false;
  buffer: { duration: number } | null = null;
  connect() {}
  start(t: number) {
    this.startedAt = t;
  }
  stop() {
    this.stopped = true;
  }
}

function setup() {
  const sources: FakeSource[] = [];
  const ctx = {
    currentTime: 0,
    destination: {},
    createBuffer: (_ch: number, len: number, rate: number) => ({ duration: len / rate, getChannelData: () => new Float32Array(len) }),
    createBufferSource: () => {
      const s = new FakeSource();
      sources.push(s);
      return s;
    },
  };
  const player = new StreamPlayer(() => ctx as unknown as AudioContext);
  const events: string[] = [];
  player.on('start', () => events.push('start'));
  player.on('idle', () => events.push('idle'));
  const format = { sample_rate: 22050, bits: 16, channels: 1 };
  const frame = (seconds: number) => new Int16Array(Math.round(22050 * seconds)).buffer;
  return { ctx, sources, player, events, format, frame };
}

describe('StreamPlayer', () => {
  it('schedules frames back-to-back', () => {
    const { player, sources, format, frame } = setup();
    player.begin(format);
    [1, 1, 1].forEach((s) => player.push(frame(s)));
    const starts = sources.map((s) => s.startedAt!);
    expect(starts[1] - starts[0]).toBeCloseTo(1, 3);
    expect(starts[2] - starts[1]).toBeCloseTo(1, 3);
  });

  it('is idle only after the last frame ends AND the stream is finished', () => {
    const { player, sources, events, format, frame } = setup();
    player.begin(format);
    player.push(frame(1));
    player.push(frame(1));
    expect(events).toEqual(['start']);
    player.finish();
    sources[0].onended!();
    expect(events).toEqual(['start']);
    sources[1].onended!();
    expect(events).toEqual(['start', 'idle']);
  });

  it('does not go idle while the stream is still arriving', () => {
    const { player, sources, events, format, frame } = setup();
    player.begin(format);
    player.push(frame(1));
    sources[0].onended!();
    expect(events).toEqual(['start']);
    player.finish();
    expect(events).toEqual(['start', 'idle']);
  });

  it('restarts with a small lead if the stream falls behind', () => {
    const { player, sources, ctx, format, frame } = setup();
    player.begin(format);
    player.push(frame(1));
    ctx.currentTime = 50;
    player.push(frame(1));
    expect(sources[1].startedAt).toBeGreaterThanOrEqual(50.05);
  });

  it('goes idle immediately when finished with nothing played', () => {
    const { player, events, format } = setup();
    player.begin(format);
    player.finish();
    expect(events).toEqual(['idle']);
  });

  it('stop() halts everything, ignores late frames and stale ended events', () => {
    const { player, sources, events, format, frame } = setup();
    player.begin(format);
    player.push(frame(1));
    player.push(frame(1));
    expect(player.stop()).toBe(true);
    expect(sources.every((s) => s.stopped)).toBe(true);

    player.push(frame(1)); // in-flight frame from the stopped answer
    expect(sources).toHaveLength(2);
    expect(sources[0].onended).toBeNull();
    expect(events).toEqual(['start']);
  });

  it('plays the next answer normally after a stop', () => {
    const { player, sources, events, format, frame } = setup();
    player.begin(format);
    player.push(frame(1));
    player.stop();
    player.begin(format);
    player.push(frame(1));
    player.finish();
    sources.at(-1)!.onended!();
    expect(events).toEqual(['start', 'start', 'idle']);
  });

  it('stop() reports false when nothing was active', () => {
    expect(setup().player.stop()).toBe(false);
  });
});
