"""Schreibt die Puffer des Kursstands alle 20 Sekunden in den Speicher und räumt nachts auf.

Beendet außerdem Orderbuch-Aufnahmen, deren Zeit abgelaufen ist.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .zustand import Zustand

UTC = timezone.utc


class Schreiber(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="schreiber")
        self.z = zustand
        self.s = zustand.speicher
        self.aufgeraeumt = None

    def einmal(self) -> None:
        p = self.z.puffer_leeren()
        for id_, zeilen in p["kerzen"].items():
            self.s.kerzen("kerzen_1m", id_, zeilen)
        self.s.abschluesse(p["trades"])
        self.s.buch(p["buch"])
        self.s.roh(p["roh"])
        self.s.downloads_sichern()
        jetzt = datetime.now(UTC).isoformat(timespec="seconds")
        for a in self.s.aufnahmen():                       # abgelaufene Aufnahmen beenden
            if not a["ende"] and a["bis"] <= jetzt:
                self.aufnahme_beenden(a["id"], a["nr"])
        self.z.melden("speicher", geschrieben=jetzt, fehler=None)

    def aufnahme_beenden(self, id_: str, nr: int) -> None:
        with self.z.lock:
            if self.z.aufnahmen.get(id_) == nr:
                del self.z.aufnahmen[id_]
            self.z.geaendert()
        self.s.aufnahme_beenden(nr)

    def run(self) -> None:
        while True:
            try:
                self.einmal()
                tag = datetime.now().date()
                if datetime.now().hour == 3 and self.aufgeraeumt != tag:   # nachts aufräumen
                    self.z.melden("aufraeumen", **self.s.aufraeumen())
                    self.aufgeraeumt = tag
            except Exception as e:
                self.z.melden("speicher", fehler=f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(20)
