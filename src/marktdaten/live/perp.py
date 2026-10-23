"""Krypto-Kontext aus Binance Futures (öffentlich, ohne Schlüssel) für die Krypto-Paare der Watchlist:
Liquidationen (forceOrder), Markpreis, Indexpreis und Funding-Rate (markPrice@1s) sowie Open Interest je Minute (REST).

Die Spot-Paare (BTCUSDT, ETHUSDT, SOLUSDT) heißen auf dem Perpetual-Markt gleich. Tabellen: liquidationen (jede
Zwangsliquidation mit Seite long/short = was liquidiert wurde, Preis, Menge; t in Millisekunden) und perp_1m
(Mittel von Mark- und Indexpreis der Minute, letzte Funding-Rate, letzter Open Interest). Stream-Pfad wie bei den
Rohstoffen: /market/stream. Fehler werden nur gemeldet (zustand.melden "binance", Felder perp und perp_fehler).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone

from ..netz import holen, zaehlen
from .zustand import Wert, Zustand

UTC = timezone.utc
STROM = "wss://fstream.binance.com/market/stream?streams="
OI_URL = "https://fapi.binance.com/fapi/v1/openInterest?symbol="
NETZ = "Binance Futures"
DIENST = "binance"
MARKT = "KRYPTO"
TAKT_OI = 60
TAKT_SCHREIBEN = 5


def liquidation_aus(daten: dict) -> tuple | None:
    """forceOrder-Nachricht -> (paar, t_ms, seite, preis, menge). S = SELL heißt: eine Long-Position wurde liquidiert."""
    o = daten.get("o") or {}
    try:
        paar, t = str(o["s"]).upper(), int(o["T"])
        seite = "long" if str(o.get("S", "")).upper() == "SELL" else "short"
        return paar, t, seite, float(o["p"]), float(o["q"])
    except (KeyError, TypeError, ValueError):
        return None


def markpreis_aus(daten: dict) -> tuple | None:
    """markPriceUpdate -> (paar, t_s, mark, index, funding)."""
    try:
        return (str(daten["s"]).upper(), int(daten["E"]) // 1000, float(daten["p"]),
                float(daten["i"]) if daten.get("i") not in (None, "") else None,
                float(daten["r"]) if daten.get("r") not in (None, "") else None)
    except (KeyError, TypeError, ValueError):
        return None


class Minuten:
    """Verdichtung je Paar und Minute: Mittel von Mark und Index, letzte Funding-Rate, letzter Open Interest."""

    def __init__(self):
        self.lauf: dict[str, dict] = {}
        self.oi_letzt: dict[str, float] = {}
        self.fertig: list[tuple] = []

    def _minute(self, paar: str, t_s: int) -> dict:
        minute = t_s // 60 * 60
        m = self.lauf.get(paar)
        if m and minute > m["minute"]:
            self._abschluss(paar)
            m = None
        if m is None or minute > m["minute"]:
            m = self.lauf[paar] = {"minute": minute, "mark": 0.0, "index": 0.0, "n": 0, "n_index": 0,
                                   "funding": None, "oi": self.oi_letzt.get(paar)}
        return m

    def markpreis(self, paar: str, t_s: int, mark: float, index: float | None, funding: float | None) -> None:
        m = self._minute(paar, t_s)
        if t_s // 60 * 60 < m["minute"]:
            return                                   # verspätet: nichts mehr ändern
        m["mark"] += mark
        m["n"] += 1
        if index is not None:
            m["index"] += index
            m["n_index"] += 1
        if funding is not None:
            m["funding"] = funding

    def oi(self, paar: str, t_s: int, wert: float) -> None:
        self.oi_letzt[paar] = wert
        self._minute(paar, t_s)["oi"] = wert

    def _abschluss(self, paar: str) -> None:
        m = self.lauf.pop(paar, None)
        if not m or not (m["n"] or m["oi"] is not None):
            return
        self.fertig.append((paar, m["minute"], round(m["mark"] / m["n"], 8) if m["n"] else None,
                            round(m["index"] / m["n_index"], 8) if m["n_index"] else None, m["funding"], m["oi"]))

    def abschliessen(self, jetzt_s: int) -> list[tuple]:
        """Minuten, die vor jetzt_s enden, fertigstellen und alle fertigen Zeilen zurückgeben."""
        for paar in list(self.lauf):
            if self.lauf[paar]["minute"] + 60 <= jetzt_s:
                self._abschluss(paar)
        aus, self.fertig = self.fertig, []
        return aus


class PerpKontext(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="perp-kontext")
        self.z = zustand
        self.neu_verbinden = threading.Event()
        self.minuten = Minuten()
        self.liqu: list[tuple] = []
        self.ohne_oi: set[str] = set()               # Paare, die es auf dem Futures-Markt nicht gibt
        zustand.beobachter.append(self._watchlist)

    def paare(self) -> list[str]:
        with self.z.lock:
            return [w.id for w in self.z.werte.values() if w.markt == MARKT]

    def _watchlist(self, w: Wert, dazu: bool) -> None:
        if w.markt == MARKT:
            self.neu_verbinden.set()

    def _melden(self, **werte) -> None:
        self.z.melden(DIENST, **werte)

    # --- Netz ----------------------------------------------------------------------------
    def _oi(self, paar: str) -> float | None:
        d = json.loads(holen(OI_URL + paar, versuche=1, timeout=20))
        return float(d["openInterest"])

    async def _oi_takt(self, paare: list[str]) -> None:
        while True:
            for paar in paare:
                if paar in self.ohne_oi:
                    continue
                try:
                    wert = await asyncio.to_thread(self._oi, paar)
                    self.minuten.oi(paar, int(time.time()), wert)
                except Exception as e:               # z. B. 400: kein Perpetual zu diesem Paar
                    if "400" in str(e):
                        self.ohne_oi.add(paar)
                    self._melden(perp_fehler=f"OI {paar}: {type(e).__name__}: {str(e)[:80]}")
            await asyncio.sleep(TAKT_OI)

    async def _schreib_takt(self) -> None:
        while True:
            await asyncio.sleep(TAKT_SCHREIBEN)
            self.schreiben(int(time.time()))

    def schreiben(self, jetzt_s: int) -> None:
        zeilen, self.liqu = self.liqu, []
        if zeilen:
            self.z.speicher.liquidationen(zeilen)
        fertig = self.minuten.abschliessen(jetzt_s)
        if fertig:
            self.z.speicher.perp(fertig)

    async def strom(self, paare: list[str]) -> None:
        import websockets
        namen = "/".join(f"{p.lower()}@forceOrder/{p.lower()}@markPrice@1s" for p in paare)

        async def lesen():
            letzte_meldung = 0.0
            async with websockets.connect(STROM + namen, max_size=None, ping_interval=60) as ws:
                async for roh in ws:
                    zaehlen(NETZ, len(roh))
                    d = json.loads(roh)
                    strom, daten = d.get("stream", ""), d.get("data", {})
                    if "@forceOrder" in strom:
                        l = liquidation_aus(daten)
                        if l:
                            self.liqu.append(l)
                    elif "@markPrice" in strom:
                        m = markpreis_aus(daten)
                        if m:
                            self.minuten.markpreis(*m)
                    if time.time() - letzte_meldung > 10:
                        self._melden(perp=datetime.now(UTC).isoformat(timespec="seconds"), perp_fehler=None)
                        letzte_meldung = time.time()

        aufgaben = [asyncio.ensure_future(lesen()), asyncio.ensure_future(self._oi_takt(paare)),
                    asyncio.ensure_future(self._schreib_takt())]
        try:
            while not aufgaben[0].done():
                await asyncio.sleep(1)
                if self.neu_verbinden.is_set():
                    break
        finally:
            for a in aufgaben:
                a.cancel()
            self.schreiben(int(time.time()))
        if aufgaben[0].done() and not aufgaben[0].cancelled() and aufgaben[0].exception():
            raise aufgaben[0].exception()

    def run(self) -> None:
        time.sleep(10)                                # die übrigen Sammler zuerst
        while True:
            paare = self.paare()
            self.neu_verbinden.clear()
            if not paare:
                self.neu_verbinden.wait()
                continue
            try:
                asyncio.run(self.strom(paare))
            except Exception as e:
                self._melden(perp_fehler=f"{type(e).__name__}: {str(e)[:120]}")
                self.neu_verbinden.wait(30)
