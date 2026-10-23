"""Gedächtnis der KI: Dossiers je Wert, Journal der Einschätzungen mit Prüfung, Notizen (Morgenlage,
Nachbesprechung) und die KI-Einstellungen. Alles sind kleine Dateien unter ~/.local/share/marktdaten/analysen.

Das Journal ist die Bilanz: Jede Einschätzung mit Richtung und Horizont wird gespeichert und nach Ablauf
gegen den Kurs geprüft. Schwelle je Horizont, damit Rauschen nicht als Treffer zählt.
"""
from __future__ import annotations

import json
import pathlib
import threading
import uuid
from datetime import datetime, timedelta, timezone

from . import lesen

ORT = lesen.DATEN / "analysen"
DOSSIER = ORT / "dossier"
JOURNAL = ORT / "journal.jsonl"
NOTIZEN = ORT / "notizen.jsonl"
KI = ORT / "ki.json"
HORIZONTE = {"1h": 3600, "4h": 4 * 3600, "1d": 86400, "1w": 7 * 86400}
SCHWELLE_PCT = {"1h": 0.1, "4h": 0.2, "1d": 0.3, "1w": 0.6}
RICHTUNGEN = ("steigt", "faellt", "seitwaerts")
STANDARD_KI = {"anbieter": "claude-code", "modell": "claude-opus-5-5", "modell_routine": "claude-sonnet-5-5",
               "zweitmeinung": True, "zweitmeinung_anbieter": "", "zweitmeinung_modell": "",
               "liga": False, "liga_anbieter": "openrouter", "liga_modell": "",
               "morgenlage": "08:45", "nachbesprechung": "17:50", "routinen": True}
_lock = threading.RLock()


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _wert(wert: str) -> dict | None:
    werte = {w["id"]: w for w in lesen.watchlist()}
    if wert in werte:
        return werte[wert]
    return next((w for w in werte.values() if w["symbol"] == wert), None)


# --- Dossier -------------------------------------------------------------------------------------------
def dossier_lesen(wert: str) -> dict:
    w = _wert(wert)
    if not w:
        raise ValueError("Wert nicht auf der Watchlist")
    p = DOSSIER / f"{w['id']}.md"
    text = p.read_text() if p.exists() else ""
    stand = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds") if p.exists() else None
    return {"wert": w, "text": text, "stand": stand}


def dossier_schreiben(wert: str, text: str) -> dict:
    w = _wert(wert)
    if not w:
        raise ValueError("Wert nicht auf der Watchlist")
    DOSSIER.mkdir(parents=True, exist_ok=True)
    text = str(text).strip()[:8000]
    with _lock:
        (DOSSIER / f"{w['id']}.md").write_text(text + "\n")
    return {"ok": True, "wert": w, "zeichen": len(text)}


def dossiers() -> list[dict]:
    aus = []
    for w in lesen.watchlist():
        p = DOSSIER / f"{w['id']}.md"
        if p.exists():
            aus.append({"wert": w, "text": p.read_text(), "stand": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return aus


# --- Journal -------------------------------------------------------------------------------------------
def _journal_lesen() -> list[dict]:
    if not JOURNAL.exists():
        return []
    return [json.loads(z) for z in JOURNAL.read_text().splitlines() if z.strip()]


def _journal_schreiben(eintraege: list[dict]) -> None:
    tmp = JOURNAL.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eintraege))
    tmp.replace(JOURNAL)


def journal_eintragen(d: dict) -> dict:
    w = _wert(d.get("wert", ""))
    if not w:
        raise ValueError("Wert nicht auf der Watchlist")
    richtung, horizont = d.get("richtung"), d.get("horizont", "1d")
    if richtung not in RICHTUNGEN or horizont not in HORIZONTE:
        raise ValueError("richtung muss steigt, faellt oder seitwaerts sein; horizont 1h, 4h, 1d oder 1w")
    lk = lesen.letzter_kurs(w["id"])
    if not lk:
        raise ValueError("kein Kurs für diesen Wert gespeichert")
    jetzt = _jetzt()
    faellig = jetzt + timedelta(seconds=HORIZONTE[horizont])
    while faellig.weekday() >= 5 and horizont != "1h":                   # Wochenende überspringen
        faellig += timedelta(days=1)
    e = {"id": uuid.uuid4().hex[:8], "zeit": jetzt.isoformat(timespec="seconds"), "wert": w["id"], "symbol": w["symbol"], "markt": w["markt"],
         "richtung": richtung, "horizont": horizont, "faellig": faellig.isoformat(timespec="seconds"), "kurs": lk[1],
         "begruendung": str(d.get("begruendung", ""))[:400], "modell": str(d.get("modell", ""))[:60], "quelle": str(d.get("quelle", "chat"))[:30],
         "ergebnis": None, "kurs_danach": None, "aenderung_pct": None, "geprueft": None}
    ORT.mkdir(parents=True, exist_ok=True)
    with _lock, open(JOURNAL, "a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def journal_pruefen() -> int:
    """Fällige Einschätzungen gegen den Kurs prüfen. Rückgabe: Anzahl neu geprüfter Einträge."""
    with _lock:
        eintraege = _journal_lesen()
        jetzt = _jetzt()
        n = 0
        for e in eintraege:
            if e.get("ergebnis") is not None or datetime.fromisoformat(e["faellig"]) > jetzt:
                continue
            lk = lesen.letzter_kurs(e["wert"])
            if not lk or not e.get("kurs"):
                continue
            aenderung = (lk[1] / e["kurs"] - 1) * 100
            schwelle = SCHWELLE_PCT[e["horizont"]]
            if e["richtung"] == "seitwaerts":
                ergebnis = "treffer" if abs(aenderung) < schwelle else "fehl"
            elif abs(aenderung) < schwelle:
                ergebnis = "unentschieden"
            else:
                ergebnis = "treffer" if (aenderung > 0) == (e["richtung"] == "steigt") else "fehl"
            e.update(ergebnis=ergebnis, kurs_danach=lk[1], aenderung_pct=round(aenderung, 2), geprueft=jetzt.isoformat(timespec="seconds"))
            n += 1
        if n:
            _journal_schreiben(eintraege)
    return n


def journal_bilanz(n_letzte: int = 30) -> dict:
    eintraege = _journal_lesen()
    gruppen: dict[tuple, dict] = {}
    for e in eintraege:
        for schluessel in (("horizont", e["horizont"]), ("modell", e.get("modell") or "unbekannt"), ("wert", e["symbol"])):
            g = gruppen.setdefault(schluessel, {"n": 0, "treffer": 0, "fehl": 0, "unentschieden": 0, "offen": 0})
            g["n"] += 1
            g[e["ergebnis"] or "offen"] += 1
    def quote(g):
        entschieden = g["treffer"] + g["fehl"]
        return round(g["treffer"] / entschieden * 100, 1) if entschieden else None
    aus = {"gesamt": {"n": len(eintraege), "treffer": sum(e["ergebnis"] == "treffer" for e in eintraege), "fehl": sum(e["ergebnis"] == "fehl" for e in eintraege),
                      "unentschieden": sum(e["ergebnis"] == "unentschieden" for e in eintraege), "offen": sum(e["ergebnis"] is None for e in eintraege)},
           "je_horizont": [], "je_modell": [], "je_wert": [], "letzte": eintraege[-n_letzte:][::-1], "schwellen_pct": SCHWELLE_PCT}
    aus["gesamt"]["quote"] = quote(aus["gesamt"])
    for (art, name), g in sorted(gruppen.items()):
        aus["je_" + art].append({"name": name, **g, "quote": quote(g)})
    return aus


# --- Notizen (Morgenlage, Nachbesprechung, freie Notizen) --------------------------------------------
def _fokus_pruefen(f) -> dict | None:
    """Datenblock der Morgenlage für das Briefing: höchstens fünf Highlights, je Wert, Formation, These, Herleitung und zwei Grenzen (oben über unten)."""
    if not isinstance(f, dict):
        return None
    punkte = []
    for p in (f.get("punkte") or [])[:5]:
        try:
            o, u = float(p["oben"]["kurs"]), float(p["unten"]["kurs"])
        except (KeyError, TypeError, ValueError):
            continue
        if not str(p.get("wert") or "").strip() or o <= u:
            continue
        punkte.append({"wert": str(p["wert"]).strip()[:20], "muster": str(p.get("muster") or "")[:60], "these": str(p.get("these") or "")[:400], "herleitung": str(p.get("herleitung") or "")[:2000],
                       "oben": {"kurs": o, "text": str(p["oben"].get("text") or "")[:60]}, "unten": {"kurs": u, "text": str(p["unten"].get("text") or "")[:60]}})
    return {"kernsatz": str(f.get("kernsatz") or "")[:300], "punkte": punkte} if punkte else None


def notiz_schreiben(art: str, text: str, modell: str = "", wert: str | None = None, fokus: dict | None = None) -> dict:
    art = str(art)[:30]
    e = {"id": uuid.uuid4().hex[:8], "zeit": _jetzt().isoformat(timespec="seconds"), "art": art, "text": str(text)[:12000], "modell": str(modell)[:60], "wert": wert}
    if (f := _fokus_pruefen(fokus)):
        e["fokus"] = f
    ORT.mkdir(parents=True, exist_ok=True)
    with _lock, open(NOTIZEN, "a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def notizen_lesen(n: int = 10, art: str | None = None) -> list[dict]:
    if not NOTIZEN.exists():
        return []
    aus = [json.loads(z) for z in NOTIZEN.read_text().splitlines() if z.strip()]
    if art:
        aus = [e for e in aus if e["art"] == art]
    return aus[-n:][::-1]


# --- KI-Einstellungen --------------------------------------------------------------------------------
def ki_lesen() -> dict:
    try:
        return {**STANDARD_KI, **json.loads(KI.read_text())}
    except (OSError, ValueError):
        return dict(STANDARD_KI)


def ki_schreiben(d: dict) -> dict:
    neu = ki_lesen()
    for k, v in d.items():
        if k in STANDARD_KI and type(v) is type(STANDARD_KI[k]):
            neu[k] = v if not isinstance(v, str) else v[:80]
    ORT.mkdir(parents=True, exist_ok=True)
    with _lock:
        KI.write_text(json.dumps(neu, ensure_ascii=False, indent=1))
    return neu
