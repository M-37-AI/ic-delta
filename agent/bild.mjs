/*
 * bild.mjs – Chartbilder für Telegram: ein unsichtbarer Browser (Chromium von Playwright) öffnet den Bildmodus der
 * Analysen-Seite (?bild=chart|labor) und fotografiert ihn. Die Bilder liegen kurz im Temp-Ordner (Präfix icdelta-)
 * und werden nach dem Versand gelöscht. Der Browser bleibt zwischen Aufrufen offen und schließt nach 5 Minuten Ruhe.
 */
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { chromium } from 'playwright-core';

const ANALYSEN = process.env.ANALYSEN || `http://127.0.0.1:${+(process.env.MARKTDATEN_PORT || 8812) + 1}`;
export const ORDNER = os.tmpdir();
let browser = null, zu = null;

async function holen() {
  clearTimeout(zu);
  if (!browser || !browser.isConnected()) browser = await chromium.launch();
  zu = setTimeout(() => { browser?.close().catch(() => {}); browser = null; }, 5 * 60e3);
  return browser;
}

// art: chart (wert, tf, f = Index der Formation, alle, ind) oder labor (id, wert)
export async function bild(art, werte = {}) {
  const b = await holen();
  const ctx = await b.newContext({ viewport: { width: 1200, height: 675 }, deviceScaleFactor: 2, colorScheme: 'dark' });
  try {
    const p = await ctx.newPage();
    const q = new URLSearchParams({ bild: art, thema: 'dunkel', ...Object.fromEntries(Object.entries(werte).filter(([, v]) => v !== undefined && v !== null && v !== false).map(([k, v]) => [k, String(v)])) });
    await p.goto(`${ANALYSEN}/?${q}`, { waitUntil: 'networkidle', timeout: 30000 });
    await p.waitForFunction(() => window.__bildFertig === true, null, { timeout: 30000 });
    const pfad = path.join(ORDNER, `icdelta-${crypto.randomUUID().slice(0, 12)}.png`);
    await p.screenshot({ path: pfad });
    return pfad;
  } finally { await ctx.close().catch(() => {}); }
}
