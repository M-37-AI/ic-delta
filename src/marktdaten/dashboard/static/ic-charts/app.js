/*
 * app.js – Oberfläche, Daten, Live-Anbindung und Zeichenwerkzeuge von "iC Charts".
 * Kopie aus github.com/M-37-AI/ic-charts für das Marktdaten-Dashboard. Angepasst (mit "Marktdaten:" markiert):
 * Aktienliste und Daten kommen aus iC Delta (data/snapshot.js wird vom Dashboard erzeugt), 1-Minuten-Kerzen für Aktien.
 * Eigener Code. Darstellung über Lightweight Charts 4.2.0 (Apache 2.0).
 */
(() => {
  'use strict';
  const LW = window.LightweightCharts;
  const I = window.Indicators;
  const SNAP = window.SNAPSHOT || { krypto: {}, aktien: {} };
  const $ = (s) => document.querySelector(s);

  // ---------------- Konfiguration ----------------
  const KRYPTO_ECHT = !SNAP.demo || SNAP.krypto_echt;   // Marktdaten: Demo nur mit echten Namen auch mit echtem Krypto (Binance)
  const CRYPTO = KRYPTO_ECHT ? ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] : [];
  // Marktdaten: Aktien aus dem Dashboard statt fester Liste
  const STOCKS = Object.keys(SNAP.aktien || {}).length ? Object.keys(SNAP.aktien) : ['AAPL', 'NVDA', 'MSFT', 'SAP'];
  const TFS = [
    { id: '1m', label: '1m', sec: 60 }, { id: '5m', label: '5m', sec: 300 }, { id: '15m', label: '15m', sec: 900 },
    { id: '1h', label: '1h', sec: 3600 }, { id: '4h', label: '4h', sec: 14400 }, { id: '1d', label: '1D', sec: 86400 },
  ];
  const K = window.IndikatorKatalog;   // Indikator-Verzeichnis (indikator-katalog.js)
  // Alte Kurz-IDs der festen Knöpfe (bis Oktober 2026) – gelten weiter für ?ind=…, setIndicator und state.ind
  const IND = ['sma', 'ema', 'bb', 'vwap', 'st', 'rsi', 'macd', 'btcd'].map((id) => ({ id, label: K.NACH_TYP[id].kurz(K.params(id)), color: K.NACH_TYP[id].farbe }));
  const C = { bg: '#0e1218', grid: 'rgba(255,255,255,0.045)', text: '#7d8794', line: '#1a2029', up: '#2fcf7a', down: '#f05454', accent: '#f5b942', warn: '#f5b942' };   // Marktdaten: Töne wie das Dashboard
  const EXAMPLES = [
    { name: 'EMA-Kreuzung', code: '// Schnelle gegen langsame EMA\nschnell = ema(close, 9)\nlangsam = ema(close, 21)\nplot(schnell, "orange")\nplot(langsam, "lila")\nalert(crossover(schnell, langsam), "Kreuz hoch")' },
    { name: 'Bollinger-Ausbruch', code: '// Ausbruch über das obere Band\noben = bbupper(close, 20, 2)\nplot(oben, "blau", "BB oben")\nplot(bblower(close, 20, 2), "blau", "BB unten")\nalert(crossover(close, oben), "Ausbruch")' },
    { name: 'RSI-Wende', code: '// RSI kommt aus dem überverkauften Bereich\nr = rsi(close, 14)\npanel(r, "lila", "RSI")\npanel(ema(r, 9), "orange", "RSI-Signal")\nalert(crossover(r, 30), "RSI dreht")' },
    { name: 'Abstand SMA', code: '// Wie weit ist der Kurs von der SMA 50 weg? (in %)\npanel(close / sma(close, 50) * 100 - 100, "cyan", "Abstand SMA50 %")' },
    { name: 'Eigener Kanal', code: '// Donchian-Kanal: höchstes Hoch / tiefstes Tief\nplot(highest(high, 20), "grün", "Hoch 20")\nplot(lowest(low, 20), "rot", "Tief 20")\nalert(high >= highest(high, 50), "Neues 50er-Hoch")' },
    { name: 'Strategie EMA', code: '// Strategie: kaufen beim Kreuz nach oben, verkaufen beim Kreuz nach unten\n// buy/sell starten den Backtest (nur long, nächste Eröffnung, 0,1 % Gebühr)\nschnell = ema(close, 9)\nlangsam = ema(close, 21)\nplot(schnell, "orange")\nplot(langsam, "lila")\nbuy(crossover(schnell, langsam))\nsell(crossunder(schnell, langsam))' },
    { name: 'Strategie SuperTrend', code: '// SuperTrend (10, 3): Linie unter dem Kurs = Aufwärtstrend\nst = supertrend(10, 3)\nplot(st, "cyan", "SuperTrend")\nbuy(crossover(close, st))\nsell(crossunder(close, st))' },
  ];
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem('sarge.' + k)); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem('sarge.' + k, JSON.stringify(v)); } catch (e) { /* privater Modus o. ä. */ } },
  };

  const params = new URLSearchParams(location.search);
  const state = {
    symbol: (params.get('sym') || 'BTCUSDT').toUpperCase(),
    tf: params.get('tf') || '1h',
    test: params.get('test') === '1',
    ind: Object.fromEntries(IND.map((d) => [d.id, false])),   // abgeleitet aus inds (nur noch zur Kompatibilität)
    inds: [], nextUid: 1, ankerFuer: null,
    of: { sym: null, minuten: null },   // Orderflow (Delta) aus den Abschlüssen des Kerns
    bars: [], times: [], timeIndex: new Map(), source: '', kind: 'crypto',
    tool: 'cross', trendStart: null, trends: [], hlines: [], alarms: [],
    lastPrice: null, prevTickPrice: null, watch: {}, ticks: 0, lastMsg: 0,
    script: { text: '', outputs: [], series: [], lines: [], notified: new Set(), bt: null },
    ws: null, wsWatch: null, wsBook: null, sim: null, loadToken: 0, es: null,
    replay: { on: false, full: null, pos: 0, sub: 0, timer: null, playing: false },
    // Bitcoin-Dominanz: Historie (Server-Proxy oder Schnappschuss) + Live-Punkt, zusammengeführt in points
    dom: { hist: null, live: null, liveUsed: false, points: [], source: '', loading: null, lastLive: 0 },
  };
  // Indikatoren beim Start: ?ind=ema:9,ema:21,bb,rsi (Werte in der Reihenfolge der Einstellungen), sonst gespeichert, sonst Standard
  (() => {
    let liste = null;
    if (params.has('ind')) liste = params.get('ind').split(',').filter(Boolean).map((t) => K.ausText(t)).filter(Boolean);
    else {
      const s = store.get('inds');
      if (Array.isArray(s)) liste = s.filter((x) => K.NACH_TYP[x?.typ]).map((x) => ({ typ: x.typ, p: K.params(x.typ, x.p || {}), farbe: x.farbe }));
    }
    if (!liste) liste = String(store.get('ind') ?? 'ema,bb,rsi,macd').split(',').map((t) => K.ausText(t)).filter(Boolean);
    state.inds = liste.slice(0, 24).map((x) => ({ uid: state.nextUid++, typ: x.typ, p: x.p, farbe: typeof x.farbe === 'string' && /^#[0-9a-f]{6}$/i.test(x.farbe) ? x.farbe : K.NACH_TYP[x.typ].farbe }));
    IND.forEach((d) => (state.ind[d.id] = state.inds.some((x) => x.typ === d.id)));
  })();

  // ---------------- Hilfen ----------------
  const isNum = I.isNum;
  const precisionFor = (p) => (p >= 1 ? 2 : p >= 0.01 ? 4 : 6);
  const fmt = (v, d) => (isNum(v) ? v.toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–');
  const fmtP = (v) => fmt(v, precisionFor(Math.abs(state.lastPrice || v || 1)));
  const fmtVol = (v) => (v >= 1e9 ? fmt(v / 1e9, 2) + ' Mrd' : v >= 1e6 ? fmt(v / 1e6, 2) + ' Mio' : v >= 1e3 ? fmt(v / 1e3, 1) + ' Tsd' : fmt(v, 2));
  // Lightweight Charts zeigt UTC – wir verschieben die Anzeigezeit in die lokale Zeitzone
  const dispTime = (t) => t - new Date(t * 1000).getTimezoneOffset() * 60;
  const tfSec = () => TFS.find((t) => t.id === state.tf).sec;
  const isStock = (s) => STOCKS.includes(s);
  function fetchTimeout(url, ms) {
    const ctl = new AbortController(); const id = setTimeout(() => ctl.abort(), ms);
    return fetch(url, { signal: ctl.signal }).finally(() => clearTimeout(id));
  }
  function toast(title, text, kind = '') {
    const el = document.createElement('div'); el.className = 'toast ' + kind;
    el.innerHTML = `<b></b><span></span>`; el.querySelector('b').textContent = title; el.querySelector('span').textContent = text;
    $('#toasts').prepend(el); setTimeout(() => el.remove(), 7000);
  }
  let audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.18, 0.36].forEach((d, k) => {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.frequency.value = k === 2 ? 1320 : 880; o.type = 'sine';
        g.gain.setValueAtTime(0.0001, audioCtx.currentTime + d); g.gain.exponentialRampToValueAtTime(0.2, audioCtx.currentTime + d + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + d + 0.15);
        o.connect(g).connect(audioCtx.destination); o.start(audioCtx.currentTime + d); o.stop(audioCtx.currentTime + d + 0.16);
      });
    } catch (e) { /* kein Ton möglich */ }
  }

  // ---------------- Charts / Panes ----------------
  function baseOptions() {
    return {
      autoSize: true,
      layout: { attributionLogo: false, background: { type: 'solid', color: C.bg }, textColor: C.text, fontSize: 11, fontFamily: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
      grid: { vertLines: { color: C.grid, style: LW.LineStyle.Dotted }, horzLines: { color: C.grid, style: LW.LineStyle.Dotted } },
      crosshair: {
        mode: LW.CrosshairMode.Normal,
        vertLine: { color: 'rgba(245,185,66,0.55)', width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: '#3a3324' },
        horzLine: { color: 'rgba(245,185,66,0.55)', width: 1, style: LW.LineStyle.Dashed, labelBackgroundColor: '#3a3324' },
      },
      rightPriceScale: { borderColor: C.line, minimumWidth: 84 },
      timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 7 },
      localization: { locale: 'de-DE' },
    };
  }
  function makePane(id, cls, before) {
    const el = document.createElement('div'); el.className = 'pane ' + cls; el.id = 'pane-' + id;
    el.innerHTML = '<div class="chart"></div><div class="legend"></div>';
    $('#panes').insertBefore(el, before || null);
    const chart = LW.createChart(el.querySelector('.chart'), baseOptions());
    return { id, el, chart, legend: el.querySelector('.legend') };
  }
  const panes = { main: makePane('main', 'main'), script: makePane('script', 'sub') };   // Indikator-Bereiche kommen je Exemplar dazu (viewErstellen)
  const main = panes.main.chart;
  main.applyOptions({ rightPriceScale: { scaleMargins: { top: 0.12, bottom: 0.22 } } });

  const candle = main.addCandlestickSeries({ upColor: C.up, downColor: C.down, borderUpColor: C.up, borderDownColor: C.down, wickUpColor: C.up, wickDownColor: C.down });
  const volume = main.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
  main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const lineOpts = (color, w = 2, extra = {}) => ({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...extra });
  const S = {
    scriptBase: panes.script.chart.addLineSeries(lineOpts('rgba(0,0,0,0)', 1)), // Platzhalter für gleiche Zeitachse
  };
  // Serie, an der das Fadenkreuz eines Bereichs hängt
  const paneSeries = (p) => (p.id === 'main' ? candle : p.id === 'script' ? (state.script.series.find((x) => x.chart === panes.script.chart)?.s || S.scriptBase) : p.mainSeries);

  function layoutPanes() {
    panes.script.el.style.display = state.script.outputs.some((o) => o.type === 'panel') ? '' : 'none';
    // Reihenfolge wie im Bild: nur der unterste sichtbare Bereich zeigt die Zeitachse
    const byEl = new Map(Object.values(panes).map((p) => [p.el, p]));
    const visible = [...$('#panes').children].map((el) => byEl.get(el)).filter((p) => p && p.el.style.display !== 'none');
    visible.forEach((p, k) => p.chart.applyOptions({ timeScale: { visible: k === visible.length - 1 } }));
    // Nach dem Ein-/Ausblenden alle Panes auf den Bereich des Hauptcharts setzen
    layoutLock = Date.now();
    const r = main.timeScale().getVisibleLogicalRange();
    requestAnimationFrame(() => { const rr = main.timeScale().getVisibleLogicalRange() || r; if (rr) visible.forEach((p) => p !== panes.main && p.chart.timeScale().setVisibleLogicalRange(rr)); });
  }
  const isVisible = (p) => p.el.style.display !== 'none';

  // Zeitachsen aller Panes koppeln (gleiche logische Indizes dank Platzhalter-Punkten)
  let syncingRange = false, syncingCross = false, layoutLock = 0;
  function bindPane(p) {
    p.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (syncingRange || !r || !isVisible(p)) return;
      if (p !== panes.main && Date.now() - layoutLock < 400) return; // Größenänderung, keine Nutzeraktion
      syncingRange = true;
      Object.values(panes).forEach((o) => { if (o !== p && isVisible(o)) o.chart.timeScale().setVisibleLogicalRange(r); });
      syncingRange = false;
    });
    p.chart.subscribeCrosshairMove((param) => {
      if (syncingCross) return; syncingCross = true;
      const idx = param.time !== undefined ? state.timeIndex.get(param.time) : undefined;
      Object.values(panes).forEach((o) => {
        if (o === p || !isVisible(o)) return;
        try {
          const d = paneValueAt(o.id, idx);
          if (idx === undefined || !isNum(d)) o.chart.clearCrosshairPosition();
          else o.chart.setCrosshairPosition(d, param.time, paneSeries(o));
        } catch (e) { /* Pane ohne Werte an dieser Stelle */ }
      });
      syncingCross = false;
      updateLegends(idx);
      if (p.id === 'main') onMainMove(param);
    });
  }
  Object.values(panes).forEach(bindPane);

  // ---------------- Daten laden ----------------
  function setBars(rows) {
    state.bars = rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5], kauf: r[6] }));   // kauf: Taker-Kaufvolumen (nur Binance)
    rebuildTimes();
  }
  function rebuildTimes() {
    state.times = state.bars.map((b) => dispTime(b.time));
    state.timeIndex = new Map(state.times.map((t, i) => [t, i]));
  }
  function aggregate(rows, n) { // 1h -> 4h für Aktien: je Handelstag Blöcke zu n Kerzen
    const out = []; let cur = null, day = null, cnt = 0;
    for (const r of rows) {
      const d = Math.floor(r[0] / 86400);
      if (!cur || d !== day || cnt === n) { if (cur) out.push(cur); cur = [...r]; day = d; cnt = 1; continue; }
      cur[2] = Math.max(cur[2], r[2]); cur[3] = Math.min(cur[3], r[3]); cur[4] = r[4]; cur[5] += r[5]; cnt++;
    }
    if (cur) out.push(cur); return out;
  }
  async function load() {
    const token = ++state.loadToken;
    if (state.replay.on) replayStop(false);
    stopFeeds();
    const sym = state.symbol, tf = state.tf;
    state.kind = isStock(sym) ? 'stock' : 'crypto';
    let rows = null, source = '';
    if (state.kind === 'stock') {
      const st = SNAP.aktien[sym] || {};
      if (tf === '1m' && !st['1m']) { state.tf = '5m'; return load(); }   // Marktdaten: 1m, wenn vorhanden
      rows = tf === '4h' ? aggregate(st['1h'] || [], 4) : st[tf];
      // Yahoo liefert für die laufende Stunde/den laufenden Tag oft eine leere Kerze (Volumen 0, Hoch = Tief) – weglassen
      if (rows) rows = rows.filter((r) => !(r[5] === 0 && r[2] === r[3]));
      source = `${st.quelle || 'Schnappschuss'} · Stand ${SNAP.erstellt || ''}`;   // Marktdaten: Quelle nennen
    } else {
      if (!state.test && KRYPTO_ECHT) {   // Marktdaten: erfundene Demo ohne echte Kurse von Binance
        try {
          const r = await fetchTimeout(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=${tf}&limit=1000`, 7000);
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const k = await r.json();
          rows = k.map((x) => [x[0] / 1000, +x[1], +x[2], +x[3], +x[4], +x[5], +x[9]]);   // x[9] = Taker-Kaufvolumen (Aggressor = Käufer)
          source = 'Binance live';
        } catch (e) { console.warn('REST nicht erreichbar, nutze Schnappschuss', e); }
      }
      if (!rows) {
        rows = SNAP.krypto[sym]?.[tf]; source = state.test ? `Testmodus · Schnappschuss ${SNAP.erstellt || ''}` : `Offline-Fallback · Schnappschuss ${SNAP.erstellt || ''}`;
      }
    }
    if (token !== state.loadToken) return;
    if (!rows || !rows.length) { toast('Keine Daten', `${sym} ${tf} ist weder live noch im Schnappschuss verfügbar.`); return; }
    state.source = source;
    setBars(rows);
    clearTrends();
    renderAll(true);
    renderToolbarState();
    startFeeds();
    orderflowLaden(true);
  }

  // ---------------- Darstellung ----------------
  const toLine = (arr) => arr.map((v, i) => (isNum(v) ? { time: state.times[i], value: v } : { time: state.times[i] }));
  let calc = {};
  function compute() {
    const close = state.bars.map((b) => b.close);
    calc = {
      sma: I.sma(close, 50), ema: I.ema(close, 21), bb: I.bollinger(close, 20, 2),
      vwap: I.vwap(state.bars, state.tf === '1d' ? (t) => { const d = new Date(t * 1000); return d.getUTCFullYear() * 12 + d.getUTCMonth(); } : undefined),
      rsi: I.rsi(close, 14), macd: I.macd(close, 12, 26, 9), st: I.supertrend(state.bars, 10, 3),
      btcd: I.alignSeries(state.dom.points, state.bars.map((b) => b.time), tfSec()),
      inst: calc.inst,
    };
  }
  const histColor = (h, prev) => (h >= 0 ? (isNum(prev) && h < prev ? 'rgba(61,220,151,0.45)' : 'rgba(61,220,151,0.9)') : (isNum(prev) && h > prev ? 'rgba(255,107,122,0.45)' : 'rgba(255,107,122,0.9)'));
  const volColor = (b) => (b.close >= b.open ? 'rgba(61,220,151,0.32)' : 'rgba(255,107,122,0.32)');

  function renderAll(fit) {
    const p = precisionFor(state.bars[state.bars.length - 1].close);
    const pf = { type: 'price', precision: p, minMove: 1 / 10 ** p };
    candle.applyOptions({ priceFormat: pf });
    views.forEach((v, uid) => { if (v.pane === panes.main) { const inst = state.inds.find((x) => x.uid === uid); if (inst && !defOf(inst).volumenFormat) v.reihen.forEach(({ s }) => s.applyOptions({ priceFormat: pf })); } });
    candle.setData(state.bars.map((b, i) => ({ time: state.times[i], open: b.open, high: b.high, low: b.low, close: b.close })));
    volume.setData(state.bars.map((b, i) => ({ time: state.times[i], value: b.volume, color: volColor(b) })));
    S.scriptBase.setData(state.times.map((t) => ({ time: t })));
    compute();
    renderIndikatoren(true);
    applyIndicatorVisibility();
    runScript(false);
    layoutPanes();
    if (fit) {
      const n = state.bars.length;
      main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 6 });
      // Preisachsen wieder automatisch skalieren: nach Ziehen/Zoomen der Achse bleibt sonst der alte Bereich stehen
      Object.values(panes).forEach((p) => p.chart.priceScale('right').applyOptions({ autoScale: true }));
    }
    state.lastPrice = state.bars[state.bars.length - 1].close;
    updateHeader(false); updateLegends(); updateStatus();
  }
  function applyIndicatorVisibility() {
    if (hasTyp('btcd')) ensureDominance();
  }
  // Live: nur die letzte Kerze neu zeichnen (schnell), Indikatoren komplett neu rechnen (500–1000 Werte sind billig)
  function renderLast() {
    const i = state.bars.length - 1, b = state.bars[i], t = state.times[i];
    candle.update({ time: t, open: b.open, high: b.high, low: b.low, close: b.close });
    volume.update({ time: t, value: b.volume, color: volColor(b) });
    S.scriptBase.update({ time: t });
    compute();
    if (state.bars.length !== lastN) { renderIndikatoren(true); updateLegends(hoverIdx); } else renderIndikatorenBald();
    scheduleScript();
  }

  function paneValueAt(id, idx) {
    if (id === 'main') return state.bars[idx]?.close;
    const p = panes[id];
    if (p?.inst) { const res = calc.inst?.[p.inst.uid] || {}, l = views.get(p.inst.uid)?.reihen.find((x) => x.s === p.mainSeries)?.l; return l ? res[l.r]?.[idx] : null; }
    const o = state.script.outputs.find((x) => x.type === 'panel'); return o ? o.values[idx] : null;
  }

  // Legende oben links (Fadenkreuz-Werte)
  let hoverIdx;
  function updateLegends(idx) {
    hoverIdx = idx;
    const n = state.bars.length; if (!n) return;
    const i = idx === undefined ? n - 1 : idx; const b = state.bars[i]; if (!b) return;
    const d = precisionFor(b.close);
    const ch = b.close - b.open, chp = (ch / b.open) * 100, cls = ch >= 0 ? 'pos' : 'neg';
    const tf = TFS.find((t) => t.id === state.tf).label;
    const v = (x) => fmt(x, d);
    let html = `<div class="title">${state.symbol} · ${tf}<span class="src">${state.source}</span></div>
      <div class="row"><span class="k">E</span><span class="v ${cls}">${v(b.open)}</span><span class="k">H</span><span class="v ${cls}">${v(b.high)}</span><span class="k">T</span><span class="v ${cls}">${v(b.low)}</span><span class="k">S</span><span class="v ${cls}">${v(b.close)}</span><span class="v ${cls}">${ch >= 0 ? '+' : ''}${v(ch)} (${ch >= 0 ? '+' : ''}${fmt(chp, 2)} %)</span><span class="k">Vol</span><span class="v">${fmtVol(b.volume)}</span></div>`;
    const ind = (name, color, val) => `<span class="ind"><b style="color:${color}">${name}</b> <span style="color:${color}">${val}</span></span>`;
    state.inds.forEach((inst) => {
      const lg = legendeInd(inst, i, d);
      if (defOf(inst).ort === 'chart') html += lg; else { const v2 = views.get(inst.uid); if (v2) v2.pane.legend.innerHTML = lg; }
    });
    state.script.outputs.filter((o) => o.type === 'plot').forEach((o) => { html += ind('ƒ ' + esc(o.title), o.color, v(o.values[i])); });
    panes.main.legend.innerHTML = html;
    panes.script.legend.innerHTML = state.script.outputs.filter((o) => o.type === 'panel').map((o) => ind('ƒ ' + esc(o.title), o.color, fmt(o.values[i], 2))).join('');
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------------- Indikatoren (Verzeichnis: indikator-katalog.js, Formeln: indicators.js, Flächen/Profil: indikator-zeichnen.js) ----------------
  // Jeder Indikator ist ein Exemplar { uid, typ, p (Einstellungen), farbe }; derselbe Typ darf mehrfach vorkommen (EMA 9, 21, 200).
  const Z = window.IndikatorZeichnen;
  const views = new Map();            // uid -> { pane, reihen: [{ l, s }], flaechen: [{ f, prim }], profil }
  const defOf = (inst) => K.NACH_TYP[inst.typ];
  const hasTyp = (typ) => state.inds.some((x) => x.typ === typ);
  const NACHFARBEN = ['#ff9f43', '#4fa3ff', '#ffd166', '#b388ff', '#36c5b0', '#ff6bcb', '#7cd4ff', '#8fd16a', '#f4a6ff'];
  function indSpeichern() {
    store.set('inds', state.inds.map(({ typ, p, farbe }) => ({ typ, p, farbe })));
    IND.forEach((d) => (state.ind[d.id] = hasTyp(d.id)));   // Kompatibilität: alte Kurz-IDs an/aus
  }
  function farbeFuer(typ) {
    const d = K.NACH_TYP[typ], belegt = new Set(state.inds.filter((x) => x.typ === typ).map((x) => x.farbe));
    if (!belegt.size) return d.farbe;
    return NACHFARBEN.find((f) => !belegt.has(f)) || d.farbe;
  }
  function indCtx() {
    const sn = SNAP.aktien[state.symbol];
    const markt = state.kind === 'crypto' ? 'KRYPTO' : sn?.waehrung === 'EUR' ? 'DE' : 'US';
    return {
      tf: state.tf, tfSec: tfSec(), kind: state.kind, markt, of: state.of.sym === state.symbol ? state.of : null, vergleich,
      dom: { points: state.dom.points, text: state.dom.source ? state.dom.source + (state.dom.liveUsed ? ' · letzter Punkt live (CoinGecko)' : '') : (state.dom.loading ? 'lädt …' : 'keine Daten') },
    };
  }
  // Vergleichswert für die Relative Stärke: gleichgewichteter Schnitt der übrigen Watchlist-Aktien desselben Markts oder ein Symbol
  function vergleichsReihe(sym) {
    const st = SNAP.aktien[sym]; if (!st) return null;
    let rows = state.tf === '4h' ? aggregate(st['1h'] || [], 4) : st[state.tf];
    if (!rows || !rows.length) return null;
    const m = new Map(rows.map((r) => [r[0], r[4]]));
    let last = null, k = 0;
    const sorted = rows.map((r) => r[0]);
    return state.bars.map((b) => {   // Schluss zur selben Zeit, sonst der letzte davor
      while (k < sorted.length && sorted[k] <= b.time) { last = m.get(sorted[k]); k++; }
      return last;
    });
  }
  function vergleich(gegen) {
    if (gegen !== 'watchlist') { const r = vergleichsReihe(gegen); return r ? { werte: r, text: `gegen ${gegen} (${SNAP.aktien[gegen]?.name || gegen})` } : null; }
    const w = SNAP.aktien[state.symbol]?.waehrung;
    const syms = STOCKS.filter((s) => s !== state.symbol && SNAP.aktien[s]?.waehrung === w);
    const reihen = syms.map((s) => [s, vergleichsReihe(s)]).filter(([, r]) => r && r.some(isNum));
    if (!reihen.length) return null;
    const start = state.bars.findIndex((_, i) => reihen.every(([, r]) => isNum(r[i])));
    if (start < 0) return null;
    const werte = state.bars.map((_, i) => (i < start ? null : reihen.reduce((s, [, r]) => s + r[i] / r[start], 0) / reihen.length));
    return { werte, text: `gegen Ø ${reihen.length} Werte der Watchlist (${reihen.map(([s]) => s).join(', ')})` };
  }

  // Zeit eines Index; jenseits der letzten Kerze (Ichimoku-Wolke) in Kerzenabständen fortgeschrieben
  const zeitAn = (i) => (i < state.times.length ? state.times[i] : dispTime(state.bars[state.bars.length - 1].time + (i - state.bars.length + 1) * tfSec()));
  function histoFarbe(art, arr, i) {
    const h = arr[i], prev = arr[i - 1];
    if (art === 'macd' || art === 'schwung') return histColor(h, prev);
    return h >= 0 ? 'rgba(47,207,122,0.75)' : 'rgba(240,84,84,0.75)';
  }
  function punktFuer(l, res, i) {
    const arr = res[l.r], t = zeitAn(i), val = arr ? arr[i] : null;
    if (!isNum(val)) return { time: t };
    const p = { time: t, value: val };
    if (l.histo) p.color = histoFarbe(l.histo, arr, i);
    else if (l.farbeJe === 'dir') p.color = res.dir?.[i] === -1 ? C.down : C.up;
    else if (l.farbeJe === 'squeeze') p.color = res.on?.[i] === 1 ? C.down : 'rgba(47,207,122,0.6)';
    return p;
  }
  function setReihe(s, l, res, voll) {
    const arr = res[l.r];
    if (l.zickzack) {
      s.setData((arr || []).filter((p) => p.i < state.times.length).map((p) => ({ time: state.times[p.i], value: p.price })));
      return;
    }
    if (!Array.isArray(arr)) { if (s.__hatte !== false) { s.setData([]); s.__hatte = false; } return; }
    if (voll || l.zukunft || l.voll || !s.__hatte) { s.setData(arr.map((_, i) => punktFuer(l, res, i))); s.__hatte = true; return; }
    s.update(punktFuer(l, res, state.bars.length - 1));
  }
  function formatFuer(d, inst) {
    if (d.volumenFormat) return { type: 'volume' };
    if (d.einheit) return { type: 'custom', minMove: 0.01, formatter: (v) => fmt(v, d.nachkomma ?? 2) + d.einheit };
    if (d.nachkomma !== undefined) return { type: 'price', precision: d.nachkomma, minMove: 1 / 10 ** d.nachkomma };
    const p = precisionFor(state.bars[state.bars.length - 1]?.close || 1);   // preisnah (MACD, ATR, Schwung): wie der Kurs
    return { type: 'price', precision: p, minMove: 1 / 10 ** p };
  }
  function viewErstellen(inst) {
    const d = defOf(inst);
    let pane = panes.main;
    if (d.ort === 'panel') {
      pane = makePane('i' + inst.uid, 'sub', panes.script.el); pane.inst = inst; panes[pane.id] = pane; bindPane(pane);
      if (state.times.length) pane.chart.timeScale().setVisibleLogicalRange(main.timeScale().getVisibleLogicalRange() || { from: 0, to: state.times.length });
    }
    const ch = pane.chart, pf = formatFuer(d, inst);
    const reihen = d.linien(inst.farbe, inst.p).filter((l) => !l.nurLegende).map((l) => {
      const c = l.farbe || inst.farbe, col = l.alpha ? Z.rgba(c, l.alpha) : c;
      let s;
      if (l.histo) s = ch.addHistogramSeries({ priceLineVisible: false, lastValueVisible: !!l.preis, priceFormat: pf });
      else {
        const o = lineOpts(col, l.breite ?? 2, {
          lastValueVisible: d.ort === 'panel' && !!l.preis, priceFormat: pf,
          lineStyle: l.gestrichelt ? LW.LineStyle.Dashed : l.gepunktet ? LW.LineStyle.Dotted : LW.LineStyle.Solid,
        });
        if (l.stufe) o.lineType = LW.LineType.WithSteps;
        if (l.punkte) Object.assign(o, { lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 1.7 });
        if (l.eigeneSkala) o.priceScaleId = 'eigen';
        if (l.zickzack) o.crosshairMarkerVisible = false;
        s = ch.addLineSeries(o);
      }
      return { l, s };
    });
    const sk = typeof d.skala === 'function' ? d.skala(inst.p) : d.skala;
    if (d.ort === 'panel' && reihen.length) {
      const s0 = reihen.find((x) => x.l.preis)?.s || reihen[0].s;
      pane.mainSeries = s0;
      if (sk && isNum(sk.min) && isNum(sk.max)) s0.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: sk.min, maxValue: sk.max } }) });
      (sk?.niveaus || []).forEach((v) => s0.createPriceLine({ price: v, color: 'rgba(122,133,151,0.4)', lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false }));
      if (sk?.nulllinie) s0.createPriceLine({ price: 0, color: 'rgba(122,133,151,0.35)', lineWidth: 1, lineStyle: LW.LineStyle.Solid, axisLabelVisible: false });
      s0.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.1 } });
      if (reihen.some((x) => x.l.eigeneSkala)) ch.priceScale('eigen').applyOptions({ scaleMargins: { top: 0.12, bottom: 0.1 } });
    }
    const flaechen = (d.flaeche || []).map((f) => {
      const host = reihen.find((x) => x.l.r === f.oben)?.s || reihen[0]?.s;
      const prim = new Z.Flaeche({ farbe: f.farbe || inst.farbe, alpha: f.alpha, farbeAuf: f.farbeAuf, farbeAb: f.farbeAb });
      host.attachPrimitive(prim);
      return { f, prim };
    });
    let profil = null;
    if (d.profil) { profil = new Z.Profil({ up: C.up, down: C.down, fmt: (v) => fmtP(v) }); candle.attachPrimitive(profil); }
    const v = { pane, reihen, flaechen, profil };
    views.set(inst.uid, v);
    return v;
  }
  function viewEntfernen(uid) {
    const v = views.get(uid); if (!v) return;
    if (v.profil) { try { candle.detachPrimitive(v.profil); } catch (e) { /* schon weg */ } }
    if (v.pane === panes.main) v.reihen.forEach(({ s }) => { try { main.removeSeries(s); } catch (e) { /* schon weg */ } });
    else { delete panes[v.pane.id]; v.pane.chart.remove(); v.pane.el.remove(); }
    views.delete(uid);
  }
  let lastN = 0, indQueued = false;
  function renderIndikatoren(voll) {
    const N = state.bars.length; if (!N) return;
    if (N !== lastN) voll = true;
    lastN = N;
    const ctx = indCtx();
    calc.inst = {};
    for (const inst of state.inds) {
      const d = defOf(inst), v = views.get(inst.uid) || viewErstellen(inst);
      let res;
      try { res = d.rechne(state.bars, inst.p, ctx) || {}; } catch (e) { console.warn(e); res = { fehlt: 'Rechenfehler: ' + e.message }; }
      const arrs = d.linien(inst.farbe, inst.p).map((l) => res[l.r]).filter((a) => Array.isArray(a) && !a[0]?.price);
      if (!res.fehlt && arrs.length && !arrs.some((a) => a.some(isNum))) res.fehlt = `zu wenig Kerzen (${N}) für diese Einstellung`;
      calc.inst[inst.uid] = res;
      v.reihen.forEach(({ l, s }) => setReihe(s, l, res, voll));
      v.flaechen.forEach(({ f, prim }) => prim.setData(res[f.oben], res[f.unten]));
      if (v.profil && voll) profilPlanen(inst, 0);
    }
  }
  // Live: viele Ticks je Sekunde – Indikatoren höchstens einmal je Bild neu rechnen
  function renderIndikatorenBald() {
    if (indQueued) return; indQueued = true;
    requestAnimationFrame(() => { indQueued = false; renderIndikatoren(false); updateLegends(hoverIdx); });
  }

  // ---------------- Orderflow aus echten Abschlüssen (Aktien, Kern: /ic-charts/data/orderflow) ----------------
  async function orderflowLaden(force) {
    if (state.kind !== 'stock' || state.test || !location.protocol.startsWith('http')) return;
    if (!state.inds.some((x) => x.typ === 'delta')) return;
    const sym = state.symbol;
    if (!force && state.of.sym === sym && (state.of.laden || Date.now() - state.of.t < 20000)) return;
    if (state.of.sym !== sym) state.of = { sym, minuten: null };
    Object.assign(state.of, { laden: true, t: Date.now() });
    try {
      const r = await fetchTimeout(`data/orderflow?sym=${encodeURIComponent(sym)}`, 15000);
      if (!r.ok) throw new Error(r.status === 404 ? 'für diesen Wert nicht verfügbar' : 'HTTP ' + r.status);
      const d = await r.json();
      if (state.symbol !== sym) return;
      Object.assign(state.of, { minuten: d.minuten?.length ? d.minuten : null, hinweis: d.hinweis, quelle: d.quelle, fehler: d.minuten?.length ? '' : d.hinweis, laden: false });
    } catch (e) { Object.assign(state.of, { laden: false, fehler: 'Abschlüsse ' + e.message }); }
    if (state.symbol === sym) { renderIndikatoren(true); updateLegends(hoverIdx); syncPanes(); }
  }
  // Nach nachgeladenen Daten: Bereiche wieder auf den Ausschnitt des Kurscharts setzen (ein leerer Bereich springt sonst ans Ende)
  function syncPanes() {
    requestAnimationFrame(() => {
      const r = main.timeScale().getVisibleLogicalRange(); if (!r) return;
      layoutLock = Date.now();
      Object.values(panes).forEach((p) => { if (p !== panes.main && isVisible(p)) p.chart.timeScale().setVisibleLogicalRange(r); });
    });
  }
  const profilTimer = new Map();
  function profilPlanen(inst, ms = 350) { clearTimeout(profilTimer.get(inst.uid)); profilTimer.set(inst.uid, setTimeout(() => profilRechnen(inst), ms)); }
  async function profilRechnen(inst) {
    const v = views.get(inst.uid), N = state.bars.length; if (!v || !N) return;
    const p = inst.p; let from, to = N - 1;
    if (p.bereich === 'tag') { const k = I.periodKey(state.bars[N - 1].time, 'tag'); from = N - 1; while (from > 0 && I.periodKey(state.bars[from - 1].time, 'tag') === k) from--; }
    else { const r = main.timeScale().getVisibleLogicalRange(); from = r ? Math.max(0, Math.ceil(r.from)) : 0; to = r ? Math.min(N - 1, Math.floor(r.to)) : N - 1; }
    let d = null, titel = '';
    if (to > from && state.kind === 'stock' && p.daten === 'auto' && !state.test && location.protocol.startsWith('http')) {
      const sym = state.symbol, von = state.bars[from].time, bis = state.bars[to].time + tfSec();
      try {
        const r = await fetchTimeout(`data/orderflow?sym=${encodeURIComponent(sym)}&profil=1&von=${von}&bis=${bis}&stufen=${p.stufen}`, 15000);
        const j = r.ok ? await r.json() : null;
        if (state.symbol !== sym || !views.get(inst.uid)) return;
        if (j?.profil?.levels?.length) {
          const s = j.profil.schritt;
          d = { schritt: s, levels: j.profil.levels.map(([unten, kauf, verkauf, neutral]) => ({ unten, preis: unten + s / 2, vol: kauf + verkauf + neutral, kauf, verkauf })) };
          titel = `${j.profil.anzahl.toLocaleString('de-DE')} Abschlüsse ${j.quelle}${j.quelle === 'IEX' ? ' (Stichprobe)' : ''}`;
        }
      } catch (e) { /* Rückfall: aus Kerzen */ }
    }
    if (!d && to > from) {
      const kauf = state.bars.some((b) => isNum(b.kauf)) ? state.bars.map((b) => b.kauf) : null;
      d = I.volumeProfile(state.bars, from, to, p.stufen, kauf);
      titel = kauf ? 'aus Kerzen geschätzt · Taker-Volumen' : 'aus Kerzen geschätzt';
    }
    if (d) { Object.assign(d, I.valueArea(d.levels, p.anteil / 100)); d.titel = `Volumenprofil ${p.bereich === 'tag' ? 'letzte Sitzung' : 'sichtbar'} · ${titel}`; }
    v.profil.setData(d); v.profilInfo = d; updateLegends(hoverIdx);
  }
  main.timeScale().subscribeVisibleLogicalRangeChange(() => state.inds.forEach((x) => { if (x.typ === 'vprofil' && x.p.bereich === 'sichtbar') profilPlanen(x); }));

  // ---------------- Indikatoren hinzufügen, ändern, entfernen ----------------
  function nachIndAenderung() {
    indSpeichern(); renderIndikatoren(true); layoutPanes(); updateLegends(hoverIdx); renderToolbarState();
    if (hasTyp('btcd')) ensureDominance();
    orderflowLaden();
  }
  const okFarbe = (f) => (typeof f === 'string' && /^#[0-9a-f]{6}$/i.test(f) ? f : null);
  function addIndicator(typ, p = {}, farbe) {
    if (!K.NACH_TYP[typ]) throw new Error('Indikator ' + typ + ' unbekannt');
    if (state.inds.length >= 24) throw new Error('Höchstens 24 Indikatoren gleichzeitig');
    const inst = { uid: state.nextUid++, typ, p: K.params(typ, p), farbe: okFarbe(farbe) || farbeFuer(typ) };
    state.inds.push(inst); nachIndAenderung();
    return inst;
  }
  function updateIndicator(uid, p, farbe) {
    const inst = state.inds.find((x) => x.uid === uid); if (!inst) throw new Error('Indikator ' + uid + ' nicht vorhanden');
    if (p) inst.p = K.params(inst.typ, { ...inst.p, ...p });
    if (okFarbe(farbe)) inst.farbe = farbe;
    viewEntfernen(uid); nachIndAenderung();
    return inst;
  }
  function removeIndicator(uid) {
    const k = state.inds.findIndex((x) => x.uid === uid); if (k < 0) return false;
    viewEntfernen(uid); state.inds.splice(k, 1); nachIndAenderung(); schliessePop();
    return true;
  }
  // Alte Schnittstelle (Knöpfe bis Oktober 2026, agent-panel.js): an = Standard-Exemplar anlegen, aus = alle dieses Typs entfernen
  function setIndicator(id, on) {
    if (!K.NACH_TYP[id]) throw new Error('Indikator ' + id + ' unbekannt');
    if (on && !hasTyp(id)) addIndicator(id);
    if (!on) state.inds.filter((x) => x.typ === id).forEach((x) => removeIndicator(x.uid));
  }
  function indikatorListe() {
    const N = state.bars.length;
    return state.inds.map((inst) => {
      const d = defOf(inst), res = calc.inst?.[inst.uid] || {}, werte = {};
      const ls = d.linien(inst.farbe, inst.p).filter((l) => Array.isArray(res[l.r]) && !l.zickzack && !l.ohneLegende);
      // letzte Kerze mit Werten (Volumen-Indikatoren blenden bei US-Aktien die letzten 15 Min. aus)
      let n = N; if (ls.length) while (n > 1 && !ls.some((l) => isNum(res[l.r][n - 1]))) n--;
      ls.forEach((l) => { const a = res[l.r]; werte[l.titel || l.r] = isNum(a[n - 1]) ? +a[n - 1].toPrecision(8) : null; });
      if (n < N && ls.some((l) => isNum(res[l.r][n - 1]))) werte.stand = `${N - n} Kerze${N - n === 1 ? '' : 'n'} vor der letzten`;
      if (res.dir) werte.richtung = res.dir[n - 1] === 1 ? 'aufwärts' : res.dir[n - 1] === -1 ? 'abwärts' : null;
      if (inst.typ === 'squeeze') werte.squeeze_an = res.on?.[n - 1] === 1;
      if (inst.typ === 'zigzag' && res.punkte?.length) werte.letzte_wendepunkte = res.punkte.slice(-4).map((x) => ({ typ: x.typ, preis: x.price, kerzen_zurueck: N - 1 - x.i, fest: x.fest }));
      if (inst.typ === 'vprofil' && views.get(inst.uid)?.profilInfo) { const pi = views.get(inst.uid).profilInfo; werte.poc = pi.levels[pi.poc]?.preis; werte.vah = pi.levels[pi.vah]?.preis; werte.val = pi.levels[pi.val]?.preis; werte.daten = pi.titel; }
      if (inst.typ === 'linreg' && isNum(res.steigungProz)) werte.steigung_prozent_je_kerze = +res.steigungProz.toFixed(4);
      return { id: inst.uid, type: inst.typ, name: d.kurz(inst.p), params: inst.p, ort: d.ort === 'chart' ? 'Kurschart' : 'eigener Bereich', werte, hinweis: res.fehlt || res.hinweis || undefined };
    });
  }

  // ---------------- Auswahl und Einstellungen ----------------
  const pop = document.createElement('div'); pop.id = 'indPop'; pop.hidden = true; document.body.appendChild(pop);
  let popUid = null;
  function schliessePop() { pop.hidden = true; popUid = null; }
  document.addEventListener('pointerdown', (e) => { if (!pop.hidden && !pop.contains(e.target) && !e.target.closest('#indGroup')) schliessePop(); });
  function feldHtml(x, wert, inst) {
    const id = 'ip-' + x.k;
    if (x.wahl) {
      let wahl = x.wahl;
      if (x.offen) wahl = [...x.wahl, ...STOCKS.filter((s) => s !== state.symbol && SNAP.aktien[s])];
      const opt = wahl.map((w) => `<option value="${esc(w)}"${String(w) === String(wert) ? ' selected' : ''}>${esc(x.namen?.[w] ?? (SNAP.aktien[w] ? `${w} · ${SNAP.aktien[w].name}` : w))}</option>`).join('');
      return `<label for="${id}">${esc(x.name)}<select id="${id}" data-k="${x.k}">${opt}</select></label>`;
    }
    return `<label for="${id}">${esc(x.name)}<input id="${id}" data-k="${x.k}" type="number" min="${x.min}" max="${x.max}" step="${x.step}" value="${wert}"></label>`;
  }
  function einstellungenOeffnen(inst, anker) {
    if (popUid === inst.uid && !pop.hidden) { schliessePop(); return; }
    const d = defOf(inst); popUid = inst.uid;
    const sicht = (x) => !x.nurBei || Object.entries(x.nurBei).every(([k, v]) => v.includes(inst.p[k]));   // Feld nur bei passender Einstellung
    pop.innerHTML = `<div class="ip-kopf"><i style="background:${inst.farbe}"></i><b>${esc(d.titel)}</b><button class="ip-zu" title="Schließen">✕</button></div>
      <div class="ip-felder">${d.params.filter(sicht).map((x) => feldHtml(x, inst.p[x.k], inst)).join('')}
        <label>Farbe<input type="color" data-farbe value="${inst.farbe.length === 7 ? inst.farbe : '#ffffff'}"></label></div>
      ${inst.typ === 'vwap' ? '<button class="ip-anker ghost">Startpunkt im Chart wählen</button>' : ''}
      <p class="ip-info">${esc(d.info)}</p><p class="ip-quelle">Formel: ${esc(d.herkunft)}${d.braucht === 'volumen' ? ' · braucht Volumen' : d.braucht === 'orderflow' ? ' · braucht Abschlüsse oder Taker-Volumen' : ''}</p>
      <div class="ip-fuss"><button class="ip-weg">Entfernen</button><span>entfernt nur diesen Indikator</span><button class="primary ip-ok">Fertig</button></div>`;
    const r = anker.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(window.innerWidth - 340, r.left)) + 'px'; pop.style.top = (r.bottom + 6) + 'px'; pop.hidden = false;
    let t = null;
    const lesen = () => {
      const roh = {}; pop.querySelectorAll('[data-k]').forEach((el) => (roh[el.dataset.k] = el.value));
      const farbe = pop.querySelector('[data-farbe]').value;
      const vorher = JSON.stringify(inst.p);
      updateIndicator(inst.uid, roh, farbe);
      const b = [...$('#indGroup').children].find((x) => +x.dataset.uid === inst.uid);
      if (b && JSON.stringify(inst.p) !== vorher && d.params.some((x) => x.nurBei)) { popUid = null; einstellungenOeffnen(inst, b); }
    };
    pop.querySelectorAll('input,select').forEach((el) => el.addEventListener('input', () => { clearTimeout(t); t = setTimeout(lesen, el.tagName === 'SELECT' ? 0 : 300); }));
    pop.querySelector('.ip-zu').onclick = schliessePop;
    pop.querySelector('.ip-ok').onclick = schliessePop;
    pop.querySelector('.ip-weg').onclick = () => removeIndicator(inst.uid);
    const ak = pop.querySelector('.ip-anker');
    if (ak) ak.onclick = () => { schliessePop(); state.ankerFuer = inst.uid; setTool('anker'); };
  }
  const dlg = document.createElement('dialog'); dlg.id = 'indDlg'; document.body.appendChild(dlg);
  function auswahlOeffnen() {
    schliessePop();
    dlg.innerHTML = `<div class="id-kopf"><h2>Indikatoren</h2><input id="idSuche" placeholder="Suchen, z. B. Trend, Volumen, Ichimoku" autocomplete="off"><button class="ghost id-zu">Schließen</button></div>
      <div class="id-liste"></div><p class="id-fuss">Alle Formeln sind eigener Code nach den Erstbeschreibungen, geprüft gegen TA-Lib. Mehrfach hinzufügen geht, z. B. EMA 9 und EMA 200.</p>`;
    const liste = dlg.querySelector('.id-liste');
    const zeichnen = (q) => {
      q = q.trim().toLowerCase();
      liste.innerHTML = K.GRUPPEN.map((g) => {
        const eintr = K.KATALOG.filter((d) => d.gruppe === g && (!q || `${d.name} ${d.titel} ${d.info} ${d.gruppe}`.toLowerCase().includes(q)));
        if (!eintr.length) return '';
        return `<section><h3>${esc(g)}</h3>${eintr.map((d) => {
          const n = state.inds.filter((x) => x.typ === d.typ).length;
          return `<button data-typ="${d.typ}"><i style="background:${d.farbe}"></i><span class="id-n"><b>${esc(d.name)}</b><small>${esc(d.info)}</small></span>
            <span class="id-tags"><em>${d.ort === 'chart' ? 'Kurschart' : 'eigener Bereich'}</em>${d.braucht === 'volumen' ? '<em>Volumen</em>' : ''}${d.braucht === 'orderflow' ? '<em>Abschlüsse</em>' : ''}${n ? `<em class="an">${n}× aktiv</em>` : ''}</span></button>`;
        }).join('')}</section>`;
      }).join('') || '<p class="muted">Nichts gefunden.</p>';
      liste.querySelectorAll('[data-typ]').forEach((b) => (b.onclick = () => {
        try { addIndicator(b.dataset.typ); dlg.close(); toast('Indikator hinzugefügt', K.NACH_TYP[b.dataset.typ].titel + ' · Klick auf den Namen oben öffnet die Einstellungen', 'signal'); }
        catch (e) { toast('Nicht möglich', e.message); }
      }));
    };
    zeichnen('');
    dlg.querySelector('#idSuche').addEventListener('input', (e) => zeichnen(e.target.value));
    dlg.querySelector('.id-zu').onclick = () => dlg.close();
    dlg.showModal(); dlg.querySelector('#idSuche').focus();
  }
  // Legende eines Indikators (Fadenkreuz-Werte)
  function legendeInd(inst, i, dp) {
    const d = defOf(inst), res = calc.inst?.[inst.uid] || {}, name = d.kurz(inst.p);
    const tag = (n, c, val) => `<span class="ind"><b style="color:${c}">${esc(n)}</b> <span style="color:${c}">${val}</span></span>`;
    if (res.fehlt) return tag(name, inst.farbe, `<span class="src">${esc(res.fehlt)}</span>`);
    const zahl = (x) => (d.volumenFormat ? (x < 0 ? '−' : '') + fmtVol(Math.abs(x)) : fmt(x, d.nachkomma ?? (d.ort === 'chart' ? dp : 2)) + (d.einheit || ''));
    let val;
    if (inst.typ === 'zigzag') { const pts = res.punkte || [], l = pts[pts.length - 1]; val = l ? `${l.typ === 'hoch' ? 'Hoch' : 'Tief'} ${fmt(l.price, dp)}${l.fest ? '' : ' (vorläufig)'}` : '–'; }
    else if (inst.typ === 'vprofil') { const pi = views.get(inst.uid)?.profilInfo; val = pi ? `POC ${fmt(pi.levels[pi.poc].preis, dp)} · VAH ${fmt(pi.levels[pi.vah].preis, dp)} · VAL ${fmt(pi.levels[pi.val].preis, dp)}` : '–'; }
    else {
      const ls = d.linien(inst.farbe, inst.p).filter((l) => !l.ohneLegende && Array.isArray(res[l.r]));
      val = ls.map((l) => {
        const x = res[l.r][i], c = l.histo ? (x >= 0 ? C.up : C.down) : l.farbeJe === 'dir' ? (res.dir?.[i] === -1 ? C.down : C.up) : (l.farbe || inst.farbe);
        const pfeil = l.farbeJe === 'dir' ? (res.dir?.[i] === 1 ? ' ▲' : res.dir?.[i] === -1 ? ' ▼' : '') : '';
        if (l.farbeJe === 'squeeze') return `<span style="color:${res.on?.[i] === 1 ? C.down : C.up}">${res.on?.[i] === 1 ? 'Squeeze an' : 'aus'}</span>`;
        return `<span style="color:${c}">${l.titel && ls.length > 1 ? esc(l.titel) + ' ' : ''}${isNum(x) ? zahl(x) : '–'}${pfeil}</span>`;
      }).join(' · ');
      if (inst.typ === 'linreg' && isNum(res.steigungProz)) val += ` · ${res.steigungProz >= 0 ? '+' : ''}${fmt(res.steigungProz, 3)} % je Kerze`;
    }
    return tag(name, inst.farbe, val) + (res.hinweis ? `<span class="src">${esc(res.hinweis)}</span>` : '');
  }


  function updateHeader(flash) {
    const el = $('#lastPrice'), p = state.lastPrice;
    el.textContent = fmt(p, precisionFor(p));
    if (flash && isNum(state.prevTickPrice) && p !== state.prevTickPrice) {
      el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth;
      el.classList.add(p > state.prevTickPrice ? 'flash-up' : 'flash-down');
      clearTimeout(updateHeader.t); updateHeader.t = setTimeout(() => el.classList.remove('flash-up', 'flash-down'), 260);
    }
    // Veränderung zum Vortag: live aus dem 24h-Ticker, sonst aus den Tageskerzen des Schnappschusses
    let ref = (state.test ? null : state.watch[state.symbol]?.open) ?? watchData(state.symbol)?.open;
    if (!isNum(ref)) { const n = state.bars.length; ref = state.bars[Math.max(0, n - 2)]?.close; }
    const ch = ((p - ref) / ref) * 100;
    const c = $('#lastChange'); c.textContent = `${ch >= 0 ? '+' : ''}${fmt(ch, 2)} %`; c.className = ch >= 0 ? 'pos' : 'neg';
    document.title = `${state.symbol} ${fmt(p, precisionFor(p))} · iC Charts`;
  }
  function setConn(kind, text) { const c = $('#conn'); c.className = 'conn ' + kind; c.querySelector('span').textContent = text; }
  function updateStatus() {
    $('#srcInfo').textContent = `Quelle: ${state.source} · ${state.bars.length} Kerzen`;
    const age = state.lastMsg ? ((Date.now() - state.lastMsg) / 1000).toFixed(1).replace('.', ',') : '–';
    $('#tickInfo').textContent = state.ticks ? `Ticks: ${state.ticks} · letzte Nachricht vor ${age} s` : '';
  }
  setInterval(updateStatus, 1000);

  // ---------------- Live-Kurse ----------------
  function onTick(t, o, h, l, c, v, kauf) { // t = Kerzen-Startzeit (Unix-Sekunden), kauf = Taker-Kaufvolumen (nur Binance)
    const bars = state.bars, last = bars[bars.length - 1];
    if (t < last.time) return;
    if (t === last.time) Object.assign(last, { open: o, high: h, low: l, close: c, volume: v, kauf });
    else { bars.push({ time: t, open: o, high: h, low: l, close: c, volume: v, kauf }); state.times.push(dispTime(t)); state.timeIndex.set(dispTime(t), bars.length - 1); }
    state.prevTickPrice = state.lastPrice; state.lastPrice = c; state.ticks++; state.lastMsg = Date.now();
    renderLast(); updateHeader(true); checkAlarms(state.prevTickPrice, c);
  }
  function startFeeds() {
    state.ticks = 0;
    if (state.kind === 'crypto' && state.source === 'Binance live') { connectKline(); startBook(); }
    else if (state.test || state.kind === 'crypto') startSim(state.test ? 'Testmodus · simulierte Ticks' : 'Offline · simulierte Ticks');
    else if (state.kind === 'stock') connectAktie();   // Marktdaten: Aktien live nachführen (auch 4h, gleiche Bündelung wie aggregate())
    else setConn('off', 'Schnappschuss (kein Live-Kurs)');
  }
  // Eine noch verbindende WebSocket erst nach dem Öffnen schließen (sonst warnt der Browser)
  const closeWs = (ws) => { ws.onclose = null; ws.onmessage = null; if (ws.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close(); else ws.close(); };
  function stopFeeds() {
    if (state.ws) { closeWs(state.ws); state.ws = null; }
    if (state.sim) { clearInterval(state.sim); state.sim = null; }
    if (state.es) { state.es.close(); state.es = null; }   // Marktdaten
    stopBook();
  }

  // ---------------- Bitcoin-Dominanz (BTC.D) ----------------
  // Historie: CoinMarketCap-Website-Endpunkt ohne Schlüssel, aber ohne CORS – daher über den Agent-Server
  // (/api/dominanz) oder aus dem Schnappschuss. Live-Punkt: CoinGecko /global (CORS frei), nur wenn die
  // Historie älter als zwei Stunden ist, weil CoinGecko ein etwas anderes Coin-Universum zählt.
  const DOM_LIVE_URL = 'https://api.coingecko.com/api/v3/global';
  function loadDominance() {
    if (state.dom.loading) return state.dom.loading;
    state.dom.loading = (async () => {
      let hist = null, src = '';
      if (!state.test && location.protocol.startsWith('http')) {
        try {
          const r = await fetchTimeout('api/dominanz', 10000);
          if (r.ok) { hist = await r.json(); src = 'CoinMarketCap über Server'; }
        } catch (e) { /* kein Agent-Server, Schnappschuss nehmen */ }
      }
      if (!hist && SNAP.dominanz) { hist = SNAP.dominanz; src = `Schnappschuss ${SNAP.dominanz.stand || ''}`; }
      state.dom.hist = hist; state.dom.source = src;
      applyDominance();
    })();
    return state.dom.loading;
  }
  async function pollDominanceLive() {
    if (state.test || Date.now() - state.dom.lastLive < 60e3) return;
    state.dom.lastLive = Date.now();
    try {
      const r = await fetchTimeout(DOM_LIVE_URL, 7000); const j = await r.json();
      const v = j?.data?.market_cap_percentage?.btc;
      if (isNum(v)) { state.dom.live = [Math.floor(Date.now() / 1000), Math.round(v * 100) / 100]; applyDominance(); }
    } catch (e) { /* kein Live-Wert, Historie reicht */ }
  }
  function applyDominance() {
    const h = state.dom.hist || {}, last = (h.stunden || h.tage || []).slice(-1)[0];
    state.dom.liveUsed = !!(state.dom.live && (!last || state.dom.live[0] - last[0] > 2 * 3600));
    state.dom.points = I.mergeSeries([h.tage, h.stunden, state.dom.liveUsed ? [state.dom.live] : []]);
    if (!state.bars.length) return;
    calc.btcd = I.alignSeries(state.dom.points, state.bars.map((b) => b.time), tfSec());
    if (hasTyp('btcd')) renderIndikatoren(true);
    updateLegends(hoverIdx);
  }
  function ensureDominance() { loadDominance(); pollDominanceLive(); }
  setInterval(() => { if (hasTyp('btcd')) pollDominanceLive(); }, 5 * 60e3);

  // ---------------- Orderbuch (Binance Partial Book Depth, 10 Stufen je Seite, 1 s) ----------------
  function stopBook() { if (state.wsBook) { closeWs(state.wsBook); state.wsBook = null; } $('#bookCard').style.display = 'none'; }
  function startBook() {
    stopBook();
    try {
      const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${state.symbol.toLowerCase()}@depth10`);
      state.wsBook = ws;
      ws.onmessage = (ev) => { const d = JSON.parse(ev.data); if (d.bids && d.asks) { $('#bookCard').style.display = ''; renderBook(d.bids, d.asks); } };
      ws.onerror = () => {};
    } catch (e) { /* offline */ }
  }
  function renderBook(bids, asks) {
    const p = precisionFor(state.lastPrice || +bids[0][0]);
    const lv = (arr) => arr.map(([pr, q]) => [+pr, +q]);
    const B = lv(bids).slice(0, 7), A = lv(asks).slice(0, 7);
    const maxCum = Math.max(B.reduce((s, x) => s + x[1], 0), A.reduce((s, x) => s + x[1], 0)) || 1;
    const rows = (arr, reverse) => {
      let cum = 0; const out = arr.map(([pr, q]) => { cum += q; return `<div class="row"><i style="width:${(cum / maxCum) * 100}%"></i><span>${fmt(pr, p)}</span><span class="q">${fmt(q, q >= 100 ? 1 : q >= 1 ? 3 : 4)}</span></div>`; });
      return (reverse ? out.reverse() : out).join('');
    };
    $('#book .asks').innerHTML = rows(A, true);
    $('#book .bids').innerHTML = rows(B, false);
    const spread = A[0][0] - B[0][0];
    $('#book .spread').innerHTML = `<span>Spread</span><b>${fmt(spread, p)}</b><span>${fmt((spread / A[0][0]) * 100, 3)} %</span>`;
  }

  // ---------------- Replay: die Vergangenheit Kerze für Kerze abspielen ----------------
  const R = state.replay;
  const SUB = [0.35, 0.7, 1]; // jede Kerze wächst in drei Schritten (Eröffnung → Schluss)
  function replayStart() {
    if (!state.bars.length || R.on) return;
    stopFeeds();
    R.on = true; R.full = state.bars.map((b) => ({ ...b })); R.pos = Math.max(60, R.full.length - 150); R.sub = 0;
    state.bars = R.full.slice(0, R.pos); rebuildTimes();
    state.prevTickPrice = null; state.lastPrice = state.bars[state.bars.length - 1].close;
    clearScriptNotified(); renderAll(true); setConn('sim', 'Replay · ' + state.source);
    $('#replayBar').style.display = 'flex'; $('#btnReplay').classList.add('on');
    $('#rpSlider').max = R.full.length; replayUpdateUi(); replayPlay(true);
    toast('Replay gestartet', `${state.symbol} ${state.tf}: Kerze ${R.pos} von ${R.full.length}. Leertaste = Pause, → = Schritt.`, 'signal');
  }
  function replayTick() {
    if (R.pos >= R.full.length) { replayPlay(false); toast('Replay zu Ende', 'Alle Kerzen abgespielt.', 'signal'); return; }
    const b = R.full[R.pos], f = SUB[R.sub];
    const close = b.open + (b.close - b.open) * f;
    const high = Math.max(b.open, close) + (b.high - Math.max(b.open, b.close)) * f;
    const low = Math.min(b.open, close) - (Math.min(b.open, b.close) - b.low) * f;
    onTick(b.time, b.open, high, low, close, b.volume * f, isNum(b.kauf) ? b.kauf * f : undefined);
    if (++R.sub >= SUB.length) { R.sub = 0; R.pos++; }
    replayUpdateUi();
  }
  function replayPlay(on) {
    R.playing = on; clearInterval(R.timer); R.timer = null;
    if (on) R.timer = setInterval(replayTick, 450 / (+$('#rpSpeed').value || 1));
    $('#rpPlay').textContent = on ? '⏸' : '▶';
  }
  function replaySeek(pos) {
    pos = Math.max(60, Math.min(R.full.length, Math.round(pos)));
    replayPlay(false); R.pos = pos; R.sub = 0;
    state.bars = R.full.slice(0, pos); rebuildTimes();
    state.prevTickPrice = null; state.lastPrice = state.bars[state.bars.length - 1].close;
    clearScriptNotified(); renderAll(true); replayUpdateUi();
  }
  function replayUpdateUi() {
    const b = state.bars[state.bars.length - 1];
    $('#rpPos').textContent = `${R.pos} / ${R.full.length} · ${new Date(b.time * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`;
    $('#rpSlider').value = R.pos;
  }
  function replayStop(reload = true) {
    if (!R.on) return;
    replayPlay(false); R.on = false; R.full = null;
    $('#replayBar').style.display = 'none'; $('#btnReplay').classList.remove('on');
    if (reload) load();
  }
  $('#btnReplay').onclick = () => (R.on ? replayStop() : replayStart());
  $('#rpPlay').onclick = () => replayPlay(!R.playing);
  $('#rpStep').onclick = () => { replayPlay(false); for (let k = 0; k < SUB.length; k++) replayTick(); };
  $('#rpStop').onclick = () => replayStop();
  $('#rpSpeed').onchange = () => { if (R.playing) replayPlay(true); };
  $('#rpSlider').oninput = (e) => replaySeek(+e.target.value);
  // Marktdaten: Aktien über den Push-Kanal des Dashboards (/api/strom). Bei jeder neuen Version werden
  // die letzten Kerzen des Symbols geholt und wie Binance-Ticks fortgeschrieben.
  function connectAktie() {
    const sym = state.symbol, tf = state.tf, euro = SNAP.aktien[sym]?.waehrung === 'EUR';
    const es = new EventSource('/api/strom');
    state.es = es; let warte = null;
    const holen = async () => {
      if (state.es !== es || state.replay.on) return;
      try {
        const r = await fetch(`data/kerzen?sym=${encodeURIComponent(sym)}&tf=${tf}`);
        if (!r.ok || state.es !== es) return;
        const d = await r.json();
        d.kerzen.forEach((k) => onTick(...k.slice(0, 6)));
        orderflowLaden();   // Delta: neue Abschlüsse höchstens alle 20 s
        setConn('live', euro ? 'Xetra · 15 Min. verzögert · nachgeführt' : 'Live · IEX über Alpaca');
      } catch (e) { setConn('', 'Verbindung unterbrochen'); }
    };
    es.onopen = () => setConn('live', euro ? 'Xetra · 15 Min. verzögert · nachgeführt' : 'Live · IEX über Alpaca');
    es.onmessage = () => { clearTimeout(warte); warte = setTimeout(holen, 700); };
    es.onerror = () => setConn('', 'verbinde…');
  }
  // Marktdaten: Kursleiste für Aktien alle 5 Sekunden auffrischen
  async function aktienKurse() {
    if (state.test) return;
    try {
      const d = await (await fetch('data/kurse')).json();
      Object.entries(d).forEach(([s, w]) => { if (STOCKS.includes(s)) { state.watch[s] = w; renderWatchRow(s); } });
    } catch (e) { /* Dashboard nicht erreichbar */ }
  }
  setTimeout(aktienKurse, 300);   // Marktdaten: erste Abfrage sofort, nicht erst nach 5 Sekunden
  setInterval(aktienKurse, 5000);

  function connectKline(attempt = 0) {
    setConn('', 'verbinde…');
    const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${state.symbol.toLowerCase()}@kline_${state.tf}`);
    state.ws = ws; let got = false;
    const guard = setTimeout(() => { if (!got) { closeWs(ws); fallback(); } }, 9000);
    const fallback = () => { if (state.ws !== ws) return; state.ws = null; if (attempt < 2) setTimeout(() => connectKline(attempt + 1), 1500); else startSim('WebSocket nicht erreichbar · simuliert'); };
    ws.onmessage = (ev) => {
      if (!got) { got = true; clearTimeout(guard); setConn('live', 'Live · Binance WebSocket'); }
      const k = JSON.parse(ev.data).k; if (!k) return;
      onTick(k.t / 1000, +k.o, +k.h, +k.l, +k.c, +k.v, +k.V);   // k.V = Taker-Kaufvolumen
    };
    ws.onerror = () => {};
    ws.onclose = () => { clearTimeout(guard); fallback(); };
  }
  // Simulation: Zufallsbewegung auf Basis der zuletzt typischen Schwankung.
  // Im Testmodus leicht zum nächsten offenen Alarm gezogen, damit die Demo sicher auslöst.
  function startSim(label) {
    if (state.sim) clearInterval(state.sim);
    setConn('sim', label);
    const bars = state.bars, n = bars.length;
    const vol = bars.slice(-50).reduce((s, b) => s + Math.abs(b.close - b.open), 0) / Math.min(50, n) || bars[n - 1].close * 0.002;
    let k = 0, t = bars[n - 1].time;
    state.sim = setInterval(() => {
      const last = state.bars[state.bars.length - 1];
      let p = last.close;
      const open = state.alarms.filter((a) => !a.fired).sort((a, b) => Math.abs(a.price - p) - Math.abs(b.price - p))[0];
      const pull = open ? Math.sign(open.price - p) * vol * 0.06 : 0;
      p = Math.max(p + (Math.random() - 0.5) * vol * 0.35 + pull, 1e-9);
      if (++k % 25 === 0) { t += tfSec(); onTick(t, last.close, Math.max(last.close, p), Math.min(last.close, p), p, last.volume * 0.05); return; }
      onTick(last.time, last.open, Math.max(last.high, p), Math.min(last.low, p), p, last.volume + Math.random() * last.volume * 0.01);
    }, 600);
  }
  // Beobachtungsliste: 24h-Werte per REST, dann Mini-Ticker per WebSocket
  async function startWatch() {
    renderWatch();
    if (state.test || !CRYPTO.length) return;   // Marktdaten: Demo ohne Binance
    try {
      const r = await fetchTimeout(`https://api.binance.com/api/v3/ticker/24hr?symbols=${encodeURIComponent(JSON.stringify(CRYPTO))}`, 6000);
      (await r.json()).forEach((x) => { state.watch[x.symbol] = { price: +x.lastPrice, open: +x.openPrice }; });
      renderWatch(); updateHeader(false);
    } catch (e) { /* offline */ }
    try {
      const ws = new WebSocket('wss://stream.binance.com:9443/stream?streams=' + CRYPTO.map((s) => s.toLowerCase() + '@miniTicker').join('/'));
      ws.onmessage = (ev) => { const d = JSON.parse(ev.data).data; state.watch[d.s] = { price: +d.c, open: +d.o }; renderWatchRow(d.s); };
      state.wsWatch = ws;
    } catch (e) { /* offline */ }
  }
  function watchData(s) {
    if (state.watch[s]) return state.watch[s];
    const rows = isStock(s) ? SNAP.aktien[s]?.['1d'] : SNAP.krypto[s]?.['1d'];
    if (!rows || rows.length < 2) return null;
    return { price: rows[rows.length - 1][4], open: rows[rows.length - 2][4], snap: true };
  }
  function renderWatch() {
    const tape = $('#tape'); tape.innerHTML = '';
    [...CRYPTO, ...STOCKS].forEach((s) => {
      const b = document.createElement('button'); b.className = 'chip'; b.dataset.sym = s;
      b.innerHTML = `<span class="n">${s.replace('USDT', '')}<small>${isStock(s) ? (SNAP.aktien[s]?.waehrung === 'EUR' ? 'XETRA' : 'US') : 'USDT'}</small></span><span class="p"></span><span class="c"></span>`;
      b.onclick = () => switchSymbol(s); tape.appendChild(b); renderWatchRow(s);
    });
  }
  function renderWatchRow(s) {
    const el = document.querySelector(`#tape .chip[data-sym="${s}"]`); if (!el) return;
    el.classList.toggle('sel', s === state.symbol);
    const d = watchData(s); if (!d) return;
    const pEl = el.querySelector('.p'), old = pEl.dataset.v ? +pEl.dataset.v : null;
    pEl.textContent = fmt(d.price, precisionFor(d.price)); pEl.dataset.v = d.price;
    if (old !== null && old !== d.price) { pEl.style.color = d.price > old ? C.up : C.down; setTimeout(() => (pEl.style.color = ''), 300); }
    const ch = ((d.price - d.open) / d.open) * 100; const c = el.querySelector('.c');
    c.textContent = `${ch >= 0 ? '+' : ''}${fmt(ch, 2)} %`; c.className = 'c ' + (ch >= 0 ? 'pos' : 'neg');
  }

  // ---------------- Zeichenwerkzeuge & Alarme ----------------
  const hints = { hline: 'Klick setzt eine horizontale Linie', alarm: 'Klick setzt einen Preisalarm', trend: 'Trendlinie: Startpunkt klicken',
    fib: 'Fibonacci: ersten Punkt klicken (Hoch oder Tief)', pos: 'Position: Einstieg klicken', anker: 'VWAP: Kerze für den Startpunkt klicken' };
  // Eigene Zeichenebene (drawings.js) für Fibonacci und Positionen – hängt an der Kerzenserie
  const layer = new window.Drawings.Layer({ up: C.up, down: C.down, accent: C.accent, fmt: (v) => fmtP(v) });
  candle.attachPrimitive(layer);
  let drawPts = []; // gesammelte Klicks des laufenden Werkzeugs
  function setTool(t) {
    state.tool = t; state.trendStart = null; drawPts = []; layer.setPending(null); removePreview();
    document.querySelectorAll('#toolbar [data-tool]').forEach((b) => b.classList.toggle('active', b.dataset.tool === t));
    const h = $('#toolHint'); h.textContent = hints[t] || ''; h.style.display = hints[t] ? 'block' : 'none';
  }
  function pointFromParam(param) {
    if (!param.point) return null;
    const price = candle.coordinateToPrice(param.point.y);
    let idx = param.logical !== undefined ? Math.round(param.logical) : undefined;
    if (!isNum(price) || idx === undefined) return null;
    idx = Math.max(0, Math.min(state.bars.length - 1, idx));
    return { idx, time: state.times[idx], price };
  }
  // Klicks selbst erkennen (Zeiger runter/hoch ohne Ziehen) – unabhängig von der Klick-Logik der Bibliothek,
  // die schnelle Folgeklicks als Doppelklick schluckt. Koordinaten werden in Zeitindex und Preis umgerechnet.
  let downAt = null;
  const mainEl = panes.main.el.querySelector('.chart');
  mainEl.addEventListener('pointerdown', (e) => { if (e.button === 0) downAt = { x: e.clientX, y: e.clientY }; }, true);
  mainEl.addEventListener('pointerup', (e) => {
    if (!downAt || e.button !== 0) return;
    const moved = Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y); downAt = null;
    if (moved > 4 || state.tool === 'cross') return;
    const r = mainEl.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    if (x > r.width - main.priceScale('right').width()) return; // Klick auf die Preisachse ignorieren
    handleChartClick({ point: { x, y }, logical: main.timeScale().coordinateToLogical(x) ?? undefined });
  }, true);
  function handleChartClick(param) {
    const pt = pointFromParam(param); if (!pt) return;
    if (state.tool === 'anker') {
      const uid = state.ankerFuer; state.ankerFuer = null; setTool('cross');
      if (state.inds.some((x) => x.uid === uid)) { updateIndicator(uid, { anker: 'zeit', zeit: state.bars[pt.idx].time }); toast('VWAP verankert', new Date(state.bars[pt.idx].time * 1000).toLocaleString('de-DE'), 'signal'); }
      return;
    }
    if (state.tool === 'hline') { addHLine(pt.price); setTool('cross'); }
    else if (state.tool === 'alarm') { addAlarm(pt.price); setTool('cross'); }
    else if (state.tool === 'trend') {
      if (!state.trendStart) { state.trendStart = pt; $('#toolHint').textContent = 'Trendlinie: Endpunkt klicken'; }
      else { addTrend(state.trendStart, pt); setTool('cross'); }
    }
    else if (state.tool === 'fib') {
      drawPts.push(pt);
      if (drawPts.length === 1) $('#toolHint').textContent = 'Fibonacci: zweiten Punkt klicken';
      else { layer.add(shapeFrom('fib', drawPts)); const s = layer.shapes[layer.shapes.length - 1]; toast('Fibonacci gesetzt', `Strecke ${fmtP(Math.min(s.a.price, s.b.price))} – ${fmtP(Math.max(s.a.price, s.b.price))}`, 'signal'); setTool('cross'); }
    }
    else if (state.tool === 'pos') {
      drawPts.push(pt);
      if (drawPts.length === 1) $('#toolHint').textContent = 'Ziel klicken (über dem Einstieg = Long, darunter = Short)';
      else if (drawPts.length === 2) $('#toolHint').textContent = 'Stop klicken';
      else {
        const s = shapeFrom('pos', drawPts); layer.add(s);
        const crv = Math.abs(s.target.price - s.entry.price) / Math.abs(s.entry.price - s.stop.price);
        toast(`${s.target.price >= s.entry.price ? 'Long' : 'Short'} eingezeichnet`, `Einstieg ${fmtP(s.entry.price)} · Ziel ${fmtP(s.target.price)} · Stop ${fmtP(s.stop.price)} · CRV 1 : ${fmt(crv, 2)}`, 'signal');
        setTool('cross');
      }
    }
  }
  function shapeFrom(type, pts) {
    if (type === 'fib') return { type, a: pts[0], b: pts[1] };
    return { type, entry: pts[0], target: pts[1], stop: pts[2], idx0: pts[0].idx, idx1: Math.max(...pts.map((p) => p.idx)) };
  }
  function undoDrawing() {
    if (layer.shapes.length) { layer.removeLast(); return; }
    const t = state.trends.pop(); if (t) { main.removeSeries(t); return; }
    const h = state.hlines.pop(); if (h) candle.removePriceLine(h);
  }
  let preview = null;
  function removePreview() { previewPt = null; if (preview) { try { main.removeSeries(preview); } catch (e) { /* schon entfernt */ } preview = null; } }
  // Vorschau der Trendlinie: außerhalb des Crosshair-Ereignisses zeichnen (sonst löst setData neue Ereignisse aus)
  let previewPt = null, previewQueued = false;
  let pendQueued = false;
  function onMainMove(param) {
    if ((state.tool === 'fib' || state.tool === 'pos') && drawPts.length) {
      const pt = pointFromParam(param); if (!pt || pendQueued) return;
      pendQueued = true; requestAnimationFrame(() => { pendQueued = false; if (drawPts.length) layer.setPending(shapeFrom(state.tool, [...drawPts, pt, pt].slice(0, state.tool === 'fib' ? 2 : 3))); });
      return;
    }
    if (state.tool !== 'trend' || !state.trendStart) return;
    const pt = pointFromParam(param); if (!pt || pt.idx === state.trendStart.idx) return;
    if (previewPt && previewPt.idx === pt.idx && Math.abs(previewPt.price - pt.price) < 1e-12) return;
    previewPt = pt;
    if (previewQueued) return; previewQueued = true;
    requestAnimationFrame(() => {
      previewQueued = false;
      if (state.tool !== 'trend' || !state.trendStart || !previewPt) return;
      if (!preview) preview = main.addLineSeries(lineOpts('rgba(245,185,66,0.6)', 2, { lineStyle: LW.LineStyle.Dashed, autoscaleInfoProvider: () => null, lastValueVisible: false }));
      preview.setData(trendPoints(state.trendStart, previewPt));
    });
  }
  const trendPoints = (a, b) => [a, b].sort((x, y) => x.idx - y.idx).map((p) => ({ time: p.time, value: p.price }));
  function addTrend(a, b) {
    removePreview(); if (a.idx === b.idx) return;
    const s = main.addLineSeries(lineOpts(C.accent, 2, { autoscaleInfoProvider: () => null }));
    s.setData(trendPoints(a, b));
    s.setMarkers(trendPoints(a, b).map((p) => ({ time: p.time, position: 'inBar', shape: 'circle', color: C.accent, size: 0.6 })));
    state.trends.push(s);
  }
  function clearTrends() { state.trends.forEach((s) => main.removeSeries(s)); state.trends = []; removePreview(); state.trendStart = null; }
  function addHLine(price) {
    state.hlines.push(candle.createPriceLine({ price, color: '#8fb3ff', lineWidth: 1, lineStyle: LW.LineStyle.Solid, axisLabelVisible: true, title: '' }));
  }
  function addAlarm(price) {
    const a = { price, fired: false, dir: price > state.lastPrice ? 'steigt über' : 'fällt unter' };
    a.line = candle.createPriceLine({ price, color: C.warn, lineWidth: 2, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: 'ALARM' });
    state.alarms.push(a); renderAlarms();
    toast('Alarm gesetzt', `${state.symbol} ${a.dir} ${fmtP(price)}`, 'signal');
  }
  function checkAlarms(prev, now) {
    if (!isNum(prev)) return;
    state.alarms.forEach((a) => {
      if (a.fired) return;
      if ((prev < a.price && now >= a.price) || (prev > a.price && now <= a.price)) {
        a.fired = true; a.at = new Date();
        a.line.applyOptions({ color: now >= prev ? C.up : C.down, lineStyle: LW.LineStyle.Solid, title: 'AUSGELÖST' });
        toast(`Alarm ausgelöst: ${state.symbol}`, `Kurs ${fmtP(now)} hat ${fmtP(a.price)} gekreuzt (${a.at.toLocaleTimeString('de-DE')})`);
        beep(); const st = $('#stage'); st.classList.remove('flash'); void st.offsetWidth; st.classList.add('flash');
        renderAlarms();
      }
    });
  }
  function renderAlarms() {
    const ul = $('#alarmList'); ul.innerHTML = '';
    $('#alarmCount').textContent = state.alarms.length;
    if (!state.alarms.length) { ul.innerHTML = '<li class="muted">Glocke links wählen und in den Chart klicken.</li>'; return; }
    state.alarms.forEach((a, k) => {
      const li = document.createElement('li'); li.className = a.fired ? 'fired' : '';
      li.innerHTML = `<span>${a.fired ? '●' : '○'}</span><span class="p">${state.symbol} ${a.dir} ${fmtP(a.price)}${a.fired ? ' · ausgelöst ' + a.at.toLocaleTimeString('de-DE') : ''}</span><button title="Löschen">✕</button>`;
      li.querySelector('button').onclick = () => { candle.removePriceLine(a.line); state.alarms.splice(k, 1); renderAlarms(); };
      ul.appendChild(li);
    });
  }
  function clearDrawings() {
    clearTrends(); layer.clear();
    state.hlines.forEach((l) => candle.removePriceLine(l)); state.hlines = [];
    state.alarms.forEach((a) => candle.removePriceLine(a.line)); state.alarms = []; renderAlarms();
  }

  // ---------------- Mini-Script ----------------
  const code = $('#code'), hl = $('#hl');
  const FN = new Set(['sma', 'ema', 'rma', 'rsi', 'stdev', 'highest', 'lowest', 'bbupper', 'bblower', 'macd', 'macdsignal', 'macdhist', 'vwap', 'atr', 'stoch', 'stochd', 'supertrend', 'crossover', 'crossunder', 'change', 'abs', 'max', 'min',
    'wma', 'hma', 'roc', 'linreg', 'bbwidth', 'bbpercent', 'kcupper', 'kclower', 'dcupper', 'dclower', 'psar', 'adx', 'plusdi', 'minusdi', 'aroonup', 'aroondown', 'willr', 'cci', 'obv', 'mfi', 'cmf', 'tenkan', 'kijun']);
  const ACT = new Set(['plot', 'panel', 'alert', 'hline', 'buy', 'sell']);
  const BUILTIN = new Set(['close', 'open', 'high', 'low', 'volume', 'hl2', 'hlc3']);
  function highlight() {
    const src = code.value;
    const re = /(\/\/[^\n]*|#[^\n]*)|("[^"\n]*"?|'[^'\n]*'?)|(\b\d*\.?\d+\b)|([A-Za-z_äöüÄÖÜß][\wäöüÄÖÜß]*)|([^\w\s])|(\s+)/g;
    let out = '', m;
    while ((m = re.exec(src))) {
      const t = esc(m[0]);
      if (m[1]) out += `<span class="hl-com">${t}</span>`;
      else if (m[2]) out += `<span class="hl-str">${t}</span>`;
      else if (m[3]) out += `<span class="hl-num">${t}</span>`;
      else if (m[4]) { const w = m[4].toLowerCase(); out += ACT.has(w) ? `<span class="hl-act">${t}</span>` : FN.has(w) ? `<span class="hl-fn">${t}</span>` : BUILTIN.has(w) ? `<span class="hl-var">${t}</span>` : ['and', 'or', 'not'].includes(w) ? `<span class="hl-op">${t}</span>` : t; }
      else if (m[5]) out += `<span class="hl-op">${t}</span>`;
      else out += t;
    }
    hl.innerHTML = out + '\n';
    hl.scrollTop = code.scrollTop;
  }
  code.addEventListener('input', highlight);
  code.addEventListener('scroll', () => (hl.scrollTop = code.scrollTop));
  code.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); applyScript(); }
    if (e.key === 'Tab') { e.preventDefault(); document.execCommand('insertText', false, '  '); }
  });
  function clearScriptVisuals() {
    state.script.series.forEach(({ chart, s }) => chart.removeSeries(s)); state.script.series = [];
    state.script.lines.forEach((l) => candle.removePriceLine(l)); state.script.lines = [];
    candle.setMarkers([]);
  }
  const clearScriptNotified = () => { state.script.notified = new Set(); };
  function applyScript() { state.script.text = code.value; clearScriptNotified(); store.set('script', code.value); runScript(true); layoutPanes(); updateLegends(hoverIdx); }
  let scriptTimer = null;
  function scheduleScript() {
    if (!state.script.text) return;
    const o = state.script.outputs[0];
    if (o && o.values.length !== state.bars.length) { clearTimeout(scriptTimer); scriptTimer = null; runScript(false, true); updateLegends(hoverIdx); return; }
    if (scriptTimer) return;
    scriptTimer = setTimeout(() => { scriptTimer = null; runScript(false, true); updateLegends(hoverIdx); }, 800);
  }
  function runScript(report, live) {
    const out = $('#scriptOut');
    const btEl = $('#btStats');
    if (!state.script.text.trim()) { clearScriptVisuals(); state.script.outputs = []; state.script.bt = null; btEl.style.display = 'none'; return; }
    const t0 = performance.now();
    let outputs;
    try { outputs = window.MiniScript.run(state.script.text, state.bars); }
    catch (e) { if (report) out.innerHTML = `<span class="err">✕ ${esc(e.message)}</span>`; return; }
    // Backtest, sobald buy() und sell() vorkommen: Kapitalkurve und Buy & Hold als eigenes Panel
    const buys = outputs.filter((o) => o.type === 'buy'), sells = outputs.filter((o) => o.type === 'sell');
    let bt = null;
    if (buys.length || sells.length) {
      const any = (arr, i) => arr.some((o) => o.values[i]);
      const n = state.bars.length, bs = Array.from({ length: n }, (_, i) => any(buys, i)), ss = Array.from({ length: n }, (_, i) => any(sells, i));
      bt = window.Backtest.backtest(state.bars, bs, ss, { fee: 0.001 });
      outputs.push({ type: 'panel', values: bt.equity, color: C.up, title: 'Strategie %', synthetic: true });
      outputs.push({ type: 'panel', values: bt.buyHold, color: 'rgba(122,133,151,0.9)', title: 'Buy & Hold %', synthetic: true });
    }
    state.script.bt = bt;
    const ms = performance.now() - t0;
    const sameShape = live && outputs.length === state.script.outputs.length && outputs.every((o, k) => o.type === state.script.outputs[k].type);
    state.script.outputs = outputs;
    if (!sameShape) {
      clearScriptVisuals();
      outputs.forEach((o) => {
        if (o.type === 'plot') state.script.series.push({ chart: main, s: main.addLineSeries(lineOpts(o.color, 2)), o });
        if (o.type === 'panel') state.script.series.push({ chart: panes.script.chart, s: panes.script.chart.addLineSeries(lineOpts(o.color, 2, { lastValueVisible: true })), o });
        if (o.type === 'hline') { const v = o.values[o.values.length - 1]; if (isNum(v)) state.script.lines.push(candle.createPriceLine({ price: v, color: o.color, lineWidth: 1, lineStyle: LW.LineStyle.Dotted, title: o.title })); }
      });
    }
    let k = 0;
    outputs.forEach((o) => { if (o.type === 'plot' || o.type === 'panel') state.script.series[k++].s.setData(toLine(o.values)); });
    // Signale (alert) als Pfeile unter den Kerzen; ausgelöst wird beim Wechsel von 0 auf 1.
    // Beschriftung nur, wenn es wenige Signale sind – sonst überlagern sich die Texte.
    const markers = []; let signals = 0;
    outputs.filter((o) => o.type === 'alert').forEach((o) => {
      const idx = []; o.values.forEach((v, i) => { if (v && !o.values[i - 1]) idx.push(i); });
      signals += idx.length;
      const withText = idx.length <= 6 && o.title.length <= 14;
      idx.forEach((i) => markers.push({ time: state.times[i], position: 'belowBar', color: o.color, shape: 'arrowUp', size: 1, text: withText ? o.title : '' }));
      const n = o.values.length - 1, key = o.title + '@' + state.bars[n].time;
      if (live && o.values[n] && !o.values[n - 1] && !state.script.notified.has(key)) {
        state.script.notified.add(key); toast('Skript-Signal: ' + o.title, `${state.symbol} bei ${fmtP(state.lastPrice)}`, 'signal'); beep();
      }
    });
    // Backtest: ausgeführte Käufe/Verkäufe an der Ausführungskerze, Verkäufe mit Ergebnis des Trades
    if (bt) {
      const few = bt.marks.length <= 16;
      bt.marks.forEach((m) => markers.push(m.side === 'buy'
        ? { time: state.times[m.i], position: 'belowBar', color: C.up, shape: 'arrowUp', size: 1.2, text: few ? 'Kauf' : '' }
        : { time: state.times[m.i], position: 'aboveBar', color: C.down, shape: 'arrowDown', size: 1.2, text: few ? `${m.ret >= 0 ? '+' : ''}${fmt(m.ret * 100, 1)} %` : '' }));
      const last = bt.marks[bt.marks.length - 1], key = 'bt:' + (last ? last.side + '@' + state.bars[last.i].time : '');
      if (live && last && last.i === state.bars.length - 1 && !state.script.notified.has(key)) {
        state.script.notified.add(key); toast(last.side === 'buy' ? 'Strategie kauft' : 'Strategie verkauft', `${state.symbol} zu ${fmtP(last.px)}`, 'signal'); beep();
      }
      renderBacktest(bt);
    } else { btEl.style.display = 'none'; btEl.closest('.card').classList.remove('has-bt'); }
    markers.sort((a, b) => a.time - b.time);
    candle.setMarkers(markers);
    out.innerHTML = `<span class="ok">✓ ${outputs.filter((o) => !o.synthetic).length} Ausgabe${outputs.length === 1 ? '' : 'n'}</span> · ${signals} Signal${signals === 1 ? '' : 'e'}${bt ? ` · ${bt.stats.trades} Trades` : ''} auf ${state.bars.length} Kerzen · ${ms.toFixed(1).replace('.', ',')} ms`;
  }
  function renderBacktest(bt) {
    const s = bt.stats, el = $('#btStats'); el.style.display = ''; el.closest('.card').classList.add('has-bt');
    const pct = (x, d = 1) => (isNum(x) ? `${x >= 0 ? '+' : ''}${fmt(x * 100, d)} %` : '–');
    const cell = (k, v, cls = '') => `<div><span class="k">${k}</span><span class="v ${cls}">${v}</span></div>`;
    const cmp = isNum(s.ret) && isNum(s.buyHoldRet) ? (s.ret > s.buyHoldRet ? 'pos' : 'neg') : '';
    el.innerHTML = `<div class="h">Backtest · nur long · nächste Eröffnung · ${fmt(bt.fee * 100, 1)} % Gebühr je Seite${s.open ? ' · Position offen' : ''}</div>`
      + cell('Strategie', pct(s.ret), cmp) + cell('Buy & Hold', pct(s.buyHoldRet)) + cell('Max. Rückgang', isNum(s.maxDD) ? `−${fmt(s.maxDD * 100, 1)} %` : '–', 'neg')
      + cell('Trades', s.trades) + cell('Trefferquote', isNum(s.winRate) ? `${fmt(s.winRate * 100, 0)} %` : '–') + cell('Profitfaktor', s.profitFactor === Infinity ? '∞' : isNum(s.profitFactor) ? fmt(s.profitFactor, 2) : '–')
      + cell('Ø Trade', pct(s.avgTrade, 2)) + cell('Investiert (Zeit)', `${fmt(s.exposure * 100, 0)} %`) + cell('Kerzen', state.bars.length);
  }
  $('#btnRun').onclick = applyScript;
  $('#btnClearScript').onclick = () => { state.script.text = ''; state.script.bt = null; store.set('script', ''); clearScriptVisuals(); state.script.outputs = []; $('#scriptOut').textContent = ''; $('#btStats').style.display = 'none'; $('#btStats').closest('.card').classList.remove('has-bt'); layoutPanes(); updateLegends(hoverIdx); };
  EXAMPLES.forEach((ex) => {
    const b = document.createElement('button'); b.textContent = ex.name;
    b.onclick = () => { code.value = ex.code; highlight(); applyScript(); };
    $('#examples').appendChild(b);
  });

  // ---------------- Kopfzeile ----------------
  function renderToolbarState() {
    const tg = $('#tfGroup'); tg.innerHTML = '';
    TFS.forEach((t) => {
      const b = document.createElement('button'); b.textContent = t.label; b.className = t.id === state.tf ? 'active' : '';
      b.disabled = isStock(state.symbol) && t.id === '1m' && !SNAP.aktien[state.symbol]?.['1m'];   // Marktdaten: 1m, wenn vorhanden
      b.title = b.disabled ? 'Für Aktien nicht im Schnappschuss' : '';
      b.onclick = () => { state.tf = t.id; load(); }; tg.appendChild(b);
    });
    const ig = $('#indGroup'); ig.innerHTML = '';
    state.inds.forEach((inst) => {
      const d = defOf(inst), b = document.createElement('button');
      b.textContent = d.kurz(inst.p); b.className = 'on'; b.dataset.uid = inst.uid; b.style.setProperty('--c', inst.farbe);
      b.title = `${d.titel} – Klick: Einstellungen`;
      b.onclick = (e) => { e.stopPropagation(); einstellungenOeffnen(inst, b); };
      ig.appendChild(b);
    });
    const plus = document.createElement('button'); plus.className = 'ind-plus'; plus.textContent = '+ Indikator'; plus.title = 'Indikator hinzufügen';
    plus.onclick = () => auswahlOeffnen(); ig.prepend(plus);   // vorne: bleibt sichtbar, auch wenn die Chips scrollen
    $('#symbolInput').value = state.symbol;
    $('#btnTest').classList.toggle('on', state.test);
    document.querySelectorAll('#tape .chip').forEach((el) => el.classList.toggle('sel', el.dataset.sym === state.symbol));
  }
  function switchSymbol(s) {
    s = s.trim().toUpperCase().replace(/[^A-Z0-9]/g, ''); if (!s) return;
    if (s !== state.symbol) clearDrawings();
    state.symbol = s; state.watch[s] = state.watch[s]; load();
  }
  const dl = $('#symbolList');
  [...CRYPTO.map((s) => [s, 'Krypto · Binance live']), ...STOCKS.map((s) => [s, `${SNAP.aktien[s]?.name || 'Aktie'} · ${SNAP.aktien[s]?.quelle || 'Schnappschuss'}`])]   // Marktdaten: Namen.forEach(([s, l]) => { const o = document.createElement('option'); o.value = s; o.label = l; dl.appendChild(o); });
  $('#symbolInput').addEventListener('change', (e) => switchSymbol(e.target.value));
  $('#symbolInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
  $('#btnTest').onclick = () => { state.test = !state.test; if (state.test && state.wsWatch) { state.wsWatch.close(); state.wsWatch = null; } if (!state.test) startWatch(); load(); };
  document.querySelectorAll('#toolbar [data-tool]').forEach((b) => (b.onclick = () => setTool(b.dataset.tool)));
  document.querySelector('[data-action="clear"]').onclick = clearDrawings;
  function setFullscreen(on) {
    const open = on ?? !document.body.classList.contains('fullscreen');
    document.body.classList.toggle('fullscreen', open);
    // Marktdaten: im Dashboard läuft iC Charts in einem Rahmen; echtes Vollbild füllt dann den ganzen Bildschirm
    if (open && !document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {});
    if (!open && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    document.querySelector('[data-action="fullscreen"]').title = open ? 'Vollbild verlassen (Esc, Alt+V)' : 'Vollbild (Alt+V)';
    const h = $('#toolHint'); clearTimeout(setFullscreen.t);
    if (open) { h.textContent = 'Vollbild · Esc zum Verlassen'; h.style.display = 'block'; setFullscreen.t = setTimeout(() => { if (h.textContent.startsWith('Vollbild')) h.style.display = 'none'; }, 2000); }
    else if (h.textContent.startsWith('Vollbild')) h.style.display = 'none';
  }
  document.querySelector('[data-action="fullscreen"]').onclick = () => setFullscreen();
  document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && document.body.classList.contains('fullscreen')) setFullscreen(false); });   // Marktdaten: Esc des Browsers
  document.querySelector('[data-action="fit"]').onclick = () => { const n = state.bars.length; main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 6 }); main.priceScale('right').applyOptions({ autoScale: true }); };
  $('#licLink').onclick = (e) => { e.preventDefault(); $('#licDlg').showModal(); };
  document.addEventListener('keydown', (e) => {
    if (e.target === code || e.target.tagName === 'INPUT') return;
    if (e.key === 'Escape') { setTool('cross'); setFullscreen(false); }
    if (e.altKey && e.code === 'KeyV') { e.preventDefault(); setFullscreen(); }
    if (R.on && e.code === 'Space') { e.preventDefault(); replayPlay(!R.playing); }
    if (R.on && e.key === 'ArrowRight') { e.preventDefault(); $('#rpStep').onclick(); }
    if (e.altKey && e.code === 'KeyH') setTool('hline');
    if (e.altKey && e.code === 'KeyA') setTool('alarm');
    if (e.altKey && e.code === 'KeyT') setTool('trend');
    if (e.altKey && e.code === 'KeyF') setTool('fib');
    if (e.altKey && e.code === 'KeyP') setTool('pos');
    if (e.key === 'Backspace' || e.key === 'Delete') undoDrawing();
  });

  // ---------------- Start ----------------
  const saved = params.has('ex') ? null : store.get('script');
  code.value = typeof saved === 'string' && saved.trim() ? saved : 'plot(ema(close, 21))'; highlight();
  renderToolbarState(); startWatch();
  load().then(() => {
    // Optionale Vorführ-Parameter (für Screenshots/Video): ?ex=1 lädt ein Beispiel, ?demo=1 setzt Alarm + Trendlinie, ?replay=1 startet das Replay
    const ex = parseInt(params.get('ex'), 10);
    if (EXAMPLES[ex - 1]) { code.value = EXAMPLES[ex - 1].code; highlight(); applyScript(); }
    else if (saved && saved.trim()) applyScript();
    if (params.get('replay') === '1') { replayStart(); if (params.get('pause') === '1') replayPlay(false); }
    if (params.get('demo') === '1' && state.bars.length > 60) {
      const n = state.bars.length, b = state.bars;
      let i1 = n - 60, i2 = n - 15;
      for (let i = n - 80; i < n - 40; i++) if (b[i].low < b[i1].low) i1 = i;
      for (let i = n - 30; i < n - 3; i++) if (b[i].low < b[i2].low) i2 = i;
      addTrend({ idx: i1, time: state.times[i1], price: b[i1].low }, { idx: i2, time: state.times[i2], price: b[i2].low });
      addAlarm(state.lastPrice * (1 + (parseFloat(params.get('alarmPct')) || 0.15) / 100));
      addHLine(Math.max(...b.slice(-120).map((x) => x.high)));
    }
    window.__ready = true;
  });
  // Öffentliche Schnittstelle: für automatische Tests und für die Chart-Werkzeuge des Claude-Agenten (agent-panel.js)
  window.__sarge = {
    state, layer, IND, TFS, calc: () => calc, main, candle,
    addAlarm, addHLine, applyScript, replayStart, replayPlay, replayStop, clearDrawings, switchSymbol,
    setTimeframe: (tf) => { if (!TFS.some((t) => t.id === tf)) throw new Error('Zeiteinheit ' + tf + ' unbekannt'); state.tf = tf; return load(); },
    setIndicator, addIndicator, updateIndicator, removeIndicator, indicators: indikatorListe, katalog: K,
    runScript: (text) => { code.value = text; highlight(); applyScript(); return $('#scriptOut').textContent; },
    clearScript: () => $('#btnClearScript').onclick(),
    idxForTime: (t) => { let best = 0, d = Infinity; state.bars.forEach((b, i) => { const x = Math.abs(b.time - t); if (x < d) { d = x; best = i; } }); return best; },
    waitLoaded: () => new Promise((r) => { const k = setInterval(() => { if (state.bars.length && !state.loading) { clearInterval(k); r(); } }, 50); }),
    toast,
  };
})();
