"""Orderbuch-Analysen eines Werts an einem Handelstag.

Grundlage ist das Xetra-Buch (5 Stufen mit Orderanzahl), Tradegate und Cboe Europe dienen zum Vergleich.
Ein Durchlauf über alle Ereignisse in Börsenzeit sammelt zeitgewichtete Kennzahlen, ein Sekundenraster
für Signal- und Strategietests und die Abschlüsse mit dem Buch davor. Wände brauchen einen zweiten
Durchlauf, weil ihre Schwelle vom Median der Stufengrößen des ganzen Tages abhängt.

Basispunkte (bp) beziehen sich immer auf den Mittelkurs. Gezählt wird nur der fortlaufende Handel
(09:01 bis 17:30 Uhr); Auktionen werden getrennt ausgewertet. Die Daten sind 15 Minuten verzögert:
alles hier ist Rückschau und Forschung, kein Handelssignal.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from ..buch import Orderbuch
from ..modell import KAUF, VERKAUF, BuchSeite, BuchStufe, Quote, Trade

BERLIN = ZoneInfo("Europe/Berlin")
S = 1_000_000_000
PLATZ = {"XETA": "XETR", "XETB": "XETR", "XETS": "XETR", "XETU": "XETR", "XGRM": "XGAT", "TGAT": "XGAT"}
PLATZNAME = {"XETR": "Xetra", "XGAT": "Tradegate", "CBOE": "Cboe Europe", "XFRA": "Frankfurt"}
# MiFID-Kennung mmtTradingMode der Abschlüsse
MODUS = {"O": "Eröffnungsauktion", "K": "Schlussauktion", "I": "Intraday-Auktion", "U": "Volatilitätsunterbrechung",
         "1": "Auktion", "2": "Fortlaufender Handel", "3": "Handel zum Schlusskurs", "4": "Außerhalb der Hauptsitzung",
         "5": "Meldung", "P": "Periodische Auktion", "6": "Sonstiges"}
AUKTION = {"O", "K", "I", "U", "1", "P"}
STANDARD = {"theta": 0.6, "halten": 30, "wand": 5.0, "ordern": (10_000, 50_000, 250_000), "mm_eur": 10_000, "mm_limit": 5}
HORIZONTE = (5, 30, 60, 300)


def platz(e) -> str:
    return "CBOE" if e.quelle == "cboe" else PLATZ.get(e.venue, e.venue)


def _ols(x: list[float], y: list[float]) -> dict | None:
    n = len(x)
    if n < 10:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if not sxx or not syy:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    beta = sxy / sxx
    return {"beta": beta, "alpha": my - beta * mx, "r2": sxy * sxy / (sxx * syy), "n": n}


def _stichprobe(liste: list, n: int = 600) -> list:
    if len(liste) <= n:
        return liste
    schritt = len(liste) / n
    return [liste[int(i * schritt)] for i in range(n)]


def _r(x, stellen=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, stellen)


def kosten_bp(stufen: list, eur: float, mitte: float) -> float | None:
    """Durchschnittspreis einer Order über eur Euro gegen den Mittelkurs; None, wenn 5 Stufen nicht reichen."""
    rest, stueck, bezahlt = eur, 0.0, 0.0
    for p, m, _ in stufen:
        if not p or p <= 0:                                  # Xetra meldet vereinzelt Stufen ohne Preis
            continue
        nimm = min(m, rest / p)
        stueck += nimm
        bezahlt += nimm * p
        rest -= nimm * p
        if rest <= 1e-6:
            return abs(bezahlt / stueck - mitte) / mitte * 1e4
    return None


class Lauf:
    def __init__(self, isin: str, tag: str, p: dict):
        self.isin, self.tag, self.p = isin, tag, p
        t0 = datetime.fromisoformat(tag).replace(tzinfo=BERLIN)
        self.start = int(t0.replace(hour=9, minute=1).timestamp()) * S
        self.ende = int(t0.replace(hour=17, minute=30).timestamp()) * S
        self.null = int(t0.replace(hour=9).timestamp()) * S          # Bezug für Minutenindex
        self.buch = Orderbuch()
        self.kx = ("XETR", isin, "regulaer")
        self.kr = ("XETR", isin, "retail")
        self.zx = None                      # Xetra-Zustand: dict mit bid, ask, mitte, ...
        self.zx_ts = None
        # zeitgewichtete Minutensummen
        self.minuten = defaultdict(lambda: defaultdict(float))
        self.mids_t: list[int] = []
        self.mids_v: list[float] = []
        self.raster: list[tuple] = []       # (t_s, bid, ask, bq1, aq1, obi5, micro_bp)
        self.naechste = self.start
        self.ofi = defaultdict(float)       # 10-Sekunden-Fach -> Order Flow Imbalance (Stück)
        self.ofi_s = defaultdict(lambda: [0.0, 0.0])   # Sekunde -> [OFI Stufe 1, OFI Stufen 1–5] (für das Signalraster)
        self.updates_s = defaultdict(int)               # Sekunde -> Anzahl Buch-Updates (für den Datenstrom im Reiter Orderbuch)
        self.tiefe1: list[float] = []
        self.stufen_mengen: list[float] = []
        self.ordergroesse = {s: [[0.0, 0.0, 0, 0] for _ in range(5)] for s in (KAUF, VERKAUF)}   # Menge, Orders, Stufen, Einzelorders
        self.trades: list[tuple] = []       # (ts, preis, menge, seite, mitte, bid, ask)
        self.letzter_preis = None
        self.letzte_seite = 0
        self.an_preis = defaultdict(list)   # (Buchseite, Preis) -> [(ts, menge)] getroffene Liquidität
        self.eis = {KAUF: None, VERKAUF: None}
        self.eisberge: list[dict] = []
        self.plaetze_l1: dict[str, dict] = {}      # Platz -> {bid, ask}
        self.plaetze_ts = None
        self.plaetze_akku = defaultdict(lambda: defaultdict(float))
        self.mid_wechsel = defaultdict(list)       # Platz -> [(ts, Richtung)]
        self.letzte_mitte = {}
        self.retail_l1 = {"bid": None, "ask": None}
        self.retail_akku = defaultdict(float)
        self.retail_ts = None
        self.volumen = defaultdict(lambda: [0, 0.0, 0.0])          # Platz -> [Anzahl, Stück, Euro]
        self.auktion = defaultdict(lambda: [0, 0.0, 0.0, None, None])  # Modus -> [Anzahl, Stück, Euro, erster ts, Preis]
        self.phasen = defaultdict(int)
        self.mm = {"pos": 0.0, "kasse": 0.0, "fills": 0, "umsatz": 0.0, "spread": 0.0, "maxpos": 0.0,
                   KAUF: None, VERKAUF: None, "kurve": []}
        self.mm_menge = None
        self.ereignisse = 0
        self.n_xetra = 0

    # --- Xetra-Zustand ------------------------------------------------------------------
    def _zustand(self) -> dict | None:
        b, a = self.buch.stufen(self.kx, KAUF, 5), self.buch.stufen(self.kx, VERKAUF, 5)
        if not b or not a or a[0][0] <= b[0][0]:
            return None
        bid, ask, bq, aq = b[0][0], a[0][0], b[0][1], a[0][1]
        mitte = (bid + ask) / 2
        tb, ta = sum(s[1] for s in b), sum(s[1] for s in a)
        z = {"bid": bid, "ask": ask, "bq": bq, "aq": aq, "mitte": mitte, "b": b, "a": a,
             "spread": (ask - bid) / mitte * 1e4, "tiefe_b": tb * mitte, "tiefe_a": ta * mitte,
             "obi1": (bq - aq) / (bq + aq), "obi5": (tb - ta) / (tb + ta),
             "micro": ((bid * aq + ask * bq) / (bq + aq) - mitte) / mitte * 1e4}
        for eur in self.p["ordern"]:
            kk, kv = kosten_bp(a, eur, mitte), kosten_bp(b, eur, mitte)
            z[f"k{eur}"] = None if kk is None or kv is None else (kk + kv) / 2
        return z

    def _akku(self, t_a: int, t_b: int, z: dict) -> None:
        """Zustand z galt von t_a bis t_b: zeitgewichtet in die Minute von t_a buchen (Lücken höchstens 5 Min.)."""
        t_a, t_b = max(t_a, self.start), min(t_b, self.ende, t_a + 300 * S)
        if t_b <= t_a:
            return
        w = (t_b - t_a) / S
        m = self.minuten[(t_a - self.null) // (60 * S)]
        m["w"] += w
        for k in ("spread", "tiefe_b", "tiefe_a", "obi1", "obi5", "micro"):
            m[k] += z[k] * w
        for eur in self.p["ordern"]:
            k = z[f"k{eur}"]
            if k is None:
                m[f"n{eur}"] += w
            else:
                m[f"k{eur}"] += k * w
                m[f"w{eur}"] += w

    def xetra_aenderung(self, ts: int) -> None:
        alt = self.zx
        if alt is not None and self.zx_ts is not None:
            self._akku(self.zx_ts, ts, alt)
        while self.naechste <= ts and self.naechste <= self.ende:     # Sekundenraster mit dem bis dahin gültigen Zustand
            if alt is not None and self.naechste >= self.start:
                self.raster.append((self.naechste // S, alt["bid"], alt["ask"], alt["bq"], alt["aq"], alt["obi5"], alt["micro"], alt["b"], alt["a"]))
            self.naechste += S
        neu = self._zustand()
        self.zx, self.zx_ts = neu, ts
        if neu is None or not (self.start <= ts <= self.ende):
            return
        self.updates_s[ts // S] += 1
        if alt is not None:                                            # Order Flow Imbalance nach Cont, Kukanov, Stoikov
            e = 0.0
            if neu["bid"] >= alt["bid"]:
                e += neu["bq"]
            if neu["bid"] <= alt["bid"]:
                e -= alt["bq"]
            if neu["ask"] <= alt["ask"]:
                e -= neu["aq"]
            if neu["ask"] >= alt["ask"]:
                e += alt["aq"]
            self.ofi[(ts - self.start) // (10 * S)] += e
            e5 = 0.0                                                   # dieselbe Regel je Stufe 1–5 (Multi-Level-OFI)
            for stufen_neu, stufen_alt, vz in ((neu["b"], alt["b"], 1), (neu["a"], alt["a"], -1)):
                for (p_n, q_n, _), (p_a, q_a, _) in zip(stufen_neu, stufen_alt):
                    if (p_n >= p_a) if vz > 0 else (p_n <= p_a):
                        e5 += vz * q_n
                    if (p_n <= p_a) if vz > 0 else (p_n >= p_a):
                        e5 -= vz * q_a
            sek = self.ofi_s[ts // S]
            sek[0] += e
            sek[1] += e5
        if not self.mids_v or neu["mitte"] != self.mids_v[-1]:
            self.mids_t.append(ts)
            self.mids_v.append(neu["mitte"])
            self._mitte_wechsel("XETR", ts, neu["mitte"])
        self.n_xetra += 1
        if self.n_xetra % 7 == 1:                                     # Stichprobe für Mediane, schont den Speicher
            self.tiefe1.append((neu["bq"] + neu["aq"]) / 2)
            for s, st in ((KAUF, neu["b"]), (VERKAUF, neu["a"])):
                for i, (_, m, o) in enumerate(st):
                    self.stufen_mengen.append(m)
                    g = self.ordergroesse[s][i]
                    g[0] += m
                    g[2] += 1
                    if o:
                        g[1] += o
                        g[3] += o == 1
        self._eisberg_buch(neu)
        self._mm_buch(neu, ts)

    # --- Abschlüsse -----------------------------------------------------------------------
    def trade(self, e: Trade) -> None:
        if not e.menge:
            return
        pl = platz(e)
        v = self.volumen[pl]
        v[0] += 1
        v[1] += e.menge
        v[2] += e.menge * e.preis
        if e.quelle != "xetra":
            return
        modus = e.extra.get("mmtTradingMode")
        if modus and modus != "2":
            a = self.auktion[modus]
            a[0] += 1
            a[1] += e.menge
            a[2] += e.menge * e.preis
            if a[3] is None or e.ts < a[3]:
                a[3], a[4] = e.ts, e.preis
            return
        if not (self.start <= e.ts <= self.ende):
            return
        a = self.auktion["2"]
        a[0] += 1
        a[1] += e.menge
        a[2] += e.menge * e.preis
        z = self.zx
        if z is None:
            return
        # Lee-Ready: über der Mitte = käuferinitiiert, darunter verkäuferinitiiert, auf der Mitte Tick-Regel
        if e.preis > z["mitte"]:
            seite = 1
        elif e.preis < z["mitte"]:
            seite = -1
        elif self.letzter_preis is not None and e.preis != self.letzter_preis:
            seite = 1 if e.preis > self.letzter_preis else -1
        else:
            seite = self.letzte_seite or 1
        self.letzter_preis, self.letzte_seite = e.preis, seite
        self.trades.append((e.ts, e.preis, e.menge, seite, z["mitte"], z["bid"], z["ask"]))
        buchseite = VERKAUF if seite > 0 else KAUF                 # Käufer nimmt Liquidität von der Verkaufsseite
        self.an_preis[(buchseite, e.preis)].append((e.ts, e.menge))
        t = self.eis[buchseite]
        if t and t["preis"] == e.preis:
            t["gehandelt"] += e.menge
            t["n"] += 1
        self._mm_trade(e, buchseite)

    # --- Eisberg-Hinweise: mehr gehandelt, als an der besten Stufe je sichtbar war -------------
    def _eisberg_buch(self, z: dict) -> None:
        for s, preis, menge in ((KAUF, z["bid"], z["bq"]), (VERKAUF, z["ask"], z["aq"])):
            t = self.eis[s]
            if t and t["preis"] != preis:
                self._eisberg_ende(s, t)
                t = None
            if t is None:
                self.eis[s] = {"preis": preis, "start": self.zx_ts, "sichtbar": menge, "gehandelt": 0.0, "n": 0}
            else:
                t["sichtbar"] = max(t["sichtbar"], menge)
                t["ende"] = self.zx_ts

    def _eisberg_ende(self, s: str, t: dict) -> None:
        if t["n"] >= 2 and t["gehandelt"] > 1.5 * t["sichtbar"] and t["gehandelt"] * t["preis"] >= 20_000:
            self.eisberge.append({"seite": s, "preis": t["preis"], "start": t["start"], "ende": t.get("ende", t["start"]),
                                  "sichtbar": t["sichtbar"], "gehandelt": t["gehandelt"], "abschluesse": t["n"],
                                  "faktor": round(t["gehandelt"] / t["sichtbar"], 1)})

    # --- Market Maker (vereinfacht, mit Warteschlange) -------------------------------------
    def _mm_buch(self, z: dict, ts: int) -> None:
        mm = self.mm
        if self.mm_menge is None:
            self.mm_menge = max(1.0, round(self.p["mm_eur"] / z["mitte"]))
        q, limit = self.mm_menge, self.p["mm_limit"] * self.mm_menge
        for s, preis, sichtbar, darf in ((KAUF, z["bid"], z["bq"], mm["pos"] < limit), (VERKAUF, z["ask"], z["aq"], mm["pos"] > -limit)):
            o = mm[s]
            if not darf:
                mm[s] = None
            elif o is None or o["preis"] != preis:
                mm[s] = {"preis": preis, "vor_uns": sichtbar, "rest": q}     # hinten anstellen
        if not mm["kurve"] or ts - mm["kurve"][-1][0] * S >= 60 * S:
            mm["kurve"].append((ts // S, round(mm["kasse"] + mm["pos"] * z["mitte"], 2), mm["pos"]))

    def _mm_trade(self, e: Trade, buchseite: str) -> None:
        mm = self.mm
        o = mm.get(buchseite)
        if not o or o["preis"] != e.preis:
            return
        o["vor_uns"] -= e.menge
        if o["vor_uns"] >= 0:
            return
        fill = min(o["rest"], -o["vor_uns"])
        o["vor_uns"], o["rest"] = 0.0, o["rest"] - fill
        richtung = 1 if buchseite == KAUF else -1                    # unser Gebot wurde getroffen: wir kaufen
        mm["pos"] += richtung * fill
        mm["kasse"] -= richtung * fill * e.preis
        mm["fills"] += 1
        mm["umsatz"] += fill * e.preis
        if self.zx:
            mm["spread"] += fill * abs(self.zx["mitte"] - e.preis)
        mm["maxpos"] = max(mm["maxpos"], abs(mm["pos"]))
        if o["rest"] <= 0:
            mm[buchseite] = None                                      # beim nächsten Buchupdate neu einstellen

    # --- Handelsplätze und Retail-Buch ----------------------------------------------------------
    def _mitte_wechsel(self, pl: str, ts: int, mitte: float) -> None:
        alt = self.letzte_mitte.get(pl)
        if alt is not None and mitte != alt and self.start <= ts <= self.ende:
            self.mid_wechsel[pl].append((ts, 1 if mitte > alt else -1))
        self.letzte_mitte[pl] = mitte

    def plaetze_aenderung(self, ts: int) -> None:
        """Bester Kurs je Platz; zeitgewichtet: wer hatte das beste Gebot, wer den besten Brief."""
        if self.plaetze_ts is not None:
            t_a, t_b = max(self.plaetze_ts, self.start), min(ts, self.ende, self.plaetze_ts + 300 * S)
            if t_b > t_a:
                w = (t_b - t_a) / S
                gueltig = {k: v for k, v in self.plaetze_l1.items() if v.get("bid") and v.get("ask") and v["ask"] > v["bid"]}
                if len(gueltig) >= 2:
                    bb = max(v["bid"] for v in gueltig.values())
                    ba = min(v["ask"] for v in gueltig.values())
                    for k, v in gueltig.items():
                        a = self.plaetze_akku[k]
                        a["w"] += w
                        a["spread"] += (v["ask"] - v["bid"]) / ((v["ask"] + v["bid"]) / 2) * 1e4 * w
                        a["bester_bid"] += w * (v["bid"] == bb)
                        a["bester_ask"] += w * (v["ask"] == ba)
                        a["allein_bid"] += w * (v["bid"] == bb and sum(x["bid"] == bb for x in gueltig.values()) == 1)
                        a["allein_ask"] += w * (v["ask"] == ba and sum(x["ask"] == ba for x in gueltig.values()) == 1)
                    self.plaetze_akku["_alle"]["w"] += w
        self.plaetze_ts = ts
        z = self.zx
        self.plaetze_l1["XETR"] = {"bid": z["bid"], "ask": z["ask"]} if z else {}
        cboe = [self.buch.bestes(k) for k in self.buch.seiten if k[0] not in ("XETR",) and k[2] == "regulaer"]
        bids = [b for b, a in cboe if b is not None]
        asks = [a for b, a in cboe if a is not None]
        if bids or asks:
            self.plaetze_l1["CBOE"] = {"bid": max(bids) if bids else None, "ask": min(asks) if asks else None}

    def quote(self, e: Quote) -> None:
        if e.extra.get("si"):
            return
        if e.extra.get("phase") is not None:
            self.phasen[str(e.extra["phase"])] += 1
        if e.quelle == "xetra":
            if e.extra.get("buch") == "retail":
                self._retail_akku(e.ts)
                if e.bid is not None:
                    self.retail_l1["bid"] = e.bid
                if e.ask is not None:
                    self.retail_l1["ask"] = e.ask
            return
        pl = platz(e)
        l1 = self.plaetze_l1.setdefault(pl, {})
        if e.bid is not None:
            l1["bid"] = e.bid
        if e.ask is not None:
            l1["ask"] = e.ask
        if l1.get("bid") and l1.get("ask") and l1["ask"] > l1["bid"]:
            self._mitte_wechsel(pl, e.ts, (l1["bid"] + l1["ask"]) / 2)
        self.plaetze_aenderung(e.ts)

    def _retail_akku(self, ts: int) -> None:
        """Retail-Buch gegen reguläres Buch, zeitgewichtet."""
        if self.retail_ts is not None and self.zx:
            t_a, t_b = max(self.retail_ts, self.start), min(ts, self.ende, self.retail_ts + 300 * S)
            r, z = self.retail_l1, self.zx
            if t_b > t_a:
                w = (t_b - t_a) / S
                a = self.retail_akku
                a["w"] += w
                if r["bid"] is not None:
                    a["w_bid"] += w
                    a["bid_besser"] += w * (r["bid"] > z["bid"])
                    a["bid_gleich"] += w * (r["bid"] == z["bid"])
                    a["bid_abstand"] += w * (r["bid"] - z["bid"]) / z["mitte"] * 1e4
                if r["ask"] is not None:
                    a["w_ask"] += w
                    a["ask_besser"] += w * (r["ask"] < z["ask"])
                    a["ask_gleich"] += w * (r["ask"] == z["ask"])
                    a["ask_abstand"] += w * (z["ask"] - r["ask"]) / z["mitte"] * 1e4
                if r["bid"] is not None and r["ask"] is not None and r["ask"] > r["bid"]:
                    a["w_spread"] += w
                    a["spread"] += w * (r["ask"] - r["bid"]) / z["mitte"] * 1e4
                a["spread_reg"] += w * z["spread"]
        self.retail_ts = ts

    # --- Hauptschleife -----------------------------------------------------------------------
    def verarbeiten(self, ereignisse: list) -> None:
        for ts, e, roh in ereignisse:
            self.ereignisse += 1
            if isinstance(e, Trade):
                self.trade(e)
            elif isinstance(e, (BuchSeite, BuchStufe)):
                if e.quelle == "xetra":
                    e.venue = "XETR"
                    self.buch.anwenden(e)
                    if e.buch == "regulaer":
                        self.xetra_aenderung(ts)
                        self.plaetze_aenderung(ts)
                    else:
                        b, a = self.buch.bestes(self.kr)
                        self._retail_akku(ts)
                        self.retail_l1 = {"bid": b, "ask": a}
                else:
                    self.buch.anwenden(e)
                    b, a = self.buch.bestes((e.venue, e.instrument, e.buch))
                    if b and a and a > b:
                        self._mitte_wechsel("CBOE", ts, (a + b) / 2)
                    self.plaetze_aenderung(ts)
            elif isinstance(e, Quote):
                self.quote(e)
            if roh is not None and roh.get("messageId") == "pretrade":
                for k in ("tradingSystemPhase", "tradingPhase", "mdEntryType"):
                    if k in roh and k != "tradingSystemPhase":
                        self.phasen[f"{k}={roh[k]}"] += 1
        if self.zx is not None and self.zx_ts is not None:
            self.xetra_aenderung(min(self.ende, self.zx_ts + 60 * S))   # Raster und Akku bis zum letzten Zustand
        for s, t in self.eis.items():
            if t:
                self._eisberg_ende(s, t)


# ======================================================================================
# Auswertung
# ======================================================================================

def _mitte_bei(lauf: Lauf, ts: int) -> float | None:
    i = bisect.bisect_right(lauf.mids_t, ts) - 1
    return lauf.mids_v[i] if i >= 0 else None


def _minutenreihe(lauf: Lauf, fach_min: int = 5) -> dict:
    """Minutensummen zu Fächern (Ortszeit) zusammenfassen."""
    faecher = defaultdict(lambda: defaultdict(float))
    for m, werte in lauf.minuten.items():
        f = faecher[m // fach_min * fach_min]
        for k, v in werte.items():
            f[k] += v
    zeiten, reihen = [], defaultdict(list)
    for m in sorted(faecher):
        f = faecher[m]
        if f["w"] < 5:
            continue
        zeiten.append(lauf.null // S + m * 60)
        for k in ("spread", "tiefe_b", "tiefe_a", "obi1", "obi5", "micro"):
            reihen[k].append(_r(f[k] / f["w"], 3))
        for eur in lauf.p["ordern"]:
            reihen[f"k{eur}"].append(_r(f[f"k{eur}"] / f[f"w{eur}"], 2) if f[f"w{eur}"] else None)
    return {"t": zeiten, **reihen}


def a_kosten(lauf: Lauf) -> dict:
    gesamt = defaultdict(float)
    for werte in lauf.minuten.values():
        for k, v in werte.items():
            gesamt[k] += v
    w = gesamt["w"]
    if not w:
        return {"leer": True}
    # effektiver und realisierter Spread (60 s), Preiswirkung = effektiv - realisiert
    eff, real, gew = 0.0, 0.0, 0.0
    for ts, preis, menge, seite, mitte, *_ in lauf.trades:
        spaeter = _mitte_bei(lauf, ts + 60 * S)
        if spaeter is None or ts + 60 * S > lauf.ende:
            continue
        eur = preis * menge
        eff += eur * 2 * seite * (preis - mitte) / mitte * 1e4
        real += eur * 2 * seite * (preis - spaeter) / mitte * 1e4
        gew += eur
    ordern = []
    for eur in lauf.p["ordern"]:
        wk = gesamt[f"w{eur}"]
        ordern.append({"eur": eur, "kosten_bp": _r(gesamt[f"k{eur}"] / wk) if wk else None,
                       "reicht_nicht": _r((gesamt[f"n{eur}"]) / w * 100, 1)})
    reihe = _minutenreihe(lauf, 5)
    halbstunden = _minutenreihe(lauf, 30)
    beste = min(zip(halbstunden["t"], halbstunden["spread"]), key=lambda x: x[1], default=None)
    schlechteste = max(zip(halbstunden["t"], halbstunden["spread"]), key=lambda x: x[1], default=None)
    return {
        "spread_bp": _r(gesamt["spread"] / w), "tiefe_bid": round(gesamt["tiefe_b"] / w), "tiefe_ask": round(gesamt["tiefe_a"] / w),
        "effektiv_bp": _r(eff / gew) if gew else None, "realisiert_bp": _r(real / gew) if gew else None,
        "wirkung_bp": _r((eff - real) / gew) if gew else None, "abschluesse": len(lauf.trades),
        "ordern": ordern, "reihe": reihe, "halbstunden": halbstunden,
        "beste_halbstunde": beste, "schlechteste_halbstunde": schlechteste, "stunden": round(w / 3600, 2),
    }


def b_druck(lauf: Lauf) -> dict:
    r = lauf.raster
    if len(r) < 600:
        return {"leer": True}
    t = [x[0] for x in r]
    mitte = [(x[1] + x[2]) / 2 for x in r]
    pos = {s: i for i, s in enumerate(t)}
    # Signaltest: Ungleichgewicht der besten Stufe in Fünftel, Kursbewegung danach
    obi1 = [(x[3] - x[4]) / (x[3] + x[4]) for x in r]
    fuenftel = []
    sortiert = sorted(obi1)
    grenzen = [sortiert[int(len(sortiert) * q / 5)] for q in range(1, 5)]
    gruppen = [bisect.bisect_right(grenzen, v) for v in obi1]
    halber = statistics.fmean((x[2] - x[1]) / ((x[1] + x[2]) / 2) * 1e4 / 2 for x in r)
    for g in range(5):
        zeile = {"fuenftel": g + 1, "obi_von": _r(sortiert[int(len(sortiert) * g / 5)], 2),
                 "obi_bis": _r(sortiert[min(len(sortiert) - 1, int(len(sortiert) * (g + 1) / 5) - 1)], 2)}
        for h in HORIZONTE:
            werte = [(mitte[pos[t[i] + h]] - mitte[i]) / mitte[i] * 1e4
                     for i in range(len(r)) if gruppen[i] == g and t[i] + h in pos]
            zeile[f"h{h}"] = _r(statistics.fmean(werte), 2) if werte else None
            zeile[f"n{h}"] = len(werte)
        fuenftel.append(zeile)
    # Microprice: Trefferquote der Richtung
    micro = []
    for h in HORIZONTE:
        treffer = gesamt = 0
        for i, x in enumerate(r):
            j = pos.get(t[i] + h)
            if j is None or x[6] == 0:
                continue
            d = mitte[j] - mitte[i]
            if d == 0:
                continue
            gesamt += 1
            treffer += (d > 0) == (x[6] > 0)
        micro.append({"h": h, "treffer": _r(treffer / gesamt * 100, 1) if gesamt else None, "n": gesamt})
    # OFI gegen Mittelkurs-Änderung je 10 Sekunden
    tiefe = statistics.fmean(lauf.tiefe1) if lauf.tiefe1 else 1.0
    punkte = []
    for fach, ofi in sorted(lauf.ofi.items()):
        a = lauf.start // S + fach * 10
        if a in pos and a + 10 in pos:
            i, j = pos[a], pos[a + 10]
            punkte.append((ofi / tiefe, (mitte[j] - mitte[i]) / mitte[i] * 1e4))
    reg = _ols([x for x, _ in punkte], [y for _, y in punkte])
    reihe = _minutenreihe(lauf, 1)
    return {"fuenftel": fuenftel, "halber_spread_bp": _r(halber), "micro": micro,
            "ofi": {**({k: _r(v, 4) for k, v in reg.items()} if reg else {}), "tiefe1": _r(tiefe, 0),
                    "punkte": [(_r(x, 3), _r(y, 2)) for x, y in _stichprobe(punkte, 700)]},
            "reihe": {"t": reihe["t"], "obi1": reihe["obi1"], "obi5": reihe["obi5"], "micro": reihe["micro"]}}


def c_aufbau(lauf: Lauf, ereignisse: list) -> dict:
    if not lauf.stufen_mengen:
        return {"leer": True}
    median = statistics.median(lauf.stufen_mengen)
    schwelle = lauf.p["wand"] * median
    aktiv = {KAUF: {}, VERKAUF: {}}
    waende = []

    def gehandelt(seite, preis, von, bis):
        return sum(m for ts, m in lauf.an_preis.get((seite, preis), ()) if von <= ts <= bis)

    def beenden(seite, preis, w, ts, grund_sicht):
        if grund_sicht:
            ergebnis = "aus dem Blick"
        else:
            g = gehandelt(seite, preis, ts - 2 * S, ts + S)
            ergebnis = "abgearbeitet" if g >= 0.5 * w["letzte"] else "zurückgezogen"
        dauer = (ts - w["start"]) / S
        verdacht = (ergebnis == "zurückgezogen" and w["idx_min"] >= 1 and w["idx_letzt"] < w["idx_start"] and dauer < 60)
        waende.append({"seite": seite, "preis": preis, "start": w["start"] // S, "ende": ts // S, "dauer_s": round(dauer, 1),
                       "max": w["max"], "eur": round(w["max"] * preis), "orders": w["orders"], "stufe_start": w["idx_start"] + 1,
                       "stufe_min": w["idx_min"] + 1, "ergebnis": ergebnis, "verdacht": verdacht})

    for ts, e, _ in ereignisse:
        if not (isinstance(e, BuchSeite) and e.quelle == "xetra" and e.buch == "regulaer" and lauf.start <= ts <= lauf.ende):
            continue
        stufen = [(p, m, o) for p, m, o in e.stufen if m]
        seite = e.seite
        if not stufen:
            continue
        preise = {p: i for i, (p, _, _) in enumerate(stufen)}
        schlechteste = stufen[-1][0]
        for preis, w in list(aktiv[seite].items()):
            i = preise.get(preis)
            if i is not None and stufen[i][1] >= schwelle:
                continue
            ausser_sicht = i is None and len(stufen) == 5 and (preis < schlechteste if seite == KAUF else preis > schlechteste)
            beenden(seite, preis, w, ts, ausser_sicht)
            del aktiv[seite][preis]
        for i, (p, m, o) in enumerate(stufen):
            if m < schwelle:
                continue
            w = aktiv[seite].get(p)
            if w is None:
                aktiv[seite][p] = {"start": ts, "max": m, "orders": o, "idx_start": i, "idx_min": i, "idx_letzt": i, "letzte": m}
            else:
                if m > w["max"]:
                    w["max"], w["orders"] = m, o
                w["idx_min"], w["idx_letzt"], w["letzte"] = min(w["idx_min"], i), i, m
    for seite in aktiv:
        for preis, w in aktiv[seite].items():
            beenden(seite, preis, w, lauf.ende, True)

    zaehler = defaultdict(lambda: {"anzahl": 0, "dauer": []})
    for w in waende:
        z = zaehler[w["ergebnis"]]
        z["anzahl"] += 1
        z["dauer"].append(w["dauer_s"])
    ergebnisse = [{"ergebnis": k, "anzahl": v["anzahl"], "dauer_median_s": _r(statistics.median(v["dauer"]), 1)}
                  for k, v in sorted(zaehler.items())]
    groesse = []
    for i in range(5):
        zeile = {"stufe": i + 1}
        for s, name in ((KAUF, "bid"), (VERKAUF, "ask")):
            m, o, n, einzel = lauf.ordergroesse[s][i]
            zeile[f"{name}_je_order"] = _r(m / o, 0) if o else None
            zeile[f"{name}_orders"] = _r(o / n, 1) if n else None
            zeile[f"{name}_einzel"] = _r(einzel / n * 100, 1) if n else None
        groesse.append(zeile)
    mitte = [(t, _r((b + a) / 2, 4)) for t, b, a, *_ in lauf.raster[::10]]
    waende.sort(key=lambda w: -w["eur"])
    return {"median_stufe": median, "schwelle": schwelle, "ergebnisse": ergebnisse, "verdacht": sum(w["verdacht"] for w in waende),
            "waende": waende[:300], "anzahl": len(waende), "mitte": mitte, "ordergroesse": groesse,
            "eisberge": sorted(lauf.eisberge, key=lambda x: -x["gehandelt"] * x["preis"])[:100],
            "eisberge_anzahl": len(lauf.eisberge)}


def d_plaetze(lauf: Lauf) -> dict:
    alle = lauf.plaetze_akku.get("_alle", {}).get("w", 0)
    plaetze = []
    for k, a in lauf.plaetze_akku.items():
        if k == "_alle" or not a["w"]:
            continue
        plaetze.append({"platz": k, "name": PLATZNAME.get(k, k), "anteil_zeit": _r(a["w"] / alle * 100, 1) if alle else None,
                        "spread_bp": _r(a["spread"] / a["w"]), "bester_bid": _r(a["bester_bid"] / a["w"] * 100, 1),
                        "bester_ask": _r(a["bester_ask"] / a["w"] * 100, 1), "allein_bid": _r(a["allein_bid"] / a["w"] * 100, 1),
                        "allein_ask": _r(a["allein_ask"] / a["w"] * 100, 1)})
    gesamt_eur = sum(v[2] for v in lauf.volumen.values())
    volumen = [{"platz": k, "name": PLATZNAME.get(k, k), "abschluesse": v[0], "stueck": v[1], "eur": round(v[2]),
                "anteil": _r(v[2] / gesamt_eur * 100, 1) if gesamt_eur else None}
               for k, v in sorted(lauf.volumen.items(), key=lambda x: -x[1][2])]
    # Wer bewegt sich zuerst? Xetra-Wechsel gegen gleichgerichtete Wechsel anderer Plätze binnen einer Sekunde
    fuehrung = []
    xw = lauf.mid_wechsel.get("XETR", [])
    for pl, liste in lauf.mid_wechsel.items():
        if pl == "XETR" or not liste or not xw:
            continue
        zeiten = [t for t, _ in liste]
        andere_zuerst = xetra_zuerst = 0
        for t, r in xw:
            i = bisect.bisect_left(zeiten, t - S)
            vorher = nachher = False
            while i < len(zeiten) and zeiten[i] <= t + S:
                if liste[i][1] == r:
                    if zeiten[i] < t:
                        vorher = True
                    elif zeiten[i] > t:
                        nachher = True
                i += 1
            if vorher and not nachher:
                andere_zuerst += 1
            elif nachher and not vorher:
                xetra_zuerst += 1
        n = andere_zuerst + xetra_zuerst
        fuehrung.append({"platz": pl, "name": PLATZNAME.get(pl, pl), "xetra_zuerst": xetra_zuerst, "andere_zuerst": andere_zuerst,
                         "anteil_xetra": _r(xetra_zuerst / n * 100, 1) if n else None,
                         "wechsel_xetra": len(xw), "wechsel_platz": len(liste)})
    a = lauf.retail_akku
    retail = None
    if a.get("w"):
        retail = {"stunden": _r(a["w"] / 3600, 2),
                  "bid_besser": _r(a["bid_besser"] / a["w_bid"] * 100, 1) if a.get("w_bid") else None,
                  "bid_gleich": _r(a["bid_gleich"] / a["w_bid"] * 100, 1) if a.get("w_bid") else None,
                  "bid_abstand_bp": _r(a["bid_abstand"] / a["w_bid"]) if a.get("w_bid") else None,
                  "ask_besser": _r(a["ask_besser"] / a["w_ask"] * 100, 1) if a.get("w_ask") else None,
                  "ask_gleich": _r(a["ask_gleich"] / a["w_ask"] * 100, 1) if a.get("w_ask") else None,
                  "ask_abstand_bp": _r(a["ask_abstand"] / a["w_ask"]) if a.get("w_ask") else None,
                  "spread_bp": _r(a["spread"] / a["w_spread"]) if a.get("w_spread") else None,
                  "spread_reg_bp": _r(a["spread_reg"] / a["w"])}
    return {"plaetze": sorted(plaetze, key=lambda x: x["platz"] != "XETR"), "volumen": volumen, "fuehrung": fuehrung, "retail": retail}


def e_fortgeschritten(lauf: Lauf) -> dict:
    tr = lauf.trades
    if len(tr) < 50:
        return {"leer": True}
    # Kauf-/Verkaufsdruck je 5 Minuten
    faecher = defaultdict(lambda: [0.0, 0.0])
    for ts, p, m, s, *_ in tr:
        f = faecher[(ts - lauf.null) // (300 * S)]
        f[0 if s > 0 else 1] += p * m
    zeiten = sorted(faecher)
    druck = {"t": [lauf.null // S + f * 300 for f in zeiten],
             "kauf": [_r(faecher[f][0] / (faecher[f][0] + faecher[f][1]) * 100, 1) for f in zeiten]}
    kauf_eur = sum(p * m for _, p, m, s, *_ in tr if s > 0)
    gesamt_eur = sum(p * m for _, p, m, *_ in tr)
    # VPIN: Volumeneimer mit je 1/50 des Tagesvolumens, Mittel über die letzten 10 Eimer
    eimer_groesse = sum(m for _, _, m, *_ in tr) / 50
    eimer, kauf, verkauf, fuell = [], 0.0, 0.0, 0.0
    for ts, p, m, s, *_ in tr:
        rest = m
        while rest > 0:
            nimm = min(rest, eimer_groesse - fuell)
            if s > 0:
                kauf += nimm
            else:
                verkauf += nimm
            fuell += nimm
            rest -= nimm
            if fuell >= eimer_groesse - 1e-9:
                eimer.append((ts, abs(kauf - verkauf)))
                kauf = verkauf = fuell = 0.0
    vpin = {"t": [], "v": []}
    for i in range(10, len(eimer) + 1):
        vpin["t"].append(eimer[i - 1][0] // S)
        vpin["v"].append(_r(sum(x[1] for x in eimer[i - 10:i]) / (10 * eimer_groesse), 3))
    # Kyle's Lambda: Mittelkurs-Änderung je Minute gegen vorzeichenbehaftetes Volumen (Mio. Euro)
    netto = defaultdict(float)
    for ts, p, m, s, *_ in tr:
        netto[(ts - lauf.start) // (60 * S)] += s * p * m / 1e6
    punkte = []
    for minute, v in netto.items():
        a = lauf.start + minute * 60 * S
        m0, m1 = _mitte_bei(lauf, a), _mitte_bei(lauf, a + 60 * S)
        if m0 and m1:
            punkte.append((v, (m1 - m0) / m0 * 1e4))
    reg = _ols([x for x, _ in punkte], [y for _, y in punkte])
    return {"druck": druck, "kauf_anteil": _r(kauf_eur / gesamt_eur * 100, 1) if gesamt_eur else None,
            "vpin": vpin, "vpin_eimer_stueck": round(eimer_groesse), "vpin_mittel": _r(statistics.fmean(vpin["v"]), 3) if vpin["v"] else None,
            "kyle": {**({k: _r(v, 4) for k, v in reg.items()} if reg else {}),
                     "punkte": [(_r(x, 3), _r(y, 2)) for x, y in _stichprobe(punkte, 600)]}}


def _strategie(name: str, trades: list, kurve_start: int) -> dict:
    netto = [x[2] for x in trades]
    kurve, summe = [], 0.0
    for t, _, n, _ in trades:
        summe += n
        kurve.append((t, _r(summe, 2)))
    return {"name": name, "trades": len(trades), "netto_bp": _r(statistics.fmean(netto)) if netto else None,
            "brutto_bp": _r(statistics.fmean(x[3] for x in trades)) if trades else None,
            "treffer": _r(sum(n > 0 for n in netto) / len(netto) * 100, 1) if netto else None,
            "summe_bp": _r(summe), "kurve": _stichprobe(kurve, 500)}


def f_strategien(lauf: Lauf, schwelle_wand: float | None) -> dict:
    r = lauf.raster
    if len(r) < 600:
        return {"leer": True}
    th, h = lauf.p["theta"], int(lauf.p["halten"])
    pos = {x[0]: i for i, x in enumerate(r)}
    # 1. Ungleichgewicht-Momentum: Einstieg über den Spread, Ausstieg nach h Sekunden über den Spread
    momentum, i = [], 0
    while i < len(r):
        t, bid, ask, bq, aq, *_ = r[i]
        obi = (bq - aq) / (bq + aq)
        j = pos.get(t + h)
        if j is None or abs(obi) <= th:
            i += 1
            continue
        _, bid2, ask2, *_ = r[j]
        m1, m2 = (bid + ask) / 2, (bid2 + ask2) / 2
        if obi > th:
            momentum.append((t, 1, (bid2 - ask) / m1 * 1e4, (m2 - m1) / m1 * 1e4))
        else:
            momentum.append((t, -1, (bid - ask2) / m1 * 1e4, (m1 - m2) / m1 * 1e4))
        i = j + 1
    # 2. Abprallen an Wänden: beste Stufe mindestens so groß wie die Wandschwelle
    abprall, i = [], 0
    while schwelle_wand and i < len(r):
        t, bid, ask, bq, aq, *_ = r[i]
        richtung = 1 if bq >= schwelle_wand and aq < schwelle_wand else -1 if aq >= schwelle_wand and bq < schwelle_wand else 0
        if not richtung:
            i += 1
            continue
        m1, ende = (bid + ask) / 2, None
        for k in range(1, h + 1):
            j = pos.get(t + k)
            if j is None:
                break
            ende = j
            _, b2, a2, *_ = r[j]
            if (richtung > 0 and b2 < bid) or (richtung < 0 and a2 > ask):     # Wand gebrochen: raus
                break
        if ende is None:
            i += 1
            continue
        _, b2, a2, *_ = r[ende]
        m2 = (b2 + a2) / 2
        if richtung > 0:
            abprall.append((t, 1, (b2 - ask) / m1 * 1e4, (m2 - m1) / m1 * 1e4))
        else:
            abprall.append((t, -1, (bid - a2) / m1 * 1e4, (m1 - m2) / m1 * 1e4))
        i = ende + 1
    mm = lauf.mm
    letzte = r[-1]
    mitte = (letzte[1] + letzte[2]) / 2
    ergebnis = mm["kasse"] + mm["pos"] * mitte
    marktmacher = {"fills": mm["fills"], "umsatz": round(mm["umsatz"]), "ergebnis_eur": _r(ergebnis),
                   "ergebnis_bp": _r(ergebnis / mm["umsatz"] * 1e4) if mm["umsatz"] else None,
                   "spread_eur": _r(mm["spread"]), "auswahl_eur": _r(ergebnis - mm["spread"]),
                   "max_bestand": mm["maxpos"], "menge": lauf.mm_menge, "endbestand": mm["pos"],
                   "kurve": _stichprobe([(t, e) for t, e, _ in mm["kurve"]], 500)}
    return {"parameter": {"theta": th, "halten": h, "wand": lauf.p["wand"], "mm_eur": lauf.p["mm_eur"], "mm_limit": lauf.p["mm_limit"]},
            "momentum": _strategie("Ungleichgewicht-Momentum", momentum, r[0][0]),
            "abprall": _strategie("Abprallen an Wänden", abprall, r[0][0]), "marktmacher": marktmacher}


def auktionen(lauf: Lauf) -> dict:
    gesamt = sum(a[2] for a in lauf.auktion.values())
    zeilen = []
    for k, (n, stueck, eur, ts, preis) in sorted(lauf.auktion.items(), key=lambda x: -x[1][2]):
        zeilen.append({"modus": k, "name": MODUS.get(k, k), "auktion": k in AUKTION, "abschluesse": n, "stueck": stueck,
                       "eur": round(eur), "anteil": _r(eur / gesamt * 100, 1) if gesamt else None,
                       "vwap": _r(eur / stueck, 4) if stueck else None, "zeit": ts // S if ts else None, "preis": preis})
    schluss = lauf.auktion.get("K")
    eroeff = lauf.auktion.get("O")
    vergleich = {}
    if schluss and schluss[1] and lauf.mids_v:
        m = _mitte_bei(lauf, lauf.ende)
        vwap = schluss[2] / schluss[1]
        vergleich["schluss_gegen_mitte_bp"] = _r((vwap - m) / m * 1e4) if m else None
    if eroeff and eroeff[1] and lauf.mids_v:
        m = lauf.mids_v[0]
        vwap = eroeff[2] / eroeff[1]
        vergleich["eroeffnung_gegen_erste_mitte_bp"] = _r((vwap - m) / m * 1e4)
    return {"modi": zeilen, "vergleich": vergleich, "phasen": dict(lauf.phasen)}


def auswerten(ereignisse: list, isin: str, tag: str, parameter: dict | None = None) -> dict:
    p = {**STANDARD, **(parameter or {})}
    lauf = Lauf(isin, tag, p)
    lauf.verarbeiten(ereignisse)
    c = c_aufbau(lauf, ereignisse)
    return {"isin": isin, "tag": tag, "ereignisse": lauf.ereignisse, "parameter": {k: v for k, v in p.items()},
            "fenster": [lauf.start // S, lauf.ende // S],
            "a": a_kosten(lauf), "b": b_druck(lauf), "c": c, "d": d_plaetze(lauf), "e": e_fortgeschritten(lauf),
            "f": f_strategien(lauf, c.get("schwelle")), "auktionen": auktionen(lauf)}
