// Verifies the PWA basics against a running server: manifest, service worker
// registration, and that the app shell still loads with the network offline.
//   URL=http://localhost:8090/ node e2e/pwa-check.mjs
import puppeteer from 'puppeteer-core';

const URL = process.env.URL ?? 'http://localhost:8090/';
const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.goto(URL, { waitUntil: 'networkidle2' });

const sw = await page.evaluate(async () => {
  const reg = await navigator.serviceWorker.ready;
  return { scope: reg.scope, active: !!reg.active };
});
console.log('service worker:', sw);

const manifest = await page.evaluate(async () => {
  const href = document.querySelector('link[rel=manifest]')?.href;
  return href ? { href, ...(await (await fetch(href)).json()) } : null;
});
console.log('manifest:', manifest && { name: manifest.name, display: manifest.display, icons: manifest.icons.map((i) => i.sizes) });

await page.setOfflineMode(true);
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('h1', { timeout: 10000 });
console.log('offline reload renders shell:', JSON.stringify(await page.$eval('h1', (h) => h.textContent)));
await page.setOfflineMode(false);

// The SW's navigation fallback must not swallow the sibling clients: this
// has to be a real navigation (a fetch() isn't subject to the fallback).
for (const [path, selector] of [['lvgl/', 'canvas#canvas'], ['virtual_device/', '#talk']]) {
  await page.goto(new globalThis.URL(path, URL).href, { waitUntil: 'domcontentloaded' });
  const own = (await page.$(selector)) !== null && (await page.$('h1')) === null;
  console.log(`navigating to /${path} shows its own page, not the app shell:`, own);
}
await browser.close();
