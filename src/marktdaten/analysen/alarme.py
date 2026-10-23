"""Alarme: Regeln speichern, laufend gegen Kerzen und Buch prüfen und per Telegram melden.

Arten: Preis (Kurs kreuzt eine Marke), Linie (Schlusskurs bricht eine Trendlinie), Zone (Schlusskurs verlässt
eine Zone), Formation (eine erkannte Formation wird bestätigt) und Buch (Kennzahl des Orderbuchs je Minute über
oder unter einer Schwelle, n Minuten in Folge). Linien und Zonen kommen aus der Formationserkennung, von Hand
oder von Claude. Geprüft werden nur abgeschlossene Kerzen bzw. Minuten.

Zweitmeinung: Löst ein Alarm aus, geht die Meldung sofort raus; danach bittet der Dienst den Agenten-Server
(Claude, Port 8814) um eine kurze Einordnung aus Buch und Chart und schickt sie als zweite Nachricht hinterher.
Rückkanal: Antworten im Telegram-Chat gehen an Claude, die Antwort kommt zurück in den Chat.

Telegram: Bot-Token und Chat-ID stehen in ~/.config/marktdaten/telegram.env (TELEGRAM_TOKEN=..., TELEGRAM_CHAT=...).
Die Datei legt der Nutzer selbst an; Token und Adresse erscheinen nie in Meldungen oder Protokollen.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import ports
from . import lesen, notizen, signale
from .formationen import ARTEN, TF, erkennen

ORT = lesen.DATEN / "analysen"
REGELN = ORT / "alarme.json"
VERLAUF = ORT / "alarme_verlauf.jsonl"
TELEGRAM = ports.KONFIG / "telegram.env"
AGENT = ports.url(ports.AGENT)
RUHE = ORT / "telegram_ruhe.json"                 # Demo läuft: Meldungen zurückhalten
ZURUECK = ORT / "telegram_zurueckgehalten.jsonl"
BERLIN = ZoneInfo("Europe/Berlin")
QUELLE = {"DE": "Xetra, 15 Min. verzögert", "US": "IEX live", "KRYPTO": "Binance live", "ROHSTOFFE": "Binance Futures live"}
KENNZAHLEN = {"ungleich": ("Ungleichgewicht", 2, ""), "spread_bp": ("Spread", 1, " bp"), "tiefe_bid": ("Tiefe Kaufseite", 0, " €"),
              "tiefe_ask": ("Tiefe Verkaufsseite", 0, " €"), "updates": ("Buchänderungen je Minute", 0, "")}
HILFE = ("Ich bin Claude in iC Delta. Schreib mir eine Frage zu einem Wert der Watchlist, zum Orderbuch, zu Formationen oder Alarmen.\n"
         "/lage – Morgenlage jetzt\n/bilanz – Trefferquote der Einschätzungen\n/alarme – aktive Alarme\n/neu – neues Gespräch\n/hilfe – diese Hilfe")


# --- Telegram ---------------------------------------------------------------------------------------
def _telegram() -> dict:
    try:
        werte = {}
        for z in TELEGRAM.read_text().splitlines():
            if "=" in z and not z.lstrip().startswith("#"):
                k, v = z.split("=", 1)
                werte[k.strip()] = v.strip().strip('"').strip("'")
        return werte
    except OSError:
        return {}


def telegram_stand() -> dict:
    w = _telegram()
    return {"token": bool(w.get("TELEGRAM_TOKEN")), "chat": bool(w.get("TELEGRAM_CHAT")), "datei": str(TELEGRAM)}


def _api(methode: str, daten: dict | None = None, timeout: int = 20) -> dict:
    token = _telegram().get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Telegram nicht eingerichtet")
    anfrage = urllib.request.Request(f"https://api.telegram.org/bot{token}/{methode}",
                                     data=json.dumps(daten).encode() if daten is not None else None,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:                      # Text ohne URL: die URL enthält den Token
        try:
            beschreibung = json.load(e).get("description", "")
        except Exception:
            beschreibung = ""
        raise RuntimeError(f"Telegram antwortet {e.code} {beschreibung}".strip()) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram nicht erreichbar ({type(e.reason).__name__})") from None


BILD_NAME = re.compile(r"^icdelta-[0-9a-f-]{8,40}\.png$")


def _foto(chat: str, pfad: pathlib.Path, unterschrift: str = "") -> None:
    """Schickt ein Bild per sendPhoto (multipart). Der Token steht nur in der URL, nie in Fehlermeldungen."""
    token = _telegram().get("TELEGRAM_TOKEN")
    grenze = uuid.uuid4().hex
    teile = []
    for k, v in (("chat_id", str(chat)), ("caption", unterschrift[:1000]), ("parse_mode", "HTML")):
        if v:
            teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
                 + pfad.read_bytes() + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    anfrage = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(teile),
                                     headers={"Content-Type": f"multipart/form-data; boundary={grenze}"})
    try:
        with urllib.request.urlopen(anfrage, timeout=60) as antwort:
            json.load(antwort)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram antwortet {e.code} beim Bild") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram nicht erreichbar ({type(e.reason).__name__})") from None


def ruhe_stand() -> dict:
    try:
        bis = json.loads(RUHE.read_text())["bis"]
    except (OSError, ValueError, KeyError):
        return {"an": False}
    if bis < time.time():
        return {"an": False}
    n = len(ZURUECK.read_text().splitlines()) if ZURUECK.exists() else 0
    return {"an": True, "bis": bis, "zurueckgehalten": n}


def ruhe_setzen(an: bool, minuten: float = 180) -> dict:
    """Während der Demo kommen Alarme, Zweitmeinungen und Routinen nicht aufs Handy, sondern werden danach nachgereicht.
    Antworten auf eigene Fragen gehen weiter sofort raus. Ruhe endet spätestens nach minuten."""
    ORT.mkdir(parents=True, exist_ok=True)
    if an:
        RUHE.write_text(json.dumps({"bis": time.time() + min(max(minuten, 5), 600) * 60}))
    else:
        RUHE.unlink(missing_ok=True)
        nachreichen()
    return ruhe_stand()


def nachreichen() -> int:
    """Zurückgehaltene Meldungen senden, sobald die Ruhe vorbei ist."""
    if ruhe_stand()["an"] or not ZURUECK.exists():
        return 0
    zeilen = [json.loads(z) for z in ZURUECK.read_text().splitlines() if z.strip()]
    ZURUECK.unlink(missing_ok=True)
    for i, m in enumerate(zeilen):
        kopf = f"⏸ <i>Während der Demo zurückgehalten ({i + 1}/{len(zeilen)})</i>\n"
        telegram_senden(kopf + m["text"], m.get("bilder"), antwort=True)
    return len(zeilen)


def telegram_senden(text: str, bilder: list | None = None, antwort: bool = False) -> str | None:
    """Sendet Bilder (Chartbilder des Agenten, werden danach gelöscht) und dann den Text (HTML, bei Bedarf in Stücken).
    Rückgabe: None oder Fehlertext (ohne Token)."""
    chat = _telegram().get("TELEGRAM_CHAT")
    if not chat:
        return "Telegram nicht eingerichtet"
    if not antwort and ruhe_stand()["an"]:                     # Demo läuft: später nachreichen
        with open(ZURUECK, "a") as f:
            f.write(json.dumps({"text": text, "bilder": bilder or []}, ensure_ascii=False) + "\n")
        return None
    try:
        for b in bilder or []:
            pfad = pathlib.Path(b.get("pfad", "") if isinstance(b, dict) else str(b))
            if not BILD_NAME.match(pfad.name) or not pfad.is_file():
                continue                                   # nur Bilder des Agenten, keine beliebigen Dateien
            try:
                _foto(chat, pfad, html.escape(b.get("titel", "")) if isinstance(b, dict) else "")
            finally:
                pfad.unlink(missing_ok=True)
        for stueck in _stuecke(text):
            try:
                _api("sendMessage", {"chat_id": chat, "text": stueck, "parse_mode": "HTML", "disable_web_page_preview": True})
            except RuntimeError as e:
                if "400" not in str(e):
                    raise
                ohne = html.unescape(re.sub(r"<[^>]+>", "", stueck))     # Formatierung kaputt (z. B. Schnitt im Block): ohne senden
                _api("sendMessage", {"chat_id": chat, "text": ohne, "disable_web_page_preview": True})
        return None
    except Exception as e:
        return str(e)[:200]


def _stuecke(text: str, n: int = 3900) -> list[str]:
    aus, rest = [], text
    while len(rest) > n:
        schnitt = rest.rfind("\n", 0, n)
        schnitt = schnitt if schnitt > n // 2 else n
        aus.append(rest[:schnitt])
        rest = rest[schnitt:].lstrip("\n")
    return aus + [rest]


def _inline(z: str) -> str:
    z = html.escape(z, quote=False)
    z = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", z)
    z = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", z)
    z = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<i>\1</i>", z)
    return z


def markdown_zu_html(text: str) -> str:
    """Markdown von Claude nach Telegram-HTML: # Titel und ## Abschnitt fett, Listen als Punkte (eingerückt als ◦),
    > Zitat als Zitatblock, ::: Titel … ::: als aufklappbarer Block, **fett**, _kursiv_, `Code`, --- als Trennlinie."""
    aus: list[str] = []
    details: list[str] | None = None
    for zeile in str(text).replace("\r", "").split("\n"):
        s_ = zeile.strip()
        if s_.startswith(":::"):
            if details is None:
                details = [f"<b>{_inline(s_[3:].strip())}</b>"] if s_[3:].strip() else []
            else:
                aus.append("<blockquote expandable>" + "\n".join(details).strip() + "</blockquote>")
                details = None
            continue
        if s_.startswith("```"):
            continue
        if re.match(r"^#\s", s_):
            neu = f"<b>{_inline(s_[2:].strip())}</b>"
        elif re.match(r"^#{2,6}\s", s_):
            neu = f"\n<b>{_inline(s_.lstrip('#').strip())}</b>"
        elif s_.startswith(">"):
            neu = f"<blockquote>{_inline(s_.lstrip('>').strip())}</blockquote>"
        elif re.match(r"^\s*[-*•]\s+", zeile):
            einzug = len(zeile) - len(zeile.lstrip())
            neu = ("    ◦ " if einzug >= 2 else "• ") + _inline(re.sub(r"^\s*[-*•]\s+", "", zeile))
        elif s_ in ("---", "***", "___"):
            neu = "┈┈┈┈┈┈┈┈┈┈"
        else:
            neu = _inline(s_)
        (details if details is not None else aus).append(neu)
    if details is not None:
        aus.append("<blockquote expandable>" + "\n".join(details).strip() + "</blockquote>")
    t = "\n".join(aus).replace("</blockquote>\n<blockquote>", "\n")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def fusszeile(modell: str | None = None, kosten: float | None = None, quelle: str | None = None) -> str:
    """Kleine Fußzeile: wer geantwortet hat, was es gekostet hat, woher die Daten kommen."""
    m = str(modell or "").replace("openrouter/", "")
    m = re.sub(r"^claude-(opus|sonnet|haiku|fable)-(\d+)-(\d+)$", lambda x: f"{x[1].capitalize()} {x[2]}.{x[3]}", m)
    teile = [f"✦ {m}" if m else "", f"{kosten:.2f} $".replace(".", ",") if kosten else "", quelle or ""]
    return "<i>" + html.escape(" · ".join(t for t in teile if t)) + "</i>" if any(teile) else ""


def telegram_einrichten() -> None:
    """Für den Nutzer im eigenen Terminal: Chat-ID aus der letzten Nachricht an den Bot ermitteln, speichern, testen."""
    w = _telegram()
    if not w.get("TELEGRAM_TOKEN"):
        print(f"Zuerst den Token von @BotFather in {TELEGRAM} eintragen: TELEGRAM_TOKEN=...")
        return
    if not w.get("TELEGRAM_CHAT"):
        neu = _api("getUpdates").get("result", [])
        chats = [u["message"]["chat"]["id"] for u in neu if "message" in u]
        if not chats:
            print("Keine Nachricht gefunden. Schreib dem Bot in Telegram einmal /start und starte den Befehl erneut.")
            return
        with open(TELEGRAM, "a") as f:
            f.write(f"\nTELEGRAM_CHAT={chats[-1]}\n")
        print("Chat gefunden und gespeichert.")
    fehler = telegram_senden("✅ <b>iC Delta</b>: Alarme kommen ab jetzt hier an. Du kannst mir hier auch Fragen stellen.")
    print("Testnachricht gesendet." if fehler is None else f"Fehler: {fehler}")


# --- Agenten-Server (Claude) ------------------------------------------------------------------------------
def agent(pfad: str, daten: dict, timeout: int = 120) -> dict:
    """Fragt den Agenten-Server auf demselben Rechner. Fehler kommen als {"fehler": ...} zurück."""
    anfrage = urllib.request.Request(AGENT + pfad, data=json.dumps(daten).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return {"fehler": json.load(e).get("fehler") or f"Agent antwortet {e.code}"}
        except Exception:
            return {"fehler": f"Agent antwortet {e.code}"}
    except Exception as e:
        return {"fehler": f"Agent nicht erreichbar ({type(e).__name__})"}


# --- Regeln ----------------------------------------------------------------------------------------------
def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _zeit(t: int) -> str:
    return datetime.fromtimestamp(t, BERLIN).strftime("%d.%m. %H:%M")


def _preis(p: float) -> str:
    return f"{p:,.4f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".") if p < 10 else \
        f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _zahl(x: float, stellen: int) -> str:
    return f"{x:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _linie_wert(r: dict, k: list) -> float | None:
    """Wert der Linie an der letzten Kerze, über den Kerzenindex wie im Chart (Nächte zählen nicht)."""
    zeiten = [x[0] for x in k]
    if not zeiten:
        return None
    sek = TF[r["tf"]]

    def index(t):                                          # außerhalb der geladenen Kerzen: über die Zeit schätzen
        if t < zeiten[0]:
            return (t - zeiten[0]) / sek
        for i, z in enumerate(zeiten):
            if z >= t:
                return i
        return len(zeiten) - 1 + (t - zeiten[-1]) / sek

    i1, i2 = index(r["t1"]), index(r["t2"])
    if i2 == i1:
        return r["p2"]
    return r["p1"] + (r["p2"] - r["p1"]) * (len(k) - 1 - i1) / (i2 - i1)


def bedingung_text(r: dict) -> str:
    ri = {"ueber": "nach oben", "unter": "nach unten", "beide": "in beide Richtungen"}.get(r.get("richtung"), "")
    if r["art"] == "preis":
        return f"Kurs {'über' if r['richtung'] == 'ueber' else 'unter' if r['richtung'] == 'unter' else 'kreuzt'} {_preis(r['preis'])}"
    if r["art"] == "linie":
        return f"Linie {_preis(r['p1'])} → {_preis(r['p2'])} bricht {ri}"
    if r["art"] == "zone":
        return f"Zone {_preis(r['lo'])} bis {_preis(r['hi'])} verlassen {ri}"
    if r["art"] == "buch":
        name, stellen, einheit = KENNZAHLEN[r["kennzahl"]]
        return f"{name} {'über' if r['vergleich'] == 'ueber' else 'unter'} {_zahl(r['schwelle'], stellen)}{einheit}" + (f" seit {r['minuten']} Min." if r["minuten"] > 1 else "")
    if r["art"] == "signal":
        return f"Orderbuch-Signal {signale.NAMEN.get(r.get('signal_art'), 'jeder Art')} ab Score {_zahl(r.get('score_min', 0.5), 2)}"
    return "Formation bestätigt: " + ", ".join(ARTEN.get(a, a) for a in r.get("arten", []))


class Alarme:
    def __init__(self):
        ORT.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.regeln: list[dict] = json.loads(REGELN.read_text()) if REGELN.exists() else []
        self.fehler: str | None = None
        self.geprueft: str | None = None
        self.telegram_fehler: str | None = None
        self.zweitmeinung = False                          # erst im Dienst (starten) an, nicht in Tests

    def _speichern(self) -> None:
        tmp = REGELN.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.regeln, ensure_ascii=False, indent=1))
        tmp.replace(REGELN)

    def liste(self) -> list[dict]:
        with self.lock:
            return [{**r, "bedingung": bedingung_text(r)} for r in self.regeln]

    def verlauf(self, n: int = 50) -> list[dict]:
        if not VERLAUF.exists():
            return []
        return [json.loads(z) for z in VERLAUF.read_text().splitlines()[-n:]][::-1]

    def anlegen(self, d: dict) -> dict:
        werte = {w["id"]: w for w in lesen.watchlist()}
        wert = d.get("wert")
        if wert not in werte:                              # auch Kürzel erlauben (iC Charts kennt Symbole)
            wert = next((w["id"] for w in werte.values() if w["symbol"] == wert), None)
        if wert is None:
            raise ValueError("Wert nicht auf der Watchlist")
        art, tf = d.get("art"), d.get("tf", "15m")
        if art not in ("preis", "linie", "zone", "formation", "buch", "signal") or tf not in TF:
            raise ValueError("Art oder Zeitrahmen unbekannt")
        r = {"id": uuid.uuid4().hex[:8], "wert": wert, "symbol": werte[wert]["symbol"], "name": werte[wert]["name"],
             "markt": werte[wert]["markt"], "art": art, "tf": tf, "richtung": d.get("richtung", "beide"),
             "toleranz_pct": float(d.get("toleranz_pct", 0.1)), "einmalig": bool(d.get("einmalig", art not in ("formation", "buch", "signal"))),
             "notiz": str(d.get("notiz", ""))[:200], "quelle": d.get("quelle", "manuell"), "aktiv": True,
             "erstellt": _jetzt(), "ausgeloest": None, "stand": {}}
        if art == "preis":
            r["preis"] = float(d["preis"])
        elif art == "linie":
            r.update(t1=int(d["t1"]), p1=float(d["p1"]), t2=int(d["t2"]), p2=float(d["p2"]))
        elif art == "zone":
            r.update(lo=min(float(d["lo"]), float(d["hi"])), hi=max(float(d["lo"]), float(d["hi"])))
        elif art == "signal":                            # Orderbuch-Signal aus dem Journal (analysen.signale)
            sa = d.get("signal_art") or "alle"
            if sa != "alle" and sa not in signale.NAMEN:
                raise ValueError("signal_art muss alle oder eine Signalart sein: " + ", ".join(signale.NAMEN))
            r.update(signal_art=sa, score_min=min(1.0, max(0.0, float(d.get("score_min", 0.5)))))
        elif art == "buch":
            if d.get("kennzahl") not in KENNZAHLEN or d.get("vergleich") not in ("ueber", "unter"):
                raise ValueError("kennzahl muss ungleich, spread_bp, tiefe_bid, tiefe_ask oder updates sein; vergleich ueber oder unter")
            r.update(kennzahl=d["kennzahl"], vergleich=d["vergleich"], schwelle=float(d["schwelle"]), minuten=min(60, max(1, int(d.get("minuten", 1)))))
        else:
            arten = [a for a in d.get("arten", []) if a in ARTEN and a not in ("unterstuetzung", "widerstand")]
            if not arten:
                raise ValueError("keine bekannte Formation gewählt (Unterstützung und Widerstand gehen als Zone)")
            r.update(arten=arten, staerke=int(d.get("staerke", 5)))
        self._pruefen(r, erstmals=True)                    # Ausgangslage merken, nichts aus der Vergangenheit melden
        with self.lock:
            self.regeln.append(r)
            self._speichern()
        return {**r, "bedingung": bedingung_text(r)}

    def loeschen(self, id_: str) -> bool:
        with self.lock:
            vorher = len(self.regeln)
            self.regeln = [r for r in self.regeln if r["id"] != id_]
            self._speichern()
            return len(self.regeln) < vorher

    def umschalten(self, id_: str, aktiv: bool) -> None:
        with self.lock:
            for r in self.regeln:
                if r["id"] == id_:
                    r["aktiv"] = aktiv
                    if aktiv:
                        r["stand"] = {}
                        self._pruefen(r, erstmals=True)
            self._speichern()

    # --- Prüfen ---------------------------------------------------------------------------------------------
    def _seite(self, kurs: float, linie: float, tol: float) -> str:
        return "ueber" if kurs > linie * (1 + tol) else "unter" if kurs < linie * (1 - tol) else "auf"

    def _pruefen(self, r: dict, erstmals: bool = False) -> list[str]:
        """Prüft eine Regel; gibt Meldungstexte zurück. erstmals: nur Ausgangslage setzen."""
        st, tol, meldungen = r["stand"], r["toleranz_pct"] / 100, []
        if r["art"] == "preis":
            lk = lesen.letzter_kurs(r["wert"])
            if not lk:
                return []
            t, kurs = lk
            if st.get("t") == t:
                return []
            seite = "ueber" if kurs >= r["preis"] else "unter"
            alt = st.get("seite")
            st.update(t=t, seite=seite)
            if not erstmals and alt and seite != alt and r["richtung"] in (seite, "beide"):
                meldungen.append(f"Kurs {'über' if seite == 'ueber' else 'unter'} {_preis(r['preis'])}: jetzt {_preis(kurs)} ({_zeit(t)} Uhr)")
            return meldungen
        if r["art"] == "signal":
            jetzt = int(time.time())
            if erstmals or "t" not in st:
                st["t"] = jetzt
                return []
            for s_ in signale.neue_seit(r["wert"], st["t"], r.get("signal_art"), r.get("score_min", 0.5)):
                st["t"] = max(st["t"], s_["t"])
                pfeil = "↑" if s_["richtung"] > 0 else "↓" if s_["richtung"] < 0 else "•"
                meldungen.append(f"{s_['name']} {pfeil} bei {_preis(s_['preis'])} (Score {_zahl(s_['score'], 2)}, {_zeit(s_['t'])} Uhr): "
                                 f"{s_['begruendung']} Ungültig, wenn: {s_['invalidierung']}")
            return meldungen
        if r["art"] == "buch":
            n = r["minuten"]
            zeilen = lesen.buch_minuten(r["wert"], n + 30)        # auch Minuten, die zwischen zwei Prüfungen lagen
            if len(zeilen) < n or st.get("t") == zeilen[-1]["t"]:
                return []
            werte = [z.get(r["kennzahl"]) for z in zeilen]

            def erfuellt(j):                                      # Fenster der n Minuten bis Zeile j
                teil = werte[j - n + 1:j + 1]
                return len(teil) == n and all(v is not None and ((v > r["schwelle"]) if r["vergleich"] == "ueber" else (v < r["schwelle"])) for v in teil)

            alt = st.get("erfuellt")
            start = next((j for j, z in enumerate(zeilen) if z["t"] > st.get("t", -1)), None)
            if erstmals or start is None:
                st.update(t=zeilen[-1]["t"], erfuellt=erfuellt(len(zeilen) - 1))
                return []
            for j in range(max(start, n - 1), len(zeilen)):
                e = erfuellt(j)
                if e and not alt:
                    name, stellen, einheit = KENNZAHLEN[r["kennzahl"]]
                    z = zeilen[j]
                    meldungen = [f"{name} {'über' if r['vergleich'] == 'ueber' else 'unter'} {_zahl(r['schwelle'], stellen)}{einheit}"
                                 f"{' seit ' + str(n) + ' Min.' if n > 1 else ''}: zuletzt {_zahl(werte[j], stellen)}{einheit}"
                                 f" · Bid {_preis(z['bid'])} / Ask {_preis(z['ask'])} ({_zeit(z['t'])} Uhr)"]
                alt = e
            st.update(t=zeilen[-1]["t"], erfuellt=alt)
            return meldungen
        k = lesen.kerzen(r["wert"], r["tf"], 400)
        if not k:
            return []
        letzte = k[-1][0]
        if st.get("letzte") == letzte:
            return []
        neue = [x for x in k if x[0] > st.get("letzte", 0)] if not erstmals else []
        st["letzte"] = letzte
        if r["art"] in ("linie", "zone"):
            for j in range(len(k) - len(neue), len(k)) if neue else [len(k) - 1]:
                teil = k[:j + 1]
                kurs = teil[-1][4]
                if r["art"] == "linie":
                    wert = _linie_wert(r, teil)
                    seite = self._seite(kurs, wert, tol)
                else:
                    wert = None
                    seite = "ueber" if kurs > r["hi"] * (1 + tol) else "unter" if kurs < r["lo"] * (1 - tol) else "auf"
                alt = st.get("seite")
                if seite != "auf":
                    st["seite"] = seite
                if erstmals or not alt or seite == "auf" or seite == alt or r["richtung"] not in (seite, "beide"):
                    continue
                if r["art"] == "linie":
                    meldungen.append(f"Linie nach {'oben' if seite == 'ueber' else 'unten'} gebrochen bei {_preis(wert)}: "
                                     f"Schluss {_preis(kurs)} ({r['tf']}-Kerze {_zeit(teil[-1][0])} Uhr)")
                else:
                    meldungen.append(f"Zone {_preis(r['lo'])} bis {_preis(r['hi'])} nach {'oben' if seite == 'ueber' else 'unten'} verlassen: "
                                     f"Schluss {_preis(kurs)} ({r['tf']}-Kerze {_zeit(teil[-1][0])} Uhr)")
            return meldungen
        # Formation: bestätigte Formationen der gewählten Arten, jede nur einmal
        gemeldet = st.setdefault("gemeldet", [])
        for f in erkennen(k, r.get("staerke", 5)):
            if f["art"] not in r["arten"] or f["status"] not in ("bestätigt", "gebrochen") or f["schluessel"] in gemeldet:
                continue
            gemeldet.append(f["schluessel"])
            if erstmals or (f["ereignis_t"] or 0) < (neue[0][0] if neue else letzte):
                continue                                   # schon vor dem Anlegen bzw. vor der letzten Prüfung bestätigt
            meldungen.append(f"{f['name']} ({f['richtung']}){': ' + f['text'] if f['text'] else ''}, "
                             f"{r['tf']}-Kerze {_zeit(f['ereignis_t'])} Uhr, Schluss {_preis(k[-1][4])}")
        del gemeldet[:-300]
        return meldungen

    # --- Melden ---------------------------------------------------------------------------------------------
    def _verlauf(self, eintrag: dict) -> None:
        with self.lock, open(VERLAUF, "a") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")

    def melden(self, r: dict, text: str) -> None:
        art = {"preis": "Kursalarm", "buch": "Orderbuch-Alarm", "signal": "Orderbuch-Signal", "linie": "Linienbruch", "zone": "Zone verlassen", "formation": "Formation"}.get(r["art"], "Alarm")
        haupt, _, rest = text.partition(": ")
        zeilen = [f"🔔 <b>{html.escape(r['name'])}</b> · {html.escape(r['symbol'])}", f"<i>{art}{' · ' + r['tf'] if r['art'] not in ('preis', 'buch', 'signal') else ''}</i>", "",
                  f"<b>{html.escape(haupt)}</b>"]
        if rest:
            zeilen.append(html.escape(rest))
        if r["notiz"]:
            zeilen += ["", f"📝 {html.escape(r['notiz'])}"]
        zeilen += ["", f"<i>{QUELLE.get(r['markt'], '')} · Zweitmeinung folgt</i>" if self.zweitmeinung and notizen.ki_lesen().get("zweitmeinung") else f"<i>{QUELLE.get(r['markt'], '')}</i>"]
        fehler = telegram_senden("\n".join(zeilen))
        self._verlauf({"zeit": _jetzt(), "regel": r["id"], "wert": r["symbol"], "art": r["art"], "text": text,
                       "telegram": "gesendet" if fehler is None else fehler})
        if self.zweitmeinung and notizen.ki_lesen().get("zweitmeinung"):
            threading.Thread(target=self._zweitmeinung, args=(dict(r), text), daemon=True, name="zweitmeinung").start()

    def _zweitmeinung(self, r: dict, text: str) -> None:
        """Claude ordnet den Alarm mit Buch und Chart ein; kommt als zweite Nachricht."""
        antwort = agent("/zweitmeinung", {"wert": r["wert"], "symbol": r["symbol"], "name": r["name"], "markt": r["markt"], "art": r["art"],
                                          "tf": r["tf"], "text": text, "notiz": r.get("notiz", ""), "bedingung": bedingung_text(r)}, timeout=150)
        if antwort.get("fehler") or not antwort.get("text"):
            self._verlauf({"zeit": _jetzt(), "regel": r["id"], "wert": r["symbol"], "art": "zweitmeinung", "text": "keine Zweitmeinung: " + str(antwort.get("fehler", "leer")), "telegram": "–"})
            return
        kopf = f"💬 <b>Zweitmeinung</b> · {html.escape(r['name'])}"
        fehler = telegram_senden(kopf + "\n\n" + markdown_zu_html(antwort["text"]) + "\n\n" + fusszeile(antwort.get("modell"), antwort.get("kosten")), antwort.get("bilder"))
        self._verlauf({"zeit": _jetzt(), "regel": r["id"], "wert": r["symbol"], "art": "zweitmeinung", "text": antwort["text"][:2000],
                       "modell": antwort.get("modell"), "telegram": "gesendet" if fehler is None else fehler})

    def runde(self) -> None:
        with self.lock:
            regeln = [r for r in self.regeln if r["aktiv"]]
        for r in regeln:
            try:
                for text in self._pruefen(r):
                    self.melden(r, text)
                    r["ausgeloest"] = _jetzt()
                    if r["einmalig"]:
                        r["aktiv"] = False
                        break
            except Exception as e:
                self.fehler = f"{r['symbol']}: {type(e).__name__}: {str(e)[:120]}"
        with self.lock:
            self._speichern()
        try:
            nachreichen()                                  # nach der Demo: zurückgehaltene Meldungen senden
        except Exception as e:
            self.fehler = f"Nachreichen: {type(e).__name__}: {str(e)[:120]}"
        try:
            notizen.journal_pruefen()
        except Exception as e:
            self.fehler = f"Journal: {type(e).__name__}: {str(e)[:120]}"
        self.geprueft = _jetzt()

    # --- Rückkanal: Fragen aus Telegram an Claude ---------------------------------------------------------------
    def _telegram_antwort(self, text: str) -> str:
        befehl = text.strip().split()[0].lower() if text.strip() else ""
        if befehl in ("/start", "/hilfe", "/help"):
            return HILFE
        if befehl == "/alarme":
            aktive = [r for r in self.liste() if r["aktiv"]]
            return "Keine aktiven Alarme." if not aktive else "Aktive Alarme:\n" + "\n".join(f"• {r['symbol']}: {r['bedingung']}" for r in aktive[:30])
        if befehl == "/bilanz":
            b = notizen.journal_bilanz(5)
            g = b["gesamt"]
            zeilen = [f"Einschätzungen: {g['n']} · Treffer {g['treffer']} · Fehl {g['fehl']} · unentschieden {g['unentschieden']} · offen {g['offen']}"
                      + (f" · Quote {g['quote']} %" if g['quote'] is not None else "")]
            zeilen += [f"• {m['name']}: {m['quote']} % ({m['treffer']}/{m['treffer'] + m['fehl']})" for m in b["je_modell"] if m["quote"] is not None]
            return "\n".join(zeilen)
        if befehl == "/neu":
            agent("/frage", {"kanal": "telegram", "neu": True, "text": ""}, timeout=10)
            return "Neues Gespräch. Was möchtest du wissen?"
        if befehl == "/lage":
            antwort = agent("/routine", {"art": "morgenlage", "senden": False}, timeout=280)
            self._bilder = antwort.get("bilder") or []
            self._fuss = fusszeile(antwort.get("modell"), antwort.get("kosten"))
            return ("# 🌅 Morgenlage auf Abruf\n" + antwort["text"]) if antwort.get("text") else ("Fehler: " + str(antwort.get("fehler")))
        antwort = agent("/frage", {"kanal": "telegram", "text": text[:4000]}, timeout=280)
        self._bilder = antwort.get("bilder") or []
        self._fuss = fusszeile(antwort.get("modell"), antwort.get("kosten"))
        return antwort.get("text") or ("Claude ist gerade nicht erreichbar: " + str(antwort.get("fehler")))

    def telegram_schleife(self) -> None:
        offset = None
        while True:
            w = _telegram()
            if not (w.get("TELEGRAM_TOKEN") and w.get("TELEGRAM_CHAT")):
                time.sleep(60)
                continue
            try:
                daten = {"timeout": 25, "allowed_updates": ["message"]}
                if offset is not None:
                    daten["offset"] = offset
                for u in _api("getUpdates", daten, timeout=40).get("result", []):
                    offset = u["update_id"] + 1
                    m = u.get("message") or {}
                    text = m.get("text")
                    if not text or str(m.get("chat", {}).get("id")) != str(w["TELEGRAM_CHAT"]):
                        continue                               # nur der eingerichtete Chat, alles andere wird verworfen
                    try:
                        _api("sendChatAction", {"chat_id": w["TELEGRAM_CHAT"], "action": "typing"})
                    except Exception:
                        pass
                    self._bilder, self._fuss = [], ""
                    antwort = self._telegram_antwort(text)
                    fehler = telegram_senden(markdown_zu_html(antwort) + ("\n\n" + self._fuss if self._fuss else ""), self._bilder, antwort=True)
                    self._verlauf({"zeit": _jetzt(), "regel": None, "wert": None, "art": "telegram", "text": f"Frage: {text[:200]} → {antwort[:300]}",
                                   "telegram": "gesendet" if fehler is None else fehler})
                self.telegram_fehler = None
            except Exception as e:
                self.telegram_fehler = f"{type(e).__name__}: {str(e)[:150]}"
                time.sleep(15)

    def starten(self) -> None:
        self.zweitmeinung = True

        def schleife():
            while True:
                try:
                    self.runde()
                except Exception as e:
                    self.fehler = f"{type(e).__name__}: {str(e)[:150]}"
                time.sleep(30)
        threading.Thread(target=schleife, daemon=True, name="alarme").start()
        threading.Thread(target=self.telegram_schleife, daemon=True, name="telegram").start()
