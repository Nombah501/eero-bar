pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui
import "EeroModel.js" as EeroModel

// One row in the popup's "Clients" section: wifi/ethernet glyph, name, ip,
// node name, a guest badge, and a Pause ToggleSwitch. `width` is set by the
// caller (Panel.qml renders it from a Repeater and binds the width at the
// call site, the same division of responsibility as NodeRow.qml).
//
// Toggle optimism: `checked` is a pure binding to `client.paused`, never
// assigned imperatively, so it cannot desync from the snapshot -- see the
// matching note in NodeRow.qml.
Item {
    id: root

    required property var client // {mac,name,ip,connected,wireless,paused,guest,nodeName,profileName} | null
    required property var service // Service.qml, or null while unavailable
    required property color foreground
    required property string fontFamily
    property bool showDetails: true
    property string density: "compact"
    readonly property real densityGap: density === "comfortable" ? Style.space(6) : 0
    signal detailsRequested()
    readonly property string navigationIdentity: root.macValid ? root.macLower : "inspect:" + EeroModel.clientDisplayKey(root.client)
    readonly property string pauseKey: EeroModel.actionKey("pause-client", root.macLower)
    readonly property var feedback: root.service ? root.service.actionDetails(root.pauseKey) : {state: "", error: null}

    readonly property string macLower: root.client && typeof root.client.mac === "string"
        ? root.client.mac.toLowerCase() : ""
    readonly property bool macValid: EeroModel.isMac(root.macLower)
    readonly property bool pauseBusy: root.service && root.macValid
        ? root.service.actionPending(root.pauseKey) : false
    readonly property bool isPaused: root.client ? root.client.paused === true : false

    implicitHeight: content.implicitHeight + (feedbackLabel.visible ? feedbackLabel.implicitHeight : 0) + Style.spacing.rowGap + root.densityGap

    Item {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        implicitHeight: Math.max(kindGlyph.implicitHeight, info.implicitHeight, pauseGroup.implicitHeight)

        Text {
            id: kindGlyph
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: root.client && root.client.wireless === false ? EeroModel.GLYPH_ETHERNET
                : root.client && root.client.wireless === true ? EeroModel.GLYPH_WIFI : "?"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
        }

        Column {
            id: info
            anchors.left: kindGlyph.right
            anchors.leftMargin: Style.space(8)
            anchors.right: pauseGroup.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(1) + root.densityGap

            // An Item with anchored children, not a Row: a Row never
            // constrains a child Text's width, so `elide` had nothing to
            // elide against and a long client name just pushed the row
            // wider than the popup instead of truncating.
            Item {
                id: nameRow
                width: parent.width
                implicitHeight: Math.max(nameText.implicitHeight, badgeRow.implicitHeight)

                Row {
                    id: badgeRow
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(6)

                    // Paused state must be explicit, not just a dimmed row
                    // (opacity alone is easy to miss/mistake for "loading").
                    Text {
                        visible: root.isPaused
                        textFormat: Text.PlainText
                        text: "PAUSED"
                        color: Color.urgent
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                    }

                    Text {
                        visible: !!(root.client && root.client.guest)
                        textFormat: Text.PlainText
                        text: "GUEST"
                        color: Color.accent
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.bold: true
                    }
                }

                Text {
                    id: nameText
                    anchors.left: parent.left
                    anchors.right: badgeRow.left
                    anchors.rightMargin: badgeRow.width > 0 ? Style.space(6) : 0
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: EeroModel.displayValue(root.client ? root.client.name : null)
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                }
            }

            Text {
                textFormat: Text.PlainText
                text: {
                    var parts = []
                    if (root.client && root.client.ip) parts.push(root.client.ip)
                    if (root.client && root.client.nodeName) parts.push(root.client.nodeName)
                    if (root.client && root.client.profileName) parts.push("Profile: " + root.client.profileName)
                    parts.push(EeroModel.booleanLabel(root.client ? root.client.connected : null, "Connected", "Offline"))
                    parts.push(EeroModel.booleanLabel(root.client ? root.client.paused : null, "Internet paused", "Internet not paused"))
                    return parts.join(" · ")
                }
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
                width: parent.width
            }
        }

        Column {
            id: pauseGroup
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4) + root.densityGap

            Text {
                textFormat: Text.PlainText
                text: root.isPaused ? "Resume internet" : "Pause internet"
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
            }

            ToggleSwitch {
                id: pauseToggle
                objectName: "nav:client:pause:" + root.macLower
                checked: root.isPaused
                busy: root.pauseBusy
                // interactive stays tied only to mac validity, not the
                // transient busy flag -- see NodeRow.qml's ledToggle comment
                // for why (ToggleSwitch's cursorRing/track width follows
                // interactive, so tying it to busy jumped the row).
                interactive: !!root.service && root.service.actionsEnabled && root.macValid && root.client && typeof root.client.paused === "boolean"
                opacity: (root.service && root.service.actionsEnabled && root.macValid && !root.pauseBusy) ? 1.0 : 0.6
                foreground: root.foreground
                Accessible.name: (root.isPaused ? "Resume internet for " : "Pause internet for ") + EeroModel.displayValue(root.client ? root.client.name : null)
                onToggled: if (root.macValid && root.service && root.service.actionsEnabled && !root.pauseBusy) root.service.pauseClient(root.client.mac, !root.isPaused)

                PanelToolTip {
                    visible: pauseToggle.containsMouse
                    text: root.isPaused ? "Resume" : "Pause"
                    fontFamily: root.fontFamily
                }
            }
            Button {
                height: Math.max(implicitHeight, Style.space(root.density === "comfortable" ? 40 : 32))
                objectName: "nav:client:details:" + root.navigationIdentity
                focusable: true
                visible: root.showDetails
                text: "Details"
                foreground: root.foreground
                fontFamily: root.fontFamily
                Accessible.name: "Details for " + EeroModel.displayValue(root.client ? root.client.name : null)
                onClicked: root.detailsRequested()
            }
        }
    }
    Text {
        id: feedbackLabel
        anchors.top: content.bottom
        width: parent.width
        textFormat: Text.PlainText
        text: (!root.macValid ? "Pause unavailable: invalid or missing MAC. " : root.client && typeof root.client.paused !== "boolean" ? "Pause unavailable: state unknown. " : "")
            + root.feedback.state + (root.feedback.error ? " · " + EeroModel.errorText(root.feedback.error.code, root.feedback.error.message) : "")
        visible: text !== ""
        color: root.feedback.error ? Color.urgent : Color.muted
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
    }
}
