"""Nur lesen: Rohdateien, die das Dashboard beim Sammeln ablegt, und die Watchlist aus dessen Speicher.

Die Analysen rufen selbst nichts bei den Börsen ab. Das Dashboard legt die gefilterten Zeilen jeder
Minutendatei unverändert ab:

  ~/.local/share/marktdaten/roh/<Tag>/<Dateiname der Börse>.gz          (alle Werte der Watchlist)
  ~/.local/share/marktdaten/roh/<Tag>/<Dateiname der Börse>.<ISIN>.gz   (Nachladen für einen einzelnen Wert)
  ~/.local/share/marktdaten/roh/<Tag>/index.jsonl                       ({"datei", "isins", "zeilen"} je Datei)
"""
from __future__ import annotations

import gzip
import json
import os
import pathlib
import re
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..modell import Trade
from ..quellen import CBOE, TRADEGATE, XETRA

DATEN = pathlib.Path(os.environ.get("MARKTDATEN_DATEN", "~/.local/share/marktdaten")).expanduser()
ROH = pathlib.Path(os.environ.get("MARKTDATEN_ROH", DATEN / "roh")).expanduser()

# Dateiname -> (Quelle, Art)
MUSTER = [(re.compile(r"^DETR-(pretrade|posttrade)-"), "xetra"), (re.compile(r"^DGAT-(pretrade|posttrade)-"), "tradegate"),
          (re.compile(r"^eqt_(pretrade|trade)_[A-Z]{4}_"), "cboe")]
UMWANDLER = {"xetra": XETRA, "tradegate": TRADEGATE}


def art_der_datei(name: str) -> tuple[str, str] | None:
    for muster, quelle in MUSTER:
        m = muster.match(name)
        if m:
            return quelle, "posttrade" if m[1] in ("posttrade", "trade") else "pretrade"
    return None


def _basis(name: str) -> tuple[str, str | None]:
    """'X.json.gz' -> ('X.json', None); 'X.json.DE0007164600.gz' -> ('X.json', 'DE0007164600')"""
    stamm = name[:-3]
    m = re.match(r"^(.*)\.([A-Z]{2}[A-Z0-9]{9}\d)$", stamm)
    return (m[1], m[2]) if m else (stamm, None)


class Rohdaten:
    def __init__(self, ort: pathlib.Path = ROH):
        self.ort = ort

    def tage(self) -> list[str]:
        if not self.ort.exists():
            return []
        return sorted((p.name for p in self.ort.iterdir() if p.is_dir() and re.match(r"\d{4}-\d\d-\d\d$", p.name)), reverse=True)

    def index(self, tag: str) -> dict[str, list[str] | None]:
        """Datei -> enthaltene ISINs (None: unbekannt, Datei ohne Eintrag im Index)."""
        d = self.ort / tag
        aus: dict[str, list[str] | None] = {p.name: None for p in d.glob("*.gz")} if d.exists() else {}
        p = d / "index.jsonl"
        if p.exists():
            for z in p.read_text().splitlines():
                try:
                    x = json.loads(z)
                except ValueError:
                    continue
                if x.get("datei") in aus:
                    aus[x["datei"]] = x.get("isins")
        return aus

    def dateien(self, tag: str, isin: str) -> list[tuple[str, str, str]]:
        """Dateien, die den Wert enthalten können: (Datei, Quelle, Art). Je Börsendatei nur eine Ablage,
        die volle bevorzugt, sonst die Einzelablage für diesen Wert."""
        idx = self.index(tag)
        gewaehlt: dict[str, str] = {}
        for datei, isins in sorted(idx.items()):
            basis, nur = _basis(datei)
            if nur and nur != isin:
                continue
            if not nur and isins is not None and isin not in isins:
                continue
            if basis not in gewaehlt or not nur:
                gewaehlt[basis] = datei
        aus = []
        for basis, datei in sorted(gewaehlt.items()):
            art = art_der_datei(basis)
            if art:
                aus.append((datei, *art))
        return aus

    def werte(self, tag: str) -> set[str]:
        aus: set[str] = set()
        for datei, isins in self.index(tag).items():
            aus.update(isins or [])
            basis, nur = _basis(datei)
            if nur:
                aus.add(nur)
        return aus

    def abdeckung(self, tag: str, isin: str) -> dict[str, int]:
        """Minutendateien je Strom, z. B. {"xetra pretrade": 510, ...}."""
        aus: dict[str, int] = {}
        for _, quelle, art in self.dateien(tag, isin):
            k = f"{quelle} {art}"
            aus[k] = aus.get(k, 0) + 1
        return aus

    def stand(self, tag: str) -> float:
        """Kennzahl für den Cache: ändert sich, sobald Dateien dazukommen."""
        d = self.ort / tag
        return max((p.stat().st_mtime for p in d.glob("*.gz")), default=0.0) if d.exists() else 0.0

    def ereignisse(self, isin: str, tag: str) -> list:
        """Alle Ereignisse eines Werts, zeitlich sortiert; Abschlüsse vor Buchmeldungen derselben Zeit,
        damit ein Abschluss das Buch vor sich sieht. Element: (ts, ereignis, rohfelder oder None)."""
        aus, b = [], isin.encode()
        for datei, quelle, art in self.dateien(tag, isin):
            try:
                with gzip.open(self.ort / tag / datei, "rb") as f:
                    zeilen = [z for z in f if b in z]
            except (OSError, EOFError):
                continue                                   # Datei wird gerade geschrieben oder ist beschädigt
            if quelle == "cboe":
                for e in CBOE.umwandeln([z.decode() for z in zeilen], art):
                    if e.instrument == isin:
                        aus.append((e.ts, e, None))
                continue
            for z in zeilen:
                if not z.startswith(b"{"):
                    continue
                d = json.loads(z)
                if d.get("instrumentIdentificationCode") != isin:
                    continue
                for e in UMWANDLER[quelle].umwandeln(d):
                    aus.append((e.ts, e, d))
        aus.sort(key=lambda x: (x[0], 0 if isinstance(x[1], Trade) else 1))
        return aus


def watchlist() -> list[dict]:
    """Watchlist aus dem Speicher des Dashboards, schreibgeschützt geöffnet."""
    datei = DATEN / "marktdaten.db"
    if not datei.exists():
        return []
    try:
        db = sqlite3.connect(f"file:{datei}?mode=ro", uri=True, timeout=5)
        try:
            zeilen = db.execute("SELECT id, symbol, name, markt FROM watchlist ORDER BY name").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    return [dict(zip(("id", "symbol", "name", "markt"), z)) for z in zeilen]


def _db() -> sqlite3.Connection | None:
    datei = DATEN / "marktdaten.db"
    if not datei.exists():
        return None
    return sqlite3.connect(f"file:{datei}?mode=ro", uri=True, timeout=5)


def kerzen(id_: str, tf: str, anzahl: int = 400) -> list[list]:
    """Abgeschlossene Kerzen [t, o, h, l, c, v] aus dem Speicher des Dashboards, älteste zuerst.
    1m bis 1h werden aus den 1-Minuten-Kerzen gebündelt, 1d aus den Tageskerzen plus den Minuten der letzten Tage."""
    from .formationen import TF
    sek = TF[tf]
    db = _db()
    if db is None:
        return []
    try:
        letzte = db.execute("SELECT MAX(t) FROM kerzen_1m WHERE id = ?", (id_,)).fetchone()[0]
        markt = (db.execute("SELECT markt FROM watchlist WHERE id = ?", (id_,)).fetchone() or [None])[0]
        splits, vol_bereinigt = [], set()
        if markt == "DE":                                   # Splits aus der Tabelle des Kerns (EODHD)
            try:
                splits = db.execute("SELECT t, faktor FROM splits WHERE id = ? AND faktor > 0", (id_,)).fetchall()
            except sqlite3.OperationalError:                # ältere Datenbank ohne Tabelle splits
                splits = []
        if tf == "1d":
            tage = [list(z) for z in db.execute("SELECT t, o, h, l, c, v, q FROM kerzen_1d WHERE id = ? ORDER BY t", (id_,))]
            vol_bereinigt = {z[0] for z in tage if z[6] == "eodhd"}       # EODHD liefert das Volumen schon bereinigt
            tage = [z[:6] for z in tage]
            seit = (tage[-1][0] + 86400) if tage else 0
            minuten = db.execute("SELECT t, o, h, l, c, v FROM kerzen_1m WHERE id = ? AND t >= ? ORDER BY t", (id_, seit)).fetchall()
            roh = tage + _buendeln(minuten, 86400)
        else:
            seit = (letzte or 0) - anzahl * sek * 4 - 7 * 86400          # Nächte und Wochenenden überbrücken
            minuten = db.execute("SELECT t, o, h, l, c, v FROM kerzen_1m WHERE id = ? AND t >= ? ORDER BY t", (id_, seit)).fetchall()
            roh = _buendeln(minuten, sek)
    finally:
        db.close()
    if letzte is not None:                                 # laufende Kerze weglassen
        jetzt = _jetzt()
        roh = [x for x in roh if x[0] + sek <= letzte + 60 or (tf == "1d" and tag_abgeschlossen(x[0], markt, jetzt))]
    if markt == "DE":                                      # Splits bekannt (EODHD): genau nach Tabelle
        roh = splits_aus_tabelle(roh, splits, vol_bereinigt)
    elif not id_.endswith(("USDT", "USDC", "BTC", "EUR")):  # übrige Aktien: Splits am Kurssprung erkennen (Krypto hat keine)
        roh = splits_bereinigen(roh)
    return roh[-anzahl:]


BERLIN = ZoneInfo("Europe/Berlin")
SCHLUSS_DE = (17, 45)                                      # Xetra: Schlussauktion bis etwa 17:35, mit Puffer


def _jetzt() -> float:
    return time.time()


def tag_abgeschlossen(t: int, markt: str | None, jetzt: float) -> bool:
    """Ist die Tageskerze, die bei t (UTC-Mitternacht) beginnt, fertig? Aktien haben nach Börsenschluss keine neuen
    Minuten; ohne diese Regel bliebe der letzte Handelstag bis zum nächsten Handelsbeginn als „laufend“ unsichtbar.
    DE: ab 17:45 Uhr Berlin. US: ab Ende des UTC-Tages (die Minuten reichen bis 20:00 New York, also Nachbörse
    eingeschlossen). Krypto handelt rund um die Uhr: dort gilt weiter die nächste Minute als Beweis."""
    if markt == "DE":
        tag = datetime.fromtimestamp(t, timezone.utc).date()
        schluss = datetime(tag.year, tag.month, tag.day, *SCHLUSS_DE, tzinfo=BERLIN).timestamp()
        return jetzt >= schluss
    if markt == "US":
        return jetzt >= t + 86400
    return False


SPLIT_FAKTOREN = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50)


def splits_aus_tabelle(k: list[list], splits: list, vol_bereinigt: set | None = None) -> list[list]:
    """Splits aus der Tabelle splits (t = erster Tag mit neuem Kurs, faktor = neue Stücke je alte) herausrechnen:
    Kerzen vor dem Split durch das Produkt der späteren Faktoren teilen, Volumen damit multiplizieren, außer bei
    Kerzen, deren Volumen die Quelle schon bereinigt liefert (EODHD)."""
    if not splits or not k:
        return k
    vol_bereinigt = vol_bereinigt or set()
    splits = sorted(splits)
    aus = []
    for x in k:
        f = 1.0
        for t, faktor in splits:
            if x[0] < t:
                f *= faktor
        if f == 1.0:
            aus.append(list(x))
            continue
        v = x[5] if x[0] in vol_bereinigt else (x[5] or 0) * f
        aus.append([x[0], x[1] / f, x[2] / f, x[3] / f, x[4] / f, v])
    return aus


def splits_bereinigen(k: list[list]) -> list[list]:
    """Rechnet Aktiensplits heraus, die der Speicher unbereinigt enthält (z. B. Amazon 20:1, Nvidia 10:1, Tesla 3:1).
    Erkannt wird ein Sprung über Nacht, der fast genau einem üblichen Splitverhältnis entspricht (±7 %); Kurse davor
    werden durch das Verhältnis geteilt, Volumen damit multipliziert. Umgekehrte Splits (Zusammenlegungen) ebenso."""
    if len(k) < 2:
        return k
    faktor, aus = 1.0, [list(x) for x in k]
    for i in range(len(aus) - 1, 0, -1):                   # von hinten: jüngste Kurse bleiben wie sie sind
        vor, nach = aus[i - 1][4] * faktor, aus[i][1]
        if vor <= 0 or nach <= 0:
            continue
        r = vor / nach
        for f in SPLIT_FAKTOREN:
            for ziel in (f, 1 / f):
                if abs(r / ziel - 1) < 0.07:
                    faktor /= ziel
                    break
            else:
                continue
            break
        if faktor != 1.0:
            x = aus[i - 1]
            x[1], x[2], x[3], x[4], x[5] = x[1] * faktor, x[2] * faktor, x[3] * faktor, x[4] * faktor, (x[5] or 0) / faktor
    return aus


def letzter_kurs(id_: str) -> tuple[int, float] | None:
    db = _db()
    if db is None:
        return None
    try:
        z = db.execute("SELECT t, c FROM kerzen_1m WHERE id = ? ORDER BY t DESC LIMIT 1", (id_,)).fetchone()
    finally:
        db.close()
    return (z[0], z[1]) if z else None


def _buendeln(zeilen, sek: int) -> list[list]:
    aus = []
    for t, o, h, l, c, v in zeilen:
        fach = t // sek * sek
        if aus and aus[-1][0] == fach:
            k = aus[-1]
            k[2], k[3], k[4], k[5] = max(k[2], h), min(k[3], l), c, k[5] + (v or 0)
        else:
            aus.append([fach, o, h, l, c, v or 0])
    return aus


def buch_minuten(id_: str, n: int = 5) -> list[dict]:
    """Die letzten n Minuten des je Minute verdichteten Buchs (buch_1m des Dashboards), älteste zuerst."""
    db = _db()
    if db is None:
        return []
    try:
        zeilen = db.execute("SELECT t, bid, ask, spread_bp, tiefe_bid, tiefe_ask, ungleich, updates FROM buch_1m WHERE id = ? ORDER BY t DESC LIMIT ?",
                            (id_, int(n))).fetchall()
    finally:
        db.close()
    return [dict(zip(("t", "bid", "ask", "spread_bp", "tiefe_bid", "tiefe_ask", "ungleich", "updates"), z)) for z in zeilen][::-1]
