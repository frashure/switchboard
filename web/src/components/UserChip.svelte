<script lang="ts">
  import { fly } from 'svelte/transition';
  import { useSession } from '../lib/context';

  const session = useSession();
  let open = $state(false);

  const user = $derived(session.user);
  const initial = $derived(user?.name.trim()[0]?.toUpperCase() ?? '?');

  function closeOnOutside(event: MouseEvent) {
    if (!(event.target as Element).closest('.user-chip')) open = false;
  }
</script>

<svelte:window onclick={closeOnOutside} onkeydown={(e) => e.key === 'Escape' && (open = false)} />

{#if user}
  <div class="user-chip">
    <button class="chip" aria-haspopup="menu" aria-expanded={open} onclick={() => (open = !open)}>
      <span class="badge">{initial}</span>
      <span class="name">{user.name}</span>
    </button>
    {#if open}
      <div class="menu" role="menu" transition:fly={{ y: -8, duration: 180 }}>
        <p class="who">
          <strong>{user.name}</strong>
          <span>{user.email}</span>
        </p>
        <button role="menuitem" class="signout" onclick={() => session.logout()}>Sign out</button>
      </div>
    {/if}
  </div>
{/if}

<style>
  .user-chip {
    position: relative;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 6px 14px 6px 6px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition: transform 0.25s var(--ease-spring);
  }
  .chip:active {
    transform: scale(0.96);
  }
  .badge {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: 50%;
    background: linear-gradient(145deg, var(--accent-strong), hsl(calc(var(--hue) + 40) 70% 45%));
    font-weight: 700;
    font-size: 0.9rem;
  }
  .name {
    max-width: 14ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.92rem;
    font-weight: 600;
  }
  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 10px);
    z-index: 10;
    min-width: 230px;
    padding: 8px;
    border-radius: 18px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    box-shadow: 0 24px 60px -20px #000;
  }
  .who {
    margin: 0;
    padding: 10px 12px 12px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 6px;
  }
  .who span {
    font-size: 0.85rem;
    color: var(--text-dim);
    overflow-wrap: anywhere;
  }
  .signout {
    width: 100%;
    padding: 11px 12px;
    border: 0;
    border-radius: 12px;
    background: none;
    color: #ffb3bc;
    text-align: left;
    font-weight: 600;
    cursor: pointer;
  }
  .signout:hover {
    background: hsl(355 85% 62% / 0.12);
  }
</style>
