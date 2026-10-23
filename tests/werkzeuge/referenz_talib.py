"""Referenzwerte für die Indikatoren von iC Charts aus TA-Lib (BSD-Lizenz) erzeugen.

TA-Lib ist nur ein Prüfwerkzeug für die Entwicklung, keine Abhängigkeit von iC Delta. Es wird nicht mitgeliefert
und nicht im Produkt verwendet. Einmalig in einer eigenen Umgebung installieren:

    python3.12 -m venv ~/arbeit/indikatoren/.venv-talib
    ~/arbeit/indikatoren/.venv-talib/bin/pip install "TA-Lib>=0.6" numpy
    ~/arbeit/indikatoren/.venv-talib/bin/python tests/werkzeuge/referenz_talib.py

Ergebnis: tests/daten/indikatoren_referenz.json (frei erzeugte Kerzen, keine Marktdaten) mit den TA-Lib-Werten.
Die Tests (tests/js/test_indikatoren.cjs, tests/test_indikator_referenz.py) lesen nur diese Datei.
"""
from __future__ import annotations

import json
import pathlib
import random

import numpy as np
import talib

ZIEL = pathlib.Path(__file__).resolve().parents[1] / "daten" / "indikatoren_referenz.json"


def kerzen(n: int = 600, seed: int = 7) -> list[dict]:
    """Zufallspfad mit Trendphasen, Seitwärtsphasen und Volumenspitzen (frei erzeugt)."""
    r = random.Random(seed)
    preis, out, t = 100.0, [], 1_700_000_000
    for i in range(n):
        drift = 0.0015 if (i // 120) % 2 == 0 else -0.001
        o = preis
        c = o * (1 + drift + r.gauss(0, 0.012))
        h = max(o, c) * (1 + abs(r.gauss(0, 0.004)))
        l = min(o, c) * (1 - abs(r.gauss(0, 0.004)))
        v = round(1000 * (1 + abs(r.gauss(0, 0.6))) * (4 if r.random() < 0.03 else 1), 2)
        out.append({"time": t + i * 3600, "open": round(o, 4), "high": round(h, 4), "low": round(l, 4), "close": round(c, 4), "volume": v})
        preis = c
    return out


def liste(a) -> list:
    return [None if np.isnan(x) else round(float(x), 10) for x in a]


def main() -> None:
    b = kerzen()
    o, h, l, c, v = (np.array([x[k] for x in b], dtype=float) for k in ("open", "high", "low", "close", "volume"))
    macd, sig, hist = talib.MACD(c, 12, 26, 9)
    up, mid, lo = talib.BBANDS(c, 20, 2, 2, 0)
    k, d = talib.STOCH(h, l, c, 14, 3, 0, 3, 0)
    ar_dn, ar_up = talib.AROON(h, l, 25)
    half, full = talib.WMA(c, 10), talib.WMA(c, 20)
    tp = (h + l + c) / 3
    ref = {
        "sma20": liste(talib.SMA(c, 20)), "ema20": liste(talib.EMA(c, 20)), "wma20": liste(talib.WMA(c, 20)),
        "hma20": liste(talib.WMA(2 * half - full, 4)),
        "rsi14": liste(talib.RSI(c, 14)), "macd": liste(macd), "macd_signal": liste(sig), "macd_hist": liste(hist),
        "bb_upper": liste(up), "bb_mid": liste(mid), "bb_lower": liste(lo),
        "atr14": liste(talib.ATR(h, l, c, 14)), "stoch_k": liste(k), "stoch_d": liste(d),
        "sar": liste(talib.SAR(h, l, 0.02, 0.2)),
        "adx14": liste(talib.ADX(h, l, c, 14)), "plus_di14": liste(talib.PLUS_DI(h, l, c, 14)), "minus_di14": liste(talib.MINUS_DI(h, l, c, 14)),
        "aroon_up25": liste(ar_up), "aroon_down25": liste(ar_dn),
        "willr14": liste(talib.WILLR(h, l, c, 14)), "cci20": liste(talib.CCI(h, l, c, 20)), "roc12": liste(talib.ROC(c, 12)),
        "obv": liste(talib.OBV(c, v)), "mfi14": liste(talib.MFI(h, l, c, v, 14)),
        "linreg20": liste(talib.LINEARREG(c, 20)),
        "donchian_upper20": liste(talib.MAX(h, 20)), "donchian_lower20": liste(talib.MIN(l, 20)),
        "keltner_mid20": liste(talib.EMA(c, 20)), "keltner_atr10": liste(talib.ATR(h, l, c, 10)),
        "typ_preis": liste(tp),
    }
    ZIEL.parent.mkdir(parents=True, exist_ok=True)
    ZIEL.write_text(json.dumps({"erzeugt_mit": f"TA-Lib {talib.__version__}", "hinweis": "Frei erzeugte Kerzen, keine Marktdaten.",
                                "kerzen": b, "talib": ref}, separators=(",", ":")))
    print(f"{ZIEL} · {len(b)} Kerzen · {len(ref)} Reihen")


if __name__ == "__main__":
    main()
