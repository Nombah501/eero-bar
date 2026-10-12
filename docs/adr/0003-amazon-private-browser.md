# ADR-0003: Amazon MAP default-browser return acquisition

Accepted implementation boundary, 2026-10-12. This supersedes the earlier compulsory isolated Chromium/CDP/profile approach. Independent static security review, local validation and actual Zen synthetic local handoff passed, including the same-origin form POST policy. Fresh authorized human login/re-login remains unproven. Historical Chromium interception demonstrated protocol exchange, not this replacement's human return flow.

## Decision

Preserve the historically observed default-US protocol: fresh DSN and S256 PKCE, fixed `https://www.amazon.com/ap/maplanding`, exact `openid.oa2.authorization_code`, unsigned Amazon `/auth/token`, eero `/2.2/login/amazon` with `create_account=false`, and read-only account verification. No guessed state/identity fields, localhost return_to, custom scheme or generic LWA substitution. A successful historical exchange for one account does not establish other marketplaces or repeatable human login.

The one-shot Helper retains verifier and owns a bounded stdlib receiver child. Private child stdin configures portal/deadline; owned child stdout carries bounded startup/callback JSON, never public Helper output. Receiver binds only 127.0.0.1 on an ephemeral port. Launch argv contains only its public root address; prefer `/usr/share/omarchy/bin/omarchy-launch-browser`, otherwise `xdg-open`. No Chromium fallback, installation, browser-profile reads or global clipboard reads. Never terminate the user's browser.

The local page links Amazon in a new tab and instructs the human to paste the entire returned Amazon address into a masked, autocomplete-disabled field. Password/MFA stay on Amazon. Return transfer is not consent to save. Exact Host/Origin, per-child CSRF nonce, fixed paths, bounded form body/private JSON, short absolute HTTP handler deadline and ten-minute aggregate deadline protect the receiver. No external assets, CORS, access logs, URL echo or secret files. CSP denies assets and framing; responses are no-store with Referrer-Policy: same-origin. This preserves the serialized loopback Origin on same-origin native form POSTs: no-referrer instead taints their Origin to null, which the exact Origin guard rejects. Cross-origin navigation sends no referrer, and the Amazon link additionally retains noopener noreferrer. Invalid Host/Origin/CSRF requests, including null Origin, do not consume the attempt.

Main Helper validates exact HTTPS www.amazon.com, no port/userinfo/fragment, exact path and single bounded nonempty code. Manual URL syntax is not browser provenance. Cryptographic correlation is fresh PKCE/client validated by exchange; no returned OAuth state has been observed. Reject replay, cancellation and expiry. Old/cross-attempt artifacts cannot produce a candidate without successful fresh-bound exchange.

Reap the receiver and close private descriptors before exchange/save, deferring SIGTERM during scoped cleanup. Preserve three absolute 15-second exchanges, 75-second post-acquisition window and complete 30-second explicit commit consent. Service keeps its 690-second watchdog, generation fences and all-owned-writer barrier. Only final approved eero Session is atomically saved through existing guarded store. Existing Session remains until consent. Popup input surface yields to browser acquisition/exchange while retaining logical open intent; candidate/error returns only while intent is open.

## Consequences and acceptance

Browser closure cannot be observed: native Cancel/Popup close and acquisition expiry are authoritative. The default browser may retain callback in history/sync; clipboard managers may retain copied addresses. Helper never inspects or deletes them. The page recommends user-selected private mode without claiming the portable launcher can enforce it. Python cannot guarantee immutable-string zeroization or hard-crash cleanup.

The critical unproven fact is whether normal Amazon navigation leaves the entire callback address available for human copying. Historical interception stopped navigation before that fact could be established. Synthetic default-browser handoff proves only local transfer/cleanup, and static security review does not prove live authentication. Authorized human login AND re-login, separate explicit Session save consent and read-only status after each remain unproven. Do not claim complete production or live acceptance.

Loopback acceptance requires direct browser routing, bypassing configured proxies.
Helper does not silently change proxy/profile settings. Extensions, a proxy that
sees the page and POST, and malicious same-user processes are outside web-origin
protections. Existing Amazon browser account state may choose a different account
than intended; PKCE binds the attempt, not human account intent.
