/*
 * indikator-katalog.js – Verzeichnis aller Indikatoren von iC Charts (eigener Code).
 *
 * Jeder Eintrag beschreibt einen Indikator vollständig: einstellbare Werte, Ort (Kurschart oder eigener Bereich),
 * Linien, Skala, Datenbedarf und Quelle der Formel. Aus diesem Verzeichnis entstehen die Auswahl, das
 * Einstellungsfenster, die Legende und die Werkzeuge für Claude. Gerechnet wird in indicators.js.
 *
 * rechne(bars, p, ctx) liefert ein Objekt mit gleich langen Reihen (null = kein Wert) und optional
 *   hinweis (Text für die Legende) oder fehlt (Grund, warum nichts gezeigt wird).
 * ctx: { tf, tfSec, kind ('stock'|'crypto'), markt ('DE'|'US'|'KRYPTO'), dom, of (Orderflow), vergleich(sym), symbole }
 * Läuft im Browser (window.IndikatorKatalog) und in Node für die Tests.
 */
(function (root) {
  'use strict';
  const I = root.Indicators || (typeof require !== 'undefined' ? require('./indicators.js') : null);
  const isNum = I.isNum;

  const QUELLEN = ['close', 'open', 'high', 'low', 'hl2', 'hlc3'];
  const QNAME = { close: 'Schluss', open: 'Eröffnung', high: 'Hoch', low: 'Tief', hl2: '(H+T)/2', hlc3: '(H+T+S)/3' };
  function reihe(bars, q) {
    if (q === 'hl2') return bars.map((b) => (b.high + b.low) / 2);
    if (q === 'hlc3') return bars.map((b) => (b.high + b.low + b.close) / 3);
    return bars.map((b) => b[q] ?? b.close);
  }
  const n = (name, def, min = 1, max = 1000, step = 1) => ({ k: null, name, def, min, max, step });
  const P = (k, o) => Object.assign({}, o, { k });
  const quelle = P('quelle', { name: 'Quelle', def: 'close', wahl: QUELLEN, namen: QNAME });
  const q = (p) => (p.quelle && p.quelle !== 'close' ? ' ' + QNAME[p.quelle] : '');
  const z = (x) => String(x).replace('.', ',');   // Dezimalkomma in Namen
  // Punkte in zwei Reihen (steigend/fallend): Lightweight Charts zeichnet Punktmarken nicht mit wechselnder Farbe je Punkt
  const teilen = (werte, wahr) => [werte.map((v, i) => (wahr(i) ? v : null)), werte.map((v, i) => (wahr(i) ? null : v))];

  /**
   * Volumen der letzten 15 Minuten ist bei US-Aktien nur IEX (rund 4 % des Marktes, vorläufig) – siehe DATENQUALITAET.md.
   * Volumen-Indikatoren blenden diese Kerzen aus, statt eine falsche Linie zu zeigen.
   */
  function volumenSchnitt(bars, ctx, werte) {
    if (ctx.markt !== 'US' || !bars.length) return { werte, hinweis: '' };
    const ende = bars[bars.length - 1].time + ctx.tfSec, grenze = ende - 900;
    let k = 0;
    const aus = Object.fromEntries(Object.entries(werte).map(([name, arr]) => [name, Array.isArray(arr) ? arr.map((v, i) => {
      if (i < bars.length && bars[i].time + ctx.tfSec > grenze) { k = Math.max(k, bars.length - i); return null; }
      return v;
    }) : arr]));
    return { werte: aus, hinweis: k ? `letzte ${k} Kerze${k === 1 ? '' : 'n'} ausgeblendet: US-Volumen der letzten 15 Min. nur IEX` : '' };
  }

  const KATALOG = [
    // ---------------- Durchschnitte und Bänder (Kurschart) ----------------
    {
      typ: 'sma', name: 'SMA', titel: 'Einfacher gleitender Durchschnitt', gruppe: 'Durchschnitte', ort: 'chart', farbe: '#ffd166',
      params: [P('n', n('Länge', 50)), quelle], kurz: (p) => `SMA ${p.n}${q(p)}`,
      rechne: (bars, p) => ({ wert: I.sma(reihe(bars, p.quelle), p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f }],
      info: 'Durchschnitt der letzten n Kurse, jeder Kurs zählt gleich.', herkunft: 'Lehrbuchdefinition',
    },
    {
      typ: 'ema', name: 'EMA', titel: 'Exponentieller gleitender Durchschnitt', gruppe: 'Durchschnitte', ort: 'chart', farbe: '#ff9f43',
      params: [P('n', n('Länge', 21)), quelle], kurz: (p) => `EMA ${p.n}${q(p)}`,
      rechne: (bars, p) => ({ wert: I.ema(reihe(bars, p.quelle), p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f }],
      info: 'Gewichtet jüngere Kurse stärker (α = 2 / (n + 1)), reagiert schneller als die SMA.', herkunft: 'Lehrbuchdefinition, Startwert = SMA der ersten n Kurse',
    },
    {
      typ: 'wma', name: 'WMA', titel: 'Linear gewichteter Durchschnitt', gruppe: 'Durchschnitte', ort: 'chart', farbe: '#f4a6ff',
      params: [P('n', n('Länge', 20)), quelle], kurz: (p) => `WMA ${p.n}${q(p)}`,
      rechne: (bars, p) => ({ wert: I.wma(reihe(bars, p.quelle), p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f }],
      info: 'Gewichte n, n−1, …, 1: der jüngste Kurs zählt am meisten.', herkunft: 'Lehrbuchdefinition',
    },
    {
      typ: 'hma', name: 'HMA', titel: 'Hull Moving Average', gruppe: 'Durchschnitte', ort: 'chart', farbe: '#7cd4ff',
      params: [P('n', n('Länge', 21, 2)), quelle], kurz: (p) => `HMA ${p.n}${q(p)}`,
      rechne: (bars, p) => ({ wert: I.hma(reihe(bars, p.quelle), p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f }],
      info: 'Glatter Durchschnitt mit wenig Verzögerung: WMA(2·WMA(n/2) − WMA(n), √n).', herkunft: 'Alan Hull, 2005',
    },
    {
      typ: 'bb', name: 'Bollinger-Bänder', titel: 'Bollinger-Bänder', gruppe: 'Bänder und Kanäle', ort: 'chart', farbe: '#4d8dff',
      params: [P('n', n('Länge', 20, 2)), P('k', { name: 'Abstand (σ)', def: 2, min: 0.1, max: 10, step: 0.1 }), quelle],
      kurz: (p) => `BB ${p.n} ${z(p.k)}${q(p)}`,
      rechne: (bars, p) => I.bollinger(reihe(bars, p.quelle), p.n, p.k),
      linien: (f) => [{ r: 'upper', farbe: f, breite: 1 }, { r: 'mid', farbe: f, breite: 1, gestrichelt: true, alpha: 0.6 }, { r: 'lower', farbe: f, breite: 1 }],
      flaeche: [{ oben: 'upper', unten: 'lower', alpha: 0.05 }],
      info: 'SMA ± k Standardabweichungen: zeigt, wie weit der Kurs gerade schwankt.', herkunft: 'John Bollinger, 1980er; „Bollinger on Bollinger Bands“, 2001',
    },
    {
      typ: 'keltner', name: 'Keltner-Kanal', titel: 'Keltner-Kanal', gruppe: 'Bänder und Kanäle', ort: 'chart', farbe: '#36c5b0',
      params: [P('n', n('Länge (EMA)', 20, 2)), P('mult', { name: 'Abstand (× ATR)', def: 2, min: 0.1, max: 10, step: 0.1 }), P('atr', n('ATR-Länge', 10))],
      kurz: (p) => `KC ${p.n} ${z(p.mult)}`,
      rechne: (bars, p) => I.keltner(bars, p.n, p.mult, p.atr),
      linien: (f) => [{ r: 'upper', farbe: f, breite: 1 }, { r: 'mid', farbe: f, breite: 1, gestrichelt: true, alpha: 0.6 }, { r: 'lower', farbe: f, breite: 1 }],
      flaeche: [{ oben: 'upper', unten: 'lower', alpha: 0.05 }],
      info: 'EMA ± Vielfaches der ATR: Kanal aus der echten Schwankungsbreite.', herkunft: 'Chester Keltner, 1960; heutige Form mit ATR nach Linda Bradford Raschke',
    },
    {
      typ: 'donchian', name: 'Donchian-Kanal', titel: 'Donchian-Kanal', gruppe: 'Bänder und Kanäle', ort: 'chart', farbe: '#9aa7ff',
      params: [P('n', n('Länge', 20))], kurz: (p) => `DC ${p.n}`,
      rechne: (bars, p) => I.donchian(bars, p.n),
      linien: (f) => [{ r: 'upper', farbe: f, breite: 1, stufe: true }, { r: 'mid', farbe: f, breite: 1, gestrichelt: true, alpha: 0.5 }, { r: 'lower', farbe: f, breite: 1, stufe: true }],
      flaeche: [{ oben: 'upper', unten: 'lower', alpha: 0.04 }],
      info: 'Höchstes Hoch und tiefstes Tief der letzten n Kerzen. Ausbruch = neues n-Kerzen-Hoch oder -Tief.', herkunft: 'Richard Donchian, 1960er',
    },
    {
      typ: 'ichimoku', name: 'Ichimoku', titel: 'Ichimoku Kinko Hyo', gruppe: 'Trend', ort: 'chart', farbe: '#e8e8e8',
      params: [P('tenkan', n('Tenkan', 9)), P('kijun', n('Kijun', 26)), P('spanb', n('Senkou B', 52)), P('v', n('Verschiebung', 26))],
      kurz: (p) => `Ichimoku ${p.tenkan} ${p.kijun} ${p.spanb}`,
      rechne: (bars, p) => I.ichimoku(bars, p.tenkan, p.kijun, p.spanb, p.v),
      linien: () => [{ r: 'tenkan', farbe: '#4fa3ff', breite: 1, titel: 'Tenkan' }, { r: 'kijun', farbe: '#ff6b7a', breite: 1, titel: 'Kijun' },
        { r: 'chikou', farbe: '#b8a2ff', breite: 1, alpha: 0.7, titel: 'Chikou' },
        { r: 'spanA', farbe: '#2fcf7a', breite: 1, alpha: 0.55, titel: 'Senkou A', zukunft: true }, { r: 'spanB', farbe: '#f05454', breite: 1, alpha: 0.55, titel: 'Senkou B', zukunft: true }],
      flaeche: [{ oben: 'spanA', unten: 'spanB', alpha: 0.12, farbeAuf: '#2fcf7a', farbeAb: '#f05454' }],
      info: 'Wolke (Kumo) aus Senkou A und B, Trendlinien Tenkan und Kijun, Chikou als verschobener Schlusskurs.', herkunft: 'Goichi Hosoda, veröffentlicht 1969',
    },
    {
      typ: 'psar', name: 'Parabolic SAR', titel: 'Parabolic Stop and Reverse', gruppe: 'Trend', ort: 'chart', farbe: '#f5b942',
      params: [P('step', { name: 'Schritt', def: 0.02, min: 0.001, max: 0.5, step: 0.001 }), P('max', { name: 'Höchstwert', def: 0.2, min: 0.01, max: 1, step: 0.01 })],
      kurz: (p) => `SAR ${z(p.step)} ${z(p.max)}`,
      rechne: (bars, p) => { const r = I.psar(bars, p.step, p.max), [auf, ab] = teilen(r.line, (i) => r.dir[i] === 1); return { ...r, auf, ab }; },
      linien: () => [{ r: 'auf', punkte: true, farbe: '#2fcf7a', ohneLegende: true }, { r: 'ab', punkte: true, farbe: '#f05454', ohneLegende: true }, { r: 'line', farbeJe: 'dir', nurLegende: true }],
      info: 'Nachziehender Stopp: Punkte unter dem Kurs = Aufwärtstrend, darüber = Abwärtstrend.', herkunft: 'J. Welles Wilder, „New Concepts in Technical Trading Systems“, 1978',
    },
    {
      typ: 'st', name: 'SuperTrend', titel: 'SuperTrend', gruppe: 'Trend', ort: 'chart', farbe: '#3ddc97',
      params: [P('n', n('ATR-Länge', 10)), P('mult', { name: 'Faktor', def: 3, min: 0.1, max: 20, step: 0.1 })],
      kurz: (p) => `SuperTrend ${p.n} ${z(p.mult)}`,
      rechne: (bars, p) => I.supertrend(bars, p.n, p.mult),
      linien: () => [{ r: 'line', stufe: true, farbeJe: 'dir' }],
      info: 'Trendlinie aus (H+T)/2 ± Faktor · ATR, kippt beim Schluss jenseits der Linie.', herkunft: 'ATR-Band nach Wilder (1978); freie, verbreitete Konstruktion',
    },
    {
      typ: 'vwap', name: 'VWAP', titel: 'VWAP mit Anker und Bändern', gruppe: 'Niveaus', ort: 'chart', farbe: '#ff6bcb', braucht: 'volumen',
      params: [
        P('anker', { name: 'Start', def: 'auto', wahl: ['auto', 'tag', 'woche', 'monat', 'hoch', 'tief', 'zeit'],
          namen: { auto: 'je Sitzung', tag: 'je Tag', woche: 'je Woche', monat: 'je Monat', hoch: 'am höchsten Hoch', tief: 'am tiefsten Tief', zeit: 'ab Zeitpunkt' } }),
        Object.assign(P('rueck', n('Suche Hoch/Tief (Kerzen)', 200, 2, 5000)), { nurBei: { anker: ['hoch', 'tief'] } }),
        P('zeit', { name: 'Zeitpunkt (Unix)', def: 0, min: 0, max: 4e9, step: 60, nurBei: { anker: ['zeit'] } }),
        P('baender', { name: 'Bänder', def: '0', wahl: ['0', '1', '2'], namen: { 0: 'keine', 1: '± 1 σ', 2: '± 1 σ und ± 2 σ' } }),
      ],
      kurz: (p) => (p.anker === 'auto' && p.baender === '0' ? 'VWAP' : `VWAP ${({ auto: 'Sitzung', tag: 'Tag', woche: 'Woche', monat: 'Monat', hoch: 'ab Hoch', tief: 'ab Tief', zeit: 'ab Anker' })[p.anker]}`),
      rechne: (bars, p, ctx) => {
        let anchors;
        if (p.anker === 'hoch' || p.anker === 'tief') {
          const s = Math.max(0, bars.length - p.rueck); let k = s;
          for (let i = s; i < bars.length; i++) if (p.anker === 'hoch' ? bars[i].high >= bars[k].high : bars[i].low <= bars[k].low) k = i;
          anchors = [k];
        } else if (p.anker === 'zeit') {
          const k = bars.findIndex((b) => b.time >= p.zeit); anchors = k < 0 ? [] : [k];
        } else anchors = I.periodStarts(bars, p.anker === 'auto' ? (ctx.tf === '1d' ? 'monat' : 'tag') : p.anker);
        const r = I.anchoredVwap(bars, anchors, 1, 2), b = +p.baender;
        const w = { vwap: r.vwap, u1: b >= 1 ? r.u1 : null, l1: b >= 1 ? r.l1 : null, u2: b >= 2 ? r.u2 : null, l2: b >= 2 ? r.l2 : null };
        const v = volumenSchnitt(bars, ctx, w);
        return { ...v.werte, anker: anchors.length === 1 ? anchors[0] : null, hinweis: v.hinweis };
      },
      linien: (f) => [{ r: 'vwap', farbe: f, gepunktet: true }, { r: 'u1', farbe: f, breite: 1, alpha: 0.55 }, { r: 'l1', farbe: f, breite: 1, alpha: 0.55 },
        { r: 'u2', farbe: f, breite: 1, alpha: 0.35, gestrichelt: true }, { r: 'l2', farbe: f, breite: 1, alpha: 0.35, gestrichelt: true }],
      flaeche: [{ oben: 'u1', unten: 'l1', alpha: 0.05 }],
      info: 'Volumengewichteter Durchschnittspreis ab einem Startpunkt, Bänder = volumengewichtete Standardabweichung.', herkunft: 'VWAP: Lehrbuchdefinition; Bänder nach der Varianzformel',
    },
    {
      typ: 'pivots', name: 'Pivot-Punkte', titel: 'Pivot-Punkte', gruppe: 'Niveaus', ort: 'chart', farbe: '#c9b37e',
      params: [
        P('zeitraum', { name: 'Zeitraum', def: 'auto', wahl: ['auto', 'tag', 'woche', 'monat'], namen: { auto: 'automatisch', tag: 'Tag', woche: 'Woche', monat: 'Monat' } }),
        P('methode', { name: 'Methode', def: 'klassisch', wahl: ['klassisch', 'fibonacci'], namen: { klassisch: 'klassisch', fibonacci: 'Fibonacci' } }),
      ],
      kurz: (p) => `Pivots ${p.methode === 'fibonacci' ? 'Fib ' : ''}${p.zeitraum === 'auto' ? '' : p.zeitraum}`.trim(),
      rechne: (bars, p, ctx) => {
        const art = p.zeitraum === 'auto' ? (ctx.tf === '1d' ? 'monat' : ctx.tf === '4h' ? 'woche' : 'tag') : p.zeitraum, r = I.pivots(bars, art, p.methode);
        return r.p.some(isNum) ? r : { fehlt: `braucht einen abgeschlossenen ${({ tag: 'Vortag', woche: 'Vorwoche', monat: 'Vormonat' })[art]} in den Daten` };
      },
      linien: (f) => [{ r: 'r3', farbe: '#f05454', breite: 1, stufe: true, alpha: 0.5, titel: 'R3' }, { r: 'r2', farbe: '#f05454', breite: 1, stufe: true, alpha: 0.7, titel: 'R2' },
        { r: 'r1', farbe: '#f05454', breite: 1, stufe: true, titel: 'R1' }, { r: 'p', farbe: f, breite: 1, stufe: true, titel: 'P' },
        { r: 's1', farbe: '#2fcf7a', breite: 1, stufe: true, titel: 'S1' }, { r: 's2', farbe: '#2fcf7a', breite: 1, stufe: true, alpha: 0.7, titel: 'S2' },
        { r: 's3', farbe: '#2fcf7a', breite: 1, stufe: true, alpha: 0.5, titel: 'S3' }],
      info: 'Niveaus aus Hoch, Tief und Schluss des vorigen Tages (Woche, Monat).', herkunft: 'Floor-Trader-Methode; Fibonacci-Variante mit 0,382 / 0,618 / 1,0',
    },
    {
      typ: 'linreg', name: 'Regressionskanal', titel: 'Linearer Regressionskanal', gruppe: 'Trend', ort: 'chart', farbe: '#9ad0ff',
      params: [P('n', n('Kerzen', 100, 5, 3000)), P('k', { name: 'Abstand (σ)', def: 2, min: 0.1, max: 10, step: 0.1 }), quelle],
      kurz: (p) => `Regression ${p.n}`,
      rechne: (bars, p) => { const r = I.linregChannel(reihe(bars, p.quelle), Math.min(p.n, bars.length), p.k); return { ...r, steigungProz: isNum(r.slope) && bars.length ? (r.slope / bars[bars.length - 1].close) * 100 : null }; },
      linien: (f) => [{ r: 'upper', farbe: f, breite: 1, voll: true }, { r: 'mid', farbe: f, breite: 1, gestrichelt: true, voll: true }, { r: 'lower', farbe: f, breite: 1, voll: true }],
      flaeche: [{ oben: 'upper', unten: 'lower', alpha: 0.05 }],
      info: 'Ausgleichsgerade über die letzten n Kerzen (kleinste Quadrate) mit Band aus der Streuung.', herkunft: 'Methode der kleinsten Quadrate (Gauß/Legendre)',
    },
    {
      typ: 'zigzag', name: 'ZigZag', titel: 'ZigZag', gruppe: 'Trend', ort: 'chart', farbe: '#f5b942',
      params: [P('pct', { name: 'Mindestbewegung %', def: 5, min: 0.1, max: 50, step: 0.1 })], kurz: (p) => `ZigZag ${z(p.pct)} %`,
      rechne: (bars, p) => ({ punkte: I.zigzag(bars, p.pct) }),
      linien: (f) => [{ r: 'punkte', farbe: f, breite: 2, zickzack: true }],
      info: 'Verbindet Hochs und Tiefs, sobald der Kurs um mindestens x % dreht. Der letzte Schenkel ist vorläufig.', herkunft: 'Arthur Merrill, „Filtered Waves“, 1977',
    },
    {
      typ: 'vprofil', name: 'Volumenprofil', titel: 'Volumenprofil mit POC und Wertebereich', gruppe: 'Volumen und Orderflow', ort: 'chart', farbe: '#8b93a1', braucht: 'volumen',
      params: [
        P('bereich', { name: 'Bereich', def: 'sichtbar', wahl: ['sichtbar', 'tag'], namen: { sichtbar: 'sichtbarer Ausschnitt', tag: 'letzte Sitzung' } }),
        P('stufen', n('Preisstufen', 40, 8, 200)),
        P('anteil', { name: 'Wertebereich %', def: 70, min: 10, max: 99, step: 1 }),
        P('daten', { name: 'Daten', def: 'auto', wahl: ['auto', 'kerzen'], namen: { auto: 'echte Abschlüsse, wenn vorhanden', kerzen: 'aus Kerzen geschätzt' } }),
      ],
      kurz: (p) => `Profil ${p.bereich === 'tag' ? 'Sitzung' : 'sichtbar'}`,
      rechne: () => ({}),   // wird im Chart je Ausschnitt gerechnet (indikator-zeichnen.js / app.js)
      linien: () => [], profil: true,
      info: 'Gehandeltes Volumen je Preisstufe. POC = Preis mit dem meisten Volumen, Wertebereich = Zone mit 70 % des Volumens. Xetra aus allen Xetra-Abschlüssen, US aus IEX-Abschlüssen (Stichprobe), Krypto aus Kerzen mit Taker-Kaufvolumen.',
      herkunft: 'Market Profile, J. Peter Steidlmayer (CBOT, 1985)',
    },

    // ---------------- Oszillatoren und eigene Bereiche ----------------
    {
      typ: 'rsi', name: 'RSI', titel: 'Relative-Stärke-Index', gruppe: 'Oszillatoren', ort: 'panel', farbe: '#b388ff',
      params: [P('n', n('Länge', 14, 2)), quelle], kurz: (p) => `RSI ${p.n}${q(p)}`,
      rechne: (bars, p) => ({ wert: I.rsi(reihe(bars, p.quelle), p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { min: 0, max: 100, niveaus: [70, 50, 30] }, nachkomma: 2,
      info: 'Verhältnis der Auf- zu den Abwärtsbewegungen, 0–100. Über 70 überkauft, unter 30 überverkauft.', herkunft: 'J. Welles Wilder, 1978',
    },
    {
      typ: 'macd', name: 'MACD', titel: 'Moving Average Convergence Divergence', gruppe: 'Oszillatoren', ort: 'panel', farbe: '#22d3ee',
      params: [P('fast', n('Schnell', 12)), P('slow', n('Langsam', 26)), P('signal', n('Signal', 9)), quelle],
      kurz: (p) => `MACD ${p.fast} ${p.slow} ${p.signal}`,
      rechne: (bars, p) => I.macd(reihe(bars, p.quelle), p.fast, p.slow, p.signal),
      linien: (f) => [{ r: 'hist', histo: 'macd' }, { r: 'macd', farbe: f, preis: true }, { r: 'signal', farbe: '#ff9f43', breite: 1.5 }], skala: { nulllinie: true },
      info: 'Differenz zweier EMAs, Signallinie und Histogramm.', herkunft: 'Gerald Appel, 1970er',
    },
    {
      typ: 'stoch', name: 'Stochastik', titel: 'Stochastik-Oszillator', gruppe: 'Oszillatoren', ort: 'panel', farbe: '#5ad7e6',
      params: [P('n', n('Länge', 14)), P('k', n('%K-Glättung', 3)), P('d', n('%D', 3))], kurz: (p) => `Stoch ${p.n} ${p.k} ${p.d}`,
      rechne: (bars, p) => I.stoch(bars, p.n, p.k, p.d),
      linien: (f) => [{ r: 'k', farbe: f, preis: true, titel: '%K' }, { r: 'd', farbe: '#ff9f43', breite: 1.5, titel: '%D' }], skala: { min: 0, max: 100, niveaus: [80, 20] }, nachkomma: 2,
      info: 'Lage des Schlusskurses in der Spanne der letzten n Kerzen, 0–100.', herkunft: 'George Lane, 1950er',
    },
    {
      typ: 'willr', name: 'Williams %R', titel: 'Williams %R', gruppe: 'Oszillatoren', ort: 'panel', farbe: '#ffa8d2',
      params: [P('n', n('Länge', 14))], kurz: (p) => `%R ${p.n}`,
      rechne: (bars, p) => ({ wert: I.willr(bars, p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { min: -100, max: 0, niveaus: [-20, -80] }, nachkomma: 2,
      info: 'Abstand des Schlusskurses zum n-Kerzen-Hoch, −100 … 0. Über −20 überkauft, unter −80 überverkauft.', herkunft: 'Larry Williams, 1973',
    },
    {
      typ: 'cci', name: 'CCI', titel: 'Commodity Channel Index', gruppe: 'Oszillatoren', ort: 'panel', farbe: '#e6c75a',
      params: [P('n', n('Länge', 20, 2))], kurz: (p) => `CCI ${p.n}`,
      rechne: (bars, p) => ({ wert: I.cci(bars, p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { niveaus: [100, 0, -100] }, nachkomma: 1,
      info: 'Abweichung des typischen Preises von seinem Durchschnitt, gemessen an der mittleren Abweichung.', herkunft: 'Donald Lambert, 1980',
    },
    {
      typ: 'roc', name: 'ROC', titel: 'Rate of Change', gruppe: 'Oszillatoren', ort: 'panel', farbe: '#8fd16a',
      params: [P('n', n('Länge', 12)), quelle], kurz: (p) => `ROC ${p.n}`,
      rechne: (bars, p) => ({ wert: I.roc(reihe(bars, p.quelle), p.n) }),
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { nulllinie: true }, nachkomma: 2, einheit: ' %',
      info: 'Kursveränderung gegenüber vor n Kerzen in Prozent.', herkunft: 'Lehrbuchdefinition',
    },
    {
      typ: 'dmi', name: 'ADX / DMI', titel: 'Directional Movement Index mit ADX', gruppe: 'Trend', ort: 'panel', farbe: '#f5b942',
      params: [P('n', n('Länge', 14, 2))], kurz: (p) => `ADX ${p.n}`,
      rechne: (bars, p) => I.dmi(bars, p.n),
      linien: (f) => [{ r: 'adx', farbe: f, preis: true, titel: 'ADX' }, { r: 'plus', farbe: '#2fcf7a', breite: 1.5, titel: '+DI' }, { r: 'minus', farbe: '#f05454', breite: 1.5, titel: '−DI' }],
      skala: { min: 0, max: 100, niveaus: [25] }, nachkomma: 1,
      info: 'Trendstärke (ADX, über 25 = deutlicher Trend) und Richtung (+DI gegen −DI).', herkunft: 'J. Welles Wilder, 1978',
    },
    {
      typ: 'aroon', name: 'Aroon', titel: 'Aroon', gruppe: 'Trend', ort: 'panel', farbe: '#2fcf7a',
      params: [P('n', n('Länge', 25, 2))], kurz: (p) => `Aroon ${p.n}`,
      rechne: (bars, p) => I.aroon(bars, p.n),
      linien: () => [{ r: 'up', farbe: '#2fcf7a', preis: true, titel: 'hoch' }, { r: 'down', farbe: '#f05454', titel: 'tief' }], skala: { min: 0, max: 100, niveaus: [70, 30] }, nachkomma: 0,
      info: 'Wie lange liegt das letzte n-Kerzen-Hoch bzw. -Tief zurück? Hoch nahe 100 = frischer Aufwärtstrend.', herkunft: 'Tushar Chande, 1995',
    },
    {
      typ: 'atr', name: 'ATR', titel: 'Average True Range', gruppe: 'Volatilität', ort: 'panel', farbe: '#c0c7c6',
      params: [P('n', n('Länge', 14)), P('anzeige', { name: 'Anzeige', def: 'preis', wahl: ['preis', 'prozent'], namen: { preis: 'in Preis', prozent: 'in % vom Kurs' } })],
      kurz: (p) => `ATR ${p.n}${p.anzeige === 'prozent' ? ' %' : ''}`,
      rechne: (bars, p) => { const a = I.atr(bars, p.n); return { wert: p.anzeige === 'prozent' ? a.map((v, i) => (isNum(v) ? (v / bars[i].close) * 100 : null)) : a }; },
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }],
      info: 'Durchschnittliche Schwankung je Kerze inklusive Kurslücken. Grundlage für Stopps.', herkunft: 'J. Welles Wilder, 1978',
    },
    {
      typ: 'bbw', name: 'BB-Breite / %B', titel: 'Bollinger-Bandbreite und %B', gruppe: 'Volatilität', ort: 'panel', farbe: '#4d8dff',
      params: [P('n', n('Länge', 20, 2)), P('k', { name: 'Abstand (σ)', def: 2, min: 0.1, max: 10, step: 0.1 }),
        P('anzeige', { name: 'Anzeige', def: 'breite', wahl: ['breite', 'pctb'], namen: { breite: 'Bandbreite in %', pctb: '%B (Lage im Band)' } })],
      kurz: (p) => (p.anzeige === 'pctb' ? `%B ${p.n} ${p.k}` : `BB-Breite ${p.n} ${p.k}`),
      rechne: (bars, p) => { const r = I.bbWidth(bars.map((b) => b.close), p.n, p.k); return { wert: p.anzeige === 'pctb' ? r.pctB : r.width }; },
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: (p) => (p.anzeige === 'pctb' ? { niveaus: [1, 0.5, 0] } : {}), nachkomma: 2,
      info: 'Bandbreite: wie weit die Bänder offen sind (Enge = Ruhe vor dem Ausbruch). %B: 1 = oberes Band, 0 = unteres.', herkunft: 'John Bollinger, 2001',
    },
    {
      typ: 'squeeze', name: 'Squeeze', titel: 'Squeeze (Bollinger im Keltner)', gruppe: 'Volatilität', ort: 'panel', farbe: '#2fcf7a',
      params: [P('n', n('Länge', 20, 2)), P('bbK', { name: 'BB-Abstand (σ)', def: 2, min: 0.1, max: 10, step: 0.1 }), P('kcK', { name: 'KC-Abstand (× ATR)', def: 1.5, min: 0.1, max: 10, step: 0.1 })],
      kurz: (p) => `Squeeze ${p.n}`,
      rechne: (bars, p) => {
        const r = I.squeeze(bars, p.n, p.bbK, p.kcK), punkt = r.on.map((x) => (x === null ? null : 0)), [an, aus] = teilen(punkt, (i) => r.on[i] === 1);
        return { mom: r.mom, on: r.on, punkt, an, aus };
      },
      linien: () => [{ r: 'mom', histo: 'schwung', preis: true }, { r: 'an', punkte: true, farbe: '#f05454', ohneLegende: true }, { r: 'aus', punkte: true, farbe: '#2fcf7a', alpha: 0.55, ohneLegende: true },
        { r: 'punkt', farbeJe: 'squeeze', nurLegende: true, titel: 'Squeeze' }], skala: { nulllinie: true },
      info: 'Rote Punkte: Bollinger-Bänder liegen im Keltner-Kanal (Schwankung zieht sich zusammen). Balken: Schwung.', herkunft: 'Idee nach John Carter, „Mastering the Trade“, 2005 (eigene Umsetzung, kein Markenname)',
    },
    {
      typ: 'obv', name: 'OBV', titel: 'On-Balance Volume', gruppe: 'Volumen und Orderflow', ort: 'panel', farbe: '#7ce6bb', braucht: 'volumen',
      params: [], kurz: () => 'OBV',
      rechne: (bars, p, ctx) => { const v = volumenSchnitt(bars, ctx, { wert: I.obv(bars) }); return { ...v.werte, hinweis: v.hinweis }; },
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], volumenFormat: true,
      info: 'Summiert das Volumen an Tagen mit steigendem Schluss und zieht es an fallenden ab.', herkunft: 'Joe Granville, 1963',
    },
    {
      typ: 'mfi', name: 'MFI', titel: 'Money Flow Index', gruppe: 'Volumen und Orderflow', ort: 'panel', farbe: '#ffd166', braucht: 'volumen',
      params: [P('n', n('Länge', 14, 2))], kurz: (p) => `MFI ${p.n}`,
      rechne: (bars, p, ctx) => { const v = volumenSchnitt(bars, ctx, { wert: I.mfi(bars, p.n) }); return { ...v.werte, hinweis: v.hinweis }; },
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { min: 0, max: 100, niveaus: [80, 20] }, nachkomma: 1,
      info: 'RSI mit Volumen: Anteil des Geldflusses an steigenden Kerzen, 0–100.', herkunft: 'Gene Quong und Avrum Soudack, 1989',
    },
    {
      typ: 'cmf', name: 'CMF', titel: 'Chaikin Money Flow', gruppe: 'Volumen und Orderflow', ort: 'panel', farbe: '#36c5b0', braucht: 'volumen',
      params: [P('n', n('Länge', 20, 2))], kurz: (p) => `CMF ${p.n}`,
      rechne: (bars, p, ctx) => { const v = volumenSchnitt(bars, ctx, { wert: I.cmf(bars, p.n) }); return { ...v.werte, hinweis: v.hinweis }; },
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { nulllinie: true }, nachkomma: 3,
      info: 'Schließen die Kerzen eher oben (Kaufdruck, > 0) oder unten (Verkaufsdruck, < 0) in ihrer Spanne, gewichtet mit Volumen.', herkunft: 'Marc Chaikin',
    },
    {
      typ: 'delta', name: 'Delta', titel: 'Kauf- gegen Verkaufsvolumen (Delta)', gruppe: 'Volumen und Orderflow', ort: 'panel', farbe: '#2fcf7a', braucht: 'orderflow',
      params: [P('reset', { name: 'Kumuliert ab', def: 'tag', wahl: ['tag', 'woche', 'nie'], namen: { tag: 'je Tag', woche: 'je Woche', nie: 'Anfang' } })],
      kurz: () => 'Delta',
      rechne: (bars, p, ctx) => {
        const times = bars.map((b) => b.time);
        if (ctx.kind === 'crypto') {
          if (!bars.some((b) => isNum(b.kauf))) return { fehlt: 'kein Taker-Volumen in diesen Daten (nur Binance live)' };
          const r = I.deltaFromSplit(bars.map((b) => (isNum(b.kauf) ? b.kauf : null)), bars.map((b) => (isNum(b.kauf) ? b.volume - b.kauf : null)), times, p.reset);
          return { ...r, hinweis: 'echte Aggressor-Seite (Binance Taker-Volumen)' };
        }
        if (!ctx.of || !ctx.of.minuten) return { fehlt: ctx.of?.laden ? 'Abschlüsse werden geladen …' : (ctx.of?.fehler || 'keine Abschlüsse') };
        const r = I.deltaOnBars(ctx.of.minuten, times, p.reset);
        return { ...r, hinweis: ctx.of.hinweis };
      },
      linien: () => [{ r: 'delta', histo: 'delta', preis: true, titel: 'je Kerze' }, { r: 'cumDelta', farbe: '#f5b942', breite: 1.5, titel: 'kumuliert', eigeneSkala: true }],
      skala: { nulllinie: true }, volumenFormat: true,
      info: 'Volumen, das zum Briefkurs gekauft (Käufer drängt) bzw. zum Geldkurs verkauft wurde. Aktien: Näherung über die Tick-Regel aus echten Abschlüssen, Krypto: Taker-Volumen von Binance.',
      herkunft: 'Tick-Regel nach Lee und Ready, „Inferring Trade Direction from Intraday Data“, 1991',
    },
    {
      typ: 'rs', name: 'Relative Stärke', titel: 'Relative Stärke gegen Vergleichswert', gruppe: 'Vergleich', ort: 'panel', farbe: '#f4a6ff', braucht: 'vergleich',
      params: [P('gegen', { name: 'Vergleich', def: 'watchlist', wahl: ['watchlist'], namen: { watchlist: 'Ø Watchlist (gleicher Markt)' }, offen: true })],
      kurz: (p) => `RS ${p.gegen === 'watchlist' ? 'Watchlist' : p.gegen}`,
      rechne: (bars, p, ctx) => {
        if (ctx.kind !== 'stock') return { fehlt: 'nur für Aktien der Watchlist' };
        const v = ctx.vergleich(p.gegen);
        if (!v) return { fehlt: 'keine Vergleichsdaten für diesen Zeitrahmen' };
        return { wert: I.relStrength(bars.map((b) => b.close), v.werte), hinweis: v.text };
      },
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], skala: { niveaus: [100] }, nachkomma: 2,
      info: 'Kurs geteilt durch Vergleichswert, Start = 100. Steigt die Linie, läuft der Wert besser als der Vergleich. Kein RSI.',
      herkunft: 'Kursverhältnis (Lehrbuch); Vergleich = gleichgewichteter Schnitt der eigenen Watchlist, keine Indexdaten',
    },
    {
      typ: 'btcd', name: 'BTC.D', titel: 'Bitcoin-Dominanz', gruppe: 'Vergleich', ort: 'panel', farbe: '#f7931a',
      params: [], kurz: () => 'BTC.D',
      rechne: (bars, p, ctx) => (ctx.dom.points.length ? { wert: I.alignSeries(ctx.dom.points, bars.map((b) => b.time), ctx.tfSec), hinweis: ctx.dom.text } : { fehlt: ctx.dom.text }),
      linien: (f) => [{ r: 'wert', farbe: f, preis: true }], nachkomma: 2, einheit: ' %',
      info: 'Anteil von Bitcoin an der Marktkapitalisierung aller Kryptowährungen.', herkunft: 'CoinMarketCap (Historie über Server), CoinGecko (Live-Punkt)',
    },
  ];
  const NACH_TYP = Object.fromEntries(KATALOG.map((d) => [d.typ, d]));
  const GRUPPEN = ['Durchschnitte', 'Bänder und Kanäle', 'Trend', 'Oszillatoren', 'Volatilität', 'Volumen und Orderflow', 'Niveaus', 'Vergleich'];

  /** Einstellungen eines Indikators vervollständigen und prüfen (fehlende = Standard, Zahlen in Grenzen) */
  function params(typ, roh = {}) {
    const d = NACH_TYP[typ]; if (!d) throw new Error('Indikator ' + typ + ' unbekannt');
    const p = {};
    for (const x of d.params) {
      let v = roh[x.k];
      if (x.wahl) { v = v === undefined || v === null ? x.def : String(v); if (!x.wahl.includes(v) && !x.offen) v = x.def; }
      else { v = Number(v); if (!Number.isFinite(v)) v = x.def; v = Math.min(x.max, Math.max(x.min, v)); if (x.step >= 1) v = Math.round(v); }
      p[x.k] = v;
    }
    return p;
  }
  /** Kurzform für URL und Speicher: "ema:200", "bb:20:2.5" – Werte in der Reihenfolge der Einstellungen */
  function ausText(text) {
    const [typ, ...werte] = String(text).trim().split(':');
    const d = NACH_TYP[typ]; if (!d) return null;
    const roh = {}; werte.forEach((w, k) => { if (d.params[k] && w !== '') roh[d.params[k].k] = w; });
    return { typ, p: params(typ, roh) };
  }
  function alsText(typ, p) {
    const d = NACH_TYP[typ], w = d.params.map((x) => p[x.k]);
    while (w.length && w[w.length - 1] === d.params[w.length - 1].def) w.pop();
    return [typ, ...w].join(':');
  }

  const api = { KATALOG, NACH_TYP, GRUPPEN, params, ausText, alsText, reihe, QUELLEN, QNAME };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.IndikatorKatalog = api;
})(typeof window !== 'undefined' ? window : globalThis);
