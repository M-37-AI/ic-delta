"""Demo-Sammler: erfundene Marktdaten statt Abrufen bei Börsen, für Videos und zum Ausprobieren ohne Konten.

Läuft nur im Demo-Betrieb (MARKTDATEN_DEMO=1, eigener Datenordner). Er ersetzt die drei echten Sammler und benutzt
denselben Weg wie sie: deutsche Werte als Zeilen im Originalformat der Deutschen Börse (Xetra pretrade/posttrade),
die durch den Umwandler ins Board laufen und als Rohdateien für die Orderbuch-Analysen abgelegt werden; US-Werte und
Krypto als Minutenkerzen wie von Alpaca und Binance. Alle Namen sind erfunden, deutsche ISINs beginnen mit "ZZ".
Fürs Video (MARKTDATEN_DEMO_NAMEN=echt): Aktien mit Namen, Kürzeln und Kennungen der echten Watchlist, die Kurse bleiben
erfunden und starten bei frei gewählten Größenordnungen, nie bei echten Kursen. Krypto läuft dann echt (Binance).

Beim ersten Start entstehen fünf Jahre Tageskerzen, drei Handelstage Minutenkerzen samt Orderbuch-Rohdateien (mit
Wänden und Auktionen) und das Buch je Minute; danach laufen Ticks im Sekundentakt. Gleicher Startwert, gleiche Daten.
Szenen für den Dreh (Ausbruch, Sprung, Wand im Buch) kommen per `python -m marktdaten demo szene …` in die Datei
<Datenordner>/demo/szenen.jsonl und wirken sofort.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import random
import sqlite3
import threading
import time
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..modell import BuchSeite, Trade
from ..quellen import XETRA
from .eu import ablegen
from .speicher import ORT
from .zustand import Zustand

UTC, BERLIN = timezone.utc, ZoneInfo("Europe/Berlin")
SZENEN = ORT / "demo" / "szenen.jsonl"
# id, Kürzel, Name, Markt, Startpreis vor fünf Jahren, Jahresschwankung, Startwert für den Zufall
WERTE = [
    ("ZZ0000000011", "MUSW", "Muster Software", "DE", 120.0, 0.28, 11),
    ("ZZ0000000029", "BSPC", "Beispiel Chemie", "DE", 52.0, 0.24, 29),
    ("ZZ0000000037", "DAUT", "Demo Auto", "DE", 70.0, 0.32, 37),
    ("ZZ0000000045", "TBNK", "Test Bank", "DE", 16.0, 0.30, 45),
    ("ZZ0000000052", "FHLB", "Fiktiv Halbleiter", "DE", 22.0, 0.45, 52),
    ("NOVX", "NOVX", "Nova Robotics", "US", 60.0, 0.45, 61),
    ("ORBT", "ORBT", "Orbit Cloud", "US", 80.0, 0.38, 62),
    ("KITE", "KITE", "Kite Energy", "US", 30.0, 0.50, 63),
    ("DMOUSDT", "DMOUSDT", "Demo Coin", "KRYPTO", 30000.0, 0.60, 71),
]
ECHT_DB = pathlib.Path(os.environ.get("MARKTDATEN_DEMO_ECHT_DB", "~/.local/share/marktdaten/marktdaten.db")).expanduser()
KRYPTO_ECHT: list[tuple] = []                              # Video-Demo: Krypto-Werte, die der echte Krypto-Sammler bedient


def _echte_namen() -> list[tuple]:
    """Watchlist des echten Betriebs (nur Kennung, Kürzel, Name, Markt; keine Kurse), Startpreis und Zufall je Wert erfunden."""
    try:
        db = sqlite3.connect(f"file:{ECHT_DB}?mode=ro", uri=True)
        try:
            zeilen = db.execute("SELECT id, symbol, name, markt FROM watchlist ORDER BY markt, name").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    aus = []
    for id_, sym, name, markt in zeilen:
        seed = zlib.crc32(id_.encode()) % 10_000
        r = random.Random(seed)
        aus.append((id_, sym, name, markt, float(round(r.uniform(20, 300))), round(r.uniform(0.22, 0.4), 2), seed))
    return aus


if os.environ.get("MARKTDATEN_DEMO_NAMEN") == "echt" and (_echt := _echte_namen()):
    WERTE = [w for w in _echt if w[3] not in ("KRYPTO", "ROHSTOFFE")]
    KRYPTO_ECHT = [w[:4] for w in _echt if w[3] in ("KRYPTO", "ROHSTOFFE")]   # Krypto und Rohstoffe bedienen die echten Binance-Sammler
SITZUNG = {"DE": ((9, 0), (17, 30)), "US": ((15, 30), (22, 0))}      # Berliner Zeit; Krypto rund um die Uhr
JAHR_S = 365.25 * 86400


def watchlist() -> list[tuple]:
    return [(i, s, n, m) for i, s, n, m, *_ in WERTE] + KRYPTO_ECHT


def _tick(p: float) -> float:
    return 0.05 if p >= 100 else 0.01 if p >= 10 else 0.001


def _iso(ts_ns: int) -> str:
    s, ns = divmod(ts_ns, 10**9)
    return datetime.fromtimestamp(s, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ns:09d}Z"


class Pfad:
    """Kursverlauf: geometrische Zufallsbewegung mit wechselnden Trendphasen und gelegentlichen Sprüngen."""

    def __init__(self, preis: float, vol: float, seed: int):
        self.preis, self.vol, self.r = preis, vol, random.Random(seed)
        self.drift, self.bis = 0.0, 0.0
        self.szene: dict | None = None

    def schritt(self, dt_s: float, t: float) -> float:
        if t >= self.bis:                                   # neue Trendphase alle paar Wochen bis Monate
            self.drift = self.r.gauss(0.08, 0.45)
            self.bis = t + self.r.uniform(20, 120) * 86400
        dt = dt_s / JAHR_S
        vol, drift = self.vol, self.drift
        sz = self.szene
        if sz and sz["start"] <= t < sz["start"] + sz["dauer"]:
            vol *= sz.get("vol", 1.0)
            drift += math.log(1 + sz.get("prozent", 0) / 100) / (sz["dauer"] / JAHR_S)   # Szene: Ziel über die Dauer verteilt
        elif sz and t >= sz["start"] + sz["dauer"]:
            self.szene = None
        z = self.r.gauss(0, 1)
        if self.r.random() < dt * 4:                         # seltene Sprünge (Nachrichten)
            z += self.r.choice([-1, 1]) * self.r.uniform(4, 8)
        self.preis *= math.exp((drift - vol * vol / 2) * dt + vol * math.sqrt(dt) * z)
        return self.preis


class Buch:
    """Fünf Stufen je Seite um den Kurs, mit Orderzahl; gelegentlich eine Wand (großes Volumen auf einer Stufe)."""

    def __init__(self, r: random.Random):
        self.r = r
        self.wand: dict | None = None                       # {"seite", "preis", "menge", "bis", "rueckzug"}

    def stufen(self, mitte: float, t: float) -> tuple[list, list]:
        tick = _tick(mitte)
        halber = tick * self.r.choice([1, 1, 1, 2])
        bid = round(math.floor((mitte - halber / 2) / tick) * tick, 4)
        ask = round(bid + 2 * halber, 4)
        basis = max(5, 30000 / mitte)                       # rund 30.000 € je Stufe
        neigung = self.r.gauss(0, 0.35)                     # Ungleichgewicht: leicht mit dem nächsten Schritt verknüpft
        kauf, verkauf = [], []
        for i in range(5):
            for seite, liste, start in (("B", kauf, bid - i * tick), ("A", verkauf, ask + i * tick)):
                f = math.exp(neigung if seite == "B" else -neigung)
                menge = round(basis * f * self.r.lognormvariate(0, 0.6))
                if self.wand and self.wand["seite"] == seite and abs(self.wand["preis"] - start) < tick / 2 and t < self.wand["bis"]:
                    menge += self.wand["menge"]
                liste.append((round(start, 4), float(max(1, menge)), self.r.randint(1, 8)))
        if self.wand and (t >= self.wand["bis"] or (self.wand.get("rueckzug") and abs(self.wand["preis"] - mitte) <= 2 * tick)):
            self.wand = None                                 # abgelaufen oder (Spoofing) zurückgezogen, als der Kurs näher kam
        if not self.wand and self.r.random() < 0.004:      # neue Wand: 6- bis 15-fache Menge, 1 bis 8 Minuten
            seite = self.r.choice("BA")
            stufe = self.r.randint(1, 4)
            preis = (bid - stufe * tick) if seite == "B" else (ask + stufe * tick)
            self.wand = {"seite": seite, "preis": round(preis, 4), "menge": basis * self.r.uniform(6, 15), "bis": t + self.r.uniform(60, 480)}
        return kauf, verkauf


class DemoSammler(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="demo-sammler")
        self.z = zustand
        self.s = zustand.speicher
        self.pfade: dict[str, Pfad] = {}
        self.buecher: dict[str, Buch] = {}
        self.minute: dict[str, list] = {}                  # laufende Minutenkerze für US und Krypto
        self.roh: dict[str, list] = {"pre": [], "post": []}
        self.roh_minute: int | None = None
        self.szenen_gelesen: set[str] = set()
        self.n = 0
        self.bekannt: set[str] = set()                    # Watchlist-Stand, zu dem Kurse und Historie angelegt sind

    # --- Hilfen ----------------------------------------------------------------------------------------
    def _sitzung(self, markt: str, tag: date) -> tuple[float, float] | None:
        if markt == "KRYPTO":
            a = datetime(tag.year, tag.month, tag.day, tzinfo=BERLIN)
            return a.timestamp(), (a + timedelta(days=1)).timestamp()
        if tag.weekday() >= 5:
            return None
        (h1, m1), (h2, m2) = SITZUNG[markt]
        return (datetime(tag.year, tag.month, tag.day, h1, m1, tzinfo=BERLIN).timestamp(),
                datetime(tag.year, tag.month, tag.day, h2, m2, tzinfo=BERLIN).timestamp())

    def _xetra(self, isin: str, ts_ns: int, kauf, verkauf, trade: tuple | None, modus: str = "2") -> list[str]:
        basis = {"messageId": "pretrade", "instrumentIdentificationCode": isin, "priceCurrency": "EUR", "updateDateAndTime": _iso(ts_ns)}
        zeilen = [json.dumps({**basis, "aggregatedNumberOfOrdersAndQuotesBid": [{"noOfOrders": o} for *_, o in kauf],
                              "aggregatedNumberOfOrdersAndQuotesAsk": [{"noOfOrders": o} for *_, o in verkauf],
                              "mdBidMktDepthGroup1": [{"price": p, "quantity": m} for p, m, _ in kauf],
                              "mdAskMktDepthGroup1": [{"price": p, "quantity": m} for p, m, _ in verkauf]})] if kauf else []
        if trade:
            self.n += 1
            zeilen.append(json.dumps({"messageId": "posttrade", "instrumentIdentificationCode": isin, "priceCurrency": "EUR", "tradingSystem": "1",
                                      "mmtTradingMode": modus, "price": trade[0], "quantity": trade[1], "tradingDateAndTime": _iso(ts_ns),
                                      "publicationDateAndTime": _iso(ts_ns), "transactionIdentificationCode": f"DEMO{self.n:010d}",
                                      "venueOfExecution": "XETA", "venueOfPublication": "XETR"}))
        return zeilen

    def _roh_schreiben(self, minute_s: int, pre: list[str], post: list[str]) -> None:
        stempel = datetime.fromtimestamp(minute_s, UTC)
        name = stempel.strftime("%Y-%m-%dT%H_%M")
        isins = {w[0].encode() for w in WERTE if w[3] == "DE"}
        if pre:
            ablegen(f"DETR-pretrade-{name}.json.gz", [z.encode() for z in pre], isins, stempel.date().isoformat())
        if post:
            ablegen(f"DETR-posttrade-{name}.json.gz", [z.encode() for z in post], isins, stempel.date().isoformat())

    # --- Erster Start: Historie --------------------------------------------------------------------------
    def historie(self) -> None:
        heute = datetime.now(BERLIN).date()
        tage, d = [], heute
        while len(tage) < 3:                                 # drei Handelstage mit Minuten, heute nur bis jetzt
            if d.weekday() < 5:
                tage.append(d)
            d -= timedelta(days=1)
        tage.reverse()
        jetzt = time.time()
        for id_, sym, name, markt, start, vol, seed in WERTE:
            if self.s.kerzen_lesen("kerzen_1d", id_):
                continue                                     # schon angelegt
            p = Pfad(start, vol, seed)
            r = random.Random(seed * 7)
            q = {"DE": "xetra", "US": "sip", "KRYPTO": "binance"}[markt]
            # Tageskerzen: fünf Jahre bis vor den ersten Minutentag
            erster = tage[0]
            tag = erster - timedelta(days=5 * 365)
            zeilen = []
            while tag < erster:
                if markt == "KRYPTO" or tag.weekday() < 5:
                    t0 = datetime(tag.year, tag.month, tag.day, tzinfo=UTC).timestamp()
                    o = p.preis
                    preise = [p.schritt(86400 / 8, t0 + i * 10800) for i in range(8)]
                    v = r.lognormvariate(math.log(4e6 / max(o, 1)), 0.4) * (1 + abs(preise[-1] / o - 1) * 25)
                    zeilen.append([int(t0), round(o, 4), round(max(o, *preise), 4), round(min(o, *preise), 4), round(preise[-1], 4), round(v)])
                tag += timedelta(days=1)
            self.s.kerzen("kerzen_1d", id_, zeilen, q)
            # Minutenkerzen der letzten Handelstage, deutsche Werte samt Orderbuch-Rohdateien und Buch je Minute
            minuten, abschluesse, buch_1m = [], [], []
            buch = Buch(random.Random(seed * 13))
            for tag in tage:
                if markt == "KRYPTO":
                    a, e = self._sitzung("KRYPTO", tag)
                else:
                    a, e = self._sitzung(markt, tag)
                e = min(e, jetzt - 60)
                t = a
                while t < e:
                    tick_n = 30 if markt == "DE" else 12
                    o = p.preis
                    preise = [p.schritt(60 / tick_n, t + i * 60 / tick_n) for i in range(tick_n)]
                    rand = 1 + 2.5 * (math.exp(-(t - a) / 1800) + math.exp(-(e - t) / 1800)) if markt != "KRYPTO" else 1   # U-Form: Eröffnung und Schluss lebhaft
                    if markt == "DE":
                        pre, post, upd, spread, ungl = [], [], 0, 0.0, 0.0
                        menge_min = 0.0
                        for i, pr in enumerate(preise):
                            ts = int((t + i * 2) * 1e9) + r.randint(0, 999_999_999)
                            kauf, verkauf = buch.stufen(pr, t)
                            trade = None
                            if r.random() < min(0.95, 0.35 * rand):
                                seite_px = verkauf[0][0] if r.random() < 0.5 else kauf[0][0]
                                trade = (seite_px, float(max(1, round(r.lognormvariate(math.log(4000 / pr), 0.9)))))
                                menge_min += trade[1]
                                abschluesse.append((id_, ts, "XETA", trade[0], trade[1]))
                            for zeile in self._xetra(id_, ts, kauf, verkauf, trade):
                                (post if '"posttrade"' in zeile else pre).append(zeile)
                            upd += 1
                            mitte = (kauf[0][0] + verkauf[0][0]) / 2
                            spread += (verkauf[0][0] - kauf[0][0]) / mitte * 1e4
                            tb, ta = sum(x[1] for x in kauf), sum(x[1] for x in verkauf)
                            ungl += (tb - ta) / (tb + ta)
                        if t == a:                                      # Eröffnungsauktion
                            ts = int((a + 0.4) * 1e9)
                            menge = round(40000 / o)
                            post.insert(0, self._xetra(id_, ts, [], [], (round(o, 2), float(menge)), "O")[0])
                            abschluesse.append((id_, ts, "XETA", round(o, 2), float(menge)))
                        self._roh_schreiben(int(t), pre, post)
                        buch_1m.append((id_, int(t), kauf[0][0], verkauf[0][0], round(spread / upd, 2), round(sum(x[1] for x in kauf) * mitte),
                                        round(sum(x[1] for x in verkauf) * mitte), round(ungl / upd, 3), upd))
                        v = menge_min
                    else:
                        v = r.lognormvariate(math.log(8000 / max(o, 1) * (1 if markt == "US" else 0.02)), 0.6) * rand
                    minuten.append([int(t), round(o, 4), round(max(o, *preise), 4), round(min(o, *preise), 4), round(preise[-1], 4), round(v, 4)])
                    t += 60
                if markt == "DE" and e >= self._sitzung("DE", tag)[1] - 60:   # Schlussauktion
                    ts = int((self._sitzung("DE", tag)[1] + 300) * 1e9)
                    menge = round(90000 / p.preis)
                    self._roh_schreiben(int(self._sitzung("DE", tag)[1] + 300) // 60 * 60, [], self._xetra(id_, ts, [], [], (round(p.preis, 2), float(menge)), "K"))
                    abschluesse.append((id_, ts, "XETA", round(p.preis, 2), float(menge)))
            self.s.kerzen("kerzen_1m", id_, minuten, q)
            if abschluesse:
                self.s.abschluesse(abschluesse)
            if buch_1m:
                self.s.buch(buch_1m)
            self.pfade[id_] = p
            self.buecher[id_] = buch

    def _vorbereiten(self) -> None:
        """Pfade auf den letzten gespeicherten Kurs setzen und den Tag im Board wiederherstellen."""
        heute0 = datetime.now(BERLIN).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        for id_, sym, name, markt, start, vol, seed in WERTE:
            letzte = self.s.kerzen_lesen("kerzen_1m", id_)[-1:] or self.s.kerzen_lesen("kerzen_1d", id_)[-1:]
            if id_ not in self.pfade:
                self.pfade[id_] = Pfad(letzte[0][4] if letzte else start, vol, seed + int(time.time()))
                self.buecher[id_] = Buch(random.Random(seed * 17 + int(time.time())))
            with self.z.lock:
                w = self.z.werte.get(id_)
                if w is None:
                    continue
                w.kerzen.clear()
                self.z._wiederherstellen(w)
                vorher = [k for k in self.s.kerzen_lesen("kerzen_1m", id_, int(heute0) - 6 * 86400, int(heute0))]
                tage = self.s.kerzen_lesen("kerzen_1d", id_)
                if vorher:
                    w.vortag = vorher[-1][4]
                elif tage:
                    w.vortag = tage[-1][4]
                self.z.geaendert()
        self.z.melden("demo", anlauf=datetime.now(UTC).isoformat(timespec="seconds"), fehler=None, hinweis="erfundene Daten")

    def _neue(self) -> None:
        """Nachträglich aufgenommene DE- und US-Werte: Startpreis und Zufall wie bei den echten Namen, dann Historie und Kurse."""
        with self.z.lock:
            jetzt = {w.id: (w.id, w.symbol, w.name, w.markt) for w in self.z.werte.values() if w.markt in ("DE", "US")}
        self.bekannt &= set(jetzt)                               # entfernte Werte vergessen, damit sie beim erneuten Aufnehmen wieder Historie bekommen
        neu = [jetzt[i] for i in jetzt if i not in self.bekannt]
        if not neu:
            return
        vorhanden = {w[0] for w in WERTE}
        for id_, sym, name, markt in neu:
            if id_ not in vorhanden:
                seed = zlib.crc32(id_.encode()) % 10_000
                r = random.Random(seed)
                WERTE.append((id_, sym, name, markt, float(round(r.uniform(20, 300))), round(r.uniform(0.22, 0.4), 2), seed))
        self.historie()                                         # legt nur an, was noch keine Tageskerzen hat
        self._vorbereiten()
        self.bekannt |= set(jetzt)

    # --- Szenen ------------------------------------------------------------------------------------------
    def _szenen(self) -> None:
        if not SZENEN.exists():
            return
        jetzt = time.time()
        for zeile in SZENEN.read_text().splitlines():
            try:
                s = json.loads(zeile)
            except ValueError:
                continue
            if s["id"] in self.szenen_gelesen:
                continue
            self.szenen_gelesen.add(s["id"])
            w = next((x for x in WERTE if x[1] == s["wert"] or x[0] == s["wert"]), None)
            if not w or jetzt > s["start"] + s["dauer"]:
                continue
            p = self.pfade.get(w[0])
            if s["art"] in ("ausbruch_auf", "ausbruch_ab", "sprung_auf", "sprung_ab"):
                p.szene = {"start": s["start"], "dauer": s["dauer"], "prozent": s["prozent"] * (1 if s["art"].endswith("auf") else -1),
                           "vol": 0.6 if s["art"].startswith("ausbruch") else 0.3}
            elif s["art"] in ("wand", "spoofing") and w[3] == "DE":
                b, mitte = self.buecher[w[0]], p.preis
                tick = _tick(mitte)
                seite = "B" if s.get("seite", "kauf") == "kauf" else "A"
                preis = round((mitte - 3 * tick) if seite == "B" else (mitte + 3 * tick), 4)
                b.wand = {"seite": seite, "preis": preis, "menge": 30000 / mitte * 20, "bis": s["start"] + s["dauer"], "rueckzug": s["art"] == "spoofing"}
                if s["art"] == "spoofing":                     # Kurs läuft auf die Wand zu, sie verschwindet kurz vorher
                    p.szene = {"start": s["start"], "dauer": min(s["dauer"], 90), "prozent": 0.25 * (-1 if seite == "B" else 1), "vol": 0.4}

    # --- Laufend ------------------------------------------------------------------------------------------
    def schritt(self) -> None:
        t = time.time()
        ts_ns = int(t * 1e9)
        minute = int(t // 60 * 60)
        if self.roh_minute is not None and minute != self.roh_minute:
            self._roh_schreiben(self.roh_minute, self.roh["pre"], self.roh["post"])
            self.roh = {"pre": [], "post": []}
        self.roh_minute = minute
        with self.z.lock:
            for id_, sym, name, markt, start, vol, seed in WERTE:
                w, p = self.z.werte.get(id_), self.pfade.get(id_)
                if w is None or p is None:
                    continue
                preis = p.schritt(1.0, t)
                if markt == "DE":
                    kauf, verkauf = self.buecher[id_].stufen(preis, t)
                    trade = None
                    if p.r.random() < (0.9 if p.szene else 0.55):
                        trade = (verkauf[0][0] if p.r.random() < 0.5 else kauf[0][0],
                                 float(max(1, round(p.r.lognormvariate(math.log(4000 / preis), 0.9) * (3 if p.szene else 1)))))
                    for zeile in self._xetra(id_, ts_ns, kauf, verkauf, trade):
                        (self.roh["post"] if '"posttrade"' in zeile else self.roh["pre"]).append(zeile)
                        for e in XETRA.umwandeln(json.loads(zeile)):
                            self.z.anwenden(e)
                elif markt == "US":                              # wie Alpaca: Abschlüsse über IEX, Kerze und Umsatz daraus
                    if p.r.random() < (0.95 if p.szene else 0.7):
                        menge = float(max(1, round(p.r.lognormvariate(math.log(3000 / max(preis, 1)), 0.9) * (3 if p.szene else 1))))
                        self.z.anwenden(Trade("alpaca", "IEXG", id_, ts_ns, round(preis, 2), menge))
                else:
                    k = self.minute.get(id_)
                    menge = p.r.lognormvariate(math.log(150 / max(preis, 1) * (1 if markt == "US" else 0.02) * 60), 0.7) * (3 if p.szene else 1) / 60
                    if not k or k[0] != minute:
                        k = self.minute[id_] = [minute, preis, preis, preis, preis, 0.0]
                    k[2], k[3], k[4], k[5] = max(k[2], preis), min(k[3], preis), preis, k[5] + menge
                    self.z.kerze_setzen(w, minute * 10**9, round(k[1], 4), round(k[2], 4), round(k[3], 4), round(k[4], 4), round(k[5], 4),
                                        "sip" if markt == "US" else None)
                    w.letzter, w.letzter_ts, w.zeit = round(preis, 4), ts_ns, ts_ns
                    w.eroeffnung = w.eroeffnung or round(preis, 4)
                    if markt == "KRYPTO" and int(t) % 2 == 0:
                        kauf, verkauf = self.buecher[id_].stufen(preis, t)
                        for seite, stufen in (("B", kauf), ("A", verkauf)):
                            self.z.anwenden(BuchSeite("binance", "BINANCE", id_, ts_ns, seite, stufen))
            self.z.geaendert()

    def run(self) -> None:
        try:
            self.z.melden("demo", hinweis="baut die erfundene Historie auf (einmalig, etwa eine Minute)")
            self.historie()
            self._vorbereiten()
            self._neue()
        except Exception as e:
            self.z.melden("demo", fehler=f"{type(e).__name__}: {str(e)[:150]}")
        while True:
            try:
                self._szenen()
                if int(time.time()) % 5 == 0:
                    self._neue()
                self.schritt()
            except Exception as e:
                self.z.melden("demo", fehler=f"{type(e).__name__}: {str(e)[:150]}")
            time.sleep(max(0.05, 1 - time.time() % 1))


# --- Szenen per Befehl -------------------------------------------------------------------------------------------
ARTEN = {"ausbruch_auf": "Kurs steigt über die Dauer um prozent (ruhig, mit mehr Volumen)",
         "ausbruch_ab": "Kurs fällt über die Dauer um prozent",
         "sprung_auf": "schneller Anstieg um prozent", "sprung_ab": "schneller Rückgang um prozent",
         "wand": "große Order im Buch nahe am Kurs (seite kauf oder verkauf), deutsche Werte",
         "spoofing": "wie wand, verschwindet aber, bevor der Kurs sie erreicht"}


def szene(art: str, wert: str, prozent: float = 2.0, minuten: float = 3.0, seite: str = "kauf", in_sekunden: float = 0) -> dict:
    if art not in ARTEN:
        raise ValueError("Art unbekannt: " + ", ".join(ARTEN))
    if not any(x[1] == wert.upper() or x[0] == wert for x in WERTE):
        raise ValueError("Wert unbekannt: " + ", ".join(x[1] for x in WERTE))
    s = {"id": uuid.uuid4().hex[:8], "art": art, "wert": wert.upper() if not wert.startswith("ZZ") else wert, "prozent": float(prozent),
         "start": time.time() + in_sekunden, "dauer": minuten * 60 if art not in ("sprung_auf", "sprung_ab") else max(20, minuten * 60 / 6), "seite": seite}
    SZENEN.parent.mkdir(parents=True, exist_ok=True)
    with open(SZENEN, "a") as f:
        f.write(json.dumps(s) + "\n")
    return s
