#!/bin/bash
# Deliberate operator entry point; no configuration or service is touched by default.
if [[ "${1:-}" != "--apply" ]]; then
  printf '%s\n' 'PLAN: tools/sync_payload_to_base.sh (no action performed).' 'Review the source and rerun with --apply to execute this configured operator action.'
  exit 0
fi
shift
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="${PAYLOAD_DIR:-$SCRIPT_DIR/../payload}"
BASE_DIR="${BASE_DIR:-${LUMINAX_REMOTE_DIR:?Set LUMINAX_REMOTE_DIR}}"

if [ ! -d "$PAYLOAD_DIR" ]; then
  echo "[sync] payload directory missing: $PAYLOAD_DIR"
  exit 1
fi

[[ "$BASE_DIR" == /* && "$BASE_DIR" != / ]] || { echo 'Choose an explicit absolute destination directory below the filesystem root.' >&2; exit 2; }
[[ ! -e "$BASE_DIR" ]] || { echo 'Destination must not exist; preserve existing deployments and data.' >&2; exit 2; }
command -v rsync >/dev/null || { echo 'rsync is required for excluded runtime data handling.' >&2; exit 2; }
mkdir -p "$BASE_DIR"
rsync -a --exclude='.env' --exclude='.env.*' --exclude='runtime/' --exclude='data/' --exclude='logs/' --exclude='__pycache__/' --exclude='node_modules/' --exclude='.venv/' "$PAYLOAD_DIR"/ "$BASE_DIR"/
echo '[sync] source copied; no services were started.'
