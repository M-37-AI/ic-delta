"""Liquiditäts-Heatmap (analysen/heatmap.py): Spalten je Zeitfach und Wände mit ihrem Ende, aus erfundenen Buchmeldungen."""
import unittest

from marktdaten.analysen import heatmap as H
from marktdaten.modell import BuchSeite, Trade

ISIN, NS = "DE0000000001", 1_000_000_000


def buch(s, kauf, verkauf):
    return [(s * NS, BuchSeite("xetra", "XETR", ISIN, s * NS, "B", kauf), None),
            (s * NS, BuchSeite("xetra", "XETR", ISIN, s * NS, "A", verkauf), None)]


def stufen(start, schritt, wand=None):
    return [(round(start + i * schritt, 2), (wand[1] if wand and abs(start + i * schritt - wand[0]) < 1e-9 else 100.0), 1) for i in range(5)]


class Heatmap(unittest.TestCase):
    def setUp(self):
        self.ev = []
        for s in range(0, 120):
            verkauf_wand = (100.15, 2000.0) if s < 60 else None   # Verkaufswand bis 59, dann weg
            if 30 <= s < 33:
                verkauf_wand = None                                   # kurze Unterbrechung: zählt als dieselbe Wand
            kauf_wand = (99.85, 2000.0) if s < 90 else None
            self.ev += buch(s, stufen(99.95, -0.05, kauf_wand), stufen(100.05, 0.05, verkauf_wand))
        for s in range(80, 90):                                       # an der Kaufwand wird gehandelt
            self.ev.append((s * NS, Trade("xetra", "XETR", ISIN, s * NS, 99.85, 150.0), None))
        self.ev.sort(key=lambda x: (x[0], 0 if isinstance(x[1], Trade) else 1))

    def test_spalten(self):
        e = H._rechnen(self.ev, ISIN, 15)
        self.assertEqual(e["tick"], 0.05)
        self.assertEqual(len(e["spalten"]), 8)
        erste = e["spalten"][0]
        self.assertEqual(erste["t"], 0)
        self.assertIn((100.15, 2000.0), erste["a"])                 # Mittel über die 15 Sekunden des Fachs
        zweite = e["spalten"][2]                                       # 30-44 s: 3 s ohne Wand (100), 12 s mit (2000)
        self.assertIn((100.15, 1620.0), zweite["a"])

    def test_waende(self):
        w = {x["seite"]: x for x in H._rechnen(self.ev, ISIN, 15)["waende"]}
        self.assertEqual(w["verkauf"]["ende"], "zurückgezogen")
        self.assertTrue(w["verkauf"]["nah"])
        self.assertEqual(w["verkauf"]["abstand_ticks"], 2)
        self.assertEqual(w["verkauf"]["unterbrechungen"], 1)
        self.assertEqual(w["verkauf"]["von"], 0)
        self.assertEqual(w["kauf"]["ende"], "abgearbeitet")
        self.assertEqual(w["kauf"]["gehandelt"], 1500.0)


if __name__ == "__main__":
    unittest.main()
