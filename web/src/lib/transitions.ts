import { tick } from 'svelte';

/** Runs a state change inside the browser's View Transitions API when
 *  available, so elements sharing a `view-transition-name` morph between
 *  their old and new positions (e.g. a persona card's avatar flying into the
 *  session header). Without support, or with reduced motion requested, the
 *  change is applied immediately and CSS provides a simple fade instead. */
export async function withViewTransition(update: () => void): Promise<void> {
  const supported = typeof document.startViewTransition === 'function';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!supported || reduced) {
    update();
    return;
  }
  const transition = document.startViewTransition(async () => {
    update();
    await tick(); // let Svelte flush the DOM before the browser snapshots the new state
  });
  await transition.finished.catch(() => {});
}
