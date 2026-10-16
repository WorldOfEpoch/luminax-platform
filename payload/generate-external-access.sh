#!/bin/sh
set -eu
base_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
echo 'This compatibility command generates local configuration only; it opens no external access.'
exec python3 "$base_dir/tools/initialize_local_config.py" "$@"
