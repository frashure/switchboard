import type { Persona } from './protocol';

/** Accents are a hue (0-360). All colour variants -- glow, ring, tints --
 *  are derived from it in CSS as hsl(var(--hue) ...), which works in older
 *  browsers (no color-mix / relative colour syntax needed) and lets the
 *  hue itself be animated. */

/** Deterministic fallback hue for personas without an avatar. */
export function hashHue(id: string): number {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash % 360;
}

/** Derives a persona's hue from its avatar: the average hue of the
 *  saturated, mid-bright pixels (so a mostly-grey or white background
 *  doesn't wash it out). Falls back to a hash hue if nothing qualifies. */
export async function accentFromAvatar(persona: Persona): Promise<number> {
  if (!persona.avatar) return hashHue(persona.id);
  const image = await loadImage(persona.avatar);
  const size = 32;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return hashHue(persona.id);
  ctx.drawImage(image, 0, 0, size, size);
  const { data } = ctx.getImageData(0, 0, size, size);

  let x = 0;
  let y = 0;
  let weight = 0;
  for (let i = 0; i < data.length; i += 4) {
    const [h, s, l] = rgbToHsl(data[i], data[i + 1], data[i + 2]);
    if (data[i + 3] < 200 || s < 0.25 || l < 0.2 || l > 0.85) continue;
    const w = s * (1 - Math.abs(l - 0.55));
    // Average hue on the circle, not the number line (350deg and 10deg are neighbours).
    x += Math.cos((h * Math.PI) / 180) * w;
    y += Math.sin((h * Math.PI) / 180) * w;
    weight += w;
  }
  if (weight === 0) return hashHue(persona.id);
  return Math.round(((Math.atan2(y, x) * 180) / Math.PI + 360) % 360);
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('avatar failed to decode'));
    image.src = src;
  });
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}
