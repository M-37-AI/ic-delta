"""Einrichtung für Nutzer des Repos: Einstellungsdatei, Schlüssel, Watchlist-Vorlagen, Herzschlag und Selbsttest.

Die Einrichtungsseite (/einrichtung) erscheint beim ersten Start, solange es keine Einstellungsdatei gibt, und bleibt
danach als Übersicht „Einrichtung und Datenquellen“ erreichbar. Zuerst laufen die Quellen ohne Schlüssel, danach
lassen sich Alpaca, EODHD, Telegram und der KI-Analyst mit eigenem Konto freischalten.

Einstellungen: ~/.config/marktdaten/einstellungen.toml (anders per MARKTDATEN_KONFIG), lesbar und von Hand änderbar.
Schlüssel: dieselben Dateien, aus denen die Quellen lesen, mit Rechten nur für den Nutzer (0600). Die Seite schreibt
sie, liest sie aber nie zurück: Antworten nennen nur „vorhanden“ und das Ergebnis der Prüfung, nie einen Wert, und
Fehlermeldungen enthalten keine URL (EODHD und Telegram tragen den Schlüssel in der URL).

Herzschlag: jede Minute ein Eintrag in der Tabelle herzschlag; daraus Online-Status und Lücken (Dienst lief nicht,
z. B. Rechner im Ruhezustand).
"""
from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import ports
from .live import watchlist
from .netz import USER_AGENT
from .quellen import PROFIL, QUELLEN
from .quellen import eodhd

UTC = timezone.utc
DATEI = ports.KONFIG / "einstellungen.toml"
SEITE = pathlib.Path(__file__).parent / "dashboard" / "einrichtung.html"
DEMO_LEISTE = pathlib.Path(__file__).parent / "dashboard" / "demo-leiste.js"
# Demo im Terminal (python -m marktdaten demo): Nachfrage nach FRAGE_S, ohne Antwort Ende nach weiteren FRAGE_S
DEMO_TERMINAL = ports.DEMO and os.environ.get("MARKTDATEN_DEMO_TERMINAL") == "1"
FRAGE_S = float(os.environ.get("MARKTDATEN_DEMO_FRAGE_S", "600"))
_demo = {"start": float(os.environ.get("MARKTDATEN_DEMO_START") or time.time())}
_demo["frage_ab"] = _demo["start"] + FRAGE_S
LUECKE_S = 180                                     # mehr als drei Minuten ohne Herzschlag gilt als Lücke

BETRIEBSARTEN = ("server", "miete", "pc")
ZUGRIFF = ("geraet", "heimnetz", "unterwegs")
VORSCHLAG = {"server": {"orderbuch": True}, "miete": {"orderbuch": False}, "pc": {"orderbuch": False}}

# Quellen, die der Sammler ohne Schlüssel laufend abruft (die übrigen aus QUELLEN stehen für Analysen bereit)
GESAMMELT = ("xetra", "tradegate", "cboe")
BINANCE = {"name": "binance", "titel": "Binance (Krypto)", "gruppe": "Krypto", "verzug": "live", "nutzung": "öffentliche Schnittstelle",
           "inhalt": "Kurse, Abschlüsse und Orderbuch bis 1.000 Stufen", "tiefe": "bis 1.000 Stufen",
           "lizenz": "öffentliche Marktdaten-Schnittstelle; es gelten die Nutzungsbedingungen von Binance",
           "info_url": "https://www.binance.com/de/terms"}

VORLAGEN = {
    "dax": {"titel": "Deutschland", "text": "5 große DAX-Werte, Xetra mit Orderbuch",
            "werte": [w for w in watchlist.START if w[3] == "DE"]},
    "krypto": {"titel": "Krypto", "text": "Bitcoin, Ether und Solana, live von Binance",
               "werte": [("BTCUSDT", "BTCUSDT", "BTC / USDT", "KRYPTO"), ("ETHUSDT", "ETHUSDT", "ETH / USDT", "KRYPTO"),
                         ("SOLUSDT", "SOLUSDT", "SOL / USDT", "KRYPTO")]},
    "usa": {"titel": "USA", "text": "5 große US-Werte, braucht Alpaca", "schluessel": "alpaca",
            "werte": [w for w in watchlist.START if w[3] == "US"]},
}


def _env_pfad(variable: str, standard: str) -> pathlib.Path:
    return pathlib.Path(os.environ.get(variable, standard)).expanduser()


# Freischaltbare Quellen. felder: Variablen in der Datei; die Datei ist dieselbe, aus der der jeweilige Teil liest.
SCHLUESSEL = {
    "alpaca": {"titel": "Alpaca", "was": "US-Aktien live (IEX) und Historie aller US-Börsen",
               "ohne": "US-Werte bleiben ohne Kurse", "tarif": "kostenlos; live nur IEX, höchstens 30 US-Werte",
               "anmeldung": "https://app.alpaca.markets/signup", "info_url": "https://alpaca.markets/data",
               "anleitung": "Konto anlegen, im Dashboard „API Keys“ erzeugen, Key ID und Secret hier eintragen.",
               "felder": [("APCA_API_KEY_ID", "Key ID"), ("APCA_API_SECRET_KEY", "Secret Key")],
               "datei": lambda: _env_pfad("MARKTDATEN_ALPACA_ENV", "~/.config/marktdaten/alpaca.env"),
               "lizenz": QUELLEN["alpaca"].lizenz},
    "eodhd": {"titel": "EODHD", "was": "ein Jahr Tageskerzen und Splits für deutsche Aktien",
              "ohne": "neue deutsche Werte haben nur die selbst gesammelten Tage",
              "tarif": "kostenlos; 20 Abrufe am Tag, letztes Jahr, nur privat",
              "anmeldung": "https://eodhd.com/register", "info_url": "https://eodhd.com/pricing",
              "anleitung": "Konto anlegen, im Nutzerbereich den API-Token kopieren und hier eintragen.",
              "felder": [("EODHD_API_TOKEN", "API-Token")], "datei": eodhd._datei, "lizenz": QUELLEN["eodhd"].lizenz},
    "telegram": {"titel": "Telegram", "was": "Alarme und Meldungen aufs Handy, Fragen an Claude im Chat",
                 "ohne": "keine Benachrichtigungen", "tarif": "kostenlos",
                 "anmeldung": "https://t.me/BotFather", "info_url": "https://core.telegram.org/bots",
                 "anleitung": "In Telegram @BotFather öffnen, /newbot senden, Namen wählen und den Token hier eintragen.",
                 "felder": [("TELEGRAM_TOKEN", "Bot-Token")], "datei": lambda: ports.KONFIG / "telegram.env",
                 "lizenz": "eigener Bot; Nachrichten laufen über die Server von Telegram"},
    "ki": {"titel": "KI-Analyst", "was": "Claude wertet Charts, Orderbuch und Formationen aus und schreibt ins Journal",
           "ohne": "keine KI-Auswertungen; Charts, Analysen und Alarme laufen trotzdem",
           "tarif": "Claude-Code-Abo auf diesem Rechner oder OpenRouter mit Guthaben",
           "anmeldung": "https://openrouter.ai/keys", "info_url": "https://openrouter.ai/docs",
           "anleitung": "Mit Claude Code (Abo) ist nichts einzutragen: einmal „claude“ im Terminal starten und anmelden. "
                        "Ohne Abo: bei OpenRouter einen Schlüssel erzeugen und hier eintragen.",
           "felder": [("OPENROUTER_KEY", "OpenRouter-Schlüssel")],
           "datei": lambda: _env_pfad("MARKTDATEN_KI_ENV", "~/.config/marktdaten/ki.env"),
           "lizenz": "Die Deutsche Börse verlangt laut Market Data Policy für maschinelle Auswertung (Non-Display) eine "
                     "eigene Lizenz; ob das für rein private Nutzung gilt, klärt nur die Deutsche Börse."},
}

_sperre = threading.Lock()
_geprueft: dict[str, dict] = {}                    # Ergebnis der letzten Prüfung je Quelle (nur ok/Text)
_demo_schluessel: set[str] = set()                 # Demo: „eingetragene“ Schlüssel nur im Arbeitsspeicher
_demo_einstellungen: dict = {}                     # Demo: Auswahl der Seite nur im Arbeitsspeicher
_mtime = {"t": None, "werte": {}}
VERBINDUNG: dict = {}                              # vom Server: zustand, speicher, aufnehmen, nach_schluessel, hosts


# --- Einstellungsdatei -------------------------------------------------------------------------
def laden() -> dict:
    try:
        t = DATEI.stat().st_mtime
    except OSError:
        return {}
    if _mtime["t"] != t:
        try:
            _mtime.update(t=t, werte=tomllib.loads(DATEI.read_text()))
        except (OSError, tomllib.TOMLDecodeError):
            _mtime.update(t=t, werte={})
    return dict(_mtime["werte"])


def _toml(wert) -> str:
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, (int, float)):
        return repr(wert)
    if isinstance(wert, list):
        return "[" + ", ".join(_toml(x) for x in wert) + "]"
    return json.dumps(str(wert), ensure_ascii=False)


KOMMENTARE = {"betriebsart": "server | miete | pc", "zugriff": "geraet | heimnetz | unterwegs",
              "orderbuch": "Xetra-, Tradegate- und Cboe-Orderbuch sammeln (10 bis 15 GB Download je Handelstag)"}


def speichern(**neu) -> dict:
    with _sperre:
        werte = laden() | {k: v for k, v in neu.items() if v is not None}
        zeilen = ["# iC Delta: Einstellungen. Geschrieben von der Einrichtung (/einrichtung), von Hand änderbar.", ""]
        for k, v in werte.items():
            zeilen.append(f"{k} = {_toml(v)}" + (f"   # {KOMMENTARE[k]}" if k in KOMMENTARE else ""))
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        DATEI.write_text("\n".join(zeilen) + "\n")
        return laden()


def offen() -> bool:
    """True, solange die Einrichtung noch nicht abgeschlossen ist (nie im Demo-Betrieb)."""
    return not ports.DEMO and not laden().get("eingerichtet")


def orderbuch() -> bool:
    """Ob der Sammler Orderbücher (pretrade) lädt; ohne Einstellung wie bisher: ja."""
    return bool(laden().get("orderbuch", True))


def bestand_uebernehmen(speicher) -> None:
    """Bestehende Installation (Watchlist vorhanden, keine Einstellungsdatei): gilt als eingerichtet."""
    if not ports.DEMO and not DATEI.exists() and speicher.watchlist():
        speichern(eingerichtet=_jetzt(), uebernommen=True, betriebsart="server", orderbuch=True)


def _jetzt() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --- Schlüssel ---------------------------------------------------------------------------------
def _env_lesen(pfad: pathlib.Path) -> dict[str, str]:
    """Nur für den Gebrauch im Dienst selbst (Prüfen, Ergänzen); Werte verlassen diese Datei nie."""
    werte = {}
    try:
        for z in pfad.read_text().splitlines():
            if "=" in z and not z.lstrip().startswith("#"):
                k, v = z.split("=", 1)
                werte[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return werte


def _env_schreiben(pfad: pathlib.Path, setzen: dict[str, str], entfernen: tuple = ()) -> None:
    """Variablen setzen, andere Zeilen behalten; Datei nur für den Nutzer lesbar."""
    zeilen, gesetzt = [], set()
    try:
        alt = pfad.read_text().splitlines()
    except OSError:
        alt = []
    for z in alt:
        k = z.split("=", 1)[0].strip() if "=" in z and not z.lstrip().startswith("#") else None
        if k in entfernen:
            continue
        if k in setzen:
            zeilen.append(f"{k}={setzen[k]}")
            gesetzt.add(k)
        else:
            zeilen.append(z)
    zeilen += [f"{k}={v}" for k, v in setzen.items() if k not in gesetzt]
    pfad.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(zeilen).strip("\n") + "\n")
    os.chmod(pfad, 0o600)


def vorhanden(name: str) -> bool:
    if ports.DEMO and name != "telegram":
        return name in _demo_schluessel
    s = SCHLUESSEL[name]
    if name == "alpaca" and os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"):
        return True
    if any(os.environ.get(k) for k, _ in s["felder"]) and name != "alpaca":
        return True
    w = _env_lesen(s["datei"]())
    return all(w.get(k) for k, _ in s["felder"])


def _anfrage(url: str, kopf: dict | None = None, daten: dict | None = None, timeout: int = 15) -> tuple[int, dict]:
    """(HTTP-Status, JSON) ohne URL in Fehlern; 0 = nicht erreichbar."""
    h = {"User-Agent": USER_AGENT, **(kopf or {})}
    if daten is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(daten).encode() if daten is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


def _werte(name: str) -> dict[str, str]:
    s = SCHLUESSEL[name]
    w = _env_lesen(s["datei"]())
    for k, _ in s["felder"]:
        if os.environ.get(k):
            w[k] = os.environ[k]
    return w


def pruefen(name: str) -> dict:
    """Verbindung mit dem eingetragenen Schlüssel prüfen. Antwort: {ok, text}; nie ein Schlüsselwert."""
    if name not in SCHLUESSEL:
        return {"ok": False, "text": "unbekannte Quelle"}
    if ports.DEMO and name != "telegram":
        erg = {"ok": name in _demo_schluessel, "text": "Demo: verbunden (nichts abgerufen)" if name in _demo_schluessel else "nicht eingetragen"}
    elif not vorhanden(name):
        erg = {"ok": False, "text": "noch kein Schlüssel eingetragen"}
    else:
        w = _werte(name)
        if name == "alpaca":                  # kleinste Datenanfrage: letzte Minutenkerze von SPY aus dem IEX-Feed
            code, _ = _anfrage("https://data.alpaca.markets/v2/stocks/bars/latest?symbols=SPY&feed=iex",
                               {"APCA-API-KEY-ID": w.get("APCA_API_KEY_ID", ""), "APCA-API-SECRET-KEY": w.get("APCA_API_SECRET_KEY", "")})
            erg = _bewerten(code, "verbunden")
        elif name == "eodhd":                 # Kontoabfrage: zählt nicht zu den 20 Datenabrufen
            code, d = _anfrage("https://eodhd.com/api/user?" + urllib.parse.urlencode({"api_token": w.get("EODHD_API_TOKEN", ""), "fmt": "json"}))
            erg = _bewerten(code, "verbunden" + (f" · {d.get('apiRequests', 0)} von {d.get('dailyRateLimit', '?')} Abrufen heute"
                                                 if isinstance(d, dict) and d.get("dailyRateLimit") else ""))
        elif name == "telegram":
            code, d = _anfrage(f"https://api.telegram.org/bot{w.get('TELEGRAM_TOKEN', '')}/getMe")
            bot = (d.get("result") or {}).get("username") if isinstance(d, dict) else None
            erg = _bewerten(code, f"Bot @{bot} gefunden" + ("" if w.get("TELEGRAM_CHAT") else " · Chat fehlt noch"))
            if erg["ok"]:
                erg.update(bot=bot, chat=bool(w.get("TELEGRAM_CHAT")))
        else:                                 # OpenRouter: Angaben zum Schlüssel, kein Modellaufruf
            code, d = _anfrage("https://openrouter.ai/api/v1/key", {"Authorization": "Bearer " + w.get("OPENROUTER_KEY", "")})
            erg = _bewerten(code, "verbunden")
    erg["zeit"] = _jetzt()
    _geprueft[name] = erg
    return erg


def _bewerten(code: int, gut: str) -> dict:
    if code == 200:
        return {"ok": True, "text": gut}
    if code in (401, 403):
        return {"ok": False, "text": "Schlüssel abgelehnt – bitte prüfen und neu eintragen"}
    if code == 429:
        return {"ok": False, "text": "zu viele Anfragen, später erneut prüfen"}
    if code == 0:
        return {"ok": False, "text": "Dienst nicht erreichbar (Internet?)"}
    return {"ok": False, "text": f"unerwartete Antwort ({code})"}


def schluessel_setzen(name: str, werte: dict) -> dict:
    """Schlüssel speichern, sofort prüfen und den passenden Sammler anstoßen."""
    if name not in SCHLUESSEL:
        return {"ok": False, "text": "unbekannte Quelle"}
    s = SCHLUESSEL[name]
    neu = {k: str(werte.get(k, "")).strip() for k, _ in s["felder"]}
    if not all(neu.values()) or any(len(v) > 400 or any(c in v for c in "\n\r= ") for v in neu.values()):
        return {"ok": False, "text": "Bitte alle Felder ausfüllen (ohne Leerzeichen)"}
    if ports.DEMO and name != "telegram":
        _demo_schluessel.add(name)           # Demo: nichts schreiben, die Dateien gehören dem echten Betrieb
    else:
        _env_schreiben(s["datei"](), neu, entfernen=("TELEGRAM_CHAT",) if name == "telegram" else ())
    erg = pruefen(name)
    if erg["ok"] and VERBINDUNG.get("nach_schluessel"):
        try:
            VERBINDUNG["nach_schluessel"](name)
        except Exception as e:               # Anzeige statt Absturz
            erg["text"] += f" · Sammler: {type(e).__name__}"
    return erg


def schluessel_entfernen(name: str) -> dict:
    if name not in SCHLUESSEL:
        return {"ok": False, "text": "unbekannte Quelle"}
    if ports.DEMO and name != "telegram":
        _demo_schluessel.discard(name)
    else:
        _env_schreiben(SCHLUESSEL[name]["datei"](), {}, entfernen=tuple(k for k, _ in SCHLUESSEL[name]["felder"]) + (
            ("TELEGRAM_CHAT",) if name == "telegram" else ()))
    _geprueft.pop(name, None)
    return {"ok": True, "text": "entfernt"}


# --- Telegram: Chat finden und Testnachricht ------------------------------------------------------
def telegram_chat() -> dict:
    """Nach der ersten Nachricht an den Bot: Chat-ID per getUpdates ermitteln, speichern, Testnachricht schicken."""
    pfad = SCHLUESSEL["telegram"]["datei"]()
    w = _env_lesen(pfad)
    if not w.get("TELEGRAM_TOKEN"):
        return {"ok": False, "text": "zuerst den Bot-Token eintragen"}
    basis = f"https://api.telegram.org/bot{w['TELEGRAM_TOKEN']}/"
    if not w.get("TELEGRAM_CHAT"):
        code, d = _anfrage(basis + "getUpdates")
        if code != 200:
            return _bewerten(code, "")
        chats = [u["message"]["chat"]["id"] for u in d.get("result", []) if "message" in u]
        if not chats:
            return {"ok": False, "text": "Noch keine Nachricht gefunden. Schreib dem Bot in Telegram /start und versuch es erneut."}
        _env_schreiben(pfad, {"TELEGRAM_CHAT": str(chats[-1])})
        w["TELEGRAM_CHAT"] = str(chats[-1])
    code, _ = _anfrage(basis + "sendMessage", daten={"chat_id": w["TELEGRAM_CHAT"], "parse_mode": "HTML",
                                                     "text": "✅ <b>iC Delta</b>: Telegram ist eingerichtet. Alarme kommen ab jetzt hier an."})
    erg = _bewerten(code, "Chat gespeichert, Testnachricht gesendet")
    if erg["ok"]:
        _geprueft["telegram"] = {**erg, "chat": True, "zeit": _jetzt()}
    return erg


# --- Herzschlag --------------------------------------------------------------------------------
class Herzschlag(threading.Thread):
    def __init__(self, speicher):
        super().__init__(daemon=True, name="herzschlag")
        self.sp = speicher
        self.sp._x("CREATE TABLE IF NOT EXISTS herzschlag (t INTEGER PRIMARY KEY)")

    def schlag(self, jetzt: float | None = None) -> None:
        t = int(jetzt or time.time()) // 60 * 60
        self.sp._x("INSERT OR IGNORE INTO herzschlag VALUES (?)", (t,))

    def run(self) -> None:
        while True:
            self.schlag()
            if DEMO_TERMINAL:
                demo_stand()                             # Demo ohne Antwort auf die Nachfrage beenden
            self.sp._x("DELETE FROM herzschlag WHERE t < ?", (int(time.time()) - 90 * 86400,))
            time.sleep(60 - time.time() % 60 + 1)


def online(speicher, tage: int = 7, jetzt: float | None = None) -> dict:
    """Online-Status aus dem Herzschlag: letzter Schlag, Laufzeit seit der letzten Lücke, Lücken der letzten Tage."""
    jetzt = jetzt or time.time()
    try:
        t = [r[0] for r in speicher._x("SELECT t FROM herzschlag WHERE t >= ? ORDER BY t", (int(jetzt) - tage * 86400,))]
    except Exception:
        t = []
    if not t:
        return {"letzter": None, "seit": None, "luecken": [], "anteil": None, "erster": None}
    luecken = [{"von": a + 60, "bis": b, "minuten": round((b - a - 60) / 60)} for a, b in zip(t, t[1:]) if b - a > LUECKE_S]
    seit = luecken[-1]["bis"] if luecken else t[0]
    spanne = max(60, jetzt - t[0])
    return {"letzter": t[-1], "seit": seit, "erster": t[0], "luecken": luecken[-20:],
            "anteil": round(100 * min(1, len(t) * 60 / spanne), 1), "steht": jetzt - t[-1] > LUECKE_S}


# --- Watchlist-Vorlagen ------------------------------------------------------------------------------
def vorlagen_json() -> list[dict]:
    z = VERBINDUNG.get("zustand")
    da = set(z.werte) if z else set()
    return [{"name": k, "titel": v["titel"], "text": v["text"], "schluessel": v.get("schluessel"),
             "frei": not v.get("schluessel") or vorhanden(v["schluessel"]),
             "werte": [{"id": w[0], "symbol": w[1], "name": w[2], "markt": w[3], "da": w[0] in da} for w in v["werte"]]}
            for k, v in VORLAGEN.items()]


def vorlagen_uebernehmen(namen: list[str]) -> dict:
    werte = []
    for n in namen:
        v = VORLAGEN.get(n)
        if v and (not v.get("schluessel") or vorhanden(v["schluessel"])):
            werte += v["werte"]
    neu = VERBINDUNG["aufnehmen"](werte) if werte and VERBINDUNG.get("aufnehmen") else []
    return {"ok": True, "neu": neu, "text": f"{len(neu)} Werte aufgenommen" if neu else "alle Werte standen schon auf der Watchlist"}


# --- Speicher, Autostart, Selbsttest ---------------------------------------------------------------------
def speicher_vorschlag(betriebsart: str, frei: int) -> float:
    gb = frei / 1e9
    if betriebsart == "pc":
        return float(max(1, min(5, round(gb * 0.05))))
    return float(max(2, min(100 if betriebsart == "server" else 25, round(gb * 0.25))))


def autostart() -> dict:
    """Ob ein Hintergrunddienst mit Autostart eingerichtet ist (nur nachsehen, nichts ändern)."""
    system = platform.system()
    if system == "Darwin":
        ordner = pathlib.Path("~/Library/LaunchAgents").expanduser()
        for p in sorted(ordner.glob("*.plist")) if ordner.exists() else []:
            try:
                t = p.read_text(errors="ignore")
            except OSError:
                continue
            if "marktdaten" in t and "dashboard" in t and str(ports.DASHBOARD) in t:
                return {"ok": "RunAtLoad" in t, "text": f"launchd: {p.name}" + ("" if "RunAtLoad" in t else " (ohne Autostart)")}
        return {"ok": False, "text": "kein launchd-Dienst gefunden – einrichten.sh legt ihn an"}
    if system == "Linux":
        ordner = pathlib.Path("~/.config/systemd/user").expanduser()
        treffer = [p for p in ordner.glob("*.service") if "marktdaten" in p.read_text(errors="ignore")] if ordner.exists() else []
        return {"ok": bool(treffer), "text": f"systemd: {treffer[0].name}" if treffer else "kein systemd-Dienst gefunden"}
    return {"ok": None, "text": f"{system}: Autostart nicht geprüft"}


def selbsttest() -> dict:
    from . import status
    sp = VERBINDUNG["speicher"]
    z = VERBINDUNG.get("zustand")
    punkte = []
    on = online(sp)
    punkte.append({"was": "Dienst läuft", "ok": bool(on["letzter"]) and not on.get("steht"),
                   "text": f"Herzschlag zuletzt {datetime.fromtimestamp(on['letzter']).strftime('%H:%M')} Uhr" if on["letzter"] else "noch kein Herzschlag"})
    punkte.append({"was": "Autostart", **autostart()})
    def quelle(name: str) -> dict:
        if name == "binance":
            code, _ = _anfrage("https://api.binance.com/api/v3/ping")
            return {"was": BINANCE["titel"], "ok": code == 200, "text": "erreichbar" if code == 200 else "nicht erreichbar"}
        q = status.pruefen(name)
        return {"was": QUELLEN[name].titel, "ok": q.get("zustand") == "erreichbar",
                "text": "erreichbar" if q.get("zustand") == "erreichbar" else q.get("hinweis", "Fehler")}

    def freigeschaltet(name: str) -> dict:
        if not vorhanden(name):
            return {"was": SCHLUESSEL[name]["titel"], "ok": None, "text": "nicht freigeschaltet (optional)"}
        p = pruefen(name)
        return {"was": SCHLUESSEL[name]["titel"], "ok": p["ok"], "text": p["text"]}

    with ThreadPoolExecutor(8) as ex:                    # Prüfungen parallel, damit der Test nur Sekunden dauert
        quellen = [] if ports.DEMO else list(ex.map(quelle, (*GESAMMELT, "binance")))
        freie = list(ex.map(freigeschaltet, SCHLUESSEL))
    if ports.DEMO:
        quellen = [{"was": "Quellen", "ok": True, "text": "Demo: erfundene Daten, keine Abrufe bei Börsen"}]
    punkte += quellen + freie
    if z:
        with z.lock:
            mit = sum(1 for w in z.werte.values() if w.letzter)
            n = len(z.werte)
        punkte.append({"was": "Watchlist", "ok": n > 0, "text": f"{n} Werte, {mit} davon mit Kurs" if n else "leer"})
    g = sp.groesse()
    grenze = g["einstellungen"]["obergrenze_gb"] * 1e9
    punkte.append({"was": "Speicher", "ok": g["frei"] > grenze - g["belegt"],
                   "text": f"{g['belegt'] / 1e9:.1f} von {grenze / 1e9:g} GB belegt, {g['frei'] / 1e9:.0f} GB frei auf dem Laufwerk".replace(".", ",")})
    return {"punkte": punkte, "zeit": _jetzt()}


# --- Ausgabe für die Seite --------------------------------------------------------------------------
def stand() -> dict:
    sp = VERBINDUNG.get("speicher")
    e = laden()
    quellen = []
    for name in GESAMMELT:
        q = QUELLEN[name]
        gruppe, verzug, nutzung = PROFIL[name]
        quellen.append({"name": name, "titel": q.titel, "gruppe": gruppe, "verzug": verzug, "nutzung": nutzung,
                        "inhalt": q.inhalt, "tiefe": q.tiefe, "lizenz": q.lizenz, "info_url": q.info_url})
    quellen.append(BINANCE)
    weitere = [{"name": n, "titel": q.titel, "nutzung": PROFIL[n][2], "lizenz": q.lizenz, "info_url": q.info_url}
               for n, q in QUELLEN.items() if n not in GESAMMELT and not q.schluessel]
    schluessel = [{"name": n, **{k: v for k, v in s.items() if k not in ("datei", "felder")},
                   "felder": [{"name": k, "titel": t} for k, t in s["felder"]],
                   "vorhanden": vorhanden(n), "geprueft": _geprueft.get(n),
                   "datei": "nur im Arbeitsspeicher (Demo)" if ports.DEMO and n != "telegram" else str(s["datei"]()).replace(str(pathlib.Path.home()), "~")}
                  for n, s in SCHLUESSEL.items()]
    frei = shutil.disk_usage(sp.datei.parent).free if sp else 0
    return {"einstellungen": e | _demo_einstellungen if ports.DEMO else e, "offen": offen(), "demo": ports.DEMO, "datei": str(DATEI).replace(str(pathlib.Path.home()), "~"),
            "quellen": quellen, "weitere": weitere, "schluessel": schluessel, "vorlagen": vorlagen_json(),
            "hosts": VERBINDUNG.get("hosts", []), "port": ports.DASHBOARD,
            "gegenstueck": {"port": ports.GEGENSTUECK, "laeuft": demo_laeuft()}, "system": platform.system(),
            "speicher": {"frei": frei, "obergrenze_gb": sp.einstellungen()["obergrenze_gb"] if sp else None,
                         "vorschlag": {b: speicher_vorschlag(b, frei) for b in BETRIEBSARTEN}},
            "online": online(sp) if sp else None}


# --- Demo im Terminal: stoppen, Nachfrage --------------------------------------------------------------
def _demo_ordner() -> pathlib.Path | None:
    sp = VERBINDUNG.get("speicher")
    return sp.datei.parent if sp else None


def demo_stand(jetzt: float | None = None) -> dict:
    if not DEMO_TERMINAL:
        return {"terminal": False}
    jetzt = jetzt or time.time()
    ordner = _demo_ordner()
    belegt = sum(f.stat().st_size for f in ordner.rglob("*") if f.is_file()) if ordner and ordner.exists() else 0
    if jetzt >= _demo["frage_ab"] + FRAGE_S:
        demo_stoppen()                                   # Nachfrage blieb unbeantwortet
    return {"terminal": True, "laeuft_s": round(jetzt - _demo["start"]), "belegt": belegt, "frage": jetzt >= _demo["frage_ab"],
            "frist_s": FRAGE_S, "gestoppt": _demo.get("gestoppt", False)}


def demo_weiter() -> dict:
    _demo["frage_ab"] = time.time() + FRAGE_S
    return demo_stand()


def demo_stoppen() -> dict:
    """Bittet den Startprozess (python -m marktdaten demo) per Datei, alle Dienste zu beenden und die Demo-Daten zu löschen."""
    if not DEMO_TERMINAL:
        return {"ok": False, "text": "nur für die Demo im Terminal"}
    ordner = _demo_ordner()
    if ordner:
        (ordner / ".stopp").write_text("loeschen")
    _demo["gestoppt"] = True
    return {"ok": True, "echt": ports.GEGENSTUECK if demo_laeuft() else None}


DEMO_LOG = pathlib.Path("~/.local/share/marktdaten-demo.log").expanduser()


def demo_starten() -> dict:
    """Aus dem echten Dashboard: Demo wie `python -m marktdaten demo` im Hintergrund starten (mit Stopp-Leiste und
    Nachfrage nach 10 Minuten). Läuft sie schon, nichts tun."""
    if ports.DEMO:
        return {"ok": False, "text": "läuft schon als Demo"}
    if demo_laeuft():
        return {"ok": True, "laeuft": True, "port": ports.GEGENSTUECK}
    p = _demo.get("prozess")
    if p and p.poll() is None:
        return {"ok": True, "laeuft": False, "port": ports.GEGENSTUECK, "text": "startet schon"}
    env = {k: v for k, v in os.environ.items() if k not in ("MARKTDATEN_PORT", "MARKTDATEN_DATEN", "MARKTDATEN_KONFIG", "MARKTDATEN_DEMO")}
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"])   # Node für den Agenten
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(pathlib.Path(__file__).resolve().parent.parent), env.get("PYTHONPATH")]))
    DEMO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DEMO_LOG, "ab") as log:
        _demo["prozess"] = subprocess.Popen(
            [sys.executable, "-m", "marktdaten", "demo", "--port", str(ports.GEGENSTUECK), "--host", ",".join(VERBINDUNG.get("hosts") or ["127.0.0.1"])],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)   # überlebt Neustarts des Dashboards
    return {"ok": True, "laeuft": False, "port": ports.GEGENSTUECK, "text": "startet"}


def demo_laeuft() -> bool:
    """Ob das Gegenstück (Demo bzw. echter Betrieb) auf diesem Rechner antwortet."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", ports.GEGENSTUECK)) == 0


def get(pfad: str) -> tuple[int, bytes, str] | None:
    if pfad == "/einrichtung":
        return 200, SEITE.read_bytes(), "text/html; charset=utf-8"
    if pfad == "/api/einrichtung":
        return 200, json.dumps(stand()).encode(), "application/json"
    if pfad == "/demo-leiste.js":
        return 200, DEMO_LEISTE.read_bytes(), "text/javascript; charset=utf-8"
    if pfad == "/api/einrichtung/demo":
        p = _demo.get("prozess")
        if p:
            p.poll()                                          # beendete Demo abräumen
        return 200, json.dumps(demo_stand() | {"gegenstueck": ports.GEGENSTUECK, "gegenstueck_laeuft": demo_laeuft(), "demo": ports.DEMO}).encode(), "application/json"
    if pfad == "/api/einrichtung/online":
        return 200, json.dumps(online(VERBINDUNG["speicher"])).encode(), "application/json"
    return None


def bestaetigt() -> bool:
    return bool((_demo_einstellungen if ports.DEMO else laden()).get("bedingungen"))


def post(pfad: str, d: dict) -> dict | None:
    if pfad == "/api/einrichtung/demo/stoppen":           # ohne Bestätigung erlaubt: die Demo muss sich immer beenden lassen
        return demo_stoppen()
    if pfad == "/api/einrichtung/demo/weiter":
        return demo_weiter()
    if pfad == "/api/einrichtung/demo/starten":           # auch vor der Bestätigung: Ausprobieren ohne echte Daten
        return demo_starten()
    if pfad != "/api/einrichtung" and pfad.startswith("/api/einrichtung/") and not bestaetigt():
        return {"ok": False, "fehler": "Bitte zuerst die Nutzungsbedingungen bestätigen", "text": "Bitte zuerst die Nutzungsbedingungen bestätigen"}
    if pfad == "/api/einrichtung":                      # Schritte speichern; fertig=True schließt die Einrichtung ab
        neu = {k: d[k] for k in ("bedingungen", "betriebsart", "zugriff", "orderbuch") if k in d}
        if neu.get("betriebsart") not in (None, *BETRIEBSARTEN) or neu.get("zugriff") not in (None, *ZUGRIFF):
            return {"fehler": "unbekannte Auswahl"}
        if "obergrenze_gb" in d and VERBINDUNG.get("speicher"):
            VERBINDUNG["speicher"].einstellen(obergrenze_gb=max(0.5, min(10000, float(d["obergrenze_gb"]))))
        if d.get("fertig"):
            if not (neu.get("bedingungen") or bestaetigt()):
                return {"fehler": "Bitte zuerst die Nutzungsbedingungen bestätigen"}
            neu["eingerichtet"] = laden().get("eingerichtet") or _jetzt()
        if ports.DEMO:
            _demo_einstellungen.update(neu)
            return {"ok": True, "demo": True, **stand()}  # Demo: Einstellungen nicht in eine Datei schreiben
        speichern(**neu)
        return {"ok": True, **stand()}
    if pfad == "/api/einrichtung/schluessel":
        return schluessel_setzen(str(d.get("quelle")), d.get("werte") or {})
    if pfad == "/api/einrichtung/schluessel/entfernen":
        return schluessel_entfernen(str(d.get("quelle")))
    if pfad == "/api/einrichtung/pruefen":
        return pruefen(str(d.get("quelle")))
    if pfad == "/api/einrichtung/telegram":
        return telegram_chat()
    if pfad == "/api/einrichtung/vorlagen":
        return vorlagen_uebernehmen([str(x) for x in d.get("namen") or []])
    if pfad == "/api/einrichtung/selbsttest":
        return selbsttest()
    return None
