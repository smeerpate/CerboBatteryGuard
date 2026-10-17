# CerboBatteryGuard
## Wat doet CerboBatteryGuard?
CerboBatteryGuard is een Python service die draait op een Victron Cerbo GX en de laadtoestand (State of Charge, SOC) van een LiFePO4 batterijpakket bewaakt. Wanneer de SOC onder een kritische drempel zakt, schakelt de service de Victron Multiplus omvormer automatisch uit om diepontlading en blijvende schade aan de batterijen te voorkomen.

## Waarom is dit nodig?
Victron DVCC (Distributed Voltage and Current Control) laat het BMS toe om via CAN bus laad- en ontlaadstroomlimieten (CCL/DCL) door te geven aan de omvormer. Dit zijn stroomlimieten, geen SOC-limieten. Sommige BMS-systemen ondersteunen deze CCL/DCL communicatie niet. Zonder CCL/DCL kan DVCC zijn rol als beschermingslaag niet vervullen, maar dat lost het probleem van diepontlading nog steeds niet op: er is bij gebruik van DVCC geen ingebouwd Victron mechanisme dat de omvormer automatisch uitschakelt wanneer de SOC een kritische grens bereikt. CerboBatteryGuard vult precies dit gat op door de SOC continu te bewaken en de Multiplus uit te schakelen wanneer nodig.

## Hoe werkt het?
De service leest elke 5 seconden de SOC uit via D-Bus van het BMS, en stuurt de Multiplus aan via VE.Bus. Er zijn twee drempelwaarden:

- Soft limit: de SOC daalt onder dit niveau, er wordt een waarschuwing gelogd maar de omvormer blijft aan.
- Hard limit: de SOC daalt onder dit niveau, de omvormer wordt uitgeschakeld. Hij komt pas terug aan wanneer de SOC de hersteldrempel overschrijdt.

Een fysieke drukknop op de Cerbo GX laat toe om de uitschakeling tijdelijk te overbruggen (override), bijvoorbeeld om in noodgevallen toch nog belasting te kunnen aansluiten. Zie [Override](#override).

De status wordt zichtbaar gemaakt via een LED op een relais van de Cerbo GX, zodat de toestand van het systeem ook zonder toegang tot de logs afgelezen kan worden. Zie [Statusled](#statusled).

## Statusled
De LED is aangesloten op Relay2 van de Cerbo GX (D-Bus `/Relay/1`). Het relais moet in de Cerbo-instellingen op functie *Manual* staan.

### Opstart
Bij het starten van de service licht de LED 3 keer kort op, gevolgd door een pauze van 3 seconden.

### Normaal bedrijf
De LED is uit. Om de 60 seconden licht ze 1 keer kort op als hartslag. Zo is te zien dat de service nog draait. Een LED die langer dan een minuut volledig uit blijft, wijst op een gestopte service of een Cerbo zonder voeding.

### Alarmen
Bij een actief alarm licht de LED per alarm een aantal keer kort op (0,2 s aan, 0,2 s uit). Het aantal is de alarmcode:

| Aantal | Alarm |
|---|---|
| 2x | SOC kritisch: SOC onder de hard limit, omvormer uitgeschakeld (alleen laden) |
| 3x | SOC kritisch met override actief: omvormer tijdelijk terug aan via de drukknop |
| 4x | Geen communicatie met het BMS |
| 5x | Alarm gemeld door het BMS |
| 6x | Rookdetector (DIN3) |

Zijn er meerdere alarmen tegelijk actief, dan worden ze na elkaar getoond in oplopende volgorde, met 1 seconde pauze tussen de alarmen en 3 seconden pauze na de volledige reeks. Daarna begint de reeks opnieuw.

Een nieuw of gewist alarm wordt pas zichtbaar na de lopende reeks.

### Fouten
Als het aansturen van het relais mislukt (bijvoorbeeld tijdens een herstart van de Victron-services), wordt dit één keer gelogd en probeert de service het elke 2 seconden opnieuw. Zodra het weer lukt, verschijnt "Knipperthread hersteld" in de log.

Als de service stopt of crasht, blijft het relais in zijn laatste stand staan.

## Override
Wanneer de omvormer uitgeschakeld is door een kritische SOC, kan hij met een drukknop tijdelijk terug aangezet worden.

### Parameters
De parameters staan bovenaan `batteryGuard.py`:

| Parameter | Waarde | Betekenis |
|---|---|---|
| `overrideDuration` | 900 s (15 min) | Duur van de override |
| `socHardLimit` | 10 % | SOC waaronder de omvormer uitgeschakeld wordt (alleen laden) |
| `socRecover` | 20 % | SOC waarboven de omvormer zonder override automatisch terug aan gaat |

De drukknop is aangesloten op digitale ingang 1 (DIN1) van de Cerbo GX en is actief laag: ingedrukt is `0`.

### Werking
- De knop werkt alleen wanneer de omvormer uitgeschakeld is door een kritische SOC (status SHUTDOWN). In andere toestanden heeft de knop geen effect.
- Na een druk op de knop gaat de omvormer aan voor de duur van `overrideDuration`. De LED toont dan alarmcode 3x.
- Tijdens de override wordt de SOC niet bewaakt. De batterij kan in die periode verder ontladen onder `socHardLimit`.
- Opnieuw drukken tijdens een lopende override verlengt de override niet.
- De knop wordt om de 5 seconden uitgelezen. Hou de knop ongeveer 5 seconden ingedrukt, anders kan een korte druk gemist worden.

### Na afloop van de override
- SOC ≤ `socHardLimit`: de omvormer wordt opnieuw uitgeschakeld (alleen laden). De LED toont terug 2x.
- SOC > `socHardLimit`: de omvormer blijft aan en de service keert terug naar normaal bedrijf, ook als de SOC nog onder `socRecover` ligt. Zakt de SOC daarna terug tot `socHardLimit`, dan wordt de omvormer opnieuw uitgeschakeld.

### Handmatige schakelaar
Staat de handmatige schakelaar (DIN2) op "omvormer uit", dan heeft de override geen effect: de omvormer blijft in alleen-laden.

## Initscipt
### Werking
- **start**: wacht 10 seconden voor het de Python service opstart. Die vertraging is nodig omdat Venus OS bij het booten tijd nodig heeft om de D-Bus services ('com.victronenergy.system', BMS) beschikbaar te stellen. De Python process wordt als achtergrondproces gestart (&) zodat het init script zelf meteen terugkeert. Zowel stdout als stderr van het Python process worden omgeleid naar het logbestand (>> $LOG 2>&1).

- **stop**: zoekt het proces op via pgrep -f batteryGuard.py en stuurt een SIGTERM signaal via kill. Dit geeft het proces de kans om netjes af te sluiten.

- **restart**: roept gewoon stop op, wacht één seconde, en roept daarna start op.

### Hoe het samenwerkt met Venus OS
Venus OS heeft een ingebouwd mechanisme om custom scripts automatisch uit te voeren bij elke opstart.

Het systeem zoekt bij het booten naar een bestand met de naam initscript op de persistente /data/ partitie. Als dat bestand aanwezig en uitvoerbaar is, wordt het automatisch aangeroepen met het argument start.
Om de service automatisch te laten opstarten volstaat het om het script op de juiste plaats te zetten en uitvoerbaar te maken:

    chmod +x /data/initscript

De /data/ partitie is persistent en overleeft firmware-updates van Venus OS, waardoor de service ook na een update automatisch blijft opstarten.
