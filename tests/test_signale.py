"""Signalmodul: konstruierte Sekundenraster, keine Börsendaten."""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from marktdaten.analysen import signale   # noqa: E402

T0 = 1_800_000_000
KAL = {"obi5_p80": 0.3, "delta30_p90": 100.0, "vpin_p90": 0.9, "kauf60_p90": 500.0, "verkauf60_p90": 500.0, "tiefe_hinten_med": 400.0,
       "spread_p90": 5.0, "delta300_p50": 50.0, "tage": 1}


def zeile(t, bid=100.0, ask=100.02, b=None, a=None, ofi5=0.0, kauf=0.0, verkauf=0.0, n=0):
    b = b or [100.0, 100.0, 100.0, 100.0, 100.0]
    a = a or [100.0, 100.0, 100.0, 100.0, 100.0]
    return {"t": t, "bid": bid, "ask": ask, "b": b, "a": a, "bp": [round(bid - 0.01 * k, 2) for k in range(5)], "ap": [round(ask + 0.01 * k, 2) for k in range(5)],
            "b_tief": None, "a_tief": None, "ofi": ofi5, "ofi5": ofi5, "updates": 1, "kauf": kauf, "verkauf": verkauf, "n": n,
            "letzter": (bid + ask) / 2 if kauf + verkauf else None}


def ruhig(anzahl, **k):
    return [zeile(T0 + i, **k) for i in range(anzahl)]


class Features(unittest.TestCase):
    def test_ofi_z_spitze(self):
        r = ruhig(700, kauf=1, verkauf=1, n=2)
        for i in range(0, 700, 7):
            r[i]["ofi5"] = 5.0 if i % 2 else -5.0          # etwas Streuung
        r[-1]["ofi5"] = 400.0
        F = signale.features(r, "DE")
        self.assertGreater(F["ofi_z"][-1], 3.0)
        self.assertLess(abs(F["ofi_z"][300]), 1.5)
        self.assertAlmostEqual(F["spread_bp"][0], 0.02 / 100.01 * 1e4, places=3)
        self.assertEqual(F["cvd"][-1], 0.0)

    def test_obi_und_microprice(self):
        r = [zeile(T0, b=[300, 100, 100, 100, 100], a=[100, 100, 100, 100, 100])] * 2
        F = signale.features(r, "DE")
        self.assertGreater(F["obi5"][0], 0)
        self.assertGreater(F["micro_bp"][0], 0)          # schwere Kaufseite zieht den Microprice nach oben

    def test_vpin_einseitig(self):
        r = ruhig(600, kauf=10, verkauf=0, n=1)
        F = signale.features(r, "DE")
        self.assertGreater(F["vpin"][-1], 0.9)


class Regeln(unittest.TestCase):
    def test_absorption_am_tief(self):
        r = ruhig(400, kauf=1, verkauf=1, n=2)
        for i in range(360, 400):                        # 40 s lang viel Verkaufsdruck, Kurs steht
            r[i]["verkauf"], r[i]["n"] = 20.0, 5
        F = signale.features(r, "DE")
        s = signale.regeln(r, F, KAL, [], 0.01, "DE")
        arten = [(x["art"], x["richtung"]) for x in s]
        self.assertIn(("absorption", 1), arten)
        a = next(x for x in s if x["art"] == "absorption")
        self.assertIn("Verkäufer", a["begruendung"])
        self.assertTrue(a["invalidierung"])

    def test_wand_abprall(self):
        r = ruhig(200, kauf=1, verkauf=1, n=1)
        waende = [{"seite": "kauf", "preis": 99.99, "von": T0 + 10, "bis": T0 + 199, "ende_t": T0 + 199, "ende": "steht", "menge": 5000.0, "dauer_s": 189, "nah": False}]
        F = signale.features(r, "DE")
        s = signale.regeln(r, F, KAL, waende, 0.01, "DE")
        w = [x for x in s if x["art"] == "wand_abprall"]
        self.assertEqual(len(w), 1)                        # je Wand nur einmal
        self.assertEqual(w[0]["richtung"], 1)
        self.assertGreaterEqual(w[0]["t"], T0 + 30)

    def test_fake_wand(self):
        r = ruhig(200, kauf=1, verkauf=1, n=1)
        waende = [{"seite": "verkauf", "preis": 100.03, "von": T0 + 10, "bis": T0 + 100, "ende_t": T0 + 100, "ende": "zurückgezogen", "menge": 5000.0,
                   "dauer_s": 90, "nah": True, "gehandelt": 0.0, "abstand_ticks": 1}]
        F = signale.features(r, "DE")
        s = signale.regeln(r, F, KAL, waende, 0.01, "DE")
        f = [x for x in s if x["art"] == "fake_wand"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["richtung"], 1)              # Verkaufswand weg: Weg nach oben frei
        self.assertEqual(f[0]["t"], T0 + 100)

    def test_entprellung_imbalance(self):
        r = ruhig(300, kauf=1, verkauf=1, n=2)
        for i in (250, 255, 260):                          # dreimal binnen 10 s dieselbe Lage
            r[i].update(b=[900, 100, 100, 100, 100], ofi5=800.0)
        F = signale.features(r, "DE")
        s = [x for x in signale.regeln(r, F, KAL, [], 0.01, "DE") if x["art"] == "imbalance_momentum"]
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["richtung"], 1)


class Markout(unittest.TestCase):
    def test_markout_und_netto(self):
        t = [T0 + i for i in range(400)]
        mitte = [100.0 + (0.1 if i >= 30 else 0.0) + (0.2 if i >= 60 else 0.0) for i in range(400)]   # +10 bp nach 30 s, +30 bp nach 60 s
        mo = signale.markout({"t": T0, "richtung": 1}, t, mitte, 2.0, 10.0, T0 + 399)
        self.assertAlmostEqual(mo["m30"], 10.0, places=1)
        self.assertAlmostEqual(mo["m60"], 30.0, places=1)
        self.assertAlmostEqual(mo["netto60"], 30.0 - 1.0 - 10.0, places=1)
        self.assertIsNone(signale.markout({"t": T0 + 200, "richtung": -1}, t, mitte, 2.0, 0.0, T0 + 399)["m300"])   # noch nicht fällig
        gegen = signale.markout({"t": T0, "richtung": -1}, t, mitte, 2.0, 0.0, T0 + 399)
        self.assertAlmostEqual(gegen["m30"], -10.0, places=1)

    def test_journal_entprellt_gegen_fruehere_laeufe(self):
        with tempfile.TemporaryDirectory() as d:
            alt_ort, alt_db = signale.ORT, signale.DB
            signale.ORT, signale.DB = pathlib.Path(d), pathlib.Path(d) / "signale.db"
            try:
                r = ruhig(400, kauf=1, verkauf=1, n=1)
                F = signale.features(r, "DE")
                s1 = signale._sig("absorption", 1, 0.7, T0 + 50, 100.01, "a", "b", 300)
                s2 = signale._sig("absorption", 1, 0.7, T0 + 150, 100.01, "a", "b", 300)   # binnen Horizont: fällt weg
                s3 = signale._sig("absorption", 1, 0.7, T0 + 50, 100.01, "a", "b", 300)   # Wiederholung desselben: fällt weg
                zeilen = signale.journal_schreiben("TEST", "DE", [s1], F, T0 + 399)
                zeilen = signale.journal_schreiben("TEST", "DE", [s2, s3], F, T0 + 399)
                self.assertEqual(len(zeilen), 1)
                self.assertIsNotNone(zeilen[0]["markout"])
                self.assertAlmostEqual(zeilen[0]["markout"]["m30"], 0.0)
                st = signale.statistik("TEST")
                self.assertEqual(st["gesamt"][0]["n"], 1)
            finally:
                signale.ORT, signale.DB = alt_ort, alt_db


if __name__ == "__main__":
    unittest.main()
