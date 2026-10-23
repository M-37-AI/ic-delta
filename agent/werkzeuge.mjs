/*
 * werkzeuge.mjs – alle Werkzeuge von Claude in iC Delta, einmal definiert.
 *
 * Jedes Werkzeug hat einen Ausführungsort (wo):
 *   server    – läuft hier in Node gegen den Analysen-Dienst (8813) und das Dashboard (8812); braucht keinen Browser.
 *   dashboard – läuft im Browser in der Dashboard-Hülle (panel.js): sehen, navigieren, hervorheben.
 *   chart     – läuft im Browser in iC Charts (agent-panel.js): zeichnen, Indikatoren, Scripts, Replay.
 * Dieselben Definitionen dienen dem Claude Agent SDK (als MCP-Werkzeuge) und OpenRouter (als JSON-Schema).
 */
import { z } from 'zod';

const BASIS = +(process.env.MARKTDATEN_PORT || 8812);
export const ANALYSEN = process.env.ANALYSEN || `http://127.0.0.1:${BASIS + 1}`;
export const DASHBOARD = process.env.DASHBOARD || `http://127.0.0.1:${BASIS}`;
const num = (d) => z.number().describe(d);
const str = (d) => z.string().describe(d);

// ---------------- HTTP zu den eigenen Diensten ----------------
async function api(basis, pfad, opts = {}, timeout = 30000) {
  let r;
  try { r = await fetch(basis + pfad, { ...opts, signal: AbortSignal.timeout(timeout) }); }
  catch (e) { throw new Error(`${basis === ANALYSEN ? 'Analysen-Dienst' : 'Dashboard'} nicht erreichbar (${e.name})`); }
  const text = await r.text();
  let d; try { d = JSON.parse(text); } catch (e) { d = { text }; }
  if (!r.ok || d.fehler) throw new Error(d.fehler || `${basis}${pfad} antwortet ${r.status}`);
  return d;
}
export const analysen = (pfad, opts, timeout) => api(ANALYSEN, pfad, opts, timeout);
export const dashboard = (pfad, opts, timeout) => api(DASHBOARD, pfad, opts, timeout);
const post = (pfad, body, timeout) => analysen(pfad, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, timeout);

let wl = { t: 0, werte: [] };
export async function watchlist() {
  if (Date.now() - wl.t > 30000) { try { wl = { t: Date.now(), werte: (await dashboard('/api/watchlist')).werte || [] }; } catch (e) { if (!wl.werte.length) throw e; } }
  return wl.werte;
}
export async function wertFinden(wert) {
  const w = await watchlist();
  const s = String(wert || '').trim().toUpperCase();
  const t = w.find((x) => x.id === s || x.symbol === s || x.name.toUpperCase() === s) || w.find((x) => x.name.toUpperCase().includes(s) && s.length > 2);
  if (!t) throw new Error(`"${wert}" ist nicht auf der Watchlist. Vorhanden: ${w.map((x) => x.symbol).join(', ')}`);
  return t;
}
const UHR = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit' });
const DATUM = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
export const uhr = (t) => (t == null ? null : UHR.format(new Date(typeof t === 'number' ? (t > 1e11 ? t : t * 1000) : t)));
const datum = (t) => (t == null ? null : DATUM.format(new Date(typeof t === 'number' ? (t > 1e11 ? t : t * 1000) : t)));
export const heute = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Berlin' }).format(new Date());
const r2 = (v, d = 2) => (typeof v === 'number' && isFinite(v) ? +v.toFixed(d) : v ?? null);

async function tagWaehlen(tag) {
  const u = await analysen('/api/uebersicht');
  const tage = u.tage.map((t) => t.tag);
  if (!tage.length) throw new Error('Noch keine Rohdaten: Der Sammler legt sie an Handelstagen ab 09:00 Uhr ab.');
  if (!tag || tag === 'heute' || tag === 'letzter') return tage[0];
  if (!tage.includes(tag)) throw new Error(`Für ${tag} gibt es keine Rohdaten. Vorhanden: ${tage.join(', ')}`);
  return tag;
}

function kerzenKompakt(k) {
  return k.map(([t, o, h, l, c, v]) => [datum(t), o, h, l, c, Math.round(v)]);
}

// Werkstatt-Ergebnis für das Modell eindampfen: Reihen nur als Kennzahlen, Tabellen gekürzt
function werkstattKompakt(e) {
  const a = e.ausgabe || {};
  const reihen = (a.reihen || []).map((r) => ({ titel: r.titel, punkte: r.t.length, serien: r.serien.map((s) => { const w = s.werte.filter((x) => typeof x === 'number'); return { name: s.name, min: w.length ? r2(Math.min(...w), 4) : null, max: w.length ? r2(Math.max(...w), 4) : null, zuletzt: w.length ? r2(w[w.length - 1], 4) : null }; }) }));
  return { id: e.id, titel: e.titel, angepinnt: e.angepinnt, wert: e.wert?.symbol, tag: e.tag, ok: e.ok, dauer_s: e.dauer_s, fehler: e.fehler,
    stdout: e.stdout || undefined, zahlen: a.zahlen, text: a.text, tabellen: (a.tabellen || []).map((t) => ({ titel: t.titel, spalten: t.spalten, zeilen: t.zeilen.slice(0, 40), zeilen_gesamt: t.zeilen.length })),
    reihen: reihen.length ? reihen : undefined, punkte: a.punkte ? a.punkte.map((p) => ({ titel: p.titel, n: p.punkte.length })) : undefined,
    hinweis: e.ok ? 'Die Karte steht im Reiter Analysen → Werkstatt' + (e.angepinnt ? '.' : ' unter Entwürfe; mit pin_analysis wird sie fester Teil des Reiters.') : 'Fehler im Code: korrigieren und erneut run_analysis aufrufen (id mitgeben, dann wird derselbe Entwurf aktualisiert).' };
}

// ---------------- Chart-Werkzeuge (Ausführung in iC Charts, agent-panel.js) ----------------
const CHART = [
  ['get_state', 'Aktueller Zustand von iC Charts: Symbol, Zeiteinheit, letzter Kurs, letzte Kerze, Indikatoren mit Werten, Alarme, Zeichnungen, Script und Backtest-Kennzahlen. Zuerst aufrufen, bevor du etwas über den Chart sagst oder darin zeichnest.', {}, true],
  ['get_bars', 'Die letzten N Kerzen des Charts (OHLCV, RSI 14, EMA 21) als Tabelle. Höchstens 500.', { count: num('Anzahl Kerzen, Standard 50').optional() }, true],
  ['find_extremes', 'Höchstes Hoch und tiefstes Tief der letzten N Kerzen mit Zeit und Abstand in Kerzen. Grundlage für Fibonacci oder Positionen.', { lookback: num('Anzahl Kerzen zurück, Standard 100').optional() }, true],
  ['screenshot', 'Liefert den Kurschart als PNG-Bild, damit du die Struktur sehen kannst (Kerzen, Indikatoren, Zeichnungen).', {}, true],
  ['set_symbol', 'Wechselt das Symbol im Chart. Aktien der Watchlist per Kürzel (SAP, SIE, AAPL), Krypto live (BTCUSDT, ETHUSDT).', { symbol: str('z. B. SAP') }, false],
  ['set_timeframe', 'Wechselt die Zeiteinheit des Charts.', { timeframe: z.enum(['1m', '5m', '15m', '1h', '4h', '1d']) }, false],
  ['set_indicators', 'Schaltet Indikatoren und ändert ihre Einstellungen. Kurz-Schalter sma, ema, bb, vwap, st, rsi, macd, btcd: true legt das Standard-Exemplar an, false entfernt alle dieses Typs. add: weitere Exemplare mit Einstellungen, z. B. {type:"ema", params:{n:200}}, {type:"ichimoku"}, {type:"vwap", params:{anker:"hoch", baender:"2"}}, {type:"vprofil"}; derselbe Typ darf mehrfach vorkommen. update: Einstellungen eines Exemplars per id ändern. remove: Exemplare per id entfernen. Typen und Einstellungen liefert list_indicators. Antwort: alle aktiven Indikatoren mit id, Einstellungen und aktuellen Werten.', { sma: z.boolean().optional(), ema: z.boolean().optional(), bb: z.boolean().optional(), vwap: z.boolean().optional(), st: z.boolean().describe('SuperTrend').optional(), rsi: z.boolean().optional(), macd: z.boolean().optional(), btcd: z.boolean().describe('Bitcoin-Dominanz (BTC.D) als Panel').optional(),
    add: z.array(z.object({ type: str('Typ aus list_indicators'), params: z.record(z.string(), z.union([z.number(), z.string()])).describe('Einstellungen, fehlende = Standard').optional(), color: str('#rrggbb').optional() })).max(8).optional(),
    update: z.array(z.object({ id: num('id des Exemplars'), params: z.record(z.string(), z.union([z.number(), z.string()])).optional(), color: str('#rrggbb').optional() })).optional(),
    remove: z.array(z.number()).describe('ids der Exemplare').optional() }, false],
  ['list_indicators', 'Verzeichnis aller Indikatoren von iC Charts (32 Typen): type, Name, Gruppe, Ort (Kurschart oder eigener Bereich), Einstellungen mit Standard und Grenzen bzw. Auswahl, Datenbedarf (Volumen, Abschlüsse), was er zeigt und woher die Formel stammt. Vor set_indicators mit add nutzen.', {}, true],
  ['run_script', 'Führt ein Mini-Script aus (eigene Formelsprache, kein JavaScript). Reihen: close open high low volume hl2 hlc3. Funktionen: sma(x,n) ema(x,n) rma(x,n) wma(x,n) hma(x,n) rsi(x,n) stdev(x,n) highest(x,n) lowest(x,n) bbupper(x,n,k) bblower(x,n,k) bbwidth(x,n,k) bbpercent(x,n,k) macd(x) macdsignal(x) macdhist(x) vwap() atr(n) stoch(n,k) stochd(n,k,d) supertrend(n,mult) psar(step,max) adx(n) plusdi(n) minusdi(n) aroonup(n) aroondown(n) willr(n) cci(n) roc(x,n) linreg(x,n) kcupper(n,mult) kclower(n,mult) dcupper(n) dclower(n) tenkan(n) kijun(n) obv() mfi(n) cmf(n) crossover(a,b) crossunder(a,b) change(x,n) abs max min. Ausgaben je Zeile: plot(x,"farbe","Titel") im Kurschart, panel(x) unten, alert(bedingung,"Text") markiert Signale, hline(preis), buy(bedingung) und sell(bedingung) starten einen Backtest (nur long, nächste Eröffnung, 0,1 % Gebühr). Variablen: name = ausdruck. Operatoren + - * / > < >= <= == != and or not. Kommentare mit //. Beispiel: schnell = ema(close, 9)\\nlangsam = ema(close, 21)\\nplot(schnell, "orange")\\nbuy(crossover(schnell, langsam))\\nsell(crossunder(schnell, langsam))', { code: str('Der Script-Text, Zeilen mit \\n getrennt') }, false],
  ['clear_script', 'Entfernt das Mini-Script und seine Zeichnungen.', {}, false],
  ['add_alarm', 'Preisalarm nur im offenen Chart (Ton im Browser). Für Alarme, die auch bei geschlossenem Browser per Telegram kommen, create_alert nehmen.', { price: num('Preis') }, false],
  ['add_hline', 'Zeichnet eine horizontale Linie bei einem Preis.', { price: num('Preis') }, false],
  ['draw_fibonacci', 'Zeichnet ein Fibonacci-Retracement zwischen zwei Punkten. Ebene 1 liegt am Startpunkt, Ebene 0 am Endpunkt. Punkte per Zeit (ISO) oder Abstand in Kerzen; ohne Preis wird der Schlusskurs genommen – für Hoch/Tief den Preis aus find_extremes mitgeben.', { from_time: z.string().optional(), from_bars_ago: num('Startpunkt: Kerzen zurück').optional(), from_price: num('Preis am Startpunkt').optional(), to_time: z.string().optional(), to_bars_ago: num('Endpunkt: Kerzen zurück').optional(), to_price: num('Preis am Endpunkt').optional() }, false],
  ['draw_position', 'Zeichnet eine Position mit Einstieg, Ziel und Stop (Ziel über Einstieg = Long, darunter = Short) und berechnet das Chance-Risiko-Verhältnis.', { entry: num('Einstiegspreis'), target: num('Zielpreis'), stop: num('Stop-Preis'), bars_ago: num('Startkerze: Kerzen zurück, Standard 0').optional() }, false],
  ['clear_drawings', 'Löscht alle Zeichnungen, Linien und Chart-Alarme.', {}, false],
  ['undo_drawing', 'Entfernt die zuletzt gezeichnete Figur.', {}, false],
  ['find_pivots', 'Wendepunkte (Swing-Hochs und -Tiefs) der letzten N Kerzen mit Preis und Abstand in Kerzen. Linien immer durch echte Wendepunkte legen.', { lookback: num('Kerzen zurück, Standard 200').optional(), strength: num('Kerzen links und rechts, die der Punkt überragen muss, Standard 5').optional() }, true],
  ['draw_line', 'Zeichnet eine beschriftete Linie durch zwei Punkte (Kerzen zurück + Preis), z. B. eine Trendlinie durch zwei Tiefs. extend_right verlängert sie als Strahl nach rechts.', { from_bars_ago: num('Startpunkt: Kerzen zurück'), from_price: num('Preis am Startpunkt'), to_bars_ago: num('Endpunkt: Kerzen zurück'), to_price: num('Preis am Endpunkt'), extend_right: z.boolean().optional(), label: str('kurze Beschriftung, z. B. "Aufwärtstrend"').optional(), kind: z.enum(['trend', 'support', 'resistance', 'neutral', 'bull', 'bear']).optional(), dashed: z.boolean().optional() }, false],
  ['draw_level', 'Zeichnet ein waagerechtes Niveau (Unterstützung oder Widerstand) ab einem Punkt bis zum rechten Rand, beschriftet und mit Preis.', { price: num('Preis'), label: z.string().optional(), kind: z.enum(['support', 'resistance', 'neutral']).optional(), from_bars_ago: num('ab welcher Kerze, Standard 150').optional() }, false],
  ['draw_zone', 'Zeichnet eine Zone zwischen zwei Preisen (Angebot/Nachfrage, Konsolidierung, Lücke).', { price_low: num('unterer Preis'), price_high: num('oberer Preis'), from_bars_ago: num('Beginn: Kerzen zurück, Standard 150').optional(), to_bars_ago: num('Ende: Kerzen zurück, Standard 0').optional(), label: z.string().optional(), kind: z.enum(['support', 'resistance', 'neutral', 'bull', 'bear']).optional() }, false],
  ['draw_pattern', 'Zeichnet ein Chartmuster als Linienzug durch mehrere Punkte mit Beschriftung, z. B. Dreieck (closed), Kopf-Schulter (5 Punkte), Kanal, Doppelboden. Punkte aus find_pivots nehmen.', { points: z.array(z.object({ bars_ago: z.number(), price: z.number() })).min(2).max(12), label: str('z. B. "Kopf-Schulter"'), closed: z.boolean().describe('Linienzug schließen und füllen').optional(), kind: z.enum(['pattern', 'bull', 'bear', 'neutral']).optional() }, false],
  ['annotate', 'Setzt eine kurze Textnotiz mit Zeiger an eine Kerze, z. B. "Ausbruch" oder "Fehlausbruch".', { bars_ago: num('Kerzen zurück'), price: num('Preis, Standard Schlusskurs').optional(), text: z.string(), kind: z.enum(['neutral', 'bull', 'bear', 'support', 'resistance']).optional(), above: z.boolean().describe('Notiz über (true) oder unter (false) dem Punkt').optional() }, false],
  ['optimize_strategy', 'Strategie-Labor: führt ein Mini-Script mit buy()/sell() für alle Kombinationen von ein oder zwei Parametern aus und zeigt eine Hitzekarte im Chart. Platzhalter im Script als $name, z. B. "schnell = ema(close, $f)". Liefert Raster, beste Kombination, Buy & Hold, wie viele Kombinationen Buy & Hold schlagen, und einen Robustheitstest (Wahl auf den ersten 70 % der Kerzen, Prüfung auf den letzten 30 %). Höchstens 60 Werte je Parameter und 900 Kombinationen.', { code: str('Mini-Script mit $-Platzhaltern'), param1: z.object({ name: z.string(), from: z.number(), to: z.number(), step: z.number().optional() }), param2: z.object({ name: z.string(), from: z.number(), to: z.number(), step: z.number().optional() }).optional(), metric: z.enum(['return', 'profit_factor', 'max_drawdown', 'win_rate']).optional() }, false],
  ['replay', 'Steuert das Kerzen-Replay im Chart.', { action: z.enum(['start', 'pause', 'play', 'stop']) }, false],
  ['liquidity_heatmap', 'Liquiditäts-Heatmap im Kurschart ein- oder ausschalten: Orderbuch je Preisstufe im Zeitverlauf hinter den Kerzen (Kaufseite grün, Verkaufsseite rot, kräftiger = mehr Stück). Deutsche Werte aus den Xetra-Daten (beste 5 Stufen, 15 Min. verzögert) mit erkannten Wänden und ihrem Ende (abgearbeitet, zurückgezogen, aus dem Blick, steht); Krypto live seit dem Einschalten; US-Werte haben kein Buch mit Tiefe. Zeiteinheiten 1m bis 1h. Antwort: Quelle, Zeitraum, Skala und die letzten Wände. Zurückgezogene Wände nahe am Kurs sind ein Muster wie bei Spoofing, kein Beweis – so formulieren.', { on: z.boolean().describe('true = einschalten (Standard), false = ausschalten').optional(), anteil: num('nur die größten x % der Stufen zeichnen (5-100, Standard 20); kleiner = aufgeräumter Chart').optional() }, false],
  ['draw_formations', 'Zeichnet Formationen aus detect_patterns in den Chart ein (Linien, Zonen, Linienzüge). Normalerweise ruft detect_patterns das selbst auf.', { formationen: z.array(z.any()) }, false],
].map(([name, beschreibung, schema, nurLesen]) => ({ name, beschreibung, schema, nurLesen, wo: 'chart' }));

// ---------------- Dashboard-Werkzeuge (Ausführung in der Dashboard-Hülle, panel.js) ----------------
const DASH = [
  { name: 'get_view', wo: 'dashboard', nurLesen: true, beschreibung: 'Was Niko gerade sieht: aktiver Reiter (kurse, icharts, analysen, speicher, quellen), bei Analysen Wert, Tag und Unterreiter mit den Kartentiteln, bei iC Charts Symbol und Zeiteinheit, dazu ein Textauszug der sichtbaren Seite.', schema: {} },
  { name: 'navigate', wo: 'dashboard', nurLesen: false, beschreibung: 'Bedient das Dashboard: Reiter wechseln und bei Analysen Wert, Tag und Unterreiter setzen: charts (Startseite mit den auffälligsten Charts; mit wert die Detailansicht des Charts), orderbuch (Kosten, Druck, Wände, Eisberge), pruefstein (Rückblick: Wie gut halten Formationen ihre Ziele?), pruefstand (Ideen-Labor: Strategie in Worten testen), handel (Plätze, Retail-Buch, Lee-Ready/VPIN/Kyle, Auktionen), strategien, werkstatt, alarme, claude (Bilanz, Dossiers), bei iC Charts Symbol und Zeiteinheit. Danach ist der Chart für die Chart-Werkzeuge offen. tab orderbuch ist der eigene Reiter Orderbuch (Signal-Lage, Heatmap, Live-Buch, Flow, Bilanz) für deutsche und Krypto-Werte; wert wählt den Wert, US-Werte und Rohstoffe haben dort kein Buch.', schema: { tab: z.enum(['kurse', 'depot', 'icharts', 'analysen', 'orderbuch', 'speicher', 'quellen']).optional(), section: str('Unterreiter der Analysen').optional(), wert: str('Kürzel oder ISIN').optional(), tag: str('YYYY-MM-DD').optional(), tf: z.enum(['1m', '5m', '15m', '1h', '4h', '1d']).optional() } },
  { name: 'highlight', wo: 'dashboard', nurLesen: false, beschreibung: 'Hebt eine Karte oder Rubrik im sichtbaren Reiter hervor (Puls am Rand, scrollt hin), damit Niko sieht, wovon du sprichst. title: Titel oder ein eindeutiges Wort daraus.', schema: { title: str('Kartentitel oder Rubrik') } },
];

// ---------------- Server-Werkzeuge ----------------
const SERVER = [
  { name: 'get_signals', nurLesen: true,
    beschreibung: 'Signal-Lage aus dem Orderbuch eines Werts (Reiter Orderbuch): Regime (ruhig/aktiv/stress), Ort (Kurs, VWAP, Volumenschwerpunkt, nächste Kauf-/Verkaufswand), Confluence-Lage mit Bausteinen, aktive Signale (Ungleichgewicht mit Fluss, Absorption, Delta-Divergenz, Abprall an Wand, Wand verschwindet, Eisberg, Ausbruch in dünne Schicht, Sweep und Rückkehr, toxischer Fluss, Liquidationskaskade, Open-Interest-Lage) mit Begründung, Invalidierung und Markout, dazu die Statistik je Signalart der letzten 10 Tage (Trefferquote, netto bp nach Kosten) und ein fertiger Absatz in Worten. Krypto live (Binance, je Sekunde), DE Rückschau (Xetra 15 Min. verzögert, nur Handelstag). Signale sind Hypothesen, keine Empfehlungen; nenne immer Markout und Kosten.',
    schema: { wert: str('Kürzel oder ISIN'), stunden: num('Zeitraum in Stunden (Krypto, Standard 6, höchstens 24)').optional(), tag: str('Handelstag YYYY-MM-DD (DE, Standard: letzter Tag mit Buch)').optional() },
    async run({ wert, stunden, tag }) {
      const bis = Math.floor(Date.now() / 1000), von = bis - Math.min(24, Math.max(1, stunden || 6)) * 3600;
      const d = await analysen(`/api/signale?wert=${encodeURIComponent(wert)}&schritt=60&von=${von}&bis=${bis}${tag ? '&tag=' + encodeURIComponent(tag) : ''}`);
      if (d.fehler) return d;
      const kurz = (r) => { if (!r || !r.t) return undefined; const n = r.t.length, idx = [...new Set([0, ...Array.from({ length: 12 }, (_, i) => Math.floor(n * (i + 1) / 12) - 1)])].filter((i) => i >= 0 && i < n); const aus = {}; for (const k of Object.keys(r)) aus[k] = Array.isArray(r[k]) ? idx.map((i) => r[k][i]) : r[k]; return aus; };
      return { wert: d.wert, symbol: d.symbol, name: d.name, markt: d.markt, live: d.live, stand: datum(d.stand * 1000), quelle: d.quelle, hinweis: d.hinweis, kosten: d.kosten, regime: d.regime, ort: d.ort, lage: d.lage,
        signale_aktiv: d.signale_aktiv, letzte_signale: (d.signale || []).slice(-12), statistik_wert: d.statistik, statistik_alle_werte: d.statistik_gesamt, perp: d.perp && { funding: d.perp.funding, oi: d.perp.oi, mark: d.perp.mark }, liquidationen_anzahl: (d.liquidationen || []).length,
        reihen_verdichtet: kurz(d.reihen), erklaerung: d.erklaerung, absatz: d.absatz };
    } },
  { name: 'get_overview', nurLesen: true,
    beschreibung: 'Überblick: alle Werte der Watchlist mit letztem Kurs, Veränderung, Spread, Umsatz und Datenzeit; vorhandene Rohdaten-Tage für Orderbuch-Analysen; Anzahl Alarme; Telegram-Stand. Erster Aufruf bei allgemeinen Fragen ("was ist los?").',
    schema: {},
    async run() {
      const [k, u, al] = await Promise.all([dashboard('/api/kurse'), analysen('/api/uebersicht'), analysen('/api/alarme')]);
      return { stand: new Date().toISOString(), werte: (k.werte || []).map((w) => ({ symbol: w.symbol, name: w.name, markt: w.markt, kurs: w.letzter, vortag: w.vortag, veraenderung_pct: w.veraenderung, spread_bp: w.spread_bp, umsatz: w.umsatz, abschluesse: w.abschluesse, datenzeit: datum(w.zeit), verlauf_heute: w.verlauf ? [w.verlauf[0], ...w.verlauf.filter((_, i) => i % Math.max(1, Math.floor(w.verlauf.length / 10)) === 0).slice(1), w.verlauf[w.verlauf.length - 1]] : undefined })),
        orderbuch_tage: u.tage.map((t) => ({ tag: t.tag, werte: t.werte.length })), alarme_aktiv: al.regeln.filter((r) => r.aktiv).length, alarme_gesamt: al.regeln.length,
        telegram: al.telegram.token && al.telegram.chat ? 'eingerichtet' : 'nicht eingerichtet',
        hinweise: 'DE: Xetra 15 Min. verzögert, Buch nur live gesammelt (09:00–17:30). US: IEX live, Volumen der letzten 15 Min. unvollständig. Krypto: Binance live.' };
    } },
  { name: 'get_quote', nurLesen: true,
    beschreibung: 'Ein Wert im Detail: Kurs, Veränderung, Umsatz, aktuelles Orderbuch (5 Stufen, bei Xetra abends das Retail-Buch), Kurse anderer Plätze (Tradegate), letzte Abschlüsse.',
    schema: { wert: str('Kürzel, Name oder ISIN') },
    async run({ wert }) {
      const w = await wertFinden(wert);
      const d = await dashboard('/api/kurs?id=' + encodeURIComponent(w.id));
      const buch = d.buch ? { art: d.buch.art, zeit: datum(d.buch.zeit), bid: d.buch.bid, ask: d.buch.ask, spread_bp: d.buch.bid?.[0] && d.buch.ask?.[0] ? r2((d.buch.ask[0][0] - d.buch.bid[0][0]) / ((d.buch.ask[0][0] + d.buch.bid[0][0]) / 2) * 1e4) : null, stufen_format: '[Preis, Stück, Orders]' } : null;
      return { symbol: d.symbol, name: d.name, markt: d.markt, kurs: d.letzter, vortag: d.vortag, veraenderung_pct: d.veraenderung, datenzeit: datum(d.zeit), umsatz: d.umsatz, stueck: d.stueck, abschluesse: d.abschluesse,
        buch, plaetze: d.plaetze, letzte_abschluesse: (d.trades || []).slice(0, 12).map((t) => ({ zeit: datum(t.zeit), platz: t.venue, preis: t.preis, menge: t.menge })) };
    } },
  { name: 'get_candles', nurLesen: true,
    beschreibung: 'Abgeschlossene Kerzen eines Werts aus dem Speicher: [Zeit Berlin, Open, High, Low, Close, Volumen]. tf 1m, 5m, 15m, 1h, 1d. Höchstens 400.',
    schema: { wert: str('Kürzel oder ISIN'), tf: z.enum(['1m', '5m', '15m', '1h', '1d']).optional(), count: num('Anzahl, Standard 60').optional() },
    async run({ wert, tf = '15m', count = 60 }) {
      const w = await wertFinden(wert);
      const d = await analysen(`/api/kerzen?wert=${w.id}&tf=${tf}&anzahl=${Math.min(400, Math.max(5, Math.round(count)))}`);
      const k = d.kerzen;
      const hoch = Math.max(...k.map((x) => x[2])), tief = Math.min(...k.map((x) => x[3]));
      return { symbol: w.symbol, tf, anzahl: k.length, von: datum(k[0]?.[0]), bis: datum(k[k.length - 1]?.[0]), hoch, tief, letzter_schluss: k[k.length - 1]?.[4], aenderung_pct: k.length > 1 ? r2((k[k.length - 1][4] / k[0][1] - 1) * 100) : null, kerzen: kerzenKompakt(k) };
    } },
  { name: 'get_orderbook_analysis', nurLesen: true,
    beschreibung: 'Orderbuch-Analyse eines deutschen Werts für einen Tag aus dem Reiter Analysen, als Kurzfassung: a Kosten/Liquidität (Spread, Tiefe, effektiver Spread, Orderkosten), b Druck (OBI, OFI, Microprice und ob sie den Kurs vorhersagen), c Aufbau (Wände mit Ergebnis, Spoofing-Muster, Eisberge, Ordergrößen), d Plätze/Lead-Lag/Retail-Buch, e Lee-Ready/VPIN/Kyle, f Strategietests, auktionen. teil wählen, um Platz zu sparen. Nur Tage mit Rohdaten; das Buch gibt es nur, wenn der Sammler im fortlaufenden Handel lief.',
    schema: { wert: str('Kürzel oder ISIN eines DE-Werts'), tag: str('YYYY-MM-DD oder "heute" (letzter Tag mit Daten)').optional(), teil: z.enum(['a', 'b', 'c', 'd', 'e', 'f', 'auktionen', 'alle']).optional() },
    async run({ wert, tag, teil = 'alle' }) {
      const w = await wertFinden(wert);
      if (w.markt !== 'DE') throw new Error(`${w.symbol} ist kein deutscher Wert: Orderbuch-Analysen gibt es nur für Xetra-Werte.`);
      const t = await tagWaehlen(tag);
      return analysen(`/api/analyse?wert=${w.id}&tag=${t}&kurz=1&teil=${teil}`, {}, 120000);
    } },
  // --- Ideen-Labor (Strategie-Backtest) (Lernteil offen, Prüfteil gesperrt bis zum Prüfen)
  { name: 'strategy_check', nurLesen: true,
    beschreibung: 'Ideen-Labor: rechnet ein Mini-Script nur auf dem Lernteil (erste 70 % der Tageskerzen je Wert), um Fehler zu finden und zu sehen, ob es überhaupt handelt. Den gesperrten Prüfteil sieht niemand vor dem Prüfen. Liefert Fehlermeldung oder je Wert Rendite gegen Kaufen und Halten und Zahl der Trades im Lernteil sowie die Parameterwahl. Nicht zum Optimieren auf schöne Zahlen missbrauchen: übersetzen, nicht erfinden.',
    schema: { code: str('Mini-Script mit buy() und sell(), Parameter als $name'), params: z.array(z.object({ name: z.string(), from: z.number(), to: z.number(), step: z.number().optional() })).describe('Bereiche für $-Parameter').optional(),
      ausstieg: z.object({ stop_pct: z.number().optional(), ziel_pct: z.number().optional(), max_tage: z.number().optional() }).describe('Stop/Kursziel in % vom Einstieg, Haltedauer in Tagen').optional(), werte: z.array(z.string()).describe('Kürzel, Standard alle mit langer Historie').optional() },
    async run({ code, params = [], ausstieg = {}, werte }) {
      const P = await import('./pruefstand.mjs');
      const l = await P.lernteil({ code, params, ausstieg, werte });
      const b = l.bester;
      return { prueftail: 'gesperrt', kombinationen: l.kombinationen, gewaehlt: b.params, median_vorsprung_lern_pct: +(b.mittel_vorsprung * 100).toFixed(1), trades_lern: b.trades,
        je_wert: b.je.map((r) => ({ symbol: r.symbol, rendite_pct: +(r.ret * 100).toFixed(1), kaufen_halten_pct: +(r.bh * 100).toFixed(1), trades: r.trades, von: new Date(r.von * 1000).toISOString().slice(0, 10), bis: new Date(r.bis * 1000).toISOString().slice(0, 10) })) };
    } },
  { name: 'strategy_save', nurLesen: false,
    beschreibung: 'Ideen-Labor: speichert eine übersetzte Strategie als Entwurf (Titel, Nikos Text, Script, Parameterbereiche, Ausstieg, Rückbeschreibung). Niko prüft sie in Analysen → Ideen-Labor und startet dort den Test mit dem Prüfteil. Vorher mit strategy_check auf Fehler prüfen.',
    schema: { titel: str('kurz, z. B. "RSI-Rücksetzer im Aufwärtstrend"'), text: str('Nikos Beschreibung in seinen Worten'), script: z.string(), params: z.array(z.object({ name: z.string(), from: z.number(), to: z.number(), step: z.number().optional(), warum: z.string().optional() })).optional(),
      ausstieg: z.object({ stop_pct: z.number().optional(), ziel_pct: z.number().optional(), max_tage: z.number().optional() }).optional(), beschreibung: str('zwei bis vier Sätze: so habe ich dich verstanden'),
      annahmen: z.array(z.string()).optional(), grenzen: z.array(z.string()).optional(), werte: z.array(z.string()).optional() },
    async run(i, ctx) {
      const P = await import('./pruefstand.mjs');
      const st = P.neu({ ...i, params: i.params || [], ausstieg: i.ausstieg || {}, status: 'entwurf', modell: ctx?.modell || '' });
      if (ctx?.dashboard) { try { await ctx.dashboard('navigate', { tab: 'analysen', section: 'pruefstand' }); } catch (e) { /* Dashboard nicht offen */ } }
      return { ok: true, id: st.id, hinweis: 'Entwurf gespeichert. Niko kann ihn in Analysen → Ideen-Labor ansehen, anpassen und den Prüfteil öffnen.' };
    } },
  { name: 'strategy_run', nurLesen: false,
    beschreibung: 'Ideen-Labor: öffnet den gesperrten Prüfteil für einen Entwurf (id) – genau einmal. Parameter werden vorher nachvollziehbar im Lernteil gewählt. Danach ist die Studie gesperrt; Änderungen werden ein neuer Versuch. Nur aufrufen, wenn Niko ausdrücklich prüfen lassen will.',
    schema: { id: z.string() },
    async run({ id }) {
      const P = await import('./pruefstand.mjs');
      const st = P.lesen(id); if (!st) throw new Error('Unbekannte Studie');
      const e = await P.pruefen(st);
      P.speichern({ ...st, status: 'fertig', gesperrt: true, ergebnis: e, geprueft: new Date().toISOString() });
      const g = e.gesamt, pct = (v) => +(v * 100).toFixed(1);
      return { urteil: e.urteil, gewaehlt: e.gewaehlt, kombinationen: e.kombinationen, versuche_familie: e.versuche_familie,
        median_lern_vorsprung_pct: pct(g.lern_vorsprung), median_pruef_vorsprung_pct: pct(g.pruef_vorsprung), pruef_schlaegt_kaufen_halten: `${g.pruef_schlaegt}/${g.werte}`, trades_lern: g.trades_lern, trades_pruef: g.trades_pruef,
        je_wert: e.je_wert.map((w) => ({ symbol: w.symbol, lern_pct: pct(w.lern.ret), lern_bh_pct: pct(w.lern.bh), pruef_pct: pct(w.pruef.ret), pruef_bh_pct: pct(w.pruef.bh), max_rueckgang_pruef_pct: pct(w.pruef.maxDD), trades_pruef: w.pruef.trades })) };
    } },
  { name: 'list_strategies', nurLesen: true, beschreibung: 'Ideen-Labor: alle Studien mit Titel, Status, Versuch und Urteil.', schema: {},
    async run() { const P = await import('./pruefstand.mjs'); return { studien: P.liste().slice(0, 40).map((x) => ({ id: x.id, titel: x.titel, status: x.status, versuch: x.versuch, urteil: x.ergebnis?.urteil?.text, erstellt: x.erstellt })) }; } },
  { name: 'chart_image', nurLesen: true, nurNachricht: true,
    beschreibung: 'Hängt ein Chartbild an deine Antwort (Telegram). art chart: Kerzenchart eines Werts in der Optik von iC Charts mit der Formation (formation = Index aus detect_patterns desselben Zeitrahmens, Empfindlichkeit normal; ohne formation die wichtigste aus der Chart-Vorauswahl; alle = true zeichnet alle; indikatoren = true zeigt SMA 50/200 und Bollinger). art labor: Kapitalkurve einer geprüften Studie aus dem Ideen-Labor gegen Kaufen und Halten (id, wert). Höchstens zwei Bilder je Antwort; nur, wenn das Bild etwas zeigt, das der Text beschreibt.',
    schema: { art: z.enum(['chart', 'labor']), wert: str('Kürzel, z. B. SAP').optional(), tf: z.enum(['5m', '15m', '1h', '1d']).describe('Standard: Zeitrahmen der Chart-Vorauswahl').optional(), formation: num('Index der Formation').optional(),
      alle: z.boolean().optional(), indikatoren: z.boolean().optional(), id: str('Studie im Ideen-Labor').optional(), titel: str('kurze Bildunterschrift').optional() },
    async run(i, ctx) {
      if (!ctx?.bilder) throw new Error('Bilder gibt es nur in Nachrichten (Telegram), nicht im Dashboard');
      if (ctx.bilder.length >= 2) throw new Error('Höchstens zwei Bilder je Antwort');
      const { bild } = await import('./bild.mjs');
      const pfad = i.art === 'labor' ? await bild('labor', { id: i.id, wert: i.wert }) : await bild('chart', { wert: (await wertFinden(i.wert)).symbol, tf: i.tf, f: i.formation, alle: i.alle ? 1 : null, ind: i.indikatoren ? 1 : null });
      ctx.bilder.push({ pfad, titel: String(i.titel || '').slice(0, 200) });
      return { ok: true, hinweis: 'Bild hängt an der Antwort. Im Text nicht beschreiben, dass ein Bild kommt; einfach darauf Bezug nehmen.' };
    } },
  { name: 'get_pattern_stats', nurLesen: true,
    beschreibung: 'Prüfstein: wie sich die Formationen und Signale der Erkennung rückblickend auf den Tageskerzen der Watchlist geschlagen haben (US-Werte 5 Jahre, Bitcoin knapp 3 Jahre, ohne Blick in die Zukunft). Je Art und Richtung: Fälle, Ziel erreicht (Measure Rule nach Bulkowski, 60 Tage), Fehlausbruch, Bewegung unter 5 %, Rendite nach 5/20/60 Tagen, Vorsprung gegenüber einem beliebigen Tag, Bulkowskis Anteil; dazu Wirkung von Güte und Ausbruchsvolumen. Nutzen, wenn Niko fragt, ob eine Formation etwas taugt, und um eigene Einschätzungen zu belegen. Kleine Fallzahlen (unter 10) immer dazusagen.',
    schema: { art: str('optional: nur diese Art, z. B. doppeltief, dreieck_sym, flagge_bull, trendlinie').optional() },
    async run({ art }) {
      const d = (await analysen('/api/pruefstein?ohne_ereignisse=1')).ergebnis;
      if (!d) return { hinweis: 'Prüfstein noch nicht gerechnet' };
      const zeilen = d.zeilen.filter((z) => !art || z.art === art);
      return { stand: d.stand, regeln: d.regeln, gesamt: d.gesamt, basis_pct: d.basis_pct, guete: d.guete, volumen: d.volumen,
        zeilen: zeilen.map((z) => ({ name: z.name, art: z.art, richtung: z.richtung, faelle: z.n, ziel_quote: z.ziel_quote, ziel_faelle: z.ziel_n, ziel_schon_beim_einstieg: z.ziel_schon,
          ziel_abstand_pct: z.ziel_abstand, fehl_quote: z.fehl_quote, unter5_quote: z.unter5_quote, r20: z.r20, vorsprung_r20: z.vorsprung_r20, bulkowski_anteil: z.bulkowski_anteil })),
        hinweis: 'Stichprobe: wenige große US-Werte und Bitcoin, überwiegend Bullenmarkt, keine Kosten. Splits sind herausgerechnet.' };
    } },
  { name: 'save_briefing', nurLesen: false, nurNachricht: true,
    beschreibung: `Morgenlage: speichert das Briefing „Die fünf Highlights“ für Analysen → Claude. Pflicht in jeder Morgenlage, nach dem Text. Bei Fehlern korrigieren und erneut aufrufen, bis ok.
So entsteht daraus die Seite: je Highlight Titel (Name und muster), darunter these als fette Zeile, dann herleitung als Fließtext (in Absätze zu drei Sätzen geteilt; die beiden Marken werden darin farbig markiert, wenn sie genau so geschrieben sind wie in der Antwort unter marken_im_text), darunter ein Chart: Tagesschluss der letzten 40 Tage als Linie, letzter Punkt live, eine grüne Linie bei oben.kurs und eine rote bei unten.kurs, von „heute“ ein Fächer zu beiden mit Beschriftung „Kurs (Abstand %)“, Legende „über oben.kurs: oben.text“ und „unter unten.kurs: unten.text“.
Regeln: punkte = genau die Top 5 aus get_chart_picks in derselben Reihenfolge. muster = Name der Leitformation, kurz (z. B. „Aufsteigendes Dreieck“). these = ein Satz, was die Formation gerade sagt. herleitung = drei bis fünf Sätze ohne Markdown: Güte und Volumen der Formation, Indikatoren, wo der Kurs steht, welche Marke bestätigt und welche bricht. oben/unten = die zwei Marken der Formation, die entscheiden (Linie, Zone, Hoch/Tief aus get_chart_picks bzw. detect_patterns), oben über unten, beide höchstens 20 % vom Kurs; text zwei bis vier Wörter, was dort gilt. Keine Kauf- oder Verkaufsempfehlung.`,
    schema: { kernsatz: str('ein Satz: was heute über die fünf Charts hinweg zählt'),
      punkte: z.array(z.object({ wert: str('Kürzel wie in get_chart_picks, z. B. RHM oder BTCUSDT'), muster: str('Name der Formation'), these: str('ein Satz'),
        herleitung: str('drei bis fünf Sätze, Fließtext ohne Markdown'),
        oben: z.object({ kurs: num('Marke über dem Kurs'), text: str('zwei bis vier Wörter, was über der Marke gilt') }),
        unten: z.object({ kurs: num('Marke unter dem Kurs'), text: str('zwei bis vier Wörter, was unter der Marke gilt') }) })).describe('die Top 5 in Reihenfolge') },
    async run({ kernsatz, punkte }, ctx) {
      if (ctx?.raum !== 'routine') throw new Error('save_briefing gibt es nur in der Morgenlage');
      const picks = (await analysen('/api/charts')).werte.slice(0, 5);
      const fehler = [], hinweise = [], vorschau = [];
      const zahl = (x) => x.toLocaleString('de-DE', { minimumFractionDigits: x >= 10000 || Number.isInteger(x) ? 0 : 2, maximumFractionDigits: x >= 10000 || Number.isInteger(x) ? 0 : 2 });   // wie auf der Seite
      const saetze = (t) => String(t || '').split(/(?<=[.!?])\s+(?=[A-ZÄÖÜ„])/).filter(Boolean).length;
      if (!kernsatz || kernsatz.length > 220) fehler.push('kernsatz: ein Satz, höchstens 220 Zeichen');
      if (!Array.isArray(punkte) || punkte.length !== picks.length) fehler.push(`punkte: genau ${picks.length} Einträge (Top ${picks.length} aus get_chart_picks)`);
      (punkte || []).forEach((p, i) => {
        const x = picks[i], name = `Punkt ${i + 1}`;
        if (!x) return;
        if (![x.wert.symbol, x.wert.id].includes(String(p.wert || '').toUpperCase())) fehler.push(`${name}: erwartet ${x.wert.symbol} (Platz ${i + 1} in get_chart_picks), nicht ${p.wert}`);
        if (!p.muster || p.muster.length > 40) fehler.push(`${name}: muster kurz, höchstens 40 Zeichen`);
        if (!p.these || p.these.length > 200 || saetze(p.these) > 1) fehler.push(`${name}: these genau ein Satz, höchstens 200 Zeichen`);
        const n = saetze(p.herleitung);
        if (n < 3 || n > 6 || String(p.herleitung).length > 1100) fehler.push(`${name}: herleitung drei bis fünf Sätze (jetzt ${n}), höchstens 1.100 Zeichen`);
        if (/\*\*|^#|^\s*[-*]\s/m.test(p.herleitung || '')) fehler.push(`${name}: herleitung ohne Markdown, nur Fließtext`);
        const o = +p.oben?.kurs, u = +p.unten?.kurs, k = +x.kurs;
        if (!(o > u && u > 0)) { fehler.push(`${name}: oben.kurs muss über unten.kurs liegen`); return; }
        for (const [art, v] of [['oben', o], ['unten', u]]) {
          if (Math.abs(v - k) / k > 0.2) fehler.push(`${name}: ${art}.kurs ${zahl(v)} liegt mehr als 20 % vom Kurs ${zahl(k)} entfernt, der Chart würde gestaucht`);
          const t = p[art]?.text || '';
          if (!t || t.split(/\s+/).length > 5 || t.length > 40) fehler.push(`${name}: ${art}.text zwei bis vier Wörter`);
          if (!String(p.herleitung || '').includes(zahl(v))) hinweise.push(`${name}: ${zahl(v)} kommt in der herleitung nicht wörtlich vor und wird nicht markiert`);
        }
        const bekannt = x.formationen.flatMap((f) => [...f.linien.map((l) => l.p2), ...(f.zone || []), f.ziel].filter((v) => v != null));
        for (const v of [o, u]) if (!bekannt.some((b) => Math.abs(b - v) / v < 0.005)) hinweise.push(`${name}: ${zahl(v)} ist keine Linie, Zone oder Ziel der erkannten Formationen; passt sie wirklich?`);
        const abst = (v) => `${v > k ? '+' : '−'}${(Math.abs(v - k) / k * 100).toFixed(1).replace('.', ',')} %`;
        vorschau.push(`${i + 1}. ${x.wert.name} – ${p.muster}: grüne Linie ${zahl(o)} (${abst(o)}) „${p.oben.text}“, rote Linie ${zahl(u)} (${abst(u)}) „${p.unten.text}“`);
      });
      if (fehler.length) return { ok: false, fehler, hinweise, hinweis: 'Nichts gespeichert. Korrigieren und save_briefing erneut aufrufen.' };
      ctx.briefing = { kernsatz, punkte: punkte.map((p, i) => ({ ...p, wert: picks[i].wert.symbol })) };
      return { ok: true, vorschau, marken_im_text: 'Zahlen deutsch wie oben geschrieben (z. B. 4.126,64 oder 1.099), dann werden sie markiert.', hinweise,
        hinweis: 'Gespeichert wird zusammen mit der Morgenlage. Erneuter Aufruf ersetzt den Entwurf.' };
    } },
  { name: 'get_chart_picks', nurLesen: true,
    beschreibung: 'Die Vorauswahl der Startseite von Analysen: alle Werte der Watchlist nach Auffälligkeit (0–100) sortiert, je Wert Zeitrahmen, wichtigste Formationen mit Abstand zur Linie in ATR und rechnerischem Ziel, fertige Alarmvorschläge (zum Anlegen: create_alert oder die Regel aus vorschlaege) und deine letzte Einordnung. Für „Was ist auffällig?“ und „Welcher Chart lohnt einen Blick?“.',
    schema: { n: num('Anzahl Werte, Standard 5').optional() },
    async run({ n = 5 }) {
      const d = await analysen('/api/charts');
      return { stand: datum(d.stand), claude_stand: d.claude ? datum(d.claude.zeit) : null,
        werte: d.werte.slice(0, Math.max(1, Math.min(20, n))).map((x) => ({ symbol: x.wert.symbol, name: x.wert.name, tf: x.tf, kurs: x.kurs, auffaelligkeit: x.auffaelligkeit, veraenderung_pct: x.veraenderung_pct,
          formationen: x.formationen.map((f) => ({ name: f.name, richtung: f.richtung, status: f.status, guete: f.guete, volumen_ausbruch: f.volumen, abstand_atr: f.naehe_atr, ziel: f.ziel,
            linien_jetzt: f.linien.map((l) => l.p2), zone: f.zone || undefined })),
          vorschlaege: x.vorschlaege.map((v) => v.titel), einordnung: d.claude?.werte?.[x.wert.symbol]?.text })) };
    } },
  { name: 'detect_patterns', nurLesen: true,
    beschreibung: 'Erkennt Chartformationen nach festen Regeln (Pivots mit ATR-Toleranzen): Unterstützung/Widerstand, Trendlinien (intakt/gebrochen), Dreiecke, Keile, Doppelhoch/-tief, Schulter-Kopf-Schulter, Flaggen, Kerzenmuster, Golden/Death Cross, RSI, 100-Kerzen-Hoch/-Tief. Dieselbe Erkennung nutzt der Alarmdienst. Ist iC Charts mit diesem Wert offen, werden die Treffer eingezeichnet. Für Formationen immer dieses Werkzeug statt Augenmaß; 4h wird als 1h geprüft.',
    schema: { wert: str('Kürzel oder ISIN'), tf: z.enum(['1m', '5m', '15m', '1h', '4h', '1d']).optional(), sensitivity: z.enum(['fein', 'normal', 'grob']).optional(), draw: z.boolean().describe('im offenen Chart einzeichnen, Standard true').optional() },
    async run({ wert, tf = '15m', sensitivity = 'normal', draw = true }, ctx) {
      const w = await wertFinden(wert);
      const tfa = tf === '4h' ? '1h' : tf;
      const d = await analysen(`/api/formationen?wert=${w.id}&tf=${tfa}&staerke=${{ fein: 3, normal: 5, grob: 8 }[sensitivity] || 5}&ohne_kerzen=1`);
      let gezeichnet = false;
      if (draw && ctx?.chart && d.formationen.length) {
        try { const st = await ctx.chart('get_state', {}); if (st?.symbol === w.symbol) { await ctx.chart('draw_formations', { formationen: d.formationen }); gezeichnet = true; } } catch (e) { /* Chart nicht offen */ }
      }
      return { symbol: w.symbol, tf: tfa, kerzen: d.kerzen_gesamt, anzahl: d.formationen.length, eingezeichnet: gezeichnet,
        formationen: d.formationen.map((f) => ({ art: f.art, name: f.name, richtung: f.richtung, status: f.status, hinweis: f.text || undefined, ereignis: f.ereignis_t ? datum(f.ereignis_t) : undefined, zone: f.zone || undefined,
          linien: f.linien.map((l) => ({ art: l.art, von: datum(l.t1), p1: l.p1, bis: datum(l.t2), p2: l.p2, t1: l.t1, t2: l.t2 })), punkte: f.punkte.map(([t, p]) => [datum(t), p]) })),
        hinweis: 'Formationen sind Ermessenssache und kein Handelssignal. Für einen Alarm auf eine Linie create_alert mit kind line und t1/p1/t2/p2 aus linien.' };
    } },
  { name: 'create_alert', nurLesen: false,
    beschreibung: 'Legt einen Alarm im Alarmdienst an: wird alle 30 s auf dem Server geprüft und per Telegram gemeldet, auch bei geschlossenem Browser; danach schickt Claude automatisch eine Zweitmeinung hinterher. kind: price (Kurs kreuzt price), line (Schlusskurs bricht die Linie durch zwei Punkte: t1/p1/t2/p2 als Unix-Sekunden aus detect_patterns oder from_bars_ago/to_bars_ago mit tf), zone (Schlusskurs verlässt price_low..price_high), pattern (Formation der Arten in patterns wird bestätigt, z. B. doppeltief, sks, dreieck_sym, trendlinie für gebrochene Trendlinien), book (Orderbuch-Kennzahl je Minute: metric ungleich (−1..1), spread_bp, tiefe_bid, tiefe_ask, updates; comparison above/below threshold; minutes in Folge). direction above/below/both. Richtung: Widerstand und Abwärtstrend meist above, Unterstützung und Aufwärtstrend meist below.',
    schema: { wert: str('Kürzel oder ISIN'), kind: z.enum(['price', 'line', 'zone', 'pattern', 'book']), tf: z.enum(['1m', '5m', '15m', '1h', '1d']).optional(), direction: z.enum(['above', 'below', 'both']).optional(),
      price: num('price: Kursmarke').optional(), t1: num('line: Unix-Sekunden Punkt 1').optional(), p1: num('line: Preis Punkt 1').optional(), t2: num('line: Unix-Sekunden Punkt 2').optional(), p2: num('line: Preis Punkt 2').optional(),
      from_bars_ago: num('line: alternativ Kerzen zurück für Punkt 1').optional(), to_bars_ago: num('line: Kerzen zurück für Punkt 2').optional(),
      price_low: num('zone: unten').optional(), price_high: num('zone: oben').optional(), patterns: z.array(z.string()).describe('pattern: Arten').optional(),
      metric: z.enum(['ungleich', 'spread_bp', 'tiefe_bid', 'tiefe_ask', 'updates']).optional(), comparison: z.enum(['above', 'below']).optional(), threshold: num('book: Schwelle').optional(), minutes: num('book: Minuten in Folge, Standard 1').optional(),
      note: str('kurze Notiz, erscheint in der Telegram-Nachricht').optional(), once: z.boolean().describe('nur einmal melden, Standard true (pattern/book: false)').optional() },
    async run(i, ctx) {
      const w = await wertFinden(i.wert);
      const tf = i.tf || '15m';
      const regel = { wert: w.id, tf, richtung: { above: 'ueber', below: 'unter', both: 'beide' }[i.direction || 'both'], notiz: String(i.note || '').slice(0, 200), quelle: ctx?.quelle || 'claude' };
      if (i.once !== undefined) regel.einmalig = !!i.once;
      if (i.kind === 'price') Object.assign(regel, { art: 'preis', preis: +i.price });
      else if (i.kind === 'zone') Object.assign(regel, { art: 'zone', lo: Math.min(+i.price_low, +i.price_high), hi: Math.max(+i.price_low, +i.price_high) });
      else if (i.kind === 'pattern') Object.assign(regel, { art: 'formation', arten: i.patterns || [] });
      else if (i.kind === 'book') Object.assign(regel, { art: 'buch', kennzahl: i.metric, vergleich: i.comparison === 'below' ? 'unter' : 'ueber', schwelle: +i.threshold, minuten: Math.max(1, Math.round(i.minutes || 1)) });
      else if (i.kind === 'line') {
        let { t1, p1, t2, p2 } = i;
        if ((t1 == null || t2 == null) && i.from_bars_ago != null && i.to_bars_ago != null) {
          const k = (await analysen(`/api/kerzen?wert=${w.id}&tf=${tf}&anzahl=400`)).kerzen;
          const bei = (n) => k[Math.max(0, k.length - 1 - Math.round(n))];
          t1 = bei(i.from_bars_ago)[0]; t2 = bei(i.to_bars_ago)[0];
          if (p1 == null) p1 = bei(i.from_bars_ago)[4];
          if (p2 == null) p2 = bei(i.to_bars_ago)[4];
        }
        if ([t1, p1, t2, p2].some((x) => x == null)) throw new Error('line braucht t1/p1/t2/p2 (aus detect_patterns) oder from_bars_ago/to_bars_ago mit Preisen');
        Object.assign(regel, { art: 'linie', t1: Math.round(t1 > 1e11 ? t1 / 1000 : t1), p1: +p1, t2: Math.round(t2 > 1e11 ? t2 / 1000 : t2), p2: +p2 });
      } else throw new Error('kind unbekannt');
      const r = await post('/api/alarme', regel);
      const tg = await analysen('/api/alarme');
      return { ok: true, id: r.id, symbol: r.symbol, bedingung: r.bedingung, tf: r.tf, richtung: r.richtung, einmalig: r.einmalig, telegram: tg.telegram.token && tg.telegram.chat ? 'eingerichtet: Meldung kommt per Telegram' : 'nicht eingerichtet: Meldung erscheint nur im Verlauf unter Analysen → Formationen & Alarme' };
    } },
  { name: 'list_alerts', nurLesen: true,
    beschreibung: 'Alle Alarme mit Bedingung und Status, die zuletzt ausgelösten Meldungen (auch Zweitmeinungen) und der Telegram-Stand.',
    schema: {},
    async run() {
      const d = await analysen('/api/alarme');
      return { telegram: d.telegram.token && d.telegram.chat ? 'eingerichtet' : 'nicht eingerichtet', geprueft: datum(d.geprueft), fehler: d.fehler || undefined,
        alarme: d.regeln.map((r) => ({ id: r.id, symbol: r.symbol, bedingung: r.bedingung, tf: r.tf, richtung: r.richtung, aktiv: r.aktiv, einmalig: r.einmalig, ausgeloest: r.ausgeloest ? datum(r.ausgeloest) : null, notiz: r.notiz || undefined, quelle: r.quelle })),
        verlauf: d.verlauf.slice(0, 15).map((v) => ({ zeit: datum(v.zeit), wert: v.wert, art: v.art, text: v.text, telegram: v.telegram })) };
    } },
  { name: 'delete_alert', nurLesen: false, beschreibung: 'Löscht einen Alarm (id aus list_alerts).', schema: { id: z.string() },
    async run({ id }) { return analysen('/api/alarme?id=' + encodeURIComponent(id), { method: 'DELETE' }); } },
  { name: 'get_data_notes', nurLesen: true, beschreibung: 'Die Hinweise zur Datenqualität (DATENQUALITAET.md): Herkunft q der Kerzen (sip/iex/xetra/binance), 15-Minuten-Verzögerung, was das Buch je Minute enthält, Zeit und Verdichtung. Lesen, bevor du Volumen, Lücken oder Sprünge deutest.', schema: {},
    async run() { const fs = await import('node:fs'); const path = await import('node:path'); const url = await import('node:url'); const p = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '../DATENQUALITAET.md'); return { text: fs.readFileSync(p, 'utf8').slice(0, 12000) }; } },

  // --- Werkstatt
  { name: 'get_workshop_reference', nurLesen: true, beschreibung: 'Die Anleitung für die Werkstatt: welche Variablen und Hilfsfunktionen der Analyse-Code hat (ereignisse, lauf, analyse(), kerzen()), das Ausgabeformat und ein Beispiel. Vor dem ersten run_analysis lesen.', schema: {},
    async run() { const r = await fetch(ANALYSEN + '/api/werkstatt/referenz'); return { text: await r.text() }; } },
  { name: 'run_analysis', nurLesen: false,
    beschreibung: 'Werkstatt: führt eigenen Python-Code gegen die Rohdaten eines DE-Werts und Tags im Sandkasten aus (kein Netz, keine Dateien, 90 s, nur Standardbibliothek). Das Ergebnis erscheint als Karte im Reiter Analysen → Werkstatt (Entwürfe). Bei einem Fehler kommt der Traceback zurück: korrigieren und mit derselben id erneut aufrufen. Vorher get_workshop_reference lesen.',
    schema: { titel: str('kurzer Titel der Analyse'), code: str('Python-Code, legt das Ergebnis in ausgabe[...]'), wert: str('Kürzel oder ISIN eines DE-Werts'), tag: str('YYYY-MM-DD oder "heute"').optional(), id: str('id eines vorhandenen Entwurfs, um ihn zu aktualisieren').optional(), beschreibung: str('ein Satz, was die Analyse zeigt').optional() },
    async run({ titel, code, wert, tag, id, beschreibung }, ctx) {
      const w = await wertFinden(wert);
      if (w.markt !== 'DE') throw new Error('Die Werkstatt rechnet auf Xetra-Rohdaten: nur deutsche Werte.');
      const t = await tagWaehlen(tag);
      const e = await post('/api/werkstatt/lauf', { titel, code, wert: w.id, tag: t, id, beschreibung, modell: ctx?.modell || '', quelle: ctx?.quelle || 'claude' }, 130000);
      if (ctx?.dashboard) { try { await ctx.dashboard('navigate', { tab: 'analysen', section: 'werkstatt', wert: w.id, tag: t }); setTimeout(() => ctx.dashboard('highlight', { title: titel }).catch(() => {}), 1500); } catch (x) { /* Dashboard nicht offen */ } }
      return werkstattKompakt(e);
    } },
  { name: 'pin_analysis', nurLesen: false, beschreibung: 'Pinnt einen Werkstatt-Entwurf an: Er wird fester Teil des Reiters Analysen → Werkstatt und für jeden Wert und Tag neu gerechnet. Nur auf Nikos Wunsch. angepinnt false löst ihn wieder.', schema: { id: z.string(), titel: z.string().optional(), beschreibung: z.string().optional(), angepinnt: z.boolean().optional() },
    async run({ id, titel, beschreibung, angepinnt = true }) { const a = await post('/api/werkstatt/anpinnen', { id, titel, beschreibung, angepinnt }); return { ok: true, id: a.id, titel: a.titel, angepinnt: a.angepinnt }; } },
  { name: 'list_workshop', nurLesen: true, beschreibung: 'Angepinnte Analysen und Entwürfe der Werkstatt mit Titel, Beschreibung und letztem Lauf.', schema: {},
    async run() { return analysen('/api/werkstatt'); } },
  { name: 'get_analysis_code', nurLesen: true, beschreibung: 'Code und Beschreibung einer Werkstatt-Analyse (id aus list_workshop), um sie zu erklären oder weiterzuentwickeln.', schema: { id: z.string() },
    async run({ id }) { const a = await analysen('/api/werkstatt/analyse?id=' + encodeURIComponent(id)); return { id: a.id, titel: a.titel, beschreibung: a.beschreibung, angepinnt: a.angepinnt, code: a.code, zuletzt: a.zuletzt ? { ...a.zuletzt, ergebnis: undefined } : undefined }; } },
  { name: 'delete_analysis', nurLesen: false, beschreibung: 'Löscht eine Werkstatt-Analyse endgültig (id aus list_workshop). Nur auf Nikos Wunsch.', schema: { id: z.string() },
    async run({ id }) { return analysen('/api/werkstatt?id=' + encodeURIComponent(id), { method: 'DELETE' }); } },

  // --- Gedächtnis
  { name: 'get_dossier', nurLesen: true, beschreibung: 'Das Dossier eines Werts: was Claude über Tage hinweg über Spread, Wände, Auktionen, Retail-Buch und Formationen gelernt hat. Lesen, bevor du einen Wert einordnest.', schema: { wert: str('Kürzel oder ISIN') },
    async run({ wert }) { const w = await wertFinden(wert); const d = await analysen('/api/dossier?wert=' + w.id); return { symbol: w.symbol, stand: d.stand ? datum(d.stand) : null, text: d.text || '(noch kein Dossier)' }; } },
  { name: 'save_dossier', nurLesen: false, beschreibung: 'Schreibt das Dossier eines Werts neu (ganzer Text, Markdown, höchstens etwa 25 Zeilen). Nur Beobachtungen, die über mehrere Tage tragen, mit Datum; Veraltetes kürzen. Normalerweise nur in der Nachbesprechung oder auf Nikos Wunsch.', schema: { wert: str('Kürzel oder ISIN'), text: z.string() },
    async run({ wert, text }) { const w = await wertFinden(wert); return post('/api/dossier', { wert: w.id, text }); } },
  { name: 'note_expectation', nurLesen: false, beschreibung: 'Journal: Trägt eine Einschätzung mit Richtung und Horizont ein, die später automatisch gegen den Kurs geprüft wird (Trefferquote in der Bilanz). Immer aufrufen, wenn du eine Richtung für einen Zeitraum nennst. richtung steigt/faellt/seitwaerts, horizont 1h, 4h, 1d, 1w. Schwellen: 0,1 % (1h), 0,2 % (4h), 0,3 % (1d), 0,6 % (1w).', schema: { wert: str('Kürzel oder ISIN'), richtung: z.enum(['steigt', 'faellt', 'seitwaerts']), horizont: z.enum(['1h', '4h', '1d', '1w']), begruendung: str('ein Satz') },
    async run({ wert, richtung, horizont, begruendung }, ctx) { const w = await wertFinden(wert); const e = await post('/api/journal', { wert: w.id, richtung, horizont, begruendung, modell: ctx?.modell || '', quelle: ctx?.quelle || 'chat' }); return { ok: true, id: e.id, symbol: e.symbol, kurs: e.kurs, faellig: datum(e.faellig) }; } },
  { name: 'get_scorecard', nurLesen: true, beschreibung: 'Bilanz des Journals: Trefferquote der Einschätzungen gesamt, je Horizont, je Modell und je Wert, plus die letzten Einträge mit Ergebnis.', schema: { n: num('Anzahl der letzten Einträge, Standard 15').optional() },
    async run({ n = 15 }) { const b = await analysen('/api/journal?n=' + n); return { ...b, letzte: b.letzte.map((e) => ({ zeit: datum(e.zeit), symbol: e.symbol, richtung: e.richtung, horizont: e.horizont, kurs: e.kurs, ergebnis: e.ergebnis || 'offen', aenderung_pct: e.aenderung_pct, modell: e.modell, begruendung: e.begruendung })) }; } },
  { name: 'get_notes', nurLesen: true, beschreibung: 'Die letzten Notizen: Morgenlagen, Nachbesprechungen und freie Notizen.', schema: { n: num('Anzahl, Standard 5').optional(), art: z.enum(['morgenlage', 'nachbesprechung', 'notiz']).optional() },
    async run({ n = 5, art }) { const d = await analysen(`/api/notizen?n=${n}${art ? '&art=' + art : ''}`); return { notizen: d.notizen.map((e) => ({ zeit: datum(e.zeit), art: e.art, modell: e.modell, text: e.text })) }; } },
  { name: 'save_note', nurLesen: false, beschreibung: 'Speichert eine freie Notiz (z. B. eine Beobachtung, die Niko festhalten möchte). Erscheint unter Analysen → Claude.', schema: { text: z.string(), wert: str('Kürzel, optional').optional() },
    async run({ text, wert }, ctx) { let id; if (wert) id = (await wertFinden(wert)).id; return post('/api/notizen', { art: 'notiz', text, modell: ctx?.modell || '', wert: id }); } },
  { name: 'get_paper_portfolio', nurLesen: true, beschreibung: 'Papierhandel (simuliert, kein echtes Geld): Depot je Währung (Guthaben, Positionen, Ergebnis), offene Positionen, offene Orders, abgeschlossene Trades mit Ergebnis (FIFO) und das Journal mit deinen Bewertungen. Orders gibt Niko nur in iC Charts auf; du selbst handelst nicht.', schema: { n: num('Anzahl der letzten Trades, Standard 20').optional() },
    async run({ n = 20 }) {
      const d = await dashboard('/api/papier');
      return { stand: datum(d.stand), seit: datum(d.seit), konto: d.konto, statistik: d.statistik,
        positionen: d.positionen.map((p) => ({ symbol: p.symbol, name: p.name, waehrung: p.waehrung, stueck: p.menge, einstand: r2(p.einstand), kurs: p.kurs, ergebnis: p.ergebnis, ergebnis_pct: p.ergebnis_pct, seit: datum(p.seit) })),
        offene_orders: d.offen.map((o) => ({ nr: o.nr, symbol: o.symbol, seite: o.seite, art: o.art, stueck: o.menge, limit: o.limit_preis, stopp: o.stopp_preis, notiz: o.notiz, hinweis: o.hinweis, erstellt: datum(o.erstellt) })),
        trades: d.trades.slice(0, n).map((t) => ({ nr: t.nr, symbol: t.symbol, waehrung: t.waehrung, stueck: t.menge, einstieg: r2(t.einstieg), ausstieg: r2(t.ausstieg), eroeffnet: datum(t.eroeffnet), geschlossen: datum(t.geschlossen), ergebnis: t.ergebnis, ergebnis_pct: t.ergebnis_pct, gebuehren: t.gebuehren, notiz_kauf: t.notiz_kauf, notiz_verkauf: t.notiz_verkauf })),
        journal: d.journal.slice(0, 10).map((j) => ({ art: j.art, von: j.art === 'eigen' ? 'Niko' : 'Claude', trade: j.bezug, symbol: j.symbol, frage: j.titel, zeit: datum(j.zeit), text: j.text })),
        hinweis: 'Deutsche Werte werden zum Datenstand 15 Minuten verzögert ausgeführt, nur in der Xetra-Hauptsitzung; nur long, ganze Orders, feste Gebühr je Order.' };
    } },
  { name: 'search_paper_journal', nurLesen: true, beschreibung: 'Durchsucht das ganze Journal des Papierhandels (Tagebuch): Nikos eigene Einträge (art eigen) und deine Einträge (woche = Wochenrückblick, tag = Tagesrückblick, frage = Antwort auf eine Frage von Niko, trade = Bewertung eines Trades). Nutze es, um wiederkehrende Muster mit Datum zu belegen.', schema: { q: str('Suchwörter, alle müssen vorkommen (Text, Frage, Kürzel); leer = alle').optional(), wer: z.enum(['du', 'claude']).optional().describe('du = nur Nikos eigene Einträge'), symbol: str('Kürzel, z. B. SAP oder BTCUSDT').optional(), n: num('Anzahl, Standard 30').optional() },
    async run({ q = '', wer, symbol, n = 30 }) {
      const d = await dashboard(`/api/papier/journal?q=${encodeURIComponent(q)}&n=${n}${wer ? '&wer=' + wer : ''}${symbol ? '&symbol=' + encodeURIComponent(symbol) : ''}`);
      return { eintraege: d.eintraege.map((j) => ({ nr: j.nr, art: j.art, von: j.art === 'eigen' ? 'Niko' : 'Claude', zeit: datum(j.zeit), symbol: j.symbol, trade: j.bezug, frage: j.titel, text: j.text })) };
    } },

  // --- Einstellungen per Nachricht (Telegram): Watchlist erweitern, Briefing-Zeit ändern
  { name: 'add_to_watchlist', nurLesen: false,
    beschreibung: 'Nimmt einen Wert auf die Watchlist; ab dann sammelt iC Delta ihn (deutsche Werte mit Tagesverlauf und Tageshistorie, US live über IEX, Krypto und Rohstoffe live von Binance). Suche per Kürzel, Name, ISIN oder Binance-Paar in den offiziellen Listen; markt eingrenzen, wenn der Name mehrdeutig ist. Nur auf Nikos ausdrücklichen Wunsch. Liefert das Werkzeug mehrere Treffer, nenne sie und frag nach, welcher gemeint ist.',
    schema: { wert: str('Kürzel, Name, ISIN oder Binance-Paar, z. B. AMD, Mercedes, DE0007100000, DOGEUSDT'), markt: z.enum(['DE', 'US', 'KRYPTO', 'ROHSTOFFE']).optional() },
    async run({ wert, markt }) {
      const q = String(wert || '').trim(), Q = q.toUpperCase();
      if (q.length < 2) throw new Error('Bitte Kürzel, Name oder ISIN nennen');
      const maerkte = markt ? [markt] : ['DE', 'US', 'KRYPTO', 'ROHSTOFFE'];
      const listen = await Promise.all(maerkte.map(async (m) => ((await dashboard(`/api/suche?q=${encodeURIComponent(q)}&markt=${m}`)) || []).map((x) => ({ ...x, markt: m }))));
      const treffer = listen.flat();
      if (!treffer.length) throw new Error(`"${q}" steht in keiner der offiziellen Listen (Xetra, USA, Binance)`);
      const genau = treffer.filter((x) => [x.symbol, x.id].map((v) => String(v).toUpperCase()).some((v) => v === Q || v === Q + 'USDT'));
      const maerkteMitTreffer = new Set(treffer.map((x) => x.markt));
      const wahl = genau.length === 1 ? genau[0] : !genau.length && maerkteMitTreffer.size === 1 && String(treffer[0].name).toUpperCase().startsWith(Q) ? treffer[0] : null;
      const liste = (await dashboard('/api/watchlist')).werte || [];
      const schon = (wahl ? [wahl] : genau).map((x) => liste.find((v) => v.id === x.id)).find(Boolean);
      if (schon) return { ok: true, schon_auf_der_watchlist: true, symbol: schon.symbol, name: schon.name, markt: schon.markt };
      if (!wahl) return { ok: false, frage: 'Mehrere Treffer: welcher Wert ist gemeint?', treffer: (genau.length ? genau : treffer).slice(0, 8).map((x) => ({ symbol: x.symbol, name: x.name, id: x.id, markt: x.markt })) };
      const r = await dashboard('/api/watchlist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: wahl.id, markt: wahl.markt }) });
      wl.t = 0;   // Watchlist-Zwischenspeicher verwerfen, damit die anderen Werkzeuge den neuen Wert sofort kennen
      const h = r.hinzugefuegt || wahl;
      return { ok: true, symbol: h.symbol, name: h.name, id: h.id, markt: wahl.markt,
        hinweis: { DE: 'Xetra 15 Min. verzögert; Tagesverlauf und Tageshistorie werden im Hintergrund nachgeladen.', US: 'live über IEX, alle Börsen nach 15 Minuten.', KRYPTO: 'live von Binance, ab sofort.', ROHSTOFFE: 'Perpetual-Future von Binance, live ab sofort, kein Spotpreis.' }[wahl.markt] };
    } },
  { name: 'set_briefing_time', nurLesen: false,
    beschreibung: 'Ändert die Uhrzeit der Morgenlage (Briefing an Handelstagen, Standard 08:45) oder der Nachbesprechung (Standard 17:50), Zeit in Berlin. Gilt sofort: noch heute, wenn die Zeit noch nicht vorbei ist und die Routine heute noch nicht lief, sonst ab dem nächsten Handelstag. Nur auf Nikos Wunsch.',
    schema: { uhrzeit: str('HH:MM, z. B. 07:30'), art: z.enum(['morgenlage', 'nachbesprechung']).optional() },
    async run({ uhrzeit, art = 'morgenlage' }) {
      const m = /^(\d{1,2})[:.](\d{2})$/.exec(String(uhrzeit || '').trim().replace(/\s*uhr$/i, ''));
      if (!m || +m[1] > 23 || +m[2] > 59) throw new Error(`"${uhrzeit}" ist keine Uhrzeit im Format HH:MM`);
      const zeit = m[1].padStart(2, '0') + ':' + m[2];
      const vorher = (await analysen('/api/ki'))[art];
      const neu = await post('/api/ki', { [art]: zeit });
      return { ok: true, art, vorher, jetzt: neu[art], routinen: neu.routinen ? 'eingeschaltet' : 'ausgeschaltet: die Routine läuft erst, wenn Niko sie in den Agent-Einstellungen wieder einschaltet', tage: 'Montag bis Freitag' };
    } },
].map((w) => ({ ...w, wo: 'server' }));

export const WERKZEUGE = [...SERVER, ...DASH, ...CHART];
export const WERKZEUG = Object.fromEntries(WERKZEUGE.map((w) => [w.name, w]));

// Kurze deutsche Bezeichnungen für die Anzeige im Panel
export const BEZEICHNUNG = {
  get_overview: 'liest die Watchlist', get_quote: 'liest Kurs und Buch', get_candles: 'holt Kerzen', get_chart_picks: 'liest die Chart-Vorauswahl', get_pattern_stats: 'liest den Prüfstein', chart_image: 'zeichnet ein Chartbild', strategy_check: 'prüft im Lernteil', strategy_save: 'speichert den Entwurf', strategy_run: 'öffnet den Prüfteil', list_strategies: 'liest das Ideen-Labor', get_orderbook_analysis: 'liest die Orderbuch-Analyse', detect_patterns: 'sucht Formationen',
  create_alert: 'legt einen Alarm an', list_alerts: 'liest die Alarme', delete_alert: 'löscht einen Alarm', get_data_notes: 'liest die Datenhinweise',
  get_workshop_reference: 'liest die Werkstatt-Anleitung', run_analysis: 'rechnet in der Werkstatt', pin_analysis: 'pinnt die Analyse an', list_workshop: 'liest die Werkstatt', get_analysis_code: 'liest den Code', delete_analysis: 'löscht die Analyse',
  get_dossier: 'liest das Dossier', get_paper_portfolio: 'liest das Papier-Depot', search_paper_journal: 'durchsucht das Journal', save_dossier: 'schreibt das Dossier', note_expectation: 'trägt eine Einschätzung ein', get_scorecard: 'liest die Bilanz', get_notes: 'liest die Notizen', save_note: 'speichert eine Notiz',
  add_to_watchlist: 'nimmt einen Wert auf die Watchlist', set_briefing_time: 'stellt die Briefing-Zeit',
  get_view: 'schaut auf das Dashboard', navigate: 'wechselt die Ansicht', highlight: 'hebt hervor',
  get_state: 'liest den Chart', get_bars: 'holt Kerzen aus dem Chart', find_extremes: 'sucht Hoch und Tief', screenshot: 'sieht sich den Chart an', set_symbol: 'wechselt das Symbol', set_timeframe: 'wechselt die Zeiteinheit', set_indicators: 'schaltet Indikatoren', list_indicators: 'liest das Indikator-Verzeichnis', run_script: 'führt ein Script aus', clear_script: 'entfernt das Script', add_alarm: 'setzt einen Chart-Alarm', add_hline: 'zeichnet eine Linie', draw_fibonacci: 'zeichnet Fibonacci', draw_position: 'zeichnet eine Position', clear_drawings: 'löscht Zeichnungen', undo_drawing: 'macht rückgängig', find_pivots: 'sucht Wendepunkte', draw_line: 'zeichnet eine Linie', draw_level: 'zeichnet ein Niveau', draw_zone: 'zeichnet eine Zone', draw_pattern: 'zeichnet ein Muster', annotate: 'setzt eine Notiz', optimize_strategy: 'optimiert die Strategie', replay: 'steuert das Replay', draw_formations: 'zeichnet Formationen ein',
};
