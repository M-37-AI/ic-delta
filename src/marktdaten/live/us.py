"""USA, live: Alpaca-WebSocket (IEX) für Trades, Quotes und Minutenkerzen der Watchlist.

Beim Start und für jeden neu hinzugefügten Wert holt der Dienst per REST den Vortagesschluss
(SIP, alle Börsen), die Minutenkerzen des Tages (IEX) und einmalig die Historie: Tageskerzen
über fünf Jahre und Minutenkerzen der letzten Tage (SIP), die in den Speicher gehen.
Ändert sich die Watchlist, verbindet sich der Strom mit der neuen Symbolliste neu.
Ohne eingerichteten Schlüssel bleibt der Dienst untätig.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from ..modell import Quote, Trade, ns_aus_iso
from ..netz import holen
from ..quellen.alpaca import ALPACA, DATEN, _kopf
from .watchlist import MAX_US
from .zustand import Wert, Zustand

UTC = timezone.utc


class UsSammler(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="us-sammler")
        self.z = zustand
        self.neu_verbinden = threading.Event()
        zustand.beobachter.append(self._watchlist)

    def symbole(self) -> list[str]:
        with self.z.lock:
            return [w.id for w in self.z.werte.values() if w.markt == "US"][:MAX_US]

    def _watchlist(self, w: Wert, dazu: bool) -> None:
        if w.markt != "US":
            return
        if dazu:
            threading.Thread(target=self._neu, args=(w.id,), daemon=True, name=f"us-{w.id}").start()
        else:
            self.neu_verbinden.set()

    def _neu(self, sym: str) -> None:
        try:
            self.historie(sym)
            self.anlauf([sym])
        except Exception as e:
            self.z.melden("alpaca", fehler=f"{sym}: {type(e).__name__}: {str(e)[:100]}")
        self.neu_verbinden.set()

    def _rest(self, pfad: str, **p) -> dict:
        """Alpaca-REST mit Weiterblättern; Kerzen werden je Symbol zusammengeführt."""
        aus: dict = {}
        while True:
            r = json.loads(holen(f"{DATEN}{pfad}?{urllib.parse.urlencode(p)}", kopf=_kopf()))
            if "bars" not in r:
                return r
            for sym, liste in (r.get("bars") or {}).items():
                aus.setdefault(sym, []).extend(liste)
            if not r.get("next_page_token"):
                return {"bars": aus}
            p["page_token"] = r["next_page_token"]

    def historie(self, sym: str) -> None:
        """Einmalig: Tageskerzen 5 Jahre und Minutenkerzen der letzten 7 Tage (SIP, alle Börsen) in den Speicher."""
        jetzt = datetime.now(UTC)
        heute = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
        tage = self._rest("/v2/stocks/bars", symbols=sym, timeframe="1Day", feed="sip", limit=10000,
                          start=(jetzt - timedelta(days=5 * 365)).date().isoformat(), end=heute.isoformat())
        zeilen = [[ns_aus_iso(b["t"]) // 10**9, b["o"], b["h"], b["l"], b["c"], b["v"]] for b in tage["bars"].get(sym, [])]
        self.z.speicher.kerzen("kerzen_1d", sym, zeilen, "sip")
        minuten = self._rest("/v2/stocks/bars", symbols=sym, timeframe="1Min", feed="sip", limit=10000,
                             start=(heute - timedelta(days=7)).isoformat(), end=heute.isoformat())
        zeilen = [[ns_aus_iso(b["t"]) // 10**9, b["o"], b["h"], b["l"], b["c"], b["v"]] for b in minuten["bars"].get(sym, [])]
        self.z.speicher.kerzen("kerzen_1m", sym, zeilen, "sip")

    def sip_nachziehen(self) -> None:
        """Ersetzt die Kerzen des Tages, die älter als 15 Minuten sind, durch die vollständigen aller US-Börsen (SIP).
        Alpaca gibt SIP-Daten im Gratis-Tarif erst nach 15 Minuten heraus; bis dahin bleibt die IEX-Kerze (q = iex)."""
        symbole = self.symbole()
        if not symbole:
            return
        jetzt = datetime.now(UTC)
        ende = (jetzt - timedelta(minutes=16)).replace(second=0, microsecond=0)
        tagesbeginn = jetzt.replace(hour=8, minute=0, second=0, microsecond=0)
        if getattr(self, "_sip_tag", None) != jetzt.date():
            self._sip_tag, self._sip_bis = jetzt.date(), {}
        start = min(self._sip_bis.get(s, tagesbeginn) for s in symbole)
        if ende <= start:
            return
        bars = self._rest("/v2/stocks/bars", symbols=",".join(symbole), timeframe="1Min", feed="sip", limit=10000,
                          start=start.isoformat(), end=ende.isoformat())["bars"]
        with self.z.lock:
            for sym in symbole:
                w = self.z.werte.get(sym)
                if w is None:
                    continue
                for b in bars.get(sym, []):
                    self.z.kerze_setzen(w, ns_aus_iso(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"], "sip")
                w.stueck = sum(k[4] for k in w.kerzen.values())             # Tagesvolumen aus den Kerzen, ab jetzt überwiegend sip
                w.umsatz = sum(k[3] * k[4] for k in w.kerzen.values())
                self._sip_bis[sym] = ende
            self.z.melden("alpaca", sip_bis=ende.isoformat(timespec="minutes"))
            self.z.geaendert()

    def _sip_schleife(self) -> None:
        while True:
            try:
                self.sip_nachziehen()
            except Exception as e:
                self.z.melden("alpaca", sip_fehler=f"{type(e).__name__}: {str(e)[:100]}")
            time.sleep(60)

    def anlauf(self, symbole: list[str]) -> None:
        jetzt = datetime.now(UTC)
        heute = jetzt.date().isoformat()
        liste = ",".join(symbole)
        tage = self._rest("/v2/stocks/bars", symbols=liste, timeframe="1Day", feed="sip", limit=10000,
                          start=(jetzt - timedelta(days=10)).date().isoformat(),
                          end=(jetzt - timedelta(minutes=20)).isoformat(timespec="seconds"))
        snap = self._rest("/v2/stocks/snapshots", symbols=liste, feed="iex")
        # Minutenkerzen des laufenden Tages ab 8:00 UTC (US-Vorbörse). Vorher gibt es keine, und einen Start in der Zukunft
        # beantwortet Alpaca mit HTTP 400; dann scheiterte der ganze Anlauf und wiederholte sich bis 8:00 UTC alle 30 Sekunden.
        kerzen = (self._rest("/v2/stocks/bars", symbols=liste, timeframe="1Min", feed="iex", start=f"{heute}T08:00:00Z", limit=10000)
                  if jetzt.hour >= 8 else {"bars": {}})
        with self.z.lock:
            for sym in symbole:
                w = self.z.werte.get(sym)
                if w is None:
                    continue
                vorher = [b for b in tage["bars"].get(sym, []) if not b["t"].startswith(heute)]
                if vorher:
                    w.vortag = vorher[-1]["c"]
                s = snap.get(sym) or {}
                if s.get("latestTrade"):
                    t = s["latestTrade"]
                    w.letzter, w.zeit = t["p"], ns_aus_iso(t["t"])
                if s.get("dailyBar", {}).get("t", "").startswith(heute):
                    w.eroeffnung = s["dailyBar"]["o"]
                    w.stueck = s["dailyBar"]["v"]
                    w.umsatz = s["dailyBar"]["v"] * s["dailyBar"].get("vw", s["dailyBar"]["c"])
                if s.get("latestQuote"):
                    q = s["latestQuote"]
                    self.z.anwenden(Quote("alpaca", "IEXG", sym, ns_aus_iso(q["t"]), q["bp"], q["bs"], q["ap"], q["as"], "USD"))
                for b in kerzen["bars"].get(sym, []):
                    self.z.kerze_setzen(w, ns_aus_iso(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"])
            self.z.melden("alpaca", anlauf=jetzt.isoformat(timespec="seconds"), fehler=None)
            self.z.geaendert()

    async def strom(self, symbole: list[str]) -> None:
        """Liest den Strom, bis sich die Watchlist ändert."""
        async def lesen():
            letzte_meldung = 0.0
            async for e in ALPACA.strom(symbole, arten=("trades", "quotes", "bars"), feed="iex"):
                with self.z.lock:
                    if isinstance(e, dict):                                # Minutenkerze
                        w = self.z.werte.get(e["instrument"])
                        if w:
                            self.z.kerze_setzen(w, e["ts"], e["open"], e["high"], e["low"], e["close"], e["volumen"])
                    elif isinstance(e, (Trade, Quote)):
                        self.z.anwenden(e)
                    self.z.geaendert()
                    if time.time() - letzte_meldung > 10:
                        self.z.melden("alpaca", strom=datetime.now(UTC).isoformat(timespec="seconds"), fehler=None, hinweis=None)
                        letzte_meldung = time.time()
        aufgabe = asyncio.ensure_future(lesen())
        while not aufgabe.done():
            await asyncio.sleep(1)
            if self.neu_verbinden.is_set():
                aufgabe.cancel()
        if not aufgabe.cancelled() and aufgabe.exception():
            raise aufgabe.exception()

    def run(self) -> None:
        threading.Thread(target=self._sip_schleife, daemon=True, name="us-sip").start()
        erste = True
        while True:
            symbole = self.symbole()
            self.neu_verbinden.clear()
            if not symbole:
                self.z.melden("alpaca", strom=None, hinweis="kein US-Wert auf der Watchlist")
                self.neu_verbinden.wait()
                continue
            try:
                if erste:
                    jetzt = int(time.time())
                    for s in symbole:                                       # Historie fehlt noch (erster Start)
                        if not self.z.speicher.kerzen_lesen("kerzen_1d", s, jetzt - 30 * 86400):
                            self.historie(s)
                    self.anlauf(symbole)
                    erste = False
                asyncio.run(self.strom(symbole))
            except Exception as e:
                self.z.melden("alpaca", fehler=f"{type(e).__name__}: {str(e)[:120]}")
                self.neu_verbinden.wait(30)
