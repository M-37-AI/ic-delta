"""Dauerhafter Speicher in SQLite: Watchlist, Kerzen, Abschlüsse, verdichtetes Orderbuch, Aufnahmen.

Eine Datei außerhalb des Repos (Standard ~/.local/share/marktdaten/marktdaten.db, anders per
MARKTDATEN_DATEN). Gespeichert werden nur Werte der Watchlist. Aufbewahrung und Obergrenze
stehen in den Einstellungen; aufraeumen() setzt sie durch.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

ORT = pathlib.Path(os.environ.get("MARKTDATEN_DATEN", "~/.local/share/marktdaten")).expanduser()
ROH = ORT / "roh"          # gefilterte Rohzeilen der Börsendateien, je UTC-Tag ein Ordner (für den Reiter Analysen)
VOREINSTELLUNGEN = {"obergrenze_gb": 5.0, "tage_1m": 365, "tage_abschluesse": 30, "tage_buch": 365, "tage_roh": 10,
                    "tage_eodhd": 365, "eodhd_max_tag": 20, "tage_tiefe": 30,   # tage_tiefe: Krypto-Buchtiefe (buch_tiefe)       # EODHD: Historie in Tagen, Abrufe am Tag (Gratisplan 20)
                    "tage_1s": 14, "tage_perp": 14}                             # Krypto-Orderflow je Sekunde (buch_1s), Futures-Kontext (liquidationen, perp_1m)
UTC = timezone.utc

# Bekannte Fallstricke der Daten. Liegen auch als Tabelle "datenhinweise" in der Datenbank,
# damit spätere (KI-)Analysen sie mitlesen. Ausführlich: DATENQUALITAET.md im Repo.
DATENHINWEISE = {
    "kerzen.q": "Herkunft jeder Kerze: sip = alle US-Börsen (vollständiges Volumen); iex = nur IEX, rund 4 % des US-Volumens, "
                "vorläufig, wird nach 15 Minuten durch sip ersetzt; xetra = Xetra-Hauptsitzung, 15 Minuten verzögert; binance = Binance Spot; binance_f = Binance Futures (Perpetual-Kontrakt, kein Spotpreis); "
                "eodhd = Xetra-Tageskerzen von EODHD (nur kerzen_1d, Historie vor bzw. zwischen eigenen Daten).",
    "us.volumen": "US-Volumen ist nur bei q = sip vergleichbar. Kerzen mit q = iex haben systematisch zu wenig Volumen. "
                  "Der Wechsel iex -> sip (letzte 15 Minuten, Nachladen, Tageswechsel) erzeugt Volumensprünge, die KEIN Marktsignal sind. "
                  "Für Volumenanalysen nur q = sip verwenden.",
    "us.kurs": "US-Kurse der letzten 15 Minuten stammen nur von IEX; der Preis entspricht praktisch dem Gesamtmarkt, einzelne Ausreißer sind möglich.",
    "de.verzoegerung": "Deutsche Kurse sind 15 Minuten verzögert (MiFIR). Zeitstempel sind die echten Handelszeiten, nicht die Abrufzeit.",
    "de.abendhandel": "Xetra-Abschlüsse außerhalb der Hauptsitzung (MiFID mmtTradingMode 4, nach 17:30 Uhr) gehen nicht in Kerzen und Kurs ein, "
                      "stehen aber in der Tabelle abschluesse.",
    "de.luecken": "Minuten können fehlen, wenn die Deutsche Börse den Abruf bremst (HTTP 429) oder der Dienst lief nicht. "
                  "Eine fehlende Minute bedeutet nicht kein Handel.",
    "buch.retail": "Xetra führt ein getrenntes Retail-Buch; es kann sich mit dem regulären Buch kreuzen. buch_1m enthält nur das reguläre Buch.",
    "buch_1m": "Orderbuch je Minute: bid/ask = letzter bester Kurs der Minute, spread_bp = Mittel, tiefe_* = Wert der besten 5 Stufen in Währung, "
               "ungleich = Mittel von (Kauf - Verkauf) / Summe über die besten 5 Stufen, updates = Anzahl Buchänderungen. "
               "Bei Krypto ein Schnappschuss pro Sekunde (Binance depth20, Kursstand einmal je Sekunde).",
    "buch_1s": "Tabelle buch_1s (nur Krypto, seit 21.10.2026): Orderflow je Sekunde aus Binance depth20@100ms und aggTrade. bid/ask und b1..b5/a1..a5 = "
               "Buch am Ende der Sekunde (Mengen in Basiswährung, b1 = beste Kaufstufe), b_tief/a_tief = Summe der Stufen 6 bis 20, "
               "ofi = Order Flow Imbalance nach Cont/Kukanov/Stoikov an Stufe 1 über alle Updates der Sekunde, ofi5 = dieselbe Regel je Stufe 1 bis 5 summiert, "
               "updates = Anzahl Buch-Updates, kauf/verkauf = aggressives Kauf-/Verkaufsvolumen (aggTrade, m = true heißt aggressiver Verkauf), "
               "n = Anzahl aggTrades, letzter = letzter Handelspreis. Zeitbasis Empfangszeit (UTC-Sekunde). Sekunden ohne Buch-Update wiederholen "
               "den letzten Zustand mit ofi 0; Lücken ohne Zeile = Strom war länger als 30 s weg.",
    "liquidationen": "Tabelle liquidationen (nur Krypto, Binance Futures Perpetual desselben Paars): jede Zwangsliquidation, t in Millisekunden, "
                     "seite = was liquidiert wurde (long = Verkauf durch die Börse, short = Kauf), preis und menge in Basiswährung. "
                     "Binance sendet höchstens eine Liquidation je Sekunde und Paar; Kaskaden sind daher untererfasst.",
    "perp_1m": "Tabelle perp_1m (nur Krypto, Binance Futures Perpetual): je Minute Mittel von Markpreis (mark) und Indexpreis (index_), letzte Funding-Rate "
               "(funding, je 8 Stunden, positiv = Longs zahlen) und letzter Open Interest (oi, in Basiswährung, einmal je Minute per REST). Kein Spotpreis.",
    "zeit": "Alle Zeitstempel UTC: Kerzen in Sekunden (Beginn der Minute), Abschlüsse und Aufnahmen in Nanosekunden.",
    "roh": "Unter roh/<UTC-Tag>/ liegen die auf die Watchlist gefilterten Originalzeilen der Börsendateien (gzip), dazu index.jsonl "
           "mit den ISINs, die eine Datei enthalten kann. Gilt eine Datei mehrfach im Index, zählt die letzte Zeile. "
           "Dateien mit .<ISIN>.gz stammen aus dem Nachladen eines einzelnen neuen Werts.",
    "verdichtung": "1-Minuten-Kerzen älter als die Aufbewahrung werden zu kerzen_15m und kerzen_1d verdichtet; feinere Analysen sind dann nicht mehr möglich.",
    "de.tage_eodhd": "Deutsche Tageskerzen mit q = eodhd stammen von EODHD (Xetra, Tagesende): Eröffnung, Hoch, Tief, Schluss roh (nicht um Splits bereinigt), das Volumen dagegen von EODHD splitbereinigt. Eigene Tage (q = xetra bzw. aus Minuten) haben Vorrang. Am Übergang eodhd -> eigene Daten kann das Volumen leicht springen; das ist kein Marktsignal.",
    "buch_tiefe": "Tabelle buch_tiefe (nur Krypto): Orderbuch bis 1.000 Stufen je Seite (Binance, etwa ±0,3 % um den Kurs), alle 10 s abgetastet, "
                  "zu Preisbändern zusammengefasst (band = Breite, preis = Bandbeginn, 1 Basispunkt des Kurses gerundet) und je Minute gemittelt "
                  "(menge in Basiswährung). seite B = Kauf, A = Verkauf. Das äußerste, angeschnittene Band fehlt. Sichtbar ist nur, was im Buch "
                  "stand; versteckte Orders und Orders anderer Börsen fehlen.",
    "splits": "Tabelle splits: Aktiensplits je Wert (t = Tag, faktor = neue Stücke je alte, z. B. 4.0 für 4:1). Kerzen sind roh; für lange Vergleiche Preise vor dem Split durch den Faktor teilen und Volumen damit multiplizieren (EODHD-Volumen ist schon bereinigt).",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (id TEXT PRIMARY KEY, symbol TEXT, name TEXT, markt TEXT, hinzugefuegt TEXT);
CREATE TABLE IF NOT EXISTS kerzen_1m (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (id, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kerzen_15m (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (id, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kerzen_1d (id TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (id, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS abschluesse (id TEXT, t INTEGER, venue TEXT, preis REAL, menge REAL,
    PRIMARY KEY (id, t, venue, preis, menge)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS buch_1m (id TEXT, t INTEGER, bid REAL, ask REAL, spread_bp REAL, tiefe_bid REAL, tiefe_ask REAL,
    ungleich REAL, updates INTEGER, PRIMARY KEY (id, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS aufnahmen (nr INTEGER PRIMARY KEY, id TEXT, start TEXT, bis TEXT, ende TEXT, zeilen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS buch_roh (nr INTEGER, t INTEGER, seite TEXT, buch TEXT, stufen TEXT);
CREATE INDEX IF NOT EXISTS buch_roh_nr ON buch_roh (nr, t);
CREATE TABLE IF NOT EXISTS geladen (datei TEXT PRIMARY KEY, t INTEGER) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS downloads (tag TEXT, quelle TEXT, bytes INTEGER, PRIMARY KEY (tag, quelle)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS einstellungen (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE IF NOT EXISTS datenhinweise (thema TEXT PRIMARY KEY, hinweis TEXT);
CREATE TABLE IF NOT EXISTS splits (id TEXT, t INTEGER, faktor REAL, quelle TEXT, PRIMARY KEY (id, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS buch_tiefe (id TEXT, t INTEGER, seite TEXT, preis REAL, menge REAL, band REAL,
    PRIMARY KEY (id, t, seite, preis)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS buch_1s (id TEXT, t INTEGER, bid REAL, ask REAL, b1 REAL, b2 REAL, b3 REAL, b4 REAL, b5 REAL,
    a1 REAL, a2 REAL, a3 REAL, a4 REAL, a5 REAL, b_tief REAL, a_tief REAL, ofi REAL, ofi5 REAL, updates INTEGER,
    kauf REAL, verkauf REAL, n INTEGER, letzter REAL, PRIMARY KEY (id, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS liquidationen (id TEXT, t INTEGER, seite TEXT, preis REAL, menge REAL,
    PRIMARY KEY (id, t, seite, preis)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS perp_1m (id TEXT, t INTEGER, mark REAL, index_ REAL, funding REAL, oi REAL,
    PRIMARY KEY (id, t)) WITHOUT ROWID;
"""
KERZEN = ("kerzen_1m", "kerzen_15m", "kerzen_1d")
DATENARTEN = {"kerzen_1m": "Kerzen 1 Min.", "kerzen_15m": "Kerzen 15 Min. (verdichtet)", "kerzen_1d": "Tageskerzen",
              "abschluesse": "Abschlüsse", "buch_1m": "Orderbuch je Minute", "buch_roh": "Orderbuch-Aufnahmen",
              "buch_tiefe": "Orderbuch-Tiefe Krypto je Minute", "buch_1s": "Orderflow Krypto je Sekunde",
              "liquidationen": "Liquidationen Krypto (Futures)", "perp_1m": "Futures-Kontext Krypto je Minute"}
# grobe Bytes je Zeile in SQLite, falls dbstat fehlt (nur für die Aufteilung; die Dateigröße ist exakt)
ZEILE_BYTES = {"kerzen_1m": 60, "kerzen_15m": 60, "kerzen_1d": 60, "abschluesse": 55, "buch_1m": 75, "buch_roh": 140, "buch_tiefe": 50,
               "buch_1s": 130, "liquidationen": 50, "perp_1m": 60}
JE_WERT = ("kerzen_1m", "kerzen_15m", "kerzen_1d", "abschluesse", "buch_1m", "buch_tiefe", "buch_1s", "liquidationen", "perp_1m")


class Speicher:
    def __init__(self, ort: pathlib.Path = ORT):
        ort.mkdir(parents=True, exist_ok=True)
        self.datei = ort / "marktdaten.db"
        neu = not self.datei.exists()
        self.db = sqlite3.connect(self.datei, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        with self.lock:
            if neu:
                self.db.execute("PRAGMA auto_vacuum = INCREMENTAL")    # Platz nach dem Löschen zurückgeben
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = NORMAL")
            self.db.executescript(SCHEMA)
            for tabelle in KERZEN:                                   # Herkunft je Kerze (q), auch in älteren Datenbanken
                if "q" not in {s[1] for s in self.db.execute(f"PRAGMA table_info({tabelle})")}:
                    self.db.execute(f"ALTER TABLE {tabelle} ADD COLUMN q TEXT")
            self.db.executemany("INSERT OR REPLACE INTO datenhinweise VALUES (?, ?)", DATENHINWEISE.items())
            # Kerzen aus der Zeit vor der Herkunftsspalte nachtragen. US-Kerzen vor dem laufenden Tag kamen aus der
            # SIP-Historie; US-Minuten des laufenden Tags ohne Angabe gelten vorsichtig als iex (Volumen unsicher).
            heute = int(datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            for tabelle in KERZEN:
                for markt, q in (("DE", "xetra"), ("KRYPTO", "binance"), ("ROHSTOFFE", "binance_f")):
                    self.db.execute(f"UPDATE {tabelle} SET q = ? WHERE q IS NULL AND id IN (SELECT id FROM watchlist WHERE markt = ?)", (q, markt))
                us = "id IN (SELECT id FROM watchlist WHERE markt = 'US')"
                self.db.execute(f"UPDATE {tabelle} SET q = 'sip' WHERE q IS NULL AND t < ? AND {us}", (heute,))
                self.db.execute(f"UPDATE {tabelle} SET q = 'iex' WHERE q IS NULL AND {us}")
        self._downloads: dict[str, int] = {}
        self._dl_lock = threading.Lock()

    def _x(self, sql: str, werte=()) -> list:
        with self.lock:
            return self.db.execute(sql, werte).fetchall()

    def _viele(self, sql: str, zeilen: list) -> None:
        if not zeilen:
            return
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany(sql, zeilen)
            self.db.execute("COMMIT")

    # --- Watchlist -----------------------------------------------------------------
    def watchlist(self) -> list[dict]:
        return [dict(zip(("id", "symbol", "name", "markt", "hinzugefuegt"), z))
                for z in self._x("SELECT id, symbol, name, markt, hinzugefuegt FROM watchlist ORDER BY markt, name")]

    def hinzufuegen(self, id_: str, symbol: str, name: str, markt: str) -> None:
        self._x("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?, ?)",
                (id_, symbol, name, markt, datetime.now(UTC).isoformat(timespec="seconds")))

    def entfernen(self, id_: str, daten_loeschen: bool) -> None:
        self._x("DELETE FROM watchlist WHERE id = ?", (id_,))
        if daten_loeschen:
            for tabelle in JE_WERT:
                self._x(f"DELETE FROM {tabelle} WHERE id = ?", (id_,))
            for (nr,) in self._x("SELECT nr FROM aufnahmen WHERE id = ?", (id_,)):
                self.aufnahme_loeschen(nr)
            self._x("PRAGMA incremental_vacuum")

    # --- Schreiben -----------------------------------------------------------------
    def kerzen(self, tabelle: str, id_: str, zeilen: list, q: str | None = None) -> None:
        """zeilen: [t, o, h, l, c, v] oder [t, o, h, l, c, v, q]; q gilt für Zeilen ohne eigene Herkunft."""
        assert tabelle in KERZEN
        self._viele(f"INSERT OR REPLACE INTO {tabelle} (id, t, o, h, l, c, v, q) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(id_, *z[:6], z[6] if len(z) > 6 else q) for z in zeilen])

    def abschluesse(self, zeilen: list) -> None:
        self._viele("INSERT OR IGNORE INTO abschluesse VALUES (?, ?, ?, ?, ?)", zeilen)

    def buch(self, zeilen: list) -> None:
        self._viele("INSERT OR REPLACE INTO buch_1m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", zeilen)

    def tiefe(self, zeilen: list) -> None:
        """Krypto-Buchtiefe je Minute: [(id, t, seite, preis, menge, band)]."""
        self._viele("INSERT OR REPLACE INTO buch_tiefe VALUES (?, ?, ?, ?, ?, ?)", zeilen)

    def buch_1s(self, zeilen: list) -> None:
        """Krypto-Orderflow je Sekunde: 23 Felder je Zeile in der Reihenfolge der Tabelle (siehe krypto_flow.SPALTEN)."""
        self._viele("INSERT OR REPLACE INTO buch_1s VALUES (" + ", ".join("?" * 23) + ")", zeilen)

    def sekunden(self, id_: str, seit: int | None = None, n: int = 180) -> list:
        """Letzte Sekunden aus buch_1s für den Live-Takt, älteste zuerst: [t, bid, ask, kauf, verkauf, n, letzter, ofi, updates]."""
        zeilen = self._x("SELECT t, bid, ask, kauf, verkauf, n, letzter, ofi, updates FROM buch_1s WHERE id = ? AND t > ? ORDER BY t DESC LIMIT ?",
                         (id_, seit or 0, n))
        return [list(z) for z in reversed(zeilen)]

    def liquidationen(self, zeilen: list) -> None:
        """[(id, t_ms, seite, preis, menge)]"""
        self._viele("INSERT OR IGNORE INTO liquidationen VALUES (?, ?, ?, ?, ?)", zeilen)

    def perp(self, zeilen: list) -> None:
        """[(id, t_minute, mark, index_, funding, oi)]"""
        self._viele("INSERT OR REPLACE INTO perp_1m VALUES (?, ?, ?, ?, ?, ?)", zeilen)

    def roh(self, zeilen: list) -> None:
        self._viele("INSERT INTO buch_roh VALUES (?, ?, ?, ?, ?)", zeilen)
        zaehler: dict[int, int] = {}
        for z in zeilen:
            zaehler[z[0]] = zaehler.get(z[0], 0) + 1
        for nr, n in zaehler.items():
            self._x("UPDATE aufnahmen SET zeilen = zeilen + ? WHERE nr = ?", (n, nr))

    # --- Lesen -----------------------------------------------------------------------
    def kerzen_lesen(self, tabelle: str, id_: str, seit: int = 0, bis: int | None = None, mit_quelle: bool = False) -> list:
        assert tabelle in KERZEN
        spalten = "t, o, h, l, c, v" + (", q" if mit_quelle else "")
        sql = f"SELECT {spalten} FROM {tabelle} WHERE id = ? AND t >= ?" + (" AND t < ?" if bis else "") + " ORDER BY t"
        return [list(z) for z in self._x(sql, (id_, seit, bis) if bis else (id_, seit))]

    def abschluesse_lesen(self, id_: str, seit_ns: int, anzahl: int = 25) -> list:
        return self._x("SELECT t, venue, preis, menge FROM abschluesse WHERE id = ? AND t >= ? ORDER BY t DESC LIMIT ?",
                       (id_, seit_ns, anzahl))

    def abschluesse_zaehlen(self, id_: str, seit_ns: int, venues: tuple) -> int:
        frage = ",".join("?" * len(venues))
        return self._x(f"SELECT COUNT(*) FROM abschluesse WHERE id = ? AND t >= ? AND venue IN ({frage})",
                       (id_, seit_ns, *venues))[0][0]

    # --- Nachladen: welche Minutendateien schon verarbeitet sind ------------------------
    def geladen(self) -> set[str]:
        return {d for (d,) in self._x("SELECT datei FROM geladen")}

    def geladen_markieren(self, dateien: list[str]) -> None:
        jetzt = int(datetime.now(UTC).timestamp())
        self._viele("INSERT OR IGNORE INTO geladen VALUES (?, ?)", [(d, jetzt) for d in dateien])

    # --- Aufnahmen ---------------------------------------------------------------------
    def aufnahme_starten(self, id_: str, minuten: int) -> int:
        jetzt = datetime.now(UTC)
        with self.lock:
            cur = self.db.execute("INSERT INTO aufnahmen (id, start, bis) VALUES (?, ?, ?)",
                                  (id_, jetzt.isoformat(timespec="seconds"),
                                   (jetzt + timedelta(minutes=minuten)).isoformat(timespec="seconds")))
            return cur.lastrowid

    def aufnahme_beenden(self, nr: int) -> None:
        self._x("UPDATE aufnahmen SET ende = ? WHERE nr = ? AND ende IS NULL", (datetime.now(UTC).isoformat(timespec="seconds"), nr))

    def aufnahme_loeschen(self, nr: int) -> None:
        self._x("DELETE FROM buch_roh WHERE nr = ?", (nr,))
        self._x("DELETE FROM aufnahmen WHERE nr = ?", (nr,))

    def aufnahmen(self) -> list[dict]:
        return [dict(zip(("nr", "id", "start", "bis", "ende", "zeilen"), z))
                for z in self._x("SELECT nr, id, start, bis, ende, zeilen FROM aufnahmen ORDER BY nr DESC")]

    # --- Download-Zähler ------------------------------------------------------------------
    def download(self, quelle: str, bytes_: int) -> None:
        with self._dl_lock:
            self._downloads[quelle] = self._downloads.get(quelle, 0) + bytes_

    def downloads_sichern(self) -> None:
        with self._dl_lock:
            stand, self._downloads = self._downloads, {}
        tag = datetime.now(UTC).date().isoformat()
        self._viele("INSERT INTO downloads VALUES (?, ?, ?) ON CONFLICT (tag, quelle) DO UPDATE SET bytes = bytes + excluded.bytes",
                    [(tag, q, b) for q, b in stand.items()])

    # --- Einstellungen ----------------------------------------------------------------------
    def einstellungen(self) -> dict:
        werte = dict(VOREINSTELLUNGEN)
        for k, v in self._x("SELECT schluessel, wert FROM einstellungen"):
            werte[k] = json.loads(v)
        return werte

    def einstellen(self, **werte) -> None:
        for k, v in werte.items():
            if k in VOREINSTELLUNGEN:
                self._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)", (k, json.dumps(float(v) if k == "obergrenze_gb" else int(v))))

    # --- Größe -------------------------------------------------------------------------------
    @staticmethod
    def roh_groesse() -> tuple[int, int]:
        """(Bytes, Anzahl Dateien) der Rohablage."""
        dateien = [d for d in ROH.glob("*/*") if d.is_file()] if ROH.exists() else []
        return sum(d.stat().st_size for d in dateien), sum(1 for d in dateien if d.name != "index.jsonl")

    def groesse(self) -> dict:
        dateien = [self.datei, self.datei.with_name(self.datei.name + "-wal")]
        roh_bytes, roh_dateien = self.roh_groesse()
        belegt = sum(d.stat().st_size for d in dateien if d.exists()) + roh_bytes
        zeilen = {t: self._x(f"SELECT COUNT(*) FROM {t}")[0][0] for t in DATENARTEN}
        try:                                                     # exakt, wenn SQLite mit dbstat gebaut ist
            seiten = dict(self._x("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"))
            bytes_art = {t: seiten.get(t, 0) + (seiten.get("buch_roh_nr", 0) if t == "buch_roh" else 0) for t in DATENARTEN}
            genau = True
        except sqlite3.OperationalError:
            bytes_art = {t: zeilen[t] * ZEILE_BYTES[t] for t in DATENARTEN}
            genau = False
        je_wert = {}
        for t in JE_WERT:
            for id_, n in self._x(f"SELECT id, COUNT(*) FROM {t} GROUP BY id"):
                je_wert.setdefault(id_, {})[t] = n
        for id_, n in self._x("SELECT a.id, COUNT(*) FROM buch_roh r JOIN aufnahmen a ON a.nr = r.nr GROUP BY a.id"):
            je_wert.setdefault(id_, {})["buch_roh"] = n
        heute = datetime.now(UTC).date()
        dl = self._x("SELECT tag, SUM(bytes) FROM downloads WHERE tag >= ? GROUP BY tag", ((heute - timedelta(days=31)).isoformat(),))
        dl_quelle = self._x("SELECT quelle, SUM(bytes) FROM downloads WHERE tag = ? GROUP BY quelle", (heute.isoformat(),))
        with self._dl_lock:
            offen = dict(self._downloads)
        frei = shutil.disk_usage(self.datei.parent).free
        return {"datei": str(self.datei), "belegt": belegt, "frei": frei, "genau": genau,
                "datenarten": [{"art": t, "titel": DATENARTEN[t], "zeilen": zeilen[t], "bytes": bytes_art[t]} for t in DATENARTEN]
                              + [{"art": "roh", "titel": "Rohdateien für Analysen (Dateien)", "zeilen": roh_dateien, "bytes": roh_bytes}],
                "je_wert": je_wert, "zeile_bytes": ZEILE_BYTES,
                "download_heute": sum(b for t, b in dl if t == heute.isoformat()) + sum(offen.values()),
                "download_monat": sum(b for t, b in dl if t[:7] == heute.isoformat()[:7]) + sum(offen.values()),
                "download_quellen": {q: b + offen.get(q, 0) for q, b in dl_quelle} | {q: b for q, b in offen.items() if q not in dict(dl_quelle)},
                "einstellungen": self.einstellungen(), "aufnahmen": self.aufnahmen()}

    # --- Aufräumen --------------------------------------------------------------------------------
    def aufraeumen(self, probe: bool = False) -> dict:
        """Setzt Aufbewahrung und Obergrenze durch. probe=True zählt nur, was passieren würde, und ändert nichts.
        Rückgabe: je Schritt ein Eintrag mit Anzahl und Beschreibung, dazu die Größe vorher und nachher."""
        e = self.einstellungen()
        jetzt = datetime.now(UTC)
        vorher = self.groesse()["belegt"]
        grenze_1m = int((jetzt - timedelta(days=e["tage_1m"])).timestamp())
        grenze_ab = int((jetzt - timedelta(days=e["tage_abschluesse"])).timestamp() * 1e9)
        grenze_buch = int((jetzt - timedelta(days=e["tage_buch"])).timestamp())
        grenze_roh = (jetzt - timedelta(days=e["tage_roh"])).date().isoformat()
        grenze_tiefe = int((jetzt - timedelta(days=e["tage_tiefe"])).timestamp())
        grenze_1s = int((jetzt - timedelta(days=e["tage_1s"])).timestamp())
        grenze_perp = int((jetzt - timedelta(days=e["tage_perp"])).timestamp())
        alte_roh = [o for o in (sorted(ROH.glob("????-??-??")) if ROH.exists() else []) if o.name < grenze_roh]
        schritte = [
            {"was": "kerzen_1m", "anzahl": self._x("SELECT COUNT(*) FROM kerzen_1m WHERE t < ?", (grenze_1m,))[0][0],
             "text": f"1-Minuten-Kerzen älter als {e['tage_1m']} Tage zu 15-Minuten- und Tageskerzen verdichten"},
            {"was": "abschluesse", "anzahl": self._x("SELECT COUNT(*) FROM abschluesse WHERE t < ?", (grenze_ab,))[0][0],
             "text": f"Abschlüsse älter als {e['tage_abschluesse']} Tage löschen"},
            {"was": "buch_1m", "anzahl": self._x("SELECT COUNT(*) FROM buch_1m WHERE t < ?", (grenze_buch,))[0][0],
             "text": f"Orderbuch je Minute älter als {e['tage_buch']} Tage löschen"},
            {"was": "buch_tiefe", "anzahl": self._x("SELECT COUNT(*) FROM buch_tiefe WHERE t < ?", (grenze_tiefe,))[0][0],
             "text": f"Krypto-Buchtiefe älter als {e['tage_tiefe']} Tage löschen"},
            {"was": "buch_1s", "anzahl": self._x("SELECT COUNT(*) FROM buch_1s WHERE t < ?", (grenze_1s,))[0][0],
             "text": f"Krypto-Orderflow je Sekunde älter als {e['tage_1s']} Tage löschen"},
            {"was": "perp", "anzahl": self._x("SELECT COUNT(*) FROM perp_1m WHERE t < ?", (grenze_perp,))[0][0]
                                      + self._x("SELECT COUNT(*) FROM liquidationen WHERE t < ?", (grenze_perp * 1000,))[0][0],
             "text": f"Futures-Kontext und Liquidationen älter als {e['tage_perp']} Tage löschen"},
            {"was": "roh", "anzahl": len(alte_roh), "text": f"Tagesordner mit Rohdateien älter als {e['tage_roh']} Tage löschen"},
        ]
        ueber = max(0, vorher - e["obergrenze_gb"] * 1e9)
        schritte.append({"was": "obergrenze", "anzahl": int(ueber),
                         "text": "Über der Obergrenze: älteste Aufnahmen, dann Rohdateien-Tage, dann Abschlüsse löschen" if ueber
                                 else f"Obergrenze von {e['obergrenze_gb']:g} GB eingehalten, nichts zusätzlich zu löschen"})
        if probe:
            return {"probe": True, "schritte": schritte, "vorher": vorher, "zeit": jetzt.isoformat(timespec="seconds")}

        # 1-Minuten-Kerzen jenseits der Aufbewahrung zu 15-Minuten- und Tageskerzen verdichten, dann löschen
        for (id_,) in self._x("SELECT DISTINCT id FROM kerzen_1m WHERE t < ?", (grenze_1m,)):
            alt = self.kerzen_lesen("kerzen_1m", id_, 0, grenze_1m, mit_quelle=True)
            q = "sip" if all(z[6] == "sip" for z in alt) else ",".join(sorted({str(z[6]) for z in alt}))
            alt = [z[:6] for z in alt]
            self.kerzen("kerzen_15m", id_, buendeln(alt, 900), q)
            self.kerzen("kerzen_1d", id_, buendeln(alt, 86400), q)
        self._x("DELETE FROM kerzen_1m WHERE t < ?", (grenze_1m,))
        self._x("DELETE FROM abschluesse WHERE t < ?", (grenze_ab,))
        self._x("DELETE FROM buch_1m WHERE t < ?", (grenze_buch,))
        self._x("DELETE FROM buch_tiefe WHERE t < ?", (grenze_tiefe,))
        self._x("DELETE FROM buch_1s WHERE t < ?", (grenze_1s,))
        self._x("DELETE FROM perp_1m WHERE t < ?", (grenze_perp,))
        self._x("DELETE FROM liquidationen WHERE t < ?", (grenze_perp * 1000,))
        self._x("DELETE FROM geladen WHERE t < ?", (int((jetzt - timedelta(days=4)).timestamp()),))
        for ordner in alte_roh:
            shutil.rmtree(ordner)
        geloescht = []
        # Obergrenze: zuerst die ältesten Aufnahmen, dann die ältesten Rohdateien-Tage, dann die ältesten Abschlüsse
        while self.groesse()["belegt"] > e["obergrenze_gb"] * 1e9:
            alt = self._x("SELECT nr FROM aufnahmen WHERE ende IS NOT NULL ORDER BY nr LIMIT 1")
            tage_roh = sorted(ROH.glob("????-??-??")) if ROH.exists() else []
            if alt:
                self.aufnahme_loeschen(alt[0][0]); geloescht.append(f"Aufnahme {alt[0][0]}")
            elif len(tage_roh) > 1:                                  # den laufenden Tag nie
                shutil.rmtree(tage_roh[0]); geloescht.append(f"Rohdateien {tage_roh[0].name}")
            else:
                erster = self._x("SELECT MIN(t) FROM abschluesse")[0][0]
                if erster is None:
                    break
                self._x("DELETE FROM abschluesse WHERE t < ?", (erster + 86_400 * 10**9,)); geloescht.append("ältester Abschlusstag")
            self._x("PRAGMA incremental_vacuum")
            self._x("PRAGMA wal_checkpoint(TRUNCATE)")
        self._x("PRAGMA incremental_vacuum")              # freigewordenen Platz an das Laufwerk zurückgeben
        self._x("PRAGMA wal_checkpoint(TRUNCATE)")
        schritte[-1]["geloescht"] = geloescht
        return {"probe": False, "schritte": schritte, "vorher": vorher, "nachher": self.groesse()["belegt"],
                "geloescht": geloescht, "zeit": jetzt.isoformat(timespec="seconds")}

def buendeln(zeilen: list, sekunden: int) -> list:
    """Kerzen [t, o, h, l, c, v] auf einen größeren Zeitrahmen zusammenfassen (t in Sekunden)."""
    aus = []
    for t, o, h, l, c, v in zeilen:
        fach = t // sekunden * sekunden
        if aus and aus[-1][0] == fach:
            k = aus[-1]
            k[2], k[3], k[4], k[5] = max(k[2], h), min(k[3], l), c, k[5] + v
        else:
            aus.append([fach, o, h, l, c, v])
    return aus
