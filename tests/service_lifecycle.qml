import QtQuick
import Quickshell

ShellRoot {
    id: test
    property int stage: 0
    property var retained: null
    function require(condition, message) {
        if (!condition) {
            console.error("FAIL: " + message)
            Qt.quit()
            throw new Error(message)
        }
    }
    Loader {
        id: serviceLoader
        source: Quickshell.env("EERO_SERVICE_URL")
    }
    Timer {
        interval: 20
        repeat: true
        running: true
        onTriggered: {
            var service = serviceLoader.item
            if (!service) return
            test.require(service.helperPath === Quickshell.env("EERO_TEST_HELPER_PATH"), "refusing non-fixture helper")
            if (test.stage === 0 && service.phase === "ready") {
                test.retained = service.snapshot
                var generation = service.generation
                var phase = service.phase
                var feedback = service.actionFeedback
                var cases = [
                    {input: null, poll: 60, count: false, density: "compact", offline: false},
                    {input: {pollSeconds: 10, showClientCount: true, density: "comfortable", showOfflineClients: true}, poll: 30, count: true, density: "comfortable", offline: true},
                    {input: {pollSeconds: 4000, showClientCount: "true", density: "dense", showOfflineClients: 1}, poll: 3600, count: false, density: "compact", offline: false},
                    {input: {pollSeconds: "75.9"}, poll: 75, count: false, density: "compact", offline: false}
                ]
                for (var i = 0; i < cases.length; i++) {
                    var c = cases[i]
                    service.configure(c.input)
                    test.require(service.pollSeconds === c.poll && service.showClientCount === c.count
                        && service.density === c.density && service.showOfflineClients === c.offline,
                        "host settings normalize through actual Service.configure case " + i)
                    test.require(service.snapshot === test.retained && service.generation === generation
                        && service.phase === phase && service.actionFeedback === feedback,
                        "preference changes preserve live Snapshot, generation, phase and Action feedback")
                }
                var invalidPoll = [undefined, null, "", "  ", true, false, {}, [], NaN, Infinity, "bad"]
                for (var p = 0; p < invalidPoll.length; p++) {
                    service.configure({pollSeconds: invalidPoll[p]})
                    test.require(service.pollSeconds === 60, "invalid poll falls back to 60 case " + p)
                }
                service.configure({})
                if (Quickshell.env("EERO_CLIENTS_PAGE_URL") !== "") {
                    var component = Qt.createComponent(Quickshell.env("EERO_CLIENTS_PAGE_URL"))
                    test.require(component.status === Component.Ready, "native ClientsPage loads: " + component.errorString())
                    var page = component.createObject(test, {
                        service: service, snapshot: service.snapshot, foreground: "white", fontFamily: "sans-serif",
                        width: 400,
                        density: Qt.binding(function() { return service.density })
                    })
                    test.require(page !== null, "native ClientsPage is created")
                    service.configure({showOfflineClients: true, density: "comfortable"})
                    page.resetForOpen(service.showOfflineClients)
                    test.require(page.connection === "all", "offline preference initializes scope on open")
                    page.connection = "offline"
                    page.searchQuery = "synthetic"
                    page.pausedOnly = true
                    page.visible = false
                    service.configure({showOfflineClients: false, density: "compact", pollSeconds: 90})
                    page.visible = true
                    test.require(page.connection === "offline" && page.searchQuery === "synthetic" && page.pausedOnly,
                        "tab visibility and preference changes preserve explicit scope, query and filters")
                    page.resetForOpen(service.showOfflineClients)
                    test.require(page.connection === "connected" && page.searchQuery === "" && !page.pausedOnly,
                        "next open applies changed offline default and resets query/filters")
                    page.destroy()
                    service.configure({})
                }
                service.sendCode("first@example.invalid")
                service.cancelLogin()
                test.require(service.phase === "ready" && service.snapshot === test.retained, "cancel keeps previous live Snapshot")
                test.require(!service.actionsEnabled, "cancelled writer still settling keeps visible ready Snapshot read-only")
                var cancelledGuestState = service.guestState
                var cancelledSpeedtestState = service.speedtestState
                service.setGuest(true)
                service.runSpeedtest()
                service.setLed(service.snapshot.nodes[0].url, true)
                service.pauseClient(service.snapshot.clients[0].mac, true)
                service.pauseProfile(service.snapshot.profiles[0].url, true)
                service.rebootNode(service.snapshot.nodes[0].url)
                service.rebootNetwork()
                test.require(service.guestState === cancelledGuestState && service.speedtestState === cancelledSpeedtestState,
                             "cancelled writer rejects mutations before observation starts")
                service.sendCode("cancelled-send-queue@example.invalid")
                service.cancelLogin()
                test.require(service.phase === "ready" && !service.loginBusy && service.snapshot === test.retained,
                             "second cancellation keeps live phase and Snapshot")
                service.sendCode("second@example.invalid")
                test.stage = 1
            } else if (test.stage === 1 && service.phase === "codeSent" && !service.loginBusy) {
                test.require(service.maskedIdentifier === "second@example.invalid", "cancelled send cannot replace new attempt")
                service.verifyCode("123456")
                service.cancelLogin()
                test.require(service.phase === "ready", "pending verify restores live phase")
                service.sendCode("third@example.invalid")
                test.stage = 2
            } else if (test.stage === 2 && service.phase === "codeSent" && !service.loginBusy) {
                test.require(service.maskedIdentifier === "third@example.invalid", "late verify cannot resurrect old attempt")
                service.verifyCode("123456")
                test.stage = 3
            } else if (test.stage === 3 && service.phase === "loading" && !service.loginBusy) {
                test.retained = service.snapshot
                test.require(!service.actionsEnabled, "old Snapshot cannot authorize Actions while login commit settles")
                var previousGuestState = service.guestState
                var previousSpeedtestState = service.speedtestState
                service.setGuest(true)
                service.runSpeedtest()
                service.setLed("/2.2/eeros/1", true)
                service.pauseClient("aa:bb:cc:dd:ee:01", true)
                service.pauseProfile("/2.2/networks/1/profiles/1", true)
                service.rebootNode("/2.2/eeros/1")
                service.rebootNetwork()
                test.require(service.guestState === previousGuestState && service.speedtestState === previousSpeedtestState,
                             "rejected Actions do not start observation during auth settlement")
                service.sendCode("cancelled-approved-queue@example.invalid")
                service.cancelLogin()
                test.require(service.phase === "loading" && !service.loginBusy && service.snapshot === test.retained,
                             "queued cancellation leaves approved predecessor and Snapshot intact")
                test.stage = 9
            } else if (test.stage === 9 && service.phase === "ready" && !service.refreshing) {
                test.require(service.snapshot.network.name === "Synthetic", "approved predecessor still publishes live Snapshot")
                service.sendCode("before-signout@example.invalid")
                test.stage = 10
            } else if (test.stage === 10 && service.phase === "codeSent" && !service.loginBusy) {
                service.verifyCode("123456")
                test.stage = 11
            } else if (test.stage === 11 && service.phase === "loading" && !service.loginBusy) {
                service.signOut()
                test.require(service.phase === "signedOut" && service.snapshot === null, "signOut clears consumer state immediately")
                service.sendCode("fourth@example.invalid")
                test.stage = 4
            } else if (test.stage === 4 && service.phase === "codeSent" && !service.loginBusy) {
                test.require(service.maskedIdentifier === "fourth@example.invalid", "logout finishes before queued login")
                test.require(service.lastError === null, "logout successor clears prior errors")
                service.verifyCode("123456")
                test.stage = 5
            } else if (test.stage === 5 && service.phase === "ready" && !service.refreshing) {
                service.setGuest(true)
                service.setGuest(true)
                test.stage = 6
            } else if (test.stage === 6 && service.guestState === "Confirmed by readback") {
                test.require(service.snapshot.network.guestEnabled === true, "Guest confirmed from Snapshot")
                service.setGuest(false)
                test.stage = 7
            } else if (test.stage === 7 && service.guestState === "Request failed" && !service.refreshing) {
                test.require(service.snapshot.network.guestEnabled === true, "Guest failure preserves last known value")
                test.require(service.actionError && !service.lastError, "Action error stays separate from status/login")
                service.runSpeedtest()
                service.runSpeedtest()
                test.stage = 8
            } else if (test.stage === 8 && service.speedtestState === "Result pending" && !service.refreshing) {
                test.require(service.snapshot.network.speed.date === "2026-10-10T09:00:00Z", "ack retains old measurement")
                service.setLed("/2.2/eeros/999", true)
                test.require(service.actionError && service.actionError.code === "usage", "absent Node cannot dispatch")
                service.setLed("/2.2/eeros/2", true)
                test.require(service.actionError && service.actionError.code === "usage", "unknown LED cannot dispatch")
                service.setLed("/2.2/eeros/1", true)
                service.setLed("/2.2/eeros/1", false)
                test.require(service.snapshot.nodes[0].ledOn === false, "sending LED keeps Snapshot value")
                test.stage = 12
            } else if (test.stage === 12 && service.actionState("led:/2.2/eeros/1") === "Confirmed by readback") {
                test.require(service.snapshot.nodes[0].ledOn === true, "LED only confirmed by readback")
                service.setLed("/2.2/eeros/1", false)
                test.stage = 13
            } else if (test.stage === 13 && service.actionState("led:/2.2/eeros/1") === "Request failed" && !service.refreshing) {
                test.require(service.snapshot.nodes[0].ledOn === true, "failed LED keeps last-known state")
                test.require(service.actionDetails("led:/2.2/eeros/1").error && !service.lastError, "local failure independent of status")
                service.rebootNode("/2.2/eeros/999")
                service.rebootNode("/2.2/eeros/1")
                service.rebootNode("/2.2/eeros/1")
                test.stage = 14
            } else if (test.stage === 14 && service.actionState("reboot-node:/2.2/eeros/1") === "Request accepted") {
                test.require(service.actionDetails("led:/2.2/eeros/1").error !== null, "unrelated Action preserves LED error")
                service.rebootNetwork()
                service.rebootNetwork()
                test.stage = 15
            } else if (test.stage === 15 && service.actionState("reboot-network:") === "Request accepted" && !service.refreshing) {
                service.pauseClient("AA:BB:CC:DD:EE:FF", true)
                service.pauseClient("aa:bb:cc:dd:ee:ff", false)
                test.require(service.snapshot.clients[0].paused === false && service.snapshot.profiles[0].paused === false, "Pause sends without optimism")
                test.stage = 16
            } else if (test.stage === 16 && service.actionState("pause-client:aa:bb:cc:dd:ee:ff") === "Confirmed by readback" && !service.refreshing) {
                service.pauseProfile("/2.2/networks/1/profiles/1", true)
                service.pauseProfile("/2.2/networks/1/profiles/1", false)
                test.require(service.snapshot.profiles[0].paused === false, "Profile Pause sends without optimism")
                test.stage = 18
            } else if (test.stage === 18 && service.actionState("pause-profile:/2.2/networks/1/profiles/1") === "Confirmed by readback" && !service.refreshing) {
                test.require(service.snapshot.clients[0].paused && service.snapshot.profiles[0].paused, "Pause confirmed Snapshot")
                service.pauseClient("aa:bb:cc:dd:ee:ff", false)
                service.pauseProfile("/2.2/networks/1/profiles/1", false)
                test.stage = 17
            } else if (test.stage === 17 && service.actionState("pause-client:aa:bb:cc:dd:ee:ff") === "Request failed"
                && service.actionState("pause-profile:/2.2/networks/1/profiles/1") === "Request failed" && !service.refreshing) {
                test.require(service.snapshot.clients[0].paused && service.snapshot.profiles[0].paused, "failed Pause preserves readback")
                test.require(service.actionDetails("pause-client:aa:bb:cc:dd:ee:ff").error
                    && service.actionDetails("pause-profile:/2.2/networks/1/profiles/1").error && !service.lastError, "independent keyed errors")
                service.startAmazonLogin()
                service.cancelLogin()
                test.require(service.phase === "ready" && !service.loginBusy, "queued Amazon close preserves live state")
                service.startAmazonLogin()
                test.stage = 112
            } else if (test.stage === 112 && service.amazonPhase === "verification_ready") {
                test.require(service.snapshot.network.name === "Synthetic", "candidate does not replace live Snapshot")
                test.require(!service.actionsEnabled, "Amazon candidate fences Actions")
                var guestBefore = service.guestState
                var speedBefore = service.speedtestState
                service.setGuest(false)
                service.runSpeedtest()
                service.rebootNetwork()
                test.require(service.guestState === guestBefore && service.speedtestState === speedBefore
                             && Object.keys(service.busyActions).length === 0,
                             "Amazon dispatch guard preserves observation state")
                service.cancelLogin()
                test.require(service.phase === "ready" && service.amazonPhase === "", "cancel candidate restores prior Session")
                service.approveAmazonSession()
                service.startAmazonLogin()
                test.stage = 113
            } else if (test.stage === 113 && service.amazonPhase === "verification_ready") {
                service.approveAmazonSession()
                service.signOut()
                service.startAmazonLogin()
                test.stage = 114
            } else if (test.stage === 114 && service.amazonPhase === "verification_ready") {
                service.approveAmazonSession()
                test.stage = 115
            } else if (test.stage === 115 && service.phase === "ready" && !service.refreshing) {
                test.require(service.amazonPhase === "" && !service.loginBusy, "Amazon success settled before readback")
                service.refresh()
                service.rebootNetwork()
                service.signOut()
                service.refresh()
                service.rebootNetwork()
                test.require(service.phase === "signedOut" && service.snapshot === null,
                             "writer drain does not delay signedOut presentation")
                service.sendCode("writer-session@example.invalid")
                test.stage = 117
            } else if (test.stage === 117 && service.phase === "codeSent" && !service.loginBusy) {
                service.verifyCode("123456")
                test.stage = 118
            } else if (test.stage === 118 && service.phase === "ready" && !service.refreshing) {
                service.refresh()
                service.rebootNetwork()
                service.sendCode("cancelled-writer-queue@example.invalid")
                service.refresh()
                service.rebootNetwork()
                service.cancelLogin()
                test.require(service.phase === "ready" && !service.loginBusy,
                             "cancel queued OTP preserves current live Session")
                service.sendCode("after-writers@example.invalid")
                service.refresh()
                service.rebootNetwork()
                test.stage = 119
            } else if (test.stage === 119 && service.phase === "codeSent" && !service.loginBusy) {
                service.cancelLogin()
                test.stage = 120
            } else if (test.stage === 120 && service.phase === "ready" && !service.refreshing) {
                service.refresh()
                service.rebootNetwork()
                service.startAmazonLogin()
                service.refresh()
                service.rebootNetwork()
                service.cancelLogin()
                test.require(service.phase === "ready" && !service.loginBusy,
                             "cancel queued Amazon preserves current live Session")
                service.startAmazonLogin()
                service.refresh()
                service.rebootNetwork()
                test.stage = 121
            } else if (test.stage === 121 && service.amazonPhase === "verification_ready") {
                service.approveAmazonSession()
                test.stage = 122
            } else if (test.stage === 122 && service.phase === "ready" && !service.refreshing) {
                service.sendCode("reservation-predecessor@example.invalid")
                test.stage = 130
            } else if (test.stage === 130 && service.phase === "codeSent" && !service.loginBusy) {
                service.verifyCode("123456")
                test.stage = 131
            } else if (test.stage === 131 && service.phase === "loading" && !service.loginBusy) {
                service.startAmazonLogin()
                test.stage = 132
            } else if (test.stage === 132 && service.phase === "loading") {
                test.require(service.loginBusy && !service.actionsEnabled, "queued Amazon reservation survives predecessor completion")
                service.refresh()
                service.sendCode("must-not-bypass-reservation@example.invalid")
                service.startAmazonLogin()
            } else if (test.stage === 132 && service.amazonPhase === "verification_ready") {
                service.cancelLogin()
                test.stage = 133
            } else if (test.stage === 133 && service.phase === "ready" && !service.refreshing) {
                console.log("PASS: actual Service lifecycle and readback")
                Qt.quit()
            }
        }
    }
    Timer {
        interval: 25000
        running: true
        onTriggered: {
            console.error("FAIL: lifecycle timeout at stage " + test.stage)
            Qt.quit()
        }
    }
}
