# Changelog

## 0.5.1

Korrekturen aus dem Fehlertest vor dem ersten Live-Test:

- Eine Änderung gilt nur für den Betrieb, in dem sie gemacht wurde. Wer kurz nach einem Klick von Demo auf Live
  umschaltet, sendet den Wert nicht mehr versehentlich: Die App verwirft ihn, und das Backend lehnt jeden Befehl ab,
  der in einem anderen Betrieb entschieden wurde.
- Bleibt die Antwort der Wärmepumpe aus, meldet die App „Ergebnis unbekannt“ statt „Fehler“ und beobachtet weiter,
  ob der Wert ankommt. Eindeutige Ablehnungen heißen „Nicht ausgeführt“.
- Jeder Schreibversuch steht als „Wird gesendet“ im Protokoll, bevor er die App verlässt; auch ein Absturz mitten im
  Senden hinterlässt so eine Spur.
- Läuft die Bestätigungszeit während einer Verbindungsunterbrechung ab, trägt die App das Ergebnis nach, sobald die
  Verbindung wieder steht („Nachträglich bestätigt“ oder „Nicht übernommen“).

## 0.5.0

- Die App kann jetzt steuern: Solltemperatur und Betriebsart jeder Heizzone, Solltemperatur und Betriebsart des
  Warmwassers und die Einmalladung. Die Bedienelemente sitzen direkt in den Kacheln der Übersicht.
- **Demo- und Live-Betrieb:** Die App startet im Demo-Betrieb und sendet dann nichts an die Wärmepumpe; eine
  Änderung erscheint nur kurz als Vorschau und wird protokolliert. Erst nach dem Umschalten auf **Live** und einer
  ausdrücklichen Bestätigung sendet die App. Der gewählte Betrieb bleibt über Neustarts erhalten.
- Im Live-Betrieb zeigt eine Kachel „Wird gesendet …“, bis die Wärmepumpe den neuen Wert meldet. Je Einstellung
  sendet die App höchstens alle 5 Sekunden und schickt keinen Wert, der schon eingestellt ist. Grenzen,
  Schrittweite und angebotene Betriebsarten kommen immer vom Gerät.
- Neue Seite **Protokoll**: alle Schreibvorgänge mit Ergebnis, Änderungen am Gerät (z. B. aus der myVAILLANT-App),
  Verbindungsereignisse und Moduswechsel.
- Zonen im Auto-Betrieb erklären, dass dort das Zeitprogramm des Reglers gilt und das Gateway dessen
  Solltemperatur nicht meldet. Der angezeigte Sollwert ist der, den das Gateway über EEBUS meldet.
- Die EEBUS-Bridge schickt einen Befehl für eine Zone nie an eine andere Zone, auch nicht kurz nach einer
  Neuverbindung, solange das Gateway noch nicht alle Zonen gemeldet hat.
- Bricht eine Anfrage an die Bridge ab, bleibt die Live-Verbindung der Oberfläche bestehen.

## 0.4.0

- Die Übersicht zeigt jede Heizzone in einer eigenen Kachel mit ihrem Namen, ihrer Solltemperatur, ihrem
  Bereich und ihrer Betriebsart. Bisher erschien nur eine Kachel, teils mit dem Namen der anderen Zone.
- Die EEBUS-Bridge liest und schreibt jede Zone einzeln. Schreiben ohne Angabe der Zone lehnt sie bei mehreren
  Zonen ab, statt zu raten. Gesteuert wird aus der App erst in einer späteren Version.
- Meldet eine Zone eine gültige Raumtemperatur über EEBUS, zeigt die Kachel sie mit dem Hinweis
  „Raumfühler (EEBUS)“. Künftig wird die Raumtemperatur jeder Zone als Mittelwert von Temperatursensoren aus
  Home Assistant berechnet.
- Beim Start schreibt die App die abgelehnten Verbindungsversuche des Gateways nicht mehr einzeln als Warnung
  ins Log, sondern zusammengefasst, und sagt, wie lange der Verbindungsaufbau gedauert hat.
- Korrektur der Anleitung: Die Raumtemperatur fehlt nicht bei Vaillant allgemein, sondern nur bei Zonen ohne
  gültigen Wert.

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
