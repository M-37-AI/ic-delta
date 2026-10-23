# iC Delta · AI Engine

Einheitlicher Zugang zu **kostenlosen, offiziellen Marktdatenquellen**: Orderbücher, Kurse und Abschlüsse
von Xetra, Tradegate, Cboe Europe, Euronext, Nasdaq Nordic, den Regionalbörsen, IEX und Alpaca.

Das Repo enthält **nur Code, keine Daten**. Jeder ruft die Daten selbst bei der Quelle ab und
akzeptiert dabei deren Bedingungen. Die meisten Quellen brauchen weder Konto noch Schlüssel.

## Was drin ist

| Quelle | Region | Inhalt | Tiefe | abrufbar | Schlüssel |
|---|---|---|---|---|---|
| `xetra` | DE | Orderbuch und Abschlüsse, ~5.000 Werte | **5 Stufen** mit Orderanzahl, Retail-Buch getrennt | ~2 Tage | – |
| `tradegate` | DE | Kurse und Abschlüsse, ~16.000 Werte | bester Kurs | ~2 Tage | – |
| `frankfurt` | DE | Kurse und Abschlüsse, ~28.000 Werte | bester Kurs | ~2 Tage | – |
| `eurex` | DE | Abschlüsse in Optionen und Futures | – | ~2 Tage | – |
| `boeag` | DE | Düsseldorf, Hamburg, Hannover, Quotrix | bester Kurs | 24 h | – |
| `gettex` | DE | Kurse und Abschlüsse, alle 15 Min. (tagsüber > 600 MB je Datei) | bester Kurs | kurz | – |
| `equiduct` | EU | Börse Berlin, Abschlüsse des Tages | bester Kurs | laufender Tag | – |
| `cboe` | EU/UK | Cboe Europe BXE, CXE, DXE inkl. DAX-Werten | **bis 5 Stufen** | **nur laufender Tag** | – |
| `euronext` | EU | 7 Märkte, nur Abschlüsse | – | laufender und Vortag | – |
| `nordic` | Nord | Nasdaq Stockholm, Kopenhagen, Helsinki, Baltikum | bester Kurs | 48 h | – |
| `ngm` | Nord | Nordic Growth Market | bester Kurs | 72 h | – |
| `iex` | US | IEX DEEP vom Vortag (pcapng, ~12 GB/Tag, wird gestreamt) | alle Preisstufen | Archiv | – |
| `alpaca` | US | Trades, Quotes, Kerzen; live IEX, Historie aller US-Börsen | bester Kurs | viele Jahre | **ja** |

Dazu Referenzdaten in `marktdaten.quellen.referenz`: FINRA-Leerverkaufsvolumen, Nasdaq-Symbolverzeichnis,
SEC-Fundamentaldaten (braucht `MARKTDATEN_KONTAKT`) und EZB-Wechselkurse.

Bewusst **nicht** enthalten: Quellen, deren Bedingungen den automatischen Abruf verbieten (z. B. Yahoo, LS Exchange),
und Quellen hinter Bot-Sperren (z. B. Börse Stuttgart, Aquis). Diese Sperren werden nicht umgangen.

## Nutzungsbedingungen

**Der Code steht unter MIT. Die Daten nicht.** Für jede Quelle gelten ihre eigenen Bedingungen,
und du bist selbst dafür verantwortlich, sie einzuhalten. Kurzfassung (Stand Oktober 2026, keine Rechtsberatung):

| Quelle | Bedingung |
|---|---|
| Deutsche Börse (Xetra, Frankfurt, Tradegate, Eurex) | kostenlos, solange die Daten nicht kommerzialisiert werden; KI-Lösungen brauchen laut Market Data Policy eine Non-Display-Lizenz |
| Cboe Europe | mit dem Abruf gilt der „Delayed Market Data Disclaimer“ |
| Euronext | frei; bei Weitergabe Euronext nennen und deren Hinweis beifügen |
| Nasdaq Nordic, NGM | nicht-kommerzielle Nutzung kostenlos |
| BÖAG, gettex | nur natürliche Personen, ausschließlich privat, keine Weitergabe |
| Equiduct | kostenlos veröffentlicht, keine weiteren Bedingungen gefunden |
| IEX | Nutzungsbedingungen von IEX beachten |
| Alpaca | eigenes Konto; Daten nur für die eigene Nutzung |
| EODHD | eigenes Konto; Gratisplan nur private Nutzung, letztes Jahr, 20 Abrufe am Tag |

`python -m marktdaten quellen` zeigt alle Bedingungen mit Link zur offiziellen Seite.
**Veröffentliche keine abgerufenen Daten** (auch nicht als Screenshot, Chart oder Datei), ohne vorher die Rechte zu klären.

## Installation

```bash
git clone <repo-url> && cd ic-delta
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # ohne Abhängigkeiten
pip install -e ".[live]"    # für den Alpaca-Live-Strom (websockets)
```

Python 3.11 oder neuer. Der Kern nutzt nur die Standardbibliothek.

## Schnellstart

```bash
marktdaten quellen                     # Übersicht mit Bedingungen
marktdaten probe xetra pretrade        # neueste Datei abrufen, nur Kennzahlen ausgeben
marktdaten probe iex --max-mb 20       # nur die ersten 20 MB des Tagesmitschnitts
marktdaten synth beispiel/             # synthetische Dateien im Originalformat
```

`probe` gibt bewusst keine Kurse aus, nur Anzahl, Instrumente, Zeitraum und Buchtiefe.

```python
from marktdaten.quellen import XETRA, CBOE
from marktdaten.buch import Orderbuch

buch = Orderbuch()
for e in XETRA.neueste("pretrade"):
    k = buch.anwenden(e)
    if k and k[1] == "DE0007164600":        # SAP
        print(buch.spread_bp(k), buch.ungleichgewicht(k))
```

## Einheitliches Format

Jede Quelle liefert vier Ereignistypen (`marktdaten.modell`):

| Typ | Bedeutung |
|---|---|
| `Trade` | Abschluss: Preis, Menge, Zeit, Handelsplatz (MIC), Kennzeichen |
| `Quote` | bester Kauf-/Verkaufskurs; `None` = in dieser Meldung nicht enthalten |
| `BuchSeite` | komplette Momentaufnahme einer Buchseite (Xetra) |
| `BuchStufe` | Änderung einer Preisstufe, Menge 0 = Stufe entfällt (Cboe, IEX) |

Zeitstempel sind Nanosekunden seit 1970 in UTC. `instrument` ist in Europa die ISIN, in den USA der Ticker.
`Orderbuch` schreibt beide Buchformate fort und berechnet Spread und Ungleichgewicht.

**Wichtig bei Xetra:** Die Dateien enthalten zwei Bücher, das normale und das Retail-Buch (`buch="retail"`).
Beide können sich kreuzen und müssen getrennt betrachtet werden. Meldungen enthalten oft nur eine Seite.

## Alpaca

Kostenloses Konto bei [alpaca.markets](https://alpaca.markets), Paper Trading genügt. Schlüssel als Umgebungsvariablen
setzen oder `.env.example` nach `~/.config/marktdaten/alpaca.env` kopieren und ausfüllen (`chmod 600`).
Die Schlüssel gehören nie ins Repo.

```python
from marktdaten.quellen import ALPACA
trades = ALPACA.abfrage("trades", ["AAPL"], "2026-10-19T14:00:00Z", "2026-10-19T14:01:00Z")
```

## EODHD (Tageskerzen-Historie für deutsche Werte)

Ein neuer deutscher Wert auf der Watchlist hat sonst nur den letzten Handelstag. Mit einem kostenlosen Konto bei
[eodhd.com](https://eodhd.com/register) holt iC Delta je Wert ein Jahr Tageskerzen (Xetra) und die Splits nach.
Den Schlüssel als `EODHD_API_TOKEN` in `~/.config/marktdaten/eodhd.env` eintragen (`chmod 600`). Zeitraum und
Abrufe je Tag stehen in den Einstellungen (`tage_eodhd`, `eodhd_max_tag`). Eigene Xetra-Daten haben immer Vorrang.

## Datenqualität

**Vor jeder Analyse lesen:** [DATENQUALITAET.md](DATENQUALITAET.md). Wichtigster Punkt: Bei US-Werten ist das Volumen
der letzten 15 Minuten vorläufig (nur IEX, rund 4 % des Markts) und wird danach durch das aller Börsen ersetzt.
Jede Kerze im Speicher trägt dafür ihre Herkunft in der Spalte `q`; die Hinweise stehen auch als Tabelle
`datenhinweise` in der Datenbank.

## Indikatoren in iC Charts

32 Indikatoren (Durchschnitte, Bänder, Ichimoku, Parabolic SAR, ADX, Volumenprofil, Delta aus echten Abschlüssen …),
alle als eigener Code nach den Erstbeschreibungen und gegen TA-Lib geprüft. Verzeichnis, Formeln und Quellen:
[INDIKATOREN.md](INDIKATOREN.md).

## Demo ausprobieren

```bash
python -m marktdaten demo        # Dashboard, Analysen und Agent mit erfundenen Daten, http://127.0.0.1:8822/
```

Die Demo erzeugt Kurse, Orderbücher und Abschlüsse selbst (nie aus echten Kursen abgeleitet), ruft nichts bei Börsen ab
und hat einen eigenen Datenordner (`~/.local/share/marktdaten-demo`). Sie läuft, bis du im Terminal Strg+C drückst.
Gut zum Ausprobieren, Üben und Vorführen, ohne Daten weiterzugeben.

## Synthetische Daten

`marktdaten.synth` erzeugt Dateien im exakten Format von Xetra und Cboe, mit Zufallspfad, Spread und Orderbuch.
Alle ISINs beginnen mit `ZZ`, damit sie nie mit echten verwechselt werden. Gedacht für Tests, Demos und Videos.

## Tests

```bash
python -m unittest discover -s tests
node tests/js/test_indikatoren.cjs        # Indikatoren von iC Charts gegen TA-Lib-Referenzwerte
```

Die Tests laufen offline und nur mit erfundenen Werten.

## Haftungsausschluss

iC Delta ist ein privates Lern- und Bastelprojekt. Es wird kostenlos und ohne jede Gewähr bereitgestellt; die Nutzung
erfolgt auf eigenes Risiko.

- **Keine Anlageberatung:** Kurse, Auswertungen, Formationen, Alarme und Einschätzungen von Claude sind keine Empfehlung
  zum Kauf oder Verkauf von Wertpapieren oder Kryptowerten.
- **Nur Papierhandel:** Orders in iC Delta sind ausschließlich Papierhandel mit Spielgeld zu Übungs- und Demozwecken.
  Es gibt keine Verbindung zu einem Broker; es wird kein echtes Geld bewegt.
- **Daten ohne Gewähr:** Daten können fehlen, verzögert oder falsch sein, auch durch Fehler in iC Delta.
- **Keine Haftung:** Für Schäden aus der Nutzung, insbesondere finanzielle Verluste, wird keine Haftung übernommen,
  soweit gesetzlich zulässig.
- **Deine Verantwortung:** Die Bedingungen der Datenquellen und Dienste (siehe oben) hältst du selbst ein.

## Lizenz

MIT für den Code, siehe [LICENSE](LICENSE). Keine Gewähr, keine Anlageberatung.
Fremde Bestandteile (Lightweight Charts, Apache 2.0) und Entwicklungswerkzeuge: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
