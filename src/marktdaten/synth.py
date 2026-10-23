"""Synthetische Marktdaten im Originalformat der Quellen.

Für Tests, Vorführungen und Videos: Aufbau und Felder entsprechen den echten Dateien,
die Werte sind erfunden. Alle ISINs beginnen mit "ZZ" (kein gültiges Länderkürzel),
damit synthetische Daten nie mit echten verwechselt werden.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

WERTE = [("ZZ0000000011", "Muster Software", 200.0), ("ZZ0000000029", "Beispiel Chemie", 45.0),
         ("ZZ0000000037", "Demo Auto", 60.0), ("ZZ0000000045", "Test Bank", 28.0),
         ("ZZ0000000052", "Fiktiv Halbleiter", 35.0)]


@dataclass
class _Wert:
    isin: str
    name: str
    mitte: float
    tick: float


def _ticks(preis: float) -> float:
    return 0.05 if preis >= 100 else 0.01 if preis >= 10 else 0.001


class Markt:
    """Zufallspfad mit Spread, 5 Buchstufen und Abschlüssen. Gleicher seed = gleiche Daten."""

    def __init__(self, seed: int = 1, werte=WERTE):
        self.r = random.Random(seed)
        self.werte = [_Wert(i, n, p, _ticks(p)) for i, n, p in werte]

    def _schritt(self, w: _Wert):
        w.mitte = max(w.tick * 10, w.mitte * (1 + self.r.gauss(0, 0.0004)))
        halber = w.tick * self.r.choice([1, 1, 1, 2, 3])
        bid = round(round((w.mitte - halber) / w.tick) * w.tick, 4)
        ask = round(bid + 2 * halber, 4)
        def stufen(start, richtung):
            out, p = [], start
            for _ in range(5):
                out.append((round(p, 4), float(self.r.randint(1, 40) * 10), self.r.randint(1, 6)))
                p += richtung * w.tick * self.r.choice([1, 1, 2])
            return out
        return bid, ask, stufen(bid, -1), stufen(ask, 1)

    def ablauf(self, start: datetime, minuten: int, je_minute: int = 200):
        """Liefert (zeit, wert, bid, ask, kaufstufen, verkaufstufen, trade|None)."""
        t, ende = start, start + timedelta(minutes=minuten)
        abstand = timedelta(seconds=60 / je_minute)
        while t < ende:
            w = self.r.choice(self.werte)
            bid, ask, kauf, verkauf = self._schritt(w)
            trade = None
            if self.r.random() < 0.15:
                trade = (ask if self.r.random() < 0.5 else bid, float(self.r.randint(1, 50) * 5))
            yield t, w, bid, ask, kauf, verkauf, trade
            t += abstand * self.r.uniform(0.2, 1.8)


def _iso_ns(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"     # Deutsche Börse: Nanosekunden


def xetra(start: datetime, minuten: int = 1, seed: int = 1) -> tuple[list[str], list[str]]:
    """(pretrade-Zeilen, posttrade-Zeilen) im JSON-Format der Deutschen Börse."""
    pre, post = [], []
    for n, (t, w, bid, ask, kauf, verkauf, trade) in enumerate(Markt(seed).ablauf(start, minuten)):
        ts = _iso_ns(t)
        basis = {"messageId": "pretrade", "instrumentIdentificationCode": w.isin, "priceCurrency": "EUR",
                 "updateDateAndTime": ts}
        pre.append(json.dumps({**basis, "bestBid": bid, "bestBidQty": kauf[0][1]}))
        pre.append(json.dumps({**basis,
                               "aggregatedNumberOfOrdersAndQuotesBid": [{"noOfOrders": o} for *_, o in kauf],
                               "aggregatedNumberOfOrdersAndQuotesAsk": [{"noOfOrders": o} for *_, o in verkauf],
                               "mdBidMktDepthGroup1": [{"price": p, "quantity": m} for p, m, _ in kauf],
                               "mdAskMktDepthGroup1": [{"price": p, "quantity": m} for p, m, _ in verkauf]}))
        if trade:
            post.append(json.dumps({"messageId": "posttrade", "instrumentIdentificationCode": w.isin,
                                    "priceCurrency": "EUR", "tradingSystem": "1", "mmtTradingMode": "2",
                                    "mmtAlgoInd": "H" if n % 3 else "-", "price": trade[0], "quantity": trade[1],
                                    "tradingDateAndTime": ts, "publicationDateAndTime": ts,
                                    "transactionIdentificationCode": f"SYN{n:010d}",
                                    "venueOfExecution": "XETR", "venueOfPublication": "XETR"}))
    return pre, post


def cboe(start: datetime, minuten: int = 1, seed: int = 2, segment: str = "CEUX") -> tuple[list[str], list[str]]:
    """(pretrade-Zeilen, trade-Zeilen) im CSV-Format von Cboe Europe."""
    pre, post = [], []
    zeit = lambda t: t.strftime("%Y-%m-%d %H:%M:%S.%f")
    sichtbar: dict[tuple[str, str], set[float]] = {}
    for n, (t, w, bid, ask, kauf, verkauf, trade) in enumerate(Markt(seed).ablauf(start, minuten)):
        sym = w.name.split()[0][:4].upper() + "d"
        for seite, stufen in (("BUYL", kauf), ("SELL", verkauf)):
            stufen = stufen[: 1 if segment == "CEUX" else 5]
            neu = {p for p, _, _ in stufen}
            for p in sichtbar.get((w.isin, seite), set()) - neu:      # Invalidierung wie im Original: Menge 0
                pre.append(f"{zeit(t)},{w.isin},{sym},{seite},{p:.4f},EUR,MONE,0,EUR,0,{segment},CLOB,COTR,{zeit(t)}")
            sichtbar[(w.isin, seite)] = neu
            for p, m, o in stufen:
                pre.append(f"{zeit(t)},{w.isin},{sym},{seite},{p:.4f},EUR,MONE,{m:.0f},EUR,{o},{segment},CLOB,COTR,{zeit(t)}")
        if trade:
            post.append(f'{zeit(t)},{w.isin},{trade[0]:.4f},EUR,MONE,{trade[1]:.0f},{segment},CLOB,{zeit(t)},{segment},SYN{n:07d},""')
    return pre, post


def jetzt_gerundet() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=16)
