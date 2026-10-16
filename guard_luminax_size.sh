#!/usr/bin/env bash
# The public launcher no longer embeds thousands of source lines.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${1:-$script_dir/luminax.sh}"
[[ -f "$target" ]] || { echo 'Launcher file missing.' >&2; exit 2; }
bash -n "$target"
[[ -f "$script_dir/payload/docker-compose.yml" ]] || { echo 'Tracked Compose source missing.' >&2; exit 2; }
printf '%s\n' 'PASS: launcher parses and Compose source exists. Runtime health is not established.'
