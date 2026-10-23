"""EODHD (eodhd.com): Tageskerzen und Splits für deutsche Aktien (Xetra), damit ein neuer Wert sofort Historie hat.

Eigener Schlüssel nötig (Gratisplan: 20 Abrufe am Tag, letztes Jahr, nur private Nutzung). Der Schlüssel steht in
EODHD_API_TOKEN oder in ~/.config/marktdaten/eodhd.env und erscheint nie in Ausgaben oder Fehlermeldungen.
Jeder Abruf kostet einen der Tagesabrufe, auch ein fehlgeschlagener; deshalb gibt es keine automatischen Wiederholungen
und einen Zähler je Tag (UTC, so zählt EODHD) in ~/.local/share/marktdaten/eodhd.json.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..netz import USER_AGENT, zaehlen
from .basis import Quelle

UTC = timezone.utc
API = "https://eodhd.com/api"
STAND = pathlib.Path("~/.local/share/marktdaten/eodhd.json").expanduser()
_lock = threading.Lock()


def _datei() -> pathlib.Path:
    return pathlib.Path(os.environ.get("MARKTDATEN_EODHD_ENV", "~/.config/marktdaten/eodhd.env")).expanduser()


def schluessel_vorhanden() -> bool:
    """Nur ob Variable oder Datei da sind; der Inhalt wird hier nicht gelesen."""
    return bool(os.environ.get("EODHD_API_TOKEN")) or _datei().exists()


def _token() -> str:
    t = os.environ.get("EODHD_API_TOKEN", "")
    if not t and _datei().exists():
        for z in _datei().read_text().splitlines():
            k, _, v = z.partition("=")
            if k.strip() == "EODHD_API_TOKEN":
                t = v.strip()
    if not t:
        raise RuntimeError("EODHD-Schlüssel fehlt: EODHD_API_TOKEN in ~/.config/marktdaten/eodhd.env eintragen")
    return t


class Kontingent(Exception):
    """Tagesgrenze der Abrufe erreicht."""


def stand() -> dict:
    """Zähler und Merkzettel: {tag, abrufe, letzter, kuerzel: {id: Code}, werte: {id: {...}}}."""
    with _lock:
        try:
            d = json.loads(STAND.read_text())
        except (OSError, ValueError):
            d = {}
    heute = datetime.now(UTC).date().isoformat()
    if d.get("tag") != heute:
        d.update(tag=heute, abrufe=0)
    d.setdefault("kuerzel", {}); d.setdefault("werte", {})
    return d


def _sichern(d: dict) -> None:
    with _lock:
        STAND.parent.mkdir(parents=True, exist_ok=True)
        tmp = STAND.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        tmp.replace(STAND)


def merken(**werte) -> None:
    d = stand(); d.update(werte); _sichern(d)


def abruf(pfad: str, max_tag: int, **params) -> list | dict:
    """Ein Abruf, ohne Wiederholung. Zählt vor dem Abruf (EODHD zählt auch Fehlversuche)."""
    d = stand()
    if d["abrufe"] >= max_tag:
        raise Kontingent(f"Tagesgrenze erreicht ({d['abrufe']} von {max_tag} Abrufen)")
    token = _token()
    d["abrufe"] += 1
    d["letzter"] = datetime.now(UTC).isoformat(timespec="seconds")
    _sichern(d)
    url = f"{API}/{pfad}?" + urllib.parse.urlencode({**params, "fmt": "json", "api_token": token})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as r:
            roh = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"EODHD antwortet {e.code} für {pfad}") from None      # ohne URL: dort steht der Schlüssel
    except Exception as e:
        raise RuntimeError(f"EODHD nicht erreichbar ({type(e).__name__})") from None
    zaehlen("EODHD", len(roh))
    return json.loads(roh)


class Eodhd(Quelle):
    def __init__(self):
        super().__init__(
            name="eodhd", titel="EODHD (Tageskerzen DE)", region="DE",
            inhalt="Tageskerzen und Splits deutscher Aktien (Xetra) als Historie für neue Watchlist-Werte",
            tiefe="keine", abrufbar="Gratisplan: letztes Jahr, 20 Abrufe am Tag", arten=("bars",), schluessel=True,
            lizenz="eigenes Konto nötig; Gratisplan nur für private Nutzung, keine Weitergabe oder Veröffentlichung",
            info_url="https://eodhd.com/financial-apis/api-for-historical-data-and-volumes",
        )

    def dateien(self, art: str) -> list[str]:
        raise NotImplementedError("EODHD hat keine Dateien: tageskerzen() oder splits() verwenden")

    @staticmethod
    def tageskerzen(code: str, von: str, max_tag: int) -> list[dict]:
        """[{date, open, high, low, close, adjusted_close, volume}] ab von (YYYY-MM-DD). OHLC roh, Volumen splitbereinigt."""
        r = abruf(f"eod/{code}", max_tag, **{"from": von})
        if not isinstance(r, list):
            raise RuntimeError(f"EODHD: unerwartete Antwort für {code}")
        return [x for x in r if isinstance(x, dict) and x.get("date")]

    @staticmethod
    def splits(code: str, max_tag: int) -> list[dict]:
        """[{date, split: "neu/alt"}] seit 2000."""
        r = abruf(f"splits/{code}", max_tag, **{"from": "2000-01-01"})
        return r if isinstance(r, list) else []

    @staticmethod
    def suchen(isin: str, max_tag: int) -> str | None:
        """Kürzel für eine ISIN an Xetra, z. B. "SAP.XETRA" (Rückfall, wenn Watchlist-Kürzel nicht passt)."""
        r = abruf(f"search/{urllib.parse.quote(isin)}", max_tag, exchange="XETRA", limit=5)
        for x in r if isinstance(r, list) else []:
            if x.get("ISIN") == isin and x.get("Exchange") == "XETRA" and x.get("Code"):
                return f"{x['Code']}.XETRA"
        return None


EODHD = Eodhd()
