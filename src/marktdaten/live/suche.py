"""Wertpapiersuche für die Watchlist über offizielle Listen.

- Deutschland: Liste aller auf Xetra handelbaren Instrumente (Deutsche Börse, täglich neu)
- USA: Symbolverzeichnis von Nasdaq Trader (alle US-Börsen)
- Krypto: Handelspaare von Binance (Spot, gegen USDT und EUR)
Die Listen werden einmal am Tag geholt und im Datenordner zwischengespeichert.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from datetime import date

from ..netz import holen
from .speicher import ORT

XETRA_SEITE = "https://www.cashmarket.deutsche-boerse.com/cash-en/trading/Tradable-Instruments-Xetra/Downloads"
_cache: dict[str, list[dict]] = {}
_lock = threading.Lock()


def _datei(name: str):
    return ORT / f"liste-{name}-{date.today().isoformat()}.json"


def _laden(name: str, holer) -> list[dict]:
    with _lock:
        if name in _cache:
            return _cache[name]
        d = _datei(name)
        if d.exists():
            _cache[name] = json.loads(d.read_text())
        else:
            for alt in ORT.glob(f"liste-{name}-*.json"):
                alt.unlink()                                    # Listen von gestern verwerfen
            _cache[name] = holer()
            d.write_text(json.dumps(_cache[name]))
        return _cache[name]


def _xetra() -> list[dict]:
    seite = holen(XETRA_SEITE).decode(errors="replace")
    url = re.search(r'href="([^"]*t7-xetr-allTradableInstruments\.csv)"', seite)[1]
    if url.startswith("/"):
        url = "https://www.cashmarket.deutsche-boerse.com" + url
    zeilen = holen(url).decode("utf-8", errors="replace").splitlines()
    start = next(i for i, z in enumerate(zeilen) if z.startswith("Product Status"))
    aus = []
    for z in csv.DictReader(io.StringIO("\n".join(zeilen[start:])), delimiter=";"):
        if z.get("Instrument Status") != "Active" or not z.get("ISIN"):
            continue
        aus.append({"id": z["ISIN"], "symbol": z.get("Mnemonic") or z["ISIN"], "name": z["Instrument"],
                    "zusatz": " · ".join(x for x in (z.get("WKN", "").lstrip("0"), z.get("Product Assignment Group Description", ""),
                                                     z.get("Instrument Type", "")) if x),
                    "markt": "DE"})
    return aus


def _usa() -> list[dict]:
    aus = []
    for datei, sym_spalte, boerse in (("nasdaqlisted.txt", "Symbol", lambda z: "Nasdaq"),
                                      ("otherlisted.txt", "ACT Symbol", lambda z: {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca",
                                                                                   "Z": "Cboe BZX", "V": "IEX"}.get(z.get("Exchange"), ""))):
        text = holen(f"https://www.nasdaqtrader.com/dynamic/SymDir/{datei}").decode(errors="replace")
        for z in csv.DictReader(io.StringIO(text), delimiter="|"):
            sym = z.get(sym_spalte) or ""
            if not sym or z.get("Test Issue") == "Y" or "File Creation" in sym:
                continue
            aus.append({"id": sym, "symbol": sym, "name": z.get("Security Name", "").split(" - ")[0],
                        "zusatz": boerse(z) + (" · ETF" if z.get("ETF") == "Y" else ""), "markt": "US"})
    return aus


def _krypto() -> list[dict]:
    preise = json.loads(holen("https://api.binance.com/api/v3/ticker/price"))
    aus = []
    for p in preise:
        s = p["symbol"]
        for quote in ("USDT", "EUR"):
            if s.endswith(quote) and len(s) > len(quote):
                aus.append({"id": s, "symbol": s, "name": f"{s[:-len(quote)]} / {quote}", "zusatz": "Binance Spot", "markt": "KRYPTO"})
    return aus


ROHSTOFF_KONTRAKTE = [("CLUSDT", "WTI Rohöl"), ("BZUSDT", "Brent Rohöl"), ("NATGASUSDT", "Erdgas"), ("XAUUSDT", "Gold"),
                      ("XAGUSDT", "Silber"), ("XPTUSDT", "Platin"), ("XPDUSDT", "Palladium"), ("COPPERUSDT", "Kupfer")]


def _rohstoffe() -> list[dict]:
    """Rohstoffe als Perpetual-Kontrakte auf Binance Futures (feste Liste, kein Abruf)."""
    return [{"id": s, "symbol": s, "name": f"{n} (Perpetual)", "zusatz": "Binance Futures · kein Spotpreis", "markt": "ROHSTOFFE"}
            for s, n in ROHSTOFF_KONTRAKTE]


LISTEN = {"DE": ("xetra", _xetra), "US": ("usa", _usa), "KRYPTO": ("krypto", _krypto), "ROHSTOFFE": ("rohstoffe", _rohstoffe)}


def suchen(text: str, markt: str | None = None, anzahl: int = 25) -> list[dict]:
    text = text.strip().upper()
    if len(text) < 2:
        return []
    treffer = []
    for m, (name, holer) in LISTEN.items():
        if markt and m != markt:
            continue
        for x in _laden(name, holer):
            sym, nam, id_ = x["symbol"].upper(), x["name"].upper(), x["id"].upper()
            if text in (sym, id_):
                rang = 0
            elif sym.startswith(text) or nam.startswith(text):
                rang = 1
            elif text in nam or text in id_:
                rang = 2
            else:
                continue
            treffer.append((rang, len(nam), x))
    treffer.sort(key=lambda t: (t[0], t[1]))
    return [x for _, _, x in treffer[:anzahl]]


def finden(id_: str, markt: str) -> dict | None:
    name, holer = LISTEN[markt]
    return next((x for x in _laden(name, holer) if x["id"] == id_), None)
