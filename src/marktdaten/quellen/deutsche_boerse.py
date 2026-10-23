"""Deutsche Börse: MiFIR-Delayed-Dateien für Xetra, Börse Frankfurt, Tradegate und Eurex.

Eine Datei pro Minute, gz-komprimiertes JSON (eine Meldung pro Zeile), 15 Minuten verzögert.
Xetra enthält das Orderbuch mit bis zu 5 Stufen pro Seite; Meldungen mit retailFlag gehören
zum getrennten Retail-Buch. Die Meldungen sind Teil-Updates: oft kommt nur eine Seite.
"""
from __future__ import annotations

import json
from typing import Iterator

from ..modell import KAUF, VERKAUF, BuchSeite, Ereignis, Quote, Trade, ns_aus_iso
from ..netz import holen, holen_json
from .basis import Quelle

BASIS = "https://mfs.deutsche-boerse.com/api/"


class DeutscheBoerse(Quelle):
    def __init__(self, name, kuerzel, mic, titel, inhalt, tiefe, arten=("pretrade", "posttrade")):
        super().__init__(
            name=name, titel=titel, region="DE", inhalt=inhalt, tiefe=tiefe,
            abrufbar="etwa 2 Tage", arten=arten,
            lizenz="kostenlos, solange die Daten nicht kommerzialisiert werden; KI-Nutzung braucht laut DBAG eine Non-Display-Lizenz",
            info_url="https://www.mds.deutsche-boerse.com/mds-en/real-time-data/Delayed-data",
        )
        self.kuerzel, self.mic = kuerzel, mic

    def dateien(self, art: str) -> list[str]:
        return holen_json(f"{BASIS}{self.kuerzel}-{art}")["CurrentFiles"]

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        for zeile in holen(BASIS + "download/" + datei).splitlines():
            if zeile.startswith(b"{"):
                yield from self.umwandeln(json.loads(zeile))

    def umwandeln(self, d: dict) -> Iterator[Ereignis]:
        isin = d["instrumentIdentificationCode"]
        venue = d.get("venueOfExecution") or self.mic
        ts_pub = ns_aus_iso(d["publicationDateAndTime"]) if "publicationDateAndTime" in d else None
        if d.get("messageId") == "posttrade":
            yield Trade(
                self.name, venue, isin, ns_aus_iso(d["tradingDateAndTime"]),
                float(d.get("price", 0)), float(d.get("quantity", 0)), d.get("priceCurrency", ""),
                ts_pub, d.get("transactionIdentificationCode", ""),
                {k: v for k, v in d.items() if k.startswith("mmt") or k in ("tradingSystem", "notionalAmount")},
            )
            return
        ts = ns_aus_iso(d["updateDateAndTime"])
        buch = "retail" if d.get("retailFlag") else "regulaer"
        tiefe = False
        for seite, preise, orders in ((KAUF, "mdBidMktDepthGroup1", "aggregatedNumberOfOrdersAndQuotesBid"),
                                      (VERKAUF, "mdAskMktDepthGroup1", "aggregatedNumberOfOrdersAndQuotesAsk")):
            if preise in d:
                tiefe = True
                anz = [o.get("noOfOrders") for o in d.get(orders, [])]
                stufen = [(s["price"], s["quantity"], anz[i] if i < len(anz) else None)
                          for i, s in enumerate(d[preise])]
                yield BuchSeite(self.name, venue, isin, ts, seite, stufen, buch, ts_pub)
        if tiefe:
            return
        # Level 1: Xetra/Frankfurt nennen es bestBid, Tradegate bid
        bid, ask = d.get("bestBid", d.get("bid")), d.get("bestAsk", d.get("ask"))
        if bid is not None or ask is not None:
            yield Quote(self.name, venue, isin, ts, bid, d.get("bestBidQty", d.get("bidQty")),
                        ask, d.get("bestAskQty", d.get("askQty")), d.get("priceCurrency", ""), ts_pub,
                        {"buch": buch, "phase": d.get("tradingSystemPhase")})


XETRA = DeutscheBoerse("xetra", "DETR", "XETR", "Xetra (Deutsche Börse)",
                       "Orderbuch und Abschlüsse, rund 5.000 Werte", "5 Stufen mit Orderanzahl")
FRANKFURT = DeutscheBoerse("frankfurt", "DFRA", "XFRA", "Börse Frankfurt",
                           "Kurse und Abschlüsse, rund 28.000 Werte", "bester Kurs")
TRADEGATE = DeutscheBoerse("tradegate", "DGAT", "XGAT", "Tradegate",
                           "Kurse und Abschlüsse, rund 16.000 Werte", "bester Kurs")
EUREX = DeutscheBoerse("eurex", "DEUR", "XEUR", "Eurex (Derivate)",
                       "Abschlüsse in Optionen und Futures", "–", arten=("posttrade",))
