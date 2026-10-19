# Changelog

## 0.3.0

- Neue Seite **Übersicht**: je Heizzone und für das Warmwasser ein Rundinstrument mit Messwert, Solltemperatur,
  erlaubtem Bereich und Betriebsart, dazu Leistung, Energie und Temperaturen. Oben stehen Verbindung, Heartbeat,
  Betriebszustand, Verdichter und Außentemperatur. Die Übersicht zeigt nur an; gesteuert wird noch nichts.
- Die Werte aktualisieren sich live, sobald die Wärmepumpe etwas meldet, spätestens nach einer Minute.
- Die regelmäßige Leseanfrage verhindert, dass die Bridge die Verbindung zum Gateway ohne Grund neu aufbaut
  (`monitoring recovery` im Log).
- Werte, die die Wärmepumpe über EEBUS nicht liefert, stehen als „nicht verfügbar“ da. Raumtemperaturen kommen
  bei Vaillant nicht über EEBUS; eine spätere Version übernimmt sie aus Home Assistant.
- Die Gerätesuche zeigt auch für Geräte ohne Marke und Modell einen erkennbaren Namen statt
  „Unbekanntes EEBUS-Gerät“ (Name aus der Netzwerksuche, SHIP-ID oder Gerätekategorie).
- Nach einem Update lädt die Oberfläche sofort die neue Version statt einer zwischengespeicherten.

## 0.2.1

- Die App erkennt, ob sie mit ihrer eigenen EEBUS-Bridge spricht. Läuft eine andere EEBUS-App, zeigt sie das
  deutlich an, statt über deren Bridge zu koppeln.
- Die Bridge der App nutzt einen eigenen gRPC-Port (50471) statt des Standard-Ports 50051.
- Ist der EEBUS-Port 4712 durch eine andere App belegt, sagt die Oberfläche das.
- Die Gerätesuche zeigt Marke, Modell, Gerätetyp und Seriennummer der gefundenen Geräte.
- Fehlermeldungen nennen den Fehlercode; große Diagnose-Dumps werden nicht mehr abgeschnitten.

## 0.2.0

- Einrichtungsassistent in der Seitenleiste „Wärmepumpe“: Gateway auswählen, in myVAILLANT bestätigen.
- Das gekoppelte Gateway wird gespeichert und nach jedem Neustart automatisch wieder angemeldet.
- Diagnose-Dump zum Herunterladen (nur lesend).
- Warnung von s6-overlay beim Start behoben.

## 0.1.0

- Erste Version: EEBUS-Bridge und Backend laufen in der App, das Backend schreibt die SKI der Bridge ins Log.
  Noch ohne Kopplung, Oberfläche und Steuerung.
