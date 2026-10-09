<script lang="ts">
  import type { Persona } from '../lib/protocol';

  let {
    persona,
    size = '64px',
    morph = false,
  }: {
    persona: Pick<Persona, 'display_name' | 'avatar'>;
    size?: string;
    /** Tag this avatar as the shared element of a view transition. */
    morph?: boolean;
  } = $props();

  const initial = $derived(persona.display_name.trim()[0]?.toUpperCase() ?? '?');
</script>

<span class="avatar" style:width={size} style:height={size} style:view-transition-name={morph ? 'persona-avatar' : 'none'}>
  {#if persona.avatar}
    <img src={persona.avatar} alt="" draggable="false" />
  {:else}
    <span class="initial">{initial}</span>
  {/if}
</span>

<style>
  .avatar {
    display: grid;
    place-items: center;
    border-radius: 50%;
    overflow: hidden;
    flex: none;
    background: linear-gradient(145deg, var(--accent-strong), hsl(calc(var(--hue) + 40) 70% 45%));
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    pointer-events: none;
  }
  .initial {
    font-weight: 700;
    font-size: 2.2em;
    color: #fff;
  }
</style>
