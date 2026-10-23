"""Vorauswahl für die Startseite von Analysen: die auffälligsten Charts der Watchlist mit Formationen,
Alarmvorschlägen und (vom Agenten) einer kurzen Einordnung durch Claude.

Auffälligkeit ist eine Rangfolge für die Aufmerksamkeit, kein Handelssignal: junge Bestätigungen und
Brüche zählen am meisten, offene Formationen weniger, dazu kommt, wie nah der Kurs an einer Linie steht.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone

from . import lesen
from .alarme import _linie_wert, _preis as _p, agent, bedingung_text
from .formationen import ARTEN, TF, atr, erkennen, pivots, rsi, sma

ORT = lesen.DATEN / "analysen"
CLAUDE = ORT / "charts_claude.json"
WAHL = (("1d", 150), ("1h", 150), ("15m", 100), ("5m", 100))     # erster Zeitrahmen mit genug Kerzen
GEWICHT = {"sks": 1.0, "sks_invers": 1.0, "doppelhoch": 1.0, "doppeltief": 1.0, "dreifachhoch": 1.0, "dreifachtief": 1.0, "tasse_henkel": 1.0,
           "dreieck_auf": 1.0, "dreieck_ab": 1.0, "dreieck_sym": 1.0, "keil_steigend": 1.0, "keil_fallend": 1.0, "rechteck": 0.95,
           "kanal_auf": 0.9, "kanal_ab": 0.9, "flagge_bull": 0.95, "flagge_bear": 0.95, "wimpel_bull": 0.95, "wimpel_bear": 0.95,
           "trendlinie": 0.85, "unterstuetzung": 0.8, "widerstand": 0.8,
           "hoch_100": 0.7, "tief_100": 0.7, "golden_cross": 0.7, "death_cross": 0.7, "rsi_ueber70": 0.5, "rsi_unter30": 0.5}
MIT_ZIEL = {"sks", "sks_invers", "doppelhoch", "doppeltief", "dreifachhoch", "dreifachtief", "tasse_henkel", "dreieck_auf", "dreieck_ab",
            "dreieck_sym", "keil_steigend", "keil_fallend", "rechteck", "kanal_auf", "kanal_ab", "flagge_bull", "flagge_bear", "wimpel_bull", "wimpel_bear"}
_cache = {"t": 0.0, "daten": None}
_lock = threading.Lock()
LAEUFT = {"seit": None, "fehler": None}


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def zeitrahmen(wert: str) -> tuple[str, list]:
    beste = ("15m", [])
    for tf, mindest in WAHL:
        k = lesen.kerzen(wert, tf, 400)
        if len(k) >= mindest:
            return tf, k
        if len(k) > len(beste[1]) * 1.5:
            beste = (tf, k)
    return beste


def _bewerten(f: dict, k: list, a_letzt: float, tf: str) -> dict:
    """Ergänzt eine Formation um Punkte für die Rangfolge, Abstand zur Linie (in ATR) und rechnerisches Ziel."""
    n, kurs = len(k), k[-1][4]
    alter = None
    if f.get("ereignis_t"):
        alter = n - 1 - next((i for i in range(n - 1, -1, -1) if k[i][0] <= f["ereignis_t"]), 0)
    if f["status"] in ("bestätigt", "gebrochen"):
        basis = 55 if alter is not None and alter <= 3 else 45 if alter is not None and alter <= 10 else 30
    elif f["status"] == "bildet sich":
        basis = 32
    else:
        basis = 18
    gewicht = GEWICHT.get(f["art"], 0.4)
    marken = []
    for l in f["linien"]:
        w = _linie_wert({"tf": tf, **l}, k)
        if w is not None:
            marken.append((w, l))
    if f.get("zone"):
        marken += [(f["zone"][0], {"art": "unterstuetzung"}), (f["zone"][1], {"art": "widerstand"})]
    naehe = min((abs(kurs - w) / a_letzt for w, _ in marken), default=None) if a_letzt else None
    bonus = 0 if naehe is None else 25 if naehe < 0.5 else 15 if naehe < 1 else 6 if naehe < 2 else 0
    ziel = f.get("ziel")                                  # Kursziel nach Bulkowski aus der Erkennung
    if ziel is None and f["art"] in MIT_ZIEL and f["punkte"] and marken and f.get("hoehe") is None:
        preise = [p for _, p in f["punkte"]]
        hoehe = max(preise) - min(preise)
        bull = f["richtung"] == "bullisch" or (f["richtung"] == "neutral" and kurs >= marken[0][0])
        bezug = max(w for w, _ in marken) if bull else min(w for w, _ in marken)
        ziel = round(bezug + hoehe if bull else bezug - hoehe, 4)
    guete = f.get("guete")
    faktor = 0.6 + 0.4 * guete / 100 if guete is not None else 0.8   # saubere Formationen nach vorn
    return {**f, "punkte_rang": round(basis * gewicht * faktor + bonus, 1), "naehe_atr": None if naehe is None else round(naehe, 2),
            "alter_kerzen": alter, "ziel": ziel, "art_name": ARTEN.get(f["art"], f["art"])}


# --- Indikatorsignale ------------------------------------------------------------------------------------------
def _ema(werte: list[float], n: int) -> list[float]:
    aus, e = [], None
    for w in werte:
        e = w if e is None else w * 2 / (n + 1) + e * (n - 1) / (n + 1)
        aus.append(e)
    return aus


def indikator_signale(k: list) -> list[dict]:
    """Lage und frische Signale der üblichen Indikatoren auf der letzten Kerze. frisch = Signal in den letzten Kerzen.
    richtung: bullisch, bärisch oder neutral. Für sich genommen schwach; interessant, wenn sie eine Formation stützen."""
    n, c = len(k), [x[4] for x in k]
    if n < 30:
        return []
    aus = []

    def neu(art, name, wert, richtung, text, frisch=False):
        aus.append({"art": art, "name": name, "wert": wert, "richtung": richtung, "text": text, "frisch": frisch})

    zahl = lambda x, d=2: f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    # Gleitende Durchschnitte: Lage und Kreuzungen
    s50, s200 = sma(c, 50), sma(c, 200)
    if s50[-1] is not None:
        if s200[-1] is not None:
            kreuz = next((i for i in range(n - 1, max(n - 11, 200), -1) if (s50[i] - s200[i]) * (s50[i - 1] - s200[i - 1]) < 0), None)
            if kreuz is not None:
                gold = s50[kreuz] > s200[kreuz]
                neu("ma_kreuz", "Golden Cross" if gold else "Death Cross", f"vor {n - 1 - kreuz} Kerzen", "bullisch" if gold else "bärisch",
                    "SMA 50 hat den SMA 200 " + ("von unten nach oben" if gold else "von oben nach unten") + " gekreuzt", True)
            ueber = c[-1] > s50[-1] and c[-1] > s200[-1]
            unter = c[-1] < s50[-1] and c[-1] < s200[-1]
            neu("ma_lage", "Trend (SMA 50/200)", "über beiden" if ueber else "unter beiden" if unter else "dazwischen",
                "bullisch" if ueber and s50[-1] > s200[-1] else "bärisch" if unter and s50[-1] < s200[-1] else "neutral",
                f"Kurs {zahl(c[-1])}, SMA 50 {zahl(s50[-1])}, SMA 200 {zahl(s200[-1])}")
        else:
            neu("ma_lage", "Trend (SMA 50)", "darüber" if c[-1] > s50[-1] else "darunter", "bullisch" if c[-1] > s50[-1] else "bärisch",
                f"Kurs {zahl(c[-1])}, SMA 50 {zahl(s50[-1])} (für SMA 200 zu wenig Kerzen)")
    # RSI 14: Zone, Rückkehr aus der Zone, Divergenz an den letzten zwei Hochs bzw. Tiefs
    r = rsi(c)
    if r[-1] is not None:
        zurueck = next((i for i in range(n - 1, n - 6, -1) if r[i - 1] is not None and ((r[i - 1] > 70 >= r[i]) or (r[i - 1] < 30 <= r[i]))), None)
        if zurueck is not None:
            von_oben = r[zurueck - 1] > 70
            neu("rsi_zurueck", "RSI verlässt " + ("Überkauft" if von_oben else "Überverkauft"), zahl(r[-1], 0), "bärisch" if von_oben else "bullisch",
                f"RSI fiel unter 70 zurück" if von_oben else "RSI stieg über 30 zurück", True)
        else:
            neu("rsi", "RSI 14", zahl(r[-1], 0), "bärisch" if r[-1] >= 70 else "bullisch" if r[-1] <= 30 else "neutral",
                "überkauft (≥ 70)" if r[-1] >= 70 else "überverkauft (≤ 30)" if r[-1] <= 30 else "neutral zwischen 30 und 70")
        piv = pivots(k, 3)
        for typ, art, name, richtung in (("H", "div_baer", "Bärische RSI-Divergenz", "bärisch"), ("L", "div_bull", "Bullische RSI-Divergenz", "bullisch")):
            pts = [(i, p) for i, t, p in piv if t == typ and r[i] is not None][-2:]
            if len(pts) == 2 and pts[1][0] >= n - 12:
                (i1, p1), (i2, p2) = pts
                if (typ == "H" and p2 > p1 and r[i2] < r[i1] - 2) or (typ == "L" and p2 < p1 and r[i2] > r[i1] + 2):
                    neu(art, name, f"{zahl(r[i1], 0)} → {zahl(r[i2], 0)}", richtung,
                        ("Kurs mit höherem Hoch, RSI mit tieferem" if typ == "H" else "Kurs mit tieferem Tief, RSI mit höherem") + " – nachlassende Kraft", True)
    # Bollinger-Bänder 20/2: Lage, Ausbruch aus dem Band, Squeeze
    if n >= 40:
        bb = []
        for i in range(19, n):
            fenster = c[i - 19:i + 1]
            m = sum(fenster) / 20
            sd = (sum((x - m) ** 2 for x in fenster) / 20) ** 0.5
            bb.append((m, m + 2 * sd, m - 2 * sd, 4 * sd / m if m else 0))
        m, oben, unten, breite = bb[-1]
        pb = (c[-1] - unten) / (oben - unten) if oben > unten else 0.5
        breiten = [x[3] for x in bb[-120:]]
        if breite <= min(breiten) * 1.05 and len(breiten) >= 60:
            neu("bb_squeeze", "Bollinger-Squeeze", f"Breite {zahl(breite * 100, 1)} %", "neutral",
                f"Bänder so eng wie seit {len(breiten)} Kerzen nicht – oft Ruhe vor einer größeren Bewegung", True)
        if c[-1] > oben:
            neu("bb_ausbruch", "Über dem oberen Band", f"%B {zahl(pb, 2)}", "bullisch", "Schluss über dem oberen Bollinger-Band: starker Schub, aber überdehnt", True)
        elif c[-1] < unten:
            neu("bb_ausbruch", "Unter dem unteren Band", f"%B {zahl(pb, 2)}", "bärisch", "Schluss unter dem unteren Bollinger-Band: starker Druck, aber überdehnt", True)
        else:
            neu("bb", "Bollinger %B", zahl(pb, 2), "neutral", "Lage im Band (0 = unteres, 1 = oberes Band)")
    # MACD 12/26/9: frisches Kreuz der Signallinie, sonst Lage
    if n >= 40:
        e12, e26 = _ema(c, 12), _ema(c, 26)
        macd = [a - b for a, b in zip(e12, e26)]
        sig = _ema(macd, 9)
        hist = [a - b for a, b in zip(macd, sig)]
        kreuz = next((i for i in range(n - 1, n - 6, -1) if hist[i] * hist[i - 1] < 0), None)
        if kreuz is not None:
            auf = hist[kreuz] > 0
            neu("macd_kreuz", "MACD kreuzt " + ("nach oben" if auf else "nach unten"), f"vor {n - 1 - kreuz} Kerzen", "bullisch" if auf else "bärisch",
                "MACD-Linie kreuzt die Signallinie" + (" unterhalb der Nulllinie" if auf and macd[-1] < 0 else " oberhalb der Nulllinie" if not auf and macd[-1] > 0 else ""), True)
        else:
            neu("macd", "MACD", "über Signal" if hist[-1] > 0 else "unter Signal", "bullisch" if hist[-1] > 0 else "bärisch",
                "Histogramm " + ("wächst" if abs(hist[-1]) > abs(hist[-2]) else "schrumpft"))
    # Volumen
    vor = [x[5] for x in k[-21:-1] if x[5]]
    if vor and k[-1][5]:
        f = k[-1][5] / (sum(vor) / len(vor))
        if f >= 2:
            neu("volumen", "Volumenspitze", f"{zahl(f, 1)}×", "bullisch" if k[-1][4] >= k[-1][1] else "bärisch",
                "Volumen der letzten Kerze weit über dem 20-Kerzen-Schnitt", True)
    return aus


def _vorschlaege(w: dict, tf: str, formationen: list, k: list) -> list[dict]:
    """Fertige Alarmregeln aus den Formationen, die die Seite mit einem Klick anlegen kann."""
    aus, gesehen, kurs = [], set(), k[-1][4]

    def neu(titel, regel, fi):
        regel = {"wert": w["id"], "tf": tf, "quelle": "charts", **regel}
        schluessel = json.dumps({k: v for k, v in regel.items() if k not in ("notiz", "quelle")}, sort_keys=True)
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            aus.append({"titel": titel, "regel": regel, "formation": fi, "bedingung": bedingung_text({"art": regel["art"], **regel, "symbol": w["symbol"]})})

    # Richtung immer zur Gegenseite des aktuellen Kurses: über einer Linie heißt der Alarm "fällt darunter".
    # Nach einer Bestätigung ist das der Rückfall (Fehlausbruch), vorher der Bruch.
    for fi, f in enumerate(formationen):
        name, bestaetigt = f["name"], f["status"] in ("bestätigt", "gebrochen")
        if f.get("zone") and (f["naehe_atr"] is None or f["naehe_atr"] < 3):
            stuetze = f["art"] == "unterstuetzung"
            ueber = kurs > f["zone"][1] or (stuetze and kurs >= f["zone"][0])     # im Bereich: Unterstützung bricht nach unten, Widerstand nach oben
            neu(f"{f['art_name']} {_p(f['zone'][0])}–{_p(f['zone'][1])} {'nach unten' if ueber else 'nach oben'} verlassen",
                {"art": "zone", "lo": f["zone"][0], "hi": f["zone"][1], "richtung": "unter" if ueber else "ueber", "notiz": name}, fi)
        linien = [(l, _linie_wert({"tf": tf, **l}, k)) for l in f["linien"]]
        linien = [(l, w_) for l, w_ in linien if w_ is not None]
        if bestaetigt and linien:
            linien = [min(linien, key=lambda x: abs(kurs - x[1]))]       # nur die Ausbruchslinie
        erwartet = "ueber" if f["richtung"] == "bullisch" else "unter" if f["richtung"] == "bärisch" else None
        gescheitert = False
        for l, wert_jetzt in linien:
            ueber = kurs >= wert_jetzt
            seite = "ueber" if ueber else "unter"
            teil = {"widerstand": "obere Linie", "unterstuetzung": "untere Linie", "nackenlinie": "Nackenlinie"}.get(l["art"], "Linie") \
                if len(f["linien"]) > 1 or l["art"] == "nackenlinie" else "Linie"
            dativ = {"obere Linie": "oberen Linie", "untere Linie": "unteren Linie"}.get(teil, teil)
            if bestaetigt and erwartet and seite != erwartet:                 # Ausbruch ist gescheitert, Kurs wieder auf der alten Seite
                gescheitert = True
                titel = f"erneuter Ausbruch {'über' if erwartet == 'ueber' else 'unter'} die {teil} ({_p(wert_jetzt)})"
            elif bestaetigt:
                titel = f"Rückfall {'unter' if ueber else 'über'} die {teil} ({_p(wert_jetzt)})"
            else:
                titel = f"Bruch der {dativ} {'nach unten' if ueber else 'nach oben'} ({_p(wert_jetzt)})"
            neu(f"{f['art_name']}: {titel}", {"art": "linie", "t1": l["t1"], "p1": l["p1"], "t2": l["t2"], "p2": l["p2"],
                                               "richtung": "unter" if ueber else "ueber", "notiz": name}, fi)
        ziel = f.get("ziel")
        if ziel and bestaetigt and not gescheitert and ((ziel > kurs) == (erwartet != "unter")) and abs(ziel - kurs) > 0.001 * kurs:
            neu(f"{f['art_name']}: rechnerisches Ziel {_p(ziel)} erreicht", {"art": "preis", "preis": ziel, "richtung": "ueber" if ziel > kurs else "unter",
                                                                             "notiz": f"Ziel aus {name} (Formationshöhe)"}, fi)
        if f["art"] in MIT_ZIEL and f["status"] == "bildet sich":
            neu(f"{f['art_name']} wird bestätigt", {"art": "formation", "arten": [f["art"]], "notiz": name}, fi)
    return aus[:6]


def wert_auswerten(w: dict) -> dict | None:
    tf, k = zeitrahmen(w["id"])
    if len(k) < 30:
        return None
    a = atr(k)
    a_letzt = a[-1] if a else 0
    staerke = 5 if len(k) >= 150 else 3
    formationen = sorted((_bewerten(f, k, a_letzt, tf) for f in erkennen(k, staerke)), key=lambda f: -f["punkte_rang"])
    wichtige = [f for f in formationen if f["art"] not in ("doji",)][:5]
    rang = (wichtige[0]["punkte_rang"] + 4 * min(3, sum(f["punkte_rang"] >= 20 for f in wichtige[1:]))) if wichtige else 0
    signale = indikator_signale(k)
    leit = next((f["richtung"] for f in wichtige if f["richtung"] != "neutral"), None)
    gerichtet = [s_ for s_ in signale if s_["richtung"] != "neutral"]
    stuetzen = sum(s_["richtung"] == leit for s_ in gerichtet) if leit else 0
    frische = sum(s_["frisch"] for s_ in signale)
    rang += min(9, 3 * frische) + (2 * stuetzen if leit else 0)     # frische Signale und Bestätigung der Formation
    erster, letzter = k[-min(len(k), 60)][4], k[-1][4]
    return {"wert": w, "tf": tf, "staerke": staerke, "kerzen": k[-260:], "sichtbar": 140, "kerzen_gesamt": len(k), "kurs": letzter, "zeit": k[-1][0],
            "veraenderung_pct": round((letzter / erster - 1) * 100, 2) if erster else None, "atr": round(a_letzt, 4),
            "auffaelligkeit": min(100, round(rang)), "formationen": wichtige, "vorschlaege": _vorschlaege(w, tf, wichtige, k),
            "signale": signale, "bestaetigung": {"richtung": leit, "stuetzen": stuetzen, "gerichtet": len(gerichtet)} if leit else None}


def uebersicht(neu: bool = False) -> dict:
    with _lock:
        if not neu and _cache["daten"] and time.time() - _cache["t"] < 60:
            daten = _cache["daten"]
        else:
            werte = [x for x in (wert_auswerten(w) for w in lesen.watchlist()) if x]
            werte.sort(key=lambda x: -x["auffaelligkeit"])
            daten = {"stand": _jetzt(), "werte": werte}
            _cache.update(t=time.time(), daten=daten)
    from .pruefstein import kurz_je_art
    return {**daten, "claude": claude_lesen(), "laeuft": LAEUFT, "pruefstein": kurz_je_art()}


# --- Einordnung durch Claude ------------------------------------------------------------------------------------
def claude_lesen() -> dict | None:
    try:
        return json.loads(CLAUDE.read_text())
    except (OSError, ValueError):
        return None


def _auftrag(werte: list[dict]) -> str:
    kurz = []
    for x in werte:
        kurz.append({"symbol": x["wert"]["symbol"], "name": x["wert"]["name"], "markt": x["wert"]["markt"], "tf": x["tf"], "kurs": x["kurs"],
                     "veraenderung_60_kerzen_pct": x["veraenderung_pct"], "atr": x["atr"], "auffaelligkeit": x["auffaelligkeit"],
                     "formationen": [{"name": f["name"], "richtung": f["richtung"], "status": f["status"], "hinweis": f.get("text") or None, "guete": f.get("guete"),
                                      "volumen_ausbruch": f.get("volumen"), "abstand_zur_linie_atr": f["naehe_atr"], "ziel": f["ziel"],
                                      "ziel_regel": f.get("ziel_text") or None, "alter_kerzen": f["alter_kerzen"]} for f in x["formationen"]],
                     "indikatoren": [f"{s_['name']}: {s_['wert']} ({s_['richtung']}{', frisch' if s_['frisch'] else ''})" for s_ in x.get("signale", [])],
                     "alarmvorschlaege": [f"{i}: {v['titel']}" for i, v in enumerate(x["vorschlaege"])]})
    return ("Chartanalyse für die Startseite von Analysen. Unten stehen die fünf auffälligsten Charts der Watchlist mit den Formationen der "
            "Regel-Erkennung (dieselbe wie detect_patterns) und fertigen Alarmvorschlägen.\n"
            "Für jeden Wert: Hol get_candles (gleicher tf, 120 Kerzen), bei deutschen Werten zusätzlich get_dossier. Schreib eine Chartanalyse in "
            "zwei bis drei Sätzen: Trend, die wichtigste Formation und ob sie trägt (Güte, Berührungen, Volumen beim Ausbruch, Abstand), ob die Indikatoren sie "
            "stützen oder widersprechen, die entscheidende Marke mit Preis. Der erste Satz ist das Wichtigste (er steht in der Karte vorn). "
            "Keine Handlungsempfehlung. Wähle den sinnvollsten Alarmvorschlag (Index) oder null und sag in einem Halbsatz warum. "
            "Nennst du eine Tendenz, trag sie mit note_expectation ein (horizont 1d bei 1d, sonst 4h).\n"
            "Antworte am Ende nur mit einem JSON-Block:\n```json\n{\"werte\": {\"SYMBOL\": {\"text\": \"…\", \"alarm\": 0, \"warum\": \"…\", "
            "\"tendenz\": \"aufwaerts|abwaerts|seitwaerts\"}}}\n```\n\nDaten:\n" + json.dumps(kurz, ensure_ascii=False))


def _json_aus(text: str) -> dict:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("keine JSON-Antwort")
    return json.loads(m.group(1))


def claude_starten() -> dict:
    if LAEUFT["seit"]:
        return {"laeuft": True}
    LAEUFT.update(seit=_jetzt(), fehler=None)

    def lauf():
        try:
            werte = uebersicht(neu=True)["werte"][:5]
            antwort = agent("/chartanalyse", {"text": _auftrag(werte)}, timeout=420)
            if antwort.get("fehler"):
                raise RuntimeError(antwort["fehler"])
            d = _json_aus(antwort.get("text", ""))
            ergebnis = {"zeit": _jetzt(), "modell": antwort.get("modell"), "kosten": antwort.get("kosten"), "werte": d.get("werte", {}),
                        "stand_daten": {x["wert"]["symbol"]: x["zeit"] for x in werte}}
            ORT.mkdir(parents=True, exist_ok=True)
            tmp = CLAUDE.with_suffix(".tmp")
            tmp.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=1))
            tmp.replace(CLAUDE)
        except Exception as e:
            LAEUFT["fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            LAEUFT["seit"] = None
    threading.Thread(target=lauf, daemon=True, name="chartanalyse").start()
    return {"laeuft": True}
