# Switchboard web app

The primary client: a Svelte 5 + TypeScript PWA that talks to the Gateway over
its WebSocket protocol. It runs in any modern browser, installs to a tablet or
phone home screen, and is served by the Gateway itself (one origin, so one
HTTPS endpoint is enough).

```bash
npm install
npm run dev      # http://localhost:5173, proxies /profiles and /ws to the Gateway (:8090)
npm run build    # -> dist/ (the Gateway serves it via SWITCHBOARD_STATIC_DIR)
npm test         # unit tests (vitest)
npm run check    # type-check .ts and .svelte
npm run e2e      # headless Chrome + fake microphone walkthrough with screenshots (needs a Gateway)
```

Use `?gateway=host:port` to point a dev build at a different Gateway.

## How it is put together

```
components/  (dumb: render state, call actions)
     |
lib/session.svelte.ts   SessionStore: ALL app logic -- turn state machine, history
     |   depends only on three small interfaces:
     +-- lib/gateway.ts        GatewayClient   WebSocket, reconnect, typed messages
     +-- lib/audio/capture.ts  Capture         microphone -> 16 kHz PCM frames
     +-- lib/audio/player.ts   Player          gapless streaming playback
lib/protocol.ts   the Gateway protocol as TypeScript types
```

- **Components never touch the network or audio.** They read `SessionStore`
  state (`phase`, `turns`, `personas`...) and call its actions (`select`,
  `startTalking`, `cancel`...). Restyling or replacing the UI cannot break the
  protocol handling.
- **The store never touches the browser.** The gateway, mic and speaker are
  injected (`SessionDeps`), which is how `tests/session.test.ts` drives full
  turns, cancels, reconnects and error paths without a browser.
- **Per-persona colour** is a single hue (`--hue`). Every accent (glow, ring,
  bubble tint) is derived from it in `app.css`; give any element that sets
  `--hue` the `tint` class so its derived colours are recomputed there.
- **Transitions:** `lib/transitions.ts` wraps state changes in the View
  Transitions API so the persona avatar morphs between the picker and the
  session screen; browsers without it (or with reduced motion) get a CSS fade.

## Extending it

| To... | Do this |
|---|---|
| Change the look | Tokens are in `src/app.css`; component styles are scoped in each `.svelte` file |
| Add a screen (settings, history...) | Add a value to `View` in `session.svelte.ts`, a component, and a branch in `App.svelte`. Wrap the switch in `withViewTransition` for the animated change |
| Add a field to a persona (e.g. a description) | Extend `Persona` in `protocol.ts` (and the Gateway's `/profiles`); read it in `PersonaCard.svelte` |
| Handle a new Gateway message | Add it to the `ServerMessage` union in `protocol.ts`; TypeScript then points at `handleMessage` in the store |
| Add a new turn state | Extend `Phase`; the `Record<Phase, ...>` in `SessionView.svelte` will fail to compile until you give it a caption |
| Use a different mic/speaker (e.g. a Bluetooth device layer, or a test double) | Implement `Capture` / `Player` and pass it to `SessionStore` in `main.ts` |
| Persist something (last persona, settings) | Add it to the store and read/write `localStorage` there, not in components |

## Things worth knowing

- **Cancel race.** After a cancel, the Gateway may still send messages from the
  cancelled turn. The store ignores everything until the Gateway acknowledges
  the cancel (`pendingCancelAcks`), so a quick re-tap can't be corrupted by the
  previous turn. There is a test for this.
- **Audio:** playback is scheduled frame by frame on the Web Audio clock so
  sentences join without gaps while the rest of the answer is still arriving.
  Capture uses an AudioWorklet and a streaming resampler (state carries across
  chunks); echo cancellation is requested so a tablet doesn't hear itself.
- **Service worker:** it caches the app shell only. Its navigation fallback
  explicitly excludes `/profiles`, `/ws`, `/lvgl/` and `/virtual_device/`
  (see `vite.config.ts`), which the Gateway serves alongside this app.
- **Icons:** edit `public/icon.svg`, then `npm run icons`.
