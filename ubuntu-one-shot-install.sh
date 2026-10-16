#!/usr/bin/env bash
# Retained entry-point name; all startup uses the tracked Compose owner.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" != --apply ]]; then
  printf '%s\n' 'PLAN: initialize configuration, then start explicitly selected Compose profiles.' 'Prerequisites: Python 3, OpenSSL, Docker with Compose. No packages or boot services are installed.' 'Usage: bash ubuntu-one-shot-install.sh --apply --profile core [--profile FEATURE]'
  exit 0
fi
shift
command -v python3 >/dev/null || { echo 'Install Python 3 deliberately first.' >&2; exit 2; }
command -v docker >/dev/null || { echo 'Install and start Docker with Compose deliberately first.' >&2; exit 2; }
[[ " $* " == *' --profile core '* ]] || { echo 'Select --profile core explicitly.' >&2; exit 2; }
if [[ ! -f "$script_dir/payload/.env" || ! -s "$script_dir/payload/runtime/luminax.htpasswd" ]]; then
  python3 "$script_dir/payload/tools/initialize_local_config.py"
fi
exec bash "$script_dir/luminax.sh" --apply "$@"
