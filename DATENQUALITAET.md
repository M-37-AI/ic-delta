# Datenqualität: was man vor jeder Analyse wissen muss

Diese Hinweise gelten für alle Auswertungen, ob von Hand, per Skript oder durch eine KI. Sie stehen
zusätzlich als Tabelle `datenhinweise` in der Datenbank (`~/.local/share/marktdaten/marktdaten.db`),
damit Analysen sie direkt mitlesen können.

## 1. US-Volumen: zwei Quellen, nur `sip` ist vollständig

Jede Kerze trägt in der Spalte `q` ihre Herkunft:

| q | Herkunft | Volumen |
|---|---|---|
| `sip` | alle US-Börsen zusammen (Nasdaq, NYSE, IEX, …) | vollständig |
| `iex` | nur die Börse IEX, live | **rund 4 % des Gesamtvolumens, vorläufig** |
| `xetra` | Xetra-Hauptsitzung, 15 Minuten verzögert | Xetra, ohne andere deutsche Plätze |
| `binance` | Binance Spot | Binance |
| `eodhd` | Xetra-Tageskerzen von EODHD (nur `kerzen_1d`, Historie deutscher Werte) | Xetra, von EODHD splitbereinigt |

Warum: Alpaca liefert im Gratis-Tarif live nur IEX. Daten aller Börsen (SIP) gibt es gratis erst,
wenn sie älter als 15 Minuten sind. Der Dienst ersetzt deshalb jede Minute die IEX-Kerzen, die
älter als 15 Minuten sind, durch die SIP-Kerzen.

**Folge für Analysen:**
- Die jeweils letzten ~15 Minuten eines US-Werts haben zu wenig Volumen (`q = iex`).
- Beim Wechsel `iex` → `sip`, beim Nachladen und am Tageswechsel springt das Volumen.
  **Diese Sprünge sind kein Marktsignal.**
- Für Volumen, VWAP, Volumenprofile und alles Ähnliche nur Kerzen mit `q = 'sip'` verwenden,
  z. B. `SELECT … FROM kerzen_1m WHERE id = 'AAPL' AND q = 'sip'`.
- Der Kurs (o, h, l, c) einer `iex`-Kerze entspricht praktisch dem Gesamtmarkt; einzelne Ausreißer
  in dünnen Minuten (vor- und nachbörslich) sind möglich.

## 2. Deutsche Werte: 15 Minuten verzögert

- Die Deutsche Börse veröffentlicht kostenlos nur mit 15 Minuten Verzögerung (MiFIR Art. 13).
  Die Zeitstempel sind die echten Handelszeiten, nicht die Abrufzeit.
- Abschlüsse außerhalb der Hauptsitzung (nach 17:30 Uhr, MiFID `mmtTradingMode = 4`) gehen nicht
  in Kerzen und Kurs ein. Sie stehen aber in der Tabelle `abschluesse`.
- Kerzen und Kurs kommen nur von Xetra. Tradegate und Cboe Europe stehen zum Vergleich in den
  Abschlüssen bzw. im Orderbuch der Detailansicht.
- Fehlende Minuten bedeuten nicht „kein Handel“: Die Börse bremst Abrufe (HTTP 429), oder der Dienst
  lief nicht.

## 3. Orderbuch

- `buch_1m` ist das je Minute verdichtete Buch des Hauptplatzes:
  `bid`/`ask` = letzter bester Kurs der Minute, `spread_bp` = Mittelwert, `tiefe_bid`/`tiefe_ask` =
  Wert der besten 5 Stufen in Währung, `ungleich` = Mittel von (Kauf − Verkauf) / Summe über die besten
  5 Stufen, `updates` = Anzahl Buchänderungen in der Minute.
- Xetra führt ein getrenntes **Retail-Buch**, das sich mit dem regulären kreuzen kann. `buch_1m` enthält
  nur das reguläre Buch. Abends (nach 17:30 Uhr) ist das reguläre Buch leer.
- Krypto: Live-Buch aus Binance `depth20@100ms` (20 Stufen, zehnmal je Sekunde), in den Kursstand geht höchstens ein Stand je Sekunde.
- `buch_1s` (seit 21.10.2026, nur Krypto): Orderflow je Sekunde für die Signal-Schicht: Buch am Sekundenende (5 Stufen plus Summe 6–20),
  Order Flow Imbalance (Stufe 1 und Stufen 1–5), aggressives Kauf-/Verkaufsvolumen aus `aggTrade`, Anzahl Trades, letzter Preis.
  Zeitbasis ist die Empfangszeit. Für Xetra entsteht dasselbe Raster beim Lesen der Rohdateien in den Analysen (15 Min. verzögert).
- `liquidationen` und `perp_1m` (Binance Futures desselben Paars): Zwangsliquidationen (höchstens eine je Sekunde und Paar gemeldet,
  Kaskaden untererfasst), Markpreis, Indexpreis, Funding, Open Interest je Minute. Kein Spotpreis.
- Signale der Analysen (`signale.db`) sind Hypothesen mit Messwert (Markout 5/30/60/300 s, netto nach halbem Spread und Gebühr,
  Krypto 10 bp Taker). Schwellen sind rollierende Perzentile je Wert, keine festen Zahlen.
- `buch_roh` enthält bei Aufnahmen jede einzelne Änderung, so wie sie geliefert wurde: Xetra als
  komplette Buchseite (5 Stufen), Cboe als einzelne Stufe.

## 4. Zeit und Verdichtung

- Alle Zeitstempel sind UTC: Kerzen in Sekunden (Beginn der Minute), Abschlüsse und Aufnahmen in
  Nanosekunden.
- 1-Minuten-Kerzen älter als die Aufbewahrung (Standard 365 Tage) werden zu `kerzen_15m` und
  `kerzen_1d` verdichtet. Für diesen Zeitraum sind feinere Analysen dann nicht mehr möglich.
- Die Herkunft `q` einer verdichteten Kerze ist `sip`, wenn alle Minuten `sip` waren, sonst die Liste
  der beteiligten Herkünfte.

## 5. Nutzungsrechte

Die Daten sind nur für den privaten Gebrauch lizenziert (siehe README). Ergebnisse einer Analyse
mit echten Kursen nicht veröffentlichen, ohne die Rechte zu klären.
