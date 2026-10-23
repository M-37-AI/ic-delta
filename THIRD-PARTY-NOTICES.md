# Fremde Bestandteile

iC Delta selbst steht unter der MIT-Lizenz (siehe `LICENSE`). Im Repo liegt nur ein fremder Bestandteil:

## Lightweight Charts™ 4.2.0

- Ort: `src/marktdaten/dashboard/static/ic-charts/lib/lightweight-charts.standalone.production.js` (unverändert)
- Copyright © 2024 TradingView, Inc. — https://www.tradingview.com/
- Lizenz: Apache License 2.0, Text in `src/marktdaten/dashboard/static/ic-charts/lib/LICENSE-lightweight-charts.txt`
- Hinweis zur Namensnennung: iC Charts nennt TradingView in der Fußzeile und im Lizenzdialog.

## Bei der Installation geladen (nicht im Repo)

- Python: `websockets` (optional für Live-Kurse), siehe `pyproject.toml`.
- Agent: `@anthropic-ai/claude-agent-sdk`, `playwright-core`, `ws`, `zod`, siehe `agent/package.json`.
- Schriften der Oberfläche kommen von Google Fonts (SIL Open Font License).

Sie stehen unter ihren eigenen Lizenzen.

## Nur zur Entwicklung, nicht mitgeliefert

- **TA-Lib** (BSD-Lizenz, https://ta-lib.org/) dient in `tests/werkzeuge/referenz_talib.py` als unabhängige Referenz
  für die Indikator-Tests. Es wird nicht mitgeliefert und vom Produkt nicht verwendet.

Alle Indikatoren, Zeichenebenen, das Mini-Script und die Orderflow-Auswertung sind eigener Code
(Quellen der Formeln in `INDIKATOREN.md`).
