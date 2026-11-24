# Changelog

## 1.4.3

**Schneller lernen, Fenster sicher erkennen, Regelung lernt mit.** Mit 1.4.2 lernte das Thermostat sein
Regelverhalten aus Testläufen und Alltag – das dauerte Nächte bzw. Tage. Beim Lüften am 23.11. erkannte die App ein
offenes Fenster mit den Aqara-Raumsensoren oft gar nicht oder erst nach Minuten.

- **Fenster werden schneller und sicherer erkannt:** Die App vergleicht jede Meldung des Raumsensors mit der letzten,
  auch wenn die älter ist (bisher nur innerhalb 10 Minuten – die Sensoren melden oft nur bei einer Änderung). Die
  **Luftfeuchte** ist das Hauptsignal: Fällt die absolute Feuchte schnell genug um eine Schwelle, die sich nach dem
  Wetter richtet (0,2 × Unterschied innen/außen, 0,5–1,0 g/m³; bei mildem, feuchtem Wetter zählt sie nicht), und
  steigt die Temperatur nicht, ist das Fenster offen. Dazu die Temperatur (−0,5 K in 15, −0,8 K in 30 Minuten, wenn
  es draußen deutlich kälter ist) und der **Fühler am Thermostat** (−1 K in 8 Minuten, der Raumsensor steigt nicht).
  Zu gilt es nach +0,3 g/m³, +0,2 K im Raum oder +0,5 K am Thermostat, spätestens nach 30 Minuten. Nachgerechnet mit
  dem Lüften vom 23.11.: offen nach 2,5 bzw. 3,4 Minuten (bisher nicht bzw. nach 6 Minuten erkannt).
- **Fensterkontakt je Raum (optional):** ein `binary_sensor` im Raum-Editor; mit Kontakt gilt nur er, sofort. Meldet
  er sich 10 Minuten nicht, gelten wieder die Regeln.
- **Sofort reagieren:** Die Raumregelung entscheidet beim Öffnen und Schließen eines Fensters sofort, nicht erst im
  5-Minuten-Takt. Das Protokoll nennt das Signal; die Seite zeigt je Raum, wie das Fenster erkannt wird.
- **Schneller Durchlauf:** In etwa 30 Minuten, ohne Fühlerpaar und ohne warmes Wasser, lernt die App, ab wann das
  Ventil öffnet und schließt: alle 2 Minuten ein neuer Sollwert, 0,5 K (1 K, wenn sich das Ventil kaum bewegt), hoch
  bis ganz offen, runter bis zu, zurück. Höchstens 20 Befehle, eigenes Budget (nicht bei den 24 am Tag). Per Knopf
  je Raum oder **„Alle Räume nacheinander“**. Das Ergebnis geht ins Kennfeld (Quelle „Durchlauf“, nur wo sonst
  nichts bekannt ist) und **reicht für „Steuern“**. Die Seite zeigt Fortschritt und die Kurve des letzten Durchlaufs.
- **Nacht-Testlauf kürzer:** Er beginnt mit einem schnellen Durchlauf (entfällt, wenn einer jünger als 7 Tage ist)
  und hält danach mit dem Fühlerpaar nur noch vier Stellungen – zu, etwa 30 %, 60 %, ganz offen – für Spreizung und
  Wärme.
- **Regelgüte je Raum und Tag** (ab „Vorschau“): mittlere Abweichung (zu kalt/zu warm und Betrag), Zeit außerhalb
  des Komfortbands, Überschwinger, Hin und her, Aufheizen nach einer Soll-Erhöhung (getrennt), Befehle, Ventil.
  Ohne Fenster, Testlauf und Durchlauf. Die Seite zeigt heute und 7 Tage, kurz nach Mitternacht steht je Raum eine
  Zeile im Protokoll.
- **Bedarf je Außentemperatur:** Die Ventilöffnung, die den Raum am Soll hält, je Stufe −10 … 15 °C (Mittel der
  letzten 3 Stunden, dazwischen gemittelt), Startwert aus den ruhigen Stunden der letzten 21 Tage. In „Steuern“ führt
  die App ihn nach (±2 % je halbe Stunde daneben); täglich rückt er 20 % zum Verlauf der letzten 7 Tage.
- **Verstärkung je Raum:** Start aus dem Raummodell (20–40 %/K, sonst 30 %/K); höchstens einmal am Tag nach einem
  Tag mit mindestens 12 Stunden „Steuern“ ×0,8 bei Überschwingern oder Hin und her, ×1,2, wenn der Raum lange träge
  daneben liegt (10–60 %/K). Jede Änderung mit Grund im Protokoll.
- **Seite „Räume“:** je Raum „Regelung lernt mit“ mit Bedarfskurve, Verstärkung, Regelgüte und dem Knopf „Gelerntes
  der Regelung zurücksetzen“ (am Handy zugeklappt).
- **Sauber zurück nach Testlauf und Durchlauf** (Befund 4 der Abnahme 1.4.0: Bad nach dem Testlauf vom 23./24.11.
  1 K zu kalt, Ventil 50 Minuten zu):
  - Ein Lauf nimmt als „Sollwert vorher“ den Rückweg-Wert, den sich die App von einem früheren, nicht sauber
    beendeten Lauf gemerkt hat – nicht den stehengebliebenen Testschritt. Lauf und Schreibschutz kennen so denselben
    Wert, der Rückweg wird nicht mehr abgelehnt.
  - Steht der Raum auf „Steuern“, übernimmt nach dem Lauf sofort die Regelung (Raum-Soll mit Ausgleich bzw.
    Korrektur), ohne Totzone und ohne 15 Minuten Abstand.
  - Geht der Rückweg nicht hinaus, setzt die App das Raum-Soll statt des Notfallwerts (den gibt es nur, wenn sie das
    Thermostat ganz loslässt). Ist das Ergebnis unbekannt (Thermostat kurz nicht erreichbar), versucht sie es wieder,
    sobald klar ist, dass er nicht angekommen ist – höchstens 2 Stunden.
  - **Ventilstellung des Comet:** Zigbee2MQTT kann sie beim Eurotronic Comet nicht abfragen (`pi_heating_demand` nur
    gemeldet, kein „get“); er meldet sie meist nur kurz nach einem neuen Sollwert. Die Seite zeigt deshalb, wann die
    Stellung gemeldet wurde, und – hängt das Fühlerpaar am Heizkörper – die Spreizung mit der Öffnung, die der
    Testlauf dabei gemessen hat; passt die gemeldete Stellung nicht dazu, steht ein Hinweis da.

## 1.4.2

**Das Thermostat lernt sein Regelverhalten** (Abnahme 1.4.0, Befunde 2 und 3). In 1.4.0/1.4.1 ließ sich „Steuern“
in keinem Raum einschalten: Es brauchte einen vollständigen Testlauf, und der Testlauf im Bad (22./23.11.) wurde mit
0,5-K-Schritten bis 5 Uhr nicht fertig; der Comet kam nie auf 90 %. Außerdem meldeten die Aqara W600 ihre
Ventilstellung oft stundenlang nicht.

- **Die App fragt die Ventilstellung der Aqara W600 selbst ab:** über die MQTT-Integration von Home Assistant eine
  Nachricht an Zigbee2MQTT (`zigbee2mqtt/<Gerätename>/get` mit `{"running_state": ""}`) – nur diese, über den
  Schreibschutz mit dem eigenen Grund „Abfrage“, nicht bei den 24 Befehlen am Tag. Alle 5 Minuten kurz vor dem Takt
  der Regelung, 1 und 3 Minuten nach jedem neuen Sollwert (auch von Hand oder durch einen Zeitplan) und im Testlauf
  jede Minute; höchstens alle 30 Sekunden und 720-mal am Tag je Thermostat. Die Abfrage liest nur und läuft deshalb
  auch im Demo-Betrieb, ab „Beobachten“. Gerätename aus Home Assistant, je Raum änderbar; Basis-Topic einstellbar.
  Namen, die Zigbee2MQTT als Schreibbefehl lesen könnte („…/set“, „get“, `+`, `#`), nimmt die App nicht. Der Comet
  meldet seine Stellung selbst.
- **Regelverhalten je Thermostat (Kennfeld):** Ventilstellung nach dem Einschwingen über der Abweichung (Sollwert
  minus Temperatur am Thermostat, 0,5-K-Raster), steigend und fallend getrennt, nach Wärmeangebot (Vorlauf unter 30,
  30 bis 35, über 35 °C); dazu Sprungantwort (Reaktion, Geschwindigkeit, Einschwingzeit), Reglerart (auf/zu, P, PI)
  und kleinster wirksamer Schritt. Gelernt aus Testläufen und **aus dem Alltag** ab „Beobachten“, ohne Fühlerpaar
  (Sollwert und Abweichung 30 Minuten ruhig, warmes Wasser, aktuelle Ventilstellung). Neuere Werte zählen mehr. Die
  Testläufe von 1.4.1 werden übernommen.
- **„Steuern“ ist frei, sobald „zu“ und „ganz offen“ bei warmem Wasser bekannt sind** – aus einem Testlauf oder aus
  dem Alltag. „Ganz offen“ heißt jetzt mindestens 85 % (oder ab 50 % kein Zuwachs mehr bei +1 K; der Comet bleibt bei
  88 %). Die Seite zeigt beim Raum, was noch fehlt.
- **Testlauf mit Schritten nach dem Ventil:** zuerst 1 K; bewegt sich das Ventil um weniger als 10 % je K, 2 K, dann
  3 K (größter Schritt); bewegt es sich um mehr als 40 Prozentpunkte, 0,5 K; sonst Richtung der nächsten Marke (25 /
  50 / 75 / 85 % hoch, 50 / 25 / 5 % runter). Er plant mit der Zeit bis 5 Uhr und nimmt größere Schritte, wenn sie
  nicht reicht. Runter bis „zu“, höchstens 2 K (bisher 1 K) unter die Temperatur am Thermostat. Endet ein Lauf nach
  „ganz offen“ um 5 Uhr, fährt er in der nächsten Nacht nur noch abwärts; ebenso ein neuer Lauf, wenn „ganz offen“
  schon bekannt ist. Nachgespielt mit der echten Nacht im Bad: fertig um 3:55 statt unvollständig um 5 Uhr.
- **Regeln mit dem Kennfeld:** Für die Öffnung, die der Raum braucht, nimmt die App die kleinste Abweichung, die sie im
  jetzigen Zustand (steigend/fallend, Wärmeangebot) erreicht hat. Bei PI-Thermostaten wartet sie nach jeder Änderung
  die Einschwingzeit ab (mindestens 30 Minuten) und legt nicht nach, solange das Ventil noch in die richtige Richtung
  läuft.
- **Seite „Räume“ live:** Sie lädt neu, sobald sich Thermostat, Ventil, Raumsensor oder Fühlerpaar ändern oder die
  App entscheidet oder sendet (über die bestehende Live-Verbindung, höchstens alle 2 Sekunden; sonst jede Minute).
- **„Zuletzt gesehen“ (optional):** Gibt es `sensor.<Gerät>_last_seen` von Zigbee2MQTT, gilt es als Lebenszeichen
  des Thermostats und zeigt, ob ein W600 auf die Abfrage geantwortet hat. Die falsche Warnung „Ventilstellung seit über
  12 h nicht gemeldet“ entfällt; gewarnt wird erst, wenn sich das Thermostat 12 Stunden gar nicht meldet.

## 1.4.1

**Der Testlauf startet auch mit Fühlern, die nur bei einer Änderung melden.** In 1.4.0 galt ein Wert des
Fühlerpaars nur 15 Minuten. Viele Anlegefühler (etwa die wasserdichten Sonoff-Fühler mit Sonde) melden aber nur, wenn
sich die Temperatur ändert; steht sie still, kommt eine Stunde lang nichts. Der Testlauf wartete dann mit „Das
Fühlerpaar hängt nicht an diesem Heizkörper oder meldet nichts“ und startete nie; ein gestarteter Lauf wäre nach
30 Minuten mit „Kein warmes Wasser“ abgebrochen.

- Ein Wert des Fühlerpaars gilt jetzt **bis 2 Stunden** nach seiner Meldung, vor dem Start und während des Laufs.
  Meldet ein Fühler „nicht verfügbar“, fehlt er wie bisher sofort.
- **Vertauschte Fühler:** Ist der Rücklauf 15 Minuten lang mindestens 1 K wärmer als der Vorlauf, während warmes
  Wasser am offenen Ventil (ab 20 %) ankommt, zeigt die Karte „Fühlerpaar“ einen Hinweis und den Knopf **„Vorlauf und
  Rücklauf tauschen“**; im Protokoll steht es als Problem. Solange startet kein Testlauf, weil seine Messwerte falsch
  wären.
- **Wartegründe im Protokoll:** Wartet ein vorgemerkter Lauf zwischen 22 und 1 Uhr, steht jeder neue Grund einmal je
  Nacht im Protokoll („Testlauf Bad wartet: …“), und um 1 Uhr „heute Nacht nicht gestartet: zuletzt …“. Ein Abbruch
  von Hand nennt den letzten Wartegrund mit.
- Die Wartegründe sind genauer: Fühlerpaar an einem anderen Heizkörper, Vorlauf- oder Rücklauf-Fühler seit über 2 h
  ohne Meldung, Fühler nicht verfügbar, Fühler vertauscht.
- Die Karte „Fühlerpaar“ zeigt je Fühler, wann er zuletzt gemeldet hat, und die längste Meldepause (statt „meldet
  etwa alle … min“, das nur die Zeit einer Änderung zeigte).

## 1.4.0

**Raumregelung: die App stellt die Heizkörperthermostate** (Stufe 1, Punkt 1; vorgezogen vor die Zonen, die jetzt
mit 1.5.0 kommen). Jedes Thermostat bekommt von der App seinen Sollwert, damit der Raum – gemessen am Raumsensor –
sein Soll hält. An die Wärmepumpe sendet diese Version weiterhin nichts Neues.

- **Ein neuer Schreibweg, nur über die Schreibsperre:** `climate.set_temperature` (nur die Temperatur) auf dem
  Thermostat, das einem Raum zugeordnet ist – nie auf einer myVAILLANT-Zone. Kalibrierung, Fühlerwahl, externe
  Temperatur, `trv_mode`, Betrieb, Presets, Zeitpläne, Fenstererkennung und Kindersicherung der Thermostate schreibt
  die App nie; sie prüft sie nur und warnt. Strukturtests sichern das ab.
- **Stufen:** ein Schalter für alle Räume (Aus, Beobachten, Vorschau, Steuern) und eine Stufe je Raum; es gilt die
  kleinere. „Steuern“ nur im Live-Betrieb, mit Bestätigung und erst nach einem vollständigen **Testlauf** des
  Thermostats. Nach dem Update steht alles auf „Beobachten“ (wenn das Energiemanagement auf Vorschau stand, sonst
  „Aus“): Es ändert sich nichts, bis Sie umstellen.
- **Regeln je Raum:** Raum-Soll und Zeitplan (rechtzeitig vorheizen, höchstens 3 h vorher) plus eine gelernte
  Korrektur am Raumsensor (0,5-K-Schritte nach 0,2 K Abweichung über 30 Minuten, nicht höher bei offenem Ventil oder
  ohne warmes Wasser, nicht tiefer bei geschlossenem Ventil); nach einem Testlauf über die gelernte Ventilkennlinie
  („braucht etwa 45 % Ventil“). Ein Raum ohne eigenen Sensor (Bad mit Comet) bekommt Raum-Soll + festen Ausgleich.
  Fenster offen: Thermostat sofort auf den kleinsten Wert und danach zurück; im Bad (Fühler am Heizkörper) gilt ein
  Fenster 30 Minuten nach einem eigenen Befehl oder nach dem Schließen des Ventils nicht. Eine Handänderung am
  Thermostat wird beim nächsten Durchlauf zurückgesetzt. Raum über Soll + Komfortband + 0,5 K: sofort Soll − 0,5 K
  und die Korrektur einmal 0,5 K tiefer.
- **Wärmeangebot und Umverteilen:** Die App erkennt aus Leistung und Puffer, ob warmes Wasser ankommt (von der
  Wärmepumpe oder aus dem Puffer, mit Restzeit). Ist ein Raum trotz offenem Ventil zu kalt, drosselt sie erst gut
  versorgte Räume; reicht das nicht, zeigt sie **„Vorlauf höher nötig“** – angehoben wird der Vorlauf erst mit den
  Zonen (1.5.0). Die Vorschau des Energiemanagements meldet „Vorlauf höher“ entsprechend erst nach dem Umverteilen.
- **Ventile schonen, im Schreibschutz erzwungen:** Totzone 0,5 K, höchstens alle 15 Minuten und höchstens 24 Befehle
  je Thermostat und Tag (alle einstellbar; Fenster, Sicherheit, Rückweg und Rückfall ausgenommen); Zähler
  überstehen einen Neustart. Ein Wert, der noch zum Thermostat unterwegs ist, gilt schon als dessen Sollwert. Jeder
  Sollwert wird über die Rückmeldung bestätigt; zweimal nicht bestätigt pausiert das Thermostat eine Stunde. Die App
  zählt außerdem die Ventilbewegungen je Tag.
- **Testlauf je Thermostat:** von Hand vorgemerkt, läuft nachts (Start ab 22 Uhr, spätestens 1 Uhr, Ende spätestens
  5 Uhr) und nur mit dem **Fühlerpaar** an Vor- und Rücklauf des Heizkörpers: Sollwert in Stufen hoch bis das Ventil
  offen ist, dann bis 1 K unter die Temperatur am Thermostat, dann zurück; höchstens 16 Befehle je Nacht; Abbruch
  bei Raum über Soll + 1 K, Fenster oder ohne warmes Wasser. Der Rückweg auf den Sollwert von vorher geht immer
  hinaus (auch nach einem Neustart); geht er nicht, setzt die App den Notfallwert. Danach gibt die App das Thermostat
  wieder frei, außer der Raum steht auf „Steuern“. Vollständig ist ein Lauf nur, wenn das Ventil ganz offen und
  wieder zu war. Ergebnis: Ventilverhalten mit Tabelle je Stufe.
- **Absicherung:** Lebenszeichen `sensor.vaillant_eebus_raeume_lebenszeichen` jede Minute (unabhängig von den
  Export-Gruppen); es bleibt stehen, wenn die Raumregelung über 12 Minuten nicht mehr rechnet. Sein Attribut
  `notfallwerte` nennt die Thermostate, die die App gerade gestellt hat. Die **Vorlage der Notfall-Automation** zum
  Kopieren setzt nach 15 Minuten ohne Lebenszeichen genau diese auf ihren Notfallwert (fehlt das Lebenszeichen ganz,
  alle Thermostate der Räume). Verlässt ein Raum „Steuern“, wechselt die App auf Demo, wird sie beendet, wird ein
  Raum entfernt oder bekommt er ein anderes Thermostat, oder meldet ein gestelltes Thermostat eine blockierende
  Warnung, setzt sie selbst den Notfallwert – auch für ein Thermostat, dessen Raum es nicht mehr gibt. Eine
  myVAILLANT-Zone kann kein Raum-Thermostat sein (auch nicht, wenn sie nur so aussieht).
- **Seite Räume:** Karte **Raumregelung** (Stufe, Lebenszeichen, Notfall-Automation, Fühlerpaar mit Meldeabstand
  und Auflösung), je Raum Thermostat-Soll und Ziel, Zustand, Befehle und Ventilbewegungen heute, Warnungen, Testlauf
  und Ventilverhalten; je Heizkörper-Zone Wärmeangebot, Umverteilen und Vorlauf-Hinweis. Im Raum-Editor neu: Grenze
  der Korrektur bzw. fester Ausgleich, Totzone, Abstand und Befehle je Tag.
- **Korrektur aus der Abnahme 1.3.0:** Ein Temperatursensor desselben Zigbee-Geräts wie das Thermostat (etwa
  `…_local_temperature` des Comet) gilt jetzt als „am Thermostat gemessen“ und zählt nicht mehr im Mittelwert der Zone.
- Die Raumproben speichern zusätzlich die Temperatur am Thermostat.

## 1.3.1

**Live-Verbindung erholt sich wieder von selbst.** Brach die Verbindung der Übersicht zur App ab (Neustart oder
Update der App, Neustart von Home Assistant, Ruhezustand), versuchte der Browser es nur einmal neu. Antwortete Home
Assistant in diesem Moment mit einem Fehler (502, solange die App startet; 401 nach abgelaufener Sitzung), gab er
für immer auf: Das Band „Die Live-Verbindung zur App ist unterbrochen …“ blieb stehen, „Stand“ und alle Werte froren
ein, bis man die Seite neu lud.

- Die Oberfläche baut die Verbindung dann selbst neu auf: nach 1, 2, 5 und 10 Sekunden, danach alle 15 Sekunden;
  sofort, wenn die Seite wieder sichtbar wird oder das Netz zurück ist.
- Die App schickt alle 15 Sekunden ein Lebenszeichen, das die Seite sieht (bisher ein Kommentar, den der Browser
  nicht weitergibt). Kommt 45 Sekunden lang nichts an, ersetzt die Seite die Verbindung – etwa nach dem Ruhezustand
  oder einem Wechsel des Netzwerks, wenn die alte Verbindung ohne Fehlermeldung hängt.
- Nach einem Update der App lädt sich eine offene Seite von selbst neu und zeigt die neue Oberfläche. Bei
  ungespeicherten Einstellungen erscheint stattdessen ein Hinweis mit **Seite neu laden**.

## 1.3.0

**Energiemanagement neu, Stufe 1: beobachten, lernen, planen.** Die App lernt Haus und Räume kennen, zählt Takte und
Abtauungen und plant 48 Stunden voraus. In dieser Version zeigt sie nur, was sie tun würde – **sie sendet nichts
Neues an die Wärmepumpe**; das Energiemanagement ruft die Schreibsperre gar nicht auf (Strukturtest). Steuern kommt
mit 1.4.0 (Zonen) und 1.5.0 (Heizkörperthermostate).

- **Prognose 48 h:** Open-Meteo mit den Modellen des Deutschen Wetterdienstes (Temperatur, Feuchte, Taupunkt, Wind,
  Einstrahlung, Bewölkung, Niederschlag), höchstens stündlich und mit auf etwa 1 km gerundetem Standort; die
  Wetter-Entität von Home Assistant ist der Rückfall. Die Prognose wird am Außenfühler der Wärmepumpe geeicht; die
  App zeigt, wie gut die Prognose für den nächsten Tag stimmte.
- **Lernen aus der Langzeitstatistik von Home Assistant** (nur lesen, über die bestehende Verbindung): Wärmeverlust
  des Hauses, Gewinne, Sonne, Verzögerung und Arbeitszahl aus echten Wärme- und Stromwerten; daraus Wärme- und
  Strombedarf je Stunde und Zone. Ein Export aus der myVAILLANT-App ist nicht nötig.
- **Räume:** neue Seite **Räume** mit Raumdaten (Soll, Zeitplan, Komfortband, erlaubter Verlust nachts und gegen
  Abtauen, Notfallwert, Drosseln erlaubt, Thermostat, Ventilstellung, Luftfeuchte und die Werte des statischen
  Abgleichs), Lernstand (Abkühlen, Aufheizen, Anlaufzeit, Ventil), Versorgung und Fenster als „würde“. Ein Raum ohne
  Sensor kann sein Heizkörperthermostat als Temperatur nehmen; es zählt nicht im Mittelwert der Zone.
- **Takten und Abtauen:** Starts, Lauf und Pause sowie Abtauungen (am Muster der Leistung erkannt) heute, der
  letzten Tage und je Außentemperatur; nach einem Start werden die letzten zehn Tage aus dem Verlauf nachgeholt.
  Abtaurisiko je Stunde aus Temperatur und Feuchte, gelernt aus den gezählten Abtauungen.
- **Frostschutz der Wärmepumpe** (länger als 4 h unter 4 °C) wird erkannt, angezeigt und beim Planen beachtet.
- **Neuer Regel-Planer, nur Vorschau:** je Zone und Stunde mit Grund – Vorlauf nach den Ventilstellungen,
  vorausschauend nach Kälte und Sonne, warme Tage, weniger Last in Stunden mit Abtaurisiko (vorher speichern),
  Nachtabsenkung nur wenn sie sich lohnt (mit Schutz vor Schimmel), Blockbetrieb bei mildem Wetter, die
  Zone-1-Logik der bekannten Automation als „würde“. Neue Seite **Plan** (48 h, „Nur Änderungen“ oder „Stündlich“,
  Rückblick 24 h, CSV), auf der Übersicht die Karten **Nächste Änderungen** und **Takten und Abtauen**.
- **Einstellungen Energiemanagement neu:** Stufe Aus/Vorschau („Steuern“ ab 1.4.0), Wetter, Lernquellen, Zonen
  (Art, Auslegung, Vorlauf-Sensoren, Band), Nachtfenster und Verluste, Ziel Starts je Tag, Heizgrenze und – wirksam
  ab 1.4.0 – Quick-Veto und Tagesgrenze für Cloud-Befehle.
- **Sensoren:** `sensor.vaillant_eebus_hems_next_change`, `sensor.vaillant_eebus_starts_today`,
  `sensor.vaillant_eebus_defrosts_today`, `binary_sensor.vaillant_eebus_frost_protection`; `…_hems_plan` zählt jetzt
  die geplanten Änderungen. `…_hems_next_action` und `…_hems_demand_tomorrow` entfallen.
- **Protokoll:** neue Kategorie **Räume** (Fenster und Versorgung als „würde“); unter „Planer“ der Plan in Kurzform,
  erkannte Abtauungen und der Frostschutz.
- **Entfallen:** der Planer aus 1.1/1.2 (Warmwasser und Zonen nach Strompreis, PV-Überschuss und Akku) mit seinen
  Einstellungen; PV kommt später neu. Ein Planer auf „Steuern“ steht nach dem Update auf „Vorschau“. Hatte er beim
  Update noch eine Verschiebung offen, steht ein Hinweis im Protokoll.

## 1.2.1

Korrekturen aus der Abnahme von 1.2.0 an der Anlage (Anbindung myVAILLANT):

- **Außentemperatur:** 1.2.0 hat als Außentemperatur den eigenen Sensor der App
  (`sensor.vaillant_eebus_outdoor_temperature`) vorgeschlagen; die App las dann nur ihren eigenen Wert zurück, und er
  blieb stehen. Die eigenen Sensoren werden nicht mehr vorgeschlagen und nicht mehr angenommen. Ist einer gespeichert,
  entfernt die App ihn beim Start und vermerkt es im Protokoll – bitte unter Einstellungen → Anbindung den Sensor der
  Integration wählen.
- **Rücksprünge der Cloud:** Die Integration zeigt einen gesendeten Wert sofort, fällt kurz auf den alten Wert der
  Cloud zurück und meldet erst danach den neuen. Das stand bisher als „Am Gerät geändert“ im Protokoll, der Regler
  sprang kurz zurück, und der Planer hätte es für eine Änderung von Hand gehalten. Jetzt zeigt die App den gesendeten
  Wert blass, bis er bestätigt ist, und wertet den Rücksprung nicht als Änderung.
- **Übersteuerung beenden:** neuer Knopf unter einer Zone mit laufender zeitweiser Übersteuerung (Quick-Veto).
- **Räume beim Umstellen:** Die Räume nehmen ihre Zone mit (über den Namen, „Zone 1“ zu „Zone 1“), und die
  Zuordnung bleibt je Anbindung gespeichert. Eine Zone, die es nicht mehr gibt, heißt „Zone nicht mehr vorhanden“
  statt „Zone 5.1.1“.
- **Einstellungen:** Der Knopf heißt „Räume, Wärme und Export speichern“; ungespeicherte Änderungen werden angezeigt,
  und beim Verlassen der Seite fragt die App nach.
- **Zone in „Aus“ und Absenkung:** keine „Solltemperatur 0,0 °C“ mehr in Übersicht und Protokoll. In der Absenkung
  des Zeitprogramms („eco“) zeigt die Zone einen Hinweis und die letzte Solltemperatur; der Planer lässt sie in Ruhe.
- **Übersicht mit myVAILLANT:** Zeilen, die die Integration nie liefert (Vorlauf, Rücklauf, Verdichter,
  Energiezähler), sind ausgeblendet.
- Anleitung: 90 Sekunden Bestätigungsfrist mit myVAILLANT, Update, Räume beim Umstellen, Übersteuerung beenden.

## 1.2.0

**Anbindung über myVAILLANT:** Die App erreicht die Wärmepumpe jetzt wahlweise über die Entitäten der
myVAILLANT-Integration in Home Assistant oder wie bisher lokal über EEBUS.

- **Einrichtungsassistent** fragt zuerst nach der Anbindung (myVAILLANT vorausgewählt) und schlägt die Entitäten
  der Integration vor: Zonen, Warmwasser, Außentemperatur und optional die Leistung. Bestehende Installationen
  bleiben bei EEBUS, bis sie umgestellt werden.
- **Einstellungen → Anbindung:** Entitäten ändern und auf den anderen Weg umstellen. Beim Umstellen setzt der Planer
  seine Werte zurück, die App wechselt in den Demo-Betrieb und startet neu. Mit myVAILLANT startet die
  EEBUS-Bridge nicht.
- **Auto-Betrieb:** Mit myVAILLANT ist die Solltemperatur einer Zone im Auto-Betrieb frei; eine Änderung startet
  eine zeitweise Übersteuerung (Quick-Veto). Die Übersicht zeigt „Zeitweise übersteuert bis …“ und in der Kopfzeile,
  wie alt die Werte sind.
- **Energiemanagement:** Mit myVAILLANT verschiebt der Planer Zonen auch im Auto-Betrieb, mit einer Übersteuerung,
  die genau so lange dauert wie der Block und daher von selbst endet. Eine Übersteuerung von Hand pausiert die Zone
  bis zum Blockende.
- **Sicherheit wie bisher:** Gesendet wird nur über die Schreibsperre (Demo-Betrieb, Prüfung gegen die Werte der
  Anlage, höchstens alle 5 Sekunden je Einstellung, Protokoll). Die App ruft nur sechs fest erlaubte Dienste auf
  und nur für die gewählten myVAILLANT-Entitäten. Leistungsdeckel, Failsafe und Verdichter gibt es nur mit EEBUS.
- Anleitung: Abschnitte Anbindung, Einrichtung mit myVAILLANT, Anbindung wechseln.

Korrekturen am Energiemanagement aus einer Prüfung des Codes (vorher als 1.1.1 geplant):

- **Einmalladung:** Wird mitten in einem Warmwasser-Block von Vorschau auf Steuern umgestellt, startet der Planer
  jetzt auch die Einmalladung; bisher ging nur die Solltemperatur hinaus.
- **Änderung bei gestoppter App:** Wurde eine Solltemperatur geändert, während die App nicht lief (zum Beispiel in
  myVAILLANT), wird sie jetzt neuer Grundwert. Bisher hätte der Planer den alten Wert zurückgeschrieben.
- **Beenden:** Der Planer setzt alle verschobenen Werte gleichzeitig zurück und hat dafür bis zu 15 Sekunden. Bisher
  konnte das Herunterfahren das Zurücksetzen abbrechen. Home Assistant wartet beim Stoppen der App jetzt bis zu
  30 Sekunden.
- **Handänderung:** Ein Wert, den der Planer nur vorhatte, aber nie gesendet hat (Demo-Betrieb, abgelehnt), gilt
  nicht mehr als seiner. Stellt ihn jemand von Hand ein, wird er neuer Grundwert.
- **Akku:** Mit Akku-Ladestand sind Kapazität und Ladeleistung jetzt Pflicht. Fehlt die Ladeleistung in älteren
  Einstellungen, nennt die Karte das.
- **Karte Energiemanagement:** lädt neu, sobald die nächste Aktion fällig ist und wenn die Seite wieder sichtbar
  wird.
- **Kopplung:** Beim Wechsel des Gateways wird erst das neue angemeldet, dann das alte abgemeldet. Schlägt das
  Anmelden fehl, bleibt das bisherige gekoppelt.

## 1.1.0

**Energiemanagement (HEMS):** Ein Planer legt Warmwasser und Heizen voraus in PV- und günstige Stunden.

- Plant bis morgen Abend in Viertelstunden: Warmwasser einmal am Tag auf den Höchstwert (Voreinstellung 60 °C) mit
  Einmalladung, wenn PV-Überschuss oder deutlich günstiger Strom erwartet wird; Zonen in „Ein“ vorheizen bzw. bei
  teurem Strom absenken, je Zone einstellbar (Voreinstellung ±1 K), nur unter der Heizgrenze.
- **Strompreis:** Festpreis, dynamisch (`epex_spot`) oder Festpreis mit §14a Modul 3 (Zeitfenster für Hoch- und
  Niedertarif). **PV-Überschuss** aus der PV-Prognose, dem gelernten Hausverbrauch und dem Akku (lädt zuerst).
  **Temperaturprognose** aus einer Wetter-Entität oder von Open-Meteo.
- **Stufen** Aus, Vorschau (sendet nichts, auch im Live-Betrieb) und Steuern (nur nach Bestätigung). Schreibt nur
  über die Schreibsperre: Solltemperaturen und Einmalladung, nie Betriebsarten, Leistungsdeckel oder Verdichter.
  Höchstens eine Änderung je Steuerung alle 30 Minuten.
- **Grundwert:** Eine Änderung von Hand wird neuer Grundwert. Zurück zum Grundwert am Ende jedes Blocks, beim
  Ausschalten, vor dem Demo-Betrieb und beim Beenden der App.
- **Übersicht:** Karte „Energiemanagement“ mit Zeitleiste (PV-Überschuss, Preis, geplante Blöcke) statt der
  gestrichelten Karte. **Einstellungen:** neuer Abschnitt „Energiemanagement“. **Protokoll:** neue Kategorie
  „Planer“. **Export:** neue Gruppe „Energiemanagement“ mit Status, nächster Aktion, Plan und Strombedarf morgen.
- Die App lernt je Tag Strom, Außen- und Innentemperatur und schätzt ab zehn Heiztagen den Strombedarf für morgen
  (nur Anzeige).
- Anleitung: Abschnitt Energiemanagement; Hinweis zum Abrufintervall der myVAILLANT-Integration.

## 1.0.0

Abschluss der geplanten Phasen:

- **Protokoll:** Einträge nach Tagen geordnet, neue erscheinen von selbst. Neuer Filter **Probleme** (abgelehnte,
  nicht ausgeführte oder nicht bestätigte Änderungen, unbekanntes Ergebnis, Verbindungsabbrüche); solche Einträge
  sind rot markiert. **Springen zu** einem Tag und **Herunterladen (CSV)** für Excel. Die Filtertasten sind jetzt
  44 px hoch.
- **Verlauf 24 Stunden:** Die Karte „Leistung und Energie“ zeigt die Leistungsaufnahme der letzten 24 Stunden aus
  dem Verlauf von Home Assistant (5-Minuten-Mittelwerte, Lücken, wo nichts aufgezeichnet ist), mit Wert beim Zeigen
  und Bedienung per Tastatur. Voraussetzung ist der Export „Leistung und Energie“.
- **Einstellungen:** Der Teil „Home Assistant“ zeigt, ob die Verbindung steht. Neue Karte **Über die App** mit
  den Versionen von App und EEBUS-Bridge, Links zu Anleitung, Änderungen und Fehlermeldungen sowie dem
  Lizenzhinweis.
- **Sicherheit:** Die EEBUS-Bridge in der App nimmt Befehle nur noch mit dem eigenen Zertifikat und einem Token
  der App an. Bisher hätte jede andere App mit Host-Netzwerk an der App und ihrem Demo-Betrieb vorbei an die
  Wärmepumpe schreiben können. Zertifikat und Token entstehen beim ersten Start von selbst; nichts einzurichten.
- Übersicht: gestrichelte Karte „PV-Überschuss-Steuerung – folgt mit dem HEMS“.
- Symbol und Logo für den App-Store.
- Anleitung: neue Abschnitte Installation, Update, Fehlerbehebung und Deinstallieren.
- Aufgeräumt: doppelte Stile und ungenutzter Code entfernt.

## 0.6.1

Korrekturen aus der Abnahme von 0.6.0:

- Einstellungen: Die Liste der Räume schiebt die anderen Karten nicht mehr nach unten. Kopplung und Diagnose
  stehen oben, darunter die Räume über die ganze Breite (mehrere nebeneinander), darunter „Wärme heute“ und
  „Export“.
- „Wärme heute“ nimmt bis zu vier Sensoren und addiert sie, etwa Heizung und Warmwasser der
  myVAILLANT-Integration. Mit nur dem Heizungs-Sensor fiel die Arbeitszahl zu niedrig aus. Ein in 0.6.0 gewählter
  Sensor bleibt erhalten.

## 0.6.0

Anbindung an Home Assistant:

- **Räume:** In den Einstellungen wählen Sie Temperatursensoren aus Home Assistant, geben ihnen einen Namen und
  ordnen sie einer Zone zu. Das Rundinstrument jeder Zone zeigt den Mittelwert ihrer Räume; die neue Karte
  „Innentemperaturen“ listet alle Räume. Fehlende oder veraltete Werte (älter als 30 Minuten) werden markiert und
  zählen nicht mit.
- **Wärme heute und Arbeitszahl:** Ein Sensor mit der heute erzeugten Wärme (etwa aus der myVAILLANT-Integration)
  ergibt zusammen mit „Energie heute“ die Arbeitszahl des Tages.
- **Export:** Die App meldet die Werte der Wärmepumpe als Sensoren `sensor.vaillant_eebus_…` an Home Assistant,
  nach Gruppen wählbar, und nach einem Neustart von Home Assistant von selbst neu.
- Die App ruft in Home Assistant keine Dienste auf und ändert keine anderen Entitäten. Ist Home Assistant nicht
  erreichbar, läuft die Steuerung der Wärmepumpe normal weiter.
- Neue Protokoll-Kategorie „Einstellungen“.

## 0.5.3

- Zonen im Auto-Betrieb zeigen wieder die Solltemperatur und den orangen Bogen mit Knopf statt „Zeitprogramm“.
  Verstellen bleibt dort gesperrt; der Hinweis sagt, dass der Wert nur in „Ein“ gilt.

## 0.5.2

Korrekturen aus der ersten Abnahme an der Anlage:

- Den Knopf auf dem Bogen kann man jetzt ziehen (Maus oder Finger), um die Solltemperatur einzustellen; der Bogen
  folgt dem gewählten Wert. Mit der Tastatur gehen die Pfeiltasten.
- Zonen im Auto-Betrieb: Eine über EEBUS gesendete Solltemperatur wirkt dort nicht. Die App zeigt „Zeitprogramm“,
  sperrt die Solltemperatur und verweist für einen Boost auf myVAILLANT bzw. Home Assistant. In „Ein“ bleibt sie
  einstellbar.
- Ruhigere Bedienung: keine kurzen Meldungen mehr nach jeder Eingabe („Wird gleich übernommen“, „Wird gesendet“,
  „Demo: wird nicht gesendet“). Ein noch nicht bestätigter Wert erscheint blass, eine Demo-Vorschau blau. Fehler
  werden weiter angezeigt.
- Schnellere Eingaben: Nach der Quittung der Wärmepumpe kann man sofort weiter einstellen. Wer innerhalb von
  5 Sekunden nachstellt, wird nicht mehr abgewiesen; die App sendet dann, sobald es wieder erlaubt ist.
- Kein Band mehr im Live-Betrieb; der orange Umschalter zeigt ihn an.
- Die Rundinstrumente der Zonen zeigen nicht mehr die unzuverlässige Raumtemperatur des Gateways. Der Mittelwert
  aus Home-Assistant-Sensoren folgt in einer späteren Version.
- Neu: **Energie heute (berechnet)** – der Stromverbrauch seit Mitternacht, aus der gemeldeten Leistung
  aufsummiert, mit dem Hinweis „lückenhaft“, wenn Zeit fehlt.

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
