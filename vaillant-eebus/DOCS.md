# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe an Home Assistant an – über die **myVAILLANT-Integration** in Home Assistant oder
**lokal über EEBUS** (ohne Vaillant-Cloud, ohne eBUS-Adapter und ohne MQTT).

## Was die App kann

Die App liest die Werte Ihrer Wärmepumpe – über die Entitäten der myVAILLANT-Integration oder direkt vom Gateway über
EEBUS (siehe „Anbindung“) – und zeigt sie live in einer eigenen Oberfläche in der Seitenleiste von Home Assistant,
jede Heizzone einzeln, mit dem Verlauf der Leistung über 24 Stunden. Sie ändert
Solltemperaturen, Betriebsarten und die Einmalladung des Warmwassers. Die Raumtemperatur jeder Zone kommt aus
Temperatursensoren in Home Assistant, und die App meldet die Werte der Wärmepumpe als Sensoren an Home Assistant.
Ein Protokoll hält jede Änderung und jedes Verbindungsereignis fest. Seit Version 1.3.0 lernt ein
**Energiemanagement** Haus und Räume kennen, zählt Takte und Abtauungen und plant 48 Stunden voraus – für die Zonen
nur als Vorschau (siehe „Energiemanagement“). Seit Version 1.4.0 stellt die **Raumregelung** auf Wunsch die
Heizkörperthermostate der Räume (siehe „Raumregelung“).

Nach der Installation läuft die App im **Demo-Betrieb** und sendet nichts an die Wärmepumpe (siehe
„Demo- und Live-Betrieb“).

## Voraussetzungen

- Vaillant-Wärmepumpe mit Vaillant-Gateway (VR921 oder VR940f) und der myVAILLANT-App.
- Home Assistant OS oder Supervised.
- **Mit der Anbindung myVAILLANT:** die Integration **myVAILLANT** (über HACS, `signalkraft/mypyllant-component`,
  ab Version 0.9.19), eingerichtet und mit verfügbaren Entitäten für Zonen und Warmwasser.
- **Mit der Anbindung EEBUS:** Gateway und Home Assistant im selben Netzwerk (die App sucht per mDNS und braucht
  dafür das Host-Netzwerk). **Das Gateway erlaubt nur eine aktive EEBUS-Verbindung.** Ist bereits ein anderer
  EEBUS-Partner gekoppelt (z. B. ein Energiemanager, das Add-on „EEBUS Bridge“ oder eine andere EEBUS-App), kommt
  keine Verbindung zustande. Andere EEBUS-Apps bitte stoppen; sie belegen außerdem dieselben Ports.

## Installation

1. In Home Assistant **Einstellungen → Apps → App-Store** öffnen.
2. Oben rechts **⋮ → Repositories** wählen, diese Adresse eintragen und **Hinzufügen**:
   `https://github.com/wattarosolar-lgtm/vaillant-eebus`
3. Den Dialog schließen. Im App-Store erscheint **Vaillant EEBUS** (sonst die Seite neu laden). Die App öffnen
   und **Installieren** wählen.
4. Nach der Installation **Starten** und auf derselben Seite **In der Seitenleiste anzeigen** einschalten. Mit dem
   **Watchdog** startet Home Assistant die App neu, falls sie abstürzt.
5. In der Seitenleiste **Wärmepumpe** öffnen (oder **Benutzeroberfläche öffnen** wählen). Der
   Einrichtungsassistent führt durch die Einrichtung (nächster Abschnitt).

## Anbindung

Die App erreicht die Wärmepumpe auf einem von zwei Wegen. Der Einrichtungsassistent fragt zuerst danach;
**myVAILLANT** ist vorausgewählt. Installationen bis Version 1.1 nutzen EEBUS, bis Sie umstellen.

| | myVAILLANT in Home Assistant | EEBUS im Heimnetz |
|---|---|---|
| Weg | Entitäten der myVAILLANT-Integration, über die Vaillant-Cloud | direkt zum Gateway, ohne Cloud |
| Werte | so alt wie die letzte Abfrage der Integration (Voreinstellung 30 Minuten) | jede Minute und bei jeder Änderung |
| Zone im Auto-Betrieb | Soll-Änderung wirkt als **zeitweise Übersteuerung** (Quick-Veto) | Solltemperatur gesperrt |
| Leistung und „Energie heute“ | nur mit einem Leistungssensor (optional) | von der Wärmepumpe gemeldet |
| Leistungsdeckel, Failsafe, Verdichter | nicht verfügbar | über die API |
| Kopplung, Diagnose-Dump | nicht nötig bzw. nicht verfügbar | ja |

## Einrichtung

### Mit myVAILLANT

1. **Anbindung wählen:** „myVAILLANT in Home Assistant“ und **Weiter**.
2. **Entitäten prüfen:** Die App sucht die Entitäten der myVAILLANT-Integration und schlägt vor: je Heizzone die
   Klima-Entität (`climate.…_zone_…`), die Warmwasser-Entität (`water_heater.…`), die Außentemperatur und – wenn die
   Integration sie liefert – die aktuelle Leistung („Current Power“). Ergänzen oder entfernen Sie Einträge bei
   Bedarf; als Leistung geht auch ein anderer Sensor in W oder kW, etwa ein Zähler vor der Wärmepumpe. Die eigenen
   Sensoren der App (`sensor.vaillant_eebus_…`) sind keine Quelle: Ihr Wert käme nur von der App selbst zurück.
   **Übernehmen** speichert die Auswahl; die App startet dafür einmal neu.
3. Unter **Einstellungen** die Räume für die Zonen wählen (siehe „Home Assistant“).

Die Integration fragt die Vaillant-Cloud von sich aus nur in ihrem Intervall ab (Option „Sekunden zwischen
Updates“, Voreinstellung 1800). Nach jedem Befehl der App liest sie die Werte nach wenigen Sekunden neu. Vaillant
begrenzt die Zahl der Abrufe; stellen Sie das Intervall nicht zu kurz ein (unter etwa 5 Minuten sperrt die Cloud
zeitweise). Wie alt die Werte sind, zeigt die Kopfzeile der Übersicht („Stand 14:05“).

### Mit EEBUS

1. **Anbindung wählen:** „EEBUS im Heimnetz“ und **Weiter**.
2. **Gateway auswählen:** Die App sucht im Netzwerk nach EEBUS-Geräten. Wählen Sie das Gateway Ihrer
   Wärmepumpe (VR921 oder VR940f).
3. **In der myVAILLANT-App bestätigen:** *Einstellungen → Netzwerk → EEBUS*, dort diese App auf
   **Vertrauenswürdig** setzen. Der Assistent zeigt die Kennung (SKI) der App zum Vergleich.
4. Sobald die Verbindung steht, zeigt der Assistent **Verbunden**. Laden Sie dann den **Diagnose-Dump** herunter
   und heben Sie ihn auf; er hilft bei Fragen zur Anlage.
5. Unter **Einstellungen** die Räume für die Zonen wählen (siehe „Home Assistant“).

Das gekoppelte Gateway steht in `/data/settings.json`. Die App meldet es nach jedem Neustart selbst wieder an.

Nach einem Start oder Update der App lehnt das Gateway die ersten Verbindungsversuche oft ab. Bis die Werte
erscheinen, können einige Minuten vergehen. Das Log fasst die Versuche zusammen und nennt die Dauer, sobald die
Verbindung steht.

### Anbindung wechseln

Unter **Einstellungen → Anbindung** stellen Sie auf den anderen Weg um (mit Bestätigung). Dabei

- wechselt die App in den **Demo-Betrieb**; den neuen Weg schalten Sie bewusst wieder auf Live,
- startet die App neu; die Seite lädt danach von selbst neu.

Mit myVAILLANT ruht die EEBUS-Bridge der App: Sie startet nicht und verbindet sich nicht mit dem Gateway. Die
Kopplung bleibt gespeichert; beim Zurückwechseln verbindet sich die App ohne neue Kopplung.

Die Zonen haben je Anbindung eigene Kennungen. Die Räume nehmen ihre Zone mit: Beim ersten Wechsel ordnet die App
jeden Raum der Zone mit demselben Namen zu („Zone 1“ zu „Zone 1“), danach merkt sie sich die Zuordnung je
Anbindung. Prüfen Sie die Räume nach dem Wechsel einmal unter Einstellungen; eine Zone, die es mit der neuen
Anbindung nicht gibt, steht dort als „Zone nicht mehr vorhanden“.

## Update

Home Assistant meldet eine neue Version unter **Einstellungen → Apps** und auf der Seite der App; dort steht auch,
was sich geändert hat. Ein Update behält Kopplung, Betrieb (Demo oder Live), Räume, Export-Auswahl, Protokoll und
Energiewerte sowie die Stufe des Energiemanagements (ein Planer auf „Steuern“ steht nach dem Update auf 1.3.0 auf
„Vorschau“). Mit EEBUS baut die App nach dem Update
die Verbindung zum Gateway neu auf (siehe oben, das kann einige Minuten dauern); mit myVAILLANT sind die Werte da,
sobald die App wieder mit Home Assistant verbunden ist. Eine offene Seite der App lädt sich ab 1.3.1 nach dem Update
von selbst neu; gibt es dort ungespeicherte Einstellungen, erscheint stattdessen ein Hinweis mit **Seite neu laden**.

**Hinweis zu 1.2.0 → 1.2.1:** Version 1.2.0 hat mit myVAILLANT als Außentemperatur versehentlich den eigenen Sensor
der App vorgeschlagen; der Wert blieb dann stehen. Version 1.2.1 entfernt diese Auswahl beim Start und vermerkt es
im Protokoll. Wählen Sie danach unter Einstellungen → Anbindung den Sensor der Integration (etwa „Outdoor
Temperature“) und übernehmen Sie.

## Übersicht

Die Seite **Übersicht** zeigt für jede Heizzone und für das Warmwasser ein Rundinstrument: die Solltemperatur auf
dem Bogen mit ihrem erlaubten Bereich, die aktive Betriebsart und beim Warmwasser die Speichertemperatur. Jede Zone
trägt den Namen, den sie in der Anlage hat (z. B. „Zone 1“ und „Zone 2“). Darunter stehen Leistung, Energie und
Temperaturen. Oben sieht man, ob das Gateway verbunden ist, ob die Wärmepumpe ihren Heartbeat sendet, ihren
Betriebszustand, die Außentemperatur und ob der Verdichter läuft (ab 100 W Leistungsaufnahme).

Die Werte aktualisieren sich von selbst, sobald die Wärmepumpe etwas meldet: mit EEBUS spätestens nach einer
Minute, mit myVAILLANT so oft, wie die Integration die Cloud abfragt („Stand“ in der Kopfzeile).

**Bedienen:** Die Solltemperatur stellen Sie ein, indem Sie den Knopf auf dem Bogen ziehen (mit Maus oder
Finger), mit **−** und **+** darunter oder mit den Pfeiltasten, wenn der Bogen ausgewählt ist. Darunter wählen Sie
die Betriebsart (**Aus**, **Auto**, **Ein**; **Eco** nur, wenn die Anlage es anbietet). Die App sendet den Wert
einmal, kurz nachdem Sie losgelassen oder aufgehört haben zu klicken. Beim Warmwasser schaltet **Einmalladung** den
Speicher einmal auf. Bereich, Schrittweite und Betriebsarten kommen immer von der Anlage.

**Zonen im Auto-Betrieb mit myVAILLANT:** Das Rundinstrument zeigt die Temperatur, die das Zeitprogramm gerade
vorgibt. Ändern Sie sie, startet die Integration eine **zeitweise Übersteuerung** (Quick-Veto) für die Dauer, die in
ihren Optionen steht (meist 3 Stunden); danach gilt wieder das Zeitprogramm. Während sie läuft, steht unter der Zone
„Zeitweise übersteuert bis …“, und **Übersteuerung beenden** beendet sie vorzeitig (im Demo-Betrieb nur als
Vorschau). Meldet die Integration die Absenkung des Zeitprogramms („eco“, ohne Solltemperatur), steht das unter der
Zone; angezeigt ist dann die letzte Solltemperatur davor, und eine Änderung startet ebenfalls eine Übersteuerung. In
der Betriebsart **Ein** ändert die App die feste Solltemperatur der Zone. In **Aus** zeigt und nimmt sie keine
Solltemperatur an, weil eine Übersteuerung die Zone dann heizen würde.

**Werte über die Cloud:** Mit myVAILLANT zeigt die Integration einen gesendeten Wert oft sofort, fällt kurz auf den
alten Wert der Cloud zurück und meldet erst danach den neuen. Die App zeigt in dieser Zeit den gesendeten Wert blass
und trägt den kurzen Rücksprung nicht als Änderung am Gerät ins Protokoll ein.

**Zonen im Auto-Betrieb mit EEBUS:** Im Auto-Betrieb heizt die Zone nach dem Zeitprogramm des Reglers. Dessen
Temperatur meldet das Gateway über EEBUS nicht, und eine über EEBUS gesendete Solltemperatur wirkt dort nicht. Das
Rundinstrument zeigt die Solltemperatur, die das Gateway meldet; sie gilt nur in der Betriebsart **Ein** und kann
von dem Wert abweichen, den myVAILLANT für das laufende Zeitfenster zeigt. Im Auto-Betrieb ist sie gesperrt
(Regler und −/+). Für eine zeitweise höhere oder niedrigere Temperatur (Boost) nutzen Sie die myVAILLANT-App oder
deren Integration in Home Assistant. Auch was Sie dort im Auto-Betrieb ändern, sieht diese App nicht. In der
Betriebsart **Ein** heizt die Zone auf die Solltemperatur, die Sie hier einstellen.

Was die Wärmepumpe über EEBUS nicht liefert, steht als **nicht verfügbar** da. Mit myVAILLANT fehlen die Zeilen,
die die Integration nie liefert (Vorlauf, Rücklauf, Verdichter, Energiezähler), ganz.

**Raumtemperatur:** Die Raumfühler der Zonen liefern über EEBUS keine verlässlichen Werte. Das Rundinstrument
einer Zone zeigt deshalb den Mittelwert der Räume, die Sie ihr in den Einstellungen zuordnen („Mittelwert aus
2 Räumen“). Ohne zugeordnete Räume steht dort „—“ mit einem Hinweis. Die Karte **Innentemperaturen** listet alle
gewählten Räume und die Mittelwerte je Zone.

**Verlauf 24 Stunden:** Die Karte „Leistung und Energie“ zeigt die Leistungsaufnahme der letzten 24 Stunden als
Linie, in Mittelwerten über je 5 Minuten. Zeigen Sie mit Maus oder Finger auf die Linie, steht der Wert dieser 5
Minuten darüber; mit der Tastatur gehen die Pfeiltasten (Bild auf/ab: eine Stunde). Die Werte kommen aus dem
Verlauf, den Home Assistant für den Sensor `sensor.vaillant_eebus_power` aufzeichnet; dafür muss der Export
„Leistung und Energie“ eingeschaltet sein. Wo nichts aufgezeichnet ist (App aus, Gateway getrennt), hat die Linie
eine Lücke. Nach der Installation füllt sich der Verlauf erst nach und nach.

**Energie heute (berechnet):** Die Wärmepumpe meldet über EEBUS keine Energiezähler, nur ihre aktuelle
Leistungsaufnahme. Die App rechnet daraus den Stromverbrauch seit Mitternacht (Zeitzone von Home Assistant). Das
ist eine Schätzung. Mit myVAILLANT nimmt sie dafür den gewählten Leistungssensor; ohne ihn steht hier „nicht
verfügbar“. Lief die App nicht oder war die Wärmepumpe nicht erreichbar, steht „(lückenhaft)“ dahinter;
die fehlende Zeit wird nicht geschätzt. Das gilt auch für den ersten Tag nach der Installation.

**Wärme heute und Arbeitszahl heute:** Haben Sie in den Einstellungen Sensoren mit der heute erzeugten Wärme
gewählt, zeigt die Karte „Leistung und Energie“ deren Summe und die Arbeitszahl des Tages (Wärme ÷ Strom). Die Summe
erscheint nur, wenn jeder gewählte Sensor einen Wert hat. Die Arbeitszahl erscheint nur, wenn die Wärmewerte aktuell
sind (höchstens 3 Stunden alt), der Stromverbrauch des Tages lückenlos ist und mindestens 0,5 kWh beträgt.

**Energiemanagement:** Darunter zeigen die Karten **Nächste Änderungen** (was das Energiemanagement vorhat, mit
Grund; der ganze Plan auf der Seite „Plan“) und **Takten und Abtauen** (siehe „Energiemanagement“).

## Demo- und Live-Betrieb

In der Kopfzeile schalten Sie zwischen **Demo** und **Live** um; der gewählte Betrieb ist dort hervorgehoben.

- **Demo** (Voreinstellung nach der Installation): Ein blaues Band unter der Kopfzeile sagt, dass die App nur
  liest. Eine Änderung erscheint einige Sekunden lang blau als Vorschau, danach zeigt die Kachel wieder den echten
  Wert der Anlage. Das Protokoll vermerkt „Würde senden: …“. Einzige Nachricht, die die App auch im Demo-Betrieb
  verschickt: die Abfrage der Ventilstellung der Aqara W600 (Raumregelung, nur lesen, verstellt nichts).
- **Live** (Umschalter orange): Die App sendet Änderungen an die Wärmepumpe. Das Umschalten muss bestätigt werden.
  Bis die Anlage den neuen Wert meldet, zeigt die Kachel ihn blass; danach normal. Sobald die Wärmepumpe den
  Befehl quittiert hat, können Sie weiter einstellen. Meldet die Anlage den Wert nicht innerhalb von 30 Sekunden
  (mit myVAILLANT 90 Sekunden), steht das im Protokoll.

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

Die App liest Werte aus Home Assistant und meldet ihre eigenen Werte dorthin. Für das Energiemanagement liest sie
außerdem die Langzeitstatistik von Home Assistant (stündliche Werte, nur lesen). Sie ruft diese Dienste auf:
`weather.get_forecasts` für die Wetterprognose des Energiemanagements (liest nur) und – nur mit der Anbindung
myVAILLANT und nur für die gewählten myVAILLANT-Entitäten – `climate.set_temperature`, `climate.set_hvac_mode`,
`water_heater.set_temperature`, `water_heater.set_operation_mode`, `mypyllant.set_quick_veto` und
`mypyllant.cancel_quick_veto`. Diese gehen wie jede Änderung durch die Schreibsperre (Demo-Betrieb, Prüfung,
Protokoll). Andere Geräte oder Entitäten ändert die App nicht. Ist Home Assistant kurz nicht erreichbar (etwa bei
einem Neustart), laufen mit EEBUS Übersicht und Steuerung der Wärmepumpe weiter; die Werte aus Home Assistant
stehen solange als „nicht verfügbar“ da. Mit myVAILLANT ist die Wärmepumpe so lange nicht erreichbar. Ob die App gerade mit Home Assistant verbunden ist, zeigt die Seite **Einstellungen** neben der
Überschrift „Home Assistant“.

Eingerichtet wird alles unter **Einstellungen**, gespeichert mit **Räume, Wärme und Export speichern** unter den
drei Karten. Verlassen Sie die Seite mit ungespeicherten Änderungen, fragt die App vorher nach.

- **Räume:** Unter „Raum hinzufügen“ suchen Sie einen Temperatursensor (°C oder °F), geben dem Raum einen Namen
  und wählen Zone 1, Zone 2 oder keine Zone. Für einen Raum ohne Sensor wählen Sie sein Heizkörperthermostat; es
  misst am Heizkörper und zählt deshalb nicht im Mittelwert. Alles Weitere je Raum (Soll, Zeitplan, Thermostat,
  statischer Abgleich) stellen Sie auf der Seite **Räume** ein; es bleibt erhalten, wenn Sie hier die Raumliste
  speichern. Ein Wert zählt für den Mittelwert, solange er eine Zahl ist und der
  Sensor sich in den letzten 30 Minuten gemeldet hat; sonst steht er als „veraltet“ oder „nicht verfügbar“ in der
  Karte Innentemperaturen.
- **Wärme heute:** bis zu vier Energiesensoren (Wh, kWh oder MWh), die jeden Tag bei 0 beginnen; die App
  addiert sie. Die myVAILLANT-Integration liefert die Wärme für Heizung und Warmwasser getrennt – wählen Sie beide,
  sonst fällt die Arbeitszahl zu niedrig aus. Aus einem Gesamtzähler macht der Helfer „Verbrauchszähler“ mit
  täglichem Zyklus einen Tageswert. Zeigt Home Assistant einen gewählten Sensor als „nicht verfügbar“, liegt das an
  dessen Integration; die App zeigt dann keine Summe. **myVAILLANT-Integration:** Sie holt die Energiedaten ohne
  weitere Einstellung nur einmal beim Start. Stellen Sie in ihren Optionen „Intervall zwischen Energiedaten
  Updates“ auf höchstens 3 Stunden (z. B. 3600 s), sonst gelten die Werte hier als veraltet. Vaillant begrenzt die
  Zahl der Abrufe je Woche; deaktivieren Sie Energie-Entitäten, die Sie nicht brauchen. Ist das Kontingent
  erschöpft, bleiben die Sensoren „nicht verfügbar“, bis es (montags) zurückgesetzt ist und die Integration neu
  geladen wird.
- **Export nach Home Assistant:** Welche Werte die App als Sensoren meldet, wählen Sie nach Gruppen. Voreingestellt
  sind Status, Zonen, Warmwasser, Temperaturen, Leistung und Energie sowie (bei neuen Installationen)
  Energiemanagement; Leistungsdeckel und Verdichter-Flexibilität sind aus. Den Verlauf 24 Stunden gibt es nur mit
  „Leistung und Energie“.

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

## Energiemanagement

Seit Version 1.3.0 ist das Energiemanagement neu: Es lernt Haus und Räume kennen, schaut 48 Stunden voraus und plant
je Stunde, wie die Zonen verschoben werden sollten – damit der Vorlauf möglichst niedrig bleibt, die Wärmepumpe
seltener taktet und abtaut und nachts nur dann abgesenkt wird, wenn es sich lohnt. **Seit Version 1.5.0 kann es
die Zonen auch steuern** (Abschnitt „Zonen steuern“): Funktion für Funktion, jede mit eigener Stufe.
Die Heizkörperthermostate stellt seit 1.4.0 die Raumregelung (eigener Abschnitt).
Der Planer der Versionen 1.1 und 1.2 (Warmwasser und Zonen nach Strompreis und PV) ist entfallen; PV-Überschuss
kommt später neu auf dieser Grundlage.

**Stufen** (Einstellungen → Energiemanagement → Stufe):

- **Aus** (Voreinstellung): Es wird nicht geplant. Takte, Abtauungen und Raumwerte zählt und lernt die App trotzdem.
- **Vorschau:** Das Energiemanagement plant alle 15 Minuten neu und zeigt, was es tun würde: auf der Übersicht, auf
  der Seite **Plan** und im Protokoll („würde“). Es sendet nichts, auch nicht im Live-Betrieb.
- **Steuern:** Was die Funktionen auf „Steuern“ planen, geht an die Wärmepumpe; Funktionen auf „Vorschau“ zeigen
  weiter nur, was sie tun würden. Wirksam ist je Funktion die kleinere der beiden Stufen. Im Demo-Betrieb und ohne
  die Anbindung myVAILLANT wird nichts gesendet.

**Was die App beobachtet und lernt:**

- **Takten und Abtauen:** Aus der Leistung der Wärmepumpe (myVAILLANT meldet sie etwa jede Minute) zählt die App
  Starts, Laufzeiten und Pausen und erkennt Abtauungen an ihrem Muster: Im Lauf fällt die Leistung für einige
  Minuten auf etwa 370–520 W und springt danach für rund 15 Minuten auf 1,1–2,3 kW. Nach einem Start holt sie die
  letzten zehn Tage aus dem Verlauf von Home Assistant nach. Die Karte **Takten und Abtauen** auf der Übersicht zeigt
  Starts, Lauf und Pause (Median) und Abtauungen von heute, der letzten Tage und je Außentemperatur.
- **Frostschutz der Wärmepumpe:** Ist es länger als 4 Stunden kälter als 4 °C, heizt die Vaillant-Regelung von sich
  aus (auch in „Aus“ und in der Absenkung). Die App erkennt das, zeigt „Frostschutz aktiv“ und plant dann keine
  langen Pausen; sie stellt sich nie dagegen.
- **Haus:** Aus der Langzeitstatistik von Home Assistant (stündliche Werte, oft über ein Jahr; die App liest sie
  nur) lernt sie, wie viel Wärme das Haus je Grad Temperaturunterschied verliert (W/K), wie viel Wärme Personen,
  Geräte und Sonne bringen, wie träge das Haus ist und – aus echten Wärme- und Stromwerten – die Arbeitszahl je
  Außentemperatur. Dafür braucht sie die Wärmesensoren der myVAILLANT-Integration (Heizen und Warmwasser), die
  Leistung und die Außentemperatur. Bis zehn Heiztage bekannt sind, gilt die Heizlast aus dem statischen Abgleich als
  Startwert. Daraus schätzt sie Wärme- und Strombedarf je Stunde für die nächsten 48 Stunden. Seit 1.5.0 zählen für
  die Arbeitszahl Wärme und Strom nur über dieselben Stunden (ein Tag ab 20 solchen Stunden; Lücken in den
  Cloud-Werten drückten sie vorher zu tief), und über die gelernten Außentemperaturen hinaus geht die Gerade nur 2 K
  weiter und bleibt dann gleich. Eine Verzögerung des Hauses, die genau am Ende der Suche (8 h) liegt, gilt als nicht
  gelernt.
- **Räume:** Alle 5 Minuten speichert die App je Raum Temperatur, Feuchte, Ventilstellung und Thermostat-Soll
  (21 Tage). Daraus lernt sie, wie schnell der Raum ohne Heizung abkühlt, wie schnell er aufheizt, wie lange es nach
  einem Start der Wärmepumpe dauert, bis er nicht mehr fällt, und wie weit das Ventil für das Soll offen steht. Bis
  genug Phasen gesehen sind, steht „lernt noch“ da; die Abkühlung rechnet bis dahin mit einer Zeitkonstante von 150 h
  (seit 1.5.0; vorher 30 h, viel zu schnell). Ist eine Zone ausgeschaltet, zählen ihre Räume als „ohne Heizung“.
- **Abtaurate:** je Außentemperatur, seit 1.5.0 zählt jede Laufstunde bei der Außentemperatur dieser Stunde (vorher
  beim Start des Laufs) und mit der Luftfeuchte ihres Wetters (feuchte Stunden wiegen mehr), so dass die Feuchte der
  Prognose nur einmal zählt.
- **Minimalleistung (1.5.0):** Steigt die Puffertemperatur über den Vorlauf-Soll, obwohl der gleich bleibt, kann die
  Wärmepumpe nicht weiter herunterregeln und überhöht, um ihre Mindestlaufzeit zu schaffen – an milden Tagen bei jedem
  Lauf zu sehen. Die Leistung in solchen Phasen (ohne Abtauung und Warmwasser) ist ihre kleinste; die App zeigt sie
  je Außentemperatur in der Karte Zonensteuerung.
- **Fenster (würde):** Erkennt die App ein offenes Fenster (Regeln unter „Raumregelung → Fenster offen“), meldet sie
  es im Protokoll (Kategorie „Räume“) mit dem Signal, an dem sie es erkannt hat. So lässt sich prüfen, ob die
  Erkennung zu echtem Lüften passt. Räume, die in der Raumregelung auf „Vorschau“ oder „Steuern“ stehen, meldet die
  Raumregelung selbst.
- **Versorgung (würde):** Steht das Ventil eines Raums seit 30 Minuten zu mindestens 90 % offen und ist der Raum
  0,3 K unter seinem Soll, gilt er als unterversorgt; hält er sein Soll mit wenig offenem Ventil, als überversorgt.
  Für die Heizkörper-Zone leitet die App daraus ab, ob der Vorlauf höher oder tiefer sein dürfte. Stehen Räume der
  Zone in der Raumregelung auf „Vorschau“ oder „Steuern“, heißt es „höher“ erst, wenn das Umverteilen nicht reicht.
- **Zone-1-Logik (würde):** wie die bekannte Automation „Fußbodenheizung aus, solange die Wärmepumpe steht“ – nicht
  bei Frostschutz und nicht, wenn ein Raum der Zone unter sein Komfortband fällt. Die Karte zeigt, wie oft das heute
  zum tatsächlichen Schalten gepasst hätte.

**Was der Plan berücksichtigt** (je Zone und Stunde, mit Grund):

- **Vorlauf:** die Verschiebung der Heizkörper-Zone nach dem Engpassraum (Abschnitt „Zonen steuern“).
- **Vorausschauend:** Wird es in der Zeit, die die Zone braucht (Fußbodenheizung etwa 3 h, Heizkörper 1 h), deutlich
  kälter, etwas früher mehr; wird es milder oder kommt Sonne, etwas früher weniger.
- **Warmer Tag:** Liegt das Tagesmittel über der Heizgrenze, wird nicht geheizt.
- **Abtaurisiko:** je Stunde aus Temperatur und Luftfeuchte der Prognose, gelernt aus den gezählten Abtauungen. In
  riskanten Stunden etwas weniger Last (höchstens 3 Stunden am Stück), davor speichert die Fußbodenheizung etwas
  mehr – im erlaubten Verlust.
- **Nachtabsenkung:** im Nachtfenster (seit 1.5.0 Voreinstellung 5:30–10:00 Uhr) Zonen aus oder Minimalleistung
  (Abschnitt „Zonen steuern“); die Räume behalten ihr Soll.
- **Blockbetrieb:** In milden Stunden (Voreinstellung ab 5 °C) lange Läufe und lange Pausen; Zone 1 nimmt im Lauf
  Wärme auf, Zone 2 darf in Pausen etwas tiefer liegen.

**Seiten:**

- **Plan:** die nächsten 48 Stunden als Tabelle – „Nur Änderungen“ oder „Stündlich“ – mit Verschiebung, Soll und
  erwartetem Vorlauf je Zone, Soll je Raum, Grund und Prognose (Außentemperatur, Abtaurisiko, Wärme- und Strombedarf).
  **Rückblick 24 h** zeigt je Stunde, was geplant war und was gemessen wurde; **CSV herunterladen** speichert beides.
- **Räume:** je Raum Temperatur, Soll mit Komfortband, Thermostat-Soll, Ventil, Feuchte, Versorgung, Fenster, wie
  schnell der Raum gerade abkühlt und wie lange er ohne Heizung bis zur unteren Komfortgrenze bräuchte, dazu der
  Lernstand. Unter **Bearbeiten** tragen Sie ein: Raum-Soll und bis zu vier Zeitfenster (jeden Tag gleich),
  Komfortband, erlaubten Verlust nachts und gegen Abtauen, Notfallwert, ob der Raum gedrosselt werden darf,
  Heizkörperthermostat, Ventilstellung (Aqara W600: „position“, Eurotronic Comet: „pi_heating_demand“) und
  Luftfeuchte sowie die Werte des statischen Abgleichs (Heizlast, Auslegungs-Soll, Heizflächen mit Normleistung,
  Durchfluss und Voreinstellung). Die Werte des Gutachtens sind nur Startwerte. Räume ohne Temperatursensor (etwa ein
  Bad) wählen Sie in den Einstellungen mit ihrem Thermostat oder dessen Temperatur-Sensor (etwa
  `sensor.…_local_temperature`); der Wert ist am Heizkörper gemessen und zählt nicht im Mittelwert der Zone.

**Einrichten** unter Einstellungen → Energiemanagement, gespeichert mit **Speichern**:

- **Wetterprognose:** Open-Meteo mit den Modellen des Deutschen Wetterdienstes (Breite und Länge, die Taste übernimmt
  den Standort aus Home Assistant) und eine Wetter-Entität als Rückfall. Die App fragt `api.open-meteo.com`
  höchstens einmal pro Stunde mit auf etwa 1 km gerundetem Standort – ihre einzige Verbindung ins Internet. Die
  Prognose wird am Außenfühler der Wärmepumpe geeicht; nach einigen Tagen zeigt die App, wie gut die Prognose für
  den nächsten Tag stimmte.
- **Lernen:** die Wärmesensoren Heizen und Warmwasser, wenn vorhanden die Puffertemperatur, und aus dem statischen
  Abgleich Normaußentemperatur und Heizlast des Hauses.
- **Zonen:** je Zone die Art (Fußbodenheizung oder Heizkörper), Auslegung Vor- und Rücklauf, die Vorlauf-Sensoren
  (Ist und Soll, bei myVAILLANT „… circuit_0/1 … flow temperature“), die Heizkurve (optional, nur gelesen, „…
  circuit_0/1 heating curve“) und wie weit das Soll verschoben werden darf (Vorgabe: Fußbodenheizung −1 … +2 K,
  Heizkörper −2 … +2 K).
- **Nachtabsenkung und Abtauen:** Nachtfenster und erlaubte Verluste (Vorgabe für alle Räume). Wer bisher das
  Fenster 0–10 Uhr der Voreinstellung hatte, bekommt mit 1.5.0 einmalig 5:30–10:00.
- **Takten und Grenzen:** Ziel Starts je Tag, Blockbetrieb ab, Heizgrenze, Schwelle der Zone-1-Logik, Schritt und
  Abstand des Quick-Veto sowie die Tagesgrenze für Befehle an die Vaillant-Cloud.

**Sensoren** (Export-Gruppe „Energiemanagement“): `sensor.vaillant_eebus_hems_status` (Aus, Vorschau),
`sensor.vaillant_eebus_hems_next_change` (nächste geplante Änderung mit Zeit und Grund),
`sensor.vaillant_eebus_hems_plan` (Zahl der geplanten Änderungen in 48 Stunden, die Liste als Attribut),
`sensor.vaillant_eebus_starts_today`, `sensor.vaillant_eebus_defrosts_today` und
`binary_sensor.vaillant_eebus_frost_protection`. Immer gemeldet (unabhängig von den Export-Gruppen):
`sensor.vaillant_eebus_zones_lifesign`, das Lebenszeichen der Zonensteuerung (Attribute: ausgeschaltete Zonen,
laufende Quick-Vetos, Befehle heute).

### Zonen steuern

Unter Einstellungen → Energiemanagement → **Zonensteuerung** hat jede Funktion ihre eigene Stufe (Aus, Vorschau,
Steuern):

- **Zone 1 Aus/Auto:** ersetzt die eigene Automation „Fußbodenheizung aus, solange die Wärmepumpe steht“. Liegt die
  Leistung der Wärmepumpe länger als eine Minute unter der Schwelle (Vorgabe 30 W), schaltet die App die Zone mit
  Fußbodenheizung auf **Aus**; steigt sie darüber, sofort wieder auf **Auto**. Nicht bei Frostschutz, nicht wenn ein
  Raum der Zone unter sein Komfortband fällt, nicht an der Tagesgrenze, und Aus frühestens 5 Minuten nach dem letzten
  Auto. **Vor „Steuern“ die eigene Automation in Home Assistant abschalten.** Schaltet sie weiter (zweimal am Tag im
  Takt der Wärmepumpe von außen geschaltet), hält die Funktion an und warnt; nach dem Abschalten die Stufe neu
  wählen.
- **Vorlauf und Vorausschau:** die Verschiebung der Zonen aus Ventilstellungen, Vorausschau und warmen Tagen als
  **Quick-Veto** (Programmwert ± Verschiebung). Das Quick-Veto gilt bis zur nächsten geplanten Änderung, mindestens
  1 und höchstens 4 Stunden, wird 10 Minuten vor dem Ende erneuert und endet sofort, wenn keine Verschiebung mehr
  geplant ist. Tiefer geht es frühestens nach dem eingestellten Abstand (Vorgabe 30 min), höher sofort. Nur im
  Auto-Betrieb und nur mit bekanntem Programmwert (in einer Absenkung des Zeitprogramms sendet die App nichts).
  **Vorlauf der Heizkörper-Zone (seit 1.5.0):**
  - **Engpassraum:** der Raum mit dem im Mittel am weitesten offenen Ventil der letzten 7 Tage – gezählt nur, wenn die
    Wärmepumpe Wärme liefert, ohne Fenster (und 30 min danach), Sollwechsel, Aufheizen nach einer Soll-Erhöhung,
    Testlauf und Durchlauf. Bis 24 solche Stunden gesammelt sind, gilt die Momentaufnahme der letzten halben Stunde.
    Ein am Thermostat gemessener Raum (Bad) zählt nur mit seinem Ventil. Die Seite „Räume“ zeigt ihn mit Mittel,
    Anteil „unterversorgt“ und dem Wert jetzt.
  - **Höher, sofort:** Ist ein Raum unterversorgt und hilft Umverteilen nicht (mehr), springt eine Verschiebung unter
    0 sofort auf 0; ab 0 geht es alle 30 min um 0,5 K höher, bis zur Bandgrenze.
  - **Tiefer, langsam:** 0,5 K, wenn die Wärmepumpe Wärme liefert, kein Raum unterversorgt ist, kein Ventil im Mittel
    der letzten Stunde zu 90 % offen steht und das Ventil des Engpassraums zwischen 10 und 70 % liegt (es zieht Wärme
    und hat Reserve; ist es zu, sagt das nichts). Höchstens alle 2 Stunden, nach einem Sprung zurück 6 Stunden gar
    nicht, und nur, solange der Vorlauf-Soll der Zone mindestens 1 K über dem der Fußbodenheizung bleibt (sonst
    bestimmt die den Vorlauf) und deren Raum nicht unter seinem Komfortband ist. Zwischen 70 und 90 % hält die App.
  - **Heizkurven-Empfehlung:** Liegt die Verschiebung über 7 Tage (je Tag mindestens 6 Stunden mit Wärme, mindestens
    5 Tage) im Mittel bei mindestens ±0,5 K, empfiehlt die App eine steilere oder flachere Heizkurve (wenn die
    Verschiebung an kalten und milden Stunden deutlich verschieden ist) oder eine andere Wunschtemperatur im
    Zeitprogramm. Die App ändert die Heizkurve nie; nach einer Änderung der Kurve beginnt die Rechnung neu.
  - **Voreinstellung am Maximum:** im Raum-Editor je Heizfläche ankreuzbar. Dann nennt der Hinweis bei oft offenen
    Ventilen die Absperrungen am Heizkörper (H-Block, Rücklaufverschraubung), den Vorlauf oder eine zu kleine
    Heizfläche statt „Voreinstellung größer“.
- **Nachtabsenkung (seit 1.5.0):** nur im Nachtfenster (Vorgabe 5:30–10:00). Maßgeblich ist das Mittel der Prognose
  über das Fenster:
  - **über 3 °C** (und ohne Frostschutz im Fenster): die Zonen gehen auf **Aus**. Ist Vereisung möglich (Abtaurisiko
    der Stunde mindestens „gering“), schaltet die App erst **direkt nach einer Abtauung** ab, damit die Wärmepumpe
    nicht vereist stillsteht; kommt bis 7:00 keine, bleibt es bei der Minimalleistung. Zurück auf **Auto** um das Ende
    des Fensters, früher, wenn ein Raum der Zone seinen erlaubten Verlust (Vorgabe 0,5 K, je Raum einstellbar) bis auf
    0,1 K erreicht, ein Raum 65 % Luftfeuchte erreicht oder der Frostschutz beginnt – dann Minimalleistung bis zum Ende.
  - **3 °C und darunter:** **Minimalleistung**, ohne dass die Wärmepumpe abschaltet. Die Heizkörper-Zone beginnt mit
    der gemerkten Verschiebung (zuerst −1 K) und geht alle 20 Minuten um 0,5 K tiefer, bis der Puffer über den
    Vorlauf-Soll steigt (Wärmepumpe am Minimum); dann einen Schritt zurück und halten. Bleibt die Wärmepumpe trotzdem
    stehen, 1 K höher. Die Zone mit Fußbodenheizung bekommt −1 K, solange der Frostschutz sie an hält. Die App merkt
    sich, wo es hielt, und beginnt in der nächsten Nacht dort.
  - Aufgeheizt wird ab dem Ende des Fensters (kein Vorheizen); der Vorlauf (F2) hält derweil. Keine Absenkung in einer
    Zone mit einem Raum ab 65 % Luftfeuchte oder mit vorgemerktem oder laufendem Testlauf. Die Raumthermostate behalten
    ihr Soll.
  - **Vorher umstellen:** Zeitprogramm der Zonen in myVAILLANT auf durchgehend Komfort (ohne Absenkfenster), Zonen auf
    **Auto** (in „Ein“ gibt es kein Quick-Veto), die Automation „Zonen auf Auto, wenn die App schweigt“ anlegen.
- Abtaurisiko und Blockbetrieb (geplant für 1.6.0) stehen weiter nur im Plan.

**Änderungen von Hand haben Vorrang:** Ändert jemand Soll, Betriebsart oder Quick-Veto einer Zone – in myVAILLANT,
am Regler oder in dieser App (auch „Verlängern“) –, lässt der Planer die Zone bis zur nächsten geplanten Änderung in
Ruhe, höchstens 3 Stunden (Protokoll und Karte zeigen, bis wann). Das Eingreifen des Frostschutzes zählt nicht als
Änderung von Hand.

**Tagesgrenze für Befehle an myVAILLANT:** Die App zählt jeden Befehl an die Vaillant-Cloud (aus der Oberfläche, vom
Planer, von Zone 1 Aus/Auto) je Tag. Ab 80 % der Grenze (Vorgabe 100) warnt sie; an der Grenze sendet der Planer an
diesem Tag nur noch, was die Zonen zurückgibt (Quick-Veto beenden, Zone auf Auto). Änderungen von Hand gehen immer.
In der Vorschau zählt die App, wie viele Befehle es gewesen wären.

**Zurückgeben:** Geht das Energiemanagement oder eine Funktion von „Steuern“ weg, wechselt die App in den
Demo-Betrieb oder stoppt sie, beendet sie nur ihre eigenen Quick-Vetos und setzt nur die Zonen, die sie selbst
ausgeschaltet hat, zurück auf Auto. Stürzt die App ab, endet ein Quick-Veto von selbst (höchstens 4 h). Für eine
ausgeschaltete Zone gibt es die Automation **„Zonen auf Auto, wenn die App schweigt“** (Karte Zonensteuerung →
Vorlage anzeigen → kopieren; in Home Assistant unter Automationen → Automation erstellen → ⋮ → „In YAML bearbeiten“
einfügen): Kommt 15 Minuten kein Lebenszeichen, setzt sie die Zonen, die die App ausgeschaltet hat, auf Auto.

**Eigene Übersteuerung verlängern:** Läuft an einer Zone eine zeitweise Übersteuerung, lässt sie sich auf der
Übersicht mit gleichem Soll für 1 bis 12 Stunden ab jetzt verlängern.

**Was das Energiemanagement nie tut:** Heizkurve, Zeitprogramm, Warmwasser oder Leistungsdeckel ändern (die Heizkurve
liest es nur für die Empfehlung). Es sendet nur
Quick-Veto, das Ende eines Quick-Veto und die Betriebsarten Aus und Auto einer Zone – über die Schreibsperre, die das
prüft; ein Test der App stellt sicher, dass nur die Zonensteuerung sie aufruft. Die Thermostate stellt allein die
Raumregelung über ihren eigenen Teil der Schreibsperre.

**Wechsel von 1.2:** Hatte der alte Planer beim Update noch eine Verschiebung offen, steht ein Hinweis im Protokoll;
bitte die Solltemperatur prüfen (ein Quick-Veto endet von selbst). Seine Einstellungen zu Tarif, PV und Akku werden
nicht mehr gebraucht.

## Raumregelung

Seit Version 1.4.0 kann die App die **Heizkörperthermostate** der Räume stellen (Zigbee2MQTT, z. B. Aqara W600 und
Eurotronic Comet). Jedes Thermostat regelt sein Ventil weiter selbst; die App gibt ihm nur den Sollwert vor, so dass
der Raum – gemessen am Raumsensor – sein Raum-Soll hält. Fällt die App aus, regeln die Thermostate einfach weiter.
An die Wärmepumpe sendet die Raumregelung nichts.

**Stufen** (Seite Räume → Karte „Raumregelung“ für alle Räume, dazu je Raum; es gilt die kleinere):

- **Aus:** keine Raumregelung.
- **Beobachten** (nach dem Update, wenn das Energiemanagement auf Vorschau stand): wie in 1.3 – lernen, Fenster und
  Versorgung als „würde“.
- **Vorschau:** Die App rechnet alle 5 Minuten den Sollwert jedes Thermostats und schreibt ins Protokoll, was sie
  senden würde („Vorschau, würde senden“), mit den Grenzen der Ventilschonung wie beim echten Senden.
- **Steuern:** nur im Live-Betrieb, mit Bestätigung, und je Raum erst, wenn die App vom Thermostat **„zu“ und
  „ganz offen“** kennt – aus einem **schnellen Durchlauf** (seit 1.4.3, etwa 30 Minuten, tagsüber per Knopf), einem
  Testlauf oder aus dem Alltag (siehe „Regelverhalten“). Die Seite zeigt beim Raum, was noch fehlt. Empfehlung: erst
  einige Tage Vorschau (die Regelgüte zeigt den Vergleich), dann ein einzelner Raum (etwa das Bad), dann die übrigen.

Die Seite **Räume** aktualisiert sich live, sobald sich ein Thermostat, ein Ventil, ein Raumsensor oder das
Fühlerpaar ändert oder die App etwas entscheidet oder sendet (sonst jede Minute).

**Was die App je Raum tut:**

- **Sollwert:** Raum-Soll aus dem Zeitplan (vor einem wärmeren Zeitfenster heizt sie rechtzeitig vor, höchstens
  3 Stunden; solange das Aufheizen nicht gelernt ist, 1 Stunde vorher) plus eine **Korrektur**: Weicht der
  Raumsensor 30 Minuten lang um mindestens 0,2 K ab, ändert sie die Korrektur um 0,5 K (höchstens ± 5 K,
  einstellbar). Sie erhöht nicht, wenn das Ventil schon zu mindestens 90 % offen ist (dann fehlt Wasser oder Vorlauf)
  oder kein warmes Wasser kommt, und senkt nicht, wenn das Ventil höchstens 5 % offen ist. Kennt sie das
  Regelverhalten des Thermostats („zu“ und „ganz offen“), rechnet sie genauer: welche Ventilöffnung der Raum braucht
  (seit 1.4.3: der gelernte **Bedarf** bei der Außentemperatur plus die **Verstärkung** des Raums je K zu kalt, minus
  ein Drittel davon je K/h, um das der Raum gerade steigt; siehe „Regelung lernt mit“) und welche kleinste Abweichung
  (Sollwert über der Temperatur am Thermostat) diese Öffnung im jetzigen Zustand erreicht hat – steigend oder fallend,
  bei so warmem Wasser wie jetzt. Bei einem PI-Thermostat (dessen Ventil bei
  gleichem Sollwert weiterwandert, etwa der Comet) wartet sie nach jeder Änderung die gelernte Einschwingzeit ab
  (mindestens 30 Minuten) und legt nicht nach, solange das Ventil noch in die richtige Richtung läuft.
- **Raum ohne eigenen Sensor** (Bad mit Comet): Raum-Soll + **fester Ausgleich** (Voreinstellung 0 K). Ist es gefühlt
  zu kühl, den Ausgleich z. B. auf +1 K stellen.
- **Fenster offen:** Thermostat sofort auf den kleinsten Wert (Aqara 5 °C, Comet 8 °C), nach dem Schließen sofort
  zurück – seit 1.4.3 in dem Augenblick, in dem die App das Fenster erkennt, nicht erst im 5-Minuten-Takt. Die eigene
  Fenstererkennung der Thermostate sollte aus sein. So erkennt die App ein offenes Fenster (seit 1.4.3; die Raumsensoren
  melden oft nur bei einer Änderung, deshalb vergleicht sie jede Meldung mit der letzten, auch wenn die älter ist):
  - **Luftfeuchte** (Hauptsignal): Die absolute Feuchte fällt gegenüber der letzten Meldung (höchstens 90 Minuten alt)
    um eine Schwelle, schnell genug (mindestens 1 g/m³ je Stunde) und ohne dass die Temperatur steigt. Die Schwelle
    richtet sich nach dem Wetter: 0,2 × (Feuchte innen − Feuchte außen), 0,5 bis 1,0 g/m³; ist es draußen mild und
    feucht (Unterschied unter 1,5 g/m³), zählt die Feuchte nicht. Die Seite zeigt die Schwelle je Raum.
  - **Temperatur:** −0,5 K gegenüber einer Meldung, die höchstens 15 Minuten alt ist, oder −0,8 K bis 30 Minuten,
    wenn der Raum mindestens 8 K wärmer ist als draußen.
  - **Fühler am Thermostat:** fällt um 1 K gegenüber seinem höchsten Wert der letzten 8 Minuten, und der Raumsensor
    ist seitdem nicht gestiegen (ohne Raumsensor 1,5 K). Nicht in den 30 Minuten nach einem Befehl der App oder dem
    Schließen des Ventils, weil der Fühler am Heizkörper dann von selbst fällt.
  - **Zu** gilt das Fenster frühestens 3 Minuten danach, wenn die Feuchte 0,3 g/m³, der Raum 0,2 K oder das
    Thermostat 0,5 K über dem tiefsten Wert liegt, spätestens nach 30 Minuten.
  - **Fensterkontakt** (optional je Raum, Bearbeiten → Fensterkontakt, ein `binary_sensor`): Mit Kontakt gilt nur er,
    sofort beim Öffnen und Schließen. Meldet er sich 10 Minuten nicht, gelten wieder die Regeln (Hinweis beim Raum).

  Das Protokoll nennt das Signal (etwa „Feuchte −1,3 g/m³ in 34 min“, „Thermostat −1,0 K“, „Fensterkontakt“).
- **Handänderung** am Thermostat setzt die App beim nächsten Durchlauf zurück. Wärmer oder kühler stellen Sie in
  der App (Raum-Soll).
- **Zu warm:** Steigt der Raum über Soll + Komfortband + 0,5 K, stellt sie das Thermostat sofort auf Soll − 0,5 K
  und senkt die Korrektur einmal um 0,5 K (steht im Protokoll).
- **Raumsensor fällt aus:** Die Korrektur bleibt stehen, die Fenstererkennung ruht.

**Zone der Heizkörper:** Die App erkennt aus der Leistung der Wärmepumpe und der Puffertemperatur, ob warmes Wasser
ankommt (von der Wärmepumpe oder aus dem Puffer, mit Restzeit) – nur dann darf eine Korrektur steigen. Fehlen
Vorlauf und Puffer, gilt das Wärmeangebot als unklar. Ist ein Raum
trotz offenem Ventil zu kalt, **verteilt sie erst um**: gut versorgte Räume (in derselben Stufe, „Darf gedrosselt
werden“ an) gehen bis zur unteren Komfortgrenze herunter, höchstens 2 Stunden. Reicht das nach 45 Minuten nicht,
zeigt die Seite **„Vorlauf höher nötig“**. Die App hebt den Vorlauf, wenn „Vorlauf und Vorausschau“ auf Steuern
steht; bis dahin können Sie ihn in myVAILLANT von Hand anheben.

**Ventile schonen** (je Raum einstellbar, von der Schreibsperre erzwungen): kein Befehl unter der **Totzone**
(0,5 K), höchstens alle **15 Minuten** und höchstens **24 Befehle** je Thermostat und Tag. Testlauf und schneller
Durchlauf haben ein eigenes Budget (16 je Nacht, 20 je Durchlauf und 40 am Tag). Fenster, Handänderung,
Sicherheit, Rückweg nach Testlauf oder Durchlauf und Rückfall gehen sofort hinaus; über der Höchstzahl nur noch Fenster,
Sicherheit, Rückweg und Rückfall. Ein Wert, der noch zum Thermostat unterwegs ist, gilt schon als sein Sollwert. Jeder
Sollwert gilt erst als bestätigt, wenn das Thermostat ihn meldet; meldet es ihn zweimal nicht, sendet die App ihm eine
Stunde lang nur Fenster, Sicherheit, Rückweg und Rückfall. Die Seite zeigt Befehle und Ventilbewegungen je Tag.

**Regelverhalten** (seit 1.4.2): Die App lernt je Thermostat ein **Kennfeld** – welche Ventilstellung sich nach
dem Einschwingen bei welcher Abweichung einstellt (Sollwert minus Temperatur am Thermostat, in 0,5-K-Schritten),
getrennt nach steigender und fallender Abweichung und danach, wie warm das Wasser am Heizkörper ist (Vorlauf unter 30,
30 bis 35, über 35 °C; vom Fühlerpaar, sonst vom Vorlauf der Zone oder vom Puffer). Neuere Werte zählen mehr
(Halbwertszeit 14 Tage). Sie lernt

- im **Alltag** ab „Beobachten“, ohne Fühlerpaar: Stehen Sollwert und Abweichung 30 Minuten ruhig, kommt warmes
  Wasser an und ist die Ventilstellung aktuell, wird daraus ein Punkt (Median der letzten 10 Minuten);
- in **Testläufen** aus jeder eingeschwungenen Stufe;
- im **schnellen Durchlauf** (seit 1.4.3) aus jedem Schritt: wie das Ventil sofort reagiert. Diese Punkte füllen nur
  Lücken – wo es Punkte aus Alltag oder Testlauf gibt, zählen nur diese, weil sie eingeschwungen sind (wichtig beim
  Comet, dessen Ventil nachläuft). Für „zu“ und „ganz offen“ und damit für „Steuern“ reichen sie.

Dazu kommen aus jeder Änderung des Sollwerts (durch die App, von Hand oder durch einen Zeitplan) die **Sprungantwort**
(nach wie vielen Minuten sich das Ventil bewegt, wie schnell, wann es steht), die **Reglerart** (auf/zu, P oder PI)
und der **kleinste wirksame Schritt**. „Ganz offen“ heißt mindestens 85 % – oder mindestens 50 %, wenn 1 K mehr keine
5 Prozentpunkte mehr bringt (der Comet kommt nicht über etwa 88 %). „Zu“ heißt höchstens 5 %. Beim Raum unter
„Regelverhalten“ stehen das Kennfeld (steigend und fallend nebeneinander, mit der Zahl der Punkte), was fehlt und
woher die Punkte stammen. Die Testläufe der Version 1.4.1 übernimmt die App als erste Punkte.

**Ventilstellung der Aqara W600** (seit 1.4.2): Der W600 meldet seine Ventilstellung nur selten, mitunter einen Tag
lang nicht. Die App fragt sie deshalb selbst ab, über die MQTT-Integration von Home Assistant: eine Nachricht an
Zigbee2MQTT, `zigbee2mqtt/<Gerätename>/get` mit `{"running_state": ""}` – genau diese, nichts anderes, und nur für
Thermostate der Räume. Sie liest nur und verstellt nichts, deshalb auch im Demo-Betrieb; bei ausgeschalteter
Raumregelung nicht. Wann: alle 5 Minuten (kurz bevor die App rechnet), 1 und 3 Minuten nach jedem neuen Sollwert und
im Testlauf 10 Minuten lang jede Minute, danach alle 2 Minuten – höchstens alle 30 Sekunden und höchstens 720-mal am
Tag je Thermostat. Die Abfragen zählen nicht zu den Befehlen und stehen nicht einzeln im Protokoll; geht eine nicht
(etwa ohne MQTT-Integration), steht das einmal als Problem darin. Die Seite zeigt je W600 die letzte Abfrage, die
Zahl heute und das Topic.

- **Gerätename:** der Name des Geräts in Home Assistant, so wie Zigbee2MQTT es angelegt hat (nicht ein Name, den Sie
  in Home Assistant vergeben haben). Heißt das Gerät in Zigbee2MQTT anders, den Namen beim Raum eintragen (Bearbeiten
  → „Gerätename in Zigbee2MQTT“). Namen mit einem Teil „get“ oder „set“, mit `+` oder `#` nimmt die App nicht.
- **Basis-Topic:** `zigbee2mqtt`, änderbar auf der Karte „Raumregelung“ unter „Ventilstellung der Aqara W600
  abfragen“.
- **Zuletzt gesehen (empfohlen):** In Zigbee2MQTT unter Einstellungen → Erweitert „last_seen“ auf ISO_8601 stellen
  und in Home Assistant die Entitäten „… Last seen“ der Thermostate aktivieren. Dann erkennt die App, ob ein
  Thermostat auf die Abfrage antwortet; eine unveränderte Stellung gilt dann als bestätigt. Ohne „zuletzt gesehen“
  gilt eine Abfrage, die Home Assistant angenommen hat, als Bestätigung. Der Eurotronic Comet meldet seine Stellung
  selbst, meist aber nur kurz nach einem neuen Sollwert; Zigbee2MQTT kann sie bei ihm nicht abfragen
  (`pi_heating_demand` hat dort kein „get“). Die Seite zeigt deshalb beim Comet (und jedem Thermostat ohne
  Abfrage), wann die Stellung gemeldet wurde, ab 15 Minuten mit ihrem Alter. Hängt das Fühlerpaar am Heizkörper und
  kommt warmes Wasser an, steht dort die **Spreizung** (Vorlauf minus Rücklauf) und die Öffnung, die ein Testlauf bei
  dieser Spreizung gemessen hat; weicht die gemeldete Stellung um 30 Prozentpunkte oder mehr ab und ist sie älter
  als 30 Minuten, nennt die Seite sie „vermutlich alt“.

**Schneller Durchlauf** (seit 1.4.3; je Thermostat mit Ventilstellung, der schnellste Weg zu „zu“ und „ganz offen“):
Ohne Fühlerpaar und ohne warmes Wasser, zu jeder Tageszeit, etwa 25–35 Minuten.

1. Beim Raum **„Schnellen Durchlauf starten“** (nur im Live-Betrieb, Raumregelung nicht „Aus“, mit Bestätigung) –
   oder auf der Karte „Raumregelung“ **„Alle Räume nacheinander“**. Nicht bei offenem Fenster, nicht erreichbarem
   Thermostat, Raum mehr als 1 K über Soll oder wenn schon ein Testlauf oder Durchlauf läuft (ein vorgemerkter
   Nacht-Testlauf stört nicht; er wartet, bis der Durchlauf fertig ist).
2. Die App merkt sich den Sollwert, stellt das Thermostat auf seine eigene Temperatur und dann **alle 2 Minuten** um
   0,5 K höher – hat sich das Ventil um weniger als 10 Prozentpunkte bewegt, um 1 K –, bis es **ganz offen** ist
   (höchstens 6 K über dem Start oder bis zur Grenze des Thermostats), dann ebenso tiefer, bis es **zu** ist
   (höchstens 2 K unter die Temperatur am Thermostat), und zurück auf den Wert von vorher. Die Ventilstellung liest
   sie jeweils nach dem Schritt (Comet meldet sie selbst, den W600 fragt sie eine Minute danach ab); kommt nach 4
   Minuten keine frische, bricht sie ab.
3. Höchstens **20 Befehle** je Durchlauf (sie zählen nicht zu den 24 am Tag); sind sie verbraucht, geht es mit dem
   Gelernten zurück.
4. Abbruch (sofort zurück auf den Wert von vorher): Fenster offen, Raum über Soll + 1 K, Thermostat nicht erreichbar,
   Sollwert am Thermostat von Hand geändert, Knopf „Abbrechen“.

Jeder Schritt wird ein Punkt im Regelverhalten und eine Sprungantwort. Beim Raum zeigt die Seite den Fortschritt und
die **Kurve des letzten Durchlaufs** (Ventil über der Abweichung, steigend und fallend); im Protokoll stehen Start,
Ende und eine Zusammenfassung, und sobald „zu“ und „ganz offen“ bekannt sind, dass „Steuern“ möglich ist.

**Testlauf** (je Thermostat, mit dem Fühlerpaar; misst zusätzlich Vor- und Rücklauf und die Wärmeabgabe):

1. Das **Fühlerpaar** an Vor- und Rücklauf des Heizkörpers befestigen (Anlegefühler, gut anliegend, mit Isolierung
   darüber; gut ist eine Auflösung von 0,1–0,2 K). Der **Vorlauf** ist das Rohr am Thermostatventil. Unter
   „Fühlerpaar“ den Vorlauf- und den Rücklauf-Fühler wählen und den Raum eintragen. Fühler, die nur bei einer
   Änderung melden, sind in Ordnung: Ein Wert gilt bis **2 Stunden** nach seiner Meldung. Die Seite zeigt je Fühler,
   wann er zuletzt gemeldet hat, die längste Meldepause und die kleinste Änderung. Ist der Rücklauf bei warmem Wasser
   am offenen Ventil 15 Minuten lang wärmer als der Vorlauf, steht dort „vertauscht?“ mit dem Knopf **„Vorlauf und
   Rücklauf tauschen“**; so lange startet kein Testlauf.
2. Am Raum **Testlauf für heute Nacht vormerken** (nur im Live-Betrieb).
3. Ab 22 Uhr startet er, sobald der Raum zwischen 0,5 K unter und 0,2 K über seinem Soll liegt, kein Fenster offen
   ist und am Heizkörper warmes Wasser ankommt – spätestens um 1 Uhr, sonst in der nächsten Nacht (höchstens drei;
   eine Nacht, deren Startzeit beim Vormerken schon vorbei war, zählt nicht). Worauf er wartet, steht beim Raum
   („Wartet: …“) und – jeder neue Grund einmal je Nacht – im Protokoll; um 1 Uhr folgt „heute Nacht nicht
   gestartet: zuletzt …“.
4. Seit 1.4.3 beginnt er mit einem **schnellen Durchlauf** (etwa 30 Minuten; entfällt, wenn das Thermostat einen
   vollständigen hat, der jünger als 7 Tage ist). Danach hält die App mit dem Fühlerpaar nur noch **vier Stellungen**
   aus dem Gelernten – zu, etwa 30 %, etwa 60 % und ganz offen – je 20–40 Minuten (Spreizung, Wärme) und geht zurück
   auf den Wert von vorher, spätestens um 5 Uhr. Kennt die App „zu“ und „ganz offen“ danach noch nicht, geht der Lauf
   wie bisher vor: Er stellt das Thermostat auf seine eigene Temperatur, dann höher, bis das Ventil **ganz offen**
   ist, danach tiefer, bis es **zu** ist (höchstens bis 2 K unter die Temperatur am Thermostat), und zurück auf den
   Wert von vorher. Die **Schritte richten sich nach dem Ventil** (seit 1.4.2): der erste je Richtung
   1 K; hat sich das Ventil um weniger als 10 % je K bewegt, folgen 2 K, dann 3 K (größter Schritt); hat es sich um
   mehr als 40 Prozentpunkte bewegt, 0,5 K; sonst so viel, dass das Ventil etwa die nächste Marke erreicht (25, 50,
   75, 85 % aufwärts, 50, 25, 5 % abwärts). Reicht die Zeit bis 5 Uhr nicht für alle Marken, werden die Schritte
   größer. Jede Stufe dauert 20–40 Minuten mit warmem Wasser, bis Ventil und Rücklauf ruhig stehen. Höchstens 16
   Befehle je Nacht (sie zählen nicht zu den 24; der Rückweg zählt nicht mit).
   Endet die Nacht (oder das Budget), nachdem das Ventil ganz offen war, aber bevor es zu war, bleibt der Lauf
   vorgemerkt und geht in der **nächsten Nacht nur abwärts** weiter: Er beginnt bei der Abweichung, bei der das Ventil
   ganz offen war. Ebenso läuft ein neu vorgemerkter Testlauf nur abwärts, wenn die App „ganz offen“ schon kennt, „zu“
   aber nicht (etwa im Bad nach der Nacht vom 22./23.11.).
5. Abbruch (zurück auf den Wert von vorher): Raum über Soll + 1 K, Fenster offen, 30 Minuten keine Ventilstellung
   oder kein warmes Wasser, Abbruch durch Sie. Der Raum kann während des Laufs bis zu 1 K wärmer werden, und der Motor
   des Thermostats ist zu hören.
6. Der **Rückweg** auf den Wert von vorher geht immer hinaus, auch nach einem Neustart der App mitten im Lauf. Steht
   der Raum auf **„Steuern“**, übernimmt stattdessen sofort die Regelung (Raum-Soll mit Ausgleich bzw. Korrektur,
   ohne Totzone und Abstand; seit 1.4.3). Wird der Rückweg abgelehnt oder nicht ausgeführt, setzt die App das
   **Raum-Soll** (seit 1.4.3; bisher den Notfallwert); ist sein Ergebnis unbekannt (Thermostat kurz nicht
   erreichbar), versucht sie es wieder, sobald klar ist, dass er nicht angekommen ist (höchstens 2 Stunden). Hat ein
   Lauf nicht sauber zurückgegeben, nimmt der nächste Lauf (oder Durchlauf) den gemerkten Wert von vorher als seinen
   Rückweg, nicht den stehengebliebenen Testschritt. Danach gibt die App das Thermostat wieder frei, außer der Raum
   steht auf „Steuern“.

Jede eingeschwungene Stufe geht sofort ins Regelverhalten. Unter „Testläufe“ stehen die Stufen des letzten Laufs
mit Sollwert, Abweichung, Ventil, Vor- und Rücklauf und der geschätzten Wärmeabgabe. Vollständig ist ein Lauf, wenn
das Ventil ganz offen und wieder zu war. Mit einem Fühlerpaar geht ein Lauf je Nacht; die übrigen Räume lernen im
Alltag.

**Regelung lernt mit** (seit 1.4.3; beim Raum aufklappbar, am Handy zugeklappt):

- **Regelgüte** je Raum und Tag, ab „Vorschau“ (dann mit den Befehlen, die die App gesendet hätte – so ist die
  Vorschau der Vergleich für später): mittlere Abweichung vom gültigen Soll mit Richtung („zu kalt“/„zu warm“) und
  als Betrag, Anteil der Zeit unter und über dem Komfortband, **Überschwinger** (von unter Soll auf mehr als 0,3 K
  darüber, mit dem größten Wert), **Hin und her** (Wechsel von 0,3 K zu kalt auf 0,3 K zu warm oder umgekehrt),
  **Aufheizen** (nach jeder Erhöhung des Solls durch den Zeitplan oder das Vorheizen die Minuten, bis der Raum bis
  auf 0,2 K dran ist, höchstens 3 Stunden – diese Zeit zählt nicht als Abweichung), Befehle, Ventilbewegungen,
  mittlere Ventilöffnung und Zeit am Anschlag (mindestens 90 %: dann fehlt Wasser oder Vorlauf). Nicht gezählt:
  Fenster offen und 30 Minuten danach, Testlauf und Durchlauf, Thermostat nicht erreichbar, Raumsensor fehlt. Im Bad
  (am Thermostat gemessen) nur Befehle und Ventil. Die Seite zeigt heute und die letzten 7 Tage, gespeichert werden
  30; kurz nach Mitternacht steht je Raum eine Zeile im Protokoll.
- **Bedarf je Außentemperatur:** die Ventilöffnung, die den Raum am Soll hält, bei −10, −5, 0, 5, 10 und 15 °C.
  Maßgeblich ist das Mittel der Außentemperatur der letzten 3 Stunden; dazwischen wird gemittelt. Den Startwert jeder
  Stufe holt die App aus dem Verlauf der letzten 21 Tage (ruhige Stunden: Raum mindestens eine Stunde lang innerhalb
  0,3 K am Soll; neuere zählen mehr), ab 5 ruhigen Stunden je Stufe; leere Stufen liegen auf der Geraden durch die
  gelernten, sonst gilt der Bedarf aus dem Raummodell oder 30 %. **Nur in „Steuern“** führt sie ihn nach: liegt der
  Raum 30 Minuten lang durchgehend mindestens 0,2 K daneben, 2 % mehr oder weniger, verteilt auf die beiden
  Nachbarstufen – nicht bei einem Ventil am Anschlag, ohne Wärme, bei Fenster (und 30 Minuten danach), in den 30
  Minuten nach einem neuen Soll, beim Aufheizen und als Spender; Grenzen 0–90 %. Kurz nach Mitternacht rückt jede
  Stufe 20 % in Richtung des Medians ihrer ruhigen Stunden der letzten 7 Tage. Die Seite zeigt die **Bedarfskurve**
  mit dem Punkt für jetzt (gefüllt: gelernt, hohl: Startwert); die Änderungen stehen gesammelt in der täglichen Zeile
  („Bedarf bei 0 °C 34 → 38 %“).
- **Verstärkung je Raum** (% Ventilöffnung je K zu kalt): Start aus dem Raummodell – die Öffnung, die 1 K in etwa
  2 Stunden aufholt, begrenzt auf 20–40 %/K –, ohne gelernte Aufheizrate 30 %/K. Höchstens einmal am Tag, nach einem
  Tag mit mindestens 12 Stunden in „Steuern“: ein Überschwinger über 0,5 K oder zweimal hin und her → ×0,8 („zu
  nervös“); sonst mindestens 2 Stunden am Stück 0,3 K daneben, ohne dass die Abweichung um 0,1 K je Stunde sinkt,
  Ventil nicht am Anschlag → ×1,2 („zu träge“); beides oder nichts → bleibt. Grenzen 10–60 %/K. Der Anteil für den
  Trend ist die Verstärkung mal eine Drittelstunde (30 %/K → 10 % je K/h). Jede Änderung steht im Protokoll
  („Büro: Verstärkung 30 → 24 %/K, zu nervös: 2 Überschwinger bis 0,7 K“); die Seite zeigt Startwert, letzte
  Änderung und Grund.
- **„Gelerntes der Regelung zurücksetzen“** (beim Raum): Der Bedarf wird neu aus dem Verlauf berechnet, die
  Verstärkung geht auf ihren Startwert. Kennfeld und Regelgüte bleiben.

**Absicherung:**

- **Notfallwerte** je Raum eintragen (Bearbeiten → Notfallwert; ohne gilt das Raum-Soll).
- **Lebenszeichen:** `sensor.vaillant_eebus_raeume_lebenszeichen` (jede Minute, unabhängig vom Export). Es bleibt
  stehen, wenn die Raumregelung länger als 12 Minuten nicht mehr gerechnet hat, auch wenn die App sonst läuft. Sein
  Attribut `notfallwerte` nennt die Thermostate, die die App gerade gestellt hat, mit ihren Notfallwerten.
- **Notfall-Automation:** Die Karte „Raumregelung“ zeigt eine Vorlage für Home Assistant. Anlegen: Einstellungen →
  Automationen & Szenen → Automation erstellen → ⋮ → „In YAML bearbeiten“, einfügen, speichern. Kommt 15 Minuten
  kein Lebenszeichen, setzt sie die Thermostate aus `notfallwerte` auf ihren Notfallwert – Thermostate, die Sie selbst
  bedienen, fasst sie also nicht an, und nach einem Wechsel der Stufen muss sie nicht neu kopiert werden. Fehlt das
  Lebenszeichen ganz (Home Assistant neu gestartet, die App läuft nicht), setzt sie alle Thermostate der Räume aus der
  Vorlage. Nach einem Neustart von Home Assistant gibt sie der App erst 15 Minuten Zeit. Prüfen: App stoppen, nach 15
  Minuten stehen die Notfallwerte an. Wird die Raumregelung nicht mehr genutzt, die Automation abschalten.
- **Rückfall durch die App:** Verlässt ein Raum „Steuern“, schalten Sie auf Demo, wird die App beendet, entfernen Sie
  einen Raum oder geben ihm ein anderes Thermostat oder meldet ein gestelltes Thermostat eine blockierende Warnung,
  setzt sie jedes Thermostat, das sie gestellt hat, selbst auf seinen Notfallwert – auch eines, dessen Raum es nicht
  mehr gibt (die App merkt sich dafür den Notfallwert).

**Warnungen** (die App liest sie über die IEEE-Adresse des Thermostats und schreibt nichts davon): Aqara-Fühler nicht
auf „internal“, Betrieb nicht „heat“ oder ein Preset aktiv, Comet nicht in `trv_mode` 2, eigene Fenstererkennung an,
Kindersicherung aus, Ventilalarm, Batterie schwach, Thermostat seit über 12 Stunden nicht gemeldet (mit „zuletzt
gesehen“ nach seiner letzten Nachricht, sonst nach der letzten Änderung von Thermostat oder Ventil). Bei Fühler oder
`trv_mode` stellt die App das Thermostat nicht.

**Was die Raumregelung nie schreibt:** Kalibrierung, Fühlerwahl, externe Temperatur, `trv_mode`, Betrieb, Presets,
Zeitpläne, Fenstererkennung und Kindersicherung der Thermostate – nur den Sollwert (`climate.set_temperature`), und
nur auf dem Thermostat, das einem Raum zugeordnet ist, nie auf einer Zone von myVAILLANT (auch nicht auf einer
Klima-Entität, die wie eine aussieht). Dazu kommt nur die Abfrage der Ventilstellung der W600 (`mqtt.publish` mit
genau der Nachricht oben, nie gespeichert/retained).

## Protokoll

Die Seite **Protokoll** listet, neueste Einträge zuerst und nach Tagen geordnet:

- **Schreiben:** jede Änderung mit Ergebnis: „Würde senden“ (Demo); „Wird gesendet“ (steht im Protokoll, bevor
  der Befehl die App verlässt), „Gesendet“, „Bestätigt vom Gerät“; „Abgelehnt“ mit Grund; „Nicht ausgeführt“ (die
  Wärmepumpe oder die Bridge hat abgelehnt); „Ergebnis unbekannt“; „Nicht bestätigt“; nach einer
  Verbindungsunterbrechung „Nachträglich bestätigt“ oder „Nicht übernommen“,
- **Gerät:** Sollwerte, Betriebsarten und Einmalladung, die außerhalb der App geändert wurden, etwa in der
  myVAILLANT-App,
- **Planer:** Stufe des Energiemanagements, der Plan der nächsten 12 Stunden in Kurzform, sobald er sich ändert,
  erkannte Abtauungen, Beginn und Ende des Frostschutzes und was der Vorlauf tun würde (Vorschau),
- **Räume:** erkannte offene Fenster (mit dem Signal) und unter- oder überversorgte Räume, jeweils als „würde“;
  die Raumregelung mit Fenstern, Testläufen, schnellen Durchläufen, Änderungen der Verstärkung und – kurz nach
  Mitternacht – je Raum einer Zeile zur Regelgüte des Vortags,
- **Verbindung:** Kopplung, Verbindungsaufbau und -abbrüche, auch die Verbindung zu Home Assistant,
- **Modus:** Start der App mit ihrem Betrieb und jeder Wechsel zwischen Demo und Live,
- **Einstellungen:** geänderte Räume und Raumdaten, Wärmesensoren, Export und Einstellungen des
  Energiemanagements.

Neue Einträge erscheinen von selbst. Der Filter **Probleme** zeigt nur, was schiefging: abgelehnte, nicht
ausgeführte oder nicht bestätigte Änderungen, ein unbekanntes Ergebnis, Verbindungsabbrüche und abgelehnte
Verbindungsversuche. Solche Einträge sind auch in den anderen Ansichten rot markiert. Mit **Springen zu** wählen Sie
einen Tag; die Liste beginnt dann am Ende dieses Tages, **Neueste** führt zurück. **Herunterladen (CSV)** speichert
alle Einträge des gewählten Filters als Datei, die sich direkt in Excel öffnen lässt.

Messwerte stehen nicht im Protokoll. Es behält die letzten 10.000 Einträge.

## Diagnose-Dump

Nur mit der Anbindung EEBUS. Der Dump (Einstellungen → Diagnose) beschreibt die vollständige EEBUS-Struktur der
Anlage: Zonen, Messwerte,
Sollwerte mit Grenzen und angebotene Betriebsarten. Die App schickt dafür nur Leseanfragen an das Gateway.
Der Dump enthält Modell- und Seriennummern Ihrer Geräte; die SKI des Gateways ist gekürzt.

## Fehlerbehebung

- **Der Assistent findet kein Gateway:** Gateway und Home Assistant müssen im selben Netzwerk sein (die Suche
  läuft per mDNS und geht nicht über Router oder getrennte VLANs). In der myVAILLANT-App unter *Netzwerk → EEBUS*
  prüfen, dass EEBUS eingeschaltet ist.
- **„Getrennt“ nach einem Start oder Update:** Das Gateway lehnt die ersten Versuche oft einige Minuten lang ab;
  die App versucht es von selbst weiter. Das Protokoll (Filter „Probleme“) zeigt die abgelehnten Versuche.
- **„EEBUS-Port belegt“ oder „Fremde Bridge“:** Eine andere EEBUS-App oder das Add-on „EEBUS Bridge“ läuft
  noch. Unter Einstellungen → Apps stoppen, dort den Autostart ausschalten und diese App neu starten.
- **„Vertrauen entzogen“:** In der myVAILLANT-App unter *Einstellungen → Netzwerk → EEBUS* diese App wieder auf
  **Vertrauenswürdig** setzen.
- **Änderungen kommen nicht an:** Steht der Betrieb auf **Demo**, sendet die App nichts. Im Auto-Betrieb einer
  Zone ist ihre Solltemperatur gesperrt (siehe „Zonen im Auto-Betrieb“). Das Protokoll nennt bei jeder Änderung
  das Ergebnis.
- **Raumtemperaturen oder Wärme heute fehlen:** In den Einstellungen prüfen, ob Home Assistant verbunden ist und
  welche Sensoren gewählt sind. Zeigt Home Assistant selbst einen Sensor als „nicht verfügbar“, liegt das an dessen
  Integration.
- **Kein Verlauf 24 Stunden:** Der Export „Leistung und Energie“ muss eingeschaltet sein, und die Aufzeichnung
  (Recorder) von Home Assistant darf `sensor.vaillant_eebus_power` nicht ausschließen. Die Karte nennt den Grund.
- **Kein Plan:** Die Seite „Plan“ und die Einstellungen nennen fehlende Daten, etwa den Standort für Open-Meteo
  oder dass die Wärmepumpe nicht verbunden ist. Ohne Wetterprognose gibt es keinen Plan.
- **Haus „lernt noch“ oder keine Arbeitszahl:** Die App braucht mindestens zehn Heiztage mit Werten der gewählten
  Wärmesensoren, der Leistung und der Außentemperatur in der Langzeitstatistik von Home Assistant. Sensoren ohne
  Zustandsklasse („state_class“) haben dort keine Statistik.
- **„Die Live-Verbindung zur App ist unterbrochen …“:** Die Übersicht bekommt ihre Werte über eine dauerhaft offene
  Verbindung. Nach einem Neustart oder Update der App, einem Neustart von Home Assistant oder nach dem Ruhezustand
  des Rechners baut die Seite sie von selbst wieder auf (erst nach Sekunden, dann alle 15 s); eine Verbindung, über
  die 45 s lang nichts kommt, ersetzt sie ebenfalls. Bis 1.3.0 blieb das Band in manchen Fällen stehen und die Werte
  froren ein, bis man die Seite neu lud.
- **myVAILLANT: Werte ändern sich nur selten:** Die Integration fragt die Cloud nur in ihrem Intervall ab (siehe
  „Einrichtung mit myVAILLANT“). „Stand“ in der Kopfzeile zeigt die letzte Abfrage.
- **myVAILLANT: „Nicht ausgeführt (HTTP …)“ oder „Ergebnis unbekannt (HTTP 500)“ im Protokoll:** Home Assistant oder
  die Integration hat den Befehl abgelehnt bzw. die Cloud hat einen Fehler gemeldet. Im Log von Home Assistant
  nach „myVAILLANT“ suchen; steht dort „Quota Exceeded“, ist das Abrufkontingent der Cloud erschöpft.
- **myVAILLANT: „Home Assistant nicht erreichbar“ oder „Getrennt“:** Mit myVAILLANT kommen alle Werte über Home
  Assistant. „Getrennt“ heißt: Mindestens eine gewählte Zone oder das Warmwasser ist in Home Assistant „nicht
  verfügbar“.
- **Für eine Fehlermeldung:** Die Log-Stufe in den Optionen der App auf `debug` stellen, den Fehler nachstellen
  und das Log der App, den Diagnose-Dump und das Protokoll (CSV) einer Meldung unter
  <https://github.com/wattarosolar-lgtm/vaillant-eebus/issues> beifügen. Danach die Log-Stufe wieder auf `info`
  stellen.

## Deinstallieren

1. Nur wenn die App je mit EEBUS gekoppelt war: In der myVAILLANT-App unter *Einstellungen → Netzwerk → EEBUS*
   diese App entfernen, damit das Gateway frei ist für einen anderen EEBUS-Partner.
2. In Home Assistant unter Einstellungen → Apps die App öffnen und **Deinstallieren** wählen.

Das Deinstallieren löscht alle Daten der App (siehe „Daten und Sicherung“): Kopplung, Protokoll, Energiewerte und
die EEBUS-Identität. Nach einer Neuinstallation hat die App eine neue Kennung (SKI) und muss in myVAILLANT neu
gekoppelt werden. Die Sensoren `sensor.vaillant_eebus_…` verschwinden mit dem nächsten Neustart von Home Assistant.

## Daten und Sicherung

Die App speichert ihre EEBUS-Identität in `/data/eebus` (Zertifikat und Seriennummer), die Anbindung, die
gewählten myVAILLANT-Entitäten, das gekoppelte Gateway, den gewählten Betrieb, die Räume, die Export-Auswahl und die Einstellungen des Energiemanagements in
`/data/settings.json` (auch die Raumdaten), das Protokoll in `/data/app.db`, den berechneten Energieverbrauch je
Tag (400 Tage) in `/data/energy.json`. Das Energiemanagement speichert seine Raumproben (21 Tage) in
`/data/hems.db`, Takte und Abtauungen (90 Tage) in `/data/cycles.json`, den Plan der letzten 24 Stunden in
`/data/plan_history.json`, die gemerkten Prognosen in `/data/forecast_check.json` und seinen Stand in
`/data/hems_state.json`. Die Raumregelung speichert Korrekturen, Testläufe und Ventilverhalten in
`/data/rooms.json` und die gezählten Befehle je Thermostat in `/data/room_gate.json`. Die Dateien `/data/hems.json`
und `/data/hems_days.json` früherer Versionen werden nicht mehr gebraucht. Diese Dateien sind Teil der
Home-Assistant-Sicherung. Gehen die Daten in `/data/eebus` verloren, ändert sich die SKI der App und die Kopplung
in der myVAILLANT-App muss neu gemacht werden.

**Schutz der EEBUS-Bridge:** Die App enthält eine EEBUS-Bridge, mit der nur die App selbst sprechen darf. Weil sich
alle Apps mit Host-Netzwerk die Netzwerkanschlüsse teilen, schützt die App sie mit einem eigenen Zertifikat und
einem Kennwort (Token). Beides erzeugt sie beim ersten Start selbst in `/data/eebus/grpc`; einzurichten ist nichts.
So kann keine andere App an der App und ihrem Demo-Betrieb vorbei Befehle an die Wärmepumpe senden.

## Optionen

| Option | Bedeutung |
|---|---|
| Log-Stufe | Wie ausführlich die App ins Log schreibt. `debug` schaltet zusätzlich das SHIP-Log der EEBUS-Bridge ein. |

## Lizenzen

Die App steht unter der MIT-Lizenz. Sie enthält die EEBUS-Bridge aus
[volschin/eebus-ha-bridge](https://github.com/volschin/eebus-ha-bridge) (MIT-Lizenz, Copyright (c) 2024 volschin).
Der Lizenztext liegt im Image unter `/usr/share/licenses/eebus-bridge/LICENSE`. Die Seite **Einstellungen → Über
die App** nennt die Versionen von App und Bridge.
