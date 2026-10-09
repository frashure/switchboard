/** Minimal typed event emitter -- enough for the adapters here, no dependency. */
export class Emitter<Events extends Record<string, unknown>> {
  private handlers: { [K in keyof Events]?: Set<(payload: Events[K]) => void> } = {};

  on<K extends keyof Events>(event: K, handler: (payload: Events[K]) => void): () => void {
    (this.handlers[event] ??= new Set()).add(handler);
    return () => this.handlers[event]?.delete(handler);
  }

  protected emit<K extends keyof Events>(event: K, payload: Events[K]): void {
    this.handlers[event]?.forEach((handler) => handler(payload));
  }
}
