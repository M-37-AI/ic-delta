"""Cboe Europe: Delayed Transparency Files (seit 21.08.2026).

Eine CSV pro Minute und Segment, ohne Kopfzeile (außer der ersten Datei des Tages).
BXE (BATE) und CXE (CHIX) liefern bis zu 5 Preisstufen, DXE (CEUX) nur das beste Gebot.
Stufen kommen einzeln; Menge 0 heißt, dass die Stufe entfällt.
Spezifikation: https://cdn.cboe.com/resources/participant_resources/Cboe_Europe_RTS_Delayed_Feed_Specification.pdf
Praktisch sind nur Dateien des laufenden Tages abrufbar.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from typing import Iterator

from ..modell import KAUF, VERKAUF, BuchStufe, Ereignis, Quote, Trade, ns_aus_iso
from ..netz import holen, text
from .basis import Quelle

BASIS = "https://cdn.cboe.com/data/europe/equities/delayed_data/"
SEGMENTE = {"BATE": "BXE (UK, 5 Stufen)", "CHIX": "CXE (UK, 5 Stufen)", "CEUX": "DXE (EU, bester Kurs)"}
ORDNER = {"pretrade": "pretrade", "posttrade": "trade"}


class CboeEurope(Quelle):
    def __init__(self):
        super().__init__(
            name="cboe", titel="Cboe Europe (BXE, CXE, DXE)", region="EU/UK",
            inhalt="Orderbuch und Abschlüsse, rund 2.800 Werte inkl. DAX", tiefe="bis 5 Stufen",
            abrufbar="nur der laufende Tag",
            lizenz="frei abrufbar; mit dem Abruf gilt der Delayed Market Data Disclaimer von Cboe",
            info_url="https://www.cboe.com/markets/eu/equities/transparency-data",
        )

    def dateien(self, art: str, tag: datetime | None = None) -> list[str]:
        """Minutendateien von 07:00 UTC bis jetzt minus 15 Minuten, für alle Segmente."""
        jetzt = datetime.now(timezone.utc)
        tag = tag or jetzt
        t = tag.replace(hour=7, minute=0, second=0, microsecond=0)
        ende = min(jetzt - timedelta(minutes=16), tag.replace(hour=16, minute=40))
        namen = []
        while t <= ende:
            for seg in SEGMENTE:
                o = ORDNER[art]
                namen.append(f"eqt_{o}_{seg}/c20282023c/minute/eqt_{o}_{seg}_{t:%Y%m%d_%H%M}.csv")
            t += timedelta(minutes=1)
        return namen[::-1]

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        return self.umwandeln(text(holen(BASIS + datei)), art)

    def umwandeln(self, zeilen: list[str], art: str) -> Iterator[Ereignis]:
        for f in csv.reader(zeilen):
            if not f or not f[0][:1].isdigit():
                continue                                   # Kopfzeile der ersten Tagesdatei
            if art == "posttrade" and len(f) >= 11:
                yield Trade(self.name, f[6], f[1], ns_aus_iso(f[0]), float(f[2]), float(f[5]), f[3],
                            ns_aus_iso(f[8]), f[10], {"system": f[7], "flags": f[11] if len(f) > 11 else ""})
            elif len(f) == 14:                             # Börsenbuch, eine Preisstufe pro Zeile
                yield BuchStufe(self.name, f[10], f[1], ns_aus_iso(f[0]), KAUF if f[3] == "BUYL" else VERKAUF,
                                float(f[4]), float(f[7]), int(float(f[9] or 0)),
                                "regulaer" if f[11] == "CLOB" else f[11].lower(), ns_aus_iso(f[13]))
            elif len(f) == 9:                              # Quote eines Systematischen Internalisierers
                kauf = f[3] == "BUYL"
                p, m = float(f[4]), float(f[6])
                yield Quote(self.name, f[7], f[1], ns_aus_iso(f[0]), p if kauf else None, m if kauf else None,
                            None if kauf else p, None if kauf else m, f[5], ns_aus_iso(f[8]), {"si": True})


CBOE = CboeEurope()
