/*
 * felder.js – Marktdaten: rechte Seite von iC Charts als Symbolleiste mit einem Feld in voller Höhe.
 *
 * Statt vier gestapelter Karten (Papierhandel, Orderbuch, Alarme, Mini-Script) zeigt die Seite immer nur ein Feld.
 * Die Symbolleiste am rechten Rand wählt es; ein zweiter Klick auf dasselbe Symbol oder › klappt die Seite zu,
 * dann hat der Chart die volle Breite. Das Claude-Symbol öffnet das Panel aus agent-panel.js.
 * Gewähltes Feld und Zustand merkt sich der Browser.
 */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  let feld = 'watch', zu = false;
  try { feld = localStorage.getItem('sarge.feld') || 'watch'; zu = localStorage.getItem('sarge.feldZu') === '1'; } catch (e) { /* ohne Speicher */ }

  function zeigen() {
    document.body.classList.toggle('seite-zu', zu);
    document.querySelectorAll('#side [data-reiter]').forEach((s) => s.classList.toggle('reiter-aus', s.dataset.reiter !== feld));
    // Orderbuch: iC Charts zeigt ein Live-Buch nur für Krypto; sonst ein Hinweis, wo das Xetra-Buch steht
    const buchDa = $('#bookCard').style.display !== 'none';
    $('#buchLeer').classList.toggle('reiter-aus', feld !== 'buch' || buchDa);
    document.querySelectorAll('#rail [data-feld]').forEach((b) => b.classList.toggle('on', b.dataset.feld === 'claude'
      ? document.body.classList.contains('agent-open') : !zu && b.dataset.feld === feld));
    try { localStorage.setItem('sarge.feld', feld); localStorage.setItem('sarge.feldZu', zu ? '1' : '0'); } catch (e) { /* ohne Speicher */ }
  }
  function oeffnen(f) { feld = f; zu = false; zeigen(); }

  document.querySelectorAll('#rail [data-feld]').forEach((b) => b.onclick = () => {
    if (b.dataset.feld === 'claude') { window.__agent?.toggle(); return; }
    if (b.dataset.feld === feld && !zu) { zu = true; zeigen(); } else oeffnen(b.dataset.feld);
  });
  $('#sideZu').onclick = () => { zu = true; zeigen(); };
  // Werkzeuge im Chart öffnen das passende Feld
  document.querySelector('#toolbar [data-tool="order"]')?.addEventListener('click', () => oeffnen('order'));
  document.querySelector('#toolbar [data-tool="alarm"]')?.addEventListener('click', () => oeffnen('alarme'));
  new MutationObserver(zeigen).observe($('#bookCard'), { attributes: true, attributeFilter: ['style'] });
  new MutationObserver(() => $('#rail [data-feld="claude"]').classList.toggle('on', document.body.classList.contains('agent-open')))
    .observe(document.body, { attributes: true, attributeFilter: ['class'] });
  // Watchlist: die Kursleiste von iC Charts (#tape, gepflegt von app.js) wandert als Liste in ihr Feld,
  // gruppiert nach Markt, mit Kürzel-Kreis und Namen. Die Werte oben aus der Leiste entfallen damit.
  const SNAP = window.SNAPSHOT || { aktien: {} };
  const KRYPTO_NAME = { BTC: 'Bitcoin', ETH: 'Ethereum', SOL: 'Solana' };
  const markt = (s) => (/USDT$/.test(s) ? 'krypto' : SNAP.aktien[s]?.waehrung === 'EUR' ? 'de' : 'us');
  const GRUPPEN = [['krypto', 'Krypto · Binance live'], ['de', 'Deutschland · Xetra, 15 Min. verzögert'], ['us', 'USA']];
  const tape = $('#tape');
  $('#watchCard').appendChild(tape);
  function ordnen() {
    const chips = [...tape.querySelectorAll('.chip')];
    if (!chips.length || chips.every((c) => c.dataset.deko)) return;
    setTimeout(schlusskurse, 0);                                // neue Zeilen: Vortagesschluss gleich eintragen
    tape.querySelectorAll('.wl-kopf').forEach((k) => k.remove());
    for (const [m, titel] of GRUPPEN) {
      const teil = chips.filter((c) => markt(c.dataset.sym) === m);
      if (!teil.length) continue;
      const kopf = document.createElement('div'); kopf.className = 'wl-kopf'; kopf.textContent = titel; tape.appendChild(kopf);
      for (const c of teil) {
        if (!c.dataset.deko) {
          const s = c.dataset.sym, kurz = s.replace(/USDT$/, '');
          const bild = LOGOS[s] || LOGOS[s.replace(/USDT$/, '') + 'USDT'];
          c.insertAdjacentHTML('afterbegin', bild ? `<span class="logo bild ${m}"><img src="${bild}" alt=""></span>` : `<span class="logo ${m}">${kurz.slice(0, 4)}</span>`);
          c.insertAdjacentHTML('beforeend', `<span class="name">${m === 'krypto' ? (KRYPTO_NAME[kurz] || kurz) + ' · USDT' : SNAP.aktien[s]?.name || s}</span>`);
          c.dataset.deko = '1';
        }
        tape.appendChild(c);
      }
    }
  }
  new MutationObserver(ordnen).observe(tape, { childList: true });
  // Logos vom Kern (live/logos.py): einmal laden, danach die Zeilen neu schmücken
  let LOGOS = {};
  fetch('/api/logos').then((r) => r.json()).then((d) => {
    LOGOS = d.symbol || {};
    tape.querySelectorAll('.chip[data-deko]').forEach((c) => { c.querySelector('.logo')?.remove(); c.querySelector('.name')?.remove(); delete c.dataset.deko; });
    ordnen();
  }).catch(() => {});
  ordnen();
  // Deutsche Werte vor dem ersten Xetra-Abschluss des Tages: Schlusskurs des letzten Handelstags statt leerer Zeile
  async function schlusskurse() {
    let d; try { d = await (await fetch('/api/kurse', { cache: 'no-store' })).json(); } catch (e) { return; }
    for (const w of d.werte || []) {
      const c = tape.querySelector(`.chip[data-sym="${w.symbol}"]`);
      if (!c || w.markt !== 'DE' || w.letzter != null || w.vortag == null) continue;
      const p = c.querySelector('.p'), v = c.querySelector('.c');
      if (p.textContent && !p.classList.contains('schluss')) continue;           // app.js hat einen aktuellen Kurs gesetzt
      p.textContent = w.vortag.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); p.classList.add('schluss');
      v.textContent = 'Schluss'; v.className = 'c schluss';
      c.title = 'Noch kein Abschluss heute: Schlusskurs des letzten Handelstags. Xetra liefert ab 9 Uhr, 15 Minuten verzögert.';
    }
  }
  schlusskurse();
  setInterval(schlusskurse, 60000);
  window.__felder = { oeffnen };
  zeigen();
})();
