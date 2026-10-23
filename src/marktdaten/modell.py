"""Einheitliches Format für alle Quellen.

Jede Quelle liefert einen Strom aus vier Ereignistypen. Zeitstempel sind immer
Nanosekunden seit 1970 in UTC (``ts``), dazu optional der Veröffentlichungszeitpunkt (``ts_pub``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KAUF, VERKAUF = "B", "A"  # Bid / Ask


@dataclass(slots=True)
class Trade:
    quelle: str
    venue: str            # MIC des Handelsplatzes
    instrument: str       # ISIN in Europa, Ticker in den USA
    ts: int
    preis: float
    menge: float
    waehrung: str = ""
    ts_pub: int | None = None
    trade_id: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class Quote:
    """Bester Kauf- und Verkaufskurs (Level 1).

    Seite = None heißt: in dieser Meldung nicht enthalten. Manche Quellen (Xetra) melden
    nur die Seite, die sich geändert hat; dann gilt der letzte bekannte Wert weiter.
    """
    quelle: str
    venue: str
    instrument: str       # ISIN in Europa, Ticker in den USA
    ts: int
    bid: float | None = None
    bid_menge: float | None = None
    ask: float | None = None
    ask_menge: float | None = None
    waehrung: str = ""
    ts_pub: int | None = None
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class BuchSeite:
    """Vollständige Momentaufnahme einer Buchseite: [(preis, menge, orders), ...], beste Stufe zuerst."""
    quelle: str
    venue: str
    instrument: str       # ISIN in Europa, Ticker in den USA
    ts: int
    seite: str
    stufen: list[tuple[float, float, int | None]]
    buch: str = "regulaer"   # Xetra trennt zusätzlich das Retail-Buch
    ts_pub: int | None = None


@dataclass(slots=True)
class BuchStufe:
    """Änderung einer einzelnen Preisstufe. menge == 0 heißt: Stufe entfällt."""
    quelle: str
    venue: str
    instrument: str       # ISIN in Europa, Ticker in den USA
    ts: int
    seite: str
    preis: float
    menge: float
    orders: int | None = None
    buch: str = "regulaer"
    ts_pub: int | None = None


Ereignis = Trade | Quote | BuchSeite | BuchStufe

_BERLIN = ZoneInfo("Europe/Berlin")


def ns_aus_iso(s: str) -> int:
    """'2026-10-20T15:22:59.998840347Z' oder '2026-10-20 07:00:01.607663' (UTC) -> Nanosekunden."""
    s = s.strip().replace(" ", "T").rstrip("Z")
    haupt, _, bruch = s.partition(".")
    sek = int(datetime.fromisoformat(haupt).replace(tzinfo=timezone.utc).timestamp())
    return sek * 1_000_000_000 + int((bruch + "000000000")[:9])


def ns_aus_lokal(d: datetime) -> int:
    """Naive Zeit in deutscher Ortszeit -> Nanosekunden UTC."""
    return int(d.replace(tzinfo=_BERLIN).timestamp() * 1e9)


def iso_aus_ns(ns: int) -> str:
    sek, rest = divmod(ns, 1_000_000_000)
    return datetime.fromtimestamp(sek, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{rest:09d}Z"


def zahl(s: str | None) -> float | None:
    """Leere Felder -> None, Komma als Dezimaltrenner erlaubt."""
    if s is None:
        return None
    s = s.strip().strip('"')
    if not s or s == "-":
        return None
    if "," in s:                       # deutsches Format, z. B. 1.234,5
        s = s.replace(".", "").replace(",", ".")
    return float(s)
