// Prüft die Indikatoren von iC Charts (indicators.js) gegen TA-Lib-Referenzwerte und gegen Handrechnungen.
// Start: node tests/js/test_indikatoren.cjs   (Node 18+, keine Pakete)
// Referenzwerte: tests/daten/indikatoren_referenz.json, erzeugt mit tests/werkzeuge/referenz_talib.py
'use strict';
const path = require('path');
const fs = require('fs');
const DIR = process.env.ICC_DIR || path.join(__dirname, '../../src/marktdaten/dashboard/static/ic-charts');
const I = require(path.join(DIR, 'indicators.js'));
const REF = JSON.parse(fs.readFileSync(path.join(__dirname, '../daten/indikatoren_referenz.json'), 'utf8'));
const bars = REF.kerzen, T = REF.talib, close = bars.map((b) => b.close);

let ok = 0, fail = 0;
function check(name, pass, info = '') {
  if (pass) ok++; else { fail++; console.log(`FEHLER ${name} ${info}`); }
}
// Vergleich ab Kerze `ab` (Startwerte dürfen sich unterscheiden, z. B. Wilders Startsumme): relative Abweichung
function same(name, got, exp, { ab = 150, tol = 1e-6 } = {}) {
  let worst = 0, at = -1;
  for (let i = ab; i < exp.length; i++) {
    if (exp[i] === null) continue;
    if (got[i] === null || got[i] === undefined) { worst = Infinity; at = i; break; }
    const e = Math.abs(got[i] - exp[i]) / Math.max(1, Math.abs(exp[i]));
    if (e > worst) { worst = e; at = i; }
  }
  check(`${name} = TA-Lib`, worst <= tol, `(größte Abweichung ${worst} bei ${at}: ${got[at]} statt ${exp[at]})`);
}

// ---- gegen TA-Lib ----
same('SMA 20', I.sma(close, 20), T.sma20, { ab: 19, tol: 1e-9 });
same('EMA 20', I.ema(close, 20), T.ema20, { ab: 19, tol: 1e-9 });
same('WMA 20', I.wma(close, 20), T.wma20, { ab: 19, tol: 1e-9 });
same('HMA 20', I.hma(close, 20), T.hma20, { ab: 30, tol: 1e-9 });
same('RSI 14', I.rsi(close, 14), T.rsi14, { ab: 14, tol: 1e-9 });
const m = I.macd(close, 12, 26, 9);
same('MACD', m.macd, T.macd); same('MACD Signal', m.signal, T.macd_signal); same('MACD Histogramm', m.hist, T.macd_hist);
const bb = I.bollinger(close, 20, 2);
same('BB oben', bb.upper, T.bb_upper, { ab: 19, tol: 1e-9 }); same('BB Mitte', bb.mid, T.bb_mid, { ab: 19, tol: 1e-9 }); same('BB unten', bb.lower, T.bb_lower, { ab: 19, tol: 1e-9 });
same('ATR 14', I.atr(bars, 14), T.atr14, { ab: 14, tol: 1e-9 });
const st = I.stoch(bars, 14, 3, 3);
same('Stochastik %K', st.k, T.stoch_k, { ab: 20, tol: 1e-9 }); same('Stochastik %D', st.d, T.stoch_d, { ab: 20, tol: 1e-9 });
same('Parabolic SAR', I.psar(bars, 0.02, 0.2).line, T.sar, { ab: 1, tol: 1e-9 });
const dm = I.dmi(bars, 14);
// TA-Lib startet die Wilder-Summen der DMI mit n−1 statt n Werten (bekannte Eigenheit, „unstable period“);
// wir rechnen nach Wilder. Der Unterschied klingt mit (13/14)^k ab, deshalb erst ab Kerze 400 vergleichen.
same('+DI 14', dm.plus, T.plus_di14, { ab: 400 }); same('−DI 14', dm.minus, T.minus_di14, { ab: 400 }); same('ADX 14', dm.adx, T.adx14, { ab: 400 });
const ar = I.aroon(bars, 25);
same('Aroon hoch 25', ar.up, T.aroon_up25, { ab: 25, tol: 1e-9 }); same('Aroon tief 25', ar.down, T.aroon_down25, { ab: 25, tol: 1e-9 });
same('Williams %R 14', I.willr(bars, 14), T.willr14, { ab: 13, tol: 1e-9 });
same('CCI 20', I.cci(bars, 20), T.cci20, { ab: 19, tol: 1e-9 });
same('ROC 12', I.roc(close, 12), T.roc12, { ab: 12, tol: 1e-9 });
const ob = I.obv(bars), obRef = T.obv;   // TA-Lib startet bei Volumen[0], wir bei 0: Verlauf vergleichen
same('OBV (Verlauf)', ob.map((v) => v + bars[0].volume), obRef, { ab: 0, tol: 1e-9 });
same('MFI 14', I.mfi(bars, 14), T.mfi14, { ab: 14, tol: 1e-9 });
same('Regression (Endpunkt) 20', I.linregValue(close, 20), T.linreg20, { ab: 19, tol: 1e-9 });
const dc = I.donchian(bars, 20);
same('Donchian oben', dc.upper, T.donchian_upper20, { ab: 19, tol: 1e-12 }); same('Donchian unten', dc.lower, T.donchian_lower20, { ab: 19, tol: 1e-12 });
const kc = I.keltner(bars, 20, 2, 10);
same('Keltner oben', kc.upper, T.keltner_mid20.map((v, i) => (v === null || T.keltner_atr10[i] === null ? null : v + 2 * T.keltner_atr10[i])));

// ---- Handrechnungen und Eigenschaften ----
const tiny = [
  { time: 0, open: 10, high: 11, low: 9, close: 10, volume: 100 },
  { time: 60, open: 10, high: 12, low: 10, close: 12, volume: 200 },
  { time: 120, open: 12, high: 12, low: 10, close: 11, volume: 100 },
];
check('CMF Handrechnung (n=3)', Math.abs(I.cmf(tiny, 3)[2] - ((0 * 100 + 1 * 200 + 0 * 100) / 400)) < 1e-12);
const pv = I.pivots([{ time: 0, high: 12, low: 8, close: 10 }, { time: 86400, high: 11, low: 9, close: 10 }], 'tag');
check('Pivot klassisch P = (12+8+10)/3', Math.abs(pv.p[1] - 10) < 1e-12 && Math.abs(pv.r1[1] - 12) < 1e-12 && Math.abs(pv.s1[1] - 8) < 1e-12 && pv.p[0] === null);
const pf = I.pivots([{ time: 0, high: 12, low: 8, close: 10 }, { time: 86400, high: 11, low: 9, close: 10 }], 'tag', 'fibonacci');
check('Pivot Fibonacci R2 = P + 0,618·4', Math.abs(pf.r2[1] - (10 + 0.618 * 4)) < 1e-12);
const av = I.anchoredVwap(tiny, [1]);
check('Verankerter VWAP startet am Anker', av.vwap[0] === null && Math.abs(av.vwap[1] - (34 / 3)) < 1e-12);
const tp1 = 34 / 3, tp2 = 11, w = (tp1 * 200 + tp2 * 100) / 300, sd = Math.sqrt((200 * (tp1 - w) ** 2 + 100 * (tp2 - w) ** 2) / 300);
check('VWAP-Band = volumengewichtete Standardabweichung', Math.abs(av.u1[2] - (w + sd)) < 1e-9);
const lr = I.linregChannel([1, 2, 3, 4, 5], 5, 2);
check('Regressionskanal auf Gerade: Steigung 1, Breite 0', Math.abs(lr.slope - 1) < 1e-12 && Math.abs(lr.upper[4] - 5) < 1e-12);
const zz = I.zigzag(bars, 5);
check('ZigZag wechselt Hoch/Tief ab', zz.length > 4 && zz.every((p, k) => k === 0 || p.typ !== zz[k - 1].typ));
check('ZigZag: Schwünge ≥ 5 %', zz.slice(1, -1).every((p, k) => Math.abs(p.price / zz[k].price - 1) >= 0.05 - 1e-12));
check('Relative Stärke startet bei 100', I.relStrength([10, 11, 12], [5, 5, 6])[0] === 100 && Math.abs(I.relStrength([10, 11, 12], [5, 5, 6])[2] - 100) < 1e-12);
const vp = I.volumeProfile(tiny, 0, 2, 3);
const vsum = vp.levels.reduce((s, l) => s + l.vol, 0);
check('Volumenprofil verteilt das ganze Volumen', Math.abs(vsum - 400) < 1e-9);
const va = I.valueArea([{ vol: 1 }, { vol: 5 }, { vol: 10 }, { vol: 3 }, { vol: 1 }], 0.7);
check('Wertebereich: POC und 70 %', va.poc === 2 && va.val === 1 && va.vah === 2);
const dl = I.deltaOnBars([[0, 5, 2], [60, 1, 4], [86400, 3, 0]], [0, 60, 86400], 'tag');
check('Delta je Kerze und kumuliert mit Tageswechsel', dl.delta.join() === '3,-3,3' && dl.cumDelta.join() === '3,0,3');
const sq = I.squeeze(bars, 20, 2, 1.5);
check('Squeeze liefert an/aus und Schwung', sq.on.slice(40).every((x) => x === 0 || x === 1) && sq.mom.slice(40).every(I.isNum));
const ic = I.ichimoku(bars, 9, 26, 52, 26);
check('Ichimoku: Wolke 25 Kerzen voraus', ic.spanA.length === bars.length + 25 && ic.chikou[bars.length - 26] === bars[bars.length - 1].close);
const hl = (bars[100].high + bars[100].low);
check('Ichimoku Tenkan = Mitte 9', Math.abs(ic.tenkan[100] - (Math.max(...bars.slice(92, 101).map((b) => b.high)) + Math.min(...bars.slice(92, 101).map((b) => b.low))) / 2) < 1e-12 && hl > 0);
const bw = I.bbWidth(close, 20, 2);
check('%B = 1 am oberen Band', Math.abs(((bb.upper[300] - bb.lower[300]) * bw.pctB[300] + bb.lower[300]) - close[300]) < 1e-9);

console.log(`${ok} bestanden, ${fail} fehlgeschlagen`);
process.exit(fail ? 1 : 0);
