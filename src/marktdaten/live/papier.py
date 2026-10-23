"""Papierhandel: simulierte Orders gegen den laufenden Kursstand. Kein echtes Geld, keine Börsenanbindung.

Orders entstehen nur in iC Charts (papier.js). Ausgeführt wird hier im Kern, weil nur er Kurse und Orderbuch
hat und nur er Börsendaten abruft:
  - Markt-Orders laufen durch das Orderbuch (Xetra und Binance je 5 Stufen); ohne Buch zum besten Kurs des
    Hauptplatzes, ohne Kurs zum letzten Abschluss.
  - Limit-Orders werden ausgeführt, sobald der Durchschnittspreis durch das Buch das Limit einhält.
  - Stopp-Orders werden zur Markt-Order, sobald der letzte Abschluss den Stopp erreicht.
Nur long (verkaufen bis zum Bestand), nur ganze Orders (keine Teilausführung), feste Gebühr je Order.
Deutsche Kurse sind 15 Minuten verzögert: ausgeführt wird zum Datenstand, nicht zum Echtzeitkurs.

Nach jeder Ausführung geht eine Meldung an Telegram (über den Analysen-Dienst), nach jedem abgeschlossenen
Trade bewertet Claude ihn (Agent auf Port 8814) für das Journal.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..modell import iso_aus_ns
from ..quellen.referenz import ezb_kurs
from .zustand import HAUPTPLATZ, Zustand

UTC = timezone.utc
WAEHRUNG = {"DE": "EUR", "US": "USD", "KRYPTO": "USD", "ROHSTOFFE": "USD"}
ZEICHEN = {"EUR": "€", "USD": "$"}
# Wie alt der Datenstand höchstens sein darf, damit eine Order ausgeführt wird (DE: 15 Min. Verzögerung plus Puffer)
FRISCH_NS = {"DE": 35 * 60 * 10**9, "US": 20 * 60 * 10**9, "KRYPTO": 5 * 60 * 10**9, "ROHSTOFFE": 5 * 60 * 10**9}
# Reguläre Handelszeit je Markt (Ortszeit der Börse, nur werktags); Krypto handelt immer.
# Abends handelt Xetra nur im dünnen Retail-Buch, das reguläre Buch ist dann leer oder veraltet.
HANDELSZEIT = {"DE": (ZoneInfo("Europe/Berlin"), (9, 0), (17, 30)), "US": (ZoneInfo("America/New_York"), (9, 30), (16, 0))}
VOREINSTELLUNGEN = {"start_eur": 50_000.0, "start_usd": 50_000.0, "gebuehr": 1.0}
from .. import ports
ANALYSEN = ports.url(ports.ANALYSEN)
AGENT = ports.url(ports.AGENT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS papier_orders (nr INTEGER PRIMARY KEY, id TEXT, symbol TEXT, name TEXT, markt TEXT,
    seite TEXT, art TEXT, menge REAL, limit_preis REAL, stopp_preis REAL, notiz TEXT, status TEXT, erstellt TEXT,
    ausgeloest TEXT, ausgefuehrt TEXT, datenzeit TEXT, preis REAL, gebuehr REAL, hinweis TEXT);
CREATE TABLE IF NOT EXISTS papier_journal (nr INTEGER PRIMARY KEY, art TEXT, bezug INTEGER, zeit TEXT, text TEXT,
    modell TEXT, kosten REAL);
"""
# Journal: art "eigen" schreibt Niko selbst, alle anderen Claude (woche, tag, frage, trade, fehler)
JFELDER = ("nr", "art", "bezug", "zeit", "text", "modell", "kosten", "symbol", "titel", "geaendert")
FELDER = ("nr", "id", "symbol", "name", "markt", "seite", "art", "menge", "limit_preis", "stopp_preis", "notiz", "status",
          "erstellt", "ausgeloest", "ausgefuehrt", "datenzeit", "preis", "gebuehr", "hinweis")


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _preis(p: float | None, waehrung: str = "") -> str:
    if p is None:
        return "–"
    s = f"{p:,.{3 if p < 10 else 2}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {ZEICHEN.get(waehrung, '')}".strip()


def _menge(m: float) -> str:
    return f"{m:g}".replace(".", ",")


def _post(url: str, daten: dict, timeout: int) -> dict:
    anfrage = urllib.request.Request(url, data=json.dumps(daten).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as r:
            return json.load(r)
    except Exception as e:
        return {"fehler": f"{type(e).__name__}: {e}"[:200]}


def durch_das_buch(stufen: list, menge: float) -> tuple[float, float] | None:
    """Durchschnittspreis und schlechtester Preis, wenn menge durch die Stufen [(preis, menge, …)] läuft.
    None, wenn das Buch leer ist; reicht die Tiefe nicht, wird der Rest zum letzten Preis gerechnet."""
    if not stufen:
        return None
    rest, kosten, letzter = menge, 0.0, stufen[0][0]
    for s in stufen:
        n = min(rest, s[1])
        kosten += n * s[0]
        rest -= n
        letzter = s[0]
        if rest <= 0:
            break
    kosten += max(rest, 0) * letzter
    return kosten / menge, letzter


def in_handelszeit(markt: str, ts_ns: int) -> bool:
    if markt not in HANDELSZEIT:
        return True
    zone, von, bis = HANDELSZEIT[markt]
    d = datetime.fromtimestamp(ts_ns / 1e9, zone)
    return d.weekday() < 5 and von <= (d.hour, d.minute) < bis


class Papier(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="papier")
        self.z = zustand
        self.s = zustand.speicher
        self.lock = threading.RLock()
        with self.s.lock:
            self.s.db.executescript(SCHEMA)
            spalten = {z[1] for z in self.s.db.execute("PRAGMA table_info(papier_journal)")}
            for s in ("symbol", "titel", "geaendert"):                # Journal als Tagebuch (21.10.2026)
                if s not in spalten:
                    self.s.db.execute(f"ALTER TABLE papier_journal ADD COLUMN {s} TEXT")

    # --- Einstellungen -----------------------------------------------------------------------------
    def einstellungen(self) -> dict:
        werte = dict(VOREINSTELLUNGEN)
        for k, v in self.s._x("SELECT schluessel, wert FROM einstellungen WHERE schluessel LIKE 'papier_%'"):
            werte[k.removeprefix("papier_")] = json.loads(v)
        return werte

    def einstellen(self, **werte) -> dict:
        for k, v in werte.items():
            if k in VOREINSTELLUNGEN and float(v) >= 0:
                self.s._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)", ("papier_" + k, json.dumps(float(v))))
        return self.einstellungen()

    def zuruecksetzen(self) -> dict:
        with self.lock:
            self.s._x("DELETE FROM papier_orders")
            self.s._x("DELETE FROM papier_journal")
            self.s._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)", ("papier_seit", json.dumps(_jetzt())))
        return {"ok": True}

    # --- Lesen -------------------------------------------------------------------------------------
    def orders(self, wo: str = "", werte: tuple = ()) -> list[dict]:
        return [dict(zip(FELDER, z)) for z in self.s._x(f"SELECT {', '.join(FELDER)} FROM papier_orders {wo} ORDER BY nr", werte)]

    def journal(self, q: str = "", wer: str = "", symbol: str = "", n: int | None = None) -> list[dict]:
        """Einträge, neueste zuerst; q sucht in Text, Frage und Kürzel, wer = du | claude."""
        wo, werte = [], []
        for wort in q.split():
            wo.append("(text LIKE ? OR titel LIKE ? OR symbol LIKE ?)")
            werte += [f"%{wort}%"] * 3
        if wer == "du":
            wo.append("art = 'eigen'")
        elif wer == "claude":
            wo.append("art != 'eigen'")
        if symbol:
            wo.append("symbol = ?")
            werte.append(symbol)
        sql = (f"SELECT {', '.join(JFELDER)} FROM papier_journal {'WHERE ' + ' AND '.join(wo) if wo else ''} ORDER BY nr DESC"
               + (f" LIMIT {int(n)}" if n else ""))
        return [dict(zip(JFELDER, z)) for z in self.s._x(sql, tuple(werte))]

    def abrechnung(self) -> dict:
        """Positionen und abgeschlossene Trades nach FIFO aus den ausgeführten Orders, Guthaben je Währung."""
        e = self.einstellungen()
        kasse = {"EUR": e["start_eur"], "USD": e["start_usd"]}
        lose: dict[str, list] = {}           # Wert -> offene Kauf-Teile [menge, preis, gebuehr_je_stueck, nr, zeit, notiz]
        trades = []
        ausgefuehrt = sorted(self.orders("WHERE status = 'ausgefuehrt'"), key=lambda o: (o["ausgefuehrt"] or "", o["nr"]))
        for o in ausgefuehrt:
            w = WAEHRUNG[o["markt"]]
            if o["seite"] == "kauf":
                kasse[w] -= o["menge"] * o["preis"] + o["gebuehr"]
                lose.setdefault(o["id"], []).append([o["menge"], o["preis"], o["gebuehr"] / o["menge"], o["nr"], o["datenzeit"] or o["ausgefuehrt"], o["notiz"]])
                continue
            kasse[w] += o["menge"] * o["preis"] - o["gebuehr"]
            rest, einstand, gebuehren, kaeufe, eroeffnet, notizen = o["menge"], 0.0, o["gebuehr"], [], None, []
            for los in lose.get(o["id"], []):
                if rest <= 0:
                    break
                n = min(rest, los[0])
                einstand += n * los[1]
                gebuehren += n * los[2]
                los[0] -= n
                rest -= n
                kaeufe.append(los[3])
                eroeffnet = eroeffnet or los[4]
                if los[5]:
                    notizen.append(los[5])
            lose[o["id"]] = [x for x in lose.get(o["id"], []) if x[0] > 1e-12]
            menge = o["menge"] - max(rest, 0)
            if menge <= 0:
                continue
            ergebnis = (o["preis"] * menge - einstand) - gebuehren
            trades.append({"nr": o["nr"], "id": o["id"], "symbol": o["symbol"], "name": o["name"], "markt": o["markt"], "waehrung": w,
                           "menge": menge, "einstieg": einstand / menge, "ausstieg": o["preis"], "eroeffnet": eroeffnet,
                           "geschlossen": o["datenzeit"] or o["ausgefuehrt"], "gebuehren": round(gebuehren, 2),
                           "ergebnis": round(ergebnis, 2), "ergebnis_pct": round(ergebnis / einstand * 100, 2) if einstand else None,
                           "kaeufe": kaeufe, "notiz_kauf": " · ".join(dict.fromkeys(notizen)), "notiz_verkauf": o["notiz"] or ""})
        positionen = []
        with self.z.lock:
            kurse = {w.id: (w.letzter, iso_aus_ns(w.zeit) if w.zeit else None) for w in self.z.werte.values()}
        for id_, teile in lose.items():
            menge = sum(t[0] for t in teile)
            if menge <= 1e-12:
                continue
            o = next(x for x in ausgefuehrt if x["id"] == id_)
            einstand = sum(t[0] * t[1] for t in teile) / menge
            kurs, zeit = kurse.get(id_, (None, None))
            wert = (kurs or einstand) * menge
            positionen.append({"id": id_, "symbol": o["symbol"], "name": o["name"], "markt": o["markt"], "waehrung": WAEHRUNG[o["markt"]],
                               "menge": menge, "einstand": einstand, "kurs": kurs, "datenzeit": zeit, "wert": round(wert, 2),
                               "ergebnis": round((kurs - einstand) * menge, 2) if kurs else None,
                               "ergebnis_pct": round((kurs / einstand - 1) * 100, 2) if kurs else None,
                               "seit": teile[0][4]})
        konto = {}
        for w in ("EUR", "USD"):
            pos = [p for p in positionen if p["waehrung"] == w]
            tr = [t for t in trades if t["waehrung"] == w]
            start = e["start_eur" if w == "EUR" else "start_usd"]
            wert = sum(p["wert"] for p in pos)
            konto[w] = {"start": start, "guthaben": round(kasse[w], 2), "positionen": round(wert, 2), "depot": round(kasse[w] + wert, 2),
                        "ergebnis": round(kasse[w] + wert - start, 2), "ergebnis_pct": round((kasse[w] + wert - start) / start * 100, 2) if start else None,
                        "realisiert": round(sum(t["ergebnis"] for t in tr), 2),
                        "unrealisiert": round(sum(p["ergebnis"] or 0 for p in pos), 2)}
        gewinner = sum(1 for t in trades if t["ergebnis"] > 0)
        return {"konto": konto, "positionen": positionen, "trades": list(reversed(trades)),
                "statistik": {"trades": len(trades), "gewinner": gewinner, "verlierer": len(trades) - gewinner,
                              "trefferquote": round(gewinner / len(trades) * 100) if trades else None}}

    def uebersicht(self) -> dict:
        a = self.abrechnung()
        with self.z.lock:
            handelbar = {w.symbol: {"id": w.id, "name": w.name, "markt": w.markt, "waehrung": WAEHRUNG[w.markt], "kurs": w.letzter}
                         for w in self.z.werte.values()}
        alle = self.orders()
        e = self.einstellungen()
        return {**a, "offen": [o for o in alle if o["status"] == "offen"],
                "verlauf": list(reversed([o for o in alle if o["status"] != "offen"]))[:200],
                "journal": self.journal()[:100], "einstellungen": {k: e[k] for k in VOREINSTELLUNGEN}, "seit": e.get("seit"), "fx": e.get("fx"),
                "handelbar": handelbar, "stand": _jetzt()}

    # --- Orders ------------------------------------------------------------------------------------
    def aufgeben(self, symbol: str, seite: str, art: str, menge, limit=None, stopp=None, notiz: str = "", **_) -> dict:
        with self.z.lock:
            w = next((x for x in self.z.werte.values() if x.symbol == symbol or x.id == symbol), None)
        if w is None:
            return {"fehler": f"{symbol} ist nicht auf der Watchlist; Papierhandel geht nur mit Werten der Watchlist."}
        if seite not in ("kauf", "verkauf") or art not in ("markt", "limit", "stopp"):
            return {"fehler": "Seite oder Orderart unbekannt"}
        try:
            menge = float(menge)
            limit = float(limit) if art == "limit" else None
            stopp = float(stopp) if art == "stopp" else None
        except (TypeError, ValueError):
            return {"fehler": "Stückzahl oder Preis ist keine Zahl"}
        if menge <= 0 or (w.markt not in ("KRYPTO", "ROHSTOFFE") and menge != int(menge)):
            return {"fehler": "Aktien nur in ganzen Stück, Menge größer als null"}
        if (art == "limit" and not limit or limit is not None and limit <= 0) or (art == "stopp" and not stopp or stopp is not None and stopp <= 0):
            return {"fehler": "Für Limit- und Stopp-Orders einen Preis angeben"}
        with self.lock:
            if seite == "verkauf":
                bestand = next((p["menge"] for p in self.abrechnung()["positionen"] if p["id"] == w.id), 0)
                reserviert = sum(o["menge"] for o in self.orders("WHERE status = 'offen' AND seite = 'verkauf' AND id = ?", (w.id,)))
                if menge > bestand - reserviert + 1e-9:
                    return {"fehler": f"Nur long: Bestand {_menge(bestand)}, davon {_menge(reserviert)} schon in offenen Verkaufsorders"}
            self.s._x("INSERT INTO papier_orders (id, symbol, name, markt, seite, art, menge, limit_preis, stopp_preis, notiz, status, erstellt)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'offen', ?)",
                      (w.id, w.symbol, w.name, w.markt, seite, art, menge, limit, stopp, (notiz or "").strip()[:500], _jetzt()))
            nr = self.s._x("SELECT max(nr) FROM papier_orders")[0][0]
        self.pruefen()
        return {"order": self.orders("WHERE nr = ?", (nr,))[0]}

    def stornieren(self, nr: int) -> dict:
        with self.lock:
            o = self.orders("WHERE nr = ?", (nr,))
            if not o or o[0]["status"] != "offen":
                return {"fehler": "Order ist nicht mehr offen"}
            self.s._x("UPDATE papier_orders SET status = 'storniert', ausgefuehrt = ? WHERE nr = ?", (_jetzt(), nr))
        return {"nr": nr}

    def journal_schreiben(self, art: str, text: str, bezug: int | None = None, modell: str = "", kosten: float | None = None,
                          symbol: str | None = None, titel: str | None = None) -> dict:
        self.s._x("INSERT INTO papier_journal (art, bezug, zeit, text, modell, kosten, symbol, titel) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (art, bezug, _jetzt(), text.strip(), modell, kosten, symbol or None, (titel or "").strip()[:500] or None))
        nr = self.s._x("SELECT max(nr) FROM papier_journal")[0][0]
        return {"ok": True, "eintrag": self.eintrag_lesen(nr)}

    def eintrag_lesen(self, nr: int) -> dict | None:
        z = self.s._x(f"SELECT {', '.join(JFELDER)} FROM papier_journal WHERE nr = ?", (nr,))
        return dict(zip(JFELDER, z[0])) if z else None

    def eintrag(self, text: str = "", symbol: str = "", nr: int | None = None, **_) -> dict:
        """Eigener Eintrag von Niko: neu oder (mit nr) geändert. Claudes Einträge bleiben, wie sie geschrieben wurden."""
        text = str(text or "").strip()
        if not text:
            return {"fehler": "Der Eintrag ist leer"}
        if len(text) > 5000:
            return {"fehler": "Höchstens 5.000 Zeichen"}
        symbol = str(symbol or "").strip() or None
        if nr is None:
            return self.journal_schreiben("eigen", text, symbol=symbol)
        alt = self.eintrag_lesen(int(nr))
        if not alt or alt["art"] != "eigen":
            return {"fehler": "Nur eigene Einträge lassen sich ändern"}
        self.s._x("UPDATE papier_journal SET text = ?, symbol = ?, geaendert = ? WHERE nr = ?", (text, symbol, _jetzt(), int(nr)))
        return {"ok": True, "eintrag": self.eintrag_lesen(int(nr))}

    def eintrag_loeschen(self, nr: int) -> dict:
        alt = self.eintrag_lesen(nr)
        if not alt or alt["art"] != "eigen":
            return {"fehler": "Nur eigene Einträge lassen sich löschen"}
        self.s._x("DELETE FROM papier_journal WHERE nr = ?", (nr,))
        return {"nr": nr}

    # --- Ausführung --------------------------------------------------------------------------------
    def _markt(self, w, seite: str, menge: float) -> tuple[float, str] | None:
        """Ausführungspreis einer Markt-Order und ein Hinweis, wie er zustande kam; None ohne Kurs."""
        if w.markt in ("DE", "KRYPTO"):
            k = (HAUPTPLATZ[w.markt], w.id, "regulaer")
            stufen = self.z.buch.stufen(k, "A" if seite == "kauf" else "B", 5)
            r = durch_das_buch(stufen, menge)
            if r:
                tiefe = sum(s[1] for s in stufen)
                return r[0], "durch das Orderbuch" + (" · Buch zu dünn, Rest zum letzten Stufenpreis" if tiefe < menge else "")
        p = w.plaetze.get(HAUPTPLATZ[w.markt], {})
        bester = p.get("ask" if seite == "kauf" else "bid")
        if bester:
            return bester, "zum besten Kurs" + (" (IEX)" if w.markt == "US" else "")
        if w.letzter:
            return w.letzter, "zum letzten Abschluss (kein Kurs im Buch)"
        return None

    def _ausfuehren(self, o: dict, preis: float, hinweis: str, w) -> None:
        gebuehr = self.einstellungen()["gebuehr"]
        if o["seite"] == "kauf":
            guthaben = self.abrechnung()["konto"][WAEHRUNG[o["markt"]]]["guthaben"]
            if o["menge"] * preis + gebuehr > guthaben + 1e-6:
                self.s._x("UPDATE papier_orders SET status = 'abgelehnt', ausgefuehrt = ?, hinweis = ? WHERE nr = ?",
                          (_jetzt(), f"nicht genug Guthaben ({_preis(guthaben, WAEHRUNG[o['markt']])})", o["nr"]))
                return
        else:
            bestand = next((p["menge"] for p in self.abrechnung()["positionen"] if p["id"] == o["id"]), 0)
            if o["menge"] > bestand + 1e-9:
                self.s._x("UPDATE papier_orders SET status = 'abgelehnt', ausgefuehrt = ?, hinweis = ? WHERE nr = ?",
                          (_jetzt(), f"Bestand reicht nicht ({_menge(bestand)})", o["nr"]))
                return
        if o["markt"] == "DE":
            hinweis += " · Datenstand 15 Min. verzögert"
        self.s._x("UPDATE papier_orders SET status = 'ausgefuehrt', ausgefuehrt = ?, datenzeit = ?, preis = ?, gebuehr = ?, hinweis = ? WHERE nr = ?",
                  (_jetzt(), iso_aus_ns(w.zeit) if w.zeit else _jetzt(), round(preis, 6), gebuehr, hinweis, o["nr"]))
        threading.Thread(target=self._nach_ausfuehrung, args=(o["nr"],), daemon=True).start()

    def pruefen(self) -> None:
        jetzt = time.time_ns()
        with self.lock:
            for o in self.orders("WHERE status = 'offen'"):
                with self.z.lock:
                    w = self.z.werte.get(o["id"])
                    if w is None:
                        self.s._x("UPDATE papier_orders SET status = 'abgelehnt', ausgefuehrt = ?, hinweis = ? WHERE nr = ?",
                                  (_jetzt(), "nicht mehr auf der Watchlist", o["nr"]))
                        continue
                    if not w.zeit or jetzt - w.zeit > FRISCH_NS[w.markt] or not in_handelszeit(w.markt, w.zeit):
                        if o["hinweis"] != "wartet auf Handel":
                            self.s._x("UPDATE papier_orders SET hinweis = 'wartet auf Handel' WHERE nr = ?", (o["nr"],))
                        continue                                       # außerhalb der Handelszeit: Order wartet
                    kauf = o["seite"] == "kauf"
                    art = o["art"]
                    if art == "stopp":
                        if not w.letzter or (w.letzter < o["stopp_preis"] if kauf else w.letzter > o["stopp_preis"]):
                            continue
                        if not o["ausgeloest"]:
                            self.s._x("UPDATE papier_orders SET ausgeloest = ? WHERE nr = ?", (_jetzt(), o["nr"]))
                        art = "markt"
                    r = self._markt(w, o["seite"], o["menge"])
                    if r is None:
                        continue
                    preis, hinweis = r
                    if art == "limit":
                        if kauf and preis > o["limit_preis"] or not kauf and preis < o["limit_preis"]:
                            continue                                   # Limit noch nicht erreicht: Order bleibt offen
                        hinweis = "Limit erreicht, " + hinweis
                    elif o["art"] == "stopp":
                        hinweis = f"Stopp {_preis(o['stopp_preis'])} ausgelöst, " + hinweis
                    self._ausfuehren(o, preis, hinweis, w)

    # --- Meldungen und Journal -------------------------------------------------------------------
    def _nach_ausfuehrung(self, nr: int) -> None:
        o = self.orders("WHERE nr = ?", (nr,))[0]
        w = WAEHRUNG[o["markt"]]
        art = {"markt": "Markt", "limit": f"Limit {_preis(o['limit_preis'])}", "stopp": f"Stopp {_preis(o['stopp_preis'])}"}[o["art"]]
        zeilen = [f"🧾 <b>Papierhandel</b>: {'Kauf' if o['seite'] == 'kauf' else 'Verkauf'} {_menge(o['menge'])} {o['symbol']} zu {_preis(o['preis'], w)}",
                  f"{o['name']} · {art} · Gebühr {_preis(o['gebuehr'], w)}"]
        trade = next((t for t in self.abrechnung()["trades"] if t["nr"] == nr), None) if o["seite"] == "verkauf" else None
        if trade:
            zeilen.append(f"Ergebnis {'+' if trade['ergebnis'] >= 0 else ''}{_preis(trade['ergebnis'], w)} ({trade['ergebnis_pct']:+.2f} %)".replace(".", ",", 1))
        if o["markt"] == "DE":
            zeilen.append("<i>Datenstand 15 Min. verzögert</i>")
        _post(ANALYSEN + "/api/telegram/senden", {"text": "\n".join(zeilen)}, 20)
        if trade:
            self._bewerten(trade)

    def _bewerten(self, t: dict) -> None:
        """Claude bewertet einen abgeschlossenen Trade fürs Journal."""
        text = ("Abgeschlossener Papier-Trade (nur simuliert): " + json.dumps(t, ensure_ascii=False)
                + "\nZeiten sind UTC (ISO). Bewerte ihn für das Journal.")
        antwort = _post(AGENT + "/papier", {"art": "trade", "text": text}, 300)
        if antwort.get("text"):
            self.journal_schreiben("trade", antwort["text"], t["nr"], antwort.get("modell", ""), antwort.get("kosten"), t["symbol"])
            _post(ANALYSEN + "/api/telegram/senden", {"text": f"📓 <b>Journal</b> · {t['symbol']}\n" + antwort["text"][:3500]}, 20)
        else:
            self.journal_schreiben("fehler", "Keine Bewertung: " + str(antwort.get("fehler", "leer")), t["nr"], symbol=t["symbol"])

    # --- Wechselkurs: EZB-Referenzkurs (Dollar je Euro), damit das Depot einen Gesamtwert in Euro hat ------------
    _fx_versuch = 0.0

    def fx_auffrischen(self) -> None:
        if time.time() - self._fx_versuch < 6 * 3600:
            return
        self._fx_versuch = time.time()
        try:
            k = list(ezb_kurs("USD", 1))[-1]
            self.s._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)",
                      ("papier_fx", json.dumps({"eurusd": float(k["kurs"]), "datum": k["datum"], "quelle": "EZB"})))
        except Exception:
            self._fx_versuch = time.time() - 6 * 3600 + 600          # in 10 Minuten noch einmal

    def run(self) -> None:
        while True:
            try:
                self.fx_auffrischen()
                self.pruefen()
                self.z.melden("papier", geprueft=_jetzt(), fehler=None)
            except Exception as e:
                self.z.melden("papier", fehler=f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(2)
