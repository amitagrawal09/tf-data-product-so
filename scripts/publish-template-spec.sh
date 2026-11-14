#!/usr/bin/env bash
# Publish the one-click template as an Azure Template Spec so anyone with access can deploy the
# Sales Order data product from the Azure portal (Template specs → tfso-sales-order → Deploy),
# even when the GitHub repository is private.
#
#   ./scripts/publish-template-spec.sh [version]
# Env: TS_RESOURCE_GROUP (default rg-tfso-templates), LOCATION (default eastus2), TS_NAME (default tfso-sales-order)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

version="${1:-$(date -u +%Y.%m.%d-%H%M)}"
rg="${TS_RESOURCE_GROUP:-rg-tfso-templates}"
location="${LOCATION:-eastus2}"
name="${TS_NAME:-tfso-sales-order}"

az account show -o none 2>/dev/null || { echo "Run 'az login' first" >&2; exit 1; }
az group create -n "$rg" -l "$location" -o none
az ts create --name "$name" --version "$version" --resource-group "$rg" --location "$location" \
  --template-file infra/oneclick.bicep \
  --display-name "Sales Order data product (one-click)" \
  --description "Three-tier Order Status API, data marketplace and regional operations on Azure" \
  --version-description "Published $(date -u +%Y-%m-%dT%H:%MZ)" --yes -o none
id="$(az ts show --name "$name" --resource-group "$rg" --version "$version" --query id -o tsv)"
echo "Published template spec ${name} v${version}"
echo "Deploy in the portal: https://portal.azure.com/#create/Microsoft.Template/templateSpecVersionId/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1], safe=""))' "$id")"
echo "Or CLI: az deployment sub create -l ${location} --template-spec '${id}' -p apimPublisherEmail=<you@company.com>"
