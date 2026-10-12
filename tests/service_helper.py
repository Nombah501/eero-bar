"""No-network subprocess fixture for actual Service lifecycle regressions."""
import atexit
import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ["EERO_SERVICE_TEST_STATE"])
root.mkdir(parents=True, exist_ok=True)
command = sys.argv[1]
log = root / "commands.jsonl"

def record(event):
    with log.open("a") as stream:
        stream.write(json.dumps(event) + "\n")

def emit(payload):
    print(json.dumps(payload), flush=True)

def read_state():
    path = root / "session.json"
    return json.loads(path.read_text()) if path.exists() else {"token": "old", "pending": None, "guest": False}

def save(state):
    (root / "session.json").write_text(json.dumps(state))

record({"command": command, "event": "start"})
atexit.register(record, {"command": command, "event": "exit"})
state = read_state()
if command == "status":
    time.sleep(0.20)
    if not state.get("token"):
        emit({"ok": False, "type": "error", "code": "auth_required", "message": "No synthetic Session"})
        sys.exit(1)
    current = read_state()
    current["token"] = state["token"] + "-status-refreshed"
    current["cache"] = "refreshed-status"
    save(current)
    state = current
    record({"command": command, "event": "late-write", "token": state["token"]})
    emit({"ok": True, "type": "snapshot", "fetchedAt": "2026-10-10T10:00:00Z",
          "network": {"url": "/2.2/networks/1", "name": "Synthetic", "status": "online", "internetOnline": True,
                      "guestEnabled": state.get("guest", False),
                      "speed": {"down": 100, "up": 20, "units": "Mbps", "date": "2026-10-10T09:00:00Z"}},
          "nodes": [{"url": "/2.2/eeros/1", "name": "Synthetic Gateway", "gateway": True, "ledOn": state.get("led", False)},
                    {"url": "/2.2/eeros/2", "name": "Unknown LED", "ledOn": None}],
          "clients": [{"mac": "aa:bb:cc:dd:ee:ff", "paused": state.get("pause-client", False)}],
          "profiles": [{"url": "/2.2/networks/1/profiles/1", "paused": state.get("pause-profile", False)}]})
elif command == "login":
    identifier = sys.stdin.readline().strip()
    record({"command": command, "event": "identifier", "identifier": identifier})
    time.sleep(0.12)
    state = read_state()
    state["pending"] = identifier
    save(state)
    record({"command": command, "event": "preserved", "token": state["token"]})
    emit({"ok": True, "type": "code_sent", "identifier": identifier})
elif command == "verify":
    sys.stdin.readline()
    time.sleep(0.12)
    emit({"ok": True, "type": "verification_ready"})
    if sys.stdin.readline() != "commit\n":
        record({"command": command, "event": "cancelled"})
        emit({"ok": False, "type": "error", "code": "login_cancelled", "message": "Synthetic cancellation"})
        sys.exit(1)
    record({"command": command, "event": "approved"})
    time.sleep(0.25)
    state = read_state()
    state["token"] = state["pending"]
    state["pending"] = None
    save(state)
    record({"command": command, "event": "saved"})
    emit({"ok": True, "type": "logged_in"})
elif command == "amazon-login":
    emit({"ok": True, "type": "amazon_progress", "phase": "browser_open"})
    time.sleep(0.12)
    emit({"ok": True, "type": "amazon_progress", "phase": "exchanging"})
    emit({"ok": True, "type": "amazon_progress", "phase": "verification_ready"})
    if sys.stdin.readline() != "commit\n":
        record({"command": command, "event": "cancelled"})
        emit({"ok": False, "type": "error", "code": "login_cancelled", "message": "Synthetic cancellation"})
        sys.exit(1)
    record({"command": command, "event": "approved"})
    time.sleep(0.25)
    state = read_state()
    state["token"] = "synthetic-amazon-eero"
    state["pending"] = None
    save(state)
    record({"command": command, "event": "saved"})
    emit({"ok": True, "type": "logged_in"})
elif command == "logout":
    time.sleep(0.12)
    save({"token": None, "pending": None, "guest": False})
    record({"command": command, "event": "deleted"})
    emit({"ok": True, "type": "logged_out"})
elif command == "guest":
    if sys.argv[2] == "off":
        emit({"ok": False, "type": "error", "code": "api", "message": "Synthetic Guest failure"})
        sys.exit(1)
    state["guest"] = True
    save(state)
    emit({"ok": True, "type": "done", "action": command})
elif command == "led":
    time.sleep(0.08)
    if sys.argv[3] == "off":
        emit({"ok": False, "type": "error", "code": "api", "message": "Synthetic LED failure"})
        sys.exit(1)
    state["led"] = True
    save(state)
    emit({"ok": True, "type": "done", "action": command})
elif command == "reboot-node":
    time.sleep(0.08)
    emit({"ok": True, "type": "done", "action": command})
elif command in ("pause-client", "pause-profile"):
    time.sleep(0.08)
    if sys.argv[3] == "off":
        emit({"ok": False, "type": "error", "code": "api", "message": "Synthetic Pause failure"})
        sys.exit(1)
    state = read_state()
    state[command] = True
    save(state)
    emit({"ok": True, "type": "done", "action": command})
elif command == "speedtest":
    emit({"ok": True, "type": "done", "action": command})
elif command == "reboot-network":
    time.sleep(0.35)
    current = read_state()
    current["token"] = state["token"] + "-refreshed"
    current["cache"] = "refreshed-action"
    save(current)
    state = current
    record({"command": command, "event": "late-write", "token": state["token"]})
    emit({"ok": True, "type": "done", "action": command})
else:
    raise SystemExit("Unexpected synthetic command")
