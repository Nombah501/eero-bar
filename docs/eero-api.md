# eero cloud API (unofficial) — reference

Reverse-engineered, with no stability guarantees. Protocol-research references:
[343max/eero-client](https://github.com/343max/eero-client),
[schmittx/home-assistant-eero](https://github.com/schmittx/home-assistant-eero)
(`custom_components/eero/api/*`), [erikh/eero](https://github.com/erikh/eero) (Rust),
[arvarik/eero-go](https://github.com/arvarik/eero-go), and
[fulviofreitas/eero-api](https://github.com/fulviofreitas/eero-api)
([wiki](https://github.com/fulviofreitas/eero-api/wiki)).
These are research references, not a claim that their code was copied or that
their licenses grant rights to this repository.

This is the unofficial **control API** used by the mobile app, not the official
EU Data Act data-portability API. The latter is read-only portability access,
not a supported interface for reboot, Guest, LED, Pause or Speedtest controls.

## Transport
- Origin `https://api-user.e2ro.com`; paths are `/2.2/...`.
- **Exception:** device writes (pause) must use `PUT /2.3/networks/{id}/devices/{mac}` — `/2.2` returns 200 but silently does nothing.
- Auth: cookie `s=<token>` on every request. Send `Content-Type: application/json` on bodies and a plausible `User-Agent` (e.g. `eero-bar/0.2`).
- Resources carry their own `url` (e.g. `/2.2/eeros/123`); build requests as origin + `url`, never re-append `/2.2`.

## Envelope
`{"meta": {"code": 200, "server_time": "...", "error": null}, "data": ...}`. Success = `meta.code` 200–299.
Errors: `meta.code` + `meta.error` string. HTTP 429 means rate limited; the historical estimate of about 100 requests/minute is not a guaranteed quota.

## Login
```
POST /2.2/login          {"login": "<email or +E164 phone>"}        -> data.user_token
POST /2.2/login/verify   {"code": "<code>"}   cookie s=<user_token>  -> session now valid (token = user_token; prefer Set-Cookie s= if present)
POST /2.2/login/refresh  cookie s=<token>                           -> data.user_token (new token)
```
On `meta.code == 401` with `meta.error` in (`error.session.refresh`, `error.session.invalid`): call refresh once, retry once; if still 401 → re-login required.
One historical account/default-US Amazon MAP → eero exchange succeeded on
2026-10-09, and its saved eero Session works with this helper. That observation
does not establish marketplace coverage or a repeatable human login flow.
The default-browser/manual local return implementation has passed local synthetic
transport validation and independent static security review, including its
same-origin POST policy. Fresh authorized human login/re-login remains unproven;
Session persistence requires separate explicit consent.
OTP must not be promised for an Amazon account merely because it has an email address.

## Endpoints
| Purpose | Method + path | Body | Useful fields |
|---|---|---|---|
| Account | GET `/2.2/account` | — | `networks.data[].url`, `.name` |
| Network | GET `{network.url}` | — | `name`, `status`, `health.internet.status`, `health.eero_network.status`, `speed.down/up.{value,units}`, `speed.date`, `guest_network.{enabled,name}`, `premium_status` |
| Nodes | GET `{network.url}/eeros` | — | `url`, `serial`, `model`, `location`, `status`, `gateway`, `connected_clients_count`, `led_on`, `mesh_quality_bars`, `ip_address`, `os_version`, `update_available`, `wired` |
| Clients | GET `{network.url}/devices` | — | `url`, `mac`, `nickname`, `hostname`, `ip`, `connected`, `wireless`, `paused`, `is_guest`, `source.location`, `profile.{url,name}` |
| Profiles | GET `{network.url}/profiles` | — | `url`, `name`, `paused`, `devices[]` |
| Reboot node | POST `{eero.url}/reboot` | — | ack |
| Reboot network | POST `{network.url}/reboot` | — | ack |
| Guest | PUT `{network.url}/guestnetwork` | `{"enabled": bool}` | `enabled` |
| LED | PUT `{eero.url}` | `{"led_on": bool}` | node |
| Pause client | PUT `/2.3/networks/{nid}/devices/{mac}` | `{"paused": bool}` | `paused` |
| Pause profile | PUT `{profile.url}` | `{"paused": bool}` | `paused` |
| Speed test | POST `{network.url}/speedtest` | — | async; result lands in network `speed` later |

Pause suspends internet access; it does not Block association with the Network.
A Client can therefore be both connected and paused.
Speedtest acknowledgement only accepts the request. The measurement is
Gateway→internet, not Wi-Fi Client throughput; only a newer `speed.date`
confirms a new result.

Treat every field as optional (`.get` with defaults). Premium-only features may be null.
