pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui
import "EeroModel.js" as EeroModel

Column {
    id: root
    required property var service
    required property var snapshot
    required property color foreground
    required property string fontFamily
    property string density: "compact"
    readonly property real densityGap: density === "comfortable" ? Style.space(6) : 0
    property var selectedNode: null
    readonly property var detailNode: selectedNode && EeroModel.isNodeUrl(selectedNode.url)
        ? EeroModel.nodeForUrl(snapshot, selectedNode.url) : selectedNode
    signal rebootRequested(string url, string name)
    spacing: Style.spacing.panelGap + root.densityGap

    function resetForOpen() { selectedNode = null }
    onSnapshotChanged: if (!snapshot) resetForOpen()
    Connections {
        target: root.service
        function onGenerationChanged() { root.resetForOpen() }
    }
    function handleEscape() {
        if (!selectedNode) return false
        selectedNode = null
        return true
    }
    function value(value) { return value === null || value === undefined || value === "" ? "Unknown" : String(value) }
    function state(value, yes, no) { return value === true ? yes : value === false ? no : "Unknown" }

    Button {
        height: Math.max(implicitHeight, Style.space(root.density === "comfortable" ? 40 : 32))
        objectName: "nav:nodes:back"
        focusable: true
        visible: !!root.selectedNode
        text: "Back to Nodes"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.handleEscape()
    }
    PanelSectionHeader {
        text: root.selectedNode ? "NODE DETAILS" : "NODES"
        foreground: root.foreground
        fontFamily: root.fontFamily
    }
    Column {
        width: parent.width
        visible: !root.selectedNode
        spacing: Style.space(10) + root.densityGap
        Repeater {
            model: EeroModel.orderedNodes(root.snapshot)
            delegate: NodeRow {
                required property var modelData
                width: parent.width
                node: modelData
                service: root.service
                foreground: root.foreground
                fontFamily: root.fontFamily
                density: root.density
                onDetailsRequested: root.selectedNode = modelData
                onRebootRequested: function(url, name) { root.rebootRequested(url, name) }
            }
        }
        Text {
            width: parent.width
            visible: EeroModel.orderedNodes(root.snapshot).length === 0
            text: "No Nodes in the current Snapshot."
            textFormat: Text.PlainText
            color: Color.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap
        }
    }
    Column {
        width: parent.width
        visible: !!root.selectedNode
        spacing: Style.space(10) + root.densityGap
        Text {
            width: parent.width
            text: root.value(root.detailNode ? root.detailNode.name : root.selectedNode ? root.selectedNode.name : null)
            textFormat: Text.PlainText
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            wrapMode: Text.WrapAnywhere
        }
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: {
                var n = root.detailNode
                if (!n) return "This Node is no longer in the current Snapshot. Actions are unavailable."
                return "Role: " + root.state(n.gateway, "Gateway", "Leaf")
                    + "\nStatus: " + root.state(n.online, "Online", "Offline")
                    + "\nConnection: " + root.state(n.wired, "Wired", "Mesh")
                    + "\nIP: " + root.value(n.ip)
                    + "\nModel: " + root.value(n.model)
                    + "\nFirmware: " + root.value(n.firmware)
                    + "\nUpdate: " + root.state(n.updateAvailable, "Available", "Not reported available")
                    + "\nMesh quality bars: " + root.value(n.meshBars)
                    + "\nClients: " + root.value(n.clients)
                    + "\nLED: " + root.state(n.ledOn, "On", "Off")
                    + "\nTarget: " + root.value(n.url)
            }
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WrapAnywhere
        }
        NodeRow {
            width: parent.width
            node: root.detailNode
            service: root.service
            foreground: root.foreground
            fontFamily: root.fontFamily
            density: root.density
            showDetails: false
            onRebootRequested: function(url, name) { root.rebootRequested(url, name) }
        }
    }
}
