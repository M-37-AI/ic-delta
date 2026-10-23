"""Chartformationen aus Kerzen erkennen: Unterstützung/Widerstand, Trendlinien, Dreiecke, Doppelhoch/-tief,
Schulter-Kopf-Schulter, Flaggen, Kerzenmuster und Indikator-Signale.

Grundlage sind bestätigte Hoch- und Tiefpunkte (Pivots: höchstes Hoch bzw. tiefstes Tief in staerke Kerzen
links und rechts). Toleranzen hängen an der ATR (durchschnittliche Schwankungsbreite), damit dieselben Regeln
für SAP im Minutenchart und Bitcoin im Tageschart passen. Formationen sind Ermessenssache: jede Erkennung
liefert ihre Punkte und Linien mit, damit man sie im Chart prüfen kann.

Ausgabe je Treffer: art, name, richtung (bullisch/bärisch/neutral), status (bildet sich/bestätigt/intakt/
gebrochen), punkte [(t, preis)], linien [{t1, p1, t2, p2, art}], zone, ereignis_t, schluessel,
guete (0–100), ziel, ziel_text, volumen (Volumen der Ausbruchskerze gegen den 20-Kerzen-Schnitt), hoehe.

Bestätigung: Schlusskurs jenseits der entscheidenden Linie (nicht nur ein Docht), mit kleinem ATR-Puffer.
Kursziel (Measure Rule): Höhe der Formation mal dem Anteil, den Bulkowski in seiner Statistik als mittleres
Erreichen ermittelt hat (thepatternsite.com/measure.html, Bullenmarkt), ab dem Ausbruchspunkt. Die volle
Höhe wird im Schnitt nicht erreicht; deshalb diese Anteile statt 100 %.
Güte: Berührungen, Passgenauigkeit der Linien (Abweichung in ATR), Symmetrie und Volumen beim Ausbruch.
Hohes Volumen beim Ausbruch gilt in der Literatur als Bestätigung, schwaches als Warnzeichen.
"""
from __future__ import annotations

import math

TF = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


def atr(k: list, n: int = 14) -> list[float]:
    aus, a = [], None
    for i, (t, o, h, l, c, v) in enumerate(k):
        tr = h - l if i == 0 else max(h - l, abs(h - k[i - 1][4]), abs(l - k[i - 1][4]))
        a = tr if a is None else (a * (n - 1) + tr) / n
        aus.append(max(a, 1e-9))
    return aus


def sma(werte: list[float], n: int) -> list[float | None]:
    aus, s = [], 0.0
    for i, w in enumerate(werte):
        s += w
        if i >= n:
            s -= werte[i - n]
        aus.append(s / n if i >= n - 1 else None)
    return aus


def rsi(werte: list[float], n: int = 14) -> list[float | None]:
    aus, auf, ab = [None], 0.0, 0.0
    for i in range(1, len(werte)):
        d = werte[i] - werte[i - 1]
        g, v = max(d, 0), max(-d, 0)
        if i <= n:
            auf += g / n
            ab += v / n
        else:
            auf = (auf * (n - 1) + g) / n
            ab = (ab * (n - 1) + v) / n
        aus.append(None if i < n else 100.0 if ab == 0 else 100 - 100 / (1 + auf / ab))
    return aus


def pivots(k: list, staerke: int) -> list[tuple[int, str, float]]:
    """(Index, "H"/"L", Preis), nur bestätigte: rechts müssen staerke Kerzen folgen."""
    aus = []
    for i in range(staerke, len(k) - staerke):
        fenster = k[i - staerke:i + staerke + 1]
        if k[i][2] == max(x[2] for x in fenster) and k[i][2] > k[i - 1][2]:
            aus.append((i, "H", k[i][2]))
        if k[i][3] == min(x[3] for x in fenster) and k[i][3] < k[i - 1][3]:
            aus.append((i, "L", k[i][3]))
    return aus


def _r(x, n=4):
    return None if x is None else round(x, n)


# Anteil der Formationshöhe für das Kursziel je Ausbruchsrichtung (Bulkowski, thepatternsite.com/measure.html).
# Flagge und Wimpel beziehen sich auf den Fahnenmast, Kanal (dort nicht erfasst) auf die Kanalbreite.
ZIEL = {"sks": {"ab": .51}, "sks_invers": {"auf": .71}, "doppelhoch": {"ab": .54}, "doppeltief": {"auf": .70},
        "dreifachhoch": {"ab": .49}, "dreifachtief": {"auf": .74},
        "dreieck_auf": {"auf": .70, "ab": .44}, "dreieck_ab": {"ab": .50, "auf": .64}, "dreieck_sym": {"auf": .58, "ab": .36},
        "keil_fallend": {"auf": .62, "ab": .29}, "keil_steigend": {"ab": .32, "auf": .63},
        "rechteck": {"auf": .78, "ab": .54}, "kanal_auf": {"auf": 1.0, "ab": 1.0}, "kanal_ab": {"auf": 1.0, "ab": 1.0},
        "flagge_bull": {"auf": .46}, "flagge_bear": {"ab": .46}, "wimpel_bull": {"auf": .35}, "wimpel_bear": {"ab": .32},
        "tasse_henkel": {"auf": .61}}


def volumen_faktor(k: list, i: int, n: int = 20) -> float | None:
    """Volumen der Kerze i gegen den Schnitt der n Kerzen davor."""
    vor = [x[5] for x in k[max(0, i - n):i] if x[5]]
    return round(k[i][5] / (sum(vor) / len(vor)), 2) if vor and k[i][5] else None


def _ziel(art: str, richtung: str, basis: float, hoehe: float) -> tuple[float | None, str]:
    anteil = ZIEL.get(art, {}).get("auf" if richtung == "bullisch" else "ab")
    if anteil is None or not hoehe:
        return None, ""
    z = basis + hoehe * anteil if richtung == "bullisch" else basis - hoehe * anteil
    zahl = f"{basis:,.{3 if basis < 10 else 2}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return z, f"{round(anteil * 100)} % der {'Mastlänge' if art.startswith(('flagge', 'wimpel')) else 'Höhe'} ab {zahl}"


def _guete(teile: list[tuple[float, float]], vol: float | None = None, bestaetigt: bool = False) -> int:
    """Gewichtetes Mittel aus (Wert 0–1, Gewicht); Volumen beim Ausbruch hebt oder senkt."""
    s = sum(w * g for w, g in teile) / max(1e-9, sum(g for _, g in teile))
    if bestaetigt and vol is not None:
        s += 0.12 if vol >= 1.5 else 0.05 if vol >= 1.1 else -0.1 if vol < 0.8 else 0
    return max(0, min(100, round(s * 100)))


def _treffer(art, name, richtung, status, k, punkte=(), linien=(), zone=None, ereignis=None, text="", staerke=None,
             guete=None, hoehe=None, basis=None):
    t = lambda i: k[min(max(i, 0), len(k) - 1)][0]
    vol = volumen_faktor(k, ereignis) if ereignis is not None and ereignis > 0 else None
    erwartet = richtung if richtung != "neutral" else None
    ziel, ziel_text = _ziel(art, erwartet, basis, hoehe) if erwartet and basis is not None and hoehe else (None, "")
    zusatz = [x for x in (text, f"Volumen beim Ausbruch {vol:.1f}×".replace(".", ",") if vol is not None and status in ("bestätigt", "gebrochen") and linien else "") if x]
    d = {"art": art, "name": name, "richtung": richtung, "status": status, "text": " · ".join(zusatz),
         "punkte": [(t(i), _r(p)) for i, p in punkte],
         "linien": [{"t1": t(a), "p1": _r(pa), "t2": t(b), "p2": _r(pb), "art": la} for a, pa, b, pb, la in linien],
         "zone": [_r(zone[0]), _r(zone[1])] if zone else None,
         "ereignis_t": t(ereignis) if ereignis is not None else None, "staerke": staerke,
         "guete": guete, "volumen": vol, "hoehe": _r(hoehe), "ziel": _r(ziel), "ziel_text": ziel_text}
    anker = d["punkte"][0][0] if d["punkte"] else (d["linien"][0]["t1"] if d["linien"] else d["ereignis_t"])
    d["schluessel"] = f"{art}:{anker}"
    return d


def _aktuell(n: int, bruch: int | None, letzter_punkt: int) -> bool:
    """Bestätigte Formationen nur, wenn die Bestätigung jung ist; offene nur mit jungem letzten Punkt."""
    return bruch >= n - 30 if bruch is not None else letzter_punkt >= n - 60


# --- Unterstützung und Widerstand ---------------------------------------------------------------
def zonen(k, piv, a, anzahl=3):
    if not piv:
        return []
    tol = 0.5 * sorted(a)[len(a) // 2]
    preise = sorted(piv, key=lambda x: x[2])
    gruppen, g = [], [preise[0]]
    for p in preise[1:]:
        if p[2] - g[0][2] <= tol:
            g.append(p)
        else:
            gruppen.append(g)
            g = [p]
    gruppen.append(g)
    c = k[-1][4]
    aus = []
    for g in gruppen:
        if len(g) < 2:
            continue
        lo, hi = min(x[2] for x in g), max(x[2] for x in g)
        mitte = sum(x[2] for x in g) / len(g)
        if hi - lo < 0.2 * tol:
            lo, hi = mitte - 0.1 * tol, mitte + 0.1 * tol
        widerstand = mitte > c
        letzte = max(x[0] for x in g)
        aus.append(_treffer("widerstand" if widerstand else "unterstuetzung", "Widerstand" if widerstand else "Unterstützung",
                            "bärisch" if widerstand else "bullisch", "intakt", k, punkte=[(x[0], x[2]) for x in sorted(g)],
                            zone=(lo, hi), text=f"{len(g)} Berührungen", staerke=len(g),
                            guete=_guete([(min(1, (len(g) - 1) / 4), 1)])) | {"_abstand": abs(mitte - c), "_letzte": letzte})
    oben = sorted((z for z in aus if z["art"] == "widerstand"), key=lambda z: z["_abstand"])[:anzahl]
    unten = sorted((z for z in aus if z["art"] == "unterstuetzung"), key=lambda z: z["_abstand"])[:anzahl]
    for z in oben + unten:
        z.pop("_abstand"), z.pop("_letzte")
    return oben + unten


# --- Trendlinien --------------------------------------------------------------------------------------
def _linien_suchen(k, piv, a, typ, staerke, fenster):
    n = len(k)
    start = max(0, n - fenster)
    pts = [(i, p) for i, t, p in piv if t == typ and i >= start]
    kandidaten = []
    for x in range(len(pts)):
        for y in range(x + 1, len(pts)):
            (i1, p1), (i2, p2) = pts[x], pts[y]
            if i2 - i1 < 2 * staerke:
                continue
            m = (p2 - p1) / (i2 - i1)
            tol = 0.3 * a[i2]
            wert = lambda j: p1 + m * (j - i1)
            bruch = None
            for j in range(i1, n):
                c = k[j][4]
                if (typ == "L" and c < wert(j) - tol) or (typ == "H" and c > wert(j) + tol):
                    bruch = j
                    break
            if bruch is not None and bruch < n - 6:
                continue                                    # längst gebrochen: keine aktuelle Linie
            beruehrt = [(i, p) for i, p in pts if i >= i1 and abs(p - wert(i)) <= tol]
            if len(beruehrt) < 3:
                continue
            if abs(wert(n - 1) - k[-1][4]) > 3 * a[-1] or beruehrt[-1][0] < n - 0.4 * min(fenster, n):
                continue                                    # weit weg vom Kurs oder lange nicht mehr berührt
            fehler = sum(abs(p - wert(i)) / a[i] for i, p in beruehrt) / len(beruehrt)   # mittlere Abweichung in ATR
            kandidaten.append({"i1": i1, "p1": p1, "m": m, "beruehrt": beruehrt, "bruch": bruch, "fehler": fehler,
                               "wert": (len(beruehrt), i2 - i1)})
    kandidaten.sort(key=lambda x: x["wert"], reverse=True)
    gewaehlt = []
    for kd in kandidaten:                                   # ähnliche Linien nur einmal
        if all(abs(kd["m"] - g["m"]) > 0.05 * a[-1] or abs((kd["p1"] + kd["m"] * (n - 1 - kd["i1"])) - (g["p1"] + g["m"] * (n - 1 - g["i1"]))) > 0.5 * a[-1]
               for g in gewaehlt):
            gewaehlt.append(kd)
        if len(gewaehlt) == 2:
            break
    return gewaehlt


def _linie_guete(k, kd, bestaetigt):
    beruehrt = min(1, (len(kd["beruehrt"]) - 2) / 3)               # 3 Berührungen ≈ 0,33, ab 5 voll
    genau = max(0, 1 - kd["fehler"] / 0.3)                          # 0 ATR Abweichung = voll, Toleranzgrenze = 0
    vol = volumen_faktor(k, kd["bruch"]) if kd.get("bruch") else None
    return _guete([(beruehrt, 2), (genau, 1)], vol, bestaetigt)


def trendlinien(k, piv, a, staerke, fenster=250):
    aus, roh = [], {}
    n = len(k)
    for typ in ("L", "H"):
        roh[typ] = _linien_suchen(k, piv, a, typ, staerke, fenster)
        for kd in roh[typ]:
            ende = n - 1
            p_ende = kd["p1"] + kd["m"] * (ende - kd["i1"])
            steigend = kd["m"] > 0.02 * a[-1]
            fallend = kd["m"] < -0.02 * a[-1]
            if typ == "L":
                name = "Aufwärtstrendlinie" if steigend else "Unterstützungslinie" if not fallend else "fallende Unterstützung"
                richtung = "bullisch"
            else:
                name = "Abwärtstrendlinie" if fallend else "Widerstandslinie" if not steigend else "steigender Widerstand"
                richtung = "bärisch"
            gebrochen = kd["bruch"] is not None
            aus.append(_treffer("trendlinie", name + (" gebrochen" if gebrochen else ""),
                                ("bärisch" if typ == "L" else "bullisch") if gebrochen else richtung,
                                "gebrochen" if gebrochen else "intakt", k, punkte=kd["beruehrt"],
                                linien=[(kd["i1"], kd["p1"], ende, p_ende, "unterstuetzung" if typ == "L" else "widerstand")],
                                ereignis=kd["bruch"], text=f"{len(kd['beruehrt'])} Berührungen", staerke=len(kd["beruehrt"]),
                                guete=_linie_guete(k, kd, gebrochen)))
    return aus, roh


def dreiecke(k, roh, a):
    """Formationen aus einer Unterstützungs- und einer Widerstandslinie:
    zusammenlaufend: aufsteigendes/absteigendes/symmetrisches Dreieck, steigender/fallender Keil (beide Linien in
    dieselbe Richtung; Keile brechen meist gegen ihre Neigung); parallel: Rechteck (waagerecht) oder Kanal."""
    aus = []
    n = len(k)
    flach = 0.02 * a[-1]
    for lo in roh.get("L", []):
        for hi in roh.get("H", []):
            start = max(lo["i1"], hi["i1"])
            oben = hi["p1"] + hi["m"] * (n - 1 - hi["i1"])
            unten = lo["p1"] + lo["m"] * (n - 1 - lo["i1"])
            o_start, u_start = hi["p1"] + hi["m"] * (start - hi["i1"]), lo["p1"] + lo["m"] * (start - lo["i1"])
            if oben - unten < 1.0 * a[-1] and lo["m"] <= hi["m"]:
                continue
            parallel = abs(lo["m"] - hi["m"]) <= 0.15 * max(abs(lo["m"]), abs(hi["m"]), flach) or abs(lo["m"] - hi["m"]) <= flach
            if parallel:
                if oben - unten < 2 * a[-1]:
                    continue                                # zu eng für einen Kanal
                if abs(hi["m"]) <= flach and abs(lo["m"]) <= flach:
                    art, name, richtung = "rechteck", "Rechteck", "neutral"
                elif lo["m"] > 0:
                    art, name, richtung = "kanal_auf", "Aufwärtskanal", "bullisch"
                else:
                    art, name, richtung = "kanal_ab", "Abwärtskanal", "bärisch"
            elif lo["m"] > hi["m"]:                           # laufen zusammen
                spitze = (hi["p1"] - lo["p1"] + lo["m"] * lo["i1"] - hi["m"] * hi["i1"]) / (lo["m"] - hi["m"])
                if not (n - 1 < spitze < n - 1 + 2 * (n - start)):
                    continue
                if abs(hi["m"]) <= flach and lo["m"] > flach:
                    art, name, richtung = "dreieck_auf", "Aufsteigendes Dreieck", "bullisch"
                elif abs(lo["m"]) <= flach and hi["m"] < -flach:
                    art, name, richtung = "dreieck_ab", "Absteigendes Dreieck", "bärisch"
                elif hi["m"] < -flach and lo["m"] > flach:
                    art, name, richtung = "dreieck_sym", "Symmetrisches Dreieck", "neutral"
                elif lo["m"] > 0 and hi["m"] > 0:
                    art, name, richtung = "keil_steigend", "Steigender Keil", "bärisch"
                else:
                    art, name, richtung = "keil_fallend", "Fallender Keil", "bullisch"
            else:
                continue                                    # auseinanderlaufend (Megafon) wird nicht gezeigt
            punkte = sorted(lo["beruehrt"] + hi["beruehrt"])
            hoehe = max(o_start - u_start, max(p for _, p in hi["beruehrt"]) - min(p for _, p in lo["beruehrt"]))
            c = k[-1][4]
            status, ereignis, basis, erwartet = "bildet sich", None, None, richtung
            # Ausbruch: erster Schlusskurs jenseits einer Linie nach dem letzten Berührungspunkt
            for j in range(max(i for i, _ in punkte) + 1, n):
                o_j, u_j = hi["p1"] + hi["m"] * (j - hi["i1"]), lo["p1"] + lo["m"] * (j - lo["i1"])
                if k[j][4] > o_j + 0.2 * a[j]:
                    status, ereignis, erwartet, basis = "bestätigt", j, "bullisch", o_j
                    break
                if k[j][4] < u_j - 0.2 * a[j]:
                    status, ereignis, erwartet, basis = "bestätigt", j, "bärisch", u_j
                    break
            if ereignis is not None and ereignis < n - 30:
                continue                                    # Ausbruch zu alt
            if status == "bestätigt":
                name += ": Ausbruch nach oben" if erwartet == "bullisch" else ": Ausbruch nach unten"
            elif erwartet != "neutral":
                basis = oben if erwartet == "bullisch" else unten   # Ziel für den erwarteten Ausbruch
            vol = volumen_faktor(k, ereignis) if ereignis is not None else None
            guete = _guete([(min(1, (len(punkte) - 4) / 4 + 0.4), 2), (max(0, 1 - (lo["fehler"] + hi["fehler"]) / 0.6), 1),
                            (min(1, (n - start) / 40), 1)], vol, status == "bestätigt")
            aus.append(_treffer(art, name, erwartet, status, k, punkte=punkte,
                                linien=[(start, u_start, n - 1, unten, "unterstuetzung"), (start, o_start, n - 1, oben, "widerstand")],
                                ereignis=ereignis, text=f"{len(lo['beruehrt'])}+{len(hi['beruehrt'])} Berührungen", guete=guete,
                                hoehe=hoehe, basis=basis))
    aus.sort(key=lambda f: -(f["guete"] or 0))
    gewaehlt = []
    for f in aus:                                          # je Familie (zusammenlaufend, parallel) die beste
        familie = f["art"].split("_")[0] in ("rechteck", "kanal")
        if not any((g["art"].split("_")[0] in ("rechteck", "kanal")) == familie for g in gewaehlt):
            gewaehlt.append(f)
    return gewaehlt


# --- Umkehrformationen ------------------------------------------------------------------------------------
def doppel(k, piv, a, staerke, fenster=150):
    """Doppel- und Dreifachhoch/-tief: zwei bzw. drei Hochs (Tiefs) auf gleicher Höhe, dazwischen kein höheres Hoch,
    Nackenlinie am tiefsten Tief (höchsten Hoch) dazwischen. Bestätigt mit Schlusskurs jenseits der Nackenlinie."""
    aus = []
    n = len(k)
    for typ, richtung in (("H", "bärisch"), ("L", "bullisch")):
        s = 1 if typ == "H" else -1
        pts = [(i, p) for i, t, p in piv if t == typ and i >= n - fenster]
        for anzahl in (3, 2):
            art = ("dreifach" if anzahl == 3 else "doppel") + ("hoch" if typ == "H" else "tief")
            name = ("Dreifach" if anzahl == 3 else "Doppel") + ("hoch" if typ == "H" else "tief")
            for x in range(len(pts) - anzahl, -1, -1):          # jüngste zuerst
                gruppe = pts[x:x + anzahl]
                i1, i2 = gruppe[0][0], gruppe[-1][0]
                preise = [p for _, p in gruppe]
                tol = max(0.5 * a[i2], 0.003 * preise[0])
                if any(gruppe[q + 1][0] - gruppe[q][0] < 2 * staerke for q in range(anzahl - 1)) or max(preise) - min(preise) > tol:
                    continue
                zwischen = k[i1:i2 + 1]
                extrem = max(x_[2] for x_ in zwischen) if typ == "H" else min(x_[3] for x_ in zwischen)
                if s * (extrem - (max(preise) if typ == "H" else min(preise))) > 0:
                    continue                                # dazwischen lag ein höheres Hoch bzw. tieferes Tief
                if typ == "H":
                    j = i1 + min(range(len(zwischen)), key=lambda q: zwischen[q][3])
                    hals = k[j][3]
                else:
                    j = i1 + max(range(len(zwischen)), key=lambda q: zwischen[q][2])
                    hals = k[j][2]
                hoehe = abs((sum(preise) / anzahl) - hals)
                if hoehe < 1.5 * a[i2]:
                    continue
                bruch = next((q for q in range(i2, n) if s * (hals - k[q][4]) > 0.1 * a[q]), None)
                ende = n if bruch is None else bruch
                if any(s * (k[q][4] - (max(preise) if typ == "H" else min(preise))) > 0.3 * a[q] for q in range(i2, ende)):
                    continue                                # vor dem Bruch über das Hoch (unter das Tief) gelaufen: ungültig
                if not _aktuell(n, bruch, i2):
                    continue
                familie = "hoch" if typ == "H" else "tief"
                if any(f["art"] in ("doppel" + familie, "dreifach" + familie) for f in aus):
                    continue                                # je Richtung nur die jüngste, Dreifach verdrängt Doppel
                vol = volumen_faktor(k, bruch) if bruch is not None else None
                symmetrie = max(0, 1 - (max(preise) - min(preise)) / tol)
                guete = _guete([(symmetrie, 1), (min(1, hoehe / (4 * a[i2])), 1), (min(1, (i2 - i1) / (6 * staerke)), 1), (0.7 if anzahl == 3 else 0.5, 1)],
                               vol, bruch is not None)
                tiefs = [(j, hals)] if anzahl == 2 else []
                if anzahl == 3:                              # die zwei Zwischentiefs (-hochs) für den Linienzug
                    for q in range(2):
                        a_, b_ = gruppe[q][0], gruppe[q + 1][0]
                        seg = k[a_:b_ + 1]
                        jj = a_ + (min(range(len(seg)), key=lambda z: seg[z][3]) if typ == "H" else max(range(len(seg)), key=lambda z: seg[z][2]))
                        tiefs.append((jj, k[jj][3] if typ == "H" else k[jj][2]))
                punkte = sorted(gruppe + tiefs)
                aus.append(_treffer(art, name + (" bestätigt" if bruch is not None else ""), richtung,
                                    "bestätigt" if bruch is not None else "bildet sich", k, punkte=punkte,
                                    linien=[(i1, hals, n - 1, hals, "nackenlinie")], ereignis=bruch,
                                    text=f"Nackenlinie {hals:.4g}", guete=guete, hoehe=hoehe, basis=hals))
    return aus


def kopf_schulter(k, piv, a, fenster=200):
    aus = []
    n = len(k)
    for typ, art, name, richtung in (("H", "sks", "Schulter-Kopf-Schulter", "bärisch"), ("L", "sks_invers", "Inverse Schulter-Kopf-Schulter", "bullisch")):
        hoch = [(i, p) for i, t, p in piv if t == typ and i >= n - fenster]
        tief = [(i, p) for i, t, p in piv if t != typ and i >= n - fenster]
        for x in range(len(hoch) - 2):
            (ia, pa), (ib, pb), (ic, pc) = hoch[x:x + 3]
            s = 1 if typ == "H" else -1
            if s * (pb - pa) < a[ib] or s * (pb - pc) < a[ib] or abs(pa - pc) > 1.0 * a[ib]:
                continue
            t1 = [q for q in tief if ia < q[0] < ib]
            t2 = [q for q in tief if ib < q[0] < ic]
            if not t1 or not t2:
                continue
            (j1, q1) = min(t1, key=lambda q: s * q[1])
            (j2, q2) = min(t2, key=lambda q: s * q[1])
            m = (q2 - q1) / (j2 - j1)
            hals = lambda q: q1 + m * (q - j1)
            bruch = next((q for q in range(ic, n) if s * (k[q][4] - hals(q)) < -0.1 * a[q]), None)
            if not _aktuell(n, bruch, ic):
                continue
            hoehe = abs(pb - hals(ib))
            vol = volumen_faktor(k, bruch) if bruch is not None else None
            symmetrie = max(0, 1 - abs(pa - pc) / a[ib])                  # Schultern auf gleicher Höhe
            zeit = 1 - min(1, abs((ib - ia) - (ic - ib)) / max(1, ic - ia))   # Schultern gleich weit vom Kopf
            neigung = max(0, 1 - abs(m) * (ic - ia) / max(hoehe, 1e-9))   # flache Nackenlinie
            kopf = min(1, min(s * (pb - pa), s * (pb - pc)) / (2 * a[ib]))
            guete = _guete([(symmetrie, 1), (zeit, 1), (neigung, 1), (kopf, 1)], vol, bruch is not None)
            basis = hals(bruch if bruch is not None else n - 1)
            aus.append(_treffer(art, name + (" bestätigt" if bruch is not None else ""), richtung,
                                "bestätigt" if bruch is not None else "bildet sich", k,
                                punkte=[(ia, pa), (j1, q1), (ib, pb), (j2, q2), (ic, pc)],
                                linien=[(j1, q1, n - 1, hals(n - 1), "nackenlinie")], ereignis=bruch,
                                guete=guete, hoehe=hoehe, basis=basis))
    return aus[-2:]


def flaggen(k, a, max_mast=12, fenster=60):
    """Flagge bzw. Wimpel: steiler Mast (mindestens 4 ATR in höchstens max_mast Kerzen), danach eine enge Pause, die
    höchstens die Hälfte des Masts zurückgibt. Laufen Hochs und Tiefs der Pause zusammen, ist es ein Wimpel.
    Ausbruch in Mastrichtung; Ziel ab dem Pausenrand mit Bulkowskis Anteil der Mastlänge."""
    aus = []
    n = len(k)
    if n < 30:
        return aus
    for ende in range(max(1, n - fenster), n - 5):
        for laenge in range(3, max_mast + 1):
            anfang = ende - laenge
            if anfang < 0:
                break
            hub = k[ende][4] - k[anfang][4]
            if abs(hub) < 4 * a[ende]:
                continue
            rest = k[ende + 1:]
            hi, lo = max(x[2] for x in rest), min(x[3] for x in rest)
            if hi - lo > 0.5 * abs(hub):
                continue
            bull = hub > 0
            if (bull and k[ende][4] - lo > 0.5 * hub) or (not bull and hi - k[ende][4] > 0.5 * -hub):
                continue
            koerper = k[ende + 1:-1] or rest
            grenze_o, grenze_u = max(x[2] for x in koerper), min(x[3] for x in koerper)
            haelfte = max(2, len(koerper) // 2)
            vorn, hinten = koerper[:haelfte], koerper[haelfte:] or koerper[-1:]
            wimpel = max(x[2] for x in hinten) < max(x[2] for x in vorn) - 0.2 * a[-1] and min(x[3] for x in hinten) > min(x[3] for x in vorn) + 0.2 * a[-1]
            c = k[-1][4]
            ausbruch = (bull and c > grenze_o) or (not bull and c < grenze_u)
            art = ("wimpel_" if wimpel else "flagge_") + ("bull" if bull else "bear")
            name = ("Bullischer Wimpel" if wimpel else "Bullische Flagge") if bull else ("Bärischer Wimpel" if wimpel else "Bärische Flagge")
            if wimpel:                                      # Wimpel: zusammenlaufende Begrenzung
                linien = [(ende + 1, max(x[2] for x in vorn), n - 1, max(x[2] for x in hinten), "widerstand"),
                          (ende + 1, min(x[3] for x in vorn), n - 1, min(x[3] for x in hinten), "unterstuetzung")]
            else:
                linien = [(ende + 1, grenze_o, n - 1, grenze_o, "widerstand"), (ende + 1, grenze_u, n - 1, grenze_u, "unterstuetzung")]
            vol = volumen_faktor(k, n - 1) if ausbruch else None
            ruhe = 1 - (hi - lo) / (0.5 * abs(hub))                 # je enger die Pause, desto sauberer
            vol_pause = sum(x[5] or 0 for x in rest) / len(rest)
            vol_mast = sum(x[5] or 0 for x in k[anfang + 1:ende + 1]) / max(1, laenge)
            leiser = 1.0 if vol_mast and vol_pause < 0.8 * vol_mast else 0.4   # Volumen in der Pause sollte nachlassen
            guete = _guete([(min(1, abs(hub) / (8 * a[ende])), 1), (max(0, ruhe), 1), (leiser, 1)], vol, ausbruch)
            aus.append(_treffer(art, name + (": Ausbruch" if ausbruch else ""), "bullisch" if bull else "bärisch",
                                "bestätigt" if ausbruch else "bildet sich", k,
                                punkte=[(anfang, k[anfang][4]), (ende, k[ende][4])], linien=linien,
                                ereignis=n - 1 if ausbruch else None, text=f"Mast {abs(hub) / a[ende]:.1f} ATR".replace(".", ","),
                                guete=guete, hoehe=abs(hub), basis=lo if bull else hi))
            return aus                                     # nur die jüngste Flagge
    return aus


def tasse_henkel(k, piv, a, staerke, fenster=250):
    """Tasse mit Henkel (O'Neil, Bulkowski): zwei Hochs (Ränder) auf ähnlicher Höhe, dazwischen ein runder Boden
    (U statt V: das Tief liegt mittig und der Kurs verweilt unten), danach ein kurzer, flacher Henkel, der höchstens
    die Hälfte der Tassentiefe zurückgibt. Bestätigt mit Schlusskurs über dem rechten Rand."""
    n = len(k)
    hochs = [(i, p) for i, t, p in piv if t == "H" and i >= n - fenster]
    for x in range(len(hochs) - 1, 0, -1):                    # jüngster rechter Rand zuerst
        i2, p2 = hochs[x]
        for y in range(x - 1, -1, -1):
            i1, p1 = hochs[y]
            breite = i2 - i1
            if breite < 6 * staerke:
                continue
            rand = max(p1, p2)
            if abs(p1 - p2) > max(1.0 * a[i2], 0.03 * rand) or max(z[2] for z in k[i1 + 1:i2]) > rand:
                continue
            seg = k[i1:i2 + 1]
            jb = min(range(len(seg)), key=lambda q: seg[q][3])
            boden = seg[jb][3]
            tiefe = rand - boden
            if tiefe < 3 * a[i2] or not (0.25 <= jb / breite <= 0.75):
                continue
            unten = sum(1 for z in seg if z[3] <= boden + 0.33 * tiefe) / len(seg)
            if unten < 0.2:
                continue                                    # V-Form, keine Tasse
            nach = k[i2 + 1:]
            if len(nach) < 2 or len(nach) > breite // 2 + 2:
                continue
            henkel_tief = min(z[3] for z in nach)
            if not (0.05 * tiefe <= p2 - henkel_tief <= 0.5 * tiefe) or henkel_tief < boden + 0.5 * tiefe:
                continue
            jh = i2 + 1 + min(range(len(nach)), key=lambda q: nach[q][3])
            bruch = next((q for q in range(jh, n) if k[q][4] > rand + 0.1 * a[q]), None)
            vol = volumen_faktor(k, bruch) if bruch is not None else None
            guete = _guete([(max(0, 1 - abs(p1 - p2) / max(1.0 * a[i2], 0.03 * rand)), 1), (min(1, unten / 0.4), 1),
                            (1 - abs(jb / breite - 0.5) * 2, 1), (min(1, tiefe / (6 * a[i2])), 1)], vol, bruch is not None)
            return [_treffer("tasse_henkel", "Tasse mit Henkel" + (" bestätigt" if bruch is not None else ""), "bullisch",
                             "bestätigt" if bruch is not None else "bildet sich", k,
                             punkte=[(i1, p1), (i1 + jb, boden), (i2, p2), (jh, henkel_tief)],
                             linien=[(i1, rand, n - 1, rand, "widerstand")], ereignis=bruch,
                             text=f"Tiefe {tiefe / a[i2]:.1f} ATR, Henkel {(p2 - henkel_tief) / tiefe * 100:.0f} %".replace(".", ","),
                             guete=guete, hoehe=tiefe, basis=rand)]
    return []


# --- Kerzen und Indikatoren ------------------------------------------------------------------------------
def kerzenmuster(k, a, letzte=3):
    aus = []
    n = len(k)
    for i in range(max(1, n - letzte), n):
        t, o, h, l, c, v = k[i]
        _, o0, h0, l0, c0, _ = k[i - 1]
        koerper, spanne = abs(c - o), max(h - l, 1e-12)
        if spanne < 0.5 * a[i]:
            continue
        if c > o and c0 < o0 and c >= o0 and o <= c0 and koerper > abs(c0 - o0):
            aus.append(_treffer("engulfing_bull", "Bullish Engulfing", "bullisch", "bestätigt", k, punkte=[(i, c)], ereignis=i))
        elif c < o and c0 > o0 and c <= o0 and o >= c0 and koerper > abs(c0 - o0):
            aus.append(_treffer("engulfing_bear", "Bearish Engulfing", "bärisch", "bestätigt", k, punkte=[(i, c)], ereignis=i))
        elif koerper <= 0.35 * spanne and min(o, c) - l >= 2 * koerper and h - max(o, c) <= 0.25 * spanne:
            aus.append(_treffer("hammer", "Hammer", "bullisch", "bestätigt", k, punkte=[(i, l)], ereignis=i))
        elif koerper <= 0.35 * spanne and h - max(o, c) >= 2 * koerper and min(o, c) - l <= 0.25 * spanne:
            aus.append(_treffer("shooting_star", "Shooting Star", "bärisch", "bestätigt", k, punkte=[(i, h)], ereignis=i))
        elif koerper <= 0.08 * spanne:
            aus.append(_treffer("doji", "Doji", "neutral", "bestätigt", k, punkte=[(i, c)], ereignis=i))
    return aus


def indikatoren(k, letzte=3):
    aus = []
    n = len(k)
    c = [x[4] for x in k]
    s50, s200, r = sma(c, 50), sma(c, 200), rsi(c)
    for i in range(max(1, n - letzte), n):
        if s200[i] is not None and s200[i - 1] is not None:
            if s50[i - 1] <= s200[i - 1] and s50[i] > s200[i]:
                aus.append(_treffer("golden_cross", "Golden Cross (SMA 50 über 200)", "bullisch", "bestätigt", k, punkte=[(i, s50[i])], ereignis=i))
            if s50[i - 1] >= s200[i - 1] and s50[i] < s200[i]:
                aus.append(_treffer("death_cross", "Death Cross (SMA 50 unter 200)", "bärisch", "bestätigt", k, punkte=[(i, s50[i])], ereignis=i))
        if r[i] is not None and r[i - 1] is not None:
            if r[i - 1] < 70 <= r[i]:
                aus.append(_treffer("rsi_ueber70", "RSI über 70 (überkauft)", "bärisch", "bestätigt", k, punkte=[(i, c[i])], ereignis=i))
            if r[i - 1] > 30 >= r[i]:
                aus.append(_treffer("rsi_unter30", "RSI unter 30 (überverkauft)", "bullisch", "bestätigt", k, punkte=[(i, c[i])], ereignis=i))
        if i >= 100:
            if k[i][2] > max(x[2] for x in k[i - 100:i]):
                aus.append(_treffer("hoch_100", "Neues Hoch der letzten 100 Kerzen", "bullisch", "bestätigt", k, punkte=[(i, k[i][2])], ereignis=i))
            if k[i][3] < min(x[3] for x in k[i - 100:i]):
                aus.append(_treffer("tief_100", "Neues Tief der letzten 100 Kerzen", "bärisch", "bestätigt", k, punkte=[(i, k[i][3])], ereignis=i))
    return aus


ARTEN = {
    "unterstuetzung": "Unterstützung", "widerstand": "Widerstand", "trendlinie": "Trendlinie",
    "dreieck_auf": "Aufsteigendes Dreieck", "dreieck_ab": "Absteigendes Dreieck", "dreieck_sym": "Symmetrisches Dreieck",
    "keil_steigend": "Steigender Keil", "keil_fallend": "Fallender Keil", "keil": "Keil (alt)",
    "rechteck": "Rechteck", "kanal_auf": "Aufwärtskanal", "kanal_ab": "Abwärtskanal",
    "doppelhoch": "Doppelhoch", "doppeltief": "Doppeltief", "dreifachhoch": "Dreifachhoch", "dreifachtief": "Dreifachtief",
    "sks": "Schulter-Kopf-Schulter", "sks_invers": "Inverse Schulter-Kopf-Schulter", "tasse_henkel": "Tasse mit Henkel",
    "flagge_bull": "Bullische Flagge", "flagge_bear": "Bärische Flagge", "wimpel_bull": "Bullischer Wimpel", "wimpel_bear": "Bärischer Wimpel",
    "engulfing_bull": "Bullish Engulfing", "engulfing_bear": "Bearish Engulfing", "hammer": "Hammer", "shooting_star": "Shooting Star", "doji": "Doji",
    "golden_cross": "Golden Cross", "death_cross": "Death Cross", "rsi_ueber70": "RSI über 70", "rsi_unter30": "RSI unter 30",
    "hoch_100": "Neues 100-Kerzen-Hoch", "tief_100": "Neues 100-Kerzen-Tief",
}


def erkennen(k: list, staerke: int = 5) -> list[dict]:
    """k: Kerzen [t, o, h, l, c, v], älteste zuerst, nur abgeschlossene."""
    if len(k) < 4 * staerke + 10:
        return []
    a = atr(k)
    piv = pivots(k, staerke)
    linien, roh = trendlinien(k, piv, a, staerke)
    aus = zonen(k, piv, a) + linien + dreiecke(k, roh, a) + doppel(k, piv, a, staerke) + kopf_schulter(k, piv, a) \
        + tasse_henkel(k, piv, a, staerke) + flaggen(k, a) + kerzenmuster(k, a) + indikatoren(k)
    return aus
