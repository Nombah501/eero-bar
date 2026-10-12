import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import eero_snapshot
import fakes  # noqa: F401  (installs the suite-wide network-forbidden guard)


def snap(**overrides):
    kwargs = dict(
        network_raw={"name": "Home"},
        nodes_raw=[],
        clients_raw=[],
        profiles_raw=[],
        network_url="/2.2/networks/123",
        fetched_at="2026-09-22T12:00:00Z",
    )
    kwargs.update(overrides)
    return eero_snapshot.build_snapshot(**kwargs)


class NetworkTests(unittest.TestCase):
    def test_full_network_fields(self):
        raw = {
            "name": "Home",
            "health": {"internet": {"status": "green"}, "eero_network": {"status": "green"}},
            "speed": {"down": {"value": 312.4, "units": "Mbps"}, "up": {"value": 41.0, "units": "Mbps"}, "date": "2026-09-22T11:00:00Z"},
            "guest_network": {"enabled": True, "name": "Home Guest"},
            "premium_status": "premium_enabled",
        }
        result = snap(network_raw=raw)["network"]
        self.assertEqual(result["id"], "123")
        self.assertEqual(result["url"], "/2.2/networks/123")
        self.assertEqual(result["name"], "Home")
        self.assertEqual(result["status"], "online")
        self.assertTrue(result["internetOnline"])
        self.assertTrue(result["guestEnabled"])
        self.assertEqual(result["guestName"], "Home Guest")
        self.assertEqual(result["speed"], {"down": 312.4, "up": 41.0, "units": "Mbps", "date": "2026-09-22T11:00:00Z"})
        self.assertTrue(result["premium"])

    def test_missing_network_fields_are_null_not_absent(self):
        result = snap(network_raw={})["network"]
        self.assertEqual(
            set(result.keys()),
            {"id", "url", "name", "status", "internetOnline", "guestEnabled", "guestName", "speed", "premium"},
        )
        self.assertIsNone(result["name"])
        self.assertIsNone(result["guestEnabled"])
        self.assertIsNone(result["premium"])
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["internetOnline"])
        self.assertEqual(result["speed"], {"down": None, "up": None, "units": None, "date": None})

    def test_network_raw_not_a_dict_does_not_crash(self):
        result = snap(network_raw=None)["network"]
        self.assertIsNone(result["name"])
        self.assertEqual(result["status"], "unknown")

    def test_bad_internet_health_is_offline_regardless_of_top_status(self):
        raw = {"status": "connected", "health": {"internet": {"status": "red"}}}
        result = snap(network_raw=raw)["network"]
        self.assertEqual(result["status"], "offline")
        self.assertFalse(result["internetOnline"])

    def test_degraded_when_mesh_is_bad_but_internet_is_good(self):
        raw = {"health": {"internet": {"status": "green"}, "eero_network": {"status": "red"}}}
        result = snap(network_raw=raw)["network"]
        self.assertEqual(result["status"], "degraded")

    def test_unrecognized_status_words_are_unknown_not_online(self):
        raw = {"status": "some_future_value", "health": {"internet": {"status": "some_future_value"}}}
        result = snap(network_raw=raw)["network"]
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["internetOnline"])

    def test_network_id_null_when_url_does_not_match_pattern(self):
        result = snap(network_url="/not/a/network/url")["network"]
        self.assertIsNone(result["id"])
        self.assertEqual(result["url"], "/not/a/network/url")

    def test_premium_negative_words_are_false_not_true(self):
        # M3: "inactive" contains the substring "active" -- must not match
        # the positive branch. Same for "not_active"/"not_enabled".
        for value in ("inactive", "not_active", "not_enabled", "disabled", "premium_disabled", "not_premium"):
            with self.subTest(value=value):
                result = snap(network_raw={"premium_status": value})["network"]
                self.assertFalse(result["premium"], f"{value!r} should map to premium: false")

    def test_premium_positive_words_still_true(self):
        for value in ("premium_enabled", "eero_plus_active", "true"):
            with self.subTest(value=value):
                result = snap(network_raw={"premium_status": value})["network"]
                self.assertTrue(result["premium"], f"{value!r} should map to premium: true")

    def test_premium_bool_passthrough(self):
        self.assertTrue(snap(network_raw={"premium_status": True})["network"]["premium"])
        self.assertFalse(snap(network_raw={"premium_status": False})["network"]["premium"])


class NodeTests(unittest.TestCase):
    def test_full_node_fields_and_name_fallback_to_location(self):
        raw = {
            "url": "/2.2/eeros/456",
            "serial": "SN1",
            "model": "eero Pro 6",
            "location": "Living Room",
            "status": "green",
            "gateway": True,
            "connected_clients_count": 12,
            "led_on": True,
            "mesh_quality_bars": 5,
            "ip_address": "192.168.4.1",
            "os_version": "v7.x",
            "update_available": False,
            "wired": False,
        }
        result = snap(nodes_raw=[raw])["nodes"][0]
        self.assertEqual(
            result,
            {
                "id": "456",
                "url": "/2.2/eeros/456",
                "name": "Living Room",
                "model": "eero Pro 6",
                "gateway": True,
                "online": True,
                "clients": 12,
                "ledOn": True,
                "meshBars": 5,
                "ip": "192.168.4.1",
                "firmware": "v7.x",
                "updateAvailable": False,
                "wired": False,
            },
        )

    def test_name_falls_back_model_then_serial(self):
        no_location = snap(nodes_raw=[{"url": "/2.2/eeros/1", "model": "eero 6", "serial": "SN2"}])["nodes"][0]
        self.assertEqual(no_location["name"], "eero 6")

        only_serial = snap(nodes_raw=[{"url": "/2.2/eeros/2", "serial": "SN3"}])["nodes"][0]
        self.assertEqual(only_serial["name"], "SN3")

        nothing = snap(nodes_raw=[{"url": "/2.2/eeros/3"}])["nodes"][0]
        self.assertIsNone(nothing["name"])

    def test_missing_node_fields_are_null(self):
        result = snap(nodes_raw=[{}])["nodes"][0]
        self.assertIsNone(result["id"])
        self.assertIsNone(result["url"])
        self.assertIsNone(result["name"])
        self.assertIsNone(result["gateway"])
        self.assertIsNone(result["online"])
        self.assertIsNone(result["clients"])

    def test_unicode_digit_node_url_drops_to_null(self):
        # MINOR-1: \d matches Unicode decimal digits, not just ASCII 0-9.
        result = snap(nodes_raw=[{"url": "/2.2/eeros/١٢٣"}])["nodes"][0]
        self.assertIsNone(result["id"])
        self.assertIsNone(result["url"])

    def test_malformed_node_url_drops_to_null_id_and_url(self):
        # M1b: QML sends this url straight back to reboot-node/led. A url
        # that doesn't match /2.2/eeros/<digits> -- e.g. an absolute or
        # off-origin url smuggled in through a compromised/broken API
        # response -- must never survive into the snapshot.
        result = snap(nodes_raw=[{"url": "https://evil.host/2.2/eeros/456", "location": "Kitchen"}])["nodes"][0]
        self.assertIsNone(result["id"])
        self.assertIsNone(result["url"])
        self.assertEqual(result["name"], "Kitchen")  # other fields still populate

    def test_node_online_mapping_is_defensive(self):
        def online_for(status):
            return snap(nodes_raw=[{"status": status}])["nodes"][0]["online"]

        self.assertTrue(online_for("green"))
        self.assertTrue(online_for("connected"))
        self.assertFalse(online_for("red"))
        self.assertFalse(online_for("disconnected"))
        self.assertTrue(online_for("yellow"))  # warn: reachable, just weak mesh
        self.assertIsNone(online_for("some_new_value"))  # unrecognized: unknown, not false
        self.assertIsNone(online_for(None))

    def test_nodes_sorted_gateway_first_then_name_case_insensitive(self):
        nodes_raw = [
            {"url": "/2.2/eeros/1", "location": "zeta", "gateway": False},
            {"url": "/2.2/eeros/2", "location": "Alpha", "gateway": False},
            {"url": "/2.2/eeros/3", "location": "Beta", "gateway": True},
        ]
        names = [node["name"] for node in snap(nodes_raw=nodes_raw)["nodes"]]
        self.assertEqual(names, ["Beta", "Alpha", "zeta"])


class ClientTests(unittest.TestCase):
    def test_full_client_fields(self):
        raw = {
            "mac": "AA:BB:CC:DD:EE:FF",
            "nickname": "Pixel",
            "hostname": "pixel-7",
            "ip": "192.168.4.30",
            "connected": True,
            "wireless": True,
            "paused": False,
            "is_guest": False,
            "source": {"location": "Living Room"},
            "profile": {"name": "Kids"},
        }
        result = snap(clients_raw=[raw])["clients"][0]
        self.assertEqual(
            result,
            {
                "mac": "aa:bb:cc:dd:ee:ff",
                "name": "Pixel",
                "ip": "192.168.4.30",
                "connected": True,
                "wireless": True,
                "paused": False,
                "guest": False,
                "nodeName": "Living Room",
                "profileName": "Kids",
            },
        )

    def test_name_falls_back_nickname_hostname_mac(self):
        no_nickname = snap(clients_raw=[{"mac": "aa:bb:cc:dd:ee:ff", "hostname": "host1"}])["clients"][0]
        self.assertEqual(no_nickname["name"], "host1")

        only_mac = snap(clients_raw=[{"mac": "aa:bb:cc:dd:ee:ff"}])["clients"][0]
        self.assertEqual(only_mac["name"], "aa:bb:cc:dd:ee:ff")

    def test_malformed_mac_is_null_field_but_still_usable_as_display_name(self):
        result = snap(clients_raw=[{"mac": "not-a-real-mac"}])["clients"][0]
        self.assertIsNone(result["mac"])
        self.assertEqual(result["name"], "not-a-real-mac")

    def test_uppercase_mac_is_lowercased(self):
        result = snap(clients_raw=[{"mac": "AA:BB:CC:DD:EE:FF"}])["clients"][0]
        self.assertEqual(result["mac"], "aa:bb:cc:dd:ee:ff")

    def test_missing_profile_name_is_null(self):
        result = snap(clients_raw=[{"mac": "aa:bb:cc:dd:ee:ff", "profile": None}])["clients"][0]
        self.assertIsNone(result["profileName"])
        result2 = snap(clients_raw=[{"mac": "aa:bb:cc:dd:ee:ff"}])["clients"][0]
        self.assertIsNone(result2["profileName"])

    def test_clients_sorted_connected_first_then_name_case_insensitive(self):
        clients_raw = [
            {"mac": "1", "nickname": "zeta", "connected": True},
            {"mac": "2", "nickname": "Alpha", "connected": False},
            {"mac": "3", "nickname": "Beta", "connected": True},
        ]
        names = [client["name"] for client in snap(clients_raw=clients_raw)["clients"]]
        self.assertEqual(names, ["Beta", "zeta", "Alpha"])


class ProfileTests(unittest.TestCase):
    def test_full_profile_fields(self):
        raw = {"url": "/2.2/networks/123/profiles/789", "name": "Kids", "paused": False, "devices": [1, 2, 3]}
        result = snap(profiles_raw=[raw])["profiles"][0]
        self.assertEqual(
            result,
            {"id": "789", "url": "/2.2/networks/123/profiles/789", "name": "Kids", "paused": False, "clientCount": 3},
        )

    def test_malformed_profile_url_drops_to_null_id_and_url(self):
        result = snap(profiles_raw=[{"url": ".evil.host/2.2/networks/1/profiles/9", "name": "Kids"}])["profiles"][0]
        self.assertIsNone(result["id"])
        self.assertIsNone(result["url"])
        self.assertEqual(result["name"], "Kids")

    def test_missing_devices_is_null_count_not_zero(self):
        result = snap(profiles_raw=[{"name": "Kids"}])["profiles"][0]
        self.assertIsNone(result["clientCount"])

    def test_empty_devices_list_is_zero_count(self):
        result = snap(profiles_raw=[{"name": "Kids", "devices": []}])["profiles"][0]
        self.assertEqual(result["clientCount"], 0)

    def test_profiles_sorted_by_name_case_insensitive(self):
        profiles_raw = [{"name": "zeta"}, {"name": "Alpha"}, {"name": "Beta"}]
        names = [profile["name"] for profile in snap(profiles_raw=profiles_raw)["profiles"]]
        self.assertEqual(names, ["Alpha", "Beta", "zeta"])


class EnvelopeTests(unittest.TestCase):
    def test_top_level_shape(self):
        result = snap()
        self.assertTrue(result["ok"])
        self.assertEqual(result["type"], "snapshot")
        self.assertEqual(result["fetchedAt"], "2026-09-22T12:00:00Z")
        self.assertIn("network", result)
        self.assertIn("nodes", result)
        self.assertIn("clients", result)
        self.assertIn("profiles", result)

    def test_non_list_raw_collections_do_not_crash(self):
        result = eero_snapshot.build_snapshot(
            network_raw={},
            nodes_raw=None,
            clients_raw="not a list",
            profiles_raw={"unexpected": "shape"},
            network_url="/2.2/networks/1",
            fetched_at="2026-09-22T12:00:00Z",
        )
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["clients"], [])
        self.assertEqual(result["profiles"], [])


if __name__ == "__main__":
    unittest.main()
