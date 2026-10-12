#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() {
  printf '%s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    say "FAIL: required command not found: $1"
    exit 1
  fi
}

require_command python3

say "==> Python syntax"
python3 -m py_compile bin/eero-helper lib/eero_api.py lib/eero_session.py lib/eero_snapshot.py lib/eero_cli.py lib/eero_amazon.py lib/eero_amazon_return.py

say "==> Python unit tests"
python3 -m unittest discover -s tests -p 'test_*.py' -v

say "==> JavaScript tests"
if compgen -G "tests/*.mjs" >/dev/null || compgen -G "tests/*.cjs" >/dev/null; then
  require_command node
  for script in tests/*.mjs tests/*.cjs; do
    [[ -e $script ]] || continue
    node "$script"
  done
else
  say "SKIP: no tests/*.mjs or tests/*.cjs found"
fi

say "==> Shell syntax"
while IFS= read -r script; do
  bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print | sort)

if command -v shellcheck >/dev/null 2>&1; then
  say "==> shellcheck"
  while IFS= read -r script; do
    shellcheck "$script"
  done < <(find scripts -type f -name '*.sh' -print | sort)
else
  say "SKIP: shellcheck is not installed"
fi

if compgen -G "*.qml" >/dev/null; then
  # qmllint ships with Qt6 but isn't always on PATH (e.g. Arch's qt6-declarative
  # puts it under /usr/lib/qt6/bin instead).
  if command -v qmllint >/dev/null 2>&1; then
    QMLLINT_BIN="qmllint"
  elif [[ -x /usr/lib/qt6/bin/qmllint ]]; then
    QMLLINT_BIN="/usr/lib/qt6/bin/qmllint"
  else
    QMLLINT_BIN=""
  fi
  if [[ -n $QMLLINT_BIN ]]; then
    OMARCHY_SHELL_PATH="${OMARCHY_PATH:-/usr/share/omarchy}/shell"
    if [[ -d $OMARCHY_SHELL_PATH ]]; then
      say "==> qmllint"
      # qs.Commons/qs.Ui only resolve inside a running omarchy-shell (they
      # depend on its full QML_IMPORT_PATH and Singleton registration, which
      # -I alone does not reproduce), so every plugin file gets a cascade of
      # import/unresolved-type/unqualified-access warnings for them -- the
      # reference n501.karaoke plugin lints with the exact same warning
      # classes. qmllint's own exit code already reflects only genuine
      # errors (it stays 0 for warnings-only runs), so no extra filtering is
      # needed here; a nonzero exit still fails this script.
      for qml in *.qml; do
        "$QMLLINT_BIN" -I "$OMARCHY_SHELL_PATH" "$qml"
      done
    else
      say "SKIP: qmllint found, but $OMARCHY_SHELL_PATH is unavailable"
    fi
  else
    say "SKIP: qmllint is not installed"
  fi
else
  say "SKIP: no *.qml files found (arrives in a later ticket)"
fi

if [[ -f manifest.json ]]; then
  if command -v omarchy >/dev/null 2>&1; then
    say "==> Omarchy plugin validation"
    omarchy plugin validate "$ROOT"
  elif [[ -n ${OMARCHY_VALIDATOR:-} && -x $OMARCHY_VALIDATOR ]]; then
    say "==> Omarchy plugin validation"
    "$OMARCHY_VALIDATOR" "$ROOT"
  else
    say "SKIP: Omarchy plugin validator is unavailable"
  fi
else
  say "SKIP: manifest.json not present (arrives in ticket 02)"
fi

say "PASS: portable validation completed"
