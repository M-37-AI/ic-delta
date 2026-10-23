/*
 * indikator-zeichnen.js – eigene Zeichenebenen für Indikatoren (Plugin-Schnittstelle „Series Primitive“ von Lightweight Charts).
 *
 *  - Flaeche: füllt den Raum zwischen zwei Reihen (Bollinger, Keltner, Ichimoku-Wolke). Mit farbeAuf/farbeAb
 *             wechselt die Farbe je nachdem, welche Reihe oben liegt.
 *  - Profil:  Volumenprofil am rechten Rand des Kurscharts – Balken je Preisstufe (Kauf/Verkauf getrennt, wenn bekannt),
 *             POC als Linie, Wertebereich hervorgehoben.
 * Reihen sind nach Kerzenindex abgelegt; Index = logischer Index der Zeitachse (wie in drawings.js).
 */
(function (root) {
  'use strict';
  const isNum = (v) => v !== null && v !== undefined && Number.isFinite(v);
  const rgba = (hex, a) => {
    const h = hex.replace('#', ''), v = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
  };

  class Basis {
    constructor() { this.view = { zOrder: () => this.z || 'bottom', renderer: () => ({ draw: (t) => this.draw(t) }) }; }
    attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.req = requestUpdate; }
    detached() { this.chart = null; this.series = null; }
    updateAllViews() {}
    paneViews() { return [this.view]; }
    update() { this.req && this.req(); }
  }

  class Flaeche extends Basis {
    constructor(o) { super(); this.o = o; this.oben = []; this.unten = []; }
    setData(oben, unten) { this.oben = oben || []; this.unten = unten || []; this.update(); }
    draw(target) {
      if (!this.chart || !this.series) return;
      const ts = this.chart.timeScale(), r = ts.getVisibleLogicalRange(); if (!r) return;
      const a = Math.max(0, Math.floor(r.from) - 1), b = Math.min(this.oben.length - 1, Math.ceil(r.to) + 1);
      const o = this.o, x = (i) => ts.logicalToCoordinate(i), y = (p) => this.series.priceToCoordinate(p);
      target.useMediaCoordinateSpace(({ context: ctx }) => {
        for (let i = a; i < b; i++) {
          const p0 = this.oben[i], q0 = this.unten[i], p1 = this.oben[i + 1], q1 = this.unten[i + 1];
          if (![p0, q0, p1, q1].every(isNum)) continue;
          const x0 = x(i), x1 = x(i + 1); if (x0 === null || x1 === null) continue;
          const col = o.farbeAuf ? (p0 + p1 >= q0 + q1 ? o.farbeAuf : o.farbeAb) : o.farbe;
          ctx.fillStyle = rgba(col, o.alpha);
          ctx.beginPath(); ctx.moveTo(x0, y(p0)); ctx.lineTo(x1, y(p1)); ctx.lineTo(x1, y(q1)); ctx.lineTo(x0, y(q0)); ctx.closePath(); ctx.fill();
        }
      });
    }
  }

  class Profil extends Basis {
    constructor(o) { super(); this.o = Object.assign({ breite: 0.24, auf: '#2fcf7a', ab: '#f05454', neutral: '#8b93a1', poc: '#f5b942', text: '#b6bcbb', fmt: String, font: '10.5px -apple-system, system-ui, sans-serif' }, o); this.z = 'bottom'; this.d = null; }
    setData(d) { this.d = d; this.update(); }
    draw(target) {
      const d = this.d; if (!d || !d.levels?.length || !this.series) return;
      const o = this.o, y = (p) => this.series.priceToCoordinate(p);
      target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
        const W = mediaSize.width * o.breite, x1 = mediaSize.width - 2;
        const max = Math.max(...d.levels.map((l) => l.vol)) || 1, split = d.levels.some((l) => l.kauf + l.verkauf > 0);
        ctx.save();
        d.levels.forEach((l, k) => {
          const yt = y(l.unten + d.schritt), yb = y(l.unten); if (!isNum(yt) || !isNum(yb)) return;
          const h = Math.max(1, yb - yt - 1), w = (l.vol / max) * W, inVa = k >= d.val && k <= d.vah, a = inVa ? 0.42 : 0.2;
          if (split && l.vol > 0) {
            const wk = w * (l.kauf / (l.kauf + l.verkauf || 1));
            ctx.fillStyle = rgba(o.auf, a); ctx.fillRect(x1 - w, yt, wk, h);
            ctx.fillStyle = rgba(o.ab, a); ctx.fillRect(x1 - w + wk, yt, w - wk, h);
          } else { ctx.fillStyle = rgba(o.neutral, a + 0.08); ctx.fillRect(x1 - w, yt, w, h); }
        });
        const line = (k, farbe, text, dash) => {
          const l = d.levels[k]; if (!l) return;
          const yy = y(l.preis); if (!isNum(yy)) return;
          ctx.strokeStyle = farbe; ctx.lineWidth = 1; ctx.setLineDash(dash); ctx.beginPath(); ctx.moveTo(x1 - W - 30, yy); ctx.lineTo(x1, yy); ctx.stroke(); ctx.setLineDash([]);
          ctx.fillStyle = farbe; ctx.font = o.font; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(`${text} ${o.fmt(l.preis)}`, x1 - W - 34, yy);
        };
        line(d.vah, rgba(o.text, 0.7), 'VAH', [2, 3]); line(d.val, rgba(o.text, 0.7), 'VAL', [2, 3]); line(d.poc, o.poc, 'POC', []);
        if (d.titel) { ctx.fillStyle = rgba(o.text, 0.75); ctx.font = o.font; ctx.textAlign = 'right'; ctx.textBaseline = 'top'; ctx.fillText(d.titel, x1, 6); }
        ctx.restore();
      });
    }
  }

  const api = { Flaeche, Profil, rgba };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.IndikatorZeichnen = api;
})(typeof window !== 'undefined' ? window : globalThis);
