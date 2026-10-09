<script lang="ts">
  import ConnectionBanner from './components/ConnectionBanner.svelte';
  import LoginView from './components/LoginView.svelte';
  import PersonaPicker from './components/PersonaPicker.svelte';
  import SessionView from './components/SessionView.svelte';
  import Toast from './components/Toast.svelte';
  import { provideSession } from './lib/context';
  import type { SessionStore } from './lib/session.svelte';

  let { session }: { session: SessionStore } = $props();
  // The store is created once in main.ts and never swapped, so capturing it here is correct.
  // svelte-ignore state_referenced_locally
  provideSession(session);

  const hue = $derived(session.view === 'session' && session.selectedId ? session.accents[session.selectedId] : undefined);
</script>

<div class="ambient tint" style:--hue={hue} data-view={session.view}></div>

<main>
  {#if session.auth === 'checking'}
    <div class="splash" aria-busy="true" aria-label="Connecting">
      <img src="./icon.svg" alt="" width="84" height="84" />
    </div>
  {:else if session.auth === 'signed_out'}
    <LoginView />
  {:else if session.view === 'session' && session.selected}
    <SessionView />
  {:else}
    <PersonaPicker />
  {/if}
</main>

{#if session.auth === 'signed_in'}
  <ConnectionBanner />
{/if}
<Toast />

<style>
  /* The persona's colour bleeds into the background; --hue is animatable
     (see @property in app.css), so switching persona fades the tint. */
  .ambient {
    position: fixed;
    inset: 0;
    z-index: -1;
    background:
      radial-gradient(70% 55% at 50% 22%, var(--accent-faint), transparent 70%),
      radial-gradient(45% 40% at 88% 92%, hsl(var(--hue) 70% 50% / 0.1), transparent 70%);
    transition:
      --hue 0.9s var(--ease-out),
      opacity 0.6s;
  }
  .ambient[data-view='picker'] {
    opacity: 0.6;
  }
  main {
    height: 100%;
  }
  .splash {
    height: 100%;
    display: grid;
    place-items: center;
  }
  .splash img {
    border-radius: 22px;
    animation: breathe 1.8s ease-in-out infinite;
  }
  @keyframes breathe {
    50% {
      transform: scale(1.07);
      opacity: 0.8;
    }
  }
</style>
