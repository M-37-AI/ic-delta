"""Die Python-Indikatoren der Analysen rechnen wie iC Charts: Abgleich mit denselben TA-Lib-Referenzwerten
(tests/daten/indikatoren_referenz.json, erzeugt mit tests/werkzeuge/referenz_talib.py).

Startwerte dürfen sich unterscheiden (z. B. EMA und ATR starten in Python mit dem ersten Wert statt mit einem
Schnitt), deshalb wird erst nach dem Einschwingen verglichen."""
import json
import pathlib
import unittest

from marktdaten.analysen.charts import _ema
from marktdaten.analysen.formationen import atr, rsi, sma

REF = json.loads((pathlib.Path(__file__).parent / "daten" / "indikatoren_referenz.json").read_text())
K = [[b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in REF["kerzen"]]
C = [k[4] for k in K]
T = REF["talib"]


class Referenz(unittest.TestCase):
    def gleich(self, name, ist, soll, ab, tol=1e-6):
        for i in range(ab, len(soll)):
            if soll[i] is None:
                continue
            self.assertIsNotNone(ist[i], f"{name}[{i}] fehlt")
            self.assertLessEqual(abs(ist[i] - soll[i]) / max(1, abs(soll[i])), tol, f"{name}[{i}]: {ist[i]} statt {soll[i]}")

    def test_sma(self):
        self.gleich("SMA 20", sma(C, 20), T["sma20"], 19, 1e-9)

    def test_rsi(self):
        self.gleich("RSI 14", rsi(C, 14), T["rsi14"], 14, 1e-9)

    def test_ema(self):
        self.gleich("EMA 20", _ema(C, 20), T["ema20"], 300)

    def test_atr(self):
        self.gleich("ATR 14", atr(K, 14), T["atr14"], 300)


if __name__ == "__main__":
    unittest.main()
