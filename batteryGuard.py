import dbus
import time
import logging
from logging.handlers import RotatingFileHandler
import threading
from datetime import datetime, timedelta

# Drempelwaarden
socHardLimit = 10
socSoftLimit = 15
socRecover = 20
tempMax = 35
humidityMax = 70
overrideDuration = 15 * 60

# Alarmcodes
ALARM_SOC_CRITICAL         = 2
ALARM_SOC_CRITICAL_OVERRIDE = 3
ALARM_NO_BMS_COMM          = 4
ALARM_BMS_ALARM            = 5

# Victron Multiplus II modes
MP2_CHARGER_ONLY = 1
MP2_INVERTER_ONLY = 2
MP2_ON = 3
MP2_OFF = 4

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
MAX_LOGBYTES    = 20000 # 20kB ongeveer 200 lijnen

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
lastLogState = None

# Logging
handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=MAX_LOGBYTES,
    backupCount=1 # 1 bestand in backup houden
)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(handlers=[handler], level=logging.INFO)

# DBUS thread safety (voor get Value en setValue)
dbusLock = threading.Lock()

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
    with dbusLock:
        obj = bus.get_object(service, path)
        return obj.GetValue(dbus_interface='com.victronenergy.BusItem')

def setValue(bus, service, path, value):
    with dbusLock:
        obj = bus.get_object(service, path)
        obj.SetValue(value, dbus_interface='com.victronenergy.BusItem')

def setRelay(bus, relayIndex, state):
    setValue(bus, 'com.victronenergy.system', f'/Relay/{relayIndex}/State', dbus.Int32(state))

def setMultiplus(bus, mode):
    setValue(bus, VEBUS_SERVICE, '/Mode', dbus.Int32(mode))

def readButton():
    try:
        with open('/dev/gpio/digital_input_1/value', 'r') as f:
            val = f.read().strip()
        return val == '0'   # actief laag: ingedrukt = 0
    except (FileNotFoundError, OSError) as e:
        logging.error(f"Fout bij lezen digitale input: {e}")
        return False

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
STATE_INIT     = 'INIT'
STATE_NORMAL   = 'NORMAL'
STATE_SOC_LOW  = 'SOC_LOW'
STATE_SHUTDOWN = 'SHUTDOWN'
STATE_OVERRIDE = 'OVERRIDE'

def mainLoop(bus):
    global overrideActive, overrideUntil, buttonWasPressed, multiplusShutdown, acConnected

    state = STATE_INIT
    lastLogState = None

    while True:
        try:
            now = datetime.now()

            # ── SOC uitlezen ──────────────────────────────────────────────────
            try:
                soc = getValue(bus, BMS_SERVICE, '/Soc')
                clearAlarm(ALARM_NO_BMS_COMM)
            except dbus.exceptions.DBusException:
                setAlarm(ALARM_NO_BMS_COMM)
                logging.error("Geen BMS communicatie")
                time.sleep(5)
                continue

            # ── BMS alarmen ───────────────────────────────────────────────────
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

            # ── Knop detectie ─────────────────────────────────────────────────
            try:
                buttonPressed = readButton()
                if buttonPressed and not buttonWasPressed:
                    if state == STATE_SHUTDOWN:
                        overrideUntil = now + timedelta(seconds=overrideDuration)
                        state = STATE_OVERRIDE
                        setMultiplus(bus, MP2_ON)
                        multiplusShutdown = False
                        logging.warning(f"Override geactiveerd tot {overrideUntil.strftime('%H:%M:%S')}")
                buttonWasPressed = buttonPressed
            except dbus.exceptions.DBusException:
                pass

            # ── AC-ingang bewaking ────────────────────────────────────────────
            try:
                acNowConnected = getValue(bus, VEBUS_SERVICE, '/Ac/ActiveIn/Connected') == 1
                if acConnected is not None and acNowConnected != acConnected:
                    if acNowConnected:
                        logging.info("Netspanning aangesloten")
                    else:
                        logging.warning("Netspanning weggevallen")
                acConnected = acNowConnected
            except dbus.exceptions.DBusException:
                pass

            # ── State machine ─────────────────────────────────────────────────
            if state == STATE_INIT:
                if soc <= socHardLimit:
                    state = STATE_SHUTDOWN
                    setMultiplus(bus, MP2_CHARGER_ONLY)
                    multiplusShutdown = True
                    setAlarm(ALARM_SOC_CRITICAL)
                    logging.warning(f"Opstart: Multiplus uitgeschakeld op SOC {soc}%")
                elif soc <= socSoftLimit:
                    state = STATE_SOC_LOW
                    setMultiplus(bus, MP2_ON)
                    multiplusShutdown = False
                    logging.warning(f"Opstart: SOC laag ({soc}%), Multiplus aan")
                else:
                    state = STATE_NORMAL
                    setMultiplus(bus, MP2_ON)
                    multiplusShutdown = False
                    logging.info(f"Opstart: SOC normaal ({soc}%), Multiplus aan")

            elif state == STATE_NORMAL:
                if soc <= socHardLimit:
                    state = STATE_SHUTDOWN
                    setMultiplus(bus, MP2_CHARGER_ONLY)
                    multiplusShutdown = True
                    setAlarm(ALARM_SOC_CRITICAL)
                    logging.warning(f"Multiplus uitgeschakeld op SOC {soc}%")
                elif soc <= socSoftLimit:
                    state = STATE_SOC_LOW
                    logging.warning(f"SOC laag: {soc}%")

            elif state == STATE_SOC_LOW:
                if soc <= socHardLimit:
                    state = STATE_SHUTDOWN
                    setMultiplus(bus, MP2_CHARGER_ONLY)
                    multiplusShutdown = True
                    setAlarm(ALARM_SOC_CRITICAL)
                    logging.warning(f"Multiplus uitgeschakeld op SOC {soc}%")
                elif soc > socSoftLimit:
                    state = STATE_NORMAL
                    logging.info(f"SOC hersteld: {soc}%")

            elif state == STATE_SHUTDOWN:
                if soc >= socRecover:
                    state = STATE_NORMAL
                    setMultiplus(bus, MP2_ON)
                    multiplusShutdown = False
                    clearAlarm(ALARM_SOC_CRITICAL)
                    logging.info(f"Multiplus terug aan op SOC {soc}%")

            elif state == STATE_OVERRIDE:
                setAlarm(ALARM_SOC_CRITICAL_OVERRIDE)
                clearAlarm(ALARM_SOC_CRITICAL)
                if now >= overrideUntil:
                    logging.info("Override verlopen")
                    if soc <= socHardLimit:
                        state = STATE_SHUTDOWN
                        setMultiplus(bus, MP2_CHARGER_ONLY)
                        multiplusShutdown = True
                        setAlarm(ALARM_SOC_CRITICAL)
                        clearAlarm(ALARM_SOC_CRITICAL_OVERRIDE)
                        logging.warning(f"Multiplus uitgeschakeld na override op SOC {soc}%")
                    else:
                        state = STATE_NORMAL
                        clearAlarm(ALARM_SOC_CRITICAL_OVERRIDE)

            # ── Logging bij verandering ───────────────────────────────────────
            currentLogState = (round(soc, 0), state, sorted(activeAlarms))
            if currentLogState != lastLogState:
                logging.info(f"SOC: {soc}% | State: {state} | Alarmen: {activeAlarms}")
                lastLogState = currentLogState

        except Exception as e:
            logging.error(f"Fout in hoofdloop: {e}")

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
