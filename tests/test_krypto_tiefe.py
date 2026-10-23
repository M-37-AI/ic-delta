"""Krypto-Buchtiefe: Bänder, Mittel je Minute, Ablage in buch_tiefe und Lesen für die Heatmap."""
import pathlib
import sqlite3
import tempfile
import threading
import unittest

from marktdaten.analysen import heatmap as H, lesen
from marktdaten.live import krypto_tiefe as K
from marktdaten.live.speicher import Speicher


class Zustand:
    def __init__(self, speicher):
        self.speicher, self.lock, self.werte = speicher, threading.Lock(), {}

    def melden(self, *a, **k):
        pass


def buch(mitte, wand=None):
    """Bids/Asks im Abstand 1, Menge 1; optional eine Wand (seite, preis, menge)."""
    b = [[str(mitte - 0.5 - i), "1"] for i in range(60)]
    a = [[str(mitte + 0.5 + i), "1"] for i in range(60)]
    if wand:
        seite, preis, menge = wand
        ziel = b if seite == "B" else a
        ziel.append([str(preis + 0.25), str(menge)])
    return {"bids": b, "asks": a}


class Baender(unittest.TestCase):
    def test_bandbreite(self):
        self.assertEqual(K.bandbreite(82900), 10)
        self.assertEqual(K.bandbreite(4000), 0.5)
        self.assertEqual(K.bandbreite(150), 0.02)

    def test_baender_ohne_angeschnittenes(self):
        b = K.baender([(105.0, 1), (104.0, 1), (99.0, 2), (95.0, 3)], 10, "B")
        self.assertEqual(b, {100.0: 2})                          # Band 90 (äußerstes) fällt weg
        a = K.baender([(105.0, 1), (111.0, 2)], 10, "A")
        self.assertEqual(a, {100.0: 1})


class Sammler(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.sp = Speicher(pathlib.Path(self.ordner.name))
        self.z = Zustand(self.sp)

    def tearDown(self):
        self.ordner.cleanup()

    def test_minute_mitteln_und_schreiben(self):
        antworten = iter([buch(1000), buch(1000, ("B", 990, 41)), buch(1000)])
        k = K.KryptoTiefe(self.z, abruf=lambda paar: next(antworten))
        k.abtasten("TESTUSDT", 120.0)
        k.abtasten("TESTUSDT", 150.0)
        self.assertEqual(self.sp._x("SELECT COUNT(*) FROM buch_tiefe")[0][0], 0)   # Minute noch offen
        k.abtasten("TESTUSDT", 185.0)                            # neue Minute: die alte wird geschrieben
        zeilen = {(s, p): (m, b) for s, p, m, b in self.sp._x("SELECT seite, preis, menge, band FROM buch_tiefe WHERE t = 120")}
        # Band 0,1 (1 Basispunkt von 1.000): jede Stufe ein eigenes Band; die Wand (990,25) liegt im Band 990,2
        # und war in einer von zwei Abtastungen da -> Mittel 20,5; normale Stufen haben Menge 1
        self.assertEqual(zeilen[("B", 990.2)], (20.5, 0.1))
        self.assertEqual(zeilen[("B", 999.5)], (1.0, 0.1))
        self.assertNotIn(("B", 940.5), zeilen)                   # äußerstes Band fällt weg
        self.assertEqual(min(p for s, p in zeilen if s == "B"), 941.5)
        self.assertEqual(self.sp.einstellungen()["tage_tiefe"], 30)

    def test_heatmap_liest_krypto(self):
        sp = self.sp
        sp.hinzufuegen("TESTUSDT", "TESTUSDT", "Test", "KRYPTO")
        zeilen = []
        for i in range(6):                                       # 6 Minuten, Kaufwand bei 990 in den Minuten 0-3
            t = 600 + 60 * i
            for p in range(991, 1000):
                zeilen.append(("TESTUSDT", t, "B", float(p), 1.0, 1.0))
            for p in range(1001, 1010):
                zeilen.append(("TESTUSDT", t, "A", float(p), 1.0, 1.0))
            zeilen.append(("TESTUSDT", t, "B", 990.0, 50.0 if i < 4 else 1.0, 1.0))
        sp.tiefe(zeilen)
        sp.kerzen("kerzen_1m", "TESTUSDT", [[600 + 60 * i, 1000, 1001, 999, 1000, 1] for i in range(6)])
        alt = lesen.DATEN
        try:
            lesen.DATEN = pathlib.Path(self.ordner.name)
            d = H.heatmap("TESTUSDT", 600, 1000, 60)
        finally:
            lesen.DATEN = alt
        self.assertEqual(d["tick"], 1.0)
        self.assertEqual(len(d["spalten"]), 6)
        self.assertIn((990.5, 50.0), d["spalten"][0]["b"])        # Bandmitte
        self.assertEqual(len(d["waende"]), 1)
        w = d["waende"][0]
        self.assertEqual((w["seite"], w["preis"], w["ende"]), ("kauf", 990.5, "zurückgezogen"))
        self.assertTrue(w["dauer_s"] >= 120)


if __name__ == "__main__":
    unittest.main()
