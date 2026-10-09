// Drives the built app in headless Chrome against a running Gateway, with a
// fake microphone playing a recorded question, and saves screenshots of
// each stage. Needs: the Gateway serving web/dist on :8090 and a WAV of a
// spoken question (default /tmp/test_input.wav).
//   node e2e/walkthrough.mjs [tablet|phone] [persona-name]
import { mkdirSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const device = process.argv[2] ?? 'tablet';
const persona = process.argv[3] ?? 'Phil';
const URL = process.env.URL ?? 'http://localhost:8090/';
const WAV = process.env.WAV ?? '/tmp/test_input.wav';
const OUT = `/tmp/shots/${device}`;
const viewport = device === 'phone' ? { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true } : { width: 1280, height: 800 };
mkdirSync(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${WAV}`, '--autoplay-policy=no-user-gesture-required'],
});
const page = await browser.newPage();
await page.setViewport(viewport);
const problems = [];
page.on('console', (m) => ['error', 'warning'].includes(m.type()) && problems.push(`[console.${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => problems.push(`[pageerror] ${e.message}`));

const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png` });
const phase = () => page.$eval('.orb', (el) => el.dataset.phase).catch(() => null);
const waitPhase = (p, timeout = 120000) => page.waitForFunction((p) => document.querySelector('.orb')?.dataset.phase === p, { timeout }, p);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await page.goto(URL, { waitUntil: 'networkidle2' });
// With AUTH_MODE=owui the first screen is the login form (EMAIL / PASSWORD env).
await page.waitForSelector('.card, input[name=email]', { timeout: 30000 });
if (await page.$('input[name=email]')) {
  if (!process.env.EMAIL || !process.env.PASSWORD) throw new Error('login required: set EMAIL and PASSWORD');
  await page.type('input[name=email]', process.env.EMAIL);
  await page.type('input[name=password]', process.env.PASSWORD);
  await page.click('button.submit');
}
await page.waitForSelector('.card', { timeout: 30000 });
await sleep(1800); // entrance animations + accent extraction
await shot('1-picker');

const card = await page.evaluateHandle((name) => [...document.querySelectorAll('.card')].find((c) => c.textContent.includes(name)), persona);
await card.click();
await sleep(900);
await shot('2-session-ready');

const t0 = Date.now();
await page.click('.orb');
await waitPhase('capturing', 10000);
await sleep(700);
await shot('3-listening');

await waitPhase('thinking');
await shot('4-thinking');
console.log(`thinking after ${((Date.now() - t0) / 1000).toFixed(1)}s`);

await waitPhase('speaking');
const tSpeak = Date.now();
await sleep(1500);
await shot('5-speaking');
console.log(`speaking after ${((tSpeak - t0) / 1000).toFixed(1)}s from tap`);

await page.click('.orb'); // tap to stop
await waitPhase('ready', 5000);
await sleep(500);
await shot('6-stopped');
console.log('stopped cleanly -> ready');

await page.click('.back');
await sleep(900);
await shot('7-back-to-picker');

console.log(problems.length ? `PROBLEMS:\n${problems.join('\n')}` : 'no console errors');
await browser.close();
