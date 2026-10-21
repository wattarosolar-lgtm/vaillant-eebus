# Vaillant EEBUS

Bindet eine Vaillant-Wärmepumpe **lokal über EEBUS** an Home Assistant an – ohne Vaillant-Cloud,
ohne eBUS-Adapter und ohne MQTT.

## Was die App kann

Die App koppelt sich mit dem Gateway Ihrer Wärmepumpe und zeigt deren Werte live in einer eigenen Oberfläche in
der Seitenleiste von Home Assistant, jede Heizzone einzeln, mit dem Verlauf der Leistung über 24 Stunden. Sie ändert
Solltemperaturen, Betriebsarten und die Einmalladung des Warmwassers. Die Raumtemperatur jeder Zone kommt aus
Temperatursensoren in Home Assistant, und die App meldet die Werte der Wärmepumpe als Sensoren an Home Assistant.
Ein Protokoll hält jede Änderung und jedes Verbindungsereignis fest. Seit Version 1.1.0 plant ein
**Energiemanagement** Warmwasser und Heizen nach Strompreis, PV-Prognose und Wetter voraus (siehe
„Energiemanagement“).

Nach der Installation läuft die App im **Demo-Betrieb** und sendet nichts an die Wärmepumpe (siehe
„Demo- und Live-Betrieb“).

## Voraussetzungen

- Vaillant-Wärmepumpe mit Vaillant-Gateway (VR921 oder VR940f) und der myVAILLANT-App.
- Home Assistant OS oder Supervised (die App braucht das Host-Netzwerk für die EEBUS-Suche per mDNS).
- Gateway und Home Assistant im selben Netzwerk.
- **Das Gateway erlaubt nur eine aktive EEBUS-Verbindung.** Ist bereits ein anderer EEBUS-Partner
  gekoppelt (z. B. ein Energiemanager, das Add-on „EEBUS Bridge“ oder eine andere EEBUS-App), kommt keine
  Verbindung zustande. Andere EEBUS-Apps bitte stoppen; sie belegen außerdem dieselben Ports.

## Installation

1. In Home Assistant **Einstellungen → Apps → App-Store** öffnen.
2. Oben rechts **⋮ → Repositories** wählen, diese Adresse eintragen und **Hinzufügen**:
   `https://github.com/wattarosolar-lgtm/vaillant-eebus`
3. Den Dialog schließen. Im App-Store erscheint **Vaillant EEBUS** (sonst die Seite neu laden). Die App öffnen
   und **Installieren** wählen.
4. Nach der Installation **Starten** und auf derselben Seite **In der Seitenleiste anzeigen** einschalten. Mit dem
   **Watchdog** startet Home Assistant die App neu, falls sie abstürzt.
5. In der Seitenleiste **Wärmepumpe** öffnen (oder **Benutzeroberfläche öffnen** wählen). Der
   Einrichtungsassistent führt durch die Kopplung (nächster Abschnitt).

## Einrichtung

1. **Gateway auswählen:** Die App sucht im Netzwerk nach EEBUS-Geräten. Wählen Sie das Gateway Ihrer
   Wärmepumpe (VR921 oder VR940f).
2. **In der myVAILLANT-App bestätigen:** *Einstellungen → Netzwerk → EEBUS*, dort diese App auf
   **Vertrauenswürdig** setzen. Der Assistent zeigt die Kennung (SKI) der App zum Vergleich.
3. Sobald die Verbindung steht, zeigt der Assistent **Verbunden**. Laden Sie dann den **Diagnose-Dump** herunter
   und heben Sie ihn auf; er hilft bei Fragen zur Anlage.
4. Unter **Einstellungen** die Räume für die Zonen wählen (siehe „Home Assistant“).

Das gekoppelte Gateway steht in `/data/settings.json`. Die App meldet es nach jedem Neustart selbst wieder an.

Nach einem Start oder Update der App lehnt das Gateway die ersten Verbindungsversuche oft ab. Bis die Werte
erscheinen, können einige Minuten vergehen. Das Log fasst die Versuche zusammen und nennt die Dauer, sobald die
Verbindung steht.

## Update

Home Assistant meldet eine neue Version unter **Einstellungen → Apps** und auf der Seite der App; dort steht auch,
was sich geändert hat. Ein Update behält Kopplung, Betrieb (Demo oder Live), Räume, Export-Auswahl, Protokoll und
Energiewerte sowie die Einstellungen und Grundwerte des Energiemanagements. Nach dem Update baut die App die
Verbindung zum Gateway neu auf (siehe oben, das kann einige Minuten dauern).

## Übersicht

Die Seite **Übersicht** zeigt für jede Heizzone und für das Warmwasser ein Rundinstrument: die Solltemperatur auf
dem Bogen mit ihrem erlaubten Bereich, die aktive Betriebsart und beim Warmwasser die Speichertemperatur. Jede Zone
trägt den Namen, den sie in der Anlage hat (z. B. „Zone 1“ und „Zone 2“). Darunter stehen Leistung, Energie und
Temperaturen. Oben sieht man, ob das Gateway verbunden ist, ob die Wärmepumpe ihren Heartbeat sendet, ihren
Betriebszustand, die Außentemperatur und ob der Verdichter läuft (ab 100 W Leistungsaufnahme).

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
(Regler und −/+). Für eine zeitweise höhere oder niedrigere Temperatur (Boost) nutzen Sie die myVAILLANT-App oder
deren Integration in Home Assistant. Auch was Sie dort im Auto-Betrieb ändern, sieht diese App nicht. In der
Betriebsart **Ein** heizt die Zone auf die Solltemperatur, die Sie hier einstellen.

Was die Wärmepumpe über EEBUS nicht liefert, steht als **nicht verfügbar** da.

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
ist eine Schätzung. Lief die App nicht oder war die Wärmepumpe nicht erreichbar, steht „(lückenhaft)“ dahinter;
die fehlende Zeit wird nicht geschätzt. Das gilt auch für den ersten Tag nach der Installation.

**Wärme heute und Arbeitszahl heute:** Haben Sie in den Einstellungen Sensoren mit der heute erzeugten Wärme
gewählt, zeigt die Karte „Leistung und Energie“ deren Summe und die Arbeitszahl des Tages (Wärme ÷ Strom). Die Summe
erscheint nur, wenn jeder gewählte Sensor einen Wert hat. Die Arbeitszahl erscheint nur, wenn die Wärmewerte aktuell
sind (höchstens 3 Stunden alt), der Stromverbrauch des Tages lückenlos ist und mindestens 0,5 kWh beträgt.

**Energiemanagement:** Die Karte unten zeigt, was der Planer vorhat (siehe „Energiemanagement“).

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

Die App liest Werte aus Home Assistant und meldet ihre eigenen Werte dorthin. Sie ändert keine anderen Geräte
oder Entitäten. Der einzige Dienst, den sie aufruft, ist `weather.get_forecasts` für die Temperaturprognose des
Energiemanagements; er liest nur. Ist Home Assistant kurz nicht erreichbar (etwa bei einem Neustart),
laufen Übersicht und Steuerung der Wärmepumpe weiter; die Werte aus Home Assistant stehen solange als „nicht
verfügbar“ da. Ob die App gerade mit Home Assistant verbunden ist, zeigt die Seite **Einstellungen** neben der
Überschrift „Home Assistant“.

Eingerichtet wird alles unter **Einstellungen**, gespeichert mit **Speichern**:

- **Räume:** Unter „Raum hinzufügen“ suchen Sie einen Temperatursensor (°C oder °F), geben dem Raum einen Namen
  und wählen Zone 1, Zone 2 oder keine Zone. Ein Wert zählt für den Mittelwert, solange er eine Zahl ist und der
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
  Energiemanagement; Leistungsdeckel und Verdichter-Flexibilität sind aus. Den Verlauf 24 Stunden und den
  gelernten Hausverbrauch ohne Wärmepumpe gibt es nur mit „Leistung und Energie“.

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

Der Planer legt das Laden des Warmwassers in Stunden mit PV-Überschuss oder günstigem Strom und heizt die Zonen
dann vor; bei teurem Strom senkt er sie etwas ab. Er plant von jetzt bis morgen Abend in Viertelstunden und plant
alle 15 Minuten neu, außerdem sobald neue Strompreise oder eine neue PV-Prognose kommen.

**Stufen** (Einstellungen → Energiemanagement → Planer):

- **Aus** (Voreinstellung): Der Planer plant nicht und ändert nichts.
- **Vorschau:** Der Planer plant und zeigt auf der Übersicht, was er tun würde. Er sendet nichts – auch nicht im
  Live-Betrieb. Im Protokoll steht „Planer würde senden: …“. Damit sollten Sie ein paar Tage beginnen.
- **Steuern:** Der Planer setzt den Plan um (nach einer Bestätigung). Er sendet über dieselbe Schreibsperre wie die
  Oberfläche; im Demo-Betrieb der App sendet er also trotzdem nichts. Seine Befehle stehen im Protokoll mit
  „(Planer)“.

**Was der Planer tut:**

- **Warmwasser**, höchstens einmal am Tag: in den besten 1,5 Stunden mit mindestens 1,5 kW PV-Überschuss, sonst mit
  Strom, der mindestens 5 ct/kWh unter dem Tagesmittel liegt. Dann hebt er die Solltemperatur auf den Höchstwert
  (Voreinstellung 60 °C) und startet die Einmalladung. Danach kehrt er zum Grundwert zurück. Er senkt das
  Warmwasser nie unter den Grundwert.
- **Zonen**, nur in der Betriebsart **Ein** und nur unter der Heizgrenze (Voreinstellung 15 °C außen): Vorheizen um
  die eingestellte Verschiebung (Voreinstellung +1 K), solange mindestens 1 kW PV-Überschuss erwartet wird, oder
  bei Strom mindestens 5 ct/kWh unter dem Tagesmittel (höchstens die 4 günstigsten Stunden am Tag). Absenken
  (Voreinstellung −1 K) nur bei Strom mindestens 5 ct/kWh über dem Tagesmittel, höchstens 3 Stunden am Tag; bei
  einem reinen Festpreis also nie. Jeder Block dauert mindestens eine Stunde.
- Als **PV-Überschuss** zählt, was nach dem üblichen Hausverbrauch und dem Laden des Akkus ins Netz ginge. Den
  üblichen Hausverbrauch ohne Wärmepumpe lernt der Planer aus den letzten sieben Tagen in Home Assistant. Im Winter
  nimmt der Akku den Überschuss meist selbst auf; bei einem Festpreis tut der Planer dann wenig – das ist gewollt.
- Warmwasser und Vorheizen können sich überschneiden; die Wärmepumpe lädt dann zuerst das Warmwasser.

**Grundwert:** Für jede Zone und das Warmwasser merkt sich der Planer den normalen Wert, beim Einschalten den
aktuellen. Ändern Sie eine Solltemperatur selbst – in dieser App, in myVAILLANT oder am Gerät, auch während die
App nicht läuft –, wird sie zum neuen Grundwert; der Planer lässt diese Steuerung bis zum Ende des laufenden Blocks in Ruhe und plant danach um den neuen
Wert herum. Jede Steuerung ändert er höchstens alle 30 Minuten.

**Zurück zum Grundwert** geht der Planer am Ende jedes Blocks, beim Umschalten auf Aus oder Vorschau, vor dem
Wechsel in den Demo-Betrieb und wenn die App beendet wird (dafür hat er bis zu 15 Sekunden). Fehlt ihm beim Start noch ein verschobener Wert, den der
neue Plan nicht mehr vorsieht, setzt er ihn ebenfalls zurück.

**Was der Planer nie tut:** Betriebsarten umschalten, den Leistungsdeckel oder den Verdichter steuern, den Akku
steuern oder Werte an die Wärmepumpe melden. Im Auto-Betrieb einer Zone verschiebt er nichts.

**Einrichten** unter Einstellungen → Energiemanagement, gespeichert mit **Speichern**:

- **Strompreis:** *Festpreis* (Arbeitspreis), *Dynamisch* (Preis-Sensor der Integration `epex_spot`, am besten
  „Total Price“ mit Aufschlägen und MwSt.; sonst Aufschlag und MwSt. hier eintragen) oder *Festpreis mit §14a
  Modul 3* (Preise für Standardzeit, Hoch- und Niedertarif mit Netzentgelt und die Zeitfenster Ihres
  Netzbetreibers; ein Fenster darf über Mitternacht gehen). Dazu die Einspeisevergütung.
- **PV, Hausverbrauch und Akku:** die PV-Prognose für heute und morgen (Sensoren `…energy_production_today` und
  `…_tomorrow` der Integration „Open-Meteo Solar Forecast“), ein Sensor mit dem Verbrauch des ganzen Hauses in W
  oder kW (mit Wärmepumpe) und, falls vorhanden, der Ladestand des Akkus mit Kapazität, Ladeleistung und
  Entladegrenze (Kapazität und Ladeleistung sind dann Pflicht). Gibt es keinen Hausverbrauch-Sensor, legen Sie in Home Assistant einen Template-Sensor an
  (Einstellungen → Geräte & Dienste → Helfer → Template → Sensor, Einheit W, Zustandsklasse „Messung“). Mit
  `huawei_solar` zum Beispiel:
  `{{ (states('sensor.inverter_active_power') | float(0) - states('sensor.power_meter_active_power') | float(0)) | round(0) }}`
  – Entitätsnamen und Vorzeichen bitte an Ihre Anlage anpassen (hier meldet der Zähler die Einspeisung positiv).
- **Temperaturprognose:** eine Wetter-Entität aus Home Assistant oder Open-Meteo mit Breite und Länge (die Taste
  übernimmt den Standort aus Home Assistant). Mit Open-Meteo fragt die App höchstens einmal pro Stunde
  `api.open-meteo.com` ab – ihre einzige Verbindung ins Internet.
- **Grenzen:** je Zone, wie weit der Planer hoch und runter darf (0 bis 3 K in 0,5er-Schritten), der Höchstwert
  des Warmwassers und die Heizgrenze.

**Übersicht:** Die Karte „Energiemanagement“ zeigt die Stufe, die nächste Aktion mit Grund, fehlende Daten und eine
Zeitleiste bis morgen Abend: oben der erwartete PV-Überschuss, darunter der Strompreis und je Steuerung die
geplanten Blöcke (Warmwasser laden, Vorheizen, Absenken). Zeigen Sie mit Maus oder Finger auf die Zeitleiste oder
gehen Sie mit den Pfeiltasten (Bild auf/ab: zwei Stunden), steht darüber, was zu dieser Viertelstunde gilt.

**Lernen:** Die App sammelt je Tag Strom der Wärmepumpe, mittlere Außen- und Innentemperatur
(`/data/hems_days.json`). Ab zehn vollständigen Heiztagen schätzt sie daraus den Strombedarf für morgen; in dieser
Version ist das nur eine Anzeige, der Plan nutzt es noch nicht.

**Sensoren** (Export-Gruppe „Energiemanagement“): `sensor.vaillant_eebus_hems_status` (Aus, Vorschau, Steuern),
`sensor.vaillant_eebus_hems_next_action`, `sensor.vaillant_eebus_hems_plan` (Anzahl der kommenden Blöcke, die
Blöcke als Attribut) und `sensor.vaillant_eebus_hems_demand_tomorrow` (geschätzt).

## Protokoll

Die Seite **Protokoll** listet, neueste Einträge zuerst und nach Tagen geordnet:

- **Schreiben:** jede Änderung mit Ergebnis: „Würde senden“ (Demo); „Wird gesendet“ (steht im Protokoll, bevor
  der Befehl die App verlässt), „Gesendet“, „Bestätigt vom Gerät“; „Abgelehnt“ mit Grund; „Nicht ausgeführt“ (die
  Wärmepumpe oder die Bridge hat abgelehnt); „Ergebnis unbekannt“; „Nicht bestätigt“; nach einer
  Verbindungsunterbrechung „Nachträglich bestätigt“ oder „Nicht übernommen“,
- **Gerät:** Sollwerte, Betriebsarten und Einmalladung, die außerhalb der App geändert wurden, etwa in der
  myVAILLANT-App,
- **Planer:** Stufe des Energiemanagements, jeder geänderte Plan in Kurzform, „Planer würde senden“ (Vorschau),
  neue Grundwerte nach einer Änderung von Hand und das Zurücksetzen auf die Grundwerte,
- **Verbindung:** Kopplung, Verbindungsaufbau und -abbrüche, auch die Verbindung zu Home Assistant,
- **Modus:** Start der App mit ihrem Betrieb und jeder Wechsel zwischen Demo und Live,
- **Einstellungen:** geänderte Räume, Wärmesensoren, Export und Einstellungen des Energiemanagements.

Neue Einträge erscheinen von selbst. Der Filter **Probleme** zeigt nur, was schiefging: abgelehnte, nicht
ausgeführte oder nicht bestätigte Änderungen, ein unbekanntes Ergebnis, Verbindungsabbrüche und abgelehnte
Verbindungsversuche. Solche Einträge sind auch in den anderen Ansichten rot markiert. Mit **Springen zu** wählen Sie
einen Tag; die Liste beginnt dann am Ende dieses Tages, **Neueste** führt zurück. **Herunterladen (CSV)** speichert
alle Einträge des gewählten Filters als Datei, die sich direkt in Excel öffnen lässt.

Messwerte stehen nicht im Protokoll. Es behält die letzten 10.000 Einträge.

## Diagnose-Dump

Der Dump (Einstellungen → Diagnose) beschreibt die vollständige EEBUS-Struktur der Anlage: Zonen, Messwerte,
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
- **Der Planer plant nichts:** Die Karte „Energiemanagement“ nennt fehlende Daten (Preis, PV-Prognose,
  Hausverbrauch, Akku, Temperaturprognose) und je Steuerung den Grund, etwa „nur in der Betriebsart Ein“ oder „über
  der Heizgrenze“. Für den Hausverbrauch braucht der Planer dessen Verlauf in Home Assistant.
- **Für eine Fehlermeldung:** Die Log-Stufe in den Optionen der App auf `debug` stellen, den Fehler nachstellen
  und das Log der App, den Diagnose-Dump und das Protokoll (CSV) einer Meldung unter
  <https://github.com/wattarosolar-lgtm/vaillant-eebus/issues> beifügen. Danach die Log-Stufe wieder auf `info`
  stellen.

## Deinstallieren

1. In der myVAILLANT-App unter *Einstellungen → Netzwerk → EEBUS* diese App entfernen, damit das Gateway frei ist
   für einen anderen EEBUS-Partner.
2. In Home Assistant unter Einstellungen → Apps die App öffnen und **Deinstallieren** wählen.

Das Deinstallieren löscht alle Daten der App (siehe „Daten und Sicherung“): Kopplung, Protokoll, Energiewerte und
die EEBUS-Identität. Nach einer Neuinstallation hat die App eine neue Kennung (SKI) und muss in myVAILLANT neu
gekoppelt werden. Die Sensoren `sensor.vaillant_eebus_…` verschwinden mit dem nächsten Neustart von Home Assistant.

## Daten und Sicherung

Die App speichert ihre EEBUS-Identität in `/data/eebus` (Zertifikat und Seriennummer), das gekoppelte Gateway,
den gewählten Betrieb, die Räume, die Export-Auswahl und die Einstellungen des Energiemanagements in
`/data/settings.json`, das Protokoll in `/data/app.db`, den berechneten Energieverbrauch je Tag (400 Tage) in
`/data/energy.json`, die Grundwerte des Planers in `/data/hems.json` und seine Tageswerte (400 Tage) in
`/data/hems_days.json`. Diese Dateien sind Teil der
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
