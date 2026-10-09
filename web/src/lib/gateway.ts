import { Emitter } from './emitter';
import type { GatewayEndpoints } from './config';
import type { ClientMessage, Persona, ServerMessage } from './protocol';

/** The Gateway says this browser isn't signed in (HTTP 401). */
export class UnauthorizedError extends Error {}

/** WebSocket close codes the Gateway uses to refuse a connection. */
const WS_UNAUTHORIZED = 4401;
const WS_FORBIDDEN = 4403;

export type GatewayEvents = {
  /** Socket opened. `reconnect` is true for every open after the first. */
  open: { reconnect: boolean };
  close: void;
  /** The Gateway refused the socket because there is no valid session. */
  unauthorized: void;
  message: ServerMessage;
  /** One binary TTS frame (PCM, format given by the preceding audio_header). */
  audio: ArrayBuffer;
};

export interface GatewayDeps {
  WebSocketImpl?: typeof WebSocket;
  fetchImpl?: typeof fetch;
  /** Reconnect backoff bounds, ms. */
  minBackoffMs?: number;
  maxBackoffMs?: number;
}

/** Owns the WebSocket: connect, automatic reconnect with backoff, typed
 *  send/receive. Knows nothing about the app's state machine. */
export class GatewayClient extends Emitter<GatewayEvents> {
  private ws: WebSocket | null = null;
  private everOpened = false;
  private closedByUser = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private backoffMs: number;
  private readonly minBackoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly WS: typeof WebSocket;
  private readonly fetchImpl: typeof fetch;

  constructor(private readonly endpoints: GatewayEndpoints, deps: GatewayDeps = {}) {
    super();
    this.WS = deps.WebSocketImpl ?? WebSocket;
    this.fetchImpl = deps.fetchImpl ?? ((...args) => fetch(...args));
    this.minBackoffMs = deps.minBackoffMs ?? 1000;
    this.maxBackoffMs = deps.maxBackoffMs ?? 10000;
    this.backoffMs = this.minBackoffMs;
  }

  get connected(): boolean {
    return this.ws?.readyState === this.WS.OPEN;
  }

  connect(): void {
    this.closedByUser = false;
    const ws = new this.WS(this.endpoints.ws);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      this.backoffMs = this.minBackoffMs;
      const reconnect = this.everOpened;
      this.everOpened = true;
      this.emit('open', { reconnect });
    };
    ws.onclose = (event: CloseEvent) => {
      if (this.ws === ws) this.ws = null;
      this.emit('close', undefined);
      if (event.code === WS_UNAUTHORIZED || event.code === WS_FORBIDDEN) {
        // Reconnecting can't fix a missing session; the user has to sign in.
        this.closedByUser = true;
        this.emit('unauthorized', undefined);
        return;
      }
      if (this.closedByUser) return;
      this.retryTimer = setTimeout(() => this.connect(), this.backoffMs);
      this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs);
    };
    ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) this.emit('audio', event.data);
      else this.emit('message', JSON.parse(event.data as string) as ServerMessage);
    };
  }

  close(): void {
    this.closedByUser = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
  }

  /** Dropped (not thrown) while disconnected: a tap during a reconnect
   *  shouldn't surface as an exception. Returns whether it was sent. */
  send(message: ClientMessage): boolean {
    if (!this.connected) return false;
    this.ws!.send(JSON.stringify(message));
    return true;
  }

  sendAudio(pcm: ArrayBuffer): boolean {
    if (!this.connected) return false;
    this.ws!.send(pcm);
    return true;
  }

  async fetchPersonas(): Promise<Persona[]> {
    const res = await this.fetchImpl(`${this.endpoints.http}/profiles`, { credentials: 'same-origin' });
    if (res.status === 401) throw new UnauthorizedError();
    if (!res.ok) throw new Error(`GET /profiles -> ${res.status}`);
    const body = (await res.json()) as { personas: Persona[] };
    return body.personas;
  }
}
