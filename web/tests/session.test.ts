import { describe, expect, it } from 'vitest';
import type { Capture, CaptureEvents } from '../src/lib/audio/capture';
import type { Player, PlayerEvents } from '../src/lib/audio/player';
import { Emitter } from '../src/lib/emitter';
import type { GatewayEvents } from '../src/lib/gateway';
import type { AudioFormat, ClientMessage, Persona, ServerMessage } from '../src/lib/protocol';
import { SessionStore, type GatewayPort } from '../src/lib/session.svelte';

const PERSONAS: Persona[] = [
  { id: 'phil', display_name: 'Phil', avatar: null },
  { id: 'ophelia', display_name: 'Ophelia', avatar: null },
];
const FORMAT: AudioFormat = { sample_rate: 22050, bits: 16, channels: 1 };

class FakeGateway extends Emitter<GatewayEvents> implements GatewayPort {
  sent: ClientMessage[] = [];
  audio: ArrayBuffer[] = [];
  online = true;
  personaFetches = 0;
  connect() {}
  close() {}
  send(message: ClientMessage) {
    if (!this.online) return false;
    this.sent.push(message);
    return true;
  }
  sendAudio(pcm: ArrayBuffer) {
    if (!this.online) return false;
    this.audio.push(pcm);
    return true;
  }
  async fetchPersonas() {
    this.personaFetches++;
    return PERSONAS;
  }
  // test helpers
  server(message: ServerMessage) {
    this.emit('message', message);
  }
  frame(buf: ArrayBuffer) {
    this.emit('audio', buf);
  }
  fire<K extends keyof GatewayEvents>(event: K, payload: GatewayEvents[K]) {
    this.emit(event, payload);
  }
  types() {
    return this.sent.map((m) => m.type);
  }
}

class FakeCapture extends Emitter<CaptureEvents> implements Capture {
  running = false;
  failWith: Error | null = null;
  async start() {
    if (this.failWith) throw this.failWith;
    this.running = true;
  }
  stop() {
    this.running = false;
  }
  fire<K extends keyof CaptureEvents>(event: K, payload: CaptureEvents[K]) {
    this.emit(event, payload);
  }
}

class FakePlayer extends Emitter<PlayerEvents> implements Player {
  calls: string[] = [];
  pushed = 0;
  begin() {
    this.calls.push('begin');
  }
  push() {
    this.pushed++;
  }
  finish() {
    this.calls.push('finish');
  }
  stop() {
    this.calls.push('stop');
    return true;
  }
  level() {
    return 0;
  }
  fire<K extends keyof PlayerEvents>(event: K, payload: PlayerEvents[K]) {
    this.emit(event, payload);
  }
}

async function setup() {
  const gateway = new FakeGateway();
  const capture = new FakeCapture();
  const player = new FakePlayer();
  const store = new SessionStore({ gateway, capture, player, retryPersonasMs: 1 });
  store.start();
  gateway.fire('open', { reconnect: false });
  await Promise.resolve();
  await Promise.resolve();
  return { gateway, capture, player, store };
}

async function inSession() {
  const ctx = await setup();
  ctx.store.select('phil');
  return ctx;
}

describe('SessionStore', () => {
  it('loads personas and goes online', async () => {
    const { store } = await setup();
    expect(store.connection).toBe('online');
    expect(store.personas.map((p) => p.id)).toEqual(['phil', 'ophelia']);
    expect(store.personasLoaded).toBe(true);
  });

  it('selecting a persona tells the Gateway and opens the session view', async () => {
    const { store, gateway } = await setup();
    store.select('phil');
    expect(gateway.sent).toEqual([{ type: 'select_persona', id: 'phil' }]);
    expect(store.view).toBe('session');
    expect(store.selected?.display_name).toBe('Phil');
    expect(store.phase).toBe('ready');
  });

  it('runs a full turn: listen -> think -> speak -> ready', async () => {
    const { store, gateway, capture, player } = await inSession();

    await store.startTalking();
    expect(store.phase).toBe('capturing');
    expect(capture.running).toBe(true);
    expect(gateway.types()).toContain('talk_start');

    capture.fire('frame', new ArrayBuffer(8));
    expect(gateway.audio).toHaveLength(1);

    gateway.server({ type: 'status', state: 'thinking', detail: 'capture_end_reason=vad' });
    expect(store.phase).toBe('thinking');
    expect(capture.running).toBe(false); // the Gateway's VAD ended the utterance
    capture.fire('frame', new ArrayBuffer(8));
    expect(gateway.audio).toHaveLength(1); // no audio forwarded after capture ends

    gateway.server({ type: 'transcript', text: '  hello there ' });
    gateway.server({ type: 'llm_text', text: ' Hi! ' });
    expect(store.turns).toMatchObject([{ transcript: 'hello there', reply: 'Hi!' }]);

    gateway.server({ type: 'status', state: 'speaking' });
    gateway.server({ type: 'audio_header', ...FORMAT });
    gateway.frame(new ArrayBuffer(4));
    gateway.frame(new ArrayBuffer(4));
    expect(store.phase).toBe('speaking');
    expect(player.calls).toContain('begin');
    expect(player.pushed).toBe(2);

    gateway.server({ type: 'done' });
    expect(player.calls).toContain('finish');
    expect(store.phase).toBe('speaking'); // still playing locally
    player.fire('idle', undefined);
    expect(store.phase).toBe('ready');
  });

  it('tapping while listening sends talk_end and moves to thinking', async () => {
    const { store, gateway, capture } = await inSession();
    await store.startTalking();
    store.finishTalking();
    expect(gateway.types()).toContain('talk_end');
    expect(store.phase).toBe('thinking');
    expect(capture.running).toBe(false);
  });

  it('cancel stops everything and sends cancel', async () => {
    const { store, gateway, capture, player } = await inSession();
    await store.startTalking();
    store.cancel();
    expect(store.phase).toBe('ready');
    expect(capture.running).toBe(false);
    expect(player.calls).toContain('stop');
    expect(gateway.types().at(-1)).toBe('cancel');
  });

  it('ignores the cancelled turn\'s in-flight messages until the cancel is acknowledged', async () => {
    const { store, gateway } = await inSession();
    await store.startTalking();
    store.cancel();
    await store.startTalking(); // user taps again straight away
    expect(store.phase).toBe('capturing');

    // Late messages from the cancelled turn must not disturb the new one.
    gateway.server({ type: 'status', state: 'thinking' });
    gateway.server({ type: 'transcript', text: 'ghost' });
    expect(store.phase).toBe('capturing');
    expect(store.turns).toHaveLength(0);

    gateway.server({ type: 'status', state: 'done' }); // the ack
    gateway.server({ type: 'status', state: 'thinking' }); // now this is the new turn
    expect(store.phase).toBe('thinking');
  });

  it('cancelling while speaking stops playback without waiting for the Gateway', async () => {
    const { store, gateway, player } = await inSession();
    await store.startTalking();
    gateway.server({ type: 'status', state: 'thinking' });
    gateway.server({ type: 'status', state: 'speaking' });
    expect(store.phase).toBe('speaking');
    store.cancel();
    expect(store.phase).toBe('ready');
    expect(player.calls.at(-1)).toBe('stop');
  });

  it('a turn that produces no audio ends on done', async () => {
    const { store, gateway } = await inSession();
    await store.startTalking();
    gateway.server({ type: 'status', state: 'thinking' });
    gateway.server({ type: 'done' });
    expect(store.phase).toBe('ready');
  });

  it('shows an error and resets on stt/llm failure', async () => {
    const { store, gateway } = await inSession();
    await store.startTalking();
    gateway.server({ type: 'status', state: 'thinking' });
    gateway.server({ type: 'status', state: 'error', detail: 'llm_failed' });
    expect(store.phase).toBe('ready');
    expect(store.error?.code).toBe('llm_failed');
    store.dismissError();
    expect(store.error).toBeNull();
  });

  it('keeps playing already-delivered audio when TTS fails part-way', async () => {
    const { store, gateway } = await inSession();
    await store.startTalking();
    gateway.server({ type: 'status', state: 'thinking' });
    gateway.server({ type: 'status', state: 'speaking' });
    gateway.server({ type: 'status', state: 'error', detail: 'tts_failed' });
    expect(store.phase).toBe('speaking');
    expect(store.error?.code).toBe('tts_failed');
  });

  it('returns to a refreshed picker when the Gateway does not know the persona', async () => {
    const { store, gateway } = await inSession();
    const fetchesBefore = gateway.personaFetches;
    gateway.server({ type: 'status', state: 'error', detail: 'unknown_persona' });
    await Promise.resolve();
    expect(store.view).toBe('picker');
    expect(store.selectedId).toBeNull();
    expect(store.error?.code).toBe('unknown_persona');
    expect(gateway.personaFetches).toBe(fetchesBefore + 1);
  });

  it('abandons the turn if the microphone is unavailable', async () => {
    const { store, gateway, capture } = await inSession();
    capture.failWith = new Error('NotAllowedError');
    await store.startTalking();
    expect(store.phase).toBe('ready');
    expect(store.error?.code).toBe('mic_unavailable');
    expect(gateway.types()).toContain('cancel');
  });

  it('cannot go back mid-turn', async () => {
    const { store } = await inSession();
    await store.startTalking();
    store.back();
    expect(store.view).toBe('session');
    store.cancel();
    store.back();
    expect(store.view).toBe('picker');
  });

  it('survives a reconnect: resets the turn, restores the persona, refreshes the list', async () => {
    const { store, gateway } = await inSession();
    await store.startTalking();
    const fetchesBefore = gateway.personaFetches;

    gateway.online = false;
    gateway.fire('close', undefined);
    expect(store.connection).toBe('offline');
    expect(store.phase).toBe('ready');
    expect(store.error?.code).toBe('connection_lost');

    gateway.online = true;
    gateway.sent.length = 0;
    gateway.fire('open', { reconnect: true });
    await Promise.resolve();
    expect(store.connection).toBe('online');
    expect(store.error).toBeNull();
    expect(gateway.sent).toEqual([{ type: 'select_persona', id: 'phil' }]);
    expect(gateway.personaFetches).toBe(fetchesBefore + 1);
  });

  it('starting a new conversation clears the history', async () => {
    const { store, gateway } = await inSession();
    await store.startTalking();
    gateway.server({ type: 'transcript', text: 'hi' });
    expect(store.turns).toHaveLength(1);
    store.cancel();
    store.select('ophelia');
    expect(store.turns).toHaveLength(0);
  });
});
