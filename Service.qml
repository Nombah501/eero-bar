pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "EeroModel.js" as EeroModel

// Long-lived n501.eero-bar service (ADR-0001, docs/helper-protocol.md). Runs
// bin/eero-helper once per command -- there is no persistent worker -- polls
// `status` on a timer, and exposes the normalized state Panel.qml (and
// ticket 03's popup) read. Public API is documented in docs/service-api.md.
//
// Process lifecycle: each helper invocation gets its own Process, created
// from a local Component via createObject() and destroyed after actual exit
// or start failure, not timeout notification alone. A small fixed pool was
// considered, but actions can legitimately run concurrently (different
// actionKeys) while status/login are naturally single-shot, so a pool would
// need the same per-slot bookkeeping as one-Process-per-call without buying
// anything -- createObject()/destroy() is the simplest option that is still
// correct.
Item {
    id: root

    // -- public state -------------------------------------------------------

    property string phase: "loading" // loading | signedOut | codeSent | ready | error
    property var snapshot: null // {network, nodes, clients, profiles} | null
    // Status/login errors only. Cleared by a successful status or login step
    // (never by an action's success/failure -- see actionError).
    property var lastError: null // {code, message} | null
    // The most recent action's failure. Cleared by clearError(), by starting
    // a new action (valid or not), or by signOut(). A successful refresh()
    // (including the one every action triggers afterward) never touches it,
    // so the user has time to actually read it.
    property var actionError: null // {key, code, message} | null
    property string maskedIdentifier: ""
    property bool loginBusy: false
    property string amazonPhase: "" // browser_open | exchanging | verification_ready
    property bool refreshing: false
    readonly property bool actionsEnabled: !!root.snapshot && root.phase === "ready"
        && !root.loginBusy && !root._loginProcessInFlight && !root._queuedSendCode
        && !root._logoutInFlight && !root._logoutRequested
    // actionKey -> true. Always reassigned wholesale (never mutated in
    // place) so property bindings that read busyActions[key] update.
    property var busyActions: ({})
    // Per-target feedback; independent of status/login errors.
    property var actionFeedback: ({})
    function feedbackFor(key) { return root.actionFeedback[key] || null }
    function actionState(key) {
        var feedback = root.feedbackFor(key)
        if (!feedback) return ""
        var states = { sending: "Sending request", awaiting: "Awaiting readback",
            confirmed: "Confirmed by readback", failed: "Request failed",
            unconfirmed: "Readback unconfirmed; refresh to check", acknowledged: "Request accepted" }
        return states[feedback.state] || ""
    }
    function actionPending(key) { return root._ledPending(key) === true }
    function actionDetails(key) {
        var feedback = root.feedbackFor(key)
        return { state: root.actionState(key), error: feedback && feedback.state === "failed"
            ? { code: feedback.code, message: feedback.message } : null }
    }
    function _setFeedback(key, value) {
        var next = {}
        var keys = Object.keys(root.actionFeedback)
        for (var j = 0; j < keys.length; j++)
            if (keys[j] !== key) next[keys[j]] = root.actionFeedback[keys[j]]
        next[key] = value
        root.actionFeedback = next
    }
    function _ledPending(key) {
        var feedback = root.feedbackFor(key)
        return feedback && (feedback.state === "sending" || feedback.state === "awaiting")
    }
    property var lastFetchedAt: null // ISO8601 string | null
    property int pollSeconds: 60
    property bool showClientCount: false
    property string density: "compact"
    property bool showOfflineClients: false
    property string guestState: ""
    property string speedtestState: ""
    property var _guestExpected: null
    property var _speedtestPreviousDate: null
    property double _speedtestRequestedAt: 0
    property bool _loginProcessInFlight: false
    property bool _logoutRequested: false
    property bool _verifyCommitGranted: false
    property var _loginProcess: null

    // Bumped on every state-resetting transition (sendCode, cancelLogin,
    // signOut). Each dispatched status/login call captures the generation
    // it started under; a result that lands after the generation has moved
    // on is discarded so it cannot resurrect state from before a sign-out
    // or a new login attempt.
    property int generation: 0
    onGenerationChanged: {
        root.actionFeedback = ({})
        // An acknowledgement fenced by a new login generation cannot strand
        // pending readback or suppress the next Action indefinitely.
        if (root.guestState === "Sending request" || root.guestState === "Awaiting readback") {
            root.guestState = "Readback unconfirmed; refresh to check"
            root._guestExpected = null
        }
        if (root.speedtestState === "Sending request" || root.speedtestState === "Result pending")
            root.speedtestState = "Result unconfirmed; refresh to check"
    }

    // refresh() called while a status call is already in flight sets this
    // instead of dropping the request; the in-flight call's settle handler
    // runs exactly one more refresh() when it sees this set. Without this, a
    // refresh() that loses the race against a poll tick (post-action,
    // post-verify) was simply lost, leaving stale state on screen.
    property bool _refreshQueued: false

    // True while signOut()'s `logout` call is in flight. sendCode() queues
    // behind it (see _queuedSendCode) rather than racing it: both commands
    // rewrite the session file, and a slow logout finishing after a fresh
    // login started could delete the new pending login token.
    property bool _logoutInFlight: false
    property var _queuedSendCode: null // {identifier, phase} | null
    // The phase a login attempt started from, when that phase was a live
    // one ("ready"/"error", or "loading" while confirming a committed login).
    // Helper keeps the old token while pending, so aborting restores this phase
    // instead of dropping to signedOut. null for login started from signedOut.
    property var _phaseBeforeLogin: null

    // Every owned helper may persist Session/cache updates. Keep ownership
    // through actual exit/start failure, even after a timeout clears UI flags.
    property var _activeProcesses: []

    readonly property string helperPath: {
        var url = String(Qt.resolvedUrl("bin/eero-helper"))
        return decodeURIComponent(url.replace(/^file:\/\//, ""))
    }

    // -- public functions -----------------------------------------------

    function configure(settings) {
        var opts = settings && typeof settings === "object" ? settings : {}
        var poll = opts.pollSeconds
        var pollNum = Number(poll)
        if ((typeof poll !== "number" && typeof poll !== "string")
            || (typeof poll === "string" && poll.trim() === "") || !isFinite(pollNum)) pollNum = 60
        root.pollSeconds = Math.max(30, Math.min(3600, Math.floor(pollNum)))
        root.showClientCount = opts.showClientCount === true
        root.density = opts.density === "comfortable" ? "comfortable" : "compact"
        root.showOfflineClients = opts.showOfflineClients === true
    }

    function isBusy(key) {
        return root.busyActions[key] === true
    }

    function clearError() {
        root.lastError = null
        root.actionError = null
    }

    // Clears only actionError, leaving a status/login lastError on screen.
    // Ticket 03's action-error banner dismiss (×) uses this instead of
    // clearError() so dismissing "reboot failed" doesn't also silently drop
    // an unrelated "could not reach eero" banner from the last status poll.
    function clearActionError() {
        root.actionError = null
    }

    function refresh() {
        // Ticket 03 calls refreshIfStale() on popup open; a poll tick or
        // that call must never interrupt an in-progress login by resolving
        // to auth_required/signedOut out from under the code-entry step.
        if (root.phase === "codeSent") return
        // A `status` call dispatched while `logout` is in flight could read
        // the session before `logout` deletes it and resolve successfully
        // afterward, resurrecting `ready` + a snapshot right after the user
        // signed out (reproduced headlessly). signOut() already sets phase
        // to "signedOut" synchronously, but phase alone does not block
        // refresh() (a plain refresh() call is otherwise valid in every
        // phase but codeSent), so this needs its own guard.
        if (root._logoutInFlight) return
        if (root._loginProcessInFlight || root._logoutRequested) return
        if (root.loginBusy || root._queuedSendCode || root.phase === "amazon") return
        if (root.refreshing) {
            root._refreshQueued = true
            return
        }
        root.refreshing = true
        var myGeneration = root.generation
        root._runHelper(["status"], {
            onSuccess: function(data) {
                root.refreshing = false
                root._runQueuedRefresh()
                if (myGeneration !== root.generation) return
                if (root.phase === "codeSent") return
                root.snapshot = { network: data.network || null, nodes: data.nodes || [],
                    clients: data.clients || [], profiles: data.profiles || [] }
                root.lastFetchedAt = typeof data.fetchedAt === "string" ? data.fetchedAt : null
                root.lastError = null
                root.phase = "ready"
                root._observeActions()
            },
            onError: function(code, message) {
                root.refreshing = false
                root._runQueuedRefresh()
                if (myGeneration !== root.generation) return
                if (root.phase === "codeSent") return
                if (code === "auth_required") {
                    root.phase = "signedOut"
                    root.snapshot = null
                    root.maskedIdentifier = ""
                    root.lastFetchedAt = null
                    root.lastError = null
                    return
                }
                root.lastError = { code: code, message: message }
                // Keep the stale snapshot (and whatever phase it implies);
                // only fall back to "error" when there is nothing to show.
                if (!root.snapshot) root.phase = "error"
            }
        })
    }

    function refreshIfStale(maxAgeSec) {
        if (root.phase === "codeSent") return
        if (typeof root.lastFetchedAt !== "string") {
            root.refresh()
            return
        }
        var fetchedMs = Date.parse(root.lastFetchedAt)
        if (!isFinite(fetchedMs)) {
            root.refresh()
            return
        }
        if ((Date.now() - fetchedMs) / 1000 >= maxAgeSec) root.refresh()
    }

    function startAmazonLogin() {
        if (root.loginBusy || root._queuedSendCode || root.phase === "codeSent") return
        root.loginBusy = true
        root.lastError = null
        root._queuedSendCode = { amazon: true, phase: root.phase }
        amazonQueueTimer.start()
    }

    function _dispatchAmazonLogin() {
        root.generation += 1
        root._phaseBeforeLogin = (root.phase === "ready" || root.phase === "error" || root.phase === "loading") ? root.phase : null
        var myGeneration = root.generation
        root.phase = "amazon"
        root.amazonPhase = "browser_open"
        root._loginProcessInFlight = true
        root._verifyCommitGranted = false
        root._runHelper(["amazon-login"], {
            keepStdin: true,
            amazon: true,
            onAmazonProgress: function(phase) {
                if (myGeneration !== root.generation) return
                root.amazonPhase = phase
            },
            onSettled: function() { root._loginSettled() },
            onSuccess: function() {
                if (myGeneration !== root.generation) return
                root.loginBusy = !!root._queuedSendCode
                root.amazonPhase = ""
                root._phaseBeforeLogin = null
                root.phase = "loading"
                root.refresh()
            },
            onError: function(code, message) {
                if (myGeneration !== root.generation) return
                root.loginBusy = !!root._queuedSendCode
                root.amazonPhase = ""
                root.phase = root._phaseBeforeLogin || "signedOut"
                root._phaseBeforeLogin = null
                root.lastError = { code: code, message: message }
            }
        })
    }

    function approveAmazonSession() {
        if (root.phase !== "amazon" || root.amazonPhase !== "verification_ready"
            || !root._loginProcess || root._verifyCommitGranted) return
        root._verifyCommitGranted = true
        root._loginProcess.approve()
        root.amazonPhase = "saving"
    }

    function sendCode(identifier) {
        if (root.loginBusy || root._queuedSendCode) return
        if (typeof identifier !== "string" || identifier.trim() === "") {
            root.lastError = { code: "usage", message: "Enter an email address or phone number." }
            return
        }
        if (root._activeProcesses.length > 0 || root._logoutRequested) {
            // Reserve the identity transition immediately, then drain every
            // owned writer before starting the new login.
            root.loginBusy = true
            root.lastError = null
            root._queuedSendCode = { identifier: identifier,
                phase: root._phaseBeforeLogin || root.phase }
            identifier = null
            return
        }
        root._dispatchSendCode(identifier)
        identifier = null
    }

    function verifyCode(code) {
        if (root.loginBusy || root.phase !== "codeSent" || root._loginProcessInFlight) return
        if (typeof code !== "string" || code.trim() === "") {
            root.lastError = { code: "usage", message: "Enter the verification code." }
            return
        }
        var myGeneration = root.generation
        var secret = code
        code = null
        root.loginBusy = true
        root.lastError = null
        root._loginProcessInFlight = true
        root._verifyCommitGranted = false
        root._runHelper(["verify"], {
            secret: secret,
            keepStdin: true,
            onVerificationReady: function() {
                if (myGeneration !== root.generation) return false
                // Approval is the successful verification linearization point.
                // No pending login remains to cancel after this grant.
                root._verifyCommitGranted = true
                root.loginBusy = false
                root.maskedIdentifier = ""
                root.phase = "loading"
                return true
            },
            onSettled: function() { root._loginSettled() },
            onSuccess: function(data) {
                if (myGeneration !== root.generation) return
                root.loginBusy = !!root._queuedSendCode
                root.maskedIdentifier = ""
                root._phaseBeforeLogin = null
                // No longer codeSent as of this line -- refresh() itself
                // would otherwise no-op while phase is still "codeSent".
                root.phase = "loading"
                root.refresh()
            },
            onError: function(errCode, message) {
                if (myGeneration !== root.generation) return
                root.loginBusy = !!root._queuedSendCode
                if (root._verifyCommitGranted) {
                    root.phase = root._phaseBeforeLogin || "signedOut"
                    root._phaseBeforeLogin = null
                }
                // login_rejected (or anything else): stay in the current
                // phase (codeSent) and just surface the error.
                root.lastError = { code: errCode, message: message }
            }
        })
        secret = null
    }

    function cancelLogin() {
        // The queued attempt does not own the predecessor's approval or stdin.
        // Cancel it separately, including after that predecessor was committed.
        if (root._queuedSendCode) {
            var queuedPhase = root._queuedSendCode.phase
            root._queuedSendCode = null
            root.loginBusy = false
            root.lastError = null
            if (!root._verifyCommitGranted) {
                root.maskedIdentifier = ""
                root.phase = queuedPhase
                if (queuedPhase === "ready" || queuedPhase === "error" || queuedPhase === "loading")
                    root.refresh()
            }
            return
        }
        // A no-op outside an active login attempt (codeSent) or a pending
        // one (loginBusy, including one still queued behind a signOut()).
        if (root.phase !== "codeSent" && !root.loginBusy) return
        if (root._verifyCommitGranted) return
        root.amazonPhase = ""
        root.generation += 1
        if (root._loginProcess) root._loginProcess.stdinEnabled = false
        root.loginBusy = false
        root.maskedIdentifier = ""
        root.lastError = null
        root._queuedSendCode = null
        // A re-login started over a live session restores it rather than
        // dropping to signedOut -- the helper kept the old token while the
        // attempt was pending, and one refresh() confirms it still works.
        var previous = root._phaseBeforeLogin
        root._phaseBeforeLogin = null
        if (previous === "ready" || previous === "error" || previous === "loading") {
            root.phase = previous
            root.refresh()
        } else {
            root.phase = "signedOut"
        }
    }

    function signOut() {
        // Switch state immediately -- do not wait for `logout` to answer.
        root.generation += 1
        root.amazonPhase = ""
        if (root._loginProcess && !root._verifyCommitGranted) root._loginProcess.stdinEnabled = false
        root.phase = "signedOut"
        root.snapshot = null
        root.lastFetchedAt = null
        root.maskedIdentifier = ""
        root.lastError = null
        root.actionError = null
        root.guestState = ""
        root.speedtestState = ""
        root._guestExpected = null
        root.loginBusy = false
        // Deliberately NOT touching `refreshing` here: if a `status` call
        // is still in flight, it alone owns clearing `refreshing` (and
        // running any queued refresh) when it settles. Forcing it false
        // here let a second refresh() dispatch while the first was still
        // running -- two status calls in flight at once (reproduced
        // headlessly) -- because refresh()'s own in-flight check reads
        // `refreshing`. The in-flight call's generation check (below,
        // inside refresh()'s callbacks) already keeps its stale reply from
        // touching phase/snapshot; refresh() also now no-ops entirely while
        // _logoutInFlight, so nothing can dispatch a fresh status call into
        // this window either.
        root._refreshQueued = false
        root._queuedSendCode = null
        root._phaseBeforeLogin = null
        // pollTimer.running is bound to phase, so it stops on its own now
        // that phase is "signedOut".
        root._logoutRequested = true
        if (root._activeProcesses.length > 0) return
        root._dispatchLogout()
    }

    function _dispatchLogout() {
        root._logoutRequested = false
        root._logoutInFlight = true
        root._runHelper(["logout"], {
            onSettled: function() {
                root._logoutInFlight = false
                root._drainIdentityQueue()
            },
            onError: function(code, message) {
                // logout is documented to never fail (docs/helper-protocol.md),
                // but if it somehow does: the user is signed out locally
                // either way, so just surface it as long as nothing else has
                // moved the service on in the meantime.
                if (root.phase === "signedOut") root.lastError = { code: code, message: message }
            }
        })
    }

    function rebootNode(url) {
        var key = EeroModel.actionKey("reboot-node", url)
        if (!EeroModel.nodeForUrl(root.snapshot, url) || (root.phase !== "ready" && root.phase !== "error") || root.loginBusy) {
            root.actionError = { key: key, code: "usage", message: "Invalid node." }
            return
        }
        root._runAction(key, ["reboot-node", url])
    }

    function rebootNetwork() {
        if (!root.snapshot || !root.snapshot.network || !EeroModel.isNetworkUrl(root.snapshot.network.url)
            || (root.phase !== "ready" && root.phase !== "error") || root.loginBusy) {
            root.actionError = { key: EeroModel.actionKey("reboot-network", ""), code: "usage", message: "Invalid Network." }
            return
        }
        root._runAction(EeroModel.actionKey("reboot-network", ""), ["reboot-network"])
    }

    function setGuest(on) {
        if (!root._actionsAllowed(EeroModel.actionKey("guest", ""))) return
        if (root.isBusy(EeroModel.actionKey("guest", "")) || root._guestExpected !== null) return
        root._guestExpected = on === true
        root.guestState = "Sending request"
        root._runAction(EeroModel.actionKey("guest", ""), ["guest", on === true ? "on" : "off"])
    }

    function setLed(url, on) {
        var key = EeroModel.actionKey("led", url)
        var node = EeroModel.nodeForUrl(root.snapshot, url)
        if (!node || typeof node.ledOn !== "boolean" || (root.phase !== "ready" && root.phase !== "error") || root.loginBusy) {
            root.actionError = { key: key, code: "usage", message: "Invalid node." }
            return
        }
        if (root.isBusy(key) || root._ledPending(key)) return
        root._runAction(key, ["led", url, on === true ? "on" : "off"])
    }

    function pauseClient(mac, on) {
        var lowered = typeof mac === "string" ? mac.toLowerCase() : mac
        var key = EeroModel.actionKey("pause-client", lowered)
        var clients = root.snapshot ? root.snapshot.clients : []
        var client = clients.find(function(row) { return row && row.mac === lowered })
        if (!EeroModel.isMac(lowered) || !client || typeof client.paused !== "boolean"
            || root.loginBusy || (root.phase !== "ready" && root.phase !== "error")) {
            root.actionError = { key: key, code: "usage", message: "Invalid client." }
            return
        }
        root._runAction(key, ["pause-client", lowered, on === true ? "on" : "off"])
    }

    function pauseProfile(url, on) {
        var key = EeroModel.actionKey("pause-profile", url)
        var profiles = root.snapshot ? root.snapshot.profiles : []
        var profile = profiles.find(function(row) { return row && row.url === url })
        if (!EeroModel.isProfileUrl(url) || !profile || typeof profile.paused !== "boolean"
            || root.loginBusy || (root.phase !== "ready" && root.phase !== "error")) {
            root.actionError = { key: key, code: "usage", message: "Invalid profile." }
            return
        }
        root._runAction(key, ["pause-profile", url, on === true ? "on" : "off"])
    }

    function runSpeedtest() {
        if (!root._actionsAllowed(EeroModel.actionKey("speedtest", ""))) return
        if (root.isBusy(EeroModel.actionKey("speedtest", "")) || root.speedtestState === "Result pending") return
        root._speedtestRequestedAt = Date.now()
        root._speedtestPreviousDate = root.snapshot && root.snapshot.network && root.snapshot.network.speed
            ? root.snapshot.network.speed.date : null
        root.speedtestState = "Sending request"
        root._runAction(EeroModel.actionKey("speedtest", ""), ["speedtest"])
    }

    // -- internal ---------------------------------------------------------

    function _observeActions() {
        var keys = Object.keys(root.actionFeedback)
        for (var i = 0; i < keys.length; i++) {
            var feedback = root.actionFeedback[keys[i]]
            if (feedback.state !== "awaiting") continue
            var node = feedback.kind === "led" ? EeroModel.nodeForUrl(root.snapshot, feedback.url) : null
            var actual = node ? node.ledOn : null
            var rows = feedback.kind === "pause-client" ? root.snapshot.clients
                : feedback.kind === "pause-profile" ? root.snapshot.profiles : []
            for (var j = 0; j < rows.length; j++)
                if (rows[j] && (feedback.kind === "pause-client" ? rows[j].mac === feedback.url : rows[j].url === feedback.url))
                    actual = rows[j].paused
            if (actual === feedback.expected)
                root._setFeedback(keys[i], { state: "confirmed" })
        }
        var network = root.snapshot ? root.snapshot.network : null
        if (root._guestExpected !== null && network && network.guestEnabled === root._guestExpected
            && root.guestState === "Awaiting readback") {
            root.guestState = "Confirmed by readback"
            root._guestExpected = null
        }
        if ((root.speedtestState === "Result pending" || root.speedtestState === "Result unconfirmed; refresh to check")
            && EeroModel.hasNewSpeedResult(network ? network.speed : null, root._speedtestPreviousDate, root._speedtestRequestedAt)) {
            root.speedtestState = "New measurement received"
        }
    }

    function _loginSettled() {
        root._loginProcessInFlight = false
        root._verifyCommitGranted = false
        root._loginProcess = null
        if (!root._logoutRequested && !root._queuedSendCode
            && (root.phase === "ready" || root.phase === "error" || root.phase === "loading")) root.refresh()
    }

    function _runQueuedRefresh() {
        if (!root._refreshQueued) return
        root._refreshQueued = false
        root.refresh()
    }

    function _dispatchSendCode(identifier) {
        root.generation += 1
        // Remember where the login started, so cancelLogin() can restore a
        // live session (the helper's login keeps the old token pending).
        root._phaseBeforeLogin = (root.phase === "ready" || root.phase === "error" || root.phase === "loading") ? root.phase : null
        var myGeneration = root.generation
        var secret = identifier
        identifier = null
        root.loginBusy = true
        root.lastError = null
        root._loginProcessInFlight = true
        root._runHelper(["login"], {
            secret: secret,
            onSettled: function() { root._loginSettled() },
            onSuccess: function(data) {
                if (myGeneration !== root.generation) return
                root.loginBusy = !!root._queuedSendCode
                root.maskedIdentifier = typeof data.identifier === "string" ? data.identifier : ""
                root.phase = "codeSent"
            },
            onError: function(code, message) {
                if (myGeneration !== root.generation) return
                root.loginBusy = !!root._queuedSendCode
                root.lastError = { code: code, message: message }
            }
        })
        secret = null
    }

    function _drainIdentityQueue() {
        if (root._activeProcesses.length > 0) return
        if (root._logoutRequested) root._dispatchLogout()
        else root._runQueuedSendCode()
    }

    function _runQueuedSendCode() {
        if (root._activeProcesses.length > 0 || root._logoutRequested) return
        var queued = root._queuedSendCode
        if (!queued) return
        root.loginBusy = true
        if (queued.amazon) amazonQueueTimer.start()
        else {
            root._queuedSendCode = null
            root._dispatchSendCode(queued.identifier)
        }
    }

    function _markBusy(key) {
        var next = {}
        for (var existing in root.busyActions) next[existing] = root.busyActions[existing]
        next[key] = true
        root.busyActions = next
    }

    function _clearBusy(key) {
        var next = {}
        for (var existing in root.busyActions) {
            if (existing !== key) next[existing] = root.busyActions[existing]
        }
        root.busyActions = next
    }

    function _actionsAllowed(key) {
        if (root.actionsEnabled) return true
        var message = "Actions are unavailable until sign-in settles and a fresh Snapshot is ready."
        root.actionError = { key: key, code: "state", message: message }
        root._setFeedback(key, { state: "failed", code: "state", message: message })
        return false
    }

    function _runAction(key, args) {
        if (root.isBusy(key) || root._ledPending(key)) return
        if (!root._actionsAllowed(key)) return
        root.actionError = null
        root._markBusy(key)
        if (args[0] === "led" || args[0] === "pause-client" || args[0] === "pause-profile")
            root._setFeedback(key, { state: "sending", kind: args[0], expected: args[2] === "on", url: args[1] })
        if (args[0] === "reboot-node" || args[0] === "reboot-network")
            root._setFeedback(key, { state: "sending" })
        var myGeneration = root.generation
        root._runHelper(args, {
            onSuccess: function(data) {
                // Always clear the busy flag -- the process genuinely
                // finished, and leaving isBusy(key) stuck true forever
                // would strand the UI. But an action that settles after a
                // signOut()/cancelLogin()/sendCode() moved the generation
                // on must not set actionError or trigger a refresh() (which
                // would otherwise run a fresh, generation-blind `status`
                // call of its own).
                root._clearBusy(key)
                if (myGeneration !== root.generation) return
                if (args[0] === "led" || args[0] === "pause-client" || args[0] === "pause-profile")
                    root._setFeedback(key, { state: "awaiting", kind: args[0], expected: args[2] === "on",
                        url: args[1], deadline: Date.now() + 120000 })
                if (args[0] === "reboot-node" || args[0] === "reboot-network")
                    root._setFeedback(key, { state: "acknowledged" })
                if (args[0] === "guest") {
                    root.guestState = "Awaiting readback"
                    guestObservationTimer.restart()
                }
                if (args[0] === "speedtest") {
                    root.speedtestState = "Result pending"
                    speedObservationTimer.restart()
                }
                root.refresh()
            },
            onError: function(code, message) {
                root._clearBusy(key)
                if (myGeneration !== root.generation) return
                root.actionError = { key: key, code: code, message: message }
                root._setFeedback(key, { state: "failed", code: code, message: message })
                if (args[0] === "guest") {
                    root.guestState = "Request failed"
                    root._guestExpected = null
                }
                if (args[0] === "speedtest") root.speedtestState = "Request failed"
                root.refresh()
            }
        })
    }

    // Runs one helper invocation. `options`: { secret: string=, onSuccess:
    // function(data), onError: function(code, message) }. `data` is the
    // full parsed success payload (docs/helper-protocol.md's fields live at
    // its top level, e.g. data.network, data.identifier).
    function _runHelper(args, options) {
        var opts = options || {}
        // -B: never write lib/__pycache__ into the installed plugin directory
        // (ticket 08; the dir is gitignored but the file-watcher noise isn't).
        var wrapper = helperProcessComponent.createObject(root, {
            command: ["python3", "-B", root.helperPath].concat(args)
        })
        if (!wrapper) {
            if (opts.onError) opts.onError("api", "eero-helper could not be started.")
            if (opts.onSettled) opts.onSettled()
            return
        }
        root._activeProcesses.push(wrapper)
        if (args[0] === "login" || args[0] === "verify" || args[0] === "amazon-login") root._loginProcess = wrapper
        var detach = function() {
            var index = root._activeProcesses.indexOf(wrapper)
            if (index >= 0) root._activeProcesses.splice(index, 1)
            if (opts.onSettled) opts.onSettled()
            root._drainIdentityQueue()
        }
        // Both failure shapes ("timed out" and "failed to start") surface
        // through the same callback/error code: from the caller's point of
        // view both just mean the helper never answered.
        wrapper.onTimeout = function(message) {
            // Notification only: the child may still be writing after kill.
            if (opts.onError) opts.onError("network", message)
        }
        wrapper.onResult = function(text, exitCode) {
            var result = EeroModel.parseHelperOutput(text, exitCode)
            if (result.ok && opts.keepStdin && (result.type !== "logged_in" || !wrapper.approvalHandled)) {
                if (opts.onError) opts.onError("api", "Helper did not confirm the approved login.")
            } else if (result.ok) {
                if (opts.onSuccess) opts.onSuccess(result.data)
            } else if (opts.onError) {
                opts.onError(result.code, result.message)
            }
        }
        wrapper.settledCallback = detach
        wrapper.onVerificationReady = opts.onVerificationReady || null
        wrapper.keepStdin = opts.keepStdin === true
        wrapper.amazon = opts.amazon === true
        wrapper.onAmazonProgress = opts.onAmazonProgress || null
        if (wrapper.amazon) wrapper.stdinEnabled = true
        if (typeof opts.secret === "string") {
            wrapper.pendingStdin = opts.secret
            wrapper.hasStdin = true
            // Must be enabled before running = true: Quickshell only opens
            // a stdin pipe to the child if stdinEnabled is already true when
            // the process starts (confirmed by running Service.qml headless
            // against a stub helper -- with stdinEnabled left false until
            // onStarted, the child's stdin read blocked forever instead of
            // seeing EOF). onStarted's write()+stdinEnabled=false below is
            // what then closes that already-open pipe.
            wrapper.stdinEnabled = true
        }
        wrapper.start()
    }

    // One Process per helper invocation, created from this Component via
    // createObject(). Results/errors notify once; ownership and destruction
    // wait for actual exit or start failure. Wrapped in an Item
    // rather than instantiated as a bare Process: Quickshell's Process has
    // no default property (confirmed by qmllint: nesting the kill Timer
    // directly inside a Process fails with "Cannot assign to non-existent
    // default property"), so the kill Timer is a plain Item child instead,
    // and the Item forwards the handful of Process properties/methods
    // callers need (command, stdinEnabled, start(), kill()).
    //
    // write()+stdinEnabled=false (rather than any "close" method --
    // Quickshell's Process exposes no such method; see
    // quickshell-io.qmltypes) is the documented way to give a stdin-reading
    // child its EOF once the pipe is open (see the stdinEnabled=true comment
    // in _runHelper above): every stdin-writing Process in the installed
    // shell (omarchy plugins under ~/.config/omarchy/plugins/, e.g.
    // mihoro.omarchy/Service.qml) sets stdinEnabled = true before starting,
    // then write()s and sets stdinEnabled = false in onStarted.
    Component {
        id: helperProcessComponent

        Item {
            id: wrapper

            property alias command: proc.command
            property alias stdinEnabled: proc.stdinEnabled
            property string pendingStdin: ""
            property bool hasStdin: false
            property bool exitedFlag: false
            property bool streamDone: false
            property bool settled: false
            property int lastExitCode: -1
            property var onResult: null  // function(text, exitCode)
            property var onTimeout: null // function(message)
            property var settledCallback: null
            property var onVerificationReady: null
            property bool keepStdin: false
            property bool approvalHandled: false
            property bool amazon: false
            property var onAmazonProgress: null
            property int progressOffset: 0

            function approve() {
                if (wrapper.settled || wrapper.approvalHandled || !wrapper.amazon) return
                wrapper.approvalHandled = true
                proc.write("commit\n")
                proc.stdinEnabled = false
            }

            // Starts the kill/start-failure watchdog at dispatch time, not
            // in reaction to `running` becoming true: a process that fails
            // to start can flip running straight back to false before any
            // binding-driven "start the timer on running" handler would be
            // useful.
            function start() {
                killTimer.restart()
                proc.running = true
            }

            function kill(number) {
                try {
                    proc.signal(number)
                } catch (error) {
                }
            }

            function _clearSecret() {
                wrapper.pendingStdin = ""
                wrapper.hasStdin = false
            }

            // Settles exactly once (guarded by `settled`) on every path --
            // normal exit, kill-timeout, or start failure -- and always
            // destroys the wrapper afterward.
            function _settleResult(text, exitCode) {
                if (wrapper.settled) return
                wrapper.settled = true
                killTimer.stop()
                startFailureTimer.stop()
                wrapper._clearSecret()
                if (wrapper.onResult) wrapper.onResult(text, exitCode)
                if (wrapper.settledCallback) wrapper.settledCallback()
                wrapper.destroy()
            }

            function _settleFailure(message) {
                if (wrapper.settled) return
                wrapper.settled = true
                killTimer.stop()
                startFailureTimer.stop()
                wrapper._clearSecret()
                wrapper.stdinEnabled = false
                wrapper.kill(wrapper.amazon ? 15 : 9)
                if (wrapper.onTimeout) wrapper.onTimeout(message)
                // Serialize Session writers until the killed process really exits.
                if (wrapper.exitedFlag || !proc.running) {
                    if (wrapper.settledCallback) wrapper.settledCallback()
                    wrapper.destroy()
                }
            }

            function _maybeFinish() {
                if (!(wrapper.exitedFlag && wrapper.streamDone)) return
                wrapper._settleResult(collector.text, wrapper.lastExitCode)
            }

            Process {
                id: proc
                stdinEnabled: false

                stdout: StdioCollector {
                    id: collector
                    waitForEnd: !wrapper.keepStdin
                    onDataChanged: {
                        if (wrapper.amazon && !wrapper.settled && !wrapper.approvalHandled) {
                            var lines = collector.text.slice(wrapper.progressOffset).split("\n")
                            for (var i = 0; i < lines.length - 1; i++) {
                                wrapper.progressOffset += lines[i].length + 1
                                var metadata
                                try { metadata = JSON.parse(lines[i]) } catch (error) { continue }
                                if (metadata && metadata.ok === true && metadata.type === "amazon_progress"
                                    && Object.keys(metadata).length === 3
                                    && ["browser_open", "exchanging", "verification_ready"].indexOf(metadata.phase) >= 0
                                    && wrapper.onAmazonProgress) wrapper.onAmazonProgress(metadata.phase)
                            }
                            return
                        }
                        if (!wrapper.keepStdin || wrapper.approvalHandled || wrapper.settled) return
                        var newline = collector.text.indexOf("\n")
                        if (newline < 0) return
                        var progress
                        try { progress = JSON.parse(collector.text.slice(0, newline)) } catch (error) { return }
                        if (!progress || progress.ok !== true || progress.type !== "verification_ready"
                            || Object.keys(progress).length !== 2) return
                        wrapper.approvalHandled = true
                        if (wrapper.onVerificationReady && wrapper.onVerificationReady()) proc.write("commit\n")
                        proc.stdinEnabled = false
                    }
                    onStreamFinished: {
                        wrapper.streamDone = true
                        wrapper._maybeFinish()
                    }
                }

                onStarted: {
                    if (wrapper.hasStdin) {
                        var secret = wrapper.pendingStdin
                        wrapper.pendingStdin = ""
                        wrapper.hasStdin = false
                        proc.write(secret + "\n")
                        secret = null
                        if (!wrapper.keepStdin) proc.stdinEnabled = false
                    }
                }

                // qmllint disable signal-handler-parameters
                // exited's second parameter (QProcess::ExitStatus) has no
                // resolvable QML type in this import set -- the same
                // warning appears on every Process.onExited handler in this
                // shell, including n501.karaoke's. Not needed here anyway:
                // wrapper._settleFailure() already handles a killed/crashed
                // process (kill-timeout, start-failure), and the exit code
                // alone is enough for EeroModel.parseHelperOutput to treat
                // any nonzero/malformed exit as an error.
                onExited: function(exitCode) {
                    wrapper.lastExitCode = exitCode
                    wrapper.exitedFlag = true
                    if (wrapper.settled) {
                        if (wrapper.settledCallback) wrapper.settledCallback()
                        wrapper.destroy()
                    } else wrapper._maybeFinish()
                }
                // qmllint enable signal-handler-parameters

                onRunningChanged: {
                    if (!proc.running && !wrapper.exitedFlag && !wrapper.settled) {
                        // `running` went false without `exited` ever firing
                        // -- most likely the process failed to start at all
                        // (missing interpreter/helper binary). Give any
                        // already-queued `exited` one tick to land first.
                        startFailureTimer.restart()
                    }
                }
            }

            Timer {
                id: killTimer
                interval: wrapper.amazon ? 690000 : 30000
                running: false
                onTriggered: wrapper._settleFailure("eero helper timed out")
            }

            Timer {
                id: startFailureTimer
                interval: 20
                running: false
                onTriggered: {
                    if (wrapper.settled || wrapper.exitedFlag) return
                    wrapper._settleFailure("eero helper failed to start")
                }
            }
        }
    }

    Timer {
        interval: 1000
        repeat: true
        running: Object.keys(root.actionFeedback).some(function(key) {
            return root.actionFeedback[key].state === "awaiting"
        })
        onTriggered: {
            var keys = Object.keys(root.actionFeedback)
            for (var i = 0; i < keys.length; i++) {
                var feedback = root.actionFeedback[keys[i]]
                if (feedback.state === "awaiting" && Date.now() >= feedback.deadline)
                    root._setFeedback(keys[i], { state: "unconfirmed" })
            }
        }
    }

    Timer {
        id: amazonQueueTimer
        interval: 100
        repeat: true
        onTriggered: {
            if (!root._queuedSendCode || !root._queuedSendCode.amazon) { stop(); return }
            if (root._activeProcesses.length > 0 || root._logoutRequested) return
            root._queuedSendCode = null
            stop()
            root._dispatchAmazonLogin()
        }
    }

    Timer {
        id: guestObservationTimer
        interval: 120000
        onTriggered: {
            if (root.guestState === "Awaiting readback") {
                root.guestState = "Readback unconfirmed; refresh to check"
                root._guestExpected = null
            }
        }
    }

    Timer {
        id: speedObservationTimer
        interval: 120000
        onTriggered: {
            if (root.speedtestState === "Result pending")
                root.speedtestState = "Result unconfirmed; refresh to check"
        }
    }

    Timer {
        id: pollTimer
        interval: root.pollSeconds * 1000
        repeat: true
        running: root.phase === "ready" || root.phase === "error"
        onTriggered: {
            if (!root.refreshing) root.refresh()
        }
    }

    Component.onCompleted: root.refresh()

    Component.onDestruction: {
        // A queued login identifier (see sendCode()/_queuedSendCode) is
        // never dispatched now, so it never reaches the helper; drop it
        // rather than leaving it sitting in a property.
        root._queuedSendCode = null
        for (var i = 0; i < root._activeProcesses.length; i++) {
            var proc = root._activeProcesses[i]
            try {
                // An in-flight secret write is abandoned along with the
                // process itself.
                proc.pendingStdin = ""
                proc.hasStdin = false
            } catch (error) {
            }
            try {
                proc.stdinEnabled = false
                proc.kill(proc.amazon ? 15 : 9)
            } catch (error) {
            }
        }
    }
}
