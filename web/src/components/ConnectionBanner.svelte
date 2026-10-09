<script lang="ts">
  import { fly } from 'svelte/transition';
  import { useSession } from '../lib/context';

  const session = useSession();

  // Debounced so a brief reconnect (or the initial connect) doesn't flash a banner.
  let visible = $state(false);
  $effect(() => {
    if (session.connection !== 'offline') {
      visible = false;
      return;
    }
    const timer = setTimeout(() => (visible = true), 1200);
    return () => clearTimeout(timer);
  });
</script>

{#if visible}
  <div class="banner" role="status" transition:fly={{ y: -24, duration: 300 }}>
    <span class="dot"></span> Reconnecting to the Gateway…
  </div>
{/if}

<style>
  .banner {
    position: fixed;
    top: max(12px, env(safe-area-inset-top));
    left: 50%;
    translate: -50% 0;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 18px;
    border-radius: 999px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    font-size: 0.92rem;
    box-shadow: 0 10px 30px -10px #000;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #ffb454;
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      opacity: 0.35;
      transform: scale(0.8);
    }
  }
</style>
