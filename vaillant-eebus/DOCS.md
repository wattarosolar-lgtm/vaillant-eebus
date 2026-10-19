# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe **lokal über EEBUS** an Home Assistant an – ohne Vaillant-Cloud,
ohne eBUS-Adapter und ohne MQTT.

## Stand dieser Version

Version 0.2.0 koppelt die App mit dem Gateway Ihrer Wärmepumpe und kann einen Diagnose-Dump erstellen.
Die App liest nur; sie sendet nichts an die Wärmepumpe. Übersicht, Steuerung und Sensoren in Home Assistant
folgen in den nächsten Versionen.

## Einrichtung

1. App starten und in der Seitenleiste **Wärmepumpe** öffnen.
2. **Gateway auswählen:** Die App sucht im Netzwerk nach EEBUS-Geräten. Wählen Sie das Gateway Ihrer
   Wärmepumpe (VR921 oder VR940f).
3. **In der myVAILLANT-App bestätigen:** *Einstellungen → Netzwerk → EEBUS*, dort diese App auf
   **Vertrauenswürdig** setzen. Der Assistent zeigt die Kennung (SKI) der App zum Vergleich.
4. Sobald die Verbindung steht, zeigt der Assistent **Verbunden**. Laden Sie dann den **Diagnose-Dump** herunter.

Das gekoppelte Gateway steht in `/data/settings.json`. Die App meldet es nach jedem Neustart selbst wieder an.

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
