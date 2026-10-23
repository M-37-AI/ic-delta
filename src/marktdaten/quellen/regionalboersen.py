"""Deutsche Regionalbörsen und weitere kleine Plätze.

- BÖAG: Börse Düsseldorf, Hamburg, Hannover und Quotrix
- gettex (Bayerische Börse München)
- Equiduct (Börse Berlin)
- NGM (Nordic Growth Market, Gruppe Börse Stuttgart)
Alle liefern nur den besten Kurs, kein tiefes Buch.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from ..modell import Ereignis, Quote, Trade, ns_aus_iso, ns_aus_lokal, zahl
from ..netz import holen, holen_json, text, zeilen_strom
from .basis import Quelle, spalte, tabelle

NUR_PRIVAT = "nur natürliche Personen, ausschließlich privat, keine Weitergabe an Dritte"


class Boeag(Quelle):
    BASIS = "https://cld42.boersenag.de/m13data/data/"

    def __init__(self):
        super().__init__(
            name="boeag", titel="BÖAG: Düsseldorf, Hamburg, Hannover, Quotrix", region="DE",
            inhalt="Kurse und Abschlüsse, rund 16.000 Werte", tiefe="bester Kurs", abrufbar="24 Stunden",
            lizenz=NUR_PRIVAT, info_url="https://www.boerse-duesseldorf.de",
        )

    def dateien(self, art: str) -> list[str]:
        liste = "m13filespt.json" if art == "pretrade" else "m13files.json"
        return holen_json(self.BASIS + liste)["M13Files"]

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        return self.umwandeln(text(holen(self.BASIS + datei)), art)

    def umwandeln(self, zeilen: list[str], art: str) -> Iterator[Ereignis]:
        for z in tabelle(zeilen, ";"):
            if art == "pretrade":
                yield Quote(self.name, z["mic"], z["isin"], ns_aus_iso(z["time"]), zahl(z["bid"]),
                            zahl(z["bid_volume"]), zahl(z["ask"]), zahl(z["ask_volume"]), "EUR",
                            extra={"status": z.get("status", "")})
            else:
                preis = zahl(z["price"])
                if preis is None:
                    continue
                ts = ns_aus_lokal(datetime.strptime(z["time"], "%d.%m.%Y %H:%M:%S"))   # Ortszeit
                yield Trade(self.name, z["mic"], z["isin"], ts, preis, zahl(z["size"]) or 0, "EUR",
                            extra={"zusatz": z.get("supplement", "").strip('" ')})


class Gettex(Quelle):
    SEITE = "https://www.gettex.de/en/trading/delayed-data/{}-data/"

    def __init__(self):
        super().__init__(
            name="gettex", titel="gettex (Börse München)", region="DE",
            inhalt="Kurse und Abschlüsse alle 15 Minuten, tagsüber über 600 MB je Datei", tiefe="bester Kurs",
            abrufbar="laufender und vorheriger Tag",
            lizenz=NUR_PRIVAT, info_url="https://www.gettex.de/en/trading/delayed-data/",
        )

    def dateien(self, art: str) -> list[str]:
        html = holen(self.SEITE.format(art)).decode(errors="replace")
        return sorted(set(re.findall(rf'https://erdk\.bayerische-boerse\.de:8000/delayed-data/[^"\s<>]*{art}[^"\s<>]*\.csv\.gz', html)),
                      reverse=True)

    def lesen(self, datei: str, art: str, max_mb: float | None = None) -> Iterator[Ereignis]:
        """Tagsüber sind die Dateien über 600 MB groß: deshalb als Strom, max_mb begrenzt die Menge."""
        return self.umwandeln(zeilen_strom(datei, max_mb), art, datei)

    def umwandeln(self, zeilen: list[str], art: str, datei: str) -> Iterator[Ereignis]:
        # Dateiname: pretrade.20261019.16.45.munc.csv.gz; Zeitangaben in UTC
        m = re.search(r"\.(\d{8})\.\d\d\.\d\d\.(mun[cd])\.", datei)
        tag, venue = f"{m[1][:4]}-{m[1][4:6]}-{m[1][6:]}", m[2].upper()
        for z in zeilen:
            f = z.split(",")
            if len(f) < 5 or not f[0][:2].isalpha():
                continue
            ts = ns_aus_iso(f"{tag}T{f[1]}")
            if art == "pretrade" and len(f) >= 7:
                yield Quote(self.name, venue, f[0], ts, zahl(f[3]), zahl(f[4]), zahl(f[5]), zahl(f[6]), f[2])
            elif art == "posttrade":
                yield Trade(self.name, venue, f[0], ts, float(f[3]), zahl(f[4]) or 0, f[2])


class Equiduct(Quelle):
    BASIS = "https://download.equiduct.com/"

    def __init__(self):
        super().__init__(
            name="equiduct", titel="Equiduct (Börse Berlin)", region="EU",
            inhalt="Abschlüsse des Tages und letzter Kurs, rund 1.800 Werte", tiefe="bester Kurs",
            abrufbar="nur der laufende Tag",
            lizenz="kostenlos veröffentlicht, 15 Minuten verzögert; keine weiteren Bedingungen gefunden",
            info_url="https://www.equiduct.com/document-library/",
        )

    def dateien(self, art: str) -> list[str]:
        return ["equiduct_pretrade.csv" if art == "pretrade" else "equiduct_trades.csv"]

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        return self.umwandeln(text(holen(self.BASIS + datei)), art)

    def umwandeln(self, zeilen: list[str], art: str) -> Iterator[Ereignis]:
        for z in tabelle(zeilen):
            d = z["timestamp"]                      # 20261020-07:01:51.500957, UTC
            ts = ns_aus_iso(f"{d[:4]}-{d[4:6]}-{d[6:8]}T{d[9:]}")
            if art == "pretrade":
                yield Quote(self.name, "XEQT", z["isin"], ts, zahl(z["buyprice"]), zahl(z["buyqty"]),
                            zahl(z["sellprice"]), zahl(z["sellqty"]), z["currency"],
                            extra={"symbol": z["symbol"], "heimatmarkt": z["home_market"]})
            else:
                yield Trade(self.name, "XEQT", z["isin"], ts, float(z["price"]), zahl(z["quantity"]) or 0,
                            z["currency"], trade_id=z["tvtic"],
                            extra={"symbol": z["symbol"], "heimatmarkt": z["home_market"],
                                   "flags": z.get("flags", "").strip('="')})


class Ngm(Quelle):
    BASIS = "https://mdapi.ngm.se/delayed/"

    def __init__(self):
        super().__init__(
            name="ngm", titel="NGM Nordic Growth Market", region="Nordeuropa",
            inhalt="Kurse (vor allem strukturierte Produkte, rund 70.000) und Abschlüsse", tiefe="bester Kurs",
            abrufbar="72 Stunden", lizenz="nicht-kommerzielle Nutzung kostenlos",
            info_url="https://www.ngm.se/en/the-exchange/market-data/delayed",
        )

    def dateien(self, art: str) -> list[str]:
        return holen_json(self.BASIS + ("pre-trade" if art == "pretrade" else "post-trade"))

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        url = self.BASIS + ("pre-trade/" if art == "pretrade" else "post-trade/") + datei
        return self.umwandeln(text(holen(url)), art, datei)

    def umwandeln(self, zeilen: list[str], art: str, datei: str) -> Iterator[Ereignis]:
        for z in tabelle(zeilen):
            if art == "pretrade":
                # Momentaufnahme ohne eigenen Zeitstempel: Zeit aus dem Dateinamen (YYYY-MM-DDTHHMM, UTC)
                ts = ns_aus_iso(f"{datei[:13]}:{datei[13:15]}:00")
                bid, ask = zahl(spalte(z, "bidprice")), zahl(spalte(z, "askprice"))
                if bid is None and ask is None:
                    continue
                yield Quote(self.name, "XNGM", z["isin"], ts, bid, zahl(spalte(z, "bidquantity")),
                            ask, zahl(spalte(z, "askquantity")), z.get("currency", ""), extra={"symbol": z.get("symbol", "")})
            else:
                preis = zahl(spalte(z, "price"))
                if preis is None:
                    continue
                pub = spalte(z, "publicationdateandtime")
                yield Trade(self.name, spalte(z, "venueofexecution") or "XNGM",
                            spalte(z, "instrumentidentificationcode") or "", ns_aus_iso(z["tradingdateandtime"]),
                            preis, zahl(spalte(z, "quantity")) or 0, spalte(z, "pricecurrency") or "",
                            ns_aus_iso(pub) if pub else None, spalte(z, "transactionidentificationcode") or "",
                            {"notation": spalte(z, "pricenotation")})


BOEAG, GETTEX, EQUIDUCT, NGM = Boeag(), Gettex(), Equiduct(), Ngm()
