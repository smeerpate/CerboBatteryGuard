# CerboBatteryGuard

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
