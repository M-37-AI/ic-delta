"""Kommandozeile.

  python -m marktdaten quellen                 Übersicht aller Quellen mit Nutzungsbedingungen
  python -m marktdaten probe xetra pretrade    neueste Datei abrufen und nur Kennzahlen zeigen (keine Kurse)
  python -m marktdaten synth ordner/           synthetische Beispieldateien im Originalformat schreiben
  python -m marktdaten dashboard               lokales Dashboard auf http://127.0.0.1:8812/
  python -m marktdaten demo                    Demo mit erfundenen Daten auf http://127.0.0.1:8822/ (Strg+C beendet)
"""
from __future__ import annotations

import argparse
import collections
import gzip
import pathlib
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

from . import synth
from .modell import BuchSeite, iso_aus_ns
from .quellen import QUELLEN


def quellen(_):
    for q in QUELLEN.values():
        print(f"{q.name:10} {q.titel}")
        print(f"{'':10} Inhalt: {q.inhalt} | Tiefe: {q.tiefe} | abrufbar: {q.abrufbar}"
              f"{' | eigener Schlüssel nötig' if q.schluessel else ''}")
        print(f"{'':10} Nutzung: {q.lizenz}\n{'':10} {q.info_url}\n")


def probe(a):
    q = QUELLEN[a.quelle]
    art = a.art or q.arten[0]
    if q.name == "alpaca":
        ende = datetime.now(timezone.utc) - timedelta(minutes=20)
        strom = q.abfrage("trades", ["SPY"], (ende - timedelta(minutes=1)).isoformat(), ende.isoformat())
        datei = "SPY, eine Minute vor 20 Minuten (SIP)"
    else:
        liste = q.dateien(art)
        print(f"{len(liste)} Dateien verfügbar")
        datei, strom, fehlend = None, None, 0
        gross = q.name in ("iex", "gettex")                  # riesige Dateien nur anlesen
        for d in liste[:200]:          # neueste kann noch fehlen; Cboe schreibt nach Handelsschluss keine Pretrade-Dateien
            try:
                strom = list(q.lesen(d, art, max_mb=a.max_mb) if gross else q.lesen(d, art))
                datei = d
                break
            except urllib.error.HTTPError:
                fehlend += 1
        if fehlend:
            print(f"{fehlend} neuere Dateien nicht abrufbar, nehme die erste vorhandene")
        if strom is None:
            sys.exit("keine Datei lesbar")
    typen, instrumente, stufen, zeiten = collections.Counter(), set(), collections.Counter(), []
    for e in strom:
        typen[type(e).__name__] += 1
        instrumente.add(e.instrument)
        zeiten.append(e.ts)
        if isinstance(e, BuchSeite):
            stufen[len(e.stufen)] += 1
    print(f"Datei: {datei}")
    print(f"Ereignisse: {dict(typen)} | Instrumente: {len(instrumente)}")
    if zeiten:
        print(f"Zeitraum: {iso_aus_ns(min(zeiten))} bis {iso_aus_ns(max(zeiten))}")
    if stufen:
        print(f"Buchstufen je Seite: {dict(sorted(stufen.items()))}")


def synth_schreiben(a):
    ziel = pathlib.Path(a.ordner)
    ziel.mkdir(parents=True, exist_ok=True)
    start = synth.jetzt_gerundet()
    pre, post = synth.xetra(start, a.minuten)
    for art, zeilen in (("pretrade", pre), ("posttrade", post)):
        p = ziel / f"SYNTH-DETR-{art}-{start:%Y-%m-%dT%H_%M}.json.gz"
        p.write_bytes(gzip.compress("\n".join(zeilen).encode()))
        print(p, len(zeilen), "Zeilen")
    pre, post = synth.cboe(start, a.minuten)
    for art, zeilen in (("pretrade", pre), ("trade", post)):
        p = ziel / f"SYNTH-eqt_{art}_CEUX_{start:%Y%m%d_%H%M}.csv"
        p.write_text("\n".join(zeilen))
        print(p, len(zeilen), "Zeilen")


def demo_starten(a):
    """Demo zum Ausprobieren: Dashboard, Analysen und (falls Node da ist) Claude-Agent mit erfundenen Daten,
    eigenem Datenordner und eigenen Ports. Keine Abrufe bei Börsen, kein Hintergrunddienst: läuft, bis Strg+C."""
    import os
    import shutil
    import signal
    import socket
    import subprocess
    import time
    basis = a.port
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", basis)) == 0:
            sys.exit(f"Auf Port {basis} läuft schon etwas (die Demo?): http://127.0.0.1:{basis}/")
    daten = pathlib.Path(os.environ.get("MARKTDATEN_DEMO_DATEN", "~/.local/share/marktdaten-demo")).expanduser()
    konfig = pathlib.Path(os.environ.get("MARKTDATEN_DEMO_KONFIG", "~/.config/marktdaten-demo")).expanduser()
    (daten / "analysen").mkdir(parents=True, exist_ok=True)
    konfig.mkdir(parents=True, exist_ok=True)
    ki = daten / "analysen" / "ki.json"
    if not ki.exists():
        ki.write_text('{"routinen": false}')                    # keine täglichen Claude-Läufe in der Demo
    src = pathlib.Path(__file__).resolve().parent.parent
    stopp = daten / ".stopp"                                  # „Demo stoppen“ im Browser legt diese Datei an
    stopp.unlink(missing_ok=True)
    env = {**os.environ, "MARKTDATEN_DEMO": "1", "MARKTDATEN_DEMO_TERMINAL": "1", "MARKTDATEN_DEMO_START": str(time.time()), "MARKTDATEN_PORT": str(basis), "MARKTDATEN_DATEN": str(daten),
           "MARKTDATEN_KONFIG": str(konfig), "PYTHONPATH": os.pathsep.join(filter(None, [str(src), os.environ.get("PYTHONPATH")]))}
    dienste = [("Dashboard", [sys.executable, "-m", "marktdaten", "dashboard", "--port", str(basis), "--host", a.host], None, {}),
               ("Analysen", [sys.executable, "-m", "marktdaten.analysen", "--port", str(basis + 1), "--host", a.host], None, {})]
    agent = src.parent / "agent"
    if shutil.which("node") and (agent / "node_modules").exists():
        dienste.append(("Claude-Agent", [shutil.which("node"), "server.mjs"], agent, {"PORT": str(basis + 2), "HOST": a.host}))
    else:
        print("Claude-Agent übersprungen (Node oder agent/node_modules fehlt: cd agent && npm install)")
    def beenden(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, signal.default_int_handler)            # Strg+C auch, wenn die Shell es sonst ignoriert
    signal.signal(signal.SIGTERM, beenden)                               # kill: Dienste mit beenden statt verwaist lassen
    laeufe = [(n, subprocess.Popen(b, cwd=c, env={**env, **e})) for n, b, c, e in dienste]
    time.sleep(3)
    print(f"\niC Delta DEMO · erfundene Daten, keine echten Kurse\n  http://127.0.0.1:{basis}/\n"
          f"  Daten: {daten}\n  Beenden mit Strg+C\n", flush=True)
    gemeldet = set()
    try:
        while all(p.poll() is None for _, p in laeufe[:2]) and not stopp.exists():   # ohne Dashboard oder Analysen keine Demo
            for n, p in laeufe[2:]:
                if p.poll() is not None and n not in gemeldet:      # Agent ist Zugabe: Demo läuft ohne ihn weiter
                    print(f"{n} hat sich beendet (Code {p.returncode}); die Demo läuft ohne ihn weiter", flush=True)
                    gemeldet.add(n)
            time.sleep(1)
        for n, p in laeufe[:2]:
            if p.poll() is not None:
                print(f"{n} hat sich beendet (Code {p.returncode})")
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in laeufe:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for _, p in laeufe:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        if stopp.exists() and stopp.read_text().strip() == "loeschen":
            if "demo" in daten.name and daten != pathlib.Path("~/.local/share/marktdaten").expanduser():
                shutil.rmtree(daten, ignore_errors=True)
                print("Demo im Browser beendet, Demo-Daten gelöscht:", daten)
            else:
                stopp.unlink(missing_ok=True)
                print("Demo im Browser beendet. Ordner", daten, "nicht gelöscht (Name ohne „demo“).")
        else:
            print("Demo beendet. Neu anfangen: Ordner", daten, "löschen.")


def demo_befehl(a):
    """Szenen für den Dreh im Demo-Betrieb (Datenordner der Demo über MARKTDATEN_DATEN, siehe demo.sh)."""
    if a.was == "starten":
        return demo_starten(a)
    from .live import demo
    if a.was == "arten" or not a.art:
        for k, v in demo.ARTEN.items():
            print(f"{k:14} {v}")
        print("Werte:", ", ".join(x[1] for x in demo.WERTE))
        return
    s = demo.szene(a.art, a.wert or demo.WERTE[0][1], a.prozent, a.minuten, a.seite, a.in_sekunden)
    print(f"Szene {s['art']} für {s['wert']} startet in {a.in_sekunden:.0f} s und dauert {s['dauer']:.0f} s")


def dashboard(a):
    from .dashboard.server import starten
    starten(a.port, a.host)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="marktdaten", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    sub.add_parser("quellen").set_defaults(f=quellen)
    p = sub.add_parser("probe")
    p.add_argument("quelle", choices=QUELLEN)
    p.add_argument("art", nargs="?")
    p.add_argument("--max-mb", type=float, default=20, help="nur IEX und gettex: so viele MB lesen")
    p.set_defaults(f=probe)
    s = sub.add_parser("synth")
    s.add_argument("ordner")
    s.add_argument("--minuten", type=int, default=1)
    s.set_defaults(f=synth_schreiben)
    dm = sub.add_parser("demo", help="Demo mit erfundenen Daten starten; Szenen für den Dreh (über demo.sh aufrufen)")
    dm.add_argument("was", nargs="?", default="starten", choices=["starten", "szene", "arten"])
    dm.add_argument("--port", type=int, default=8822, help="Basisport der Demo (Analysen +1, Agent +2)")
    dm.add_argument("--host", default="127.0.0.1", help="kommagetrennt, z. B. 127.0.0.1,<Tailscale-IP>")
    dm.add_argument("art", nargs="?")
    dm.add_argument("wert", nargs="?")
    dm.add_argument("--prozent", type=float, default=2.0)
    dm.add_argument("--minuten", type=float, default=3.0)
    dm.add_argument("--seite", choices=["kauf", "verkauf"], default="kauf")
    dm.add_argument("--in", dest="in_sekunden", type=float, default=0)
    dm.set_defaults(f=demo_befehl)
    d = sub.add_parser("dashboard")
    d.add_argument("--port", type=int, default=__import__("marktdaten.ports", fromlist=["x"]).DASHBOARD)
    d.add_argument("--host", default="127.0.0.1", help="kommagetrennt, z. B. 127.0.0.1,<Tailscale-IP>")
    d.set_defaults(f=dashboard)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
