"""Euronext: MiFIR-Abschlussdateien für alle sieben Märkte (nur Post-Trade).

Die Datei wird auf Anfrage erzeugt (ZIP mit CSV). Zeile 1 ist ein Copyright-Hinweis, dann folgt die Kopfzeile.
Zeitstempel in UTC.
"""
from __future__ import annotations

from typing import Iterator

from ..modell import Ereignis, Trade, ns_aus_iso, zahl
from ..netz import holen, text
from .basis import Quelle, spalte, tabelle

BASIS = "https://marketdata.euronext.com/data-reporting-service/trades-file/download/"
MAERKTE = ["PAR", "AMS", "BRU", "LIS", "MIL", "DUB", "OSL"]
ZEITRAUM = ["LAST_15_MINUTES", "LAST_HOUR", "CURRENT_TRADING_DAY", "PREVIOUS_TRADING_DAY"]


class Euronext(Quelle):
    def __init__(self):
        super().__init__(
            name="euronext", titel="Euronext (Paris, Amsterdam, Mailand, Brüssel, Lissabon, Dublin, Oslo)",
            region="EU", inhalt="nur Abschlüsse", tiefe="–", abrufbar="laufender und vorheriger Handelstag",
            lizenz="frei nutzbar; bei Weitergabe Euronext nennen und deren Hinweis beifügen",
            info_url="https://marketdata.euronext.com/data-reporting-service/trades-file", arten=("posttrade",),
        )

    def dateien(self, art: str, gattung: str = "EQUITIES") -> list[str]:
        """Bezeichner im Format GATTUNG/ZEITRAUM/MARKT."""
        return [f"{gattung}/{z}/{m}" for z in ZEITRAUM for m in MAERKTE]

    def lesen(self, datei: str, art: str = "posttrade") -> Iterator[Ereignis]:
        return self.umwandeln(text(holen(BASIS + datei, timeout=300)))

    def umwandeln(self, zeilen: list[str]) -> Iterator[Ereignis]:
        start = next(i for i, z in enumerate(zeilen) if z.startswith("TradingDateTime"))
        for z in tabelle(zeilen[start:]):
            preis = zahl(spalte(z, "mifidprice"))
            if preis is None:
                continue
            yield Trade(self.name, spalte(z, "venue") or "", z["mifidinstrumentid"], ns_aus_iso(z["tradingdatetime"]),
                        preis, zahl(spalte(z, "mifidquantity")) or 0, spalte(z, "mifidcurrency") or "",
                        ns_aus_iso(z["publicationdatetime"]), spalte(z, "tradeuniqueidentifier") or "",
                        {k: v for k, v in z.items() if k.startswith("mmt") and v not in ("", "-")})


EURONEXT = Euronext()
