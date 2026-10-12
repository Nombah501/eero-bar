# eero-bar: glossary

Project terminology for code, documentation and tests. Use these names consistently.

- **Network** — an eero network in the account. The active Network is the first returned by `/2.2/account`.
- **Node** — a physical eero unit, called `eero` in the API. The `gateway` field identifies the **Gateway node**; other Nodes are leaves.
- **Client** — a device connected to the Network, called `device` in the API. A valid MAC identifies it for Actions.
- **Profile** — an eero group of Clients that can be paused together.
- **Pause** — suspend internet access for a Client or Profile (`paused: true`), not Wi-Fi association.
- **Guest network** — the Network's guest Wi-Fi.
- **LED** — a Node's indicator (`led_on`).
- **Snapshot** — normalized Network, Nodes, Clients and Profiles returned by Helper to QML as one JSON line.
- **Helper** (`bin/eero-helper`) — a Python standard-library process, the only component that calls the eero API or accesses Session.
- **Session** — `$XDG_STATE_HOME/n501.eero-bar/session.json` (0600), containing the live session token and, during OTP login, `pendingUserToken` (received as the upstream `user_token` field).
- **Login flow** — OTP uses Identifier (email or phone) → Verification code, both sent through private stdin, never argv. Amazon uses the default browser and manual local return, followed by separate Session save consent.
- **Service** (`Service.qml`) — the long-lived QML plugin object that invokes Helper, holds Snapshot and polls the Network.
- **Widget / Popup** (`Panel.qml`) — the bar icon and native control panel.
- **Action** — a mutating command: reboot, Guest, LED, Pause or Speedtest. Reboot always requires confirmation.
