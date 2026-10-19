# Changelog

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
