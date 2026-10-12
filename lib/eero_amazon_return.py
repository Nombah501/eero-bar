"""Owned, bounded loopback return page. stdout is an owned private pipe only."""
import html
import http.server
import json
import secrets
import re
import signal
import sys
import time
import urllib.parse

MAX_BODY = 65536
MAX_URL = 16384


def callback_code(value):
    if not isinstance(value, str) or not value or len(value) > MAX_URL or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError("Invalid return")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value) or "\\" in value:
        raise ValueError("Invalid encoding")
    url = urllib.parse.urlsplit(value)
    if url.scheme != "https" or url.netloc != "www.amazon.com" or url.path != "/ap/maplanding" or "#" in value:
        raise ValueError("Invalid return")
    query = urllib.parse.parse_qs(url.query, keep_blank_values=True, max_num_fields=64, errors="strict")
    codes = query.get("openid.oa2.authorization_code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in codes[0]):
        raise ValueError("Invalid return")
    return codes[0]


def serve(config, output):
    portal = config["portal"]
    deadline = config["deadline"]
    if not isinstance(portal, str) or not portal.startswith("https://www.amazon.com/ap/signin?") or len(portal) > MAX_URL:
        raise ValueError("Invalid configuration")
    if not isinstance(deadline, (int, float)) or not time.monotonic() < deadline <= time.monotonic() + 601:
        raise ValueError("Invalid deadline")
    nonce = secrets.token_urlsafe(32)
    finished = False

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *unused):
            pass

        def setup(self):
            super().setup()
            signal.setitimer(signal.ITIMER_REAL, max(0.001, min(3, deadline - time.monotonic())))
            self.connection.settimeout(max(0.001, min(2, deadline - time.monotonic())))

        def send_error(self, code, message=None, explain=None):
            self.reply(code, "Request rejected.")

        def reply(self, status, text):
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def valid_host(self):
            return self.headers.get_all("Host") == [host] and time.monotonic() < deadline and not finished

        def do_GET(self):
            if not self.valid_host() or self.path != "/":
                self.reply(400, "Request rejected.")
                return
            self.reply(200, '<!doctype html><html lang="en"><meta charset="utf-8"><title>eero Amazon return</title><h1>Amazon sign-in</h1>'
                '<p><a target="_blank" rel="noopener noreferrer" href="' + html.escape(portal, quote=True) + '">Open Amazon sign-in in a new tab</a></p>'
                '<p>Enter password and MFA only on Amazon. After finishing, copy the entire returned Amazon address and paste it below. Return to this tab if Amazon shows an error or blank page.</p>'
                '<p>Loopback must reach this machine directly, bypassing any browser proxy. Extensions, browser history/sync and clipboard managers may retain the returned address. You may use your preferred private mode; Helper never reads or changes browser profiles, proxy settings or clipboard.</p>'
                '<form method="post" action="/return" autocomplete="off"><input type="hidden" name="nonce" value="' + nonce + '">'
                '<label>Entire returned Amazon address <input type="password" name="callback" autocomplete="off" spellcheck="false" required maxlength="16384"></label>'
                '<button type="submit">Return sign-in for verification</button></form><p>This transfer does not save a Session. Approve Save new Session separately in the native popup. Cancel there; the attempt expires after ten minutes. Closing browser tabs does not cancel.</p></html>')

        def do_POST(self):
            nonlocal finished
            if (not self.valid_host() or self.path != "/return" or self.headers.get_all("Origin") != [origin]
                    or self.headers.get_all("Content-Type") != ["application/x-www-form-urlencoded"]
                    or self.headers.get("Transfer-Encoding") is not None):
                self.reply(400, "Request rejected.")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BODY:
                self.reply(400, "Request rejected.")
                return
            try:
                length = int(lengths[0])
                raw = self.rfile.read(length)
                if len(raw) != length or time.monotonic() >= deadline:
                    raise ValueError("Invalid body")
                encoded = raw.decode("utf-8", "strict")
                if re.search(r"%(?![0-9A-Fa-f]{2})", encoded):
                    raise ValueError("Invalid encoding")
                fields = urllib.parse.parse_qs(encoded, keep_blank_values=True, strict_parsing=True, max_num_fields=3, errors="strict")
                if set(fields) != {"nonce", "callback"} or any(len(v) != 1 for v in fields.values()):
                    raise ValueError("Invalid fields")
                if not secrets.compare_digest(fields["nonce"][0], nonce):
                    raise ValueError("Invalid nonce")
                value = fields["callback"][0]
                callback_code(value)
            except (ValueError, TypeError, UnicodeError, OSError):
                self.reply(400, "Request rejected. Use the entire returned Amazon address.")
                return
            finished = True
            self.reply(200, "Sign-in returned for verification. Return to the native popup for separate Session save consent. You may close this tab.")
            output.write(json.dumps({"callback": value}) + "\n")
            output.flush()

    class Server(http.server.HTTPServer):
        def handle_error(self, request, client_address):
            pass

    with Server(("127.0.0.1", 0), Handler) as server:
        host = "127.0.0.1:" + str(server.server_port)
        origin = "http://" + host
        server.timeout = 0.1
        output.write(json.dumps({"url": origin + "/"}) + "\n")
        output.flush()
        while not finished and time.monotonic() < deadline:
            server.handle_request()

            signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.monotonic()))

def main():
    # Bound the entire child, including slow HTTP headers/body reads.
    def timeout(*unused):
        raise TimeoutError("Return deadline")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(602)
    raw = sys.stdin.buffer.readline(MAX_BODY + 1)
    if len(raw) > MAX_BODY or not raw.endswith(b"\n"):
        return 1
    config = json.loads(raw)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, config["deadline"] - time.monotonic()))
    serve(config, sys.stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
