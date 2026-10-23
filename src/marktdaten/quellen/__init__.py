"""Alle Quellen an einer Stelle."""
from .alpaca import ALPACA
from .cboe_europe import CBOE
from .deutsche_boerse import EUREX, FRANKFURT, TRADEGATE, XETRA
from .eodhd import EODHD
from .euronext import EURONEXT
from .iex import IEX
from .nasdaq_nordic import NORDIC
from .regionalboersen import BOEAG, EQUIDUCT, GETTEX, NGM

QUELLEN = {q.name: q for q in (XETRA, TRADEGATE, FRANKFURT, EUREX, BOEAG, GETTEX, EQUIDUCT,
                               CBOE, EURONEXT, NORDIC, NGM, IEX, ALPACA, EODHD)}

# Kurzprofil für die Übersicht: (Gruppe, Verzögerung, Nutzung in zwei, drei Wörtern).
# Die vollständige Bedingung steht in Quelle.lizenz.
PROFIL = {
    "xetra": ("Deutschland", "15 Min.", "nicht kommerziell"),
    "tradegate": ("Deutschland", "15 Min.", "nicht kommerziell"),
    "frankfurt": ("Deutschland", "15 Min.", "nicht kommerziell"),
    "eurex": ("Deutschland", "15 Min.", "nicht kommerziell"),
    "boeag": ("Deutschland", "15 Min.", "nur privat"),
    "gettex": ("Deutschland", "15 Min.", "nur privat"),
    "equiduct": ("Europa", "15 Min.", "frei"),
    "cboe": ("Europa", "15 Min.", "frei mit Hinweis"),
    "euronext": ("Europa", "15 Min.", "frei, Quelle nennen"),
    "nordic": ("Europa", "15 Min.", "nicht kommerziell"),
    "ngm": ("Europa", "15 Min.", "nicht kommerziell"),
    "iex": ("USA", "Vortag", "Bedingungen IEX"),
    "alpaca": ("USA", "live", "eigenes Konto"),
    "eodhd": ("Deutschland", "Tagesende", "eigenes Konto, privat"),
}
