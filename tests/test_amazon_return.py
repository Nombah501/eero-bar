"""Real loopback HTTP and owned private pipes; synthetic artifacts only."""
import http.client
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import time
import threading
from unittest import mock
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import eero_amazon as auth


class LoopbackConnection(http.client.HTTPConnection):
    """Exercise owned local HTTP without weakening the suite's network guard."""
    def connect(self):
        if self.host != "127.0.0.1" or self._tunnel_host is not None:
            raise AssertionError("Only the owned loopback receiver is allowed")
        self.sock = socket.create_connection((self.host, self.port), self.timeout)


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen([sys.executable, "-B", str(ROOT / "lib/eero_amazon_return.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.cleanup)
        self.child.stdin.write(json.dumps({"portal": "https://www.amazon.com/ap/signin?synthetic=1", "deadline": time.monotonic() + 10}).encode() + b"\n")
        self.child.stdin.close()
        self.assertTrue(select.select([self.child.stdout], [], [], 3)[0])
        self.url = json.loads(self.child.stdout.readline())["url"]
        self.parts = urllib.parse.urlsplit(self.url)
        self.host = self.parts.netloc
        status, page, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.nonce = re.search(r'name="nonce" value="([^"]+)"', page).group(1)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn('type="password"', page)

    def cleanup(self):
        if self.child.poll() is None:
            self.child.terminate()
        self.child.wait(timeout=3)
        self.child.stdout.close()
        self.child.stderr.close()

    def request(self, method, path, body=None, headers=None):
        connection = LoopbackConnection("127.0.0.1", self.parts.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode(), dict(response.getheaders())
        finally:
            connection.close()

    def post(self, callback=None, nonce=None, **overrides):
        value = callback if callback is not None else auth.CALLBACK + "?openid.oa2.authorization_code=synthetic-private"
        body = urllib.parse.urlencode({"nonce": self.nonce if nonce is None else nonce, "callback": value})
        headers = {"Origin": self.url.rstrip("/"), "Content-Type": "application/x-www-form-urlencoded"}
        headers.update(overrides)
        return self.request("POST", "/return", body, headers)

    def test_rejected_requests_do_not_consume_return_then_private_pipe_receives(self):
        for headers in ({"Origin": "https://foreign.invalid"}, {"Origin": "null"}, {"Host": "foreign.invalid"}, {"Content-Type": "application/json"}):
            status, body, _ = self.post(**headers)
            self.assertEqual(status, 400)
            self.assertNotIn("synthetic-private", body)
        self.assertEqual(self.post(nonce="wrong")[0], 400)
        for path in ("/return?x=1", "/unknown", "/?x=1"):
            self.assertEqual(self.request("GET", path)[0], 400)
        self.assertEqual(self.post(auth.CALLBACK + "?openid.oa2.authorization_code=a&openid.oa2.authorization_code=b")[0], 400)
        self.assertEqual(self.post("https://evil.invalid/?secret=synthetic-private")[0], 400)
        self.assertEqual(self.request("POST", "/return", "x" * 65537, {"Origin": self.url.rstrip("/"), "Content-Type": "application/x-www-form-urlencoded"})[0], 400)
        status, body, _ = self.post()
        self.assertEqual(status, 200)
        self.assertNotIn("synthetic-private", body)
        self.assertTrue(select.select([self.child.stdout], [], [], 3)[0])
        value = json.loads(self.child.stdout.readline())
        attempt = auth.Attempt()
        self.assertEqual(attempt.receive(value["callback"]), "synthetic-private")
        with self.assertRaises(Exception):
            attempt.receive(value["callback"])
        self.child.wait(timeout=3)
        self.assertEqual(self.child.stderr.read(), b"")

    def test_duplicate_form_fields_and_nonce_rejected(self):
        body = urllib.parse.urlencode({"nonce": self.nonce, "callback": auth.CALLBACK}) + "&nonce=duplicate"
        self.assertEqual(self.request("POST", "/return", body, {"Origin": self.url.rstrip("/"), "Content-Type": "application/x-www-form-urlencoded"})[0], 400)
        self.assertEqual(self.post()[0], 200)

    def test_owned_child_cancellation_closes_socket_without_browser_ownership(self):
        self.child.terminate()
        self.child.wait(timeout=3)
        with self.assertRaises(OSError):
            self.request("GET", "/")


    def test_duplicate_host_origin_and_content_length_rejected(self):
        for name, value in (("Host", self.host), ("Origin", self.url.rstrip("/")), ("Content-Length", "1")):
            connection = LoopbackConnection("127.0.0.1", self.parts.port, timeout=3)
            try:
                connection.putrequest("POST", "/return", skip_host=True)
                connection.putheader("Host", self.host)
                connection.putheader("Origin", self.url.rstrip("/"))
                connection.putheader("Content-Type", "application/x-www-form-urlencoded")
                connection.putheader("Content-Length", "1")
                connection.putheader(name, value)
                connection.endheaders(b"x")
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                connection.close()
        self.assertEqual(self.post()[0], 200)

    def test_absolute_acquisition_expiry_closes_private_pipe_without_callback(self):
        child = subprocess.Popen([sys.executable, "-B", str(ROOT / "lib/eero_amazon_return.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            child.stdin.write(json.dumps({"portal": "https://www.amazon.com/ap/signin?synthetic=1",
                "deadline": time.monotonic() + 0.3}).encode() + b"\n")
            child.stdin.close()
            self.assertTrue(select.select([child.stdout], [], [], 2)[0])
            self.assertEqual(set(json.loads(child.stdout.readline())), {"url"})
            child.wait(timeout=2)
            self.assertEqual(child.stdout.read(), b"")
            self.assertEqual(child.stderr.read(), b"")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            child.stdout.close()
            child.stderr.close()


    def test_slow_partial_body_is_bounded_and_does_not_emit_callback(self):
        import socket
        connection = socket.create_connection(("127.0.0.1", self.parts.port), timeout=4)
        self.addCleanup(connection.close)
        connection.sendall(("POST /return HTTP/1.0\r\nHost: " + self.host
            + "\r\nOrigin: " + self.url.rstrip("/") + "\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 100\r\n\r\nx").encode())
        started = time.monotonic()
        while connection.recv(4096):
            pass
        self.assertLess(time.monotonic() - started, 4)
        self.assertFalse(select.select([self.child.stdout], [], [], 0)[0])
        self.assertEqual(self.post()[0], 200)


class DefaultBrowserFlowTests(unittest.TestCase):
    def test_real_receiver_exchange_candidate_and_explicit_consent(self):
        from tests.test_eero_amazon import AcquisitionTests
        fixture = AcquisitionTests()
        fixture.setUp()
        real_popen = subprocess.Popen
        workers = []
        errors = []
        launched = []

        def launch(argv, **kwargs):
            if len(argv) > 2 and argv[1] == "-B":
                return real_popen(argv, **kwargs)
            launched.append(argv)
            def transfer():
                try:
                    parts = urllib.parse.urlsplit(argv[1])
                    connection = LoopbackConnection(parts.hostname, parts.port, timeout=3)
                    connection.request("GET", "/")
                    page = connection.getresponse().read().decode()
                    nonce = re.search(r'name="nonce" value="([^"]+)"', page).group(1)
                    connection.close()
                    connection = LoopbackConnection(parts.hostname, parts.port, timeout=3)
                    body = urllib.parse.urlencode({"nonce": nonce, "callback": auth.CALLBACK + "?openid.oa2.authorization_code=synthetic-code"})
                    connection.request("POST", "/return", body, {"Origin": argv[1].rstrip("/"), "Content-Type": "application/x-www-form-urlencoded"})
                    response = connection.getresponse()
                    if response.status != 200:
                        raise AssertionError("Return rejected")
                    response.read()
                    connection.close()
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=transfer)
            workers.append(worker)
            worker.start()
            return mock.Mock(poll=mock.Mock(return_value=0))
        try:
            attempt = auth.Attempt()
            attempt.deadline = time.monotonic() + 10
            with mock.patch.object(auth.subprocess, "Popen", side_effect=launch), mock.patch.object(auth.os, "access", return_value=True):
                result = auth.run(fixture.stream, fixture.env, fixture.transport, fixture.emit, attempt_factory=lambda: attempt)
            for worker in workers:
                worker.join(timeout=3)
            self.assertEqual(errors, [])
            self.assertEqual(result, {"ok": True, "type": "logged_in"})
            self.assertEqual([m["phase"] for m in fixture.metadata], ["browser_open", "exchanging", "verification_ready"])
            self.assertEqual(len(launched), 1)
            self.assertRegex(launched[0][1], r"^http://127\.0\.0\.1:\d+/$")
            self.assertNotIn("synthetic-code", json.dumps(launched))
        finally:
            fixture.doCleanups()

    def test_launcher_failure_preserves_old_session_and_reaps_receiver(self):
        from tests.test_eero_amazon import AcquisitionTests
        fixture = AcquisitionTests()
        fixture.setUp()
        real_popen = subprocess.Popen
        children = []
        def launch(argv, **kwargs):
            if len(argv) > 2 and argv[1] == "-B":
                child = real_popen(argv, **kwargs)
                children.append(child)
                return child
            return mock.Mock(poll=mock.Mock(return_value=1))
        try:
            with mock.patch.object(auth.subprocess, "Popen", side_effect=launch), mock.patch.object(auth.os, "access", return_value=True):
                with self.assertRaises(Exception) as caught:
                    auth.run(fixture.stream, fixture.env, fixture.transport, fixture.emit)
            self.assertEqual(caught.exception.code, "browser_unavailable")
            self.assertEqual(fixture.requests, [])
            self.assertEqual(auth.eero_session.load_session(fixture.env), fixture.old)
            self.assertTrue(children[0].stdout.closed)
            self.assertIsNotNone(children[0].poll())
        finally:
            fixture.doCleanups()


    def test_eof_cancels_owned_receiver_before_exchange_without_killing_launcher(self):
        from tests.test_eero_amazon import AcquisitionTests
        fixture = AcquisitionTests()
        fixture.setUp()
        real_popen = subprocess.Popen
        children = []
        launcher = mock.Mock(poll=mock.Mock(return_value=None))
        def launch(argv, **kwargs):
            if len(argv) > 2 and argv[1] == "-B":
                child = real_popen(argv, **kwargs)
                children.append(child)
                return child
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, fixture.write_fd)
            finally:
                os.close(null)
            return launcher
        try:
            with mock.patch.object(auth.subprocess, "Popen", side_effect=launch), mock.patch.object(auth.os, "access", return_value=True):
                with self.assertRaises(Exception) as caught:
                    auth.run(fixture.stream, fixture.env, fixture.transport, fixture.emit)
            self.assertEqual(caught.exception.code, "login_cancelled")
            self.assertEqual(fixture.requests, [])
            self.assertEqual(auth.eero_session.load_session(fixture.env), fixture.old)
            self.assertTrue(children[0].stdout.closed)
            self.assertIsNotNone(children[0].poll())
            launcher.terminate.assert_not_called()
            launcher.kill.assert_not_called()
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
