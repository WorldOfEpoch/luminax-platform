#!/bin/sh
set -eu
payload_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf '%s\n' 'Planning local Underfoot source services. Game binaries and an allocator backend remain operator-supplied.'
exec bash "$payload_dir/../luminax.sh" --profile core --profile underfoot "$@"
