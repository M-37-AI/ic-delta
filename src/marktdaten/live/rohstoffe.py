"""Rohstoffe, live: Binance-Futures-WebSocket (öffentlich, ohne Schlüssel) für die Rohstoff-Werte der Watchlist.

Dieselbe Arbeitsweise wie der Krypto-Sammler (Minutenkerzen, beim Hinzufügen Tages- und Minutenhistorie), aber auf den
Perpetual-Kontrakten von Binance Futures (WTI, Brent, Erdgas, Gold, Silber, Platin, Palladium, Kupfer). Das sind Kontrakte
in USDT und kein Spotpreis. Ohne Orderbuch. Stand der Futures-Streams: Kerzen laufen über den Pfad /market/stream
(der alte Pfad /stream blieb am 21.10.2026 stumm).
"""
from __future__ import annotations

from .krypto import KryptoSammler


class RohstoffSammler(KryptoSammler):
    MARKT = "ROHSTOFFE"
    REST_URL = "https://fapi.binance.com/fapi/v1/"
    STROM_URL = "wss://fstream.binance.com/market/stream?streams="
    DIENST = "binance_futures"
    HERKUNFT = "binance_f"
    NETZ = "Binance Futures"
    TIEFE = False
