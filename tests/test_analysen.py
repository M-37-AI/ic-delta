"""Analysen mit gebauten Fällen: Wand zurückgezogen (Spoofing-Muster), Wand abgearbeitet, Eisberg."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from marktdaten.analysen.rechnen import auswerten
from marktdaten.modell import KAUF, VERKAUF, BuchSeite, Trade

TAG = "2026-10-19"
T0 = int(datetime(2026, 10, 19, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()) * 10**9
ISIN = "ZZ0000000011"


def seite(t, s, stufen):
    return (T0 + int(t * 1e9), BuchSeite("xetra", "XETR", ISIN, T0 + int(t * 1e9), s, stufen), None)


def trade(t, preis, menge):
    e = Trade("xetra", "XETA", ISIN, T0 + int(t * 1e9), preis, menge, "EUR", extra={"mmtTradingMode": "2"})
    return (e.ts, e, None)


def bid(top, wand=None, n=5):
    return [(round(top - i * 0.01, 2), 5000.0 if wand == round(top - i * 0.01, 2) else 100.0, 3) for i in range(n)]


def ask(top, wand=None, menge_top=100.0):
    return [(round(top + i * 0.01, 2), 5000.0 if wand == round(top + i * 0.01, 2) else (menge_top if i == 0 else 100.0), 3)
            for i in range(5)]


class Analysen(unittest.TestCase):
    def setUp(self):
        ev = []
        for k in range(100):                                      # ruhiger Hintergrund
            ev += [seite(k * 0.1, KAUF, bid(100.00)), seite(k * 0.1 + 0.05, VERKAUF, ask(100.01))]
        # Kaufwand bei 99.97 (Stufe 4), Kurs fällt auf sie zu, Wand verschwindet ohne Abschluss
        ev += [seite(10, KAUF, bid(100.00, 99.97)), seite(15, KAUF, bid(99.99, 99.97)), seite(20, KAUF, bid(99.98, 99.97)),
               seite(25, KAUF, bid(99.98))]
        # Verkaufswand bei 100.03 wird weggehandelt
        ev += [seite(30, VERKAUF, ask(100.03, 100.03)), trade(39, 100.03, 2500), trade(39.5, 100.03, 2500),
               seite(40, VERKAUF, ask(100.04))]
        # Eisberg bei 100.05: sichtbar 200, gehandelt 2000
        ev.append(seite(50, VERKAUF, ask(100.05, menge_top=200.0)))
        for k in range(10):
            ev += [trade(51 + k, 100.05, 200), seite(51.5 + k, VERKAUF, ask(100.05, menge_top=200.0))]
        ev.append(seite(70, VERKAUF, ask(100.06)))
        ev.sort(key=lambda x: (x[0], 0 if isinstance(x[1], Trade) else 1))
        self.e = auswerten(ev, ISIN, TAG)

    def test_waende(self):
        c = self.e["c"]
        nach_preis = {(w["seite"], w["preis"]): w for w in c["waende"]}
        self.assertEqual(nach_preis[(KAUF, 99.97)]["ergebnis"], "zurückgezogen")
        self.assertTrue(nach_preis[(KAUF, 99.97)]["verdacht"])
        self.assertEqual(nach_preis[(VERKAUF, 100.03)]["ergebnis"], "abgearbeitet")
        self.assertFalse(nach_preis[(VERKAUF, 100.03)]["verdacht"])

    def test_eisberg(self):
        eis = self.e["c"]["eisberge"]
        self.assertTrue(any(x["preis"] == 100.05 and x["gehandelt"] == 2000 and x["seite"] == VERKAUF for x in eis))

    def test_kaeufer(self):
        self.assertEqual(self.e["e"].get("leer"), True)                       # zu wenige Abschlüsse für E
        self.assertGreater(self.e["a"]["spread_bp"], 0)


if __name__ == "__main__":
    unittest.main()


class Lesen(unittest.TestCase):
    def test_keine_doppelten_dateien(self):
        import gzip, json, pathlib, tempfile
        from marktdaten.analysen.lesen import Rohdaten
        with tempfile.TemporaryDirectory() as d:
            tag = pathlib.Path(d) / TAG
            tag.mkdir()
            zeile = json.dumps({"messageId": "posttrade", "instrumentIdentificationCode": ISIN, "price": 1, "quantity": 2,
                                "tradingDateAndTime": "2026-10-19T08:00:00.000000000Z", "mmtTradingMode": "2"})
            namen = {"DETR-posttrade-2026-10-19T08_00.json.gz": [ISIN, "ZZ0000000029"],
                     f"DETR-posttrade-2026-10-19T08_00.json.{ISIN}.gz": [ISIN],        # Einzelablage derselben Minute
                     "DETR-posttrade-2026-10-19T08_01.json.ZZ0000000029.gz": ["ZZ0000000029"]}
            with open(tag / "index.jsonl", "w") as idx:
                for n, isins in namen.items():
                    with gzip.open(tag / n, "wt") as f:
                        f.write(zeile + "\n")
                    idx.write(json.dumps({"datei": n, "isins": isins, "zeilen": 1}) + "\n")
            r = Rohdaten(pathlib.Path(d))
            self.assertEqual([x[0] for x in r.dateien(TAG, ISIN)], ["DETR-posttrade-2026-10-19T08_00.json.gz"])
            self.assertEqual(len(r.ereignisse(ISIN, TAG)), 1)
            self.assertEqual(r.werte(TAG), {ISIN, "ZZ0000000029"})
