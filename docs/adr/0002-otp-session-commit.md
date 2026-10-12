# ADR-0002: OTP Session commit approval

Status: accepted for the Overview/login lifecycle, 2026-10-10.

## Context

Generation fencing in Service protects QML replies, not the persistent Session:
previously Helper `verify` could save a new token after Popup cancellation while
its terminal reply was correctly ignored. Killing a subprocess alone does not
prove a disk write did not already happen. Re-login must preserve the old live
Session until the current attempt is successfully verified and approved.

## Decision

Keep the one-shot Python Helper and existing atomic Session store. For `verify`
only, split verification from commit on the existing private process pipes:

1. Service sends the OTP code line but keeps stdin open.
2. Helper performs HTTP verification and holds the candidate token in memory.
   It flushes exactly `{ "ok": true, "type": "verification_ready" }` on stdout.
3. Service accepts this progress once, only for the current generation. Granting
   approval is the verification linearization point: Service leaves pending
   login, enters `loading`, and writes exactly `commit\n` then closes stdin.
4. Helper saves through the existing Session store and emits terminal `logged_in`.
   Only after process exit does Service refresh the Snapshot.

Before approval, Popup close or Cancel increments the generation, denies stdin
approval and restores the prior live phase/Snapshot. EOF, invalid approval or
30-second approval timeout never saves the candidate; terminal `login_cancelled`
reports this outcome. HTTP failure emits only the terminal error. A local save
failure after approval restores the prior live phase (or signed-out origin) and
surfaces an error, not success. Service's existing 30-second total process
watchdog also bounds the operation; timeout writers are released only after the
killed process exits, not when a callback is merely fenced.

All Service-owned Helper processes, including status/Action token/cache writers
and cancelled identifier sends, settle before new OTP/Amazon login or local logout.
Sign out clears QML state immediately but waits for every owned exit before deleting Session; a commit already
granted cannot save after logout and resurrect the Session. New login queues
behind this deletion. A cancelled identifier send may finish storing its pending
token, but does not replace the live token, and must exit before the next attempt
can overwrite pending state. New status/Actions are blocked as soon as a transition
is queued; outside transitions their ordinary concurrency remains unchanged.
The existing owned-process list is authoritative, not presentation busy flags.

Writer-ordering regression: `python3 -m unittest tests.test_service_lifecycle`
runs actual Service with delayed synthetic status/Action token/cache persistence,
checks subprocess ordering before logout/OTP/Amazon, cancelled queues and concurrent
ordinary writers. Timeout-after-SIGKILL/uninterruptible-filesystem behavior remains
a static-review limit: this fixture does not inject private wrapper/kill/timer state.

No token, code, verifier or candidate is emitted in progress, argv, QML metadata
or diagnostics. Approval travels on the owning subprocess's private stdin, not
a public IPC endpoint. No new runtime dependency, persistent candidate file,
Session schema, lock service or authentication framework is introduced. This
is an OTP lifecycle decision, not an Amazon browser/callback integration.

## Alternatives and consequences

Persisted attempt IDs plus compare-and-swap/locking could fence every independent
writer, but would add Session fields, secure lock lifecycle and broader store
migration for this single owning Service. Private approval reuses the existing
process boundary and avoids copying/rolling back tokens in QML. It deliberately
requires direct Helper consumers to migrate: `verify` now has progress plus one
terminal line and requires approval; there is no legacy implicit-save mode.

After approval a verified commit is no longer pending, even if its terminal line
has not arrived. Close does not roll back this completed verification. Sign out
remains a separate deletion of local Session, never a promise of server revoke.
Independent external Helper writers are outside Service serialization; this does
not claim process-wide transactional isolation for arbitrary external clients.

## Regression seams

Helper command tests use injected transport and an isolated real Session store:
EOF/invalid approval preserve the live token, approved verification replaces it,
and progress never exposes secrets. The real Service subprocess test checks
cancelled send/new login ordering, late verification denial, sign out after grant,
Guest readback/failure and Speedtest acknowledgement with unchanged measurement.
The runner uses an isolated temporary root/state and hard no-network guard.
Offscreen Service testing does not replace native Popup smoke testing.
