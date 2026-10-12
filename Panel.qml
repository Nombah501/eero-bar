pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "EeroModel.js" as EeroModel
import "Navigation.js" as Navigation

// Native bar entry point: persistent health/freshness header and top pages,
// with one full-width scroll body. Existing Client/Node/Profile controls
// retain their behavior; detailed views and preferences are separate slices.
//
// docs/service-api.md is the only contract this file relies on for
// Service.qml; nothing here parses raw eero JSON.
Panel {
    id: root
    moduleName: "n501.eero-bar"
    ipcTarget: "n501.eero-bar"
    manageIpc: false

    readonly property var eeroService: root.bar && root.bar.shell && typeof root.bar.shell.serviceFor === "function"
        ? root.bar.shell.serviceFor("n501.eero-bar") : null

    readonly property string servicePhase: root.eeroService ? String(root.eeroService.phase || "loading") : "loading"
    readonly property string amazonPhase: root.eeroService ? String(root.eeroService.amazonPhase || "") : ""
    // The controller retains the user's open intent, but the layer-shell
    // input surface must yield to the default browser until explicit save consent.
    readonly property bool browserDelegated: root.servicePhase === "amazon"
        && root.amazonPhase !== "verification_ready" && root.amazonPhase !== "saving"
    onAmazonPhaseChanged: {
        if (root.opened && root.servicePhase === "amazon" && !root.browserDelegated)
            Qt.callLater(loginView.focusPrimaryField)
    }
    Component.onDestruction: {
        if ((root.opened || root.showLogin) && root.eeroService) root.eeroService.cancelLogin()
    }
    readonly property var serviceSnapshot: root.eeroService ? root.eeroService.snapshot : null
    readonly property var serviceLastError: root.eeroService ? root.eeroService.lastError : null
    readonly property var serviceActionError: root.eeroService ? root.eeroService.actionError : null
    readonly property bool showClientCount: root.eeroService ? root.eeroService.showClientCount === true : false
    readonly property string density: root.eeroService ? root.eeroService.density : "compact"
    readonly property bool showOfflineClients: root.eeroService ? root.eeroService.showOfflineClients === true : false
    readonly property real densityGap: root.density === "comfortable" ? Style.space(6) : 0
    readonly property real controlHeight: Style.space(root.density === "comfortable" ? 40 : 32)
    readonly property bool canEditPreferences: !!root.bar && !!root.bar.shell
        && typeof root.bar.shell.updateEntryInline === "function"
    property string preferenceError: ""

    function savePreference(key, value) {
        if (!root.canEditPreferences) return
        var next = {}
        for (var field in root.settings) next[field] = root.settings[field]
        if (next[key] === value) { root.preferenceError = ""; return }
        next[key] = value
        root.preferenceError = root.bar.shell.updateEntryInline(root.moduleName, next)
            ? "" : "The host did not save this change. Check the eero bar-widget settings and try again."
    }

    readonly property string level: EeroModel.barLevel(root.servicePhase, root.serviceSnapshot, root.serviceLastError)
    readonly property color glyphColor: {
        if (root.level === "bad") return Color.urgent
        // qs.Commons.Color has no dedicated warning/yellow role; accent is
        // the closest fit and is themed per-theme (distinct from foreground
        // in 21 of the 22 bundled themes).
        if (root.level === "warn") return Color.accent
        // Matches the bar's own foreground (what karaoke/built-in widgets
        // paint their "normal" glyph with) rather than the bare Commons
        // fallback, so this tracks a per-bar override if one exists.
        if (root.level === "ok") return root.barForeground
        return Color.muted // "signedOut" or "unknown"
    }
    readonly property string tooltipText: EeroModel.tooltipText(root.servicePhase, root.serviceSnapshot, root.serviceLastError)
        + (root.refreshBusy ? " · refreshing" : "")
        + (root.eeroService && root.eeroService.lastFetchedAt
            ? " · last success " + EeroModel.relativeTime(root.eeroService.lastFetchedAt, root.nowMs) : " · freshness unknown")
        + (root.browserDelegated ? " · Amazon sign-in in default browser; click to cancel" : "")
    readonly property string clientCountText: {
        if (!root.showClientCount || !root.serviceSnapshot) return ""
        return String(root.summary.connectedClients) + "/" + String(root.summary.totalClients)
    }

    // -- popup content colors/fonts ------------------------------------------
    // Popup content sits on Color.popups.background, not the (possibly
    // transparent) bar surface, so it uses bar.foreground directly --
    // matching the built-in network/bluetooth panels -- rather than the
    // bar-icon-specific `barForeground` above.
    readonly property color popupForeground: root.bar ? root.bar.foreground : Color.foreground
    readonly property string popupFontFamily: root.bar ? root.bar.fontFamily : Style.font.family

    // -- derived snapshot views -----------------------------------------------
    readonly property var networkInfo: root.serviceSnapshot ? root.serviceSnapshot.network : null
    readonly property var summary: EeroModel.summarize(root.serviceSnapshot)
    readonly property color levelColor: {
        if (root.summary.level === "bad") return Color.urgent
        if (root.summary.level === "warn") return Color.accent
        if (root.summary.level === "ok") return root.popupForeground
        return Color.muted
    }
    readonly property string internetStatusText: {
        if (!root.networkInfo) return ""
        if (root.networkInfo.internetOnline === true) return "Internet online"
        if (root.networkInfo.internetOnline === false) return "Internet offline"
        return "Internet status unknown"
    }
    readonly property color internetStatusColor: {
        if (!root.networkInfo) return Color.muted
        if (root.networkInfo.internetOnline === true) return root.popupForeground
        if (root.networkInfo.internetOnline === false) return Color.urgent
        return Color.muted
    }
    readonly property string speedText: {
        if (!root.networkInfo) return ""
        var text = EeroModel.formatSpeed(root.networkInfo.speed)
        var rel = root.networkInfo.speed ? EeroModel.relativeTime(root.networkInfo.speed.date, root.nowMs) : ""
        return rel !== "" ? text + " · " + rel : text
    }
    readonly property bool speedtestBusy: root.eeroService
        ? root.eeroService.isBusy(EeroModel.actionKey("speedtest", "")) : false
    readonly property bool refreshBusy: root.eeroService ? root.eeroService.refreshing === true : false
    readonly property bool guestBusy: root.eeroService
        ? root.eeroService.isBusy(EeroModel.actionKey("guest", "")) || root.eeroService.guestState === "Awaiting readback" : false
    readonly property bool rebootNetworkBusy: root.eeroService
        ? root.eeroService.isBusy(EeroModel.actionKey("reboot-network", "")) : false

    // -- view mode: which body the popup shows -------------------------------
    // A) loading, no snapshot yet. B) signedOut/codeSent: login flow --
    // Service.qml guarantees snapshot is null in both. C) ready, or error
    // with a snapshot: main view with an inline error banner. D) error, no
    // snapshot: bare error + Retry. showLogin overrides everything: a
    // re-login opened over a live session shows the login flow instead of
    // the main view until it completes or is cancelled.
    readonly property string viewMode: {
        if (root.showLogin) return "login"
        if (root.servicePhase === "signedOut" || root.servicePhase === "codeSent" || root.servicePhase === "amazon") return "login"
        if (root.serviceSnapshot) return "main"
        if (root.servicePhase === "error") return "error"
        return "loading"
    }

    // A re-login opened over a live session: the login flow replaces the
    // main view until it completes or is cancelled (ticket 07).
    property bool showLogin: false
    // True once a re-login reaches the OTP code or Amazon browser step.
    // Leaving that step restores the live view on completion/cancellation;
    // a rejected identifier stays in the editor for an explicit retry.
    property bool showLoginCodeSent: false

    // -- local UI state -------------------------------------------------------
    property string selectedPage: "Overview"
    property double nowMs: Date.now()
    property string cursorKey: ""
    function navigationTargets() {
        var targets = Navigation.targets(keyCatcher)
        for (var i = 0; i < targets.length; i++) {
            var target = targets[i]
            if (target.item.hasCursor !== undefined)
                target.item.hasCursor = Qt.binding((function(key) {
                    return function() { return root.cursorKey === key && !root.confirmOpen && !(loginView.visible && loginView.fieldFocused) && !clientsPage.fieldFocused && !pollInput.activeFocus }
                })(target.key))
        }
        return targets
    }
    function cursorMove(direction) {
        if (root.confirmOpen || root.browserDelegated || (loginView.visible && loginView.fieldFocused) || clientsPage.fieldFocused || pollInput.activeFocus) return
        root.cursorKey = Navigation.move(root.navigationTargets(), root.cursorKey, direction)
        var target = Navigation.current(root.navigationTargets(), root.cursorKey)
        if (!target) return
        var point = target.item.mapToItem(column, 0, 0)
        // Only body descendants scroll; the persistent header and tabs stay fixed.
        var ancestor = target.item
        while (ancestor && ancestor !== column) ancestor = ancestor.parent
        if (ancestor !== column) return
        var flick = scrollArea.contentItem
        var bottom = point.y + target.item.height
        if (point.y < flick.contentY) flick.contentY = Math.max(0, point.y)
        else if (bottom > flick.contentY + scrollArea.height)
            flick.contentY = Math.max(0, Math.min(bottom - scrollArea.height, column.height - scrollArea.height))
    }
    function cursorActivate() {
        if (root.confirmOpen || root.browserDelegated || (loginView.visible && loginView.fieldFocused) || clientsPage.fieldFocused || pollInput.activeFocus) return false
        return Navigation.activate(Navigation.current(root.navigationTargets(), root.cursorKey))
    }
    function leaveEditor(key) {
        root.cursorKey = key
        keyCatcher.forceActiveFocus()
        root.cursorMove(1)
    }

    property string confirmKind: "" // "" | "reboot-node" | "reboot-network" | "signout"
    property string confirmNodeUrl: ""
    property string confirmNodeName: ""
    property int confirmGeneration: -1
    property string confirmNetworkUrl: ""
    readonly property int serviceGeneration: root.eeroService ? root.eeroService.generation : -1
    onServiceGenerationChanged: { root.cancelConfirm(); root.cursorKey = "" }
    onServiceSnapshotChanged: {
        Qt.callLater(root.navigationTargets)
        if (!root.confirmOpen) return
        var network = root.serviceSnapshot ? root.serviceSnapshot.network : null
        if (!network || network.url !== root.confirmNetworkUrl
            || (root.confirmKind === "reboot-node" && !EeroModel.nodeForUrl(root.serviceSnapshot, root.confirmNodeUrl)))
            root.cancelConfirm()
    }
    readonly property bool confirmOpen: root.confirmKind !== ""
    readonly property string confirmMessage: {
        if (root.confirmKind === "reboot-node")
            return "Reboot Node " + (root.confirmNodeName || "Unknown name") + " (" + root.confirmNodeUrl + ")? Connected Clients may lose connectivity. A Gateway reboot may interrupt the whole Network. Restoration time is not guaranteed."
        if (root.confirmKind === "reboot-network")
            return "Reboot the entire Network " + (root.networkInfo ? root.networkInfo.name || root.confirmNetworkUrl : root.confirmNetworkUrl) + "? All Clients may lose connectivity. Restoration time is not guaranteed."
        if (root.confirmKind === "signout")
            return "Sign out of eero? This deletes the local Session; server revocation is not promised."
        return ""
    }
    readonly property string confirmActionText: root.confirmKind === "signout" ? "Sign out" : "Reboot"

    function requestConfirm(kind, nodeUrl, nodeName) {
        if (!root.eeroService || root.eeroService.loginBusy || (root.servicePhase !== "ready" && root.servicePhase !== "error")) return
        if (kind !== "signout" && !root.eeroService.actionsEnabled) return
        if (kind === "reboot-node" && !EeroModel.nodeForUrl(root.serviceSnapshot, nodeUrl)) return
        if (kind === "reboot-network" && (!root.networkInfo || !EeroModel.isNetworkUrl(root.networkInfo.url))) return
        root.confirmGeneration = root.serviceGeneration
        root.confirmNetworkUrl = root.networkInfo ? root.networkInfo.url || "" : ""
        root.confirmKind = kind
        root.confirmNodeUrl = nodeUrl || ""
        root.confirmNodeName = nodeName || ""
    }

    function cancelConfirm() {
        root.confirmKind = ""
        root.confirmNodeUrl = ""
        root.confirmNodeName = ""
        root.confirmGeneration = -1
        root.confirmNetworkUrl = ""
    }

    function acceptConfirm() {
        var kind = root.confirmKind
        var nodeUrl = root.confirmNodeUrl
        var valid = root.confirmGeneration === root.serviceGeneration && root.eeroService && !root.eeroService.loginBusy
            && (root.servicePhase === "ready" || root.servicePhase === "error")
            && (kind === "signout" || (root.networkInfo && root.networkInfo.url === root.confirmNetworkUrl))
            && (kind === "signout" || root.eeroService.actionsEnabled)
            && (kind !== "reboot-node" || !!EeroModel.nodeForUrl(root.serviceSnapshot, nodeUrl))
        root.cancelConfirm()
        if (!valid) return
        if (kind === "reboot-node") root.eeroService.rebootNode(nodeUrl)
        else if (kind === "reboot-network") root.eeroService.rebootNetwork()
        else if (kind === "signout") root.eeroService.signOut()
    }

    // Centralized close so callers can't forget to drop the confirm dialog.
    function close() {
        root.controller.hide()
        root.cancelConfirm()
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    // Pushes the CURRENT settings to the service right now.
    function pushSettings() {
        if (root.eeroService && typeof root.eeroService.configure === "function")
            root.eeroService.configure(root.settings)
    }

    onSettingsChanged: root.pushSettings()
    // The host (Bar.qml injectProps) sets `bar` before `settings`, so the
    // instant `bar` resolves (and eeroService with it) `settings` may still
    // be Ui/Panel.qml's `({})` placeholder default. Defer one turn so this
    // re-reads `settings` once the turn's real value has landed, matching
    // n501.karaoke's Panel.qml.
    onEeroServiceChanged: { root.cancelConfirm(); Qt.callLater(root.pushSettings) }

    onOpenedChanged: {
        if (root.opened) {
            root.cursorKey = ""
            root.selectedPage = "Overview"
            nodesPage.resetForOpen()
            root.nowMs = Date.now()
            clientsPage.resetForOpen(root.showOfflineClients)
            if (root.eeroService && typeof root.eeroService.refreshIfStale === "function")
                root.eeroService.refreshIfStale(15)
            // Double-deferred so this runs strictly after KeyboardPanel's own
            // single Qt.callLater focus-prime of keyCatcher (see
            // n501.karaoke's focusSearchTitle() for the same pattern) --
            // otherwise the login view's field loses the race and the popup
            // opens with keyCatcher, not the Identifier field, focused.
            Qt.callLater(function() {
                Qt.callLater(function() {
                    if (root.opened && loginView.visible) loginView.focusPrimaryField()
                })
            })
        } else {
            if (root.eeroService) root.eeroService.cancelLogin()
            root.cancelConfirm()
            // Any close path (Esc, host-driven hide, ...) drops the
            // re-login view so the next open starts from the live state.
            root.showLogin = false
            root.showLoginCodeSent = false
        }
    }

    // Login's fields go readOnly (not disabled) while busy so they keep
    // activeFocus, but nothing else claims it back once the view leaves
    // "login" (e.g. after verifyCode() succeeds and the popup moves through
    // "loading" to "main") -- Esc/Tab would otherwise stay dead until the
    // popup was closed and reopened. Mirrors the network panel's
    // `onPasswordSsidChanged: ... keyCatcher.forceActiveFocus()`.
    onViewModeChanged: {
        if (root.opened && root.viewMode !== "login") {
            Qt.callLater(function() {
                if (root.opened && root.viewMode !== "login") keyCatcher.forceActiveFocus()
            })
        }
    }

    // A reboot/sign-out confirm must never fire against a session/phase it
    // was raised under -- drop it on any phase change (covers sign-out,
    // auth demotion, and a fresh login) rather than only when the view
    // leaves "main", since phase can also move ready <-> error while the
    // view stays "main".
    onServicePhaseChanged: {
        if (root.confirmOpen) root.cancelConfirm()
        // Re-login exit: only the code step proves a login attempt is
        // actually running; leaving it means verify succeeded (phase
        // "loading") or cancelLogin() restored the previous phase. A send
        // failure keeps phase at its pre-login value and must leave the
        // login view open for a retry.
        if (root.showLogin && (root.servicePhase === "codeSent" || root.servicePhase === "amazon")) root.showLoginCodeSent = true
        if (root.showLogin && root.showLoginCodeSent && root.servicePhase !== "codeSent" && root.servicePhase !== "amazon") {
            root.showLogin = false
            root.showLoginCodeSent = false
        }
    }

    Timer {
        id: relativeTimeTimer
        interval: 30000
        repeat: true
        running: !!root.eeroService && !!root.eeroService.lastFetchedAt
        onTriggered: root.nowMs = Date.now()
    }

    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: EeroModel.GLYPH_ROUTER + (root.clientCountText !== "" ? " " + root.clientCountText : "")
        foreground: root.glyphColor
        useActiveColor: false
        tooltipText: root.tooltipText
        onPressed: function(code) { root.toggle() }
    }

    KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened && !root.browserDelegated
        focusTarget: keyCatcher
        contentWidth: panel.fittedContentWidth(Style.space(400))
        // Same fixed cap as the audio/monitor panels (both also
        // `panel.fittedContentHeight(<content>, Style.space(560))`):
        // fittedContentHeight() already clamps to whatever actually fits
        // between the bar and the screen edge, so this is a second, smaller
        // ceiling on top of that so a long client/node list caps out at a
        // sane popup height instead of only ever being screen-limited.
        contentHeight: panel.fittedContentHeight(persistentHeader.implicitHeight + pageTabs.implicitHeight
            + column.implicitHeight + Style.space(20), Style.space(560))

        // Routes keys to the confirm dialog while it is open, so Esc/Enter
        // reach ConfirmDialog.handleKey() instead of the (blocked)
        // keyCatcher below -- same shape as the clipboard/menu plugins'
        // `if (root.deleteConfirmOpen) { if (deleteConfirm.handleKey(event))
        // event.accepted = true; return }`. keyCatcher holds activeFocus
        // (KeyboardPanel's focusTarget) the whole time; this only fires for
        // keys keyCatcher itself left unaccepted (it does nothing at all
        // while `blocked`), which is how an unaccepted key event bubbles up
        // to a focused item's parent in Qt Quick.
        Item {
            id: confirmKeyWrapper
            anchors.fill: parent
            Keys.onPressed: function(event) {
                if (root.confirmOpen && confirmDialog.handleKey(event)) event.accepted = true
                else if (!root.confirmOpen && event.key === Qt.Key_Escape && clientsPage.visible) {
                    if (pollInput.activeFocus) keyCatcher.forceActiveFocus()
                    if (!clientsPage.handleEscape()) root.close()
                    event.accepted = true
                }
            }

            // Catches Esc/Tab for the whole popup. Blocked while a confirm
            // dialog or a login field has focus, so those keys are not
            // double-handled here (they route to confirmKeyWrapper/the
            // field instead) -- mirrors the network panel's
            // `blocked: root.passwordSsid !== ""`. Esc still closes the
            // popup once nothing else claims it: close() (below) drops any
            // pending confirm as part of closing, so a confirm dialog can
            // never outlive the popup.
            PanelKeyCatcher {
                id: keyCatcher
                anchors.fill: parent
                blocked: root.confirmOpen || (loginView.visible && loginView.fieldFocused) || (clientsPage.visible && clientsPage.fieldFocused) || pollInput.activeFocus

                onCloseRequested: {
                    if (root.selectedPage === "Nodes" && nodesPage.handleEscape()) return
                    if (clientsPage.visible && clientsPage.handleEscape()) return
                    root.close()
                }
                onTabRequested: function(direction) { root.switchPanel(direction) }
                onMoveRequested: function(dx, dy) { root.cursorMove(dy || dx) }
                onActivateRequested: root.cursorActivate()

                Column {
                    id: persistentHeader
                    width: parent.width
                    spacing: Style.space(8)
                    Item {
                        width: parent.width
                        implicitHeight: Math.max(headerLabels.implicitHeight, headerActions.implicitHeight)

                        Column {
                            id: headerLabels
                            anchors.left: parent.left
                            anchors.right: headerActions.left
                            anchors.rightMargin: Style.space(10)
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: Style.space(2)

                            // An Item with anchored children, not a Row: a Row
                            // never constrains a child Text's width, so
                            // `elide` had nothing to elide against and a long
                            // network name just pushed the row wider than the
                            // popup instead of truncating.
                            Item {
                                id: nameRow
                                width: parent.width
                                implicitHeight: Math.max(statusDot.height, nameText.implicitHeight)

                                Rectangle {
                                    id: statusDot
                                    width: Style.space(8)
                                    height: width
                                    radius: width / 2
                                    anchors.left: parent.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: root.levelColor
                                }

                                Text {
                                    id: nameText
                                    anchors.left: statusDot.right
                                    anchors.leftMargin: Style.space(8)
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    textFormat: Text.PlainText
                                    text: root.networkInfo ? (root.networkInfo.name || "eero") : "eero"
                                    color: root.popupForeground
                                    font.family: root.popupFontFamily
                                    font.pixelSize: Style.font.title
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                            }

                            Text {
                                width: parent.width
                                visible: text !== ""
                                textFormat: Text.PlainText
                                text: (root.networkInfo ? "Network " + (root.networkInfo.status || "unknown") : "Network health unknown")
                                    + " · " + root.internetStatusText
                                color: root.internetStatusColor
                                font.family: root.popupFontFamily
                                font.pixelSize: Style.font.bodySmall
                                wrapMode: Text.WordWrap
                            }

                            Text {
                                width: parent.width
                                visible: text !== ""
                                textFormat: Text.PlainText
                                text: root.serviceSnapshot ? root.summary.connectedClients + "/" + root.summary.totalClients
                                    + " Clients connected" + (root.summary.unknownClients ? " · " + root.summary.unknownClients + " unknown" : "") : "Client count unknown"
                                color: Color.muted
                                font.family: root.popupFontFamily
                                font.pixelSize: Style.font.caption
                                wrapMode: Text.WordWrap
                            }

                            Text {
                                width: parent.width
                                visible: true
                                textFormat: Text.PlainText
                                text: (root.refreshBusy ? "Refreshing · " : "")
                                    + (root.eeroService && EeroModel.relativeTime(root.eeroService.lastFetchedAt, root.nowMs) !== ""
                                        ? "Last success " + EeroModel.relativeTime(root.eeroService.lastFetchedAt, root.nowMs)
                                        : "No successful update")
                                    + (root.serviceLastError && root.serviceSnapshot ? " · retained data; update failed" : "")
                                color: Color.muted
                                font.family: root.popupFontFamily
                                font.pixelSize: Style.font.caption
                                wrapMode: Text.WordWrap
                            }
                        }

                        Row {
                            id: headerActions
                            anchors.right: parent.right
                            anchors.top: parent.top
                            spacing: Style.space(6)

                            Button {
                                objectName: "nav:refresh"
                                iconText: "\u{f0450}" // md-refresh
                                tooltipText: "Refresh"
                                Accessible.role: Accessible.Button
                                Accessible.name: "Refresh Network status"
                                foreground: root.popupForeground
                                fontFamily: root.popupFontFamily
                                iconSpinning: root.refreshBusy
                                opacity: enabled ? 1.0 : 0.5
                                enabled: !!root.eeroService && !root.refreshBusy && !root.eeroService.loginBusy && root.servicePhase !== "codeSent"
                                onClicked: if (root.eeroService) root.eeroService.refresh()
                            }
                        }
                    }
                }
                TextMetrics {
                    id: tabMetrics
                    text: "Overview"
                    font.family: root.popupFontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                }
                Grid {
                    id: pageTabs
                    anchors.top: persistentHeader.bottom
                    anchors.topMargin: Style.space(8)
                    width: parent.width
                    spacing: Style.space(4)
                    readonly property real minimumTabWidth: tabMetrics.width
                        + Style.spacing.controlPaddingX * 2 + Style.space(8)
                    columns: width >= minimumTabWidth * 4 + spacing * 3 ? 4
                        : (width >= minimumTabWidth * 2 + spacing ? 2 : 1)
                    Repeater {
                        model: ["Overview", "Clients", "Nodes", "Settings"]
                        delegate: Button {
                            required property string modelData
                            objectName: "nav:page:" + modelData
                            width: (pageTabs.width - (pageTabs.columns - 1) * pageTabs.spacing) / pageTabs.columns
                            text: modelData
                            bordered: true
                            Accessible.role: Accessible.Button
                            Accessible.name: modelData
                            Accessible.description: root.selectedPage === modelData ? "Selected page" : "Open page"
                            foreground: root.selectedPage === modelData ? Color.accent : root.popupForeground
                            fontFamily: root.popupFontFamily
                            onClicked: {
                                root.selectedPage = modelData
                                scrollArea.contentItem.contentY = 0
                                if (modelData === "Clients") Qt.callLater(clientsPage.focusSearch)
                            }
                        }
                    }
                }
                ScrollView {
                    id: scrollArea
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: pageTabs.bottom
                    anchors.topMargin: Style.space(10)
                    anchors.bottom: parent.bottom
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical.policy: column.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff

                    Binding {
                        target: scrollArea.contentItem
                        property: "interactive"
                        value: column.implicitHeight > scrollArea.height
                    }

            Column {
                id: column
                width: scrollArea.availableWidth
                spacing: Style.spacing.panelGap + root.densityGap

                // -- A) loading, no snapshot ---------------------------------
                Text {
                    visible: root.viewMode === "loading"
                    width: parent.width
                    textFormat: Text.PlainText
                    text: "Connecting to eero…"
                    color: Color.muted
                    font.family: root.popupFontFamily
                    font.pixelSize: Style.font.body
                }

                // -- D) error, no snapshot ------------------------------------
                Column {
                    visible: root.viewMode === "error"
                    width: parent.width
                    spacing: Style.space(10)

                    Text {
                        width: parent.width
                        textFormat: Text.PlainText
                        text: root.serviceLastError
                            ? EeroModel.errorText(root.serviceLastError.code, root.serviceLastError.message)
                            : "Something went wrong."
                        color: Color.urgent
                        wrapMode: Text.WordWrap
                        font.family: root.popupFontFamily
                        font.pixelSize: Style.font.body
                    }

                    Row {
                        spacing: Style.space(10)

                        Button {
                            objectName: "nav:retry"
                            text: "Retry"
                            bordered: true
                            foreground: root.popupForeground
                            fontFamily: root.popupFontFamily
                            onClicked: if (root.eeroService) root.eeroService.refresh()
                        }

                        // A repeated non-auth error here (network/api/etc.)
                        // may really be an expired/invalid session the
                        // helper hasn't reported as auth_required yet;
                        // offer a way out without waiting for that.
                        Button {
                            objectName: "nav:error:signout"
                            text: "Sign out"
                            foreground: Color.urgent
                            fontFamily: root.popupFontFamily
                            onClicked: root.requestConfirm("signout", "", "")
                        }
                    }
                }

                // -- B) signedOut / codeSent: login flow -----------------------
                LoginView {
                    id: loginView
                    visible: root.viewMode === "login"
                    width: parent.width
                    service: root.eeroService
                    phase: root.servicePhase
                    foreground: root.popupForeground
                    fontFamily: root.popupFontFamily
                    onCloseRequested: root.close()
                    onNavigationRequested: function(key) {
                        if (root.servicePhase === "amazon") {
                            root.cursorKey = key
                            keyCatcher.forceActiveFocus()
                            root.navigationTargets()
                        } else root.leaveEditor(key)
                    }
                }

                // -- C) ready, or error with a snapshot: main view -------------
                Column {
                    visible: root.viewMode === "main"
                    width: parent.width
                    spacing: Style.spacing.panelGap + root.densityGap

                    // ---- error banners ----
                    Column {
                        width: parent.width
                        visible: !!root.serviceLastError
                        spacing: Style.space(2)

                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: root.serviceLastError
                                ? EeroModel.errorText(root.serviceLastError.code, root.serviceLastError.message) : ""
                            color: Color.urgent
                            wrapMode: Text.WordWrap
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.bodySmall
                        }

                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: "Showing last known data."
                            color: Color.muted
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.caption
                        }
                    }

                    Item {
                        width: parent.width
                        visible: !!root.serviceActionError
                        implicitHeight: actionErrorText.implicitHeight

                        Text {
                            id: actionErrorText
                            anchors.left: parent.left
                            anchors.right: dismissActionError.left
                            anchors.rightMargin: Style.space(6)
                            textFormat: Text.PlainText
                            text: root.serviceActionError
                                ? EeroModel.errorText(root.serviceActionError.code, root.serviceActionError.message) : ""
                            color: Color.urgent
                            wrapMode: Text.WordWrap
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.bodySmall
                        }

                        PanelActionButton {
                            id: dismissActionError
                            anchors.right: parent.right
                            anchors.top: parent.top
                            iconText: "✕"
                            tooltipText: "Dismiss"
                            foreground: root.popupForeground
                            hoverColor: Color.urgent
                            fontFamily: root.popupFontFamily
                            onClicked: if (root.eeroService) root.eeroService.clearActionError()
                        }
                    }

                    NodesPage {
                        id: nodesPage
                        width: parent.width
                        visible: root.selectedPage === "Nodes"
                        service: root.eeroService
                        snapshot: root.serviceSnapshot
                        foreground: root.popupForeground
                        fontFamily: root.popupFontFamily
                        density: root.density
                        onRebootRequested: function(url, name) { root.requestConfirm("reboot-node", url, name) }
                    }

                    // ---- guest network ----
                    Column {
                        width: parent.width
                        visible: root.selectedPage === "Overview"
                        spacing: Style.spacing.panelGap + root.densityGap
                    PanelSeparator { foreground: root.popupForeground }

                    Item {
                        width: parent.width
                        implicitHeight: Math.max(guestLabels.implicitHeight, guestToggle.implicitHeight)

                        Column {
                            id: guestLabels
                            anchors.left: parent.left
                            anchors.right: guestToggle.left
                            anchors.rightMargin: Style.space(10)
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: Style.space(2) + root.densityGap

                            PanelSectionHeader {
                                text: "GUEST NETWORK"
                                foreground: root.popupForeground
                                fontFamily: root.popupFontFamily
                            }

                            Text {
                                width: parent.width
                                visible: text !== ""
                                textFormat: Text.PlainText
                                text: root.networkInfo ? (root.networkInfo.guestName || "") : ""
                                color: Color.muted
                                font.family: root.popupFontFamily
                                font.pixelSize: Style.font.caption
                                elide: Text.ElideRight
                            }
                        }

                        ToggleSwitch {
                            id: guestToggle
                            objectName: "nav:overview:guest"
                            Accessible.name: "Guest network"
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            checked: root.networkInfo ? root.networkInfo.guestEnabled === true : false
                            busy: root.guestBusy
                            // `interactive` (not just `busy`) also drives
                            // ToggleSwitch's cursorRing/width -- tying it to
                            // the transient busy flag shrank the track by
                            // 2*cursorPad and jumped the row every action.
                            // `busy` alone already swallows the click
                            // (ToggleSwitch.onClicked: if (!busy) toggled()).
                            interactive: !!root.eeroService && root.eeroService.actionsEnabled && !!root.networkInfo && typeof root.networkInfo.guestEnabled === "boolean"
                            opacity: root.guestBusy ? 0.6 : 1.0
                            foreground: root.popupForeground
                            onToggled: {
                                if (!root.eeroService || !root.networkInfo) return
                                root.eeroService.setGuest(!(root.networkInfo.guestEnabled === true))
                            }
                        }
                    }
                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: "Guest: " + (!root.networkInfo || typeof root.networkInfo.guestEnabled !== "boolean"
                                ? "unknown" : root.networkInfo.guestEnabled ? "enabled" : "disabled")
                                + (root.eeroService ? " · " + root.eeroService.guestState : "")
                            color: Color.muted
                            wrapMode: Text.WordWrap
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.bodySmall
                        }
                        PanelSeparator { foreground: root.popupForeground }
                        PanelSectionHeader {
                            text: "Gateway → internet speed"
                            foreground: root.popupForeground
                            fontFamily: root.popupFontFamily
                        }
                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: root.speedText + "\nMeasured: " + (root.networkInfo && root.networkInfo.speed && root.networkInfo.speed.date
                                ? root.networkInfo.speed.date : "unknown") + "\n"
                                + (root.eeroService ? root.eeroService.speedtestState : "No result")
                            color: root.popupForeground
                            wrapMode: Text.WordWrap
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.bodySmall
                        }
                        Button {
                            objectName: "nav:overview:speed"
                            height: Math.max(implicitHeight, root.controlHeight)
                            focusable: true
                            text: root.speedtestBusy ? "Requesting speedtest…" : "Run speedtest"
                            bordered: true
                            enabled: !!root.eeroService && !root.speedtestBusy && root.eeroService.speedtestState !== "Result pending"
                            foreground: root.popupForeground
                            fontFamily: root.popupFontFamily
                            onClicked: if (root.eeroService) root.eeroService.runSpeedtest()
                        }
                    }

                    ClientsPage {
                        id: clientsPage
                        width: parent.width
                        visible: root.selectedPage === "Clients"
                        service: root.eeroService
                        snapshot: root.serviceSnapshot
                        foreground: root.popupForeground
                        fontFamily: root.popupFontFamily
                        density: root.density
                        onNavigationRequested: root.leaveEditor("clients:search")
                        onHostTabRequested: function(direction) { root.switchPanel(direction) }
                    }

                    // ---- footer ----
                    Column {
                        width: parent.width
                        visible: root.selectedPage === "Settings"
                        spacing: Style.spacing.panelGap + root.densityGap
                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: "Density: " + root.density
                                + "\nDefault offline Clients: " + (root.showOfflineClients ? "included" : "hidden")
                                + "\nPoll interval: " + (root.eeroService ? root.eeroService.pollSeconds : 60)
                                + " s\nWidget Client count (connected/total): " + (root.showClientCount ? "shown" : "hidden")
                                + (root.canEditPreferences ? "\nChanges below are saved by the host." : "\nEdit these persisted preferences in the host’s eero bar-widget settings.")
                                + "\nDefault offline visibility takes effect on the next Popup open; current search and scope stay unchanged."
                                + "\nPause stops internet access, not the Client’s connection."
                            color: root.popupForeground
                            wrapMode: Text.WordWrap
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.bodySmall
                        }
                        Flow {
                            width: parent.width
                            visible: root.canEditPreferences
                            spacing: Style.space(6) + root.densityGap
                            Repeater {
                                model: ["compact", "comfortable"]
                                delegate: Button {
                                    required property string modelData
                                    objectName: "nav:settings:density:" + modelData
                                    text: (root.density === modelData ? "✓ " : "") + modelData + " density"
                                    height: Math.max(implicitHeight, root.controlHeight)
                                    focusable: true
                                    foreground: root.popupForeground
                                    fontFamily: root.popupFontFamily
                                    onClicked: root.savePreference("density", modelData)
                                }
                            }
                            Button {
                                objectName: "nav:settings:offline"
                                text: "Default offline Clients: " + (root.showOfflineClients ? "included" : "hidden")
                                height: Math.max(implicitHeight, root.controlHeight)
                                focusable: true
                                foreground: root.popupForeground
                                fontFamily: root.popupFontFamily
                                onClicked: root.savePreference("showOfflineClients", !root.showOfflineClients)
                            }
                            Button {
                                objectName: "nav:settings:count"
                                text: "Widget connected/total count: " + (root.showClientCount ? "shown" : "hidden")
                                height: Math.max(implicitHeight, root.controlHeight)
                                focusable: true
                                foreground: root.popupForeground
                                fontFamily: root.popupFontFamily
                                onClicked: root.savePreference("showClientCount", !root.showClientCount)
                            }
                        }
                        Column {
                            width: parent.width
                            visible: root.canEditPreferences
                            spacing: Style.space(4) + root.densityGap
                            Text {
                                text: "Refresh interval (30–3600 seconds)"
                                color: root.popupForeground
                                font.family: root.popupFontFamily
                                font.pixelSize: Style.font.bodySmall
                            }
                            TextField {
                                id: pollInput
                                objectName: "nav:settings:poll"
                                width: parent.width
                                height: Math.max(implicitHeight, root.controlHeight)
                                text: String(root.eeroService ? root.eeroService.pollSeconds : 60)
                                validator: IntValidator { bottom: 30; top: 3600 }
                                Accessible.name: "Refresh interval in seconds"
                                foreground: root.popupForeground
                                font.family: root.popupFontFamily
                                onAccepted: {
                                    if (acceptableInput) root.savePreference("pollSeconds", Number(text))
                                    root.leaveEditor("settings:poll")
                                }
                                Keys.onEscapePressed: { keyCatcher.forceActiveFocus(); event.accepted = true }
                                Keys.onTabPressed: { root.switchPanel(event.modifiers & Qt.ShiftModifier ? -1 : 1); event.accepted = true }
                                Keys.onBacktabPressed: { root.switchPanel(-1); event.accepted = true }
                            }
                            Button {
                                objectName: "nav:settings:save-poll"
                                text: "Save refresh interval"
                                height: Math.max(implicitHeight, root.controlHeight)
                                enabled: pollInput.acceptableInput
                                focusable: true
                                foreground: root.popupForeground
                                fontFamily: root.popupFontFamily
                                onClicked: root.savePreference("pollSeconds", Number(pollInput.text))
                            }
                        }
                        Text {
                            width: parent.width
                            visible: root.preferenceError !== ""
                            text: root.preferenceError
                            color: Color.urgent
                            wrapMode: Text.WordWrap
                            font.family: root.popupFontFamily
                            font.pixelSize: Style.font.bodySmall
                        }
                        Button {
                            objectName: "nav:settings:login"
                            height: Math.max(implicitHeight, root.controlHeight)
                            focusable: true
                            text: "Sign in again"
                            foreground: root.popupForeground
                            fontFamily: root.popupFontFamily
                            onClicked: root.showLogin = true
                        }
                    PanelSeparator { foreground: root.popupForeground }
                    Text {
                        width: parent.width
                        text: "Dangerous Actions\nNetwork reboot interrupts connectivity for every Client."
                            + (root.eeroService && root.eeroService.actionState(EeroModel.actionKey("reboot-network", ""))
                                ? "\nReboot: " + root.eeroService.actionState(EeroModel.actionKey("reboot-network", "")) + "; restoration not confirmed." : "")
                        textFormat: Text.PlainText
                        color: root.popupForeground
                        font.family: root.popupFontFamily
                        font.pixelSize: Style.font.bodySmall
                        wrapMode: Text.Wrap
                    }

                    Flow {
                        width: parent.width
                        spacing: Style.space(10) + root.densityGap

                        Button {
                            objectName: "nav:settings:reboot"
                            height: Math.max(implicitHeight, root.controlHeight)
                            focusable: true
                            text: "Reboot network"
                            bordered: true
                            foreground: root.popupForeground
                            fontFamily: root.popupFontFamily
                            opacity: enabled ? 1.0 : 0.5
                            enabled: !root.rebootNetworkBusy && root.networkInfo && EeroModel.isNetworkUrl(root.networkInfo.url)
                                && !root.eeroService.loginBusy && (root.servicePhase === "ready" || root.servicePhase === "error")
                            onClicked: root.requestConfirm("reboot-network", "", "")
                        }

                        Button {
                            objectName: "nav:settings:signout"
                            height: Math.max(implicitHeight, root.controlHeight)
                            focusable: true
                            text: "Sign out"
                            bordered: true
                            foreground: Color.urgent
                            fontFamily: root.popupFontFamily
                            onClicked: root.requestConfirm("signout", "", "")
                        }
                    }
                    }
                } // Column (visible: viewMode === "main")
                } // Column (column)
                } // ScrollView (scrollArea)
            } // PanelKeyCatcher (keyCatcher)

            ConfirmDialog {
                id: confirmDialog
                anchors.fill: parent
                opened: root.confirmOpen
                message: root.confirmMessage
                confirmText: root.confirmActionText
                background: Color.popups.background
                foreground: root.popupForeground
                fontFamily: root.popupFontFamily
                onCanceled: root.cancelConfirm()
                onConfirmed: root.acceptConfirm()
            }
        } // Item (confirmKeyWrapper)
    }

}
