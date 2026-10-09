<script lang="ts">
  import { cubicOut } from 'svelte/easing';
  import { fly } from 'svelte/transition';
  import { onMount } from 'svelte';
  import { useSession } from '../lib/context';

  const session = useSession();

  let email = $state('');
  let password = $state('');
  let reveal = $state(false);
  let emailInput: HTMLInputElement | undefined = $state();

  onMount(() => emailInput?.focus());

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    await session.login(email, password);
    if (session.loginError) password = ''; // never leave a rejected password sitting in the field
  }
</script>

<section class="login view-enter">
  <form class="panel" onsubmit={submit} in:fly={{ y: 26, duration: 560, easing: cubicOut }}>
    <img class="logo" src="./icon.svg" alt="" width="76" height="76" />
    <h1>Switchboard</h1>
    <p class="sub">Sign in with your Open WebUI account</p>

    {#if session.loginNotice}
      <p class="notice" role="status">{session.loginNotice}</p>
    {/if}

    <label>
      <span>Email</span>
      <input
        bind:this={emailInput}
        bind:value={email}
        type="email"
        name="email"
        autocomplete="username"
        inputmode="email"
        autocapitalize="none"
        spellcheck="false"
        required
      />
    </label>

    <label>
      <span>Password</span>
      <span class="password">
        <input
          bind:value={password}
          type={reveal ? 'text' : 'password'}
          name="password"
          autocomplete="current-password"
          required
        />
        <button type="button" class="reveal" aria-label={reveal ? 'Hide password' : 'Show password'} onclick={() => (reveal = !reveal)}>
          {reveal ? 'Hide' : 'Show'}
        </button>
      </span>
    </label>

    {#key session.loginError}
      {#if session.loginError}
        <p class="error" role="alert">{session.loginError}</p>
      {/if}
    {/key}

    <button class="submit" type="submit" disabled={session.loginBusy || !email || !password}>
      {#if session.loginBusy}<span class="spinner" aria-hidden="true"></span> Signing in…{:else}Sign in{/if}
    </button>
  </form>
</section>

<style>
  .login {
    height: 100%;
    display: grid;
    place-items: center;
    padding: 24px;
    overflow-y: auto;
  }
  .panel {
    width: min(100%, 400px);
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: clamp(24px, 5vw, 36px);
    border-radius: 28px;
    border: 1px solid var(--border);
    background: linear-gradient(180deg, var(--surface-2), var(--surface));
    box-shadow: 0 40px 90px -40px #000;
  }
  .logo {
    align-self: center;
    border-radius: 20px;
  }
  h1 {
    margin: 0;
    text-align: center;
    font-size: 1.7rem;
    letter-spacing: -0.02em;
  }
  .sub {
    margin: -8px 0 6px;
    text-align: center;
    color: var(--text-dim);
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-dim);
  }
  input {
    width: 100%;
    padding: 14px 16px;
    border-radius: 14px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    /* 16px+ keeps iOS from zooming the page when a field is focused. */
    font: inherit;
    font-size: 1rem;
    font-weight: 400;
    transition:
      border-color 0.2s,
      box-shadow 0.2s;
  }
  input:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 4px var(--accent-faint);
  }
  .password {
    position: relative;
    display: block;
  }
  .password input {
    padding-right: 76px;
  }
  .reveal {
    position: absolute;
    right: 6px;
    top: 50%;
    translate: 0 -50%;
    padding: 8px 12px;
    border: 0;
    border-radius: 10px;
    background: none;
    color: var(--text-dim);
    font-size: 0.85rem;
    cursor: pointer;
  }
  .error,
  .notice {
    margin: 0;
    padding: 11px 14px;
    border-radius: 12px;
    font-size: 0.92rem;
  }
  .error {
    color: #ffb3bc;
    background: hsl(355 85% 62% / 0.12);
    border: 1px solid hsl(355 85% 62% / 0.35);
    animation: shake 0.42s var(--ease-out);
  }
  .notice {
    background: var(--accent-faint);
    border: 1px solid var(--accent-soft);
  }
  @keyframes shake {
    20%,
    60% {
      transform: translateX(-6px);
    }
    40%,
    80% {
      transform: translateX(6px);
    }
  }
  .submit {
    margin-top: 4px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 15px;
    border: 0;
    border-radius: 14px;
    background: linear-gradient(135deg, var(--accent-strong), hsl(calc(var(--hue) + 35) 80% 58%));
    color: #fff;
    font-weight: 650;
    font-size: 1.02rem;
    cursor: pointer;
    transition:
      transform 0.25s var(--ease-spring),
      opacity 0.2s;
  }
  .submit:active:not(:disabled) {
    transform: scale(0.98);
  }
  .submit:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgba(255, 255, 255, 0.35);
    border-top-color: #fff;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
