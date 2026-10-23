"""Start-Watchlist: wird nur beim ersten Start in einen leeren Speicher übernommen.

Danach gilt, was im Dashboard hinzugefügt oder entfernt wird.
"""
from __future__ import annotations

START = [
    ("DE0007164600", "SAP", "SAP", "DE"),
    ("DE0007236101", "SIE", "Siemens", "DE"),
    ("DE0008404005", "ALV", "Allianz", "DE"),
    ("DE0005557508", "DTE", "Deutsche Telekom", "DE"),
    ("DE0007030009", "RHM", "Rheinmetall", "DE"),
    ("AAPL", "AAPL", "Apple", "US"),
    ("NVDA", "NVDA", "Nvidia", "US"),
    ("MSFT", "MSFT", "Microsoft", "US"),
    ("AMZN", "AMZN", "Amazon", "US"),
    ("TSLA", "TSLA", "Tesla", "US"),
]

# Alpaca erlaubt im Gratis-Tarif bis zu 30 Symbole im Live-Strom.
MAX_US = 30
