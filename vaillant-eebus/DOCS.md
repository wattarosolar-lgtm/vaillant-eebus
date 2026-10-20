# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe **lokal über EEBUS** an Home Assistant an – ohne Vaillant-Cloud,
ohne eBUS-Adapter und ohne MQTT.

## Stand dieser Version

Version 0.6.0 koppelt die App mit dem Gateway Ihrer Wärmepumpe, zeigt deren Werte live in einer Übersicht, jede
Heizzone einzeln, und kann Solltemperaturen, Betriebsarten und die Einmalladung des Warmwassers ändern.
Nach der Installation läuft die App im **Demo-Betrieb** und sendet nichts an die Wärmepumpe (siehe unten).
Die Raumtemperatur jeder Zone kommt aus Temperatursensoren in Home Assistant, und die App meldet die Werte der
Wärmepumpe als Sensoren an Home Assistant (siehe „Home Assistant“).

## Einrichtung

1. App starten. Auf der App-Seite **In der Seitenleiste anzeigen** einschalten und dann in der Seitenleiste
   **Wärmepumpe** öffnen (oder **Benutzeroberfläche öffnen** wählen).
2. **Gateway auswählen:** Die App sucht im Netzwerk nach EEBUS-Geräten. Wählen Sie das Gateway Ihrer
   Wärmepumpe (VR921 oder VR940f).
3. **In der myVAILLANT-App bestätigen:** *Einstellungen → Netzwerk → EEBUS*, dort diese App auf
   **Vertrauenswürdig** setzen. Der Assistent zeigt die Kennung (SKI) der App zum Vergleich.
4. Sobald die Verbindung steht, zeigt der Assistent **Verbunden**. Laden Sie dann den **Diagnose-Dump** herunter.

Das gekoppelte Gateway steht in `/data/settings.json`. Die App meldet es nach jedem Neustart selbst wieder an.

Nach einem Start oder Update der App lehnt das Gateway die ersten Verbindungsversuche oft ab. Bis die Werte
erscheinen, können einige Minuten vergehen. Das Log fasst die Versuche zusammen und nennt die Dauer, sobald die
Verbindung steht.

## Übersicht

Die Seite **Übersicht** zeigt für jede Heizzone und für das Warmwasser ein Rundinstrument: die Solltemperatur auf
dem Bogen mit ihrem erlaubten Bereich, die aktive Betriebsart und beim Warmwasser die Speichertemperatur. Jede Zone trägt den Namen,
den sie in der Anlage hat (z. B. „Zone 1“ und „Zone 2“). Darunter stehen Leistung,
Energie und Temperaturen. Oben sieht man, ob das Gateway verbunden ist, ob die Wärmepumpe ihren Heartbeat sendet,
ihren Betriebszustand, die Außentemperatur und ob der Verdichter läuft (ab 100 W Leistungsaufnahme).

Die Werte aktualisieren sich von selbst, sobald die Wärmepumpe etwas meldet, spätestens nach einer Minute.

**Bedienen:** Die Solltemperatur stellen Sie ein, indem Sie den Knopf auf dem Bogen ziehen (mit Maus oder
Finger), mit **−** und **+** darunter oder mit den Pfeiltasten, wenn der Bogen ausgewählt ist. Darunter wählen Sie
die Betriebsart (**Aus**, **Auto**, **Ein**; **Eco** nur, wenn die Anlage es anbietet). Die App sendet den Wert
einmal, kurz nachdem Sie losgelassen oder aufgehört haben zu klicken. Beim Warmwasser schaltet **Einmalladung** den
Speicher einmal auf. Bereich, Schrittweite und Betriebsarten kommen immer von der Anlage.

**Zonen im Auto-Betrieb:** Im Auto-Betrieb heizt die Zone nach dem Zeitprogramm des Reglers. Dessen Temperatur
meldet das Gateway über EEBUS nicht, und eine über EEBUS gesendete Solltemperatur wirkt dort nicht. Das
Rundinstrument zeigt die Solltemperatur, die das Gateway meldet; sie gilt nur in der Betriebsart **Ein** und kann
von dem Wert abweichen, den myVAILLANT für das laufende Zeitfenster zeigt. Im Auto-Betrieb ist sie gesperrt
(Regler und −/+). Für eine zeitweise höhere oder
niedrigere Temperatur (Boost) nutzen Sie die myVAILLANT-App oder deren Integration in Home Assistant. Auch was Sie
dort im Auto-Betrieb ändern, sieht diese App nicht. In der Betriebsart **Ein** heizt die Zone auf die
Solltemperatur, die Sie hier einstellen.

Was die Wärmepumpe über EEBUS nicht liefert, steht als **nicht verfügbar** da.

**Raumtemperatur:** Die Raumfühler der Zonen liefern über EEBUS keine verlässlichen Werte. Das Rundinstrument
einer Zone zeigt deshalb den Mittelwert der Räume, die Sie ihr in den Einstellungen zuordnen („Mittelwert aus
2 Räumen“). Ohne zugeordnete Räume steht dort „—“ mit einem Hinweis. Die Karte **Innentemperaturen** listet alle
gewählten Räume und die Mittelwerte je Zone.

**Energie heute (berechnet):** Die Wärmepumpe meldet über EEBUS keine Energiezähler, nur ihre aktuelle
Leistungsaufnahme. Die App rechnet daraus den Stromverbrauch seit Mitternacht (Zeitzone von Home Assistant). Das
ist eine Schätzung. Lief die App nicht oder war die Wärmepumpe nicht erreichbar, steht „(lückenhaft)“ dahinter;
die fehlende Zeit wird nicht geschätzt. Das gilt auch für den ersten Tag nach der Installation.

**Wärme heute und Arbeitszahl heute:** Haben Sie in den Einstellungen einen Sensor mit der heute erzeugten Wärme
gewählt, zeigt die Karte „Leistung und Energie“ diesen Wert und die Arbeitszahl des Tages (Wärme ÷ Strom). Die
Arbeitszahl erscheint nur, wenn der Wärmewert aktuell ist (höchstens 3 Stunden alt), der Stromverbrauch des Tages
lückenlos ist und mindestens 0,5 kWh beträgt.

## Demo- und Live-Betrieb

In der Kopfzeile schalten Sie zwischen **Demo** und **Live** um; der gewählte Betrieb ist dort hervorgehoben.

- **Demo** (Voreinstellung nach der Installation): Ein blaues Band unter der Kopfzeile sagt, dass die App nur
  liest. Eine Änderung erscheint einige Sekunden lang blau als Vorschau, danach zeigt die Kachel wieder den echten
  Wert der Anlage. Das Protokoll vermerkt „Würde senden: …“.
- **Live** (Umschalter orange): Die App sendet Änderungen an die Wärmepumpe. Das Umschalten muss bestätigt werden.
  Bis die Anlage den neuen Wert meldet, zeigt die Kachel ihn blass; danach normal. Sobald die Wärmepumpe den
  Befehl quittiert hat, können Sie weiter einstellen. Meldet die Anlage den Wert nicht innerhalb von 30 Sekunden,
  steht das im Protokoll.

Eine Änderung gilt immer für den Betrieb, in dem Sie sie gemacht haben: Wechseln Sie den Betrieb, während die App
Klicks auf − und + noch sammelt, verwirft sie diese, statt sie im neuen Betrieb zu senden.

Bleibt die Antwort der Wärmepumpe aus (etwa weil die Verbindung beim Senden abreißt), ist unklar, ob der Wert
angekommen ist. Die App meldet dann „Ergebnis unbekannt“, sendet nicht von selbst erneut und beobachtet weiter, ob
die Wärmepumpe den Wert meldet. Kommt die Verbindung erst später zurück, trägt sie das Ergebnis nach.

Schutzregeln im Live-Betrieb: Jeder Wert muss im Bereich und auf der Schrittweite der Anlage liegen. Je
Einstellung sendet die App höchstens alle 5 Sekunden; stellen Sie schneller nach, wartet sie so lange. Ein Wert,
der schon eingestellt ist, wird nicht noch einmal gesendet. Ohne Verbindung zum Gateway sendet die App nichts.

Der gewählte Betrieb bleibt nach einem Neustart oder Update erhalten. Ist die Einstellungsdatei beschädigt,
startet die App sicherheitshalber im Demo-Betrieb.

## Home Assistant

Die App liest Werte aus Home Assistant und meldet ihre eigenen Werte dorthin. Sie ruft keine Dienste auf und
ändert keine anderen Geräte oder Entitäten. Ist Home Assistant kurz nicht erreichbar (etwa bei einem Neustart),
laufen Übersicht und Steuerung der Wärmepumpe weiter; die Werte aus Home Assistant stehen solange als „nicht
verfügbar“ da.

Eingerichtet wird alles unter **Einstellungen**, gespeichert mit **Speichern**:

- **Räume aus Home Assistant:** Unter „Raum hinzufügen“ suchen Sie einen Temperatursensor (°C oder °F), geben dem
  Raum einen Namen und wählen Zone 1, Zone 2 oder keine Zone. Ein Wert zählt für den Mittelwert, solange er eine
  Zahl ist und der Sensor sich in den letzten 30 Minuten gemeldet hat; sonst steht er als „veraltet“ oder „nicht
  verfügbar“ in der Karte Innentemperaturen.
- **Wärme heute:** ein Energiesensor (Wh, kWh oder MWh), der jeden Tag bei 0 beginnt. Die myVAILLANT-Integration
  liefert solche Werte; aus einem Gesamtzähler macht der Helfer „Verbrauchszähler“ mit täglichem Zyklus einen
  Tageswert.
- **Export nach Home Assistant:** Welche Werte die App als Sensoren meldet, wählen Sie nach Gruppen. Voreingestellt
  sind Status, Zonen, Warmwasser, Temperaturen sowie Leistung und Energie; Leistungsdeckel und
  Verdichter-Flexibilität sind aus.

Die Sensoren heißen `sensor.vaillant_eebus_…` bzw. `binary_sensor.vaillant_eebus_…`, zum Beispiel
`sensor.vaillant_eebus_outdoor_temperature`, `sensor.vaillant_eebus_zone_1_room_temperature` (Mittelwert der
Räume) oder `sensor.vaillant_eebus_energy_today`. Die vollständige Liste steht in den Einstellungen unter
„Gemeldete Sensoren“. Die App meldet eine Änderung höchstens alle 10 Sekunden je Sensor, Energie höchstens jede
Minute. „Energie heute (berechnet)“ lässt sich im Energie-Dashboard als Verbrauch der Wärmepumpe wählen; es bleibt
eine Schätzung.

**Bekannte Einschränkungen der Sensoren:** Sie haben in Home Assistant keine feste Identität. Man kann sie dort
nicht umbenennen, keinem Bereich zuordnen und nicht in der Geräteübersicht finden. Nach einem Neustart von Home
Assistant fehlen sie kurz, bis die App sie neu meldet (meist innerhalb weniger Sekunden). Werte, die Ihre Anlage
nie liefert (etwa Vorlauftemperatur oder Energiezähler), bekommen keinen Sensor.

## Protokoll

Die Seite **Protokoll** listet, neueste Einträge zuerst:

- **Schreiben:** jede Änderung mit Ergebnis: „Würde senden“ (Demo); „Wird gesendet“ (steht im Protokoll, bevor
  der Befehl die App verlässt), „Gesendet“, „Bestätigt vom Gerät“; „Abgelehnt“ mit Grund; „Nicht ausgeführt“ (die
  Wärmepumpe oder die Bridge hat abgelehnt); „Ergebnis unbekannt“; „Nicht bestätigt“; nach einer
  Verbindungsunterbrechung „Nachträglich bestätigt“ oder „Nicht übernommen“,
- **Gerät:** Sollwerte, Betriebsarten und Einmalladung, die außerhalb der App geändert wurden, etwa in der
  myVAILLANT-App,
- **Verbindung:** Kopplung, Verbindungsaufbau und -abbrüche, auch die Verbindung zu Home Assistant,
- **Modus:** Start der App mit ihrem Betrieb und jeder Wechsel zwischen Demo und Live,
- **Einstellungen:** geänderte Räume, Wärmesensor und Export.

Messwerte stehen nicht im Protokoll. Es behält die letzten 10.000 Einträge.

## Diagnose-Dump

Der Dump (Einstellungen → Diagnose) beschreibt die vollständige EEBUS-Struktur der Anlage: Zonen, Messwerte,
Sollwerte mit Grenzen und angebotene Betriebsarten. Die App schickt dafür nur Leseanfragen an das Gateway.
Der Dump enthält Modell- und Seriennummern Ihrer Geräte; die SKI des Gateways ist gekürzt.

## Voraussetzungen

- Vaillant-Wärmepumpe mit Vaillant-Gateway (VR921 oder VR940f) und der myVAILLANT-App.
- Home Assistant OS oder Supervised (die App braucht das Host-Netzwerk für die EEBUS-Suche per mDNS).
- **Das Gateway erlaubt nur eine aktive EEBUS-Verbindung.** Ist bereits ein anderer EEBUS-Partner
  gekoppelt (z. B. ein Energiemanager, das Add-on „EEBUS Bridge“ oder eine andere EEBUS-App), kommt keine
  Verbindung zustande. Andere EEBUS-Apps bitte stoppen; sie belegen außerdem dieselben Ports.

## Daten und Sicherung

Die App speichert ihre EEBUS-Identität in `/data/eebus` (Zertifikat und Seriennummer), das gekoppelte Gateway,
den gewählten Betrieb, die Räume und die Export-Auswahl in `/data/settings.json`, das Protokoll in `/data/app.db`
und den berechneten
Energieverbrauch je Tag (400 Tage) in `/data/energy.json`.
Diese Dateien sind Teil der Home-Assistant-Sicherung. Gehen die Daten in `/data/eebus` verloren, ändert sich die
SKI der App und die Kopplung in der myVAILLANT-App muss neu gemacht werden.

## Optionen

| Option | Bedeutung |
|---|---|
| Log-Stufe | Wie ausführlich die App ins Log schreibt. `debug` schaltet zusätzlich das SHIP-Log der EEBUS-Bridge ein. |

## Lizenzen

Die App enthält die EEBUS-Bridge aus [volschin/eebus-ha-bridge](https://github.com/volschin/eebus-ha-bridge)
(MIT-Lizenz, Copyright (c) 2024 volschin). Der Lizenztext liegt im Image unter
`/usr/share/licenses/eebus-bridge/LICENSE`.
