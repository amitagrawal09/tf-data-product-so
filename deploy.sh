#!/usr/bin/env bash
# =====================================================================================
# Sales Order data product – single end-to-end deployment script
#
#   ./deploy.sh local         run all 4 services on this machine (SQLite, HTTP bus) + smoke test
#   ./deploy.sh local-stop    stop the local services
#   ./deploy.sh docker        docker compose (Postgres + 4 containers) + smoke test
#   ./deploy.sh docker-down   stop and remove the compose stack (add --volumes to wipe data)
#   ./deploy.sh azure         provision Azure (Bicep), build image in ACR, roll out apps,
#                             publish the console, smoke test through API Management
#   ./deploy.sh test          run the automated test suite (pytest)
#   ./deploy.sh smoke [URL]   run the smoke test against an API base URL
#   ./deploy.sh destroy       delete the Azure resource group (asks for confirmation)
#   ./deploy.sh --help
# =====================================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# ---------- configuration (override with env vars) ----------------------------------
PREFIX="${PREFIX:-tfso}"
LOCATION="${LOCATION:-eastus2}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
RG="${RG:-rg-${PREFIX}-${ENVIRONMENT}}"
APIM_SKU="${APIM_SKU:-Developer}"
APIM_PUBLISHER_EMAIL="${APIM_PUBLISHER_EMAIL:-}"
IMAGE_REPO="${IMAGE_REPO:-tfso-sales-order}"
D365_URL="${D365_URL:-}"
D365_TENANT_ID="${D365_TENANT_ID:-}"
D365_CLIENT_ID="${D365_CLIENT_ID:-}"
D365_CLIENT_SECRET="${D365_CLIENT_SECRET:-}"
D365_COMPANY="${D365_COMPANY:-usmf}"
ISA_SENDER_ID="${ISA_SENDER_ID:-TAYLORFARMS}"
LOCAL_API_KEY="${LOCAL_API_KEY:-local-dev-key}"
# Pick a working interpreter. On Windows `python3` is often the Microsoft Store stub, so probe each candidate.
if [[ -z "${PYTHON:-}" ]]; then
  for _cand in python3 python py; do
    if command -v "$_cand" >/dev/null 2>&1 && "$_cand" -c 'import sys' >/dev/null 2>&1; then PYTHON="$_cand"; break; fi
  done
  PYTHON="${PYTHON:-python3}"
fi
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
# Virtual environments use Scripts\python.exe on Windows (Git Bash / MSYS / Cygwin) and bin/python elsewhere.
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) VENV_PY="$VENV_DIR/Scripts/python.exe" ;;
  *)                    VENV_PY="$VENV_DIR/bin/python" ;;
esac
RUN_DIR="$ROOT_DIR/data/run"
STATE_DIR="$ROOT_DIR/.deploy"
ASSUME_YES="${ASSUME_YES:-false}"
SKIP_SMOKE="${SKIP_SMOKE:-false}"

# name:port pairs for local mode (gateway last so it starts after the backends)
LOCAL_SERVICES=("system:8003" "process:8002" "experience:8001" "gateway:8080")

# ---------- logging -------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[34m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""
fi
log()  { printf '%s[%s]%s %s\n' "$C_BLUE" "$(date +%H:%M:%S)" "$C_RESET" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '%s[fail]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }
step() { printf '\n%s==> %s%s\n' "$C_BOLD" "$*" "$C_RESET"; }

usage() {
  sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<EOF

Environment variables (azure):
  PREFIX=${PREFIX}  LOCATION=${LOCATION}  ENVIRONMENT=${ENVIRONMENT}  RG=${RG}
  APIM_SKU=${APIM_SKU} (Consumption|Developer|Basic|Standard|Premium|BasicV2|StandardV2)
  APIM_PUBLISHER_EMAIL (required for azure)
  D365_URL D365_TENANT_ID D365_CLIENT_ID D365_CLIENT_SECRET D365_COMPANY  (empty D365_URL = D365 simulator)
  ISA_SENDER_ID=${ISA_SENDER_ID}  IMAGE_TAG (default: git short sha or timestamp)
  ASSUME_YES=true  skip confirmations      SKIP_SMOKE=true  skip smoke tests
Generated secrets are kept in .deploy/<environment>.env (git-ignored) so reruns are idempotent.
EOF
}

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed"; }

confirm() {
  local prompt="$1"
  [[ "$ASSUME_YES" == "true" ]] && return 0
  read -r -p "$prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

wait_http() {
  # wait_http <url> <label> [timeout-seconds]
  local url="$1" label="$2" timeout="${3:-90}" i=0
  until curl -fsS -o /dev/null --max-time 3 "$url"; do
    i=$((i + 1))
    if (( i >= timeout )); then
      warn "$label did not become healthy at $url within ${timeout}s"
      return 1
    fi
    sleep 1
  done
  ok "$label healthy ($url)"
}

rand_secret() {
  # URL-safe random secret (no shell/URI-special characters)
  "$PYTHON" -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(int(__import__("sys").argv[1]))))' "${1:-32}"
}

# ---------- python environment ------------------------------------------------------
ensure_python() {
  need "$PYTHON"
  "$PYTHON" - <<'EOF' || die "Python 3.11+ is required"
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF
  if [[ ! -x "$VENV_PY" ]]; then
    step "Creating virtual environment ($VENV_DIR)"
    "$PYTHON" -m venv "$VENV_DIR"
  fi
  local stamp="$VENV_DIR/.tfso-installed"
  if [[ ! -f "$stamp" || requirements.txt -nt "$stamp" || pyproject.toml -nt "$stamp" ]]; then
    step "Installing dependencies"
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -r requirements.txt
    "$VENV_PY" -m pip install --quiet -e .
    touch "$stamp"
  fi
  ok "Python environment ready ($("$VENV_PY" --version))"
}

run_smoke() {
  # run_smoke <api-base> [extra args...]
  local base="$1"; shift
  if [[ "$SKIP_SMOKE" == "true" ]]; then
    warn "SKIP_SMOKE=true – smoke test skipped"
    return 0
  fi
  step "Smoke test against $base"
  local py="$VENV_PY"
  [[ -x "$py" ]] || { ensure_python; }
  "$py" -m tfso.tools.smoke --base "$base" "$@"
  ok "Smoke test passed"
}

# =====================================================================================
# local
# =====================================================================================
local_stop() {
  step "Stopping local services"
  local any=false
  if [[ -d "$RUN_DIR" ]]; then
    local pidfile pid
    for pidfile in "$RUN_DIR"/*.pid; do
      [[ -e "$pidfile" ]] || continue
      pid="$(cat "$pidfile")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        any=true
        log "stopped $(basename "$pidfile" .pid) (pid $pid)"
      fi
      rm -f "$pidfile"
    done
  fi
  [[ "$any" == "true" ]] || log "nothing running"
}

local_up() {
  ensure_python
  mkdir -p "$RUN_DIR" "$ROOT_DIR/data/lakehouse"
  local_stop >/dev/null 2>&1 || true

  step "Starting services (SQLite + local HTTP bus)"
  export TFSO_ENV=local
  export TFSO_BUS_MODE=http
  export TFSO_AUTH_MODE="${TFSO_AUTH_MODE:-apikey}"
  export TFSO_API_KEYS="$LOCAL_API_KEY"
  export TFSO_PROCESS_URL=http://localhost:8002
  export TFSO_SYSTEM_URL=http://localhost:8003
  export TFSO_EXPERIENCE_URL=http://localhost:8001
  export TFSO_D365_MODE="${TFSO_D365_MODE:-simulator}"
  export TFSO_SIM_AUTOPROGRESS="${TFSO_SIM_AUTOPROGRESS:-false}"
  export TFSO_LAKEHOUSE_MODE=file
  export TFSO_FRONTEND_DIR="./frontend"  # relative: Windows Python cannot read Git Bash /c/... paths
  export TFSO_ISA_SENDER_ID="$ISA_SENDER_ID"

  local entry name port
  for entry in "${LOCAL_SERVICES[@]}"; do
    name="${entry%%:*}"; port="${entry##*:}"
    if curl -fsS -o /dev/null --max-time 1 "http://localhost:${port}/health" 2>/dev/null; then
      die "port ${port} is already serving something; run './deploy.sh local-stop' or free the port"
    fi
    case "$name" in
      process) export TFSO_DB_URL="sqlite:///./data/process.db" ;;
      system)  export TFSO_DB_URL="sqlite:///./data/system.db" ;;
      *)       unset TFSO_DB_URL ;;
    esac
    SERVICE="$name" nohup "$VENV_PY" -m uvicorn "tfso.${name}.app:app" \
      --host 0.0.0.0 --port "$port" --proxy-headers \
      >"$RUN_DIR/${name}.log" 2>&1 &
    echo $! >"$RUN_DIR/${name}.pid"
    log "started ${name} on :${port} (pid $!, log data/run/${name}.log)"
  done
  unset TFSO_DB_URL

  for entry in "${LOCAL_SERVICES[@]}"; do
    name="${entry%%:*}"; port="${entry##*:}"
    wait_http "http://localhost:${port}/health" "$name" 60 || {
      tail -n 40 "$RUN_DIR/${name}.log" >&2 || true
      exit 1
    }
  done

  run_smoke "http://localhost:8080/api" --api-key "$LOCAL_API_KEY"

  printf '\n%sLocal deployment ready%s\n' "$C_GREEN$C_BOLD" "$C_RESET"
  cat <<EOF
  Order Operations Console : http://localhost:8080/
  Gateway (APIM stand-in)  : http://localhost:8080/api/{exp|process|system}/...
  Experience API docs      : http://localhost:8001/docs
  Process API docs         : http://localhost:8002/docs   (Order Status API)
  System API docs          : http://localhost:8003/docs
  API key (x-api-key)      : ${LOCAL_API_KEY}
  Logs                     : data/run/*.log      Stop: ./deploy.sh local-stop
EOF
}

# =====================================================================================
# docker
# =====================================================================================
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    die "docker compose is required"
  fi
}

docker_up() {
  need docker
  docker info >/dev/null 2>&1 || die "Docker daemon is not reachable"
  step "Building and starting the compose stack"
  TFSO_API_KEYS="$LOCAL_API_KEY" compose up -d --build
  local entry name port
  for entry in "system:8003" "process:8002" "experience:8001" "gateway:8080"; do
    name="${entry%%:*}"; port="${entry##*:}"
    wait_http "http://localhost:${port}/health" "$name" 180 || { compose logs --tail 40 "$name" >&2; exit 1; }
  done
  run_smoke "http://localhost:8080/api" --api-key "$LOCAL_API_KEY"
  ok "Compose stack ready – console at http://localhost:8080/"
}

docker_down() {
  step "Stopping compose stack"
  compose down "$@"
}

# =====================================================================================
# azure
# =====================================================================================
STATE_FILE=""

load_state() {
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  STATE_FILE="$STATE_DIR/${ENVIRONMENT}.env"
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
  fi
  if [[ -z "${POSTGRES_ADMIN_PASSWORD:-}" ]]; then
    # upper + lower + digit satisfies the Azure PostgreSQL complexity policy; alphanumeric keeps URLs clean
    POSTGRES_ADMIN_PASSWORD="Pg$(rand_secret 28)9"
  fi
  [[ -n "${TFSO_BACKEND_API_KEY:-}" ]] || TFSO_BACKEND_API_KEY="$(rand_secret 40)"
  umask 077
  cat >"$STATE_FILE" <<EOF
# Generated by deploy.sh – DO NOT COMMIT. Reused on every run for environment '${ENVIRONMENT}'.
POSTGRES_ADMIN_PASSWORD='${POSTGRES_ADMIN_PASSWORD}'
TFSO_BACKEND_API_KEY='${TFSO_BACKEND_API_KEY}'
EOF
  umask 022
  ok "Deployment state: ${STATE_FILE#"$ROOT_DIR"/}"
}

deployment_output() {
  # deployment_output <name>
  az deployment group show -g "$RG" -n "$DEPLOYMENT_NAME" --query "properties.outputs.$1.value" -o tsv
}

bicep_deploy() {
  # bicep_deploy <usePlaceholderImage true|false> <imageTag>
  local placeholder="$1" tag="$2" attempt
  for attempt in 1 2 3; do
    log "Bicep deployment '$DEPLOYMENT_NAME' (placeholder=$placeholder, tag=$tag, attempt $attempt)"
    if az deployment group create \
        --resource-group "$RG" \
        --name "$DEPLOYMENT_NAME" \
        --template-file infra/main.bicep \
        --parameters infra/main.parameters.json \
        --parameters \
          prefix="$PREFIX" \
          location="$LOCATION" \
          environment="$ENVIRONMENT" \
          containerImageRepo="$IMAGE_REPO" \
          imageTag="$tag" \
          usePlaceholderImage="$placeholder" \
          postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
          apiKey="$TFSO_BACKEND_API_KEY" \
          apimPublisherEmail="$APIM_PUBLISHER_EMAIL" \
          apimSku="$APIM_SKU" \
          d365Url="$D365_URL" \
          d365TenantId="$D365_TENANT_ID" \
          d365ClientId="$D365_CLIENT_ID" \
          d365ClientSecret="$D365_CLIENT_SECRET" \
          d365Company="$D365_COMPANY" \
          isaSenderId="$ISA_SENDER_ID" \
        --only-show-errors -o none; then
      ok "Bicep deployment succeeded"
      return 0
    fi
    # Usual cause of a first-run failure: RBAC propagation for Key Vault references / AcrPull.
    warn "Deployment attempt $attempt failed; retrying in 60s (RBAC propagation is a common cause)"
    sleep 60
  done
  die "Bicep deployment failed after 3 attempts"
}

azure_up() {
  need az
  need curl
  az account show -o none 2>/dev/null || die "Run 'az login' (and 'az account set -s <subscription>') first"
  [[ -n "$APIM_PUBLISHER_EMAIL" ]] || die "APIM_PUBLISHER_EMAIL is required, e.g. APIM_PUBLISHER_EMAIL=owner@example.com ./deploy.sh azure"
  [[ "$PREFIX" =~ ^[a-z][a-z0-9]{1,7}$ ]] || die "PREFIX must be 2-8 lowercase letters/digits starting with a letter"
  ensure_python   # smoke test + secret generation

  local sub_name
  sub_name="$(az account show --query name -o tsv)"
  step "Deploying environment '${ENVIRONMENT}' to subscription '${sub_name}', resource group '${RG}' (${LOCATION})"
  az config set bicep.use_binary_from_path=false --only-show-errors >/dev/null 2>&1 || true
  az bicep install --only-show-errors >/dev/null 2>&1 || az bicep upgrade --only-show-errors >/dev/null 2>&1 || true
  local provider
  for provider in Microsoft.App Microsoft.ApiManagement Microsoft.ContainerRegistry Microsoft.ServiceBus \
                  Microsoft.EventHub Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.CognitiveServices \
                  Microsoft.OperationalInsights Microsoft.Insights Microsoft.Storage Microsoft.ManagedIdentity; do
    az provider register --namespace "$provider" --only-show-errors -o none || true
  done

  load_state
  DEPLOYMENT_NAME="tfso-${ENVIRONMENT}"

  step "1/7 Resource group"
  az group create -n "$RG" -l "$LOCATION" \
    --tags data-product=sales-order environment="$ENVIRONMENT" managed-by=deploy.sh -o none
  ok "$RG"

  local image_tag
  image_tag="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
  if ! git diff --quiet HEAD 2>/dev/null; then
    image_tag="${image_tag}-$(date +%H%M%S)"
  fi

  step "2/7 Platform (Bicep)"
  local acr_name="" image_exists=false
  if az deployment group show -g "$RG" -n "$DEPLOYMENT_NAME" -o none 2>/dev/null; then
    acr_name="$(deployment_output acrName || true)"
  fi
  if [[ -n "$acr_name" ]] && az acr repository show-tags -n "$acr_name" --repository "$IMAGE_REPO" -o tsv 2>/dev/null | grep -q .; then
    image_exists=true
  fi
  if [[ "$image_exists" == "false" ]]; then
    log "No application image in ACR yet – provisioning with a placeholder image first"
    bicep_deploy true "$image_tag"
    acr_name="$(deployment_output acrName)"
  fi

  step "3/7 Build image in ACR ($acr_name/$IMAGE_REPO:$image_tag)"
  az acr build --registry "$acr_name" \
    --image "${IMAGE_REPO}:${image_tag}" --image "${IMAGE_REPO}:latest" \
    --file docker/Dockerfile . --only-show-errors
  ok "Image pushed"

  step "4/7 Roll out the Tier-2 APIs with the new image"
  bicep_deploy false "$image_tag"

  local apim_name apim_url web_account web_url sub_id app_names
  apim_name="$(deployment_output apimName)"
  apim_url="$(deployment_output apimGatewayUrl)"
  web_account="$(deployment_output staticWebsiteStorageAccount)"
  web_url="$(deployment_output staticWebsiteUrl)"
  app_names="$(deployment_output containerAppNames)"
  sub_id="$(az account show --query id -o tsv)"

  local fqdn
  for fqdn in $(az deployment group show -g "$RG" -n "$DEPLOYMENT_NAME" --query 'properties.outputs.containerAppFqdns.value.*' -o tsv); do
    wait_http "https://${fqdn}/health" "$fqdn" 240 || warn "continuing; check 'az containerapp logs show' for $fqdn"
  done

  step "5/7 APIM console subscription key"
  local sub_key
  sub_key="$(az rest --method post \
    --url "https://management.azure.com/subscriptions/${sub_id}/resourceGroups/${RG}/providers/Microsoft.ApiManagement/service/${apim_name}/subscriptions/order-console/listSecrets?api-version=2022-08-01" \
    --query primaryKey -o tsv)"
  [[ -n "$sub_key" ]] || die "Could not read the APIM console subscription key"
  ok "Subscription key retrieved for 'order-console'"

  step "6/7 Publish the Order Operations Console (static website)"
  az storage blob service-properties update --account-name "$web_account" \
    --static-website --index-document index.html --404-document index.html \
    --auth-mode login --only-show-errors -o none 2>/dev/null \
  || az storage blob service-properties update --account-name "$web_account" \
    --static-website --index-document index.html --404-document index.html \
    --account-key "$(az storage account keys list -g "$RG" -n "$web_account" --query '[0].value' -o tsv)" \
    --only-show-errors -o none

  local build_dir
  build_dir="$(mktemp -d)"
  trap 'rm -rf "$build_dir"' RETURN
  cp -R frontend/. "$build_dir/"
  cat >"$build_dir/config.js" <<EOF
// Generated by deploy.sh for environment '${ENVIRONMENT}' – $(date -u +%Y-%m-%dT%H:%M:%SZ)
window.TFSO_CONFIG = {
  apiBase: "${apim_url}",
  subscriptionKey: "${sub_key}",
  apiKey: "",
  environment: "${ENVIRONMENT}"
};
EOF
  local deployer_id
  deployer_id="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
  if [[ -n "$deployer_id" ]]; then
    az role assignment create --assignee-object-id "$deployer_id" --assignee-principal-type User \
      --role "Storage Blob Data Contributor" \
      --scope "/subscriptions/${sub_id}/resourceGroups/${RG}/providers/Microsoft.Storage/storageAccounts/${web_account}" \
      --only-show-errors -o none 2>/dev/null || true
  fi
  local uploaded=false attempt
  for attempt in 1 2 3 4 5; do
    if az storage blob upload-batch --account-name "$web_account" --destination "\$web" \
         --source "$build_dir" --overwrite --auth-mode login --only-show-errors -o none 2>/dev/null; then
      uploaded=true; break
    fi
    log "waiting for Storage RBAC propagation (attempt $attempt)"
    sleep 20
  done
  if [[ "$uploaded" == "false" ]]; then
    warn "Entra ID upload not yet authorized – falling back to account key"
    az storage blob upload-batch --account-name "$web_account" --destination "\$web" \
      --source "$build_dir" --overwrite --only-show-errors -o none \
      --account-key "$(az storage account keys list -g "$RG" -n "$web_account" --query '[0].value' -o tsv)"
  fi
  ok "Console published to ${web_url}"

  step "7/7 Smoke test through API Management"
  run_smoke "${apim_url%/}" --subscription-key "$sub_key"

  printf '\n%sAzure deployment complete (%s)%s\n' "$C_GREEN$C_BOLD" "$ENVIRONMENT" "$C_RESET"
  cat <<EOF
  Resource group           : ${RG}
  Order Operations Console : ${web_url}
  API Management gateway   : ${apim_url}
     Experience APIs       : ${apim_url}/exp/...
     Order Status API      : ${apim_url}/process/v1/orders/{orderId}/status
     System APIs           : ${apim_url}/system/...   (non-prod only)
  Container apps           : $(echo "$app_names" | tr '\n\t' '  ')
  Image                    : ${acr_name}.azurecr.io/${IMAGE_REPO}:${image_tag}
  Subscription key header  : Ocp-Apim-Subscription-Key (stored in the console config.js)
  Secrets                  : Key Vault $(deployment_output keyVaultName); local copy ${STATE_FILE#"$ROOT_DIR"/}
EOF
}

azure_destroy() {
  need az
  az account show -o none 2>/dev/null || die "Run 'az login' first"
  az group show -n "$RG" -o none 2>/dev/null || { log "Resource group $RG does not exist"; return 0; }
  warn "This permanently deletes resource group '$RG' and everything in it."
  confirm "Delete $RG?" || die "Aborted"
  az group delete -n "$RG" --yes --no-wait
  ok "Deletion of $RG started (Key Vault and APIM are soft-deleted; purge them to reuse names)"
}

run_tests() {
  ensure_python
  step "Running tests"
  "$VENV_PY" -m pip install --quiet pytest >/dev/null
  "$VENV_PY" -m pytest -q "$@"
}

# =====================================================================================
main() {
  local cmd="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$cmd" in
    local)        local_up ;;
    local-stop)   local_stop ;;
    docker)       docker_up ;;
    docker-down)  docker_down "$@" ;;
    azure)        azure_up ;;
    destroy)      azure_destroy ;;
    test)         run_tests "$@" ;;
    smoke)        ensure_python; run_smoke "${1:-http://localhost:8080/api}" --api-key "$LOCAL_API_KEY" ;;
    -h|--help|help|"") usage ;;
    *)            usage; die "unknown command: $cmd" ;;
  esac
}

main "$@"
