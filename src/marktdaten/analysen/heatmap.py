"""Liquiditäts-Heatmap: Orderbuch je Preisstufe im Zeitverlauf, für iC Charts (Ebene hinter den Kerzen).

Krypto: Tabelle buch_tiefe des Dashboards (siehe krypto()). Deutsche Werte: Quelle sind die Xetra-Rohdateien (pretrade), die das Dashboard beim Sammeln ablegt; hier wird nichts abgerufen.
Xetra veröffentlicht die Daten 15 Minuten verzögert und nur die besten 5 Stufen je Seite.

Rechnung: Das Buch wird Ereignis für Ereignis nachgebaut und jede Sekunde abgetastet. Je Zeitfach (fach Sekunden) und
Preisstufe steht die mittlere Menge über die Sekunden mit gültigem Buch. Bevorzugt wird das reguläre Buch, sonst das
Retail-Buch (abends gibt es nur dieses). Ohne neue Buchmeldung gilt der letzte Stand höchstens STALE Sekunden.

Wände: Eine Stufe mit mindestens WAND_FAKTOR-mal der mittleren Stufenmenge ihrer Seite, die mindestens WAND_MIN_S
Sekunden steht. Ihr Ende wird eingeordnet:
  abgearbeitet    an diesem Preis wurde mindestens die Hälfte der Wand gehandelt
  zurückgezogen   verschwunden, obwohl der Preis noch im sichtbaren Bereich der 5 Stufen lag, ohne nennenswerten Handel
  aus dem Blick   der Kurs hat sich entfernt, die Stufe liegt jenseits der 5 sichtbaren Stufen (Ende unbekannt)
  steht           am Ende des Zeitraums noch da
"zurückgezogen" mit kleinem Abstand zum Kurs ist das Muster, das man bei Spoofing erwartet; ein Beweis ist es nicht.
"""
from __future__ import annotations

import statistics
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import ports
from ..buch import Orderbuch
from ..modell import KAUF, VERKAUF, BuchSeite, BuchStufe, Trade
from . import lesen

BERLIN = ZoneInfo("Europe/Berlin")
FAECHER = (5, 10, 15, 30, 60, 300, 900)
STALE = 600
WAND_FAKTOR, WAND_MIN_S, WAND_PAUSE_S = 4.0, 20, 15   # Pause: so lange darf eine Wand fehlen und gilt bei Rückkehr als dieselbe
MAX_TAGE = 5
_cache: dict = {}
_sperre = threading.Lock()


def _r(x: float, n: int = 4) -> float:
    return round(x, n)


def tag_rechnen(isin: str, tag: str, fach: int, roh: lesen.Rohdaten | None = None) -> dict:
    """Heatmap und Wände eines Werts an einem Tag (Ordner der Rohablage). Zwischengespeichert, bis neue Dateien kommen."""
    roh = roh or lesen.Rohdaten()
    stand = roh.stand(tag)
    with _sperre:
        c = _cache.get((isin, tag, fach))
        if c and c[0] == stand and c[1] is roh.ort:
            return c[2]
    erg = _aus_signalraster(isin, tag, fach)          # schneller Weg: Sekundenraster der Signal-Schicht (dort zwischengespeichert)
    if erg is None:
        erg = _rechnen(roh.ereignisse(isin, tag), isin, fach)
    with _sperre:
        if len(_cache) > 200:
            _cache.clear()
        _cache[(isin, tag, fach)] = (stand, roh.ort, erg)
    return erg


def _aus_signalraster(isin: str, tag: str, fach: int) -> dict | None:
    """Spalten aus dem Sekundenraster der Signal-Schicht (regulärer Handel, 5 Stufen je Seite mit Preisen). Das Raster
    entsteht dort ohnehin für die Signale und wird je Wert und Tag zwischengespeichert; so kostet die Heatmap keinen
    zweiten Durchlauf über die Rohdateien. None, wenn es (noch) kein Raster gibt, dann rechnet _rechnen wie bisher."""
    from . import signale                               # hier importiert, weil signale selbst _waende aus diesem Modul nutzt
    try:
        raster, extras = signale._lauf_de(isin, tag)
    except Exception:
        return None
    if not raster or not extras.get("tick"):
        return None
    faecher: dict[int, dict] = {}
    for r in raster:
        f = faecher.get(r["t"] - r["t"] % fach)
        if f is None:
            f = faecher[r["t"] - r["t"] % fach] = {KAUF: defaultdict(float), VERKAUF: defaultdict(float), "n": 0}
        for p, m in zip(r["bp"], r["b"]):
            if m:
                f[KAUF][p] += m
        for p, m in zip(r["ap"], r["a"]):
            if m:
                f[VERKAUF][p] += m
        f["n"] += 1
    spalten = []
    for t in sorted(faecher):
        f = faecher[t]
        n = f["n"]
        spalten.append({"t": t, "b": sorted(((p, _r(m / n, 2)) for p, m in f[KAUF].items()), reverse=True),
                        "a": sorted((p, _r(m / n, 2)) for p, m in f[VERKAUF].items()), "retail": False})
    return {"spalten": spalten, "waende": extras.get("waende", []), "tick": extras["tick"]}


def _rechnen(ereignisse: list, isin: str, fach: int) -> dict:
    buch = Orderbuch()
    kx, kr = ("XETR", isin, "regulaer"), ("XETR", isin, "retail")
    letzte = {kx: None, kr: None}                      # Zeit der letzten Buchmeldung (s)
    faecher: dict[int, dict] = {}                      # Fachbeginn -> {"B": {preis: summe}, "A": {...}, "n": Sekunden, "r": Retail-Sekunden}
    trades_an: dict[float, list[tuple[int, float]]] = defaultdict(list)
    preise: set[float] = set()
    proben: list[tuple[int, dict, dict]] = []          # (Sekunde, bid {p: m}, ask {p: m}) für die Wände

    def abtasten(bis_s: int, ab_s: int) -> None:
        for s in range(ab_s, bis_s):
            k = None
            for kk in (kx, kr):
                if letzte[kk] is not None and s - letzte[kk] <= STALE and buch.seiten[kk][KAUF] and buch.seiten[kk][VERKAUF]:
                    k = kk
                    break
            if k is None:
                continue
            b = {p: m for p, m, _ in buch.stufen(k, KAUF, 5)}
            a = {p: m for p, m, _ in buch.stufen(k, VERKAUF, 5)}
            if max(b) >= min(a):                       # gekreuztes Buch (Auktion): nicht zeichnen
                continue
            f = faecher.setdefault(s - s % fach, {KAUF: defaultdict(float), VERKAUF: defaultdict(float), "n": 0, "r": 0})
            for p, m in b.items():
                f[KAUF][p] += m
            for p, m in a.items():
                f[VERKAUF][p] += m
            f["n"] += 1
            f["r"] += k is kr
            proben.append((s, b, a))

    zeit = None
    for ts, e, _ in ereignisse:
        s = ts // 1_000_000_000
        if isinstance(e, Trade):
            if e.venue in ("XETR", "XETRA", "XETA") or e.venue.startswith("XET"):
                trades_an[e.preis].append((s, e.menge))
            continue
        if not isinstance(e, (BuchSeite, BuchStufe)) or e.venue != "XETR":
            continue
        if zeit is not None and s > zeit:
            abtasten(min(s, zeit + STALE + 1), zeit)       # über Nacht nicht jede Sekunde der Lücke durchgehen
        k = buch.anwenden(e)
        if k in letzte:
            letzte[k] = s
        zeit = s if zeit is None else max(zeit, s)
    if zeit is not None:
        abtasten(zeit + 1, zeit)

    spalten = []
    for t in sorted(faecher):
        f = faecher[t]
        n = f["n"]
        b = sorted(((p, _r(m / n, 2)) for p, m in f[KAUF].items()), reverse=True)
        a = sorted((p, _r(m / n, 2)) for p, m in f[VERKAUF].items())
        preise.update(p for p, _ in b + a)
        spalten.append({"t": t, "b": b, "a": a, "retail": f["r"] * 2 > n})
    stufen = sorted(preise)
    diffs = [y - x for x, y in zip(stufen, stufen[1:]) if y - x > 1e-9]
    tick = _r(min(diffs), 6) if diffs else None
    return {"spalten": spalten, "waende": _waende(proben, trades_an, tick), "tick": tick}


def _waende(proben: list, trades_an: dict, tick: float | None, min_s: int = WAND_MIN_S, pause_s: int = WAND_PAUSE_S) -> list[dict]:
    if not proben:
        return []
    aus, offen = [], {}                                # (seite, preis) -> Wand
    pause: dict = {}                                   # beendet, aber noch nicht endgültig: (seite, preis) -> (Wand, Ergebnis, Sekunde)
    for s, b, a in proben:
        for key in [k for k, (_, _, t) in pause.items() if s - t > pause_s]:
            aus.append(pause.pop(key)[1])              # nicht zurückgekommen: endgültig
        for seite, stufen in ((KAUF, b), (VERKAUF, a)):
            if not stufen:
                continue
            mittel = statistics.fmean(stufen.values())
            grenze = mittel * WAND_FAKTOR / (1 + (WAND_FAKTOR - 1) / len(stufen))   # Mittel ohne die Wand selbst
            for p, m in stufen.items():
                key = (seite, p)
                if m >= grenze and m > 0:
                    w = offen.get(key)
                    if w is None and key in pause:     # kurz weg und wieder da (Order neu eingestellt): dieselbe Wand
                        w = offen[key] = pause.pop(key)[0]
                        w["unterbrechungen"] = w.get("unterbrechungen", 0) + 1
                    if w is None:
                        offen[key] = {"seite": seite, "preis": p, "von": s, "bis": s, "menge": m, "max": m}
                    else:
                        w["bis"], w["menge"], w["max"] = s, m, max(w["max"], m)
            # beendete Wände dieser Seite
            beste = max(b) if b else None, min(a) if a else None
            for key in [k for k in offen if k[0] == seite]:
                w = offen[key]
                p = w["preis"]
                m_jetzt = stufen.get(p, 0.0)
                if m_jetzt >= w["max"] * 0.3 and p in stufen:
                    if m_jetzt >= grenze:
                        continue
                    if s - w["bis"] < 3:                # kurz unter die Grenze: noch keine Entscheidung
                        continue
                elif s - w["bis"] < 2:
                    continue
                del offen[key]
                pause[key] = (w, _einordnen(w, s, stufen, seite, beste, trades_an, tick), s)
    ende = proben[-1][0]
    aus += [e for _, e, _ in pause.values()]
    aus = [e for e in aus if e["dauer_s"] >= min_s]
    for w in offen.values():
        if w["bis"] - w["von"] >= min_s:
            aus.append({**_basis(w), "ende": "steht", "ende_t": ende})
    return sorted(aus, key=lambda w: w["von"])


def _basis(w: dict) -> dict:
    return {"seite": "kauf" if w["seite"] == KAUF else "verkauf", "preis": w["preis"], "von": w["von"], "bis": w["bis"],
            "menge": _r(w["max"], 2), "dauer_s": w["bis"] - w["von"], "unterbrechungen": w.get("unterbrechungen", 0)}


def _einordnen(w: dict, s: int, stufen: dict, seite: str, beste: tuple, trades_an: dict, tick: float | None) -> dict:
    p = w["preis"]
    gehandelt = sum(m for t, m in trades_an.get(p, []) if w["von"] <= t <= s)
    bester = beste[0] if seite == KAUF else beste[1]
    abstand = None if bester is None else abs(bester - p)
    abstand_ticks = None if abstand is None or not tick else round(abstand / tick)
    sichtbar = (min(stufen) <= p) if seite == KAUF else (max(stufen) >= p)   # Preis liegt innerhalb der 5 Stufen
    if gehandelt >= w["max"] * 0.5:
        ende = "abgearbeitet"
    elif sichtbar or (seite == KAUF and bester is not None and bester <= p) or (seite == VERKAUF and bester is not None and bester >= p):
        ende = "zurückgezogen"
    else:
        ende = "aus dem Blick"
    return {**_basis(w), "ende": ende, "ende_t": s, "gehandelt": _r(gehandelt, 2), "abstand_ticks": abstand_ticks,
            "nah": ende == "zurückgezogen" and abstand_ticks is not None and abstand_ticks <= 2}


def krypto(paar: str, von: int, bis: int, fach: int) -> dict:
    """Krypto aus der Tabelle buch_tiefe des Dashboards (Binance, bis 1.000 Stufen, Bänder je Minute gemittelt).
    Ohne gespeicherte Abschlüsse gilt eine Wand als abgearbeitet, wenn der Kurs ihr Band in der Minute ihres Verschwindens
    (oder der davor) erreicht hat; sonst als zurückgezogen bzw. aus dem Blick."""
    fach = max(60, fach)
    leer = {"wert": paar, "fach": fach, "von": von, "bis": bis, "tage": [], "tick": None, "skala": None, "spalten": [], "waende": [],
            "quelle": "Binance, Orderbuch bis 1.000 Stufen je Seite (etwa ±0,3 % um den Kurs), alle 10 s abgetastet, je Minute gemittelt"}
    db = lesen._db()
    if db is None:
        return {**leer, "hinweis": "Keine Datenbank des Dashboards gefunden."}
    try:
        zeilen = db.execute("SELECT t, seite, preis, menge, band FROM buch_tiefe WHERE id = ? AND t >= ? AND t <= ? ORDER BY t",
                            (paar, von - von % 60, bis)).fetchall()
        kerzen = db.execute("SELECT t, h, l FROM kerzen_1m WHERE id = ? AND t >= ? AND t <= ?", (paar, von - 3600, bis + 120)).fetchall()
    except Exception:                                       # ältere Datenbank ohne Tabelle buch_tiefe
        zeilen, kerzen = [], []
    finally:
        db.close()
    if not zeilen:
        return {**leer, "hinweis": "Für diesen Zeitraum gibt es noch keine Buchtiefe. Das Dashboard sammelt sie seit dem 21.10.2026 für die Krypto-Werte der Watchlist."}
    minuten: dict[int, dict] = {}
    baender = defaultdict(int)
    for t, seite, preis, menge, band in zeilen:                # Preis = Bandmitte, damit die Zelle das Band abdeckt
        m = minuten.setdefault(t, {KAUF: {}, VERKAUF: {}})
        m[KAUF if seite == "B" else VERKAUF][_r(preis + band / 2, 8)] = menge
        baender[band] += 1
    band = max(baender, key=baender.get)
    proben = [(t, m[KAUF], m[VERKAUF]) for t, m in sorted(minuten.items()) if m[KAUF] and m[VERKAUF]]
    spalten = []
    for t0 in sorted({t - t % fach for t, _, _ in proben}):
        teil = [x for x in proben if t0 <= x[0] < t0 + fach]
        summe = {KAUF: defaultdict(float), VERKAUF: defaultdict(float)}
        for _, b, a in teil:
            for p, m in b.items():
                summe[KAUF][p] += m
            for p, m in a.items():
                summe[VERKAUF][p] += m
        n = len(teil)
        spalten.append({"t": t0, "b": sorted(((p, _r(m / n, 6)) for p, m in summe[KAUF].items()), reverse=True),
                        "a": sorted((p, _r(m / n, 6)) for p, m in summe[VERKAUF].items()), "retail": False})
    hl = {t: (h, l) for t, h, l in kerzen}
    waende = _waende(proben, {}, band, min_s=120, pause_s=90)
    for w in waende:
        if w["ende"] in ("zurückgezogen", "aus dem Blick"):
            erreicht = any(hl.get(t) and hl[t][1] <= w["preis"] + band / 2 and hl[t][0] >= w["preis"] - band / 2
                           for t in range(w["ende_t"] - 120, w["ende_t"] + 1, 60))
            if erreicht:
                w.update(ende="abgearbeitet", nah=False, gehandelt=None)
    mengen = sorted(m for x in spalten for _, m in x["b"] + x["a"])
    skala = mengen[min(len(mengen) - 1, int(len(mengen) * 0.98))] if mengen else None
    return {**leer, "tick": band, "skala": skala, "spalten": spalten, "waende": waende, "hinweis": None,
            "quelle": leer["quelle"] + f", Bänder zu {band:g}", "einheit": "Stück der Basiswährung"}


def heatmap(isin: str, von: int, bis: int, fach: int = 15, roh: lesen.Rohdaten | None = None) -> dict:
    """Spalten [{t, b: [[preis, menge]], a: [...], retail}] und Wände zwischen von und bis (Unix-Sekunden)."""
    fach = min(FAECHER, key=lambda f: abs(f - fach))
    if next((w["markt"] for w in lesen.watchlist() if w["id"] == isin), None) == "KRYPTO":
        return krypto(isin, von, bis, fach)
    roh = roh or lesen.Rohdaten()
    tag0 = datetime.fromtimestamp(von, BERLIN).date()
    tag1 = datetime.fromtimestamp(bis, BERLIN).date()
    if (tag1 - tag0).days > MAX_TAGE:
        tag0 = tag1 - timedelta(days=MAX_TAGE)
    vorhanden = set(roh.tage())
    spalten, waende, ticks, tage = [], [], [], []
    d = tag0 - timedelta(days=1)                       # Ordner nach UTC-Datum: Abend kann im Vortag liegen
    while d <= tag1:
        tag = d.isoformat()
        d += timedelta(days=1)
        if tag not in vorhanden:
            continue
        e = tag_rechnen(isin, tag, fach, roh)
        if not e["spalten"]:
            continue
        tage.append(tag)
        spalten += [x for x in e["spalten"] if von <= x["t"] <= bis]
        waende += [w for w in e["waende"] if w["bis"] >= von and w["von"] <= bis]
        if e["tick"]:
            ticks.append(e["tick"])
    spalten.sort(key=lambda x: x["t"])
    mengen = sorted(m for x in spalten for _, m in x["b"] + x["a"])
    skala = mengen[min(len(mengen) - 1, int(len(mengen) * 0.98))] if mengen else None
    return {"wert": isin, "fach": fach, "von": von, "bis": bis, "tage": tage, "tick": min(ticks) if ticks else None,
            "skala": skala, "spalten": spalten, "waende": sorted(waende, key=lambda w: w["von"]),
            "quelle": "Demo · erfundene Daten im Xetra-Format, beste 5 Stufen je Seite" if ports.DEMO
                      else "Xetra (Deutsche Börse), beste 5 Stufen je Seite, 15 Minuten verzögert",
            "hinweis": None if spalten else "Für diesen Zeitraum liegen keine Xetra-Buchdaten vor (das Dashboard sammelt sie nur live mit)."}
