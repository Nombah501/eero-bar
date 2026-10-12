"""Synthetic public acquisition seam; no Amazon/network/browser launch."""
import io
import json
import os
import signal
import threading
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import eero_amazon as auth
import eero_api
import eero_session


class Response(io.BytesIO):
    status = 200


def callback(url=None):
    return url or auth.CALLBACK + "?openid.oa2.authorization_code=synthetic-code"


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"EERO_BAR_STATE_DIR": self.tmp.name + "/state"}
        eero_session.save_session({"token": "old-session", "networkUrl": "/2.2/networks/1"}, self.env)
        self.old = eero_session.load_session(self.env)
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(os.close, self.write_fd)
        self.stream = os.fdopen(self.read_fd, "rb", buffering=0)
        self.addCleanup(self.stream.close)
        self.replies = [
            {"access_token": "synthetic-amazon"},
            {"meta": {"code": 200}, "data": {"user_token": "synthetic-eero"}},
            {"meta": {"code": 200}, "data": {"networks": {"data": [{"url": "/2.2/networks/2"}]}}}]
        self.requests = []
        self.metadata = []
        self.consent = b"commit\n"
        self.event = callback()
        self.mutate = lambda attempt: None
        self.cleaned = False

    def transport(self, req, timeout):
        self.requests.append(req)
        self.assertEqual(timeout, 15)
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(json.dumps(value).encode())

    def emit(self, value):
        self.metadata.append(value)
        self.assertEqual(eero_session.load_session(self.env), self.old)
        if value.get("phase") == "verification_ready":
            os.write(self.write_fd, self.consent)

    def run_flow(self):
        owner = self
        class Browser:
            def __init__(self, env): pass
            def __enter__(self): return self
            def __exit__(self, *args): owner.cleaned = True
            def acquire(self, attempt, control):
                owner.mutate(attempt)
                return attempt.receive(owner.event)
        return auth.run(self.stream, self.env, self.transport, self.emit, browser_factory=Browser)

    def rejected(self):
        with self.assertRaises(eero_api.ApiError): self.run_flow()
        self.assertEqual(eero_session.load_session(self.env), self.old)
        self.assertTrue(self.cleaned)

    def test_verified_relogin_requires_consent_and_saves_only_final_session(self):
        self.assertEqual(self.run_flow(), {"ok": True, "type": "logged_in"})
        saved = eero_session.load_session(self.env)
        self.assertEqual(saved, {"token": "synthetic-eero", "pendingUserToken": None, "networkUrl": "/2.2/networks/2"})
        self.assertTrue(self.cleaned)
        self.assertEqual([r.full_url for r in self.requests], ["https://api.amazon.com/auth/token", eero_api.ORIGIN + "/2.2/login/amazon", eero_api.ORIGIN + "/2.2/account"])
        self.assertEqual(json.loads(self.requests[0].data)["source_token"], "synthetic-code")
        self.assertIn(b"create_account=false", self.requests[1].data)
        self.assertEqual(self.requests[2].get_header("X-user-token"), "synthetic-eero")
        output = json.dumps(self.metadata)
        for secret in ("synthetic-code", "synthetic-amazon", "synthetic-eero", "old-session"):
            self.assertNotIn(secret, output)

    def test_wrong_origin_path_and_malformed_callback(self):
        for url in ("https://evil.invalid/ap/maplanding", "http://www.amazon.com/ap/maplanding",
                "https://www.amazon.com:443/ap/maplanding", "https://www.amazon.com/ap/maplanding/",
                "https://user@www.amazon.com/ap/maplanding", callback() + "#fragment",
                auth.CALLBACK + "?code=synthetic", callback() + "&openid.oa2.authorization_code=duplicate",
                callback() + "&openid%2Eoa2%2Eauthorization_code=duplicate", callback() + "%ZZ",
                callback() + "%FF", callback() + "\n", callback() + "\\suffix",
                auth.CALLBACK + "?openid.oa2.authorization_code=" + "x" * 8193, None, {}, 123):
            with self.subTest(url=url):
                self.event = url
                self.rejected()
        self.assertEqual(self.requests, [])


    def test_cancel_closed_expired_and_replayed_attempts(self):
        for mutation in (lambda a: setattr(a, "cancelled", True),
                lambda a: setattr(a, "deadline", 0), lambda a: setattr(a, "used", True)):
            self.mutate = mutation
            self.rejected()
        self.assertEqual(self.requests, [])

    def test_duplicate_code_field_is_rejected(self):
        self.event = callback(auth.CALLBACK + "?openid.oa2.authorization_code=a&openid.oa2.authorization_code=b")
        self.rejected()

    def test_exchange_and_verification_errors_preserve_session(self):
        for index in range(3):
            with self.subTest(stage=index):
                self.replies = [{"access_token": "synthetic-amazon"},
                    {"meta": {"code": 200}, "data": {"user_token": "synthetic-eero"}},
                    {"meta": {"code": 200}, "data": {"networks": {"data": [{"url": "/2.2/networks/2"}]}}}]
                self.replies[index] = OSError("synthetic-private-code-error")
                self.rejected()

    def test_declined_save_preserves_session(self):
        self.consent = b"cancel\n"
        self.rejected()

    def test_storage_error_preserves_session(self):
        with mock.patch.object(eero_session, "save_session", side_effect=eero_session.SessionError("synthetic refusal")):
            with self.assertRaises(eero_session.SessionError): self.run_flow()
        self.assertEqual(eero_session.load_session(self.env), self.old)

    def test_late_cancel_after_candidate_prevents_save(self):
        original = self.emit
        def emit(value):
            if value.get("phase") == "verification_ready": self.consent = b"cancel\n"
            original(value)
        self.emit = emit
        self.rejected()

    def test_receiver_replay_rejected_after_first_callback(self):
        attempt = auth.Attempt()
        self.assertEqual(attempt.receive(callback()), "synthetic-code")
        with self.assertRaises(eero_api.ApiError): attempt.receive(callback())

    def test_closed_control_pipe_cancels_before_exchange(self):
        os.close(self.write_fd)
        self.write_fd = os.open(os.devnull, os.O_WRONLY)
        self.rejected()
        self.assertEqual(self.requests, [])


    def test_browser_cleanup_failure_prevents_exchange_or_save(self):
        class FailedCleanup:
            def __init__(self, env): pass
            def __enter__(self): return self
            def acquire(self, attempt, control): return "synthetic-code"
            def __exit__(self, *args): auth.fail("browser_unavailable")
        with self.assertRaises(eero_api.ApiError):
            auth.run(self.stream, self.env, self.transport, self.emit, browser_factory=FailedCleanup)
        self.assertEqual(self.requests, [])
        self.assertEqual(eero_session.load_session(self.env), self.old)

    def test_malformed_account_metadata_preserves_session(self):
        self.replies[2]["meta"] = []
        self.rejected()

    def test_upstream_error_text_is_not_returned(self):
        self.replies[0] = eero_api.ApiError("api", "synthetic-private-code-error")
        with self.assertRaises(eero_api.ApiError) as caught:
            self.run_flow()
        self.assertNotIn("synthetic-private-code-error", caught.exception.message)
        self.assertEqual(eero_session.load_session(self.env), self.old)


    def test_blocked_exchange_deadline_cancel_expiry_and_preconsent(self):
        for stage in ("open", "read"):
            for reason in ("deadline", "eof", "partial", "commit", "expiry", "sigterm"):
                with self.subTest(stage=stage, reason=reason):
                    read_fd, write_fd = os.pipe()
                    stop = threading.Event()
                    active = []
                    self.mutate = lambda attempt: active.append(attempt)
                    entered = threading.Event()
                    response_closed = []
                    def block():
                        entered.set()
                        while True:
                            if os.read(read_fd, 1) == b"!":
                                raise OSError("synthetic safety release")
                    class BlockedResponse:
                        status = 200
                        def read(self, size): return block()
                        def close(self): response_closed.append(True)
                    def transport(req, timeout):
                        self.requests.append(req)
                        if reason == "expiry":
                            active[0].deadline = active[0].clock() + 0.08
                        if stage == "open": return block()
                        return BlockedResponse()
                    def producer():
                        if not entered.wait(1): return
                        release = time.monotonic() + 1
                        if reason == "eof":
                            null = os.open(os.devnull, os.O_WRONLY)
                            try: os.dup2(null, self.write_fd)
                            finally: os.close(null)
                        elif reason in ("partial", "commit"):
                            os.write(self.write_fd, b"comm" if reason == "partial" else b"commit\n")
                        elif reason == "sigterm":
                            os.kill(os.getpid(), signal.SIGTERM)
                        while not stop.wait(0.02):
                            if time.monotonic() >= release:
                                os.write(write_fd, b"!")
                                return
                            os.write(write_fd, b"x")
                    worker = threading.Thread(target=producer)
                    previous_alarm = signal.getsignal(signal.SIGALRM)
                    previous_term = signal.signal(signal.SIGTERM, lambda *_: auth.fail())
                    started = time.monotonic()
                    original_transport = self.transport
                    self.transport = transport
                    worker.start()
                    try:
                        with mock.patch.object(auth, "_EXCHANGE_SECONDS", 0.15), mock.patch.object(auth, "_CONTROL_INTERVAL", 0.01):
                            with self.assertRaises(eero_api.ApiError) as caught: self.run_flow()
                        self.assertEqual(caught.exception.code, "network" if reason == "deadline" else "login_cancelled")
                        self.assertLess(time.monotonic() - started, 1)
                        self.assertEqual(response_closed, [True] if stage == "read" else [])
                        self.assertNotIn("verification_ready", [m.get("phase") for m in self.metadata])
                        self.assertEqual(eero_session.load_session(self.env), self.old)
                        self.assertIs(signal.getsignal(signal.SIGALRM), previous_alarm)
                        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))
                    finally:
                        stop.set()
                        worker.join()
                        signal.signal(signal.SIGTERM, previous_term)
                        self.transport = original_transport
                        os.close(read_fd)
                        os.close(write_fd)
                        self.metadata = []
                        self.requests = []
                        if reason == "eof":
                            # Restore the control pipe for the next subcase.
                            new_read, new_write = os.pipe()
                            os.dup2(new_read, self.stream.fileno())
                            os.dup2(new_write, self.write_fd)
                            os.close(new_read)
                            os.close(new_write)

    def test_exchange_restores_existing_alarm_handler_and_timer(self):
        previous = signal.getsignal(signal.SIGALRM)
        timer = signal.getitimer(signal.ITIMER_REAL)
        handler = lambda *_: self.fail("preexisting timer fired during synthetic flow")
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, 10, 2)
        try:
            self.assertEqual(self.run_flow(), {"ok": True, "type": "logged_in"})
            self.assertIs(signal.getsignal(signal.SIGALRM), handler)
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertGreater(remaining, 8)
            self.assertLessEqual(remaining, 10)
            self.assertEqual(interval, 2)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            signal.setitimer(signal.ITIMER_REAL, *timer)



class ControlDeadlineTests(unittest.TestCase):
    def test_partial_line_does_not_extend_consent_deadline(self):
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(read_fd, "rb", buffering=0) as stream:
                now = [0]
                control = auth.Control(stream, clock=lambda: now[0])
                os.write(write_fd, b"comm")
                real_select = auth.select.select
                def ready(readers, writers, errors, timeout):
                    if timeout == 0: return real_select(readers, writers, errors, 0)
                    now[0] = 31
                    return (readers, [], [])
                with mock.patch.object(auth.select, "select", side_effect=ready):
                    with self.assertRaises(eero_api.ApiError): control.consent()
        finally:
            os.close(write_fd)

    def test_partial_preapproval_rejected(self):
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(read_fd, "rb", buffering=0) as stream:
                control = auth.Control(stream)
                os.write(write_fd, b"comm")
                self.assertIsNone(control.poll())
                with self.assertRaises(eero_api.ApiError): control.consent()
        finally: os.close(write_fd)


if __name__ == "__main__": unittest.main()
