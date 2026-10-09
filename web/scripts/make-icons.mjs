// Renders public/icon.svg to the PNG sizes the manifest needs, using the
// system Chrome (puppeteer-core is a dev dependency anyway for e2e checks).
//   node scripts/make-icons.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const svg = readFileSync(new URL('../public/icon.svg', import.meta.url), 'utf8');
const targets = { 'icon-512.png': 512, 'icon-192.png': 192, 'apple-touch-icon.png': 180 };

const browser = await puppeteer.launch({ executablePath: process.env.CHROME ?? '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const page = await browser.newPage();
for (const [name, size] of Object.entries(targets)) {
  await page.setViewport({ width: size, height: size });
  await page.setContent(`<body style="margin:0;background:transparent"><div style="width:${size}px;height:${size}px">${svg.replace('<svg ', `<svg width="${size}" height="${size}" `)}</div></body>`);
  const png = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
  writeFileSync(new URL(`../public/${name}`, import.meta.url), png);
  console.log(`wrote public/${name}`);
}
await browser.close();
