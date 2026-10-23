"""Historie für deutsche Watchlist-Werte: Tageskerzen und Splits von EODHD (Quelle quellen/eodhd.py).

Regeln:
  - Eigene Daten haben Vorrang. Tage, an denen iC Delta selbst Xetra-Minuten oder eine eigene Tageskerze hat, werden
    nie überschrieben; EODHD füllt nur die übrigen Tage (Herkunft q = "eodhd").
  - Zeitraum: Einstellung tage_eodhd (Standard 365; der Gratisplan liefert ohnehin nur das letzte Jahr).
  - Abrufe: höchstens eodhd_max_tag am Tag (Gratisplan 20). Je Wert ein Abruf für die Kerzen, einer für die Splits
    (Splits nur alle 30 Tage neu), einer für die Suche, falls das Watchlist-Kürzel bei EODHD nicht passt.
  - Wann: beim Start für Werte ohne Historie, für einen neuen Wert sofort, täglich nach 18:30 Uhr (Berlin) nur bei Lücken.
Ohne Schlüssel passiert nichts; die Seite Datenquellen zeigt dann „ohne Schlüssel“.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..quellen import eodhd as E
from .zustand import Zustand

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
TAG = 86400


def _tag(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).date().isoformat()


def _t(datum: str) -> int:
    return int(datetime.fromisoformat(datum).replace(tzinfo=UTC).timestamp())


def _faktor(text: str) -> float | None:
    """"4.000000/1.000000" -> 4.0 (neue Stücke je alte)."""
    try:
        neu, alt = (float(x) for x in str(text).split("/"))
        return neu / alt if alt else None
    except ValueError:
        return None


class HistorieDE(threading.Thread):
    def __init__(self, zustand: Zustand, quelle=E.EODHD):
        super().__init__(daemon=True, name="historie-de")
        self.z = zustand
        self.q = quelle
        self.auftraege: list[str] = []
        self.wecker = threading.Event()
        self._sperre = threading.Lock()

    # --- Bedarf ----------------------------------------------------------------------
    def _eigene_tage(self, id_: str, seit: int) -> set[int]:
        """Tage (UTC-Mitternacht) mit eigenen Daten: Xetra-Minuten oder Tageskerzen anderer Herkunft als eodhd."""
        sp = self.z.speicher
        m = {r[0] for r in sp._x("SELECT DISTINCT t / 86400 * 86400 FROM kerzen_1m WHERE id = ? AND t >= ?", (id_, seit))}
        d = {r[0] for r in sp._x("SELECT t FROM kerzen_1d WHERE id = ? AND t >= ? AND (q IS NULL OR q != 'eodhd')", (id_, seit))}
        return m | d

    def _letzter_handelstag(self) -> int:
        d = datetime.now(BERLIN)
        d = d.date() if d.hour * 60 + d.minute >= 18 * 60 + 30 else d.date() - timedelta(days=1)
        while d.weekday() > 4:
            d -= timedelta(days=1)
        return _t(d.isoformat())

    def bedarf(self, id_: str) -> str | None:
        """Ab welchem Tag geholt werden muss, oder None, wenn alles da ist."""
        e = self.z.speicher.einstellungen()
        seit = _t((datetime.now(UTC).date() - timedelta(days=int(e["tage_eodhd"]))).isoformat())
        st = E.stand()["werte"].get(id_, {})
        letzter = self._letzter_handelstag()
        if not st.get("bis"):                                     # noch nie geholt
            return _tag(seit)
        bis = _t(st["bis"])
        if _t(st.get("von", st["bis"])) > seit + 7 * TAG:
            return _tag(seit)                                     # Zeitraum vergrößert
        eigene = self._eigene_tage(id_, bis + TAG)
        luecken = [t for t in range(bis + TAG, letzter + 1, TAG) if datetime.fromtimestamp(t, UTC).weekday() < 5 and t not in eigene]
        return _tag(bis + TAG) if luecken else None               # nur bei Werktagen ohne eigene Daten (auch Feiertage, einmalig)

    # --- Holen und ablegen -------------------------------------------------------------
    @staticmethod
    def _kuerzel(w) -> str:
        return E.stand()["kuerzel"].get(w.id) or f"{w.symbol}.XETRA"

    def holen(self, w, von: str) -> dict:
        e = self.z.speicher.einstellungen()
        max_tag = int(e["eodhd_max_tag"])
        code = self._kuerzel(w)
        try:
            kerzen = self.q.tageskerzen(code, von, max_tag)
        except RuntimeError as fehler:
            if "404" not in str(fehler) or E.stand()["kuerzel"].get(w.id):
                raise
            code = self.q.suchen(w.id, max_tag)                   # Kürzel passt nicht: per ISIN suchen
            if not code:
                raise RuntimeError(f"{w.symbol}: bei EODHD an Xetra nicht gefunden") from None
            kerzen = self.q.tageskerzen(code, von, max_tag)
        d = E.stand(); d["kuerzel"][w.id] = code; E._sichern(d)
        eigene = self._eigene_tage(w.id, _t(von))
        zeilen = [[_t(k["date"]), k["open"], k["high"], k["low"], k["close"], k["volume"]] for k in kerzen
                  if all(k.get(x) is not None for x in ("open", "high", "low", "close")) and _t(k["date"]) not in eigene]
        self.z.speicher.kerzen("kerzen_1d", w.id, zeilen, "eodhd")
        alt = d["werte"].get(w.id, {})
        splits = None
        if not alt.get("splits_am") or time.time() - alt["splits_am"] > 30 * TAG:
            splits = [(w.id, _t(s["date"]), f, "eodhd") for s in self.q.splits(code, max_tag)
                      if s.get("date") and (f := _faktor(s.get("split")))]
            self.z.speicher._viele("INSERT OR REPLACE INTO splits VALUES (?, ?, ?, ?)", splits)
        d = E.stand()
        neu = {"kuerzel": code, "von": min(von, alt.get("von", von)), "bis": kerzen[-1]["date"] if kerzen else alt.get("bis", von),
               "tage": int(e["tage_eodhd"]), "geladen": len(zeilen), "eigene": len(eigene), "geholt": datetime.now(UTC).isoformat(timespec="seconds"),
               "splits_am": time.time() if splits is not None else alt.get("splits_am"), "splits": len(splits) if splits is not None else alt.get("splits", 0)}
        d["werte"][w.id] = neu
        E._sichern(d)
        return neu

    def pruefen(self, nur: set[str] | None = None) -> dict:
        """Alle deutschen Werte (oder nur die genannten) prüfen und bei Bedarf holen."""
        if not E.schluessel_vorhanden():
            self.z.melden("eodhd", fehler=None, hinweis="ohne Schlüssel")
            return {}
        with self.z.lock:
            werte = [w for w in self.z.werte.values() if w.markt == "DE" and (nur is None or w.id in nur)]
        erg = {}
        for w in werte:
            von = self.bedarf(w.id)
            if not von:
                continue
            try:
                erg[w.symbol] = self.holen(w, von)
                self.z.melden("eodhd", fehler=None, letzter=w.symbol)
            except E.Kontingent as k:
                self.z.melden("eodhd", fehler=str(k))
                break
            except Exception as f:                                # Anzeige statt Absturz
                self.z.melden("eodhd", fehler=f"{w.symbol}: {str(f)[:160]}")
        if erg:
            self.z.geaendert()
        return erg

    def nachladen(self, id_: str) -> None:
        """Neuer Wert auf der Watchlist: sofort holen (im Hintergrund)."""
        with self._sperre:
            self.auftraege.append(id_)
        self.wecker.set()

    def run(self) -> None:
        time.sleep(20)                                            # Start der übrigen Sammler abwarten
        self.pruefen()
        jetzt = datetime.now(BERLIN)
        zuletzt = jetzt.date() if jetzt.hour * 60 + jetzt.minute >= 18 * 60 + 30 else jetzt.date() - timedelta(days=1)
        while True:
            self.wecker.wait(600)
            self.wecker.clear()
            with self._sperre:
                auftraege, self.auftraege = set(self.auftraege), []
            if auftraege:
                self.pruefen(auftraege)
            jetzt = datetime.now(BERLIN)
            if jetzt.date() != zuletzt and jetzt.hour * 60 + jetzt.minute >= 18 * 60 + 30:
                zuletzt = jetzt.date()
                self.pruefen()
