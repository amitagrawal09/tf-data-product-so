#!/bin/bash
# Runs inside the Azure deployment-script container (Azure CLI image), signed in as the deployer
# managed identity. Downloads the repository and runs ./deploy.sh azure.
set -euo pipefail

log() { printf '[oneclick %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

: "${REPO_URL:?}" "${REPO_REF:?}" "${RG:?}" "${ENVIRONMENT:?}"

# ---------------------------------------------------------------- source code
owner_repo="${REPO_URL#https://github.com/}"
owner_repo="${owner_repo%.git}"
owner_repo="${owner_repo%/}"
work="$(mktemp -d)"
log "Downloading ${owner_repo}@${REPO_REF}"
curl_args=(-fsSL --retry 5 --retry-delay 5 -H "Accept: application/vnd.github+json" -o "$work/src.tgz")
if [[ -n "${REPO_TOKEN:-}" ]]; then
  curl_args+=(-H "Authorization: Bearer ${REPO_TOKEN}")
fi
if ! curl "${curl_args[@]}" "https://api.github.com/repos/${owner_repo}/tarball/${REPO_REF}"; then
  log "ERROR: could not download the repository. For a private repository supply gitHubToken (read access to contents)."
  exit 1
fi
mkdir -p "$work/src"
tar -xzf "$work/src.tgz" -C "$work/src" --strip-components=1
cd "$work/src"
chmod +x deploy.sh
log "Source ready ($(du -sh . | cut -f1))"

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
./deploy.sh azure

# ---------------------------------------------------------------- outputs for the template
outputs="$(az deployment group show -g "$RG" -n "tfso-${ENVIRONMENT}" --query properties.outputs -o json)"
console="$(echo "$outputs" | jq -r '.staticWebsiteUrl.value // ""')"
gateway="$(echo "$outputs" | jq -r '.apimGatewayUrl.value // ""')"
jq -n --arg c "$console" --arg g "${gateway%/}" --arg r "$RG" \
  '{consoleUrl: $c, apiGatewayUrl: $g, resourceGroup: $r}' >"$AZ_SCRIPTS_OUTPUT_PATH"
log "Done. Console: ${console}  API gateway: ${gateway}"
