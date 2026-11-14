#!/bin/bash
# Runs inside the Azure deployment-script container (Azure CLI image), signed in as the deployer
# managed identity. Downloads the repository and runs ./deploy.sh azure.
set -euo pipefail

log() { printf '[oneclick %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

: "${REPO_URL:?}" "${REPO_REF:?}" "${RG:?}" "${ENVIRONMENT:?}"

# ---------------------------------------------------------------- tools
# The Azure CLI deployment-script image is minimal (no curl/tar/jq in recent versions). Everything this script
# needs is done with python3 (always present: the Azure CLI runs on it); curl is installed best-effort for deploy.sh.
PY="$(command -v python3 || command -v python || true)"
[[ -n "$PY" ]] || { echo "ONE-CLICK DEPLOYMENT FAILED: python3 not found in the Azure CLI container" >&2; exit 1; }
if ! command -v curl >/dev/null 2>&1; then
  if tdnf install -y curl >/dev/null 2>&1 || apk add --no-cache curl >/dev/null 2>&1 \
     || { apt-get update -qq && apt-get install -y -qq curl; } >/dev/null 2>&1; then
    log "installed curl"
  else
    log "curl unavailable - deploy.sh uses its python fallback"
  fi
fi

# ---------------------------------------------------------------- source code
owner_repo="${REPO_URL#https://github.com/}"
owner_repo="${owner_repo%.git}"
owner_repo="${owner_repo%/}"
work="$(mktemp -d)"
log "Downloading ${owner_repo}@${REPO_REF}"
if ! "$PY" - "$owner_repo" "$REPO_REF" "$work/src" <<'PYEOF'
import io, os, sys, tarfile, time, urllib.request
repo, ref, dest = sys.argv[1:4]
url = f"https://api.github.com/repos/{repo}/tarball/{ref}"
headers = {"Accept": "application/vnd.github+json", "User-Agent": "tfso-oneclick"}
if os.environ.get("REPO_TOKEN"):
    headers["Authorization"] = "Bearer " + os.environ["REPO_TOKEN"]
data = None
for attempt in range(5):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
            data = r.read()
        break
    except Exception as exc:  # noqa: BLE001
        print(f"download attempt {attempt + 1} failed: {exc}", file=sys.stderr)
        time.sleep(5)
if data is None:
    sys.exit(1)
os.makedirs(dest, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
    members = []
    for m in tf.getmembers():
        parts = m.name.split("/", 1)
        if len(parts) < 2 or not parts[1]:
            continue
        m.name = parts[1]                       # strip the top-level "<owner>-<repo>-<sha>/" folder
        if m.name.startswith("/") or ".." in m.name.split("/"):
            continue
        members.append(m)
    try:
        tf.extractall(dest, members=members, filter="data")   # Python ≥ 3.12: safe extraction
    except TypeError:
        tf.extractall(dest, members=members)
print(f"extracted {len(members)} entries")
PYEOF
then
  echo "ONE-CLICK DEPLOYMENT FAILED: could not download ${owner_repo}@${REPO_REF}. For a private repository supply gitHubToken (read access to contents)." >&2
  exit 1
fi
cd "$work/src"
chmod +x deploy.sh
log "Source ready"

# ---------------------------------------------------------------- wait for the Owner role assignment
for i in $(seq 1 30); do
  rg_id="$(az group show -n "$RG" --query id -o tsv 2>/dev/null || true)"
  if [[ -n "$rg_id" ]] && az role assignment list --scope "$rg_id" \
       --query "[?principalId=='${DEPLOYER_PRINCIPAL_ID}' && roleDefinitionName=='Owner'] | length(@)" -o tsv 2>/dev/null \
       | grep -q '^[1-9]'; then
    log "deployer role assignment visible"
    break
  fi
  log "waiting for the deployer role assignment to propagate ($i/30)"
  sleep 10
done
sleep 30  # authorization caches

# ---------------------------------------------------------------- python for smoke test / demo data
if [[ "${SKIP_SMOKE:-false}" != "true" || "${LOAD_DEMO_DATA:-false}" == "true" ]]; then
  if ! python3 -m venv "$work/venv-probe" >/dev/null 2>&1; then
    log "python3 venv not available in this container - platform will deploy, smoke test and demo data are skipped"
    export SKIP_SMOKE=true LOAD_DEMO_DATA=false
  fi
fi

# ---------------------------------------------------------------- deploy
export ASSUME_YES=true PYTHON="${PYTHON:-python3}"
log "Running ./deploy.sh azure (environment=${ENVIRONMENT}, rg=${RG}, apim=${APIM_SKU:-Developer})"
set +e
./deploy.sh azure 2>&1 | tee "$work/deploy.log"
status=${PIPESTATUS[0]}
set -e
if [[ $status -ne 0 ]]; then
  # stderr becomes the deployment script's error message in the portal - put the cause there.
  {
    echo "ONE-CLICK DEPLOYMENT FAILED (deploy.sh exit ${status}). Last lines of the deployment log:"
    sed 's/\x1b\[[0-9;]*m//g' "$work/deploy.log" | grep -v '^\s*$' | tail -25
    echo "Full log: resource group ${RG} > ds-${PREFIX:-tfso}-${ENVIRONMENT}-deploy (Show hidden types) > Logs"
  } >&2
  exit "$status"
fi

# ---------------------------------------------------------------- outputs for the template
az deployment group show -g "$RG" -n "tfso-${ENVIRONMENT}" --query properties.outputs -o json >"$work/outputs.json"
"$PY" - "$work/outputs.json" "$RG" "$AZ_SCRIPTS_OUTPUT_PATH" <<'PYEOF'
import json, sys
outs = json.load(open(sys.argv[1]))
val = lambda k: (outs.get(k) or {}).get("value") or ""
result = {"consoleUrl": val("staticWebsiteUrl"), "apiGatewayUrl": val("apimGatewayUrl").rstrip("/"), "resourceGroup": sys.argv[2]}
json.dump(result, open(sys.argv[3], "w"))
print(f"Done. Console: {result['consoleUrl']}  API gateway: {result['apiGatewayUrl']}")
PYEOF
