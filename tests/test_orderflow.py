"""Orderflow für iC Charts: Tick-Regel, Minutenwerte, Volumenprofil, nur der Hauptplatz zählt."""
import pathlib
import tempfile
import unittest

from marktdaten.live.orderflow import _schritt, orderflow
from marktdaten.live.speicher import Speicher
from marktdaten.live.zustand import Wert

T0 = 1_792_483_200 * 10**9          # Dienstag, 20.10.2026, 10:00 Uhr in Berlin (fortlaufender Handel)
ISIN = "ZZ0000000011"


class Orderflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sp = Speicher(pathlib.Path(self.tmp.name))
        self.w = Wert(ISIN, "TST", "Test AG", "DE")
        m = 60 * 10**9
        self.sp.abschluesse([
            (ISIN, T0 + 1, "XETA", 10.00, 100),        # erster Abschluss: neutral
            (ISIN, T0 + 2, "XETA", 10.00, 50),         # kein Preiswechsel: weiter neutral
            (ISIN, T0 + 3, "XETA", 10.02, 30),         # Aufwärts-Tick: Kauf
            (ISIN, T0 + 4, "XETA", 10.02, 20),         # gleich: wie vorher (Kauf)
            (ISIN, T0 + m + 1, "XETA", 10.01, 40),     # Abwärts-Tick: Verkauf (nächste Minute)
            (ISIN, T0 + m + 2, "XGRM", 99.0, 1000),    # Tradegate: zählt nicht (anderer Platz)
        ])

    def tearDown(self):
        self.sp.db.close()
        self.tmp.cleanup()

    def test_minuten_tick_regel(self):
        d = orderflow(self.sp, self.w)
        self.assertEqual(d["minuten"], [[T0 // 10**9, 50.0, 0.0], [T0 // 10**9 + 60, 0.0, 40.0]])
        self.assertEqual(d["anzahl"], 5)
        self.assertIn("Tick-Regel", d["hinweis"])

    def test_profil(self):
        d = orderflow(self.sp, self.w, profil=True, von=T0 // 10**9, bis=T0 // 10**9 + 120, stufen=4)
        p = d["profil"]
        self.assertEqual(p["anzahl"], 5)
        gesamt = sum(k + v + n for _, k, v, n in p["levels"])
        self.assertAlmostEqual(gesamt, 240)
        unten = p["levels"][0]
        self.assertAlmostEqual(unten[3], 150)                # die beiden neutralen Abschlüsse bei 10,00

    def test_runde_stufen(self):
        self.assertEqual(_schritt(1.0, 40), 0.025)
        self.assertEqual(_schritt(37, 40), 1)

    def test_auktionen_neutral(self):
        # 21.10.2026 in Berlin: Eröffnung 9:00 und Schlussauktion 17:35 zählen nicht zum Delta
        tag = 1_792_533_600 * 10**9                        # 21.10.2026 00:00 in Berlin (MESZ) = 20.10. 22:00 UTC
        self.sp.abschluesse([(ISIN, tag + (9 * 3600 + 26) * 10**9, "XETA", 11.0, 500),
                             (ISIN, tag + (9 * 3600 + 60) * 10**9, "XETA", 11.1, 7),
                             (ISIN, tag + (17 * 3600 + 35 * 60) * 10**9, "XETA", 10.9, 9000)])
        m = {t: (k, v) for t, k, v in orderflow(self.sp, self.w)["minuten"]}
        self.assertNotIn((tag // 10**9 + 9 * 3600), m)                    # Eröffnungsauktion: neutral
        self.assertEqual(m[tag // 10**9 + 9 * 3600 + 60], (7.0, 0.0))      # danach fortlaufend: Kauf (Aufwärts-Tick)
        self.assertNotIn(tag // 10**9 + 17 * 3600 + 35 * 60, m)            # Schlussauktion: neutral

    def test_krypto_ohne_abschluesse(self):
        self.assertIsNone(orderflow(self.sp, Wert("BTCUSDT", "BTCUSDT", "Bitcoin", "KRYPTO")))


if __name__ == "__main__":
    unittest.main()
