"""Kurzfassung einer Orderbuch-Analyse für die KI: nur Kennzahlen und kleine Tabellen, Reihen verdichtet.

Die volle Analyse (rechnen.auswerten) enthält Zeitreihen mit hunderten Punkten; für ein Sprachmodell
reichen Spannweite, Mittel, letzter Wert und ein Verlauf in acht Abschnitten.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
S = 1_000_000_000


def _uhr(t) -> str | None:
    if t is None:
        return None
    t = t / S if t > 1e12 else t
    return datetime.fromtimestamp(t, BERLIN).strftime("%H:%M")


def _r(x, n=2):
    return None if x is None else round(x, n)


def reihe(t: list, v: list, n: int = 8, stellen: int = 2) -> dict | None:
    """Verdichtet eine Zeitreihe: Spannweite, Mittel, letzter Wert und Mittel je Abschnitt (mit Uhrzeit)."""
    paare = [(a, b) for a, b in zip(t, v) if b is not None]
    if not paare:
        return None
    werte = [b for _, b in paare]
    schritt = max(1, len(paare) // n)
    verlauf = []
    for i in range(0, len(paare), schritt):
        teil = paare[i:i + schritt]
        verlauf.append([_uhr(teil[0][0]), _r(sum(b for _, b in teil) / len(teil), stellen)])
    return {"min": _r(min(werte), stellen), "max": _r(max(werte), stellen), "mittel": _r(sum(werte) / len(werte), stellen),
            "zuletzt": _r(werte[-1], stellen), "verlauf": verlauf[:n + 1]}


def _ohne(d: dict, *schluessel) -> dict:
    return {k: v for k, v in d.items() if k not in schluessel}


def kurzfassung(a: dict, teil: str = "alle") -> dict:
    """teil: a, b, c, d, e, f, auktionen oder alle."""
    aus = {"isin": a.get("isin"), "tag": a.get("tag"), "meldungen": a.get("ereignisse"),
           "fenster": [_uhr(x) for x in a.get("fenster", [])], "abdeckung_minuten": a.get("abdeckung"),
           "hinweis": "Xetra-Buch mit 5 Stufen, 15 Minuten verzögert, nur fortlaufender Handel 09:01 bis 17:30 Uhr. "
                      "Basispunkte beziehen sich auf den Mittelkurs. Ein Tag ist keine Statistik."}
    teile = ["a", "b", "c", "d", "e", "f", "auktionen"] if teil == "alle" else [teil]
    for k in teile:
        d = a.get(k)
        if d is None:
            continue
        if d.get("leer"):
            aus[k] = {"leer": True, "grund": "kein Xetra-Buch aus dem fortlaufenden Handel in den Rohdaten dieses Tages"}
            continue
        aus[k] = globals()["_" + k](d, a)
    return aus


def _a(d: dict, a: dict) -> dict:
    r = d.get("reihe", {})
    return {"titel": "Kosten und Liquidität",
            "spread_bp": d.get("spread_bp"), "effektiver_spread_bp": d.get("effektiv_bp"), "realisierter_spread_60s_bp": d.get("realisiert_bp"),
            "preiswirkung_bp": d.get("wirkung_bp"), "abschluesse_xetra": d.get("abschluesse"), "stunden_ausgewertet": d.get("stunden"),
            "tiefe_kauf_eur": d.get("tiefe_bid"), "tiefe_verkauf_eur": d.get("tiefe_ask"),
            "orderkosten": d.get("ordern"),
            "guenstigste_halbstunde": [_uhr(d["beste_halbstunde"][0]), d["beste_halbstunde"][1]] if d.get("beste_halbstunde") else None,
            "teuerste_halbstunde": [_uhr(d["schlechteste_halbstunde"][0]), d["schlechteste_halbstunde"][1]] if d.get("schlechteste_halbstunde") else None,
            "spread_verlauf_bp": reihe(r.get("t", []), r.get("spread", [])),
            "tiefe_kauf_verlauf_eur": reihe(r.get("t", []), r.get("tiefe_b", []), stellen=0),
            "tiefe_verkauf_verlauf_eur": reihe(r.get("t", []), r.get("tiefe_a", []), stellen=0)}


def _b(d: dict, a: dict) -> dict:
    r = d.get("reihe", {})
    ofi = d.get("ofi", {})
    return {"titel": "Druck im Buch (Ungleichgewicht, OFI, Microprice)",
            "erklaerung": "OBI = (Kauf − Verkauf) / Summe der sichtbaren Mengen, +1 nur Kaufinteresse. fuenftel: mittlere Bewegung der Mitte in bp "
                          "nach 5/30/60/300 s, je nach OBI-Fünftel der besten Stufe. Microprice-Trefferquote: 50 % wäre Zufall.",
            "halber_spread_bp": d.get("halber_spread_bp"),
            "fuenftel": [_ohne(z, "n5", "n30", "n60", "n300") for z in d.get("fuenftel", [])],
            "microprice_trefferquote": d.get("micro"),
            "ofi_regression": {"steigung_bp_je_tiefe": ofi.get("beta"), "achse": ofi.get("alpha"), "r2": ofi.get("r2"), "n": ofi.get("n")},
            "obi_beste_stufe_verlauf": reihe(r.get("t", []), r.get("obi1", []), stellen=3),
            "obi_5_stufen_verlauf": reihe(r.get("t", []), r.get("obi5", []), stellen=3),
            "microprice_bp_verlauf": reihe(r.get("t", []), r.get("micro", []), stellen=3)}


def _c(d: dict, a: dict) -> dict:
    def wand(x):
        return {"von": _uhr(x.get("start")), "bis": _uhr(x.get("ende")), "seite": "Kauf" if x.get("seite") == "B" else "Verkauf",
                "preis": x.get("preis"), "stueck_max": x.get("max"), "eur": x.get("eur"), "orders": x.get("orders"),
                "stufe": x.get("stufe_start"), "dauer_s": x.get("dauer_s"), "ergebnis": "Spoofing-Muster" if x.get("verdacht") else x.get("ergebnis")}

    def eis(x):
        return {"zeit": _uhr(x.get("start")), "seite": "Kauf" if x.get("seite") == "B" else "Verkauf", "preis": x.get("preis"),
                "sichtbar_max": x.get("sichtbar"), "gehandelt": x.get("gehandelt"), "faktor": x.get("faktor"), "abschluesse": x.get("abschluesse")}
    return {"titel": "Aufbau des Buchs (Wände, Eisberge, Ordergrößen)",
            "erklaerung": "Wand = Stufe mit mehr als schwelle Stück. abgearbeitet = weggehandelt, zurückgezogen = ohne Handel verschwunden, "
                          "Spoofing-Muster = zurückgezogen, als der Kurs näher kam (< 60 s). Verdacht, kein Beweis.",
            "median_stufe_stueck": d.get("median_stufe"), "schwelle_stueck": d.get("schwelle"), "waende_anzahl": d.get("anzahl"),
            "waende_ergebnisse": d.get("ergebnisse"), "spoofing_muster": d.get("verdacht"),
            "groesste_waende": [wand(x) for x in d.get("waende", [])[:12]],
            "eisberg_hinweise_anzahl": d.get("eisberge_anzahl"), "eisberg_hinweise": [eis(x) for x in d.get("eisberge", [])[:8]],
            "ordergroesse_je_stufe": d.get("ordergroesse"),
            "mittelkurs_verlauf": reihe([x[0] for x in d.get("mitte", [])], [x[1] for x in d.get("mitte", [])], n=12, stellen=3)}


def _d(d: dict, a: dict) -> dict:
    return {"titel": "Handelsplätze, Lead-Lag, Retail-Buch",
            "plaetze_kursqualitaet": d.get("plaetze"), "umsatz_je_platz": d.get("volumen"), "wer_bewegt_zuerst": d.get("fuehrung"),
            "retail_buch_gegen_regulaer": d.get("retail")}


def _e(d: dict, a: dict) -> dict:
    kyle = d.get("kyle", {})
    return {"titel": "Fortgeschritten (Lee-Ready, VPIN, Kyle)",
            "erklaerung": "kaeufer_anteil: Anteil käuferinitiierter Abschlüsse am Umsatz (Lee-Ready). VPIN hoch = einseitiger, womöglich "
                          "informierter Fluss. Kyle-Lambda: bp Kursänderung je 1 Mio. € Nettokauf, höher = weniger liquide.",
            "kaeufer_anteil_pct": d.get("kauf_anteil"), "vpin_mittel": d.get("vpin_mittel"), "vpin_eimer_stueck": d.get("vpin_eimer_stueck"),
            "kyle_lambda": {"bp_je_mio_eur": kyle.get("beta"), "r2": kyle.get("r2"), "n": kyle.get("n")},
            "kaufdruck_verlauf_pct": reihe(d.get("druck", {}).get("t", []), d.get("druck", {}).get("kauf", []), stellen=1),
            "vpin_verlauf": reihe(d.get("vpin", {}).get("t", []), d.get("vpin", {}).get("v", []), stellen=3)}


def _f(d: dict, a: dict) -> dict:
    def strat(s):
        k = s.get("kurve") or []
        return {**_ohne(s, "kurve"), "ergebnis_am_ende_bp": k[-1][1] if k else None}
    mm = d.get("marktmacher", {})
    return {"titel": "Strategietests (rückblickend, mit Spread als Kosten)",
            "parameter": d.get("parameter"),
            "obi_momentum": strat(d.get("momentum", {})), "wand_abprall": strat(d.get("abprall", {})),
            "market_maker": {**_ohne(mm, "kurve"), "ergebnis_am_ende_eur": (mm.get("kurve") or [[None, None]])[-1][1]},
            "hinweis": "15 Minuten Verzögerung: keine dieser Strategien ist live handelbar; sie prüfen, ob im Buch überhaupt Information steckt."}


def _auktionen(d: dict, a: dict) -> dict:
    modi = []
    for z in d.get("modi", []):
        modi.append({**_ohne(z, "zeit"), "erster_abschluss": _uhr(z.get("zeit")) if z.get("zeit") else None})
    return {"titel": "Auktionen und Handelsformen", "handelsformen": modi, "vergleich": d.get("vergleich"), "phasen": d.get("phasen")}


def signal_lage(wert: str) -> str:
    """Ein Absatz Signal-Lage aus dem Orderbuch (für Morgenlage und Telegram); Krypto live, DE Rückschau."""
    from . import signale
    return signale.absatz(signale.lage(wert, schritt=60))
