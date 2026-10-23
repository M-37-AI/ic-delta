"""Läuft im Sandkasten (sandbox-exec, ohne Netz, ohne Schreibrechte): führt eine Analyse aus der Werkstatt aus.

Auftrag als JSON auf stdin: {"code", "isin", "tag", "parameter"}. Ergebnis als JSON auf stdout.
Der Code bekommt die Rohdaten des Tages, den fertig durchlaufenen Lauf und Hilfsfunktionen und legt sein
Ergebnis in das Wörterbuch `ausgabe` (siehe REFERENZ in werkstatt.py).
"""
from __future__ import annotations

import bisect
import collections
import datetime
import io
import itertools
import json
import math
import statistics
import sys
import traceback

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))   # -I ignoriert PYTHONPATH: Quellordner selbst eintragen

GRENZEN = {"reihe_punkte": 800, "tabelle_zeilen": 250, "punkte": 2500, "text": 4000, "zahlen": 24, "stdout": 4000}


def _duenn(liste: list, n: int) -> list:
    if len(liste) <= n:
        return liste
    schritt = len(liste) / n
    return [liste[int(i * schritt)] for i in range(n)]


def _zahl(x):
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return None if isinstance(x, float) and (math.isnan(x) or math.isinf(x)) else x
    return str(x)


def begrenzen(ausgabe: dict) -> dict:
    """Bringt die Ausgabe in die erwartete Form und schneidet Übergroßes ab."""
    aus: dict = {}
    z = ausgabe.get("zahlen")
    if isinstance(z, dict):
        z = [{"titel": str(k), "wert": v} for k, v in z.items()]
    if isinstance(z, list):
        aus["zahlen"] = [{"titel": str(x.get("titel", "")), "wert": _zahl(x.get("wert")), "neben": str(x.get("neben", ""))[:80]}
                         for x in z[:GRENZEN["zahlen"]] if isinstance(x, dict)]
    tab = ausgabe.get("tabelle")
    tabs = tab if isinstance(tab, list) and tab and isinstance(tab[0], dict) and "spalten" in tab[0] else ([tab] if isinstance(tab, dict) else [])
    if tabs:
        aus["tabellen"] = [{"titel": str(t.get("titel", "")), "spalten": [str(s) for s in t.get("spalten", [])],
                            "zeilen": [[_zahl(w) for w in zeile] for zeile in list(t.get("zeilen", []))[:GRENZEN["tabelle_zeilen"]]],
                            "rechts": [int(i) for i in t.get("rechts", [])]} for t in tabs[:6]]
    reihen = ausgabe.get("reihen") or ([ausgabe["reihe"]] if isinstance(ausgabe.get("reihe"), dict) else [])
    if reihen:
        aus["reihen"] = []
        for r in reihen[:8]:
            t = list(r.get("t", []))
            serien = r.get("serien") or [{"name": r.get("name", "Wert"), "werte": r.get("werte", [])}]
            idx = _duenn(list(range(len(t))), GRENZEN["reihe_punkte"])
            aus["reihen"].append({"titel": str(r.get("titel", "")), "neben": str(r.get("neben", ""))[:120], "einheit": str(r.get("einheit", ""))[:12],
                                  "null": bool(r.get("null", False)), "stellen": int(r.get("stellen", 2)),
                                  "t": [_zahl(t[i]) for i in idx],
                                  "serien": [{"name": str(s.get("name", "")), "werte": [_zahl(list(s.get("werte", []))[i]) if i < len(s.get("werte", [])) else None for i in idx]}
                                             for s in serien[:4]]})
    punkte = ausgabe.get("punkte")
    if isinstance(punkte, dict):
        punkte = [punkte]
    if isinstance(punkte, list) and punkte and isinstance(punkte[0], dict):
        aus["punkte"] = [{"titel": str(p.get("titel", "")), "x": str(p.get("x", "x")), "y": str(p.get("y", "y")),
                          "punkte": [[_zahl(a), _zahl(b)] for a, b in _duenn(list(p.get("punkte", [])), GRENZEN["punkte"])],
                          "regression": bool(p.get("regression", False))} for p in punkte[:4]]
    if ausgabe.get("text"):
        aus["text"] = str(ausgabe["text"])[:GRENZEN["text"]]
    return aus


def main() -> None:
    auftrag = json.load(sys.stdin)
    echt = sys.stdout
    sys.stdout = puffer = io.StringIO()
    try:
        from marktdaten.analysen import lesen, rechnen
        from marktdaten.modell import KAUF, VERKAUF, BuchSeite, BuchStufe, Quote, Trade

        isin, tag = auftrag["isin"], auftrag["tag"]
        p = {**rechnen.STANDARD, **(auftrag.get("parameter") or {})}
        ereignisse = lesen.Rohdaten().ereignisse(isin, tag)
        lauf = rechnen.Lauf(isin, tag, p)
        lauf.verarbeiten(ereignisse)
        speicher: dict = {}

        def analyse() -> dict:
            if "a" not in speicher:
                speicher["a"] = rechnen.auswerten(ereignisse, isin, tag, p)
            return speicher["a"]

        def kerzen(tf: str = "1m", anzahl: int = 400) -> list:
            return lesen.kerzen(isin, tf, anzahl)

        S = rechnen.S

        def zeit(ts) -> str:
            ts = ts / S if ts > 1e12 else ts
            return datetime.datetime.fromtimestamp(ts, rechnen.BERLIN).strftime("%H:%M:%S")

        def sekunden(ts) -> int:
            return int(ts // S) if ts > 1e12 else int(ts)

        ausgabe: dict = {}
        raum = {"__name__": "__werkstatt__", "isin": isin, "tag": tag, "ereignisse": ereignisse, "lauf": lauf, "analyse": analyse,
                "kerzen": kerzen, "zeit": zeit, "sekunden": sekunden, "mitte_bei": lambda ts: rechnen._mitte_bei(lauf, ts),
                "ausgabe": ausgabe, "S": S, "KAUF": KAUF, "VERKAUF": VERKAUF, "Trade": Trade, "Quote": Quote,
                "BuchSeite": BuchSeite, "BuchStufe": BuchStufe, "math": math, "statistics": statistics, "collections": collections,
                "bisect": bisect, "itertools": itertools, "datetime": datetime, "json": json, "defaultdict": collections.defaultdict}
        exec(compile(auftrag["code"], "<analyse>", "exec"), raum)
        if isinstance(raum.get("ausgabe"), dict):
            ausgabe = raum["ausgabe"]
        ergebnis = {"ok": True, "ausgabe": begrenzen(ausgabe), "meldungen": len(ereignisse), "stdout": puffer.getvalue()[-GRENZEN["stdout"]:]}
    except Exception:
        text = traceback.format_exc()
        zeilen = [z for z in text.splitlines() if "werkstatt_lauf.py" not in z]      # nur der Teil, der den Code betrifft
        ergebnis = {"ok": False, "fehler": "\n".join(zeilen)[-2500:], "stdout": puffer.getvalue()[-GRENZEN["stdout"]:]}
    finally:
        sys.stdout = echt
    echt.write(json.dumps(ergebnis, ensure_ascii=False, default=str))
    echt.flush()


if __name__ == "__main__":
    main()
