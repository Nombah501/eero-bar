.pragma library

// Pure JS model for n501.eero-bar (docs/helper-protocol.md is the wire
// contract; ADR-0001 is the architecture). No QML imports here so this file
// loads unmodified under Node for tests/eero_model.test.cjs.

// -- helper output --------------------------------------------------------

// Parses one helper invocation's stdout + exit code into a normalized
// result. docs/helper-protocol.md: exactly one JSON object per line, exit 0
// iff the object is `ok`. We defend against a helper that logs extra lines,
// trailing blank lines, or a broken pipe: only the LAST non-empty line is
// ever considered.
function parseHelperOutput(stdoutText, exitCode) {
    var lines = String(stdoutText === undefined || stdoutText === null ? "" : stdoutText).split("\n")
    var lastLine = ""
    for (var i = lines.length - 1; i >= 0; i--) {
        var trimmed = lines[i].replace(/^\s+|\s+$/g, "")
        if (trimmed !== "") {
            lastLine = trimmed
            break
        }
    }
    var invalid = { ok: false, type: "error", code: "api", message: "Helper returned invalid output", data: null }
    if (lastLine === "") return invalid

    var parsed
    try {
        parsed = JSON.parse(lastLine)
    } catch (error) {
        return invalid
    }
    if (!parsed || typeof parsed !== "object") return invalid

    if (parsed.ok === true) {
        // The protocol ties exit 0 to an `ok` payload; a nonzero exit paired
        // with an `ok` object is a contract violation, not a success.
        if (exitCode !== 0) return invalid
        return { ok: true, type: typeof parsed.type === "string" ? parsed.type : "", code: null, message: null, data: parsed }
    }

    var code = typeof parsed.code === "string" && parsed.code !== "" ? parsed.code : "api"
    var message = typeof parsed.message === "string" && parsed.message !== "" ? parsed.message : "Helper returned invalid output"
    return { ok: false, type: "error", code: code, message: message, data: null }
}

// -- snapshot summary -------------------------------------------------------

function summarize(snapshot) {
    var networkName = null
    var status = null
    var internetOnline = null
    var nodes = []
    var clients = []

    if (snapshot && typeof snapshot === "object") {
        var network = snapshot.network
        if (network && typeof network === "object") {
            networkName = typeof network.name === "string" ? network.name : null
            status = typeof network.status === "string" ? network.status : null
            if (network.internetOnline === true) internetOnline = true
            else if (network.internetOnline === false) internetOnline = false
        }
        if (Array.isArray(snapshot.nodes)) nodes = snapshot.nodes
        if (Array.isArray(snapshot.clients)) clients = snapshot.clients
    }

    var totalNodes = nodes.length
    var onlineNodes = 0
    var anyNodeOffline = false
    for (var i = 0; i < nodes.length; i++) {
        var node = nodes[i]
        var online = node && typeof node === "object" ? node.online : undefined
        // online === null/undefined (unrecognized status) is excluded from
        // the count but must not by itself downgrade the level -- only a
        // confirmed-offline node (online === false) does that.
        if (online === true) onlineNodes += 1
        else if (online === false) anyNodeOffline = true
    }

    var connectedClients = 0
    var unknownClients = 0
    for (var j = 0; j < clients.length; j++) {
        var client = clients[j]
        if (client && typeof client === "object" && client.connected === true) connectedClients += 1
        else if (!client || (client.connected !== true && client.connected !== false)) unknownClients += 1
    }

    var level = "unknown"
    if (status === "offline" || internetOnline === false) level = "bad"
    else if (status === "degraded" || anyNodeOffline) level = "warn"
    else if (status === "online") level = "ok"

    return {
        level: level,
        onlineNodes: onlineNodes,
        totalNodes: totalNodes,
        connectedClients: connectedClients,
        totalClients: clients.length,
        unknownClients: unknownClients,
        networkName: networkName
    }
}

// bar icon level: adds the "signedOut" state (not part of a snapshot's own
// level) and keeps a stale snapshot's level visible through a transient
// fetch error, while still surfacing connectivity trouble as at least warn.
function barLevel(phase, snapshot, lastError) {
    if (phase === "signedOut" || phase === "codeSent") return "signedOut"
    var level = summarize(snapshot).level
    if (lastError && (lastError.code === "network" || lastError.code === "rate_limited")) {
        if (level === "ok" || level === "unknown") return "warn"
    }
    return level
}

function tooltipText(phase, snapshot, lastError) {
    if (phase === "signedOut" || phase === "codeSent") return "eero · sign in"
    if (!snapshot) {
        if (phase === "loading") return "eero · loading"
        return "eero · Network health unknown"
    }
    var summary = summarize(snapshot)
    var name = summary.networkName || "eero"
    return name + " · health " + summary.level + " · " + summary.onlineNodes + "/" + summary.totalNodes + " nodes · "
        + summary.connectedClients + "/" + summary.totalClients + " clients connected"
        + (summary.unknownClients ? " · " + summary.unknownClients + " unknown" : "")
        + (lastError ? " · last known data; cloud update failed" : "")
}

function visibleClients(snapshot, showOffline) {
    var clients = snapshot && Array.isArray(snapshot.clients) ? snapshot.clients : []
    if (showOffline === true) return clients
    var visible = []
    for (var i = 0; i < clients.length; i++) {
        if (clients[i] && clients[i].connected === true) visible.push(clients[i])
    }
    return visible
}

// Case-insensitive substring filter over client name/ip/mac. Same argument
// shape as visibleClients(snapshot, ...) -- takes the Snapshot, not the
// array. Null-safe: non-object entries are skipped, never thrown. Order
// comes from the Snapshot untouched (the helper sorts connected-first),
// and a match does not care about `connected` -- an offline client is a
// hit too.
function matchClients(snapshot, query) {
    var clients = snapshot && Array.isArray(snapshot.clients) ? snapshot.clients : []
    var needle = typeof query === "string" ? query.trim().toLowerCase() : ""
    var matched = []
    for (var i = 0; i < clients.length; i++) {
        var client = clients[i]
        if (!client || typeof client !== "object") continue
        if (needle === "") {
            matched.push(client)
            continue
        }
        var haystack = [client.name, client.ip, client.mac]
            .map(function (value) { return typeof value === "string" ? value.toLowerCase() : "" })
            .join(" ")
        if (haystack.indexOf(needle) !== -1) matched.push(client)
    }
    return matched
}

// Search always respects the explicitly selected connection scope.
function filterClients(snapshot, query, filters) {
    var opts = filters || {}
    return matchClients(snapshot, query).filter(function(client) {
        if (opts.connection === "connected" && client.connected !== true) return false
        if (opts.connection === "offline" && client.connected !== false) return false
        if (opts.paused === true && client.paused !== true) return false
        if (opts.guest === true && client.guest !== true) return false
        if (opts.node && client.nodeName !== opts.node) return false
        if (opts.profile && client.profileName !== opts.profile) return false
        return true
    }).sort(function(a, b) {
        var connected = Number(b.connected === true) - Number(a.connected === true)
        return connected || displayValue(a.name).toLowerCase().localeCompare(displayValue(b.name).toLowerCase())
    })
}

function displayValue(value) {
    return typeof value === "string" && value.trim() !== "" ? value : "Unknown"
}

function booleanLabel(value, yes, no) {
    return value === true ? yes : value === false ? no : "Unknown"
}

function clientByMac(snapshot, mac) {
    if (!isMac(mac)) return null
    var clients = snapshot && Array.isArray(snapshot.clients) ? snapshot.clients : []
    for (var i = 0; i < clients.length; i++)
        if (clients[i] && typeof clients[i].mac === "string" && clients[i].mac.toLowerCase() === mac.toLowerCase())
            return clients[i]
    return null
}

function formatSpeed(speed) {
    if (!speed || typeof speed !== "object") return "—"
    var down = speed.down
    var up = speed.up
    var hasDown = typeof down === "number" && isFinite(down)
    var hasUp = typeof up === "number" && isFinite(up)
    if (!hasDown && !hasUp) return "—"
    var units = typeof speed.units === "string" && speed.units !== "" ? speed.units : "units unknown"
    var parts = []
    if (hasDown) parts.push("↓" + Math.round(down))
    if (hasUp) parts.push("↑" + Math.round(up))
    return parts.join(" ") + " " + units
}

// Only a strictly newer, parseable measurement confirms an async request.
function hasNewSpeedResult(speed, previousDate, requestedAt) {
    if (!speed || !((typeof speed.down === "number" && isFinite(speed.down) && speed.down >= 0)
        || (typeof speed.up === "number" && isFinite(speed.up) && speed.up >= 0))) return false
    var next = speed && typeof speed.date === "string" ? Date.parse(speed.date) : NaN
    var previous = typeof previousDate === "string" ? Date.parse(previousDate) : NaN
    var baseline = isFinite(previous) ? previous : requestedAt
    return isFinite(next) && typeof baseline === "number" && isFinite(baseline) && next > baseline
}

function relativeTime(iso, nowMs) {
    if (typeof iso !== "string" || iso === "") return ""
    var then = Date.parse(iso)
    if (!isFinite(then)) return ""
    var now = typeof nowMs === "number" && isFinite(nowMs) ? nowMs : Date.now()
    var diffMs = now - then
    if (diffMs < 0) diffMs = 0
    var diffSec = Math.floor(diffMs / 1000)
    if (diffSec < 60) return "just now"
    var diffMin = Math.floor(diffSec / 60)
    if (diffMin < 60) return diffMin + " min ago"
    var diffHour = Math.floor(diffMin / 60)
    if (diffHour < 24) return diffHour + " h ago"
    var diffDay = Math.floor(diffHour / 24)
    return diffDay + " d ago"
}

// Human text for docs/helper-protocol.md's error codes. auth_required and
// network always show the fixed copy below -- a stale/odd helper message
// must never contradict "you're signed out" or "you're offline". Every
// other code (including an unrecognized one) prefers a non-empty helper
// message, since the helper's text is usually more specific than the
// generic per-code line (e.g. "api"'s "Network changed; refresh and try
// again." from docs/helper-protocol.md); the generic line is only a
// fallback for when the helper didn't send one.
function errorText(code, message) {
    if (code === "auth_required") return "Sign in to eero to continue."
    if (code === "network") return "Could not reach eero. Check your connection."
    if (typeof message === "string" && message !== "") return message
    switch (code) {
    case "login_rejected":
        return "That identifier or code was not accepted."
    case "rate_limited":
        return "Too many requests to eero. Try again shortly."
    case "api":
        return "eero returned an unexpected response."
    case "usage":
        return "That request was invalid."
    case "state":
        return "eero's saved session is unsafe to use. Check file permissions."
    default:
        return "Something went wrong."
    }
}

// -- validators mirroring lib/eero_cli.py's regexes -------------------------

var NODE_URL_RE = /^\/2\.2\/eeros\/[0-9]+$/
var PROFILE_URL_RE = /^\/2\.2\/networks\/[0-9]+\/profiles\/[0-9]+$/
var MAC_RE = /^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/

function isNodeUrl(value) {
    return typeof value === "string" && NODE_URL_RE.test(value)
}

function nodeForUrl(snapshot, url) {
    if (!isNodeUrl(url)) return null
    var nodes = snapshot && Array.isArray(snapshot.nodes) ? snapshot.nodes : []
    for (var i = 0; i < nodes.length; i++)
        if (nodes[i] && nodes[i].url === url) return nodes[i]
    return null
}

function orderedNodes(snapshot) {
    var nodes = snapshot && Array.isArray(snapshot.nodes) ? snapshot.nodes.slice() : []
    return nodes.filter(function(node) { return !!node }).sort(function(a, b) {
        if ((a.gateway === true) !== (b.gateway === true)) return a.gateway === true ? -1 : 1
        return String(a.name || "").toLowerCase().localeCompare(String(b.name || "").toLowerCase())
    })
}

function isNetworkUrl(value) {
    return typeof value === "string" && /^\/2\.2\/networks\/[0-9]+$/.test(value)
}

function isProfileUrl(value) {
    return typeof value === "string" && PROFILE_URL_RE.test(value)
}

function isMac(value) {
    // lib/eero_cli.py._validate_mac lowercases before matching, so a
    // mixed-case mac is accepted the same way here.
    if (typeof value !== "string") return false
    return MAC_RE.test(value.toLowerCase())
}

function actionKey(kind, id) {
    return String(kind) + ":" + String(id === undefined || id === null ? "" : id)
}

// -- glyphs (JetBrainsMono Nerd Font, Material Design Icons set) -----------

var GLYPH_ROUTER = "\u{f0469}"    // md-router_wireless: mesh node / router
var GLYPH_WIFI = "\u{f05a9}"      // md-wifi: wireless client
var GLYPH_ETHERNET = "\u{f0200}"  // md-ethernet: wired client
var GLYPH_PAUSE = "\u{f03e5}"     // md-pause_circle: pause client/profile
var GLYPH_REBOOT = "\u{f0709}"    // md-restart: reboot node/network
var GLYPH_LED = "\u{f032c}"      // md-led_on: node LED toggle
var GLYPH_LOGIN = "\u{f0343}"    // md-login_variant: re-login / switch account
var GLYPH_GUEST = "\u{f05f9}"     // md-incognito: guest network
var GLYPH_WARNING = "\u{f0026}"   // md-alert: warning/degraded state

// Fingerprint is equivalence for read-only inspection, never an Action identity.
function clientDisplayKey(client) {
    if (!client) return ""
    return JSON.stringify(["mac", "name", "ip", "connected", "wireless", "paused", "guest", "nodeName", "profileName"].map(function(field) {
        return client[field] === undefined ? null : client[field]
    }))
}
function equivalentClient(snapshot, selected) {
    var clients = snapshot && Array.isArray(snapshot.clients) ? snapshot.clients : []
    var key = clientDisplayKey(selected)
    var matches = clients.filter(function(client) { return client && clientDisplayKey(client) === key })
    return matches.length === 1 ? matches[0] : null
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        parseHelperOutput: parseHelperOutput,
        summarize: summarize,
        barLevel: barLevel,
        tooltipText: tooltipText,
        visibleClients: visibleClients,
        matchClients: matchClients,
        filterClients: filterClients,
        displayValue: displayValue,
        booleanLabel: booleanLabel,
        clientByMac: clientByMac,
        clientDisplayKey: clientDisplayKey,
        equivalentClient: equivalentClient,
        formatSpeed: formatSpeed,
        hasNewSpeedResult: hasNewSpeedResult,
        relativeTime: relativeTime,
        errorText: errorText,
        isNodeUrl: isNodeUrl,
        nodeForUrl: nodeForUrl,
        orderedNodes: orderedNodes,
        isNetworkUrl: isNetworkUrl,
        isProfileUrl: isProfileUrl,
        isMac: isMac,
        actionKey: actionKey,
        GLYPH_ROUTER: GLYPH_ROUTER,
        GLYPH_WIFI: GLYPH_WIFI,
        GLYPH_ETHERNET: GLYPH_ETHERNET,
        GLYPH_PAUSE: GLYPH_PAUSE,
        GLYPH_REBOOT: GLYPH_REBOOT,
        GLYPH_LED: GLYPH_LED,
        GLYPH_LOGIN: GLYPH_LOGIN,
        GLYPH_GUEST: GLYPH_GUEST,
        GLYPH_WARNING: GLYPH_WARNING
    }
}
