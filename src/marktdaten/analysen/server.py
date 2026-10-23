"""Kleiner HTTP-Dienst für den Reiter „Analysen“. Ruft nichts bei Börsen ab, liest nur Rohdateien.

Das Dashboard bindet die Seite als iframe ein. Ergebnisse werden je Wert, Tag und Parameter
zwischengespeichert, bis neue Rohdateien dazukommen. Der Agenten-Server (Claude, Port 8814) nutzt dieselbe
API: Kurzfassungen für die KI, Werkstatt, Alarme, Dossiers, Journal und Notizen.
"""
from __future__ import annotations

import json
import re
import pathlib
import threading
import time
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import ports
from . import buchlage, charts, heatmap, kurz, lesen, notizen, pruefstein, signale
from .alarme import Alarme, agent, ruhe_setzen, ruhe_stand, telegram_senden, telegram_stand
from .formationen import ARTEN, TF, erkennen
from .lesen import Rohdaten, watchlist
from .rechnen import STANDARD, auswerten
from .werkstatt import REFERENZ, Werkstatt

SEITE = pathlib.Path(__file__).with_name("seite.html")
UI_CSS = pathlib.Path(__file__).parent.parent / "ui.css"    # gemeinsame Gestaltung, dieselbe Datei wie im Dashboard
PRUEFSTAND = lesen.DATEN / "analysen" / "pruefstand"
ROH = Rohdaten()
_cache: OrderedDict = OrderedDict()
_rechnen = threading.Lock()          # immer nur eine Rechnung: schont den Mac Mini
ALARME: Alarme | None = None
WERKSTATT: Werkstatt | None = None


def parameter(frage: dict) -> dict:
    p = {}
    for k, typ, lo, hi in (("theta", float, 0.05, 0.99), ("halten", int, 1, 900), ("wand", float, 1.5, 50),
                           ("mm_eur", float, 1000, 1_000_000), ("mm_limit", int, 1, 50)):
        if k in frage:
            try:
                p[k] = min(hi, max(lo, typ(frage[k][0])))
            except ValueError:
                pass
    return p


def isin_von(wert: str) -> str:
    """ISIN oder Kürzel (SAP) → ISIN; unbekannte Angaben bleiben, wie sie sind."""
    werte = {w["id"]: w for w in watchlist()}
    if wert in werte:
        return wert
    return next((i for i, w in werte.items() if w["symbol"] == wert), wert)


def analyse(isin: str, tag: str, p: dict) -> dict:
    schluessel = (isin, tag, tuple(sorted(p.items())))
    stand = ROH.stand(tag)
    treffer = _cache.get(schluessel)
    if treffer and treffer[0] == stand:
        _cache.move_to_end(schluessel)
        return treffer[1]
    with _rechnen:
        treffer = _cache.get(schluessel)
        if treffer and treffer[0] == stand:
            return treffer[1]
        t = time.time()
        ergebnis = auswerten(ROH.ereignisse(isin, tag), isin, tag, p)
        ergebnis["rechenzeit_s"] = round(time.time() - t, 2)
        ergebnis["abdeckung"] = ROH.abdeckung(tag, isin)
        _cache[schluessel] = (stand, ergebnis)
        while len(_cache) > 24:
            _cache.popitem(last=False)
        return ergebnis


def uebersicht() -> dict:
    tage = ROH.tage()
    namen = {w["id"]: w for w in watchlist()}
    je_tag = []
    for tag in tage[:15]:
        werte = sorted(ROH.werte(tag))
        je_tag.append({"tag": tag, "werte": werte})
    alle = sorted({i for t in je_tag for i in t["werte"]} | {i for i, w in namen.items() if w["markt"] == "DE"})
    return {"tage": je_tag, "werte": [{"id": i, "symbol": namen.get(i, {}).get("symbol", i), "name": namen.get(i, {}).get("name", i)}
                                       for i in alle],
            "standard": {k: v for k, v in STANDARD.items() if k != "ordern"}, "ordern": STANDARD["ordern"], "roh": str(ROH.ort)}


def formationen(wert: str, tf: str, staerke: int, mit_kerzen: bool = True) -> dict:
    werte = {w["id"]: w for w in watchlist()}
    wert = isin_von(wert)
    k = lesen.kerzen(wert, tf, 400)
    return {"wert": werte.get(wert, {"id": wert}), "tf": tf, "staerke": staerke, "kerzen": k[-300:] if mit_kerzen else [],
            "formationen": erkennen(k, staerke), "kerzen_gesamt": len(k)}


class Handler(BaseHTTPRequestHandler):
    def _senden(self, code: int, inhalt: bytes, typ: str):
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(inhalt)

    def _cors(self):
        """Nur das Dashboard (Port 8812) darf von außen zugreifen, z. B. Claude im Chart."""
        herkunft = self.headers.get("Origin", "")
        if herkunft.endswith(f":{ports.DASHBOARD}"):
            self.send_header("Access-Control-Allow-Origin", herkunft)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _lokal(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _json(self, daten, code: int = 200):
        self._senden(code, json.dumps(daten, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _koerper(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        pfad = urllib.parse.urlparse(self.path).path
        try:
            d = self._koerper()
            if pfad == "/api/alarme":
                self._json(ALARME.anlegen(d))
            elif pfad == "/api/alarme/aktiv":
                ALARME.umschalten(d["id"], bool(d["aktiv"]))
                self._json({"ok": True})
            elif pfad == "/api/ruhe":                              # Demo läuft: Telegram-Meldungen zurückhalten
                self._json(ruhe_setzen(bool(d.get("an")), float(d.get("minuten", 180))))
            elif pfad == "/api/telegram/test":
                fehler = telegram_senden("🔔 <b>iC Delta</b>: Testnachricht aus dem Reiter Analysen.")
                self._json({"ok": fehler is None, "fehler": fehler})
            elif pfad == "/api/telegram/senden":                  # nur der Agenten-Server auf demselben Rechner
                if not self._lokal():
                    self._json({"fehler": "nur lokal"}, 403)
                    return
                text = str(d.get("text", ""))[:8000]
                if d.get("markdown"):                               # Routine: Markdown von Claude plus Kopf und Fußzeile
                    from .alarme import fusszeile, markdown_zu_html
                    text = (d.get("kopf", "") + "\n\n" if d.get("kopf") else "") + markdown_zu_html(text) + "\n\n" + fusszeile(d.get("modell"), d.get("kosten"))
                fehler = telegram_senden(text, d.get("bilder") or [])
                self._json({"ok": fehler is None, "fehler": fehler})
            elif pfad == "/api/werkstatt/lauf":
                self._json(WERKSTATT.lauf(d.get("code", ""), d.get("wert", ""), d.get("tag") or (ROH.tage() or [""])[0], titel=d.get("titel", ""),
                                          id_=d.get("id"), quelle=d.get("quelle", "claude"), beschreibung=d.get("beschreibung", ""),
                                          modell=d.get("modell", ""), parameter=parameter({k: [v] for k, v in (d.get("parameter") or {}).items()})))
            elif pfad == "/api/werkstatt/anpinnen":
                self._json(WERKSTATT.anpinnen(d["id"], d.get("titel"), d.get("beschreibung"), bool(d.get("angepinnt", True))))
            elif pfad == "/api/dossier":
                self._json(notizen.dossier_schreiben(d.get("wert", ""), d.get("text", "")))
            elif pfad == "/api/journal":
                self._json(notizen.journal_eintragen(d))
            elif pfad == "/api/notizen":
                self._json(notizen.notiz_schreiben(d.get("art", "notiz"), d.get("text", ""), d.get("modell", ""), d.get("wert"), d.get("fokus")))
            elif pfad == "/api/ki":
                self._json(notizen.ki_schreiben(d))
            elif pfad in ("/api/pruefstand/uebersetzen", "/api/pruefstand/pruefen", "/api/pruefstand/ableiten"):   # an den Agenten weiterreichen
                antwort = agent(pfad.replace("/api", ""), d, timeout=30)
                self._json(antwort, 400 if antwort.get("fehler") else 200)
            elif pfad == "/api/pruefstein":                       # neu rechnen (dauert wenige Sekunden, im Hintergrund)
                self._json(pruefstein.starten())
            elif pfad == "/api/orderbuch/claude":                 # Einordnung des Orderbuchs durch Claude (Knopf im Bereich Orderbuch)
                self._json(buchlage.einordnen(str(d.get("wert", "")), str(d.get("name", "")), d.get("fakten") or {}))
            elif pfad == "/api/charts/claude":                    # Einordnung der Top-Charts durch Claude starten (läuft im Hintergrund)
                self._json(charts.claude_starten())
            else:
                self._senden(404, b"nicht gefunden", "text/plain")
        except (ValueError, KeyError) as e:
            self._json({"fehler": str(e) or type(e).__name__}, 400)
        except Exception as e:
            self._json({"fehler": f"{type(e).__name__}: {str(e)[:200]}"}, 500)

    def do_DELETE(self):
        url = urllib.parse.urlparse(self.path)
        id_ = urllib.parse.parse_qs(url.query).get("id", [""])[0]
        if url.path == "/api/alarme":
            self._json({"geloescht": ALARME.loeschen(id_)})
        elif url.path == "/api/werkstatt":
            self._json({"geloescht": WERKSTATT.loeschen(id_)})
        elif url.path == "/api/pruefstand":
            p_ = PRUEFSTAND / (re.sub(r"[^a-z0-9]", "", id_) + ".json")
            ok = p_.exists()
            if ok:
                p_.unlink()
            self._json({"geloescht": ok})
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        frage = urllib.parse.parse_qs(url.query)
        f1 = lambda k, standard="": frage.get(k, [standard])[0]
        try:
            if url.path == "/ui.css":
                self._senden(200, UI_CSS.read_bytes(), "text/css; charset=utf-8")
                return
            if url.path == "/":
                self._senden(200, SEITE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/uebersicht":
                self._json(uebersicht())
            elif url.path == "/api/analyse":
                isin, tag = isin_von(f1("isin") or f1("wert")), f1("tag") or (ROH.tage() or [""])[0]
                if not isin or tag not in ROH.tage():
                    self._json({"fehler": "keine Rohdaten für diesen Tag"}, 404)
                    return
                a = analyse(isin, tag, parameter(frage))
                self._json(kurz.kurzfassung(a, f1("teil", "alle")) if f1("kurz") else a)
            elif url.path == "/api/kerzen":
                tf = f1("tf", "15m")
                if tf not in TF:
                    self._json({"fehler": "Zeitrahmen unbekannt"}, 400)
                    return
                isin = isin_von(f1("wert"))
                k = lesen.kerzen(isin, tf, min(5000, max(5, int(f1("anzahl", "200")))))
                self._json({"wert": isin, "tf": tf, "kerzen": k, "letzter": lesen.letzter_kurs(isin)})
            elif url.path == "/api/orderbuch/claude":
                self._json(buchlage.lesen(f1("wert")) or {})
            elif url.path == "/api/heatmap":                         # Liquiditäts-Heatmap für iC Charts (Xetra-Buch je Preisstufe)
                bis = int(float(f1("bis") or time.time()))
                von = int(float(f1("von") or bis - 6 * 3600))
                self._json(heatmap.heatmap(isin_von(f1("wert")), von, bis, int(float(f1("fach", "15"))), ROH))
            elif url.path == "/api/signale":                         # Signal-Lage aus dem Orderbuch (Reiter Orderbuch im Dashboard)
                bis = int(float(f1("bis"))) if f1("bis") else None
                von = int(float(f1("von"))) if f1("von") else None
                self._json(signale.lage(isin_von(f1("wert")), von, bis, min(60, max(1, int(f1("schritt", "5")))), tag=f1("tag") or None))
            elif url.path == "/api/datenstrom":                      # Xetra-Abschlüsse und Buch-Updates für den Datenstrom im Reiter Orderbuch
                ab = f1("ab", "0")
                self._json(signale.datenstrom(isin_von(f1("wert")), int(ab) if ab.isdigit() else 0))
            elif url.path == "/api/signale/statistik":
                self._json(signale.statistik(isin_von(f1("wert")) if f1("wert") else None))
            elif url.path == "/api/signale/journal":
                self._json(signale.journal(isin_von(f1("wert")) if f1("wert") else None, min(2000, max(1, int(f1("limit", "200"))))))
            elif url.path == "/api/formationen":
                tf = f1("tf", "15m")
                staerke = min(20, max(2, int(f1("staerke", "5"))))
                if tf not in TF:
                    self._json({"fehler": "Zeitrahmen unbekannt"}, 400)
                    return
                self._json(formationen(f1("wert"), tf, staerke, mit_kerzen=not f1("ohne_kerzen")))
            elif url.path == "/api/werte":
                self._json({"werte": watchlist(), "arten": ARTEN, "zeitrahmen": list(TF)})
            elif url.path == "/api/alarme":
                self._json({"regeln": ALARME.liste(), "verlauf": ALARME.verlauf(), "telegram": telegram_stand(),
                            "geprueft": ALARME.geprueft, "fehler": ALARME.fehler, "ki": notizen.ki_lesen(), "ruhe": ruhe_stand()})
            elif url.path == "/api/werkstatt":
                self._json(WERKSTATT.uebersicht())
            elif url.path == "/api/werkstatt/analyse":
                a = WERKSTATT.lesen(f1("id"))
                self._json(a if a else {"fehler": "unbekannte Analyse"}, 200 if a else 404)
            elif url.path == "/api/werkstatt/ergebnis":
                self._json(WERKSTATT.ergebnis(f1("id"), f1("wert"), f1("tag") or (ROH.tage() or [""])[0], neu=bool(f1("neu"))))
            elif url.path == "/api/werkstatt/referenz":
                self._senden(200, REFERENZ.encode(), "text/markdown; charset=utf-8")
            elif url.path == "/api/dossier":
                self._json(notizen.dossier_lesen(f1("wert")))
            elif url.path == "/api/dossiers":
                self._json({"dossiers": notizen.dossiers()})
            elif url.path == "/api/journal":
                self._json(notizen.journal_bilanz(int(f1("n", "30"))))
            elif url.path == "/api/notizen":
                self._json({"notizen": notizen.notizen_lesen(int(f1("n", "10")), f1("art") or None)})
            elif url.path == "/api/ki":
                self._json(notizen.ki_lesen())
            elif url.path == "/api/pruefstand":                    # Studien (schreibt der Agenten-Server)
                if f1("id"):
                    p_ = PRUEFSTAND / (re.sub(r"[^a-z0-9]", "", f1("id")) + ".json")
                    self._json(json.loads(p_.read_text()) if p_.exists() else {"fehler": "unbekannt"}, 200 if p_.exists() else 404)
                else:
                    studien = []
                    for p_ in PRUEFSTAND.glob("*.json") if PRUEFSTAND.exists() else []:
                        try:
                            x = json.loads(p_.read_text())
                        except ValueError:
                            continue
                        e = x.get("ergebnis") or {}
                        studien.append({k: x.get(k) for k in ("id", "titel", "text", "status", "versuch", "familie", "erstellt", "geprueft", "fehler")} |
                                       {"urteil": e.get("urteil"), "gesamt": e.get("gesamt")})
                    studien.sort(key=lambda x: x.get("erstellt") or "", reverse=True)
                    self._json({"studien": studien})
            elif url.path == "/api/pruefstein":
                d = pruefstein.lesen_ergebnis(mit_ereignissen=not f1("ohne_ereignisse"))
                if d and "ereignisse" in d:
                    if f1("art"):                                   # Fälle einer Formation: art|richtung
                        art, _, richtung = f1("art").partition("|")
                        d["ereignisse"] = [e for e in d["ereignisse"] if e["art"] == art and (not richtung or e["richtung"] == richtung)]
                    d["ereignisse"] = d["ereignisse"][:int(f1("n", "400"))]
                self._json({"ergebnis": d, "laeuft": pruefstein.LAEUFT})
            elif url.path == "/api/charts":
                self._json(charts.uebersicht(neu=bool(f1("neu"))))
            else:
                self._senden(404, b"nicht gefunden", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, KeyError) as e:
            self._json({"fehler": str(e) or type(e).__name__}, 400)
        except Exception as e:
            self._json({"fehler": f"{type(e).__name__}: {str(e)[:200]}"}, 500)

    def log_message(self, *_):
        pass


def starten(port: int = ports.ANALYSEN, hosts: str = "127.0.0.1"):
    global ALARME, WERKSTATT
    ALARME = Alarme()
    ALARME.starten()
    WERKSTATT = Werkstatt()

    def pruefstein_taeglich():                             # einmal am Tag neu, sobald neue Tageskerzen da sind
        while True:
            try:
                alt = pruefstein.DATEI.stat().st_mtime if pruefstein.DATEI.exists() else 0
                if time.time() - alt > 20 * 3600:
                    pruefstein.rechnen()
            except Exception as e:
                pruefstein.LAEUFT["fehler"] = f"{type(e).__name__}: {str(e)[:200]}"
            time.sleep(3600)
    threading.Thread(target=pruefstein_taeglich, daemon=True, name="pruefstein").start()
    signale.starten()                                      # Krypto: Signale alle 10 s ins Journal, Markouts nachtragen
    ThreadingHTTPServer.daemon_threads = True
    server = [ThreadingHTTPServer((h.strip(), port), Handler) for h in hosts.split(",") if h.strip()]
    for s in server:
        print(f"Analysen: http://{s.server_address[0]}:{port}/", flush=True)
        threading.Thread(target=s.serve_forever, daemon=True).start()
    threading.Event().wait()
