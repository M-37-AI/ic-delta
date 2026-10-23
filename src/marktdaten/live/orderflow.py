"""Orderflow für iC Charts: Kauf-/Verkaufsvolumen je Minute und Volumenprofil aus den gespeicherten Abschlüssen.

Liest nur die Tabelle abschluesse (keine eigenen Abrufe). Die Börsendateien nennen die Aggressor-Seite nicht, deshalb
wird jeder Abschluss mit der Tick-Regel eingeordnet (Lee und Ready, „Inferring Trade Direction from Intraday Data“, 1991):
höher als der vorige Preis = Kauf, tiefer = Verkauf, gleich = wie der vorige Abschluss. Bis zum ersten Preiswechsel
bleibt ein Abschluss neutral (zählt zum Volumen, nicht zum Delta). Das ist eine Näherung und wird so gekennzeichnet.

Nur der Hauptplatz zählt (Xetra bzw. IEX), damit sich Preise verschiedener Plätze nicht gegenseitig einordnen.
Xetra-Auktionen haben keine Aggressor-Seite: Eröffnung (erster Abschluss des Tages bis 9:05), Mittagsauktion (erster
Abschluss ab 13:00 bis 13:05) und Schlussauktion (17:30–17:40) zählen zum Volumen, aber neutral (nicht zum Delta).
"""
from __future__ import annotations

import math
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from .zustand import HAUPTPLATZ, PLATZ

TAGE = 10                                          # wie die Minutenkerzen im Schnappschuss von iC Charts
HINWEIS = {
    "DE": "Xetra-Abschlüsse, Seite per Tick-Regel geschätzt, Auktionen neutral",
    "US": "nur IEX-Abschlüsse (rund 4 % des US-Volumens, Stichprobe), Seite per Tick-Regel geschätzt",
}
QUELLE = {"DE": "Xetra", "US": "IEX"}

BERLIN = ZoneInfo("Europe/Berlin")
_cache: dict[str, tuple] = {}


def _auktion(t: int, vorher: int | None) -> bool:
    """Xetra: liegt der Abschluss in einem Auktionsfenster (Zeit in Berlin, Minuten seit Mitternacht)?"""
    z = datetime.fromtimestamp(t / 1e9, BERLIN)
    m = z.hour * 60 + z.minute
    if 17 * 60 + 30 <= m < 17 * 60 + 40:
        return True
    v = datetime.fromtimestamp(vorher / 1e9, BERLIN) if vorher else None
    if m < 9 * 60 + 5 and (v is None or v.date() != z.date()):
        return True
    return 13 * 60 <= m < 13 * 60 + 5 and v is not None and v.date() == z.date() and v.hour * 60 + v.minute < 13 * 60
_lock = threading.Lock()


def _eingeordnet(speicher, w) -> list[tuple[int, float, float, int]]:
    """(t_ns, preis, menge, seite) aufsteigend; seite 1 = Kauf, -1 = Verkauf, 0 = neutral. Zwischengespeichert je Datenstand."""
    platz = HAUPTPLATZ[w.markt]
    venues = sorted({v for v, p in PLATZ.items() if p == platz} | {platz})
    frage = ",".join("?" * len(venues))
    stand = speicher._x(f"SELECT COUNT(*), MAX(t) FROM abschluesse WHERE id = ? AND venue IN ({frage})", (w.id, *venues))[0]
    with _lock:
        alt = _cache.get(w.id)
        if alt and alt[0] == stand:
            return alt[1]
    zeilen = speicher._x(f"SELECT t, preis, menge FROM abschluesse WHERE id = ? AND venue IN ({frage}) ORDER BY t", (w.id, *venues))
    aus, vorher, seite, t_vorher = [], None, 0, None
    for t, preis, menge in zeilen:
        if vorher is not None and preis != vorher:
            seite = 1 if preis > vorher else -1
        auktion = w.markt == "DE" and _auktion(t, t_vorher)
        aus.append((t, preis, menge, 0 if auktion else seite))
        vorher, t_vorher = preis, t
    with _lock:
        _cache[w.id] = (stand, aus)
    return aus


def _schritt(spanne: float, stufen: int) -> float:
    """Runde Stufengröße (1, 2, 2,5 oder 5 mal Zehnerpotenz), damit die Preise lesbar bleiben."""
    roh = max(spanne / max(stufen, 1), 1e-9)
    e = 10 ** math.floor(math.log10(roh))
    return next(f * e for f in (1, 2, 2.5, 5, 10) if f * e >= roh)


def orderflow(speicher, w, profil: bool = False, von: int | None = None, bis: int | None = None, stufen: int = 40) -> dict | None:
    """Minutenwerte [[t_s, kauf, verkauf]] der letzten TAGE Tage oder (profil=True) das Volumenprofil im Zeitraum von…bis (Unix-Sekunden)."""
    if w.markt not in HINWEIS:
        return None
    alle = _eingeordnet(speicher, w)
    if not alle:
        return {"quelle": QUELLE[w.markt], "hinweis": "noch keine Abschlüsse gespeichert", "minuten": [], "anzahl": 0}
    kopf = {"quelle": QUELLE[w.markt], "hinweis": HINWEIS[w.markt]}
    if profil:
        a, b = (von or 0) * 10**9, (bis or 2**62 // 10**9) * 10**9
        teil = [x for x in alle if a <= x[0] < b]
        if not teil:
            return {**kopf, "profil": {"schritt": 0, "levels": [], "anzahl": 0}}
        lo, hi = min(x[1] for x in teil), max(x[1] for x in teil)
        s = _schritt(hi - lo, max(4, min(int(stufen), 200)))
        basis = math.floor(lo / s + 1e-9) * s
        n = int(math.floor((hi - basis) / s + 1e-9)) + 1
        levels = [[round(basis + k * s, 10), 0.0, 0.0, 0.0] for k in range(n)]
        for _, preis, menge, seite in teil:
            k = min(n - 1, int(math.floor((preis - basis) / s + 1e-9)))
            levels[k][1 if seite > 0 else 2 if seite < 0 else 3] += menge
        return {**kopf, "profil": {"schritt": s, "levels": levels, "anzahl": len(teil)}}
    grenze = (alle[-1][0] // 10**9 - TAGE * 86400) * 10**9
    minuten: dict[int, list] = {}
    for t, _, menge, seite in alle:
        if t < grenze or seite == 0:
            continue
        m = minuten.setdefault(t // 60_000_000_000 * 60, [0.0, 0.0])
        m[0 if seite > 0 else 1] += menge
    return {**kopf, "von": alle[0][0] // 10**9, "bis": alle[-1][0] // 10**9, "anzahl": len(alle),
            "minuten": [[t, round(k, 6), round(v, 6)] for t, (k, v) in sorted(minuten.items())]}
