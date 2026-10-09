<script lang="ts">
  import { cubicOut } from 'svelte/easing';
  import { fade, fly } from 'svelte/transition';
  import { renderMarkdown } from '../lib/markdown';
  import type { Turn } from '../lib/session.svelte';

  let { turns }: { turns: Turn[] } = $props();

  let scroller: HTMLDivElement | undefined = $state();

  // Follow the conversation as turns arrive and replies fill in.
  $effect(() => {
    turns.length;
    turns.at(-1)?.reply;
    scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
  });
</script>

<div class="conversation" bind:this={scroller}>
  {#if turns.length === 0}
    <p class="empty" in:fade={{ duration: 400, delay: 250 }}>Tap the circle and ask me anything.</p>
  {/if}
  {#each turns as turn (turn.id)}
    <div class="turn">
      <p class="bubble you" in:fly={{ y: 14, duration: 380, easing: cubicOut }}>{turn.transcript}</p>
      {#if turn.reply !== null}
        <div class="bubble assistant prose" in:fly={{ y: 14, duration: 420, easing: cubicOut }}>
          {@html renderMarkdown(turn.reply)}
        </div>
      {:else}
        <p class="bubble assistant typing" in:fade={{ duration: 200 }} aria-label="Thinking">
          <span></span><span></span><span></span>
        </p>
      {/if}
    </div>
  {/each}
</div>

<style>
  .conversation {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding: 4px 2px 8px;
    mask-image: linear-gradient(to bottom, transparent 0, #000 18px);
    -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 18px);
  }
  .turn {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .bubble {
    margin: 0;
    max-width: 92%;
    padding: 12px 16px;
    border-radius: 18px;
    line-height: 1.45;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    user-select: text;
  }
  .you {
    align-self: flex-end;
    background: var(--accent-faint);
    border: 1px solid var(--accent-soft);
    border-bottom-right-radius: 6px;
  }
  .assistant {
    align-self: flex-start;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-bottom-left-radius: 6px;
    font-size: 1.04rem;
  }
  /* Rendered markdown (see lib/markdown.ts). {@html} content isn't scoped, hence :global. */
  .prose {
    white-space: normal;
  }
  .prose :global(p) {
    margin: 0 0 0.7em;
  }
  .prose :global(p:last-child),
  .prose :global(ul:last-child),
  .prose :global(ol:last-child),
  .prose :global(pre:last-child) {
    margin-bottom: 0;
  }
  .prose :global(.heading) {
    font-weight: 700;
  }
  .prose :global(ul),
  .prose :global(ol) {
    margin: 0 0 0.7em;
    padding-left: 1.3em;
  }
  .prose :global(li) {
    margin: 0.2em 0;
  }
  .prose :global(code) {
    padding: 0.1em 0.4em;
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.08);
    font-size: 0.92em;
  }
  .prose :global(pre) {
    margin: 0 0 0.7em;
    padding: 12px 14px;
    overflow-x: auto;
    border-radius: 12px;
    background: rgba(0, 0, 0, 0.35);
  }
  .prose :global(pre code) {
    padding: 0;
    background: none;
  }
  .prose :global(a) {
    color: var(--accent);
  }

  .empty {
    margin: auto;
    color: var(--text-dim);
    text-align: center;
  }
  .typing {
    display: flex;
    gap: 6px;
    padding: 16px 18px;
  }
  .typing span {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-dim);
    animation: dot 1.2s ease-in-out infinite;
  }
  .typing span:nth-child(2) {
    animation-delay: 0.15s;
  }
  .typing span:nth-child(3) {
    animation-delay: 0.3s;
  }
  @keyframes dot {
    30% {
      transform: translateY(-5px);
      opacity: 1;
    }
    0%,
    60%,
    100% {
      opacity: 0.4;
    }
  }
</style>
