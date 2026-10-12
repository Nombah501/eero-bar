import socket
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import eero_api
from fakes import FakeTransport, FakeHeaders, FakeResponse, envelope


class RequestBuildingTests(unittest.TestCase):
    def test_url_is_origin_plus_resource_url_without_reappending_2_2(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"name": "Home"}))
        client = eero_api.EeroClient(token="tok", transport=transport)
        client.request("GET", "/2.2/networks/123")
        self.assertEqual(transport.calls[0]["url"], "https://api-user.e2ro.com/2.2/networks/123")

    def test_pause_client_uses_2_3_path_not_2_2(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"paused": True}))
        client = eero_api.EeroClient(token="tok", transport=transport)
        client.pause_client("123", "aa:bb:cc:dd:ee:ff", True)
        self.assertEqual(
            transport.calls[0]["url"],
            "https://api-user.e2ro.com/2.3/networks/123/devices/aa:bb:cc:dd:ee:ff",
        )
        self.assertEqual(transport.calls[0]["method"], "PUT")

    def test_cookie_and_user_agent_headers_are_sent(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        client = eero_api.EeroClient(token="secret-token", transport=transport)
        client.request("GET", "/2.2/account")
        headers = transport.calls[0]["headers"]
        self.assertEqual(headers.get("cookie"), "s=secret-token")
        self.assertIn("eero-bar", headers.get("user-agent", ""))

    def test_no_cookie_header_when_no_token(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "abc"}))
        client = eero_api.EeroClient(transport=transport)
        client.request("POST", "/2.2/login", {"login": "a@example.invalid"})
        self.assertNotIn("cookie", transport.calls[0]["headers"])


class ErrorMappingTests(unittest.TestCase):
    def test_rate_limited_on_http_429(self):
        transport = FakeTransport()
        transport.queue_response(429, raw=b"")
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "rate_limited")

    def test_network_error_on_url_error(self):
        transport = FakeTransport()
        transport.queue_error(urllib.error.URLError("boom"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "network")

    def test_network_error_on_timeout(self):
        transport = FakeTransport()
        transport.queue_error(socket.timeout("timed out"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "network")

    def test_malformed_json_is_api_error(self):
        transport = FakeTransport()
        transport.queue_response(200, raw=b"not json{")
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "api")

    def test_missing_meta_is_api_error(self):
        transport = FakeTransport()
        transport.queue_response(200, payload={"data": {}})
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "api")

    def test_unexpected_list_shape_is_api_error(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"nope": "not a list or data.data"}))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.nodes("/2.2/networks/1")
        self.assertEqual(ctx.exception.code, "api")

    def test_list_endpoint_accepts_bare_list(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data=[{"mac": "aa:bb:cc:dd:ee:ff"}]))
        client = eero_api.EeroClient(token="tok", transport=transport)
        result = client.devices("/2.2/networks/1")
        self.assertEqual(result, [{"mac": "aa:bb:cc:dd:ee:ff"}])

    def test_list_endpoint_unwraps_nested_data(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"count": 1, "data": [{"url": "/2.2/eeros/1"}]}))
        client = eero_api.EeroClient(token="tok", transport=transport)
        result = client.nodes("/2.2/networks/1")
        self.assertEqual(result, [{"url": "/2.2/eeros/1"}])


class SessionRefreshTests(unittest.TestCase):
    def test_401_refresh_then_retry_succeeds(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.refresh"))
        transport.queue_response(200, envelope(200, data={"user_token": "new-token"}))
        transport.queue_response(200, envelope(200, data={"name": "Home"}))
        refreshed = []
        client = eero_api.EeroClient(token="old-token", transport=transport, on_token_refreshed=refreshed.append)

        result = client.request("GET", "/2.2/networks/1")

        self.assertEqual(result, {"name": "Home"})
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(refreshed, ["new-token"])
        self.assertEqual(client.token, "new-token")
        # The retried call must use the refreshed token's cookie.
        self.assertEqual(transport.calls[2]["headers"]["cookie"], "s=new-token")

    def test_second_401_after_refresh_is_auth_required(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.invalid"))
        transport.queue_response(200, envelope(200, data={"user_token": "new-token"}))
        transport.queue_response(200, envelope(401, error="error.session.invalid"))
        client = eero_api.EeroClient(token="old-token", transport=transport)

        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/networks/1")

        self.assertEqual(ctx.exception.code, "auth_required")
        self.assertEqual(transport.call_count, 3)

    def test_refresh_failure_is_auth_required_without_looping(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.refresh"))
        transport.queue_response(200, envelope(401, error="error.session.invalid"))
        client = eero_api.EeroClient(token="old-token", transport=transport)

        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/networks/1")

        self.assertEqual(ctx.exception.code, "auth_required")
        self.assertEqual(transport.call_count, 2)

    def test_non_refreshable_401_is_auth_required_immediately(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.something.else"))
        client = eero_api.EeroClient(token="old-token", transport=transport)

        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/networks/1")

        self.assertEqual(ctx.exception.code, "auth_required")
        self.assertEqual(transport.call_count, 1)


class LoginVerifyTests(unittest.TestCase):
    def test_login_stores_user_token_and_returns_it(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"user_token": "pending-123"}))
        client = eero_api.EeroClient(transport=transport)
        token = client.login("person@example.com")
        self.assertEqual(token, "pending-123")
        self.assertEqual(client.token, "pending-123")

    def test_login_rejection_maps_to_login_rejected(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(400, error="error.login.invalid"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.login("bad")
        self.assertEqual(ctx.exception.code, "login_rejected")

    def test_login_network_error_stays_network_not_login_rejected(self):
        transport = FakeTransport()
        transport.queue_error(urllib.error.URLError("boom"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.login("person@example.com")
        self.assertEqual(ctx.exception.code, "network")

    def test_verify_rejection_is_login_rejected(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(400, error="error.login.verify.invalid"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.verify("pending-123", "000000")
        self.assertEqual(ctx.exception.code, "login_rejected")

    def test_verify_prefers_set_cookie_token_over_pending(self):
        transport = FakeTransport()
        headers = FakeHeaders([("Set-Cookie", "s=final-token; Path=/; HttpOnly")])
        transport.queue_response(200, envelope(200, data={}), headers=headers)
        client = eero_api.EeroClient(transport=transport)
        token = client.verify("pending-123", "123456")
        self.assertEqual(token, "final-token")
        self.assertEqual(client.token, "final-token")

    def test_verify_falls_back_to_pending_token_without_set_cookie(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        client = eero_api.EeroClient(transport=transport)
        token = client.verify("pending-123", "123456")
        self.assertEqual(token, "pending-123")

    def test_verify_sends_pending_token_as_cookie(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={}))
        client = eero_api.EeroClient(transport=transport)
        client.verify("pending-123", "123456")
        self.assertEqual(transport.calls[0]["headers"]["cookie"], "s=pending-123")

    def test_login_5xx_is_api_not_login_rejected(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(500, error="error.internal"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.login("person@example.com")
        self.assertEqual(ctx.exception.code, "api")

    def test_verify_5xx_is_api_not_login_rejected(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(503, error="error.unavailable"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.verify("pending-123", "000000")
        self.assertEqual(ctx.exception.code, "api")

    def test_login_rejected_message_never_echoes_server_error_text(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(400, error="super-secret-internal-detail"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.login("bad-identifier@example.com")
        self.assertNotIn("super-secret-internal-detail", ctx.exception.message)
        self.assertNotIn("bad-identifier@example.com", ctx.exception.message)

    def test_login_401_does_not_attempt_refresh(self):
        # nit: /2.2/login must never trigger a refresh -- there is no
        # session yet to refresh. A single call, straight to login_rejected.
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.refresh"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.login("person@example.com")
        self.assertEqual(ctx.exception.code, "login_rejected")
        self.assertEqual(transport.call_count, 1)

    def test_verify_401_does_not_attempt_refresh(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(401, error="error.session.invalid"))
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.verify("pending-123", "000000")
        self.assertEqual(ctx.exception.code, "login_rejected")
        self.assertEqual(transport.call_count, 1)

    def test_verify_rate_limited_stays_rate_limited_not_login_rejected(self):
        transport = FakeTransport()
        transport.queue_response(429, raw=b"")
        client = eero_api.EeroClient(transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.verify("pending-123", "000000")
        self.assertEqual(ctx.exception.code, "rate_limited")


class OriginSafetyTests(unittest.TestCase):
    """M1b: every outbound path must stay on https://api-user.e2ro.com."""

    def test_path_not_starting_with_slash_is_refused(self):
        transport = FakeTransport()
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", ".evil.host/2.2/networks/1")
        self.assertEqual(ctx.exception.code, "api")
        self.assertEqual(transport.call_count, 0)

    def test_host_suffix_smuggling_is_refused_before_send(self):
        # origin + ".evil.host/..." (no leading slash) would otherwise
        # concatenate into "https://api-user.e2ro.com.evil.host/...", a
        # subdomain an attacker controls, cookie included.
        transport = FakeTransport()
        client = eero_api.EeroClient(token="secret", transport=transport)
        with self.assertRaises(eero_api.ApiError):
            client.network(".evil.host/2.2/networks/1")
        self.assertEqual(transport.call_count, 0)

    def test_absolute_url_as_path_is_refused(self):
        transport = FakeTransport()
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError):
            client.request("GET", "https://evil.host/2.2/networks/1")
        self.assertEqual(transport.call_count, 0)

    def test_non_string_url_is_refused(self):
        transport = FakeTransport()
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError):
            client.request("GET", None)
        self.assertEqual(transport.call_count, 0)

    def test_ordinary_path_still_works(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(200, data={"name": "Home"}))
        client = eero_api.EeroClient(token="tok", transport=transport)
        client.request("GET", "/2.2/networks/1")
        self.assertEqual(transport.call_count, 1)


class RedirectRefusalTests(unittest.TestCase):
    """M1c: the default transport's opener must never follow a redirect."""

    def test_redirect_request_raises_instead_of_following(self):
        handler = eero_api._NoRedirectHandler()
        request = eero_api.urllib.request.Request("https://api-user.e2ro.com/2.2/account")
        with self.assertRaises(eero_api.ApiError) as ctx:
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.host/steal")
        self.assertEqual(ctx.exception.code, "api")

    def test_redirect_request_closes_the_3xx_response_before_raising(self):
        handler = eero_api._NoRedirectHandler()
        request = eero_api.urllib.request.Request("https://api-user.e2ro.com/2.2/account")
        fp = FakeResponse(302, raw=b"")
        with self.assertRaises(eero_api.ApiError):
            handler.redirect_request(request, fp, 302, "Found", {}, "https://evil.host/steal")
        self.assertTrue(fp.closed)

    def test_redirect_request_tolerates_a_close_failure(self):
        # A response whose close() itself raises must not mask the refusal
        # with an unrelated traceback.
        handler = eero_api._NoRedirectHandler()
        request = eero_api.urllib.request.Request("https://api-user.e2ro.com/2.2/account")

        class _BrokenClose:
            def close(self):
                raise OSError("already gone")

        with self.assertRaises(eero_api.ApiError):
            handler.redirect_request(request, _BrokenClose(), 302, "Found", {}, "https://evil.host/steal")

    def test_default_transport_uses_an_opener_with_the_no_redirect_handler(self):
        # The opener build_opener() constructs must have replaced the
        # stdlib's default HTTPRedirectHandler with ours, not merely added
        # ours alongside it.
        redirect_handlers = [
            h for h in eero_api._OPENER.handlers if isinstance(h, eero_api.urllib.request.HTTPRedirectHandler)
        ]
        self.assertEqual(len(redirect_handlers), 1)
        self.assertIsInstance(redirect_handlers[0], eero_api._NoRedirectHandler)


class ReadTimeErrorTests(unittest.TestCase):
    """M2: a failure while reading the body is `network`, not the generic
    unexpected-internal-error catch-all."""

    def test_incomplete_read_is_network(self):
        import http.client

        transport = FakeTransport()
        transport.queue_response(200, read_error=http.client.IncompleteRead(b"partial"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "network")

    def test_bad_status_line_is_network(self):
        import http.client

        transport = FakeTransport()
        transport.queue_response(200, read_error=http.client.BadStatusLine("garbage"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "network")

    def test_timeout_during_read_is_network(self):
        transport = FakeTransport()
        transport.queue_response(200, read_error=TimeoutError("timed out"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "network")


class NonFiniteNumberTests(unittest.TestCase):
    """MINOR-2: NaN/Infinity in an eero API response must not parse through."""

    def test_nan_in_response_is_api_error(self):
        transport = FakeTransport()
        transport.queue_response(200, raw=b'{"meta":{"code":200,"error":null},"data":{"x":NaN}}')
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "api")

    def test_infinity_in_response_is_api_error(self):
        transport = FakeTransport()
        transport.queue_response(200, raw=b'{"meta":{"code":200,"error":null},"data":{"x":Infinity}}')
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "api")


class MetaCodeQuirkTests(unittest.TestCase):
    """MINOR-5: rate limiting and auth failures can hide inside an HTTP 200,
    or an HTTP 401 with a body that isn't a proper envelope."""

    def test_meta_code_429_inside_http_200_is_rate_limited(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(429, error="error.ratelimit"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/account")
        self.assertEqual(ctx.exception.code, "rate_limited")

    def test_http_401_empty_body_refreshes_then_retries(self):
        transport = FakeTransport()
        transport.queue_response(401, raw=b"")  # bare 401, no envelope at all
        transport.queue_response(200, envelope(200, data={"user_token": "fresh"}))
        transport.queue_response(200, envelope(200, data={"name": "Home"}))
        client = eero_api.EeroClient(token="stale", transport=transport)
        result = client.request("GET", "/2.2/networks/1")
        self.assertEqual(result, {"name": "Home"})
        self.assertEqual(transport.call_count, 3)

    def test_http_401_non_envelope_body_then_refresh_failure_is_auth_required(self):
        transport = FakeTransport()
        transport.queue_response(401, raw=b"<html>not json</html>")
        transport.queue_response(401, raw=b"")  # refresh itself also bare-401s
        client = eero_api.EeroClient(token="stale", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/networks/1")
        self.assertEqual(ctx.exception.code, "auth_required")
        self.assertEqual(transport.call_count, 2)

    def test_status_carries_underlying_meta_code_for_stale_network_retry(self):
        transport = FakeTransport()
        transport.queue_response(200, envelope(404, error="error.network.notFound"))
        client = eero_api.EeroClient(token="tok", transport=transport)
        with self.assertRaises(eero_api.ApiError) as ctx:
            client.request("GET", "/2.2/networks/999")
        self.assertEqual(ctx.exception.code, "api")
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
