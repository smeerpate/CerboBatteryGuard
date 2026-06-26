#!/usr/bin/env python3
"""
CaerusVision D-Bus service.

Publishes batteryGuard.py's state machine status and a handful of
telemetry values on their own D-Bus service, so a gui-v2 UI Plugin page
(Settings -> Integrations -> CaerusVision) can show and control them on
the Cerbo's local screen.

DRAFT - checked against the actual vedbus.py you provided (one fix
applied: SetValue re-applies the requested value right after the
onchangecallback returns, so /OverrideActive resets itself via
GLib.idle_add rather than from inside the callback). Still not tested
on real hardware.

Import this from batteryGuard.py, don't run it standalone.
"""

import sys
import logging

from gi.repository import GLib

# vedbus.py / ve_utils.py already ship with Venus OS as part of
# dbus-systemcalc-py. Reuse them instead of vendoring our own copy.
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
from vedbus import VeDbusService  # noqa: E402

logger = logging.getLogger(__name__)

# Deliberately NOT a recognised Venus OS device class (battery, vebus,
# multi, settings, system, tank, ...) so systemcalc / the Device List
# won't try to aggregate this as a real battery/inverter. Compare to
# "com.victronenergy.example" used in Victron's own velib_python demo.
SERVICE_NAME = 'com.victronenergy.batteryguard'


class CaerusVisionDbusService:
    """Thin wrapper around VeDbusService exposing batteryGuard's state."""

    def __init__(self, dbusLock, onOverrideRequested):
        """
        dbusLock: the same threading.Lock() batteryGuard.py already uses
                  around D-Bus getValue/setValue calls.
        onOverrideRequested: callable(bool) -> bool, invoked when someone
                  toggles /OverrideActive from the GUI plugin page. Return
                  True to accept the new value, False to reject it.
        """
        self._dbusLock = dbusLock
        self._onOverrideRequested = onOverrideRequested

        self._dbusservice = VeDbusService(SERVICE_NAME, register=False)

        # --- Mandatory paths (Venus OS dbus-api) ---
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path('/Mgmt/ProcessVersion', '1.0')
        self._dbusservice.add_path('/Mgmt/Connection', 'CaerusVision batteryGuard')
        self._dbusservice.add_path('/DeviceInstance', 0)
        self._dbusservice.add_path('/ProductId', 0xFFFF)
        self._dbusservice.add_path('/ProductName', 'CaerusVision BatteryGuard')
        self._dbusservice.add_path('/FirmwareVersion', 1)
        self._dbusservice.add_path('/HardwareVersion', 0)
        self._dbusservice.add_path('/Connected', 1)

        # --- Custom telemetry paths (read-only from the GUI's side) ---
        # Path names follow Victron's PascalCase dbus-api convention,
        # not the camelCase used for Python identifiers in this project.
        self._dbusservice.add_path('/State', 'INIT')
        self._dbusservice.add_path('/Soc', 0.0)
        self._dbusservice.add_path('/SocHardLimit', 0.0)
        self._dbusservice.add_path('/SocSoftLimit', 0.0)

        # --- Writable path: GUI button writes 1 to request an override ---
        self._dbusservice.add_path(
            '/OverrideActive',
            0,
            writeable=True,
            onchangecallback=self._handleOverrideChanged,
        )

        self._dbusservice.register()
        logger.info('%s registered on D-Bus', SERVICE_NAME)

    def _handleOverrideChanged(self, path, value):
        """Called by vedbus when the GUI plugin writes to /OverrideActive."""
        with self._dbusLock:
            accepted = self._onOverrideRequested(bool(value))

        if value:
            # /OverrideActive is a momentary trigger, not a persistent
            # flag. VeDbusItemExport.SetValue only calls this callback
            # when the new value actually differs from the current one,
            # and unconditionally re-applies the requested value right
            # after this callback returns (overwriting any reset done
            # here). So schedule the reset to 0 for just after that,
            # on the GLib main loop - otherwise a second press of the
            # same button (writing 1 again) would silently be ignored.
            GLib.idle_add(self._resetOverrideTrigger)

        return accepted

    def _resetOverrideTrigger(self):
        with self._dbusLock:
            self._dbusservice['/OverrideActive'] = 0
        return False  # don't repeat this idle callback

    def updateState(self, stateName):
        with self._dbusLock:
            self._dbusservice['/State'] = stateName

    def updateTelemetry(self, soc=None, socHardLimit=None, socSoftLimit=None):
        with self._dbusLock:
            if soc is not None:
                self._dbusservice['/Soc'] = soc
            if socHardLimit is not None:
                self._dbusservice['/SocHardLimit'] = socHardLimit
            if socSoftLimit is not None:
                self._dbusservice['/SocSoftLimit'] = socSoftLimit
