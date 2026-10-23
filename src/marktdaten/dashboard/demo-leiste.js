// Demo im Terminal (python -m marktdaten demo): Leiste „Demo stoppen“ auf jeder Seite und Nachfrage nach 10 Minuten.
// Tut nichts im echten Betrieb und nicht in der Demo als Hintergrunddienst (dort fragt /api/einrichtung/demo terminal = false).
(() => {
  'use strict';
  let stand = null, offen = null, ende = false;
  const stil = document.createElement('style');
  stil.textContent = `
.dl-leiste { position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 2147483000; display: flex; align-items: center; gap: 10px;
  padding: 6px 6px 6px 14px; border-radius: 99px; font: 500 12px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: rgb(20 16 6 / .92); color: #f6e7c4; border: 1px solid rgb(227 162 26 / .55); box-shadow: 0 8px 26px rgb(0 0 0 / .35); backdrop-filter: blur(10px); }
.dl-leiste i { width: 7px; height: 7px; border-radius: 50%; background: #e3a21a; box-shadow: 0 0 0 3px rgb(227 162 26 / .2); }
.dl-leiste b { color: #ffd27a; letter-spacing: .05em; }
.dl-leiste span { opacity: .8; white-space: nowrap; }
.dl-leiste button, .dl-dialog button { font: 600 12px/1 inherit; font-family: inherit; border-radius: 99px; cursor: pointer; padding: 7px 13px; border: 0; }
.dl-leiste button { background: #e3a21a; color: #1a1405; }
.dl-leiste button:hover { background: #f0b53a; }
.dl-hinter { position: fixed; inset: 0; z-index: 2147483001; background: rgb(0 0 0 / .55); display: grid; place-items: center; padding: 16px; backdrop-filter: blur(3px); }
.dl-dialog { max-width: 440px; width: 100%; background: #12161d; color: #e9ecef; border: 1px solid rgb(255 255 255 / .09); border-radius: 18px; padding: 22px 22px 18px;
  box-shadow: 0 24px 60px rgb(0 0 0 / .5); font: 400 13.5px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
.dl-dialog h2 { margin: 0 0 8px; font-size: 17px; font-weight: 650; text-transform: none; letter-spacing: normal; color: #fff; }
.dl-dialog p { margin: 0 0 10px; color: #c3c2b7; }
.dl-dialog strong { color: #fff; font-weight: 600; }
.dl-dialog .dl-knoepfe { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; flex-wrap: wrap; }
.dl-dialog .ja { background: #e3a21a; color: #1a1405; padding: 9px 16px; font-size: 13px; }
.dl-dialog .nein { background: transparent; color: #c3c2b7; border: 1px solid rgb(255 255 255 / .14); padding: 9px 16px; font-size: 13px; }
.dl-dialog a { color: #5cb0f2; }
@media (max-width: 560px) { .dl-leiste span.dl-mehr { display: none; } }`;
  document.head.append(stil);

  const mb = (b) => (b / 1e6).toLocaleString('de-DE', { maximumFractionDigits: b < 1e7 ? 1 : 0 }) + ' MB';
  const min = (s) => Math.max(0, Math.floor(s / 60));

  async function post(pfad) {
    const r = await fetch(pfad, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IC-Delta': '1' }, body: '{}' });
    return r.json();
  }

  function leiste() {
    let l = document.querySelector('.dl-leiste');
    if (!l) {
      l = document.createElement('div'); l.className = 'dl-leiste'; l.setAttribute('role', 'status');
      l.innerHTML = '<i></i><b>DEMO</b><span class="dl-zeit"></span><span class="dl-mehr"></span><button type="button">Demo stoppen</button>';
      l.querySelector('button').onclick = () => dialog(false);
      document.body.append(l);
    }
    l.querySelector('.dl-zeit').textContent = `läuft seit ${min(stand.laeuft_s)} Min.`;
    l.querySelector('.dl-mehr').textContent = `· ${mb(stand.belegt)} Demo-Daten`;
  }

  function dialog(automatisch) {
    if (offen || ende) return;
    offen = document.createElement('div'); offen.className = 'dl-hinter';
    offen.innerHTML = `<div class="dl-dialog" role="dialog" aria-modal="true" aria-labelledby="dl-titel">
      <h2 id="dl-titel">${automatisch ? `Die Demo läuft seit ${min(stand.laeuft_s)} Minuten` : 'Demo beenden?'}</h2>
      <p>Die Demo erzeugt laufend <strong>künstliche Daten, die Speicher belegen</strong> (zurzeit ${mb(stand.belegt)}).</p>
      <p>Willst du die Demo jetzt beenden? <strong>Die Demo-Daten werden dabei gelöscht.</strong></p>
      ${automatisch ? `<p style="font-size:12.5px">Ohne Antwort endet die Demo in ${Math.round(stand.frist_s / 60)} ${Math.round(stand.frist_s / 60) === 1 ? 'Minute' : 'Minuten'} von selbst.</p>` : ''}
      <div class="dl-knoepfe"><button class="nein" type="button">${automatisch ? 'Nein, weiterlaufen lassen' : 'Abbrechen'}</button><button class="ja" type="button">Ja, beenden und löschen</button></div></div>`;
    offen.querySelector('.ja').onclick = stoppen;
    offen.querySelector('.nein').onclick = async () => { offen.remove(); offen = null; if (automatisch) { stand = await post('/api/einrichtung/demo/weiter'); leiste(); } };
    document.body.append(offen);
    offen.querySelector('.ja').focus();
  }

  async function stoppen() {
    ende = true;
    let d = {};
    try { d = await post('/api/einrichtung/demo/stoppen'); } catch (e) { /* Dienst beendet sich gerade */ }
    document.querySelector('.dl-leiste')?.remove();
    const echt = d.echt ? `<p><a href="${location.protocol}//${location.hostname}:${d.echt}/?ruhe=aus">Zum echten iC Delta</a></p>` : '';
    const h = offen || document.createElement('div'); h.className = 'dl-hinter';
    h.innerHTML = `<div class="dl-dialog" role="alertdialog"><h2>Demo beendet</h2><p>Dashboard, Analysen und Agent der Demo werden gestoppt, <strong>die Demo-Daten gelöscht</strong>. Du kannst dieses Fenster schließen.</p>${echt}</div>`;
    if (!offen) document.body.append(h);
  }

  // Echter Betrieb: Demo bei Bedarf starten (🎬-Knopf, Einrichtung). weiter() läuft, sobald die Demo antwortet.
  window.__demoStarten = async (weiter) => {
    let s; try { s = await (await fetch('/api/einrichtung/demo', { cache: 'no-store' })).json(); } catch (e) { return weiter(); }
    if (s.demo || s.gegenstueck_laeuft) return weiter();
    if (offen) return;
    offen = document.createElement('div'); offen.className = 'dl-hinter';
    offen.innerHTML = `<div class="dl-dialog" role="dialog" aria-modal="true" aria-labelledby="dl-titel">
      <h2 id="dl-titel">Demo starten?</h2>
      <p>Die Demo läuft gerade nicht. Sie startet mit <strong>erfundenen Daten</strong> in einem eigenen Ordner, ruft nichts bei Börsen ab und lässt das echte iC Delta unberührt.</p>
      <p>Sie <strong>belegt Speicher</strong>, solange sie läuft. Nach 10 Minuten fragt sie, ob sie weiterlaufen soll, und lässt sich jederzeit mit „Demo stoppen“ beenden.</p>
      <div class="dl-knoepfe"><button class="nein" type="button">Abbrechen</button><button class="ja" type="button">Demo starten</button></div></div>`;
    const zu = () => { offen.remove(); offen = null; };
    offen.querySelector('.nein').onclick = zu;
    offen.querySelector('.ja').onclick = async () => {
      const ja = offen.querySelector('.ja'); ja.disabled = true; ja.textContent = 'Startet …';
      try { await post('/api/einrichtung/demo/starten'); } catch (e) { ja.textContent = 'Fehler beim Start'; return; }
      for (let i = 0; i < 60; i++) {                       // bis zu 60 s auf die Demo warten
        await new Promise((r) => setTimeout(r, 1000));
        try { if ((await (await fetch('/api/einrichtung/demo', { cache: 'no-store' })).json()).gegenstueck_laeuft) { await new Promise((r) => setTimeout(r, 2500)); zu(); return weiter(); } } catch (e) { /* weiter warten */ }
        ja.textContent = `Startet … ${i + 1} s`;
      }
      ja.textContent = 'Startet nicht – Protokoll: ~/.local/share/marktdaten-demo.log';
    };
    document.body.append(offen);
    offen.querySelector('.ja').focus();
  };

  async function holen() {
    if (ende) return;
    try { stand = await (await fetch('/api/einrichtung/demo', { cache: 'no-store' })).json(); } catch (e) { return; }
    if (!stand.terminal) { clearInterval(takt); return; }
    leiste();
    if (stand.frage) dialog(true);
  }
  const takt = setInterval(holen, 20000);
  holen();
})();
