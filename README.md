# CerboBatteryGuard
## Wat doet CerboBatteryGuard?
CerboBatteryGuard is een Python service die draait op een Victron Cerbo GX en de laadtoestand (State of Charge, SOC) van een LiFePO4 batterijpakket bewaakt. Wanneer de SOC onder een kritische drempel zakt, schakelt de service de Victron Multiplus omvormer automatisch uit om diepontlading en blijvende schade aan de batterijen te voorkomen.

## Waarom is dit nodig?
Victron DVCC (Distributed Voltage and Current Control) laat het BMS toe om via CAN bus laad- en ontlaadstroomlimieten (CCL/DCL) door te geven aan de omvormer. Dit zijn stroomlimieten, geen SOC-limieten. Sommige BMS-systemen ondersteunen deze CCL/DCL communicatie niet. Zonder CCL/DCL kan DVCC zijn rol als beschermingslaag niet vervullen, maar dat lost het probleem van diepontlading nog steeds niet op: er is bij gebruik van DVCC geen ingebouwd Victron mechanisme dat de omvormer automatisch uitschakelt wanneer de SOC een kritische grens bereikt. CerboBatteryGuard vult precies dit gat op door de SOC continu te bewaken en de Multiplus uit te schakelen wanneer nodig.

## Hoe werkt het?
De service leest elke 5 seconden de SOC uit via D-Bus van het BMS, en stuurt de Multiplus aan via VE.Bus. Er zijn twee drempelwaarden:

- Soft limit: de SOC daalt onder dit niveau, er wordt een waarschuwing gelogd maar de omvormer blijft aan.
- Hard limit: de SOC daalt onder dit niveau, de omvormer wordt uitgeschakeld. Hij komt pas terug aan wanneer de SOC de hersteldrempel overschrijdt.

Een fysieke drukknop op de Cerbo GX laat toe om de uitschakeling tijdelijk te overbruggen (override), bijvoorbeeld om in noodgevallen toch nog belasting te kunnen aansluiten. De override is tijdelijk en verloopt automatisch na een instelbare duur.
De status wordt zichtbaar gemaakt via een LED die op een relay van de Cerbo GX is aangesloten. Bij normaal bedrijf brandt de LED continu. Bij een alarm knippert de LED een aantal keer dat overeenkomt met de alarmcode, zodat de toestand van het systeem ook zonder toegang tot de logs afgelezen kan worden.

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
