# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe **lokal über EEBUS** an Home Assistant an – ohne Vaillant-Cloud,
ohne eBUS-Adapter und ohne MQTT.

## Stand dieser Version

Version 0.5.0 koppelt die App mit dem Gateway Ihrer Wärmepumpe, zeigt deren Werte live in einer Übersicht, jede
Heizzone einzeln, und kann Solltemperaturen, Betriebsarten und die Einmalladung des Warmwassers ändern.
Nach der Installation läuft die App im **Demo-Betrieb** und sendet nichts an die Wärmepumpe (siehe unten).
Sensoren in Home Assistant und die Raumtemperatur aus Home Assistant folgen in den nächsten Versionen.

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

Die Seite **Übersicht** zeigt für jede Heizzone und für das Warmwasser ein Rundinstrument: den gemessenen Wert,
die Solltemperatur auf dem Bogen mit ihrem erlaubten Bereich und die aktive Betriebsart. Jede Zone trägt den Namen,
den sie in der Anlage hat (z. B. „Zone 1“ und „Zone 2“). Darunter stehen Leistung,
Energie und Temperaturen. Oben sieht man, ob das Gateway verbunden ist, ob die Wärmepumpe ihren Heartbeat sendet,
ihren Betriebszustand, die Außentemperatur und ob der Verdichter läuft (ab 100 W Leistungsaufnahme).

Die Werte aktualisieren sich von selbst, sobald die Wärmepumpe etwas meldet, spätestens nach einer Minute.

**Bedienen:** Unter jedem Rundinstrument stellen Sie die Solltemperatur mit **−** und **+** ein und wählen die
Betriebsart (**Aus**, **Auto**, **Ein**; **Eco** nur, wenn die Anlage es anbietet). Mehrere Klicks auf − oder +
sammelt die App und sendet den Wert einmal, kurz nachdem Sie aufgehört haben. Beim Warmwasser schaltet
**Einmalladung** den Speicher einmal auf. Bereich, Schrittweite und Betriebsarten kommen immer von der Anlage.

**Solltemperatur im Auto-Betrieb:** Im Auto-Betrieb regelt der Regler nach seinem Zeitprogramm. Die Temperatur
des aktuellen Zeitfensters meldet das Gateway über EEBUS nicht; die App zeigt den Sollwert, den das Gateway meldet.
Er kann deshalb von dem Wert abweichen, den die myVAILLANT-App für das laufende Zeitfenster zeigt.

Was die Wärmepumpe über EEBUS nicht liefert, steht als **nicht verfügbar** da.

**Raumtemperatur:** Meldet eine Zone über EEBUS einen gültigen Wert, zeigt ihr Rundinstrument ihn mit dem Hinweis
„Raumfühler (EEBUS)“. Nicht jede Zone meldet einen; dann steht dort „—“. Eine spätere Version berechnet die
Raumtemperatur jeder Zone als Mittelwert der Temperatursensoren, die Sie in Home Assistant für die Räume dieser
Zone auswählen.

## Demo- und Live-Betrieb

Oben rechts schalten Sie zwischen **Demo** und **Live** um. Ein farbiges Band unter der Kopfzeile zeigt, welcher
Betrieb gerade gilt.

- **Demo** (blau, Voreinstellung nach der Installation): Die App liest nur. Eine Änderung erscheint einige
  Sekunden lang als Vorschau mit dem Hinweis „Demo: wird nicht gesendet“, danach zeigt die Kachel wieder den
  echten Wert der Anlage. Das Protokoll vermerkt „Würde senden: …“.
- **Live** (orange): Die App sendet Änderungen an die Wärmepumpe. Das Umschalten muss bestätigt werden. Bis die
  Anlage den neuen Wert meldet, zeigt die Kachel „Wird gesendet …“ und weiter den bisherigen Wert; danach den
  neuen. Meldet die Anlage den Wert nicht innerhalb von 30 Sekunden, steht das im Protokoll.

Schutzregeln im Live-Betrieb: Jeder Wert muss im Bereich und auf der Schrittweite der Anlage liegen. Je
Einstellung sendet die App höchstens alle 5 Sekunden; ein Wert, der schon eingestellt ist, wird nicht noch einmal
gesendet. Ohne Verbindung zum Gateway sendet die App nichts.

Der gewählte Betrieb bleibt nach einem Neustart oder Update erhalten. Ist die Einstellungsdatei beschädigt,
startet die App sicherheitshalber im Demo-Betrieb.

## Protokoll

Die Seite **Protokoll** listet, neueste Einträge zuerst:

- **Schreiben:** jede Änderung mit Ergebnis („Würde senden“, „Gesendet“, „Bestätigt vom Gerät“, „Abgelehnt“ mit
  Grund, „Nicht bestätigt“, Fehler),
- **Gerät:** Sollwerte, Betriebsarten und Einmalladung, die außerhalb der App geändert wurden, etwa in der
  myVAILLANT-App,
- **Verbindung:** Kopplung, Verbindungsaufbau und -abbrüche,
- **Modus:** Start der App mit ihrem Betrieb und jeder Wechsel zwischen Demo und Live.

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

Die App speichert ihre EEBUS-Identität in `/data/eebus` (Zertifikat und Seriennummer), das gekoppelte Gateway
und den gewählten Betrieb in `/data/settings.json` und das Protokoll in `/data/app.db`.
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
