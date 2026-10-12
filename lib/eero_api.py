"""HTTP client for the unofficial eero cloud API (see docs/eero-api.md).

Only this module talks HTTP. It knows nothing about argv, stdin, or the
session file on disk -- callers pass in a token and get one back via
`on_token_refreshed`. The transport is injectable so tests never touch the
network: pass any callable `transport(request, timeout) -> response`, where
`response` has `.status` (or `.getcode()`), `.headers` (supports `.get_all`
or `.get`) and `.read() -> bytes`. `urllib.error.HTTPError` satisfies this
shape already, so a transport may simply let it propagate.

Security notes (see verifier fix round, ticket 01):
- Every outbound path must start with "/", and the fully-built URL's
  scheme+host+port must match `origin` exactly, or the request is refused
  before it is ever sent. This stops a hostile "url" field pulled out of an
  API response (e.g. `.evil.host/2.2/networks/1`, which naive string
  concatenation would turn into a request to
  `https://api-user.e2ro.com.evil.host/...`, cookie and all) from ever
  leaving this process.
- The default transport uses an opener that refuses every HTTP redirect
  (3xx), since the stdlib's default redirect handling re-sends the Cookie
  header to wherever Location points, including a different host.
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request


ORIGIN = "https://api-user.e2ro.com"
DEFAULT_TIMEOUT = 15
USER_AGENT = "eero-bar/0.1"

# docs/eero-api.md: "On meta.code == 401 with meta.error in (...): call
# refresh once, retry once; if still 401 -> re-login required."
REFRESHABLE_ERRORS = ("error.session.refresh", "error.session.invalid")


class ApiError(Exception):
    """A request failed. `code` is one of the protocol's error codes:
    auth_required, login_rejected, rate_limited, network, api.

    `status`, when set, is the underlying HTTP status or eero meta.code that
    produced an "api" error -- callers (eero_cli._with_network_retry) use it
    to notice a stale cached networkUrl (403/404) without eero_api needing
    to know anything about the session store.
    """

    def __init__(self, code, message, *, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect (see module docstring)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # `fp` is the 3xx response's body stream; close it before aborting so
        # the underlying connection isn't leaked just because we're refusing
        # to follow it.
        if fp is not None:
            try:
                fp.close()
            except OSError:
                pass
        raise ApiError("api", f"eero API returned an unexpected redirect (HTTP {code}).")


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _default_transport(request, timeout):
    return _OPENER.open(request, timeout=timeout)


def _reject_json_constant(name):
    # json.loads calls this for NaN/Infinity/-Infinity instead of returning
    # a float; raising turns them into an "api" error rather than a value
    # that could later reach json.dumps(allow_nan=False) and crash emission.
    raise ValueError(f"eero API response contained a non-finite number ({name}).")


def _extract_list(data, label):
    # List endpoints (eeros/devices/profiles) observed in reverse-engineered
    # clients wrap their array as data.data (matching the documented
    # networks.data[] shape for /2.2/account); also accept a bare list in
    # case a given endpoint does not double-wrap.
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    raise ApiError("api", f"eero API returned an unexpected shape for {label}.")


def _extract_cookie_token(headers):
    if headers is None:
        return None
    values = []
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all("Set-Cookie") or []
    else:
        single = headers.get("Set-Cookie") if hasattr(headers, "get") else None
        if single:
            values = [single]
    for value in values:
        for part in value.split(";"):
            part = part.strip()
            if part.startswith("s="):
                token = part[2:].strip()
                if token:
                    return token
    return None


class EeroClient:
    def __init__(self, token=None, transport=None, timeout=DEFAULT_TIMEOUT, origin=ORIGIN, on_token_refreshed=None):
        self.token = token
        self._transport = transport or _default_transport
        self.timeout = timeout
        self.origin = origin
        self.on_token_refreshed = on_token_refreshed

    # -- transport plumbing --

    def _send(self, method, url, body):
        if not isinstance(url, str) or not url.startswith("/"):
            raise ApiError("api", "eero API request used an invalid resource path.")
        full_url = self.origin + url
        parsed = urllib.parse.urlsplit(full_url)
        expected = urllib.parse.urlsplit(self.origin)
        if (parsed.scheme, parsed.hostname, parsed.port) != (expected.scheme, expected.hostname, expected.port):
            raise ApiError("api", "eero API request would leave the expected origin.")

        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Cookie"] = f"s={self.token}"
        request = urllib.request.Request(full_url, data=data, headers=headers, method=method)

        try:
            response = self._transport(request, self.timeout)
        except urllib.error.HTTPError as error:
            # HTTPError already exposes .code/.headers/.read(), so it is used
            # directly as the response for a non-2xx HTTP status.
            response = error
        except OSError as error:
            # Covers urllib.error.URLError, socket.timeout/TimeoutError,
            # ssl.SSLError and plain connection failures -- all OSError
            # subclasses.
            raise ApiError("network", f"Network error contacting eero API: {error}") from error

        try:
            status = response.status
        except AttributeError:
            status = response.getcode()
        try:
            raw = response.read()
        except (OSError, http.client.HTTPException) as error:
            # Covers a dropped connection, a read timeout, and malformed
            # HTTP framing (IncompleteRead, BadStatusLine, ...) while the
            # body is being read -- all "the network misbehaved", not an
            # internal bug.
            raise ApiError("network", f"Network error reading eero API response: {error}") from error
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return status, raw, getattr(response, "headers", None)

    def _call(self, method, url, body):
        status, raw, headers = self._send(method, url, body)
        if status == 429:
            raise ApiError("rate_limited", "eero API rate limit exceeded.")
        try:
            text = raw.decode("utf-8") if raw else ""
            envelope = json.loads(text, parse_constant=_reject_json_constant) if text else None
        except (UnicodeDecodeError, ValueError):
            envelope = None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("meta"), dict):
            if status == 401:
                # An empty or non-envelope body on a 401 is still an auth
                # failure, just without eero's usual error string.
                # Synthesizing a refreshable error lets the normal
                # refresh-once-then-retry flow in request() handle it.
                return 401, "error.session.invalid", None, headers
            raise ApiError("api", "eero API returned an unexpected response shape.", status=status)
        meta = envelope["meta"]
        meta_code = meta.get("code")
        if meta_code == 429:
            raise ApiError("rate_limited", "eero API rate limit exceeded.")
        return meta_code, meta.get("error"), envelope.get("data"), headers

    def _refresh(self):
        meta_code, _meta_error, data, _headers = self._call("POST", "/2.2/login/refresh", None)
        if not (isinstance(meta_code, int) and 200 <= meta_code < 300):
            raise ApiError("auth_required", "eero session refresh failed; please log in again.")
        new_token = data.get("user_token") if isinstance(data, dict) else None
        if not new_token:
            raise ApiError("auth_required", "eero session refresh did not return a new token.")
        self.token = new_token
        if self.on_token_refreshed:
            self.on_token_refreshed(new_token)

    def request(self, method, url, body=None, *, allow_refresh=True):
        meta_code, meta_error, data, _headers = self._call(method, url, body)
        if isinstance(meta_code, int) and 200 <= meta_code < 300:
            return data
        if meta_code == 401 and meta_error in REFRESHABLE_ERRORS:
            if not allow_refresh:
                raise ApiError("auth_required", "eero session expired; please log in again.")
            self._refresh()
            return self.request(method, url, body, allow_refresh=False)
        if meta_code == 401:
            raise ApiError("auth_required", "eero session is not authenticated.")
        raise ApiError("api", f"eero API error {meta_code}: {meta_error}", status=meta_code)

    # -- endpoints (docs/eero-api.md) --

    def login(self, identifier):
        # Calls _call() directly, never request(): /2.2/login must never
        # trigger a session-refresh attempt (there is no session yet), and
        # a 4xx here means the identifier was rejected, not that auth
        # expired.
        meta_code, _meta_error, data, _headers = self._call("POST", "/2.2/login", {"login": identifier})
        if not (isinstance(meta_code, int) and 200 <= meta_code < 300):
            if isinstance(meta_code, int) and 400 <= meta_code < 500:
                # Fixed human message: never echo the server's meta.error
                # text, which is untrusted and not meant for a user-facing
                # string.
                raise ApiError("login_rejected", "eero rejected the identifier.", status=meta_code)
            raise ApiError("api", "eero API returned an unexpected response to login.", status=meta_code)
        user_token = data.get("user_token") if isinstance(data, dict) else None
        if not user_token:
            raise ApiError("login_rejected", "eero did not return a login token.")
        self.token = user_token
        return user_token

    def verify(self, pending_user_token, code):
        # Same reasoning as login(): no refresh attempt for /2.2/login/verify.
        self.token = pending_user_token
        meta_code, _meta_error, _data, headers = self._call("POST", "/2.2/login/verify", {"code": code})
        if not (isinstance(meta_code, int) and 200 <= meta_code < 300):
            if isinstance(meta_code, int) and 400 <= meta_code < 500:
                raise ApiError("login_rejected", "eero rejected the verification code.", status=meta_code)
            raise ApiError("api", "eero API returned an unexpected response to verify.", status=meta_code)
        # "prefer Set-Cookie s= if present" (docs/eero-api.md); otherwise the
        # pending user_token used to authenticate this request stays valid.
        cookie_token = _extract_cookie_token(headers)
        self.token = cookie_token or pending_user_token
        return self.token

    def account(self):
        data = self.request("GET", "/2.2/account")
        if not isinstance(data, dict):
            raise ApiError("api", "eero API returned an unexpected shape for the account.")
        return data

    def network(self, network_url):
        data = self.request("GET", network_url)
        if not isinstance(data, dict):
            raise ApiError("api", "eero API returned an unexpected shape for the network.")
        return data

    def nodes(self, network_url):
        return _extract_list(self.request("GET", network_url + "/eeros"), "nodes")

    def devices(self, network_url):
        return _extract_list(self.request("GET", network_url + "/devices"), "devices")

    def profiles(self, network_url):
        return _extract_list(self.request("GET", network_url + "/profiles"), "profiles")

    def reboot_node(self, node_url):
        self.request("POST", node_url + "/reboot")

    def reboot_network(self, network_url):
        self.request("POST", network_url + "/reboot")

    def set_guest(self, network_url, enabled):
        self.request("PUT", network_url + "/guestnetwork", {"enabled": enabled})

    def set_led(self, node_url, on):
        self.request("PUT", node_url, {"led_on": on})

    def pause_client(self, network_id, mac, paused):
        # Exception to the "origin + resource url" rule: device writes must
        # use /2.3, not /2.2 (docs/eero-api.md).
        self.request("PUT", f"/2.3/networks/{network_id}/devices/{mac}", {"paused": paused})

    def pause_profile(self, profile_url, paused):
        self.request("PUT", profile_url, {"paused": paused})

    def speedtest(self, network_url):
        self.request("POST", network_url + "/speedtest")
