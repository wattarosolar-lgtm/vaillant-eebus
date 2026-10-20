# Briefing: Vaillant EEBUS (Stand v0.6.1)

Dieses Dokument fasst alles Wichtige über den Code zusammen – für eine KI, die den Code prüft, und als Übergabe
an eine neue Arbeitssitzung. Bitte den Prüfbericht **auf Deutsch** verfassen (Code-Begriffe dürfen englisch bleiben).

## 1. Worum es geht

Eine **Home-Assistant-App** (früher „Add-on“), die eine Vaillant-Wärmepumpe **lokal über EEBUS** anbindet –
ohne Vaillant-Cloud, ohne eBUS-Adapter, ohne MQTT. Die App bringt eine eigene Oberfläche mit (Seitenleiste
„Wärmepumpe“ in Home Assistant, über Ingress).

- **Stand:** Version 0.6.1, Phasen 1–6 von 7 umgesetzt (Abnahme von Phase 5 und 6 an der Anlage offen). 0.5.1 enthält
  die Korrekturen aus dem Fehlertest vor dem Live-Test (Abschnitt 5a), 0.5.2 die aus der ersten Abnahme an der
  Anlage (ziehbarer Regler, Sperre im Auto-Betrieb, ruhigere Bedienung, „Energie heute“), 0.5.3 zeigt im
  Auto-Betrieb wieder Sollwert und Bogen (weiter gesperrt). 0.6.0 bindet Home Assistant an (Abschnitt 5b), 0.6.1
  ordnet die Einstellungsseite neu und addiert mehrere Wärmesensoren.
- **Die App schreibt jetzt** – ausschließlich über `write_gate.py` (Abschnitt 5a). Nach der Installation läuft sie im
  Demo-Betrieb und sendet nichts; erst nach ausdrücklicher Bestätigung im Live-Betrieb.
- Konvention: Code und Kommentare englisch, Oberfläche und Nutzer-Doku deutsch.

| Phase | Inhalt | Stand |
|---|---|---|
| 1 | Repository, Bridge-Fork, Dockerfile, App startet | erledigt (0.1.0) |
| 2 | Kopplung, Diagnose-Dump, Einrichtungsassistent | erledigt (0.2.0/0.2.1) |
| 3 | Lesen: Zustand, API, Übersicht, Live-Updates | erledigt (0.3.0) |
| 4 | Mehrere Heizzonen in der Bridge, Eco nur wenn angeboten | erledigt (0.4.0), an der Anlage abgenommen |
| 5 | Schreibschutz, Demo/Live, Steuerung in der Oberfläche, Protokoll | umgesetzt (0.5.0, Korrekturen 0.5.1–0.5.3), Abnahme an der Anlage offen |
| 6 | HA-Import (Raumtemperatur je Zone als Mittelwert gewählter HA-Sensoren, Wärme heute), HA-Export als Sensoren | umgesetzt (0.6.0), Abnahme an der Anlage offen |
| 7 | Einstellungs-/Protokollseite fertig, Doku, Release | offen |

## 2. Repositories

| Repo | Rolle |
|---|---|
| https://github.com/wattarosolar-lgtm/vaillant-eebus | **Die App** (dieses Repo). Phase 5 auf `main`; Korrekturen aus den Abnahmen auf Branches `fix/abnahme-…`. |
| https://github.com/wattarosolar-lgtm/HEMS-EEBUS | **Fork der EEBUS-Bridge** (Go), eingebunden als Git-Submodul `bridge/`, fest auf Commit `78a1a5a` (Branch `feat/multi-zone`). Muss öffentlich bleiben, sonst kann die CI ihn nicht laden. |
| https://github.com/volschin/eebus-ha-bridge | Original der Bridge (MIT-Lizenz). Der Fork-`main` (`25e061f`) entspricht dem Original. |

**Eigener Code im Fork** sind nur diese fünf Commits auf `25e061f`; alles andere ist Code des Originals:

| Commit | Inhalt |
|---|---|
| `6772a26` | Nur lesender Struktur-Dump des Geräts (`GetDeviceStructureDump`, `internal/eebus/structure_dump.go`) |
| `e9baadc` | Gerätesuche liefert Marke/Modell/Seriennummer/Typ; `ServerInfo.device_serial` |
| `47ce5ff` | Gerätesuche liefert mDNS-Name, SHIP-ID, Gerätekategorien |
| `69234ad` | **Mehrere Heizzonen** (siehe Abschnitt 5) |
| `78a1a5a` | Korrekturen aus der Prüfung von v0.4.0: Schreiben nie an eine andere Zone (auch bei nur einem Konfigurations-Kandidaten Adressvergleich; benannte Zone ohne Zonenliste → `NOT_FOUND`), Zonennamen ohne Entität verworfen |

Prüfen: `git diff 25e061f..78a1a5a` im Fork, ohne die erzeugten Dateien (`*.pb.go`,
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
  `/data/settings.json` (gekoppelte Gateway-SKI, Demo/Live), `/data/app.db` (Protokoll, SQLite).
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
| `main.py` | Konfiguration aus Umgebungsvariablen (`BRIDGE_ADDRESS`, `DATA_DIR`, `HTTP_HOST`, `HTTP_PORT`, `TRUSTED_CLIENTS`, `STATIC_DIR`, `BRIDGE_SERIAL_FILE`, `SHIP_PORT`, `TZ`, …; ungültige Ports → klare Fehlermeldung); startet uvicorn, `PairingService.run()`, `StateService.run()`, `WriteGate.run()` und `EnergyMeter.run()` in einer `asyncio.TaskGroup`, speichert danach den Energiezähler und schließt das Protokoll |
| `bridge_client.py` | gRPC-Client (`grpc.aio`): Server-Info, Gerätesuche, Kopplung, Gerätestatus, Snapshot, Ereignis-Streams, Struktur-Dump; **Schreibmethoden** (`WRITE_METHODS`: Zone-Soll/-Betriebsart mit `zone_id`, Warmwasser-Soll/-Betriebsart/Einmalladung, Leistungsdeckel, Failsafe, Verdichter-Flexibilität) mit 15 s Zeitlimit (die Bridge wartet bis zu 10 s auf das Gerät); Backoff, `RETRYABLE_CODES`, max. Nachrichtengröße 64 MB |
| `pairing.py` | Kopplung und Verbindungszustand (`ConnectionState`); prüft, ob die eigene Bridge antwortet; erkennt fremde Bridge und belegten SHIP-Port; meldet das gekoppelte Gateway nach jedem Bridge-Neustart wieder an; `ConnectionLog` fasst abgelehnte Verbindungsversuche beim Start zusammen und schreibt Verbindungsereignisse deutsch ins Protokoll; Diagnose-Dump |
| `write_gate.py` | **Einzige Stelle, die schreibt** (Abschnitt 5a). |
| `energy.py` | `EnergyMeter`: summiert die gemeldete Gesamtleistung (`power_consumption`) über die Zeit zu Wh je Tag (die Anlage liefert keine Energiezähler); > 3 min ohne Messwert wird nicht überbrückt, der Tag gilt dann als lückenhaft (auch der erste Tag); Tageswechsel um Mitternacht in `TZ` (Rückfall UTC mit Warnung); `/data/energy.json` atomar, höchstens einmal pro Minute und beim Beenden, 400 Tage; kurzer Neustart (< 3 min) wird überbrückt. `today()` zählt das laufende Intervall bis jetzt mit. |
| `ha_client.py` | Zugriff auf HA Core über den Supervisor (`SUPERVISOR_TOKEN`, `http://supervisor/core`): REST (Zustände, eigene Sensoren setzen/entfernen) und eine WebSocket-Verbindung (Anmeldung, `subscribe_trigger` auf die gewählten Entitäten mit Rückfall auf `platform`, `homeassistant_started`), Wiederverbindung mit Backoff, alle 5 min erneutes Lesen (`last_reported`). Schreibt nur `sensor.vaillant_eebus_*`/`binary_sensor.vaillant_eebus_*` (`OWN_ENTITY`, sonst `ValueError`). Listener-Schnittstelle `HomeAssistantListener`. |
| `ha_import.py` | Räume (Name, Zone), Mittelwert je Zone aus gültigen Werten (Zahl, °C/°F, ≤ 30 min alt), Wärme heute als Summe von bis zu 4 Sensoren (Wh/kWh/MWh, ≤ 3 h alt; Summe nur, wenn jeder einen Wert hat, Status = schlechtester); bei Verbindungsverlust werden Werte verworfen; meldet Änderungen gedrosselt (2 s) per `state.republish()`. |
| `ha_export.py` | Sensoren nach Gruppen (`ExportGroup`): Status, Zonen (Raumtemperatur = HA-Mittelwert, Soll, Betriebsart), Warmwasser, Temperaturen, Leistung/Energie (inkl. `energy_today`, `total_increasing`), Leistungsdeckel, Verdichter. Nur Werte, die die Anlage je geliefert hat; fehlt einer später → `unavailable`. Je Sensor höchstens alle 10 s, Energie 60 s; nach Neuverbindung/`homeassistant_started` alles neu; abgewählte Gruppen per DELETE entfernt. |
| `home_assistant.py` | Fassade für `main`/`api`: verbindet Client, Import und Export; `entities(kind, q)` für die Auswahl; `change()` speichert Räume/Wärmesensor/Export und protokolliert (`settings`); `performance_today()` (Wärme ÷ Strom, nur aktuell, lückenlos, ≥ 0,5 kWh). Ohne Token kein Client. |
| `log_store.py` | Protokoll in SQLite (`/data/app.db`): `add()` kehrt sofort zurück, alle DB-Arbeit läuft geordnet in einem Worker-Thread; höchstens 10.000 Einträge; Kategorien `mode`, `write`, `connection`, `device`; beschädigte DB wird beiseitegelegt |
| `state.py` | `StateService`: liest den Geräte-Snapshot nach jedem Zustandsereignis der Bridge (1 s gebündelt) und **spätestens jede Minute** – diese Leseanfrage braucht auch die Verbindungsüberwachung der Bridge (ohne Lesezugriff baut sie die Verbindung nach 2 min neu auf). Bildet `DeviceSnapshot` auf `HeatPumpState` ab (Messwerte, Zonen, Warmwasser, Status). Nicht gemeldete Werte bleiben `None`, nie Ersatzwerte. Verdichter gilt als aktiv ab 100 W. Liest auch `mode_writable`, Leistungsdeckel, Failsafe und Verdichter-Flexibilität. `refresh_now()` (nach einem Schreibvorgang) und `republish()` (Modus/offene Schreibvorgänge geändert). |
| `api.py` | FastAPI: `GET api/pairing`, `GET api/state`, `GET api/events` (Server-Sent Events, Keep-alive alle 15 s; ein fehlschlagender Bridge-Aufruf überspringt eine Nachricht statt den Stream zu beenden), `POST api/pairing/register`, `GET api/diagnostics/dump`, `GET/POST api/mode`, `POST api/zones/{zone_id}/setpoint|mode`, `POST api/dhw/setpoint|mode|boost`, `POST api/lpc`, `POST api/lpc/failsafe`, `POST api/ohpcf`, `GET api/log`; Middleware lässt nur die Ingress-Adresse zu; gRPC-Fehler → HTTP mit „(Fehlercode X)“; `WriteRejected` → 4xx mit deutscher Begründung (429 mit `Retry-After`); `index.html` mit `no-cache`, Assets `immutable` |
| `settings.py` | `SettingsStore` ist alleiniger Besitzer der aktuellen Einstellungen (`current`, `update()`), damit Kopplung und Moduswechsel sich nicht überschreiben; `settings.json` atomar schreiben; beschädigte Datei wird beiseitegelegt (→ Demo) |

### Oberfläche (`frontend/src/`)

Svelte 5 (Runes), Hash-Routing (`#/uebersicht`, `#/protokoll`, `#/einstellungen`, `#/einrichtung`), nur relative URLs
(Ingress-Pfad), Live-Daten über `EventSource('api/events')`. Seiten: `Overview.svelte` (Rundinstrumente je Zone und
Warmwasser mit Bedienelementen, Karten für Leistung/Energie und Temperaturen), `Log.svelte` (Protokoll mit Filter und
„Ältere laden“), `Setup.svelte` (Einrichtungsassistent), `Settings.svelte` (Kopplung, Diagnose-Dump).
Komponenten: `Dial.svelte` (Rundinstrument; besitzt den Entwurf der Solltemperatur: Bogen ziehbar per Pointer,
Pfeiltasten als `role="slider"`, −/+; gesendet 1,2 s nach dem Loslassen bzw. letzten Klick, im Live-Betrieb nicht
vor Ablauf der 5 s seit dem letzten Senden; Zone im Auto-Betrieb zeigt den gemeldeten Sollwert, gesperrt),
`SetpointControl.svelte` (Anzeige und −/+), `ModeSegments.svelte`, `BoostSwitch.svelte`, `ModeSwitch.svelte`
(Demo | Live), `ModeBanner.svelte` (Band nur im Demo-Betrieb, Bestätigung für Live), `HeaderStatus.svelte`,
`StatusPill.svelte`, `DumpButton.svelte`. `lib/feedback.svelte.ts` hält Vorschau (nur Demo, 5 s, blau), den gerade
gesendeten Wert (bis das Gerät oder `pending` ihn zeigt, höchstens 5 s) und Fehler eines Bedienelements. Keine
Statuszeilen mehr („Wird gesendet …“ usw.), nur für Screenreader (`.sr-only`); Fehler, „Ergebnis unbekannt“ und
„Am Gerät nicht änderbar“ bleiben sichtbar. Bedienelemente sind nach der Quittung des Geräts wieder frei; der
Wert bleibt blass, bis das Gerät ihn meldet. Texte in `lib/texts.ts`, Formatierung (de-DE) in `lib/format.ts`.

## 5. Mehrere Heizzonen (Bridge, Commits `69234ad` und `78a1a5a`)

Vorher nahm die Bridge pro Gerät die erste passende Raum-Entität und speicherte nur einen Zonennamen; bei zwei Zonen
stand der Name der zweiten über den Werten der ersten, und Werte konnten aus verschiedenen Zonen stammen.

- `internal/usecases/entity.go`: `compatibleEntities()` liefert alle Entitäten eines Geräts, numerisch nach Adresse
  sortiert; `sameZoneEntity()` ordnet Entitäten verschiedener Use Cases über die Adresse zu – seit `78a1a5a` auch
  bei nur einem Kandidaten; unbekannte Adresse → keine Zuordnung (fail closed).
- `internal/eebus/zones.go`: `ZoneID()` = SPINE-Adresse mit Punkten, z. B. `"5.1.1"`.
- `internal/eebus/registry.go`: Zonennamen je `HeatingZone`-Entität; Zuordnung Raum → Zone über Adresspräfix
  (`5.1.1` gehört zu `5.1`), bei Mehrdeutigkeit kein Name.
- `internal/usecases/classification.go`: Name wird je Zone gelesen und angefordert; Namen ohne Entität werden
  verworfen (`78a1a5a`).
- `internal/grpc/hvac_service.go`: `ListRoomHeatingZones`; Lesen je Zone (Name, Raum-Ist, Sollwert mit Grenzen,
  Betriebsart); `GetRoomHeating` liefert weiter **eine** Zone (die erste nach Adresse) für ältere Clients.
  Schreiben mit optionaler `zone_id`: ohne sie nur bei genau einer Zone, sonst `FAILED_PRECONDITION`; unbekannte Zone
  `NOT_FOUND` (seit `78a1a5a` auch, solange noch keine Zone ausgehandelt ist); Grenzen der jeweiligen Zone. Quellen
  ohne Zonenliste (Test-Doubles) laufen über den alten Einzelpfad.
- Protos: `hvac_service.proto` (`RoomHeatingZone`, `RoomHeatingZoneList`, `zone_id`), `device_service.proto`
  (`DeviceSnapshot.room_heating_zones`, Feld 29).
- Ereignisse tragen die Zonen-ID (`eebus.Event.ZoneID`).
- Eco wird nur durchgereicht, wenn das Gerät es anbietet (Test).

## 5a. Schreiben: `write_gate.py`

- Befehle: `SetZoneSetpoint`, `SetZoneMode`, `SetHotWaterSetpoint`, `SetHotWaterMode`, `SetHotWaterBoost`,
  `SetConsumptionLimit`, `SetFailsafeLimit`, `ControlCompressor`. `submit()` hält je Steuerung (Art + Zone) ein
  `asyncio.Lock`.
- Vorbedingungen: gekoppelt (`NotPairedError`), Zustand lesbar und verbunden (409). `expected_mode` (über die API
  Pflichtfeld `operating_mode`): der Betrieb, den der Nutzer beim Ändern sah; weicht er ab → 409, nichts gesendet. Die
  Oberfläche verwirft gesammelte Klicks bei einem Moduswechsel.
- Prüfung gegen die Gerätewerte **in beiden Modi**: Zone bekannt (404), schreibbar (409), Bereich und Schrittweite
  (422; Schrittweite ≤ 0 → 409, fail closed), Betriebsart angeboten (422). Zonen-Solltemperatur bei Zone im
  Auto-Betrieb → 409 (wirkt dort nicht, Feldtest 0.5.1; Boost über myVAILLANT/HA).
- **Demo:** nichts senden. Zonen/Warmwasser → `WriteResult(sent=False, would_send=…)` und „Würde senden: …“ im
  Protokoll; Leistungsdeckel/Failsafe/Verdichter → „Im Demo-Betrieb gesperrt“.
- **Live:** gleicher Wert wie am Gerät oder wie ein noch offener Schreibvorgang → nicht senden; höchstens ein
  Befehl je Steuerung alle 5 s (429, `Retry-After`). „Wird gesendet“ steht per `LogStore.record()` in der Datenbank,
  bevor die Bridge den Befehl sieht. Fehler: `INVALID_ARGUMENT`, `FAILED_PRECONDITION`, `NOT_FOUND`, `OUT_OF_RANGE`,
  `UNIMPLEMENTED`, `PERMISSION_DENIED`, `UNAUTHENTICATED` → „Nicht ausgeführt“ (sicher nicht angekommen); jeder
  andere Code (Bridge meldet z. B. `INTERNAL`, wenn die Antwort des Geräts nach 10 s ausbleibt) → „Ergebnis
  unbekannt“, `WriteOutcomeUnknown` (HTTP 504), der Wert wird wie ein gesendeter weiter verfolgt. Kein automatischer
  Wiederholversuch. Offene Schreibvorgänge (`pending`) werden bestätigt, sobald der Snapshot den Wert zeigt, sonst
  nach 30 s „Nicht bestätigt“; ist das Gerät dann nicht erreichbar, klärt der erste Zustand nach der Neuverbindung
  das Ergebnis („Nachträglich bestätigt“ / „Nicht übernommen“).
- `run()` protokolliert den Modus beim Start und erkennt Änderungen außerhalb der App (Kategorie `device`).
- Moduswechsel: Live nur mit `confirm: true` (400 sonst); gespeichert in `settings.json`; Wechsel protokolliert.
- Ein Strukturtest (AST) stellt sicher, dass nur `write_gate.py` die `WRITE_METHODS` des Bridge-Clients aufruft.

## 5b. Home Assistant (Phase 6)

- Die App liest nur und meldet eigene Sensoren; keine Dienste, keine fremden Entitäten (Test
  `test_only_the_apps_own_sensors_can_be_written`). Nichts davon läuft über `write_gate`, weil nichts an die
  Wärmepumpe geht.
- Einstellungen (`settings.json`): `rooms` (`entity_id`, `name`, `zone_id`), `heat_energy_entities` (0.6.0:
  einzelnes `heat_energy_entity`, wird übernommen), `export`
  (Gruppen, Vorgabe ohne Leistungsdeckel/Verdichter). Ungültige Einträge werden einzeln verworfen, Kopplung und
  Modus bleiben.
- API: `GET/PUT api/settings`, `GET api/ha/entities?kind=temperature|energy&q=`; `api/state` mit `home_assistant`
  und `performance_today`.
- Oberfläche: Einstellungen „Räume aus Home Assistant“, „Wärme heute“, „Export nach Home Assistant“; Zonenkreis mit
  Mittelwert, Karte „Innentemperaturen“, „Wärme heute“ und „Arbeitszahl heute“ in „Leistung und Energie“.
- Tests gegen `tests/fake_home_assistant.py` (aiohttp: REST, WebSocket mit Anmeldung, Trigger, Neustart).
- Nicht umgesetzt: aktiver Sollwert je Zone aus der myVAILLANT-Integration (Nutzer: „noch nicht“), Verlauf 24 h.

## 6. Die echte Anlage (Feldbefunde)

- Wärmepumpe: Vaillant aroTHERM split plus, VWL 75/8.2 AS 230V S2, über Vaillant-Gateway.
- Entitäten: Wärmepumpe `3`, Verdichter `3:1`, Warmwasser `4`, Zone 1 (`HeatingZone 5:1`, `HVACRoom 5:1:1`),
  Zone 2 (`6:1`, `6:1:1`), Außenfühler `7`.
- Betriebsarten: auto/on/off, **kein eco**.
- Raumtemperatur: Zone 2 immer mit Fehlerstatus; Zone 1 nur zeitweise gültig. Daher soll die Raumtemperatur je Zone
  künftig als Mittelwert gewählter HA-Sensoren berechnet werden (Phase 6).
- Nicht gemeldet: Vorlauf, Rücklauf, Verdichtertemperatur, Energiezähler, Werte je Phase, Wärmemenge. Gemeldet:
  elektrische Gesamtleistung der Wärmepumpe und des Verdichters (daraus „Energie heute“).
- Nach jedem Start lehnt das Gateway die SHIP-Verbindung oft rund 2 Minuten lang ab (einmal 5,5 min), bevor sie
  hält. Ursache **unbekannt** (SHIP-Log mit `log_level: debug` steht aus).
- Der minütliche Snapshot-Lesezugriff hat die zyklischen Neuverbindungen der Bridge-Überwachung behoben (bestätigt).
- 0.4.0 an der Anlage (19.10.2026, 20:33): Verbindung sofort, ohne abgelehnte Versuche; zwei Kacheln „Zone 1“/„Zone 2“.
- **Sollwert im Auto-Betrieb:** Das Gateway meldet je Zone genau einen Sollwert (Zone 2: 23 °C), die myVAILLANT-App
  zeigt im Auto-Betrieb mit Zeitprogramm aber 21,5 °C. Die Temperatur des laufenden Zeitfensters kommt nicht über
  EEBUS. **Feldtest 20.10.2026:** Erhöhen/Senken im Auto-Betrieb über EEBUS bewirkt nichts; myVAILLANT startet
  dabei eine zeitweise Übersteuerung (3 h, Dauer änderbar, späteres Ändern der Temperatur verlängert sie nicht).
  Diese Übersteuerung und Änderungen in myVAILLANT im Auto-Betrieb sind über EEBUS nicht sichtbar, die Zonen bieten
  keinen Overrun an (nur Warmwasser `oneTimeDhw`). Entscheidung: kein Nachbau, App sperrt die Solltemperatur im
  Auto-Betrieb (0.5.3: Wert und Bogen sichtbar, nicht verstellbar), Boost über myVAILLANT/HA. Ob die Zone in „Ein“
  auf den EEBUS-Sollwert heizt, ist noch zu prüfen. Den aktiven Sollwert des Zeitprogramms könnte Phase 6 aus der
  myVAILLANT-Integration in HA übernehmen.
- Vom Gerät gemeldet: Zonen 5–30 °C in 0,5er-Schritten, Warmwasser 35–70 °C in 1er-Schritten, Betriebsarten
  änderbar, Einmalladung schreibbar, Leistungsdeckel 0 W inaktiv, Failsafe 1.000.000 W für 2 h.

## 7. Tests

- Python: 152 Tests (`pytest`), gegen die Fake-Bridge (echter gRPC-Server, zwei Zonen wie die Anlage, Zone 2 ohne
  Raumfühler) und ein Fake-Home-Assistant (`tests/fake_home_assistant.py`). Die Fake-Bridge prüft Schreibbefehle wie die echte Bridge und zählt jeden in `write_calls`;
  Abnahmetest: im Demo-Betrieb erreicht kein Schreibbefehl die Bridge (alle Steuerungen, auch über die HTTP-API).
  Kein Test spricht mit echter Hardware.
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

1. Ursache der abgelehnten Verbindungsversuche nach dem Start ist unbekannt (tritt nur manchmal auf).
2. `GetRoomHeating` ohne Zone liefert bewusst die erste Zone (Abweichung von der ursprünglichen Planung, die dort
   bei mehreren Zonen einen Fehler vorsah). Die App nutzt nur `room_heating_zones`.
3. Leistungsdeckel, Failsafe und Verdichter-Flexibilität sind nur über die API erreichbar und **nicht an der Anlage
   getestet**. Ein niedriger Failsafe-Wert würde die Wärmepumpe nach jedem Neustart der App bis zu 2 h drosseln. Für
   Leistungsdeckel und Failsafe wartet die Bridge nicht auf eine Quittung des Geräts („Gesendet“ = abgeschickt).
10. Der gRPC-Port der Bridge (127.0.0.1:50471) hat keine Anmeldung. Andere Prozesse im Host-Netzwerk (unter HAOS auch
    HA Core) könnten an der App und ihrem Demo-Betrieb vorbei schreiben. Absicherung (Token) ist für eine spätere
    Phase vorgesehen.
4. Raumtemperatur und Wärmemenge aus Home Assistant sowie Export nach HA sind umgesetzt (0.6.0), an der Anlage noch nicht geprüft.
5. Im Fork gibt es einen Alt-Pfad in `hvac_service.go` für Quellen ohne Zonenliste (per Typ-Zusicherung). Er existiert
   für bestehende Test-Doubles; im Produktivbetrieb liefern alle Use Cases Zonenlisten.
6. `recordCapabilitySupport()` in `internal/usecases/entity.go` (Original-Code) entfernt die Registry-Beobachtung
   der zweiten Zone. Bewertet in der Prüfung von v0.4.0: kein Einfluss auf Raumheizung, Gesundheit oder
   Bereitschaft; nur Registry-Fallbacks anderer Werte wären „nicht verfügbar“, nie falsch. Bewusst nicht geändert.
7. `TemperatureMonitoringWrapper` wird für Raum- und Außentemperatur genutzt; Außentemperatur-Ereignisse tragen
   ebenfalls eine Zonen-ID (die des Außenfühlers). In der Prüfung als harmlos bestätigt.
8. Zonen ohne eigenen Namen heißen „Zone N“ nach ihrer Position unter den gerade lesbaren Zonen; die Nummer ist nur
   stabil, solange alle Zonen lesbar sind (die echte Anlage benennt ihre Zonen).
9. Die Prüfung von v0.4.0 (Funde 1–10) ist eingearbeitet: 1, 2, 5 im Fork (`78a1a5a`); 3, 7, 8, 9 in der App; 6
   dokumentiert; 4 bewertet (Punkt 6); Testlücken aus 10 geschlossen.

## 10. Wo eine Prüfung am meisten bringt

1. **Schreibschutz** (`write_gate.py`): Kann im Demo-Betrieb irgendein Weg einen Schreibbefehl an die Bridge
   bringen? Sind Sperre je Steuerung, 5-s-Grenze, Duplikaterkennung und Bestätigung nebenläufig korrekt? Kann ein
   Befehl die falsche Zone treffen (App → `zone_id` → Fork `78a1a5a`)?
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
