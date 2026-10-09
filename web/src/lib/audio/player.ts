import { Emitter } from '../emitter';
import type { AudioFormat } from '../protocol';
import { getAudioContext } from './context';

export type PlayerEvents = {
  /** First audio of an answer is about to be heard. */
  start: void;
  /** The answer has been fully delivered *and* has finished playing. */
  idle: void;
};

/** What the session store needs from audio output. */
export interface Player {
  on: Emitter<PlayerEvents>['on'];
  /** A new answer's audio is starting (resets scheduling). */
  begin(format: AudioFormat): void;
  /** One PCM frame (16-bit mono) of the current answer. */
  push(frame: ArrayBuffer): void;
  /** The Gateway has sent everything for this answer. */
  finish(): void;
  /** Stop immediately and drop anything still in flight. True if audio was active. */
  stop(): boolean;
  /** Current output loudness 0..1, for visualisation. */
  level(): number;
}

/** Plays an answer's audio as it streams in: every frame is scheduled to
 *  start exactly when the previous one ends (gapless), so playback begins
 *  after the first sentence rather than after the whole answer. */
export class StreamPlayer extends Emitter<PlayerEvents> implements Player {
  private readonly sources = new Set<AudioBufferSourceNode>();
  private format: AudioFormat | null = null;
  private nextTime = 0;
  private finished = false;
  private started = false;
  private analyser: AnalyserNode | null = null;
  private levelBuffer: Uint8Array<ArrayBuffer> | null = null;

  constructor(private readonly context: () => AudioContext = getAudioContext) {
    super();
  }

  begin(format: AudioFormat): void {
    this.format = format;
    this.finished = false;
    this.started = false;
    if (this.sources.size === 0) this.nextTime = 0;
  }

  push(frame: ArrayBuffer): void {
    if (!this.format) return; // stale frame after a stop, or no header yet
    const ctx = this.context();
    const samples = new Int16Array(frame);
    const buffer = ctx.createBuffer(1, samples.length, this.format.sample_rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.output(ctx));
    source.onended = () => {
      // stop() detaches this first, so a cancelled clip's late `ended`
      // can't be mistaken for the next answer finishing.
      this.sources.delete(source);
      this.maybeIdle();
    };

    // Back-to-back with the previous frame; if the stream fell behind,
    // restart with a small lead rather than scheduling in the past.
    const startAt = Math.max(ctx.currentTime + 0.05, this.nextTime);
    source.start(startAt);
    this.nextTime = startAt + buffer.duration;
    this.sources.add(source);
    if (!this.started) {
      this.started = true;
      this.emit('start', undefined);
    }
  }

  finish(): void {
    this.finished = true;
    this.format = null;
    this.maybeIdle();
  }

  stop(): boolean {
    const hadAudio = this.sources.size > 0 || this.format !== null;
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.nextTime = 0;
    this.finished = false;
    this.format = null; // ignore frames still in flight from the stopped answer
    return hadAudio;
  }

  level(): number {
    if (!this.analyser || !this.levelBuffer) return 0;
    this.analyser.getByteTimeDomainData(this.levelBuffer);
    let sum = 0;
    for (const v of this.levelBuffer) sum += ((v - 128) / 128) ** 2;
    return Math.sqrt(sum / this.levelBuffer.length);
  }

  private output(ctx: AudioContext): AudioNode {
    if (!this.analyser && typeof ctx.createAnalyser === 'function') {
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 256;
      this.levelBuffer = new Uint8Array(this.analyser.fftSize);
      this.analyser.connect(ctx.destination);
    }
    return this.analyser ?? ctx.destination;
  }

  private maybeIdle(): void {
    if (this.finished && this.sources.size === 0) {
      this.finished = false;
      this.emit('idle', undefined);
    }
  }
}
