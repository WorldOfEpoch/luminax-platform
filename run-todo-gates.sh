#!/usr/bin/env bash
# Offline source gates replace the retired embedded installer's live failure tests.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$script_dir/guard_luminax_size.sh"
python3 -m unittest discover -s "$script_dir/tools/tests" -p 'test_*.py'
