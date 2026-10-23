import argparse
import sys

if len(sys.argv) > 1 and sys.argv[1] == "telegram":
    # Für den Nutzer im eigenen Terminal: Chat-ID ermitteln und Testnachricht senden
    from .alarme import telegram_einrichten
    telegram_einrichten()
    sys.exit()

if len(sys.argv) > 1 and sys.argv[1] == "pruefstein":
    # Prüfstein der Formationen einmal rechnen und die Übersicht ausgeben
    from .pruefstein import rechnen
    d = rechnen()
    print(f"{d['gesamt']['ereignisse']} Ereignisse in {d['dauer_s']} s, Ziel erreicht {d['gesamt']['ziel_quote']} %")
    for z in d["zeilen"]:
        print(f"{z['name']:32} {z['richtung']:8} n={z['n']:4} Ziel {z['ziel_quote']} %  Fehl {z['fehl_quote']} %  20 Tage {z['r20']} %  Vorsprung {z['vorsprung_r20']}")
    sys.exit()

if len(sys.argv) > 1 and sys.argv[1] == "signale":
    # Signal-Lage und Statistik eines Werts ausgeben: python -m marktdaten.analysen signale <wert> [tag]
    from . import signale
    d = signale.lage(sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT", schritt=60, tag=sys.argv[3] if len(sys.argv) > 3 else None)
    print(signale.absatz(d))
    print()
    for s in d.get("signale", [])[-15:]:
        m = s.get("markout") or {}
        print(f"{signale._uhr(s['t'])} {s['name']:28} {'↑' if s['richtung'] > 0 else '↓' if s['richtung'] < 0 else '•'} Score {s['score']:.2f}  "
              f"m30 {m.get('m30')} m60 {m.get('m60')} netto60 {m.get('netto60')}  {s['begruendung'][:90]}")
    print()
    for z in d.get("statistik", []):
        print(f"{z['name']:28} n={z['n']:4} Treffer 30 s {z['treffer30']} %  60 s {z['treffer60']} %  netto 30/60/300 s {z['netto30_bp']} / {z['netto60_bp']} / {z['netto300_bp']} bp")
    sys.exit()

from .server import starten

a = argparse.ArgumentParser(prog="python -m marktdaten.analysen", description="Reiter Analysen: eigener kleiner Dienst, liest nur")
a.add_argument("--port", type=int, default=__import__("marktdaten.ports", fromlist=["x"]).ANALYSEN)
a.add_argument("--host", default="127.0.0.1", help="kommagetrennt, z. B. 127.0.0.1,<Tailscale-IP>")
x = a.parse_args()
starten(x.port, x.host)
