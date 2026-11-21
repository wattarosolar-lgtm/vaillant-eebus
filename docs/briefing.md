# Briefing: Vaillant EEBUS (Stand v1.3.1)

Dieses Dokument fasst alles Wichtige über den Code zusammen – für eine KI, die den Code prüft, und als Übergabe
an eine neue Arbeitssitzung. Bitte den Prüfbericht **auf Deutsch** verfassen (Code-Begriffe dürfen englisch bleiben).

## 1. Worum es geht

Eine **Home-Assistant-App** (früher „Add-on“), die eine Vaillant-Wärmepumpe anbindet – seit 1.2.0 wahlweise über die
**Entitäten der myVAILLANT-Integration** in Home Assistant (Vaillant-Cloud) oder **lokal über EEBUS** (ohne Cloud,
ohne eBUS-Adapter, ohne MQTT; Abschnitt 5e). Die App bringt eine eigene Oberfläche mit (Seitenleiste
„Wärmepumpe“ in Home Assistant, über Ingress).

- **Stand:** Version 1.2.0: die 7 Phasen der Spezifikation (1.0.0, an der Anlage abgenommen), Phase 8, das
  Energiemanagement (HEMS, Abschnitt 5d), und Phase 9, die Anbindung myVAILLANT (Abschnitt 5e); Abnahme beider an
  der Anlage offen. 0.5.1 enthält
  die Korrekturen aus dem Fehlertest vor dem Live-Test (Abschnitt 5a), 0.5.2 die aus der ersten Abnahme an der
  Anlage (ziehbarer Regler, Sperre im Auto-Betrieb, ruhigere Bedienung, „Energie heute“), 0.5.3 zeigt im
  Auto-Betrieb wieder Sollwert und Bogen (weiter gesperrt). 0.6.0 bindet Home Assistant an (Abschnitt 5b), 0.6.1
  ordnet die Einstellungsseite neu und addiert mehrere Wärmesensoren. 1.0.0 schließt ab: Protokoll (Probleme,
  Datum, CSV), Verlauf 24 h, „Über die App“, TLS mit Token für die Bridge (Abschnitt 5c), Doku, Aufräumen. 1.1.0
  bringt den Planer für Warmwasser und Heizen nach Preis, PV-Prognose und Wetter. 1.2.0 (Phase 9)
  bringt die Anbindung myVAILLANT (Abschnitt 5e) und die Korrekturen aus der Prüfung von
  1.1.0, die als 1.1.1 geplant waren; 1.2.1 die Korrekturen aus deren Abnahme. **1.3.0** ersetzt den Planer durch
  Stufe 1 des neuen Energiemanagements: beobachten, lernen, planen (nur Vorschau, kein neuer Schreibweg;
  Abschnitt 5d). 1.3.1 macht die Live-Verbindung der Oberfläche robust (Wiederverbindung, Wächter, Neuladen nach
  Update).
- **Die App schreibt jetzt** – ausschließlich über `write_gate.py` (Abschnitt 5a). Nach der Installation läuft sie im
  Demo-Betrieb und sendet nichts; erst nach ausdrücklicher Bestätigung im Live-Betrieb.
- Konvention: Code und Kommentare englisch, Oberfläche und Nutzer-Doku deutsch.

| Phase | Inhalt | Stand |
|---|---|---|
| 1 | Repository, Bridge-Fork, Dockerfile, App startet | erledigt (0.1.0) |
| 2 | Kopplung, Diagnose-Dump, Einrichtungsassistent | erledigt (0.2.0/0.2.1) |
| 3 | Lesen: Zustand, API, Übersicht, Live-Updates | erledigt (0.3.0) |
| 4 | Mehrere Heizzonen in der Bridge, Eco nur wenn angeboten | erledigt (0.4.0), an der Anlage abgenommen |
| 5 | Schreibschutz, Demo/Live, Steuerung in der Oberfläche, Protokoll | umgesetzt (0.5.0, Korrekturen 0.5.1–0.5.3), an der Anlage abgenommen |
| 6 | HA-Import (Raumtemperatur je Zone als Mittelwert gewählter HA-Sensoren, Wärme heute), HA-Export als Sensoren | umgesetzt (0.6.0/0.6.1), weitgehend abgenommen (Prüfung von 0.6.1 offen) |
| 7 | Einstellungs-/Protokollseite fertig, Verlauf 24 h, Absicherung der Bridge, Doku, Release | umgesetzt (1.0.0), an der Anlage abgenommen (21.10.2026) |
| 8 | Energiemanagement: Planer für Warmwasser und Zonen, Tarife, PV-Überschuss, Wetter, Stufen Aus/Vorschau/Steuern | umgesetzt (1.1.0), Abnahme an der Anlage offen (erst Vorschau, dann Steuern) |
| 9 | Anbindung myVAILLANT (Entitäten der Integration) neben EEBUS, wählbar; Planer im Auto-Betrieb über Quick-Veto | umgesetzt (1.2.0), an der Anlage abgenommen (1.2.1) |
| Stufe 1 | Energiemanagement neu: 1.3.0 beobachten, lernen, planen (Vorschau); 1.4.0 Zonen; 1.5.0 Thermostate | 1.3.0 umgesetzt, Abnahme an der Anlage offen (1–2 Wochen beobachten) |

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
                                                                     │ gRPC, TLS + Token, nur 127.0.0.1:50471
                                                                     ▼
Home Assistant ──Ingress (Supervisor 172.30.32.2)──▶ backend (Python, FastAPI, 172.30.32.1:8714)
                                                       └─ liefert auch die Oberfläche (Svelte, statische Dateien)
```

- **Anbindung myVAILLANT (1.2.0):** Das Backend liest den Zustand aus Entitäten der myVAILLANT-Integration über die
  WebSocket-Verbindung zu Home Assistant und schreibt über Dienstaufrufe (Abschnitt 5e). Die Bridge läuft dann nicht:
  Ihr s6-Dienst liest `connection` aus `/data/settings.json` und schläft.
- `host_network: true` (mDNS, Port 4712). Folge: **alle Apps mit Host-Netzwerk teilen sich die Ports.** Deshalb
  eigener gRPC-Port 50471 (statt 50051) und eine Identitätsprüfung: Das Backend spricht nur mit einer Bridge, die
  die EEBUS-Seriennummer aus `/data/eebus/serial` meldet (`ServerInfo.device_serial`).
- Persistente Daten: `/data/eebus/` (Zertifikat und Seriennummer = SKI der App; Verlust erzwingt Neukopplung),
  `/data/eebus/grpc/` (TLS-Zertifikat, Schlüssel und Token der gRPC-API, Abschnitt 5c), `/data/settings.json`
  (gekoppelte Gateway-SKI, Demo/Live, Räume, Export), `/data/app.db` (Protokoll, SQLite), `/data/energy.json`.
- Das Gateway erlaubt **nur eine EEBUS-Verbindung** gleichzeitig.
- Image: `ghcr.io/wattarosolar-lgtm/vaillant-eebus:<version>` für amd64 und aarch64, von der CI gebaut; der
  Supervisor baut nicht selbst.

## 4. Aufbau dieses Repos

| Pfad | Inhalt |
|---|---|
| `repository.yaml` | App-Repository für den HA-App-Store |
| `vaillant-eebus/config.yaml` | App-Konfiguration (Version, Ingress-Port 8714, `host_network`, Option `log_level`) |
| `vaillant-eebus/Dockerfile` | Mehrstufig: Go-Bridge (Cross-Compile, CGO aus), Python-Stubs aus den Protos, Svelte-Build, Laufzeit auf `ghcr.io/home-assistant/base-python:3.13-alpine3.24-2026.10.0`; Rauchtest `import vaillant_eebus.main` |
| `vaillant-eebus/rootfs/etc/s6-overlay/s6-rc.d/init-grpc-credentials/` | s6-Oneshot vor Bridge und Backend: `python -m vaillant_eebus.bridge_auth /data/eebus/grpc` (Abschnitt 5c) |
| `vaillant-eebus/rootfs/etc/s6-overlay/s6-rc.d/eebus-bridge/run` | Erzeugt Seriennummer (einmalig) und Bridge-Konfiguration (`grpc.security.mode: tls_token`), startet die Bridge |
| `vaillant-eebus/rootfs/etc/s6-overlay/s6-rc.d/backend/run` | Setzt Umgebungsvariablen, startet das Backend |
| `vaillant-eebus/DOCS.md`, `CHANGELOG.md`, `translations/de.yaml`, `icon.png`, `logo.png` | Nutzer-Doku, Symbol und Logo im App-Store |
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
| `myvaillant.py` | Anbindung myVAILLANT (Abschnitt 5e): `MyVaillantState` (Zustand aus den gewählten Entitäten), `MyVaillantLink` (Befehle als Dienstaufrufe), `discover()` (Vorschlag der Entitäten) |
| `main.py` | Konfiguration aus Umgebungsvariablen (`BRIDGE_ADDRESS`, `DATA_DIR`, `HTTP_HOST`, `HTTP_PORT`, `TRUSTED_CLIENTS`, `STATIC_DIR`, `BRIDGE_SERIAL_FILE`, `SHIP_PORT`, `TZ`, …; ungültige Ports → klare Fehlermeldung); startet uvicorn, `PairingService.run()`, `StateService.run()`, `WriteGate.run()` und `EnergyMeter.run()` in einer `asyncio.TaskGroup`, speichert danach den Energiezähler und schließt das Protokoll |
| `bridge_auth.py` | Erzeugt einmalig selbstsigniertes Zertifikat (EC P-256, SAN `eebus-bridge` und `127.0.0.1`), Schlüssel und Token in `/data/eebus/grpc` (0600, alle drei zusammen über ein umbenanntes Verzeichnis); `BridgeCredentials.load()` für das Backend |
| `bridge_client.py` | gRPC-Client (`grpc.aio`), mit Zugangsdaten TLS (vertraut nur diesem Zertifikat) und Bearer-Token, sonst Klartext (Fake-Bridge); `refused_tls()` erkennt einen fehlgeschlagenen TLS-Handshake (→ fremde Bridge): Server-Info, Gerätesuche, Kopplung, Gerätestatus, Snapshot, Ereignis-Streams, Struktur-Dump; **Schreibmethoden** (`WRITE_METHODS`: Zone-Soll/-Betriebsart mit `zone_id`, Warmwasser-Soll/-Betriebsart/Einmalladung, Leistungsdeckel, Failsafe, Verdichter-Flexibilität) mit 15 s Zeitlimit (die Bridge wartet bis zu 10 s auf das Gerät); Backoff, `RETRYABLE_CODES`, max. Nachrichtengröße 64 MB |
| `pairing.py` | Kopplung und Verbindungszustand (`ConnectionState`); prüft, ob die eigene Bridge antwortet; erkennt fremde Bridge und belegten SHIP-Port; meldet das gekoppelte Gateway nach jedem Bridge-Neustart wieder an; `ConnectionLog` fasst abgelehnte Verbindungsversuche beim Start zusammen und schreibt Verbindungsereignisse deutsch ins Protokoll; Diagnose-Dump |
| `write_gate.py` | **Einzige Stelle, die schreibt** (Abschnitt 5a). |
| `energy.py` | `EnergyMeter`: summiert die gemeldete Gesamtleistung (`power_consumption`) über die Zeit zu Wh je Tag (die Anlage liefert keine Energiezähler); > 3 min ohne Messwert wird nicht überbrückt, der Tag gilt dann als lückenhaft (auch der erste Tag); Tageswechsel um Mitternacht in `TZ` (Rückfall UTC mit Warnung); `/data/energy.json` atomar, höchstens einmal pro Minute und beim Beenden, 400 Tage; kurzer Neustart (< 3 min) wird überbrückt. `today()` zählt das laufende Intervall bis jetzt mit. |
| `ha_client.py` | Zugriff auf HA Core über den Supervisor (`SUPERVISOR_TOKEN`, `http://supervisor/core`): REST (Zustände, Verlauf `history()` über `/api/history/period`, eigene Sensoren setzen/entfernen) und eine WebSocket-Verbindung (Anmeldung, `subscribe_trigger` auf die gewählten Entitäten mit Rückfall auf `platform`, `homeassistant_started`), Wiederverbindung mit Backoff, alle 5 min erneutes Lesen (`last_reported`). Schreibt nur `sensor.vaillant_eebus_*`/`binary_sensor.vaillant_eebus_*` (`OWN_ENTITY`, sonst `ValueError`). Listener-Schnittstelle `HomeAssistantListener`. |
| `ha_import.py` | Räume (Name, Zone), Mittelwert je Zone aus gültigen Werten (Zahl, °C/°F, ≤ 30 min alt), Wärme heute als Summe von bis zu 4 Sensoren (Wh/kWh/MWh, ≤ 3 h alt; Summe nur, wenn jeder einen Wert hat, Status = schlechtester); bei Verbindungsverlust werden Werte verworfen; meldet Änderungen gedrosselt (2 s) per `state.republish()`. |
| `ha_export.py` | Sensoren nach Gruppen (`ExportGroup`): Status, Zonen (Raumtemperatur = HA-Mittelwert, Soll, Betriebsart), Warmwasser, Temperaturen, Leistung/Energie (inkl. `energy_today`, `total_increasing`), Leistungsdeckel, Verdichter. Nur Werte, die die Anlage je geliefert hat; fehlt einer später → `unavailable`. Je Sensor höchstens alle 10 s, Energie 60 s; nach Neuverbindung/`homeassistant_started` alles neu; abgewählte Gruppen per DELETE entfernt. |
| `home_assistant.py` | Fassade für `main`/`api`: verbindet Client, Import und Export; `entities(kind, q)` für die Auswahl; `change()` speichert Räume/Wärmesensor/Export und protokolliert (`settings`); `performance_today()` (Wärme ÷ Strom, nur aktuell, lückenlos, ≥ 0,5 kWh); `power_history()` (Verlauf von `sensor.vaillant_eebus_power`, 60 s zwischengespeichert, sonst Grund). Ohne Token kein Client. |
| `history.py` | `bucket_means()`: zeitgewichtete 5-Minuten-Mittelwerte über 24 h aus den Zustandswechseln (Zustand gilt bis zum nächsten), Lücke bei weniger als 50 % Abdeckung, keine Zahl = fehlt; Mittel- und Höchstwert |
| `log_store.py` | Protokoll in SQLite (`/data/app.db`): `add()` kehrt sofort zurück, alle DB-Arbeit läuft geordnet in einem Worker-Thread; höchstens 10.000 Einträge; Kategorien `mode`, `write`, `connection`, `device`, `settings`; Kennzeichen `problem` (Spalte wird in alten DBs ergänzt); Seiten mit `before`/`after`/`before_time`; `entries()` und `csv_document()` für den Download; beschädigte DB wird beiseitegelegt |
| `state.py` | `StateService`: liest den Geräte-Snapshot nach jedem Zustandsereignis der Bridge (1 s gebündelt) und **spätestens jede Minute** – diese Leseanfrage braucht auch die Verbindungsüberwachung der Bridge (ohne Lesezugriff baut sie die Verbindung nach 2 min neu auf). Bildet `DeviceSnapshot` auf `HeatPumpState` ab (Messwerte, Zonen, Warmwasser, Status). Nicht gemeldete Werte bleiben `None`, nie Ersatzwerte. Verdichter gilt als aktiv ab 100 W. Liest auch `mode_writable`, Leistungsdeckel, Failsafe und Verdichter-Flexibilität. `refresh_now()` (nach einem Schreibvorgang) und `republish()` (Modus/offene Schreibvorgänge geändert). |
| `api.py` | FastAPI: `GET api/pairing`, `GET api/state`, `GET api/events` (Server-Sent Events, ohne Änderung alle 15 s ein Ereignis `ping` – kein Kommentar, damit die Seite es sieht; ein fehlschlagender Bridge-Aufruf überspringt eine Nachricht statt den Stream zu beenden), `POST api/pairing/register`, `GET api/diagnostics/dump`, `GET/POST api/mode`, `POST api/zones/{zone_id}/setpoint|mode`, `POST api/dhw/setpoint|mode|boost`, `POST api/lpc`, `POST api/lpc/failsafe`, `POST api/ohpcf`, `GET api/log` (`category`, `problems`, `before`, `after`, `until`), `GET api/log/export` (CSV), `GET api/history/power`, `GET/PUT api/settings`, `GET api/ha/entities`; Middleware lässt nur die Ingress-Adresse zu; gRPC-Fehler → HTTP mit „(Fehlercode X)“; `WriteRejected` → 4xx mit deutscher Begründung (429 mit `Retry-After`); `index.html` mit `no-cache`, Assets `immutable` |
| `hems/` | Energiemanagement, siehe Abschnitt 5d |
| `room_data.py` | Raumdaten je Raum (`RoomDetails`: statischer Abgleich, Thermostat, Soll, Zeitplan, Komfortband, Verluste) |
| `ids.py` | Formate von Entitäts- und Zonen-IDs (`ENTITY_ID`, `ZONE_ID`) |
| `settings.py` | `SettingsStore` ist alleiniger Besitzer der aktuellen Einstellungen (`current`, `update()`), damit Kopplung und Moduswechsel sich nicht überschreiben; `settings.json` atomar schreiben; beschädigte Datei wird beiseitegelegt (→ Demo) |

### Oberfläche (`frontend/src/`)

Svelte 5 (Runes), Hash-Routing (`#/uebersicht`, `#/protokoll`, `#/einstellungen`, `#/einrichtung`), nur relative URLs
(Ingress-Pfad), Live-Daten über `EventSource('api/events')` in `subscribeState()` (`lib/api.ts`): Der Browser
verbindet nur nach Netzwerkfehlern selbst neu; nach einer Fehlerantwort des Ingress (502 beim Neustart der App, 401
nach abgelaufener Sitzung) ist die EventSource zu – dann öffnet `subscribeState` eine neue (1/2/5/10 s, dann alle
15 s), ebenso nach 45 s ohne Nachricht (`ping`) und sofort bei `visibilitychange`/`online`. `App.svelte` vergleicht
`app_version` aus `api/connection` mit der beim Laden und lädt nach einem Update neu (bei ungespeicherten
Einstellungen nur Hinweis). Seiten: `Overview.svelte` (Rundinstrumente je Zone und
Warmwasser mit Bedienelementen, Karten für Leistung/Energie mit `PowerChart.svelte` (Verlauf 24 h, eigenes SVG,
Fadenkreuz, Tastatur als `role="slider"`, alle 5 min neu), Temperaturen, Innentemperaturen, gestrichelte Karte
„PV-Überschuss-Steuerung“), `Log.svelte` (Protokoll nach Tagen, Filter inkl. „Probleme“, „Springen zu“, alle 10 s
neue Einträge über `after`, CSV-Download, „Ältere laden“), `Setup.svelte` (Einrichtungsassistent), `Settings.svelte`
(Kopplung, Diagnose-Dump, „Über die App“, darunter `HomeAssistantSettings.svelte` mit Verbindungszustand).
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

- Die App liest nur und meldet eigene Sensoren; keine fremden Entitäten (Test
  `test_only_the_apps_own_sensors_can_be_written`). Einziger Dienst seit 1.1.0: `weather.get_forecasts` (liest nur;
  Test `test_weather_get_forecasts_is_the_only_service_the_app_calls`). Nichts davon läuft über `write_gate`, weil nichts an die
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
- Nicht umgesetzt: aktiver Sollwert je Zone aus der myVAILLANT-Integration (Nutzer: „noch nicht“). Der Verlauf 24 h kam mit 1.0.0.

## 5c. Absicherung der gRPC-API der Bridge (1.0.0)

- Alle Apps mit Host-Netzwerk (unter HAOS auch HA Core) teilen sich Loopback; ohne Anmeldung hätten sie an
  `write_gate` und dem Demo-Betrieb vorbei schreiben können (früher Punkt 10 der offenen Punkte).
- Die Bridge läuft im Modus `tls_token` des **Originals** (`internal/grpc/security.go`, `internal/config/config.go`):
  TLS mit Zertifikat/Schlüssel aus Dateien, Bearer-Token geprüft per SHA-256 und `ConstantTimeCompare` in Unary- und
  Stream-Interceptoren. Der Fork ist dafür **nicht** geändert.
- `bridge_auth.py` erzeugt Zertifikat, Schlüssel und Token einmalig im s6-Oneshot `init-grpc-credentials`, von dem
  Bridge und Backend abhängen (kein Wettlauf beim Start). Das Backend lädt sie über `BRIDGE_CREDENTIALS_DIR` und
  setzt `grpc.ssl_target_name_override = eebus-bridge`.
- Schlägt der TLS-Handshake fehl (Klartext-Dienst oder fremdes Zertifikat auf dem Port), meldet `api/pairing`
  `foreign_bridge`; die Prüfung der Seriennummer bleibt zusätzlich bestehen.
- Lokal Ende-zu-Ende gegen das echte Bridge-Binary geprüft: mit Zertifikat und Token Antwort, falsches Token
  `UNAUTHENTICATED`, Klartext abgewiesen.

## 5d. Energiemanagement (Stufe 1, 1.3.0: beobachten, lernen, planen)

Plan und Entscheidungen: `vaillant-eebus-notizen/plan-stufe-1-energiemanagement.md` (freigegeben 20.11.2026, Abschnitt
„Umsetzung 1.3.0“), `fahrplan-energiemanagement.md` (Punkte 1–9), `punkt1-trv-regelung.md` (Raummodell, Fenster,
Versorgung). Drei Releases: 1.3.0 ohne neuen Schreibweg, 1.4.0 Zonen (Quick-Veto, Zone-1-Takt-Logik,
Nachtabsenkung), 1.5.0 Thermostate. Der Planer aus 1.1/1.2 (Warmwasser/Zonen nach Preis und PV, `planner.py`,
`executor.py`, `supply.py`, `tariff.py`, `inputs.py`, `days.py`, `weather.py`) ist entfernt.

| Modul (`hems/`) | Aufgabe |
|---|---|
| `config.py` | `HemsSettings`: Stufe (`off`/`preview`; `control` ist für 1.4.0 vorgesehen und wird beim Laden zu `preview`, die API antwortet 409), Wetter, Lernquellen, Haus (Normaußentemperatur, Heizlast), Zonen (`ZoneConfig`: Art Fußbodenheizung/Heizkörper, Vorlauf-Entitäten, Auslegung, Band), Quick-Veto-Grenzen und Cloud-Budget (erst 1.4.0), Nachtfenster, Verluste, Ziel Starts, Heizgrenze, Blockbetrieb, Zone-1-Schwelle; ungültige Werte einzeln verworfen |
| `sources.py` | `HemsSources` (HA-Listener): Raum-, Thermostat-, Ventil-, Feuchte-, Vorlauf-, Puffer- und Wärmesensor-Zustände; meldet Änderungen (Fenster) und die Verbindung (neu planen) |
| `forecast.py` | Open-Meteo `/v1/dwd-icon` (stündlich, `past_days=7`, Koordinaten auf 2 Stellen), höchstens stündlich, nach Fehler 10 min, alte Prognose bis 6 h; Rückfall `weather.get_forecasts`; Eichung (Median gemessen − Prognose je lokaler Stunde, ab 3 Tagen, ±5 K); Day-ahead-Werte in `/data/forecast_check.json` → mittlerer Fehler |
| `statistics.py` | `StatisticsHistory`: HA-Langzeitstatistik (`ha_client.statistics()`, WebSocket `recorder/statistics_during_period`, nur lesen) für 400 Tage in Monatsstücken, danach nur Neues; `daily_values()` (lokale Tage) |
| `house_model.py` | `fit_house()` (Wärme/Tag = H·ΔT·24 − Gewinne, mit Sonne gemeinsam gefittet, ab 10 Heiztagen), `fit_cop()` (Arbeitszahl je Außentemperatur aus echter Wärme und Strom), `find_lag()`, `demand()` je Stunde und Zone (Anteil nach Heizlast der Räume), `start_model()` aus der Heizlast |
| `samples.py` | SQLite `/data/hems.db`: 5-Minuten-Proben (Räume, Außen, Leistung, Vorlauf, Zonen-Soll, Puffer), 21 Tage |
| `room_model.py` | Raummodell (punkt1 2/9a): Zeitkonstante und Fremdwärme aus Abkühlphasen, Aufheizgeschwindigkeit, Anlaufzeit, Bedarfsöffnung, Korrektur, Überschwingen; „lernt noch“ unter 5 Phasen; Abkühlung jetzt/Tabelle, Zeit bis zur unteren Komfortgrenze (Simulation mit Prognose); Versorgung und Vorlauf-Hinweis |
| `cycles.py` | Läufe, Pausen und Abtau-Muster aus der Leistung (Schwelle 100 W; Abtauen: Lauf ≥ 500 W → 2–15 min 200–600 W → ≥ 900 W in 4 min), Lücken als NaN; `CycleTracker` hält 48 h Punkte, Ereignisse 90 Tage in `/data/cycles.json`, holt nach dem Start 10 Tage aus dem HA-Verlauf nach |
| `defrost.py` | Abtauraten je Außenband (Startwerte aus den Felddaten 09.–19.11.2026, 10 Laufstunden Gewicht), Feuchtefaktor, Risiko je Stunde |
| `frost.py` | Frostschutz: < 4 °C länger als 4 h, auch für die Prognose-Stunden |
| `windows.py` | Fenster aus Temperatur und absoluter Feuchte (Regeln punkt1 Abschnitt 5) |
| `rules.py` | `make_plan()` (reine Funktion): 48 h je Zone und Stunde, Verschiebung im Band und Schritt, Soll je Raum, Gründe: warmer Tag, vorausschauend (Verzögerung je Zonenart), Abtaurisiko (höchstens 3 h, vorher speichern in der Fußbodenheizung), Nachtabsenkung (nur wenn es sich lohnt, Feuchteschutz ab 65 %, rechtzeitig wieder auf), Blockbetrieb, Vorlauf-Verschiebung, Frostschutz |
| `service.py` | `HemsService`: beobachtet jeden Zustand (Takte, Abtauen, Frostschutz, Zone-1-Logik „würde“), Proben alle 5 min (Fenster, Versorgung, Vorlauf „würde“ mit Abstand), lernt (Statistik alle 6 h, Räume stündlich), plant alle 15 min und nach dem Verbinden mit HA; Status, Plan mit Rückblick (`/data/plan_history.json`), Räume, CSV, Sensoren, Protokoll (Kategorien `hems`, `rooms`); Hinweis auf eine offene Verschiebung in einer alten `/data/hems.json` |

- **Kein Schreibweg:** Das Paket importiert weder `write_gate` noch den Bridge-Client und ruft weder `submit` noch
  `send_command`/`call_device_service`/`set_state` auf (Strukturtest). Gelesen wird über REST, WebSocket (nur die
  Befehle `auth`, `subscribe_*`, `unsubscribe_events`, `recorder/statistics_during_period`; Test) und Open-Meteo.
- **Raumdaten** (`room_data.py`, je Raum in `settings.json` unter `details`): Heizlast, Auslegungs-Soll, Heizflächen,
  Thermostat, Ventil, Feuchte, Raum-Soll, Zeitplan, Komfortband, Verluste, Drosseln, Notfallwert. `PUT api/settings`
  behält sie; `PUT api/rooms/{entity_id}` ändert sie. Ein Raum kann ein Thermostat (`climate.*`) als Temperatur
  haben (`current_temperature`); er zählt nicht im Zonen-Mittelwert.
- **API:** `GET api/hems`, `PUT api/hems/settings`, `POST api/hems/mode` (`control` → 409), `GET api/hems/plan`,
  `GET api/hems/plan/export`, `GET api/rooms`, `PUT api/rooms/{entity_id}`; `api/ha/entities` mit `room`,
  `thermostat`, `humidity`, `percent`.
- **Oberfläche:** Seiten `Plan.svelte`, `Rooms.svelte` (mit `RoomEditor.svelte`), Karten `HemsOverview.svelte`
  (Nächste Änderungen, Takten und Abtauen), `HemsSettings.svelte` neu.
- **Export** (Gruppe `hems`): `hems_status`, `hems_next_change`, `hems_plan`, `starts_today`, `defrosts_today`,
  `binary_sensor.vaillant_eebus_frost_protection`.
- **Bekannte Vereinfachungen:** Wärme je Zone nur nach Heizlast-Anteil geschätzt (myVAILLANT liefert Wärme nur fürs
  Haus); Verzögerung je Zone als Startwert (Fußbodenheizung 3 h, Heizkörper 1 h), gelernt nur fürs Haus; Sonne nur aus
  Tagen mit Einstrahlung (Prognose-Vergangenheit 7 Tage); Programmwert einer Zone in der Zukunft unbekannt (Soll
  „jetzt“ als Grundwert); Abtauerkennung nur am Leistungsmuster (an der Anlage zu prüfen).

## 5e. Anbindung myVAILLANT (Phase 9, 1.2.0)

Plan und Entscheidungen: `vaillant-eebus-notizen/plan-phase-9-myvaillant.md`. Grundlage ist der Quellcode der
Integration `signalkraft/mypyllant-component` v0.9.19 (nicht gegen eine echte Installation geprüft).

- **Einstellung** `settings.json`: `connection` (`myvaillant`, `eebus`; fehlt bei älteren Dateien → EEBUS, `null`
  bei neuer Installation → Assistent fragt, Backend arbeitet bis dahin wie EEBUS) und `myvaillant` (Zonen =
  Klima-Entitäten, Warmwasser, Außentemperatur, Leistung optional). Zonen-IDs sind dann die Entitäts-IDs
  (`ZONE_ID` erlaubt beides).
- **Lesen:** `StateHub` (in `state.py`) hält Zustand und Listener; `StateService` (EEBUS) und `MyVaillantState` sind
  Unterklassen. `MyVaillantState` ist `HomeAssistantListener`, baut `HeatPumpState` bei jedem Zustand neu und
  spätestens jede Minute (für den Energiezähler). `heat_cool` → „on“, `auto` → „auto“; Preset `boost` = Quick-Veto
  (`Zone.override`, `override_until`). Warmwasser „Cylinder Boost“ verdeckt die Betriebsart; die App merkt sich die
  letzte. Verbunden = alle gewählten Zonen und Warmwasser verfügbar; `captured_at` = ältestes `last_reported`.
- **Schreiben:** `WriteGate(..., link=MyVaillantLink)`; ohne Link wie bisher über die Bridge. Gleiche Prüfungen,
  Demo/Vorschau, 5-s-Grenze, Protokoll, Bestätigung (90 s statt 30 s). Unterschiede: Zonen-Soll im Auto-Betrieb
  erlaubt (Quick-Veto der Integration), in „Aus“ abgelehnt; neue Befehle `SetZoneOverride` (Quick-Veto mit Dauer,
  1–24 h, nur Auto) und `EndZoneOverride` für den Planer; Leistungsdeckel/Failsafe/Verdichter → 409. Fehler: HTTP
  400/401/403/404/405 oder keine Verbindung → `WriteNotExecuted`; 5xx, Zeitüberschreitung, Abbruch → „Ergebnis
  unbekannt“. `ha_client.call_device_service()` erlaubt genau `DEVICE_SERVICES` (6 Dienste) auf passenden
  Entitäten; Strukturtests: nur `myvaillant.py` ruft es auf, nur `write_gate.py` ruft `send_command()`.
- **Planer (1.2, mit 1.3.0 entfernt):** Zonen im Auto-Betrieb bekamen je Block ein Quick-Veto mit der Dauer des
  Blocks. Die Befehle `SetZoneOverride`/`EndZoneOverride` und `Source.PLANNER`/`preview=True` im Schreibschutz
  bleiben für 1.4.0 (Test des Vorschau-Wegs in `test_myvaillant.py`).
- **Umstellen:** `PUT api/connection` mit anderer Anbindung → Demo, speichern, `restart()` →
  uvicorn endet, `run_backend()` gibt `True` zurück, `_main()` startet den Bridge-Dienst per `s6-svc -r` neu
  (`BRIDGE_SERVICE_DIR`), der Prozess endet, s6 startet das Backend neu. Kopplung und Diagnose-Dump nur mit EEBUS
  (sonst 409).
- **Oberfläche:** Assistent mit Wahl der Anbindung (`Setup.svelte`, `MyVaillantChoice.svelte`, EEBUS-Schritte in
  `EebusSetup.svelte`), Karte „Anbindung“ in den Einstellungen, Hinweise zum Quick-Veto auf der Übersicht, „Stand“
  in der Kopfzeile.
- **1.2.1 (Abnahme an der Anlage, 19.11.2026):**
  - Eigene Sensoren (`ids.OWN_ENTITY`, `is_own_entity`) sind nie Quelle: `discover()` überspringt sie, `PUT
    api/connection` lehnt sie ab (422), `HomeAssistant._drop_own_sources()` entfernt eine gespeicherte (1.2.0 schlug
    `sensor.vaillant_eebus_outdoor_temperature` vor → Kreislauf) mit Protokolleintrag.
  - Echo und Rücksprung der Cloud: Die Integration zeigt einen Wert sofort, holt nach ~5 s die Cloud (oft noch der
    alte Wert) und später den neuen. `WriteGate` merkt den Wert bei einem Link **vor** dem Dienstaufruf vor
    (`_Pending.in_flight`, `before`), wertet Änderungen während des Aufrufs und Rücksprünge auf `before` nicht als
    „Am Gerät geändert“, hält einen bestätigten Wert noch `LINK_SETTLE_S` (20 s) und zeigt ihn als offen, solange die
    Cloud den alten zeigt. `settling(control, zone)` sagt dem Executor, dass er die Steuerung so lange nicht als
    Handänderung auswertet. `EndZoneOverride` hat das Ziel `TIME_PROGRAM` (bestätigt, sobald die Zone wieder dem
    Programm folgt).
  - `POST api/zones/{zone}/override/end` (`EndZoneOverride` über den Schreibschutz), Knopf `OverrideEnd.svelte`.
  - `Room.other_zone_id`: Zone der anderen Anbindung; `HomeAssistant.switch_rooms()` tauscht beim Umstellen und ordnet
    beim ersten Mal über den Zonennamen zu (`myvaillant_zone_labels()`); die API zeigt nur `zone_id`.
  - Soll 0 (Zone „Aus“) → kein Soll; Preset `eco` im Auto-Betrieb → `Zone.setback`, Soll = letzter Wert davor; der
    Planer überspringt Zonen in der Absenkung (`_devices`).
  - Oberfläche: Warnung bei ungespeicherten Einstellungen (`lib/unsaved.ts`), Zeilen ohne myVAILLANT-Werte
    ausgeblendet, „Zone nicht mehr vorhanden“.
  - Feldbefunde: Fehlerliste `vaillant-eebus-notizen/fehlerliste-abnahme-1.2.0.md`. Zone 1 schaltet beim Nutzer per
    Automation bei jedem WP-Stopp auf „Aus“ (echt, 15–25-mal am Tag); ob die Eco-Absenkung als `off` + Preset `eco`
    gemeldet wird (Kühl-Soll 0,0 → `active_operating_type` COOLING in der Integration), ist ungeklärt.

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

- Python: 305 Tests (`pytest`, 1 übersprungen ohne Zeitzonen-Datenbank unter Windows), gegen die Fake-Bridge (echter gRPC-Server, zwei Zonen wie die Anlage, Zone 2 ohne
  Raumfühler) und ein Fake-Home-Assistant (`tests/fake_home_assistant.py`, mit `add_myvaillant()`: Entitäten und
  Dienste der myVAILLANT-Integration wie im Quellcode der Integration). Die Fake-Bridge prüft Schreibbefehle wie die echte Bridge und zählt jeden in `write_calls`;
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
| image | Multi-Arch-Build (Rauchtest: Import und ein erzeugtes gRPC-Zertifikat); bei Tag `v*` Veröffentlichung auf ghcr.io (Tag muss zur Version in `config.yaml` passen), danach Prüfung ohne Anmeldung, dass amd64 und arm64 abrufbar sind |

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
10. Erledigt in 1.0.0: Der gRPC-Port der Bridge hat jetzt TLS und Token (Abschnitt 5c). Wer root im Container oder
    Zugriff auf `/data` der App hat (Supervisor, Sicherungen), kann das Token lesen – das ist gewollt.
11. Energiemanagement 1.3.0, bewusst so: nur Vorschau; Regeln statt Optimierung; Startwerte (Abtauraten,
    Zeitkonstante 30 h, Anlaufzeit, Verzögerung je Zonenart, Schwellen der Fenstererkennung) bis zum Lernen; siehe
    „Bekannte Vereinfachungen“ in Abschnitt 5d.
12. Energiemanagement, an der Anlage ungeprüft: Abtauerkennung gegen echte Abtauungen, Format der Langzeitstatistik
    der myVAILLANT-Sensoren (Einheit, `change`), Open-Meteo `/v1/dwd-icon` (die alten ICON-Gitter laufen laut
    Open-Meteo am 30.11.2026 aus; der Endpunkt bleibt nach deren Doku), Raumsensoren für die Fenstererkennung (Auflösung,
    Meldehäufigkeit), `weather.get_forecasts` per REST mit `return_response` nur nach Doku.
13. Unter Windows fehlt die Zeitzonen-Datenbank; zwei Tests (Zeitumstellung) laufen nur in der CI. Im Image prüft der
    Rauchtest `zoneinfo`.
14. Die Prüfung von v1.1.0 (15 Funde) ist bewertet und auf `fix/pruefung-1.1.0` eingearbeitet: 1, 3, 7, 8, 10, 11
    behoben, 5 verbessert (dabei gefunden: ein zweiter Abbruch beim Herunterfahren hat das Zurücksetzen bisher
    abgebrochen). Ohne Änderung, mit Begründung: 2 (ein Absturz endet mit Exit-Code 1, s6 startet neu), 4, 6, 9,
    13–15; 12 (Lücken > 3 min machen einen Tag im Energiezähler „lückenhaft“) kommt mit dem Modell in 1.3.0.
15. Energiemanagement, bewusst offen: Ist die Wärmepumpe beim Planen noch nicht verbunden (nach dem Start), fehlen
    die Zonen im Plan bis zur nächsten Viertelstunde; nach dem Verbinden mit Home Assistant plant es sofort neu.
16. Anbindung myVAILLANT, an der Anlage abgenommen (19.11.2026, Fehlerliste in den Notizen): Entitäten gefunden,
    Werte stimmen mit der myVAILLANT-App überein, Demo, Quick-Veto in Zone 1 und 2 (Dauer laut Optionen),
    Einmalladung, Warmwasser-Betriebsart; Bestätigung meist im selben Augenblick, der Dienstaufruf dauert ~5 s.
    Ungeprüft: Soll in „Ein“ (`heat_cool`) und Zonen-Betriebsart (der Nutzer hat beides ausgelassen),
    Warmwasser-Soll, Fehlercodes bei Cloud-Fehlern, Kontingent. Die Korrekturen von 1.2.1 sind nur gegen das
    nachgebaute Verhalten (Fake-HA mit `cloud_lag`) geprüft. Die Werte sind so alt wie das Abfrageintervall der
    Integration (Vorgabe 30 min).

## 10. Wo eine Prüfung am meisten bringt

Zu prüfen ist der Branch `feat/stufe-1-beobachten` (1.3.0) gegen `v1.2.1` (`9cc7fb1`). Der Schwerpunkt liegt auf
dem neuen Energiemanagement; es schreibt in 1.3.0 nicht, liest aber viel und läuft dauerhaft mit.

0. **Energiemanagement** (`backend/src/vaillant_eebus/hems/`, `room_data.py`, Ergänzungen in `ha_client.py`
   (`statistics()`), `ha_import.py` (Thermostat als Raumtemperatur), `home_assistant.py`, `api.py`, `settings.py`):
   - Kein Schreibweg: Gibt es irgendeinen Pfad aus `hems/` zur Wärmepumpe oder zu fremden Entitäten?
   - `service.py`: Fehler dürfen die App nicht beenden (Schleifen `_planning`, `_observing`, `_sampling`), Laufzeit
     und Speicher bei 400 Tagen Statistik und 10 Tagen Leistungsverlauf, SQLite in Threads, Neustart (gespeicherte
     Dateien), Verhalten ohne Home Assistant.
   - `cycles.py`: Lauf-/Pausen-/Abtauerkennung an Lücken, am Fensterrand (48 h) und beim Nachholen aus dem Verlauf;
     Doppelzählungen.
   - `rules.py`: Regeln und ihre Kombination (Band, Schritt), Nachtfenster über Mitternacht und Zeitumstellung
     (Stunden in UTC, Fenster lokal), Wiederaufheizen, Feuchteschutz, Abtau-Stunden.
   - `house_model.py`, `room_model.py`: Statistik richtig (Einheiten Wh/kWh, W/kW, Tagesgrenzen), robuste Fits,
     keine Division durch null, plausible Start- und Grenzwerte.
   - `forecast.py`: Open-Meteo-Antwort (Zeitstempel, Summen der Stunde davor), Rückfall, Eichung, Datei.
   - API: Prüfung von `PUT api/hems/settings` und `PUT api/rooms/{entity_id}`; Raumdaten bleiben bei `PUT
     api/settings` erhalten.
   - Oberfläche: `Plan.svelte`, `Rooms.svelte`, `RoomEditor.svelte`, `HemsSettings.svelte`, `HemsOverview.svelte`
     (Formularzustand, leere Zahlenfelder, 375 px, Screenreader).
1. **Schreibschutz** (`write_gate.py`): Kann im Demo-Betrieb irgendein Weg einen Schreibbefehl an die Bridge
   bringen? Sind Sperre je Steuerung, 5-s-Grenze, Duplikaterkennung und Bestätigung nebenläufig korrekt? Kann ein
   Befehl die falsche Zone treffen (App → `zone_id` → Fork `78a1a5a`)?
2. **Nebenläufigkeit im Backend**: `StateService` (Ereignis-Stream, Entprellung, Listener-Queues, SSE),
   `PairingService.run()` (Wiederverbindung, Backoff), Abbruch beim Herunterfahren, Verhalten bei Bridge-Neustart.
3. **Sicherheit**: Ingress-Prüfung (nur 172.30.32.2), gRPC nur auf Loopback mit TLS und Token (`bridge_auth.py`,
   s6-Oneshot, Erkennung einer fremden Bridge), Umgang mit fremder Bridge,
   Diagnose-Dump (enthält Seriennummern; SKI gekürzt), statische Dateien, Eingabeprüfung (`POST api/pairing/register`).
4. **Paketierung**: Dockerfile (Stufen, Pinning, Rauchtest), s6-Skripte (Quoting, atomares Schreiben der Seriennummer,
   Konfigurationsdatei), `config.yaml`.
5. **Oberfläche**: Wiederverbindung des Event-Streams, Darstellung „nicht verfügbar“, Barrierefreiheit,
   relative Pfade unter Ingress.
6. **Testlücken**: Was ist ungetestet und sollte abgesichert sein, bevor der Planer auf „Steuern“ geht?
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
