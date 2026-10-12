# Service.qml public API

`Service.qml` is the `n501.eero-bar` service (`bar.shell.serviceFor("n501.eero-bar")`). It owns the
helper process, the polled Snapshot, and login state. Nothing here parses raw eero JSON --
that is `bin/eero-helper`'s job (`docs/helper-protocol.md`); this is the normalized shape QML consumes.

## Properties

| Property | Type | Meaning |
|---|---|---|
| `phase` | string | `"loading"` \| `"signedOut"` \| `"codeSent"` \| `"amazon"` \| `"ready"` \| `"error"` |
| `snapshot` | object \| null | `{network, nodes, clients, profiles}`, `docs/helper-protocol.md`'s Snapshot shape (minus `ok`/`type`/`fetchedAt`). `null` until the first successful `status`, and after `signOut()`. |
| `lastError` | object \| null | `{code, message}` from the most recent failed **status or login** call, or `null`. Cleared by the next successful status/login step, or by `clearError()`. Actions never set or clear this -- see `actionError`. |
| `actionError` | object \| null | `{key, code, message}` from the most recent failed **action** (including a rejected-before-dispatch invalid argument), or `null`. Cleared by `clearError()`, by starting a new action (whether or not that action itself succeeds), or by `signOut()`. The `refresh()` every action triggers afterward never clears it, so the message stays on screen long enough to read. |
| `maskedIdentifier` | string | Set by `sendCode()` on `code_sent` (e.g. `"n***@gmail.com"`). Cleared on a successful `verifyCode()` and on `signOut()`; empty outside the login flow. |
| `loginBusy` | bool | `true` during OTP/Amazon work and for any reserved successor login, including while previous owned Helpers settle. Predecessor completion does not release a queued successor's reservation. |
| `amazonPhase` | string | Empty outside acquisition; `browser_open`, `exchanging`, `verification_ready`, then `saving` after consent. Safe metadata only. |
| `refreshing` | bool | `true` while a `status` call is in flight. |
| `busyActions` | object | `actionKey -> true` map (see `EeroModel.actionKey`). Reassigned wholesale on every change, never mutated in place, so bindings on `busyActions[key]` update. Use `isBusy(key)` rather than reading it directly. |
| `lastFetchedAt` | string \| null | ISO8601 UTC timestamp of the last successful `status`. Cleared on `signOut()`. |
| `pollSeconds` | int | Poll interval, default 60, floored and clamped to `[30, 3600]` by `configure()`. |
| `showClientCount` | bool | Widget setting, default false. Displays connected/total, never total alone as connected. |
| `density` | string | `"compact"` (default) or `"comfortable"`; local native row spacing and minimum Button hit heights, not global theme/font changes. |
| `showOfflineClients` | bool | Default false; initializes Clients connection scope on Popup open only. |
| `guestState` | string | Empty, `Sending request`, `Awaiting readback`, `Confirmed by readback`, `Request failed`, or `Readback unconfirmed; refresh to check`. Snapshot values never optimistically change. |
| `speedtestState` | string | Empty, `Sending request`, `Result pending`, `New measurement received`, `Request failed`, or `Result unconfirmed; refresh to check`. Request acknowledgement is not a measurement. |
| `actionFeedback` | object | Service-owned per-target Node/Pause/reboot feedback. Use the methods below. Cleared on generation changes; valid current Snapshot targets determine the key space. |
| `actionsEnabled` | bool | Snapshot present, phase ready, no pending/approved login writer, queued identity transition or requested/in-flight logout. Old visible Snapshot does not authorize mutation during auth settlement. |

## Functions

- `configure(settings)` -- apply host-owned widget preferences (`pollSeconds`,
  `showClientCount`, `density`, `showOfflineClients`). A non-object input is treated
  as missing settings. Poll accepts finite numbers or nonblank numeric strings,
  floors them and clamps to `[30, 3600]`; missing/null/blank/boolean/object/nonfinite
  values fall back to 60. Booleans accept only literal `true`; all other values
  normalize to false. Only literal `"comfortable"` selects comfortable density;
  missing/invalid density normalizes to `"compact"`. Safe to call repeatedly:
  it changes neither Session generation, Snapshot, Actions nor local view state.
- `refresh()` -- run `status` now. A no-op while `phase` is `"codeSent"`, or while a `signOut()`'s
  `logout` call is still in flight (see below). If a `status` call is already in flight, the request is
  queued rather than dropped: exactly one more `refresh()` runs once the in-flight call settles (this
  is what makes the refresh a reboot/pause/etc. action triggers, and the one `verifyCode()` triggers,
  actually land even when they race a poll tick). Only the in-flight call itself ever clears
  `refreshing`/runs the queued refresh, so two `status` calls are never in flight at once.
- `refreshIfStale(maxAgeSec)` -- `refresh()` (with the same rules above) only if `lastFetchedAt` is
  missing/unparseable or older than `maxAgeSec`. Panel calls this on Popup open.
- `sendCode(identifier)` -- start login. On success, `phase` becomes `"codeSent"` and
  `maskedIdentifier` is set. If any owned Helper invocation (including status or an Action)
  is outstanding, the request queues (`loginBusy` becomes true immediately).
  Dispatch waits for actual exit/start failure, including cancelled or timed-out writers.
  While queued, the identifier sits in a private property (cleared when dispatched or if `cancelLogin()`/`signOut()`
  is called before that happens, or on destruction); once dispatched it is handed straight to the
  helper's stdin and the property holding it is cleared immediately after the write.
- `verifyCode(code)` -- valid only in `"codeSent"`, with no login process outstanding.
  Helper verifies HTTP first, then emits safe `verification_ready` progress.
  Service grants private stdin approval only for the current generation. At grant,
  `phase` becomes `"loading"`, `maskedIdentifier` clears and `loginBusy` becomes false:
  verification is no longer pending/cancelable. After Helper saves and exits,
  `refresh()` resolves the live phase. Before grant, failure keeps `"codeSent"`
  and sets `lastError`; a save failure after grant restores the previous live phase
  (or `"signedOut"`) and exposes `lastError`. See [ADR-0002](adr/0002-otp-session-commit.md).
- `startAmazonLogin()` -- queue a bounded default-browser/local-return acquisition. Drain
  existing status/Actions before dispatch; share the OTP/logout writer barrier.
  `phase="amazon"` hides old Snapshot without destroying it. No secret parameter.
- `approveAmazonSession()` -- valid only for the current `verification_ready`
  candidate. This explicit user save consent synchronously grants private stdin
  commit; the candidate is no longer pending/cancelable afterward. Session save
  failure restores the prior phase and reports a safe error; success triggers
  status only after writer settlement. Password/MFA are never supplied to Service.
- `cancelLogin()` -- abort an in-progress or queued login attempt. A no-op unless `phase` is
  `"codeSent"` or `loginBusy` is `true`; otherwise `phase` becomes `"signedOut"`.
  A re-login started from `"ready"`/`"error"` restores that phase; `"loading"`
  origin (e.g. queued login after commit, before readback) is restored too and refreshed
  instead, because the helper keeps the previous token while a login is pending
  (`docs/helper-protocol.md`), so the old session is still valid and signing out would be a
  lie. Starting a login from a live phase does not clear `snapshot` -- the popup hides the main
  view behind its own re-login state and shows it again on restore.
  Pending verify loses its stdin approval on cancel; late progress cannot save
  the candidate Session. An in-flight identifier send can still finish storing
  a pending token, but cannot replace the live token; a new login waits for its
  exit before overwriting that pending token. Popup close calls `cancelLogin()`
  through the shared `opened` transition, not `signOut()`. Approval already
  granted is a completed verification and is intentionally not cancelled.
- `signOut()` -- switches to signed-out state immediately (`phase = "signedOut"`, `snapshot`,
  `lastFetchedAt`, `maskedIdentifier`, `lastError`, `actionError` all cleared, `loginBusy` reset,
  polling stops, any queued `refresh()` dropped), then queues local `logout`
  behind every outstanding owned Helper invocation. New status and Actions are blocked
  immediately when OTP/Amazon/logout is reserved, throughout drain and acquisition/deletion.
  Late token refresh/cache persistence therefore finishes before logout deletes Session.
  Logout's normal reply is otherwise ignored; a local deletion failure surfaces as
  `lastError` only if nothing else has moved the Service on since. A `sendCode()` made
  while logout is still in flight queues behind deletion. A `status` call or Action that
  was already in flight *before* `signOut()` is left running (never force-cancelled): its own
  generation check discards any effect on `phase`/`snapshot`/`actionError`/`refresh()` once it
  settles, and only it (not `signOut()`) clears `refreshing` if it was the in-flight `status` call.
  Timeout notifications clear UI flags but do not release writer ownership before actual exit.
  Ordinary status and different-key Actions remain concurrent outside identity transitions.
- `rebootNode(url)`, `rebootNetwork()`, `setGuest(on)`, `setLed(url, on)`, `pauseClient(mac, on)`,
  `pauseProfile(url, on)`, `runSpeedtest()` -- actions. Args are validated with `EeroModel`'s
  validators before any process runs; an invalid arg sets `actionError` (`code: "usage"`) and runs
  nothing. `pauseClient`'s `mac` is lowercased before validation, before the action key is built, and
  before it is passed to the helper. Each action's key (`EeroModel.actionKey(kind, id)`) can only
  have one call in flight at a time; a second call while busy is a no-op. `isBusy(key)` always clears
  once the process actually exits, even if a `signOut()`/`cancelLogin()`/new `sendCode()` happened
  meanwhile; but in that case the action's completion is otherwise generation-fenced like
  status/login, so it does not set `actionError` or call `refresh()`. Otherwise, both success and
  failure end with `refresh()`.
- `isBusy(key)` -- `true` while the action for that key is in flight.
- `actionState(key)` -- empty, `Sending request`, `Awaiting readback`,
  `Confirmed by readback`, `Request failed`, `Readback unconfirmed; refresh to check`,
  or (reboot only) `Request accepted`. Reboot acceptance never proves restoration.
- `actionPending(key)` -- sending or awaiting boolean readback; conflicting
  repeated LED/Client Pause/Profile Pause requests are suppressed through both phases.
- `actionDetails(key)` -- `{state, error}` with the same display state and a
  per-key `{code,message}` failure or null. Unrelated Actions and successful status
  do not erase this local failure; status/login `lastError` is independent.
- `_runAction` rejects all mutations before dispatch when `actionsEnabled` is
  false, with contextual `state` Action failure. Boolean/reboot controls consume
  the same predicate; auth commit must settle and fresh status become ready first.
- `clearError()` -- clears both `lastError` and `actionError` (e.g. when the user dismisses an inline
  error).
- `clearActionError()` -- clears only `actionError`, leaving `lastError` untouched.
  The action-error banner dismiss (×) uses this instead of `clearError()`, so
  dismissing an action failure never drops an unrelated status/login error.

## Behavior notes

- Polling runs only while `phase` is `"ready"` or `"error"` (not `"signedOut"`/`"codeSent"`), starts
  with an immediate `refresh()` on load, and never overlaps a call already in flight (see the queueing
  note on `refresh()` above).
- `refresh()` is blocked during pending login/verify/logout writers and while
  `phase` is `"codeSent"`. A previously dispatched status reply cannot interrupt
  a new Login flow: generation fencing discards it. The outstanding status alone
  still owns `refreshing` and queued-refresh settlement. Post-verify refresh runs
  after the Session-writing process actually exits.
- A `status`/login/action reply that lands after a `signOut()`/`cancelLogin()`/new `sendCode()` is
  discarded (generation-fenced) so it cannot resurrect state from a previous session/attempt.
- On a non-`auth_required` error, the previous `snapshot` is kept (`phase` only becomes `"error"` when
  there is no snapshot at all yet) so the popup can keep showing last-known state with an inline error.
- Secrets (`identifier`, `code`) go to the helper over stdin and are never logged. A secret is held in
  a local variable while the call that sends it runs. The one exception is an identifier queued behind
  the helper `logout` that `signOut()` started: it sits in the private property `_queuedSendCode` until
  it is dispatched. The secret is cleared once it is written to the helper's stdin, or dropped on
  `cancelLogin()`, `signOut()` or destruction if it never gets that far. QML/JS cannot zero the
  underlying string's memory, so the guarantee is only that no secret is left in a property.

## Native Overview

Panel uses the existing host theme/KeyboardPanel, a persistent Network health,
connected/total Client count (including explicit unknown count), freshness and
Refresh header, and Overview/Clients/Nodes/Settings top tabs. Every open selects
Overview; each selected page uses the one full-width scroll body.
Clients provides search, connection filters and Profile Pause controls;
Nodes uses `NodesPage.qml`, and Settings shares confirmed Network reboot.
Closing through host/controller/Escape paths resets confirmation and cancels
unapproved OTP or Amazon login, not an already approved Session commit.

Guest acknowledgement starts readback observation. Only a successful Snapshot
with the requested boolean confirms it; failure retains the last known boolean.
Repeat Guest requests are suppressed during HTTP and pending readback.
Speedtest acknowledgement enters `Result pending`; only a strictly newer,
parseable `speed.date` with at least one finite, nonnegative `down`/`up` measurement
confirms a result (when the prior date was unknown, it must be newer than the
request timestamp). A newer timestamp with no measurement remains unconfirmed.
Measurements show date/units and Gateway→internet scope, never Wi-Fi Client
throughput. Missing units are unknown.
Each observation has an independent 120-second deadline, then shows unconfirmed;
there is no extra polling/retry. Normal polling/manual Refresh can still observe
a late Speedtest result. Action failures stay separate from status/login errors.

## Behavioral regression entry points

`python3 -m unittest tests.test_eero_helper tests.test_service_lifecycle -v`
exercises isolated Helper Session approval and, when Quickshell is installed,
the actual Service public API with a synthetic subprocess fixture. The latter
uses a temporary symlink root, offscreen Qt (not native UI proof), isolated state,
a process-local Python no-network guard and an exact helper-path refusal before
auth calls. `EERO_SERVICE_SOURCE` may point to a baseline source for a red run.
`tests/test_service_lifecycle.py` is automatically included by the existing
`scripts/validate.sh` unittest discovery. `node tests/eero_model.test.cjs` covers
unknown counts, transport-health distinction and measurement predicates.
Native Popup keyboard/theme/screen acceptance requires real-surface smoke;
these commands are documented entry points, not a claim of passes.

## Nodes and shared boolean Actions

`NodesPage.qml` accepts `service`, `snapshot`, `foreground`, `fontFamily`;
exports `handleEscape():bool`, `resetForOpen()` and `rebootRequested(url,name)`.
Details survive tab switches and resolve the exact URL against each current
Snapshot, never another Node with the same name. The full name/target wraps.
Gateway sorts first. Status, wired/mesh, IP, firmware, update, mesh bars and
Client count use only existing Snapshot fields; null remains Unknown. Details
do not issue HTTP requests. Invalid/null/removed targets cannot mutate; unknown
LED state disables the switch. Checked always comes from the Snapshot.

LED and Client/Profile Pause share Service-owned expected-value observation.
HTTP acknowledgement starts a 120-second readback deadline, not confirmed state.
Only a successful Snapshot matching the target and expected boolean confirms.
Timeout exposes unconfirmed and permits a new explicit request; no automatic
mutation retries or extra polls are introduced. A failure retains the previous
Snapshot and per-key error. Session generation changes reset observation;
old subprocesses still own their busy flags and cannot publish stale feedback.
There is no arbitrary concurrency cap on valid Snapshot targets.
Node/Pause Actions require valid current Snapshot identity and known boolean
(for switches); Network reboot requires a valid Snapshot Network URL.

Panel owns confirmation. Node and entire Network consequences differ; Cancel,
Esc and close do not dispatch. Phase, Service instance, generation, Network URL
or removed Node invalidates the pending target, and accept rechecks validity.
Esc from Nodes details goes Back before closing. Settings labels Network reboot
as dangerous and displays acknowledgement without promising restoration.

Validation entry points: `node tests/eero_model.test.cjs`,
`python3 -m unittest tests.test_service_lifecycle tests.test_eero_helper -v`,
then `bash scripts/validate.sh`. Native host keyboard/focus/theme, small-screen
scaling, long-name/unknown details and confirmation cancel/close cases require
real Popup smoke. Synthetic fixtures do not establish live Action acceptance;
live mutations require separate permission.
## Native Clients page

`ClientsPage.qml` public properties are `service`, `snapshot`, `foreground`,
`fontFamily`, `showOfflineDefault` (false before persisted preferences), and
read-only `fieldFocused`. Methods: `focusSearch()`, `handleEscape(): bool`,
`resetForOpen(defaultOffline)`. Panel owns the one body scroll and forwards
focus/Escape; the page owns query, combinable filters and Client details.
Tab changes retain that context; a fresh opening, null Snapshot or Service
generation change resets it to avoid retaining another Session's identity.

Connection scope is Connected, Include offline/unknown, or Offline only.
Case-insensitive name/IP/MAC search never bypasses that scope. Paused, Guest,
Node and Profile filters intersect it. Reset clears query/secondary filters
while retaining the visible connection scope. Counts separately label Network
connected/total and current result/connected-in-result. Null connection is
unknown, not offline; missing detail fields are explicitly unknown.
Back returns to the same query/filters; details resolve current Snapshot by
validated MAC rather than holding an obsolete mutable object. A Client with no
valid MAC can be inspected but cannot receive an Action.

Client/Profile controls consume the shared Service `actionState(key): string`,
`actionDetails(key): {state,error}` and `actionPending(key): bool` interface.
Pending includes HTTP and acknowledged readback, so a second conflicting
request is disabled until observation settles. Only matching Snapshot boolean
readback confirms Pause; failed requests retain the last known Snapshot and
an object-keyed error even when an unrelated Action starts. The readback
deadline is 120 seconds; there are no extra mutation retries. Profile Pause
names the group and its Snapshot Client count; Client Pause identifies its
Client. Neither action blocks Wi-Fi association or promises Profile CRUD.
Controls also consume read-only `actionsEnabled`: Actions are unavailable while
an auth/Session writer or logout is pending, including the approved-login
`loading` interval where the old Snapshot can remain visible. Shared Service
dispatch enforces the same gate before HTTP; disabled UI is not the boundary.

Validation entry points: `node tests/eero_model.test.cjs`,
`python3 -m unittest tests.test_eero_helper tests.test_service_lifecycle -v`,
then `bash scripts/validate.sh`. Native keyboard/Escape/back/tab persistence,
theme, long names, 100+ Clients and small-screen/scaling require real host
Popup smoke; synthetic readback is not live Action evidence.

## Persisted display preferences

`manifest.json`'s `barWidget.defaults` and `barWidget.schema` advertise the same
four preferences consumed by `configure()`. Density uses the host-supported
`type: "enum"` with string-array `options` (as in the host agents manifest).
The host injects widget settings into `Panel.settings`; the existing
`pushSettings()` forwards that object to `Service.configure()` on settings
changes and deferred Service attachment. Service is a normalized mirror, not
a persistence store.

Settings edits use the capability-scoped host
`bar.shell.updateEntryInline("n501.eero-bar", settings)` API. That API replaces
the inline entry, so the Popup copies all current host settings before changing
one known preference. It never writes user files itself or optimistically
mutates Service. Host propagation updates the existing settings→Service path.
Without this capability, editing controls are hidden and Settings explains
where to configure preferences through the host. Saving an identical stored
value is a no-op; an unsuccessful changed write exposes a visible error.

Compact retains host row spacing with minimum 32 scaled-pixel Button/search
heights; comfortable adds six scaled pixels to local groups/rows and raises
those minimum heights to 40. Neither mode shrinks host implicit control sizes,
changes the global Style singleton, nor substitutes typography. Overview,
Clients, Profiles, Nodes, details and Settings use this local geometry.

Clients scope starts as connected-only and is initialized by
`resetForOpen(showOfflineClients)` on Popup open: false→`connected`, true→`all`
(including offline/unknown). Changing preferences or switching tabs while open
does not reinitialize scope, query, filters or details. Session generation
changes still clear stale filters/details; they do not reapply the offline
default. The existing connected/total Widget count semantics are unchanged.

The Service lifecycle fixture exercises normalization and preservation of
Snapshot/generation/phase/feedback. Where installed host QML sources are available,
it additionally loads ClientsPage to exercise scope/query/filter preservation
across visibility and preference changes, then next-open initialization.
An in-memory fixture does not prove disk persistence or that an installed host
has loaded the current plugin; those require separate host acceptance.

## Native page keyboard and anonymous inspection

`Panel.navigationTargets()`, `cursorMove(direction)` and `cursorActivate()` are
the bounded public cursor seam. Shared `Navigation.js` collects only explicitly
named `nav:` controls, skips hidden/disabled/noninteractive/busy targets and
rejects duplicate keys. Activation resolves the live key again, never an index.
Native `hasCursor` highlights controls. Only body targets scroll into view.

Arrows and j/k (also host h/l) move; Enter/Space activate. Tab/Backtab switch
host panels, not local pages. Page tabs are cursor targets. Search Enter exits
editing to scope controls; activating search re-enters editing. Settings interval
Enter saves valid input and exits to Save; Escape exits that editor. Editors keep
arrows, j/k and Space. Confirmation/login retain precedence. Escape clears Clients
query first, then returns from details, then closes.

Keys: `refresh`, `page:<page>`, `retry`, `error:signout`, `overview:guest`,
`overview:speed`, `clients:search`, `clients:scope:<scope>`, `clients:paused`,
`clients:guest`, `clients:node:all`, `clients:node:value:<name>`,
`clients:profile:all`, `clients:profile:value:<name>`,
`clients:reset`, `clients:back`, `nodes:back`,
`client:details:<MAC|inspect:fingerprint>`, `client:pause:<MAC>`,
`node:details:<URL|inspect:record>`, `node:led:<URL>`, `node:reboot:<URL>`,
`profile:pause:<URL>`, and
`settings:<density:value|offline|count|poll|save-poll|login|reboot|signout>`.
Anonymous keys permit inspection only, never Actions; duplicate keys are skipped.
Node/Profile `all` keys are sentinels only; actual names occupy the `value:`
namespace. A literal name `all` therefore resolves to `value:all`, never the
sentinel, avoiding duplicate navigation keys and ambiguous activation.

Invalid/missing-MAC Client details survive refresh only when exactly one current
normalized record matches every displayed field (MAC, name, IP, connection,
wireless, Pause, Guest, Node and Profile). Changed/removed/ambiguous records retain
explicit unavailable last-known read-only details until Back and do not reattach.
Valid MAC details resolve current Snapshot by MAC. Null Snapshot and generation
clear context. No full Snapshot copy or cross-Session identity is retained.

Regression entry points: `node tests/native_navigation.test.cjs` and host-import
Quickshell fixture `tests/native_pages.qml` (`EERO_CLIENTS_PAGE_URL` is the absolute
ClientsPage file URL). Native exercise must establish key propagation, cursor
visuals, body scrolling and confirmation/login precedence.
## Amazon native boundary and acceptance

`cancelLogin()`/Popup close invalidates pending Amazon acquisition and closes its
private stdin, preserving old Session. Late progress/results are generation-fenced.
Sign out also closes unapproved acquisition and waits for actual writer exit;
approved commits settle before local logout. Re-login uses the same native controls,
including after expiry. OTP remains separate, with no Amazon-email OTP promise.

Amazon watchdog is 690 seconds (600-second acquisition, bounded receiver cleanup,
three 15-second exchanges and 30-second complete-line consent), not the ordinary Helper watchdog.
Graceful SIGTERM lets Helper reap only its owned receiver child and close its pipes;
normal cancel is EOF. It never kills the user's browser or reads/deletes its profile.
Browser closure is not observable: explicit native cancellation or deadline governs.
Default-browser history/sync and clipboard-manager retention remain user-controlled risks.

Validation entry points:
`python3 -B -m unittest tests.test_eero_amazon tests.test_amazon_return tests.test_service_lifecycle`;
`bash scripts/validate.sh`; `python3 -B scripts/amazon-return-smoke.py` for synthetic
default-browser local return only. Local validation, independent static security
review and Zen synthetic return passed, including same-origin form POST handling.
These do not prove fresh authorized human login or re-login; each still requires
separate Session save consent and read-only status. No complete live acceptance
or marketplace coverage is claimed.

`readonly actionsEnabled` is true only with a non-null Snapshot, `phase === "ready"`, and no busy/pending login process or requested/in-flight logout. The common Action dispatch guard enforces it independently of UI enabled bindings. Guest and Speedtest check it before changing observation state; blocked dispatch cannot create a false sending/pending state. This is the same readiness contract as the shared Nodes provider, not a separate feedback provider.

### Browser input handoff

During Amazon browser acquisition/exchange, Panel retains logical open intent but
unmaps its layer-shell input surface so the default browser can receive
Amazon password/MFA and local return input. This delegation is not a controller close:
explicit Widget toggle/close/IPC or destruction cancels unapproved work.
On verified candidate or error the native Popup resumes only if the controller
still has open intent. Save consent is never automatic. Widget tooltip explains
how to cancel the delegated flow.

Named `nav:login:*` controls participate in the same native cursor route.
Down exits an OTP editor without submitting; Enter still submits OTP. Tab keeps
the host panel shortcut. Browser phase gives input ownership to the default browser;
candidate-ready focus returns to native consent/cancel controls.
