# Briefing: Vaillant EEBUS (Stand v1.5.0)

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
  Update). **1.4.0** bringt die **Raumregelung**: Die App stellt die Heizkörperthermostate (Punkt 1 von Stufe 1,
  vom Nutzer am 21.11.2026 vor die Zonen gezogen; die Zonen kommen mit 1.5.0). Einziger neuer Schreibweg ist
  `climate.set_temperature` auf den Thermostaten der Räume, über `RoomGate` in `write_gate.py` (Abschnitt 5f).
  1.4.1 korrigiert den Befund 1 der Abnahme von 1.4.0: Der Testlauf startete mit Fühlern, die nur bei einer
  Änderung melden, nie (Werte galten 15 min, jetzt 2 h); vertauschte Fühler werden erkannt, Wartegründe stehen im
  Protokoll. **1.4.2** (Befunde 2 und 3 der Abnahme 1.4.0): Das Thermostat lernt sein Regelverhalten (Kennfeld aus
  Testläufen und Alltag, `rooms/learning.py`), „Steuern“ ist frei, sobald „zu“ und „ganz offen“ bekannt sind, der
  Testlauf wählt seine Schritte nach dem Ventil (0,5–3 K), und die App fragt die Ventilstellung der Aqara W600 selbst
  ab (zweiter Weg über `RoomGate`: nur `mqtt.publish` mit `{"running_state": ""}` auf `<Basis>/<Gerät>/get`).
  **1.4.3** (Plan `plan-1.4.3.md`): Fenstererkennung über die Feuchte gegen die letzte Meldung, Thermostat-Fühler und
  optionalen Fensterkontakt, sofortige Reaktion (Teil B); **schneller Durchlauf** (alle 2 min ein Schritt, ohne
  Fühlerpaar, eigener Grund `SWEEP` mit eigenem Budget, reicht für „Steuern“, Teil 1 des Nacht-Testlaufs, der danach
  nur noch 4 Stellungen hält; Teil A); **Regelung lernt mit**: Regelgüte je Raum und Tag, Bedarf je
  Außentemperatur-Stufe, Verstärkung je Raum (Teil C, R1–R4); dazu Befund 4 der Abnahme 1.4.0 (sauberer Rückweg nach
  Testlauf/Durchlauf, Übernahme durch die Regelung in „Steuern“, Rückfall aufs Raum-Soll, Alter der Comet-Stellung
  und Spreizung als Plausibilität).
  **1.5.0** (Plan `plan-1.5.0-zonen.md`, Branch `feat/zonen`): **Zonen steuern** – das Energiemanagement schreibt über
  den Executor (`hems/executor.py`, `Source.PLANNER`) an die Zonen, nur mit myVAILLANT, Funktion für Funktion mit
  eigener Stufe: F0 Grundlage (Quick-Veto, Handänderung, Cloud-Budget, Rückfall, Lebenszeichen), F1 Zone 1 Aus/Auto,
  F2 Vorlauf der Heizkörper-Zone nach dem Engpassraum (`hems/flow.py`), F3 Nachtabsenkung 5:30–10:00 (Aus über 3 °C,
  sonst Minimalleistung; `hems/night.py`), W6 eigene Übersteuerung verlängern; dazu Korrekturen der Prognosewerte K1
  (Arbeitszahl), K2 (Abtaurate), K4 (Verzögerung), K5 (Zeitkonstante).
- **Die App schreibt jetzt** – ausschließlich über `write_gate.py` (Abschnitt 5a; Thermostate Abschnitt 5f). Nach der
  Installation läuft sie im Demo-Betrieb und sendet nichts; erst nach ausdrücklicher Bestätigung im Live-Betrieb.
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
| Stufe 1 | Energiemanagement neu: 1.3.0 beobachten, lernen, planen (Vorschau); 1.4.0 Thermostate (Raumregelung, vorgezogen); 1.5.0 Zonen | 1.3.0/1.3.1 veröffentlicht, Abnahme an der Anlage läuft; 1.4.0 veröffentlicht (21.11.2026), Abnahme läuft (`fehlerliste-abnahme-1.4.0.md`), 1.4.1 mit deren Befund 1, 1.4.2 mit Befund 2 und 3 (lernendes Thermostat, W600-Abfrage), 1.4.3 Fenster, schneller Durchlauf, Regelung lernt mit; Prüfung (Aufwand max) offen, Steuern zuerst nur im Bad; 1.5.0 Zonen steuern (F0–F3, W6, K1–K5) lokal auf `feat/zonen`, Prüfung (max) vor dem Steuern, Abnahme nach 1.4.x |

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
| `write_gate.py` | **Einzige Stelle, die schreibt** (Abschnitt 5a); `RoomGate` für die Heizkörperthermostate und die Abfrage der W600-Ventilstellung (Abschnitt 5f). |
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
| `rooms/` | Raumregelung (1.4.0), siehe Abschnitt 5f |
| `room_data.py` | Raumdaten je Raum (`RoomDetails`: statischer Abgleich, Thermostat, Soll, Zeitplan, Komfortband, Verluste, Stufe der Raumregelung, Korrektur-Grenze, fester Ausgleich, Ventilschonung); Schalter `RoomControlSettings` (Stufe, Fühlerpaar, Nachtfenster des Testlaufs), `effective_stage()` |
| `ids.py` | Formate von Entitäts- und Zonen-IDs (`ENTITY_ID`, `ZONE_ID`); `ieee_address()` (Zigbee2MQTT-Gerät einer Entität); `MQTT_BASE_TOPIC`, `device_name_problem()` (kein Teil „get“/„set“, kein `+`/`#`, nicht „bridge…“, damit eine Abfrage nie als Schreibbefehl gelesen wird) |
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
- **Thermostate (1.4.0):** eigener Teil `RoomGate` in derselben Datei, Abschnitt 5f.

## 5b. Home Assistant (Phase 6)

- Die App liest nur und meldet eigene Sensoren; keine fremden Entitäten (Test
  `test_only_the_apps_own_sensors_can_be_written`). Dienste: `weather.get_forecasts` (liest nur), mit myVAILLANT
  `DEVICE_SERVICES` (über `write_gate`) und seit 1.4.0 `ROOM_SERVICES` = `climate.set_temperature` auf den
  Thermostaten der Räume (nur über `RoomGate`; Test `test_the_app_calls_only_the_weather_forecast_and_the_device_services`
  und die Strukturtests in `tests/test_room_gate.py`).
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
Versorgung). Drei Releases: 1.3.0 ohne neuen Schreibweg, 1.4.0 Thermostate (Raumregelung, Abschnitt 5f; vom
Nutzer am 21.11.2026 vorgezogen, Plan `plan-thermostate-vorgezogen.md`), 1.5.0 Zonen (Quick-Veto, Zone-1-Takt-Logik,
Nachtabsenkung). Der Planer aus 1.1/1.2 (Warmwasser/Zonen nach Preis und PV, `planner.py`,
`executor.py`, `supply.py`, `tariff.py`, `inputs.py`, `days.py`, `weather.py`) ist entfernt.

| Modul (`hems/`) | Aufgabe |
|---|---|
| `config.py` | `HemsSettings`: Stufe (`off`/`preview`/`control`; `control` aus Einstellungen vor 1.5.0 – ohne `zone1_mode` – wird beim Laden zu `preview`), Stufen je Funktion (`Function`: `zone1` F1, `flow` F2, `night` F3, alle bis Steuern; `FUNCTION_MODES`, wirksam ist `effective()` = die kleinere), Wetter, Lernquellen, Haus (Normaußentemperatur, Heizlast), Zonen (`ZoneConfig`: Art Fußbodenheizung/Heizkörper, Vorlauf-Entitäten, `heating_curve_entity` (nur lesen, 1.5.0), Auslegung, Band), Quick-Veto-Grenzen und Cloud-Budget, Nachtfenster (Vorgabe 5:30–10:00; das alte 0–10 Uhr ohne `night_mode` wird einmal umgestellt), Verluste, Ziel Starts, Heizgrenze, Blockbetrieb, Zone-1-Schwelle; ungültige Werte einzeln verworfen |
| `sources.py` | `HemsSources` (HA-Listener): Raum-, Thermostat-, Ventil-, Feuchte-, Vorlauf-, Puffer- und Wärmesensor-Zustände; meldet Änderungen (Fenster) und die Verbindung (neu planen) |
| `forecast.py` | Open-Meteo `/v1/dwd-icon` (stündlich, `past_days=7`, Koordinaten auf 2 Stellen), höchstens stündlich, nach Fehler 10 min, alte Prognose bis 6 h; Rückfall `weather.get_forecasts`; Eichung (Median gemessen − Prognose je lokaler Stunde, ab 3 Tagen, ±5 K); Day-ahead-Werte in `/data/forecast_check.json` → mittlerer Fehler |
| `statistics.py` | `StatisticsHistory`: HA-Langzeitstatistik (`ha_client.statistics()`, WebSocket `recorder/statistics_during_period`, nur lesen) für 400 Tage in Monatsstücken, danach nur Neues; `daily_values()` (lokale Tage; seit 1.5.0/K1 `cop_heat_kwh`/`cop_electricity_kwh`/`cop_hours`: Wärme und Strom über dieselben Stunden, ab 20 Stunden) |
| `house_model.py` | `fit_house()` (Wärme/Tag = H·ΔT·24 − Gewinne, mit Sonne gemeinsam gefittet, ab 10 Heiztagen), `fit_cop()` (Arbeitszahl je Außentemperatur aus Wärme und Strom derselben Stunden; `CopModel` mit `low_c`/`high_c`, außerhalb ±2 K flach; K1), `find_lag()` (Treffer am Rand `MAX_LAG_H` = nicht gelernt; K4), `demand()` je Stunde und Zone (Anteil nach Heizlast der Räume), `start_model()` aus der Heizlast |
| `samples.py` | SQLite `/data/hems.db`: 5-Minuten-Proben (Räume, Außen, Leistung, Vorlauf, Zonen-Soll, Puffer), 21 Tage |
| `room_model.py` | Raummodell (punkt1 2/9a): Zeitkonstante (Startwert 150 h seit 1.5.0/K5, vorher 30 h) und Fremdwärme aus Abkühlphasen (Ventil zu, ohne Ventil WP aus, seit 1.5.0 auch Zone ausgeschaltet: Probe `zone_off:<zone>`), Aufheizgeschwindigkeit, Anlaufzeit, Bedarfsöffnung, Korrektur, Überschwingen; „lernt noch“ unter 5 Phasen; Abkühlung jetzt/Tabelle, Zeit bis zur unteren Komfortgrenze (Simulation mit Prognose); Versorgung |
| `cycles.py` | Läufe, Pausen und Abtau-Muster aus der Leistung (Schwelle 100 W; Abtauen: Lauf ≥ 500 W → 2–15 min 200–600 W → ≥ 900 W in 4 min), Lücken als NaN; seit 1.5.0 (K2) je Lauf `pieces` (je Stunde Außentemperatur, Feuchte, Minuten; `split_run()`), die Bänder zählen Laufstunden danach; `CycleTracker` hält 48 h Punkte, Ereignisse 90 Tage in `/data/cycles.json` (Läufe mit `pieces`), holt nach dem Start 10 Tage aus dem HA-Verlauf nach |
| `defrost.py` | Abtauraten je Außenband (Startwerte aus den Felddaten 09.–19.11.2026, 10 Laufstunden Gewicht), seit 1.5.0 (K2) Laufstunden je Stunde nach deren Außentemperatur und mit dem Feuchtefaktor ihrer Stunde gewichtet (`weighted_hours`; Rate je Stunde normaler Feuchte), Feuchtefaktor der Prognose, Risiko je Stunde |
| `frost.py` | Frostschutz: < 4 °C länger als 4 h, auch für die Prognose-Stunden |
| `windows.py` | Fenster (1.4.3, Teil B): Feuchte gegen die letzte Meldung (≤ 90 min, Schwelle 0,2 × (innen − außen) g/m³, 0,5–1,0, unter 1,5 g/m³ Unterschied zählt die Feuchte nicht), Temperatur (−0,5 K/15 min, −0,8 K/30 min bei Raum − außen ≥ 8 K), Thermostat-Fühler (−1 K gegen das Maximum der letzten 8 min, Raumsensor nicht gestiegen; ohne Raumsensor 1,5 K; Sperre 30 min nach Befehl/Ventilschluss), Schließen (+0,3 g/m³, +0,2 K, Thermostat +0,5 K, frühestens 3 min, spätestens 30 min); Fensterkontakt je Raum (`window_entity`) hat Vorrang, > 10 min stumm → Regeln; Ereignisse rufen die Raumregelung sofort (`on_window`) |
| `rules.py` | `make_plan()` (reine Funktion): 48 h je Zone und Stunde, Verschiebung im Band und Schritt, je Stunde mit ihren Teilen (`ZoneHour.parts`, `Part`: flow, ahead, warm, defrost, store, night; `shift_of()` summiert nur gewählte Teile), `ZoneHour.off` (F3), Soll je Raum (= Zeitplan, nachts nicht mehr abgesenkt), Gründe: warmer Tag, vorausschauend (Verzögerung je Zonenart), Abtaurisiko (höchstens 3 h, vorher speichern in der Fußbodenheizung), Nacht (F3: im laufenden Fenster der Live-Zustand aus `ZoneInput.night_*`, in späteren Fenstern geplant: Aus bzw. Zone 2 −Start, Zone 1 −1 K bei Frost; die Stunde vor Fensterbeginn bleibt leer), Blockbetrieb, Vorlauf-Verschiebung, Frostschutz |
| `flow.py` | F2 (1.5.0, rein): `normal_moments()` (ohne Fenster + 30 min, Sollwechsel 30 min, Aufheizen nach Soll-Erhöhung bis 0,2 K/3 h, Testlauf/Durchlauf; Proben `window`/`testing` je Raum), `bottleneck()` (größte mittlere Öffnung über 7 Tage nur mit WP-Wärme, ab 24 h, sonst `snapshot()`), `hour_valve()`, `decide()` (höher: unter 0 sofort auf 0, sonst +0,5 K je Abstand bis Band, nur mit WP-Wärme und Berater; tiefer: Engpass 10–70 %, kein Ventil ≥ 90 %, 2 h, 6 h nach Sprung, Zone 2 ≥ Zone 1 + 1 K, Fußbodenraum nicht kalt), `curve_advice()` (7 Tage, ≥ 5 Tage mit ≥ 6 h, ab ±0,5 K; steiler/flacher, wenn kalte und milde Hälfte ≥ 0,5 K auseinander und ≥ 4 K Spanne, sonst parallel; neu nach Kurvenänderung) |
| `night.py` | F3 (1.5.0, rein): `window_at()`, `plan_window()` (Mittel der Prognose > 3 °C und kein Frostschutz → Aus, sonst Minimalleistung), `reduce_step()` (alle 20 min −0,5 K bis Band/Zone 1; `at_minimum` → +0,5 K und halten; WP steht → +1 K), `next_start()`, `off_start()` (ohne Vereisung sofort, sonst ≤ 15 min nach Abtauende, bis 7:00, dann aufgeben), `off_end()` (Verlust − 0,1 K, Feuchte 65 %, Frostschutz), `at_minimum()`/`minimum_phases()`/`minimum_by_band()` (Puffer ≥ Soll + 0,5 K und nicht fallend, Soll 20 min ±0,3 K, WP ≥ 10 min an, keine Abtauung ±15 min, kein Warmwasser), `hot_water()` |
| `executor.py` | `ZoneExecutor` (1.5.0, `plan-1.5.0-zonen.md` F0–F3): einziger Weg des Pakets zur Schreibsperre (`Source.PLANNER`); jeder Durchlauf (nach Plan und jedem Zustand, sonst jede Minute) erkennt fremde Änderungen (Quick-Veto erscheint/ändert sich/endet vor seiner Zeit, Betriebsart nicht von ihm; UI-Befehle über `gate.on_sent`) → Ruhe bis zur nächsten geplanten Änderung, höchstens 3 h; Quick-Veto aus den Teilen der Funktionen auf Steuern (`PART_FUNCTION`: flow/ahead/warm → F2, night → F3; defrost/store noch nicht) = Programmwert (gemerkt, nicht während des Einschwingens) + Verschiebung, Dauer bis zur nächsten Änderung 1–4 h (ganze Stunden), Erneuern 10 min vor Ende, Abstand `min_interval_min` nur abwärts (höher sofort), Ende sofort bei 0; F3 `_night_mode`: `ZoneHour.off` der Stunde → `SetZoneMode off` (`night_off`), danach auto, F1 lässt solche Zonen in Ruhe; Vorschau-Spur simuliert und protokolliert „würde“ (`submit(preview=True)`), zählt `count_would`; F1 Zone 1 Aus/Auto nach `HemsService.zone1_would()` (Aus 5 min nach Auto, nicht bei Frost/Tagesgrenze), Automation erkannt (2 fremde Wechsel im Takt der WP) → hält an bis `resume_zone1`; Rückfall bei Stufenwechsel, `release()` vor Demo (api `to_demo`) und beim Stopp (15 s); Lebenszeichen `sensor.vaillant_eebus_zones_lifesign` (immer, Attribut `ausgeschaltet`), Vorlage `automation()`; Stand in `/data/zone_control.json` |
| `service.py` | `HemsService`: beobachtet jeden Zustand (Takte, Abtauen, Frostschutz, Zone-1-Logik „würde“; neue Abtauung → `_night_after_defrost`), Proben alle 5 min (Fenster, Versorgung; seit 1.5.0 auch `window`/`testing` je Raum, `flow_offset`/`curve`/`zone_off` je Zone, `dhw`), danach F2 `_update_flow` (Engpass stündlich aus 7 Tagen, `decide()`, Heizkurven-Empfehlung täglich, `_flow_hold` während der Nacht und 3 h Aufwärmen) und F3 `_update_night` (`_NightZone` je Zone: Fenster, Aus mit Abtau-Warten und Rückkehr, Minimalleistung über `reduce_step` mit Live-Proben, Zone 1 −1 K bei Frost, Testlauf-/Feuchte-Sperre über `set_night_guard`, Lernen des Starts; jede Änderung `wake()`), lernt (Statistik alle 6 h, Räume und Minimalleistung stündlich), plant alle 15 min und nach dem Verbinden mit HA; Status (`flow`, `night`, `minimum`), Plan mit Rückblick (`/data/plan_history.json`), Räume, CSV, Sensoren, Protokoll (Kategorien `hems`, `rooms`); Stand in `/data/hems_state.json` (`flows`, `nights`) |

- **Schreibweg nur über den Executor (1.5.0):** Außer `executor.py` importiert kein Modul des Pakets `write_gate`;
  keines den Bridge-Client oder `rooms`; `submit` ruft nur der Executor, immer mit `Source.PLANNER` (Strukturtests).
  Die Schreibsperre lässt vom Planer nur `SetZoneOverride`, `EndZoneOverride` und `SetZoneMode` off/auto zu, nur
  mit myVAILLANT, und zählt jeden myVAILLANT-Befehl im Cloud-Budget (`cloud_budget.py`, `/data/cloud_budget.json`,
  Tag lokal; Warnung 80 %, an der Grenze nur `returns_control()`). `HemsService.on_update` weckt den Executor. Seit 1.4.0 ruft `sample()` nach
  den Proben und der Versorgung die Raumregelung auf (`on_sampled`), und ein „Berater“ der Raumregelung
  (`set_flow_advisor`) darf den Vorlauf-Hinweis einer Heizkörper-Zone auf „halten“ setzen, solange umverteilt wird;
  die Proben enthalten zusätzlich die Temperatur am Thermostat. „Würde“-Fenster und -Versorgung protokolliert das
  Paket nur noch für Räume, die die Raumregelung nicht selbst führt (Stufe ≤ Beobachten). Gelesen wird über REST, WebSocket (nur die
  Befehle `auth`, `subscribe_*`, `unsubscribe_events`, `recorder/statistics_during_period`; Test) und Open-Meteo.
- **Raumdaten** (`room_data.py`, je Raum in `settings.json` unter `details`): Heizlast, Auslegungs-Soll, Heizflächen,
  Thermostat, Ventil, Feuchte, Raum-Soll, Zeitplan, Komfortband, Verluste, Drosseln, Notfallwert. `PUT api/settings`
  behält sie; `PUT api/rooms/{entity_id}` ändert sie. Ein Raum kann ein Thermostat (`climate.*`) als Temperatur
  haben (`current_temperature`); er zählt nicht im Zonen-Mittelwert.
- **API:** `GET api/hems` (mit `functions` und `control`, dem Stand des Executors), `PUT api/hems/settings`,
  `POST api/hems/mode`, `POST api/hems/function` (`zone1`, `flow`, `night`), `GET api/hems/automation`,
  `POST api/zones/{id}/override` (W6: eigene Übersteuerung 1–12 h), `GET api/hems/plan`,
  `GET api/hems/plan/export`, `GET api/rooms`, `PUT api/rooms/{entity_id}`; `api/ha/entities` mit `room`,
  `thermostat`, `humidity`, `percent`.
- **Oberfläche:** Seiten `Plan.svelte`, `Rooms.svelte` (mit `RoomEditor.svelte`), Karten `HemsOverview.svelte`
  (Nächste Änderungen, Takten und Abtauen), `HemsSettings.svelte` neu; 1.5.0: `ZoneControlCard.svelte` (Stufen je
  Funktion, Tagesgrenze, Zonen, letzte Befehle, Vorlage der Automation), `OverrideEnd.svelte` mit „Verlängern“.
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

## 5f. Raumregelung (1.4.0: Heizkörperthermostate)

Plan und Entscheidungen: `vaillant-eebus-notizen/plan-thermostate-vorgezogen.md` (freigegeben 21.11.2026 als 1.4.0,
mit Besprechung der Funktionen und Testläufe), fachlich maßgeblich `punkt1-trv-regelung.md`. „Alle über Sollwert“:
Jedes Thermostat (Zigbee2MQTT; beim Nutzer 4× Aqara W600 immer `internal`, 1× Eurotronic Comet in `trv_mode` 2)
regelt sein Ventil selbst, die App gibt den Sollwert vor.

**Schreibweg (`write_gate.py`, `RoomGate`; `ha_client.py`):**
- `ROOM_SERVICES` = nur `("climate", "set_temperature")`, `call_room_service()` nimmt nur das Feld `temperature`
  (endliche Zahl); nur `write_gate.py` ruft ihn auf, nur `myvaillant.py` `call_device_service()` (Strukturtests).
- Befehl `SetThermostatSetpoint(room_id, celsius, reason, trv=None)`; das Thermostat ist das `trv_entity` des Raums
  in den Einstellungen (Einstellungen werden innerhalb der Sperre je Thermostat gelesen). Nur der Rückfall eines
  Thermostats, dessen Raum fehlt, nennt `trv`; dann gilt der Notfallwert, den das Gate je Thermostat mit `room_id` und
  Name gespeichert hat. Nie eine myVAILLANT-Zone (gewählt oder mit deren Attributen `quick_veto_end_date_time`/
  `time_program_heating`, fail closed), nie ein Thermostat zweier Räume.
- Erlaubt: `reason` normal (Regelung, Vorheizen, Umverteilen, Fenster, Handänderung, Sicherheit) nur bei wirksamer
  Stufe „Steuern“ (Schalter und Raum); `TEST` bei Raumregelung ≠ Aus; `RESTORE` (Rückweg nach dem Testlauf) und
  `FALLBACK` nur für Thermostate, die die App gestellt hat (`touched`): `FALLBACK` auf Notfallwert bzw. Raum-Soll,
  `RESTORE` zusätzlich auf `restore_c` (gemeldeter Sollwert vor dem ersten `TEST`-Befehl, gespeichert). Nach Rückfall
  oder Rückweg gibt das Gate das Thermostat frei (`touched` = False), nach dem Rückweg nicht, wenn der Raum wirksam auf
  „Steuern“ steht. `touched` erst nach Erfolg oder „Ergebnis unbekannt“ (oder Abbruch mitten im Aufruf).
- Wert: in `min_temp`/`max_temp`, auf `target_temp_step` (ohne Schritt 0,5 K) der Entität; außer Fenster
  (kleinster Wert), Rückfall und Testlauf höchstens `correction_limit_k` (Start 5 K) um die Raum-Sollwerte.
- **Ventilschonung erzwungen:** gleicher Wert → nicht gesendet; als aktueller Wert gilt ein noch unbestätigter
  (`pending`) vor dem gemeldeten. Totzone (0,5 K), Abstand (15 min) und Höchstzahl je lokalem Tag (24) je
  Thermostat, einstellbar je Raum. Frei von Totzone und Abstand: Fenster, Handänderung, Sicherheit, Rückweg,
  Rückfall, Testlauf; über der Höchstzahl und trotz Pause nur Fenster, Sicherheit, Rückweg, Rückfall. Technisch
  höchstens ein Befehl je 5 s (nicht für Rückweg und Rückfall). Testlauf: eigenes Budget 16 je Nacht (Mittag bis
  Mittag); der Rückweg zählt nicht. Durchlauf (1.4.3, `RoomReason.SWEEP`): wie `TEST` frei von Totzone und Abstand,
  Wert nur in den Grenzen des Thermostats, nur bei Raumregelung ≠ Aus; eigenes Budget `SWEEP_COMMANDS_DAY` = 40 je
  Thermostat und lokalem Tag (der Durchlauf selbst hört bei 20 auf), setzt wie `TEST` beim ersten Befehl `restore_c`.
  Befund 4 (1.4.3): `restore_value(trv)` gibt einen nicht quittierten `restore_c` heraus (neuer Lauf nimmt ihn als
  `before_c`); `take_over(trv)` lässt den nächsten Befehl `CONTROL`/`PREHEAT`/`REDISTRIBUTE`/`MANUAL` ohne Totzone und
  Abstand hinaus und löscht danach `restore_c` (`_taken_over`); `FALLBACK`/`RESTORE` dürfen auf jedes Raum-Soll des
  Zeitplans (bisher nur kleinstes/größtes). Ein gehaltener Befehl ist kein Fehler (`RoomWriteResult.held`: `same`,
  `deadband`, `interval`, `limit`, `paused`).
- **Abfrage der Ventilstellung (1.4.2):** `ReadValvePosition(room_id, device)` → `RoomGate.read_position()` →
  `ha_client.publish_position_read()` → `mqtt.publish` mit `topic` = `position_read_topic(base, device)`
  (`<Basis>/<Gerät>/get`), `payload` = `POSITION_READ_PAYLOAD` (`{"running_state": ""}`), `qos` 0, `retain` false.
  Erlaubt bei Raumregelung ≠ Aus, auch im Demo-Betrieb und in jeder Stufe (liest nur); nur für das Thermostat eines
  Raums, nie eine myVAILLANT-Zone; höchstens 1 je 30 s und 720 je lokalem Tag je Thermostat (`READ_SPACING`,
  `READS_PER_DAY`), eigene Zählung in `room_gate.json` (`reads`), nicht bei den Befehlen, `touched` bleibt unberührt.
  Kein Protokolleintrag je Abfrage; ein Fehler einmal als Problem, die Rückkehr einmal. `submit()` weist
  `RoomReason.READ` ab. Strukturtests: nur `write_gate.py` ruft `publish_position_read`, nur `ha_client.py` kennt
  `_post_service`, „mqtt“ kommt im Client genau einmal vor.
- Demo und Vorschau senden nie; ihre Zählung ist simuliert (eigener Zustand), damit die Vorschau zeigt, was
  „Steuern“ senden würde. Live: „Wird gesendet“ vor dem Aufruf in der Datenbank; abgewiesen → „Nicht ausgeführt“
  (zählt nicht), sonst „Ergebnis unbekannt“; bestätigt, wenn das Attribut `temperature` den Wert zeigt, sonst nach
  5 min „Nicht bestätigt“; zweimal → 1 h nur Fenster/Sicherheit/Rückweg/Rückfall. Zähler, letzter Befehl,
  erwarteter Wert, `touched`, `restore_c`, Raum, Name und Notfallwert in `/data/room_gate.json` (überstehen
  Neustarts); die letzten Grenzen jedes Thermostats im Speicher für den Rückfall beim Stoppen.

**Raumregelung (`rooms/`):**

| Modul | Aufgabe |
|---|---|
| `thermostats.py` | Thermostat aus HA lesen (Soll, Temperatur am Thermostat, Betrieb, Preset), Neben-Entitäten über die IEEE-Adresse (`sensor`, `window_detection`, `child_lock`, `valve_alarm`, `battery`, `trv_mode`, `last_seen`, …), Modell Aqara/Comet/anderes, Warnungen; Lebenszeichen = `last_seen` (live verfolgt), sonst die letzte Änderung von Thermostat oder Ventil; Warnung erst nach 12 h ohne Lebenszeichen (1.4.2; vorher „Ventilstellung seit 12 h nicht gemeldet“); `blocking()` (nicht erreichbar, kein Bereich, Fühler nicht intern, `trv_mode` ≠ 2) hält das Steuern an |
| `supply.py` | Wärmeangebot je Heizkörper-Zone: Wärmepumpe (> 50 W und Vorlauf bzw. Puffer ≥ Raum + 8 K), Puffer (≥ Raum + 5 K und fällt ≥ 2 K/h, Restzeit bis zur gelernten Neustart-Temperatur, Start 28 °C), keins, unklar (Daten > 15 min, oder WP läuft ohne Vorlauf und Puffer) |
| `quality.py` | Regelgüte (1.4.3, R1, rein): `QualityWatch` je Raum, alle 5 min ein `QualitySample` ab „Vorschau“; je lokalem Tag Abweichung (Soll − Raum, Vorzeichen und Betrag), Zeit unter/über Komfortband, Überschwinger (von unter Soll auf > Soll + 0,3 K, Maximum), Hin-und-her (±0,3 K), Aufheizen nach Soll-Erhöhung (bis 0,2 K dran, ≤ 3 h, zählt nicht als Abweichung), Befehle (live + Vorschau), Ventilbewegungen, Ventil-Mittel und Zeit ≥ 90 %, längste „träge“ Strecke (≥ 2 h ≥ 0,3 K daneben, sinkt < 0,1 K/h, Ventil nicht am Anschlag), Minuten in „Steuern“; ausgeschlossen: Fenster + 30 min, Testlauf/Durchlauf, Thermostat nicht erreichbar, Raumsensor fehlt; Bad nur Befehle/Ventil; 30 Tage, `summary()` für die tägliche Protokollzeile |
| `regulation.py` | Bedarf und Verstärkung (1.4.3, R2/R3, rein): `DemandCurve` mit 6 Stufen (−10…15 °C), `weights()`/`value()` linear, Start `demand_from_history()` aus `calm_hours()` (ruhige ganze Stunden, Halbwertszeit 14 Tage, ab 5 h je Stufe; leere Stufen auf der Ausgleichsgeraden, sonst Raummodell, sonst 30 %), `follow()` ±2 % nach Gewicht, `toward()` 20 % zum 7-Tage-Median, 0–90 %; `DemandFollow` (30 min durchgehend ≥ 0,2 K daneben); `Gain` mit `start_gain()` (1 K in 2 h, 20–40 %/K, sonst 30), `daily_gain()` (×0,8 nervös / ×1,2 träge nach ≥ 12 h „Steuern“, 10–60 %/K), Trend = Verstärkung × ⅓ h |
| `sweep.py` | Schneller Durchlauf (1.4.3, Teil A, reine Schrittfunktion): Start auf die Temperatur am Thermostat, alle 2 min ±0,5 K (1 K nach < 10 Punkten Bewegung), hoch bis ganz offen (≥ 85 % oder Plateau) / +6 K / Grenze, runter bis zu (≤ 5 %) / 2 K unter Thermostat; frische Ventilstellung nach dem Schritt nötig (sonst bis 4 min, dann Abbruch); Abbruch Fenster, Raum > Soll + 1 K, nicht stellbar, Handänderung (Soll ≠ eigener, nichts unterwegs, ≥ 1 min); ≤ 20 Befehle; Punkte Quelle `SWEEP` und Sprungantworten; `fresh()` (vollständig, < 7 Tage) |
| `controller.py` | `decide()` (rein): Fenster → kleinster Wert; Raum > Soll + Band + 0,5 K → Soll − 0,5 K; Spender → untere Komfortgrenze (+ Korrektur); sonst Raum-Soll (Vorheizen nach gelernter Aufheizzeit, höchstens 3 h, sonst 1 h) + Korrektur (0,5 K nach 0,2 K über 30 min, Ventil-Regeln ≥ 90 %/≤ 5 %, nicht ohne Wärme, Pause nach Fenster und Sollwechsel) bzw. fester Ausgleich (Raum am Thermostat gemessen); mit bereitem Kennfeld (1.4.2) „Kaskade“ (Bedarf + Verstärkung × Abweichung − Verstärkung × ⅓ h × Trend, seit 1.4.3 mit `demand_valve` aus der Bedarfskurve und `gain_per_k` je Raum, Start 30 %/K → nächste erreichte Stufe → kleinste Abweichung, die sie im jetzigen Zustand erreicht hat: Richtung steigend/fallend, Wärmeangebot, sonst Nachbarklasse; Hysterese Totzone + 0,25 K gegen den zuletzt gesetzten Wert; PI: nach eigener Änderung mindestens 30 min bzw. Einschwingzeit warten, nicht nachlegen, solange das Ventil ≥ 5 %/h in die richtige Richtung läuft); weicht der Soll vom zuletzt gesetzten ab → Handänderung (`MANUAL`); beim Übergang in „zu warm“ einmal Korrektur − 0,5 K (`Correction.too_warm`) |
| `learning.py` | Kennfeld (1.4.2, rein): `ValveMap` mit Punkten (Abweichung im 0,5-K-Raster, steigend/fallend, Wärmeangebot `Supply` < 30 / 30–35 / > 35 °C / unbekannt, Ventil, Quelle Testlauf/Alltag/Durchlauf – Durchlauf-Punkte zählen in einer Zelle nur ohne andere (`_settled_first`); je Zelle die 10 neuesten, gewichtet mit Halbwertszeit 14 Tage), Sprungantworten (`Response`), Drifts (`Drift`); `closed_k()` (≤ 5 %), `full()` (≥ 85 % oder steigendes Plateau ab 50 %: +1 K < 5 Punkte), `missing()`/`ready()` (Freigabe „Steuern“), `offset_for()`, `levels()`, `kind()` (auf/zu ≥ 90 % der Punkte an den Anschlägen; PI Drift ≥ 10 %/h in Richtung der Abweichung; sonst P), `smallest_step_k()`; `CalmWatch` (Punkt nach 30 min ruhigem Soll, ruhiger Abweichung und warmem Wasser mit aktueller Ventilstellung, Median 10 min; Drift je ruhige Stunde), `StepWatch` (Reaktion ≥ 5 Punkte, Einschwingen 10 min ruhig, höchstens 60 min); `points_from_steps()` (Testlauf-Stufen, auch die gespeicherten von 1.4.1) |
| `reads.py` | Wann ein W600 abgefragt wird (1.4.2, rein): `ReadPlan.due()` regulär alle 5 min, höchstens 2,5 min vor dem Takt (nach ≥ 4 min), ohne Takt alle 5 min; `after_change()` +1 und +3 min nach einem neuen Sollwert; im Testlauf 10 min lang jede Minute nach dem Schritt, dann alle 2 min; im Durchlauf (1.4.3) jede Minute ab 1 min nach dem Schritt (`sweep_step_at`) |
| `curve.py` | Die Testläufe (Verlauf für die Seite): Stufen (Abstand, Öffnung, auf/ab, VL/RL, Wärme), `TestResult` (vollständig = ganz offen und wieder zu), höchstens 5 Läufe je Thermostat; das Gelernte steht seit 1.4.2 im Kennfeld |
| `testrun.py` | Seit 1.4.3: beim Start ohne frischen Durchlauf (`sweep_needed`) Phase `SWEEP` → Aktion „sweep“, die Raumregelung führt einen `Sweep(part_of_test=True)`, danach `after_sweep()`: mit bereitem Kennfeld Phase `HOLD` mit `hold_positions()` (zu − 0,5 K, ~30 %, ~60 %, ganz offen, steigend), je 20–40 min wie bisher, sonst der bisherige Ablauf. Testlauf als reine Schrittfunktion `advance()` (jede Minute): vorgemerkt → Start 22–1 Uhr bei Raum −0,5…+0,2 K um Soll, kein Fenster, warmes Wasser am Heizkörper (Fühlerpaar VL ≥ Raum + 5 K), Fühlerpaar am Raum, Werte höchstens 2 h nach der Meldung (`PROBE_VALID`, `probe_reading`; 1.4.1) und nicht vertauscht (`SwapWatch`); Stufen 20–40 min mit Wärme bis Ventil und Rücklauf 10 min ruhig; Schritte nach dem Ventil (1.4.2, `_step`): erster je Richtung 1 K, Wirkung < 10 %/K → 2 K, dann 3 K, Bewegung in Schrittrichtung > 40 Punkte → 0,5 K, sonst Richtung nächste Marke (25/50/75/85 bzw. 50/25/5 %), Zeitplanung bis 5 Uhr (`steps_left`, Stufendauer = Median, Start 30 min) mit späteren Marken; hoch bis ganz offen (≥ 85 % oder Plateau) oder Grenze, runter bis zu (≤ 5 %) oder 2 K unter der Temperatur am Thermostat; zurück; Ende spätestens 5 Uhr; endet die Nacht oder das Budget nach „ganz offen“ und vor „zu“, bleibt der Lauf vorgemerkt mit `down_only` und `full_offset_k` (nächste Nacht Start dort, nur abwärts; `outcome` hält das Ergebnis der Nacht); Abbruch Raum > Soll + 1 K, Fenster, 30 min keine Ventilstellung oder kein warmes Wasser; nach 3 Nächten ohne Start „nicht möglich“ |
| `service.py` | `RoomControl`: Takt nach den Proben (`tick`), jede Minute (`minute`: Lebenszeichen, Neben-Entitäten alle 15 min, Ventilbewegungen, Testlauf); Umverteilen je Heizkörper-Zone (kalter Raum und Spender in derselben wirksamen Stufe, nach 30 min Wärme, Prüfung nach 45 min, Spender höchstens 2 h, danach 1 h Ruhe, „hilft nicht“ nach 3 Fehlschlägen); Vorlauf-Berater für `hems`; Empfehlung Voreinstellung (7 Tage Proben); Stufen (`set_stage` ändert nur die Stufe am aktuellen Raum), Fühlerpaar (vertauscht? jede Minute per `SwapWatch`, im Protokoll als Problem; neue Wahl beurteilt neu), Wartegründe des vorgemerkten Laufs im Protokoll (jeder Grund einmal je Nacht, um 1 Uhr „heute Nacht nicht gestartet“, Abbruch von Hand mit letztem Grund; 1.4.1), Testlauf mit Rückweg (`RESTORE`; geht er nicht hinaus → `release`; nach einem Neustart mitten im Lauf erneut versucht), Rückfall (`release`, auch für verwaiste Thermostate), `before_rooms_change` vor `PUT api/settings`/`PUT api/rooms/{id}`, Vorlage der Notfall-Automation, Status; im Bad gilt ein Fenster 30 min nach eigenem Befehl oder Ventilschluss (> 30 % → ≤ 5 %) nicht; 1.4.2: jede Minute `_learn` (CalmWatch, StepWatch je Thermostat ab „Beobachten“, nicht während des eigenen Testlaufs), Testlauf-Stufen sofort ins Kennfeld, `_due_reads`/`_read_positions` (W600 mit bekanntem Gerätenamen: `RoomDetails.zigbee_name` oder per Template `device_attr(device_id(…), 'name')`, 10 s gestaffelt), Ventil „aktuell“ (W600 ≤ 10 min nach Abfrage bzw. mit `last_seen` nach beantworteter Abfrage, sonst ≤ 70 min nach Meldung), `changes()` (Live-Signal für die Seite), `set_base_topic`, Vormerken nur abwärts, wenn „ganz offen“ bekannt und „zu“ nicht; `/data/rooms.json` (Korrekturen, Umverteil-Statistik, Testläufe, Kennfelder `maps`, Testlauf); 1.4.3: im Takt `_rate` (Regelgüte), `_ensure_regulation` (Bedarfskurve aus 21 Tagen Proben, Verstärkung aus dem Raummodell), `_follow_demand` (nur „Steuern“, Kaskade aktiv, gesperrt bei Anschlag, ohne Wärme, Fenster + 30 min, Sollwechsel < 30 min, Aufheizen, Spender, Modus ≠ normal), `_new_day` (erster Takt nach Mitternacht: Verstärkung, Bedarf 20 % zum 7-Tage-Median, Protokollzeile je Raum); `_outdoor_3h` aus den Proben; Durchlauf: `start_sweep`, `start_sweep_all` (Warteschlange), `cancel_sweep(s)`, `_advance_sweep` jede Minute, `_end_sweep` (Ergebnis `sweeps`, je Thermostat 3, Rückweg `RESTORE` bzw. Fortsetzung des Testlaufs), ein vorgemerkter Testlauf startet nicht, solange ein Durchlauf läuft, `_testing()` gilt auch für den Durchlauf (keine Regelung, kein Alltagspunkt, keine Regelgüte); `reset_regulation`; nach einem Lauf `_hand_back_after_run` (in „Steuern“ live: `take_over` + `_control`, sonst
`_restore_thermostat`: abgelehnt/nicht ausgeführt → `_fallback_to_setpoint` aufs Raum-Soll; Ergebnis unbekannt oder
Rückfall gescheitert → `_retry_restore`, jede Minute, sobald der Schreibschutz nichts mehr unterwegs hat, höchstens
2 h); `_valve_check` (Alter der gemeldeten Stellung, Spreizung des Fühlerpaars und die Öffnung aus den Testläufen,
Hinweis „vermutlich alt“); in `rooms.json` zusätzlich `quality`, `demand`, `demand_day_start`, `gain` je Raum, `sweeps`, laufender `sweep` (nach Neustart Rückweg) |

- **Stufen:** `room_control.mode` (Aus/Beobachten/Vorschau/Steuern) und `RoomDetails.stage` (Beobachten/Vorschau/
  Steuern), wirksam die kleinere. Steuern für alle: nur live, `confirm`; je Raum zusätzlich ein Kennfeld, das „zu“
  und „ganz offen“ kennt (1.4.2; vorher ein vollständiger Testlauf), und keine blockierende Warnung. Ohne gespeicherte Einstellung (1.3.x): Beobachten, wenn der Planer auf
  Vorschau stand, sonst Aus. `PUT api/rooms/{id}` behält die Stufe.
- **Rückfall:** Raum verlässt Steuern, Schalter verlässt Steuern, Wechsel auf Demo (`api/mode`, `api/connection`,
  noch live), App-Ende (`run()`-finally, gegen doppelten Abbruch geschützt, höchstens 15 s), Raum entfernt oder
  Thermostat gewechselt, blockierende Warnung eines gestellten, erreichbaren Thermostats → jedes `touched` Thermostat
  auf `fallback_c()` (Notfallwert, sonst Raum-Soll; ohne Raum der im Gate gespeicherte Notfallwert).
- **Lebenszeichen:** `sensor.vaillant_eebus_raeume_lebenszeichen` = Zeit des letzten `minute()`, solange die
  Regelung gesund ist (letzter erfolgreicher `tick` bzw. Start ≤ 12 min, sobald die Raumregelung an ist oder
  Thermostate `touched` sind); sonst bleibt der Zeitstempel stehen. Attribut `notfallwerte` =
  `[{entity_id, temperature}]` aller `touched` Thermostate. Export unabhängig von den Gruppen (`Sensor.always`,
  `add_source(..., always=True)`). Die Vorlage der Notfall-Automation (`GET api/rooms/automation`) setzt nach 15 min
  ohne Lebenszeichen die Thermostate aus `notfallwerte` (`variables`, `repeat for_each`), fehlt das Attribut, alle
  Thermostate der Räume.
- **Raum am Thermostat gemessen:** `Room.measured_at_thermostat` jetzt auch für einen Sensor mit derselben
  IEEE-Adresse wie das Thermostat (Bad mit `sensor.…_local_temperature`; Befund der Abnahme 1.3.0) → nicht im
  Zonen-Mittelwert, Fenster ohne Feuchte, fester Ausgleich statt Korrektur.
- **Oberfläche:** `Rooms.svelte` mit `RoomControlCard.svelte` (Schalter, Lebenszeichen, Notfall-Automation zum
  Kopieren, Fühlerpaar mit letzter Meldung je Fühler, längster Meldepause, Auflösung, Hinweis „vertauscht?“ und Knopf zum Tauschen) und `ThermostatPanel.svelte` (Stufe, Ziel/Ist, Zustand, Befehle
  und Bewegungen heute, Ventil mit Bestätigung, zuletzt gesehen, Abfrage, Warnungen, Testlauf, Regelverhalten mit
  Kennfeld-Tabelle, Testläufe); `RoomEditor.svelte` mit Korrektur-Grenze bzw. Ausgleich, Totzone, Abstand, Befehle je
  Tag und Gerätename in Zigbee2MQTT. Seit 1.4.2 lädt `Rooms.svelte` neu, sobald der Event-Stream ein Ereignis `rooms`
  bringt (`lib/live.svelte.ts`; Backend: `state_events(..., rooms=RoomControl.changes)`, höchstens alle 2 s).

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

- Python: 618 Tests (`pytest`, 1 übersprungen ohne Zeitzonen-Datenbank unter Windows; neu in 1.5.0
  `test_zone_executor.py` (F0/F1/F3, Budget, W6), `test_flow.py` (F2: Entscheidung, Engpass, Phasen,
  Heizkurven-Empfehlung, Voreinstellung am Maximum), `test_night.py` (F3: Fenster, Schritte, Abtau-Warten, Rückkehr,
  Minimalleistung am Muster eines milden Tages), in `test_hems_service.py` F2 und F3 über den ganzen Dienst, in
  `test_hems_models.py` K1/K2/K4/K5; neu in 1.4.3
  `test_regulation.py` (Regelgüte, Bedarf, Verstärkung, Kaskade, Durchlauf, Stellungen des Testlaufs) und in
  `test_rooms.py` Durchlauf per Knopf/für alle Räume/mit Fenster, Nacht-Testlauf mit Durchlauf, Regelgüte nach
  Mitternacht, Zurücksetzen, Neustart; Fenstertests mit den Lüftungsdaten vom 23.11. in `test_hems_models.py`; neu in 1.4.2
  `test_thermostat_learning.py` (Kennfeld, Alltag, Sprungantwort, Abfrage-Takt, Gerätenamen, Testlauf-Schritte) und
  `test_night_bath.py` mit der echten Nacht 22./23.11. im Bad (Fühler, Comet-Temperatur und -Ventil aus dem
  HA-Verlauf; mit 1.4.2 und einem Comet-Modell aus dieser Nacht fertig um 3:55), dazu Abfrage-Tests in
  `test_room_gate.py` und `test_rooms.py`; neu in 1.4.1
  `test_testrun_probe.py` mit der echten Nacht 21./22.11. im Bad; neu in 1.4.0
  `test_room_gate.py` und `test_rooms.py`), gegen die Fake-Bridge (echter gRPC-Server, zwei Zonen wie die Anlage, Zone 2 ohne
  Raumfühler) und ein Fake-Home-Assistant (`tests/fake_home_assistant.py`, mit `add_myvaillant()`: Entitäten und
  Dienste der myVAILLANT-Integration wie im Quellcode der Integration). Die Fake-Bridge prüft Schreibbefehle wie die echte Bridge und zählt jeden in `write_calls`;
  Abnahmetest: im Demo-Betrieb erreicht kein Schreibbefehl die Bridge (alle Steuerungen, auch über die HTTP-API).
  Kein Test spricht mit echter Hardware. Das Fake-HA kennt seit 1.4.0 Zigbee2MQTT-Thermostate (`add_thermostats()`:
  Aqara und Comet mit Neben-Entitäten, Raumsensor, Fühlerpaar; `trv_lag`, `trv_silent`; seit 1.4.2 Template
  `device_attr(…, 'name')`, `mqtt.publish`-Abfragen mit `positions` und `add_last_seen()` wie Zigbee2MQTT).
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
17. Raumregelung 1.4.0, bewusst so: Der Schreibschutz prüft beim Testlauf nur „Raumregelung an“, die Grenzen des
    Geräts und das Budget (16 je Nacht), nicht, ob gerade ein Testlauf läuft (nur `RoomControl` sendet ihn;
    Strukturtests). Die Prüfung von 1.4.0 (Aufwand xhigh, `pruefung-1.4.0.md`, Funde 1–14, H1–H5) ist eingearbeitet;
    H2 bleibt (die Stufe „Steuern“ bleibt im Demo-Betrieb gespeichert, Live braucht ohnehin Bestätigung).
    Neben-Entitäten (Fühlerwahl, Kindersicherung …) liest die App alle 15 min, nicht live. Ein Abtauen erkennt das
    Wärmeangebot nicht live (erst danach), die Leistung bleibt dabei über 50 W. Vorlauf Zone 2 (`circuit_1`) ist beim
    Nutzer schlecht montiert; das Wärmeangebot nimmt dann den Puffer. Die Entscheidung je Raum wird nur alle 5 min neu
    gerechnet (die Seite zeigt sie so lange). Nachtabsenkung und Blockbetrieb über die Thermostate kommen erst mit den
    Zonen (1.5.0). Wunsch W1 der Abnahme (Fremdwärme-Phasen) ist nicht umgesetzt. An der Anlage ungeprüft: die echten
    Namen der Neben-Entitäten (nach Zigbee2MQTT-Doku angenommen, `child_lock` als `lock`/`switch`), Rückmeldung und
    Verhalten der W600 beim Testlauf. Fühlerpaar beim Nutzer (Abnahme 1.4.0): Sonoff mit Sonde, Meldung nur bei
    0,2 K Änderung, Pausen bis 90 min; Home Assistant zeigt unveränderte Meldungen nicht (`last_reported` =
    `last_changed`), daher gilt ein Wert 2 h. Ein Fühler, der in dieser Zeit ausfällt, zählt so lange mit seinem
    letzten Wert, außer Zigbee2MQTT meldet ihn „nicht verfügbar“.
18. Raumregelung 1.4.2, bewusst so: Ohne `last_seen` gilt eine Abfrage, die Home Assistant angenommen hat, als
    Bestätigung der W600-Ventilstellung (HA übernimmt unveränderte MQTT-Werte nicht; ob das Gerät antwortete, sieht die
    App dann nicht). Der Comet lässt sich nicht abfragen (Zigbee2MQTT hat für `pi_heating_demand` keinen „get“); er
    meldet nach jedem Sollwert und etwa stündlich, sein Wert gilt 70 min. Punkte im Alltag brauchen „warmes Wasser“ aus
    dem Wärmeangebot der Zone (alle 5 min gerechnet). Das Comet-Modell im Nacht-Test ist unterhalb von +1 K
    angenommen (Schließen bei −1 K). Die Reaktionszeit hat Minutenauflösung (0 = in derselben Minute).
19. Raumregelung 1.4.3, bewusst so: Durchlauf-Punkte sind „sofort“, nicht eingeschwungen (beim PI-Comet zu klein),
    deshalb nur Lückenfüller; die Reaktionszeit aus dem Durchlauf hat Minutenauflösung. Ein Durchlauf braucht die
    Ventil-Entität des Raums; der W600 wird eine Minute nach dem Schritt abgefragt (die Antwort, nicht die zweite
    Abfrage nach 110 s). Der Demo-Betrieb startet keinen Durchlauf (wie den Testlauf; geprüft gegen das Fake-HA).
    Die Regelgüte nimmt die 5-min-Werte (erster Takt nach Mitternacht schließt den Tag; ein Tag ohne Takt nach
    Mitternacht – Raumregelung aus – wird beim nächsten Takt geschlossen). Ventilbewegungen während eines Durchlaufs
    zählen bei „Bewegungen heute“ mit. Bedarf und Verstärkung gelten nur für Räume mit eigenem Sensor; das Bad behält
    den festen Ausgleich. Die Außentemperatur der Bedarfskurve kommt aus den Proben (`outdoor`), sonst aus dem
    aktuellen Messwert. Befund 4c: Zigbee2MQTT hat für den Comet (SPZB0001) keinen Lese-Konverter für
    `pi_heating_demand` (Expose nur `state`); abfragbar wäre `valve_position` (Herstellerattribut 0x4001, `ea.ALL`) –
    ob es in `trv_mode` 2 die echte Stellung zeigt, ist ungeprüft, die App nutzt es nicht (die Entität wäre auch
    schreibbar). Für die Freigabe eines Comet-Werts im Lernen gilt weiter `last_seen` (70 min).
20. Zonen 1.5.0, bewusst so: Der Executor sendet nur, was der Plan für die laufende Stunde sagt; F2 und F3 lösen bei
    jeder Änderung ein neues Planen aus (`wake()`, 2 s). Die Nacht entscheidet Aus/Minimalleistung einmal zu Beginn
    des Fensters aus der Prognose. Ohne Leistungsmeldung über 10 min senkt die Minimalleistung nicht weiter (statt der
    besprochenen Prüfung „Meldung alle 2 min“); die Minimalleistung lernt aus den 21 Tagen der Proben (besprochen
    waren 30). Ein Stopp zählt nur, wenn er nach dem letzten Schritt begann. Die gelernte Minimalleistung wird
    angezeigt; die Regelung selbst folgt dem Puffer-Signal. Engpass und Heizkurven-Empfehlung brauchen die neuen
    Proben (`window`, `testing`, `flow_offset`), also einige Tage nach dem Update. Das Abschalten in der Nacht trifft
    beide Zonen je für sich; eine Zone kann früher zurückkommen. An der Anlage ungeprüft: Verhalten der WP bei
    „Aus“ (Frostschutz 3–4 °C), Puffer-Signal bei Dauerlauf im Frost, Update-Takt von Puffer und Vorlauf-Soll.

## 10. Wo eine Prüfung am meisten bringt

1.5.0 (`feat/zonen` gegen `v1.4.3`, `35b7725`) – **vor dem ersten „Steuern“ (Aufwand max)**: der neue Schreibweg des
Planers (`executor.py` → `write_gate.py`, nur Quick-Veto/Ende/Aus/Auto, nur myVAILLANT, Budget, Rückfall bei Demo/
Stufe/Stopp, Absturz → Quick-Veto endet, Aus-Zonen über die Lebenszeichen-Automation), F3-Aus (kann eine Zone aus
bleiben: Abtau-Warten, Rückkehr bei Verlust/Feuchte/Frost, Fensterende, Handänderung, Neustart mitten im Fenster mit
`nights` in `hems_state.json`, F1 gegen `night_off`), die Minimalleistung (Vorzeichen, Band, Stopp-Erkennung, Puffer
fehlt, Abtauung/Warmwasser), F2 (Sprung zurück, „höher“ ohne Abstand im Executor, Zone-1-Grenze, Proben/Phasen,
Laufzeit der 7-Tage-Rechnung), die Stunde vor Fensterbeginn (darf nichts senden), Umstellung des Nachtfensters und
die Korrekturen K1/K2 (Datenpfade, alte `cycles.json` ohne `pieces`).

1.4.3 (`feat/1.4.3` gegen `v1.4.2`, `619c986`): neuer Schreibgrund `SWEEP` (Budget, Rückweg `restore_c`, kann ein
Durchlauf an Demo/Stufe vorbei senden? – er braucht wie der Testlauf nur „Raumregelung an“ und Live), Zusammenspiel
Durchlauf ↔ Testlauf (Teil 1, Abbruch in beide Richtungen, Neustart, Warteschlange „Alle Räume“), Durchlauf-Punkte als
Lückenfüller und Freigabe „Steuern“ (kann ein Durchlauf „zu“/„ganz offen“ vortäuschen?), Nachführen des Bedarfs
(Sperren, Grenzen), tägliche Anpassung der Verstärkung (Vorzeichen, 12-h-Regel), Fenstererkennung (Feuchte gegen
alte Meldung, Thermostat-Fühler, Kontakt) und die sofortige Reaktion darauf (`_window_now` unter der Sperre).

1.4.2 (`feat/thermostat-lernt` gegen `v1.4.1`, `c91ea69`): neuer Lese-Weg `mqtt.publish` (nur Abfrage, Gerätename
und Basis-Topic geprüft – kann ein Name eine Abfrage zu einem `set` machen?), Kennfeld und Freigabe „Steuern“ (kann ein
falscher Punkt „zu“/„ganz offen“ vortäuschen?), Testlauf-Schritte und Fortsetzung über Nächte, PI-Warten, Live-Signal
(`state_events` mit zwei Warteschlangen, Abbruch). Zuvor:

Zu prüfen ist der Branch `feat/thermostate` (1.4.0) gegen `v1.3.1` (`c70845a`) – **vor** dem ersten Steuern an der
Anlage (Aufwand max). Eine erste Prüfung (Aufwand xhigh, `pruefung-1.4.0.md` in den Notizen) ist eingearbeitet
(`bbb9bbe` und folgende); eine zweite sollte vor allem die Korrekturen nachprüfen: Wert unterwegs als aktueller Wert,
`RESTORE` und `restore_c`, Budget je Nacht, `touched` und seine Freigabe, verwaiste Thermostate (`trv` im Befehl),
Lebenszeichen an `tick` gekoppelt, `notfallwerte` und die Automation, Fenster im Bad. Schwerpunkt ist der neue
Schreibweg zu den Thermostaten (Abschnitt 5f):

- **`RoomGate`** (`write_gate.py`): Kann irgendein Weg im Demo-Betrieb, in der Vorschau, bei Stufe ≠ Steuern oder auf
  ein fremdes Thermostat / eine myVAILLANT-Zone senden? Sind Totzone, Abstand, Höchstzahl und ihre Ausnahmen richtig
  und nebenläufig sicher (Sperre je Thermostat), überstehen sie Neustart und Tageswechsel (Zeitumstellung)? Stimmen
  Bestätigung, „Nicht bestätigt“, Pause und Rückfall (`touched`, zwischengespeicherte Grenzen)?
- **`ha_client.call_room_service()`**: nur `climate.set_temperature` mit `temperature`; Fehlerklassen.
- **Raumregelung** (`rooms/`): Regeln in `controller.py` (Vorrang, Korrektur, Kaskade, Handänderung, Vorheizen über
  Mitternacht), Testlauf in `testrun.py` (Nachtfenster über Mitternacht, Budget, Abbruch, Wiederherstellen nach
  Neustart), Umverteilen und Vorlauf-Berater in `service.py`, Rückfall beim Stoppen (doppelter Abbruch, 15 s),
  Lebenszeichen (stirbt mit der Schleife), Vorlage der Notfall-Automation (gültiges YAML für HA, Template).
- **Zusammenspiel mit `hems/`** (`on_sampled`, `set_flow_advisor`, `_observed`), `Room.measured_at_thermostat` über
  die IEEE-Adresse und seine Folgen (Zonen-Mittelwert, Hausmodell, Fenster).
- **API/Oberfläche:** `api/rooms/*`, Bestätigungen, Wechsel auf Demo, `Rooms.svelte`, `RoomControlCard.svelte`,
  `ThermostatPanel.svelte`, `RoomEditor.svelte` (375 px, Screenreader).

Die übrigen Punkte unten gelten weiter (Stand 1.3.0, Branch `feat/stufe-1-beobachten` gegen `v1.2.1`):

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
