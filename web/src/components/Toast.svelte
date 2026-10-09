<script lang="ts">
  import { cubicOut } from 'svelte/easing';
  import { fly } from 'svelte/transition';
  import { useSession } from '../lib/context';

  const session = useSession();

  $effect(() => {
    if (!session.error) return;
    const timer = setTimeout(() => session.dismissError(), 5000);
    return () => clearTimeout(timer);
  });
</script>

{#if session.error}
  {#key session.error.id}
    <div class="toast" role="alert" transition:fly={{ y: 30, duration: 320, easing: cubicOut }}>
      <span>{session.error.message}</span>
      <button aria-label="Dismiss" onclick={() => session.dismissError()}>✕</button>
    </div>
  {/key}
{/if}

<style>
  .toast {
    position: fixed;
    bottom: max(22px, env(safe-area-inset-bottom));
    left: 50%;
    translate: -50% 0;
    z-index: 20;
    max-width: min(92vw, 460px);
    padding: 13px 20px;
    border-radius: 16px;
    border: 1px solid hsl(355 85% 62% / 0.5);
    background: var(--surface-2);
    color: var(--text);
    font-size: 0.95rem;
    box-shadow: 0 14px 40px -12px #000;
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .toast button {
    flex: none;
    border: 0;
    background: none;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px 6px;
  }
</style>
