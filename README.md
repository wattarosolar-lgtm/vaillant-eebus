# Vaillant EEBUS für Home Assistant

Home-Assistant-App, die eine Vaillant-Wärmepumpe anbindet: über die Entitäten der myVAILLANT-Integration in Home
Assistant oder lokal über EEBUS (Einstellung „Anbindung“).
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

**Anbindung:** Mit myVAILLANT liest das Backend den Zustand aus den gewählten Entitäten der Integration
(`myvaillant.py`) und schreibt über sechs fest erlaubte Dienste (`DEVICE_SERVICES` in `ha_client.py`), nur aus
`write_gate.py`. Die Bridge läuft dann nicht (ihr s6-Dienst liest `connection` aus `/data/settings.json`); beim
Umstellen beendet sich das Backend, s6 startet es neu, und es startet den Dienst der Bridge neu.

Im Container laufen zwei s6-Dienste: `eebus-bridge` (Go, SHIP/SPINE, Port 4712, gRPC auf `127.0.0.1:50471`)
und `backend` (Python), das nur über gRPC mit der Bridge spricht. Das Backend liefert API und Oberfläche auf
`172.30.32.1:8714` aus und nimmt nur Anfragen des Ingress-Proxys (`172.30.32.2`) an. Alle Apps mit
Host-Netzwerk teilen sich die Ports des Hosts; das Backend prüft deshalb, dass die Bridge auf seinem Port die
eigene ist (sie muss die EEBUS-Seriennummer aus `/data/eebus/serial` melden).

**Absicherung der Bridge:** Auch Loopback teilen sich alle Apps mit Host-Netzwerk (unter HAOS auch HA Core). Die
Bridge läuft deshalb im Modus `tls_token` des Originals: TLS mit einem eigenen, selbstsignierten Zertifikat und ein
Bearer-Token. Beides erzeugt der s6-Oneshot `init-grpc-credentials` einmalig in `/data/eebus/grpc`
(`backend/src/vaillant_eebus/bridge_auth.py`, Dateien nur für root lesbar), bevor Bridge und Backend starten. Das
Backend vertraut genau diesem Zertifikat und schickt das Token mit (`BRIDGE_CREDENTIALS_DIR`). Scheitert der
TLS-Handshake, gilt der Dienst auf dem Port als fremde Bridge. Ohne `BRIDGE_CREDENTIALS_DIR` (Entwicklung mit der
Fake-Bridge) spricht das Backend Klartext.

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
bei Versionen vor 2024.10 mit `platform` statt `trigger`) und `homeassistant_started`, liest die Entitäten alle
5 Minuten neu (`last_reported`) und auf derselben Verbindung die Langzeitstatistik (`statistics()`, nur lesen). Schreiben darf der Client nur `sensor.vaillant_eebus_*` und
`binary_sensor.vaillant_eebus_*`. Dienste: `weather.get_forecasts` (liest nur, per REST mit `return_response`) und
mit der Anbindung myVAILLANT `call_device_service()` für genau die Dienste in `DEVICE_SERVICES` auf Klima- und
Warmwasser-Entitäten; Tests prüfen, dass es keine anderen sind und nur `write_gate.py` (über `myvaillant.py`) sie
aufruft. `ha_import.py` bildet daraus Räume, den
Mittelwert je Zone (nur Zahlen in °C/°F, höchstens 30 Minuten alt) und die Wärme des Tages; `ha_export.py` meldet
die Wärmepumpe als Sensoren (je Sensor höchstens alle 10 s, Energie jede Minute, alles neu nach einer
Neuverbindung oder `homeassistant_started`, abgewählte Gruppen werden entfernt). Der Zustand enthält dazu
`home_assistant` und `performance_today` (Wärme ÷ Strom des Tages, nur aus verlässlichen Werten). Ohne Token läuft
die App ohne Home Assistant. Den Verlauf der Leistung liest `ha_client.history()` aus der Historie von Home
Assistant; `history.py` rechnet daraus zeitgewichtete 5-Minuten-Mittelwerte mit Lücken, wo nichts aufgezeichnet
ist.

Die Bridge liefert jede Heizzone einzeln (`room_heating_zones` im Snapshot, `ListRoomHeatingZones`). Eine Zone heißt
nach der SPINE-Adresse ihres Raum-Eintrags, z. B. `5.1.1`; mit myVAILLANT ist ihre ID die Klima-Entität, z. B.
`climate.zuhause_zone_1_climate`. Schreibende Aufrufe brauchen bei mehreren Zonen diese
Zonen-ID; ohne sie lehnt die Bridge ab, statt eine Zone zu raten.

**Energiemanagement** (`backend/src/vaillant_eebus/hems/`, 1.3.0: beobachten, lernen, planen; sendet nichts):
`config.py` (Einstellungen in `settings.json` unter `hems`), `sources.py` (gelesene HA-Zustände: Räume, Thermostate,
Ventile, Feuchte, Vorlauf, Puffer), `forecast.py` (Open-Meteo `/v1/dwd-icon` höchstens stündlich, Koordinaten auf
2 Stellen gerundet, Rückfall `weather.get_forecasts`, Eichung je Tageszeit am Außenfühler, Day-ahead-Fehler),
`statistics.py` (HA-Langzeitstatistik über WebSocket `recorder/statistics_during_period`, monatsweise, Tageswerte),
`house_model.py` (Wärmeverlust W/K, Gewinne, Sonne, Verzögerung, Arbeitszahl aus echten Wärme- und Stromwerten,
Bedarf je Stunde), `samples.py` (5-Minuten-Proben in `/data/hems.db`, 21 Tage), `room_model.py` (Zeitkonstante,
Fremdwärme, Aufheizen, Anlaufzeit, Bedarfsöffnung, Korrektur, Versorgung, Vorlauf-Hinweis), `cycles.py` (Starts,
Lauf, Pause, Abtau-Muster, `/data/cycles.json`), `defrost.py` (Abtaurisiko je Stunde), `frost.py` (Frostschutz
< 4 °C länger als 4 h), `windows.py` (Fenster aus Temperatur und absoluter Feuchte), `rules.py` (reine Funktion:
Plan 48 h je Zone und Stunde mit Grund), `service.py` (Beobachten, Proben, Lernen, Planen alle 15 min, Status,
Sensoren, CSV). Das Paket importiert weder `write_gate` noch den Bridge-Client (Strukturtest). Raumdaten stehen
in `room_data.py` (je Raum in `settings.json`).

**Schreiben:** Einzige Stelle, die an die Wärmepumpe schreibt, ist `backend/src/vaillant_eebus/write_gate.py`
(Tests stellen sicher, dass kein anderes Modul die Schreibmethoden des Bridge-Clients oder `send_command()` des
myVAILLANT-Wegs aufruft). Es prüft jeden
Befehl gegen die Angaben des Geräts, sendet im Demo-Betrieb nichts und im Live-Betrieb höchstens einen Befehl je
Steuerung alle 5 s. Alles landet im Protokoll (`/data/app.db`, SQLite).

| Methode | Pfad | Zweck |
|---|---|---|
| GET/PUT | `api/connection` | Anbindung (`myvaillant` oder `eebus`) und die gewählten myVAILLANT-Entitäten; eine neue Anbindung schaltet auf Demo und startet das Backend neu (`restarting`) |
| GET | `api/myvaillant/discover` | Vorschlag der myVAILLANT-Entitäten aus Home Assistant |
| GET/POST | `api/mode` | Demo/Live lesen und setzen (Live nur mit `"confirm": true`) |
| POST | `api/zones/{zone_id}/setpoint`, `api/zones/{zone_id}/mode` | Solltemperatur (`value`), Betriebsart (`mode`) einer Zone; mit EEBUS die Solltemperatur nicht im Auto-Betrieb der Zone (409, wirkt dort nicht), mit myVAILLANT dort als zeitweise Übersteuerung und nicht in „Aus“ |
| POST | `api/dhw/setpoint`, `api/dhw/mode`, `api/dhw/boost` | Warmwasser: `value`, `mode`, `active` |
| POST | `api/lpc`, `api/lpc/failsafe` | Leistungsdeckel und Failsafe-Grenze (nur API und nur mit EEBUS, im Demo-Betrieb gesperrt; die Bridge wartet hier nicht auf eine Quittung des Geräts, „Gesendet“ heißt nur abgeschickt) |
| POST | `api/ohpcf` | Verdichter-Flexibilität (`schedule`, `pause`, `resume`, `abort`; nur API, im Demo gesperrt) |
| GET | `api/log` | Protokoll, neueste zuerst: seitenweise (`before`, `limit`), neuere als ein Eintrag (`after`), bis zu einem Tag in Ortszeit (`until=JJJJ-MM-TT`), gefiltert nach `category` oder `problems=true` |
| GET | `api/log/export` | Protokoll als CSV (Semikolon, UTF-8 mit BOM, Ortszeit), gleiche Filter |
| GET | `api/history/power` | Leistung der letzten 24 h als 5-Minuten-Mittelwerte aus der HA-Historie von `sensor.vaillant_eebus_power`; ohne Daten mit Grund in `gap` (`no_home_assistant`, `not_exported`, `unreachable`) |
| GET/PUT | `api/settings` | Räume (`entity_id`, `name`, `zone_id`; die Raumdaten bleiben erhalten), Wärmesensoren (`heat_energy_entities`, bis zu 4, addiert), Export-Gruppen (`export`); dazu `exported` und `home_assistant_available` |
| GET | `api/ha/entities?kind=temperature\|energy\|power\|percent\|humidity\|room\|thermostat\|weather\|climate\|water_heater&q=…` | Entitäten aus Home Assistant zur Auswahl (503, wenn Home Assistant nicht erreichbar ist) |
| GET | `api/ha/location` | Standort aus der HA-Konfiguration (für Open-Meteo); 404 ohne Standort |
| GET | `api/hems` | Energiemanagement: Stufe, Einstellungen, fehlende Daten, Prognose, Hausmodell, Bedarf, Takte und Abtauungen, Abtauraten, Frostschutz, Zone-1-Logik, Vorlauf, nächste Änderungen |
| PUT | `api/hems/settings` | Einstellungen des Energiemanagements (ohne Stufe), geprüft (422 mit Begründung) |
| POST | `api/hems/mode` | Stufe `off` oder `preview`; `control` antwortet in 1.3.0 mit 409 |
| GET | `api/hems/plan`, `api/hems/plan/export` | Plan 48 h je Stunde und Rückblick 24 h; als CSV |
| GET | `api/rooms` | Räume mit Messwerten, Lernstand, Versorgung und Fenster; je Zone der begrenzende Raum |
| PUT | `api/rooms/{entity_id}` | Raumdaten eines Raums (Soll, Zeitplan, Komfortband, Verluste, Thermostat, Ventil, Feuchte, statischer Abgleich) |

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

1. Version in `vaillant-eebus/config.yaml`, `pyproject.toml`, `frontend/package.json` (und `package-lock.json`)
   erhöhen und in `vaillant-eebus/CHANGELOG.md` einen Abschnitt `## <version>` anlegen. Die CI prüft, dass alle
   übereinstimmen.
2. Branch pushen und warten, bis die CI grün ist; dann `main` vorspulen.
3. Tag `v<version>` auf `main` pushen. Die CI baut das Image für amd64 und aarch64, veröffentlicht es als
   `ghcr.io/wattarosolar-lgtm/vaillant-eebus:<version>` und prüft danach ohne Anmeldung, dass Home Assistant es für
   beide Architekturen laden kann.
4. Nur beim ersten Release: das Paket `vaillant-eebus` auf GitHub unter *Packages → Package settings*
   auf **Public** stellen; sonst schlägt die Prüfung aus Schritt 3 fehl und Home Assistant kann das Image nicht
   laden.
