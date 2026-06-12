import dbus
import time
import logging
import threading
from datetime import datetime, timedelta

# Drempelwaarden
socHardLimit = 71 #10
socSoftLimit = 73 #15
socRecover = 75 #20
tempMax = 35
humidityMax = 70
overrideDuration = 60 #15 * 60

# Alarmcodes
ALARM_SOC_CRITICAL         = 2
ALARM_SOC_CRITICAL_OVERRIDE = 3
ALARM_NO_BMS_COMM          = 4
ALARM_BMS_ALARM            = 5

# Knipperparameters
BLINK_ON_TIME   = 0.2
BLINK_OFF_TIME  = 0.2
BLINK_PAUSE     = 1.0
CYCLE_PAUSE     = 3.0

# Andere constanten en parameters
LED_RELAY       = 2
VEBUS_SERVICE   = 'com.victronenergy.vebus.ttyS4'
BMS_SERVICE     = 'com.victronenergy.battery.socketcan_can1'
MAX_LOGLINES    = 200
LOG_FILE        = '/data/CaerusVision/caerusVision.log'
# BMS alarm paden
BMS_ALARM_PATHS = [
    '/Alarms/LowVoltage',
    '/Alarms/HighCellVoltage',
    '/Alarms/LowTemperature',
    '/Alarms/HighTemperature',
    '/Alarms/HighDischargeCurrent',
    '/Alarms/HighChargeCurrent',
    '/Alarms/HighChargeTemperature',
    '/Alarms/LowChargeTemperature',
    '/Alarms/CellImbalance',
    '/Alarms/InternalFailure',
    '/Alarms/ChargeBlocked',
    '/Alarms/DischargeBlocked',
]

# Gedeelde state
activeAlarms = []
alarmsLock   = threading.Lock()
overrideActive   = False
overrideUntil    = None
buttonWasPressed = False
multiplusShutdown = False

# Logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

# ─── D-Bus hulpfuncties ────────────────────────────────────────────────────────

def waitForService(bus, serviceName, timeout=60):
    logging.info(f"Wachten op {serviceName}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            bus.get_name_owner(serviceName)
            logging.info(f"{serviceName} beschikbaar")
            return True
        except dbus.exceptions.DBusException:
            time.sleep(2)
    logging.error(f"Timeout: {serviceName} niet beschikbaar na {timeout}s")
    return False

def getValue(bus, service, path):
    obj = bus.get_object(service, path)
    return obj.GetValue(dbus_interface='com.victronenergy.BusItem')

def setValue(bus, service, path, value):
    obj = bus.get_object(service, path)
    obj.SetValue(value, dbus_interface='com.victronenergy.BusItem')

def setRelay(bus, relayIndex, state):
    setValue(bus, 'com.victronenergy.system', f'/Relay/{relayIndex}/State', dbus.Int32(state))

def setMultiplus(bus, mode):
    setValue(bus, VEBUS_SERVICE, '/Mode', dbus.Int32(mode))

def readButton(bus):
    val = getValue(bus, 'com.victronenergy.system', '/DigitalInput/1/State')
    return val == 1

def trimLog(logFile, maxLines=200):
    try:
        with open(logFile, 'r') as f:
            lines = f.readlines()
        if len(lines) > maxLines:
            with open(logFile, 'w') as f:
                f.writelines(lines[-maxLines:])
    except Exception as e:
        logging.error(f"Fout bij trimmen log: {e}")

# ─── Alarm beheer ─────────────────────────────────────────────────────────────

def setAlarm(alarmCode):
    with alarmsLock:
        if alarmCode not in activeAlarms:
            activeAlarms.append(alarmCode)
            activeAlarms.sort()
            logging.warning(f"Alarm toegevoegd: {alarmCode}x")

def clearAlarm(alarmCode):
    with alarmsLock:
        if alarmCode in activeAlarms:
            activeAlarms.remove(alarmCode)
            logging.info(f"Alarm gewist: {alarmCode}x")

# ─── Knipperthread ────────────────────────────────────────────────────────────

def blinkThread(bus):
    while True:
        with alarmsLock:
            currentAlarms = list(activeAlarms)

        if not currentAlarms:
            # Normaal bedrijf: lamp continu aan
            setRelay(bus, LED_RELAY-1, 1)
            time.sleep(0.5)
            continue

        # Alarmen actief: lamp eerst uit
        setRelay(bus, LED_RELAY-1, 0)
        time.sleep(0.5)

        for alarmCode in currentAlarms:
            # X keer knipperen
            for _ in range(alarmCode):
                setRelay(bus, LED_RELAY-1, 1)
                time.sleep(BLINK_ON_TIME)
                setRelay(bus, LED_RELAY-1, 0)
                time.sleep(BLINK_OFF_TIME)
            time.sleep(BLINK_PAUSE)

        time.sleep(CYCLE_PAUSE)

# ─── Hoofdloop ────────────────────────────────────────────────────────────────

def mainLoop(bus):
    global overrideActive, overrideUntil, buttonWasPressed, multiplusShutdown
    loopCount = 0

    while True:
        try:
            now = datetime.now()

            # SOC uitlezen
            try:
                soc = getValue(bus, BMS_SERVICE, '/Soc')
                clearAlarm(ALARM_NO_BMS_COMM)
            except dbus.exceptions.DBusException:
                setAlarm(ALARM_NO_BMS_COMM)
                logging.error("Geen BMS communicatie")
                time.sleep(5)
                continue

            # BMS alarmen uitlezen
            try:
                bmsAlarmActive = any(
                    getValue(bus, BMS_SERVICE, path) != 0
                    for path in BMS_ALARM_PATHS
                )
                if bmsAlarmActive:
                    setAlarm(ALARM_BMS_ALARM)
                else:
                    clearAlarm(ALARM_BMS_ALARM)
            except dbus.exceptions.DBusException:
                pass

            # Knop detectie
            try:
                buttonPressed = readButton(bus)
                if buttonPressed and not buttonWasPressed:
                    overrideActive = True
                    overrideUntil = now + timedelta(seconds=overrideDuration)
                    logging.warning(f"Override geactiveerd tot {overrideUntil.strftime('%H:%M:%S')}")
                    if multiplusShutdown:
                        setMultiplus(bus, 3)
                        multiplusShutdown = False
                        logging.warning("Multiplus terug aan via override")
                buttonWasPressed = buttonPressed
            except dbus.exceptions.DBusException:
                pass

            # Override verlopen
            if overrideActive and now >= overrideUntil:
                overrideActive = False
                logging.info("Override verlopen")

            # SOC bewaking
            if not overrideActive:
                if soc <= socHardLimit and not multiplusShutdown:
                    setMultiplus(bus, 4)
                    multiplusShutdown = True
                    setAlarm(ALARM_SOC_CRITICAL)
                    clearAlarm(ALARM_SOC_CRITICAL_OVERRIDE)
                    logging.warning(f"Multiplus uitgeschakeld op SOC {soc}%")

                elif soc <= socSoftLimit:
                    logging.warning(f"SOC laag: {soc}%")
dbus -y com.victronenergy.vebus.ttdbus -y | grep digitalinput^C
root@einstein:/data/CaerusVision# cat batteryGuard.py
import dbus
import time
import logging
import threading
from datetime import datetime, timedelta

# Drempelwaarden
socHardLimit = 71 #10
socSoftLimit = 73 #15
socRecover = 75 #20
tempMax = 35
humidityMax = 70
overrideDuration = 60 #15 * 60

# Alarmcodes
ALARM_SOC_CRITICAL         = 2
ALARM_SOC_CRITICAL_OVERRIDE = 3
ALARM_NO_BMS_COMM          = 4
ALARM_BMS_ALARM            = 5

# Knipperparameters
BLINK_ON_TIME   = 0.2
BLINK_OFF_TIME  = 0.2
BLINK_PAUSE     = 1.0
CYCLE_PAUSE     = 3.0

# Andere constanten en parameters
LED_RELAY       = 2
VEBUS_SERVICE   = 'com.victronenergy.vebus.ttyS4'
BMS_SERVICE     = 'com.victronenergy.battery.socketcan_can1'
MAX_LOGLINES    = 200
LOG_FILE        = '/data/CaerusVision/caerusVision.log'
# BMS alarm paden
BMS_ALARM_PATHS = [
    '/Alarms/LowVoltage',
    '/Alarms/HighCellVoltage',
    '/Alarms/LowTemperature',
    '/Alarms/HighTemperature',
    '/Alarms/HighDischargeCurrent',
    '/Alarms/HighChargeCurrent',
    '/Alarms/HighChargeTemperature',
    '/Alarms/LowChargeTemperature',
    '/Alarms/CellImbalance',
    '/Alarms/InternalFailure',
    '/Alarms/ChargeBlocked',
    '/Alarms/DischargeBlocked',
]

# Gedeelde state
activeAlarms = []
alarmsLock   = threading.Lock()
overrideActive   = False
overrideUntil    = None
buttonWasPressed = False
multiplusShutdown = False

# Logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

# ─── D-Bus hulpfuncties ────────────────────────────────────────────────────────

def waitForService(bus, serviceName, timeout=60):
    logging.info(f"Wachten op {serviceName}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            bus.get_name_owner(serviceName)
            logging.info(f"{serviceName} beschikbaar")
            return True
        except dbus.exceptions.DBusException:
            time.sleep(2)
    logging.error(f"Timeout: {serviceName} niet beschikbaar na {timeout}s")
    return False

def getValue(bus, service, path):
    obj = bus.get_object(service, path)
    return obj.GetValue(dbus_interface='com.victronenergy.BusItem')

def setValue(bus, service, path, value):
    obj = bus.get_object(service, path)
    obj.SetValue(value, dbus_interface='com.victronenergy.BusItem')

def setRelay(bus, relayIndex, state):
    setValue(bus, 'com.victronenergy.system', f'/Relay/{relayIndex}/State', dbus.Int32(state))

def setMultiplus(bus, mode):
    setValue(bus, VEBUS_SERVICE, '/Mode', dbus.Int32(mode))

def readButton(bus):
    val = getValue(bus, 'com.victronenergy.system', '/DigitalInput/1/State')
    return val == 1

def trimLog(logFile, maxLines=200):
    try:
        with open(logFile, 'r') as f:
            lines = f.readlines()
        if len(lines) > maxLines:
            with open(logFile, 'w') as f:
                f.writelines(lines[-maxLines:])
    except Exception as e:
        logging.error(f"Fout bij trimmen log: {e}")

# ─── Alarm beheer ─────────────────────────────────────────────────────────────

def setAlarm(alarmCode):
    with alarmsLock:
        if alarmCode not in activeAlarms:
            activeAlarms.append(alarmCode)
            activeAlarms.sort()
            logging.warning(f"Alarm toegevoegd: {alarmCode}x")

def clearAlarm(alarmCode):
    with alarmsLock:
        if alarmCode in activeAlarms:
            activeAlarms.remove(alarmCode)
            logging.info(f"Alarm gewist: {alarmCode}x")

# ─── Knipperthread ────────────────────────────────────────────────────────────

def blinkThread(bus):
    while True:
        with alarmsLock:
            currentAlarms = list(activeAlarms)

        if not currentAlarms:
            # Normaal bedrijf: lamp continu aan
            setRelay(bus, LED_RELAY-1, 1)
            time.sleep(0.5)
            continue

        # Alarmen actief: lamp eerst uit
        setRelay(bus, LED_RELAY-1, 0)
        time.sleep(0.5)

        for alarmCode in currentAlarms:
            # X keer knipperen
            for _ in range(alarmCode):
                setRelay(bus, LED_RELAY-1, 1)
                time.sleep(BLINK_ON_TIME)
                setRelay(bus, LED_RELAY-1, 0)
                time.sleep(BLINK_OFF_TIME)
            time.sleep(BLINK_PAUSE)

        time.sleep(CYCLE_PAUSE)

# ─── Hoofdloop ────────────────────────────────────────────────────────────────

def mainLoop(bus):
    global overrideActive, overrideUntil, buttonWasPressed, multiplusShutdown
    loopCount = 0

    while True:
        try:
            now = datetime.now()

            # SOC uitlezen
            try:
                soc = getValue(bus, BMS_SERVICE, '/Soc')
                clearAlarm(ALARM_NO_BMS_COMM)
            except dbus.exceptions.DBusException:
                setAlarm(ALARM_NO_BMS_COMM)
                logging.error("Geen BMS communicatie")
                time.sleep(5)
                continue

            # BMS alarmen uitlezen
            try:
                bmsAlarmActive = any(
                    getValue(bus, BMS_SERVICE, path) != 0
                    for path in BMS_ALARM_PATHS
                )
                if bmsAlarmActive:
                    setAlarm(ALARM_BMS_ALARM)
                else:
                    clearAlarm(ALARM_BMS_ALARM)
            except dbus.exceptions.DBusException:
                pass

            # Knop detectie
            try:
                buttonPressed = readButton(bus)
                if buttonPressed and not buttonWasPressed:
                    overrideActive = True
                    overrideUntil = now + timedelta(seconds=overrideDuration)
                    logging.warning(f"Override geactiveerd tot {overrideUntil.strftime('%H:%M:%S')}")
                    if multiplusShutdown:
                        setMultiplus(bus, 3)
                        multiplusShutdown = False
                        logging.warning("Multiplus terug aan via override")
                buttonWasPressed = buttonPressed
            except dbus.exceptions.DBusException:
                pass

            # Override verlopen
            if overrideActive and now >= overrideUntil:
                overrideActive = False
                logging.info("Override verlopen")

            # SOC bewaking
            if not overrideActive:
                if soc <= socHardLimit and not multiplusShutdown:
                    setMultiplus(bus, 4)
                    multiplusShutdown = True
                    setAlarm(ALARM_SOC_CRITICAL)
                    clearAlarm(ALARM_SOC_CRITICAL_OVERRIDE)
                    logging.warning(f"Multiplus uitgeschakeld op SOC {soc}%")

                elif soc <= socSoftLimit:
                    logging.warning(f"SOC laag: {soc}%")

                elif soc >= socRecover and multiplusShutdown:
                    setMultiplus(bus, 3)
                    multiplusShutdown = False
                    clearAlarm(ALARM_SOC_CRITICAL)
                    logging.info(f"Multiplus terug aan op SOC {soc}%")

            else:
                # Override actief
                if soc <= socHardLimit:
                    setAlarm(ALARM_SOC_CRITICAL_OVERRIDE)
                    clearAlarm(ALARM_SOC_CRITICAL)
                else:
                    clearAlarm(ALARM_SOC_CRITICAL_OVERRIDE)

            logging.info(f"SOC: {soc}% | Override: {overrideActive} | Shutdown: {multiplusShutdown} | Alarmen: {activeAlarms}")

        except Exception as e:
            logging.error(f"Fout in hoofdloop: {e}")

        loopCount += 1
        if loopCount % 100 == 0:
            trimLog(LOG_FILE, MAX_LOGLINES)
            loopCount = 0

        time.sleep(5)

# ─── Opstart ──────────────────────────────────────────────────────────────────

bus = dbus.SystemBus()

if not waitForService(bus, 'com.victronenergy.system'):
    logging.error("com.victronenergy.system niet beschikbaar, stoppen")
    exit(1)

if not waitForService(bus, BMS_SERVICE):
    logging.error("BMS niet beschikbaar, stoppen")
    exit(1)

# 3x knipperen als opstartsignaal
for _ in range(3):
    setRelay(bus, LED_RELAY-1, 1)
    time.sleep(BLINK_ON_TIME)
    setRelay(bus, LED_RELAY-1, 0)
    time.sleep(BLINK_OFF_TIME)
time.sleep(CYCLE_PAUSE)

# Knipperthread starten
blinker = threading.Thread(target=blinkThread, args=(bus,), daemon=True)
blinker.start()
logging.info("CaerusVision Battery Guard gestart")

# Hoofdloop
mainLoop(bus)