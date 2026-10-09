import { mount } from 'svelte';
import { registerSW } from 'virtual:pwa-register';
import App from './App.svelte';
import './app.css';
import { MicCapture } from './lib/audio/capture';
import { unlockAudio } from './lib/audio/context';
import { StreamPlayer } from './lib/audio/player';
import { resolveEndpoints } from './lib/config';
import { GatewayClient } from './lib/gateway';
import { SessionStore } from './lib/session.svelte';
import { accentFromAvatar } from './lib/theme';
import { keepScreenOn } from './lib/wakelock';

const session = new SessionStore({
  gateway: new GatewayClient(resolveEndpoints()),
  capture: new MicCapture(),
  player: new StreamPlayer(),
  unlockAudio,
  accentFor: accentFromAvatar,
});

session.start();
keepScreenOn();
registerSW({ immediate: true });

mount(App, { target: document.getElementById('app')!, props: { session } });
