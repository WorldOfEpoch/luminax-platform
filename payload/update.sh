#!/bin/sh
set -eu
base_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# The tracked launcher owns plan/apply and explicit profiles. No hidden rebuild.
exec bash "$base_dir/../luminax.sh" "$@"
