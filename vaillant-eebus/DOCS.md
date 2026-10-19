# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe **lokal über EEBUS** an Home Assistant an – ohne Vaillant-Cloud,
ohne eBUS-Adapter und ohne MQTT.

## Stand dieser Version

Version 0.4.0 koppelt die App mit dem Gateway Ihrer Wärmepumpe und zeigt deren Werte live in einer Übersicht,
jede Heizzone einzeln.
Die App liest nur; sie sendet nichts an die Wärmepumpe. Steuerung und Sensoren in Home Assistant folgen in den
nächsten Versionen.

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
In dieser Version ist die Übersicht nur eine Anzeige.

Was die Wärmepumpe über EEBUS nicht liefert, steht als **nicht verfügbar** da.

**Raumtemperatur:** Meldet eine Zone über EEBUS einen gültigen Wert, zeigt ihr Rundinstrument ihn mit dem Hinweis
„Raumfühler (EEBUS)“. Nicht jede Zone meldet einen; dann steht dort „—“. Eine spätere Version berechnet die
Raumtemperatur jeder Zone als Mittelwert der Temperatursensoren, die Sie in Home Assistant für die Räume dieser
Zone auswählen.

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

Die App speichert ihre EEBUS-Identität in `/data/eebus` (Zertifikat und Seriennummer).
Dieser Ordner ist Teil der Home-Assistant-Sicherung. Gehen die Daten verloren, ändert sich die SKI
der App und die Kopplung in der myVAILLANT-App muss neu gemacht werden.

## Optionen

| Option | Bedeutung |
|---|---|
| Log-Stufe | Wie ausführlich die App ins Log schreibt. `debug` schaltet zusätzlich das SHIP-Log der EEBUS-Bridge ein. |

## Lizenzen

Die App enthält die EEBUS-Bridge aus [volschin/eebus-ha-bridge](https://github.com/volschin/eebus-ha-bridge)
(MIT-Lizenz, Copyright (c) 2024 volschin). Der Lizenztext liegt im Image unter
`/usr/share/licenses/eebus-bridge/LICENSE`.
