/*
 * heatmap.js – Liquiditäts-Heatmap für iC Charts: das Orderbuch je Preisstufe im Zeitverlauf, hinter den Kerzen.
 * Eigener Code (Marktdaten/iC Delta). Zeichnet über die Plugin-Schnittstelle von Lightweight Charts 4.2 (Apache 2.0).
 *
 * Daten:
 *   Deutsche Werte  Analysen-Dienst /api/heatmap (Xetra-Rohdaten: beste 5 Stufen je Seite, 15 Min. verzögert),
 *                   mit erkannten Wänden und ihrem Ende (abgearbeitet, zurückgezogen, aus dem Blick, steht).
 *   Krypto          ebenfalls /api/heatmap: Buchtiefe aus der Tabelle buch_tiefe (Binance, bis 1.000 Stufen, etwa ±0,3 %,
 *                   in Preisbändern je Minute gemittelt; Wände ab 2 Minuten, Ende über die Kerzen statt über Abschlüsse).
 *   US-Werte        kein Buch mit Tiefe vorhanden (nur bester Kurs) – Hinweis statt Heatmap.
 * Farbe: Kaufseite grün, Verkaufsseite rot. Gezeichnet werden nur die größten Stufen (Regler in der Infozeile, Standard: größte
 * 20 % aller Mengen im geladenen Zeitraum), damit der Chart frei bleibt; Deckkraft nach Menge zwischen Schwelle und Skala.
 */
(() => {
  'use strict';
  const FACH = { '1m': 15, '5m': 60, '15m': 300, '1h': 900 };   // Sekunden je Spalte, passend zur Kerzenbreite
  const ANALYSEN_TAKT = 30000;
  const ANALYSEN = location.protocol.startsWith('http') ? `${location.protocol}//${location.hostname}:${(+location.port || 8812) + 1}` : null;
  const FARBE = { B: [47, 207, 122], A: [240, 84, 84] };
  const fmt = (x, n = 2) => (x == null ? '–' : Number(x).toLocaleString('de-DE', { minimumFractionDigits: n, maximumFractionDigits: n }));
  const stk = (x) => Number(x).toLocaleString('de-DE', { maximumFractionDigits: Math.abs(x) >= 100 ? 0 : Math.abs(x) >= 1 ? 2 : 4 });
  const einheit = () => (H.art === 'krypto' ? (H.sym || '').replace(/USDT$|USDC$|EUR$|BTC$/, '') || 'Stk' : 'Stk');
  const uhr = (t) => new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Europe/Berlin' });

  const H = {
    an: false, art: null, sym: null, tf: null, daten: null, fehler: '', laden: false, timer: null, vonGeladen: null,
    anteil: (() => { try { return +localStorage.getItem('ic-heatmap-anteil') || 20; } catch (e) { return 20; } })(),   // größte x % zeigen
  };
  // Schwelle: Menge, ab der eine Zelle gezeichnet wird (Perzentil über alle Zellen des geladenen Zeitraums)
  const schwelleCache = new WeakMap();
  function schwelle(d) {
    if (!d?.spalten?.length) return 0;
    let mengen = schwelleCache.get(d);
    if (!mengen) { mengen = d.spalten.flatMap((s) => [...s.b, ...s.a].map((x) => x[1])).sort((x, y) => x - y); schwelleCache.set(d, mengen); }
    if (H.anteil >= 100) return 0;
    return mengen[Math.min(mengen.length - 1, Math.floor(mengen.length * (1 - H.anteil / 100)))] || 0;
  }
  let A = null, ebene = null, knopf = null, info = null, tipp = null;

  // ---------------- Ebene hinter den Kerzen ----------------
  class Ebene {
    // Hintergrund-Durchgang: liegt unter Kerzen, Linien und Gitter-Beschriftung
    constructor() { this._view = { zOrder: () => 'normal', renderer: () => ({ draw: () => {}, drawBackground: (target) => zeichnen(target) }) }; }
    attached({ requestUpdate }) { this.requestUpdate = requestUpdate; }
    paneViews() { return [this._view]; }
    updateAllViews() {}
  }
  const neuZeichnen = () => ebene?.requestUpdate?.();

  // Zeit (UTC-Sekunden) -> x: Kerze suchen, innerhalb der Kerze anteilig; Lücken (Nacht, Wochenende) nicht überbrücken
  function xFuer(t, ende = false) {
    const bars = A.state.bars, n = bars.length, sek = A.TFS.find((x) => x.id === A.state.tf).sec;
    if (!n || t < bars[0].time) return null;
    let lo = 0, hi = n - 1;
    if (ende) t -= 1e-6;                                     // Spaltenende gehört noch zur Kerze davor
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (bars[m].time <= t) lo = m; else hi = m - 1; }
    const anteil = (t - bars[lo].time) / sek;
    if (anteil >= 1) return null;                            // keine Kerze zu dieser Zeit (Nacht, Abendhandel, Lücke)
    const ts = A.main.timeScale(), c0 = ts.logicalToCoordinate(lo), c1 = ts.logicalToCoordinate(lo + 1);   // 4.2 rechnet nur ganze Kerzen um
    if (c0 == null || c1 == null) return null;
    return c0 + (Math.max(0, anteil) + (ende ? 1e-6 / sek : 0) - 0.5) * (c1 - c0);
  }

  function zellen() {
    return H.daten ? { spalten: H.daten.spalten, skala: H.daten.skala, fach: H.daten.fach, tick: H.daten.tick } : null;
  }

  function zeichnen(target) {
    if (!H.an || !A || H.sym !== A.state.symbol) return;
    const z = zellen(); if (!z || !z.spalten.length || !z.skala) return;
    const s = A.candle, tick = z.tick || 0.01, grenze = schwelle(H.daten), lg = Math.log(Math.max(grenze, 1e-9)), spanne = Math.max(1e-9, Math.log(Math.max(z.skala, grenze * 1.0001)) - lg);
    target.useBitmapCoordinateSpace(({ context: c, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
      const breite = bitmapSize.width;
      let gezeichnet = 0;
      for (const sp of z.spalten) {
        const x0 = xFuer(sp.t), x1 = xFuer(sp.t + z.fach, true);
        if (x0 == null || x1 == null || x1 < 0 || x0 * hr > breite) continue;
        gezeichnet++;
        const xl = Math.round(x0 * hr), w = Math.max(1, Math.round(x1 * hr) - xl);   // Kanten runden statt Breite: keine Fugen
        for (const [seite, stufen] of [['B', sp.b], ['A', sp.a]]) {
          const [r, g, b] = FARBE[seite];
          for (const [p, m] of stufen) {
            if (m < grenze || m <= 0) continue;                     // nur die größten Stufen: der Chart bleibt frei
            const y0 = s.priceToCoordinate(p + tick / 2), y1 = s.priceToCoordinate(p - tick / 2);
            if (y0 == null || y1 == null) continue;
            const a = Math.min(1, Math.max(0, (Math.log(m) - lg) / spanne));
            c.fillStyle = `rgba(${r},${g},${b},${(0.22 + 0.58 * a).toFixed(3)})`;
            const yo = Math.round(Math.min(y0, y1) * vr);
            c.fillRect(xl, yo, w, Math.max(1, Math.round(Math.max(y0, y1) * vr) - yo));
          }
        }
      }
      if (H.ohneKerzen !== !gezeichnet) { H.ohneKerzen = !gezeichnet; setTimeout(infoZeigen); }
      if (!H.daten?.waende) return;
      for (const wd of H.daten.waende) {                     // Wände: Linie über ihre Dauer, Ende markiert
        const x0 = xFuer(wd.von), x1 = xFuer(wd.ende_t || wd.bis, true), y = s.priceToCoordinate(wd.preis);
        if (x0 == null || x1 == null || y == null) continue;
        const [r, g, b] = FARBE[wd.seite === 'kauf' ? 'B' : 'A'];
        c.strokeStyle = `rgba(${r},${g},${b},0.95)`; c.lineWidth = 2 * vr;
        c.beginPath(); c.moveTo(x0 * hr, y * vr); c.lineTo(x1 * hr, y * vr); c.stroke();
        const ex = x1 * hr, ey = y * vr, d = 4 * hr;
        if (wd.ende === 'zurückgezogen') {                   // ×: verschwunden ohne Handel (bei "nah" in Gelb)
          c.strokeStyle = wd.nah ? '#f5b942' : `rgba(${r},${g},${b},0.95)`; c.lineWidth = (wd.nah ? 2.2 : 1.6) * hr;
          c.beginPath(); c.moveTo(ex - d, ey - d); c.lineTo(ex + d, ey + d); c.moveTo(ex + d, ey - d); c.lineTo(ex - d, ey + d); c.stroke();
        } else if (wd.ende === 'abgearbeitet') {             // Punkt: weggehandelt
          c.fillStyle = `rgb(${r},${g},${b})`; c.beginPath(); c.arc(ex, ey, 3.5 * hr, 0, 7); c.fill();
        }
      }
    });
  }

  // ---------------- Daten ----------------
  function artFuer(sym) {
    const akt = window.SNAPSHOT?.aktien?.[sym];
    if (akt) return akt.waehrung === 'EUR' ? 'xetra' : 'keins';
    return 'krypto';
  }

  async function analysenLaden() {
    const st = A.state, sym = st.symbol, fach = FACH[st.tf];
    if (!ANALYSEN || !fach || !st.bars.length) return;
    const r = A.main.timeScale().getVisibleLogicalRange();
    const i0 = Math.max(0, Math.floor(r ? r.from : 0) - 5);
    const von = st.bars[Math.min(i0, st.bars.length - 1)].time, bis = st.bars[st.bars.length - 1].time + 86400;
    H.laden = true; H.vonGeladen = von; infoZeigen();
    try {
      const d = await (await fetch(`${ANALYSEN}/api/heatmap?wert=${encodeURIComponent(sym)}&von=${von}&bis=${Math.round(Math.min(bis, Date.now() / 1000 + 60))}&fach=${fach}`)).json();
      if (A.state.symbol !== sym) return;
      H.daten = d; H.fehler = d.fehler || d.hinweis || '';
    } catch (e) { H.fehler = 'Analysen-Dienst nicht erreichbar'; }
    H.laden = false; infoZeigen(); neuZeichnen();
  }

  function planen() {
    clearInterval(H.timer); H.timer = null;
    if (!H.an) { tipp.hidden = true; infoZeigen(); neuZeichnen(); return; }   // aus: Info und Ebene sofort leeren
    H.sym = A.state.symbol; H.tf = A.state.tf; H.art = artFuer(H.sym); H.daten = null; H.fehler = '';
    if (!FACH[H.tf]) { H.fehler = 'Die Heatmap gibt es für 1m bis 1h. Auf Tages- und 4-Stunden-Kerzen wäre das Buch nur ein Strich.'; }
    else if (H.art === 'keins') { H.fehler = 'Für US-Werte gibt es kein Orderbuch mit Tiefe, nur den besten Kurs.'; }
    else { analysenLaden(); H.timer = setInterval(analysenLaden, ANALYSEN_TAKT); }
    infoZeigen(); neuZeichnen();
  }

  // ---------------- Bedienung ----------------
  function schalten(an) {
    H.an = an === undefined ? !H.an : !!an;
    try { localStorage.setItem('ic-heatmap', H.an ? '1' : '0'); } catch (e) { /* ohne Speicher */ }
    knopf.classList.toggle('on', H.an);
    planen();
  }

  function infoZeigen() {
    if (!info) return;
    info.hidden = !H.an;
    if (!H.an) return;
    const z = H.daten;
    let text;
    if (H.fehler) text = H.fehler;
    else if (H.laden && !z) text = 'lädt …';
    else if (z) {
      const n = (e) => z.waende.filter((w) => w.ende === e).length;
      text = (H.ohneKerzen && z.spalten.length ? `Die Buchdaten (${z.spalten.length} Spalten, ${uhr(z.spalten[0].t)}–${uhr(z.spalten[z.spalten.length - 1].t)}) liegen außerhalb der Kerzen im Bild, z. B. im Abendhandel. ` : '') + `${z.quelle} · Skala bis ${stk(z.skala || 0)} ${einheit()} · ${z.waende.length} Wände: ${n('abgearbeitet')} abgearbeitet, ${n('zurückgezogen')} zurückgezogen (${z.waende.filter((w) => w.nah).length} nah am Kurs)`;
    }
    info.querySelector('.hm-text').textContent = text;
    info.querySelector('.hm-wert').textContent = H.anteil >= 100 ? 'alle' : `größte ${H.anteil} %`;
  }

  // Hover: Wand oder Zelle unter dem Mauszeiger
  function hover(param) {
    if (!H.an || !param.point || H.sym !== A.state.symbol) { tipp.hidden = true; return; }
    const { x, y } = param.point, preis = A.candle.coordinateToPrice(y);
    let html = '';
    if (H.daten?.waende) {
      const wd = H.daten.waende.find((w) => { const x0 = xFuer(w.von), x1 = xFuer(w.ende_t || w.bis, true), yw = A.candle.priceToCoordinate(w.preis); return x0 != null && x1 != null && yw != null && x >= x0 - 4 && x <= x1 + 6 && Math.abs(y - yw) <= 6; });
      if (wd) {
        const ende = { abgearbeitet: wd.gehandelt == null ? 'abgearbeitet: der Kurs hat die Wand erreicht' : `abgearbeitet (${stk(wd.gehandelt)} ${einheit()} gehandelt)`, 'zurückgezogen': `zurückgezogen, ${wd.abstand_ticks ?? '?'} ${H.art === 'krypto' ? 'Bänder' : 'Ticks'} vor dem Kurs${wd.nah ? ' · Muster wie bei Spoofing, kein Beweis' : ''}`, 'aus dem Blick': `aus dem Blick (Kurs hat sich entfernt, ${H.art === 'krypto' ? 'nur etwa ±0,3 %' : 'nur 5 Stufen'} sichtbar)`, steht: 'steht noch' }[wd.ende];
        html = `<b>${wd.seite === 'kauf' ? 'Kaufwand' : 'Verkaufswand'} ${fmt(wd.preis)}</b><br>bis ${stk(wd.menge)} ${einheit()} · ${uhr(wd.von)}–${uhr(wd.ende_t || wd.bis)} (${Math.round(wd.dauer_s / 6) / 10} Min)<br>${ende}`;
      }
    }
    if (!html) {
      const z = zellen();                                    // Zeit aus x zurückrechnen, Zelle an Preis und Zeit suchen
      if (z && z.spalten.length && preis != null) {
        const tick = z.tick || 0.01, l = A.main.timeScale().coordinateToLogical(x);
        const bars = A.state.bars, i = Math.max(0, Math.min(bars.length - 1, Math.round(l)));
        const sek = A.TFS.find((q) => q.id === A.state.tf).sec, tz = bars[i] ? bars[i].time + (l - i + 0.5) * sek : null;
        const sp = tz == null ? null : z.spalten.find((s) => tz >= s.t && tz < s.t + z.fach);
        const treffer = sp && [...sp.b.map((v) => ['Kauf', ...v]), ...sp.a.map((v) => ['Verkauf', ...v])].find((v) => Math.abs(v[1] - preis) <= tick / 2 && v[2] >= schwelle(H.daten));
        if (treffer) html = `${treffer[0]} ${fmt(treffer[1])}${H.art === 'krypto' ? ` (Band ${fmt(tick, tick < 1 ? 2 : 0)})` : ''} · Ø ${stk(treffer[2])} ${einheit()}<br><span class="muted">${uhr(sp.t)} · ${z.fach} s je Spalte</span>`;
      }
    }
    if (!html) { tipp.hidden = true; return; }
    tipp.innerHTML = html; tipp.hidden = false;
    const box = document.getElementById('pane-main').getBoundingClientRect();
    tipp.style.left = Math.min(x + 16, box.width - 260) + 'px'; tipp.style.top = Math.max(8, y - 12) + 'px';
  }

  // ---------------- Werkzeug für Claude (agent-panel.js) ----------------
  async function werkzeug({ on = true, anteil } = {}) {
    if (on !== H.an) schalten(on);
    if (anteil) {                                            // nur die größten x % zeigen
      H.anteil = Math.max(5, Math.min(100, Math.round(anteil / 5) * 5));
      const r = info.querySelector('input'); if (r) r.value = H.anteil;
      try { localStorage.setItem('ic-heatmap-anteil', H.anteil); } catch (e) { /* ohne Speicher */ }
      infoZeigen(); neuZeichnen();
    }
    if (!H.an) return { heatmap: 'aus' };
    for (let i = 0; i < 40 && (H.laden || (!H.daten && !H.fehler)); i++) await new Promise((r) => setTimeout(r, 150));
    if (H.fehler) return { heatmap: 'an', symbol: H.sym, hinweis: H.fehler };
    const d = H.daten, zeit = (t) => new Date(t * 1000).toLocaleString('de-DE', { timeZone: 'Europe/Berlin' });
    const zaehlen = {}; d.waende.forEach((w) => { zaehlen[w.ende] = (zaehlen[w.ende] || 0) + 1; });
    return {
      heatmap: 'an', symbol: H.sym, gezeigt: H.anteil >= 100 ? 'alle Stufen' : `nur die größten ${H.anteil} % (ab ${stk(schwelle(d))} ${einheit()})`, quelle: d.quelle, zeitraum: d.spalten.length ? [zeit(d.spalten[0].t), zeit(d.spalten[d.spalten.length - 1].t)] : null,
      sekunden_je_spalte: d.fach, tick_oder_band: d.tick, einheit: einheit(), skala: d.skala, waende_nach_ende: zaehlen,
      waende: d.waende.slice(-15).map((w) => ({ seite: w.seite, preis: w.preis, menge_max: w.menge, von: zeit(w.von), dauer_min: Math.round(w.dauer_s / 6) / 10, ende: w.ende, abstand_ticks: w.abstand_ticks, gehandelt: w.gehandelt, nah_am_kurs: w.nah || false, unterbrechungen: w.unterbrechungen })),
      regeln: 'Wand = Stufe mit mindestens 4-mal der mittleren Stufenmenge ihrer Seite, mindestens 20 s. zurückgezogen = verschwunden ohne Handel, obwohl der Preis noch im sichtbaren Bereich lag; nah = höchstens 2 Ticks vom besten Kurs. Ein Muster wie bei Spoofing, kein Beweis: Market Maker ziehen und erneuern ihre Orders ständig. Xetra: nur 5 Stufen je Seite sichtbar. Krypto: Preisbänder je Minute, Wände ab 2 Minuten, abgearbeitet heißt dort nur, dass der Kurs die Wand erreicht hat (Abschlüsse werden bei Krypto nicht gespeichert).',
    };
  }

  // ---------------- Start ----------------
  function start() {
    A = window.__sarge;
    const st = document.createElement('style');
    st.textContent = `.hm-info{position:absolute;left:14px;bottom:30px;z-index:6;max-width:calc(100% - 120px);font:11px/1.4 var(--ui);color:var(--text2);background:color-mix(in srgb,var(--panel) 88%,transparent);border:1px solid var(--line2);border-radius:8px;padding:4px 9px;pointer-events:none}
.hm-info b{color:var(--text);margin-right:4px}.hm-regler{pointer-events:auto;display:inline-flex;align-items:center;gap:5px;margin:0 8px 0 2px;color:var(--text)}.hm-regler input{width:90px;accent-color:var(--accent)}.hm-wert{min-width:74px;font-variant-numeric:tabular-nums}.hm-skala{display:inline-flex;gap:2px;vertical-align:-1px;margin-right:4px}.hm-skala i{width:26px;height:9px;border-radius:2px}
.hm-b{background:linear-gradient(90deg,rgba(47,207,122,.05),rgba(47,207,122,.65))}.hm-a{background:linear-gradient(90deg,rgba(240,84,84,.05),rgba(240,84,84,.65))}
.hm-tipp{position:absolute;z-index:7;width:max-content;max-width:250px;font:12px/1.45 var(--ui);color:var(--text);background:var(--panel2);border:1px solid var(--line2);border-radius:8px;padding:6px 9px;box-shadow:var(--shadow);pointer-events:none}
.hm-tipp .muted{color:var(--muted)}`;
    document.head.append(st);
    knopf = document.createElement('button'); knopf.id = 'btnHeatmap'; knopf.className = 'pill'; knopf.type = 'button'; knopf.textContent = '▦ Heatmap';
    knopf.title = 'Liquiditäts-Heatmap: Orderbuch je Preisstufe hinter den Kerzen. Deutsche Werte aus den Xetra-Daten (15 Min. verzögert), Krypto aus dem tiefen Binance-Buch, jeweils mit Wänden.';
    knopf.onclick = () => schalten();
    const vor = document.getElementById('btnReplay'); vor ? vor.before(knopf) : document.getElementById('topbar')?.append(knopf);
    const pane = document.getElementById('pane-main');
    info = document.createElement('div'); info.className = 'hm-info'; info.hidden = true;
    info.innerHTML = `<b>Heatmap</b> <span class="hm-skala"><i class="hm-b"></i><i class="hm-a"></i></span><label class="hm-regler" title="Nur die größten Stufen zeichnen, damit der Chart frei bleibt">zeigen: <input type="range" min="5" max="100" step="5"> <span class="hm-wert"></span></label> <span class="hm-text"></span>`;
    const regler = info.querySelector('input'); regler.value = H.anteil;
    regler.oninput = () => { H.anteil = +regler.value; try { localStorage.setItem('ic-heatmap-anteil', H.anteil); } catch (e) { /* ohne Speicher */ } infoZeigen(); neuZeichnen(); };
    pane.append(info);
    tipp = document.createElement('div'); tipp.className = 'hm-tipp'; tipp.hidden = true; pane.append(tipp);
    ebene = new Ebene(); A.candle.attachPrimitive(ebene);
    A.main.subscribeCrosshairMove(hover);
    A.main.timeScale().subscribeVisibleLogicalRangeChange(() => { if (H.an && H.daten && H.vonGeladen != null) { const r = A.main.timeScale().getVisibleLogicalRange(); if (r && A.state.bars[Math.max(0, Math.floor(r.from))]?.time < H.vonGeladen - 3600) { clearTimeout(H.nach); H.nach = setTimeout(analysenLaden, 400); } } });
    setInterval(() => { if (H.an && (A.state.symbol !== H.sym || A.state.tf !== H.tf)) planen(); }, 400);   // Symbol- oder Zeitwechsel
    if (window.__agent?.TOOLS) window.__agent.TOOLS.liquidity_heatmap = werkzeug;
    const q = new URLSearchParams(location.search).get('heatmap');
    let gemerkt = null; try { gemerkt = localStorage.getItem('ic-heatmap'); } catch (e) { /* ohne Speicher */ }
    if (q === '1' || (q !== '0' && gemerkt === '1')) A.waitLoaded().then(() => schalten(true));
  }
  window.__heatmap = { schalten, werkzeug, zustand: () => H, xFuer };
  const warten = setInterval(() => { if (window.__sarge?.candle && window.__ready) { clearInterval(warten); start(); } }, 100);
})();
