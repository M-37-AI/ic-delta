"""Alpaca Market Data (USA). Braucht ein kostenloses Konto mit eigenem API-Schlüssel.

Gratis-Tarif: live nur IEX (rund 4 % des Volumens, max. 30 Symbole im Strom);
Historie aller US-Börsen (SIP) frei, sofern älter als 15 Minuten.

Schlüssel: Umgebungsvariablen APCA_API_KEY_ID und APCA_API_SECRET_KEY,
oder eine Datei (Standard ~/.config/marktdaten/alpaca.env, anders per MARKTDATEN_ALPACA_ENV).
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.parse
from typing import AsyncIterator, Iterator

from ..modell import Ereignis, Quote, Trade, ns_aus_iso
from ..netz import holen, zaehlen
from .basis import Quelle

DATEN = "https://data.alpaca.markets"
STROM = "wss://stream.data.alpaca.markets/v2/"


def schluessel() -> dict[str, str]:
    namen = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
    werte = {k: os.environ.get(k, "") for k in namen}
    datei = pathlib.Path(os.environ.get("MARKTDATEN_ALPACA_ENV", "~/.config/marktdaten/alpaca.env")).expanduser()
    if not all(werte.values()) and datei.exists():
        for z in datei.read_text().splitlines():
            k, _, v = z.partition("=")
            if k.strip() in namen and not werte[k.strip()]:
                werte[k.strip()] = v.strip()
    if not all(werte.values()):
        raise RuntimeError("Alpaca-Schlüssel fehlt: siehe .env.example")
    return werte


def _kopf() -> dict[str, str]:
    s = schluessel()
    return {"APCA-API-KEY-ID": s["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": s["APCA_API_SECRET_KEY"]}


class Alpaca(Quelle):
    def __init__(self):
        super().__init__(
            name="alpaca", titel="Alpaca (USA)", region="US",
            inhalt="Trades, Quotes und Kerzen; live IEX, Historie aller US-Börsen", tiefe="bester Kurs",
            abrufbar="Historie über viele Jahre", arten=("trades", "quotes", "bars"), schluessel=True,
            lizenz="eigenes Konto nötig; Daten nur für die eigene Nutzung, keine Weitergabe oder Veröffentlichung",
            info_url="https://alpaca.markets/data",
        )

    def dateien(self, art: str) -> list[str]:
        raise NotImplementedError("Alpaca hat keine Dateien: abfrage() oder strom() verwenden")

    def abfrage(self, art: str, symbole: list[str], start: str, ende: str | None = None,
                feed: str = "sip", zeitrahmen: str = "1Min") -> Iterator[Ereignis | dict]:
        """art = trades | quotes | bars. Blättert automatisch durch alle Seiten.
        Kerzen kommen als dict zurück, Trades und Quotes als Ereignisse."""
        p = {"symbols": ",".join(symbole), "start": start, "limit": 10000, "feed": feed}
        if ende:
            p["end"] = ende
        if art == "bars":
            p["timeframe"] = zeitrahmen
        kopf = _kopf()
        while True:
            r = json.loads(holen(f"{DATEN}/v2/stocks/{art}?{urllib.parse.urlencode(p)}", kopf=kopf))
            for sym, liste in (r.get(art) or {}).items():
                for x in liste:
                    yield _umwandeln(art, sym, x, feed)
            if not r.get("next_page_token"):
                return
            p["page_token"] = r["next_page_token"]

    async def strom(self, symbole: list[str], arten=("trades", "quotes"), feed: str = "iex") -> AsyncIterator[Ereignis | dict]:
        """Live-Strom per WebSocket (braucht das Paket websockets)."""
        import websockets
        s = schluessel()
        async with websockets.connect(STROM + feed, max_size=None) as ws:
            await ws.recv()
            await ws.send(json.dumps({"action": "auth", "key": s["APCA_API_KEY_ID"], "secret": s["APCA_API_SECRET_KEY"]}))
            antwort = json.loads(await ws.recv())
            if antwort[0].get("T") == "error":
                raise RuntimeError(f"Anmeldung abgelehnt: {antwort[0].get('msg')}")
            await ws.send(json.dumps({"action": "subscribe", **{a: symbole for a in arten}}))
            await ws.recv()
            kurz = {"t": "trades", "q": "quotes", "b": "bars"}
            async for roh in ws:
                zaehlen("Alpaca", len(roh))
                for m in json.loads(roh):
                    if m.get("T") in kurz:
                        yield _umwandeln(kurz[m["T"]], m["S"], m, feed)


def _umwandeln(art: str, sym: str, x: dict, feed: str):
    venue = "IEXG" if feed == "iex" else "SIP"
    if art == "trades":
        return Trade("alpaca", venue, sym, ns_aus_iso(x["t"]), x["p"], x["s"], "USD", trade_id=str(x.get("i", "")),
                     extra={"boerse": x.get("x"), "bedingungen": x.get("c")})
    if art == "quotes":
        return Quote("alpaca", venue, sym, ns_aus_iso(x["t"]), x.get("bp"), x.get("bs"), x.get("ap"), x.get("as"),
                     "USD", extra={"bid_boerse": x.get("bx"), "ask_boerse": x.get("ax")})
    return {"instrument": sym, "ts": ns_aus_iso(x["t"]), "open": x["o"], "high": x["h"], "low": x["l"],
            "close": x["c"], "volumen": x["v"], "trades": x.get("n"), "vwap": x.get("vw")}


ALPACA = Alpaca()
