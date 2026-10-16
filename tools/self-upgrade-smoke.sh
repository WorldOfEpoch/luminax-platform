#!/usr/bin/env bash
# Deliberate operator entry point; no configuration or service is touched by default.
if [[ "${1:-}" != "--apply" ]]; then
  printf '%s\n' 'PLAN: tools/self-upgrade-smoke.sh (no action performed).' 'Review the source and rerun with --apply to execute this configured operator action.'
  exit 0
fi
shift
set -euo pipefail

UPGRADE_URL="${UPGRADE_URL:-http://127.0.0.1:8130}"
ADMIN_URL="${ADMIN_URL:-http://127.0.0.1:8040}"
SERVICE="${SERVICE:-lumi-upgrade-agent}"
APPLY="${APPLY:-0}"
CONTROL_TOKEN="${CONTROL_TOKEN:-}"
ERROR_EXCERPT="${ERROR_EXCERPT:-Traceback (most recent call last):\\n  File \"/app/main.py\", line 1, in <module>\\nNameError: self-upgrade smoke sentinel\\n}"

echo "[smoke] requesting Codex-backed proposal from ${UPGRADE_URL}"
ANALYZE_PAYLOAD="$(cat <<JSON
{"service":"${SERVICE}","error_excerpt":"${ERROR_EXCERPT}","log_path":"${LUMINAX_REMOTE_DIR:?Set LUMINAX_REMOTE_DIR}/logs/new/self_upgrade_smoke.log","trigger_kind":"smoke_test","context":{"source":"self-upgrade-smoke"},"apply_if_safe":false,"file_hints":["${SERVICE}/main.py"]}
JSON
)"

ANALYZE_OUT="$(curl --connect-timeout 5 --max-time 45 -fsS "${UPGRADE_URL}/analyze" -H 'Content-Type: application/json' -d "${ANALYZE_PAYLOAD}")"
echo "${ANALYZE_OUT}" | python3 -c 'import json, sys
data = json.load(sys.stdin)
proposal = data.get("proposal") or {}
if not proposal.get("proposal_id"):
    raise SystemExit("proposal_id missing")
excerpt = proposal.get("error_excerpt") or ""
if "self-upgrade smoke sentinel" not in excerpt:
    raise SystemExit("error excerpt was not preserved in proposal")
print(f"[smoke] proposal_id={proposal.get('"'"'proposal_id'"'"')} risk={proposal.get('"'"'risk'"'"')} confidence={proposal.get('"'"'confidence'"'"')}")'

LATEST_OUT="$(curl --connect-timeout 5 --max-time 45 -fsS "${UPGRADE_URL}/latest")"
echo "${LATEST_OUT}" | python3 -c 'import json, sys
data = json.load(sys.stdin)
row = data.get("row") or {}
if "self-upgrade smoke sentinel" not in (row.get("error_excerpt") or ""):
    raise SystemExit("latest proposal does not expose the triggering error excerpt")
print(f"[smoke] latest proposal visible: {row.get('"'"'proposal_id'"'"')}")'

if [[ "${APPLY}" == "1" ]]; then
  if [[ -z "${CONTROL_TOKEN}" ]]; then
    echo "[smoke] APPLY=1 requires CONTROL_TOKEN"
    exit 2
  fi
  PROPOSAL_ID="$(echo "${LATEST_OUT}" | python3 -c "import json,sys; print((json.load(sys.stdin).get('row') or {}).get('proposal_id',''))")"
  echo "[smoke] attempting guarded apply through admin-api for proposal ${PROPOSAL_ID}"
  APPLY_PAYLOAD="{\"token\":\"${CONTROL_TOKEN}\",\"proposal_id\":\"${PROPOSAL_ID}\",\"allow_medium_risk\":false}"
  curl --connect-timeout 5 --max-time 45 -fsS "${ADMIN_URL}/self-upgrade/apply" -H 'Content-Type: application/json' -d "${APPLY_PAYLOAD}" | python3 -c 'import json, sys
data = json.load(sys.stdin)
print(json.dumps({"status": data.get("status"), "detail": data.get("detail", ""), "proposal_id": (data.get("proposal") or {}).get("proposal_id")}, indent=2))'
  curl --connect-timeout 5 --max-time 45 -fsS "${UPGRADE_URL}/health" >/dev/null
  echo "[smoke] apply flow completed; upgrade-agent health endpoint still reachable"
else
  echo "[smoke] proposal generation flow verified. Re-run with APPLY=1 CONTROL_TOKEN=... to exercise the guarded apply path."
fi
