# Vaillant EEBUS für Home Assistant

Home-Assistant-App, die eine Vaillant-Wärmepumpe ausschließlich lokal über EEBUS anbindet.
Anleitung für Nutzer: [vaillant-eebus/DOCS.md](vaillant-eebus/DOCS.md).

## Installation

In Home Assistant unter *Einstellungen → Apps → App-Store → ⋮ → Repositories* diese URL hinzufügen:

```
https://github.com/wattarosolar-lgtm/vaillant-eebus
```

## Aufbau

| Pfad | Inhalt |
|---|---|
| `repository.yaml` | App-Repository für den HA-App-Store |
| `vaillant-eebus/` | Die App: `config.yaml`, `Dockerfile`, s6-Dienste (`rootfs/`), Doku |
| `bridge/` | Git-Submodul: Fork der EEBUS-Bridge ([volschin/eebus-ha-bridge](https://github.com/volschin/eebus-ha-bridge)), fester Commit |
| `backend/` | Python-Backend (`src/vaillant_eebus`), Lock-Dateien, Skript für die gRPC-Stubs |
| `frontend/` | Oberfläche (Svelte, TypeScript, Vite), vom Backend per Ingress ausgeliefert |
| `tests/` | Backend-Tests, Fake-Bridge und Fake-Home-Assistant |
| `.github/workflows/` | CI und Image-Build |

Im Container laufen zwei s6-Dienste: `eebus-bridge` (Go, SHIP/SPINE, Port 4712, gRPC auf `127.0.0.1:50471`)
und `backend` (Python), das nur über gRPC mit der Bridge spricht. Das Backend liefert API und Oberfläche auf
`172.30.32.1:8714` aus und nimmt nur Anfragen des Ingress-Proxys (`172.30.32.2`) an. Alle Apps mit
Host-Netzwerk teilen sich die Ports des Hosts; das Backend prüft deshalb, dass die Bridge auf seinem Port die
eigene ist (sie muss die EEBUS-Seriennummer aus `/data/eebus/serial` melden).

Die Oberfläche holt den Zustand der Wärmepumpe über `api/state` und bekommt jede Änderung per Server-Sent Events
über `api/events`. Das Backend liest den Zustand neu, sobald die Bridge eine Änderung meldet, und spätestens jede
Minute. Diese regelmäßige Leseanfrage braucht auch die Verbindungsüberwachung der Bridge: Bleibt sie aus, baut die
Bridge die Verbindung zum Gateway nach zwei Minuten neu auf.

Der Zustand enthält außerdem `energy_today` (`{"kwh": …, "incomplete": …}`): den Stromverbrauch seit Mitternacht,
den `backend/src/vaillant_eebus/energy.py` aus der gemeldeten Leistung aufsummiert, weil die Anlage keine
Energiezähler liefert. Mehr als 3 Minuten ohne Messwert werden nicht überbrückt; der Tag gilt dann als lückenhaft.
Der Tageswechsel folgt der Zeitzone von Home Assistant (`TZ`, vom Supervisor gesetzt); gespeichert wird in
`/data/energy.json`.

**Home Assistant** (`homeassistant_api: true`, Token aus `SUPERVISOR_TOKEN`, über `http://supervisor/core`):
`ha_client.py` hält eine WebSocket-Verbindung, abonniert die Zustände der gewählten Entitäten (`subscribe_trigger`,
bei Versionen vor 2024.10 mit `platform` statt `trigger`) und `homeassistant_started` und liest die Entitäten alle
5 Minuten neu (`last_reported`). Schreiben darf der Client nur `sensor.vaillant_eebus_*` und
`binary_sensor.vaillant_eebus_*`; Dienste ruft die App nicht auf. `ha_import.py` bildet daraus Räume, den
Mittelwert je Zone (nur Zahlen in °C/°F, höchstens 30 Minuten alt) und die Wärme des Tages; `ha_export.py` meldet
die Wärmepumpe als Sensoren (je Sensor höchstens alle 10 s, Energie jede Minute, alles neu nach einer
Neuverbindung oder `homeassistant_started`, abgewählte Gruppen werden entfernt). Der Zustand enthält dazu
`home_assistant` und `performance_today` (Wärme ÷ Strom des Tages, nur aus verlässlichen Werten). Ohne Token läuft
die App ohne Home Assistant.

Die Bridge liefert jede Heizzone einzeln (`room_heating_zones` im Snapshot, `ListRoomHeatingZones`). Eine Zone heißt
nach der SPINE-Adresse ihres Raum-Eintrags, z. B. `5.1.1`. Schreibende Aufrufe brauchen bei mehreren Zonen diese
Zonen-ID; ohne sie lehnt die Bridge ab, statt eine Zone zu raten.

**Schreiben:** Einzige Stelle, die an die Wärmepumpe schreibt, ist `backend/src/vaillant_eebus/write_gate.py`
(ein Test stellt sicher, dass kein anderes Modul die Schreibmethoden des Bridge-Clients aufruft). Es prüft jeden
Befehl gegen die Angaben des Geräts, sendet im Demo-Betrieb nichts und im Live-Betrieb höchstens einen Befehl je
Steuerung alle 5 s. Alles landet im Protokoll (`/data/app.db`, SQLite).

| Methode | Pfad | Zweck |
|---|---|---|
| GET/POST | `api/mode` | Demo/Live lesen und setzen (Live nur mit `"confirm": true`) |
| POST | `api/zones/{zone_id}/setpoint`, `api/zones/{zone_id}/mode` | Solltemperatur (`value`), Betriebsart (`mode`) einer Zone; die Solltemperatur nicht im Auto-Betrieb der Zone (409, wirkt dort nicht) |
| POST | `api/dhw/setpoint`, `api/dhw/mode`, `api/dhw/boost` | Warmwasser: `value`, `mode`, `active` |
| POST | `api/lpc`, `api/lpc/failsafe` | Leistungsdeckel und Failsafe-Grenze (nur API, im Demo-Betrieb gesperrt; die Bridge wartet hier nicht auf eine Quittung des Geräts, „Gesendet“ heißt nur abgeschickt) |
| POST | `api/ohpcf` | Verdichter-Flexibilität (`schedule`, `pause`, `resume`, `abort`; nur API, im Demo gesperrt) |
| GET | `api/log` | Protokoll, seitenweise (`before`, `limit`) und nach `category` gefiltert |
| GET/PUT | `api/settings` | Räume (`entity_id`, `name`, `zone_id`), Wärmesensor (`heat_energy_entity`), Export-Gruppen (`export`); dazu `exported` und `home_assistant_available` |
| GET | `api/ha/entities?kind=temperature\|energy&q=…` | Sensoren aus Home Assistant zur Auswahl (503, wenn Home Assistant nicht erreichbar ist) |

Jeder Schreibbefehl nennt in `operating_mode` (`demo` oder `live`) den Betrieb, den der Nutzer beim Ändern sah;
stimmt er nicht mehr, wird nichts gesendet (409). Antworten sagen immer, ob gesendet wurde: `{"sent": false,
"mode": "demo", "message": "Würde senden: …", "would_send": {...}}` bzw. `{"sent": true, "mode": "live", "message":
"Gesendet: …"}`. Abgelehnte Befehle antworten mit 4xx und einer deutschen Begründung in `detail`; ist unklar, ob
ein gesendeter Wert angekommen ist, mit 504 (die App beobachtet den Wert dann weiter).

## Entwicklung

Voraussetzungen: Git, Go 1.27, Python 3.13, Node.js 24. Kein Test spricht mit echter Hardware.

```bash
git clone --recurse-submodules https://github.com/wattarosolar-lgtm/vaillant-eebus.git
cd vaillant-eebus
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r backend/requirements-dev.lock   # Windows: .venv\Scripts\...
.venv/bin/python backend/scripts/generate_proto.py --typed                # Stubs nach backend/gen/
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

Die gRPC-Stubs werden aus `bridge/eebus-bridge/proto` erzeugt und nie von Hand geändert oder eingecheckt.

**Oberfläche:**

```bash
cd frontend
npm ci
npm run check && npm run lint && npm run build   # Ergebnis in frontend/dist/
```

**Ohne Wärmepumpe ausprobieren:** Die Fake-Bridge ist ein gRPC-Server mit denselben Protos und Beispieldaten.
Ihr Gateway nimmt eine Kopplung nach `--connect-after` Sekunden von selbst an. Danach die Oberfläche unter
`http://127.0.0.1:8714/` öffnen:

```bash
PYTHONPATH=backend/gen .venv/bin/python -m tests.fake_bridge --port 50471 --connect-after 10
export DATA_DIR=/tmp/vaillant-eebus HTTP_HOST=127.0.0.1 TRUSTED_CLIENTS=127.0.0.1 STATIC_DIR=frontend/dist
PYTHONPATH=backend/src:backend/gen .venv/bin/python -m vaillant_eebus
```

Ohne `SUPERVISOR_TOKEN` läuft das Backend ohne Home Assistant. Mit `HA_URL=http://<host>:8123` und einem
langlebigen Zugriffstoken als `SUPERVISOR_TOKEN` spricht es stattdessen mit einer Test-Instanz von Home Assistant.

Für die Arbeit an der Oberfläche mit Hot Reload stattdessen `npm run dev` im Ordner `frontend/`; Vite leitet
`/api` an das Backend auf Port 8714 weiter. Unter Windows `PYTHONPATH` mit `;` statt `:` trennen.

**Lock-Dateien** neu erzeugen (mit [uv](https://docs.astral.sh/uv/)), nachdem eine `requirements*.in` geändert wurde:

```bash
cd backend
for f in requirements requirements-build requirements-dev; do
  uv pip compile $f.in --universal --python-version 3.13 --generate-hashes --no-header -o $f.lock
done
```

**Bridge:** `cd bridge/eebus-bridge && go vet ./... && go test -race ./...`
(`-race` braucht CGO und läuft deshalb in der CI unter Linux). Änderungen am Bridge-Code entstehen im Fork
([wattarosolar-lgtm/HEMS-EEBUS](https://github.com/wattarosolar-lgtm/HEMS-EEBUS)); nach einer Proto-Änderung dort
`bash generate_proto.sh` ausführen und die erzeugten Dateien mit committen.

## Release

1. Version in `vaillant-eebus/config.yaml` und `CHANGELOG.md` erhöhen.
2. Tag `v<version>` pushen. Die CI baut das Image für amd64 und aarch64 und veröffentlicht es als
   `ghcr.io/wattarosolar-lgtm/vaillant-eebus:<version>`.
3. Nur beim ersten Release: das Paket `vaillant-eebus` auf GitHub unter *Packages → Package settings*
   auf **Public** stellen, sonst kann Home Assistant das Image nicht laden.
