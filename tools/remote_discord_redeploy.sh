#!/usr/bin/env bash
# Reuse the same deployment owner and explicit configuration.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$script_dir/remote_discord_deploy.sh" "$@"
