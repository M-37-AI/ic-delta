"""Rauchtest für CI auf macOS, Linux und Windows: Demo starten, Dashboard und Analysen per HTTP prüfen, Demo beenden.

Start: python tests/werkzeuge/rauchtest_demo.py [basisport]
Die Demo nutzt erfundene Daten in einem Wegwerf-Ordner und ruft nichts bei Börsen ab. Beendet wird sie über die
Stopp-Datei (wie „Demo stoppen“ im Browser), das klappt auch unter Windows ohne Strg+C.
"""
import json
import os
import pathlib
import platform
import subprocess
import sys
import tempfile
import time
import urllib.request

BASIS = int(sys.argv[1]) if len(sys.argv) > 1 else 18822


def holen(url: str, versuche: int = 90) -> tuple[int, bytes]:
    letzter = None
    for _ in range(versuche):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.status, r.read()
        except OSError as e:
            letzter = e
            time.sleep(1)
    raise SystemExit(f"{url} nicht erreichbar: {letzter}")


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        daten = pathlib.Path(tmp) / "marktdaten-demo"
        env = {**os.environ, "MARKTDATEN_DEMO_DATEN": str(daten), "MARKTDATEN_DEMO_KONFIG": str(pathlib.Path(tmp) / "konfig")}
        demo = subprocess.Popen([sys.executable, "-m", "marktdaten", "demo", "--port", str(BASIS)], env=env)
        start = time.time()
        try:
            for pfad in (f"{BASIS}/", f"{BASIS}/einrichtung", f"{BASIS + 1}/", f"{BASIS + 1}/api/werkstatt"):
                status, inhalt = holen(f"http://127.0.0.1:{pfad}")
                print(f"{pfad:22} HTTP {status}", flush=True)
                assert status == 200, pfad
            werkstatt = json.loads(inhalt)
            print("Werkstatt:", werkstatt["sandkasten"], flush=True)
            if platform.system() != "Darwin":
                assert "nur auf macOS" in werkstatt["sandkasten"], werkstatt["sandkasten"]
            assert demo.poll() is None, f"Demo hat sich beendet (Code {demo.returncode})"
        finally:
            time.sleep(max(0.0, 5 - (time.time() - start)))      # die Demo prüft die Stopp-Datei erst nach dem Start
            daten.mkdir(parents=True, exist_ok=True)
            (daten / ".stopp").write_text("", encoding="utf-8")
            try:
                demo.wait(60)
            except subprocess.TimeoutExpired:
                demo.kill()
                raise SystemExit("Demo hat sich nicht über die Stopp-Datei beendet")
    print("Rauchtest ok", flush=True)


if __name__ == "__main__":
    main()
