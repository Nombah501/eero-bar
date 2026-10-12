"""Proves the suite-wide network-forbidden guard (tests/fakes.py) is
actually installed under the exact invocation scripts/validate.sh uses
(`python3 -m unittest discover -s tests -p 'test_*.py'`), not just under
`python3 -m unittest tests.test_x` or a manual `import tests`.

This is a regression test for a real bug: tests/__init__.py used to install
the guard, but a package's __init__.py does not run under `discover -s tests`
when top-level-dir defaults to the start dir -- discovery imports test
modules by their bare name ("test_eero_api"), never "tests.test_eero_api",
so tests/__init__.py was silently skipped and the guard never activated.
"""

import http.client
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import eero_api
import fakes


class NetworkGuardIsActiveTests(unittest.TestCase):
    def test_default_transport_is_patched_to_the_guard(self):
        self.assertIs(eero_api._default_transport, fakes.NETWORK_FORBIDDEN)

    def test_default_transport_raises_instead_of_connecting(self):
        with self.assertRaises(AssertionError):
            eero_api._default_transport(None, 15)

    def test_https_connection_connect_raises_instead_of_dialing_out(self):
        # A real HTTPSConnection, never given a chance to open a socket:
        # .connect() is called by urllib internals before any bytes go out,
        # and it is exactly what's patched.
        connection = http.client.HTTPSConnection("api-user.e2ro.com")
        with self.assertRaises(AssertionError):
            connection.connect()

    def test_http_connection_connect_raises_instead_of_dialing_out(self):
        connection = http.client.HTTPConnection("api-user.e2ro.com")
        with self.assertRaises(AssertionError):
            connection.connect()


if __name__ == "__main__":
    unittest.main()
