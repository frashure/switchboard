import { describe, expect, it } from 'vitest';
import type { Capture, CaptureEvents } from '../src/lib/audio/capture';
import { LoginError, type AuthPort, type AuthStatus, type AuthUser } from '../src/lib/auth';
import type { Player, PlayerEvents } from '../src/lib/audio/player';
import { Emitter } from '../src/lib/emitter';
import { UnauthorizedError, type GatewayEvents } from '../src/lib/gateway';
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
  connects = 0;
  closes = 0;
  personaError: Error | null = null;
  connect() {
    this.connects++;
  }
  close() {
    this.closes++;
  }
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
    if (this.personaError) throw this.personaError;
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

const USER: AuthUser = { id: 'u1', name: 'Alice', email: 'alice@example.com', role: 'user' };

class FakeAuth implements AuthPort {
  status: AuthStatus = { auth_mode: 'none', authenticated: true, user: null };
  loginResult: AuthUser | Error = USER;
  calls: string[] = [];
  async me() {
    this.calls.push('me');
    return this.status;
  }
  async login(email: string, _password: string) {
    this.calls.push(`login:${email}`);
    if (this.loginResult instanceof Error) throw this.loginResult;
    return this.loginResult;
  }
  async logout() {
    this.calls.push('logout');
  }
}

const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

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

async function setup(configure?: (auth: FakeAuth) => void) {
  const gateway = new FakeGateway();
  const capture = new FakeCapture();
  const player = new FakePlayer();
  const auth = new FakeAuth();
  configure?.(auth);
  const store = new SessionStore({ gateway, auth, capture, player, retryPersonasMs: 1 });
  store.start();
  await flush();
  gateway.fire('open', { reconnect: false });
  await flush();
  return { gateway, capture, player, auth, store };
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

describe('SessionStore sign-in', () => {
  const signedOut = (auth: FakeAuth) => {
    auth.status = { auth_mode: 'owui', authenticated: false, user: null };
  };
  const signedIn = (auth: FakeAuth) => {
    auth.status = { auth_mode: 'owui', authenticated: true, user: USER };
  };

  it('with login off (AUTH_MODE=none) goes straight in', async () => {
    const { store, gateway } = await setup();
    expect(store.auth).toBe('signed_in');
    expect(store.authMode).toBe('none');
    expect(gateway.connects).toBe(1);
  });

  it('shows the login screen, without connecting or loading anything, when signed out', async () => {
    const { store, gateway } = await setup(signedOut);
    expect(store.auth).toBe('signed_out');
    expect(gateway.connects).toBe(0);
    expect(gateway.personaFetches).toBe(0);
  });

  it('resumes silently when the browser already has a valid session', async () => {
    const { store, gateway } = await setup(signedIn);
    expect(store.auth).toBe('signed_in');
    expect(store.user?.name).toBe('Alice');
    expect(gateway.connects).toBe(1);
    expect(store.personas).toHaveLength(2);
  });

  it('logs in, then connects and loads that user\'s personas', async () => {
    const { store, gateway, auth } = await setup(signedOut);
    await store.login('  alice@example.com ', 'pw');
    await flush();
    expect(auth.calls).toContain('login:alice@example.com'); // email trimmed, password untouched
    expect(store.auth).toBe('signed_in');
    expect(store.user?.email).toBe('alice@example.com');
    expect(gateway.connects).toBe(1);
    expect(store.personas.map((p) => p.id)).toEqual(['phil', 'ophelia']);
    expect(store.loginBusy).toBe(false);
  });

  it.each([
    [new LoginError('invalid'), 'Incorrect email or password.'],
    [new LoginError('rate_limited', 42), 'Too many attempts. Try again in 42s.'],
    [new LoginError('rate_limited'), 'Too many attempts. Try again shortly.'],
    [new LoginError('unreachable'), "Can't reach the Gateway. Check your connection."],
  ])('reports a failed login: %s', async (error, message) => {
    const { store, gateway } = await setup((a) => {
      signedOut(a);
      a.loginResult = error;
    });
    await store.login('a@x', 'bad');
    expect(store.loginError).toBe(message);
    expect(store.auth).toBe('signed_out');
    expect(store.loginBusy).toBe(false);
    expect(gateway.connects).toBe(0);
  });

  it('a second submit while one is in flight is ignored', async () => {
    const { store, auth } = await setup(signedOut);
    const first = store.login('a@x', 'pw');
    await store.login('a@x', 'pw');
    await first;
    expect(auth.calls.filter((c) => c.startsWith('login')).length).toBe(1);
  });

  it('logging out wipes the previous user\'s state completely', async () => {
    const { store, gateway, capture, player, auth } = await setup(signedIn);
    store.select('phil');
    await store.startTalking();
    gateway.server({ type: 'transcript', text: 'private question' });
    await store.logout();

    expect(auth.calls).toContain('logout');
    expect(store.auth).toBe('signed_out');
    expect(store.user).toBeNull();
    expect(store.personas).toEqual([]);
    expect(store.selectedId).toBeNull();
    expect(store.turns).toEqual([]); // the next person must not see this conversation
    expect(store.view).toBe('picker');
    expect(capture.running).toBe(false);
    expect(player.calls).toContain('stop');
    expect(gateway.closes).toBeGreaterThan(0);
    expect(store.loginNotice).toBeNull(); // deliberate sign-out: no "expired" message
  });

  it('signing in as someone else after logout shows that user\'s data only', async () => {
    const { store, gateway } = await setup(signedIn);
    store.select('phil');
    gateway.server({ type: 'transcript', text: 'first user secret' });
    await store.logout();
    await store.login('bob@example.com', 'pw');
    await flush();
    expect(store.auth).toBe('signed_in');
    expect(store.turns).toEqual([]);
    expect(store.selectedId).toBeNull();
    expect(store.view).toBe('picker');
  });

  it.each([
    ['the socket is refused as unauthorized', (g: FakeGateway) => g.fire('unauthorized', undefined)],
    ['the persona list returns 401', (g: FakeGateway) => (g.personaError = new UnauthorizedError())],
    ['Open WebUI rejects the token mid-turn', (g: FakeGateway) => g.server({ type: 'status', state: 'error', detail: 'session_expired' })],
  ])('an expired session returns to login with a notice when %s', async (_name, trigger) => {
    const { store, gateway } = await setup(signedIn);
    if (_name.startsWith('the persona list')) {
      trigger(gateway);
      gateway.fire('open', { reconnect: true }); // reconnect refetches personas
      await flush();
    } else {
      trigger(gateway);
    }
    expect(store.auth).toBe('signed_out');
    expect(store.loginNotice).toBe('Your session expired. Please sign in again.');
    expect(store.personas).toEqual([]);
  });

  it('keeps trying to reach a Gateway that is not up yet instead of showing the login form', async () => {
    const gateway = new FakeGateway();
    const auth = new FakeAuth();
    let attempts = 0;
    auth.me = async () => {
      if (++attempts < 3) throw new Error('network');
      return { auth_mode: 'owui', authenticated: false, user: null };
    };
    const store = new SessionStore({ gateway, auth, capture: new FakeCapture(), player: new FakePlayer(), retryPersonasMs: 1 });
    store.start();
    expect(store.auth).toBe('checking');
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(store.auth).toBe('signed_out');
    expect(attempts).toBe(3);
  });
});
