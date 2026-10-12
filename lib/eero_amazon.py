"""Bounded default-US MAP acquisition. Secrets never leave Helper memory/pipe."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
import select
import sys
import secrets
import shutil
import signal
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

import eero_api
import eero_session
import eero_amazon_return

CALLBACK = "https://www.amazon.com/ap/maplanding"
BROWSER_SECONDS = 600
MAX_MESSAGE = 1024 * 1024
_EXCHANGE_SECONDS = 15
_CONTROL_INTERVAL = 0.1


@contextmanager
def defer_cancellation():
    """Coalesce graceful SIGTERM until mandatory cleanup has finished."""
    pending = False
    def defer(*unused):
        nonlocal pending
        pending = True
    previous = signal.signal(signal.SIGTERM, defer)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
        if pending:
            fail()


@contextmanager
def exchange_deadline(attempt, control):
    """Interrupt blocking stdlib I/O in the one-shot Linux main thread."""
    deadline = min(attempt.deadline, attempt.clock() + _EXCHANGE_SECONDS)
    def check(*unused):
        attempt.check()
        if control.poll() is not None or control.buffer:
            fail()
        if attempt.clock() >= deadline:
            fail("network")
        if unused:
            signal.setitimer(signal.ITIMER_REAL, min(_CONTROL_INTERVAL, max(0.000001, deadline - attempt.clock())))
    check()
    previous = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, check)
    try:
        signal.setitimer(signal.ITIMER_REAL, min(_CONTROL_INTERVAL, max(0.000001, deadline - attempt.clock())))
        yield
        check()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, timer[0] - (time.monotonic() - started)), timer[1])


def fail(code="login_cancelled"):
    raise eero_api.ApiError(code, "Amazon sign-in did not complete; previous Session preserved.")


class Attempt:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + BROWSER_SECONDS
        self.verifier = secrets.token_urlsafe(32)
        self.client = (uuid.uuid4().hex + "#A2W01JEFAJFTXI").encode().hex()
        self.used = False
        self.cancelled = False

    def check(self):
        if self.cancelled or self.clock() >= self.deadline:
            fail()

    def portal(self):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        return "https://www.amazon.com/ap/signin?" + urllib.parse.urlencode({
            "openid.ns": "http://specs.openid.net/auth/2.0", "openid.mode": "checkid_setup",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.return_to": CALLBACK, "openid.assoc_handle": "amzn_eero_mobile_android_us",
            "openid.oa2.response_type": "code", "openid.oa2.scope": "device_auth_access",
            "openid.oa2.client_id": "device:" + self.client,
            "openid.oa2.code_challenge": challenge, "openid.oa2.code_challenge_method": "S256",
            "pageId": "eero_mobile_accounts", "language": "en_US", "accountStatusPolicy": "DEFAULT",
            "openid.pape.max_auth_age": "0"})

    def receive(self, value):
        self.check()
        if self.used:
            fail()
        try:
            code = eero_amazon_return.callback_code(value)
        except (ValueError, TypeError):
            fail("login_rejected")
        self.used = True
        return code


class Control:
    def __init__(self, stdin, clock=time.monotonic):
        self.fd = getattr(stdin, "buffer", stdin).fileno()
        self.clock = clock
        self.buffer = b""

    def poll(self):
        if not select.select([self.fd], [], [], 0)[0]:
            return None
        chunk = os.read(self.fd, 16)
        if not chunk:
            fail()
        self.buffer += chunk
        if len(self.buffer) > 16:
            fail()
        if b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            return line
        return None

    def consent(self):
        if self.buffer:
            fail()
        deadline = self.clock() + 30
        while self.clock() < deadline:
            select.select([self.fd], [], [], max(0, deadline - self.clock()))
            line = self.poll()
            if self.clock() >= deadline:
                fail()
            if line is not None:
                if line != b"commit" or self.buffer:
                    fail()
                return
        fail()


class Browser:
    """Own the return receiver, never the user's browser or its profile."""
    def __init__(self, environ):
        self.environ = environ
        self.process = None
        self.launcher = None
        self.buffer = b""

    def __enter__(self):
        return self

    def next(self, attempt, control, deadline=None):
        while True:
            attempt.check()
            if control.poll() is not None or control.buffer:
                fail()
            if deadline is not None and attempt.clock() >= deadline:
                fail("browser_unavailable")
            if self.launcher is not None and self.launcher.poll() not in (None, 0):
                fail("browser_unavailable")
            if b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
                except ValueError:
                    fail("browser_unavailable")
            if select.select([self.process.stdout], [], [], 0.1)[0]:
                chunk = os.read(self.process.stdout.fileno(), 4096)
                if not chunk:
                    attempt.check()
                    fail("browser_unavailable")
                self.buffer += chunk
                if len(self.buffer) > 65536:
                    fail("browser_unavailable")

    def acquire(self, attempt, control):
        try:
            self.process = subprocess.Popen([sys.executable, "-B", os.path.join(os.path.dirname(__file__), "eero_amazon_return.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=self.environ, start_new_session=True)
            self.process.stdin.write(json.dumps({"portal": attempt.portal(), "deadline": attempt.deadline}).encode() + b"\n")
            self.process.stdin.flush()
            self.process.stdin.close()
            startup = self.next(attempt, control, attempt.clock() + 15)
            url = startup.get("url")
            parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
            if (set(startup) != {"url"} or parsed is None or parsed.scheme != "http"
                    or parsed.hostname != "127.0.0.1" or parsed.path != "/" or parsed.query
                    or parsed.fragment or parsed.username or not parsed.port
                    or parsed.netloc != "127.0.0.1:" + str(parsed.port)):
                fail("browser_unavailable")
            launcher = "/usr/share/omarchy/bin/omarchy-launch-browser"
            if not os.access(launcher, os.X_OK):
                launcher = shutil.which("xdg-open", path=self.environ.get("PATH"))
            if not launcher:
                fail("browser_unavailable")
            # Only the public loopback root is in argv; launcher is not owned
            # browser state and is never killed on cancellation.
            self.launcher = subprocess.Popen([launcher, url], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=self.environ, start_new_session=True)
            returned = self.next(attempt, control)
            if set(returned) != {"callback"}:
                fail("login_rejected")
            return attempt.receive(returned["callback"])
        except (OSError, ValueError):
            raise eero_api.ApiError("browser_unavailable", "Default browser return page could not open; previous Session preserved.") from None

    def __exit__(self, *unused):
        with defer_cancellation():
            failed = False
            try:
                if self.process is not None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                failed = True
            finally:
                if self.process is not None:
                    for stream in (self.process.stdin, self.process.stdout):
                        try:
                            stream.close()
                        except OSError:
                            failed = True
                self.buffer = b""
            if failed:
                fail("browser_unavailable")


def request(url, data, headers, transport, attempt, control):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    response = None
    try:
        with exchange_deadline(attempt, control):
            response = (transport or eero_api._default_transport)(req, 15)
            raw = response.read(MAX_MESSAGE + 1)
        if response.status != 200 or len(raw) > MAX_MESSAGE:
            fail("login_rejected")
        value = json.loads(raw, parse_constant=eero_api._reject_json_constant)
        if not isinstance(value, dict):
            fail("api")
        return value
    except eero_api.ApiError as error:
        if error.code in ("login_cancelled", "network"):
            fail(error.code)
        raise eero_api.ApiError("api", "Amazon sign-in exchange failed; previous Session preserved.") from None
    except (OSError, ValueError):
        fail("network")
    finally:
        if response is not None:
            with defer_cancellation():
                response.close()


def run(stdin, environ, transport, emit, *, browser_factory=Browser, control_factory=Control, attempt_factory=Attempt):
    # Validate store before browser launch, without touching existing Session.
    eero_session.load_session(environ)
    attempt = attempt_factory()
    control = control_factory(stdin)
    with browser_factory(environ) as browser:
        emit({"ok": True, "type": "amazon_progress", "phase": "browser_open"})
        code = browser.acquire(attempt, control)
        attempt.check()
    # Cleanup succeeded; reserve exchange + consent time near browser expiry.
    attempt.deadline = attempt.clock() + 75
    if control.poll() is not None:
        fail()
    emit({"ok": True, "type": "amazon_progress", "phase": "exchanging"})
    amazon = request("https://api.amazon.com/auth/token", json.dumps({
        "app_name": "com.eero.android", "app_version": "130050002",
        "source_token_type": "authorization_code", "source_token": code,
        "requested_token_type": "access_token", "code_verifier": attempt.verifier,
        "code_algorithm": "SHA-256", "client_id": attempt.client,
        "client_domain": "DeviceLegacy", "token_app_id": "com.eero.android"}).encode(),
        {"Accept": "application/json", "Content-Type": "application/json", "x-amzn-identity-auth-domain": "api.amazon.com"}, transport, attempt, control)
    access = amazon.get("access_token")
    if not isinstance(access, str) or not access or len(access) > 16384:
        fail("api")
    code = None
    attempt.verifier = None
    result = request(eero_api.ORIGIN + "/2.2/login/amazon", urllib.parse.urlencode({"auth_token": access, "create_account": "false"}).encode(),
        {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, transport, attempt, control)
    amazon = access = None
    token = result.get("data", {}).get("user_token") if isinstance(result.get("data"), dict) else None
    if (not isinstance(result.get("meta"), dict) or result["meta"].get("code") != 200
            or not isinstance(token, str) or not token or len(token) > 16384):
        fail("login_rejected")
    result = None
    account = request(eero_api.ORIGIN + "/2.2/account", None, {"Accept": "application/json", "X-User-Token": token}, transport, attempt, control)
    from eero_cli import _first_network_url
    if not isinstance(account.get("meta"), dict) or account["meta"].get("code") != 200:
        fail("login_rejected")
    network = _first_network_url(account.get("data"))
    attempt.check()
    if control.poll() is not None:
        fail()
    emit({"ok": True, "type": "amazon_progress", "phase": "verification_ready"})
    control.consent()
    attempt.check()
    eero_session.save_session({"token": token, "pendingUserToken": None, "networkUrl": network}, environ)
    return {"ok": True, "type": "logged_in"}
