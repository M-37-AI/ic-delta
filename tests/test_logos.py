"""Logos: eigene Datei vor geholter, nur echte Bilder speichern, Fehlschläge merken."""
import pathlib
import tempfile
import unittest

from marktdaten.live import logos
from marktdaten.live.speicher import Speicher
from marktdaten.live.zustand import Wert, Zustand

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


class Logos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.alt = (logos.ORDNER, logos.EIGEN)
        logos.ORDNER, logos.EIGEN = t / "logos", t / "logos" / "eigen"
        self.z = Zustand(Speicher(t / "db"))
        for w in (Wert("DE0007164600", "SAP", "SAP", "DE"), Wert("US0378331005", "AAPL", "Apple", "US"), Wert("BTCUSDT", "BTCUSDT", "BTC / USDT", "KRYPTO")):
            self.z.hinzufuegen(w, aus_speicher=False)
        self.l = logos.Logos(self.z)

    def tearDown(self):
        logos.ORDNER, logos.EIGEN = self.alt
        self.tmp.cleanup()

    def test_holen_speichern_und_fehlschlag_merken(self):
        antworten = {"US": (PNG, ".png"), "KRYPTO": (b"<html>nicht gefunden</html>", ".svg"), "DE": None}
        self.l.holen = lambda w: antworten[w.markt]
        self.l.runde()
        self.assertIsNotNone(logos.datei("US0378331005"))
        self.assertIsNone(logos.datei("BTCUSDT"))                        # keine SVG: nicht gespeichert
        stand = self.l._stand()
        self.assertFalse(stand["BTCUSDT"]["ok"])
        self.assertNotIn("DE0007164600", stand)                          # ohne Quelle: nichts gemerkt, später wieder prüfen
        aufrufe = []
        self.l.holen = lambda w: aufrufe.append(w.symbol)
        self.l.runde()
        self.assertEqual(aufrufe, ["SAP"])                                # Fehlschlag wartet sieben Tage, vorhandenes wird nicht neu geholt

    def test_eigene_datei_hat_vorrang(self):
        logos.EIGEN.mkdir(parents=True)
        (logos.EIGEN / "SAP.svg").write_text("<svg></svg>")
        self.assertEqual(logos.datei("DE0007164600", "SAP").name, "SAP.svg")
        self.assertEqual(logos.uebersicht(self.z)["symbol"]["SAP"].split("?")[0], "/logo/DE0007164600")


if __name__ == "__main__":
    unittest.main()
