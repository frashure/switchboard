/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { VitePWA } from 'vite-plugin-pwa';

// `npm run dev` proxies the Gateway's endpoints so the app runs same-origin
// in development exactly as it does when the Gateway serves the build.
const GATEWAY = process.env.GATEWAY_URL ?? 'http://localhost:8090';

export default defineConfig({
  // Relative asset URLs: the build works whether served at "/" (Gateway) or a subpath.
  base: './',
  plugins: [
    svelte(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Switchboard',
        short_name: 'Switchboard',
        description: 'Talk to your Open WebUI personas by voice.',
        start_url: './',
        scope: './',
        display: 'standalone',
        display_override: ['fullscreen', 'standalone'],
        background_color: '#0a0a0f',
        theme_color: '#0a0a0f',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Cache the app shell only. The Gateway's API/WS and the sibling
        // clients it serves (/lvgl, /virtual_device) must never be
        // answered with this app's index.html.
        navigateFallbackDenylist: [/^\/profiles/, /^\/ws/, /^\/lvgl\//, /^\/virtual_device\//],
      },
    }),
  ],
  server: {
    proxy: {
      '/profiles': GATEWAY,
      '/ws': { target: GATEWAY.replace(/^http/, 'ws'), ws: true },
    },
  },
  test: { include: ['tests/**/*.test.ts'], environment: 'node' },
});
