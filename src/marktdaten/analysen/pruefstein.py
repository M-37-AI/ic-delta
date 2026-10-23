"""Prüfstein: Halten Chartformationen, was die Lehrbücher versprechen? Rückblick auf die Tageskerzen der Watchlist.

Ablauf ohne Blick in die Zukunft: Die Erkennung läuft Kerze für Kerze und sieht dabei nur die letzten FENSTER Kerzen
bis zu diesem Tag. Wird eine Formation an diesem Tag (oder bis zu VERSPAETUNG Kerzen vorher, weil Wendepunkte erst
mit Verzögerung feststehen) bestätigt, gilt der Schlusskurs des Erkennungstags als Einstieg. Danach wird gemessen:

  Ziel erreicht   Kursziel der Formation (Measure Rule mit Bulkowskis Anteil) innerhalb von HORIZONT Kerzen,
                  bevor der Ausbruch scheiterte.
  Fehlausbruch    Schlusskurs wieder um mehr als eine halbe ATR auf der alten Seite der Ausbruchslinie, bevor das
                  Ziel erreicht wurde.
  unter 5 %       größte Bewegung in Ausbruchsrichtung kleiner als 5 % (Bulkowskis Definition eines Fehlschlags).
  Rendite         Kursänderung nach 5, 20 und 60 Kerzen in Ausbruchsrichtung (Short-Signale mit umgekehrtem Vorzeichen).
  Basislinie      dieselbe Rendite für jeden beliebigen Tag in dieselbe Richtung; der Vorsprung zeigt, ob die Formation
                  mehr bringt als der allgemeine Trend der Stichprobe.

Grenzen: wenige große US- und deutsche Werte und Bitcoin (deutsche Werte nur ein Jahr Historie, Vorlauf START_KURZ),
überwiegend Bullenmarkt, keine Kosten, kleine Fallzahlen je Formation.
Das Ergebnis beschreibt diese Stichprobe, keine allgemeine Wahrheit.
"""
from __future__ import annotations

import json
import statistics
import threading
import time
from datetime import datetime, timezone

from . import lesen
from .formationen import ARTEN, ZIEL, atr, erkennen

ORT = lesen.DATEN / "analysen"
DATEI = ORT / "pruefstein.json"
FENSTER, HORIZONT, VERSPAETUNG, START, STAERKE = 300, 60, 3, 250, 5
START_KURZ = 120          # Vorlauf für Werte mit kurzer Historie (deutsche Werte: ein Jahr von EODHD); lange Formationen
                          # und das Golden Cross (200 Tage) können dort erst spät oder gar nicht erkannt werden
KERZEN_ARTEN = {"engulfing_bull", "engulfing_bear", "hammer", "shooting_star", "doji"}
LAEUFT = {"seit": None, "fehler": None}


def _r(x, n=2):
    return None if x is None else round(x, n)


def _basis(f: dict, k: list) -> float | None:
    """Ausbruchslinie zum Erkennungstag: aus Ziel und Höhe zurückgerechnet, sonst Endwert der Linie oder Zone."""
    anteil = ZIEL.get(f["art"], {}).get("auf" if f["richtung"] == "bullisch" else "ab")
    if f.get("ziel") is not None and f.get("hoehe") and anteil:
        return f["ziel"] - f["hoehe"] * anteil if f["richtung"] == "bullisch" else f["ziel"] + f["hoehe"] * anteil
    if f["linien"]:
        return f["linien"][0]["p2"]
    return None


def _ergebnis(k: list, t: int, f: dict, a: list) -> dict:
    """Misst den Verlauf nach dem Erkennungstag t."""
    s = 1 if f["richtung"] == "bullisch" else -1
    einstieg = k[t][4]
    basis, ziel = _basis(f, k), f.get("ziel")
    schon = ziel is not None and s * (einstieg - ziel) >= 0          # Ziel lag beim Einstieg schon hinter dem Kurs
    if schon:
        ziel = None                                                   # zählt nicht als Treffer, wird getrennt ausgewiesen
    ende = min(len(k) - 1, t + HORIZONT)
    ziel_tag = fehl_tag = None
    beste = 0.0
    for j in range(t + 1, ende + 1):                                  # wie Bulkowski: Ziel zählt auch nach einem Rücksetzer
        hoch, tief, schluss = k[j][2], k[j][3], k[j][4]
        beste = max(beste, s * ((hoch if s > 0 else tief) / einstieg - 1))
        if ziel is not None and ziel_tag is None and ((s > 0 and hoch >= ziel) or (s < 0 and tief <= ziel)):
            ziel_tag = j - t
        if basis is not None and fehl_tag is None and s * (schluss - basis) < -0.5 * a[j]:
            fehl_tag = j - t
    fehl = None if basis is None else (fehl_tag is not None and (ziel_tag is None or fehl_tag < ziel_tag))
    rendite = {h: _r(s * (k[t + h][4] / einstieg - 1) * 100) if t + h < len(k) else None for h in (5, 20, 60)}
    return {"ziel_beim_einstieg": schon, "ziel_erreicht": None if ziel is None else ziel_tag is not None, "tage_bis_ziel": ziel_tag,
            "fehlausbruch": fehl, "tage_bis_fehl": fehl_tag, "ziel_abstand_pct": _r(s * (ziel / einstieg - 1) * 100) if ziel is not None else None,
            "beste_pct": _r(beste * 100), "unter_5": beste < 0.05, "r5": rendite[5], "r20": rendite[20], "r60": rendite[60],
            "vollstaendig": t + HORIZONT < len(k)}


def wert_pruefen(w: dict) -> tuple[list[dict], dict, dict]:
    k = lesen.kerzen(w["id"], "1d", 5000)
    ereignisse, gesehen = [], set()
    start = START if len(k) >= START + 30 else START_KURZ
    info = {"kerzen": len(k), "start": start, "kurz": start != START}
    if len(k) < start + 30:
        return [], {}, info
    a = atr(k)
    for t in range(start, len(k)):
        fenster = k[max(0, t - FENSTER + 1):t + 1]
        for f in erkennen(fenster, STAERKE):
            if f["status"] not in ("bestätigt", "gebrochen") or f["richtung"] not in ("bullisch", "bärisch") or not f.get("ereignis_t"):
                continue
            verspaetung = sum(1 for x in fenster if x[0] > f["ereignis_t"])
            if verspaetung > VERSPAETUNG:
                continue
            schluessel = (f["schluessel"], f["richtung"]) if f["art"] not in KERZEN_ARTEN | {"hoch_100", "tief_100", "rsi_ueber70", "rsi_unter30", "golden_cross", "death_cross"} \
                else (f["art"], f["ereignis_t"])     # eine Formation zählt einmal, auch wenn ihr Ausbruchstag weiterwandert
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            ereignisse.append({"symbol": w["symbol"], "wert": w["id"], "t": k[t][0], "ausbruch_t": f["ereignis_t"], "verspaetung": verspaetung,
                               "art": f["art"], "name": ARTEN.get(f["art"], f["art"]), "richtung": f["richtung"], "guete": f.get("guete"),
                               "volumen": f.get("volumen"), "einstieg": k[t][4], "ziel": f.get("ziel"), "hoehe": f.get("hoehe"),
                               "basis": _r(_basis(f, k), 4), "formation": f, **_ergebnis(k, t, f, a)})
    # Basislinie: jeder Tag ab dem Vorlauf, Rendite nach 5/20/60 Kerzen (long)
    basis = {}
    for h in (5, 20, 60):
        werte = [(k[t + h][4] / k[t][4] - 1) * 100 for t in range(start, len(k) - h)]
        basis[h] = (sum(werte), len(werte))
    return ereignisse, basis, info


def _quote(liste, schluessel):
    werte = [e[schluessel] for e in liste if e[schluessel] is not None]
    return (_r(sum(werte) / len(werte) * 100, 1), len(werte)) if werte else (None, 0)


def _mittel(liste, schluessel):
    werte = [e[schluessel] for e in liste if e[schluessel] is not None]
    return _r(statistics.mean(werte)) if werte else None


def auswerten(ereignisse: list[dict], basis_summen: dict) -> dict:
    basis = {h: (s / n if n else 0.0) for h, (s, n) in basis_summen.items()}
    gruppen: dict[tuple, list] = {}
    for e in ereignisse:
        gruppen.setdefault((e["art"], e["richtung"]), []).append(e)
    zeilen = []
    for (art, richtung), liste in gruppen.items():
        s = 1 if richtung == "bullisch" else -1
        ziel_q, ziel_n = _quote(liste, "ziel_erreicht")
        fehl_q, _ = _quote(liste, "fehlausbruch")
        r20 = _mittel(liste, "r20")
        zeilen.append({"art": art, "name": ARTEN.get(art, art), "richtung": richtung, "n": len(liste), "ziel_quote": ziel_q, "ziel_n": ziel_n,
                       "ziel_schon": sum(e["ziel_beim_einstieg"] for e in liste), "ziel_abstand": _mittel(liste, "ziel_abstand_pct"),
                       "fehl_quote": fehl_q, "unter5_quote": _r(sum(e["unter_5"] for e in liste) / len(liste) * 100, 1),
                       "r5": _mittel(liste, "r5"), "r20": r20, "r60": _mittel(liste, "r60"),
                       "r20_median": _r(statistics.median([e["r20"] for e in liste if e["r20"] is not None])) if any(e["r20"] is not None for e in liste) else None,
                       "basis_r20": _r(s * basis[20]), "vorsprung_r20": _r(r20 - s * basis[20]) if r20 is not None else None,
                       "tage_bis_ziel": _r(statistics.median([e["tage_bis_ziel"] for e in liste if e["tage_bis_ziel"]]), 0) if any(e["tage_bis_ziel"] for e in liste) else None,
                       "bulkowski_anteil": ZIEL.get(art, {}).get("auf" if richtung == "bullisch" else "ab"),
                       "kerzenmuster": art in KERZEN_ARTEN})
    zeilen.sort(key=lambda z: (z["kerzenmuster"], -z["n"]))
    # Wirkt die Güte? Wirkt das Volumen beim Ausbruch? (nur Formationen mit Kursziel)
    mit_ziel = [e for e in ereignisse if e["ziel_erreicht"] is not None]
    def klasse(liste):
        q, n = _quote(liste, "ziel_erreicht")
        f, _ = _quote(liste, "fehlausbruch")
        return {"n": n, "ziel_quote": q, "fehl_quote": f, "r20": _mittel(liste, "r20")}
    ohne_flaggen = [e for e in mit_ziel if not e["art"].startswith(("flagge", "wimpel"))]   # Flaggen haben sehr nahe Ziele und verzerren
    guete = [{"klasse": name, **klasse([e for e in ohne_flaggen if e["guete"] is not None and lo <= e["guete"] < hi])}
             for name, lo, hi in (("unter 60", 0, 60), ("60 bis 79", 60, 80), ("ab 80", 80, 101))]
    volumen = [{"klasse": name, **klasse([e for e in ohne_flaggen if e["volumen"] is not None and lo <= e["volumen"] < hi])}
               for name, lo, hi in (("unter 1,0×", 0, 1.0), ("1,0 bis 1,5×", 1.0, 1.5), ("ab 1,5×", 1.5, 1e9))]
    alle_q, alle_n = _quote(mit_ziel, "ziel_erreicht")
    return {"zeilen": zeilen, "guete": guete, "volumen": volumen, "basis_pct": {str(h): _r(v) for h, v in basis.items()},
            "gesamt": {"ereignisse": len(ereignisse), "mit_ziel": alle_n, "ziel_quote": alle_q, "fehl_quote": _quote(mit_ziel, "fehlausbruch")[0]}}


def rechnen() -> dict:
    t0 = time.time()
    alle, basis_summen, werte = [], {5: [0.0, 0], 20: [0.0, 0], 60: [0.0, 0]}, []
    for w in lesen.watchlist():
        ereignisse, basis, info = wert_pruefen(w)
        if not basis:
            continue
        werte.append({"symbol": w["symbol"], "name": w["name"], "markt": w.get("markt"), "ereignisse": len(ereignisse), **info})
        alle += ereignisse
        for h, (s, n) in basis.items():
            basis_summen[h][0] += s
            basis_summen[h][1] += n
    erg = auswerten(alle, {h: tuple(v) for h, v in basis_summen.items()})
    erg.update(stand=datetime.now(timezone.utc).isoformat(timespec="seconds"), dauer_s=round(time.time() - t0, 1), werte=werte,
               regeln={"fenster": FENSTER, "horizont": HORIZONT, "verspaetung": VERSPAETUNG, "staerke": STAERKE, "zeitrahmen": "1d"},
               ereignisse=[{k_: v for k_, v in e.items()} for e in sorted(alle, key=lambda e: -e["t"])])
    ORT.mkdir(parents=True, exist_ok=True)
    tmp = DATEI.with_suffix(".tmp")
    tmp.write_text(json.dumps(erg, ensure_ascii=False))
    tmp.replace(DATEI)
    return erg


def lesen_ergebnis(mit_ereignissen: bool = True) -> dict | None:
    try:
        d = json.loads(DATEI.read_text())
    except (OSError, ValueError):
        return None
    if not mit_ereignissen:
        d.pop("ereignisse", None)
    return d


def starten() -> dict:
    if LAEUFT["seit"]:
        return {"laeuft": True}
    LAEUFT.update(seit=datetime.now(timezone.utc).isoformat(timespec="seconds"), fehler=None)

    def lauf():
        try:
            rechnen()
        except Exception as e:
            LAEUFT["fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            LAEUFT["seit"] = None
    threading.Thread(target=lauf, daemon=True, name="pruefstein").start()
    return {"laeuft": True}


def kurz_je_art() -> dict:
    """Für die Chartkarten: Zielquote und Fallzahl je Formationsart und Richtung."""
    d = lesen_ergebnis(False)
    if not d:
        return {}
    return {f"{z['art']}|{z['richtung']}": {"ziel_quote": z["ziel_quote"], "n": z["n"], "ziel_n": z["ziel_n"], "fehl_quote": z["fehl_quote"]} for z in d["zeilen"]}
