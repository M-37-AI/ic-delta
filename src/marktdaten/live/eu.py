"""Europa, 15 Minuten verzögert: Xetra, Tradegate und Cboe Europe laufend einlesen.

Alle 20 Sekunden werden die neuesten Minutendateien geholt und nur die Zeilen der
Watchlist ausgewertet. Die Xetra-Datei hat rund 30 MB je Minute; sie wird als Strom
gelesen und vor dem JSON-Parsen per ISIN gefiltert.

Der Tagesverlauf (Abschlüsse seit Handelsbeginn) und der Vortagesschluss kommen über
einen eigenen Auftrag `nachladen()`, der beim Start läuft und sich im Dashboard per Knopf
erneut starten lässt. Er holt nur Minuten, die noch nicht im Speicher sind, neueste zuerst,
und passt sein Tempo an: Die Deutsche Börse antwortet bei zu vielen Anfragen mit 429.
Kommt ein deutscher Wert neu auf die Watchlist, lädt `nachladen(isins)` den Tag nur für ihn nach.
Steht kein deutscher Wert auf der Watchlist, lädt der Sammler nichts.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import einrichtung
from ..netz import USER_AGENT, Zaehler
from ..quellen import CBOE, TRADEGATE, XETRA
from ..quellen.cboe_europe import BASIS as CBOE_BASIS
from ..quellen.deutsche_boerse import BASIS as DB_BASIS
from .speicher import ROH
from .zustand import Zustand

SCHLUESSEL = b'"instrumentIdentificationCode":"'
BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


class Gebremst(Exception):
    """Der Server hat mit 429 geantwortet."""


def _zeit_aus_name(name: str) -> datetime | None:
    """'DETR-pretrade-2026-10-20T16_05.json.gz' -> 2026-10-20 16:05 UTC; Tagesdateien ('daily-...') -> None"""
    m = re.search(r"(\d{4}-\d\d-\d\dT\d\d_\d\d)", name)
    return datetime.strptime(m[1], "%Y-%m-%dT%H_%M").replace(tzinfo=UTC) if m else None


def _minutendateien(liste: list[str]) -> list[str]:
    return [d for d in liste if _zeit_aus_name(d)]


def _zeilen(url: str, zaehlen=None):
    """gz-Datei zeilenweise als Bytes, ohne sie ganz in den Speicher zu laden. zaehlen(bytes) erfasst den Download."""
    try:
        antwort = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=120)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Gebremst(e.headers.get("Retry-After")) from e
        raise
    with antwort:
        zaehler = Zaehler(antwort)
        quelle = gzip.GzipFile(fileobj=zaehler) if url.endswith(".gz") else zaehler
        try:
            yield from quelle
        finally:
            if zaehlen:
                zaehlen(zaehler.n)


_INDEX_LOCK = threading.Lock()


def ablegen(name: str, zeilen: list[bytes], isins: set[bytes], tag: str) -> None:
    """Gefilterte Rohzeilen unverändert als gzip ablegen: roh/<UTC-Tag>/<Originalname>.gz, dazu ein Eintrag in index.jsonl.
    Erst unter .tmp schreiben, dann umbenennen, damit Leser nie eine halbe Datei sehen."""
    ordner = ROH / tag
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / (name if name.endswith(".gz") else name + ".gz")
    tmp = ziel.with_name(ziel.name + ".tmp")
    tmp.write_bytes(gzip.compress(b"".join(z if z.endswith(b"\n") else z + b"\n" for z in zeilen)))
    tmp.replace(ziel)
    with _INDEX_LOCK, open(ordner / "index.jsonl", "a") as f:
        f.write(json.dumps({"datei": ziel.name, "isins": sorted(i.decode() for i in isins), "zeilen": len(zeilen)}) + "\n")


class Tempo:
    """Abstand zwischen Abrufen: wird bei 429 verdoppelt und bei glattem Lauf langsam verkürzt."""

    def __init__(self, start=0.3, minimum=0.15, maximum=8.0):
        self.pause, self.minimum, self.maximum, self.glatt = start, minimum, maximum, 0

    def gut(self):
        self.glatt += 1
        if self.glatt >= 15:
            self.pause, self.glatt = max(self.minimum, self.pause * 0.8), 0

    def gebremst(self, retry_after: str | None) -> float:
        self.pause, self.glatt = min(self.maximum, self.pause * 2), 0
        return float(retry_after) if retry_after else 10.0


class EuSammler(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="eu-sammler")
        self.z = zustand
        self.erledigt: set[str] = zustand.speicher.geladen()   # schon verarbeitete Abschlussdateien
        self._sperre = threading.Lock()
        self._auftrag: threading.Thread | None = None
        self.tempo = Tempo()

    # --- Dateien beanspruchen, damit Lauf und Nachladen nichts doppelt zählen -----
    def _beanspruchen(self, datei: str) -> bool:
        with self._sperre:
            if datei in self.erledigt:
                return False
            self.erledigt.add(datei)
            return True

    def _freigeben(self, datei: str) -> None:
        with self._sperre:
            self.erledigt.discard(datei)

    def _isins(self) -> set[bytes]:
        with self.z.lock:
            return {w.id.encode() for w in self.z.werte.values() if w.markt == "DE"}

    def _zaehlen(self, quelle: str):
        return lambda n: self.z.speicher.download(quelle, n)

    # --- Lesen ------------------------------------------------------------------
    def _db(self, quelle, datei: str, isins: set[bytes] | None = None, einzeln: bool = False) -> list:
        """Datei lesen, auf die ISINs filtern, Ereignisse liefern und die gefilterten Rohzeilen ablegen.
        einzeln = Nachladen für einzelne neue Werte: je ISIN eine eigene Datei <Name>.<ISIN>.gz."""
        isins = isins if isins is not None else self._isins()
        ereignisse, roh = [], {}
        for zeile in _zeilen(DB_BASIS + "download/" + datei, self._zaehlen("Deutsche Börse")):
            i = zeile.find(SCHLUESSEL)
            if i < 0 or zeile[i + 32:i + 44] not in isins:
                continue
            roh.setdefault(zeile[i + 32:i + 44], []).append(zeile)
            ereignisse.extend(quelle.umwandeln(json.loads(zeile)))
        tag = _zeit_aus_name(datei).date().isoformat()
        try:
            if einzeln:
                for isin in isins:
                    ablegen(f"{datei.removesuffix('.gz')}.{isin.decode()}.gz", roh.get(isin, []), {isin}, tag)
            else:
                ablegen(datei, [z for liste in roh.values() for z in liste], isins, tag)
        except OSError as e:
            self.z.melden("rohablage", fehler=f"{type(e).__name__}: {str(e)[:100]}")
        return ereignisse

    def _cboe(self, datei: str, art: str) -> list:
        isins = self._isins()
        roh = [z for z in _zeilen(CBOE_BASIS + datei, self._zaehlen("Cboe Europe"))
               if z.split(b",", 2)[1:2] and z.split(b",", 2)[1] in isins]
        name = datei.rsplit("/", 1)[-1]
        try:
            stamp = datetime.strptime(name[-17:-4], "%Y%m%d_%H%M")
            ablegen(name + ".gz", roh, isins, stamp.date().isoformat())
        except (OSError, ValueError) as e:
            self.z.melden("rohablage", fehler=f"{type(e).__name__}: {str(e)[:100]}")
        return list(CBOE.umwandeln([z.decode() for z in roh], art))

    def _einspielen(self, dienst: str | None, datei: str, ereignisse: list) -> None:
        ereignisse.sort(key=lambda e: e.ts)
        with self.z.lock:
            for e in ereignisse:
                self.z.anwenden(e)
            if dienst:
                self.z.melden(dienst, datei=datei, verarbeitet=datetime.now(UTC).isoformat(timespec="seconds"),
                              ereignisse=len(ereignisse), fehler=None)
            self.z.geaendert()

    # --- Nachladen: Vortagesschluss und Tagesverlauf -----------------------------
    def nachladen(self, isins: set[str] | None = None) -> bool:
        """Startet den Auftrag im Hintergrund. Mit isins nur für diese (neu hinzugefügten) Werte.
        False, wenn der allgemeine Auftrag schon läuft."""
        if isins:
            threading.Thread(target=self._nachladen, args=({i.encode() for i in isins},), daemon=True, name="nachladen-neu").start()
            return True
        if self._auftrag and self._auftrag.is_alive():
            return False
        self._auftrag = threading.Thread(target=self._nachladen, daemon=True, name="nachladen")
        self._auftrag.start()
        return True

    def _fortschritt(self, **werte) -> None:
        with self.z.lock:
            self.z.melden("nachladen", **werte)
            self.z.geaendert()

    def _nachladen(self, nur: set[bytes] | None = None) -> None:
        """nur = None: alle Werte, nur fehlende Minuten. nur = ISINs: alle Minuten des Tages, nur für diese Werte."""
        start = time.time()
        if not self._isins():
            self._fortschritt(laeuft=False, fehler=None, hinweis="kein deutscher Wert auf der Watchlist", gesamt=0, erledigt=0)
            return
        try:
            liste = _minutendateien(XETRA.dateien("posttrade"))
            heute = datetime.now(UTC).date()
            frueher = sorted({_zeit_aus_name(d).date() for d in liste if _zeit_aus_name(d).date() < heute})
            beginn = datetime.combine(heute, datetime.min.time(), BERLIN).replace(hour=9).astimezone(UTC)
            ende = min(beginn + timedelta(hours=8, minutes=45), datetime.now(UTC))
            # Reihenfolge: letzte Stunde (Seite schnell brauchbar), Vortagesschluss, Rest des Tages, Tradegate
            heutige = [("tag", XETRA, d) for d in sorted((d for d in liste if beginn <= _zeit_aus_name(d) <= ende), reverse=True)
                       if nur or d not in self.erledigt]
            vortag_dateien = []
            ohne_vortag = [w for w in self.z.werte.values() if w.markt == "DE" and not w.vortag
                           and (nur is None or w.id.encode() in nur)]
            if frueher and ohne_vortag:
                tag = frueher[-1]
                von = datetime.combine(tag, datetime.min.time(), BERLIN).replace(hour=17, minute=25).astimezone(UTC)
                vortag_dateien = [("vortag", XETRA, d) for d in liste if von <= _zeit_aus_name(d) <= von + timedelta(minutes=25)]
            auftrag = heutige[:60] + vortag_dateien + heutige[60:]
            if not nur:
                tg = sorted((d for d in _minutendateien(TRADEGATE.dateien("posttrade"))
                             if _zeit_aus_name(d) >= ende - timedelta(minutes=30) and d not in self.erledigt), reverse=True)
                auftrag += [("tag", TRADEGATE, d) for d in tg]

            gesamt, erledigt, gebremst, vortag = len(auftrag), 0, 0, {}
            self._fortschritt(laeuft=True, gesamt=gesamt, erledigt=0, gebremst=0, fehler=None, hinweis=None,
                              gestartet=datetime.now(UTC).isoformat(timespec="seconds"), fertig=None, rest_s=None)
            for schritt, quelle, datei in auftrag:
                if schritt == "tag" and not nur and not self._beanspruchen(datei):
                    erledigt += 1
                    continue
                while True:
                    try:
                        ereignisse = self._db(quelle, datei, nur, einzeln=bool(nur))
                        self.tempo.gut()
                        break
                    except Gebremst as e:
                        gebremst += 1
                        warten = self.tempo.gebremst(e.args[0] if e.args else None)
                        self._fortschritt(gebremst=gebremst, hinweis=f"gebremst, warte {warten:.0f} s")
                        time.sleep(warten)
                    except Exception:
                        if schritt == "tag" and not nur:
                            self._freigeben(datei)
                        raise
                if schritt == "vortag":
                    for e in ereignisse:
                        if e.venue.startswith("XET") and e.menge:     # Xetra-Segmente XETA, XETB, XETS
                            if e.instrument not in vortag or e.ts >= vortag[e.instrument][0]:
                                vortag[e.instrument] = (e.ts, e.preis)
                else:
                    self._einspielen(None, datei, ereignisse)
                    if not nur:
                        self.z.speicher.geladen_markieren([datei])
                erledigt += 1
                laufzeit = time.time() - start
                self._fortschritt(erledigt=erledigt, hinweis=None,
                                  rest_s=round(laufzeit / erledigt * (gesamt - erledigt)) if erledigt else None)
                time.sleep(self.tempo.pause)
            if vortag:
                with self.z.lock:
                    for isin, (_, preis) in vortag.items():
                        if isin in self.z.werte:
                            self.z.werte[isin].vortag = preis
            self._fortschritt(laeuft=False, fertig=datetime.now(UTC).isoformat(timespec="seconds"), rest_s=0,
                              dauer_s=round(time.time() - start))
        except Exception as e:
            self._fortschritt(laeuft=False, fehler=f"{type(e).__name__}: {str(e)[:120]}")

    # --- Laufend -----------------------------------------------------------------
    def runde(self) -> None:
        if not self._isins():
            return                                            # kein deutscher Wert auf der Watchlist: nichts laden
        for dienst, quelle, art in (("xetra-pretrade", XETRA, "pretrade"), ("xetra-posttrade", XETRA, "posttrade"),
                                    ("tradegate-pretrade", TRADEGATE, "pretrade"), ("tradegate-posttrade", TRADEGATE, "posttrade")):
            if art == "pretrade" and not einrichtung.orderbuch():
                continue                                      # Orderbuch abgeschaltet (Einrichtung): spart 10–15 GB am Tag
            try:
                neu = [d for d in _minutendateien(quelle.dateien(art))[:3] if d not in self.erledigt]
                for d in sorted(neu):                # auch beim Buch jede Minute: Grundlage der Buch-Historie
                    if not self._beanspruchen(d):
                        continue
                    try:
                        self._einspielen(dienst, d, self._db(quelle, d))
                        if art == "posttrade":
                            self.z.speicher.geladen_markieren([d])
                    except Exception:
                        self._freigeben(d)
                        raise
            except Gebremst:
                self.z.melden(dienst, fehler="gebremst (429), nächster Versuch in 20 s")
            except Exception as e:
                self.z.melden(dienst, fehler=f"{type(e).__name__}: {str(e)[:100]}")
        grenze = datetime.now(UTC) - timedelta(minutes=30)
        for art in ("pretrade", "posttrade") if einrichtung.orderbuch() else ("posttrade",):
            for d in sorted(CBOE.dateien(art)[:15]):         # die letzten fünf Minuten, drei Segmente
                stamp = datetime.strptime(d[-17:-4], "%Y%m%d_%H%M").replace(tzinfo=UTC)
                if stamp < grenze or not self._beanspruchen(d):
                    continue
                try:
                    self._einspielen(f"cboe-{art}", d.rsplit("/", 1)[-1], self._cboe(d, art))
                except urllib.error.HTTPError:
                    self._freigeben(d)                        # Minute noch nicht veröffentlicht
                except Exception as e:
                    self._freigeben(d)
                    self.z.melden(f"cboe-{art}", fehler=f"{type(e).__name__}: {str(e)[:100]}")

    def run(self) -> None:
        self.nachladen()
        while True:
            self.runde()
            with self._sperre:
                if len(self.erledigt) > 20000:
                    self.erledigt = set(sorted(self.erledigt)[-5000:])
            time.sleep(30)                                  # schont das Abruflimit der Deutschen Börse
