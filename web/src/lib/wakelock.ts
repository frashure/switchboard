/** Keep the screen on while the app is visible (it's a desk device, often a
 *  tablet that would otherwise dim mid-conversation). Wake locks are released
 *  automatically when the tab is hidden, so re-acquire on return. */
export function keepScreenOn(): void {
  if (!('wakeLock' in navigator)) return;
  let sentinel: WakeLockSentinel | null = null;
  const acquire = async () => {
    if (document.visibilityState !== 'visible' || sentinel) return;
    try {
      sentinel = await navigator.wakeLock.request('screen');
      sentinel.addEventListener('release', () => (sentinel = null));
    } catch {
      /* denied (e.g. low battery) -- not worth surfacing */
    }
  };
  document.addEventListener('visibilitychange', acquire);
  void acquire();
}
