"""Einrichtung: Einstellungsdatei, Schlüsseldateien (nie in Antworten), Vorlagen, Herzschlag und Lücken."""
import json
import os
import pathlib
import stat
import tempfile
import threading
import unittest
from unittest import mock

from marktdaten import einrichtung as E
from marktdaten.live.speicher import Speicher

GEHEIM = "geheim-1234567890"


class Zustand:
    def __init__(self, speicher):
        self.speicher, self.lock, self.werte = speicher, threading.RLock(), {}


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.konfig = self.tmp / "konfig"
        self.sp = Speicher(self.tmp / "daten")
        dateien = {n: self.konfig / f"{n}.env" for n in E.SCHLUESSEL}
        self.dateien = dateien
        self.patches = [mock.patch.object(E, "DATEI", self.konfig / "einstellungen.toml"),
                        mock.patch.object(E.ports, "DEMO", False),
                        mock.patch.dict(E.VERBINDUNG, {"speicher": self.sp, "zustand": Zustand(self.sp)}, clear=True),
                        mock.patch.dict(os.environ, {k: "" for s in E.SCHLUESSEL.values() for k, _ in s["felder"]})]
        for n, s in E.SCHLUESSEL.items():
            self.patches.append(mock.patch.dict(s, {"datei": (lambda p: lambda: p)(dateien[n])}))
        for p in self.patches:
            p.start()
        E._mtime.update(t=None, werte={})
        E._geprueft.clear()
        E._demo_einstellungen.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()


class Einstellungen(Basis):
    def test_offen_bis_fertig_und_toml_lesbar(self):
        self.assertTrue(E.offen())
        self.assertTrue(E.orderbuch())                         # ohne Einstellung wie bisher: Orderbuch an
        E.speichern(betriebsart="pc", orderbuch=False, bedingungen=True)
        self.assertTrue(E.offen())
        self.assertFalse(E.orderbuch())
        E.speichern(eingerichtet="2026-10-21T05:00:00+02:00")
        self.assertFalse(E.offen())
        text = E.DATEI.read_text()
        self.assertIn('betriebsart = "pc"   # server | miete | pc', text)
        self.assertEqual(E.laden()["betriebsart"], "pc")

    def test_demo_ist_nie_offen(self):
        with mock.patch.object(E.ports, "DEMO", True):
            self.assertFalse(E.offen())

    def test_bestand_gilt_als_eingerichtet(self):
        E.bestand_uebernehmen(self.sp)                         # leere Watchlist: neu, Einrichtung bleibt offen
        self.assertTrue(E.offen())
        self.sp.hinzufuegen("BTCUSDT", "BTCUSDT", "BTC / USDT", "KRYPTO")
        E.bestand_uebernehmen(self.sp)
        self.assertFalse(E.offen())
        self.assertTrue(E.laden()["uebernommen"])

    def test_ohne_bestaetigung_keine_schritte(self):
        for pfad in ("/api/einrichtung/schluessel", "/api/einrichtung/vorlagen", "/api/einrichtung/selbsttest"):
            self.assertIn("fehler", E.post(pfad, {"quelle": "eodhd", "werte": {"EODHD_API_TOKEN": "x"}}))
        self.assertFalse(self.dateien["eodhd"].exists())
        E.post("/api/einrichtung", {"bedingungen": True})
        self.assertTrue(E.bestaetigt())

    def test_post_fertig_braucht_bedingungen(self):
        self.assertIn("fehler", E.post("/api/einrichtung", {"fertig": True}))
        self.assertIn("fehler", E.post("/api/einrichtung", {"betriebsart": "irgendwas"}))
        d = E.post("/api/einrichtung", {"bedingungen": True, "betriebsart": "server", "obergrenze_gb": 12, "fertig": True})
        self.assertTrue(d["ok"])
        self.assertFalse(E.offen())
        self.assertEqual(self.sp.einstellungen()["obergrenze_gb"], 12.0)


class Schluessel(Basis):
    def test_datei_nur_fuer_nutzer_und_andere_zeilen_bleiben(self):
        p = self.dateien["telegram"]
        p.parent.mkdir(parents=True)
        p.write_text("# Kommentar\nTELEGRAM_TOKEN=alt\nTELEGRAM_CHAT=42\nANDERES=1\n")
        with mock.patch.object(E, "_anfrage", return_value=(200, {"result": {"username": "testbot"}})):
            d = E.schluessel_setzen("telegram", {"TELEGRAM_TOKEN": GEHEIM})
        self.assertTrue(d["ok"])
        text = p.read_text()
        self.assertIn(f"TELEGRAM_TOKEN={GEHEIM}", text)
        self.assertNotIn("TELEGRAM_CHAT", text)               # neuer Bot: alter Chat gilt nicht mehr
        self.assertIn("ANDERES=1", text)
        self.assertIn("# Kommentar", text)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertNotIn(GEHEIM, json.dumps(d))
        self.assertEqual(d["bot"], "testbot")
        self.assertFalse(d["chat"])

    def test_stand_und_antworten_enthalten_nie_den_schluessel(self):
        with mock.patch.object(E, "_anfrage", return_value=(200, {})):
            E.schluessel_setzen("alpaca", {"APCA_API_KEY_ID": GEHEIM, "APCA_API_SECRET_KEY": GEHEIM + "x"})
            E.schluessel_setzen("eodhd", {"EODHD_API_TOKEN": GEHEIM})
            E.schluessel_setzen("ki", {"OPENROUTER_KEY": GEHEIM})
        s = json.dumps(E.stand())
        self.assertNotIn(GEHEIM, s)
        st = {k["name"]: k for k in E.stand()["schluessel"]}
        self.assertTrue(st["alpaca"]["vorhanden"] and st["eodhd"]["vorhanden"] and st["ki"]["vorhanden"])
        self.assertFalse(st["telegram"]["vorhanden"])

    def test_fehler_ohne_url_und_ohne_wert(self):
        self.dateien["eodhd"].parent.mkdir(parents=True)
        self.dateien["eodhd"].write_text(f"EODHD_API_TOKEN={GEHEIM}\n")
        with mock.patch.object(E, "_anfrage", return_value=(401, {})):
            d = E.pruefen("eodhd")
        self.assertFalse(d["ok"])
        self.assertIn("abgelehnt", d["text"])
        self.assertNotIn(GEHEIM, json.dumps(d))
        with mock.patch.object(E, "_anfrage", return_value=(0, {})):
            self.assertIn("nicht erreichbar", E.pruefen("eodhd")["text"])

    def test_ungueltige_eingaben_und_entfernen(self):
        self.assertFalse(E.schluessel_setzen("alpaca", {"APCA_API_KEY_ID": "a"})["ok"])   # Secret fehlt
        self.assertFalse(E.schluessel_setzen("eodhd", {"EODHD_API_TOKEN": "a b"})["ok"])
        self.assertFalse(E.schluessel_setzen("gibtsnicht", {})["ok"])
        self.assertFalse(self.dateien["alpaca"].exists())
        with mock.patch.object(E, "_anfrage", return_value=(200, {})):
            E.schluessel_setzen("eodhd", {"EODHD_API_TOKEN": GEHEIM})
        self.assertTrue(E.vorhanden("eodhd"))
        E.schluessel_entfernen("eodhd")
        self.assertFalse(E.vorhanden("eodhd"))

    def test_sammler_wird_angestossen(self):
        gerufen = []
        E.VERBINDUNG["nach_schluessel"] = gerufen.append
        with mock.patch.object(E, "_anfrage", return_value=(403, {})):
            E.schluessel_setzen("alpaca", {"APCA_API_KEY_ID": "a", "APCA_API_SECRET_KEY": "b"})
        self.assertEqual(gerufen, [])                         # abgelehnter Schlüssel startet nichts
        with mock.patch.object(E, "_anfrage", return_value=(200, {})):
            E.schluessel_setzen("alpaca", {"APCA_API_KEY_ID": "a", "APCA_API_SECRET_KEY": "b"})
        self.assertEqual(gerufen, ["alpaca"])

    def test_demo_schreibt_keine_echten_schluessel(self):
        with mock.patch.object(E.ports, "DEMO", True):
            d = E.schluessel_setzen("alpaca", {"APCA_API_KEY_ID": "a", "APCA_API_SECRET_KEY": "b"})
            self.assertTrue(d["ok"])
            self.assertTrue(E.vorhanden("alpaca"))
            E.schluessel_entfernen("alpaca")
        self.assertFalse(self.dateien["alpaca"].exists())

    def test_telegram_chat_finden(self):
        self.dateien["telegram"].parent.mkdir(parents=True)
        self.dateien["telegram"].write_text(f"TELEGRAM_TOKEN={GEHEIM}\n")
        with mock.patch.object(E, "_anfrage", return_value=(200, {"result": []})):
            self.assertIn("/start", E.telegram_chat()["text"])
        antworten = iter([(200, {"result": [{"message": {"chat": {"id": 777}}}]}), (200, {"ok": True})])
        with mock.patch.object(E, "_anfrage", side_effect=lambda *a, **k: next(antworten)):
            d = E.telegram_chat()
        self.assertTrue(d["ok"])
        self.assertIn("TELEGRAM_CHAT=777", self.dateien["telegram"].read_text())


class Vorlagen(Basis):
    def test_usa_erst_mit_alpaca(self):
        aufgenommen = []
        E.VERBINDUNG["aufnehmen"] = lambda w: aufgenommen.extend(w) or [x[1] for x in w]
        E.vorlagen_uebernehmen(["usa", "krypto"])
        self.assertEqual({w[3] for w in aufgenommen}, {"KRYPTO"})
        frei = {v["name"]: v["frei"] for v in E.vorlagen_json()}
        self.assertEqual(frei, {"dax": True, "krypto": True, "usa": False})


class Herzschlag(Basis):
    def test_luecken_und_laufzeit(self):
        h = E.Herzschlag(self.sp)
        t0 = 1_760_000_000 // 60 * 60
        for m in list(range(0, 30)) + list(range(50, 60)):          # 20 Minuten Pause nach Minute 29
            h.schlag(t0 + m * 60)
        o = E.online(self.sp, jetzt=t0 + 59 * 60 + 10)
        self.assertEqual(len(o["luecken"]), 1)
        self.assertEqual(o["luecken"][0]["minuten"], 20)
        self.assertEqual(o["seit"], t0 + 50 * 60)
        self.assertFalse(o["steht"])
        self.assertTrue(E.online(self.sp, jetzt=t0 + 70 * 60)["steht"])
        self.assertIsNone(E.online(Speicher(self.tmp / "leer"))["letzter"])


class DemoImTerminal(Basis):
    def test_nachfrage_weiter_und_ende_ohne_antwort(self):
        with mock.patch.object(E, "DEMO_TERMINAL", True), mock.patch.object(E, "FRAGE_S", 600), \
                mock.patch.dict(E._demo, {"start": 1000.0, "frage_ab": 1600.0}, clear=True):
            self.assertFalse(E.demo_stand(jetzt=1500)["frage"])
            self.assertTrue(E.demo_stand(jetzt=1700)["frage"])
            self.assertFalse((self.sp.datei.parent / ".stopp").exists())
            with mock.patch.object(E.time, "time", return_value=1700):
                E.demo_weiter()                                  # „Nein“: nächste Frage in 10 Minuten
            self.assertFalse(E.demo_stand(jetzt=2200)["frage"])
            E.demo_stand(jetzt=2300 + 600)                       # Frage um 2300 blieb 10 Minuten offen
            self.assertEqual((self.sp.datei.parent / ".stopp").read_text(), "loeschen")

    def test_stoppen_nur_in_der_demo_im_terminal(self):
        self.assertFalse(E.post("/api/einrichtung/demo/stoppen", {})["ok"])
        self.assertEqual(E.demo_stand(), {"terminal": False})
        self.assertFalse((self.sp.datei.parent / ".stopp").exists())


if __name__ == "__main__":
    unittest.main()
