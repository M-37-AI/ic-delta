"""Krypto-Orderflow je Sekunde: die besten 20 Stufen des Buchs (depth20@100ms) und die aggressiven Abschlüsse
(aggTrade) werden zu einer Zeile je Sekunde und Paar verdichtet (Tabelle buch_1s). Grundlage der Signal-Schicht
in den Analysen (OBI, OFI, Microprice, Absorption, Delta).

Je Sekunde: Zustand des Buchs am Ende der Sekunde (beste 5 Stufen einzeln, Stufen 6 bis 20 als Summe), Order Flow
Imbalance nach Cont, Kukanov und Stoikov über alle Buch-Updates der Sekunde (ofi = Stufe 1, ofi5 = Stufen 1 bis 5
summiert), Anzahl Updates, aggressives Kauf- und Verkaufsvolumen (aggTrade: m = true heißt Käufer war Maker, also
aggressiver Verkauf), Anzahl Trades und letzter Preis. Sekunden ohne Buch-Update wiederholen den letzten Zustand
mit ofi 0, solange das Buch nicht älter als FRISCH Sekunden ist. Zeitbasis ist die Empfangszeit, weil der
Partial-Book-Strom keinen Zeitstempel trägt.

Nur Rechenlogik, kein Netz: der Krypto-Sammler füttert buch() und trade() und holt mit abschliessen() die fertigen Zeilen.
"""
from __future__ import annotations

import threading
from collections import deque

SPALTEN = ("id", "t", "bid", "ask", "b1", "b2", "b3", "b4", "b5", "a1", "a2", "a3", "a4", "a5", "b_tief", "a_tief",
           "ofi", "ofi5", "updates", "kauf", "verkauf", "n", "letzter")
FRISCH = 30          # Sekunden: so lange gilt das letzte Buch als aktuell (Lücken werden damit aufgefüllt)
STUFEN_OFI = 5
BAND = 3000         # letzte Abschlüsse je Paar im Speicher, für den Datenstrom im Reiter Orderbuch (nicht gespeichert)


def ofi_schritt(alt: tuple[list, list], neu: tuple[list, list]) -> tuple[float, float]:
    """OFI eines Buch-Updates. alt/neu = (bids, asks) als [(preis, menge)], beste Stufe zuerst.
    Rückgabe (Stufe 1, Stufen 1 bis 5 summiert). Fehlt eine Stufe auf einer Seite, zählt sie nicht."""
    summe = 0.0
    e1 = 0.0
    for i in range(STUFEN_OFI):
        e = 0.0
        if i < len(alt[0]) and i < len(neu[0]):
            (pb_a, qb_a), (pb_n, qb_n) = alt[0][i], neu[0][i]
            if pb_n >= pb_a:
                e += qb_n
            if pb_n <= pb_a:
                e -= qb_a
        if i < len(alt[1]) and i < len(neu[1]):
            (pa_a, qa_a), (pa_n, qa_n) = alt[1][i], neu[1][i]
            if pa_n <= pa_a:
                e -= qa_n
            if pa_n >= pa_a:
                e += qa_a
        if i == 0:
            e1 = e
        summe += e
    return e1, summe


def _neu() -> dict:
    return {"sek": None, "buch": None, "buch_t": None, "ofi": 0.0, "ofi5": 0.0, "updates": 0,
            "kauf": 0.0, "verkauf": 0.0, "n": 0, "letzter": None, "fertig": []}


class Flow:
    def __init__(self):
        self.paare: dict[str, dict] = {}
        self.band: dict[str, deque] = {}
        self.band_lock = threading.Lock()                # trade() läuft im Sammler, abschluesse() im Webserver

    def _s(self, paar: str) -> dict:
        s = self.paare.get(paar)
        if s is None:
            s = self.paare[paar] = _neu()
        return s

    # --- Ereignisse ---------------------------------------------------------------------
    def buch(self, paar: str, t_s: int, bids: list, asks: list) -> None:
        """bids/asks: [(preis, menge)] beste Stufe zuerst (bis 20 Stufen)."""
        if not bids or not asks:
            return
        s = self._s(paar)
        self._sekunde(s, t_s)
        neu = (bids, asks)
        if s["buch"] is not None and s["buch_t"] is not None and t_s - s["buch_t"] <= FRISCH:
            e1, e5 = ofi_schritt(s["buch"], neu)
            s["ofi"] += e1
            s["ofi5"] += e5
        s["buch"], s["buch_t"] = neu, t_s
        s["updates"] += 1

    def trade(self, paar: str, t_s: int, preis: float, menge: float, kauf: bool, t_ms: int | None = None) -> None:
        with self.band_lock:
            self.band.setdefault(paar, deque(maxlen=BAND)).append((t_ms or t_s * 1000, preis, menge, kauf))
        s = self._s(paar)
        self._sekunde(s, t_s)
        s["kauf" if kauf else "verkauf"] += menge
        s["n"] += 1
        s["letzter"] = preis

    def abschluesse(self, paar: str, ab_ms: int = 0, n: int = 2000) -> list:
        """Abschlüsse nach ab_ms aus dem Speicher, älteste zuerst: [t_ms, preis, menge, kauf]."""
        with self.band_lock:
            band = list(self.band.get(paar, ()))
        return [list(a) for a in band if a[0] > ab_ms][-n:]

    # --- Sekunden ----------------------------------------------------------------------
    def _sekunde(self, s: dict, t_s: int) -> None:
        """Beginnt bei neuer Sekunde eine frische Zeile; die alte und etwaige Lücken werden fertiggestellt."""
        if s["sek"] is None:
            s["sek"] = t_s
            return
        if t_s <= s["sek"]:
            return                                   # gleiche Sekunde (oder verspätet): in die laufende buchen
        self._zeile(s)
        for luecke in range(s["sek"] + 1, t_s):      # Sekunden ohne Ereignis: letzter Zustand, OFI 0
            s["sek"] = luecke
            self._zeile(s)
        s["sek"] = t_s

    def _zeile(self, s: dict) -> None:
        b, t = s["buch"], s["sek"]
        if b is not None and s["buch_t"] is not None and t - s["buch_t"] <= FRISCH:
            bids, asks = b
            bq = [m for _, m in bids[:5]] + [0.0] * (5 - min(5, len(bids)))
            aq = [m for _, m in asks[:5]] + [0.0] * (5 - min(5, len(asks)))
            s["fertig"].append((t, bids[0][0], asks[0][0], *[round(x, 8) for x in bq], *[round(x, 8) for x in aq],
                                round(sum(m for _, m in bids[5:]), 8), round(sum(m for _, m in asks[5:]), 8),
                                round(s["ofi"], 8), round(s["ofi5"], 8), s["updates"],
                                round(s["kauf"], 8), round(s["verkauf"], 8), s["n"], s["letzter"]))
        s["ofi"] = s["ofi5"] = 0.0
        s["updates"] = s["n"] = 0
        s["kauf"] = s["verkauf"] = 0.0

    def abschliessen(self, jetzt_s: int) -> list[tuple]:
        """Alle Sekunden vor jetzt_s fertigstellen und die Zeilen (mit Paar vorn) zurückgeben."""
        aus = []
        for paar, s in self.paare.items():
            if s["sek"] is not None and s["sek"] < jetzt_s:
                self._sekunde(s, jetzt_s)
            aus.extend((paar, *z) for z in s["fertig"])
            s["fertig"] = []
        return aus

    def vergessen(self, paare: list[str]) -> None:
        with self.band_lock:
            for p in paare:
                self.band.pop(p, None)
        for p in paare:
            self.paare.pop(p, None)
