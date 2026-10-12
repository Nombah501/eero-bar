"""Exercise the real Service public API and subprocess lifecycle, without HTTP."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServiceLifecycleTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("quickshell") and Path("/usr/share/omarchy/shell/Ui").is_dir(),
                         "Quickshell and host UI sources are required")
    def test_native_page_details_and_control_navigation(self):
        with tempfile.TemporaryDirectory(prefix="eero-native-pages-") as directory:
            harness = Path(directory)
            for name in ("Commons", "Ui"):
                (harness / name).symlink_to(Path("/usr/share/omarchy/shell") / name, target_is_directory=True)
            suite_url = (ROOT / "tests/native_pages.qml").as_uri()
            # Load the suite by absolute URL so its ../Navigation.js import
            # retains the real source base. Run after configuration startup.
            (harness / "shell.qml").write_text(
                "import QtQuick\nimport Quickshell\n"
                "ShellRoot { id: runner; property var suite: null\n"
                " Timer { interval: 10; running: true; onTriggered: {\n"
                f"  var component = Qt.createComponent({json.dumps(suite_url)})\n"
                "  if (component.status !== Component.Ready) {\n"
                "   console.error(component.errorString()); Qt.quit(); return\n"
                "  }\n"
                "  runner.suite = component.createObject(runner)\n"
                " } }\n}\n"
            )
            environment = os.environ.copy()
            environment.update({
                "QT_QPA_PLATFORM": "offscreen", "QS_DISABLE_FILE_WATCHER": "1",
                "QS_NO_RELOAD_POPUP": "1", "XDG_STATE_HOME": str(harness / "state"),
                "XDG_CACHE_HOME": str(harness / "cache"), "XDG_DATA_HOME": str(harness / "data"),
                "EERO_CLIENTS_PAGE_URL": (ROOT / "ClientsPage.qml").as_uri(),
            })
            result = subprocess.run([shutil.which("quickshell"), "-n", "-p", str(harness)],
                                    env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: native page behavior", result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("quickshell"), "Quickshell is not installed")
    def test_cancel_commit_logout_and_action_readback(self):
        with tempfile.TemporaryDirectory(prefix="eero-service-test-") as directory:
            harness = Path(directory)
            (harness / "bin").mkdir()
            service_source = Path(os.environ.get("EERO_SERVICE_SOURCE", ROOT / "Service.qml"))
            (harness / "Service.qml").symlink_to(service_source)
            (harness / "EeroModel.js").symlink_to(ROOT / "EeroModel.js")
            (harness / "shell.qml").symlink_to(ROOT / "tests/service_lifecycle.qml")
            host_shell = Path("/usr/share/omarchy/shell")
            if host_shell.is_dir():
                for name in ("Commons", "Ui"):
                    (harness / name).symlink_to(host_shell / name, target_is_directory=True)
                for name in ("ClientsPage.qml", "ClientRow.qml"):
                    (harness / name).symlink_to(ROOT / name)
            helper = harness / "bin/eero-helper"
            helper.symlink_to(ROOT / "tests/service_helper.py")
            # A hard no-network guard applies even if URL resolution unexpectedly
            # selects the production helper during Service's initial status call.
            (harness / "sitecustomize.py").write_text(
                "import socket\n"
                "def deny(*args, **kwargs):\n"
                "    raise RuntimeError('HTTP forbidden in Service regression')\n"
                "socket.create_connection = deny\n"
                "socket.socket.connect = deny\n"
            )
            environment = os.environ.copy()
            environment.update({
                "QT_QPA_PLATFORM": "offscreen",
                "QS_DISABLE_FILE_WATCHER": "1",
                "QS_NO_RELOAD_POPUP": "1",
                "XDG_STATE_HOME": str(harness / "state"),
                "XDG_CACHE_HOME": str(harness / "cache"),
                "XDG_DATA_HOME": str(harness / "data"),
                "EERO_BAR_STATE_DIR": str(harness / "production-state-must-stay-empty"),
                "EERO_SERVICE_TEST_STATE": str(harness / "fixture-state"),
                "EERO_SERVICE_URL": (harness / "Service.qml").as_uri(),
                "EERO_TEST_HELPER_PATH": str(helper),
                "EERO_CLIENTS_PAGE_URL": (harness / "ClientsPage.qml").as_uri() if host_shell.is_dir() else "",
                "PYTHONPATH": str(harness),
            })
            result = subprocess.run(
                [shutil.which("quickshell"), "-n", "-p", str(harness)],
                env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: actual Service lifecycle and readback", result.stdout + result.stderr)
            events = [json.loads(line) for line in (harness / "fixture-state/commands.jsonl").read_text().splitlines()]
            verifications = [event["event"] for event in events if event["command"] == "verify"]
            self.assertEqual(verifications.count("cancelled"), 1)
            self.assertEqual(verifications.count("saved"), 5)
            identifiers = [event["identifier"] for event in events if event["event"] == "identifier"]
            self.assertNotIn("cancelled-send-queue@example.invalid", identifiers,
                             "a cancelled queued send must not reach a helper")
            self.assertNotIn("cancelled-approved-queue@example.invalid", identifiers,
                             "cancelling a queue must not undo or dispatch its approved predecessor")
            self.assertNotIn("cancelled-writer-queue@example.invalid", identifiers)
            self.assertNotIn("must-not-bypass-reservation@example.invalid", identifiers,
                             "an approved predecessor cannot release a queued successor reservation")
            session = json.loads((harness / "fixture-state/session.json").read_text())
            self.assertTrue(session["token"].startswith("reservation-predecessor@example.invalid"),
                            "cancelled successor preserves the verified predecessor Session")
            self.assertIsNone(session["pending"])
            first_saved = next(i for i, event in enumerate(events) if event == {"command": "verify", "event": "saved"})
            deleted = next(i for i, event in enumerate(events) if event == {"command": "logout", "event": "deleted"})
            self.assertLess(first_saved, deleted, "logout must run after granted verify has finished saving")
            self.assertEqual(sum(event == {"command": "guest", "event": "start"} for event in events), 2)
            self.assertEqual(sum(event == {"command": "speedtest", "event": "start"} for event in events), 1)
            self.assertEqual(sum(event == {"command": "led", "event": "start"} for event in events), 2)
            self.assertEqual(sum(event == {"command": "reboot-node", "event": "start"} for event in events), 1)
            self.assertEqual(sum(event == {"command": "reboot-network", "event": "start"} for event in events), 4)
            self.assertEqual(sum(event == {"command": "pause-client", "event": "start"} for event in events), 2)
            self.assertEqual(sum(event == {"command": "pause-profile", "event": "start"} for event in events), 2)
            amazon_events = [event["event"] for event in events if event["command"] == "amazon-login"]
            self.assertEqual(amazon_events.count("cancelled"), 2)
            self.assertEqual(amazon_events.count("saved"), 3)
            identities = {"login", "verify", "logout", "amazon-login"}
            active = set()
            concurrent_reads_actions = False
            for event in events:
                command = event["command"]
                if event["event"] == "start":
                    if command in identities:
                        self.assertFalse(active, "identity transition must wait for every owned writer")
                    else:
                        self.assertFalse(active & identities, "reserved identity transition blocks new writers")
                    active.add(command)
                    concurrent_reads_actions |= {"status", "reboot-network"} <= active
                elif event["event"] == "exit":
                    active.discard(command)
            self.assertTrue(concurrent_reads_actions, "ordinary status and different-key Action still overlap")
            late_actions = [i for i, event in enumerate(events)
                            if event["command"] == "reboot-network" and event["event"] == "late-write"]
            self.assertEqual(len(late_actions), 4, "reserved transitions must not dispatch extra Actions")
            self.assertTrue(all(events[i]["token"].endswith("-refreshed") for i in late_actions))
            last_deleted = max(i for i, event in enumerate(events) if event["event"] == "deleted")
            self.assertLess(late_actions[0], last_deleted, "logout deletes after late token/cache persistence")
            self.assertEqual(session["cache"], "refreshed-status")
            late_statuses = [event for event in events
                             if event["command"] == "status" and event["event"] == "late-write"]
            self.assertTrue(late_statuses)
            self.assertTrue(all(event["token"].endswith("-status-refreshed") for event in late_statuses))
