"""End-to-end tests for bin/eero-helper's command dispatch (eero_cli.main).

Every HTTP call goes through a FakeTransport (tests/fakes.py); nothing here
ever reaches the network. The one exception is TestSubprocess, which runs the
real executable but only for commands that need no HTTP at all.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import eero_cli
import eero_session
from fakes import FakeStdin, FakeTransport, envelope


def run(argv, transport=None, environ=None, stdin_text="", stdin_bytes=None):
    # FakeStdin exposes .buffer like the real sys.stdin, so these tests
    # exercise eero_cli._read_secret's primary bytes-decode-strict path, not
    # just its io.StringIO/.readline() fallback.
    stdin = FakeStdin(stdin_bytes if stdin_bytes is not None else stdin_text)
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = eero_cli.main(argv, stdin, stdout, stderr, transport=transport, environ=environ)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def parse(output):
    lines = output.splitlines()
    assert len(lines) in (1, 2), f"unexpected stdout: {output!r}"
    if len(lines) == 2:
        assert json.loads(lines[0]) == {"ok": True, "type": "verification_ready"}
    return json.loads(lines[-1])


class StateDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.environ = {"EERO_BAR_STATE_DIR": str(Path(self._tmp.name) / "state")}

    def seed_session(self, **fields):
        eero_session.save_session(fields, self.environ)


class HappyPathTests(StateDirCase):
    def test_login_verify_status_flow(self):
        # login
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "pending-tok"}))
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_text="network-user@example.invalid\n")
        self.assertEqual(code, 0)
        payload = parse(out)
        self.assertEqual(payload, {"ok": True, "type": "code_sent", "identifier": "n***@example.invalid"})
        self.assertEqual(err, "")
        self.assertNotIn("pending-tok", out)

        # verify
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))  # no Set-Cookie -> keep pending token
        code, out, err = run(["verify"], transport=transport, environ=self.environ, stdin_text="123456\ncommit\n")
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "logged_in"})
        self.assertNotIn("pending-tok", out)
        self.assertNotIn("123456", out)

        session = eero_session.load_session(self.environ)
        self.assertEqual(session["token"], "pending-tok")
        self.assertIsNone(session["pendingUserToken"])

        # status
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"networks": {"data": [{"url": "/2.2/networks/123", "name": "Home"}]}}))
        transport.queue_response(200, envelope(200, data={"name": "Home", "status": "connected"}))
        transport.queue_response(200, envelope(200, data={"count": 0, "data": []}))
        transport.queue_response(200, envelope(200, data={"count": 0, "data": []}))
        transport.queue_response(200, envelope(200, data={"count": 0, "data": []}))
        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        payload = parse(out)
        self.assertEqual(payload["type"], "snapshot")
        self.assertEqual(payload["network"]["name"], "Home")
        self.assertEqual(payload["network"]["id"], "123")
        # networkUrl should now be cached in the session.
        self.assertEqual(eero_session.load_session(self.environ)["networkUrl"], "/2.2/networks/123")


class AuthTests(StateDirCase):
    def test_status_without_session_is_auth_required(self):
        code, out, err = run(["status"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out), {"ok": False, "type": "error", "code": "auth_required", "message": "Not logged in."})

    def test_401_refresh_and_retry_is_transparent_to_status(self):
        self.seed_session(token="stale-tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.refresh"))  # network fetch fails
        transport.queue_response(200, envelope(200, data={"user_token": "fresh-tok"}))  # refresh
        transport.queue_response(200, envelope(200, data={"name": "Home"}))  # network retried
        transport.queue_response(200, envelope(200, data=[]))  # nodes
        transport.queue_response(200, envelope(200, data=[]))  # devices
        transport.queue_response(200, envelope(200, data=[]))  # profiles

        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out)["network"]["name"], "Home")
        self.assertEqual(eero_session.load_session(self.environ)["token"], "fresh-tok")
        self.assertNotIn("fresh-tok", out)

    def test_second_401_yields_auth_required(self):
        self.seed_session(token="stale-tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.refresh"))
        transport.queue_response(200, envelope(200, data={"user_token": "fresh-tok"}))
        transport.queue_response(200, envelope(401, error="error.session.invalid"))

        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "auth_required")

    def test_rate_limited(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(429, raw=b"")
        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "rate_limited")

    def test_network_error(self):
        import urllib.error

        self.seed_session(token="tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_error(urllib.error.URLError("no route to host"))
        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "network")

    def test_malformed_json_is_api_error(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(200, raw=b"{not json")
        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")


class ApprovalPipeTests(StateDirCase):
    def test_real_pipe_approval_after_progress_commits_session(self):
        self.seed_session(token="live-token", pendingUserToken="pending-token",
                          networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        read_fd, write_fd = os.pipe()

        class ProgressOutput(io.StringIO):
            approved = False
            def flush(self):
                if not self.approved and '"verification_ready"' in self.getvalue():
                    self.approved = True
                    os.write(write_fd, b"commit\n")

        os.write(write_fd, b"123456\n")
        try:
            with io.TextIOWrapper(os.fdopen(read_fd, "rb")) as stdin:
                stdout, stderr = ProgressOutput(), io.StringIO()
                code = eero_cli.main(["verify"], stdin, stdout, stderr,
                                     transport=transport, environ=self.environ)
                self.assertEqual(code, 0)
                self.assertEqual(parse(stdout.getvalue()), {"ok": True, "type": "logged_in"})
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(eero_session.load_session(self.environ),
                                 {"token": "pending-token", "pendingUserToken": None,
                                  "networkUrl": "/2.2/networks/123"})
        finally:
            os.close(write_fd)

    def test_complete_approval_deadline_preserves_session(self):
        previous = {"token": "live-token", "pendingUserToken": "pending-token",
                    "networkUrl": "/2.2/networks/123"}
        self.seed_session(**previous)
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        read_fd, write_fd = os.pipe()
        progress = threading.Event()
        clock = [0.0]
        real_select = eero_cli.select.select

        class ProgressOutput(io.StringIO):
            def flush(self):
                progress.set()

        def write_approval():
            if progress.wait(2):
                os.write(write_fd, b"c")
                time.sleep(0.05)
                os.write(write_fd, b"ommit\n")

        calls = [0]
        def select_with_expiry(readers, writers, errors, timeout):
            calls[0] += 1
            ready = real_select(readers, writers, errors, min(timeout, 2))
            if calls[0] > 1:
                clock[0] = 31.0
            return ready

        os.write(write_fd, b"123456\n")
        writer = threading.Thread(target=write_approval)
        writer.start()
        try:
            # A real buffered stdin pipe exercises secret consumption too.
            with os.fdopen(read_fd, "rb") as raw:
                stdin = io.TextIOWrapper(raw)
                stdout, stderr = ProgressOutput(), io.StringIO()
                with mock.patch.object(eero_cli.time, "monotonic", side_effect=lambda: clock[0]), \
                     mock.patch.object(eero_cli.select, "select", side_effect=select_with_expiry):
                    code = eero_cli.main(["verify"], stdin, stdout, stderr,
                                         transport=transport, environ=self.environ)
                self.assertEqual(code, 1)
                self.assertEqual(parse(stdout.getvalue())["code"], "login_cancelled")
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(eero_session.load_session(self.environ), previous)
        finally:
            writer.join(3)
            os.close(write_fd)
        self.assertFalse(writer.is_alive(), "approval writer must finish")


class ValidationTests(StateDirCase):
    def test_bad_mac_rejected_with_zero_http_calls(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        code, out, err = run(["pause-client", "not-a-mac", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_bad_node_url_rejected_with_zero_http_calls(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        code, out, err = run(["reboot-node", "/etc/passwd"], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_bad_profile_url_rejected_with_zero_http_calls(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        code, out, err = run(["pause-profile", "/2.2/networks/x/profiles/1", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_unicode_digits_rejected_in_node_url_with_zero_http_calls(self):
        # MINOR-1: \d matches Unicode decimal digits (e.g. Arabic-indic), not
        # just ASCII 0-9. "/2.2/eeros/١٢٣" must not pass.
        self.seed_session(token="tok")
        transport = FakeTransport()
        node_url = "/2.2/eeros/١٢٣"
        code, out, err = run(["reboot-node", node_url], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_unicode_digits_rejected_in_profile_url_with_zero_http_calls(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        profile_url = "/2.2/networks/1/profiles/١٢٣"
        code, out, err = run(["pause-profile", profile_url, "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_bad_on_off_rejected_with_zero_http_calls(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        code, out, err = run(["guest", "yes"], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_uppercase_mac_is_lowercased_and_accepted(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"paused": True}))
        code, out, err = run(["pause-client", "AA:BB:CC:DD:EE:FF", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(transport.calls[0]["url"], "https://api-user.e2ro.com/2.3/networks/123/devices/aa:bb:cc:dd:ee:ff")

    def test_wrong_arg_count_is_usage(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        code, out, err = run(["led", "/2.2/eeros/1"], transport=transport, environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")

    def test_unknown_command_is_usage(self):
        code, out, err = run(["frobnicate"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")

    def test_no_command_is_usage(self):
        code, out, err = run([], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")

    def test_empty_stdin_secret_is_usage_with_zero_http_calls(self):
        transport = FakeTransport()
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_text="")
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_oversized_stdin_secret_is_usage_with_zero_http_calls(self):
        transport = FakeTransport()
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_text=("x" * 257) + "\n")
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)


class ActionTests(StateDirCase):
    def test_pause_client_uses_2_3_path(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"paused": True}))
        code, out, err = run(["pause-client", "aa:bb:cc:dd:ee:ff", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "done", "action": "pause-client"})
        self.assertEqual(transport.calls[0]["url"], "https://api-user.e2ro.com/2.3/networks/123/devices/aa:bb:cc:dd:ee:ff")
        self.assertEqual(transport.calls[0]["method"], "PUT")
        self.assertEqual(json.loads(transport.calls[0]["body"]), {"paused": True})

    def test_reboot_node_needs_no_network_resolution(self):
        self.seed_session(token="tok")  # no networkUrl cached, and none should be needed
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["reboot-node", "/2.2/eeros/456"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "done", "action": "reboot-node"})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.calls[0]["url"], "https://api-user.e2ro.com/2.2/eeros/456/reboot")

    def test_guest_resolves_network_from_account_when_not_cached(self):
        self.seed_session(token="tok")  # no networkUrl cached
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"networks": {"data": [{"url": "/2.2/networks/9"}]}}))
        transport.queue_response(200, envelope(200, data={"enabled": True}))
        code, out, err = run(["guest", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(transport.calls[1]["url"], "https://api-user.e2ro.com/2.2/networks/9/guestnetwork")
        self.assertEqual(eero_session.load_session(self.environ)["networkUrl"], "/2.2/networks/9")

    def test_led_toggle(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["led", "/2.2/eeros/1", "off"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(transport.calls[0]["body"]), {"led_on": False})
        self.assertEqual(parse(out), {"ok": True, "type": "done", "action": "led"})
        self.assertEqual(transport.call_count, 1, "LED acknowledgement does not fetch details")

    def test_led_failure_does_not_retry_or_retarget(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        transport.queue_response(200, envelope(500, error="error.internal"))
        code, out, err = run(["led", "/2.2/eeros/1", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.calls[0]["url"], "https://api-user.e2ro.com/2.2/eeros/1")

    def test_speedtest(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["speedtest"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "done", "action": "speedtest"})

    def test_pause_profile(self):
        self.seed_session(token="tok")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(
            ["pause-profile", "/2.2/networks/1/profiles/9", "off"], transport=transport, environ=self.environ
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(transport.calls[0]["body"]), {"paused": False})

    def test_reboot_network(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["reboot-network"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "done", "action": "reboot-network"})


class LogoutTests(StateDirCase):
    def test_logout_when_never_logged_in(self):
        code, out, err = run(["logout"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "logged_out"})

    def test_logout_twice_is_idempotent(self):
        self.seed_session(token="tok")
        code, out, err = run(["logout"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 0)
        code, out, err = run(["logout"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out), {"ok": True, "type": "logged_out"})
        self.assertEqual(eero_session.load_session(self.environ)["token"], None)


class ReLoginTests(StateDirCase):
    """A login started while already signed in must not wipe the current
    token until verify succeeds (spec: re-login contract) -- that is what
    lets the popup's cancel-restore keep the user signed in."""

    def test_login_preserves_existing_token_and_network(self):
        self.seed_session(token="live-token", networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "pending-2"}))
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_text="a@example.invalid\n")
        self.assertEqual(code, 0)
        session = eero_session.load_session(self.environ)
        self.assertEqual(session["pendingUserToken"], "pending-2")
        self.assertEqual(session["token"], "live-token")
        self.assertEqual(session["networkUrl"], "/2.2/networks/123")

    def test_verify_replaces_preserved_token(self):
        self.seed_session(token="live-token", networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "pending-2"}))
        run(["login"], transport=transport, environ=self.environ, stdin_text="a@example.invalid\n")
        transport.queue_response(200, envelope(200, data={}))  # no Set-Cookie -> pending stays
        code, out, err = run(["verify"], transport=transport, environ=self.environ, stdin_text="123456\ncommit\n")
        self.assertEqual(code, 0)
        session = eero_session.load_session(self.environ)
        self.assertEqual(session["token"], "pending-2")
        self.assertIsNone(session["pendingUserToken"])
        self.assertEqual(session["networkUrl"], "/2.2/networks/123")

    def test_cancel_after_verified_reply_preserves_live_session(self):
        self.seed_session(token="live-token", pendingUserToken="pending-2", networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["verify"], transport=transport, environ=self.environ, stdin_text="123456\n")
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "login_cancelled")
        self.assertEqual(eero_session.load_session(self.environ)["token"], "live-token")
        self.assertEqual(eero_session.load_session(self.environ)["networkUrl"], "/2.2/networks/123")

    def test_only_exact_commit_approval_can_replace_session(self):
        self.seed_session(token="live-token", pendingUserToken="pending-2")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["verify"], transport=transport, environ=self.environ, stdin_text="123456\nanything\n")
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "login_cancelled")
        self.assertEqual(eero_session.load_session(self.environ)["token"], "live-token")

    def test_status_still_answers_while_login_pending(self):
        self.seed_session(token="live-token", networkUrl="/2.2/networks/123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "pending-2"}))
        run(["login"], transport=transport, environ=self.environ, stdin_text="a@example.invalid\n")
        # Network resolves from the preserved cached url -- no account call.
        for payload in (
            {"name": "Home", "status": "connected"},
            [{"serial": "s1", "location": "Living Room"}],
            [{"mac": "aa:bb:cc:dd:ee:ff"}],
            [],
        ):
            transport.queue_response(200, envelope(200, data=payload))
        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out)["type"], "snapshot")


class SecretsNeverLeakTests(StateDirCase):
    def test_login_output_never_contains_raw_identifier_or_token(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "super-secret-token"}))
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_text="network-user@example.invalid\n")
        combined = out + err
        self.assertNotIn("network-user@example.invalid", combined)
        self.assertNotIn("super-secret-token", combined)

    def test_verify_output_never_contains_code_or_token(self):
        self.seed_session(pendingUserToken="pending-secret")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["verify"], transport=transport, environ=self.environ, stdin_text="999999\ncommit\n")
        combined = out + err
        self.assertNotIn("999999", combined)
        self.assertNotIn("pending-secret", combined)

    def test_session_file_never_readable_by_group_or_other(self):
        self.seed_session(token="super-secret-token")
        session_path = Path(self.environ["EERO_BAR_STATE_DIR"]) / "session.json"
        mode = stat.S_IMODE(session_path.stat().st_mode)
        self.assertEqual(mode, 0o600)


class TestSubprocess(unittest.TestCase):
    """Runs the real bin/eero-helper for commands that need no HTTP call."""

    def test_usage_error_via_real_executable(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "eero-helper"), "bogus"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout.decode("utf-8").strip())
        self.assertEqual(payload, {"ok": False, "type": "error", "code": "usage", "message": "Unknown command: bogus"})

    def test_logout_via_real_executable_with_isolated_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            environ = {"EERO_BAR_STATE_DIR": str(Path(tmp) / "state")}
            result = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "eero-helper"), "logout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environ,
                timeout=5,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.decode("utf-8").strip()), {"ok": True, "type": "logged_out"})


class SecretStdinEdgeCaseTests(StateDirCase):
    """MINOR-3: non-UTF-8 stdin -> usage/exit 2; a missing trailing newline
    (stdin closed right after the secret, as QML does) still works."""

    def test_non_utf8_stdin_is_usage_with_zero_http_calls(self):
        transport = FakeTransport()
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_bytes=b"\xff\xfe\x00bad\n")
        self.assertEqual(code, 2)
        self.assertEqual(parse(out)["code"], "usage")
        self.assertEqual(transport.call_count, 0)

    def test_secret_without_trailing_newline_still_works(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "pending-tok"}))
        # No trailing "\n": QML closes stdin right after writing the secret.
        code, out, err = run(["login"], transport=transport, environ=self.environ, stdin_bytes=b"person@example.com")
        self.assertEqual(code, 0)
        self.assertEqual(parse(out)["type"], "code_sent")

    def test_verify_eof_without_commit_does_not_save_session(self):
        self.seed_session(pendingUserToken="pending-123")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["verify"], transport=transport, environ=self.environ, stdin_bytes=b"123456")
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "login_cancelled")
        self.assertIsNone(eero_session.load_session(self.environ)["token"])


class StaleNetworkRetryTests(StateDirCase):
    """status (read-only) retries once after a 403/404: clears the cached
    networkUrl, re-resolves it via /account, and retries the fetch. A
    mutating action (reboot-network, guest, pause-client, speedtest) instead
    clears the cache and fails WITHOUT retrying -- see N1: retrying a
    mutation against a network re-resolved after the fact could act on a
    network the user never confirmed."""

    def test_status_retries_once_after_404_then_succeeds(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")  # stale
        transport = FakeTransport()
        transport.queue_response(200, envelope(404, error="error.network.notFound"))  # stale network -> network()
        transport.queue_response(200, envelope(200, data={"networks": {"data": [{"url": "/2.2/networks/222"}]}}))
        transport.queue_response(200, envelope(200, data={"name": "Home"}))
        transport.queue_response(200, envelope(200, data=[]))
        transport.queue_response(200, envelope(200, data=[]))
        transport.queue_response(200, envelope(200, data=[]))

        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(parse(out)["network"]["name"], "Home")
        self.assertEqual(eero_session.load_session(self.environ)["networkUrl"], "/2.2/networks/222")

    def test_status_retry_exhausted_still_surfaces_as_api_error_not_infinite_loop(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")
        transport = FakeTransport()
        transport.queue_response(200, envelope(404, error="error.network.notFound"))
        transport.queue_response(200, envelope(200, data={"networks": {"data": [{"url": "/2.2/networks/222"}]}}))
        transport.queue_response(200, envelope(404, error="error.network.notFound"))  # still 404 after retry

        code, out, err = run(["status"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 3)  # exactly one retry, no loop

    def test_reboot_network_stale_network_clears_cache_and_fails_without_retry(self):
        # The concrete regression N1 fixes: a 404 on the cached (stale)
        # network must NOT go on to reboot a different, re-resolved
        # network the user never confirmed.
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")
        transport = FakeTransport()
        transport.queue_response(200, envelope(404, error="error.network.notFound"))
        code, out, err = run(["reboot-network"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        payload = parse(out)
        self.assertEqual(payload["code"], "api")
        self.assertIn("Network changed", payload["message"])
        # Exactly the one (failed) call to the stale network -- no /account
        # re-resolve, and definitely no second reboot call to any network.
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.calls[0]["url"], "https://api-user.e2ro.com/2.2/networks/111/reboot")
        self.assertIsNone(eero_session.load_session(self.environ)["networkUrl"])

    def test_guest_stale_network_clears_cache_and_fails_without_retry(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")
        transport = FakeTransport()
        transport.queue_response(200, envelope(403, error="error.network.forbidden"))
        code, out, err = run(["guest", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 1)
        self.assertIsNone(eero_session.load_session(self.environ)["networkUrl"])

    def test_pause_client_stale_network_clears_cache_and_fails_without_retry(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")
        transport = FakeTransport()
        transport.queue_response(200, envelope(404, error="error.network.notFound"))
        code, out, err = run(["pause-client", "aa:bb:cc:dd:ee:ff", "on"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(
            transport.calls[0]["url"], "https://api-user.e2ro.com/2.3/networks/111/devices/aa:bb:cc:dd:ee:ff"
        )
        self.assertIsNone(eero_session.load_session(self.environ)["networkUrl"])

    def test_speedtest_stale_network_clears_cache_and_fails_without_retry(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")
        transport = FakeTransport()
        transport.queue_response(200, envelope(403, error="error.network.forbidden"))
        code, out, err = run(["speedtest"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 1)
        self.assertIsNone(eero_session.load_session(self.environ)["networkUrl"])

    def test_non_stale_api_error_is_not_treated_as_stale_network(self):
        # A 500 (or any status outside 403/404) must not trigger the
        # clear-and-fail path at all -- it's a plain "api" error and the
        # cached networkUrl is left alone.
        self.seed_session(token="tok", networkUrl="/2.2/networks/111")
        transport = FakeTransport()
        transport.queue_response(200, envelope(500, error="error.internal"))
        code, out, err = run(["reboot-network"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(eero_session.load_session(self.environ)["networkUrl"], "/2.2/networks/111")


class StaleCachedNetworkUrlValidationTests(StateDirCase):
    """M1a: an invalid cached networkUrl is silently re-resolved, never
    trusted or sent as-is."""

    def test_malformed_cached_network_url_is_ignored_and_reresolved(self):
        self.seed_session(token="tok", networkUrl="https://evil.host/2.2/networks/1")
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"networks": {"data": [{"url": "/2.2/networks/9"}]}}))
        transport.queue_response(200, envelope(200, data={}))
        code, out, err = run(["reboot-network"], transport=transport, environ=self.environ)
        self.assertEqual(code, 0)
        self.assertEqual(transport.calls[1]["url"], "https://api-user.e2ro.com/2.2/networks/9/reboot")
        self.assertEqual(eero_session.load_session(self.environ)["networkUrl"], "/2.2/networks/9")

    def test_malformed_url_from_account_is_api_error(self):
        self.seed_session(token="tok")  # no cached networkUrl at all
        transport = FakeTransport()
        transport.queue_response(
            200, envelope(200, data={"networks": {"data": [{"url": ".evil.host/2.2/networks/1"}]}})
        )
        code, out, err = run(["reboot-network"], transport=transport, environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "api")
        self.assertEqual(transport.call_count, 1)  # never attempted the reboot call


class StateErrorTests(StateDirCase):
    """Item 8: CLI-level `state` errors for a symlinked/foreign-owned
    session, surfaced through eero_cli.main() exactly like any other error."""

    def test_symlinked_session_directory_is_state_error(self):
        state_dir = Path(self.environ["EERO_BAR_STATE_DIR"])
        real_dir = state_dir.parent / "real-target"
        real_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        state_dir.parent.mkdir(parents=True, exist_ok=True)
        state_dir.symlink_to(real_dir, target_is_directory=True)

        code, out, err = run(["logout"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "state")

    def test_foreign_owned_session_file_is_state_error(self):
        self.seed_session(token="tok")
        with mock.patch("eero_session.os.getuid", return_value=os.getuid() + 1):
            code, out, err = run(["status"], transport=FakeTransport(), environ=self.environ)
        self.assertEqual(code, 1)
        self.assertEqual(parse(out)["code"], "state")


class CatchAllTests(StateDirCase):
    """Item 8: an unexpected (non-ApiError/SessionError/_UsageError)
    exception must still yield exactly one JSON line on stdout and exit 1 --
    never a Python traceback."""

    def test_unexpected_exception_yields_single_json_line_and_exit_1(self):
        self.seed_session(token="tok", networkUrl="/2.2/networks/1")

        def _boom(request, timeout):
            raise RuntimeError("something no error code covers")

        code, out, err = run(["status"], transport=_boom, environ=self.environ)
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["type"], "error")
        self.assertEqual(payload["code"], "api")
        # No exception text/traceback leaks into the message.
        self.assertNotIn("RuntimeError", payload["message"])
        self.assertNotIn("something no error code covers", payload["message"])


if __name__ == "__main__":
    unittest.main()
