"""Werkstatt: Analysen als Python-Code, von Claude oder von Hand geschrieben, laufen im Sandkasten und
lassen sich anpinnen. Angepinnte Analysen rechnet der Reiter Analysen wie A bis F für jeden Wert und Tag.

Sandkasten (macOS sandbox-exec): kein Netz, keine Schreibrechte, lesen nur Quellcode, Python, venv und die
Marktdaten. Der Rest des Home-Verzeichnisses (Konfiguration, Schlüssel, Dokumente) ist unsichtbar.
Dazu Zeitlimit, CPU- und Speichergrenze. Der Code wird mit dem Ergebnis gespeichert und angezeigt.
"""
from __future__ import annotations

import json
import pathlib
import resource
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from . import lesen

ORT = lesen.DATEN / "analysen" / "werkstatt"
ERGEBNISSE = ORT / "ergebnisse"
SRC = pathlib.Path(__file__).resolve().parents[2]
LAEUFER = pathlib.Path(__file__).with_name("werkstatt_lauf.py")
LAUFZEIT_S = 90
SPEICHER_BYTES = 3 * 1024 ** 3
ENTWUERFE_MAX = 25

REFERENZ = """# Werkstatt: eigene Analysen in Python

Der Code läuft auf dem Mac Mini in einem Sandkasten: kein Netz, keine Dateien, nur Standardbibliothek (kein numpy,
kein pandas), 90 Sekunden Zeit. Alles, was `print()` ausgibt, kommt als stdout zurück. Das Ergebnis legst du in
das Wörterbuch `ausgabe`; es wird als Karte im Reiter Analysen → Werkstatt gezeichnet.

## Vorhandene Namen
- `isin`, `tag` ("YYYY-MM-DD"), `S` = 1_000_000_000 (Zeitstempel sind Nanosekunden UTC).
- `ereignisse`: Liste aller Meldungen des Tages, zeitlich sortiert, Elemente `(ts_ns, ereignis, roh)`. Arten:
  - `Trade(venue, preis, menge, ts, extra)`: Abschluss. venue "XETA"/"XETB" = Xetra, "XGAT" = Tradegate, Cboe "BATE"/"CHIX"/"CEUX";
    `extra.get("mmtTradingMode")` bei Xetra: "2" fortlaufend, "O" Eröffnungs-, "K" Schluss-, "I" Intraday-Auktion, "4" Abendhandel.
  - `BuchSeite(venue, seite, stufen, buch, ts)`: komplette Buchseite von Xetra, `seite` KAUF ("B") oder VERKAUF ("A"),
    `stufen` = [(preis, menge, orders), ...] beste Stufe zuerst, `buch` "regulaer" oder "retail".
  - `BuchStufe(venue, seite, preis, menge, ts)`: einzelne Stufe (Cboe). `Quote(venue, bid, ask, bid_menge, ask_menge, ts)`: Tradegate.
- `lauf`: der fertig durchgelaufene Lauf (rechnen.Lauf) über alle Ereignisse, nur fortlaufender Handel 09:01 bis 17:30 Uhr:
  - `lauf.start`, `lauf.ende` (ns), `lauf.raster`: je Sekunde `(t_s, bid, ask, bq1, aq1, obi5, micro_bp, b_stufen, a_stufen)` des Xetra-Buchs (Stufen = [(preis, menge, orders)…]).
  - `lauf.mids_t` (ns), `lauf.mids_v`: jede Änderung des Xetra-Mittelkurses. `mitte_bei(ts_ns)` liefert die Mitte zu einer Zeit.
  - `lauf.trades`: Xetra-Abschlüsse `(ts_ns, preis, menge, seite, mitte, bid, ask)`, `seite` +1 käufer-, −1 verkäuferinitiiert (Lee-Ready).
  - `lauf.ofi`: dict 10-Sekunden-Fach (ab start) → Order Flow Imbalance in Stück.
  - `lauf.minuten`: dict Minutenindex (ab 09:00) → zeitgewichtete Summen {"w": Sekunden, "spread", "tiefe_b", "tiefe_a", "obi1", "obi5", "micro", "k10000", ...}; Mittel = Summe / w.
  - `lauf.volumen`: dict Platz ("XETR", "XGAT", "CBOE") → [Anzahl, Stück, Euro]. `lauf.auktion`: dict Modus → [Anzahl, Stück, Euro, erster ts, Preis].
  - `lauf.eisberge`, `lauf.mm` (Market-Maker-Simulation), `lauf.ordergroesse`, `lauf.tiefe1`, `lauf.stufen_mengen` (Stichproben).
- `analyse()`: die komplette Auswertung A bis F als dict wie im Reiter Analysen (Schlüssel "a", "b", "c", "d", "e", "f", "auktionen");
  z. B. `analyse()["c"]["waende"]` = Wände mit start/ende (s), seite, preis, max, eur, orders, stufe_start, dauer_s, ergebnis, verdacht.
  Kostet etwa eine halbe Sekunde, nur aufrufen, wenn du die fertigen Kennzahlen brauchst. Fehlt das Xetra-Buch
  des Tages (Sammler lief nicht im fortlaufenden Handel), sind "a", "b", "c", "e", "f" nur `{"leer": True}`: vorher prüfen.
  Dann bleiben Abschlüsse (`lauf.trades` ist dann auch leer, aber `ereignisse` enthält Trades), `lauf.volumen`, `lauf.auktion`, `kerzen()`.
- `kerzen(tf="1m", anzahl=400)`: abgeschlossene Kerzen `[t_s, o, h, l, c, v]` aus dem Speicher des Dashboards, tf 1m 5m 15m 1h 1d.
- `zeit(ts)` → "HH:MM:SS" Berlin, `sekunden(ts)` → Unix-Sekunden. Module: math, statistics, collections, defaultdict, bisect, itertools, datetime, json.

## Ausgabe
```python
ausgabe["zahlen"] = [{"titel": "Wände", "wert": 12, "neben": "über der Schwelle"}, ...]        # Kacheln, höchstens 24
ausgabe["tabelle"] = {"titel": "...", "spalten": ["Zeit", "Preis", "Stück"], "zeilen": [["09:31:05", 201.2, 5000], ...], "rechts": [1, 2]}
ausgabe["reihen"] = [{"titel": "OBI je Minute", "t": [unix_s, ...], "serien": [{"name": "OBI", "werte": [...]}], "einheit": "", "null": True}]
ausgabe["punkte"] = [{"titel": "OFI gegen Kursänderung", "x": "OFI", "y": "Δ Mitte bp", "punkte": [[x, y], ...], "regression": True}]
ausgabe["text"] = "Zwei bis vier Sätze: was zu sehen ist und was es nicht beweist."
```
Zeiten in Reihen und Tabellen in Unix-Sekunden (Reihen) oder als Text per `zeit()` (Tabellen). Reihen werden auf 800 Punkte
ausgedünnt, Tabellen auf 250 Zeilen gekürzt.

## Regeln
- Kurz und lesbar, Kommentare auf Deutsch. Zeitgewichtet rechnen, wo Zustände unterschiedlich lange gelten.
- Nur Xetra-Buch (XETR, regulaer) für Buchkennzahlen; Retail-Buch und andere Plätze getrennt ausweisen.
- Daten sind 15 Minuten verzögert und ein Tag ist keine Statistik: im `text` sagen, was das Ergebnis trägt und was nicht.
- Bei einem Fehler kommt der Traceback zurück: Code korrigieren und erneut laufen lassen.

## Beispiel
```python
# Hält eine Wand den Kurs? Für jede Wand: Abstand der Mitte 60 s nach ihrem Ende gegen den Wandpreis.
w = analyse()["c"]["waende"]
zeilen, gehalten = [], 0
for x in w:
    m = mitte_bei((x["ende"] + 60) * S)
    if m is None: continue
    hielt = (m <= x["preis"]) if x["seite"] == "A" else (m >= x["preis"])
    gehalten += hielt
    zeilen.append([zeit(x["start"] * S), "Kauf" if x["seite"] == "B" else "Verkauf", x["preis"], round(m, 2), x["ergebnis"], "ja" if hielt else "nein"])
ausgabe["zahlen"] = [{"titel": "Wände", "wert": len(zeilen)}, {"titel": "gehalten nach 60 s", "wert": f"{gehalten / len(zeilen) * 100:.0f} %" if zeilen else "–"}]
ausgabe["tabelle"] = {"spalten": ["Start", "Seite", "Wand", "Mitte +60 s", "Ergebnis", "gehalten"], "zeilen": zeilen[:60], "rechts": [2, 3]}
ausgabe["text"] = f"{len(zeilen)} Wände an diesem Tag; ob der Kurs 60 s nach dem Ende der Wand auf ihrer Seite blieb. Ein Tag, kein Beweis."
```
"""


def _profil() -> str:
    """Sandkasten-Profil: Home gesperrt, nur Quellcode, Python, venv und Marktdaten lesbar; kein Netz, kein Schreiben."""
    heim = pathlib.Path.home().resolve()
    erlaubt = {SRC, pathlib.Path(sys.prefix).resolve(), lesen.DATEN.resolve(), lesen.ROH.resolve(), pathlib.Path(sys.base_prefix).resolve().parent}
    eltern = {heim}
    for p in erlaubt:
        q = p.parent
        while q.is_relative_to(heim) and q != heim:
            eltern.add(q)
            q = q.parent
    literale = " ".join(f'(literal "{e}")' for e in sorted(eltern))
    unterpfade = " ".join(f'(subpath "{e}")' for e in sorted(erlaubt))
    return (f'(version 1)(allow default)(deny network*)(deny file-write*)(deny file-read* (subpath "{heim}"))'
            f'(allow file-read-metadata {literale})(allow file-read* {unterpfade})')


def _grenzen() -> None:
    for art, wert in ((resource.RLIMIT_CPU, LAUFZEIT_S + 30), (resource.RLIMIT_AS, SPEICHER_BYTES)):
        try:
            resource.setrlimit(art, (wert, wert))
        except (ValueError, OSError):
            pass


def ausfuehren(code: str, isin: str, tag: str, parameter: dict | None = None) -> dict:
    """Führt Code im Sandkasten aus. Rückgabe: {ok, ausgabe|fehler, stdout, dauer_s}."""
    auftrag = json.dumps({"code": code, "isin": isin, "tag": tag, "parameter": parameter or {}}).encode()
    umgebung = {"PATH": "/usr/bin:/bin", "HOME": str(pathlib.Path.home()), "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8",
                "LANG": "de_DE.UTF-8", "TZ": "Europe/Berlin", "MARKTDATEN_DATEN": str(lesen.DATEN), "MARKTDATEN_ROH": str(lesen.ROH)}
    befehl = ["/usr/bin/sandbox-exec", "-p", _profil(), sys.executable, "-I", "-B", str(LAEUFER)]
    t = time.time()
    try:
        p = subprocess.run(befehl, input=auftrag, capture_output=True, timeout=LAUFZEIT_S, env=umgebung, cwd="/", preexec_fn=_grenzen)
    except subprocess.TimeoutExpired:
        return {"ok": False, "fehler": f"Zeitlimit von {LAUFZEIT_S} s überschritten", "stdout": "", "dauer_s": round(time.time() - t, 1)}
    dauer = round(time.time() - t, 2)
    stdout, stderr = p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
    try:
        aus = json.loads(stdout[stdout.index("{"):]) if "{" in stdout else None
    except ValueError:
        aus = None
    if not isinstance(aus, dict):
        return {"ok": False, "fehler": (stderr[-2000:] or stdout[-2000:] or f"Abbruch mit Code {p.returncode}"), "stdout": "", "dauer_s": dauer}
    aus["dauer_s"] = dauer
    return aus


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Werkstatt:
    def __init__(self):
        ORT.mkdir(parents=True, exist_ok=True)
        ERGEBNISSE.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.roh = lesen.Rohdaten()

    # --- Ablage ---------------------------------------------------------------------------------------
    def liste(self) -> list[dict]:
        aus = []
        for p in ORT.glob("*.json"):
            try:
                aus.append(json.loads(p.read_text()))
            except ValueError:
                continue
        return sorted(aus, key=lambda a: a.get("geaendert", ""), reverse=True)

    def lesen(self, id_: str) -> dict | None:
        p = ORT / f"{id_}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _speichern(self, a: dict) -> None:
        a["geaendert"] = _jetzt()
        tmp = ORT / f"{a['id']}.tmp"
        tmp.write_text(json.dumps(a, ensure_ascii=False, indent=1))
        tmp.replace(ORT / f"{a['id']}.json")

    def _aufraeumen(self) -> None:
        entwuerfe = [a for a in self.liste() if not a.get("angepinnt")]
        for a in entwuerfe[ENTWUERFE_MAX:]:
            self.loeschen(a["id"])

    def _wert(self, wert: str) -> dict:
        werte = {w["id"]: w for w in lesen.watchlist()}
        if wert in werte:
            return werte[wert]
        w = next((w for w in werte.values() if w["symbol"] == wert), None)
        if not w:
            raise ValueError("Wert nicht auf der Watchlist")
        return w

    # --- Ausführen --------------------------------------------------------------------------------------
    def lauf(self, code: str, wert: str, tag: str, titel: str = "", id_: str | None = None, quelle: str = "claude",
             beschreibung: str = "", modell: str = "", parameter: dict | None = None) -> dict:
        w = self._wert(wert)
        if tag not in self.roh.tage():
            raise ValueError("für diesen Tag gibt es keine Rohdaten")
        if not str(code).strip():
            raise ValueError("kein Code")
        with self.lock:
            a = (self.lesen(id_) if id_ else None) or {"id": uuid.uuid4().hex[:8], "erstellt": _jetzt(), "angepinnt": False, "quelle": quelle}
            a.update(titel=str(titel or a.get("titel") or "Analyse")[:80], code=str(code)[:40000], beschreibung=str(beschreibung or a.get("beschreibung", ""))[:600],
                     modell=str(modell or a.get("modell", ""))[:60])
        ergebnis = ausfuehren(a["code"], w["id"], tag, parameter)
        with self.lock:
            a["zuletzt"] = {"isin": w["id"], "symbol": w["symbol"], "tag": tag, "ok": ergebnis["ok"], "dauer_s": ergebnis["dauer_s"], "zeit": _jetzt(),
                            "ergebnis": ergebnis}
            self._speichern(a)
            self._aufraeumen()
        if a["angepinnt"] and ergebnis["ok"]:
            self._ergebnis_speichern(a["id"], w["id"], tag, ergebnis)
        return {"id": a["id"], "titel": a["titel"], "angepinnt": a["angepinnt"], "wert": w, "tag": tag, **ergebnis}

    def anpinnen(self, id_: str, titel: str | None = None, beschreibung: str | None = None, angepinnt: bool = True) -> dict:
        with self.lock:
            a = self.lesen(id_)
            if not a:
                raise ValueError("unbekannte Analyse")
            a["angepinnt"] = bool(angepinnt)
            if titel:
                a["titel"] = str(titel)[:80]
            if beschreibung is not None:
                a["beschreibung"] = str(beschreibung)[:600]
            self._speichern(a)
            return a

    def loeschen(self, id_: str) -> bool:
        with self.lock:
            p = ORT / f"{id_}.json"
            if not p.exists():
                return False
            p.unlink()
            for e in ERGEBNISSE.glob(f"{id_}.*.json"):
                e.unlink()
            return True

    # --- Ergebnisse angepinnter Analysen je Wert und Tag ----------------------------------------------------
    def _ergebnis_speichern(self, id_: str, isin: str, tag: str, ergebnis: dict) -> None:
        (ERGEBNISSE / f"{id_}.{isin}.{tag}.json").write_text(json.dumps({"stand": self.roh.stand(tag), **ergebnis}, ensure_ascii=False))

    def ergebnis(self, id_: str, wert: str, tag: str, neu: bool = False) -> dict:
        a = self.lesen(id_)
        if not a:
            raise ValueError("unbekannte Analyse")
        w = self._wert(wert)
        p = ERGEBNISSE / f"{id_}.{w['id']}.{tag}.json"
        if p.exists() and not neu:
            alt = json.loads(p.read_text())
            if alt.get("stand") == self.roh.stand(tag) and alt.get("ok"):
                return {"id": id_, "titel": a["titel"], "wert": w, "tag": tag, **alt}
        ergebnis = ausfuehren(a["code"], w["id"], tag)
        if ergebnis["ok"]:
            self._ergebnis_speichern(id_, w["id"], tag, ergebnis)
        return {"id": id_, "titel": a["titel"], "wert": w, "tag": tag, **ergebnis}

    def uebersicht(self) -> dict:
        alle = self.liste()
        kurz = lambda a: {k: a.get(k) for k in ("id", "titel", "beschreibung", "erstellt", "geaendert", "quelle", "modell", "angepinnt")} | \
            {"zuletzt": {k: v for k, v in (a.get("zuletzt") or {}).items() if k != "ergebnis"}}
        return {"angepinnt": [kurz(a) for a in alle if a.get("angepinnt")], "entwuerfe": [kurz(a) for a in alle if not a.get("angepinnt")],
                "sandkasten": "sandbox-exec: kein Netz, keine Schreibrechte, Home gesperrt", "laufzeit_s": LAUFZEIT_S}
