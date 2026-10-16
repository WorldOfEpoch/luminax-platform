#!/usr/bin/env bash
# Run on an operator-selected host from an explicitly prepared public checkout.
set -euo pipefail
if [[ "${1:-}" != --apply ]]; then
  printf '%s\n' 'PLAN: start the Discord Compose profile in LUMINAX_REMOTE_DIR.' 'No source is uploaded or connection opened. Use --apply after configuring the target checkout and its credentials.'
  exit 0
fi
: "${LUMINAX_REMOTE_DIR:?Set LUMINAX_REMOTE_DIR to the public checkout on this host}"
[[ -f "$LUMINAX_REMOTE_DIR/luminax.sh" ]] || { echo 'Target must be a prepared public checkout.' >&2; exit 2; }
exec bash "$LUMINAX_REMOTE_DIR/luminax.sh" --profile core --profile discord --apply
