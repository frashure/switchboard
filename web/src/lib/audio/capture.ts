import { Emitter } from '../emitter';
import { PCM_INPUT_RATE } from '../protocol';
import { getAudioContext } from './context';
import { LinearResampler, floatToPcm16, rmsLevel } from './resample';
import workletUrl from './capture.worklet.js?url&no-inline';

export type CaptureEvents = {
  /** 16-bit / 16 kHz / mono PCM, as the Gateway expects. */
  frame: ArrayBuffer;
  /** Instantaneous mic level, 0..1, for visualisation. */
  level: number;
};

/** What the session store needs from a microphone -- also the seam where a
 *  test (or another input device) substitutes its own implementation. */
export interface Capture {
  on: Emitter<CaptureEvents>['on'];
  start(): Promise<void>;
  stop(): void;
}

let workletLoaded: Promise<void> | null = null;

export class MicCapture extends Emitter<CaptureEvents> implements Capture {
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private starting = false;

  async start(): Promise<void> {
    if (this.stream || this.starting) return;
    this.starting = true;
    try {
      const ctx = getAudioContext();
      workletLoaded ??= ctx.audioWorklet.addModule(workletUrl);
      await workletLoaded;

      // Echo cancellation matters on a tablet: the speaker and mic are a
      // few centimetres apart, and the Gateway's VAD would otherwise hear
      // the assistant's own voice.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const resampler = new LinearResampler(ctx.sampleRate, PCM_INPUT_RATE);
      this.source = ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(ctx, 'pcm-capture');
      this.node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        const samples = new Float32Array(e.data);
        this.emit('level', rmsLevel(samples));
        const pcm = floatToPcm16(resampler.process(samples));
        if (pcm.length) this.emit('frame', pcm.buffer as ArrayBuffer);
      };
      // Deliberately not connected to the destination: no mic monitoring.
      this.source.connect(this.node);
    } catch (error) {
      this.stop();
      throw error;
    } finally {
      this.starting = false;
    }
  }

  stop(): void {
    this.source?.disconnect();
    this.node?.disconnect();
    if (this.node) this.node.port.onmessage = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source = this.node = this.stream = null;
  }
}
