"""Erreichbarkeit und Aktualität der Quellen prüfen, ohne Marktdaten zu laden.

Gelesen werden nur Dateilisten oder Kopfzeilen der Antworten (HEAD), nie Kurse.
"""
from __future__ import annotations

import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from .netz import USER_AGENT, holen_json
from .quellen import QUELLEN
from .quellen import eodhd
from .quellen.cboe_europe import BASIS as CBOE_BASIS
from .quellen.referenz import QUELLEN as REFERENZ

UTC = timezone.utc
BERLIN, STOCKHOLM = ZoneInfo("Europe/Berlin"), ZoneInfo("Europe/Stockholm")


def _kopf(url: str) -> dict:
    """HEAD-Anfrage: Status und Last-Modified, ohne Inhalt."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return {"status": r.status, "geaendert": r.headers.get("Last-Modified")}


def _zeit(muster: str, text: str, fmt: str, zone=UTC) -> datetime | None:
    m = re.search(muster, text)
    return datetime.strptime(m[1], fmt).replace(tzinfo=zone).astimezone(UTC) if m else None


def _neueste(name: str) -> tuple[int | None, datetime | None, str]:
    """(Anzahl Dateien, Zeit der neuesten, Name der neuesten) je Quelle."""
    q = QUELLEN[name]
    if name in ("xetra", "tradegate", "frankfurt", "eurex"):
        liste = q.dateien(q.arten[-1] if name == "eurex" else "pretrade")
        return len(liste), _zeit(r"(\d{4}-\d\d-\d\dT\d\d_\d\d)", liste[0], "%Y-%m-%dT%H_%M"), liste[0]
    if name == "cboe":
        for d in q.dateien("posttrade")[:120]:                 # neueste vorhandene Minute suchen
            try:
                _kopf(CBOE_BASIS + d)
                return None, _zeit(r"_(\d{8}_\d{4})\.csv", d, "%Y%m%d_%H%M"), d.rsplit("/", 1)[-1]
            except urllib.error.HTTPError:
                continue
        return None, None, "keine Datei von heute"
    if name == "nordic":
        liste = q.dateien("pretrade")
        return len(liste), _zeit(r"(\d{4}-\d\d-\d\dT\d{4})", liste[0], "%Y-%m-%dT%H%M", STOCKHOLM), liste[0].split("/")[-1]
    if name == "boeag":
        liste = q.dateien("pretrade")
        return len(liste), _zeit(r"_(\d{14})\d{4}\.csv", liste[0], "%Y%m%d%H%M%S", BERLIN), liste[0]
    if name == "gettex":
        liste = q.dateien("pretrade")
        return len(liste), _zeit(r"\.(\d{8}\.\d\d\.\d\d)\.", liste[0], "%Y%m%d.%H.%M"), liste[0].rsplit("/", 1)[-1]
    if name == "ngm":
        liste = q.dateien("pretrade")
        return len(liste), _zeit(r"(\d{4}-\d\d-\d\dT\d{4})", liste[0], "%Y-%m-%dT%H%M"), liste[0]
    if name == "equiduct":
        k = _kopf(q.BASIS + "equiduct_trades.csv")
        return 2, parsedate_to_datetime(k["geaendert"]) if k["geaendert"] else None, "equiduct_trades.csv"
    if name == "euronext":
        k = _kopf("https://marketdata.euronext.com/data-reporting-service/trades-file")
        return None, None, f"Downloadseite antwortet ({k['status']})"
    if name == "iex":
        r = holen_json("https://iextrading.com/api/1.0/hist")
        tag = max(r)
        return len(r), datetime.strptime(tag, "%Y%m%d").replace(tzinfo=UTC), f"Handelstag {tag}"
    raise KeyError(name)


def _schluessel_vorhanden() -> bool:
    """Prüft nur, ob Umgebungsvariablen oder Schlüsseldatei da sind. Der Inhalt wird nicht gelesen."""
    if os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"):
        return True
    return pathlib.Path(os.environ.get("MARKTDATEN_ALPACA_ENV", "~/.config/marktdaten/alpaca.env")).expanduser().exists()


def pruefen(name: str) -> dict:
    t0 = time.perf_counter()
    erg = {"quelle": name, "geprueft": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        if name == "alpaca":
            ok = _schluessel_vorhanden()
            erg.update(zustand="eingerichtet" if ok else "ohne_schluessel",
                       hinweis="Schlüsseldatei gefunden (nicht geprüft)" if ok else "eigener Schlüssel nötig, siehe .env.example")
            return erg
        if name == "eodhd":                                       # kein Abruf: jeder kostet einen der Tagesabrufe
            ok, st = eodhd.schluessel_vorhanden(), eodhd.stand()
            werte = st["werte"]
            erg.update(zustand="eingerichtet" if ok else "ohne_schluessel",
                       hinweis=(f"Historie für {len(werte)} Werte · heute {st['abrufe']} Abrufe"
                                + (f" · zuletzt {st['letzter'][:16].replace('T', ' ')} UTC" if st.get("letzter") else "")) if ok
                       else "eigener Schlüssel nötig, siehe .env.example")
            return erg
        anzahl, zeit, neueste = _neueste(name)
        erg.update(zustand="erreichbar", dateien=anzahl, neueste=neueste,
                   zeit=zeit.isoformat(timespec="minutes") if zeit else None,
                   alter_min=round((datetime.now(UTC) - zeit).total_seconds() / 60) if zeit else None)
    except Exception as e:                                       # Anzeige statt Absturz
        erg.update(zustand="fehler", hinweis=f"{type(e).__name__}: {str(e)[:120]}")
    finally:
        erg["antwort_ms"] = round((time.perf_counter() - t0) * 1000)
    return erg


def referenz_pruefen() -> list[dict]:
    gestern = datetime.now(UTC) - timedelta(days=1)
    while gestern.weekday() > 4:
        gestern -= timedelta(days=1)
    ziele = {"finra": f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{gestern:%Y%m%d}.txt",
             "nasdaq_symbole": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
             "sec": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
             "ezb": "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?lastNObservations=1"}

    def eins(name):
        t0 = time.perf_counter()
        titel, inhalt, info = REFERENZ[name]
        d = {"quelle": name, "titel": titel, "inhalt": inhalt, "info_url": info}
        for versuch in (1, 2):                                   # ein zweiter Versuch gegen kurze Aussetzer
            try:
                _kopf(ziele[name])
                d.pop("hinweis", None)
                d["zustand"] = "erreichbar"
                break
            except Exception as e:
                d.update(zustand="fehler", hinweis=f"{type(e).__name__}: {str(e)[:80]}")
        d["antwort_ms"] = round((time.perf_counter() - t0) * 1000)
        return d

    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(eins, ziele))


def alle() -> dict:
    with ThreadPoolExecutor(8) as ex:
        quellen = list(ex.map(pruefen, QUELLEN))
    return {"quellen": quellen, "referenz": referenz_pruefen(),
            "stand": datetime.now(UTC).isoformat(timespec="seconds")}
