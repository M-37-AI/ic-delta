/*
 * pruefstand.mjs – Strategie-Prüfstand: Strategie in Worten → Mini-Script → Backtest mit gesperrtem Prüfteil.
 *
 * Schutz gegen Überanpassung:
 *  - Je Wert werden die letzten 30 % der Tageskerzen als Prüfteil gesperrt. Übersetzen, Prüfen auf Fehler und die
 *    Parameterwahl sehen nur den Lernteil (die ersten 70 %).
 *  - Parameter ($name im Script) wählt der Server nachvollziehbar: die Kombination mit dem besten Median-Vorsprung
 *    gegenüber Kaufen und Halten im Lernteil, über alle gewählten Werte zugleich (ein Parametersatz für alle).
 *  - Der Prüfteil wird genau einmal gerechnet. Danach ist die Studie gesperrt; Änderungen werden ein neuer Versuch,
 *    und die Zahl der Versuche bleibt sichtbar (wer oft genug probiert, findet zufällig etwas).
 * Backtest wie in iC Charts (nur long, Signal auf Kerze t → Ausführung zur Eröffnung t+1, 0,1 % Gebühr je Seite),
 * ergänzt um Ausstiege: Stop und Ziel in Prozent vom Einstieg (innerhalb der Kerze, Stop zuerst) und Haltedauer.
 * Formelsprache, Indikatoren: Dateien von iC Charts (nur gelesen).
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { analysen, watchlist } from './werkzeuge.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const IC = path.resolve(here, '../src/marktdaten/dashboard/static/ic-charts');
const MiniScript = require(path.join(IC, 'script-engine.js'));
export const ORT = path.join((process.env.MARKTDATEN_DATEN || '~/.local/share/marktdaten').replace(/^~/, os.homedir()), 'analysen', 'pruefstand');
const LERNANTEIL = 0.7, GEBUEHR = 0.001, MAX_KOMBIS = 150, MIN_KERZEN = 400;
fs.mkdirSync(ORT, { recursive: true });

export const SPRACHE = `Mini-Script (eigene Formelsprache von iC Charts, kein JavaScript, kein Pine Script). Eine Anweisung je Zeile.
Reihen: close open high low volume hl2 hlc3. Funktionen: sma(x,n) ema(x,n) rma(x,n) rsi(x,n) stdev(x,n) highest(x,n) lowest(x,n)
bbupper(x,n,k) bblower(x,n,k) macd(x) macdsignal(x) macdhist(x) atr(n) stoch(n,k) stochd(n,k,d) supertrend(n,faktor)
crossover(a,b) crossunder(a,b) change(x,n) abs(x) max(a,b) min(a,b). Operatoren + - * / > < >= <= == != and or not, Variablen name = ausdruck,
Kommentare mit //. Pflicht: buy(bedingung) und sell(bedingung) (nur long: kaufen und wieder verkaufen; Leerverkauf gibt es nicht).
Keine Positionsdaten im Script (kein Einstiegskurs): Stop, Kursziel und Haltedauer stehen getrennt im Feld ausstieg.
Parameter, die der Prüfstand im Lernteil wählen darf, als $name schreiben, z. B. rsi(close, 14) < $unten; Bereiche in params.
Beispiel: schnell = ema(close, $f)\\nlangsam = sma(close, 200)\\nbuy(crossover(schnell, langsam))\\nsell(crossunder(schnell, langsam))`;

// ---------------- Daten ----------------
const kerzenCache = new Map();
async function kerzen(symbol) {
  const c = kerzenCache.get(symbol);
  if (c && Date.now() - c.t < 30 * 60e3) return c.bars;
  const d = await analysen(`/api/kerzen?wert=${encodeURIComponent(symbol)}&tf=1d&anzahl=5000`);
  const bars = d.kerzen.map(([t, o, h, l, cl, v]) => ({ time: t, open: o, high: h, low: l, close: cl, volume: v || 0 }));
  kerzenCache.set(symbol, { t: Date.now(), bars });
  return bars;
}
export async function werteMitHistorie() {
  const aus = [];
  for (const w of await watchlist()) {
    try { const b = await kerzen(w.symbol); if (b.length >= MIN_KERZEN) aus.push({ symbol: w.symbol, name: w.name, markt: w.markt, kerzen: b.length, von: b[0].time, bis: b[b.length - 1].time }); } catch (e) { /* keine Tageskerzen */ }
  }
  return aus;
}

// ---------------- Script und Backtest ----------------
const einsetzen = (code, vals) => Object.entries(vals || {}).reduce((c, [k, v]) => c.split('$' + k).join(String(v)), code);
export function platzhalter(code) { return [...new Set((code.match(/\$([A-Za-z_][A-Za-z0-9_]*)/g) || []).map((x) => x.slice(1)))]; }

function signale(code, bars) {
  const out = MiniScript.run(code, bars);
  const buys = out.filter((o) => o.type === 'buy'), sells = out.filter((o) => o.type === 'sell');
  if (!buys.length) throw new Error('Das Script braucht mindestens eine Zeile buy(...)');
  const n = bars.length, any = (arr, i) => arr.some((o) => o.values[i]);
  return { buy: Array.from({ length: n }, (_, i) => !!any(buys, i)), sell: Array.from({ length: n }, (_, i) => !!any(sells, i)) };
}

// Backtest auf dem Abschnitt [von, bis): flach starten, offene Position am Ende zum Schluss bewerten und schließen
export function backtest(bars, sig, von, bis, ausstieg = {}) {
  const stop = ausstieg.stop_pct ? ausstieg.stop_pct / 100 : null, ziel = ausstieg.ziel_pct ? ausstieg.ziel_pct / 100 : null, maxTage = ausstieg.max_tage || null;
  let cash = 1, units = 0, drin = false, einstieg = null, einstiegCash = 1, tage = 0, posBars = 0;
  const trades = [], kurve = [];
  const raus = (i, px, grund) => {
    cash = units * px * (1 - GEBUEHR); units = 0; drin = false;
    trades.push({ in: einstieg.t, out: bars[i].time, inPx: einstieg.px, outPx: px, ret: cash / einstiegCash - 1, tage, grund });
  };
  for (let i = von; i < bis; i++) {
    const b = bars[i];
    if (i > von) {
      if (!drin && sig.buy[i - 1]) {
        einstiegCash = cash; units = (cash * (1 - GEBUEHR)) / b.open; cash = 0; drin = true; tage = 0; einstieg = { t: b.time, px: b.open };
      } else if (drin && sig.sell[i - 1]) raus(i, b.open, 'Signal');
    }
    if (drin) {
      tage++;
      const sPx = stop ? einstieg.px * (1 - stop) : null, zPx = ziel ? einstieg.px * (1 + ziel) : null;
      if (sPx && b.low <= sPx) raus(i, Math.min(b.open, sPx), 'Stop');                       // Stop zuerst (vorsichtig)
      else if (zPx && b.high >= zPx) raus(i, Math.max(b.open, zPx), 'Ziel');
      else if (maxTage && tage >= maxTage) raus(i, b.close, 'Haltedauer');
    }
    if (drin) posBars++;
    kurve.push([b.time, (cash + units * b.close) * 100]);
  }
  if (drin) raus(bis - 1, bars[bis - 1].close, 'Ende');
  let spitze = -Infinity, maxDD = 0;
  for (const [, e] of kurve) { spitze = Math.max(spitze, e); maxDD = Math.max(maxDD, 1 - e / spitze); }
  const gew = trades.filter((t) => t.ret > 0), sumG = gew.reduce((s, t) => s + t.ret, 0), sumV = -trades.filter((t) => t.ret <= 0).reduce((s, t) => s + t.ret, 0);
  const ret = cash - 1, bh = bars[bis - 1].close / bars[von].open - 1;
  return { ret, bh, vorsprung: ret - bh, maxDD, trades: trades.length, trefferquote: trades.length ? gew.length / trades.length : null,
    profitfaktor: sumV > 0 ? sumG / sumV : gew.length ? 99 : null, schnitt: trades.length ? trades.reduce((s, t) => s + t.ret, 0) / trades.length : null,
    investiert: (bis - von) ? posBars / (bis - von) : 0, von: bars[von].time, bis: bars[bis - 1].time, kerzen: bis - von, liste: trades, kurve };
}

function raster(params) {
  let kombis = [{}];
  for (const p of params || []) {
    const werte = [], schritt = p.step || 1;
    for (let v = p.from; v <= p.to + 1e-9; v += schritt) werte.push(+v.toFixed(6));
    if (!werte.length || werte.length > 40) throw new Error(`Parameter ${p.name}: 1 bis 40 Werte (jetzt ${werte.length})`);
    kombis = kombis.flatMap((k) => werte.map((v) => ({ ...k, [p.name]: v })));
  }
  if (kombis.length > MAX_KOMBIS) throw new Error(`Höchstens ${MAX_KOMBIS} Parameterkombinationen (jetzt ${kombis.length}); Schritte gröber wählen`);
  return kombis;
}

// Nur Lernteil: für das Übersetzen und Prüfen auf Fehler. Liefert je Wert Kennzahlen des Lernteils, nie des Prüfteils.
export async function lernteil({ code, params = [], ausstieg = {}, werte }) {
  const fehlend = platzhalter(code).filter((p) => !params.some((x) => x.name === p));
  if (fehlend.length) throw new Error('Für diese Platzhalter fehlt ein Bereich in params: ' + fehlend.join(', '));
  const kombis = raster(params), symbole = werte?.length ? werte : (await werteMitHistorie()).map((w) => w.symbol);
  const daten = [];
  for (const s of symbole) { const bars = await kerzen(s); daten.push({ s, bars, cut: Math.floor(bars.length * LERNANTEIL) }); }
  const ergebnisse = kombis.map((k) => {
    const je = daten.map(({ s, bars, cut }) => { const sig = signale(einsetzen(code, k), bars); const r = backtest(bars, sig, 0, cut, ausstieg); return { symbol: s, ...r }; });
    const mittel = median(je.map((r) => r.vorsprung)), trades = je.reduce((x, r) => x + r.trades, 0);   // Median: ein Ausreißer-Wert soll nicht entscheiden
    return { params: k, je, mittel_vorsprung: mittel, trades, wertung: trades < 3 * je.length ? mittel - 1 : mittel };   // zu wenige Trades: hinten anstellen
  });
  ergebnisse.sort((a, b) => b.wertung - a.wertung);
  return { kombinationen: kombis.length, bester: ergebnisse[0], verteilung: ergebnisse.map((e) => ({ params: e.params, mittel_vorsprung: e.mittel_vorsprung, trades: e.trades })), daten };
}

// ---------------- Studien ----------------
const datei = (id) => path.join(ORT, id.replace(/[^a-z0-9]/gi, '') + '.json');
export function lesen(id) { try { return JSON.parse(fs.readFileSync(datei(id), 'utf8')); } catch (e) { return null; } }
export function speichern(s) { s.geaendert = new Date().toISOString(); fs.writeFileSync(datei(s.id) + '.tmp', JSON.stringify(s)); fs.renameSync(datei(s.id) + '.tmp', datei(s.id)); return s; }
export function liste() {
  return fs.readdirSync(ORT).filter((f) => f.endsWith('.json')).map((f) => { try { return JSON.parse(fs.readFileSync(path.join(ORT, f), 'utf8')); } catch (e) { return null; } }).filter(Boolean)
    .sort((a, b) => (b.erstellt || '').localeCompare(a.erstellt || ''));
}
export function neu(felder) {
  const s = { id: crypto.randomUUID().slice(0, 8), erstellt: new Date().toISOString(), status: 'neu', versuch: 1, familie: null, ...felder };
  if (!s.familie) s.familie = s.id;
  return speichern(s);
}
export function ableiten(alt, aenderung) {
  const versuche = liste().filter((x) => x.familie === alt.familie).length;
  return neu({ text: alt.text, titel: alt.titel, werte: alt.werte, script: alt.script, params: alt.params, ausstieg: alt.ausstieg, beschreibung: alt.beschreibung,
    annahmen: alt.annahmen, familie: alt.familie, versuch: versuche + 1, abgeleitet_von: alt.id, status: 'entwurf', ...aenderung });
}

const median = (a) => { const b = [...a].sort((x, y) => x - y), m = b.length >> 1; return b.length ? (b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2) : 0; };
const duenn = (kurve, n = 400) => { if (kurve.length <= n) return kurve; const s = kurve.length / n; return Array.from({ length: n }, (_, i) => kurve[Math.floor(i * s)]).concat([kurve[kurve.length - 1]]); };
const kurz = (r) => ({ ret: r.ret, bh: r.bh, vorsprung: r.vorsprung, maxDD: r.maxDD, trades: r.trades, trefferquote: r.trefferquote, profitfaktor: r.profitfaktor, schnitt: r.schnitt, investiert: r.investiert, von: r.von, bis: r.bis, kerzen: r.kerzen });

// Lernteil optimieren, dann den Prüfteil genau einmal öffnen
export async function pruefen(s) {
  if (s.gesperrt) throw new Error('Diese Studie ist schon geprüft. Änderungen bitte als neuen Versuch.');
  const t0 = Date.now();
  const lern = await lernteil({ code: s.script, params: s.params, ausstieg: s.ausstieg, werte: s.werte });
  const gewaehlt = lern.bester.params;
  const je_wert = lern.daten.map(({ s: symbol, bars, cut }) => {
    const sig = signale(einsetzen(s.script, gewaehlt), bars);
    const l = backtest(bars, sig, 0, cut, s.ausstieg), p = backtest(bars, sig, cut, bars.length, s.ausstieg);
    // durchgehende Kapitalkurve: Prüfteil startet mit dem Endstand des Lernteils
    const faktor = l.kurve.length ? l.kurve[l.kurve.length - 1][1] / 100 : 1, bhStart = bars[0].open;
    const kurve = [...l.kurve, ...p.kurve.map(([t, e]) => [t, e * faktor])].map(([t, e], i) => [t, +e.toFixed(2), +((bars[i].close / bhStart) * 100).toFixed(2)]);
    return { symbol, lern: kurz(l), pruef: kurz(p), pruef_ab: bars[cut].time, kurve: duenn(kurve),
      trades: [...l.liste.map((t) => ({ ...t, teil: 'lern' })), ...p.liste.map((t) => ({ ...t, teil: 'pruef' }))] };
  });
  const mittel = (f) => median(je_wert.map(f));
  const gesamt = { lern_vorsprung: mittel((w) => w.lern.vorsprung), pruef_vorsprung: mittel((w) => w.pruef.vorsprung), lern_ret: mittel((w) => w.lern.ret), pruef_ret: mittel((w) => w.pruef.ret),
    pruef_bh: mittel((w) => w.pruef.bh), pruef_schlaegt: je_wert.filter((w) => w.pruef.vorsprung > 0).length, werte: je_wert.length,
    trades_lern: je_wert.reduce((x, w) => x + w.lern.trades, 0), trades_pruef: je_wert.reduce((x, w) => x + w.pruef.trades, 0), maxDD_pruef: Math.max(...je_wert.map((w) => w.pruef.maxDD)) };
  // Urteil nach festen Regeln, Claude formuliert es danach in Worten
  let urteil;
  if (gesamt.trades_pruef < 10) urteil = { stufe: 'zu_wenig', text: 'Zu wenige Trades im Prüfteil für ein Urteil' };
  else if (gesamt.lern_vorsprung <= 0) urteil = { stufe: 'schwach', text: 'Schon im Lernteil schwächer als Kaufen und Halten' };
  else if (gesamt.pruef_vorsprung > 0 && gesamt.pruef_schlaegt * 2 >= gesamt.werte && gesamt.pruef_vorsprung >= 0.25 * gesamt.lern_vorsprung) urteil = { stufe: 'haelt', text: 'Hält im Prüfteil' };
  else if (gesamt.pruef_vorsprung > 0) urteil = { stufe: 'wackelt', text: 'Im Prüfteil deutlich schwächer als im Lernteil' };
  else urteil = { stufe: 'bricht', text: 'Bricht im Prüfteil ein: Überanpassung wahrscheinlich' };
  const versuche = liste().filter((x) => x.familie === s.familie && x.gesperrt).length + 1;
  return { gewaehlt, kombinationen: lern.kombinationen, verteilung_lern: lern.verteilung.slice(0, 40), je_wert, gesamt, urteil, versuche_familie: versuche,
    regeln: { lernanteil: LERNANTEIL, gebuehr: GEBUEHR, zeitrahmen: '1d', ausfuehrung: 'nächste Eröffnung' }, dauer_ms: Date.now() - t0 };
}
