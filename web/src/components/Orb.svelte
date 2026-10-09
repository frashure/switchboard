<script lang="ts">
  import type { Persona } from '../lib/protocol';
  import type { Phase } from '../lib/session.svelte';
  import Avatar from './Avatar.svelte';

  let {
    persona,
    phase,
    micLevel,
    outputLevel,
    label,
    onTap,
  }: {
    persona: Persona;
    phase: Phase;
    micLevel: number;
    outputLevel: number;
    label: string;
    onTap: () => void;
  } = $props();

  /** One 0..1 number drives the audio-reactive scale: the mic while
   *  listening, the assistant's voice while speaking. */
  const level = $derived(
    phase === 'capturing' ? Math.min(1, micLevel * 6) : phase === 'speaking' ? Math.min(1, outputLevel * 3) : 0,
  );
</script>

<button class="orb" data-phase={phase} style:--level={level} aria-label={label} onclick={onTap}>
  <span class="glow"></span>
  <span class="ripple r1"></span>
  <span class="ripple r2"></span>
  <span class="ripple r3"></span>
  <span class="spinner"></span>
  <span class="face"><Avatar {persona} size="100%" morph /></span>
</button>

<style>
  .orb {
    --level: 0;
    position: relative;
    width: clamp(190px, 40vmin, 340px);
    aspect-ratio: 1;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: none;
    cursor: pointer;
  }
  .orb > span {
    position: absolute;
    border-radius: 50%;
    pointer-events: none;
  }

  .face {
    inset: 9%;
    overflow: hidden;
    box-shadow:
      0 0 0 3px var(--accent-soft),
      0 28px 80px -26px var(--accent);
    transform: scale(calc(1 + var(--level) * 0.06));
    transition:
      transform 0.12s linear,
      box-shadow 0.4s;
  }

  .glow {
    inset: -14%;
    background: radial-gradient(closest-side, var(--accent-soft), transparent);
    opacity: 0.55;
    transform: scale(calc(1 + var(--level) * 0.45));
    transition:
      transform 0.1s linear,
      opacity 0.5s;
  }
  /* Breathing only when nothing audio-reactive is driving the scale. */
  .orb:is([data-phase='ready'], [data-phase='thinking']) .glow {
    animation: breathe 5s ease-in-out infinite;
  }
  @keyframes breathe {
    50% {
      transform: scale(1.08);
      opacity: 0.85;
    }
  }

  /* Listening: red-tinted so it's obvious the mic is live. */
  .orb[data-phase='capturing'] .glow {
    background: radial-gradient(closest-side, hsl(355 85% 62% / 0.5), transparent);
    opacity: 0.95;
  }
  .orb[data-phase='capturing'] .face {
    box-shadow:
      0 0 0 4px hsl(355 85% 62% / 0.7),
      0 28px 80px -26px hsl(355 85% 62%);
  }

  /* Thinking: a rotating arc around the avatar. */
  .spinner {
    inset: 2%;
    opacity: 0;
    transition: opacity 0.3s;
    background: conic-gradient(from 0deg, transparent 0 55%, var(--accent) 100%);
    -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 5px), #000 calc(100% - 4px));
    mask: radial-gradient(farthest-side, transparent calc(100% - 5px), #000 calc(100% - 4px));
  }
  .orb[data-phase='thinking'] .spinner {
    opacity: 1;
    animation: spin 1.1s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* Speaking: ripples expanding outward. */
  .ripple {
    inset: 9%;
    border: 2px solid var(--accent);
    opacity: 0;
  }
  .orb[data-phase='speaking'] .ripple {
    animation: ripple 2.6s var(--ease-out) infinite;
  }
  .orb[data-phase='speaking'] .r2 {
    animation-delay: 0.87s;
  }
  .orb[data-phase='speaking'] .r3 {
    animation-delay: 1.74s;
  }
  @keyframes ripple {
    from {
      transform: scale(1);
      opacity: 0.55;
    }
    to {
      transform: scale(1.6);
      opacity: 0;
    }
  }

  .orb:active .face {
    transform: scale(0.97);
  }
</style>
