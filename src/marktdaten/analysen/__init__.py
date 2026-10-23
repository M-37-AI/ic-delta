"""Analysen zum Orderbuch: Handelskosten, Druck im Buch, Aufbau, Handelsplätze, Mikrostruktur, Strategietests.

Läuft als eigener kleiner Dienst neben dem Dashboard (Reiter „Analysen“) und hält eigene Rohdaten:
die Minutendateien von Xetra, Tradegate und Cboe Europe, gefiltert auf die deutschen Werte der Watchlist.

  python -m marktdaten.analysen --port 8813 --host 127.0.0.1,<Tailscale-IP>
"""
