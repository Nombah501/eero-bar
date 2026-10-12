"""A fake HTTP transport shared by the eero-bar test modules.

Never touches the network: `FakeTransport` is passed as `eero_api.EeroClient`'s
`transport=`, so every test controls exactly what "the eero API" returns.

This module also installs the suite-wide network-forbidden guard (see
_install_network_guard below) as a side effect of being imported. It lives
here, not in tests/__init__.py, because a package's __init__.py is not
guaranteed to run under `python3 -m unittest discover -s tests` (verified:
it does not, when top-level-dir defaults to the start dir -- discovery then
imports test modules by their bare name, e.g. "test_eero_api", never
"tests.test_eero_api", so tests/__init__.py is simply never touched).
`fakes.py` is a plain module reached via `sys.path.insert(0, .../tests)` from
every test file, which always runs its top-level code exactly once per
process (Python caches it in sys.modules) regardless of how the *importing*
test file itself was discovered or named. Every eero-bar test file must
import this module -- including ones that don't otherwise need FakeTransport
-- specifically so this guard is always installed.
"""

import http.client
import io
import json
import sys
from pathlib import Path


def _install_network_guard():
    """Make any real network attempt fail loudly instead of silently
    succeeding (or hanging). AGENTS.md and every ticket for this ticket are
    explicit: no test may ever touch the network.

    Two layers, since eero_api.EeroClient's default transport uses a custom
    urllib opener rather than calling urllib.request.urlopen() directly:
      1. eero_api._default_transport itself, patched to raise -- this is the
         one function every EeroClient falls back to when a test forgets to
         pass transport=FakeTransport(...).
      2. http.client.HTTPConnection/HTTPSConnection.connect, patched to
         raise -- every real HTTP(S) request made through urllib, by any
         code path, ultimately calls one of these to open the socket. This
         is the belt-and-suspenders guard that holds even if some future
         code bypasses eero_api entirely.
    """
    root = Path(__file__).resolve().parents[1]
    lib_path = str(root / "lib")
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    import eero_api

    def _network_forbidden(*_args, **_kwargs):
        raise AssertionError(
            "A test attempted a real network connection. Every eero_api.EeroClient "
            "in tests must be constructed with transport=FakeTransport()."
        )

    eero_api._default_transport = _network_forbidden
    http.client.HTTPConnection.connect = _network_forbidden
    http.client.HTTPSConnection.connect = _network_forbidden
    return _network_forbidden


# Runs once per process, the moment any test file does `import fakes`.
NETWORK_FORBIDDEN = _install_network_guard()


class FakeStdin:
    """A stand-in for sys.stdin: exposes `.buffer` (a BytesIO), the same way
    the real text-mode stdin does, so eero_cli._read_secret's primary
    (bytes-decode-strict) code path is what tests actually exercise -- not
    just its io.StringIO fallback.
    """

    def __init__(self, data):
        raw = data.encode("utf-8") if isinstance(data, str) else data
        self.buffer = io.BytesIO(raw)

    def readline(self):
        # Fallback text path; _read_secret only uses this when .buffer is
        # absent, so real callers never reach it. Kept for completeness.
        return self.buffer.readline().decode("utf-8", errors="replace")


class FakeHeaders:
    """Minimal stand-in for the header object urllib hands back."""

    def __init__(self, items=None):
        self._items = list(items or [])

    def get_all(self, name):
        matches = [value for key, value in self._items if key.lower() == name.lower()]
        return matches or None

    def get(self, name, default=None):
        for key, value in self._items:
            if key.lower() == name.lower():
                return value
        return default


class FakeResponse:
    def __init__(self, status, payload=None, raw=None, headers=None, read_error=None):
        self.status = status
        if raw is not None:
            self._body = raw
        elif payload is not None:
            self._body = json.dumps(payload).encode("utf-8")
        else:
            self._body = b""
        self.headers = headers if headers is not None else FakeHeaders()
        self.closed = False
        self._read_error = read_error

    def read(self):
        if self._read_error is not None:
            raise self._read_error
        return self._body

    def close(self):
        self.closed = True


class FakeTransport:
    """Queue of responses/exceptions, consumed in order by each request."""

    def __init__(self):
        self.calls = []
        self._queue = []

    def queue_response(self, status, payload=None, raw=None, headers=None, read_error=None):
        self._queue.append(
            ("response", FakeResponse(status, payload=payload, raw=raw, headers=headers, read_error=read_error))
        )

    def queue_error(self, exc):
        self._queue.append(("error", exc))

    def __call__(self, request, timeout):
        self.calls.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "body": request.data,
                # urllib.request.Request.add_header() title-cases via
                # str.capitalize() (e.g. "User-Agent" -> "User-agent"), so
                # normalize to lowercase for case-insensitive assertions.
                "headers": {key.lower(): value for key, value in request.header_items()},
                "timeout": timeout,
            }
        )
        if not self._queue:
            raise AssertionError("FakeTransport queue exhausted: unexpected extra HTTP call")
        kind, value = self._queue.pop(0)
        if kind == "error":
            raise value
        return value

    @property
    def call_count(self):
        return len(self.calls)


def envelope(code, data=None, error=None):
    return {"meta": {"code": code, "server_time": "2026-01-01T00:00:00Z", "error": error}, "data": data}
