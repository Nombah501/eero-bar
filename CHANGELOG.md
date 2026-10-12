# Changelog

## 0.2.0 — initial standalone source snapshot

This is a publication-preparation snapshot, not a tagged GitHub release. It starts
a clean repository history without exporting the surrounding workstation project.

### Included

- Native Omarchy bar Widget and Popup with Overview, Clients, Nodes and Settings.
- Client search and combinable connection, Guest, Pause, Node and Profile filters;
  Client and Node details; Client/Profile Pause controls.
- Guest network and Node LED controls, confirmed Node/Network reboot, and
  Gateway-to-internet speedtest requests with acknowledgement/readback feedback.
- Host-persisted polling interval, Client count, density and offline visibility.
- Email/phone verification and default-browser Amazon sign-in with a bounded
  local return page and separate explicit Session save consent.
- A one-shot Python standard-library Helper, guarded atomic Session storage,
  synthetic Python/JavaScript/QML checks and a portable validation entry point.
- English and Russian user documentation, MIT license and portable CI workflow.

### Corrected before publication

- Local Amazon return pages use `Referrer-Policy: same-origin`, preserving the
  exact local Origin on native form submissions. Null/foreign Origin requests
  remain rejected; cross-origin navigation does not send a referrer.

### Verification limits

Native synthetic UI, local Zen return, installed read-only status and preference
reload/restart checks have been exercised locally. These do not establish fresh
human Amazon login and repeated-login acceptance, every real Network Action,
compatibility with every Omarchy version, or other Amazon marketplaces. GitHub
CI results are not available before the repository is published.
