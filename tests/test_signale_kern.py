"""Kern der Signal-Schicht: Orderflow je Sekunde (buch_1s), Futures-Kontext (liquidationen, perp_1m), Speicher."""
import pathlib
import tempfile
import threading
import unittest

from marktdaten.live import krypto_flow as F
from marktdaten.live import perp as P
from marktdaten.live.speicher import Speicher


def buch(bid=100.0, ask=101.0, bq=1.0, aq=1.0, stufen=20):
    bids = [(bid - i, bq if i == 0 else 2.0) for i in range(stufen)]
    asks = [(ask + i, aq if i == 0 else 2.0) for i in range(stufen)]
    return bids, asks


class OFI(unittest.TestCase):
    def test_gleicher_preis_mehr_menge_am_bid(self):
        alt, neu = buch(bq=1.0), buch(bq=3.0)
        e1, e5 = F.ofi_schritt(alt, neu)
        self.assertAlmostEqual(e1, 2.0)           # +3 (bid gleich, neue Menge) −1 (alte Menge)
        self.assertAlmostEqual(e5, 2.0)           # übrige Stufen unverändert: je 0

    def test_bid_steigt_ask_steigt(self):
        alt = buch(bid=100.0, ask=101.0, bq=1.0, aq=4.0)
        neu = buch(bid=100.5, ask=101.5, bq=2.0, aq=5.0)
        e1, _ = F.ofi_schritt(alt, neu)
        # bid rauf: +bq_neu = 2; ask rauf: +aq_alt = 4
        self.assertAlmostEqual(e1, 6.0)

    def test_ask_faellt_bid_faellt(self):
        alt = buch(bid=100.0, ask=101.0, bq=3.0, aq=1.0)
        neu = buch(bid=99.5, ask=100.5, bq=1.0, aq=2.0)
        e1, _ = F.ofi_schritt(alt, neu)
        # bid runter: −bq_alt = −3; ask runter: −aq_neu = −2
        self.assertAlmostEqual(e1, -5.0)

    def test_ofi5_summiert_stufen(self):
        alt = buch()
        bids, asks = buch()
        bids[2] = (bids[2][0], 7.0)               # dritte Kaufstufe wächst um 5
        e1, e5 = F.ofi_schritt(alt, (bids, asks))
        self.assertAlmostEqual(e1, 0.0)
        self.assertAlmostEqual(e5, 5.0)


class Sekunden(unittest.TestCase):
    def test_zeile_je_sekunde_mit_trades_und_luecke(self):
        f = F.Flow()
        f.buch("X", 100, *buch(bq=1.0))
        f.buch("X", 100, *buch(bq=2.0))                     # OFI +1 in Sekunde 100
        f.trade("X", 100, 100.5, 0.4, kauf=True)
        f.trade("X", 100, 100.4, 0.1, kauf=False)
        f.trade("X", 103, 100.6, 0.2, kauf=True)            # Sekunden 101 und 102 ohne Ereignis
        zeilen = f.abschliessen(104)
        self.assertEqual([z[1] for z in zeilen], [100, 101, 102, 103])
        z = dict(zip(F.SPALTEN, zeilen[0]))
        self.assertEqual((z["id"], z["bid"], z["ask"], z["b1"], z["a1"]), ("X", 100.0, 101.0, 2.0, 1.0))
        self.assertAlmostEqual(z["b_tief"], 15 * 2.0)       # Stufen 6-20
        self.assertAlmostEqual(z["ofi"], 1.0)
        self.assertEqual(z["updates"], 2)
        self.assertEqual((z["kauf"], z["verkauf"], z["n"], z["letzter"]), (0.4, 0.1, 2, 100.4))
        l = dict(zip(F.SPALTEN, zeilen[1]))                 # Lücke: letzter Zustand, nichts passiert
        self.assertEqual((l["ofi"], l["updates"], l["kauf"], l["n"], l["b1"], l["letzter"]), (0.0, 0, 0.0, 0, 2.0, 100.4))
        d = dict(zip(F.SPALTEN, zeilen[3]))
        self.assertEqual((d["kauf"], d["n"], d["letzter"]), (0.2, 1, 100.6))
        self.assertEqual(f.abschliessen(104), [])           # nichts doppelt

    def test_aggtrade_seite(self):
        f = F.Flow()
        f.buch("X", 10, *buch())
        f.trade("X", 10, 100.5, 1.0, kauf=not True)         # m = true: Käufer war Maker -> aggressiver Verkauf
        f.trade("X", 10, 100.5, 2.0, kauf=not False)
        z = dict(zip(F.SPALTEN, f.abschliessen(11)[0]))
        self.assertEqual((z["kauf"], z["verkauf"]), (2.0, 1.0))

    def test_altes_buch_fuellt_nicht_auf(self):
        f = F.Flow()
        f.buch("X", 0, *buch())
        zeilen = f.abschliessen(100)
        self.assertEqual([z[1] for z in zeilen], list(range(0, F.FRISCH + 1)))
        f.buch("X", 200, *buch(bq=5.0))                     # nach langer Pause: kein OFI gegen das alte Buch
        z = dict(zip(F.SPALTEN, f.abschliessen(201)[0]))
        self.assertEqual((z["t"], z["ofi"], z["b1"]), (200, 0.0, 5.0))

    def test_ohne_buch_keine_zeile(self):
        f = F.Flow()
        f.trade("X", 5, 1.0, 1.0, kauf=True)
        self.assertEqual(f.abschliessen(10), [])


class Futures(unittest.TestCase):
    def test_liquidation(self):
        d = {"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "SELL", "p": "9910", "q": "0.014", "T": 1568014460893}}
        self.assertEqual(P.liquidation_aus(d), ("BTCUSDT", 1568014460893, "long", 9910.0, 0.014))
        d["o"]["S"] = "BUY"
        self.assertEqual(P.liquidation_aus(d)[2], "short")
        self.assertIsNone(P.liquidation_aus({"o": {}}))

    def test_markpreis_und_minuten(self):
        d = {"e": "markPriceUpdate", "E": 1562306400123, "s": "BTCUSDT", "p": "100", "i": "99", "r": "0.0001", "T": 1}
        self.assertEqual(P.markpreis_aus(d), ("BTCUSDT", 1562306400, 100.0, 99.0, 0.0001))
        m = P.Minuten()
        m.markpreis("BTCUSDT", 600, 100.0, 99.0, 0.0001)
        m.markpreis("BTCUSDT", 630, 102.0, None, 0.0002)
        m.oi("BTCUSDT", 640, 5000.0)
        self.assertEqual(m.abschliessen(650), [])           # Minute 600 läuft noch
        m.markpreis("BTCUSDT", 661, 110.0, 100.0, None)     # neue Minute schließt die alte
        zeilen = m.abschliessen(662)
        self.assertEqual(zeilen, [("BTCUSDT", 600, 101.0, 99.0, 0.0002, 5000.0)])
        zeilen = m.abschliessen(730)
        self.assertEqual(zeilen, [("BTCUSDT", 660, 110.0, 100.0, None, 5000.0)])   # OI wird mitgenommen


class Zustand:
    def __init__(self, speicher):
        self.speicher, self.lock, self.werte, self.beobachter = speicher, threading.Lock(), {}, []

    def melden(self, *a, **k):
        pass


class SpeicherTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.sp = Speicher(pathlib.Path(self.ordner.name))

    def tearDown(self):
        self.ordner.cleanup()

    def test_tabellen_schreiben_und_aufraeumen(self):
        f = F.Flow()
        f.buch("BTCUSDT", 1000, *buch())
        self.sp.buch_1s(f.abschliessen(1003))
        self.sp.liquidationen([("BTCUSDT", 1000500, "long", 100.0, 1.0), ("BTCUSDT", 1000500, "long", 100.0, 1.0)])
        self.sp.perp([("BTCUSDT", 960, 100.0, 99.0, 0.0001, 5000.0)])
        self.assertEqual(self.sp._x("SELECT COUNT(*) FROM buch_1s")[0][0], 3)
        self.assertEqual(self.sp._x("SELECT COUNT(*) FROM liquidationen")[0][0], 1)
        self.assertEqual(self.sp._x("SELECT t, ofi, n FROM buch_1s ORDER BY t")[0], (1000, 0.0, 0))
        e = self.sp.einstellungen()
        self.assertEqual((e["tage_1s"], e["tage_perp"]), (14, 14))
        g = self.sp.groesse()
        arten = {d["art"]: d["zeilen"] for d in g["datenarten"]}
        self.assertEqual((arten["buch_1s"], arten["liquidationen"], arten["perp_1m"]), (3, 1, 1))
        self.assertEqual(g["je_wert"]["BTCUSDT"]["buch_1s"], 3)
        probe = self.sp.aufraeumen(probe=True)
        schritte = {s["was"]: s["anzahl"] for s in probe["schritte"]}
        self.assertEqual((schritte["buch_1s"], schritte["perp"]), (3, 2))     # alles uralt (1970)
        self.sp.aufraeumen()
        self.assertEqual(self.sp._x("SELECT COUNT(*) FROM buch_1s")[0][0], 0)
        self.assertEqual(self.sp._x("SELECT COUNT(*) FROM perp_1m")[0][0], 0)

    def test_perp_kontext_schreibt(self):
        z = Zustand(self.sp)
        k = P.PerpKontext(z)
        k.liqu.append(("BTCUSDT", 5000, "short", 1.0, 2.0))
        k.minuten.markpreis("BTCUSDT", 0, 1.0, 1.0, None)
        k.schreiben(120)
        self.assertEqual(self.sp._x("SELECT seite FROM liquidationen")[0][0], "short")
        self.assertEqual(self.sp._x("SELECT mark FROM perp_1m")[0][0], 1.0)


if __name__ == "__main__":
    unittest.main()
