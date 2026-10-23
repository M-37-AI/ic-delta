"""Ergänzende Referenzdaten: Leerverkaufsvolumen, Symbolverzeichnis, Fundamentaldaten, Wechselkurse.

Diese Quellen liefern Tabellen (dicts), keine Marktereignisse.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

from ..netz import holen, holen_json, text
from .basis import tabelle

QUELLEN = {
    "finra": ("FINRA Reg SHO (USA)", "tägliches Leerverkaufsvolumen je Aktie", "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data"),
    "nasdaq_symbole": ("Nasdaq Trader Symbolverzeichnis", "alle US-Symbole mit Börse und ETF-Kennzeichen", "https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs"),
    "sec": ("SEC EDGAR XBRL", "Fundamentaldaten aus Pflichtmeldungen", "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"),
    "ezb": ("EZB Referenzkurse", "tägliche Wechselkurse zum Euro", "https://data.ecb.europa.eu/help/api/overview"),
}


def finra_leerverkauf(datum: str) -> Iterator[dict]:
    """datum als JJJJMMTT. Volumen der FINRA-Meldestellen (außerbörslich), nicht das Gesamtvolumen."""
    zeilen = text(holen(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{datum}.txt"))
    yield from tabelle([z for z in zeilen if "|" in z], "|")


def nasdaq_symbole() -> Iterator[dict]:
    for datei in ("nasdaqlisted.txt", "otherlisted.txt"):
        zeilen = text(holen(f"https://www.nasdaqtrader.com/dynamic/SymDir/{datei}"))
        yield from tabelle([z for z in zeilen if not z.startswith("File Creation Time")], "|")


def sec_kennzahlen(cik: int) -> dict:
    """Die SEC verlangt einen User-Agent mit Kontakt: Umgebungsvariable MARKTDATEN_KONTAKT setzen."""
    kontakt = os.environ.get("MARKTDATEN_KONTAKT")
    if not kontakt:
        raise RuntimeError("MARKTDATEN_KONTAKT setzen, z. B. 'Name mail@beispiel.de' (Vorgabe der SEC)")
    return holen_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", kopf={"User-Agent": kontakt})


def ezb_kurs(waehrung: str = "USD", letzte: int = 30) -> Iterator[dict]:
    url = (f"https://data-api.ecb.europa.eu/service/data/EXR/D.{waehrung}.EUR.SP00.A"
           f"?lastNObservations={letzte}&format=jsondata")
    d = json.loads(holen(url))
    tage = [w["id"] for w in d["structure"]["dimensions"]["observation"][0]["values"]]
    beob = next(iter(d["dataSets"][0]["series"].values()))["observations"]
    for i, tag in enumerate(tage):
        if str(i) in beob:
            yield {"datum": tag, "waehrung": waehrung, "kurs": beob[str(i)][0]}
