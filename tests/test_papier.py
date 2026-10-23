"""Papierhandel mit eigenem Speicher und Attrappen für Telegram und Agent: Ausführung, Limits, Stopps, FIFO, nur long."""
import pathlib
import tempfile
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from marktdaten.live import papier
from marktdaten.live.speicher import Speicher
from marktdaten.live.zustand import Wert, Zustand


class Papierhandel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.alt = papier._post
        self.gesendet = []
        papier._post = lambda url, daten, timeout: self.gesendet.append((url, daten)) or {"text": "Bewertung", "modell": "attrappe"}
        self.z = Zustand(Speicher(pathlib.Path(self.tmp.name)))
        self.w = Wert("BTCUSDT", "BTCUSDT", "BTC / USDT", "KRYPTO")
        self.z.hinzufuegen(self.w, aus_speicher=False)
        self.p = papier.Papier(self.z)
        self.kurs(100.0)

    def tearDown(self):
        papier._post = self.alt
        self.tmp.cleanup()

    def kurs(self, preis, buch=None):
        """Kurs setzen; buch = (Verkaufsstufen, Kaufstufen) als [(preis, menge)]."""
        self.w.letzter, self.w.zeit = preis, time.time_ns()
        k = ("BINANCE", "BTCUSDT", "regulaer")
        if buch:
            self.z.buch.seiten[k] = {"A": {p: (m, None) for p, m in buch[0]}, "B": {p: (m, None) for p, m in buch[1]}}
        else:
            self.z.buch.seiten.pop(k, None)
            self.w.plaetze["BINANCE"] = {"bid": preis - 0.5, "ask": preis + 0.5}

    def warten(self):
        time.sleep(0.2)                       # Meldungen laufen in einem eigenen Faden

    def test_markt_kauf_durch_das_buch(self):
        self.kurs(100, buch=([(101, 1), (102, 2)], [(99, 5)]))
        o = self.p.aufgeben("BTCUSDT", "kauf", "markt", 2)["order"]
        self.assertEqual(o["status"], "ausgefuehrt")
        self.assertAlmostEqual(o["preis"], 101.5)                  # 1 zu 101, 1 zu 102
        self.assertIn("Orderbuch", o["hinweis"])
        konto = self.p.abrechnung()["konto"]["USD"]
        self.assertAlmostEqual(konto["guthaben"], 50_000 - 203 - 1)

    def test_limit_wartet_und_fuehrt_aus(self):
        o = self.p.aufgeben("BTCUSDT", "kauf", "limit", 1, limit=95)["order"]
        self.assertEqual(o["status"], "offen")
        self.kurs(94)
        self.p.pruefen()
        o = self.p.orders("WHERE nr = ?", (o["nr"],))[0]
        self.assertEqual(o["status"], "ausgefuehrt")
        self.assertLessEqual(o["preis"], 95)

    def test_stopp_verkauf_und_fifo_trade(self):
        self.p.aufgeben("BTCUSDT", "kauf", "markt", 1)              # zu 100,5 (bester Briefkurs)
        self.kurs(110)
        self.p.aufgeben("BTCUSDT", "kauf", "markt", 1)              # zu 110,5
        o = self.p.aufgeben("BTCUSDT", "verkauf", "stopp", 2, stopp=105)["order"]
        self.assertEqual(o["status"], "offen")
        self.kurs(104)
        self.p.pruefen()
        self.warten()
        a = self.p.abrechnung()
        self.assertEqual(a["positionen"], [])
        t = a["trades"][0]
        self.assertEqual(t["menge"], 2)
        self.assertAlmostEqual(t["einstieg"], 105.5)
        self.assertAlmostEqual(t["ausstieg"], 103.5)
        self.assertAlmostEqual(t["ergebnis"], (103.5 - 105.5) * 2 - 3)   # drei Orders zu je 1 $ Gebühr
        self.assertTrue(any("/papier" in u for u, _ in self.gesendet))  # Claude wurde um eine Bewertung gebeten
        self.assertEqual(self.p.journal()[0]["text"], "Bewertung")

    def test_nur_long_und_guthaben(self):
        self.assertIn("fehler", self.p.aufgeben("BTCUSDT", "verkauf", "markt", 1))
        o = self.p.aufgeben("BTCUSDT", "kauf", "markt", 1000)["order"]   # 100.500 $ bei 50.000 $ Guthaben
        self.assertEqual(o["status"], "abgelehnt")
        self.assertIn("Guthaben", o["hinweis"])

    def test_alter_kurs_wartet(self):
        self.w.zeit = time.time_ns() - 3600 * 10**9
        o = self.p.aufgeben("BTCUSDT", "kauf", "markt", 1)["order"]
        self.assertEqual(o["status"], "offen")
        self.assertEqual(o["hinweis"], "wartet auf Handel")

    def test_stornieren(self):
        o = self.p.aufgeben("BTCUSDT", "kauf", "limit", 1, limit=50)["order"]
        self.assertEqual(self.p.stornieren(o["nr"]), {"nr": o["nr"]})
        self.assertIn("fehler", self.p.stornieren(o["nr"]))

    def test_handelszeit(self):
        berlin = ZoneInfo("Europe/Berlin")
        ns = lambda d: int(d.timestamp() * 1e9)
        self.assertTrue(papier.in_handelszeit("DE", ns(datetime(2026, 10, 20, 10, 0, tzinfo=berlin))))
        self.assertFalse(papier.in_handelszeit("DE", ns(datetime(2026, 10, 20, 18, 0, tzinfo=berlin))))   # Abendhandel
        self.assertFalse(papier.in_handelszeit("DE", ns(datetime(2026, 10, 24, 10, 0, tzinfo=berlin))))   # Samstag
        self.assertTrue(papier.in_handelszeit("KRYPTO", 0))

    def test_journal_eigene_eintraege_und_suche(self):
        e = self.p.eintrag("Zu früh rein, FOMO", "SAP")["eintrag"]
        self.assertEqual((e["art"], e["symbol"]), ("eigen", "SAP"))
        self.p.journal_schreiben("woche", "Stopps zu eng gesetzt", modell="attrappe")
        self.p.journal_schreiben("frage", "Meist zu früh", titel="Was mache ich falsch?")
        self.assertEqual([j["art"] for j in self.p.journal()], ["frage", "woche", "eigen"])
        self.assertEqual([j["art"] for j in self.p.journal("fomo")], ["eigen"])               # Groß-/Kleinschreibung egal
        self.assertEqual([j["art"] for j in self.p.journal("falsch")], ["frage"])             # sucht auch in der Frage
        self.assertEqual([j["art"] for j in self.p.journal(wer="claude")], ["frage", "woche"])
        self.assertEqual(len(self.p.journal(symbol="SAP")), 1)
        self.assertEqual(len(self.p.journal(n=2)), 2)
        self.assertIn("fehler", self.p.eintrag("   "))

    def test_journal_nur_eigene_aendern_und_loeschen(self):
        e = self.p.eintrag("erst")["eintrag"]
        g = self.p.eintrag("dann", nr=e["nr"])["eintrag"]
        self.assertEqual(g["text"], "dann")
        self.assertIsNotNone(g["geaendert"])
        claude = self.p.journal_schreiben("woche", "Rückblick")["eintrag"]
        self.assertIn("fehler", self.p.eintrag("überschrieben", nr=claude["nr"]))
        self.assertIn("fehler", self.p.eintrag_loeschen(claude["nr"]))
        self.assertEqual(self.p.eintrag_loeschen(e["nr"]), {"nr": e["nr"]})
        self.assertEqual([j["art"] for j in self.p.journal()], ["woche"])

    def test_journal_alte_tabelle_wird_ergaenzt(self):
        with self.z.speicher.lock:
            self.z.speicher.db.execute("DROP TABLE papier_journal")
            self.z.speicher.db.execute("CREATE TABLE papier_journal (nr INTEGER PRIMARY KEY, art TEXT, bezug INTEGER, zeit TEXT, text TEXT, modell TEXT, kosten REAL)")
        p = papier.Papier(self.z)
        self.assertEqual(p.eintrag("geht", "SAP")["eintrag"]["symbol"], "SAP")

    def test_wechselkurs_in_der_uebersicht(self):
        self.assertIsNone(self.p.uebersicht()["fx"])
        self.z.speicher._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)", ("papier_fx", '{"eurusd": 1.08, "datum": "2026-10-20", "quelle": "EZB"}'))
        self.assertEqual(self.p.uebersicht()["fx"]["eurusd"], 1.08)

    def test_durch_das_buch_zu_duenn(self):
        self.assertEqual(papier.durch_das_buch([(10, 1)], 3), (10.0, 10))
        self.assertIsNone(papier.durch_das_buch([], 1))


if __name__ == "__main__":
    unittest.main()
