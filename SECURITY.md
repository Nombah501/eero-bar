# Security

This is an unofficial, unsandboxed Omarchy plugin with access to a saved eero Session and network-changing API operations. Trust the source before enabling or updating it. No supported-version security-maintenance window or live production certification is promised.

## Reporting a vulnerability

Do **not** publish credentials or a working secret-bearing reproduction in an issue, pull request, discussion or chat.

If the repository owner has enabled private vulnerability reporting or published a private security contact, use that channel. No private contact is assumed or invented by this document. If none is available, open only a minimal, non-sensitive public request asking the owner to establish a private reporting channel; withhold exploit details and confidential attachments until one exists. Do not send secrets to an unverified contact.

A private report should describe the affected version, trust boundary, impact and a minimal synthetic reproduction. State whether it was observed in a portable fixture, native synthetic UI/browser run or authorized live account. Share only the minimum needed; real credentials are not necessary to explain most boundary failures.

## Never attach

- Session files, eero/Amazon tokens, OTPs, passwords or MFA data.
- The returned Amazon address or its authorization code—even when Amazon shows PageNotFound.
- Raw helper stdout/log dumps, raw account/API responses or browser profiles/history exports.
- Unsanitized screenshots with account identifiers, network/client names, IP/MAC addresses or serial numbers.

For an ordinary public bug report use sanitized steps, plugin/host versions and safe error text. Prefer synthetic screenshots and fixtures. Do not assume a pasted callback becomes harmless after a failed attempt.

## Current boundaries and residual risks

Secrets enter the helper through private stdin, not command-line arguments or public progress output. Session storage uses `$XDG_STATE_HOME/n501.eero-bar/session.json` (normally `~/.local/state/n501.eero-bar/session.json`), restricted directory/file permissions, atomic writes and symlink refusal. This is a local credential file, **not encrypted storage**; permissions do not protect against malicious processes running as the same user.

Amazon password/MFA stay on Amazon. The current default-US flow uses the default browser and a temporary loopback return page. Pasting a returned address transfers a secret; separate **Save new Session** approval authorizes replacement. Cancelling before approval preserves the previous Session. There is no promised browser-provenance check, marketplace coverage, Session lifetime or token-rotation guarantee. Fresh human login/re-login remains independently unproven; synthetic checks do not establish it.

Browser history/sync, clipboard managers, extensions, screen sharing and same-user processes can expose the returned address. The helper does not read or clear the browser profile/history/clipboard or change privacy/proxy settings. Loopback must bypass proxies. The unofficial upstream API can change without notice.

## If a secret may have leaked

Stop sharing the artifact and remove public copies where possible, but assume copies may remain. **Sign out** removes the local Session only; it does not revoke server authorization or erase browser/clipboard history. Use the account provider's available security/session-management controls and seek its support if needed; do not assume a particular revocation mechanism exists.

Plugin disable/removal does not delete the Session stored outside its clone. If local credential deletion is intended, Sign out before disabling/removing the plugin. See the [usage guide](docs/usage.md#update-re-login-and-removal) ([русский](docs/usage.ru.md#обновление-повторный-вход-и-удаление)).
