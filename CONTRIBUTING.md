# Contributing

Keep changes small, source-grounded and dependency-free where practical. The helper uses Python 3's standard library; use the existing `unittest`, Node assertion scripts and QML fixtures rather than introducing another test framework or runtime dependency.

## Local work

Clone using the actual repository URL supplied by its owner (for example, GitHub's **Code → Copy URL**), then work from the clone root. The root is also the Omarchy plugin root. Do not use a real Session or modify your active bar configuration just to run a portable regression.

Read [CONTEXT.md](CONTEXT.md) for terminology and the [architecture decision](docs/adr/0001-plugin-architecture.md), [Service API](docs/service-api.md) and [Helper protocol](docs/helper-protocol.md) for boundaries. Keep API traffic and Session writes in the helper; QML receives normalized snapshots and safe progress, never tokens or raw callbacks.

## Existing validation gates

Run the existing aggregate gate from the repository root:

```sh
bash scripts/validate.sh
```

It checks Python syntax, stdlib unittest discovery, each Node `.mjs`/`.cjs` test, shell syntax, optional shellcheck, QML lint when the tool and host sources exist, and Omarchy plugin validation when available. Python 3 is required; Node is required for the checked-in JavaScript tests. Some host-dependent gates can be skipped. A final portable PASS does **not** mean all optional/native gates ran: report skips and warnings separately.

Focused commands for the existing suites:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/eero_model.test.cjs
node tests/native_navigation.test.cjs
python3 -m unittest tests.test_service_lifecycle -v
```

The Service lifecycle suite uses offscreen Quickshell, temporary state and a fake helper/no-network guard. Native page tests additionally need the host UI sources at `/usr/share/omarchy/shell/Ui`; absent prerequisites cause skips. QML lint outside the running host can warn about native imports: preserve the existing gate's exit-code handling instead of suppressing diagnostics indiscriminately.

Distinguish three kinds of evidence in a change report:

- **Portable:** syntax, model/helper tests and fake API responses.
- **Native synthetic:** actual QML components or a local browser return with fabricated inputs. These do not prove live authentication or network mutation.
- **Live:** explicitly authorized human login/re-login or real network behaviour, with scope and outcome recorded without secrets. Never claim live acceptance based on fixtures or an already installed Session.

## Safe tests and reviews

Use synthetic names, reserved example domains and fabricated identifiers. Isolate state in temporary directories using the existing fixture patterns. Never commit Session files, tokens, OTPs, passwords, callbacks, raw account responses or captured browser profiles. Do not add real HTTP/network-changing behaviour to ordinary regressions. A demonstration must not silently use production credentials or host settings.

For authentication changes cover cancellation, stale attempts, timeouts and save approval without weakening the private-stdin boundary or previous-Session preservation. For controls distinguish acknowledgement from readback and do not automatically repeat mutations against a changed network.

Submit a concise description of behaviour, changed contracts/docs, exact validation commands and results, skipped gates and remaining native/live gaps. Update both README/usage languages when user-facing behaviour changes. Send security-sensitive findings through the process in [SECURITY.md](SECURITY.md), not a public reproduction containing secrets.
