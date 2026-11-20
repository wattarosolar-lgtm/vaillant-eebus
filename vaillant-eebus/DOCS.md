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
**Energiemanagement** Haus und Räume kennen, zählt Takte und Abtauungen und plant 48 Stunden voraus – in dieser
Version nur als Vorschau (siehe „Energiemanagement“).

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
sobald die App wieder mit Home Assistant verbunden ist.

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
  Wert der Anlage. Das Protokoll vermerkt „Würde senden: …“.
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
seltener taktet und abtaut und nachts nur dann abgesenkt wird, wenn es sich lohnt. **In Version 1.3.0 beobachtet,
lernt und plant es nur; es sendet nichts.** Steuern kommt mit 1.4.0 (Zonen) und 1.5.0 (Heizkörperthermostate).
Der Planer der Versionen 1.1 und 1.2 (Warmwasser und Zonen nach Strompreis und PV) ist entfallen; PV-Überschuss
kommt später neu auf dieser Grundlage.

**Stufen** (Einstellungen → Energiemanagement → Stufe):

- **Aus** (Voreinstellung): Es wird nicht geplant. Takte, Abtauungen und Raumwerte zählt und lernt die App trotzdem.
- **Vorschau:** Das Energiemanagement plant alle 15 Minuten neu und zeigt, was es tun würde: auf der Übersicht, auf
  der Seite **Plan** und im Protokoll („würde“). Es sendet nichts, auch nicht im Live-Betrieb.
- **Steuern** ist sichtbar, aber erst ab Version 1.4.0 wählbar.

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
  Startwert. Daraus schätzt sie Wärme- und Strombedarf je Stunde für die nächsten 48 Stunden.
- **Räume:** Alle 5 Minuten speichert die App je Raum Temperatur, Feuchte, Ventilstellung und Thermostat-Soll
  (21 Tage). Daraus lernt sie, wie schnell der Raum ohne Heizung abkühlt, wie schnell er aufheizt, wie lange es nach
  einem Start der Wärmepumpe dauert, bis er nicht mehr fällt, und wie weit das Ventil für das Soll offen steht. Bis
  genug Phasen gesehen sind, steht „lernt noch“ da.
- **Fenster (würde):** Fallen Temperatur und absolute Luftfeuchte gemeinsam schnell (etwa 0,3 K und 0,5 g/m³ in
  10 Minuten) – ohne Feuchtesensor 0,5 K in 10 oder 0,8 K in 20 Minuten –, meldet die App ein offenes Fenster im
  Protokoll (Kategorie „Räume“). So lässt sich prüfen, ob die Erkennung zu echtem Lüften passt.
- **Versorgung (würde):** Steht das Ventil eines Raums seit 30 Minuten zu mindestens 90 % offen und ist der Raum
  0,3 K unter seinem Soll, gilt er als unterversorgt; hält er sein Soll mit wenig offenem Ventil, als überversorgt.
  Für die Heizkörper-Zone leitet die App daraus ab, ob der Vorlauf höher oder tiefer sein dürfte.
- **Zone-1-Logik (würde):** wie die bekannte Automation „Fußbodenheizung aus, solange die Wärmepumpe steht“ – nicht
  bei Frostschutz und nicht, wenn ein Raum der Zone unter sein Komfortband fällt. Die Karte zeigt, wie oft das heute
  zum tatsächlichen Schalten gepasst hätte.

**Was der Plan berücksichtigt** (je Zone und Stunde, mit Grund):

- **Vorlauf:** die Verschiebung aus den Ventilstellungen (höher bei einem unterversorgten Raum, tiefer, wenn alle
  Räume versorgt sind und kein Ventil weit offen steht).
- **Vorausschauend:** Wird es in der Zeit, die die Zone braucht (Fußbodenheizung etwa 3 h, Heizkörper 1 h), deutlich
  kälter, etwas früher mehr; wird es milder oder kommt Sonne, etwas früher weniger.
- **Warmer Tag:** Liegt das Tagesmittel über der Heizgrenze, wird nicht geheizt.
- **Abtaurisiko:** je Stunde aus Temperatur und Luftfeuchte der Prognose, gelernt aus den gezählten Abtauungen. In
  riskanten Stunden etwas weniger Last (höchstens 3 Stunden am Stück), davor speichert die Fußbodenheizung etwas
  mehr – im erlaubten Verlust.
- **Nachtabsenkung:** im Nachtfenster (Voreinstellung 0–10 Uhr) um den erlaubten Verlust (Voreinstellung 0,5 K, je
  Raum einstellbar), aber nur, wenn sich das Wiederaufheizen lohnt (kein Frostschutz, kein Abtaurisiko beim
  Aufheizen, nicht kälter als −5 °C), und nicht in Räumen mit mindestens 65 % Luftfeuchte (Schimmelschutz). Die Zone
  heizt rechtzeitig vor dem Ende des Fensters wieder auf.
- **Blockbetrieb:** In milden Stunden (Voreinstellung ab 5 °C) lange Läufe und lange Pausen; Zone 1 nimmt im Lauf
  Wärme auf, Zone 2 darf in Pausen etwas tiefer liegen.

**Seiten:**

- **Plan:** die nächsten 48 Stunden als Tabelle – „Nur Änderungen“ oder „Stündlich“ – mit Verschiebung, Soll und
  erwartetem Vorlauf je Zone, Soll je Raum, Grund und Prognose (Außentemperatur, Abtaurisiko, Wärme- und Strombedarf).
  **Rückblick 24 h** zeigt je Stunde, was geplant war und was gemessen wurde; **CSV herunterladen** speichert beides.
- **Räume:** je Raum Temperatur, Soll mit Komfortband, Thermostat-Soll, Ventil, Feuchte, Versorgung, Fenster, wie
  schnell der Raum gerade abkühlt und wie lange er ohne Heizung bis zur unteren Komfortgrenze bräuchte, dazu der
  Lernstand. Unter **Bearbeiten** tragen Sie ein: Raum-Soll und bis zu vier Zeitfenster (jeden Tag gleich),
  Komfortband, erlaubten Verlust nachts und gegen Abtauen, Notfallwert, ob der Raum gedrosselt werden darf (ab 1.5.0),
  Heizkörperthermostat, Ventilstellung (Aqara W600: „position“, Eurotronic Comet: „pi_heating_demand“) und
  Luftfeuchte sowie die Werte des statischen Abgleichs (Heizlast, Auslegungs-Soll, Heizflächen mit Normleistung,
  Durchfluss und Voreinstellung). Die Werte des Gutachtens sind nur Startwerte. Räume ohne Temperatursensor (etwa ein
  Bad) wählen Sie in den Einstellungen mit ihrem Thermostat; dessen Wert ist am Heizkörper gemessen und zählt nicht im
  Mittelwert der Zone.

**Einrichten** unter Einstellungen → Energiemanagement, gespeichert mit **Speichern**:

- **Wetterprognose:** Open-Meteo mit den Modellen des Deutschen Wetterdienstes (Breite und Länge, die Taste übernimmt
  den Standort aus Home Assistant) und eine Wetter-Entität als Rückfall. Die App fragt `api.open-meteo.com`
  höchstens einmal pro Stunde mit auf etwa 1 km gerundetem Standort – ihre einzige Verbindung ins Internet. Die
  Prognose wird am Außenfühler der Wärmepumpe geeicht; nach einigen Tagen zeigt die App, wie gut die Prognose für
  den nächsten Tag stimmte.
- **Lernen:** die Wärmesensoren Heizen und Warmwasser, wenn vorhanden die Puffertemperatur, und aus dem statischen
  Abgleich Normaußentemperatur und Heizlast des Hauses.
- **Zonen:** je Zone die Art (Fußbodenheizung oder Heizkörper), Auslegung Vor- und Rücklauf, die Vorlauf-Sensoren
  (Ist und Soll, bei myVAILLANT „… circuit_0/1 … flow temperature“) und wie weit das Soll verschoben werden darf
  (Vorgabe: Fußbodenheizung −1 … +2 K, Heizkörper −2 … +2 K).
- **Nachtabsenkung und Abtauen:** Nachtfenster und erlaubte Verluste (Vorgabe für alle Räume).
- **Takten und Grenzen:** Ziel Starts je Tag, Blockbetrieb ab, Heizgrenze, Schwelle der Zone-1-Logik und – wirksam
  ab 1.4.0 – Schritt und Abstand des Quick-Veto sowie die Tagesgrenze für Befehle an die Vaillant-Cloud.

**Sensoren** (Export-Gruppe „Energiemanagement“): `sensor.vaillant_eebus_hems_status` (Aus, Vorschau),
`sensor.vaillant_eebus_hems_next_change` (nächste geplante Änderung mit Zeit und Grund),
`sensor.vaillant_eebus_hems_plan` (Zahl der geplanten Änderungen in 48 Stunden, die Liste als Attribut),
`sensor.vaillant_eebus_starts_today`, `sensor.vaillant_eebus_defrosts_today` und
`binary_sensor.vaillant_eebus_frost_protection`.

**Was das Energiemanagement nie tut:** Heizkurve, Zeitprogramm, Warmwasser oder Leistungsdeckel ändern. In dieser
Version ruft es die Schreibsperre gar nicht auf; ein Test der App prüft das.

**Wechsel von 1.2:** Hatte der alte Planer beim Update noch eine Verschiebung offen, steht ein Hinweis im Protokoll;
bitte die Solltemperatur prüfen (ein Quick-Veto endet von selbst). Seine Einstellungen zu Tarif, PV und Akku werden
nicht mehr gebraucht.

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
- **Räume:** erkannte offene Fenster und unter- oder überversorgte Räume, jeweils als „würde“,
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
`/data/hems_state.json`. Die Dateien `/data/hems.json` und `/data/hems_days.json` früherer Versionen werden nicht
mehr gebraucht. Diese Dateien sind Teil der
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
