# Briefing: Vaillant EEBUS (Stand v0.4.0)

Dieses Dokument fasst alles Wichtige über den Code zusammen – für eine KI, die den Code prüft, und als Übergabe
an eine neue Arbeitssitzung. Bitte den Prüfbericht **auf Deutsch** verfassen (Code-Begriffe dürfen englisch bleiben).

## 1. Worum es geht

Eine **Home-Assistant-App** (früher „Add-on“), die eine Vaillant-Wärmepumpe **lokal über EEBUS** anbindet –
ohne Vaillant-Cloud, ohne eBUS-Adapter, ohne MQTT. Die App bringt eine eigene Oberfläche mit (Seitenleiste
„Wärmepumpe“ in Home Assistant, über Ingress).

- **Stand:** Version 0.4.0 (Tag `v0.4.0`), Phasen 1–4 von 7 erledigt.
- **Die App liest derzeit nur.** Sie sendet keine Schreibbefehle an die Wärmepumpe. Die Bridge kann bereits
  schreiben (getestet), die App nutzt das erst ab Phase 5 hinter einem Schreibschutz (Demo-/Live-Betrieb).
- Konvention: Code und Kommentare englisch, Oberfläche und Nutzer-Doku deutsch.

| Phase | Inhalt | Stand |
|---|---|---|
| 1 | Repository, Bridge-Fork, Dockerfile, App startet | erledigt (0.1.0) |
| 2 | Kopplung, Diagnose-Dump, Einrichtungsassistent | erledigt (0.2.0/0.2.1) |
| 3 | Lesen: Zustand, API, Übersicht, Live-Updates | erledigt (0.3.0) |
| 4 | Mehrere Heizzonen in der Bridge, Eco nur wenn angeboten | erledigt (0.4.0), Abnahme an der Anlage offen |
| 5 | Schreibschutz, Demo/Live, Steuerung in der Oberfläche, Protokoll | offen |
| 6 | HA-Import (Raumtemperatur je Zone als Mittelwert gewählter HA-Sensoren), HA-Export als Sensoren | offen |
| 7 | Einstellungs-/Protokollseite fertig, Doku, Release | offen |

## 2. Repositories

| Repo | Rolle |
|---|---|
| https://github.com/wattarosolar-lgtm/vaillant-eebus | **Die App** (dieses Repo). `main` = v0.4.0 (`5721b8b`). |
| https://github.com/wattarosolar-lgtm/HEMS-EEBUS | **Fork der EEBUS-Bridge** (Go), eingebunden als Git-Submodul `bridge/`, fest auf Commit `69234ad` (Branch `feat/multi-zone`). Muss öffentlich bleiben, sonst kann die CI ihn nicht laden. |
| https://github.com/volschin/eebus-ha-bridge | Original der Bridge (MIT-Lizenz). Der Fork-`main` (`25e061f`) entspricht dem Original. |

**Eigener Code im Fork** sind nur diese vier Commits auf `25e061f`; alles andere ist Code des Originals:

| Commit | Inhalt |
|---|---|
| `6772a26` | Nur lesender Struktur-Dump des Geräts (`GetDeviceStructureDump`, `internal/eebus/structure_dump.go`) |
| `e9baadc` | Gerätesuche liefert Marke/Modell/Seriennummer/Typ; `ServerInfo.device_serial` |
| `47ce5ff` | Gerätesuche liefert mDNS-Name, SHIP-ID, Gerätekategorien |
| `69234ad` | **Mehrere Heizzonen** (siehe Abschnitt 5) |

Prüfen: `git diff 25e061f..69234ad` im Fork, ohne die erzeugten Dateien (`*.pb.go`,
`custom_components/eebus/generated/`). Der Ordner `custom_components/` im Fork ist die HA-Integration des
Originals und wird von dieser App **nicht** benutzt.

## 3. Architektur

Ein Container mit zwei s6-Diensten (s6-overlay aus dem HA-Basis-Image):

```
Vaillant-Gateway (VR921/VR940f) ──SHIP/SPINE, TCP 4712, mDNS──▶ eebus-bridge (Go)
                                                                     │ gRPC, Klartext, nur 127.0.0.1:50471
                                                                     ▼
Home Assistant ──Ingress (Supervisor 172.30.32.2)──▶ backend (Python, FastAPI, 172.30.32.1:8714)
                                                       └─ liefert auch die Oberfläche (Svelte, statische Dateien)
```

- `host_network: true` (mDNS, Port 4712). Folge: **alle Apps mit Host-Netzwerk teilen sich die Ports.** Deshalb
  eigener gRPC-Port 50471 (statt 50051) und eine Identitätsprüfung: Das Backend spricht nur mit einer Bridge, die
  die EEBUS-Seriennummer aus `/data/eebus/serial` meldet (`ServerInfo.device_serial`).
- Persistente Daten: `/data/eebus/` (Zertifikat und Seriennummer = SKI der App; Verlust erzwingt Neukopplung),
  `/data/settings.json` (gekoppelte Gateway-SKI).
- Das Gateway erlaubt **nur eine EEBUS-Verbindung** gleichzeitig.
- Image: `ghcr.io/wattarosolar-lgtm/vaillant-eebus:<version>` für amd64 und aarch64, von der CI gebaut; der
  Supervisor baut nicht selbst.

## 4. Aufbau dieses Repos

| Pfad | Inhalt |
|---|---|
| `repository.yaml` | App-Repository für den HA-App-Store |
| `vaillant-eebus/config.yaml` | App-Konfiguration (Version, Ingress-Port 8714, `host_network`, Option `log_level`) |
| `vaillant-eebus/Dockerfile` | Mehrstufig: Go-Bridge (Cross-Compile, CGO aus), Python-Stubs aus den Protos, Svelte-Build, Laufzeit auf `ghcr.io/home-assistant/base-python:3.13-alpine3.24-2026.10.0`; Rauchtest `import vaillant_eebus.main` |
| `vaillant-eebus/rootfs/etc/s6-overlay/s6-rc.d/eebus-bridge/run` | Erzeugt Seriennummer (einmalig) und Bridge-Konfiguration, startet die Bridge |
| `vaillant-eebus/rootfs/etc/s6-overlay/s6-rc.d/backend/run` | Setzt Umgebungsvariablen, startet das Backend |
| `vaillant-eebus/DOCS.md`, `CHANGELOG.md`, `translations/de.yaml` | Nutzer-Doku |
| `backend/src/vaillant_eebus/` | Backend (Module siehe unten) |
| `backend/scripts/generate_proto.py` | Erzeugt die gRPC-Stubs nach `backend/gen/` (nicht eingecheckt) |
| `backend/requirements*.in/.lock` | Abhängigkeiten, Lock-Dateien mit Hashes (uv) |
| `frontend/src/` | Oberfläche (Svelte 5, TypeScript, Vite) |
| `tests/` | Backend-Tests und **Fake-Bridge** (`fake_bridge.py`: gRPC-Server mit denselben Protos und Beispieldaten) |
| `.github/workflows/ci.yml` | CI (siehe Abschnitt 8) |
| `bridge/` | Git-Submodul: der Bridge-Fork |

### Backend-Module (`backend/src/vaillant_eebus/`)

| Modul | Aufgabe |
|---|---|
| `main.py` | Konfiguration aus Umgebungsvariablen (`BRIDGE_ADDRESS`, `DATA_DIR`, `HTTP_HOST`, `HTTP_PORT`, `TRUSTED_CLIENTS`, `STATIC_DIR`, `BRIDGE_SERIAL_FILE`, `SHIP_PORT`, …); startet uvicorn, `PairingService.run()` und `StateService.run()` in einer `asyncio.TaskGroup` |
| `bridge_client.py` | gRPC-Client (`grpc.aio`): Server-Info, Gerätesuche, Kopplung, Gerätestatus, Snapshot, Ereignis-Streams, Struktur-Dump; Zeitlimits, Backoff, `RETRYABLE_CODES`, max. Nachrichtengröße 64 MB |
| `pairing.py` | Kopplung und Verbindungszustand (`ConnectionState`); prüft, ob die eigene Bridge antwortet; erkennt fremde Bridge und belegten SHIP-Port; meldet das gekoppelte Gateway nach jedem Bridge-Neustart wieder an; `ConnectionLog` fasst abgelehnte Verbindungsversuche beim Start zusammen; Diagnose-Dump |
| `state.py` | `StateService`: liest den Geräte-Snapshot nach jedem Zustandsereignis der Bridge (1 s gebündelt) und **spätestens jede Minute** – diese Leseanfrage braucht auch die Verbindungsüberwachung der Bridge (ohne Lesezugriff baut sie die Verbindung nach 2 min neu auf). Bildet `DeviceSnapshot` auf `HeatPumpState` ab (Messwerte, Zonen, Warmwasser, Status). Nicht gemeldete Werte bleiben `None`, nie Ersatzwerte. Verdichter gilt als aktiv ab 100 W. |
| `api.py` | FastAPI: `GET api/pairing`, `GET api/state`, `GET api/events` (Server-Sent Events, Keep-alive alle 15 s), `POST api/pairing/register`, `GET api/diagnostics/dump`; Middleware lässt nur die Ingress-Adresse zu; gRPC-Fehler → HTTP mit „(Fehlercode X)“; `index.html` mit `no-cache`, Assets `immutable` |
| `settings.py` | `settings.json` atomar schreiben; beschädigte Datei wird beiseitegelegt |

### Oberfläche (`frontend/src/`)

Svelte 5 (Runes), Hash-Routing (`#/uebersicht`, `#/einstellungen`, `#/einrichtung`), nur relative URLs (Ingress-Pfad),
Live-Daten über `EventSource('api/events')`. Seiten: `Overview.svelte` (Rundinstrumente je Zone und Warmwasser, Karten
für Leistung/Energie und Temperaturen), `Setup.svelte` (Einrichtungsassistent), `Settings.svelte` (Kopplung, Diagnose-Dump).
Komponenten: `Dial.svelte`, `HeaderStatus.svelte`, `StatusPill.svelte`, `DumpButton.svelte`. Texte in `lib/texts.ts`,
Formatierung (de-DE) in `lib/format.ts`. Noch **keine Bedienelemente** zum Steuern.

## 5. Mehrere Heizzonen (Bridge, Commit `69234ad`)

Vorher nahm die Bridge pro Gerät die erste passende Raum-Entität und speicherte nur einen Zonennamen; bei zwei Zonen
stand der Name der zweiten über den Werten der ersten, und Werte konnten aus verschiedenen Zonen stammen.

- `internal/usecases/entity.go`: `compatibleEntities()` liefert alle Entitäten eines Geräts, numerisch nach Adresse
  sortiert; `sameZoneEntity()` ordnet Entitäten verschiedener Use Cases über die Adresse zu.
- `internal/eebus/zones.go`: `ZoneID()` = SPINE-Adresse mit Punkten, z. B. `"5.1.1"`.
- `internal/eebus/registry.go`: Zonennamen je `HeatingZone`-Entität; Zuordnung Raum → Zone über Adresspräfix
  (`5.1.1` gehört zu `5.1`), bei Mehrdeutigkeit kein Name.
- `internal/usecases/classification.go`: Name wird je Zone gelesen und angefordert.
- `internal/grpc/hvac_service.go`: `ListRoomHeatingZones`; Lesen je Zone (Name, Raum-Ist, Sollwert mit Grenzen,
  Betriebsart); `GetRoomHeating` liefert weiter **eine** Zone (die erste nach Adresse) für ältere Clients.
  Schreiben mit optionaler `zone_id`: ohne sie nur bei genau einer Zone, sonst `FAILED_PRECONDITION`; unbekannte Zone
  `NOT_FOUND`; Grenzen der jeweiligen Zone. Quellen ohne Zonenliste (Test-Doubles) laufen über den alten Einzelpfad.
- Protos: `hvac_service.proto` (`RoomHeatingZone`, `RoomHeatingZoneList`, `zone_id`), `device_service.proto`
  (`DeviceSnapshot.room_heating_zones`, Feld 29).
- Ereignisse tragen die Zonen-ID (`eebus.Event.ZoneID`).
- Eco wird nur durchgereicht, wenn das Gerät es anbietet (Test).

## 6. Die echte Anlage (Feldbefunde)

- Wärmepumpe: Vaillant aroTHERM split plus, VWL 75/8.2 AS 230V S2, über Vaillant-Gateway.
- Entitäten: Wärmepumpe `3`, Verdichter `3:1`, Warmwasser `4`, Zone 1 (`HeatingZone 5:1`, `HVACRoom 5:1:1`),
  Zone 2 (`6:1`, `6:1:1`), Außenfühler `7`.
- Betriebsarten: auto/on/off, **kein eco**.
- Raumtemperatur: Zone 2 immer mit Fehlerstatus; Zone 1 nur zeitweise gültig. Daher soll die Raumtemperatur je Zone
  künftig als Mittelwert gewählter HA-Sensoren berechnet werden (Phase 6).
- Nicht gemeldet: Vorlauf, Rücklauf, Verdichtertemperatur, Energiezähler, Werte je Phase.
- Nach jedem Start lehnt das Gateway die SHIP-Verbindung oft rund 2 Minuten lang ab (einmal 5,5 min), bevor sie
  hält. Ursache **unbekannt** (SHIP-Log mit `log_level: debug` steht aus).
- Der minütliche Snapshot-Lesezugriff hat die zyklischen Neuverbindungen der Bridge-Überwachung behoben (bestätigt).

## 7. Tests

- Python: 57 Tests (`pytest`), gegen die Fake-Bridge (echter gRPC-Server, zwei Zonen wie die Anlage, Zone 2 ohne
  Raumfühler). Kein Test spricht mit echter Hardware.
- Go (Fork): `go test -race ./...`; neue Tests u. a. `internal/grpc/hvac_zones_test.go`,
  `internal/usecases/entity_test.go`, `roomheatingsysfn_adapter_test.go`, `classification_test.go`, `registry_test.go`.
- Frontend: `svelte-check`, ESLint, Build; keine Unit-Tests der Oberfläche.

## 8. CI und Release

`.github/workflows/ci.yml`, alle Actions per Commit-SHA gepinnt:

| Job | Prüft |
|---|---|
| python | `ruff check`, `ruff format --check`, `mypy` (strict), `pytest` |
| go | `go vet`, `go test -race` im Submodul |
| frontend | `npm ci`, `svelte-check`, ESLint, Build |
| app | HA-App-Linter, hadolint, shellcheck der s6-Skripte |
| image | Multi-Arch-Build; bei Tag `v*` Veröffentlichung auf ghcr.io (Tag muss zur Version in `config.yaml` passen) |

Release: Version in `config.yaml`, `pyproject.toml`, `frontend/package.json` und `CHANGELOG.md` erhöhen, Tag
`v<version>` pushen.

## 9. Bekannte Grenzen und offene Punkte

Damit sie nicht als neue Funde gemeldet werden – Bewertung und bessere Lösungen sind aber willkommen:

1. Ursache der abgelehnten Verbindungsversuche nach dem Start ist unbekannt.
2. `GetRoomHeating` ohne Zone liefert bewusst die erste Zone (Abweichung von der ursprünglichen Planung, die dort
   bei mehreren Zonen einen Fehler vorsah). Die App nutzt nur `room_heating_zones`.
3. Die App schreibt noch nichts; Schreibschutz, Demo/Live und Protokoll kommen in Phase 5.
4. Raumtemperatur aus Home Assistant und Export nach HA kommen in Phase 6.
5. Im Fork gibt es einen Alt-Pfad in `hvac_service.go` für Quellen ohne Zonenliste (per Typ-Zusicherung). Er existiert
   für bestehende Test-Doubles; im Produktivbetrieb liefern alle Use Cases Zonenlisten.
6. **Bitte prüfen:** `recordCapabilitySupport()` in `internal/usecases/entity.go` (Original-Code) entfernt die
   Registry-Beobachtung einer Entität, wenn sie nicht die „erste passende“ ist. Bei zwei Zonen betrifft das die zweite
   Zone. Ob das Auswirkungen auf Gesundheits-/Bereitschaftsanzeigen hat, ist nicht untersucht.
7. `TemperatureMonitoringWrapper` wird für Raum- und Außentemperatur genutzt; Außentemperatur-Ereignisse tragen
   ebenfalls eine Zonen-ID (die des Außenfühlers). Der HVAC-Stream filtert nach Ereignistyp – vermutlich harmlos.

## 10. Wo eine Prüfung am meisten bringt

1. **Mehrzonen-Logik im Fork** (`25e061f..69234ad`): kann ein Schreibbefehl jemals die falsche Zone treffen?
   Stimmen Sortierung, Zuordnung Raum ↔ Zone ↔ Konfigurations-Entität nach Neuverbindungen, Fehlercodes?
2. **Nebenläufigkeit im Backend**: `StateService` (Ereignis-Stream, Entprellung, Listener-Queues, SSE),
   `PairingService.run()` (Wiederverbindung, Backoff), Abbruch beim Herunterfahren, Verhalten bei Bridge-Neustart.
3. **Sicherheit**: Ingress-Prüfung (nur 172.30.32.2), gRPC nur auf Loopback, Umgang mit fremder Bridge,
   Diagnose-Dump (enthält Seriennummern; SKI gekürzt), statische Dateien, Eingabeprüfung (`POST api/pairing/register`).
4. **Paketierung**: Dockerfile (Stufen, Pinning, Rauchtest), s6-Skripte (Quoting, atomares Schreiben der Seriennummer,
   Konfigurationsdatei), `config.yaml`.
5. **Oberfläche**: Wiederverbindung des Event-Streams, Darstellung „nicht verfügbar“, Barrierefreiheit,
   relative Pfade unter Ingress.
6. **Testlücken**: Was ist ungetestet und sollte vor Phase 5 (Schreiben) abgesichert sein?
7. **Lieferkette**: gepinnte Actions, Lock-Dateien mit Hashes, Basis-Images.

Nicht prüfen: erzeugte Dateien (`backend/gen/`, `*.pb.go`, `*_pb2*.py`), den Original-Code des Forks außerhalb der
oben genannten Commits (nur dort, wo er mit unseren Änderungen zusammenspielt), `custom_components/` im Fork.

## 11. Gewünschtes Berichtsformat

- Je Fund: **Schweregrad** (kritisch / hoch / mittel / niedrig / Hinweis), **Datei und Zeile** (mit Repo und Commit),
  **was passiert** (konkretes Szenario: Eingabe/Zustand → falsches Verhalten), **Vorschlag**.
- Zuerst die schwersten Funde, am Ende eine kurze Gesamteinschätzung.
- Unsichere Funde als solche kennzeichnen.

## 12. Lokal ausprobieren (ohne Wärmepumpe)

```bash
git clone --recurse-submodules https://github.com/wattarosolar-lgtm/vaillant-eebus.git
cd vaillant-eebus
python3.13 -m venv .venv && .venv/bin/pip install --require-hashes -r backend/requirements-dev.lock
.venv/bin/python backend/scripts/generate_proto.py --typed
.venv/bin/pytest
cd bridge/eebus-bridge && go test -race ./...
```

Weitere Befehle (Fake-Bridge, Oberfläche, Lock-Dateien) stehen in der `README.md`.
