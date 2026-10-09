<script lang="ts">
  import { fade } from 'svelte/transition';
  import { useSession } from '../lib/context';
  import type { Phase } from '../lib/session.svelte';
  import { withViewTransition } from '../lib/transitions';
  import Conversation from './Conversation.svelte';
  import Orb from './Orb.svelte';

  const session = useSession();

  // Guaranteed by App: this view only renders with a selected persona.
  const persona = $derived(session.selected!);
  const hue = $derived(session.selectedId ? session.accents[session.selectedId] : undefined);

  const CAPTIONS: Record<Phase, { title: string; sub: string; label: string }> = {
    ready: { title: 'Tap to talk', sub: "I'll know when you stop speaking", label: 'Start talking' },
    capturing: { title: 'Listening…', sub: "Tap when you're done", label: 'Send what I said' },
    thinking: { title: 'Thinking…', sub: 'Tap to cancel', label: 'Cancel' },
    speaking: { title: 'Speaking…', sub: 'Tap to stop', label: 'Stop speaking' },
  };
  const caption = $derived(CAPTIONS[session.phase]);

  function onTap() {
    if (session.phase === 'ready') void session.startTalking();
    else if (session.phase === 'capturing') session.finishTalking();
    else session.cancel();
  }

  // Output loudness has no event to hook, so sample it per frame -- only
  // while the assistant is actually speaking.
  let outputLevel = $state(0);
  $effect(() => {
    if (session.phase !== 'speaking') {
      outputLevel = 0;
      return;
    }
    let frame = requestAnimationFrame(function sample() {
      outputLevel = session.outputLevel();
      frame = requestAnimationFrame(sample);
    });
    return () => cancelAnimationFrame(frame);
  });
</script>

<section class="session tint view-enter" style:--hue={hue}>
  <header>
    <button
      class="back"
      disabled={session.phase !== 'ready'}
      aria-label="Change persona"
      onclick={() => withViewTransition(() => session.back())}
    >
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 5l-7 7 7 7" />
      </svg>
      <span>People</span>
    </button>
    <h2>{persona.display_name}</h2>
    <span class="spacer"></span>
  </header>

  <div class="body">
    <div class="stage">
      <Orb
        {persona}
        phase={session.phase}
        micLevel={session.micLevel}
        {outputLevel}
        label={caption.label}
        {onTap}
      />
      <div class="caption" aria-live="polite">
        <p class="title">{caption.title}</p>
        <p class="sub">{caption.sub}</p>
      </div>
      <div class="actions">
        {#if session.phase !== 'ready'}
          <button class="secondary" transition:fade={{ duration: 180 }} onclick={() => session.cancel()}>
            {session.phase === 'speaking' ? 'Stop' : 'Cancel'}
          </button>
        {/if}
      </div>
    </div>

    <div class="panel">
      <Conversation turns={session.turns} />
    </div>
  </div>
</section>

<style>
  .session {
    height: 100%;
    display: flex;
    flex-direction: column;
    padding: max(14px, env(safe-area-inset-top)) clamp(16px, 4vw, 40px) max(18px, env(safe-area-inset-bottom));
    gap: 8px;
    max-width: 1200px;
    margin: 0 auto;
  }
  header {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    min-height: 48px;
  }
  h2 {
    margin: 0;
    font-size: 1.15rem;
    font-weight: 650;
    text-align: center;
  }
  .back {
    justify-self: start;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 10px 14px 10px 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition:
      opacity 0.2s,
      transform 0.2s var(--ease-spring);
  }
  .back:active {
    transform: scale(0.95);
  }
  .back:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .body {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 14px;
  }
  .stage {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    padding-top: 6px;
  }
  .caption {
    text-align: center;
    min-height: 3.4rem;
  }
  .title {
    margin: 0;
    font-size: 1.35rem;
    font-weight: 650;
  }
  .sub {
    margin: 4px 0 0;
    color: var(--text-dim);
    font-size: 0.95rem;
  }
  .actions {
    min-height: 44px;
  }
  .secondary {
    padding: 11px 26px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--text);
    font-weight: 600;
    cursor: pointer;
  }
  .secondary:active {
    transform: scale(0.96);
  }

  .panel {
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  /* Landscape tablets / desktops: orb on the left, conversation on the right. */
  @media (min-aspect-ratio: 5/4) and (min-width: 700px) {
    .body {
      grid-template-rows: none;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: clamp(20px, 4vw, 56px);
    }
    .stage {
      padding-top: 0;
    }
    .panel {
      padding: 18px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: linear-gradient(180deg, var(--surface), transparent 140%);
    }
  }
</style>
