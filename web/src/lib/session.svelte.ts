import type { Capture } from './audio/capture';
import type { Player } from './audio/player';
import type { GatewayClient } from './gateway';
import type { AudioFormat, Persona, ServerMessage } from './protocol';

/** Where a turn is, as the UI sees it. */
export type Phase = 'ready' | 'capturing' | 'thinking' | 'speaking';
export type View = 'picker' | 'session';
export type Connection = 'connecting' | 'online' | 'offline';

export interface Turn {
  id: number;
  transcript: string;
  /** null until the assistant's text arrives. */
  reply: string | null;
}

export type ErrorCode = 'stt_failed' | 'llm_failed' | 'tts_failed' | 'mic_unavailable' | 'connection_lost';

export interface AppError {
  id: number;
  code: ErrorCode;
  message: string;
}

const ERROR_MESSAGES: Record<ErrorCode, string> = {
  stt_failed: "I couldn't make out that audio. Try again?",
  llm_failed: "The assistant didn't respond. Try again?",
  tts_failed: "I couldn't speak that reply, but the text is on screen.",
  mic_unavailable: 'Microphone unavailable. Check the browser permission.',
  connection_lost: 'Connection lost. Reconnecting…',
};

/** The slice of GatewayClient the store uses (the seam for tests). */
export type GatewayPort = Pick<GatewayClient, 'on' | 'connect' | 'close' | 'send' | 'sendAudio' | 'fetchPersonas'>;

export interface SessionDeps {
  gateway: GatewayPort;
  capture: Capture;
  player: Player;
  /** Called from user gestures so later event-driven playback is allowed. */
  unlockAudio?: () => Promise<void>;
  /** Derives a persona's accent hue (0-360), e.g. from its avatar. */
  accentFor?: (persona: Persona) => Promise<number | null>;
  retryPersonasMs?: number;
}

/** All application logic: the turn state machine, connection handling and
 *  conversation history. Components only read this and call its actions;
 *  the gateway, mic and speaker are injected, so none of it needs a
 *  browser to test. */
export class SessionStore {
  connection = $state<Connection>('connecting');
  personas = $state.raw<Persona[]>([]);
  personasLoaded = $state(false);
  /** Accent hue per persona id, filled in asynchronously after personas load. */
  accents = $state<Record<string, number>>({});

  view = $state<View>('picker');
  selectedId = $state<string | null>(null);
  phase = $state<Phase>('ready');
  turns = $state<Turn[]>([]);
  error = $state.raw<AppError | null>(null);
  micLevel = $state(0);

  selected = $derived(this.personas.find((p) => p.id === this.selectedId) ?? null);

  private nextId = 1;
  /** Cancels sent but not yet acknowledged by the Gateway (see handleMessage). */
  private pendingCancelAcks = 0;
  private stopped = false;
  private readonly g: GatewayPort;
  private readonly capture: Capture;
  private readonly player: Player;
  private readonly unlock: () => Promise<void>;
  private readonly accentFor?: SessionDeps['accentFor'];
  private readonly retryPersonasMs: number;

  constructor(deps: SessionDeps) {
    this.g = deps.gateway;
    this.capture = deps.capture;
    this.player = deps.player;
    this.unlock = deps.unlockAudio ?? (async () => {});
    this.accentFor = deps.accentFor;
    this.retryPersonasMs = deps.retryPersonasMs ?? 2000;

    this.g.on('open', ({ reconnect }) => this.onOpen(reconnect));
    this.g.on('close', () => this.onClose());
    this.g.on('message', (message) => this.handleMessage(message));
    this.g.on('audio', (frame) => this.player.push(frame));
    this.capture.on('frame', (pcm) => {
      if (this.phase === 'capturing') this.g.sendAudio(pcm);
    });
    this.capture.on('level', (level) => (this.micLevel = level));
    this.player.on('start', () => {
      if (this.phase !== 'ready') this.phase = 'speaking';
    });
    this.player.on('idle', () => {
      if (this.phase === 'speaking') this.phase = 'ready';
    });
  }

  /** Connect and load personas. Safe to call once at startup. */
  start(): void {
    this.stopped = false;
    this.g.connect();
    void this.loadPersonas();
  }

  stop(): void {
    this.stopped = true;
    this.capture.stop();
    this.player.stop();
    this.g.close();
  }

  // ---- Actions (what the UI calls) ----

  /** Pick a persona and enter its session. Wrap in a view transition at the call site. */
  select(id: string): void {
    void this.unlock();
    this.selectedId = id;
    this.turns = [];
    this.resetTurnState();
    this.g.send({ type: 'select_persona', id });
    this.view = 'session';
  }

  /** Back to the picker. Ignored mid-turn; cancel first. */
  back(): void {
    if (this.phase !== 'ready') return;
    this.view = 'picker';
  }

  async startTalking(): Promise<void> {
    if (this.phase !== 'ready' || !this.selectedId) return;
    void this.unlock();
    this.phase = 'capturing';
    this.g.send({ type: 'talk_start' });
    try {
      await this.capture.start();
    } catch {
      // Permission denied / no device: abandon the turn on both ends.
      this.abandonTurn();
      this.fail('mic_unavailable');
    }
  }

  /** Tap while listening: stop now instead of waiting for the silence detector. */
  finishTalking(): void {
    if (this.phase !== 'capturing') return;
    this.capture.stop();
    this.g.send({ type: 'talk_end' });
    this.phase = 'thinking';
  }

  /** Abort whatever is happening: listening, thinking or speaking. */
  cancel(): void {
    if (this.phase === 'ready') return;
    this.abandonTurn();
  }

  /** Loudness of the assistant's voice right now, 0..1 (poll from an animation frame). */
  outputLevel(): number {
    return this.player.level();
  }

  dismissError(): void {
    this.error = null;
  }

  // ---- Gateway events ----

  private onOpen(reconnect: boolean): void {
    this.connection = 'online';
    this.pendingCancelAcks = 0;
    if (this.error?.code === 'connection_lost') this.error = null;
    if (!reconnect) return;
    // A new socket is a new server-side session: restore the persona the
    // UI is still showing, and refresh the list (the Gateway may have restarted).
    this.resetTurnState();
    if (this.selectedId) this.g.send({ type: 'select_persona', id: this.selectedId });
    void this.loadPersonas();
  }

  private onClose(): void {
    this.connection = 'offline';
    if (this.phase !== 'ready') this.fail('connection_lost');
    this.resetTurnState();
  }

  private handleMessage(message: ServerMessage): void {
    // After a cancel, everything the Gateway sends until it acknowledges
    // (status `done`) belongs to the cancelled turn. Letting it through
    // would let a late "thinking" or "speaking" corrupt the *next* turn if
    // the user taps talk again straight away.
    if (this.pendingCancelAcks > 0) {
      if (message.type === 'status' && message.state === 'done') this.pendingCancelAcks--;
      return;
    }

    switch (message.type) {
      case 'status':
        this.handleStatus(message.state, message.detail ?? undefined);
        break;
      case 'transcript':
        this.turns.push({ id: this.nextId++, transcript: message.text.trim(), reply: null });
        break;
      case 'llm_text': {
        const turn = this.turns.at(-1);
        if (turn) turn.reply = message.text.trim();
        break;
      }
      case 'audio_header':
        if (this.phase !== 'ready') this.player.begin(message as AudioFormat);
        break;
      case 'done':
        this.player.finish();
        // No audio was produced (e.g. empty reply): nothing will play, so
        // the turn is over now rather than when playback ends.
        if (this.phase === 'thinking') this.phase = 'ready';
        break;
      case 'stop_audio':
        this.player.stop();
        if (this.phase === 'speaking') this.phase = 'ready';
        break;
    }
  }

  private handleStatus(state: string, detail: string | undefined): void {
    switch (state) {
      case 'thinking':
        if (this.phase === 'capturing') {
          // The Gateway's silence detector decided the utterance is over.
          this.capture.stop();
          this.phase = 'thinking';
        }
        break;
      case 'speaking':
        if (this.phase !== 'ready') this.phase = 'speaking';
        break;
      case 'error':
        if (detail === 'tts_failed') {
          // Audio already sent still plays; `done` follows.
          this.fail('tts_failed');
        } else {
          this.capture.stop();
          this.phase = 'ready';
          this.fail(detail === 'stt_failed' || detail === 'llm_failed' ? detail : 'llm_failed');
        }
        break;
      // 'listening' (persona selected) and 'done' (cancel ack) need no action.
    }
  }

  // ---- Internals ----

  private abandonTurn(): void {
    this.capture.stop();
    this.player.stop();
    if (this.g.send({ type: 'cancel' })) this.pendingCancelAcks++;
    this.phase = 'ready';
  }

  private resetTurnState(): void {
    this.capture.stop();
    this.player.stop();
    this.phase = 'ready';
  }

  private fail(code: ErrorCode): void {
    this.error = { id: this.nextId++, code, message: ERROR_MESSAGES[code] };
  }

  private async loadPersonas(): Promise<void> {
    while (!this.stopped) {
      try {
        this.personas = await this.g.fetchPersonas();
        this.personasLoaded = true;
        void this.loadAccents();
        return;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, this.retryPersonasMs));
      }
    }
  }

  private async loadAccents(): Promise<void> {
    if (!this.accentFor) return;
    for (const persona of this.personas) {
      const accent = await this.accentFor(persona).catch(() => null);
      if (accent !== null) this.accents[persona.id] = accent;
    }
  }
}
