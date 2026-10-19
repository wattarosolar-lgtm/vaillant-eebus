# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe **lokal über EEBUS** an Home Assistant an – ohne Vaillant-Cloud,
ohne eBUS-Adapter und ohne MQTT.

## Stand dieser Version

Version 0.1.0 ist die technische Grundlage:

- Die EEBUS-Bridge startet und erzeugt beim ersten Start ihr Zertifikat.
- Das Backend verbindet sich mit der Bridge und schreibt deren **SKI** ins Log.

Kopplung mit dem Gateway, Oberfläche und Steuerung folgen in den nächsten Versionen.

## Voraussetzungen

- Vaillant-Wärmepumpe mit Vaillant-Gateway (VR921 oder VR940f) und der myVAILLANT-App.
- Home Assistant OS oder Supervised (die App braucht das Host-Netzwerk für die EEBUS-Suche per mDNS).
- **Das Gateway erlaubt nur eine aktive EEBUS-Verbindung.** Ist bereits ein anderer EEBUS-Partner
  gekoppelt (z. B. ein Energiemanager oder das Add-on „EEBUS Bridge“), kommt keine Verbindung zustande.

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
