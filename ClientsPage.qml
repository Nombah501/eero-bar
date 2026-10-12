pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
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
    readonly property real controlHeight: Style.space(density === "comfortable" ? 40 : 32)
    readonly property bool fieldFocused: search.activeFocus
    property alias searchQuery: search.text
    property string connection: "connected"
    property bool pausedOnly: false
    property bool guestOnly: false
    property string nodeFilter: ""
    property string profileFilter: ""
    property string detailMac: ""
    property var unidentifiedDetail: null
    property bool unidentifiedUnavailable: false
    readonly property var detail: detailMac !== "" ? EeroModel.clientByMac(snapshot, detailMac) : unidentifiedDetail
    readonly property bool inDetails: detailMac !== "" || unidentifiedDetail !== null
    readonly property var results: EeroModel.filterClients(snapshot, search.text, {connection: connection, paused: pausedOnly, guest: guestOnly, node: nodeFilter, profile: profileFilter})
    readonly property var summary: EeroModel.summarize(snapshot)
    readonly property var profiles: snapshot && Array.isArray(snapshot.profiles) ? snapshot.profiles.filter(function(p) { return !!p }) : []
    spacing: Style.spacing.panelGap + root.densityGap

    function focusSearch() { if (!inDetails) search.forceActiveFocus() }
    function handleEscape() {
        if (search.text !== "") { search.text = ""; return true }
        if (inDetails) { detailMac = ""; unidentifiedDetail = null; unidentifiedUnavailable = false; focusSearch(); return true }
        return false
    }
    function resetFilters() {
        search.text = ""; pausedOnly = false; guestOnly = false
        nodeFilter = ""; profileFilter = ""
    }
    function resetForOpen(defaultOffline) {
        connection = defaultOffline === true ? "all" : "connected"
        resetFilters(); detailMac = ""; unidentifiedDetail = null; unidentifiedUnavailable = false
    }
    function options(field) {
        var values = []
        var clients = snapshot && Array.isArray(snapshot.clients) ? snapshot.clients : []
        for (var i = 0; i < clients.length; i++) {
            var value = clients[i] ? clients[i][field] : null
            if (typeof value === "string" && value.trim() !== "" && values.indexOf(value) < 0) values.push(value)
        }
        return values.sort(function(a, b) { return a.toLowerCase().localeCompare(b.toLowerCase()) })
    }
    function openDetails(client) {
        detailMac = EeroModel.isMac(client.mac) ? client.mac.toLowerCase() : ""
        unidentifiedDetail = detailMac === "" ? client : null
        unidentifiedUnavailable = false
    }
    onSnapshotChanged: {
        if (!snapshot) { resetForOpen(false) }
        else if (unidentifiedDetail !== null && !unidentifiedUnavailable) {
            var equivalent = EeroModel.equivalentClient(snapshot, unidentifiedDetail)
            if (equivalent) unidentifiedDetail = equivalent
            else unidentifiedUnavailable = true
        }
    }
    Connections {
        target: root.service
        function onGenerationChanged() { root.resetForOpen(false) }
    }
    Keys.onEscapePressed: function(event) { event.accepted = root.handleEscape() }
    signal navigationRequested()
    signal hostTabRequested(int direction)

    Column {
        width: parent.width
        visible: !root.inDetails
        spacing: Style.space(8) + root.densityGap
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: root.summary.connectedClients + " connected / " + root.summary.totalClients + " total"
                + (root.summary.unknownClients ? " · " + root.summary.unknownClients + " unknown connection" : "")
                + "\n" + root.results.length + " results · " + EeroModel.summarize({clients: root.results}).connectedClients + " connected in results"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
        }
        Text {
            text: "Search Clients"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
        }
        TextField {
            id: search
            objectName: "nav:clients:search"
            width: parent.width
            height: Math.max(implicitHeight, root.controlHeight)
            placeholderText: "Search name, IP or MAC"
            Accessible.name: "Search Clients by name, IP or MAC"
            foreground: root.foreground
            font.family: root.fontFamily
            Keys.onEscapePressed: function(event) { event.accepted = root.handleEscape() }
            onAccepted: root.navigationRequested()
            Keys.onTabPressed: { root.hostTabRequested(event.modifiers & Qt.ShiftModifier ? -1 : 1); event.accepted = true }
            Keys.onBacktabPressed: { root.hostTabRequested(-1); event.accepted = true }
        }
        Flow {
            width: parent.width
            spacing: Style.space(6) + root.densityGap
            Repeater {
                model: [{value: "connected", label: "Connected"}, {value: "all", label: "Include offline / unknown"}, {value: "offline", label: "Offline only"}]
                delegate: Button {
                    required property var modelData
                    objectName: "nav:clients:scope:" + modelData.value
                    height: Math.max(implicitHeight, root.controlHeight)
                    focusable: true
                    text: (root.connection === modelData.value ? "✓ " : "") + modelData.label
                    bordered: true
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.connection = modelData.value
                }
            }
            Button {
                height: Math.max(implicitHeight, root.controlHeight)
                objectName: "nav:clients:paused"
                focusable: true
                text: (root.pausedOnly ? "✓ " : "") + "Paused only"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.pausedOnly = !root.pausedOnly
            }
            Button {
                height: Math.max(implicitHeight, root.controlHeight)
                objectName: "nav:clients:guest"
                focusable: true
                text: (root.guestOnly ? "✓ " : "") + "Guest only"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.guestOnly = !root.guestOnly
            }
        }
        Text { text: "Node"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
        Flow {
            width: parent.width
            spacing: Style.space(6) + root.densityGap
            Button { objectName: "nav:clients:node:all"; focusable: true; height: Math.max(implicitHeight, root.controlHeight); text: root.nodeFilter === "" ? "✓ All Nodes" : "All Nodes"; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.nodeFilter = "" }
            Repeater {
                model: root.options("nodeName")
                delegate: Button {
                    required property string modelData
                    objectName: "nav:clients:node:value:" + modelData
                    height: Math.max(implicitHeight, root.controlHeight)
                    focusable: true
                    width: Math.min(implicitWidth, root.width)
                    text: (root.nodeFilter === modelData ? "✓ " : "") + modelData
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.nodeFilter = modelData
                }
            }
        }
        Text { text: "Profile"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
        Flow {
            width: parent.width
            spacing: Style.space(6) + root.densityGap
            Button { objectName: "nav:clients:profile:all"; focusable: true; height: Math.max(implicitHeight, root.controlHeight); text: root.profileFilter === "" ? "✓ All Profiles" : "All Profiles"; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.profileFilter = "" }
            Repeater {
                model: root.options("profileName")
                delegate: Button {
                    required property string modelData
                    height: Math.max(implicitHeight, root.controlHeight)
                    objectName: "nav:clients:profile:value:" + modelData
                    focusable: true
                    width: Math.min(implicitWidth, root.width)
                    text: (root.profileFilter === modelData ? "✓ " : "") + modelData
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.profileFilter = modelData
                }
            }
        }
        Button { objectName: "nav:clients:reset"; focusable: true; height: Math.max(implicitHeight, root.controlHeight); text: "Reset search and filters (keep connection scope)"; width: Math.min(implicitWidth, root.width); foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.resetFilters() }
        Text {
            width: parent.width
            visible: root.results.length === 0
            text: "No Clients match this connection scope and filters."
            color: Color.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
        }
        Repeater {
            model: root.results
            delegate: ClientRow {
                required property var modelData
                width: root.width
                client: modelData
                service: root.service
                foreground: root.foreground
                fontFamily: root.fontFamily
                density: root.density
                onDetailsRequested: root.openDetails(modelData)
            }
        }
        PanelSeparator { foreground: root.foreground; visible: root.profiles.length > 0 }
        PanelSectionHeader { text: "Profiles · Pause internet for the whole group"; foreground: root.foreground; fontFamily: root.fontFamily; visible: root.profiles.length > 0 }
        Repeater {
            model: root.profiles
            delegate: Column {
                id: profileRow
                required property var modelData
                readonly property string key: EeroModel.actionKey("pause-profile", modelData.url)
                readonly property var feedback: root.service ? root.service.actionDetails(key) : {state: "", error: null}
                width: root.width
                spacing: Style.space(4) + root.densityGap
                Text {
                    width: parent.width
                    textFormat: Text.PlainText
                    text: EeroModel.displayValue(profileRow.modelData.name) + " · "
                        + (typeof profileRow.modelData.clientCount === "number" ? profileRow.modelData.clientCount + " Clients" : "Client count unknown")
                        + " · " + EeroModel.booleanLabel(profileRow.modelData.paused, "Paused", "Not paused")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    wrapMode: Text.WrapAnywhere
                }
                Button {
                    height: Math.max(implicitHeight, root.controlHeight)
                    objectName: "nav:profile:pause:" + profileRow.modelData.url
                    focusable: true
                    text: profileRow.modelData.paused === true ? "Resume Profile internet" : "Pause Profile internet"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    Accessible.name: (profileRow.modelData.paused === true ? "Resume internet for Profile " : "Pause internet for Profile ") + EeroModel.displayValue(profileRow.modelData.name)
                    Accessible.description: "Applies to all Clients in this Profile; does not Block association."
                    enabled: !!root.service && root.service.actionsEnabled && EeroModel.isProfileUrl(profileRow.modelData.url) && typeof profileRow.modelData.paused === "boolean" && !root.service.actionPending(profileRow.key)
                    onClicked: root.service.pauseProfile(profileRow.modelData.url, !profileRow.modelData.paused)
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    textFormat: Text.PlainText
                    text: (!EeroModel.isProfileUrl(profileRow.modelData.url) ? "Pause unavailable: invalid or missing Profile target. " : typeof profileRow.modelData.paused !== "boolean" ? "Pause unavailable: state unknown. " : "")
                        + profileRow.feedback.state + (profileRow.feedback.error ? " · " + EeroModel.errorText(profileRow.feedback.error.code, profileRow.feedback.error.message) : "")
                    color: profileRow.feedback.error ? Color.urgent : Color.muted
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
    Column {
        width: parent.width
        visible: root.inDetails
        spacing: Style.space(10) + root.densityGap
        Button { objectName: "nav:clients:back"; focusable: true; height: Math.max(implicitHeight, root.controlHeight); text: "Back to Clients"; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: { root.detailMac = ""; root.unidentifiedDetail = null; root.unidentifiedUnavailable = false; root.focusSearch() } }
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: root.detail ? EeroModel.displayValue(root.detail.name) : "Client no longer in Snapshot"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            wrapMode: Text.WrapAnywhere
        }
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: {
                var c = root.detail
                if (!c) return "Refresh or return to the list; no Action is available for a missing Client."
                if (root.unidentifiedUnavailable) return "Client unavailable in the current Snapshot. Read-only last known details:\nMAC: " + EeroModel.displayValue(c.mac) + "\nIP: " + EeroModel.displayValue(c.ip) + "\nNode: " + EeroModel.displayValue(c.nodeName) + "\nProfile: " + EeroModel.displayValue(c.profileName) + "\nConnection: " + EeroModel.booleanLabel(c.connected, "Connected", "Offline") + "\nLink: " + EeroModel.booleanLabel(c.wireless, "Wireless", "Wired") + "\nInternet Pause: " + EeroModel.booleanLabel(c.paused, "Paused", "Not paused") + "\nGuest: " + EeroModel.booleanLabel(c.guest, "Yes", "No")
                return "MAC: " + EeroModel.displayValue(c.mac) + "\nIP: " + EeroModel.displayValue(c.ip)
                    + "\nNode: " + EeroModel.displayValue(c.nodeName) + "\nProfile: " + EeroModel.displayValue(c.profileName)
                    + "\nConnection: " + EeroModel.booleanLabel(c.connected, "Connected", "Offline")
                    + "\nLink: " + EeroModel.booleanLabel(c.wireless, "Wireless", "Wired")
                    + "\nInternet Pause: " + EeroModel.booleanLabel(c.paused, "Paused", "Not paused")
                    + "\nGuest: " + EeroModel.booleanLabel(c.guest, "Yes", "No")
            }
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WrapAnywhere
        }
        ClientRow {
            visible: !!root.detail
            width: root.width
            client: root.detail
            service: root.service
            foreground: root.foreground
            fontFamily: root.fontFamily
            density: root.density
            showDetails: false
        }
    }
    Text {
        width: parent.width
        text: "Pause stops internet access. It does not Block association: a Client may remain connected while paused."
        color: Color.muted
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
    }
}
