"""Pure normalization: raw eero API JSON -> the Snapshot shape QML consumes.

No I/O here. Every field is read defensively (`.get` with type checks) per
ADR-0001: an unexpected API shape yields null fields, never a crash. Unknown
or missing values are always emitted as `null`, never as an absent key
(docs/helper-protocol.md).
"""

import re


# [0-9] rather than \d: \d matches Unicode decimal digits by default.
_NETWORK_URL_ID = re.compile(r"^/2\.2/networks/([0-9]+)$")
_NODE_URL_ID = re.compile(r"^/2\.2/eeros/([0-9]+)$")
_PROFILE_URL_ID = re.compile(r"^/2\.2/networks/[0-9]+/profiles/([0-9]+)$")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# --- status-word heuristics -------------------------------------------------
#
# docs/eero-api.md does not enumerate the exact strings the API returns for
# `status` / `health.*.status`. Reverse-engineered clients report values like
# "green"/"red"/"yellow" (health) and "connected"/"disconnected" (device
# status), so we classify by keyword rather than exact match and fall back to
# unknown (None) for anything not clearly good or bad. This keeps an
# unrecognized future value from being silently misreported as healthy or
# broken.
_GOOD_WORDS = {"green", "good", "connected", "online", "up", "healthy", "true"}
_BAD_WORDS = {"red", "bad", "disconnected", "offline", "down", "unhealthy", "no_internet", "false"}
_WARN_WORDS = {"yellow", "orange", "amber", "degraded", "warning", "partial"}


def _get(source, *keys):
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_str(value):
    return value if isinstance(value, str) and value != "" else None


def _as_bool(value):
    return value if isinstance(value, bool) else None


def _as_number(value):
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _as_int(value):
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _first_nonempty(*values):
    for value in values:
        text = _as_str(value)
        if text is not None:
            return text
    return None


def _as_mac(value):
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    return lowered if _MAC_RE.fullmatch(lowered) else None


def _as_matched_url(value, pattern):
    """Return (url, id) if `value` matches `pattern` exactly, else (None, None).

    A snapshot containing a bad url must drop it to null rather than pass it
    through: QML sends these urls straight back to a reboot-node/led/
    pause-profile command, so a malformed one here would otherwise become a
    malformed argument there (docs/helper-protocol.md's regexes already
    guard that path, but this keeps a broken/hostile API response from ever
    reaching it in the first place).
    """
    url = _as_str(value)
    if url is None:
        return None, None
    # fullmatch, not match: with a "$"-anchored pattern, .match() is
    # satisfied by a string with a trailing "\n" (re's "$" matches just
    # before a final newline) even though that newline was never part of
    # the pattern. fullmatch requires the entire string to be consumed, so
    # "/2.2/eeros/456\n" is correctly rejected.
    match = pattern.fullmatch(url)
    if not match:
        return None, None
    return url, match.group(1)


def _health_word(value):
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered in _BAD_WORDS:
        return "bad"
    if lowered in _WARN_WORDS:
        return "warn"
    if lowered in _GOOD_WORDS:
        return "good"
    return None


def _network_status(network):
    """Map network status/health -> online|degraded|offline|unknown.

    Priority: a bad internet health (or, lacking that, a bad top-level
    status) means offline outright, since nothing else matters without
    internet. Otherwise a warn/bad mesh health, or a warn internet health,
    means degraded. A good internet health (or good top-level status with no
    contrary signal) means online. Anything we cannot classify is unknown --
    we never guess "online" from silence.
    """
    internet = _health_word(_get(network, "health", "internet", "status"))
    mesh = _health_word(_get(network, "health", "eero_network", "status"))
    top = _health_word(network.get("status") if isinstance(network, dict) else None)

    if internet == "bad" or (internet is None and top == "bad"):
        return "offline"
    if internet == "warn" or mesh in ("warn", "bad"):
        return "degraded"
    if internet == "good" or top == "good":
        return "online"
    return "unknown"


def _internet_online(network):
    state = _health_word(_get(network, "health", "internet", "status"))
    if state == "good":
        return True
    if state == "bad":
        return False
    return None


def _node_online(status):
    # A warn/yellow node is still reachable -- just on a weak mesh link --
    # so it counts as online, same as green/connected. Only a clearly bad
    # status counts as offline; a status word we don't recognize at all
    # reports unknown rather than guessing either way.
    state = _health_word(status)
    if state == "bad":
        return False
    if state in ("good", "warn"):
        return True
    return None


_PREMIUM_NEGATIVE_MARKERS = ("disabled", "inactive", "not_active", "not_enabled", "not_premium")


def _premium(value):
    # docs/eero-api.md only says "premium_status ... may be null"; the exact
    # shape (bool vs. a status string like "premium_enabled") is undocumented.
    # Accept a bool as-is; for a string, check negative markers FIRST -- e.g.
    # "inactive" contains the substring "active", so checking positive words
    # first would misread a negative status as premium enabled.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if not lowered:
            return None
        if lowered in ("none", "false") or any(marker in lowered for marker in _PREMIUM_NEGATIVE_MARKERS):
            return False
        if lowered == "true" or "enabled" in lowered or "active" in lowered:
            return True
        return None
    return None


def _network_id(network_url):
    _url, network_id = _as_matched_url(network_url, _NETWORK_URL_ID)
    return network_id


def _build_network(raw, network_url):
    network = raw if isinstance(raw, dict) else {}
    down = _get(network, "speed", "down", "value")
    up = _get(network, "speed", "up", "value")
    units = _first_nonempty(_get(network, "speed", "down", "units"), _get(network, "speed", "up", "units"))
    return {
        "id": _network_id(network_url),
        "url": _as_str(network_url),
        "name": _as_str(network.get("name")),
        "status": _network_status(network),
        "internetOnline": _internet_online(network),
        "guestEnabled": _as_bool(_get(network, "guest_network", "enabled")),
        "guestName": _as_str(_get(network, "guest_network", "name")),
        "speed": {
            "down": _as_number(down),
            "up": _as_number(up),
            "units": units,
            "date": _as_str(_get(network, "speed", "date")),
        },
        "premium": _premium(network.get("premium_status")),
    }


def _build_node(raw):
    node = raw if isinstance(raw, dict) else {}
    url, node_id = _as_matched_url(node.get("url"), _NODE_URL_ID)
    name = _first_nonempty(node.get("location"), node.get("model"), node.get("serial"))
    gateway = _as_bool(node.get("gateway"))
    return {
        "id": node_id,
        "url": url,
        "name": name,
        "model": _as_str(node.get("model")),
        "gateway": gateway,
        "online": _node_online(node.get("status")),
        "clients": _as_int(node.get("connected_clients_count")),
        "ledOn": _as_bool(node.get("led_on")),
        "meshBars": _as_int(node.get("mesh_quality_bars")),
        "ip": _as_str(node.get("ip_address")),
        "firmware": _as_str(node.get("os_version")),
        "updateAvailable": _as_bool(node.get("update_available")),
        "wired": _as_bool(node.get("wired")),
        "_sortGateway": gateway is True,
        "_sortName": (name or "").lower(),
    }


def _build_client(raw):
    client = raw if isinstance(raw, dict) else {}
    raw_mac = client.get("mac")
    mac = _as_mac(raw_mac)
    # The display-name fallback uses the raw mac text even if it fails
    # strict validation (better a slightly-odd display string than an empty
    # name); the "mac" field itself is null unless it is genuinely usable
    # for a pause-client action.
    name = _first_nonempty(client.get("nickname"), client.get("hostname"), raw_mac)
    connected = _as_bool(client.get("connected"))
    return {
        "mac": mac,
        "name": name,
        "ip": _as_str(client.get("ip")),
        "connected": connected,
        "wireless": _as_bool(client.get("wireless")),
        "paused": _as_bool(client.get("paused")),
        "guest": _as_bool(client.get("is_guest")),
        "nodeName": _as_str(_get(client, "source", "location")),
        "profileName": _as_str(_get(client, "profile", "name")),
        "_sortConnected": connected is True,
        "_sortName": (name or "").lower(),
    }


def _build_profile(raw):
    profile = raw if isinstance(raw, dict) else {}
    url, profile_id = _as_matched_url(profile.get("url"), _PROFILE_URL_ID)
    devices = profile.get("devices")
    name = _as_str(profile.get("name"))
    return {
        "id": profile_id,
        "url": url,
        "name": name,
        "paused": _as_bool(profile.get("paused")),
        "clientCount": len(devices) if isinstance(devices, list) else None,
        "_sortName": (name or "").lower(),
    }


def _drop_sort_keys(item):
    return {key: value for key, value in item.items() if not key.startswith("_sort")}


def build_snapshot(*, network_raw, nodes_raw, clients_raw, profiles_raw, network_url, fetched_at):
    nodes = [_build_node(item) for item in (nodes_raw if isinstance(nodes_raw, list) else [])]
    clients = [_build_client(item) for item in (clients_raw if isinstance(clients_raw, list) else [])]
    profiles = [_build_profile(item) for item in (profiles_raw if isinstance(profiles_raw, list) else [])]

    # Nodes: gateway first, then name (case-insensitive).
    nodes.sort(key=lambda item: (not item["_sortGateway"], item["_sortName"]))
    # Clients: connected first, then name (case-insensitive).
    clients.sort(key=lambda item: (not item["_sortConnected"], item["_sortName"]))
    # Profiles: by name.
    profiles.sort(key=lambda item: item["_sortName"])

    return {
        "ok": True,
        "type": "snapshot",
        "fetchedAt": fetched_at,
        "network": _build_network(network_raw, network_url),
        "nodes": [_drop_sort_keys(item) for item in nodes],
        "clients": [_drop_sort_keys(item) for item in clients],
        "profiles": [_drop_sort_keys(item) for item in profiles],
    }
