"""Alarme mit Attrappen für Kerzen und Telegram: Linie, Zone, Kurs, keine Meldungen aus der Vergangenheit."""
import pathlib
import tempfile
import unittest

from marktdaten.analysen import alarme, lesen
from marktdaten.analysen.formationen import erkennen


class Alarme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ort = pathlib.Path(self.tmp.name)
        self.alt = (alarme.ORT, alarme.REGELN, alarme.VERLAUF, alarme.telegram_senden, lesen.kerzen, lesen.letzter_kurs, lesen.watchlist)
        alarme.ORT, alarme.REGELN, alarme.VERLAUF = ort, ort / "alarme.json", ort / "verlauf.jsonl"
        self.gesendet = []
        alarme.telegram_senden = lambda text: self.gesendet.append(text)
        lesen.watchlist = lambda: [{"id": "ZZ0000000011", "symbol": "MUST", "name": "Muster", "markt": "DE"}]
        self.k = [[1_000_000 + i * 900, 100, 101, 99, 100, 10] for i in range(50)]   # 15-Minuten-Kerzen, seitwärts um 100
        lesen.kerzen = lambda wert, tf, anzahl=400: [list(x) for x in self.k]
        self.kurs = (self.k[-1][0], 100.0)
        lesen.letzter_kurs = lambda wert: self.kurs
        self.a = alarme.Alarme()

    def tearDown(self):
        (alarme.ORT, alarme.REGELN, alarme.VERLAUF, alarme.telegram_senden, lesen.kerzen, lesen.letzter_kurs, lesen.watchlist) = self.alt
        self.tmp.cleanup()

    def kerze(self, schluss):
        t = self.k[-1][0] + 900
        self.k.append([t, schluss, max(schluss, 100) + 0.5, min(schluss, 100) - 0.5, schluss, 10])

    def test_linie_bricht_nach_oben(self):
        self.a.anlegen({"wert": "MUST", "art": "linie", "tf": "15m", "t1": self.k[0][0], "p1": 102, "t2": self.k[-1][0], "p2": 102, "richtung": "ueber"})
        self.a.runde()
        self.assertEqual(self.gesendet, [])                                  # nichts Neues
        self.kerze(101.5)
        self.a.runde()
        self.assertEqual(self.gesendet, [])                                  # noch unter der Linie
        self.kerze(103)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 1)
        self.assertIn("nach oben gebrochen", self.gesendet[0])
        self.assertFalse(self.a.liste()[0]["aktiv"])                         # einmalig
        self.kerze(104)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 1)

    def test_zone_nur_gewaehlte_richtung(self):
        self.a.anlegen({"wert": "ZZ0000000011", "art": "zone", "tf": "15m", "lo": 98, "hi": 102, "richtung": "unter"})
        self.kerze(103)
        self.a.runde()
        self.assertEqual(self.gesendet, [])                                  # oben raus: nicht gewünscht
        self.kerze(100)
        self.kerze(97)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 1)
        self.assertIn("nach unten verlassen", self.gesendet[0])

    def test_kurs(self):
        self.a.anlegen({"wert": "MUST", "art": "preis", "preis": 100.5, "richtung": "ueber"})
        self.kurs = (self.kurs[0] + 60, 100.4)
        self.a.runde()
        self.assertEqual(self.gesendet, [])
        self.kurs = (self.kurs[0] + 60, 100.6)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 1)
        self.assertEqual(len(self.a.verlauf()), 1)

    def test_formation_nicht_aus_der_vergangenheit(self):
        self.a.anlegen({"wert": "MUST", "art": "formation", "tf": "15m", "arten": ["hoch_100", "engulfing_bull"]})
        self.a.runde()
        self.assertEqual(self.gesendet, [])

    def test_unbekannter_wert(self):
        with self.assertRaises(ValueError):
            self.a.anlegen({"wert": "XYZ", "art": "preis", "preis": 1})


class Formationen(unittest.TestCase):
    def test_doppeltief(self):
        # zwei Tiefs bei 90, Hoch dazwischen bei 100, dann Ausbruch über 100
        verlauf = [100 - i for i in range(10)] + [90 + i for i in range(11)] + [100 - i for i in range(11)] + [90 + i * 1.2 for i in range(12)]
        k = [[i * 60, c, c + 0.3, c - 0.3, c, 1] for i, c in enumerate(verlauf)]
        arten = {f["art"]: f for f in erkennen(k, 3)}
        self.assertIn("doppeltief", arten)
        self.assertEqual(arten["doppeltief"]["status"], "bestätigt")


if __name__ == "__main__":
    unittest.main()
