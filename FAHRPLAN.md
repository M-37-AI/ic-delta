# Fahrplan iC Delta: Funktionen nach der Recherche vom 20.10.2026

Grundsatz: AI first. Funktionen, die Nutzer von TradingView, Bookmap und KI-Tools kennen, nur dann,
wenn sie zu unseren Daten passen. Lieber wenige, gute Funktionen („Mehr Funktionen machen es schlechter“).

## Reihenfolge (Empfehlung)

1. **MCP-Server für iC Delta:** Claude (Desktop/Code) fragt die eigenen Daten direkt ab
   (Kerzen, Abschlüsse, Buch je Minute, Rohdateien). Die Tabelle `datenhinweise` geht immer mit.
2. **Telegram:** Alarme in natürlicher Sprache (KI übersetzt in eine Regel, der Sammler prüft laufend)
   und KI-Tagesbericht über die Watchlist. Baut auf PLAN-EINRICHTUNG.md auf.
3. **Liquiditäts-Heatmap und Handelsplatz-Vergleich:** Orderbuch im Zeitverlauf (Xetra 15 Min. verzögert,
   Krypto live) und „wo ist es gerade am günstigsten“ (Xetra, Tradegate, Cboe). Mit dem Reiter Analysen abstimmen.
4. **Papierhandel mit KI-Journal:** simulierte Orders, Ausführungen im Chart, KI bewertet jeden Trade,
   Wochenrückblick. Kein echtes Handeln.
5. **Strategie-Labor:** Strategie in Worten → Mini-Script (iC Charts) → Backtest mit zurückgehaltenem
   Datenteil gegen Überanpassung.

## Weitere Kandidaten

- Mustererkennung mit **ehrlicher Trefferquote** aus der eigenen Historie statt Marketing-Konfidenz
- Volumenprofil und Delta (Delta nur als Näherung über die Tick-Regel, so gekennzeichnet:
  die Börsendateien nennen den Aggressor nicht)
- Depot-Ansicht mit Einstiegskursen, ggf. Anschluss an den Portfolio-Agenten
- Mehrere Charts nebeneinander in iC Charts
- Termine (Quartalszahlen, Dividenden) aus offiziellen Quellen, z. B. SEC EDGAR

## Bewusst nicht

- Echte Orderausführung (Regulierung, Anlageberatung)
- Signale als Botschaft („Kaufen!“)
- Konfidenzwerte ohne Nachweis

## Quellen (Auswahl)

TradingView-Updates 2026 (chartwisehub.com), KI-Screener-Vergleiche (wallstreetzen.com, altfins.com,
chartinglens.com), Orderflow (atas.net, bookmap.com), Journale (tradezella.com, Capterra: Journali,
TradersForge), Foren (tradingqna.com, multicharts.com, elitetrader.com), MCP-Agenten (babypips.com,
lablab.ai Odysseus, modulusfe.com, enterprisedna.co). Die meisten Ranglisten stammen von Anbietern
selbst; Nutzerwünsche sind nicht durch Umfragen belegt.
