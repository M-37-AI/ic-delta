/*
 * agent-panel.js – Chat mit Claude direkt im Chart.
 *
 * Der Browser spricht per WebSocket mit agent/server.mjs. Dort läuft das Claude Agent SDK
 * (der Kern von Claude Code). Die Chart-Werkzeuge sind beim Server als Tools angemeldet,
 * ausgeführt werden sie aber HIER im Browser: der Server schickt {type:'tool_call'}, der Browser
 * führt die Funktion gegen window.__sarge aus und antwortet mit {type:'tool_result'}.
 *
 * Es gibt keine Börsenanbindung: Claude kann zeichnen, rechnen und lesen, aber nicht handeln.
 */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const A = () => window.__sarge;
  const params = new URLSearchParams(location.search);
  // Marktdaten: Agenten-Server von iC Delta auf Port 8814 (ic-delta/agent/server.mjs), Analysen-Dienst auf 8813
  const defaultUrl = location.protocol.startsWith('http') ? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}:${(+location.port || 8812) + 2}/agent` : 'ws://localhost:8814/agent';
  const ANALYSEN = `${location.protocol.startsWith('http') ? location.protocol : 'http:'}//${location.hostname || 'localhost'}:${(+location.port || 8812) + 1}`;
  let wsUrl = params.get('agent') || (() => { try { return localStorage.getItem('sarge.agentUrl'); } catch (e) { return null; } })() || defaultUrl;

  // ---------------- Chart-Werkzeuge (werden vom Server aufgerufen) ----------------
  const fmtTime = (t) => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  const toTime = (v) => { // ISO-Text, Unix-Sekunden oder "vor N Kerzen"
    if (v === undefined || v === null) return null;
    if (typeof v === 'number') return v > 1e11 ? v / 1000 : v;
    const d = Date.parse(v); if (!isNaN(d)) return d / 1000;
    throw new Error('Zeitangabe nicht verstanden: ' + v);
  };
  const point = (time, barsAgo, price) => {
    const S = A().state; let idx;
    if (barsAgo !== undefined && barsAgo !== null) idx = Math.max(0, S.bars.length - 1 - Math.round(barsAgo));
    else if (time !== undefined && time !== null) idx = A().idxForTime(toTime(time));
    else idx = S.bars.length - 1;
    const b = S.bars[idx];
    return { idx, time: S.times[idx], price: typeof price === 'number' ? price : b.close };
  };
  const round = (v, d = 2) => (typeof v === 'number' && isFinite(v) ? +v.toFixed(d) : null);

  // Marktdaten: Formationen und Alarme über den Analysen-Dienst
  const analysenTf = () => (A().state.tf === '4h' ? '1h' : A().state.tf);
  async function analysen(pfad, opts) {
    let r;
    try { r = await fetch(ANALYSEN + pfad, opts); } catch (e) { throw new Error('Analysen-Dienst (Port 8813) nicht erreichbar'); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.fehler) throw new Error(d.fehler || 'Analysen-Dienst antwortet ' + r.status);
    return d;
  }
  function formationZeichnen(f) {
    const art = f.richtung === 'bullisch' ? 'bull' : f.richtung === 'bärisch' ? 'bear' : 'neutral';
    const pt = ([t, p]) => point(t, undefined, p);
    if (f.zone) {
      const a = f.punkte.length ? pt(f.punkte[0]) : point(undefined, 150);
      A().layer.add({ type: 'zone', p1: f.zone[0], p2: f.zone[1], idx0: a.idx, idx1: A().state.bars.length - 1, label: f.name, kind: f.art === 'widerstand' ? 'resistance' : 'support' });
      return;
    }
    for (const l of f.linien) {
      const kind = l.art === 'unterstuetzung' ? 'support' : l.art === 'widerstand' ? 'resistance' : art;
      A().layer.add({ type: 'line', a: point(l.t1, undefined, l.p1), b: point(l.t2, undefined, l.p2), extend: f.art === 'trendlinie', label: f.linien.length > 1 ? '' : f.name, kind, dashed: l.art === 'nackenlinie' });
    }
    if (f.punkte.length >= 3 && f.art !== 'trendlinie') A().layer.add({ type: 'poly', pts: f.punkte.map(pt), closed: false, label: f.name, kind: art });
    else if (!f.linien.length && f.punkte.length) { const q = pt(f.punkte[f.punkte.length - 1]); A().layer.add({ type: 'note', idx: q.idx, price: q.price, text: f.name, kind: art, above: art !== 'bull' }); }
  }

  const TOOLS = {
    get_state() {
      const S = A().state, c = A().calc(), n = S.bars.length, b = S.bars[n - 1];
      return {
        symbol: S.symbol, timeframe: S.tf, source: S.source, bars: n, last_price: S.lastPrice,
        last_bar: { time: fmtTime(b.time), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume },
        indicators_on: Object.keys(S.ind).filter((k) => S.ind[k]),
        indicators: A().indicators(),   // alle Exemplare mit id, Einstellungen und Werten der letzten Kerze
        values: { sma50: round(c.sma[n - 1], 4), ema21: round(c.ema[n - 1], 4), bb_upper: round(c.bb.upper[n - 1], 4), bb_lower: round(c.bb.lower[n - 1], 4), vwap: round(c.vwap[n - 1], 4), rsi14: round(c.rsi[n - 1]), macd: round(c.macd.macd[n - 1], 4), macd_signal: round(c.macd.signal[n - 1], 4), supertrend: round(c.st.line[n - 1], 4), supertrend_dir: c.st.dir[n - 1] === 1 ? 'up' : 'down', btc_dominance_pct: S.ind.btcd ? round(c.btcd?.[n - 1]) : undefined },
        alarms: S.alarms.map((a) => ({ price: a.price, fired: a.fired })),
        drawings: A().layer.shapes.map((s) => (s.type === 'fib' ? { type: 'fib', from: s.a.price, to: s.b.price } : s.type === 'pos' ? { type: 'position', entry: s.entry.price, target: s.target.price, stop: s.stop.price } : s.type === 'line' ? { type: 'line', label: s.label, kind: s.kind, from: s.a.price, to: s.b.price } : s.type === 'level' ? { type: 'level', label: s.label, kind: s.kind, price: s.price } : s.type === 'zone' ? { type: 'zone', label: s.label, low: Math.min(s.p1, s.p2), high: Math.max(s.p1, s.p2) } : s.type === 'poly' ? { type: 'pattern', label: s.label, points: s.pts.length } : { type: 'note', text: s.text })),
        script: S.script.text || null, backtest: S.script.bt ? S.script.bt.stats : null, replay: S.replay.on,
      };
    },
    get_bars({ count = 50 } = {}) {
      const S = A().state, c = A().calc(), n = S.bars.length, k = Math.min(Math.max(1, Math.round(count)), 500);
      return { symbol: S.symbol, timeframe: S.tf, columns: ['time_utc', 'open', 'high', 'low', 'close', 'volume', 'rsi14', 'ema21'],
        rows: S.bars.slice(n - k).map((b, j) => { const i = n - k + j; return [fmtTime(b.time), b.open, b.high, b.low, b.close, round(b.volume, 2), round(c.rsi[i]), round(c.ema[i], 4)]; }) };
    },
    find_extremes({ lookback = 100 } = {}) {
      const S = A().state, n = S.bars.length, k = Math.min(Math.max(2, Math.round(lookback)), n);
      let hi = n - k, lo = n - k;
      for (let i = n - k; i < n; i++) { if (S.bars[i].high > S.bars[hi].high) hi = i; if (S.bars[i].low < S.bars[lo].low) lo = i; }
      return { lookback: k, highest: { price: S.bars[hi].high, time: fmtTime(S.bars[hi].time), bars_ago: n - 1 - hi }, lowest: { price: S.bars[lo].low, time: fmtTime(S.bars[lo].time), bars_ago: n - 1 - lo } };
    },
    async set_symbol({ symbol }) { A().switchSymbol(symbol); await A().waitLoaded(); return { ok: true, symbol: A().state.symbol, bars: A().state.bars.length, source: A().state.source }; },
    async set_timeframe({ timeframe }) { await A().setTimeframe(String(timeframe).toLowerCase()); await A().waitLoaded(); return { ok: true, timeframe: A().state.tf }; },
    set_indicators(input = {}) {
      const S = A().state, fehler = [];
      const tu = (f) => { try { f(); } catch (e) { fehler.push(e.message); } };
      Object.entries(input).forEach(([k, v]) => { if (typeof v === 'boolean' && k in S.ind) tu(() => A().setIndicator(k, v)); });
      (input.remove || []).forEach((id) => tu(() => A().removeIndicator(+id)));
      (input.update || []).forEach((u) => tu(() => A().updateIndicator(+u.id, u.params || null, u.color)));
      (input.add || []).forEach((a) => tu(() => A().addIndicator(String(a.type), a.params || {}, a.color)));
      return { indicators: A().indicators(), fehler: fehler.length ? fehler : undefined };
    },
    list_indicators() {
      return A().katalog.KATALOG.map((d) => ({
        type: d.typ, name: d.titel, gruppe: d.gruppe, ort: d.ort === 'chart' ? 'Kurschart' : 'eigener Bereich', braucht: d.braucht || undefined,
        params: d.params.map((x) => (x.wahl ? { key: x.k, name: x.name, default: x.def, wahl: x.wahl, offen: x.offen || undefined } : { key: x.k, name: x.name, default: x.def, min: x.min, max: x.max })),
        zeigt: d.info, formel: d.herkunft,
      }));
    },
    run_script({ code }) { const out = A().runScript(code); if (out.startsWith('✕')) throw new Error(out); const bt = A().state.script.bt; return { result: out, backtest: bt ? bt.stats : null }; },
    clear_script() { A().clearScript(); return { ok: true }; },
    add_alarm({ price }) { A().addAlarm(+price); return { ok: true, price: +price }; },
    add_hline({ price }) { A().addHLine(+price); return { ok: true, price: +price }; },
    draw_fibonacci({ from_time, from_bars_ago, from_price, to_time, to_bars_ago, to_price }) {
      const a = point(from_time, from_bars_ago, from_price), b = point(to_time, to_bars_ago, to_price);
      A().layer.add({ type: 'fib', a, b });
      const lv = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map((l) => ({ level: l, price: round(b.price + (a.price - b.price) * l, 4) }));
      A().toast('Claude zeichnet', `Fibonacci ${round(a.price)} → ${round(b.price)}`, 'signal');
      return { ok: true, levels: lv };
    },
    draw_position({ entry, target, stop, time, bars_ago }) {
      const p = point(time, bars_ago, +entry);
      A().layer.add({ type: 'pos', entry: p, target: { ...p, price: +target }, stop: { ...p, price: +stop }, idx0: p.idx, idx1: p.idx + 20 });
      const crv = Math.abs(target - entry) / Math.abs(entry - stop);
      A().toast('Claude zeichnet', `${target >= entry ? 'Long' : 'Short'} · CRV 1 : ${crv.toFixed(2)}`, 'signal');
      return { ok: true, side: target >= entry ? 'long' : 'short', crv: round(crv) };
    },
    clear_drawings() { A().clearDrawings(); return { ok: true }; },
    undo_drawing() { const s = A().layer.removeLast(); return { removed: s ? s.type : null, remaining: A().layer.shapes.length }; },
    // Wendepunkte (Swing-Hochs/-Tiefs): ein Hoch, das in `strength` Kerzen links und rechts nicht übertroffen wird
    find_pivots({ lookback = 200, strength = 5 } = {}) {
      const S = A().state, n = S.bars.length, k = Math.min(Math.max(10, Math.round(lookback)), n), st = Math.max(1, Math.round(strength));
      const out = [];
      for (let i = Math.max(st, n - k); i < n - st; i++) {
        const b = S.bars[i]; let hi = true, lo = true;
        for (let j = i - st; j <= i + st && (hi || lo); j++) { if (j === i) continue; if (S.bars[j].high >= b.high) hi = false; if (S.bars[j].low <= b.low) lo = false; }
        if (hi) out.push({ type: 'high', price: b.high, bars_ago: n - 1 - i, time: fmtTime(b.time) });
        if (lo) out.push({ type: 'low', price: b.low, bars_ago: n - 1 - i, time: fmtTime(b.time) });
      }
      return { lookback: k, strength: st, last_price: S.lastPrice, pivots: out.slice(-40) };
    },
    draw_line({ from_bars_ago, from_price, to_bars_ago, to_price, extend_right = true, label = '', kind = 'trend', dashed = false }) {
      const a = point(undefined, from_bars_ago, +from_price), b = point(undefined, to_bars_ago, +to_price);
      A().layer.add({ type: 'line', a, b, extend: !!extend_right, label: String(label), kind, dashed: !!dashed });
      return { ok: true, from: a.price, to: b.price, label };
    },
    draw_level({ price, label = '', kind = 'support', from_bars_ago = 150 }) {
      const p = point(undefined, from_bars_ago, +price);
      A().layer.add({ type: 'level', price: +price, idx0: p.idx, label: String(label), kind });
      return { ok: true, price: +price, label, kind };
    },
    draw_zone({ price_low, price_high, from_bars_ago = 150, to_bars_ago = 0, label = '', kind = 'neutral' }) {
      const a = point(undefined, from_bars_ago), b = point(undefined, to_bars_ago);
      A().layer.add({ type: 'zone', p1: +price_low, p2: +price_high, idx0: a.idx, idx1: Math.max(b.idx, a.idx + 1), label: String(label), kind });
      return { ok: true, label };
    },
    draw_pattern({ points, label = '', closed = false, kind = 'pattern' }) {
      if (!Array.isArray(points) || points.length < 2) throw new Error('Mindestens zwei Punkte');
      const pts = points.map((q) => point(undefined, q.bars_ago, +q.price));
      A().layer.add({ type: 'poly', pts, closed: !!closed, label: String(label), kind });
      A().toast('Claude zeichnet', label || 'Muster', 'signal');
      return { ok: true, points: pts.length, label };
    },
    annotate({ bars_ago = 0, price, text, kind = 'neutral', above = true }) {
      const p = point(undefined, bars_ago, price !== undefined ? +price : undefined);
      A().layer.add({ type: 'note', idx: p.idx, price: p.price, text: String(text), kind, above: !!above });
      return { ok: true, text };
    },
    // Marktdaten: Formationen und Alarme (Analysen-Dienst von iC Delta)
    async detect_patterns({ sensitivity = 'normal', draw = true, only } = {}) {
      const S = A().state, tf = analysenTf();
      const d = await analysen('/api/formationen?' + new URLSearchParams({ wert: S.symbol, tf, staerke: { fein: 3, normal: 5, grob: 8 }[sensitivity] || 5 }));
      let f = d.formationen;
      if (Array.isArray(only) && only.length) f = f.filter((x) => only.includes(x.art));
      if (draw) { f.forEach(formationZeichnen); if (f.length) A().toast('Formationen', `${f.length} Treffer eingezeichnet`, 'signal'); }
      return { symbol: S.symbol, timeframe: tf, candles: d.kerzen_gesamt, count: f.length,
        patterns: f.map((x) => ({ type: x.art, name: x.name, direction: x.richtung, status: x.status, note: x.text || undefined,
          event: x.ereignis_t ? fmtTime(x.ereignis_t) : undefined, zone: x.zone || undefined,
          lines: x.linien.map((l) => ({ kind: l.art, from: fmtTime(l.t1), from_price: l.p1, to: fmtTime(l.t2), to_price: l.p2 })) })) };
    },
    async draw_formations({ formationen = [] } = {}) {   // Marktdaten: Treffer von detect_patterns (Server) einzeichnen
      formationen.forEach(formationZeichnen); if (formationen.length) A().toast('Formationen', `${formationen.length} eingezeichnet`, 'signal');
      return { ok: true, count: formationen.length };
    },
    async create_alert({ kind, direction = 'both', price, from_bars_ago, from_price, to_bars_ago, to_price, price_low, price_high, patterns, drawing_index, note = '' } = {}) {
      const S = A().state, n = S.bars.length;
      const regel = { wert: S.symbol, tf: analysenTf(), richtung: { above: 'ueber', below: 'unter', both: 'beide' }[direction] || 'beide', notiz: String(note), quelle: 'claude' };
      if (kind === 'drawing') {
        const s = A().layer.shapes[drawing_index];
        if (!s) throw new Error('Keine Zeichnung mit Index ' + drawing_index);
        if (s.type === 'line') Object.assign(regel, { art: 'linie', t1: S.times[s.a.idx], p1: s.a.price, t2: S.times[s.b.idx], p2: s.b.price, notiz: regel.notiz || s.label || '' });
        else if (s.type === 'level') Object.assign(regel, { art: 'linie', t1: S.times[Math.max(0, s.idx0 ?? 0)], p1: s.price, t2: S.times[n - 1], p2: s.price, notiz: regel.notiz || s.label || '' });
        else if (s.type === 'zone') Object.assign(regel, { art: 'zone', lo: Math.min(s.p1, s.p2), hi: Math.max(s.p1, s.p2), notiz: regel.notiz || s.label || '' });
        else throw new Error('Aus dieser Zeichnung lässt sich kein Alarm machen (nur Linie, Level, Zone)');
      } else if (kind === 'price') Object.assign(regel, { art: 'preis', preis: +price });
      else if (kind === 'line') {
        const a = point(undefined, from_bars_ago, +from_price), b = point(undefined, to_bars_ago, +to_price);
        Object.assign(regel, { art: 'linie', t1: a.time, p1: a.price, t2: b.time, p2: b.price });
      } else if (kind === 'zone') Object.assign(regel, { art: 'zone', lo: Math.min(+price_low, +price_high), hi: Math.max(+price_low, +price_high) });
      else if (kind === 'pattern') Object.assign(regel, { art: 'formation', arten: patterns || [] });
      else throw new Error('Unbekannte Art ' + kind);
      const r = await analysen('/api/alarme', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(regel) });
      A().toast('Alarm angelegt', `${r.symbol} · ${r.art} · ${r.tf}${S.tf === '4h' ? ' (4h wird als 1h geprüft)' : ''}`, 'signal');
      return { ok: true, id: r.id, symbol: r.symbol, type: r.art, timeframe: r.tf, direction: r.richtung, telegram_hint: 'Meldung per Telegram, sobald eingerichtet' };
    },
    async list_alerts() {
      const d = await analysen('/api/alarme');
      return { telegram: d.telegram.token && d.telegram.chat ? 'eingerichtet' : 'nicht eingerichtet', checked: d.geprueft,
        alerts: d.regeln.map((r) => ({ id: r.id, symbol: r.symbol, type: r.art, timeframe: r.tf, direction: r.richtung, active: r.aktiv, fired: r.ausgeloest, note: r.notiz,
          price: r.preis, line: r.art === 'linie' ? { from: fmtTime(r.t1), from_price: r.p1, to: fmtTime(r.t2), to_price: r.p2 } : undefined,
          zone: r.art === 'zone' ? [r.lo, r.hi] : undefined, patterns: r.arten })),
        recent: d.verlauf.slice(0, 10).map((v) => ({ time: v.zeit, symbol: v.wert, text: v.text, telegram: v.telegram })) };
    },
    async delete_alert({ id }) {
      const d = await analysen('/api/alarme?id=' + encodeURIComponent(id), { method: 'DELETE' });
      return { deleted: d.geloescht, id };
    },
    async optimize_strategy({ code, param1, param2, metric = 'return' }) {
      const r = await window.__lab.optimize({ code, param1, param2, metric });
      const { grid, ...rest } = r; return { ...rest, grid_rows: grid.length, grid: grid.length <= 120 ? grid : grid.filter((_, i) => i % Math.ceil(grid.length / 120) === 0) };
    },
    replay({ action = 'start' }) { const r = A().state.replay; if (action === 'start') A().replayStart(); else if (action === 'stop') A().replayStop(); else if (action === 'pause') A().replayPlay(false); else if (action === 'play') A().replayPlay(true); return { replay: r.on, position: r.pos }; },
    screenshot() {
      const cv = A().main.takeScreenshot();
      return { image_png_base64: cv.toDataURL('image/png').split(',')[1], width: cv.width, height: cv.height };
    },
  };

  // ---------------- Panel ----------------
  const panel = document.createElement('aside'); panel.id = 'agent';
  panel.innerHTML = `
    <div class="ag-head"><b>Claude im Chart</b><span id="agStatus" class="ag-status"><i></i><span>getrennt</span></span><button id="agNew" class="ghost small" title="Neue Unterhaltung">Neu</button><button id="agClose" class="ghost small" title="Schließen (Alt+C)">✕</button></div>
    <div id="agMsgs" class="ag-msgs"></div>
    <div class="ag-quick" id="agQuick"></div>
    <div class="ag-input"><textarea id="agText" rows="2" placeholder="Frag Claude … (Enter sendet, Umschalt+Enter = Zeile)"></textarea><button id="agSend" class="primary">Senden</button><button id="agStop" class="ghost" style="display:none">Stopp</button></div>
    <div class="ag-foot"><span id="agCost"></span><span class="spacer"></span><a href="#" id="agUrl" title="Adresse des Agenten-Servers ändern">${wsUrl}</a></div>`;
  $('#app').appendChild(panel);
  // Beispielprompts: kurze Beschriftung auf dem Knopf, der volle Text wird gesendet (und als Tooltip gezeigt)
  const QUICK = [
    ['Chart analysieren und einzeichnen', 'Analysiere den Chart und zeichne ein, was du siehst: Trendlinien, Unterstützungen, Widerstände, Muster – alles beschriftet.'],
    ['EMA-Strategie optimieren', 'Optimiere eine EMA-Kreuzungs-Strategie: schnelle EMA 5–30, langsame EMA 20–80. Schlägt sie Buy & Hold?'],
    ['Fibonacci über 150 Kerzen', 'Zeichne ein Fibonacci vom höchsten Hoch zum tiefsten Tief der letzten 150 Kerzen.'],
    ['Alarm 1 % über dem Kurs', 'Setze einen Alarm 1 % über dem aktuellen Kurs.'],
    ['Chart als Bild beschreiben', 'Schau dir den Chart als Bild an und beschreibe die Struktur.'],
  ];
  QUICK.forEach(([label, q]) => { const b = document.createElement('button'); b.textContent = label; b.title = q; b.onclick = () => { $('#agText').value = q; send(); }; $('#agQuick').appendChild(b); });

  const msgs = $('#agMsgs');
  let cur = null; // laufende Assistant-Blase
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // Sehr kleines Markdown: Codeblöcke, Inline-Code, fett, Listen, Absätze
  function md(src) {
    const parts = src.split(/```/); let out = '';
    parts.forEach((p, i) => {
      if (i % 2) { out += `<pre><code>${esc(p.replace(/^\w*\n/, ''))}</code></pre>`; return; }
      let h = esc(p).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
      h = h.split(/\n{2,}/).map((para) => (/^(\s*[-*] .*\n?)+$/.test(para) ? '<ul>' + para.trim().split('\n').map((l) => `<li>${l.replace(/^\s*[-*] /, '')}</li>`).join('') + '</ul>' : `<p>${para.replace(/\n/g, '<br>')}</p>`)).join('');
      out += h;
    });
    return out;
  }
  function bubble(role, html) { const d = document.createElement('div'); d.className = 'ag-msg ' + role; d.innerHTML = html; msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d; }
  function toolChip(name, input, state = 'run') {
    const d = document.createElement('div'); d.className = 'ag-tool ' + state;
    const short = name.replace(/^mcp__(chart|delta)__/, '');   // Marktdaten: Werkzeugserver heißt jetzt delta
    const arg = input && Object.keys(input).length ? ' ' + esc(JSON.stringify(input)).slice(0, 90) : '';
    d.innerHTML = `<i></i><b>${esc(short)}</b><span>${arg}</span>`; msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d;
  }
  let curText = '';
  function appendText(t) {
    if (!cur) { cur = bubble('assistant', ''); curText = ''; }
    curText += t; cur.innerHTML = md(curText); msgs.scrollTop = msgs.scrollHeight;
  }
  function endAssistant() { cur = null; curText = ''; }

  // ---------------- Verbindung ----------------
  let ws = null, busy = false, retry = 0;
  const pendingTools = new Map();
  function setStatus(kind, text) { const s = $('#agStatus'); s.className = 'ag-status ' + kind; s.querySelector('span').textContent = text; }
  function connect() {
    try { ws = new WebSocket(wsUrl); } catch (e) { setStatus('off', 'Adresse ungültig'); return; }
    setStatus('', 'verbinde …');
    ws.onopen = () => { retry = 0; setStatus('live', 'verbunden'); };
    ws.onclose = () => { setStatus('off', 'Server aus · agent/server.mjs starten'); setBusy(false); setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++)); };
    ws.onerror = () => {};
    ws.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'status') { setStatus(m.ready ? 'live' : 'off', m.text || (m.ready ? 'bereit' : 'nicht bereit')); if (m.model) $('#agCost').textContent = m.model; }
      else if (m.type === 'text_delta') appendText(m.text);
      else if (m.type === 'assistant_done') endAssistant();
      else if (m.type === 'tool_use') { endAssistant(); toolChip(m.name, m.input, 'done'); }
      else if (m.type === 'tool_call') {
        endAssistant();
        const chip = toolChip(m.name, m.input, 'run');
        try {
          const fn = TOOLS[m.name]; if (!fn) throw new Error('Unbekanntes Werkzeug ' + m.name);
          const result = await fn(m.input || {});
          ws.send(JSON.stringify({ type: 'tool_result', id: m.id, ok: true, result }));
          chip.className = 'ag-tool done';
        } catch (e) { ws.send(JSON.stringify({ type: 'tool_result', id: m.id, ok: false, error: e.message })); chip.className = 'ag-tool err'; chip.querySelector('span').textContent = ' ' + e.message; }
      }
      else if (m.type === 'result') {
        endAssistant(); setBusy(false);
        const c = m.cost !== undefined ? `${m.cost.toFixed(4).replace('.', ',')} $` : '';
        $('#agCost').textContent = [m.model, c, m.turns ? `${m.turns} Züge` : '', m.duration ? `${(m.duration / 1000).toFixed(1).replace('.', ',')} s` : ''].filter(Boolean).join(' · ');
        if (m.error) bubble('error', esc(m.error));
      }
      else if (m.type === 'error') { endAssistant(); setBusy(false); bubble('error', esc(m.message)); }
    };
  }
  function setBusy(b) { busy = b; $('#agSend').style.display = b ? 'none' : ''; $('#agStop').style.display = b ? '' : 'none'; $('#agText').disabled = b; }
  function send() {
    const t = $('#agText').value.trim(); if (!t || busy) return;
    if (!ws || ws.readyState !== 1) { bubble('error', 'Keine Verbindung zum Agenten-Server. Auf dem Rechner mit der Claude-Anmeldung starten: <code>cd agent && npm start</code>'); return; }
    bubble('user', md(t)); $('#agText').value = ''; setBusy(true);
    ws.send(JSON.stringify({ type: 'user', text: t, context: TOOLS.get_state() }));
  }
  $('#agSend').onclick = send;
  $('#agStop').onclick = () => ws && ws.send(JSON.stringify({ type: 'abort' }));
  $('#agNew').onclick = () => { msgs.innerHTML = ''; ws && ws.send(JSON.stringify({ type: 'new_session' })); };
  $('#agText').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  $('#agUrl').onclick = (e) => { e.preventDefault(); const u = prompt('WebSocket-Adresse des Agenten-Servers', wsUrl); if (u) { wsUrl = u; try { localStorage.setItem('sarge.agentUrl', u); } catch (x) { /* egal */ } $('#agUrl').textContent = u; if (ws) ws.close(); } };

  // Umschalten
  const btn = document.createElement('button'); btn.id = 'btnAgent'; btn.className = 'pill'; btn.textContent = '✦ Claude'; btn.title = 'Claude im Chart (Alt+C)';
  $('#btnReplay').before(btn);
  function toggle(on) { const open = on ?? !document.body.classList.contains('agent-open'); document.body.classList.toggle('agent-open', open); btn.classList.toggle('on', open); if (open) { $('#agText').focus(); if (!ws) connect(); } }
  btn.onclick = () => toggle(); $('#agClose').onclick = () => toggle(false);
  document.addEventListener('keydown', (e) => { if (e.altKey && e.code === 'KeyC') toggle(); });
  if (params.get('claude') === '1') toggle(true);
  window.__agent = { TOOLS, send, toggle, connect };
})();
