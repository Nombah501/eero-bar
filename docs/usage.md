# Usage guide

[README](../README.md) · [Русский](usage.ru.md)

## Sign-in

### Existing eero email/phone account (OTP)

1. Open the eero bar panel and enter the email or phone number used for an existing eero login. Use international `+E.164` format for a phone number.
2. Choose **Send code**, enter the received verification code, then **Verify**.
3. Successful verification is approved by the current panel flow and saved locally; there is no separate OTP Save button. Pending cancellation or closing the popup before approval preserves the previous Session. Once approval has been granted, closing the popup does not undo the committed login.

OTP and Amazon are separate methods. An Amazon account email is not evidence that eero OTP is supported. Do not repeatedly request codes for an unsupported method.

### Amazon account

1. Choose **Sign in with Amazon**. The default browser opens a private local return page with the Amazon sign-in link. This is the current **default US** flow.
2. Follow that link and complete password/MFA on Amazon. The plugin does not read your browser profile or take your Amazon password.
3. Copy the **entire returned Amazon address** from the current attempt and paste it into the masked field on the local return page. A possible Amazon **PageNotFound** page does not itself mean authentication failed: the returned address may still contain the authorization code. Do not share that address.
4. Let the helper check the return. A pasted address transfers a secret; it is **not consent to save**. When the panel reports that the new sign-in is verified, choose **Save new Session** to replace the previous Session, or **Cancel Amazon sign-in** to keep it.
5. The panel refreshes account data after a successful save. A browser page or a progress phase alone is not proof of a saved Session.

Use only the local page and Amazon tab belonging to the **current attempt**. Old tabs and old returned addresses are not reusable. Cancel a failed attempt and start a fresh one instead of mixing them.

The local receiver listens on `127.0.0.1` at a temporary port. It checks the exact local origin and accepts only the expected Amazon return format; no raw callback is exposed to the panel. Acquisition is bounded to 10 minutes. A timely return has a separate bounded exchange/consent window; Save consent expires after 30 seconds once verification is ready. If it expires, start again. Closing a browser tab is not observable by the plugin: use **Cancel**, close the plugin popup, or allow the attempt to time out. The browser itself is not killed.

During re-login, the existing Session remains unchanged until the new login is approved. Cancel, invalid return or timeout before approval preserves it; this does not make an already expired Session valid. Do not sign out first merely to try another login.

### Browser privacy and connectivity

The copied return address contains a credential. Browser history/sync, clipboard managers, extensions and other processes running as your user may retain or access it. The helper neither inspects nor clears them and does not change browser settings. Avoid sharing your screen during return transfer; handle clipboard/history cleanup yourself according to your browser's controls.

Loopback must bypass browser proxies and route directly. A VPN/proxy, extension or privacy setting can block the local page or its same-origin submission. Do not disable protections globally; check local routing and the current attempt first. A browser-launch error means the return transport could not start, not that your password was rejected. Fresh live login/re-login and real-world return visibility remain unproven independently; synthetic local-return checks are narrower evidence.

## Reading and controlling the network

The first account Network is selected. Use Overview, Nodes and Clients to inspect it; Profile controls are part of Clients, not a separate page. Search/filter Clients and inspect Node details before acting. Unknown values are not automatically offline. Reboot and Sign out require confirmation; reboot can interrupt connectivity. Guest, LED and pause controls change real network state. A speed-test request is not an immediate new measurement.

**Request accepted** acknowledges the request, not the resulting state. **Confirmed by readback** means a later snapshot matched the requested boolean value. **Readback unconfirmed; refresh to check** means confirmation was not established; do not assume either success or failure. Reboot acknowledgement does not prove restored connectivity. Refresh and inspect the relevant state before repeating an action.

## Settings

Preferences belong to the host bar-widget configuration, not the account Session.

| Key | Default | Behaviour |
|---|---|---|
| `pollSeconds` | `60` | Poll every 30–3600 seconds |
| `showClientCount` | `false` | Bar shows connected/total; total is not the connected count |
| `density` | `compact` | `compact` or `comfortable`, with host theme and font |
| `showOfflineClients` | `false` | Initial Clients scope on popup open; does not overwrite an active search/scope |

If the host reports that a preference was not saved, check its eero bar-widget settings and retry. Avoid very frequent manual refreshes when rate-limited.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Unknown status or missing details | The API may omit fields or return unfamiliar values. Refresh; do not reinterpret unknown as offline. |
| Old data remains visible with an error | This is the last successful snapshot, not current proof. Check its freshness/last-success time and restore connectivity. |
| Sign-in required | Session is missing or expired. Use the appropriate login method. |
| Code rejected | Check the current code and identifier; an Amazon email alone does not support OTP. |
| Amazon return rejected or timed out | Use the current attempt's full returned address on its own local page; cancel and restart if stale. Never post the address. |
| Network changed; refresh and try again | Refresh to resolve the account Network, inspect the target, then deliberately retry. Mutations are not retried against another network automatically. |
| Request failed / unconfirmed readback | Inspect the safe error and refresh. An acknowledgement and a successful readback are different. |
| Rate-limited / network / API error | Wait before retrying; check connectivity. The unofficial API may have changed. |
| Panel/import/host failure | Check the host's current plugin API and native QML components. Portable tests cannot establish host compatibility. |

For a public bug report include the plugin/host versions, sanitized steps, safe error text and whether reproduction was synthetic or live. Do not attach raw helper stdout, Session files, account responses or browser callbacks. Remove names, identifiers, IP/MAC addresses and network details from screenshots. See [Security](../SECURITY.md).

## Update, re-login and removal

Review upstream changes before updating an unsandboxed plugin:

```sh
omarchy plugin update n501.eero-bar
```

Use the panel's re-login entry to acquire a replacement without deleting a working Session first. **Sign out** deletes the local Session only; it does not promise server revocation. The file is `$XDG_STATE_HOME/n501.eero-bar/session.json`, normally `~/.local/state/n501.eero-bar/session.json`, stored with restricted permissions. Do not publish or back it up to a public repository.

To stop the plugin:

```sh
omarchy plugin disable n501.eero-bar
```

To remove its clone:

```sh
omarchy plugin remove n501.eero-bar
```

Removal does **not** remove the Session outside the clone. If you want local credentials deleted, Sign out while the plugin is still enabled, then disable/remove it. Never assume uninstall revokes access on the server.

## Technical reference

[Helper protocol](helper-protocol.md) · [Service API](service-api.md) · [API notes](eero-api.md) · [Architecture decisions](adr/0001-plugin-architecture.md)
