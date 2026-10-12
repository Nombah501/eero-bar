"""Command dispatch for bin/eero-helper (docs/helper-protocol.md is the contract).

`main()` never lets an exception escape as a Python traceback: every command
either returns a success payload or raises `_UsageError` / `eero_api.ApiError`
/ `eero_session.SessionError`, and `main()` emits the terminal JSON object
with the matching exit code (0 ok, 1 error, 2 usage). `verify` alone also
emits safe progress metadata before private commit approval. Nothing writes token,
code, or (unmasked) identifier to stdout or stderr.
"""

import io
import json
import os
import re
import select
import time
import signal
from datetime import datetime, timezone

import eero_api
import eero_session
import eero_snapshot
import eero_amazon


MAX_SECRET_LENGTH = 256
# Worst case UTF-8 is 4 bytes/char; +2 for slack around the newline. Bounds
# memory use while still accepting a legitimate 256-character secret.
_MAX_SECRET_BYTES = MAX_SECRET_LENGTH * 4 + 2

# [0-9] rather than \d: \d matches Unicode decimal digits (e.g. Arabic-indic
# digits) by default, which would let a non-ASCII "digit" string slip past
# what looks like a numeric-only pattern.
_NODE_URL_RE = re.compile(r"^/2\.2/eeros/[0-9]+$")
_PROFILE_URL_RE = re.compile(r"^/2\.2/networks/[0-9]+/profiles/[0-9]+$")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_NETWORK_URL_RE = re.compile(r"^/2\.2/networks/([0-9]+)$")

# A network-scoped request failing with one of these almost certainly means
# the Session's cached networkUrl is stale (the network was deleted and
# re-created, etc.), not that the request itself was wrong.
_STALE_NETWORK_STATUSES = (403, 404)


class _UsageError(Exception):
    pass


# -- validation (docs/helper-protocol.md); always runs before any HTTP call --


def _read_secret(stdin):
    # Read raw bytes from stdin.buffer (real sys.stdin always has one) and
    # decode strictly, so invalid UTF-8 is a clean "usage" error/exit 2
    # instead of an uncaught UnicodeDecodeError falling into the generic
    # internal-error handler. readline(size) stops at the first newline, at
    # EOF, or at the byte bound, whichever comes first -- a secret with no
    # trailing newline (stdin closed right after) still reads correctly.
    buffer = getattr(stdin, "buffer", None)
    if buffer is not None:
        raw = buffer.readline(_MAX_SECRET_BYTES)
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _UsageError("Secret on stdin must be valid UTF-8.") from error
    else:
        line = stdin.readline()
    secret = line.strip()
    if not secret or len(secret) > MAX_SECRET_LENGTH:
        raise _UsageError("Expected a single secret line (1-256 characters) on stdin.")
    return secret


def _validate_node_url(value):
    if not isinstance(value, str) or not _NODE_URL_RE.fullmatch(value):
        raise _UsageError("nodeUrl must match /2.2/eeros/<id>.")
    return value


def _validate_profile_url(value):
    if not isinstance(value, str) or not _PROFILE_URL_RE.fullmatch(value):
        raise _UsageError("profileUrl must match /2.2/networks/<id>/profiles/<id>.")
    return value


def _validate_mac(value):
    if not isinstance(value, str):
        raise _UsageError("mac must be a colon-separated MAC address.")
    lowered = value.lower()
    if not _MAC_RE.fullmatch(lowered):
        raise _UsageError("mac must be a colon-separated MAC address.")
    return lowered


def _validate_on_off(value):
    if value not in ("on", "off"):
        raise _UsageError("Expected 'on' or 'off'.")
    return value == "on"


def _mask_identifier(identifier):
    # docs/helper-protocol.md gives two examples ("n***@gmail.com",
    # "+7******12") but no formal algorithm. Email: keep the first character
    # of the local part and the whole domain. Phone (leading "+"): keep the
    # "+" plus country digit and the last two digits, mask the middle with a
    # fixed run of six asterisks (matching the example's literal width, not
    # scaled to the input length). Anything else falls back to a short,
    # clearly-truncated prefix so we never echo a full unmasked secret.
    value = identifier.strip()
    if "@" in value:
        local, _, domain = value.partition("@")
        first = local[0] if local else ""
        return f"{first}***@{domain}"
    if value.startswith("+") and len(value) >= 3:
        return f"{value[:2]}******{value[-2:]}"
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[:1]}***"


# -- session / client wiring --


def _require_token(session):
    token = session.get("token")
    if not token:
        raise eero_api.ApiError("auth_required", "Not logged in.")
    return token


def _make_client(session, environ, transport):
    def _on_refresh(new_token):
        eero_session.update_session({"token": new_token}, environ)

    return eero_api.EeroClient(token=session["token"], transport=transport, on_token_refreshed=_on_refresh)


def _first_network_url(account_data):
    networks = account_data.get("networks") if isinstance(account_data, dict) else None
    entries = networks.get("data") if isinstance(networks, dict) else None
    if not isinstance(entries, list) or not entries:
        raise eero_api.ApiError("api", "eero account has no networks.")
    first = entries[0]
    url = first.get("url") if isinstance(first, dict) else None
    # M1a: an absolute or off-origin "url" here (e.g. "https://evil.host/...",
    # or ".evil.host/2.2/networks/1", which naive concatenation would turn
    # into a request to a subdomain of evil.host) must never be accepted as
    # a network path. Anything not exactly /2.2/networks/<digits> is bad
    # data from the API, not a usable network -- fail closed.
    if not isinstance(url, str) or not _NETWORK_URL_RE.fullmatch(url):
        raise eero_api.ApiError("api", "eero account network has an unexpected url.")
    return url


def _resolve_network_url(client, session, environ, *, force_refresh=False):
    cached = session.get("networkUrl")
    # An invalid cached value (corrupt session, or a pre-fix session written
    # before this validation existed) is silently treated as absent and
    # re-resolved, rather than trusted or surfaced as an error.
    if not force_refresh and isinstance(cached, str) and _NETWORK_URL_RE.fullmatch(cached):
        return cached
    url = _first_network_url(client.account())
    eero_session.update_session({"networkUrl": url}, environ)
    session["networkUrl"] = url
    return url


def _fetch_with_network_retry(client, session, environ, action):
    """Read-only use only (status): call `action(network_url)`. A 403/404
    most likely means the cached networkUrl is stale (docs/helper-
    protocol.md doesn't cover this, but a silently deleted/re-created
    network is a real-world case); clear it, re-resolve via /account, and
    retry the action exactly once more. Safe here because a retried GET
    just shows fresher data -- see _mutate_with_network_check for why a
    mutating action must never do this.
    """
    network_url = _resolve_network_url(client, session, environ)
    try:
        return action(network_url)
    except eero_api.ApiError as error:
        if error.status not in _STALE_NETWORK_STATUSES:
            raise
        eero_session.update_session({"networkUrl": None}, environ)
        session["networkUrl"] = None
        network_url = _resolve_network_url(client, session, environ, force_refresh=True)
        return action(network_url)


def _mutate_with_network_check(client, session, environ, action):
    """Mutating actions only (reboot-network, guest, pause-client,
    speedtest): call `action(network_url)` exactly once. A 403/404 means the
    cached networkUrl is stale -- clear it so the *next* command re-resolves
    -- but never silently retry the mutation itself: the freshly-resolved
    network could be a different one than what the user saw/confirmed
    (e.g. deleted network 1, re-created as network 2), and rebooting/pausing
    on it without the user's knowledge would be a real, if rare, hazard.
    """
    network_url = _resolve_network_url(client, session, environ)
    try:
        return action(network_url)
    except eero_api.ApiError as error:
        if error.status not in _STALE_NETWORK_STATUSES:
            raise
        eero_session.update_session({"networkUrl": None}, environ)
        session["networkUrl"] = None
        raise eero_api.ApiError("api", "Network changed; refresh and try again.") from error


def _network_id_from_url(network_url):
    # fullmatch, not match: see eero_snapshot._as_matched_url for why "$"
    # alone tolerates a trailing newline that match() would let through.
    match = _NETWORK_URL_RE.fullmatch(network_url)
    if not match:
        raise eero_api.ApiError("api", "eero network url has an unexpected shape.")
    return match.group(1)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- commands: each returns the success payload dict, or raises --


def _cmd_status(args, stdin, environ, transport):
    if args:
        raise _UsageError("status takes no arguments.")
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)

    def _fetch(network_url):
        return (
            client.network(network_url),
            client.nodes(network_url),
            client.devices(network_url),
            client.profiles(network_url),
            network_url,
        )

    network_raw, nodes_raw, clients_raw, profiles_raw, network_url = _fetch_with_network_retry(
        client, session, environ, _fetch
    )
    return eero_snapshot.build_snapshot(
        network_raw=network_raw,
        nodes_raw=nodes_raw,
        clients_raw=clients_raw,
        profiles_raw=profiles_raw,
        network_url=network_url,
        fetched_at=_now_iso(),
    )


def _cmd_login(args, stdin, environ, transport):
    if args:
        raise _UsageError("login takes no arguments.")
    identifier = _read_secret(stdin)
    # A login started while already signed in (the popup's re-login) must
    # not destroy the live session before verify succeeds: keep the current
    # token/networkUrl and overwrite both only when verify lands (spec:
    # re-login contract). If verify never happens, cancelLogin() restores
    # the ready state on this preserved token.
    session = eero_session.load_session(environ)
    client = eero_api.EeroClient(transport=transport)
    client.login(identifier)
    eero_session.save_session(
        {
            "pendingUserToken": client.token,
            "token": session.get("token"),
            "networkUrl": session.get("networkUrl"),
        },
        environ,
    )
    return {"ok": True, "type": "code_sent", "identifier": _mask_identifier(identifier)}


def _cmd_verify(args, stdin, environ, transport, stdout):
    if args:
        raise _UsageError("verify takes no arguments.")
    code = _read_secret(stdin)
    session = eero_session.load_session(environ)
    pending = session.get("pendingUserToken")
    if not pending:
        raise eero_api.ApiError("auth_required", "No login in progress; call login first.")
    client = eero_api.EeroClient(transport=transport)
    token = client.verify(pending, code)
    # A successful HTTP reply is not permission to replace the live Session.
    # The owning Service approves only while this attempt is still current.
    _emit(stdout, {"ok": True, "type": "verification_ready"})
    stream = getattr(stdin, "buffer", stdin)
    try:
        fd = stream.fileno()
    except (AttributeError, io.UnsupportedOperation):
        fd = None  # In-memory input used by the public command tests.
    if fd is None:
        approval = stream.readline(16)
    else:
        deadline = time.monotonic() + 30
        approval = b""
        while len(approval) < 7 and not approval.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise eero_api.ApiError("login_cancelled", "Login approval expired; previous Session preserved.")
            # Read only ready bytes, never a buffered/blocking readline. The
            # code was consumed before progress; approval arrives afterward.
            chunk = os.read(fd, 1)
            if time.monotonic() >= deadline:
                raise eero_api.ApiError("login_cancelled", "Login approval expired; previous Session preserved.")
            if not chunk:
                break
            approval += chunk
    if approval not in (b"commit\n", "commit\n"):
        raise eero_api.ApiError("login_cancelled", "Login cancelled; previous Session preserved.")
    eero_session.save_session(
        {"token": token, "pendingUserToken": None, "networkUrl": session.get("networkUrl")}, environ
    )
    return {"ok": True, "type": "logged_in"}


def _cmd_logout(args, stdin, environ, transport):
    if args:
        raise _UsageError("logout takes no arguments.")
    eero_session.delete_session(environ)
    return {"ok": True, "type": "logged_out"}


def _cmd_reboot_node(args, stdin, environ, transport):
    if len(args) != 1:
        raise _UsageError("reboot-node requires exactly one argument: nodeUrl.")
    node_url = _validate_node_url(args[0])
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)
    client.reboot_node(node_url)
    return {"ok": True, "type": "done", "action": "reboot-node"}


def _cmd_reboot_network(args, stdin, environ, transport):
    if args:
        raise _UsageError("reboot-network takes no arguments.")
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)
    _mutate_with_network_check(client, session, environ, client.reboot_network)
    return {"ok": True, "type": "done", "action": "reboot-network"}


def _cmd_guest(args, stdin, environ, transport):
    if len(args) != 1:
        raise _UsageError("guest requires exactly one argument: on|off.")
    enabled = _validate_on_off(args[0])
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)
    _mutate_with_network_check(client, session, environ, lambda network_url: client.set_guest(network_url, enabled))
    return {"ok": True, "type": "done", "action": "guest"}


def _cmd_led(args, stdin, environ, transport):
    if len(args) != 2:
        raise _UsageError("led requires exactly two arguments: nodeUrl on|off.")
    node_url = _validate_node_url(args[0])
    on = _validate_on_off(args[1])
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)
    client.set_led(node_url, on)
    return {"ok": True, "type": "done", "action": "led"}


def _cmd_pause_client(args, stdin, environ, transport):
    if len(args) != 2:
        raise _UsageError("pause-client requires exactly two arguments: mac on|off.")
    mac = _validate_mac(args[0])
    paused = _validate_on_off(args[1])
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)

    def _pause(network_url):
        network_id = _network_id_from_url(network_url)
        client.pause_client(network_id, mac, paused)

    _mutate_with_network_check(client, session, environ, _pause)
    return {"ok": True, "type": "done", "action": "pause-client"}


def _cmd_pause_profile(args, stdin, environ, transport):
    if len(args) != 2:
        raise _UsageError("pause-profile requires exactly two arguments: profileUrl on|off.")
    profile_url = _validate_profile_url(args[0])
    paused = _validate_on_off(args[1])
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)
    client.pause_profile(profile_url, paused)
    return {"ok": True, "type": "done", "action": "pause-profile"}


def _cmd_speedtest(args, stdin, environ, transport):
    if args:
        raise _UsageError("speedtest takes no arguments.")
    session = eero_session.load_session(environ)
    _require_token(session)
    client = _make_client(session, environ, transport)
    _mutate_with_network_check(client, session, environ, client.speedtest)
    return {"ok": True, "type": "done", "action": "speedtest"}


def _cmd_amazon(args, stdin, environ, transport, stdout):
    if args:
        raise _UsageError("amazon-login takes no arguments.")
    previous = signal.signal(signal.SIGTERM, lambda *_: eero_amazon.fail())
    try:
        return eero_amazon.run(stdin, environ, transport, lambda payload: _emit(stdout, payload))
    finally:
        signal.signal(signal.SIGTERM, previous)


_COMMANDS = {
    "status": _cmd_status,
    "login": _cmd_login,
    "verify": _cmd_verify,
    "amazon-login": _cmd_amazon,
    "logout": _cmd_logout,
    "reboot-node": _cmd_reboot_node,
    "reboot-network": _cmd_reboot_network,
    "guest": _cmd_guest,
    "led": _cmd_led,
    "pause-client": _cmd_pause_client,
    "pause-profile": _cmd_pause_profile,
    "speedtest": _cmd_speedtest,
}


def _emit(stdout, payload):
    # allow_nan=False: a NaN/Infinity float should never reach here (eero_api
    # rejects them at JSON-parse time, per M2/MINOR-2), but this is the
    # last-resort guard that turns a violation into a clean caught exception
    # (-> the "api" catch-all below) instead of ever emitting invalid JSON.
    stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False))
    stdout.write("\n")
    stdout.flush()


def _emit_error(stdout, code, message):
    _emit(stdout, {"ok": False, "type": "error", "code": code, "message": message})


def main(argv, stdin, stdout, stderr, transport=None, environ=None):
    """Run one command. `argv` excludes the program name (e.g. ["status"])."""
    environ = os.environ if environ is None else environ
    try:
        if not argv:
            raise _UsageError("Expected a command.")
        command, args = argv[0], argv[1:]
        handler = _COMMANDS.get(command)
        if handler is None:
            raise _UsageError(f"Unknown command: {command}")
        payload = handler(args, stdin, environ, transport, stdout) if command in ("verify", "amazon-login") else handler(args, stdin, environ, transport)
        _emit(stdout, payload)
        return 0
    except _UsageError as error:
        _emit_error(stdout, "usage", str(error))
        return 2
    except eero_api.ApiError as error:
        _emit_error(stdout, error.code, error.message)
        return 1
    except eero_session.SessionError as error:
        _emit_error(stdout, "state", str(error))
        return 1
    except Exception:
        # ADR-0001: an unexpected shape/failure yields error "api", never a
        # crash -- this is the last-resort net for anything not already
        # classified above (including a payload that fails allow_nan=False).
        # It never includes exception text, so a stray secret cannot leak
        # through a traceback.
        _emit_error(stdout, "api", "eero-helper encountered an unexpected internal error.")
        return 1
