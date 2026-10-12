"""Coordinator-only default-browser local handoff; no Amazon/API/Session access.

Run: python3 -B scripts/amazon-return-smoke.py
Do NOT click the Amazon link. Paste the printed fixed synthetic address into the
masked local page and submit. Only acquisition is exercised, no exchange/save.
Cancel with Ctrl-C. Browser tabs remain user-owned. This is not human auth proof.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from eero_amazon import Attempt, Browser, Control, CALLBACK


def main():
    read_fd, write_fd = os.pipe()
    try:
        print("Local-only smoke. Do NOT click Amazon. Paste this synthetic address:", flush=True)
        print(CALLBACK + "?openid.oa2.authorization_code=synthetic-smoke", flush=True)
        with os.fdopen(read_fd, "rb", buffering=0) as stream:
            with Browser(os.environ.copy()) as receiver:
                value = receiver.acquire(Attempt(), Control(stream))
                if value != "synthetic-smoke":
                    raise ValueError()
        print("PASS: synthetic default-browser handoff and owned receiver cleanup; no exchange or Session access")
        return 0
    except (Exception, KeyboardInterrupt):
        print("FAIL: synthetic return cancelled or transport unavailable")
        return 1
    finally:
        os.close(write_fd)


if __name__ == "__main__":
    sys.exit(main())
