"""Letzter Handelstag in den Analysen: Aktien-Tageskerze nach Börsenschluss abgeschlossen, Krypto wie bisher."""
import pathlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from marktdaten.analysen import lesen


def _t(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


class LetzterTag(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.alt = (lesen.DATEN, lesen._jetzt)
        lesen.DATEN = pathlib.Path(self.ordner.name)
        db = sqlite3.connect(lesen.DATEN / "marktdaten.db")
        db.execute("CREATE TABLE watchlist (id TEXT PRIMARY KEY, symbol TEXT, name TEXT, markt TEXT, hinzugefuegt TEXT)")
        db.execute("CREATE TABLE kerzen_1d (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, q TEXT)")
        db.execute("CREATE TABLE kerzen_1m (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, q TEXT)")
        for id_, markt, von, bis in (("DE0007164600", "DE", "2026-10-20T07:00", "2026-10-20T15:37"),
                                     ("US0378331005", "US", "2026-10-20T08:00", "2026-10-20T23:43"),
                                     ("BTCUSDT", "KRYPTO", "2026-10-20T00:00", "2026-10-20T23:00")):
            db.execute("INSERT INTO watchlist VALUES (?, ?, ?, ?, '')", (id_, id_, id_, markt))
            db.execute("INSERT INTO kerzen_1d VALUES (?, ?, 100, 101, 99, 100, 1000, 'x')", (id_, _t("2026-10-19T00:00")))
            db.executemany("INSERT INTO kerzen_1m VALUES (?, ?, 100, 101, 99, 100, 10, 'x')",
                           [(id_, t) for t in range(_t(von), _t(bis) + 1, 60)])
        db.commit(); db.close()

    def tearDown(self):
        lesen.DATEN, lesen._jetzt = self.alt
        self.ordner.cleanup()

    def tage(self, id_, jetzt):
        lesen._jetzt = lambda: _t(jetzt)
        return [datetime.fromtimestamp(x[0], timezone.utc).date().isoformat() for x in lesen.kerzen(id_, "1d", 10)]

    def test_de_nach_schluss(self):
        self.assertEqual(self.tage("DE0007164600", "2026-10-20T14:00"), ["2026-10-19"])            # 16:00 Berlin: läuft noch
        self.assertEqual(self.tage("DE0007164600", "2026-10-20T15:50"), ["2026-10-19", "2026-10-20"])  # 17:50 Berlin

    def test_us_nach_tagesende(self):
        self.assertEqual(self.tage("US0378331005", "2026-10-20T22:00"), ["2026-10-19"])
        self.assertEqual(self.tage("US0378331005", "2026-10-21T00:10"), ["2026-10-19", "2026-10-20"])

    def test_krypto_unveraendert(self):
        self.assertEqual(self.tage("BTCUSDT", "2026-10-21T05:00"), ["2026-10-19"])                  # keine Minute vom 21.


if __name__ == "__main__":
    unittest.main()
