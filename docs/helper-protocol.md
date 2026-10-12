# Helper protocol (contract between `bin/eero-helper` and QML)

Invocation: `python3 bin/eero-helper <command> [args...]`. Ordinary commands emit
exactly one terminal JSON object on stdout. `verify` emits safe verification
progress before private approval; `amazon-login` emits safe acquisition progress
before explicit private save consent. Terminal output remains one UTF-8 JSON line.
Exit code follows the terminal object: 0 on success, 1 on error, 2 on usage/validation error. stderr is diagnostics only and never contains tokens or codes.
Secrets arrive on stdin (one trimmed line); they never appear in argv, stdout, or stderr.
For `verify`, keep stdin open after the code and supply a second approval line only after progress.

## Output
Success: `{"ok": true, "type": "<type>", ...}`. Error: `{"ok": false, "type": "error", "code": "<code>", "message": "<human text>"}`.
Error codes: `auth_required` (no/expired session), `login_rejected` (bad identifier/code), `login_cancelled` (verification not approved/approval expired; old Session unchanged), `rate_limited`, `network`, `api`, `usage`, `state`.

## Commands
| Command | stdin | Success type & payload |
|---|---|---|
| `status` | — | `snapshot` (below) |
| `login` | identifier | `code_sent` `{"identifier": "<masked, e.g. n***@gmail.com / +7******12>"}` |
| `verify` | code, then private `commit\n` approval | `verification_ready` progress, then terminal `logged_in` — QML calls `status` after process settlement |
| `amazon-login` | keep stdin open; explicit `commit\n` only after candidate ready, EOF to cancel | `amazon_progress` with `phase` (`browser_open`, `exchanging`, `verification_ready`), then terminal `logged_in` |
| `logout` | — | `logged_out` (deletes local Session; an already absent Session is successful) |
| `reboot-node <nodeUrl>` | — | `done` `{"action":"reboot-node"}` |
| `reboot-network` | — | `done` |
| `guest <on\|off>` | — | `done` |
| `led <nodeUrl> <on\|off>` | — | `done` |
| `pause-client <mac> <on\|off>` | — | `done` |
| `pause-profile <profileUrl> <on\|off>` | — | `done` |
| `speedtest` | — | `done` |

Validation: `nodeUrl` =~ `^/2\.2/eeros/\d+$`; `profileUrl` =~ `^/2\.2/networks/\d+/profiles/\d+$`; `mac` =~ `^[0-9a-f]{2}(:[0-9a-f]{2}){5}$` (lowercased first). Anything else → `usage`, no HTTP.
Network id for actions comes from the Session (`networkUrl`, cached by `status`); if missing, helper resolves it via `/2.2/account`.

## Snapshot
```json
{"ok": true, "type": "snapshot", "fetchedAt": "<ISO8601 UTC>",
 "network": {"id": "123", "url": "/2.2/networks/123", "name": "Home", "status": "online|degraded|offline|unknown",
             "internetOnline": true, "guestEnabled": false, "guestName": "Home Guest",
             "speed": {"down": 312.4, "up": 41.0, "units": "Mbps", "date": "<iso|null>"}, "premium": false},
 "nodes": [{"id": "456", "url": "/2.2/eeros/456", "name": "Living Room", "model": "eero Pro 6", "gateway": true,
            "online": true, "clients": 12, "ledOn": true, "meshBars": 5, "ip": "192.168.4.1", "firmware": "v7.x",
            "updateAvailable": false, "wired": false}],
 "clients": [{"mac": "aa:bb:cc:dd:ee:ff", "name": "Pixel", "ip": "192.168.4.30", "connected": true, "wireless": true,
              "paused": false, "guest": false, "nodeName": "Living Room", "profileName": "Kids|null"}],
 "profiles": [{"id": "789", "url": "/2.2/networks/123/profiles/789", "name": "Kids", "paused": false, "clientCount": 3}]}
```
`name` fallbacks: node `location` → `model` → serial; client `nickname` → `hostname` → mac. Nodes sorted gateway first then name; clients connected first then name (case-insensitive); profiles by name. Unknown/missing values → `null`, never absent keys.

## Command clarifications
- `done` always carries `"action": "<command>"`.
- `on`/`off` sets the named flag: `pause-client <mac> on` → `paused: true`; `guest on` → enabled; `led … on` → `led_on: true`.
- `network.internetOnline`, `network.premium`, and `node.online` may be `null` when eero reports an unrecognised status. A yellow/warn node counts as online.
- Only `status` re-resolves the network after a 403/404. Mutating commands clear the cached network and fail with `api` ("Network changed; refresh and try again."). They never retry against a different network.
- Any url in a Snapshot that fails its regex is `null`. QML must disable actions for rows with a `null` url or mac.
- `done` for LED/reboot is request acknowledgement, not confirmed LED state or
  proof of restored connectivity. The UI uses the normal `status` Snapshot for
  boolean readback; no Node detail GET or new endpoint is introduced. Node
  firmware/update/wired/mesh/IP details are already present in that Snapshot.
- For ordinary secret-bearing `login`, write `secret\n` and close stdin. `verify` and `amazon-login` instead keep stdin open for their private approval handshakes.
- `login` keeps the session's existing `token`/`networkUrl` and stores only the
  `pendingUserToken`: a re-login (started while already signed in) must not destroy the live
  session before verification is approved. `verify` replaces the live token only after private approval.

## OTP commit handshake

`verify` first performs the existing OTP HTTP exchange. On success it flushes
`{"ok":true,"type":"verification_ready"}` — **exactly these two metadata fields,
no token/code/identifier**. The candidate eero token stays only in Helper memory.
The owning Service writes exactly `commit\n` to that same subprocess's private
stdin only if its generation is still current. EOF, a different line, or a
30-second approval deadline yields terminal `login_cancelled` and no Session
replacement. Successful approval atomically saves through the existing Session
store and returns the terminal `logged_in` line. Failed HTTP verification emits
only the terminal error. Consumers parse the final line as the terminal result;
the progress line is not login completion.

Service approval is the successful verification linearization point: before it,
Popup close/cancel restores the old Session and fences late progress; after it,
the verified commit is no longer a pending Login flow. Sign out and new login
wait for **every owned Helper invocation** to exit or fail to start, including
status/Action token refresh and cancelled/timeout processes, so a late write cannot resurrect Session after logout or replace a
new pending login. See [ADR-0002](adr/0002-otp-session-commit.md). No server revoke
is promised; `logout` deletes only the local Session.

## Amazon acquisition

See [ADR-0003](adr/0003-amazon-private-browser.md). Native UI starts a bounded
default-US acquisition in the system default browser, using Omarchy's supported
browser launcher when available and otherwise `xdg-open`. No Chromium requirement,
installation, browser automation or profile access remains. Helper owns a child
stdlib loopback return page, configured via private stdin; startup and callback
JSON use an owned private pipe, not public Helper stdout. Only the root loopback
address is passed to the browser launcher. Human password/MFA stay on Amazon;
the user pastes the entire fixed Amazon return address into the masked local page.
This is a secret transfer, not Session save consent. Manual URL validation is not
browser provenance: fresh PKCE/client and successful exchange provide binding.
No returned OAuth state is established or invented.

Each progress line is exactly `{\"ok\":true,\"type\":\"amazon_progress\",\"phase\":\"...\"}`.
It never carries identity, URLs, codes or tokens. `verification_ready` means all
three HTTP calls and account validation succeeded, **not** that Session is saved.
Only complete `commit\n` received within 30 seconds afterward permits guarded
atomic replacement with the final eero token and validated Network URL. EOF,
cancel/other lines, expired attempts and invalid callbacks preserve the previous
Session. Browser tab closure cannot be observed; explicit Cancel/Popup close or
the 600-second acquisition deadline are authoritative. Each HTTP exchange has a
15-second absolute deadline across open/read and refuses redirects/retries.
Blocked exchanges check EOF/cancel, partial or premature consent and attempt expiry
at most every 100 ms. The owned receiver is reaped and its pipes closed before
exchange/save; graceful SIGTERM is deferred during cleanup. The user's browser
is never killed and its profile is never read or deleted. `browser_unavailable`
means the default-browser return transport could not start; errors are fixed safe text.

Validation, independent static security review and actual Zen synthetic local return
passed; human evidence remains pending. Synthetic checks do not prove fresh live
login/re-login, real save consent, marketplace coverage, lifetime or token rotation.

The return receiver binds only 127.0.0.1 on an ephemeral port. Exact Host and POST
Origin, per-receiver hidden CSRF nonce, fixed paths, bounded form body, short
absolute handler deadline and aggregate acquisition deadline protect input.
No CORS, external assets, access logs or URL echo; no-store, Referrer-Policy:
same-origin and CSP frame-ancestors none apply. The policy preserves the serialized
loopback Origin for same-origin native form POSTs; no-referrer would taint their
Origin to null, which remains rejected. Cross-origin navigation sends no referrer;
the Amazon link also retains noopener noreferrer. Callback requires exact HTTPS www.amazon.com,
no userinfo/port/fragment, /ap/maplanding and one bounded nonempty
openid.oa2.authorization_code. Cleanup failure prevents exchange/save. A timely
return gets a separate 75-second exchange/consent window including 30-second consent.
Default browser history/sync and clipboard managers may retain the human-copied
callback; Helper neither inspects nor clears them. Real callback address visibility
after Amazon navigation remains unproven pending authorized human login/re-login.
Loopback must route directly, bypassing browser proxies. Extensions and same-user
malicious processes are residual risks; Helper does not change browser settings.

