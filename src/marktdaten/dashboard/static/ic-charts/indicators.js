/*
 * indicators.js – eigene Implementierung der Chart-Indikatoren (Niko-Sarge-Nachbau).
 * Keine fremde Quelle übernommen; Formeln nach den Lehrbuch-Definitionen
 * (J. Welles Wilder 1978 für RSI, Gerald Appel für MACD, John Bollinger für Bänder; weitere Quellen
 * je Funktion und in INDIKATOREN.md).
 *
 * Konventionen:
 *  - Jede Funktion bekommt ein Array von Zahlen (eine "Reihe", z. B. Schlusskurse)
 *    und liefert ein gleich langes Array zurück.
 *  - Wo noch nicht genug Daten für einen Wert vorliegen ("Aufwärmphase"), steht null.
 *  - Eingaben dürfen selbst null am Anfang enthalten (z. B. ema(rsi(close,14), 9));
 *    gerechnet wird dann ab dem ersten gültigen Wert.
 *  - Läuft im Browser (window.Indicators) und in Node (module.exports) für die Tests.
 */
(function (root) {
  'use strict';

  const isNum = (v) => v !== null && v !== undefined && Number.isFinite(v);

  /** Index des ersten gültigen (nicht-null) Wertes */
  function firstValid(src) {
    for (let i = 0; i < src.length; i++) if (isNum(src[i])) return i;
    return src.length;
  }

  /**
   * SMA – einfacher gleitender Durchschnitt
   *   SMA_t = (x_t + x_{t-1} + ... + x_{t-n+1}) / n
   * Gleitende Summe: O(n) statt O(n*len).
   */
  function sma(src, n) {
    const out = new Array(src.length).fill(null);
    let sum = 0, count = 0;
    for (let i = 0; i < src.length; i++) {
      if (!isNum(src[i])) { sum = 0; count = 0; continue; } // Lücke: neu starten
      sum += src[i]; count++;
      if (count > n) { sum -= src[i - n]; count = n; }
      if (count === n) out[i] = sum / n;
    }
    return out;
  }

  /**
   * EMA – exponentieller gleitender Durchschnitt
   *   alpha = 2 / (n + 1)
   *   EMA_t = alpha * x_t + (1 - alpha) * EMA_{t-1}
   * Startwert (Seed) = SMA der ersten n Werte (wie bei StockCharts / den meisten Plattformen).
   */
  function ema(src, n) {
    return expSmooth(src, n, 2 / (n + 1));
  }

  /**
   * RMA – Wilders Glättung ("Smoothed Moving Average"), alpha = 1/n.
   *   RMA_t = (RMA_{t-1} * (n - 1) + x_t) / n
   * Wird für RSI und ATR verwendet.
   */
  function rma(src, n) {
    return expSmooth(src, n, 1 / n);
  }

  function expSmooth(src, n, alpha) {
    const out = new Array(src.length).fill(null);
    const start = firstValid(src);
    if (src.length - start < n) return out;
    let seed = 0;
    for (let i = start; i < start + n; i++) seed += src[i];
    let prev = seed / n;
    out[start + n - 1] = prev;
    for (let i = start + n; i < src.length; i++) {
      if (!isNum(src[i])) { out[i] = prev; continue; } // Lücke: letzten Wert halten
      prev = alpha * src[i] + (1 - alpha) * prev;
      out[i] = prev;
    }
    return out;
  }

  /**
   * Standardabweichung über n Werte (Grundgesamtheit, Teiler n – so definiert
   * John Bollinger seine Bänder).
   *   sigma = sqrt( sum((x - mittel)^2) / n )
   */
  function stdev(src, n) {
    const out = new Array(src.length).fill(null);
    const mean = sma(src, n);
    for (let i = 0; i < src.length; i++) {
      if (!isNum(mean[i])) continue;
      let s = 0;
      for (let j = i - n + 1; j <= i; j++) s += (src[j] - mean[i]) ** 2;
      out[i] = Math.sqrt(s / n);
    }
    return out;
  }

  /**
   * Bollinger-Bänder (Standard 20, 2)
   *   Mitte = SMA(n), Oben = Mitte + k*sigma, Unten = Mitte - k*sigma
   */
  function bollinger(src, n = 20, k = 2) {
    const mid = sma(src, n), sd = stdev(src, n);
    const upper = mid.map((m, i) => (isNum(m) ? m + k * sd[i] : null));
    const lower = mid.map((m, i) => (isNum(m) ? m - k * sd[i] : null));
    return { mid, upper, lower };
  }

  /**
   * RSI – Relative-Stärke-Index nach Wilder (Standard 14)
   *   Veränderung d_t = x_t - x_{t-1}
   *   Gewinn g_t = max(d_t, 0), Verlust l_t = max(-d_t, 0)
   *   Durchschnittsgewinn/-verlust: erster Wert = einfacher Schnitt der ersten n
   *   Veränderungen, danach Wilder-Glättung (RMA): avg_t = (avg_{t-1}*(n-1) + g_t)/n
   *   RS = avgGewinn / avgVerlust, RSI = 100 - 100 / (1 + RS)
   * Der erste RSI-Wert steht also beim Index n (braucht n+1 Kurse).
   */
  function rsi(src, n = 14) {
    const out = new Array(src.length).fill(null);
    const gains = new Array(src.length).fill(null);
    const losses = new Array(src.length).fill(null);
    for (let i = 1; i < src.length; i++) {
      if (!isNum(src[i]) || !isNum(src[i - 1])) continue;
      const d = src[i] - src[i - 1];
      gains[i] = d > 0 ? d : 0;
      losses[i] = d < 0 ? -d : 0;
    }
    const ag = rma(gains, n), al = rma(losses, n);
    for (let i = 0; i < src.length; i++) {
      if (!isNum(ag[i]) || !isNum(al[i])) continue;
      if (al[i] === 0) out[i] = ag[i] === 0 ? 50 : 100;
      else out[i] = 100 - 100 / (1 + ag[i] / al[i]);
    }
    return out;
  }

  /**
   * MACD (Standard 12, 26, 9) nach Gerald Appel
   *   MACD-Linie  = EMA(12) - EMA(26)
   *   Signallinie = EMA(9) der MACD-Linie
   *   Histogramm  = MACD-Linie - Signallinie
   */
  function macd(src, fast = 12, slow = 26, signal = 9) {
    const ef = ema(src, fast), es = ema(src, slow);
    const line = ef.map((v, i) => (isNum(v) && isNum(es[i]) ? v - es[i] : null));
    const sig = ema(line, signal);
    const hist = line.map((v, i) => (isNum(v) && isNum(sig[i]) ? v - sig[i] : null));
    return { macd: line, signal: sig, hist };
  }

  /**
   * VWAP – volumengewichteter Durchschnittspreis
   *   typischer Preis TP = (Hoch + Tief + Schluss) / 3
   *   VWAP_t = Summe(TP * Volumen) / Summe(Volumen), seit Beginn der Sitzung
   * bars: [{time (Unix-Sekunden), high, low, close, volume}]
   * sessionKey: Funktion time -> Schlüssel; wechselt der Schlüssel, beginnt die Summe neu
   *            (Standard: UTC-Kalendertag).
   */
  function vwap(bars, sessionKey = (t) => Math.floor(t / 86400)) {
    const out = new Array(bars.length).fill(null);
    let pv = 0, vol = 0, key = null;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i], k = sessionKey(b.time);
      if (k !== key) { pv = 0; vol = 0; key = k; }
      const tp = (b.high + b.low + b.close) / 3;
      pv += tp * b.volume; vol += b.volume;
      out[i] = vol > 0 ? pv / vol : tp;
    }
    return out;
  }

  /** Höchster / tiefster Wert der letzten n Werte */
  function highest(src, n) { return windowFn(src, n, (a) => Math.max(...a)); }
  function lowest(src, n) { return windowFn(src, n, (a) => Math.min(...a)); }
  function windowFn(src, n, f) {
    const out = new Array(src.length).fill(null);
    for (let i = n - 1; i < src.length; i++) {
      const w = src.slice(i - n + 1, i + 1);
      if (w.every(isNum)) out[i] = f(w);
    }
    return out;
  }

  /**
   * crossover(a, b): wahr (1) an der Kerze, an der a über b steigt:
   *   a_t > b_t  und  a_{t-1} <= b_{t-1}
   * crossunder: umgekehrt. Ergebnis 1/0, null in der Aufwärmphase.
   */
  function crossover(a, b) {
    return a.map((v, i) => (i > 0 && [v, b[i], a[i - 1], b[i - 1]].every(isNum)
      ? (v > b[i] && a[i - 1] <= b[i - 1] ? 1 : 0) : null));
  }
  function crossunder(a, b) {
    return a.map((v, i) => (i > 0 && [v, b[i], a[i - 1], b[i - 1]].every(isNum)
      ? (v < b[i] && a[i - 1] >= b[i - 1] ? 1 : 0) : null));
  }

  /**
   * ATR – Average True Range nach Wilder (Standard 14)
   *   TR_t = max(Hoch - Tief, |Hoch - Schluss_{t-1}|, |Tief - Schluss_{t-1}|)
   *   ATR  = RMA(TR, n), erster Wert = Schnitt der ersten n True Ranges ab der zweiten Kerze (wie bei Wilder:
   *   die erste Kerze hat keinen Vorschluss). Erster ATR-Wert also bei Index n.
   */
  function atr(bars, n = 14) {
    const tr = bars.map((b, i) => (i === 0 ? null
      : Math.max(b.high - b.low, Math.abs(b.high - bars[i - 1].close), Math.abs(b.low - bars[i - 1].close))));
    return rma(tr, n);
  }

  /**
   * Stochastik (Standard 14, 3, 3) nach George Lane
   *   roh%K = (Schluss - tiefstes Tief_n) / (höchstes Hoch_n - tiefstes Tief_n) * 100
   *   %K = SMA(roh%K, k), %D = SMA(%K, d)
   */
  function stoch(bars, n = 14, k = 3, d = 3) {
    const hh = highest(bars.map((b) => b.high), n), ll = lowest(bars.map((b) => b.low), n);
    const raw = bars.map((b, i) => (isNum(hh[i]) ? (hh[i] === ll[i] ? 50 : ((b.close - ll[i]) / (hh[i] - ll[i])) * 100) : null));
    const K = sma(raw, k);
    return { raw, k: K, d: sma(K, d) };
  }

  /**
   * SuperTrend (Standard 10, 3)
   *   Basisbänder: hl2 ± mult * ATR(n)
   *   Endbänder ziehen nur in Trendrichtung nach (oberes nur runter, unteres nur rauf),
   *   solange der Vorschluss sie nicht verlassen hat.
   *   Trend wechselt, wenn der Schluss das gegenüberliegende Endband durchbricht.
   *   Linie = unteres Band im Aufwärtstrend (dir = 1), oberes im Abwärtstrend (dir = -1).
   */
  function supertrend(bars, n = 10, mult = 3) {
    const a = atr(bars, n), N = bars.length;
    const line = new Array(N).fill(null), dir = new Array(N).fill(null);
    let fu = null, fl = null, trend = 1;
    for (let i = 0; i < N; i++) {
      if (!isNum(a[i])) continue;
      const mid = (bars[i].high + bars[i].low) / 2;
      const bu = mid + mult * a[i], bl = mid - mult * a[i];
      const pc = i > 0 ? bars[i - 1].close : bars[i].close;
      if (fu === null) { fu = bu; fl = bl; }
      else { fu = bu < fu || pc > fu ? bu : fu; fl = bl > fl || pc < fl ? bl : fl; }
      if (trend === 1 && bars[i].close < fl) trend = -1;
      else if (trend === -1 && bars[i].close > fu) trend = 1;
      line[i] = trend === 1 ? fl : fu; dir[i] = trend;
    }
    return { line, dir };
  }


  /**
   * Zeitreihen-Punkte [[zeit, wert], …] (aufsteigend) auf Kerzen legen: je Kerze gilt der letzte Punkt
   * vor ihrem Schluss (zeit < kerzenzeit + step), also auch ein Live-Wert innerhalb der laufenden Kerze.
   * Vor dem ersten Punkt null. Genutzt für die Bitcoin-Dominanz.
   */
  function alignSeries(points, times, step) {
    const out = new Array(times.length).fill(null);
    let j = 0;
    for (let i = 0; i < times.length; i++) {
      const end = times[i] + step;
      while (j + 1 < points.length && points[j + 1][0] < end) j++;
      out[i] = points.length && points[j][0] < end ? points[j][1] : null;
    }
    return out;
  }
  /**
   * Mehrere Zeitreihen zu einer zusammenführen; spätere Reihen sind feiner/aktueller und haben Vorrang:
   * jede Reihe ersetzt alles ab ihrem ersten Zeitpunkt. Liegt sie komplett vor dem bisherigen Anfang, entfällt sie.
   */
  function mergeSeries(series) {
    let out = [];
    for (const s of series) {
      if (!s || !s.length) continue;
      if (out.length && s[s.length - 1][0] < out[0][0]) continue; // liegt komplett vor dem Bisherigen: veraltet
      const start = s[0][0];
      out = out.filter((p) => p[0] < start).concat(s);
    }
    return out;
  }


  // ======================================================================================
  // Erweiterung Oktober 2026 – weitere Indikatoren, eigener Code nach den Erstbeschreibungen.
  // Quellen je Funktion im Kommentar und ausführlich in INDIKATOREN.md (Repo iC Delta).
  // Geprüft gegen TA-Lib (BSD) als unabhängige Referenz: tests/js/test_indikatoren.cjs
  // ======================================================================================

  /** True Range nach Wilder (1978); an der ersten Kerze Hoch − Tief */
  function trueRange(bars) {
    return bars.map((b, i) => (i === 0 ? b.high - b.low
      : Math.max(b.high - b.low, Math.abs(b.high - bars[i - 1].close), Math.abs(b.low - bars[i - 1].close))));
  }
  const typical = (bars) => bars.map((b) => (b.high + b.low + b.close) / 3);
  /** Summe der letzten n Werte (null, solange nicht n gültige Werte vorliegen) */
  function rollingSum(src, n) {
    const out = new Array(src.length).fill(null);
    let sum = 0, count = 0;
    for (let i = 0; i < src.length; i++) {
      if (!isNum(src[i])) { sum = 0; count = 0; continue; }
      sum += src[i]; count++;
      if (count > n) { sum -= src[i - n]; count = n; }
      if (count === n) out[i] = sum;
    }
    return out;
  }

  /**
   * WMA – linear gewichteter Durchschnitt (Lehrbuchdefinition)
   *   WMA_t = (n·x_t + (n−1)·x_{t−1} + … + 1·x_{t−n+1}) / (n(n+1)/2)
   */
  function wma(src, n) {
    const out = new Array(src.length).fill(null), den = (n * (n + 1)) / 2;
    let run = 0;
    for (let i = 0; i < src.length; i++) {
      if (!isNum(src[i])) { run = 0; continue; }
      if (++run < n) continue;
      let s = 0;
      for (let j = 0; j < n; j++) s += (n - j) * src[i - j];
      out[i] = s / den;
    }
    return out;
  }

  /**
   * HMA – Hull Moving Average (Alan Hull, 2005)
   *   HMA = WMA( 2·WMA(x, n/2) − WMA(x, n), √n )   (n/2 und √n abgerundet)
   */
  function hma(src, n) {
    const half = wma(src, Math.max(1, Math.floor(n / 2))), full = wma(src, n);
    const diff = full.map((v, i) => (isNum(v) && isNum(half[i]) ? 2 * half[i] - v : null));
    return wma(diff, Math.max(1, Math.floor(Math.sqrt(n))));
  }

  /**
   * Keltner-Kanal in der heute üblichen Form (Linda Bradford Raschke; Ursprung Chester Keltner 1960)
   *   Mitte = EMA(Schluss, n), Bänder = Mitte ± mult · ATR(atrN)
   */
  function keltner(bars, n = 20, mult = 2, atrN = 10) {
    const mid = ema(bars.map((b) => b.close), n), a = atr(bars, atrN);
    return {
      mid,
      upper: mid.map((m, i) => (isNum(m) && isNum(a[i]) ? m + mult * a[i] : null)),
      lower: mid.map((m, i) => (isNum(m) && isNum(a[i]) ? m - mult * a[i] : null)),
    };
  }

  /**
   * Donchian-Kanal (Richard Donchian): höchstes Hoch und tiefstes Tief der letzten n Kerzen, Mitte dazwischen
   */
  function donchian(bars, n = 20) {
    const upper = highest(bars.map((b) => b.high), n), lower = lowest(bars.map((b) => b.low), n);
    return { upper, lower, mid: upper.map((u, i) => (isNum(u) ? (u + lower[i]) / 2 : null)) };
  }

  /**
   * Ichimoku Kinko Hyo (Goichi Hosoda, veröffentlicht 1969), Standard 9 / 26 / 52
   *   Tenkan = (HH9 + LL9) / 2, Kijun = (HH26 + LL26) / 2
   *   Senkou A = (Tenkan + Kijun) / 2, Senkou B = (HH52 + LL52) / 2 – beide v Perioden voraus
   *   Chikou = Schluss, v Perioden zurück
   * Hosoda zählt die aktuelle Kerze als Periode 1, die Verschiebung beträgt also v − 1 Kerzen.
   * spanA/spanB sind um vorlauf = v − 1 Einträge länger als bars (die Wolke reicht in die Zukunft).
   */
  function ichimoku(bars, tenkanN = 9, kijunN = 26, spanBN = 52, v = 26) {
    const H = bars.map((b) => b.high), L = bars.map((b) => b.low), N = bars.length, sh = Math.max(0, v - 1);
    const mid = (n) => { const hh = highest(H, n), ll = lowest(L, n); return hh.map((x, i) => (isNum(x) ? (x + ll[i]) / 2 : null)); };
    const tenkan = mid(tenkanN), kijun = mid(kijunN), b52 = mid(spanBN);
    const spanA = new Array(N + sh).fill(null), spanB = new Array(N + sh).fill(null), chikou = new Array(N).fill(null);
    for (let i = 0; i < N; i++) {
      if (isNum(tenkan[i]) && isNum(kijun[i])) spanA[i + sh] = (tenkan[i] + kijun[i]) / 2;
      if (isNum(b52[i])) spanB[i + sh] = b52[i];
      if (i - sh >= 0) chikou[i - sh] = bars[i].close;
    }
    return { tenkan, kijun, spanA, spanB, chikou, vorlauf: sh };
  }

  /**
   * Parabolic SAR (J. Welles Wilder, 1978), Standard Schritt 0,02, Höchstwert 0,2
   *   SAR_{t+1} = SAR_t + AF · (EP − SAR_t)
   *   EP = Extrempunkt des laufenden Trends, AF startet bei step und wächst je neuem EP um step bis max.
   *   Im Aufwärtstrend liegt der SAR nie über den Tiefs der beiden Vorkerzen (abwärts spiegelbildlich).
   *   Durchbricht der Kurs den SAR, kippt der Trend: SAR = alter EP, AF = step.
   * Startrichtung wie bei Wilder aus der Bewegung der ersten beiden Kerzen.
   */
  function psar(bars, step = 0.02, max = 0.2) {
    const N = bars.length, line = new Array(N).fill(null), dir = new Array(N).fill(null);
    if (N < 2) return { line, dir };
    const upMove = bars[1].high - bars[0].high, downMove = bars[0].low - bars[1].low;
    let up = !(downMove > 0 && downMove > upMove);
    let sar = up ? bars[0].low : bars[0].high, ep = up ? bars[1].high : bars[1].low, af = step;
    if (up) sar = Math.min(sar, bars[1].low); else sar = Math.max(sar, bars[1].high);
    line[1] = sar; dir[1] = up ? 1 : -1;
    for (let i = 2; i < N; i++) {
      const b = bars[i];
      sar = sar + af * (ep - sar);
      if (up) {
        sar = Math.min(sar, bars[i - 1].low, bars[i - 2].low);
        if (b.low < sar) { up = false; sar = Math.max(ep, b.high, bars[i - 1].high); ep = b.low; af = step; }
        else if (b.high > ep) { ep = b.high; af = Math.min(af + step, max); }
      } else {
        sar = Math.max(sar, bars[i - 1].high, bars[i - 2].high);
        if (b.high > sar) { up = true; sar = Math.min(ep, b.low, bars[i - 1].low); ep = b.high; af = step; }
        else if (b.low < ep) { ep = b.low; af = Math.min(af + step, max); }
      }
      line[i] = sar; dir[i] = up ? 1 : -1;
    }
    return { line, dir };
  }

  /**
   * DMI / ADX (J. Welles Wilder, 1978), Standard 14
   *   +DM = H_t − H_{t−1}, −DM = L_{t−1} − L_t; nur der größere zählt und nur, wenn er > 0 ist
   *   +DI = 100 · RMA(+DM) / RMA(TR), −DI entsprechend
   *   DX = 100 · |+DI − −DI| / (+DI + −DI), ADX = RMA(DX, n)
   */
  function dmi(bars, n = 14) {
    const N = bars.length, pdm = new Array(N).fill(null), mdm = new Array(N).fill(null), tr = new Array(N).fill(null);
    for (let i = 1; i < N; i++) {
      const upM = bars[i].high - bars[i - 1].high, dnM = bars[i - 1].low - bars[i].low;
      pdm[i] = upM > dnM && upM > 0 ? upM : 0;
      mdm[i] = dnM > upM && dnM > 0 ? dnM : 0;
      tr[i] = Math.max(bars[i].high - bars[i].low, Math.abs(bars[i].high - bars[i - 1].close), Math.abs(bars[i].low - bars[i - 1].close));
    }
    const sp = rma(pdm, n), sm = rma(mdm, n), st = rma(tr, n);
    const plus = sp.map((v, i) => (isNum(v) && st[i] > 0 ? (100 * v) / st[i] : null));
    const minus = sm.map((v, i) => (isNum(v) && st[i] > 0 ? (100 * v) / st[i] : null));
    const dx = plus.map((p, i) => (isNum(p) && isNum(minus[i]) ? (p + minus[i] > 0 ? (100 * Math.abs(p - minus[i])) / (p + minus[i]) : 0) : null));
    return { plus, minus, adx: rma(dx, n) };
  }

  /**
   * Aroon (Tushar Chande, 1995), Standard 25
   *   Aroon hoch = 100 · (n − Kerzen seit dem höchsten Hoch der letzten n+1 Kerzen) / n, tief entsprechend
   *   Oszillator = hoch − tief. Bei Gleichstand zählt das jüngste Extrem.
   */
  function aroon(bars, n = 25) {
    const N = bars.length, up = new Array(N).fill(null), down = new Array(N).fill(null);
    for (let i = n; i < N; i++) {
      let hi = i - n, lo = i - n;
      for (let j = i - n; j <= i; j++) { if (bars[j].high >= bars[hi].high) hi = j; if (bars[j].low <= bars[lo].low) lo = j; }
      up[i] = (100 * (n - (i - hi))) / n; down[i] = (100 * (n - (i - lo))) / n;
    }
    return { up, down, osc: up.map((u, i) => (isNum(u) ? u - down[i] : null)) };
  }

  /**
   * Williams %R (Larry Williams, 1973), Standard 14
   *   %R = −100 · (HH_n − Schluss) / (HH_n − LL_n), Bereich −100 … 0
   */
  function willr(bars, n = 14) {
    const hh = highest(bars.map((b) => b.high), n), ll = lowest(bars.map((b) => b.low), n);
    return bars.map((b, i) => (isNum(hh[i]) ? (hh[i] === ll[i] ? -50 : (-100 * (hh[i] - b.close)) / (hh[i] - ll[i])) : null));
  }

  /**
   * CCI – Commodity Channel Index (Donald Lambert, 1980), Standard 20
   *   TP = (H + L + S) / 3, CCI = (TP − SMA_n(TP)) / (0,015 · mittlere absolute Abweichung von SMA_n(TP))
   */
  function cci(bars, n = 20) {
    const tp = typical(bars), m = sma(tp, n);
    return tp.map((x, i) => {
      if (!isNum(m[i])) return null;
      let md = 0;
      for (let j = i - n + 1; j <= i; j++) md += Math.abs(tp[j] - m[i]);
      md /= n;
      return md === 0 ? 0 : (x - m[i]) / (0.015 * md);
    });
  }

  /** ROC – Rate of Change in Prozent: 100 · (x_t − x_{t−n}) / x_{t−n} */
  function roc(src, n = 12) {
    return src.map((v, i) => (i >= n && isNum(v) && isNum(src[i - n]) && src[i - n] !== 0 ? (100 * (v - src[i - n])) / src[i - n] : null));
  }

  /**
   * Bollinger-Bandbreite und %B (John Bollinger, „Bollinger on Bollinger Bands“, 2001)
   *   Breite = (Oben − Unten) / Mitte · 100 (in %), %B = (Schluss − Unten) / (Oben − Unten)
   */
  function bbWidth(src, n = 20, k = 2) {
    const b = bollinger(src, n, k);
    return {
      width: b.mid.map((m, i) => (isNum(m) && m !== 0 ? ((b.upper[i] - b.lower[i]) / m) * 100 : null)),
      pctB: src.map((x, i) => (isNum(b.mid[i]) ? (b.upper[i] === b.lower[i] ? 0.5 : (x - b.lower[i]) / (b.upper[i] - b.lower[i])) : null)),
    };
  }

  /**
   * OBV – On-Balance Volume (Joe Granville, 1963)
   *   Schluss höher: OBV + Volumen, tiefer: OBV − Volumen, gleich: unverändert. Start bei 0.
   */
  function obv(bars) {
    let v = 0;
    return bars.map((b, i) => {
      if (i > 0) { if (b.close > bars[i - 1].close) v += b.volume; else if (b.close < bars[i - 1].close) v -= b.volume; }
      return v;
    });
  }

  /**
   * MFI – Money Flow Index (Gene Quong und Avrum Soudack, 1989), Standard 14
   *   Geldfluss = TP · Volumen, positiv bei steigendem TP, negativ bei fallendem
   *   MFI = 100 − 100 / (1 + Σpositiv_n / Σnegativ_n)
   */
  function mfi(bars, n = 14) {
    const tp = typical(bars), N = bars.length, pos = new Array(N).fill(null), neg = new Array(N).fill(null);
    for (let i = 1; i < N; i++) {
      const f = tp[i] * bars[i].volume;
      pos[i] = tp[i] > tp[i - 1] ? f : 0; neg[i] = tp[i] < tp[i - 1] ? f : 0;
    }
    const sp = rollingSum(pos, n), sn = rollingSum(neg, n);
    return sp.map((p, i) => (isNum(p) ? (sn[i] === 0 ? (p === 0 ? 50 : 100) : 100 - 100 / (1 + p / sn[i])) : null));
  }

  /**
   * CMF – Chaikin Money Flow (Marc Chaikin), Standard 20
   *   Faktor = ((S − T) − (H − S)) / (H − T), CMF = Σ(Faktor · Volumen)_n / ΣVolumen_n
   */
  function cmf(bars, n = 20) {
    const mfv = bars.map((b) => (b.high === b.low ? 0 : (((b.close - b.low) - (b.high - b.close)) / (b.high - b.low)) * b.volume));
    const sv = rollingSum(mfv, n), vv = rollingSum(bars.map((b) => b.volume), n);
    return sv.map((s, i) => (isNum(s) && vv[i] > 0 ? s / vv[i] : null));
  }

  /** Schlüssel eines Zeitraums (UTC) für Unix-Sekunden: Tag, Woche (ab Montag) oder Monat */
  function periodKey(t, art) {
    const d = Math.floor(t / 86400);
    if (art === 'woche') return Math.floor((d + 3) / 7);           // 1.1.1970 war ein Donnerstag
    if (art === 'monat') { const x = new Date(t * 1000); return x.getUTCFullYear() * 12 + x.getUTCMonth(); }
    return d;
  }

  /**
   * Pivot-Punkte aus Hoch, Tief und Schluss des vorigen Zeitraums (Floor-Trader-Methode)
   *   klassisch: P = (H + T + S) / 3, R1 = 2P − T, S1 = 2P − H, R2 = P + (H − T), S2 = P − (H − T),
   *              R3 = H + 2(P − T), S3 = T − 2(H − P)
   *   fibonacci: R_k / S_k = P ± f_k · (H − T) mit f = 0,382 / 0,618 / 1,0
   * art: 'tag' | 'woche' | 'monat'
   */
  function pivots(bars, art = 'tag', methode = 'klassisch') {
    const N = bars.length, keys = ['p', 'r1', 'r2', 'r3', 's1', 's2', 's3'];
    const out = Object.fromEntries(keys.map((k) => [k, new Array(N).fill(null)]));
    let key = null, cur = null, lvl = null;
    for (let i = 0; i < N; i++) {
      const b = bars[i], k = periodKey(b.time, art);
      if (k !== key) {
        if (cur) {
          const { h, l, c } = cur, p = (h + l + c) / 3, r = h - l;
          lvl = methode === 'fibonacci'
            ? { p, r1: p + 0.382 * r, r2: p + 0.618 * r, r3: p + r, s1: p - 0.382 * r, s2: p - 0.618 * r, s3: p - r }
            : { p, r1: 2 * p - l, s1: 2 * p - h, r2: p + r, s2: p - r, r3: h + 2 * (p - l), s3: l - 2 * (h - p) };
        }
        key = k; cur = { h: b.high, l: b.low, c: b.close };
      } else { cur.h = Math.max(cur.h, b.high); cur.l = Math.min(cur.l, b.low); cur.c = b.close; }
      if (lvl) keys.forEach((x) => (out[x][i] = lvl[x]));
    }
    return out;
  }

  /**
   * VWAP mit Anker und Standardabweichungsbändern
   *   ab dem Anker: VWAP = Σ(TP · V) / ΣV, σ² = Σ(V · TP²) / ΣV − VWAP²  (volumengewichtet)
   *   Bänder = VWAP ± k · σ
   * anchors: Array von Indizes, an denen neu begonnen wird (z. B. jeder Sitzungsbeginn), aufsteigend.
   * Vor dem ersten Anker null. Ohne Volumen (ΣV = 0) gilt TP.
   */
  function anchoredVwap(bars, anchors, k1 = 1, k2 = 2) {
    const N = bars.length, vw = new Array(N).fill(null), sd = new Array(N).fill(null);
    const starts = new Set(anchors);
    let pv = 0, vol = 0, pv2 = 0, active = false;
    for (let i = 0; i < N; i++) {
      if (starts.has(i)) { pv = 0; vol = 0; pv2 = 0; active = true; }
      if (!active) continue;
      const b = bars[i], tp = (b.high + b.low + b.close) / 3, v = b.volume || 0;
      pv += tp * v; vol += v; pv2 += tp * tp * v;
      const m = vol > 0 ? pv / vol : tp;
      vw[i] = m; sd[i] = vol > 0 ? Math.sqrt(Math.max(0, pv2 / vol - m * m)) : 0;
    }
    const band = (k) => vw.map((m, i) => (isNum(m) ? m + k * sd[i] : null));
    return { vwap: vw, sd, u1: band(k1), l1: band(-k1), u2: band(k2), l2: band(-k2) };
  }
  /** Anker-Indizes je Zeitraum (Sitzung = UTC-Tag, Woche, Monat) */
  function periodStarts(bars, art) {
    const out = []; let key = null;
    bars.forEach((b, i) => { const k = periodKey(b.time, art); if (k !== key) { out.push(i); key = k; } });
    return out;
  }

  /**
   * Linearer Regressionskanal über die letzten n Kerzen (Methode der kleinsten Quadrate)
   *   y = a + b·x, Bänder = Linie ± k · Standardabweichung der Abweichungen. Nur die letzten n Kerzen haben Werte.
   */
  function linregChannel(src, n = 100, k = 2) {
    const N = src.length, mid = new Array(N).fill(null), upper = new Array(N).fill(null), lower = new Array(N).fill(null);
    const s = N - n;
    if (s < 0 || !src.slice(s).every(isNum)) return { mid, upper, lower, slope: null };
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (let j = 0; j < n; j++) { const y = src[s + j]; sx += j; sy += y; sxx += j * j; sxy += j * y; }
    const b = (n * sxy - sx * sy) / (n * sxx - sx * sx || 1), a = (sy - b * sx) / n;
    let e = 0;
    for (let j = 0; j < n; j++) e += (src[s + j] - (a + b * j)) ** 2;
    const sd = Math.sqrt(e / n);
    for (let j = 0; j < n; j++) { const y = a + b * j; mid[s + j] = y; upper[s + j] = y + k * sd; lower[s + j] = y - k * sd; }
    return { mid, upper, lower, slope: b, sd };
  }
  /** Gleitender Endpunkt der Regressionsgeraden über n Werte (Least Squares Moving Average) */
  function linregValue(src, n) {
    const out = new Array(src.length).fill(null);
    let run = 0;
    for (let i = 0; i < src.length; i++) {
      if (!isNum(src[i])) { run = 0; continue; }
      if (++run < n) continue;
      let sx = 0, sy = 0, sxx = 0, sxy = 0;
      for (let j = 0; j < n; j++) { const y = src[i - n + 1 + j]; sx += j; sy += y; sxx += j * j; sxy += j * y; }
      const b = (n * sxy - sx * sy) / (n * sxx - sx * sx || 1), a = (sy - b * sx) / n;
      out[i] = a + b * (n - 1);
    }
    return out;
  }

  /**
   * ZigZag mit Prozent-Schwelle (Arthur Merrill, „Filtered Waves“, 1977)
   *   Ein Wendepunkt gilt erst, wenn der Kurs um mindestens pct % vom Extrem zurückläuft.
   *   Liefert die Wendepunkte [{i, price, typ: 'hoch'|'tief', fest}] – der letzte ist vorläufig (fest = false).
   */
  function zigzag(bars, pct = 5) {
    const N = bars.length, pts = [];
    if (N < 2) return pts;
    const th = pct / 100;
    let trend = 0, ext = 0, hiI = 0, loI = 0;
    for (let i = 1; i < N; i++) {
      if (bars[i].high > bars[hiI].high) hiI = i;
      if (bars[i].low < bars[loI].low) loI = i;
      if (trend === 0) {
        if (bars[hiI].high >= bars[loI].low * (1 + th) && loI < hiI) { pts.push({ i: loI, price: bars[loI].low, typ: 'tief', fest: true }); trend = 1; ext = hiI; }
        else if (bars[loI].low <= bars[hiI].high * (1 - th) && hiI < loI) { pts.push({ i: hiI, price: bars[hiI].high, typ: 'hoch', fest: true }); trend = -1; ext = loI; }
        continue;
      }
      if (trend === 1) {
        if (bars[i].high >= bars[ext].high) ext = i;
        else if (bars[i].low <= bars[ext].high * (1 - th)) { pts.push({ i: ext, price: bars[ext].high, typ: 'hoch', fest: true }); trend = -1; ext = i; }
      } else {
        if (bars[i].low <= bars[ext].low) ext = i;
        else if (bars[i].high >= bars[ext].low * (1 + th)) { pts.push({ i: ext, price: bars[ext].low, typ: 'tief', fest: true }); trend = 1; ext = i; }
      }
    }
    if (trend !== 0) pts.push({ i: ext, price: trend === 1 ? bars[ext].high : bars[ext].low, typ: trend === 1 ? 'hoch' : 'tief', fest: false });
    return pts;
  }

  /**
   * Relative Stärke gegen einen Vergleichswert (Kursverhältnis, kein RSI)
   *   RS_t = (x_t / v_t) / (x_s / v_s) · 100, s = erste Kerze mit beiden Werten (Start = 100)
   */
  function relStrength(src, bench) {
    const out = new Array(src.length).fill(null);
    let base = null;
    for (let i = 0; i < src.length; i++) {
      if (!isNum(src[i]) || !isNum(bench[i]) || bench[i] === 0) continue;
      const r = src[i] / bench[i];
      if (base === null) base = r;
      out[i] = (r / base) * 100;
    }
    return out;
  }

  /**
   * Squeeze: Bollinger-Bänder liegen innerhalb des Keltner-Kanals (Idee nach John Carter, „Mastering the Trade“, 2005)
   *   an = BB(n, bbK) vollständig innerhalb von KC(n, kcK · ATR(n)) mit SMA als Mitte
   *   Schwung = gleitender Regressions-Endpunkt von (Schluss − Mittel aus Donchian-Mitte und SMA) über n
   */
  function squeeze(bars, n = 20, bbK = 2, kcK = 1.5) {
    const close = bars.map((b) => b.close), bb = bollinger(close, n, bbK), m = sma(close, n), a = atr(bars, n), dc = donchian(bars, n);
    const on = bars.map((_, i) => (isNum(bb.upper[i]) && isNum(a[i]) ? (bb.upper[i] < m[i] + kcK * a[i] && bb.lower[i] > m[i] - kcK * a[i] ? 1 : 0) : null));
    const dev = close.map((c, i) => (isNum(dc.mid[i]) && isNum(m[i]) ? c - (dc.mid[i] + m[i]) / 2 : null));
    return { on, mom: linregValue(dev, n) };
  }

  /**
   * Wertebereich (Value Area) eines Volumenprofils (Konzept: J. Peter Steidlmayer, Market Profile, CBOT 1985)
   *   Start beim Preis mit dem meisten Volumen (POC), dann jeweils die volumenstärkere Nachbarstufe dazunehmen,
   *   bis anteil (Standard 70 %) des Volumens erreicht sind.
   * levels: [{preis, vol}] aufsteigend nach Preis. Liefert {poc, vah, val} als Indizes in levels.
   */
  function valueArea(levels, anteil = 0.7) {
    if (!levels.length) return null;
    let poc = 0, total = 0;
    levels.forEach((l, i) => { total += l.vol; if (l.vol > levels[poc].vol) poc = i; });
    let lo = poc, hi = poc, sum = levels[poc].vol;
    while (sum < total * anteil && (lo > 0 || hi < levels.length - 1)) {
      const below = lo > 0 ? levels[lo - 1].vol : -1, above = hi < levels.length - 1 ? levels[hi + 1].vol : -1;
      if (above >= below) sum += levels[++hi].vol; else sum += levels[--lo].vol;
    }
    return { poc, vah: hi, val: lo };
  }

  /**
   * Volumenprofil aus Kerzen (Näherung): das Volumen jeder Kerze wird gleichmäßig auf ihre Spanne Tief … Hoch verteilt.
   * Mit kauf (Array, z. B. Taker-Kaufvolumen von Binance) wird zusätzlich Kauf/Verkauf je Stufe getrennt.
   * Liefert {schritt, levels: [{preis (Stufenmitte), unten, vol, kauf, verkauf}], poc, vah, val}.
   */
  function volumeProfile(bars, from, to, rows = 40, kauf = null) {
    from = Math.max(0, from); to = Math.min(bars.length - 1, to);
    if (to < from) return null;
    let lo = Infinity, hi = -Infinity;
    for (let i = from; i <= to; i++) { lo = Math.min(lo, bars[i].low); hi = Math.max(hi, bars[i].high); }
    if (!(hi > lo)) hi = lo + Math.abs(lo || 1) * 1e-6;
    const step = (hi - lo) / rows;
    const lv = Array.from({ length: rows }, (_, k) => ({ unten: lo + k * step, preis: lo + (k + 0.5) * step, vol: 0, kauf: 0, verkauf: 0 }));
    for (let i = from; i <= to; i++) {
      const b = bars[i], v = b.volume || 0; if (!v) continue;
      const buy = kauf && isNum(kauf[i]) ? kauf[i] / v : null;
      const k0 = Math.min(rows - 1, Math.floor((b.low - lo) / step)), k1 = Math.min(rows - 1, Math.floor((b.high - lo) / step));
      const span = b.high - b.low;
      for (let k = k0; k <= k1; k++) {
        const part = span > 0 ? (Math.min(b.high, lv[k].unten + step) - Math.max(b.low, lv[k].unten)) / span : 1;
        if (part <= 0) continue;
        lv[k].vol += v * part;
        if (buy !== null) { lv[k].kauf += v * part * buy; lv[k].verkauf += v * part * (1 - buy); }
      }
    }
    return { schritt: step, levels: lv, ...valueArea(lv) };
  }

  /**
   * Kauf-/Verkaufsvolumen je Kerze aus Minutenwerten [[t_s, kauf, verkauf], …] (aufsteigend).
   * barTimes: Startzeiten der Kerzen (Unix-Sekunden, aufsteigend); eine Minute zählt zur letzten Kerze, die vor ihr beginnt.
   * Kumuliertes Delta beginnt je Zeitraum neu (reset: 'tag' | 'woche' | 'nie').
   */
  function deltaOnBars(minutes, barTimes, reset = 'tag') {
    const N = barTimes.length, buy = new Array(N).fill(null), sell = new Array(N).fill(null);
    let j = 0;
    for (const [t, b, s] of minutes) {
      while (j + 1 < N && barTimes[j + 1] <= t) j++;
      if (!N || t < barTimes[0]) continue;
      buy[j] = (buy[j] || 0) + b; sell[j] = (sell[j] || 0) + s;
    }
    return deltaFromSplit(buy, sell, barTimes, reset);
  }
  /** Delta = Kauf − Verkauf je Kerze, dazu das kumulierte Delta (setzt je Zeitraum zurück) */
  function deltaFromSplit(buy, sell, barTimes, reset = 'tag') {
    let cum = 0, key = null;
    const delta = buy.map((b, i) => (isNum(b) && isNum(sell[i]) ? b - sell[i] : null));
    const cumDelta = delta.map((d, i) => {
      const k = reset === 'nie' ? 0 : periodKey(barTimes[i], reset);
      if (k !== key) { cum = 0; key = k; }
      if (!isNum(d)) return null;
      cum += d; return cum;
    });
    return { buy, sell, delta, cumDelta };
  }

  const api = { alignSeries, mergeSeries, sma, ema, rma, stdev, bollinger, rsi, macd, vwap, highest, lowest, crossover, crossunder, atr, stoch, supertrend, isNum,
    trueRange, rollingSum, wma, hma, keltner, donchian, ichimoku, psar, dmi, aroon, willr, cci, roc, bbWidth, obv, mfi, cmf,
    periodKey, periodStarts, pivots, anchoredVwap, linregChannel, linregValue, zigzag, relStrength, squeeze, valueArea, volumeProfile, deltaOnBars, deltaFromSplit };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Indicators = api;
})(typeof window !== 'undefined' ? window : globalThis);
