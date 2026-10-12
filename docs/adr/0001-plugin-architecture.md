# ADR-0001: n501.eero-bar plugin architecture

Status: accepted, 2026-09-22; updated for the Amazon acquisition boundary.

## Context

The plugin uses the mobile application's unofficial control API at `https://api-user.e2ro.com`, documented by open-source clients (see [API reference](../eero-api.md)). The official EU Data Act read-only portability API is a separate interface, not the interface for these Actions. The tested host baseline is Omarchy 4.0.4 with `omarchy-shell` (Quickshell); plugins execute without a sandbox. This baseline is not a universal compatibility range.

## Decision

1. One plugin, `n501.eero-bar`, with the repository root as the plugin root: `kinds: ["service","bar-widget"]`, `keepLoaded: true`, and entry points `service: Service.qml`, `barWidget: Panel.qml`.
2. The Popup uses `qs.Ui.KeyboardPanel` so `TextField` receives keyboard input. It uses the host's native `qs.Commons`/`qs.Ui` API, QtQuick controls/layouts, Quickshell process APIs and local model/page modules.
3. Network requests and token handling live in `bin/eero-helper` (Python 3, standard library only). Each command launches a one-shot Helper and returns one terminal JSON line. `verify` additionally emits safe progress and requires private stdin approval under [ADR-0002](0002-otp-session-commit.md). A persistent worker is unnecessary for the default 60-second poll and occasional Actions.
4. Secrets (Identifier and Verification code) travel through stdin. Tokens never appear on public stdout or in QML.
5. Session is stored in `$XDG_STATE_HOME/n501.eero-bar/session.json` (default state root `~/.local/state`): file mode 0600, directory mode 0700, atomic writes and refusal to use symlinks.
6. Helper normalizes API responses into Snapshot. QML does not parse raw eero JSON.
7. Action arguments from QML are checked with strict resource URL, MAC and on/off validators before HTTP requests.
8. Polling defaults to 60 seconds and is configurable from 30 to 3600 seconds. Actions trigger refresh. The unofficial API's approximate rate limit is not a guaranteed service contract.
9. OTP verification and Session persistence are separated by private stdin approval; cancelled attempts receive no write approval. Session writer ordering and the linearization point are specified in [ADR-0002](0002-otp-session-commit.md).
10. Amazon acquisition uses a bounded Helper-owned standard-library loopback receiver child, not a persistent worker. The default browser is launched through Omarchy's supported launcher or `xdg-open`; private pipes carry return artifacts, and QML sees only safe phases. See [ADR-0003](0003-amazon-private-browser.md).

## Consequences

- The unofficial API can change without notice. Fields are read defensively; an unsupported response shape produces an `api` error rather than a crash.
- Amazon login now uses the default browser and a local manual-return page. Local transport validation and independent static security review passed; fresh authorized human login and re-login remain unproven. A historical default-US MAP exchange and an existing working Session do not prove those flows. OTP is not promised for an Amazon account merely because it has an email address.
- Each command starts Python rather than retaining a daemon. Startup overhead is accepted for this polling cadence; no portable startup-time guarantee is claimed.
