"""IEX HIST: Mitschnitte der IEX-Feeds vom Vortag (T+1) als pcapng.gz.

DEEP enthält das Orderbuch nach Preisstufen und alle Trades auf IEX. Eine Tagesdatei hat
rund 12 GB; die Datei wird deshalb als Strom gelesen und nie ganz geladen.
IEX hat nur etwa 4 % des US-Volumens.
Spezifikation: https://www.iexexchange.io/resources/trading/market-data
"""
from __future__ import annotations

import gzip
import struct
import urllib.request
from typing import Iterator

from ..modell import KAUF, VERKAUF, BuchStufe, Ereignis, Trade
from ..netz import USER_AGENT, Zaehler, holen_json
from .basis import Quelle


class Iex(Quelle):
    def __init__(self):
        super().__init__(
            name="iex", titel="IEX HIST (USA, Vortag)", region="US",
            inhalt="Orderbuch nach Preisstufen und Trades, alle IEX-Symbole", tiefe="alle Preisstufen",
            abrufbar="Archiv, je Handelstag", arten=("deep",),
            lizenz="kostenloser Download; Nutzungsbedingungen von IEX beachten",
            info_url="https://www.iex.io/products/equities/market-data-connectivity",
        )

    def dateien(self, art: str = "deep", datum: str = "") -> list[str]:
        """datum als JJJJMMTT; ohne Angabe die Liste aller verfügbaren Tage."""
        url = "https://iextrading.com/api/1.0/hist" + (f"?date={datum}" if datum else "")
        r = holen_json(url)
        if isinstance(r, dict):                          # ohne Datum: {datum: [dateien]}
            r = [x for v in r.values() for x in v]
        return [x["link"] for x in r if x.get("feed") == "DEEP"][::-1]

    def lesen(self, datei: str, art: str = "deep", max_mb: float | None = None) -> Iterator[Ereignis]:
        """Liest den Strom; max_mb begrenzt die Menge an komprimierten Daten (z. B. für einen Test)."""
        antwort = urllib.request.urlopen(urllib.request.Request(datei, headers={"User-Agent": USER_AGENT}), timeout=60)
        zaehler = Zaehler(antwort)
        yield from self.umwandeln(gzip.GzipFile(fileobj=zaehler), lambda: max_mb is not None and zaehler.n > max_mb * 1e6)

    def umwandeln(self, f, genug=lambda: False) -> Iterator[Ereignis]:
        while not genug():
            kopf = f.read(8)                              # pcapng: Blocktyp und Länge
            if len(kopf) < 8:
                return
            typ, laenge = struct.unpack("<II", kopf)
            koerper = f.read(laenge - 8)
            if typ != 6:                                  # nur Enhanced Packet Blocks
                continue
            caplen = struct.unpack("<I", koerper[12:16])[0]
            yield from _segment(koerper[20 + 42:20 + caplen])   # Ethernet + IPv4 + UDP überspringen


def _segment(seg: bytes) -> Iterator[Ereignis]:
    """IEX-TP-Segment: 40 Byte Kopf, dann Nachrichten mit 2 Byte Längenangabe."""
    if len(seg) < 40:
        return
    anzahl = struct.unpack("<H", seg[14:16])[0]
    p = 40
    for _ in range(anzahl):
        ln = struct.unpack("<H", seg[p:p + 2])[0]
        m = seg[p + 2:p + 2 + ln]
        p += 2 + ln
        typ = m[:1]
        if typ in (b"8", b"5", b"T"):
            ts = struct.unpack("<q", m[2:10])[0]
            sym = m[10:18].rstrip(b" ").decode()
            menge, preis = struct.unpack("<Iq", m[18:30])
            if typ == b"T":
                yield Trade("iex", "IEXG", sym, ts, preis / 1e4, menge, "USD",
                            trade_id=str(struct.unpack("<q", m[30:38])[0]), extra={"flags": m[1]})
            else:
                yield BuchStufe("iex", "IEXG", sym, ts, KAUF if typ == b"8" else VERKAUF, preis / 1e4, menge)



IEX = Iex()
