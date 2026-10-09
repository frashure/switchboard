<script lang="ts">
  import { tick } from 'svelte';
  import { useSession } from '../lib/context';
  import { withViewTransition } from '../lib/transitions';
  import PersonaCard from './PersonaCard.svelte';
  import UserChip from './UserChip.svelte';

  const session = useSession();

  /** The card that was just tapped: it must carry the shared-element tag
   *  *before* the transition snapshots the old state. */
  let pendingId = $state<string | null>(null);

  async function choose(id: string) {
    pendingId = id;
    await tick();
    await withViewTransition(() => session.select(id));
    pendingId = null;
  }
</script>

<section class="picker view-enter">
  <header>
    <div class="titles">
      <p class="eyebrow">Switchboard</p>
      <h1>Who would you like to talk to?</h1>
    </div>
    <UserChip />
  </header>

  {#if !session.personasLoaded}
    <div class="grid" aria-busy="true">
      {#each { length: 6 } as _, i (i)}
        <div class="skeleton" style:animation-delay="{i * 120}ms"></div>
      {/each}
    </div>
    <p class="hint">{session.connection === 'offline' ? 'Waiting for the Gateway…' : 'Loading personas…'}</p>
  {:else if session.personas.length === 0}
    <p class="hint">No personas found. Create a preset model in Open WebUI and it will appear here.</p>
  {:else}
    <div class="grid">
      {#each session.personas as persona, i (persona.id)}
        <PersonaCard
          {persona}
          index={i}
          hue={session.accents[persona.id]}
          morph={(pendingId ?? session.selectedId) === persona.id}
          onselect={choose}
        />
      {/each}
    </div>
  {/if}
</section>

<style>
  .picker {
    height: 100%;
    overflow-y: auto;
    padding: max(28px, env(safe-area-inset-top)) clamp(18px, 5vw, 56px) 40px;
    display: flex;
    flex-direction: column;
    gap: 28px;
    max-width: 1100px;
    margin: 0 auto;
  }
  header {
    padding-top: clamp(4px, 3vh, 28px);
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }
  .eyebrow {
    margin: 0 0 8px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--accent);
  }
  h1 {
    margin: 0;
    font-size: clamp(1.7rem, 4.6vw, 2.6rem);
    line-height: 1.12;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(clamp(140px, 22vw, 190px), 1fr));
    gap: clamp(12px, 2.2vw, 22px);
  }
  .hint {
    margin: 0;
    color: var(--text-dim);
    text-align: center;
  }
  .skeleton {
    height: clamp(170px, 26vmin, 210px);
    border-radius: var(--radius);
    background: linear-gradient(110deg, var(--surface) 30%, var(--surface-2) 50%, var(--surface) 70%);
    background-size: 200% 100%;
    animation: shimmer 1.6s linear infinite;
  }
  @keyframes shimmer {
    to {
      background-position: -200% 0;
    }
  }
</style>
