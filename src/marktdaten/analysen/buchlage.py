"""Claudes Einordnung des Orderbuchs für den Bereich Orderbuch im Dashboard.

Läuft nur auf Knopfdruck. Die Seite schickt die Kennzahlen, die sie gerade zeigt (Spread, Kosten, Tiefe, Druck, Wände),
Claude fasst sie in einem Satz zusammen. Das Ergebnis wird je Wert gespeichert und beim nächsten Öffnen wieder gezeigt.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from . import notizen
from .alarme import agent

DATEI = notizen.ORT / "orderbuch_claude.json"
_lock = threading.Lock()


def _alle() -> dict:
    try:
        return json.loads(DATEI.read_text())
    except (OSError, ValueError):
        return {}


def lesen(wert: str) -> dict | None:
    return _alle().get(str(wert).upper())


def einordnen(wert: str, name: str, fakten: dict) -> dict:
    text = (f"Ordne das Orderbuch von {name} ({wert}) für Niko ein: genau ein Satz, höchstens 35 Wörter, ohne Markdown, ohne Überschrift, "
            "ohne Kauf- oder Verkaufsempfehlung. Nenne, was im Buch gerade auffällt (Druck einer Seite, große Wände und was mit ihnen passiert ist, "
            "Kosten einer Order) mit einer Zahl. Nur zurückgezogene Wände mit nah_am_kurs = true sind ein Muster wie bei Spoofing (kein Beweis); andere zurückgezogene Wände nicht so nennen. Druck positiv = Käufer überwiegen, in Prozent. "
            "Nutze nur diese Kennzahlen, keine Werkzeuge:\n" + json.dumps(fakten, ensure_ascii=False)[:6000])
    r = agent("/chartanalyse", {"text": text}, timeout=180)
    if r.get("fehler"):
        raise ValueError(r["fehler"])
    e = {"text": str(r.get("text") or "").strip()[:600], "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "modell": r.get("modell"), "kosten": r.get("kosten")}
    with _lock:
        alle = _alle()
        alle[str(wert).upper()] = e
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        DATEI.write_text(json.dumps(alle, ensure_ascii=False))
    return e
