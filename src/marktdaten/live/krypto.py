"""Krypto, live: Binance-WebSocket (öffentlich, ohne Schlüssel) für die Krypto-Werte der Watchlist.

Je Paar: Minutenkerzen (kline_1m), die besten 20 Stufen des Orderbuchs alle 100 ms (depth20) und die aggressiven
Abschlüsse (aggTrade). Das Buch geht einmal je Sekunde in den Kursstand (Live-Buch, buch_1m, Papierhandel); Buch und
Abschlüsse werden außerdem je Sekunde zu einer Zeile buch_1s verdichtet (krypto_flow.py: OFI, Tiefe, Kauf-/Verkaufsvolumen).
Einzelne Abschlüsse werden nicht gespeichert, das wären Millionen am Tag.
Beim Hinzufügen holt der Dienst einmalig Tageskerzen (1.000 Tage) und die letzten 1.000 Minutenkerzen.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone

from ..modell import BuchSeite
from ..netz import holen, zaehlen
from .krypto_flow import Flow
from .zustand import Wert, Zustand

UTC = timezone.utc
REST = "https://api.binance.com/api/v3/"
STROM = "wss://stream.binance.com:9443/stream?streams="
TAKT_SCHREIBEN = 1                 # Sekunden zwischen zwei Schreibvorgängen von buch_1s (Live-Takt im Reiter Orderbuch)


class KryptoSammler(threading.Thread):
    # Unterklassen (z. B. RohstoffSammler) ändern nur diese Angaben
    MARKT = "KRYPTO"
    REST_URL = REST
    STROM_URL = STROM
    DIENST = "binance"                 # Name in zustand.melden
    HERKUNFT = "binance"               # Herkunft (q) der gespeicherten Kerzen
    NETZ = "Binance"                   # Name im Download-Zähler
    TIEFE = True                       # Orderbuch (depth20) und Abschlüsse (aggTrade) mitlesen

    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="krypto-sammler")
        self.z = zustand
        self.neu_verbinden = threading.Event()
        self.flow = Flow() if self.TIEFE else None
        if self.flow:
            zustand.flow = self.flow                    # letzte Abschlüsse für /api/sekunden (Datenstrom im Reiter Orderbuch)
        zustand.beobachter.append(self._watchlist)

    def paare(self) -> list[str]:
        with self.z.lock:
            return [w.id for w in self.z.werte.values() if w.markt == self.MARKT]

    def _watchlist(self, w: Wert, dazu: bool) -> None:
        if w.markt != self.MARKT:
            return
        if dazu:
            threading.Thread(target=self._neu, args=(w.id,), daemon=True, name=f"krypto-{w.id}").start()
        else:
            if self.flow:
                self.flow.vergessen([w.id])
            self.neu_verbinden.set()

    def _neu(self, paar: str) -> None:
        try:
            self.historie(paar)
            self.anlauf([paar])
        except Exception as e:
            self.z.melden(self.DIENST, fehler=f"{paar}: {type(e).__name__}: {str(e)[:100]}")
        self.neu_verbinden.set()

    def _kerzen(self, paar: str, intervall: str, anzahl: int) -> list:
        k = json.loads(holen(f"{self.REST_URL}klines?symbol={paar}&interval={intervall}&limit={anzahl}"))
        return [[x[0] // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in k]

    def historie(self, paar: str) -> None:
        self.z.speicher.kerzen("kerzen_1d", paar, self._kerzen(paar, "1d", 1000)[:-1], self.HERKUNFT)
        self.z.speicher.kerzen("kerzen_1m", paar, self._kerzen(paar, "1m", 1000), self.HERKUNFT)

    def anlauf(self, paare: list[str]) -> None:
        heute = int(datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        for paar in paare:
            tage = self._kerzen(paar, "1d", 2)
            minuten = self._kerzen(paar, "1m", 1000)
            with self.z.lock:
                w = self.z.werte.get(paar)
                if w is None:
                    continue
                if len(tage) == 2:
                    w.vortag = tage[0][4]                     # Schluss des Vortags (UTC)
                for t, o, h, l, c, v in minuten:
                    if t >= heute:
                        self.z.kerze_setzen(w, t * 10**9, o, h, l, c, v)
                if minuten:
                    w.letzter, w.letzter_ts, w.zeit = minuten[-1][4], minuten[-1][0] * 10**9, minuten[-1][0] * 10**9
                self.z.geaendert()
        self.z.melden(self.DIENST, anlauf=datetime.now(UTC).isoformat(timespec="seconds"), fehler=None)

    def flow_schreiben(self, jetzt_s: int) -> int:
        """Fertige Sekunden aus dem Orderflow in buch_1s schreiben; Rückgabe: Anzahl Zeilen."""
        if not self.flow:
            return 0
        zeilen = self.flow.abschliessen(jetzt_s)
        if zeilen:
            self.z.speicher.buch_1s(zeilen)
        return len(zeilen)

    async def strom(self, paare: list[str]) -> None:
        import websockets
        namen = "/".join(f"{p.lower()}@kline_1m" + (f"/{p.lower()}@depth20@100ms/{p.lower()}@aggTrade" if self.TIEFE else "")
                         for p in paare)
        async def lesen():
            letzte_meldung = 0.0
            letztes_schreiben = time.time()
            buch_angewandt: dict[str, float] = {}          # Paar -> wann das Buch zuletzt in den Kursstand ging
            async with websockets.connect(self.STROM_URL + namen, max_size=None, ping_interval=60) as ws:
                async for roh in ws:
                    zaehlen(self.NETZ, len(roh))
                    d = json.loads(roh)
                    strom, daten = d.get("stream", ""), d.get("data", {})
                    paar = strom.split("@")[0].upper()
                    jetzt = time.time()
                    if "@depth" in strom:
                        bids = [(float(p), float(m)) for p, m in daten.get("bids", [])]
                        asks = [(float(p), float(m)) for p, m in daten.get("asks", [])]
                        if self.flow:
                            self.flow.buch(paar, int(jetzt), bids, asks)
                        if jetzt - buch_angewandt.get(paar, 0.0) < 1.0:
                            continue                        # Kursstand höchstens einmal je Sekunde (wie bisher depth5@1000ms)
                        buch_angewandt[paar] = jetzt
                    elif "@aggTrade" in strom:
                        if self.flow:
                            try:
                                self.flow.trade(paar, int(jetzt), float(daten["p"]), float(daten["q"]), kauf=not daten.get("m"), t_ms=int(jetzt * 1000))
                            except (KeyError, TypeError, ValueError):
                                pass
                        if jetzt - letztes_schreiben >= TAKT_SCHREIBEN:
                            letztes_schreiben = jetzt
                            self.flow_schreiben(int(jetzt))
                        continue
                    with self.z.lock:
                        w = self.z.werte.get(paar)
                        if w is None:
                            continue
                        if "@kline" in strom:
                            k = daten["k"]
                            self.z.kerze_setzen(w, k["t"] * 10**6, float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"]))
                            w.letzter, w.letzter_ts = float(k["c"]), daten["E"] * 10**6
                            w.eroeffnung = w.eroeffnung or float(k["o"])
                        elif "@depth" in strom:
                            ts = int(jetzt * 1e9)
                            for seite, stufen in (("B", bids), ("A", asks)):
                                self.z.anwenden(BuchSeite("binance", "BINANCE", paar, ts, seite, [(p, m, None) for p, m in stufen]))
                        w.zeit = max(w.zeit or 0, w.letzter_ts)
                        self.z.geaendert()
                    if jetzt - letztes_schreiben >= TAKT_SCHREIBEN:
                        letztes_schreiben = jetzt
                        self.flow_schreiben(int(jetzt))
                    if jetzt - letzte_meldung > 10:
                        self.z.melden(self.DIENST, strom=datetime.now(UTC).isoformat(timespec="seconds"), fehler=None, hinweis=None)
                        letzte_meldung = jetzt
        aufgabe = asyncio.ensure_future(lesen())
        try:
            while not aufgabe.done():
                await asyncio.sleep(1)
                if self.neu_verbinden.is_set():
                    aufgabe.cancel()
        finally:
            self.flow_schreiben(int(time.time()))
        if not aufgabe.cancelled() and aufgabe.exception():
            raise aufgabe.exception()

    def run(self) -> None:
        erste = True
        while True:
            paare = self.paare()
            self.neu_verbinden.clear()
            if not paare:
                self.z.melden(self.DIENST, strom=None, hinweis=f"kein Wert im Markt {self.MARKT} auf der Watchlist")
                self.neu_verbinden.wait()
                continue
            try:
                if erste:
                    self.anlauf(paare)
                    erste = False
                asyncio.run(self.strom(paare))
            except Exception as e:
                self.z.melden(self.DIENST, fehler=f"{type(e).__name__}: {str(e)[:120]}")
                self.neu_verbinden.wait(30)
