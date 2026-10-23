"""Split-Bereinigung deutscher Werte über die Tabelle splits (EODHD)."""
import pathlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from marktdaten.analysen import lesen

TAG = 86400


def _t(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


class SplitsTabelle(unittest.TestCase):
    def test_rechnung(self):
        k = [[1, 400, 404, 396, 400, 100], [2, 100, 101, 99, 100, 400], [3, 100, 101, 99, 100, 400]]
        aus = lesen.splits_aus_tabelle(k, [(2, 4.0)], vol_bereinigt=set())
        self.assertEqual(aus[0], [1, 100, 101, 99, 100, 400])                 # vor dem Split: Kurs /4, Volumen ×4
        self.assertEqual(aus[1:], k[1:])                                      # ab dem Split-Tag unverändert
        aus = lesen.splits_aus_tabelle(k, [(2, 4.0)], vol_bereinigt={1})
        self.assertEqual(aus[0][5], 100)                                      # EODHD-Volumen nicht doppelt
        self.assertEqual(lesen.splits_aus_tabelle(k, []), k)

    def test_kerzen_de(self):
        with tempfile.TemporaryDirectory() as ordner:
            alt = (lesen.DATEN, lesen._jetzt)
            try:
                lesen.DATEN = pathlib.Path(ordner)
                lesen._jetzt = lambda: _t("2026-10-21T12:00")
                db = sqlite3.connect(lesen.DATEN / "marktdaten.db")
                db.execute("CREATE TABLE watchlist (id TEXT PRIMARY KEY, symbol TEXT, name TEXT, markt TEXT, hinzugefuegt TEXT)")
                db.execute("CREATE TABLE kerzen_1d (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, q TEXT)")
                db.execute("CREATE TABLE kerzen_1m (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, q TEXT)")
                db.execute("CREATE TABLE splits (id TEXT, t INTEGER, faktor REAL, quelle TEXT)")
                db.execute("INSERT INTO watchlist VALUES ('DE1', 'X', 'X', 'DE', '')")
                t0 = _t("2026-10-12T00:00")
                zeilen = [(t0, 200, "eodhd"), (t0 + TAG, 200, "eodhd"), (t0 + 2 * TAG, 100, "eodhd"), (t0 + 3 * TAG, 100, "xetra")]
                db.executemany("INSERT INTO kerzen_1d VALUES ('DE1', ?, ?, ?, ?, ?, 1000, ?)", [(t, c, c, c, c, q) for t, c, q in zeilen])
                db.execute("INSERT INTO splits VALUES ('DE1', ?, 2.0, 'eodhd')", (t0 + 2 * TAG,))
                db.commit(); db.close()
                k = lesen.kerzen("DE1", "1d", 10)
                self.assertEqual([x[4] for x in k], [100, 100, 100, 100])     # kein Sprung mehr
                self.assertEqual([x[5] for x in k], [1000, 1000, 1000, 1000]) # EODHD-Volumen schon bereinigt
            finally:
                lesen.DATEN, lesen._jetzt = alt


if __name__ == "__main__":
    unittest.main()
