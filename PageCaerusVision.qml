/*
** CaerusVision status & control page - gui-v2 UI Plugin.
**
** DRAFT - cross-check against the live examples on the device/repo before
** compiling:
**   /opt/victronenergy/gui-v2/examples
**   pages/settings/debug/PageSettingsDemo.qml
**
** Note: /OverrideActive is a one-shot trigger, not a persistent on/off
** state (matches batteryGuard.py's button-triggered override, which only
** works from STATE_SHUTDOWN and has no remote cancel). Hence a ListButton
** writing through VeQuickItem.setValue(), not a ListSwitch.
**
** Compile with (on the Cerbo, from this file's folder):
**   python3 /opt/victronenergy/gui-v2/gui-v2-plugin-compiler.py \
**     --name CaerusVision \
**     --min-required-version v1.2.13 \
**     --settings PageCaerusVision.qml
*/

import QtQuick
import QtQuick.Layouts
import Victron.VenusOS

Page {
    id: root

    property string serviceUid: "com.victronenergy.batteryguard"

    GradientListView {
        model: VisibleItemModel {

            PrimaryListLabel {
                text: "CaerusVision battery protection status"
            }

            ListText {
                text: "State"
                dataItem.uid: root.serviceUid + "/State"
            }

            ListQuantityGroup {
                text: "Battery info"
                model: QuantityObjectModel {
                    QuantityObject { object: socItem; unit: VenusOS.Units_Percentage }
                    QuantityObject { object: hardLimitItem; unit: VenusOS.Units_Percentage }
                    QuantityObject { object: softLimitItem; unit: VenusOS.Units_Percentage }
                }
            }

            VeQuickItem {
                id: socItem
                uid: root.serviceUid + "/Soc"
            }
            VeQuickItem {
                id: hardLimitItem
                uid: root.serviceUid + "/SocHardLimit"
            }
            VeQuickItem {
                id: softLimitItem
                uid: root.serviceUid + "/SocSoftLimit"
            }

            ListButton {
                text: "Forceer override"
                secondaryText: "Enkel mogelijk vanuit status SHUTDOWN"
                onClicked: {
                    overrideTrigger.setValue(1)
                    Global.showToastNotification(VenusOS.Notification_Info, "Override aangevraagd")
                }
            }

            VeQuickItem {
                id: overrideTrigger
                uid: root.serviceUid + "/OverrideActive"
            }
        }
    }
}
