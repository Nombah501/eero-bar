pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui
import "EeroModel.js" as EeroModel

// Snapshot-only Node controls; Action acknowledgement never changes checked.
Item {
    id: root

    required property var node // {id,url,name,model,gateway,online,clients,ledOn,meshBars,ip,firmware,updateAvailable,wired} | null
    required property var service // Service.qml, or null while unavailable
    required property color foreground
    required property string fontFamily

    signal rebootRequested(string url, string name)
    signal detailsRequested()
    property bool showDetails: true
    property string density: "compact"
    readonly property real densityGap: density === "comfortable" ? Style.space(6) : 0
    readonly property string ledState: root.service ? root.service.actionState(EeroModel.actionKey("led", root.nodeUrl)) : ""
    readonly property string rebootState: root.service ? root.service.actionState(EeroModel.actionKey("reboot-node", root.nodeUrl)) : ""
    readonly property var ledFeedback: root.service ? root.service.actionDetails(EeroModel.actionKey("led", root.nodeUrl)) : null
    readonly property var rebootFeedback: root.service ? root.service.actionDetails(EeroModel.actionKey("reboot-node", root.nodeUrl)) : null

    readonly property string nodeUrl: root.node && typeof root.node.url === "string" ? root.node.url : ""
    readonly property bool validUrl: !!EeroModel.nodeForUrl(root.service ? root.service.snapshot : null, root.nodeUrl)
        && root.service.actionsEnabled
    readonly property bool ledBusy: root.service && root.validUrl
        ? root.service.isBusy(EeroModel.actionKey("led", root.nodeUrl)) || root.service.actionPending(EeroModel.actionKey("led", root.nodeUrl)) : false
    readonly property bool rebootBusy: root.service && root.validUrl
        ? root.service.isBusy(EeroModel.actionKey("reboot-node", root.nodeUrl)) || root.service.actionPending(EeroModel.actionKey("reboot-node", root.nodeUrl)) : false
    readonly property var onlineState: root.node ? root.node.online : null // true | false | null
    readonly property bool ledOn: root.node ? root.node.ledOn === true : false

    function meshBarsText(bars) {
        if (typeof bars !== "number" || !isFinite(bars)) return ""
        var total = 5
        var filled = Math.max(0, Math.min(total, Math.round(bars)))
        var text = ""
        for (var i = 0; i < total; i++) text += (i < filled ? "●" : "○")
        return text
    }

    implicitHeight: content.implicitHeight + feedbackText.implicitHeight + Style.spacing.rowGap + root.densityGap
    Text {
        id: feedbackText
        anchors.top: content.bottom
        width: parent.width
        textFormat: Text.PlainText
        text: (root.ledState ? "LED: " + root.ledState : "")
            + (root.ledFeedback && root.ledFeedback.error ? " — " + EeroModel.errorText(root.ledFeedback.error.code, root.ledFeedback.error.message) : "")
            + (root.rebootState ? "\nReboot: " + root.rebootState + (root.rebootState === "Request accepted" ? "; restoration not confirmed." : "") : "")
            + (root.rebootFeedback && root.rebootFeedback.error ? " — " + EeroModel.errorText(root.rebootFeedback.error.code, root.rebootFeedback.error.message) : "")
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
        visible: text !== ""
    }

    Item {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        implicitHeight: info.implicitHeight + actions.implicitHeight + Style.space(6) + root.densityGap

        Column {
            id: info
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            spacing: Style.space(1) + root.densityGap

            // An Item with anchored children, not a Row: a Row never
            // constrains a child Text's width, so `elide` had nothing to
            // elide against and a long node name just pushed the row wider
            // than the popup instead of truncating.
            Item {
                id: nameRow
                width: parent.width
                implicitHeight: Math.max(onlineDot.height, nameText.implicitHeight,
                    gatewayBadge.implicitHeight, updateBadge.implicitHeight)

                Rectangle {
                    id: onlineDot
                    width: Style.space(8)
                    height: width
                    radius: width / 2
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.onlineState === true ? root.foreground
                        : (root.onlineState === false ? Color.urgent : Color.muted)

                    MouseArea {
                        id: onlineDotMouse
                        anchors.fill: parent
                        anchors.margins: -Style.space(4) // easier to hit than the bare dot
                        hoverEnabled: true
                    }

                    PanelToolTip {
                        visible: onlineDotMouse.containsMouse
                        text: root.onlineState === true ? "Online"
                            : (root.onlineState === false ? "Offline" : "Status unknown")
                        fontFamily: root.fontFamily
                    }
                }

                Text {
                    id: gatewayBadge
                    visible: !!(root.node && root.node.gateway)
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "GATEWAY"
                    color: Color.accent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true
                }

                // Firmware update available: the Snapshot has carried
                // updateAvailable since day one, nothing rendered it.
                Text {
                    id: updateBadge
                    visible: root.node ? root.node.updateAvailable === true : false
                    anchors.right: gatewayBadge.visible ? gatewayBadge.left : parent.right
                    anchors.rightMargin: gatewayBadge.visible ? Style.space(6) : 0
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: "UPDATE"
                    color: Color.accent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.bold: true

                    PanelToolTip {
                        visible: updateBadgeMouse.containsMouse
                        text: "Firmware update available"
                        fontFamily: root.fontFamily
                    }

                    MouseArea {
                        id: updateBadgeMouse
                        anchors.fill: parent
                        hoverEnabled: true
                    }
                }

                Text {
                    id: nameText
                    anchors.left: onlineDot.right
                    anchors.leftMargin: Style.space(6)
                    anchors.right: updateBadge.visible ? updateBadge.left
                        : (gatewayBadge.visible ? gatewayBadge.left : parent.right)
                    anchors.rightMargin: updateBadge.visible ? Style.space(6)
                        : (gatewayBadge.visible ? Style.space(6) : 0)
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: root.node ? (root.node.name || "") : ""
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    elide: Text.ElideRight
                }
            }

            Text {
                textFormat: Text.PlainText
                text: {
                    var parts = []
                    if (root.node && root.node.model) parts.push(root.node.model)
                    parts.push(root.onlineState === true ? "Online" : root.onlineState === false ? "Offline" : "Status unknown")
                    parts.push(root.node && root.node.wired === true ? "Wired" : root.node && root.node.wired === false ? "Mesh" : "Connection unknown")
                    if (root.node && typeof root.node.clients === "number") parts.push(root.node.clients + " clients")
                    var mesh = root.node ? root.meshBarsText(root.node.meshBars) : ""
                    if (mesh !== "") parts.push(mesh)
                    return parts.join(" · ")
                }
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
                width: parent.width
            }
        }

        Flow {
            id: actions
            anchors.right: parent.right
            anchors.left: parent.left
            anchors.top: info.bottom
            anchors.topMargin: Style.space(6) + root.densityGap
            spacing: Style.space(10) + root.densityGap

            Row {
                spacing: Style.space(4)

                Text {
                    textFormat: Text.PlainText
                    text: root.node && typeof root.node.ledOn === "boolean" ? "LED" : "LED unknown"
                    color: Color.muted
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    anchors.verticalCenter: parent.verticalCenter
                }

                ToggleSwitch {
                    id: ledToggle
                    objectName: "nav:node:led:" + root.nodeUrl
                    anchors.verticalCenter: parent.verticalCenter
                    checked: root.ledOn
                    busy: root.ledBusy
                    // interactive stays tied only to URL validity, not the
                    // transient busy flag: ToggleSwitch's cursorRing (and so
                    // its track width) follows `interactive` by default, so
                    // toggling it off during an action shrank the switch by
                    // 2*cursorPad and jumped the row. `busy` alone already
                    // blocks the click (ToggleSwitch.onClicked: if (!busy)
                    // toggled()).
                    interactive: root.validUrl && root.node && typeof root.node.ledOn === "boolean"
                    opacity: (interactive && !root.ledBusy) ? 1.0 : 0.6
                    foreground: root.foreground
                    onToggled: if (root.validUrl && root.service) root.service.setLed(root.nodeUrl, !root.ledOn)
                }
            }

            Button {
                height: Math.max(implicitHeight, Style.space(root.density === "comfortable" ? 40 : 32))
                objectName: "nav:node:details:" + (EeroModel.isNodeUrl(root.nodeUrl) ? root.nodeUrl : "inspect:" + JSON.stringify(root.node))
                focusable: true
                visible: root.showDetails
                text: "Details"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.detailsRequested()
            }
            Button {
                height: Math.max(implicitHeight, Style.space(root.density === "comfortable" ? 40 : 32))
                objectName: "nav:node:reboot:" + root.nodeUrl
                focusable: true
                text: "Reboot Node"
                foreground: root.foreground
                fontFamily: root.fontFamily
                enabled: root.validUrl && !root.rebootBusy
                onClicked: root.rebootRequested(root.nodeUrl, root.node ? (root.node.name || "") : "")
            }
        }
    }
}
