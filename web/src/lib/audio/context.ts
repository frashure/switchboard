let context: AudioContext | null = null;

/** One AudioContext for the whole app (capture and playback share it). */
export function getAudioContext(): AudioContext {
  context ??= new AudioContext();
  return context;
}

/** Browsers only let audio start from a user gesture: call this from the
 *  first tap handler so later, event-driven playback is allowed. */
export async function unlockAudio(): Promise<void> {
  const ctx = getAudioContext();
  if (ctx.state === 'suspended') await ctx.resume();
}
