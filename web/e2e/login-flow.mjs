// Drives sign-in against a running Gateway (AUTH_MODE=owui) in headless Chrome:
// wrong password, correct login, persistence across reload, cookie hygiene,
// WebSocket authentication, and sign-out. Takes credentials from the
// environment so none are written anywhere:
//   EMAIL=... PASSWORD=... URL=http://localhost:8090/ node e2e/login-flow.mjs
import { mkdirSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const URL = process.env.URL ?? 'http://localhost:8090/';
const { EMAIL, PASSWORD } = process.env;
if (!EMAIL || !PASSWORD) throw new Error('set EMAIL and PASSWORD');
const OUT = '/tmp/shots/login';
mkdirSync(OUT, { recursive: true });

const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 800 });
const problems = [];
page.on('pageerror', (e) => problems.push(`[pageerror] ${e.message}`));
page.on('console', (m) => m.type() === 'error' && !/401|4401|Failed to load resource/.test(m.text()) && problems.push(`[console] ${m.text()}`));

let failures = 0;
const check = (ok, label) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) failures++;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png` });
const wsCloseCode = () =>
  page.evaluate(
    () =>
      new Promise((resolve) => {
        const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
        ws.onclose = (e) => resolve(e.code);
        setTimeout(() => resolve('still-open'), 1500);
      }),
  );
const signIn = async (password) => {
  await page.$eval('input[name=email]', (el) => (el.value = ''));
  await page.type('input[name=email]', EMAIL);
  await page.type('input[name=password]', password);
  await page.click('button.submit');
};

// 1. Signed out: login screen, and the API refuses us.
await page.goto(URL, { waitUntil: 'networkidle2' });
await page.waitForSelector('input[name=email]', { timeout: 15000 });
await sleep(900);
await shot('1-login');
check((await page.$('.card')) === null, 'signed out: login screen shown, no personas');
check((await wsCloseCode()) === 4401, 'signed out: WebSocket refused with 4401');
check((await page.evaluate(async () => (await fetch('/profiles')).status)) === 401, 'signed out: /profiles is 401');

// 2. Wrong password.
await signIn('definitely-not-the-password');
await page.waitForSelector('.error', { timeout: 15000 });
const message = await page.$eval('.error', (e) => e.textContent.trim());
await shot('2-wrong-password');
check(message === 'Incorrect email or password.', `wrong password shows "${message}"`);
check((await page.$eval('input[name=password]', (e) => e.value)) === '', 'rejected password is cleared from the field');

// 3. Correct login.
await signIn(PASSWORD);
await page.waitForSelector('.card', { timeout: 30000 });
await sleep(1800);
await shot('3-picker');
const chip = await page.$eval('.user-chip .name', (e) => e.textContent.trim());
check(chip.length > 0, `signed in as "${chip}" (user chip visible)`);
check((await page.$$('.card')).length > 0, 'personas are listed');

// 4. Cookie hygiene.
const cookies = await page.cookies();
const session = cookies.find((c) => c.name === 'sb_session');
check(!!session && session.httpOnly, 'session cookie is HttpOnly');
check(!session?.value.includes('eyJ'), 'cookie does not contain a readable JWT');
check(!(await page.evaluate(() => document.cookie)).includes('sb_session'), 'page JavaScript cannot read the cookie');
check((await wsCloseCode()) === 'still-open', 'signed in: WebSocket accepted');

// 5. The session survives a reload (a tablet that reboots / restarts its browser).
await page.reload({ waitUntil: 'networkidle2' });
await page.waitForSelector('.card', { timeout: 30000 });
check((await page.$('input[name=email]')) === null, 'reload: still signed in, no login screen');

// 6. Sign out.
await page.click('.user-chip .chip');
await sleep(400);
await shot('4-menu');
await page.evaluate(() => [...document.querySelectorAll('.menu button')].find((b) => b.textContent.includes('Sign out')).click());
await page.waitForSelector('input[name=email]', { timeout: 10000 });
await sleep(500);
check((await page.$('.card')) === null, 'sign out: personas gone from the page');
check((await wsCloseCode()) === 4401, 'sign out: WebSocket refused again');
await page.reload({ waitUntil: 'networkidle2' });
await page.waitForSelector('input[name=email]', { timeout: 15000 });
check(true, 'sign out: stays signed out after reload');

console.log(problems.length ? `\nBROWSER ERRORS:\n${problems.join('\n')}` : '\nno unexpected browser errors');
await browser.close();
process.exit(failures ? 1 : 0);
