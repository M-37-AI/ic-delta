"""Krypto, tiefes Orderbuch: je Paar alle TAKT Sekunden die besten 1.000 Stufen je Seite (Binance REST, öffentlich),
zu Preisbändern zusammengefasst und je Minute gemittelt in der Tabelle buch_tiefe abgelegt. Grundlage der
Liquiditäts-Heatmap in iC Charts.

Warum REST statt WebSocket: depth5 aus krypto.py deckt bei Bitcoin nur wenige Cent ab. 1.000 Stufen reichen etwa
±0,3 % um den Kurs. Eine Antwort ist mit gzip rund 10 KB groß (alle 10 s also rund 90 MB am Tag je Paar) und kostet
50 von 6.000 Gewichtspunkten je Minute bei Binance.

Bänder: Breite = 1 Basispunkt des Kurses, auf 1-2-2,5-5 gerundet (BTC um 80.000 USDT: 10 USDT). Das äußerste,
nur teilweise erfasste Band jeder Seite wird weggelassen. menge = mittlere Stückzahl (Basiswährung) im Band über die
Abtastungen der Minute.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from ..netz import holen
from .zustand import Zustand

UTC = timezone.utc
REST = "https://api.binance.com/api/v3/depth"
TAKT = 10
STUFEN = 1000


def bandbreite(kurs: float) -> float:
    """1 Basispunkt des Kurses, auf 1, 2, 2,5 oder 5 mal Zehnerpotenz gerundet."""
    roh = kurs * 1e-4
    e = 10 ** math.floor(math.log10(roh))
    for f in (1, 2, 2.5, 5, 10):
        if roh <= f * e * 1.25:
            return round(f * e, 10)
    return round(10 * e, 10)


def baender(stufen: list[tuple[float, float]], band: float, seite: str) -> dict[float, float]:
    """Stufen [(preis, menge)] (beste zuerst) -> {Bandbeginn: Menge}; das letzte, angeschnittene Band fällt weg."""
    aus: dict[float, float] = defaultdict(float)
    for p, m in stufen:
        aus[round(math.floor(p / band + 1e-9) * band, 10)] += m
    if aus:
        aeusserstes = min(aus) if seite == "B" else max(aus)
        del aus[aeusserstes]
    return dict(aus)


class KryptoTiefe(threading.Thread):
    def __init__(self, zustand: Zustand, takt: float = TAKT, abruf=None):
        super().__init__(daemon=True, name="krypto-tiefe")
        self.z, self.takt = zustand, takt
        self.abruf = abruf or self._abruf
        self.minute: dict[str, int] = {}
        self.summen: dict[str, dict] = {}              # Paar -> {"n", "band", "B": {preis: summe}, "A": {...}}
        self.lock = threading.Lock()                   # summen wird abgetastet und zugleich von jetzt() gelesen
        zustand.tiefe = self                           # für die laufende Minute im Reiter Orderbuch (/api/tiefe/jetzt)

    def jetzt(self, paar: str) -> dict | None:
        """Laufende, noch nicht gespeicherte Minute im Format einer Heatmap-Spalte (Preis = Bandmitte, Mittel der Abtastungen)."""
        with self.lock:
            s, t = self.summen.get(paar), self.minute.get(paar)
            if not s or not s["n"] or t is None:
                return None
            n, band = s["n"], s["band"]
            b = sorted(((round(p + band / 2, 8), round(m / n, 6)) for p, m in s["B"].items()), reverse=True)
            a = sorted((round(p + band / 2, 8), round(m / n, 6)) for p, m in s["A"].items())
        return {"t": t, "b": b, "a": a, "n": n, "band": band, "laufend": True}

    @staticmethod
    def _abruf(paar: str) -> dict:
        return json.loads(holen(f"{REST}?symbol={paar}&limit={STUFEN}", {"Accept-Encoding": "gzip"}, versuche=1, timeout=20))

    def paare(self) -> list[str]:
        with self.z.lock:
            return [w.id for w in self.z.werte.values() if w.markt == "KRYPTO"]

    def abtasten(self, paar: str, jetzt: float) -> None:
        d = self.abruf(paar)
        b = [(float(p), float(m)) for p, m in d.get("bids", [])]
        a = [(float(p), float(m)) for p, m in d.get("asks", [])]
        if not b or not a:
            return
        minute = int(jetzt) // 60 * 60
        if self.minute.get(paar) not in (None, minute):
            self.schreiben(paar)
        with self.lock:
            self.minute[paar] = minute
            s = self.summen.get(paar)
            if s is None:
                s = self.summen[paar] = {"n": 0, "band": bandbreite((b[0][0] + a[0][0]) / 2), "B": defaultdict(float), "A": defaultdict(float)}
            s["n"] += 1
            for seite, stufen in (("B", b), ("A", a)):
                for p, m in baender(stufen, s["band"], seite).items():
                    s[seite][p] += m

    def schreiben(self, paar: str) -> None:
        with self.lock:
            s, t = self.summen.pop(paar, None), self.minute.get(paar)
        if not s or not s["n"] or t is None:
            return
        zeilen = [(paar, t, seite, p, round(m / s["n"], 8), s["band"]) for seite in ("B", "A") for p, m in s[seite].items()]
        self.z.speicher.tiefe(zeilen)

    def run(self) -> None:
        time.sleep(15)                                   # Start der übrigen Sammler abwarten
        while True:
            beginn = time.time()
            for paar in self.paare():
                try:
                    self.abtasten(paar, time.time())
                    self.z.melden("binance", tiefe=datetime.now(UTC).isoformat(timespec="seconds"), tiefe_fehler=None)
                except Exception as e:                   # Anzeige statt Absturz; nächster Takt versucht es wieder
                    self.z.melden("binance", tiefe_fehler=f"{paar}: {type(e).__name__}: {str(e)[:100]}")
            for paar in [p for p in self.summen if p not in self.paare()]:
                self.summen.pop(paar, None)              # von der Watchlist entfernt
            time.sleep(max(1.0, self.takt - (time.time() - beginn)))
