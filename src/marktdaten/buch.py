"""Orderbuch aus BuchSeite- und BuchStufe-Ereignissen fortschreiben."""
from __future__ import annotations

from collections import defaultdict

from .modell import KAUF, VERKAUF, BuchSeite, BuchStufe, Ereignis

Schluessel = tuple[str, str, str]   # (venue, instrument, buch)


class Orderbuch:
    def __init__(self):
        self.seiten: dict[Schluessel, dict[str, dict[float, tuple[float, int | None]]]] = \
            defaultdict(lambda: {KAUF: {}, VERKAUF: {}})
        self.zeit: dict[Schluessel, int] = {}

    def anwenden(self, e: Ereignis) -> Schluessel | None:
        """Verarbeitet ein Ereignis; gibt den betroffenen Buch-Schlüssel zurück (sonst None)."""
        if isinstance(e, BuchSeite):
            k = (e.venue, e.instrument, e.buch)
            self.seiten[k][e.seite] = {p: (m, o) for p, m, o in e.stufen if m}
        elif isinstance(e, BuchStufe):
            k = (e.venue, e.instrument, e.buch)
            seite = self.seiten[k][e.seite]
            if e.menge:
                seite[e.preis] = (e.menge, e.orders)
            else:
                seite.pop(e.preis, None)
        else:
            return None
        self.zeit[k] = e.ts
        return k

    def stufen(self, k: Schluessel, seite: str, n: int = 5) -> list[tuple[float, float, int | None]]:
        s = self.seiten[k][seite]
        preise = sorted(s, reverse=(seite == KAUF))[:n]
        return [(p, *s[p]) for p in preise]

    def bestes(self, k: Schluessel) -> tuple[float | None, float | None]:
        b, a = self.stufen(k, KAUF, 1), self.stufen(k, VERKAUF, 1)
        return (b[0][0] if b else None, a[0][0] if a else None)

    def spread_bp(self, k: Schluessel) -> float | None:
        b, a = self.bestes(k)
        if b is None or a is None or a <= b:
            return None
        return (a - b) / ((a + b) / 2) * 1e4

    def ungleichgewicht(self, k: Schluessel, n: int = 5) -> float | None:
        """(Kaufmenge - Verkaufsmenge) / Summe über die besten n Stufen, zwischen -1 und +1."""
        kauf = sum(m for _, m, _ in self.stufen(k, KAUF, n))
        verkauf = sum(m for _, m, _ in self.stufen(k, VERKAUF, n))
        return (kauf - verkauf) / (kauf + verkauf) if kauf + verkauf else None
