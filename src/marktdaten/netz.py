"""HTTP-Abruf ohne Zusatzpakete. Entpackt gzip und zip automatisch."""
from __future__ import annotations

import gzip
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import __version__

USER_AGENT = f"ic-delta/{__version__}"

# Download-Zähler: wer den Verbrauch erfassen will (z. B. das Dashboard), setzt ZAEHLER = funktion(quelle, bytes).
ZAEHLER = None
HOSTS = {"mfs.deutsche-boerse.com": "Deutsche Börse", "storage.googleapis.com": "Deutsche Börse",
         "www.cashmarket.deutsche-boerse.com": "Deutsche Börse", "cdn.cboe.com": "Cboe Europe",
         "data.alpaca.markets": "Alpaca", "api.binance.com": "Binance", "fapi.binance.com": "Binance Futures", "www.nasdaqtrader.com": "Nasdaq",
         "iextrading.com": "IEX", "marketdata.euronext.com": "Euronext", "www.gettex.de": "gettex",
         "erdk.bayerische-boerse.de": "gettex", "cld42.boersenag.de": "BÖAG", "mdapi.ngm.se": "NGM",
         "tradereports.nasdaq.com": "Nasdaq Nordic", "download.equiduct.com": "Equiduct", "cdn.finra.org": "FINRA",
         "data.sec.gov": "SEC", "data-api.ecb.europa.eu": "EZB", "api.finra.org": "FINRA", "eodhd.com": "EODHD"}


def zaehlen(quelle_oder_url: str, bytes_: int) -> None:
    if ZAEHLER:
        host = urllib.parse.urlparse(quelle_oder_url).hostname if "://" in quelle_oder_url else None
        ZAEHLER(HOSTS.get(host, host or "andere") if host else quelle_oder_url, bytes_)   # Nasdaq blockiert Zusätze wie "(+https://...)"



def _ssl_kontext() -> ssl.SSLContext:
    """Zertifikate: SSL_CERT_FILE, sonst ein vollständiges Bündel (Homebrew, certifi), sonst Systemstandard.
    Manche Pythons (z. B. von uv) kennen neuere Wurzeln nicht, etwa die der EZB (Sectigo E46)."""
    kandidaten = [os.environ.get("SSL_CERT_FILE"), "/opt/homebrew/etc/ca-certificates/cert.pem",
                  "/usr/local/etc/ca-certificates/cert.pem"]
    try:
        import certifi
        kandidaten.append(certifi.where())
    except ImportError:
        pass
    for datei in kandidaten:
        if datei and os.path.exists(datei):
            return ssl.create_default_context(cafile=datei)
    return ssl.create_default_context()


# Gilt für alle urlopen-Aufrufe im Paket, auch in den Quellen und im Status.
urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_kontext())))


def holen(url: str, kopf: dict | None = None, daten: bytes | None = None,
          versuche: int = 3, timeout: int = 120) -> bytes:
    h = {"User-Agent": USER_AGENT, **(kopf or {})}
    for i in range(versuche):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=daten, headers=h), timeout=timeout) as r:
                roh = r.read()
                zaehlen(url, len(roh))
                return entpacken(roh)
        except urllib.error.HTTPError as e:
            if (e.code < 500 and e.code != 429) or i == versuche - 1:
                raise
            if e.code == 429:                               # zu viele Anfragen: länger warten
                time.sleep(float(e.headers.get("Retry-After") or 15 * 2 ** i))
                continue
        except (urllib.error.URLError, TimeoutError):
            if i == versuche - 1:
                raise
        time.sleep(2 ** i)
    raise RuntimeError("unerreichbar")


def entpacken(b: bytes) -> bytes:
    if b[:2] == b"\x1f\x8b":
        return gzip.decompress(b)
    if b[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(b))
        return z.read(z.namelist()[0])
    return b


def zeilen_strom(url: str, max_mb: float | None = None, timeout: int = 60):
    """Liest eine (gz-)Textdatei zeilenweise, ohne sie ganz zu laden. max_mb begrenzt die übertragene Menge."""
    antwort = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)
    zaehler = Zaehler(antwort)
    roh = io.BufferedReader(zaehler)
    quelle = gzip.GzipFile(fileobj=roh) if url.endswith(".gz") else roh
    try:
        for zeile in quelle:
            yield zeile.decode("utf-8", errors="replace").rstrip("\r\n")
            if max_mb is not None and zaehler.n > max_mb * 1e6:
                return
    finally:
        antwort.close()


class Zaehler(io.RawIOBase):
    """Zählt die übertragenen Bytes eines Datenstroms."""

    def __init__(self, r):
        self.r, self.n = r, 0

    def readable(self):
        return True

    def readinto(self, puffer):
        b = self.r.read(len(puffer))
        self.n += len(b)
        puffer[:len(b)] = b
        return len(b)

    def read(self, k=-1):
        b = self.r.read(k)
        self.n += len(b)
        return b


def holen_json(url: str, **kw):
    return json.loads(holen(url, **kw))


def text(b: bytes) -> list[str]:
    return b.decode("utf-8-sig", errors="replace").splitlines()
