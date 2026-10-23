"""Historie deutscher Werte über EODHD: offline mit erfundenen Antworten (keine Abrufe, kein echter Schlüssel)."""
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from marktdaten.live import historie_de as H
from marktdaten.live.speicher import Speicher
from marktdaten.live.zustand import Wert, Zustand
from marktdaten.quellen import eodhd as E

UTC = timezone.utc
ISIN = "ZZ0000000011"


def werktage(n):
    """Die letzten n Werktage vor heute als YYYY-MM-DD, aufsteigend."""
    d, aus = datetime.now(UTC).date() - timedelta(days=1), []
    while len(aus) < n:
        if d.weekday() < 5:
            aus.append(d.isoformat())
        d -= timedelta(days=1)
    return aus[::-1]


class Attrappe:
    """Antwortet wie EODHD, zählt Abrufe."""
    def __init__(self, tage, unbekannt=()):
        self.tage, self.unbekannt, self.abrufe = tage, set(unbekannt), []

    def tageskerzen(self, code, von, max_tag):
        self.abrufe.append(("eod", code))
        if code in self.unbekannt:
            raise RuntimeError(f"EODHD antwortet 404 für eod/{code}")
        return [{"date": t, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "adjusted_close": 10.5, "volume": 1000}
                for t in self.tage if t >= von]

    def splits(self, code, max_tag):
        self.abrufe.append(("splits", code))
        return [{"date": "2006-12-21", "split": "4.000000/1.000000"}]

    def suchen(self, isin, max_tag):
        self.abrufe.append(("suche", isin))
        return "TSTX.XETRA"


class Historie(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        (t / "eodhd.env").write_text("EODHD_API_TOKEN=nur-ein-test\n")
        self.env = mock.patch.dict(os.environ, {"MARKTDATEN_EODHD_ENV": str(t / "eodhd.env")})
        self.stand = mock.patch.object(E, "STAND", t / "eodhd.json")
        self.env.start(); self.stand.start()
        self.sp = Speicher(t)
        self.z = Zustand(self.sp)
        self.w = Wert(ISIN, "TST", "Test AG", "DE")
        self.z.hinzufuegen(self.w)
        self.tage = werktage(30)

    def tearDown(self):
        self.env.stop(); self.stand.stop()
        self.sp.db.close()
        self.tmp.cleanup()

    def kerzen(self):
        return {datetime.fromtimestamp(t, UTC).date().isoformat(): q for t, *_, q in self.sp.kerzen_lesen("kerzen_1d", ISIN, mit_quelle=True)}

    def test_eigene_tage_haben_vorrang(self):
        eigener = self.tage[-3]                                   # an diesem Tag hat iC Delta selbst Minuten
        t = int(datetime.fromisoformat(eigener).replace(tzinfo=UTC).timestamp()) + 9 * 3600
        self.sp.kerzen("kerzen_1m", ISIN, [[t, 20, 21, 19, 20.5, 5]], "xetra")
        a = Attrappe(self.tage)
        H.HistorieDE(self.z, a).pruefen()
        k = self.kerzen()
        self.assertNotIn(eigener, k)
        self.assertEqual(len(k), len(self.tage) - 1)
        self.assertEqual(set(k.values()), {"eodhd"})
        self.assertEqual(self.sp._x("SELECT faktor FROM splits WHERE id = ?", (ISIN,)), [(4.0,)])

    def test_kein_zweiter_abruf_ohne_luecke(self):
        a = Attrappe(self.tage)
        h = H.HistorieDE(self.z, a)
        h.pruefen()
        n = len(a.abrufe)
        with mock.patch.object(h, "_letzter_handelstag", return_value=int(datetime.fromisoformat(self.tage[-1]).replace(tzinfo=UTC).timestamp())):
            h.pruefen()
        self.assertEqual(len(a.abrufe), n)                       # alles da: nichts holen
        self.assertEqual(n, 2)                                   # Kerzen und Splits

    def test_kuerzel_per_isin_suchen(self):
        a = Attrappe(self.tage, unbekannt={"TST.XETRA"})
        H.HistorieDE(self.z, a).pruefen()
        self.assertEqual([x[0] for x in a.abrufe], ["eod", "suche", "eod", "splits"])
        self.assertEqual(E.stand()["kuerzel"][ISIN], "TSTX.XETRA")

    def test_tagesgrenze(self):
        d = E.stand(); d["abrufe"] = 20; E._sichern(d)
        with self.assertRaises(E.Kontingent):
            E.abruf("eod/TST.XETRA", 20)                          # bricht vor jedem Netzzugriff ab

    def test_ohne_schluessel_passiert_nichts(self):
        with mock.patch.dict(os.environ, {"MARKTDATEN_EODHD_ENV": "/gibt/es/nicht"}):
            a = Attrappe(self.tage)
            self.assertEqual(H.HistorieDE(self.z, a).pruefen(), {})
            self.assertEqual(a.abrufe, [])
            self.assertEqual(self.z.dienste["eodhd"]["hinweis"], "ohne Schlüssel")

    def test_schluessel_nie_in_fehlermeldung(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("kaputt")):
            with self.assertRaises(RuntimeError) as f:
                E.abruf("eod/TST.XETRA", 20)
        self.assertNotIn("nur-ein-test", str(f.exception))


if __name__ == "__main__":
    unittest.main()
