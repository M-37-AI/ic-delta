"""Signal-Lage aus dem Orderbuch: ein Sekundenraster für beide Märkte, gleitende Kennzahlen, Regeln aus der
Orderbuch-Literatur (Ungleichgewicht und Orderfluss nach Cont/Kukanov/Stoikov, Microprice, Absorption, Wände,
Eisberge, VPIN, Sweeps, Liquidationen), Journal mit Markout und Statistik je Signalart.

Datenquellen (nur lesen):
  Krypto   Tabelle buch_1s des Kerns (Binance, 20 Stufen je 100 ms und aggTrade, je Sekunde verdichtet), dazu
           buch_tiefe (Wände), liquidationen und perp_1m (Futures-Kontext) – live.
  DE       Xetra-Rohdateien über rechnen.Lauf (5 Stufen mit Orderanzahl, Lee-Ready-Abschlüsse) – 15 Min. verzögert,
           nur 09:01–17:30 Uhr, reine Rückschau.

Alle Schwellen sind Perzentile der eigenen Verteilung je Wert (Kalibrierung in signale.db, bis 5 Tage gemittelt),
keine festen Zahlen. Jedes Signal trägt Richtung, Score 0–1, Begründung mit Zahlen, Invalidierung und Horizont.
Markout = Mid-Änderung in Signalrichtung nach 5/30/60/300 s in bp; netto = Markout − halber Spread − Gebühr
(Binance Spot 10 bp Taker, DE 0, weil Rückschau). Nichts hier ist eine Handelsempfehlung.
"""
from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..modell import KAUF, VERKAUF
from . import heatmap, lesen
from .rechnen import S, STANDARD, Lauf

BERLIN = ZoneInfo("Europe/Berlin")
ORT = lesen.DATEN / "analysen"
DB = ORT / "signale.db"
GEBUEHR = {"KRYPTO": 10.0, "DE": 0.0}
HORIZONTE = (5, 30, 60, 300)
FENSTER_KRYPTO = 6 * 3600
FENSTER_THREAD = 2 * 3600
WAENDE_KRYPTO_S = 2 * 3600
KALIBRIER_TAGE = 5
STATISTIK_TAGE = 10
MIN_NOTIONAL = {"KRYPTO": 50_000.0, "DE": 20_000.0}      # Eisberg: so viel muss an einer Stufe gehandelt worden sein
ROH = lesen.Rohdaten()
_de_rechnen = threading.Lock()                           # nur ein Xetra-Durchlauf gleichzeitig
ZULETZT_DE: dict[str, float] = {}                        # ISIN -> zuletzt im Dashboard angefragt; nur die hält der Hintergrund warm
WARM_MIN = 20 * 60
_lock = threading.Lock()
_de_cache: dict = {}                                     # (isin, tag) -> (stand, raster, extras)
_waende_cache: dict = {}                                 # wert -> (zeit, waende, band)
LAEUFT = {"stand": None, "fehler": None, "dauer_s": None}

NAMEN = {"imbalance_momentum": "Ungleichgewicht mit Fluss", "absorption": "Absorption", "delta_divergenz": "Delta-Divergenz",
         "wand_abprall": "Abprall an Wand", "fake_wand": "Wand verschwindet", "eisberg": "Eisberg",
         "duennschicht_ausbruch": "Ausbruch in dünne Schicht", "sweep_reclaim": "Sweep und Rückkehr", "vpin_stop": "Toxischer Fluss",
         "liquidations_kaskade": "Liquidationskaskade", "oi_matrix": "Open-Interest-Lage"}
ERKLAERUNG = {
    "imbalance_momentum": "Eine Seite des Buchs ist deutlich schwerer und der Orderfluss drückt in dieselbe Richtung. Der nächste Tick folgt meist dieser Seite, die Wirkung hält nur Sekunden.",
    "absorption": "Viel aggressives Volumen trifft auf eine Stufe, der Kurs bewegt sich kaum. Ein passiver Großakteur nimmt alles auf; danach dreht der Kurs oft gegen die Aggressoren.",
    "delta_divergenz": "Der Kurs macht ein neues Extrem, das kumulierte Delta nicht. Der Antrieb der Bewegung lässt nach.",
    "wand_abprall": "Eine große, lange stehende Order liegt direkt vor dem Kurs und bleibt bei Annäherung. Der Kurs prallt in der Regel ab.",
    "fake_wand": "Eine große Order verschwindet, sobald der Kurs näher kommt, ohne gehandelt zu werden. Die erwartete Stütze fällt weg, der Kurs läuft oft hindurch.",
    "eisberg": "An einer Stufe wird ein Vielfaches der sichtbaren Menge gehandelt, die Stufe füllt sich immer wieder auf. Dahinter steht ein großer verdeckter Auftrag.",
    "duennschicht_ausbruch": "Der Kurs bricht ein Niveau, hinter dem wenig Liquidität liegt, und der Fluss ist initiativ. Die Bewegung beschleunigt meist.",
    "sweep_reclaim": "Eine Spitze räumt die Liquidität jenseits eines Hochs oder Tiefs ab, der Kurs kehrt sofort zurück. Die Gegenseite übernimmt.",
    "vpin_stop": "Der Fluss ist einseitig und womöglich informiert. Market Maker ziehen sich zurück, Kosten und Sprungrisiko steigen: kein Richtungssignal, ein Risikofilter.",
    "liquidations_kaskade": "Zwangsverkäufe oder -käufe von Hebelpositionen treiben den Kurs, das Open Interest fällt. Nach dem Ende der Kaskade kehrt der Kurs oft ein Stück zurück.",
    "oi_matrix": "Kurs, Open Interest und Delta zusammen zeigen, ob eine Bewegung von neuen Positionen getragen wird oder nur Zwangsabwicklung ist.",
}
QUELLE = {"KRYPTO": "Binance, Buch 20 Stufen je 100 ms und aggTrade, je Sekunde verdichtet; Wände aus 1.000 Stufen alle 10 s",
          "DE": "Xetra, 5 Stufen mit Orderanzahl und Abschlüsse (Lee-Ready), 15 Min. verzögert"}


# ======================================================================================
# Hilfen
# ======================================================================================

def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(x, n)


def _perz(werte: list, q: float) -> float | None:
    if not werte:
        return None
    s = sorted(werte)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def _uhr(t: int) -> str:
    return datetime.fromtimestamp(t, BERLIN).strftime("%H:%M:%S")


def _z(x: float, s: int = 2) -> str:
    return ("+" if x > 0 else "") + f"{x:,.{s}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _preis(p: float) -> str:
    return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if p >= 100 else f"{p:.4g}".replace(".", ",")


def _db_signale() -> sqlite3.Connection:
    ORT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS signale (wert TEXT, t INTEGER, art TEXT, richtung INTEGER, score REAL, preis REAL, mitte REAL, spread_bp REAL,
            begruendung TEXT, invalidierung TEXT, horizont_s INTEGER, markt TEXT, m5 REAL, m30 REAL, m60 REAL, m300 REAL,
            netto30 REAL, netto60 REAL, netto300 REAL, PRIMARY KEY (wert, t, art, richtung)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS signale_art ON signale (art, t);
        CREATE TABLE IF NOT EXISTS kalibrierung (wert TEXT, tag TEXT, json TEXT, PRIMARY KEY (wert, tag)) WITHOUT ROWID;
    """)
    return db


def _wert(wert: str) -> dict:
    werte = lesen.watchlist()
    w = next((x for x in werte if x["id"] == wert or x["symbol"] == str(wert).upper()), None)
    return w or {"id": wert, "symbol": wert, "name": wert, "markt": "KRYPTO" if str(wert).upper().endswith(("USDT", "USDC")) else "DE"}


def _tick(preise: list[float]) -> float:
    """Kleinster Preisschritt, aus den beobachteten Unterschieden geschätzt."""
    d = sorted({round(abs(a - b), 10) for a, b in zip(preise, preise[1:]) if a != b})
    if not d:
        return 0.01
    tick = d[0]
    for f in (0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10):   # auf eine runde Stufe ziehen
        if tick <= f * 1.01:
            return f
    return tick


# ======================================================================================
# Raster: je Sekunde dieselben Spalten für Krypto (buch_1s) und DE (Xetra-Rohdateien)
#   t, bid, ask, b[5], a[5] (Mengen), bp[5], ap[5] (Preise, Krypto nur Stufe 1), b_tief, a_tief,
#   ofi, ofi5, updates, kauf, verkauf, n, letzter
# ======================================================================================

def raster_krypto(wert: str, von: int, bis: int) -> list[dict]:
    db = lesen._db()
    if db is None:
        return []
    try:
        try:
            spalten = [r[1] for r in db.execute("PRAGMA table_info(buch_1s)")]
        except sqlite3.Error:
            return []
        if not spalten:
            return []
        wunsch = ["t", "bid", "ask", "b1", "b2", "b3", "b4", "b5", "a1", "a2", "a3", "a4", "a5", "b_tief", "a_tief", "ofi", "ofi5", "updates",
                  "kauf", "verkauf", "n", "letzter"]
        sel = ", ".join(k if k in spalten else "NULL" for k in wunsch)
        zeilen = db.execute(f"SELECT {sel} FROM buch_1s WHERE id = ? AND t >= ? AND t <= ? ORDER BY t", (wert, von, bis)).fetchall()
    finally:
        db.close()
    aus = []
    for z in zeilen:
        t, bid, ask = z[0], z[1], z[2]
        if not bid or not ask or ask <= bid:
            continue
        b = [float(x or 0) for x in z[3:8]]
        a = [float(x or 0) for x in z[8:13]]
        aus.append({"t": int(t), "bid": float(bid), "ask": float(ask), "b": b, "a": a, "bp": None, "ap": None,
                    "b_tief": None if z[13] is None else float(z[13]), "a_tief": None if z[14] is None else float(z[14]),
                    "ofi": float(z[15] or 0), "ofi5": float(z[16] or 0), "updates": int(z[17] or 0),
                    "kauf": float(z[18] or 0), "verkauf": float(z[19] or 0), "n": int(z[20] or 0), "letzter": z[21]})
    return aus


def _lauf_de(isin: str, tag: str) -> tuple[list[dict], dict]:
    """Raster und Zusatz (Wände, Eisberge) aus den Xetra-Rohdateien; je Wert und Tag zwischengespeichert."""
    stand = ROH.stand(tag)
    with _lock:
        treffer = _de_cache.get((isin, tag))
        if treffer and (treffer[0] == stand or time.time() - treffer[3] < 180):
            return treffer[1], treffer[2]
    with _de_rechnen:                                     # ein Durchlauf zur Zeit: parallele Abrufe (Heatmap, Signale) warten auf denselben
        with _lock:
            treffer = _de_cache.get((isin, tag))
            if treffer and (treffer[0] == stand or time.time() - treffer[3] < 180):
                return treffer[1], treffer[2]
        return _lauf_de_rechnen(isin, tag, stand)


def _lauf_de_rechnen(isin: str, tag: str, stand) -> tuple[list[dict], dict]:
    lauf = Lauf(isin, tag, dict(STANDARD))
    lauf.verarbeiten(ROH.ereignisse(isin, tag))
    je_sekunde: dict[int, list] = defaultdict(lambda: [0.0, 0.0, 0, None])
    for ts, preis, menge, seite, *_ in lauf.trades:
        s = je_sekunde[ts // S]
        s[0 if seite > 0 else 1] += menge
        s[2] += 1
        s[3] = preis
    raster = []
    for r in lauf.raster:
        t, bid, ask, bq, aq, _, _, b, a = r
        if ask <= bid or not b or not a:
            continue
        tr = je_sekunde.get(t)
        o = lauf.ofi_s.get(t, (0.0, 0.0))
        raster.append({"t": t, "bid": bid, "ask": ask, "b": [m for _, m, _ in b] + [0.0] * (5 - len(b)), "a": [m for _, m, _ in a] + [0.0] * (5 - len(a)),
                       "bp": [p for p, _, _ in b], "ap": [p for p, _, _ in a], "b_tief": None, "a_tief": None,
                       "ofi": o[0], "ofi5": o[1], "updates": 0, "kauf": tr[0] if tr else 0.0, "verkauf": tr[1] if tr else 0.0,
                       "n": tr[2] if tr else 0, "letzter": tr[3] if tr else None})
    trades_an: dict = defaultdict(list)
    for (_, preis), liste in lauf.an_preis.items():
        trades_an[preis] += [(ts // S, m) for ts, m in liste]
    proben = [(r["t"], dict(zip(r["bp"], r["b"])), dict(zip(r["ap"], r["a"]))) for r in raster]
    tick = _tick([r["bid"] for r in raster] + [r["ask"] for r in raster]) if raster else 0.01
    waende = heatmap._waende(proben, trades_an, tick) if proben else []
    extras = {"waende": waende, "tick": tick, "eisberge": [{"t": e["ende"] // S, "seite": e["seite"], "preis": e["preis"], "sichtbar": e["sichtbar"],
                                                            "gehandelt": e["gehandelt"], "faktor": e["faktor"]} for e in lauf.eisberge],
              "abschluesse": [(ts // 1_000_000, preis, menge, seite > 0) for ts, preis, menge, seite, *_ in lauf.trades],   # Datenstrom
              "updates_s": dict(lauf.updates_s)}
    with _lock:
        _de_cache[(isin, tag)] = (stand, raster, extras, time.time())
        while len(_de_cache) > 6:
            _de_cache.pop(next(iter(_de_cache)))
    return raster, extras


def raster(wert: str, von: int | None = None, bis: int | None = None, tag: str | None = None) -> list[dict]:
    """Sekundenraster für beide Märkte (Krypto: Zeitraum von…bis, DE: Handelstag tag)."""
    w = _wert(wert)
    if w["markt"] == "DE":
        tag = tag or next((t for t in ROH.tage() if w["id"] in ROH.werte(t)), None)
        if not tag:
            return []
        r, _ = _lauf_de(w["id"], tag)
        if von or bis:
            r = [x for x in r if (von or 0) <= x["t"] <= (bis or 2 ** 40)]
        return r
    bis = bis or int(time.time())
    return raster_krypto(w["id"], von or bis - FENSTER_KRYPTO, bis)


# ======================================================================================
# Kennzahlen, gleitend über das Raster
# ======================================================================================

class _Rollend:
    """Mittel und Standardabweichung der letzten n Werte."""
    __slots__ = ("n", "q", "s", "s2")

    def __init__(self, n: int):
        self.n, self.q, self.s, self.s2 = n, deque(), 0.0, 0.0

    def push(self, x: float) -> None:
        self.q.append(x)
        self.s += x
        self.s2 += x * x
        if len(self.q) > self.n:
            y = self.q.popleft()
            self.s -= y
            self.s2 -= y * y

    def z(self, x: float) -> float:
        k = len(self.q)
        if k < 30:
            return 0.0
        m = self.s / k
        v = max(0.0, self.s2 / k - m * m)
        sd = math.sqrt(v)
        return 0.0 if sd < 1e-12 else (x - m) / sd

    def mittel(self) -> float:
        return self.s / len(self.q) if self.q else 0.0


def _rollmax(werte: list, n: int, lag: int = 0) -> list:
    """Maximum über [i-lag-n+1, i-lag] je i (None, solange nichts da ist)."""
    aus, dq = [None] * len(werte), deque()
    for i in range(len(werte)):
        j = i - lag
        if j >= 0:
            while dq and werte[dq[-1]] <= werte[j]:
                dq.pop()
            dq.append(j)
        while dq and dq[0] < j - n + 1:
            dq.popleft()
        aus[i] = werte[dq[0]] if dq else None
    return aus


def _rollmin(werte: list, n: int, lag: int = 0) -> list:
    neg = [-x for x in werte]
    m = _rollmax(neg, n, lag)
    return [None if x is None else -x for x in m]


def features(raster: list[dict], markt: str) -> dict:
    n = len(raster)
    F: dict = {k: [0.0] * n for k in ("mitte", "spread_bp", "micro_bp", "obi1", "obi5", "obi_tief", "tiefe_b", "tiefe_a", "delta", "cvd",
                                        "ofi_n", "ofi_z1", "ofi_z", "ofi_z30", "delta10", "delta30", "kauf10", "verkauf10", "kauf60", "verkauf60",
                                        "vol_bp", "intens", "vpin", "pers_anteil", "n10", "dm30", "hoch", "tief")}
    F["t"] = [r["t"] for r in raster]
    if not n:
        return F
    tiefe1 = _Rollend(1800)
    z1, z5, z30 = _Rollend(1800), _Rollend(1800), _Rollend(1800)
    q5, q30 = deque(), deque()
    s5 = s30 = 0.0
    d10, d30, k10, v10, k60, v60 = deque(), deque(), deque(), deque(), deque(), deque()
    sd10 = sd30 = sk10 = sv10 = sk60 = sv60 = 0.0
    r60 = deque()
    sr2 = 0.0
    cvd = 0.0
    hoch = -1e300
    tief = 1e300
    pers_seit: dict[tuple, int] = {}
    gesamt_vol = sum(r["kauf"] + r["verkauf"] for r in raster)
    dauer = max(1, raster[-1]["t"] - raster[0]["t"])
    eimer_groesse = gesamt_vol / 50 if markt == "DE" else gesamt_vol / max(1.0, 50 * dauer / 86400)
    eimer: deque = deque()
    e_kauf = e_verkauf = e_fuell = 0.0
    vpin_wert = 0.0
    for i, r in enumerate(raster):
        bid, ask, b, a = r["bid"], r["ask"], r["b"], r["a"]
        mitte = (bid + ask) / 2
        bq, aq = b[0], a[0]
        F["mitte"][i] = mitte
        F["spread_bp"][i] = (ask - bid) / mitte * 1e4
        F["micro_bp"][i] = ((bid * aq + ask * bq) / (bq + aq) - mitte) / mitte * 1e4 if bq + aq > 0 else 0.0
        F["obi1"][i] = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
        wb = sum(m / (k + 1) for k, m in enumerate(b))
        wa = sum(m / (k + 1) for k, m in enumerate(a))
        F["obi5"][i] = (wb - wa) / (wb + wa) if wb + wa > 0 else 0.0
        tb = sum(b) + (r["b_tief"] or 0.0)
        ta = sum(a) + (r["a_tief"] or 0.0)
        F["obi_tief"][i] = (tb - ta) / (tb + ta) if tb + ta > 0 else 0.0
        F["tiefe_b"][i] = sum(b) * mitte
        F["tiefe_a"][i] = sum(a) * mitte
        # Orderfluss
        tiefe1.push((bq + aq) / 2)
        norm = max(1e-9, tiefe1.mittel())
        o = r["ofi5"] / norm
        F["ofi_n"][i] = o
        q5.append(o)
        s5 += o
        if len(q5) > 5:
            s5 -= q5.popleft()
        q30.append(o)
        s30 += o
        if len(q30) > 30:
            s30 -= q30.popleft()
        z1.push(o)
        z5.push(s5)
        z30.push(s30)
        F["ofi_z1"][i], F["ofi_z"][i], F["ofi_z30"][i] = z1.z(o), z5.z(s5), z30.z(s30)
        # Abschlüsse
        d = r["kauf"] - r["verkauf"]
        cvd += d
        F["delta"][i], F["cvd"][i] = d, cvd
        d10.append(d); sd10 += d
        if len(d10) > 10: sd10 -= d10.popleft()
        d30.append(d); sd30 += d
        if len(d30) > 30: sd30 -= d30.popleft()
        k10.append(r["kauf"]); sk10 += r["kauf"]
        if len(k10) > 10: sk10 -= k10.popleft()
        v10.append(r["verkauf"]); sv10 += r["verkauf"]
        if len(v10) > 10: sv10 -= v10.popleft()
        k60.append(r["kauf"]); sk60 += r["kauf"]
        if len(k60) > 60: sk60 -= k60.popleft()
        v60.append(r["verkauf"]); sv60 += r["verkauf"]
        if len(v60) > 60: sv60 -= v60.popleft()
        F["delta10"][i], F["delta30"][i] = sd10, sd30
        F["kauf10"][i], F["verkauf10"][i], F["kauf60"][i], F["verkauf60"][i] = sk10, sv10, sk60, sv60
        # Volatilität der Mitte, 60 s, in bp
        if i:
            ret = math.log(mitte / F["mitte"][i - 1]) * 1e4
            r60.append(ret)
            sr2 += ret * ret
            if len(r60) > 60:
                y = r60.popleft()
                sr2 -= y * y
        F["vol_bp"][i] = math.sqrt(max(0.0, sr2))
        F["dm30"][i] = (mitte - F["mitte"][i - 30]) / F["mitte"][i - 30] * 1e4 if i >= 30 else 0.0
        hoch, tief = max(hoch, mitte), min(tief, mitte)
        F["hoch"][i], F["tief"][i] = hoch, tief
        # VPIN
        rest = r["kauf"] + r["verkauf"]
        kauf_anteil = r["kauf"] / rest if rest > 0 else 0.5
        while rest > 0 and eimer_groesse > 0:
            nimm = min(rest, eimer_groesse - e_fuell)
            e_kauf += nimm * kauf_anteil
            e_verkauf += nimm * (1 - kauf_anteil)
            e_fuell += nimm
            rest -= nimm
            if e_fuell >= eimer_groesse - 1e-9:
                eimer.append(abs(e_kauf - e_verkauf))
                if len(eimer) > 10:
                    eimer.popleft()
                e_kauf = e_verkauf = e_fuell = 0.0
                if len(eimer) >= 3:
                    vpin_wert = sum(eimer) / (len(eimer) * eimer_groesse)
        F["vpin"][i] = vpin_wert
        # Persistenz: Stufen, die seit mindestens 3 s stehen (Krypto: nur die beste Stufe, es gibt keine Preise dahinter)
        pers = gesamt = 0.0
        if not r["bp"]:                                   # Krypto: keine Preise hinter der besten Stufe, Persistenz nicht messbar
            F["pers_anteil"][i] = None
            continue
        stufen = [(KAUF, p, m) for p, m in zip(r["bp"], b)] + [(VERKAUF, p, m) for p, m in zip(r["ap"], a)]
        jetzt = set()
        for seite, p, m in stufen:
            key = (seite, p)
            jetzt.add(key)
            seit = pers_seit.setdefault(key, r["t"])
            gesamt += m
            if r["t"] - seit >= 3:
                pers += m
        for key in [k for k in pers_seit if k not in jetzt]:
            del pers_seit[key]
        F["pers_anteil"][i] = pers / gesamt if gesamt > 0 else 1.0
    # Intensität: Abschlüsse je 10 s gegen den Median
    n10s, dq = [0] * n, deque()
    s = 0
    for i, r in enumerate(raster):
        dq.append(r["n"])
        s += r["n"]
        if len(dq) > 10:
            s -= dq.popleft()
        n10s[i] = s
    F["n10"] = n10s
    med = statistics.median([x for x in n10s if x > 0] or [1])
    F["intens"] = [x / med for x in n10s]
    # Kyle-Lambda: Mid-Änderung je Minute gegen Nettovolumen in Quote-Währung (je Mio.), letzte 60 Minuten
    minuten: dict[int, list] = {}
    for i, r in enumerate(raster):
        m = minuten.setdefault(r["t"] // 60, [F["mitte"][i], F["mitte"][i], 0.0])
        m[1] = F["mitte"][i]
        m[2] += (r["kauf"] - r["verkauf"]) * F["mitte"][i] / 1e6
    punkte = [(v[2], (v[1] - v[0]) / v[0] * 1e4) for v in list(minuten.values())[-60:] if v[0]]
    F["kyle"] = _ols(punkte)
    return F


def _ols(punkte: list) -> float | None:
    if len(punkte) < 10:
        return None
    mx = statistics.fmean(x for x, _ in punkte)
    my = statistics.fmean(y for _, y in punkte)
    sxx = sum((x - mx) ** 2 for x, _ in punkte)
    return None if sxx <= 0 else sum((x - mx) * (y - my) for x, y in punkte) / sxx


# ======================================================================================
# Kalibrierung: Perzentile je Wert, über bis zu 5 Tage gemittelt
# ======================================================================================

PERZENTILE = {"obi5_p80": ("obi5", 0.8, True), "delta30_p90": ("delta30", 0.9, True), "vol_p20": ("vol_bp", 0.2, False), "vol_p80": ("vol_bp", 0.8, False),
              "vol_p95": ("vol_bp", 0.95, False), "spread_p20": ("spread_bp", 0.2, False), "spread_p80": ("spread_bp", 0.8, False), "spread_p90": ("spread_bp", 0.9, False),
              "intens_p80": ("intens", 0.8, False), "vpin_p90": ("vpin", 0.9, False), "tiefe_p20": ("tiefe", 0.2, False), "tiefe_p80": ("tiefe", 0.8, False),
              "kauf60_p90": ("kauf60", 0.9, False), "verkauf60_p90": ("verkauf60", 0.9, False), "delta300_p50": ("delta300", 0.5, True),
              "tiefe_hinten_med": ("tiefe_hinten", 0.5, False)}


def kalibrieren(wert: str, F: dict, raster: list[dict]) -> dict:
    n = len(raster)
    if n < 120:
        return {}
    spalten = dict(F)
    spalten["tiefe"] = [F["tiefe_b"][i] + F["tiefe_a"][i] for i in range(n)]
    spalten["delta300"] = [F["cvd"][i] - F["cvd"][i - 300] for i in range(300, n)] or [0.0]
    spalten["tiefe_hinten"] = [sum(r["b"][1:]) + (r["b_tief"] or 0) for r in raster] + [sum(r["a"][1:]) + (r["a_tief"] or 0) for r in raster]
    heute = {}
    for k, (sp, q, absolut) in PERZENTILE.items():
        werte = spalten.get(sp) or []
        werte = [abs(x) for x in werte if x is not None] if absolut else [x for x in werte if x is not None]
        werte = [x for x in werte if x != 0] if sp in ("delta30", "delta300", "kauf60", "verkauf60", "vpin", "obi5") else werte
        heute[k] = _perz(werte, q)
    heute["n"] = n
    tag = datetime.fromtimestamp(raster[-1]["t"], timezone.utc).strftime("%Y-%m-%d")
    try:
        with _lock:
            db = _db_signale()
            try:
                db.execute("INSERT OR REPLACE INTO kalibrierung VALUES (?, ?, ?)", (wert, tag, json.dumps(heute)))
                db.commit()
                alte = [json.loads(z[0]) for z in db.execute("SELECT json FROM kalibrierung WHERE wert = ? ORDER BY tag DESC LIMIT ?", (wert, KALIBRIER_TAGE))]
            finally:
                db.close()
    except sqlite3.Error:
        alte = [heute]
    aus = {}
    for k in PERZENTILE:
        werte = [(a[k], a.get("n", 1)) for a in alte if a.get(k) is not None]
        aus[k] = sum(v * g for v, g in werte) / sum(g for _, g in werte) if werte else None
    aus["tage"] = len(alte)
    return aus


# ======================================================================================
# Ort: Wände, VWAP, Volumenprofil
# ======================================================================================

def waende_fuer(wert: str, markt: str, von: int, bis: int, extras: dict | None) -> tuple[list[dict], float, list]:
    """Wände, Stufenbreite und (Krypto) die Minutenspalten der Buchtiefe."""
    if markt == "DE":
        return (extras or {}).get("waende", []), (extras or {}).get("tick", 0.01), []
    with _lock:
        c = _waende_cache.get(wert)
        if c and time.time() - c[0] < 60:
            return c[1], c[2], c[3]
    h = heatmap.krypto(wert, max(von, bis - WAENDE_KRYPTO_S), bis, 60)
    band = h.get("tick") or 1.0
    with _lock:
        _waende_cache[wert] = (time.time(), h.get("waende", []), band, h.get("spalten", []))
    return h.get("waende", []), band, h.get("spalten", [])


def schicht_krypto(raster: list[dict], spalten: list[dict], bp: float = 10.0) -> dict:
    """Je Sekunde die Liquidität in den ersten bp Basispunkten hinter bester Kauf- und Verkaufsstufe (aus den Minutenbändern)."""
    if not spalten:
        return {}
    zeiten = [sp["t"] for sp in spalten]
    hb, ha = [None] * len(raster), [None] * len(raster)
    for i, r in enumerate(raster):
        j = bisect.bisect_right(zeiten, r["t"]) - 1
        if j < 0 or r["t"] - zeiten[j] > 180:
            continue
        sp = spalten[j]
        ha[i] = sum(m for p, m in sp["a"] if r["ask"] < p <= r["ask"] * (1 + bp / 1e4))
        hb[i] = sum(m for p, m in sp["b"] if r["bid"] * (1 - bp / 1e4) <= p < r["bid"])
    werte = [x for x in hb + ha if x is not None and x > 0]
    return {"hinten_b": hb, "hinten_a": ha, "hinten_med": statistics.median(werte) if werte else 0.0}


def profil(raster: list[dict], F: dict) -> dict:
    """VWAP und Volumenprofil (POC, HVN, LVN) über das Raster, aus kauf+verkauf je Preis des letzten Abschlusses."""
    vol = [(r["letzter"] or F["mitte"][i], r["kauf"] + r["verkauf"]) for i, r in enumerate(raster) if r["kauf"] + r["verkauf"] > 0]
    if not vol:
        return {"vwap": None, "poc": None, "hvn": [], "lvn": [], "schritt": None}
    summe = sum(v for _, v in vol)
    vwap = sum(p * v for p, v in vol) / summe
    lo, hi = min(p for p, _ in vol), max(p for p, _ in vol)
    schritt = max((hi - lo) / 40, 1e-9)
    e = 10 ** math.floor(math.log10(schritt))
    schritt = next(f * e for f in (1, 2, 2.5, 5, 10) if f * e >= schritt)
    basis = math.floor(lo / schritt) * schritt
    k = int((hi - basis) / schritt) + 1
    bins = [0.0] * k
    for p, v in vol:
        bins[min(k - 1, int((p - basis) / schritt))] += v
    mitte = lambda j: round(basis + (j + 0.5) * schritt, 8)
    poc = max(range(k), key=lambda j: bins[j])
    p80, p20 = _perz(bins, 0.8), _perz([x for x in bins if x > 0] or [0], 0.2)
    hvn = [mitte(j) for j in range(k) if bins[j] >= p80 and bins[j] > 0 and (j == 0 or bins[j] >= bins[j - 1]) and (j == k - 1 or bins[j] >= bins[j + 1])]
    lvn = [mitte(j) for j in range(1, k - 1) if 0 < bins[j] <= p20 and bins[j] <= bins[j - 1] and bins[j] <= bins[j + 1]]
    return {"vwap": vwap, "poc": mitte(poc), "hvn": hvn[:4], "lvn": lvn[:4], "schritt": schritt}


# ======================================================================================
# Regeln
# ======================================================================================

def _sig(art: str, richtung: int, score: float, t: int, preis: float, begruendung: str, invalidierung: str, horizont: int) -> dict:
    return {"art": art, "name": NAMEN[art], "richtung": richtung, "score": round(min(1.0, max(0.0, score)), 2), "t": int(t), "preis": round(preis, 6),
            "begruendung": begruendung, "invalidierung": invalidierung, "horizont_s": horizont}


def regeln(raster: list[dict], F: dict, kal: dict, waende: list[dict], stufe: float, markt: str, kontext: dict | None = None) -> list[dict]:
    """Alle Signale über das Raster, zeitlich sortiert. kontext (Krypto): liquidationen [(t_s, seite, notional)], oi {t_min: oi}."""
    n = len(raster)
    if n < 60 or not kal:
        return []
    aus: list[dict] = []
    letzte: dict = {}
    t = F["t"]
    mitte = F["mitte"]
    kontext = kontext or {}
    def fmt(x, s=2, vz=False):
        return ("+" if vz and x > 0 else "") + f"{x:,.{s}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    einmal: set = set()

    def feuern(sig: dict, key=None) -> None:
        k = key or (sig["art"], sig["richtung"])
        if key is not None:                                           # je Wand, Eisberg-Stufe nur einmal
            if k in einmal:
                return
            einmal.add(k)
        elif k in letzte and sig["t"] - letzte[k] < sig["horizont_s"]:
            return
        letzte[k] = sig["t"]
        aus.append(sig)

    p_obi, p_d30, p_vp = kal.get("obi5_p80") or 0.5, kal.get("delta30_p90") or 1e300, kal.get("vpin_p90") or 1.0
    p_k60, p_v60 = kal.get("kauf60_p90") or 1e300, kal.get("verkauf60_p90") or 1e300
    med_hinten = kontext["hinten_med"] if kontext.get("hinten_med") else (kal.get("tiefe_hinten_med") or 0.0)
    hinten_b_k, hinten_a_k = kontext.get("hinten_b"), kontext.get("hinten_a")
    p_sp90 = kal.get("spread_p90")
    max300, min300 = _rollmax(mitte, 300, 1), _rollmin(mitte, 300, 1)
    cmax300, cmin300 = _rollmax(F["cvd"], 300, 1), _rollmin(F["cvd"], 300, 1)
    cmax60, cmin60 = _rollmax(F["cvd"], 60, 1), _rollmin(F["cvd"], 60, 1)
    swing_hoch, swing_tief = _rollmax(mitte, 540, 61), _rollmin(mitte, 540, 61)
    max60, min60 = _rollmax(mitte, 60, 0), _rollmin(mitte, 60, 0)
    # Wände nach Zeit
    waende_sortiert = sorted(waende, key=lambda w: w["von"])
    wand_ende = defaultdict(list)
    for w in waende_sortiert:
        wand_ende[w.get("ende_t") or w["bis"]].append(w)
    offen_waende: list[dict] = []
    wi = 0
    # Eisberg-Zustand: Lauf der besten Stufe
    eis = {KAUF: None, VERKAUF: None}
    # Liquidationen je Sekunde (Krypto)
    liq_s: dict[int, list] = defaultdict(lambda: [0.0, 0.0])          # t -> [long-Liquidationen (Verkäufe), short (Käufe)] in Quote
    for ts, seite, notional in kontext.get("liquidationen", []):
        liq_s[ts][0 if seite == "long" else 1] += notional
    liq60 = deque()
    sl = [0.0, 0.0]
    liq_summen = []
    oi = kontext.get("oi") or {}
    oi_zeiten = sorted(oi)
    for i in range(n):
        ti = t[i]
        m = mitte[i]
        bid, ask = raster[i]["bid"], raster[i]["ask"]
        while wi < len(waende_sortiert) and waende_sortiert[wi]["von"] <= ti:
            offen_waende.append(waende_sortiert[wi])
            wi += 1
        offen_waende = [w for w in offen_waende if (w.get("ende_t") or w["bis"]) >= ti - 1 or w["ende"] == "steht"]
        vol = max(F["vol_bp"][i], F["spread_bp"][i], 0.1)
        # 1. Ungleichgewicht mit Fluss
        ob, oz, mi = F["obi5"][i], F["ofi_z"][i], F["micro_bp"][i]
        if abs(ob) >= p_obi and abs(oz) >= 1.5 and (mi > 0) == (ob > 0) and (oz > 0) == (ob > 0) and mi != 0:
            ri = 1 if ob > 0 else -1
            score = 0.4 + 0.3 * min(1.0, (abs(oz) - 1.5) / 1.5) + 0.3 * min(1.0, (abs(ob) - p_obi) / max(1e-9, 1 - p_obi))
            feuern(_sig("imbalance_momentum", ri, score, ti, m,
                        f"Buch {'Kauf' if ri > 0 else 'Verkauf'}seite schwerer (OBI 5 Stufen {fmt(ob, 2, True)}), OFI-z {fmt(oz, 1, True)} in 5 s, Microprice {fmt(mi, 2, True)} bp.",
                        f"OFI-z dreht unter 0 oder Spread über {fmt(p_sp90 or F['spread_bp'][i] * 2)} bp.", 30))
        # 2. Absorption
        d30 = F["delta30"][i]
        if abs(d30) >= p_d30 and abs(F["dm30"][i]) < 0.3 * vol and i >= 30:
            ri = 1 if d30 < 0 else -1                                   # Verkäufer werden absorbiert → aufwärts
            seite_wand = "kauf" if ri > 0 else "verkauf"
            wand = next((w for w in offen_waende if w["seite"] == seite_wand and abs((bid if ri > 0 else ask) - w["preis"]) <= 3 * stufe), None)
            hvn = kontext.get("hvn") or []
            schritt = kontext.get("profil_schritt") or stufe
            an_hvn = any(abs(m - h) <= schritt for h in hvn)
            am_extrem = (m - F["tief"][i]) / m * 1e4 <= 0.5 * vol if ri > 0 else (F["hoch"][i] - m) / m * 1e4 <= 0.5 * vol
            if wand or an_hvn or am_extrem:
                ort = f"an der {'Kauf' if ri > 0 else 'Verkaufs'}wand {_preis(wand['preis'])}" if wand else "am Volumenschwerpunkt" if an_hvn else ("am Tagestief" if ri > 0 else "am Tageshoch")
                score = 0.4 + 0.3 * min(1.0, (abs(d30) - p_d30) / p_d30) + (0.3 if wand else 0.2)
                feuern(_sig("absorption", ri, score, ti, m,
                            f"{'Verkäufer' if ri > 0 else 'Käufer'} warfen {fmt(abs(d30), 2 if markt == 'KRYPTO' else 0)} in 30 s {ort}, der Kurs bewegte sich nur {fmt(abs(F['dm30'][i]), 1)} bp.",
                            f"Kurs schließt {'unter' if ri > 0 else 'über'} {_preis(wand['preis'] if wand else (F['tief'][i] if ri > 0 else F['hoch'][i]))}.", 300))
        # 3. Delta-Divergenz
        if i >= 300 and max300[i] is not None:
            if m > max300[i] and F["cvd"][i] < cmax300[i] and F["cvd"][i] <= cmax60[i]:
                feuern(_sig("delta_divergenz", -1, 0.45 + 0.3 * min(1.0, (cmax300[i] - F["cvd"][i]) / max(1e-9, kal.get("delta300_p50") or 1)), ti, m,
                            f"Neues 5-Minuten-Hoch bei {_preis(m)}, das kumulierte Delta bleibt {fmt(cmax300[i] - F['cvd'][i], 2 if markt == 'KRYPTO' else 0)} unter seinem Hoch.",
                            "Delta macht ein neues Hoch oder der Kurs steigt mehr als 1 Volatilitätseinheit weiter.", 300))
            elif m < min300[i] and F["cvd"][i] > cmin300[i] and F["cvd"][i] >= cmin60[i]:
                feuern(_sig("delta_divergenz", 1, 0.45 + 0.3 * min(1.0, (F["cvd"][i] - cmin300[i]) / max(1e-9, kal.get("delta300_p50") or 1)), ti, m,
                            f"Neues 5-Minuten-Tief bei {_preis(m)}, das kumulierte Delta bleibt {fmt(F['cvd'][i] - cmin300[i], 2 if markt == 'KRYPTO' else 0)} über seinem Tief.",
                            "Delta macht ein neues Tief oder der Kurs fällt mehr als 1 Volatilitätseinheit weiter.", 300))
        # 4. Abprall an Wand
        for w in offen_waende:
            if ti - w["von"] < 20:
                continue
            if w["seite"] == "kauf" and 0 <= bid - w["preis"] <= 2 * stufe:
                ri = 1
            elif w["seite"] == "verkauf" and 0 <= w["preis"] - ask <= 2 * stufe:
                ri = -1
            else:
                continue
            feuern(_sig("wand_abprall", ri, 0.5 + 0.3 * min(1.0, (ti - w["von"]) / 300) + (0.2 if w["ende"] == "steht" else 0.0), ti, m,
                        f"{'Kauf' if ri > 0 else 'Verkaufs'}wand {fmt(w['menge'], 2 if markt == 'KRYPTO' else 0)} bei {_preis(w['preis'])} steht seit {int(ti - w['von'])} s, der Kurs ist {int(round(abs((bid if ri > 0 else ask) - w['preis']) / stufe))} Stufen davor.",
                        f"Wand wird abgearbeitet oder verschwindet; Kurs schließt {'unter' if ri > 0 else 'über'} {_preis(w['preis'])}.", 120),
                   key=("wand_abprall", w["seite"], w["preis"], w["von"]))
        # 5. Wand verschwindet (Spoofing-Muster)
        for w in wand_ende.get(ti, []):
            if w["ende"] == "zurückgezogen" and w.get("nah") and (w.get("gehandelt") or 0) < 0.2 * w["menge"]:
                ri = -1 if w["seite"] == "kauf" else 1
                feuern(_sig("fake_wand", ri, 0.55 + 0.25 * min(1.0, w["dauer_s"] / 120), ti, m,
                            f"{'Kauf' if ri < 0 else 'Verkaufs'}wand {fmt(w['menge'], 2 if markt == 'KRYPTO' else 0)} bei {_preis(w['preis'])} verschwand nach {w['dauer_s']} s bei {w.get('abstand_ticks', '?')} Stufen Abstand, ohne gehandelt zu werden.",
                            "Die Wand kehrt binnen 60 s zurück oder wird an anderer Stelle neu gestellt.", 120),
                       key=("fake_wand", w["seite"], w["preis"], w["von"]))
        # 6. Eisberg: beste Stufe hält, dort wird ein Vielfaches des Sichtbaren gehandelt
        for seite, preis, sichtbar, getroffen in ((KAUF, bid, raster[i]["b"][0], raster[i]["verkauf"]), (VERKAUF, ask, raster[i]["a"][0], raster[i]["kauf"])):
            e = eis[seite]
            if e is None or e["preis"] != preis:
                e = eis[seite] = {"preis": preis, "start": ti, "sichtbar": sichtbar, "gehandelt": 0.0, "treffer": 0, "gemeldet": False}
            e["sichtbar"] = max(e["sichtbar"], sichtbar)
            if getroffen > 0:
                e["gehandelt"] += getroffen
                e["treffer"] += 1
            if not e["gemeldet"] and e["treffer"] >= 3 and e["sichtbar"] > 0 and e["gehandelt"] >= 3 * e["sichtbar"] and e["gehandelt"] * preis >= MIN_NOTIONAL.get(markt, 20_000):
                e["gemeldet"] = True
                ri = 1 if seite == KAUF else -1
                feuern(_sig("eisberg", ri, 0.5 + 0.3 * min(1.0, e["gehandelt"] / e["sichtbar"] / 10) + 0.2 * min(1.0, (ti - e["start"]) / 120), ti, m,
                            f"Bei {_preis(preis)} wurden {fmt(e['gehandelt'], 2 if markt == 'KRYPTO' else 0)} gehandelt, sichtbar waren höchstens {fmt(e['sichtbar'], 2 if markt == 'KRYPTO' else 0)} ({fmt(e['gehandelt'] / e['sichtbar'], 1)}×), die Stufe hält seit {int(ti - e['start'])} s.",
                            f"Die Stufe {_preis(preis)} fällt: das Auffüllen hört auf.", 300), key=("eisberg", seite, preis))
        # 7. Ausbruch in dünne Schicht
        if i >= 60 and med_hinten > 0:
            if hinten_a_k is not None:
                hinten_a, hinten_b = hinten_a_k[i], hinten_b_k[i]
            else:
                hinten_a = sum(raster[i]["a"][1:]) + (raster[i]["a_tief"] or 0)
                hinten_b = sum(raster[i]["b"][1:]) + (raster[i]["b_tief"] or 0)
            if hinten_a is None or hinten_b is None:
                pass
            elif m > F["hoch"][i - 1] and hinten_a < 0.3 * med_hinten and F["delta10"][i] > 0:
                feuern(_sig("duennschicht_ausbruch", 1, 0.5 + 0.3 * (1 - hinten_a / max(1e-9, 0.3 * med_hinten)) + 0.2 * min(1.0, F["delta10"][i] / max(1e-9, p_k60 / 6)), ti, m,
                            f"Kurs über dem bisherigen Hoch {_preis(F['hoch'][i - 1])}, hinter der besten Verkaufsstufe liegen nur {fmt(hinten_a, 2 if markt == 'KRYPTO' else 0)} ({hinten_a / max(1e-9, med_hinten) * 100:.0f} % des Üblichen), Delta 10 s {fmt(F['delta10'][i], 2 if markt == 'KRYPTO' else 0)}.",
                            f"Kurs fällt zurück unter {_preis(F['hoch'][i - 1])}.", 120))
            elif m < F["tief"][i - 1] and hinten_b < 0.3 * med_hinten and F["delta10"][i] < 0:
                feuern(_sig("duennschicht_ausbruch", -1, 0.5 + 0.3 * (1 - hinten_b / max(1e-9, 0.3 * med_hinten)) + 0.2 * min(1.0, -F["delta10"][i] / max(1e-9, p_v60 / 6)), ti, m,
                            f"Kurs unter dem bisherigen Tief {_preis(F['tief'][i - 1])}, hinter der besten Kaufstufe liegen nur {fmt(hinten_b, 2 if markt == 'KRYPTO' else 0)} ({hinten_b / max(1e-9, med_hinten) * 100:.0f} % des Üblichen), Delta 10 s {fmt(F['delta10'][i], 2 if markt == 'KRYPTO' else 0)}.",
                            f"Kurs steigt zurück über {_preis(F['tief'][i - 1])}.", 120))
        # 8. Sweep und Rückkehr
        if i >= 600 and swing_hoch[i] is not None:
            if max60[i] >= swing_hoch[i] + stufe and m < swing_hoch[i] and F["kauf60"][i] >= p_k60 and F["delta10"][i] < 0:
                feuern(_sig("sweep_reclaim", -1, 0.5 + 0.25 * min(1.0, (max60[i] - swing_hoch[i]) / m * 1e4 / vol) + 0.25 * min(1.0, F["kauf60"][i] / p_k60 - 1), ti, m,
                            f"Spitze bis {_preis(max60[i])} über das Hoch {_preis(swing_hoch[i])} mit {fmt(F['kauf60'][i], 2 if markt == 'KRYPTO' else 0)} Käufen in 60 s, Kurs wieder darunter, Delta dreht ({fmt(F['delta10'][i], 2 if markt == 'KRYPTO' else 0)}).",
                            f"Kurs schließt erneut über {_preis(max60[i])}.", 300))
            elif min60[i] <= swing_tief[i] - stufe and m > swing_tief[i] and F["verkauf60"][i] >= p_v60 and F["delta10"][i] > 0:
                feuern(_sig("sweep_reclaim", 1, 0.5 + 0.25 * min(1.0, (swing_tief[i] - min60[i]) / m * 1e4 / vol) + 0.25 * min(1.0, F["verkauf60"][i] / p_v60 - 1), ti, m,
                            f"Spitze bis {_preis(min60[i])} unter das Tief {_preis(swing_tief[i])} mit {fmt(F['verkauf60'][i], 2 if markt == 'KRYPTO' else 0)} Verkäufen in 60 s, Kurs wieder darüber, Delta dreht ({fmt(F['delta10'][i], 2 if markt == 'KRYPTO' else 0)}).",
                            f"Kurs schließt erneut unter {_preis(min60[i])}.", 300))
        # 9. Toxischer Fluss
        if F["vpin"][i] > p_vp and p_vp < 1:
            feuern(_sig("vpin_stop", 0, (F["vpin"][i] - p_vp) / max(1e-9, 1 - p_vp), ti, m,
                        f"VPIN {fmt(F['vpin'][i], 2)} über dem 90. Perzentil ({fmt(p_vp, 2)}): einseitiger Fluss, Kosten und Sprungrisiko steigen.",
                        f"VPIN fällt unter {fmt(p_vp, 2)}.", 300))
        # 10. Liquidationskaskade (Krypto)
        if liq_s:
            l = liq_s.get(ti, (0.0, 0.0))
            liq60.append(l)
            sl[0] += l[0]
            sl[1] += l[1]
            if len(liq60) > 60:
                y = liq60.popleft()
                sl[0] -= y[0]
                sl[1] -= y[1]
            liq_summen.append(sl[0] + sl[1])
            p95 = _perz([x for x in liq_summen if x > 0] or [0], 0.95) if len(liq_summen) >= 600 else None
            if p95 and sl[0] + sl[1] >= p95 and oi_zeiten:
                j = bisect.bisect_right(oi_zeiten, ti // 60) - 1
                oi_jetzt = oi[oi_zeiten[j]] if j >= 0 else None
                k = bisect.bisect_right(oi_zeiten, ti // 60 - 5) - 1
                oi_vor = oi[oi_zeiten[k]] if k >= 0 else None
                long_seite = sl[0] >= sl[1]
                if oi_jetzt and oi_vor and oi_jetzt < oi_vor and ((F["delta10"][i] > 0) if long_seite else (F["delta10"][i] < 0)):
                    ri = 1 if long_seite else -1
                    feuern(_sig("liquidations_kaskade", ri, 0.5 + 0.3 * min(1.0, (sl[0] + sl[1]) / p95 - 1) + 0.2 * min(1.0, (oi_vor - oi_jetzt) / oi_vor * 100), ti, m,
                                f"{fmt(sl[0] + sl[1], 0)} $ {'Long' if long_seite else 'Short'}-Liquidationen in 60 s, Open Interest −{fmt((oi_vor - oi_jetzt) / oi_vor * 100, 2)} % in 5 Min., Delta dreht {'nach oben' if ri > 0 else 'nach unten'}.",
                                "Die Kaskade läuft weiter: neue Liquidationen und weiter fallendes Open Interest.", 600))
    return sorted(aus, key=lambda s: s["t"])


def oi_matrix(F: dict, oi: dict, i_von: int, i_bis: int) -> dict | None:
    """Preis × OI × Delta der letzten 15 Minuten als Einordnung (kein Richtungssignal)."""
    if not oi or i_bis - i_von < 60:
        return None
    zeiten = sorted(oi)
    t0, t1 = F["t"][i_von] // 60, F["t"][i_bis] // 60
    j0, j1 = bisect.bisect_right(zeiten, t0) - 1, bisect.bisect_right(zeiten, t1) - 1
    if j0 < 0 or j1 < 0:
        return None
    dp = (F["mitte"][i_bis] - F["mitte"][i_von]) / F["mitte"][i_von] * 1e4
    doi = (oi[zeiten[j1]] - oi[zeiten[j0]]) / max(1e-9, oi[zeiten[j0]]) * 100
    dc = F["cvd"][i_bis] - F["cvd"][i_von]
    if abs(dp) < 1 and abs(doi) < 0.05:
        lage = "ruhig: Kurs und Open Interest bewegen sich kaum"
    elif dp > 0 and doi > 0:
        lage = "Aufbau: steigender Kurs mit neuen Positionen" + (" und Kaufdruck" if dc > 0 else ", aber ohne Kaufdruck im Spot")
    elif dp > 0 and doi <= 0:
        lage = "Short-Squeeze: steigender Kurs, Positionen werden geschlossen (anfällig)"
    elif dp <= 0 and doi > 0:
        lage = "neue Shorts: fallender Kurs mit neuen Positionen"
    else:
        lage = "Long-Liquidation: fallender Kurs, Positionen werden geschlossen"
    return _sig("oi_matrix", 0, 0.3, F["t"][i_bis], F["mitte"][i_bis],
                f"{lage}. Kurs {_z(dp, 1)} bp, Open Interest {_z(doi, 2)} %, Delta {_z(dc, 2)} in 15 Min.", "Einordnung, keine Invalidierung.", 900)


# ======================================================================================
# Journal und Markout
# ======================================================================================

def _mitte_bei(t_liste: list, mitte: list, t: int, toleranz: int = 5) -> float | None:
    j = bisect.bisect_left(t_liste, t)
    if j < len(t_liste) and t_liste[j] - t <= toleranz:
        return mitte[j]
    return None


def markout(sig: dict, t_liste: list, mitte: list, spread_bp: float, gebuehr: float, bis_t: int) -> dict | None:
    m0 = _mitte_bei(t_liste, mitte, sig["t"])
    if m0 is None:
        return None
    ri = sig["richtung"] or 1
    aus = {}
    for h in HORIZONTE:
        if sig["t"] + h > bis_t:
            aus[f"m{h}"] = None
            continue
        m1 = _mitte_bei(t_liste, mitte, sig["t"] + h)
        aus[f"m{h}"] = None if m1 is None else _r((m1 - m0) / m0 * 1e4 * ri, 2)
    for h in (30, 60, 300):
        aus[f"netto{h}"] = None if aus[f"m{h}"] is None else _r(aus[f"m{h}"] - spread_bp / 2 - gebuehr, 2)
    return aus


def journal_schreiben(wert: str, markt: str, signale: list[dict], F: dict, bis_t: int) -> list[dict]:
    """Neue Signale ins Journal (Entprellung auch gegen frühere Läufe), Markouts nachtragen; gibt die Journalzeilen im Raster-Zeitraum zurück."""
    if not F["t"]:
        return []
    gebuehr = GEBUEHR.get(markt, 0.0)
    t_liste, mitte = F["t"], F["mitte"]
    with _lock:
        db = _db_signale()
        try:
            for s in signale:
                if s["art"] == "oi_matrix":
                    continue
                vorher = db.execute("SELECT MAX(t) FROM signale WHERE wert = ? AND art = ? AND richtung = ? AND t < ?", (wert, s["art"], s["richtung"], s["t"])).fetchone()[0]
                if vorher is not None and s["t"] - vorher < s["horizont_s"]:
                    continue
                j = bisect.bisect_left(t_liste, s["t"])
                sp = F["spread_bp"][min(j, len(t_liste) - 1)]
                db.execute("INSERT OR IGNORE INTO signale (wert, t, art, richtung, score, preis, mitte, spread_bp, begruendung, invalidierung, horizont_s, markt) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (wert, s["t"], s["art"], s["richtung"], s["score"], s["preis"], mitte[min(j, len(t_liste) - 1)], _r(sp, 3),
                                                              s["begruendung"], s["invalidierung"], s["horizont_s"], markt))
            offen = db.execute("SELECT t, art, richtung, spread_bp FROM signale WHERE wert = ? AND t >= ? AND t <= ? AND m300 IS NULL",
                               (wert, t_liste[0], bis_t)).fetchall()
            for t, art, richtung, sp in offen:
                mo = markout({"t": t, "richtung": richtung}, t_liste, mitte, sp or 0.0, gebuehr, bis_t)
                if mo and any(v is not None for v in mo.values()):
                    db.execute("UPDATE signale SET m5 = COALESCE(?, m5), m30 = COALESCE(?, m30), m60 = COALESCE(?, m60), m300 = COALESCE(?, m300), "
                               "netto30 = COALESCE(?, netto30), netto60 = COALESCE(?, netto60), netto300 = COALESCE(?, netto300) WHERE wert = ? AND t = ? AND art = ? AND richtung = ?",
                               (mo["m5"], mo["m30"], mo["m60"], mo["m300"], mo["netto30"], mo["netto60"], mo["netto300"], wert, t, art, richtung))
            db.commit()
            zeilen = db.execute("SELECT t, art, richtung, score, preis, mitte, spread_bp, begruendung, invalidierung, horizont_s, m5, m30, m60, m300, netto30, netto60, netto300 "
                                "FROM signale WHERE wert = ? AND t >= ? AND t <= ? ORDER BY t", (wert, t_liste[0], bis_t)).fetchall()
        finally:
            db.close()
    return [_zeile(z) for z in zeilen]


def _zeile(z) -> dict:
    t, art, richtung, score, preis, mitte, sp, beg, inv, hz, m5, m30, m60, m300, n30, n60, n300 = z
    return {"art": art, "name": NAMEN.get(art, art), "richtung": richtung, "score": score, "t": t, "preis": preis, "mitte": mitte, "spread_bp": sp,
            "begruendung": beg, "invalidierung": inv, "horizont_s": hz,
            "markout": None if m5 is None and m30 is None and m60 is None and m300 is None else {"m5": m5, "m30": m30, "m60": m60, "m300": m300, "netto30": n30, "netto60": n60, "netto300": n300}}


def markouts_nachtragen_alt(wert: str, jetzt: int) -> int:
    """Krypto: Journalzeilen außerhalb des laufenden Fensters, deren Markout noch fehlt, über Punktabfragen auf buch_1s füllen."""
    with _lock:
        db = _db_signale()
        try:
            offen = db.execute("SELECT t, art, richtung, spread_bp, markt FROM signale WHERE wert = ? AND m300 IS NULL AND t < ? AND t > ? LIMIT 200",
                               (wert, jetzt - FENSTER_THREAD, jetzt - 3 * 86400)).fetchall()
        finally:
            db.close()
    if not offen:
        return 0
    kern = lesen._db()
    if kern is None:
        return 0
    n = 0
    try:
        def mitte_bei(t):
            z = kern.execute("SELECT bid, ask FROM buch_1s WHERE id = ? AND t >= ? AND t <= ? ORDER BY t LIMIT 1", (wert, t, t + 5)).fetchone()
            return (z[0] + z[1]) / 2 if z and z[0] and z[1] else None
        aenderungen = []
        for t, art, richtung, sp, markt in offen:
            m0 = mitte_bei(t)
            if m0 is None:
                aenderungen.append((-1, -1, -1, -1, -1, -1, -1, wert, t, art, richtung))     # keine Daten: als unbestimmt markieren
                continue
            ri = richtung or 1
            mo = {}
            for h in HORIZONTE:
                m1 = mitte_bei(t + h)
                mo[h] = None if m1 is None else _r((m1 - m0) / m0 * 1e4 * ri, 2)
            g = GEBUEHR.get(markt, 0.0)
            netto = {h: None if mo[h] is None else _r(mo[h] - (sp or 0) / 2 - g, 2) for h in (30, 60, 300)}
            aenderungen.append((mo[5], mo[30], mo[60], mo[300] if mo[300] is not None else -999, netto[30], netto[60], netto[300], wert, t, art, richtung))
    except sqlite3.Error:
        return 0
    finally:
        kern.close()
    with _lock:
        db = _db_signale()
        try:
            for a in aenderungen:
                db.execute("UPDATE signale SET m5=?, m30=?, m60=?, m300=?, netto30=?, netto60=?, netto300=? WHERE wert=? AND t=? AND art=? AND richtung=?", a)
                n += 1
            db.commit()
        finally:
            db.close()
    return n


def statistik(wert: str | None = None, tage: int = STATISTIK_TAGE) -> dict:
    seit = int(time.time()) - tage * 86400
    with _lock:
        db = _db_signale()
        try:
            frage = "SELECT wert, art, richtung, m30, m60, netto30, netto60, netto300 FROM signale WHERE t >= ? AND m300 IS NOT NULL AND m300 > -900"
            zeilen = db.execute(frage + (" AND wert = ?" if wert else ""), (seit, wert) if wert else (seit,)).fetchall()
        finally:
            db.close()
    def fassen(zl):
        je_art = defaultdict(list)
        for z in zl:
            je_art[z[1]].append(z)
        aus = []
        for art in NAMEN:
            l = je_art.get(art)
            if not l:
                continue
            ger = [z for z in l if z[2] != 0]
            t30 = [z[3] > 0 for z in ger if z[3] is not None]
            t60 = [z[4] > 0 for z in ger if z[4] is not None]
            aus.append({"art": art, "name": NAMEN[art], "n": len(l), "treffer30": _r(sum(t30) / len(t30) * 100, 1) if t30 else None,
                        "treffer60": _r(sum(t60) / len(t60) * 100, 1) if t60 else None,
                        "netto30_bp": _r(statistics.fmean([z[5] for z in ger if z[5] is not None]), 2) if any(z[5] is not None for z in ger) else None,
                        "netto60_bp": _r(statistics.fmean([z[6] for z in ger if z[6] is not None]), 2) if any(z[6] is not None for z in ger) else None,
                        "netto300_bp": _r(statistics.fmean([z[7] for z in ger if z[7] is not None]), 2) if any(z[7] is not None for z in ger) else None})
        return aus
    je_wert = defaultdict(list)
    for z in zeilen:
        je_wert[z[0]].append(z)
    namen = {w["id"]: w for w in lesen.watchlist()}
    return {"tage": tage, "gesamt": fassen(zeilen),
            "werte": [{"wert": w, "symbol": namen.get(w, {}).get("symbol", w), "statistik": fassen(l)} for w, l in sorted(je_wert.items())]}


def journal(wert: str | None = None, limit: int = 200) -> dict:
    with _lock:
        db = _db_signale()
        try:
            frage = ("SELECT wert, t, art, richtung, score, preis, mitte, spread_bp, begruendung, invalidierung, horizont_s, m5, m30, m60, m300, netto30, netto60, netto300 "
                     "FROM signale" + (" WHERE wert = ?" if wert else "") + " ORDER BY t DESC LIMIT ?")
            zeilen = db.execute(frage, (wert, limit) if wert else (limit,)).fetchall()
        finally:
            db.close()
    return {"signale": [{"wert": z[0], **_zeile(z[1:])} for z in zeilen]}


# ======================================================================================
# Lage: alles zusammen für die API
# ======================================================================================

def _regime(F: dict, kal: dict, i: int) -> dict:
    j0 = max(0, i - 119)                                  # letzte zwei Minuten statt der letzten Sekunde: ein Punkt allein ist oft 0
    sp = statistics.median(F["spread_bp"][j0:i + 1])
    vol, it, vp = max(F["vol_bp"][j0:i + 1]), max(F["intens"][j0:i + 1]), F["vpin"][i]
    tiefe = statistics.median(F["tiefe_b"][j] + F["tiefe_a"][j] for j in range(j0, i + 1))
    rel = lambda x, p: None if not p else x / p
    stress = (kal.get("spread_p90") and sp > kal["spread_p90"] and kal.get("tiefe_p20") and tiefe < kal["tiefe_p20"]) or (kal.get("vol_p95") and vol > kal["vol_p95"]) \
        or (kal.get("vpin_p90") and vp > kal["vpin_p90"])
    aktiv = (kal.get("vol_p80") and vol > kal["vol_p80"]) or (kal.get("intens_p80") and it > kal["intens_p80"]) or (kal.get("spread_p80") and sp > kal["spread_p80"])
    name = "stress" if stress else "aktiv" if aktiv else "ruhig"
    teile = [f"Spread {'weit' if kal.get('spread_p80') and sp > kal['spread_p80'] else 'eng' if kal.get('spread_p20') and sp < kal['spread_p20'] else 'normal'}",
             f"Volatilität {'hoch' if kal.get('vol_p80') and vol > kal['vol_p80'] else 'niedrig' if kal.get('vol_p20') and vol < kal['vol_p20'] else 'normal'}",
             f"Abschlüsse {'dicht' if kal.get('intens_p80') and it > kal['intens_p80'] else 'normal'}",
             f"Tiefe {'dünn' if kal.get('tiefe_p20') and tiefe < kal['tiefe_p20'] else 'dick' if kal.get('tiefe_p80') and tiefe > kal['tiefe_p80'] else 'normal'}"]
    if kal.get("vpin_p90") and vp > kal["vpin_p90"]:
        teile.append("Fluss einseitig (VPIN hoch)")
    return {"name": name, "text": ", ".join(teile) + ".", "spread_bp": _r(sp, 3), "vol_bp": _r(vol, 2), "intensitaet": _r(it, 2),
            "tiefe": _r(rel(tiefe, kal.get("tiefe_p80")), 2), "vpin": _r(vp, 3), "kyle_bp_je_mio": _r(F.get("kyle"), 2),
            "persistenz": None if F["pers_anteil"][i] is None else _r(F["pers_anteil"][i] * 100, 0)}


def _lage_text(bausteine: list, ri: int, stufe: str, ort: dict, vpin_aktiv: bool) -> str:
    if not bausteine:
        return "Kein Signal im Buch: " + ("der Fluss ist einseitig, Risiko hoch." if vpin_aktiv else "ausgeglichen, nichts zu tun.")
    namen = ", ".join(b["name"] for b in bausteine if b["richtung"] == ri)
    richtung = "nach oben" if ri > 0 else "nach unten"
    ortstext = ""
    if ri > 0 and ort.get("wand_kauf") and (ort.get("abstand_wand_kauf_bp") or 99) < 10:
        ortstext = f", Kaufwand {_preis(ort['wand_kauf']['preis'])} darunter"
    elif ri < 0 and ort.get("wand_verkauf") and (ort.get("abstand_wand_verkauf_bp") or 99) < 10:
        ortstext = f", Verkaufswand {_preis(ort['wand_verkauf']['preis'])} darüber"
    gegen = [b for b in bausteine if b["richtung"] == -ri]
    mit = [b for b in bausteine if b["richtung"] == ri]
    return (f"{len(mit)} Baustein{'e' if len(mit) != 1 else ''} {richtung}: {namen}{ortstext}. "
            + (f"Gegenläufig: {', '.join(b['name'] for b in gegen)}. " if gegen else "")
            + ("Risikofilter aktiv: einseitiger Fluss. " if vpin_aktiv else "")
            + ("Setup, drei unabhängige Bausteine stimmen überein." if stufe == "Setup" else "Hypothese, noch keine drei Bausteine."))


def lage(wert: str, von: int | None = None, bis: int | None = None, schritt: int = 5, tag: str | None = None, nur_journal: bool = False) -> dict:
    t0 = time.time()
    w = _wert(wert)
    markt = w["markt"]
    if markt not in ("DE", "KRYPTO"):
        return {"wert": w["id"], "symbol": w["symbol"], "name": w["name"], "markt": markt, "hinweis": "Für diesen Markt gibt es kein Orderbuch in der Sammlung.",
                "signale": [], "signale_aktiv": [], "statistik": [], "reihen": {"schritt": schritt, "t": []}, "erklaerung": ERKLAERUNG}
    extras = None
    if markt == "DE":
        tag = tag or next((t for t in ROH.tage() if w["id"] in ROH.werte(t)), None)
        if not tag:
            return _leer(w, schritt, "Keine Xetra-Rohdaten für diesen Wert.")
        if not nur_journal:
            ZULETZT_DE[w["id"]] = time.time()
        r, extras = _lauf_de(w["id"], tag)
        von_ = von or (r[0]["t"] if r else 0)
        bis_ = bis or (r[-1]["t"] if r else 0)
    else:
        bis_ = bis or int(time.time())
        von_ = von or bis_ - FENSTER_KRYPTO
        r = raster_krypto(w["id"], von_, bis_)
    if len(r) < 120:
        return _leer(w, schritt, "Noch zu wenig Sekundendaten im Zeitraum." + (" Der Kern sammelt buch_1s seit dem Umbau." if markt == "KRYPTO" else " Das Buch gibt es nur 09:01–17:30 Uhr."), tag)
    F = features(r, markt)
    kal = kalibrieren(w["id"], F, r)
    waende, stufe, spalten = waende_fuer(w["id"], markt, von_, bis_, extras)
    pr = profil(r, F)
    kontext: dict = {"hvn": pr["hvn"] + ([pr["poc"]] if pr["poc"] else []), "profil_schritt": pr["schritt"]}
    if markt == "KRYPTO":
        kontext.update(schicht_krypto(r, spalten))
    liq, perp = [], None
    if markt == "KRYPTO":
        liq, perp = _krypto_kontext(w["id"], von_, bis_)
        kontext["liquidationen"] = [(x["t"] // 1000, x["seite"], x["preis"] * x["menge"]) for x in liq]
        kontext["oi"] = perp["_oi"] if perp else {}
    signale = regeln(r, F, kal, waende, stufe, markt, kontext)
    zeilen = journal_schreiben(w["id"], markt, signale, F, bis_)
    if nur_journal:
        return {"wert": w["id"], "signale": len(signale), "journal": len(zeilen), "dauer_s": round(time.time() - t0, 2)}
    i = len(r) - 1
    jetzt = r[-1]["t"]
    aktiv = []                                                    # je Art und Richtung nur das jüngste Signal: zwei Absorptionen sind ein Baustein, nicht zwei
    for s in sorted((s for s in zeilen if jetzt - s["t"] <= s["horizont_s"]), key=lambda s: s["t"], reverse=True):
        if not any(a["art"] == s["art"] and a["richtung"] == s["richtung"] for a in aktiv):
            aktiv.append(s)
    if markt == "KRYPTO" and perp:
        om = oi_matrix(F, perp["_oi"], max(0, i - 900), i)
        if om:
            aktiv.append({**om, "markout": None})
    # Ort
    kurs = F["mitte"][i]
    wk = max((x for x in waende if x["seite"] == "kauf" and x["ende"] == "steht" and x["preis"] <= r[-1]["bid"]), key=lambda x: x["preis"], default=None)
    wv = min((x for x in waende if x["seite"] == "verkauf" and x["ende"] == "steht" and x["preis"] >= r[-1]["ask"]), key=lambda x: x["preis"], default=None)
    ort = {"kurs": _r(kurs, 6), "vwap": _r(pr["vwap"], 6), "poc": pr["poc"], "hvn": pr["hvn"], "lvn": pr["lvn"], "hoch": _r(F["hoch"][i], 6), "tief": _r(F["tief"][i], 6),
           "wand_kauf": {"preis": wk["preis"], "menge": wk["menge"], "seit_s": int(jetzt - wk["von"])} if wk else None,
           "wand_verkauf": {"preis": wv["preis"], "menge": wv["menge"], "seit_s": int(jetzt - wv["von"])} if wv else None,
           "abstand_wand_kauf_bp": _r((kurs - wk["preis"]) / kurs * 1e4, 1) if wk else None,
           "abstand_wand_verkauf_bp": _r((wv["preis"] - kurs) / kurs * 1e4, 1) if wv else None,
           "abstand_vwap_bp": _r((kurs - pr["vwap"]) / kurs * 1e4, 1) if pr["vwap"] else None, "stufe": stufe}
    # Confluence
    gerichtet = [s for s in aktiv if s["richtung"] != 0]
    vpin_aktiv = any(s["art"] == "vpin_stop" for s in aktiv)
    summe = defaultdict(float)
    for s in gerichtet:
        summe[s["richtung"]] += s["score"]
    ri = 0 if not summe else max(summe, key=summe.get)
    bausteine = [{"name": s["name"], "art": s["art"], "richtung": s["richtung"], "score": s["score"]} for s in gerichtet]
    if ri and ((ri > 0 and wk and ort["abstand_wand_kauf_bp"] is not None and ort["abstand_wand_kauf_bp"] < 10) or (ri < 0 and wv and ort["abstand_wand_verkauf_bp"] is not None and ort["abstand_wand_verkauf_bp"] < 10)):
        bausteine.append({"name": "Wand im Rücken", "art": "ort", "richtung": ri, "score": 0.3})
    mit = [b for b in bausteine if b["richtung"] == ri]
    score = (sum(b["score"] for b in mit) - sum(b["score"] for b in bausteine if b["richtung"] == -ri)) / max(1, len(mit)) if mit else 0.0
    stufe_text = "neutral" if not mit else "Setup" if len(mit) >= 3 and score >= 0.5 and not vpin_aktiv else "Hypothese"
    lage_ = {"score": _r(max(0.0, min(1.0, score)), 2), "richtung": ri, "stufe": stufe_text, "text": _lage_text(bausteine, ri, stufe_text, ort, vpin_aktiv), "bausteine": bausteine}
    # Reihen, verdichtet
    idx = list(range(0, len(r), max(1, schritt)))
    if idx[-1] != len(r) - 1:
        idx.append(len(r) - 1)
    reihen = {"schritt": schritt, "t": [F["t"][j] for j in idx]}
    for k, quelle in (("mitte", "mitte"), ("spread_bp", "spread_bp"), ("obi5", "obi5"), ("ofi_z", "ofi_z"), ("micro_bp", "micro_bp"), ("cvd", "cvd"),
                      ("vpin", "vpin"), ("tiefe_b", "tiefe_b"), ("tiefe_a", "tiefe_a"), ("intensitaet", "intens"), ("vol_bp", "vol_bp"), ("persistenz", "pers_anteil")):
        reihen[k] = [None if F[quelle][j] is None else _r(F[quelle][j], 4 if k in ("obi5", "vpin", "persistenz") else 2 if k != "mitte" else 6) for j in idx]
    delta = []
    vorher = 0
    for j in idx:
        delta.append(_r(F["cvd"][j] - F["cvd"][vorher], 4))
        vorher = j
    reihen["delta"] = delta
    reihen["kauf"] = [_r(sum(x["kauf"] for x in r[max(0, j - schritt + 1):j + 1]), 4) for j in idx]
    reihen["verkauf"] = [_r(sum(x["verkauf"] for x in r[max(0, j - schritt + 1):j + 1]), 4) for j in idx]
    stat = statistik(w["id"])
    stat_wert = stat["werte"][0]["statistik"] if stat["werte"] else []
    aus = {"wert": w["id"], "symbol": w["symbol"], "name": w["name"], "markt": markt, "live": markt == "KRYPTO", "verzoegert_min": 0 if markt == "KRYPTO" else 15,
            "stand": jetzt, "tag": tag, "von": r[0]["t"], "bis": jetzt, "quelle": QUELLE[markt], "sekunden": len(r),
            "kosten": {"spread_bp": _r(F["spread_bp"][i], 3), "gebuehr_bp": GEBUEHR[markt],
                       "hinweis": "Binance Spot: 10 bp Taker-Gebühr, die Kante dieser Signale liegt bei wenigen bp – Timing und Papierhandel, kein Scalping." if markt == "KRYPTO"
                       else "Xetra 15 Minuten verzögert: Rückschau und Forschung, kein Handelssignal."},
            "regime": _regime(F, kal, i), "kalibrierung": {k: _r(v, 4) for k, v in kal.items()}, "ort": ort, "lage": lage_,
            "signale_aktiv": aktiv, "signale": zeilen, "statistik": stat_wert, "statistik_gesamt": stat["gesamt"], "reihen": reihen,
            "waende": [{k: x.get(k) for k in ("seite", "preis", "von", "bis", "ende_t", "ende", "menge", "dauer_s", "nah")} for x in waende[-40:]],
            "eisberge": (extras or {}).get("eisberge", [])[-20:],
            "liquidationen": [{"t": x["t"], "seite": x["seite"], "preis": x["preis"], "menge": x["menge"]} for x in liq[-200:]],
            "perp": {k: v for k, v in perp.items() if not k.startswith("_")} if perp else None,
            "erklaerung": ERKLAERUNG, "rechenzeit_s": round(time.time() - t0, 2), "laeuft": dict(LAEUFT)}
    aus["absatz"] = absatz(aus)
    return aus


def _leer(w: dict, schritt: int, hinweis: str, tag: str | None = None) -> dict:
    stat = statistik(w["id"])
    return {"wert": w["id"], "symbol": w["symbol"], "name": w["name"], "markt": w["markt"], "live": w["markt"] == "KRYPTO", "verzoegert_min": 0 if w["markt"] == "KRYPTO" else 15,
            "stand": int(time.time()), "tag": tag, "quelle": QUELLE.get(w["markt"], ""), "hinweis": hinweis, "kosten": {"spread_bp": None, "gebuehr_bp": GEBUEHR.get(w["markt"], 0), "hinweis": ""},
            "regime": None, "ort": None, "lage": {"score": 0, "richtung": 0, "stufe": "neutral", "text": hinweis, "bausteine": []},
            "signale_aktiv": [], "signale": [], "statistik": stat["werte"][0]["statistik"] if stat["werte"] else [], "statistik_gesamt": stat["gesamt"],
            "reihen": {"schritt": schritt, "t": []}, "liquidationen": [], "perp": None, "erklaerung": ERKLAERUNG}


def _krypto_kontext(wert: str, von: int, bis: int) -> tuple[list[dict], dict | None]:
    db = lesen._db()
    if db is None:
        return [], None
    liq, perp = [], None
    try:
        try:
            liq = [{"t": int(t), "seite": s, "preis": float(p), "menge": float(m)} for t, s, p, m in
                   db.execute("SELECT t, seite, preis, menge FROM liquidationen WHERE id = ? AND t >= ? AND t <= ? ORDER BY t", (wert, von * 1000, bis * 1000 + 999))]
        except sqlite3.Error:
            liq = []
        try:
            z = db.execute("SELECT t, mark, index_, funding, oi FROM perp_1m WHERE id = ? AND t >= ? AND t <= ? ORDER BY t", (wert, von - 3600, bis)).fetchall()
            if z:
                oi = {int(t) // 60: float(o) for t, _, _, _, o in z if o is not None}
                letzte = z[-1]
                perp = {"funding": letzte[3], "mark": letzte[1], "index": letzte[2], "oi": letzte[4], "naechste_funding_t": (bis // 28800 + 1) * 28800,
                        "oi_reihe": {"t": [int(t) for t, *_ in z[::5]], "oi": [o for *_, o in z[::5]]}, "_oi": oi}
        except sqlite3.Error:
            perp = None
    finally:
        db.close()
    return liq, perp


# ======================================================================================
# Hintergrund: Krypto alle 10 s, Journal und Markouts
# ======================================================================================

def _schleife() -> None:
    time.sleep(20)
    while True:
        t0 = time.time()
        try:
            jetzt = int(time.time())
            for w in lesen.watchlist():
                if w["markt"] != "KRYPTO":
                    continue
                lage(w["id"], jetzt - FENSTER_THREAD, jetzt, 60, nur_journal=True)
                markouts_nachtragen_alt(w["id"], jetzt)
            _de_warm_halten(jetzt)
            LAEUFT["stand"], LAEUFT["fehler"] = datetime.now(timezone.utc).isoformat(timespec="seconds"), None
        except Exception as e:
            LAEUFT["fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
        LAEUFT["dauer_s"] = round(time.time() - t0, 2)
        time.sleep(max(2.0, 10 - (time.time() - t0)))


def datenstrom(wert: str, ab_ms: int = 0, n: int = 3000) -> dict:
    """Xetra für den Datenstrom im Reiter Orderbuch: einzelne Abschlüsse nach ab_ms ([t_ms, preis, menge, kauf], Seite nach
    Lee-Ready) und Buch-Updates je Sekunde ([t_s, anzahl]), aus dem zwischengespeicherten Lauf (15 Min. verzögert)."""
    w = _wert(wert)
    if w["markt"] != "DE":
        return {"wert": w["id"], "abschluesse": [], "updates": [], "hinweis": "Nur für Xetra-Werte; Krypto kommt vom Kern."}
    tag = next((t for t in ROH.tage() if w["id"] in ROH.werte(t)), None)
    if not tag:
        return {"wert": w["id"], "abschluesse": [], "updates": [], "hinweis": "Keine Xetra-Rohdaten für diesen Wert."}
    ZULETZT_DE[w["id"]] = time.time()
    _, extras = _lauf_de(w["id"], tag)
    alle = extras.get("abschluesse", [])
    if not ab_ms and alle:                                  # erster Abruf: die letzten 3 Minuten als Vorrat für die Wiedergabe
        ab_ms = alle[-1][0] - 180_000
    ab = [list(a) for a in alle if a[0] > ab_ms][:n]
    von_s, bis_s = ab_ms // 1000, (ab[-1][0] // 1000 if len(ab) == n else None)
    upd = sorted([t, k] for t, k in extras.get("updates_s", {}).items() if t > von_s and (bis_s is None or t <= bis_s))
    stand = max([a[0] for a in ab[-1:]] + [t * 1000 for t, _ in upd[-1:]] + [0])
    return {"wert": w["id"], "tag": tag, "abschluesse": ab, "updates": upd, "stand_ms": stand or None, "tick": extras.get("tick")}


def _de_warm_halten(jetzt: int) -> None:
    """Xetra-Werte, die das Dashboard in den letzten WARM_MIN Sekunden angefragt hat, im Hintergrund nachrechnen,
    damit Heatmap und Signale aus dem Cache kommen (ein Durchlauf dauert 5–15 s). _lauf_de rechnet höchstens alle
    180 s neu und nur, wenn neue Rohdateien da sind; außerhalb der Xetra-Zeit kostet das nichts."""
    for isin, t in list(ZULETZT_DE.items()):
        if jetzt - t > WARM_MIN:
            ZULETZT_DE.pop(isin, None)
            continue
        tag = next((d for d in ROH.tage() if isin in ROH.werte(d)), None)
        if tag:
            _lauf_de(isin, tag)


def starten() -> None:
    threading.Thread(target=_schleife, daemon=True, name="signale").start()


def neue_seit(wert: str, seit_t: int, art: str | None = None, score_min: float = 0.0) -> list[dict]:
    """Für Alarme: Journalzeilen eines Werts nach seit_t."""
    with _lock:
        db = _db_signale()
        try:
            frage = ("SELECT t, art, richtung, score, preis, mitte, spread_bp, begruendung, invalidierung, horizont_s, m5, m30, m60, m300, netto30, netto60, netto300 "
                     "FROM signale WHERE wert = ? AND t > ? AND score >= ?" + (" AND art = ?" if art and art != "alle" else "") + " AND art != 'oi_matrix' ORDER BY t")
            zeilen = db.execute(frage, (wert, seit_t, score_min, art) if art and art != "alle" else (wert, seit_t, score_min)).fetchall()
        finally:
            db.close()
    return [_zeile(z) for z in zeilen]


def absatz(d: dict) -> str:
    """Ein Absatz Signal-Lage in Worten, für Morgenlage und Telegram."""
    if d.get("hinweis") and not d.get("lage", {}).get("bausteine"):
        return f"{d.get('name', d.get('wert'))}: {d['hinweis']}"
    l, o, rg = d.get("lage") or {}, d.get("ort") or {}, d.get("regime") or {}
    teile = [f"{d.get('name')}: Regime {rg.get('name', '?')} ({rg.get('text', '')})", l.get("text", "")]
    if o.get("wand_kauf"):
        teile.append(f"Kaufwand {_preis(o['wand_kauf']['preis'])} ({_z(o['abstand_wand_kauf_bp'], 1).lstrip('+')} bp unter dem Kurs)")
    if o.get("wand_verkauf"):
        teile.append(f"Verkaufswand {_preis(o['wand_verkauf']['preis'])} ({_z(o['abstand_wand_verkauf_bp'], 1).lstrip('+')} bp über dem Kurs)")
    st = [s for s in d.get("statistik", []) if s.get("n", 0) >= 5]
    if st:
        best = max(st, key=lambda s: s.get("netto60_bp") or -99)
        teile.append(f"Beste Signalart der letzten Tage: {best['name']} ({best['n']}×, {_z(best['treffer60'] or 0, 0).lstrip('+')} % Treffer nach 60 s, netto {_z(best['netto60_bp'] or 0, 1)} bp)")
    return " ".join(x.rstrip(".") + "." for x in teile if x)
