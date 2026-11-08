// Prüft die Werkzeugliste des Agenten (agent/werkzeuge.mjs, gilt für Agent SDK und OpenRouter):
// ohne macOS-Sandkasten fehlen die Werkstatt-Werkzeuge und ihr Absatz im Systemprompt, mit Sandkasten sind sie da.
// Kommt trotzdem „nur auf macOS“ zurück, antwortet run_analysis mit „Nicht erneut versuchen“ statt „korrigieren“.
// Start: node tests/js/test_werkzeuge.mjs   (braucht agent/node_modules: npm ci --prefix agent). Keine Netzaufrufe.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const WERKZEUGE_URL = pathToFileURL(path.join(path.dirname(fileURLToPath(import.meta.url)), '../../agent/werkzeuge.mjs')).href;

// Jeder Fall läuft in einem eigenen Node-Prozess, weil werkzeuge.mjs Plattform und Sandkasten beim Laden prüft.
// plattform: process.platform vortäuschen (null = echt); sandkasten: Antwort für /usr/bin/sandbox-exec (null = echt)
function fall(plattform, sandkasten, extra = '') {
  const code = `
    const fs = (await import('node:fs')).default;
    ${plattform ? `Object.defineProperty(process, 'platform', { value: ${JSON.stringify(plattform)} });` : ''}
    ${sandkasten !== null ? `const echt = fs.existsSync; fs.existsSync = (p) => p === '/usr/bin/sandbox-exec' ? ${sandkasten} : echt(p);` : ''}
    const m = await import(${JSON.stringify(WERKZEUGE_URL)});
    const aus = { werkstatt: m.WERKSTATT, namen: m.WERKZEUGE.map((w) => w.name), anleitung: m.WERKSTATT_ANLEITUNG, liste: [...m.WERKSTATT_WERKZEUGE] };
    ${extra}
    process.stdout.write(JSON.stringify(aus));`;
  try {
    return JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
  } catch (e) {
    const zeile = String(e.stderr || e.message).split('\n').find((z) => /^\w*Error/.test(z)) || 'Absturz';
    return { absturz: zeile, werkstatt: null, namen: [], liste: [], anleitung: null };
  }
}

// Rückfall: Analysen-Dienst als Attrappe, die Werkstatt meldet „nur auf macOS“, danach ein echter Codefehler
const RUECKFALL = `
    let fehler = 'Werkstatt nur auf macOS verfügbar: Der Code läuft dort im Sandkasten (sandbox-exec), den es auf diesem System nicht gibt.';
    globalThis.fetch = async (url) => {
      const u = String(url);
      const d = u.endsWith('/api/watchlist') ? { werte: [{ id: 'DE0007164600', symbol: 'SAP', name: 'SAP', markt: 'DE' }] }
        : u.endsWith('/api/uebersicht') ? { tage: [{ tag: '2026-11-06', werte: [] }] }
        : u.endsWith('/api/werkstatt/lauf') ? { ok: false, fehler, stdout: '', dauer_s: 0 } : {};
      return new Response(JSON.stringify(d), { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    const run = m.WERKZEUGE.find((w) => w.name === 'run_analysis').run;
    aus.ohne = await run({ titel: 'Probe', code: 'print(1)', wert: 'SAP' }, {});
    fehler = 'Traceback (most recent call last): NameError: name x is not defined';
    try { await run({ titel: 'Probe', code: 'x', wert: 'SAP' }, {}); aus.codefehler = 'kein Fehler'; } catch (e) { aus.codefehler = e.message; }`;

let ok = 0, fail = 0;
function check(name, pass, info = '') {
  if (pass) ok++; else { fail++; console.log(`FEHLER ${name} ${info}`); }
}

const mit = fall('darwin', true);
check('mit Sandkasten: WERKSTATT an', mit.werkstatt === true);
check('Werkstatt-Werkzeuge: sechs Namen', mit.liste.length === 6, mit.liste.join(','));
check('mit Sandkasten: alle Werkstatt-Werkzeuge angeboten', mit.liste.every((n) => mit.namen.includes(n)), mit.liste.filter((n) => !mit.namen.includes(n)).join(','));
check('mit Sandkasten: Systemprompt erklärt die Werkstatt', mit.anleitung.includes('run_analysis'));

for (const [name, f] of [['Windows', fall('win32', null)], ['Linux', fall('linux', false)], ['macOS ohne sandbox-exec', fall('darwin', false)]]) {
  check(`${name}: WERKSTATT aus`, f.werkstatt === false);
  check(`${name}: keine Werkstatt-Werkzeuge`, !f.liste.some((n) => f.namen.includes(n)), f.liste.filter((n) => f.namen.includes(n)).join(','));
  check(`${name}: übrige Werkzeuge vollständig`, f.namen.length === mit.namen.length - mit.liste.length && f.namen.every((n) => mit.namen.includes(n)), `${f.namen.length} von ${mit.namen.length}`);
  check(`${name}: kein Werkstatt-Absatz im Systemprompt`, f.anleitung === '');
}

const r = fall('darwin', true, RUECKFALL);
check('Rückfall: ok false statt Ausnahme', r.ohne?.ok === false, r.absturz || JSON.stringify(r.ohne));
check('Rückfall: „Nicht erneut versuchen“', /Nicht erneut versuchen/.test(r.ohne?.hinweis || ''), JSON.stringify(r.ohne));
check('Codefehler bleibt ein Fehler zum Korrigieren', /NameError/.test(r.codefehler || ''), r.codefehler);

const echt = fall(null, null);
check('echte Plattform wie werkstatt.VERFUEGBAR', echt.werkstatt === (process.platform === 'darwin' && fs.existsSync('/usr/bin/sandbox-exec')));

console.log(`${ok} bestanden, ${fail} fehlgeschlagen (hier ${process.platform}: Werkstatt ${echt.werkstatt ? 'an' : 'aus'}, ${echt.namen.length} Werkzeuge)`);
process.exit(fail ? 1 : 0);
