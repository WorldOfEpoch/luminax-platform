#!/usr/bin/env bash
# One local Compose entry point. Configuration belongs to payload/tools.
set -euo pipefail
apply=0
action=up
profiles=()
while (($#)); do
  case "$1" in
    --apply) apply=1; shift ;;
    --profile) [[ $# -ge 2 ]] || { echo 'Missing --profile value' >&2; exit 2; }; profiles+=("$2"); shift 2 ;;
    --action) [[ $# -ge 2 ]] || { echo 'Missing --action value' >&2; exit 2; }; action="$2"; shift 2 ;;
    --help|-h) printf '%s\n' 'Usage: bash luminax.sh --profile core [--profile FEATURE] [--action up|down|status] [--apply]' 'Default: print the plan. Initialize configuration with payload/tools/initialize_local_config.py first.'; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
case "$action" in up|down|status) ;; *) echo 'Action must be up, down, or status.' >&2; exit 2 ;; esac
for profile in "${profiles[@]}"; do
  [[ "$profile" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo 'Invalid profile name.' >&2; exit 2; }
done
printf 'PLAN: local Compose action=%s profiles=%s\n' "$action" "${profiles[*]:-(none selected)}"
if (( ! apply )); then
  printf '%s\n' 'No configuration was loaded and no Docker command ran. Add --apply after reviewing the selected profiles.'
  exit 0
fi
[[ " ${profiles[*]} " == *' core '* ]] || { echo 'Explicit --profile core is required, including with optional profiles.' >&2; exit 2; }
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
payload_dir="$repo_dir/payload"
[[ -f "$payload_dir/.env" && -s "$payload_dir/runtime/luminax.htpasswd" ]] || {
  echo 'Initialize local configuration first: python payload/tools/initialize_local_config.py' >&2; exit 2;
}
command -v docker >/dev/null || { echo 'Docker with Compose is required; install and start it deliberately.' >&2; exit 2; }
args=(compose --project-directory "$payload_dir" --env-file "$payload_dir/.env" -f "$payload_dir/docker-compose.yml")
for profile in "${profiles[@]}"; do args+=(--profile "$profile"); done
case "$action" in
  up) args+=(up -d --build) ;;
  down) args+=(down) ;;
  status) args+=(ps) ;;
esac
exec docker "${args[@]}"
