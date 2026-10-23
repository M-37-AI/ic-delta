"""KI-Anbau im Reiter Analysen: Kurzfassung, Werkstatt-Ausgabe und Sandkasten, Journal, Buch-Alarme, Telegram-HTML."""
import json
import pathlib
import platform
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from marktdaten.analysen import alarme, kurz, lesen, notizen, werkstatt, werkstatt_lauf


class Kurzfassung(unittest.TestCase):
    def test_reihe_und_leer(self):
        r = kurz.reihe([1792393200 + i * 300 for i in range(24)], [float(i) for i in range(24)], n=4)
        self.assertEqual((r["min"], r["max"], r["zuletzt"]), (0.0, 23.0, 23.0))
        self.assertLessEqual(len(r["verlauf"]), 5)
        a = {"isin": "X", "tag": "2026-10-20", "ereignisse": 5, "fenster": [1792393260, 1792423800], "a": {"leer": True},
             "d": {"plaetze": [], "volumen": [{"platz": "XETR", "eur": 1}], "fuehrung": [], "retail": None}}
        k = kurz.kurzfassung(a)
        self.assertTrue(k["a"]["leer"])
        self.assertEqual(k["d"]["umsatz_je_platz"][0]["platz"], "XETR")
        self.assertNotIn("b", k)
        self.assertEqual(set(kurz.kurzfassung(a, "d")) & {"a", "d"}, {"d"})


class Werkstatt(unittest.TestCase):
    def test_begrenzen(self):
        aus = werkstatt_lauf.begrenzen({"zahlen": {"n": 3}, "tabelle": {"spalten": ["a"], "zeilen": [[i] for i in range(400)]},
                                        "reihen": [{"t": list(range(2000)), "serien": [{"name": "x", "werte": list(range(2000))}]}],
                                        "punkte": {"punkte": [[i, i] for i in range(5000)]}, "text": "t" * 9000})
        self.assertEqual(aus["zahlen"][0], {"titel": "n", "wert": 3, "neben": ""})
        self.assertEqual(len(aus["tabellen"][0]["zeilen"]), 250)
        self.assertLessEqual(len(aus["reihen"][0]["t"]), 800)
        self.assertEqual(len(aus["reihen"][0]["t"]), len(aus["reihen"][0]["serien"][0]["werte"]))
        self.assertLessEqual(len(aus["punkte"][0]["punkte"]), 2500)
        self.assertEqual(len(aus["text"]), 4000)

    def test_profil_sperrt_home(self):
        p = werkstatt._profil()
        heim = str(pathlib.Path.home().resolve())
        self.assertIn(f'(deny file-read* (subpath "{heim}"))', p)
        self.assertIn("(deny network*)", p)
        self.assertIn("(deny file-write*)", p)
        self.assertIn(str(werkstatt.SRC), p)

    @unittest.skipUnless(platform.system() == "Darwin" and pathlib.Path("/usr/bin/sandbox-exec").exists(), "nur macOS")
    def test_sandkasten_blockt(self):
        tage = lesen.Rohdaten().tage()
        if not tage:
            self.skipTest("keine Rohdaten")
        isin = sorted(lesen.Rohdaten().werte(tage[0]))[0]
        r = werkstatt.ausfuehren("import os\nos.listdir(os.path.expanduser('~/Documents'))", isin, tage[0])
        self.assertFalse(r["ok"])
        self.assertIn("Operation not permitted", r["fehler"])
        r = werkstatt.ausfuehren("ausgabe['zahlen'] = [{'titel': 'Meldungen', 'wert': len(ereignisse)}]\nprint('hallo')", isin, tage[0])
        self.assertTrue(r["ok"], r.get("fehler"))
        self.assertEqual(r["ausgabe"]["zahlen"][0]["titel"], "Meldungen")
        self.assertIn("hallo", r["stdout"])


class Journal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ort = pathlib.Path(self.tmp.name)
        self.alt = (notizen.ORT, notizen.JOURNAL, notizen.NOTIZEN, notizen.KI, notizen.DOSSIER, lesen.watchlist, lesen.letzter_kurs)
        notizen.ORT, notizen.JOURNAL, notizen.NOTIZEN, notizen.KI, notizen.DOSSIER = ort, ort / "j.jsonl", ort / "n.jsonl", ort / "ki.json", ort / "dossier"
        lesen.watchlist = lambda: [{"id": "ZZ0000000011", "symbol": "MUST", "name": "Muster", "markt": "DE"}]
        self.kurs = 100.0
        lesen.letzter_kurs = lambda wert: (1, self.kurs)

    def tearDown(self):
        (notizen.ORT, notizen.JOURNAL, notizen.NOTIZEN, notizen.KI, notizen.DOSSIER, lesen.watchlist, lesen.letzter_kurs) = self.alt
        self.tmp.cleanup()

    def test_einschaetzung_wird_geprueft(self):
        e = notizen.journal_eintragen({"wert": "MUST", "richtung": "steigt", "horizont": "1h", "begruendung": "Test", "modell": "m"})
        self.assertEqual(e["kurs"], 100.0)
        self.assertEqual(notizen.journal_pruefen(), 0)                       # noch nicht fällig
        eintraege = notizen._journal_lesen()
        eintraege[0]["faellig"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        notizen._journal_schreiben(eintraege)
        self.kurs = 100.05                                                   # unter der Schwelle von 0,1 %
        self.assertEqual(notizen.journal_pruefen(), 1)
        self.assertEqual(notizen._journal_lesen()[0]["ergebnis"], "unentschieden")
        e2 = notizen.journal_eintragen({"wert": "MUST", "richtung": "faellt", "horizont": "1h", "begruendung": "x"})
        eintraege = notizen._journal_lesen()
        eintraege[1]["faellig"] = eintraege[0]["faellig"]
        notizen._journal_schreiben(eintraege)
        self.kurs = 99.0
        notizen.journal_pruefen()
        b = notizen.journal_bilanz()
        self.assertEqual(b["gesamt"]["treffer"], 1)
        self.assertEqual(b["je_horizont"][0]["name"], "1h")
        with self.assertRaises(ValueError):
            notizen.journal_eintragen({"wert": "MUST", "richtung": "hoch", "horizont": "1h"})

    def test_dossier_notiz_ki(self):
        notizen.dossier_schreiben("MUST", "# Muster\n- Spread meist 5 bp")
        self.assertIn("5 bp", notizen.dossier_lesen("ZZ0000000011")["text"])
        self.assertEqual(len(notizen.dossiers()), 1)
        notizen.notiz_schreiben("morgenlage", "Guten Morgen", "m")
        self.assertEqual(notizen.notizen_lesen(5)[0]["art"], "morgenlage")
        self.assertEqual(notizen.ki_schreiben({"anbieter": "openrouter", "modell": "x/y", "unsinn": 1, "zweitmeinung": "ja"})["anbieter"], "openrouter")
        self.assertTrue(notizen.ki_lesen()["zweitmeinung"])                   # falscher Typ wird ignoriert
        self.assertNotIn("unsinn", notizen.ki_lesen())


class BuchAlarm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ort = pathlib.Path(self.tmp.name)
        self.alt = (alarme.ORT, alarme.REGELN, alarme.VERLAUF, alarme.telegram_senden, lesen.buch_minuten, lesen.watchlist, notizen.JOURNAL)
        alarme.ORT, alarme.REGELN, alarme.VERLAUF = ort, ort / "alarme.json", ort / "verlauf.jsonl"
        notizen.JOURNAL = ort / "j.jsonl"
        self.gesendet = []
        alarme.telegram_senden = lambda text: self.gesendet.append(text)
        lesen.watchlist = lambda: [{"id": "ZZ0000000011", "symbol": "MUST", "name": "Muster", "markt": "DE"}]
        self.zeilen = [{"t": 1000 + 60 * i, "bid": 100, "ask": 100.1, "spread_bp": 10, "tiefe_bid": 1e5, "tiefe_ask": 1e5, "ungleich": 0.1, "updates": 5} for i in range(5)]
        lesen.buch_minuten = lambda wert, n=5: [dict(z) for z in self.zeilen[-n:]]
        self.a = alarme.Alarme()

    def tearDown(self):
        (alarme.ORT, alarme.REGELN, alarme.VERLAUF, alarme.telegram_senden, lesen.buch_minuten, lesen.watchlist, notizen.JOURNAL) = self.alt
        self.tmp.cleanup()

    def minute(self, ungleich):
        z = dict(self.zeilen[-1]); z["t"] += 60; z["ungleich"] = ungleich
        self.zeilen.append(z)

    def test_ungleichgewicht_zwei_minuten(self):
        r = self.a.anlegen({"wert": "MUST", "art": "buch", "kennzahl": "ungleich", "vergleich": "ueber", "schwelle": 0.5, "minuten": 2})
        self.assertIn("Ungleichgewicht über 0,50 seit 2 Min.", r["bedingung"])
        self.assertFalse(r["einmalig"])
        self.a.runde()
        self.assertEqual(self.gesendet, [])
        self.minute(0.6)
        self.a.runde()
        self.assertEqual(self.gesendet, [])                                  # erst eine Minute über der Schwelle
        self.minute(0.7)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 1)
        self.assertIn("zuletzt 0,70", self.gesendet[0])
        self.minute(0.8)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 1)                              # bleibt erfüllt: keine zweite Meldung
        self.minute(0.1)
        self.minute(0.9)
        self.minute(0.9)
        self.a.runde()
        self.assertEqual(len(self.gesendet), 2)                              # nach Unterbrechung wieder
        with self.assertRaises(ValueError):
            self.a.anlegen({"wert": "MUST", "art": "buch", "kennzahl": "mond", "vergleich": "ueber", "schwelle": 1})

    def test_telegram_html(self):
        h = alarme.markdown_zu_html("**SAP** steigt & fällt\n- erstens `x<y`\n# Titel")
        self.assertEqual(h, "<b>SAP</b> steigt &amp; fällt\n• erstens <code>x&lt;y</code>\n<b>Titel</b>")
        self.assertEqual(len(alarme._stuecke("a\n" * 3000, 3900)), 2)


if __name__ == "__main__":
    unittest.main()
