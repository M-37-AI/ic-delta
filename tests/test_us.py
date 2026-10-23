"""US-Sammler: der Anlauf fragt vor 8:00 UTC keine Minutenkerzen des Tages ab (Start läge in der Zukunft, HTTP 400)."""
import pathlib
import tempfile
import unittest
from datetime import datetime, timezone

from marktdaten.live import us
from marktdaten.live.speicher import Speicher
from marktdaten.live.zustand import Wert, Zustand


def uhr(stunde):
    class Fest(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, stunde, 15, tzinfo=timezone.utc)
    return Fest


class Anlauf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.z = Zustand(Speicher(pathlib.Path(self.tmp.name)))
        self.z.hinzufuegen(Wert("AAPL", "AAPL", "Apple", "US"), aus_speicher=False)
        self.s = us.UsSammler(self.z)
        self.abfragen = []
        self.s._rest = lambda pfad, **p: self.abfragen.append((pfad, p.get("timeframe"))) or {"bars": {}}
        self.alt = us.datetime

    def tearDown(self):
        us.datetime = self.alt
        self.tmp.cleanup()

    def test_nachts_ohne_minutenkerzen(self):
        us.datetime = uhr(3)
        self.s.anlauf(["AAPL"])
        self.assertNotIn(("/v2/stocks/bars", "1Min"), self.abfragen)
        self.assertIsNone(self.z.dienste["alpaca"]["fehler"])

    def test_tagsueber_mit_minutenkerzen(self):
        us.datetime = uhr(14)
        self.s.anlauf(["AAPL"])
        self.assertIn(("/v2/stocks/bars", "1Min"), self.abfragen)


if __name__ == "__main__":
    unittest.main()
