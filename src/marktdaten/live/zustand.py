"""Gemeinsamer Kursstand im Speicher. Threadsicher, mit Versionszähler für die Push-Anzeige.

Der laufende Tag liegt im Arbeitsspeicher. Neue Kerzen, Abschlüsse, das je Minute verdichtete
Orderbuch und laufende Orderbuch-Aufnahmen sammeln sich in Puffern, die der Schreiber
(schreiber.py) regelmäßig in den Speicher (SQLite) schreibt.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from ..buch import Orderbuch
from ..modell import BuchSeite, BuchStufe, Quote, Trade, iso_aus_ns
from .speicher import Speicher, buendeln

MINUTE = 60_000_000_000
HAUPTPLATZ = {"DE": "XETR", "US": "IEXG", "KRYPTO": "BINANCE", "ROHSTOFFE": "BINANCEF"}
QUELLE = {"DE": "Xetra, 15 Min. verzögert", "US": "alle US-Börsen, letzte 15 Min. nur IEX (Volumen vorläufig)", "KRYPTO": "Binance live",
         "ROHSTOFFE": "Binance Futures live (Perpetual-Kontrakt, kein Spotpreis)"}
HERKUNFT = {"DE": "xetra", "US": "iex", "KRYPTO": "binance", "ROHSTOFFE": "binance_f"}     # Herkunft (q) einer live gebildeten Kerze, siehe DATENHINWEISE
# Segment-Kennungen auf den Handelsplatz zusammenfassen: Xetra meldet Abschlüsse als XETA/XETB/XETS,
# Tradegate als XGAT/XGRM.
PLATZ = {"XETA": "XETR", "XETB": "XETR", "XETS": "XETR", "XETU": "XETR", "XGRM": "XGAT", "TGAT": "XGAT"}


def platz(venue: str) -> str:
    return PLATZ.get(venue, venue)


@dataclass
class Wert:
    id: str                  # ISIN (DE), Ticker (US) oder Binance-Paar (KRYPTO)
    symbol: str
    name: str
    markt: str               # "DE", "US", "KRYPTO" oder "ROHSTOFFE" (Binance-Futures-Perpetuals)
    vortag: float | None = None
    eroeffnung: float | None = None
    letzter: float | None = None
    letzter_ts: int = 0
    eroeffnung_ts: int | None = None
    zeit: int | None = None              # Datenzeit der letzten Änderung (ns, UTC)
    stueck: float = 0.0
    umsatz: float = 0.0
    abschluesse: int = 0
    kerzen: dict = field(default_factory=dict)       # Minute -> [o, h, l, c, v, erste_ts, letzte_ts, q]
    plaetze: dict = field(default_factory=dict)      # Venue -> {bid, ask, bid_menge, ask_menge, letzter, zeit}
    trades: deque = field(default_factory=lambda: deque(maxlen=25))
    offen: set = field(default_factory=set)          # Minuten, deren Kerze noch geschrieben werden muss
    buch_minute: dict | None = None                  # laufende Verdichtung des Orderbuchs


class Zustand:
    def __init__(self, speicher: Speicher):
        self.speicher = speicher
        self.werte: dict[str, Wert] = {}
        self.buch = Orderbuch()
        self.lock = threading.RLock()
        self.version = 0
        self.dienste: dict[str, dict] = {}         # Sammler -> Stand, Fehler
        self.puffer_trades: list = []
        self.puffer_buch: list = []
        self.puffer_roh: list = []
        self.aufnahmen: dict[str, int] = {}        # Wert -> Nummer der laufenden Aufnahme
        self.beobachter: list = []                 # Rückrufe bei Änderungen der Watchlist

    # --- Watchlist -------------------------------------------------------------
    def hinzufuegen(self, w: Wert, aus_speicher: bool = True) -> None:
        with self.lock:
            self.werte[w.id] = w
            if aus_speicher:
                self._wiederherstellen(w)
            self.geaendert()
        for b in self.beobachter:
            b(w, True)

    def entfernen(self, id_: str) -> None:
        with self.lock:
            w = self.werte.pop(id_, None)
            self.aufnahmen.pop(id_, None)
            self.geaendert()
        if w:
            for b in self.beobachter:
                b(w, False)

    def _wiederherstellen(self, w: Wert) -> None:
        """Tag aus dem Speicher laden: Kerzen, Vortagesschluss, letzte Abschlüsse."""
        jetzt = datetime.now().astimezone()
        heute = int(jetzt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        for t, o, h, l, c, v, q in self.speicher.kerzen_lesen("kerzen_1m", w.id, heute, mit_quelle=True):
            w.kerzen[t // 60] = [o, h, l, c, v, t * 10**9, t * 10**9 + MINUTE - 1, q or HERKUNFT[w.markt]]
        if w.kerzen:
            erste, letzte = min(w.kerzen), max(w.kerzen)
            w.eroeffnung, w.eroeffnung_ts = w.kerzen[erste][0], erste * MINUTE
            w.letzter, w.letzter_ts = w.kerzen[letzte][3], letzte * MINUTE
            w.zeit = w.letzter_ts
            w.stueck = sum(k[4] for k in w.kerzen.values())
            w.umsatz = sum(k[3] * k[4] for k in w.kerzen.values())
            w.abschluesse = self.speicher.abschluesse_zaehlen(w.id, heute * 10**9, ("XETA", "XETB", "XETS", "XETR", "IEXG"))
        vorher = self.speicher.kerzen_lesen("kerzen_1m", w.id, heute - 5 * 86400, heute)
        if vorher and w.markt != "US":                 # US: Vortagesschluss aller Börsen kommt von Alpaca
            w.vortag = vorher[-1][4]
        for t, v, p, m in reversed(self.speicher.abschluesse_lesen(w.id, heute * 10**9)):
            w.trades.appendleft((t, v, p, m))

    # --- Ereignisse ------------------------------------------------------------
    def anwenden(self, e) -> None:
        w = self.werte.get(e.instrument)
        if w is None:
            return
        if isinstance(e, Trade):
            self._trade(w, e)
        elif isinstance(e, Quote):
            if e.extra.get("si") or e.extra.get("buch") == "retail":
                return                               # Systematische Internalisierer und Retail-Buch nicht als Platzkurs
            p = w.plaetze.setdefault(platz(e.venue), {})
            for k in ("bid", "ask"):
                if getattr(e, k) is not None:
                    p[k], p[k + "_menge"] = getattr(e, k), getattr(e, k + "_menge")
            p["zeit"] = e.ts
        elif isinstance(e, (BuchSeite, BuchStufe)):
            k = self.buch.anwenden(e)
            if w.id in self.aufnahmen:
                stufen = e.stufen if isinstance(e, BuchSeite) else [(e.preis, e.menge, e.orders)]
                self.puffer_roh.append((self.aufnahmen[w.id], e.ts, e.seite, e.buch, json.dumps(stufen)))
            if e.buch == "regulaer":
                b, a = self.buch.stufen(k, "B", 1), self.buch.stufen(k, "A", 1)
                p = w.plaetze.setdefault(platz(e.venue), {})
                if b:
                    p["bid"], p["bid_menge"] = b[0][0], b[0][1]
                if a:
                    p["ask"], p["ask_menge"] = a[0][0], a[0][1]
                p["zeit"] = e.ts
                if platz(e.venue) == HAUPTPLATZ[w.markt]:
                    self._buch_verdichten(w, k, e.ts)
        w.zeit = max(w.zeit or 0, e.ts)

    def _buch_verdichten(self, w: Wert, k, ts: int) -> None:
        """Orderbuch je Minute: letzter bester Kurs, mittlerer Spread, Tiefe der besten 5 Stufen, Ungleichgewicht."""
        minute = ts // MINUTE
        m = w.buch_minute
        if m and minute > m["minute"]:
            self._buch_abschliessen(w)
            m = None
        elif m and minute < m["minute"]:
            return                                   # verspätete Meldung einer abgeschlossenen Minute
        if not m:
            m = w.buch_minute = {"minute": minute, "spread": 0.0, "n": 0, "ungleich": 0.0, "updates": 0}
        m["updates"] += 1
        b, a = self.buch.stufen(k, "B", 5), self.buch.stufen(k, "A", 5)
        if b and a and a[0][0] > b[0][0]:
            mitte = (a[0][0] + b[0][0]) / 2
            tb, ta = sum(s[1] for s in b), sum(s[1] for s in a)
            m.update(bid=b[0][0], ask=a[0][0], tiefe_bid=tb * mitte, tiefe_ask=ta * mitte)
            m["spread"] += (a[0][0] - b[0][0]) / mitte * 1e4
            m["ungleich"] += (tb - ta) / (tb + ta) if tb + ta else 0
            m["n"] += 1

    def _buch_abschliessen(self, w: Wert) -> None:
        m, w.buch_minute = w.buch_minute, None
        if m and m["n"]:
            self.puffer_buch.append((w.id, m["minute"] * 60, m["bid"], m["ask"], round(m["spread"] / m["n"], 2),
                                     round(m["tiefe_bid"]), round(m["tiefe_ask"]), round(m["ungleich"] / m["n"], 3), m["updates"]))

    def _trade(self, w: Wert, t: Trade) -> None:
        # Nachladen läuft rückwärts (neueste Minute zuerst): deshalb überall nach Zeitstempel entscheiden.
        v = platz(t.venue)
        p = w.plaetze.setdefault(v, {})
        if t.ts >= p.get("zeit_trade", 0):
            p["letzter"], p["zeit_trade"] = t.preis, t.ts
            p["zeit"] = max(p.get("zeit", 0), t.ts)
        if not w.trades or t.ts >= w.trades[-1][0] or len(w.trades) < w.trades.maxlen:
            w.trades = deque(sorted([*w.trades, (t.ts, v, t.preis, t.menge)], reverse=True)[:25], maxlen=25)
        if t.menge and w.markt not in ("KRYPTO", "ROHSTOFFE"):
            self.puffer_trades.append((w.id, t.ts, t.venue, t.preis, t.menge))
        if v != HAUPTPLATZ[w.markt] or not t.menge:
            return
        if t.extra.get("mmtTradingMode") == "4":     # MiFID "Out of Main Session": dünner Abendhandel, nicht für Kurs und Chart
            return
        if t.ts >= w.letzter_ts:
            w.letzter, w.letzter_ts = t.preis, t.ts
        if w.eroeffnung_ts is None or t.ts < w.eroeffnung_ts:
            w.eroeffnung, w.eroeffnung_ts = t.preis, t.ts
        w.stueck += t.menge
        w.umsatz += t.preis * t.menge
        w.abschluesse += 1
        # Laufende Minute aus den Trades; bei US ersetzt die fertige Alpaca-Kerze sie später (kerze_setzen)
        self.kerze(w, t.ts, t.preis, t.preis, t.preis, t.preis, t.menge)

    def kerze(self, w: Wert, ts: int, o, h, l, c, v) -> None:
        m = ts // MINUTE
        w.offen.add(m)
        k = w.kerzen.get(m)
        if k is None:
            w.kerzen[m] = [o, h, l, c, v, ts, ts, HERKUNFT[w.markt]]
            return
        if k[7] == "sip":
            return                                   # vollständige Kerze aller Börsen nicht mehr mit IEX-Trades verändern
        k[1], k[2], k[4] = max(k[1], h), min(k[2], l), k[4] + v
        if ts < k[5]:
            k[0], k[5] = o, ts
        if ts >= k[6]:
            k[3], k[6] = c, ts

    def kerze_setzen(self, w: Wert, ts: int, o, h, l, c, v, q: str | None = None) -> None:
        """Fertige Minutenkerze (Alpaca, Binance) übernimmt die aus Trades gebaute Kerze derselben Minute.
        Eine sip-Kerze (alle US-Börsen) wird nicht mehr durch eine iex-Kerze ersetzt."""
        m = ts // MINUTE
        if q != "sip" and w.kerzen.get(m, [None] * 8)[7] == "sip":
            return
        w.kerzen[m] = [o, h, l, c, v, ts, ts + MINUTE - 1, q or HERKUNFT[w.markt]]
        w.offen.add(m)

    def melden(self, dienst: str, **werte) -> None:
        self.dienste.setdefault(dienst, {}).update(werte)

    def geaendert(self) -> None:
        self.version += 1

    # --- Puffer für den Schreiber ------------------------------------------------
    def puffer_leeren(self) -> dict:
        with self.lock:
            kerzen = {}
            jetzt_minute = int(datetime.now().timestamp() * 1e9) // MINUTE
            for w in self.werte.values():
                if w.offen:
                    kerzen[w.id] = [[m * 60, *w.kerzen[m][:5], w.kerzen[m][7]] for m in sorted(w.offen) if m in w.kerzen]
                    w.offen = set()
                if w.buch_minute and w.buch_minute["minute"] < jetzt_minute - 20:
                    self._buch_abschliessen(w)               # Minute ohne weitere Updates abschließen
            aus = {"kerzen": kerzen, "trades": self.puffer_trades, "buch": self.puffer_buch, "roh": self.puffer_roh}
            self.puffer_trades, self.puffer_buch, self.puffer_roh = [], [], []
            return aus

    # --- Ausgabe ---------------------------------------------------------------
    def _spread(self, w: Wert) -> float | None:
        p = w.plaetze.get(HAUPTPLATZ[w.markt], {})
        b, a = p.get("bid"), p.get("ask")
        return round((a - b) / ((a + b) / 2) * 1e4, 1) if b and a and a > b else None

    def zeile(self, w: Wert) -> dict:
        basis, art = (w.vortag, "Vortag") if w.vortag else (w.eroeffnung, "Eröffnung")
        verlauf = [k[3] for _, k in sorted(w.kerzen.items())]
        if len(verlauf) > 120:                       # Sparkline braucht keine 500 Punkte
            schritt = len(verlauf) / 120
            verlauf = [verlauf[int(i * schritt)] for i in range(120)] + [verlauf[-1]]
        return {"id": w.id, "symbol": w.symbol, "name": w.name, "markt": w.markt,
                "letzter": w.letzter, "vortag": w.vortag, "basis": art,
                "veraenderung": round((w.letzter / basis - 1) * 100, 2) if w.letzter and basis else None,
                "zeit": iso_aus_ns(w.zeit) if w.zeit else None, "spread_bp": self._spread(w),
                "umsatz": round(w.umsatz), "stueck": w.stueck, "abschluesse": w.abschluesse, "verlauf": verlauf,
                "aufnahme": self.aufnahmen.get(w.id)}

    def kurse(self) -> dict:
        with self.lock:
            return {"version": self.version, "werte": [self.zeile(w) for w in self.werte.values()],
                    "dienste": self.dienste}

    def _minuten(self, w: Wert, tage: int) -> list:
        """1-Minuten-Kerzen: frühere Tage aus dem Speicher, der laufende Tag aus dem Arbeitsspeicher."""
        heute = min(w.kerzen) * 60 if w.kerzen else int(datetime.now().timestamp())
        alt = self.speicher.kerzen_lesen("kerzen_1m", w.id, heute - tage * 86400, heute)
        return alt + [[m * 60, *k[:5]] for m, k in sorted(w.kerzen.items())]

    def ic_snapshot(self) -> dict:
        """Daten im Format von iC Charts (window.SNAPSHOT): je Symbol Kerzen [t_s, o, h, l, c, v] je Zeitrahmen."""
        with self.lock:
            werte = list(self.werte.values())
        from .. import ports
        aktien = {}
        for w in werte:
            if w.markt == "KRYPTO":
                continue                                    # Krypto holt iC Charts selbst live von Binance
            minuten = self._minuten(w, 10)
            tage = self.speicher.kerzen_lesen("kerzen_1d", w.id)
            if not minuten and not tage:
                continue
            d = {"name": w.name, "waehrung": "EUR" if w.markt == "DE" else "USD", "quelle": "Demo · erfundene Kurse" if ports.DEMO else QUELLE[w.markt]}
            if minuten:
                d.update({"1m": minuten[-3000:], "5m": buendeln(minuten, 300), "15m": buendeln(minuten, 900),
                          "1h": buendeln(minuten, 3600)})
            aus_minuten = buendeln(minuten, 86400)
            # Tageskerzen je Tag zusammenführen: gespeicherte (eigene oder EODHD), Tage aus Minuten haben Vorrang
            je_tag = {x[0]: x for x in tage}
            je_tag.update({x[0]: x for x in aus_minuten})
            d["1d"] = [je_tag[t] for t in sorted(je_tag)]
            aktien[w.symbol] = d
        krypto = [w.symbol for w in werte if w.markt == "KRYPTO"]
        return {"erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"), "krypto": {}, "aktien": aktien, "krypto_liste": krypto, "demo": ports.DEMO,
                "krypto_echt": not ports.DEMO or ports.DEMO_NAMEN_ECHT}

    def ic_kerzen(self, symbol: str, tf: str, anzahl: int = 3) -> dict | None:
        """Die letzten Kerzen eines Symbols für das Live-Nachführen in iC Charts."""
        sekunden = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 3600, "1d": 86400}.get(tf)
        with self.lock:
            w = next((x for x in self.werte.values() if x.symbol == symbol), None)
            if w is None or sekunden is None:
                return None
            minuten = [[m * 60, *k[:5]] for m, k in sorted(w.kerzen.items())]
            kerzen = buendeln(minuten, sekunden)
            if tf == "4h":                   # wie iC Charts: je Handelstag vier Stundenkerzen zusammen
                kerzen = _je_tag(kerzen, 4)
            return {"kerzen": kerzen[-anzahl:], "preis": w.letzter,
                    "zeit": iso_aus_ns(w.zeit) if w.zeit else None}

    def ic_kurse(self) -> dict:
        """Letzter Kurs und Bezug je Symbol für die Kursleiste in iC Charts."""
        with self.lock:
            return {w.symbol: {"price": w.letzter, "open": w.vortag or w.eroeffnung}
                    for w in self.werte.values() if w.letzter and w.markt != "KRYPTO"}

    def detail(self, id_: str) -> dict | None:
        with self.lock:
            w = self.werte.get(id_)
            if w is None:
                return None
            d = self.zeile(w)
            d["kerzen"] = [[m * 60_000, *k[:5]] for m, k in sorted(w.kerzen.items())]
            d["buch"] = None
            if w.markt in ("DE", "KRYPTO"):
                # Nach 17:30 Uhr ist das reguläre Xetra-Buch leer; dann das Retail-Buch des Abendhandels zeigen.
                for art in ("regulaer", "retail"):
                    k = (HAUPTPLATZ[w.markt], w.id, art)
                    bid, ask = self.buch.stufen(k, "B", 5), self.buch.stufen(k, "A", 5)
                    if bid or ask:
                        d["buch"] = {"art": art, "bid": bid, "ask": ask,
                                     "zeit": iso_aus_ns(self.buch.zeit[k]) if k in self.buch.zeit else None}
                        break
            d["plaetze"] = [{"venue": v, **{x: y for x, y in p.items() if x not in ("zeit", "zeit_trade")},
                             "zeit": iso_aus_ns(p["zeit"]) if p.get("zeit") else None}
                            for v, p in sorted(w.plaetze.items())]
            d["trades"] = [{"zeit": iso_aus_ns(t), "venue": v, "preis": p, "menge": m} for t, v, p, m in w.trades]
            return d


def _je_tag(stunden: list, n: int) -> list:
    """Wie aggregate() in iC Charts: je Tag Blöcke zu n Stundenkerzen, beginnend mit der ersten Stunde des Tages."""
    aus, tag, zaehler = [], None, 0
    for k in stunden:
        if k[5] == 0 and k[2] == k[3]:       # leere Kerzen filtert iC Charts ebenfalls weg
            continue
        d = k[0] // 86400
        if not aus or d != tag or zaehler == n:
            aus.append(list(k))
            tag, zaehler = d, 1
            continue
        a = aus[-1]
        a[2], a[3], a[4], a[5] = max(a[2], k[2]), min(a[3], k[3]), k[4], a[5] + k[5]
        zaehler += 1
    return aus
