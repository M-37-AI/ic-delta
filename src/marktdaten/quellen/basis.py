"""Gemeinsame Schnittstelle aller Quellen."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Iterator

from ..modell import Ereignis


@dataclass
class Quelle:
    name: str           # Kurzname für die Kommandozeile
    titel: str
    region: str
    inhalt: str         # was geliefert wird
    tiefe: str          # Orderbuchtiefe
    abrufbar: str       # wie lange Dateien bereitliegen
    lizenz: str         # Nutzungsbedingung in einem Satz
    info_url: str       # offizielle Seite mit den Bedingungen
    arten: tuple[str, ...] = ("pretrade", "posttrade")
    schluessel: bool = False

    def dateien(self, art: str) -> list[str]:
        """Verfügbare Dateien, neueste zuerst."""
        raise NotImplementedError

    def lesen(self, datei: str, art: str) -> Iterator[Ereignis]:
        raise NotImplementedError

    def neueste(self, art: str) -> Iterator[Ereignis]:
        liste = self.dateien(art)
        if not liste:
            raise LookupError(f"{self.name}: keine Dateien für {art}")
        return self.lesen(liste[0], art)


def tabelle(zeilen: list[str], trenner: str = ",") -> Iterator[dict[str, str]]:
    """CSV mit Kopfzeile -> Zeilen mit vereinheitlichten Spaltennamen (klein, ohne Leerzeichen)."""
    r = csv.reader(zeilen, delimiter=trenner)
    kopf = [k.strip().lower().replace(" ", "") for k in next(r)]
    for z in r:
        if z:
            yield dict(zip(kopf, (w.strip() for w in z)))


def spalte(zeile: dict[str, str], *namen: str) -> str | None:
    """Erste vorhandene Spalte aus mehreren möglichen Namen (vereinheitlicht wie in tabelle())."""
    for n in namen:
        if n in zeile:
            return zeile[n]
    return None
