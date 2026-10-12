import QtQuick
import QtQuick.Window
import Quickshell
import "../Navigation.js" as Navigation

ShellRoot {
    id: test
    Window {
        id: scene
        visible: true
        width: 400
        height: 1000
    }
    QtObject {
        id: service
        property int generation: 1
        property bool actionsEnabled: false
        function actionDetails(key) { return {state: "", error: null} }
        function actionPending(key) { return false }
    }
    function require(condition, message) {
        if (!condition) { console.error("FAIL: " + message); Qt.quit(); throw new Error(message) }
    }
    function record() {
        return {mac: null, name: "Synthetic", ip: "192.0.2.1", connected: true,
            wireless: true, paused: false, guest: false, nodeName: "Synthetic Node", profileName: null}
    }
    Component.onCompleted: {
        var component = Qt.createComponent(Quickshell.env("EERO_CLIENTS_PAGE_URL"))
        require(component.status === Component.Ready, component.errorString())
        var page = component.createObject(scene.contentItem, {service: service, snapshot: {clients: [record()]}, foreground: "white", fontFamily: "sans-serif", width: 400})
        require(!!page, "ClientsPage creation")
        page.searchQuery = "Synthetic"
        page.guestOnly = true
        page.openDetails(page.snapshot.clients[0])
        page.visible = false
        page.visible = true
        page.snapshot = {clients: [record()]}
        require(page.inDetails && !page.unidentifiedUnavailable && page.detail === page.snapshot.clients[0], "equivalent anonymous details resolve current record")
        require(page.searchQuery === "Synthetic" && page.guestOnly, "refresh preserves query/filter")
        var changed = record(); changed.paused = true
        page.snapshot = {clients: [changed]}
        require(page.inDetails && page.unidentifiedUnavailable && page.detail.paused === false, "changed record retains unavailable last known details")
        page.snapshot = {clients: [record()]}
        require(page.unidentifiedUnavailable, "unavailable selection never reattaches")
        page.searchQuery = ""
        require(page.handleEscape() && !page.inDetails, "Back clears unavailable selection")
        page.openDetails(record())
        page.snapshot = {clients: []}
        require(page.inDetails && page.unidentifiedUnavailable, "removed record unavailable")
        page.handleEscape()
        page.openDetails(record())
        page.snapshot = {clients: [record(), record()]}
        require(page.inDetails && page.unidentifiedUnavailable, "ambiguous record unavailable")
        page.snapshot = null
        require(!page.inDetails && page.searchQuery === "" && !page.guestOnly, "null Snapshot resets context")
        page.snapshot = {clients: [record()]}
        page.openDetails(record())
        service.generation++
        require(!page.inDetails, "generation resets details")
        var valid = record(); valid.mac = "aa:bb:cc:dd:ee:ff"
        page.snapshot = {clients: [valid]}; page.openDetails(valid)
        var current = record(); current.mac = valid.mac; current.name = "Current"
        page.snapshot = {clients: [current]}
        require(page.detail === current, "valid MAC resolves current Snapshot")
        page.handleEscape()
        page.resetForOpen(false)
        // Real native controls and their callbacks, through the shared cursor seam.
        var targets = Navigation.targets(page)
        var scope = Navigation.current(targets, "clients:scope:all")
        require(!!scope && Navigation.activate(scope) && page.connection === "all", "native scope callback")
        targets = Navigation.targets(page)
        var details = Navigation.current(targets, "client:details:aa:bb:cc:dd:ee:ff")
        require(!!details && Navigation.activate(details) && page.detailMac === current.mac, "native Details callback")
        require(Navigation.activate(Navigation.current(Navigation.targets(page), "clients:back")) && !page.inDetails, "native Back callback")
        require(!Navigation.current(Navigation.targets(page), "client:pause:aa:bb:cc:dd:ee:ff"), "disabled Actions excluded")
        var namedAll = record(); namedAll.mac = valid.mac; namedAll.nodeName = "all"; namedAll.profileName = "all"
        page.snapshot = {clients: [namedAll]}
        page.resetForOpen(true)
        targets = Navigation.targets(page)
        require(!!Navigation.current(targets, "clients:node:all")
            && !!Navigation.current(targets, "clients:node:value:all"), "Node sentinel and real all value are distinct")
        require(Navigation.activate(Navigation.current(targets, "clients:node:value:all")) && page.nodeFilter === "all",
                "keyboard selects real Node named all")
        require(Navigation.activate(Navigation.current(Navigation.targets(page), "clients:node:all")) && page.nodeFilter === "",
                "keyboard clears Node filter")
        require(Navigation.activate(Navigation.current(Navigation.targets(page), "clients:profile:value:all")) && page.profileFilter === "all",
                "keyboard selects real Profile named all")
        require(Navigation.activate(Navigation.current(Navigation.targets(page), "clients:profile:all")) && page.profileFilter === "",
                "keyboard clears Profile filter")
        page.destroy()
        console.log("PASS: native page behavior")
        Qt.quit()
    }
}
