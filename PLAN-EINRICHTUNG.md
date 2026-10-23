# Plan: Einrichtung für Nutzer des Repos

Stand 20.10.2026, vereinbart, noch nicht gebaut. Start nach dem Umbau der Analysen.
iC Delta bleibt vorerst im Browser (keine Mac- oder Windows-App).

## Grundsatz

Der Sammler muss dauerhaft laufen. Deshalb immer: Hintergrunddienst plus Oberfläche im Browser.
Ziel: Repo laden, ein Befehl, danach alles im Browser.

## Baustein 1: `einrichten.sh` (später `einrichten.ps1` für Windows)

- Python prüfen, Umgebung anlegen, Pakete installieren
- Hintergrunddienst mit Autostart einrichten: macOS launchd, Linux systemd (Nutzerdienst), Windows Aufgabenplanung
- Dashboard starten, Browser auf die Einrichtungsseite öffnen

## Baustein 2: Einrichtungsseite (erscheint, solange keine Einstellungsdatei existiert)

Jeder Schritt überspringbar und später unter „Einstellungen“ änderbar.

1. **Willkommen:** Was iC Delta ist, Nutzungsbedingungen bestätigen (privater Gebrauch, keine Weitergabe)
2. **Betriebsart:** a) eigener Server im Heimnetz, b) gemieteter Server, c) eigener PC oder Laptop
3. **Zugriff:** nur dieses Gerät, Heimnetz, von unterwegs (Anleitung Tailscale)
4. **Schlüssel:** je Quelle „keiner nötig / optional / nötig“, Alpaca-Feld mit „Verbindung prüfen“
5. **Telegram (optional):** BotFather-Anleitung, Token einfügen, Nutzer schreibt dem Bot, Chat-ID wird
   automatisch per getUpdates ermittelt, Testnachricht
6. **Benachrichtigungen:** Sammler gestört, Lücken entstanden, Speicher fast voll, Tagesbericht, später Kursalarme
7. **Watchlist:** Vorlagen (5 DAX + 5 US, nur Krypto, leer) oder Suche
8. **Speicher:** Obergrenze vorgeschlagen nach freiem Platz und Betriebsart
9. **Selbsttest:** Quellen erreichbar, Schlüssel gültig, Telegram ok, Dienst läuft, Autostart aktiv

## Betriebsarten

- **a) Eigener Server (empfohlen):** Dauerbetrieb, volles Sammeln, Live-Orderbuch an
- **b) Gemieteter Server:** Dashboard nie offen im Internet, nur über Tailscale oder SSH-Tunnel;
  nur für den eigenen Gebrauch (Zugriff für andere wäre Weitergabe der Daten); Download-Volumen
  beachten (10–15 GB pro Handelstag mit Xetra-Orderbuch)
- **c) Eigener PC/Laptop:** Warnung vor Lücken im Ruhezustand; vorsichtige Voreinstellungen
  (kleine Obergrenze, Orderbuch nur auf Wunsch); Online-Status mit Lückenanzeige, Nachholen nach
  dem Aufwachen, Telegram-Meldung über verlorene Zeiträume

## Querschnitt

- **Herzschlag:** jede Minute ein Lebenszeichen im Speicher; daraus Online-Status, Lückenanzeige,
  Meldung „Sammler steht seit 20 Minuten“
- **Schlüssel** in `~/.config/ic-delta/` mit Rechten nur für den Nutzer, nie angezeigt oder protokolliert
- **Einstellungen** als lesbare Datei `~/.config/ic-delta/einstellungen.toml`

## Reihenfolge

1. Einstellungsdatei, Herzschlag, Online-Status
2. Einrichtungsseite mit Betriebsart, Schlüsseln, Watchlist-Vorlagen, Selbsttest
3. Telegram-Bot mit automatischer Chat-ID und Benachrichtigungen
4. `einrichten.sh`, danach `einrichten.ps1`

## Ebenfalls gemerkt

- **Demo-Modus fürs Video:** eigene Quelle mit frei erzeugten Daten (nie aus echten Kursen abgeleitet),
  echte Namen mit dauerhaftem Banner „DEMO-DATEN · keine echten Kurse“, getrennte Demo-Datenbank

## Umsetzung (Stand 21.10.2026, Teile 1 und 2 gebaut)

Geänderte Reihenfolge nach Rücksprache: zuerst alles ohne Schlüssel, danach „Mehr freischalten“.
Die Watchlist-Vorlagen richten sich nach den freigeschalteten Quellen (USA erst mit Alpaca).

- **Modul** `src/marktdaten/einrichtung.py`, **Seite** `dashboard/einrichtung.html` unter `/einrichtung`
  (beim ersten Start leitet `/` dorthin, danach als „Einrichtung“ in der Seitenleiste)
- **Schritte:** Willkommen (Bedingungen bestätigen) → Betrieb und Zugriff → Sofort startklar (Quellen ohne Schlüssel
  mit Bedingung je Quelle, Watchlist-Vorlagen Deutschland / Krypto / USA) → Mehr freischalten (Alpaca, EODHD,
  Telegram mit automatischer Chat-ID, KI-Analyst mit Non-Display-Hinweis) → Speicher (Obergrenze, Orderbuch-Schalter)
  → Selbsttest
- **Einstellungen** in `~/.config/marktdaten/einstellungen.toml` (Ordner wie `MARKTDATEN_KONFIG`, nicht ic-delta,
  damit die vorhandenen Schlüsseldateien bleiben, wo die Quellen sie lesen). Bestehende Installationen gelten beim
  Start als eingerichtet (`uebernommen = true`).
- **Schlüssel** in denselben Dateien wie bisher (alpaca.env, eodhd.env, telegram.env, ki.env), Rechte 0600; Antworten
  nennen nur „vorhanden“ und das Prüfergebnis. Prüfungen: Alpaca letzte SPY-Kerze (IEX), EODHD `/api/user` (zählt
  nicht zu den 20 Abrufen), Telegram `getMe`, OpenRouter `/api/v1/key`. Neuer Alpaca- oder EODHD-Schlüssel wirkt
  ohne Neustart. Schreibende Aufrufe brauchen den Kopf `X-IC-Delta: 1` (Schutz gegen fremde Webseiten).
- **Orderbuch-Schalter** `orderbuch` (Standard an): aus = Xetra, Tradegate und Cboe nur Abschlüsse, spart 10–15 GB am Tag
- **Herzschlag** je Minute in der Tabelle `herzschlag` (90 Tage), `/api/einrichtung/online`: läuft seit, Anteil online,
  Lücken über 3 Minuten
- **Demo:** Schlüssel nur zum Schein (nichts geschrieben, nichts abgerufen), Telegram mit dem Demo-Bot echt
- **Tests:** `tests/test_einrichtung.py`

Offen: Benachrichtigungen (Sammler steht, Lücken, Speicher fast voll) über Telegram, Lücken auf der Seite
Datenquellen, `einrichten.sh` (Dienst mit Autostart, Tailscale-Adresse), danach `einrichten.ps1`.

### Nachtrag 21.10.2026: Demo für alle, Haftungsausschluss

- `python -m marktdaten demo [--port 8822] [--host …]` startet Dashboard, Analysen und (mit Node) den Agenten mit
  erfundenen Daten, eigenem Datenordner (`~/.local/share/marktdaten-demo`) und eigenen Ports; kein Hintergrunddienst,
  Strg+C beendet alles. Belegter Port: Hinweis statt zweitem Start. `./demo.sh terminal` ruft dasselbe auf.
- Willkommen: Kasten „Erst ausprobieren?“ mit Link zur laufenden Demo bzw. dem Befehl.
- Haftungsausschluss auf Willkommen (Bestätigung „privat und auf eigenes Risiko“) und im README.
- Betriebsart und Zugriff wirken nur auf Voreinstellungen (Orderbuch, Obergrenze) und Hinweise; Nikos Entscheidung:
  keine automatische Einrichtung von Dienst oder Adressen, das regelt der Nutzer selbst.
- Demo im Terminal: Leiste „DEMO · läuft seit … · MB · Demo stoppen“ auf Dashboard (alle Reiter) und Einrichtung
  (`dashboard/demo-leiste.js`). Nach 10 Minuten Nachfrage (Ja = beenden und Demo-Daten löschen, Nein = 10 Minuten weiter),
  ohne Antwort endet die Demo nach weiteren 10 Minuten. Stopp über die Datei `.stopp` im Demo-Datenordner, die
  `python -m marktdaten demo` überwacht; gelöscht wird nur ein Ordner mit „demo“ im Namen. Die Demo als
  Hintergrunddienst (`./demo.sh start`, fürs Video) zeigt weder Leiste noch Nachfrage.
- 🎬-Knopf im echten Dashboard und „Demo starten“ auf Willkommen: läuft die Demo nicht, Nachfrage und Start im
  Hintergrund (`einrichtung.demo_starten`, POST /api/einrichtung/demo/starten, Protokoll
  `~/.local/share/marktdaten-demo.log`), Wechsel, sobald sie antwortet. Danach gelten Stopp-Leiste und Nachfrage.
