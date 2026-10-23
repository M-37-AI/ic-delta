"""Logos der Watchlist-Werte: einmal holen, lokal ablegen, vom Dashboard ausliefern.

Quellen je Markt (nur der Kern holt, jedes Logo einmal):
  - Krypto: cryptocurrency-icons (CC0, gemeinfrei) über jsDelivr, farbige SVG je Kürzel.
  - Aktien (Deutschland und USA): Logo.dev über die Firmen-Domain, bei US-Werten ohne bekannte Domain über das
    Kürzel. Braucht einen eigenen, kostenlosen Schlüssel (publishable key, pk_…) in LOGODEV_TOKEN oder
    ~/.config/marktdaten/logos.env. Ohne Schlüssel bleibt der Kürzel-Kreis. (Der Logo-Endpunkt von Alpaca ist im
    Gratiszugang gesperrt, HTTP 403.)
  - Eigene Dateien haben Vorrang: <Daten>/logos/eigen/<SYMBOL>.svg|png|jpg|webp, z. B. eigen/ADS.svg.
Logos sind Marken, kein Open Source: Sie liegen nur im Datenordner, nie im Repo. Fehlschläge werden gemerkt
und erst nach sieben Tagen erneut versucht.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time

from .. import netz
from .speicher import ORT
from .zustand import Zustand

# Video-Demo: Logos des echten Betriebs nur lesen (MARKTDATEN_LOGOS); im Demo-Betrieb wird nichts geholt
ORDNER = pathlib.Path(os.environ["MARKTDATEN_LOGOS"]).expanduser() if os.environ.get("MARKTDATEN_LOGOS") else ORT / "logos"
EIGEN = ORDNER / "eigen"
ENDUNG = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
WIEDER_S = 7 * 86400
KRYPTO = "https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color/{}.svg"
LOGODEV = "https://img.logo.dev/{}?token={}&size=128&format=png&retina=true"           # {} = Domain oder ticker/<KÜRZEL>
# Firmen-Domains für Logo.dev nach Xetra-Kürzel (DAX und häufige Werte); fehlt ein Wert, hilft eine eigene Datei
DOMAINS = {
    "ADS": "adidas.com", "AIR": "airbus.com", "ALV": "allianz.com", "BAS": "basf.com", "BAYN": "bayer.com", "BEI": "beiersdorf.com",
    "BMW": "bmwgroup.com", "BNR": "brenntag.com", "CBK": "commerzbank.de", "CON": "continental.com", "DB1": "deutsche-boerse.com",
    "DBK": "db.com", "DHL": "dhl.com", "DTE": "telekom.com", "DTG": "daimlertruck.com", "ENR": "siemens-energy.com", "EOAN": "eon.com",
    "FRE": "fresenius.com", "G1A": "gea.com", "HEI": "heidelbergmaterials.com", "HEN3": "henkel.com", "HNR1": "hannover-re.com",
    "IFX": "infineon.com", "MBG": "mercedes-benz.com", "MRK": "merckgroup.com", "MTX": "mtu.de", "MUV2": "munichre.com",
    "P911": "porsche.com", "PAH3": "porsche-se.com", "QIA": "qiagen.com", "RHM": "rheinmetall.com", "RWE": "rwe.com", "SAP": "sap.com",
    "SHL": "siemens-healthineers.com", "SIE": "siemens.com", "SRT3": "sartorius.com", "SY1": "symrise.com", "VNA": "vonovia.com",
    "VOW3": "volkswagen-group.com", "ZAL": "zalando.com", "LHA": "lufthansagroup.com", "PUM": "puma.com", "TKA": "thyssenkrupp.com",
}
DOMAINS_US = {"AAPL": "apple.com", "AMZN": "amazon.com", "MSFT": "microsoft.com", "NVDA": "nvidia.com", "TSLA": "tesla.com",
              "GOOGL": "google.com", "GOOG": "google.com", "META": "meta.com", "NFLX": "netflix.com", "AMD": "amd.com"}


def _logodev_token() -> str:
    t = os.environ.get("LOGODEV_TOKEN", "")
    datei = pathlib.Path(os.environ.get("MARKTDATEN_LOGOS_ENV", "~/.config/marktdaten/logos.env")).expanduser()
    if not t and datei.exists():
        for z in datei.read_text().splitlines():
            k, _, v = z.partition("=")
            if k.strip() == "LOGODEV_TOKEN":
                t = v.strip()
    return t


def _dateiname(id_: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in id_)


def datei(id_: str, symbol: str = "") -> pathlib.Path | None:
    """Vorhandenes Logo eines Werts: eigene Datei vor geholter."""
    for ordner, name in ((EIGEN, symbol), (ORDNER, _dateiname(id_))):
        if name:
            for e in ENDUNG:
                p = ordner / (name + e)
                if p.exists() and p.stat().st_size > 0:
                    return p
    return None


def _ist_bild(roh: bytes, endung: str) -> bool:
    if endung == ".svg":
        return b"<svg" in roh[:2000]
    return roh[:8] == b"\x89PNG\r\n\x1a\n" or roh[:3] == b"\xff\xd8\xff" or roh[8:12] == b"WEBP"


class Logos(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="logos")
        self.z = zustand
        self.stand_datei = ORDNER / "stand.json"

    def _stand(self) -> dict:
        try:
            return json.loads(self.stand_datei.read_text())
        except (OSError, ValueError):
            return {}

    def holen(self, w) -> tuple[bytes, str] | None:
        """Logo eines Werts aus der Quelle seines Markts; None, wenn es keine gibt."""
        if w.markt == "KRYPTO":
            kurz = w.symbol.removesuffix("USDT").removesuffix("USDC").removesuffix("EUR").lower()
            return netz.holen(KRYPTO.format(kurz), versuche=1, timeout=30), ".svg"
        token = _logodev_token()
        if not token:
            return None
        if w.markt == "US":
            ziel = DOMAINS_US.get(w.symbol) or f"ticker/{w.symbol}"
        else:
            ziel = DOMAINS.get(w.symbol)
        return (netz.holen(LOGODEV.format(ziel, token), versuche=1, timeout=30), ".png") if ziel else None

    def runde(self) -> None:
        ORDNER.mkdir(parents=True, exist_ok=True)
        EIGEN.mkdir(exist_ok=True)
        stand = self._stand()
        with self.z.lock:
            werte = list(self.z.werte.values())
        for w in werte:
            if datei(w.id, w.symbol) or time.time() - stand.get(w.id, {}).get("versuch", 0) < WIEDER_S:
                continue
            try:
                r = self.holen(w)
                if r is None:
                    continue                                    # keine Quelle (z. B. DE ohne Schlüssel): später wieder prüfen
                roh, endung = r
                if not _ist_bild(roh, endung):
                    raise ValueError("kein Bild")
                (ORDNER / (_dateiname(w.id) + endung)).write_bytes(roh)
                stand[w.id] = {"versuch": time.time(), "ok": True}
            except Exception as e:                              # Fehler ohne Adresse melden (Schlüssel stehen in der URL)
                stand[w.id] = {"versuch": time.time(), "ok": False, "fehler": f"{type(e).__name__} {getattr(e, 'code', '')}".strip()}
            self.stand_datei.write_text(json.dumps(stand))
        self.z.melden("logos", vorhanden=sum(1 for w in werte if datei(w.id, w.symbol)), werte=len(werte))

    def run(self) -> None:
        while True:
            try:
                self.runde()
            except Exception as e:
                self.z.melden("logos", fehler=type(e).__name__)
            time.sleep(300)


def uebersicht(zustand: Zustand) -> dict:
    """Adresse je Wert für die Seite: {id: "/logo/<id>?v=<stand>"} und dasselbe nach Kürzel."""
    with zustand.lock:
        werte = list(zustand.werte.values())
    je_id, je_symbol = {}, {}
    for w in werte:
        p = datei(w.id, w.symbol)
        if p:
            adresse = f"/logo/{w.id}?v={int(p.stat().st_mtime)}"
            je_id[w.id] = je_symbol[w.symbol] = adresse
    return {"id": je_id, "symbol": je_symbol}
