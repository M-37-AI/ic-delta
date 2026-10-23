"""Ports und Betriebsart an einer Stelle.

Drei Dienste gehören zusammen: Dashboard (Basisport), Analysen (Basis + 1) und Claude-Agent (Basis + 2).
Echt läuft iC Delta auf 8812/8813/8814. Die Demo (erfundene Daten für Videos und zum Ausprobieren) läuft mit
demselben Code daneben, z. B. auf 8822/8823/8824, mit eigenem Datenordner (MARKTDATEN_DATEN) und eigener
Konfiguration (MARKTDATEN_KONFIG, z. B. ein eigener Telegram-Bot).

  MARKTDATEN_PORT    Basisport (Standard 8812)
  MARKTDATEN_DEMO    "1": Demo-Betrieb, keine Abrufe bei Börsen, stattdessen der Demo-Sammler
  MARKTDATEN_KONFIG  Ordner für telegram.env und Ähnliches (Standard ~/.config/marktdaten)
"""
from __future__ import annotations

import os
import pathlib

BASIS = int(os.environ.get("MARKTDATEN_PORT", "8812"))
DASHBOARD, ANALYSEN, AGENT = BASIS, BASIS + 1, BASIS + 2
DEMO = os.environ.get("MARKTDATEN_DEMO") == "1"
# Video-Demo mit echten Namen: Aktien erfunden, Krypto echt (Binance live wie im normalen Betrieb)
DEMO_NAMEN_ECHT = DEMO and os.environ.get("MARKTDATEN_DEMO_NAMEN") == "echt"
KONFIG = pathlib.Path(os.environ.get("MARKTDATEN_KONFIG", "~/.config/marktdaten")).expanduser()
# Gegenstück für den Demo-Schalter im Dashboard: echt ↔ Demo (Basis 8812 ↔ 8822)
GEGENSTUECK = int(os.environ.get("MARKTDATEN_GEGENSTUECK", str(BASIS - 10 if DEMO else BASIS + 10)))


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}"
