"""Lokales Dashboard mit Live-Kursen. Nur Standardbibliothek (plus websockets für Alpaca).

  python -m marktdaten dashboard --port 8812 --host 127.0.0.1,<Tailscale-IP>
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__, einrichtung, netz, ports, status
from ..live import logos, orderflow, suche, watchlist
from ..live.demo import DemoSammler
from ..live.demo import watchlist as demo_watchlist
from ..live.eu import EuSammler
from ..live.historie_de import HistorieDE
from ..live.krypto import KryptoSammler
from ..live.krypto_tiefe import KryptoTiefe
from ..live.perp import PerpKontext
from ..live.rohstoffe import RohstoffSammler
from ..live.papier import Papier
from ..live.schreiber import Schreiber
from ..live.speicher import Speicher
from ..live.us import UsSammler
from ..live.zustand import Wert, Zustand
from ..quellen import PROFIL, QUELLEN
from ..quellen.alpaca import schluessel

SEITE = pathlib.Path(__file__).with_name("seite.html")
UI_CSS = pathlib.Path(__file__).parent.parent / "ui.css"    # gemeinsame Gestaltung aller Seiten
IC_CHARTS = pathlib.Path(__file__).with_name("static") / "ic-charts"   # Kopie der Web-App aus iC Charts
TYPEN = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
CACHE_S = 120                         # Quellenstatus höchstens alle zwei Minuten neu prüfen

_cache: dict = {"zeit": 0.0, "daten": None}
_sperre = threading.Lock()
ZUSTAND: Zustand | None = None
EU: EuSammler | None = None
SCHREIBER: Schreiber | None = None
PAPIER: Papier | None = None
HISTORIE: HistorieDE | None = None
US: UsSammler | None = None


def quellen_json() -> list[dict]:
    felder = ("name", "titel", "region", "inhalt", "tiefe", "abrufbar", "lizenz", "info_url", "arten", "schluessel")
    aus = []
    for q in QUELLEN.values():
        d = {k: asdict(q)[k] for k in felder}
        gruppe, verzug, nutzung = PROFIL[q.name]
        d.update(gruppe=gruppe, verzug=verzug, nutzung=nutzung,
                 orderbuch=q.tiefe.split(" mit")[0] if "tufe" in q.tiefe else "",
                 kurse=bool({"pretrade", "quotes", "deep"} & set(q.arten)),
                 abschluesse=bool({"posttrade", "trades", "deep"} & set(q.arten)))
        aus.append(d)
    return aus


def status_json(neu: bool = False) -> dict:
    with _sperre:
        if neu or not _cache["daten"] or time.time() - _cache["zeit"] > CACHE_S:
            _cache.update(daten=status.alle(), zeit=time.time())
        return _cache["daten"]


class Handler(BaseHTTPRequestHandler):
    def _senden(self, code: int, inhalt: bytes, typ: str):
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(inhalt)

    def _json(self, daten) -> None:
        self._senden(200, json.dumps(daten).encode(), "application/json")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        frage = urllib.parse.parse_qs(url.query)
        if url.path == "/" and einrichtung.offen():                 # erster Start: zuerst die Einrichtung
            self.send_response(302)
            self.send_header("Location", "/einrichtung")
            self.end_headers()
        elif (antwort := einrichtung.get(url.path)) is not None:     # /einrichtung und /api/einrichtung…
            self._senden(*antwort)
        elif url.path == "/ui.css":
            self._senden(200, UI_CSS.read_bytes(), "text/css; charset=utf-8")
        elif url.path == "/":
            self._senden(200, SEITE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/quellen":
            self._json({"version": __version__, "quellen": quellen_json(), "demo": ports.DEMO, "demo_krypto_echt": ports.DEMO_NAMEN_ECHT, "gegenstueck": ports.GEGENSTUECK})
        elif url.path == "/api/status":
            self._json(status_json(neu="1" in frage.get("neu", [])))
        elif url.path == "/api/kurse":
            self._json(ZUSTAND.kurse())
        elif url.path == "/api/kurs":
            d = ZUSTAND.detail(frage.get("id", [""])[0])
            self._json(d) if d else self._senden(404, b"unbekannt", "text/plain")
        elif url.path in ("/ic-charts", "/ic-charts/"):
            self.send_response(302)
            self.send_header("Location", "/ic-charts/index.html" + (f"?{url.query}" if url.query else ""))
            self.end_headers()
        elif url.path == "/ic-charts/data/snapshot.js":            # Daten für iC Charts aus dem Kursstand
            js = "window.SNAPSHOT = " + json.dumps(ZUSTAND.ic_snapshot(), separators=(",", ":")) + ";"
            self._senden(200, js.encode(), TYPEN[".js"])
        elif url.path == "/ic-charts/data/kerzen":                 # Live-Nachführen einer Aktie in iC Charts
            d = ZUSTAND.ic_kerzen(frage.get("sym", [""])[0], frage.get("tf", [""])[0])
            self._json(d) if d else self._senden(404, b"unbekannt", "text/plain")
        elif url.path == "/ic-charts/data/orderflow":              # Delta und Volumenprofil aus den gespeicherten Abschlüssen
            sym = frage.get("sym", [""])[0]
            with ZUSTAND.lock:
                w = next((x for x in ZUSTAND.werte.values() if x.symbol == sym), None)
            zahl = lambda k: int(frage[k][0]) if frage.get(k, [""])[0].isdigit() else None
            d = orderflow.orderflow(ZUSTAND.speicher, w, profil="1" in frage.get("profil", []), von=zahl("von"), bis=zahl("bis"),
                                    stufen=zahl("stufen") or 40) if w else None
            self._json(d) if d else self._senden(404, b"unbekannt", "text/plain")
        elif url.path == "/api/sekunden":                           # Krypto-Orderflow je Sekunde für den Live-Takt im Reiter Orderbuch
            id_, seit, n, ab = frage.get("id", [""])[0], frage.get("seit", [""])[0], frage.get("n", [""])[0], frage.get("ab", [""])[0]
            d = {"zeilen": ZUSTAND.speicher.sekunden(id_, int(seit) if seit.isdigit() else None, min(600, int(n)) if n.isdigit() else 180)}
            flow = getattr(ZUSTAND, "flow", None)
            if ab.isdigit() and flow:                               # einzelne Abschlüsse (Empfangszeit in ms) für den Datenstrom
                d["abschluesse"] = flow.abschluesse(id_, int(ab))
            self._json(d)
        elif url.path == "/api/tiefe/jetzt":                        # Krypto: laufende Minute der Buchtiefe als Heatmap-Spalte
            tiefe = getattr(ZUSTAND, "tiefe", None)
            self._json({"spalte": tiefe.jetzt(frage.get("id", [""])[0]) if tiefe else None})
        elif url.path == "/ic-charts/data/kurse":
            self._json(ZUSTAND.ic_kurse())
        elif url.path.startswith("/ic-charts/"):
            datei = (IC_CHARTS / url.path.removeprefix("/ic-charts/")).resolve()
            if datei.is_relative_to(IC_CHARTS.resolve()) and datei.is_file() and datei.suffix in TYPEN:   # kein Pfad nach außen
                self._senden(200, datei.read_bytes(), TYPEN[datei.suffix])
            else:
                self._senden(404, b"nicht gefunden", "text/plain")
        elif url.path == "/api/watchlist":
            self._json({"werte": ZUSTAND.speicher.watchlist(), "max_us": watchlist.MAX_US})
        elif url.path == "/api/suche":
            self._json(suche.suchen(frage.get("q", [""])[0], frage.get("markt", [None])[0] or None))
        elif url.path == "/api/speicher":
            self._json(ZUSTAND.speicher.groesse())
        elif url.path == "/api/logos":                           # Logo-Adressen je Wert (sonst Kürzel-Kreis)
            self._json(logos.uebersicht(ZUSTAND))
        elif url.path.startswith("/logo/"):                      # ein Logo aus dem Datenordner
            id_ = urllib.parse.unquote(url.path[len("/logo/"):])
            w = ZUSTAND.werte.get(id_)
            p = logos.datei(id_, w.symbol if w else "")
            if p is None:
                self._senden(404, b"kein Logo", "text/plain")
                return
            self.send_response(200)
            self.send_header("Content-Type", logos.ENDUNG[p.suffix])
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(p.read_bytes())
        elif url.path == "/api/papier":                          # Papierhandel: Depot, Orders, Trades, Journal
            self._json(PAPIER.uebersicht())
        elif url.path == "/api/papier/journal":                  # Journal durchsuchen (Seite und Claude)
            n = frage.get("n", [""])[0]
            self._json({"eintraege": PAPIER.journal(frage.get("q", [""])[0], frage.get("wer", [""])[0],
                                                    frage.get("symbol", [""])[0], int(n) if n.isdigit() else None)})
        elif url.path == "/api/strom":
            self._strom()
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    def _strom(self) -> None:
        """Server-Sent Events: meldet jede neue Version, höchstens einmal pro Sekunde."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        gesendet, ruhe = -1, time.time()
        try:
            while True:
                if ZUSTAND.version != gesendet:
                    gesendet = ZUSTAND.version
                    self.wfile.write(f"data: {gesendet}\n\n".encode())
                    self.wfile.flush()
                    ruhe = time.time()
                elif time.time() - ruhe > 15:                    # Lebenszeichen gegen Zeitüberschreitungen
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    ruhe = time.time()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _koerper(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_POST(self):
        pfad = urllib.parse.urlparse(self.path).path
        if pfad.startswith("/api/einrichtung"):            # schreibt Schlüssel: nur mit eigenem Kopf (fremde Seiten scheitern am CORS-Vorabtest)
            if self.headers.get("X-IC-Delta") != "1":
                self._senden(403, b"fehlender Kopf X-IC-Delta", "text/plain")
                return
            d = einrichtung.post(pfad, self._koerper())
            self._json(d) if d is not None else self._senden(404, b"nicht gefunden", "text/plain")
        elif pfad == "/api/nachladen":
            self._json({"gestartet": EU.nachladen()} if EU else {"gestartet": False, "hinweis": "Demo: erfundene Daten, nichts nachzuladen"})
        elif pfad == "/api/watchlist":
            self._json(watchlist_hinzufuegen(**self._koerper()))
        elif pfad == "/api/einstellungen":
            ZUSTAND.speicher.einstellen(**self._koerper())
            self._json(ZUSTAND.speicher.einstellungen())
        elif pfad == "/api/aufraeumen":                    # ?probe=1: nur zählen, nichts ändern
            self._json(ZUSTAND.speicher.aufraeumen(probe="probe=1" in self.path))
        elif pfad == "/api/aufnahme":
            d = self._koerper()
            self._json(aufnahme_starten(d["id"], int(d.get("minuten", 15))))
        elif pfad == "/api/papier/order":                  # Orders kommen nur aus iC Charts
            self._json(PAPIER.aufgeben(**self._koerper()))
        elif pfad == "/api/papier/einstellungen":
            self._json(PAPIER.einstellen(**self._koerper()))
        elif pfad == "/api/papier/zuruecksetzen":
            self._json(PAPIER.zuruecksetzen())
        elif pfad == "/api/papier/eintrag":                # eigener Journal-Eintrag von Niko (neu oder geändert)
            self._json(PAPIER.eintrag(**self._koerper()))
        elif pfad == "/api/papier/journal":                # Claudes Einträge vom Agenten auf demselben Rechner
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._senden(403, b"nur lokal", "text/plain")
                return
            d = self._koerper()
            self._json(PAPIER.journal_schreiben(str(d.get("art", "woche")), str(d.get("text", "")), d.get("bezug"),
                                                str(d.get("modell", "")), d.get("kosten"), d.get("symbol"), d.get("titel")))
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    def do_DELETE(self):
        url = urllib.parse.urlparse(self.path)
        frage = urllib.parse.parse_qs(url.query)
        if url.path == "/api/watchlist":
            id_ = frage.get("id", [""])[0]
            ZUSTAND.entfernen(id_)
            ZUSTAND.speicher.entfernen(id_, daten_loeschen="1" in frage.get("daten", []))
            self._json({"entfernt": id_})
        elif url.path == "/api/aufnahme":
            nr = int(frage.get("nr", ["0"])[0])
            a = next((x for x in ZUSTAND.speicher.aufnahmen() if x["nr"] == nr), None)
            if a and not a["ende"]:
                SCHREIBER.aufnahme_beenden(a["id"], nr)            # laufende Aufnahme beenden
            elif a:
                ZUSTAND.speicher.aufnahme_loeschen(nr)             # beendete Aufnahme löschen
            self._json({"nr": nr})
        elif url.path == "/api/papier/order":
            self._json(PAPIER.stornieren(int(frage.get("nr", ["0"])[0])))
        elif url.path == "/api/papier/eintrag":
            self._json(PAPIER.eintrag_loeschen(int(frage.get("nr", ["0"])[0])))
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    def log_message(self, *_):
        pass


def watchlist_hinzufuegen(id: str, markt: str, **_) -> dict:
    """Wert per ID (ISIN, Ticker, Binance-Paar) aufnehmen; Name und Kürzel kommen aus den offiziellen Listen."""
    if id in ZUSTAND.werte:
        return {"fehler": "steht schon auf der Watchlist"}
    if markt == "US" and sum(w.markt == "US" for w in ZUSTAND.werte.values()) >= watchlist.MAX_US:
        return {"fehler": f"Alpaca erlaubt im Gratis-Tarif höchstens {watchlist.MAX_US} US-Werte im Live-Strom"}
    x = suche.finden(id, markt)
    if x is None:
        return {"fehler": "nicht in der offiziellen Liste gefunden"}
    ZUSTAND.speicher.hinzufuegen(x["id"], x["symbol"], x["name"], markt)
    ZUSTAND.hinzufuegen(Wert(x["id"], x["symbol"], x["name"], markt))
    if markt == "DE" and EU:
        EU.nachladen({x["id"]})                                      # Tagesverlauf nur für den neuen Wert
    if markt == "DE" and HISTORIE:
        HISTORIE.nachladen(x["id"])                                  # Tageskerzen-Historie (EODHD), falls Schlüssel da
    return {"hinzugefuegt": x}


def werte_aufnehmen(werte: list) -> list[str]:
    """Einrichtung: Watchlist-Vorlage übernehmen (bekannte Werte, ohne Suche in den offiziellen Listen)."""
    neu = [w for w in werte if w[0] not in ZUSTAND.werte]
    for id_, sym, name, markt in neu:
        ZUSTAND.speicher.hinzufuegen(id_, sym, name, markt)
        ZUSTAND.hinzufuegen(Wert(id_, sym, name, markt))
        if markt == "DE" and HISTORIE:
            HISTORIE.nachladen(id_)
    de = {w[0] for w in neu if w[3] == "DE"}
    if de and EU:
        EU.nachladen(de)
    return [w[1] for w in neu]


def nach_schluessel(name: str) -> None:
    """Einrichtung: ein neu eingetragener Schlüssel wirkt ohne Neustart."""
    global US
    if ports.DEMO:
        return
    if name == "alpaca" and US is None:
        US = UsSammler(ZUSTAND)
        US.start()
        ZUSTAND.melden("alpaca", fehler=None)
    elif name == "eodhd" and HISTORIE:
        for w in list(ZUSTAND.werte.values()):
            if w.markt == "DE":
                HISTORIE.nachladen(w.id)


def aufnahme_starten(id_: str, minuten: int) -> dict:
    with ZUSTAND.lock:
        if id_ not in ZUSTAND.werte:
            return {"fehler": "nicht auf der Watchlist"}
        if id_ in ZUSTAND.aufnahmen:
            return {"fehler": "Aufnahme läuft schon", "nr": ZUSTAND.aufnahmen[id_]}
        nr = ZUSTAND.speicher.aufnahme_starten(id_, max(1, min(minuten, 600)))
        ZUSTAND.aufnahmen[id_] = nr
        ZUSTAND.geaendert()
    return {"nr": nr}


def _zustand_aufbauen() -> Zustand:
    global EU, SCHREIBER, PAPIER, HISTORIE, US
    speicher = Speicher()
    netz.ZAEHLER = speicher.download                                   # Download-Verbrauch erfassen
    einrichtung.bestand_uebernehmen(speicher)                          # bestehende Installation gilt als eingerichtet
    if not speicher.watchlist() and not einrichtung.offen():           # erster Start: Start-Watchlist übernehmen (sonst wählt die Einrichtung)
        for id_, sym, name, markt in (demo_watchlist() if ports.DEMO else watchlist.START):
            speicher.hinzufuegen(id_, sym, name, markt)
    z = Zustand(speicher)
    einrichtung.VERBINDUNG.update(zustand=z, speicher=speicher, aufnehmen=werte_aufnehmen, nach_schluessel=nach_schluessel)
    einrichtung.Herzschlag(speicher).start()                           # Lebenszeichen je Minute: Online-Status und Lücken
    for a in speicher.aufnahmen():                                     # nach Neustart: offene Aufnahmen schließen
        if not a["ende"]:
            speicher.aufnahme_beenden(a["nr"])
    for x in speicher.watchlist():
        z.hinzufuegen(Wert(x["id"], x["symbol"], x["name"], x["markt"]))
    SCHREIBER = Schreiber(z)
    SCHREIBER.start()
    PAPIER = Papier(z)
    PAPIER.start()
    if ports.DEMO:                                                     # Demo: erfundene Daten, keine Abrufe bei Börsen
        DemoSammler(z).start()
        if ports.DEMO_NAMEN_ECHT:                                      # Video-Demo: Krypto echt wie im normalen Betrieb
            KryptoSammler(z).start()
            KryptoTiefe(z).start()
            PerpKontext(z).start()
            RohstoffSammler(z).start()                                 # Rohstoffe: Binance-Futures live, wie im normalen Betrieb
        return z
    EU = EuSammler(z)
    EU.start()
    HISTORIE = HistorieDE(z)                                           # Tageskerzen-Historie DE (EODHD, eigener Schlüssel)
    HISTORIE.start()
    logos.Logos(z).start()                                             # Logos je Wert, einmal geholt (nicht im Demo-Betrieb)
    KryptoSammler(z).start()
    KryptoTiefe(z).start()                                             # Krypto: Buch bis 1.000 Stufen (Heatmap)
    PerpKontext(z).start()                                             # Krypto: Liquidationen, Funding, Open Interest (Binance Futures)
    RohstoffSammler(z).start()                                         # Rohstoffe: Binance-Futures-Perpetuals
    try:
        schluessel()
        US = UsSammler(z)
        US.start()
    except RuntimeError:
        z.melden("alpaca", fehler="kein Schlüssel eingerichtet, US-Werte bleiben leer")
    return z


def starten(port: int = ports.DASHBOARD, hosts: str = "127.0.0.1"):
    """hosts: kommagetrennt, z. B. "127.0.0.1,100.x.y.z" für localhost plus Tailscale.
    Bewusst kein 0.0.0.0: das Dashboard soll nicht im ganzen Heimnetz erreichbar sein."""
    global ZUSTAND
    einrichtung.VERBINDUNG["hosts"] = [h.strip() for h in hosts.split(",") if h.strip()]
    ZUSTAND = _zustand_aufbauen()
    ThreadingHTTPServer.daemon_threads = True
    server = [ThreadingHTTPServer((h.strip(), port), Handler) for h in hosts.split(",") if h.strip()]
    for s in server:
        print(f"Dashboard: http://{s.server_address[0]}:{port}/", flush=True)
        threading.Thread(target=s.serve_forever, daemon=True).start()
    threading.Event().wait()
