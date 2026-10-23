/*
 * server.mjs – Claude als Analyst in iC Delta (Agentenkern).
 *
 * Ein Kern, drei Räume: das Panel in der Dashboard-Hülle (panel.js, Raum "dashboard"), das Panel in iC Charts
 * (agent-panel.js, Raum "chart") und kopflose Läufe ohne Browser (Telegram-Rückkanal, Zweitmeinung zu Alarmen,
 * Morgenlage und Nachbesprechung). Werkzeuge stehen in werkzeuge.mjs: Server-Werkzeuge laufen hier in Node
 * gegen Analysen-Dienst (8813) und Dashboard (8812), Browser-Werkzeuge werden per WebSocket an das Panel
 * geschickt und dort ausgeführt.
 *
 * Zwei Treiber hinter demselben Kern: das Claude Agent SDK mit dem Claude-Login dieses Rechners (Standard) und
 * OpenRouter (treiber-openrouter.mjs, Schlüssel in ~/.config/marktdaten/ki.env als OPENROUTER_KEY=..., legt der
 * Nutzer selbst an). Anbieter und Modelle stehen in den KI-Einstellungen des Analysen-Diensts (/api/ki).
 *
 * Sicherheit: Bindung nur an HOST (localhost und Tailscale), WebSocket nur mit Origin des Dashboards (:8812),
 * kopflose Endpunkte nur von 127.0.0.1. Keine Börsenanbindung: Claude liest, rechnet, zeichnet, meldet.
 *
 * Start: PORT=8814 HOST=127.0.0.1,<Tailscale-IP> node server.mjs   (LaunchAgent com.m37.ic-delta-agent)
 * Umgebung: CLAUDE_MODEL (Vorgabe, wenn keine Einstellung), AGENT_MOCK=1 (Probelauf ohne Claude)
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';
import { query, tool, createSdkMcpServer } from '@anthropic-ai/claude-agent-sdk';
import { WERKZEUGE, BEZEICHNUNG, analysen, dashboard, wertFinden, heute } from './werkzeuge.mjs';
import { openrouterLauf, openrouterSchluessel } from './treiber-openrouter.mjs';
import * as Pruefstand from './pruefstand.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '../src/marktdaten/dashboard/static/ic-charts');   // Quellcode von iC Charts zum Nachlesen (Raum chart)
try {                                                                             // .env (optional), nur vom Server gelesen
  for (const line of fs.readFileSync(path.join(here, '.env'), 'utf8').split('\n')) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line); if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch (e) { /* keine .env */ }
const PORT_BASIS = +(process.env.MARKTDATEN_PORT || 8812);            // Dashboard; Analysen = +1, Agent = +2 (echt 8812, Demo 8822)
const PORT = +(process.env.PORT || PORT_BASIS + 2);
const HOSTS = (process.env.HOST || '127.0.0.1').split(',').map((h) => h.trim()).filter(Boolean);
const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const NAMEN = ["localhost", "127.0.0.1", ...HOSTS].map(esc).join("|");
const RECHNER = esc(os.hostname().replace(/\.local$/i, ""));                   // z. B. per Tailscale-Name, auch mit Domain
const ERLAUBT = new RegExp(`^https?://(${NAMEN}|${RECHNER}(\\.[\\w.-]+)?):${PORT_BASIS}$`, "i");   // nur das Dashboard
const vomDashboard = (origin) => ERLAUBT.test(origin || '');
const MODEL = process.env.CLAUDE_MODEL || 'claude-opus-5-5';
const MOCK = process.env.AGENT_MOCK === '1';
const ROUTINE_DATEI = process.env.MARKTDATEN_DATEN ? path.join(process.env.MARKTDATEN_DATEN.replace(/^~/, os.homedir()), 'analysen', 'routinen.json') : path.join(here, '.routinen.json');

// ---------------- KI-Einstellungen (liegen beim Analysen-Dienst) ----------------
let einst = { t: 0, d: null };
async function einstellungen() {
  if (Date.now() - einst.t > 15000 || !einst.d) {
    try { einst = { t: Date.now(), d: await analysen('/api/ki') }; } catch (e) { einst.d = einst.d || { anbieter: 'claude-code', modell: MODEL, modell_routine: MODEL, zweitmeinung: true, routinen: true, morgenlage: '08:45', nachbesprechung: '17:50' }; }
  }
  return einst.d;
}
function modellFuer(raum, e) {
  const anbieter = e.anbieter || 'claude-code';
  const modell = (raum === 'dashboard' || raum === 'chart' || raum === 'telegram' || raum === 'pruefstand') ? (e.modell || MODEL) : (e.modell_routine || e.modell || MODEL);
  return { anbieter, modell };
}

// ---------------- Systemprompt ----------------
const BASIS = `Du bist Claude, der Analyst in „iC Delta · AI Engine“, dem selbst gebauten Marktdaten-Dashboard von Niko Sarge (YouTube-Kanal zu KI und Finanzen). Du sprichst Deutsch, duzt Niko und schreibst kurz und konkret.

Daten: Deutsche Aktien kommen von Xetra, 15 Minuten verzögert; das Xetra-Orderbuch (5 Stufen mit Orderanzahl, dazu ein getrenntes Retail-Buch) sammelt der Dienst nur live zwischen 09:00 und 17:30 Uhr, abends ist nur das Retail-Buch gefüllt. US-Werte laufen live über IEX (Volumen der letzten 15 Minuten unvollständig). Krypto live von Binance. Es gibt nur Werte der Watchlist. Alles ist Rückschau und Forschung: keine Anlageberatung, keine Börsenanbindung, kein echtes Geld.

Arbeitsweise:
- Zahlen immer über Werkzeuge holen, nie raten. Allgemeine Fragen: zuerst get_overview. Ein Wert: get_quote, bei deutschen Werten dazu get_orderbook_analysis (teil wählen) und get_dossier. Chartfragen: get_candles oder im offenen Chart get_state. Bevor du Volumen, Lücken oder Sprünge deutest: get_data_notes.
- Am Anfang der Nachricht steht oft ein Kontext: Reiter, Wert, Tag und die Karte, die Niko gerade sieht. Beziehe dich darauf; get_view liefert ihn frisch.
- Antworte in zwei bis sechs Sätzen oder einer kurzen Liste, mit konkreten Zahlen und Uhrzeiten. Keine Einleitung, keine Wiederholung der Frage, keine Floskeln. Fachbegriffe beim ersten Mal in einem Halbsatz erklären.
- Ehrlich bleiben: sag, was die Daten tragen und was nicht (ein Tag ist keine Statistik, 15 Minuten Verzögerung, dünnes Buch abends, Formationen sind Ermessenssache). Wenn etwas fehlt, sag warum (Sammler lief nicht, Wert nicht auf der Watchlist).
- Nennst du eine Richtung für einen Zeitraum, trag sie mit note_expectation ein und erwähne das in einem Halbsatz. Deine Trefferquote steht in get_scorecard; nenn sie, wenn Niko nach deiner Verlässlichkeit fragt.

Werkstatt: Für Auswertungen, die es im Reiter Analysen nicht gibt, schreibst du Python gegen die Rohdaten: erst get_workshop_reference lesen, dann run_analysis; bei einem Fehler korrigieren und mit derselben id erneut laufen lassen; danach das Ergebnis in zwei Sätzen deuten. Kurzer, lesbarer Code mit deutschen Kommentaren, jede Karte mit einem \`text\`, der die Grenzen nennt. pin_analysis nur, wenn Niko es sagt.

Strategien: Beschreibt Niko eine Handelsstrategie, die getestet werden soll, übersetze sie für das Ideen-Labor: mit strategy_check im Lernteil auf Fehler prüfen (den gesperrten Prüfteil sieht vorher niemand), dann mit strategy_save als Entwurf speichern und sagen, dass er ihn unter Analysen → Ideen-Labor prüfen kann. strategy_run (Prüfteil öffnen, einmalig) nur, wenn Niko das ausdrücklich will. Ergebnisse ehrlich einordnen: Median-Vorsprung gegen Kaufen und Halten, Zahl der Trades, Zahl der Versuche.

Alarme: Alarme, die per Telegram kommen sollen, immer mit create_alert anlegen (add_alarm klingelt nur im offenen Browser). Für Linien zuerst detect_patterns, damit die Linie die der Erkennung ist; Richtung passend wählen (Widerstand und Abwärtstrend meist above, Unterstützung und Aufwärtstrend meist below). Alltagssprache in Regeln übersetzen, auch mit Buchbedingungen (kind book: Ungleichgewicht, Spread, Tiefe). Nach dem Anlegen die Bedingung in einem Satz wiederholen. Jeder ausgelöste Alarm bekommt von dir automatisch eine Zweitmeinung per Telegram.

Papierhandel: Niko übt mit simulierten Orders, die er nur in iC Charts aufgibt; der Reiter Depot zeigt Depot, Orders, Trades und dein Journal. get_paper_portfolio liest das alles. Du gibst selbst keine Orders auf und empfiehlst keine Käufe oder Verkäufe einzelner Werte; du hilfst beim Handwerk (Einstieg, Ausstieg, Stopp, Größe).`;

const RAUM = {
  dashboard: `\nDashboard: Du sitzt in einem Panel neben dem ganzen Dashboard (Reiter Kurse, Depot, iC Charts, Analysen, Speicher, Datenquellen). Wenn du von etwas Sichtbarem sprichst, zeig es: navigate wechselt Reiter, Wert, Tag und Unterreiter, highlight hebt eine Karte hervor (höchstens zwei je Antwort). „Zeig mir …“ heißt hinnavigieren. Chart-Werkzeuge (zeichnen, Indikatoren, Scripts) gehen, sobald iC Charts offen ist: navigate mit tab icharts und dem Wert öffnet ihn. Linien immer durch echte Wendepunkte (find_pivots oder detect_patterns).`,
  chart: `\nChart: Du sitzt direkt in iC Charts. Rufe zuerst get_state auf, wenn du etwas über den Chart sagen oder tun sollst. Für Fibonacci oder Positionen erst find_extremes oder get_bars und echte Preise übergeben. Chartanalyse: get_state, detect_patterns (zeichnet ein), bei Bedarf find_pivots und screenshot; eigene Linien mit draw_line durch zwei echte Wendepunkte, Niveaus mit draw_level, Zonen mit draw_zone, Muster mit draw_pattern, Hinweise mit annotate; höchstens etwa acht Figuren, dann einmal screenshot zur Kontrolle. Scripts in der Mini-Script-Sprache, nicht Pine Script. Strategie-Labor: Script mit $-Platzhaltern, optimize_strategy mit sinnvollen Bereichen, ehrlich bewerten (wie viele Kombinationen schlagen Buy & Hold, hält der Robustheitstest), beste Kombination mit run_script setzen. Du darfst den Quellcode der App lesen (Read, Grep, Glob), um Formeln zu erklären.`,
  telegram: `\nTelegram: Du antwortest in einem Telegram-Chat auf Nikos Handy. Format für Telegram (wird in Telegram-Formatierung umgewandelt): erste Zeile '# ' mit passendem Symbol und kurzem Titel, z. B. '# 📈 SAP · Dreiecksausbruch'. Darunter ein Satz '**Kurz:** …' mit dem Wichtigsten. Dann zwei bis vier Abschnitte mit '## ' und Symbol (z. B. '## 📊 Lage', '## ⚖️ Dafür und dagegen', '## 🎯 Marken'), darin knappe Punkte mit '- ' (Zahlen **fett**, Preise mit Einheit, Uhrzeiten nennen). Längere Erklärungen, Herleitungen oder Fachbegriffe in einen aufklappbaren Block: eine Zeile '::: Details', die Erklärung, dann eine Zeile ':::'. Keine Tabellen, keine Links, keine Code-Blöcke. Ganze Nachricht ohne Details-Block höchstens etwa 15 Zeilen. Dashboard und Chart sind nicht offen, nur Server-Werkzeuge. Zeigt ein Chart, worüber du schreibst (Formation, Ausbruch, Marke, Ergebnis aus dem Ideen-Labor), häng mit chart_image ein Bild an, höchstens zwei.`,
  wache: `\nZweitmeinung: Ein Alarm hat ausgelöst. Ordne ihn in höchstens vier Sätzen ein: Hol get_quote, get_candles (tf des Alarms, 60 Kerzen) und bei deutschen Werten get_orderbook_analysis (teil b und c, heute) sowie detect_patterns. Sag, ob Volumen, Buch und Formationen den Alarm stützen oder eher nicht, und was das nicht beweist. Keine Handlungsempfehlung. Nennst du eine Richtung für die nächsten Stunden, trag sie mit note_expectation ein (horizont 4h). Häng mit chart_image genau ein Bild des Charts an (Zeitrahmen des Alarms, die betroffene Formation, falls erkannt). Antworte nur mit dem Text. Format: erste Zeile '**Einordnung:** …' (ein Satz: stützen die Daten den Alarm oder eher nicht?), dann '## 📊 Was die Daten zeigen' mit drei bis fünf Punkten '- ' (Volumen, Buch, Formation, Indikatoren, Zahlen **fett**), dann '## ⚠️ Was es nicht beweist' mit einem Punkt. Keine Überschrift mit '# ', die setzt das System.`,
  pruefstand: `\nIdeen-Labor: Du übersetzt eine in Worten beschriebene Handelsstrategie in die Formelsprache von iC Charts, damit sie mit gesperrtem Prüfteil getestet werden kann.\n${Pruefstand.SPRACHE}\nVorgehen: 1) Script schreiben. Zahlen, die Niko festgelegt hat, bleiben fest. Wo er nichts festgelegt hat und man sinnvoll variieren kann, einen $parameter mit kleinem Bereich (höchstens zwei Parameter, je höchstens 8 Werte). Stop, Kursziel und Haltedauer gehören ins Feld ausstieg, nicht ins Script. 2) Mit strategy_check auf Fehler prüfen und korrigieren, bis es läuft und Trades erzeugt. strategy_check zeigt nur den Lernteil; den Prüfteil sieht vor dem Prüfen niemand. Du übersetzt nur: keine Regeln erfinden oder verändern, um den Lernteil schöner zu machen. 3) Was sich nicht ausdrücken lässt (Leerverkauf, Positionsgrößen, Fundamentaldaten, Nachrichten, Intraday) in grenzen nennen und die nächstliegende Umsetzung wählen.\nAntworte am Ende nur mit einem JSON-Block:\n\`\`\`json\n{"titel": "…", "beschreibung": "zwei bis vier Sätze: So habe ich dich verstanden …", "annahmen": ["…"], "grenzen": ["…"], "script": "…", "params": [{"name": "…", "from": 0, "to": 0, "step": 1, "warum": "…"}], "ausstieg": {"stop_pct": null, "ziel_pct": null, "max_tage": null}}\n\`\`\``,
  journal: `\nJournal: Du bewertest einen abgeschlossenen Papier-Trade von Niko (simuliert, kein echtes Geld) in drei bis fünf Sätzen. Hol get_candles des Werts rund um Einstieg und Ausstieg (Zeitrahmen passend zur Haltedauer: unter zwei Stunden 1m oder 5m, unter zwei Tagen 15m, sonst 1h) und detect_patterns; bei deutschen Werten get_orderbook_analysis (teil a) vom Tag des Einstiegs für Spread und Kosten, falls vorhanden. Beurteile: Passte der Einstieg zur Lage (Trend, Formation, Linie)? War der Ausstieg nachvollziehbar (Ziel, Stopp, Zeit)? Was war Glück, was Handwerk? Zum Schluss ein konkreter Lernpunkt. Nikos Notizen (notiz_kauf, notiz_verkauf) einbeziehen; mit search_paper_journal (symbol des Werts, dann wer du) prüfen, ob sich ein Muster aus früheren Trades oder Nikos eigenen Einträgen wiederholt, und es mit Datum nennen. Ehrlich, nicht schönreden, keine Anlageberatung. Antworte nur mit dem Text, Markdown nur **fett**.`,
  tagebuch: `\nTagebuch: Niko stellt dir im Journal seines Papierhandels (simuliert, kein echtes Geld) eine Frage. Hol die Daten mit get_paper_portfolio und search_paper_journal (frühere Einträge, auch seine eigenen), bei Bedarf get_candles. Belege Aussagen mit Datum und Zahl, sag ehrlich, wenn die Daten für ein Urteil nicht reichen. Keine Anlageberatung, keine Empfehlungen zu einzelnen Werten. Markdown: **fett**, Listen mit '- ', Zwischenüberschriften mit '## '. Antworte nur mit dem Text.`,
  routine: `\nRoutine: Du schreibst eine Nachricht für Telegram und das Dashboard, Zahlen mit Uhrzeit. Format: keine Zeile mit '# ' (den Titel setzt das System), sonst wie in Telegram: '**Kurz:** …', Abschnitte mit '## ' und Symbol (z. B. '## 🇺🇸 USA', '## 🇩🇪 Deutschland', '## ₿ Krypto', '## 🔔 Alarme', '## 🔍 Worauf ich achte'), knappe Punkte '- ', Herleitungen in '::: Details' … ':::'. Antworte am Ende nur mit dem fertigen Text.`,
};
const SYSTEM = (raum) => BASIS + (RAUM[raum] || '');

const ROUTINEN = {
  morgenlage: `Schreibe die Morgenlage vom ${heute()} als Briefing zu den fünf auffälligsten Charts. Pflicht: häng mit chart_image (art chart, wert = Platz 1 aus get_chart_picks, titel = Name und Formation) das Bild des ersten Highlights an. Ziel: Niko liest in zwei Minuten, was die fünf Charts gerade zeigen und welche Marken entscheiden. Daten: get_chart_picks (die Top 5 in genau dieser Reihenfolge, mit Formationen, Linien und Alarmvorschlägen), get_quote je Wert, get_dossier der Werte, falls vorhanden. Keine Kurslisten; Zahlen nur, wo sie eine Marke oder einen Abstand belegen.
Aufbau, genau in dieser Reihenfolge:
1) '**Kurz:**' ein Satz: was heute über die fünf Charts hinweg zählt.
2) '## 🔍 Die fünf Highlights': genau die Top 5 aus get_chart_picks in derselben Reihenfolge. Je Wert eine Zeile '**Name – Formation**', darunter die These in einem Satz und die Herleitung in drei bis fünf Sätzen: was die Formation zeigt (Güte, Volumen, Indikatoren), wo der Kurs steht und welche zwei Marken entscheiden, über der einen gilt die Formation als bestätigt, unter der anderen als gebrochen. Keine Kauf- oder Verkaufsempfehlung.
3) Vor der Antwort save_briefing aufrufen: Kernsatz (der Satz aus **Kurz:**) und dieselben fünf Highlights mit muster, these, herleitung und den zwei entscheidenden Marken. Meldet das Werkzeug Fehler, korrigieren und erneut aufrufen, bis ok; Hinweise prüfen. Ohne erfolgreiches save_briefing bleibt das Briefing im Dashboard leer.
Stil des Briefings (so hat Niko es abgenommen): ruhig und sachlich wie ein Analystenbericht; These als klare Aussage ohne „vielleicht“; Herleitung nennt Güte und Volumen, Indikatoren, den Kursstand und beide Marken mit genau den Zahlen aus oben und unten; Zahlen deutsch (4.126,64, 1.099); kein Markdown in der Herleitung; keine Kurslisten, keine Einschätzungen-Bilanz, kein Handlungsbedarf. Die Antwort beginnt direkt mit '**Kurz:**', ohne Vorrede und ohne Hinweis auf save_briefing.
Datenhinweise (Verzögerung, unvollständiges Volumen) nur einmal kurz am Ende als '**Hinweis:** …'.`,
  tagesrueckblick: `Schreibe den Tagesrückblick zum Papierhandel für ${heute()}: 1) get_paper_portfolio: Orders und Trades von heute mit Ergebnis, offene Positionen mit Stand. 2) search_paper_journal (wer du): Nikos eigene Einträge von heute und den letzten Tagen einbeziehen. 3) Was lief gut, was nicht, ein konkreter Punkt für morgen; keine Empfehlungen zu einzelnen Werten. Gab es heute keine Orders, sag das in zwei Sätzen und nenne die offenen Positionen.`,
  wochenrueckblick: `Schreibe den Wochenrückblick zum Papierhandel bis ${heute()}: Nikos eigene Einträge der Woche (search_paper_journal, wer du) einbeziehen; wiederholt sich ein Muster aus früheren Wochen, nenne es mit Datum. 1) get_paper_portfolio: Depotwert und Ergebnis je Währung, Trades dieser Woche mit Ergebnis, Trefferquote, offene Positionen und Orders. 2) Muster in Nikos Handeln: Haltedauer, Einstiege (zu früh, zu spät, gegen den Trend), Stopps, Positionsgröße, Gebühren im Verhältnis zum Ergebnis; die Bewertungen im Journal einbeziehen. 3) Zwei bis drei konkrete Punkte fürs Handwerk in der nächsten Woche, keine Empfehlungen zu einzelnen Werten. Gab es diese Woche keine Trades, sag das in zwei Sätzen und nenne die offenen Positionen.`,
  nachbesprechung: `Schreibe die Nachbesprechung des Handelstages ${heute()}: je deutschem Wert zwei Sätze aus get_orderbook_analysis (teil alle, heute): Spread und Tiefe, Druck (OBI/OFI), Wände und Spoofing-Muster, Auktionen; dann die heute ausgelösten Alarme (list_alerts) und die Bilanz der heute fälligen Einschätzungen (get_scorecard). Danach für jeden deutschen Wert das Dossier fortschreiben: get_dossier lesen, mit save_dossier neu schreiben (höchstens 25 Zeilen, nur Beobachtungen, die über Tage tragen, mit Datum; Veraltetes kürzen). Antworte am Ende nur mit dem Text der Nachbesprechung.`,
};

// ---------------- Verbindungen (Browser-Räume) ----------------
class Verbindung {
  constructor(ws, raum) {
    this.ws = ws; this.raum = raum; this.sessionId = null; this.pending = new Map(); this.controller = null; this.chartOffen = false;
  }
  send(o) { if (this.ws.readyState === 1) this.ws.send(JSON.stringify(o)); }
  callBrowser(name, input) {
    return new Promise((resolve) => {
      const id = crypto.randomUUID();
      const t = setTimeout(() => { this.pending.delete(id); resolve({ ok: false, error: 'Browser antwortet nicht' }); }, 90000);
      this.pending.set(id, (r) => { clearTimeout(t); resolve(r); });
      this.send({ type: 'tool_call', id, name, input, label: BEZEICHNUNG[name] });
    });
  }
}

// Ausführungs-Kontext eines Laufs: Server-Werkzeuge bekommen Zugriff auf Browser-Werkzeuge, falls ein Browser da ist
function kontextFuer(raum, conn, modell, quelle) {
  const ctx = { raum, modell, quelle };
  if (['telegram', 'wache', 'routine'].includes(raum)) ctx.bilder = [];          // chart_image hängt Bilder an die Antwort
  const rufen = async (name, input) => { const r = await conn.callBrowser(name, input); if (!r.ok) throw new Error(r.error); return r.result; };
  if (conn && raum === 'dashboard') { ctx.dashboard = rufen; ctx.chart = rufen; }
  if (conn && raum === 'chart') ctx.chart = rufen;
  return ctx;
}

// Werkzeuge eines Laufs als einheitliche Liste {name, beschreibung, schema, nurLesen, handler}
function werkzeugeFuer(raum, conn, ctx, onEvent) {
  const aus = [];
  for (const w of WERKZEUGE) {
    if (w.wo === 'dashboard' && raum !== 'dashboard') continue;
    if (w.wo === 'chart' && raum !== 'dashboard' && raum !== 'chart') continue;
    if (w.nurNachricht && !['telegram', 'wache', 'routine'].includes(raum)) continue;
    const handler = async (input) => {
      if (w.wo === 'server') {
        onEvent?.({ type: 'step', name: w.name, label: BEZEICHNUNG[w.name], input, state: 'run' });
        try { const r = await w.run(input || {}, ctx); onEvent?.({ type: 'step', name: w.name, label: BEZEICHNUNG[w.name], state: 'done' }); return { text: JSON.stringify(r) }; }
        catch (e) { onEvent?.({ type: 'step', name: w.name, label: BEZEICHNUNG[w.name], state: 'err', text: e.message }); return { text: 'Fehler: ' + e.message, fehler: true }; }
      }
      const r = await conn.callBrowser(w.name, input || {});
      if (!r.ok) return { text: 'Fehler: ' + r.error, fehler: true };
      if (w.name === 'screenshot' && r.result?.image_png_base64) return { bild: r.result.image_png_base64, text: `Chart ${r.result.width}×${r.result.height} px` };
      return { text: JSON.stringify(r.result) };
    };
    aus.push({ ...w, handler });
  }
  return aus;
}

// ---------------- Ein Zug über den gewählten Treiber ----------------
const sitzungen = new Map();   // Schlüssel (z. B. "telegram") -> { sdk: sessionId, openrouter: Nachrichten }

async function lauf({ text, raum, conn, sitzung, onEvent, signal, quelle }) {
  const e = await einstellungen();
  const { anbieter, modell } = modellFuer(raum, e);
  const ctx = kontextFuer(raum, conn, modell, quelle);
  const werkzeuge = werkzeugeFuer(raum, conn, ctx, onEvent);
  const t0 = Date.now();
  const st = sitzung ? (sitzungen.get(sitzung) || sitzungen.set(sitzung, {}).get(sitzung)) : {};
  if (anbieter === 'openrouter') {
    const key = openrouterSchluessel();
    if (!key) throw new Error('OpenRouter ist als Anbieter gewählt, aber es gibt keinen Schlüssel. Trag OPENROUTER_KEY=... in ~/.config/marktdaten/ki.env ein oder stell in den Einstellungen auf Claude Code um.');
    st.openrouter = st.openrouter || [];
    const r = await openrouterLauf({ key, modell, system: SYSTEM(raum), nachrichten: st.openrouter, text, werkzeuge, onEvent, signal });
    return { ...r, modell: 'openrouter/' + modell, dauer: Date.now() - t0, bilder: ctx.bilder || [] };
  }
  // Claude Agent SDK mit dem Claude-Login dieses Rechners
  const tools = werkzeuge.map((w) => tool(w.name, w.beschreibung, w.schema, async (input) => {
    const r = await w.handler(input);
    if (r.fehler) return { content: [{ type: 'text', text: r.text }], isError: true };
    if (r.bild) return { content: [{ type: 'image', data: r.bild, mimeType: 'image/png' }, { type: 'text', text: r.text }] };
    return { content: [{ type: 'text', text: r.text }] };
  }, { annotations: { readOnlyHint: !!w.nurLesen } }));
  const controller = new AbortController();
  signal?.addEventListener('abort', () => controller.abort());
  const extra = raum === 'chart' ? ['Read', 'Grep', 'Glob'] : raum === 'dashboard' ? ['WebSearch', 'WebFetch'] : [];
  const q = query({
    prompt: text,
    options: {
      cwd: raum === 'chart' ? ROOT : here, model: modell, systemPrompt: SYSTEM(raum), includePartialMessages: true,
      resume: st.sdk || undefined, mcpServers: { delta: createSdkMcpServer({ name: 'delta', version: '2.0.0', tools }) },
      allowedTools: ['mcp__delta__*', ...extra], disallowedTools: ['Bash', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'],
      permissionMode: 'default', abortController: controller, env: { ...process.env, MCP_TOOL_TIMEOUT: '150000' },
    },
  });
  let antwort = '', kosten, zuege;
  for await (const msg of q) {
    if (msg.type === 'system' && msg.subtype === 'init') st.sdk = msg.session_id;
    else if (msg.type === 'stream_event') {
      const ev = msg.event;
      if (ev.type === 'content_block_delta' && ev.delta?.type === 'text_delta') { antwort += ev.delta.text; onEvent?.({ type: 'text_delta', text: ev.delta.text }); }
      if (ev.type === 'content_block_stop') onEvent?.({ type: 'assistant_done' });
    } else if (msg.type === 'assistant') {
      for (const b of msg.message.content) if (b.type === 'tool_use' && !b.name.startsWith('mcp__delta__')) onEvent?.({ type: 'tool_use', name: b.name, input: b.input });
    } else if (msg.type === 'result') {
      st.sdk = msg.session_id || st.sdk; kosten = msg.total_cost_usd; zuege = msg.num_turns;
      if (msg.subtype !== 'success') throw new Error(`${msg.subtype}${msg.errors ? ': ' + msg.errors.join('; ') : ''}`);
      if (!antwort && msg.result) antwort = msg.result;
    }
  }
  return { text: antwort.trim(), modell, kosten, zuege, dauer: Date.now() - t0, bilder: ctx.bilder || [], briefing: ctx.briefing || null };
}

// Kopfloser Lauf (ohne Browser): Telegram, Zweitmeinung, Routine
const laufende = new Map();
async function kopflos(raum, text, sitzung, quelle, zeit = 280000) {
  if (MOCK) return { text: `Probelauf (${raum}): ${text.slice(0, 80)}`, modell: 'mock', kosten: 0 };
  const schluessel = raum + ':' + (sitzung || crypto.randomUUID());
  if (laufende.has(schluessel)) throw new Error('Es läuft noch eine Antwort in diesem Kanal.');
  laufende.set(schluessel, true);
  try { return await lauf({ text, raum, conn: null, sitzung, quelle, onEvent: null, signal: AbortSignal.timeout(zeit) }); }
  finally { laufende.delete(schluessel); }
}

// ---------------- Prüfstand: Übersetzen und Prüfen im Hintergrund ----------------
function jsonAus(text) {
  const m = /```json\s*(\{[\s\S]*?\})\s*```/.exec(text) || /(\{[\s\S]*\})/.exec(text);
  if (!m) throw new Error('Claude hat kein JSON geliefert');
  return JSON.parse(m[1]);
}
async function uebersetzen(id) {
  let st = Pruefstand.lesen(id);
  try {
    const werte = st.werte?.length ? st.werte.join(', ') : 'alle Werte mit langer Historie';
    const r = await kopflos('pruefstand', `Übersetze diese Strategie von Niko (Werte: ${werte}, Tageskerzen):\n\n${st.text}`, null, 'pruefstand', 300000);
    const j = jsonAus(r.text);
    if (!j.script || !/buy\s*\(/.test(j.script)) throw new Error('Kein gültiges Script mit buy()');
    await Pruefstand.lernteil({ code: j.script, params: j.params || [], ausstieg: j.ausstieg || {}, werte: st.werte });   // läuft es?
    st = Pruefstand.lesen(id);
    Pruefstand.speichern({ ...st, status: 'entwurf', titel: String(j.titel || st.titel).slice(0, 80), beschreibung: j.beschreibung || '', annahmen: j.annahmen || [], grenzen: j.grenzen || [],
      script: j.script, params: (j.params || []).map((p) => ({ name: p.name, from: +p.from, to: +p.to, step: +(p.step || 1), warum: p.warum || '' })),
      ausstieg: Object.fromEntries(Object.entries(j.ausstieg || {}).filter(([, v]) => v != null && v !== '' && +v > 0).map(([k, v]) => [k, +v])), modell: r.modell, kosten_uebersetzen: r.kosten });
  } catch (e) {
    st = Pruefstand.lesen(id); Pruefstand.speichern({ ...st, status: 'fehler', fehler: e.message || String(e) });
  }
}
async function pruefenLauf(id) {
  let st = Pruefstand.lesen(id);
  Pruefstand.speichern({ ...st, status: 'rechnet', fehler: null });
  try {
    const e = await Pruefstand.pruefen(st);
    st = Pruefstand.speichern({ ...Pruefstand.lesen(id), status: 'kommentiert', gesperrt: true, ergebnis: e, geprueft: new Date().toISOString() });
    const pct = (v) => (v * 100).toFixed(1).replace('.', ',') + ' %';
    const zahlen = { urteil: e.urteil.text, parameter_im_lernteil_gewaehlt: e.gewaehlt, kombinationen_getestet: e.kombinationen, versuch_in_dieser_familie: e.versuche_familie,
      median_vorsprung: { lernteil: pct(e.gesamt.lern_vorsprung), pruefteil: pct(e.gesamt.pruef_vorsprung) }, pruefteil_schlaegt_kaufen_halten: `${e.gesamt.pruef_schlaegt} von ${e.gesamt.werte} Werten`,
      trades: { lernteil: e.gesamt.trades_lern, pruefteil: e.gesamt.trades_pruef },
      je_wert: e.je_wert.map((w) => `${w.symbol}: Lernteil ${pct(w.lern.ret)} (Kaufen und Halten ${pct(w.lern.bh)}), Prüfteil ${pct(w.pruef.ret)} (${pct(w.pruef.bh)}), max. Rückgang ${pct(w.pruef.maxDD)}, ${w.pruef.trades} Trades`) };
    try {
      const r = await kopflos('routine', `Ordne das Ergebnis dieses Strategietests für Niko in drei bis fünf Sätzen ein, ohne Überschrift und ohne Liste. Strategie: „${st.titel}“ (${st.beschreibung || st.text}).\nErgebnis: ${JSON.stringify(zahlen)}\nSag, ob sie im Prüfteil hält, wie sie gegen Kaufen und Halten abschneidet (diese Zeit war ein starker Bullenmarkt), ob die Zahl der Trades für ein Urteil reicht, und was das Ergebnis nicht beweist. Erwähne die Zahl der Versuche, wenn es mehr als einer ist (wer oft genug probiert, findet zufällig etwas). Keine Empfehlung zum Handeln.`, null, 'pruefstand-kommentar', 200000);
      Pruefstand.speichern({ ...Pruefstand.lesen(id), status: 'fertig', claude_text: r.text, kosten_kommentar: r.kosten, modell_kommentar: r.modell });
    } catch (x) { Pruefstand.speichern({ ...Pruefstand.lesen(id), status: 'fertig', claude_text: null, fehler: 'Kommentar fehlgeschlagen: ' + x.message }); }
  } catch (e) {
    Pruefstand.speichern({ ...Pruefstand.lesen(id), status: 'fehler', fehler: e.message || String(e) });
  }
}

// Nach einem Neustart halbfertige Studien fortsetzen (der Lauf im Hintergrund wäre sonst verloren)
async function kommentarNachholen(id) {
  const st = Pruefstand.lesen(id), e = st.ergebnis;
  try {
    const r = await kopflos('routine', `Ordne das Ergebnis dieses Strategietests für Niko in drei bis fünf Sätzen ein, ohne Überschrift und ohne Liste. Strategie: „${st.titel}“ (${st.beschreibung || st.text}). Urteil: ${e.urteil.text}. Median-Vorsprung gegen Kaufen und Halten: Lernteil ${(e.gesamt.lern_vorsprung * 100).toFixed(1)} %, Prüfteil ${(e.gesamt.pruef_vorsprung * 100).toFixed(1)} %; im Prüfteil besser als Kaufen und Halten bei ${e.gesamt.pruef_schlaegt} von ${e.gesamt.werte} Werten; Trades Lernteil ${e.gesamt.trades_lern}, Prüfteil ${e.gesamt.trades_pruef}; Versuch ${e.versuche_familie}. Sag, ob sie hält, was die Zahl der Trades erlaubt und was das nicht beweist. Keine Empfehlung.`, null, 'pruefstand-kommentar', 200000);
    Pruefstand.speichern({ ...Pruefstand.lesen(id), status: 'fertig', claude_text: r.text, kosten_kommentar: r.kosten, modell_kommentar: r.modell });
  } catch (x) { Pruefstand.speichern({ ...Pruefstand.lesen(id), status: 'fertig', fehler: 'Kommentar fehlgeschlagen: ' + x.message }); }
}
setTimeout(() => {
  for (const st of Pruefstand.liste()) {
    if (st.status === 'uebersetzt') { console.log('Prüfstand: Übersetzen fortsetzen', st.id); uebersetzen(st.id).catch(() => {}); }
    else if (st.status === 'rechnet') { console.log('Prüfstand: Prüfen fortsetzen', st.id); pruefenLauf(st.id).catch(() => {}); }
    else if (st.status === 'kommentiert' && st.ergebnis) { console.log('Prüfstand: Einordnung fortsetzen', st.id); kommentarNachholen(st.id).catch(() => {}); }
  }
}, 5000);

// ---------------- Routinen: Morgenlage und Nachbesprechung ----------------
function routinenStand() { try { return JSON.parse(fs.readFileSync(ROUTINE_DATEI, 'utf8')); } catch (e) { return {}; } }
async function routine(art, senden = true) {
  const text = ROUTINEN[art];
  if (!text) throw new Error('unbekannte Routine');
  const r = await kopflos('routine', text, null, art);
  // Briefing der Morgenlage: kommt aus save_briefing; ein alter Datenblock ':::fokus' am Textende gilt nur als Rückfall und wird immer herausgeschnitten
  let fokus = r.briefing || null;
  const block = /\n?:::fokus\s*\n([\s\S]*?)\n:::\s*$/.exec(r.text || '');
  if (art === 'morgenlage' && !fokus && !block) console.log('Morgenlage ohne Briefing: save_briefing nicht aufgerufen');
  if (art === 'morgenlage' && r.text) { const k = r.text.indexOf('**Kurz:**'); if (k > 0) r.text = r.text.slice(k); }   // Vorrede („Das Briefing ist gespeichert …“) weg
  if (art === 'morgenlage' && !(r.bilder || []).length) {            // Chartbild ist Pflicht: fehlt es, selbst anhängen (erstes Highlight)
    try {
      const erster = fokus?.punkte?.[0]?.wert || (await analysen('/api/charts')).werte[0]?.wert.symbol;
      if (erster) {
        const { bild } = await import('./bild.mjs');
        r.bilder = [{ pfad: await bild('chart', { wert: erster }), titel: [fokus?.punkte?.[0]?.wert, fokus?.punkte?.[0]?.muster].filter(Boolean).join(' – ') }];
        console.log('Morgenlage: Chartbild selbst angehängt', erster);
      }
    } catch (e) { console.log('Chartbild fehlgeschlagen:', e.message); }
  }
  if (block) {
    if (!fokus) try { fokus = JSON.parse(block[1]); } catch (e) { console.log('Fokus-Block unlesbar:', e.message); }
    r.text = r.text.slice(0, block.index).trimEnd();
  }
  const stand = routinenStand(); stand[art] = heute(); fs.writeFileSync(ROUTINE_DATEI, JSON.stringify(stand));
  const journalArt = { wochenrueckblick: 'woche', tagesrueckblick: 'tag' }[art];
  if (journalArt) {                                         // gehört ins Journal des Papierhandels, nicht zu den Notizen
    try { await dashboard('/api/papier/journal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ art: journalArt, text: r.text, modell: r.modell, kosten: r.kosten }) }); } catch (e) { console.log('Rückblick nicht gespeichert:', e.message); }
    if (senden) { try { await analysen('/api/telegram/senden', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: `📓 <b>${journalArt === 'woche' ? 'Wochenrückblick' : 'Tagesrückblick'} Papierhandel</b>\n` + telegramHtml(r.text) }) }); } catch (e) { /* Telegram nicht eingerichtet */ } }
    return r;
  }
  try { await analysen('/api/notizen', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ art, text: r.text, modell: r.modell, fokus }) }); } catch (e) { console.log('Notiz nicht gespeichert:', e.message); }
  // danach die Chart-Einordnung für Analysen auffrischen (läuft im Analysen-Dienst im Hintergrund)
  analysen('/api/charts/claude', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
  if (senden) {
    const datum = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', weekday: 'long', day: 'numeric', month: 'long' }).format(new Date());
    const titel = (art === 'morgenlage' ? '🌅 <b>Morgenlage</b>' : '🌇 <b>Nachbesprechung</b>') + ` · ${datum}`;
    try { await analysen('/api/telegram/senden', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: r.text, markdown: true, kopf: titel, modell: r.modell, kosten: r.kosten, bilder: r.bilder || [] }) }); } catch (e) { /* Telegram nicht eingerichtet */ }
  }
  return r;
}
function telegramHtml(t) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  return esc(String(t)).replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/^#{1,6}\s*(.+)$/gm, '<b>$1</b>').replace(/^\s*[-*]\s+/gm, '• ');
}
const BERLIN = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
setInterval(async () => {
  try {
    const e = await einstellungen();
    if (e.routinen === false || MOCK) return;
    const teile = Object.fromEntries(BERLIN.formatToParts(new Date()).map((p) => [p.type, p.value]));
    if (['Sa', 'So'].includes(teile.weekday.replace('.', ''))) return;
    const jetzt = `${teile.hour}:${teile.minute}`, stand = routinenStand();
    for (const art of ['morgenlage', 'nachbesprechung']) {
      if (jetzt === (e[art] || '') && stand[art] !== heute() && !laufende.has('routine:' + art)) {
        console.log('Routine', art, jetzt);
        laufende.set('routine:' + art, true);
        routine(art, true).catch((x) => console.log('Routine fehlgeschlagen:', x.message)).finally(() => laufende.delete('routine:' + art));
      }
    }
    // Wochenrückblick Papierhandel: freitags 22:30 Uhr (nach US-Schluss), nur wenn es Orders gibt
    if (teile.weekday.replace('.', '') === 'Fr' && jetzt === '22:30' && stand.wochenrueckblick !== heute() && !laufende.has('routine:wochenrueckblick')) {
      const d = await dashboard('/api/papier').catch(() => null);
      if (d && (d.verlauf.length || d.offen.length)) {
        laufende.set('routine:wochenrueckblick', true);
        routine('wochenrueckblick', true).catch((x) => console.log('Wochenrückblick fehlgeschlagen:', x.message)).finally(() => laufende.delete('routine:wochenrueckblick'));
      }
    }
  } catch (e) { /* nächste Minute */ }
}, 60000);

// ---------------- HTTP ----------------
const PANEL = path.join(here, 'web', 'panel.js');
const lesen = (req) => new Promise((resolve) => { let b = ''; req.on('data', (c) => { b += c; if (b.length > 1e6) req.destroy(); }); req.on('end', () => { try { resolve(JSON.parse(b || '{}')); } catch (e) { resolve({}); } }); });
const json = (res, code, d) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(d)); };
const lokal = (req) => ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress);

// Bitcoin-Dominanz für iC Charts (CoinMarketCap ohne CORS-Header), 10 Minuten zwischengespeichert
const DOM_URL = 'https://api.coinmarketcap.com/data-api/v3/global-metrics/quotes/historical';
let domCache = { at: 0, body: null };
async function dominanz() {
  if (Date.now() - domCache.at < 600e3 && domCache.body) return domCache.body;
  const jetzt = Math.floor(Date.now() / 1000), out = {};
  for (const [name, iv, tage, schritt] of [['tage', '1d', 3 * 365, 86400], ['stunden', '1h', 30, 3600]]) {
    const r = await fetch(`${DOM_URL}?timeStart=${jetzt - tage * 86400}&timeEnd=${jetzt}&interval=${iv}&convertId=2781`, { headers: { 'User-Agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(15e3) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const m = new Map();
    for (const q of (await r.json()).data.quotes) m.set(Math.floor(Date.parse(q.timestamp) / 1000 / schritt) * schritt, Math.round(q.btcDominance * 100) / 100);
    out[name] = [...m.entries()].sort((a, b) => a[0] - b[0]);
  }
  out.stand = new Date().toISOString();
  domCache = { at: Date.now(), body: JSON.stringify(out) };
  return domCache.body;
}

// Modelllisten für die Einstellungen: Claude Code fest, OpenRouter live (öffentliche Liste, ohne Schlüssel), 1 Stunde zwischengespeichert
const CLAUDE_MODELLE = [
  { id: 'claude-opus-5-5', name: 'Claude Opus 5.5', hinweis: 'stark, für den Chat' },
  { id: 'claude-sonnet-5-5', name: 'Claude Sonnet 5.5', hinweis: 'schnell und günstiger, für Routinen' },
  { id: 'claude-haiku-5-5', name: 'Claude Haiku 5.5', hinweis: 'am günstigsten' },
  { id: 'claude-fable-5-1', name: 'Claude Fable 5.1', hinweis: 'stärkstes Modell' },
];
let orCache = { at: 0, liste: null };
async function modelle() {
  let openrouter = orCache.liste, fehler;
  if (!openrouter || Date.now() - orCache.at > 3600e3) {
    try {
      const r = await fetch('https://openrouter.ai/api/v1/models', { signal: AbortSignal.timeout(15000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      openrouter = (await r.json()).data
        .filter((m) => (m.supported_parameters || []).includes('tools') && !m.id.endsWith(':batch'))   // Batch-Varianten streamen nicht
        .map((m) => ({ id: m.id, name: m.name, anbieter: m.id.split('/')[0], ein: +m.pricing?.prompt * 1e6, aus: +m.pricing?.completion * 1e6 }))
        .sort((a, b) => a.anbieter.localeCompare(b.anbieter) || a.name.localeCompare(b.name));
      orCache = { at: Date.now(), liste: openrouter };
    } catch (e) { fehler = 'OpenRouter-Liste nicht abrufbar: ' + e.message; openrouter = openrouter || []; }
  }
  return { claude: CLAUDE_MODELLE, openrouter, fehler };
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x');
  const origin = req.headers.origin || '';
  if (vomDashboard(origin)) { res.setHeader('Access-Control-Allow-Origin', origin); res.setHeader('Access-Control-Allow-Headers', 'Content-Type'); res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'); }
  if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
  try {
    if (u.pathname === '/panel.js') { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8', 'Cache-Control': 'no-cache' }); return res.end(fs.readFileSync(PANEL)); }
    if (u.pathname === '/api/dominanz') { const body = await dominanz(); res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' }); return res.end(body); }
    if (u.pathname === '/modelle') return json(res, 200, await modelle());
    if (u.pathname === '/status') {
      const e = await einstellungen();
      return json(res, 200, { ok: true, anbieter: e.anbieter, modell: modellFuer('dashboard', e).modell, modell_routine: modellFuer('routine', e).modell, openrouter_schluessel: !!openrouterSchluessel(), mock: MOCK, routinen: routinenStand(), laeuft: [...laufende.keys()] });
    }
    if (req.method === 'POST' && u.pathname.startsWith('/pruefstand/')) {            // Prüfstand: läuft im Hintergrund, Status steht in der Studie
      if (!lokal(req)) return json(res, 403, { fehler: 'nur lokal' });
      const d = await lesen(req);
      if (u.pathname === '/pruefstand/uebersetzen') {
        const text = String(d.text || '').trim().slice(0, 3000);
        if (text.length < 10) return json(res, 400, { fehler: 'Bitte die Strategie in ein, zwei Sätzen beschreiben' });
        const st = Pruefstand.neu({ text, titel: text.slice(0, 60), werte: d.werte || null, status: 'uebersetzt' });
        uebersetzen(st.id).catch(() => {});
        return json(res, 200, { id: st.id });
      }
      if (u.pathname === '/pruefstand/pruefen') {
        let st = Pruefstand.lesen(String(d.id || '')); if (!st) return json(res, 404, { fehler: 'unbekannte Studie' });
        const aenderung = {}; for (const k of ['script', 'params', 'ausstieg', 'werte']) if (d[k] !== undefined) aenderung[k] = d[k];
        if (st.gesperrt) st = Pruefstand.ableiten(st, aenderung);                   // geprüfte Studie: neuer Versuch
        else st = Pruefstand.speichern({ ...st, ...aenderung });
        if (st.status === 'rechnet') return json(res, 409, { fehler: 'läuft schon' });
        pruefenLauf(st.id).catch(() => {});
        return json(res, 200, { id: st.id, versuch: st.versuch });
      }
      if (u.pathname === '/pruefstand/ableiten') {
        const st = Pruefstand.lesen(String(d.id || '')); if (!st) return json(res, 404, { fehler: 'unbekannte Studie' });
        return json(res, 200, { id: Pruefstand.ableiten(st, {}).id });
      }
      return json(res, 404, { fehler: 'unbekannt' });
    }
    if (req.method === 'POST' && ['/frage', '/zweitmeinung', '/routine', '/chartanalyse', '/papier'].includes(u.pathname)) {
      if (!lokal(req) && !(u.pathname === '/routine' && vomDashboard(origin))) return json(res, 403, { fehler: 'nur lokal' });
      const d = await lesen(req);
      if (u.pathname === '/frage') {
        if (d.neu) { sitzungen.delete('telegram'); if (!d.text) return json(res, 200, { ok: true }); }
        const r = await kopflos('telegram', String(d.text || '').slice(0, 4000), 'telegram', 'telegram');
        return json(res, 200, { text: r.text, modell: r.modell, kosten: r.kosten, bilder: r.bilder });
      }
      if (u.pathname === '/zweitmeinung') {
        const text = `Alarm ausgelöst für ${d.name} (${d.symbol}, ${d.markt}): ${d.text}\nBedingung: ${d.bedingung || d.art}${d.notiz ? `\nNotiz von Niko: ${d.notiz}` : ''}\nZeitrahmen: ${d.tf}. Heute ist der ${heute()}.`;
        const r = await kopflos('wache', text, null, 'zweitmeinung');
        return json(res, 200, { text: r.text, modell: r.modell, kosten: r.kosten, bilder: r.bilder });
      }
      if (u.pathname === '/papier') {                          // Bewertung eines abgeschlossenen Papier-Trades fürs Journal (vom Kern)
        const r = await kopflos('journal', String(d.text || '').slice(0, 20000), null, 'papier');
        return json(res, 200, { text: r.text, modell: r.modell, kosten: r.kosten });
      }
      if (u.pathname === '/chartanalyse') {                    // Einordnung der Top-Charts für die Startseite von Analysen
        const r = await kopflos('routine', String(d.text || '').slice(0, 60000), null, 'chartanalyse', 400000);
        return json(res, 200, { text: r.text, modell: r.modell, kosten: r.kosten });
      }
      if (d.art === 'journalfrage') {                       // freie Frage aus dem Journal des Depots, Antwort wird ein Eintrag
        const frage = String(d.frage || '').trim().slice(0, 2000);
        if (!frage) return json(res, 400, { fehler: 'Bitte eine Frage eingeben' });
        const r = await kopflos('tagebuch', `Frage von Niko (${heute()})${d.symbol ? `, bezogen auf ${d.symbol}` : ''}: ${frage}`, null, 'journalfrage');
        await dashboard('/api/papier/journal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ art: 'frage', titel: frage, symbol: d.symbol || null, text: r.text, modell: r.modell, kosten: r.kosten }) });
        return json(res, 200, { text: r.text, modell: r.modell, kosten: r.kosten });
      }
      const r = await routine(String(d.art || 'morgenlage'), d.senden !== false);
      return json(res, 200, { text: r.text, modell: r.modell, kosten: r.kosten, bilder: d.senden === false ? r.bilder : [] });
    }
    res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end('iC Delta · Claude: WebSocket unter /agent, Panel unter /panel.js\n');
  } catch (e) { json(res, 500, { fehler: e.message || String(e) }); }
});

// ---------------- WebSocket: eine Unterhaltung je Verbindung ----------------
const wss = new WebSocketServer({ noServer: true });
wss.on('connection', async (ws, req) => {
  const raum = new URL(req.url, 'http://x').searchParams.get('raum') === 'dashboard' ? 'dashboard' : 'chart';
  const conn = new Verbindung(ws, raum);
  const send = (o) => conn.send(o);
  const e = await einstellungen();
  send({ type: 'status', ready: true, text: MOCK ? 'Probelauf ohne Claude' : 'bereit', model: modellFuer(raum, e).modell, anbieter: e.anbieter, raum });

  ws.on('message', async (raw) => {
    let m; try { m = JSON.parse(raw); } catch (e) { return; }
    if (m.type === 'tool_result') { const f = conn.pending.get(m.id); if (f) { conn.pending.delete(m.id); f(m); } return; }
    if (m.type === 'abort') { conn.controller?.abort(); return; }
    if (m.type === 'new_session') { conn.sessionId = null; sitzungen.delete('ws:' + conn.sessionKey); conn.sessionKey = null; return; }
    if (m.type !== 'user') return;
    if (conn.controller) { send({ type: 'error', message: 'Es läuft noch eine Antwort.' }); return; }
    if (MOCK) return mockTurn(conn, send, m);
    conn.controller = new AbortController();
    conn.sessionKey = conn.sessionKey || crypto.randomUUID();
    const ctx = m.context ? `\n\n[Kontext beim Absenden: ${JSON.stringify(m.context).slice(0, 3500)}]` : '';
    try {
      const r = await lauf({ text: m.text + ctx, raum, conn, sitzung: 'ws:' + conn.sessionKey, quelle: raum, onEvent: send, signal: conn.controller.signal });
      send({ type: 'result', model: r.modell, cost: r.kosten, duration: r.dauer, turns: r.zuege });
    } catch (e) {
      send({ type: 'error', message: conn.controller?.signal.aborted ? 'Abgebrochen.' : (e.message || String(e)) });
    } finally { conn.controller = null; }
  });
  ws.on('close', () => { conn.controller?.abort(); });
});

// Probelauf ohne Claude: prüft nur den Weg Server → Browser-Werkzeug → Server
async function mockTurn(conn, send, m) {
  conn.controller = new AbortController();
  try {
    const name = conn.raum === 'dashboard' ? 'get_view' : 'get_state';
    const st = await conn.callBrowser(name, {});
    send({ type: 'text_delta', text: `Probelauf (${conn.raum}): ${JSON.stringify(st.result || st.error).slice(0, 300)}` }); send({ type: 'assistant_done' });
    send({ type: 'result', model: 'mock', cost: 0, duration: 0, turns: 1 });
  } finally { conn.controller = null; }
}

// Bindung nur an die genannten Adressen (wie das Dashboard: localhost und Tailscale, nicht das Heimnetz).
// Fremde Webseiten im Browser dürfen Claude nicht fernsteuern: WebSocket nur mit Origin des Dashboards.
for (const [i, h] of HOSTS.entries()) {
  const s = i === 0 ? server : http.createServer(server.listeners('request')[0]);
  s.on('upgrade', (req, socket, head) => {
    if (new URL(req.url, 'http://x').pathname !== '/agent' || !vomDashboard(req.headers.origin)) { socket.write('HTTP/1.1 403 Forbidden\r\n\r\n'); socket.destroy(); return; }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
  });
  s.listen(PORT, h, () => console.log(`iC Delta · Claude: ws://${h}:${PORT}/agent   (Vorgabe ${MODEL}${MOCK ? ', PROBELAUF ohne Claude' : ''}, Einstellungen über http://127.0.0.1:${PORT_BASIS + 1}/api/ki)`));
}
export { lauf, routine, wertFinden, os };
