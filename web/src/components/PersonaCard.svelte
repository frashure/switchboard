<script lang="ts">
  import { cubicOut } from 'svelte/easing';
  import { fly } from 'svelte/transition';
  import type { Persona } from '../lib/protocol';
  import Avatar from './Avatar.svelte';

  let {
    persona,
    hue,
    index,
    morph,
    onselect,
  }: {
    persona: Persona;
    hue: number | undefined;
    index: number;
    morph: boolean;
    onselect: (id: string) => void;
  } = $props();
</script>

<button
  class="card tint"
  style:--hue={hue}
  in:fly={{ y: 20, duration: 520, delay: 80 + index * 60, easing: cubicOut }}
  onclick={() => onselect(persona.id)}
>
  <span class="avatar-ring">
    <Avatar {persona} size="clamp(76px, 15vmin, 112px)" {morph} />
  </span>
  <span class="name">{persona.display_name}</span>
</button>

<style>
  .card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
    padding: 22px 14px 18px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: linear-gradient(180deg, var(--surface-2), var(--surface));
    cursor: pointer;
    transition:
      transform 0.35s var(--ease-spring),
      box-shadow 0.35s var(--ease-out),
      border-color 0.35s;
  }
  .avatar-ring {
    display: grid;
    padding: 4px;
    border-radius: 50%;
    box-shadow: 0 0 0 2px var(--accent-soft);
    transition: box-shadow 0.35s var(--ease-out);
  }
  .name {
    font-size: 1.05rem;
    font-weight: 600;
    letter-spacing: 0.01em;
  }
  @media (hover: hover) {
    .card:hover {
      transform: translateY(-5px);
      border-color: var(--accent-soft);
      box-shadow: 0 18px 40px -18px var(--accent-soft);
    }
    .card:hover .avatar-ring {
      box-shadow: 0 0 0 3px var(--accent);
    }
  }
  .card:active {
    transform: scale(0.96);
  }
</style>
