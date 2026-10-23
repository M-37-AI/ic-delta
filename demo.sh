#!/bin/zsh
# iC Delta im Demo-Betrieb: derselbe Code mit erfundenen Daten, eigenem Datenordner und eigenen Ports (8822/8823/8824).
# Das echte iC Delta (8812/8813/8814) sammelt dabei ungestört weiter.
#
#   ./demo.sh start | stop | status | neu        Dienste starten, stoppen, Stand zeigen, Demo-Daten neu erzeugen
#   ./demo.sh szene ausbruch_auf MUSW --prozent 2 --minuten 3 [--in 30]
#   ./demo.sh szene arten                         alle Szenen und Werte
#   ./demo.sh telegram                            Demo-Bot einrichten (Token vorher in ~/.config/marktdaten-demo/telegram.env)
#   ./demo.sh terminal                            Demo ohne Hintergrunddienste im Terminal (wie python -m marktdaten demo)
cd "$(dirname "$0")"
export MARKTDATEN_DATEN="$HOME/.local/share/marktdaten-demo" MARKTDATEN_KONFIG="$HOME/.config/marktdaten-demo" MARKTDATEN_PORT=8822 MARKTDATEN_DEMO=1 PYTHONPATH="$PWD/src"
DIENSTE=(com.m37.ic-delta-demo com.m37.ic-delta-demo-analysen com.m37.ic-delta-demo-agent)
case "$1" in
  start)
    mkdir -p "$MARKTDATEN_DATEN/analysen" "$MARKTDATEN_KONFIG"
    [ -f "$MARKTDATEN_DATEN/analysen/ki.json" ] || echo '{"routinen": false}' > "$MARKTDATEN_DATEN/analysen/ki.json"   # keine täglichen Claude-Läufe in der Demo
    for d in $DIENSTE; do launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$d.plist 2>/dev/null || launchctl kickstart -k gui/$(id -u)/$d; done
    echo "Demo läuft: http://localhost:8822/  (erster Start baut etwa eine Minute lang die Historie auf)";;
  stop)
    for d in $DIENSTE; do launchctl bootout gui/$(id -u)/$d 2>/dev/null; done; echo "Demo gestoppt";;
  status)
    for d in $DIENSTE; do launchctl print gui/$(id -u)/$d 2>/dev/null | grep -m1 "state =" | sed "s/^/$d: /" || echo "$d: aus"; done;;
  neu)
    "$0" stop; rm -rf "$MARKTDATEN_DATEN"; "$0" start;;
  terminal)
    .venv/bin/python -m marktdaten demo;;
  telegram)
    .venv/bin/python -m marktdaten.analysen telegram;;
  szene)
    shift; if [ "$1" = "arten" ] || [ -z "$1" ]; then .venv/bin/python -m marktdaten demo arten; else .venv/bin/python -m marktdaten demo szene "$@"; fi;;
  *) sed -n 2,9p "$0";;
esac
