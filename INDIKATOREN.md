# Indikatoren in iC Charts

Alle Indikatoren sind **eigener Code** (MIT, wie das Repo). Gerechnet wird in
`src/marktdaten/dashboard/static/ic-charts/indicators.js`, beschrieben (Einstellungen, Ort, Linien, Quelle) in
`indikator-katalog.js`. Flächen und das Volumenprofil zeichnet `indikator-zeichnen.js` über die Plugin-Schnittstelle
von Lightweight Charts.

## Grundsätze

- **Formeln, kein fremder Code.** Rechenvorschriften sind nicht urheberrechtlich geschützt, Quelltext schon. Jede Formel
  ist nach der Erstbeschreibung bzw. der Lehrbuchdefinition umgesetzt (Quelle in der Tabelle). Es wurde **kein Code**
  aus TradingView (Pine Script, auch nicht aus Community-Skripten), Highcharts oder anderen Bibliotheken übernommen.
- **Unabhängig geprüft.** `tests/js/test_indikatoren.cjs` vergleicht die Ergebnisse mit
  [TA-Lib](https://ta-lib.org/) (BSD-Lizenz). TA-Lib ist nur ein Prüfwerkzeug: Es wird nicht mitgeliefert und vom
  Produkt nicht benutzt. Die Referenzwerte liegen in `tests/daten/indikatoren_referenz.json` (frei erzeugte Kerzen,
  keine Marktdaten) und werden mit `tests/werkzeuge/referenz_talib.py` erzeugt.
- **Gleiche Zahlen in Python.** `tests/test_indikator_referenz.py` prüft die Python-Indikatoren der Analysen
  (SMA, RSI, EMA, ATR) gegen dieselben Referenzwerte.
- **Datenqualität vor schöner Linie.** Volumen-Indikatoren blenden bei US-Aktien die Kerzen der letzten 15 Minuten aus
  (dort gibt es nur IEX-Volumen, siehe `DATENQUALITAET.md`) und sagen das in der Legende.
- **Namen.** Indikatoren heißen wie in der Fachliteratur. Markennamen Dritter werden nicht als Produktname benutzt
  (z. B. „Squeeze“ statt „TTM Squeeze“). „Bollinger Bands“ ist eine eingetragene Marke von John Bollinger; der Name
  beschreibt hier nur die Methode.

## Verzeichnis

| Typ | Name | Formel (Kurzform) | Quelle |
|---|---|---|---|
| `sma` | SMA | Durchschnitt der letzten n Werte | Lehrbuch |
| `ema` | EMA | α = 2/(n+1), Start = SMA der ersten n Werte | Lehrbuch |
| `wma` | WMA | Gewichte n, n−1, …, 1 | Lehrbuch |
| `hma` | HMA | WMA(2·WMA(n/2) − WMA(n), √n) | Alan Hull, 2005 |
| `bb` | Bollinger-Bänder | SMA(n) ± k·σ (Grundgesamtheit) | John Bollinger, „Bollinger on Bollinger Bands“, 2001 |
| `keltner` | Keltner-Kanal | EMA(n) ± mult·ATR(m) | Chester Keltner, 1960; ATR-Form nach Linda Bradford Raschke |
| `donchian` | Donchian-Kanal | höchstes Hoch / tiefstes Tief über n | Richard Donchian, 1960er |
| `ichimoku` | Ichimoku Kinko Hyo | Tenkan/Kijun = Mitte von Hoch/Tief über 9/26, Senkou A/B 26 Perioden voraus, Chikou zurück | Goichi Hosoda, 1969 |
| `psar` | Parabolic SAR | SAR += AF·(EP − SAR), AF 0,02 … 0,2 | J. Welles Wilder, „New Concepts in Technical Trading Systems“, 1978 |
| `st` | SuperTrend | (H+T)/2 ± Faktor·ATR, nachziehende Bänder | ATR nach Wilder 1978; freie, verbreitete Konstruktion |
| `vwap` | VWAP mit Anker und Bändern | Σ(TP·V)/ΣV ab Anker, Bänder ± k·σ (volumengewichtet) | Lehrbuch / Varianzformel |
| `pivots` | Pivot-Punkte | P = (H+T+S)/3 des Vorzeitraums, R/S klassisch oder Fibonacci (0,382/0,618/1,0) | Floor-Trader-Methode |
| `linreg` | Regressionskanal | kleinste Quadrate über n Kerzen ± k·σ der Abweichungen | Gauß/Legendre |
| `zigzag` | ZigZag | Wendepunkt ab x % Gegenbewegung, letzter Schenkel vorläufig | Arthur Merrill, „Filtered Waves“, 1977 |
| `vprofil` | Volumenprofil | Volumen je Preisstufe, POC, Wertebereich 70 % | J. Peter Steidlmayer, Market Profile (CBOT), 1985 |
| `rsi` | RSI | 100 − 100/(1 + Ø Gewinn/Ø Verlust), Wilder-Glättung | J. Welles Wilder, 1978 |
| `macd` | MACD | EMA(12) − EMA(26), Signal EMA(9), Histogramm | Gerald Appel, 1970er |
| `stoch` | Stochastik | (S − LL)/(HH − LL), %K und %D geglättet | George Lane, 1950er |
| `willr` | Williams %R | −100·(HH − S)/(HH − LL) | Larry Williams, 1973 |
| `cci` | CCI | (TP − SMA)/(0,015 · mittlere Abweichung) | Donald Lambert, 1980 |
| `roc` | ROC | 100·(x − x₋ₙ)/x₋ₙ | Lehrbuch |
| `dmi` | ADX / DMI | +DI/−DI aus gerichteter Bewegung, ADX = Wilder-Glättung von DX | J. Welles Wilder, 1978 |
| `aroon` | Aroon | 100·(n − Kerzen seit Hoch/Tief)/n | Tushar Chande, 1995 |
| `atr` | ATR | Wilder-Glättung der True Range (auch in % vom Kurs) | J. Welles Wilder, 1978 |
| `bbw` | BB-Breite / %B | (Oben − Unten)/Mitte bzw. (S − Unten)/(Oben − Unten) | John Bollinger, 2001 |
| `squeeze` | Squeeze | BB innerhalb Keltner; Schwung = Regressionsendpunkt von S − Mittel(Donchian-Mitte, SMA) | Idee nach John Carter, „Mastering the Trade“, 2005 |
| `obv` | OBV | ± Volumen je nach Schlussrichtung | Joe Granville, 1963 |
| `mfi` | MFI | RSI-Formel auf TP·Volumen | Gene Quong und Avrum Soudack, 1989 |
| `cmf` | CMF | Σ(((S−T)−(H−S))/(H−T)·V)/ΣV | Marc Chaikin |
| `delta` | Delta | Kauf- minus Verkaufsvolumen je Kerze und kumuliert | Tick-Regel nach Lee und Ready, 1991; Krypto: Binance-Taker-Volumen |
| `rs` | Relative Stärke | (Kurs/Vergleich), Start = 100 | Lehrbuch (Kursverhältnis, kein RSI) |
| `btcd` | BTC-Dominanz | Anteil Bitcoin an der Marktkapitalisierung | CoinMarketCap, CoinGecko |

## Abweichungen von TA-Lib (bewusst)

- **ADX/DMI:** TA-Lib beginnt die Wilder-Summen mit n−1 statt n Werten. Wir rechnen wie Wilder. Der Unterschied
  klingt ab und ist nach rund 400 Kerzen kleiner als ein Millionstel.
- **OBV:** TA-Lib startet beim Volumen der ersten Kerze, wir bei 0. Der Verlauf ist gleich.
- **ATR:** Erster Wert = Durchschnitt der ersten n True Ranges ab der zweiten Kerze (wie Wilder und TA-Lib). Bis
  Oktober 2026 zählte iC Charts die erste Kerze mit; dadurch verschiebt sich der Start um eine Kerze.

## Orderflow (Delta, Volumenprofil)

- **Xetra:** alle gespeicherten Xetra-Abschlüsse (Hauptplatz). Die Börsendateien nennen die Aggressor-Seite nicht,
  deshalb ordnet die Tick-Regel jeden Abschluss ein (Preis höher = Kauf, tiefer = Verkauf, gleich = wie zuvor).
  Auktionen (Eröffnung, Mittag, Schluss) haben keine Aggressor-Seite und zählen nur zum Volumen.
- **US:** nur IEX-Abschlüsse, rund 4 % des US-Volumens. Das Profil ist eine Stichprobe und als solche beschriftet.
- **Krypto:** Binance liefert das Taker-Kaufvolumen je Kerze, also die echte Aggressor-Seite.
- **Ohne Abschlüsse** (ältere Tage, Testmodus) wird das Profil aus Kerzen geschätzt: Das Volumen einer Kerze wird
  gleichmäßig auf ihre Spanne verteilt. Die Legende sagt, welche Grundlage gerade gilt.
- Schnittstelle des Kerns: `GET /ic-charts/data/orderflow?sym=SAP` (Minutenwerte) und
  `…&profil=1&von=<Unix>&bis=<Unix>&stufen=40` (Profil). Liest nur die Tabelle `abschluesse`, ruft nichts ab.

## Bedienung

- Kopfzeile: Jeder aktive Indikator ist ein Chip. Klick öffnet die Einstellungen (Werte, Farbe, Entfernen),
  „+ Indikator“ öffnet die Auswahl mit Suche. Derselbe Typ kann mehrfach aktiv sein.
- URL: `?ind=ema:9,ema:200,bb:20:2.5,rsi` (Werte in der Reihenfolge der Einstellungen; alte Kurz-IDs gelten weiter).
- Mini-Script: `wma hma roc linreg bbwidth bbpercent kcupper kclower dcupper dclower psar adx plusdi minusdi aroonup
  aroondown willr cci obv mfi cmf tenkan kijun` zusätzlich zu den bisherigen Funktionen.
- Claude: `list_indicators` (Verzeichnis), `set_indicators` mit `add`, `update`, `remove`; `get_state` liefert alle
  Exemplare mit ihren Werten.
