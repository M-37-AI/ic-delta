"""Nasdaq Nordic und Baltic: Regulatory Trade Reports (Stockholm, Kopenhagen, Helsinki, Island, Baltikum).

Eine CSV pro Minute (Trenner ';', erste Zeile "sep=;"), 48 Stunden abrufbar.
Pre-Trade ist ein Ereignisstrom der besten Kurse je Seite, kein tiefes Buch.
"""
from __future__ import annotations

from typing import Iterator

from ..modell import KAUF, VERKAUF, Ereignis, Quote, Trade, ns_aus_iso, zahl
from ..netz import holen, holen_json, text
from .basis import Quelle, spalte, tabelle

BASIS = "https://tradereports.nasdaq.com/api/regulatory/"
TYP = {"pretrade": "PRE_TRADE", "posttrade": "POST_TRADE"}


class NasdaqNordic(Quelle):
    def __init__(self):
        super().__init__(
            name="nordic", titel="Nasdaq Nordic und Baltic", region="Nordeuropa",
            inhalt="beste Kurse und Abschlüsse, rund 600 Werte pro Minute", tiefe="bester Kurs je Seite",
            abrufbar="48 Stunden",
            lizenz="nicht-kommerzielle Nutzung kostenlos; kommerziell über dataeurope@nasdaq.com",
            info_url="https://tradereports.nasdaq.com",
        )

    def dateien(self, art: str, gattung: str = "EQUITY") -> list[str]:
        r = holen_json(f"{BASIS}trade-reports?type={TYP[art]}&assetClass={gattung}")
        return [f"{gattung}/{n}" for n in r["reports"]]

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        gattung, name = datei.split("/", 1)
        url = f"{BASIS}trade-report/download?type={TYP[art]}&assetClass={gattung}&fileName={name}"
        return self.umwandeln(text(holen(url)), art)

    def umwandeln(self, zeilen: list[str], art: str) -> Iterator[Ereignis]:
        zeilen = [z for z in zeilen if not z.startswith('"sep=')]
        if not zeilen:
            return
        for z in tabelle(zeilen, ";"):
            isin = spalte(z, "instrumentidentificationcode", "isin") or ""
            preis = zahl(spalte(z, "price"))
            if preis is None:
                continue
            pub = spalte(z, "publicationdateandtime", "publicationtime")
            if art == "pretrade":
                kauf = spalte(z, "side") == "BUYL"
                menge = zahl(spalte(z, "quantity"))
                yield Quote(self.name, spalte(z, "venue") or "", isin, ns_aus_iso(z["updatedateandtime"]),
                            preis if kauf else None, menge if kauf else None,
                            None if kauf else preis, None if kauf else menge,
                            spalte(z, "pricecurrency") or "", ns_aus_iso(pub) if pub else None,
                            {"orders": zahl(spalte(z, "aggregatednumberofordersandquotes")),
                             "market_maker": spalte(z, "marketmarker", "marketmaker") or ""})
            else:
                ts = spalte(z, "tradingdateandtime", "tradingtime")
                yield Trade(self.name, spalte(z, "venueofexecution") or "", isin, ns_aus_iso(ts), preis,
                            zahl(spalte(z, "quantity")) or 0, spalte(z, "pricecurrency", "currency") or "",
                            ns_aus_iso(pub) if pub else None,
                            spalte(z, "transactionidentificationcode") or "")


NORDIC = NasdaqNordic()
