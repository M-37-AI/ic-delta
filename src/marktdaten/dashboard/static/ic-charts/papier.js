/*
 * papier.js – Marktdaten: Papierhandel in iC Charts (simuliert, kein echtes Geld).
 *
 * Orders werden nur hier aufgegeben: über die Karte „Papierhandel“ rechts oder mit dem Order-Werkzeug links
 * (Klick in den Chart setzt den Preis: beim Kauf darunter Limit, darüber Stopp; beim Verkauf umgekehrt).
 * Preis, Menge und Gesamtbetrag hängen zusammen: wer den Betrag eingibt, bekommt die Stückzahl und umgekehrt.
 * Ausgeführt wird im Kern des Dashboards (/api/papier, live/papier.py) gegen Kurs und Orderbuch.
 * Im Chart: Pfeile für Ausführungen, Linie und Schild mit ✕ für jede offene Order, eine Linie für den Einstand.
 */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const A = () => window.__sarge;
  const ZEICHEN = { EUR: '€', USD: '$' };
  const zahl = (x, n = 2) => (x == null || !isFinite(x) ? '–' : x.toLocaleString('de-DE', { minimumFractionDigits: n, maximumFractionDigits: n }));
  const preis = (x) => (x == null ? '–' : zahl(x, x < 10 ? 3 : 2));
  const sek = (iso) => (iso ? Date.parse(String(iso).replace(/(\.\d{3})\d+/, '$1')) / 1000 : null);   // Nanosekunden kürzen
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const wert = () => daten?.handelbar?.[A().state.symbol];
  const krypto = () => wert()?.markt === 'KRYPTO';
  const stueck = (m, k = krypto()) => zahl(m, k ? (m < 1 ? 4 : 3) : 0);
  const einheit = () => (krypto() ? A().state.symbol.replace(/USDT$|USDC$/, '') : 'Stk');
  const artName = { markt: 'Markt', limit: 'Limit', stopp: 'Stopp' };
  let daten = null, seite = 'kauf', zuletzt = 'menge', mengeFuer = null;
  let linien = [], vorschau = null, markerSig = '', linienSig = '', symbolVorher = null;

  // ---------------- Daten ----------------
  async function laden() {
    try { daten = await (await fetch('/api/papier', { cache: 'no-store' })).json(); } catch (e) { return; }
    zeichnen();
  }
  const position = () => daten?.positionen.find((p) => p.symbol === A().state.symbol);
  const offene = () => (daten?.offen || []).filter((o) => o.symbol === A().state.symbol);

  // ---------------- Preis, Menge, Gesamt ----------------
  const art = () => $('#pdArt').value;
  const bezug = () => (art() === 'markt' ? A().state.lastPrice : +$('#pdPreis').value || A().state.lastPrice);
  function preisSchritt(p) { return p >= 10000 ? 5 : p >= 1000 ? 0.5 : p >= 100 ? 0.05 : p >= 10 ? 0.01 : 0.001; }
  function runden(m) { return krypto() ? Math.floor(m * 1e6) / 1e6 : Math.floor(m); }
  // Menge oder Gesamt nachführen, je nachdem, was zuletzt eingegeben wurde
  function abgleichen() {
    const p = bezug(); if (!p) return;
    if (zuletzt === 'gesamt') $('#pdMenge').value = runden((+$('#pdGesamt').value || 0) / p);
    else $('#pdGesamt').value = ((+$('#pdMenge').value || 0) * p).toFixed(2);
  }
  function plusMinus(feld, richtung) {
    const el = $('#pd' + feld[0].toUpperCase() + feld.slice(1)), v = +el.value || 0;
    const schritt = feld === 'preis' ? preisSchritt(v || A().state.lastPrice) : feld === 'menge' ? (krypto() ? (v >= 1 ? 0.1 : 0.001) : 1) : 100;
    const neu = Math.max(0, Math.round((v + richtung * schritt) / schritt) * schritt);
    el.value = feld === 'menge' ? runden(neu) : +neu.toFixed(feld === 'gesamt' ? 2 : 6);
    if (feld !== 'preis') zuletzt = feld;
    abgleichen(); vorschauSetzen(); zeichnen();
  }

  // ---------------- Karte ----------------
  function zeichnen() {
    const w = wert(), sym = A().state.symbol;
    $('#orderCard').classList.toggle('aus', !w);
    $('#pdWaehrung').textContent = w ? ZEICHEN[w.waehrung] : '';
    if (!w) {
      $('#pdInfo').innerHTML = `<span class="pd-warn">${esc(sym)} ist nicht auf der Watchlist. Papierhandel geht nur mit Werten der Watchlist (Reiter Kurse → Wert hinzufügen).</span>`;
      $('#pdOffen').innerHTML = ''; $('#pdOffenKopf').hidden = true; chartAktualisieren(); return;
    }
    if (mengeFuer !== sym) {                                     // neuer Wert: sinnvolle Vorgaben
      mengeFuer = sym; zuletzt = 'menge';
      $('#pdMenge').value = w.markt === 'KRYPTO' ? '0.01' : '10';
      if (art() !== 'markt') $('#pdPreis').value = +(A().state.lastPrice || 0).toFixed(A().state.lastPrice < 10 ? 3 : 2);
      abgleichen();
    }
    if (art() === 'markt' && document.activeElement?.id !== 'pdGesamt' && document.activeElement?.id !== 'pdMenge') abgleichen();   // Markt-Order: Betrag folgt dem Kurs
    const z = ZEICHEN[w.waehrung], k = daten.konto[w.waehrung], pos = position();
    $('#pdPreisZeile').hidden = art() === 'markt';
    $('#pdPreisLabel').textContent = art() === 'stopp' ? 'Stopp-Preis' : 'Limit-Preis';
    $('#pdPreisEinheit').textContent = $('#pdGesamtEinheit').textContent = z;
    $('#pdMengeEinheit').textContent = einheit();
    $('#pdLos').textContent = `${seite === 'kauf' ? 'Kauf' : 'Verkauf'}-Order aufgeben`;
    $('#pdLos').className = 'primary pd-los ' + seite;
    $('#pdHinweis').textContent = w.markt === 'DE' ? 'Xetra · 15 Min. verzögert' : w.markt === 'US' ? 'US · IEX live' : 'Binance live';
    const erklaerung = art() === 'markt' ? 'sofort zum besten Kurs, durch das Orderbuch'
      : art() === 'limit' ? (seite === 'kauf' ? 'ausgeführt, sobald der Kurs auf das Limit fällt' : 'ausgeführt, sobald der Kurs auf das Limit steigt')
        : (seite === 'kauf' ? 'wird zur Markt-Order, sobald der Kurs den Stopp erreicht' : 'Schutz: verkauft, sobald der Kurs auf den Stopp fällt');
    $('#pdInfo').innerHTML = `<span>${erklaerung}</span>
      <span>Guthaben <b>${zahl(k.guthaben)} ${z}</b> · Bestand <b>${pos ? stueck(pos.menge) : 0} ${esc(einheit())}</b>${pos ? ` <button class="pd-link" id="pdAlles" title="ganzen Bestand verkaufen">alles verkaufen</button>` : ''}</span>`;
    if ($('#pdAlles')) $('#pdAlles').onclick = () => { seiteSetzen('verkauf'); $('#pdMenge').value = pos.menge; zuletzt = 'menge'; abgleichen(); vorschauSetzen(); zeichnen(); };
    const liste = offene();
    $('#pdOffenKopf').hidden = !liste.length;
    $('#pdOffenKopf').textContent = `Offene Orders (${liste.length})`;
    $('#pdOffen').innerHTML = liste.map((o) => { const p = o.limit_preis ?? o.stopp_preis;
      return `<li><span class="pd-tag ${o.seite}">${o.seite === 'kauf' ? 'Kauf' : 'Verkauf'}</span>
        <span class="p"><b>${artName[o.art]}${p ? ' ' + preis(p) + ' ' + z : ''}</b> · ${stueck(o.menge)} ${esc(einheit())}${p ? ` · ${zahl(o.menge * p)} ${z}` : ''}
          <small>${esc(o.hinweis || (o.art === 'limit' ? 'wartet auf Limit' : o.art === 'stopp' ? 'wartet auf Stopp' : 'wartet auf Kurs'))}</small></span>
        <button class="pd-storno" data-storno="${o.nr}">Stornieren</button></li>`; }).join('');
    $('#pdOffen').querySelectorAll('[data-storno]').forEach((b) => b.onclick = () => stornieren(+b.dataset.storno));
    chartAktualisieren();
  }
  function seiteSetzen(s) {
    seite = s; $('#orderCard').querySelectorAll('[data-seite]').forEach((x) => x.classList.toggle('on', x.dataset.seite === s));
  }

  async function stornieren(nr) {
    await fetch('/api/papier/order?nr=' + nr, { method: 'DELETE' });
    A().toast('Order storniert', `${A().state.symbol} · Nr. ${nr}`); laden();
  }

  async function aufgeben() {
    const w = wert(); if (!w) return;
    const p = +$('#pdPreis').value, m = +$('#pdMenge').value;
    if (!(m > 0)) { A().toast('Order nicht aufgegeben', 'Menge oder Gesamtbetrag eingeben'); return; }
    const body = { symbol: A().state.symbol, seite, art: art(), menge: m, notiz: $('#pdNotiz').value };
    if (art() === 'limit') body.limit = p; if (art() === 'stopp') body.stopp = p;
    $('#pdLos').disabled = true;
    let r;
    try { r = await (await fetch('/api/papier/order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json(); }
    catch (e) { r = { fehler: 'Dashboard nicht erreichbar' }; }
    $('#pdLos').disabled = false;
    if (r.fehler) { A().toast('Order nicht aufgegeben', r.fehler); return; }
    const o = r.order, z = ZEICHEN[w.waehrung];
    if (o.status === 'ausgefuehrt') A().toast(`${o.seite === 'kauf' ? 'Gekauft' : 'Verkauft'}: ${o.symbol}`, `${stueck(o.menge)} ${einheit()} zu ${preis(o.preis)} ${z} = ${zahl(o.menge * o.preis)} ${z} · ${o.hinweis}`, 'signal');
    else if (o.status === 'abgelehnt') A().toast('Order abgelehnt', o.hinweis);
    else A().toast('Order offen', `${o.seite === 'kauf' ? 'Kauf' : 'Verkauf'}-${artName[o.art]} ${preis(o.limit_preis ?? o.stopp_preis)} ${z} · ${stueck(o.menge)} ${einheit()}. Löschen: ✕ am Schild im Chart oder „Stornieren“ in der Karte.`, 'signal');
    $('#pdNotiz').value = ''; vorschauWeg();
    laden();
  }

  // ---------------- Chart: Ausführungen, offene Orders, Einstand, Vorschau ----------------
  function barZeit(t) {
    const b = A().state.bars; if (!b.length || t == null || t < b[0].time) return null;
    let lo = 0, hi = b.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (b[mid].time <= t) lo = mid; else hi = mid - 1; }
    return A().state.times[lo];                                  // Anzeigezeit der Kerze (iC Charts zeigt Ortszeit)
  }
  function chartAktualisieren() {
    const S = A(); if (!S || !daten) return;
    const sym = S.state.symbol, b = S.state.bars;
    const ausgefuehrt = (daten.verlauf || []).filter((o) => o.symbol === sym && o.status === 'ausgefuehrt');
    const marker = ausgefuehrt.map((o) => ({ time: barZeit(sek(o.datenzeit || o.ausgefuehrt)), kauf: o.seite === 'kauf', o })).filter((x) => x.time != null)
      .map(({ time, kauf, o }) => ({ time, position: kauf ? 'belowBar' : 'aboveBar', shape: kauf ? 'arrowUp' : 'arrowDown', color: kauf ? '#2fcf7a' : '#f05454',
        text: `${kauf ? 'K' : 'V'} ${stueck(o.menge, o.markt === 'KRYPTO')}` }))
      .sort((x, y) => x.time - y.time);
    const sig = sym + (b[0]?.time || '') + b.length + JSON.stringify(marker);
    if (sig !== markerSig) { markerSig = sig; try { S.candle.setMarkers(marker); } catch (e) { /* Chart lädt noch */ } }
    const pos = position();
    const soll = [...offene().map((o) => ({ price: o.limit_preis ?? o.stopp_preis, color: o.seite === 'kauf' ? '#2fcf7a' : '#f05454', style: 2, title: '' })).filter((l) => l.price),
      ...(pos ? [{ price: pos.einstand, color: '#4fc79a', style: 0, title: `Ø Einstand ${stueck(pos.menge)}` }] : [])];
    const lsig = sym + JSON.stringify(soll);
    if (lsig !== linienSig) {
      linienSig = lsig;
      linien.forEach((l) => { try { S.candle.removePriceLine(l); } catch (e) { /* schon weg */ } });
      linien = soll.map((l) => S.candle.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.style, axisLabelVisible: true, title: l.title }));
    }
    schilderZeichnen();
  }
  // Schild an jeder offenen Order: was sie ist und ein ✕ zum Löschen
  function schilderZeichnen() {
    const box = $('#pdSchilder'); if (!box || !daten) return;
    const w = wert(), z = w ? ZEICHEN[w.waehrung] : '';
    const html = offene().filter((o) => o.limit_preis ?? o.stopp_preis).map((o) => { const p = o.limit_preis ?? o.stopp_preis;
      return `<div class="pd-schild ${o.seite}" data-preis="${p}"><b>${o.seite === 'kauf' ? 'Kauf' : 'Verkauf'}-${artName[o.art]}</b>
        ${stueck(o.menge)} ${esc(einheit())} · ${zahl(o.menge * p)} ${z}<button data-storno="${o.nr}" title="Order löschen (stornieren)">✕</button></div>`; }).join('');
    if (box.dataset.html !== html) {                             // nur bei Änderungen neu, damit Klicks nicht ins Leere gehen
      box.dataset.html = html; box.innerHTML = html;
      box.querySelectorAll('[data-storno]').forEach((b) => b.onclick = (e) => { e.stopPropagation(); stornieren(+b.dataset.storno); });
    }
    schilderSetzen();
  }
  function schilderSetzen() {
    const S = A(); if (!S) return;
    const rechts = (S.main.priceScale('right').width() || 60) + 10, hoehe = S.main.chartElement().clientHeight;
    document.querySelectorAll('#pdSchilder .pd-schild').forEach((el) => {
      const y = S.candle.priceToCoordinate(+el.dataset.preis);
      el.style.display = y == null || y < 4 || y > hoehe - 4 ? 'none' : '';
      el.style.top = `${y - 12}px`; el.style.right = `${rechts}px`;
    });
  }
  function vorschauWeg() { if (vorschau) { try { A().candle.removePriceLine(vorschau); } catch (e) { /* schon weg */ } vorschau = null; } }
  // Vorschau-Linie, solange eine Limit- oder Stopp-Order eingestellt wird: zeigt Art, Menge und Betrag
  function vorschauSetzen() {
    vorschauWeg();
    const w = wert(), p = +$('#pdPreis').value; if (!w || art() === 'markt' || !p) return;
    const m = +$('#pdMenge').value || 0;
    vorschau = A().candle.createPriceLine({ price: p, color: '#f5b942', lineWidth: 1, lineStyle: 1, axisLabelVisible: true,
      title: `Vorschau ${seite === 'kauf' ? 'Kauf' : 'Verkauf'}-${artName[art()]} · ${stueck(m)} ${einheit()} · ${zahl(m * p)} ${ZEICHEN[w.waehrung]}` });
  }

  // Order-Werkzeug: Klick in den Chart setzt Preis und Orderart
  function chartKlick(y) {
    const S = A(), p = S.candle.coordinateToPrice(y), last = S.state.lastPrice;
    if (!isFinite(p) || !last) return;
    const unter = p < last;
    $('#pdArt').value = seite === 'kauf' ? (unter ? 'limit' : 'stopp') : (unter ? 'stopp' : 'limit');
    $('#pdPreis').value = +p.toFixed(p < 10 ? 3 : 2);
    abgleichen(); vorschauSetzen();
    document.querySelector('#toolbar [data-tool="cross"]')?.click();
    zeichnen();
    $('#pdMenge').focus(); $('#pdMenge').select();
    S.toast(`${seite === 'kauf' ? 'Kauf' : 'Verkauf'}-${artName[art()]} vorbereitet`, `Preis ${preis(+$('#pdPreis').value)} gesetzt. Menge oder Betrag rechts prüfen, dann „Order aufgeben“.`);
  }

  // ---------------- Start ----------------
  function start() {
    const S = A();
    if (!S || !S.state.bars.length) return setTimeout(start, 300);
    const schilder = document.createElement('div'); schilder.id = 'pdSchilder'; $('#stage').appendChild(schilder);
    $('#orderCard').querySelectorAll('[data-seite]').forEach((b) => b.onclick = () => { seiteSetzen(b.dataset.seite); vorschauSetzen(); zeichnen(); });
    $('#pdArt').addEventListener('change', () => {
      if (art() !== 'markt' && !+$('#pdPreis').value) $('#pdPreis').value = +(S.state.lastPrice || 0).toFixed(S.state.lastPrice < 10 ? 3 : 2);
      abgleichen(); vorschauSetzen(); zeichnen();
    });
    $('#pdPreis').addEventListener('input', () => { abgleichen(); vorschauSetzen(); zeichnen(); });
    $('#pdMenge').addEventListener('input', () => { zuletzt = 'menge'; abgleichen(); vorschauSetzen(); zeichnen(); });
    $('#pdGesamt').addEventListener('input', () => { zuletzt = 'gesamt'; abgleichen(); vorschauSetzen(); zeichnen(); });
    $('#pdGesamt').addEventListener('blur', () => { zuletzt = 'menge'; abgleichen(); zeichnen(); });   // Betrag auf ganze Stück runden
    $('#orderCard').querySelectorAll('[data-pm]').forEach((b) => b.onclick = () => { const [f, r] = b.dataset.pm.split(':'); plusMinus(f, +r); });
    $('#pdLos').onclick = aufgeben;
    const knopf = document.querySelector('#toolbar [data-tool="order"]');
    knopf.addEventListener('click', () => {                      // nach setTool() der App: eigener Hinweis
      const h = $('#toolHint'); h.textContent = 'Klick in den Chart setzt den Order-Preis (Kauf: darunter Limit, darüber Stopp)'; h.style.display = 'block';
    });
    addEventListener('keydown', (e) => { if (e.altKey && e.code === 'KeyO') { e.preventDefault(); knopf.click(); } });
    const el = S.main.chartElement();
    let unten = null;
    el.addEventListener('pointerdown', (e) => { if (e.button === 0) unten = { x: e.clientX, y: e.clientY }; }, true);
    el.addEventListener('pointerup', (e) => {
      if (!unten || S.state.tool !== 'order') return;
      const weit = Math.hypot(e.clientX - unten.x, e.clientY - unten.y); unten = null;
      if (weit > 4) return;
      chartKlick(e.clientY - el.getBoundingClientRect().top);
    }, true);
    laden();
    setInterval(() => { if (!document.hidden) laden(); }, 4000);
    setInterval(() => { if (!document.hidden) schilderSetzen(); }, 120);   // Schilder folgen Zoom und Verschieben
    setInterval(() => {                                          // Symbolwechsel: Karte und Chart sofort anpassen
      const s = S.state.symbol + '|' + S.state.tf + '|' + (S.state.bars[0]?.time || '');
      if (s !== symbolVorher) { symbolVorher = s; vorschauWeg(); markerSig = ''; linienSig = ''; if (daten) zeichnen(); }
    }, 500);
  }
  start();
})();
