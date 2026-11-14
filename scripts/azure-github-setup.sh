#!/usr/bin/env bash
# =====================================================================================
# One-time setup so the "Deploy to Azure" GitHub workflow can deploy with a single click.
# Run in Azure Cloud Shell (bash) or anywhere with az CLI (+ optional gh CLI):
#
#   ./scripts/azure-github-setup.sh <github-owner>/<repo> [apim-publisher-email]
#
# Options (environment variables):
#   SUBSCRIPTION_ID   default: current az subscription
#   LOCATION          default: eastus2
#   PREFIX            default: tfso
#   ENVIRONMENTS      default: "dev test prod"
#   BRANCH            also trust workflow runs on this branch (default: current repo default branch, optional)
#
# What it does (no long-lived secrets are created):
#   1. Registers the Azure resource providers the solution uses (subscription level, once).
#   2. Creates resource groups rg-<prefix>-<env> for each environment.
#   3. Creates an Entra ID app registration + service principal "github-<repo>-deploy" and grants it
#      Owner on those resource groups only (the platform assigns roles to its workload identity).
#   4. Adds GitHub OIDC federated credentials for repo:<owner>/<repo>:environment:<env>.
#   5. Sets GitHub secrets AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_SUBSCRIPTION_ID and variables
#      APIM_PUBLISHER_EMAIL / AZURE_LOCATION / TFSO_PREFIX with gh (or prints them to add by hand).
# =====================================================================================
set -euo pipefail

repo="${1:-}"
email="${2:-${APIM_PUBLISHER_EMAIL:-}}"
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "usage: $0 <owner>/<repo> [apim-publisher-email]" >&2; exit 64; }
command -v az >/dev/null || { echo "az CLI is required (use Azure Cloud Shell)" >&2; exit 1; }
az account show -o none 2>/dev/null || { echo "Run 'az login' first" >&2; exit 1; }

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
TENANT_ID="$(az account show --subscription "$SUBSCRIPTION_ID" --query tenantId -o tsv)"
LOCATION="${LOCATION:-eastus2}"
PREFIX="${PREFIX:-tfso}"
ENVIRONMENTS="${ENVIRONMENTS:-dev test prod}"
APP_NAME="github-${repo#*/}-deploy"
az account set -s "$SUBSCRIPTION_ID"

echo "==> 1/5 Registering resource providers (subscription $SUBSCRIPTION_ID)"
for p in Microsoft.App Microsoft.ApiManagement Microsoft.ContainerRegistry Microsoft.ServiceBus Microsoft.EventHub \
         Microsoft.DBforPostgreSQL Microsoft.KeyVault Microsoft.CognitiveServices Microsoft.OperationalInsights \
         Microsoft.Insights Microsoft.Storage Microsoft.ManagedIdentity Microsoft.ContainerInstance; do
  az provider register --namespace "$p" -o none
done

echo "==> 2/5 Resource groups"
for env in $ENVIRONMENTS; do
  az group create -n "rg-${PREFIX}-${env}" -l "$LOCATION" \
    --tags data-product=sales-order environment="$env" managed-by=github-actions -o none
  echo "    rg-${PREFIX}-${env}"
done

echo "==> 3/5 Entra ID app registration '$APP_NAME'"
app_id="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [[ -z "$app_id" ]]; then
  app_id="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
fi
sp_id="$(az ad sp list --filter "appId eq '$app_id'" --query '[0].id' -o tsv)"
[[ -n "$sp_id" ]] || sp_id="$(az ad sp create --id "$app_id" --query id -o tsv)"
for env in $ENVIRONMENTS; do
  scope="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/rg-${PREFIX}-${env}"
  for _ in 1 2 3 4 5; do
    if az role assignment create --assignee-object-id "$sp_id" --assignee-principal-type ServicePrincipal \
         --role Owner --scope "$scope" -o none 2>/dev/null; then break; fi
    sleep 10   # new service principals take a moment to replicate
  done
  echo "    Owner on rg-${PREFIX}-${env}"
done

echo "==> 4/5 GitHub OIDC federated credentials"
existing="$(az ad app federated-credential list --id "$app_id" --query '[].subject' -o tsv)"
add_cred() {  # add_cred <name> <subject>
  if grep -qxF "$2" <<<"$existing"; then echo "    exists: $2"; return; fi
  az ad app federated-credential create --id "$app_id" -o none --parameters "$(cat <<JSON
{"name": "$1", "issuer": "https://token.actions.githubusercontent.com", "subject": "$2",
 "audiences": ["api://AzureADTokenExchange"], "description": "GitHub Actions deploy ($2)"}
JSON
)"
  echo "    added: $2"
}
for env in $ENVIRONMENTS; do
  add_cred "gh-env-${env}" "repo:${repo}:environment:${env}"
done
if [[ -n "${BRANCH:-}" ]]; then
  add_cred "gh-branch-$(echo "$BRANCH" | tr -c 'A-Za-z0-9-' '-')" "repo:${repo}:ref:refs/heads/${BRANCH}"
fi

echo "==> 5/5 GitHub secrets and variables"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh secret set AZURE_CLIENT_ID --repo "$repo" --body "$app_id"
  gh secret set AZURE_TENANT_ID --repo "$repo" --body "$TENANT_ID"
  gh secret set AZURE_SUBSCRIPTION_ID --repo "$repo" --body "$SUBSCRIPTION_ID"
  [[ -n "$email" ]] && gh variable set APIM_PUBLISHER_EMAIL --repo "$repo" --body "$email"
  gh variable set AZURE_LOCATION --repo "$repo" --body "$LOCATION"
  gh variable set TFSO_PREFIX --repo "$repo" --body "$PREFIX"
  for env in $ENVIRONMENTS; do
    gh api -X PUT "repos/${repo}/environments/${env}" >/dev/null && echo "    GitHub environment '${env}' ready"
  done
  echo "    secrets and variables set on ${repo}"
else
  cat <<EOF
    gh CLI not signed in - add these in GitHub → Settings → Secrets and variables → Actions:
      Secret    AZURE_CLIENT_ID        = ${app_id}
      Secret    AZURE_TENANT_ID        = ${TENANT_ID}
      Secret    AZURE_SUBSCRIPTION_ID  = ${SUBSCRIPTION_ID}
      Variable  APIM_PUBLISHER_EMAIL   = ${email:-<your e-mail>}
      Variable  AZURE_LOCATION         = ${LOCATION}
      Variable  TFSO_PREFIX            = ${PREFIX}
    and create GitHub environments: ${ENVIRONMENTS}
EOF
fi

cat <<EOF

Done. Deploy with one click:
  GitHub → ${repo} → Actions → "Deploy to Azure" → Run workflow → choose environment → Run
EOF
